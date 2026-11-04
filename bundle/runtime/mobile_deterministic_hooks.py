#!/usr/bin/env python3
"""Deterministic time/randomness hooks (EPIC M2.04 of docs/plan/MOBILE-EPIC-M2-UNITS.md).

Deliberately small, per the source roadmap's own words for this unit:
"Deterministic time/randomness hooks, only where product flows actually
need them... Avoid widespread invasive test plumbing." This is NOT a
general dependency-injection framework -- it is two pure functions that
turn a validated mobile-state-fixture-v1 record's `clock` and
`randomness_seed` fields into env vars, plus one thin apply function that
sets them via the exact same mechanism M2.02's `mobile_state_reset.py`
already uses for feature-flag env injection: SIMCTL_CHILD_-prefixed env
vars forwarded by `xcrun simctl launch --terminate-running-process` into
the relaunched app process. Sibling module, read but never modified:
`mobile_state_reset.py` -- its `_app_container` "is the app installed"
check is imported and called directly, never reimplemented, and its
`apply_feature_flags` function is the structural/convention template this
module's `apply_hooks` mirrors (same _result shape, same PASS/FAIL/NO-DATA
routing, same platform/device/bundle_id/installed checks).

WHY THIS MODULE COMPUTES FEATURE-FLAG ENV TOO (feature_flags_env), EVEN
THOUGH FEATURE FLAGS ARE M2.02's OWN SCOPE, NOT THIS UNIT'S: `simctl
launch --terminate-running-process` kills and restarts the app, and the
relaunched process's env is built fresh from `dict(os.environ)` plus
whatever this one call's `env=` carries -- it does NOT remember env vars
injected by an earlier, separate `simctl launch` call. A caller who first
live-applies feature flags via `mobile_state_reset.apply_feature_flags`
and then separately calls this module's `apply_hooks` with only clock/seed
env would silently lose the feature flags from the now-running process:
the second relaunch's env never carried them. `feature_flags_env` (the
env-naming mapping, not the relaunch logic) is imported directly from
`mobile_state_reset.py`, the single shared home for it, so `combined_env`
can carry all three sources -- feature flags, clock, randomness seed --
into ONE relaunch here without keeping a second hand copy of that mapping
to quietly diverge from the original. This closes both the overwrite risk
and the duplication by construction (this unit's own adversarial-review
target) rather than documenting either as an accepted ceiling.

REMAINING ORDERING CEILING, not closed by this module (would require
editing M2.02's own apply_feature_flags, out of this unit's scope): if a
caller runs `apply_feature_flags` AFTER this module's `apply_hooks`, THAT
call still only carries feature-flag env and will drop clock/seed the
same way, symmetrically. The fix in either direction is the same: call
whichever mechanism computes the fuller env LAST, or call only this
module's `apply_hooks` (which already includes feature flags) and skip
`apply_feature_flags` entirely when clock/seed hooks are also in play.

Exit contract matches every sibling gate in this repo: 0 = PASS, 1 = FAIL,
2 = NO-DATA. NO-DATA is never a pass. A mechanism-level result can also be
NO-OP (the fixture asked for nothing to happen here and nothing real was
done); see mobile_state_reset.py's own module docstring for the vocabulary
this reuses. Unlike that module, apply_hooks here is not an aggregator over
several named mechanisms feeding one overall PASS/FAIL/NO-DATA verdict --
it IS the top-level result, so its own NO-OP status is real and must never
exit 0 the way a real PASS does; the exit-code map below sends NO-OP to the
same code as NO-DATA (2), matching what nothing-real-happened means in the
sibling module's own aggregation rule.
"""
import argparse
import json
import os
import sys
from datetime import datetime
from pathlib import Path

import contract_check as CC
import mobile_state_fixture as F
from mobile_state_reset import _app_container, feature_flags_env
from mobile_workflow import Refusal, require, invoke, lease

#: Reserved SIMCTL_CHILD_ env var names this module injects. Checked
#: against feature-flag-derived names in combined_env() so a feature flag
#: that happens to uppercase to one of these never silently clobbers (or
#: gets clobbered by) the clock/seed hook it collides with.
CLOCK_ENV_VAR = "SIMCTL_CHILD_MOBILE_CLOCK_FIXED_VALUE"
SEED_ENV_VAR = "SIMCTL_CHILD_MOBILE_JOURNEY_SEED"


def _result(status, detail, **extra):
    require(status in ("PASS", "FAIL", "NO-DATA", "NO-OP"), "Invalid mechanism status: %r" % status)
    return {"status": status, "detail": detail, **extra}


def clock_hook(fixture):
    """Pure: fixture -> {"env": {...}, "applies": bool, "note": str}.

    mode "real" is a STRUCTURAL NO-OP: env is {} and applies is False, but
    note always says so explicitly -- never silently indistinguishable
    from "nothing configured" (the exact failure this unit's adversarial
    review was asked to hunt for: a mode:real fixture quietly getting a
    fake clock override anyway). mode "fixed" injects CLOCK_ENV_VAR."""
    clock = fixture["clock"]
    mode = clock["mode"]
    if mode == "real":
        return {
            "env": {}, "applies": False,
            "note": "clock.mode is 'real': structural no-op, app uses the device's real clock, no override injected",
        }
    if mode == "fixed":
        value = clock["fixed_value"]
        # Defense in depth, independent of M2.01's own schema-layer check
        # (mobile_state_fixture._check_clock_fixed_value_format): this
        # function is also called directly (by tests, or by any future
        # caller that skips the CLI's F.check() gate), so a blank or
        # malformed timestamp must be refused here too, not silently
        # forwarded into a live simctl launch. Same non-blank helper
        # mobile_state_reset.py reuses for the same reason (PR #709's
        # fix), same real ISO-8601 parse mobile_state_fixture.py uses.
        require(not F._is_blank(value), "clock.fixed_value: must not be blank (empty or whitespace-only)")
        try:
            # Same Z-suffix rewrite as mobile_state_fixture.py: fromisoformat
            # does not accept a bare Z before Python 3.11, and this estate's
            # floor is 3.9.
            datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError as exc:
            raise Refusal("clock.fixed_value: %r is not a valid ISO-8601 timestamp: %s" % (value, exc))
        return {
            "env": {CLOCK_ENV_VAR: value}, "applies": True,
            "note": "clock.mode is 'fixed': injecting %s=%s" % (CLOCK_ENV_VAR, value),
        }
    raise ValueError("clock.mode: unknown mode %r (fixture should have failed M2.01 validation first)" % mode)


def randomness_hook(fixture):
    """Pure: fixture -> {"env": {...}, "applies": bool, "note": str}.

    randomness_seed null is a STRUCTURAL NO-OP (real/unseeded randomness),
    stated explicitly for the same reason as clock_hook's real branch."""
    seed = fixture.get("randomness_seed")
    if seed is None:
        return {
            "env": {}, "applies": False,
            "note": "randomness_seed is null: structural no-op, real/unseeded randomness, no override injected",
        }
    return {
        "env": {SEED_ENV_VAR: str(seed)}, "applies": True,
        "note": "randomness_seed=%r: injecting %s=%s" % (seed, SEED_ENV_VAR, seed),
    }


def combined_env(fixture):
    """feature_flags_env (imported from mobile_state_reset.py, the single
    shared home for the flag-to-env-var mapping -- this module no longer
    keeps its own hand copy) + clock_hook + randomness_hook, merged into
    one dict for a single relaunch. Raises Refusal on a real key collision
    (a feature flag literally named e.g. 'mobile_journey_seed') instead of
    letting one silently overwrite the other -- the second half of this
    unit's adversarial-review target."""
    ff_env = feature_flags_env(fixture)
    clock_env = clock_hook(fixture)["env"]
    seed_env = randomness_hook(fixture)["env"]
    collisions = (set(clock_env) | set(seed_env)) & set(ff_env)
    require(
        not collisions,
        "combined_env: feature_flags name(s) collide with reserved deterministic-hook env var(s): %s "
        "-- rename the flag, this module refuses to guess which one wins" % sorted(collisions),
    )
    merged = dict(ff_env)
    merged.update(clock_env)
    merged.update(seed_env)
    return merged


def apply_hooks(fixture, ctx, cwd, root, stages):
    """Thin apply function: computes combined_env(fixture) and, if
    non-empty, relaunches the app exactly the way
    mobile_state_reset.apply_feature_flags does (same _app_container
    check, same invoke() call, same NO-DATA reasons) -- never a second
    relaunch mechanism. See module docstring for the ordering ceiling this
    does not close (a later, separate apply_feature_flags call can still
    drop this call's env)."""
    # clock_hook/randomness_hook run inside the same try as combined_env
    # (which calls them again internally): an unknown clock mode or an
    # invalid/blank fixed_value must never raise past this function's own
    # boundary, the same way every other mechanism in this repo turns a
    # real validation failure into a clean FAIL result instead of an
    # uncaught crash.
    try:
        hooks_note = "; ".join([clock_hook(fixture)["note"], randomness_hook(fixture)["note"]])
        env = combined_env(fixture)
    except Refusal as exc:
        return _result("FAIL", str(exc))
    except (ValueError, KeyError) as exc:
        # An unknown clock.mode (ValueError) or a fixture missing a key
        # F.check() should have required (KeyError) means the fixture
        # skipped M2.01 validation somehow -- a real, if unlikely, bug
        # elsewhere in the call chain, never a reason for this function's
        # own public boundary to crash uncaught.
        return _result("FAIL", "deterministic_hooks: %s: %s" % (type(exc).__name__, exc))

    if not env:
        # NO-OP, never PASS: nothing real happened (no feature_flags, clock
        # mode "real", randomness_seed null all at once), so reporting PASS
        # here would fabricate a deterministic-hooks application that never
        # took place -- the exact failure class PR #719's fix closed for
        # mobile_state_reset.py's own "nothing to apply" mechanisms, reused
        # here rather than reintroduced. This check runs before the
        # platform/device/installed guards below on purpose: when there is
        # truly nothing to apply, no guard result would be more honest --
        # blaming an absent platform/device for a fixture that needed no
        # forwarding in the first place would be less honest, not more.
        return _result("NO-OP", "deterministic_hooks: nothing to apply (%s; no feature_flags)" % hooks_note, env=env)

    if ctx.platform != "ios":
        return _result(
            "NO-DATA",
            "deterministic_hooks: platform %r has no SIMCTL_CHILD_ forwarding path on this machine; "
            "%d env vars mapped but not live-applied (%s)" % (ctx.platform, len(env), hooks_note),
            env=env,
        )

    if not ctx.device or not ctx.bundle_id:
        return _result(
            "NO-DATA",
            "deterministic_hooks: env mapping computed but not live-applied (device or bundle_id missing) (%s)" % hooks_note,
            env=env,
        )

    container = _app_container(ctx.device, ctx.bundle_id, cwd, root, stages)
    if container is None:
        return _result(
            "NO-DATA",
            "deterministic_hooks: app %s is not installed; env mapping computed but not live-applied (%s)"
            % (ctx.bundle_id, hooks_note),
            env=env,
        )

    launch_env = dict(os.environ)
    launch_env.update(env)
    invoke(
        ["xcrun", "simctl", "launch", "--terminate-running-process", ctx.device, ctx.bundle_id],
        cwd, root, stages, "simctl_launch", env=launch_env,
    )
    return _result(
        "PASS",
        "deterministic_hooks: launched %s with %d SIMCTL_CHILD_ vars forwarded (feature_flags + clock + seed "
        "combined into this one relaunch so an earlier separate feature_flags-only relaunch is not silently "
        "dropped) (%s)" % (ctx.bundle_id, len(env), hooks_note),
        env=env,
        limit="This relaunch carries feature_flags + clock + randomness_seed together, so it is safe to call "
              "AFTER mobile_state_reset.apply_feature_flags. It is NOT safe to call apply_feature_flags AFTER "
              "this: that call only carries feature-flag env and would drop clock/seed from the live process "
              "the same way. Whichever mechanism runs must run last, or callers should use only this module's "
              "apply_hooks when clock/seed hooks are in play.",
    )


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--fixture", required=True)
    parser.add_argument("--schema", default=F.DEFAULT_SCHEMA)
    parser.add_argument("--platform", choices=["ios", "android"], default="ios")
    parser.add_argument("--device", default=None)
    parser.add_argument("--bundle-id", default=None)
    parser.add_argument("--repo", default=".")
    parser.add_argument("--out", required=True)
    args = parser.parse_args(argv)

    ctx = argparse.Namespace(platform=args.platform, device=args.device, bundle_id=args.bundle_id)

    repo = Path(args.repo).resolve()
    out = Path(args.out).resolve()

    try:
        require(not out.exists(), "output path already exists, refusing to overwrite prior evidence: %s" % out)
        fixture = F.load_fixture(args.fixture)
        schema = CC.load_json(args.schema, "mobile-state-fixture-v1 schema")
        problems = F.check(fixture, schema)
        require(not problems, "fixture fails M2.01 validation and will not be applied: %s" % "; ".join(problems))

        stages = []
        with lease():
            out.mkdir(parents=True, exist_ok=False)
            result = apply_hooks(fixture, ctx, repo, out, stages)
    except CC.NoData as exc:
        print("mobile_deterministic_hooks: NO-DATA: %s" % exc)
        return 2
    except Refusal as exc:
        status = getattr(exc, "status", "FAIL")
        print("mobile_deterministic_hooks: %s: %s" % (status, exc))
        return 2 if status == "NO-DATA" else 1
    except (OSError, ValueError) as exc:
        print("mobile_deterministic_hooks: FAIL: %s" % exc)
        return 1

    report = {
        "schema": "brother-mobile-deterministic-hooks-v1",
        "status": result["status"],
        "fixture_id": fixture["fixture_id"],
        "platform": ctx.platform,
        "device": ctx.device,
        "bundle_id": ctx.bundle_id,
        "result": result,
        "stages": stages,
    }
    print(json.dumps(report, indent=2))
    return {"PASS": 0, "FAIL": 1, "NO-DATA": 2, "NO-OP": 2}[result["status"]]


if __name__ == "__main__":
    sys.exit(main())

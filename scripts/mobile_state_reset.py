#!/usr/bin/env python3
"""Mobile state reset adapters (EPIC M2.02 of docs/plan/MOBILE-EPIC-M2-UNITS.md).

Sibling module mobile_state_fixture (M2.01) only VALIDATES a JSON record
against docs/schema/mobile-state-fixture-v1.json. This module APPLIES the
reset mechanisms that a validated mobile-state-fixture-v1 record names, to
a real device where this machine has real tooling, and to honest NO-DATA
hook points everywhere else.

Exit contract matches every sibling gate in this repo:
    0 = PASS, 1 = FAIL, 2 = NO-DATA.

NO-DATA is never a pass. A mechanism this adapter cannot exercise for real
must say so by name (Android tooling absent, no per-app keychain clear, no
generic per-key storage API, no backend-reset endpoint configured, no
fixture-API endpoint configured, no local-database fixture script supplied)
and must never silently no-op while claiming success.

A mechanism's own PER-MECHANISM status can also be NO-OP: the fixture
asked for nothing to happen here (keychain_policy.mode "preserve", an
empty feature_flags map) and the mechanism correctly did nothing, without
even touching the device. That is a true, honest outcome for that one
mechanism, distinct from both PASS (a real action was taken or a real
device query confirmed the goal state) and NO-DATA (could not be
exercised at all). apply_fixture's aggregation never lets a fixture made
entirely of NO-OP and NO-DATA mechanisms report an overall PASS: nothing
real happened to the device, so the overall verdict says NO-DATA, never
PASS. NO-OP never appears as apply_fixture's own top-level `status` field
(that stays PASS/FAIL/NO-DATA, matching the exit contract above); it is
only ever a mechanism's own status inside the receipt.

Real tool behavior this module relies on (verified by hand on a booted iOS
Simulator before writing this file, not guessed):
  - `xcrun simctl get_app_container <udid> <bundle> data` exits 0 when the
    app is installed, exits 2 when it is not. There is no separate
    is-installed subcommand.
  - `xcrun simctl uninstall` removes the app binary AND its data container
    together. There is no data-only wipe on iOS Simulator, so reset_mode
    "clean" can only be reached by uninstalling, and a caller must reinstall
    before the next journey. That is a named limitation, not a bug to hide.
  - `xcrun simctl keychain <udid> reset` is real but DEVICE-WIDE: it clears
    the entire simulator keychain, not one app's entries. simctl has no
    per-app keychain clear and no generic named-item seeding hook (only
    add-root-cert/add-cert, both certificate-only).
  - `xcrun simctl launch --terminate-running-process <udid> <bundle>`
    forwards SIMCTL_CHILD_-prefixed env vars from the calling process
    environment into the launched child. That is the real way to pass
    feature-flag-shaped env vars to a simulator app at launch time.
  - There is no adb or other Android tooling on this machine. Every Android
    code path is a live branch that returns NO-DATA with a stated reason,
    never a fabricated success.
  - No live backend-reset endpoint, no fixture-API endpoint, and no
    project-specific local-database fixture script are reachable from this
    generic repo (it holds Brother's own tooling, not the target mobile
    app's source, so it has no visibility into that project's own
    architecture). Each is a real dispatchable hook: supplied by the caller,
    really called and reported on; not supplied, NO-DATA naming the missing
    hook.

PARTIAL FAILURE IS NEVER HIDDEN. `apply_fixture` runs every mechanism the
schema names unconditionally: one mechanism raising on a real command
failure never aborts the rest or skips writing the evidence receipt. Every
mechanism call is isolated by `_run_mechanism`, so the receipt always
reflects the true, possibly-partial state of the device, and a caller can
always see exactly which mechanisms ran, which failed, and which were never
exercised for real.
"""
import argparse
import json
import os
import re
import shutil
import subprocess
import sys
import tarfile
import urllib.request
import urllib.error
from pathlib import Path

import contract_check as CC
import mobile_state_fixture as F
from mobile_workflow import Refusal, require, invoke, lease, write_new, digest

#: A flag name must be a legal environment-variable identifier on its own
#: (before the SIMCTL_CHILD_ prefix and upper() are applied): letters,
#: digits, underscore, not starting with a digit. Catches the exact crash
#: named in review -- a flag name containing "=" (e.g.
#: "mobile_journey_seed=999") passes M2.01 schema validation (feature_flags
#: keys have no pattern constraint there) and would otherwise reach
#: `subprocess` and raise `ValueError: illegal environment variable name`
#: deep inside simctl invocation instead of failing cleanly here.
_ENV_VAR_NAME_SHAPE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")


def _result(status, detail, **extra):
    require(status in ("PASS", "FAIL", "NO-DATA", "NO-OP"), "Invalid mechanism status: %r" % status)
    return {"status": status, "detail": detail, **extra}


def _unsafe_members(tf, dest):
    """The members of tarfile `tf` that filter="data" would refuse when extracting into `dest`, as (name, reason)
    pairs: an absolute name, a name that resolves outside dest, a symlink or hard link whose target resolves outside
    dest, and any device, fifo or other special file. Empty means every member is a plain file or directory inside."""
    root = os.path.realpath(str(dest))

    def inside(path):
        real = os.path.realpath(path)
        return real == root or real.startswith(root + os.sep)

    bad = []
    for m in tf.getmembers():
        where = os.path.join(root, m.name)
        if os.path.isabs(m.name) or not inside(where):
            bad.append((m.name, "outside the container"))
        elif m.issym() and (os.path.isabs(m.linkname) or not inside(os.path.join(os.path.dirname(where), m.linkname))):
            bad.append((m.name, "symlink to outside the container"))
        elif m.islnk() and (os.path.isabs(m.linkname) or not inside(os.path.join(root, m.linkname))):
            bad.append((m.name, "hard link to outside the container"))
        elif not (m.isfile() or m.isdir() or m.issym() or m.islnk()):
            bad.append((m.name, "special file"))
    return bad


def _app_container(device, bundle_id, cwd, root, stages):
    """Return the app data container path, or None if the bundle is not
    installed. Exit code 0 means installed, exit code 2 means not
    installed: both are real, meaningful observations, only 2 maps to
    None. Any other exit code is a real error and propagates as a
    Refusal via invoke()."""
    stdout = invoke(
        ["xcrun", "simctl", "get_app_container", device, bundle_id, "data"],
        cwd, root, stages, "get_app_container", allowed_codes=(0, 2),
    )
    if stages[-1]["exit_code"] == 0:
        return stdout.strip() or None
    return None


def uninstall_app(ctx, cwd, root, stages):
    if ctx.platform != "ios":
        return _result(
            "NO-DATA",
            "uninstall: platform %r has no adapter on this machine (no adb, no Android tooling installed)" % ctx.platform,
        )
    if not ctx.bundle_id:
        return _result("NO-DATA", "uninstall: no --bundle-id given")
    container = _app_container(ctx.device, ctx.bundle_id, cwd, root, stages)
    if container is None:
        return _result("PASS", "uninstall: app %s already absent from device %s" % (ctx.bundle_id, ctx.device))
    invoke(["xcrun", "simctl", "uninstall", ctx.device, ctx.bundle_id], cwd, root, stages, "simctl_uninstall")
    return _result("PASS", "uninstall: removed %s from device %s" % (ctx.bundle_id, ctx.device))


def install_app(ctx, cwd, root, stages):
    if ctx.platform != "ios":
        return _result(
            "NO-DATA",
            "install: platform %r has no adapter on this machine (no adb, no Android tooling installed)" % ctx.platform,
        )
    if not ctx.app_path:
        return _result("NO-DATA", "install: no --app path given")
    app_path = Path(ctx.app_path)
    if not app_path.is_dir():
        return _result("FAIL", "install: app path is not a directory: %s" % app_path)
    invoke(["xcrun", "simctl", "install", ctx.device, str(app_path)], cwd, root, stages, "simctl_install")
    return _result("PASS", "install: installed %s onto device %s" % (app_path, ctx.device))


def reset_app_storage(fixture, ctx, cwd, root, stages):
    storage = fixture["app_storage"]
    mode = storage["reset_mode"]

    if mode == "clean":
        if ctx.platform != "ios":
            return _result("NO-DATA", "app_storage clean: platform %r has no adapter on this machine" % ctx.platform)
        if not ctx.bundle_id:
            return _result("NO-DATA", "app_storage clean: no --bundle-id given")
        r = uninstall_app(ctx, cwd, root, stages)
        if r["status"] == "PASS":
            r = dict(r)
            r["reinstall_required"] = True
            r["detail"] = (
                "app_storage clean: " + r["detail"]
                + "; on iOS Simulator the app binary is removed together with its data "
                  "container, so a reinstall is required before the next journey"
            )
        return r

    if mode == "restore-checkpoint":
        if ctx.platform != "ios":
            return _result("NO-DATA", "app_storage restore-checkpoint: platform %r has no adapter on this machine" % ctx.platform)
        if not ctx.checkpoints_dir:
            return _result("NO-DATA", "app_storage restore-checkpoint: no --checkpoints-dir configured")
        if not ctx.bundle_id:
            return _result("NO-DATA", "app_storage restore-checkpoint: no --bundle-id given, cannot resolve a container")
        ref = storage.get("checkpoint_ref")
        # Reuse mobile_state_fixture's own blank check (defense in depth,
        # same non-blank rule the M2.01 schema layer already enforces via
        # _is_blank): a plain falsiness test lets a whitespace-only ref
        # sail through, the exact bug class PR #709's fix removed there.
        if F._is_blank(ref):
            return _result("FAIL", "app_storage restore-checkpoint: fixture has no checkpoint_ref")
        target = Path(ctx.checkpoints_dir) / ("%s.tar" % ref)
        if not target.is_file():
            return _result("FAIL", "app_storage restore-checkpoint: checkpoint file missing at %s" % target)
        container = _app_container(ctx.device, ctx.bundle_id, cwd, root, stages)
        if container is None:
            return _result(
                "NO-DATA",
                "app_storage restore-checkpoint: app %s is not installed, cannot resolve a container to restore into" % ctx.bundle_id,
            )
        croot = Path(container)
        if not croot.is_dir():
            return _result("FAIL", "app_storage restore-checkpoint: resolved container is not a directory: %s" % croot)
        # The checkpoint tar is locally authored, but extraction is still a
        # trust boundary: filter="data" (Python 3.12+) refuses absolute
        # paths, path traversal and other unsafe tar members, and the
        # `filter` kwarg does not exist before 3.12 (PEP 706) while this
        # estate's floor is 3.9. So every member is checked here first, on
        # every interpreter, and a refusal leaves the container untouched.
        with tarfile.open(target, "r") as tf:
            unsafe = _unsafe_members(tf, croot)
            if unsafe:
                return _result("FAIL", "app_storage restore-checkpoint: %s has an unsafe member %r (%s); the container was not touched"
                               % (target, unsafe[0][0], unsafe[0][1]))
            # Clear the container's own contents (never follow a symlink
            # inside it; is_dir() follows symlinks, so the symlink check has
            # to come first) before extracting the checkpoint into it.
            for child in list(croot.iterdir()):
                if child.is_symlink():
                    child.unlink()
                elif child.is_dir():
                    shutil.rmtree(child)
                else:
                    child.unlink()
            try:
                tf.extractall(croot, filter="data")
            except TypeError:   # Python < 3.12: _unsafe_members above already refused what filter="data" refuses
                tf.extractall(croot)
        return _result(
            "PASS",
            "app_storage restore-checkpoint: restored %s into %s" % (target, croot),
            checkpoint=str(target), container=str(croot),
        )

    if mode == "preserve-named-keys":
        preserved = storage.get("preserved_keys") or []
        return _result(
            "NO-DATA",
            "app_storage preserve-named-keys: iOS Simulator exposes no generic per-key storage "
            "API, so %d preserved_keys cannot be handled here; this needs the target app's own "
            "fixture-API hook. No destructive or silent action was attempted." % len(preserved),
        )

    return _result("FAIL", "app_storage: unknown reset_mode %r" % mode)


def reset_keychain(fixture, ctx, cwd, root, stages):
    policy = fixture["keychain_policy"]
    mode = policy["mode"]

    if mode == "preserve":
        return _result(
            "NO-OP",
            "keychain preserve: no command run, entries left untouched (this is the mode's correct real "
            "behavior; not counted toward an overall PASS on its own, since no real action was taken)",
        )

    if mode == "seeded":
        items = policy.get("seeded_items") or []
        return _result(
            "NO-DATA",
            "keychain seeded: no generic named-item seeding hook exists (%d seeded_items declared); "
            "simctl keychain only supports reset, add-root-cert and add-cert, both certificate-only" % len(items),
        )

    if mode == "clean":
        if ctx.platform != "ios":
            return _result("NO-DATA", "keychain clean: platform %r has no adapter on this machine" % ctx.platform)
        if not ctx.device:
            return _result("NO-DATA", "keychain clean: no --device given")
        invoke(["xcrun", "simctl", "keychain", ctx.device, "reset"], cwd, root, stages, "simctl_keychain_reset")
        target_app = ctx.bundle_id or "(no bundle_id given)"
        return _result(
            "PASS",
            "keychain clean: DEVICE-WIDE reset ran on device %s; the entire simulator keychain "
            "was cleared, not just entries for %s. This is a real ceiling, not a per-app clear."
            % (ctx.device, target_app),
            scope="device-wide",
        )

    return _result("FAIL", "keychain: unknown mode %r" % mode)


def feature_flags_env(fixture):
    """fixture -> {"SIMCTL_CHILD_<NAME>": "1"/"0", ...}. The single shared
    home for the flag-to-env-var mapping: `mobile_deterministic_hooks.py`
    (M2.04) imports and calls this directly rather than keeping its own
    hand copy, so the two modules can never quietly diverge (review found
    exactly that: an undocumented duplicate carrying the same known gap --
    two differently-cased flag names, e.g. "debug_mode" and "DEBUG_MODE",
    silently colliding into one env var once both are upper()-ed; still
    deferred as low priority, unchanged by this extraction).

    Raises Refusal (FAIL) on a flag name that is not itself a legal
    environment-variable identifier (e.g. contains "="): M2.01's schema
    has no pattern constraint on feature_flags keys, so a malformed name
    would otherwise reach `subprocess` untouched and crash there with
    `ValueError: illegal environment variable name` instead of failing
    cleanly at this validation layer, naming the fixture and the flag."""
    flags = fixture.get("feature_flags") or {}
    for name in flags:
        require(
            _ENV_VAR_NAME_SHAPE.match(name),
            "feature_flags: %r (fixture_id=%r) is not a legal environment-variable name "
            "(letters, digits, underscore only, must not start with a digit)"
            % (name, fixture.get("fixture_id")),
        )
    return {"SIMCTL_CHILD_" + name.upper(): ("1" if value else "0") for name, value in flags.items()}


def apply_feature_flags(fixture, ctx, cwd, root, stages):
    env = feature_flags_env(fixture)

    if not env:
        return _result(
            "NO-OP",
            "feature_flags: nothing to apply (fixture named no flags; not counted toward an overall PASS "
            "on its own, since no real action was taken)",
            env=env,
        )

    if ctx.platform != "ios":
        return _result(
            "NO-DATA",
            "feature_flags: platform %r has no SIMCTL_CHILD_ forwarding path on this machine; "
            "%d flags mapped but not live-applied" % (ctx.platform, len(env)),
            env=env,
        )

    if not ctx.device or not ctx.bundle_id:
        return _result(
            "NO-DATA",
            "feature_flags: env mapping computed but not live-applied (device or bundle_id missing)",
            env=env,
        )

    container = _app_container(ctx.device, ctx.bundle_id, cwd, root, stages)
    if container is None:
        return _result(
            "NO-DATA",
            "feature_flags: app %s is not installed; env mapping computed but not live-applied" % ctx.bundle_id,
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
        "feature_flags: launched %s with %d SIMCTL_CHILD_ vars forwarded" % (ctx.bundle_id, len(env)),
        env=env,
        limit="simctl delivered the env vars into the launched process environment; whether the "
              "app itself reads them as feature flags is not verified here",
    )


def fixture_api_hook(fixture, ctx):
    if not ctx.fixture_api_url:
        return _result(
            "NO-DATA",
            "fixture_api: no --fixture-api-url configured; no live fixture-API endpoint is reachable from this generic repo",
        )
    payload = json.dumps({
        "fixture_id": fixture["fixture_id"],
        "account_data_ref": fixture["account_data_ref"],
    }).encode("utf-8")
    request = urllib.request.Request(
        ctx.fixture_api_url, data=payload, method="POST", headers={"Content-Type": "application/json"},
    )
    try:
        with urllib.request.urlopen(request, timeout=15) as response:
            status = response.status
    except urllib.error.HTTPError as exc:
        # HTTPError is a URLError subclass, so it must be caught first: a
        # real server responding with a real error status is FAIL (an
        # attempt that broke), never NO-DATA (could not be observed at all).
        return _result("FAIL", "fixture_api: endpoint %s returned HTTP %s" % (ctx.fixture_api_url, exc.code))
    except urllib.error.URLError as exc:
        return _result(
            "NO-DATA",
            "fixture_api: endpoint %s unreachable (%s), could not observe a reset" % (ctx.fixture_api_url, exc),
        )
    if not 200 <= status < 300:
        return _result("FAIL", "fixture_api: endpoint %s returned HTTP %s" % (ctx.fixture_api_url, status))
    return _result("PASS", "fixture_api: posted fixture_id to %s, HTTP %s" % (ctx.fixture_api_url, status))


def local_database_fixture(fixture, ctx):
    if not ctx.db_fixture_script:
        return _result(
            "NO-DATA",
            "local_database: no --db-fixture-script configured; this generic repo holds Brother's "
            "own tooling, not the target mobile app's source, so it has no visibility into that "
            "project's local-database architecture and cannot safely act here",
        )
    script = Path(ctx.db_fixture_script)
    if not script.is_file():
        return _result("FAIL", "local_database: script path is not a file: %s" % script)
    proc = subprocess.run([str(script), json.dumps(fixture)], capture_output=True, timeout=120)
    if proc.returncode != 0:
        stderr_text = proc.stderr.decode("utf-8", "replace").strip()
        return _result(
            "FAIL",
            "local_database: %s exited %d: %s" % (script, proc.returncode, stderr_text[-500:]),
            exit_code=proc.returncode,
        )
    return _result("PASS", "local_database: %s applied the fixture payload" % script)


def backend_namespace_reset(fixture, ctx):
    dataset = fixture["backend_dataset"]
    label = "%s@%s" % (dataset.get("id"), dataset.get("version"))
    if not ctx.backend_reset_url:
        return _result(
            "NO-DATA",
            "backend_namespace: no --backend-reset-url configured; dataset %s could not be reset "
            "(no reachable test backend on this machine)" % label,
        )
    payload = json.dumps(dataset).encode("utf-8")
    request = urllib.request.Request(
        ctx.backend_reset_url, data=payload, method="POST", headers={"Content-Type": "application/json"},
    )
    try:
        with urllib.request.urlopen(request, timeout=15) as response:
            status = response.status
    except urllib.error.HTTPError as exc:
        # Same HTTPError-before-URLError ordering as fixture_api_hook, and
        # for the same reason: a real bad status is FAIL, not NO-DATA.
        return _result(
            "FAIL",
            "backend_namespace: dataset %s reset endpoint %s returned HTTP %s" % (label, ctx.backend_reset_url, exc.code),
        )
    except urllib.error.URLError as exc:
        return _result(
            "NO-DATA",
            "backend_namespace: dataset %s could not be reset, endpoint %s unreachable (%s)"
            % (label, ctx.backend_reset_url, exc),
        )
    if not 200 <= status < 300:
        return _result(
            "FAIL",
            "backend_namespace: dataset %s reset endpoint %s returned HTTP %s" % (label, ctx.backend_reset_url, status),
        )
    return _result("PASS", "backend_namespace: dataset %s reset via %s, HTTP %s" % (label, ctx.backend_reset_url, status))


#: Real device/tool failures a mechanism can raise without wrapping them in
#: a Refusal first: a filesystem error (shutil.rmtree, tarfile writing to
#: disk), a subprocess that genuinely broke or hung outside invoke()'s own
#: handling (invoke() already turns its own OSError/TimeoutExpired into a
#: Refusal, but local_database_fixture's bare subprocess.run does not), or
#: a corrupt/unsafe tar archive. Kept separate from a genuine programming
#: bug (TypeError, AttributeError, KeyError, ...) so a receipt reader can
#: tell "the device/tool broke" from "our own code broke" instead of both
#: reading as an identical device-level FAIL.
_DEVICE_ERROR_TYPES = (OSError, subprocess.SubprocessError, tarfile.TarError)


def _run_mechanism(name, func, *args):
    """Run one reset mechanism in isolation. A real command failure inside
    one mechanism (a Refusal from invoke(), or any other exception from a
    file operation or subprocess call) must never abort the remaining
    mechanisms or the evidence write: it becomes that mechanism's own FAIL
    result instead, so apply_fixture can always run every mechanism the
    schema names and always write a complete receipt. This is the fix for
    the exact failure mode named in review: a partial reset silently left
    half-applied, with the mechanisms after the break never even attempted
    and no evidence saying so.

    Every path still turns into a FAIL result and still runs every other
    mechanism (nothing here re-raises): a real device/tool failure and a
    genuine programming bug are told apart by the receipt's own
    error_class field instead, so an operator reading a FAIL never
    mistakes our own crash for a device that actually failed to reset."""
    try:
        return func(*args)
    except Refusal as exc:
        return _result(getattr(exc, "status", "FAIL"), "%s: %s" % (name, exc))
    except _DEVICE_ERROR_TYPES as exc:
        return _result("FAIL", "%s: device/tool error: %s" % (name, exc), error_class="device")
    except Exception as exc:  # noqa: BLE001 - a batch dispatcher must not let one mechanism's crash hide the rest
        return _result(
            "FAIL",
            "%s: unexpected internal error (%s): %s" % (name, type(exc).__name__, exc),
            error_class="bug",
        )


def apply_fixture(fixture_path, schema_path, ctx, repo, out):
    repo = Path(repo).resolve()
    out = Path(out).resolve()
    require(not out.exists(), "output path already exists, refusing to overwrite prior evidence: %s" % out)

    fixture = F.load_fixture(fixture_path)
    schema = CC.load_json(schema_path, "mobile-state-fixture schema")
    # Defense in depth, independent of F.check's own robustness: a
    # type-confused fixture must produce a clean FAIL verdict line here,
    # never an uncaught traceback, even if a future change to F.check
    # (or the schema it enforces) reopens a path that raises instead of
    # returning problem strings.
    try:
        problems = F.check(fixture, schema)
    except Exception as exc:
        raise Refusal("fixture validation crashed unexpectedly: %s" % exc) from exc
    require(not problems, "fixture fails M2.01 validation and will not be applied: %s" % "; ".join(problems))

    stages = []
    with lease():
        out.mkdir(parents=True, exist_ok=False)

        # Every mechanism runs unconditionally, each isolated by
        # _run_mechanism. A caller must see the true partial state of the
        # device, never have later mechanisms silently skipped while the
        # report looks clean.
        results = {
            "app_storage": _run_mechanism("app_storage", reset_app_storage, fixture, ctx, repo, out, stages),
            "keychain": _run_mechanism("keychain", reset_keychain, fixture, ctx, repo, out, stages),
            "feature_flags": _run_mechanism("feature_flags", apply_feature_flags, fixture, ctx, repo, out, stages),
            "fixture_api": _run_mechanism("fixture_api", fixture_api_hook, fixture, ctx),
            "local_database": _run_mechanism("local_database", local_database_fixture, fixture, ctx),
            "backend_namespace": _run_mechanism("backend_namespace", backend_namespace_reset, fixture, ctx),
        }

        statuses = [r["status"] for r in results.values()]
        if "FAIL" in statuses:
            overall = "FAIL"
        elif all(s in ("NO-DATA", "NO-OP") for s in statuses):
            # A fixture whose every mechanism was either unexercisable
            # (NO-DATA) or a correct no-op (NO-OP, e.g. keychain preserve,
            # an empty feature_flags map) never happened on the real
            # device. Reporting PASS here would fabricate a reset that
            # never took place, exactly the defect named in review.
            overall = "NO-DATA"
        else:
            overall = "PASS"

        # Stamped here, inside the lease, at the moment the real reset
        # actually ran: this is the one field a forger can't produce without
        # this function itself having read the real fixture bytes right now,
        # closing the window state_provenance.py's record_setup used to ask
        # the caller to self-report instead (a caller-supplied hash proves
        # nothing about whether apply_fixture ever ran).
        fixture_hash = digest(fixture_path)

        report = {
            "schema": "brother-mobile-state-reset-v1",
            "status": overall,
            "fixture_id": fixture["fixture_id"],
            "fixture_hash": fixture_hash,
            "platform": ctx.platform,
            "device": ctx.device,
            "bundle_id": ctx.bundle_id,
            "mechanisms": results,
            "stages": stages,
            "limits": [
                "Each mechanism's own status is the source of truth; NO-DATA is never a pass.",
                "There is no rollback: a FAIL after other mechanisms already ran real destructive "
                "commands leaves the device in that partial state, read every mechanism's own status.",
            ],
        }
        receipt = out / "receipt.json"
        write_new(receipt, report)

    return report, receipt


def capture_checkpoint(device, bundle_id, name, checkpoints_dir, cwd, root, stages):
    container = _app_container(device, bundle_id, cwd, root, stages)
    require(container is not None, "cannot capture checkpoint, app %s is not installed on %s" % (bundle_id, device), status="NO-DATA")
    target_dir = Path(checkpoints_dir)
    target_dir.mkdir(parents=True, exist_ok=True)
    target = target_dir / ("%s.tar" % name)
    require(not target.exists(), "checkpoint already exists, refusing to overwrite: %s" % target)
    # Write to a temp path and rename into place only on success: a
    # mid-write failure (disk full, a signal) must never leave a corrupt
    # partial file sitting at the real target name, which would otherwise
    # permanently block every retry with the same require() check above.
    tmp_target = target_dir / (".%s.tar.partial" % name)
    try:
        with tarfile.open(tmp_target, "w") as tf:
            tf.add(container, arcname=".")
    except BaseException:
        tmp_target.unlink(missing_ok=True)
        raise
    tmp_target.rename(target)
    return target


def run_uninstall(ctx, repo, out):
    repo = Path(repo).resolve()
    out = Path(out).resolve()
    require(not out.exists(), "output path already exists, refusing to overwrite prior evidence: %s" % out)
    stages = []
    with lease():
        out.mkdir(parents=True, exist_ok=False)
        result = uninstall_app(ctx, repo, out, stages)
        report = {
            "schema": "brother-mobile-state-uninstall-v1", "status": result["status"],
            "platform": ctx.platform, "device": ctx.device, "bundle_id": ctx.bundle_id,
            "result": result, "stages": stages,
        }
        receipt = out / "receipt.json"
        write_new(receipt, report)
    return report, receipt


def run_install(ctx, repo, out):
    repo = Path(repo).resolve()
    out = Path(out).resolve()
    require(not out.exists(), "output path already exists, refusing to overwrite prior evidence: %s" % out)
    stages = []
    with lease():
        out.mkdir(parents=True, exist_ok=False)
        result = install_app(ctx, repo, out, stages)
        report = {
            "schema": "brother-mobile-state-install-v1", "status": result["status"],
            "platform": ctx.platform, "device": ctx.device, "bundle_id": ctx.bundle_id,
            "app_path": ctx.app_path, "result": result, "stages": stages,
        }
        receipt = out / "receipt.json"
        write_new(receipt, report)
    return report, receipt


def run_capture_checkpoint(ctx, repo, out, name):
    repo = Path(repo).resolve()
    out = Path(out).resolve()
    require(not out.exists(), "output path already exists, refusing to overwrite prior evidence: %s" % out)
    stages = []
    with lease():
        out.mkdir(parents=True, exist_ok=False)
        target = capture_checkpoint(ctx.device, ctx.bundle_id, name, ctx.checkpoints_dir, repo, out, stages)
        report = {
            "schema": "brother-mobile-state-capture-checkpoint-v1", "status": "PASS",
            "platform": ctx.platform, "device": ctx.device, "bundle_id": ctx.bundle_id,
            "checkpoint": str(target), "digest": digest(target), "stages": stages,
        }
        receipt = out / "receipt.json"
        write_new(receipt, report)
    return report, receipt


def _add_common_flags(sub):
    sub.add_argument("--platform", choices=["ios", "android"], default="ios")
    sub.add_argument("--device", default=None)
    sub.add_argument("--bundle-id", default=None)


def main(argv=None):
    parser = argparse.ArgumentParser(prog="mobile_state_reset", description="M2.02 mobile state reset adapters")
    subs = parser.add_subparsers(dest="command", required=True)

    apply_p = subs.add_parser("apply", help="apply a validated mobile-state-fixture to a device")
    _add_common_flags(apply_p)
    apply_p.add_argument("--fixture", required=True)
    apply_p.add_argument("--schema", default=F.DEFAULT_SCHEMA)
    apply_p.add_argument("--repo", default=".")
    apply_p.add_argument("--out", required=True)
    apply_p.add_argument("--checkpoints-dir", default=None)
    apply_p.add_argument("--fixture-api-url", default=None)
    apply_p.add_argument("--db-fixture-script", default=None)
    apply_p.add_argument("--backend-reset-url", default=None)

    uninstall_p = subs.add_parser("uninstall", help="uninstall the app from the device")
    _add_common_flags(uninstall_p)
    uninstall_p.add_argument("--repo", default=".")
    uninstall_p.add_argument("--out", required=True)

    install_p = subs.add_parser("install", help="install a built .app onto the device")
    _add_common_flags(install_p)
    install_p.add_argument("--app", required=True)
    install_p.add_argument("--repo", default=".")
    install_p.add_argument("--out", required=True)

    capture_p = subs.add_parser("capture-checkpoint", help="capture the app data container into a tar")
    _add_common_flags(capture_p)
    capture_p.add_argument("--checkpoints-dir", required=True)
    capture_p.add_argument("--name", required=True)
    capture_p.add_argument("--repo", default=".")
    capture_p.add_argument("--out", required=True)

    args = parser.parse_args(argv)

    ctx = argparse.Namespace(
        platform=args.platform, device=args.device, bundle_id=args.bundle_id,
        checkpoints_dir=getattr(args, "checkpoints_dir", None),
        app_path=getattr(args, "app", None),
        fixture_api_url=getattr(args, "fixture_api_url", None),
        db_fixture_script=getattr(args, "db_fixture_script", None),
        backend_reset_url=getattr(args, "backend_reset_url", None),
    )

    try:
        if args.command == "apply":
            report, _ = apply_fixture(args.fixture, args.schema, ctx, args.repo, args.out)
        elif args.command == "uninstall":
            report, _ = run_uninstall(ctx, args.repo, args.out)
        elif args.command == "install":
            report, _ = run_install(ctx, args.repo, args.out)
        elif args.command == "capture-checkpoint":
            report, _ = run_capture_checkpoint(ctx, args.repo, args.out, args.name)
        else:
            print("mobile_state_reset: FAIL: unknown command %r" % args.command)
            return 1
    except CC.NoData as exc:
        print("mobile_state_reset: NO-DATA: %s" % exc)
        return 2
    except Refusal as exc:
        status = getattr(exc, "status", "FAIL")
        print("mobile_state_reset: %s: %s" % (status, exc))
        return 2 if status == "NO-DATA" else 1
    except (OSError, ValueError, tarfile.TarError) as exc:
        # tarfile.TarError is not an OSError subclass, so it needs naming
        # here too: without it, a corrupt/unwritable checkpoint tar from
        # the standalone capture-checkpoint command (not wrapped by
        # _run_mechanism, since it runs outside apply_fixture) would
        # escape as a raw traceback instead of this module's own promised
        # "mobile_state_reset: FAIL: ..." verdict line.
        print("mobile_state_reset: FAIL: %s" % exc)
        return 1

    print(json.dumps(report, indent=2))
    return {"PASS": 0, "FAIL": 1, "NO-DATA": 2}[report["status"]]


if __name__ == "__main__":
    sys.exit(main())

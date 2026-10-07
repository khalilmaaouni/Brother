"""Brother Core: the real integration point tying OR-1 through OR-4
together into one callable dispatch(), so the gates are used from the
first real call after landing, not left as untested-in-anger library
code until OR-5's own formal scaling work begins.

Order of operations, each backed by its own unit's own real tests:
1. OR-4 acquire_slot(): bound machine-wide concurrent dispatch.
2. OR-3 validate_jev_question_type(): hard-refuse a quarantined Jev
   type before ever spending a token on it.
2. OR-2 enforce_floors(): refuse a sub-300s timeout or a too-low max
   before dispatch, matching this session's own measured mistakes.
3. OR-1 reserve(): claim estimated cost against the daily cap.
4. OR-2 run_strict(): run the real bridge, refuse a silent fallback.
5. OR-1 reconcile(): record the real cost from the usage line.
6. OR-4 release_slot(): free the slot for the next caller.
"""

import stat
import sys
import os
import math
import re
import datetime
import subprocess
import threading
from dataclasses import dataclass
from typing import Optional
from plugin.runtime.brother.core import openrouter_ledger as ledger
from plugin.runtime.brother.core import openrouter_prices
from plugin.runtime.brother.core import dispatch_semaphore as semaphore
from plugin.runtime.brother.core import repo_paths
from plugin.runtime.brother.core.openrouter_strict import (
    run_strict, enforce_floors, FallbackDetected, MaxTokensTooLow,
    UsageObservation, parse_usage_observation,
)
from scripts.loop import or_ask   # the bridge's own attempt plan, the one the dispatcher prices (objection 11)
from plugin.runtime.brother.core.model_capability_profile import (
    validate_jev_question_type,
    CapabilityProfile,
)


# Overridable so a sandbox (the mutation probe, a test harness) can never write
# to the live ledger and slot pool. Read once, at import.
# DURABLE BY DEFAULT, 2026-09-21: a reboot at about 11:33 JST cleared /tmp and with it the only copy of
# the OpenRouter ledger, so the night's recorded spend was lost and every lane went unfunded. A home
# owned root also answers the cross user planting worry L5a raised against a world writable /tmp.
# The environment variable still wins, so every sandboxed caller keeps its own isolated root.
DEFAULT_STATE_ROOT = (os.environ.get("BROTHER_OR_STATE_ROOT")
                      or os.path.expanduser("~/.claude/brother-or-dispatch-state"))
DEFAULT_DAILY_CAP = 20.0
DEFAULT_MAX_SLOTS = 4


CAP_GRANT_FILENAME = "cap-grant.json"


def _grant_file_refusal(path):
    """L5a-9c (REQ-03, CAP-GRANT-OWNER): the reason a grant file is not trusted,
    or an empty string. The file is opened without following a link and judged
    by its descriptor: a regular file, owned by this user, with no bit of 0o077
    set; its parent directory owned by this user with no bit of 0o022 set.
    Returns (reason, fd): the descriptor is open only when the reason is empty,
    and the caller reads the grant from that same descriptor, never the name."""
    try:
        fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
    except OSError as exc:
        return "grant not opened without following links (%s)" % exc.__class__.__name__, None
    try:
        info = os.fstat(fd)
        parent = os.lstat(os.path.dirname(path) or ".")
    except OSError as exc:
        os.close(fd)
        return "grant not inspected (%s)" % exc.__class__.__name__, None
    reason = ""
    if not stat.S_ISREG(info.st_mode):
        reason = "grant is not a regular file"
    elif info.st_uid != os.getuid():
        reason = "grant owned by uid %d, not this user" % info.st_uid
    elif stat.S_IMODE(info.st_mode) & 0o077:
        reason = "grant mode %s is open to group or others" % oct(stat.S_IMODE(info.st_mode))
    elif not stat.S_ISDIR(parent.st_mode):
        reason = "grant parent is not a directory"
    elif parent.st_uid != os.getuid():
        reason = "grant directory owned by uid %d, not this user" % parent.st_uid
    elif stat.S_IMODE(parent.st_mode) & 0o022:
        reason = "grant directory mode %s is writable by group or others" % oct(stat.S_IMODE(parent.st_mode))
    if reason:
        os.close(fd)
        return reason, None
    return "", fd


def _effective_cap_grant(state_root, daily_cap, now=None):
    """The cap in force: daily_cap, or a founder grant above it while that
    grant is unexpired. A grant is {"daily_cap": number, "until": ISO 8601
    with offset, "words": his words}. RAISE, NEVER OVERRIDE: a grant can only
    lift the cap, and only until its date. Anything unreadable, expired,
    undated, non-finite or lower falls back to daily_cap with one stderr
    line: the failure direction is the lower cap, never the higher one.
    A grant open to other users, owned by another user, or reached through a
    link is refused the same way (L5a-9c)."""
    path = os.path.join(state_root, CAP_GRANT_FILENAME)
    if not os.path.exists(path):
        return daily_cap, None
    try:
        reason, fd = _grant_file_refusal(path)
        if reason:
            raise ValueError(reason)
        with os.fdopen(fd, encoding="utf-8") as fh:
            grant = ledger.proof_ledger.loads(fh.read())   # a repeated member refuses: the lower cap stands
        cap = grant["daily_cap"]
        until = datetime.datetime.fromisoformat(grant["until"])
        if until.tzinfo is None:
            raise ValueError("until carries no offset")
        if isinstance(cap, bool) or not isinstance(cap, (int, float)) or not math.isfinite(cap):
            raise ValueError("daily_cap is not a finite number")
        now = now or datetime.datetime.now(datetime.timezone.utc)
        if until <= now:
            raise ValueError("expired at %s" % grant["until"])
        if cap <= daily_cap:
            return daily_cap, None
        return float(cap), grant["until"]
    except (OSError, ValueError, KeyError, TypeError, OverflowError) as exc:
        sys.stderr.write("openrouter_dispatch: cap grant ignored (%s); cap stays %s\n"
                         % (exc, daily_cap))
        return daily_cap, None


@dataclass(frozen=True)
class DeploymentLimits:
    daily_cap_usd: float
    max_slots: int
    source: str
    grant_until: Optional[str]


def effective_daily_cap(state_root, daily_cap, now=None):
    """Compatibility entry point sharing the validated grant reader."""
    return _effective_cap_grant(state_root, daily_cap, now)[0]


def effective_limits(state_root, now=None):
    """Read deployment policy, falling back per invalid field with a diagnostic.

    The state directory is the policy authority. Caller arguments may only
    lower these ceilings; an in-date owner grant may lift the configured cap.
    """
    if not isinstance(state_root, str) or not state_root:
        raise ValueError("state_root must be a non-empty str, got %r" % (state_root,))
    if now is not None:
        if not isinstance(now, datetime.datetime):
            raise ValueError("now must be a datetime or None, got %r" % (now,))
        if now.tzinfo is None:
            now = now.replace(tzinfo=datetime.timezone.utc)
    else:
        now = datetime.datetime.now(datetime.timezone.utc)
    cap, slots, source = DEFAULT_DAILY_CAP, DEFAULT_MAX_SLOTS, 'default'
    # A RUN'S OWN ROOT (owner, 2026-09-27): the cap IS the run's budget, lower or higher than any default, until the
    # run's deadline, then nothing; an unreadable record admits nothing. No grant or deployment file applies there.
    try:
        run = ledger.run_budget(state_root)
    except ledger.LedgerError as exc:
        sys.stderr.write('openrouter_dispatch: %s; nothing is admitted\n' % exc)
        return DeploymentLimits(0.0, slots, 'run-unreadable', None)
    if run is not None:
        until = datetime.datetime.fromisoformat(run['until'])
        now = now or datetime.datetime.now(datetime.timezone.utc)
        live = until > now
        run_slots = run.get('max_slots')
        if isinstance(run_slots, int) and not isinstance(run_slots, bool) and run_slots >= 1:
            slots = run_slots
        return DeploymentLimits(run['openrouter_usd'] if live else 0.0, slots, 'run' if live else 'run-expired', run['until'])
    path = os.path.join(state_root, 'dispatch-limits.json')
    try:
        with open(path, encoding='utf-8') as fh:
            data = ledger.proof_ledger.loads(fh.read())   # a repeated member refuses: the defaults stand
        if not isinstance(data, dict):
            raise ValueError('deployment limits must be an object')
    except FileNotFoundError:
        data = {}
    except (OSError, ValueError, TypeError) as exc:
        sys.stderr.write('openrouter_dispatch: deployment limits ignored (%s); defaults apply\n' % exc)
        data = {}
    for key in ('daily_cap', 'max_slots'):
        if key not in data:
            continue
        value = data[key]
        try:
            valid = (not isinstance(value, bool) and isinstance(value, (int, float))
                     and math.isfinite(value) and value >= 0) if key == 'daily_cap' else (
                     not isinstance(value, bool) and isinstance(value, int) and value >= 1)
        except OverflowError:
            valid = False
        if not valid:
            sys.stderr.write('openrouter_dispatch: invalid deployment %s; default applies\n' % key)
            continue
        if key == 'daily_cap':
            cap = float(value)
        else:
            slots = value
        source = 'file'
    cap, until = _effective_cap_grant(state_root, cap, now)
    return DeploymentLimits(cap, slots, 'grant' if until is not None else source, until)


CATALOG_FILENAME = "openrouter-models.json"


def measured_cost(result, state_root):
    """(usd, None) from the bridge's own usage line and the saved catalog, or
    (None, reason). Never raises: pricing must not be able to fail a call
    that already succeeded. None means unmeasured, never zero."""
    try:
        # THE BILLED CHARGE FIRST (A/B/C test 2026-09-23: tokens times the saved catalog, from the FINAL attempt only,
        # booked 2.22 USD while the account meter read 9.21 for the same hour). The bridge now prints OpenRouter's own
        # cost summed over every attempt; it is used whenever every attempt carried a figure (known=yes).
        billed = re.search(r"\[billed\] usd=([0-9.]+) attempts=(\d+) known=(yes|no)", getattr(result, "stderr", "") or "")
        if billed and billed.group(3) == "yes":
            return float(billed.group(1)), None
        usage = openrouter_prices.parse_usage(getattr(result, "stderr", "") or "")
        if usage is None:
            return None, "no usage line"
        catalog = openrouter_prices.load_catalog(os.path.join(state_root, CATALOG_FILENAME))
        return openrouter_prices.cost(catalog, usage[2], usage[0], usage[1])
    except Exception as exc:
        return None, "pricing unavailable: %s" % str(exc)[:120]


class Stopped(RuntimeError):
    """This process was told to stop (a fan out's deferred TERM); no new call is reserved."""


#: Set by a fan out that received TERM (objection 9). Checked after the slot wait and before the reservation, so a
#: job queued in the process when the TERM arrived never starts a call the stop would then orphan.
STOPPED = threading.Event()


def _bridge_request(bridge_argv):
    """or_ask's own parse of the argv this dispatch will run, or None when the argv is not the bridge's, will not
    parse, or carries no prompt (a prompt on stdin, which the dispatcher cannot measure)."""
    if not isinstance(bridge_argv, (list, tuple)) or len(bridge_argv) < 2 \
            or os.path.basename(str(bridge_argv[1])) != "or_ask.py":
        return None
    try:
        request = or_ask.build_parser().parse_args([str(arg) for arg in bridge_argv[2:]])
    except SystemExit:
        return None   # sbe: allow-silent None means unmeasured: attempt_envelope_usd then claims no bound and the proof reservation stays unbound
    return request if request.prompt else None


def attempt_envelope_usd(state_root, request):
    """(usd, True) when the saved catalog prices EVERY attempt or_ask.attempt_plan says the bridge can make for this
    request, each at its full max_tokens with len(prompt bytes) + 1024 prompt tokens; else (None, False). A missing,
    stale or partial catalog, an unpriced model or an unreadable request is never a bound (objection 11)."""
    if request is None or or_ask.decision_call(request):
        # a decision request sends no max_tokens, so no attempt plan bounds what it can cost (money audit 2026-09-27,
        # finding 3): it is never bound, whatever the catalog prices
        return None, False
    try:
        catalog = openrouter_prices.load_catalog(os.path.join(state_root, CATALOG_FILENAME))
        tokens = len((" ".join(request.prompt) + (request.system or "")).encode("utf-8")) + 1024
        total = 0.0
        for model, max_tokens in or_ask.attempt_plan(request.model, request.max, request.effort, request.only_model):
            usd, _why = openrouter_prices.cost(catalog, model, tokens, max_tokens)
            if usd is None:
                return None, False
            total += usd
    except (openrouter_prices.CatalogError, OSError, ValueError, TypeError):
        return None, False
    return total, True


LEGACY_HAND_ESTIMATE = 0.05  # last resort when neither history nor the price
# catalog can answer; the same flat number every caller used before M4,
# never a crash and never zero.

DEFAULT_ESTIMATOR_FLOOR = 0.01
DEFAULT_ESTIMATOR_PERCENTILE = 0.95


def _finite_nonnegative(value, name):
    if isinstance(value, bool) or not isinstance(value, (int, float)) \
            or not math.isfinite(value) or value < 0:
        raise ledger.LedgerError("%s must be a finite number >= 0, got %r" % (name, value))
    return value


def estimate_for_model(state_root, model, hand_estimate, floor=DEFAULT_ESTIMATOR_FLOOR,
                       percentile=DEFAULT_ESTIMATOR_PERCENTILE, limit=1000):
    """M4.3: the reservation this model's own measured history actually
    supports, never more than hand_estimate (the caller's own upper
    bound) and never less than floor. Effective estimate is
    min(hand_estimate, max(picked percentile, floor)). Empty history (a
    new model, or one with no RECONCILE yet) returns hand_estimate
    unchanged: no data is never guessed into a lower number. A
    corrupt ledger blocks (LedgerError from actual_costs_for_model)
    rather than silently falling back to hand, since a reservation
    computed from a ledger already known to be untrustworthy is worse
    than refusing the call outright."""
    _validate_estimator_params(hand_estimate, floor, percentile)
    costs = ledger.actual_costs_for_model(state_root, model, limit=limit)  # LedgerError propagates: never guess past a corrupt read
    if not costs:
        return hand_estimate  # no data fallback, never zero, never a guess
    return effective_estimate_from_costs(costs, hand_estimate, floor, percentile)


def _validate_estimator_params(hand_estimate, floor, percentile):
    """M4.3's one validation point for the estimator's own numeric
    parameters: hand and floor must be finite and at or above zero, and
    percentile must be finite and inside (0, 1]. Both estimate_for_model
    and effective_estimate_from_costs route through here, so no caller
    reaches the percentile pick with an unchecked value."""
    _finite_nonnegative(hand_estimate, "hand_estimate")
    _finite_nonnegative(floor, "floor")
    if isinstance(percentile, bool) or not isinstance(percentile, (int, float)) \
            or not math.isfinite(percentile) or not (0 < percentile <= 1):
        raise ledger.LedgerError(
            "percentile must be a finite number in (0, 1], got %r" % (percentile,))
    return hand_estimate, floor, percentile


def effective_estimate_from_costs(costs, hand_estimate, floor, percentile):
    """M4.3's pure pick: the effective reservation a model's own measured
    history supports, never above hand_estimate (the caller's own upper
    bound) and never below floor, so thin or cheap history only ever
    lowers a reserve and can never lift one. costs is the already
    fetched per model history, in any order. Every entry must be a
    finite number at or above zero: an unknown or corrupt history is
    refused here rather than sorted into a reservation, and a bool,
    which is an int in Python, is never read as a cost. The picked value
    is the nearest rank at percentile by ceiling math:
    ceil(percentile * n) - 1, clamped into range. The result is
    min(hand_estimate, max(picked, floor))."""
    _validate_estimator_params(hand_estimate, floor, percentile)
    if not isinstance(costs, (list, tuple)):
        raise ledger.LedgerError(
            "costs must be a list or tuple, got %r" % (type(costs).__name__,))
    checked = []
    for value in costs:
        if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) or value < 0:
            raise ledger.LedgerError(
                "a history cost must be a finite number >= 0, got %r" % (value,))
        checked.append(value)
    if not checked:
        raise ledger.LedgerError(
            "an empty history is not a zero reserve; estimate_for_model returns hand for it")
    ordered = sorted(checked)
    index = min(max(math.ceil(percentile * len(ordered)) - 1, 0), len(ordered) - 1)
    picked = ordered[index]
    return min(hand_estimate, max(picked, floor))


def default_hand_estimate(state_root, requested_model, max_tokens, prompt_tokens=0):
    """The upper bound reservation a job that named no estimated_cost at
    all gets, board unit M4.4's sibling contract: never a flat number
    common to every model, and never zero. Priced from the saved
    catalog at the worst case (the full max_tokens ceiling spent as
    completion); when the catalog cannot price this model at all
    (missing, stale, unlisted), falls back to LEGACY_HAND_ESTIMATE, the
    same flat figure every caller used before this unit, kept as the
    last resort floor of floors rather than a crash or a synthesized
    zero."""
    try:
        catalog = openrouter_prices.load_catalog(os.path.join(state_root, CATALOG_FILENAME))
        usd, _why = openrouter_prices.cost(catalog, requested_model, prompt_tokens, max_tokens)
        if usd is not None:
            return usd
    except Exception:
        pass   # sbe: allow-silent the documented flat floor; a proof reservation priced this way is unbound, so its abandon funds nothing and a settle records the billed cost
    return LEGACY_HAND_ESTIMATE


# F7 (audit: "the model capability check is never called at dispatch"): one
# CapabilityProfile per (model, kind) pair, cached for the life of the
# process exactly as CapabilityProfile itself already promises (checked
# once, trusted for max_age_seconds). The canary here is never a live
# network probe (that stays a follow-up per model_capability_profile.py's
# own docstring): it is a read of the static per-model "kinds" declaration
# already maintained in docs/plan/model-registry.json via
# scripts/loop/model_router.py's can_do(), reused rather than duplicated.
_CAPABILITY_PROFILES = {}


def _capability_canary(model_alias, kind):
    """Reads the static registry declaration for whether model_alias can
    do kind. An unreadable or malformed registry (the module fails to
    import, the JSON is malformed) raises here, which
    CapabilityProfile.check() already converts into a quarantine (F7:
    "an unreadable profile blocks and never reads as capable"), never
    a silent pass-through.

    H9 closure check, 2026-09-26: this used to compute scripts/loop's
    path with its own hand counted dirname() chain, one hop short of
    the real repo root, and that bug was invisible in every normal test
    run because or_fanout._router()'s OWN (separately fixed) copy of
    the same computation usually ran first in the same process and left
    the correct directory on sys.path already. A fresh process that
    calls require_capable() before anything else imports or_fanout
    (real production usage: or_dispatch_cli.py, for one) got a
    ModuleNotFoundError canary and quarantined every model. Fixed by
    calling the ONE shared resolver (repo_paths.repo_loop_dir) that
    or_fanout._router() also calls, so this can never drift out of sync
    with itself again and never depends on which caller runs first.

    WHICH FILE (2026-09-27, the rule lane F2 set for or_fanout._router()).
    The directories were APPENDED to sys.path, so any earlier entry
    holding a model_router.py won (a PYTHONPATH naming the landing tree's
    scripts/loop loaded the landing copy in a proof), a router already
    imported from elsewhere was reused, and a candidate missing its router
    fell through to the live ~/.claude/bin. The directory now goes FIRST,
    for this one import only, and the module loaded must be the file the
    router's own resolver names: code_root()/scripts/loop under a code
    root or a proof phase, else the directory chosen here. Any other file
    raises ImportError, which check() turns into a quarantine."""
    import importlib
    import sys as _sys
    loop_dir = repo_paths.repo_loop_dir(__file__) or os.path.expanduser("~/.claude/bin")
    _sys.path.insert(0, loop_dir)
    try:
        router = importlib.import_module("model_router")
    finally:
        _sys.path.remove(loop_dir)
    if os.environ.get("BROTHER_CODE_ROOT") or os.environ.get("BROTHER_PROOF_PHASE"):
        loop_dir = os.path.join(router.code_root(), "scripts", "loop")
    want = os.path.join(loop_dir, "model_router.py")
    if os.path.realpath(router.__file__) != os.path.realpath(want):
        raise ImportError("the model router loaded is %s, not %s; no capability is read from it" % (router.__file__, want))
    return router.can_do(model_alias, kind)


def require_capable(model_alias, kind, canary=None):
    """F7: refuse at dispatch, with a named reason, when model_alias's
    profile says it cannot do kind, or when the profile cannot be read
    at all. Raises QuarantinedCapability; never silently proceeds."""
    key = (model_alias, kind)
    profile = _CAPABILITY_PROFILES.get(key)
    if profile is None:
        profile = CapabilityProfile(
            model_alias, kind,
            canary or (lambda: _capability_canary(model_alias, kind)))
        _CAPABILITY_PROFILES[key] = profile
    profile.check()
    profile.require_not_quarantined()


# SETTLE BEFORE ABANDON (2026-09-27, for the proof pair). The bridge announces every attempt ("[attempt] n model=...")
# and names each attempt's generation the moment its headers arrive ("[generation] id=... attempt=n"), both flushed, so
# they survive the kill below; a refusal before the response is committed is marked "[attempt] n unbilled http=...".
# A call whose charge the bridge could not report is settled here from the provider's own record, attempt by attempt,
# through the bridge's --settle mode (the key stays in the bridge), and only when EVERY started attempt settles: one
# attempt with no generation, or a record that never answers, leaves the call unknown and it is abandoned as before.
# This runs before abandon because the ledger never reopens a closed reservation.
ATTEMPT_LINE = re.compile(r"^\[attempt\] (\d+) model=\S+$", re.M)
UNBILLED_LINE = re.compile(r"^\[attempt\] (\d+) unbilled http=\d+$", re.M)
GENERATION_LINE = re.compile(r"^\[generation\] id=(gen-[A-Za-z0-9_-]{1,120}) attempt=(\d+)$", re.M)
SETTLE_TIMEOUT_S = 30
BRIDGE_MARGIN_S = 30   # the bridge's wall ends this long before the dispatcher's kill (key read, settlement, exit)


def settle_from_stderr(bridge_argv, stderr, env=None, run=None):
    """The provider's own charge (usd) summed over every attempt a bridge started, or None when any attempt cannot be
    settled. stderr may be bytes (a TimeoutExpired's partial output) or text. run(argv) returns a completed process;
    the default runs the bridge's --settle mode, bounded."""
    text = stderr.decode("utf-8", "replace") if isinstance(stderr, bytes) else (stderr if isinstance(stderr, str) else "")
    started = set(ATTEMPT_LINE.findall(text))
    if not started or not isinstance(bridge_argv, (list, tuple)) or len(bridge_argv) < 2:
        return None
    unbilled = set(UNBILLED_LINE.findall(text))
    generation = {}
    for gid, n in GENERATION_LINE.findall(text):
        generation.setdefault(n, gid)
    if any(n not in generation and n not in unbilled for n in started):
        return None
    run = run or (lambda argv: subprocess.run(argv, capture_output=True, text=True, timeout=SETTLE_TIMEOUT_S + 10, env=env))
    total = 0.0
    for n in sorted(started, key=int):
        if n not in generation:
            continue   # refused before the response was committed: no generation, nothing billed
        try:
            done = run(list(bridge_argv[:2]) + ["--settle", generation[n], "--timeout", str(SETTLE_TIMEOUT_S)])
        except (OSError, subprocess.SubprocessError):
            return None
        m = re.search(r"^\[billed\] usd=([0-9.]+) attempts=1 known=yes$", getattr(done, "stderr", "") or "", re.M)
        if getattr(done, "returncode", 1) != 0 or not m:
            return None
        total += _finite_nonnegative(float(m.group(1)), "provider settled cost")
    return total


class ConfigHeld(RuntimeError):
    """dispatch()'s admit callback refused after the slot wait: a configuration breaker holds this model. Nothing sent."""


import importlib


class BreakerOpen(RuntimeError):
    """A breaker key for this bridge call is open, or the breaker cannot be
    read while BROTHER_BREAKER reads on. Raised before any slot, any ledger
    row and any money: the refusal costs nothing."""


def _breaker_module():
    """Import breaker.py from the pinned loop directory, the same lookup
    _capability_canary uses for the router. Only called while the switch reads
    on; any failure is the caller's to refuse, never to proceed."""
    loop_dir = repo_paths.repo_loop_dir(__file__)
    if not loop_dir:
        loop_dir = os.path.expanduser("~/.claude/bin")
    sys.path.insert(0, loop_dir)
    try:
        return importlib.import_module("breaker")
    finally:
        try:
            sys.path.remove(loop_dir)
        except ValueError:
            pass


def _bridge_keys(brk, bridge_argv, model_alias, requested_model):
    """The [model key, account key] pair this bridge call is admitted on. The
    model part is the registry alias when the caller named one, else the
    requested model id; the account part collapses every model of one account."""
    argv = list(bridge_argv) if isinstance(bridge_argv, (list, tuple)) else bridge_argv
    account = brk.account_of("bridge", argv)
    model = model_alias if model_alias is not None else requested_model
    return brk.keys_for("bridge", account, model)


def _breaker_after(brk, keys, call_id, result, exc):
    """One breaker.record per bridge call, whatever the route. A completed
    process is classified by the breaker's own bridge table; a runner that
    raised is TIMEOUT when its text names TimeoutExpired, MALFORMED when the
    dispatcher caught a fallback, else TRANSPORT_DOWN. A record that cannot be
    written does not mask the call's own outcome: the call already ran and the
    ledger already accounts for the money."""
    if brk is None:
        return
    if result is not None:
        try:
            status = brk.classify_bridge(result.returncode, result.stdout, result.stderr)
        except (ValueError, OSError):
            status = "MALFORMED"
        detail = result.stderr or ""
    elif exc is not None:
        if isinstance(exc, FallbackDetected):
            status = "MALFORMED"
        elif type(exc).__name__ == "TimeoutExpired" or (
                isinstance(exc, ValueError) and "TimeoutExpired" in str(exc)):
            status = "TIMEOUT"
        else:
            status = "TRANSPORT_DOWN"
        detail = str(exc)
    else:
        return
    for item in keys:
        try:
            brk.record(item, status, call_id, detail=detail)
        except (ValueError, OSError):
            continue


def dispatch(bridge_argv, requested_model, estimated_cost=None, holder_id=None,
             timeout_seconds=300, max_tokens=4000, min_max_tokens=1000,
             jev_question_type=None, state_root=DEFAULT_STATE_ROOT,
             allow_fallback=False, model_alias=None, kind=None,
             capability_canary=None, admit=None, breaker_record=True):
    """One real, gated call to the OpenRouter bridge. Raises early
    (before spending anything) for a quarantined Jev type, a refused
    capability (F7), or a sub-floor timeout/max, raises BudgetExceeded
    before the call if the daily cap would be exceeded, and raises
    FallbackDetected after the call if the bridge silently answered
    with a different model than requested.

    THE CEILINGS ARE NOT THE CALLER'S (D2.6 REQ-FAN-5, docs/plan/specs/D2.md):
    the daily cap and the slot count come from the deployment policy
    (effective_limits) and the owner grant alone. A caller passing
    daily_cap= or max_slots= is refused with TypeError by their absence
    from this signature, before any slot, row or call: a control that
    prevents a request from moving a ceiling, not a clamp that reports.

    estimated_cost is the caller's own upper bound ("hand"); when it is
    None (no estimated_cost given at all, M4 sibling contract), it is
    derived from the saved price catalog at the worst case for
    max_tokens (default_hand_estimate), never a flat number and never
    zero. model_alias, when given, tags the reservation for per model
    history (M4.1) and lets the derived estimate (M4.3, estimate_for_model)
    lower that hand estimate toward this model's own measured cost,
    never above it. model_alias plus kind together also gate F7's
    capability check; either left None skips it, matching every
    existing caller that predates F7.
    """
    proof_policy = ledger.proof_ledger.configuration(state_root)
    if holder_id is None:
        raise ValueError("holder_id is required")
    # THE CALLER THAT REFUSES A SUBSTITUTE NEVER ASKS FOR ONE (2026-09-27). Without allow_fallback this function raises
    # FallbackDetected on any answer from another model, while the bridge tried a substitute after every failure: 140
    # substitute answers were paid for in time and thrown away. The bridge is told to try the requested model only; the
    # flag goes before any "--", so it is an option and never prompt text.
    if not allow_fallback and isinstance(bridge_argv, (list, tuple)) and len(bridge_argv) >= 2 \
            and os.path.basename(str(bridge_argv[1])) == "or_ask.py":
        _opts = list(bridge_argv[2:])
        _opts = _opts[:_opts.index("--")] if "--" in _opts else _opts
        if "--only-model" not in _opts:
            bridge_argv = list(bridge_argv[:2]) + ["--only-model"] + list(bridge_argv[2:])
    # THE BRIDGE ENDS BEFORE THIS FUNCTION KILLS IT (2026-09-27): run_strict kills the bridge at timeout_seconds, and the
    # bridge needs its own wall to end inside that, its lost attempts settled; so its --timeout is lowered by
    # BRIDGE_MARGIN_S here, the one place that knows the kill time, for every caller. Never raised. (A call shorter than
    # the margin cannot reach this point: enforce_floors above refuses it.)
    if isinstance(bridge_argv, (list, tuple)) and len(bridge_argv) >= 2 and os.path.basename(str(bridge_argv[1])) == "or_ask.py":
        _args = list(bridge_argv)
        _end = _args.index("--") if "--" in _args[2:] else len(_args)
        if "--timeout" in _args[2:_end]:
            _i = _args.index("--timeout", 2)
            try:
                if _i + 1 < _end and int(_args[_i + 1]) > timeout_seconds - BRIDGE_MARGIN_S:
                    _args[_i + 1] = str(int(timeout_seconds - BRIDGE_MARGIN_S))
                    bridge_argv = _args
            except ValueError:
                pass   # an unreadable --timeout is left for the bridge's own parser to refuse

    if model_alias is not None and kind is not None:
        require_capable(model_alias, kind, canary=capability_canary)

    if jev_question_type is not None:
        validate_jev_question_type(jev_question_type)

    enforce_floors(timeout_seconds, max_tokens, min_max_tokens)

    hand = estimated_cost
    if hand is None:
        hand = default_hand_estimate(state_root, requested_model, max_tokens)
    effective_cost, bound = hand, None
    if proof_policy is not None:
        # A proof reservation is the bridge's whole priced worst case when the frozen catalog prices every attempt
        # (bound), else today's hand rule tagged unbound; history never lowers it. A --max the bridge would have
        # to raise is refused here, before any row: in a proof phase the bridge refuses a raise, which would
        # otherwise leave an abandoned call with an unknown bill.
        request = _bridge_request(bridge_argv)
        if request is not None:
            ceiling = or_ask.attempt_plan(request.model, request.max, request.effort)[0][1]
            if ceiling != request.max:
                raise MaxTokensTooLow("--max %s is below the bridge's %s floor for its effort; a proof phase "
                                      "call cannot raise the ceiling it is priced at" % (request.max, ceiling))
        envelope, bound = attempt_envelope_usd(state_root, request)
        if bound:
            effective_cost = envelope
    else:
        effective_cost = estimate_for_model(
            state_root, model_alias if model_alias is not None else requested_model, hand)

    # Deployment policy is read afresh before admission. No caller argument
    # moves it (REQ-FAN-5); only the owner grant lifts the configured cap.
    limits = effective_limits(state_root)
    effective_max_slots = limits.max_slots
    caps = []

    def cap_at(now):
        """The cap in force at registration, read by reserve() inside its held lock: a grant read before the slot
        wait was reused after it expired (money audit 2026-09-27, finding 9). The policy's own figure, never
        lowered by a caller: a default of 20 applied as min(policy, 20) once clipped every owner grant above 20
        for callers that named no cap (or_fanout, model_call)."""
        caps.append(effective_limits(state_root, datetime.datetime.fromtimestamp(now, datetime.timezone.utc)).daily_cap_usd)
        return caps[-1]

    # FX-11.3: the breaker admits before any slot, ledger row or money moves.
    # While BROTHER_BREAKER reads on, an open key refuses this call here with
    # nothing spent, and a breaker that cannot be imported or read is NO-DATA,
    # never a silent proceed. With the switch off this block is skipped and the
    # path is byte for byte the one every existing caller already takes.
    brk = None
    breaker_keys = []
    _be = os.environ.get("BROTHER_BREAKER")
    if breaker_record and isinstance(_be, str) and _be.strip().lower() == "on":
        try:
            brk = _breaker_module()
        except Exception as exc:
            raise BreakerOpen("breaker NO-DATA: %s" % type(exc).__name__)
        try:
            breaker_keys = _bridge_keys(brk, bridge_argv, model_alias, requested_model)
            _verdict, _reason = brk.admit(breaker_keys, holder_id,
                                          ttl_s=float(timeout_seconds) + 60.0)
        except Exception as exc:
            raise BreakerOpen("breaker NO-DATA: %s" % type(exc).__name__)
        if _verdict == "OPEN":
            raise BreakerOpen(_reason)

    # Wait for a slot as long as one job may run. The old fixed 60 s starved
    # lanes whenever several fan-outs shared the pool (seen live 2026-09-20:
    # two lanes lost all three tries to NoSlotAvailable while slots were busy
    # with healthy long jobs).
    slot = semaphore.acquire_slot(state_root, effective_max_slots, holder_id,
                                  timeout_seconds=timeout_seconds)
    try:
        if STOPPED.is_set():
            raise Stopped("this process was told to stop; no new call is reserved")
        # THE CALLER'S LAST WORD AFTER THE SLOT WAIT (attack R1 F6): a configuration breaker another call tripped while
        # this one queued stops it here, before anything is reserved or sent. admit returns '' to go on, else why not.
        if admit is not None:
            held = admit()
            if held:
                raise ConfigHeld(held)
        try:
            reservation_id = ledger.reserve(
                state_root, cap_at, effective_cost, holder_id,
                model=model_alias if model_alias is not None else requested_model,
                bound=bound, timeout_seconds=timeout_seconds,
            )
        except ledger.BudgetExceeded as exc:
            # R7: a refusal names settled, inflight, hand and effective
            # separately from the cap, never one mixed spend number. Read
            # only, after the fact: the atomic check-then-append already
            # happened inside reserve()'s own held lock, this only enriches
            # the message reserve() already raised.
            breakdown = ledger.spend_breakdown(state_root)
            raise ledger.BudgetExceeded(
                "%s (settled=%.6f inflight=%.6f hand=%.6f effective=%.6f cap=%.6f)"
                % (exc, breakdown["settled"], breakdown["inflight"], hand, effective_cost,
                   caps[-1] if caps else cap_at(datetime.datetime.now(datetime.timezone.utc).timestamp()))
            ) from exc
        if STOPPED.is_set():
            # a TERM that arrived while reserve() waited for the ledger lock: nothing was sent, so the reservation is
            # released as a known zero and no call starts (Codex check-in 2, finding 4)
            ledger.release(state_root, reservation_id, holder_id)
            raise Stopped("this process was told to stop while its call was being reserved; no call is started")
        bridge = dict(allow_fallback=allow_fallback)
        if proof_policy is not None:
            try:
                # STOPPED is checked again inside the dispatch marker's own lock, so a TERM during that wait refuses
                # before any marker is written and the reservation is still releasable
                ledger.mark_dispatched(state_root, reservation_id, holder_id, stopped=STOPPED.is_set)
            except ledger.proof_ledger.DrainRefused:
                # ended between registration and provider contact: nothing was sent, so this is a known zero
                ledger.release(state_root, reservation_id, holder_id)
                raise
            # the bridge answers a proof phase call only with its reservation (or_ask, objection 5)
            bridge["env"] = dict(os.environ, BROTHER_DISPATCH_RESERVATION=reservation_id)
        try:
            result, actual_model = run_strict(
                bridge_argv, requested_model, timeout_seconds, **bridge
            )
        except FallbackDetected as _fb_exc:
            _breaker_after(brk, breaker_keys, reservation_id, None, _fb_exc)
            # REQ-DISP-2 ruling, 2026-09-26 (supersedes the earlier
            # release-to-zero policy this exact except clause used to
            # apply here): a caught fallback is a REAL post dispatch
            # failure. The bridge call genuinely ran and a different
            # model answered, which OpenRouter may already have billed;
            # unknown is never zero. Abandoned as an unknown liability,
            # so it keeps counting against the cap exactly like any
            # other unresolved post dispatch cost, never released as
            # if nothing had happened.
            ledger.abandon(state_root, reservation_id, "fallback_detected")
            raise
        except BaseException as exc:
            _breaker_after(brk, breaker_keys, reservation_id, None, exc)
            # Entering the bridge does not prove whether provider contact or
            # billing occurred. Keep the reserved amount explicitly unknown,
            # unless a killed bridge named every attempt's generation and the
            # provider's own record settles each one (settle before abandon).
            settled = (settle_from_stderr(bridge_argv, exc.stderr, bridge.get("env"))
                       if isinstance(exc, subprocess.TimeoutExpired) else None)
            if settled is None:
                ledger.abandon(state_root, reservation_id,
                               "post_dispatch." + type(exc).__name__)
            elif proof_policy is not None:
                ledger.reconcile(state_root, reservation_id, actual_cost=settled, cost_source='provider_usage')
            else:
                ledger.reconcile(state_root, reservation_id, actual_cost=settled)
            raise
        else:
            _breaker_after(brk, breaker_keys, reservation_id, result, None)
            if proof_policy is not None:
                billed = re.fullmatch(r"\[billed\] usd=([0-9.]+) attempts=([1-9][0-9]*) known=yes", next((line.strip() for line in (getattr(result, 'stderr', '') or '').splitlines() if line.strip().startswith('[billed]')), ''))
                measured = float(billed.group(1)) if billed else None
                if measured is not None:
                    measured = _finite_nonnegative(measured, 'provider billed cost')
            else:
                measured, why = measured_cost(result, state_root)
            if measured is None:
                # the bridge could not report the charge: the provider's own record, attempt by attempt, or unknown
                measured = settle_from_stderr(bridge_argv, getattr(result, 'stderr', ''), bridge.get("env"))
            if measured is None:
                # An estimate must not become a measured history sample.
                ledger.abandon(state_root, reservation_id, "usage.cost_not_provided")
            else:
                if proof_policy is not None:
                    ledger.reconcile(state_root, reservation_id, actual_cost=measured, cost_source='provider_usage')
                else:
                    ledger.reconcile(state_root, reservation_id, actual_cost=measured)
            # D2.6 REQ-FAN-2: the settled, typed outcome. RETAINED names an unknown charge whose reservation stays
            # counted (the row above is ABANDONED, never a RELEASE), RECONCILED a measured one.
            return DispatchResult(stdout=getattr(result, "stdout", None), stderr=getattr(result, "stderr", None),
                                  returncode=getattr(result, "returncode", None), requested_model=requested_model,
                                  actual_model=actual_model,
                                  usage=parse_usage_observation(getattr(result, "stderr", "") or ""),
                                  reservation_id=reservation_id,
                                  settle_state="RETAINED" if measured is None else "RECONCILED")
    finally:
        # Must wrap the reserve() call too: a BudgetExceeded raised by
        # reserve() itself happens after the slot is already acquired,
        # and without this outer finally the slot would leak forever
        # on every over-cap refusal. Found live by this module's own
        # test suite: a ResourceWarning for an unclosed slot file
        # surfaced on the very first over-cap test run.
        semaphore.release_slot(slot)


# D2.5 (docs/plan/specs/D2.md, REQ-DISP-1 to REQ-DISP-5): the settled, typed form of one dispatch. dispatch_observed()
# returns it, and since D2.6 REQ-FAN-2 so does dispatch(), which still unpacks as its old pair (since D2.6 REQ-FAN-5
# neither function takes a daily_cap or max_slots keyword: passing one is a TypeError, by absence from the
# signature). Here a charge is reconciled only from the provider's own known figure, and every other outcome
# after the dispatch marker keeps the reservation counted as a retained, unknown liability.
@dataclass(frozen=True)
class DispatchResult:
    stdout: str
    stderr: str
    returncode: int
    requested_model: str
    actual_model: Optional[str]
    usage: Optional[UsageObservation]
    reservation_id: str
    settle_state: str   # "RECONCILED" | "RETAINED"

    def __iter__(self):
        """The pre D2.6 (result, actual_model) pair, so a caller that unpacks dispatch() keeps working: the result
        half is this object, which carries stdout, stderr and returncode itself."""
        return iter((self, self.actual_model))


def _dispatch_observed_refusal(bridge_argv, requested_model, estimated_cost, holder_id, jev_question_type,
                               state_root, allow_fallback):
    """Why a dispatch_observed() argument is refused, or None. Pure: nothing is read, reserved or sent. A bool is
    refused where a number belongs, and a non str question type is refused before the quarantine set ever sees
    it (an unhashable one would otherwise surface as a raw TypeError)."""
    if (not isinstance(bridge_argv, (list, tuple)) or not bridge_argv
            or not all(isinstance(item, str) for item in bridge_argv)):
        return "bridge_argv must be a non-empty list or tuple of str"
    if not isinstance(requested_model, str) or not requested_model.strip():
        return "requested_model must be a non-empty str"
    try:
        cost_ok = (not isinstance(estimated_cost, bool) and isinstance(estimated_cost, (int, float))
                   and math.isfinite(estimated_cost) and estimated_cost > 0)
    except OverflowError:
        cost_ok = False
    if not cost_ok:
        return "estimated_cost must be a finite number above zero"
    if not isinstance(holder_id, str) or not holder_id.strip():
        return "holder_id must be a non-empty str"
    if jev_question_type is not None and not isinstance(jev_question_type, str):
        return "jev_question_type must be a str or None"
    if not isinstance(state_root, str) or not state_root:
        return "state_root must be a non-empty str"
    if not isinstance(allow_fallback, bool):
        return "allow_fallback must be a bool"
    return None


def dispatch_observed(bridge_argv, requested_model, estimated_cost, holder_id, timeout_seconds=300,
                      max_tokens=4000, min_max_tokens=1000, jev_question_type=None,
                      state_root=DEFAULT_STATE_ROOT, allow_fallback=False):
    """One gated call that returns a DispatchResult. Order (REQ-DISP-1): validate_jev_question_type,
    enforce_floors, effective_limits, acquire_slot at the deployment slot ceiling, reserve at the deployment cap,
    mark_dispatched, run_strict, then reconcile or retain, and the slot is always released. A failure before the
    dispatch marker releases the reservation with a reason; any failure after it retains the reservation as
    post_dispatch.<ExcName> and is raised again (REQ-DISP-2). A hostile argument is refused with ValueError before
    any slot, row or call. A proof phase call is refused here: dispatch() is the one path that hands the bridge
    its reservation."""
    why = _dispatch_observed_refusal(bridge_argv, requested_model, estimated_cost, holder_id,
                                     jev_question_type, state_root, allow_fallback)
    if why is not None:
        raise ValueError("dispatch_observed: %s" % why)
    if ledger.proof_ledger.configuration(state_root) is not None:
        raise ValueError("dispatch_observed: a proof phase call goes through dispatch(), which hands the bridge "
                         "its reservation; nothing was reserved")
    if jev_question_type is not None:
        validate_jev_question_type(jev_question_type)
    enforce_floors(timeout_seconds, max_tokens, min_max_tokens)
    limits = effective_limits(state_root)

    def cap_at(now):
        """The cap reserve() reads inside its held lock: the deployment cap read before the slot wait, lowered by a
        fresh read when a grant expired during the wait, never raised by one (money audit 2026-09-27, finding 9)."""
        fresh = effective_limits(state_root, datetime.datetime.fromtimestamp(now, datetime.timezone.utc))
        return min(limits.daily_cap_usd, fresh.daily_cap_usd)

    slot = semaphore.acquire_slot(state_root, limits.max_slots, holder_id, timeout_seconds=timeout_seconds)
    try:
        reservation_id = ledger.reserve(state_root, cap_at, estimated_cost, holder_id,
                                        model=requested_model, timeout_seconds=timeout_seconds)
        try:
            ledger.mark_dispatched(state_root, reservation_id, holder_id)
        except BaseException as exc:
            # nothing reached the provider: a known zero, released with its reason
            ledger.release(state_root, reservation_id, reason="pre_dispatch." + type(exc).__name__)
            raise
        try:
            result, actual_model = run_strict(bridge_argv, requested_model, timeout_seconds,
                                              allow_fallback=allow_fallback)
            usage = parse_usage_observation(result.stderr)
            if usage is not None and usage.cost_usd is not None:
                ledger.reconcile(state_root, reservation_id, actual_cost=usage.cost_usd,
                                 provenance=usage.provenance)
                settle_state = "RECONCILED"
            else:
                # an estimate is never an actual charge: the reservation stays counted as unknown liability
                ledger.retain(state_root, reservation_id, "usage.cost_not_provided")
                settle_state = "RETAINED"
        except BaseException as exc:
            # after the dispatch marker the provider may have billed: retained, never released
                ledger.retain(state_root, reservation_id, "post_dispatch." + type(exc).__name__)
                raise
        return DispatchResult(stdout=result.stdout, stderr=result.stderr, returncode=result.returncode,
                              requested_model=requested_model, actual_model=actual_model, usage=usage,
                              reservation_id=reservation_id, settle_state=settle_state)
    finally:
        semaphore.release_slot(slot)

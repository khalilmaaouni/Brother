#!/usr/bin/env python3
"""ORCH-09 of the 1.0.20 orchestration control plane: the durable night
supervisor.

WHAT THIS OWNS, AND ONLY THIS. Process continuity, nothing else. When an
orchestrator process dies, this module starts another one. That is the
whole job. It is deliberately NOT scripts/night_tick.py's replacement or
its superior: night_tick.py measures the estate and shouts (see its own
docstring); it never dispatches. This module is the other half named by
that separation: the piece willing to act, but only on the process, never
on the task.

WHAT IT MAY DO: read the run manifest, read the hard stop, inspect
heartbeats, inspect leases, inspect whether a process exists, launch a
missing orchestrator, restart a crashed one, request a graceful stop near
the hard stop, and write supervisor events.

WHAT IT MAY NEVER DO, each one task JUDGEMENT that belongs to an elected
orchestrator, never a process babysitter:
  - edit source
  - choose a decomposition
  - merge
  - lower a risk class
  - mark a unit DONE
  - invent a verdict
  - bypass a live lease
  - execute a RED action
This module never imports scripts/integrate.py (the only thing that
establishes canonical truth) or scripts/graph_loop.py (the only thing that
decides what may run), on purpose: importing either would be the first
step toward this module quietly growing one of the judgements above.
scripts/test_night_supervisor.py asserts this by scanning this file's own
source, and separately asserts this module exposes no callable whose name
even suggests one of these eight verbs.

THE FAILURE MODE THIS BUILD WAS WARNED ABOUT. A supervisor that restarts an
orchestrator under the SAME INSTANCE IDENTITY is the natural
implementation, and it is exactly the case that broke the authority lease
earlier tonight: instance "primary-a" holds epoch 5, stalls, the lease
expires, a restarted "primary-a" takes over at epoch 6, and the OLD process
wakes believing it still holds the lane. scripts/orchestrator_authority.py
now refuses a stale-epoch caller unconditionally (check()/renew()/
release() all compare the epoch on the call to the epoch on disk, never to
the wall clock or to anyone's belief). This module must never undo that
protection by reintroducing a shortcut around it. So: reusing the same
INSTANCE NAME across a restart is fine and expected (a manifest names one
logical slot, e.g. "primary-a", once); reusing the same EPOCH is never
allowed. Every restart calls orchestrator_authority.takeover() (or, for a
scope this supervisor itself still holds a live lease on, release() first,
then takeover()) to mint the new epoch, and the epoch handed to a freshly
spawned process always comes from that call's return value, never from
this module's own memory of a prior one, and this module never writes the
lease store file directly.

SIGNAL PRIORITY WHEN LIVENESS SIGNALS DISAGREE (see classify_liveness()).
Detecting death needs heartbeat age AND process existence, never one
alone: a process can exist and be wedged (hung on a model call, a network
pause) with a heartbeat that has gone stale, and a heartbeat file can look
stale simply because the machine was too busy to schedule the writer for
a while even though the process is fine. PROCESS EXISTENCE WINS whenever
it gives a definite answer: a pid that does not exist is conclusively dead
no matter how fresh its last heartbeat looks (a stale clock, clock skew,
or a stray second writer could all produce a misleadingly fresh stamp, but
nothing can fake a process actually running). A pid that does exist is
only judged dead once its heartbeat is BOTH present and stale past the
configured threshold: existence alone cannot see a wedge, only the
heartbeat can. When process existence itself cannot be determined (no
adapter answer), heartbeat freshness is used as the sole signal, and only
in the direction of proving liveness, never of proving death: this module
would rather do nothing (NO-DATA) than restart a process that might still
be alive on evidence it cannot fully corroborate. See classify_liveness()
for the full decision table.

CONTINGENCY, how this module fails and what recovers it.
  DIRECTION OF FAILURE: always toward NOT ACTING. An unreadable manifest,
  an unreadable durable state file, or an unreadable authority store all
  raise (SupervisorRefused, or orchestrator_authority.AuthorityUnreadable
  propagated unchanged) rather than supervising on an assumption. The one
  exception is proximity to the hard stop, which fails toward requesting a
  graceful stop rather than continuing to run past the deadline: silence
  there would be the same "assumed state is ground truth" mistake this
  estate has already paid for once.
  OPERATOR RECOVERY: a SupervisorRefused or AuthorityUnreadable names the
  path and the problem in its message; fix that file (or restore it from
  its last good version) and rerun. A scope marked permanently "down"
  (crash-loop bound exceeded) is never retried automatically; an operator
  clears "down"/"down_reason" and resets "restart_count" to 0 in the state
  file for that scope once the underlying cause is understood to be fixed.
  A scope refused for a foreign live lease is left exactly as it was;
  resolve the ownership question (who is actually supposed to hold that
  scope) before touching the lease by hand.
  WHAT THIS DOES NOT PROTECT AGAINST: it cannot distinguish a wedged
  orchestrator from a merely slow one any better than the configured
  heartbeat_stale_s allows; it trusts the pid a spawn() call reports back
  rather than independently proving that pid is really the process it
  asked for; it never judges whether a task's WORK is correct, only
  whether the process doing it is up; restart_count is monotonic for the
  life of a run and is never auto-decremented, so a scope that fails
  rarely but repeatedly over a long night can still hit the crash-loop
  bound even though no single failure was severe; and two manifests naming
  the same run_id/scope against the same authority store is an operator
  configuration error this module has no way to detect.

Python 3.9 floor, standard library only, no network beyond what an injected
spawn() callable does on its own. Imports exactly claim_store.py (its Lock,
for cross-process exclusion while this module reads and writes its own
durable state, and pid_alive(), for the one production liveness adapter
this module ships) and orchestrator_authority.py (every lease mutation).
Never imports scripts/integrate.py or scripts/graph_loop.py: see above.
"""

import json
import os
import tempfile
import time
from datetime import datetime, timezone

import claim_store
import orchestrator_authority


class SupervisorRefused(Exception):
    """Raised whenever this module stops rather than proceeding on an
    assumption: an unreadable or malformed manifest, an unreadable or
    malformed durable state file, a second supervisor instance already
    running against the same state, a missing adapter for a configured
    scope, or a restart attempt that would touch a live lease this
    supervisor does not own. Never returned as a falsy value: a caller
    that forgets to check a return value still stops here."""


#: Defaults for manifest fields that are genuinely optional tuning knobs.
#: Every other manifest and scope field is required and validated: see
#: read_manifest(). No permissive default exists for any of the fields
#: that decide whether a restart happens, since an unstated default there
#: would be exactly the "assumed state is ground truth" failure this
#: estate has already paid for.
DEFAULT_GRACEFUL_STOP_LEAD_S = 900.0
DEFAULT_STARTUP_GRACE_S = 60.0
DEFAULT_BACKOFF_CAP_S = 3600.0

_REQUIRED_MANIFEST_KEYS = (
    "run_id", "authority_store", "state_path", "journal_path", "hard_stop",
    "scopes",
)
_REQUIRED_SCOPE_KEYS = (
    "scope", "orchestrator", "instance", "ttl_seconds", "heartbeat_stale_s",
    "max_restarts", "backoff_base_s",
)

#: How long supervise() waits for another concurrent instance's lock on the
#: same state file before refusing (the "started twice concurrently" edge).
#: A module-level variable rather than a literal inline so a test can
#: shrink it and prove the refusal without a real multi-second wait.
STATE_LOCK_TIMEOUT_S = 5.0


def _now(now):
    return time.time() if now is None else float(now)


def _iso(epoch_s):
    return datetime.fromtimestamp(epoch_s, tz=timezone.utc).isoformat()


def _parse_iso(text):
    try:
        return datetime.fromisoformat(str(text).replace("Z", "+00:00")).timestamp()
    except (TypeError, ValueError) as exc:
        raise SupervisorRefused(
            "hard_stop %r is not a parseable ISO 8601 timestamp: %s"
            % (text, exc)) from exc


# ------------------------------------------------------------------ manifest

def read_manifest(manifest_path):
    """The run manifest as a dict, fully validated. Raises SupervisorRefused
    on anything short of a complete, well-shaped document: a missing file,
    a file that is not JSON, a JSON value that is not an object, a missing
    required key at the top level or inside any scope entry, or a scopes
    list that is not a list. This is the "missing or corrupt manifest
    raises rather than supervising with assumptions" rule; there is no
    code path here that turns an unreadable manifest into an empty one."""
    try:
        with open(manifest_path, encoding="utf-8") as fh:
            data = json.load(fh)
    except OSError as exc:
        raise SupervisorRefused(
            "the run manifest at %s could not be read: %s"
            % (manifest_path, exc)) from exc
    except ValueError as exc:
        raise SupervisorRefused(
            "the run manifest at %s is not valid JSON: %s"
            % (manifest_path, exc)) from exc
    if not isinstance(data, dict):
        raise SupervisorRefused(
            "the run manifest at %s did not hold a JSON object" % manifest_path)
    missing = [k for k in _REQUIRED_MANIFEST_KEYS if k not in data]
    if missing:
        raise SupervisorRefused(
            "the run manifest at %s is missing required key(s): %s"
            % (manifest_path, ", ".join(missing)))
    if not isinstance(data["scopes"], list):
        raise SupervisorRefused(
            "the run manifest at %s has a non-list \"scopes\"" % manifest_path)
    for entry in data["scopes"]:
        if not isinstance(entry, dict):
            raise SupervisorRefused(
                "the run manifest at %s has a non-object scope entry: %r"
                % (manifest_path, entry))
        missing = [k for k in _REQUIRED_SCOPE_KEYS if k not in entry]
        if missing:
            raise SupervisorRefused(
                "the run manifest at %s has a scope entry missing required "
                "key(s) %s: %r" % (manifest_path, ", ".join(missing), entry))
    data.setdefault("graceful_stop_lead_s", DEFAULT_GRACEFUL_STOP_LEAD_S)
    for entry in data["scopes"]:
        entry.setdefault("startup_grace_s", DEFAULT_STARTUP_GRACE_S)
        entry.setdefault("backoff_cap_s", DEFAULT_BACKOFF_CAP_S)
    return data


# --------------------------------------------------------------- durable state

def _read_state(state_path):
    """This supervisor's own durable state: {scope: record}. {} when the
    file has never been written (the normal first-tick state). Raises
    SupervisorRefused on a file that exists but is not the JSON object
    this module writes, the same "unreadable is never empty" rule
    read_manifest() enforces, so a corrupt state file can never be
    mistaken for a fresh run."""
    if not os.path.exists(state_path):
        return {}
    try:
        with open(state_path, encoding="utf-8") as fh:
            data = json.load(fh)
    except (OSError, ValueError) as exc:
        raise SupervisorRefused(
            "the supervisor state at %s could not be read: %s"
            % (state_path, exc)) from exc
    if not isinstance(data, dict):
        raise SupervisorRefused(
            "the supervisor state at %s did not hold a JSON object"
            % state_path)
    return data


def _write_state(state_path, state):
    """Atomic: temp file plus os.replace, mirroring
    orchestrator_authority._write, so a crash mid-write leaves the prior
    version on disk rather than a half-written file a later reader could
    mistake for corruption or, worse, read as empty."""
    d = os.path.dirname(os.path.abspath(state_path)) or "."
    os.makedirs(d, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=d, prefix=".night-supervisor-", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            json.dump(state, fh, indent=1, sort_keys=True)
            fh.flush()
            os.fsync(fh.fileno())
        os.replace(tmp, state_path)
    finally:
        if os.path.exists(tmp):
            os.unlink(tmp)


# ------------------------------------------------------------------- journal

def append_event(journal_path, record):
    """Append one JSON line. Best effort, mirroring night_tick.append_tick:
    a supervisor event that cannot be written to disk must not take the
    supervisor itself down over its own bookkeeping. Returns True/False."""
    try:
        d = os.path.dirname(os.path.abspath(journal_path)) or "."
        os.makedirs(d, exist_ok=True)
        with open(journal_path, "a", encoding="utf-8") as fh:
            fh.write(json.dumps(record, sort_keys=True) + "\n")
        return True
    except OSError:  # sbe: allow-silent an event that cannot be journalled must not take the supervisor down over its own bookkeeping; every caller only ever checks the boolean, and the decision it describes has already been applied to the durable state regardless
        return False


def count_events(journal_path, action=None):
    """How many journal lines exist, optionally filtered to one `action`.
    A command any operator can run to answer "has this thing ever fired".
    A malformed line is skipped, never counted and never a crash: this is
    an append-only log a still-running process might be mid-write into."""
    if not os.path.exists(journal_path):
        return 0
    count = 0
    with open(journal_path, encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            try:
                record = json.loads(line)
            except ValueError:  # sbe: allow-silent one malformed line in an append-only journal must not stop a count of the readable lines around it
                continue
            if action is None or record.get("action") == action:
                count += 1
    return count


def _journal(journal_path, run_id, scope, action, reason, now, **extra):
    record = {"at": _iso(now), "run_id": run_id, "scope": scope,
              "action": action, "reason": reason}
    record.update(extra)
    append_event(journal_path, record)


# ----------------------------------------------------------------- liveness

def classify_liveness(pid, process_alive, heartbeat_age_s, heartbeat_stale_s,
                       in_startup_grace):
    """(status, reason). status is one of "ALIVE", "DEAD", "NO-DATA".

    See the module docstring's SIGNAL PRIORITY section for why process
    existence wins whenever it gives a definite answer, and why an
    inconclusive process check falls back to heartbeat freshness only in
    the direction of proving liveness, never of proving death.

    pid: the pid this supervisor last recorded for the scope, or None if
        it has never spawned one.
    process_alive: True/False/None from the adapter (None: could not be
        determined). Never consulted when pid is None: nothing to check.
    heartbeat_age_s: seconds since the last heartbeat, or None if it could
        not be read. A NEGATIVE value means the heartbeat's own timestamp
        is ahead of `now` (clock skew); treated as fresh, since a skewed
        clock cannot be told apart from a genuine wedge without a second,
        independent clock this module does not have.
    in_startup_grace: True when the process was recorded as started
        recently enough that "no heartbeat yet" is expected, not alarming.
    """
    if pid is None:
        return ("DEAD", "no process has ever been recorded for this scope")
    if process_alive is False:
        return ("DEAD", "process %s does not exist" % pid)
    if process_alive is None:
        if heartbeat_age_s is None:
            return ("NO-DATA",
                     "neither process existence nor heartbeat age could be "
                     "read for pid %s" % pid)
        if heartbeat_age_s <= heartbeat_stale_s:
            return ("ALIVE",
                     "heartbeat is fresh (%.0fs) even though process "
                     "existence for pid %s is unknown" % (heartbeat_age_s, pid))
        return ("NO-DATA",
                 "process existence for pid %s is unknown and its heartbeat "
                 "is stale (%.0fs); not enough evidence to call it dead"
                 % (pid, heartbeat_age_s))
    # process_alive is True from here on: the pid genuinely exists.
    if heartbeat_age_s is None:
        if in_startup_grace:
            return ("ALIVE",
                     "pid %s exists and is still inside its startup grace "
                     "window" % pid)
        return ("DEAD",
                 "pid %s exists but has never reported a heartbeat past its "
                 "startup grace window" % pid)
    if heartbeat_age_s < 0:
        return ("ALIVE",
                 "pid %s heartbeat timestamp is ahead of now by %.0fs "
                 "(clock skew); treated as fresh, never as proof of a wedge"
                 % (pid, -heartbeat_age_s))
    if heartbeat_age_s <= heartbeat_stale_s:
        return ("ALIVE", "pid %s heartbeat is fresh (%.0fs)" % (pid, heartbeat_age_s))
    return ("DEAD",
             "pid %s exists but its heartbeat is stale by %.0fs past the "
             "%.0fs threshold (wedged)"
             % (pid, heartbeat_age_s - heartbeat_stale_s, heartbeat_stale_s))


def next_backoff_s(restart_count, backoff_base_s, backoff_cap_s):
    """Exponential backoff, capped: base * 2**restart_count, never past the
    cap. restart_count is the count BEFORE this attempt (0 for the first
    restart), so the first restart's backoff is exactly backoff_base_s."""
    return min(float(backoff_cap_s), float(backoff_base_s) * (2 ** restart_count))


# ----------------------------------------------------------------- restart

def attempt_restart(store, run_id, scope, expected_instance, orchestrator_name,
                     ttl_seconds, reason, now, spawn, scope_cfg):
    """Mint a fresh epoch for `scope` and spawn a replacement process.

    Raises SupervisorRefused when a LIVE lease is held by an instance other
    than `expected_instance`: this is the one hard "never bypass a live
    lease" boundary, and it is a real raise, not a value a caller could
    forget to check.

    When the live lease (if any) already belongs to `expected_instance`,
    it is released first (this supervisor is the sole minter of that
    scope's identity, so closing its own, still-open generation before
    opening the next one is not a bypass of anyone else's authority, it is
    this supervisor retiring its own prior generation early rather than
    waiting out a TTL against a pid it has already independently confirmed
    dead). Either way, the new epoch always comes from
    orchestrator_authority.takeover()'s return value, never from this
    module's own memory of a previous one, and this module never writes
    the lease store file directly.

    Returns {"lease": Lease, "pid": int} on a successful spawn, or
    {"lease": Lease, "pid": None, "spawn_error": str} when spawn() raised;
    the newly minted lease is released again in that case, so a scope
    whose replacement process never actually started is not left holding
    a lease nothing is using.
    """
    current_lease = orchestrator_authority.current(store, run_id, scope, now=now)
    if current_lease is not None and current_lease.instance != expected_instance:
        raise SupervisorRefused(
            "scope %s has a live lease held by instance %r (epoch %d), not "
            "the configured %r; refusing to restart over a lease this "
            "supervisor does not own"
            % (scope, current_lease.instance, current_lease.epoch,
               expected_instance))
    if current_lease is not None:
        orchestrator_authority.release(
            store, run_id, scope, current_lease.instance,
            current_lease.epoch, now=now)
    lease = orchestrator_authority.takeover(
        store, run_id, scope, orchestrator_name, expected_instance,
        ttl_seconds, reason, now=now)
    try:
        pid = spawn(scope_cfg, lease)
    except Exception as exc:  # sbe: allow-silent spawn() is an arbitrary injected launcher; any way it can fail is an ordinary operational failure counted toward the crash-loop bound, never a reason to crash the supervisor that is trying to recover from exactly this kind of failure
        orchestrator_authority.release(
            store, run_id, scope, lease.instance, lease.epoch, now=now)
        return {"lease": lease, "pid": None, "spawn_error": repr(exc)}
    return {"lease": lease, "pid": pid}


# ------------------------------------------------------------------- report

class Report(object):
    """The outcome of one supervise() tick. Every bucket is a list of
    dicts with at least a "scope" key (unless noted), so a caller or a
    test can inspect exactly what happened without re-deriving it from the
    journal."""

    def __init__(self):
        self.restarted = []
        self.alive = []
        self.deferred = []
        self.down = []
        self.refused = []
        self.graceful_stop = []
        self.no_data = []

    def as_dict(self):
        return {
            "restarted": self.restarted, "alive": self.alive,
            "deferred": self.deferred, "down": self.down,
            "refused": self.refused, "graceful_stop": self.graceful_stop,
            "no_data": self.no_data,
        }


# ---------------------------------------------------------------- supervise

def supervise(manifest_path, *, adapters, now=None, spawn=None,
              clock_budget=None):
    """One supervision tick, fully driven from disk: every decision this
    call makes is reconstructed from the manifest, this module's own
    durable state file and each scope's adapter, never from anything held
    in memory by a prior call. That is deliberate (see the module
    docstring's "the supervisor itself can die and be restarted" rule):
    the supervisor process itself can be killed and replaced exactly like
    the orchestrators it watches, and the next call must reach the same
    decisions from the same files.

    adapters: {scope: adapter}, one per manifest scope. An adapter exposes
        process_alive(pid) -> True/False/None and heartbeat_age_s(now) ->
        float seconds or None. A configured scope with no adapter is a
        configuration error and raises SupervisorRefused rather than being
        silently skipped or guessed at.
    spawn: callable(scope_cfg, lease) -> int pid. Required; there is no
        default that would fork a real process, on purpose (see
        attempt_restart's docstring and the worker contract's "never
        actually fork a real orchestrator in a test").
    now: epoch-seconds float, or None for the real clock. A value, not a
        callable, matching orchestrator_authority's own convention.
    clock_budget: optional wall-clock seconds (measured with
        time.monotonic(), independent of the injected `now`) this call may
        spend walking scopes. A heavily loaded machine can make even cheap
        per-scope work slow; a scope this budget is exhausted before
        reaching is recorded in report.deferred rather than judged on a
        clock this call no longer trusts itself to have enough of.

    Raises SupervisorRefused for anything that makes the WHOLE tick
    untrustworthy (an unreadable manifest, an unreadable state file, a
    second supervisor instance already running, a missing adapter) and
    orchestrator_authority.AuthorityUnreadable unchanged for a corrupt
    lease store: both propagate out of this call rather than being caught
    per scope, because there is nothing safe left to decide once the
    ground itself cannot be read. A per-scope foreign-live-lease refusal
    is different: it is caught here and recorded in report.refused, so one
    scope's ownership conflict never blocks judgement of the others
    sharing this manifest (the "one crash-looping while the other is
    healthy" edge, generalised).
    """
    if spawn is None:
        raise SupervisorRefused("supervise() requires an injected spawn callable")
    manifest = read_manifest(manifest_path)
    run_id = manifest["run_id"]
    store = manifest["authority_store"]
    state_path = manifest["state_path"]
    journal_path = manifest["journal_path"]
    hard_stop_epoch = _parse_iso(manifest["hard_stop"])
    graceful_lead_s = float(manifest["graceful_stop_lead_s"])
    now = _now(now)
    report = Report()

    with claim_store.Lock(state_path, timeout=STATE_LOCK_TIMEOUT_S):
        state = _read_state(state_path)
        started_monotonic = time.monotonic()
        for scope_cfg in manifest["scopes"]:
            scope = scope_cfg["scope"]
            if clock_budget is not None:
                if time.monotonic() - started_monotonic >= float(clock_budget):
                    report.deferred.append(
                        {"scope": scope,
                         "reason": "clock_budget exhausted before this scope "
                                   "could be judged"})
                    continue
            if scope not in adapters:
                raise SupervisorRefused(
                    "manifest names scope %s but no adapter was provided "
                    "for it" % scope)
            rec = state.get(scope) or {
                "instance": scope_cfg["instance"], "epoch": None, "pid": None,
                "started_at": None, "restart_count": 0, "backoff_until": None,
                "down": False, "down_reason": None,
            }
            state[scope] = _process_scope(
                scope_cfg, rec, adapters[scope], store=store, run_id=run_id,
                now=now, spawn=spawn, hard_stop_epoch=hard_stop_epoch,
                graceful_lead_s=graceful_lead_s, journal_path=journal_path,
                report=report)
        _write_state(state_path, state)
    return report


def _process_scope(scope_cfg, rec, adapter, *, store, run_id, now, spawn,
                    hard_stop_epoch, graceful_lead_s, journal_path, report):
    """Judge and, if warranted, act on one scope. Returns the (possibly
    updated) state record for that scope. Never raises SupervisorRefused
    for a foreign-lease conflict (caught here and folded into `report`);
    orchestrator_authority.AuthorityUnreadable and any other unexpected
    exception propagate unchanged, per supervise()'s own contract."""
    scope = scope_cfg["scope"]
    pid = rec.get("pid")
    process_alive = adapter.process_alive(pid) if pid is not None else None
    heartbeat_age = adapter.heartbeat_age_s(now)
    in_grace = (rec.get("started_at") is not None
                and (now - rec["started_at"]) < scope_cfg["startup_grace_s"])
    status, reason = classify_liveness(
        pid, process_alive, heartbeat_age, scope_cfg["heartbeat_stale_s"],
        in_grace)

    near_hard_stop = now >= (hard_stop_epoch - graceful_lead_s)

    if status == "ALIVE":
        if near_hard_stop:
            _journal(journal_path, run_id, scope, "graceful-stop-requested",
                     reason, now)
            report.graceful_stop.append({"scope": scope, "reason": reason})
        else:
            report.alive.append({"scope": scope, "reason": reason})
        return rec

    if status == "NO-DATA":
        _journal(journal_path, run_id, scope, "no-data", reason, now)
        report.no_data.append({"scope": scope, "reason": reason})
        return rec

    # status == "DEAD" from here on.
    if rec.get("down"):
        _journal(journal_path, run_id, scope, "already-down",
                 rec.get("down_reason"), now)
        report.down.append({"scope": scope, "reason": rec.get("down_reason")})
        return rec

    if near_hard_stop:
        msg = ("dead (%s) but within the graceful-stop window of the hard "
               "stop; not restarted" % reason)
        _journal(journal_path, run_id, scope, "restart-skipped-near-hard-stop",
                 msg, now)
        report.deferred.append({"scope": scope, "reason": msg})
        return rec

    backoff_until = rec.get("backoff_until")
    if backoff_until is not None and now < backoff_until:
        msg = "dead (%s) but still in backoff until %.0f" % (reason, backoff_until)
        _journal(journal_path, run_id, scope, "restart-deferred-backoff",
                 msg, now)
        report.deferred.append({"scope": scope, "reason": msg})
        return rec

    restart_count = rec.get("restart_count", 0)
    if restart_count >= scope_cfg["max_restarts"]:
        rec["down"] = True
        rec["down_reason"] = ("crash-loop bound exceeded after %d restarts"
                               % restart_count)
        _journal(journal_path, run_id, scope, "crash-loop-down",
                 rec["down_reason"], now)
        report.down.append({"scope": scope, "reason": rec["down_reason"]})
        return rec

    try:
        outcome = attempt_restart(
            store, run_id, scope, scope_cfg["instance"],
            scope_cfg["orchestrator"], scope_cfg["ttl_seconds"],
            "dead orchestrator restart: %s" % reason, now, spawn, scope_cfg)
    except SupervisorRefused as exc:
        _journal(journal_path, run_id, scope, "refused-foreign-live-lease",
                 str(exc), now)
        report.refused.append({"scope": scope, "reason": str(exc)})
        return rec

    rec["instance"] = outcome["lease"].instance
    rec["epoch"] = outcome["lease"].epoch
    rec["restart_count"] = restart_count + 1
    rec["backoff_until"] = now + next_backoff_s(
        restart_count, scope_cfg["backoff_base_s"], scope_cfg["backoff_cap_s"])

    if outcome["pid"] is None:
        rec["pid"] = None
        rec["started_at"] = None
        _journal(journal_path, run_id, scope, "spawn-failed",
                 "%s; spawn error: %s" % (reason, outcome.get("spawn_error")),
                 now, epoch=outcome["lease"].epoch)
        report.deferred.append(
            {"scope": scope, "reason": "spawn failed: %s"
             % outcome.get("spawn_error")})
        return rec

    rec["pid"] = outcome["pid"]
    rec["started_at"] = now
    _journal(journal_path, run_id, scope, "restarted", reason, now,
             pid=outcome["pid"], epoch=outcome["lease"].epoch,
             instance=outcome["lease"].instance)
    report.restarted.append(
        {"scope": scope, "pid": outcome["pid"], "epoch": outcome["lease"].epoch,
         "instance": outcome["lease"].instance, "reason": reason})
    return rec


# --------------------------------------------------- a real production adapter

class FileHeartbeatAdapter(object):
    """The one concrete adapter this module ships, for a real orchestrator
    process that writes its own liveness to disk. Not used by any test in
    this suite (tests use small stand-ins so nothing here ever depends on
    a real file or a real process), but supervise()'s adapter contract
    needs at least one working implementation to be more than an
    abstraction nobody could actually wire up.

    Process existence is claim_store.pid_alive() unchanged: this module
    composes that existing primitive rather than re-deriving os.kill(pid,
    0) semantics a second time (permission-denied and other ambiguous
    OSErrors read as alive, exactly as claim_store's own callers read
    them, since lacking the signal-sending permission is not proof of
    death).

    Heartbeat file format: a JSON object {"at": <epoch-seconds float>},
    written by write_heartbeat_file() below. A missing or corrupt file
    reads as heartbeat_age_s() returning None (NO-DATA about the
    heartbeat), never as zero age and never as infinite age.
    """

    def __init__(self, heartbeat_path):
        self.heartbeat_path = heartbeat_path

    def process_alive(self, pid):
        return claim_store.pid_alive(pid)

    def heartbeat_age_s(self, now):
        try:
            with open(self.heartbeat_path, encoding="utf-8") as fh:
                data = json.load(fh)
            at = float(data["at"])
        except (OSError, ValueError, KeyError, TypeError):  # sbe: allow-silent a missing or corrupt heartbeat file is NO-DATA about this scope's heartbeat, read as None and never as zero or infinite age
            return None
        return now - at


def write_heartbeat_file(heartbeat_path, now=None):
    """For a real orchestrator process to call periodically. Atomic, same
    temp-file-plus-replace technique as _write_state, so a reader never
    sees a half-written heartbeat."""
    now = _now(now)
    d = os.path.dirname(os.path.abspath(heartbeat_path)) or "."
    os.makedirs(d, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=d, prefix=".heartbeat-", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            json.dump({"at": now}, fh)
        os.replace(tmp, heartbeat_path)
    finally:
        if os.path.exists(tmp):
            os.unlink(tmp)

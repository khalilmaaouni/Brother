#!/usr/bin/env python3
"""bm_stall.py: the active stall, dead-owner, and orphan sweep.

Loop SD (docs/plan/PROGRAM-PLAN-2026-08-10.md, section "Loop SD"; SD.1
through SD.3 in docs/program/category-ownership/PROGRAM-2026-08-11.md,
section 11, "LOOP SD: Active stall, dead-owner, and orphan recovery").

WHY THIS EXISTS
  Five real fences went stale on 2026-08-10 (README.md, SKILL.md twice, the
  findings ledger, the narrowing work itself) and nobody noticed until a
  founder read STATE.md by hand. The watchdog that should have caught this
  died with its own session. Nine provisional records sat with no living
  owner. tools/bm_controller.py already heartbeats (:1797) and resumes
  orphans (:2434) -- but ONLY inside a running Full-Auto controller. Nothing
  sweeps the store's own fences and claims when NO controller is running,
  which is the ordinary state of an interactive session. This file is that
  sweep.

WHAT THIS DOES (SD1, SD2, SD5)
  SD1 -- owner_liveness(): one pure function, several weak signals combined
    (process liveness, heartbeat age, controller-run registration), NEVER
    pid alone. See its own docstring for the PID-reuse caveat.
  SD2 -- sweep(): a pure-read pass over a store's active records (this
    project's own name for a "fence"), their claims, and their provisional
    records, reporting Findings: stale fence, dead watchdog, dead-owner
    provisional record, and duplicate/overlapping active claims (a state
    the store's own write-time checks should prevent, kept here as a drift
    detector the same way bm_store.verify() keeps its own dupes/overlap
    check for exactly that reason).
  SD5 -- every Finding carries the EXACT `bm_store`/`bm_learn` command that
    would clear it, as a STRING. Nothing in this file ever executes one
    (dashboard decision D-3, PROGRAM-PLAN.md's own words: "sweep and
    propose only... deliberately OUT of SD: auto-adopt or auto-release").

WHAT THIS DOES NOT DO (left as explicit deltas for other steps)
  SD3 -- registering this module in tools/bm_effects.py's REGISTRY as
    pure_read is a change to THAT file, not this one.
  SD4 -- wiring `sweep` into tools/bm_sessionstart.sh (fail-open, the same
    way tools/bm_progress_check.py is wired there today) is a change to
    that shell script.
  SD6 -- registering this module and its test in tools/test_all.py, the CI
    workflow, pyproject.toml, tools/write_sites.json, and CHECKSUMS.sha256
    (the four-places-plus-checksums contract) are changes to those files.
  None of the five are made here; see the delta list this session reported
  alongside this file.

A KNOWN, STATED GAP: NO PID IS EVER RECORDED PER SESSION TODAY
  tools/bm_store.py's own session ids are opaque random tokens
  ("cli-<uuid4 hex>", or whatever a caller passes to --session); no table
  in the schema stores a process id against one. So `sweep()` always
  computes pid=None, pid_alive=None for every real record it reads: process
  liveness is a signal this oracle KNOWS how to use (see owner_liveness),
  but the store gives it nothing to use today. `pid_hints` is the
  documented extension point -- an optional {session_id: pid} map a caller
  who has out-of-band knowledge (a future session-pid registry, SD4/SD6's
  own territory) may pass in. Passing none is honest, not a defect; a
  liveness oracle that pretended to check a signal it cannot see would be
  the more dangerous of the two wrong answers.

ANOTHER KNOWN, STATED GAP: THE FOREIGN-COMMIT-BASE CHECK IS STANDALONE
  foreign_commit_base_finding() is a pure function that takes a claimed
  base commit sha and the current HEAD sha as PARAMETERS -- this module
  spawns no subprocess (pure_read forbids it), so nothing here ever runs
  `git`. It is not wired into sweep()'s default output because no table in
  today's schema records which commit a claim was made against; a caller
  who already has both shas (an ordinary `git rev-parse` a session or a
  future hook already ran) may call it directly.

EFFECT CLASS: pure_read
  This file writes NOT ONE BYTE anywhere, ever. It opens the store only
  through bm_store.ReadOnlyStore (never bm_store.Store, whose own
  construction is itself a write -- WAL/SHM sidecars, git-excludes,
  chmod, a possible schema migration; see tools/bm_effects.py's module
  docstring for the measured root cause this project keeps rediscovering).
  Every SD5 "clearing verb" is a STRING built from data already in hand,
  never a subprocess.run call. No network. No subprocess.

BE SILENT-SAFE
  Any unexpected exception anywhere in a sweep is reported as NO-DATA
  (exit 2), never silently treated as "nothing found" (exit 0): a sweep
  that cannot read the store must never be mistaken for a store with
  nothing wrong in it. Mirrors tools/bm_progress_check.py's own contract.

Python 3.9, standard library only. No network. No subprocess.
No em or en dashes anywhere in this file or its output.

Usage:
  python3 tools/bm_stall.py [sweep] [--root PATH] [--now ISO8601]
    [--stale-seconds N] [--pid-hint SESSION_ID=PID] [--json]

`sweep` is the only verb and is also the default when no verb is given.
"""

import collections
import datetime
import importlib.util
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))


def _load(name):
    """Load a sibling tools/ module by PATH, the same technique
    tools/bm_project.py and tools/bm_progress_check.py use: this file is
    invoked from an arbitrary working directory (including from a
    SessionStart hook), and a plain `import bm_store` would depend on
    whatever sys.path the caller happened to have."""
    spec = importlib.util.spec_from_file_location(
        name, os.path.join(HERE, name + ".py"))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _load_bm_store():
    """Never raises: an unimportable bm_store degrades to an explicit
    NO-DATA reason, not a crash, mirroring
    tools/bm_progress_check.py:_load_bm_store."""
    try:
        return _load("bm_store"), None
    except Exception as exc:
        return None, "%s: %s" % (type(exc).__name__, exc)


# ---------------------------------------------------------------------------
# SD1: the liveness oracle.
# ---------------------------------------------------------------------------

LIVE = "LIVE"
DEAD = "DEAD"
UNKNOWN = "UNKNOWN"

#: Default staleness window: a record with no corroborating live signal and
#: a heartbeat older than this is judged DEAD. Four hours: long enough that
#: an ordinary attended pause (lunch, a meeting) never trips it, short
#: enough that "still fenced the next morning" (the 2026-08-10 scar) does.
#: A judgment call, stated here rather than silently assumed; --stale-
#: seconds overrides it per invocation.
DEFAULT_STALE_AFTER_SECONDS = 4 * 3600


def process_alive(pid):
    """True when signal 0 reaches `pid`. Mirrors tools/bm_continue.py:801
    process_alive EXACTLY (same guarantees, same reasoning), copied rather
    than imported: bm_continue.py is a heavy CLI module (it loads
    bm_store.py itself, spawns successor sessions, and carries the whole
    continuity-protocol surface), and pulling all of that in just to reach
    one nine-line function would make this file's own pure_read guarantee
    harder to see at a glance, not easier. If tools/bm_continue.py's own
    process_alive ever changes, this copy is the one that must change with
    it; there is exactly one of these two functions that matters (the
    other is a mirror), and this comment is how a future reader finds the
    original.

    EPERM counts as alive: a process this user may not signal is still a
    process that exists, and reporting it dead would be the more
    dangerous of the two wrong answers.

    ZERO AND NEGATIVE IDS ARE REFUSED BEFORE os.kill EVER SEES THEM: in
    POSIX a negative pid addresses a process GROUP and -1 addresses every
    process the caller may signal, so os.kill(-1, 0) succeeds and would
    report a successor alive that never existed (tools/bm_continue.py's
    own history, 2026-08-08)."""
    try:
        pid = int(pid)
    except (ValueError, TypeError):
        return False
    if pid <= 0:
        return False
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    except (OSError, ValueError, TypeError):
        return False
    return True


#: One owner's evidence, gathered before the oracle ever runs, so the
#: oracle itself stays a pure function of DATA rather than reaching for a
#: store or a clock on its own (testable with plain values, no fixture).
#:   pid                       int or None. None means "no pid known for
#:                              this owner" (the ordinary case today; see
#:                              this module's own docstring, "A KNOWN,
#:                              STATED GAP").
#:   pid_alive                 True/False/None. None exactly when pid is
#:                              None; otherwise process_alive(pid).
#:   heartbeat_age_seconds     float or None seconds since the newest
#:                              activity this store recorded for this
#:                              owner (a record's own updated_at, its
#:                              latest transition, a controller run/
#:                              checkpoint/dispatch tied to the same
#:                              session). None only when nothing at all
#:                              was found, which should not happen for a
#:                              real records row (updated_at is NOT NULL)
#:                              but is representable rather than assumed
#:                              impossible.
#:   session_registered_active True when this session_id is the recorded
#:                              driver of a controller_runs row still in a
#:                              non-terminal state -- the "controller
#:                              state"/"session registry" signal SD.1
#:                              names, the strongest corroboration this
#:                              store can offer.
OwnerSignals = collections.namedtuple(
    "OwnerSignals",
    ("pid", "pid_alive", "heartbeat_age_seconds", "session_registered_active"))


def owner_liveness(signals, stale_after_seconds=DEFAULT_STALE_AFTER_SECONDS):
    """(verdict, reason) -- verdict is one of LIVE, DEAD, UNKNOWN.

    THE PID-REUSE CAVEAT (SD.1's own words: "PID reuse must not be treated
    as sufficient proof of liveness"). `signals.pid_alive is True` is NEVER,
    by itself, enough to return LIVE anywhere in this function. A pid that
    answers signal 0 only PROVES some process holds that number right now;
    the operating system reissues pids, and a long-idle owner's pid can
    already belong to an unrelated program by the time this runs. pid_alive
    is used two ways only: pid_alive is False is treated as real evidence
    of death (a process that is confirmed GONE strengthens a stale verdict,
    though it is still not the only thing decided on); pid_alive is True
    is used ONLY as one more fact folded into an otherwise-live picture --
    never as the deciding fact on its own.

    THE RULES, IN ORDER:
      1. A fresh heartbeat (age <= stale_after_seconds) is corroborated
         reality: LIVE.
      2. Failing that, a session still registered as the live driver of a
         controller run is the strongest remaining signal this store
         offers: LIVE.
      3. Failing both, no heartbeat evidence exists at all: UNKNOWN. This
         is not "assume alive" and not "assume dead"; it is "this sweep
         cannot tell", and callers must not treat UNKNOWN as either LIVE
         or DEAD by default (see sweep()'s own handling).
      4. Failing that (a real but STALE heartbeat, no live controller
         registration): DEAD. pid_alive is True is annotated into the
         reason as an EXPLICIT non-decision, naming the PID-reuse caveat
         by name, so a founder reading a stale-fence finding is never left
         thinking "but the process answered, so maybe it is fine".

    `now` is never read here: heartbeat_age_seconds is computed by the
    caller (sweep(), against an explicit or injected clock), which is what
    makes this function deterministic and directly testable with plain
    numbers rather than real elapsed time."""
    if (signals.heartbeat_age_seconds is not None
            and signals.heartbeat_age_seconds <= stale_after_seconds):
        return LIVE, ("heartbeat age %.0fs is within the %.0fs staleness "
                      "window" % (signals.heartbeat_age_seconds,
                                  stale_after_seconds))
    if signals.session_registered_active:
        return LIVE, "session is the recorded live driver of a controller run"
    if signals.heartbeat_age_seconds is None:
        return UNKNOWN, "no heartbeat evidence was found for this owner at all"
    if signals.pid_alive is True:
        return DEAD, (
            "heartbeat is %.0fs old (over the %.0fs staleness window) and no "
            "controller run is registered live for this session; pid %s "
            "answers alive, but PID REUSE IS NOT RULED OUT (a process may "
            "have reused this number since the real owner exited), so a "
            "live pid alone is not treated as sufficient proof of life"
            % (signals.heartbeat_age_seconds, stale_after_seconds, signals.pid))
    if signals.pid_alive is False:
        return DEAD, (
            "heartbeat is %.0fs old (over the %.0fs staleness window) and "
            "pid %s no longer exists"
            % (signals.heartbeat_age_seconds, stale_after_seconds, signals.pid))
    return DEAD, (
        "heartbeat is %.0fs old (over the %.0fs staleness window), no "
        "controller run is registered live for this session, and no pid is "
        "known for it either"
        % (signals.heartbeat_age_seconds, stale_after_seconds))


# ---------------------------------------------------------------------------
# SD2: the pure-read sweep.
# ---------------------------------------------------------------------------

STALE_FENCE = "stale-fence"
DEAD_WATCHDOG = "dead-watchdog"
DEAD_OWNER_PROVISIONAL = "dead-owner-provisional-record"
DUPLICATE_FENCE_LINES = "duplicate-fence-lines"
FOREIGN_COMMIT_BASE = "foreign-commit-base-invalidated"

FINDING_KINDS = (STALE_FENCE, DEAD_WATCHDOG, DEAD_OWNER_PROVISIONAL,
                 DUPLICATE_FENCE_LINES, FOREIGN_COMMIT_BASE)


def _finding(kind, severity, lifecycle_uuid, name, message, actions):
    return {"kind": kind, "severity": severity,
            "lifecycle_uuid": lifecycle_uuid, "name": name,
            "message": message, "actions": list(actions)}


def _scrub(bs, value):
    """The same two-function funnel bm_store.verify() applies to every
    string it builds from a record's own name or a claim's own path
    (redact_text for secret shapes, mask_absolute_paths for the founder's
    filesystem layout): a Finding's message is exactly the kind of
    ungated diagnostic output that scrub exists for."""
    return bs.mask_absolute_paths(bs.redact_text(value or ""))


def _active_records(bs, store):
    return [dict(r) for r in bs._exec(
        store,
        "SELECT lifecycle_uuid, name, state, session_id, owner, version, "
        "created_at, updated_at FROM records WHERE state='active' "
        "ORDER BY name, lifecycle_uuid").fetchall()]


def _claims_for(bs, store, lifecycle_uuid):
    return [r["path"] for r in bs._exec(
        store, "SELECT path FROM claims WHERE lifecycle_uuid=? ORDER BY path",
        (lifecycle_uuid,)).fetchall()]


def _latest_transition_at(bs, store, lifecycle_uuid):
    row = bs._exec(
        store, "SELECT at FROM transitions WHERE lifecycle_uuid=? "
              "ORDER BY id DESC LIMIT 1", (lifecycle_uuid,)).fetchone()
    return row["at"] if row else None


def _provisional_row(bs, store, lifecycle_uuid):
    row = bs._exec(
        store, "SELECT lifecycle_uuid, requested_name, created_session_id, "
              "created_at, promoted_at, cancelled_at FROM provisional_records "
              "WHERE lifecycle_uuid=?", (lifecycle_uuid,)).fetchone()
    return dict(row) if row else None


def _controller_terminal_states(bs):
    return frozenset(
        s for s, legal in bs.CONTROLLER_STATE_TRANSITIONS.items() if not legal)


def _session_registered_active(bs, store, session_id, terminal_states):
    """(registered, newest_timestamp). registered is True exactly when
    `session_id` is the recorded driver of a controller_runs row still in
    a non-terminal state -- SD.1's "controller state" signal. The newest
    timestamp seen (registered or not) is returned too, folded into the
    heartbeat computation either way: a controller run that already ended
    is still real, recent activity for that session."""
    if not session_id:
        return False, None
    rows = bs._exec(
        store, "SELECT state, updated_at FROM controller_runs "
              "WHERE session_id=? ORDER BY updated_at DESC",
        (session_id,)).fetchall()
    newest = rows[0]["updated_at"] if rows else None
    for r in rows:
        if r["state"] not in terminal_states:
            return True, r["updated_at"]
    return False, newest


def _latest_checkpoint_at(bs, store, session_id):
    if not session_id:
        return None
    row = bs._exec(
        store, "SELECT created_at FROM autonomy_checkpoints "
              "WHERE session_id=? ORDER BY created_at DESC LIMIT 1",
        (session_id,)).fetchone()
    return row["created_at"] if row else None


def _latest_dispatch_activity(bs, store, session_id):
    if not session_id:
        return None
    row = bs._exec(
        store, "SELECT created_at, resulted_at FROM controller_dispatches "
              "WHERE session_id=? ORDER BY created_at DESC LIMIT 1",
        (session_id,)).fetchone()
    if row is None:
        return None
    return row["resulted_at"] or row["created_at"]


def _owner_signals(bs, store, record, terminal_states, pid_hints, now):
    session_id = record["session_id"] or ""
    candidates = [record["updated_at"]]
    candidates.append(_latest_transition_at(bs, store, record["lifecycle_uuid"]))
    registered, controller_ts = _session_registered_active(
        bs, store, session_id, terminal_states)
    candidates.append(controller_ts)
    candidates.append(_latest_checkpoint_at(bs, store, session_id))
    candidates.append(_latest_dispatch_activity(bs, store, session_id))
    parsed = [p for p in (bs.parse_iso_stamp(c) for c in candidates if c)
             if p is not None]
    heartbeat_age_seconds = None
    if parsed:
        heartbeat_age_seconds = max(0.0, (now - max(parsed)).total_seconds())
    pid = (pid_hints or {}).get(session_id)
    pid_alive = process_alive(pid) if pid is not None else None
    return OwnerSignals(pid=pid, pid_alive=pid_alive,
                        heartbeat_age_seconds=heartbeat_age_seconds,
                        session_registered_active=registered)


def _resolved_cmd(bs, script_name, module_basename):
    """The paste-able command in the reader's actual layout (P17 rule via
    bm_store.invocation); degrades to the sibling module's absolute path
    when bm_store is unavailable, never to a repo-relative lie."""
    path = os.path.join(HERE, module_basename)
    if bs is not None:
        try:
            return bs.invocation(script_name, path)
        except Exception:  # sbe: allow-silent documented above: degrades to the sibling module's absolute path when bm_store is unavailable
            pass
    return "python3 %s" % path


def _release_and_adopt_actions(record, bs=None):
    """SD5, and the dashboard's own D-3: propose, never seize. Both
    commands are exactly what a founder would type by hand off
    tools/bm_store.py's own CLI (see cmd_park/cmd_adopt).

    PARK ALONE CANNOT RELEASE SOMEONE ELSE'S FENCE. Measured, not assumed
    (tools/bm_store.py's own transition() guard, the 'not-owner' refusal
    at :11712): park refuses outright the moment the record's session_id
    differs from the caller's, in EVERY case, with no override -- "only
    that session may move it to 'parked'". The store has no notion of
    dead at the transition layer; ADOPT is the one door that can move a
    fence held by a different session, and even then only with
    --adopt-from-live-session, because the store itself cannot tell a
    dead session from a live one (that is this whole feature's reason to
    exist) and always demands the override in the caller's OWN words. So
    the one command actually printed here for a THIRD PARTY recovering a
    dead fence is adopt, with the override, not park; park is named in
    the action's own label as the thing to do INSTEAD if the reader
    turns out to actually be the original session (its own process
    simply lost its heartbeat trail some other way), because in that one
    case park needs no override and is the gentler move."""
    store_cmd = _resolved_cmd(bs, "bm-store", "bm_store.py")
    adopt_cmd = (
        "%s adopt %s --version %s --session "
        "<YOUR_SESSION_ID> --adopt-from-live-session"
        % (store_cmd, record["lifecycle_uuid"], record["version"]))
    park_cmd = (
        "%s park %s --version %s --session %s "
        "--note \"released: this session's own work is done\""
        % (store_cmd, record["lifecycle_uuid"], record["version"],
           record["session_id"]))
    return [
        {"label": "adopt this fence and continue (or release) the work "
                  "yourself -- the store cannot tell a dead session from "
                  "a live one, so this requires the explicit override",
         "command": adopt_cmd},
        {"label": "if you ARE session %s and simply lost your heartbeat "
                  "trail, park it yourself instead (no override needed, "
                  "only that exact session may run this)"
                  % (record["session_id"] or "(unknown)"),
         "command": park_cmd},
    ]


def foreign_commit_base_finding(record, claimed_base_sha, current_head_sha, bs=None):
    """SD2's 'foreign commit that invalidates the current execution base',
    PROGRAM-PLAN.md's own phrasing: 'where git facts are handed in'. Git
    facts are PARAMETERS, never fetched here (this module spawns no
    subprocess; see the module docstring's EFFECT CLASS section). None
    when either sha is unknown or when they match (nothing foreign)."""
    if not claimed_base_sha or not current_head_sha:
        return None
    if claimed_base_sha == current_head_sha:
        return None
    return _finding(
        FOREIGN_COMMIT_BASE, "high", record["lifecycle_uuid"], record["name"],
        "active fence '%s' (%s) was claimed against commit %s, but the "
        "current execution base is %s: a foreign commit has landed under "
        "this work since it was claimed. Re-verify the claim's own files "
        "against the new base before trusting anything checkpointed on it."
        % (record["name"], record["lifecycle_uuid"][:8], claimed_base_sha[:12],
           current_head_sha[:12]),
        actions=_release_and_adopt_actions(record, bs))


def sweep(bs, store, now=None, stale_after_seconds=DEFAULT_STALE_AFTER_SECONDS,
          pid_hints=None):
    """The whole SD2 pass over one already-open ReadOnlyStore. Returns a
    list of Finding dicts (possibly empty: a genuinely healthy store is a
    real, legitimate result, distinct from NO-DATA -- see this module's
    cmd_sweep for where that line is actually drawn).

    Read-only by construction: every query in this function goes through
    bs._exec against `store`, which must be a bs.ReadOnlyStore (an
    ordinary bs.Store would satisfy the same SQL, but constructing one is
    itself a write; see the module docstring's EFFECT CLASS section, and
    tools/bm_effects.py's own root-cause note this project keeps
    rediscovering)."""
    now = now or datetime.datetime.now(datetime.timezone.utc)
    terminal_states = _controller_terminal_states(bs)
    records = _active_records(bs, store)
    by_uuid = {r["lifecycle_uuid"]: r for r in records}
    claims_by_uuid = {u: _claims_for(bs, store, u) for u in by_uuid}

    findings = []

    # -- duplicate/contradictory active fences --------------------------
    # A drift detector, mirroring bm_store.verify()'s own dupes/overlap
    # query and its own reasoning: the store's write-time checks (the
    # partial unique index on records.name, claim()'s own overlap refusal)
    # should prevent this under normal use, which is exactly why seeing it
    # anyway is worth a loud finding rather than a silent shrug -- it means
    # the rows arrived some OTHER way (a migration, a restore, a hand
    # edit), the precise shape of "duplicate fence lines" 2026-08-10
    # actually produced.
    by_name = {}
    for u, r in by_uuid.items():
        by_name.setdefault(r["name"], []).append(u)
    for name, uuids in by_name.items():
        if len(uuids) <= 1:
            continue
        actions = []
        for u in sorted(uuids):
            actions.extend(_release_and_adopt_actions(by_uuid[u]))
        findings.append(_finding(
            DUPLICATE_FENCE_LINES, "high", uuids[0], name,
            "%d active fences share the name %r: %s. The store's own "
            "unique index should prevent two ACTIVE records with the same "
            "name; seeing it anyway means these rows did not all arrive "
            "through the normal claim() path. Keep the newest, reconcile "
            "or release the rest."
            % (len(uuids), _scrub(bs, name),
               ", ".join(sorted(u[:8] for u in uuids))),
            actions=actions))

    flat_claims = [(u, p) for u in claims_by_uuid for p in claims_by_uuid[u]]
    seen_pairs = set()
    for i in range(len(flat_claims)):
        for j in range(i + 1, len(flat_claims)):
            ua, pa = flat_claims[i]
            ub, pb = flat_claims[j]
            if ua == ub or (ua, ub) in seen_pairs or (ub, ua) in seen_pairs:
                continue
            if bs.paths_overlap(pa, pb):
                seen_pairs.add((ua, ub))
                findings.append(_finding(
                    DUPLICATE_FENCE_LINES, "high", ua, by_uuid[ua]["name"],
                    "active claims overlap: '%s' (%s) path %r vs '%s' (%s) "
                    "path %r"
                    % (_scrub(bs, by_uuid[ua]["name"]), ua[:8], _scrub(bs, pa),
                       _scrub(bs, by_uuid[ub]["name"]), ub[:8], _scrub(bs, pb)),
                    actions=(_release_and_adopt_actions(by_uuid[ua])
                            + _release_and_adopt_actions(by_uuid[ub]))))

    # -- stale fences (including dead watchdogs) and dead-owner ---------
    # provisional records, both judged by the SD1 oracle.
    for u, record in by_uuid.items():
        signals = _owner_signals(bs, store, record, terminal_states,
                                 pid_hints, now)
        verdict, reason = owner_liveness(signals, stale_after_seconds)
        if verdict != DEAD:
            continue
        is_watchdog = "watchdog" in (record["name"] or "").lower()
        kind = DEAD_WATCHDOG if is_watchdog else STALE_FENCE
        label = "dead watchdog" if is_watchdog else "stale fence"
        findings.append(_finding(
            kind, "high", u, record["name"],
            "%s: active fence '%s' (%s, session %s) has a dead owner -- %s"
            % (label, _scrub(bs, record["name"]), u[:8],
               _scrub(bs, record["session_id"]) or "(no session recorded)",
               reason),
            actions=_release_and_adopt_actions(record, bs)))

        prov = _provisional_row(bs, store, u)
        if prov is not None and not prov["promoted_at"] and not prov["cancelled_at"]:
            learn_cmd = _resolved_cmd(bs, "bm-learn", "bm_learn.py")
            cancel_cmd = (
                "%s cancel %s --session "
                "<YOUR_SESSION_ID> --because \"dead owner found by "
                "bm_stall sweep\"" % (learn_cmd, u))
            promote_cmd = "%s promote %s" % (learn_cmd, u)
            findings.append(_finding(
                DEAD_OWNER_PROVISIONAL, "medium", u, record["name"],
                "provisional record '%s' (%s) was created by session %s, "
                "never promoted or cancelled, and that session's fence is "
                "dead -- %s"
                % (_scrub(bs, record["name"]), u[:8],
                   _scrub(bs, record["session_id"]) or "(no session recorded)",
                   reason),
                actions=[
                    {"label": "cancel the provisional record (it was never "
                              "promoted to real work)",
                     "command": cancel_cmd},
                    {"label": "promote it if the work is real and should "
                              "continue",
                     "command": promote_cmd}]))

    return findings


# ---------------------------------------------------------------------------
# CLI.
# ---------------------------------------------------------------------------

def _out(msg=""):
    sys.stdout.write("%s\n" % msg)


def _err(msg):
    sys.stderr.write("%s\n" % msg)


def resolve_project_root(bs, explicit_root):
    """(root, None) or (None, reason). Mirrors
    tools/bm_progress_check.py:resolve_project_root exactly: --root always
    wins; otherwise bs.resolve_root() is borrowed for that lookup alone,
    never opening a store just to find one."""
    if explicit_root:
        candidate = os.path.realpath(os.path.expanduser(explicit_root))
        if not os.path.isdir(candidate):
            return None, "no such directory: %s" % candidate
        return candidate, None
    root, _source = bs.resolve_root()
    if not root:
        return None, ("nothing anchors a BrotherMode project here (no "
                      "BROTHERMODE_ROOT, no marker directory, no git repo "
                      "found); pass --root explicitly")
    return root, None


def _parse_pid_hint(text):
    if "=" not in text:
        raise ValueError("--pid-hint needs SESSION_ID=PID, got %r" % text)
    session_id, pid_text = text.split("=", 1)
    return session_id, int(pid_text)


def _parse_argv(argv):
    """(verb, kv, error). kv holds root/now/stale-seconds/json/pid-hints;
    error set means print it and exit 2 (a plain usage failure, not a
    NO-DATA verdict -- that prefix is reserved for 'could not determine',
    mirroring tools/bm_progress_check.py's own split)."""
    args = list(argv)
    verb = "sweep"
    if args and not args[0].startswith("--"):
        verb = args.pop(0)
    if verb != "sweep":
        return None, None, "bm_stall: unknown verb: %s" % verb
    kv = {"root": None, "now": None, "stale_seconds": None, "json": False,
          "pid_hints": {}}
    i = 0
    while i < len(args):
        arg = args[i]
        if arg == "--root":
            if i + 1 >= len(args):
                return None, None, "bm_stall: --root requires a value"
            kv["root"] = args[i + 1]; i += 2
        elif arg == "--now":
            if i + 1 >= len(args):
                return None, None, "bm_stall: --now requires a value"
            kv["now"] = args[i + 1]; i += 2
        elif arg == "--stale-seconds":
            if i + 1 >= len(args):
                return None, None, "bm_stall: --stale-seconds requires a value"
            try:
                kv["stale_seconds"] = int(args[i + 1])
            except ValueError:  # sbe: allow-silent not silent: returns (None, None, message), the message is the usage error the caller prints and exits 2 on
                return None, None, ("bm_stall: --stale-seconds must be an "
                                    "integer, got %r" % args[i + 1])
            i += 2
        elif arg == "--pid-hint":
            if i + 1 >= len(args):
                return None, None, "bm_stall: --pid-hint requires a value"
            try:
                session_id, pid = _parse_pid_hint(args[i + 1])
            except ValueError as exc:
                return None, None, "bm_stall: %s" % exc
            kv["pid_hints"][session_id] = pid; i += 2
        elif arg == "--json":
            kv["json"] = True; i += 1
        else:
            return None, None, "bm_stall: unknown argument: %s" % arg
    return verb, kv, None


def _render_text(findings):
    lines = []
    by_kind = {}
    for f in findings:
        by_kind.setdefault(f["kind"], 0)
        by_kind[f["kind"]] += 1
    if not findings:
        lines.append("bm_stall sweep: 0 finding(s). No active fence, "
                     "provisional record, or claim looks stale or "
                     "contradictory right now.")
        return "\n".join(lines)
    lines.append("bm_stall sweep: %d finding(s) (%s)"
                 % (len(findings),
                    ", ".join("%s=%d" % (k, n)
                             for k, n in sorted(by_kind.items()))))
    for f in findings:
        lines.append("")
        lines.append("[%s/%s] %s (%s)"
                     % (f["severity"], f["kind"], f["name"] or "(unnamed)",
                        (f["lifecycle_uuid"] or "")[:8]))
        lines.append("  %s" % f["message"])
        for action in f["actions"]:
            lines.append("  -> %s" % action["label"])
            lines.append("     %s" % action["command"])
    return "\n".join(lines)


def _run(argv):
    verb, kv, err = _parse_argv(argv)
    if err:
        _err(err)
        return 2
    bs, load_err = _load_bm_store()
    if bs is None:
        _out("NO-DATA: could not load bm_store.py (%s)" % load_err)
        return 2
    root, reason = resolve_project_root(bs, kv["root"])
    if root is None:
        _out("NO-DATA: %s" % reason)
        return 2
    now = datetime.datetime.now(datetime.timezone.utc)
    if kv["now"]:
        parsed_now = bs.parse_iso_stamp(kv["now"])
        if parsed_now is None:
            _out("NO-DATA: --now %r is not in the store's own timestamp "
                "format (%s)" % (kv["now"], bs._ISO_STAMP_FORMAT))
            return 2
        now = parsed_now
    stale_seconds = (kv["stale_seconds"] if kv["stale_seconds"] is not None
                     else DEFAULT_STALE_AFTER_SECONDS)
    try:
        store = bs.ReadOnlyStore(root)
    except bs.OwnershipRefused as exc:
        _out("NO-DATA: refused (%s): %s" % (exc.reason, exc))
        return 2
    try:
        findings = sweep(bs, store, now=now, stale_after_seconds=stale_seconds,
                         pid_hints=kv["pid_hints"])
    finally:
        store.close()
    if kv["json"]:
        _out(json.dumps({"findings": findings}, indent=2, sort_keys=True))
    else:
        _out(_render_text(findings))
    return 1 if findings else 0


def main(argv):
    try:
        return _run(argv)
    except Exception as exc:
        _out("NO-DATA: %s: %s" % (type(exc).__name__, exc))
        return 2


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))

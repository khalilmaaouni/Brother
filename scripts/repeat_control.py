#!/usr/bin/env python3
"""repeat_control: does a shown lesson actually stop the same failure happening again.

learning_loop.priority item 5 (docs/plan/READINESS-ROADMAP-2026-08-29.json) and its
design row E53 both say the same thing: the estate's recall counter measures lessons
SHOWN, never repeats PREVENTED. This is the instrument for the second half, built
once and run once against the estate's own real logs, honestly reporting NO-DATA
where the two-week comparison it is designed for has not run yet.

SOURCES READ, all real, on disk, nothing here typed by hand.

  * ~/.claude/repeat-guard/<session_id>.jsonl -- tools/repeat-guard/repeat_guard.py's
    own PostToolUse state, one FILE per real session id (the same id
    products/brothermode/tools/vault_recall_hook.py's payload carries), one JSON
    line per tool call: {"sig", "approach", "ok", "exit_code", "err"}. No line
    carries a timestamp, so this file's own mtime is the only time signal
    available and is used as the session's clock. This is the ONLY source here
    that carries a real per-session id, so it is the SESSION UNIVERSE this script
    counts over; "sig" (repeat_guard.py's own stable fingerprint of an approach,
    documented in its own signature() docstring) stands in for "failure class"
    below, because the attempt ledger's declared class cannot be joined to a
    session (see the next source).

  * ~/.claude/.vault_recall_seen -- products/brothermode/tools/vault_recall_hook.py's
    own once-per-session marker, plain text, one "SESSION:BASENAME" key per
    line. Marked ONLY when cmd_check actually printed RECORDED FAILURES to the
    model, or the fixed "SESSION:__unconfigured__" sentinel when no tools root
    was ever set for that session (never a shown lesson). A real, non-sentinel
    entry for a session id is this script's definition of "a lesson was shown"
    for that session.

  * the attempt ledger, scripts/attempt_ledger.py's own STORE (default
    ~/.claude/attempt-ledger/attempts.jsonl): read for its own record count.
    Its record() writes {"problem", "class", "outcome", "note"} only -- no
    session id, no timestamp -- so a row here cannot be joined to a session or
    placed in time. Reported, never folded into the arm comparison; that gap is
    named plainly in the output rather than papered over.

WHY NOT products/brothermode/tools/bm_vault_audit.py's own audit log. Its row
shape ({"ts","event_id","principal","query","served_ids","withheld_count",
"purpose"}) has no session id either, only a caller-supplied "principal" (three
distinct values on this machine's real file: alice, NO-DATA, fable-orchestrator).
Worse: products/brothermode/tools/bm_vault.py's own cmd_check -- the function
vault_recall_hook.py's PreToolUse handler actually calls -- never calls
_append_audit; only cmd_recall (a separate, manual "recall --query" CLI path)
does. So that file records manual `bm_vault.py recall` usage, not the
point-of-need hook this experiment is about, and even if it did it would still
have no session id to key on. .vault_recall_seen is the hook's own record of its
own firing, keyed by the same real session id repeat-guard already uses, so it
is what this script reads instead.

THE ARM. A session (one repeat-guard file) is "recall on" when .vault_recall_seen
holds a real (non-sentinel) entry for its session id, "recall off" when it does
not. --start names the date row E53's own design (assigned by session parity of
the day, for two weeks) actually begins: a session whose repeat-guard file was
last written before that date is classified by presence as above, regardless of
the flag, because the parity design has not started for it; a session on or after
that date is classified by day parity instead (even days since --start: on; odd:
off). As of this build no real session has ever run on or after a plausible
--start, so the parity branch is exercised only by the tests below, never by the
driven run.

A REPEAT. Ordering sessions by their repeat-guard file's own mtime, a repeat is:
a failing sig (ok is False) in session E, the SAME sig failing again (ok is
False) in a strictly later session L, with no failure of that sig in between
attributed elsewhere -- pairwise, so three failures of one sig across three
sessions count as two repeats, one charged to each earlier session of the pair.
It is charged to arm(E): for an "on" session E this is exactly row E53's own
wording ("the same failure class recurring in a later session after the lesson
was shown"), since E being "on" means a lesson was shown in it by construction;
an "off" session E never had a lesson shown, so its repeat rate is the baseline
this whole experiment exists to compare against. A second failure of the same
sig inside ONE session never counts: only distinct, later session files do.

NO-DATA is never a zero. An absent guard directory, an absent recall-seen file,
or an absent ledger file each print their own NO-DATA line naming the path they
looked for, and an arm short of --min-sessions prints its own NO-DATA line
rather than a computed rate nobody should trust.

Python 3.9, standard library only. Read-only: this script never writes to any of
the three paths above.
"""
import argparse
import datetime
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import attempt_ledger  # noqa: E402

DEFAULT_GUARD_DIR = os.path.join(os.path.expanduser("~"), ".claude", "repeat-guard")
DEFAULT_RECALL_LOG = os.path.join(os.path.expanduser("~"), ".claude", ".vault_recall_seen")
DEFAULT_LEDGER = str(attempt_ledger.STORE)
DEFAULT_MIN_SESSIONS = 5

UNCONFIGURED_SENTINEL = "__unconfigured__"


def read_guard_sessions(guard_dir):
    """dict session_id -> {"mtime": float, "rows": [...]}, or None when
    guard_dir does not exist. A malformed or unreadable individual file is
    skipped (never crashes the read), the same defensive posture
    repeat_guard.py's own read_attempts already takes."""
    if not os.path.isdir(guard_dir):
        return None
    sessions = {}
    for name in sorted(os.listdir(guard_dir)):
        if not name.endswith(".jsonl"):
            continue
        path = os.path.join(guard_dir, name)
        session_id = name[:-len(".jsonl")]
        try:
            mtime = os.path.getmtime(path)
        except OSError as e:
            sys.stderr.write("repeat_control: %s vanished mid-scan (%s), skipping\n"
                             % (path, e))
            continue
        rows = []
        try:
            with open(path, encoding="utf-8") as f:
                for n, line in enumerate(f, 1):
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        rows.append(json.loads(line))
                    except ValueError as e:
                        sys.stderr.write("repeat_control: %s:%d is not valid JSON, "
                                         "skipping (%s)\n" % (path, n, e))
                        continue
        except OSError as e:
            sys.stderr.write("repeat_control: %s could not be read (%s), skipping\n"
                             % (path, e))
            continue
        sessions[session_id] = {"mtime": mtime, "rows": rows}
    return sessions


def read_shown_sessions(recall_log):
    """dict session_id -> True for every session with a real (non-sentinel)
    entry, or None when recall_log does not exist. Reads the plain
    "SESSION:BASENAME" lines vault_recall_hook.py's _mark_seen writes."""
    if not os.path.exists(recall_log):
        return None
    shown = {}
    try:
        with open(recall_log, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line or ":" not in line:
                    continue
                session, _, base = line.partition(":")
                if base == UNCONFIGURED_SENTINEL:
                    continue
                shown[session] = True
    except OSError as e:
        # sbe: allow-silent None is an explicit sentinel, not a swallow: run()
        # branches on it and prints its own "NO-DATA: no recall-seen log at
        # <path>" line, the same explicit-None-sentinel contract
        # attempt_ledger.read()'s own OSError branch already uses.
        sys.stderr.write("repeat_control: %s could not be read (%s)\n" % (recall_log, e))
        return None
    return shown


def classify_session(session_id, mtime, shown_map, start_date):
    """('on'|'off'). shown_map may be None (recall log absent -> every
    session classifies 'off' by presence, honestly reflecting that nothing
    was ever recorded as shown)."""
    if start_date is not None:
        day = datetime.date.fromtimestamp(mtime)
        if day >= start_date:
            parity = (day.toordinal() - start_date.toordinal()) % 2
            return "on" if parity == 0 else "off"
    return "on" if (shown_map and shown_map.get(session_id)) else "off"


def compute_repeats(sessions):
    """dict session_id -> repeat count charged to it, per the pairwise,
    chronological definition in the module docstring."""
    ordered = sorted(sessions.items(), key=lambda kv: kv[1]["mtime"])
    last_fail_session = {}
    repeats_by_session = {}
    for session_id, data in ordered:
        failed_sigs = {row.get("sig") for row in data["rows"]
                       if row.get("sig") is not None and row.get("ok") is False}
        for sig in failed_sigs:
            prev = last_fail_session.get(sig)
            if prev is not None and prev != session_id:
                repeats_by_session[prev] = repeats_by_session.get(prev, 0) + 1
            last_fail_session[sig] = session_id
    return repeats_by_session


def arm_report(label, session_ids, sessions, shown_map, repeats_by_session, min_sessions):
    """(line, stats-or-None). stats is None exactly when this arm is NO-DATA."""
    n = len(session_ids)
    if n < min_sessions:
        return ("NO-DATA: %s has %d session(s), fewer than %d" % (label, n, min_sessions),
                None)
    attempts = sum(len(sessions[s]["rows"]) for s in session_ids)
    lessons_shown = sum(1 for s in session_ids if shown_map and shown_map.get(s))
    repeats = sum(repeats_by_session.get(s, 0) for s in session_ids)
    rate = (100.0 * repeats / attempts) if attempts else 0.0
    line = ("%s: %d session(s), %d tool call(s), %d lesson(s) shown, %d repeat(s), "
            "%.2f repeat(s) per hundred attempts" % (label, n, attempts, lessons_shown,
                                                       repeats, rate))
    return line, {"sessions": n, "attempts": attempts, "lessons_shown": lessons_shown,
                   "repeats": repeats, "rate": rate}


def run(guard_dir=DEFAULT_GUARD_DIR, recall_log=DEFAULT_RECALL_LOG,
        ledger=DEFAULT_LEDGER, min_sessions=DEFAULT_MIN_SESSIONS, start=None,
        out=sys.stdout):
    """Returns the process exit code: 0 when both arms report, 2 otherwise."""
    start_date = None
    if start:
        start_date = datetime.date.fromisoformat(start)

    sessions = read_guard_sessions(guard_dir)
    if sessions is None:
        print("NO-DATA: no repeat-guard directory at %s" % guard_dir, file=out)
        sessions = {}

    shown_map = read_shown_sessions(recall_log)
    if shown_map is None:
        print("NO-DATA: no recall-seen log at %s" % recall_log, file=out)

    if os.path.exists(ledger):
        rows = attempt_ledger.read(ledger)
        if rows is None:
            print("NO-DATA: attempt ledger at %s could not be read" % ledger, file=out)
        else:
            print("attempt ledger: %d record(s) at %s (no session id or timestamp "
                  "in this file's shape; not counted in the arm comparison below)"
                  % (len(rows), ledger), file=out)
    else:
        print("NO-DATA: no attempt ledger at %s" % ledger, file=out)

    repeats_by_session = compute_repeats(sessions)

    arms = {"on": [], "off": []}
    for session_id, data in sessions.items():
        arm = classify_session(session_id, data["mtime"], shown_map, start_date)
        arms[arm].append(session_id)

    stats = {}
    for key, label in (("on", "recall on"), ("off", "recall off")):
        line, s = arm_report(label, arms[key], sessions, shown_map,
                              repeats_by_session, min_sessions)
        print(line, file=out)
        stats[key] = s

    if stats["on"] and stats["off"]:
        on_rate, off_rate = stats["on"]["rate"], stats["off"]["rate"]
        print("comparison: recall on repeats at %.2f per hundred attempts, "
              "recall off at %.2f, difference %.2f"
              % (on_rate, off_rate, on_rate - off_rate), file=out)
        return 0
    print("NO-DATA: the comparison needs both arms", file=out)
    return 2


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--start", default=None,
                     help="YYYY-MM-DD the row E53 day-parity design begins")
    ap.add_argument("--recall-log", default=DEFAULT_RECALL_LOG)
    ap.add_argument("--ledger", default=DEFAULT_LEDGER)
    ap.add_argument("--guard-log", dest="guard_log", default=DEFAULT_GUARD_DIR)
    ap.add_argument("--min-sessions", dest="min_sessions", type=int,
                     default=DEFAULT_MIN_SESSIONS)
    args = ap.parse_args(list(sys.argv[1:] if argv is None else argv))
    return run(guard_dir=args.guard_log, recall_log=args.recall_log,
               ledger=args.ledger, min_sessions=args.min_sessions, start=args.start)


if __name__ == "__main__":
    sys.exit(main())

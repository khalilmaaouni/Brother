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

THE ARM. A session (one repeat-guard file) is "recall on" when the recall hook
actually showed at least one lesson IN IT, never by the calendar. FIX 2026-09-05
(evidence audit, lane E53 instrument-honest): the previous build classified any
session on or after --start by (day minus start).days modulo 2 "regardless of
the flag", which made the arm label a coin flip on the calendar date rather than
a read of what happened, of 22 real lesson-shown sessions, 21 landed in the arm
that flip called "off". The mechanism is the only source of truth now, for every
session regardless of its date: a real (non-sentinel) entry in
.vault_recall_seen for that session id, OR a "vault_recall" row in the shared
hook-outcomes log naming that session with lessons_shown > 0 (see
merge_shown_sessions). --start's day-parity SCHEDULE is still computed and
printed as its own "scheduled arm by parity" line, because it is the design's
stated intent and worth seeing, but it decides nothing.

WINDOWING. FIX 2026-09-05 (same audit): --start used to accept every session
ever recorded, regardless of date, so 435 of 553 guard files (spanning
2026-08-24 to 2026-09-05, before any plausible --start) were counted into an arm
the design had not started for. A session whose repeat-guard file's own mtime
falls before --start is now excluded from BOTH arms entirely (not classified
into either), reported as its own "excluded: N session(s) before <date>" line,
and --min-sessions is checked AFTER exclusion.

A REPEAT, the SECONDARY signal (same-sig cross-session collision). Ordering
sessions by their repeat-guard file's own mtime, a repeat is: a failing sig (ok
is False) in session E, the SAME sig failing again (ok is False) in a strictly
later session L, with no failure of that sig in between attributed elsewhere,
pairwise, so three failures of one sig across three sessions count as two
repeats, one charged to each earlier session of the pair. It is charged to
arm(E): for an "on" session E this is exactly row E53's own wording ("the same
failure class recurring in a later session after the lesson was shown"), since E
being "on" means a lesson was shown in it by construction; an "off" session E
never had a lesson shown, so its repeat rate is the baseline this whole
experiment exists to compare against. A second failure of the same sig inside
ONE session never counts: only distinct, later session files do.

FIX 2026-09-05 (same audit): this detector has never once fired on this
machine's real corpus, 90,873 rows, 465 with ok is False, 440 distinct sigs,
ZERO sigs failing in more than one session. When the whole corpus (the sessions
actually in scope, after windowing) shows zero cross-session collisions, the
rate line says so by name rather than printing a trustworthy-looking "0.00
repeat(s) per hundred attempts": a detector that has never fired even once
cannot yet be told apart from a detector that is broken, and 0.00 reads exactly
like the first when it might be the second.

THE PRIMARY signal, added the same fix: the E53.5 subtask
(scripts/lesson_repeat_trial.py) already built and ran a different, working
repeat detector, a REPLAY of the estate's own run_evidence captures through
repeat_guard's real matcher, asking per real failure whether the lesson that
describes it was already recorded at that moment (SHOWN) or not (SILENT), and it
fires: 4 of 4 on the one real run so far. That module's own build_log,
read_lessons and replay functions are imported and reused here verbatim (never a
second implementation of the same rule) as read_e53_5_signal's primary line,
printed once, corpus-wide: the run_evidence store carries no session id, so this
signal cannot be split into the on/off arms the way the secondary signal is.

NO-DATA is never a zero. An absent guard directory, an absent recall-seen file,
an absent ledger file, an absent evidence store, or an absent lesson store each
print their own NO-DATA line naming the path they looked for, and an arm short
of --min-sessions (after windowing) prints its own NO-DATA line rather than a
computed rate nobody should trust.

Python 3.9, standard library only. Read-only: this script never writes to any of
the paths it reads, including the two the primary signal adds (the run_evidence
store and repeat_guard's own lesson store).
"""
import argparse
import datetime
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
# C3: the config directory is resolved by brother_paths, the one seam
# that knows which coding client is running (docs/codex/HOOKS-MAPPING.md).
import brother_paths  # noqa: E402
import attempt_ledger  # noqa: E402
# The E53.5 primary signal, reused rather than reimplemented (see THE
# PRIMARY signal in the module docstring above).
import lesson_repeat_trial  # noqa: E402

# NOT routed through brother_paths on purpose: this directory is written
# by ~/.claude/hooks/repeat_guard.py, a MACHINE-LEVEL hook this product
# does not ship and cannot move. Reading it anywhere else would read an
# empty directory and report a real guard as absent.
DEFAULT_GUARD_DIR = os.path.join(os.path.expanduser("~"), ".claude", "repeat-guard")
DEFAULT_RECALL_LOG = brother_paths.config_path(".vault_recall_seen")
DEFAULT_LEDGER = str(attempt_ledger.STORE)
DEFAULT_MIN_SESSIONS = 5

# The primary signal's own two sources, lesson_repeat_trial's own defaults
# (its run_evidence store and repeat_guard's own lesson store), never a
# second copy of either path typed by hand here.
DEFAULT_EVIDENCE_STORE = lesson_repeat_trial.DEFAULT_STORE
DEFAULT_REPEAT_LESSONS = str(lesson_repeat_trial.repeat_guard.LESSONS)

UNCONFIGURED_SENTINEL = "__unconfigured__"

#: E57 mechanism 1, the outcome number beside the mechanism (borrowed from
#: MemOS, https://github.com/MemTensor/MemOS, whose repository publishes a
#: numeric outcome rather than only reporting that the mechanism fires). The
#: two hooks write one JSON line each per event: the recall hook writes
#: {"hook": "vault_recall", "session", "lessons_shown", "recall_chars",
#: "recall_tokens_est"} and the breaker writes {"hook": "attempt_breaker",
#: "session", "refusals", "kind"}. Read-only here, like every other source
#: this script reads.
DEFAULT_OUTCOMES = brother_paths.config_path("hook-outcomes.jsonl")


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


def read_hook_outcomes(path):
    """(rows, skipped) from the shared hook-outcome log, or (None, 0) when the
    file does not exist. A malformed line is skipped and counted rather than
    crashing the whole report, the same posture read_guard_sessions already
    takes for one bad guard file."""
    if not os.path.exists(path):
        return None, 0
    rows, skipped = [], 0
    try:
        with open(path, encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if not line:
                    continue
                try:
                    row = json.loads(line)
                except ValueError:
                    skipped += 1
                    continue
                if isinstance(row, dict):
                    rows.append(row)
                else:
                    skipped += 1
    except OSError as e:
        sys.stderr.write("repeat_control: %s could not be read (%s)\n" % (path, e))
        return None, skipped
    return rows, skipped


def outcome_lines(rows, path):
    """One line per hook naming its own outcome number, plus the sessions it
    covers. NEVER a zero when the log is absent: an absent log is NO-DATA and
    says so, naming the path it looked for."""
    if rows is None:
        return ["NO-DATA: no hook-outcome log at %s, so neither hook has "
                "reported its own outcome number" % path]
    if not rows:
        return ["NO-DATA: the hook-outcome log at %s is empty" % path]
    recall = [r for r in rows if r.get("hook") == "vault_recall"]
    breaker = [r for r in rows if r.get("hook") == "attempt_breaker"]
    lines = []
    if recall:
        lines.append(
            "hook outcome: vault_recall shown %d lesson(s) over %d session(s), "
            "costing about %d token(s) of context (estimate, four characters "
            "per token)"
            % (sum(int(r.get("lessons_shown") or 0) for r in recall),
               len({r.get("session") for r in recall}),
               sum(int(r.get("recall_tokens_est") or 0) for r in recall)))
    else:
        lines.append("NO-DATA: no vault_recall row in %s" % path)
    if breaker:
        lines.append(
            "hook outcome: attempt_breaker refused %d time(s) over %d "
            "session(s), %d of them an alternating-class loop"
            % (sum(int(r.get("refusals") or 0) for r in breaker),
               len({r.get("session") for r in breaker}),
               sum(1 for r in breaker if r.get("kind") == "alternating_classes")))
    else:
        lines.append("NO-DATA: no attempt_breaker row in %s" % path)
    return lines


def merge_shown_sessions(shown_map, outcome_rows):
    """dict session_id -> True, the MECHANISM: the union of both real
    firing records (defect 1 fix). shown_map (from .vault_recall_seen) may
    be None; outcome_rows (the shared hook-outcome log) may be None. Never
    consults --start or any calendar date -- that is the whole fix."""
    merged = dict(shown_map) if shown_map else {}
    if outcome_rows:
        for r in outcome_rows:
            if r.get("hook") != "vault_recall":
                continue
            session = r.get("session")
            if session and int(r.get("lessons_shown") or 0) > 0:
                merged[session] = True
    return merged


def classify_session(session_id, merged_shown):
    """('on'|'off') by mechanism alone: did the recall hook actually show a
    lesson in this session. merged_shown is merge_shown_sessions's output;
    an absent or empty map classifies every session 'off', honestly
    reflecting that nothing was ever recorded as shown."""
    return "on" if merged_shown.get(session_id) else "off"


def parity_schedule(mtime, start_date):
    """('on'|'off'), the row E53 day-parity SCHEDULE: informational only,
    printed so the design's stated intent is visible, never used to decide
    an arm (see defect 1 in the module docstring)."""
    day = datetime.date.fromtimestamp(mtime)
    parity = (day.toordinal() - start_date.toordinal()) % 2
    return "on" if parity == 0 else "off"


def compute_repeats(sessions):
    """dict session_id -> repeat count charged to it, per the pairwise,
    chronological definition in the module docstring (the SECONDARY
    signal)."""
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


def arm_report(label, session_ids, sessions, shown_map, repeats_by_session,
               min_sessions, corpus_zero_collisions):
    """(line, stats-or-None). stats is None exactly when this arm is
    NO-DATA for lack of sessions. When corpus_zero_collisions is True (the
    secondary detector has not collided anywhere in the sessions actually
    in scope), the rate portion of the line is NO-DATA rather than a
    printed 0.00 (defect 2 fix); stats["rate"] is then None too, so the
    comparison line downstream can tell the difference from a real zero."""
    n = len(session_ids)
    if n < min_sessions:
        return ("NO-DATA: %s has %d session(s), fewer than %d" % (label, n, min_sessions),
                None)
    attempts = sum(len(sessions[s]["rows"]) for s in session_ids)
    lessons_shown = sum(1 for s in session_ids if shown_map and shown_map.get(s))
    repeats = sum(repeats_by_session.get(s, 0) for s in session_ids)
    if corpus_zero_collisions:
        line = ("%s: %d session(s), %d tool call(s), %d lesson(s) shown, "
                "NO-DATA: repeat signal never collided across sessions: the "
                "fingerprint cannot be told from a detector that cannot fire"
                % (label, n, attempts, lessons_shown))
        return line, {"sessions": n, "attempts": attempts, "lessons_shown": lessons_shown,
                       "repeats": repeats, "rate": None}
    rate = (100.0 * repeats / attempts) if attempts else 0.0
    line = ("%s: %d session(s), %d tool call(s), %d lesson(s) shown, %d repeat(s), "
            "%.2f repeat(s) per hundred attempts" % (label, n, attempts, lessons_shown,
                                                       repeats, rate))
    return line, {"sessions": n, "attempts": attempts, "lessons_shown": lessons_shown,
                   "repeats": repeats, "rate": rate}


def read_e53_5_signal(store, lessons_path):
    """(line, stats-or-None), the PRIMARY signal. Reuses
    lesson_repeat_trial's own build_log, read_lessons and replay verbatim
    (never a second implementation of the same rule); see THE PRIMARY
    signal in the module docstring. stats is None exactly when this signal
    is NO-DATA (an absent store, an absent lesson file, or a corpus with
    no failure any lesson describes)."""
    result = lesson_repeat_trial.build_log(store)
    if result is None:
        return ("NO-DATA: no evidence store at %s for the primary repeat "
                "signal (E53.5 replay)" % store, None)
    rows, unreadable = result
    if not rows:
        return ("NO-DATA: %s holds no run_evidence capture for the primary "
                "repeat signal (E53.5 replay)" % store, None)
    lessons = lesson_repeat_trial.read_lessons(lessons_path)
    if lessons is None:
        return ("NO-DATA: no lesson store at %s for the primary repeat "
                "signal (E53.5 replay)" % lessons_path, None)
    arm_a = lesson_repeat_trial.replay(rows, lessons)
    arm_b = lesson_repeat_trial.replay(rows, [])  # memory off: the floor
    n = len(arm_a)
    if n == 0:
        return ("NO-DATA: %d command(s) replayed from %s, none of the "
                "failures matched any of the %d lesson(s) in %s for the "
                "primary repeat signal (E53.5 replay)"
                % (len(rows), store, len(lessons), lessons_path), None)
    shown_a = sum(1 for r in arm_a if r["lesson"])
    shown_b = sum(1 for r in arm_b if r["lesson"])
    line = ("primary repeat signal (E53.5 replay, reused from "
            "lesson_repeat_trial.py): shown before the repeat: %d of %d "
            "failure(s) with recall as it happened, %d of %d with memory "
            "off, replayed from %d command(s) in %s against %d lesson(s) "
            "in %s" % (shown_a, n, shown_b, n, len(rows), store, len(lessons),
                       lessons_path))
    return line, {"n": n, "shown_a": shown_a, "shown_b": shown_b}


def run(guard_dir=DEFAULT_GUARD_DIR, recall_log=DEFAULT_RECALL_LOG,
        ledger=DEFAULT_LEDGER, min_sessions=DEFAULT_MIN_SESSIONS, start=None,
        out=sys.stdout, outcomes=None, evidence_store=DEFAULT_EVIDENCE_STORE,
        repeat_lessons=DEFAULT_REPEAT_LESSONS):
    """Returns the process exit code: 0 when both arms report (by session
    count), 2 otherwise. Exit code reflects the secondary (arm) signal's
    session coverage only, unchanged from before this fix; both signals'
    own NO-DATA lines are printed regardless of the exit code."""
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

    outcomes_path = DEFAULT_OUTCOMES if outcomes is None else outcomes
    outcome_rows, skipped = read_hook_outcomes(outcomes_path)
    if skipped:
        print("hook outcome: %d malformed line(s) skipped in %s"
              % (skipped, outcomes_path), file=out)
    for line in outcome_lines(outcome_rows, outcomes_path):
        print(line, file=out)

    # C3 fix: window by --start, excluding pre-start sessions from BOTH
    # arms entirely rather than folding them into whichever arm presence
    # happens to classify them as.
    included = sessions
    if start_date is not None:
        excluded_ids = [sid for sid, data in sessions.items()
                         if datetime.date.fromtimestamp(data["mtime"]) < start_date]
        if excluded_ids:
            included = {sid: data for sid, data in sessions.items()
                        if sid not in excluded_ids}
        print("excluded: %d session(s) before %s"
              % (len(excluded_ids), start_date.isoformat()), file=out)

        scheduled = {"on": 0, "off": 0}
        for sid, data in included.items():
            scheduled[parity_schedule(data["mtime"], start_date)] += 1
        print("scheduled arm by parity (design intent, not used for the "
              "comparison): %d on-day session(s), %d off-day session(s)"
              % (scheduled["on"], scheduled["off"]), file=out)

    # C1 fix: the arm is decided by the mechanism (a lesson actually
    # shown), corroborated across both real logs, never by the calendar.
    merged_shown = merge_shown_sessions(shown_map, outcome_rows)

    repeats_by_session = compute_repeats(included)
    corpus_zero_collisions = (sum(repeats_by_session.values()) == 0)

    arms = {"on": [], "off": []}
    for session_id in included:
        arms[classify_session(session_id, merged_shown)].append(session_id)

    primary_line, primary_stats = read_e53_5_signal(evidence_store, repeat_lessons)
    print(primary_line, file=out)

    print("secondary repeat signal (same-sig cross-session collision detector):",
          file=out)
    stats = {}
    for key, label in (("on", "recall on"), ("off", "recall off")):
        line, s = arm_report(label, arms[key], included, merged_shown,
                              repeats_by_session, min_sessions, corpus_zero_collisions)
        print(line, file=out)
        stats[key] = s

    if stats["on"] and stats["off"]:
        if corpus_zero_collisions:
            print("comparison: NO-DATA: repeat signal never collided across "
                  "sessions: the fingerprint cannot be told from a detector "
                  "that cannot fire (secondary signal, same-sig cross-session "
                  "collision detector)", file=out)
        else:
            on_rate, off_rate = stats["on"]["rate"], stats["off"]["rate"]
            print("comparison: recall on repeats at %.2f per hundred attempts, "
                  "recall off at %.2f, difference %.2f (secondary signal, "
                  "same-sig cross-session collision detector)"
                  % (on_rate, off_rate, on_rate - off_rate), file=out)
        return 0
    print("NO-DATA: the comparison needs both arms", file=out)
    return 2


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--start", default=None,
                     help="YYYY-MM-DD the row E53 day-parity design begins; "
                          "sessions before this date are excluded from both arms")
    ap.add_argument("--recall-log", default=DEFAULT_RECALL_LOG)
    ap.add_argument("--ledger", default=DEFAULT_LEDGER)
    ap.add_argument("--guard-log", dest="guard_log", default=DEFAULT_GUARD_DIR)
    ap.add_argument("--min-sessions", dest="min_sessions", type=int,
                     default=DEFAULT_MIN_SESSIONS)
    ap.add_argument("--outcomes", default=DEFAULT_OUTCOMES,
                     help="the shared hook-outcome log both hooks append to "
                          "(lessons shown, recall cost, refusals)")
    ap.add_argument("--evidence-store", dest="evidence_store",
                     default=DEFAULT_EVIDENCE_STORE,
                     help="run_evidence capture store for the E53.5 replay "
                          "primary signal")
    ap.add_argument("--repeat-lessons", dest="repeat_lessons",
                     default=DEFAULT_REPEAT_LESSONS,
                     help="repeat_guard's own lessons.jsonl for the E53.5 "
                          "replay primary signal")
    args = ap.parse_args(list(sys.argv[1:] if argv is None else argv))
    return run(guard_dir=args.guard_log, recall_log=args.recall_log,
               ledger=args.ledger, min_sessions=args.min_sessions, start=args.start,
               outcomes=args.outcomes, evidence_store=args.evidence_store,
               repeat_lessons=args.repeat_lessons)


if __name__ == "__main__":
    sys.exit(main())

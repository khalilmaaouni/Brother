#!/usr/bin/env python3
"""lesson_repeat_trial: was the lesson SHOWN before the failure it describes.

Row E53 (docs/plan/READINESS-ROADMAP-2026-08-29.json) says the estate's recall
counter measures lessons SHOWN and nothing measures repeats PREVENTED. Its own
done_check is an OBSERVATIONAL two-week run (scripts/repeat_control.py, two arms
assigned by day parity). That run cannot start early and cannot be re-run.

This is the other half: a REPLAY trial that needs no live model and no waiting.
It takes a log of commands that already happened, with the outcome that followed
each one, and replays every one of them through the real matcher with the lesson
store AS IT WAS AT THAT MOMENT. For every command that later FAILED in a way a
recorded lesson describes, it asks one question: at that moment, would the
matcher have fired (SHOWN), or was the lesson not yet written (SILENT)?

THE TWO ARMS.
  arm A  the real lesson store, each lesson only counted for commands that ran
         on or after its own `recorded` date.
  arm B  the same replay with the store emptied. Memory off. It is 0 of n by
         construction, and that is the point: it is the floor the A arm is read
         against, and a run where A also reads 0 says the recall mechanism buys
         nothing on this log.

WHY THE recorded_at ORDERING IS THE WHOLE TRIAL. Without it, every lesson in
today's store matches every failure in the whole history and the A arm reads
n of n by arithmetic, which measures nothing. A lesson written AFTER a command
could not have been shown to it, so it does not count. That single rule is what
separates a trial from a tautology, and scripts/test_lesson_repeat_trial.py
drives it directly.

THE MATCHER IS NOT REIMPLEMENTED HERE. tools/repeat-guard/repeat_guard.py's own
signature() produces the exact text its PreToolUse branch matches lesson
triggers against (volatile substrings masked, lower-cased, first 200 chars), and
matching_lessons()'s rule is the plain lower-cased substring test. Both are
imported and applied here, so a change to the real hook changes this trial too
rather than leaving a second copy of the rule to drift.

THE LOG. `log` builds one from the estate's own evidence store, the durable
captures ~/Brother/scripts/run_evidence.py writes: one file per real command,
first line `$ <command>`, second line `[exit N after Ns]`, and the start time in
the filename. That is a real ledger of real commands with the outcome that
followed, which is exactly the shape the trial needs. A log file may equally be
written by hand or by another producer: one JSON object per line with
`command`, `exit_code` and `at` (an ISO date).

NO-DATA IS NEVER A PASS. An absent store, a log with no parseable capture, and a
log whose failures no lesson describes each print their own NO-DATA line naming
what was looked for, and exit 2. A trial that quietly reported "0 of 0" would
read like a clean negative result, so it is refused instead.

Python 3.9, standard library only. Read-only against the evidence store and the
lesson store; the only file it writes is the JSON record it is asked for.
"""
import argparse
import datetime
import glob
import json
import os
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, os.path.join(ROOT, "tools", "repeat-guard"))
import repeat_guard  # noqa: E402

#: run_evidence.py's own header, written by its capture(): line one is the
#: command, line two the exit code. Anything else is not one of its captures.
CAPTURE_EXIT = re.compile(r"^\[exit (-?\d+) after [\d.]+s\]")

DEFAULT_STORE = os.path.expanduser("~/.claude/evidence")
DEFAULT_RESULTS_DIR = os.path.join(ROOT, "benchmarks", "results")


# ---------------------------------------------------------------- the log


class _CaptureUnreadable(Exception):
    """read_capture's path exists but could not be opened or timestamped.

    Distinct from the ordinary None return (a file that is simply not a
    capture: wrong first line, no exit marker), so build_log can count and
    report a real read failure instead of folding it into the same silent
    skip a non-capture file gets."""


def read_capture(path):
    """One run_evidence capture as a log row, or None when it is not one.

    Raises _CaptureUnreadable when the file could not be opened or stat'd at
    all, so the caller can count and report that instead of losing it inside
    an ordinary "not a capture" skip."""
    try:
        with open(path, encoding="utf-8", errors="replace") as fh:
            first = fh.readline().rstrip("\n")
            second = fh.readline().rstrip("\n")
    except OSError as exc:
        raise _CaptureUnreadable("%s (%s)" % (path, exc)) from exc
    m = CAPTURE_EXIT.match(second)
    if not first.startswith("$ ") or not m:
        return None
    name = os.path.basename(path)
    stamp = name.split("-", 1)[0]
    try:
        at = datetime.date.fromtimestamp(int(stamp))
    except (ValueError, OSError, OverflowError):
        try:
            at = datetime.date.fromtimestamp(os.path.getmtime(path))
        except OSError as exc:
            raise _CaptureUnreadable("%s (%s)" % (path, exc)) from exc
    return {"command": first[2:], "exit_code": int(m.group(1)),
            "at": at.isoformat(), "source": name}


def build_log(store):
    """(rows, unreadable): every parseable capture, oldest first, and the
    paths that existed but could not be read. None when the store is absent."""
    if not os.path.isdir(store):
        return None
    rows, unreadable = [], []
    for path in sorted(glob.glob(os.path.join(store, "*.txt"))):
        try:
            row = read_capture(path)
        except _CaptureUnreadable as exc:
            unreadable.append(str(exc))
            continue
        if row is not None:
            rows.append(row)
    rows.sort(key=lambda r: (r["at"], r["source"]))
    return rows, unreadable


def read_log(path):
    rows = []
    torn = 0
    with open(path, encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            try:
                rec = json.loads(line)
            except ValueError:
                torn += 1
                continue
            if isinstance(rec, dict) and rec.get("command"):
                rows.append(rec)
    if torn:
        # A torn line is not a reason to abandon the log, but dropping it
        # without a word is the exact silent-swallow class this trial exists
        # to measure; report it instead of hiding it the same way.
        print("lesson_repeat_trial: dropped %d torn line(s) in %s (not "
              "parseable as JSON)" % (torn, path), file=sys.stderr)
    return rows


# ------------------------------------------------------------ the lessons


def read_lessons(path):
    """The store as a list, or None when the file is absent.

    `id` is minted from the file order because the store itself carries none;
    it is stable for a given file and is what the per-failure lines quote.
    """
    if not os.path.exists(path):
        return None
    out = []
    torn = 0
    with open(path, encoding="utf-8") as fh:
        for line in fh:
            try:
                rec = json.loads(line)
            except ValueError:
                torn += 1
                continue
            trigger = str(rec.get("trigger", "")).lower().strip()
            if not trigger:
                continue
            recorded = rec.get("recorded")
            try:
                at = datetime.date.fromisoformat(str(recorded)) if recorded else None
            except ValueError:
                at = None
            out.append({"id": "L%02d" % (len(out) + 1), "trigger": trigger,
                        "recorded": recorded, "recorded_date": at})
    if torn:
        print("lesson_repeat_trial: dropped %d torn line(s) in %s (not "
              "parseable as JSON)" % (torn, path), file=sys.stderr)
    return out


def failed(row):
    """A non-zero exit is the failure. An absent code is not a failure."""
    code = row.get("exit_code")
    return isinstance(code, int) and not isinstance(code, bool) and code != 0


def replay(rows, lessons):
    """Per failure a lesson describes: the lesson that would have fired, or None.

    Returns [{command, at, exit_code, lesson, trigger, described_by}], lesson
    None meaning SILENT: a lesson in the store describes this failure, but every
    one of them was recorded after the command ran (or carries no date at all),
    so the matcher could not have shown it at the moment it mattered.
    """
    out = []
    for row in rows:
        if not failed(row):
            continue
        _sig, shown = repeat_guard.signature("Bash", {"command": str(row["command"])})
        described = [l for l in lessons if l["trigger"] in shown]
        if not described:
            continue
        try:
            at = datetime.date.fromisoformat(str(row.get("at")))
        except (ValueError, TypeError):
            at = None
        fired = None
        if at is not None:
            for l in described:
                if l["recorded_date"] is not None and l["recorded_date"] <= at:
                    fired = l
                    break
        out.append({"command": str(row["command"]), "at": row.get("at"),
                    "exit_code": row.get("exit_code"),
                    "lesson": fired["id"] if fired else None,
                    "trigger": fired["trigger"] if fired else None,
                    "described_by": [l["id"] for l in described]})
    return out


# --------------------------------------------------------------- the verbs


def cmd_log(args):
    result = build_log(args.store)
    if result is None:
        print("NO-DATA: no evidence store at %s, so there is no real command log "
              "to replay" % args.store)
        return 2
    rows, unreadable = result
    if not rows:
        print("NO-DATA: %s holds no run_evidence capture (a capture is a file "
              "whose first line is $ <command> and whose second is [exit N ...])"
              % args.store)
        return 2
    with open(args.out, "w", encoding="utf-8") as fh:
        for row in rows:
            fh.write(json.dumps(row, ensure_ascii=False) + "\n")
    fails = sum(1 for r in rows if failed(r))
    print("log: %d command(s), %d failure(s), %s to %s"
          % (len(rows), fails, rows[0]["at"], rows[-1]["at"]))
    if unreadable:
        # A file that exists but could not be opened or stat'd is dropped
        # from the log; that drop is now named instead of silent.
        shown = "; ".join(unreadable[:5])
        more = "" if len(unreadable) <= 5 else "; and %d more" % (len(unreadable) - 5)
        print("dropped %d capture(s) that could not be read: %s%s"
              % (len(unreadable), shown, more))
    print("wrote %s" % args.out)
    return 0


def cmd_trial(args):
    lessons = read_lessons(args.lessons)
    if lessons is None:
        print("NO-DATA: no lesson store at %s, so neither arm can be replayed"
              % args.lessons)
        return 2
    rows = read_log(args.log)
    if not rows:
        print("NO-DATA: %s holds no command row (one JSON object per line with "
              "command, exit_code and at)" % args.log)
        return 2

    arm_a = replay(rows, lessons)
    arm_b = replay(rows, [])  # memory off: the floor, 0 of n by construction
    n = len(arm_a)
    if n == 0:
        print("NO-DATA: %d command(s) replayed, %d failure(s), none of them "
              "matched by any of the %d recorded lesson(s); there is nothing to "
              "compare" % (len(rows), sum(1 for r in rows if failed(r)), len(lessons)))
        return 2

    shown_a = sum(1 for r in arm_a if r["lesson"])
    shown_b = sum(1 for r in arm_b if r["lesson"])
    undated = sum(1 for l in lessons if l["recorded_date"] is None)

    print("replayed %d command(s) from %s against %d lesson(s) from %s"
          % (len(rows), args.log, len(lessons), args.lessons))
    print("shown before the repeat: %d of %d failures (A) versus %d of %d (B)"
          % (shown_a, n, shown_b, n))
    for r in arm_a:
        mark = ("SHOWN %s %r" % (r["lesson"], r["trigger"])) if r["lesson"] else "SILENT"
        print("  %s exit %s :: %s :: %s"
              % (r["at"], r["exit_code"], r["command"][:90], mark))
    if undated:
        print("note: %d of %d lesson(s) carry no recorded date and can never "
              "count as shown; they are read as SILENT rather than assumed older"
              % (undated, len(lessons)))

    record = {
        "generated": datetime.date.today().isoformat(),
        "log": os.path.abspath(args.log),
        "lessons": os.path.abspath(args.lessons),
        "commands": len(rows),
        "failures": sum(1 for r in rows if failed(r)),
        "described_failures": n,
        "arm_a_shown": shown_a,
        "arm_b_shown": shown_b,
        "undated_lessons": undated,
        "detail": arm_a,
    }
    out = args.out or os.path.join(
        DEFAULT_RESULTS_DIR, "lesson-repeat-%s.json" % record["generated"])
    d = os.path.dirname(os.path.abspath(out))
    if d:
        os.makedirs(d, exist_ok=True)
    with open(out, "w", encoding="utf-8") as fh:
        json.dump(record, fh, indent=2, ensure_ascii=False)
        fh.write("\n")
    print("wrote %s" % out)
    return 0


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    sub = ap.add_subparsers(dest="cmd", required=True)
    lg = sub.add_parser("log", help="build a command log from the run_evidence store")
    lg.add_argument("--store", default=DEFAULT_STORE)
    lg.add_argument("--out", required=True)
    lg.set_defaults(fn=cmd_log)
    tr = sub.add_parser("trial", help="replay a log through the matcher, both arms")
    tr.add_argument("--log", required=True)
    tr.add_argument("--lessons", default=str(repeat_guard.LESSONS))
    tr.add_argument("--out", default=None)
    tr.set_defaults(fn=cmd_trial)
    args = ap.parse_args(list(sys.argv[1:] if argv is None else argv))
    return args.fn(args)


if __name__ == "__main__":
    sys.exit(main())

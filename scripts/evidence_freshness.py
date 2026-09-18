#!/usr/bin/env python3
"""DOM-10.04: is this evidence still trustworthy right now, by TIME.

THE DECIDING PROPERTY. Evidence becomes STALE when its source's last edit,
or a caller-stated maximum age, has passed it by, and a stale verdict is
never read as current. This is the TIME axis of evidence freshness, and it
is deliberately kept separate from the CONTENT axis: scripts/verdict_
snapshot.py (ORCH-21) already hashes file content to decide whether a
verdict still describes the tree it was earned on. That module never
touches a clock, and this one never touches file content. A caller that
needs both answers runs both checks; this module does not re-implement
the other one, and does not attempt to replace it.

FOUR OUTCOMES, and only four, because a two-way PASS/FAIL answer would
hide which failure it was:
  FRESH     no staleness rule fired. The evidence may be read as current.
  STALE     the evidence predates the thing it proves, or has simply aged
            past a stated ceiling. Never a pass, and never treated as one.
  NO-DATA   the evidence carries no readable timestamp at all. This is not
            the same as FRESH by default and not the same as STALE either:
            it is the absence of the one fact this module exists to check,
            named as such rather than guessed at.
  REFUSED   a timestamp (the evidence's own, or its source's) is ahead of
            the clock reading passed in as `now`. A future timestamp is
            either a clock that has drifted or a record that lies, and
            this module has no second, independent clock to tell those
            apart, so it refuses rather than silently trusting either
            timestamp or silently clamping the skew away.

NEVER FILE MTIME. A source's "last edited" time comes from git history
(git_last_edit_time), never from os.path.getmtime. A freshly checked out
working copy stamps every tracked file with the checkout time regardless
of when it was really last touched, so mtime would report every file in a
new worktree as "just edited" and make every stale-evidence case invisible
on the one kind of tree (a fresh clone or worktree) where catching it
matters most.

Python 3.9 floor, standard library only, no network.
"""

import argparse
import json
import math
import subprocess
import sys
import time
from datetime import datetime, timezone


_STATUS_EXIT_CODES = {
    "FRESH": 0,
    "STALE": 1,
    "NO-DATA": 2,
    "REFUSED": 3,
}

_GIT_LOG_TIMEOUT_SECONDS = 10


class _NoDataError(Exception):
    """Raised internally when the CLI cannot obtain a needed timestamp.
    Never escapes main(): _run_check turns it into a NO-DATA exit."""


def exit_code_for(status):
    """The process exit code for a classify() status. Raises ValueError on
    a status this module did not produce itself, rather than defaulting to
    some exit code: a caller passing a typo'd or foreign status string must
    see that immediately, not get back exit 0 or 1 by accident.
    """
    try:
        return _STATUS_EXIT_CODES[status]
    except KeyError:
        raise ValueError("unknown status: %r" % (status,))


def _parse_iso_datetime(text):
    candidate = text
    if candidate.endswith("Z") or candidate.endswith("z"):
        candidate = candidate[:-1] + "+00:00"
    try:
        parsed = datetime.fromisoformat(candidate)
    except ValueError as exc:
        raise ValueError("not a valid ISO 8601 timestamp: %r" % (text,)) from exc
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.timestamp()


def parse_timestamp(value):
    """Parse a timestamp into epoch seconds.

    Accepts an int or float epoch value, a numeric string, or an ISO 8601
    datetime string (a naive one is read as UTC). Anything else, including
    None and bool (a bool is an int in Python and would otherwise silently
    parse as 0.0 or 1.0), raises ValueError. Never returns a default such
    as 0 or the current time: a timestamp this module could not read is a
    NO-DATA case for the caller to report, not a guess for this function
    to make.
    """
    if value is None:
        raise ValueError("timestamp is None")
    if isinstance(value, bool):
        raise ValueError("timestamp must not be a bool: %r" % (value,))
    if isinstance(value, (int, float)):
        result = float(value)
        if not math.isfinite(result):
            raise ValueError("timestamp is not finite: %r" % (value,))
        return result
    if isinstance(value, str):
        text = value.strip()
        if not text:
            raise ValueError("timestamp string is empty")
        try:
            numeric = float(text)
        except ValueError:
            numeric = None
        if numeric is not None:
            if not math.isfinite(numeric):
                raise ValueError("timestamp is not finite: %r" % (value,))
            return numeric
        return _parse_iso_datetime(text)
    raise ValueError("unsupported timestamp type: %r" % (type(value).__name__,))


def git_last_edit_time(path, cwd=None):
    """Epoch seconds of the last commit that touched path, or None.

    Runs `git log -1 --format=%ct -- <path>` with a 10 second timeout,
    rooted at cwd (or the current directory when cwd is None). Returns
    None, never raises, when: git is not on PATH, the subprocess fails or
    times out, or the path has no commit history (untracked, or never
    committed). See the module docstring for why this never falls back to
    os.path.getmtime.
    """
    try:
        completed = subprocess.run(
            ["git", "log", "-1", "--format=%ct", "--", str(path)],
            cwd=cwd,
            capture_output=True,
            text=True,
            timeout=_GIT_LOG_TIMEOUT_SECONDS,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    if completed.returncode != 0:
        return None
    raw = completed.stdout.strip()
    if not raw:
        return None
    try:
        return float(raw)
    except ValueError:
        return None


def classify(evidence_at, source_at, now, max_age_s=None):
    """(status, reason). status is one of FRESH, STALE, NO-DATA, REFUSED.

    Pure function, no I/O and no clock read of its own: `now` must be
    supplied by the caller, so a test can inject a fixed value and a real
    caller can pass a genuine clock reading it actually took. Rules run in
    this order, and the first that matches wins:

      evidence_at is None                         -> NO-DATA
      evidence_at is ahead of now                  -> REFUSED (clock skew)
      source_at is not None and ahead of now       -> REFUSED (clock skew)
      source_at is None and max_age_s is None      -> NO-DATA (nothing to judge against)
      source_at is not None and evidence_at older  -> STALE (predates source)
      max_age_s is not None and age exceeds it     -> STALE (too old)
      otherwise                                    -> FRESH

    evidence_at exactly equal to source_at is FRESH, not STALE: only
    strictly older than the source counts as predating it.

    Raises ValueError, never a default answer, when now is None (a caller
    must supply a real clock reading, never let this default to "now"
    inside a function that exists to be deterministic under test) or when
    max_age_s is negative (a negative ceiling is a caller defect, not a
    policy this module can honor).
    """
    if now is None:
        raise ValueError("now must not be None")
    if max_age_s is not None and max_age_s < 0:
        raise ValueError("max_age_s must not be negative")

    if evidence_at is None:
        return ("NO-DATA", "evidence has no readable timestamp")


    if evidence_at > now:
        return (
            "REFUSED",
            "evidence timestamp is ahead of now (clock skew or a future "
            "timestamp): evidence_at=%r now=%r" % (evidence_at, now),
        )

    if source_at is not None and source_at > now:
        return (
            "REFUSED",
            "source last edit is ahead of now (clock skew or a future "
            "timestamp): source_at=%r now=%r" % (source_at, now),
        )

    if source_at is None and max_age_s is None:
        # Orchestrator fix: with no source edit time and no maximum age
        # there is nothing to compare against, so FRESH would be a verdict
        # about nothing. That is NO-DATA, never a pass.
        return ("NO-DATA", "no source edit time and no maximum age: "
                "nothing to judge freshness against")

    if source_at is not None and evidence_at < source_at:
        return (
            "STALE",
            "evidence predates the source's last edit "
            "(evidence_at=%r source_at=%r)" % (evidence_at, source_at),
        )

    if max_age_s is not None and (now - evidence_at) > max_age_s:
        return (
            "STALE",
            "evidence age %.3f s exceeds max_age_s %.3f s"
            % (now - evidence_at, max_age_s),
        )

    return ("FRESH", "evidence is current")


def _load_evidence_from_record(path, field):
    try:
        with open(path, "r", encoding="utf-8") as handle:
            data = json.load(handle)
    except OSError as exc:
        raise _NoDataError("cannot read evidence record %r: %s" % (path, exc))
    except json.JSONDecodeError as exc:
        raise _NoDataError("evidence record %r is not valid JSON: %s" % (path, exc))
    if not isinstance(data, dict):
        raise _NoDataError("evidence record %r is not a JSON object" % (path,))
    if field not in data:
        raise _NoDataError("field %r is missing from evidence record %r" % (field, path))
    try:
        return parse_timestamp(data[field])
    except ValueError as exc:
        raise _NoDataError(
            "field %r in evidence record %r is not a usable timestamp: %s"
            % (field, path, exc)
        )


def _build_parser():
    parser = argparse.ArgumentParser(
        prog="evidence_freshness.py",
        description="Decide whether recorded evidence is still fresh, by time.",
    )
    subparsers = parser.add_subparsers(dest="command", required=True)
    check = subparsers.add_parser(
        "check",
        help="classify an evidence timestamp against source edit time and age",
    )
    check.add_argument(
        "--evidence-at",
        default=None,
        help="evidence timestamp as epoch seconds or ISO 8601",
    )
    check.add_argument(
        "--evidence-record",
        default=None,
        help="path to a JSON file holding the evidence timestamp",
    )
    check.add_argument(
        "--evidence-field",
        default="at",
        help="field to read from --evidence-record (default: at)",
    )
    check.add_argument(
        "--source",
        default=None,
        help="path whose git last edit time is compared to the evidence",
    )
    check.add_argument(
        "--max-age-seconds",
        type=float,
        default=None,
        help="maximum allowed evidence age in seconds",
    )
    check.add_argument(
        "--now",
        default=None,
        help="current clock reading as epoch seconds or ISO 8601 "
        "(default: real current time)",
    )
    return parser


def _emit(status, reason, also_stderr):
    line = "evidence_freshness check: %s (%s)" % (status, reason)
    if also_stderr:
        print(line, file=sys.stderr)
    print(line)
    return exit_code_for(status)


def _run_check(args):
    if args.now is None:
        now = time.time()
    else:
        try:
            now = parse_timestamp(args.now)
        except ValueError as exc:
            return _emit("REFUSED", "unusable --now value: %s" % (exc,), also_stderr=True)

    if args.max_age_seconds is not None and args.max_age_seconds < 0:
        return _emit(
            "REFUSED", "--max-age-seconds must not be negative", also_stderr=True
        )

    evidence_at = None
    if args.evidence_record is not None:
        try:
            evidence_at = _load_evidence_from_record(args.evidence_record, args.evidence_field)
        except _NoDataError as exc:
            return _emit("NO-DATA", str(exc), also_stderr=True)
    elif args.evidence_at is not None:
        try:
            evidence_at = parse_timestamp(args.evidence_at)
        except ValueError as exc:
            return _emit(
                "NO-DATA", "unusable --evidence-at value: %s" % (exc,), also_stderr=True
            )

    source_at = None
    if args.source is not None:
        source_at = git_last_edit_time(args.source)
        if source_at is None:
            return _emit(
                "NO-DATA",
                "no git history available for source %r" % (args.source,),
                also_stderr=True,
            )

    try:
        status, reason = classify(evidence_at, source_at, now, max_age_s=args.max_age_seconds)
    except ValueError as exc:
        return _emit("REFUSED", "classification refused: %s" % (exc,), also_stderr=True)
    return _emit(status, reason, also_stderr=False)


def main(argv=None):
    parser = _build_parser()
    args = parser.parse_args(argv)
    return _run_check(args)


if __name__ == "__main__":
    sys.exit(main())

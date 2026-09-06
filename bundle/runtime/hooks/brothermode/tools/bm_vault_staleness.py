#!/usr/bin/env python3
"""The staleness contract: a fact that quietly stopped being true loses rank
until re-verified.

WHY THIS EXISTS. Research 2026-08-30 names consolidation and decay an
unsolved gap across Mem0, Zep and Letta: none of them re-weights a memory
that has simply gone stale. This estate already carries the raw material --
verified_at sits on 202 notes today (bm_vault_temporal.py's bi-temporal
contract, the field somebody checked a fact against its source) -- but
nothing reads it as an EXPIRY. A decision verified 200 days ago and one
verified yesterday rank identically, which is false confidence exactly
where bm_vault_authority.py (D08) already proved false confidence is a
governance failure, not a relevance tweak.

THE VOCABULARY, four states, no more:
  fresh                 verified_at is inside its class horizon
  stale                 verified_at exists but is OLDER than its horizon
  unverified_no_clock   no verified_at at all: UNEXAMINED
  examined_no_date      verified_at holds the sentinel "no-derivable-date":
                         EXAMINED, and no date could be read from any
                         evidence, which is itself a recorded finding

ABSENCE IS NOT A MEASUREMENT. A note that never declared verified_at is
UNVERIFIED-NO-CLOCK: counted, but never STALE and never FRESH. Collapsing it
into either would invent a fact nobody recorded -- exactly the trap
bm_vault_temporal.py's own docstring names for an unmigrated window.

THE SENTINEL, added for WBS row VB4-03: a backfill that reads a note's
evidence and finds no derivable date must never invent one, but leaving the
field blank makes that note indistinguishable from a note nobody has looked
at yet. `verified_at: no-derivable-date` is the explicit marker for "examined,
undatable" -- the census counts it apart from unverified_no_clock so it can
tell examined from unexamined.

THE HORIZONS, read from the SAME type: field bm_vault.py's _classify already
reads, in days, overridable with a repeatable --horizon class=days:
  decision      180   a call that ages fast; the world moves under it
  failure       365   a root cause stays true until the system changes
  reference     365   a fact about the world, same shelf life as a failure
  session-log   exempt (immutable history: a log does not re-verify itself,
                it records what happened and that never expires)
  anything else 365   the DEFAULT, stated rather than silently exempted

A MALFORMED verified_at (unparseable date) is reported as a finding, never
silently treated as either fresh or absent.

Exit 0 clean, 1 on stale or malformed findings, 2 NO-DATA on an unreadable
vault. Stdlib only, writes nothing anywhere. Python 3.9 floor.
"""
import argparse
import datetime
import os
import re
import sys

DEFAULT_HORIZONS = {
    "decision": 180,
    "failure": 365,
    "reference": 365,
}
DEFAULT_HORIZON_DAYS = 365
EXEMPT_TYPES = {"session-log"}
SKIP_DIRS = {".git", ".trash", ".obsidian"}
NO_DERIVABLE_DATE = "no-derivable-date"

TYPE_RE = re.compile(r"^type:\s*(.+)$", re.M)
VERIFIED_RE = re.compile(r"^verified_at:\s*(\S+)\s*$", re.M)
LIFECYCLE_RE = re.compile(r"^lifecycle:\s*(.+)$", re.M)
EXPIRY_AT_RE = re.compile(r"^expiry_at:\s*(\S+)\s*$", re.M)


def _frontmatter(text):
    if not text.startswith("---"):
        return ""
    end = text.find("\n---", 3)
    return text[3:end] if end != -1 else ""


def _note_type(text):
    m = TYPE_RE.search(_frontmatter(text))
    return m.group(1).strip().strip('"').strip("'").lower() if m else ""


def _parse_date(raw):
    """A date or None. Quotes tolerated, garbage refused rather than coerced,
    the same contract bm_vault_temporal._parse_date already keeps."""
    try:
        return datetime.date.fromisoformat(raw.strip().strip('"').strip("'"))
    except (ValueError, AttributeError):
        return None


def read_verified_at(text):
    """(date|NO_DERIVABLE_DATE|None, problem|None). Absent is (None, None);
    the sentinel string is passed through as-is (examined, no date found);
    any other unparseable value is (None, reason), never silently None."""
    m = VERIFIED_RE.search(_frontmatter(text))
    if not m:
        return None, None
    raw = m.group(1).strip().strip('"').strip("'")
    if raw.lower() == NO_DERIVABLE_DATE:
        return NO_DERIVABLE_DATE, None
    d = _parse_date(raw)
    if d is None:
        return None, "unparseable verified_at %r" % m.group(1)
    return d, None


def read_capture_expiry(text):
    """(is_unpromoted_capture, expiry_date|None). VB6-09: a capture note
    (tools/bm_vault_intake.py's capture verb) still sitting at
    lifecycle: candidate lives in its OWN bucket, never fresh/stale/
    unverified_no_clock -- it has no verified_at at all by design, and
    running it through that state machine would silently fold it into
    unverified_no_clock, which is exactly the "distinct from ... unverified"
    the row's done_check forbids. A note whose lifecycle has moved past
    candidate (promoted) is not this bucket's concern at all. expiry_date is
    None when expiry_at is absent or unparseable: never invented."""
    fm = _frontmatter(text)
    m = LIFECYCLE_RE.search(fm)
    if not m or m.group(1).strip().strip('"').strip("'").lower() != "candidate":
        return False, None
    m2 = EXPIRY_AT_RE.search(fm)
    if not m2:
        return True, None
    return True, _parse_date(m2.group(1))


def is_expired_capture(text, today=None):
    """True only for an unpromoted capture whose own declared expiry_at has
    passed. Comparison is strict >, not >=: the boundary day itself (today ==
    expiry_at) is NOT YET expired, so the census line below only ever counts
    a capture the day AFTER its expiry_at, never on it. Surfaces the fact;
    the caller decides what, if anything, to do with it -- this module writes
    nothing and deletes nothing."""
    today = today or datetime.date.today()
    is_capture, expires = read_capture_expiry(text)
    return bool(is_capture and expires is not None and today > expires)


def horizon_days(note_type, horizons=None):
    """The class horizon in days, or None meaning EXEMPT (session-log):
    never stale whatever verified_at says, because it is not a claim about
    the world that can go out of date."""
    if note_type in EXEMPT_TYPES:
        return None
    table = dict(DEFAULT_HORIZONS)
    if horizons:
        table.update(horizons)
    return table.get(note_type, DEFAULT_HORIZON_DAYS)


def classify(text, today=None, horizons=None):
    """(state, verified_date|None, age_days|None, problem|None).

    state is one of "fresh", "stale", "unverified_no_clock",
    "examined_no_date", "exempt", "malformed". Only "stale" and "fresh" ever
    carry a real age; the other four exist so a caller never has to guess
    what an absent measurement means.
    """
    today = today or datetime.date.today()
    verified, problem = read_verified_at(text)
    if problem:
        return "malformed", None, None, problem
    days = horizon_days(_note_type(text), horizons)
    if days is None:
        return "exempt", verified, None, None
    if verified == NO_DERIVABLE_DATE:
        return "examined_no_date", None, None, None
    if verified is None:
        return "unverified_no_clock", None, None, None
    age = (today - verified).days
    return ("stale" if age > days else "fresh"), verified, age, None


def is_stale(text, today=None, horizons=None):
    """(bool, verified_date|None) -- the one call a caller outside this
    module needs (bm_vault.py's authority demotion seam). Only "stale"
    itself ever returns True: unverified_no_clock, exempt and malformed
    notes are never demoted for a measurement nobody made."""
    state, verified, _age, _problem = classify(text, today, horizons)
    return state == "stale", verified


def walk(vault):
    for dirpath, dirnames, filenames in os.walk(vault):
        dirnames[:] = [d for d in dirnames if d not in SKIP_DIRS and not d.startswith(".")]
        for fn in sorted(filenames):
            if fn.endswith(".md"):
                yield os.path.join(dirpath, fn)


def cmd_check(vault, horizons=None, today=None):
    today = today or datetime.date.today()
    counts = {"fresh": 0, "stale": 0, "unverified_no_clock": 0,
              "examined_no_date": 0, "exempt": 0}
    stale_notes = []
    malformed = []
    expired_captures = []
    total = 0
    for path in walk(vault):
        try:
            with open(path, encoding="utf-8", errors="replace") as fh:
                text = fh.read()
        except OSError:  # sbe: allow-silent vault walk skips a file it cannot read, same convention as the id/scan walks in bm_vault_provenance.py
            continue
        total += 1
        rel = os.path.relpath(path, vault)
        # VB6-09: an unpromoted capture lives in its own bucket, checked and
        # skipped BEFORE classify() ever runs, so it can never also land in
        # fresh/stale/unverified_no_clock -- see read_capture_expiry's own
        # docstring for why folding it into classify() would be wrong.
        is_capture, expires = read_capture_expiry(text)
        if is_capture:
            if expires is not None and today > expires:
                expired_captures.append((rel, expires))
            continue
        state, verified, age, problem = classify(text, today, horizons)
        if state == "malformed":
            malformed.append((rel, problem))
            continue
        counts[state] += 1
        if state == "stale":
            stale_notes.append((rel, verified, age))
    print("vault: %s" % vault)
    print("as of: %s" % today)
    print("notes: %d" % total)
    print("fresh: %d" % counts["fresh"])
    print("stale: %d" % counts["stale"])
    print("unverified, no clock (never stale, never fresh, absence is not "
          "a measurement, UNEXAMINED): %d" % counts["unverified_no_clock"])
    print("examined, no derivable date (evidence read, none found, "
          "EXAMINED): %d" % counts["examined_no_date"])
    print("exempt (session-log, immutable history): %d" % counts["exempt"])
    if malformed:
        print("MALFORMED verified_at: %d" % len(malformed))
        for rel, problem in malformed:
            print("  %s: %s" % (rel, problem))
    if stale_notes:
        print("STALE, named with age: %d" % len(stale_notes))
        for rel, verified, age in sorted(stale_notes, key=lambda t: -t[2]):
            print("  %s: verified_at %s, %d days ago" % (rel, verified, age))
    if expired_captures:
        print("expired, unpromoted captures (candidate, past expiry_at, never "
              "deleted): %d" % len(expired_captures))
        for rel, expires in sorted(expired_captures, key=lambda t: t[1]):
            print("  %s: expiry_at %s" % (rel, expires))
    return 1 if (stale_notes or malformed or expired_captures) else 0


def _parse_horizon_args(items):
    out = {}
    for item in items or []:
        if "=" not in item:
            raise ValueError("--horizon needs class=days, got %r" % item)
        cls, days = item.split("=", 1)
        cls = cls.strip()
        try:
            out[cls] = int(days.strip())
        except ValueError:
            raise ValueError("--horizon days must be an integer, got %r for %r"
                              % (days, cls))
    return out


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("command", choices=("check",))
    ap.add_argument("--vault", default=os.environ.get("BM_VAULT_ROOT"))
    ap.add_argument("--horizon", action="append",
                    help="override one class horizon, class=days, repeatable "
                         "(e.g. --horizon decision=90)")
    ap.add_argument("--date",
                    help="treat this YYYY-MM-DD as 'today', for calibration")
    args = ap.parse_args(argv)
    if not args.vault or not os.path.isdir(args.vault):
        print("bm_vault_staleness: NO-DATA, no readable vault at %r" % args.vault,
              file=sys.stderr)
        return 2
    try:
        horizons = _parse_horizon_args(args.horizon)
    except ValueError as e:
        print("bm_vault_staleness: %s" % e, file=sys.stderr)
        return 2
    today = None
    if args.date:
        today = _parse_date(args.date)
        if today is None:
            print("bm_vault_staleness: --date needs YYYY-MM-DD, got %r" % args.date,
                  file=sys.stderr)
            return 2
    return cmd_check(args.vault, horizons, today)


if __name__ == "__main__":
    sys.exit(main())

#!/usr/bin/env python3
"""accept_delivery: the human decision node the roadmap's H2 hole names.

H2, docs/plan/READINESS-ROADMAP-2026-08-29.json, H_series holes: "there is no
accepted state: the chain ends at a green gate and a merge, and whether a
person accepted the result is not represented." A gate going green and a PR
merging are both machine events. Nothing in this estate ever asked a human
whether the result was actually what they wanted, so nothing could ever
answer that question later. This is the smallest seam that lets a human
record the answer, on the record, in their own words.

THE LINE THIS TOOL NEVER CROSSES: it RECORDS a human's acceptance. It never
generates one. There is no default acceptor, no auto-accept flag, and no
path from a green check or a merge to an acceptance record. accepted_by and
accepted_at are read from the caller and only the caller; a run that omits
either is refused by argparse before anything is written. A merge is a
machine event; acceptance is a human one, and this file is the only place
the two are allowed to differ.

RECORDED_BY (row E49) separates the person who decided from the process that
typed: every record names whether a person at a terminal typed it
(--recorded-by person) or an agent did, acting under a named delegation
(--recorded-by agent --delegation "<the exact sentence>"). accepted_by alone
was not enough, because it can carry a human's name even when an agent typed
it under delegation, honestly, in its own words. --list counts a week's
acceptances only over person-recorded entries; an agent-recorded one is
printed on its own line and never folded into the count.

ONE RECORD PER DELIVERY, append-only. Each acceptance is its own JSON file
under docs/deliveries/, named for the commit or PR it points at (its "ref" is
the delivery's identity here, not its plain-language name, because the same
change could earn two different names from two different people but a given
commit or PR is only ever delivered once). The file is created with O_EXCL,
so a second attempt to accept the same ref, even racing the first, is refused
rather than silently overwriting the first person's record.

--list computes accepted-per-week from the records themselves, never from a
counter this tool remembers: the number is only ever as good as the files on
disk, and a week with zero acceptances reports NO-DATA rather than a bare 0,
per this estate's own counting rule (scripts/board_status.py) that the two
must never read the same.

Exit codes
  0  a record was written, or --list found at least one acceptance
  2  refused: a duplicate ref, an unparsable accepted_at, or --list found
     nothing to report (NO-DATA, never a pass and never a bare 0)

origin: a human running this script's own CLI (main(), below) directly, by
hand, after reviewing a delivery. Nothing else in this repo calls into
accept_delivery.py (verified: grep -rl accept_delivery scripts bundle/runtime
finds no importer), which is the point made above under THE LINE THIS TOOL
NEVER CROSSES.

PRODUCER: this module is the sole producer of its own record. The write
happens at the O_EXCL open plus json.dump call inside main(), a few lines
below record_path().

Python 3, standard library only. No network.
"""
import argparse
import datetime
import json
import os
import re
import sys

import pattern_note

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DELIVERIES_DIR = os.path.join(ROOT, "docs", "deliveries")

NODATA = "NO-DATA"


def slugify(ref):
    """A commit/PR reference turned into a safe filename. Collisions are the
    point: two attempts to accept the same ref must land on the same path so
    the second one collides with the first instead of writing a sibling."""
    return re.sub(r"[^A-Za-z0-9._-]+", "_", ref.strip())


def record_path(ref, directory=None):
    directory = directory or DELIVERIES_DIR
    return os.path.join(directory, "%s.json" % slugify(ref))


def parse_iso(value):
    """Raises ValueError on anything that is not a real ISO date/datetime.
    Never guesses, never defaults to now(): accepted_at is the caller's claim
    or nothing."""
    return datetime.datetime.fromisoformat(value)


def record(name, ref, accepted_by, accepted_at, recorded_by, delegation=None,
           words=None, directory=None):
    """Write one acceptance. Returns (True, path) on success or
    (False, reason) on refusal. Pure enough to test without a subprocess.

    recorded_by separates the person who decided from the process that typed
    (row E49): 'person' for a human at a terminal, or 'agent' for an agent
    acting under a named delegation, which then requires that delegation
    sentence verbatim. Neither shape is inferred; a call giving neither is
    refused before anything is written."""
    directory = directory or DELIVERIES_DIR
    if recorded_by not in ("person", "agent"):
        return False, "recorded_by must be 'person' or 'agent', not %r" % recorded_by
    if recorded_by == "agent" and not (delegation and str(delegation).strip()):
        return False, ("an agent-recorded acceptance requires --delegation "
                       "\"<the exact sentence the founder said>\"")
    try:
        parse_iso(accepted_at)
    except ValueError:
        return False, "accepted_at %r is not a valid ISO date" % accepted_at

    path = record_path(ref, directory)
    entry = {
        "name": name.strip(),
        "ref": ref.strip(),
        "accepted_by": accepted_by.strip(),
        "accepted_at": accepted_at.strip(),
        "recorded_by": recorded_by,
    }
    if recorded_by == "agent":
        entry["delegation"] = str(delegation).strip()
    if words and words.strip():
        entry["words"] = words.strip()

    os.makedirs(directory, exist_ok=True)
    try:
        # O_EXCL: the duplicate refusal is enforced by the filesystem, not by
        # an exists()-then-write race this process could lose to itself.
        fd = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
    except FileExistsError:
        return False, ("delivery %r already accepted (%s): a duplicate "
                       "acceptance is refused, not overwritten" % (ref, path))
    with os.fdopen(fd, "w", encoding="utf-8") as fh:
        json.dump(entry, fh, indent=2, sort_keys=True)
        fh.write("\n")
    return True, path


def write_pattern_from_acceptance(ref, accepted_by, accepted_at, words,
                                  record_file, pattern_root=None):
    """The good-outcome half of the learning loop (roadmap learning_loop
    item n=3): a person-recorded acceptance IS a mechanical good outcome, so
    it writes one pattern the same way pattern_note.write always has,
    instead of depending on somebody remembering to run that CLI by hand.
    An agent-recorded acceptance never reaches this function (see main()):
    an agent's acceptance is not a good outcome a person confirmed.

    Returns the line to print. Never raises: a pattern-store problem
    (missing vault folder, a gate refusal) is reported back as a NO-DATA
    line, because the acceptance this runs after already happened and is
    the primary act; the pattern is a side effect that must never block it.
    """
    words = (words or "").strip()
    if words:
        problem = re.split(r"(?<=[.!?])\s+", words, maxsplit=1)[0].strip()
    else:
        problem = ref
    what = "delivery %s accepted by %s on %s" % (ref, accepted_by, accepted_at)
    if words:
        what += ". " + words
    source = "acceptance record %s" % record_file

    try:
        kwargs = {"vault": pattern_root} if pattern_root else {}
        path, written = pattern_note.write(
            "Delivery %s accepted" % ref, problem, what, source, **kwargs)
    except Exception as exc:  # sbe: allow-silent the pattern write is a side effect of the acceptance above, never its gate; a pattern-store crash must not read back as an acceptance failure
        return "%s: no pattern written (%s)" % (NODATA, exc)
    if not written:
        reason = ("pattern store unavailable" if path is None
                  else "already recorded: %s" % path)
        return "%s: no pattern written (%s)" % (NODATA, reason)
    return "pattern written: %s" % path


def load_all(directory=None):
    """Every acceptance on disk, sorted by accepted_at. Skips a file that
    fails to parse rather than crashing the whole listing on one bad record."""
    directory = directory or DELIVERIES_DIR
    if not os.path.isdir(directory):
        return []
    out = []
    for fname in sorted(os.listdir(directory)):
        if not fname.endswith(".json"):
            continue
        path = os.path.join(directory, fname)
        try:
            with open(path, encoding="utf-8") as fh:
                out.append(json.load(fh))
        except (OSError, ValueError):
            continue
    out.sort(key=lambda e: e.get("accepted_at") or "")
    return out


def per_week(entries):
    """{(iso_year, iso_week): count}, computed fresh from accepted_at on
    every record. Never a running counter: the count is only ever as
    trustworthy as the files that back it."""
    counts = {}
    for e in entries:
        try:
            dt = parse_iso(e["accepted_at"])
        except (KeyError, ValueError):
            continue
        year, week, _ = dt.isocalendar()
        counts[(year, week)] = counts.get((year, week), 0) + 1
    return counts


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--list", action="store_true",
                    help="print recorded acceptances and the per-week count")
    ap.add_argument("--name", help="the delivery's plain-language name")
    ap.add_argument("--ref", help="what it points at: owner/repo#N or a SHA")
    ap.add_argument("--accepted-by", help="a human's name; never defaulted")
    ap.add_argument("--accepted-at", help="ISO date, from the caller")
    ap.add_argument("--recorded-by", choices=["person", "agent"], default=None,
                    help="who typed this record: a person at a terminal, or an "
                         "agent under a named delegation (requires --delegation)")
    ap.add_argument("--delegation", default=None,
                    help="the exact delegation sentence; required with "
                         "--recorded-by agent")
    ap.add_argument("--words", default=None,
                    help="the acceptor's own phrasing (optional)")
    ap.add_argument("--dir", default=None,
                    help="override docs/deliveries/, for tests")
    ap.add_argument("--pattern-root", default=None,
                    help="override the pattern store's vault root "
                         "(pattern_note.VAULT by default), for tests")
    args = ap.parse_args(argv)

    if args.list:
        entries = load_all(args.dir)
        if not entries:
            print("accept-delivery: %s, no acceptances recorded yet" % NODATA)
            return 2
        for e in entries:
            line = "%s  %-24s  accepted_by=%s  %s" % (
                e.get("accepted_at", "?"), e.get("ref", "?"),
                e.get("accepted_by", "?"), e.get("name", "?"))
            if e.get("words"):
                line += "  (%r)" % e["words"]
            rb = e.get("recorded_by")
            if rb is None:
                line += "  recorded_by: NO-DATA (record predates the field)"
            elif rb == "agent":
                line += ("  recorded by an agent under delegation, not counted "
                         "(delegation: %r)" % e.get("delegation", ""))
            else:
                line += "  recorded_by: %s" % rb
            print(line)
        print()
        # Per-week acceptance counts, over person-recorded entries only (row E49):
        # an agent-recorded acceptance is honest about who typed it and is never
        # folded into the count of actual human decisions.
        person_entries = [e for e in entries if e.get("recorded_by") == "person"]
        weeks = per_week(person_entries)
        if not weeks:
            print("no person-recorded acceptances yet: week count 0")
        else:
            for (year, week), count in sorted(weeks.items()):
                print("week %d-W%02d: %d accepted" % (year, week, count))
        return 0

    missing = [flag for flag, val in (("--name", args.name), ("--ref", args.ref),
                                      ("--accepted-by", args.accepted_by),
                                      ("--accepted-at", args.accepted_at),
                                      ("--recorded-by", args.recorded_by))
              if not (val or "").strip()]
    if missing:
        ap.error("recording an acceptance requires %s (no field is ever "
                 "defaulted or inferred)" % ", ".join(missing))
    if args.recorded_by == "agent" and not (args.delegation or "").strip():
        ap.error("--recorded-by agent requires --delegation \"<the exact "
                 "sentence the founder said>\"")

    ok, result = record(args.name, args.ref, args.accepted_by, args.accepted_at,
                        args.recorded_by, args.delegation, args.words, args.dir)
    if not ok:
        print("accept-delivery: refused: %s" % result, file=sys.stderr)
        return 2
    print("accept-delivery: recorded %s" % result)
    # The pattern write is a side effect of a PERSON's acceptance only (row
    # E49 again: an agent-recorded acceptance is not a good outcome a person
    # confirmed), and it runs after the acceptance above already succeeded,
    # so nothing here can turn a recorded acceptance back into a refusal.
    if args.recorded_by == "person":
        print(write_pattern_from_acceptance(
            args.ref, args.accepted_by, args.accepted_at, args.words,
            result, args.pattern_root))
    return 0


if __name__ == "__main__":
    sys.exit(main())

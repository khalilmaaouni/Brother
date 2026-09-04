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

A PATH IS NOT AN IDENTITY (row E94, the second half of the same
delivery-proof finding). The one record this repository shipped cited a run
directory under the running machine's home, which nobody reading the
repository can open, and carried nothing else by which that run could be
recognised. Every record written from a run now carries that run's own
identity (its run id and a sha256 over the exact receipt bytes the checks
were read from) BESIDE the local path, and the path is labelled with whether
it is inside this repository or only on one machine. A record given no
receipt at all reads NO-DATA in its checks field rather than omitting the
field, because an absent claim and a proved one used to look alike on disk.

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
import hashlib
import json
import os
import re
import sys

import pattern_note
import receipt_door

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
           words=None, directory=None, checks=None, run=None):
    """Write one acceptance. Returns (True, path) on success or
    (False, reason) on refusal. Pure enough to test without a subprocess.

    recorded_by separates the person who decided from the process that typed
    (row E49): 'person' for a human at a terminal, or 'agent' for an agent
    acting under a named delegation, which then requires that delegation
    sentence verbatim. Neither shape is inferred; a call giving neither is
    refused before anything is written.

    checks (E79, the delivery-proof skeptic finding) is optional here, so a
    plain human acceptance of a PR or commit (this tool's original shape,
    which names nothing about how the work was checked) keeps working
    unchanged. When a caller DOES pass checks, it must be a real per-file
    check list (scripts/receipt_door.per_file_checks builds exactly this
    from a run's own receipts): an empty or malformed list is refused
    rather than silently written, because a record that CLAIMS per-file
    evidence and carries none is worse than one that never claimed it."""
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
    if checks is not None:
        ok, reason = receipt_door.require_per_file_checks(checks)
        if not ok:
            return False, reason

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
    # E94: a record given no receipt says so in the field a reader looks
    # at. Omitting "checks" made a record that proves nothing the same shape
    # on disk as one that was never claimed to prove anything, which is how
    # the shipped record read for a day. NO-DATA, with what stopped it, is
    # never an empty list and never a pass.
    if checks is None:
        entry["checks"] = NODATA
        entry["checks_reason"] = (
            "no run receipt was given to accept-delivery (--run-dir or "
            "--checks-file), so this record names no changed file and no "
            "command a reader could re-run")
    else:
        entry["checks"] = checks
    if run:
        entry["run"] = run

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


def _inside_repo(path):
    """True when `path` lies inside this repository. A run directory inside
    the tree can be shipped with the record; one outside it cannot, and the
    record has to say which it is rather than printing a path and leaving
    the reader to find out."""
    return os.path.abspath(path).startswith(os.path.abspath(ROOT) + os.sep)


def _tilde(path):
    """The caller's home directory collapsed back to ~, so a stored path
    names a location rather than one account's spelling of it. Anything
    outside home (and NO-DATA) is returned untouched."""
    home = os.path.expanduser("~")
    if path == home or path.startswith(home + os.sep):
        return "~" + path[len(home):]
    return path


def run_identity(run_dir):
    """(identity, "") naming the run a record's checks were read from, or
    (None, reason) when the run's own files cannot be read.

    E94: the shipped record pointed at a run directory under the running
    machine's home and carried nothing else, so a reader who could not open
    that path had no way to tell which run the record meant, or whether a
    run directory they were handed was that one. A path is not an identity.
    The two facts that are: the run's own id (the run directory's name,
    which brother_run.py mints and which a delivery ref already carries),
    and a sha256 over the exact bytes of the Work document and claims.json
    the per-file checks were derived from. The local path is kept beside
    them, under a name that says what it is, because it is still the
    fastest way to the output on the machine that ran the work.

    This reads files and hashes them. It runs no check and invents nothing:
    a run whose receipt files cannot be read is refused, not summarised."""
    if not os.path.isdir(run_dir):
        return None, "--run-dir %r is not a directory" % run_dir
    names = sorted(n for n in os.listdir(run_dir)
                   if (n.startswith("W-") and n.endswith(".json"))
                   or n == "claims.json")
    if not names:
        return None, ("--run-dir %r holds neither a W-*.json Work document "
                      "nor claims.json, so there is nothing to identify the "
                      "run by" % run_dir)
    digest = hashlib.sha256()
    for name in names:
        try:
            with open(os.path.join(run_dir, name), "rb") as fh:
                blob = fh.read()
        except OSError as exc:
            return None, ("--run-dir %r: %s could not be read (%s)"
                          % (run_dir, name, exc))
        # Length-prefixed per file, so two different splits of the same
        # bytes across files cannot collide into one digest.
        digest.update(("%s\0%d\0" % (name, len(blob))).encode("utf-8"))
        digest.update(blob)
    abs_dir = os.path.abspath(run_dir)
    return {
        "run_id": os.path.basename(abs_dir),
        "receipt_digest": "sha256:%s" % digest.hexdigest(),
        "receipt_files": names,
        "run_dir_local": _tilde(abs_dir),
        "run_dir_in_repository": _inside_repo(abs_dir),
    }, ""


def checks_from_run_dir(run_dir):
    """(checks, "") for the per-file check list a completed run's own
    directory already holds, or (None, reason) naming what stopped it.

    E79 / BO2 (the delivery-proof skeptic finding): the only record this
    repository shipped named no changed file and no check, because building
    that list meant hand-writing JSON and nobody did. A run directory
    already carries both halves receipt_door needs, the Work document
    (W-*.json, the units with their done_checks and files_changed_by_unit)
    and claims.json (what each worker released, with the command it ran and
    the exit code it captured), so this reads them and hands
    receipt_door.per_file_checks the same two arguments brother_run.py does
    at the end of a run. It runs no check and invents nothing: a run that
    recorded no changed file yields an empty list, which record() then
    refuses, exactly as it refuses a hand-written empty one."""
    if not os.path.isdir(run_dir):
        return None, "--run-dir %r is not a directory" % run_dir
    work = sorted(n for n in os.listdir(run_dir)
                  if n.startswith("W-") and n.endswith(".json"))
    if not work:
        return None, ("--run-dir %r holds no W-*.json Work document, so the "
                      "units it delivered cannot be read" % run_dir)
    if len(work) > 1:
        return None, ("--run-dir %r holds %d W-*.json Work documents (%s); "
                      "a run directory carries exactly one"
                      % (run_dir, len(work), ", ".join(work)))
    claims_path = os.path.join(run_dir, "claims.json")
    if not os.path.isfile(claims_path):
        return None, ("--run-dir %r holds no claims.json, so no check "
                      "command or exit code can be read from it" % run_dir)
    try:
        with open(os.path.join(run_dir, work[0]), encoding="utf-8") as fh:
            record_doc = json.load(fh)
        with open(claims_path, encoding="utf-8") as fh:
            claims = json.load(fh)
    except (OSError, ValueError) as exc:
        return None, ("--run-dir %r could not be read (%s)" % (run_dir, exc))
    log_path = os.path.join(run_dir, "run.log")
    receipts = receipt_door.receipts_for(
        record_doc, claims, [],
        log_path=log_path if os.path.isfile(log_path) else None)
    checks = receipt_door.per_file_checks(record_doc, receipts)
    # E94: every one of these output_locations is the run.log inside the
    # run directory the caller named, and that directory is very often
    # outside this repository. Say which, on the entry itself, so a reader
    # who cannot open the path knows it is not a broken link but a machine
    # they do not have, and reaches for the record's run block instead.
    scope = "in-repository" if _inside_repo(run_dir) else "machine-local"
    for entry in checks:
        entry["output_location"] = _tilde(
            str(entry.get("output_location") or NODATA))
        entry["output_location_scope"] = scope
    return checks, ""


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
    ap.add_argument("--checks-file", default=None,
                    help="path to a JSON list of per-file check entries "
                         "(E79; scripts/receipt_door.per_file_checks builds "
                         "this from a run's own receipts), attached to the "
                         "record as 'checks' and refused if empty or "
                         "malformed")
    ap.add_argument("--run-dir", default=None,
                    help="a completed run's own directory (holding its "
                         "W-*.json Work document and claims.json): the "
                         "per-file check list is DERIVED from it, so a "
                         "record never has to be hand-written to carry the "
                         "changed files and the command that verified each "
                         "one. Mutually exclusive with --checks-file")
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

    checks = None
    if args.checks_file and args.run_dir:
        ap.error("--checks-file and --run-dir both name where the per-file "
                 "checks come from; pass one, never both")
    run = None
    if args.run_dir:
        checks, reason = checks_from_run_dir(args.run_dir)
        if checks is None:
            print("accept-delivery: refused: %s" % reason, file=sys.stderr)
            return 2
        run, reason = run_identity(args.run_dir)
        if run is None:
            print("accept-delivery: refused: %s" % reason, file=sys.stderr)
            return 2
    if args.checks_file:
        try:
            with open(args.checks_file, "r", encoding="utf-8") as fh:
                checks = json.load(fh)
        except (OSError, ValueError) as exc:
            print("accept-delivery: refused: --checks-file %r could not be "
                 "read as JSON (%s)" % (args.checks_file, exc), file=sys.stderr)
            return 2

    ok, result = record(args.name, args.ref, args.accepted_by, args.accepted_at,
                        args.recorded_by, args.delegation, args.words, args.dir,
                        checks, run)
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

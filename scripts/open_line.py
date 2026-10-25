#!/usr/bin/env python3
"""open_line: declare the between-release drift on release-invariant when the next release line opens.

WHY. Between releases the shipped runtime changes while the version string still names the last release, so
release_invariant.py reads drift by design, and readiness_gate's reproducible-release-artifact row turns red on the
first runtime change of a new line. The cut removes the declaration at its version bump (cut_v1.0.0.sh step 2), and
the 1.1.4 and 1.1.8 lines each found the red and wrote the same entry back by hand (52f7c3ca2, 1a17fa43e). The owner
chose this declared drift route on 2026-10-24 (ruling C3), so the line opening writes it mechanically.

Usage (this file defines both arguments): open_line.py --version X.Y.Z [--today YYYY-MM-DD]

Writes the entry when docs/plan/BATTERY-EXPECTATIONS.json has none, with review_by fourteen days out, the same way
cut_v1.0.0.sh step 2 writes that file (json indent 2, one trailing newline), and leaves every other entry alone.
An entry already there is left untouched (exit 0, ALREADY). A version that is not X.Y.Z, or a file that cannot be read
as the expectations object, refuses and writes nothing (exit 2, NO-DATA).
"""
import argparse
import datetime
import json
import os
import re
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PATH = os.path.join(ROOT, "docs", "plan", "BATTERY-EXPECTATIONS.json")
KEY = "release-invariant"
REVIEW_DAYS = 14


def entry(version, today):
    """The declaration for the line that ends with the VERSION cut, recorded TODAY (a datetime.date)."""
    return {
        "class": "expected_unavailable",
        "failing_tests": "none: scripts/release_invariant.py is a plain script; its FAIL line names drifted runtime "
                         "files, not tests",
        "reason": "Between releases the shipped runtime changes while the version string still names the last release, "
                  "so release_invariant.py reads drift by design (readiness_gate.py, reproducible-release-artifact). "
                  "The owner chose this declared drift route on 2026-10-24 (ruling C3); scripts/open_line.py wrote it "
                  "when the %s line opened." % version,
        "recorded": today.isoformat(),
        "review_by": (today + datetime.timedelta(days=REVIEW_DAYS)).isoformat(),
        "removal_condition": "the %s cut removes this entry at its version bump (cut_v1.0.0.sh step 2, 'drop the "
                             "release-invariant exception'); if no %s cut happens by review_by, re-date it with a "
                             "stated reason." % (version, version),
    }


def open_line(version, today, path=None):
    """(exit code, line). Writes the declaration only when the file holds none. PATH is read at call time."""
    path = path or PATH
    if not isinstance(version, str) or not re.fullmatch(r"[0-9]+\.[0-9]+\.[0-9]+", version):
        return 2, "NO-DATA: the version must be X.Y.Z, got %r" % (version,)
    try:
        with open(path, encoding="utf-8") as fh:
            doc = json.load(fh)
    except (OSError, ValueError) as exc:
        return 2, "NO-DATA: %s could not be read: %s" % (path, exc)
    if not isinstance(doc, dict) or not isinstance(doc.get("checks"), dict):
        return 2, "NO-DATA: %s holds no checks object" % path
    if KEY in doc["checks"]:
        return 0, "ALREADY: %s declares %s (recorded %s), left as it is" % (path, KEY, doc["checks"][KEY].get("recorded"))
    doc["checks"][KEY] = entry(version, today)
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(doc, fh, indent=2)
        fh.write("\n")
    return 0, "DECLARED: %s for the %s line, review_by %s" % (KEY, version, doc["checks"][KEY]["review_by"])


def main(argv=None):
    ap = argparse.ArgumentParser(description="declare the between-release drift when a release line opens")
    ap.add_argument("--version", required=True, help="the version the opening line will cut, X.Y.Z")
    ap.add_argument("--today", help="YYYY-MM-DD, for tests; the local date when absent")
    args = ap.parse_args(argv)
    try:
        today = datetime.date.fromisoformat(args.today) if args.today else datetime.date.today()
    except ValueError:
        print("NO-DATA: --today must be YYYY-MM-DD, got %r" % args.today)
        return 2
    code, line = open_line(args.version, today)
    print(line)
    return code


if __name__ == "__main__":
    sys.exit(main())

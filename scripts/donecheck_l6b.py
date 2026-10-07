#!/usr/bin/env python3
"""L6b's done check: the REAL catalogue holds 70 or more entries, each with its own reservation id.

usage (repo root): python3 -B scripts/donecheck_l6b.py
Exit 0 when the unit's goal is met, 1 when it is not, 2 when the deliverable cannot be read (NO-DATA, never a pass).

WHY THIS IS NOT A test_ FILE. It is a UNIT DONE CHECK, not a regression test. It is EXPECTED TO BE RED until the
unit's work is actually finished, and a permanently red test inside scripts/check_all.sh would refuse every
landing on the branch, which would stop all other work to express one unfinished unit. The plan's `state` field
is the right place to carry "unfinished"; the battery is not. `scripts/test_battery_registration.py` only
demands registration for files matching test_\\w+\\.py, so this name keeps the two concerns apart.

WHAT IT FIXES, measured 2026-09-21. All three L6b sub units read as landed and every L6b suite was green, so
the unit looked ready to close. Its own counting functions, run against the real file, returned 10 and 10
against a goal of 70. The green came from test_jev_catalogue_l6b3.test_parity_end_to_end_70, which builds a
synthetic catalogue INSIDE the test and asserts 70 against that: it proves the counting function works and says
nothing at all about the deliverable. The unit's recorded done_check was an English sentence, so no command ever
compared the two. That is the `green-but-hollow` class in the failure ledger.

The counting functions are IMPORTED from the unit's own module rather than re-implemented here on purpose: a
second regex would be a second opinion about what an entry is, and the unit's own definition is the one that
decides."""
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
CATALOGUE = os.path.join(ROOT, "docs", "plan", "JEV-USE-CASE-CATALOGUE.md")
TARGET = 70


def counts(path=CATALOGUE):
    """(markers, unique reservation ids) from the unit's own functions. Raises on anything unreadable, because a
    catalogue that cannot be read is NO-DATA and must never be reported as a count of zero or as a pass."""
    sys.path.insert(0, HERE)
    import jev_catalogue_l6b3 as l6b3
    with open(path, encoding="utf-8") as fh:
        text = fh.read()
    if not text.strip():
        raise ValueError("the catalogue is empty")
    return l6b3.count_markers(text), l6b3.recount_unique_ids(text)


def main(argv=None):
    path = (argv or sys.argv[1:] or [CATALOGUE])[0]
    try:
        markers, unique = counts(path)
    except (OSError, ValueError, ImportError) as exc:
        print("NO-DATA: the catalogue could not be counted (%s); this is not a pass" % exc)
        return 2
    print("CATALOGUE %s" % path)
    print("ENTRIES   %d (goal %d)" % (markers, TARGET))
    print("IDS       %d unique reservation id(s)" % unique)
    if markers < TARGET:
        print("NOT DONE  L6b is short by %d entries. The passing L6b suites count markers in a fixture built "
              "inside the test, never in this file." % (TARGET - markers))
        return 1
    if unique != markers:
        print("NOT DONE  every entry needs its own reservation id: %d entries against %d unique ids"
              % (markers, unique))
        return 1
    print("DONE      %d entries, each with its own reservation id" % markers)
    return 0


if __name__ == "__main__":
    sys.exit(main())

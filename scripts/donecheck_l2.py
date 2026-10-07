#!/usr/bin/env python3
"""L2's done check: EVERY finding in the wiring audit names file:line, and either its real caller or a pasted grep.

usage (repo root): python3 -B scripts/donecheck_l2.py
Exit 0 when every finding meets the bar, 1 when any does not, 2 when the audit cannot be read (never a pass).

WHY THIS IS NOT A test_ FILE: see the same note in scripts/donecheck_l6b.py. A unit done check is expected to be
red while the unit is unfinished, and a red inside scripts/check_all.sh would refuse every landing on the branch.

WHAT IT FIXES, measured 2026-09-21. All seven L2 sub units read as landed, so the unit looked ready to close.
Its recorded done_check was the English sentence this file's title paraphrases, which no tool can execute, so
the bar was never measured. Measured here for the first time: 68 findings, all 68 naming file_path and
symbol_line, but only 55 naming a caller or a pasted grep. The other 13 (F-016, F-017, F-018, F-034 to F-037,
F-042 and the rest) carry status NO_DATA with caller_path '-', caller_line 0 and both grep columns '-'. Under
this estate's standing rule a NO-DATA row is never a pass, so the unit is 13 findings short of its own bar.

The check reads the audit's own table header rather than assuming a column order, because the table has twelve
columns and an assumed order is how the first three attempts at this measurement each got a different answer."""
import os
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
AUDIT = os.path.join(ROOT, "docs", "architecture", "ONE-SYSTEM-WIRING-AUDIT.md")
EMPTY = ("", "-", "0", "none", "n/a")
NEEDED = ("finding_id", "file_path", "symbol_line", "caller_path", "caller_line", "grep_import", "grep_string")


def rows(path=AUDIT):
    """Every F-nnn row as a dict keyed by the table's OWN header. Raises when the header or the rows are absent,
    because an audit whose table cannot be parsed is NO-DATA and must not read as zero failures."""
    with open(path, encoding="utf-8") as fh:
        lines = fh.read().splitlines()
    header = next((l for l in lines if "finding_id" in l and l.strip().startswith("|")), None)
    if header is None:
        raise ValueError("no findings table header naming finding_id")
    cols = [c.strip() for c in header.strip().strip("|").split("|")]
    missing = [c for c in NEEDED if c not in cols]
    if missing:
        raise ValueError("the findings table is missing column(s): %s" % ", ".join(missing))
    out = []
    for line in lines:
        s = line.strip()
        if not re.match(r"^\|\s*F-\d+", s):
            continue
        cells = [c.strip() for c in s.strip("|").split("|")]
        if len(cells) == len(cols):
            out.append(dict(zip(cols, cells)))
    if not out:
        raise ValueError("the findings table has a header but no F-nnn rows")
    return out


def judge(row, root=ROOT):
    """(meets the bar, why not). file:line is required of every finding, the file must EXIST, and then EITHER a
    named caller OR a pasted grep.

    THE EXISTENCE PREDICATE, added 2026-09-21, is the difference between measuring FORM and measuring TRUTH.
    Until it was added this check asked only whether a row was filled in, so a row about a file that has never
    existed passed the bar the moment somebody pasted a grep next to it. Measured when it was added: 35 of the
    68 rows named a path absent from the tree, 22 of them carrying a confident ORPHAN verdict about code that
    was never written, on synthetic names (facade_1 to facade_13, helper_1 to helper_4, assurance_1 to
    assurance_6, mdm_1_facade to mdm_5_facade). Proved with `git log --all -- <path>` returning 0 commits for
    each, against a control on a real module returning 5. A finding about a file that does not exist is not a
    finding about this system, and no amount of filled columns makes it one."""
    filled = lambda k: str(row.get(k, "")).strip().lower() not in EMPTY
    if not (filled("file_path") and filled("symbol_line")):
        return False, "names no file:line"
    if not os.path.isfile(os.path.join(root, row["file_path"])):
        return False, "names %s, which is not a file in this tree" % row["file_path"]
    if filled("caller_path") and filled("caller_line"):
        return True, ""
    if filled("grep_import") or filled("grep_string"):
        return True, ""
    return False, "status %s with no caller and no grep pasted" % (row.get("status") or "unstated")


def main(argv=None):
    path = (argv or sys.argv[1:] or [AUDIT])[0]
    try:
        found = rows(path)
    except (OSError, ValueError, StopIteration) as exc:
        print("NO-DATA: the audit could not be parsed (%s); this is not a pass" % exc)
        return 2
    bad = [(r["finding_id"], why) for r in found for ok, why in [judge(r)] if not ok]
    print("AUDIT     %s" % path)
    print("FINDINGS  %d | meeting the bar %d | short %d" % (len(found), len(found) - len(bad), len(bad)))
    if bad:
        for fid, why in bad[:15]:
            print("  SHORT   %-7s %s" % (fid, why))
        if len(bad) > 15:
            print("  ...     and %d more" % (len(bad) - 15))
        print("NOT DONE  L2's bar is EVERY finding, so %d short is short." % len(bad))
        return 1
    print("DONE      every finding names a real file:line and either its caller or a pasted grep")
    return 0


if __name__ == "__main__":
    sys.exit(main())

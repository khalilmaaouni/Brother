#!/usr/bin/env python3
"""GM15: the 1.1.5 night's G1 reading (the loop finishes and is intuitive), read from the loop's own ledger.

One rule, one place: this is donecheck_114's own G1 table (landed share, seat waits, timeouts, plan defect refusals,
bare refusals), read against docs/plan/BROTHER-1.1.5-WBS.json instead of the 1.1.4 plan. Nothing is copied: a change to
a bar's rule is made once, in donecheck_114.py, and both releases read it.

usage (repo root):  python3 scripts/donecheck_115.py --goal G1 [--plan PATH] [--runs DIR] [--run-tag TAG] [--since EPOCH]
Exit 0 every bar PASS, 1 any FAIL, 2 any NO-DATA (no ledger, an unreadable plan): NO-DATA is never a pass, never zero.
The 24 hour qualification (the external pack's T91 stage 6) is not this reading: see docs/plan/specs/1.1.5/PLAN-DECISIONS.md."""
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import donecheck_114 as D  # noqa: E402

WBS = os.path.join(os.path.dirname(HERE), "docs", "plan", "BROTHER-1.1.5-WBS.json")


def argv_for(argv):
    """The 1.1.4 entry point's argv with the 1.1.5 plan and version as defaults; a caller's own --plan or --version wins."""
    out = list(argv)
    if not any(a == "--plan" or a.startswith("--plan=") for a in out):
        out += ["--plan", WBS]
    if not any(a == "--version" or a.startswith("--version=") for a in out):
        out += ["--version", "1.1.5"]
    return out


def main(argv=None) -> int:
    return D.main(argv_for(sys.argv[1:] if argv is None else argv))


if __name__ == "__main__":
    sys.exit(main())

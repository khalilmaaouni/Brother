#!/usr/bin/env python3
"""L5b.5 probe: corrupt input must map to refuse or NO-DATA.

Corrupt means counts that cannot all be true and values of the wrong type.
Prints a line beginning BLOCK and exits 0 when every corrupt case is refused
with ValueError; any accepted case prints FAIL and exits non-zero.

Run: python3 tools/l5b_audit/probes/corrupt.py
"""

import os
import sys

_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

try:
    from tools.l5b_audit.score import compute
except ImportError as exc:
    sys.stderr.write("UNKNOWN scorer unavailable: %s\n" % exc)
    raise SystemExit(2)

_CASES = (
    ("stated above total", 4, 5, 0, (), 0),
    ("tested above stated", 4, 3, 4, (), 0),
    ("negative total", -1, 0, 0, (), 0),
    ("bool total", True, 1, 1, (), 0),
    ("float total", 4.0, 1, 1, (), 0),
    ("nan total", float("nan"), 0, 0, (), 0),
    ("none total", None, 0, 0, (), 0),
    ("list probes", 4, 4, 4, [], 0),
    ("text probes", 4, 4, 4, "BLOCK", 0),
)


def main():
    refused = 0
    for case in _CASES:
        try:
            compute(case[1], case[2], case[3], case[4], case[5])
        except ValueError:
            refused += 1
        else:
            print("FAIL corrupt case was accepted: %s" % case[0])
            return 1
    if refused != len(_CASES):
        print("FAIL only %d of %d corrupt cases refused" % (refused, len(_CASES)))
        return 1
    print("BLOCK all %d corrupt scorer inputs refused with ValueError" % refused)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

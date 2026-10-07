#!/usr/bin/env python3
"""L5b.5 probe: unknown input must map to refuse or NO-DATA.

Unknown means probe evidence the scorer cannot read: output that never says
BLOCK, a non-zero probe exit, and a foreign probe record. Prints a line
beginning BLOCK and exits 0 when none of it earns credit and the foreign
record is refused with ValueError.

Run: python3 tools/l5b_audit/probes/unknown.py
"""

import os
import sys

_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

try:
    from tools.l5b_audit.score import FailClosedProbe, compute, fail_closed_fraction
except ImportError as exc:
    sys.stderr.write("UNKNOWN scorer unavailable: %s\n" % exc)
    raise SystemExit(2)


def main():
    silent = FailClosedProbe(name="silent", exit_code=0, output="PASS")
    crashed = FailClosedProbe(name="crashed", exit_code=3, output="BLOCK")
    if silent.passed or crashed.passed:
        print("FAIL unreadable probe evidence was counted as a pass")
        return 1
    fraction = fail_closed_fraction((silent, crashed))
    if fraction != 0.0:
        print("FAIL unknown probe evidence earned %r" % (fraction,))
        return 1
    try:
        fail_closed_fraction((object(),))
    except ValueError:
        pass
    else:
        print("FAIL foreign probe record was accepted")
        return 1
    result = compute(1, 1, 1, (silent,), 0)
    if result.score != 7.0 or result.meets_bar:
        print("FAIL unknown probe evidence still reached %r" % (result.score,))
        return 1
    print("BLOCK unknown probe evidence earns no credit and cannot reach the 8.5 bar")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

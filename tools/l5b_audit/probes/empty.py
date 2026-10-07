#!/usr/bin/env python3
"""L5b.5 probe: empty input must map to refuse or NO-DATA.

Prints a line beginning BLOCK and exits 0 when the scorer refuses credit for
empty input. A missing or broken scorer prints no BLOCK and exits non-zero, so
a missing dependency can never look fail-closed.

Run: python3 tools/l5b_audit/probes/empty.py
"""

import os
import sys

_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

try:
    from tools.l5b_audit.score import compute, fail_closed_fraction
except ImportError as exc:
    sys.stderr.write("UNKNOWN scorer unavailable: %s\n" % exc)
    raise SystemExit(2)


def main():
    without_evidence = fail_closed_fraction(())
    if without_evidence != 0.0:
        print("FAIL empty probe tuple earned %r, expected no credit" % (without_evidence,))
        return 1
    result = compute(0, 0, 0, (), 0)
    if result.score != 0.0 or result.reason != "NO_DATA":
        print("FAIL empty input scored %r with reason %r" % (result.score, result.reason))
        return 1
    print("BLOCK empty input is NO_DATA at score 0.0 and no probe evidence earns no credit")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

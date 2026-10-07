#!/usr/bin/env python3
"""L2b's done check: plugin/runtime/brother is wired into the fast gate, and the gate still fits its budget.

usage (repo root): python3 -B scripts/donecheck_l2b.py [--skip-timing]
Exit 0 when both measurable conditions hold, 1 when either fails, 2 when the gate cannot be read (never a pass).

WHY THIS EXISTS. L2b was marked DONE while its recorded done_check was an English sentence:
"grep -c 'plugin/runtime/brother' scripts/required_fast.sh returns nonzero; the fast gate's own wall-clock
stays within a stated, re-measured budget after the addition; marketplace.json registration for plugin/
happens only after this unit and L2b's own gate both pass green".

A shell cannot run that. Re-running it on 2026-09-21 exited 127, because the shell tried to execute the word
"returns" as a command. So L2b's closure was never mechanically verified by anything, which is the
prose-done-check class this estate's own delivery laws name. It was one of three closed units found that way in
a single sweep, and the other two named test modules with ZERO commits in the entire repository history.

WHAT IT MEASURES, and what it deliberately does not:
  REQ-WIRED    the fast gate references plugin/runtime/brother at all. This is the unit's actual deliverable,
               named in its own title, and it is decisive.
  REQ-BUDGET   the gate's wall clock against the budget stated in required_fast.sh itself ("well under 5
               minutes"), re-measured rather than assumed from the stale 90 second figure the prose warns
               about. Skippable with --skip-timing when a caller only wants the wiring answer, because a five
               minute check does not belong inside a loop pass.
  NOT MEASURED the third clause, that marketplace registration happens only after this gate is green. That is
               U8, which the owner scheduled into the 1.1.0 cut, and it is an ordering constraint on a human
               act rather than a property of this tree. It is reported, never silently folded into a pass."""
import os
import re
import subprocess
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
GATE = os.path.join(HERE, "required_fast.sh")
NEEDLE = "plugin/runtime/brother"
BUDGET_S = 300.0          # "well under 5 minutes wall clock", stated in required_fast.sh's own header


def wiring(path=GATE):
    """How many times the fast gate references the runtime. Raises when the gate cannot be read, because a
    gate that will not open is NO-DATA and must never read as zero, which would look like a clean failure."""
    with open(path, encoding="utf-8") as fh:
        text = fh.read()
    if not text.strip():
        raise ValueError("the fast gate is empty")
    return len(re.findall(re.escape(NEEDLE), text))


def timing(path=GATE, budget=BUDGET_S):
    """(seconds, within_budget). The gate's own exit code is NOT the subject here: this asks how long it takes,
    and a gate that fails fast still answers that question."""
    t0 = time.time()
    try:
        subprocess.run(["sh", path], cwd=ROOT, capture_output=True, timeout=budget * 2)
    except subprocess.TimeoutExpired:
        return budget * 2, False
    except OSError as exc:
        raise ValueError("the fast gate could not be run: %s" % exc)
    took = time.time() - t0
    return took, took <= budget


def main(argv=None):
    args = list(sys.argv[1:] if argv is None else argv)
    try:
        hits = wiring()
    except (OSError, ValueError) as exc:
        print("NO-DATA: the fast gate could not be read (%s); this is not a pass" % exc)
        return 2
    print("GATE      %s" % GATE)
    print("WIRED     %d reference(s) to %s" % (hits, NEEDLE))
    if hits < 1:
        print("NOT DONE  L2b's whole deliverable is that the fast gate reaches the runtime, and it does not.")
        return 1
    if "--skip-timing" in args:
        print("TIMING    skipped by request; the wiring condition alone is satisfied")
        print("PARTIAL   run without --skip-timing to measure the budget condition too")
        return 0
    try:
        took, ok = timing()
    except ValueError as exc:
        print("NO-DATA: %s; this is not a pass" % exc)
        return 2
    print("TIMING    %.1f s against a %.0f s budget stated in the gate's own header" % (took, BUDGET_S))
    if not ok:
        print("NOT DONE  the gate is over its stated budget, which is the condition L2b's own prose named.")
        return 1
    print("NOTE      the third clause, that marketplace registration follows this gate, is U8 and is")
    print("          scheduled into the 1.1.0 cut by owner decision. It is not measured here.")
    print("DONE      the runtime is wired into the fast gate and the gate fits its budget")
    return 0


if __name__ == "__main__":
    sys.exit(main())

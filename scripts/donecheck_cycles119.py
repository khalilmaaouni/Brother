#!/usr/bin/env python3
"""Done check: the REAL 1.1.9 plan's dependency graph has no cycle, so some order can execute it.

THE SUBJECT OF THIS CHECK IS THE DELIVERABLE, not a fixture: the fixtures live in
scripts/test_plan_119.py class Acyclic, and a green there says only that the walk works. This file
reads docs/plan/BROTHER-1.1.9-WBS.json itself.

It is named donecheck, never test_, on purpose: it is RED until the plan is repaired, and a
permanently red test_*.py in the battery refuses every landing on the branch to express one
unfinished unit (the delivery laws, 2026-09-21).

Measured when written, 2026-10-25: twelve cycles, trapping 14 of 39 units, 7 of the 8 loop lane
units (CJK1, FIRST1, FOOT1, FRUN1, PAGE1, PC1, VRT1), and both units the plan's own launch
precondition names (S8, S9).

Exit 0 acyclic. Exit 1 cyclic, naming every cycle. Exit 2 NO-DATA: the plan or the generator cannot
be read, which is never a pass.
"""
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
WBS = os.path.join(ROOT, "docs/plan/BROTHER-1.1.9-WBS.json")
GEN = os.path.join(ROOT, "docs/plan/specs/1.1.9/plan_v119.py")


def main(wbs=WBS, gen=GEN):
    if not os.path.isfile(wbs) or not os.path.isfile(gen):
        print("NO-DATA: the plan or its generator is not in this tree, so its graph cannot be judged")
        return 2
    try:
        with open(wbs, encoding="utf-8") as fh:
            units = json.load(fh)["units"]
    except (OSError, ValueError, KeyError) as exc:
        print("NO-DATA: the plan could not be read as units: %s" % exc)
        return 2
    sys.path.insert(0, os.path.dirname(gen))
    import importlib.util
    loaded = importlib.util.spec_from_file_location("plan_v119_donecheck", gen)
    mod = importlib.util.module_from_spec(loaded)
    try:
        loaded.loader.exec_module(mod)
    except Exception as exc:  # noqa: BLE001  the generator is the thing under test; any import failure is NO-DATA
        print("NO-DATA: the generator could not be loaded: %s" % exc)
        return 2
    if not hasattr(mod, "plan_cycles"):
        print("NO-DATA: the generator has no plan_cycles, so nothing here judges the graph")
        return 2
    cycles = mod.plan_cycles(units)
    if not cycles:
        print("PASS: the 1.1.9 dependency graph is acyclic over %d units" % len(units))
        return 0
    trapped = sorted({i for c in cycles for i in c})
    print("FAIL: the 1.1.9 dependency graph has %d cycle(s), trapping %d of %d units, so no order "
          "executes this plan" % (len(cycles), len(trapped), len(units)))
    for c in cycles:
        print("  " + " -> ".join(c))
    print("  trapped: " + ", ".join(trapped))
    return 1


if __name__ == "__main__":
    sys.exit(main())

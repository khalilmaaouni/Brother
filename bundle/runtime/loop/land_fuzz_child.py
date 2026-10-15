#!/usr/bin/env python3
"""THE FUZZ CHILD of land_apply: one new module imported by name and fuzzed with the hostile inputs, in its own bounded
process. land_apply.fuzz_in_child starts this file by path (never imports it): land_apply is inside the reviewed runtime
surface (land_batch and native_worker import it), whose dependency census allows no computed import, so the one computed
import_module lives here, in main's own body, where the deploy freeze accepts it as a declared work input
(deploy_stamped.CANDIDATE_WORK_INPUTS).
usage (written by land_apply, not by hand):  land_fuzz_child.py NAME RESULTS_FILE WORK_DIR [HOSTILE_INDEXES]"""
import importlib, os, sys
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))   # THIS directory's copy
import land_apply as LA  # noqa: E402


def main():
    if len(sys.argv) < 4: print("usage: land_fuzz_child.py NAME RESULTS_FILE WORK_DIR [HOSTILE_INDEXES]"); return 2
    name, results, work = sys.argv[1], os.path.abspath(sys.argv[2]), sys.argv[3]
    hostile = [LA.HOSTILE[int(j)] for j in sys.argv[4].split(",") if j] if len(sys.argv) > 4 else LA.HOSTILE
    sys.path[:0] = LA.tree_paths(os.getcwd())
    try: m = importlib.import_module(name)
    except BaseException as e: LA._record(results, {"import_failed": repr(e)[:200]}); return 3
    os.chdir(work)  # relative writes land in scratch, never the tree (R2: the "x" directory of 2026-09-28)
    LA._record(results, {"plan": len(LA.fuzz_targets(m)) * len(hostile)})
    c, r = LA.fuzz_module(m, name, hostile, report=lambda l: LA._record(results, {"crash": l}),
                          on_call=lambda f, h, n: LA._record(results, {"start": f, "value": repr(h)[:40], "n": n}))
    LA._record(results, {"end": True, "crashes": c, "returned": r})
    return 0


if __name__ == "__main__": sys.exit(main())

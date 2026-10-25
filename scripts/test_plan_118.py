#!/usr/bin/env python3
"""The 1.1.8 plan is split, specced and validated: the release precheck (docs/plan/seeds/1.1.7/REGISTER.md PLANNING
PIPELINE, P1 to P7) and the split rules R-S1 to R-S9 (docs/plan/specs/1.1.8/plan118_common.py) over
docs/plan/BROTHER-1.1.8-WBS.json, plus the harness gate HX118 must turn green before night 118-1.

One judge per fact, reused where it exists (the shape of scripts/test_plan_116.py):
  class Plan          P1 paths true and P4 shared files ordered (plan_v118.py --verify-only and --check), P3 one command
                      (test_plan_116.plan_problems and grade_build.OK_CMD per sub unit), P2 the loop landed lane owns
                      nothing held or protected (test_plan_116.loop_lane_problems), the spec floor 9 (spec_floor_problems),
                      plan_lint --report with no BLOCKING or NO-DATA except the named BRIEF-BUDGET wait on SLICE1,
                      R-S2 at most 3 files per sub unit, R-S1 no class shared by two sub units, no cross release edge to an
                      unknown id, no loop unit gated on a class 2 decision, and no private term in any spec (counts only).
  class Harness*      HX118's six groups: each harness file exists, parses, defines every class the plan names, and every
                      class of an OPEN sub unit FAILS on the tree (red on base, P5). NOT BUILT skips until HX118 writes a class.
Anything unreadable is a failure or NO-DATA, never a clean list. Fixtures live in temporary directories only.

HUB ONLY: the plan files are not exported (docs/plan/EXPORT-ALLOWLIST.txt carries no plan WBS), so in an exported tree
every class skips with NO-DATA by name, the rule test_plan_117 follows on the 1.1.7 line. NOT BUILT: a harness class
skips with the reason "NOT BUILT: <name>" until its function exists, so no red test is ever committed; the closer reads
a skip as PARTIAL, never DONE, and this file counts a NOT BUILT skip of an OPEN sub unit as red on the base.
"""
import ast
import json
import os
import re
import subprocess
import sys
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.join(HERE, "loop"))   # the loop's own grade_build and plan_lint win
import grade_build  # noqa: E402
import plan_lint  # noqa: E402
import test_plan_116 as P16  # noqa: E402

WBS = "docs/plan/BROTHER-1.1.8-WBS.json"
ADD_WBS = "docs/plan/specs/1.1.8/additions-117/ADDITIONS-1.1.7-WBS.json"
GEN = "docs/plan/specs/1.1.8/plan_v118.py"
SLICE_WAIT = "SLICE1"   # the 1.1.7 addition that lets the loop's slicer show bm_store.py (measured 2026-10-19)
IN_HUB = os.path.isfile(os.path.join(ROOT, WBS))
HUB_ONLY = "NO-DATA: the plan files are not in this tree (an exported tree carries no docs/plan WBS); this test runs in the hub"
GROUPS = {
    "HarnessFirst": ("scripts/test_first_task.py", "scripts/test_proof_can_fail.py"),
    "HarnessCard": ("scripts/test_end_card.py", "scripts/test_proof_source.py", "scripts/test_pages_wellformed.py"),
    "HarnessHooks": ("scripts/test_hook_latency.py", "scripts/test_hook_floors.py", "scripts/test_brother_hook.py"),
    "HarnessGuards": ("scripts/test_guards_for_strangers.py", "scripts/test_routed_model_safety.py"),
    "HarnessFootprint": ("scripts/test_footprint.py", "scripts/test_retired_code.py"),
    "HarnessVault": ("scripts/test_owner_paths.py", "scripts/test_grade_build_contract.py"),
    "HarnessRecords": ("scripts/test_rubric_scores.py", "scripts/test_readme_first_screen.py", "scripts/test_release_records.py"),
}
ADD_GROUPS = {   # HX117, the 1.1.7 additions' harness (read from the additions WBS)
    "H117Egress": ("scripts/test_no_egress.py", "scripts/test_launch_preflight.py"),
    "H117Night": ("scripts/loop/test_night_tick_notify.py", "scripts/loop/test_runner_pool_admit_order.py", "scripts/loop/test_cost_bar.py"),
    "H117Gates": ("scripts/test_harness_frozen.py", "scripts/test_clean_extraction.py", "scripts/test_slicer_guard.py"),
    "H117Classes": ("scripts/test_private_terms_escapes.py", "scripts/test_bundle_runtime.py", "scripts/test_grade_build.py"),
    "H117Classes2": ("scripts/test_land_batch_loop_guard.py", "scripts/test_close_unit.py"),
    "H117Plan": ("scripts/loop/test_plan_split.py",),
}
PLAN_117 = "docs/plan/BROTHER-1.1.7-WBS.json"   # once the additions are admitted, their state is read from the 1.1.7 plan


def load_plan(path=WBS):
    with open(os.path.join(ROOT, path), encoding="utf-8") as fh:
        return json.load(fh)


def sub_checks(plan):
    """{sub id: (unit, done check)} for every sub unit, from the WBS field the generator writes."""
    out = {}
    for u in plan["units"]:
        for sid, cmd in (u.get("sub_done_checks") or {}).items():
            out[sid] = (u, cmd)
    return out


def check_parts(cmd):
    """(test file, [classes]) of one accepted done check, or None."""
    m = re.match(r"^python3 (?:-B )?(scripts/(?:loop/)?test_\w+\.py)((?: [A-Za-z_][\w.]*)*)$", cmd or "")
    return (m.group(1), m.group(2).split()) if m else None


def harness_problems(path, classes, open_classes, root):
    """What is wrong with one harness file: absent, unparseable, a named class missing, or an OPEN class green."""
    full = os.path.join(root, path)
    if not os.path.isfile(full):
        return ["%s: absent" % path]
    try:
        with open(full, encoding="utf-8") as fh:
            tree = ast.parse(fh.read())
    except (OSError, SyntaxError, UnicodeDecodeError) as exc:
        return ["%s: does not parse (%s)" % (path, exc)]
    have = {n.name for n in tree.body if isinstance(n, ast.ClassDef)}
    problems = ["%s: class %s missing" % (path, c) for c in classes if c not in have]
    for c in sorted(set(open_classes) & have):
        try:
            r = subprocess.run([sys.executable, "-B", path, c, "-v"], cwd=root, capture_output=True, text=True, timeout=600)
        except (OSError, subprocess.SubprocessError) as exc:
            problems.append("%s %s: NO-DATA, could not run (%s)" % (path, c, exc))
            continue
        out = (r.stdout or "") + (r.stderr or "")
        if r.returncode == 0 and re.search(r"skipped=\d+", out):   # S4: grade_build.red_reason reads a skip as green
            problems.append("%s %s: skipped, not red by an assertion (S4: the grader reads a skip as green, so red on "
                            "the base needs a named assertion to fail)" % (path, c))
            continue
        if r.returncode == 0:
            problems.append("%s %s: green while its sub unit is OPEN (red on base refused, P5)" % (path, c))
        elif not re.search(r"failures=\d+", out) or re.search(r"errors=\d+", out):
            problems.append("%s %s: red for the wrong reason (an error, not an assertion failure; P5 needs a named "
                            "assertion to fail)" % (path, c))
    return problems


class Plan(unittest.TestCase):
    """The plan as written today: every rule a script can read."""

    @classmethod
    def setUpClass(cls):
        if not IN_HUB:
            raise unittest.SkipTest(HUB_ONLY)
        cls.plan = load_plan()
        cls.subs = sub_checks(cls.plan)

    def test_the_generator_verifies_and_is_fresh(self):
        for flag in ("--verify-only", "--check"):
            r = subprocess.run([sys.executable, "-B", GEN, flag], cwd=ROOT, capture_output=True, text=True, timeout=600)
            self.assertEqual(r.returncode, 0, "%s %s: %s" % (GEN, flag, (r.stdout + r.stderr)[-800:]))

    def test_every_done_check_is_one_command(self):
        waiting = {u["id"] for u in self.plan["units"] if SLICE_WAIT in (u.get("after_release") or [])}
        problems = [p for p in P16.plan_problems(os.path.join(ROOT, WBS), ROOT)
                    if not (p.split()[:1] == ["BLOCKING"] and len(p.split()) > 3 and p.split()[1] in waiting
                            and p.split()[3].startswith("BRIEF-BUDGET"))]
        self.assertEqual(problems, [])
        bad = [sid for sid, (_u, cmd) in self.subs.items() if not grade_build.OK_CMD.match(cmd or "")]
        self.assertEqual(bad, [])

    def test_the_loop_landed_lane_owns_nothing_held(self):
        held, why = P16.held_set(ROOT)
        self.assertIsNotNone(held, why)
        loop_landed = {"units": [dict(u, lane="loop") for u in self.plan["units"]
                                 if u.get("lane") == "loop" and u.get("landing") == "loop"]}
        self.assertEqual(P16.loop_lane_problems(loop_landed, held, plan_lint.protected_reason), [])

    def test_the_loop_sub_units_score_nine(self):
        units = [u["id"] for u in self.plan["units"] if u.get("lane") in ("loop", "kernel")]
        self.assertEqual(P16.spec_floor_problems(ROOT, WBS, units), [])

    def test_plan_lint_finds_nothing_but_the_named_wait(self):
        r = subprocess.run([sys.executable, "-B", "scripts/loop/plan_lint.py", "--report", WBS], cwd=ROOT,
                           capture_output=True, text=True, timeout=1800)
        waiting = {u["id"] for u in self.plan["units"] if SLICE_WAIT in (u.get("after_release") or [])}
        bad = []
        for line in r.stdout.splitlines():
            parts = line.split()
            if len(parts) > 4 and parts[0] == "LINT" and parts[1] in ("BLOCKING", "NO-DATA"):
                if not (parts[1] == "BLOCKING" and parts[2] in waiting and parts[4].startswith("BRIEF-BUDGET")):
                    bad.append(line[:200])
        self.assertIn("LINT SUMMARY", r.stdout)
        self.assertEqual(bad, [])

    def test_no_sub_unit_changes_more_than_three_files(self):
        sizes = {sid: n for u in self.plan["units"] for sid, n in (u.get("sub_file_counts") or {}).items()}
        self.assertTrue(sizes, "NO-DATA: the WBS carries no sub_file_counts")
        self.assertEqual({s: n for s, n in sizes.items() if n > 3}, {})

    def test_no_class_is_shared_by_two_sub_units(self):
        seen, bad = {}, []
        for sid, (_u, cmd) in self.subs.items():
            parts = check_parts(cmd)
            for c in (parts[1] if parts else []):
                key = (parts[0], c)
                if key in seen and seen[key] != sid:
                    bad.append("%s and %s both run %s %s" % (seen[key], sid, parts[0], c))
                seen.setdefault(key, sid)
        self.assertEqual(bad, [])

    def test_no_unit_runs_before_what_it_depends_on(self):
        rank = {"118-0": -1, "118-1": 0, "118-1b": 1, "118-1c": 2}
        by_id = {u["id"]: u for u in self.plan["units"]}
        bad = ["%s (%s) depends on %s (%s)" % (u["id"], u["night"], d, by_id[d]["night"])
               for u in self.plan["units"] for d in u.get("depends_on") or []
               if rank.get(by_id[d]["night"], 9) > rank.get(u["night"], -1)]
        self.assertEqual(bad, [])

    def test_no_night_admits_more_than_the_measured_rate(self):
        import plan_store   # scripts/loop, on the path above: the pool admits nothing NOT_IN_RELEASE (council C1 deferrals)
        per = {}
        for u in self.plan["units"]:
            if u.get("lane") in ("loop", "kernel") and u.get("state") not in plan_store.NOT_IN_RELEASE:
                per[u["night"]] = per.get(u["night"], 0) + len(u.get("sub_units") or [])
        self.assertTrue(per, "NO-DATA: no loop sub unit")
        self.assertEqual({n: c for n, c in per.items() if c > 21}, {})   # 90 percent of the 24 unit admission cap

    def test_no_loop_landing_waits_on_a_stamp_of_the_same_night(self):
        by_id = {u["id"]: u for u in self.plan["units"]}
        bad = ["%s waits on %s, a stamp of the same night %s" % (u["id"], d, u["night"])
               for u in self.plan["units"] if u.get("landing") == "loop" for d in u.get("depends_on") or []
               if by_id[d].get("landing") == "stamp" and by_id[d]["night"] == u["night"]]
        self.assertEqual(bad, [])

    def test_every_unit_carries_its_north_star_step_and_routes(self):
        bad = [u["id"] for u in self.plan["units"] if u.get("north_star_step", "NO-DATA") == "NO-DATA"
               or set(u.get("sub_routes") or {}) != set(u.get("sub_units") or [])]
        self.assertEqual(bad, [])

    def test_loop_scope_is_every_loop_built_unit(self):
        scoped = set(re.findall(r"[A-Z][A-Za-z0-9]+", self.plan.get("loop_scope", "")))
        built = {u["id"] for u in self.plan["units"] if u.get("lane") in ("loop", "kernel")}
        self.assertEqual(scoped, built)

    def test_no_loop_unit_waits_on_a_class_2_decision(self):
        bad = [u["id"] for u in self.plan["units"] if u.get("lane") in ("loop", "kernel")
               and "class 2" in str(u.get("owner_decision", ""))]
        self.assertEqual(bad, [])

    def test_no_private_term_in_any_spec(self):
        import private_terms_scan as S
        terms = S.load_terms()
        if terms is None:
            self.skipTest("NO-DATA: the private terms list is absent on this machine (never a pass)")
        hits = 0
        for u in self.plan["units"]:
            with open(os.path.join(ROOT, u["spec"]), encoding="utf-8") as fh:
                hits += len(S.scan_text(fh.read(), terms))
        self.assertEqual(hits, 0, "private term hits (count only): %d" % hits)


def harness_case_problems(group, groups=GROUPS, wbs=WBS, root=ROOT):
    """(skip reason or None, problems) for one harness group. Only a group no in-release sub unit names is skipped (it
    owes nothing); a harness with no class written yet is a problem, never a skip (S4: red needs a named assertion)."""
    with open(os.path.join(root, wbs), encoding="utf-8") as fh:
        plan = json.load(fh)
    if wbs == ADD_WBS and os.path.isfile(os.path.join(root, PLAN_117)):
        with open(os.path.join(root, PLAN_117), encoding="utf-8") as fh:
            live = {u["id"]: u for u in json.load(fh)["units"]}
        for u in plan["units"]:
            if u["id"] in live:
                u["state"] = live[u["id"]].get("state", u.get("state"))
    named, open_named = {}, {}
    import plan_store   # scripts/loop: a unit outside the release owes no harness class, as the pool admits it (council C1)
    for sid, (u, cmd) in sub_checks(plan).items():
        parts = check_parts(cmd)
        if not parts or parts[0] not in groups[group] or u.get("state") in plan_store.NOT_IN_RELEASE:
            continue
        named.setdefault(parts[0], set()).update(parts[1])
        if u.get("state") != "DONE":
            open_named.setdefault(parts[0], set()).update(parts[1])
    if not named:   # every unit naming this group is DEFERRED or RETIRED: nothing is owed in this release
        return "NO-DATA: no sub unit in this release names %s's harness" % group, []
    problems = []
    for path in groups[group]:
        if path in named:   # only the files an in-release sub unit names
            problems += harness_problems(path, sorted(named[path]), sorted(open_named.get(path, ())), root)
    return None, problems


def _harness_case(group, groups=GROUPS, wbs=WBS):
    def test(self):
        if not IN_HUB or not os.path.isfile(os.path.join(ROOT, wbs)):
            self.skipTest(HUB_ONLY)
        skip, problems = harness_case_problems(group, groups, wbs)
        if skip:
            self.skipTest(skip)
        self.assertEqual(problems, [])
    return test


for _g in GROUPS:
    globals()[_g] = type(_g, (unittest.TestCase,), {"test_the_harness_is_written_and_red": _harness_case(_g)})
for _g in ADD_GROUPS:
    globals()[_g] = type(_g, (unittest.TestCase,), {"test_the_harness_is_written_and_red": _harness_case(_g, ADD_GROUPS, ADD_WBS)})

if __name__ == "__main__":
    unittest.main()

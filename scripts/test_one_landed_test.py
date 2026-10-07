#!/usr/bin/env python3
"""The last hand written landed matchers read a landing through plan_store.sub_landed, the one landed test.

WHY. Lane G (2026-09-27) made plan_store.sub_landed the single whole token landed matcher: the hand written
r"<sub>[^.]{0,80}?(landed|LANDED|DONE)" read A.1 inside "A.10 landed" and counted "A.1 not landed: gate refused" as a
landing. Four copies were left in files that lane did not own: land_batch's already landed gate and its closed
computation, loop_done's landed_subs and salvage's landed_subs. Each reader is driven here with one fixture per
condition (a longer id that starts with this one, a negation) plus a control that must still read as landed.
loop_done and salvage are driven at their entry points, as the pass runs them; land_batch's gate is the decision
function main() acts on (test_land_batch_callsites covers that it acts), and unit_closed is the predicate main()
computes the closed list with.

Run: python3 -B scripts/test_one_landed_test.py
"""
import json
import os
import subprocess
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
LOOP = os.path.join(HERE, "loop")
sys.path.insert(0, LOOP)
import land_batch as LB  # noqa: E402

PREFIX = (["A.1", "A.10"], "A.10 landed 2026-09-27 on both Pythons.")   # A.1 is a prefix of a landed id
NEGATED = (["A.1"], "A.1 not landed: gate refused.")                  # A.1 named, and named NOT landed
LANDED = (["A.1"], "A.1 landed 2026-09-27 on both Pythons.")          # the control: A.1 did land


# Admission reads the unit's spec and refuses a done check the landing gate refuses (2026-09-29), so the fixture
# unit names a spec whose sections each carry one accepted command. Removed at exit with its directory.
_SPEC_DIR = tempfile.TemporaryDirectory(prefix="one-landed-spec-")
SPEC = os.path.join(_SPEC_DIR.name, "A.md")
with open(SPEC, "w", encoding="utf-8") as _fh:
    _fh.write("# A\n### A.1\nDone check: `python3 -B scripts/test_a.py`\n### A.10\nDone check: `python3 -B scripts/test_a.py`\n")


def plan_of(subs, evidence, state="FIX-FIRST"):
    return {"units": [{"id": "A", "state": state, "spec": SPEC, "sub_units": list(subs), "evidence": evidence}]}


class LandBatchGate(unittest.TestCase):
    """gate() holds a build whose sub unit already landed; a build whose sub unit did not land must pass."""
    P = "/x/A.1-r0-build.json"

    def gate(self, subs, evidence):
        return LB.gate(self.P, "READY " + self.P, {"A": {"state": "FIX-FIRST"}}, {"A.1": {"score": 9}}, plan_of(subs, evidence))

    def test_a_longer_id_that_landed_does_not_hold_this_build(self):
        self.assertEqual(self.gate(*PREFIX), "")

    def test_a_negated_landing_does_not_hold_this_build(self):
        self.assertEqual(self.gate(*NEGATED), "")

    def test_control_a_real_landing_holds_this_build(self):
        self.assertEqual(self.gate(*LANDED), "already landed per plan evidence")


class LandBatchClosed(unittest.TestCase):
    """unit_closed(u) decides whether main() lists a unit as closed after this landing's lines are appended."""

    def closed(self, subs, evidence):
        return LB.unit_closed(plan_of(subs, evidence)["units"][0])

    def test_a_longer_id_that_landed_does_not_close_the_unit(self):
        self.assertFalse(self.closed(*PREFIX))

    def test_a_negated_landing_does_not_close_the_unit(self):
        self.assertFalse(self.closed(["A.1", "A.2"], "A.1 not landed: gate refused. A.2 landed today."))

    def test_control_every_sub_unit_landed_closes_the_unit(self):
        self.assertTrue(self.closed(["A.1", "A.2"], "A.1 landed today. A.2 landed today."))

    def test_main_computes_the_closed_list_with_unit_closed(self):
        # STATIC, as test_land_batch_callsites is for paths reachable only after a real landing (minutes per case):
        # main() must still decide `closed` by unit_closed, or the behavioural cases above test a function nobody calls.
        import inspect
        self.assertIn('if unit_closed(u): closed.append(u["id"])', inspect.getsource(LB.main))


class EntryPoints(unittest.TestCase):
    """loop_done.py and salvage.py run as the pass runs them: a subprocess in a temp root with its own HOME."""

    def setUp(self):
        t = tempfile.TemporaryDirectory(prefix="one-landed-test-")
        self.addCleanup(t.cleanup)
        self.d = t.name
        self.home = os.path.join(self.d, "home")
        self.runs = os.path.join(self.home, ".claude", "evidence", "unit-runs")
        os.makedirs(os.path.join(self.d, "docs", "plan"))
        self.plan = os.path.join(self.d, "docs", "plan", "BROTHER-1.1.0-LAUNCH-WBS.json")

    def write_plan(self, subs, evidence):
        with open(self.plan, "w", encoding="utf-8") as fh:
            json.dump(plan_of(subs, evidence, state="DONE"), fh)

    def run_tool(self, argv, **extra):
        env = {k: v for k, v in os.environ.items() if not k.startswith(("BROTHER_", "SALVAGE_", "PYTHON"))}
        env.update(HOME=self.home, **extra)
        return subprocess.run([sys.executable, "-B"] + argv, cwd=self.d, env=env, capture_output=True, text=True, timeout=120)

    # loop_done: a READY build of an unlanded sub unit keeps the loop WORKING; read as landed, it vanishes
    def loop_done(self, subs, evidence):
        self.write_plan(subs, evidence)
        run = os.path.join(self.runs, "A.1-010000")
        os.makedirs(run, exist_ok=True)
        with open(os.path.join(run, "STATUS"), "w", encoding="utf-8") as fh:
            fh.write("READY /x/A.1-r0-build.json\n")
        return self.run_tool([os.path.join(LOOP, "loop_done.py")])

    def test_loop_done_a_longer_id_that_landed_leaves_this_build_ready(self):
        r = self.loop_done(*PREFIX)
        self.assertIn("READY and unlanded: A.1", r.stdout, r.stdout + r.stderr)

    def test_loop_done_a_negated_landing_leaves_this_build_ready(self):
        r = self.loop_done(*NEGATED)
        self.assertIn("READY and unlanded: A.1", r.stdout, r.stdout + r.stderr)

    def test_control_loop_done_a_real_landing_is_not_ready(self):
        r = self.loop_done(*LANDED)
        self.assertNotIn("READY and unlanded", r.stdout, r.stdout + r.stderr)
        self.assertIn(r.returncode, (0, 1), r.stdout + r.stderr)   # FINISHED, or WORKING on a live runner elsewhere

    # salvage: a grader PASS build of an unlanded sub unit is listed; read as landed, it is dropped from the list
    def salvage(self, subs, evidence):
        self.write_plan(subs, evidence)
        rd = os.path.join(self.runs, "A.1-010000", "round1")
        os.makedirs(os.path.join(rd, "out"), exist_ok=True)
        with open(os.path.join(rd, "out", "A.1-r0-build.json"), "w", encoding="utf-8") as fh:
            json.dump({"sub": "A.1"}, fh)
        with open(os.path.join(rd, "lane.log"), "w", encoding="utf-8") as fh:
            fh.write("A.1 PASS A.1-r0\n")
        with open(os.path.join(os.path.dirname(rd), "STATUS"), "w", encoding="utf-8") as fh:
            fh.write("EXHAUSTED after 5 rounds\n")
        return self.run_tool([os.path.join(LOOP, "salvage.py"), "list"], SALVAGE_RUNS=self.runs, SALVAGE_PLAN=self.plan)

    def test_salvage_a_longer_id_that_landed_leaves_this_build_listed(self):
        r = self.salvage(*PREFIX)
        self.assertIn("PASSED THE GRADER: 1 ", r.stdout, r.stdout + r.stderr)

    def test_salvage_a_negated_landing_leaves_this_build_listed(self):
        r = self.salvage(*NEGATED)
        self.assertIn("PASSED THE GRADER: 1 ", r.stdout, r.stdout + r.stderr)

    def test_control_salvage_a_real_landing_is_not_listed(self):
        r = self.salvage(*LANDED)
        self.assertIn("PASSED THE GRADER: 0 ", r.stdout, r.stdout + r.stderr)


# X3 finding 5 (Codex cross lane reviews, 2026-09-27): the pool, the runner, and the diagnostician still carried the old
# matcher, so with A.1 inside "A.10 landed" or "A.1 not landed" the pool declared the unit finished and started nothing.
import ast  # noqa: E402
import re  # noqa: E402
import plan_store  # noqa: E402
import runner_pool as RP  # noqa: E402
import diag_brief as DB  # noqa: E402


class RunnerPoolReaders(unittest.TestCase):
    """remaining_of orders the units; admissible gates every start (the pool's, salvage's and diag_apply's)."""

    def test_remaining_of_a_longer_id_that_landed_leaves_this_one_open(self):
        self.assertEqual(RP.remaining_of(plan_of(*PREFIX)["units"][0]), 1)

    def test_remaining_of_a_negated_landing_leaves_it_open(self):
        self.assertEqual(RP.remaining_of(plan_of(*NEGATED)["units"][0]), 1)

    def test_control_remaining_of_a_real_landing_is_zero(self):
        self.assertEqual(RP.remaining_of(plan_of(*LANDED)["units"][0]), 0)

    def test_admissible_refuses_a_landing_the_shared_test_reads(self):
        # a landing line with words between the id and the verb: the old gate needed the verb right after the id
        self.assertIn("already landed", RP.admissible(plan_of(["A.1"], "A.1, on both Pythons, landed 2026-09-27."), "A.1"))

    def test_admissible_a_longer_id_or_a_negation_does_not_refuse(self):
        self.assertEqual((RP.admissible(plan_of(*PREFIX), "A.1"), RP.admissible(plan_of(*NEGATED), "A.1")), ("", ""))

    def staged(self, stage):
        plan = plan_of(["A.1"], "")
        plan["units"][0]["execution_stage"] = stage
        return RP.admissible(plan, "A.1")

    def test_admissible_holds_an_s4_unit(self):
        self.assertIn("held: unit A is execution stage S4", self.staged("S4"))

    def test_admissible_refuses_an_unknown_stage_as_no_data(self):
        self.assertTrue(self.staged("S5").startswith("NO-DATA unknown execution stage"))
        self.assertTrue(self.staged("").startswith("NO-DATA unknown execution stage"))

    def test_admissible_admits_a_unit_with_no_stage(self):
        self.assertEqual(RP.admissible(plan_of(["A.1"], ""), "A.1"), "")


class RunnerPoolEntryPoint(unittest.TestCase):
    """runner_pool.py --dry as the pass runs it, in a temp root with its own HOME: which sub unit it would start."""

    def pool(self, subs, evidence, stage=None):
        t = tempfile.TemporaryDirectory(prefix="one-landed-pool-")
        self.addCleanup(t.cleanup)
        home = os.path.join(t.name, "home")
        os.makedirs(os.path.join(home, ".claude", "evidence"))
        # money is not the condition here: the guard answers a fixed cap of 4 (since 2026-09-27 an unreadable guard
        # admits nothing, which turned every case below into "budget cap reached")
        os.makedirs(os.path.join(home, ".claude", "bin"))
        with open(os.path.join(home, ".claude", "bin", "burn_guard.py"), "w", encoding="utf-8") as fh:
            fh.write("print(4)\n")
        with open(os.path.join(home, ".claude", "evidence", "spec-scores.json"), "w", encoding="utf-8") as fh:
            json.dump({s: {"score": 9} for s in subs}, fh)
        os.makedirs(os.path.join(t.name, "docs", "plan"))
        plan = plan_of(subs, evidence)
        plan["units"][0]["spec"] = "spec.md"
        if stage is not None:
            plan["units"][0]["execution_stage"] = stage
        with open(os.path.join(t.name, "docs", "plan", "BROTHER-1.1.0-LAUNCH-WBS.json"), "w", encoding="utf-8") as fh:
            json.dump(plan, fh)
        with open(os.path.join(t.name, "spec.md"), "w", encoding="utf-8") as fh:
            fh.write("".join("## %s\nEdit `scripts/f%d.py`.\nDone check: `python3 -B scripts/test_f%d.py`\n" % (s, i, i) for i, s in enumerate(subs)))
        env = {k: v for k, v in os.environ.items() if not k.startswith(("BROTHER_", "PYTHON"))}
        env.update(HOME=home, BROTHER_SCOPE="^A$")
        r = subprocess.run([sys.executable, "-B", os.path.join(LOOP, "runner_pool.py"), "--dry"], cwd=t.name, env=env,
                           capture_output=True, text=True, timeout=300)
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        return r.stdout

    def test_a_longer_id_that_landed_leaves_this_sub_unit_to_start(self):
        out = self.pool(*PREFIX)
        self.assertRegex(out, r"(?m)^A\s+A\.1\s+START$", out)

    def test_a_negated_landing_leaves_this_sub_unit_to_start(self):
        out = self.pool(*NEGATED)
        self.assertRegex(out, r"(?m)^A\s+A\.1\s+START$", out)

    def test_an_s4_unit_starts_nothing(self):
        out = self.pool(["A.1"], "", stage="S4")
        self.assertNotIn("START", out)

    def test_control_the_same_unit_without_a_stage_starts(self):
        out = self.pool(["A.1"], "")
        self.assertRegex(out, r"(?m)^A\s+A\.1\s+START$", out)

    def test_control_a_real_landing_starts_nothing_and_says_the_unit_is_done(self):
        out = self.pool(*LANDED)
        self.assertIn("every sub unit landed", out)
        self.assertNotIn("START", out)


class RunnerAndDiagnosticianReaders(unittest.TestCase):
    """unit_runner's open sub units (the expected value gate's last sub unit bonus), run as the line in its script body;
    diag_brief.stuck_subs, the list main() sends to the diagnostician."""

    def open_subs(self, subs, evidence):
        path = os.path.join(LOOP, "unit_runner.py")
        with open(path, encoding="utf-8") as fh:
            tree = ast.parse(fh.read())
        node = next(n for n in tree.body if isinstance(n, ast.Assign) and any(getattr(t, "id", "") == "_open_subs" for t in n.targets))
        ns = {"_unit_rec": plan_of(subs, evidence)["units"][0], "re": re, "plan_store": plan_store}
        exec(compile(ast.Module(body=[node], type_ignores=[]), path, "exec"), ns)
        return ns["_open_subs"]

    def test_unit_runner_a_longer_id_that_landed_leaves_this_one_open(self):
        self.assertEqual(self.open_subs(*PREFIX), ["A.1"])

    def test_unit_runner_a_negated_landing_leaves_it_open(self):
        self.assertEqual(self.open_subs(*NEGATED), ["A.1"])

    def test_control_unit_runner_a_real_landing_is_not_open(self):
        self.assertEqual(self.open_subs(*LANDED), [])

    def test_diag_brief_a_longer_id_that_landed_leaves_this_one_stuck(self):
        self.assertEqual(DB.stuck_subs(plan_of(*PREFIX), {"A.1": "EXHAUSTED"}), ["A.1"])

    def test_diag_brief_a_negated_landing_leaves_it_stuck(self):
        self.assertEqual(DB.stuck_subs(plan_of(*NEGATED), {"A.1": "WITHHELD"}), ["A.1"])

    def test_control_diag_brief_a_real_landing_is_not_stuck(self):
        self.assertEqual(DB.stuck_subs(plan_of(*LANDED), {"A.1": "EXHAUSTED"}), [])


class NoLoopToolCarriesItsOwnLandedMatcher(unittest.TestCase):
    """The class, not the instances: a copy of the landed alternation outside plan_store is a second matcher waiting to
    drift (six survived one migration because nothing looked). Every loop tool that is not a test is scanned."""

    def test_the_landed_alternation_lives_in_plan_store_only(self):
        hits = []
        for top, dirs, names in os.walk(LOOP):
            dirs[:] = sorted(d for d in dirs if d != "__pycache__")
            for name in sorted(names):
                if not name.endswith((".py", ".sh")) or name.startswith("test_") or name == "plan_store.py":
                    continue
                with open(os.path.join(top, name), encoding="utf-8") as fh:
                    hits += ["%s:%d" % (os.path.relpath(os.path.join(top, name), LOOP), n)
                             for n, line in enumerate(fh, 1) if "landed|LANDED" in line]
        self.assertEqual(hits, [])


if __name__ == "__main__":
    unittest.main()

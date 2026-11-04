#!/usr/bin/env python3
"""LOOP1.b, the integrator's cases beside the frozen Admitted harness (scripts/test_loop_delivery.py Admitted): the edges the
frozen cases leave unpinned, one condition per case. admission_reason is called directly where the property lives in it.
The two ENTRY POINT cases run the real runner_pool.py (not --dry) in a throwaway launch tree with a scratch HOME whose
~/.claude/bin/unit_runner.py is a counter, so "a refused unit spends zero worker calls" is read from the spawn itself.

    python3 -B scripts/test_loop_admission_edges.py

Python 3.9, standard library only. Lives in scripts/, like LOOP1.a's tests: no loop unit owns it."""
import json
import os
import subprocess
import sys
import tempfile
import unittest

HERE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "loop")
sys.path.insert(0, HERE)
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
GOOD = "python3 -B scripts/test_x.py"


def pool():
    import importlib
    return importlib.import_module("runner_pool")


def unit(**over):
    u = {"id": "U1", "state": "OPEN", "lane": "loop", "loop_gradable": True, "sub_units": ["U1.a"], "owns": ["scripts/x.py"]}
    u.update(over)
    return u


def sub(**over):
    s = {"id": "U1.a", "done_check": GOOD, "files": [["scripts/x.py", "NEW"]]}
    s.update(over)
    return s


class Shapes(unittest.TestCase):
    def why(self, u, s):
        return pool().admission_reason(u, s, ROOT)

    def test_control_the_base_shape_is_admitted(self):
        self.assertEqual("", self.why(unit(), sub()))

    def test_a_missing_lane_key_is_refused(self):
        u = unit()
        del u["lane"]
        why = self.why(u, sub())
        self.assertIn("names no lane", why)
        self.assertIn("clear it by:", why)

    def test_a_lane_of_the_wrong_type_is_refused(self):
        for bad in (None, 5, ["loop"], {"loop": 1}, "", "  "):
            with self.subTest(lane=bad):
                self.assertIn("names no lane", self.why(unit(lane=bad), sub()))

    def test_loop_gradable_must_be_the_boolean_true(self):
        for bad in ("true", 1, None, "yes"):
            with self.subTest(gradable=bad):
                self.assertIn("loop_gradable", self.why(unit(loop_gradable=bad), sub()))
        u = unit()
        del u["loop_gradable"]
        self.assertIn("loop_gradable", self.why(u, sub()))

    def test_a_missing_done_check_is_refused(self):
        for s in (sub(done_check=None), sub(done_check=""), sub(done_check=7)):
            with self.subTest(done_check=s["done_check"]):
                why = self.why(unit(), s)
                self.assertIn("clear it by:", why)
        s = sub()
        del s["done_check"]
        self.assertIn("clear it by:", self.why(unit(), s))

    def test_a_chained_or_c_one_liner_check_is_refused(self):
        for bad in ("python3 -B scripts/test_x.py && echo ok", "python3 -c 1", "python3 -B scripts/test_x.py; true"):
            with self.subTest(check=bad):
                self.assertIn("clear it by:", self.why(unit(), sub(done_check=bad)))

    def test_an_empty_files_list_is_refused_and_an_absent_one_is_left_to_the_spec_gates(self):
        self.assertIn("names no readable file", self.why(unit(), sub(files=[])))
        s = sub()
        del s["files"]
        self.assertEqual("", self.why(unit(), s))

    def test_a_file_entry_that_is_not_a_path_is_refused(self):
        for bad in ([[None, "NEW"]], [""], [5], "scripts/x.py"):
            with self.subTest(files=bad):
                self.assertIn("names no readable file", self.why(unit(), sub(files=bad)))

    def test_a_protected_path_is_refused_naming_the_path_and_the_reviewed_route(self):
        for path in ("scripts/required_fast.sh", ".github/workflows/x.yml", "docs/plan/specs/X.md"):
            with self.subTest(path=path):
                why = self.why(unit(), sub(files=[[path, "existing"]]))
                self.assertIn(path, why)
                self.assertIn("reviewed route", why)
                self.assertIn("clear it by:", why)

    def test_a_plain_string_path_list_is_read_like_the_pairs(self):
        self.assertEqual("", self.why(unit(), sub(files=["scripts/x.py"])))
        self.assertIn("reviewed route", self.why(unit(), sub(files=["scripts/required_fast.sh"])))

    def test_a_sub_unit_the_unit_does_not_list_is_refused(self):
        why = self.why(unit(), sub(id="U1.z"))
        self.assertIn("U1.z", why)
        self.assertIn("clear it by:", why)

    def test_a_sub_unit_that_is_not_a_dict_with_an_id_is_refused(self):
        for bad in ("U1.a", None, {}, {"id": ""}, {"id": 5}):
            with self.subTest(sub=bad):
                self.assertIn("NO-DATA", self.why(unit(), bad))

    def test_a_unit_that_is_not_a_dict_is_refused(self):
        self.assertIn("NO-DATA", self.why(None, sub()))

    def test_an_unforeseen_shape_is_refused_never_admitted(self):
        why = self.why(unit(sub_units=5), sub())   # iterating 5 raises inside the decision
        self.assertTrue(why.startswith("NO-DATA"), why)
        self.assertIn("clear it by:", why)

    def test_a_dict_sub_unit_entry_in_the_plan_is_matched_by_its_id(self):
        self.assertEqual("", self.why(unit(sub_units=[{"id": "U1.a"}]), sub()))

    def test_every_non_loop_lane_is_skipped_with_its_route(self):
        for lane, route in (("session", "lane session: built by the session"), ("owner", "lane owner"), ("kernel", "lane kernel")):
            with self.subTest(lane=lane):
                why = self.why(unit(lane=lane, loop_gradable=False), sub())
                self.assertIn(route, why)
                self.assertIn("clear it by:", why)

    def test_an_unknown_lane_is_refused_by_name_even_when_it_is_gradable(self):
        why = self.why(unit(lane="teleport", loop_gradable=True), sub())
        self.assertIn("teleport", why)
        self.assertIn("unknown lane", why)


class EntryPoint(unittest.TestCase):
    """runner_pool.py as the pass runs it (not --dry): a counter stands where the unit runner would be spawned."""

    def run_pass(self, **unit_over):
        t = tempfile.TemporaryDirectory(prefix="loop1b-edges-")
        self.addCleanup(t.cleanup)
        home = os.path.join(t.name, "home")
        os.makedirs(os.path.join(home, ".claude", "evidence"))
        os.makedirs(os.path.join(home, ".claude", "bin"))
        calls = os.path.join(t.name, "worker-calls.txt")
        with open(os.path.join(home, ".claude", "bin", "burn_guard.py"), "w", encoding="utf-8") as fh:
            fh.write("print(4)\n")
        with open(os.path.join(home, ".claude", "bin", "unit_runner.py"), "w", encoding="utf-8") as fh:
            fh.write("import sys\nopen(%r, 'a').write(' '.join(sys.argv[1:]) + '\\n')\n" % calls)
        with open(os.path.join(home, ".claude", "evidence", "spec-scores.json"), "w", encoding="utf-8") as fh:
            json.dump({"A.1": {"score": 9}}, fh)
        os.makedirs(os.path.join(t.name, "docs", "plan"))
        a = {"id": "A", "state": "OPEN", "lane": "loop", "loop_gradable": True, "spec": "spec.md", "sub_units": ["A.1"], "evidence": ""}
        a.update(unit_over)
        with open(os.path.join(t.name, "docs", "plan", "BROTHER-1.1.0-LAUNCH-WBS.json"), "w", encoding="utf-8") as fh:
            json.dump({"units": [a]}, fh)
        with open(os.path.join(t.name, "spec.md"), "w", encoding="utf-8") as fh:
            fh.write("## A.1\nEdit `scripts/f0.py`.\nDone check: `python3 -B scripts/test_f0.py`\n")
        env = {k: v for k, v in os.environ.items() if not k.startswith(("BROTHER_", "PYTHON"))}
        env.update(HOME=home, BROTHER_SCOPE="^A$")
        r = subprocess.run([sys.executable, "-B", os.path.join(HERE, "runner_pool.py")], cwd=t.name, env=env,
                           capture_output=True, text=True, timeout=300)
        self.assertEqual(0, r.returncode, r.stdout + r.stderr)
        n = 0
        if os.path.exists(calls):
            with open(calls, encoding="utf-8") as fh:
                n = len([l for l in fh.read().splitlines() if l.strip()])
        return n, r.stdout

    def test_an_admitted_unit_reaches_the_spawn(self):
        n, out = self.run_pass()
        self.assertEqual(1, n, out)

    def test_a_session_lane_unit_spends_zero_worker_calls_and_says_why(self):
        n, out = self.run_pass(lane="session", loop_gradable=False)
        self.assertEqual(0, n, out)
        self.assertIn("lane session", out)
        self.assertNotIn("START", out)

    def test_a_unit_with_no_lane_spends_zero_worker_calls(self):
        n, out = self.run_pass(lane=None)
        self.assertEqual(0, n, out)
        self.assertIn("names no lane", out)

    def test_a_not_gradable_loop_unit_spends_zero_worker_calls(self):
        n, out = self.run_pass(loop_gradable=False)
        self.assertEqual(0, n, out)
        self.assertIn("loop_gradable", out)


if __name__ == "__main__":
    unittest.main()

#!/usr/bin/env python3
"""Tests for H1.b: a red neighbour is compared against the base before a quarantine.

REQ-H-BASE: a neighbour test red with the build is the build's fault only when it is green on the base.
Every case builds its own fixture in a temp folder and uses stub runners only, so the suite runs in the
export copy too. This file imports nothing from products/ or plugin/ and never imports subprocess: it
reaches the one exception class it needs through the module under test, which already imports it.
"""
import os
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.insert(0, HERE)

import land_batch as LB


class _Result(object):
    def __init__(self, returncode):
        self.returncode = returncode
        self.stdout = ""
        self.stderr = ""


def _runner(rc):
    def call(argv, env=None):
        return _Result(rc)
    return call


class VerdictCases(unittest.TestCase):
    def test_red_with_build_green_on_base_is_red(self):
        self.assertEqual(LB.neighbour_verdict(True, False), "RED")

    def test_red_on_both_is_inherited(self):
        self.assertEqual(LB.neighbour_verdict(True, True), "INHERITED")

    def test_base_unreadable_is_no_data(self):
        self.assertEqual(LB.neighbour_verdict(True, None), "NO-DATA")

    def test_green_with_build_is_not_red(self):
        for base in (None, True, False):
            with self.subTest(base=base):
                word = LB.neighbour_verdict(False, base)
                self.assertNotEqual(word, "RED")
                self.assertNotEqual(word, "INHERITED")


class HostileInputs(unittest.TestCase):
    def test_hostile_inputs_refuse_never_crash(self):
        nan = float("nan")
        hostile = (None, 1, 0, "x", nan, [], {})
        for a in hostile:
            for b in hostile:
                with self.subTest(a=a, b=b):
                    self.assertEqual(LB.neighbour_verdict(a, b), "NO-DATA")
        # a bool-as-int or a str where the base boolean belongs, with a real bool build flag, is NO-DATA too
        self.assertEqual(LB.neighbour_verdict(True, 1), "NO-DATA")
        self.assertEqual(LB.neighbour_verdict(True, 0), "NO-DATA")
        self.assertEqual(LB.neighbour_verdict(True, "x"), "NO-DATA")


class BaseRunRed(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="h1b-run-")
        self.addCleanup(self.tmp.cleanup)
        self.tree = self.tmp.name
        os.makedirs(os.path.join(self.tree, "scripts"))
        with open(os.path.join(self.tree, "scripts", "test_x.py"), "w", encoding="utf-8") as fh:
            fh.write("# placeholder\n")
        self.tp = "scripts/test_x.py"
        self.before = os.getcwd()

    def test_rc_zero_gives_false(self):
        self.assertIs(LB.base_run_red(self.tree, self.tp, None, sys.executable, _runner(0)), False)
        self.assertEqual(os.getcwd(), self.before)

    def test_rc_one_gives_true(self):
        self.assertIs(LB.base_run_red(self.tree, self.tp, None, sys.executable, _runner(1)), True)
        self.assertEqual(os.getcwd(), self.before)

    def test_timeout_gives_none(self):
        def boom(argv, env=None):
            raise LB.subprocess.TimeoutExpired(argv, 1)
        self.assertIsNone(LB.base_run_red(self.tree, self.tp, None, sys.executable, boom))
        self.assertEqual(os.getcwd(), self.before)

    def test_oserror_gives_none(self):
        def boom(argv, env=None):
            raise OSError("no")
        self.assertIsNone(LB.base_run_red(self.tree, self.tp, None, sys.executable, boom))
        self.assertEqual(os.getcwd(), self.before)

    def test_absent_file_gives_none(self):
        self.assertIsNone(LB.base_run_red(self.tree, "scripts/test_missing.py", None, sys.executable, _runner(0)))
        self.assertEqual(os.getcwd(), self.before)

    def test_non_int_rc_gives_none(self):
        for rc in (None, True, "0"):
            with self.subTest(rc=rc):
                self.assertIsNone(LB.base_run_red(self.tree, self.tp, None, sys.executable, _runner(rc)))
                self.assertEqual(os.getcwd(), self.before)

    def test_none_or_int_tp_gives_none(self):
        for tp in (None, 5):
            with self.subTest(tp=tp):
                self.assertIsNone(LB.base_run_red(self.tree, tp, None, sys.executable, _runner(0)))
                self.assertEqual(os.getcwd(), self.before)

    def test_none_or_int_py_gives_none(self):
        for py in (None, 5):
            with self.subTest(py=py):
                self.assertIsNone(LB.base_run_red(self.tree, self.tp, None, py, _runner(0)))
                self.assertEqual(os.getcwd(), self.before)

    def test_none_runner_gives_none(self):
        self.assertIsNone(LB.base_run_red(self.tree, self.tp, None, sys.executable, None))
        self.assertEqual(os.getcwd(), self.before)

    def test_none_exists_gives_none(self):
        self.assertIsNone(LB.base_run_red(self.tree, self.tp, None, sys.executable, _runner(0), exists=None))
        self.assertEqual(os.getcwd(), self.before)


class BaseTree(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="h1b-tree-")
        self.addCleanup(self.tmp.cleanup)
        self.root = self.tmp.name

    def test_failing_read_tree_gives_none(self):
        self.assertIsNone(LB.base_tree(_runner(1), {}, tmp_root=self.root))
        self.assertEqual(os.listdir(self.root), [])

    def test_raising_runner_gives_none(self):
        def boom(argv, env=None):
            raise OSError("no")
        self.assertIsNone(LB.base_tree(boom, {}, tmp_root=self.root))
        self.assertEqual(os.listdir(self.root), [])

    def test_none_runner_gives_none(self):
        self.assertIsNone(LB.base_tree(None, {}, tmp_root=self.root))
        self.assertEqual(os.listdir(self.root), [])

    def test_tmp_root_that_is_a_file_gives_none(self):
        f = os.path.join(self.root, "a-file")
        with open(f, "w", encoding="utf-8") as fh:
            fh.write("x")
        self.assertIsNone(LB.base_tree(_runner(0), {}, tmp_root=f))
        self.assertTrue(os.path.isfile(f))

    def test_success_returns_existing_dir_and_keeps_paths_out_of_argv(self):
        seen = []

        def record(argv, env=None):
            seen.append((list(argv), dict(env) if env is not None else {}))
            return _Result(0)

        original = {"FOO": "bar"}
        out = LB.base_tree(record, original, tmp_root=self.root)
        self.assertIsNotNone(out)
        self.assertTrue(os.path.isdir(out))
        self.assertTrue(os.path.basename(out).startswith("land-base-"))
        self.assertEqual(len(seen), 2)
        for argv, genv in seen:
            for word in argv:
                self.assertNotIn(out, word)
            self.assertIn("GIT_INDEX_FILE", genv)
            self.assertIn("GIT_WORK_TREE", genv)
        self.assertNotIn("GIT_INDEX_FILE", original)
        self.assertNotIn("GIT_WORK_TREE", original)


class DropBaseTree(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="h1b-drop-")
        self.addCleanup(self.tmp.cleanup)
        self.root = self.tmp.name

    def test_removes_land_base_dir(self):
        d = os.path.join(self.root, "land-base-abc")
        os.makedirs(os.path.join(d, "sub"))
        with open(os.path.join(d, "f.txt"), "w", encoding="utf-8") as fh:
            fh.write("x")
        with open(os.path.join(d, "sub", "g.txt"), "w", encoding="utf-8") as fh:
            fh.write("y")
        self.assertTrue(LB.drop_base_tree(d))
        self.assertFalse(os.path.exists(d))

    def test_removes_a_link_to_a_directory_and_never_its_target(self):
        # the tree ships bundle/.antigravity-plugin/skills -> ../skills: os.walk lists it as a dir, rmdir refused it,
        # and every frozen copy leaked (70 copies, RB stopped on disk, 2026-10-05)
        outside = os.path.join(self.root, "outside")
        os.makedirs(outside)
        with open(os.path.join(outside, "keep.txt"), "w", encoding="utf-8") as fh:
            fh.write("k")
        d = os.path.join(self.root, "land-base-lnk")
        os.makedirs(os.path.join(d, "skills"))
        os.symlink("../skills", os.path.join(d, "inner-link"))
        os.symlink(outside, os.path.join(d, "outer-link"))
        self.assertTrue(LB.drop_base_tree(d))
        self.assertFalse(os.path.lexists(d))
        self.assertTrue(os.path.isfile(os.path.join(outside, "keep.txt")))

    def test_refuses_other_name_and_leaves_it(self):
        d = os.path.join(self.root, "some-other-dir")
        os.makedirs(d)
        self.assertFalse(LB.drop_base_tree(d))
        self.assertTrue(os.path.isdir(d))

    def test_refuses_non_dir(self):
        f = os.path.join(self.root, "a-file")
        with open(f, "w", encoding="utf-8") as fh:
            fh.write("x")
        self.assertFalse(LB.drop_base_tree(f))
        self.assertTrue(os.path.isfile(f))

    def test_refuses_non_str(self):
        for bad in (None, 5, [], {}, 1.5):
            with self.subTest(bad=bad):
                self.assertFalse(LB.drop_base_tree(bad))


class GatesTextCases(unittest.TestCase):
    def test_empty_held_is_exactly_the_old_text(self):
        self.assertEqual(LB.gates_text([], []), "green")
        self.assertEqual(LB.gates_text(["a", "b"], []), "RED: a, b")

    def test_held_only_says_held_and_never_green(self):
        out = LB.gates_text([], ["D1.1"])
        self.assertTrue(out.startswith("HELD: "))
        self.assertIn("D1.1", out)
        self.assertNotIn("green", out)

    def test_both_show_red_and_held(self):
        out = LB.gates_text(["a"], ["D1.1"])
        self.assertIn("RED: a", out)
        self.assertIn("HELD", out)
        self.assertIn("D1.1", out)

    def test_hostile_inputs_refused_never_green(self):
        self.assertEqual(LB.gates_text(None, []), "NO-DATA")
        self.assertEqual(LB.gates_text([], None), "NO-DATA")
        self.assertEqual(LB.gates_text("x", []), "NO-DATA")
        self.assertEqual(LB.gates_text([], 5), "NO-DATA")
        self.assertEqual(LB.gates_text([1], []), "NO-DATA")
        self.assertEqual(LB.gates_text([], [1]), "NO-DATA")

    def test_held_reason_empty(self):
        self.assertEqual(LB.held_reason([]), "")

    def test_held_reason_names_the_words(self):
        out = LB.held_reason([("D1.1", "INHERITED"), ("D1.2", "NO-DATA")])
        self.assertIn("D1.1", out)
        self.assertIn("INHERITED", out)
        self.assertIn("D1.2", out)
        self.assertIn("NO-DATA", out)
        self.assertIn("neither landed nor quarantined", out)

    def test_held_reason_refuses_hostile_never_the_empty_safe_case(self):
        for bad in (None, 5, "x", [1, 2, 3], [None], [("a",)], [("a", 5)], [(5, "INHERITED")]):
            with self.subTest(bad=bad):
                out = LB.held_reason(bad)
                self.assertNotEqual(out, "")
                self.assertTrue(out.startswith("NO-DATA"))


class Wiring(unittest.TestCase):
    def test_fifth_gate_is_wired(self):
        with open(os.path.join(HERE, "land_batch.py"), encoding="utf-8") as fh:
            text = fh.read()
        at = text.find("def main(")
        self.assertGreater(at, 0)
        body = text[at:]
        self.assertIn("neighbour_verdict(", body)
        self.assertIn("base_run_red(", body)
        self.assertEqual(text.count("print(unwind(head, "), 2)
        self.assertIn('if verdict in ("NOT-LANDED", "DIVERGED"):', text)
        self.assertIn("print(landed_line(", text)


class ExpectedUpstream(unittest.TestCase):
    """The pair pins the upstream it launched with; every push mode refuses a different one (Astra review 2026-10-05)."""

    def test_unset_keeps_todays_behaviour(self):
        self.assertEqual(LB.expected_upstream_refusal("hub", "loop/run-2026-09-30", env={}), "")

    def test_the_pinned_upstream_is_allowed(self):
        env = {"BROTHER_EXPECTED_UPSTREAM": "hub/practice/proof-2026-10-06"}
        self.assertEqual(LB.expected_upstream_refusal("hub", "practice/proof-2026-10-06", env=env), "")

    def test_the_release_line_is_refused_when_practice_is_pinned(self):
        env = {"BROTHER_EXPECTED_UPSTREAM": "hub/practice/proof-2026-10-06"}
        why = LB.expected_upstream_refusal("hub", "loop/run-2026-09-30", env=env)
        self.assertTrue(why.startswith("REFUSED:"), why)

    def test_main_asks_before_any_push_mode(self):
        # the guard sits right after the remote check and before the --push, --close and landing branches of main()
        import ast
        tree = ast.parse(open(LB.__file__, encoding="utf-8").read())
        main = next(n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == "main")
        src = ast.get_source_segment(open(LB.__file__, encoding="utf-8").read(), main)
        guard = src.find("expected_upstream_refusal(REMOTE, BRANCH)")
        self.assertGreater(guard, 0)
        for mode in ("reconcile_push(", "reconcile_close(", 'sh(["git", "fetch"'):
            self.assertGreater(src.find(mode), guard, mode)


if __name__ == "__main__":
    unittest.main(verbosity=2)

#!/usr/bin/env python3
"""LO1.a: the merge gate runs the owns completeness check on the tree it verifies, before the gate child.

A refusal starts no gate child and writes no ledger row; only exit 0 with a PASS: line and no SKIPPED line passes.
"""
import contextlib
import io
import os
import sys
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.insert(0, HERE)

import merge_gate  # noqa: E402
from test_merge_gate import GATE_PATH, TREE, GateCase, World  # noqa: E402

PASS_LINE = "PASS: unit BL names 3 component(s); every one exists and every one is committed"


class OwnsWorld(World):
    """Records every call as (argv head, cwd, env) in order."""

    def __init__(self):
        super().__init__()
        self.calls = []

    def __call__(self, argv, cwd=None, env=None):
        self.calls.append((argv[0], cwd, dict(env) if env is not None else None, list(argv)))
        return super().__call__(argv, cwd=cwd, env=env)

    def heads(self):
        return [c[0] for c in self.calls if c[0] in ("python3", "sh")]


class RaisingWorld(OwnsWorld):
    def __call__(self, argv, cwd=None, env=None):
        if argv[0] == "python3":
            raise OSError("no such file: python3")
        return super().__call__(argv, cwd=cwd, env=env)


class TestLO1A(GateCase):
    def setUp(self):
        super().setUp()
        self.world = OwnsWorld()
        self.wt = os.path.join(self.tmp, "wt with space")
        os.makedirs(os.path.join(self.wt, "scripts"))
        with open(os.path.join(self.wt, GATE_PATH), "wb") as handle:
            handle.write(self.world.main[GATE_PATH])
        self.world.candidate(TREE)

    def gate(self, env=None):
        return merge_gate.run_gate(self.clone, self.wt, TREE, "12", "h" * 40, "base-sha",
                                   self.env if env is None else env, runner=self.world)

    def rows(self):
        path = self.ledger()
        return merge_gate.read_ledger(path) if os.path.exists(path) else []

    def refused(self, needle):
        with self.assertRaises(merge_gate.MergeNoData) as ctx:
            self.gate()
        self.assertIn(needle, str(ctx.exception))
        self.assertNotIn("sh", self.world.heads(), "the gate child ran after a refusal")
        self.assertEqual(self.rows(), [], "a row was written for a refused tree")
        return str(ctx.exception)

    def test_the_owns_check_runs_in_the_worktree_before_the_gate(self) -> None:
        self.gate()
        self.assertEqual(self.world.heads(), ["python3", "sh"])
        owns = [c for c in self.world.calls if c[0] == "python3"][0]
        self.assertEqual(owns[1], self.wt)
        self.assertEqual(owns[3], ["python3", "-B", merge_gate.OWNS_SCRIPT])

    def test_a_passing_owns_check_lets_the_gate_run_and_record_a_row(self) -> None:
        row = self.gate()
        self.assertEqual(row["rc"], 0)
        self.assertEqual(len(self.rows()), 1)

    def test_an_unowned_loop_file_refuses_before_the_gate_and_writes_no_row(self) -> None:
        self.world.owns_code = 1
        self.world.owns_out = ("FAIL unowned      scripts/loop/new_tool.py   exists in the tree, unit BL does not name it\n"
                               "\n1 unowned, 0 absent, 0 uncommitted. The board's claim about brother.loop is not true.\n")
        msg = self.refused("OWNS-FAIL")
        self.assertIn("scripts/loop/new_tool.py", msg)

    def test_a_skipped_exit_zero_is_refused_as_no_data(self) -> None:
        self.world.owns_out = "SKIPPED: docs/plan/X.json is not in this tree, so there is nothing to judge here.\n"
        self.refused("OWNS-NO-DATA")
        self.world.calls = []
        self.world.owns_out = PASS_LINE + "\nSKIPPED: something\n"
        self.refused("OWNS-NO-DATA")

    def test_exit_zero_with_no_pass_line_is_refused(self) -> None:
        self.world.owns_out = ""
        self.refused("OWNS-NO-DATA")
        self.world.calls = []
        self.world.owns_out = "the plan was read\n"
        self.refused("no PASS: line")

    def test_exit_two_is_no_data_and_any_other_exit_is_a_fail(self) -> None:
        for code in (2, 127, 129, 137, -9):
            self.world.calls = []
            self.world.owns_code = code
            self.world.owns_out = PASS_LINE + "\n"
            self.refused("exited %d" % code)

    def test_a_runner_that_cannot_start_python_is_refused(self) -> None:
        self.world = RaisingWorld()
        self.world.candidate(TREE)
        self.refused("could not be started")

    def test_the_owns_check_gets_only_the_allowlisted_environment(self) -> None:
        self.gate({"PATH": "/usr/bin", "HOME": "/h", "GIT_DIR": "/g", "MERGE_GATE_TEST": "1", "CUSTOM": "1"})
        owns = [c for c in self.world.calls if c[0] == "python3"][0]
        self.assertEqual(owns[2], {"PATH": "/usr/bin", "HOME": "/h"})
        self.assertTrue(set(owns[2]) <= set(merge_gate.CHILD_ENV_ALLOW))

    def test_owns_verdict_table(self) -> None:
        v = merge_gate.owns_verdict
        self.assertEqual(v(0, PASS_LINE + "\n"), ("PASS", PASS_LINE))
        self.assertEqual(v(0, PASS_LINE + "\r\n")[0], "PASS")
        self.assertEqual(v(0, "note: SKIPPED nothing\n" + PASS_LINE)[0], "PASS")  # only a line STARTING SKIPPED
        self.assertEqual(v(0, PASS_LINE + "\nSKIPPED: x")[0], "NO-DATA")
        self.assertEqual(v(0, "")[0], "NO-DATA")
        self.assertEqual(v(1, PASS_LINE)[0], "FAIL")
        many = "".join("FAIL unowned scripts/loop/f%d.py\n" % i for i in range(200000))
        verdict, text = v(1, many)
        self.assertEqual(verdict, "FAIL")
        self.assertEqual(text.count("FAIL unowned"), 3)
        self.assertIn("199997 more", text)
        self.assertEqual(v(2, PASS_LINE)[0], "NO-DATA")
        self.assertEqual(v(True, PASS_LINE)[0], "NO-DATA")
        self.assertEqual(v(0, None)[0], "NO-DATA")

    def test_the_gate_command_prints_the_refusal_and_exits_two(self) -> None:
        self.world.owns_code = 1
        self.world.owns_out = "FAIL unowned      scripts/loop/x.py\n"
        err = io.StringIO()
        with contextlib.redirect_stderr(err):
            rc = merge_gate._gate_main([self.clone, self.wt, TREE, "12", "h" * 40, "base-sha"], self.world)
        self.assertEqual(rc, 2)
        self.assertTrue(err.getvalue().startswith("NO-DATA: OWNS-FAIL"), err.getvalue())
        self.assertEqual(self.rows(), [])


if __name__ == "__main__":
    unittest.main()

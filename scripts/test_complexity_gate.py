#!/usr/bin/env python3
"""Regression test for scripts/complexity_gate.py. Python 3.9 floor, stdlib only."""

import subprocess
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from complexity_gate import check_dispatch_plan, main  # noqa: E402

GATE = str(Path(__file__).resolve().parent / "complexity_gate.py")


class TestCheckDispatchPlan(unittest.TestCase):
    def test_below_threshold_no_graph_allowed(self):
        allowed, message = check_dispatch_plan(2, False, "")
        self.assertTrue(allowed)
        self.assertTrue(message)

    def test_four_agents_no_graph_refused(self):
        allowed, message = check_dispatch_plan(4, False, "")
        self.assertFalse(allowed)
        self.assertIn("dependency graph", message)

    def test_four_agents_graph_named_real_reason_allowed(self):
        allowed, message = check_dispatch_plan(
            4, True, "5 independent files, disjoint write sets, see docs/plan/X.json"
        )
        self.assertTrue(allowed)
        self.assertTrue(message)

    def test_four_agents_graph_named_empty_reason_refused(self):
        allowed, message = check_dispatch_plan(4, True, "   ")
        self.assertFalse(allowed, "a technically-True flag with no real reason must not pass")
        self.assertIn("no real reason", message)

    def test_four_agents_graph_named_placeholder_reason_refused(self):
        allowed, message = check_dispatch_plan(4, True, "n/a")
        self.assertFalse(allowed, "a placeholder reason must not count as a real one")

    def test_large_agent_count_needs_both(self):
        allowed, _ = check_dispatch_plan(12, True, "")
        self.assertFalse(allowed)
        allowed, _ = check_dispatch_plan(12, False, "real reason here")
        self.assertFalse(allowed)


class TestCLI(unittest.TestCase):
    def test_cli_refuses_five_agents_no_flags(self):
        result = subprocess.run(
            [sys.executable, GATE, "--agents", "5"],
            capture_output=True,
            text=True,
        )
        self.assertEqual(result.returncode, 1)
        self.assertIn("refused", result.stdout)

    def test_cli_allows_five_agents_with_graph_and_reason(self):
        result = subprocess.run(
            [
                sys.executable,
                GATE,
                "--agents",
                "5",
                "--graph-named",
                "--reason",
                "5 independent files, disjoint write sets, see docs/plan/X.json",
            ],
            capture_output=True,
            text=True,
        )
        self.assertEqual(result.returncode, 0)
        self.assertIn("allowed", result.stdout.lower())

    def test_main_function_directly(self):
        code = main(["--agents", "3"])
        self.assertEqual(code, 0)


if __name__ == "__main__":
    unittest.main()

#!/usr/bin/env python3
"""Tests for vault_promotion_policy.py (WBS-60.04)."""
import os
import subprocess
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import vault_promotion_policy as VPP

SCRIPT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "vault_promotion_policy.py")


class DecideTests(unittest.TestCase):
    def test_every_preferred_ground_alone_promotes(self):
        for ground in VPP.PREFERRED_GROUNDS:
            verdict, _reason = VPP.decide([ground])
            self.assertEqual(verdict, VPP.PROMOTE, ground)

    def test_every_avoid_ground_alone_refuses(self):
        for ground in VPP.AVOID_GROUNDS:
            verdict, _reason = VPP.decide([ground])
            self.assertEqual(verdict, VPP.REFUSE, ground)

    def test_multiple_preferred_grounds_promote(self):
        verdict, _reason = VPP.decide(list(VPP.PREFERRED_GROUNDS))
        self.assertEqual(verdict, VPP.PROMOTE)

    def test_no_grounds_refuses(self):
        verdict, _reason = VPP.decide([])
        self.assertEqual(verdict, VPP.REFUSE)

    def test_none_grounds_refuses(self):
        verdict, _reason = VPP.decide(None)
        self.assertEqual(verdict, VPP.REFUSE)

    def test_preferred_and_avoid_together_refuses(self):
        verdict, reason = VPP.decide(["resolved_claim", "model_speculation"])
        self.assertEqual(verdict, VPP.REFUSE)
        self.assertIn("model_speculation", reason)

    def test_avoid_wins_regardless_of_order(self):
        v1, _ = VPP.decide(["model_speculation", "resolved_claim"])
        v2, _ = VPP.decide(["resolved_claim", "model_speculation"])
        self.assertEqual(v1, VPP.REFUSE)
        self.assertEqual(v2, VPP.REFUSE)

    def test_unrecognized_ground_is_no_data(self):
        verdict, _reason = VPP.decide(["it_felt_right"])
        self.assertEqual(verdict, VPP.NO_DATA)

    def test_unrecognized_ground_alongside_preferred_is_still_no_data(self):
        verdict, _reason = VPP.decide(["resolved_claim", "it_felt_right"])
        self.assertEqual(verdict, VPP.NO_DATA)

    def test_reason_names_the_preferred_grounds_on_promote(self):
        _verdict, reason = VPP.decide(["reproducible_fault_lab_case"])
        self.assertIn("reproducible_fault_lab_case", reason)


class SelftestAndCliTests(unittest.TestCase):
    def test_run_selftest_passes(self):
        self.assertTrue(VPP.run_selftest())

    def test_cli_selftest_exits_zero(self):
        result = subprocess.run(
            [sys.executable, "-B", SCRIPT, "--selftest"],
            capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn("PASS", result.stdout)

    def test_cli_promote_exits_zero(self):
        result = subprocess.run(
            [sys.executable, "-B", SCRIPT, "--grounds", '["resolved_claim"]'],
            capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertTrue(result.stdout.startswith("PROMOTE:"))

    def test_cli_refuse_exits_one(self):
        result = subprocess.run(
            [sys.executable, "-B", SCRIPT, "--grounds", '["model_speculation"]'],
            capture_output=True, text=True)
        self.assertEqual(result.returncode, 1, result.stdout + result.stderr)

    def test_cli_no_data_exits_two(self):
        result = subprocess.run(
            [sys.executable, "-B", SCRIPT, "--grounds", '["nonsense"]'],
            capture_output=True, text=True)
        self.assertEqual(result.returncode, 2, result.stdout + result.stderr)

    def test_cli_bad_json_is_no_data(self):
        result = subprocess.run(
            [sys.executable, "-B", SCRIPT, "--grounds", "not json"],
            capture_output=True, text=True)
        self.assertEqual(result.returncode, 2, result.stdout + result.stderr)

    def test_cli_non_list_grounds_is_no_data(self):
        result = subprocess.run(
            [sys.executable, "-B", SCRIPT, "--grounds", '"resolved_claim"'],
            capture_output=True, text=True)
        self.assertEqual(result.returncode, 2, result.stdout + result.stderr)

    def test_cli_no_args_errors(self):
        result = subprocess.run(
            [sys.executable, "-B", SCRIPT], capture_output=True, text=True)
        self.assertNotEqual(result.returncode, 0)


if __name__ == "__main__":
    unittest.main()

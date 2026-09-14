#!/usr/bin/env python3
"""Tests for vertical_to_core.py (WBS-60.05)."""
import json
import os
import subprocess
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import vertical_to_core as VTC

SCRIPT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "vertical_to_core.py")


class AskTests(unittest.TestCase):
    def test_the_two_seeded_roadmap_examples(self):
        self.assertEqual(
            VTC.ask("mobile", "stale_build_artifact"),
            ("MAPPED", "core_artifact_identity"))
        self.assertEqual(
            VTC.ask("mdm", "high_consequence_merge_without_rollback"),
            ("MAPPED", "core_reversibility"))

    def test_unmapped_lesson_is_no_data_not_fabricated(self):
        verdict, reason = VTC.ask("mobile", "never_seen_before")
        self.assertEqual(verdict, "NO-DATA")
        self.assertIn("never_seen_before", reason)

    def test_domain_outside_mobile_mdm_is_no_data(self):
        verdict, _reason = VTC.ask("brotherds", "anything")
        self.assertEqual(verdict, "NO-DATA")
        verdict, _reason = VTC.ask("core", "anything")
        self.assertEqual(verdict, "NO-DATA")

    def test_default_registry_is_the_seed_and_is_not_mutated_by_ask(self):
        before = dict(VTC._SEED_MAPPINGS)
        VTC.ask("mobile", "stale_build_artifact")
        self.assertEqual(VTC._SEED_MAPPINGS, before)


class RegisterTests(unittest.TestCase):
    def setUp(self):
        self.registry = VTC.new_registry()

    def test_new_registry_is_independent_of_the_seed(self):
        self.registry[("mobile", "x")] = "y"
        self.assertNotIn(("mobile", "x"), VTC._SEED_MAPPINGS)

    def test_register_a_new_mapping_succeeds(self):
        problems = VTC.register(self.registry, "mobile", "locale_not_reset", "core_test_isolation")
        self.assertEqual(problems, [])
        self.assertEqual(
            VTC.ask("mobile", "locale_not_reset", registry=self.registry),
            ("MAPPED", "core_test_isolation"))

    def test_reregistering_the_identical_mapping_is_a_no_op(self):
        VTC.register(self.registry, "mobile", "x", "core_y")
        problems = VTC.register(self.registry, "mobile", "x", "core_y")
        self.assertEqual(problems, [])

    def test_conflicting_reregistration_is_refused_and_does_not_overwrite(self):
        VTC.register(self.registry, "mobile", "x", "core_y")
        problems = VTC.register(self.registry, "mobile", "x", "core_z")
        self.assertTrue(problems)
        self.assertEqual(self.registry[("mobile", "x")], "core_y")

    def test_register_outside_mobile_mdm_is_refused(self):
        problems = VTC.register(self.registry, "brotherds", "x", "y")
        self.assertTrue(problems)

    def test_register_empty_names_is_refused(self):
        self.assertTrue(VTC.register(self.registry, "mobile", "", "core_y"))
        self.assertTrue(VTC.register(self.registry, "mobile", "x", ""))


class SelftestAndCliTests(unittest.TestCase):
    def test_run_selftest_passes(self):
        self.assertTrue(VTC.run_selftest())

    def test_cli_selftest_exits_zero(self):
        result = subprocess.run(
            [sys.executable, "-B", SCRIPT, "--selftest"],
            capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn("PASS", result.stdout)

    def test_cli_mapped_exits_zero(self):
        result = subprocess.run(
            [sys.executable, "-B", SCRIPT, "--domain", "mobile",
             "--failure-mode", "stale_build_artifact"],
            capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        out = json.loads(result.stdout)
        self.assertEqual(out["verdict"], "MAPPED")
        self.assertEqual(out["payload"], "core_artifact_identity")

    def test_cli_unmapped_exits_two(self):
        result = subprocess.run(
            [sys.executable, "-B", SCRIPT, "--domain", "mobile",
             "--failure-mode", "nonsense"],
            capture_output=True, text=True)
        self.assertEqual(result.returncode, 2, result.stdout + result.stderr)

    def test_cli_invalid_domain_choice_errors(self):
        result = subprocess.run(
            [sys.executable, "-B", SCRIPT, "--domain", "ios",
             "--failure-mode", "x"],
            capture_output=True, text=True)
        self.assertNotEqual(result.returncode, 0)

    def test_cli_no_args_errors(self):
        result = subprocess.run(
            [sys.executable, "-B", SCRIPT], capture_output=True, text=True)
        self.assertNotEqual(result.returncode, 0)


if __name__ == "__main__":
    unittest.main()

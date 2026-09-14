#!/usr/bin/env python3
"""Tests for vault_retrieval_policy.py (WBS-60.02)."""
import json
import os
import subprocess
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import vault_retrieval_policy as VRP

SCRIPT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "vault_retrieval_policy.py")

FIXTURE = [
    {"id": "a", "symptom": "flaky-test"},
    {"id": "b", "symptom": "flaky-test"},
    {"id": "c", "symptom": "flaky-test"},
    {"id": "d", "symptom": "flaky-test"},
    {"id": "e", "component": "vault"},
    {"id": "f", "domain": "mobile"},
    {"id": "g", "failure_id": "F1"},
    {"id": "h"},
]


class RetrieveTests(unittest.TestCase):
    def test_caps_each_category_at_the_limit(self):
        sel = VRP.retrieve(FIXTURE, symptom="flaky-test", limit_per_category=3)
        self.assertEqual([it["id"] for it in sel["symptom"]], ["a", "b", "c"])

    def test_limit_of_one(self):
        sel = VRP.retrieve(FIXTURE, symptom="flaky-test", limit_per_category=1)
        self.assertEqual([it["id"] for it in sel["symptom"]], ["a"])

    def test_component_and_domain_and_prior_failure_categories(self):
        sel = VRP.retrieve(FIXTURE, component="vault", domain="mobile",
                            prior_failure_id="F1")
        self.assertEqual([it["id"] for it in sel["component"]], ["e"])
        self.assertEqual([it["id"] for it in sel["domain"]], ["f"])
        self.assertEqual([it["id"] for it in sel["prior_failure"]], ["g"])

    def test_item_matching_nothing_is_excluded(self):
        sel = VRP.retrieve(FIXTURE, symptom="flaky-test", component="vault",
                            domain="mobile", prior_failure_id="F1")
        all_ids = {it["id"] for items in sel.values() for it in items}
        self.assertNotIn("h", all_ids)

    def test_an_item_is_never_double_counted_across_categories(self):
        # "g" matches prior_failure only in the fixture; verify the
        # category-precedence order (prior_failure checked before symptom)
        # actually claims it once, not once per matching category.
        dual = [{"id": "x", "failure_id": "F1", "symptom": "flaky-test"}]
        sel = VRP.retrieve(dual, symptom="flaky-test", prior_failure_id="F1")
        total = sum(len(items) for items in sel.values())
        self.assertEqual(total, 1)
        self.assertEqual([it["id"] for it in sel["prior_failure"]], ["x"])
        self.assertEqual(sel["symptom"], [])

    def test_negative_limit_raises(self):
        with self.assertRaises(ValueError):
            VRP.retrieve(FIXTURE, symptom="flaky-test", limit_per_category=-1)

    def test_zero_candidates(self):
        sel = VRP.retrieve([], symptom="flaky-test")
        self.assertEqual(sel, {c: [] for c in VRP.CATEGORIES})


class CapsuleTests(unittest.TestCase):
    def test_build_capsule_sets_non_authoritative_true(self):
        capsule = VRP.build_capsule(FIXTURE, symptom="flaky-test")
        self.assertIs(capsule["non_authoritative"], True)

    def test_build_capsule_passes_check_capsule(self):
        capsule = VRP.build_capsule(FIXTURE, symptom="flaky-test")
        self.assertEqual(VRP.check_capsule(capsule), [])

    def test_missing_non_authoritative_is_refused(self):
        problems = VRP.check_capsule({"categories": {}})
        self.assertTrue(any("non_authoritative" in p for p in problems), problems)

    def test_truthy_but_not_true_is_refused(self):
        for bad in (1, "true", "True", [1]):
            with self.subTest(bad=bad):
                problems = VRP.check_capsule({"non_authoritative": bad, "categories": {}})
                self.assertTrue(problems)

    def test_false_is_refused(self):
        problems = VRP.check_capsule({"non_authoritative": False, "categories": {}})
        self.assertTrue(problems)

    def test_unknown_category_key_is_refused(self):
        problems = VRP.check_capsule({"non_authoritative": True, "categories": {"bogus": []}})
        self.assertTrue(any("bogus" in p for p in problems), problems)

    def test_categories_must_be_an_object(self):
        problems = VRP.check_capsule({"non_authoritative": True, "categories": []})
        self.assertTrue(problems)

    def test_non_dict_capsule_is_refused(self):
        self.assertTrue(VRP.check_capsule("not a dict"))


class SelftestAndCliTests(unittest.TestCase):
    def test_run_selftest_passes(self):
        self.assertTrue(VRP.run_selftest())

    def test_cli_selftest_exits_zero(self):
        result = subprocess.run(
            [sys.executable, "-B", SCRIPT, "--selftest"],
            capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn("PASS", result.stdout)

    def test_cli_reads_candidates_file_and_emits_capsule(self):
        tmp = tempfile.mkdtemp()
        try:
            path = os.path.join(tmp, "candidates.json")
            with open(path, "w", encoding="utf-8") as fh:
                json.dump(FIXTURE, fh)
            result = subprocess.run(
                [sys.executable, "-B", SCRIPT, "--candidates", path,
                 "--symptom", "flaky-test"],
                capture_output=True, text=True)
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            out = json.loads(result.stdout)
            self.assertIs(out["non_authoritative"], True)
            self.assertEqual([it["id"] for it in out["categories"]["symptom"]],
                              ["a", "b", "c"])
        finally:
            import shutil
            shutil.rmtree(tmp, ignore_errors=True)

    def test_cli_missing_candidates_file_is_no_data(self):
        result = subprocess.run(
            [sys.executable, "-B", SCRIPT, "--candidates", "/no/such/file.json"],
            capture_output=True, text=True)
        self.assertEqual(result.returncode, 2, result.stdout + result.stderr)
        self.assertIn("NO-DATA", result.stderr)

    def test_cli_no_args_errors(self):
        result = subprocess.run(
            [sys.executable, "-B", SCRIPT], capture_output=True, text=True)
        self.assertNotEqual(result.returncode, 0)


if __name__ == "__main__":
    unittest.main()

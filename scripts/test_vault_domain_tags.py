#!/usr/bin/env python3
"""Tests for vault_domain_tags.py (WBS-60.01)."""
import os
import shutil
import subprocess
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import vault_domain_tags as VDT

SCRIPT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "vault_domain_tags.py")


class ValidateTagsTests(unittest.TestCase):
    def test_single_domain_tag_is_valid(self):
        self.assertEqual(VDT.validate_tags({"domain_tags": ["mobile"]}), [])

    def test_every_domain_tag_alone_is_valid(self):
        for tag in VDT.DOMAIN_TAGS:
            self.assertEqual(VDT.validate_tags({"domain_tags": [tag]}), [], tag)

    def test_multiple_domain_tags_allowed(self):
        self.assertEqual(VDT.validate_tags({"domain_tags": ["core", "mobile"]}), [])

    def test_all_metadata_fields_together_are_valid(self):
        record = {"domain_tags": ["core"]}
        for field in VDT.METADATA_FIELDS:
            record[field] = "x"
        self.assertEqual(VDT.validate_tags(record), [])

    def test_empty_domain_tags_is_refused(self):
        problems = VDT.validate_tags({"domain_tags": []})
        self.assertTrue(any("domain_tags" in p for p in problems), problems)

    def test_missing_domain_tags_is_refused(self):
        problems = VDT.validate_tags({"component": "x"})
        self.assertTrue(any("domain_tags" in p for p in problems), problems)

    def test_unknown_domain_tag_is_refused(self):
        problems = VDT.validate_tags({"domain_tags": ["ios"]})
        self.assertTrue(any("ios" in p for p in problems), problems)

    def test_unlisted_field_is_refused(self):
        problems = VDT.validate_tags({"domain_tags": ["core"], "made_up": 1})
        self.assertTrue(any("made_up" in p for p in problems), problems)

    def test_non_dict_record_is_refused(self):
        problems = VDT.validate_tags(["not", "a", "dict"])
        self.assertTrue(problems)


class CheckNoSplitVaultTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)

    def test_no_sibling_directories_is_clean(self):
        vault = os.path.join(self.tmp, "Kay Vault")
        os.makedirs(vault)
        self.assertEqual(VDT.check_no_split_vault(vault), [])

    def test_ordinary_sibling_directory_is_not_flagged(self):
        vault = os.path.join(self.tmp, "Kay Vault")
        os.makedirs(vault)
        os.makedirs(os.path.join(self.tmp, "Documents"))
        self.assertEqual(VDT.check_no_split_vault(vault), [])

    def test_sibling_mobile_vault_is_caught(self):
        vault = os.path.join(self.tmp, "Kay Vault")
        os.makedirs(vault)
        os.makedirs(os.path.join(self.tmp, "Mobile Vault"))
        problems = VDT.check_no_split_vault(vault)
        self.assertEqual(len(problems), 1, problems)

    def test_case_and_separator_insensitive_match(self):
        vault = os.path.join(self.tmp, "Kay Vault")
        os.makedirs(vault)
        os.makedirs(os.path.join(self.tmp, "mdm_vault"))
        self.assertTrue(VDT.check_no_split_vault(vault))

    def test_every_forbidden_domain_name_is_caught(self):
        for domain in ("mobile", "mdm", "ds", "brotherds"):
            with self.subTest(domain=domain):
                tmp = tempfile.mkdtemp()
                try:
                    vault = os.path.join(tmp, "Kay Vault")
                    os.makedirs(vault)
                    os.makedirs(os.path.join(tmp, "%s-vault" % domain))
                    self.assertTrue(VDT.check_no_split_vault(vault))
                finally:
                    shutil.rmtree(tmp, ignore_errors=True)

    def test_vault_root_itself_named_forbidden_is_caught(self):
        problems = VDT.check_no_split_vault(os.path.join(self.tmp, "mdm-vault"))
        self.assertTrue(problems)

    def test_nonexistent_parent_is_no_problem(self):
        ghost = os.path.join(self.tmp, "nowhere", "Kay Vault")
        self.assertEqual(VDT.check_no_split_vault(ghost), [])

    def test_empty_vault_root_is_no_problem(self):
        self.assertEqual(VDT.check_no_split_vault(""), [])
        self.assertEqual(VDT.check_no_split_vault(None), [])


class SelftestAndCliTests(unittest.TestCase):
    def test_run_selftest_passes(self):
        self.assertTrue(VDT.run_selftest())

    def test_cli_selftest_exits_zero(self):
        result = subprocess.run(
            [sys.executable, "-B", SCRIPT, "--selftest"],
            capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn("PASS", result.stdout)

    def test_cli_tags_pass_and_fail(self):
        ok = subprocess.run(
            [sys.executable, "-B", SCRIPT, "--tags", '{"domain_tags": ["core"]}'],
            capture_output=True, text=True)
        self.assertEqual(ok.returncode, 0, ok.stdout + ok.stderr)

        bad = subprocess.run(
            [sys.executable, "-B", SCRIPT, "--tags", '{"domain_tags": ["ios"]}'],
            capture_output=True, text=True)
        self.assertEqual(bad.returncode, 1, bad.stdout + bad.stderr)

    def test_cli_no_args_errors(self):
        result = subprocess.run(
            [sys.executable, "-B", SCRIPT], capture_output=True, text=True)
        self.assertNotEqual(result.returncode, 0)


if __name__ == "__main__":
    unittest.main()

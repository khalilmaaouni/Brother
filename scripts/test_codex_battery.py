#!/usr/bin/env python3
"""The signed-in Codex battery scorer, pinned: its own selftest plus the two
portability rules (a receipt under a temp directory never passes B6; on a
tag after v1.0.8 an ABSENT brothermode@brother is the pass for B8)."""

import os
import subprocess
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

import codex_battery as cb  # noqa: E402


class SelftestRuns(unittest.TestCase):
    def test_selftest_exits_zero(self):
        proc = subprocess.run([sys.executable, os.path.join(HERE, "codex_battery.py"),
                               "--selftest"], capture_output=True, text=True)
        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)


class DurableReceipt(unittest.TestCase):
    def test_private_tmp_receipt_is_not_durable(self):
        self.assertTrue(cb.receipt_under_temp("/private/tmp/brother-add-x/receipt.json"))
        self.assertTrue(cb.receipt_under_temp(os.path.join(tempfile.gettempdir(), "r.json")))

    def test_home_receipt_is_durable(self):
        self.assertFalse(cb.receipt_under_temp(os.path.expanduser("~/.codex/brother/runs/r.json")))


class ReceiptProvesChange(unittest.TestCase):
    def good(self):
        entry = {"check_passed_before": False, "exit_code": 0, "state": "verified"}
        return {"scope": {"changed": [dict(entry, file="a.py"), dict(entry, file="b.py")]}}

    def test_two_verified_red_before_entries_pass(self):
        self.assertTrue(cb.receipt_proves_change(self.good()))

    def test_one_entry_is_not_enough(self):
        doc = self.good()
        doc["scope"]["changed"].pop()
        self.assertFalse(cb.receipt_proves_change(doc))

    def test_check_passed_before_true_fails(self):
        doc = self.good()
        doc["scope"]["changed"][0]["check_passed_before"] = True
        self.assertFalse(cb.receipt_proves_change(doc))

    def test_not_a_dict_fails(self):
        self.assertFalse(cb.receipt_proves_change(None))
        self.assertFalse(cb.receipt_proves_change([]))


class BrothermodeExpectation(unittest.TestCase):
    def test_tags_up_to_1_0_8_expect_present(self):
        for tag in ("v1.0.6", "v1.0.7", "v1.0.8"):
            self.assertIs(cb.expect_brothermode(tag), True, tag)

    def test_tags_after_1_0_8_expect_absent(self):
        for tag in ("v1.0.9", "v1.0.10", "v1.1.0"):
            self.assertIs(cb.expect_brothermode(tag), False, tag)

    def test_unknown_tag_is_none(self):
        self.assertIsNone(cb.expect_brothermode("nightly"))
        self.assertIsNone(cb.expect_brothermode(""))

    def test_absent_refusal_is_pass(self):
        v, _ = cb.classify_brothermode_add_absent(
            "Error: plugin brothermode@brother not found in marketplace", 1)
        self.assertEqual(v, "PASS")

    def test_absent_install_is_fail(self):
        v, _ = cb.classify_brothermode_add_absent(
            '{"pluginId": "brothermode@brother", "version": "3.4.4"}', 0)
        self.assertEqual(v, "FAIL")

    def test_list_with_brothermode_fails_when_absent_expected(self):
        body = '"pluginId": "brother@brother" "pluginId": "brothermode@brother"'
        self.assertEqual(cb.check_available_plugins(body, False)[0], "FAIL")
        self.assertEqual(cb.check_available_plugins('"pluginId": "brother@brother"', False)[0], "PASS")
        self.assertEqual(cb.check_available_plugins('"pluginId": "brother@brother"', None)[0], "NO-DATA")


if __name__ == "__main__":
    unittest.main()

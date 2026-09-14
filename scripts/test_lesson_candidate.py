#!/usr/bin/env python3
"""Tests for lesson_candidate.py (WBS-60.03)."""
import json
import os
import subprocess
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import contract_check as CC
import lesson_candidate as LC

SCRIPT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "lesson_candidate.py")


def rec(**overrides):
    r = dict(LC.BASE)
    r.update(overrides)
    return r


class SchemaShapeTests(unittest.TestCase):
    def setUp(self):
        self.schema = CC.load_json(LC.DEFAULT_SCHEMA, "schema")

    def test_a_fully_formed_record_passes(self):
        self.assertEqual(LC.check(rec(), self.schema), [])

    def test_mistaken_action_may_be_null(self):
        self.assertEqual(LC.check(rec(mistaken_action=None), self.schema), [])

    def test_every_affected_domain_alone_is_valid(self):
        for domain in ("core", "mobile", "mdm", "brotherds"):
            problems = LC.check(rec(affected_domain=domain), self.schema)
            self.assertEqual(problems, [], (domain, problems))

    def test_every_acceptance_status_alone_is_valid(self):
        for status in ("PENDING", "ACCEPTED", "REJECTED"):
            record = rec(acceptance_status=status)
            if status == "ACCEPTED":
                record["evidence_ids"] = ["ev-1"]
            problems = LC.check(record, self.schema)
            self.assertEqual(problems, [], (status, problems))

    def test_missing_required_field_is_refused(self):
        record = rec()
        del record["root_cause"]
        problems = LC.check(record, self.schema)
        self.assertTrue(any("root_cause" in p for p in problems), problems)

    def test_wrong_schema_version_is_refused(self):
        self.assertTrue(LC.check(rec(schema_version="wrong"), self.schema))

    def test_unknown_affected_domain_is_refused(self):
        problems = LC.check(rec(affected_domain="ios"), self.schema)
        self.assertTrue(problems)

    def test_unknown_acceptance_status_is_refused(self):
        problems = LC.check(rec(acceptance_status="MAYBE"), self.schema)
        self.assertTrue(problems)

    def test_generalizable_core_lesson_must_be_boolean(self):
        problems = LC.check(rec(generalizable_core_lesson="yes"), self.schema)
        self.assertTrue(problems)

    def test_additional_property_is_refused(self):
        record = rec()
        record["not_a_real_field"] = "x"
        problems = LC.check(record, self.schema)
        self.assertTrue(problems)

    def test_evidence_ids_must_be_an_array_of_strings(self):
        problems = LC.check(rec(evidence_ids="ev-1"), self.schema)
        self.assertTrue(problems)


class HandRuleTests(unittest.TestCase):
    def setUp(self):
        self.schema = CC.load_json(LC.DEFAULT_SCHEMA, "schema")

    def test_accepted_with_evidence_passes(self):
        record = rec(acceptance_status="ACCEPTED", evidence_ids=["ev-1"])
        self.assertEqual(LC.check(record, self.schema), [])

    def test_accepted_without_evidence_is_refused(self):
        record = rec(acceptance_status="ACCEPTED", evidence_ids=[])
        problems = LC.check(record, self.schema)
        self.assertTrue(any("evidence_ids" in p for p in problems), problems)

    def test_pending_without_evidence_is_fine(self):
        record = rec(acceptance_status="PENDING", evidence_ids=[])
        self.assertEqual(LC.check(record, self.schema), [])

    def test_rejected_without_evidence_is_fine(self):
        record = rec(acceptance_status="REJECTED", evidence_ids=[])
        self.assertEqual(LC.check(record, self.schema), [])

    def test_hand_rules_dedupe_with_schema_problems(self):
        # A record failing structurally AND the hand rule should not
        # duplicate any single problem string.
        record = rec(acceptance_status="ACCEPTED", evidence_ids=[])
        del record["root_cause"]
        problems = LC.check(record, self.schema)
        self.assertEqual(len(problems), len(set(problems)))


class SelftestAndCliTests(unittest.TestCase):
    def test_run_selftest_passes(self):
        self.assertTrue(LC.run_selftest())

    def test_cli_selftest_exits_zero(self):
        result = subprocess.run(
            [sys.executable, "-B", SCRIPT, "--selftest"],
            capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn("PASS", result.stdout)

    def test_cli_validates_a_file_on_disk(self):
        tmp = tempfile.mkdtemp()
        try:
            good = os.path.join(tmp, "good.json")
            with open(good, "w", encoding="utf-8") as fh:
                json.dump(rec(), fh)
            result = subprocess.run(
                [sys.executable, "-B", SCRIPT, good],
                capture_output=True, text=True)
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            self.assertIn("PASS", result.stdout)

            bad = os.path.join(tmp, "bad.json")
            with open(bad, "w", encoding="utf-8") as fh:
                json.dump(rec(affected_domain="ios"), fh)
            result = subprocess.run(
                [sys.executable, "-B", SCRIPT, bad],
                capture_output=True, text=True)
            self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
        finally:
            import shutil
            shutil.rmtree(tmp, ignore_errors=True)

    def test_cli_missing_file_is_no_data(self):
        result = subprocess.run(
            [sys.executable, "-B", SCRIPT, "/no/such/file.json"],
            capture_output=True, text=True)
        self.assertEqual(result.returncode, 2, result.stdout + result.stderr)

    def test_cli_no_args_errors(self):
        result = subprocess.run(
            [sys.executable, "-B", SCRIPT], capture_output=True, text=True)
        self.assertNotEqual(result.returncode, 0)


if __name__ == "__main__":
    unittest.main()

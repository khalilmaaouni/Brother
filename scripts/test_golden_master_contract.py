#!/usr/bin/env python3
"""Tests for golden_master_contract.py (WBS-40.01)."""
import json
import os
import sys
import tempfile
import unittest

sys.path.insert(0, __file__.rsplit("/", 1)[0])
import golden_master_contract as GMC
import contract_check as CC

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
REAL_OUTCOME_PATH = os.path.join(
    ROOT, "docs", "decisions", "convergence-1.0.17-baseline-2026-09-13.json")

VALID_OUTCOME = {
    "schema_version": "outcome-contract-v1", "project": {
        "project_id": "p", "name": "p",
        "provenance": {"project_id": "ask", "name": "ask"}},
    "language": "en", "question": "q", "success_checks": [
        {"id": "s", "command": "true", "expect": "exit-0"}],
    "must_answer": [], "affected_products": ["brother"], "ticket": None,
    "audit": {"required": False, "manifest": None}, "persona": "developer",
    "state": "contracted", "receipts": [], "questions": [], "history": [],
    "decision": None,
}

BASE_MASTER = {
    "schema_version": "golden-master-contract-v1", "master_id": "m1",
    "entity_type": "customer/location", "business_purpose": "x",
    "source_systems": ["s1"], "downstream_consumers": [],
    "critical_attributes": ["name"], "cardinality_constraints": [],
    "false_merge_cost_class": "high", "missed_match_cost_class": "medium",
    "review_policy": "human review above threshold", "merge_threshold": 0.9,
    "review_threshold": 0.5, "survivorship_policy_ref": "docs/x.md",
    "rollback_required": True, "quality_claim_ids": [],
    "locale_profiles": ["ja-JP"], "publish_authority": "data steward",
}


class GoldenMasterContractTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.outcome_path = os.path.join(self.tmp, "outcome.json")
        with open(self.outcome_path, "w") as fh:
            json.dump(VALID_OUTCOME, fh)
        self.schema = CC.load_json(GMC.DEFAULT_SCHEMA, "schema")

    def master(self, **overrides):
        rec = dict(BASE_MASTER, outcome_contract_ref=self.outcome_path)
        rec.update(overrides)
        return rec

    def test_a_valid_master_layered_on_a_valid_outcome_passes(self):
        self.assertEqual(GMC.check(self.master(), self.schema), [])

    def test_a_valid_master_layered_on_the_real_baseline_outcome_record_passes(self):
        # The synthetic fixture above proves the shape; this proves it
        # against a real outcome-contract-v1 record already living on this
        # branch, not only a hand-built stand-in.
        self.assertTrue(os.path.isfile(REAL_OUTCOME_PATH), REAL_OUTCOME_PATH)
        problems = GMC.check(self.master(outcome_contract_ref=REAL_OUTCOME_PATH), self.schema)
        self.assertEqual(problems, [])

    def test_missing_outcome_contract_ref_file_is_refused(self):
        problems = GMC.check(self.master(outcome_contract_ref="/no/such/file.json"), self.schema)
        self.assertTrue(any("not found" in p for p in problems), problems)

    def test_outcome_contract_ref_with_wrong_schema_version_is_refused(self):
        bad = dict(VALID_OUTCOME, schema_version="something-else-v1")
        path = os.path.join(self.tmp, "bad.json")
        with open(path, "w") as fh:
            json.dump(bad, fh)
        problems = GMC.check(self.master(outcome_contract_ref=path), self.schema)
        self.assertTrue(any("does not validate as outcome-contract-v1" in p for p in problems), problems)

    def test_outcome_contract_ref_that_fails_its_own_validation_is_refused(self):
        # A record claiming the right schema_version but genuinely broken
        # (missing a required field) must not be accepted just because the
        # version string matches -- the referenced record is re-validated,
        # not trusted on its own say-so.
        broken = dict(VALID_OUTCOME)
        del broken["ticket"]
        path = os.path.join(self.tmp, "broken.json")
        with open(path, "w") as fh:
            json.dump(broken, fh)
        problems = GMC.check(self.master(outcome_contract_ref=path), self.schema)
        self.assertTrue(any("fails its own outcome-contract-v1 validation" in p for p in problems), problems)

    def test_missing_required_master_field_is_refused(self):
        rec = self.master()
        del rec["critical_attributes"]
        problems = GMC.check(rec, self.schema)
        self.assertTrue(any("critical_attributes" in p for p in problems), problems)

    def test_empty_critical_attributes_is_refused_minitems(self):
        problems = GMC.check(self.master(critical_attributes=[]), self.schema)
        self.assertTrue(any("critical_attributes" in p for p in problems), problems)

    def test_wrong_schema_version_on_the_master_itself_is_refused(self):
        problems = GMC.check(self.master(schema_version="wrong"), self.schema)
        self.assertTrue(problems)

    def test_additional_property_is_refused(self):
        rec = self.master()
        rec["not_a_real_field"] = "x"
        problems = GMC.check(rec, self.schema)
        self.assertTrue(problems)

    def test_main_exits_0_on_pass_and_2_on_no_data(self):
        path = os.path.join(self.tmp, "m.json")
        with open(path, "w") as fh:
            json.dump(self.master(), fh)
        self.assertEqual(GMC.main([path]), 0)
        self.assertEqual(GMC.main(["/no/such/record.json"]), 2)

    def test_main_exits_1_on_fail(self):
        path = os.path.join(self.tmp, "m.json")
        rec = self.master()
        del rec["critical_attributes"]
        with open(path, "w") as fh:
            json.dump(rec, fh)
        self.assertEqual(GMC.main([path]), 1)


if __name__ == "__main__":
    unittest.main()

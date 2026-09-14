#!/usr/bin/env python3
"""Tests for mobile_journey_contract.py (WBS-30.01)."""
import json
import os
import sys
import tempfile
import unittest

sys.path.insert(0, __file__.rsplit("/", 1)[0])
import mobile_journey_contract as MJC
import contract_check as CC

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

BASE_JOURNEY = {
    "schema_version": "mobile-journey-contract-v1", "journey_id": "j1",
    "human_outcome": "x", "entry_state": "x", "exit_state": "x",
    "supported_device_classes": ["iphone"], "supported_os_range": "17-18",
    "locales": ["en"], "accessibility_obligations": [], "network_state_obligations": [],
    "interruption_obligations": [], "privacy_constraints": [], "performance_budgets": [],
    "visual_reference_ids": [], "required_native_tests": ["t1"],
    "human_acceptance_items": [], "post_release_claims": [],
}


class MobileJourneyContractTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.outcome_path = os.path.join(self.tmp, "outcome.json")
        with open(self.outcome_path, "w") as fh:
            json.dump(VALID_OUTCOME, fh)
        self.schema = CC.load_json(MJC.DEFAULT_SCHEMA, "schema")

    def journey(self, **overrides):
        rec = dict(BASE_JOURNEY, outcome_contract_ref=self.outcome_path)
        rec.update(overrides)
        return rec

    def test_a_valid_journey_layered_on_a_valid_outcome_passes(self):
        self.assertEqual(MJC.check(self.journey(), self.schema), [])

    def test_missing_outcome_contract_ref_file_is_refused(self):
        problems = MJC.check(self.journey(outcome_contract_ref="/no/such/file.json"), self.schema)
        self.assertTrue(any("not found" in p for p in problems), problems)

    def test_outcome_contract_ref_with_wrong_schema_version_is_refused(self):
        bad = dict(VALID_OUTCOME, schema_version="something-else-v1")
        path = os.path.join(self.tmp, "bad.json")
        with open(path, "w") as fh:
            json.dump(bad, fh)
        problems = MJC.check(self.journey(outcome_contract_ref=path), self.schema)
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
        problems = MJC.check(self.journey(outcome_contract_ref=path), self.schema)
        self.assertTrue(any("fails its own outcome-contract-v1 validation" in p for p in problems), problems)

    def test_missing_required_journey_field_is_refused(self):
        rec = self.journey()
        del rec["required_native_tests"]
        problems = MJC.check(rec, self.schema)
        self.assertTrue(any("required_native_tests" in p for p in problems), problems)

    def test_empty_required_native_tests_is_refused_minitems(self):
        problems = MJC.check(self.journey(required_native_tests=[]), self.schema)
        self.assertTrue(any("required_native_tests" in p for p in problems), problems)

    def test_wrong_schema_version_on_the_journey_itself_is_refused(self):
        problems = MJC.check(self.journey(schema_version="wrong"), self.schema)
        self.assertTrue(problems)

    def test_additional_property_is_refused(self):
        rec = self.journey()
        rec["not_a_real_field"] = "x"
        problems = MJC.check(rec, self.schema)
        self.assertTrue(problems)

    def test_main_exits_0_on_pass_and_2_on_no_data(self):
        path = os.path.join(self.tmp, "j.json")
        with open(path, "w") as fh:
            json.dump(self.journey(), fh)
        self.assertEqual(MJC.main([path]), 0)
        self.assertEqual(MJC.main(["/no/such/record.json"]), 2)

    def test_main_exits_1_on_fail(self):
        path = os.path.join(self.tmp, "j.json")
        rec = self.journey()
        del rec["required_native_tests"]
        with open(path, "w") as fh:
            json.dump(rec, fh)
        self.assertEqual(MJC.main([path]), 1)


if __name__ == "__main__":
    unittest.main()

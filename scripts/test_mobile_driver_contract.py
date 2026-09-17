#!/usr/bin/env python3
"""Tests for mobile_driver_contract.py (EPIC M3.01)."""
import json
import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import mobile_driver_contract as MDC  # noqa: E402
import contract_check as CC  # noqa: E402

BASE_RECORD = {
    "schema_version": "mobile-driver-contract-v1",
    "driver_id": "maestro-ios",
    "driver_name": "Maestro iOS Driver",
    "action_vocabulary_ref": "mobile-canonical-action-v1",
    "supported_actions": ["tap", "swipe"],
    "platforms": ["ios"],
    "device_modes": ["simulator", "real_device"],
    "remote_sessions_supported": False,
    "observations": ["screenshot", "semantic_tree"],
    "deterministic_selector_support": True,
    "visual_grounding_support": False,
    "risk_classes": [
        {"action": "tap", "risk_class": "safe"},
        {"action": "swipe", "risk_class": "reversible"},
    ],
}


class MobileDriverContractTests(unittest.TestCase):
    def setUp(self):
        self.schema = CC.load_json(MDC.DEFAULT_SCHEMA, "schema")

    def record(self, **overrides):
        rec = dict(BASE_RECORD)
        rec.update(overrides)
        if "risk_classes" not in overrides:
            rec["risk_classes"] = [dict(r) for r in BASE_RECORD["risk_classes"]]
        return rec

    def test_a_valid_record_passes(self):
        self.assertEqual(MDC.check(self.record(), self.schema), [])

    def test_missing_required_field_is_refused(self):
        rec = self.record()
        del rec["driver_id"]
        problems = MDC.check(rec, self.schema)
        self.assertTrue(any("driver_id" in p for p in problems), problems)

    def test_wrong_schema_version_is_refused(self):
        problems = MDC.check(self.record(schema_version="mobile-driver-contract-v0"), self.schema)
        self.assertTrue(problems)

    def test_unknown_top_level_field_is_refused(self):
        rec = self.record()
        rec["not_a_real_field"] = "x"
        problems = MDC.check(rec, self.schema)
        self.assertTrue(any("not_a_real_field" in p for p in problems), problems)

    def test_empty_supported_actions_is_refused_minitems(self):
        problems = MDC.check(self.record(supported_actions=[], risk_classes=[]), self.schema)
        self.assertTrue(any("supported_actions" in p for p in problems), problems)

    def test_risk_class_action_outside_supported_actions_is_refused(self):
        rec = self.record()
        rec["risk_classes"].append({"action": "kill_app", "risk_class": "destructive"})
        problems = MDC.check(rec, self.schema)
        self.assertTrue(any("kill_app" in p for p in problems), problems)

    def test_supported_action_without_risk_class_entry_is_refused(self):
        rec = self.record(supported_actions=["tap", "swipe", "long_press"])
        problems = MDC.check(rec, self.schema)
        self.assertTrue(any("long_press" in p for p in problems), problems)

    def test_duplicate_risk_class_entry_for_one_action_is_refused(self):
        rec = self.record()
        rec["risk_classes"].append({"action": "tap", "risk_class": "reversible"})
        problems = MDC.check(rec, self.schema)
        self.assertTrue(any("'tap'" in p and "2 risk_classes entries" in p for p in problems), problems)

    def test_hand_rules_does_not_crash_on_malformed_risk_classes(self):
        rec = self.record()
        rec["risk_classes"] = "not-a-list"
        # schema-level type check will also fire; the hand rule must not raise.
        problems = MDC.check(rec, self.schema)
        self.assertTrue(problems)

    def test_main_exits_0_on_pass_and_2_on_no_data(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "r.json")
            with open(path, "w", encoding="utf-8") as fh:
                json.dump(self.record(), fh)
            self.assertEqual(MDC.main([path]), 0)
            self.assertEqual(MDC.main([os.path.join(tmp, "missing.json")]), 2)

    def test_main_exits_1_on_fail(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "r.json")
            with open(path, "w", encoding="utf-8") as fh:
                json.dump(self.record(supported_actions=[], risk_classes=[]), fh)
            self.assertEqual(MDC.main([path]), 1)


if __name__ == "__main__":
    unittest.main()

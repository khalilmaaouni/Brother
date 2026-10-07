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
    "supported_actions": ["TAP_TARGET", "SWIPE"],
    "platforms": ["ios"],
    "device_modes": ["simulator", "real_device"],
    "remote_sessions_supported": False,
    "observations": ["screenshot", "semantic_tree"],
    "deterministic_selector_support": True,
    "visual_grounding_support": False,
    "risk_classes": [
        {"action": "TAP_TARGET", "risk_class": "safe"},
        {"action": "SWIPE", "risk_class": "reversible"},
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
        rec["risk_classes"].append({"action": "TAP_TARGET", "risk_class": "reversible"})
        problems = MDC.check(rec, self.schema)
        self.assertTrue(any("'TAP_TARGET'" in p and "2 risk_classes entries" in p for p in problems), problems)

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

    # CRITICAL 1: empty strings must not silently win router selection.
    def test_empty_driver_id_is_refused(self):
        problems = MDC.check(self.record(driver_id=""), self.schema)
        self.assertTrue(any("driver_id" in p and "empty" in p for p in problems), problems)

    def test_whitespace_only_driver_name_is_refused(self):
        problems = MDC.check(self.record(driver_name="   "), self.schema)
        self.assertTrue(any("driver_name" in p and "empty" in p for p in problems), problems)

    def test_empty_action_vocabulary_ref_is_refused(self):
        problems = MDC.check(self.record(action_vocabulary_ref=""), self.schema)
        self.assertTrue(any("action_vocabulary_ref" in p and "empty" in p for p in problems), problems)

    def test_empty_supported_action_entry_is_refused(self):
        rec = self.record(supported_actions=["TAP_TARGET", ""],
                           risk_classes=[{"action": "TAP_TARGET", "risk_class": "safe"},
                                         {"action": "", "risk_class": "safe"}])
        problems = MDC.check(rec, self.schema)
        self.assertTrue(any("supported_actions[1]" in p and "empty" in p for p in problems), problems)

    # CRITICAL 2: vocabulary membership, checked only when the ref resolves.
    def test_missing_vocabulary_file_passes_actions_unchecked(self):
        # action_vocabulary_ref names a vocabulary file that genuinely does
        # not exist on disk: NO-DATA, membership is not checked, and even a
        # typo'd action name still validates.
        rec = self.record(action_vocabulary_ref="nonexistent-vocab-v99",
                           supported_actions=["tap", "swipe"],
                           risk_classes=[{"action": "tap", "risk_class": "safe"},
                                         {"action": "swipe", "risk_class": "reversible"}])
        self.assertEqual(MDC.check(rec, self.schema), [])

    def test_vocabulary_without_action_enum_passes_actions_unchecked(self):
        # The ref resolves to a real file, but that file defines no
        # properties.action.enum: still NO-DATA, still skipped.
        with tempfile.TemporaryDirectory() as tmp:
            vocab_path = os.path.join(tmp, "vocab.json")
            with open(vocab_path, "w", encoding="utf-8") as fh:
                json.dump({"properties": {"action": {"type": "string"}}}, fh)
            rec = self.record(action_vocabulary_ref=vocab_path,
                               supported_actions=["tap", "swipe"],
                               risk_classes=[{"action": "tap", "risk_class": "safe"},
                                             {"action": "swipe", "risk_class": "reversible"}])
            self.assertEqual(MDC.check(rec, self.schema), [])

    def test_resolvable_vocabulary_with_typo_action_is_refused(self):
        with tempfile.TemporaryDirectory() as tmp:
            vocab_path = os.path.join(tmp, "vocab.json")
            with open(vocab_path, "w", encoding="utf-8") as fh:
                json.dump({"properties": {"action": {"enum": ["TAP_TARGET", "SWIPE"]}}}, fh)
            rec = self.record(action_vocabulary_ref=vocab_path,
                               supported_actions=["tap", "SWIPE"],
                               risk_classes=[{"action": "tap", "risk_class": "safe"},
                                             {"action": "SWIPE", "risk_class": "reversible"}])
            problems = MDC.check(rec, self.schema)
            self.assertTrue(any("'tap'" in p and "canonical action name" in p for p in problems), problems)
            self.assertFalse(any("'SWIPE'" in p and "canonical action name" in p for p in problems), problems)

    def test_resolvable_vocabulary_with_correct_names_passes(self):
        with tempfile.TemporaryDirectory() as tmp:
            vocab_path = os.path.join(tmp, "vocab.json")
            with open(vocab_path, "w", encoding="utf-8") as fh:
                json.dump({"properties": {"action": {"enum": ["TAP_TARGET", "SWIPE"]}}}, fh)
            rec = self.record(action_vocabulary_ref=vocab_path)
            self.assertEqual(MDC.check(rec, self.schema), [])

    # MAJOR 3: capability/observation coherence.
    def test_visual_grounding_without_screenshot_observation_is_refused(self):
        rec = self.record(visual_grounding_support=True, observations=["semantic_tree"])
        problems = MDC.check(rec, self.schema)
        self.assertTrue(any("visual_grounding_support" in p and "screenshot" in p for p in problems), problems)

    def test_both_capabilities_false_is_not_refused_for_a_non_target_driver(self):
        # Cross-checked against the real M3.03 native-iOS adapter: a driver
        # whose supported_actions are entirely non-target actions (no
        # TAP_TARGET/LONG_PRESS_TARGET/SCROLL_TO/ASSERT_VISIBLE) correctly
        # advertises both capabilities false. This must still validate.
        rec = self.record(supported_actions=["FINISH"],
                           risk_classes=[{"action": "FINISH", "risk_class": "safe"}],
                           deterministic_selector_support=False,
                           visual_grounding_support=False)
        self.assertEqual(MDC.check(rec, self.schema), [])

    # MAJOR 4: duplicate entries corrupt count-based router ranking.
    def test_duplicated_platform_is_refused(self):
        rec = self.record(platforms=["ios", "ios"])
        problems = MDC.check(rec, self.schema)
        self.assertTrue(any("platforms" in p and "duplicate" in p for p in problems), problems)

    def test_duplicated_observation_is_refused(self):
        rec = self.record(observations=["screenshot", "screenshot", "semantic_tree"])
        problems = MDC.check(rec, self.schema)
        self.assertTrue(any("observations" in p and "duplicate" in p for p in problems), problems)


if __name__ == "__main__":
    unittest.main()

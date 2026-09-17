#!/usr/bin/env python3
"""Tests for mobile_canonical_action.py (EPIC M3.02)."""
import contextlib
import io
import json
import os
import sys
import tempfile
import unittest

sys.path.insert(0, __file__.rsplit("/", 1)[0])
import mobile_canonical_action as MCA
import contract_check as CC

#: One valid record per action, each satisfying that action's own hand
#: rule, so every action name is exercised at least once.
VALID_BY_ACTION = {
    "OPEN_APP": {"params": {"app_id": "com.example.app"}},
    "TAP_TARGET": {"target": {"selector_type": "accessibility_id", "value": "loginButton"}},
    "LONG_PRESS_TARGET": {"target": {"selector_type": "text", "value": "Menu"}},
    "TYPE_TEXT": {"params": {"text": "hello"}},
    "SWIPE": {"params": {"direction": "up"}},
    "SCROLL_TO": {"target": {"selector_type": "testid", "value": "footer"}},
    "BACK": {},
    "HOME": {},
    "ROTATE": {"params": {"orientation": "landscape"}},
    "SET_PERMISSION": {"params": {"permission": "camera", "state": "allow"}},
    "SET_NETWORK": {"params": {"condition": "offline"}},
    "SET_LOCATION": {"params": {"latitude": 35.6, "longitude": 139.7}},
    "DEEPLINK": {"params": {"uri": "app://open/home"}},
    "WAIT_FOR": {"params": {"duration_ms": 500}},
    "ASSERT_VISIBLE": {"target": {"selector_type": "text", "value": "Welcome"}},
    "ASSERT_STATE": {"params": {"key": "screen", "expected": "home"}},
    "CAPTURE": {},
    "FINISH": {"params": {"status": "success"}},
    "NEED_HUMAN": {"reason": "ambiguous target, no stable selector found"},
    "IMPOSSIBLE": {"reason": "action requires a physical SIM, unavailable in this environment"},
}


def record_for(action, **overrides):
    rec = {"schema_version": "mobile-canonical-action-v1", "action_id": "a1", "action": action}
    rec.update(VALID_BY_ACTION[action])
    rec.update(overrides)
    return rec


class MobileCanonicalActionTests(unittest.TestCase):
    def setUp(self):
        self.schema = CC.load_json(MCA.DEFAULT_SCHEMA, "schema")

    def test_action_rules_covers_exactly_the_schema_enum(self):
        # The other direction of coverage: a verb added to the schema's enum
        # with no matching ACTION_RULES entry would validate green with no
        # hand rule ever applied to it.
        schema_actions = set(self.schema["properties"]["action"]["enum"])
        self.assertEqual(set(MCA.ACTION_RULES), schema_actions)

    def test_every_action_has_a_valid_record_that_passes(self):
        for action in MCA.ACTION_RULES:
            with self.subTest(action=action):
                self.assertEqual(MCA.check(record_for(action), self.schema), [],
                                 "action %s should validate" % action)

    def test_unknown_action_is_refused_by_the_enum(self):
        rec = record_for("TAP_TARGET")
        rec["action"] = "FLY_TO_THE_MOON"
        problems = MCA.check(rec, self.schema)
        self.assertTrue(problems)

    def test_missing_schema_version_is_refused(self):
        rec = record_for("BACK")
        del rec["schema_version"]
        problems = MCA.check(rec, self.schema)
        self.assertTrue(any("schema_version" in p for p in problems), problems)

    def test_wrong_schema_version_is_refused(self):
        problems = MCA.check(record_for("BACK", schema_version="something-else"), self.schema)
        self.assertTrue(problems)

    def test_additional_top_level_property_is_refused(self):
        rec = record_for("BACK")
        rec["not_a_real_field"] = "x"
        problems = MCA.check(rec, self.schema)
        self.assertTrue(problems)

    def test_target_with_unknown_selector_type_is_refused(self):
        rec = record_for("TAP_TARGET", target={"selector_type": "telepathy", "value": "x"})
        problems = MCA.check(rec, self.schema)
        self.assertTrue(problems)

    def test_target_missing_value_is_refused(self):
        rec = record_for("TAP_TARGET", target={"selector_type": "text"})
        problems = MCA.check(rec, self.schema)
        self.assertTrue(any("target" in p for p in problems), problems)

    # --- hand_rules: per-action required shape ---

    def test_tap_target_without_target_is_refused(self):
        rec = record_for("TAP_TARGET")
        del rec["target"]
        problems = MCA.check(rec, self.schema)
        self.assertTrue(any("TAP_TARGET" in p for p in problems), problems)

    def test_open_app_without_params_app_id_is_refused(self):
        rec = record_for("OPEN_APP", params={})
        problems = MCA.check(rec, self.schema)
        self.assertTrue(any("OPEN_APP" in p for p in problems), problems)

    def test_type_text_without_params_text_is_refused(self):
        rec = record_for("TYPE_TEXT", params={})
        problems = MCA.check(rec, self.schema)
        self.assertTrue(any("TYPE_TEXT" in p for p in problems), problems)

    def test_wait_for_with_neither_target_nor_duration_is_refused(self):
        rec = record_for("WAIT_FOR", params={})
        problems = MCA.check(rec, self.schema)
        self.assertTrue(any("WAIT_FOR" in p for p in problems), problems)

    def test_wait_for_with_only_target_is_accepted(self):
        rec = record_for("WAIT_FOR", params={},
                          target={"selector_type": "text", "value": "Loaded"})
        self.assertEqual(MCA.check(rec, self.schema), [])

    def test_need_human_without_reason_is_refused(self):
        rec = record_for("NEED_HUMAN")
        del rec["reason"]
        problems = MCA.check(rec, self.schema)
        self.assertTrue(any("NEED_HUMAN" in p for p in problems), problems)

    def test_need_human_with_blank_reason_is_refused(self):
        rec = record_for("NEED_HUMAN", reason="   ")
        problems = MCA.check(rec, self.schema)
        self.assertTrue(any("NEED_HUMAN" in p for p in problems), problems)

    def test_impossible_without_reason_is_refused(self):
        rec = record_for("IMPOSSIBLE")
        del rec["reason"]
        problems = MCA.check(rec, self.schema)
        self.assertTrue(any("IMPOSSIBLE" in p for p in problems), problems)

    def test_back_and_home_and_capture_need_nothing_extra(self):
        for action in ("BACK", "HOME", "CAPTURE"):
            with self.subTest(action=action):
                self.assertEqual(MCA.check(record_for(action), self.schema), [])

    # --- hand_rules: enum-constrained params values ---

    def test_swipe_direction_out_of_enum_is_refused(self):
        rec = record_for("SWIPE", params={"direction": "diagonally"})
        problems = MCA.check(rec, self.schema)
        self.assertTrue(any("params.direction" in p for p in problems), problems)

    def test_finish_status_out_of_enum_is_refused(self):
        rec = record_for("FINISH", params={"status": "maybe"})
        problems = MCA.check(rec, self.schema)
        self.assertTrue(any("params.status" in p for p in problems), problems)

    def test_set_permission_state_out_of_enum_is_refused(self):
        rec = record_for("SET_PERMISSION", params={"permission": "camera", "state": "sometimes"})
        problems = MCA.check(rec, self.schema)
        self.assertTrue(any("params.state" in p for p in problems), problems)

    # --- hand_rules: whitespace-only strings count as missing (adversarial
    # review finding: an exact `== ""` check let "   " slip through) ---

    def test_whitespace_only_params_value_is_refused(self):
        rec = record_for("OPEN_APP", params={"app_id": "   "})
        problems = MCA.check(rec, self.schema)
        self.assertTrue(any("OPEN_APP" in p for p in problems), problems)

    def test_whitespace_only_target_selector_type_is_refused(self):
        rec = record_for("TAP_TARGET", target={"selector_type": "  ", "value": "x"})
        problems = MCA.check(rec, self.schema)
        self.assertTrue(any("TAP_TARGET" in p for p in problems), problems)

    def test_whitespace_only_action_id_is_refused(self):
        rec = record_for("BACK", action_id="   ")
        problems = MCA.check(rec, self.schema)
        self.assertTrue(any("action_id" in p for p in problems), problems)

    def test_assert_state_expected_as_empty_string_is_refused(self):
        # Deliberate, not a bug: every required params/target string field is
        # held to the same non-blank rule (adversarial review raised this as
        # a possible false-reject for "assert equals empty string"; declined
        # as a special case -- that assertion needs its own explicit params
        # key if a later unit wants it, not an overloaded blank "expected").
        rec = record_for("ASSERT_STATE", params={"key": "banner_text", "expected": ""})
        problems = MCA.check(rec, self.schema)
        self.assertTrue(any("ASSERT_STATE" in p for p in problems), problems)

    # --- hand_rules: numeric range/type checks (adversarial review finding:
    # presence-only checks let non-numeric, out-of-range or bool values through) ---

    def test_set_location_latitude_out_of_range_is_refused(self):
        rec = record_for("SET_LOCATION", params={"latitude": 200, "longitude": 0})
        problems = MCA.check(rec, self.schema)
        self.assertTrue(any("params.latitude" in p for p in problems), problems)

    def test_set_location_non_numeric_longitude_is_refused(self):
        rec = record_for("SET_LOCATION", params={"latitude": 0, "longitude": "far"})
        problems = MCA.check(rec, self.schema)
        self.assertTrue(any("params.longitude" in p for p in problems), problems)

    def test_wait_for_duration_ms_zero_is_refused(self):
        rec = record_for("WAIT_FOR", params={"duration_ms": 0})
        problems = MCA.check(rec, self.schema)
        self.assertTrue(any("params.duration_ms" in p for p in problems), problems)

    def test_wait_for_duration_ms_bool_is_refused(self):
        # bool is an int subclass in Python; True must not pass as 1ms.
        rec = record_for("WAIT_FOR", params={"duration_ms": True})
        problems = MCA.check(rec, self.schema)
        self.assertTrue(any("params.duration_ms" in p for p in problems), problems)

    def test_coordinates_selector_with_malformed_value_is_refused(self):
        rec = record_for("TAP_TARGET", target={"selector_type": "coordinates", "value": "top-left"})
        problems = MCA.check(rec, self.schema)
        self.assertTrue(any("target.value" in p for p in problems), problems)

    def test_coordinates_selector_with_well_formed_value_is_accepted(self):
        rec = record_for("TAP_TARGET", target={"selector_type": "coordinates", "value": "120,480"})
        self.assertEqual(MCA.check(rec, self.schema), [])

    # --- coverage for the remaining target/params-required actions ---

    def test_long_press_target_without_target_is_refused(self):
        rec = record_for("LONG_PRESS_TARGET")
        del rec["target"]
        problems = MCA.check(rec, self.schema)
        self.assertTrue(any("LONG_PRESS_TARGET" in p for p in problems), problems)

    def test_scroll_to_without_target_is_refused(self):
        rec = record_for("SCROLL_TO")
        del rec["target"]
        problems = MCA.check(rec, self.schema)
        self.assertTrue(any("SCROLL_TO" in p for p in problems), problems)

    def test_assert_visible_without_target_is_refused(self):
        rec = record_for("ASSERT_VISIBLE")
        del rec["target"]
        problems = MCA.check(rec, self.schema)
        self.assertTrue(any("ASSERT_VISIBLE" in p for p in problems), problems)

    def test_rotate_without_orientation_is_refused(self):
        rec = record_for("ROTATE", params={})
        problems = MCA.check(rec, self.schema)
        self.assertTrue(any("ROTATE" in p for p in problems), problems)

    def test_set_network_without_condition_is_refused(self):
        rec = record_for("SET_NETWORK", params={})
        problems = MCA.check(rec, self.schema)
        self.assertTrue(any("SET_NETWORK" in p for p in problems), problems)

    def test_deeplink_without_uri_is_refused(self):
        rec = record_for("DEEPLINK", params={})
        problems = MCA.check(rec, self.schema)
        self.assertTrue(any("DEEPLINK" in p for p in problems), problems)

    def test_assert_state_without_key_is_refused(self):
        rec = record_for("ASSERT_STATE", params={"expected": "home"})
        problems = MCA.check(rec, self.schema)
        self.assertTrue(any("ASSERT_STATE" in p for p in problems), problems)

    def test_impossible_with_blank_reason_is_refused(self):
        rec = record_for("IMPOSSIBLE", reason="   ")
        problems = MCA.check(rec, self.schema)
        self.assertTrue(any("IMPOSSIBLE" in p for p in problems), problems)

    # --- crash guard: non-dict record bodies (adversarial review Major 1:
    # check() called hand_rules() unconditionally after CC.validate(), so a
    # list/string/null record body raised AttributeError instead of
    # returning a clean problem list) ---

    def test_list_record_returns_clean_problems_not_a_crash(self):
        problems = MCA.check([], self.schema)
        self.assertTrue(problems)

    def test_string_record_returns_clean_problems_not_a_crash(self):
        problems = MCA.check("hello", self.schema)
        self.assertTrue(problems)

    def test_null_record_returns_clean_problems_not_a_crash(self):
        problems = MCA.check(None, self.schema)
        self.assertTrue(problems)

    # --- hand_rules: params string type checks (adversarial review Major 2:
    # a presence-only check let a number/dict/list/bool through for every
    # params field with no schema `type` keyword of its own) ---

    def test_open_app_app_id_non_string_is_refused(self):
        rec = record_for("OPEN_APP", params={"app_id": 123})
        problems = MCA.check(rec, self.schema)
        self.assertTrue(any("params.app_id" in p for p in problems), problems)

    def test_type_text_text_dict_is_refused(self):
        rec = record_for("TYPE_TEXT", params={"text": {"a": 1}})
        problems = MCA.check(rec, self.schema)
        self.assertTrue(any("params.text" in p for p in problems), problems)

    def test_deeplink_uri_bool_is_refused(self):
        rec = record_for("DEEPLINK", params={"uri": True})
        problems = MCA.check(rec, self.schema)
        self.assertTrue(any("params.uri" in p for p in problems), problems)

    def test_set_permission_permission_non_string_is_refused(self):
        rec = record_for("SET_PERMISSION", params={"permission": 7, "state": "allow"})
        problems = MCA.check(rec, self.schema)
        self.assertTrue(any("params.permission" in p for p in problems), problems)

    def test_assert_state_key_list_is_refused(self):
        rec = record_for("ASSERT_STATE", params={"key": ["x"], "expected": "home"})
        problems = MCA.check(rec, self.schema)
        self.assertTrue(any("params.key" in p for p in problems), problems)

    def test_assert_state_expected_non_string_is_accepted(self):
        # Deliberately excluded from PARAMS_STRING: asserting a numeric or
        # boolean state value is legitimate, not a bug.
        rec = record_for("ASSERT_STATE", params={"key": "count", "expected": 5})
        self.assertEqual(MCA.check(rec, self.schema), [])

    # --- hand_rules: timeout_ms range (Minor: same per-instance-patching
    # root cause as Major 2, follows the WAIT_FOR.duration_ms pattern) ---

    def test_timeout_ms_zero_is_refused(self):
        rec = record_for("BACK", timeout_ms=0)
        problems = MCA.check(rec, self.schema)
        self.assertTrue(any("timeout_ms" in p for p in problems), problems)

    def test_timeout_ms_negative_is_refused(self):
        rec = record_for("BACK", timeout_ms=-5)
        problems = MCA.check(rec, self.schema)
        self.assertTrue(any("timeout_ms" in p for p in problems), problems)

    def test_timeout_ms_huge_is_accepted(self):
        # No upper bound is named (a legitimately slow device action can run
        # long); only the meaningless-value floor is enforced.
        rec = record_for("BACK", timeout_ms=10**12)
        self.assertEqual(MCA.check(rec, self.schema), [])

    def test_timeout_ms_positive_is_accepted(self):
        rec = record_for("BACK", timeout_ms=5000)
        self.assertEqual(MCA.check(rec, self.schema), [])

    # --- main() exit codes ---

    def test_main_exits_0_on_pass_and_2_on_no_data(self):
        tmp = tempfile.mkdtemp()
        path = os.path.join(tmp, "a.json")
        with open(path, "w") as fh:
            json.dump(record_for("BACK"), fh)
        with contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(MCA.main([path]), 0)
            self.assertEqual(MCA.main(["/no/such/record.json"]), 2)

    def test_main_exits_1_on_fail(self):
        tmp = tempfile.mkdtemp()
        path = os.path.join(tmp, "a.json")
        rec = record_for("OPEN_APP", params={})
        with open(path, "w") as fh:
            json.dump(rec, fh)
        with contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(MCA.main([path]), 1)


class SharedValidatorHardeningTests(unittest.TestCase):
    """Regression tests for the three shared-validator defects found by
    adversarial review of PR #726 and PR #728 (2026-09-16). Every case below
    either reached a real driver call or produced a raw traceback before the
    fix.

    These matter beyond this module: mobile_native_ios_adapter,
    mobile_appium_adapter, mobile_visual_fallback_adapter and
    mobile_hybrid_action_router all route through check(), so each defect
    here was a defect in four adapters at once, and each fix closes it for
    all four rather than one adapter at a time."""

    def setUp(self):
        self.schema = CC.load_json(MCA.DEFAULT_SCHEMA, "schema")

    # --- structural validity gate (PR #728's Critical) -------------------

    def test_non_dict_record_fails_cleanly_instead_of_raising(self):
        # hand_rules() opened with record.get("action"), so a JSON list,
        # string, number or null raised an uncaught AttributeError. Through
        # a CLI that surfaced as exit 1 with empty stdout and a raw
        # traceback on stderr, indistinguishable from a legitimate FAIL.
        for record in ([1, 2], "a string", None, 42):
            with self.subTest(record=record):
                problems = MCA.check(record, self.schema)
                self.assertTrue(problems, "a non-dict record must never validate clean")
                self.assertTrue(
                    any("must be of type 'object'" in p for p in problems),
                    "a non-dict record must FAIL naming its real type, got %r" % (problems,))

    def test_hand_rules_alone_does_not_raise_on_a_non_dict_record(self):
        # The guard belongs in hand_rules() itself, not only in check(), so
        # that a caller reaching hand_rules() directly is safe too.
        for record in ([1, 2], "a string", None, 42):
            with self.subTest(record=record):
                self.assertEqual(MCA.hand_rules(record), [])

    # --- NaN / Infinity (PR #726 Critical 1) -----------------------------

    def test_nan_coordinates_are_refused(self):
        # NaN passed every range check because `value < lo` and
        # `value > hi` are both vacuously False for NaN. It then reached
        # driver.set_location() for real, and json.dumps emitted a bare
        # `NaN` token, which is not valid RFC 8259 JSON.
        nan = float("nan")
        problems = MCA.check(
            record_for("SET_LOCATION", params={"latitude": nan, "longitude": nan}), self.schema)
        self.assertIn("params.latitude: must be a finite number, got nan", problems)
        self.assertIn("params.longitude: must be a finite number, got nan", problems)

    def test_nan_wait_duration_is_refused_as_a_validation_problem(self):
        # Previously validated clean, then time.sleep(nan) raised, which
        # surfaced as a generic FAIL rather than the validation error it is.
        problems = MCA.check(
            record_for("WAIT_FOR", params={"duration_ms": float("nan")}), self.schema)
        self.assertIn("params.duration_ms: must be a finite number, got nan", problems)

    def test_infinity_is_refused_even_where_the_range_has_no_ceiling(self):
        # Infinity was caught only by accident, where a range happened to
        # carry an upper bound (inf > 90). For a key whose ceiling was None
        # it passed clean, so that accident was never a guarantee.
        problems = MCA.check(
            record_for("WAIT_FOR", params={"duration_ms": float("inf")}), self.schema)
        self.assertIn("params.duration_ms: must be a finite number, got inf", problems)

    def test_negative_infinity_is_refused(self):
        problems = MCA.check(
            record_for("SET_LOCATION", params={"latitude": float("-inf"), "longitude": 0}),
            self.schema)
        self.assertIn("params.latitude: must be a finite number, got -inf", problems)

    # --- WAIT_FOR ceiling (PR #726 Critical 2, PR #721's identical Major) -

    def test_wait_for_duration_has_a_real_ceiling(self):
        # Proven before the fix: duration_ms=604800000 (7 days) validated
        # clean and a live record genuinely slept the full raw value.
        problems = MCA.check(
            record_for("WAIT_FOR", params={"duration_ms": 604800000}), self.schema)
        self.assertTrue(any("must be between 1 and" in p for p in problems),
                         "a 7-day WAIT_FOR must be refused, got %r" % (problems,))

    def test_wait_for_duration_at_the_ceiling_is_still_accepted(self):
        self.assertEqual(
            MCA.check(record_for("WAIT_FOR",
                                  params={"duration_ms": MCA.WAIT_FOR_MAX_DURATION_MS}),
                       self.schema), [])

    def test_wait_for_duration_one_past_the_ceiling_is_refused(self):
        self.assertTrue(
            MCA.check(record_for("WAIT_FOR",
                                  params={"duration_ms": MCA.WAIT_FOR_MAX_DURATION_MS + 1}),
                       self.schema))

    # --- LONG_PRESS_TARGET bound (PR #726 Major) -------------------------

    def test_long_press_duration_is_bounded_at_the_vocabulary(self):
        # duration_ms is a param the Appium adapter invented; nothing
        # bounded it, so 86400000 validated clean and the adapter would
        # have held a touch down on a real device for 24 hours. Bounded
        # here, at the vocabulary, so every adapter inherits the bound.
        record = record_for("LONG_PRESS_TARGET", params={"duration_ms": 86400000})
        problems = MCA.check(record, self.schema)
        self.assertTrue(any("must be between 1 and" in p for p in problems),
                         "a 24-hour long press must be refused, got %r" % (problems,))

    def test_negative_long_press_duration_is_refused(self):
        record = record_for("LONG_PRESS_TARGET", params={"duration_ms": -5})
        self.assertTrue(MCA.check(record, self.schema))

    def test_a_normal_long_press_duration_still_validates(self):
        record = record_for("LONG_PRESS_TARGET", params={"duration_ms": 1200})
        self.assertEqual(MCA.check(record, self.schema), [])


if __name__ == "__main__":
    unittest.main()

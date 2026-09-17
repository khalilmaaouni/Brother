#!/usr/bin/env python3
"""Tests for mobile_hybrid_action_router.py (EPIC M3.07).

Drives select_driver() with the REAL describe() output of the three
adapters merged into this branch tonight (mobile_native_ios_adapter M3.03,
mobile_appium_adapter M3.05, mobile_visual_fallback_adapter M3.06), never a
hand-typed fake capability dict, per this unit's own brief: if any of those
adapters' real capabilities change later, these tests change behavior with
them instead of drifting from a stale assumption. A few tests near the
bottom (tiebreak-only cases) use small, schema-valid synthetic
descriptions, clearly separated and labeled, to isolate one ranking axis
(risk, final alphabetical tiebreak) that the three real adapters do not
happen to tie on tonight; they never stand in for the real
last-resort-visual-grounding property, which is proven against the real
adapters only.
"""
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, __file__.rsplit("/", 1)[0])
import mobile_hybrid_action_router as R
import mobile_native_ios_adapter as NATIVE
import mobile_appium_adapter as APPIUM
import mobile_visual_fallback_adapter as VISUAL
import mobile_driver_contract as DC
import contract_check as CC
import test_mobile_canonical_action as MCAT  # reuse its record_for/VALID_BY_ACTION

THIS_DIR = Path(__file__).resolve().parent


def real_descriptions():
    """Fresh real describe() calls, not cached module-level dicts, so a
    test can never accidentally mutate a shared dict another test reads."""
    return [NATIVE.describe(), APPIUM.describe(), VISUAL.describe()]


class RealDriverDescribeSanityTests(unittest.TestCase):
    """The router's whole premise is that describe() output is real and
    trustworthy input. Confirm all three actually validate against the
    schema the router itself checks them against, so a later test failure
    here can never be blamed on this router."""

    def test_all_three_real_describes_validate(self):
        schema = CC.load_json(DC.DEFAULT_SCHEMA, "driver schema")
        for desc in real_descriptions():
            problems = DC.check(desc, schema)
            self.assertEqual(problems, [], "driver_id=%r: %r" % (desc.get("driver_id"), problems))

    def test_three_distinct_driver_ids(self):
        ids = [d["driver_id"] for d in real_descriptions()]
        self.assertEqual(len(ids), len(set(ids)), ids)


class TargetTierTests(unittest.TestCase):
    def test_no_target_is_api_tool(self):
        self.assertEqual(R._target_tier(MCAT.record_for("OPEN_APP")), "api_tool")

    def test_each_selector_type_maps_to_its_tier(self):
        expected = {
            "accessibility_id": "accessibility_selector",
            "testid": "accessibility_selector",
            "xpath": "native_semantic",
            "text": "native_semantic",
            "image": "visual_grounding",
            "coordinates": "raw_coordinate",
        }
        for selector_type, tier in expected.items():
            record = MCAT.record_for("TAP_TARGET",
                                      target={"selector_type": selector_type, "value": "x"})
            self.assertEqual(R._target_tier(record), tier, selector_type)


class VisualIsLastResortAgainstRealAdaptersTests(unittest.TestCase):
    """The load-bearing property this unit was told to hunt adversarially
    for a silent violation of: a real deterministic driver (appium) must
    win over the real visual-fallback driver for every non-image selector
    type, on every action both of them really declare support for, and
    visual-fallback must win only for an image selector, or when it is the
    only real candidate left standing."""

    #: Actions both appium.ACTION_HANDLERS and visual.TARGET_BEARING_ACTIONS
    #: really declare (confirmed by reading both modules directly, not
    #: assumed): TAP_TARGET, LONG_PRESS_TARGET, ASSERT_VISIBLE, WAIT_FOR.
    #: SCROLL_TO is deliberately excluded here (appium does not support it
    #: at all tonight) and covered by its own test below instead.
    _SHARED_ACTIONS = ["TAP_TARGET", "LONG_PRESS_TARGET", "ASSERT_VISIBLE"]
    _NON_IMAGE_SELECTORS = ["accessibility_id", "testid", "xpath", "text", "coordinates"]

    #: coordinates is the one selector_type whose value must look like
    #: "number,number" (mobile_canonical_action's own hand rule); every
    #: other selector_type accepts any non-blank string.
    _SELECTOR_VALUE = {"coordinates": "10,20"}

    def test_appium_beats_visual_for_every_non_image_selector_on_every_shared_action(self):
        descs = real_descriptions()
        for action in self._SHARED_ACTIONS:
            for selector_type in self._NON_IMAGE_SELECTORS:
                value = self._SELECTOR_VALUE.get(selector_type, "x")
                record = MCAT.record_for(action, target={"selector_type": selector_type, "value": value})
                result = R.select_driver(record, descs)
                self.assertEqual(result["status"], "SELECTED", (action, selector_type, result))
                self.assertEqual(result["driver_id"], "appium",
                                  "action=%s selector_type=%s picked %r instead of appium: %r"
                                  % (action, selector_type, result["driver_id"], result))
                self.assertNotIn("WARNING", result["reason"])

    def test_visual_wins_for_image_selector_on_every_shared_action(self):
        descs = real_descriptions()
        for action in self._SHARED_ACTIONS:
            record = MCAT.record_for(action, target={"selector_type": "image", "value": "ref.png"})
            result = R.select_driver(record, descs)
            self.assertEqual(result["status"], "SELECTED", (action, result))
            self.assertEqual(result["driver_id"], "visual-fallback", (action, result))
            self.assertNotIn("WARNING", result["reason"])

    def test_wait_for_target_bearing_also_prefers_appium_over_visual(self):
        # WAIT_FOR is the one shared action whose real handlers cannot
        # execute a target-bearing branch in either driver tonight (both
        # return UNSUPPORTED at runtime -- see the router module's own
        # BRANCH-LEVEL GAP note); the router still ranks by the M3.01
        # contract's action-level supported_actions, which both declare,
        # so appium still wins this selection.
        record = MCAT.record_for("WAIT_FOR", target={"selector_type": "accessibility_id", "value": "x"})
        record.pop("params", None)
        result = R.select_driver(record, real_descriptions())
        self.assertEqual(result["status"], "SELECTED")
        self.assertEqual(result["driver_id"], "appium")

    def test_scroll_to_has_no_real_deterministic_driver_tonight_visual_wins_by_necessity(self):
        # Neither native nor appium declares SCROLL_TO support tonight
        # (confirmed: appium's own UNSUPPORTED_REASONS names it). Visual
        # fallback is the only real candidate, selected with a WARNING
        # since it has no real deterministic/visual match for this
        # accessibility_id tier -- proving the WARNING fires honestly
        # rather than a bare unflagged win.
        record = MCAT.record_for("SCROLL_TO", target={"selector_type": "accessibility_id", "value": "x"})
        result = R.select_driver(record, real_descriptions())
        self.assertEqual(result["status"], "SELECTED")
        self.assertEqual(result["driver_id"], "visual-fallback")
        self.assertIn("WARNING", result["reason"])
        excluded_ids = {e["driver_id"] for e in result["excluded"]}
        self.assertEqual(excluded_ids, {"native-ios-simctl", "appium"})


class PlatformNativeFidelityTests(unittest.TestCase):
    def test_open_app_prefers_native_ios_over_appium_no_platform_given(self):
        result = R.select_driver(MCAT.record_for("OPEN_APP"), real_descriptions())
        self.assertEqual(result["status"], "SELECTED")
        self.assertEqual(result["driver_id"], "native-ios-simctl")

    def test_capture_prefers_native_ios_over_appium(self):
        result = R.select_driver(MCAT.record_for("CAPTURE"), real_descriptions())
        self.assertEqual(result["driver_id"], "native-ios-simctl")

    def test_deeplink_prefers_native_ios_over_appium(self):
        result = R.select_driver(MCAT.record_for("DEEPLINK"), real_descriptions())
        self.assertEqual(result["driver_id"], "native-ios-simctl")

    def test_platform_android_excludes_native_ios(self):
        result = R.select_driver(MCAT.record_for("SET_LOCATION"), real_descriptions(), platform="android")
        self.assertEqual(result["status"], "SELECTED")
        self.assertEqual(result["driver_id"], "appium")
        excluded_ids = {e["driver_id"] for e in result["excluded"]}
        self.assertIn("native-ios-simctl", excluded_ids)

    def test_platform_ios_keeps_native_ios_as_a_candidate(self):
        result = R.select_driver(MCAT.record_for("OPEN_APP"), real_descriptions(), platform="ios")
        self.assertEqual(result["driver_id"], "native-ios-simctl")

    def test_cross_platform_visual_survives_any_platform_filter(self):
        # visual-fallback declares "cross-platform" among its platforms;
        # a platform filter must never exclude it on that basis alone.
        result = R.select_driver(
            MCAT.record_for("TAP_TARGET", target={"selector_type": "image", "value": "ref.png"}),
            real_descriptions(), platform="android")
        self.assertEqual(result["driver_id"], "visual-fallback")


class HardFilterTests(unittest.TestCase):
    def test_action_no_real_driver_supports_is_no_driver(self):
        # HOME: not in native's ACTION_HANDLERS, not in appium's (its own
        # UNSUPPORTED_REASONS names it), not target-bearing so never in
        # visual's TARGET_BEARING_ACTIONS. Confirmed by reading all three
        # modules, not assumed.
        result = R.select_driver(MCAT.record_for("HOME"), real_descriptions())
        self.assertEqual(result["status"], "NO_DRIVER")
        self.assertEqual(len(result["excluded"]), 3)

    def test_set_network_only_appium_supports_it(self):
        result = R.select_driver(MCAT.record_for("SET_NETWORK"), real_descriptions())
        self.assertEqual(result["status"], "SELECTED")
        self.assertEqual(result["driver_id"], "appium")

    def test_empty_driver_list_is_no_driver(self):
        result = R.select_driver(MCAT.record_for("OPEN_APP"), [])
        self.assertEqual(result["status"], "NO_DRIVER")
        self.assertEqual(result["considered"], [])

    def test_none_driver_list_is_no_driver_not_a_crash(self):
        result = R.select_driver(MCAT.record_for("OPEN_APP"), None)
        self.assertEqual(result["status"], "NO_DRIVER")


class InvalidInputTests(unittest.TestCase):
    def test_invalid_record_is_fail_not_a_crash(self):
        result = R.select_driver({"action": "NOPE"}, real_descriptions())
        self.assertEqual(result["status"], "FAIL")
        self.assertIn("invalid canonical action record", result["reason"])

    def test_record_missing_action_is_fail(self):
        result = R.select_driver({"schema_version": "mobile-canonical-action-v1", "action_id": "a1"},
                                  real_descriptions())
        self.assertEqual(result["status"], "FAIL")

    def test_malformed_driver_description_is_excluded_not_trusted(self):
        result = R.select_driver(MCAT.record_for("OPEN_APP"),
                                  [{"driver_id": "bogus"}] + real_descriptions())
        self.assertEqual(result["status"], "SELECTED")
        self.assertEqual(result["driver_id"], "native-ios-simctl")
        bogus = [e for e in result["excluded"] if e["driver_id"] == "bogus"]
        self.assertEqual(len(bogus), 1)
        self.assertIn("self-description invalid", bogus[0]["reason"])

    def test_non_dict_driver_description_never_crashes(self):
        result = R.select_driver(MCAT.record_for("OPEN_APP"), ["not-a-dict", 42, None])
        self.assertEqual(result["status"], "NO_DRIVER")
        self.assertEqual(len(result["excluded"]), 3)


class OrderIndependenceTests(unittest.TestCase):
    """A caller may register drivers in any order (a dict/set iteration
    order, a config file's own order); the winner must never depend on
    registration order, only on the ranking signals themselves."""

    def test_winner_is_the_same_regardless_of_registration_order(self):
        record = MCAT.record_for("TAP_TARGET", target={"selector_type": "accessibility_id", "value": "x"})
        forward = R.select_driver(record, real_descriptions())
        backward = R.select_driver(record, list(reversed(real_descriptions())))
        self.assertEqual(forward["driver_id"], backward["driver_id"])
        self.assertEqual(forward["driver_id"], "appium")


class SyntheticTiebreakTests(unittest.TestCase):
    """Isolated, single-axis tests using small synthetic (but
    schema-valid) descriptions, because the three real adapters tonight
    never actually tie on risk_class or driver_id for a shared action.
    These never assert anything about the real adapters' own behavior
    (that is VisualIsLastResortAgainstRealAdaptersTests's job)."""

    def _desc(self, driver_id, action, risk_class, deterministic=True):
        return {
            "schema_version": "mobile-driver-contract-v1",
            "driver_id": driver_id,
            "driver_name": driver_id,
            "action_vocabulary_ref": "mobile-canonical-action-v1",
            "supported_actions": [action],
            "platforms": ["ios"],
            "device_modes": ["simulator"],
            "remote_sessions_supported": False,
            "observations": ["screenshot"],
            "deterministic_selector_support": deterministic,
            "visual_grounding_support": False,
            "risk_classes": [{"action": action, "risk_class": risk_class}],
        }

    def setUp(self):
        self.schema = CC.load_json(DC.DEFAULT_SCHEMA, "driver schema")

    def test_both_synthetic_descriptions_are_schema_valid(self):
        for desc in (self._desc("safe-driver", "OPEN_APP", "safe"),
                     self._desc("risky-driver", "OPEN_APP", "destructive")):
            self.assertEqual(DC.check(desc, self.schema), [])

    def test_lower_risk_wins_when_everything_else_ties(self):
        safe = self._desc("safe-driver", "OPEN_APP", "safe")
        risky = self._desc("risky-driver", "OPEN_APP", "destructive")
        result = R.select_driver(MCAT.record_for("OPEN_APP"), [risky, safe])
        self.assertEqual(result["driver_id"], "safe-driver")

    def test_alphabetical_driver_id_is_the_final_tiebreak(self):
        a = self._desc("a-driver", "OPEN_APP", "safe")
        b = self._desc("b-driver", "OPEN_APP", "safe")
        result = R.select_driver(MCAT.record_for("OPEN_APP"), [b, a])
        self.assertEqual(result["driver_id"], "a-driver")


class CliTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="brother-m307-tests-")
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)

    def _write_json(self, name, data):
        path = self.root / name
        path.write_text(json.dumps(data))
        return str(path)

    def test_cli_selects_and_exits_zero(self):
        record_path = self._write_json("record.json", MCAT.record_for("OPEN_APP"))
        native_path = self._write_json("native.json", NATIVE.describe())
        appium_path = self._write_json("appium.json", APPIUM.describe())
        exit_code = R.main(["--record", record_path,
                             "--driver-describe", native_path,
                             "--driver-describe", appium_path])
        self.assertEqual(exit_code, 0)

    def test_cli_no_driver_exits_one(self):
        record_path = self._write_json("record.json", MCAT.record_for("HOME"))
        native_path = self._write_json("native.json", NATIVE.describe())
        exit_code = R.main(["--record", record_path, "--driver-describe", native_path])
        self.assertEqual(exit_code, 1)

    def test_cli_missing_record_file_is_no_data_exit_two(self):
        exit_code = R.main(["--record", str(self.root / "missing.json")])
        self.assertEqual(exit_code, 2)

    def test_cli_as_a_real_subprocess_prints_valid_json(self):
        record_path = self._write_json("record.json", MCAT.record_for("OPEN_APP"))
        native_path = self._write_json("native.json", NATIVE.describe())
        appium_path = self._write_json("appium.json", APPIUM.describe())
        proc = subprocess.run(
            [sys.executable, str(THIS_DIR / "mobile_hybrid_action_router.py"),
             "--record", record_path, "--driver-describe", native_path,
             "--driver-describe", appium_path],
            capture_output=True, text=True, check=False)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        parsed = json.loads(proc.stdout)
        self.assertEqual(parsed["status"], "SELECTED")
        self.assertEqual(parsed["driver_id"], "native-ios-simctl")


class RouterHardeningTests(unittest.TestCase):
    """Regression tests for the three defects adversarial review of PR #728
    found (2026-09-16), none of which this suite's original 30 tests
    caught."""

    def _open_app(self):
        return MCAT.record_for("OPEN_APP", params={"app_id": "com.example.app"})

    # --- CRITICAL: crash on a non-dict record ----------------------------

    def test_select_driver_does_not_crash_on_a_non_dict_record(self):
        # select_driver() ran ACT.check(), which ran hand_rules()
        # unconditionally, whose first line was record.get("action"). A
        # JSON list, string or null therefore raised an uncaught
        # AttributeError out of select_driver(). Through the CLI that was
        # exit 1 with empty stdout and a raw traceback on stderr, which a
        # caller cannot tell apart from a legitimate NO_DRIVER/FAIL exit.
        #
        # Fixed at the root, in the SHARED validator
        # (mobile_canonical_action.hand_rules), not with a router-local
        # guard: three sibling adapters already guarded this at their own
        # door, and a fifth copy would have left the next caller exposed.
        for record in ([1, 2], "a string", None, 42):
            with self.subTest(record=record):
                result = R.select_driver(record, [NATIVE.describe()])
                self.assertEqual(result["status"], "FAIL")
                self.assertIn("invalid canonical action record", result["reason"])

    # --- MAJOR: risk_class / selector_match were prose-only --------------

    def test_selected_result_exposes_risk_class_as_a_top_level_key(self):
        # The router computes risk_class internally and then dropped it,
        # leaving a caller to parse it back out of the human-readable
        # `reason` prose in order to gate on risk.
        result = R.select_driver(self._open_app(), [NATIVE.describe()])
        self.assertEqual(result["status"], "SELECTED")
        self.assertEqual(result["driver_id"], "native-ios-simctl")
        self.assertEqual(result["risk_class"],
                          dict((e["action"], e["risk_class"])
                               for e in NATIVE.describe()["risk_classes"])["OPEN_APP"])

    def test_selected_result_exposes_selector_match_as_a_top_level_key(self):
        result = R.select_driver(self._open_app(), [NATIVE.describe()])
        self.assertIn("selector_match", result)
        self.assertIsInstance(result["selector_match"], bool)

    def test_risk_class_and_selector_match_are_present_but_null_when_not_selected(self):
        # A caller reading result["risk_class"] must never hit a KeyError
        # just because nothing was selected.
        result = R.select_driver(self._open_app(), [])
        self.assertEqual(result["status"], "NO_DRIVER")
        self.assertIsNone(result["risk_class"])
        self.assertIsNone(result["selector_match"])

    def test_risk_class_key_matches_the_reason_prose_it_replaces(self):
        result = R.select_driver(self._open_app(), real_descriptions())
        self.assertEqual(result["status"], "SELECTED")
        self.assertIn("risk_class=%s" % result["risk_class"], result["reason"])

    # --- MAJOR: no driver_id uniqueness check ----------------------------

    def test_a_second_description_impersonating_a_real_driver_id_is_refused(self):
        # A description claiming an existing real driver's id could win the
        # ranking for an action the real driver does not support, and then
        # be dispatched by the caller to the REAL module (which is looked up
        # BY that id), where it fails. Ambiguous identity is refused: this
        # router cannot tell the real driver from the impostor, so it must
        # not guess which one it meant.
        real = NATIVE.describe()
        impostor = json.loads(json.dumps(real))
        impostor["platforms"] = ["ios", "android", "web", "cross-platform"]
        result = R.select_driver(self._open_app(), [real, impostor])
        self.assertEqual(result["status"], "NO_DRIVER")
        self.assertIsNone(result["driver_id"])
        self.assertEqual(len(result["excluded"]), 2)
        for entry in result["excluded"]:
            self.assertIn("ambiguous identity", entry["reason"])

    def test_a_duplicate_id_does_not_exclude_the_other_distinct_drivers(self):
        # The refusal must be scoped to the duplicated id only; a genuine
        # third driver with its own unique id still competes normally.
        real = NATIVE.describe()
        impostor = json.loads(json.dumps(real))
        visual = VISUAL.describe()
        record = MCAT.record_for(
            "ASSERT_VISIBLE", target={"selector_type": "image", "value": "logo.png"})
        result = R.select_driver(record, [real, impostor, visual])
        self.assertEqual(result["status"], "SELECTED")
        self.assertEqual(result["driver_id"], visual["driver_id"])

    def test_distinct_driver_ids_are_never_treated_as_duplicates(self):
        # Guards against an over-broad uniqueness check quietly refusing
        # the ordinary multi-driver case this router exists to serve.
        result = R.select_driver(self._open_app(), real_descriptions())
        self.assertEqual(result["status"], "SELECTED")
        self.assertFalse([e for e in result["excluded"]
                          if "ambiguous identity" in e["reason"]])


if __name__ == "__main__":
    unittest.main()

"""Tests for mobile_visual_fallback_adapter.py (EPIC M3.06).

Follows scripts/test_mobile_native_ios_adapter.py's own convention for the
driver-contract half (describe() validated against the real schema,
supported_actions/risk_classes checked for lockstep). The rest of this
suite proves the three things the task brief singled out: the data
contract itself, confidence-threshold handling, and the ephemeral/
non-selector guarantee -- the last one both behaviorally (every real
execute_action() output today) and structurally (the module source writes
no file at all, and the schema/hand_rules() refuse a record that violates
either the stability const or the "no silent confidence" rule regardless
of how it was produced).
"""
import base64
import inspect
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parent))
import mobile_visual_fallback_adapter as VFA
import mobile_canonical_action as ACT
import mobile_driver_contract as DC
import contract_check as CC
import test_mobile_canonical_action as MCAT  # reuse its VALID_BY_ACTION/record_for

# Real, tiny 1x1 PNG (same fixture bytes test_mobile_workflow.py and
# test_mobile_native_ios_adapter.py use).
_PNG = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+jWZkAAAAASUVORK5CYII=")


def _grounded_record(**overrides):
    """A synthetic GROUNDED result -- the shape a future model call would
    produce -- for hand_rules()/check() tests that do not go through
    execute_action() (which never reaches this status today)."""
    rec = {
        "schema_version": "mobile-visual-grounding-v1", "action_id": "a1", "action": "TAP_TARGET",
        "stability": "visual_only", "status": "GROUNDED", "confidence": 0.9,
        "target": {"kind": "point", "x": 120.0, "y": 480.0}, "target_description": "the submit button",
        "evidence": {"screenshot": None, "semantic_tree_provided": False, "model_call": None},
        "detail": "synthetic fixture",
    }
    rec.update(overrides)
    return rec


class ClassifyConfidenceTests(unittest.TestCase):
    def test_none_confidence_is_not_attempted_regardless_of_threshold(self):
        self.assertEqual(VFA.classify_confidence(None, 0.6), "NOT_ATTEMPTED")
        self.assertEqual(VFA.classify_confidence(None, None), "NOT_ATTEMPTED")

    def test_at_or_above_threshold_is_grounded(self):
        self.assertEqual(VFA.classify_confidence(0.6, 0.6), "GROUNDED")
        self.assertEqual(VFA.classify_confidence(0.99, 0.6), "GROUNDED")
        self.assertEqual(VFA.classify_confidence(1.0, 1.0), "GROUNDED")

    def test_below_threshold_is_low_confidence(self):
        self.assertEqual(VFA.classify_confidence(0.59, 0.6), "LOW_CONFIDENCE")
        self.assertEqual(VFA.classify_confidence(0.0, 0.6), "LOW_CONFIDENCE")

    def test_missing_threshold_refuses_to_classify_a_real_confidence(self):
        with self.assertRaises(ValueError):
            VFA.classify_confidence(0.9, None)

    def test_out_of_range_confidence_is_refused(self):
        with self.assertRaises(ValueError):
            VFA.classify_confidence(1.1, 0.6)
        with self.assertRaises(ValueError):
            VFA.classify_confidence(-0.1, 0.6)

    def test_bool_confidence_is_refused_even_though_bool_is_an_int_subclass(self):
        with self.assertRaises(ValueError):
            VFA.classify_confidence(True, 0.6)

    def test_non_numeric_confidence_is_refused(self):
        with self.assertRaises(ValueError):
            VFA.classify_confidence("high", 0.6)


class TargetBearingActionsTests(unittest.TestCase):
    def test_matches_the_actions_whose_hand_rule_names_a_target(self):
        # Independent recomputation from the same source of truth
        # (mobile_canonical_action.ACTION_RULES), so this test would catch
        # a drift between the module's derivation and a manual re-reading
        # of the rules, not merely restate the module's own logic.
        expected = {"TAP_TARGET", "LONG_PRESS_TARGET", "SCROLL_TO", "ASSERT_VISIBLE", "WAIT_FOR"}
        self.assertEqual(set(VFA.TARGET_BEARING_ACTIONS), expected)

    def test_every_other_action_is_excluded(self):
        excluded = set(ACT.ACTION_RULES) - set(VFA.TARGET_BEARING_ACTIONS)
        self.assertEqual(excluded, {
            "OPEN_APP", "TYPE_TEXT", "SWIPE", "BACK", "HOME", "ROTATE", "SET_PERMISSION",
            "SET_NETWORK", "SET_LOCATION", "DEEPLINK", "ASSERT_STATE", "CAPTURE", "FINISH",
            "NEED_HUMAN", "IMPOSSIBLE",
        })


class DescribeTests(unittest.TestCase):
    def setUp(self):
        self.driver_schema = CC.load_json(DC.DEFAULT_SCHEMA, "driver schema")

    def test_describe_validates_against_the_driver_contract_schema(self):
        self.assertEqual(DC.check(VFA.describe(), self.driver_schema), [])

    def test_supported_actions_matches_target_bearing_actions(self):
        self.assertEqual(VFA.describe()["supported_actions"], sorted(VFA.TARGET_BEARING_ACTIONS))

    def test_risk_classes_cover_every_supported_action_exactly_once(self):
        description = VFA.describe()
        actions = description["supported_actions"]
        entries = {e["action"]: e["risk_class"] for e in description["risk_classes"]}
        self.assertEqual(set(entries), set(actions))
        self.assertTrue(all(v == "safe" for v in entries.values()))

    def test_visual_grounding_support_true_deterministic_selector_support_false(self):
        description = VFA.describe()
        self.assertTrue(description["visual_grounding_support"])
        self.assertFalse(description["deterministic_selector_support"])


class ExecuteActionTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="brother-m306-tests-")
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.screenshot = self.root / "shot.png"
        self.screenshot.write_bytes(_PNG)
        self.evidence_root = self.root / "evidence"
        self.grounding_schema = CC.load_json(VFA.DEFAULT_SCHEMA, "grounding schema")

    def _record(self, action, **overrides):
        return MCAT.record_for(action, **overrides)

    def test_every_target_bearing_action_with_a_real_target_is_not_attempted(self):
        # The structural core of the ephemeral guarantee, behaviorally: with
        # no vision model wired, every one of these must come back honest
        # (confidence/target null, stability visual_only), never a guess.
        for action in VFA.TARGET_BEARING_ACTIONS:
            with self.subTest(action=action):
                record = self._record(action)
                if "target" not in record:
                    # WAIT_FOR's own VALID_BY_ACTION fixture uses
                    # duration_ms, not a target; give it one explicitly so
                    # this test actually exercises the grounding path for
                    # every target-bearing action, not just four of five.
                    record["target"] = {"selector_type": "text", "value": "Continue"}
                result = VFA.execute_action(record, self.screenshot, self.evidence_root, [])
                self.assertEqual(result["status"], "NOT_ATTEMPTED")
                self.assertIsNone(result["confidence"])
                self.assertIsNone(result["target"])
                self.assertEqual(result["stability"], "visual_only")
                self.assertEqual(VFA.check(result, self.grounding_schema), [])

    def test_wait_for_with_only_duration_ms_is_unsupported_not_faked(self):
        record = self._record("WAIT_FOR")  # fixture is duration_ms-only, no target
        result = VFA.execute_action(record, self.screenshot, self.evidence_root, [])
        self.assertEqual(result["status"], "UNSUPPORTED")
        self.assertIn("no target to ground", result["detail"])

    def test_non_target_bearing_action_is_unsupported(self):
        record = self._record("OPEN_APP")
        result = VFA.execute_action(record, self.screenshot, self.evidence_root, [])
        self.assertEqual(result["status"], "UNSUPPORTED")
        self.assertIsNone(result["confidence"])
        self.assertEqual(VFA.check(result, self.grounding_schema), [])

    def test_missing_screenshot_fails_structurally_not_silently(self):
        record = self._record("TAP_TARGET")
        result = VFA.execute_action(record, self.root / "does-not-exist.png", self.evidence_root, [])
        self.assertEqual(result["status"], "FAIL")
        self.assertIn("not found or unreadable", result["detail"])

    def test_invalid_png_fails_structurally(self):
        bad = self.root / "bad.png"
        bad.write_text("not a png")
        record = self._record("TAP_TARGET")
        result = VFA.execute_action(record, bad, self.evidence_root, [])
        self.assertEqual(result["status"], "FAIL")
        self.assertIn("not a valid PNG", result["detail"])

    def test_invalid_canonical_record_fails_before_touching_the_screenshot(self):
        record = {"schema_version": "mobile-canonical-action-v1", "action_id": "a1", "action": "TAP_TARGET"}
        # no target: mobile_canonical_action's own hand rule rejects this.
        result = VFA.execute_action(record, self.root / "does-not-exist.png", self.evidence_root, [])
        self.assertEqual(result["status"], "FAIL")
        self.assertIn("invalid canonical action record", result["detail"])

    def test_non_dict_record_is_refused_as_fail_not_unsupported(self):
        """FAIL, matching mobile_native_ios_adapter and
        mobile_appium_adapter on this exact input. This assertion used to
        read UNSUPPORTED, which was the divergence
        test_mobile_driver_gauntlet.py (M3.08) found: a malformed record is
        broken for every driver, and reporting it as merely unsupported
        here tells M3.07's router to go try the next adapter with the same
        dead record."""
        result = VFA.execute_action(["not", "a", "dict"], self.screenshot, self.evidence_root, [])
        self.assertEqual(result["status"], "FAIL")
        self.assertIsNone(result["action_id"])

    def test_target_description_prefers_description_over_value(self):
        record = self._record("TAP_TARGET",
                               target={"selector_type": "text", "value": "loginBtn",
                                       "description": "the blue login button"})
        result = VFA.execute_action(record, self.screenshot, self.evidence_root, [])
        self.assertEqual(result["target_description"], "the blue login button")

    def test_evidence_carries_the_real_screenshot_identity(self):
        record = self._record("ASSERT_VISIBLE")
        result = VFA.execute_action(record, self.screenshot, self.evidence_root, [])
        self.assertIsNotNone(result["evidence"]["screenshot"])
        self.assertEqual(result["evidence"]["screenshot"]["width"], 1)
        self.assertFalse(result["evidence"]["semantic_tree_provided"])

    def test_semantic_tree_provided_flag_reflects_a_real_read_of_the_file(self):
        # Rewritten 2026-09-16 (PR #724 review). This test previously
        # passed a path that was never created and asserted the flag was
        # True, which encoded the defect rather than catching it: the flag
        # was `semantic_tree_path is not None`, a caller's unverified
        # CLAIM, and the path was never opened, read or checked. The flag
        # is now a verified fact, so this asserts the stronger property --
        # a REAL file sets it. The nonexistent-path case is covered by
        # VisualFallbackHardeningTests below.
        tree = self.root / "tree.json"
        tree.write_text(json.dumps({"root": []}))
        record = self._record("ASSERT_VISIBLE")
        result = VFA.execute_action(record, self.screenshot, self.evidence_root, [],
                                     semantic_tree_path=tree)
        self.assertTrue(result["evidence"]["semantic_tree_provided"])

    def test_this_adapter_never_writes_into_evidence_root(self):
        # execute_action()'s own docstring claims it writes no file of its
        # own into evidence_root; prove it rather than merely state it.
        self.assertFalse(self.evidence_root.exists())
        record = self._record("TAP_TARGET")
        VFA.execute_action(record, self.screenshot, self.evidence_root, [])
        self.assertFalse(self.evidence_root.exists())


class FutureModelSeamTests(unittest.TestCase):
    """ground_target()'s post-dispatch branch is unreachable today
    (_call_vision_model always returns None), but must not crash or guess
    once a real model is wired -- found by adversarial self-review of the
    first draft, which let a malformed dispatch response raise an uncaught
    KeyError/ValueError instead of a clean FAIL. Proven here by
    monkeypatching the one seam the module itself names for this."""

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="brother-m306-future-")
        self.addCleanup(self.temp.cleanup)
        self.screenshot = Path(self.temp.name) / "shot.png"
        self.screenshot.write_bytes(_PNG)

    def test_grounded_above_threshold(self):
        fake = {"confidence": 0.9, "target": {"kind": "point", "x": 5.0, "y": 6.0},
                "model_call": {"model": "fake-vision-1"}}
        with patch.object(VFA, "_call_vision_model", return_value=fake):
            result = VFA.ground_target(self.screenshot, "the button", confidence_threshold=0.5)
        self.assertEqual(result["status"], "GROUNDED")
        self.assertEqual(result["confidence"], 0.9)
        self.assertEqual(result["target"], fake["target"])
        self.assertEqual(result["evidence"]["model_call"], fake["model_call"])

    def test_low_confidence_below_threshold_still_carries_target(self):
        fake = {"confidence": 0.2, "target": {"kind": "point", "x": 5.0, "y": 6.0}, "model_call": None}
        with patch.object(VFA, "_call_vision_model", return_value=fake):
            result = VFA.ground_target(self.screenshot, "the button", confidence_threshold=0.5)
        self.assertEqual(result["status"], "LOW_CONFIDENCE")
        self.assertEqual(result["confidence"], 0.2)
        self.assertIsNotNone(result["target"])

    def test_malformed_response_missing_confidence_key_fails_cleanly(self):
        fake = {"target": {"kind": "point", "x": 1.0, "y": 1.0}}  # no "confidence"
        with patch.object(VFA, "_call_vision_model", return_value=fake):
            result = VFA.ground_target(self.screenshot, "the button", confidence_threshold=0.5)
        self.assertEqual(result["status"], "FAIL")
        self.assertIsNone(result["confidence"])
        self.assertIsNone(result["target"])
        self.assertIn("unusable result", result["detail"])

    def test_missing_threshold_with_a_real_confidence_fails_cleanly_not_uncaught(self):
        fake = {"confidence": 0.9, "target": {"kind": "point", "x": 1.0, "y": 1.0}}
        with patch.object(VFA, "_call_vision_model", return_value=fake):
            result = VFA.ground_target(self.screenshot, "the button", confidence_threshold=None)
        self.assertEqual(result["status"], "FAIL")
        self.assertIsNone(result["confidence"])
        self.assertIsNone(result["target"])

    def test_out_of_range_confidence_fails_cleanly_not_uncaught(self):
        fake = {"confidence": 1.5, "target": {"kind": "point", "x": 1.0, "y": 1.0}}
        with patch.object(VFA, "_call_vision_model", return_value=fake):
            result = VFA.ground_target(self.screenshot, "the button", confidence_threshold=0.5)
        self.assertEqual(result["status"], "FAIL")
        self.assertIsNone(result["target"])


class GroundingSchemaHandRulesTests(unittest.TestCase):
    """The structural half of the "no silent confidence, no guessed
    target" guarantee: a record is refused regardless of how it was
    produced, not merely by execute_action()'s own convention."""

    def setUp(self):
        self.schema = CC.load_json(VFA.DEFAULT_SCHEMA, "grounding schema")

    def test_grounded_record_with_a_real_point_target_passes(self):
        self.assertEqual(VFA.check(_grounded_record(), self.schema), [])

    def test_grounded_record_with_a_real_bbox_target_passes(self):
        rec = _grounded_record(target={"kind": "bbox", "x0": 10.0, "y0": 10.0, "x1": 50.0, "y1": 40.0})
        self.assertEqual(VFA.check(rec, self.schema), [])

    def test_not_attempted_with_a_confidence_set_is_refused(self):
        # The exact bug class the adversarial-review instruction named: a
        # confidence silently defaulted in place of real unavailability.
        rec = _grounded_record(status="NOT_ATTEMPTED", confidence=0.95, target=None)
        problems = VFA.check(rec, self.schema)
        self.assertTrue(any("must be null when status" in p and "confidence" in p for p in problems))

    def test_not_attempted_with_a_target_set_is_refused(self):
        rec = _grounded_record(status="NOT_ATTEMPTED", confidence=None,
                                target={"kind": "point", "x": 1.0, "y": 1.0})
        problems = VFA.check(rec, self.schema)
        self.assertTrue(any("must never guess a target" in p for p in problems))

    def test_grounded_with_no_confidence_is_refused(self):
        rec = _grounded_record(confidence=None)
        problems = VFA.check(rec, self.schema)
        self.assertTrue(any("confidence" in p for p in problems))

    def test_grounded_confidence_out_of_range_is_refused(self):
        rec = _grounded_record(confidence=1.5)
        problems = VFA.check(rec, self.schema)
        self.assertTrue(any("within [0.0, 1.0]" in p for p in problems))

    def test_grounded_confidence_as_bool_is_refused(self):
        rec = _grounded_record(confidence=True)
        problems = VFA.check(rec, self.schema)
        self.assertTrue(any("must be a real number" in p for p in problems))

    def test_grounded_target_missing_coordinates_is_refused(self):
        rec = _grounded_record(target={"kind": "point", "x": 1.0})
        problems = VFA.check(rec, self.schema)
        self.assertTrue(any("target.y" in p for p in problems))

    def test_grounded_target_with_unknown_kind_is_refused(self):
        rec = _grounded_record(target={"kind": "circle", "x": 1.0, "y": 1.0})
        problems = VFA.check(rec, self.schema)
        self.assertTrue(any("target.kind" in p for p in problems))

    def test_stability_const_refuses_any_other_value(self):
        rec = _grounded_record(stability="stable")
        problems = VFA.check(rec, self.schema)
        self.assertTrue(any("stability" in p for p in problems))


class EphemeralNonSelectorGuaranteeTests(unittest.TestCase):
    """The CRITICAL CONSTRAINT from docs/plan/MOBILE-EPIC-M3-UNITS.md's
    M3.06 row, proved at the source level: this module has no code path
    that writes a file, so it has no code path that could persist a
    grounded target as a stable selector. Crystallization (EPIC M6) is a
    separate, not-yet-built unit; this module cannot perform its job even
    by accident."""

    def test_module_source_contains_no_file_write_call(self):
        source = inspect.getsource(VFA)
        for banned in (".write_text(", ".write_bytes(", "open(", "Path.open", ".mkdir("):
            with self.subTest(banned=banned):
                if banned == "open(":
                    # Only main()'s CLI evidence-dir bookkeeping calls
                    # Path(...).mkdir(); a bare open() call for writing is
                    # what this test actually forbids -- json.dumps()
                    # (never json.dump(fh, ...)) is what the module uses to
                    # print, so "open(" itself must not appear at all.
                    self.assertNotIn(banned, source)
                elif banned == ".mkdir(":
                    # main()'s CLI wrapper creates the evidence directory
                    # for CLI-shape parity with the other adapters (see its
                    # own comment); execute_action()/ground_target() --
                    # the actual grounding logic -- must not, which
                    # test_this_adapter_never_writes_into_evidence_root
                    # above proves behaviorally. Assert the call is
                    # confined to main(), not scattered through the logic.
                    calls = [i for i in range(len(source)) if source.startswith(".mkdir(", i)]
                    self.assertEqual(len(calls), 1, "expected exactly one .mkdir() call, in main() only")
                else:
                    self.assertNotIn(banned, source)

    def test_every_status_this_module_can_actually_produce_today_is_ephemeral(self):
        # Behavioral cross-check: every status value the current
        # implementation can reach (GROUNDED/LOW_CONFIDENCE are dead code
        # until a model is wired -- see _call_vision_model) forbids a
        # persisted target.
        reachable_today = {"NOT_ATTEMPTED", "UNSUPPORTED", "FAIL"}
        self.assertEqual(reachable_today, set(VFA._STATUSES_FORBIDDING_GROUNDING))

    def test_call_vision_model_seam_returns_none_confirming_no_live_call(self):
        result = VFA._call_vision_model("shot.png", "the button", None, "some-model")
        self.assertIsNone(result)


class VisualFallbackHardeningTests(unittest.TestCase):
    """Regression tests for the three defects adversarial review of PR #724
    found (2026-09-16). All three were real but LATENT: nothing reachable
    crashed, because the vision-model seam (_call_vision_model) is not wired
    to anything yet. They are fixed and pinned here BEFORE that seam lands,
    which is exactly when they would stop being latent."""

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.png = self.tmp / "screen.png"
        self.png.write_bytes(_PNG)
        self.record = MCAT.record_for(
            "TAP_TARGET", target={"selector_type": "text", "value": "Login"})

    # --- MAJOR: no finiteness/bounds check on coordinates ----------------

    def test_nan_coordinates_are_refused(self):
        # Only isinstance(value, (int, float)) was checked, with no domain
        # check, so {"x": nan, "y": nan} validated clean -- and json.dumps
        # then emits bare `NaN` tokens, which are not valid RFC 8259 JSON,
        # so the record breaks any strict JSON reader downstream instead of
        # failing here. `confidence` already got a range check; coordinates
        # now get the explicit equivalent.
        nan = float("nan")
        problems = VFA.hand_rules(_grounded_record(
            target={"kind": "point", "x": nan, "y": nan}))
        self.assertTrue(any("finite x" in p for p in problems), problems)
        self.assertTrue(any("finite y" in p for p in problems), problems)

    def test_infinite_coordinates_are_refused_for_point_and_bbox(self):
        inf = float("inf")
        self.assertTrue(VFA.hand_rules(_grounded_record(
            target={"kind": "point", "x": inf, "y": 1})))
        self.assertTrue(VFA.hand_rules(_grounded_record(
            target={"kind": "bbox", "x0": 0, "y0": 0, "x1": inf, "y1": 4})))

    def test_negative_coordinates_are_refused(self):
        # A negative coordinate cannot name a pixel on a screenshot.
        self.assertTrue(VFA.hand_rules(_grounded_record(
            target={"kind": "point", "x": -1, "y": 10})))

    def test_every_bbox_coordinate_key_is_checked_not_just_the_first(self):
        # The predicate must be applied to every coordinate key, so a bad
        # value in any one of the four is caught.
        nan = float("nan")
        for key in ("x0", "y0", "x1", "y1"):
            with self.subTest(key=key):
                target = {"kind": "bbox", "x0": 0, "y0": 0, "x1": 4, "y1": 4}
                target[key] = nan
                problems = VFA.hand_rules(_grounded_record(target=target))
                self.assertTrue(any("finite %s" % key in p for p in problems), problems)

    def test_a_non_numeric_coordinate_still_reports_the_original_type_problem(self):
        # The added domain check must not swallow the type check that was
        # already there.
        problems = VFA.hand_rules(_grounded_record(
            target={"kind": "point", "x": "120", "y": 10}))
        self.assertTrue(any("requires a numeric x" in p for p in problems), problems)

    def test_ordinary_coordinates_still_validate(self):
        self.assertEqual(VFA.hand_rules(_grounded_record(
            target={"kind": "point", "x": 0, "y": 0})), [])
        self.assertEqual(VFA.hand_rules(_grounded_record(
            target={"kind": "bbox", "x0": 0, "y0": 1, "x1": 4.5, "y1": 9})), [])

    # --- MAJOR: execute_action never validated its own output ------------

    def test_execute_action_validates_its_own_result(self):
        # Only main() validated. The documented in-process caller (M3.07's
        # router) calls execute_action() directly and so received an
        # unvalidated grounding record. Proved by making the seam return a
        # malformed result: it must become a clean FAIL, not be handed back
        # as a grounding.
        bad = {"target": {"kind": "point", "x": float("nan"), "y": float("nan")},
               "confidence": 0.9, "model_call": {"model": "fake"}}
        with patch.object(VFA, "_call_vision_model", return_value=bad):
            result = VFA.execute_action(self.record, str(self.png), self.tmp, [],
                                         confidence_threshold=0.5)
        self.assertEqual(result["status"], "FAIL")
        self.assertIn("violates mobile-visual-grounding-v1", result["detail"])
        self.assertIsNone(result["target"])
        self.assertIsNone(result["confidence"])

    def test_execute_actions_own_fail_record_is_itself_valid(self):
        # The refusal must not itself be a record that fails validation.
        bad = {"target": {"kind": "point", "x": float("inf"), "y": 1},
               "confidence": 0.9, "model_call": None}
        with patch.object(VFA, "_call_vision_model", return_value=bad):
            result = VFA.execute_action(self.record, str(self.png), self.tmp, [],
                                         confidence_threshold=0.5)
        self.assertEqual(VFA.check(result, VFA._GROUNDING_SCHEMA), [])

    def test_a_well_formed_grounding_still_passes_through_unchanged(self):
        good = {"target": {"kind": "point", "x": 12, "y": 34},
                "confidence": 0.9, "model_call": {"model": "fake"}}
        with patch.object(VFA, "_call_vision_model", return_value=good):
            result = VFA.execute_action(self.record, str(self.png), self.tmp, [],
                                         confidence_threshold=0.5)
        self.assertEqual(result["status"], "GROUNDED")
        self.assertEqual(result["target"], {"kind": "point", "x": 12, "y": 34})

    def test_the_ordinary_not_attempted_path_is_unaffected(self):
        result = VFA.execute_action(self.record, str(self.png), self.tmp, [])
        self.assertEqual(result["status"], "NOT_ATTEMPTED")

    # --- MAJOR: semantic_tree_provided recorded a claim, not a fact ------

    def test_a_nonexistent_semantic_tree_no_longer_reports_itself_as_provided(self):
        # The flag was `semantic_tree_path is not None`: the path was never
        # opened, read or checked, so a nonexistent path still set it true.
        # The screenshot argument beside it has always been held to a real
        # read (MW.screenshot_identity FAILs honestly on an unreadable
        # file); this field is now held to the same standard.
        result = VFA.execute_action(self.record, str(self.png), self.tmp, [],
                                     semantic_tree_path=str(self.tmp / "missing.json"))
        self.assertEqual(result["status"], "FAIL")
        self.assertFalse(result["evidence"]["semantic_tree_provided"])
        self.assertIn("semantic tree not found or unreadable", result["detail"])

    def test_a_real_semantic_tree_is_reported_as_provided(self):
        tree = self.tmp / "tree.json"
        tree.write_text(json.dumps({"root": []}))
        result = VFA.execute_action(self.record, str(self.png), self.tmp, [],
                                     semantic_tree_path=str(tree))
        self.assertTrue(result["evidence"]["semantic_tree_provided"])
        self.assertEqual(result["status"], "NOT_ATTEMPTED")

    def test_no_semantic_tree_reports_false(self):
        result = VFA.execute_action(self.record, str(self.png), self.tmp, [])
        self.assertFalse(result["evidence"]["semantic_tree_provided"])

    def test_a_directory_passed_as_a_semantic_tree_fails_honestly(self):
        result = VFA.execute_action(self.record, str(self.png), self.tmp, [],
                                     semantic_tree_path=str(self.tmp))
        self.assertEqual(result["status"], "FAIL")
        self.assertFalse(result["evidence"]["semantic_tree_provided"])


if __name__ == "__main__":
    unittest.main()

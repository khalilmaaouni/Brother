#!/usr/bin/env python3
"""Tests for mobile_screen_observation.py (EPIC M5.01).

Run this file directly (python3 scripts/test_mobile_screen_observation.py -v),
never through `python3 -m unittest` from another working directory: a suite run
with PYTHONPATH pointing elsewhere tests that directory's copy of the module,
a lesson this estate has already paid for once.

Every adversarial case below is drawn from a defect really found on this
estate, not an imagined one: the non-dict crash class (three modules), the NaN
that passed every range check (two adapters), and the unverified path recorded
as a verified fact (M3.06's semantic_tree_provided).
"""
import ast
import json
import math
import os
import struct
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import mobile_screen_observation as MSO  # noqa: E402
import contract_check as CC  # noqa: E402

#: A real PNG header: signature, IHDR chunk length and type, then width and
#: height as big-endian uint32. screenshot_identity() reads the pixel
#: dimensions from exactly these bytes, so this is a genuine header rather
#: than a stub the module is told to trust.
PNG_WIDTH, PNG_HEIGHT = 1179, 2556


def png_bytes(width=PNG_WIDTH, height=PNG_HEIGHT):
    return (b"\x89PNG\r\n\x1a\n" + struct.pack(">I", 13) + b"IHDR"
            + struct.pack(">II", width, height))


BASE_RECORD = {
    "schema_version": "mobile-screen-observation-v1",
    "observed_at": "2026-09-16T12:00:00+00:00",
    "driver_id": "native-ios-simctl",
    "status": "OBSERVED",
    "screenshot": {
        "path": "/tmp/shot.png",
        "size": 24,
        "sha256": "0" * 64,
        "width": PNG_WIDTH,
        "height": PNG_HEIGHT,
        "visual_quality": "NO-DATA",
    },
    "tree": {
        "kind": "accessibility_tree",
        "identity": {"path": "/tmp/tree.json", "size": 12, "sha256": "1" * 64},
    },
    "viewport": {
        "width": PNG_WIDTH,
        "height": PNG_HEIGHT,
        "unit": "pixels",
        "scale": None,
    },
    "locale": "ja-JP",
    "targets": [
        {
            "selector_type": "accessibility_id",
            "value": "start_button",
            "geometry": {"kind": "point", "x": 590.0, "y": 1200.0},
            "stability": "tree_selector",
        }
    ],
    "detail": "screen observed",
}

DRIVER_CONTRACT = {
    "schema_version": "mobile-driver-contract-v1",
    "driver_id": "native-ios-simctl",
    "driver_name": "Native iOS Driver",
    "action_vocabulary_ref": "mobile-canonical-action-v1",
    "supported_actions": ["TAP_TARGET"],
    "platforms": ["ios"],
    "device_modes": ["simulator"],
    "remote_sessions_supported": False,
    "observations": ["screenshot", "accessibility_tree"],
    "deterministic_selector_support": True,
    "visual_grounding_support": False,
    "risk_classes": [{"action": "TAP_TARGET", "risk_class": "reversible"}],
}


class ScreenObservationTests(unittest.TestCase):
    def setUp(self):
        self.schema = CC.load_json(MSO.DEFAULT_SCHEMA, "schema")

    def record(self, **overrides):
        rec = json.loads(json.dumps(BASE_RECORD))
        rec.update(overrides)
        return rec

    # ---- the schema itself -------------------------------------------------

    def test_a_valid_record_passes(self):
        self.assertEqual(MSO.check(self.record(), self.schema), [])

    def test_wrong_schema_version_is_refused(self):
        problems = MSO.check(
            self.record(schema_version="mobile-screen-observation-v0"), self.schema)
        self.assertTrue(problems)

    def test_missing_required_field_is_refused(self):
        rec = self.record()
        del rec["viewport"]
        problems = MSO.check(rec, self.schema)
        self.assertTrue(any("viewport" in p for p in problems), problems)

    def test_unknown_top_level_field_is_refused(self):
        rec = self.record()
        rec["perceived_intent"] = "start the flow"
        problems = MSO.check(rec, self.schema)
        self.assertTrue(any("perceived_intent" in p for p in problems), problems)

    def test_screenshot_observation_kind_is_not_a_legal_tree_kind(self):
        rec = self.record()
        rec["tree"]["kind"] = "screenshot"
        problems = MSO.check(rec, self.schema)
        self.assertTrue(any("tree.kind" in p for p in problems), problems)

    def test_a_null_tree_and_null_screenshot_record_passes(self):
        rec = self.record(screenshot=None, tree=None, viewport=None, targets=[])
        self.assertEqual(MSO.check(rec, self.schema), [])

    # ---- the whole record is not a dict ------------------------------------

    def test_non_dict_records_fail_cleanly(self):
        for bad in ([], "a string", 7, None, ["x"], 0.5):
            with self.subTest(bad=bad):
                problems = MSO.check(bad, self.schema)
                self.assertTrue(problems, "%r should not validate" % (bad,))
                self.assertEqual(MSO.hand_rules(bad), [])

    # ---- hand rule: tree kind must be advertised ---------------------------

    def test_tree_kind_the_driver_does_not_advertise_is_refused(self):
        rec = self.record()
        rec["tree"]["kind"] = "semantic_tree"
        problems = MSO.check(rec, self.schema, DRIVER_CONTRACT)
        self.assertTrue(any("does not advertise" in p for p in problems), problems)

    def test_tree_kind_the_driver_advertises_is_accepted(self):
        self.assertEqual(MSO.check(self.record(), self.schema, DRIVER_CONTRACT), [])

    def test_the_driver_cross_check_is_skipped_not_failed_without_a_contract(self):
        rec = self.record()
        rec["tree"]["kind"] = "semantic_tree"
        self.assertEqual(MSO.check(rec, self.schema), [])

    # ---- hand rule: every coordinate must be finite ------------------------

    def test_nan_point_coordinates_are_refused(self):
        rec = self.record()
        rec["targets"][0]["geometry"] = {
            "kind": "point", "x": float("nan"), "y": float("nan")}
        problems = MSO.check(rec, self.schema)
        self.assertTrue(any("finite" in p for p in problems), problems)

    def test_the_modules_own_output_carries_no_nan_or_infinity_token(self):
        rec = self.record()
        rec["targets"][0]["geometry"] = {
            "kind": "point", "x": float("nan"), "y": float("inf")}
        problems = MSO.check(rec, self.schema)
        self.assertTrue(problems)
        # The protected property is that the module's own output is strict RFC
        # 8259 JSON, not that the letters NaN never appear: the problem strings
        # explain NaN in prose, and prose inside a quoted JSON string is valid.
        # A BARE NaN/Infinity token is what breaks a strict reader downstream,
        # and allow_nan=False raises on exactly that, so a clean round trip is
        # the real assertion.
        serialized = json.dumps(problems, allow_nan=False)
        self.assertEqual(json.loads(serialized), problems)
        self.assertTrue(all(isinstance(p, str) for p in problems), problems)

    def test_infinite_bbox_coordinate_is_refused(self):
        rec = self.record()
        rec["targets"][0]["geometry"] = {
            "kind": "bbox", "x0": 0, "y0": 0, "x1": float("inf"), "y1": 10}
        problems = MSO.check(rec, self.schema)
        self.assertTrue(any("finite" in p for p in problems), problems)

    def test_nan_viewport_dimension_is_refused(self):
        rec = self.record()
        rec["viewport"]["width"] = float("nan")
        problems = MSO.check(rec, self.schema)
        self.assertTrue(any("viewport.width" in p for p in problems), problems)

    def test_negative_coordinate_is_refused(self):
        rec = self.record()
        rec["targets"][0]["geometry"] = {"kind": "point", "x": -1, "y": 10}
        problems = MSO.check(rec, self.schema)
        self.assertTrue(any("non-negative" in p for p in problems), problems)

    def test_unknown_geometry_kind_is_refused(self):
        rec = self.record()
        rec["targets"][0]["geometry"] = {"kind": "circle", "x": 1, "y": 2}
        problems = MSO.check(rec, self.schema)
        self.assertTrue(any("geometry.kind" in p for p in problems), problems)

    # ---- hand rule: geometry alone never justifies a stable target ---------

    def test_stability_stronger_than_visual_only_with_geometry_alone_is_refused(self):
        rec = self.record()
        rec["targets"] = [{
            "selector_type": None,
            "value": None,
            "geometry": {"kind": "point", "x": 10, "y": 20},
            "stability": "tree_selector",
        }]
        problems = MSO.check(rec, self.schema)
        self.assertTrue(any("selector_type" in p for p in problems), problems)
        self.assertTrue(any("value" in p for p in problems), problems)

    def test_visual_only_target_with_geometry_alone_is_accepted(self):
        rec = self.record()
        rec["targets"] = [{
            "selector_type": None,
            "value": None,
            "geometry": {"kind": "bbox", "x0": 1, "y0": 2, "x1": 3, "y1": 4},
            "stability": "visual_only",
        }]
        self.assertEqual(MSO.check(rec, self.schema), [])

    def test_an_empty_selector_value_cannot_carry_a_stable_stability(self):
        rec = self.record()
        rec["targets"][0]["value"] = "   "
        problems = MSO.check(rec, self.schema)
        self.assertTrue(any("targets[0].value" in p for p in problems), problems)

    # ---- hand rule: viewport unit and scale --------------------------------

    def test_points_viewport_with_a_null_scale_is_refused(self):
        rec = self.record()
        rec["viewport"] = {"width": 393, "height": 852, "unit": "points", "scale": None}
        problems = MSO.check(rec, self.schema)
        self.assertTrue(any("viewport.scale" in p for p in problems), problems)

    def test_points_viewport_with_a_real_scale_is_accepted(self):
        rec = self.record()
        rec["viewport"] = {"width": 393, "height": 852, "unit": "points", "scale": 3.0}
        self.assertEqual(MSO.check(rec, self.schema), [])

    def test_pixels_viewport_with_a_null_scale_is_accepted(self):
        rec = self.record()
        rec["viewport"] = {
            "width": PNG_WIDTH, "height": PNG_HEIGHT, "unit": "pixels", "scale": None}
        self.assertEqual(MSO.check(rec, self.schema), [])

    def test_zero_scale_is_refused(self):
        rec = self.record()
        rec["viewport"] = {"width": 393, "height": 852, "unit": "points", "scale": 0}
        problems = MSO.check(rec, self.schema)
        self.assertTrue(any("greater than zero" in p for p in problems), problems)

    # ---- hand rule: a null screenshot with targets is a contradiction ------

    def test_targets_without_a_screenshot_are_refused(self):
        rec = self.record(screenshot=None, viewport=None)
        problems = MSO.check(rec, self.schema)
        self.assertTrue(any("contradiction" in p for p in problems), problems)

    def test_no_screenshot_and_no_targets_is_not_a_contradiction(self):
        rec = self.record(screenshot=None, viewport=None, targets=[])
        self.assertEqual(MSO.check(rec, self.schema), [])

    # ---- observe(): a path is never recorded as a verified fact ------------

    def test_observe_records_a_real_screenshot_and_a_pixel_viewport(self):
        with tempfile.TemporaryDirectory() as tmp:
            shot = os.path.join(tmp, "shot.png")
            with open(shot, "wb") as fh:
                fh.write(png_bytes())
            rec = MSO.observe("native-ios-simctl", screenshot_path=shot)
            self.assertEqual(rec["status"], "OBSERVED")
            self.assertEqual(rec["screenshot"]["width"], PNG_WIDTH)
            self.assertEqual(rec["screenshot"]["height"], PNG_HEIGHT)
            self.assertEqual(MSO.check(rec, self.schema), [])

    def test_observe_never_converts_a_pixel_viewport_into_points(self):
        with tempfile.TemporaryDirectory() as tmp:
            shot = os.path.join(tmp, "shot.png")
            with open(shot, "wb") as fh:
                fh.write(png_bytes())
            rec = MSO.observe("native-ios-simctl", screenshot_path=shot)
            viewport = rec["viewport"]
            self.assertEqual(viewport["unit"], "pixels")
            self.assertIsNone(viewport["scale"])
            # The PNG header's own numbers, undivided: a converted viewport
            # would read 393x852 at scale 3, which is precisely the guess this
            # unit refuses to make.
            self.assertEqual(viewport["width"], PNG_WIDTH)
            self.assertEqual(viewport["height"], PNG_HEIGHT)

    def test_observe_refuses_a_screenshot_that_is_not_a_png(self):
        with tempfile.TemporaryDirectory() as tmp:
            shot = os.path.join(tmp, "shot.png")
            with open(shot, "wb") as fh:
                fh.write(b"this is not a png, it is twenty four bytes long..")
            rec = MSO.observe("native-ios-simctl", screenshot_path=shot)
            self.assertEqual(rec["status"], "FAIL")
            self.assertIsNone(rec["screenshot"])
            self.assertIn("PNG", rec["detail"])
            self.assertEqual(MSO.check(rec, self.schema), [])

    def test_observe_refuses_an_unreadable_screenshot_path(self):
        with tempfile.TemporaryDirectory() as tmp:
            rec = MSO.observe("native-ios-simctl",
                              screenshot_path=os.path.join(tmp, "absent.png"))
            self.assertEqual(rec["status"], "FAIL")
            self.assertIsNone(rec["screenshot"])
            self.assertEqual(MSO.check(rec, self.schema), [])

    def test_observe_does_not_claim_a_tree_for_a_path_that_does_not_exist(self):
        with tempfile.TemporaryDirectory() as tmp:
            shot = os.path.join(tmp, "shot.png")
            with open(shot, "wb") as fh:
                fh.write(png_bytes())
            rec = MSO.observe("native-ios-simctl", screenshot_path=shot,
                              tree_path=os.path.join(tmp, "absent.json"),
                              tree_kind="accessibility_tree")
            # The M3.06 defect, not repeated: a named path is not an observed
            # tree.
            self.assertIsNone(rec["tree"])
            self.assertEqual(rec["status"], "FAIL")
            self.assertEqual(MSO.check(rec, self.schema), [])

    def test_observe_records_a_real_tree_it_actually_read(self):
        with tempfile.TemporaryDirectory() as tmp:
            shot = os.path.join(tmp, "shot.png")
            with open(shot, "wb") as fh:
                fh.write(png_bytes())
            tree = os.path.join(tmp, "tree.json")
            with open(tree, "w", encoding="utf-8") as fh:
                fh.write('{"root": []}')
            rec = MSO.observe("native-ios-simctl", screenshot_path=shot,
                              tree_path=tree, tree_kind="semantic_tree")
            self.assertEqual(rec["status"], "OBSERVED")
            self.assertEqual(rec["tree"]["kind"], "semantic_tree")
            self.assertEqual(len(rec["tree"]["identity"]["sha256"]), 64)
            self.assertEqual(MSO.check(rec, self.schema), [])

    def test_observe_refuses_an_unknown_tree_kind(self):
        with tempfile.TemporaryDirectory() as tmp:
            tree = os.path.join(tmp, "tree.json")
            with open(tree, "w", encoding="utf-8") as fh:
                fh.write("{}")
            rec = MSO.observe("native-ios-simctl", tree_path=tree,
                              tree_kind="video")
            self.assertEqual(rec["status"], "FAIL")
            self.assertIsNone(rec["tree"])

    def test_observe_defaults_locale_to_null_rather_than_a_device_default(self):
        rec = MSO.observe("native-ios-simctl")
        self.assertIsNone(rec["locale"])
        self.assertEqual(rec["targets"], [])
        self.assertEqual(MSO.check(rec, self.schema), [])

    def test_observe_output_is_strict_json(self):
        with tempfile.TemporaryDirectory() as tmp:
            shot = os.path.join(tmp, "shot.png")
            with open(shot, "wb") as fh:
                fh.write(png_bytes())
            rec = MSO.observe("native-ios-simctl", screenshot_path=shot)
            json.dumps(rec, allow_nan=False)

    # ---- the module never reaches a device or a model ---------------------

    def test_the_module_imports_nothing_that_reaches_a_device_or_a_network(self):
        # Assert over what the module really IMPORTS, read from its own parse
        # tree, not over whether a word appears in its prose: the docstring
        # says "no subprocess" in English, and a substring scan would fail on
        # the module for documenting the very guarantee it keeps.
        with open(MSO.__file__, encoding="utf-8") as fh:
            tree = ast.parse(fh.read())
        imported = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                imported.update(alias.name.split(".")[0] for alias in node.names)
            elif isinstance(node, ast.ImportFrom) and node.module:
                imported.add(node.module.split(".")[0])
        for banned in ("subprocess", "urllib", "http", "requests", "socket", "shutil"):
            self.assertNotIn(banned, imported,
                             "M5.01 observes records and files only; %r has no place "
                             "in it" % banned)

    def test_the_finiteness_predicate_is_shared_not_per_field(self):
        # A per-field range check is how NaN got through twice on this estate.
        self.assertIsNone(MSO._coordinate_problem("x", "y", 1.0))
        self.assertIn("finite", MSO._coordinate_problem("x", "y", math.nan))
        self.assertIn("finite", MSO._coordinate_problem("x", "y", math.inf))
        self.assertIn("number", MSO._coordinate_problem("x", "y", True))

    # ---- the CLI exit contract --------------------------------------------

    def test_main_exit_codes(self):
        with tempfile.TemporaryDirectory() as tmp:
            good = os.path.join(tmp, "good.json")
            with open(good, "w", encoding="utf-8") as fh:
                json.dump(self.record(), fh)
            self.assertEqual(MSO.main([good]), 0)

            bad = os.path.join(tmp, "bad.json")
            rec = self.record()
            rec["viewport"] = {
                "width": 393, "height": 852, "unit": "points", "scale": None}
            with open(bad, "w", encoding="utf-8") as fh:
                json.dump(rec, fh)
            self.assertEqual(MSO.main([bad]), 1)

            self.assertEqual(MSO.main([os.path.join(tmp, "absent.json")]), 2)

    def test_main_passes_the_driver_contract_through(self):
        with tempfile.TemporaryDirectory() as tmp:
            rec = self.record()
            rec["tree"]["kind"] = "semantic_tree"
            record_path = os.path.join(tmp, "r.json")
            with open(record_path, "w", encoding="utf-8") as fh:
                json.dump(rec, fh)
            contract_path = os.path.join(tmp, "d.json")
            with open(contract_path, "w", encoding="utf-8") as fh:
                json.dump(DRIVER_CONTRACT, fh)
            self.assertEqual(MSO.main([record_path]), 0)
            self.assertEqual(
                MSO.main([record_path, "--driver-contract", contract_path]), 1)


if __name__ == "__main__":
    unittest.main()

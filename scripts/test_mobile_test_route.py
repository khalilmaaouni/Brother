#!/usr/bin/env python3
"""Test for mobile_test_route.py (EPIC M2.03). Every route record here is
a synthetic, throwaway dict/file: no real account, project, or app name
is read or referenced anywhere in this file."""
import json
import os
import subprocess
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import mobile_test_route as MTR  # noqa: E402
import contract_check as CC  # noqa: E402


def _valid_route(**overrides):
    route = {
        "schema_version": "mobile-test-route-v1",
        "route_id": "jump-to-expired-trial-paywall",
        "description": "Skips onboarding and lands directly on the "
                        "expired-trial paywall screen for paywall-copy "
                        "journey tests.",
        "target_state": {
            "description": "Account mid-lifecycle with an expired trial "
                            "and no active subscription.",
        },
        "entry_mechanism": {
            "mechanism": "deep-link-url-scheme",
            "identifier": "brothertest://route/expired-trial-paywall",
        },
        "build_fence": {
            "fence_mechanism": "build-flag",
            "fence_identifier": "BROTHER_TEST_ROUTES_ENABLED",
            "release_build_excluded": True,
            "verification_note": "CI asserts BROTHER_TEST_ROUTES_ENABLED "
                                  "is unset in the release scheme's build "
                                  "settings before a release build is "
                                  "signed.",
        },
        "route_parameters": [],
        "exercises_real_path": {
            "classification": "partial-real-path-shortcut",
            "rationale": "Skips the real onboarding flow; a journey proof "
                         "using this route is not evidence for onboarding "
                         "itself, only for the paywall screen onward.",
        },
        "production_exposure_review": {
            "reviewer": "mobile platform lead",
            "risk_if_shipped": "A user could reach the paywall in a "
                                "misleading state without completing "
                                "onboarding, confusing support tickets but "
                                "no data exposure.",
            "residual_risk_accepted": False,
        },
    }
    route.update(overrides)
    return route


class MobileTestRouteTests(unittest.TestCase):

    def setUp(self):
        self.schema = CC.load_json(MTR.DEFAULT_SCHEMA, "mobile-test-route-v1 schema")

    # --- basic shape -----------------------------------------------

    def test_minimal_valid_route_passes(self):
        route = _valid_route()
        self.assertEqual(MTR.check(route, self.schema), [])

    def test_schema_version_const_is_enforced(self):
        route = _valid_route(schema_version="wrong-version")
        problems = MTR.check(route, self.schema)
        self.assertTrue(any("schema_version" in p for p in problems))

    def test_unknown_top_level_field_is_rejected(self):
        route = _valid_route()
        route["unexpected_field"] = "nope"
        problems = MTR.check(route, self.schema)
        self.assertTrue(any("unexpected_field" in p for p in problems))

    def test_empty_route_id_is_rejected(self):
        route = _valid_route(route_id="   ")
        problems = MTR.check(route, self.schema)
        self.assertTrue(any("route_id" in p for p in problems))

    # --- THE FENCE: no fence means refused, structurally -----------

    def test_route_with_no_build_fence_key_at_all_is_refused(self):
        route = _valid_route()
        del route["build_fence"]
        problems = MTR.check(route, self.schema)
        self.assertTrue(any("build_fence" in p for p in problems),
                         "a route with no build_fence key must be "
                         "structurally refused, got: %r" % problems)

    def test_release_build_excluded_false_is_refused(self):
        route = _valid_route()
        route["build_fence"]["release_build_excluded"] = False
        problems = MTR.check(route, self.schema)
        self.assertTrue(any("release_build_excluded" in p for p in problems))

    def test_release_build_excluded_missing_is_refused(self):
        route = _valid_route()
        del route["build_fence"]["release_build_excluded"]
        problems = MTR.check(route, self.schema)
        self.assertTrue(any("release_build_excluded" in p for p in problems))

    def test_empty_fence_identifier_is_refused_even_though_type_matches(self):
        # THE ADVERSARIAL CASE: the schema's type:string alone would let
        # "" through (no minLength in the enforced keyword subset). This
        # is exactly the "fence field present but empty and still
        # passes" shape the hand rules exist to close.
        route = _valid_route()
        route["build_fence"]["fence_identifier"] = ""
        problems = MTR.check(route, self.schema)
        self.assertTrue(any("fence_identifier" in p for p in problems))

    def test_whitespace_only_fence_identifier_is_refused(self):
        route = _valid_route()
        route["build_fence"]["fence_identifier"] = "   "
        problems = MTR.check(route, self.schema)
        self.assertTrue(any("fence_identifier" in p for p in problems))

    def test_empty_verification_note_is_refused(self):
        route = _valid_route()
        route["build_fence"]["verification_note"] = ""
        problems = MTR.check(route, self.schema)
        self.assertTrue(any("verification_note" in p for p in problems))

    def test_placeholder_verification_note_is_refused(self):
        for placeholder in ("TODO", "tbd", "n/a", "unknown", "  none  "):
            with self.subTest(placeholder=placeholder):
                route = _valid_route()
                route["build_fence"]["verification_note"] = placeholder
                problems = MTR.check(route, self.schema)
                self.assertTrue(
                    any("verification_note" in p and "placeholder" in p
                        for p in problems),
                    "placeholder %r should be refused, got: %r"
                    % (placeholder, problems))

    def test_placeholder_fence_identifier_is_refused(self):
        route = _valid_route()
        route["build_fence"]["fence_identifier"] = "TBD"
        problems = MTR.check(route, self.schema)
        self.assertTrue(
            any("fence_identifier" in p and "placeholder" in p
                for p in problems))

    def test_real_fence_content_passes(self):
        route = _valid_route()
        self.assertEqual(MTR.check(route, self.schema), [])

    # --- route_parameters, by type ----------------------------------

    def test_enum_parameter_without_allowed_values_is_refused(self):
        route = _valid_route(route_parameters=[{
            "name": "variant", "type": "enum", "required": True,
            "description": "which variant", "validation": {},
        }])
        problems = MTR.check(route, self.schema)
        self.assertTrue(any("allowed_values" in p for p in problems))

    def test_enum_parameter_with_empty_allowed_values_is_refused(self):
        route = _valid_route(route_parameters=[{
            "name": "variant", "type": "enum", "required": True,
            "description": "which variant",
            "validation": {"allowed_values": []},
        }])
        problems = MTR.check(route, self.schema)
        self.assertTrue(any("allowed_values" in p for p in problems))

    def test_enum_parameter_with_duplicate_allowed_values_is_refused(self):
        route = _valid_route(route_parameters=[{
            "name": "variant", "type": "enum", "required": True,
            "description": "which variant",
            "validation": {"allowed_values": ["a", "a"]},
        }])
        problems = MTR.check(route, self.schema)
        self.assertTrue(any("duplicated" in p for p in problems))

    def test_enum_parameter_valid_passes(self):
        route = _valid_route(route_parameters=[{
            "name": "variant", "type": "enum", "required": True,
            "description": "which variant",
            "validation": {"allowed_values": ["a", "b"]},
        }])
        self.assertEqual(MTR.check(route, self.schema), [])

    def test_string_parameter_without_max_length_is_refused(self):
        route = _valid_route(route_parameters=[{
            "name": "note", "type": "string", "required": False,
            "description": "free text", "validation": {},
        }])
        problems = MTR.check(route, self.schema)
        self.assertTrue(any("max_length" in p for p in problems))

    def test_string_parameter_with_nonpositive_max_length_is_refused(self):
        route = _valid_route(route_parameters=[{
            "name": "note", "type": "string", "required": False,
            "description": "free text",
            "validation": {"max_length": 0},
        }])
        problems = MTR.check(route, self.schema)
        self.assertTrue(any("max_length" in p for p in problems))

    def test_string_parameter_valid_passes(self):
        route = _valid_route(route_parameters=[{
            "name": "note", "type": "string", "required": False,
            "description": "free text",
            "validation": {"max_length": 64},
        }])
        self.assertEqual(MTR.check(route, self.schema), [])

    def test_integer_parameter_missing_bounds_is_refused(self):
        route = _valid_route(route_parameters=[{
            "name": "days_expired", "type": "integer", "required": True,
            "description": "days since trial expired", "validation": {},
        }])
        problems = MTR.check(route, self.schema)
        self.assertTrue(any("min_value" in p for p in problems))
        self.assertTrue(any("max_value" in p for p in problems))

    def test_integer_parameter_min_greater_than_max_is_refused(self):
        route = _valid_route(route_parameters=[{
            "name": "days_expired", "type": "integer", "required": True,
            "description": "days since trial expired",
            "validation": {"min_value": 10, "max_value": 1},
        }])
        problems = MTR.check(route, self.schema)
        self.assertTrue(any("min_value" in p and "max_value" in p
                             for p in problems))

    def test_integer_parameter_valid_passes(self):
        route = _valid_route(route_parameters=[{
            "name": "days_expired", "type": "integer", "required": True,
            "description": "days since trial expired",
            "validation": {"min_value": 0, "max_value": 365},
        }])
        self.assertEqual(MTR.check(route, self.schema), [])

    def test_boolean_parameter_valid_passes(self):
        route = _valid_route(route_parameters=[{
            "name": "seed_receipts", "type": "boolean", "required": False,
            "description": "whether to seed a receipt history",
            "validation": {},
        }])
        self.assertEqual(MTR.check(route, self.schema), [])

    def test_boolean_parameter_with_stray_validation_field_is_refused(self):
        route = _valid_route(route_parameters=[{
            "name": "seed_receipts", "type": "boolean", "required": False,
            "description": "whether to seed a receipt history",
            "validation": {"max_length": 10},
        }])
        problems = MTR.check(route, self.schema)
        self.assertTrue(any("max_length" in p and "only allowed when" in p
                             for p in problems))

    def test_wrong_validation_field_for_type_is_refused(self):
        # A stale allowed_values left over after an author switched a
        # parameter's type away from 'enum'.
        route = _valid_route(route_parameters=[{
            "name": "note", "type": "string", "required": False,
            "description": "free text",
            "validation": {"max_length": 10, "allowed_values": ["x"]},
        }])
        problems = MTR.check(route, self.schema)
        self.assertTrue(any("allowed_values" in p and "only allowed when" in p
                             for p in problems))

    # --- exercises_real_path / production_exposure_review ----------

    def test_empty_rationale_is_refused(self):
        route = _valid_route()
        route["exercises_real_path"]["rationale"] = ""
        problems = MTR.check(route, self.schema)
        self.assertTrue(any("exercises_real_path.rationale" in p
                             for p in problems))

    def test_bypasses_verification_without_accepted_risk_is_refused(self):
        route = _valid_route()
        route["exercises_real_path"]["classification"] = "bypasses-verification"
        route["production_exposure_review"]["residual_risk_accepted"] = False
        problems = MTR.check(route, self.schema)
        self.assertTrue(any("residual_risk_accepted" in p for p in problems))

    def test_bypasses_verification_with_accepted_risk_passes(self):
        route = _valid_route()
        route["exercises_real_path"]["classification"] = "bypasses-verification"
        route["production_exposure_review"]["residual_risk_accepted"] = True
        self.assertEqual(MTR.check(route, self.schema), [])

    def test_full_real_path_does_not_require_accepted_risk(self):
        route = _valid_route()
        route["exercises_real_path"]["classification"] = "full-real-path"
        route["production_exposure_review"]["residual_risk_accepted"] = False
        self.assertEqual(MTR.check(route, self.schema), [])

    def test_empty_reviewer_is_refused(self):
        route = _valid_route()
        route["production_exposure_review"]["reviewer"] = ""
        problems = MTR.check(route, self.schema)
        self.assertTrue(any("production_exposure_review.reviewer" in p
                             for p in problems))

    def test_empty_risk_if_shipped_is_refused(self):
        route = _valid_route()
        route["production_exposure_review"]["risk_if_shipped"] = ""
        problems = MTR.check(route, self.schema)
        self.assertTrue(any("risk_if_shipped" in p for p in problems))

    # --- PR #727 review: crash sites now clean FAILs, never exceptions --

    def test_dict_entry_in_allowed_values_is_a_clean_fail_not_a_crash(self):
        # Adversarial case from the PR #727 review: an unhashable dict in
        # allowed_values used to raise TypeError from `seen.add(v)`
        # before check() routed through CC.checked().
        route = _valid_route(route_parameters=[{
            "name": "variant", "type": "enum", "required": True,
            "description": "which variant",
            "validation": {"allowed_values": [{"nested": "dict"}]},
        }])
        problems = MTR.check(route, self.schema)
        self.assertTrue(any("allowed_values" in p for p in problems))

    def test_non_dict_record_is_a_clean_fail_not_a_crash(self):
        # Adversarial case from the PR #727 review: a record file that is
        # a JSON list or string used to raise AttributeError from
        # `.get()` on a non-dict before check() routed through
        # CC.checked().
        problems = MTR.check(["not", "a", "dict"], self.schema)
        self.assertTrue(any("object" in p for p in problems))

    # --- PR #727 review: reassurance-phrase bypass ---------------------

    def test_reassurance_phrase_verification_note_is_refused(self):
        # "nobody will guess this URL" is the reviewer's exact case: it
        # does not exactly match a _PLACEHOLDER_VALUES entry, but it is
        # exactly the phrase render_route_docs's own commitment block
        # warns against emitting as output.
        route = _valid_route()
        route["build_fence"]["verification_note"] = "nobody will guess this URL"
        problems = MTR.check(route, self.schema)
        self.assertTrue(
            any("verification_note" in p and "reassurance" in p
                for p in problems), problems)

    # --- PR #727 review: mechanism/identifier cross-check --------------

    def test_universal_link_path_wildcard_slash_is_refused(self):
        route = _valid_route()
        route["entry_mechanism"] = {
            "mechanism": "universal-link-path", "identifier": "/",
        }
        problems = MTR.check(route, self.schema)
        self.assertTrue(any("entry_mechanism.identifier" in p for p in problems))

    def test_universal_link_path_wildcard_star_is_refused(self):
        route = _valid_route()
        route["entry_mechanism"] = {
            "mechanism": "universal-link-path", "identifier": "/*",
        }
        problems = MTR.check(route, self.schema)
        self.assertTrue(any("entry_mechanism.identifier" in p for p in problems))

    def test_universal_link_path_real_path_passes(self):
        route = _valid_route()
        route["entry_mechanism"] = {
            "mechanism": "universal-link-path",
            "identifier": "/test/expired-trial-paywall",
        }
        self.assertEqual(MTR.check(route, self.schema), [])

    def test_identifier_with_shell_metacharacters_is_refused(self):
        route = _valid_route()
        route["entry_mechanism"]["identifier"] = "brothertest://x;rm -rf /"
        problems = MTR.check(route, self.schema)
        self.assertTrue(any("entry_mechanism.identifier" in p for p in problems))

    # --- PR #727 review: duplicate parameter names ----------------------

    def test_duplicate_parameter_names_with_contradictory_types_is_refused(self):
        route = _valid_route(route_parameters=[
            {"name": "days", "type": "integer", "required": True,
             "description": "d1", "validation": {"min_value": 0, "max_value": 10}},
            {"name": "days", "type": "string", "required": False,
             "description": "d2", "validation": {"max_length": 5}},
        ])
        problems = MTR.check(route, self.schema)
        self.assertTrue(any("duplicated" in p and "route_parameters" in p
                             for p in problems), problems)

    # --- PR #727 review: Markdown injection in generated docs -----------

    def test_render_route_docs_escapes_pipe_in_description(self):
        route = _valid_route(description="a | b breaks the table")
        problems = MTR.check(route, self.schema)
        self.assertEqual(problems, [])
        guide = MTR.render_route_docs(route, problems)
        self.assertIn("a \\| b breaks the table", guide)

    def test_render_route_docs_strips_newline_in_reviewer(self):
        route = _valid_route()
        route["production_exposure_review"]["reviewer"] = \
            "real reviewer\n# Forged heading"
        problems = MTR.check(route, self.schema)
        self.assertEqual(problems, [])
        guide = MTR.render_route_docs(route, problems)
        self.assertNotIn("\n# Forged heading", guide)

    # --- loading -----------------------------------------------------

    def test_missing_route_file_is_no_data_not_a_crash(self):
        with self.assertRaises(CC.NoData):
            MTR.load_route("/does/not/exist/route.json")

    def test_malformed_json_route_file_is_no_data_not_a_crash(self):
        with tempfile.NamedTemporaryFile(
                mode="w", suffix=".json", delete=False) as fh:
            fh.write("{not valid json")
            path = fh.name
        try:
            with self.assertRaises(CC.NoData):
                MTR.load_route(path)
        finally:
            os.remove(path)

    # --- the developer-documentation generator -----------------------

    def test_render_route_docs_refuses_when_problems_present(self):
        route = _valid_route()
        with self.assertRaises(ValueError):
            MTR.render_route_docs(route, ["some unresolved problem"])

    def test_render_route_docs_renders_for_a_clean_record(self):
        route = _valid_route()
        problems = MTR.check(route, self.schema)
        self.assertEqual(problems, [])
        guide = MTR.render_route_docs(route, problems)
        self.assertIn(route["route_id"], guide)
        self.assertIn(route["build_fence"]["fence_identifier"], guide)
        self.assertIn(route["build_fence"]["verification_note"], guide)
        self.assertIn("must never be reachable", guide)

    def test_render_route_docs_never_computes_its_own_problems(self):
        # render_route_docs must take the problems list from the caller,
        # not recompute it -- so a caller who forgets to check first
        # gets a hard refusal instead of the function silently trusting
        # an unvalidated record.
        route = _valid_route()
        del route["build_fence"]
        with self.assertRaises(ValueError):
            MTR.render_route_docs(route, ["build_fence: missing required field"])

    def test_render_route_docs_flags_a_non_full_real_path(self):
        route = _valid_route()
        problems = MTR.check(route, self.schema)
        self.assertEqual(problems, [])
        guide = MTR.render_route_docs(route, problems)
        self.assertIn("NOT full", guide)

    # --- CLI -----------------------------------------------------------

    def test_cli_exits_zero_and_prints_json_on_a_valid_route(self):
        with tempfile.NamedTemporaryFile(
                mode="w", suffix=".json", delete=False) as fh:
            json.dump(_valid_route(), fh)
            path = fh.name
        try:
            result = subprocess.run(
                [sys.executable, MTR.__file__, path],
                capture_output=True, text=True)
            self.assertEqual(result.returncode, 0, "stderr: %s" % result.stderr)
            printed = json.loads(result.stdout)
            self.assertEqual(printed["route_id"],
                              "jump-to-expired-trial-paywall")
        finally:
            os.remove(path)

    def test_cli_exits_one_on_a_route_with_no_fence(self):
        route = _valid_route()
        del route["build_fence"]
        with tempfile.NamedTemporaryFile(
                mode="w", suffix=".json", delete=False) as fh:
            json.dump(route, fh)
            path = fh.name
        try:
            result = subprocess.run(
                [sys.executable, MTR.__file__, path],
                capture_output=True, text=True)
            self.assertEqual(result.returncode, 1)
            self.assertIn("build_fence", result.stderr)
        finally:
            os.remove(path)

    def test_cli_exits_two_on_a_missing_route_file(self):
        result = subprocess.run(
            [sys.executable, MTR.__file__, "/does/not/exist/route.json"],
            capture_output=True, text=True)
        self.assertEqual(result.returncode, 2)
        self.assertIn("NO-DATA", result.stderr)

    def test_cli_render_guide_prints_markdown_on_a_valid_route(self):
        with tempfile.NamedTemporaryFile(
                mode="w", suffix=".json", delete=False) as fh:
            json.dump(_valid_route(), fh)
            path = fh.name
        try:
            result = subprocess.run(
                [sys.executable, MTR.__file__, "--render-guide", path],
                capture_output=True, text=True)
            self.assertEqual(result.returncode, 0, "stderr: %s" % result.stderr)
            self.assertIn("# Test route:", result.stdout)
            self.assertIn("must never be reachable", result.stdout)
        finally:
            os.remove(path)

    def test_cli_render_guide_refuses_on_an_invalid_route(self):
        route = _valid_route()
        route["build_fence"]["fence_identifier"] = ""
        with tempfile.NamedTemporaryFile(
                mode="w", suffix=".json", delete=False) as fh:
            json.dump(route, fh)
            path = fh.name
        try:
            result = subprocess.run(
                [sys.executable, MTR.__file__, "--render-guide", path],
                capture_output=True, text=True)
            self.assertEqual(result.returncode, 1)
            self.assertNotIn("# Test route:", result.stdout)
            self.assertIn("fence_identifier", result.stderr)
        finally:
            os.remove(path)


if __name__ == "__main__":
    unittest.main()

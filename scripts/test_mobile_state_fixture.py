#!/usr/bin/env python3
"""Test for mobile_state_fixture.py (EPIC M2.01). Every fixture here is a
synthetic, throwaway dict/file: no real account, project, or app name is
read or referenced anywhere in this file."""
import json
import os
import subprocess
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import mobile_state_fixture as MSF  # noqa: E402
import contract_check as CC  # noqa: E402


def _valid_fixture(**overrides):
    fixture = {
        "schema_version": "mobile-state-fixture-v1",
        "fixture_id": "test-journey-fresh-install",
        "account_data_ref": "fixtures/accounts/synthetic-001.json",
        "backend_dataset": {"id": "synthetic-dataset", "version": "1"},
        "app_storage": {"reset_mode": "clean"},
        "keychain_policy": {"mode": "clean"},
        "feature_flags": {"new_onboarding": True},
        "clock": {"mode": "real"},
        "randomness_seed": None,
        "locale": "en-US",
        "timezone": "Asia/Tokyo",
        "network_profile": {"mode": "online"},
        "permissions": {"location": "not-determined"},
        "location": {"mode": "disabled"},
        "notifications": {"mode": "disabled"},
        "external_services": [],
        "cleanup_policy": {"required": False},
    }
    fixture.update(overrides)
    return fixture


class MobileStateFixtureTests(unittest.TestCase):

    def setUp(self):
        self.schema = CC.load_json(MSF.DEFAULT_SCHEMA, "mobile-state-fixture-v1 schema")

    def test_minimal_valid_fixture_passes(self):
        fixture = _valid_fixture()
        self.assertEqual(MSF.check(fixture, self.schema), [])

    def test_schema_version_const_is_enforced(self):
        fixture = _valid_fixture(schema_version="wrong-version")
        problems = MSF.check(fixture, self.schema)
        self.assertTrue(any("schema_version" in p for p in problems))

    def test_unknown_top_level_field_is_rejected(self):
        fixture = _valid_fixture()
        fixture["unexpected_field"] = "nope"
        problems = MSF.check(fixture, self.schema)
        self.assertTrue(any("unexpected_field" in p for p in problems))

    def test_restore_checkpoint_without_checkpoint_ref_fails_hand_rule(self):
        fixture = _valid_fixture(app_storage={"reset_mode": "restore-checkpoint"})
        problems = MSF.check(fixture, self.schema)
        self.assertTrue(any("checkpoint_ref" in p for p in problems))

    def test_restore_checkpoint_with_checkpoint_ref_passes(self):
        fixture = _valid_fixture(app_storage={
            "reset_mode": "restore-checkpoint",
            "checkpoint_ref": "snapshots/onboarding-done",
        })
        self.assertEqual(MSF.check(fixture, self.schema), [])

    def test_preserve_named_keys_without_keys_fails_hand_rule(self):
        fixture = _valid_fixture(app_storage={"reset_mode": "preserve-named-keys"})
        problems = MSF.check(fixture, self.schema)
        self.assertTrue(any("preserved_keys" in p for p in problems))

    def test_stale_checkpoint_ref_left_over_on_clean_mode_is_rejected(self):
        # A fixture author switches reset_mode back to 'clean' but leaves
        # checkpoint_ref set from an earlier edit: this must be refused,
        # not silently ignored, since a reader could mistake it for live.
        fixture = _valid_fixture(app_storage={
            "reset_mode": "clean", "checkpoint_ref": "snapshots/stale"})
        problems = MSF.check(fixture, self.schema)
        self.assertTrue(any("checkpoint_ref" in p and "absent" in p for p in problems))

    def test_stale_preserved_keys_left_over_on_restore_checkpoint_is_rejected(self):
        fixture = _valid_fixture(app_storage={
            "reset_mode": "restore-checkpoint",
            "checkpoint_ref": "snapshots/onboarding-done",
            "preserved_keys": ["stale_key"],
        })
        problems = MSF.check(fixture, self.schema)
        self.assertTrue(any("preserved_keys" in p and "absent" in p for p in problems))

    def test_seeded_keychain_without_items_fails_hand_rule(self):
        fixture = _valid_fixture(keychain_policy={"mode": "seeded"})
        problems = MSF.check(fixture, self.schema)
        self.assertTrue(any("seeded_items" in p for p in problems))

    def test_stale_seeded_items_on_clean_keychain_is_rejected(self):
        fixture = _valid_fixture(keychain_policy={
            "mode": "clean", "seeded_items": ["stale"]})
        problems = MSF.check(fixture, self.schema)
        self.assertTrue(any("seeded_items" in p and "absent" in p for p in problems))

    def test_stale_fixed_value_on_real_clock_is_rejected(self):
        fixture = _valid_fixture(clock={
            "mode": "real", "fixed_value": "2026-01-01T00:00:00Z"})
        problems = MSF.check(fixture, self.schema)
        self.assertTrue(any("clock.fixed_value" in p and "absent" in p for p in problems))

    def test_simulated_location_without_coordinates_fails_hand_rule(self):
        fixture = _valid_fixture(location={"mode": "simulated"})
        problems = MSF.check(fixture, self.schema)
        self.assertTrue(any("latitude/longitude" in p for p in problems))

    def test_simulated_location_with_coordinates_passes(self):
        fixture = _valid_fixture(location={
            "mode": "simulated", "latitude": 35.6, "longitude": 139.7})
        self.assertEqual(MSF.check(fixture, self.schema), [])

    def test_stale_coordinates_on_disabled_location_is_rejected(self):
        fixture = _valid_fixture(location={
            "mode": "disabled", "latitude": 35.6, "longitude": 139.7})
        problems = MSF.check(fixture, self.schema)
        self.assertTrue(any("latitude/longitude" in p and "absent" in p for p in problems))

    def test_stale_method_on_cleanup_not_required_is_rejected(self):
        fixture = _valid_fixture(cleanup_policy={
            "required": False, "method": "reset-app-storage"})
        problems = MSF.check(fixture, self.schema)
        self.assertTrue(any("cleanup_policy.method" in p and "absent" in p for p in problems))

    def test_throttled_network_without_detail_fails_hand_rule(self):
        fixture = _valid_fixture(network_profile={"mode": "throttled"})
        problems = MSF.check(fixture, self.schema)
        self.assertTrue(any("network_profile.detail" in p for p in problems))

    def test_throttled_network_with_detail_passes(self):
        fixture = _valid_fixture(
            network_profile={"mode": "throttled", "detail": "slow-3g"})
        self.assertEqual(MSF.check(fixture, self.schema), [])

    def test_stale_detail_on_online_network_is_rejected(self):
        fixture = _valid_fixture(
            network_profile={"mode": "online", "detail": "slow-3g"})
        problems = MSF.check(fixture, self.schema)
        self.assertTrue(any("network_profile.detail" in p and "absent" in p for p in problems))

    def test_latitude_out_of_range_is_rejected(self):
        fixture = _valid_fixture(location={
            "mode": "simulated", "latitude": 999, "longitude": 0})
        problems = MSF.check(fixture, self.schema)
        self.assertTrue(any("latitude" in p and "range" in p for p in problems))

    def test_longitude_out_of_range_is_rejected(self):
        fixture = _valid_fixture(location={
            "mode": "simulated", "latitude": 0, "longitude": -200})
        problems = MSF.check(fixture, self.schema)
        self.assertTrue(any("longitude" in p and "range" in p for p in problems))

    def test_account_data_ref_that_looks_like_an_email_is_rejected(self):
        fixture = _valid_fixture(account_data_ref="real.user@example.com")
        problems = MSF.check(fixture, self.schema)
        self.assertTrue(any("account_data_ref" in p and "secret" in p for p in problems))

    def test_seeded_item_that_looks_like_a_password_assignment_is_rejected(self):
        fixture = _valid_fixture(keychain_policy={
            "mode": "seeded", "seeded_items": ["password=hunter2"]})
        problems = MSF.check(fixture, self.schema)
        self.assertTrue(any("seeded_items" in p and "secret" in p for p in problems))

    def test_external_service_that_looks_like_a_live_url_is_rejected(self):
        fixture = _valid_fixture(external_services=["https://prod.example.com/api"])
        problems = MSF.check(fixture, self.schema)
        self.assertTrue(any("external_services" in p and "secret" in p for p in problems))

    def test_ordinary_reference_style_values_pass(self):
        fixture = _valid_fixture(
            account_data_ref="fixtures/accounts/synthetic-002.json",
            keychain_policy={"mode": "seeded", "seeded_items": ["api_token_ref"]},
            external_services=["sandbox:payments-v2"])
        self.assertEqual(MSF.check(fixture, self.schema), [])

    def test_fixed_clock_without_fixed_value_fails_hand_rule(self):
        fixture = _valid_fixture(clock={"mode": "fixed"})
        problems = MSF.check(fixture, self.schema)
        self.assertTrue(any("fixed_value" in p for p in problems))

    def test_fixed_clock_with_fixed_value_passes(self):
        fixture = _valid_fixture(clock={
            "mode": "fixed", "fixed_value": "2026-01-01T00:00:00Z"})
        self.assertEqual(MSF.check(fixture, self.schema), [])

    def test_cleanup_required_without_method_fails_hand_rule(self):
        fixture = _valid_fixture(cleanup_policy={"required": True})
        problems = MSF.check(fixture, self.schema)
        self.assertTrue(any("cleanup_policy.method" in p for p in problems))

    def test_cleanup_required_with_method_passes(self):
        fixture = _valid_fixture(
            cleanup_policy={"required": True, "method": "reset-app-storage"})
        self.assertEqual(MSF.check(fixture, self.schema), [])

    def test_permission_value_outside_enum_is_rejected(self):
        fixture = _valid_fixture(permissions={"camera": "yolo"})
        problems = MSF.check(fixture, self.schema)
        self.assertTrue(any("permissions.camera" in p for p in problems))

    def test_feature_flag_non_boolean_value_is_rejected(self):
        fixture = _valid_fixture(feature_flags={"rollout": "50 percent"})
        problems = MSF.check(fixture, self.schema)
        self.assertTrue(any("feature_flags.rollout" in p for p in problems))

    def test_missing_fixture_file_is_no_data_not_a_crash(self):
        with self.assertRaises(CC.NoData):
            MSF.load_fixture("/does/not/exist/fixture.json")

    def test_malformed_json_fixture_file_is_no_data_not_a_crash(self):
        with tempfile.NamedTemporaryFile(
                mode="w", suffix=".json", delete=False) as fh:
            fh.write("{not valid json")
            path = fh.name
        try:
            with self.assertRaises(CC.NoData):
                MSF.load_fixture(path)
        finally:
            os.remove(path)

    def test_cli_exits_zero_and_prints_json_on_a_valid_fixture(self):
        with tempfile.NamedTemporaryFile(
                mode="w", suffix=".json", delete=False) as fh:
            json.dump(_valid_fixture(), fh)
            path = fh.name
        try:
            result = subprocess.run(
                [sys.executable, MSF.__file__, path],
                capture_output=True, text=True)
            self.assertEqual(result.returncode, 0, "stderr: %s" % result.stderr)
            printed = json.loads(result.stdout)
            self.assertEqual(printed["fixture_id"], "test-journey-fresh-install")
        finally:
            os.remove(path)

    def test_cli_exits_one_on_a_fixture_with_a_hand_rule_violation(self):
        with tempfile.NamedTemporaryFile(
                mode="w", suffix=".json", delete=False) as fh:
            json.dump(_valid_fixture(clock={"mode": "fixed"}), fh)
            path = fh.name
        try:
            result = subprocess.run(
                [sys.executable, MSF.__file__, path],
                capture_output=True, text=True)
            self.assertEqual(result.returncode, 1)
            self.assertIn("fixed_value", result.stderr)
        finally:
            os.remove(path)

    def test_cli_exits_two_on_a_missing_fixture_file(self):
        result = subprocess.run(
            [sys.executable, MSF.__file__, "/does/not/exist/fixture.json"],
            capture_output=True, text=True)
        self.assertEqual(result.returncode, 2)
        self.assertIn("NO-DATA", result.stderr)


class WrongTypeFieldsReportCleanFailNeverCrash(unittest.TestCase):
    """Adversarial cases from the 2026-09-15 review: a wrong-typed field
    on an object-shaped property used to reach hand_rules's unguarded
    secret-scan block and raise AttributeError instead of producing a
    FAIL line. check() now gates hand_rules on validate() finding no
    structural problems (see contract_check.checked()), so validate()'s
    own type error is the only problem reported and hand_rules never
    runs on the malformed shape."""

    def setUp(self):
        self.schema = CC.load_json(MSF.DEFAULT_SCHEMA, "mobile-state-fixture-v1 schema")

    def test_app_storage_as_int_fails_cleanly(self):
        fixture = _valid_fixture(app_storage=5)
        problems = MSF.check(fixture, self.schema)
        self.assertTrue(any("app_storage" in p for p in problems), problems)

    def test_app_storage_as_string_fails_cleanly(self):
        fixture = _valid_fixture(app_storage="clean")
        problems = MSF.check(fixture, self.schema)
        self.assertTrue(any("app_storage" in p for p in problems), problems)

    def test_keychain_policy_as_int_fails_cleanly(self):
        fixture = _valid_fixture(keychain_policy=7)
        problems = MSF.check(fixture, self.schema)
        self.assertTrue(any("keychain_policy" in p for p in problems), problems)

    def test_external_services_as_int_fails_cleanly(self):
        fixture = _valid_fixture(external_services=5)
        problems = MSF.check(fixture, self.schema)
        self.assertTrue(any("external_services" in p for p in problems), problems)


class BlankAndGarbageScalarsAreRejected(unittest.TestCase):
    """MAJOR 3 from the review: the schema's enforced subset has no
    minLength/pattern, so these were silently accepted before."""

    def setUp(self):
        self.schema = CC.load_json(MSF.DEFAULT_SCHEMA, "mobile-state-fixture-v1 schema")

    def test_empty_account_data_ref_fails(self):
        fixture = _valid_fixture(account_data_ref="")
        problems = MSF.check(fixture, self.schema)
        self.assertTrue(any("account_data_ref" in p for p in problems), problems)

    def test_whitespace_only_account_data_ref_fails(self):
        fixture = _valid_fixture(account_data_ref="   ")
        problems = MSF.check(fixture, self.schema)
        self.assertTrue(any("account_data_ref" in p for p in problems), problems)

    def test_whitespace_only_clock_fixed_value_fails(self):
        # Whitespace-only used to slip past _require_when's plain
        # falsiness test ("   " is truthy).
        fixture = _valid_fixture(clock={"mode": "fixed", "fixed_value": "   "})
        problems = MSF.check(fixture, self.schema)
        self.assertTrue(any("clock.fixed_value" in p for p in problems), problems)

    def test_clock_fixed_value_not_iso8601_fails(self):
        fixture = _valid_fixture(clock={"mode": "fixed", "fixed_value": "yesterday"})
        problems = MSF.check(fixture, self.schema)
        self.assertTrue(
            any("clock.fixed_value" in p and "ISO-8601" in p for p in problems),
            problems)

    def test_garbage_locale_fails(self):
        fixture = _valid_fixture(locale="not a locale!!")
        problems = MSF.check(fixture, self.schema)
        self.assertTrue(any(p.startswith("locale:") for p in problems), problems)

    def test_fake_timezone_fails(self):
        fixture = _valid_fixture(timezone="Mars/Olympus")
        problems = MSF.check(fixture, self.schema)
        self.assertTrue(any(p.startswith("timezone:") for p in problems), problems)


if __name__ == "__main__":
    unittest.main()

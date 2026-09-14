#!/usr/bin/env python3
"""Tests for release_state_tracker.py (WBS-30.09, TestFlight/release-state evidence)."""
import json
import os
import sys
import tempfile
import unittest

sys.path.insert(0, __file__.rsplit("/", 1)[0])
import release_state_tracker as RST

# Synthetic, generic fixture shaped like xcrun devicectl's real --json-output,
# mirroring test_mobile_reference_lock.py's fixture. Never a real bundle id.
SYNTHETIC_OBSERVATION = {
    "result": {"devices": [{"identifier": "00000000-0000-0000-0000-000000000000",
                             "apps": [{"bundleIdentifier": "com.example.App",
                                       "version": "2.0", "bundleVersion": "7"}]}]}
}
SYNTHETIC_BUNDLE_ID = "com.example.App"


class InstalledStateTests(unittest.TestCase):
    """INSTALLED is the one state this module can measure for real."""

    def test_installed_is_pass_from_a_real_shaped_observation(self):
        record = RST.state_evidence(RST.STATE_INSTALLED, observation=SYNTHETIC_OBSERVATION,
                                     bundle_id=SYNTHETIC_BUNDLE_ID)
        self.assertEqual(record["verdict"], "PASS")
        self.assertEqual(record["evidence"]["bundle_id"], SYNTHETIC_BUNDLE_ID)
        self.assertEqual(record["evidence"]["version"], "2.0")
        self.assertEqual(record["evidence"]["build"], "7")

    def test_installed_is_no_data_with_no_observation_supplied(self):
        record = RST.state_evidence(RST.STATE_INSTALLED, observation=None,
                                     bundle_id=SYNTHETIC_BUNDLE_ID)
        self.assertEqual(record["verdict"], "NO-DATA")
        self.assertIn("No device observation was supplied", record["evidence"]["reason"])

    def test_installed_is_no_data_with_no_bundle_id_supplied(self):
        record = RST.state_evidence(RST.STATE_INSTALLED, observation=SYNTHETIC_OBSERVATION,
                                     bundle_id=None)
        self.assertEqual(record["verdict"], "NO-DATA")
        self.assertIn("No bundle id was supplied", record["evidence"]["reason"])

    def test_installed_is_no_data_on_a_non_matching_bundle_id(self):
        record = RST.state_evidence(RST.STATE_INSTALLED, observation=SYNTHETIC_OBSERVATION,
                                     bundle_id="com.does.not.exist")
        self.assertEqual(record["verdict"], "NO-DATA")
        self.assertIn("did not match", record["evidence"]["reason"])

    def test_capture_method_is_recorded_not_assumed(self):
        record = RST.state_evidence(RST.STATE_INSTALLED, observation=SYNTHETIC_OBSERVATION,
                                     bundle_id=SYNTHETIC_BUNDLE_ID, capture_method="usb-manual")
        self.assertEqual(record["evidence"]["capture"]["method"], "usb-manual")


class NoApiAccessStateTests(unittest.TestCase):
    """The other four states have no live API access this session: each must
    return NO-DATA with a reason naming exactly what credential/API access
    is missing, never a guessed or silent PASS."""

    def test_uploaded_is_no_data_naming_app_store_connect(self):
        record = RST.state_evidence(RST.STATE_UPLOADED)
        self.assertEqual(record["verdict"], "NO-DATA")
        self.assertIn("App Store Connect API access", record["evidence"]["reason"])
        self.assertIn("builds/uploads endpoint", record["evidence"]["reason"])

    def test_processing_is_no_data_naming_processing_status_endpoint(self):
        record = RST.state_evidence(RST.STATE_PROCESSING)
        self.assertEqual(record["verdict"], "NO-DATA")
        self.assertIn("processing-status endpoint", record["evidence"]["reason"])

    def test_available_is_no_data_naming_testflight_track_endpoint(self):
        record = RST.state_evidence(RST.STATE_AVAILABLE)
        self.assertEqual(record["verdict"], "NO-DATA")
        self.assertIn("TestFlight API access", record["evidence"]["reason"])
        self.assertIn("track assignment endpoint", record["evidence"]["reason"])

    def test_accepted_released_is_no_data_naming_rollout_status_endpoint(self):
        record = RST.state_evidence(RST.STATE_ACCEPTED_RELEASED)
        self.assertEqual(record["verdict"], "NO-DATA")
        self.assertIn("phased-release", record["evidence"]["reason"])

    def test_no_state_silently_returns_pass_without_real_evidence(self):
        for name in RST.STATES:
            if name == RST.STATE_INSTALLED:
                continue
            record = RST.state_evidence(name)
            self.assertEqual(record["verdict"], "NO-DATA",
                              "%s must not PASS with no evidence supplied" % name)


class StateEvidenceDispatchTests(unittest.TestCase):
    def test_unknown_state_name_raises(self):
        with self.assertRaises(ValueError):
            RST.state_evidence("SHIPPED_IT")

    def test_states_tuple_is_the_five_named_in_the_roadmap_in_order(self):
        self.assertEqual(RST.STATES, ("UPLOADED", "PROCESSING", "AVAILABLE", "INSTALLED",
                                       "ACCEPTED_RELEASED"))


class ComposeRecordTests(unittest.TestCase):
    def test_composed_record_shows_all_five_states(self):
        record = RST.compose_release_record({
            RST.STATE_INSTALLED: RST.state_evidence(
                RST.STATE_INSTALLED, observation=SYNTHETIC_OBSERVATION,
                bundle_id=SYNTHETIC_BUNDLE_ID),
        })
        self.assertEqual(set(record["states"].keys()), set(RST.STATES))
        self.assertEqual(record["order"], list(RST.STATES))

    def test_missing_states_default_to_no_data_not_omitted(self):
        record = RST.compose_release_record({})
        for name in RST.STATES:
            self.assertIn(name, record["states"])
            self.assertEqual(record["states"][name]["verdict"], "NO-DATA")

    def test_installed_pass_never_flips_accepted_released_to_pass(self):
        """The roadmap's own rule, enforced structurally: an INSTALLED PASS
        must never make ACCEPTED_RELEASED read as PASS, and the two fields
        must be genuinely independent -- changing one does not change the
        other."""
        installed_pass = RST.state_evidence(
            RST.STATE_INSTALLED, observation=SYNTHETIC_OBSERVATION,
            bundle_id=SYNTHETIC_BUNDLE_ID)
        record = RST.compose_release_record({RST.STATE_INSTALLED: installed_pass})
        self.assertEqual(record["states"][RST.STATE_INSTALLED]["verdict"], "PASS")
        self.assertEqual(record["states"][RST.STATE_ACCEPTED_RELEASED]["verdict"], "NO-DATA")

        # Independence: flip ACCEPTED_RELEASED's own evidence and confirm
        # INSTALLED is untouched by it.
        accepted_released_pass = {
            "schema": RST.EVIDENCE_SCHEMA, "state": RST.STATE_ACCEPTED_RELEASED,
            "verdict": "PASS", "evidence": {"reason": "synthetic test override"},
            "observed_at": installed_pass["observed_at"],
        }
        record2 = RST.compose_release_record({
            RST.STATE_INSTALLED: installed_pass,
            RST.STATE_ACCEPTED_RELEASED: accepted_released_pass,
        })
        self.assertEqual(record2["states"][RST.STATE_ACCEPTED_RELEASED]["verdict"], "PASS")
        self.assertEqual(record2["states"][RST.STATE_INSTALLED]["verdict"], "PASS")
        self.assertEqual(record2["states"][RST.STATE_INSTALLED],
                          record["states"][RST.STATE_INSTALLED],
                          "INSTALLED's own record must be unaffected by ACCEPTED_RELEASED's")

    def test_caveat_names_the_never_collapse_rule(self):
        record = RST.compose_release_record({})
        self.assertIn("never", record["caveat"].lower())
        self.assertIn("boolean", record["caveat"].lower())


class MainCliTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.obs_path = os.path.join(self.tmp, "obs.json")
        with open(self.obs_path, "w") as fh:
            json.dump(SYNTHETIC_OBSERVATION, fh)

    def test_main_exits_0_with_a_real_observation(self):
        self.assertEqual(RST.main(["--observation", self.obs_path,
                                    "--bundle-id", SYNTHETIC_BUNDLE_ID]), 0)

    def test_main_exits_0_with_no_observation_all_states_no_data_except_pass_absent(self):
        self.assertEqual(RST.main([]), 0)

    def test_main_exits_2_on_missing_observation_file(self):
        self.assertEqual(RST.main(["--observation", "/no/such/observation.json",
                                    "--bundle-id", SYNTHETIC_BUNDLE_ID]), 2)

    def test_main_writes_to_out_path_when_given(self):
        out = os.path.join(self.tmp, "record.json")
        self.assertEqual(RST.main(["--observation", self.obs_path,
                                    "--bundle-id", SYNTHETIC_BUNDLE_ID, "--out", out]), 0)
        with open(out) as fh:
            written = json.load(fh)
        self.assertEqual(written["schema"], RST.RECORD_SCHEMA)
        self.assertEqual(written["states"][RST.STATE_INSTALLED]["verdict"], "PASS")


if __name__ == "__main__":
    unittest.main()

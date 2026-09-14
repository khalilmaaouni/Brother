#!/usr/bin/env python3
"""Tests for mobile_reference_lock.py (WBS-30.02, the honest partial case)."""
import json
import os
import sys
import tempfile
import unittest

sys.path.insert(0, __file__.rsplit("/", 1)[0])
import mobile_reference_lock as MRL

# Synthetic, generic fixture shaped like xcrun devicectl's real --json-output,
# never real device data.
SYNTHETIC_OBSERVATION = {
    "result": {"devices": [{"identifier": "00000000-0000-0000-0000-000000000000",
                             "apps": [{"bundleIdentifier": "com.example.App",
                                       "version": "1.0", "bundleVersion": "42"}]}]}
}


class MobileReferenceLockTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.obs_path = os.path.join(self.tmp, "obs.json")
        with open(self.obs_path, "w") as fh:
            json.dump(SYNTHETIC_OBSERVATION, fh)

    def test_artifact_identity_is_captured_from_a_real_shaped_observation(self):
        record = MRL.partial_reference(self.obs_path, "com.example.App")
        self.assertEqual(record["artifact_identity"]["bundle_id"], "com.example.App")
        self.assertEqual(record["artifact_identity"]["version"], "1.0")
        self.assertEqual(record["artifact_identity"]["build"], "42")
        self.assertEqual(record["artifact_identity"]["status"], "observed_installed")

    def test_source_provenance_is_always_explicitly_unresolved(self):
        record = MRL.partial_reference(self.obs_path, "com.example.App")
        self.assertEqual(record["source_provenance"]["status"], "UNRESOLVED")
        self.assertFalse(record["source_provenance"]["verified"])
        self.assertIsNone(record["source_provenance"]["source_revision"])
        self.assertTrue(record["source_provenance"]["reason"])

    def test_binary_sha256_defaults_to_none_not_fabricated(self):
        record = MRL.partial_reference(self.obs_path, "com.example.App")
        self.assertIsNone(record["artifact_identity"]["binary_sha256"])
        self.assertEqual(record["artifact_identity"]["identity_strength"], "metadata_only")

    def test_caveat_is_always_present_and_names_the_gap(self):
        record = MRL.partial_reference(self.obs_path, "com.example.App")
        self.assertIn("UNRESOLVED", record["caveat"])
        self.assertIn("do not establish", record["caveat"])

    def test_capture_method_is_recorded_not_assumed(self):
        record = MRL.partial_reference(self.obs_path, "com.example.App", capture_method="usb-manual")
        self.assertEqual(record["artifact_identity"]["capture"]["method"], "usb-manual")

    def test_no_matching_bundle_id_refuses(self):
        with self.assertRaises(Exception):
            MRL.partial_reference(self.obs_path, "com.does.not.exist")

    def test_main_exits_0_on_success(self):
        self.assertEqual(MRL.main([self.obs_path, "com.example.App"]), 0)

    def test_main_exits_2_on_missing_observation_file(self):
        self.assertEqual(MRL.main(["/no/such/observation.json", "com.example.App"]), 2)

    def test_main_writes_to_out_path_when_given(self):
        out = os.path.join(self.tmp, "record.json")
        self.assertEqual(MRL.main([self.obs_path, "com.example.App", "--out", out]), 0)
        written = json.load(open(out))
        self.assertEqual(written["schema"], MRL.SCHEMA)


if __name__ == "__main__":
    unittest.main()

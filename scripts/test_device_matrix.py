#!/usr/bin/env python3
"""Tests for device_matrix.py (WBS-30.07/30.08 physical-device adapter seam)."""
import subprocess
import sys
import unittest
from unittest.mock import patch

sys.path.insert(0, __file__.rsplit("/", 1)[0])
import device_matrix as DM

# Real output captured from `xcrun devicectl list devices` on this machine,
# 2026-09-13 -- used as a fixture so the parser test never depends on a
# live device being attached. Trailing spaces in the header are real
# (devicectl's own column padding), kept verbatim.
REAL_DEVICECTL_OUTPUT = (
    "Name              Hostname                          Identifier"
    "                             State                Model                     \n"
    "---------------   -------------------------------   ------------------------"
    "------------   ------------------   --------------------------\n"
    "khalil’s iPhone   khalils-iPhone.coredevice.local   98F73A29-0110-5E84-B3B2-"
    "56A34D325CC4   available (paired)   iPhone 15 Pro (iPhone16,1)\n"
)


class DeviceMatrixTests(unittest.TestCase):

    def test_real_captured_output_parses_to_one_device_not_the_separator(self):
        rows = DM.parse_devicectl_table(REAL_DEVICECTL_OUTPUT)
        self.assertEqual(len(rows), 1, rows)
        self.assertEqual(rows[0]["identifier"], "98F73A29-0110-5E84-B3B2-56A34D325CC4")
        self.assertEqual(rows[0]["model"], "iPhone 15 Pro (iPhone16,1)")

    def test_separator_row_with_spaces_between_dash_groups_is_not_a_device(self):
        # Regression: an earlier version only checked line.strip() for an
        # all-dash string, which never matched because the real separator
        # mixes dashes AND spaces (column padding), so the separator row
        # was parsed as a fake device with dashes in every field.
        separator_only = REAL_DEVICECTL_OUTPUT.splitlines()[0] + "\n" + \
            REAL_DEVICECTL_OUTPUT.splitlines()[1] + "\n"
        rows = DM.parse_devicectl_table(separator_only)
        self.assertEqual(rows, [])

    def test_no_header_line_returns_empty_not_a_crash(self):
        self.assertEqual(DM.parse_devicectl_table("nonsense\noutput\n"), [])

    def test_empty_output_returns_empty(self):
        self.assertEqual(DM.parse_devicectl_table(""), [])

    @patch("device_matrix.run")
    def test_local_pass_when_devicectl_finds_a_device(self, mock_run):
        mock_run.return_value = (REAL_DEVICECTL_OUTPUT, None)
        verdict, evidence = DM.local_physical_devices()
        self.assertEqual(verdict, "PASS")
        self.assertEqual(len(evidence["devices"]), 1)

    @patch("device_matrix.run")
    def test_local_no_data_when_devicectl_is_unavailable(self, mock_run):
        mock_run.return_value = (None, "no such file")
        verdict, evidence = DM.local_physical_devices()
        self.assertEqual(verdict, "NO-DATA")
        self.assertIn("devicectl unavailable", evidence["reason"])
        self.assertEqual(evidence["devices"], [])

    @patch("device_matrix.run")
    def test_local_no_data_when_devicectl_finds_zero_devices(self, mock_run):
        header_only = REAL_DEVICECTL_OUTPUT.splitlines()[0] + "\n" + \
            REAL_DEVICECTL_OUTPUT.splitlines()[1] + "\n"
        mock_run.return_value = (header_only, None)
        verdict, evidence = DM.local_physical_devices()
        self.assertEqual(verdict, "NO-DATA")
        self.assertIn("no locally attached device", evidence["reason"])

    def test_run_never_raises_on_a_real_timeout(self):
        with patch("subprocess.run", side_effect=subprocess.TimeoutExpired(cmd="x", timeout=1)):
            out, err = DM.run(["xcrun", "devicectl", "list", "devices"])
        self.assertIsNone(out)
        self.assertIn("timed out", err)

    def test_run_never_raises_when_the_binary_is_missing(self):
        with patch("subprocess.run", side_effect=OSError("no such file")):
            out, err = DM.run(["nope"])
        self.assertIsNone(out)
        self.assertIn("no such file", err)

    def test_farm_adapter_is_always_no_data(self):
        verdict, evidence = DM.farm_adapter()
        self.assertEqual(verdict, "NO-DATA")
        self.assertIn("no external device-farm provider", evidence["reason"])

    def test_provider_adapter_is_always_no_data(self):
        verdict, evidence = DM.provider_adapter()
        self.assertEqual(verdict, "NO-DATA")
        self.assertIn("no future-provider integration", evidence["reason"])

    def test_unknown_adapter_name_is_refused_not_silently_defaulted(self):
        with self.assertRaises(ValueError):
            DM.physical_device_evidence("made-up-adapter")

    @patch("device_matrix.run")
    def test_evidence_record_never_claims_install_or_launch(self, mock_run):
        mock_run.return_value = (REAL_DEVICECTL_OUTPUT, None)
        record = DM.physical_device_evidence(DM.ADAPTER_LOCAL)
        self.assertEqual(record["schema"], "brother-physical-device-evidence-v1")
        self.assertEqual(record["verdict"], "PASS")
        joined_limits = " ".join(record["limits"])
        self.assertIn("never infers app install, launch, or behavior", joined_limits)

    def test_main_exits_two_on_no_data(self):
        # H2 fix: NO-DATA must not collapse into the same exit code as FAIL
        # (1), or a downstream reader keyed on evidence_obligation.py's
        # PASS=0/FAIL=1/NO-DATA=2 vocabulary misreads NO-DATA as FAIL.
        with patch("device_matrix.run", return_value=(None, "boom")):
            self.assertEqual(DM.main([DM.ADAPTER_FARM]), 2)

    @patch("device_matrix.run")
    def test_main_exits_zero_on_pass(self, mock_run):
        mock_run.return_value = (REAL_DEVICECTL_OUTPUT, None)
        self.assertEqual(DM.main([DM.ADAPTER_LOCAL]), 0)

    def test_no_data_and_fail_exit_codes_are_distinct(self):
        # H2 regression: a NO-DATA verdict's exit code must differ from a
        # FAIL verdict's exit code, matching the shared PASS=0/FAIL=1/
        # NO-DATA=2 convention used elsewhere (e.g.
        # evidence_obligation.verdict_for_code, reversibility_gate.py's
        # and merge_passport.py's own exit_code_for_verdict).
        self.assertEqual(DM.exit_code_for_verdict("PASS"), 0)
        self.assertEqual(DM.exit_code_for_verdict("FAIL"), 1)
        self.assertEqual(DM.exit_code_for_verdict("NO-DATA"), 2)
        self.assertNotEqual(DM.exit_code_for_verdict("NO-DATA"), DM.exit_code_for_verdict("FAIL"))


if __name__ == "__main__":
    unittest.main()

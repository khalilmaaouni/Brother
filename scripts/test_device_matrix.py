#!/usr/bin/env python3
"""Tests for device_matrix.py (WBS-30.07/30.08 physical-device adapter seam,
and EPIC M4.03's reservation-through-cleanup lifecycle for a local physical
iOS device)."""
import json
import os
import subprocess
import sys
import tempfile
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


#: -- M4.03 lifecycle tests -----------------------------------------------
#:
#: FIXTURE_APPS/FIXTURE_PROCS below are SYNTHETIC (generic bundle ids,
#: fake UDID), not copied from the founder's own real device: the real
#: JSON shape (info.outcome, result.apps with
#: bundleIdentifier/version/bundleVersion/url, result.runningProcesses with
#: executable/processIdentifier) was verified directly against a real
#: attached device during this unit's development (see the module-level
#: note in device_matrix.py); these fixtures reproduce that shape with
#: placeholder values so no personal device data is committed.
TEST_DEVICE = "TESTDEVICE-0000-1111-2222-333344445555"
TEST_BUNDLE = "com.example.sampleapp"
TEST_APP_URL = "file:///private/var/containers/Bundle/Application/AAAA/Sample.app"


class _FakeDevice(object):
    """A minimal STATE MODEL of the device, not a table of constant return
    values: installing adds the bundle to the apps listing, uninstalling
    removes it, launching adds a process, terminating removes it.

    This replaced a set of constant mocks, and the difference is the whole
    point. A constant apps listing cannot express "the app is gone now",
    so a test using one passes identically whether uninstall really
    removed the bundle or merely claimed to, which is exactly the defect
    (uninstall's self-reported success trusted with no re-read) it was
    supposed to be covering."""

    def __init__(self, uninstall_actually_removes=True):
        self.apps = []
        self.processes = []
        self.uninstall_actually_removes = uninstall_actually_removes
        self.calls = []
        self.reachable = True

    # -- the devicectl-facing seam the module actually calls ------------

    def list_installed_apps(self, device_id, timeout=30):
        self.calls.append(("list_installed_apps", device_id))
        if not self.reachable:
            return "FAIL", {"reason": "device not connected", "apps": []}
        return "PASS", {"apps": list(self.apps)}

    def list_processes(self, device_id, timeout=30):
        self.calls.append(("list_processes", device_id))
        if not self.reachable:
            return "FAIL", {"reason": "device not connected", "processes": []}
        if self._processes_verdict != "PASS":
            return self._processes_verdict, {"reason": "simulated", "processes": []}
        return "PASS", {"processes": list(self.processes)}

    def install_app(self, device_id, app_path, timeout=180):
        self.calls.append(("install_app", device_id))
        if self._install_verdict[0] != "PASS":
            return self._install_verdict
        self.apps = [{"bundleIdentifier": TEST_BUNDLE, "version": "1.0",
                      "bundleVersion": "42", "url": TEST_APP_URL}]
        return "PASS", {"install_result": {}}

    def uninstall_app(self, device_id, bundle_id, timeout=60):
        self.calls.append(("uninstall_app", device_id, bundle_id))
        if self.uninstall_actually_removes:
            self.apps = [a for a in self.apps if a.get("bundleIdentifier") != bundle_id]
            self.processes = []
        if self._uninstall_verdict is not None:
            return self._uninstall_verdict
        # devicectl's own self-report: PASS whether or not anything moved.
        return "PASS", {"uninstall_result": {}}

    def launch_app(self, device_id, bundle_id, terminate_existing=True, timeout=60):
        self.calls.append(("launch_app", device_id, bundle_id))
        self.processes = [{"executable": TEST_APP_URL + "/Sample", "processIdentifier": 99}]
        if self._on_launch is not None:
            self._on_launch()
        if self._disconnect_after_launch:
            self.reachable = False
        return "PASS", {"launch_result": {}}

    def terminate_process(self, device_id, pid, kill=False, timeout=30):
        self.calls.append(("terminate_process", device_id, pid))
        self.processes = [p for p in self.processes if p.get("processIdentifier") != pid]
        return "PASS", {"terminate_result": {}}

    def patched(self, install_verdict=("PASS", {}), uninstall_verdict=None,
                processes_verdict="PASS", disconnect_after_launch=False, on_launch=None):
        self._install_verdict = install_verdict
        self._uninstall_verdict = uninstall_verdict
        self._processes_verdict = processes_verdict
        self._disconnect_after_launch = disconnect_after_launch
        self._on_launch = on_launch
        import contextlib as _ctx
        stack = _ctx.ExitStack()
        stack.enter_context(patch("device_matrix.get_device_details",
                                  return_value=("PASS", {"details": {}})))
        for name in ("list_installed_apps", "list_processes", "install_app",
                     "uninstall_app", "launch_app", "terminate_process"):
            stack.enter_context(patch("device_matrix." + name, getattr(self, name)))
        return stack


def _fake_run(payload_by_command, returncode=0, stderr=""):
    """A subprocess.run(...) stand-in for `xcrun devicectl ...
    --json-output <path>`: finds the `--json-output` path in argv, writes
    the JSON payload matching the invoked subcommand (keyed by the
    argv[2:4] pair, e.g. ("device","info")+("apps",) -> looked up by the
    first two non-flag tokens after "devicectl") to that path, and returns
    a fake completed-process object. This exercises _devicectl_json's real
    tempfile-write/read/cleanup path end to end, only the actual `xcrun`
    binary is replaced."""
    class _Proc:
        def __init__(self, rc, out, err):
            self.returncode = rc
            self.stdout = out.encode("utf-8")
            self.stderr = err.encode("utf-8")

    def run(argv, **kwargs):
        jpath = argv[argv.index("--json-output") + 1]
        # argv shape: ["xcrun","devicectl","device", <verb...>, "--device", ..., "--json-output", ...]
        # argv[2] is always the literal "device"; drop it so payload_by_command
        # keys only need to name the verb path, e.g. ("info", "details").
        key = tuple(a for a in argv[3:] if not a.startswith("-"))[:2]
        payload = None
        for k, v in payload_by_command.items():
            if key[:len(k)] == k:
                payload = v
                break
        if payload is not None:
            with open(jpath, "w", encoding="utf-8") as fh:
                json.dump(payload, fh)
        return _Proc(returncode, "", stderr)
    return run


FIXTURE_APPS_RESULT = {
    "info": {"outcome": "success"},
    "result": {"deviceIdentifier": TEST_DEVICE, "apps": [
        {"bundleIdentifier": TEST_BUNDLE, "version": "1.0", "bundleVersion": "42",
         "name": "Sample", "url": "file:///private/var/containers/Bundle/Application/AAAA/Sample.app"},
    ]},
}
FIXTURE_PROCS_RESULT = {
    "info": {"outcome": "success"},
    "result": {"deviceIdentifier": TEST_DEVICE, "runningProcesses": [
        {"executable": "file:///private/var/containers/Bundle/Application/AAAA/Sample.app/Sample",
         "processIdentifier": 4242},
        {"executable": "file:///sbin/launchd", "processIdentifier": 1},
    ]},
}
FIXTURE_DETAILS_RESULT = {
    "info": {"outcome": "success"},
    "result": {"identifier": TEST_DEVICE, "hardwareProperties": {}, "deviceProperties": {}},
}


class DevicectlJsonTests(unittest.TestCase):
    """_devicectl_json's own contract, independent of any one subcommand."""

    def test_success_reads_back_the_written_json(self):
        with patch("subprocess.run", side_effect=_fake_run({("info", "details"): FIXTURE_DETAILS_RESULT})):
            r = DM._devicectl_json(["device", "info", "details", "--device", TEST_DEVICE])
        self.assertTrue(r["ok"], r)
        self.assertEqual(r["outcome"], "success")
        self.assertEqual(r["result"]["identifier"], TEST_DEVICE)

    def test_nonzero_exit_is_not_ok_and_names_the_reason(self):
        with patch("subprocess.run", side_effect=_fake_run({}, returncode=1, stderr="boom")):
            r = DM._devicectl_json(["device", "info", "apps", "--device", TEST_DEVICE])
        self.assertFalse(r["ok"])
        self.assertEqual(r["returncode"], 1)
        self.assertIn("boom", r["reason"])

    def test_missing_binary_is_ok_false_with_no_returncode(self):
        with patch("subprocess.run", side_effect=OSError("no such file")):
            r = DM._devicectl_json(["device", "info", "apps", "--device", TEST_DEVICE])
        self.assertFalse(r["ok"])
        self.assertIsNone(r["returncode"])
        self.assertIn("devicectl unavailable", r["reason"])

    def test_no_json_written_is_reported_not_crashed(self):
        with patch("subprocess.run", side_effect=_fake_run({}, returncode=0)):
            r = DM._devicectl_json(["device", "info", "apps", "--device", TEST_DEVICE])
        self.assertFalse(r["ok"])
        self.assertIn("no JSON output", r["reason"])

    def test_malformed_json_is_reported_not_crashed(self):
        def run(argv, **kwargs):
            jpath = argv[argv.index("--json-output") + 1]
            with open(jpath, "w", encoding="utf-8") as fh:
                fh.write("{not json")
            class _Proc:
                returncode = 0
                stdout = b""
                stderr = b""
            return _Proc()
        with patch("subprocess.run", side_effect=run):
            r = DM._devicectl_json(["device", "info", "apps", "--device", TEST_DEVICE])
        self.assertFalse(r["ok"])
        self.assertIn("unreadable", r["reason"])

    def test_temp_json_file_is_always_cleaned_up(self):
        seen = {}

        def run(argv, **kwargs):
            jpath = argv[argv.index("--json-output") + 1]
            seen["path"] = jpath
            with open(jpath, "w", encoding="utf-8") as fh:
                json.dump(FIXTURE_DETAILS_RESULT, fh)
            class _Proc:
                returncode = 0
                stdout = b""
                stderr = b""
            return _Proc()
        with patch("subprocess.run", side_effect=run):
            DM._devicectl_json(["device", "info", "details", "--device", TEST_DEVICE])
        self.assertFalse(os.path.isfile(seen["path"]))


class ReadEndpointTests(unittest.TestCase):
    """get_device_details/list_processes/list_installed_apps against the
    verified real JSON shape."""

    def test_get_device_details_pass(self):
        with patch("subprocess.run", side_effect=_fake_run({("info", "details"): FIXTURE_DETAILS_RESULT})):
            verdict, detail = DM.get_device_details(TEST_DEVICE)
        self.assertEqual(verdict, "PASS")
        self.assertEqual(detail["details"]["identifier"], TEST_DEVICE)

    def test_list_processes_pass(self):
        with patch("subprocess.run", side_effect=_fake_run({("info", "processes"): FIXTURE_PROCS_RESULT})):
            verdict, detail = DM.list_processes(TEST_DEVICE)
        self.assertEqual(verdict, "PASS")
        self.assertEqual(len(detail["processes"]), 2)

    def test_list_processes_no_data_on_tool_missing(self):
        with patch("subprocess.run", side_effect=OSError("nope")):
            verdict, detail = DM.list_processes(TEST_DEVICE)
        self.assertEqual(verdict, "NO-DATA")
        self.assertEqual(detail["processes"], [])

    def test_list_installed_apps_pass(self):
        with patch("subprocess.run", side_effect=_fake_run({("info", "apps"): FIXTURE_APPS_RESULT})):
            verdict, detail = DM.list_installed_apps(TEST_DEVICE)
        self.assertEqual(verdict, "PASS")
        self.assertEqual(detail["apps"][0]["bundleIdentifier"], TEST_BUNDLE)

    def test_list_installed_apps_explicit_empty_is_still_pass(self):
        """An empty list is a real, confirmed observation, not the same
        shape as a missing or malformed one; it must stay legitimate."""
        fixture = {"info": {"outcome": "success"},
                  "result": {"deviceIdentifier": TEST_DEVICE, "apps": []}}
        with patch("subprocess.run", side_effect=_fake_run({("info", "apps"): fixture})):
            verdict, detail = DM.list_installed_apps(TEST_DEVICE)
        self.assertEqual(verdict, "PASS")
        self.assertEqual(detail["apps"], [])

    def test_list_installed_apps_malformed_result_is_no_data_not_pass(self):
        """Data-review finding, reproduced then closed: result:null,
        result:{}, apps:null and apps:[{}] (a non-dict-with-identity entry)
        each used to normalize to PASS with apps:[] via `(result or {}).get
        ("apps") or []`, indistinguishable from a genuinely confirmed empty
        device. Each of these must read NO-DATA instead."""
        malformed_results = [
            None,
            {},
            {"deviceIdentifier": TEST_DEVICE, "apps": None},
            {"deviceIdentifier": TEST_DEVICE, "apps": [{}]},
            {"deviceIdentifier": TEST_DEVICE, "apps": "not-a-list"},
        ]
        for result in malformed_results:
            with self.subTest(result=result):
                fixture = {"info": {"outcome": "success"}, "result": result}
                with patch("subprocess.run",
                          side_effect=_fake_run({("info", "apps"): fixture})):
                    verdict, detail = DM.list_installed_apps(TEST_DEVICE)
                self.assertEqual(verdict, "NO-DATA")
                self.assertEqual(detail["apps"], [])


class VerifyInstalledIdentityTests(unittest.TestCase):

    def test_pass_when_bundle_and_versions_match(self):
        with patch("device_matrix.list_installed_apps", return_value=("PASS", {"apps": FIXTURE_APPS_RESULT["result"]["apps"]})):
            verdict, detail = DM.verify_installed_identity(TEST_DEVICE, TEST_BUNDLE, expected_version="1.0", expected_build="42")
        self.assertEqual(verdict, "PASS")

    def test_fail_when_bundle_id_absent(self):
        with patch("device_matrix.list_installed_apps", return_value=("PASS", {"apps": []})):
            verdict, detail = DM.verify_installed_identity(TEST_DEVICE, TEST_BUNDLE)
        self.assertEqual(verdict, "FAIL")
        self.assertIn("not found", detail["reason"])

    def test_fail_when_version_mismatches(self):
        with patch("device_matrix.list_installed_apps", return_value=("PASS", {"apps": FIXTURE_APPS_RESULT["result"]["apps"]})):
            verdict, detail = DM.verify_installed_identity(TEST_DEVICE, TEST_BUNDLE, expected_version="9.9")
        self.assertEqual(verdict, "FAIL")
        self.assertIn("version", detail["mismatches"])

    def test_no_data_propagates_from_apps_read(self):
        with patch("device_matrix.list_installed_apps", return_value=("NO-DATA", {"reason": "x", "apps": []})):
            verdict, detail = DM.verify_installed_identity(TEST_DEVICE, TEST_BUNDLE)
        self.assertEqual(verdict, "NO-DATA")


class VerifyUninstalledTests(unittest.TestCase):
    """verify_uninstalled's own real promise: PASS only from a confirmed
    fresh read showing the bundle absent, never from unconfirmed data."""

    def test_pass_when_bundle_confirmed_absent(self):
        with patch("device_matrix.list_installed_apps", return_value=("PASS", {"apps": []})):
            verdict, detail = DM.verify_uninstalled(TEST_DEVICE, TEST_BUNDLE)
        self.assertEqual(verdict, "PASS")
        self.assertEqual(detail["absent"], TEST_BUNDLE)

    def test_fail_when_bundle_still_listed(self):
        with patch("device_matrix.list_installed_apps",
                  return_value=("PASS", {"apps": FIXTURE_APPS_RESULT["result"]["apps"]})):
            verdict, detail = DM.verify_uninstalled(TEST_DEVICE, TEST_BUNDLE)
        self.assertEqual(verdict, "FAIL")

    def test_no_data_never_reads_as_confirmed_absence(self):
        """Data-review finding, reproduced then closed: verify_uninstalled
        trusted list_installed_apps' verdict, but that call used to report
        PASS even for a malformed/missing apps listing (fixed above in
        ReadEndpointTests). This is the actual consumer the finding named:
        prove it, at this call site, never turns an unconfirmed read into
        a false "uninstall verified" PASS."""
        with patch("device_matrix.list_installed_apps",
                  return_value=("NO-DATA", {"reason": "malformed", "apps": []})):
            verdict, detail = DM.verify_uninstalled(TEST_DEVICE, TEST_BUNDLE)
        self.assertEqual(verdict, "NO-DATA")
        self.assertIn("unconfirmed", detail["note"])


class FindRunningPidTests(unittest.TestCase):

    def test_pass_matches_by_executable_prefix(self):
        apps = FIXTURE_APPS_RESULT["result"]["apps"]
        procs = FIXTURE_PROCS_RESULT["result"]["runningProcesses"]
        with patch("device_matrix.list_installed_apps", return_value=("PASS", {"apps": apps})), \
             patch("device_matrix.list_processes", return_value=("PASS", {"processes": procs})):
            verdict, detail = DM.find_running_pid(TEST_DEVICE, TEST_BUNDLE)
        self.assertEqual(verdict, "PASS")
        self.assertEqual(detail["pid"], 4242)

    def test_no_data_when_no_process_matches(self):
        apps = FIXTURE_APPS_RESULT["result"]["apps"]
        with patch("device_matrix.list_installed_apps", return_value=("PASS", {"apps": apps})), \
             patch("device_matrix.list_processes", return_value=("PASS", {"processes": []})):
            verdict, detail = DM.find_running_pid(TEST_DEVICE, TEST_BUNDLE)
        self.assertEqual(verdict, "NO-DATA")
        self.assertIsNone(detail["pid"])

    def test_no_data_when_app_has_no_url(self):
        with patch("device_matrix.list_installed_apps",
                    return_value=("PASS", {"apps": [{"bundleIdentifier": TEST_BUNDLE}]})):
            verdict, detail = DM.find_running_pid(TEST_DEVICE, TEST_BUNDLE)
        self.assertEqual(verdict, "NO-DATA")


class ReserveReleaseRealLeaseStoreTests(unittest.TestCase):
    """Exercises the REAL bm_device_lease.py store (M4.01), no mocking:
    only BM_DEVICE_LEASE_DB is redirected to a private tempfile so this
    never touches the machine's real default lease database."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self._old_env = os.environ.get("BM_DEVICE_LEASE_DB")
        os.environ["BM_DEVICE_LEASE_DB"] = os.path.join(self._tmp.name, "leases.sqlite3")

    def tearDown(self):
        if self._old_env is None:
            os.environ.pop("BM_DEVICE_LEASE_DB", None)
        else:
            os.environ["BM_DEVICE_LEASE_DB"] = self._old_env
        self._tmp.cleanup()

    def test_reserve_then_reserve_again_is_refused(self):
        verdict, detail = DM.reserve_device(TEST_DEVICE, "owner-a", ttl_seconds=60)
        self.assertEqual(verdict, "PASS")
        verdict2, detail2 = DM.reserve_device(TEST_DEVICE, "owner-b", ttl_seconds=60)
        self.assertEqual(verdict2, "NO-DATA")
        self.assertEqual(detail2["reason"], "already-leased")

    def test_reserve_then_release_frees_the_device(self):
        verdict, detail = DM.reserve_device(TEST_DEVICE, "owner-a", ttl_seconds=60)
        self.assertEqual(verdict, "PASS")
        verdict2, detail2 = DM.release_device(TEST_DEVICE, detail["lease_uuid"])
        self.assertEqual(verdict2, "PASS")
        self.assertEqual(detail2["state"], "available")
        verdict3, _ = DM.reserve_device(TEST_DEVICE, "owner-b", ttl_seconds=60)
        self.assertEqual(verdict3, "PASS")


class FullLifecycleAdversarialTests(unittest.TestCase):
    """The three targets the founder asked this unit's self-review to hunt
    specifically: a lease claimed but never released on a failure path, a
    device left installed-but-unverified reported as success, and an
    evidence claim with no real observation behind it. Uses the REAL
    bm_device_lease.py store (redirected to a tempfile) and mocks only the
    devicectl-facing functions, so the lease bookkeeping under test is the
    real M4.01 code, not a stand-in."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self._old_env = os.environ.get("BM_DEVICE_LEASE_DB")
        os.environ["BM_DEVICE_LEASE_DB"] = os.path.join(self._tmp.name, "leases.sqlite3")
        self._app_dir = os.path.join(self._tmp.name, "Sample.app")
        os.makedirs(self._app_dir)
        import plistlib
        with open(os.path.join(self._app_dir, "Info.plist"), "wb") as fh:
            plistlib.dump({"CFBundleIdentifier": TEST_BUNDLE, "CFBundleShortVersionString": "1.0",
                            "CFBundleVersion": "42", "CFBundleExecutable": "Sample"}, fh)
        with open(os.path.join(self._app_dir, "Sample"), "wb") as fh:
            fh.write(b"#!/bin/sh\n")
        os.chmod(os.path.join(self._app_dir, "Sample"), 0o755)

    def tearDown(self):
        if self._old_env is None:
            os.environ.pop("BM_DEVICE_LEASE_DB", None)
        else:
            os.environ["BM_DEVICE_LEASE_DB"] = self._old_env
        self._tmp.cleanup()

    def _lease_state(self):
        dl_module, _ = DM._bm_device_lease_module()
        store = dl_module.DeviceLeaseStore()
        try:
            row = store.get(TEST_DEVICE)
            return row["state"] if row else None
        finally:
            store.close()

    def test_lease_never_stranded_leased_after_install_failure_and_cleanup_failure(self):
        # Target 1: a lease claimed but never released on a failure path.
        with patch("device_matrix.get_device_details", return_value=("PASS", {"details": {}})), \
             patch("device_matrix.install_app", return_value=("FAIL", {"reason": "install refused"})), \
             patch("device_matrix.uninstall_app", return_value=("FAIL", {"reason": "uninstall also refused"})):
            record = DM.run_physical_lifecycle(TEST_DEVICE, self._app_dir, TEST_BUNDLE, "owner-a")
        self.assertEqual(record["verdict"], "FAIL")
        state = self._lease_state()
        self.assertNotEqual(state, "leased", "lease was claimed but left stranded as 'leased'")
        self.assertEqual(state, "dirty")

    def test_lease_released_clean_after_full_success(self):
        fake = _FakeDevice()
        with fake.patched():
            record = DM.run_physical_lifecycle(TEST_DEVICE, self._app_dir, TEST_BUNDLE, "owner-a")
        self.assertEqual(record["verdict"], "PASS", record["reason"])
        self.assertEqual(self._lease_state(), "available")
        # The claim in the reason string has to match what actually ran.
        self.assertIn("re-verified absent", record["reason"])
        self.assertEqual(fake.apps, [])

    # -- Critical 1: a stage that FAILs must reach the verdict ----------

    def test_mid_run_disconnect_after_launch_is_never_pass(self):
        """The device is unplugged right after launch, so every later read
        fails. This reported PASS and exit 0 before: find_running_pid and
        _collect_evidence wrote their verdicts into the stage list, then
        the variable was overwritten by the next call and never read."""
        fake = _FakeDevice()
        with fake.patched(disconnect_after_launch=True):
            record = DM.run_physical_lifecycle(TEST_DEVICE, self._app_dir, TEST_BUNDLE, "owner-a")
        stages = {s["name"]: s["verdict"] for s in record["stages"]}
        self.assertEqual(stages.get("observe-process"), "FAIL")
        self.assertEqual(stages.get("collect-evidence"), "FAIL")
        self.assertEqual(record["verdict"], "FAIL", record["reason"])
        self.assertEqual(DM.exit_code_for_verdict(record["verdict"]), 1)
        self.assertNotEqual(self._lease_state(), "leased")

    def test_no_data_stage_with_no_fail_carries_through_as_no_data(self):
        """A stage nobody could exercise is NO-DATA, and the run says so
        rather than rounding it up to PASS."""
        fake = _FakeDevice()
        with fake.patched(processes_verdict="NO-DATA"):
            record = DM.run_physical_lifecycle(TEST_DEVICE, self._app_dir, TEST_BUNDLE, "owner-a")
        self.assertEqual(record["verdict"], "NO-DATA", record["reason"])
        self.assertNotIn("FAIL", [s["verdict"] for s in record["stages"]])

    # -- Critical 2: losing the lease mid-run must reach the verdict ----

    def test_lease_lost_mid_lifecycle_is_never_pass(self):
        """A's TTL expires mid-run, B legitimately claims the same physical
        device, A keeps driving it. This reported PASS, exit 0, with the
        double-hold recorded only in lease.release_error beside the
        verdict. B's lease must also survive A's cleanup."""
        fake = _FakeDevice()
        dl_module, _ = DM._bm_device_lease_module()

        def steal_the_lease():
            store = dl_module.DeviceLeaseStore()
            try:
                store.conn.execute(
                    "UPDATE device_leases SET expires_at=? WHERE device_id=?",
                    (dl_module._add_seconds(dl_module.now_iso(), -1), TEST_DEVICE))
                store.conn.commit()
                try:
                    store.claim(TEST_DEVICE, "owner-b", "sess-b", 3600)
                except dl_module.LeaseRefused:
                    store.clear_dirty(TEST_DEVICE)
                    store.claim(TEST_DEVICE, "owner-b", "sess-b", 3600)
                return store.get(TEST_DEVICE)["lease_uuid"]
            finally:
                store.close()

        with fake.patched(on_launch=steal_the_lease):
            record = DM.run_physical_lifecycle(TEST_DEVICE, self._app_dir, TEST_BUNDLE, "owner-a")
        self.assertEqual(record["verdict"], "FAIL", record["reason"])
        self.assertEqual(DM.exit_code_for_verdict(record["verdict"]), 1)
        stages = {s["name"]: s["verdict"] for s in record["stages"]}
        self.assertEqual(stages.get("lease-final"), "FAIL")
        # B still holds the device: A's exit did not revoke a live lease.
        store = dl_module.DeviceLeaseStore()
        try:
            row = store.get(TEST_DEVICE)
        finally:
            store.close()
        self.assertEqual(row["state"], "leased")
        self.assertEqual(row["owner"], "owner-b")

    # -- Critical 3: uninstall's self-report is not evidence ------------

    def test_uninstall_reporting_success_while_the_bundle_remains_is_not_pass(self):
        """uninstall_app returns PASS, the bundle is still listed. The
        device used to go back to the pool as available carrying it."""
        fake = _FakeDevice(uninstall_actually_removes=False)
        with fake.patched():
            record = DM.run_physical_lifecycle(TEST_DEVICE, self._app_dir, TEST_BUNDLE, "owner-a")
        stages = {s["name"]: s["verdict"] for s in record["stages"]}
        self.assertEqual(stages.get("uninstall"), "PASS")
        self.assertEqual(stages.get("verify-uninstalled"), "FAIL")
        self.assertEqual(record["verdict"], "FAIL", record["reason"])
        self.assertEqual(self._lease_state(), "dirty")

    def test_cleanup_after_a_failure_reads_the_device_not_the_exit_code(self):
        """devicectl errors uninstalling an already-absent bundle the same
        way it errors on a real failure. An install that never landed has
        nothing to clean up, so the device is not quarantined over it."""
        fake = _FakeDevice()
        with fake.patched(install_verdict=("FAIL", {"reason": "install refused"}),
                          uninstall_verdict=("FAIL", {"reason": "no such bundle"})):
            record = DM.run_physical_lifecycle(TEST_DEVICE, self._app_dir, TEST_BUNDLE, "owner-a")
        stages = {s["name"]: s["verdict"] for s in record["stages"]}
        self.assertEqual(stages.get("cleanup-uninstall"), "FAIL")
        self.assertEqual(stages.get("cleanup-verify-absent"), "PASS")
        self.assertEqual(record["verdict"], "FAIL")  # the install really did fail
        # ... but the device itself is observed clean, so it stays usable.
        self.assertEqual(self._lease_state(), "available")

    # -- Critical 4: --keep-installed leaves known residue --------------

    def test_keep_installed_quarantines_instead_of_releasing_clean(self):
        """--keep-installed used to release the device as available with
        the candidate still on it, under a reason string claiming cleanup
        was verified when no cleanup ran."""
        fake = _FakeDevice()
        with fake.patched():
            record = DM.run_physical_lifecycle(TEST_DEVICE, self._app_dir, TEST_BUNDLE,
                                                "owner-a", uninstall_after=False)
        self.assertEqual(self._lease_state(), "dirty")
        self.assertNotIn("cleanup all verified", record["reason"])
        self.assertIn("left installed", record["reason"])
        self.assertIn(TEST_BUNDLE, record["lease"]["dirty_reason"])
        self.assertEqual(record["lease"]["final_state"], "dirty")

    def test_a_quarantined_device_can_be_returned_to_the_pool(self):
        """The rehabilitation path end to end: after #732 every crashed run
        quarantines, so without this the one physical device leaves the
        pool permanently on the first crash with no command to undo it."""
        fake = _FakeDevice()
        with fake.patched():
            DM.run_physical_lifecycle(TEST_DEVICE, self._app_dir, TEST_BUNDLE,
                                      "owner-a", uninstall_after=False)
        self.assertEqual(self._lease_state(), "dirty")
        dl_module, _ = DM._bm_device_lease_module()
        self.assertEqual(dl_module.main(["list", "--dirty-only"]), 0)
        self.assertEqual(dl_module.main(["clear-dirty", "--device", TEST_DEVICE]), 0)
        self.assertEqual(self._lease_state(), "available")

    def test_installed_but_unverified_never_reports_pass(self):
        # Target 2: a device left installed-but-unverified reported as success.
        # install_app claims PASS, but a fresh apps listing does not contain
        # the bundle id -- verify_installed_identity must fail the run.
        with patch("device_matrix.get_device_details", return_value=("PASS", {"details": {}})), \
             patch("device_matrix.install_app", return_value=("PASS", {})), \
             patch("device_matrix.list_installed_apps", return_value=("PASS", {"apps": []})), \
             patch("device_matrix.uninstall_app", return_value=("PASS", {})):
            record = DM.run_physical_lifecycle(TEST_DEVICE, self._app_dir, TEST_BUNDLE, "owner-a")
        self.assertNotEqual(record["verdict"], "PASS")
        names = [s["name"] for s in record["stages"]]
        self.assertIn("verify-installed-identity", names)
        self.assertNotEqual(self._lease_state(), "leased")

    def test_unexpected_exception_still_quarantines_not_leaks(self):
        with patch("device_matrix.get_device_details", side_effect=RuntimeError("boom")):
            record = DM.run_physical_lifecycle(TEST_DEVICE, self._app_dir, TEST_BUNDLE, "owner-a")
        self.assertEqual(record["verdict"], "FAIL")
        self.assertNotEqual(self._lease_state(), "leased")

    def test_no_data_when_bm_device_lease_unimportable(self):
        with patch("device_matrix._bm_device_lease_module", return_value=(None, "simulated unavailable")):
            record = DM.run_physical_lifecycle(TEST_DEVICE, self._app_dir, TEST_BUNDLE, "owner-a")
        self.assertEqual(record["verdict"], "NO-DATA")

    def test_candidate_bundle_id_mismatch_is_refused_before_install(self):
        with patch("device_matrix.get_device_details", return_value=("PASS", {"details": {}})), \
             patch("device_matrix.install_app") as mock_install:
            record = DM.run_physical_lifecycle(TEST_DEVICE, self._app_dir, "com.example.different", "owner-a")
        mock_install.assert_not_called()
        self.assertEqual(record["verdict"], "FAIL")
        self.assertNotEqual(self._lease_state(), "leased")


if __name__ == "__main__":
    unittest.main()

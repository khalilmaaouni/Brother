#!/usr/bin/env python3
"""Tests for device_matrix.py's Android physical-device lifecycle (EPIC
M4.04, the adb equivalent of M4.03's devicectl reservation-through-cleanup
half). Same fixture-tool pattern as test_device_matrix.py's own
DevicectlJsonTests/FullLifecycleAdversarialTests: subprocess.run is
replaced with a fake standing in for adb's own real, documented text
output; only the actual `adb` binary is replaced, every parsing/decision
path under test is real.

FIXTURES ARE SYNTHETIC, copied from adb's own DOCUMENTED output shape
(developer.android.com/tools/adb, developer.android.com/studio/test/
other-testing-tools/monkey, both fetched directly the session this unit
was built), never captured from a real device (none was available on this
machine: no adb on PATH, no ANDROID_HOME/ANDROID_SDK_ROOT set, checked
directly before writing this unit)."""
import inspect
import os
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, __file__.rsplit("/", 1)[0])
import device_matrix as DM

TEST_DEVICE = "TESTSERIAL0123"
TEST_PACKAGE = "com.example.sampleapp"

# Real documented shape of `adb devices -l`: a header line, one tab-
# separated row per device, a blank separator. The state token is the
# first whitespace-run after the tab; "no permissions" is the one
# documented state with an embedded space.
REAL_ADB_DEVICES_READY = (
    "List of devices attached\n"
    "TESTSERIAL0123\tdevice product:sdk_gphone64_x86_64 model:Pixel_7 device:emu64x transport_id:1\n"
    "\n"
)
REAL_ADB_DEVICES_UNAUTHORIZED = (
    "List of devices attached\n"
    "TESTSERIAL0123\tunauthorized transport_id:1\n"
    "\n"
)
REAL_ADB_DEVICES_OFFLINE = (
    "List of devices attached\n"
    "TESTSERIAL0123\toffline transport_id:1\n"
    "\n"
)
REAL_ADB_DEVICES_NO_PERMISSIONS = (
    "List of devices attached\n"
    "TESTSERIAL0123\tno permissions (user in plugdev group; are your udev rules wrong?); "
    "see [http://developer.android.com/tools/device.html] usb:1-1\n"
    "\n"
)
REAL_ADB_DEVICES_EMPTY = "List of devices attached\n\n"

REAL_DUMPSYS_PACKAGE = (
    "Packages:\n"
    "  Package [com.example.sampleapp] (a1b2c3d):\n"
    "    userId=10123\n"
    "    versionCode=42 minSdk=21 targetSdk=33\n"
    "    versionName=1.0\n"
    "    splits=[base]\n"
)
REAL_DUMPSYS_PACKAGE_NOT_FOUND = (
    "Activity Resolver Table:\n"
)


_GUARDS = {"package_id": "_require_valid_package_id",
           "device_id": "_require_valid_device_serial"}


def _find_adb_sinks(param_name):
    """Every top-level function device_matrix.py defines that (a) takes a
    `param_name` parameter and (b) actually shells out to adb in its own
    source -- discovered by INSPECTING the real module, never a hand-kept
    name list.

    A hand-kept list is the exact root cause that let adb_uninstall_app's
    package_id guard go missing once already: the fix and its tests were
    both built by naming "the functions that got fixed" instead of
    enumerating every real sink. The device_id guard added for #729 is
    enumerated the same way for the same reason, so a sink added later
    without its guard fails here rather than shipping. Both `_adb(` and a
    direct `subprocess.run(` count as shelling out: adb_capture_screenshot
    uses the latter to keep the PNG bytes binary."""
    sinks = []
    guard_names = set(_GUARDS.values())
    for name, func in inspect.getmembers(DM, inspect.isfunction):
        if func.__module__ != DM.__name__ or name in guard_names:
            continue
        if param_name not in inspect.signature(func).parameters:
            continue
        src = inspect.getsource(func)
        if "_adb(" in src or "subprocess.run(" in src:
            sinks.append((name, func))
    return sinks


def _hostile_call_kwargs(func, param_name, hostile_value, out_dir=None):
    """Bind `param_name` to hostile_value and every other required
    parameter to something harmless, so the call reaches the guard rather
    than a TypeError."""
    kwargs = {}
    for pname, param in inspect.signature(func).parameters.items():
        if pname == param_name:
            kwargs[pname] = hostile_value
        elif param.default is not inspect.Parameter.empty:
            continue
        elif pname == "device_id":
            kwargs[pname] = TEST_DEVICE
        elif pname == "package_id":
            kwargs[pname] = TEST_PACKAGE
        elif pname == "out_dir":
            kwargs[pname] = out_dir
        else:
            kwargs[pname] = "unused"
    return kwargs


def _fake_adb_run(rules, default_rc=0, default_out="", default_err=""):
    """A subprocess.run(...) stand-in keyed by the adb subcommand tokens
    after "adb". `rules` maps a tuple prefix (e.g. ("devices", "-l")) to
    (returncode, stdout, stderr); an unmatched argv falls back to the
    default triple. This exercises _adb()/_run_raw's real subprocess-args
    handling end to end, only the `adb` executable itself is replaced."""
    class _Proc:
        def __init__(self, rc, out, err):
            self.returncode = rc
            self.stdout = out.encode("utf-8")
            self.stderr = err.encode("utf-8")

    def run(argv, **kwargs):
        assert argv[0] == "adb", argv
        key = tuple(argv[1:])
        for prefix, triple in rules.items():
            if key[:len(prefix)] == prefix:
                return _Proc(*triple)
        return _Proc(default_rc, default_out, default_err)
    return run


class ParseAdbDevicesTests(unittest.TestCase):

    def test_ready_device_parses_with_extra_fields(self):
        rows = DM.parse_adb_devices(REAL_ADB_DEVICES_READY)
        self.assertEqual(len(rows), 1, rows)
        self.assertEqual(rows[0]["serial"], TEST_DEVICE)
        self.assertEqual(rows[0]["state"], "device")
        self.assertEqual(rows[0]["model"], "Pixel_7")
        self.assertEqual(rows[0]["transport_id"], "1")

    def test_no_permissions_state_keeps_its_embedded_space(self):
        rows = DM.parse_adb_devices(REAL_ADB_DEVICES_NO_PERMISSIONS)
        self.assertEqual(len(rows), 1, rows)
        self.assertEqual(rows[0]["state"], "no permissions")

    def test_header_and_blank_line_produce_no_rows(self):
        self.assertEqual(DM.parse_adb_devices(REAL_ADB_DEVICES_EMPTY), [])

    def test_empty_string_returns_empty(self):
        self.assertEqual(DM.parse_adb_devices(""), [])


class AdbListDevicesTests(unittest.TestCase):

    def test_pass_when_adb_runs(self):
        with patch("subprocess.run", side_effect=_fake_adb_run(
                {("devices", "-l"): (0, REAL_ADB_DEVICES_READY, "")})):
            verdict, detail = DM.adb_list_devices()
        self.assertEqual(verdict, "PASS")
        self.assertEqual(len(detail["devices"]), 1)

    def test_no_data_when_adb_binary_is_missing(self):
        with patch("subprocess.run", side_effect=OSError("no such file")):
            verdict, detail = DM.adb_list_devices()
        self.assertEqual(verdict, "NO-DATA")
        self.assertIn("adb unavailable", detail["reason"])
        self.assertEqual(detail["devices"], [])

    def test_no_data_on_timeout(self):
        with patch("subprocess.run", side_effect=subprocess.TimeoutExpired(cmd="adb", timeout=1)):
            verdict, detail = DM.adb_list_devices()
        self.assertEqual(verdict, "NO-DATA")

    def test_pass_with_empty_list_is_distinct_from_adb_missing(self):
        with patch("subprocess.run", side_effect=_fake_adb_run(
                {("devices", "-l"): (0, REAL_ADB_DEVICES_EMPTY, "")})):
            verdict, detail = DM.adb_list_devices()
        self.assertEqual(verdict, "PASS")
        self.assertEqual(detail["devices"], [])


class CheckDeviceAuthorizedTests(unittest.TestCase):
    """The adversarial-review target named in this unit's own brief: an
    unauthorized/offline device must never be silently treated as ready."""

    def test_pass_only_for_state_device(self):
        with patch("subprocess.run", side_effect=_fake_adb_run(
                {("devices", "-l"): (0, REAL_ADB_DEVICES_READY, "")})):
            verdict, detail = DM.check_device_authorized(TEST_DEVICE)
        self.assertEqual(verdict, "PASS")
        self.assertEqual(detail["state"], "device")

    def test_unauthorized_is_no_data_not_pass(self):
        with patch("subprocess.run", side_effect=_fake_adb_run(
                {("devices", "-l"): (0, REAL_ADB_DEVICES_UNAUTHORIZED, "")})):
            verdict, detail = DM.check_device_authorized(TEST_DEVICE)
        self.assertEqual(verdict, "NO-DATA")
        self.assertIn("unauthorized", detail["reason"])
        self.assertEqual(detail["state"], "unauthorized")

    def test_offline_is_no_data_not_pass(self):
        with patch("subprocess.run", side_effect=_fake_adb_run(
                {("devices", "-l"): (0, REAL_ADB_DEVICES_OFFLINE, "")})):
            verdict, detail = DM.check_device_authorized(TEST_DEVICE)
        self.assertEqual(verdict, "NO-DATA")
        self.assertEqual(detail["state"], "offline")

    def test_no_permissions_is_no_data_not_pass(self):
        with patch("subprocess.run", side_effect=_fake_adb_run(
                {("devices", "-l"): (0, REAL_ADB_DEVICES_NO_PERMISSIONS, "")})):
            verdict, detail = DM.check_device_authorized(TEST_DEVICE)
        self.assertEqual(verdict, "NO-DATA")
        self.assertEqual(detail["state"], "no permissions")

    def test_device_not_connected_is_a_distinct_reason(self):
        with patch("subprocess.run", side_effect=_fake_adb_run(
                {("devices", "-l"): (0, REAL_ADB_DEVICES_EMPTY, "")})):
            verdict, detail = DM.check_device_authorized(TEST_DEVICE)
        self.assertEqual(verdict, "NO-DATA")
        self.assertIn("not found", detail["reason"])

    def test_adb_missing_propagates_as_no_data(self):
        with patch("subprocess.run", side_effect=OSError("no such file")):
            verdict, detail = DM.check_device_authorized(TEST_DEVICE)
        self.assertEqual(verdict, "NO-DATA")
        self.assertIn("adb unavailable", detail["reason"])


class InstallUninstallTests(unittest.TestCase):

    def test_install_pass_on_success_marker(self):
        with patch("subprocess.run", side_effect=_fake_adb_run(
                {("-s", TEST_DEVICE, "install"): (0, "Success\n", "")})):
            verdict, detail = DM.adb_install_app(TEST_DEVICE, "/tmp/app.apk")
        self.assertEqual(verdict, "PASS")

    def test_install_fail_on_failure_marker_even_if_exit_zero(self):
        with patch("subprocess.run", side_effect=_fake_adb_run(
                {("-s", TEST_DEVICE, "install"):
                    (0, "Failure [INSTALL_FAILED_VERSION_DOWNGRADE]\n", "")})):
            verdict, detail = DM.adb_install_app(TEST_DEVICE, "/tmp/app.apk")
        self.assertEqual(verdict, "FAIL")
        self.assertIn("INSTALL_FAILED_VERSION_DOWNGRADE", detail["reason"])

    def test_install_fail_on_nonzero_exit_without_success_marker(self):
        with patch("subprocess.run", side_effect=_fake_adb_run(
                {("-s", TEST_DEVICE, "install"): (1, "", "adb: error\n")})):
            verdict, detail = DM.adb_install_app(TEST_DEVICE, "/tmp/app.apk")
        self.assertEqual(verdict, "FAIL")

    def test_install_no_data_when_adb_missing(self):
        with patch("subprocess.run", side_effect=OSError("no such file")):
            verdict, detail = DM.adb_install_app(TEST_DEVICE, "/tmp/app.apk")
        self.assertEqual(verdict, "NO-DATA")

    def test_uninstall_pass_on_success_marker(self):
        with patch("subprocess.run", side_effect=_fake_adb_run(
                {("-s", TEST_DEVICE, "uninstall"): (0, "Success\n", "")})):
            verdict, detail = DM.adb_uninstall_app(TEST_DEVICE, TEST_PACKAGE)
        self.assertEqual(verdict, "PASS")

    def test_uninstall_fail_on_failure_marker(self):
        with patch("subprocess.run", side_effect=_fake_adb_run(
                {("-s", TEST_DEVICE, "uninstall"): (0, "Failure [DELETE_FAILED_INTERNAL_ERROR]\n", "")})):
            verdict, detail = DM.adb_uninstall_app(TEST_DEVICE, TEST_PACKAGE)
        self.assertEqual(verdict, "FAIL")


class VerifyInstalledIdentityTests(unittest.TestCase):

    def test_pass_when_versions_match(self):
        with patch("subprocess.run", side_effect=_fake_adb_run(
                {("-s", TEST_DEVICE, "shell", "dumpsys", "package"): (0, REAL_DUMPSYS_PACKAGE, "")})):
            verdict, detail = DM.adb_verify_installed_identity(
                TEST_DEVICE, TEST_PACKAGE, expected_version_name="1.0", expected_version_code="42")
        self.assertEqual(verdict, "PASS")
        self.assertEqual(detail["versionName"], "1.0")
        self.assertEqual(detail["versionCode"], "42")

    def test_fail_when_package_absent_from_dumpsys(self):
        with patch("subprocess.run", side_effect=_fake_adb_run(
                {("-s", TEST_DEVICE, "shell", "dumpsys", "package"): (0, REAL_DUMPSYS_PACKAGE_NOT_FOUND, "")})):
            verdict, detail = DM.adb_verify_installed_identity(TEST_DEVICE, TEST_PACKAGE)
        self.assertEqual(verdict, "FAIL")
        self.assertIn("not found", detail["reason"])

    def test_fail_when_version_name_mismatches(self):
        with patch("subprocess.run", side_effect=_fake_adb_run(
                {("-s", TEST_DEVICE, "shell", "dumpsys", "package"): (0, REAL_DUMPSYS_PACKAGE, "")})):
            verdict, detail = DM.adb_verify_installed_identity(
                TEST_DEVICE, TEST_PACKAGE, expected_version_name="9.9")
        self.assertEqual(verdict, "FAIL")
        self.assertIn("versionName", detail["mismatches"])

    def test_fail_when_version_code_mismatches(self):
        with patch("subprocess.run", side_effect=_fake_adb_run(
                {("-s", TEST_DEVICE, "shell", "dumpsys", "package"): (0, REAL_DUMPSYS_PACKAGE, "")})):
            verdict, detail = DM.adb_verify_installed_identity(
                TEST_DEVICE, TEST_PACKAGE, expected_version_code="999")
        self.assertEqual(verdict, "FAIL")
        self.assertIn("versionCode", detail["mismatches"])

    def test_no_data_when_adb_missing(self):
        with patch("subprocess.run", side_effect=OSError("no such file")):
            verdict, detail = DM.adb_verify_installed_identity(TEST_DEVICE, TEST_PACKAGE)
        self.assertEqual(verdict, "NO-DATA")


class LaunchAndPidTests(unittest.TestCase):

    def test_launch_pass_on_events_injected(self):
        with patch("subprocess.run", side_effect=_fake_adb_run(
                {("-s", TEST_DEVICE, "shell", "monkey"):
                    (0, "Events injected: 1\n", "")})):
            verdict, detail = DM.adb_launch_app(TEST_DEVICE, TEST_PACKAGE)
        self.assertEqual(verdict, "PASS")

    def test_launch_fail_on_no_activities_found(self):
        with patch("subprocess.run", side_effect=_fake_adb_run(
                {("-s", TEST_DEVICE, "shell", "monkey"):
                    (1, "", "** No activities found to run, monkey aborted.\n")})):
            verdict, detail = DM.adb_launch_app(TEST_DEVICE, TEST_PACKAGE)
        self.assertEqual(verdict, "FAIL")

    def test_find_running_pid_pass_when_pidof_reports_a_pid(self):
        with patch("subprocess.run", side_effect=_fake_adb_run(
                {("-s", TEST_DEVICE, "shell", "pidof"): (0, "12345\n", "")})):
            verdict, detail = DM.adb_find_running_pid(TEST_DEVICE, TEST_PACKAGE)
        self.assertEqual(verdict, "PASS")
        self.assertEqual(detail["pid"], 12345)

    def test_find_running_pid_no_data_not_fail_when_nothing_running(self):
        # Deliberate: observation failure is evidentiary, never a hard stop
        # (mirrors M4.03's own find_running_pid).
        with patch("subprocess.run", side_effect=_fake_adb_run(
                {("-s", TEST_DEVICE, "shell", "pidof"): (1, "", "")})):
            verdict, detail = DM.adb_find_running_pid(TEST_DEVICE, TEST_PACKAGE)
        self.assertEqual(verdict, "NO-DATA")
        self.assertIsNone(detail["pid"])


class ForceStopTests(unittest.TestCase):

    def test_pass_on_zero_exit(self):
        with patch("subprocess.run", side_effect=_fake_adb_run(
                {("-s", TEST_DEVICE, "shell", "am", "force-stop"): (0, "", "")})):
            verdict, detail = DM.adb_force_stop_app(TEST_DEVICE, TEST_PACKAGE)
        self.assertEqual(verdict, "PASS")

    def test_fail_on_nonzero_exit(self):
        with patch("subprocess.run", side_effect=_fake_adb_run(
                {("-s", TEST_DEVICE, "shell", "am", "force-stop"): (1, "", "boom")})):
            verdict, detail = DM.adb_force_stop_app(TEST_DEVICE, TEST_PACKAGE)
        self.assertEqual(verdict, "FAIL")


class EvidenceCaptureTests(unittest.TestCase):

    def test_capture_logcat_never_uses_dash_c(self):
        # Regression guard for the "never destroy evidence" requirement:
        # capture_logcat's own argv must never include the clearing flag.
        seen = {}

        def run(argv, **kwargs):
            seen["argv"] = argv
            class _Proc:
                returncode = 0
                stdout = b"01-01 00:00:00.000  1000  1000 I Test: hello\n"
                stderr = b""
            return _Proc()
        with patch("subprocess.run", side_effect=run):
            verdict, detail = DM.adb_capture_logcat(TEST_DEVICE, None)
        self.assertEqual(verdict, "PASS")
        self.assertIn("-d", seen["argv"])
        self.assertNotIn("-c", seen["argv"])

    def test_capture_logcat_saves_to_out_dir(self):
        with tempfile.TemporaryDirectory() as tmp:
            with patch("subprocess.run", side_effect=_fake_adb_run(
                    {("-s", TEST_DEVICE, "logcat"): (0, "a log line\n", "")})):
                verdict, detail = DM.adb_capture_logcat(TEST_DEVICE, tmp)
            self.assertEqual(verdict, "PASS")
            self.assertTrue(os.path.isfile(detail["saved_to"]))

    def test_capture_screenshot_no_data_without_out_dir(self):
        verdict, detail = DM.adb_capture_screenshot(TEST_DEVICE, None)
        self.assertEqual(verdict, "NO-DATA")

    def test_capture_screenshot_rejects_non_png_output(self):
        with tempfile.TemporaryDirectory() as tmp:
            def run(argv, **kwargs):
                kwargs["stdout"].write(b"not a png")
                class _Proc:
                    returncode = 0
                    stderr = b""
                return _Proc()
            with patch("subprocess.run", side_effect=run):
                verdict, detail = DM.adb_capture_screenshot(TEST_DEVICE, tmp)
            self.assertEqual(verdict, "FAIL")
            self.assertIn("not a valid PNG", detail["reason"])

    def test_capture_screenshot_pass_on_real_png_magic(self):
        with tempfile.TemporaryDirectory() as tmp:
            def run(argv, **kwargs):
                kwargs["stdout"].write(b"\x89PNG\r\n\x1a\n" + b"\x00" * 16)
                class _Proc:
                    returncode = 0
                    stderr = b""
                return _Proc()
            with patch("subprocess.run", side_effect=run):
                verdict, detail = DM.adb_capture_screenshot(TEST_DEVICE, tmp)
            self.assertEqual(verdict, "PASS")
            self.assertTrue(os.path.isfile(detail["saved_to"]))


class RunAndroidPhysicalLifecycleTests(unittest.TestCase):
    """The three targets this unit's own brief asked its self-review to
    hunt: a lease claimed but not released on an install/launch failure
    path, an authorization-pending device silently treated as ready, and
    a hallucinated adb command. Uses the REAL bm_device_lease.py store
    (redirected to a tempfile, never the machine's real default), mocking
    only the adb-facing functions."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self._old_env = os.environ.get("BM_DEVICE_LEASE_DB")
        os.environ["BM_DEVICE_LEASE_DB"] = os.path.join(self._tmp.name, "leases.sqlite3")
        self._apk_path = os.path.join(self._tmp.name, "app.apk")
        with open(self._apk_path, "wb") as fh:
            fh.write(b"fake apk bytes")

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
        with patch("device_matrix.check_device_authorized", return_value=("PASS", {"state": "device"})), \
             patch("device_matrix.adb_install_app", return_value=("FAIL", {"reason": "install refused"})), \
             patch("device_matrix.adb_uninstall_app", return_value=("FAIL", {"reason": "uninstall also refused"})):
            record = DM.run_android_physical_lifecycle(TEST_DEVICE, self._apk_path, TEST_PACKAGE, "owner-a")
        self.assertEqual(record["verdict"], "FAIL")
        state = self._lease_state()
        self.assertNotEqual(state, "leased", "lease was claimed but left stranded as 'leased'")
        self.assertEqual(state, "dirty")

    def test_authorization_pending_device_is_never_silently_treated_as_ready(self):
        # Target 2: an unauthorized device must refuse before touching install.
        with patch("device_matrix.check_device_authorized",
                   return_value=("NO-DATA", {"reason": "device_unauthorized"})), \
             patch("device_matrix.adb_install_app") as mock_install:
            record = DM.run_android_physical_lifecycle(TEST_DEVICE, self._apk_path, TEST_PACKAGE, "owner-a")
        mock_install.assert_not_called()
        self.assertEqual(record["verdict"], "NO-DATA")
        state = self._lease_state()
        self.assertNotEqual(state, "leased", "lease was claimed but left stranded on an auth refusal")
        self.assertEqual(state, "available")

    def test_lease_released_clean_after_full_success(self):
        with patch("device_matrix.check_device_authorized", return_value=("PASS", {"state": "device"})), \
             patch("device_matrix.adb_install_app", return_value=("PASS", {})), \
             patch("device_matrix.adb_verify_installed_identity", return_value=("PASS", {})), \
             patch("device_matrix.adb_launch_app", return_value=("PASS", {})), \
             patch("device_matrix.adb_find_running_pid", return_value=("PASS", {"pid": 999})), \
             patch("device_matrix._collect_evidence_android", return_value=("PASS", {})), \
             patch("device_matrix.adb_force_stop_app", return_value=("PASS", {})), \
             patch("device_matrix.adb_uninstall_app", return_value=("PASS", {})), \
             patch("device_matrix.adb_verify_uninstalled", return_value=("PASS", {"absent": TEST_PACKAGE})):
            record = DM.run_android_physical_lifecycle(TEST_DEVICE, self._apk_path, TEST_PACKAGE, "owner-a")
        self.assertEqual(record["verdict"], "PASS", record["reason"])
        self.assertEqual(self._lease_state(), "available")
        # The claim in the reason string has to match what actually ran.
        self.assertIn("re-verified absent", record["reason"])

    def test_installed_but_unverified_never_reports_pass(self):
        with patch("device_matrix.check_device_authorized", return_value=("PASS", {"state": "device"})), \
             patch("device_matrix.adb_install_app", return_value=("PASS", {})), \
             patch("device_matrix.adb_verify_installed_identity",
                   return_value=("FAIL", {"reason": "package not found"})), \
             patch("device_matrix.adb_uninstall_app", return_value=("PASS", {})):
            record = DM.run_android_physical_lifecycle(TEST_DEVICE, self._apk_path, TEST_PACKAGE, "owner-a")
        self.assertNotEqual(record["verdict"], "PASS")
        names = [s["name"] for s in record["stages"]]
        self.assertIn("verify-installed-identity", names)
        self.assertNotEqual(self._lease_state(), "leased")

    def test_launch_failure_triggers_best_effort_uninstall_and_quarantine_on_double_failure(self):
        with patch("device_matrix.check_device_authorized", return_value=("PASS", {"state": "device"})), \
             patch("device_matrix.adb_install_app", return_value=("PASS", {})), \
             patch("device_matrix.adb_verify_installed_identity", return_value=("PASS", {})), \
             patch("device_matrix.adb_launch_app", return_value=("FAIL", {"reason": "monkey failed"})), \
             patch("device_matrix.adb_uninstall_app", return_value=("PASS", {})), \
             patch("device_matrix.adb_verify_uninstalled", return_value=("PASS", {"absent": TEST_PACKAGE})):
            record = DM.run_android_physical_lifecycle(TEST_DEVICE, self._apk_path, TEST_PACKAGE, "owner-a")
        self.assertEqual(record["verdict"], "FAIL")
        names = [s["name"] for s in record["stages"]]
        self.assertIn("cleanup-uninstall", names)
        # Cleanup is judged by re-reading the device, not by adb's exit code.
        self.assertIn("cleanup-verify-absent", names)
        self.assertEqual(self._lease_state(), "available")

    def test_observe_pid_failure_never_aborts_the_lifecycle(self):
        # Deliberate design point: observation never ABORTS the run, so
        # every later stage still executes. It does reach the verdict,
        # though: a stage nobody could exercise is NO-DATA, and the record
        # says NO-DATA rather than rounding it up to PASS.
        with patch("device_matrix.check_device_authorized", return_value=("PASS", {"state": "device"})), \
             patch("device_matrix.adb_install_app", return_value=("PASS", {})), \
             patch("device_matrix.adb_verify_installed_identity", return_value=("PASS", {})), \
             patch("device_matrix.adb_launch_app", return_value=("PASS", {})), \
             patch("device_matrix.adb_find_running_pid",
                   return_value=("NO-DATA", {"reason": "not running yet", "pid": None})), \
             patch("device_matrix._collect_evidence_android", return_value=("PASS", {})), \
             patch("device_matrix.adb_force_stop_app", return_value=("PASS", {})), \
             patch("device_matrix.adb_uninstall_app", return_value=("PASS", {})), \
             patch("device_matrix.adb_verify_uninstalled", return_value=("PASS", {"absent": TEST_PACKAGE})):
            record = DM.run_android_physical_lifecycle(TEST_DEVICE, self._apk_path, TEST_PACKAGE, "owner-a")
        names = [s["name"] for s in record["stages"]]
        # Not aborted: every later stage still ran.
        self.assertIn("observe-process", names)
        self.assertIn("terminate", names)
        self.assertIn("uninstall", names)
        # But it is not rounded up to PASS either.
        self.assertEqual(record["verdict"], "NO-DATA", record["reason"])
        self.assertNotIn("FAIL", [s["verdict"] for s in record["stages"]])
        # The device itself is observed clean, so it stays usable.
        self.assertEqual(self._lease_state(), "available")

    def test_unexpected_exception_still_quarantines_not_leaks(self):
        with patch("device_matrix.check_device_authorized", side_effect=RuntimeError("boom")):
            record = DM.run_android_physical_lifecycle(TEST_DEVICE, self._apk_path, TEST_PACKAGE, "owner-a")
        self.assertEqual(record["verdict"], "FAIL")
        self.assertNotEqual(self._lease_state(), "leased")

    def test_no_data_when_bm_device_lease_unimportable(self):
        with patch("device_matrix._bm_device_lease_module", return_value=(None, "simulated unavailable")):
            record = DM.run_android_physical_lifecycle(TEST_DEVICE, self._apk_path, TEST_PACKAGE, "owner-a")
        self.assertEqual(record["verdict"], "NO-DATA")

    def test_missing_apk_is_refused_before_install_and_lease_released_clean(self):
        with patch("device_matrix.check_device_authorized", return_value=("PASS", {"state": "device"})), \
             patch("device_matrix.adb_install_app") as mock_install:
            record = DM.run_android_physical_lifecycle(
                TEST_DEVICE, "/no/such/app.apk", TEST_PACKAGE, "owner-a")
        mock_install.assert_not_called()
        self.assertEqual(record["verdict"], "FAIL")
        self.assertEqual(self._lease_state(), "available")

    def test_shares_the_same_lease_store_as_ios_lifecycle(self):
        # Reuse proof: reserving TEST_DEVICE through the iOS-facing
        # reserve_device() must be visible to the Android lifecycle's own
        # claim() call as "already-leased" -- confirms one shared store,
        # not two parallel mechanisms.
        verdict, detail = DM.reserve_device(TEST_DEVICE, "ios-owner", ttl_seconds=120)
        self.assertEqual(verdict, "PASS")
        with patch("device_matrix.check_device_authorized", return_value=("PASS", {"state": "device"})):
            record = DM.run_android_physical_lifecycle(TEST_DEVICE, self._apk_path, TEST_PACKAGE, "android-owner")
        self.assertEqual(record["verdict"], "NO-DATA")
        self.assertEqual(record["stages"][0]["detail"]["reason"], "already-leased")


class AndroidLifecycleAdversarialTests(unittest.TestCase):
    """One test per finding in the #729 review, each written to FAIL
    against the pre-fix run_android_physical_lifecycle. The 49 tests above
    all passed over these defects rather than catching them, which is why
    a green suite was not by itself a pass signal for this unit.

    Same fixture rule as the rest of this file: the REAL bm_device_lease.py
    store (redirected to a tempdir), only the adb-facing seam replaced."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self._old_env = os.environ.get("BM_DEVICE_LEASE_DB")
        os.environ["BM_DEVICE_LEASE_DB"] = os.path.join(self._tmp.name, "leases.sqlite3")
        self._apk_path = os.path.join(self._tmp.name, "app.apk")
        with open(self._apk_path, "wb") as fh:
            fh.write(b"fake apk bytes")

    def tearDown(self):
        if self._old_env is None:
            os.environ.pop("BM_DEVICE_LEASE_DB", None)
        else:
            os.environ["BM_DEVICE_LEASE_DB"] = self._old_env
        self._tmp.cleanup()

    def _lease_row(self):
        dl_module, _ = DM._bm_device_lease_module()
        store = dl_module.DeviceLeaseStore()
        try:
            return store.get(TEST_DEVICE)
        finally:
            store.close()

    def _lease_state(self):
        row = self._lease_row()
        return row["state"] if row else None

    def _happy_path(self, **overrides):
        """The patch stack for a run where everything succeeds, so each
        test below can flip exactly one thing and nothing else."""
        defaults = {
            "check_device_authorized": ("PASS", {"state": "device"}),
            "adb_install_app": ("PASS", {}),
            "adb_verify_installed_identity": ("PASS", {}),
            "adb_launch_app": ("PASS", {}),
            "adb_find_running_pid": ("PASS", {"pid": 999}),
            "_collect_evidence_android": ("PASS", {}),
            "adb_force_stop_app": ("PASS", {}),
            "adb_uninstall_app": ("PASS", {}),
            "adb_verify_uninstalled": ("PASS", {"absent": TEST_PACKAGE}),
        }
        defaults.update(overrides)
        patchers = [patch("device_matrix.%s" % name, return_value=value)
                    for name, value in defaults.items()]
        return patchers

    def _run(self, patchers, **kwargs):
        for p in patchers:
            p.start()
        try:
            return DM.run_android_physical_lifecycle(
                TEST_DEVICE, self._apk_path, TEST_PACKAGE, "owner-a", **kwargs)
        finally:
            for p in reversed(patchers):
                p.stop()

    # -- Critical 1: a stage that FAILs must reach the verdict -----------

    def test_mid_run_disconnect_after_launch_is_never_pass(self):
        """The phone is unplugged right after launch, so every later read
        fails. This reported PASS and exit 0 before: observe-process and
        collect-evidence wrote their verdicts into the stage list and
        nothing ever read them ("Observation only: never gates the
        lifecycle" was the comment that made it deliberate)."""
        record = self._run(self._happy_path(
            adb_find_running_pid=("FAIL", {"reason": "device unplugged", "pid": None}),
            _collect_evidence_android=("FAIL", {"reason": "device unplugged"})))
        stages = {s["name"]: s["verdict"] for s in record["stages"]}
        self.assertEqual(stages.get("observe-process"), "FAIL")
        self.assertEqual(stages.get("collect-evidence"), "FAIL")
        self.assertEqual(record["verdict"], "FAIL", record["reason"])
        self.assertEqual(DM.exit_code_for_verdict(record["verdict"]), 1)
        self.assertNotEqual(self._lease_state(), "leased")

    def test_verdict_counts_every_stage_not_a_hand_picked_subset(self):
        """A stage name the verdict composition never knew about still has
        to be able to fail the run: the derivation reads the stage list the
        function actually built, so adding a stage cannot silently vote
        PASS by omission."""
        record = self._run(self._happy_path())
        self.assertEqual(record["verdict"], "PASS", record["reason"])
        self.assertEqual(DM._verdict_from_stages(record["stages"] + [
            {"name": "some-future-stage", "verdict": "FAIL"}]), "FAIL")
        # An unrecognized verdict string fails loudly rather than being skipped.
        self.assertEqual(DM._verdict_from_stages([{"name": "typo", "verdict": "PSAS"}]), "FAIL")

    # -- Critical 2: losing the lease mid-run must reach the verdict -----

    def test_lease_lost_mid_lifecycle_is_never_pass(self):
        """A's TTL expires mid-run and B legitimately claims the same
        physical phone; A keeps installing, launching and uninstalling on
        it. This reported PASS, exit 0, with the double-hold recorded only
        in lease.release_error beside the verdict. B's lease must also
        survive A's cleanup."""
        dl_module, _ = DM._bm_device_lease_module()

        def steal_the_lease(*_a, **_kw):
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
                self.b_lease_uuid = store.get(TEST_DEVICE)["lease_uuid"]
            finally:
                store.close()
            return ("PASS", {})

        patchers = self._happy_path()
        launch = patch("device_matrix.adb_launch_app", side_effect=steal_the_lease)
        patchers = [p for p in patchers
                    if p.attribute != "adb_launch_app"] + [launch]
        record = self._run(patchers)

        self.assertNotEqual(record["verdict"], "PASS", record["reason"])
        stages = {s["name"]: s["verdict"] for s in record["stages"]}
        self.assertEqual(stages.get("lease-final"), "FAIL",
                         "the lost lease has to be a STAGE, not a side field")
        row = self._lease_row()
        self.assertEqual(row["owner"], "owner-b", "owner-b's live lease was revoked by owner-a")
        self.assertEqual(row["lease_uuid"], self.b_lease_uuid)

    # -- Critical 3: uninstall's self-report is not evidence -------------

    def test_uninstall_reporting_success_while_still_installed_is_never_pass(self):
        """adb says Success, a fresh dumpsys read says the package is still
        there. Before, nothing re-read: verdict PASS, exit 0, device back in
        the pool as available still carrying today's candidate."""
        record = self._run(self._happy_path(
            adb_uninstall_app=("PASS", {"stdout": "Success"}),
            adb_verify_uninstalled=("FAIL", {"reason": "package %s is STILL installed" % TEST_PACKAGE})))
        self.assertEqual(record["verdict"], "FAIL", record["reason"])
        stages = {s["name"]: s["verdict"] for s in record["stages"]}
        self.assertEqual(stages.get("uninstall"), "PASS")
        self.assertEqual(stages.get("verify-uninstalled"), "FAIL")
        self.assertEqual(self._lease_state(), "dirty",
                         "a device still carrying the candidate must not go back to the pool")

    def test_verify_uninstalled_reads_the_device_not_adbs_own_output(self):
        """The re-read itself: present in dumpsys is FAIL, absent is PASS,
        unreadable is NO-DATA (never silently PASS)."""
        still_there = _fake_adb_run({("-s", TEST_DEVICE, "shell", "dumpsys"): (0, REAL_DUMPSYS_PACKAGE, "")})
        with patch("subprocess.run", side_effect=still_there):
            verdict, detail = DM.adb_verify_uninstalled(TEST_DEVICE, TEST_PACKAGE)
        self.assertEqual(verdict, "FAIL")
        self.assertIn("STILL installed", detail["reason"])

        gone = _fake_adb_run({("-s", TEST_DEVICE, "shell", "dumpsys"): (0, REAL_DUMPSYS_PACKAGE_NOT_FOUND, "")})
        with patch("subprocess.run", side_effect=gone):
            verdict, _ = DM.adb_verify_uninstalled(TEST_DEVICE, TEST_PACKAGE)
        self.assertEqual(verdict, "PASS")

        with patch("subprocess.run", side_effect=OSError("no such file")):
            verdict, _ = DM.adb_verify_uninstalled(TEST_DEVICE, TEST_PACKAGE)
        self.assertEqual(verdict, "NO-DATA")

    # -- Critical 4: --keep-installed leaves KNOWN residue ---------------

    def test_keep_installed_quarantines_rather_than_releasing_as_clean(self):
        """With --keep-installed the candidate is still on the phone by
        design. Before, no uninstall ran at all, the device went back to
        the pool as available, and the reason string claimed "requested
        cleanup all verified"."""
        patchers = self._happy_path()
        record = self._run(patchers, uninstall_after=False)
        names = [s["name"] for s in record["stages"]]
        self.assertNotIn("uninstall", names)
        self.assertIn("keep-installed-residue", names)
        self.assertEqual(self._lease_state(), "dirty",
                         "a device with known residue must not be offered as clean")
        self.assertEqual(record["lease"]["final_state"], "dirty")
        self.assertIn("residue", record["lease"]["dirty_reason"])
        self.assertNotIn("cleanup all verified", record["reason"] or "")

    def test_dirty_android_device_is_rehabilitated_by_the_shared_clear_dirty_cli(self):
        """The quarantine above is not a dead end, and Android does not get
        its own remediation verb: the SAME bm_device_lease.py clear-dirty
        path the iOS half uses brings this device back, because both halves
        quarantine into one store."""
        self._run(self._happy_path(), uninstall_after=False)
        self.assertEqual(self._lease_state(), "dirty")
        dl_module, _ = DM._bm_device_lease_module()
        self.assertEqual(dl_module.main(["clear-dirty", "--device", TEST_DEVICE]), 0)
        self.assertEqual(self._lease_state(), "available")
        verdict, _ = DM.reserve_device(TEST_DEVICE, "next-claimant", ttl_seconds=120)
        self.assertEqual(verdict, "PASS")

    # -- Major, Android-specific: adb install -r inherits app data ------

    def test_install_never_layers_over_a_previous_holders_data_by_default(self):
        """`adb install -r` KEEPS the previous install's app data, and
        adb_verify_installed_identity then passes because versionName and
        versionCode do match -- a verified clean install reported over
        inherited state. -r was hard-wired on with no way to turn it off."""
        seen = []

        def record_argv(argv, **kwargs):
            seen.append(list(argv))
            class _P:
                returncode = 0
                stdout = b"Success\n"
                stderr = b""
            return _P()

        with patch("subprocess.run", side_effect=record_argv):
            verdict, _ = DM.adb_install_app(TEST_DEVICE, "/tmp/app.apk")
        self.assertEqual(verdict, "PASS")
        self.assertEqual(len(seen), 1)
        self.assertNotIn("-r", seen[0],
                         "a run claiming an isolated device must not keep the previous "
                         "holder's app data: %r" % (seen[0],))

        seen[:] = []
        with patch("subprocess.run", side_effect=record_argv):
            DM.adb_install_app(TEST_DEVICE, "/tmp/app.apk", reinstall=True)
        self.assertIn("-r", seen[0], "-r must still be reachable as an explicit opt-in")

    def test_lifecycle_install_is_clean_by_default_and_records_layering_when_asked(self):
        """The lifecycle is the caller that never passed reinstall at all.
        Default: no -r. Opt-in: -r, and the record SAYS the install was
        layered, so no later reader mistakes it for a clean one."""
        calls = []

        def spy(device_id, apk_path, reinstall=False, allow_test=True, timeout=180):
            calls.append(reinstall)
            return "PASS", {}

        patchers = [p for p in self._happy_path() if p.attribute != "adb_install_app"]
        patchers.append(patch("device_matrix.adb_install_app", side_effect=spy))
        record = self._run(patchers)
        self.assertEqual(calls, [False])
        install = [s for s in record["stages"] if s["name"] == "install"][0]
        self.assertFalse(install["detail"]["layered_over_existing_data"])
        self.assertFalse(any("LAYERED OVER" in line for line in record["limits"]))

        calls[:] = []
        patchers = [p for p in self._happy_path() if p.attribute != "adb_install_app"]
        patchers.append(patch("device_matrix.adb_install_app", side_effect=spy))
        record = self._run(patchers, reinstall_keep_data=True)
        self.assertEqual(calls, [True])
        install = [s for s in record["stages"] if s["name"] == "install"][0]
        self.assertTrue(install["detail"]["layered_over_existing_data"])
        self.assertTrue(any("LAYERED OVER" in line for line in record["limits"]),
                        "an install layered over existing data must say so in the record")

    def test_reinstall_keep_data_is_reachable_from_the_cli_and_off_by_default(self):
        ap = DM.build_arg_parser()
        base = ["android-lifecycle", "--device", TEST_DEVICE, "--apk", "/tmp/a.apk",
                "--package", TEST_PACKAGE, "--owner", "o"]
        self.assertFalse(ap.parse_args(base).reinstall_keep_data)
        self.assertTrue(ap.parse_args(base + ["--reinstall-keep-data"]).reinstall_keep_data)

    # -- Major, Android-specific: device_id becomes an adb argument -----

    def test_a_device_id_adb_would_parse_as_its_own_flag_is_refused(self):
        """`adb -s --version uninstall <pkg>` retargets the command at a
        different phone than the one the lease was taken on, in a module
        whose whole purpose is per-device isolation. package_id was guarded
        and the parameter selecting WHICH PHONE was not.

        Enumerated by inspecting the real module, not a hand-kept list, so
        a device_id sink added later without its guard fails here."""
        sinks = _find_adb_sinks("device_id")
        self.assertGreaterEqual(len(sinks), 9,
                                 "expected at least the 9 known adb device_id sinks, found: %r"
                                 % ([name for name, _ in sinks],))
        for name, func in sinks:
            with self.subTest(func=name):
                kwargs = _hostile_call_kwargs(func, "device_id", "--version",
                                               out_dir=self._tmp.name)
                with patch("device_matrix._adb") as never_adb, \
                     patch("device_matrix.subprocess.run") as never_run:
                    verdict, detail = func(**kwargs)
                never_adb.assert_not_called()
                never_run.assert_not_called()
                self.assertEqual(verdict, "FAIL", "%s did not refuse a flag-shaped device_id" % name)
                self.assertIn("adb device serial", detail["reason"])

    def test_the_lifecycles_first_stage_also_refuses_a_flag_shaped_serial(self):
        """check_device_authorized reaches adb through adb_list_devices
        rather than _adb directly, so it sits outside the enumeration
        above. It is the lifecycle's FIRST stage, so it gets the guard
        explicitly: a bad serial stops there, before any install."""
        with patch("device_matrix.adb_list_devices") as never:
            verdict, detail = DM.check_device_authorized("--version")
        never.assert_not_called()
        self.assertEqual(verdict, "FAIL")
        self.assertIn("adb device serial", detail["reason"])

    def test_the_device_serial_guard_accepts_the_shapes_adb_really_prints(self):
        """The guard has to refuse a flag without refusing real serials:
        USB serials, emulator names, and network targets all appear in
        `adb devices -l` output."""
        for good in ("TESTSERIAL0123", "emulator-5554", "192.168.1.5:5555",
                     "R58M30ABCDE", "my_device-1"):
            self.assertIsNone(DM._require_valid_device_serial(good), good)
        for bad in ("--version", "-s", "", None, "SERIAL\n", "a b", "dev;rm -rf /", ".."):
            self.assertIsNotNone(DM._require_valid_device_serial(bad), repr(bad))

    # -- Minor: --ttl-seconds had no lower bound -------------------------

    def test_non_positive_ttl_is_a_clean_record_not_a_raw_traceback(self):
        """0 or a negative TTL used to escape as a ValueError traceback out
        of the lease store. It is a caller mistake, so it gets the same
        clean record every other refusal gets."""
        for bad in ("0", "-5"):
            with self.subTest(ttl=bad):
                code = DM.main(["android-lifecycle", "--device", TEST_DEVICE,
                                 "--apk", self._apk_path, "--package", TEST_PACKAGE,
                                 "--owner", "o", "--ttl-seconds", bad])
                self.assertEqual(code, 2)


class AndroidPackageIdValidationTests(unittest.TestCase):
    """Carried from #729's own additions to the iOS suite's file, where
    they did not belong: these test Android code, so they live with the
    Android suite. `adb shell <cmd> <args>` re-parses its trailing
    arguments on the DEVICE's own shell, so an unvalidated package_id is a
    real on-device shell-metacharacter risk even though the host-side
    subprocess call never uses shell=True."""

    def test_valid_package_id_passes_the_grammar_check(self):
        self.assertIsNone(DM._require_valid_package_id("com.example.app"))
        self.assertIsNone(DM._require_valid_package_id("a.b_c.D9"))

    def test_shell_metacharacters_are_refused(self):
        for bad in ("com.foo; rm -rf /sdcard", "com.foo && cat /data",
                    "com.foo`whoami`", "com.foo$(id)", "", "com.foo\n"):
            self.assertIsNotNone(DM._require_valid_package_id(bad), repr(bad))

    def test_single_segment_and_non_string_are_refused(self):
        self.assertIsNotNone(DM._require_valid_package_id("com"))
        self.assertIsNotNone(DM._require_valid_package_id(None))
        self.assertIsNotNone(DM._require_valid_package_id(123))

    def test_every_package_id_adb_sink_refuses_before_any_adb_call(self):
        """Root-cause regression: #729's own guard and its tests were both
        built by naming "the four functions that got fixed" rather than by
        enumerating every real sink, which is exactly how
        adb_uninstall_app's unguarded call was missed. This walks the
        module by inspection instead."""
        sinks = _find_adb_sinks("package_id")
        self.assertGreaterEqual(len(sinks), 6,
                                 "expected at least the 6 known adb package_id sinks, found: %r"
                                 % ([name for name, _ in sinks],))
        for name, func in sinks:
            with self.subTest(func=name):
                kwargs = _hostile_call_kwargs(func, "package_id", "com.foo; rm -rf /sdcard")
                with patch("device_matrix._adb") as never_adb, \
                     patch("device_matrix.subprocess.run") as never_run:
                    verdict, detail = func(**kwargs)
                never_adb.assert_not_called()
                never_run.assert_not_called()
                self.assertEqual(verdict, "FAIL", "%s did not refuse a malicious package_id" % name)

    def test_valid_package_id_actually_reaches_adb(self):
        with patch("device_matrix._adb",
                   return_value={"returncode": 0, "stdout": "", "stderr": "", "exception": None}) as mock_adb:
            DM.adb_force_stop_app(TEST_DEVICE, "com.example.app")
        mock_adb.assert_called_once()


class SafeDeviceIdForPathTests(unittest.TestCase):
    """device_id reaches os.path.join(out_dir, "...-%s-..." % device_id) in
    the logcat/screenshot capture functions. Two layers now stand in front
    of that: the serial guard refuses a traversal-shaped device_id before
    any path is built at all, and _safe_device_id_for_path stays as
    defense in depth for any caller reaching it another way."""

    def test_normal_serial_passes_through_unchanged(self):
        self.assertEqual(DM._safe_device_id_for_path("98F73A29-0110-5E84"), "98F73A29-0110-5E84")

    def test_traversal_characters_are_replaced_no_path_separator_survives(self):
        safe = DM._safe_device_id_for_path("../../etc/passwd")
        self.assertNotIn("/", safe)
        self.assertNotIn("\\", safe)

    def test_pure_traversal_or_empty_raises(self):
        for bad in ("..", ".", ""):
            with self.assertRaises(ValueError):
                DM._safe_device_id_for_path(bad)

    def test_capture_logcat_refuses_a_traversal_device_id_outright(self):
        """Stronger than the sanitize-and-write behavior this replaced:
        nothing is written at all, so the file cannot land anywhere."""
        with tempfile.TemporaryDirectory() as out_dir:
            with patch("device_matrix._adb") as never:
                verdict, evidence = DM.adb_capture_logcat("../../../etc", out_dir)
            never.assert_not_called()
            self.assertEqual(verdict, "FAIL")
            self.assertEqual(os.listdir(out_dir), [])

    def test_capture_screenshot_refuses_a_traversal_device_id_outright(self):
        with tempfile.TemporaryDirectory() as out_dir:
            with patch("device_matrix.subprocess.run") as never:
                verdict, evidence = DM.adb_capture_screenshot("../../../etc", out_dir)
            never.assert_not_called()
            self.assertEqual(verdict, "FAIL")
            self.assertEqual(os.listdir(out_dir), [])

    def test_a_legitimate_serial_still_writes_only_inside_out_dir(self):
        """The guard must not have turned the sanitizer into dead weight:
        a real serial still captures, and still lands inside out_dir."""
        with tempfile.TemporaryDirectory() as out_dir:
            with patch("device_matrix._adb",
                       return_value={"returncode": 0, "stdout": "log line\n", "stderr": "", "exception": None}):
                verdict, evidence = DM.adb_capture_logcat("192.168.1.5:5555", out_dir)
            self.assertEqual(verdict, "PASS")
            self.assertEqual(os.path.dirname(os.path.realpath(evidence["saved_to"])),
                              os.path.realpath(out_dir))
            self.assertEqual(len(os.listdir(out_dir)), 1)


class CliWiringTests(unittest.TestCase):

    def test_android_devices_exits_two_on_no_data(self):
        with patch("subprocess.run", side_effect=OSError("no such file")):
            self.assertEqual(DM.main(["android-devices"]), 2)

    def test_android_authorized_requires_device_flag(self):
        with self.assertRaises(SystemExit):
            DM.build_arg_parser().parse_args(["android-authorized"])

    def test_android_lifecycle_cli_runs_end_to_end_no_data_when_adb_missing(self):
        with tempfile.TemporaryDirectory() as tmp:
            os.environ["BM_DEVICE_LEASE_DB"] = os.path.join(tmp, "leases.sqlite3")
            apk = os.path.join(tmp, "app.apk")
            with open(apk, "wb") as fh:
                fh.write(b"x")
            try:
                with patch("subprocess.run", side_effect=OSError("no such file")):
                    code = DM.main(["android-lifecycle", "--device", TEST_DEVICE, "--apk", apk,
                                     "--package", TEST_PACKAGE, "--owner", "cli-owner"])
                self.assertEqual(code, 2)
            finally:
                os.environ.pop("BM_DEVICE_LEASE_DB", None)


if __name__ == "__main__":
    unittest.main()

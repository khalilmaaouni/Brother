"""Tests for mobile_native_ios_adapter.py (EPIC M3.03).

Follows scripts/test_mobile_workflow.py's own convention: a fake `xcrun` on
PATH, driven by env vars, real subprocess execution -- not a Python-level
mock -- so a call this adapter makes is genuinely exercised end to end
through mobile_workflow.invoke(), not merely asserted about in isolation.
"""
import base64
import json
import os
from pathlib import Path
import sys
import tempfile
import time
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parent))
import mobile_native_ios_adapter as A
import mobile_canonical_action as ACT
import mobile_driver_contract as DC
import contract_check as CC
import test_mobile_canonical_action as MCAT  # reuse its VALID_BY_ACTION/record_for

DEVICE = "11111111-2222-3333-4444-555555555555"

# Real, tiny 1x1 PNG (same fixture bytes test_mobile_workflow.py uses).
_PNG = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+jWZkAAAAASUVORK5CYII=")

TOOL = r'''#!/usr/bin/env python3
import base64, os, pathlib, sys
args = sys.argv[1:]
root = pathlib.Path(os.environ["FIXTURE_ROOT"])
with (root / "calls.log").open("a") as fh:
    fh.write(" ".join(args) + "\n")
if args[:2] == ["simctl", "launch"]:
    sys.exit(1 if os.environ.get("FIXTURE_LAUNCH_FAIL") else 0)
if args[:2] == ["simctl", "openurl"]:
    sys.exit(1 if os.environ.get("FIXTURE_OPENURL_FAIL") else 0)
if args[:2] == ["simctl", "privacy"]:
    sys.exit(1 if os.environ.get("FIXTURE_PRIVACY_FAIL") else 0)
if args[:2] == ["simctl", "location"]:
    sys.exit(1 if os.environ.get("FIXTURE_LOCATION_FAIL") else 0)
if args[:2] == ["simctl", "io"]:
    if os.environ.get("FIXTURE_CAPTURE_FAIL"):
        sys.exit(1)
    path = pathlib.Path(args[-1])
    if os.environ.get("FIXTURE_BAD_PNG"):
        path.write_text("not a png")
    else:
        path.write_bytes(base64.b64decode(
            "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+jWZkAAAAASUVORK5CYII="))
    sys.exit(0)
sys.stderr.write("unexpected xcrun call: %r\n" % (args,))
sys.exit(99)
'''


class MobileNativeIosAdapterTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="brother-m303-tests-")
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.bin = self.root / "bin"
        self.bin.mkdir()
        tool_path = self.bin / "xcrun"
        tool_path.write_text(TOOL)
        tool_path.chmod(0o755)
        self.env = patch.dict(os.environ, {
            "PATH": str(self.bin) + os.pathsep + os.environ["PATH"],
            "FIXTURE_ROOT": str(self.root),
        })
        self.env.start()
        self.addCleanup(self.env.stop)
        self.out = self.root / "evidence"
        self.driver_schema = CC.load_json(DC.DEFAULT_SCHEMA, "driver schema")
        self.action_schema = CC.load_json(ACT.DEFAULT_SCHEMA, "action schema")

    def calls(self):
        log = self.root / "calls.log"
        return log.read_text().splitlines() if log.exists() else []

    def run_action(self, action, **overrides):
        record = MCAT.record_for(action, **overrides)
        stages = []
        result = A.execute_action(record, DEVICE, self.out, stages)
        return result, stages

    # -- self-description matches the real implementation, both directions --

    def test_describe_validates_against_the_real_driver_contract_schema(self):
        problems = DC.check(A.describe(), self.driver_schema)
        self.assertEqual(problems, [])

    def test_supported_actions_equal_action_handlers_keys_exactly(self):
        self.assertEqual(set(A.describe()["supported_actions"]), set(A.ACTION_HANDLERS))

    def test_every_canonical_action_is_either_handled_or_has_an_unsupported_reason(self):
        vocabulary = set(self.action_schema["properties"]["action"]["enum"])
        handled = set(A.ACTION_HANDLERS)
        unsupported = set(A.UNSUPPORTED_REASONS)
        self.assertEqual(handled & unsupported, set(), "an action cannot be both")
        self.assertEqual(handled | unsupported, vocabulary,
                          "every real canonical action must be accounted for one way or the other")

    def test_every_declared_supported_action_actually_runs_not_unsupported(self):
        for action in A.describe()["supported_actions"]:
            with self.subTest(action=action):
                result, _ = self.run_action(action)
                self.assertNotEqual(result["status"], "UNSUPPORTED",
                                     "declared supported but execute_action refused to try: %r" % result)

    def test_every_undeclared_action_returns_structural_unsupported_and_touches_no_device(self):
        vocabulary = set(self.action_schema["properties"]["action"]["enum"])
        for action in vocabulary - set(A.describe()["supported_actions"]):
            with self.subTest(action=action):
                before = self.calls()
                result, stages = self.run_action(action)
                self.assertEqual(result["status"], "UNSUPPORTED")
                self.assertTrue(result["detail"].strip())
                self.assertEqual(stages, [], "an unsupported action must never touch the device")
                self.assertEqual(self.calls(), before, "an unsupported action must never invoke xcrun")

    # -- real handler behavior --

    def test_open_app_launches_via_simctl(self):
        result, stages = self.run_action("OPEN_APP")
        self.assertEqual(result["status"], "PASS")
        self.assertEqual(len(stages), 1)
        self.assertIn("simctl", stages[0]["argv"])
        self.assertIn("launch", stages[0]["argv"])
        self.assertIn(DEVICE, stages[0]["argv"])
        self.assertIn("com.example.app", stages[0]["argv"])

    def test_open_app_failure_is_surfaced_not_swallowed(self):
        with patch.dict(os.environ, {"FIXTURE_LAUNCH_FAIL": "1"}):
            result, stages = self.run_action("OPEN_APP")
        self.assertEqual(result["status"], "FAIL")
        self.assertIn("exited", result["detail"])
        self.assertEqual(stages[0]["status"], "FAIL")

    def test_deeplink_opens_url(self):
        result, _ = self.run_action("DEEPLINK")
        self.assertEqual(result["status"], "PASS")
        self.assertTrue(any("app://open/home" in line for line in self.calls()))

    def test_set_permission_grant_requires_bundle_id_and_refuses_without_a_device_call(self):
        before = self.calls()
        result, stages = self.run_action("SET_PERMISSION")  # allow -> grant, no bundle_id
        self.assertEqual(result["status"], "FAIL")
        self.assertIn("bundle_id", result["detail"])
        self.assertEqual(stages, [])
        self.assertEqual(self.calls(), before)

    def test_set_permission_grant_with_bundle_id_succeeds(self):
        result, stages = self.run_action(
            "SET_PERMISSION", params={"permission": "camera", "state": "allow", "bundle_id": "com.example.app"})
        self.assertEqual(result["status"], "PASS")
        self.assertIn("grant", stages[0]["argv"])
        self.assertIn("com.example.app", stages[0]["argv"])

    def test_set_permission_reset_does_not_require_bundle_id(self):
        result, stages = self.run_action(
            "SET_PERMISSION", params={"permission": "camera", "state": "ask"})
        self.assertEqual(result["status"], "PASS")
        self.assertIn("reset", stages[0]["argv"])

    def test_set_permission_failure_is_surfaced(self):
        with patch.dict(os.environ, {"FIXTURE_PRIVACY_FAIL": "1"}):
            result, _ = self.run_action(
                "SET_PERMISSION", params={"permission": "camera", "state": "allow", "bundle_id": "x"})
        self.assertEqual(result["status"], "FAIL")

    def test_set_location_formats_lat_lon_as_one_comma_joined_argument(self):
        result, stages = self.run_action("SET_LOCATION")
        self.assertEqual(result["status"], "PASS")
        self.assertIn("35.6,139.7", stages[0]["argv"])

    def test_capture_writes_and_validates_a_real_png(self):
        result, stages = self.run_action("CAPTURE")
        self.assertEqual(result["status"], "PASS")
        self.assertEqual(result["screenshot"]["width"], 1)
        self.assertEqual(result["screenshot"]["height"], 1)

    def test_capture_bad_png_refuses(self):
        # mobile_workflow.screenshot_identity() raises Refusal on a bad PNG;
        # execute_action() catches it (never lets it escape) and turns it
        # into a FAIL result -- proving the failure is surfaced, not eaten.
        with patch.dict(os.environ, {"FIXTURE_BAD_PNG": "1"}):
            result, _ = self.run_action("CAPTURE")
        self.assertEqual(result["status"], "FAIL")
        self.assertIn("PNG", result["detail"])

    def test_capture_sanitizes_a_hostile_action_id_to_stay_inside_evidence_root(self):
        record = MCAT.record_for("CAPTURE", action_id="../../../etc/passwd")
        stages = []
        result = A.execute_action(record, DEVICE, self.out, stages)
        self.assertEqual(result["status"], "PASS")
        # The exact sanitized name is an implementation detail; what matters
        # is that nothing escaped evidence_root.
        produced = list(self.out.glob("*capture.png"))
        self.assertEqual(len(produced), 1)
        self.assertTrue(produced[0].resolve().is_relative_to(self.out.resolve()))

    def test_wait_for_with_duration_actually_waits(self):
        start = time.monotonic()
        result, stages = self.run_action("WAIT_FOR", params={"duration_ms": 50})
        elapsed = time.monotonic() - start
        self.assertEqual(result["status"], "PASS")
        self.assertGreaterEqual(elapsed, 0.045)
        self.assertEqual(stages, [], "WAIT_FOR never touches the device")

    def test_wait_for_target_only_is_unsupported_even_though_the_action_is_supported(self):
        self.assertIn("WAIT_FOR", A.describe()["supported_actions"])
        record = MCAT.record_for("WAIT_FOR", target={"selector_type": "text", "value": "Loading"})
        del record["params"]  # satisfy the any_of via target only, per the schema's own shape
        self.assertEqual(ACT.check(record, self.action_schema), [], "fixture record must itself be valid")
        stages = []
        result = A.execute_action(record, DEVICE, self.out, stages)
        self.assertEqual(result["status"], "UNSUPPORTED")
        self.assertIn("duration_ms", result["detail"])
        self.assertEqual(stages, [])

    def test_finish_need_human_impossible_are_bookkeeping_with_no_device_call(self):
        for action in ("FINISH", "NEED_HUMAN", "IMPOSSIBLE"):
            with self.subTest(action=action):
                before = self.calls()
                result, stages = self.run_action(action)
                self.assertEqual(result["status"], "PASS")
                self.assertEqual(stages, [])
                self.assertEqual(self.calls(), before)

    # -- boundary / trust-boundary handling --

    def test_invalid_record_refuses_before_any_device_call(self):
        before = self.calls()
        record = {"schema_version": "mobile-canonical-action-v1", "action_id": "a1", "action": "OPEN_APP"}
        # missing params.app_id
        stages = []
        result = A.execute_action(record, DEVICE, self.out, stages)
        self.assertEqual(result["status"], "FAIL")
        self.assertIn("invalid canonical action record", result["detail"])
        self.assertEqual(stages, [])
        self.assertEqual(self.calls(), before)

    def test_non_dict_record_never_crashes(self):
        stages = []
        result = A.execute_action(["not", "a", "record"], DEVICE, self.out, stages)
        self.assertEqual(result["status"], "FAIL")
        self.assertIsNone(result["action_id"])
        self.assertEqual(stages, [])

    def test_evidence_root_is_created_on_demand(self):
        nested = self.out / "a" / "b" / "c"
        self.assertFalse(nested.exists())
        record = MCAT.record_for("OPEN_APP")
        A.execute_action(record, DEVICE, nested, [])
        self.assertTrue(nested.is_dir())

    def test_evidence_root_accepts_a_plain_str_path_not_only_pathlib_path(self):
        # mobile_workflow.invoke() does `root / "..."`, which raises
        # TypeError on a bare str -- execute_action() must coerce so a
        # caller (e.g. a future M3.07 router) is not forced to know that.
        str_out = str(self.out / "str-path-evidence")
        record = MCAT.record_for("OPEN_APP")
        result = A.execute_action(record, DEVICE, str_out, [])
        self.assertEqual(result["status"], "PASS")
        self.assertTrue(Path(str_out).is_dir())

    # -- CLI --

    def test_cli_describe_prints_valid_json_and_exits_0(self):
        rc = A.main(["describe"])
        self.assertEqual(rc, 0)

    def test_cli_run_maps_statuses_to_exit_codes(self):
        record_path = self.root / "record.json"
        record_path.write_text(json.dumps(MCAT.record_for("OPEN_APP")))
        rc = A.main(["run", "--record", str(record_path), "--device", DEVICE, "--out", str(self.out / "cli-run")])
        self.assertEqual(rc, 0)

    def test_cli_run_on_unsupported_action_exits_nonzero(self):
        record_path = self.root / "record2.json"
        record_path.write_text(json.dumps(MCAT.record_for("TAP_TARGET")))
        rc = A.main(["run", "--record", str(record_path), "--device", DEVICE, "--out", str(self.out / "cli-run2")])
        self.assertEqual(rc, 1)

    def test_cli_run_on_missing_record_file_is_no_data(self):
        rc = A.main(["run", "--record", str(self.root / "missing.json"),
                     "--device", DEVICE, "--out", str(self.out / "cli-run3")])
        self.assertEqual(rc, 2)


class NativeIosAdapterHardeningTests(unittest.TestCase):
    """Regression tests for the two defects adversarial review of PR #721
    found (2026-09-16), neither caught by this suite's original 27 tests.

    Both assert on `stages`, which mobile_workflow.invoke() appends to at
    the START of every call it makes: an empty stages list is therefore
    real evidence that no simctl call was ever reached, not merely that the
    result happened to say FAIL."""

    def setUp(self):
        self.out = Path(tempfile.mkdtemp())

    # --- CRITICAL: unvalidated bundle_id reached subprocess.run ----------

    def test_non_string_bundle_id_is_refused_before_any_simctl_call(self):
        # bundle_id is a param THIS ADAPTER invented: the canonical
        # vocabulary only requires params.permission/state for
        # SET_PERMISSION, so nothing upstream validated it. A dict passed
        # canonical-action validation with zero problems, then reached
        # mobile_workflow.invoke()'s subprocess.run(argv, ...) and raised
        # TypeError, uncaught -- execute_action only catches MW.Refusal and
        # OSError, so it escaped as a raw traceback.
        for bundle_id in ({"a": 1}, ["com.example.app"], 42, 3.5):
            with self.subTest(bundle_id=bundle_id):
                stages = []
                record = MCAT.record_for(
                    "SET_PERMISSION",
                    params={"permission": "camera", "state": "allow", "bundle_id": bundle_id})
                result = A.execute_action(record, DEVICE, self.out, stages)
                self.assertEqual(result["status"], "FAIL")
                self.assertIn("not a valid iOS bundle identifier", result["detail"])
                self.assertEqual(stages, [], "no simctl call may be reached")

    def test_whitespace_only_bundle_id_is_refused_before_any_simctl_call(self):
        # Truthy, so the existing "needs params.bundle_id" guard let it
        # straight through to argv.
        stages = []
        record = MCAT.record_for(
            "SET_PERMISSION",
            params={"permission": "camera", "state": "allow", "bundle_id": "   "})
        result = A.execute_action(record, DEVICE, self.out, stages)
        self.assertEqual(result["status"], "FAIL")
        self.assertEqual(stages, [])

    def test_bundle_id_with_a_trailing_newline_is_refused(self):
        # Why the grammar uses fullmatch plus \Z and not match+$: without
        # re.MULTILINE, $ still matches just before a trailing newline, so
        # "com.example.app\n" would otherwise pass and reach simctl with an
        # embedded newline. Same reasoning as _require_valid_package_id in
        # scripts/device_matrix.py, which this mirrors.
        stages = []
        record = MCAT.record_for(
            "SET_PERMISSION",
            params={"permission": "camera", "state": "allow", "bundle_id": "com.example.app\n"})
        result = A.execute_action(record, DEVICE, self.out, stages)
        self.assertEqual(result["status"], "FAIL")
        self.assertEqual(stages, [])

    def test_a_real_bundle_id_still_passes_validation(self):
        # The guard must refuse bad input without refusing ordinary input.
        for bundle_id in ("com.example.app", "com.example-co.my-app", "a.b"):
            with self.subTest(bundle_id=bundle_id):
                self.assertIsNone(A._require_valid_bundle_id(bundle_id))

    # --- MAJOR: unbounded WAIT_FOR sleep --------------------------------

    def test_wait_for_longer_than_the_records_own_timeout_is_refused(self):
        # _wait_for calls time.sleep() directly, never through
        # mobile_workflow.invoke() (which enforces a 120s timeout for every
        # other action this adapter performs), and never read the record's
        # own timeout_ms. duration_ms=600000 with timeout_ms=500 both
        # validated clean and the adapter would have slept for 10 minutes.
        stages = []
        record = MCAT.record_for("WAIT_FOR", params={"duration_ms": 600000})
        record["timeout_ms"] = 500
        started = time.monotonic()
        result = A.execute_action(record, DEVICE, self.out, stages)
        self.assertEqual(result["status"], "FAIL")
        self.assertIn("500ms cap", result["detail"])
        self.assertLess(time.monotonic() - started, 5.0, "must refuse, not sleep")

    def test_wait_for_beyond_the_default_cap_is_refused_when_no_timeout_is_given(self):
        stages = []
        record = MCAT.record_for(
            "WAIT_FOR", params={"duration_ms": A.DEFAULT_WAIT_TIMEOUT_MS + 1})
        started = time.monotonic()
        result = A.execute_action(record, DEVICE, self.out, stages)
        self.assertEqual(result["status"], "FAIL")
        self.assertIn("%sms cap" % A.DEFAULT_WAIT_TIMEOUT_MS, result["detail"])
        self.assertLess(time.monotonic() - started, 5.0, "must refuse, not sleep")

    def test_the_default_cap_matches_mobile_workflow_invokes_own_timeout(self):
        # The cap is not an arbitrary number: it is deliberately the same
        # deadline invoke() already applies to every other action, so one
        # journey does not carry two different time budgets depending on
        # which handler ran.
        import inspect

        import mobile_workflow as MW
        invoke_default = inspect.signature(MW.invoke).parameters["timeout"].default
        self.assertEqual(A.DEFAULT_WAIT_TIMEOUT_MS, invoke_default * 1000)

    def test_a_short_wait_still_runs_normally(self):
        stages = []
        record = MCAT.record_for("WAIT_FOR", params={"duration_ms": 10})
        result = A.execute_action(record, DEVICE, self.out, stages)
        self.assertEqual(result["status"], "PASS")

    def test_a_wait_exactly_at_the_cap_is_allowed(self):
        # Boundary: the cap is a ceiling, not an exclusive bound, so a
        # record asking for exactly its own timeout must not be refused.
        record = MCAT.record_for("WAIT_FOR", params={"duration_ms": 20})
        record["timeout_ms"] = 20
        self.assertEqual(A._wait_cap_ms(record), 20)
        self.assertEqual(A.execute_action(record, DEVICE, self.out, [])["status"], "PASS")

    def test_an_unusable_timeout_ms_falls_back_to_the_named_default(self):
        # bool is an int subclass in Python, so True must not read as a 1ms
        # cap; zero and negative values are not shorter deadlines either.
        for timeout_ms in (True, 0, -1, "500", None):
            with self.subTest(timeout_ms=timeout_ms):
                record = MCAT.record_for("WAIT_FOR", params={"duration_ms": 10})
                record["timeout_ms"] = timeout_ms
                self.assertEqual(A._wait_cap_ms(record), A.DEFAULT_WAIT_TIMEOUT_MS)


if __name__ == "__main__":
    unittest.main()

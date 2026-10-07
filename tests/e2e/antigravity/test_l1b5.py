import hashlib
import json
import os
import shutil
import tempfile
import unittest

from tests.e2e.antigravity import verify_log as verify_log_mod


def _sha256(data):
    return hashlib.sha256(data).hexdigest()


def _write_file(path, data):
    directory = os.path.dirname(path)
    if directory:
        os.makedirs(directory, exist_ok=True)
    with open(path, "wb") as handle:
        handle.write(data)
    return path


def _good_entries(base, run_id="a" * 32, started_at="2026-01-01T00:00:00Z",
                  host_name="testhost", host_version="1.0"):
    plan = [
        ("PreToolUse", "allow"),
        ("PostToolUse", "no_data"),
        ("PreInvocation", "no_data"),
        ("PostInvocation", "no_data"),
        ("Stop", "allow"),
    ]
    entries = []
    for sequence, (event_name, decision_kind) in enumerate(plan, start=1):
        if event_name in ("PreInvocation", "PostInvocation"):
            raw_in_data = b'{"invocationNum": 1}'
        elif event_name == "Stop":
            raw_in_data = b'{"terminationReason": "done", "fullyIdle": true}'
        else:
            raw_in_data = b'{}'
        raw_out_data = b'{"decision": "allow"}' if decision_kind == "allow" else b'{}'
        raw_in_path = os.path.join(base, "raw_in_%d.txt" % sequence)
        raw_out_path = os.path.join(base, "raw_out_%d.txt" % sequence)
        stderr_path = os.path.join(base, "stderr_%d.txt" % sequence)
        _write_file(raw_in_path, raw_in_data)
        _write_file(raw_out_path, raw_out_data)
        _write_file(stderr_path, b"")
        entries.append({
            "run_id": run_id,
            "environment": "sandbox",
            "started_at": started_at,
            "host_name": host_name,
            "host_version": host_version,
            "os_platform": "linux",
            "plugin_path_abs": "/tmp/plugin",
            "manifest_sha256": "b" * 64,
            "hooks_sha256": "c" * 64,
            "mcp_config_sha256": "d" * 64,
            "event_name": event_name,
            "event_sequence": sequence,
            "tool_name": "no_data",
            "tool_input_sha256": "no_data",
            "raw_in_path": raw_in_path,
            "raw_in_sha256": _sha256(raw_in_data),
            "raw_out_path": raw_out_path,
            "raw_out_sha256": _sha256(raw_out_data),
            "decision_verbatim": "",
            "decision_kind": decision_kind,
            "adapter_invoked": True,
            "adapter_exit_code": 0,
            "adapter_wall_ms": 100,
            "stderr_path": stderr_path,
            "stderr_sha256": "no_data",
            "hook_cwd_abs": "/tmp",
            "outcome": "pass",
        })
    return entries


def _write_log(path, entries):
    with open(path, "w", encoding="utf-8") as handle:
        for entry in entries:
            handle.write(json.dumps(entry) + "\n")
    return path


class TestLogVerifier(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.base = self._tmp.name

    def _good_log(self):
        entries = _good_entries(self.base)
        return _write_log(os.path.join(self.base, "log.jsonl"), entries), entries

    def test_relative_raw_paths_resolve_against_the_log_dir(self):
        # A committed evidence folder must verify from any checkout and any
        # working directory: relative raw paths are read against the log.
        log_path, entries = self._good_log()
        for entry in entries:
            for key in ("raw_in_path", "raw_out_path", "stderr_path"):
                if os.path.isabs(entry[key]):
                    entry[key] = os.path.relpath(entry[key], self.base)
        _write_log(log_path, entries)
        moved = os.path.join(self.base, "..", os.path.basename(self.base) + "-moved")
        shutil.copytree(self.base, moved)
        self.addCleanup(shutil.rmtree, moved, True)
        cwd = os.getcwd()
        os.chdir(tempfile.gettempdir())
        try:
            result = verify_log_mod.verify_log(os.path.join(moved, "log.jsonl"))
        finally:
            os.chdir(cwd)
        self.assertTrue(result["ok"], result["reason"])

    def test_no_data_host_version_fails(self):
        log_path, entries = self._good_log()
        entries[2]["host_version"] = "no_data"
        _write_log(log_path, entries)
        result = verify_log_mod.verify_log(log_path)
        self.assertFalse(result["ok"])
        self.assertIn("host_version", result["reason"])

    def test_good_log_passes(self):
        log_path, _ = self._good_log()
        result = verify_log_mod.verify_log(log_path)
        self.assertTrue(result["ok"], result["reason"])

    def test_missing_raw_hash_fails(self):
        log_path, entries = self._good_log()
        entries[0]["raw_out_sha256"] = "no_data"
        _write_log(log_path, entries)
        result = verify_log_mod.verify_log(log_path)
        self.assertFalse(result["ok"])
        self.assertIn("must be 64 hex", result["reason"])

    def test_raw_value_changed_after_digest_fails(self):
        log_path, entries = self._good_log()
        _write_file(entries[0]["raw_out_path"], b'{"decision": "deny"}')
        result = verify_log_mod.verify_log(log_path)
        self.assertFalse(result["ok"])
        self.assertIn("raw_out_sha256 mismatch", result["reason"])

    def test_missing_stderr_hash_fails(self):
        log_path, entries = self._good_log()
        entry = entries[2]
        corrupt = b'{"garbage": 1}'
        _write_file(entry["raw_in_path"], corrupt)
        entry["raw_in_sha256"] = _sha256(corrupt)
        entry["outcome"] = "fail"
        entry["stderr_sha256"] = "no_data"
        _write_file(entry["stderr_path"], b"PreInvocation received corrupt payload and cannot block by contract: bad shape\n")
        _write_log(log_path, entries)
        result = verify_log_mod.verify_log(log_path)
        self.assertFalse(result["ok"])
        self.assertIn("missing stderr_sha256", result["reason"])

    def test_corrupt_entry_not_pass_fails(self):
        log_path, entries = self._good_log()
        entry = entries[2]
        corrupt = b'{"garbage": 1}'
        _write_file(entry["raw_in_path"], corrupt)
        entry["raw_in_sha256"] = _sha256(corrupt)
        entry["outcome"] = "pass"
        stderr_data = b"PreInvocation received corrupt payload and cannot block by contract: bad shape\n"
        _write_file(entry["stderr_path"], stderr_data)
        entry["stderr_sha256"] = _sha256(stderr_data)
        _write_log(log_path, entries)
        result = verify_log_mod.verify_log(log_path)
        self.assertFalse(result["ok"])
        self.assertIn("marked pass", result["reason"])

    def test_timeout_cap_recomputed_fails(self):
        log_path, entries = self._good_log()
        entries[0]["adapter_wall_ms"] = 16000
        _write_log(log_path, entries)
        result = verify_log_mod.verify_log(log_path)
        self.assertFalse(result["ok"])
        self.assertIn("exceeds cap", result["reason"])

    def test_schema_only_ignores_missing_entries(self):
        log_path, entries = self._good_log()
        _write_log(log_path, entries[:1])
        result = verify_log_mod.verify_log(log_path, schema_only=True)
        self.assertTrue(result["ok"], result["reason"])

    def test_mixed_run_id_fails(self):
        log_path, entries = self._good_log()
        entries[2]["run_id"] = "f" * 32
        _write_log(log_path, entries)
        result = verify_log_mod.verify_log(log_path)
        self.assertFalse(result["ok"])
        self.assertIn("run_id", result["reason"])

    def test_duplicate_event_sequence_fails(self):
        log_path, entries = self._good_log()
        entries[1]["event_sequence"] = 1
        _write_log(log_path, entries)
        result = verify_log_mod.verify_log(log_path)
        self.assertFalse(result["ok"])
        self.assertIn("event_sequence", result["reason"])

    def test_empty_file_is_no_data(self):
        empty = _write_file(os.path.join(self.base, "empty.jsonl"), b"")
        result = verify_log_mod.verify_log(empty)
        self.assertFalse(result["ok"])
        self.assertIn("no_data", result["reason"])

    def test_hostile_input(self):
        with self.assertRaises(ValueError):
            verify_log_mod.verify_log(None)
        with self.assertRaises(ValueError):
            verify_log_mod.verify_log(123)
        with self.assertRaises(ValueError):
            verify_log_mod.verify_log(b"/tmp/x")
        result = verify_log_mod.verify_log("/nonexistent/path")
        self.assertFalse(result["ok"])
        self.assertIn("no_data", result["reason"])

    def test_main_returns_nonzero_on_no_data(self):
        log_path, entries = self._good_log()
        entries[0]["outcome"] = "no_data"
        _write_log(log_path, entries)
        rc = verify_log_mod.main(["verify_log.py", log_path])
        self.assertNotEqual(rc, 0)

    def test_main_unknown_flag_refused(self):
        log_path, _ = self._good_log()
        rc = verify_log_mod.main(["verify_log.py", log_path, "--definitely-not-a-flag"])
        self.assertNotEqual(rc, 0)

    def test_main_hostile_argv_and_paths(self):
        with self.assertRaises(ValueError):
            verify_log_mod.main(None)
        with self.assertRaises(ValueError):
            verify_log_mod.main("verify_log.py")
        with self.assertRaises(ValueError):
            verify_log_mod.main([1, 2])
        self.assertNotEqual(verify_log_mod.main([]), 0)
        self.assertNotEqual(verify_log_mod.main(["verify_log.py"]), 0)
        missing = os.path.join(self.base, "missing.jsonl")
        self.assertNotEqual(verify_log_mod.main(["verify_log.py", missing]), 0)
        empty = _write_file(os.path.join(self.base, "empty.jsonl"), b"")
        self.assertNotEqual(verify_log_mod.main(["verify_log.py", empty]), 0)
        binary = _write_file(os.path.join(self.base, "binary.jsonl"), b"\xff\xfe\x00\x01")
        self.assertNotEqual(verify_log_mod.main(["verify_log.py", binary]), 0)
        self.assertNotEqual(verify_log_mod.main(["verify_log.py", self.base]), 0)

    def test_main_no_data_exit_code(self):
        missing = os.path.join(self.base, "missing.jsonl")
        self.assertEqual(verify_log_mod.main(["verify_log.py", missing]), 2)
        empty = _write_file(os.path.join(self.base, "empty.jsonl"), b"")
        self.assertEqual(verify_log_mod.main(["verify_log.py", empty]), 2)
        binary = _write_file(os.path.join(self.base, "binary.jsonl"), b"\xff\xfe\x00\x01")
        self.assertEqual(verify_log_mod.main(["verify_log.py", binary]), 2)
        directory = os.path.join(self.base, "dir")
        os.makedirs(directory, exist_ok=True)
        self.assertEqual(verify_log_mod.main(["verify_log.py", directory]), 2)

    def test_main_genuine_fail_exit_code_one(self):
        log_path, entries = self._good_log()
        entries[0]["event_sequence"] = 2
        entries[1]["event_sequence"] = 1
        _write_log(log_path, entries)
        self.assertEqual(verify_log_mod.main(["verify_log.py", log_path]), 1)

    def test_adapter_invoked_false_fails(self):
        log_path, entries = self._good_log()
        entries[1]["adapter_invoked"] = False
        _write_log(log_path, entries)
        result = verify_log_mod.verify_log(log_path)
        self.assertFalse(result["ok"])
        self.assertIn("adapter_invoked", result["reason"])

    def test_empty_identity_fields_fail(self):
        for field in ("host_name", "host_version", "tool_name", "plugin_path_abs", "hook_cwd_abs"):
            with self.subTest(field=field):
                log_path, entries = self._good_log()
                entries[0][field] = ""
                _write_log(log_path, entries)
                result = verify_log_mod.verify_log(log_path)
                self.assertFalse(result["ok"])
                self.assertIn(field, result["reason"])

    def test_run_id_not_hex_fails(self):
        log_path, entries = self._good_log()
        entries[0]["run_id"] = "not-hex"
        _write_log(log_path, entries)
        result = verify_log_mod.verify_log(log_path)
        self.assertFalse(result["ok"])
        self.assertIn("run_id", result["reason"])

    def test_started_at_not_iso_fails(self):
        log_path, entries = self._good_log()
        entries[0]["started_at"] = "not-iso"
        _write_log(log_path, entries)
        result = verify_log_mod.verify_log(log_path)
        self.assertFalse(result["ok"])
        self.assertIn("started_at", result["reason"])

    def test_schema_only_non_bool_refused(self):
        log_path, _ = self._good_log()
        for value in (None, "yes", 1, 0, [], float("nan"), float("inf")):
            with self.subTest(value=repr(value)):
                with self.assertRaises(ValueError):
                    verify_log_mod.verify_log(log_path, schema_only=value)

    def test_exit_code_out_of_range_fails(self):
        log_path, entries = self._good_log()
        entries[1]["adapter_exit_code"] = 999
        _write_log(log_path, entries)
        result = verify_log_mod.verify_log(log_path)
        self.assertFalse(result["ok"])
        self.assertIn("adapter_exit_code", result["reason"])

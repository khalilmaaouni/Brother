"""L1b.7 run and write log tests.

Every test fails on the unchanged tree because run_sandbox and write_log
do not exist there; these tests assert the behaviour those functions
add, never the presence of a name.
"""

from __future__ import annotations

import json
import os
import tempfile
import unittest
from unittest import mock

from tests.e2e.antigravity import run_e2e


def _make_minimal_plugin(root):
    with open(os.path.join(root, "plugin.json"), "w", encoding="utf-8") as handle:
        json.dump({"name": "test-plugin"}, handle)
    with open(os.path.join(root, "mcp_config.json"), "w", encoding="utf-8") as handle:
        json.dump({"mcpServers": {}}, handle)
    skill_a = os.path.join(root, "skills", "alpha")
    skill_b = os.path.join(root, "skills", "beta")
    os.makedirs(skill_a, exist_ok=True)
    os.makedirs(skill_b, exist_ok=True)
    with open(os.path.join(skill_a, "SKILL.md"), "w", encoding="utf-8") as handle:
        handle.write("alpha\n")
    with open(os.path.join(skill_b, "SKILL.md"), "w", encoding="utf-8") as handle:
        handle.write("beta\n")
    rules_dir = os.path.join(root, "rules")
    os.makedirs(rules_dir, exist_ok=True)
    with open(os.path.join(rules_dir, "one.md"), "w", encoding="utf-8") as handle:
        handle.write("one\n")
    with open(os.path.join(root, "hooks.json"), "w", encoding="utf-8") as handle:
        json.dump({"hooks": {}}, handle)


def _fake_host(root):
    """A stand-in host binary that prints a version, as the real CLI's --version does."""
    path = os.path.join(root, "fake-host")
    with open(path, "w", encoding="utf-8") as handle:
        handle.write("#!/bin/sh\necho 9.9.9-test\n")
    os.chmod(path, 0o755)
    return path


def _fake_fire_event(host_bin, sandbox, event_name, payload, timeout_s, run_dir=None):
    seq = run_e2e._EVENT_SEQUENCE.get(event_name, 0)
    return {
        "event_name": event_name,
        "event_sequence": seq,
        "adapter_invoked": True,
        "adapter_exit_code": 0,
        "adapter_wall_ms": 1,
        "raw_in_path": "no_data",
        "raw_in_sha256": "0" * 64,
        "raw_out_path": "no_data",
        "raw_out_sha256": "0" * 64,
        "stderr_path": "no_data",
        "decision_verbatim": "",
        "decision_kind": "allow",
        "outcome": "pass",
    }


class TestRunAndWriteLog(unittest.TestCase):

    def test_entry_count_five(self):
        with tempfile.TemporaryDirectory() as src:
            _make_minimal_plugin(src)
            with mock.patch.object(run_e2e, "fire_event", side_effect=_fake_fire_event):
                result = run_e2e.run_sandbox(src, host_bin=_fake_host(src))
            self.assertIn("events", result)
            self.assertEqual(len(result["events"]), 5)
            self.assertEqual(
                [entry["event_sequence"] for entry in result["events"]],
                [1, 2, 3, 4, 5],
            )

    def test_hook_cwd_recorded(self):
        with tempfile.TemporaryDirectory() as src:
            _make_minimal_plugin(src)
            with mock.patch.object(run_e2e, "fire_event", side_effect=_fake_fire_event):
                result = run_e2e.run_sandbox(src, host_bin=_fake_host(src))
            cwds = set()
            for event in result["events"]:
                self.assertIn("hook_cwd_abs", event)
                self.assertTrue(event["hook_cwd_abs"])
                self.assertTrue(os.path.isabs(event["hook_cwd_abs"]))
                cwds.add(event["hook_cwd_abs"])
            self.assertEqual(len(cwds), 1)

    def test_write_log_round_trip(self):
        with tempfile.TemporaryDirectory() as src, tempfile.TemporaryDirectory() as out:
            _make_minimal_plugin(src)
            log_path = os.path.join(out, "run.jsonl")
            with mock.patch.object(run_e2e, "fire_event", side_effect=_fake_fire_event):
                result = run_e2e.run_sandbox(src, host_bin=_fake_host(src))
            run_e2e.write_log(result, log_path)
            self.assertTrue(os.path.isfile(log_path))
            entries = []
            with open(log_path, "r", encoding="utf-8") as handle:
                for line in handle:
                    if line.strip():
                        entries.append(json.loads(line))
            self.assertEqual(len(entries), 5)
            self.assertEqual([entry["event_sequence"] for entry in entries], [1, 2, 3, 4, 5])
            for entry in entries:
                self.assertEqual(entry["environment"], "sandbox")
                self.assertEqual(len(entry["run_id"]), 32)
                self.assertIn("hook_cwd_abs", entry)

    def test_environment_is_threaded_and_validated(self):
        with tempfile.TemporaryDirectory() as src, tempfile.TemporaryDirectory() as run_dir:
            _make_minimal_plugin(src)
            host = _fake_host(run_dir)
            with mock.patch.object(run_e2e, "fire_event", side_effect=_fake_fire_event):
                result = run_e2e.run_sandbox(src, host_bin=host, environment="real", run_dir=run_dir)
            self.assertEqual(result["environment"], "real")
            self.assertTrue(all(e["environment"] == "real" for e in result["events"]))
            for bad in ("", "live", None, 1):
                refused = run_e2e.run_sandbox(src, host_bin=host, environment=bad)
                self.assertFalse(refused["ok"])
                self.assertEqual(refused["events"], [])

    def test_real_mode_fires_in_place_and_writes_nothing_into_the_plugin(self):
        seen = []
        def spy(host_bin, sandbox, event_name, payload, timeout_s, run_dir=None):
            seen.append((sandbox, run_dir))
            return _fake_fire_event(host_bin, sandbox, event_name, payload, timeout_s)
        with tempfile.TemporaryDirectory() as src, tempfile.TemporaryDirectory() as tools:
            _make_minimal_plugin(src)
            before = sorted(os.listdir(src))
            with mock.patch.object(run_e2e, "fire_event", side_effect=spy):
                run_e2e.run_sandbox(src, host_bin=_fake_host(tools), environment="real")
            self.assertEqual({s for s, _ in seen}, {os.path.abspath(src)})
            self.assertTrue(all(r and not r.startswith(os.path.abspath(src)) for _, r in seen), seen)
            self.assertEqual(sorted(os.listdir(src)), before)

    def test_the_scratch_copy_is_removed_when_raw_lives_elsewhere(self):
        made = []
        real_mkdtemp = run_e2e.tempfile.mkdtemp
        def spy_mkdtemp(*a, **k):
            path = real_mkdtemp(*a, **k); made.append(path); return path
        with tempfile.TemporaryDirectory() as src, tempfile.TemporaryDirectory() as run_dir:
            _make_minimal_plugin(src)
            host = _fake_host(run_dir)
            with mock.patch.object(run_e2e, "fire_event", side_effect=_fake_fire_event), \
                    mock.patch.object(run_e2e.tempfile, "mkdtemp", side_effect=spy_mkdtemp):
                run_e2e.run_sandbox(src, host_bin=host, run_dir=run_dir)
            self.assertEqual(len(made), 1)
            self.assertFalse(os.path.exists(made[0]), "the sandbox copy leaked")

    def test_write_log_stores_raw_paths_relative_to_the_log(self):
        with tempfile.TemporaryDirectory() as out:
            raw = os.path.join(out, "run.jsonl.raw", "raw", "1-Stop.in")
            elsewhere = "/elsewhere/2-Stop.out"
            log_path = os.path.join(out, "run.jsonl")
            run_e2e.write_log({"events": [{"raw_in_path": raw, "raw_out_path": elsewhere,
                                           "stderr_path": "no_data"}]}, log_path)
            with open(log_path, encoding="utf-8") as handle:
                entry = json.loads(handle.readline())
            self.assertEqual(entry["raw_in_path"], os.path.join("run.jsonl.raw", "raw", "1-Stop.in"))
            self.assertEqual(entry["raw_out_path"], elsewhere)
            self.assertEqual(entry["stderr_path"], "no_data")

    def test_launcher_real_mode_writes_a_verifiable_real_log(self):
        # The ENTRY POINT: --real used to run the sandbox flow and only print
        # the mode. Now the log itself says real, verifies with its raw bytes
        # beside it, and the installed plugin directory is left untouched.
        import shutil
        import subprocess
        import sys
        from tests.e2e.antigravity import verify_log
        with tempfile.TemporaryDirectory() as tmp:
            plugin = os.path.join(tmp, "installed")
            shutil.copytree(os.path.join(run_e2e.REPO_ROOT, "bundle", ".antigravity-plugin"), plugin)
            before = sorted(os.listdir(plugin))
            log_path = os.path.join(tmp, "ev", "run.jsonl")
            env = dict(os.environ, ANTIGRAVITY_BIN=_fake_host(tmp))
            proc = subprocess.run(["sh", os.path.join(run_e2e.REPO_ROOT, "scripts", "e2e_antigravity.sh"),
                                   "--real", "--plugin", plugin, "--log", log_path],
                                  capture_output=True, text=True, env=env, timeout=120)
            self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
            self.assertEqual(json.loads(proc.stdout.strip().splitlines()[-1])["environment"], "real")
            result = verify_log.verify_log(log_path)
            self.assertTrue(result["ok"], result["reason"])
            self.assertEqual({e["environment"] for e in result["entries"]}, {"real"})
            self.assertEqual({e["hook_cwd_abs"] for e in result["entries"]}, {os.path.abspath(plugin)})
            self.assertEqual(sorted(os.listdir(plugin)), before, "the run wrote into the installed plugin")

    def test_launcher_never_overwrites_a_log_with_an_empty_run(self):
        import subprocess
        with tempfile.TemporaryDirectory() as tmp:
            log_path = os.path.join(tmp, "run.jsonl")
            with open(log_path, "w", encoding="utf-8") as handle:
                handle.write("recorded\n")
            env = dict(os.environ, ANTIGRAVITY_BIN="/usr/bin/true")
            proc = subprocess.run(["sh", os.path.join(run_e2e.REPO_ROOT, "scripts", "e2e_antigravity.sh"),
                                   "--sandbox", "--log", log_path],
                                  capture_output=True, text=True, env=env, timeout=120)
            self.assertEqual(proc.returncode, 1, proc.stdout + proc.stderr)
            self.assertIn("host_version not captured", proc.stdout)
            with open(log_path, encoding="utf-8") as handle:
                self.assertEqual(handle.read(), "recorded\n")

    def test_hostile_input_refused(self):
        with self.assertRaises(ValueError):
            run_e2e.write_log(None, "/tmp/brother-l1b7-does-not-exist.jsonl")
        with self.assertRaises(ValueError):
            run_e2e.write_log({"events": "not a list"}, "/tmp/brother-l1b7-does-not-exist.jsonl")
        with self.assertRaises(ValueError):
            run_e2e.write_log({"events": []}, None)
        with self.assertRaises(ValueError):
            run_e2e.write_log({"events": []}, "")
        with self.assertRaises(ValueError):
            run_e2e.write_log({"events": ["not a dict"]}, "/tmp/brother-l1b7-does-not-exist.jsonl")

        result = run_e2e.run_sandbox(None)
        self.assertFalse(result["ok"])
        self.assertTrue(result["reason"])

        result = run_e2e.run_sandbox(12345)
        self.assertFalse(result["ok"])

        result = run_e2e.run_sandbox("/nonexistent-path-xyz-does-not-exist")
        self.assertFalse(result["ok"])


if __name__ == "__main__":
    unittest.main()

"""L1b.3 Event firing tests.

Exercise fire_event, capture_raw and sha256_file from
tests.e2e.antigravity.run_e2e against the real hook adapter.
"""

import hashlib
import json
import os
import sys
import tempfile
import unittest

from tests.e2e.antigravity import run_e2e


PAYLOADS = {
    "PreToolUse": {
        "toolCall": {"name": "run_command", "args": {"CommandLine": "ls"}},
    },
    "PostToolUse": {"stepIdx": 1, "error": ""},
    "PreInvocation": {"invocationNum": 1},
    "PostInvocation": {"invocationNum": 1},
    "Stop": {"terminationReason": "model_stop", "fullyIdle": True},
}

EXPECTED_SEQUENCE = {
    "PreToolUse": 1,
    "PostToolUse": 2,
    "PreInvocation": 3,
    "PostInvocation": 4,
    "Stop": 5,
}


PLUGIN = os.path.join(run_e2e.REPO_ROOT, "bundle", ".antigravity-plugin")


def _rewire(sandbox, event, command):
    """Point the ONE hook the sandbox hooks.json wires to ``event`` at ``command``."""
    path = os.path.join(sandbox, "hooks.json")
    with open(path, encoding="utf-8") as handle:
        data = json.load(handle)
    for group in data.values():
        for entry in group.get(event, []):
            for hook in entry.get("hooks", [entry]):
                hook["command"] = command
    with open(path, "w", encoding="utf-8") as handle:
        json.dump(data, handle)


def _script(sandbox, name, body):
    with open(os.path.join(sandbox, "scripts", name), "w", encoding="utf-8") as handle:
        handle.write(body)
    return "python3 scripts/%s" % name


class TestEventFiring(unittest.TestCase):
    def setUp(self):
        # The sandbox is a copy of the shipped plugin, so every event runs the
        # command ITS hooks.json wires, from that file's directory.
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.sandbox = os.path.realpath(self.tmp.name)
        run_e2e._copytree_contents(PLUGIN, self.sandbox)

    def test_adapter_invoked(self):
        for event, payload in PAYLOADS.items():
            with self.subTest(event=event):
                res = run_e2e.fire_event(
                    host_bin=sys.executable,
                    sandbox=self.sandbox,
                    event_name=event,
                    payload=payload,
                    timeout_s=5.0,
                )
                self.assertTrue(res["adapter_invoked"], msg=res)
                self.assertEqual(res["outcome"], "pass", msg=res)

    def test_five_events_fire(self):
        seen = set()
        for event, payload in PAYLOADS.items():
            res = run_e2e.fire_event(
                host_bin=sys.executable,
                sandbox=self.sandbox,
                event_name=event,
                payload=payload,
                timeout_s=5.0,
            )
            self.assertEqual(res["event_sequence"], EXPECTED_SEQUENCE[event])
            seen.add(res["event_sequence"])
            self.assertTrue(os.path.isfile(res["raw_in_path"]), msg=res)
            self.assertTrue(os.path.isfile(res["raw_out_path"]), msg=res)
            self.assertTrue(os.path.isfile(res["stderr_path"]), msg=res)
        self.assertEqual(seen, {1, 2, 3, 4, 5})

    def test_raw_hashes_recompute(self):
        res = run_e2e.fire_event(
            host_bin=sys.executable,
            sandbox=self.sandbox,
            event_name="PreToolUse",
            payload=PAYLOADS["PreToolUse"],
            timeout_s=5.0,
        )
        self.assertTrue(res["adapter_invoked"], msg=res)
        with open(res["raw_out_path"], "rb") as handle:
            raw_out = handle.read()
        self.assertEqual(
            res["raw_out_sha256"], hashlib.sha256(raw_out).hexdigest()
        )
        with open(res["raw_in_path"], "rb") as handle:
            raw_in = handle.read()
        self.assertEqual(
            res["raw_in_sha256"], hashlib.sha256(raw_in).hexdigest()
        )

    def test_capture_raw_hostile_input(self):
        with self.assertRaises(ValueError):
            run_e2e.capture_raw(None, "PreToolUse", 1, b"data", ".in")
        with self.assertRaises(ValueError):
            run_e2e.capture_raw(self.sandbox, None, 1, b"data", ".in")
        with self.assertRaises(ValueError):
            run_e2e.capture_raw(self.sandbox, "PreToolUse", True, b"data", ".in")
        with self.assertRaises(ValueError):
            run_e2e.capture_raw(self.sandbox, "PreToolUse", 1, "not bytes", ".in")
        with self.assertRaises(ValueError):
            run_e2e.capture_raw(self.sandbox, "PreToolUse", 1, b"data", None)

    def test_sha256_file_hostile_input(self):
        with self.assertRaises(ValueError):
            run_e2e.sha256_file(None)
        with self.assertRaises(ValueError):
            run_e2e.sha256_file("")
        with self.assertRaises(ValueError):
            run_e2e.sha256_file(os.path.join(self.sandbox, "missing"))
        with self.assertRaises(ValueError):
            run_e2e.sha256_file(self.sandbox)

    def test_sha256_file_correct(self):
        path = os.path.join(self.sandbox, "data.txt")
        with open(path, "wb") as handle:
            handle.write(b"hello")
        expected = hashlib.sha256(b"hello").hexdigest()
        self.assertEqual(run_e2e.sha256_file(path), expected)

    def test_fire_event_hostile_input(self):
        res = run_e2e.fire_event(None, self.sandbox, "PreToolUse", {}, 5.0)
        self.assertEqual(res["outcome"], "no_data")
        self.assertFalse(res["adapter_invoked"])
        res = run_e2e.fire_event(
            sys.executable, self.sandbox, "PreToolUse", "not a dict", 5.0
        )
        self.assertEqual(res["outcome"], "fail")
        self.assertEqual(res["decision_kind"], "deny")
        res = run_e2e.fire_event(
            sys.executable, self.sandbox, "PreToolUse", {}, "slow"
        )
        self.assertEqual(res["outcome"], "fail")
        self.assertEqual(res["decision_kind"], "deny")

    def test_unknown_event_name(self):
        res = run_e2e.fire_event(
            sys.executable, self.sandbox, "NotAnEvent", {}, 5.0
        )
        self.assertEqual(res["outcome"], "no_data")
        self.assertFalse(res["adapter_invoked"])

    def test_corrupt_pre_tool_denies(self):
        res = run_e2e.fire_event(
            host_bin=sys.executable,
            sandbox=self.sandbox,
            event_name="PreToolUse",
            payload={"garbage": 1},
            timeout_s=5.0,
        )
        self.assertTrue(res["adapter_invoked"], msg=res)
        self.assertEqual(res["decision_kind"], "deny", msg=res)
        self.assertEqual(res["outcome"], "pass", msg=res)

    def test_corrupt_stop_continues(self):
        res = run_e2e.fire_event(
            host_bin=sys.executable,
            sandbox=self.sandbox,
            event_name="Stop",
            payload={"garbage": 1},
            timeout_s=5.0,
        )
        self.assertTrue(res["adapter_invoked"], msg=res)
        self.assertEqual(res["decision_kind"], "continue", msg=res)
        self.assertEqual(res["outcome"], "pass", msg=res)

    def test_corrupt_post_invocation_terminates(self):
        res = run_e2e.fire_event(
            host_bin=sys.executable,
            sandbox=self.sandbox,
            event_name="PostInvocation",
            payload={"garbage": 1},
            timeout_s=5.0,
        )
        self.assertTrue(res["adapter_invoked"], msg=res)
        self.assertEqual(res["decision_kind"], "terminate", msg=res)
        self.assertEqual(res["outcome"], "pass", msg=res)

    def test_corrupt_pre_invocation_no_block(self):
        res = run_e2e.fire_event(
            host_bin=sys.executable,
            sandbox=self.sandbox,
            event_name="PreInvocation",
            payload={"garbage": 1},
            timeout_s=5.0,
        )
        self.assertTrue(res["adapter_invoked"], msg=res)
        self.assertEqual(res["decision_kind"], "no_data", msg=res)
        self.assertEqual(res["outcome"], "pass", msg=res)

    def test_timeout_bounded(self):
        _rewire(self.sandbox, "PreToolUse", _script(
            self.sandbox, "slow_adapter.py",
            "import time\ntime.sleep(2)\nprint('{\"decision\": \"allow\"}')\n"))
        res = run_e2e.fire_event(
            host_bin=sys.executable,
            sandbox=self.sandbox,
            event_name="PreToolUse",
            payload=PAYLOADS["PreToolUse"],
            timeout_s=0.5,
        )
        self.assertEqual(res["outcome"], "fail", msg=res)
        self.assertFalse(res["adapter_invoked"], msg=res)

    def test_the_hooks_json_timeout_caps_the_event(self):
        # REQ-TIMEOUT: the cap the package declares wins over a looser caller.
        _rewire(self.sandbox, "Stop", _script(
            self.sandbox, "slow_stop.py",
            "import time\ntime.sleep(3)\nprint('{\"decision\": \"allow\"}')\n"))
        path = os.path.join(self.sandbox, "hooks.json")
        with open(path, encoding="utf-8") as handle:
            data = json.load(handle)
        for group in data.values():
            for hook in group["Stop"]:
                hook["timeout"] = 1
        with open(path, "w", encoding="utf-8") as handle:
            json.dump(data, handle)
        res = run_e2e.fire_event(sys.executable, self.sandbox, "Stop", PAYLOADS["Stop"], 30.0)
        self.assertEqual(res["outcome"], "fail", msg=res)
        self.assertLess(res["adapter_wall_ms"], 2900, msg=res)

    def test_fire_runs_the_command_hooks_json_wires(self):
        # REQ-CWD: the wired command runs, never one rebuilt from a repository
        # adapter path. The real adapter allows `ls`; this wiring answers ask.
        command = _script(self.sandbox, "ask_adapter.py",
                          "import sys\nsys.stdin.read()\nprint('{\"decision\": \"ask\"}')\n")
        _rewire(self.sandbox, "PreToolUse", command)
        res = run_e2e.fire_event(sys.executable, self.sandbox, "PreToolUse",
                                 PAYLOADS["PreToolUse"], 5.0)
        self.assertEqual(res["decision_kind"], "ask", msg=res)
        self.assertEqual(res["hook_command"], command)

    def test_hook_cwd_resolves_from_hooks_json_dir(self):
        _rewire(self.sandbox, "Stop", _script(
            self.sandbox, "cwd_adapter.py",
            "import os, sys\nsys.stdin.read()\nsys.stderr.write(os.getcwd())\n"
            "print('{\"decision\": \"allow\"}')\n"))
        res = run_e2e.fire_event(sys.executable, self.sandbox, "Stop",
                                 PAYLOADS["Stop"], 5.0)
        self.assertEqual(res["hook_cwd_abs"], self.sandbox, msg=res)
        with open(res["stderr_path"], encoding="utf-8") as handle:
            self.assertEqual(os.path.realpath(handle.read()), self.sandbox)

    def test_host_bin_is_never_executed(self):
        marker = os.path.join(self.sandbox, "host-ran")
        host = os.path.join(self.sandbox, "fake-host")
        with open(host, "w", encoding="utf-8") as handle:
            handle.write("#!/bin/sh\ntouch '%s'\n" % marker)
        os.chmod(host, 0o755)
        res = run_e2e.fire_event(host, self.sandbox, "PreToolUse",
                                 PAYLOADS["PreToolUse"], 5.0)
        self.assertTrue(res["adapter_invoked"], msg=res)
        self.assertFalse(os.path.exists(marker), "fire_event executed host_bin")

    def test_matcher_gap_is_not_fired(self):
        # REQ-MATCHER-FINDING: a read tool the matcher excludes fires nothing.
        res = run_e2e.fire_event(sys.executable, self.sandbox, "PreToolUse",
                                 {"toolCall": {"name": "view_file", "args": {}}}, 5.0)
        self.assertFalse(res["adapter_invoked"], msg=res)
        self.assertEqual(res["outcome"], "no_data", msg=res)
        self.assertIn("matcher gap", res["reason"])

    def test_unreadable_or_ambiguous_wiring_is_refused(self):
        hooks = os.path.join(self.sandbox, "hooks.json")
        with open(hooks, encoding="utf-8") as handle:
            data = json.load(handle)
        group = next(iter(data.values()))
        group["Stop"] = group["Stop"] * 2
        with open(hooks, "w", encoding="utf-8") as handle:
            json.dump(data, handle)
        res = run_e2e.fire_event(sys.executable, self.sandbox, "Stop", PAYLOADS["Stop"], 5.0)
        self.assertFalse(res["adapter_invoked"], msg=res)
        self.assertIn("refusing to pick one", res["reason"])
        group["PreToolUse"][0]["matcher"] = "("
        group["Stop"] = group["Stop"][:1]
        with open(hooks, "w", encoding="utf-8") as handle:
            json.dump(data, handle)
        res = run_e2e.fire_event(sys.executable, self.sandbox, "PreToolUse",
                                 PAYLOADS["PreToolUse"], 5.0)
        self.assertIn("not a valid pattern", res["reason"])
        with open(hooks, "w", encoding="utf-8") as handle:
            handle.write("{not json")
        res = run_e2e.fire_event(sys.executable, self.sandbox, "Stop", PAYLOADS["Stop"], 5.0)
        self.assertFalse(res["adapter_invoked"], msg=res)
        self.assertIn("hooks.json", res["reason"])

    def test_raw_bytes_go_to_run_dir(self):
        with tempfile.TemporaryDirectory() as run_dir:
            res = run_e2e.fire_event(sys.executable, self.sandbox, "Stop",
                                     PAYLOADS["Stop"], 5.0, run_dir=run_dir)
            self.assertTrue(res["raw_in_path"].startswith(run_dir), msg=res)
            self.assertFalse(os.path.exists(os.path.join(self.sandbox, "raw")))


if __name__ == "__main__":
    unittest.main()

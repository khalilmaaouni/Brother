#!/usr/bin/env python3
"""Tests for the bm_cursor_hook --run adapter.

The adapter lets the Claude Code shaped Brother hooks run behind Cursor hook
events. It reads a Cursor payload, translates it to the Claude shape, runs the
named hook with that JSON on stdin and maps the child's decision back to the
Cursor stdout contract. These tests drive the adapter only as a subprocess so
they exercise the same boundary the shipped hooks.json uses.
"""

import json
import os
import shlex
import subprocess
import sys
import tempfile
import time
import unittest

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
ADAPTER = os.path.join(REPO_ROOT, "products", "brothermode", "tools", "bm_cursor_hook.py")
HOOKS_JSON = os.path.join(REPO_ROOT, "bundle", "cursor-hooks", "hooks.json")

# A stand in for a Claude shaped Brother hook: it only sees the deny condition
# when the payload carries tool_name "Bash" plus a command containing "rm ".
REPRO_STUB = (
    "import json\n"
    "import sys\n"
    "payload = json.load(sys.stdin)\n"
    "command = payload.get('tool_input', {}).get('command', '')\n"
    "if payload.get('tool_name') == 'Bash' and 'rm ' in command:\n"
    "    sys.stderr.write('no rm here\\n')\n"
    "    sys.exit(2)\n"
    "sys.exit(0)\n"
)

# A Claude hook that denies the Claude way: nested JSON on stdout, exit 0.
NESTED_DENY_STUB = (
    "import json\n"
    "import sys\n"
    "json.load(sys.stdin)\n"
    "decision = {'hookSpecificOutput': {'permissionDecision': 'deny',"
    " 'permissionDecisionReason': 'X'}}\n"
    "print(json.dumps(decision))\n"
    "sys.exit(0)\n"
)

# A hook that always allows and prints nothing.
ALLOW_STUB = (
    "import sys\n"
    "sys.stdin.read()\n"
    "sys.exit(0)\n"
)

# A hook that hangs past any sane timeout.
SLEEP_STUB = (
    "import sys\n"
    "import time\n"
    "sys.stdin.read()\n"
    "time.sleep(5)\n"
    "sys.exit(0)\n"
)

# A hook that crashes with an uncaught exception, never a SystemExit. Proves
# the adapter's fail-open path catches more than the "exit(2)" case.
CRASH_STUB = (
    "import sys\n"
    "sys.stdin.read()\n"
    "raise RuntimeError('boom, not a SystemExit')\n"
)

# A hook that always denies, used against non-gate events to prove the gate
# set decides the outcome, not the wrapped script's own exit code.
ALWAYS_DENY_STUB = (
    "import sys\n"
    "sys.stdin.read()\n"
    "sys.stderr.write('always deny\\n')\n"
    "sys.exit(2)\n"
)

# WBS-90/U7 (2026-09-13): events Cursor's Hooks vendor page names (read
# 2026-08-07 and 2026-09-13, both dates recorded in
# products/brothermode/tools/bm_runtimes.py's runtime registry) that
# bm_cursor_hook.py now RESERVES: recognized so "unknown event" no longer
# fires for them, but never gated and never wired to a hooks.json entry.
NEW_RESERVED_EVENTS = (
    "postToolUseFailure",
    "subagentStart",
    "subagentStop",
    "beforeMCPExecution",
    "afterMCPExecution",
    "beforeReadFile",
    "beforeSubmitPrompt",
    "afterAgentResponse",
    "afterAgentThought",
    "workspaceOpen",
)

# Cursor Tab (autocomplete) events. The plan says do not wire Tab events;
# they are not agent events, so they stay off EVENTS entirely and must
# still land on the unknown-event fail-open path.
TAB_EVENTS = ("beforeTabFileRead", "afterTabFileEdit")


class CursorHookRunTest(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.tmp = self._tmp.name

    def write_stub(self, name, source):
        path = os.path.join(self.tmp, name)
        with open(path, "w") as handle:
            handle.write(source)
        return path

    def run_adapter(self, event, script, payload, env=None, timeout=60):
        run_env = dict(os.environ)
        if env:
            run_env.update(env)
        return subprocess.run(
            [sys.executable, ADAPTER, event, "--run", script],
            input=json.dumps(payload),
            capture_output=True,
            text=True,
            timeout=timeout,
            env=run_env,
        )

    def run_adapter_direct(self, event, payload=None, raw_stdin=None,
                            env=None, timeout=60):
        """Call the adapter with no --run: exercises event_name()/handle()
        directly, the same path a hooks.json entry with no --run target
        would take, and the only path that can print "unknown event"."""
        run_env = dict(os.environ)
        if env:
            run_env.update(env)
        stdin_text = raw_stdin if raw_stdin is not None else json.dumps(
            payload if payload is not None else {})
        return subprocess.run(
            [sys.executable, ADAPTER, event],
            input=stdin_text,
            capture_output=True,
            text=True,
            timeout=timeout,
            env=run_env,
        )

    def test_a_before_shell_execution_repro(self):
        stub = self.write_stub("repro_stub.py", REPRO_STUB)
        payload = {
            "command": "rm -rf build",
            "cwd": "/tmp",
            "conversation_id": "c1",
            "hook_event_name": "beforeShellExecution",
        }

        # Direct call documents the defect: no tool_name means no deny.
        direct = subprocess.run(
            [sys.executable, stub],
            input=json.dumps(payload),
            capture_output=True,
            text=True,
            timeout=60,
        )
        self.assertEqual(direct.returncode, 0)

        # Through the adapter the payload is translated and the write is blocked.
        proc = self.run_adapter("beforeShellExecution", stub, payload)
        self.assertEqual(proc.returncode, 2)
        decision = json.loads(proc.stdout)
        self.assertEqual(decision.get("permission"), "deny")
        self.assertEqual(decision.get("user_message"), "no rm here")

    def test_b_pretooluse_shell_maps_to_bash(self):
        stub = self.write_stub("repro_stub.py", REPRO_STUB)
        payload = {"tool_name": "Shell", "tool_input": {"command": "rm -rf x"}}

        proc = self.run_adapter("preToolUse", stub, payload)
        self.assertEqual(proc.returncode, 2)
        decision = json.loads(proc.stdout)
        self.assertEqual(decision.get("permission"), "deny")
        self.assertEqual(decision.get("user_message"), "no rm here")

    def test_c_nested_claude_deny_becomes_flat_cursor_deny(self):
        stub = self.write_stub("nested_deny_stub.py", NESTED_DENY_STUB)
        payload = {
            "command": "ls",
            "cwd": "/tmp",
            "hook_event_name": "beforeShellExecution",
        }

        proc = self.run_adapter("beforeShellExecution", stub, payload)
        self.assertEqual(proc.returncode, 2)
        decision = json.loads(proc.stdout)
        self.assertEqual(decision.get("permission"), "deny")
        self.assertEqual(decision.get("user_message"), "X")

    def test_d_allow_stubs_echo_cursor_allow(self):
        stub = self.write_stub("allow_stub.py", ALLOW_STUB)

        pre = self.run_adapter(
            "preToolUse",
            stub,
            {"tool_name": "Shell", "tool_input": {"command": "ls"}},
        )
        self.assertEqual(pre.returncode, 0)
        pre_decision = json.loads(pre.stdout)
        self.assertEqual(pre_decision.get("permission"), "allow")
        self.assertTrue(pre_decision.get("continue"))

        shell = self.run_adapter(
            "beforeShellExecution",
            stub,
            {"command": "ls", "cwd": "/tmp", "hook_event_name": "beforeShellExecution"},
        )
        self.assertEqual(shell.returncode, 0)
        shell_decision = json.loads(shell.stdout)
        self.assertEqual(shell_decision.get("permission"), "allow")
        self.assertTrue(shell_decision.get("continue"))

        edit = self.run_adapter("afterFileEdit", stub, {"file_path": "a.py"})
        self.assertEqual(edit.returncode, 0)
        self.assertEqual(json.loads(edit.stdout), {})

    def test_e_missing_script_fails_open(self):
        missing = os.path.join(self.tmp, "no_such_hook.py")
        proc = self.run_adapter(
            "beforeShellExecution",
            missing,
            {"command": "ls", "cwd": "/tmp", "hook_event_name": "beforeShellExecution"},
        )
        self.assertEqual(proc.returncode, 0)

    def test_f_timeout_fails_open(self):
        stub = self.write_stub("sleep_stub.py", SLEEP_STUB)
        started = time.monotonic()
        proc = self.run_adapter(
            "beforeShellExecution",
            stub,
            {"command": "ls", "cwd": "/tmp", "hook_event_name": "beforeShellExecution"},
            env={"BM_CURSOR_HOOK_TIMEOUT": "1"},
            timeout=30,
        )
        elapsed = time.monotonic() - started
        self.assertEqual(proc.returncode, 0)
        self.assertLess(elapsed, 4)

    def test_g_shipped_hooks_json_wires_through_adapter(self):
        with open(HOOKS_JSON) as handle:
            config = json.load(handle)
        hooks = config.get("hooks", {})
        self.assertTrue(hooks)

        for event, entries in hooks.items():
            self.assertTrue(entries, event)
            for entry in entries:
                command = entry["command"]
                self.assertIn("bm_cursor_hook.py", command)
                tokens = shlex.split(command)
                index = None
                for position, token in enumerate(tokens):
                    if token.endswith("bm_cursor_hook.py"):
                        index = position
                        break
                self.assertIsNotNone(index, command)
                self.assertEqual(tokens[index + 1], event, command)
                self.assertEqual(tokens[index + 2], "--run", command)
                target = tokens[index + 3]
                self.assertTrue(target.startswith("${PLUGIN_ROOT}/"), command)
                relative = "bundle/" + target[len("${PLUGIN_ROOT}/"):]
                self.assertTrue(
                    os.path.isfile(os.path.join(REPO_ROOT, relative)),
                    relative,
                )

    def test_h_non_gate_exit_two_stays_allow(self):
        stub = self.write_stub(
            "stderr_stub.py",
            "import sys\n"
            "sys.stdin.read()\n"
            "sys.stderr.write('boom\\n')\n"
            "sys.exit(2)\n",
        )

        proc = self.run_adapter("sessionStart", stub, {})
        self.assertEqual(proc.returncode, 0)
        self.assertEqual(json.loads(proc.stdout), {})

    def test_i_non_gate_nested_deny_stays_allow(self):
        stub = self.write_stub("nested_deny_stub.py", NESTED_DENY_STUB)

        proc = self.run_adapter("afterFileEdit", stub, {"file_path": "a.py"})
        self.assertEqual(proc.returncode, 0)
        self.assertEqual(json.loads(proc.stdout), {})

    def test_j_brother_client_forced_to_cursor(self):
        marker = os.path.join(self.tmp, "marker.txt")
        stub = self.write_stub(
            "client_stub.py",
            "import os\n"
            "import sys\n"
            "sys.stdin.read()\n"
            "with open(sys.argv[1], 'w') as fh:\n"
            "    fh.write(os.environ.get('BROTHER_CLIENT', ''))\n"
            "sys.exit(0)\n",
        )

        run_env = dict(os.environ)
        run_env["BROTHER_CLIENT"] = "claude"
        proc = subprocess.run(
            [sys.executable, ADAPTER, "beforeShellExecution", "--run", stub, marker],
            input=json.dumps({"command": "ls", "cwd": "/tmp"}),
            capture_output=True,
            text=True,
            timeout=60,
            env=run_env,
        )
        self.assertEqual(proc.returncode, 0)
        with open(marker) as handle:
            self.assertEqual(handle.read(), "cursor")

    def test_k_newly_reserved_events_stay_allow_with_no_unknown_warning(self):
        for event in NEW_RESERVED_EVENTS:
            proc = self.run_adapter_direct(event)
            self.assertEqual(proc.returncode, 0, event)
            decision = json.loads(proc.stdout)
            self.assertEqual(decision.get("permission"), "allow", event)
            self.assertTrue(decision.get("continue"), event)
            self.assertNotIn("unknown event", proc.stderr, event)

    def test_l_tab_events_are_not_reserved_and_fail_open_as_unknown(self):
        for event in TAB_EVENTS:
            proc = self.run_adapter_direct(
                event, {"hook_event_name": event})
            self.assertEqual(proc.returncode, 0, event)
            decision = json.loads(proc.stdout)
            self.assertEqual(decision.get("permission"), "allow", event)
            self.assertIn("unknown event", proc.stderr, event)

    def test_m_unknown_event_fails_open_with_warning(self):
        proc = self.run_adapter_direct(
            "totallyMadeUpEvent", {"hook_event_name": "totallyMadeUpEvent"})
        self.assertEqual(proc.returncode, 0)
        decision = json.loads(proc.stdout)
        self.assertEqual(decision.get("permission"), "allow")
        self.assertIn("unknown event", proc.stderr)

    def test_n_malformed_stdin_fails_open(self):
        proc = self.run_adapter_direct(
            "preToolUse", raw_stdin="not valid json{")
        self.assertEqual(proc.returncode, 0)
        decision = json.loads(proc.stdout)
        self.assertEqual(decision.get("permission"), "allow")
        self.assertIn("not JSON", proc.stderr)

    def test_o_uncaught_exception_in_wrapped_script_fails_open(self):
        stub = self.write_stub("crash_stub.py", CRASH_STUB)
        proc = self.run_adapter(
            "beforeShellExecution",
            stub,
            {"command": "ls", "cwd": "/tmp", "hook_event_name": "beforeShellExecution"},
        )
        self.assertEqual(proc.returncode, 0)
        decision = json.loads(proc.stdout)
        self.assertEqual(decision.get("permission"), "allow")
        self.assertIn("boom, not a SystemExit", proc.stderr)

    def test_p_reserved_events_never_gate_even_on_run_mode_exit_two(self):
        stub = self.write_stub("always_deny_stub.py", ALWAYS_DENY_STUB)
        for event in NEW_RESERVED_EVENTS:
            proc = self.run_adapter(event, stub, {})
            self.assertEqual(proc.returncode, 0, event)
            self.assertEqual(json.loads(proc.stdout), {}, event)


if __name__ == "__main__":
    unittest.main()

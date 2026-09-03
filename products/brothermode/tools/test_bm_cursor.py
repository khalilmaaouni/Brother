#!/usr/bin/env python3
"""Tests for BrotherMode Cursor compatibility mode.

Covers the hook adapter, the mailbox harness, rules/hooks emission, and
the install/uninstall ownership rules. No live Cursor Agent is required;
payload shapes are the documented Cursor contract from 2026-08-10.
"""

from __future__ import annotations

import io
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
SCRIPTS = os.path.join(ROOT, "scripts")
sys.path.insert(0, HERE)
sys.path.insert(0, SCRIPTS)

import bm_cursor as bc  # noqa: E402
import bm_cursor_hook as hook  # noqa: E402


def _run(argv, cwd=None, env=None, input_text=None):
    return subprocess.run(
        [sys.executable] + list(argv),
        cwd=cwd or ROOT,
        env=env,
        input=input_text,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        universal_newlines=True,
        timeout=120,
    )


class TestCursorHookAdapter(unittest.TestCase):
    def test_shell_maps_to_bash(self):
        payload = {
            "hook_event_name": "preToolUse",
            "tool_name": "Shell",
            "tool_input": {"command": "echo hi", "working_directory": "/tmp"},
            "conversation_id": "conv-1",
        }
        claude = hook.cursor_to_claude_payload(payload, "preToolUse")
        self.assertEqual(claude["tool_name"], "Bash")
        self.assertEqual(claude["hook_event_name"], "PreToolUse")
        self.assertEqual(claude["session_id"], "conv-1")
        self.assertEqual(claude["cwd"], "/tmp")

    def test_before_shell_execution_shape(self):
        payload = {"command": "ls", "cwd": "/proj", "sandbox": False}
        claude = hook.cursor_to_claude_payload(payload, "beforeShellExecution")
        self.assertEqual(claude["tool_name"], "Bash")
        self.assertEqual(claude["tool_input"]["command"], "ls")
        self.assertEqual(claude["cwd"], "/proj")

    def test_deny_translation(self):
        decision = {
            "hookSpecificOutput": {
                "hookEventName": "PreToolUse",
                "permissionDecision": "deny",
                "permissionDecisionReason": "owned by other",
            }
        }
        flat = hook.claude_deny_to_cursor(decision)
        self.assertEqual(flat["permission"], "deny")
        self.assertIn("owned", flat["user_message"])

    def test_adapter_fail_open_on_read(self):
        payload = json.dumps({
            "hook_event_name": "preToolUse",
            "tool_name": "Read",
            "tool_input": {"file_path": "/tmp/x"},
            "cwd": "/tmp",
            "conversation_id": "t1",
        })
        proc = _run(
            [os.path.join(HERE, "bm_cursor_hook.py"), "preToolUse"],
            input_text=payload,
        )
        self.assertEqual(proc.returncode, 0, proc.stderr)
        body = json.loads(proc.stdout)
        self.assertEqual(body.get("permission"), "allow")

    def test_write_path_keys_normalized(self):
        payload = {
            "tool_name": "Write",
            "tool_input": {"path": "app.py"},
        }
        claude = hook.cursor_to_claude_payload(payload, "preToolUse")
        self.assertEqual(claude["tool_input"]["file_path"], "app.py")


class TestCursorHarness(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="bm-cursor-test-")
        self.addCleanup(lambda: shutil.rmtree(self.tmp, ignore_errors=True))
        subprocess.run(["git", "init"], cwd=self.tmp, check=True,
                       stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        # Need one commit for worktree add from HEAD.
        with io.open(os.path.join(self.tmp, "README"), "w",
                     encoding="utf-8") as fh:
            fh.write("x\n")
        subprocess.run(["git", "add", "README"], cwd=self.tmp, check=True,
                       stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        subprocess.run(
            ["git", "-c", "user.email=t@t", "-c", "user.name=t",
             "commit", "-m", "init"],
            cwd=self.tmp, check=True,
            stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        )

    def test_dispatch_claim_record_adopt(self):
        env = os.environ.copy()
        env["BROTHERMODE_ROOT"] = ROOT
        tool = os.path.join(HERE, "bm_cursor.py")
        d = _run(
            [tool, "dispatch",
             "--objective", "touch marker",
             "--write-scope", "marker.txt",
             "--done-check", "test -f marker.txt",
             "--actor", "fable",
             "--project", self.tmp,
             "--json"],
            cwd=self.tmp, env=env,
        )
        self.assertEqual(d.returncode, 0, d.stderr)
        packet = json.loads(d.stdout)
        pid = packet["packet_id"]
        self.assertEqual(packet["state"], "queued")

        c = _run(
            [tool, "claim-next", "--project", self.tmp, "--actor", "cursor",
             "--json"],
            cwd=self.tmp, env=env,
        )
        self.assertEqual(c.returncode, 0, c.stderr)
        claimed = json.loads(c.stdout)
        self.assertEqual(claimed["state"], "claimed")
        self.assertEqual(claimed["packet_id"], pid)

        # Executor does the work in the project root.
        with io.open(os.path.join(self.tmp, "marker.txt"), "w",
                     encoding="utf-8") as fh:
            fh.write("ok\n")
        done_path = os.path.join(self.tmp, "done.txt")
        with io.open(done_path, "w", encoding="utf-8") as fh:
            fh.write("done\n")

        r = _run(
            [tool, "record-result",
             "--packet-id", pid,
             "--worker-claim", "wrote marker",
             "--artifact", "marker.txt",
             "--done-output", done_path,
             "--project", self.tmp,
             "--json"],
            cwd=self.tmp, env=env,
        )
        self.assertEqual(r.returncode, 0, r.stderr)
        returned = json.loads(r.stdout)
        self.assertEqual(returned["state"], "returned")

        a = _run(
            [tool, "adopt", "--packet-id", pid, "--project", self.tmp,
             "--json"],
            cwd=self.tmp, env=env,
        )
        self.assertEqual(a.returncode, 0, a.stderr)
        adopted = json.loads(a.stdout)
        self.assertEqual(adopted["state"], "adopted")
        self.assertEqual(adopted["adoption"]["verdict"], "passed")

    def test_mailbox_worker_pending(self):
        worker = bc.CursorMailboxWorker(project=self.tmp, actor="fable")
        result = worker.run({
            "unit_id": "u1",
            "objective": "do a thing",
            "read_scope": [],
            "write_scope": ["a.py"],
            "done_check": "true",
            "risk_class": "normal",
        })
        self.assertEqual(result["status"], "pending")
        self.assertTrue(result["artifacts"])
        self.assertTrue(os.path.isfile(result["artifacts"][0]))

    def test_emit_rules_has_frontmatter(self):
        tool = os.path.join(HERE, "bm_cursor.py")
        dest_dir = os.path.join(self.tmp, ".cursor", "rules")
        os.makedirs(dest_dir)
        proc = _run(
            [tool, "emit-rules", "--project", self.tmp,
             "--checkout", ROOT, "--force"],
            cwd=self.tmp,
        )
        self.assertEqual(proc.returncode, 0, proc.stderr)
        with io.open(os.path.join(dest_dir, "brothermode.mdc"),
                     encoding="utf-8") as fh:
            text = fh.read()
        self.assertTrue(text.startswith("---\n"))
        self.assertIn("alwaysApply: true", text)
        self.assertIn("BrotherMode", text)


class TestCursorInstall(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="bm-cursor-install-")
        self.addCleanup(lambda: shutil.rmtree(self.tmp, ignore_errors=True))
        self.home = os.path.join(self.tmp, "home")
        os.makedirs(self.home)
        self.cursor = os.path.join(self.home, ".cursor")
        os.makedirs(self.cursor)
        self.target = os.path.join(self.cursor, "brothermode")
        self.hooks = os.path.join(self.cursor, "hooks.json")

    def _env(self):
        env = os.environ.copy()
        env["HOME"] = self.home
        return env

    def test_install_and_uninstall(self):
        # Point defaults by explicit flags rather than relying on HOME for
        # expanduser inside already-imported modules: pass --target/--hooks.
        inst = _run(
            [os.path.join(SCRIPTS, "install_cursor.py"),
             "--target", self.target,
             "--hooks", self.hooks],
            env=self._env(),
        )
        self.assertEqual(inst.returncode, 0, inst.stderr + inst.stdout)
        self.assertTrue(os.path.isfile(
            os.path.join(self.target, "tools", "bm_cursor_hook.py")))
        self.assertTrue(os.path.isfile(self.hooks))
        with io.open(self.hooks, encoding="utf-8") as fh:
            doc = json.loads(fh.read())
        self.assertEqual(doc.get("version"), 1)
        self.assertIn("preToolUse", doc["hooks"])
        self.assertIn("beforeShellExecution", doc["hooks"])
        joined = json.dumps(doc)
        self.assertIn("bm_cursor_hook.py", joined)
        self.assertIn(self.target, joined)
        record = os.path.join(self.cursor, "brothermode-install.json")
        self.assertTrue(os.path.isfile(record))

        # Foreign hook must survive uninstall.
        doc["hooks"]["stop"] = doc["hooks"].get("stop", []) + [{
            "command": "echo foreign-hook",
            "timeout": 5,
        }]
        with io.open(self.hooks, "w", encoding="utf-8") as fh:
            fh.write(json.dumps(doc, indent=2) + "\n")

        un = _run(
            [os.path.join(SCRIPTS, "uninstall_cursor.py"),
             "--target", self.target,
             "--hooks", self.hooks,
             "--remove-files"],
            env=self._env(),
        )
        self.assertEqual(un.returncode, 0, un.stderr + un.stdout)
        self.assertFalse(os.path.isdir(self.target))
        with io.open(self.hooks, encoding="utf-8") as fh:
            left = json.loads(fh.read())
        stop = left.get("hooks", {}).get("stop", [])
        self.assertTrue(any("foreign-hook" in (e.get("command") or "")
                            for e in stop if isinstance(e, dict)))
        self.assertFalse(any("bm_cursor_hook.py" in (e.get("command") or "")
                             for entries in left.get("hooks", {}).values()
                             for e in (entries or [])
                             if isinstance(e, dict)))

    def test_refuse_overwrite_without_upgrade(self):
        first = _run(
            [os.path.join(SCRIPTS, "install_cursor.py"),
             "--target", self.target,
             "--hooks", self.hooks],
            env=self._env(),
        )
        self.assertEqual(first.returncode, 0, first.stderr)
        second = _run(
            [os.path.join(SCRIPTS, "install_cursor.py"),
             "--target", self.target,
             "--hooks", self.hooks],
            env=self._env(),
        )
        self.assertEqual(second.returncode, 4, second.stderr)


class TestCursorDocsAndSkills(unittest.TestCase):
    def test_docs_page_exists(self):
        path = os.path.join(ROOT, "docs", "CURSOR-COMPAT.md")
        self.assertTrue(os.path.isfile(path))
        with io.open(path, encoding="utf-8") as fh:
            text = fh.read()
        self.assertIn("ADVISORY", text)
        self.assertIn("install_cursor.py", text)
        self.assertNotIn("\u2014", text)  # no em dash
        self.assertNotIn("\u2013", text)  # no en dash

    def test_skills_exist(self):
        for name in ("cursor-dispatch", "cursor-execute"):
            path = os.path.join(ROOT, "skills", name, "SKILL.md")
            self.assertTrue(os.path.isfile(path), path)
            with io.open(path, encoding="utf-8") as fh:
                text = fh.read()
            self.assertTrue(text.startswith("---\n"))
            self.assertIn("bm_cursor.py", text)


if __name__ == "__main__":
    unittest.main()

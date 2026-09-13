#!/usr/bin/env python3
"""Regression test for the SHIPPED Cursor deny chain, end to end.

WHAT THE SIBLING TESTS ALREADY PROVE, and why this one is still needed:
scripts/test_cursor_hook_run.py proves the adapter translates a Cursor payload
and turns a stub's deny into Cursor's flat permission object, and that every
command in bundle/cursor-hooks/hooks.json names a file that exists. Neither
runs the REAL fence behind the real hooks.json command. A stub that denies
proves the adapter; it does not prove that the shipped bm_fence_hook.py,
reached exactly the way Cursor reaches it, refuses a write to a file another
session owns.

This test takes the preToolUse command out of bundle/cursor-hooks/hooks.json,
resolves ${PLUGIN_ROOT} to bundle/, and runs it as a subprocess with a
Cursor-shaped payload against a throwaway project whose store holds a claim
by ANOTHER session. It asserts the whole chain: exit 2, a flat Cursor deny,
a reason that names the record and the takeover command, and the fenced file
untouched. The owner's own write through the same chain must stay allowed.

What this does NOT establish: that a live signed-in Cursor Agent honors the
refusal. That remains NO-DATA until the signed-in canary runs
(docs/cursor/SMOKE-RUNBOOK.md); the handover records it as ADVISORY.

Standard library only. Run: python3 scripts/test_cursor_deny_chain.py -v
"""
import importlib.util
import io
import json
import os
import shlex
import subprocess
import sys
import tempfile
import unittest

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
BUNDLE = os.path.join(REPO_ROOT, "bundle")
HOOKS_JSON = os.path.join(BUNDLE, "cursor-hooks", "hooks.json")
SHIPPED_TOOLS = os.path.join(BUNDLE, "runtime", "hooks", "brothermode", "tools")
SHIPPED_STORE = os.path.join(SHIPPED_TOOLS, "bm_store.py")
SHIPPED_FENCE = os.path.join(SHIPPED_TOOLS, "bm_fence_hook.py")

VICTIM = "cursor-conv-owner-0001"
OTHER = "cursor-conv-other-0002"
ORIGINAL = "# app.py, owned by the victim session\n"


def _load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    sys.modules[name] = module
    return module


def shipped_fence_commands():
    """Every preToolUse command in the shipped hooks.json whose --run target
    is bm_fence_hook.py, with ${PLUGIN_ROOT} resolved to bundle/."""
    with open(HOOKS_JSON) as handle:
        config = json.load(handle)
    found = []
    for entry in config["hooks"].get("preToolUse", []):
        command = entry["command"].replace("${PLUGIN_ROOT}", BUNDLE)
        tokens = shlex.split(command)
        if any(t.endswith("bm_fence_hook.py") for t in tokens):
            found.append((tokens, entry.get("matcher", "")))
    return found


class CursorDenyChainTest(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.root = os.path.realpath(self._tmp.name)
        os.makedirs(os.path.join(self.root, ".git"))
        os.makedirs(os.path.join(self.root, "src"))
        self.target = os.path.join(self.root, "src", "app.py")
        with io.open(self.target, "w", encoding="utf-8") as handle:
            handle.write(ORIGINAL)
        # The SHIPPED store and fence, never the products/ copies: hooks fire
        # from the installed plugin, so that is the code under test.
        self.bs = _load("bm_store_shipped", SHIPPED_STORE)
        self.fh = _load("bm_fence_hook_shipped", SHIPPED_FENCE)
        with self.bs.Store(self.root) as store:
            pass
        owner_label = self.fh.session_label(self.root, VICTIM)
        with self.bs.Store(self.root, create=False) as store:
            self.record = store.claim(
                "api", "ephemeral", objective="deny chain fixture",
                files=["src/app.py"], session_id=owner_label)
        self.bs.write_state_view(self.root)
        self.owner_label = owner_label

    def env(self):
        run_env = dict(os.environ)
        for key in ("BROTHERMODE_ROOT", "BM_FENCE_STRICT",
                    "BM_FENCE_SESSION_ID", "BROTHER_CLIENT"):
            run_env.pop(key, None)
        run_env["BM_FENCE_MODE"] = "enforced"
        return run_env

    def cursor_payload(self, conversation_id, tool_name="Write"):
        return {
            "hook_event_name": "preToolUse",
            "conversation_id": conversation_id,
            "generation_id": "gen-1",
            "workspace_roots": [self.root],
            "cwd": self.root,
            "tool_name": tool_name,
            "tool_input": {"path": "src/app.py", "content": "overwritten\n"},
        }

    def run_chain(self, tokens, payload):
        return subprocess.run(
            tokens,
            input=json.dumps(payload),
            capture_output=True,
            text=True,
            timeout=60,
            cwd=self.root,
            env=self.env(),
        )

    def test_a_shipped_hooks_json_names_the_fence_on_write_events(self):
        commands = shipped_fence_commands()
        self.assertTrue(commands, "no preToolUse fence command in hooks.json")
        for tokens, matcher in commands:
            self.assertEqual(tokens[0], "python3", tokens)
            self.assertTrue(tokens[1].endswith("bm_cursor_hook.py"), tokens)
            self.assertTrue(os.path.isfile(tokens[1]), tokens[1])
            self.assertEqual(tokens[2], "preToolUse", tokens)
            self.assertEqual(tokens[3], "--run", tokens)
            self.assertTrue(os.path.isfile(tokens[4]), tokens[4])
            for tool in ("Write", "Edit"):
                self.assertIn(tool, matcher, matcher)

    def test_b_foreign_write_is_denied_through_the_shipped_chain(self):
        for tokens, _matcher in shipped_fence_commands():
            proc = self.run_chain(tokens, self.cursor_payload(OTHER))
            self.assertEqual(proc.returncode, 2, proc.stderr)
            decision = json.loads(proc.stdout)
            self.assertEqual(decision.get("permission"), "deny", decision)
            reason = decision.get("user_message", "")
            self.assertIn("api", reason)
            self.assertIn(self.record.lifecycle_uuid, reason)
            self.assertIn(self.owner_label, reason)
            self.assertIn("adopt", reason)
            with io.open(self.target, encoding="utf-8") as handle:
                self.assertEqual(handle.read(), ORIGINAL)

    def test_c_owner_write_is_allowed_through_the_same_chain(self):
        for tokens, _matcher in shipped_fence_commands():
            proc = self.run_chain(tokens, self.cursor_payload(VICTIM))
            self.assertEqual(proc.returncode, 0, proc.stderr)
            decision = json.loads(proc.stdout)
            self.assertEqual(decision.get("permission"), "allow", decision)

    def test_d_foreign_edit_tool_name_is_denied_too(self):
        for tokens, _matcher in shipped_fence_commands():
            proc = self.run_chain(tokens, self.cursor_payload(OTHER, "Edit"))
            self.assertEqual(proc.returncode, 2, proc.stderr)
            self.assertEqual(json.loads(proc.stdout).get("permission"), "deny")

    def test_e_unclaimed_project_is_the_backward_drive(self):
        """Driven backward: the same foreign write against a project whose
        store holds NO claim on src/app.py must come back allow, exit 0.
        This proves test_b's deny came from the real fence reading a real
        claim, not from the adapter or the mode refusing everything.
        (BM_FENCE_MODE does not decide this: a real ownership conflict is
        denied in advisory and enforced mode alike; the mode only decides
        what happens when the fence CANNOT work.)"""
        with self.bs.Store(self.root, create=False) as store:
            store.claim("api", "ephemeral", objective="deny chain fixture",
                        files=["src/other.py"], session_id=self.owner_label)
        self.bs.write_state_view(self.root)
        for tokens, _matcher in shipped_fence_commands():
            proc = self.run_chain(tokens, self.cursor_payload(OTHER))
            self.assertEqual(proc.returncode, 0, proc.stderr)
            self.assertEqual(json.loads(proc.stdout).get("permission"), "allow")

    def test_f_shipped_copies_match_the_product_sources(self):
        """The chain above runs bundle/; a drifted copy would test the
        wrong code. Byte equality with products/brothermode/tools."""
        product_tools = os.path.join(REPO_ROOT, "products", "brothermode", "tools")
        for name in ("bm_cursor_hook.py", "bm_fence_hook.py", "bm_store.py"):
            with open(os.path.join(SHIPPED_TOOLS, name), "rb") as shipped:
                with open(os.path.join(product_tools, name), "rb") as source:
                    self.assertEqual(shipped.read(), source.read(), name)


if __name__ == "__main__":
    unittest.main()

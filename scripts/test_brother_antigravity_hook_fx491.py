#!/usr/bin/env python3
"""FX-49.1 tests: host data path guard in brother_antigravity_hook.py.

Verifies R-FX-49-1 (command under host root denied), R-FX-49-2 (read
argument under host root denied) and R-FX-49-3 (ordinary workspace path
preserved) at the handle_pre_tool entry point, plus hostile-input refusal.

Owner ruling 2026-09-30 ("I allow full access to antigravity"): the guard is
off by default and on only under BROTHER_ANTIGRAVITY_GUARD_HOST_DATA=1. The
deny cases below therefore run under that opt in, and
TestHostDataGuardDefault proves the new default allows.
"""

import contextlib
import importlib.util
import io
import os
import tempfile
import unittest
from unittest import mock

HERE = os.path.dirname(os.path.abspath(__file__))
HOOK_SCRIPT = os.path.join(HERE, "brother_antigravity_hook.py")


def _load_hook():
    spec = importlib.util.spec_from_file_location(
        "brother_antigravity_hook_fx491", HOOK_SCRIPT
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


GUARD_ENV = "BROTHER_ANTIGRAVITY_GUARD_HOST_DATA"


class TestHostDataPathGuard(unittest.TestCase):
    """The FX-49 deny cases, run under the opt in."""

    def setUp(self):
        env = mock.patch.dict(os.environ, {GUARD_ENV: "1"})
        env.start()
        self.addCleanup(env.stop)
        self.module = _load_hook()
        tmp_handle = tempfile.TemporaryDirectory()
        self.addCleanup(tmp_handle.cleanup)
        self.tmp = tmp_handle.name
        self.host_root = os.path.join(self.tmp, "antigravity-ide")
        os.makedirs(self.host_root, exist_ok=True)
        patcher = mock.patch.object(
            self.module, "_host_data_root", return_value=self.host_root
        )
        patcher.start()
        self.addCleanup(patcher.stop)

    def _run(self, payload):
        buf = io.StringIO()
        err = io.StringIO()
        with contextlib.redirect_stdout(buf), contextlib.redirect_stderr(err):
            decision = self.module.handle_pre_tool(payload)
        return decision, buf.getvalue(), err.getvalue()

    def _host_path(self, *parts):
        return os.path.join(self.host_root, *parts)

    # R-FX-49-1
    def test_command_under_host_root_is_denied(self):
        inside = self._host_path("builtin", "skills", "x.md")
        payload = {
            "toolCall": {
                "name": "run_command",
                "args": {"CommandLine": "cat " + inside},
            }
        }
        decision, _out, _err = self._run(payload)
        self.assertEqual(decision.get("decision"), "deny")

    # R-FX-49-2
    def test_read_argument_under_host_root_is_denied(self):
        inside = self._host_path(
            "builtin", "skills", "agy-customizations", "docs", "hooks.md"
        )
        payload = {"toolCall": {"name": "view_file", "args": {"TargetFile": inside}}}
        decision, _out, _err = self._run(payload)
        self.assertEqual(decision.get("decision"), "deny")

    def test_read_argument_equal_to_host_root_is_denied(self):
        payload = {"toolCall": {"name": "list_dir", "args": {"Path": self.host_root}}}
        decision, _out, _err = self._run(payload)
        self.assertEqual(decision.get("decision"), "deny")

    def test_mixed_read_args_with_one_host_path_denied(self):
        payload = {
            "toolCall": {
                "name": "grep_search",
                "args": {"query": "needle", "Path": self._host_path("x")},
            }
        }
        decision, _out, _err = self._run(payload)
        self.assertEqual(decision.get("decision"), "deny")

    def test_dotdot_segment_resolved_before_containment(self):
        outside = os.path.join(self.tmp, "elsewhere")
        os.makedirs(outside, exist_ok=True)
        sneaky = os.path.join(outside, "..", "antigravity-ide", "x.md")
        payload = {"toolCall": {"name": "view_file", "args": {"TargetFile": sneaky}}}
        decision, _out, _err = self._run(payload)
        self.assertEqual(decision.get("decision"), "deny")

    # R-FX-49-3
    def test_ordinary_workspace_read_is_allowed(self):
        workspace = os.path.join(self.tmp, "workspace", "module.py")
        payload = {"toolCall": {"name": "view_file", "args": {"TargetFile": workspace}}}
        decision, _out, _err = self._run(payload)
        self.assertEqual(decision.get("decision"), "allow")

    def test_ordinary_workspace_command_is_allowed(self):
        payload = {"toolCall": {"name": "run_command", "args": {"CommandLine": "ls -la"}}}
        decision, _out, _err = self._run(payload)
        self.assertEqual(decision.get("decision"), "allow")

    def test_run_command_without_commandline_is_denied(self):
        # OP1.c (e): a missing CommandLine is corrupt input and blocks
        # whatever the guard says (it used to preserve allow).
        payload = {"toolCall": {"name": "run_command", "args": {}}}
        decision, _out, _err = self._run(payload)
        self.assertEqual(decision.get("decision"), "deny")

    def test_sibling_textual_prefix_is_allowed(self):
        sibling = self.host_root + "-copy"
        os.makedirs(sibling, exist_ok=True)
        payload = {
            "toolCall": {
                "name": "view_file",
                "args": {"TargetFile": os.path.join(sibling, "notes.md")},
            }
        }
        decision, _out, _err = self._run(payload)
        self.assertEqual(decision.get("decision"), "allow")

    def test_empty_path_value_is_ignored(self):
        payload = {"toolCall": {"name": "view_file", "args": {"TargetFile": ""}}}
        decision, _out, _err = self._run(payload)
        self.assertEqual(decision.get("decision"), "allow")

    def test_malformed_nonempty_command_is_denied(self):
        payload = {"toolCall": {"name": "run_command", "args": {"CommandLine": "cat 'open"}}}
        decision, _out, _err = self._run(payload)
        self.assertEqual(decision.get("decision"), "deny")

    def test_non_string_command_value_is_denied(self):
        payload = {"toolCall": {"name": "run_command", "args": {"CommandLine": 12}}}
        decision, _out, _err = self._run(payload)
        self.assertEqual(decision.get("decision"), "deny")

    def test_unresolvable_host_root_denies(self):
        with mock.patch.object(self.module, "_host_data_root", return_value=None):
            payload = {
                "toolCall": {
                    "name": "view_file",
                    "args": {"TargetFile": "/tmp/elsewhere.py"},
                }
            }
            decision, _out, _err = self._run(payload)
        self.assertEqual(decision.get("decision"), "deny")

    # Hostile input
    def test_hostile_payloads_are_refused_not_crashed(self):
        for value in (None, 0, 1.5, float("nan"), True, "text", [], (), b"bytes"):
            with self.subTest(value=repr(value)):
                decision, _out, _err = self._run(value)
                self.assertEqual(decision.get("decision"), "deny")

    def test_hostile_toolcall_is_refused_not_crashed(self):
        for value in (None, "text", 12, [], {}, 1.5, float("nan"), True):
            with self.subTest(value=repr(value)):
                decision, _out, _err = self._run({"toolCall": value})
                self.assertEqual(decision.get("decision"), "deny")

    def test_hostile_tool_name_is_refused_not_crashed(self):
        for value in (None, 12, 1.5, float("nan"), True, b"bytes", [], {}):
            with self.subTest(value=repr(value)):
                payload = {"toolCall": {"name": value, "args": {}}}
                decision, _out, _err = self._run(payload)
                self.assertEqual(decision.get("decision"), "deny")

    def test_hostile_argument_values_never_crash_the_guard(self):
        for value in (None, 0, 1.5, float("nan"), True, b"bytes", [], {}):
            with self.subTest(value=repr(value)):
                payload = {
                    "toolCall": {"name": "view_file", "args": {"TargetFile": value}}
                }
                decision, _out, _err = self._run(payload)
                # a non-string argument is not a path candidate: the
                # existing decision is preserved and nothing raises
                self.assertEqual(decision.get("decision"), "allow")

    def test_hostile_args_container_never_crashes_the_guard(self):
        for value in (None, [], "text", 12, 1.5, float("nan"), True, b"bytes"):
            with self.subTest(value=repr(value)):
                payload = {"toolCall": {"name": "view_file", "args": value}}
                decision, _out, _err = self._run(payload)
                # OP1.c (e): an args that is not an object is corrupt input
                # and blocks (it used to preserve allow); nothing raises.
                self.assertEqual(decision.get("decision"), "deny")


class TestHostDataGuardDefault(unittest.TestCase):
    """No opt in: the owner's full access ruling of 2026-09-30 applies.
    Each case differs from its opt in counterpart above only in the missing
    variable, so only the default decides the answer."""

    def setUp(self):
        env = mock.patch.dict(os.environ)
        env.start()
        self.addCleanup(env.stop)
        os.environ.pop(GUARD_ENV, None)
        self.module = _load_hook()
        tmp_handle = tempfile.TemporaryDirectory()
        self.addCleanup(tmp_handle.cleanup)
        self.host_root = os.path.join(tmp_handle.name, "antigravity-ide")
        os.makedirs(self.host_root, exist_ok=True)
        patcher = mock.patch.object(
            self.module, "_host_data_root", return_value=self.host_root
        )
        patcher.start()
        self.addCleanup(patcher.stop)

    _run = TestHostDataPathGuard._run
    _host_path = TestHostDataPathGuard._host_path

    def test_default_read_under_host_root_is_allowed(self):
        inside = self._host_path("builtin", "skills", "x.md")
        payload = {"toolCall": {"name": "view_file", "args": {"TargetFile": inside}}}
        decision, _out, _err = self._run(payload)
        self.assertEqual(decision.get("decision"), "allow")

    def test_default_write_under_host_root_is_allowed(self):
        inside = self._host_path("settings", "notes.md")
        payload = {"toolCall": {"name": "write_to_file", "args": {"TargetFile": inside}}}
        decision, _out, _err = self._run(payload)
        self.assertEqual(decision.get("decision"), "allow")

    def test_default_non_string_command_still_denied(self):
        # corrupt input, not a host data question: blocks whatever the setting
        payload = {"toolCall": {"name": "run_command", "args": {"CommandLine": 12}}}
        decision, _out, _err = self._run(payload)
        self.assertEqual(decision.get("decision"), "deny")


class TestHostDataGuardUnknownValue(unittest.TestCase):
    """An unknown opt in value keeps the guard on (unknown input blocks)."""

    def test_unknown_value_denies_read_under_host_root(self):
        module = _load_hook()
        with tempfile.TemporaryDirectory() as tmp, \
                mock.patch.dict(os.environ, {GUARD_ENV: "yes"}), \
                mock.patch.object(module, "_host_data_root", return_value=tmp):
            payload = {"toolCall": {"name": "view_file",
                                    "args": {"TargetFile": os.path.join(tmp, "x.md")}}}
            err = io.StringIO()
            with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(err):
                decision = module.handle_pre_tool(payload)
        self.assertEqual(decision.get("decision"), "deny")
        self.assertIn("unknown value", err.getvalue())

    def test_zero_value_is_off(self):
        module = _load_hook()
        with tempfile.TemporaryDirectory() as tmp, \
                mock.patch.dict(os.environ, {GUARD_ENV: "0"}), \
                mock.patch.object(module, "_host_data_root", return_value=tmp):
            payload = {"toolCall": {"name": "view_file",
                                    "args": {"TargetFile": os.path.join(tmp, "x.md")}}}
            with contextlib.redirect_stdout(io.StringIO()):
                decision = module.handle_pre_tool(payload)
        self.assertEqual(decision.get("decision"), "allow")


if __name__ == "__main__":
    unittest.main()

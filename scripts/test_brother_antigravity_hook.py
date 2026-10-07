#!/usr/bin/env python3
"""Unit tests for brother_antigravity_hook.py:
Validates protojson handling and verifies the founder's edge-case law
(an unknown, corrupt, or unreadable input BLOCKS; it never reads as safe).
"""

import json
import os
from pathlib import Path
import shlex
import shutil
import subprocess
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
HOOK_SCRIPT = os.path.join(HERE, "brother_antigravity_hook.py")
PLUGIN_DIR = Path(HERE).parent / "bundle" / ".antigravity-plugin"
GUARD_ENV = "BROTHER_ANTIGRAVITY_GUARD_HOST_DATA"
SCRATCH_ROOT = os.path.join(os.path.expanduser("~"), ".claude", "brother-scratch")


class TestAntigravityPackage(unittest.TestCase):
    def test_shipped_adapter_matches_source(self):
        self.assertEqual(
            (PLUGIN_DIR / "scripts/brother_antigravity_hook.py").read_bytes(),
            Path(HOOK_SCRIPT).read_bytes(),
            "Refresh the shipped adapter from scripts/brother_antigravity_hook.py",
        )

    def test_standalone_plugin_runs_all_hook_commands(self):
        payloads = {
            "PreToolUse": ({"toolCall": {"name": "run_command", "args": {"CommandLine": "true"}}},
                           {"decision": "allow"}),
            "PostToolUse": ({"stepIdx": 1, "error": ""}, {}),
            "PreInvocation": ({"invocationNum": 1}, {"injectSteps": []}),
            "PostInvocation": ({"invocationNum": 1},
                               {"injectSteps": [], "terminationBehavior": ""}),
            "Stop": ({"terminationReason": "model_stop", "fullyIdle": True},
                     {"decision": "allow"}),
        }
        with tempfile.TemporaryDirectory() as tmp:
            plugin = Path(tmp) / "plugin"
            shutil.copytree(PLUGIN_DIR, plugin)
            events = json.loads((plugin / "hooks.json").read_text())["brother-assurance"]
            self.assertEqual(set(events), set(payloads))
            for event, groups in events.items():
                for group in groups:
                    for hook in group.get("hooks", [group]):
                        with self.subTest(event=event):
                            payload, expected = payloads[event]
                            proc = subprocess.run(
                                shlex.split(hook["command"]), cwd=plugin,
                                input=json.dumps(payload), text=True,
                                capture_output=True, timeout=hook["timeout"],
                            )
                            self.assertEqual(proc.returncode, 0, proc.stderr)
                            self.assertEqual(proc.stderr, "")
                            self.assertEqual(json.loads(proc.stdout), expected)

    def test_rule_has_always_on_frontmatter(self):
        rule = (PLUGIN_DIR / "rules/brother.md").read_text()
        self.assertTrue(rule.startswith("---\ntrigger: always_on\n---\n"))

    def test_install_doc_states_missing_hook_limit(self):
        doc = (Path(HERE).parent / "docs/how-to/install-antigravity.md").read_text()
        self.assertIn("A missing or crashing hook does not guard", doc)


class TestBrotherAntigravityHook(unittest.TestCase):
    # Every hook run gets a scratch HOME, so the host data root the hook
    # computes is a scratch path and never the real ~/.gemini.
    @classmethod
    def setUpClass(cls):
        os.makedirs(SCRATCH_ROOT, exist_ok=True)
        cls._home = tempfile.mkdtemp(prefix="antigravity-hook-home-", dir=SCRATCH_ROOT)

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls._home, ignore_errors=True)

    def _run_hook(self, mode, stdin_str, guard=None):
        env = dict(os.environ, HOME=self._home)
        env.pop(GUARD_ENV, None)
        if guard is not None:
            env[GUARD_ENV] = guard
        proc = subprocess.run(
            [sys.executable, HOOK_SCRIPT, mode],
            input=stdin_str,
            text=True,
            capture_output=True,
            timeout=10,
            env=env,
        )
        self.assertEqual(proc.returncode, 0, f"Hook failed with stderr:\n{proc.stderr}")
        try:
            out_json = json.loads(proc.stdout.strip())
        except Exception as e:
            self.fail(f"Stdout was not valid JSON ({e}):\n{proc.stdout}")
        return out_json, proc.stderr

    def _fx492_host_root(self):
        import importlib.util
        spec = importlib.util.spec_from_file_location(
            "brother_antigravity_hook_root_probe_fx492", HOOK_SCRIPT
        )
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        from unittest import mock
        with mock.patch.dict(os.environ, {"HOME": self._home}):
            return module._host_data_root()

    def test_pre_tool_valid_command(self):
        payload = json.dumps({
            "toolCall": {"name": "run_command", "args": {"CommandLine": "ls -la"}},
            "stepIdx": 1,
        })
        out, _ = self._run_hook("pre_tool", payload)
        self.assertEqual(out.get("decision"), "allow")

    def test_pre_tool_valid_file_edit(self):
        payload = json.dumps({
            "toolCall": {"name": "write_to_file", "args": {"TargetFile": "/tmp/foo.py"}},
            "stepIdx": 2,
        })
        out, _ = self._run_hook("pre_tool", payload)
        self.assertEqual(out.get("decision"), "allow")

    def test_pre_tool_corrupt_json_blocks(self):
        """Founder law: corrupt input BLOCKS (decision: deny)."""
        corrupt = "{ broken json !!!"
        out, stderr = self._run_hook("pre_tool", corrupt)
        self.assertEqual(out.get("decision"), "deny")
        self.assertIn("corrupt", out.get("reason", "").lower())

    def test_pre_tool_empty_stdin_blocks(self):
        """Founder law: empty input BLOCKS (decision: deny)."""
        out, stderr = self._run_hook("pre_tool", "")
        self.assertEqual(out.get("decision"), "deny")
        self.assertIn("empty", out.get("reason", "").lower())

    def test_pre_tool_unknown_tool_blocks(self):
        """Founder law: unknown tool name BLOCKS (decision: deny)."""
        payload = json.dumps({
            "toolCall": {"name": "dangerous_unknown_tool", "args": {}},
        })
        out, _ = self._run_hook("pre_tool", payload)
        self.assertEqual(out.get("decision"), "deny")
        self.assertIn("unrecognised tool", out.get("reason", "").lower())

    def test_post_tool_clean(self):
        payload = json.dumps({"stepIdx": 1, "error": ""})
        out, _ = self._run_hook("post_tool", payload)
        self.assertEqual(out, {})

    def test_pre_invocation_clean(self):
        payload = json.dumps({"invocationNum": 1})
        out, _ = self._run_hook("pre_invocation", payload)
        self.assertIn("injectSteps", out)

    def test_post_invocation_clean(self):
        payload = json.dumps({"invocationNum": 1})
        out, _ = self._run_hook("post_invocation", payload)
        self.assertEqual(out.get("injectSteps"), [])
        self.assertEqual(out.get("terminationBehavior"), "")

    def test_post_invocation_corrupt_blocks(self):
        """Founder law: corrupt input BLOCKS. For PostInvocation, the real
        blocking verb is terminationBehavior=terminate, not the default ""
        (a real bug found and fixed in this session: the first version of
        this handler returned "" on corrupt input, which is default/pass,
        the opposite of blocking)."""
        out, _ = self._run_hook("post_invocation", "{ broken json !!!")
        self.assertEqual(out.get("terminationBehavior"), "terminate")

    def test_stop_clean(self):
        payload = json.dumps({"terminationReason": "model_stop", "fullyIdle": True})
        out, _ = self._run_hook("stop", payload)
        self.assertEqual(out.get("decision"), "allow")

    def test_stop_corrupt_blocks_exit(self):
        """Founder law: corrupt termination payload refuses stop (decision: continue)."""
        out, _ = self._run_hook("stop", "{ bad json")
        self.assertEqual(out.get("decision"), "continue")
        self.assertIn("corrupt", out.get("reason", "").lower())


    # kills: pre-tool-invalid-toolcall-fail-open
    def test_pre_tool_missing_toolcall_blocks(self):
        payload = json.dumps({"stepIdx": 1})
        out, _ = self._run_hook("pre_tool", payload)
        self.assertEqual(out.get("decision"), "deny")
        self.assertIn("missing or invalid 'toolCall'", out.get("reason", ""))

    # kills: pre-tool-missing-toolname-fail-open
    def test_pre_tool_missing_tool_name_blocks(self):
        payload = json.dumps({"toolCall": {"args": {}}})
        out, _ = self._run_hook("pre_tool", payload)
        self.assertEqual(out.get("decision"), "deny")
        self.assertIn("missing or non-string tool name", out.get("reason", ""))

    # kills: unknown-mode-fail-open
    def test_unknown_mode_blocks(self):
        out, _ = self._run_hook("not_a_real_mode", "{}")
        self.assertEqual(out.get("decision"), "deny")
        self.assertIn("unknown hook mode", out.get("reason", ""))

    # kills: unhandled-exception-fail-open
    def test_unhandled_exception_in_default_path_blocks(self):
        import importlib.util
        import io
        import sys
        from unittest import mock

        spec = importlib.util.spec_from_file_location("brother_antigravity_hook_test", HOOK_SCRIPT)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)

        stdout = io.StringIO()
        stderr = io.StringIO()
        with mock.patch.object(sys, 'argv', ['hook', 'pre_tool']), \
             mock.patch.object(sys, 'stdin', io.StringIO('{}')), \
             mock.patch.object(sys, 'stdout', stdout), \
             mock.patch.object(sys, 'stderr', stderr), \
             mock.patch.object(module, 'handle_pre_tool', side_effect=RuntimeError('boom')):
            module.main()

        output = stdout.getvalue().strip()
        self.assertTrue(output, "Expected JSON output")
        result = json.loads(output)
        self.assertEqual(result.get("decision"), "deny")
        self.assertIn("unhandled exception", result.get("reason", "").lower())

    # kills: post-invocation-exception-terminate-to-pass
    def test_post_invocation_unhandled_exception_terminates(self):
        import importlib.util
        import io
        import sys
        from unittest import mock

        spec = importlib.util.spec_from_file_location("brother_antigravity_hook_test2", HOOK_SCRIPT)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)

        stdout = io.StringIO()
        stderr = io.StringIO()
        with mock.patch.object(sys, 'argv', ['hook', 'post_invocation']), \
             mock.patch.object(sys, 'stdin', io.StringIO('{}')), \
             mock.patch.object(sys, 'stdout', stdout), \
             mock.patch.object(sys, 'stderr', stderr), \
             mock.patch.object(module, 'handle_post_invocation', side_effect=RuntimeError('boom')):
            module.main()

        output = stdout.getvalue().strip()
        self.assertTrue(output, "Expected JSON output")
        result = json.loads(output)
        self.assertEqual(result.get("terminationBehavior"), "terminate")
        self.assertEqual(result.get("injectSteps"), [])

    def test_valid_json_of_the_wrong_shape_blocks_stop_and_post_invocation(self):
        """Proven 2026-09-20 by execution: {"garbage": 1} was allowed to stop."""
        for bad in ('{"garbage": 1}', '{"terminationReason": 123, "fullyIdle": true}',
                    '{"terminationReason": "model_stop", "fullyIdle": "yes"}',
                    '{"terminationReason": "  ", "fullyIdle": true}', '[]', 'null', '7'):
            out, _ = self._run_hook("stop", bad)
            self.assertEqual(out.get("decision"), "continue", msg=bad)
        for bad in ('{"garbage": 1}', '{"invocationNum": "3"}', '{"invocationNum": true}', '{}'):
            out, _ = self._run_hook("post_invocation", bad)
            self.assertEqual(out.get("terminationBehavior"), "terminate", msg=bad)

    def test_an_oversized_payload_is_refused_without_reading_it_all(self):
        big = '{"terminationReason": "model_stop", "fullyIdle": true, "pad": "' + "x" * 1_200_000 + '"}'
        out, _ = self._run_hook("stop", big)
        self.assertEqual(out.get("decision"), "continue")
        self.assertIn("exceeds", out.get("reason", ""))

    def test_pre_invocation_cannot_block_by_contract_and_says_so(self):
        out, stderr = self._run_hook("pre_invocation", "{ broken")
        self.assertEqual(out, {"injectSteps": []})
        self.assertIn("cannot block by contract", stderr)

    def test_a_hook_launched_without_its_event_name_blocks(self):
        proc = subprocess.run([sys.executable, HOOK_SCRIPT], input='{"toolCall": {"name": "run_command", "args": {}}}',
                              text=True, capture_output=True, timeout=10)
        out = json.loads(proc.stdout.strip())
        self.assertEqual(out.get("decision"), "deny")
        self.assertIn("unknown hook mode", out.get("reason", ""))

    def test_a_crash_answers_in_the_events_own_schema(self):
        import importlib.util
        spec = importlib.util.spec_from_file_location("hook_under_test", HOOK_SCRIPT)
        hook = importlib.util.module_from_spec(spec); spec.loader.exec_module(hook)
        import io, contextlib
        def boom(*a, **k):
            raise RuntimeError("secret=ABC")
        for mode, handler, check in (("stop", "handle_stop", lambda o: o.get("decision") == "continue"),
                                     ("pre_invocation", "handle_pre_invocation", lambda o: o == {"injectSteps": []}),
                                     ("post_invocation", "handle_post_invocation", lambda o: o.get("terminationBehavior") == "terminate"),
                                     ("pre_tool", "handle_pre_tool", lambda o: o.get("decision") == "deny")):
            original = getattr(hook, handler); setattr(hook, handler, boom)
            old_argv, old_stdin = sys.argv, sys.stdin
            sys.argv, sys.stdin = ["hook", mode], io.StringIO('{"invocationNum": 1, "terminationReason": "x", "fullyIdle": true, "toolCall": {"name": "run_command", "args": {}}}')
            buf = io.StringIO()
            try:
                with contextlib.redirect_stdout(buf), contextlib.redirect_stderr(io.StringIO()):
                    hook.main()
            finally:
                setattr(hook, handler, original); sys.argv, sys.stdin = old_argv, old_stdin
            out = json.loads(buf.getvalue().strip())
            self.assertTrue(check(out), msg="%s -> %r" % (mode, out))
            self.assertNotIn("ABC", json.dumps(out))


    # FX-49.2 entry-point host data boundary tests.
    # Each of these calls self._fx492_host_root(), which is provided by the
    # companion code change; removing that code makes these tests error, so
    # they only pass while the boundary probe is present. Owner ruling
    # 2026-09-30 ("I allow full access to antigravity"): the guard is off by
    # default, so the deny cases run under the opt in, and the two
    # test_default_* cases prove the default allows.
    def test_default_read_under_host_data_root_is_allowed(self):
        inside = os.path.join(self._fx492_host_root(), "builtin", "skills", "x.md")
        payload = json.dumps({
            "toolCall": {"name": "view_file", "args": {"TargetFile": inside}},
        })
        out, _ = self._run_hook("pre_tool", payload)
        self.assertEqual(out.get("decision"), "allow")

    def test_default_write_under_host_data_root_is_allowed(self):
        inside = os.path.join(self._fx492_host_root(), "settings", "notes.md")
        payload = json.dumps({
            "toolCall": {"name": "write_to_file", "args": {"TargetFile": inside}},
        })
        out, _ = self._run_hook("pre_tool", payload)
        self.assertEqual(out.get("decision"), "allow")

    def test_pre_tool_command_under_host_data_root_blocks(self):
        root = self._fx492_host_root()
        self.assertIsNotNone(root)
        inside = os.path.join(root, "builtin", "skills", "x.md")
        payload = json.dumps({
            "toolCall": {"name": "run_command", "args": {"CommandLine": "cat " + inside}},
            "stepIdx": 1,
        })
        out, _ = self._run_hook("pre_tool", payload, guard="1")
        self.assertEqual(out.get("decision"), "deny")
        self.assertIn("host data directory", out.get("reason", "").lower())

    def test_pre_tool_read_under_host_data_root_blocks(self):
        root = self._fx492_host_root()
        self.assertIsNotNone(root)
        inside = os.path.join(
            root, "builtin", "skills", "agy-customizations", "docs", "hooks.md"
        )
        payload = json.dumps({
            "toolCall": {"name": "view_file", "args": {"TargetFile": inside}},
            "stepIdx": 2,
        })
        out, _ = self._run_hook("pre_tool", payload, guard="1")
        self.assertEqual(out.get("decision"), "deny")
        self.assertIn("host data directory", out.get("reason", "").lower())

    def test_pre_tool_ordinary_workspace_read_is_allowed(self):
        workspace = os.path.join(tempfile.gettempdir(), "workspace", "module.py")
        payload = json.dumps({
            "toolCall": {"name": "view_file", "args": {"TargetFile": workspace}},
            "stepIdx": 3,
        })
        out, _ = self._run_hook("pre_tool", payload)
        self.assertEqual(out.get("decision"), "allow")

    def test_pre_tool_empty_argument_preserves_allow(self):
        payload = json.dumps({
            "toolCall": {"name": "view_file", "args": {"TargetFile": ""}},
            "stepIdx": 4,
        })
        out, _ = self._run_hook("pre_tool", payload)
        self.assertEqual(out.get("decision"), "allow")

    def test_pre_tool_malformed_command_blocks(self):
        payload = json.dumps({
            "toolCall": {"name": "run_command", "args": {"CommandLine": "cat 'open"}},
            "stepIdx": 5,
        })
        out, _ = self._run_hook("pre_tool", payload, guard="1")
        self.assertEqual(out.get("decision"), "deny")

    def test_pre_tool_hostile_argument_is_refused_not_crashed(self):
        for value in (None, 0, 1.5, float("nan"), True, [], {}):
            with self.subTest(value=repr(value)):
                payload = json.dumps({
                    "toolCall": {"name": "view_file", "args": {"TargetFile": value}},
                    "stepIdx": 6,
                })
                out, _ = self._run_hook("pre_tool", payload)
                self.assertEqual(out.get("decision"), "allow")

    def test_pre_tool_hostile_toolcall_is_refused_not_crashed(self):
        for value in (None, "text", 12, [], {}, 1.5, float("nan"), True):
            with self.subTest(value=repr(value)):
                payload = json.dumps({"toolCall": value, "stepIdx": 7})
                out, _ = self._run_hook("pre_tool", payload)
                self.assertEqual(out.get("decision"), "deny")

    # OP1.c (e) and (f), docs/plan/specs/OP1.md 5.2: corrupt input blocks
    # whatever the host data setting says; with the guard on, a nested or a
    # variable carrying path cannot pass it. Each case isolates one condition.
    def test_a_known_tool_with_non_dict_args_is_denied(self):
        for tool_call in ({"name": "view_file", "args": "TargetFile=/tmp/x"},
                          {"name": "view_file"}):
            with self.subTest(args=tool_call.get("args", "<absent>")):
                out, _ = self._run_hook("pre_tool", json.dumps({"toolCall": tool_call, "stepIdx": 8}))
                self.assertEqual(out.get("decision"), "deny")
                self.assertIn("args", out.get("reason", ""))

    def test_a_run_command_without_a_command_line_is_denied(self):
        for args in ({}, {"CommandLine": ""}, {"CommandLine": "   "}):
            with self.subTest(args=args):
                payload = json.dumps({"toolCall": {"name": "run_command", "args": args}, "stepIdx": 9})
                out, _ = self._run_hook("pre_tool", payload)
                self.assertEqual(out.get("decision"), "deny")
                self.assertIn("CommandLine", out.get("reason", ""))

    def test_a_nested_host_data_path_is_denied_when_the_guard_is_on(self):
        inside = os.path.join(self._fx492_host_root(), "builtin", "skills", "x.md")
        payload = json.dumps({
            "toolCall": {"name": "multi_replace_file_content",
                         "args": {"Edits": [{"Target": {"TargetFile": inside}}]}},
            "stepIdx": 10,
        })
        out, _ = self._run_hook("pre_tool", payload, guard="1")
        self.assertEqual(out.get("decision"), "deny")
        self.assertIn("host data directory", out.get("reason", "").lower())
        out, _ = self._run_hook("pre_tool", payload)
        self.assertEqual(out.get("decision"), "allow", "with the guard off nothing changes")

    def test_the_host_data_root_under_another_spelling_is_denied_when_the_guard_is_on(self):
        """A case insensitive filesystem opens .GEMINI as .gemini: the guard compares identities, so the other
        spelling is the same folder and is denied (review 2026-10-06: it was allowed)."""
        root = self._fx492_host_root()
        os.makedirs(os.path.join(root, "sub"), exist_ok=True)
        shouted = os.path.join(os.path.dirname(os.path.dirname(root)), ".GEMINI", "Antigravity-IDE", "sub", "secret.txt")
        if not os.path.exists(os.path.dirname(shouted)):
            self.skipTest("this filesystem is case sensitive: %s names no existing folder, so there is no second "
                          "spelling of the host data root to deny" % os.path.dirname(shouted))
        payload = json.dumps({"toolCall": {"name": "view_file", "args": {"AbsolutePath": shouted}}, "stepIdx": 12})
        out, _ = self._run_hook("pre_tool", payload, guard="1")
        self.assertEqual(out.get("decision"), "deny")
        self.assertIn("host data directory", out.get("reason", "").lower())

    def test_a_payload_too_deep_to_parse_is_denied_never_a_crash(self):
        """Corrupt input blocks. A nesting deep enough to exhaust the parser used to kill the hook with no
        decision (exit 1), and a hook that crashes guards nothing."""
        deep = '{"a":' * 100000 + "1" + "}" * 100000
        out, proc = self._run_hook("pre_tool", deep)
        self.assertEqual(out.get("decision"), "deny", getattr(proc, "stderr", ""))
        out, _ = self._run_hook("pre_tool", deep, guard="1")
        self.assertEqual(out.get("decision"), "deny")

    def test_a_variable_carrying_command_path_is_denied_when_the_guard_is_on(self):
        # HOME is the scratch home _run_hook sets, so $HOME/.gemini/... names
        # the host data root only once expandvars has run.
        spelled = "cat $HOME/.gemini/antigravity-ide/builtin/skills/x.md"
        payload = json.dumps({"toolCall": {"name": "run_command", "args": {"CommandLine": spelled}}, "stepIdx": 11})
        out, _ = self._run_hook("pre_tool", payload, guard="1")
        self.assertEqual(out.get("decision"), "deny")
        self.assertIn("host data directory", out.get("reason", "").lower())
        out, _ = self._run_hook("pre_tool", payload)
        self.assertEqual(out.get("decision"), "allow", "with the guard off nothing changes")


def _fx493_guide_path():
    return os.path.join(Path(HERE).parent, "docs", "how-to", "install-antigravity.md")


@unittest.skipUnless(
    os.path.isfile(_fx493_guide_path()),
    "install-antigravity guide is not present in this checkout",
)
def _fx493_install_doc_disclaims_private_term_isolation(self):
    guide_path = _fx493_guide_path()
    with open(guide_path, "rb") as handle:
        text = handle.read().decode("utf-8")
    self.assertIn(
        "On this host, a new workspace is not a boundary, and private term isolation is not claimed.",
        text,
    )
    self.assertIn(
        "`brother.loop` is unavailable on Gemini until `model_call` has a headless transport.",
        text,
    )


TestAntigravityPackage.test_install_doc_disclaims_private_term_isolation = (
    _fx493_install_doc_disclaims_private_term_isolation
)


if __name__ == "__main__":
    unittest.main()

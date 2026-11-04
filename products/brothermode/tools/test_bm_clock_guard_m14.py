"""M1.4 selftest, mutation probe, installed copy and key components proof."""
import json
import os
import sys
import tempfile
import unittest

# The folder's own import form (as test_bm_repair_d16 beside this file): tools/test_all.py runs each suite as a
# script from this folder, where the repository root package does not exist. The hub rows still import the same file.
_HERE = os.path.dirname(os.path.abspath(__file__))
if _HERE not in sys.path:
    sys.path.insert(0, _HERE)

import bm_clock_guard as guard_module  # noqa: E402
from bm_clock_guard import (  # noqa: E402
    decide,
    selftest,
    selftest_mutation_probe,
    to_minutes,
    verify_installed,
    verify_key_components,
)


_PLUGIN_ROOT = os.path.dirname(_HERE)
_REPO_ROOT = os.path.dirname(os.path.dirname(_PLUGIN_ROOT))
_HOOKS_JSON = os.path.join(_PLUGIN_ROOT, "hooks", "hooks.json")
_KEY_COMPONENTS = os.path.join(_REPO_ROOT, "docs", "plan", "KEY-COMPONENTS.json")


def _write_json(path, data):
    with open(path, "w", encoding="utf-8") as handle:
        json.dump(data, handle)


class TestGuardSelftest(unittest.TestCase):
    def test_selftest_green(self):
        ok, message = selftest()
        self.assertTrue(ok, message)
        self.assertIsInstance(message, str)
        self.assertTrue(message)

    def test_selftest_mutation_probe_green(self):
        ok, message = selftest_mutation_probe()
        self.assertTrue(ok, message)

    def test_selftest_probe_red(self):
        original = guard_module.decide

        def broken_decide(text, minutes):
            return ("ALLOW", "OK")

        guard_module.decide = broken_decide
        try:
            ok, message = selftest_mutation_probe()
        finally:
            guard_module.decide = original
        self.assertFalse(ok, message)

    def test_selftest_probe_uses_shifted_evidence(self):
        base = to_minutes(9, 41)
        self.assertEqual(decide("meet at 09:41", [base]), ("ALLOW", "OK"))
        self.assertEqual(decide("meet at 09:41", [base + 30]), ("BLOCK", "MISMATCH"))


class TestVerifyInstalled(unittest.TestCase):
    def _make_full_fixture(self, folder, stop_command="python3 bm_clock_guard.py"):
        tools = os.path.join(folder, "tools")
        hooks = os.path.join(folder, "hooks")
        os.makedirs(tools, exist_ok=True)
        os.makedirs(hooks, exist_ok=True)
        with open(os.path.join(tools, "bm_clock_guard.py"), "w", encoding="utf-8") as handle:
            handle.write("value = 1\n")
        with open(os.path.join(tools, "test_bm_clock_guard.py"), "w", encoding="utf-8") as handle:
            handle.write("value = 1\n")
        hooks_data = {
            "hooks": {
                "PreToolUse": [
                    {"hooks": [{"type": "command", "command": "python3 bm_clock_guard.py"}]}
                ],
                "Stop": [
                    {"hooks": [{"type": "command", "command": stop_command}]}
                ],
            }
        }
        _write_json(os.path.join(hooks, "hooks.json"), hooks_data)

    @unittest.skipUnless(os.path.isfile(_HOOKS_JSON), "hooks.json not present")
    def test_verify_installed_real_tree(self):
        ok, message = verify_installed(_PLUGIN_ROOT)
        self.assertTrue(ok, message)

    def test_verify_installed_missing_files(self):
        with tempfile.TemporaryDirectory() as folder:
            ok, message = verify_installed(folder)
            self.assertFalse(ok)
            self.assertTrue(message)

    def test_verify_installed_full_fixture(self):
        with tempfile.TemporaryDirectory() as folder:
            self._make_full_fixture(folder)
            ok, message = verify_installed(folder)
            self.assertTrue(ok, message)

    def test_verify_installed_hooks_missing_guard_in_stop(self):
        with tempfile.TemporaryDirectory() as folder:
            self._make_full_fixture(folder, stop_command="python3 other_tool.py")
            ok, message = verify_installed(folder)
            self.assertFalse(ok)
            self.assertIn("Stop", message)

    def test_verify_installed_guard_does_not_compile(self):
        with tempfile.TemporaryDirectory() as folder:
            self._make_full_fixture(folder)
            with open(
                os.path.join(folder, "tools", "bm_clock_guard.py"), "w", encoding="utf-8"
            ) as handle:
                handle.write("def broken(:\n")
            ok, message = verify_installed(folder)
            self.assertFalse(ok)
            self.assertIn("compile", message)

    def test_verify_installed_test_file_missing(self):
        with tempfile.TemporaryDirectory() as folder:
            self._make_full_fixture(folder)
            os.remove(os.path.join(folder, "tools", "test_bm_clock_guard.py"))
            ok, message = verify_installed(folder)
            self.assertFalse(ok)

    def test_verify_installed_hostile(self):
        for bad in (None, 4, [], True, 3.5, b"path"):
            ok, message = verify_installed(bad)
            self.assertFalse(ok)
            self.assertTrue(message)


class TestVerifyKeyComponents(unittest.TestCase):
    def _write_plan(self, folder, payload):
        plan = os.path.join(folder, "docs", "plan")
        os.makedirs(plan, exist_ok=True)
        path = os.path.join(plan, "KEY-COMPONENTS.json")
        if isinstance(payload, str):
            with open(path, "w", encoding="utf-8") as handle:
                handle.write(payload)
        else:
            _write_json(path, payload)
        return path

    def _entry(self, **overrides):
        entry = {
            "id": "clock-guard",
            "name": "clock-guard",
            "tool": "products/brothermode/tools/bm_clock_guard.py",
            "guard": "products/brothermode/tools/test_bm_clock_guard.py",
            "hooks": ["PreToolUse", "Stop"],
        }
        entry.update(overrides)
        return entry

    @unittest.skipUnless(os.path.isfile(_KEY_COMPONENTS), "key components not present")
    def test_verify_key_components(self):
        ok, message = verify_key_components(_REPO_ROOT)
        self.assertTrue(ok, message)

    def test_verify_key_components_missing(self):
        with tempfile.TemporaryDirectory() as folder:
            ok, message = verify_key_components(folder)
            self.assertFalse(ok)
            self.assertTrue(message)

    def test_verify_key_components_missing_entry(self):
        with tempfile.TemporaryDirectory() as folder:
            self._write_plan(folder, {"components": [{"id": "something-else"}]})
            ok, message = verify_key_components(folder)
            self.assertFalse(ok)

    def test_verify_key_components_wrong_tool(self):
        with tempfile.TemporaryDirectory() as folder:
            self._write_plan(folder, {"components": [self._entry(tool="tools/bm_clock_guard.py")]})
            ok, message = verify_key_components(folder)
            self.assertFalse(ok)
            self.assertIn("tool", message)

    def test_verify_key_components_right_entry(self):
        with tempfile.TemporaryDirectory() as folder:
            self._write_plan(folder, {"components": [self._entry()]})
            self.assertEqual(verify_key_components(folder), (True, "clock-guard entry verified"))

    def test_verify_key_components_wrong_guard(self):
        with tempfile.TemporaryDirectory() as folder:
            self._write_plan(folder, {"components": [self._entry(guard="wrong/path.py")]})
            ok, message = verify_key_components(folder)
            self.assertFalse(ok)
            self.assertIn("guard", message)

    def test_verify_key_components_wrong_hooks(self):
        with tempfile.TemporaryDirectory() as folder:
            self._write_plan(folder, {"components": [self._entry(hooks=["PreToolUse"])]})
            ok, message = verify_key_components(folder)
            self.assertFalse(ok)
            self.assertIn("hooks", message)

    def test_verify_key_components_full_fixture(self):
        with tempfile.TemporaryDirectory() as folder:
            self._write_plan(folder, {"components": [self._entry()]})
            ok, message = verify_key_components(folder)
            self.assertTrue(ok, message)

    def test_verify_key_components_malformed(self):
        with tempfile.TemporaryDirectory() as folder:
            self._write_plan(folder, "not json {{{")
            ok, message = verify_key_components(folder)
            self.assertFalse(ok)

    def test_verify_key_components_hostile(self):
        for bad in (None, 4, [], True, 3.5, b"path"):
            ok, message = verify_key_components(bad)
            self.assertFalse(ok)
            self.assertTrue(message)


# Fixture times are joined at run time: written as literals, this very file is refused by the guard it tests.
T_RUN = "13" + ":24"
T_OFF = "13" + ":25"
T_START = "09" + ":41"
T_END = "10" + ":02"


class TestObservedTimesAreEvidence(unittest.TestCase):
    """B1 to B3 (1.1.9 incidents, owner order 2026-10-31 "fix it at root cause"): the guard's only evidence was a
    fresh clock read, so every time quoted from a log, ls, stat or git log output the session had just read was denied
    as if it claimed the time now. A time is evidence when it appears verbatim in a tool's OUTPUT earlier in this turn
    and not in that same tool's own input (an echo is the model typing it, not observing it). Driven through main(),
    the entry point, with Claude Code's own transcript shape."""

    def _run(self, records, content, tool_use_id="w1"):
        import io
        with tempfile.TemporaryDirectory() as folder:
            path = os.path.join(folder, "t.jsonl")
            with open(path, "w", encoding="utf-8") as fh:
                fh.write("\n".join(json.dumps(r) for r in records))
            payload = {"hook_event_name": "PreToolUse", "tool_name": "Write", "tool_use_id": tool_use_id,
                       "tool_input": {"file_path": os.path.join(folder, "notes.md"), "content": content},
                       "transcript_path": path}
            saved = sys.stdin, sys.stdout, sys.stderr
            sys.stdin, sys.stdout, sys.stderr = io.StringIO(json.dumps(payload)), io.StringIO(), io.StringIO()
            try:
                code = guard_module.main([])
                out = sys.stdout.getvalue()
            finally:
                sys.stdin, sys.stdout, sys.stderr = saved
            return code, out

    @staticmethod
    def _turn(tool_name, tool_input, output, before=()):
        return list(before) + [
            {"type": "user", "message": {"content": [{"type": "text", "text": "write the notes"}]}},
            {"type": "assistant", "message": {"content": [{"type": "tool_use", "id": "a1", "name": tool_name,
                                                            "input": tool_input}]}},
            {"type": "user", "message": {"content": [{"type": "tool_result", "tool_use_id": "a1",
                                                       "content": output}]}},
        ]

    def assertAllowed(self, result):
        self.assertEqual(result, (0, ""), "a time copied from real output was denied: %r" % (result,))

    def assertDenied(self, result):
        code, out = result
        self.assertEqual(code, 0)
        self.assertIn('"deny"', out, "a time with no evidence was allowed")
        return json.loads(out)["hookSpecificOutput"]["permissionDecisionReason"]

    def _ls(self):
        return self._turn("Bash", {"command": "ls -l notes"},
                          "-rw-r--r--  1 k  staff  12 Oct 31 %s notes" % T_RUN)

    def test_a_time_copied_from_command_output_is_allowed(self):
        self.assertAllowed(self._run(self._ls(), "the build ran at %s" % T_RUN))

    def test_a_time_printed_from_a_file_by_an_observer_is_allowed(self):
        records = self._turn("Bash", {"command": "cat run.log"}, "started %s, finished %s" % (T_START, T_END))
        self.assertAllowed(self._run(records, "the run started %s and finished %s" % (T_START, T_END)))

    def test_a_piped_observer_and_a_leading_cd_still_observe(self):
        for command in ("ls -ltr logs | tail -3", "cd /x && git log -1"):
            records = self._turn("Bash", {"command": command}, "Oct 31 %s run.log" % T_RUN)
            self.assertAllowed(self._run(records, "the run ended %s" % T_RUN))

    def test_a_time_generator_or_compound_is_not_an_observer(self):
        # A generator's output is a time the call produced (m13 pins the date forms); a compound can print anything.
        for command in ("python3 -c 'print(1)'", "ls && echo x", "cat run.log > copy"):
            records = self._turn("Bash", {"command": command}, "Oct 31 %s run.log" % T_RUN)
            self.assertDenied(self._run(records, "the run ended %s" % T_RUN))

    def test_a_time_one_minute_off_a_real_one_is_denied(self):
        self.assertIn(T_OFF, self.assertDenied(self._run(self._ls(), "the build ran at %s" % T_OFF)))

    def test_an_echoed_time_is_not_evidence(self):
        records = self._turn("Bash", {"command": "echo %s" % T_RUN}, T_RUN)
        self.assertDenied(self._run(records, "the build ran at %s" % T_RUN))

    def test_a_time_seen_in_an_earlier_turn_is_not_evidence(self):
        records = self._ls() + [{"type": "user", "message": {"content": [{"type": "text", "text": "next task"}]}}]
        self.assertDenied(self._run(records, "the build ran at %s" % T_RUN))

    def test_an_observer_that_can_produce_its_output_is_not_an_observer(self):
        # Security review of dc2004d9d: a git alias runs anything, a format directive or hex escape builds a time
        # with no literal in the command, find can print or exec; each output is produced, not read.
        h, m = T_RUN.split(":")
        for command in ("git -c alias.x=y x", "git log -1 --format=%s%%x3a%s" % (h, m), "find . -printf x",
                        "stat -f %Sm notes", "git config --list"):
            records = self._turn("Bash", {"command": command}, "Oct 31 %s run.log" % T_RUN)
            self.assertDenied(self._run(records, "the run ended %s" % T_RUN))

    def test_options_that_run_another_program_make_a_producer(self):
        for command in ("rg --pre ./x pattern run.log", "sort --compress-program=x run.log",
                        "git log --textconv -1", "git show --ext-diff HEAD"):
            records = self._turn("Bash", {"command": command}, "Oct 31 %s run.log" % T_RUN)
            self.assertDenied(self._run(records, "the run ended %s" % T_RUN))

    def test_a_time_written_this_turn_then_read_back_is_not_evidence(self):
        write = [{"type": "assistant", "message": {"content": [{"type": "tool_use", "id": "w0", "name": "Write",
                  "input": {"file_path": "/x/run.log", "content": "ended %s" % T_RUN}}]}},
                 {"type": "user", "message": {"content": [{"type": "tool_result", "tool_use_id": "w0",
                  "content": "written"}]}}]
        records = self._turn("Bash", {"command": "cat /x/run.log"}, "ended %s" % T_RUN)
        records = records[:1] + write + records[1:]
        self.assertDenied(self._run(records, "the run ended %s" % T_RUN))

    def test_a_tool_that_is_not_bash_never_runs_a_command(self):
        # Its input may carry a "command" key; its output is that tool's, not a shell's: neither observed nor a clock.
        for command in ("cat run.log", "date"):
            records = self._turn("mcp__x__run", {"command": command}, "Oct 31 %s run.log" % T_RUN)
            self.assertDenied(self._run(records, "the run ended %s" % T_RUN))

    def test_a_flat_host_record_from_another_tool_is_not_observed(self):
        # Hosts that write flat tool_result lines name the tool; only Bash output is a shell observer's.
        flat = {"type": "tool_result", "tool_use_id": "f1", "tool": "mcp__x__run", "command": "cat run.log",
                "stdout": "Oct 31 %s run.log" % T_RUN}
        records = [{"type": "user", "message": {"content": [{"type": "text", "text": "go"}]}}, flat]
        self.assertDenied(self._run(records, "the run ended %s" % T_RUN))

    def _write_call(self, tid, content):
        return [{"type": "assistant", "message": {"content": [{"type": "tool_use", "id": tid, "name": "Write",
                 "input": {"file_path": "/x/%s" % tid, "content": content}}]}},
                {"type": "user", "message": {"content": [{"type": "tool_result", "tool_use_id": tid,
                 "content": "written"}]}}]

    def test_an_abbreviated_producer_option_is_still_a_producer(self):
        for command in ("git log --textc -1", "git show --ext HEAD"):
            records = self._turn("Bash", {"command": command}, "Date: Fri Oct 31 %s 2026" % T_RUN)
            self.assertDenied(self._run(records, "the commit landed %s" % T_RUN))

    def test_pieces_written_this_turn_and_joined_by_cat_are_not_evidence(self):
        h, m = T_RUN.split(":")
        records = self._turn("Bash", {"command": "cat /x/a /x/b"}, "ended %s" % T_RUN)
        records = records[:1] + self._write_call("a", "ended %s" % h) + self._write_call("b", ":%s" % m) + records[1:]
        self.assertDenied(self._run(records, "the run ended %s" % T_RUN))

    def test_a_time_set_through_touch_then_listed_is_not_evidence(self):
        h, m = T_RUN.split(":")
        touch = self._turn("Bash", {"command": "touch -t 20261031%s%s /x/notes" % (h, m)}, "")[1:]
        records = self._turn("Bash", {"command": "ls -l /x/notes"}, "Oct 31 %s notes" % T_RUN)
        records = records[:1] + touch + records[1:]
        self.assertDenied(self._run(records, "the file changed %s" % T_RUN))

    def test_metadata_still_observes_after_a_write_this_turn(self):
        records = self._turn("Bash", {"command": "ls -l /x/run.log"}, "Oct 31 %s run.log" % T_RUN)
        records = records[:1] + self._write_call("n", "notes with no time") + records[1:]
        self.assertAllowed(self._run(records, "the run log changed %s" % T_RUN))

    def test_a_time_typed_in_any_input_field_is_not_evidence(self):
        # The description a model writes beside a command is typed text too (pins mutation F6).
        records = self._turn("Bash", {"command": "cat run.log", "description": "check the %s run" % T_RUN},
                             "ended %s" % T_RUN)
        self.assertDenied(self._run(records, "the run ended %s" % T_RUN))

    def test_a_flat_host_records_typed_command_is_not_evidence(self):
        # A host's flat record carries the command and no input, so the command is the typed text (pins F7).
        flat = {"type": "tool_result", "tool_use_id": "f2", "command": "grep %s run.log" % T_RUN,
                "stdout": "ended %s" % T_RUN}
        records = [{"type": "user", "message": {"content": [{"type": "text", "text": "go"}]}}, flat]
        self.assertDenied(self._run(records, "the run ended %s" % T_RUN))

    def test_a_time_split_by_shell_quoting_is_still_typed(self):
        h, m = T_RUN.split(":")
        records = self._turn("Bash", {"command": 'grep "%s"":%s" run.log' % (h, m)}, "ended %s" % T_RUN)
        self.assertDenied(self._run(records, "the run ended %s" % T_RUN))

    def test_git_log_and_show_still_observe(self):
        for command in ("git log -1", "git show --stat HEAD"):
            records = self._turn("Bash", {"command": command}, "Date: Fri Oct 31 %s 2026" % T_RUN)
            self.assertAllowed(self._run(records, "the commit landed %s" % T_RUN))

    def test_a_time_typed_into_an_observers_own_arguments_is_not_evidence(self):
        records = self._turn("Bash", {"command": "grep %s run.log" % T_RUN}, "ended %s" % T_RUN)
        self.assertDenied(self._run(records, "the run ended %s" % T_RUN))

    def _stop(self, records):
        import io
        with tempfile.TemporaryDirectory() as folder:
            path = os.path.join(folder, "t.jsonl")
            with open(path, "w", encoding="utf-8") as fh:
                fh.write("\n".join(json.dumps(r) for r in records))
            saved = sys.stdin, sys.stdout, sys.stderr
            sys.stdin = io.StringIO(json.dumps({"hook_event_name": "Stop", "transcript_path": path}))
            sys.stdout, sys.stderr = io.StringIO(), io.StringIO()
            try:
                code = guard_module.main([])
                err = sys.stderr.getvalue()
            finally:
                sys.stdin, sys.stdout, sys.stderr = saved
            return code, err

    def test_the_stop_check_reads_observed_times_too(self):
        said = {"type": "assistant", "message": {"content": [{"type": "text", "text": "it ended %s" % T_RUN}]}}
        code, err = self._stop(self._ls() + [said])
        self.assertEqual((code, err), (0, ""), err)
        code, err = self._stop(self._turn("Bash", {"command": "ls"}, "notes") + [said])
        self.assertEqual(code, 0)
        self.assertIn(T_RUN, err, "an unbacked time in the last message went unreported at Stop")

    def test_the_deny_names_the_guard_and_the_route_out(self):
        records = self._turn("Bash", {"command": "ls"}, "notes")
        reason = self.assertDenied(self._run(records, "it ran at %s" % T_RUN))
        # The code stays first (m13 pins "NO-DATA: " and "MISMATCH: " as the prefix); the guard names itself next.
        self.assertTrue(reason.startswith("NO-DATA: bm_clock_guard"), reason)
        self.assertIn("output", reason)


if __name__ == "__main__":
    unittest.main()

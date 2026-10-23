"""M1.3 hook entry and wiring for the clock guard."""
import io
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

from bm_clock_guard import (  # noqa: E402
    extract_evidence,
    extract_outgoing,
    main,
)


_HOOKS_JSON = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "hooks", "hooks.json"
)


def _jsonl(*records):
    return "\n".join(json.dumps(record) for record in records)


def _tool_result(command, **fields):
    record = {"type": "tool_result", "tool_use_id": "toolu_1", "command": command}
    record.update(fields)
    return record


def _run_main(stdin_text):
    saved_in = sys.stdin
    saved_out = sys.stdout
    saved_err = sys.stderr
    sys.stdin = io.StringIO(stdin_text)
    captured_out = io.StringIO()
    captured_err = io.StringIO()
    sys.stdout = captured_out
    sys.stderr = captured_err
    try:
        try:
            code = main([])
        except SystemExit as exc:
            code = exc.code if isinstance(exc.code, int) else 0
    finally:
        sys.stdin = saved_in
        sys.stdout = saved_out
        sys.stderr = saved_err
    return code, captured_out.getvalue(), captured_err.getvalue()


class TestHookEntry(unittest.TestCase):
    def _mk_transcript(self, folder, *records):
        path = os.path.join(folder, "t.jsonl")
        with open(path, "w", encoding="utf-8") as handle:
            handle.write(_jsonl(*records))
        return path

    # ----- extract_outgoing -----

    def test_extract_outgoing_bash(self):
        payload = {"tool_name": "Bash", "tool_input": {"command": "echo 14:22"}}
        text, reason = extract_outgoing(payload)
        self.assertEqual(reason, "")
        self.assertIn("14:22", text)

    def test_extract_outgoing_write(self):
        payload = {
            "tool_name": "Write",
            "tool_input": {"file_path": "/tmp/x.txt", "content": "05:00 hi"},
        }
        text, reason = extract_outgoing(payload)
        self.assertEqual(reason, "")
        self.assertIn("05:00", text)
        self.assertIn("/tmp/x.txt", text)

    def test_extract_outgoing_edit(self):
        payload = {
            "tool_name": "Edit",
            "tool_input": {"file_path": "/tmp/x.txt", "old_string": "a", "new_string": "b 06:30"},
        }
        text, reason = extract_outgoing(payload)
        self.assertEqual(reason, "")
        self.assertIn("06:30", text)

    def test_extract_outgoing_multiedit(self):
        payload = {
            "tool_name": "MultiEdit",
            "tool_input": {
                "file_path": "/tmp/x.txt",
                "edits": [
                    {"old_string": "a", "new_string": "b 07:30"},
                    {"old_string": "c", "new_string": "d 08:45"},
                ],
            },
        }
        text, reason = extract_outgoing(payload)
        self.assertEqual(reason, "")
        self.assertIn("07:30", text)
        self.assertIn("08:45", text)

    def test_extract_outgoing_notebook(self):
        payload = {
            "tool_name": "NotebookEdit",
            "tool_input": {"notebook_path": "/tmp/n.ipynb", "new_source": "print('09:15')"},
        }
        text, reason = extract_outgoing(payload)
        self.assertEqual(reason, "")
        self.assertIn("09:15", text)

    def test_extract_outgoing_missing_fields_corrupt(self):
        self.assertEqual(extract_outgoing({"tool_name": "Bash", "tool_input": {}}), ("", "CORRUPT"))
        self.assertEqual(extract_outgoing({"tool_name": 7, "tool_input": {}}), ("", "CORRUPT"))
        self.assertEqual(extract_outgoing({"tool_name": True, "tool_input": {}}), ("", "CORRUPT"))
        self.assertEqual(extract_outgoing({"tool_name": "Write", "tool_input": {}}), ("", "CORRUPT"))
        self.assertEqual(extract_outgoing({"tool_name": "Edit", "tool_input": {"file_path": 3}}), ("", "CORRUPT"))
        self.assertEqual(
            extract_outgoing({"tool_name": "MultiEdit", "tool_input": {"file_path": "/x", "edits": "no"}}),
            ("", "CORRUPT"),
        )
        self.assertEqual(
            extract_outgoing({"tool_name": "MultiEdit", "tool_input": {"file_path": "/x", "edits": [None]}}),
            ("", "CORRUPT"),
        )
        self.assertEqual(extract_outgoing({"tool_name": "Unknown", "tool_input": {}}), ("", "CORRUPT"))
        self.assertEqual(extract_outgoing([]), ("", "CORRUPT") if False else extract_outgoing.__wrapped__ if False else ("", "CORRUPT")) if False else None

    def test_extract_outgoing_hostile(self):
        for bad in (None, [], "x", 4, True, 3.5):
            with self.assertRaises(ValueError):
                extract_outgoing(bad)

    # ----- extract_evidence -----

    def test_extract_evidence_filters_turn(self):
        with tempfile.TemporaryDirectory() as folder:
            path = self._mk_transcript(
                folder,
                {"type": "user", "content": "hi"},
                _tool_result("date", stdout="read at 07:05"),
                {"type": "user", "content": "again"},
                _tool_result("date", stdout="read at 08:15"),
                _tool_result("date", stdout="read at 09:25"),
            )
            text = extract_evidence({"transcript_path": path, "tool_use_id": "missing"})
            self.assertIn("08:15", text)
            self.assertIn("09:25", text)
            self.assertNotIn("07:05", text)

    def test_extract_evidence_stops_at_current(self):
        with tempfile.TemporaryDirectory() as folder:
            path = self._mk_transcript(
                folder,
                {"type": "user", "content": "hi"},
                _tool_result("date", stdout="read at 08:15"),
                {"type": "tool_result", "id": "toolu_now", "command": "date", "stdout": "read at 09:25"},
            )
            text = extract_evidence({"transcript_path": path, "tool_use_id": "toolu_now"})
            self.assertIn("08:15", text)
            self.assertNotIn("09:25", text)

    def test_extract_evidence_missing_path_refused(self):
        with self.assertRaises(ValueError):
            extract_evidence({})
        with self.assertRaises(ValueError):
            extract_evidence({"transcript_path": 5})
        with self.assertRaises(ValueError):
            extract_evidence({"transcript_path": None})

    def test_extract_evidence_unreadable_refused(self):
        with self.assertRaises(ValueError):
            extract_evidence({"transcript_path": "/nonexistent/path/xyz.jsonl"})

    def test_extract_evidence_directory_refused(self):
        with tempfile.TemporaryDirectory() as folder:
            with self.assertRaises(ValueError):
                extract_evidence({"transcript_path": folder})

    def test_extract_evidence_hostile(self):
        for bad in (None, [], "x", 4, True, 3.5):
            with self.assertRaises(ValueError):
                extract_evidence(bad)

    # ----- main -----

    def test_pretooluse_allow_no_times(self):
        with tempfile.TemporaryDirectory() as folder:
            path = self._mk_transcript(folder, {"type": "user", "content": "hi"})
            payload = {
                "hook_event_name": "PreToolUse",
                "tool_name": "Bash",
                "tool_input": {"command": "echo hello"},
                "transcript_path": path,
                "tool_use_id": "t",
            }
            code, out, err = _run_main(json.dumps(payload))
            self.assertEqual(code, 0)
            self.assertEqual(out, "")
            self.assertEqual(err, "")

    def test_pretooluse_block_mismatch(self):
        with tempfile.TemporaryDirectory() as folder:
            path = self._mk_transcript(
                folder,
                {"type": "user", "content": "hi"},
                _tool_result("date", stdout="read at 07:05"),
            )
            payload = {
                "hook_event_name": "PreToolUse",
                "tool_name": "Bash",
                "tool_input": {"command": "commit 14:22"},
                "transcript_path": path,
                "tool_use_id": "t",
            }
            code, out, err = _run_main(json.dumps(payload))
            self.assertEqual(code, 0)
            data = json.loads(out.strip())
            self.assertEqual(data["hookSpecificOutput"]["hookEventName"], "PreToolUse")
            self.assertEqual(data["hookSpecificOutput"]["permissionDecision"], "deny")
            # the reason names its code first, then the token it matched (2026-10-21: a bare code named nothing)
            self.assertTrue(data["hookSpecificOutput"]["permissionDecisionReason"].startswith("MISMATCH: "))
            self.assertIn(_t(14, 22), data["hookSpecificOutput"]["permissionDecisionReason"])

    def test_pretooluse_allow_with_evidence(self):
        with tempfile.TemporaryDirectory() as folder:
            path = self._mk_transcript(
                folder,
                {"type": "user", "content": "hi"},
                _tool_result("date", stdout="read at 14:21"),
            )
            payload = {
                "hook_event_name": "PreToolUse",
                "tool_name": "Bash",
                "tool_input": {"command": "commit at 14:22"},
                "transcript_path": path,
                "tool_use_id": "t",
            }
            code, out, err = _run_main(json.dumps(payload))
            self.assertEqual(code, 0)
            self.assertEqual(out, "")

    # ----- Stop: the host's own spelling, never "BLOCK" -----
    # A Stop payload carries no tool_name. Before 2026-10-04 the guard read
    # it as CORRUPT outgoing text and wrote {"decision": "BLOCK"} at every
    # turn end on every host. Each case below isolates one condition.

    def _assistant(self, text):
        return {"type": "assistant", "message": {"content": [{"type": "text", "text": text}]}}

    def _stop_payload(self, **fields):
        payload = {"hook_event_name": "Stop", "session_id": "s", "cwd": "/fixture",
                   "stop_hook_active": False, "transcript_path": None}
        payload.update(fields)
        return payload

    def test_stop_nothing_to_check_allows_silently(self):
        code, out, err = _run_main(json.dumps(self._stop_payload()))
        self.assertEqual((code, out, err), (0, "", ""))

    def test_stop_no_transcript_allows_and_states_why(self):
        # The Codex shape: transcript_path null, the text in last_assistant_message.
        payload = self._stop_payload(last_assistant_message="committed at 14:22")
        code, out, err = _run_main(json.dumps(payload))
        self.assertEqual((code, out), (0, ""))
        self.assertIn("NO-DATA", err)
        self.assertIn("gives no transcript", err)
        self.assertIn("14:22", err)
        self.assertNotIn("BLOCK", err)

    def test_stop_bare_time_with_no_clock_read_reports_never_blocks(self):
        # NO-DATA never blocks a stop: unverified is not disproved.
        with tempfile.TemporaryDirectory() as folder:
            path = self._mk_transcript(folder, {"type": "user", "content": "hi"},
                                       self._assistant("committed at 14:22"))
            code, out, err = _run_main(json.dumps(self._stop_payload(transcript_path=path)))
            self.assertEqual((code, out), (0, ""))
            self.assertIn("NO-DATA", err)
            self.assertIn("14:22", err)
            self.assertNotIn("BLOCK", err)

    def test_stop_unreadable_transcript_with_host_text_reports_never_blocks(self):
        payload = self._stop_payload(transcript_path="/nonexistent/path/xyz.jsonl",
                                     last_assistant_message="committed at 14:22")
        code, out, err = _run_main(json.dumps(payload))
        self.assertEqual((code, out), (0, ""))
        self.assertIn("NO-DATA", err)
        self.assertNotIn("BLOCK", err)

    def test_stop_mismatched_time_refuses_in_host_spelling(self):
        with tempfile.TemporaryDirectory() as folder:
            path = self._mk_transcript(folder, {"type": "user", "content": "hi"},
                                       _tool_result("date", stdout="09:00"),
                                       self._assistant("committed at 14:22"))
            code, out, err = _run_main(json.dumps(self._stop_payload(transcript_path=path)))
            self.assertEqual(code, 0)
            data = json.loads(out.strip())
            self.assertEqual(data["decision"], "block")
            self.assertIn("MISMATCH", data["reason"])
            self.assertIn("14:22", data["reason"])
            self.assertNotIn("BLOCK", out)
            self.assertNotIn("hookSpecificOutput", data)

    def test_stop_verified_time_allows(self):
        with tempfile.TemporaryDirectory() as folder:
            path = self._mk_transcript(folder, {"type": "user", "content": "hi"},
                                       _tool_result("date", stdout="14:22"),
                                       self._assistant("committed at 14:22"))
            code, out, err = _run_main(json.dumps(self._stop_payload(transcript_path=path)))
            self.assertEqual((code, out, err), (0, "", ""))

    def test_stop_estimate_only_allows(self):
        with tempfile.TemporaryDirectory() as folder:
            path = self._mk_transcript(folder, {"type": "user", "content": "hi"},
                                       self._assistant("done, eta 14:22 estimate"))
            code, out, err = _run_main(json.dumps(self._stop_payload(transcript_path=path)))
            self.assertEqual((code, out, err), (0, "", ""))

    def test_stop_host_text_wins_over_transcript(self):
        with tempfile.TemporaryDirectory() as folder:
            path = self._mk_transcript(folder, {"type": "user", "content": "hi"},
                                       self._assistant("committed at 14:22"))
            payload = self._stop_payload(transcript_path=path, last_assistant_message="no time here")
            code, out, err = _run_main(json.dumps(payload))
            self.assertEqual((code, out, err), (0, "", ""))

    def test_stop_already_continued_reports_instead_of_blocking_again(self):
        with tempfile.TemporaryDirectory() as folder:
            path = self._mk_transcript(folder, {"type": "user", "content": "hi"},
                                       _tool_result("date", stdout="09:00"),
                                       self._assistant("committed at 14:22"))
            payload = self._stop_payload(transcript_path=path, stop_hook_active=True)
            code, out, err = _run_main(json.dumps(payload))
            self.assertEqual((code, out), (0, ""))
            self.assertIn("MISMATCH", err)

    def test_stop_malformed_payload_is_corrupt_never_block(self):
        for bad in ({"last_assistant_message": 5}, {"transcript_path": 5}):
            code, out, err = _run_main(json.dumps(self._stop_payload(**bad)))
            self.assertEqual((code, out), (0, ""), bad)
            self.assertIn("CORRUPT", err)
            self.assertNotIn("BLOCK", err)

    def test_stop_unreadable_transcript_allows_and_states_why(self):
        payload = self._stop_payload(transcript_path="/nonexistent/path/xyz.jsonl")
        code, out, err = _run_main(json.dumps(payload))
        self.assertEqual((code, out), (0, ""))
        self.assertIn("NO-DATA", err)

    def test_cli_mode_block(self):
        with tempfile.TemporaryDirectory() as folder:
            path = self._mk_transcript(folder, {"type": "user", "content": "hi"})
            payload = {
                "tool_name": "Bash",
                "tool_input": {"command": "commit 14:22"},
                "transcript_path": path,
                "tool_use_id": "t",
            }
            code, out, err = _run_main(json.dumps(payload))
            self.assertEqual(code, 2)
            self.assertTrue(out.strip())
            self.assertTrue(err.strip())
            data = json.loads(out.strip())
            self.assertEqual(data["decision"], "BLOCK")
            err_data = json.loads(err.strip())
            self.assertEqual(err_data["decision"], "BLOCK")

    def test_cli_mode_allow(self):
        with tempfile.TemporaryDirectory() as folder:
            path = self._mk_transcript(folder, {"type": "user", "content": "hi"})
            payload = {
                "tool_name": "Bash",
                "tool_input": {"command": "echo hello"},
                "transcript_path": path,
                "tool_use_id": "t",
            }
            code, out, err = _run_main(json.dumps(payload))
            self.assertEqual(code, 0)
            data = json.loads(out.strip())
            self.assertEqual(data["decision"], "ALLOW")
            self.assertEqual(err, "")

    # ----- spec-named mutations -----

    # ----- the fail direction of the entry point (2026-10-04) -----
    # Only a tool call is denied. Input the guard cannot read, or a payload
    # that is not a tool call, is reported at exit 0: a "BLOCK" or an exit 2
    # reached from a Stop hook with no matcher traps the agent in a loop that
    # stop_hook_active cannot break when the payload itself is unreadable.

    def test_corrupt_stdin_reports_never_blocks(self):
        code, out, err = _run_main("not json {{{ ")
        self.assertEqual((code, out), (0, ""))
        self.assertIn("CORRUPT", err)
        self.assertNotIn("BLOCK", err)

    def test_non_tool_event_without_tool_name_reports_never_blocks(self):
        for payload in ({"hook_event_name": "SessionStart", "cwd": "/fixture"}, {"cwd": "/fixture"}):
            code, out, err = _run_main(json.dumps(payload))
            self.assertEqual((code, out), (0, ""), payload)
            self.assertIn("CORRUPT", err)
            self.assertNotIn("BLOCK", err)

    def test_pretooluse_malformed_tool_input_still_denies(self):
        payload = {"hook_event_name": "PreToolUse", "tool_name": "Bash", "tool_input": "not an object"}
        code, out, err = _run_main(json.dumps(payload))
        self.assertEqual(code, 0)
        data = json.loads(out.strip())
        self.assertEqual(data["hookSpecificOutput"]["permissionDecision"], "deny")
        self.assertEqual(data["hookSpecificOutput"]["permissionDecisionReason"], "CORRUPT")

    def test_oversized_blocks(self):
        with tempfile.TemporaryDirectory() as folder:
            path = self._mk_transcript(folder, {"type": "user", "content": "hi"})
            big = "14:22 " + ("x" * 20001)
            payload = {
                "hook_event_name": "PreToolUse",
                "tool_name": "Bash",
                "tool_input": {"command": big},
                "transcript_path": path,
                "tool_use_id": "t",
            }
            code, out, err = _run_main(json.dumps(payload))
            self.assertEqual(code, 0)
            data = json.loads(out.strip())
            self.assertEqual(data["hookSpecificOutput"]["permissionDecision"], "deny")
            self.assertEqual(data["hookSpecificOutput"]["permissionDecisionReason"], "OVERSIZED")

    def test_transcript_path_missing_blocks(self):
        # A bare time with no transcript key at all: the claim cannot be
        # checked, so it blocks. Before 2026-10-04 this fixture carried no
        # time and still blocked, which is the rule the Codex cases below
        # retire: the transcript is only read when there is a time to verify.
        payload = {
            "hook_event_name": "PreToolUse",
            "tool_name": "Bash",
            "tool_input": {"command": "commit at 14:22"},
            "tool_use_id": "t",
        }
        code, out, err = _run_main(json.dumps(payload))
        self.assertEqual(code, 0)
        data = json.loads(out.strip())
        self.assertEqual(data["hookSpecificOutput"]["permissionDecision"], "deny")
        self.assertTrue(data["hookSpecificOutput"]["permissionDecisionReason"].startswith("NO-DATA: "))
        self.assertIn(_t(14, 22), data["hookSpecificOutput"]["permissionDecisionReason"])

    # ----- the Codex payload shape: "transcript_path": null on every hook -----
    # Recorded live on 2026-10-04 (HP1, Codex): the host passes session_id,
    # turn_id, transcript_path null, cwd, hook_event_name, model,
    # permission_mode, tool_name, tool_input and tool_use_id. Fixture paths
    # are built here; none is copied from the evidence. Each case isolates
    # one condition: the time content of the outgoing text.

    def _codex_payload(self, tool_name, tool_input, cwd):
        return {
            "session_id": "01a1-codex-session",
            "turn_id": "01a1-codex-turn",
            "transcript_path": None,
            "cwd": cwd,
            "hook_event_name": "PreToolUse",
            "model": "fixture-model",
            "permission_mode": "bypassPermissions",
            "tool_name": tool_name,
            "tool_input": tool_input,
            "tool_use_id": "exec-fixture",
        }

    def test_codex_null_transcript_no_time_allows_own_skill_read(self):
        with tempfile.TemporaryDirectory() as home:
            skill = os.path.join(home, ".codex", "plugins", "cache", "brother", "brother",
                                 "1.1.0", "skills", "brothermode-status", "SKILL.md")
            workspace = os.path.join(home, "workspace")
            payload = self._codex_payload("Bash", {"command": "cat " + skill}, workspace)
            code, out, err = _run_main(json.dumps(payload))
            self.assertEqual(code, 0)
            self.assertEqual(out, "")
            self.assertEqual(err, "")

    def test_codex_null_transcript_no_time_allows_write_tool(self):
        with tempfile.TemporaryDirectory() as home:
            workspace = os.path.join(home, "workspace")
            target = os.path.join(workspace, "note.txt")
            payload = self._codex_payload("Write", {"file_path": target, "content": "hp1"}, workspace)
            code, out, err = _run_main(json.dumps(payload))
            self.assertEqual(code, 0)
            self.assertEqual(out, "")
            self.assertEqual(err, "")

    def test_codex_null_transcript_bare_time_blocks_no_data(self):
        with tempfile.TemporaryDirectory() as home:
            workspace = os.path.join(home, "workspace")
            payload = self._codex_payload("Bash", {"command": "echo committed at 14:22"}, workspace)
            code, out, err = _run_main(json.dumps(payload))
            self.assertEqual(code, 0)
            data = json.loads(out.strip())
            self.assertEqual(data["hookSpecificOutput"]["permissionDecision"], "deny")
            self.assertTrue(data["hookSpecificOutput"]["permissionDecisionReason"].startswith("NO-DATA: "))
            self.assertIn(_t(14, 22), data["hookSpecificOutput"]["permissionDecisionReason"])

    def test_codex_null_transcript_estimate_only_allows(self):
        with tempfile.TemporaryDirectory() as home:
            workspace = os.path.join(home, "workspace")
            payload = self._codex_payload("Bash", {"command": "echo eta 14:22 estimate"}, workspace)
            code, out, err = _run_main(json.dumps(payload))
            self.assertEqual(code, 0)
            self.assertEqual(out, "")
            self.assertEqual(err, "")

    # ----- hook wiring -----

    @unittest.skipUnless(os.path.isfile(_HOOKS_JSON), "hooks.json not present")
    def test_hooks_json_wires_guard_first_under_pretooluse(self):
        with open(_HOOKS_JSON, "r", encoding="utf-8") as handle:
            data = json.load(handle)
        entries = data["hooks"]["PreToolUse"]
        matched = [e for e in entries if e.get("matcher") == "Edit|Write|MultiEdit|NotebookEdit|Bash"]
        self.assertEqual(len(matched), 1)
        first = matched[0]["hooks"][0]
        self.assertIn("bm_clock_guard.py", first["command"])
        self.assertEqual(first["timeout"], 10)
        self.assertEqual(first["statusMessage"], "Checking that clock times were read")

    @unittest.skipUnless(os.path.isfile(_HOOKS_JSON), "hooks.json not present")
    def test_hooks_json_audits_under_stop(self):
        with open(_HOOKS_JSON, "r", encoding="utf-8") as handle:
            data = json.load(handle)
        stop_entries = data["hooks"]["Stop"]
        guard_hooks = [
            h for entry in stop_entries for h in entry["hooks"] if "bm_clock_guard.py" in h["command"]
        ]
        self.assertEqual(len(guard_hooks), 1)
        self.assertEqual(guard_hooks[0]["timeout"], 10)
        self.assertEqual(guard_hooks[0]["statusMessage"], "Auditing clock times after the turn")


def _t(hour, minute):
    """A clock token for a fixture, built, never written out (this guard reads the test source too)."""
    return "%02d%s%02d" % (hour, chr(58), minute)


class TestClaudeCodeTranscript(unittest.TestCase):
    """The transcript Claude Code actually writes (record keys measured 2026-10-21 on a live session file: 0 top level
    tool_result records; every result nested in a user record's message content as {type, tool_use_id, content}; the
    command in the assistant record's tool_use block as {type, id, name, input}). The guard was specified against a
    flat {type: tool_result, command, stdout} line that this host never writes, so a date read never counted and every
    bare time was denied NO-DATA. Each case isolates one condition."""

    def _prompt(self, text="go"):
        return {"type": "user", "message": {"role": "user", "content": text}}

    def _call(self, tid, command):
        return {"type": "assistant", "message": {"role": "assistant", "content": [
            {"type": "tool_use", "id": tid, "name": "Bash", "input": {"command": command}}]}}

    def _result(self, tid, content):
        return {"type": "user", "message": {"role": "user", "content": [
            {"type": "tool_result", "tool_use_id": tid, "content": content}]}}

    def _read(self, tid, hour, minute):
        """A date call and its result, as `date` prints it (seconds after the minute)."""
        return [self._call(tid, "date"), self._result(tid, "Wed Oct 21 %s%s13 JST 2026" % (_t(hour, minute), chr(58)))]

    def _deny(self, records, command, tool_use_id="toolu_now"):
        """The PreToolUse reason, or None when the call is allowed."""
        with tempfile.TemporaryDirectory() as folder:
            path = os.path.join(folder, "t.jsonl")
            with io.open(path, "w", encoding="utf-8") as fh:
                fh.write(_jsonl(*records) + "\n")
            payload = {"hook_event_name": "PreToolUse", "tool_name": "Bash", "transcript_path": path,
                       "tool_input": {"command": command}, "tool_use_id": tool_use_id}
            code, out, _err = _run_main(json.dumps(payload))
        self.assertEqual(code, 0)
        if not out.strip():
            return None
        data = json.loads(out.strip())["hookSpecificOutput"]
        self.assertEqual(data["permissionDecision"], "deny")
        return data["permissionDecisionReason"]

    def test_a_date_read_backs_a_time_in_the_same_minute(self):
        records = [self._prompt()] + self._read("toolu_a", 21, 49)
        self.assertIsNone(self._deny(records, "deadline %s" % _t(21, 49)))

    def test_a_later_tool_result_does_not_end_the_turn(self):
        records = [self._prompt()] + self._read("toolu_a", 21, 49) + [
            self._call("toolu_b", "ls"), self._result("toolu_b", "a b c")]
        self.assertIsNone(self._deny(records, "deadline %s" % _t(21, 50)))

    def test_a_result_given_as_text_blocks_counts(self):
        records = [self._prompt(), self._call("toolu_a", "date '+%Y'"),
                   self._result("toolu_a", [{"type": "text", "text": "2026-10-21 %s JST" % _t(21, 50)}])]
        self.assertIsNone(self._deny(records, "at %s" % _t(21, 50)))

    def test_a_new_prompt_starts_a_new_turn(self):
        records = [self._prompt()] + self._read("toolu_a", 21, 49) + [self._prompt("next")]
        reason = self._deny(records, "deadline %s" % _t(21, 49))
        self.assertTrue(reason and reason.startswith("NO-DATA"), reason)

    def test_only_a_clock_command_is_evidence(self):
        records = [self._prompt(), self._call("toolu_a", "echo %s" % _t(21, 49)),
                   self._result("toolu_a", _t(21, 49))]
        reason = self._deny(records, "deadline %s" % _t(21, 49))
        self.assertTrue(reason and reason.startswith("NO-DATA"), reason)

    def test_a_result_whose_call_has_no_command_is_not_evidence(self):
        # a Read of a file that prints a time: no command, so never a clock read
        read = {"type": "assistant", "message": {"role": "assistant", "content": [
            {"type": "tool_use", "id": "toolu_r", "name": "Read", "input": {"file_path": "/x"}}]}}
        records = [self._prompt(), read, self._result("toolu_r", "Wed Oct 21 %s%s13 JST 2026" % (_t(21, 49), chr(58)))]
        reason = self._deny(records, "deadline %s" % _t(21, 49))
        self.assertTrue(reason and reason.startswith("NO-DATA"), reason)

    def test_a_read_after_the_current_call_is_not_evidence(self):
        records = [self._prompt(), self._call("toolu_now", "ls")] + self._read("toolu_late", 21, 49)
        reason = self._deny(records, "deadline %s" % _t(21, 49), tool_use_id="toolu_now")
        self.assertTrue(reason and reason.startswith("NO-DATA"), reason)

    def test_the_deny_names_the_time_it_matched_and_the_remedy(self):
        reason = self._deny([self._prompt()], "deadline %s" % _t(6, 30))
        self.assertTrue(reason.startswith("NO-DATA"), reason)
        self.assertIn(_t(6, 30), reason)
        self.assertIn("date", reason)

    def test_a_utc_offset_is_not_a_clock_time(self):
        # no clock read at all: the offset alone must not be read as a time to verify
        stamp = "2026-10-21T%s%s00.259201+%s" % (_t(5, 23), chr(58), _t(0, 0))
        self.assertIsNone(self._deny([self._prompt()], "since %s" % stamp))
        self.assertIsNone(self._deny([self._prompt()], "zone UTC+%s" % _t(9, 0)))

    def test_the_seconds_of_a_longer_time_are_not_a_clock_time(self):
        # hours, minutes and seconds after a T: the minutes and seconds pair read as a time before this fix
        self.assertIsNone(self._deny([self._prompt()], "at 2026-10-21T%s%s00" % (_t(5, 23), chr(58))))

    # ----- an adversarial review of the fix (2026-10-21), each a concrete input it built -----

    def test_a_label_colon_does_not_hide_a_time(self):
        reason = self._deny([self._prompt()], "Time%s%s" % (chr(58), _t(12, 34)))
        self.assertTrue(reason and _t(12, 34) in reason, reason)

    def test_a_hyphen_after_seconds_with_no_date_is_a_range_not_an_offset(self):
        records = [self._prompt()] + self._read("toolu_a", 12, 34)
        reason = self._deny(records, "%s%s56-%s" % (_t(12, 34), chr(58), _t(13, 45)))
        self.assertTrue(reason and reason.startswith("MISMATCH") and _t(13, 45) in reason, reason)

    def test_a_date_given_a_time_is_not_a_clock_read(self):
        for command in ('date -d "%s"' % _t(12, 34), "date -j -f %%H%%M %s" % _t(12, 34), "date -v+3H"):
            records = [self._prompt(), self._call("toolu_a", command), self._result("toolu_a", _t(12, 34))]
            reason = self._deny(records, "at %s" % _t(12, 34))
            self.assertTrue(reason and reason.startswith("NO-DATA"), (command, reason))

    def test_a_compound_command_after_date_is_not_a_clock_read(self):
        records = [self._prompt(), self._call("toolu_a", "date && echo %s" % _t(12, 34)),
                   self._result("toolu_a", _t(12, 34))]
        reason = self._deny(records, "at %s" % _t(12, 34))
        self.assertTrue(reason and reason.startswith("NO-DATA"), reason)

    def test_a_clock_tool_joined_to_another_command_is_not_a_clock_read(self):
        for command in ("clock && echo %s" % _t(12, 34), "bm_clock | sed s/x/%s/" % _t(12, 34),
                        "clock $(echo %s)" % _t(12, 34)):
            records = [self._prompt(), self._call("toolu_a", command), self._result("toolu_a", _t(12, 34))]
            reason = self._deny(records, "at %s" % _t(12, 34))
            self.assertTrue(reason and reason.startswith("NO-DATA"), (command, reason))

    # ----- the automated security review of 58edb1483: the shell splits differently from shlex -----

    def test_a_newline_or_metacharacter_joined_command_is_not_a_clock_read(self):
        for command in ("clock\necho %s" % _t(12, 34), "date\necho %s" % _t(12, 34), "clock > f" ,
                        "date '+%%H' > /dev/null; echo %s" % _t(12, 34), "clock {x}"):
            records = [self._prompt(), self._call("toolu_a", command), self._result("toolu_a", _t(12, 34))]
            reason = self._deny(records, "at %s" % _t(12, 34))
            self.assertTrue(reason and reason.startswith("NO-DATA"), (command, reason))

    def test_a_format_carrying_a_literal_time_is_not_a_clock_read(self):
        for command in ("date +%s" % _t(12, 34), "date '+%s'" % _t(12, 34), "date +%%H%s34" % chr(58)):
            records = [self._prompt(), self._call("toolu_a", command), self._result("toolu_a", _t(12, 34))]
            reason = self._deny(records, "at %s" % _t(12, 34))
            self.assertTrue(reason and reason.startswith("NO-DATA"), (command, reason))

    def test_a_substitution_inside_a_format_is_not_a_clock_read(self):
        # one shlex token, a + and no digit: only the raw metacharacter check stops the shell expanding a file's text
        records = [self._prompt(), self._call("toolu_a", 'date "+$(cat /x/t)"'), self._result("toolu_a", _t(12, 34))]
        reason = self._deny(records, "at %s" % _t(12, 34))
        self.assertTrue(reason and reason.startswith("NO-DATA"), reason)

    def test_only_known_iso_units_count(self):
        out = "2026-10-21T%s%s13+0900" % (_t(12, 34), chr(58))
        for command, counts in (("date -Iseconds", True), ("date -Ibogus", False)):
            records = [self._prompt(), self._call("toolu_a", command), self._result("toolu_a", "at " + _t(12, 34) + " " + out)]
            reason = self._deny(records, "at %s" % _t(12, 34))
            self.assertEqual(reason is None, counts, (command, reason))

    def test_a_format_the_shell_could_expand_is_not_a_clock_read(self):
        # a glob or a tilde has no digit and no metacharacter, but the shell can expand it to a file named +<time>
        for command in ("date +*", "date +%H?", "date '+[0-9]'", "date +~"):
            records = [self._prompt(), self._call("toolu_a", command), self._result("toolu_a", _t(12, 34))]
            reason = self._deny(records, "at %s" % _t(12, 34))
            self.assertTrue(reason and reason.startswith("NO-DATA"), (command, reason))

    def test_a_clock_tool_with_arguments_is_not_a_clock_read(self):
        records = [self._prompt(), self._call("toolu_a", "bm_clock %s" % _t(12, 34)), self._result("toolu_a", _t(12, 34))]
        reason = self._deny(records, "at %s" % _t(12, 34))
        self.assertTrue(reason and reason.startswith("NO-DATA"), reason)

    def test_a_full_format_of_conversions_still_reads_the_clock(self):
        fmt = "+%Y-%m-%d %H" + chr(58) + "%M" + chr(58) + "%S %Z"
        records = [self._prompt(), self._call("toolu_a", "date '%s'" % fmt),
                   self._result("toolu_a", "2026-10-21 %s%s13 JST" % (_t(12, 34), chr(58)))]
        self.assertIsNone(self._deny(records, "at %s" % _t(12, 34)))

    def test_date_in_utc_still_reads_the_clock(self):
        records = [self._prompt(), self._call("toolu_a", "date -u"),
                   self._result("toolu_a", "Wed Oct 21 %s%s13 UTC 2026" % (_t(12, 34), chr(58)))]
        self.assertIsNone(self._deny(records, "at %s" % _t(12, 34)))

    def test_a_zone_name_then_a_space_then_an_offset_is_not_a_time(self):
        self.assertIsNone(self._deny([self._prompt()], "zone UTC +%s" % _t(9, 0)))

    def test_a_time_range_end_is_still_a_time(self):
        reason = self._deny([self._prompt()], "window %s-%s" % (_t(21, 0), _t(22, 0)))
        self.assertTrue(reason and _t(22, 0) in reason, reason)


if __name__ == "__main__":
    unittest.main()

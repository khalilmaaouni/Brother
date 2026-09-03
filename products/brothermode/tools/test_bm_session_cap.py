#!/usr/bin/env python3
"""Contract tests for tools/bm_session_cap.py, the machine-wide spawn cap.

WHY THIS FILE EXISTS
  On 2026-08-08 forty sessions ran against this project in ten hours and
  fifteen of them were live at once, against a cap of nine that existed
  only as a sentence in SKILL.md. A sentence is not a control. The founder
  ruled on 2026-08-09 that the cap becomes mechanical.

  These tests are written RED first, before tools/bm_session_cap.py holds
  any of it, and they pin the three properties that decide whether the cap
  is worth having at all:

  1. IT DENIES. Over the cap, a `claude -p` spawn is refused with the
     PreToolUse deny shape Claude Code actually honours, and the reason
     names the count and the cap so a human can act on it.
  2. IT IS NARROW. It gates SESSION SPAWNS and nothing else. An ordinary
     `git status`, a test run, an editor call, all pass untouched however
     many sessions are live, because a cap that interferes with normal
     work is a cap that gets uninstalled within a day.
  3. IT FAILS OPEN ON ITS OWN FAILURE. If the counter cannot read the
     session directory, it warns and ALLOWS. A miscounting brake that
     bricks a founder's machine is worse than the problem it solves. It
     does NOT fail open when the count succeeds and says the cap is hit:
     that is the whole point of it.
"""

import io
import importlib.util
import json
import os
import sys
import tempfile
import time
import unittest
import unittest.mock

HERE = os.path.dirname(os.path.abspath(__file__))


def _load(path, name):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class CapCase(unittest.TestCase):

    def setUp(self):
        self.cap = _load(os.path.join(HERE, "bm_session_cap.py"),
                         "bm_session_cap")
        self.tmp = tempfile.TemporaryDirectory()
        self.projects = os.path.join(self.tmp.name, "projects")
        os.makedirs(self.projects)
        self.addCleanup(self.tmp.cleanup)

    def make_sessions(self, count, age_seconds=0.0, project="proj-a"):
        """`count` session transcripts under one project directory, each
        stamped `age_seconds` old. Real Claude Code writes one .jsonl per
        session and touches it on every turn, which is the signal this
        counter reads."""
        pdir = os.path.join(self.projects, project)
        if not os.path.isdir(pdir):
            os.makedirs(pdir)
        made = []
        for i in range(count):
            path = os.path.join(pdir, "sess-%s-%d.jsonl" % (project, i))
            with io.open(path, "w", encoding="utf-8") as fh:
                fh.write('{"type":"user"}\n')
            stamp = time.time() - age_seconds
            os.utime(path, (stamp, stamp))
            made.append(path)
        return made

    def decide(self, command, tool_name="Bash"):
        """The hook's decision for one tool call, as the dict it would
        print, or None when it says nothing (which is how a PreToolUse
        hook allows)."""
        return self.cap.decide({"tool_name": tool_name,
                                "tool_input": {"command": command}},
                               projects_root=self.projects)


class TestItDeniesOverTheCap(CapCase):

    def test_a_session_spawn_over_the_cap_is_denied(self):
        self.make_sessions(6)
        with unittest.mock.patch.dict(
                os.environ, {self.cap.CAP_ENV: "4"}):
            decision = self.decide("nohup claude -p 'do the thing' &")
        self.assertIsNotNone(
            decision, "six live sessions against a cap of four must deny")
        self.assertEqual(
            decision["hookSpecificOutput"]["permissionDecision"], "deny")

    def test_the_reason_names_the_count_and_the_cap(self):
        """A refusal a human cannot act on is a refusal they will disable."""
        self.make_sessions(6)
        with unittest.mock.patch.dict(os.environ, {self.cap.CAP_ENV: "4"}):
            decision = self.decide("claude -p 'go'")
        reason = decision["hookSpecificOutput"]["permissionDecisionReason"]
        self.assertIn("6", reason)
        self.assertIn("4", reason)

    def test_exactly_at_the_cap_denies(self):
        """At the cap means the next one would exceed it."""
        self.make_sessions(4)
        with unittest.mock.patch.dict(os.environ, {self.cap.CAP_ENV: "4"}):
            self.assertIsNotNone(self.decide("claude -p 'go'"))

    def test_under_the_cap_allows(self):
        self.make_sessions(2)
        with unittest.mock.patch.dict(os.environ, {self.cap.CAP_ENV: "4"}):
            self.assertIsNone(self.decide("claude -p 'go'"))

    def test_stale_transcripts_do_not_count_as_live_sessions(self):
        """A session that has not written a turn in an hour is finished.
        Counting it would ratchet the cap down to zero over a week and
        lock the founder out of their own machine."""
        self.make_sessions(10, age_seconds=7200)
        with unittest.mock.patch.dict(os.environ, {self.cap.CAP_ENV: "4"}):
            self.assertIsNone(self.decide("claude -p 'go'"))

    def test_sessions_are_counted_across_every_project(self):
        """The 2026-08-08 fleet spanned two project directories. A per
        project count would have seen nine and nine, never eighteen."""
        self.make_sessions(3, project="proj-a")
        self.make_sessions(3, project="proj-b")
        with unittest.mock.patch.dict(os.environ, {self.cap.CAP_ENV: "5"}):
            self.assertIsNotNone(self.decide("claude -p 'go'"))


class TestItIsNarrow(CapCase):
    """The cap gates session spawns. Everything else runs untouched, at any
    session count, forever."""

    ORDINARY = ("git status",
                "python3 tools/test_all.py",
                "ls -la",
                "grep -rn claude tools/",
                "echo 'claude -p is mentioned here but nothing runs'",
                "cat docs/README.md")

    def test_ordinary_commands_pass_at_any_session_count(self):
        self.make_sessions(50)
        with unittest.mock.patch.dict(os.environ, {self.cap.CAP_ENV: "1"}):
            for command in self.ORDINARY:
                self.assertIsNone(
                    self.decide(command),
                    "the cap interfered with an ordinary command: %r"
                    % command)

    def test_a_non_bash_tool_is_never_gated(self):
        self.make_sessions(50)
        with unittest.mock.patch.dict(os.environ, {self.cap.CAP_ENV: "1"}):
            self.assertIsNone(self.decide("claude -p 'go'",
                                          tool_name="Read"))

    def test_the_print_long_form_is_recognised(self):
        self.make_sessions(9)
        with unittest.mock.patch.dict(os.environ, {self.cap.CAP_ENV: "4"}):
            self.assertIsNotNone(self.decide("claude --print 'go'"))

    def test_a_spawn_hidden_after_a_shell_separator_is_recognised(self):
        """`cd /repo && nohup claude -p ...` is the shape the relay itself
        prints. A matcher anchored at the start of the line would miss
        every real launch this cap exists to stop."""
        self.make_sessions(9)
        with unittest.mock.patch.dict(os.environ, {self.cap.CAP_ENV: "4"}):
            self.assertIsNotNone(
                self.decide("cd /repo && nohup claude -p 'go' > log 2>&1 &"))


class TestItFailsOpenOnItsOwnFailure(CapCase):

    def test_an_unreadable_projects_directory_allows(self):
        with unittest.mock.patch.dict(os.environ, {self.cap.CAP_ENV: "1"}):
            decision = self.cap.decide(
                {"tool_name": "Bash",
                 "tool_input": {"command": "claude -p 'go'"}},
                projects_root=os.path.join(self.tmp.name, "does-not-exist"))
        self.assertIsNone(
            decision,
            "a counter that cannot count must allow and say so, never deny")

    def test_a_junk_cap_falls_back_to_the_default_rather_than_crashing(self):
        self.make_sessions(2)
        with unittest.mock.patch.dict(os.environ, {self.cap.CAP_ENV: "lots"}):
            self.assertIsNone(self.decide("claude -p 'go'"))

    def test_a_malformed_envelope_allows(self):
        self.assertIsNone(self.cap.decide({}, projects_root=self.projects))
        self.assertIsNone(self.cap.decide(
            {"tool_name": "Bash"}, projects_root=self.projects))


class TestTheDenyShapeIsTheContract(CapCase):
    """Pinned against the shape tools/bm_fence_hook.py already emits, and
    which Claude Code honours. A deny in any other shape is ignored by the
    harness, which would make this whole file theatre."""

    def test_the_payload_has_the_three_required_fields(self):
        payload = self.cap.deny_payload("because")
        block = payload["hookSpecificOutput"]
        self.assertEqual(block["hookEventName"], "PreToolUse")
        self.assertEqual(block["permissionDecision"], "deny")
        self.assertEqual(block["permissionDecisionReason"], "because")

    def test_the_hook_prints_one_json_object_and_exits_zero(self):
        """Exit 0 with a deny body: the fence hook's own convention, and
        the one the harness reads. A non-zero exit is read as a broken
        hook, not as a refusal."""
        self.make_sessions(9)
        envelope = json.dumps({"tool_name": "Bash",
                               "tool_input": {"command": "claude -p 'go'"}})
        out = io.StringIO()
        real_in, real_out = sys.stdin, sys.stdout
        sys.stdin, sys.stdout = io.StringIO(envelope), out
        try:
            with unittest.mock.patch.dict(
                    os.environ, {self.cap.CAP_ENV: "4",
                                 self.cap.PROJECTS_ENV: self.projects}):
                code = self.cap.main([])
        finally:
            sys.stdin, sys.stdout = real_in, real_out
        self.assertEqual(code, 0)
        body = json.loads(out.getvalue())
        self.assertEqual(
            body["hookSpecificOutput"]["permissionDecision"], "deny")

    def test_an_allowed_call_prints_nothing_at_all(self):
        self.make_sessions(1)
        envelope = json.dumps({"tool_name": "Bash",
                               "tool_input": {"command": "git status"}})
        out = io.StringIO()
        real_in, real_out = sys.stdin, sys.stdout
        sys.stdin, sys.stdout = io.StringIO(envelope), out
        try:
            with unittest.mock.patch.dict(
                    os.environ, {self.cap.CAP_ENV: "4",
                                 self.cap.PROJECTS_ENV: self.projects}):
                code = self.cap.main([])
        finally:
            sys.stdin, sys.stdout = real_in, real_out
        self.assertEqual(code, 0)
        self.assertEqual(out.getvalue().strip(), "")


class TestReviewFindingsOfPr36(CapCase):
    """The 2026-08-09 adversarial review's findings on the cap module, each
    pinned red-first. Finding numbers refer to that review verdict."""

    def test_finding2_an_absolute_path_to_claude_is_still_gated(self):
        self.make_sessions(9)
        with unittest.mock.patch.dict(os.environ, {self.cap.CAP_ENV: "4"}):
            self.assertIsNotNone(
                self.decide("/usr/local/bin/claude -p 'go'"),
                "a path-prefixed launch evaded the cap")

    def test_finding2_flags_between_claude_and_dash_p_are_still_gated(self):
        self.make_sessions(9)
        with unittest.mock.patch.dict(os.environ, {self.cap.CAP_ENV: "4"}):
            self.assertIsNotNone(
                self.decide(
                    "claude --dangerously-skip-permissions -p 'go'"),
                "a flag between claude and -p evaded the cap")

    def test_finding2_a_word_containing_claude_is_not_gated(self):
        """Widening must not swallow innocents: barnacled, my-claude-notes.md
        as an argument to cat, and so on."""
        self.make_sessions(9)
        with unittest.mock.patch.dict(os.environ, {self.cap.CAP_ENV: "1"}):
            self.assertIsNone(self.decide("cat notes-about-claude -p.txt"))
            self.assertIsNone(self.decide("myclaude -p 'go'"))

    def test_reverify_a_flag_with_a_space_separated_value_is_still_gated(self):
        """The re-verification executed this bypass: claude
        --permission-mode acceptEdits -p, our own shipped flag reordered,
        passed the matcher because a valued flag broke the flag-run."""
        self.make_sessions(9)
        with unittest.mock.patch.dict(os.environ, {self.cap.CAP_ENV: "4"}):
            self.assertIsNotNone(
                self.decide("claude --permission-mode acceptEdits -p 'go'"),
                "a space-valued flag between claude and -p evaded the cap")
            self.assertIsNotNone(
                self.decide("nohup claude --model opus --permission-mode "
                            "bypassPermissions -p 'go' &"))

    def test_reverify_prose_after_claude_is_still_not_gated(self):
        """Widening for valued flags must not swallow sentences: a bare
        word after claude is only a flag VALUE when a dash-led flag came
        first."""
        self.make_sessions(9)
        with unittest.mock.patch.dict(os.environ, {self.cap.CAP_ENV: "1"}):
            self.assertIsNone(self.decide("echo claude is nice -p today"))
            self.assertIsNone(self.decide("grep claude notes.txt"))

    def test_finding4_the_launching_session_does_not_count_against_itself(self):
        """cap minus one headroom deadlocked a lone session at cap 1. The
        envelope carries session_id; the counter must exclude that
        session's own transcript."""
        self.make_sessions(3)
        pdir = os.path.join(self.projects, "proj-self")
        os.makedirs(pdir)
        own = os.path.join(pdir, "my-session-id.jsonl")
        with io.open(own, "w", encoding="utf-8") as fh:
            fh.write('{"type":"user"}\n')
        with unittest.mock.patch.dict(os.environ, {self.cap.CAP_ENV: "4"}):
            decision = self.cap.decide(
                {"tool_name": "Bash", "session_id": "my-session-id",
                 "tool_input": {"command": "claude -p 'go'"}},
                projects_root=self.projects)
        self.assertIsNone(
            decision,
            "3 others plus itself against cap 4 must allow: the launcher's "
            "own transcript counted against it")

    def test_finding5_the_hook_is_actually_wired_in_the_manifest(self):
        """The 16 module tests all pass with the hook deleted from
        hooks/hooks.json. This one does not."""
        manifest = os.path.join(HERE, os.pardir, "hooks", "hooks.json")
        with io.open(manifest, encoding="utf-8") as fh:
            data = json.load(fh)
        wired = json.dumps(data.get("hooks", {}).get("PreToolUse", []))
        self.assertIn("bm_session_cap.py", wired,
                      "the cap is not wired in hooks/hooks.json, so every "
                      "module test here is testing a hook that never runs")


class TestTheFileEscapeE2(CapCase):
    """E2.1 (2026-08-31): an uncoached stranger hit the cap, read the
    refusal's advice to set BROTHERMODE_SESSION_CAP=N "in the environment
    of the session doing the launching", and could not act on it, because
    that environment belongs to the process that started `claude`, not to
    anything a running session can change afterward. These tests drive the
    fix from that exact position: a fresh process that only has a
    filesystem to write to, never the hook's own environment."""

    def decide_as_a_user(self, command, tmp_home, session_id="me"):
        """Simulates the position a person occupies while already inside a
        session: no ability to set the hook's environment (CAP_ENV is
        absent, exactly as it would be for anyone but the launcher), only
        a file they can write under their own home. This is a fresh call
        to decide(), the same as the harness makes on every new tool call,
        so a file written a moment ago is picked up honestly rather than
        cached."""
        env = {"HOME": tmp_home}
        return self.cap.decide(
            {"tool_name": "Bash", "session_id": session_id,
             "tool_input": {"command": command}},
            projects_root=self.projects, env=env)

    def test_the_named_escape_is_a_file_a_user_can_write(self):
        """The old advice named an env var unreachable from inside a
        session. The new escape must actually move the outcome when driven
        the way a user would drive it: write a number to a file, nothing
        else, then try again."""
        self.make_sessions(6)
        with tempfile.TemporaryDirectory() as home:
            # At the default cap, six live sessions refuse the seventh.
            first = self.decide_as_a_user("claude -p 'go'", home)
            self.assertIsNotNone(
                first, "six sessions against the default cap must deny")
            reason = first["hookSpecificOutput"]["permissionDecisionReason"]
            # The reason must name a file path, not just the unreachable
            # env var, or a stranger has nothing new to act on.
            self.assertIn(self.cap.CAP_FILE_DEFAULT.replace("~", ""),
                          reason.replace(os.path.expanduser("~"), ""))
            # Drive it: write the escape exactly as a user would with a
            # plain shell redirect, from inside their own home.
            override = os.path.join(home, ".claude", "bm_session_cap.override")
            os.makedirs(os.path.dirname(override))
            with io.open(override, "w", encoding="utf-8") as fh:
                fh.write("10\n")
            second = self.decide_as_a_user("claude -p 'go'", home)
            self.assertIsNone(
                second,
                "the file escape did not change the outcome: it is still "
                "not reachable from where a user actually stands")

    def test_env_var_still_wins_over_the_file_when_both_are_set(self):
        """CAP_ENV remains the real lever for whoever controls the launch;
        the file must never silently override it."""
        self.make_sessions(6)
        with tempfile.TemporaryDirectory() as home:
            override = os.path.join(home, ".claude", "bm_session_cap.override")
            os.makedirs(os.path.dirname(override))
            with io.open(override, "w", encoding="utf-8") as fh:
                fh.write("10\n")
            env = {"HOME": home, self.cap.CAP_ENV: "1"}
            decision = self.cap.decide(
                {"tool_name": "Bash", "session_id": "me",
                 "tool_input": {"command": "claude -p 'go'"}},
                projects_root=self.projects, env=env)
            self.assertIsNotNone(
                decision,
                "CAP_ENV=1 must still deny even though the file says 10")

    def test_a_missing_override_file_falls_back_to_the_default_cap(self):
        """No file, no env var: behaves exactly as before this change."""
        self.make_sessions(2)
        with tempfile.TemporaryDirectory() as home:
            self.assertIsNone(self.decide_as_a_user("claude -p 'go'", home))

    def test_a_junk_override_file_falls_back_rather_than_crashing(self):
        self.make_sessions(2)
        with tempfile.TemporaryDirectory() as home:
            override = os.path.join(home, ".claude", "bm_session_cap.override")
            os.makedirs(os.path.dirname(override))
            with io.open(override, "w", encoding="utf-8") as fh:
                fh.write("not a number\n")
            self.assertIsNone(self.decide_as_a_user("claude -p 'go'", home))

    def test_cap_file_env_relocates_the_file_for_tests_and_installs(self):
        with tempfile.TemporaryDirectory() as somewhere:
            path = os.path.join(somewhere, "custom-override")
            with io.open(path, "w", encoding="utf-8") as fh:
                fh.write("2\n")
            self.assertEqual(
                self.cap.cap({self.cap.CAP_FILE_ENV: path}), 2)


if __name__ == "__main__":
    unittest.main(verbosity=2)

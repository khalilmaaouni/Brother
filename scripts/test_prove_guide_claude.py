#!/usr/bin/env python3
"""prove_guide_claude.py, driven both ways: the parser, and a full run
against a fake `claude` on PATH so the NO-DATA path (the guide's one
interactive step, `/brother`) is proven without network or a real model
call.

No test here touches the real ~/.claude: every full-run case sets HOME to a
temp directory before invoking the script (mirroring how the script itself
isolates HOME), and the fake `claude` on PATH never reads real plugin
state.
"""
import os
import subprocess
import sys
import tempfile
import textwrap
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import prove_guide_claude as PGC  # noqa: E402

#: A fake `claude` that answers the four plugin subcommands the guide uses,
#: with the exact needle text `claude plugin marketplace add`/`install`/
#: `update`/`uninstall` print for real (measured 2026-09-05), so a test
#: reading those needles would see the same shape a real run does. No
#: network, no real plugin cache.
FAKE_CLAUDE = textwrap.dedent("""\
    #!/bin/sh
    # The real `claude` binary creates ~/.claude on its very first
    # invocation (measured 2026-09-05); mirrored here so a guide command
    # that writes under ~/.claude right after an install step behaves the
    # same against this fake as it does against the real binary.
    mkdir -p "$HOME/.claude"
    case "$*" in
      *"marketplace add"*) echo "Successfully added marketplace: fake" ;;
      *"marketplace update"*) echo "Successfully updated marketplace: fake" ;;
      *"marketplace remove"*) echo "Successfully removed marketplace: fake" ;;
      *"install "*) echo "Successfully installed plugin: fake@fake" ;;
      *"uninstall "*) echo "Successfully uninstalled plugin: fake" ;;
      *"update "*) echo "fake is already at the latest version (9.9.9)." ;;
      *) echo "fake-claude: unrecognised args: $*" >&2; exit 1 ;;
    esac
    """)

#: A small fixture guide exercising every parser case: a runnable bash
#: block, a multi-line sample-output text block (skipped), a one-line slash
#: command (the NO-DATA case), a one-line non-slash text block (also
#: skipped -- proves the parser does not treat every one-liner as a
#: command), and a second bash block writing under HOME to prove isolation.
FIXTURE_GUIDE = textwrap.dedent("""\
    # Fixture guide

    ```bash
    claude plugin marketplace add fake/repo && claude plugin install fake@fake
    ```

    ```text
    Successfully added marketplace
    Successfully installed plugin
    ```

    ```text
    /brother
    ```

    ```text
    no unfinished run found
    ```

    ```bash
    printf 'marker\\n' > ~/.claude/prove-guide-test-marker
    ```
    """)


class ExtractCommandsTest(unittest.TestCase):
    """The parser: every fenced bash block is a command, a one-line slash
    text block is a command, everything else is sample output."""

    def test_pulls_bash_and_slash_only(self):
        commands = PGC.extract_commands(FIXTURE_GUIDE)
        self.assertEqual(
            [kind for kind, _ in commands],
            ["bash", "slash", "bash"])

    def test_bash_text_is_verbatim(self):
        commands = PGC.extract_commands(FIXTURE_GUIDE)
        self.assertEqual(
            commands[0][1],
            "claude plugin marketplace add fake/repo && "
            "claude plugin install fake@fake")

    def test_slash_text_is_verbatim(self):
        commands = PGC.extract_commands(FIXTURE_GUIDE)
        self.assertEqual(commands[1][1], "/brother")

    def test_multiline_and_non_slash_text_blocks_are_skipped(self):
        commands = PGC.extract_commands(FIXTURE_GUIDE)
        texts = [text for _, text in commands]
        self.assertNotIn("Successfully added marketplace\n"
                         "Successfully installed plugin", texts)
        self.assertNotIn("no unfinished run found", texts)

    def test_real_guide_has_no_unhandled_slash_lines(self):
        """The real guide's only one-line slash-starting text block is
        `/brother`. If a future edit adds a second one, this test names it
        rather than letting an untested command silently join the proof."""
        with open(PGC.GUIDE_PATH, encoding="utf-8") as fh:
            guide_text = fh.read()
        slashes = [text for kind, text in PGC.extract_commands(guide_text)
                  if kind == "slash"]
        self.assertEqual(slashes, ["/brother"])


class SectionSpliceTest(unittest.TestCase):
    """Regenerating the guide's own proof section is idempotent: a second
    run replaces the first section rather than appending a duplicate."""

    def test_splice_replaces_earlier_section(self):
        section_a = PGC.render_section(["cmd -> exit 0"], "v1.0.0")
        once = PGC.splice_section(FIXTURE_GUIDE, section_a)
        self.assertIn("cmd -> exit 0", once)

        section_b = PGC.render_section(["cmd -> exit 1"], "v1.0.1")
        twice = PGC.splice_section(once, section_b)
        self.assertIn("cmd -> exit 1", twice)
        self.assertNotIn("cmd -> exit 0", twice)
        self.assertEqual(twice.count("## Proven on a throwaway home"), 1)


class FullRunTest(unittest.TestCase):
    """main() against the fixture guide, with a fake `claude` on PATH so
    the whole flow runs with no network and no real model call."""

    def setUp(self):
        self.work = tempfile.mkdtemp(prefix="test-prove-guide-claude-")
        self.addCleanup(lambda: subprocess.run(
            ["rm", "-rf", self.work], check=False))

        fake_bin = os.path.join(self.work, "bin")
        os.makedirs(fake_bin)
        claude_path = os.path.join(fake_bin, "claude")
        with open(claude_path, "w", encoding="utf-8") as fh:
            fh.write(FAKE_CLAUDE)
        os.chmod(claude_path, 0o755)

        self.guide_path = os.path.join(self.work, "GUIDE.md")
        with open(self.guide_path, "w", encoding="utf-8") as fh:
            fh.write(FIXTURE_GUIDE)

        # Isolate HOME for the subprocess so the fixture's own
        # ~/.claude/prove-guide-test-marker write never touches the real
        # home, matching the rule this script itself follows.
        self.fake_home = os.path.join(self.work, "outer-home")
        os.makedirs(self.fake_home)
        self.env = dict(os.environ)
        self.env["HOME"] = self.fake_home
        self.env["PATH"] = fake_bin + os.pathsep + self.env.get("PATH", "")
        self.env.pop("CLAUDE_CONFIG_DIR", None)

    def _run(self, extra_args=()):
        return subprocess.run(
            [sys.executable, os.path.join(HERE, "prove_guide_claude.py"),
             "--guide", self.guide_path, "--no-write"] + list(extra_args),
            env=self.env, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
            timeout=60)

    def test_fake_claude_command_exits_zero(self):
        proc = self._run()
        out = proc.stdout.decode()
        self.assertEqual(proc.returncode, 0, out)
        self.assertIn(
            "claude plugin marketplace add fake/repo && claude plugin "
            "install fake@fake -> exit 0", out)

    def test_slash_command_is_nodata_never_a_pass(self):
        proc = self._run()
        out = proc.stdout.decode()
        self.assertIn("/brother -> NO-DATA", out)
        self.assertNotIn("/brother -> exit", out)

    def test_summary_line_counts_match(self):
        proc = self._run()
        out = proc.stdout.decode()
        self.assertIn("3 command(s), 2 exited 0, 1 NO-DATA", out)

    def test_no_write_leaves_guide_untouched(self):
        with open(self.guide_path, encoding="utf-8") as fh:
            before = fh.read()
        self._run()
        with open(self.guide_path, encoding="utf-8") as fh:
            after = fh.read()
        self.assertEqual(before, after)

    def test_isolated_home_write_never_touches_real_home(self):
        """The fixture's own bash block writes under the SUBPROCESS's HOME
        (self.fake_home), which the script isolates a second time into its
        own tempfile.mkdtemp() HOME. Neither the outer fake HOME nor the
        real one should ever see the marker file."""
        self._run()
        self.assertFalse(os.path.exists(
            os.path.join(self.fake_home, ".claude",
                        "prove-guide-test-marker")))
        real_marker = os.path.expanduser(
            "~/.claude/prove-guide-test-marker")
        self.assertFalse(os.path.exists(real_marker))


class NoClaudeOnPathTest(unittest.TestCase):
    """When `claude` is not on PATH at all, the plugin command fails with a
    real, honest exit code (shell 127, command not found) rather than a
    silent pass. NO-DATA in this script is reserved for the documented
    interactive step; a missing binary is a reported failure, never
    disguised as either."""

    def setUp(self):
        self.work = tempfile.mkdtemp(prefix="test-prove-guide-no-claude-")
        self.addCleanup(lambda: subprocess.run(
            ["rm", "-rf", self.work], check=False))
        self.guide_path = os.path.join(self.work, "GUIDE.md")
        with open(self.guide_path, "w", encoding="utf-8") as fh:
            fh.write(FIXTURE_GUIDE)
        empty_bin = os.path.join(self.work, "empty-bin")
        os.makedirs(empty_bin)
        self.fake_home = os.path.join(self.work, "outer-home")
        os.makedirs(self.fake_home)
        self.env = {"HOME": self.fake_home,
                   # A minimal PATH with no claude: real shell builtins
                   # (mkdir, printf) still resolve via /bin and /usr/bin.
                   "PATH": empty_bin + os.pathsep + "/bin:/usr/bin"}

    def test_missing_claude_reports_a_real_exit_code(self):
        proc = subprocess.run(
            [sys.executable, os.path.join(HERE, "prove_guide_claude.py"),
             "--guide", self.guide_path, "--no-write"],
            env=self.env, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
            timeout=60)
        out = proc.stdout.decode()
        self.assertIn("claude plugin marketplace add fake/repo && claude "
                     "plugin install fake@fake -> exit 127", out)
        self.assertNotIn(
            "claude plugin marketplace add fake/repo && claude plugin "
            "install fake@fake -> NO-DATA", out)


class CommandCwdTest(unittest.TestCase):
    """Where each kind of command runs, without needing a real tag clone."""

    def test_test_script_runs_in_tag_checkout(self):
        self.assertEqual(
            PGC.command_cwd("python3 scripts/test_receipt_door.py",
                            "/home", "/tag", "/target"),
            "/tag")

    def test_dot_brother_config_runs_in_target_repo(self):
        self.assertEqual(
            PGC.command_cwd("mkdir -p .brother && printf 'hooks: on\\n' "
                            "> .brother/config", "/home", "/tag", "/target"),
            "/target")

    def test_plugin_command_runs_in_home(self):
        self.assertEqual(
            PGC.command_cwd("claude plugin marketplace add x", "/home",
                            "/tag", "/target"),
            "/home")


if __name__ == "__main__":
    unittest.main()

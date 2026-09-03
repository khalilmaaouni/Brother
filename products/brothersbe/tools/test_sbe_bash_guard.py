#!/usr/bin/env python3
"""Regression tests for the BrotherSBE PreToolUse Bash write guard (BR-1014).

Run: python3 tools/test_sbe_bash_guard.py

Standard library only, matching the zero-dependency ethos of the tools. Kept in
its own file rather than folded into tools/test_sbe_fence_hook.py so the Bash
half of the write boundary can be run, and be seen to fail, on its own.

THREE PROPERTIES CARRY THIS FILE.

  IT REFUSES THE FOUR COMMANDS THE BLOCKER NAMES. `printf > CLAUDE.md`,
  `python3 -c open(...)`, `sed -i`, and `rm -f .sbe/tasks.json` are spec
  fixtures 1, 2, 3 and 5 and are asserted here verbatim, not paraphrased.

  IT FAILS OPEN EVERYWHERE ELSE, AND SAYS SO. A command it cannot tokenize, a
  payload it cannot parse, a registry it cannot read: every one of those
  ALLOWS and prints why. A guard in front of every Bash call that failed
  closed on its own bug would stop the operator from working at all, which is
  the same law tools/sbe_fence_hook.py is built on.

  IT NEVER CLAIMS A COMMAND IS READ ONLY. Spec 1.3 rule 8. The assertion is
  mechanical rather than a matter of taste: `test_no_allow_path_ever_claims_
  the_command_was_read_only` replays every allowed fixture in this file and
  fails if any of them puts a read-only claim on either channel.

CALIBRATION. Every assertion here was run RED against a deliberately broken
copy of the guard before it was run green against the real one. The two
mutations used, and the tests each one killed, are recorded in the patch note
for BR-1014; the mutations were (a) deleting the redirection branch in
`segment_candidates`, and (b) making `protected_family` return "" always.
"""
import importlib.util
import io
import json
import os
import subprocess
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
GUARD_PATH = os.path.join(HERE, "sbe_bash_write_guard.py")

_spec = importlib.util.spec_from_file_location("sbe_bash_write_guard", GUARD_PATH)
bg = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(bg)
sys.modules["sbe_bash_write_guard"] = bg

MY_SESSION = "session-1111-2222-3333"

#: Every field src/brothersbe/tasks.py requires of a task record. A registry
#: missing one is REFUSED by the loader, which would make this fixture a
#: fail-open test by accident instead of a scope test on purpose.
TASK_FIELDS = {
    "id": "t1", "agent": "writer-1", "role": "writer", "worktree": "",
    "ownedPaths": [], "readOnlyPaths": [], "baseCommit": "0" * 40,
    "expiry": "2099-01-01T00:00:00Z", "status": "open",
    "verifyCommand": "python3 -c pass", "evidenceId": "",
    "openedAt": "2026-01-01T00:00:00Z", "closedAt": "",
}


def write(path, text):
    with io.open(path, "w", encoding="utf-8") as f:
        f.write(text)


class GuardCase(unittest.TestCase):
    """A temp project carrying one of every protected family, and an
    environment put back exactly as it was found. The disable switch changes
    the guard's answer, so a test that leaked it would silently decide the
    verdict of the next test in the file."""

    ENV_KEYS = ("BROTHERSBE_BASH_GUARD_OFF", "BROTHERSBE_REGISTRIES")

    def setUp(self):
        self._saved = {k: os.environ.get(k) for k in self.ENV_KEYS}
        for k in self.ENV_KEYS:
            os.environ.pop(k, None)
        self._tmp = tempfile.TemporaryDirectory()
        self.root = os.path.realpath(self._tmp.name)
        for d in (".claude", "hooks", ".sbe", "tools", "src", "docs",
                  os.path.join("src", "brothersbe")):
            os.makedirs(os.path.join(self.root, d), exist_ok=True)
        write(os.path.join(self.root, "CLAUDE.md"), "authority\n")
        write(os.path.join(self.root, ".claude", "settings.json"), "{}\n")
        write(os.path.join(self.root, "hooks", "hooks.json"), "{}\n")
        write(os.path.join(self.root, "docs", "notes.md"), "ordinary\n")
        write(os.path.join(self.root, "src", "brothersbe", "evidence.py"), "x = 1\n")
        self.registry = os.path.join(self.root, ".sbe", "tasks.json")
        self.set_tasks([])
        # The allow path must not be decided by a fence registry this test
        # never wrote: an absent STATE.md makes the fence hook fail open,
        # which is the fence hook's business and not this file's.
        self.assertFalse(os.path.exists(os.path.join(self.root, "STATE.md")))

    def tearDown(self):
        for k, v in self._saved.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v
        self._tmp.cleanup()

    def set_tasks(self, tasks):
        write(self.registry, json.dumps({"schemaVersion": "1.0", "tasks": tasks}))

    def owning_task(self, *paths):
        rec = dict(TASK_FIELDS)
        rec["ownedPaths"] = list(paths)
        return rec

    def payload(self, command, session=MY_SESSION):
        return {"tool_name": "Bash",
                "tool_input": {"command": command},
                "session_id": session,
                "cwd": self.root,
                "project_dir": self.root}

    def run_guard(self, command, **kw):
        """Run the guard as a SUBPROCESS, the way Claude Code runs it, so the
        stdout/stderr split is tested on the real channels rather than on an
        in-process return value."""
        proc = subprocess.run(
            [sys.executable, GUARD_PATH],
            input=json.dumps(self.payload(command, **kw)),
            capture_output=True, text=True)
        return Run(proc.returncode, proc.stdout, proc.stderr, command)

    def run_guard_from(self, command, cwd):
        """Same as run_guard, except the Bash call's `cwd` is not this
        project's root, which is how "run from a subdirectory" is tested."""
        payload = {"tool_name": "Bash", "tool_input": {"command": command},
                   "session_id": MY_SESSION, "cwd": cwd, "project_dir": self.root}
        proc = subprocess.run(
            [sys.executable, GUARD_PATH], input=json.dumps(payload),
            capture_output=True, text=True)
        return Run(proc.returncode, proc.stdout, proc.stderr, command)

    def run_guard_rooted(self, command, cwd, project_dir):
        """Same as run_guard, except BOTH `cwd` and `project_dir` are given
        explicitly, which is how "a session rooted somewhere other than
        this fixture's own self.root" is tested (the one-repo product
        subtree cases need `project_dir` to be a product subtree, not
        self.root)."""
        payload = {"tool_name": "Bash", "tool_input": {"command": command},
                   "session_id": MY_SESSION, "cwd": cwd, "project_dir": project_dir}
        proc = subprocess.run(
            [sys.executable, GUARD_PATH], input=json.dumps(payload),
            capture_output=True, text=True)
        return Run(proc.returncode, proc.stdout, proc.stderr, command)


class Run(object):
    """One guard invocation. An object rather than a 3-tuple purely for
    readability at the call sites; nothing here is a check verdict."""

    def __init__(self, code, stdout, stderr, command):
        self.code = code
        self.stdout = stdout
        self.stderr = stderr
        self.command = command

    @property
    def denied(self):
        return bool(self.stdout.strip())

    @property
    def reason(self):
        if not self.denied:
            return ""
        data = json.loads(self.stdout)
        return data["hookSpecificOutput"]["permissionDecisionReason"]


#: Every command this file expects to be ALLOWED. Kept as data so the
#: read-only-claim sweep at the bottom can replay all of them without a second
#: hand-maintained list drifting away from the first.
ALLOWED_COMMANDS = (
    "ls -la",
    "git status --porcelain",
    "cat docs/notes.md",
    "grep '>' CLAUDE.md",
    "printf 'x' > docs/notes.md",
    "python3 -c \"print(open('CLAUDE.md').read())\"",
    "echo \"unterminated",
)


class TestTheFourNamedBypasses(GuardCase):
    """Spec fixtures 1, 2, 3 and 5, verbatim from the blocker document."""

    def test_fixture_1_printf_redirected_into_claude_md_is_refused(self):
        run = self.run_guard("printf '%s\\n' 'replacement' > CLAUDE.md")
        self.assertTrue(run.denied, "printf > CLAUDE.md was allowed; stderr: %s" % run.stderr)
        self.assertIn("CLAUDE.md", run.reason)
        self.assertIn("shell redirection", run.reason)
        self.assertEqual(run.code, 0, "the guard must exit 0 and deny through stdout")

    def test_fixture_2_a_python_one_liner_opening_a_protected_file_is_refused(self):
        run = self.run_guard(
            "python3 -c \"open('.claude/settings.json', 'w').write('...')\"")
        self.assertTrue(run.denied, "python3 -c open(...) was allowed; stderr: %s" % run.stderr)
        self.assertIn(".claude/settings.json", run.reason)

    def test_fixture_3_sed_in_place_against_a_protected_file_is_refused(self):
        run = self.run_guard("sed -i 's/old/new/' hooks/hooks.json")
        self.assertTrue(run.denied, "sed -i was allowed; stderr: %s" % run.stderr)
        self.assertIn("hooks/hooks.json", run.reason)

    def test_fixture_3b_sed_without_in_place_is_not_a_write(self):
        """The same command minus -i writes nothing, and refusing it would
        teach the operator that the guard cannot tell a read from a write."""
        run = self.run_guard("sed 's/old/new/' hooks/hooks.json")
        self.assertFalse(run.denied, "a read-only sed was refused: %s" % run.reason)

    def test_fixture_3c_a_clustered_in_place_flag_is_still_in_place(self):
        """Short flags cluster, so the in-place letter is not always first.

        Calibrated against the defect a hostile refuter found here: the test
        used to be flag.startswith("-i"), which reads `sed -i` and misses
        `perl -pi -e`, letting the same rewrite through under a different
        tool's spelling."""
        run = self.run_guard("perl -pi -e 's/old/new/' CLAUDE.md")
        self.assertTrue(run.denied, "perl -pi was allowed; stderr: %s" % run.stderr)
        self.assertIn("CLAUDE.md", run.reason)

    def test_fixture_3d_a_long_flag_that_merely_starts_like_in_place_is_not(self):
        """--include is not --in-place. The cluster rule must not swallow long
        flags, or the guard starts refusing reads and teaching people to work
        around it."""
        run = self.run_guard("grep --include='*.py' -r pattern hooks/")
        self.assertFalse(run.denied, "a read-only grep was refused: %s" % run.reason)

    def test_fixture_5_deleting_the_task_registry_is_refused(self):
        run = self.run_guard("rm -f .sbe/tasks.json")
        self.assertTrue(run.denied, "rm .sbe/tasks.json was allowed; stderr: %s" % run.stderr)
        self.assertIn(".sbe/tasks.json", run.reason)


class TestTheShapesTextParsingUsuallyMisses(GuardCase):

    def test_fixture_7_a_write_through_a_symlink_to_a_protected_target_is_refused(self):
        """Spec fixture 7. Nothing in the command text says CLAUDE.md: the
        refusal comes from canonicalizing the link, which is the fence hook's
        own `canonical_target` reused rather than reimplemented."""
        os.symlink(os.path.join(self.root, "CLAUDE.md"),
                   os.path.join(self.root, "innocent.txt"))
        run = self.run_guard("printf 'x' > innocent.txt")
        self.assertTrue(run.denied, "a symlinked write was allowed: %s" % run.stderr)
        self.assertIn("CLAUDE.md", run.reason)

    def test_a_shell_wrapped_write_is_refused_through_the_recursion(self):
        run = self.run_guard("sh -c 'printf x > CLAUDE.md'")
        self.assertTrue(run.denied, "sh -c wrapping hid the write: %s" % run.stderr)
        self.assertIn("CLAUDE.md", run.reason)

    def test_removing_a_whole_protected_directory_is_refused(self):
        """`rm -rf .claude` names no member of the family, only the directory
        that holds it. A membership-only comparison walks straight past it."""
        run = self.run_guard("rm -rf .claude")
        self.assertTrue(run.denied, "rm -rf .claude was allowed: %s" % run.stderr)

    def test_renaming_a_protected_file_is_refused_on_both_sides(self):
        run = self.run_guard("mv CLAUDE.md CLAUDE.md.bak")
        self.assertTrue(run.denied, "a rename of CLAUDE.md was allowed: %s" % run.stderr)
        self.assertIn("CLAUDE.md", run.reason)

    def test_a_redirection_hidden_behind_a_pipeline_is_still_seen(self):
        run = self.run_guard("cat docs/notes.md | tr a b > hooks/hooks.json")
        self.assertTrue(run.denied, "a piped redirection was allowed: %s" % run.stderr)
        self.assertIn("hooks/hooks.json", run.reason)

    def test_a_greater_than_inside_quotes_is_text_and_not_a_redirection(self):
        """The reason this file carries its own scanner instead of
        shlex.split: shlex drops the fact that answers this, and `grep '>'
        CLAUDE.md` would have read as a redirection into CLAUDE.md."""
        run = self.run_guard("grep '>' CLAUDE.md")
        self.assertFalse(run.denied,
                         "a quoted '>' was read as a redirection: %s" % run.reason)
        self.assertIn("did NOT classify as a write", run.stderr,
                      "the protected mention was not reported at all: %r" % run.stderr)


class TestCaseVariantPaths(GuardCase):
    """Spec fixture 8, written for the filesystem this project is developed
    on rather than for an idealized one.

    macOS's default volume folds case, so `> claude.md` lands on CLAUDE.md and
    must be refused. Linux's default does not, so `claude.md` is honestly a
    different file and refusing it would be a false conflict. The test asserts
    the RIGHT answer for whichever volume it is running on, and proves which
    one that is by asking the filesystem rather than by reading sys.platform.
    """

    def _folds_case(self):
        probe = os.path.join(self.root, "CLAUDE.md")
        variant = os.path.join(self.root, "claude.md")
        try:
            return os.path.exists(variant) and os.path.samefile(probe, variant)
        except OSError:
            return False

    def test_fixture_8_a_case_variant_of_a_protected_path(self):
        run = self.run_guard("printf 'x' > claude.md")
        if self._folds_case():
            self.assertTrue(
                run.denied,
                "this filesystem folds case, so claude.md IS CLAUDE.md and the write "
                "must be refused; it was allowed. stderr: %s" % run.stderr)
            self.assertIn("CLAUDE.md", run.reason)
        else:
            self.assertFalse(
                run.denied,
                "this filesystem is case-sensitive, so claude.md is a different file "
                "and refusing it would be a false conflict: %s" % run.reason)

    def test_the_case_fold_is_never_trusted_without_the_filesystem(self):
        """A case-folded STRING match alone must never refuse. The
        confirmation is monkeypatched off and the exact-spelling answer has to
        stand, which is the same proof tools/test_sbe_fence_hook.py makes
        about `paths_overlap`."""
        fence = bg.require_fence_module()
        original = fence._same_entry_case_insensitive
        fence._same_entry_case_insensitive = lambda root, t, c: False
        try:
            surface = bg.require_surface_module()
            authority = bg.require_authority_module()
            family = bg.protected_family(fence, surface, authority, self.root, "claude.md")
            self.assertEqual(
                family, "",
                "a case-folded string match was trusted with no filesystem confirmation")
        finally:
            fence._same_entry_case_insensitive = original


class TestDeclaredScopeIsAllowed(GuardCase):

    def test_fixture_10_a_protected_path_inside_an_open_task_ownedpaths_is_allowed(self):
        """Spec fixture 10. The guard is a scope control, not a prohibition:
        a declared write proceeds, and the note says the Stop reconciler still
        judges what the command actually changed."""
        self.set_tasks([self.owning_task("hooks/hooks.json")])
        run = self.run_guard("sed -i 's/old/new/' hooks/hooks.json")
        self.assertFalse(run.denied,
                         "a declared write to hooks/hooks.json was refused: %s" % run.reason)
        self.assertIn("IS declared in an open task", run.stderr)

    def test_a_neighbouring_protected_path_is_still_refused_under_the_same_task(self):
        """The declaration covers what it names and nothing else, which is the
        difference between a scope control and an on/off switch."""
        self.set_tasks([self.owning_task("hooks/hooks.json")])
        run = self.run_guard("printf 'x' > CLAUDE.md")
        self.assertTrue(run.denied, "an undeclared protected write rode in on a "
                                    "neighbour's declaration: %s" % run.stderr)
        self.assertIn("CLAUDE.md", run.reason)

    def test_fixture_9_an_ordinary_read_only_command_is_allowed_silently(self):
        for command in ("ls -la", "git status --porcelain", "cat docs/notes.md"):
            run = self.run_guard(command)
            self.assertFalse(run.denied, "%r was refused: %s" % (command, run.reason))
            self.assertEqual(run.stderr, "",
                             "%r produced noise on stderr: %r" % (command, run.stderr))

    def test_an_ordinary_write_outside_every_protected_family_is_allowed(self):
        run = self.run_guard("printf 'x' > docs/notes.md")
        self.assertFalse(run.denied, "an ordinary write was refused: %s" % run.reason)


class TestFailOpen(GuardCase):
    """Every failure mode this file can construct ALLOWS, and says on stderr
    that it allowed and why."""

    def test_an_unterminated_quote_allows_and_names_the_reason(self):
        run = self.run_guard('echo "unterminated')
        self.assertFalse(run.denied, "an unparseable command was refused")
        self.assertIn("FAILING OPEN", run.stderr)
        self.assertIn("unterminated double quote", run.stderr)

    def test_an_unreadable_task_registry_allows_and_names_the_reason(self):
        write(self.registry, "{not json")
        run = self.run_guard("printf 'x' > CLAUDE.md")
        self.assertFalse(run.denied,
                         "a corrupt registry produced a REFUSAL, which is the wrong "
                         "direction for a hook in front of every Bash call")
        self.assertIn("FAILING OPEN", run.stderr)

    def test_a_payload_with_no_command_allows_and_names_the_reason(self):
        proc = subprocess.run(
            [sys.executable, GUARD_PATH],
            input=json.dumps({"tool_name": "Bash", "tool_input": {},
                              "session_id": MY_SESSION, "cwd": self.root,
                              "project_dir": self.root}),
            capture_output=True, text=True)
        self.assertEqual(proc.stdout, "", "an empty tool_input produced a decision")
        self.assertIn("FAILING OPEN", proc.stderr)

    def test_a_non_bash_tool_is_ignored_in_silence(self):
        proc = subprocess.run(
            [sys.executable, GUARD_PATH],
            input=json.dumps({"tool_name": "Write",
                              "tool_input": {"file_path": "CLAUDE.md", "content": "x"},
                              "session_id": MY_SESSION, "cwd": self.root,
                              "project_dir": self.root}),
            capture_output=True, text=True)
        self.assertEqual(proc.stdout, "", "the Bash guard decided a Write call")
        self.assertEqual(proc.stderr, "", "the Bash guard narrated a Write call")

    def test_empty_stdin_allows_and_names_the_reason(self):
        proc = subprocess.run([sys.executable, GUARD_PATH], input="",
                              capture_output=True, text=True)
        self.assertEqual(proc.returncode, 0)
        self.assertEqual(proc.stdout, "")
        self.assertIn("FAILING OPEN", proc.stderr)

    def test_the_disable_switch_allows_and_says_so(self):
        os.environ["BROTHERSBE_BASH_GUARD_OFF"] = "1"
        run = self.run_guard("printf 'x' > CLAUDE.md")
        self.assertFalse(run.denied, "the disable switch did not disable the guard")
        self.assertIn("BROTHERSBE_BASH_GUARD_OFF is set", run.stderr)


class TestTheHonestyOfTheAllowPath(GuardCase):
    """Spec 1.3 rule 8: never claim that an allowed Bash command is proven
    read only. Asserted mechanically over every allowed fixture in this file
    rather than by inspection, because a reassuring sentence added next year
    to one code path is exactly what an eyeball review misses."""

    CLAIMS = ("proven read only", "proven read-only", "is read only",
              "is read-only", "safe command", "no writes", "verified read")

    def test_no_allow_path_ever_claims_the_command_was_read_only(self):
        """Every read-only-shaped phrase on either channel must sit inside a
        NEGATION.

        The distinction is the whole point and it is checked rather than
        assumed: "this is not a claim that the command is read only" is the
        sentence spec 1.3 rule 8 asks for, and "the command is read only" is
        the sentence it forbids. A plain substring ban would have failed the
        honest sentence and passed nothing useful, so the window before each
        hit is what decides."""
        for command in ALLOWED_COMMANDS:
            run = self.run_guard(command)
            self.assertFalse(run.denied, "%r was refused: %s" % (command, run.reason))
            blob = (run.stdout + run.stderr).lower()
            for claim in self.CLAIMS:
                start = 0
                while True:
                    at = blob.find(claim, start)
                    if at < 0:
                        break
                    window = blob[max(0, at - 60):at]
                    self.assertIn(
                        "not", window,
                        "the guard claimed %r about %r with no negation in front of it, "
                        "which it cannot know: text cannot show what a script or a "
                        "computed path will do. Context: %r"
                        % (claim, command, blob[max(0, at - 60):at + 40]))
                    start = at + 1

    def test_a_protected_mention_in_an_unclassified_command_is_reported(self):
        """The fail-open warning spec 1.3 rule 7 asks for. `grep CLAUDE.md`
        is allowed, and the operator is told the guard saw a protected path go
        by inside something it did not classify as a write."""
        run = self.run_guard("grep -n TODO CLAUDE.md")
        self.assertFalse(run.denied)
        self.assertIn("did NOT classify as a write", run.stderr)
        self.assertIn("not a claim that the command is read only", run.stderr)


def as_command_path(path):
    """`path` written the way a shell command can actually carry it.

    The guard tokenizes with POSIX shell rules, where a backslash is an ESCAPE
    character. On Windows os.path.join produces a backslash path, which
    tokenizes to a single separator-less word, so _exec_target_raw sees no
    path, the cross-project check never runs, and a command that must be
    refused is allowed instead.

    Git Bash, the shell this project's Bash tool uses on Windows, writes those
    same paths with forward slashes, and os.path.realpath resolves either form
    to the same native path. Converting here makes the command realistic on
    both platforms, and is a no-op on POSIX where os.sep is already "/".

    This does NOT paper over the guard's backslash blindness. That blindness is
    a real defect in the guard, tracked separately; this helper only ensures
    these tests exercise the control they claim to exercise rather than passing
    through a tokenizer gap.
    """
    return path.replace(os.sep, "/")


class TestCrossProjectExecutionIsRefused(GuardCase):
    """ADR 2026-08-12, change 3: no session runs another project's tools.

    A separate temp directory stands in for "the companion product", marked
    with all three of `.git`, `.claude-plugin/plugin.json` and `PROJECT.md`
    (amendment 2026-08-29: all three together, not any one, is what makes a
    directory a project root -- see the ADR and the guard's own section
    docstring for why) so it reads as a project root the same way the real
    companion and this real project both do.
    """

    def _make_other_project(self, plugin_name="brothermode"):
        # Amendment 2026-08-29, second pass: plugin_name defaults to
        # "brothermode" (the real companion's own declared name) so the
        # read-only-verb exemption tests below, which need the identity
        # check to pass, need no fixture of their own. Tests of the
        # identity check ITSELF pass a different name explicitly.
        other = tempfile.TemporaryDirectory()
        self.addCleanup(other.cleanup)
        other_root = os.path.realpath(other.name)
        os.makedirs(os.path.join(other_root, ".git"))
        os.makedirs(os.path.join(other_root, ".claude-plugin"))
        write(os.path.join(other_root, ".claude-plugin", "plugin.json"),
              json.dumps({"name": plugin_name}) + "\n")
        write(os.path.join(other_root, "PROJECT.md"), "other project\n")
        os.makedirs(os.path.join(other_root, "tools"))
        tool = os.path.join(other_root, "tools", "bm_handover.py")
        write(tool, "print('ceremony')\n")
        return other_root, tool

    def test_executing_another_projects_tool_is_refused_by_name(self):
        other_root, tool = self._make_other_project()
        run = self.run_guard("python3 %s" % as_command_path(tool))
        self.assertTrue(run.denied,
                        "executing another project's tool was allowed: %s" % run.stderr)
        self.assertIn(other_root, run.reason, "the refusal does not name the other root")
        self.assertIn(tool, run.reason, "the refusal does not name the tool")
        self.assertIn("open a session in", run.reason)

    def test_executing_another_projects_shell_script_is_refused(self):
        other_root, _ = self._make_other_project()
        script = os.path.join(other_root, "scripts", "y.sh")
        os.makedirs(os.path.dirname(script))
        write(script, "#!/bin/sh\necho hi\n")
        run = self.run_guard("sh %s" % as_command_path(script))
        self.assertTrue(run.denied, "sh <other project script> was allowed: %s" % run.stderr)
        self.assertIn(other_root, run.reason)

    def test_executing_another_projects_binary_directly_is_refused(self):
        other_root, _ = self._make_other_project()
        binary = os.path.join(other_root, "bin", "z")
        os.makedirs(os.path.dirname(binary))
        write(binary, "#!/bin/sh\necho hi\n")
        run = self.run_guard(as_command_path(binary))
        self.assertTrue(run.denied, "executing another project's binary was allowed: %s"
                        % run.stderr)
        self.assertIn(other_root, run.reason)

    def test_executing_another_projects_tool_through_a_relative_path_is_refused(self):
        other_root, tool = self._make_other_project()
        rel = os.path.relpath(tool, self.root)
        run = self.run_guard("python3 %s" % as_command_path(rel))
        self.assertTrue(run.denied,
                        "a relative path resolving outside the root was allowed: %s"
                        % run.stderr)
        self.assertIn(other_root, run.reason)

    def test_a_read_only_verb_of_the_exempted_tool_is_allowed(self):
        # Amendment 2026-08-29: bm_project.py's own source proves `status`
        # and `next` never write (bm_project.py:298, `_read_store()` builds a
        # `bs.ReadOnlyStore`), so the categorical block no longer covers them.
        other_root, _ = self._make_other_project()
        tool = os.path.join(other_root, "tools", "bm_project.py")
        write(tool, "print('read')\n")
        for verb in ("status", "next"):
            run = self.run_guard("python3 %s %s --project-id p1" % (tool, verb))
            self.assertFalse(run.denied,
                             "bm_project.py %s was refused: %s" % (verb, run.reason))

    def test_a_write_verb_of_the_exempted_tool_stays_refused(self):
        other_root, _ = self._make_other_project()
        tool = os.path.join(other_root, "tools", "bm_project.py")
        write(tool, "print('write')\n")
        run = self.run_guard("python3 %s start --project-id p1 --name X" % tool)
        self.assertTrue(run.denied, "bm_project.py start was allowed: %s" % run.stderr)
        self.assertIn(other_root, run.reason)

    def test_the_read_only_exemption_is_scoped_to_the_named_tool_only(self):
        # A DIFFERENT tool that happens to take a "next" argument is not
        # exempted by the word matching -- the allowlist is keyed by the
        # tool's own basename, not by verb text alone.
        other_root, _ = self._make_other_project()
        tool = os.path.join(other_root, "tools", "bm_handover.py")
        write(tool, "print('ceremony')\n")
        run = self.run_guard("python3 %s next" % tool)
        self.assertTrue(run.denied,
                        "an unrelated tool's 'next' argument was allowed: %s" % run.stderr)

    def test_a_directory_with_only_one_of_the_three_markers_is_not_detected(self):
        # Amendment 2026-08-29: all three markers together, not any one, mark
        # a project root now. A directory with only `.git` (measured: the
        # shape of 30 of the 32 installed plugins on this machine that carry
        # a stray `.git` left by a git-backed marketplace install) is
        # NO-DATA, not a refusal.
        other = tempfile.TemporaryDirectory()
        self.addCleanup(other.cleanup)
        other_root = os.path.realpath(other.name)
        os.makedirs(os.path.join(other_root, ".git"))
        os.makedirs(os.path.join(other_root, "tools"))
        tool = os.path.join(other_root, "tools", "z.py")
        write(tool, "print('hi')\n")
        run = self.run_guard("python3 %s" % tool)
        self.assertFalse(run.denied,
                         "a directory with only .git was treated as a marked project: %s"
                         % run.reason)

    def test_the_exempted_verb_is_refused_when_the_manifest_names_a_different_plugin(self):
        # Amendment 2026-08-29, second pass, after independent review: this
        # file's own earlier version of this test proved a file merely NAMED
        # bm_project.py, anywhere under three markers, got exempted with
        # nothing checked about what it actually was. The exemption now
        # reads the marker root's own plugin.json and requires its declared
        # name to match; a directory that carries all three markers but is
        # NOT brothermode must not borrow the exemption just by naming a file
        # bm_project.py inside it.
        other_root, _ = self._make_other_project(plugin_name="not-brothermode")
        tool = os.path.join(other_root, "tools", "bm_project.py")
        write(tool, "print('impersonating')\n")
        run = self.run_guard("python3 %s next --project-id p1" % tool)
        self.assertTrue(run.denied,
                        "a same-named file under a differently-named plugin was exempted: %s"
                        % run.stderr)

    def test_the_exempted_verb_is_refused_when_the_manifest_is_unreadable(self):
        # The identity check fails closed: a plugin.json that cannot be
        # parsed must refuse the command, never silently exempt it, because
        # this file's own philosophy of allowing what it cannot tell governs
        # whether a command is a CANDIDATE at all, not whether an
        # already-identified candidate earns a narrower refusal.
        other_root, _ = self._make_other_project()
        write(os.path.join(other_root, ".claude-plugin", "plugin.json"), "not valid json {")
        tool = os.path.join(other_root, "tools", "bm_project.py")
        write(tool, "print('read')\n")
        run = self.run_guard("python3 %s next --project-id p1" % tool)
        self.assertTrue(run.denied,
                        "an unreadable manifest was treated as a name match: %s" % run.stderr)


class TestInstalledPluginRootsExemptsOnlyThisGuardsOwnTool(GuardCase):
    """Amendment 2026-08-29, second pass, after independent review and a live
    replay that proved it: `_installed_plugin_roots()` used to also exempt
    the ENTIRE plugin cache directory, so a genuine companion's WRITE verb,
    invoked via its real installed plugin-cache path exactly as brotherme's
    own SKILL.md documents, was let through unconditionally, before the
    marker walk or the read-only-verb table ever ran. That defeated this
    guard's ADR-change-3 protection for the one case it actually has to hold
    for: a companion installed the normal way. The cache-wide half is
    removed; only this guard's own installation root stays exempt.
    """

    def test_a_sibling_tool_under_a_fake_installed_plugin_cache_is_no_longer_exempt(self):
        cache_home = tempfile.TemporaryDirectory()
        self.addCleanup(cache_home.cleanup)
        saved = os.environ.get("CLAUDE_CONFIG_DIR")
        os.environ["CLAUDE_CONFIG_DIR"] = cache_home.name
        self.addCleanup(
            lambda: (os.environ.pop("CLAUDE_CONFIG_DIR", None) if saved is None
                     else os.environ.__setitem__("CLAUDE_CONFIG_DIR", saved)))
        companion = os.path.join(cache_home.name, "plugins", "cache",
                                  "brother", "brothermode", "9.9.9")
        os.makedirs(os.path.join(companion, ".git"))
        os.makedirs(os.path.join(companion, ".claude-plugin"))
        write(os.path.join(companion, ".claude-plugin", "plugin.json"),
              json.dumps({"name": "brothermode"}) + "\n")
        write(os.path.join(companion, "PROJECT.md"), "brothermode\n")
        os.makedirs(os.path.join(companion, "tools"))
        tool = os.path.join(companion, "tools", "bm_project.py")
        write(tool, "print('write')\n")
        run = self.run_guard("python3 %s start --project-id p1 --name X" % tool)
        self.assertTrue(run.denied,
                        "a companion's write verb under a simulated plugin cache was "
                        "allowed: %s" % run.stderr)

    def test_this_guards_own_sibling_tool_stays_exempt_from_a_foreign_root(self):
        # The one behavior _installed_plugin_roots() must keep: this guard's
        # OWN installation still runs its own tools regardless of which
        # project a session happens to be rooted in.
        own_root = os.path.dirname(HERE)
        sibling = os.path.join(own_root, "tools", "sbe_authority_hook.py")
        self.assertTrue(os.path.isfile(sibling),
                        "fixture assumes a real sibling tool ships next to this guard")
        run = self.run_guard("python3 %s" % sibling)
        self.assertFalse(run.denied,
                         "this guard's own sibling tool was refused from a foreign "
                         "project root: %s" % run.reason)


class _OtherProject(object):
    """The standin other project a FIX 8 case runs against: its root, and a
    tool inside it.

    An object rather than a (root, tool) pair, for the reason CrossProjectHit
    states in sbe_bash_write_guard.py: a 2-or-3-tuple return reads as a
    possible verdict source to this project's honesty meta-test, and this is
    not one. Following that convention here means this fixture needs no
    exemption at all."""

    def __init__(self, root, tool):
        self.root = root
        self.tool = tool


@unittest.skipUnless(
    os.name == "nt",
    "these are Windows path shapes and the rewrite under test is a documented "
    "no-op on POSIX, so there is nothing here to exercise off Windows")
class TestWindowsPathFormsReachTheCrossProjectCheck(GuardCase):
    """A path a Windows command line actually carries must reach the check.

    This guard tokenizes with POSIX rules, where a backslash ESCAPES the next
    character, so `C:\\other\\bin\\z` used to tokenize to the single
    separator-less word `C:otherbinz`: no path was seen, the cross-project check
    never ran, and the command was ALLOWED. A Git Bash `/c/...` path failed the
    same way for a different reason, resolving to a `C:\\c\\...` that does not
    exist. Both were live defects that no test failed for.

    Amendment 2026-08-29: the skip decorator used to sit on `_OtherProject`, a
    plain fixture holder rather than a TestCase, where it was a complete no-op;
    two of this class's four methods (missing their own per-method skip) ran
    unintentionally on POSIX, and kept passing only because the old "any one
    marker" rule let a `.git`-only fixture count as another project. The
    all-three-markers rule this file's sibling class now requires exposed that
    as two real failures on this Mac, not a new defect: this class was always
    meant to run on Windows only, per its own docstring.
    """

    def _other_project(self):
        other = tempfile.TemporaryDirectory()
        self.addCleanup(other.cleanup)
        root = os.path.realpath(other.name)
        os.makedirs(os.path.join(root, ".git"))
        os.makedirs(os.path.join(root, ".claude-plugin"))
        write(os.path.join(root, ".claude-plugin", "plugin.json"), "{}\n")
        write(os.path.join(root, "PROJECT.md"), "other project\n")
        os.makedirs(os.path.join(root, "bin"))
        tool = os.path.join(root, "bin", "z")
        write(tool, "#!/bin/sh\necho hi\n")
        return _OtherProject(root, tool)

    @staticmethod
    def _as_git_bash(path):
        drive, rest = path.split(":", 1)
        return "/" + drive.lower() + rest.replace(os.sep, "/")

    def test_a_native_backslash_path_is_refused(self):
        other = self._other_project()
        root, tool = other.root, other.tool
        run = self.run_guard(tool)
        self.assertTrue(run.denied,
                        "a Windows-native path to another project's tool was "
                        "allowed: %s" % run.stderr)
        self.assertIn(root, run.reason)

    @unittest.skipUnless(os.name == "nt", "Git Bash /c/ drive-path form is a Windows-only scenario")
    def test_a_git_bash_drive_path_is_refused(self):
        other = self._other_project()
        root, tool = other.root, other.tool
        run = self.run_guard(self._as_git_bash(tool))
        self.assertTrue(run.denied,
                        "a Git Bash /c/ path to another project's tool was "
                        "allowed: %s" % run.stderr)
        self.assertIn(root, run.reason)

    def test_an_interpreter_given_a_native_backslash_script_is_refused(self):
        root = self._other_project().root
        script = os.path.join(root, "tools", "bm_handover.py")
        os.makedirs(os.path.dirname(script))
        write(script, "print('ceremony')\n")
        run = self.run_guard("python3 %s" % script)
        self.assertTrue(run.denied,
                        "an interpreter handed another project's script by a "
                        "Windows-native path was allowed: %s" % run.stderr)
        self.assertIn(root, run.reason)

    @unittest.skipUnless(os.name == "nt", "Git Bash /c/ drive-path form is a Windows-only scenario")
    def test_this_projects_own_tool_is_allowed_in_both_forms(self):
        os.makedirs(os.path.join(self.root, "bin"), exist_ok=True)
        tool = os.path.join(self.root, "bin", "sbe")
        write(tool, "#!/usr/bin/env python3\n")
        native = self.run_guard(tool)
        self.assertFalse(native.denied,
                         "this project's own tool was refused through a native "
                         "path: %s" % native.reason)
        gitbash = self.run_guard(self._as_git_bash(tool))
        self.assertFalse(gitbash.denied,
                         "this project's own tool was refused through a Git Bash "
                         "path: %s" % gitbash.reason)


class TestOneRepoProductSubtreesAreHome(GuardCase):
    """M5 (docs/plan/ONE-REPO-TRANSITION-2026-08-31.md, R24 closes on this):
    the three products consolidated into this estate's one repo each still
    carry their OWN `.claude-plugin/plugin.json` / `PROJECT.md` from their
    standalone days (products/brotherds, products/brothersbe,
    products/brothermode). A session rooted anywhere in the ONE repo that
    executes a sibling product's tool must not be refused as cross-project:
    both live in the same git working tree, not two repositories, so no
    write can land in "the companion's own repository" the way the ADR's
    incident describes. A genuinely separate git repository (its own
    `.git`, sharing no ancestor with the calling root) must still be
    refused even when it also happens to carry a PROJECT_MARKERS entry."""

    def _make_product(self, name):
        product = os.path.join(self.root, "products", name)
        os.makedirs(os.path.join(product, "tools"), exist_ok=True)
        write(os.path.join(product, "PROJECT.md"), "# %s\n" % name)
        tool = os.path.join(product, "tools", "tool.py")
        write(tool, "print('ok')\n")
        return product, tool

    def test_a_sibling_products_own_marker_is_not_cross_project_in_the_one_repo(self):
        os.makedirs(os.path.join(self.root, ".git"), exist_ok=True)
        _, tool_a = self._make_product("brothermode")
        product_b, _ = self._make_product("brothersbe")
        run = self.run_guard_rooted("python3 %s" % tool_a, cwd=product_b,
                                    project_dir=product_b)
        self.assertFalse(
            run.denied,
            "a sibling product subtree inside the one repo was refused as "
            "cross-project: %s" % run.reason)

    def test_a_genuinely_separate_git_repo_is_still_refused_even_with_a_product_marker(self):
        os.makedirs(os.path.join(self.root, ".git"), exist_ok=True)
        other = tempfile.TemporaryDirectory()
        self.addCleanup(other.cleanup)
        other_root = os.path.realpath(other.name)
        os.makedirs(os.path.join(other_root, ".git"))
        tool = os.path.join(other_root, "tools", "tool.py")
        os.makedirs(os.path.dirname(tool))
        write(tool, "print('ok')\n")
        write(os.path.join(other_root, "PROJECT.md"), "# other\n")
        # the upstream contract needs all three markers before it will even
        # call a directory a project; two markers is NO-DATA, which allows.
        os.makedirs(os.path.join(other_root, ".claude-plugin"), exist_ok=True)
        write(os.path.join(other_root, ".claude-plugin", "plugin.json"),
              '{"name": "other-project"}\n')
        run = self.run_guard_rooted("python3 %s" % tool, cwd=self.root,
                                    project_dir=self.root)
        self.assertTrue(run.denied,
                        "a genuinely separate git repository was allowed: %s" % run.stderr)
        self.assertIn(other_root, run.reason)


class TestOwnToolExecutionIsUnaffected(GuardCase):
    """Property 2: running this project's own tools must be untouched, by
    absolute path, by relative path, from a subdirectory, and through a
    symlink."""

    def test_own_tool_by_absolute_path_is_allowed(self):
        tool = os.path.join(self.root, "tools", "anything.py")
        write(tool, "print('ok')\n")
        run = self.run_guard("python3 %s" % tool)
        self.assertFalse(run.denied, "own tool by absolute path was refused: %s" % run.reason)

    def test_own_tool_by_relative_path_is_allowed(self):
        write(os.path.join(self.root, "tools", "anything.py"), "print('ok')\n")
        run = self.run_guard("python3 tools/anything.py")
        self.assertFalse(run.denied, "own tool by relative path was refused: %s" % run.reason)

    def test_own_tool_run_directly_without_an_interpreter_is_allowed(self):
        os.makedirs(os.path.join(self.root, "bin"), exist_ok=True)
        write(os.path.join(self.root, "bin", "sbe"), "#!/usr/bin/env python3\n")
        run = self.run_guard("bin/sbe")
        self.assertFalse(run.denied, "own bin/sbe was refused: %s" % run.reason)

    def test_own_tool_run_from_a_subdirectory_is_allowed(self):
        os.makedirs(os.path.join(self.root, "bin"), exist_ok=True)
        write(os.path.join(self.root, "bin", "sbe"), "#!/usr/bin/env python3\n")
        subdir = os.path.join(self.root, "docs", "sub")
        os.makedirs(subdir, exist_ok=True)
        run = self.run_guard_from("../../bin/sbe", subdir)
        self.assertFalse(run.denied,
                         "own tool run from a subdirectory was refused: %s" % run.reason)

    def test_own_tool_through_a_symlink_is_allowed(self):
        real = os.path.join(self.root, "tools", "real_tool.py")
        write(real, "print('ok')\n")
        link = os.path.join(self.root, "tools", "linked_tool.py")
        os.symlink(real, link)
        run = self.run_guard("python3 %s" % link)
        self.assertFalse(run.denied, "own tool via a symlink was refused: %s" % run.reason)


class TestReadingAnotherProjectStaysOrdinary(GuardCase):
    """The property that decides whether this guard survives contact with
    real use: READING another project's files must never be refused."""

    def test_cat_grep_and_git_dash_c_against_another_project_are_allowed(self):
        other = tempfile.TemporaryDirectory()
        self.addCleanup(other.cleanup)
        other_root = os.path.realpath(other.name)
        os.makedirs(os.path.join(other_root, ".git"))
        readme = os.path.join(other_root, "README.md")
        write(readme, "hello\n")
        for command in (
            "cat %s" % readme,
            "grep hello %s" % readme,
            "git -C %s log" % other_root,
            "ls %s" % other_root,
        ):
            run = self.run_guard(command)
            self.assertFalse(
                run.denied,
                "%r was refused; reading another project must stay ordinary: %s"
                % (command, run.reason))


class TestUndeterminablePathsAllow(GuardCase):
    """Property 3: an undeterminable path is NO-DATA, and the guard says it
    could not tell rather than staying silent about it."""

    def test_an_unresolvable_path_with_no_project_marker_anywhere_above_it_is_allowed(self):
        run = self.run_guard("python3 /no-such-tree-xyz-9f3/deep/path/tool.py")
        self.assertFalse(run.denied, "an unresolvable path was refused: %s" % run.reason)
        self.assertIn("could not tell", run.stderr,
                      "the guard stayed silent about an undeterminable path: %r"
                      % run.stderr)

    def test_a_python_dash_m_module_is_never_read_as_a_file_path(self):
        """`python3 -m pytest` names an importable MODULE, not a file on
        disk. Run with `cwd` set inside a directory this test marks as a
        separate project, so a wrong reading (treating `pytest` as a relative
        path) would resolve straight into that project and be refused for
        the wrong reason: nothing at that path was ever going to run."""
        other = tempfile.TemporaryDirectory()
        self.addCleanup(other.cleanup)
        other_root = os.path.realpath(other.name)
        os.makedirs(os.path.join(other_root, ".git"))
        run = self.run_guard_from("python3 -m pytest", other_root)
        self.assertFalse(run.denied,
                         "python3 -m pytest was refused as if pytest were a file: %s"
                         % run.reason)


class TestTheWireContract(GuardCase):

    def test_the_deny_payload_matches_the_pretooluse_contract(self):
        run = self.run_guard("printf 'x' > CLAUDE.md")
        data = json.loads(run.stdout)
        out = data["hookSpecificOutput"]
        self.assertEqual(out["hookEventName"], "PreToolUse")
        self.assertEqual(out["permissionDecision"], "deny")
        self.assertTrue(out["permissionDecisionReason"].strip())

    def test_the_refusal_names_all_three_recoveries(self):
        run = self.run_guard("printf 'x' > CLAUDE.md")
        for fragment in ("sbe task open", "Edit or Write", ".sbe/break-glass.json"):
            self.assertIn(fragment, run.reason,
                          "the refusal does not name the recovery %r" % fragment)

    def test_help_exits_0_with_usage_on_stderr_and_a_clean_stdout(self):
        for argv in (("-h",), ("--help",)):
            proc = subprocess.run([sys.executable, GUARD_PATH] + list(argv),
                                  capture_output=True, text=True)
            self.assertEqual(proc.returncode, 0)
            self.assertIn("usage: sbe_bash_write_guard.py", proc.stderr)
            self.assertEqual(proc.stdout, "",
                             "usage leaked onto the decision channel")

    def test_an_unknown_subcommand_exits_2_with_a_clean_stdout(self):
        proc = subprocess.run([sys.executable, GUARD_PATH, "bogus"],
                              capture_output=True, text=True)
        self.assertEqual(proc.returncode, 2)
        self.assertEqual(proc.stdout, "")
        self.assertIn("unknown command", proc.stderr)

    def test_an_unrecognized_flag_exits_2_instead_of_reading_stdin(self):
        """A flag this surface does not know must be refused, never dropped
        and read as a bare hook invocation with a stray argument."""
        proc = subprocess.run([sys.executable, GUARD_PATH, "--no-such-flag"],
                              input="", capture_output=True, text=True)
        self.assertEqual(proc.returncode, 2)
        self.assertEqual(proc.stdout, "")
        self.assertIn("unrecognized flag", proc.stderr)

    def test_the_bare_hook_invocation_still_fails_open(self):
        proc = subprocess.run([sys.executable, GUARD_PATH], input="",
                              capture_output=True, text=True)
        self.assertEqual(proc.returncode, 0,
                         "the bare hook invocation must never block a session")
        self.assertIn("FAILING OPEN", proc.stderr)

    def test_classify_reports_on_stderr_only(self):
        proc = subprocess.run(
            [sys.executable, GUARD_PATH, "classify", "printf x > CLAUDE.md", self.root],
            capture_output=True, text=True)
        self.assertEqual(proc.returncode, 0)
        self.assertEqual(proc.stdout, "", "a diagnostic leaked onto the decision channel")
        self.assertIn("CLAUDE.md", proc.stderr)


if __name__ == "__main__":
    unittest.main(verbosity=2)

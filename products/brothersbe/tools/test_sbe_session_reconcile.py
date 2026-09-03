#!/usr/bin/env python3
"""Regression tests for the BrotherSBE session baseline and Stop reconciler
(BR-1014, release blocker 1 items 1.2, 1.4 and 1.5).

Run: python3 tools/test_sbe_session_reconcile.py

Standard library only. Every fixture builds a REAL git repository in a temp
directory and makes the change through the filesystem or through a generated
script, never by handing the reconciler a synthetic change list. The whole
claim of this control is that it reads what actually survived, so a test that
fed it a made-up survivor would be testing the opposite thing.

THREE PROPERTIES CARRY THIS FILE.

  IT CATCHES WHAT COMMAND PARSING CANNOT. A generated shell script, a
  deletion, a rename, and a symlink write are spec fixtures 4, 5, 6 and 7.
  None of them is visible in the text of the command that started them, and
  all four are asserted here against the surviving repository state.

  ATTRIBUTION IS BY CONTENT, NOT BY NAME. Spec fixtures 11 and 12: a file that
  was already dirty when the session opened is NOT this session's while it is
  untouched, and IS this session's the moment its content changes again. A
  path list cannot tell those apart, which is why the baseline stores digests.

  IT FAILS CLOSED, AND THE NAMED RECOVERY ACTUALLY WORKS. The four fail-closed
  conditions from spec 1.4 item 9 each have a fixture. And the refusal names
  three recoveries, so `test_the_named_recovery_actually_clears_the_block`
  performs one (restores the file) and replays the identical reconciliation to
  prove the door opens, exactly as tools/test_sbe_fence_hook.py does for the
  fence hook's own escape.

CALIBRATION. Every assertion here was run RED against a deliberately broken
copy of the reconciler before it was run green against the real one. The
mutations used were (a) making `attribute` return every current change (which
breaks fixture 11, the pre-session dirt case) and (b) making
`read_baseline`'s absence path allow instead of raise (which breaks the
missing-baseline fail-closed case). Both are recorded in the BR-1014 patch
note.
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
RECONCILE_PATH = os.path.join(HERE, "sbe_session_reconcile.py")
BASELINE_PATH = os.path.join(HERE, "sbe_session_baseline.py")


def _load(alias, path):
    spec = importlib.util.spec_from_file_location(alias, path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    sys.modules[alias] = mod
    return mod


sr = _load("sbe_session_reconcile", RECONCILE_PATH)
sb = _load("sbe_session_baseline", BASELINE_PATH)

MY_SESSION = "session-aaaa-bbbb-cccc"

TASK_FIELDS = {
    "id": "t1", "agent": "writer-1", "role": "writer", "worktree": "",
    "ownedPaths": [], "readOnlyPaths": [], "baseCommit": "0" * 40,
    "expiry": "2099-01-01T00:00:00Z", "status": "open",
    "verifyCommand": "python3 -c pass", "evidenceId": "",
    "openedAt": "2026-01-01T00:00:00Z", "closedAt": "",
}

#: A fence line in exactly the shape STATE.template.md prescribes, naming this
#: session as sole writer. Copied rather than imported for the reason
#: tools/test_sbe_install.py states about its own copy: a test fixture that
#: imported the shape under test could not fail when the shape drifted.
FENCE_STATE = (
    "# STATE\n\n## Fence registry\n\n"
    "- agent: reconcile-test (sole writer, session %s) | tier T1 | TTL 2099-12-31 |\n"
    "  objective: exercise the fence declaration path |\n"
    "  files: docs/fenced.md |\n"
    "  output: one commit |\n\n" % MY_SESSION)


def write(path, text):
    with io.open(path, "w", encoding="utf-8") as f:
        f.write(text)


def read(path):
    with io.open(path, encoding="utf-8") as f:
        return f.read()


class ReconcileCase(unittest.TestCase):
    """A real git repository with one of every protected family committed, and
    an environment put back exactly as it was found."""

    ENV_KEYS = ("BROTHERSBE_REGISTRIES", "BROTHERSBE_RECONCILE_OFF",
                "BROTHERSBE_SESSION_ID", "BROTHERSBE_FENCE_SESSION",
                "BROTHERSBE_RECONCILE_STATE_DIR")

    def setUp(self):
        self._saved = {k: os.environ.get(k) for k in self.ENV_KEYS}
        for k in self.ENV_KEYS:
            os.environ.pop(k, None)
        # The repeat-suppression state is pointed at a per-test directory so no
        # test reads another test's memory (or the real operator's), and so a
        # second run of this suite inside the cooldown window cannot see the
        # first run's state and wrongly suppress a block a test asserts on.
        self._state_tmp = tempfile.TemporaryDirectory()
        os.environ["BROTHERSBE_RECONCILE_STATE_DIR"] = self._state_tmp.name
        self._tmp = tempfile.TemporaryDirectory()
        self.root = os.path.realpath(self._tmp.name)
        for d in (".claude", "hooks", ".sbe", "docs"):
            os.makedirs(os.path.join(self.root, d), exist_ok=True)
        write(os.path.join(self.root, "CLAUDE.md"), "authority\n")
        write(os.path.join(self.root, ".claude", "settings.json"), "{}\n")
        write(os.path.join(self.root, "hooks", "hooks.json"), "{}\n")
        write(os.path.join(self.root, "docs", "notes.md"), "ordinary\n")
        write(os.path.join(self.root, "docs", "fenced.md"), "fenced\n")
        write(os.path.join(self.root, "STATE.md"), FENCE_STATE)
        self.registry = os.path.join(self.root, ".sbe", "tasks.json")
        self.set_tasks([])
        self.git("init", "-q")
        self.git("add", "-A")
        self.commit("initial")

    def tearDown(self):
        for k, v in self._saved.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v
        self._state_tmp.cleanup()
        self._tmp.cleanup()

    # -- helpers ------------------------------------------------------------

    def git(self, *args):
        proc = subprocess.run(["git"] + list(args), cwd=self.root,
                              capture_output=True, text=True)
        self.assertEqual(proc.returncode, 0,
                         "git %s failed: %s" % (" ".join(args), proc.stderr))
        return proc.stdout

    def commit(self, message):
        return self.git("-c", "user.email=t@example.invalid", "-c", "user.name=T",
                        "commit", "-q", "-m", message)

    def set_tasks(self, tasks):
        write(self.registry, json.dumps({"schemaVersion": "1.0", "tasks": tasks}))

    def owning_task(self, *paths):
        rec = dict(TASK_FIELDS)
        rec["ownedPaths"] = list(paths)
        return rec

    def baseline(self, session=MY_SESSION):
        return sb.write_baseline(self.root, session)

    def reconcile(self, session=MY_SESSION):
        return sr.reconcile_worktree(self.root, session)

    def blocked_reason(self, session=MY_SESSION):
        """The reason the reconciler blocks, whether it blocked through a
        violation or through a fail-closed condition. One accessor, so a test
        cannot pass because the block arrived down the other path."""
        try:
            outcome = self.reconcile(session)
        except sr.Unusable as e:
            return str(e)
        self.assertTrue(outcome.blocked,
                        "the reconciler did NOT block. Notes: %s" % "; ".join(outcome.notes))
        return outcome.reason

    def run_stop_hook(self, session=MY_SESSION):
        """The Stop hook as a SUBPROCESS, the way Claude Code runs it."""
        payload = json.dumps({"session_id": session, "cwd": self.root,
                              "hook_event_name": "Stop", "stop_hook_active": False})
        proc = subprocess.run([sys.executable, RECONCILE_PATH], input=payload,
                              capture_output=True, text=True)
        return proc


class TestWhatCommandParsingCannotSee(ReconcileCase):

    def test_fixture_4_a_generated_script_that_changes_a_protected_file_is_caught(self):
        """Spec fixture 4. The script is written OUTSIDE the repository, so the
        only thing the reconciler can see is the change the script left
        behind. No command text is available to it at all."""
        self.baseline()
        with tempfile.TemporaryDirectory() as elsewhere:
            script = os.path.join(elsewhere, "innocent-name.sh")
            write(script, "#!/bin/sh\nprintf 'rewritten\\n' > \"$1/CLAUDE.md\"\n")
            proc = subprocess.run(["sh", script, self.root],
                                  capture_output=True, text=True)
            self.assertEqual(proc.returncode, 0, proc.stderr)
        reason = self.blocked_reason()
        self.assertIn("CLAUDE.md", reason)
        self.assertIn("protected control-plane or authority surface", reason)

    def test_fixture_5_deleting_the_task_registry_is_caught(self):
        self.baseline()
        os.remove(self.registry)
        reason = self.blocked_reason()
        self.assertIn(".sbe/tasks.json", reason)

    def test_fixture_5b_deleting_the_registry_after_a_task_was_open_fails_closed(self):
        """The stronger half of the same fixture: when the session opened with
        a declared scope, removing the file that declares it is not a way to
        have no scope. That is a fail-closed condition, not a per-path
        violation, so it is asserted through the Unusable path."""
        self.set_tasks([self.owning_task("docs/notes.md")])
        self.git("add", "-A")
        self.commit("open a task")
        self.baseline()
        os.remove(self.registry)
        with self.assertRaises(sr.Unusable) as caught:
            self.reconcile()
        self.assertIn("no longer exists", str(caught.exception))

    def test_fixture_6_a_protected_file_modified_and_then_renamed_is_caught(self):
        """Spec fixture 6. Both names are named: the old one is gone and the
        new one is new, and reporting one of them would tell half the story."""
        self.baseline()
        write(os.path.join(self.root, "CLAUDE.md"), "rewritten\n")
        os.rename(os.path.join(self.root, "CLAUDE.md"),
                  os.path.join(self.root, "CLAUDE-old.md"))
        self.git("add", "-A")
        reason = self.blocked_reason()
        self.assertIn("CLAUDE.md", reason)

    def test_fixture_7_a_symlink_repointed_at_a_protected_target_is_caught(self):
        """Spec fixture 7 at the reconciliation layer. The write goes through
        a link with an innocent name; what survives is a changed CLAUDE.md."""
        self.baseline()
        link = os.path.join(self.root, "innocent.txt")
        os.symlink(os.path.join(self.root, "CLAUDE.md"), link)
        with io.open(link, "w", encoding="utf-8") as f:
            f.write("through the link\n")
        reason = self.blocked_reason()
        self.assertIn("CLAUDE.md", reason)

    def test_a_new_symlink_pointing_at_a_protected_path_is_itself_a_change(self):
        self.baseline()
        os.symlink("CLAUDE.md", os.path.join(self.root, "hooks", "shortcut.json"))
        reason = self.blocked_reason()
        self.assertIn("hooks/shortcut.json", reason)


class TestAttribution(ReconcileCase):
    """Spec fixtures 11 and 12: the difference between dirt that was already
    there and dirt this session made."""

    def test_fixture_11_a_file_dirty_before_the_session_is_not_attributed(self):
        write(os.path.join(self.root, "CLAUDE.md"), "dirty before the session\n")
        self.baseline()
        outcome = self.reconcile()
        self.assertFalse(
            outcome.blocked,
            "a file that was already dirty at session start and was never touched "
            "again was attributed to this session. Reason: %s" % outcome.reason)

    def test_fixture_12_the_same_file_changed_again_is_attributed(self):
        write(os.path.join(self.root, "CLAUDE.md"), "dirty before the session\n")
        self.baseline()
        write(os.path.join(self.root, "CLAUDE.md"), "and changed again during it\n")
        reason = self.blocked_reason()
        self.assertIn("CLAUDE.md", reason)
        self.assertIn("content changed since session start", reason)

    def test_a_preexisting_dirty_file_restored_to_head_is_not_a_surviving_change(self):
        write(os.path.join(self.root, "docs", "notes.md"), "dirty before\n")
        self.baseline()
        self.git("checkout", "--", "docs/notes.md")
        outcome = self.reconcile()
        self.assertFalse(outcome.blocked,
                         "restoring a file to its committed content was still counted "
                         "as a surviving change: %s" % outcome.reason)

    def test_a_file_that_becomes_a_symlink_is_attributed_by_kind(self):
        """A file type change, spec 1.4 item 4, asserted at the attribution
        layer rather than through a block: the path chosen here is deliberately
        NOT protected, so the only thing under test is whether the change was
        attributed to this session at all."""
        target = os.path.join(self.root, "docs", "notes.md")
        write(target, "dirty before\n")
        path = self.baseline()
        os.remove(target)
        os.symlink(os.path.join(self.root, "CLAUDE.md"), target)
        changes = sr.attribute(sb.changed_entries(self.root), json.loads(read(path)))
        matched = [c for c in changes if c.path == "docs/notes.md"]
        self.assertTrue(matched, "the type change was written off as pre-session dirt")
        self.assertIn("file kind changed", matched[0].why)


class TestFailClosed(ReconcileCase):
    """The four conditions spec 1.4 item 9 names, each asserted on its own."""

    def test_a_missing_baseline_with_a_dirty_tree_blocks(self):
        write(os.path.join(self.root, "CLAUDE.md"), "changed with no baseline\n")
        with self.assertRaises(sr.Unusable) as caught:
            self.reconcile()
        self.assertIn("no baseline exists", str(caught.exception))
        self.assertIn("never permission", str(caught.exception))

    def test_a_missing_baseline_with_a_clean_tree_allows_and_says_so(self):
        """The one carve-out, stated in the module docstring rather than
        discovered: with no change at all there is nothing to attribute, so
        blocking would refuse every session that merely read something."""
        outcome = self.reconcile()
        self.assertFalse(outcome.blocked)
        self.assertTrue(any("nothing to attribute" in n for n in outcome.notes),
                        "the allow was silent about why: %s" % outcome.notes)

    def test_a_corrupt_baseline_blocks_and_is_never_rewritten(self):
        self.baseline()
        path = sb.baseline_path(self.root, MY_SESSION)
        write(path, "{ not json")
        write(os.path.join(self.root, "CLAUDE.md"), "changed\n")
        with self.assertRaises(sr.Unusable) as caught:
            self.reconcile()
        self.assertIn("does not parse", str(caught.exception))
        self.assertEqual(read(path), "{ not json",
                         "the corrupt baseline was rewritten, which would turn a broken "
                         "control into a fresh clean one")

    def test_a_baseline_written_for_another_repository_root_blocks(self):
        path = self.baseline()
        data = json.loads(read(path))
        data["repositoryRoot"] = os.path.join(self.root, "docs")
        write(path, json.dumps(data))
        with self.assertRaises(sr.Unusable) as caught:
            self.reconcile()
        self.assertIn("repository root", str(caught.exception))

    def test_an_unreadable_task_registry_blocks(self):
        self.baseline()
        write(self.registry, "{ not json")
        with self.assertRaises(sr.Unusable) as caught:
            self.reconcile()
        self.assertIn("task registry could not be read", str(caught.exception))
        self.assertIn("fail-closed", str(caught.exception))

    def test_the_baseline_is_never_silently_re_written_mid_session(self):
        """A second SessionStart in the same session must not re-baseline a
        tree the session has already been writing to: that would launder every
        change made so far into pre-session dirt."""
        path = self.baseline()
        first = read(path)
        write(os.path.join(self.root, "CLAUDE.md"), "changed after the baseline\n")
        self.baseline()
        self.assertEqual(read(path), first,
                         "the baseline was overwritten, laundering this session's own "
                         "change into pre-session dirt")


class TestDeclaredScopeIsAllowed(ReconcileCase):

    def test_a_protected_change_inside_an_open_task_ownedpaths_is_allowed(self):
        self.set_tasks([self.owning_task("CLAUDE.md")])
        self.git("add", "-A")
        self.commit("declare CLAUDE.md")
        self.baseline()
        write(os.path.join(self.root, "CLAUDE.md"), "declared change\n")
        outcome = self.reconcile()
        self.assertFalse(outcome.blocked,
                         "a declared change was refused: %s" % outcome.reason)
        self.assertTrue(any("is declared by open task" in n for n in outcome.notes),
                        "the allow did not name the rule that permitted it: %s"
                        % outcome.notes)

    def test_a_change_covered_by_this_sessions_live_fence_is_allowed(self):
        """A task is open elsewhere, so the session IS operating under a
        declared scope and an undeclared ordinary change would be a violation.
        The only thing clearing docs/fenced.md is the live fence in STATE.md
        naming this session as sole writer."""
        self.set_tasks([self.owning_task("docs/nothing.md")])
        self.git("add", "-A")
        self.commit("open a task elsewhere")
        self.baseline()
        write(os.path.join(self.root, "docs", "fenced.md"), "changed under my fence\n")
        outcome = self.reconcile()
        self.assertFalse(outcome.blocked,
                         "a change inside this session's own live fence was refused: %s"
                         % outcome.reason)

    def test_the_same_fence_does_not_cover_another_session(self):
        self.set_tasks([self.owning_task("docs/nothing.md")])
        self.git("add", "-A")
        self.commit("open a task elsewhere")
        self.baseline(session="someone-else-9999")
        write(os.path.join(self.root, "docs", "fenced.md"), "changed by a stranger\n")
        outcome = self.reconcile(session="someone-else-9999")
        self.assertTrue(outcome.blocked,
                        "a fence owned by another session cleared this one")

    def test_an_ordinary_change_with_no_task_open_is_not_a_violation(self):
        """The named limit: with an empty task registry a session has declared
        nothing and is outside nothing. Protected paths still block; ordinary
        ones do not, and the note says this is a heuristic about intent."""
        self.baseline()
        write(os.path.join(self.root, "docs", "notes.md"), "ordinary work\n")
        outcome = self.reconcile()
        self.assertFalse(outcome.blocked, outcome.reason)
        self.assertTrue(any("heuristic about intent" in n for n in outcome.notes),
                        "the limit was applied silently: %s" % outcome.notes)

    def test_an_ordinary_change_outside_an_open_tasks_scope_is_a_violation(self):
        self.set_tasks([self.owning_task("docs/other.md")])
        self.git("add", "-A")
        self.commit("open a task elsewhere")
        self.baseline()
        write(os.path.join(self.root, "docs", "notes.md"), "outside my scope\n")
        reason = self.blocked_reason()
        self.assertIn("docs/notes.md", reason)
        self.assertIn("outside every open task", reason)


class TestBreakGlass(ReconcileCase):
    """Spec 1.4 item 10's third recovery, and the four fields spec 3.4 item 4
    requires of any exception."""

    def _record(self, **overrides):
        rec = {"id": "bg-1", "reason": "emergency rollback of a bad hook",
               "owner": "khalil", "expiry": "2099-12-31",
               "approval": {"approver": "control-plane-owner", "reference": "PR 12"},
               "paths": ["CLAUDE.md"]}
        rec.update(overrides)
        write(os.path.join(self.root, ".sbe", "break-glass.json"),
              json.dumps({"schemaVersion": "1.0", "records": [rec]}))

    def test_a_complete_unexpired_record_clears_the_path_and_is_named(self):
        self._record()
        self.git("add", "-A")
        self.commit("record the exception")
        self.baseline()
        write(os.path.join(self.root, "CLAUDE.md"), "the emergency change\n")
        outcome = self.reconcile()
        self.assertFalse(outcome.blocked, "a valid break-glass record did not clear the "
                                          "path it names: %s" % outcome.reason)
        self.assertTrue(any("BREAK GLASS" in n for n in outcome.notes),
                        "the exception was honored SILENTLY, which is how an exception "
                        "stops being reviewed: %s" % outcome.notes)

    def test_an_expired_record_grants_nothing_and_says_why(self):
        self._record(expiry="2000-01-01")
        self.git("add", "-A")
        self.commit("record the exception")
        self.baseline()
        write(os.path.join(self.root, "CLAUDE.md"), "the emergency change\n")
        outcome = self.reconcile()
        self.assertTrue(outcome.blocked, "an expired break-glass record still cleared "
                                         "the path")
        self.assertTrue(any("expired on" in n for n in outcome.notes),
                        "the expiry was refused silently: %s" % outcome.notes)

    def test_a_record_without_an_approver_grants_nothing_and_says_why(self):
        self._record(approval={"reference": "PR 12"})
        self.git("add", "-A")
        self.commit("record the exception")
        self.baseline()
        write(os.path.join(self.root, "CLAUDE.md"), "the emergency change\n")
        outcome = self.reconcile()
        self.assertTrue(outcome.blocked, "a record with no approver still cleared the path")
        self.assertTrue(any("approval.approver" in n for n in outcome.notes),
                        "the missing approver was not named: %s" % outcome.notes)


class TestTheNamedRecoveryWorks(ReconcileCase):
    """A refusal naming a door that does not open is the failure this test
    exists to catch, the same property tools/test_sbe_fence_hook.py proves of
    the fence hook's own escape."""

    def test_the_named_recovery_actually_clears_the_block(self):
        self.baseline()
        write(os.path.join(self.root, "CLAUDE.md"), "an undeclared protected change\n")
        reason = self.blocked_reason()
        self.assertIn("git restore", reason)
        self.git("checkout", "--", "CLAUDE.md")
        outcome = self.reconcile()
        self.assertFalse(outcome.blocked,
                         "the recovery the refusal named did not clear it: %s"
                         % outcome.reason)


class TestTheStopHookWireContract(ReconcileCase):

    def test_the_hook_blocks_through_stdout_and_exits_0(self):
        self.baseline()
        write(os.path.join(self.root, "CLAUDE.md"), "undeclared\n")
        proc = self.run_stop_hook()
        self.assertEqual(proc.returncode, 0,
                         "the Stop hook must block through its decision JSON, not "
                         "through an exit code: %s" % proc.stderr)
        data = json.loads(proc.stdout)
        self.assertEqual(data["decision"], "block")
        self.assertIn("CLAUDE.md", data["reason"])
        self.assertEqual(data["hookSpecificOutput"]["hookEventName"], "Stop")

    def test_a_clean_session_writes_nothing_to_the_decision_channel(self):
        self.baseline()
        proc = self.run_stop_hook()
        self.assertEqual(proc.stdout, "",
                         "a clean session produced a decision: %r" % proc.stdout)

    def test_the_disable_switch_allows_and_says_so(self):
        self.baseline()
        write(os.path.join(self.root, "CLAUDE.md"), "undeclared\n")
        env = dict(os.environ)
        env["BROTHERSBE_RECONCILE_OFF"] = "1"
        payload = json.dumps({"session_id": MY_SESSION, "cwd": self.root})
        proc = subprocess.run([sys.executable, RECONCILE_PATH], input=payload,
                              capture_output=True, text=True, env=env)
        self.assertEqual(proc.stdout, "", "the disable switch did not disable the hook")
        self.assertIn("BROTHERSBE_RECONCILE_OFF is set", proc.stderr)

    def test_help_exits_0_with_usage_on_stderr_and_a_clean_stdout(self):
        for argv in (("-h",), ("--help",)):
            proc = subprocess.run([sys.executable, RECONCILE_PATH] + list(argv),
                                  capture_output=True, text=True)
            self.assertEqual(proc.returncode, 0)
            self.assertIn("usage: sbe_session_reconcile.py", proc.stderr)
            self.assertEqual(proc.stdout, "")

    def test_an_unrecognized_flag_exits_2_instead_of_blocking_a_session(self):
        """`sbe scope --typo` used to reach the Stop hook, block on a missing
        baseline and exit 0, which taught a caller that a typo was a control
        decision. A flag this surface does not know is a usage error."""
        proc = subprocess.run([sys.executable, RECONCILE_PATH, "--no-such-flag"],
                              input="", capture_output=True, text=True)
        self.assertEqual(proc.returncode, 2)
        self.assertEqual(proc.stdout, "", "a typo produced a blocking decision")
        self.assertIn("unrecognized flag", proc.stderr)


class TestRepeatSuppression(ReconcileCase):
    """The 2026-08-29 amendment: an identical, unresolved finding must not
    re-block every Stop, because a session that cannot use any of the three
    recoveries (a peer's legitimate work in a shared tree) otherwise loops
    forever, re-answering the same block. The full block still fires on the
    FIRST occurrence, on ANY change to the finding set, and again after the
    cooldown. Fixture shape from the pending amendment: fire the hook twice
    with an identical finding set and assert the second is the short form; add
    one path and assert the full block returns."""

    def modify_protected(self):
        write(os.path.join(self.root, "CLAUDE.md"), "authority, changed\n")

    def test_an_unchanged_finding_does_not_reblock_the_second_stop(self):
        self.baseline()
        self.modify_protected()
        first = self.run_stop_hook()
        self.assertEqual(first.returncode, 0)
        self.assertIn("Three recoveries", json.loads(first.stdout)["reason"],
                      "the FIRST occurrence must still be the full block")
        second = self.run_stop_hook()
        self.assertEqual(second.returncode, 0)
        self.assertEqual(second.stdout.strip(), "",
                         "an unchanged finding re-blocked the very next Stop, which is "
                         "the loop the amendment exists to end: %r" % second.stdout)
        self.assertIn("UNCHANGED", second.stderr,
                      "the suppressed repeat must still say so on stderr")

    def test_a_changed_finding_set_blocks_in_full_again(self):
        self.baseline()
        self.modify_protected()
        self.run_stop_hook()
        write(os.path.join(self.root, "hooks", "hooks.json"), "{\"changed\": true}\n")
        third = self.run_stop_hook()
        self.assertTrue(third.stdout.strip(),
                        "a NEW path joined the finding and the hook stayed silent")
        reason = json.loads(third.stdout)["reason"]
        self.assertIn("Three recoveries", reason)
        self.assertIn("2 change(s)", reason)

    def test_a_cleared_finding_that_returns_blocks_in_full_again(self):
        self.baseline()
        self.modify_protected()
        self.run_stop_hook()
        self.git("checkout", "--", "CLAUDE.md")
        clean = self.run_stop_hook()
        self.assertEqual(clean.stdout.strip(), "",
                         "a restored tree still blocked: %r" % clean.stdout)
        self.modify_protected()
        again = self.run_stop_hook()
        self.assertIn("Three recoveries",
                      json.loads(again.stdout)["reason"],
                      "a finding resolved and then re-made is new information and "
                      "earns the full block, not the suppressed form")

    def test_the_cooldown_reasserts_the_full_block(self):
        self.baseline()
        self.modify_protected()
        self.run_stop_hook()
        state_dir = os.environ["BROTHERSBE_RECONCILE_STATE_DIR"]
        names = [n for n in os.listdir(state_dir) if n.endswith(".json")]
        self.assertEqual(len(names), 1,
                         "expected exactly one state record, found %r" % names)
        state_path = os.path.join(state_dir, names[0])
        state = json.loads(read(state_path))
        state["lastFullReport"] = float(state["lastFullReport"]) - 3600.0
        write(state_path, json.dumps(state))
        again = self.run_stop_hook()
        self.assertIn("Three recoveries", json.loads(again.stdout)["reason"],
                      "an unchanged finding older than the cooldown must re-block "
                      "in full so a standing violation is never silent forever")


class TestTheBaselineItself(ReconcileCase):

    def test_the_baseline_carries_every_key_the_spec_names(self):
        path = self.baseline()
        data = json.loads(read(path))
        for key in ("schemaVersion", "sessionId", "repositoryRoot", "headCommit",
                    "startedAt", "initialChangedPaths", "openTasks",
                    "allowedOwnedPaths", "fencedPaths", "authorityPaths",
                    "controlPlanePaths"):
            self.assertIn(key, data, "the baseline is missing the key %r" % key)

    def test_the_baseline_lives_under_the_git_directory_and_not_the_worktree(self):
        path = self.baseline()
        self.assertTrue(path.startswith(os.path.join(self.root, ".git")),
                        "the baseline landed at %s, outside the git directory" % path)
        self.assertEqual(self.git("status", "--porcelain"), "",
                         "writing the baseline dirtied the tree it is measuring")

    def test_a_path_holding_a_newline_survives_the_null_delimited_read(self):
        """A line-oriented reader would split this one changed file into two
        paths that do not exist, which is the shape a hostile write would take
        to hide."""
        weird = os.path.join(self.root, "docs", "two\nlines.md")
        write(weird, "x\n")
        entries = sb.changed_entries(self.root)
        paths = [e.path.strip('"') for e in entries]
        self.assertTrue(any("two\\nlines.md" in p or "two\nlines.md" in p for p in paths),
                        "the newline path did not survive the read: %s" % paths)

    def test_the_writer_runs_from_a_sessionstart_shaped_payload_on_stdin(self):
        """The wiring tools/sbe_sessionstart.py uses, exercised end to end
        rather than by calling the Python function the hook never calls."""
        payload = json.dumps({"session_id": "from-stdin-1234", "cwd": self.root,
                              "hook_event_name": "SessionStart"})
        proc = subprocess.run([sys.executable, BASELINE_PATH, "write"], input=payload,
                              capture_output=True, text=True)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertEqual(proc.stdout, "",
                         "the baseline writer wrote to stdout, which SessionStart "
                         "injects into the session context")
        self.assertTrue(os.path.exists(sb.baseline_path(self.root, "from-stdin-1234")),
                        "no baseline was written from the stdin payload: %s" % proc.stderr)


class TestTheCiBackstop(ReconcileCase):
    """Spec 1.5: the same reconciliation over a commit range, with no session
    baseline and no dependency on Claude Code having run locally."""

    def _verify(self, *args):
        return subprocess.run(
            [sys.executable, RECONCILE_PATH, "verify", "--path", self.root] + list(args),
            capture_output=True, text=True)

    def test_a_committed_protected_change_fails_strict_verification(self):
        base = self.git("rev-parse", "HEAD").strip()
        write(os.path.join(self.root, "CLAUDE.md"), "committed undeclared\n")
        self.git("add", "-A")
        self.commit("change the control plane")
        proc = self._verify("--base", base, "--head", "HEAD", "--strict")
        self.assertEqual(proc.returncode, 1,
                         "strict verification exited %d over an undeclared control-plane "
                         "commit: %s" % (proc.returncode, proc.stderr))
        self.assertIn("CLAUDE.md", proc.stderr)

    def test_the_same_range_without_strict_reports_and_exits_0(self):
        base = self.git("rev-parse", "HEAD").strip()
        write(os.path.join(self.root, "CLAUDE.md"), "committed undeclared\n")
        self.git("add", "-A")
        self.commit("change the control plane")
        proc = self._verify("--base", base, "--head", "HEAD")
        self.assertEqual(proc.returncode, 0)
        self.assertIn("VIOLATION", proc.stderr)
        self.assertIn("advisory", proc.stderr)

    def test_an_ordinary_committed_change_passes(self):
        base = self.git("rev-parse", "HEAD").strip()
        write(os.path.join(self.root, "docs", "notes.md"), "ordinary commit\n")
        self.git("add", "-A")
        self.commit("ordinary work")
        proc = self._verify("--base", base, "--head", "HEAD", "--strict")
        self.assertEqual(proc.returncode, 0, proc.stderr)

    def test_a_committed_rename_of_a_protected_file_is_still_detected(self):
        base = self.git("rev-parse", "HEAD").strip()
        self.git("mv", "CLAUDE.md", "CLAUDE-old.md")
        self.commit("rename the control plane")
        proc = self._verify("--base", base, "--head", "HEAD", "--strict")
        self.assertEqual(proc.returncode, 1, proc.stderr)
        self.assertIn("CLAUDE.md", proc.stderr)

    def test_an_unresolvable_base_is_no_data_and_is_neither_a_pass_nor_a_block(self):
        proc = self._verify("--base", "deadbeefdeadbeefdeadbeefdeadbeefdeadbeef",
                            "--head", "HEAD", "--strict")
        self.assertEqual(proc.returncode, 2,
                         "an unresolvable base exited %d; 0 would be a pass over nothing "
                         "and 1 would be a block nobody earned" % proc.returncode)
        self.assertIn("NO-DATA", proc.stderr)

    def test_a_missing_base_flag_is_a_usage_error(self):
        proc = self._verify("--head", "HEAD")
        self.assertEqual(proc.returncode, 2)
        self.assertIn("--base is required", proc.stderr)


if __name__ == "__main__":
    unittest.main(verbosity=2)

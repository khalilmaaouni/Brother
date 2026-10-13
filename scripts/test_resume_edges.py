"""RS1.b: the resume screen names a branch that moved since the checkpoint,
reads NO-DATA (never silence) when either side cannot be read, survives a
torn or garbage journal, and leaves the user's uncommitted edits alone.

Every repository here is a throwaway one made by test_worktree_lane.a_repo.
"""
import contextlib
import hashlib
import io
import json
import os
import subprocess
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import test_worktree_lane as TW  # noqa: E402  (a_repo, tmp sandbox)
import brother_run  # noqa: E402
import continuity  # noqa: E402
import journal  # noqa: E402


def git(repo, *args):
    return subprocess.run(["git"] + list(args), cwd=repo, capture_output=True,
                          text=True, check=True).stdout


def a_run(repo, events=True):
    """A run directory pointed at `repo`: target.json, a Work document with
    one DONE and one pending row, and (events=True) a real journal."""
    run_dir = tempfile.mkdtemp(prefix="run-")
    with open(os.path.join(run_dir, brother_run.TARGET_FILENAME), "w",
              encoding="utf-8") as fh:
        json.dump({"cwd": repo}, fh)
    with open(os.path.join(run_dir, "W-w1.json"), "w", encoding="utf-8") as fh:
        json.dump({"outcome": "two files", "work_id": "w1", "rows": [
            {"id": "U1", "title": "one", "status": "DONE"},
            {"id": "U2", "title": "two", "status": "SCHEDULED"}]}, fh)
    if events:
        journal.append(run_dir, "run.opened", payload={"cwd": repo})
        journal.append(run_dir, "dispatch.round", payload={"slots": 3, "own_tools": False})
    return run_dir


def screen(run_dir):
    out, err = io.StringIO(), io.StringIO()
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
        code = continuity.main([run_dir])
    return code, out.getvalue(), err.getvalue()


def checkpoint(run_dir):
    ok, problem = continuity.write_capsule(run_dir)
    if not ok:
        raise AssertionError("fixture checkpoint failed: %s" % problem)


class TheCapsuleRecordsItsBranch(unittest.TestCase):
    """REQ-RS1-09."""

    def test_on_main(self):
        repo = TW.a_repo()
        cap, problem = continuity.capsule(a_run(repo))
        self.assertEqual(problem, "")
        self.assertEqual(cap["branch"], "main")
        self.assertNotIn("branch", cap["zone3"])

    def test_on_a_feature_branch_with_a_slash_and_unicode(self):
        repo = TW.a_repo()
        git(repo, "checkout", "-q", "-b", "feat/café")
        cap, _ = continuity.capsule(a_run(repo))
        self.assertEqual(cap["branch"], "feat/café")

    def test_a_target_that_is_not_a_repository_is_NO_DATA(self):
        not_repo = tempfile.mkdtemp(prefix="plain-")
        cap, _ = continuity.capsule(a_run(not_repo))
        self.assertIsNotNone(cap)
        self.assertEqual(cap["branch"], continuity.NODATA)
        self.assertIsNone(continuity.current_branch(not_repo))


class TheBranchMoves(unittest.TestCase):
    """REQ-RS1-10 and 11, on the screen main() prints."""

    def test_a_changed_branch_is_named_with_both_names(self):
        repo = TW.a_repo()
        run_dir = a_run(repo)
        checkpoint(run_dir)
        git(repo, "checkout", "-q", "-b", "other")
        code, out, _ = screen(run_dir)
        self.assertEqual(code, 0)
        self.assertIn("BRANCH CHANGED since this checkpoint: it was main, it is other", out)
        lines = out.splitlines()
        rev = [i for i, l in enumerate(lines) if l.startswith("canonical revision:")][0]
        self.assertTrue(lines[rev + 1].startswith("BRANCH CHANGED"),
                        "the drift line sits right after the canonical revision")

    def test_an_unchanged_branch_prints_no_warning(self):
        repo = TW.a_repo()
        run_dir = a_run(repo)
        checkpoint(run_dir)
        _, out, _ = screen(run_dir)
        self.assertNotIn("BRANCH", out)
        self.assertNotIn("branch NO-DATA", out)

    def test_a_detached_head_is_NO_DATA_never_equal(self):
        repo = TW.a_repo()
        run_dir = a_run(repo)
        checkpoint(run_dir)
        git(repo, "checkout", "-q", "--detach")
        _, out, _ = screen(run_dir)
        self.assertIn("branch NO-DATA: the live checkout's branch could not be read", out)

    def test_a_checkpoint_from_before_the_field_is_NO_DATA(self):
        repo = TW.a_repo()
        run_dir = a_run(repo)
        checkpoint(run_dir)
        path = os.path.join(run_dir, continuity.CAPSULE_FILENAME)
        with open(path, encoding="utf-8") as fh:
            old = json.load(fh)
        del old["branch"]
        with open(path, "w", encoding="utf-8") as fh:
            json.dump(old, fh)
        _, out, _ = screen(run_dir)
        self.assertIn("branch NO-DATA: this checkpoint predates the field", out)

    def test_the_drift_rule_itself(self):
        d = continuity.branch_drift
        self.assertEqual(d("main", "main"), "")
        self.assertIn("it was main, it is x", d("main", "x"))
        self.assertIn("NO-DATA", d(None, "main"))
        self.assertIn("NO-DATA", d("main", None))
        self.assertIn("NO-DATA", d(continuity.NODATA, continuity.NODATA))
        self.assertIn("NO-DATA", d(None, None))

    def test_printing_twice_is_identical(self):
        repo = TW.a_repo()
        run_dir = a_run(repo)
        checkpoint(run_dir)
        git(repo, "checkout", "-q", "-b", "other")
        self.assertEqual(screen(run_dir)[1], screen(run_dir)[1])


class TheJournalIsDamaged(unittest.TestCase):
    """REQ-RS1-12 and 13."""

    def test_a_torn_tail_still_yields_a_capsule_from_every_earlier_event(self):
        repo = TW.a_repo()
        run_dir = a_run(repo)
        os.remove(os.path.join(run_dir, brother_run.TARGET_FILENAME))  # cwd must come from the journal
        with open(os.path.join(run_dir, journal.JOURNAL_FILENAME), "a",
                  encoding="utf-8") as fh:
            fh.write('{"type": "unit.claimed", "payload": {"uni')
        err = io.StringIO()
        with contextlib.redirect_stderr(err):
            cap, problem = continuity.capsule(run_dir)
        self.assertEqual(problem, "")
        self.assertIn("torn line 3", err.getvalue())
        self.assertEqual(cap["environment"]["target_cwd"], repo, "run.opened was used")
        self.assertEqual(cap["environment"]["slots"], 3, "dispatch.round was used")
        self.assertEqual(cap["branch"], "main")

    def test_an_all_garbage_journal_yields_a_capsule_not_an_exception(self):
        repo = TW.a_repo()
        run_dir = a_run(repo, events=False)
        with open(os.path.join(run_dir, journal.JOURNAL_FILENAME), "w",
                  encoding="utf-8") as fh:
            fh.write("\x00garbage\n{not json\n]]]\n")
        with contextlib.redirect_stderr(io.StringIO()):
            cap, problem = continuity.capsule(run_dir)
            code, _out, _err = screen(run_dir)
        self.assertEqual(problem, "")
        self.assertIsNotNone(cap)
        self.assertEqual(code, 0)

    def test_an_empty_journal_yields_a_capsule(self):
        repo = TW.a_repo()
        run_dir = a_run(repo, events=False)
        open(os.path.join(run_dir, journal.JOURNAL_FILENAME), "w").close()
        cap, problem = continuity.capsule(run_dir)
        self.assertEqual(problem, "")
        self.assertEqual(cap["branch"], "main")

    def test_a_target_json_that_is_an_array_still_yields_a_capsule(self):
        repo = TW.a_repo()
        run_dir = a_run(repo)
        with open(os.path.join(run_dir, brother_run.TARGET_FILENAME), "w",
                  encoding="utf-8") as fh:
            json.dump([repo], fh)
        with contextlib.redirect_stderr(io.StringIO()):
            cap, problem = continuity.capsule(run_dir)
        self.assertEqual(problem, "")
        self.assertEqual(cap["environment"]["target_cwd"], repo, "the journal's run.opened is the fallback")

    def test_no_journal_keeps_the_existing_refusal(self):
        repo = TW.a_repo()
        cap, problem = continuity.capsule(a_run(repo, events=False))
        self.assertIsNone(cap)
        self.assertIn("NO-DATA", problem)


class TheUsersEditsSurvive(unittest.TestCase):
    """REQ-RS1-14."""

    def _hashes(self, repo):
        out = {}
        for name in ("canonical.txt", "untracked-note.txt"):
            with open(os.path.join(repo, name), "rb") as fh:
                out[name] = hashlib.sha256(fh.read()).hexdigest()
        return out

    def test_a_checkpoint_and_a_screen_leave_both_files_byte_identical(self):
        repo = TW.a_repo()
        with open(os.path.join(repo, "canonical.txt"), "a", encoding="utf-8") as fh:
            fh.write("a user's uncommitted edit\n")
        with open(os.path.join(repo, "untracked-note.txt"), "w", encoding="utf-8") as fh:
            fh.write("never added\n")
        before = self._hashes(repo)
        status_before = git(repo, "status", "--porcelain")
        run_dir = a_run(repo)
        checkpoint(run_dir)
        screen(run_dir)
        self.assertEqual(self._hashes(repo), before)
        self.assertEqual(git(repo, "status", "--porcelain"), status_before)


if __name__ == "__main__":
    unittest.main()

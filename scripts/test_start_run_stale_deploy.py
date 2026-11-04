#!/usr/bin/env python3
"""STAB1.a, frozen before the implementation: a stale loop install refuses to launch.

THE CONTRACT. scripts/loop/start_run.sh refuses START with step stale-deploy when the deploy stamp's revision does
not contain the line's newest scripts/loop commit, naming deploy_stamped as the recovery. An unreadable stamp refuses
too. Measured 2026-11-01: the installed bin's stamp named a revision 356 commits behind the line, 34 of them under
scripts/loop, and the launcher started anyway.

THE STAMP, as the writer and its readers already name it: $BROTHER_DEPLOY_TARGET/.deploy-stamp.json, default
$HOME/.claude/bin (scripts/loop/deploy_stamped.py writes {"schema": 1, "revision": <full sha>, ...};
scripts/builder_preflight.sh reads the same path). Every case writes it under the fixture's own HOME AND sets
BROTHER_DEPLOY_TARGET to that same folder, so either reading finds the fixture's stamp and nothing here reads the
real one. "The line" is the launch worktree's HEAD (the tree step has already proved it equal to its upstream); its
newest scripts/loop commit is `git log -1 HEAD -- scripts/loop`, and "contains" means that commit is an ancestor of,
or is, the stamp's revision.

FIXTURE. scripts/loop/test_start_run.py's own (a launch worktree over a bare upstream, stub intake, canary, driver and
live check through START_RUN_INTAKE, START_RUN_CANARY, START_RUN_DRIVER, START_RUN_LIVE_CHECK, evidence under
START_RUN_EVIDENCE, a temp HOME), reused by reference as scripts/loop/test_start_run_remedy.py does. Runs are never
--dry, so the check is found wherever it sits before the driver. One condition per case: the stamp is the only thing
that differs between a case that starts and a case that is refused.

LIVES IN scripts/, not scripts/loop/: gate row loop-owns-complete refuses a scripts/loop test file no plan names
(precedent: scripts/test_pool_room_limits.py).

OUT OF SCOPE, named: a stamp revision on another branch that contains the loop commit but is not an ancestor of HEAD
(a deploy from a sibling line; the contract speaks only of containment); a line with no scripts/loop commit at all.

Python 3.9 and 3.13, standard library only, no network.
"""
import json
import os
import subprocess
import sys
import unittest

LOOP = os.path.join(os.path.dirname(os.path.abspath(__file__)), "loop")
sys.path.insert(0, LOOP)
import test_start_run as TSR  # noqa: E402  the launcher fixture of record

SCRIPT = TSR.SCRIPT   # read at call time, so a proof run can point the cases at a scratch copy without an env seam


class StaleDeploy(unittest.TestCase):
    setUp_fixture = TSR.StartRunTest.setUp
    _git_env = TSR.StartRunTest._git_env
    git = TSR.StartRunTest.git
    _env = TSR.StartRunTest._env

    def setUp(self):
        self.setUp_fixture()
        self.target = os.path.join(self.home, ".claude", "bin")
        os.makedirs(self.target)
        self.stamp_path = os.path.join(self.target, ".deploy-stamp.json")

    # fixture helpers ----------------------------------------------------------------------------------------------
    def commit(self, rel, text):
        path = os.path.join(self.repo, rel)
        os.makedirs(os.path.dirname(path) or self.repo, exist_ok=True)
        TSR._write(path, text)
        self.git("add", rel)
        self.git("commit", "-q", "-m", "fixture " + rel)
        self.git("push", "-q", "hub", "main:main")
        return self.head()

    def head(self):
        return subprocess.run(["git", "rev-parse", "HEAD"], cwd=self.repo, env=self._git_env(), check=True,
                              stdout=subprocess.PIPE, universal_newlines=True).stdout.strip()

    def stamp(self, revision=None, raw=None):
        body = raw if raw is not None else json.dumps(
            {"schema": 1, "revision": revision, "source_dirty": False, "source": self.repo, "files": {}})
        TSR._write(self.stamp_path, body)

    def launch(self):
        env = self._env(BROTHER_DEPLOY_TARGET=self.target, START_RUN_TIMING_WAIT_S="5")
        p = subprocess.run(["bash", SCRIPT, "--deadline", TSR.DATED, "--budget-usd", "5"], cwd=self.repo, env=env,
                           stdout=subprocess.PIPE, stderr=subprocess.STDOUT, universal_newlines=True, timeout=120)
        return p.returncode, p.stdout

    def started(self):
        """What the stubs record when they run: the intake's argv, the canary's TMPDIR, the driver's argv."""
        return [n for n in ("intake-argv.txt", "canary-tmpdir.txt", "driver-argv.txt")
                if os.path.exists(os.path.join(self.evd, n))]

    def assertStaleRefused(self, result, recovery=True):
        rc, out = result
        self.assertEqual(rc, 2, out)
        self.assertIn("START REFUSED [stale-deploy]", out)
        if recovery:
            self.assertIn("deploy_stamped", out.split("START REFUSED [stale-deploy]", 1)[1])
        self.assertNotIn("STARTED", out)

    def assertNotStale(self, result):
        rc, out = result
        self.assertNotIn("stale-deploy", out)
        # not vacuous: the launch went all the way through the stubs, so no earlier guard hid the check
        self.assertEqual(rc, 0, out)
        self.assertIn("STARTED", out)

    def line_with_two_loop_commits(self):
        """README, then two scripts/loop commits; returns (older loop commit, newest loop commit = HEAD)."""
        older = self.commit("scripts/loop/tool_a.py", "a = 1\n")
        newest = self.commit("scripts/loop/tool_b.py", "b = 1\n")
        return older, newest

    # 1 ------------------------------------------------------------------------------------------------------------
    def test_1_stamp_equal_to_the_newest_loop_commit_starts(self):
        _, newest = self.line_with_two_loop_commits()
        self.assertEqual(newest, self.head())
        self.stamp(newest)
        self.assertNotStale(self.launch())

    # 2 ------------------------------------------------------------------------------------------------------------
    def test_2_stamp_older_than_the_newest_loop_commit_is_refused(self):
        older, _ = self.line_with_two_loop_commits()
        self.stamp(older)   # readable, in the repository, an ancestor of HEAD: only the containment can refuse it
        self.assertStaleRefused(self.launch())

    # 3 ------------------------------------------------------------------------------------------------------------
    def test_3_later_commits_outside_scripts_loop_do_not_make_the_stamp_stale(self):
        _, newest = self.line_with_two_loop_commits()
        self.commit("docs/notes.md", "notes\n")
        self.commit("scripts/other_tool.py", "c = 1\n")   # scripts/ but not scripts/loop/
        self.assertNotEqual(newest, self.head())
        self.stamp(newest)
        self.assertNotStale(self.launch())

    # 4 ------------------------------------------------------------------------------------------------------------
    def test_4_missing_stamp_is_refused(self):
        self.line_with_two_loop_commits()
        self.assertFalse(os.path.exists(self.stamp_path))
        self.assertStaleRefused(self.launch())

    # 5 ------------------------------------------------------------------------------------------------------------
    def test_5_unreadable_stamp_is_refused(self):
        _, newest = self.line_with_two_loop_commits()
        for name, raw in (("truncated json", '{"schema": 1, "revision": "%s"' % newest),
                          ("no revision field", json.dumps({"schema": 1, "files": {}}))):
            with self.subTest(stamp=name):
                self.stamp(raw=raw)
                self.assertStaleRefused(self.launch(), recovery=False)

    # 6 ------------------------------------------------------------------------------------------------------------
    def test_6_stamp_revision_not_in_the_repository_is_refused(self):
        self.line_with_two_loop_commits()
        # a well formed sha nobody committed here, and a ref name, which is not a recorded revision even though git
        # would resolve it to the line's own HEAD
        for revision in ("deadbeef" * 5, "HEAD"):
            with self.subTest(revision=revision):
                self.stamp(revision)
                self.assertStaleRefused(self.launch(), recovery=False)

    # 7 ------------------------------------------------------------------------------------------------------------
    def test_7_the_refusal_comes_before_anything_is_started(self):
        older, _ = self.line_with_two_loop_commits()
        self.stamp(older)
        result = self.launch()
        self.assertEqual(self.started(), [], result[1])
        self.assertStaleRefused(result)


if __name__ == "__main__":
    unittest.main()

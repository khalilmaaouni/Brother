#!/usr/bin/env python3
"""Drives scripts/required_fast_local.py backwards with a scripted fake git/
gh runner, never a real checkout or network call. The fake mimics the one
piece of real git statefulness this script depends on (FETCH_HEAD following
the ref last fetched, a worktree's HEAD following the sha it was created
at) so the tests exercise the same sequencing the real tool relies on.

Exit contract: 0 all assertions pass, 1 an assertion failed.
Python 3, stdlib only. No em or en dashes anywhere in this file.
"""
import json
import os
import subprocess
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import required_fast_local as R  # noqa: E402

try:
    import tmp_sandbox
    tmp_sandbox.install()
except ImportError:
    sys.stderr.write("tmp_sandbox absent: %s leaves its temp trees behind\n"
                     % os.path.basename(__file__))

PR = 751
REPO = "khalilmaaouni/brother-hub"
HEAD_SHA = "1111111111111111111111111111111111111a"
BASE_SHA = "2222222222222222222222222222222222222b"
BASE_BRANCH = "main"

FAST_OK = "pass 30   fail 0   no-data 0\n"
FAST_INHERITED_ONLY = "pass 29   fail 1   no-data 0\nFAILED: legacy-check\n"
FAST_ONE_NEW = "pass 28   fail 2   no-data 0\nFAILED: legacy-check new-check\n"
FAST_NODATA = "pass 25   fail 0   no-data 1\nNO-DATA: flaky-check  (not a pass, and not a failure)\n"
FAST_NO_SUMMARY = "the battery crashed before printing anything\n"


class FakeRunner(object):
    """Answers gh/git/sh calls the way real git and gh would, tracking just
    enough state (last fetched ref, which sha each worktree path was made
    at) to make FETCH_HEAD and a worktree's own HEAD resolve correctly.

    `pr_views` is a list of (code, stdout) for successive `gh pr view`
    calls (index clamped at the last entry, so one entry answers every
    call). `fetch_shas` maps a fetched ref to the sha FETCH_HEAD resolves
    to. `gate_output` maps a sha to (code, stdout) for `sh
    required_fast.sh` run in that sha's worktree. `post_result` answers
    `gh api ... statuses`.
    """

    def __init__(self, pr_views, fetch_shas, gate_output, post_result=(0, "")):
        self.pr_views = list(pr_views)
        self.fetch_shas = dict(fetch_shas)
        self.gate_output = dict(gate_output)
        self.post_result = post_result
        self._pr_call = 0
        self._last_ref = None
        self._path_sha = {}
        self.calls = []
        self.removed = []
        self.posts = []

    def __call__(self, cmd, cwd=None, **kw):
        self.calls.append((list(cmd), cwd))
        if cmd[0] == "gh" and cmd[1] == "pr":
            i = min(self._pr_call, len(self.pr_views) - 1)
            self._pr_call += 1
            code, out = self.pr_views[i]
            return subprocess.CompletedProcess(cmd, code, out, "")
        if cmd[0] == "gh" and cmd[1] == "api":
            self.posts.append(cmd)
            code, out = self.post_result
            return subprocess.CompletedProcess(cmd, code, out, "")
        if cmd[:2] == ["git", "fetch"]:
            self._last_ref = cmd[-1]
            return subprocess.CompletedProcess(cmd, 0, "", "")
        if cmd[:2] == ["git", "rev-parse"] and cmd[2:3] == ["FETCH_HEAD"]:
            sha = self.fetch_shas.get(self._last_ref)
            if sha is None:
                return subprocess.CompletedProcess(cmd, 1, "", "unknown ref %s" % self._last_ref)
            return subprocess.CompletedProcess(cmd, 0, sha + "\n", "")
        if cmd[:3] == ["git", "worktree", "add"]:
            path, sha = cmd[-2], cmd[-1]
            self._path_sha[path] = sha
            return subprocess.CompletedProcess(cmd, 0, "", "")
        if cmd[:3] == ["git", "worktree", "remove"]:
            self.removed.append(cmd[-1])
            return subprocess.CompletedProcess(cmd, 0, "", "")
        if cmd[:2] == ["git", "rev-parse"] and cmd[2:3] == ["HEAD"]:
            return subprocess.CompletedProcess(cmd, 0, self._path_sha.get(cwd, "") + "\n", "")
        if cmd[:2] == ["git", "status"]:
            return subprocess.CompletedProcess(cmd, 0, "", "")
        if cmd[0] == "sh":
            sha = self._path_sha.get(cwd, "")
            code, out = self.gate_output.get(sha, (0, FAST_OK))
            return subprocess.CompletedProcess(cmd, code, out, "")
        raise AssertionError("unhandled fake command: %r (cwd=%r)" % (cmd, cwd))


def _pr_view_ok(head_sha=HEAD_SHA, base=BASE_BRANCH):
    return (0, json.dumps({"headRefOid": head_sha, "baseRefName": base}))


class EvaluateTests(unittest.TestCase):

    def _evaluate(self, gate_output, evidence_dir, pr_views=None):
        runner = FakeRunner(
            pr_views or [_pr_view_ok()],
            fetch_shas={BASE_BRANCH: BASE_SHA, BASE_SHA: BASE_SHA, HEAD_SHA: HEAD_SHA},
            gate_output=gate_output)
        result = R.evaluate("/fake/root", REPO, PR, evidence_dir=evidence_dir,
                            runner=runner)
        return result, runner

    def test_success_with_inherited_reds_only(self):
        with self._tmpdir() as d:
            result, runner = self._evaluate(
                {BASE_SHA: (0, FAST_INHERITED_ONLY), HEAD_SHA: (0, FAST_INHERITED_ONLY)}, d)
        self.assertEqual(result["state"], "success")
        self.assertEqual(result["sha"], HEAD_SHA)
        self.assertIn("inherited 1, new 0", result["description"])
        # both worktrees created were removed
        self.assertEqual(len(runner.removed), 2)

    def test_failure_with_one_new_red(self):
        with self._tmpdir() as d:
            result, _ = self._evaluate(
                {BASE_SHA: (0, FAST_INHERITED_ONLY), HEAD_SHA: (0, FAST_ONE_NEW)}, d)
        self.assertEqual(result["state"], "failure")
        self.assertIn("inherited 1, new 1", result["description"])
        self.assertTrue(any("new-check" in l for l in result["lines"]))

    def test_nodata_when_summary_missing(self):
        with self._tmpdir() as d:
            result, _ = self._evaluate(
                {BASE_SHA: (0, FAST_OK), HEAD_SHA: (0, FAST_NO_SUMMARY)}, d)
        self.assertEqual(result["state"], "error")
        self.assertTrue(any(l.startswith("NO-DATA:") for l in result["lines"]))

    def test_unnamed_failures_are_nodata_never_success(self):
        """`fail 2` with no FAILED: line cannot be split into inherited and
        new; before this guard it read as success with two reds."""
        unnamed = "pass 28   fail 2   no-data 0\n"
        with self._tmpdir() as d:
            result, _ = self._evaluate(
                {BASE_SHA: (0, FAST_OK), HEAD_SHA: (1, unnamed)}, d)
        self.assertEqual(result["state"], "error")
        self.assertTrue(any("without naming them" in l for l in result["lines"]))

    def test_nodata_when_pr_run_has_nodata_checks(self):
        with self._tmpdir() as d:
            result, _ = self._evaluate(
                {BASE_SHA: (0, FAST_OK), HEAD_SHA: (0, FAST_NODATA)}, d)
        self.assertEqual(result["state"], "error")

    def test_base_run_cached_across_evaluate_calls(self):
        with self._tmpdir() as d:
            runner1 = FakeRunner(
                [_pr_view_ok()], {BASE_BRANCH: BASE_SHA, BASE_SHA: BASE_SHA, HEAD_SHA: HEAD_SHA},
                {BASE_SHA: (0, FAST_OK), HEAD_SHA: (0, FAST_OK)})
            R.evaluate("/fake/root", REPO, PR, evidence_dir=d, runner=runner1)
            cache = R._cache_path(d, BASE_SHA)
            self.assertTrue(os.path.isfile(cache))
            # second run: base gate output deliberately wrong so a cache
            # miss would be caught by the changed FAILED name below
            runner2 = FakeRunner(
                [_pr_view_ok()], {BASE_BRANCH: BASE_SHA, BASE_SHA: BASE_SHA, HEAD_SHA: HEAD_SHA},
                {BASE_SHA: (0, FAST_ONE_NEW), HEAD_SHA: (0, FAST_OK)})
            R.evaluate("/fake/root", REPO, PR, evidence_dir=d, runner=runner2)
            # the base worktree/gate must not have been touched the second time
            self.assertFalse(any(c[0][:2] == ["git", "worktree"] and BASE_SHA in c[0]
                                 for c in runner2.calls))

    def test_crash_during_gate_still_removes_worktree(self):
        class RaisingRunner(FakeRunner):
            def __call__(self, cmd, cwd=None, **kw):
                if cmd[0] == "sh" and self._path_sha.get(cwd) == HEAD_SHA:
                    raise RuntimeError("simulated crash mid-gate")
                return super(RaisingRunner, self).__call__(cmd, cwd=cwd, **kw)

        with self._tmpdir() as d:
            runner = RaisingRunner(
                [_pr_view_ok()], {BASE_BRANCH: BASE_SHA, BASE_SHA: BASE_SHA, HEAD_SHA: HEAD_SHA},
                {BASE_SHA: (0, FAST_OK), HEAD_SHA: (0, FAST_OK)})
            with self.assertRaises(RuntimeError):
                R.evaluate("/fake/root", REPO, PR, evidence_dir=d, runner=runner)
        # both the base worktree (ran fine) and the PR worktree (crashed
        # mid-gate) were still removed
        self.assertEqual(len(runner.removed), 2)

    def _tmpdir(self):
        import tempfile
        import shutil
        import contextlib
        d = tempfile.mkdtemp(prefix="rfl-test-evidence-")

        @contextlib.contextmanager
        def cm():
            try:
                yield d
            finally:
                shutil.rmtree(d, ignore_errors=True)
        return cm()


class MainPostTests(unittest.TestCase):
    """Every R.main() call below passes evidence_dir=self.evidence_dir: the
    real ~/.claude/evidence must never receive a test's log/cache files."""

    def setUp(self):
        import tempfile
        self.evidence_dir = tempfile.mkdtemp(prefix="rfl-test-main-evidence-")

    def tearDown(self):
        import shutil
        shutil.rmtree(self.evidence_dir, ignore_errors=True)

    def _runner(self, pr_views, gate_output):
        return FakeRunner(pr_views, {BASE_BRANCH: BASE_SHA, BASE_SHA: BASE_SHA, HEAD_SHA: HEAD_SHA},
                          gate_output)

    def test_post_issues_exactly_one_statuses_post(self):
        runner = self._runner([_pr_view_ok(), _pr_view_ok()],
                              {BASE_SHA: (0, FAST_OK), HEAD_SHA: (0, FAST_OK)})
        code = R.main(["--pr", str(PR), "--repo", REPO, "--post"], runner=runner,
                      evidence_dir=self.evidence_dir)
        self.assertEqual(code, R.EXIT_OK)
        self.assertEqual(len(runner.posts), 1)
        cmd = runner.posts[0]
        self.assertIn("repos/%s/statuses/%s" % (REPO, HEAD_SHA), cmd)
        self.assertIn("state=success", cmd)
        self.assertIn("context=%s" % R.CONTEXT, cmd)

    def test_without_post_nothing_is_posted(self):
        runner = self._runner([_pr_view_ok()],
                              {BASE_SHA: (0, FAST_OK), HEAD_SHA: (0, FAST_OK)})
        code = R.main(["--pr", str(PR), "--repo", REPO], runner=runner,
                      evidence_dir=self.evidence_dir)
        self.assertEqual(code, R.EXIT_OK)
        self.assertEqual(runner.posts, [])

    def test_head_moved_before_post_skips_post(self):
        moved_sha = "3333333333333333333333333333333333333c"
        runner = self._runner(
            [_pr_view_ok(), _pr_view_ok(head_sha=moved_sha)],
            {BASE_SHA: (0, FAST_OK), HEAD_SHA: (0, FAST_OK)})
        code = R.main(["--pr", str(PR), "--repo", REPO, "--post"], runner=runner,
                      evidence_dir=self.evidence_dir)
        self.assertEqual(code, R.EXIT_NODATA)
        self.assertEqual(runner.posts, [])

    def test_failed_post_exits_nodata(self):
        runner = self._runner([_pr_view_ok(), _pr_view_ok()],
                              {BASE_SHA: (0, FAST_OK), HEAD_SHA: (0, FAST_OK)})
        runner.post_result = (1, "gh: some error")
        code = R.main(["--pr", str(PR), "--repo", REPO, "--post"], runner=runner,
                      evidence_dir=self.evidence_dir)
        self.assertEqual(code, R.EXIT_NODATA)
        self.assertEqual(len(runner.posts), 1)

    def test_failure_state_exit_code(self):
        runner = self._runner([_pr_view_ok()],
                              {BASE_SHA: (0, FAST_INHERITED_ONLY), HEAD_SHA: (0, FAST_ONE_NEW)})
        code = R.main(["--pr", str(PR), "--repo", REPO], runner=runner,
                      evidence_dir=self.evidence_dir)
        self.assertEqual(code, R.EXIT_FAILURE)


if __name__ == "__main__":
    unittest.main()

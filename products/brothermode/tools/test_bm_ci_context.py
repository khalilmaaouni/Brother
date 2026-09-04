#!/usr/bin/env python3
"""Suite for scripts/bm_ci_context.py, the CI revision-identity capture, and
for the pull request pipeline that depends on it.

Standard library only. Run: python3 tools/test_bm_ci_context.py

WHAT THIS SUITE DEFENDS

  1. A pull request run that cannot name its pull request id, its source
     commit or its destination commit REFUSES, before the gate runs. That
     refusal is the whole point of the tool: a verdict about an unknown tree
     is worse than no verdict, because it looks like one.
  2. Declared and measured identities stay APART. The environment says what
     the provider believes; git says what the runner actually had checked
     out. Bitbucket prepares pull request builds itself and does not promise
     the two agree, so the record carries both and states whether they
     matched rather than quietly using one for the other.
  3. Absent is not empty. A variable that is unset and a variable set to the
     empty string both read as absence here, and neither reads as a value.
  4. A shallow clone that cannot resolve the destination commit is reported
     with the fix, not silently tolerated: a merge base computed against a
     commit that is not present would compare something else entirely.
  5. bitbucket-pipelines.yml actually wires the pull request leg, and wires
     the capture BEFORE the gate. A capture that ran after the battery would
     be a receipt written about a verdict already published.

WHAT IT DOES NOT CLAIM. Nothing here has run on Bitbucket Cloud. No mirror
with Pipelines enabled exists, so whether Bitbucket triggers the pull
request pipeline at all is UNVERIFIED and is closed by the first real run
and by nothing in this file.

No em or en dashes anywhere in this file, its comments, or its output.
"""

import importlib.util
import io
import json
import os
import re
import subprocess
import sys
import tempfile
import unittest

# E100: one sandbox for every temp tree this process makes, removed at exit.
import os as _e100_os, sys as _e100_sys  # noqa: E402
_e100_sys.path.append(_e100_os.path.join(
    _e100_os.path.dirname(_e100_os.path.abspath(__file__)), '../../../scripts'))
try:  # noqa: E402
    import tmp_sandbox as _e100_tmp
    _e100_tmp.install()
except ImportError:
    # A packager (scripts/export_public.py, make_benchmark_bundle.py)
    # can copy this test without scripts/tmp_sandbox.py beside it. Say
    # so rather than dying: the sandbox is hygiene, not the subject.
    _e100_sys.stderr.write(
        "tmp_sandbox absent: %s leaves its temp trees behind\n"
        % _e100_os.path.basename(__file__))

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
TOOL = os.path.join(ROOT, "scripts", "bm_ci_context.py")
PIPELINES = os.path.join(ROOT, "bitbucket-pipelines.yml")

_spec = importlib.util.spec_from_file_location("bm_ci_context", TOOL)
ctx = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(ctx)

_seam_spec = importlib.util.spec_from_file_location(
    "bm_export_seam", os.path.join(HERE, "bm_export_seam.py"))
_seam = importlib.util.module_from_spec(_seam_spec)
_seam_spec.loader.exec_module(_seam)


def _read(path):
    with io.open(path, encoding="utf-8") as fh:
        return fh.read()


def _git(args, cwd):
    return subprocess.run(["git"] + list(args), cwd=cwd,
                          stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                          universal_newlines=True, timeout=60)


class _RepoCase(unittest.TestCase):
    """A throwaway two-commit repository, so the history checks have real
    commits to find and a real one to miss."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="bm-ci-context-")
        env = dict(os.environ, GIT_AUTHOR_NAME="t",
                   GIT_AUTHOR_EMAIL="t@example.com", GIT_COMMITTER_NAME="t",
                   GIT_COMMITTER_EMAIL="t@example.com")
        subprocess.run(["git", "init", "-q", self.tmp], env=env, timeout=60,
                       check=True)
        with io.open(os.path.join(self.tmp, "a.txt"), "w",
                     encoding="utf-8") as fh:
            fh.write("one\n")
        _git(["add", "-A"], self.tmp)
        subprocess.run(["git", "commit", "-qm", "one"], cwd=self.tmp, env=env,
                       timeout=60, check=True)
        self.base = _git(["rev-parse", "HEAD"], self.tmp).stdout.strip()
        with io.open(os.path.join(self.tmp, "a.txt"), "w",
                     encoding="utf-8") as fh:
            fh.write("two\n")
        _git(["add", "-A"], self.tmp)
        subprocess.run(["git", "commit", "-qm", "two"], cwd=self.tmp, env=env,
                       timeout=60, check=True)
        self.head = _git(["rev-parse", "HEAD"], self.tmp).stdout.strip()

    def tearDown(self):
        import shutil
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _pr_env(self, **overrides):
        env = {
            "BITBUCKET_PR_ID": "184",
            "BITBUCKET_COMMIT": self.head,
            "BITBUCKET_BRANCH": "feature/example",
            "BITBUCKET_PR_DESTINATION_BRANCH": "main",
            "BITBUCKET_PR_DESTINATION_COMMIT": self.base,
            "BITBUCKET_WORKSPACE": "kmaaouni",
            "BITBUCKET_REPO_SLUG": "brothermodeup",
            "BITBUCKET_REPO_FULL_NAME": "kmaaouni/brothermodeup",
            "BITBUCKET_PIPELINE_UUID": "{pipeline}",
            "BITBUCKET_STEP_UUID": "{step}",
        }
        env.update(overrides)
        return env


class TestPullRequestCapture(_RepoCase):

    def test_a_complete_pull_request_context_has_no_problems(self):
        record = ctx.capture("bitbucket", self._pr_env(), self.tmp)
        self.assertEqual(record["mode"], "pull_request")
        self.assertEqual(record["pullRequestId"], "184")
        self.assertEqual(record["sourceCommit"], self.head)
        self.assertEqual(record["destinationCommit"], self.base)
        self.assertEqual(record["testedCommit"], self.head)
        self.assertTrue(record["testedTree"])
        self.assertIs(record["destinationCommitPresentLocally"], True)
        self.assertEqual(ctx.problems(record), [])

    def test_each_missing_required_field_is_named_on_its_own(self):
        """One assertion per field rather than one for all three: a check
        that only ever reported the first missing value would let the
        second be fixed and the third stay silent."""
        for variable, key in (("BITBUCKET_PR_ID", "pullRequestId"),
                              ("BITBUCKET_COMMIT", "sourceCommit"),
                              ("BITBUCKET_PR_DESTINATION_COMMIT",
                               "destinationCommit")):
            env = self._pr_env(**{variable: ""})
            record = ctx.capture("bitbucket", env, self.tmp)
            found = ctx.problems(record)
            if variable == "BITBUCKET_PR_ID":
                # Without a pull request id there is no pull request mode to
                # be in, so the honest answer is branch mode with nothing
                # required, NOT a refusal about a pull request that is not
                # happening.
                self.assertEqual(record["mode"], "branch")
                self.assertEqual(found, [])
                continue
            self.assertTrue(found, "%s empty produced no problem" % variable)
            self.assertTrue(any(key in problem for problem in found),
                            "no problem named %s: %r" % (key, found))
            self.assertTrue(any("HOW TO FIX" in p for p in found),
                            "a refusal with no remedy: %r" % found)

    def test_an_unset_and_an_empty_variable_read_the_same(self):
        env = self._pr_env()
        del env["BITBUCKET_PR_DESTINATION_COMMIT"]
        unset = ctx.capture("bitbucket", env, self.tmp)
        empty = ctx.capture(
            "bitbucket", self._pr_env(BITBUCKET_PR_DESTINATION_COMMIT="   "),
            self.tmp)
        self.assertIsNone(unset["destinationCommit"])
        self.assertIsNone(empty["destinationCommit"])
        self.assertEqual(ctx.problems(unset), ctx.problems(empty))

    def test_a_destination_commit_this_clone_lacks_is_reported_with_the_fix(self):
        env = self._pr_env(
            BITBUCKET_PR_DESTINATION_COMMIT="0" * 40)
        record = ctx.capture("bitbucket", env, self.tmp)
        self.assertIs(record["destinationCommitPresentLocally"], False)
        found = ctx.problems(record)
        self.assertTrue(any("merge base" in p for p in found), found)
        self.assertTrue(any("full-history" in p for p in found), found)

    def test_a_present_destination_commit_is_not_reported_missing(self):
        """The calibration for the check above, and it exists because the
        first draft of the tool got this exact case backwards: `git cat-file
        -e` prints NOTHING on success, and reading empty output as failure
        made every present commit look absent, which would have failed a
        healthy pipeline."""
        record = ctx.capture("bitbucket", self._pr_env(), self.tmp)
        self.assertIs(record["destinationCommitPresentLocally"], True)
        self.assertEqual(
            [p for p in ctx.problems(record) if "merge base" in p], [])

    def test_a_tested_head_that_is_not_the_source_commit_is_stated(self):
        """Bitbucket prepares pull request builds itself, so HEAD is not
        promised to equal the source commit. The record must SAY which it
        saw rather than assume they agree: a receipt that used one for the
        other would name a revision nobody tested."""
        record = ctx.capture(
            "bitbucket", self._pr_env(BITBUCKET_COMMIT=self.base), self.tmp)
        self.assertEqual(record["sourceCommit"], self.base)
        self.assertEqual(record["testedCommit"], self.head)
        self.assertIs(record["testedIsSourceCommit"], False)

    def test_branch_mode_requires_nothing_and_says_it_is_branch_mode(self):
        record = ctx.capture("bitbucket", {}, self.tmp)
        self.assertEqual(record["mode"], "branch")
        self.assertEqual(ctx.problems(record), [])
        self.assertTrue(record["testedCommit"])

    def test_the_record_never_carries_a_credential(self):
        """The one thing that must never reach a CI log or an artifact.
        Driven with credential-shaped variables actually set, so this is a
        measurement rather than a reading of the source."""
        env = self._pr_env()
        env.update({"BITBUCKET_TOKEN": "sekrit-token-value",
                    "BITBUCKET_APP_PASSWORD": "sekrit-password-value",
                    "BITBUCKET_USERNAME": "sekrit-user"})
        blob = json.dumps(ctx.capture("bitbucket", env, self.tmp))
        for secret in ("sekrit-token-value", "sekrit-password-value",
                       "sekrit-user"):
            self.assertNotIn(secret, blob)


class TestTheCommandLine(_RepoCase):

    def _run(self, env, args=("bitbucket", "capture")):
        full = dict(os.environ)
        for key in list(full):
            if key.startswith("BITBUCKET_") or key.startswith("GITHUB_"):
                del full[key]
        full.update(env)
        return subprocess.run([sys.executable, TOOL] + list(args),
                              cwd=self.tmp, env=full, stdout=subprocess.PIPE,
                              stderr=subprocess.PIPE, universal_newlines=True,
                              timeout=120)

    def test_a_complete_context_prints_json_and_exits_zero(self):
        result = self._run(self._pr_env())
        self.assertEqual(result.returncode, 0, result.stderr)
        record = json.loads(result.stdout)
        self.assertEqual(record["pullRequestId"], "184")
        self.assertEqual(record["problems"], [])

    def test_an_incomplete_pull_request_context_exits_nonzero(self):
        result = self._run(self._pr_env(BITBUCKET_PR_DESTINATION_COMMIT=""))
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("REFUSING", result.stderr)
        # The JSON still prints: a refusal that also destroys the evidence
        # of what it saw is harder to diagnose than one that keeps it.
        self.assertTrue(json.loads(result.stdout)["problems"])

    def test_an_unknown_provider_is_a_usage_refusal(self):
        result = self._run({}, args=("gitlab", "capture"))
        self.assertEqual(result.returncode, 2)
        self.assertIn("unknown provider", result.stderr)


class TestThePullRequestPipeline(unittest.TestCase):
    """The wiring half. A capture tool nothing runs is a tool nobody
    benefits from, and this is the file that decides whether it runs."""

    def setUp(self):
        # bitbucket-pipelines.yml is a HUB record: docs/plan/EXPORT-ALLOWLIST
        # .txt withholds it, so a clone of the published release has nothing
        # to read here and three FileNotFoundErrors are the wrong answer. In
        # the hub its absence still fails, because there the file is supposed
        # to be on disk. See tools/bm_export_seam.py.
        _seam.no_data_outside_the_hub(["bitbucket-pipelines.yml"])
        self.text = _read(PIPELINES)

    def test_the_pipeline_defines_a_pull_requests_section(self):
        self.assertTrue(
            re.search(r"^\s+pull-requests:", self.text, re.MULTILINE),
            "bitbucket-pipelines.yml defines no pull-requests section, so "
            "Bitbucket Cloud runs nothing at the moment a pull request is "
            "opened, which is the moment the whole file exists for. A "
            "`default` section fires on branch pushes and never on a pull "
            "request.")

    def _pull_request_script(self):
        """The commands the pull request step actually runs, with its YAML
        anchor resolved. Resolving it is the point rather than a detail: the
        step body is one `script: *name` line, so a test that read only the
        text under `pull-requests:` would find no commands at all and could
        never tell a correct pipeline from an empty one."""
        block = self.text[self.text.index("pull-requests:"):]
        anchor = re.search(r"script:\s*\*([A-Za-z0-9_-]+)", block)
        self.assertIsNotNone(
            anchor,
            "the pull request step has no `script: *anchor`; if it was "
            "changed to an inline script, update this test to read it")
        name = anchor.group(1)
        body = re.search(
            r"- &%s\n(.*?)(?=\n\s*- &|\n\w|\Z)" % re.escape(name),
            self.text, re.DOTALL)
        self.assertIsNotNone(
            body, "bitbucket-pipelines.yml references anchor %r but never "
            "defines it, so the pull request step runs nothing at all"
            % name)
        return body.group(1)

    def test_the_pull_request_leg_captures_context_before_the_gate(self):
        """Order is the assertion. A capture that ran after the battery
        would describe a verdict that had already been published."""
        block = self._pull_request_script()
        capture_at = block.find("bm_ci_context.py")
        gate_at = block.find("tools/test_all.py")
        self.assertNotEqual(capture_at, -1,
                            "the pull request leg never captures the "
                            "revision identity it is about to test")
        self.assertNotEqual(gate_at, -1,
                            "the pull request leg never runs the gate")
        self.assertLess(capture_at, gate_at,
                        "the pull request leg runs the gate BEFORE it "
                        "captures what it is testing, so a run with no "
                        "usable context would publish a verdict first and "
                        "refuse afterwards")

    def test_the_clone_is_full_depth(self):
        """Load bearing rather than habit, and doubly so on the pull
        request leg: the destination commit has to be present for a merge
        base to mean anything, and the capture tool refuses when it is
        not."""
        self.assertTrue(re.search(r"clone:\s*\n\s*depth:\s*full", self.text),
                        "bitbucket-pipelines.yml no longer clones at full "
                        "depth, so the destination commit may be absent and "
                        "every history-dependent check becomes a guess")


if __name__ == "__main__":
    unittest.main(verbosity=1)

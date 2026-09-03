"""Calibration for the handback guard: check_handback() in
scripts/pre_push_gate.py, driven BACKWARDS with real `git push` runs against
a real local bare remote, per this estate's own lesson that a control nobody
drove backwards is a claim.

THE LAW under test, founder order 2026-08-30: a sub-session finishing work
never pushes the default branch itself. It pushes its feature branch only
and hands back to the orchestrating session, which reviews, runs the push
gates, and merges by pull request. This proves: a real push to the default
branch is refused, nonzero, naming the law and the route; a push to a
feature branch passes; and BROTHER_MAIN_PUSH=allow lets a default-branch
push through while printing that the guard was skipped.

Each test wires a throwaway repo's pre-push hook to call check_handback()
straight out of the real scripts/pre_push_gate.py module (never a copy), so
git's own ref-update protocol on stdin is exercised for real, against a real
remote's real default branch (resolved by the module itself, never hardcoded
here). The hook calls check_handback() alone, not the full multi-check gate:
this test is calibration for the handback guard, and the gate's OTHER checks
(remote-rules asks `gh`, which has nothing to say about a local bare
directory) are proven separately, by pre_push_gate.py's own suite.
"""
import os
import pathlib
import subprocess
import sys
import tempfile
import unittest

GATE = pathlib.Path(__file__).resolve().parent / "pre_push_gate.py"

GIT_ENV = {
    "GIT_AUTHOR_NAME": "Test", "GIT_AUTHOR_EMAIL": "test@example.com",
    "GIT_COMMITTER_NAME": "Test", "GIT_COMMITTER_EMAIL": "test@example.com",
    "PATH": os.environ.get("PATH", "/usr/bin:/bin:/usr/local/bin"),
}


def run_git(cwd, *args, env=None):
    full_env = dict(GIT_ENV)
    if env:
        full_env.update(env)
    proc = subprocess.run(["git", *args], cwd=str(cwd), capture_output=True,
                           text=True, env=full_env)
    if proc.returncode != 0:
        raise AssertionError("git %s failed: %s" % (list(args), proc.stderr))
    return proc


def make_pair(tmp):
    """A real local bare remote, plus a working clone pushed up to it and
    wired so its pre-push hook calls the real gate directly. Returns the
    working directory."""
    tmp = pathlib.Path(tmp)
    bare = tmp / "remote.git"
    work = tmp / "work"
    work.mkdir()
    run_git(tmp, "init", "-q", "--bare", "-b", "main", str(bare))
    run_git(work, "init", "-q", "-b", "main")
    (work / "README.md").write_text("hello\n")
    run_git(work, "add", "README.md")
    run_git(work, "commit", "-q", "-m", "initial commit")
    run_git(work, "remote", "add", "origin", str(bare))
    run_git(work, "push", "-q", "origin", "main")
    run_git(work, "remote", "set-head", "origin", "-a")

    hooks = tmp / "hooks"
    hooks.mkdir()
    # A thin runner that calls check_handback() ALONE (not the full gate:
    # the gate's remote-rules check asks `gh`, which has nothing to say
    # about a local bare directory and would report NO-DATA regardless of
    # the handback verdict, muddying the very thing under test here).
    runner = hooks / "check_handback_only.py"
    runner.write_text(
        "import sys\n"
        "sys.path.insert(0, %r)\n"
        "import pre_push_gate as g\n"
        "found = g.check_handback(cwd='.', stdin_text=sys.stdin.read())\n"
        "for f in found:\n"
        "    print('%%-8s %%-12s %%s' %% f)\n"
        "sys.exit(1 if any(f[0] == g.BLOCK for f in found) else 0)\n"
        % str(GATE.parent))
    hook = hooks / "pre-push"
    hook.write_text("#!/bin/sh\nexec %s %s\n" % (sys.executable, runner))
    hook.chmod(0o755)
    run_git(work, "config", "core.hooksPath", str(hooks))
    return work


def push(work, refspec, env=None):
    e = dict(GIT_ENV)
    if env:
        e.update(env)
    proc = subprocess.run(["git", "push", "origin", refspec], cwd=str(work),
                           capture_output=True, text=True, env=e)
    return proc.returncode, proc.stdout + proc.stderr


class DefaultBranchPushIsRefused(unittest.TestCase):
    def test_push_to_main_is_refused_naming_the_law_and_the_route(self):
        with tempfile.TemporaryDirectory() as tmp:
            work = make_pair(tmp)
            (work / "README.md").write_text("hello again\n")
            run_git(work, "commit", "-aqm", "a change on main")
            code, out = push(work, "main")
        self.assertNotEqual(code, 0, out)
        self.assertIn("default branch", out)
        self.assertIn("FEATURE BRANCH", out)
        self.assertIn("pull request", out)
        self.assertIn("BROTHER_MAIN_PUSH=allow", out)


class FeatureBranchPushPasses(unittest.TestCase):
    def test_push_to_a_feature_branch_succeeds(self):
        with tempfile.TemporaryDirectory() as tmp:
            work = make_pair(tmp)
            run_git(work, "checkout", "-qb", "feature/handback-guard-test")
            (work / "README.md").write_text("feature work\n")
            run_git(work, "commit", "-aqm", "feature commit")
            code, out = push(work, "feature/handback-guard-test")
        self.assertEqual(code, 0, out)


class AllowEscapeHatchSkipsAndSaysSo(unittest.TestCase):
    def test_allow_env_lets_main_through_and_prints_the_skip(self):
        with tempfile.TemporaryDirectory() as tmp:
            work = make_pair(tmp)
            (work / "README.md").write_text("bootstrap first push\n")
            run_git(work, "commit", "-aqm", "bootstrap")
            code, out = push(work, "main", env={"BROTHER_MAIN_PUSH": "allow"})
        self.assertEqual(code, 0, out)
        self.assertIn("SKIPPED", out)


class NoPushInFlightNeverBlocks(unittest.TestCase):
    def test_battery_style_run_on_a_main_checkout_is_ok(self):
        # CORRECTED 2026-08-30, driven before trusted: the first version of
        # check_handback fell back to judging the checked-out branch when no
        # pre-push ref lines arrived, so the battery, running the gate as a
        # plain command in a main checkout, was refused as if it were
        # pushing. A real push always feeds ref lines through git's hook
        # protocol, so an empty stdin means no push exists and the guard
        # must have nothing to say.
        import sys
        proc = subprocess.run(
            [sys.executable, str(GATE)],
            cwd=str(pathlib.Path(__file__).resolve().parent.parent),
            capture_output=True, text=True, stdin=subprocess.DEVNULL)
        out = proc.stdout + proc.stderr
        self.assertNotIn("BLOCK    handback", out, msg=out)
        self.assertIn("no push in flight", out, msg=out)


if __name__ == "__main__":
    unittest.main()

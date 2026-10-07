"""shared_config_check.py driven both ways, on real throwaway repositories it
makes for itself (each with its directory NAMED on `git init`, the form that
an inherited GIT_DIR cannot redirect)."""
import os
import shutil
import subprocess
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import shared_config_check as C  # noqa: E402

ENV = {k: v for k, v in os.environ.items() if not k.startswith("GIT_")}


class Base(unittest.TestCase):
    def setUp(self):
        self.top = tempfile.mkdtemp(prefix="shared-config-test-")
        self.addCleanup(shutil.rmtree, self.top, True)
        self.repo = os.path.join(self.top, "repo")
        subprocess.run(["git", "init", "-q", self.repo], env=ENV, check=True)

    def set(self, key, value):
        subprocess.run(["git", "config", "--file", os.path.join(self.repo, ".git", "config"),
                        key, value], env=ENV, check=True)

    def run_check(self, root=None):
        """The real script, the real exit code, never through a pipe."""
        proc = subprocess.run([sys.executable, os.path.join(HERE, "shared_config_check.py"),
                               root or self.repo], env=ENV, capture_output=True, text=True)
        return proc.returncode, proc.stdout


class TheDamageOfThatAfternoon(Base):
    def test_a_fresh_repository_is_clean_and_exits_zero(self):
        code, out = self.run_check()
        self.assertEqual(code, C.OK, out)
        self.assertIn("OK:", out)

    def test_core_bare_true_is_damage_with_its_reversing_command(self):
        self.set("core.bare", "true")
        code, out = self.run_check()
        self.assertEqual(code, C.DAMAGED, out)
        self.assertIn("core.bare false", out)

    def test_a_hooks_path_that_does_not_exist_is_damage(self):
        self.set("core.hooksPath", os.path.join(self.top, "deleted-fixture", "hooks"))
        code, out = self.run_check()
        self.assertEqual(code, C.DAMAGED, out)
        self.assertIn("--unset core.hooksPath", out)

    def test_a_hooks_path_that_exists_is_a_choice_not_damage(self):
        hooks = os.path.join(self.top, "real-hooks")
        os.makedirs(hooks)
        self.set("core.hooksPath", hooks)
        self.assertEqual(self.run_check()[0], C.OK)

    def test_every_fixture_identity_is_damage_and_a_real_one_is_not(self):
        for email in ("a@b.c", "t@example.com", "ci@example.invalid", "T@Example.ORG"):
            self.set("user.email", email)
            self.assertEqual(self.run_check()[0], C.DAMAGED, email)
        for email in ("someone@users.noreply.github.com", "a@b.co", "xa@b.cd"):
            self.set("user.email", email)
            self.assertEqual(self.run_check()[0], C.OK, email)

    def test_all_three_at_once_are_all_named(self):
        self.set("core.bare", "true")
        self.set("core.hooksPath", "/nonexistent/x/hooks")
        self.set("user.email", "a@b.c")
        code, out = self.run_check()
        self.assertEqual(code, C.DAMAGED)
        self.assertEqual(out.count("DAMAGED:"), 3, out)

    def test_a_worktree_is_judged_by_the_SHARED_config_not_its_own(self):
        subprocess.run(["git", "-c", "user.name=x", "-c", "user.email=x@users.noreply.github.com",
                        "commit", "-q", "--allow-empty", "-m", "one"], cwd=self.repo, env=ENV, check=True)
        wt = os.path.join(self.top, "wt")
        subprocess.run(["git", "worktree", "add", "-q", wt], cwd=self.repo, env=ENV, check=True)
        self.set("user.email", "a@b.c")
        code, out = self.run_check(wt)
        self.assertEqual(code, C.DAMAGED, out)
        self.assertIn(os.path.join(os.path.realpath(self.repo), ".git", "config"),
                      os.path.realpath(out.split("--file ")[1].split(" ")[0]))


class WhatCannotBeReadIsNeverClean(Base):
    def test_not_a_repository_is_no_data(self):
        code, out = self.run_check(self.top)
        self.assertEqual(code, C.NODATA, out)
        self.assertIn("NO-DATA", out)

    def test_an_unreadable_config_is_no_data(self):
        def runner(cmd, **kw):
            if "--git-common-dir" in cmd:
                return subprocess.CompletedProcess(cmd, 0, os.path.join(self.repo, ".git") + "\n", "")
            return subprocess.CompletedProcess(cmd, 128, "", "fatal: bad config line 3")
        self.assertEqual(C.main([self.repo], runner=runner), C.NODATA)

    def test_an_inherited_git_dir_does_not_pick_the_repository(self):
        other = os.path.join(self.top, "other")
        subprocess.run(["git", "init", "-q", other], env=ENV, check=True)
        subprocess.run(["git", "config", "--file", os.path.join(other, ".git", "config"),
                        "core.bare", "true"], env=ENV, check=True)
        os.environ["GIT_DIR"] = os.path.join(other, ".git")
        self.addCleanup(os.environ.pop, "GIT_DIR", None)
        path, problem = C.shared_config_path(self.repo)
        self.assertIsNone(problem)
        self.assertEqual(os.path.realpath(path), os.path.realpath(os.path.join(self.repo, ".git", "config")))


if __name__ == "__main__":
    unittest.main()

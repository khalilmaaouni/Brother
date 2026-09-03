import os
import shutil
import sys
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "tools"))

import coldstart_fixture


class Sandbox(unittest.TestCase):
    def test_sandbox_is_a_git_repo_carrying_the_subject(self):
        path = coldstart_fixture.build_sandbox("t1-endpoint")
        self.addCleanup(shutil.rmtree, path, True)
        self.assertTrue(os.path.isdir(os.path.join(path, ".git")))
        self.assertTrue(os.path.isfile(os.path.join(path, "BRIEF.md")))

    def test_sandbox_is_not_created_under_the_repository(self):
        path = coldstart_fixture.build_sandbox("t1-endpoint")
        self.addCleanup(shutil.rmtree, path, True)
        self.assertNotIn(os.path.abspath(ROOT), os.path.abspath(path))

    def test_unknown_subject_is_refused_by_name(self):
        with self.assertRaises(KeyError):
            coldstart_fixture.build_sandbox("no-such-subject")


import subprocess

RUNNER = os.path.join(ROOT, "tools", "sbe_coldstart.py")


class RunnerGuards(unittest.TestCase):
    def _run(self, *argv):
        return subprocess.run([sys.executable, RUNNER] + list(argv),
                              capture_output=True, text=True, timeout=60)

    def test_refuses_to_start_without_a_spend_ceiling(self):
        done = self._run("--runs", "1")
        self.assertNotEqual(done.returncode, 0)
        self.assertIn("max-budget-usd", done.stderr + done.stdout)

    def test_dry_run_spends_nothing_and_validates_the_fixture(self):
        done = self._run("--max-budget-usd", "2", "--dry-run")
        self.assertEqual(done.returncode, 0, done.stderr)
        self.assertIn("dry run", (done.stdout + done.stderr).lower())

    def test_help_exits_zero_and_examines_nothing(self):
        done = self._run("--help")
        self.assertEqual(done.returncode, 0)


if __name__ == "__main__":
    unittest.main()

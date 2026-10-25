#!/usr/bin/env python3
"""S6 harness: the suite environment runs the real git, never the xcrun shim.

On macOS /usr/bin/git is a developer tool trampoline (one inode shared with /usr/bin/make and /usr/bin/python3) that
calls xcrun, and inside the grader's sandbox it breaks git: 31 of 35 suites failed with the shim and 35 passed with
the real git (decision D12). The 1.1.8 night's launcher worked around it by linking `xcrun -f git` first on PATH.
loop_switches.suite_env, the one environment the grader, the landing rerun and the fuzz share, now does that itself:
it resolves the real git once, links it into a folder under the suite's HOME, puts that folder first on PATH, and
refuses (LookupError, NO-DATA) when no real git exists.

Written first, red on the base by assertion (1.1.8 stability release, owner ruling C2, 2026-10-24; spec
docs/plan/specs/1.1.9/S6.md). Done check: python3 -B scripts/test_suite_env_git.py GitFirst
"""
import os
import shutil
import sys
import tempfile
import unittest
from unittest import mock

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "loop"))
import loop_switches as LS  # noqa: E402

BASE = {"PATH": "/usr/bin:/bin", "HOME": "/Users/someone"}


class GitFirst(unittest.TestCase):
    def setUp(self):
        self.homes = []
        self.scratch = tempfile.mkdtemp(prefix="suite-env-scratch-")
        patcher = mock.patch.dict(os.environ, {"BROTHER_SCRATCH": self.scratch})
        patcher.start()
        self.addCleanup(patcher.stop)

    def tearDown(self):
        for h in self.homes + [self.scratch]:
            shutil.rmtree(h, ignore_errors=True)

    def home(self):
        h = tempfile.mkdtemp(prefix="suite-env-git-")
        self.homes.append(h)
        return h

    def test_git_in_the_suite_env_is_real(self):
        env = LS.suite_env(dict(BASE), self.home())
        first = env["PATH"].split(os.pathsep)[0]
        link = os.path.join(first, "git")
        self.assertTrue(os.path.islink(link), "the first PATH entry %r holds no git link: git resolves to %r"
                        % (first, shutil.which("git", path=env["PATH"])))
        real = os.path.realpath(link)
        self.assertTrue(os.access(real, os.X_OK), real)
        self.assertFalse(sys.platform == "darwin" and real.startswith("/usr/bin/"), "the link points at the shim")
        self.assertEqual(shutil.which("git", path=env["PATH"]), link)

    def test_no_git_refuses(self):
        self.assertTrue(hasattr(LS, "real_git"), "loop_switches has no real_git resolver, so no git is never refused")
        with mock.patch.object(LS, "real_git", lambda: None):
            with self.assertRaises(LookupError):
                LS.suite_env(dict(BASE), self.home())

    def test_a_shim_is_refused(self):
        self.assertTrue(hasattr(LS, "is_git_shim"), "loop_switches has no is_git_shim")
        self.assertTrue(LS.is_git_shim("/usr/bin/git", "darwin"))
        self.assertFalse(LS.is_git_shim("/usr/bin/git", "linux"))
        self.assertFalse(LS.is_git_shim("/Library/Developer/CommandLineTools/usr/bin/git", "darwin"))
        self.assertTrue(LS.is_git_shim("", "darwin"))

    def test_one_link_folder_and_nothing_under_home(self):
        a, b = self.home(), self.home()
        pa = LS.suite_env(dict(BASE), a)["PATH"].split(os.pathsep)
        pb = LS.suite_env(dict(BASE), b)["PATH"].split(os.pathsep)
        self.assertTrue(pa[0].startswith(self.scratch), "the git link folder %r is not under the scratch root" % pa[0])
        self.assertEqual(pa[0], pb[0])
        self.assertEqual(os.listdir(a), [], "suite_env wrote under the suite's HOME")
        again = LS.suite_env({"PATH": os.pathsep.join(pa)}, a)["PATH"].split(os.pathsep)
        self.assertEqual(again.count(pa[0]), 1)
        self.assertEqual(pa[1:], ["/usr/bin", "/bin"])

    def test_a_home_that_does_not_exist_is_left_alone(self):
        missing = os.path.join(self.scratch, "no-such-home")
        env = LS.suite_env(dict(BASE), missing)
        self.assertFalse(os.path.exists(missing))
        self.assertTrue(os.path.islink(os.path.join(env["PATH"].split(os.pathsep)[0], "git")))

    def test_an_empty_path_still_finds_git(self):
        env = LS.suite_env({"HOME": "/Users/someone"}, self.home())
        first = env.get("PATH", "").split(os.pathsep)[0]
        self.assertTrue(first and os.path.islink(os.path.join(first, "git")), "no git link with no PATH: %r" % first)


if __name__ == "__main__":
    unittest.main()

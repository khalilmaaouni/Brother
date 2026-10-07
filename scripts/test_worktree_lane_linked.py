"""worktree_lane reads git's worktree bookkeeping from the COMMON git dir, so a run started from a linked worktree
sees the lanes it registered (engine finding 26, 2026-09-26).

Measured: brother_run --cwd <a linked worktree> refused every unit with "a stale lane lane/R3 exists ... but no
worktree is registered for it on disk", about lanes the same run had just opened and `git worktree list` showed.
_admin_dirs(repo) read <repo>/.git/worktrees, and in a linked worktree <repo>/.git is a FILE pointing elsewhere, so
it found no admin dir at all and every lane looked unregistered. Run: python3 -B scripts/test_worktree_lane_linked.py
"""
import os, subprocess, sys, tempfile, unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "scripts"))
import worktree_lane as WL  # noqa: E402


def git(*a, cwd):
    r = subprocess.run(["git"] + list(a), cwd=cwd, capture_output=True, text=True,
                       env=dict(os.environ, GIT_CONFIG_GLOBAL=os.devnull, GIT_CONFIG_NOSYSTEM="1"))
    if r.returncode:
        raise AssertionError("git %s: %s" % (" ".join(a), r.stderr))
    return r.stdout.strip()


class LinkedWorktree(unittest.TestCase):
    def setUp(self):
        d = os.path.realpath(tempfile.mkdtemp(prefix="wl-linked-"))
        self.main = os.path.join(d, "main"); os.makedirs(self.main)
        git("init", "-q", "-b", "main", cwd=self.main)
        git("-c", "user.name=t", "-c", "user.email=t@t", "commit", "-q", "--allow-empty", "-m", "base", cwd=self.main)
        self.linked = os.path.join(d, "linked")
        git("worktree", "add", "-q", "-b", "work", self.linked, cwd=self.main)
        self.lane = os.path.join(d, "lane-R9")
        git("worktree", "add", "-q", "-b", "lane/R9", self.lane, cwd=self.linked)

    def test_admin_dirs_from_a_linked_worktree_are_the_common_ones(self):
        self.assertEqual(sorted(WL._admin_dirs(self.linked)), sorted(WL._admin_dirs(self.main)))
        self.assertTrue(WL._admin_dirs(self.linked), "a linked worktree must see the admin dirs git keeps")

    def test_a_registered_lane_is_found_from_a_linked_worktree(self):
        got = WL._stale_lane(self.linked, "lane/R9")
        self.assertIsNotNone(got)
        self.assertEqual(os.path.realpath(got["path"] or ""), os.path.realpath(self.lane),
                         "the lane is registered; reading it as unregistered refuses every unit")

    def test_the_main_checkout_still_works(self):
        got = WL._stale_lane(self.main, "lane/R9")
        self.assertEqual(os.path.realpath(got["path"] or ""), os.path.realpath(self.lane))


if __name__ == "__main__":
    unittest.main(verbosity=1)

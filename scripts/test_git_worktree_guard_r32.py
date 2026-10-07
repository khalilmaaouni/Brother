"""R3.2 tests for scripts/git_worktree_guard.py: the git refusal paths.

Each check hands back a refusal reason that already carries the standard
prefix named by Contract C (stderr_prefix 'git_worktree_guard: REFUSED:'),
so a caller never has to read a bare reason as an allow.
"""
import importlib.util
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from test_git_worktree_guard import (GitWorktreeGuardTest, bash_payload,
                                     run_guard)

SCRIPT = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                      'git_worktree_guard.py')
PREFIX = 'git_worktree_guard: REFUSED:'


def load_guard():
    spec = importlib.util.spec_from_file_location('git_worktree_guard_r32',
                                                  SCRIPT)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


GUARD = load_guard()


class R32GitRefusalTest(GitWorktreeGuardTest):

    def test_checkout_double_dash_modified_is_blocked(self):
        repo = self.make_repo()
        self.modify_file(repo)
        reason = GUARD.check_checkout(['--', 'file.txt'], repo)
        self.assertIsNotNone(reason)
        self.assertIn('REFUSED', reason)
        self.assertIn('file.txt', reason)

    def test_checkout_mixed_dirty_and_clean_refuses_the_dirty_one(self):
        repo = self.make_repo()
        self.modify_file(repo)
        reason = GUARD.check_checkout(['--', 'clean.txt', 'file.txt'], repo)
        self.assertIsNotNone(reason, 'a dirty path after a clean one was skipped')
        self.assertIn('REFUSED', reason)
        self.assertIn('file.txt', reason)
        self.assertNotIn('clean.txt', reason)

    def test_restore_modified_blocked_and_staged_allowed(self):
        repo = self.make_repo()
        self.modify_file(repo)
        blocked = GUARD.check_restore(['file.txt'], repo)
        self.assertIsNotNone(blocked)
        self.assertIn('REFUSED', blocked)
        self.assertIn('file.txt', blocked)
        self.assertIsNone(GUARD.check_restore(['--staged', 'file.txt'], repo))

    def test_restore_mixed_dirty_and_clean_refuses_the_dirty_one(self):
        repo = self.make_repo()
        self.modify_file(repo)
        reason = GUARD.check_restore(['clean.txt', 'file.txt'], repo)
        self.assertIsNotNone(reason, 'a dirty path after a clean one was skipped')
        self.assertIn('REFUSED', reason)
        self.assertIn('file.txt', reason)

    def test_check_git_checkout_dirty_is_refused(self):
        repo = self.make_repo()
        self.modify_file(repo)
        reason = GUARD.check_git(['checkout', '--', 'file.txt'], repo)
        self.assertIsNotNone(reason)
        self.assertIn('REFUSED', reason)
        self.assertIn('file.txt', reason)

    def test_check_git_restore_dirty_is_refused(self):
        repo = self.make_repo()
        self.modify_file(repo)
        reason = GUARD.check_git(['restore', 'file.txt'], repo)
        self.assertIsNotNone(reason)
        self.assertIn('REFUSED', reason)

    def test_check_git_stash_two_worktrees_is_refused(self):
        repo = self.make_repo()
        self.add_worktree(repo)
        reason = GUARD.check_git(['stash'], repo)
        self.assertIsNotNone(reason)
        self.assertIn('REFUSED', reason)
        self.assertIn('worktree', reason)

    def test_check_git_worktree_remove_dirty_force_is_refused(self):
        repo = self.make_repo()
        wt = self.add_worktree(repo)
        self.modify_file(wt, 'uncommitted change\n')
        reason = GUARD.check_git(['worktree', 'remove', wt, '--force'], repo)
        self.assertIsNotNone(reason)
        self.assertIn('REFUSED', reason)
        self.assertIn('uncommitted working tree changes', reason)

    def test_checkout_clean_is_allowed_and_stays_silent(self):
        repo = self.make_repo()
        self.assertIsNone(GUARD.check_checkout(['--', 'file.txt'], repo))
        result = run_guard(bash_payload('git checkout -- file.txt', repo))
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stderr, '')

    def test_refusal_line_carries_the_prefix_exactly_once(self):
        repo = self.make_repo()
        self.modify_file(repo)
        result = run_guard(bash_payload('git checkout -- file.txt', repo))
        self.assertEqual(result.returncode, 2, result.stderr)
        self.assertTrue(result.stderr.startswith(PREFIX + ' '), result.stderr)
        self.assertEqual(result.stderr.count(PREFIX), 1, result.stderr)

    def test_hostile_arguments_are_refused_not_crashed(self):
        hostile_args = [None, True, 7, float('nan'), b'file.txt', {'a': 1},
                        object(), ['file.txt', None], [1]]
        for bad in hostile_args:
            with self.assertRaises(ValueError):
                GUARD.check_checkout(bad, self.tmp)
            with self.assertRaises(ValueError):
                GUARD.check_restore(bad, self.tmp)
            with self.assertRaises(ValueError):
                GUARD.check_stash(bad, self.tmp)
            with self.assertRaises(ValueError):
                GUARD.check_worktree_remove(bad, self.tmp)
            with self.assertRaises(ValueError):
                GUARD.check_git(bad, self.tmp)
        hostile_dirs = [None, False, 7, float('nan'), b'x', ['a'], {'a': 1},
                        object()]
        for bad in hostile_dirs:
            with self.assertRaises(ValueError):
                GUARD.status_is_dirty(bad, 'file.txt')
            with self.assertRaises(ValueError):
                GUARD.check_checkout(['--', 'file.txt'], bad)
            with self.assertRaises(ValueError):
                GUARD.check_restore(['file.txt'], bad)
            with self.assertRaises(ValueError):
                GUARD.check_stash([], bad)
            with self.assertRaises(ValueError):
                GUARD.check_worktree_remove(['--force'], bad)
            with self.assertRaises(ValueError):
                GUARD.check_git(['checkout'], bad)
        with self.assertRaises(ValueError):
            GUARD.status_is_dirty(self.tmp, None)


if __name__ == '__main__':
    unittest.main()

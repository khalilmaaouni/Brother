"""R3.1 tests for parsing base in scripts/git_worktree_guard.py."""
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import git_worktree_guard as guard


class ParsingBaseTest(unittest.TestCase):
    def test_tokenize_keeps_glob_and_dollar_literal(self):
        tokens = guard.tokenize('rm -rf /tmp/* $DIR a? [x]')
        self.assertEqual(tokens, ['rm', '-rf', '/tmp/*', '$DIR', 'a?', '[x]'])

    def test_split_segments_splits_on_semicolon_and_and(self):
        tokens = guard.tokenize('cd /tmp && rm -rf a* ; git status')
        segments = guard.split_segments(tokens)
        self.assertEqual(segments, [['cd', '/tmp'], ['rm', '-rf', 'a*'], ['git', 'status']])

    def test_analyze_tracks_cd_for_git(self):
        captured = []
        original = guard.check_git
        def fake_check_git(args, directory):
            captured.append(directory)
            return None
        guard.check_git = fake_check_git
        try:
            self.assertIsNone(guard.analyze('cd /a && git status', '/base'))
            self.assertEqual(captured, ['/a'])
        finally:
            guard.check_git = original

    def test_analyze_relative_cd_normalizes(self):
        captured = []
        original = guard.check_git
        def fake_check_git(args, directory):
            captured.append(directory)
            return None
        guard.check_git = fake_check_git
        try:
            self.assertIsNone(guard.analyze('cd sub && git status', '/base'))
            self.assertEqual(captured, [os.path.normpath('/base/sub')])
        finally:
            guard.check_git = original

    def test_analyze_returns_first_refusal(self):
        original = guard.check_git
        def fake_check_git(args, directory):
            if args and args[0] == 'checkout':
                return 'blocked'
            return None
        guard.check_git = fake_check_git
        try:
            reason = guard.analyze('git checkout -- a; git checkout -- b', '/base')
            self.assertEqual(reason, 'blocked')
        finally:
            guard.check_git = original

    def test_analyze_skips_non_git_non_cd(self):
        called = []
        original = guard.check_git
        def fake_check_git(args, directory):
            called.append(True)
            return None
        guard.check_git = fake_check_git
        try:
            self.assertIsNone(guard.analyze('echo hi; ls', '/base'))
            self.assertEqual(called, [])
        finally:
            guard.check_git = original

    def test_tokenize_rejects_non_string(self):
        with self.assertRaises(ValueError):
            guard.tokenize(None)

    def test_split_segments_rejects_non_list(self):
        with self.assertRaises(ValueError):
            guard.split_segments(None)

    def test_split_segments_rejects_non_string_token(self):
        with self.assertRaises(ValueError):
            guard.split_segments(['rm', 1])

    def test_analyze_rejects_non_string_command(self):
        with self.assertRaises(ValueError):
            guard.analyze(None, '/base')

    def test_analyze_rejects_non_string_cwd(self):
        with self.assertRaises(ValueError):
            guard.analyze('git status', 123)

    def test_split_segments_empty_list(self):
        self.assertEqual(guard.split_segments([]), [])

    def test_split_segments_consecutive_separators(self):
        tokens = ['a', ';', ';', 'b']
        self.assertEqual(guard.split_segments(tokens), [['a'], ['b']])


if __name__ == '__main__':
    unittest.main()

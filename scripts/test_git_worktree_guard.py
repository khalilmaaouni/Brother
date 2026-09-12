"""Tests for scripts/git_worktree_guard.py. Drafted by DeepSeek V4.1 Flash; the
git -C case was added in review."""
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest


SCRIPT = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                      'git_worktree_guard.py')


def run_guard(payload):
    return subprocess.run(
        [sys.executable, SCRIPT],
        input=json.dumps(payload),
        capture_output=True,
        text=True,
        timeout=30,
    )


def bash_payload(command, cwd):
    return {
        'tool_name': 'Bash',
        'tool_input': {'command': command},
        'cwd': cwd,
    }


class GitWorktreeGuardTest(unittest.TestCase):

    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)

    def make_repo(self, name='repo'):
        path = os.path.join(self.tmp, name)
        os.makedirs(path)
        subprocess.run(['git', 'init', '-q', path], check=True,
                       capture_output=True)
        self.commit_file(path, 'hello\n')
        return path

    def commit_file(self, repo, content):
        with open(os.path.join(repo, 'file.txt'), 'w') as fh:
            fh.write(content)
        subprocess.run(['git', '-C', repo, 'add', 'file.txt'],
                       check=True, capture_output=True)
        subprocess.run(['git', '-C', repo, '-c', 'user.name=t',
                        '-c', 'user.email=t@t', 'commit', '-m', 'add'],
                       check=True, capture_output=True)

    def modify_file(self, repo, content='changed\n'):
        with open(os.path.join(repo, 'file.txt'), 'w') as fh:
            fh.write(content)

    def add_worktree(self, repo):
        path = os.path.join(self.tmp, 'wt')
        subprocess.run(
            ['git', '-C', repo, 'worktree', 'add', '--detach', path, 'HEAD'],
            check=True, capture_output=True)
        return path

    def test_checkout_double_dash_modified_is_blocked(self):
        repo = self.make_repo()
        self.modify_file(repo)
        result = run_guard(bash_payload('git checkout -- file.txt', repo))
        self.assertEqual(result.returncode, 2, result.stderr)
        self.assertIn('REFUSED', result.stderr)
        self.assertIn('file.txt', result.stderr)
        self.assertEqual(result.stdout, '')

    def test_checkout_double_dash_clean_is_allowed(self):
        repo = self.make_repo()
        result = run_guard(bash_payload('git checkout -- file.txt', repo))
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stderr, '')
        self.assertEqual(result.stdout, '')

    def test_checkout_head_with_cd_prefix_is_blocked(self):
        repo = self.make_repo()
        self.modify_file(repo)
        command = 'cd {0} && git checkout HEAD -- file.txt'.format(repo)
        result = run_guard(bash_payload(command, self.tmp))
        self.assertEqual(result.returncode, 2, result.stderr)
        self.assertIn('file.txt', result.stderr)

    def test_git_dash_c_checkout_modified_is_blocked(self):
        # Review addition: git -C <dir> names the repository the checkout
        # acts on; without this the guard read -C as the subcommand.
        repo = self.make_repo()
        self.modify_file(repo)
        command = 'git -C {0} checkout HEAD -- file.txt'.format(repo)
        result = run_guard(bash_payload(command, self.tmp))
        self.assertEqual(result.returncode, 2, result.stderr)
        self.assertIn('file.txt', result.stderr)

    def test_restore_modified_blocked_and_staged_allowed(self):
        repo = self.make_repo()
        self.modify_file(repo)
        blocked = run_guard(bash_payload('git restore file.txt', repo))
        self.assertEqual(blocked.returncode, 2, blocked.stderr)
        self.assertIn('file.txt', blocked.stderr)
        allowed = run_guard(bash_payload('git restore --staged file.txt', repo))
        self.assertEqual(allowed.returncode, 0, allowed.stderr)

    def test_stash_with_two_worktrees_is_blocked(self):
        repo = self.make_repo()
        self.add_worktree(repo)
        result = run_guard(bash_payload('git stash', repo))
        self.assertEqual(result.returncode, 2, result.stderr)
        self.assertIn('REFUSED', result.stderr)
        self.assertIn('worktree', result.stderr)

    def test_stash_with_one_worktree_is_allowed(self):
        repo = self.make_repo()
        result = run_guard(bash_payload('git stash', repo))
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_stash_list_with_two_worktrees_is_allowed(self):
        repo = self.make_repo()
        self.add_worktree(repo)
        result = run_guard(bash_payload('git stash list', repo))
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_malformed_stdin_exits_zero(self):
        result = subprocess.run(
            [sys.executable, SCRIPT],
            input='not json at all',
            capture_output=True,
            text=True,
            timeout=30,
        )
        self.assertEqual(result.returncode, 0)
        self.assertEqual(result.stderr, '')

    def test_non_bash_tool_exits_zero(self):
        payload = {
            'tool_name': 'Read',
            'tool_input': {'file_path': 'x'},
            'cwd': self.tmp,
        }
        result = run_guard(payload)
        self.assertEqual(result.returncode, 0)
        self.assertEqual(result.stderr, '')


if __name__ == '__main__':
    unittest.main()

"""R3.3 tests: rm refusal, rm routing and rm aware fail open input.

Lives beside the module under test, scripts/git_worktree_guard.py. The
guard's main is driven in process with test doubles for stdin and stderr, so
the public export tree needs no shell, no git and no repository fixture.
"""
import importlib.util
import io
import json
import os
import shutil
import sys
import tempfile
import unittest


HERE = os.path.dirname(os.path.abspath(__file__))
SCRIPT = os.path.join(HERE, 'git_worktree_guard.py')


def _load_guard():
    spec = importlib.util.spec_from_file_location(
        'git_worktree_guard_r33_target', SCRIPT)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


GUARD = _load_guard()


class _FakeStdin(object):
    def __init__(self, text):
        self._text = text

    def read(self):
        return self._text


def run_main_raw(raw):
    """Drive GUARD.main with raw text (str or bytes) on stdin."""
    old_in = sys.stdin
    old_err = sys.stderr
    sys.stdin = _FakeStdin(raw)
    sys.stderr = io.StringIO()
    try:
        code = GUARD.main()
        err = sys.stderr.getvalue()
    finally:
        sys.stdin = old_in
        sys.stderr = old_err
    return code, err


def run_main(payload):
    return run_main_raw(json.dumps(payload))


def bash_payload(command, cwd):
    return {
        'tool_name': 'Bash',
        'tool_input': {'command': command},
        'cwd': cwd,
    }


class GuardCase(unittest.TestCase):

    def setUp(self):
        self.dir = tempfile.mkdtemp(prefix='r33-')
        self.addCleanup(shutil.rmtree, self.dir, ignore_errors=True)

    def verdict(self, command, cwd=None):
        code, err = run_main(bash_payload(command, cwd or self.dir))
        return code, err


class RmRefusalTest(GuardCase):
    """R9: every way an rm target stops naming what it deletes."""

    def test_recursive_wildcard_is_refused_and_names_the_token(self):
        code, err = self.verdict('rm -rf build/foo*')
        self.assertEqual(code, 2, err)
        self.assertIn('git_worktree_guard: REFUSED:', err)
        self.assertIn('build/foo*', err)

    def test_variable_is_refused_recursive_and_plain(self):
        for command in ('rm -rf "$D"', 'rm "$D"', 'rm -rf ${D}', 'rm ${D}',
                        'rm -rf `pwd`', 'rm `pwd`', 'rm -rf ~/build',
                        'rm -f *.log', 'rm a*'):
            code, err = self.verdict(command)
            self.assertEqual(code, 2, command + ' -> ' + err)
            self.assertIn('git_worktree_guard: REFUSED:', err)

    def test_literal_recursive_delete_is_allowed(self):
        for command in ('rm -rf one/two', 'rm -rf one two', 'rm -rf -- one',
                        'rm -fr one', 'rm one two'):
            code, err = self.verdict(command)
            self.assertEqual(code, 0, command + ' -> ' + err)
            self.assertEqual(err, '')

    def test_empty_target_is_refused(self):
        for command in ('rm -rf', 'rm -r', 'rm -R', 'rm --recursive'):
            code, err = self.verdict(command)
            self.assertEqual(code, 2, command + ' -> ' + err)
            self.assertIn('REFUSED', err)

    def test_flag_only_target_is_refused(self):
        for command in ('rm -rf -v', 'rm -rf -- -v', 'rm -rf one -v'):
            code, err = self.verdict(command)
            self.assertEqual(code, 2, command + ' -> ' + err)
            self.assertIn('REFUSED', err)

    def test_dot_parent_and_root_are_refused(self):
        for command in ('rm -rf .', 'rm -rf ..', 'rm -rf /', 'rm -rf ./',
                        'rm -rf ../'):
            code, err = self.verdict(command)
            self.assertEqual(code, 2, command + ' -> ' + err)
            self.assertIn('REFUSED', err)

    def test_parent_of_the_tracked_directory_is_refused(self):
        parent = os.path.dirname(self.dir)
        reason = GUARD.check_rm(['-rf', parent], self.dir)
        self.assertIsNotNone(reason, 'the parent directory was allowed')
        self.assertIn(parent, reason)

    def test_glob_and_expansion_corpus_is_refused(self):
        globs = ('*', '?', '[ab]', 'a*', '*.log', 'b?', 'c[0-9]', '[!a]',
                 'x*y', 'q?q', 'dir/*', 'dir/?', '[a-z]*', 'path/*', '/abs/*',
                 'a*b*c', '[.]', '?*')
        expansions = ('$D', '${D}', '${HOME}', '$HOME', 'a$b', '$(pwd)',
                      '${X}/y', '`pwd`', '`ls -d .`', '~', '~/x', '~/build',
                      'pre$POST', '$?', 'x`date`y')
        for target in globs + expansions:
            for prefix in ('rm -rf ', 'rm '):
                code, err = self.verdict(prefix + target)
                self.assertEqual(code, 2, prefix + target + ' -> ' + err)

    def test_literal_corpus_is_allowed(self):
        names = ('one', 'two', 'three', 'build/one', 'build/two', 'src/a',
                 'src/b/c', 'tmp/alpha', 'tmp/beta', 'tmp/gamma', 'a-b', 'a.b',
                 'a_b', 'dir/sub/file', 'dir/sub/file.txt', 'logs/today.log',
                 'x/y/z', 'name-with-dash', 'name.with.dot', 'name_with_under')
        for name in names:
            for prefix in ('rm -rf ', 'rm '):
                code, err = self.verdict(prefix + name)
                self.assertEqual(code, 0, prefix + name + ' -> ' + err)


class RmRoutingTest(GuardCase):
    """R8: rm is recognised by basename at the start of a command."""

    def test_path_qualified_and_wrapped_rm_are_refused(self):
        for command in ('/bin/rm -rf a*', 'command rm -rf a*',
                        'env rm -rf a*', 'exec rm -rf a*',
                        'builtin rm -rf a*', 'eval rm -rf a*',
                        'sudo rm -rf a*', 'nohup rm -rf a*',
                        'xargs rm -rf a*', 'time rm -rf a*'):
            code, err = self.verdict(command)
            self.assertEqual(code, 2, command + ' -> ' + err)

    def test_git_rm_is_not_routed_to_check_rm(self):
        code, err = self.verdict('git rm -r --cached docs')
        self.assertEqual(code, 0, err)

    def test_cd_to_missing_directory_refuses_recursive_rm(self):
        code, err = self.verdict('cd /r33-no-such-dir-9f3a/missing && rm -rf b')
        self.assertEqual(code, 2, err)
        self.assertIn('REFUSED', err)

    def test_cd_to_verified_directory_allows_literal_recursive_rm(self):
        command = 'cd {0} && rm -rf b'.format(self.dir)
        code, err = self.verdict(command, cwd=os.path.dirname(self.dir))
        self.assertEqual(code, 0, err)


class FailOpenRmTest(GuardCase):
    """R7: a shape miss refuses when the raw input carries an rm word."""

    def test_malformed_stdin_exits_zero(self):
        code, err = run_main_raw('not json at all')
        self.assertEqual(code, 0)
        self.assertEqual(err, '')

    def test_malformed_stdin_with_rm_exits_two(self):
        code, err = run_main_raw('rm -rf x*')
        self.assertEqual(code, 2, err)
        self.assertIn('git_worktree_guard: REFUSED:', err)

    def test_rm_trigger_is_a_whole_word_not_a_substring(self):
        code, err = run_main_raw('chmod 700 warm.sh')
        self.assertEqual(code, 0, err)
        self.assertEqual(err, '')

    def test_command_int_with_rm_in_cwd_is_refused(self):
        code, err = run_main({'tool_name': 'Bash', 'tool_input': {'command': 5},
                              'cwd': 'rm -rf /tmp/r33-nowhere'})
        self.assertEqual(code, 2, err)
        self.assertIn('git_worktree_guard: REFUSED:', err)

    def test_command_none_with_rm_in_cwd_is_refused(self):
        code, err = run_main({'tool_name': 'Bash',
                              'tool_input': {'command': None},
                              'cwd': 'rm -rf /tmp/r33-nowhere'})
        self.assertEqual(code, 2, err)

    def test_cwd_int_with_rm_in_command_is_refused(self):
        code, err = run_main({
            'tool_name': 'Bash',
            'tool_input': {'command': 'rm -rf /tmp/r33-nowhere'},
            'cwd': 7})
        self.assertEqual(code, 2, err)

    def test_binary_stdin_with_rm_is_refused(self):
        code, err = run_main_raw(b'\x00\x01rm -rf x*')
        self.assertEqual(code, 2, err)
        self.assertIn('git_worktree_guard: REFUSED:', err)

    def test_tool_input_list_with_rm_command_is_refused(self):
        code, err = run_main({'tool_name': 'Bash',
                              'tool_input': ['rm -rf x*'], 'cwd': self.dir})
        self.assertEqual(code, 2, err)

    def test_tool_input_none_with_rm_in_cwd_is_refused(self):
        code, err = run_main({'tool_name': 'Bash', 'tool_input': None,
                              'cwd': 'rm -rf /tmp/r33-nowhere'})
        self.assertEqual(code, 2, err)

    def test_tool_name_int_with_rm_is_refused(self):
        code, err = run_main({
            'tool_name': 5,
            'tool_input': {'command': 'rm -rf /tmp/r33-nowhere'},
            'cwd': self.dir})
        self.assertEqual(code, 2, err)

    def test_tool_name_none_with_rm_is_refused(self):
        code, err = run_main({
            'tool_name': None,
            'tool_input': {'command': 'rm -rf /tmp/r33-nowhere'},
            'cwd': self.dir})
        self.assertEqual(code, 2, err)

    def test_compact_json_rm_value_is_refused(self):
        raw = json.dumps({'tool_name': 'Bash', 'tool_input': {'command': 5},
                          'cwd': 'rm -rf x'}, separators=(',', ':'))
        code, err = run_main_raw(raw)
        self.assertEqual(code, 2, err)

    def test_well_formed_non_rm_payload_still_allows(self):
        code, err = run_main({'tool_name': 'Bash',
                              'tool_input': {'command': 'ls -la'},
                              'cwd': self.dir})
        self.assertEqual(code, 0, err)
        self.assertEqual(err, '')

    def test_non_bash_tool_without_rm_still_allows(self):
        code, err = run_main({'tool_name': 'Read',
                              'tool_input': {'file_path': 'x'},
                              'cwd': self.dir})
        self.assertEqual(code, 0, err)
        self.assertEqual(err, '')


class HostileInputTest(GuardCase):
    """Probe rule: hostile input is refused, never a raw crash."""

    def test_check_rm_refuses_hostile_arguments(self):
        for args in (None, 5, 'rm -rf x', {'a': 1}, [1, 2], [None], [True],
                     ['-rf', float('nan')], [b'x'], [['-rf']]):
            with self.assertRaises(ValueError):
                GUARD.check_rm(args, self.dir)

    def test_check_rm_refuses_hostile_directory(self):
        for directory in (None, 5, 1.5, b'/tmp', ['/tmp'], {'a': 1}):
            with self.assertRaises(ValueError):
                GUARD.check_rm(['-rf', 'x'], directory)

    def test_analyze_refuses_hostile_arguments(self):
        for command, cwd in ((None, self.dir), (5, self.dir),
                             (b'rm', self.dir), ('rm -rf x', 5),
                             ('rm -rf x', ['/tmp'])):
            with self.assertRaises(ValueError):
                GUARD.analyze(command, cwd)

    def test_main_returns_a_verdict_for_hostile_stdin(self):
        for raw in (None, 5, b'\xff\xfe', [], {}, float('nan'), True, ''):
            code, err = run_main_raw(raw)
            self.assertIn(code, (0, 2), err)


if __name__ == '__main__':
    unittest.main()

'''test_git_location_guard.py: the guard module own contracts.'''
import os
import re
import sys
import tempfile
import time
import unittest
from collections.abc import MutableMapping

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import git_location_guard as guard

HERE = os.path.dirname(os.path.abspath(__file__))

EXPECTED_VARS = (
    'GIT_DIR', 'GIT_WORK_TREE', 'GIT_INDEX_FILE', 'GIT_COMMON_DIR',
    'GIT_OBJECT_DIRECTORY', 'GIT_ALTERNATE_OBJECT_DIRECTORIES', 'GIT_PREFIX',
    'GIT_NAMESPACE', 'GIT_CONFIG', 'GIT_CONFIG_PARAMETERS', 'GIT_CONFIG_COUNT',
    'GIT_IMPLICIT_WORK_TREE', 'GIT_GRAFT_FILE', 'GIT_SHALLOW_FILE',
    'GIT_INTERNAL_SUPER_PREFIX', 'GIT_REPLACE_REF_BASE', 'GIT_NO_REPLACE_OBJECTS')

FIXTURE_PATTERN = r'^(a@b\.c|[^@]+@example\.(com|org|invalid)|[^@]+@[^@]*\.invalid)$'

CLEAN_CONFIG = (
    '[core]\n'
    '\trepositoryformatversion = 0\n'
    '\tbare = false\n'
    '[user]\n'
    '\temail = operator@real-domain.test\n')

def _write_config(root, text):
    dotgit = os.path.join(root, '.git')
    if not os.path.isdir(dotgit):
        os.makedirs(dotgit)
    path = os.path.join(dotgit, 'config')
    with open(path, 'wb') as handle:
        handle.write(text.encode('utf-8'))
    return path

def _clear_process_location_vars():
    saved = {}
    for name in guard.git_location_vars():
        saved[name] = os.environ.pop(name, None)
    return saved

def _restore(saved):
    for name, value in saved.items():
        if value is None:
            os.environ.pop(name, None)
        else:
            os.environ[name] = value

class _TypeErrorOnLookup(MutableMapping):
    '''A mapping that raises TypeError on every membership or pop attempt.'''
    def __getitem__(self, key):
        raise TypeError('unhashable key: %r' % (key,))
    def __setitem__(self, key, value):
        raise TypeError('unhashable key: %r' % (key,))
    def __delitem__(self, key):
        raise TypeError('unhashable key: %r' % (key,))
    def __iter__(self):
        return iter([])
    def __len__(self):
        return 0
    def __contains__(self, key):
        raise TypeError('unhashable key: %r' % (key,))
    def pop(self, key, default=None):
        raise TypeError('unhashable key: %r' % (key,))

class GitLocationVarsTest(unittest.TestCase):
    def test_git_location_vars_exact(self):
        self.assertEqual(guard.git_location_vars(), EXPECTED_VARS)

    def test_git_location_vars_is_a_tuple_of_distinct_git_names(self):
        names = guard.git_location_vars()
        self.assertIsInstance(names, tuple)
        self.assertEqual(len(names), 17)
        self.assertEqual(len(set(names)), 17)
        for name in names:
            self.assertIsInstance(name, str)
            self.assertTrue(name.startswith('GIT_'))

    @unittest.skipUnless(os.path.isfile(os.path.join(HERE, 'tmp_sandbox.py')), 'tmp_sandbox.py is not on this tree')
    def test_git_location_vars_matches_the_tmp_sandbox_pin(self):
        with open(os.path.join(HERE, 'tmp_sandbox.py'), 'rb') as handle:
            text = handle.read().decode('utf-8')
        block = re.search(r'GIT_LOCATION_VARS = \((.*?)\)', text, re.S)
        self.assertIsNotNone(block, 'tmp_sandbox.py no longer defines GIT_LOCATION_VARS')
        pinned = tuple(re.findall(r'"([^"]+)"', block.group(1)))
        self.assertEqual(guard.git_location_vars(), pinned)

class DropGitLocationTest(unittest.TestCase):
    def test_drop_git_location_removes_every_name_and_returns_them_sorted(self):
        env = dict((name, '/tmp/leaked') for name in guard.git_location_vars())
        env['PATH'] = '/usr/bin'
        removed = guard.drop_git_location(env)
        self.assertEqual(env, {'PATH': '/usr/bin'})
        self.assertEqual(removed, sorted(EXPECTED_VARS))
        self.assertEqual(removed, sorted(removed))

    def test_drop_git_location_returns_nothing_for_a_clean_mapping(self):
        env = {'PATH': '/usr/bin'}
        self.assertEqual(guard.drop_git_location(env), [])
        self.assertEqual(env, {'PATH': '/usr/bin'})

    def test_drop_git_location_leaves_other_git_names_alone(self):
        env = {'GIT_ASKPASS': '/bin/askpass', 'GIT_SSH_COMMAND': 'ssh -v'}
        self.assertEqual(guard.drop_git_location(env), [])
        self.assertEqual(sorted(env), ['GIT_ASKPASS', 'GIT_SSH_COMMAND'])

    def test_drop_git_location_defaults_to_this_process_environment(self):
        saved = _clear_process_location_vars()
        try:
            os.environ['GIT_DIR'] = '/tmp/leaked-repo'
            os.environ['GIT_INDEX_FILE'] = '/tmp/leaked-index'
            removed = guard.drop_git_location()
            self.assertEqual(removed, ['GIT_DIR', 'GIT_INDEX_FILE'])
            self.assertNotIn('GIT_DIR', os.environ)
            self.assertNotIn('GIT_INDEX_FILE', os.environ)
        finally:
            _restore(saved)

    def test_drop_git_location_is_idempotent(self):
        env = dict((name, '/tmp/leaked') for name in guard.git_location_vars())
        self.assertEqual(guard.drop_git_location(env), sorted(EXPECTED_VARS))
        self.assertEqual(guard.drop_git_location(env), [])

    def test_drop_git_location_refuses_an_unhashable_key_mapping(self):
        with self.assertRaises(guard.GitLocationInputError):
            guard.drop_git_location(_TypeErrorOnLookup())

class ScrubbedEnvTest(unittest.TestCase):
    def test_scrubbed_env_removes_every_git_prefixed_name(self):
        saved = {}
        try:
            for name in ('GIT_DIR', 'GIT_ASKPASS', 'GIT_CONFIG_PARAMETERS'):
                saved[name] = os.environ.pop(name, None)
                os.environ[name] = '/tmp/leaked'
            scrubbed = guard.scrubbed_env()
            for name in ('GIT_DIR', 'GIT_ASKPASS', 'GIT_CONFIG_PARAMETERS'):
                self.assertNotIn(name, scrubbed)
            self.assertEqual(sorted(scrubbed), sorted(name for name in os.environ if not name.startswith('GIT_')))
            scrubbed['PATH'] = '/nowhere-at-all'
            self.assertNotEqual(os.environ.get('PATH'), '/nowhere-at-all')
        finally:
            for name, value in saved.items():
                if value is None:
                    os.environ.pop(name, None)
                else:
                    os.environ[name] = value

    def test_scrubbed_env_keeps_names_that_are_not_git(self):
        saved = os.environ.pop('BROTHER_GUARD_PROBE', None)
        os.environ['BROTHER_GUARD_PROBE'] = 'kept'
        try:
            self.assertEqual(guard.scrubbed_env().get('BROTHER_GUARD_PROBE'), 'kept')
        finally:
            if saved is None:
                os.environ.pop('BROTHER_GUARD_PROBE', None)
            else:
                os.environ['BROTHER_GUARD_PROBE'] = saved

class AssertNoGitLocationTest(unittest.TestCase):
    def test_raises_a_runtime_error_naming_where_and_every_variable(self):
        env = {'GIT_DIR': '/tmp/leaked-repo', 'GIT_INDEX_FILE': '/tmp/leaked-index'}
        with self.assertRaises(RuntimeError) as caught:
            guard.assert_no_git_location(env, where='unit-under-test')
        message = str(caught.exception)
        self.assertIn('unit-under-test', message)
        self.assertIn('GIT_DIR', message)
        self.assertIn('GIT_INDEX_FILE', message)

    def test_the_refusal_is_the_module_own_error_class(self):
        env = {'GIT_DIR': '/tmp/leaked-repo'}
        with self.assertRaises(guard.GitLocationError) as caught:
            guard.assert_no_git_location(env, where='named-caller')
        self.assertIsInstance(caught.exception, RuntimeError)
        self.assertTrue(issubclass(guard.GitLocationError, RuntimeError))

    def test_a_clean_environment_passes(self):
        self.assertIsNone(guard.assert_no_git_location({}, where='empty-environment'))

    def test_a_clean_process_environment_passes_and_is_not_modified(self):
        saved = _clear_process_location_vars()
        try:
            before = dict(os.environ)
            self.assertIsNone(guard.assert_no_git_location(os.environ, where='this-process'))
            self.assertEqual(dict(os.environ), before)
        finally:
            _restore(saved)

    def test_a_call_without_where_is_refused(self):
        with self.assertRaises(ValueError):
            guard.assert_no_git_location({})

    def test_assert_no_git_location_refuses_an_unhashable_key_mapping(self):
        with self.assertRaises(guard.GitLocationInputError):
            guard.assert_no_git_location(_TypeErrorOnLookup(), where='hostile-mapping')

class LiveConfigVerdictTest(unittest.TestCase):
    def test_live_config_verdict_keys_are_exactly_the_contract(self):
        with tempfile.TemporaryDirectory(prefix='glg-') as root:
            _write_config(root, CLEAN_CONFIG)
            verdict = guard.live_config_verdict(root, set())
        self.assertEqual(sorted(verdict), sorted([
            'schema', 'repo_root', 'checked_unix', 'verdict', 'core_bare',
            'hooks_path', 'user_email', 'fixture_email_match', 'reasons',
            'evidence']))

    def test_clean_config_passes(self):
        with tempfile.TemporaryDirectory(prefix='glg-') as root:
            _write_config(root, CLEAN_CONFIG)
            verdict = guard.live_config_verdict(root, set())
            self.assertEqual(verdict['verdict'], 'PASS')
            self.assertEqual(verdict['schema'], guard.SCHEMA)
            self.assertEqual(verdict['repo_root'], root)
            self.assertFalse(verdict['core_bare'])
            self.assertEqual(verdict['user_email'], 'operator@real-domain.test')
            self.assertFalse(verdict['fixture_email_match'])
            self.assertEqual(verdict['reasons'], [])
            self.assertGreater(verdict['checked_unix'], 0)
            self.assertLess(verdict['checked_unix'], time.time() + 60)
            self.assertTrue(verdict['evidence']['read_ok'])

    def test_bare_true_fails(self):
        with tempfile.TemporaryDirectory(prefix='glg-') as root:
            _write_config(root, '[core]\n\tbare = true\n')
            verdict = guard.live_config_verdict(root, set())
        self.assertEqual(verdict['verdict'], 'FAIL')
        self.assertTrue(verdict['core_bare'])
        self.assertIn('core.bare', '\n'.join(verdict['reasons']))

    def test_a_dead_hooks_path_fails(self):
        with tempfile.TemporaryDirectory(prefix='glg-') as root:
            missing = os.path.join(root, 'deleted-hooks')
            _write_config(root, '[core]\n\tbare = false\n\thooksPath = %s\n' % missing)
            verdict = guard.live_config_verdict(root, set())
        self.assertEqual(verdict['verdict'], 'FAIL')
        self.assertIn(missing, '\n'.join(verdict['reasons']))
        self.assertIn('no such directory exists', '\n'.join(verdict['reasons']))

    def test_a_live_hooks_path_passes(self):
        with tempfile.TemporaryDirectory(prefix='glg-') as root:
            os.makedirs(os.path.join(root, 'hooks'))
            _write_config(root, '[core]\n\tbare = false\n\thooksPath = hooks\n')
            verdict = guard.live_config_verdict(root, set())
        self.assertEqual(verdict['verdict'], 'PASS')
        self.assertTrue(verdict['evidence']['hooks_path_exists'])

    def test_the_built_in_fixture_identity_fails_with_no_fixture_email(self):
        with tempfile.TemporaryDirectory(prefix='glg-') as root:
            _write_config(root, '[core]\n\tbare = false\n[user]\n\temail = a@b.c\n')
            verdict = guard.live_config_verdict(root, set())
        self.assertEqual(verdict['verdict'], 'FAIL')
        self.assertTrue(verdict['fixture_email_match'])
        self.assertIn('user.email', '\n'.join(verdict['reasons']))

    def test_a_literal_fixture_email_from_the_set_fails(self):
        with tempfile.TemporaryDirectory(prefix='glg-') as root:
            _write_config(root, '[core]\n\tbare = false\n[user]\n\temail = fixture.lane@lane-host.test\n')
            verdict = guard.live_config_verdict(root, {'fixture.lane@lane-host.test'})
        self.assertEqual(verdict['verdict'], 'FAIL')
        self.assertTrue(verdict['fixture_email_match'])

    def test_a_real_looking_identity_is_not_a_fixture(self):
        with tempfile.TemporaryDirectory(prefix='glg-') as root:
            _write_config(root, '[core]\n\tbare = false\n[user]\n\temail = lane.operator@real-domain.test\n')
            verdict = guard.live_config_verdict(root, {'someone.else@elsewhere.test'})
        self.assertEqual(verdict['verdict'], 'PASS')
        self.assertFalse(verdict['fixture_email_match'])

    def test_missing_repository_is_no_data_not_a_pass(self):
        with tempfile.TemporaryDirectory(prefix='glg-') as root:
            verdict = guard.live_config_verdict(root, set())
        self.assertEqual(verdict['verdict'], 'NO-DATA')
        self.assertFalse(verdict['evidence']['read_ok'])
        self.assertEqual(verdict['reasons'], [])
        self.assertTrue(verdict['evidence']['problem'])

    def test_a_config_that_is_not_utf8_is_no_data(self):
        with tempfile.TemporaryDirectory(prefix='glg-') as root:
            os.makedirs(os.path.join(root, '.git'))
            with open(os.path.join(root, '.git', 'config'), 'wb') as handle:
                handle.write(b'[core]\n\tbare = \xff\xfe\n')
            verdict = guard.live_config_verdict(root, set())
        self.assertEqual(verdict['verdict'], 'NO-DATA')
        self.assertFalse(verdict['evidence']['read_ok'])

    def test_a_corrupt_value_alone_is_no_data(self):
        with tempfile.TemporaryDirectory(prefix='glg-') as root:
            _write_config(root, '[core]\n\tbare = maybe\n')
            verdict = guard.live_config_verdict(root, set())
        self.assertEqual(verdict['verdict'], 'NO-DATA')
        self.assertEqual(verdict['reasons'], [])

    def test_a_corrupt_value_beside_a_real_reason_still_fails(self):
        with tempfile.TemporaryDirectory(prefix='glg-') as root:
            missing = os.path.join(root, 'deleted-hooks')
            _write_config(root, '[core]\n\tbare = maybe\n\thooksPath = %s\n' % missing)
            verdict = guard.live_config_verdict(root, set())
        self.assertEqual(verdict['verdict'], 'FAIL')
        self.assertIn('core.bare', '\n'.join(verdict['evidence']['unreadable']))

    def test_a_worktree_git_file_and_commondir_are_followed(self):
        with tempfile.TemporaryDirectory(prefix='glg-') as root:
            worktree = os.path.join(root, 'lane')
            common = os.path.join(root, 'shared')
            gitdir = os.path.join(common, 'worktrees', 'lane')
            os.makedirs(gitdir)
            os.makedirs(worktree)
            with open(os.path.join(gitdir, 'commondir'), 'wb') as handle:
                handle.write(b'../..\n')
            with open(os.path.join(common, 'config'), 'wb') as handle:
                handle.write(b'[core]\n\tbare = true\n')
            with open(os.path.join(worktree, '.git'), 'wb') as handle:
                handle.write(('gitdir: %s\n' % gitdir).encode('utf-8'))
            verdict = guard.live_config_verdict(worktree, set())
        self.assertEqual(verdict['verdict'], 'FAIL')
        self.assertTrue(verdict['core_bare'])
        self.assertTrue(verdict['evidence']['config_path'].endswith(os.path.join('shared', 'config')))

    def test_a_git_directory_can_be_scanned_directly(self):
        with tempfile.TemporaryDirectory(prefix='glg-') as root:
            os.makedirs(os.path.join(root, 'objects'))
            with open(os.path.join(root, 'HEAD'), 'wb') as handle:
                handle.write(b'ref: refs/heads/main\n')
            with open(os.path.join(root, 'config'), 'wb') as handle:
                handle.write(b'[core]\n\tbare = true\n')
            verdict = guard.live_config_verdict(root, set())
        self.assertEqual(verdict['verdict'], 'FAIL')
        self.assertTrue(verdict['core_bare'])

class MainTest(unittest.TestCase):
    def test_main_without_a_mode_returns_2(self):
        self.assertEqual(guard.main([]), 2)

    def test_main_refuses_argv_that_is_not_a_list(self):
        self.assertEqual(guard.main('--assert-clean'), 2)

    def test_main_refuses_argv_holding_a_non_string(self):
        self.assertEqual(guard.main(['--assert-clean', 1]), 2)

    def test_main_assert_clean_returns_2_when_a_location_variable_is_present(self):
        saved = _clear_process_location_vars()
        try:
            os.environ['GIT_DIR'] = '/tmp/leaked-repo'
            self.assertEqual(guard.main(['--assert-clean']), 2)
        finally:
            _restore(saved)

    def test_main_assert_clean_returns_0_when_clean(self):
        saved = _clear_process_location_vars()
        try:
            self.assertEqual(guard.main(['--assert-clean']), 0)
        finally:
            _restore(saved)

    def test_main_check_live_config_maps_the_verdict_to_an_exit_code(self):
        with tempfile.TemporaryDirectory(prefix='glg-') as root:
            _write_config(root, '[core]\n\tbare = true\n')
            self.assertEqual(guard.main(['--check-live-config', '--repo-root', root]), 1)
            _write_config(root, CLEAN_CONFIG)
            self.assertEqual(guard.main(['--check-live-config', '--repo-root', root]), 0)
        with tempfile.TemporaryDirectory(prefix='glg-') as root:
            self.assertEqual(guard.main(['--check-live-config', '--repo-root', root]), 2)

    def test_main_fixture_email_is_repeatable_and_literal(self):
        with tempfile.TemporaryDirectory(prefix='glg-') as root:
            _write_config(root, '[core]\n\tbare = false\n[user]\n\temail = one.off@lane-host.test\n')
            self.assertEqual(guard.main([
                '--check-live-config', '--repo-root', root,
                '--fixture-email', 'other@elsewhere.test',
                '--fixture-email', 'one.off@lane-host.test']), 1)

class R25HooksPathScopeTest(unittest.TestCase):
    '''R2.5 R17: core.hooksPath must resolve to an existing directory that
    stays inside repo_root. A path outside it is damage, exactly like a
    missing one, because git would then run another repository's hooks.'''

    def setUp(self):
        self._saved_scan = guard.scan_repo_config

    def tearDown(self):
        guard.scan_repo_config = self._saved_scan

    def _fake_scan(self, hooks_path, hooks_exists):
        def fake(repo_root_arg, fixture_emails_arg):
            return {
                'schema': guard.SCHEMA,
                'repo_root': repo_root_arg,
                'config_path': os.path.join(repo_root_arg, '.git', 'config'),
                'read_ok': True,
                'problem': None,
                'core_bare': False,
                'hooks_path': hooks_path,
                'hooks_path_exists': hooks_exists,
                'user_email': None,
                'unreadable': [],
                'checked_unix': time.time(),
            }
        guard.scan_repo_config = fake

    def test_hooks_path_outside_root_blocks(self):
        with tempfile.TemporaryDirectory() as outer:
            root = os.path.join(outer, 'repo')
            outside = os.path.join(outer, 'elsewhere')
            os.mkdir(root)
            os.mkdir(outside)
            self._fake_scan(outside, True)
            verdict = guard.live_config_verdict(root, set())
        self.assertEqual(verdict['verdict'], guard.FAIL)
        self.assertTrue(any('outside' in reason for reason in verdict['reasons']),
                        'an existing hooks path outside repo_root must still FAIL: %r' % (verdict['reasons'],))

    def test_hooks_path_inside_root_passes(self):
        with tempfile.TemporaryDirectory() as root:
            os.mkdir(os.path.join(root, 'githooks'))
            self._fake_scan('githooks', True)
            verdict = guard.live_config_verdict(root, set())
        self.assertEqual(verdict['verdict'], guard.PASS)
        self.assertEqual(verdict['reasons'], [])

    def test_hooks_path_missing_inside_root_blocks(self):
        with tempfile.TemporaryDirectory() as root:
            self._fake_scan(os.path.join(root, 'githooks'), False)
            verdict = guard.live_config_verdict(root, set())
        self.assertEqual(verdict['verdict'], guard.FAIL)


class R25HooksPathInsideHelperTest(unittest.TestCase):
    '''R2.5 R17: the containment helper that separates outside from inside,
    and refuses hostile input instead of crashing.'''

    def test_hooks_path_inside_root_reports_inside(self):
        with tempfile.TemporaryDirectory() as root:
            os.mkdir(os.path.join(root, 'githooks'))
            self.assertTrue(guard._hooks_path_inside(root, 'githooks'))
            self.assertTrue(guard._hooks_path_inside(root, '.'))

    def test_hooks_path_outside_root_reports_outside(self):
        with tempfile.TemporaryDirectory() as outer:
            root = os.path.join(outer, 'repo')
            outside = os.path.join(outer, 'elsewhere')
            os.mkdir(root)
            os.mkdir(outside)
            self.assertFalse(guard._hooks_path_inside(root, outside))
            self.assertFalse(guard._hooks_path_inside(root, os.path.join('..', 'elsewhere')))
            self.assertFalse(guard._hooks_path_inside(root, os.path.join('..', 'repofake')))

    def test_hooks_path_hostile_input_refused(self):
        with tempfile.TemporaryDirectory() as root:
            for bad in (None, 7, 1.5, True, [], {}, b'hooks', float('nan')):
                self.assertFalse(guard._hooks_path_inside(root, bad),
                                 '%r must be refused, not accepted' % (bad,))
            self.assertFalse(guard._hooks_path_inside(None, 'githooks'))
            self.assertFalse(guard._hooks_path_inside(7, 'githooks'))
            self.assertFalse(guard._hooks_path_inside('', 'githooks'))


class R25HostileInputTest(unittest.TestCase):
    '''R2.5: hostile input is refused or denied, never a raw TypeError.'''

    def test_drop_git_location_refuses_hostile_env(self):
        for bad in (7, 1.5, True, b'x', ['x'], float('nan')):
            with self.assertRaises((guard.GitLocationError, guard.GitLocationInputError, ValueError)):
                guard.drop_git_location(bad)

    def test_assert_no_git_location_refuses_hostile_where(self):
        for bad in (7, 1.5, True, b'x', ['x'], {}, float('nan')):
            with self.assertRaises((guard.GitLocationError, guard.GitLocationInputError, ValueError)):
                guard.assert_no_git_location({}, where=bad)

    def test_live_config_verdict_refuses_hostile_repo_root(self):
        for bad in (None, 7, 1.5, True, b'/', ['x']):
            with self.assertRaises((guard.GitLocationError, guard.GitLocationInputError, ValueError)):
                guard.live_config_verdict(bad, set())

    def test_live_config_verdict_refuses_hostile_fixture_emails(self):
        with tempfile.TemporaryDirectory() as root:
            for bad in (None, 7, 1.5, True, b'a@b.c', 'a@b.c'):
                with self.assertRaises((guard.GitLocationError, guard.GitLocationInputError, ValueError)):
                    guard.live_config_verdict(root, bad)

    def test_live_config_verdict_refuses_unhashable_member(self):
        with tempfile.TemporaryDirectory() as root:
            with self.assertRaises((guard.GitLocationError, guard.GitLocationInputError, ValueError)):
                guard.live_config_verdict(root, [['a@b.c']])

    def test_scan_repo_config_never_raises_type_error(self):
        for bad in (None, 7, 1.5, True, b'/', ['x'], float('nan')):
            try:
                guard.scan_repo_config(bad, set())
            except TypeError as exc:
                self.fail('scan_repo_config raised TypeError on %r: %s' % (bad, exc))
            except Exception:
                pass

    def test_main_refuses_hostile_argv(self):
        self.assertEqual(guard.main('--check-live-config'), 2)
        self.assertEqual(guard.main([1, 2]), 2)
        self.assertEqual(guard.main(['--check-live-config', '--fixture-email', 7]), 2)


if __name__ == '__main__':
    unittest.main()

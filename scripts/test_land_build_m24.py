import os
import sys
import tempfile
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import land_build

HOSTILE = [None, 0, True, -1, float('nan'), '', 'x', b'x', [], ['x'], {}, {'a': 1}, (), {1, 2}, object()]


class TestCmdFor(unittest.TestCase):
    def test_cmd_for_script_direct(self):
        self.assertEqual(land_build.cmd_for('scripts/foo.py'), ['scripts/foo.py'])

    def test_cmd_for_unittest_module(self):
        self.assertEqual(
            land_build.cmd_for('pkg/test_foo.py'),
            ['-m', 'unittest', 'pkg.test_foo'],
        )

    def test_cmd_for_empty_refuses(self):
        with self.assertRaises(land_build.Refusal):
            land_build.cmd_for('')


class TestCollectSuites(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = self.tmp.name
        self.addCleanup(self.tmp.cleanup)

    def write(self, rel, content=''):
        path = os.path.join(self.root, rel)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, 'w', encoding='utf-8') as f:
            f.write(content)
        return path

    def test_collect_suites_combines_dedupes_sorts(self):
        with mock.patch.object(land_build, 'repository_root', return_value=self.root):
            build = {'tests': [{'path': 'scripts/test_a.py'}], 'edits': []}
            got = land_build.collect_suites(
                build, [], ['scripts/test_z.py', 'scripts/test_a.py']
            )
        self.assertEqual(got, ['scripts/test_a.py', 'scripts/test_z.py'])

    def test_sibling_suite_collected(self):
        with mock.patch.object(land_build, 'repository_root', return_value=self.root):
            self.write('scripts/test_bar.py')
            build = {'tests': [], 'edits': []}
            got = land_build.collect_suites(build, ['scripts/bar.py'], [])
        self.assertIn('scripts/test_bar.py', got)

    def test_sibling_absent_skipped(self):
        with mock.patch.object(land_build, 'repository_root', return_value=self.root):
            build = {'tests': [], 'edits': []}
            got = land_build.collect_suites(build, ['scripts/nope.py'], [])
        self.assertNotIn('scripts/test_nope.py', got)


class TestCleanEnv(unittest.TestCase):
    def test_env_cleaned(self):
        cleaned = land_build._clean_env(
            {'GIT_DIR': 'x', 'BROTHER_RUN_DIR': 'y', 'KEEP': 'z'}
        )
        self.assertNotIn('GIT_DIR', cleaned)
        self.assertNotIn('BROTHER_RUN_DIR', cleaned)
        self.assertEqual(cleaned.get('KEEP'), 'z')

    def test_env_rejects_non_string_keys(self):
        with self.assertRaises(land_build.Refusal):
            land_build._clean_env({1: 'v'})

    def test_env_rejects_non_string_values(self):
        with self.assertRaises(land_build.Refusal):
            land_build._clean_env({'a': 1})


class TestRunSuites(unittest.TestCase):
    def test_both_interpreters_run(self):
        bad, lines = land_build.run_suites(['scripts/x.py'], {})
        versions = [ln for ln in lines if ln.startswith('VERSION ')]
        self.assertEqual(len(versions), 2)
        self.assertTrue(any(sys.executable in ln for ln in versions))
        self.assertTrue(any('/usr/bin/python3' in ln for ln in versions))

    def test_run_suites_covers_each_suite(self):
        bad, lines = land_build.run_suites(['scripts/a.py', 'scripts/b.py'], {})
        joined = '\n'.join(lines)
        self.assertIn('scripts/a.py', joined)
        self.assertIn('scripts/b.py', joined)

    def test_run_timeout_is_1200(self):
        self.assertEqual(land_build.RUN_TIMEOUT_SECONDS, 1200)


class TestFuzz(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = self.tmp.name
        self.addCleanup(self.tmp.cleanup)

    def write(self, rel, content):
        path = os.path.join(self.root, rel)
        with open(path, 'w', encoding='utf-8') as f:
            f.write(content)
        return path

    def test_fuzz_counts_crash(self):
        with mock.patch.object(land_build, 'repository_root', return_value=self.root):
            self.write('m.py', 'def boom(x):\n    raise RuntimeError("bad")\n')
            crashes, returned, attempted = land_build.fuzz_new_mods(['m.py'])
        self.assertEqual(crashes, len(HOSTILE))
        self.assertEqual(returned, 0)

    def test_fuzz_allowlist_only_valueerror(self):
        with mock.patch.object(land_build, 'repository_root', return_value=self.root):
            self.write('m.py', 'def clean(x):\n    raise ValueError("bad")\n')
            crashes, returned, attempted = land_build.fuzz_new_mods(['m.py'])
        self.assertEqual(crashes, 0)

    def test_fuzz_allowlist_lookuperror(self):
        with mock.patch.object(land_build, 'repository_root', return_value=self.root):
            self.write('m.py', 'def clean(x):\n    raise KeyError("bad")\n')
            crashes, returned, attempted = land_build.fuzz_new_mods(['m.py'])
        self.assertEqual(crashes, 0)

    def test_fuzz_returned_counted(self):
        with mock.patch.object(land_build, 'repository_root', return_value=self.root):
            self.write('m.py', 'def passthrough(x):\n    return x\n')
            crashes, returned, attempted = land_build.fuzz_new_mods(['m.py'])
        self.assertEqual(crashes, 0)
        self.assertEqual(returned, len(HOSTILE))

    def test_fuzz_missing_module_refuses(self):
        with mock.patch.object(land_build, 'repository_root', return_value=self.root):
            with self.assertRaises(land_build.Refusal):
                land_build.fuzz_new_mods(['missing.py'])

    def test_fuzz_non_python_refuses(self):
        with mock.patch.object(land_build, 'repository_root', return_value=self.root):
            with self.assertRaises(land_build.Refusal):
                land_build.fuzz_new_mods(['m.txt'])


class TestHostileInputs(unittest.TestCase):
    def test_cmd_for_hostile_refuses(self):
        for value in HOSTILE:
            if isinstance(value, str) and value != '':
                continue
            with self.subTest(value=value):
                with self.assertRaises(land_build.Refusal):
                    land_build.cmd_for(value)

    def test_collect_suites_hostile_refuses(self):
        for value in HOSTILE:
            if isinstance(value, dict):
                continue
            with self.subTest(value=value):
                with self.assertRaises(land_build.Refusal):
                    land_build.collect_suites(value, [], [])

    def test_run_suites_hostile_refuses(self):
        for value in HOSTILE:
            if isinstance(value, list):
                continue
            with self.subTest(value=value):
                with self.assertRaises(land_build.Refusal):
                    land_build.run_suites(value, {})

    def test_fuzz_new_mods_hostile_refuses(self):
        for value in HOSTILE:
            if isinstance(value, list):
                continue
            with self.subTest(value=value):
                with self.assertRaises(land_build.Refusal):
                    land_build.fuzz_new_mods(value)


class TestVerdict(unittest.TestCase):
    def _patches(self, fuzz_result, run_result=(0, [])):
        return [
            mock.patch.object(
                land_build, 'load_build', return_value={'edits': [], 'tests': []}
            ),
            mock.patch.object(land_build, 'list_targets', return_value=[]),
            mock.patch.object(land_build, 'get_session_id', return_value=''),
            mock.patch.object(
                land_build,
                'load_fence_records',
                return_value=[{'id': 'i', 'path': 'x', 'owner': 'o', 'live': False}],
            ),
            mock.patch.object(land_build, 'gate_on_fences', return_value=None),
            mock.patch.object(land_build, 'verify_targets', return_value=None),
            mock.patch.object(land_build, 'recheck_before_write', return_value=None),
            mock.patch.object(land_build, 'apply_edits', return_value=[]),
            mock.patch.object(land_build, 'collect_suites', return_value=[]),
            mock.patch.object(land_build, 'run_suites', return_value=run_result),
            mock.patch.object(land_build, 'fuzz_new_mods', return_value=fuzz_result),
        ]

    def _run_main(self, fuzz_result):
        patches = self._patches(fuzz_result)
        for p in patches:
            p.start()
            self.addCleanup(p.stop)
        return land_build.main(['land_build.py', 'build.json'])

    def test_fuzz_verdict_red_on_crash(self):
        self.assertEqual(self._run_main((1, 0, 0)), 1)

    def test_verdict_green_when_clean(self):
        self.assertEqual(self._run_main((0, 1, 1)), 0)


if __name__ == '__main__':
    unittest.main()

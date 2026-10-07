import json
import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import land_build

class TestLandBuild(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = self.tmp.name

    def tearDown(self):
        self.tmp.cleanup()

    def write(self, name, content):
        path = os.path.join(self.root, name)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, 'wb') as f:
            if isinstance(content, bytes):
                f.write(content)
            else:
                f.write(content.encode('utf-8'))
        return path

    def test_empty_build_refuses(self):
        p = self.write('build.json', '{}')
        with self.assertRaises(land_build.Refusal) as cm:
            land_build.load_build(p)
        self.assertIn('empty', str(cm.exception))
        self.assertEqual(os.listdir(self.root), ['build.json'])

    def test_corrupt_json_refuses(self):
        p = self.write('build.json', '{ not json }')
        with self.assertRaises(land_build.Refusal) as cm:
            land_build.load_build(p)
        self.assertIn('corrupt', str(cm.exception).lower())
        self.assertEqual(os.listdir(self.root), ['build.json'])

    def test_missing_file_refuses(self):
        p = os.path.join(self.root, 'missing.json')
        with self.assertRaises(land_build.Refusal):
            land_build.load_build(p)

    def test_missing_brace_refuses(self):
        p = self.write('build.json', 'no braces here')
        with self.assertRaises(land_build.Refusal) as cm:
            land_build.load_build(p)
        self.assertIn('brace', str(cm.exception).lower())

    def test_wrong_edits_type_refuses(self):
        p = self.write('build.json', json.dumps({'edits': 'not a list'}))
        with self.assertRaises(land_build.Refusal):
            land_build.load_build(p)

    def test_absolute_path_refuses(self):
        with self.assertRaises(land_build.Refusal) as cm:
            land_build.safe_target_path('/abs', self.root)
        self.assertIn('absolute', str(cm.exception).lower())

    def test_dot_dot_segment_refuses(self):
        build = {'edits': [{'path': '../x'}]}
        with self.assertRaises(land_build.Refusal) as cm:
            land_build.list_targets(build, self.root)
        self.assertIn('dot dot', str(cm.exception).lower())

    def test_outside_root_refuses(self):
        outside = tempfile.mkdtemp()
        self.addCleanup(lambda: os.rmdir(outside) if os.path.isdir(outside) else None)
        os.makedirs(os.path.join(self.root, 'sub'))
        link = os.path.join(self.root, 'sub', 'out')
        try:
            os.symlink(outside, link)
        except (OSError, NotImplementedError):
            self.skipTest('symlink not supported')
        with self.assertRaises(land_build.Refusal) as cm:
            land_build.safe_target_path('sub/out/file', self.root)
        self.assertIn('symlink', str(cm.exception).lower())

    def test_symlink_target_refuses(self):
        os.makedirs(os.path.join(self.root, 'real'))
        link = os.path.join(self.root, 'link')
        try:
            os.symlink(os.path.join(self.root, 'real'), link)
        except (OSError, NotImplementedError):
            self.skipTest('symlink not supported')
        with self.assertRaises(land_build.Refusal) as cm:
            land_build.safe_target_path('link/file', self.root)
        self.assertIn('symlink', str(cm.exception).lower())

    def test_list_targets_dedupes_and_sorts(self):
        os.makedirs(os.path.join(self.root, 'a'))
        os.makedirs(os.path.join(self.root, 'b'))
        build = {'edits': [{'path': 'b'}, {'path': 'a'}], 'tests': [{'path': 'a'}]}
        result = land_build.list_targets(build, self.root)
        self.assertEqual(result, ['a', 'b'])

    def test_safe_target_path_valid(self):
        os.makedirs(os.path.join(self.root, 'a'))
        result = land_build.safe_target_path('a/b.txt', self.root)
        expected = os.path.abspath(os.path.join(self.root, 'a', 'b.txt'))
        self.assertEqual(result, expected)

    def test_get_session_id_from_env(self):
        self.assertEqual(land_build.get_session_id({'BROTHER_SESSION_ID': 'sess'}), 'sess')

    def test_get_session_id_from_run_dir(self):
        self.assertEqual(land_build.get_session_id({'BROTHER_RUN_DIR': '/tmp/run123'}), 'run123')

    def test_get_session_id_empty(self):
        self.assertEqual(land_build.get_session_id({}), '')

    def test_get_session_id_unhashable_key_env(self):
        class BadEnv(dict):
            def get(self, key, default=None):
                return {}.get([], default)
        with self.assertRaises(land_build.Refusal):
            land_build.get_session_id(BadEnv())

    def test_hostile_input_refuses(self):
        non_str = [None, 0, True, -1, float('nan'), b'x', [], {}, object()]
        for val in non_str:
            with self.subTest(val=val):
                with self.assertRaises(land_build.Refusal):
                    land_build.load_build(val)
                with self.assertRaises(land_build.Refusal):
                    land_build.safe_target_path(val, self.root)
        non_dict = [None, 0, True, -1, float('nan'), '', b'x', [], object()]
        for val in non_dict:
            with self.subTest(val=val):
                with self.assertRaises(land_build.Refusal):
                    land_build.list_targets(val, self.root)
                with self.assertRaises(land_build.Refusal):
                    land_build.get_session_id(val)

    def test_repository_root(self):
        root = land_build.repository_root()
        self.assertTrue(os.path.isabs(root))
        self.assertTrue(os.path.isdir(root))

if __name__ == '__main__':
    unittest.main()

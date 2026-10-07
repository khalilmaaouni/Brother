import os
import sys
import tempfile
import types
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import land_build


class FenceTestBase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = self.tmp.name

    def tearDown(self):
        self.tmp.cleanup()

    def install_fence_expiry(self, load_fn=None, classify_fn=None):
        module = types.ModuleType('fence_expiry')
        module.REGISTRY = os.path.join(self.root, '.sbe', 'tasks.json')

        def default_load(path=None):
            return {'tasks': [
                {'id': 'r1', 'path': 'src/a', 'owner': 'own', 'state': 'LIVE'},
            ]}

        def default_classify(task, now=None):
            if not isinstance(task, dict):
                return 'BAD'
            return task.get('state', 'LIVE')

        module.load = load_fn if load_fn is not None else default_load
        module.classify = classify_fn if classify_fn is not None else default_classify
        previous = sys.modules.get('fence_expiry')
        sys.modules['fence_expiry'] = module

        def restore():
            if previous is None:
                sys.modules.pop('fence_expiry', None)
            else:
                sys.modules['fence_expiry'] = previous

        self.addCleanup(restore)
        return module


class TestFenceGate(FenceTestBase):
    def test_load_fence_records_maps_live_and_ignores_expired(self):
        def load_fn(path=None):
            return {'tasks': [
                {'id': 'r1', 'path': 'src/a', 'owner': 'own', 'state': 'LIVE'},
                {'id': 'r2', 'path': 'src/b', 'owner': 'own', 'state': 'EXPIRED'},
                {'id': 'r3', 'path': 'src/c', 'owner': 'own', 'state': 'CLOSED'},
            ]}
        self.install_fence_expiry(load_fn=load_fn)
        records = land_build.load_fence_records()
        self.assertEqual(len(records), 3)
        self.assertTrue(records[0]['live'])
        self.assertFalse(records[1]['live'])
        self.assertFalse(records[2]['live'])
        self.assertEqual(records[0]['id'], 'r1')
        self.assertEqual(records[0]['path'], 'src/a')
        self.assertEqual(records[0]['owner'], 'own')

    def test_load_fence_records_no_expiry_refuses(self):
        def load_fn(path=None):
            return {'tasks': [
                {'id': 'r1', 'path': 'src/a', 'owner': 'own', 'state': 'NO-EXPIRY'},
            ]}
        self.install_fence_expiry(load_fn=load_fn)
        with self.assertRaises(land_build.Refusal):
            land_build.load_fence_records()

    def test_load_fence_records_empty_refuses(self):
        def load_fn(path=None):
            return {'tasks': []}
        self.install_fence_expiry(load_fn=load_fn)
        with self.assertRaises(land_build.Refusal):
            land_build.load_fence_records()

    def test_load_fence_records_missing_store_refuses(self):
        def load_fn(path=None):
            raise OSError('missing')
        self.install_fence_expiry(load_fn=load_fn)
        with self.assertRaises(land_build.Refusal):
            land_build.load_fence_records()

    def test_load_fence_records_bad_schema_refuses(self):
        bad_payloads = [None, [], 'x', 0, {'tasks': 'nope'}, {'tasks': [None]}]
        for payload in bad_payloads:
            with self.subTest(payload=payload):
                def load_fn(path=None, payload=payload):
                    return payload
                self.install_fence_expiry(load_fn=load_fn)
                with self.assertRaises(land_build.Refusal):
                    land_build.load_fence_records()

    def test_load_fence_records_missing_module_refuses(self):
        sys.modules.pop('fence_expiry', None)
        with self.assertRaises(land_build.Refusal):
            land_build.load_fence_records()

    def test_fence_store_load_trust(self):
        calls = []

        def load_fn(path=None):
            calls.append(path)
            return {'tasks': [
                {'id': 'trusted-id', 'path': 'src/z', 'owner': 'lane', 'state': 'LIVE'},
            ]}

        self.install_fence_expiry(load_fn=load_fn)
        records = land_build.load_fence_records()
        self.assertEqual(len(calls), 1)
        self.assertEqual(len(records), 1)
        self.assertEqual(records[0]['id'], 'trusted-id')
        self.assertEqual(records[0]['path'], 'src/z')
        self.assertEqual(records[0]['owner'], 'lane')

    def test_query_fences_ok_and_block(self):
        records = [{'id': 'r1', 'path': 'src/a', 'owner': 'own', 'live': True}]
        status, matched = land_build.query_fences(['src/b'], 'sess', records)
        self.assertEqual(status, 'OK')
        self.assertEqual(matched, [])
        bad_records = [None, 'x', 0, {}, [None], [{}],
                       [{'live': 'yes', 'path': 'a', 'owner': 'o'}]]
        for bad in bad_records:
            with self.subTest(bad=bad):
                status, matched = land_build.query_fences(['a'], 'sess', bad)
                self.assertEqual(status, 'BLOCK')
                self.assertEqual(matched, [])
        for bad_paths in [None, 'x', 0, {}]:
            with self.subTest(bad_paths=bad_paths):
                status, matched = land_build.query_fences(bad_paths, 'sess', records)
                self.assertEqual(status, 'BLOCK')
                self.assertEqual(matched, [])
        for bad_sid in [None, 0, True]:
            with self.subTest(bad_sid=bad_sid):
                status, matched = land_build.query_fences(['src/b'], bad_sid, records)
                self.assertEqual(status, 'BLOCK')
                self.assertEqual(matched, [])

    def test_query_fences_empty_store_blocks(self):
        status, matched = land_build.query_fences(['src/a'], 'sess', [])
        self.assertEqual(status, 'BLOCK')
        self.assertEqual(matched, [])

    def test_gate_refuses_other_owner(self):
        records = [{'id': 'rec-1', 'path': 'src/a', 'owner': 'other', 'live': True}]
        with self.assertRaises(land_build.Refusal) as cm:
            land_build.gate_on_fences(['src/a/file.txt'], 'sess', records)
        text = str(cm.exception)
        self.assertIn('rec-1', text)
        self.assertIn('other', text)
        self.assertIn('src/a', text)

    def test_gate_owner_compare(self):
        records = [{'id': 'rec-1', 'path': 'src/a', 'owner': 'sess', 'live': True}]
        land_build.gate_on_fences(['src/a/file.txt'], 'sess', records)

    def test_gate_blocks_on_store_error(self):
        with self.assertRaises(land_build.Refusal):
            land_build.gate_on_fences(['src/a'], 'sess', None)
        with self.assertRaises(land_build.Refusal):
            land_build.gate_on_fences(['src/a'], 'sess', [{'live': True}])
        with self.assertRaises(land_build.Refusal):
            land_build.gate_on_fences(['src/a'], 'sess', [])

    def test_empty_identity_refuses_live_fence(self):
        records = [{'id': 'rec-1', 'path': 'src/a', 'owner': '', 'live': True}]
        with self.assertRaises(land_build.Refusal):
            land_build.gate_on_fences(['src/a/file.txt'], '', records)

    def test_path_inside_fence(self):
        self.assertTrue(land_build.path_inside_fence('src/a/file.txt', 'src/a'))
        self.assertTrue(land_build.path_inside_fence('src/a', 'src/a'))
        self.assertFalse(land_build.path_inside_fence('src/b/file.txt', 'src/a'))

    def test_path_prefix_not_fence(self):
        self.assertFalse(land_build.path_inside_fence('src/abc', 'src/a'))
        self.assertFalse(land_build.path_inside_fence('abc', 'a'))

    def test_hostile_input_refuses_fences(self):
        non_str = [None, 0, True, -1, float('nan'), b'x', [], {}, object()]
        for val in non_str:
            with self.subTest(val=val):
                with self.assertRaises(land_build.Refusal):
                    land_build.path_inside_fence(val, 'a')
                with self.assertRaises(land_build.Refusal):
                    land_build.path_inside_fence('a', val)
        for val in [None, 0, True, -1, float('nan'), b'x', {}, object()]:
            with self.subTest(kind='query', val=val):
                status, matched = land_build.query_fences(val, 'sess', [])
                self.assertEqual(status, 'BLOCK')
                status, matched = land_build.query_fences(['a'], val, [])
                self.assertEqual(status, 'BLOCK')
        with self.assertRaises(land_build.Refusal):
            land_build.gate_on_fences(None, 'sess', None)


if __name__ == '__main__':
    unittest.main()

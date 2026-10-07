import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import land_build


class TestLandBuildM23(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = self.tmp.name
        self.old_root = land_build.repository_root
        land_build.repository_root = lambda: self.root

    def tearDown(self):
        land_build.repository_root = self.old_root
        self.tmp.cleanup()

    def test_verify_new_exists_refuses(self):
        with open(os.path.join(self.root, 'new.txt'), 'w') as f:
            f.write('old')
        build = {'edits': [{'path': 'new.txt', 'new_file_content': 'x'}]}
        with self.assertRaises(land_build.Refusal) as cm:
            land_build.verify_targets(build)
        self.assertIn('exists', str(cm.exception).lower())

    def test_verify_new_ok(self):
        build = {'edits': [{'path': 'new.txt', 'new_file_content': 'x'}]}
        land_build.verify_targets(build)

    def test_verify_find_count_refuses(self):
        with open(os.path.join(self.root, 'a.txt'), 'w') as f:
            f.write('one one')
        build = {'edits': [{'path': 'a.txt', 'find': 'one', 'replace': 'two'}]}
        with self.assertRaises(land_build.Refusal) as cm:
            land_build.verify_targets(build)
        self.assertIn('find count', str(cm.exception).lower())

    def test_verify_find_count_ok(self):
        with open(os.path.join(self.root, 'a.txt'), 'w') as f:
            f.write('one two')
        build = {'edits': [{'path': 'a.txt', 'find': 'one', 'replace': 'two'}]}
        land_build.verify_targets(build)

    def test_no_write_before_gate(self):
        called = []
        old_load_build = land_build.load_build
        old_list_targets = land_build.list_targets
        old_get_session_id = land_build.get_session_id
        old_load_fence_records = land_build.load_fence_records
        old_gate = land_build.gate_on_fences
        old_verify = land_build.verify_targets
        old_recheck = land_build.recheck_before_write
        old_apply = land_build.apply_edits
        try:
            land_build.load_build = lambda path: {'edits': [{'path': 'a', 'new_file_content': 'x'}]}
            land_build.list_targets = lambda build, root: ['a']
            land_build.get_session_id = lambda env: 'sess'
            land_build.load_fence_records = lambda: []
            def refuse_gate(paths, sid, records):
                raise land_build.Refusal('fence refused')
            land_build.gate_on_fences = refuse_gate
            land_build.verify_targets = lambda build: None
            land_build.recheck_before_write = lambda p, s, b: None
            def apply(build):
                called.append('apply')
                return ['a']
            land_build.apply_edits = apply
            rc = land_build.main(['script', 'build.json'])
            self.assertEqual(rc, 1)
            self.assertEqual(called, [])
        finally:
            land_build.load_build = old_load_build
            land_build.list_targets = old_list_targets
            land_build.get_session_id = old_get_session_id
            land_build.load_fence_records = old_load_fence_records
            land_build.gate_on_fences = old_gate
            land_build.verify_targets = old_verify
            land_build.recheck_before_write = old_recheck
            land_build.apply_edits = old_apply

    def test_recheck_before_write(self):
        calls = []
        old_load_build = land_build.load_build
        old_list_targets = land_build.list_targets
        old_get_session_id = land_build.get_session_id
        old_load_fence_records = land_build.load_fence_records
        old_gate = land_build.gate_on_fences
        old_verify = land_build.verify_targets
        old_recheck = land_build.recheck_before_write
        old_apply = land_build.apply_edits
        try:
            land_build.load_build = lambda path: {'edits': [{'path': 'a', 'new_file_content': 'x'}]}
            land_build.list_targets = lambda build, root: ['a']
            land_build.get_session_id = lambda env: 'sess'
            land_build.load_fence_records = lambda: [{'id': 'r1', 'path': 'a', 'owner': 'own', 'live': True}]
            land_build.gate_on_fences = lambda paths, sid, records: calls.append('gate')
            land_build.verify_targets = lambda build: calls.append('verify')
            land_build.recheck_before_write = lambda p, s, b: calls.append('recheck')
            def apply(build):
                calls.append('apply')
                return ['a']
            land_build.apply_edits = apply
            rc = land_build.main(['script', 'build.json'])
            self.assertEqual(rc, 0)
            self.assertIn('recheck', calls)
            self.assertLess(calls.index('recheck'), calls.index('apply'))
        finally:
            land_build.load_build = old_load_build
            land_build.list_targets = old_list_targets
            land_build.get_session_id = old_get_session_id
            land_build.load_fence_records = old_load_fence_records
            land_build.gate_on_fences = old_gate
            land_build.verify_targets = old_verify
            land_build.recheck_before_write = old_recheck
            land_build.apply_edits = old_apply

    def test_apply_edits_new_and_edit(self):
        with open(os.path.join(self.root, 'a.txt'), 'w') as f:
            f.write('one two\n')
        build = {
            'edits': [
                {'path': 'new.txt', 'new_file_content': 'hello'},
                {'path': 'a.txt', 'find': 'one', 'replace': 'ONE'},
            ],
            'tests': [],
        }
        touched = land_build.apply_edits(build)
        self.assertEqual(touched, ['new.txt', 'a.txt'])
        with open(os.path.join(self.root, 'new.txt'), 'rb') as f:
            self.assertEqual(f.read(), b'hello\n')
        with open(os.path.join(self.root, 'a.txt'), 'rb') as f:
            self.assertEqual(f.read(), b'ONE two\n')

    def test_apply_edits_partial(self):
        with open(os.path.join(self.root, 'a.txt'), 'w') as f:
            f.write('one two\n')
        build = {
            'edits': [
                {'path': 'a.txt', 'find': 'one', 'replace': 'ONE'},
                {'path': 'b.txt', 'find': 'missing', 'replace': 'x'},
            ],
            'tests': [],
        }
        with self.assertRaises(land_build.PartialRefusal) as cm:
            land_build.apply_edits(build)
        self.assertEqual(cm.exception.touched, ['a.txt'])
        with open(os.path.join(self.root, 'a.txt'), 'rb') as f:
            self.assertEqual(f.read(), b'ONE two\n')

    def test_hostile_input_refuses_new_functions(self):
        hostile = [None, 0, True, -1, float('nan'), b'x', [], object()]
        for val in hostile:
            with self.subTest(val=val):
                with self.assertRaises(land_build.Refusal):
                    land_build.verify_targets(val)
                with self.assertRaises(land_build.Refusal):
                    land_build.recheck_before_write(val, 's', {})
                with self.assertRaises(land_build.Refusal):
                    land_build.recheck_before_write([], val, {})
                with self.assertRaises(land_build.Refusal):
                    land_build.recheck_before_write([], 's', val)
                with self.assertRaises(land_build.Refusal):
                    land_build.apply_edits(val)


if __name__ == '__main__':
    unittest.main()

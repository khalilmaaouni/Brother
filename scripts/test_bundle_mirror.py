#!/usr/bin/env python3
'''Tests for the D4.f bundle mirror checker.'''
import json
import os
import tempfile
import unittest

import bundle_mirror


class TestBundleMirror(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.tmp = self._tmp.name

    def tearDown(self):
        self._tmp.cleanup()

    def _write(self, rel, body):
        path = os.path.join(self.tmp, rel)
        folder = os.path.dirname(path)
        if folder and not os.path.isdir(folder):
            os.makedirs(folder)
        with open(path, 'wb') as handle:
            handle.write(body)
        return path

    def _manifest(self, entries):
        path = os.path.join(self.tmp, bundle_mirror.MANIFEST_NAME)
        with open(path, 'w', encoding='utf-8') as handle:
            json.dump({'mirrors': list(entries)}, handle)
        return path

    def test_mirror_path_maps_plugin_runtime(self):
        self.assertEqual(
            bundle_mirror.mirror_path('plugin/runtime/brother/core/registry.py'),
            'bundle/runtime/brother/core/registry.py',
        )

    def test_mirror_path_refuses_traversal_and_hostile(self):
        for bad in (None, '', 123, float('nan'), b'bytes',
                    '/etc/passwd',
                    'plugin/runtime/../secret.py',
                    'plugin/other/file.py',
                    'plugin/runtime/',
                    'plugin/runtime/file' + chr(0) + '.py'):
            with self.assertRaises(ValueError):
                bundle_mirror.mirror_path(bad)

    def test_check_mirror_true_on_identical_bytes(self):
        source = self._write('source.bin', bytes([255, 254, 0]) + b'hello')
        dest = self._write('dest.bin', bytes([255, 254, 0]) + b'hello')
        self.assertTrue(bundle_mirror.check_mirror(source, dest))

    def test_check_mirror_false_on_corrupt_or_missing(self):
        source = self._write('source.bin', b'one')
        dest = self._write('dest.bin', b'two')
        self.assertFalse(bundle_mirror.check_mirror(source, dest))
        self.assertFalse(bundle_mirror.check_mirror(os.path.join(self.tmp, 'missing'), dest))
        self.assertFalse(bundle_mirror.check_mirror(source, os.path.join(self.tmp, 'missing')))
        self.assertFalse(bundle_mirror.check_mirror(self.tmp, dest))
        for bad in (None, 3.14, float('nan')):
            with self.assertRaises(ValueError):
                bundle_mirror.check_mirror(bad, dest)
            with self.assertRaises(ValueError):
                bundle_mirror.check_mirror(source, bad)

    def test_check_manifest_requires_each_mirror(self):
        entries = [bundle_mirror.SOURCE_ROOT + '/' + rel for rel in bundle_mirror.REQUIRED_MIRRORS]
        manifest = self._manifest(entries)
        self.assertTrue(bundle_mirror.check_manifest(manifest))
        partial = self._manifest(entries[:-1])
        self.assertFalse(bundle_mirror.check_manifest(partial))
        empty = self._manifest([])
        self.assertFalse(bundle_mirror.check_manifest(empty))

    def test_check_manifest_refuses_missing_or_corrupt(self):
        missing = os.path.join(self.tmp, 'no-manifest.json')
        self.assertFalse(bundle_mirror.check_manifest(missing))
        self.assertFalse(bundle_mirror.check_manifest(self.tmp))
        empty = self._write('empty.json', b'')
        self.assertFalse(bundle_mirror.check_manifest(empty))
        bad_json = self._write('bad.json', b'{not json')
        self.assertFalse(bundle_mirror.check_manifest(bad_json))
        bad_utf8 = self._write('bad-utf8.json', bytes([255, 254]))
        self.assertFalse(bundle_mirror.check_manifest(bad_utf8))
        not_object = self._write('list.json', b'[]')
        self.assertFalse(bundle_mirror.check_manifest(not_object))
        no_mirrors = self._write('no-mirrors.json', json.dumps({'other': []}).encode('utf-8'))
        self.assertFalse(bundle_mirror.check_manifest(no_mirrors))
        bad_mirrors = self._write('bad-mirrors.json', json.dumps({'mirrors': {}}).encode('utf-8'))
        self.assertFalse(bundle_mirror.check_manifest(bad_mirrors))
        for bad in (None, 42, float('nan'), b'bytes'):
            with self.assertRaises(ValueError):
                bundle_mirror.check_manifest(bad)


if __name__ == '__main__':
    unittest.main()

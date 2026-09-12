import unittest
import pathlib
import sys
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
from unittest.mock import patch
import release_note_from_tree as notes

class PatchNotes(unittest.TestCase):
    def build(self, manifest=('body', 'a'*64, 17, None), version='1.0.15'):
        with patch.object(notes, 'extra_notes', return_value=notes.PATCH_NOTE_MARKER+'\nAdds native evidence and experimental claim verification.'), patch.object(notes, 'head_rev', return_value='b'*40), patch.object(notes, 'manifest_version', return_value=version), patch.object(notes, 'export_manifest', return_value=manifest), patch.object(notes, 'run_suite', side_effect=AssertionError('patch format cannot claim suite measurements')):
            return notes.build_for_release('1.0.15')
    def test_measured_identity_without_unrun_claims(self):
        body, problems = self.build()
        self.assertEqual(problems, [])
        self.assertIn('`'+'a'*64+'` over 17 exported file(s)', body)
        self.assertIn('b'*40, body)
        self.assertIn('--verify-tree --tag v1.0.15', body)
        self.assertNotIn('tests passing', body)
        self.assertNotIn('PASS', body)
    def test_manifest_failure_refuses(self):
        body, problems = self.build(manifest=('', '', 0, 'NO-DATA: unavailable'))
        self.assertIsNone(body)
        self.assertTrue(problems)
    def test_wrong_version_refuses(self):
        body, problems = self.build(version='1.0.14')
        self.assertIsNone(body)
        self.assertTrue(problems)
    def test_manifest_change_changes_note(self):
        first, _ = self.build()
        second, _ = self.build(manifest=('changed', 'c'*64, 18, None))
        self.assertNotEqual(first, second)
        self.assertIn('over 18 exported file(s)', second)
    def test_legacy_dispatch_preserved(self):
        with patch.object(notes, 'extra_notes', return_value='Ordinary notes'), patch.object(notes, 'build', return_value=('legacy', [])) as build:
            self.assertEqual(notes.build_for_release('1.0.14'), ('legacy', []))
            build.assert_called_once_with('1.0.14')

if __name__ == '__main__':
    unittest.main()

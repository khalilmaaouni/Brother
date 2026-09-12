import json
import pathlib
import subprocess
import tempfile
import unittest
from unittest.mock import patch
import cursor_battery


class BatteryTests(unittest.TestCase):
    def exercise(self, failure=None, signed=False):
        with tempfile.TemporaryDirectory() as root:
            dest = pathlib.Path(root) / 'evidence'
            calls = []
            def fake(argv, **kwargs):
                calls.append(argv)
                code = failure[1] if failure and any(failure[0] in a for a in argv) else 0
                return subprocess.CompletedProcess(argv, code, 'abc123\n' if 'rev-parse' in argv else '', '')
            with patch.object(cursor_battery.subprocess, 'run', side_effect=fake):
                code = cursor_battery.run_battery('v1.0.15', '/fixture/repo', dest, signed)
            return code, json.loads((dest / 'receipt.json').read_text()), calls

    def test_pins_tag_and_runs_tagged_tools(self):
        code, report, calls = self.exercise(signed=True)
        self.assertEqual(code, 0)
        self.assertIn('v1.0.15', calls[0])
        self.assertEqual(report['revision'], 'abc123')
        self.assertEqual(len(report['checks']), 6)
        self.assertIn('--signed-in', calls[-1])

    def test_failed_package_never_passes(self):
        code, report, _ = self.exercise(('test_cursor_plugin.py', 1))
        self.assertEqual(code, 1)
        self.assertEqual(report['verdict'], 'FAIL')

    def test_auth_missing_stays_no_data(self):
        code, report, _ = self.exercise(('cursor_smoke.py', 2))
        self.assertEqual(code, 2)
        self.assertEqual(report['verdict'], 'NO-DATA')

    def test_clone_failure_stops_execution(self):
        code, report, calls = self.exercise(('clone', 128))
        self.assertEqual(code, 1)
        self.assertEqual(len(calls), 1)
        self.assertIsNone(report['revision'])

    def test_no_signed_in_claim_without_execution(self):
        _, report, calls = self.exercise()
        self.assertTrue(report['signed_in'].startswith('NO-DATA'))
        self.assertFalse(any('--signed-in' in c for c in calls))

    def test_invalid_tag_refused(self):
        with self.assertRaises(ValueError):
            cursor_battery.run_battery('--upload-pack=bad', '/repo', '/unused')

if __name__ == '__main__':
    unittest.main()

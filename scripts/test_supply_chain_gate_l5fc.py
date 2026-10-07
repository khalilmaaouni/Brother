import datetime
import hashlib
import json
import os
import pathlib
import tempfile
import unittest

try:
    from . import supply_chain_gate
except ImportError:
    import supply_chain_gate


class LicenseTests(unittest.TestCase):
    def test_T_HOSTILE_REFUSED(self):
        bad_values = (None, 1, True, 1.0, [], {}, float('nan'))
        for bad in bad_values:
            with self.subTest(bad=repr(bad)):
                with self.assertRaises(ValueError):
                    supply_chain_gate.allowed_license(bad)
        with self.assertRaises(ValueError):
            supply_chain_gate.fetch_registry_page(None, 1.0)
        with self.assertRaises(ValueError):
            supply_chain_gate.fetch_registry_page('https://pypi.org/pypi/x/json', None)
        with self.assertRaises(ValueError):
            supply_chain_gate.fetch_registry_page('https://pypi.org/pypi/x/json', float('nan'))
        with self.assertRaises(ValueError):
            supply_chain_gate.verify_licenses_offline(None, pathlib.Path('.'))
        with self.assertRaises(ValueError):
            supply_chain_gate.verify_licenses_online(None, datetime.datetime.now(datetime.timezone.utc))
        with self.assertRaises(ValueError):
            supply_chain_gate.verify_freshness(None, pathlib.Path('.'), datetime.datetime.now(datetime.timezone.utc))

    def test_T_LICENSE_ALLOW(self):
        self.assertTrue(supply_chain_gate.allowed_license('MIT'))
        self.assertTrue(supply_chain_gate.allowed_license('Apache-2.0'))
        self.assertFalse(supply_chain_gate.allowed_license('GPL-3.0-only'))
        self.assertFalse(supply_chain_gate.allowed_license('UNKNOWN'))

    def test_T_FETCH_200_RECOMPUTE(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = pathlib.Path(tmp)
            base = root / 'docs' / 'architecture' / 'L5F-fetched'
            base.mkdir(parents=True)
            snippet = base / 'x.json'
            data = json.dumps({'info': {'license': 'MIT'}}).encode('utf-8')
            snippet.write_bytes(data)
            good = {
                'eco': 'pypi',
                'fetched_http_status': 200,
                'raw_snippet_path': 'docs/architecture/L5F-fetched/x.json',
                'fetched_body_sha256': hashlib.sha256(data).hexdigest(),
                'fetched_license': 'MIT',
            }
            findings = supply_chain_gate.verify_licenses_offline([good], root)
            self.assertEqual([f for f in findings if f['severity'] == 'BLOCK'], [])
            bad = dict(good)
            bad['fetched_body_sha256'] = '0' * 64
            findings = supply_chain_gate.verify_licenses_offline([bad], root)
            self.assertTrue(any(f['code'] == 'snippet_hash_mismatch' for f in findings))

    def test_T_FETCH_RAISES(self):
        def raising_opener(url, timeout_s):
            raise RuntimeError('boom')
        result = supply_chain_gate.fetch_registry_page('https://pypi.org/pypi/x/json', 1.0, raising_opener)
        self.assertEqual(result['http_status'], 0)
        self.assertIn('boom', result['error'])

    def test_T_FETCH_OPENER_NON_BYTES_REFUSED(self):
        def bad_opener(url, timeout_s):
            return 'not bytes'
        result = supply_chain_gate.fetch_registry_page('https://pypi.org/pypi/x/json', 1.0, bad_opener)
        self.assertEqual(result['http_status'], 0)
        self.assertIn('non bytes', result['error'])

    def test_T_FETCH_SNIPPET_LICENSE_MISMATCH(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = pathlib.Path(tmp)
            base = root / 'docs' / 'architecture' / 'L5F-fetched'
            base.mkdir(parents=True)
            snippet = base / 'x.json'
            data = json.dumps({'info': {'license': 'MIT'}}).encode('utf-8')
            snippet.write_bytes(data)
            row = {
                'eco': 'pypi',
                'fetched_http_status': 200,
                'raw_snippet_path': 'docs/architecture/L5F-fetched/x.json',
                'fetched_body_sha256': hashlib.sha256(data).hexdigest(),
                'fetched_license': 'Apache-2.0',
            }
            findings = supply_chain_gate.verify_licenses_offline([row], root)
            self.assertTrue(any(f['code'] == 'snippet_license_mismatch' for f in findings))

    def test_T_STALE_FRESH_FUNCTION(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = pathlib.Path(tmp)
            pin = root / 'pyproject.toml'
            pin.write_text('x')
            old_time = datetime.datetime(2026, 9, 19, tzinfo=datetime.timezone.utc).timestamp()
            os.utime(str(pin), (old_time, old_time))
            now = datetime.datetime(2026, 9, 21, tzinfo=datetime.timezone.utc)
            row = {
                'pin_location': 'pyproject.toml:1',
                'fetched_on': '2026-09-20',
            }
            findings = supply_chain_gate.verify_freshness([row], root, now)
            self.assertEqual(findings, [])
            future = dict(row)
            future['fetched_on'] = '2026-09-22'
            findings = supply_chain_gate.verify_freshness([future], root, now)
            self.assertTrue(any(f['code'] == 'fetched_on_invalid' for f in findings))
            old = dict(row)
            old['fetched_on'] = '2026-01-01'
            findings = supply_chain_gate.verify_freshness([old], root, now)
            self.assertTrue(any(f['code'] == 'stale' for f in findings))
            missing = dict(row)
            missing['pin_location'] = 'missing.toml:1'
            findings = supply_chain_gate.verify_freshness([missing], root, now)
            self.assertTrue(any(f['code'] == 'pin_file_absent' for f in findings))


if __name__ == '__main__':
    unittest.main()

"""L5f-e tests: frozen audit record and human signoff."""

import datetime
import json
import os
import pathlib
import tempfile
import unittest

try:
    from . import supply_chain_gate
except ImportError:
    import supply_chain_gate


FROZEN_BEGIN = 'FROZEN MANIFEST l5f-supply-chain-audit-v1 BEGIN'
FROZEN_END = 'FROZEN MANIFEST l5f-supply-chain-audit-v1 END'
SCHEMA = 'l5f-supply-chain-audit-v1'

DOC_PATH = pathlib.Path(__file__).resolve().parent.parent / 'docs' / 'architecture' / 'L5F-SUPPLY-CHAIN-AUDIT.md'

H1_PENDING = 'H1 SIGNOFF: PENDING'
H2_PENDING = 'H2 SIGNOFF: PENDING'
H3_PENDING = 'H3 SIGNOFF: PENDING'


def good_audit():
    return {
        'header': {'schema': SCHEMA, 'manifests_searched': ['pyproject.toml']},
        'rows': [
            {
                'pin_location': 'requirements.txt:1',
                'lockfile_path': 'requirements.lock',
                'raw_snippet_path': 'docs/architecture/L5F-fetched/snippet.json',
            }
        ],
        'runtime_findings': [],
        'verdict': {'result': 'PASS'},
    }


def wrap(block_text):
    return '# audit\n' + FROZEN_BEGIN + '\n' + block_text + '\n' + FROZEN_END + '\n'


class FrozenAuditTests(unittest.TestCase):
    def test_hostile_inputs_refused(self):
        for bad in (None, 1, True, 1.0, float('nan'), b'bytes', [], {}, set(), ('a',)):
            with self.subTest(function='extract_frozen_block', bad=repr(bad)):
                with self.assertRaises(ValueError):
                    supply_chain_gate.extract_frozen_block(bad)
            with self.subTest(function='verify_human_signoff', bad=repr(bad)):
                with self.assertRaises(ValueError):
                    supply_chain_gate.verify_human_signoff(bad)
        for bad in (None, 'path', 1, True, 1.0, float('nan'), b'bytes', [], {}, set()):
            with self.subTest(function='load_frozen_audit', bad=repr(bad)):
                with self.assertRaises(ValueError):
                    supply_chain_gate.load_frozen_audit(bad)
        for bad in (None, 'audit', 1, True, 1.0, float('nan'), b'bytes', [], set(), ('a',)):
            with self.subTest(function='frozen_audit_findings', bad=repr(bad)):
                with self.assertRaises(ValueError):
                    supply_chain_gate.frozen_audit_findings(bad)

    def test_load_refuses_absent_directory_and_bad_bytes(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = pathlib.Path(tmp)
            with self.assertRaises(ValueError):
                supply_chain_gate.load_frozen_audit(root / 'absent.md')
            with self.assertRaises(ValueError):
                supply_chain_gate.load_frozen_audit(root)
            bad = root / 'bad.md'
            bad.write_bytes(b'\xff\xfe\x00')
            with self.assertRaises(ValueError):
                supply_chain_gate.load_frozen_audit(bad)

    def test_extract_frozen_block(self):
        payload = dict(a=1)
        text = wrap(json.dumps(payload))
        block = supply_chain_gate.extract_frozen_block(text)
        self.assertEqual(json.loads(block), payload)
        with self.assertRaises(ValueError):
            supply_chain_gate.extract_frozen_block('no markers here')
        with self.assertRaises(ValueError):
            supply_chain_gate.extract_frozen_block(FROZEN_BEGIN + '\n' + '{}')
        with self.assertRaises(ValueError):
            supply_chain_gate.extract_frozen_block('{}\n' + FROZEN_END)

    def test_load_frozen_audit_roundtrip_and_parse_error(self):
        with tempfile.TemporaryDirectory() as tmp:
            doc = pathlib.Path(tmp) / 'doc.md'
            doc.write_text(wrap(json.dumps(good_audit())), encoding='utf-8')
            parsed = supply_chain_gate.load_frozen_audit(doc)
            self.assertEqual(parsed['header']['schema'], SCHEMA)
            doc.write_text(wrap('{bad json}'), encoding='utf-8')
            with self.assertRaises(ValueError) as raised:
                supply_chain_gate.load_frozen_audit(doc)
            self.assertIn('line', str(raised.exception).lower())

    def test_frozen_audit_findings_clean_record(self):
        self.assertEqual(supply_chain_gate.frozen_audit_findings(good_audit()), [])

    def test_frozen_audit_findings_missing_key_blocks(self):
        bad = good_audit()
        del bad['runtime_findings']
        findings = supply_chain_gate.frozen_audit_findings(bad)
        codes = [item['code'] for item in findings]
        self.assertIn('missing_top_level_key', codes)
        self.assertTrue(any(item['severity'] == 'BLOCK' for item in findings))

    def test_frozen_audit_findings_schema_mismatch_blocks(self):
        bad = good_audit()
        bad['header']['schema'] = 'not-the-schema'
        codes = [item['code'] for item in supply_chain_gate.frozen_audit_findings(bad)]
        self.assertIn('schema_mismatch', codes)

    def test_frozen_audit_findings_empty_search_blocks(self):
        bad = good_audit()
        bad['rows'] = []
        bad['header']['manifests_searched'] = []
        codes = [item['code'] for item in supply_chain_gate.frozen_audit_findings(bad)]
        self.assertIn('empty_manifests_searched', codes)

    def test_frozen_audit_findings_newline_path_blocks(self):
        bad = good_audit()
        bad['rows'][0]['raw_snippet_path'] = 'a\nb.json'
        codes = [item['code'] for item in supply_chain_gate.frozen_audit_findings(bad)]
        self.assertIn('newline_in_path', codes)

    def test_signoff_all_pending_is_no_data(self):
        doc = '\n'.join([H1_PENDING, H2_PENDING, H3_PENDING])
        findings = supply_chain_gate.verify_human_signoff(doc)
        codes = set(item['code'] for item in findings)
        self.assertEqual(codes, {'signoff_pending:H1', 'signoff_pending:H2', 'signoff_pending:H3'})
        self.assertTrue(all(item['severity'] == 'NO-DATA' for item in findings))

    def test_signoff_missing_blocks(self):
        doc = '\n'.join([H1_PENDING, H3_PENDING])
        findings = supply_chain_gate.verify_human_signoff(doc)
        codes = [item['code'] for item in findings]
        self.assertIn('signoff_missing:H2', codes)
        missing = [item for item in findings if item['code'] == 'signoff_missing:H2']
        self.assertEqual(missing[0]['severity'], 'BLOCK')

    def test_signoff_duplicate_blocks_once(self):
        doc = '\n'.join([H1_PENDING, H1_PENDING, H2_PENDING, H3_PENDING])
        codes = [item['code'] for item in supply_chain_gate.verify_human_signoff(doc)]
        self.assertIn('signoff_duplicate:H1', codes)
        self.assertEqual(codes.count('signoff_duplicate:H1'), 1)

    def test_signoff_approved_without_approver_blocks(self):
        doc = '\n'.join(['H1 SIGNOFF: APPROVED by    on 2020-01-01', H2_PENDING, H3_PENDING])
        codes = [item['code'] for item in supply_chain_gate.verify_human_signoff(doc)]
        self.assertIn('signoff_no_approver', codes)

    def test_signoff_future_date_blocks(self):
        doc = '\n'.join(['H1 SIGNOFF: APPROVED by alice on 2999-01-01', H2_PENDING, H3_PENDING])
        codes = [item['code'] for item in supply_chain_gate.verify_human_signoff(doc)]
        self.assertIn('signoff_future_date', codes)

    def test_signoff_approved_today_is_not_blocked(self):
        today = datetime.datetime.now(datetime.timezone.utc).date().isoformat()
        doc = '\n'.join(['H1 SIGNOFF: APPROVED by alice on ' + today, H2_PENDING, H3_PENDING])
        codes = [item['code'] for item in supply_chain_gate.verify_human_signoff(doc)]
        self.assertEqual(codes, ['signoff_pending:H2', 'signoff_pending:H3'])

    def test_signoff_inside_frozen_block_blocks(self):
        doc = (
            FROZEN_BEGIN + '\n'
            + 'H1 SIGNOFF: PENDING\n'
            + FROZEN_END + '\n'
            + H2_PENDING + '\n'
            + H3_PENDING + '\n'
        )
        codes = [item['code'] for item in supply_chain_gate.verify_human_signoff(doc)]
        self.assertIn('signoff_inside_frozen_block', codes)

    def test_signoff_reads_document_text_not_parsed_json(self):
        doc = '\n'.join([H1_PENDING, H3_PENDING])
        missing = set(
            item['code']
            for item in supply_chain_gate.verify_human_signoff(doc)
            if item['code'].startswith('signoff_missing:')
        )
        self.assertEqual(missing, {'signoff_missing:H2'})

    @unittest.skipUnless(os.path.isfile(str(DOC_PATH)), 'audit doc not present in this tree')
    def test_shipped_doc_parses_and_carries_signoff_lines(self):
        parsed = supply_chain_gate.load_frozen_audit(DOC_PATH)
        self.assertEqual(parsed['header']['schema'], SCHEMA)
        text = DOC_PATH.read_text(encoding='utf-8')
        codes = set(item['code'] for item in supply_chain_gate.verify_human_signoff(text))
        for owner in ('H1', 'H2', 'H3'):
            self.assertNotIn('signoff_missing:' + owner, codes)


if __name__ == '__main__':
    unittest.main()

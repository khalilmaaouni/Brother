#!/usr/bin/env python3
'''Tests for L6.1 catalogue parse and noul gate.

Run: python3 scripts/test_l61.py
'''

import importlib.util
import os
import tempfile
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CONTRACT_PATH = os.path.join(ROOT, 'tools', 'jev_catalogue', 'contract.py')
LIVE_CATALOGUE = os.path.join(ROOT, 'docs', 'plan', 'JEV-USE-CASE-CATALOGUE.md')

_spec = importlib.util.spec_from_file_location('jev_catalogue_contract_l61', CONTRACT_PATH)
if _spec is None or _spec.loader is None:
    raise RuntimeError('cannot load ' + CONTRACT_PATH)
contract = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(contract)

load_catalogue_md = contract.load_catalogue_md
validate_entry = contract.validate_entry
gate_question_type = contract.gate_question_type

SAMPLE_MD = (
    '# Sample catalogue\n'
    '\n'
    '### 1. First question?\n'
    'State: `cmd` -> `out`.\n'
    'Question: `q1`: is it one?\n'
    'Answer: **noul = 0.98** (yes).\n'
    'Ledger: `jev-catalogue-01-first-123-456`.\n'
    '\n'
    '### 2. Second question?\n'
    'State: `cmd2` -> `out2`.\n'
    'Question: `q2`: is it two?\n'
    'Answer: **noul = 0.7** (abstain).\n'
    'Ledger: `jev-catalogue-02-second-124-457`.\n'
)


def _valid_entry():
    return {
        'number': 1,
        'holder_id': 'jev-catalogue-01-first-123-456',
        'slug': 'first-question',
        'question_id': 'q1',
        'type': 'noul',
        'state_excerpt': 'cmd -> out',
        'true_criteria': 'is it one?',
        'false_criteria': 'not (is it one?)',
        'answer': 0.98,
    }


class _HostileMapping(dict):
    '''Mapping whose membership test and lookups refuse, as an unhashable key would.'''

    def __contains__(self, key):
        raise TypeError("unhashable type: 'list'")

    def __getitem__(self, key):
        raise TypeError("unhashable type: 'list'")


class TestL61Catalogue(unittest.TestCase):
    def test_load_catalogue_md_parses_entries(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, 'cat.md')
            with open(path, 'wb') as fh:
                fh.write(SAMPLE_MD.encode('utf-8'))
            entries = load_catalogue_md(path)
        self.assertEqual(len(entries), 2)
        first = entries[0]
        self.assertEqual(first['number'], 1)
        self.assertEqual(first['holder_id'], 'jev-catalogue-01-first-123-456')
        self.assertEqual(first['question_id'], 'q1')
        self.assertEqual(first['type'], 'noul')
        self.assertIn('out', first['state_excerpt'])
        self.assertEqual(first['answer'], 0.98)
        self.assertTrue(first['true_criteria'])
        self.assertTrue(first['false_criteria'])
        ok, reason = validate_entry(first)
        self.assertTrue(ok, reason)

    def test_empty_catalogue_is_no_data(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, 'empty.md')
            with open(path, 'wb') as fh:
                fh.write(b'# Empty\n')
            self.assertEqual(load_catalogue_md(path), [])

    def test_missing_catalogue_is_no_data(self):
        self.assertEqual(load_catalogue_md('/no/such/catalogue.md'), [])

    def test_directory_path_is_no_data(self):
        with tempfile.TemporaryDirectory() as tmp:
            self.assertEqual(load_catalogue_md(tmp), [])

    def test_nul_byte_path_is_no_data(self):
        self.assertEqual(load_catalogue_md('\x00bad\x00path.md'), [])

    def test_undecodable_catalogue_is_no_data(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, 'bad.md')
            with open(path, 'wb') as fh:
                fh.write(b'### 1. Bad\nState: \xff\nLedger: `jev-catalogue-01`\n')
            self.assertEqual(load_catalogue_md(path), [])

    def test_corrupt_section_is_blocked(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, 'corrupt.md')
            with open(path, 'wb') as fh:
                fh.write(b'### 3. Missing ledger\nState: `x`.\nQuestion: `q3`: ok?\nAnswer: **noul = 0.5**\n')
            entries = load_catalogue_md(path)
        self.assertEqual(len(entries), 1)
        ok, reason = validate_entry(entries[0])
        self.assertFalse(ok)
        self.assertIn('holder_id', reason)

    def test_corrupt_entry_number_is_blocked(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, 'big.md')
            with open(path, 'wb') as fh:
                fh.write((
                    '### '
                    + ('9' * 40)
                    + '. Overlong number\n'
                    + 'State: `cmd` -> `out`.\n'
                    + 'Question: `q9`: is it nine?\n'
                    + 'Answer: **noul = 0.98** (yes).\n'
                    + 'Ledger: `jev-catalogue-99-nine-123-456`.\n'
                ).encode('utf-8'))
            entries = load_catalogue_md(path)
        self.assertEqual(len(entries), 1)
        ok, reason = validate_entry(entries[0])
        self.assertFalse(ok)
        self.assertTrue(reason)

    def test_gate_allows_only_exact_noul(self):
        self.assertEqual(gate_question_type('noul'), (True, ''))
        for bad in ('score', 'choice', '', 'NOUL', ' noul', 'noul '):
            ok, reason = gate_question_type(bad)
            self.assertFalse(ok, bad)
            self.assertTrue(reason, bad)

    def test_validate_accepts_valid(self):
        ok, reason = validate_entry(_valid_entry())
        self.assertTrue(ok, reason)

    def test_validate_rejects_empty(self):
        entry = _valid_entry()
        entry['state_excerpt'] = ''
        ok, reason = validate_entry(entry)
        self.assertFalse(ok)
        self.assertIn('state_excerpt', reason)

    def test_validate_rejects_whitespace_state(self):
        entry = _valid_entry()
        entry['state_excerpt'] = '   '
        ok, reason = validate_entry(entry)
        self.assertFalse(ok)
        self.assertIn('state_excerpt', reason)

    def test_validate_rejects_score_type(self):
        entry = _valid_entry()
        entry['type'] = 'score'
        ok, reason = validate_entry(entry)
        self.assertFalse(ok)
        self.assertIn('score', reason)

    def test_validate_rejects_choice_type(self):
        entry = _valid_entry()
        entry['type'] = 'choice'
        ok, reason = validate_entry(entry)
        self.assertFalse(ok)
        self.assertIn('choice', reason)

    def test_validate_rejects_answer_out_of_range(self):
        entry = _valid_entry()
        entry['answer'] = 1.01
        ok, reason = validate_entry(entry)
        self.assertFalse(ok)
        self.assertIn('range', reason)
        entry['answer'] = -0.01
        ok, reason = validate_entry(entry)
        self.assertFalse(ok)
        self.assertIn('range', reason)
        entry['answer'] = 0.0
        ok, reason = validate_entry(entry)
        self.assertTrue(ok, reason)
        entry['answer'] = 1.0
        ok, reason = validate_entry(entry)
        self.assertTrue(ok, reason)
        entry['answer'] = 0.7
        ok, reason = validate_entry(entry)
        self.assertTrue(ok, reason)

    def test_validate_rejects_nan_and_bool_answer(self):
        entry = _valid_entry()
        entry['answer'] = float('nan')
        ok, reason = validate_entry(entry)
        self.assertFalse(ok)
        entry['answer'] = float('inf')
        ok, reason = validate_entry(entry)
        self.assertFalse(ok)
        entry['answer'] = True
        ok, reason = validate_entry(entry)
        self.assertFalse(ok)
        entry['answer'] = '0.5'
        ok, reason = validate_entry(entry)
        self.assertFalse(ok)

    def test_validate_rejects_huge_int_answer(self):
        entry = _valid_entry()
        entry['answer'] = 10 ** 400
        ok, reason = validate_entry(entry)
        self.assertFalse(ok)
        self.assertTrue(reason)

    def test_validate_rejects_missing_fields(self):
        fields = [
            'number',
            'holder_id',
            'slug',
            'question_id',
            'type',
            'state_excerpt',
            'true_criteria',
            'false_criteria',
            'answer',
        ]
        for field in fields:
            entry = _valid_entry()
            del entry[field]
            ok, reason = validate_entry(entry)
            self.assertFalse(ok, field)
            self.assertTrue(reason, field)

    def test_validate_rejects_empty_holder_id(self):
        entry = _valid_entry()
        entry['holder_id'] = '   '
        ok, reason = validate_entry(entry)
        self.assertFalse(ok)
        self.assertIn('holder_id', reason)

    def test_validate_rejects_wrong_types(self):
        entry = _valid_entry()
        entry['holder_id'] = None
        ok, reason = validate_entry(entry)
        self.assertFalse(ok)
        entry = _valid_entry()
        entry['question_id'] = 123
        ok, reason = validate_entry(entry)
        self.assertFalse(ok)
        entry = _valid_entry()
        entry['number'] = True
        ok, reason = validate_entry(entry)
        self.assertFalse(ok)
        entry = _valid_entry()
        entry['state_excerpt'] = ['not', 'a', 'string']
        ok, reason = validate_entry(entry)
        self.assertFalse(ok)

    def test_validate_rejects_holder_id_traversal(self):
        entry = _valid_entry()
        for bad in ('..', '....', 'a..b', '../../etc/passwd', 'deep/../..'):
            entry['holder_id'] = bad
            ok, reason = validate_entry(entry)
            self.assertFalse(ok, bad)
            self.assertTrue(reason, bad)

    def test_validate_rejects_holder_id_path(self):
        entry = _valid_entry()
        for bad in ('/etc/passwd', 'a/b', 'C:/tmp/evil', 'sub/dir', '%2e%2e%2fetc', 'a b'):
            entry['holder_id'] = bad
            ok, reason = validate_entry(entry)
            self.assertFalse(ok, bad)
            self.assertTrue(reason, bad)

    def test_validate_rejects_identifier_paths_in_all_id_fields(self):
        for field in ('holder_id', 'slug', 'question_id'):
            entry = _valid_entry()
            entry[field] = '../../etc/passwd'
            ok, reason = validate_entry(entry)
            self.assertFalse(ok, field)
            self.assertTrue(reason, field)
            entry = _valid_entry()
            entry[field] = '/etc/passwd'
            ok, reason = validate_entry(entry)
            self.assertFalse(ok, field)
            self.assertTrue(reason, field)

    def test_validate_rejects_hostile_ids(self):
        for field in ('holder_id', 'slug', 'question_id'):
            for bad in (None, 123, b'bytes', ['a'], {'k': 'v'}, ''):
                entry = _valid_entry()
                entry[field] = bad
                ok, reason = validate_entry(entry)
                self.assertFalse(ok, field + '=' + repr(bad))
                self.assertTrue(reason, field + '=' + repr(bad))

    def test_validate_rejects_hostile_input(self):
        self.assertFalse(validate_entry(None)[0])
        self.assertFalse(validate_entry([])[0])
        self.assertFalse(validate_entry('entry')[0])
        self.assertFalse(validate_entry(123)[0])
        self.assertEqual(load_catalogue_md(None), [])
        self.assertEqual(load_catalogue_md(123), [])
        self.assertEqual(load_catalogue_md(b'path'), [])

    def test_validate_unhashable_key_mapping_is_blocked(self):
        entry = _HostileMapping(_valid_entry())
        ok, reason = validate_entry(entry)
        self.assertFalse(ok)
        self.assertTrue(reason)

    @unittest.skipUnless(os.path.isfile(LIVE_CATALOGUE), 'live catalogue not present')
    def test_live_catalogue_parses_and_validates(self):
        entries = load_catalogue_md(LIVE_CATALOGUE)
        self.assertGreaterEqual(len(entries), 10)
        for entry in entries:
            ok, reason = validate_entry(entry)
            self.assertTrue(ok, str(entry.get('holder_id')) + ': ' + reason)


if __name__ == '__main__':
    unittest.main(verbosity=2)

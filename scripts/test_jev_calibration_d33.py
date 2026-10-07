import json
import os
import sys
import tempfile
import unittest

_HERE = os.path.dirname(os.path.abspath(__file__))
if _HERE not in sys.path:
    sys.path.insert(0, _HERE)

import jev_calibration as cal


def _terminal_row(attempt_id, phase='answered', family='fam', qtype='noul',
                  framing='a' * 16, model='m', decision_id=None, **extra):
    row = {
        'decision_id': decision_id,
        'schema': 'attempt/v1',
        'phase': phase,
        'attempt_id': attempt_id,
        'parent_id': None,
        'entry_id': 'entry-1',
        'mode': 'act',
        'at': '2026-09-20T00:00:00Z',
        'family': family,
        'qtype': qtype,
        'framing': framing,
        'model': model,
        'answer': True,
        'prob': 0.9,
        'confidence': 0.9,
        'cost': 0.0,
        'reason': None,
        'audit': True,
    }
    row.update(extra)
    return row


class TestBinderD33(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.attempts = os.path.join(self.tmp.name, 'attempts.jsonl')
        self.outcomes = os.path.join(self.tmp.name, 'outcomes.jsonl')
        self.decisions = os.path.join(self.tmp.name, 'decisions.jsonl')

    def _write_attempt(self, attempt_id, **kwargs):
        cal.append_terminal(
            self.attempts,
            _terminal_row(attempt_id, **kwargs),
            max_segment_bytes=None,
            retention_segments=None,
        )

    def _write_corrupt_attempt_line(self):
        with open(self.attempts, 'a', encoding='utf-8') as f:
            f.write('this is not json\n')

    def test_proposition_hash_shape_and_stability(self):
        h1 = cal.proposition_hash('fam', 'noul', 'a' * 16, 'm')
        h2 = cal.proposition_hash('fam', 'noul', 'a' * 16, 'm')
        self.assertEqual(h1, h2)
        self.assertEqual(len(h1), 16)
        self.assertRegex(h1, r'^[0-9a-f]{16}$')
        self.assertNotEqual(h1, cal.proposition_hash('fam', 'noul', 'b' * 16, 'm'))
        self.assertNotEqual(h1, cal.proposition_hash('fam', 'noul', 'a' * 16, 'm2'))
        self.assertNotEqual(h1, cal.proposition_hash('fam2', 'noul', 'a' * 16, 'm'))

    def test_outcome_binds_to_correct_attempt(self):
        attempt_id = 'b' * 32
        self._write_attempt(attempt_id, family='fam', qtype='noul',
                            framing='a' * 16, model='m', decision_id='fam:' + 'a' * 16 + ':' + '2' * 16)
        good_hash = cal.proposition_hash('fam', 'noul', 'a' * 16, 'm')
        outcome = {
            'id': 'fam:' + 'a' * 16 + ':' + '2' * 16,
            'correct': False,
            'source': 'test',
            'at': '2026-09-20T00:02:00Z',
            'attempt_id': attempt_id,
            'proposition_hash': good_hash,
        }
        cal.append_outcome(
            self.outcomes,
            outcome,
            attempts_path=self.attempts,
            allow_unchecked=True,
            max_segment_bytes=None,
            retention_segments=None,
        )
        with open(self.outcomes, 'r', encoding='utf-8') as f:
            written = [json.loads(line) for line in f if line.strip()]
        self.assertEqual(len(written), 1)
        self.assertEqual(written[0]['attempt_id'], attempt_id)

    def test_outcome_refused_when_proposition_hash_mismatch(self):
        attempt_id = 'a' * 32
        self._write_attempt(attempt_id)
        outcome = {
            'id': 'fam:item-1',
            'correct': True,
            'source': 'test',
            'at': '2026-09-20T00:01:00Z',
            'attempt_id': attempt_id,
            'proposition_hash': '0' * 16,
        }
        with self.assertRaises(ValueError):
            cal.append_outcome(
                self.outcomes,
                outcome,
                attempts_path=self.attempts,
                allow_unchecked=True,
            )
        self.assertFalse(os.path.exists(self.outcomes))

    def test_outcome_refused_when_attempt_already_labelled(self):
        attempt_id = 'c' * 32
        self._write_attempt(attempt_id, decision_id='fam:' + 'a' * 16 + ':' + '3' * 16)
        good_hash = cal.proposition_hash('fam', 'noul', 'a' * 16, 'm')
        outcome = {
            'id': 'fam:' + 'a' * 16 + ':' + '3' * 16,
            'correct': True,
            'source': 'test',
            'at': '2026-09-20T00:03:00Z',
            'attempt_id': attempt_id,
            'proposition_hash': good_hash,
        }
        cal.append_outcome(
            self.outcomes,
            outcome,
            attempts_path=self.attempts,
            allow_unchecked=True,
            max_segment_bytes=None,
            retention_segments=None,
        )
        with self.assertRaises(ValueError):
            cal.append_outcome(
                self.outcomes,
                outcome,
                attempts_path=self.attempts,
                allow_unchecked=True,
            )

    def test_outcome_refused_when_attempts_ledger_corrupt(self):
        attempt_id = 'd' * 32
        self._write_attempt(attempt_id)
        self._write_corrupt_attempt_line()
        good_hash = cal.proposition_hash('fam', 'noul', 'a' * 16, 'm')
        outcome = {
            'id': 'fam:item-4',
            'correct': True,
            'source': 'test',
            'at': '2026-09-20T00:04:00Z',
            'attempt_id': attempt_id,
            'proposition_hash': good_hash,
        }
        with self.assertRaises(ValueError):
            cal.append_outcome(
                self.outcomes,
                outcome,
                attempts_path=self.attempts,
                allow_unchecked=True,
            )
        self.assertFalse(os.path.exists(self.outcomes))

    def test_outcome_refused_when_attempt_phase_is_not_answered(self):
        attempt_id = 'e' * 32
        self._write_attempt(attempt_id, phase='no_data', qtype=None,
                            framing=None, model='', answer=None,
                            prob=None, confidence=None, reason='no data',
                            audit=False)
        outcome = {
            'id': 'fam:item-5',
            'correct': True,
            'source': 'test',
            'at': '2026-09-20T00:05:00Z',
            'attempt_id': attempt_id,
            'proposition_hash': 'a' * 16,
        }
        with self.assertRaises(ValueError):
            cal.append_outcome(
                self.outcomes,
                outcome,
                attempts_path=self.attempts,
                allow_unchecked=True,
            )

    def test_outcome_refused_when_attempt_id_absent_from_ledger(self):
        outcome = {
            'id': 'fam:item-6',
            'correct': True,
            'source': 'test',
            'at': '2026-09-20T00:06:00Z',
            'attempt_id': 'f' * 32,
            'proposition_hash': 'a' * 16,
        }
        with self.assertRaises(ValueError):
            cal.append_outcome(
                self.outcomes,
                outcome,
                attempts_path=self.attempts,
                allow_unchecked=True,
            )

    def test_outcome_refused_when_attempts_ledger_unreadable(self):
        outcome = {
            'id': 'fam:item-7',
            'correct': True,
            'source': 'test',
            'at': '2026-09-20T00:07:00Z',
            'attempt_id': '0' * 32,
            'proposition_hash': 'a' * 16,
        }
        with self.assertRaises(ValueError):
            cal.append_outcome(
                self.outcomes,
                outcome,
                attempts_path=self.tmp.name,
                allow_unchecked=True,
            )

    def test_outcome_refused_when_both_attempt_keys_not_both_present(self):
        attempt_id = '1' * 32
        self._write_attempt(attempt_id)
        only_attempt_id = {
            'id': 'fam:item-8',
            'correct': True,
            'source': 'test',
            'at': '2026-09-20T00:08:00Z',
            'attempt_id': attempt_id,
        }
        with self.assertRaises(ValueError):
            cal.append_outcome(
                self.outcomes,
                only_attempt_id,
                attempts_path=self.attempts,
                allow_unchecked=True,
            )
        only_hash = {
            'id': 'fam:item-9',
            'correct': True,
            'source': 'test',
            'at': '2026-09-20T00:09:00Z',
            'proposition_hash': 'a' * 16,
        }
        with self.assertRaises(ValueError):
            cal.append_outcome(
                self.outcomes,
                only_hash,
                attempts_path=self.attempts,
                allow_unchecked=True,
            )

    def test_hostile_input_refused(self):
        for bad in (None, 123, True, b'bytes', [], {}, float('nan')):
            with self.assertRaises(ValueError):
                cal.proposition_hash('fam', 'noul', None, bad)
        with self.assertRaises(ValueError):
            cal.proposition_hash(None, 'noul', None, 'm')
        with self.assertRaises(ValueError):
            cal.proposition_hash('fam', 'bad', None, 'm')
        with self.assertRaises(ValueError):
            cal.proposition_hash('fam', 'noul', 123, 'm')
        outcome = {
            'id': 'fam:item-10',
            'correct': True,
            'source': 'test',
            'at': '2026-09-20T00:10:00Z',
            'attempt_id': 123,
            'proposition_hash': 'a' * 16,
        }
        with self.assertRaises(ValueError):
            cal.append_outcome(
                self.outcomes,
                outcome,
                attempts_path=self.attempts,
                allow_unchecked=True,
            )
        outcome2 = {
            'id': 'fam:item-11',
            'correct': True,
            'source': 'test',
            'at': '2026-09-20T00:11:00Z',
            'attempt_id': 'a' * 32,
            'proposition_hash': 123,
        }
        with self.assertRaises(ValueError):
            cal.append_outcome(
                self.outcomes,
                outcome2,
                attempts_path=self.attempts,
                allow_unchecked=True,
            )

    def test_hostile_outcome_path_int_refused(self):
        legacy = {
            'id': 'fam:item-12',
            'correct': True,
            'source': 'test',
            'at': '2026-09-20T00:12:00Z',
        }
        with self.assertRaises(ValueError):
            cal.append_outcome(123, legacy, allow_unchecked=True)

    def test_hostile_outcome_path_none_refused(self):
        legacy = {
            'id': 'fam:item-13',
            'correct': True,
            'source': 'test',
            'at': '2026-09-20T00:13:00Z',
        }
        with self.assertRaises(ValueError):
            cal.append_outcome(None, legacy, allow_unchecked=True)

    def test_hostile_outcome_path_directory_refused(self):
        legacy = {
            'id': 'fam:item-14',
            'correct': True,
            'source': 'test',
            'at': '2026-09-20T00:14:00Z',
        }
        with self.assertRaises(ValueError):
            cal.append_outcome(self.tmp.name, legacy, allow_unchecked=True)

    def test_legacy_outcome_rows_still_validate(self):
        cal.append_decision(
            self.decisions,
            {
                'id': 'fam:legacy-1',
                'family': 'fam',
                'qtype': 'noul',
                'framing': 'a' * 16,
                'answer': True,
                'prob': 0.9,
                'confidence': 0.9,
                'model': 'm',
                'cost': 0.0,
                'at': '2026-09-20T00:00:00Z',
            },
            max_segment_bytes=None,
            retention_segments=None,
        )
        legacy = {
            'id': 'fam:legacy-1',
            'correct': True,
            'source': 'test',
            'at': '2026-09-20T00:12:00Z',
        }
        cal.append_outcome(
            self.outcomes,
            legacy,
            decisions_path=self.decisions,
            max_segment_bytes=None,
            retention_segments=None,
        )
        with open(self.outcomes, 'r', encoding='utf-8') as f:
            written = [json.loads(line) for line in f if line.strip()]
        self.assertEqual(len(written), 1)
        self.assertNotIn('attempt_id', written[0])


    def test_outcome_refused_when_outcome_id_differs_from_terminal_decision_id(self):
        # D3 section 5: an extended outcome's id must equal the terminal
        # row's decision_id, so a label can never be pinned to a different
        # decision than the one the attempt actually answered.
        attempt_id = 'd' * 32
        self._write_attempt(attempt_id, decision_id='fam:' + 'a' * 16 + ':' + '4' * 16)
        good_hash = cal.proposition_hash('fam', 'noul', 'a' * 16, 'm')
        outcome = {
            'id': 'fam:' + 'a' * 16 + ':' + '5' * 16,
            'correct': True,
            'source': 'test',
            'at': '2026-09-20T00:04:00Z',
            'attempt_id': attempt_id,
            'proposition_hash': good_hash,
        }
        with self.assertRaises(ValueError):
            cal.append_outcome(self.outcomes, outcome, attempts_path=self.attempts,
                               allow_unchecked=True)
        self.assertFalse(os.path.exists(self.outcomes))

    def test_outcome_refused_when_terminal_row_has_no_decision_id(self):
        attempt_id = 'e' * 32
        self._write_attempt(attempt_id)  # decision_id None
        good_hash = cal.proposition_hash('fam', 'noul', 'a' * 16, 'm')
        outcome = {
            'id': 'fam:item-5',
            'correct': True,
            'source': 'test',
            'at': '2026-09-20T00:05:00Z',
            'attempt_id': attempt_id,
            'proposition_hash': good_hash,
        }
        with self.assertRaises(ValueError):
            cal.append_outcome(self.outcomes, outcome, attempts_path=self.attempts,
                               allow_unchecked=True)
        self.assertFalse(os.path.exists(self.outcomes))

    def test_outcome_binds_when_a_submission_row_precedes_the_terminal_row(self):
        # The real ledger holds the seam's submission row beside the terminal
        # row; the binder must read past it, never call the ledger corrupt.
        attempt_id = 'f' * 32
        cal.append_submission(self.attempts, {
            'schema': 'attempt/v1', 'phase': 'submitted', 'attempt_id': attempt_id,
            'parent_id': None, 'entry_id': 'entry-1', 'mode': 'act',
            'at': '2026-09-20T00:00:00Z',
        }, max_segment_bytes=None, retention_segments=None)
        did = 'fam:' + 'a' * 16 + ':' + '6' * 16
        self._write_attempt(attempt_id, decision_id=did)
        outcome = {
            'id': did, 'correct': True, 'source': 'test', 'at': '2026-09-20T00:06:00Z',
            'attempt_id': attempt_id,
            'proposition_hash': cal.proposition_hash('fam', 'noul', 'a' * 16, 'm'),
        }
        cal.append_outcome(self.outcomes, outcome, attempts_path=self.attempts,
                           allow_unchecked=True, max_segment_bytes=None, retention_segments=None)
        with open(self.outcomes, 'r', encoding='utf-8') as f:
            self.assertEqual(len([l for l in f if l.strip()]), 1)

if __name__ == '__main__':
    unittest.main()

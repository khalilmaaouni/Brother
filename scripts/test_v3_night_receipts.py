"""Calibration for scripts/v3_night_receipts.py.

The behaviour this file exists to hold: the applied and declined lists in a receipt are the
JUDGE's verdicts, never the recorder's. So the tests re-derive every verdict from
scripts/v3_judge.py over the same diff and demand the receipt match, which means a hand-edited
receipt that flatters the number fails here rather than reaching the report. The integration
case runs the real bm_recurrence.py through v3_receipts.py's CLI wrapper (never a stub) and
pins the denominator at five, the whole point of row V3.
"""
import os
import sys
import tempfile
import unittest
from pathlib import Path

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import v3_night_receipts as N  # noqa: E402
import v3_judge as J  # noqa: E402


class EveryUnitIsReal(unittest.TestCase):
    def test_every_unit_has_its_diff_on_disk(self):
        for unit in N.UNITS:
            path = N.DIFFS / unit['diff']
            self.assertTrue(path.exists(), '%s has no diff at %s' % (unit['unit_id'], path))

    def test_every_lesson_id_has_a_rule(self):
        for unit in N.UNITS:
            for lesson_id in unit['lessons']:
                self.assertIn(lesson_id, J.RULES, unit['unit_id'])

    def test_a_unit_claiming_before_first_write_carries_an_ordering_witness(self):
        """before_first_write with no witness is the exact shape bm_recurrence.py excludes."""
        for unit in N.UNITS:
            if unit['before_first_write']:
                self.assertTrue(unit['witness'].strip(), unit['unit_id'])

    def test_the_judge_is_not_the_worker(self):
        self.assertNotEqual(N.JUDGE, N.WORKER)


class TheVerdictsComeFromTheJudge(unittest.TestCase):
    def test_applied_and_declined_match_a_fresh_judging_of_the_diff(self):
        for unit in N.UNITS:
            record = N.judged(unit)
            diff_text = J.read_diff(str(N.DIFFS / unit['diff']))
            applied, declined = [], []
            for lesson_id in unit['lessons']:
                verdict, _ev = J.judge(diff_text, lesson_id)
                if verdict == J.APPLIED:
                    applied.append(lesson_id)
                elif verdict == J.DECLINED:
                    declined.append(lesson_id)
            self.assertEqual(record['applied'], applied, unit['unit_id'])
            self.assertEqual(record['declined'], declined, unit['unit_id'])

    def test_a_declined_id_always_carries_a_reason(self):
        for unit in N.UNITS:
            record = N.judged(unit)
            if record['declined']:
                self.assertTrue(record['reason'].strip(), unit['unit_id'])


class TheDenominatorIsFive(unittest.TestCase):
    def setUp(self):
        fd, self.db = tempfile.mkstemp(suffix='.sqlite3')
        os.close(fd)
        os.unlink(self.db)

    def tearDown(self):
        if os.path.exists(self.db):
            os.unlink(self.db)  # sbe: allow-silent nothing to clean if the run wrote nothing

    def test_recording_all_five_reaches_denominator_5_and_prints_a_rate(self):
        code = N.main(['--db', self.db])
        self.assertEqual(code, 0)
        import v3_receipts as V
        report = V.report(self.db)
        self.assertIn('denominator=5', report)
        self.assertIn('pre-action memory application rate:', report)

    def test_the_estate_store_is_refused_and_nothing_is_written(self):
        code = N.main(['--db', '/tmp/v3-night-guard-check/.brothermode/recurrence.sqlite3'])
        self.assertEqual(code, 2)
        self.assertFalse(Path('/tmp/v3-night-guard-check').exists())


if __name__ == '__main__':
    unittest.main()

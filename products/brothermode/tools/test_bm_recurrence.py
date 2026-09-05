"""Calibration for tools/bm_recurrence.py, in both directions.

REBUILT alongside the tool it tests, from a specification recovered out of a .pyc after the
originals were deleted from a shared tree. The contract this file asserts is the part that
survived and matters: what counts as an applicable lesson, what may not be claimed, and the
threshold below which no percentage is printed.
"""
import json
import contextlib
import io
import os
import sqlite3
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import bm_recurrence as R  # noqa: E402


class Contract(unittest.TestCase):
    """You cannot claim a lesson helped when retrieval never returned it."""

    def setUp(self):
        self.db = tempfile.NamedTemporaryFile(suffix='.sqlite3', delete=False).name
        os.unlink(self.db)

    def tearDown(self):
        if os.path.exists(self.db):
            os.unlink(self.db)  # sbe: allow-silent nothing to clean if the test never wrote

    def test_a_plain_receipt_records(self):
        R.record_receipt('u1', ['a'], ['a'], [], '', True, self.db)
        self.assertEqual(R.compute_report(self.db)['denominator'], 1)

    def test_applying_an_id_that_was_never_surfaced_is_REFUSED(self):
        with self.assertRaises(ValueError):
            R.record_receipt('u1', ['a'], ['b'], [], '', True, self.db)

    def test_declining_an_id_that_was_never_surfaced_is_REFUSED(self):
        with self.assertRaises(ValueError):
            R.record_receipt('u1', ['a'], [], ['b'], 'why', True, self.db)

    def test_an_id_both_applied_and_declined_is_REFUSED(self):
        with self.assertRaises(ValueError):
            R.record_receipt('u1', ['a'], ['a'], ['a'], 'why', True, self.db)

    def test_declining_with_no_reason_is_REFUSED(self):
        """Seen-and-not-used with no reason cannot be told apart from padding
        the denominator, which is the metric's main gaming vector."""
        with self.assertRaises(ValueError):
            R.record_receipt('u1', ['a'], [], ['a'], '   ', True, self.db)

    def test_an_empty_unit_id_is_REFUSED(self):
        with self.assertRaises(ValueError):
            R.record_receipt('', ['a'], ['a'], [], '', True, self.db)

    def test_a_receipt_is_upserted_by_unit_id_not_duplicated(self):
        R.record_receipt('u1', ['a'], ['a'], [], '', True, self.db)
        R.record_receipt('u1', ['a'], [], ['a'], 'changed my mind', True, self.db)
        self.assertEqual(R.compute_report(self.db)['total_units'], 1)


class TheRate(unittest.TestCase):
    def setUp(self):
        self.db = tempfile.NamedTemporaryFile(suffix='.sqlite3', delete=False).name
        os.unlink(self.db)

    def tearDown(self):
        if os.path.exists(self.db):
            os.unlink(self.db)  # sbe: allow-silent nothing to clean if the test never wrote

    def fill(self, n, applied=True, before=True):
        for i in range(n):
            R.record_receipt('u%d' % i, ['a'], ['a'] if applied else [],
                             [] if applied else ['a'], '' if applied else 'not relevant',
                             before, self.db,
                             # an independent judge and an ordering witness so a
                             # properly judged unit still counts toward the numerator (V9)
                             judge='reader-1', worker='worker-1', witness='surfaced 10:00 < write 10:05')

    def test_a_unit_with_NO_applicable_lesson_is_not_in_the_denominator(self):
        """The definition that makes or breaks the number. A unit where nothing
        applied and nothing was declined had no applicable lesson at all, and
        counting it would dilute the rate with work the memory never touched."""
        R.record_receipt('u1', ['a'], [], [], '', True, self.db)
        r = R.compute_report(self.db)
        self.assertEqual(r['denominator'], 0)
        self.assertEqual(r['total_units'], 1)

    def test_below_the_threshold_NO_rate_is_printed(self):
        self.fill(R.MIN_DENOMINATOR - 1)
        self.assertIsNone(R.compute_report(self.db)['rate'])

    def test_at_the_threshold_a_rate_appears(self):
        self.fill(R.MIN_DENOMINATOR)
        self.assertEqual(R.compute_report(self.db)['rate'], 100.0)

    def test_it_can_read_ZERO_percent(self):
        """The direction that matters. A counter that can only report success
        is an advertisement."""
        self.fill(R.MIN_DENOMINATOR, applied=False)
        self.assertEqual(R.compute_report(self.db)['rate'], 0.0)

    def test_applied_but_AFTER_the_first_write_does_not_count(self):
        """Surfacing a lesson after the damage is done is not prevention."""
        self.fill(R.MIN_DENOMINATOR, applied=True, before=False)
        self.assertEqual(R.compute_report(self.db)['rate'], 0.0)

    def test_a_mixed_population_reads_between_the_two(self):
        self.fill(3, applied=True)
        for i in range(3, 6):
            R.record_receipt('u%d' % i, ['a'], [], ['a'], 'not relevant', True, self.db)
        r = R.compute_report(self.db)
        self.assertEqual(r['denominator'], 6)
        self.assertEqual(r['numerator'], 3)
        self.assertAlmostEqual(r['rate'], 50.0)

    def test_a_receipt_whose_applied_memory_section_only_names_stale_or_unverified_lessons_still_counts(self):
        """LL-4: the field mismatch that read the denominator as 0 of 5 for a
        week. A unit whose recalled lessons were all stale or unverified (never
        applied) still HAD a lesson named for it -- applied_memory's own
        "stale" and "unverified" sections -- so it belongs in the denominator
        under record_receipt's own definition (applied OR declined), the exact
        shape brother_run.py's _record_recurrence_and_draft_lessons now sends
        when nothing in a unit's applied memory section was ever applied."""
        R.record_receipt('u1', ['stale-one', 'unverified-one'], [],
                         ['stale-one', 'unverified-one'],
                         'stale-one (stale): STALE: s.md; unverified-one '
                         '(unverified): NO-DATA', True, self.db)
        r = R.compute_report(self.db)
        self.assertEqual(r['denominator'], 1)
        self.assertEqual(r['total_units'], 1)


class ExitCodes(unittest.TestCase):
    def test_report_on_an_empty_store_exits_zero_and_prints_NO_DATA(self):
        db = tempfile.NamedTemporaryFile(suffix='.sqlite3', delete=False).name
        os.unlink(db)
        try:
            self.assertEqual(R.main(['--db', db, 'report']), 0)
        finally:
            if os.path.exists(db):
                os.unlink(db)  # sbe: allow-silent nothing to clean

    def test_a_contract_violation_through_the_CLI_exits_ONE(self):
        db = tempfile.NamedTemporaryFile(suffix='.sqlite3', delete=False).name
        os.unlink(db)
        try:
            code = R.main(['--db', db, 'record', '--unit', 'u1',
                           '--surfaced', 'a', '--applied', 'b',
                           '--before-first-write', 'true'])
            self.assertEqual(code, 1)
        finally:
            if os.path.exists(db):
                os.unlink(db)  # sbe: allow-silent nothing to clean

    def test_a_truthy_looking_string_does_NOT_read_as_true(self):
        """The vocabulary is explicit because 'n' reading as True is a defect
        this estate has already shipped once, in sbe_intake."""
        import argparse
        with self.assertRaises(argparse.ArgumentTypeError):
            R._bool_arg('maybe')

    def test_the_store_resolves_to_the_estate_root_not_a_temp_path(self):
        """A deliverable whose only home is a scratch directory is a deliverable
        this estate has already lost."""
        os.environ.pop('BROTHERMODE_RECURRENCE_DB', None)
        self.assertIn('.brothermode', R.default_db_path())


class TheMetricIsNamedForWhatItMeasures(unittest.TestCase):
    """The label went out as "recurrence rate" and the suite stayed green, because
    nothing pinned the printed name. That is how an overclaim survives a passing
    battery: the number was right and the word on it was wrong, and no test read
    the word. These two do."""

    def _report_output(self):
        db = tempfile.NamedTemporaryFile(suffix='.sqlite3', delete=False).name
        os.unlink(db)
        try:
            for i in range(R.MIN_DENOMINATOR):
                R.main(['--db', db, 'record', '--unit', 'u%d' % i,
                        '--surfaced', 'lesson-a', '--applied', 'lesson-a',
                        '--before-first-write', 'true'])
            buf = io.StringIO()
            with contextlib.redirect_stdout(buf):
                self.assertEqual(R.main(['--db', db, 'report']), 0)
            return buf.getvalue()
        finally:
            if os.path.exists(db):
                os.unlink(db)  # sbe: allow-silent nothing to clean

    def test_the_headline_says_pre_action_application_not_recurrence(self):
        out = self._report_output()
        self.assertIn('pre-action memory application rate:', out)
        self.assertNotIn('recurrence rate:', out)

    def test_the_report_states_that_prevention_is_NOT_measured(self):
        """A reader who quotes this number must be told, in the same output, what it
        cannot support. Prevention needs a later observation and a memory-off control
        arm; neither exists, so the report says NO-DATA rather than staying silent and
        letting the number be read as prevention."""
        out = self._report_output()
        self.assertIn('NO-DATA', out)
        self.assertIn('prevented-recurrence is NOT measured', out)



class TheNumeratorRefusesSelfFiledAndUnwitnessedUnits(unittest.TestCase):
    """V9: applied-before-first-write counts toward the numerator only with an
    INDEPENDENT judge (judge != worker) and a witness of the ordering."""

    def setUp(self):
        import tempfile
        self.db = tempfile.NamedTemporaryFile(suffix='.sqlite3', delete=False).name
        os.unlink(self.db)

    def tearDown(self):
        if os.path.exists(self.db):
            os.unlink(self.db)  # sbe: allow-silent nothing to clean

    def _five_neutral(self):
        for i in range(5):
            R.record_receipt('n%d' % i, ['a'], [], ['a'], 'seen, not relevant', True, self.db)

    def test_an_independently_judged_and_witnessed_unit_counts(self):
        R.record_receipt('good', ['a'], ['a'], [], '', True, self.db,
                         judge='reader-1', worker='worker-1', witness='ts pair')
        self._five_neutral()
        r = R.compute_report(self.db)
        self.assertEqual(r['numerator'], 1)
        self.assertEqual(r['excluded_self_filed'], [])

    def test_a_self_filed_unit_does_not_count_and_is_named(self):
        R.record_receipt('selffiled', ['a'], ['a'], [], '', True, self.db,
                         judge='same', worker='same', witness='ts pair')
        self._five_neutral()
        r = R.compute_report(self.db)
        self.assertEqual(r['numerator'], 0)
        self.assertIn('selffiled', r['excluded_self_filed'])

    def test_a_unit_with_no_judge_does_not_count(self):
        R.record_receipt('nojudge', ['a'], ['a'], [], '', True, self.db,
                         judge='', worker='worker-1', witness='ts pair')
        self._five_neutral()
        r = R.compute_report(self.db)
        self.assertIn('nojudge', r['excluded_self_filed'])

    def test_a_unit_with_no_witness_is_NO_DATA_for_the_numerator(self):
        R.record_receipt('nowitness', ['a'], ['a'], [], '', True, self.db,
                         judge='reader-1', worker='worker-1', witness='')
        self._five_neutral()
        r = R.compute_report(self.db)
        self.assertEqual(r['numerator'], 0)
        self.assertIn('nowitness', r['excluded_no_witness'])

if __name__ == '__main__':
    unittest.main()

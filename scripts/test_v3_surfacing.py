"""Calibration for scripts/v3_surfacing.py, the ordering-witness replay.

Every case here runs on SYNTHETIC records. The real repeat-guard logs live under ~/.claude and
are not part of this repository, so a test that read them would pass on one machine and be
NO-DATA everywhere else. What is proven here is the rule the witness rests on: a lesson counts
only when it surfaced BEFORE the first write to that unit's own files, and a session that never
wrote those files establishes no ordering at all.
"""
import os
import sys
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import v3_surfacing as S  # noqa: E402


class TheOrderingWitnessReplay(unittest.TestCase):
    """scripts/v3_surfacing.py on synthetic records, so these run on any machine: the real
    guard logs live under ~/.claude and are not part of this repository."""

    LESSONS = [('def ', 'a default argument binds at definition time'),
               ('grep -c', 'grep -c exits 1 when it finds nothing')]

    def _records(self, approaches):
        return [{'approach': a} for a in approaches]

    def test_a_lesson_before_the_first_write_is_reported_with_its_index(self):
        recs = self._records(['grep -c x file', 'python3 -c "def f(): pass"',
                              'edit /w/scripts/bundle_runtime.py'])
        first, surfaced = S.replay(recs, ['scripts/bundle_runtime.py'], self.LESSONS)
        self.assertEqual(first, 2)
        self.assertEqual([i for i, _t in surfaced], [0, 1])

    def test_a_lesson_after_the_first_write_is_not_counted(self):
        recs = self._records(['edit /w/scripts/bundle_runtime.py', 'grep -c x file'])
        first, surfaced = S.replay(recs, ['scripts/bundle_runtime.py'], self.LESSONS)
        self.assertEqual(first, 0)
        self.assertEqual(surfaced, [])

    def test_a_session_that_never_wrote_the_unit_files_establishes_no_ordering(self):
        recs = self._records(['grep -c x file', 'edit /w/scripts/other.py'])
        first, surfaced = S.replay(recs, ['scripts/bundle_runtime.py'], self.LESSONS)
        self.assertIsNone(first)
        self.assertEqual(surfaced, [])

    def test_a_missing_session_log_is_NO_DATA_and_never_a_guess(self):
        with self.assertRaises(SystemExit):
            S.load_session('no-such-session', '/no/such/state/dir')

if __name__ == '__main__':
    unittest.main()

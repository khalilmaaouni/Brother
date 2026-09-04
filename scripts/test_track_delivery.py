"""Calibration for scripts/track_delivery.py, in BOTH directions.

A delivery tracker that can only report health is worse than none: it launders a
slipping plan as a healthy one. Every verdict below is driven to its failing side
as well as its passing side, and every assertion reads an EXIT CODE rather than a
printed verdict, because a gate in this repository once printed FAIL and exited 0
with eleven tests passing over it.
"""
import datetime
import json
import os
import subprocess
import sys
import tempfile
import unittest

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SCRIPTS = os.path.join(REPO_ROOT, 'scripts')
SCRIPT = os.path.join(SCRIPTS, 'track_delivery.py')
sys.path.insert(0, SCRIPTS)
import track_delivery as td  # noqa: E402

T = lambda s: datetime.datetime.fromisoformat(s)  # noqa: E731


def row(rid='R1', promised='2026-08-29T18:00:00+09:00', delivered=None,
        started='2026-08-29T12:00:00+09:00', slips=None, blocker=None):
    return {'id': rid, 'promised_at': promised, 'delivered_at': delivered,
            'started_at': started, 'slip_log': slips or [], 'blocker_recorded': blocker}


class Verdicts(unittest.TestCase):
    def test_before_the_promise_is_not_due(self):
        v, _ = td.verdict_for(row(), T('2026-08-29T13:00:00+09:00'))
        self.assertEqual(v, 'NOT-DUE')

    def test_after_the_promise_and_undelivered_is_LATE(self):
        v, _ = td.verdict_for(row(), T('2026-08-29T19:00:00+09:00'))
        self.assertEqual(v, 'LATE')

    def test_delivered_before_the_promise_is_on_time(self):
        v, _ = td.verdict_for(row(delivered='2026-08-29T17:00:00+09:00'),
                              T('2026-08-29T19:00:00+09:00'))
        self.assertEqual(v, 'DELIVERED-ON-TIME')

    def test_delivered_after_the_promise_is_still_recorded_as_LATE(self):
        """Landing eventually does not un-miss the promise. Forgetting that is
        exactly how the same blocker recurs."""
        v, _ = td.verdict_for(row(delivered='2026-08-29T23:00:00+09:00'),
                              T('2026-08-30T01:00:00+09:00'))
        self.assertEqual(v, 'DELIVERED-LATE')

    def test_the_last_quarter_of_the_window_is_AT_RISK(self):
        v, _ = td.verdict_for(row(), T('2026-08-29T17:15:00+09:00'))
        self.assertEqual(v, 'AT-RISK')

    def test_a_row_with_no_promise_is_NO_DATA_not_on_time(self):
        v, _ = td.verdict_for(row(promised=None), T('2026-08-29T19:00:00+09:00'))
        self.assertEqual(v, 'NO-DATA')


class Ladder(unittest.TestCase):
    def test_no_miss_means_no_intervention(self):
        self.assertIsNone(td.intervention_for(0))

    def test_one_miss_records_and_repromises(self):
        self.assertEqual(td.intervention_for(1)[0], 'RECORD')

    def test_two_misses_send_the_ROW_to_fable_not_the_worker(self):
        self.assertEqual(td.intervention_for(2)[0], 'FABLE-REVIEW')

    def test_three_misses_escalate_to_the_founder(self):
        self.assertEqual(td.intervention_for(3)[0], 'ESCALATE-FOUNDER')

    def test_a_fourth_miss_does_not_quietly_drop_back_down(self):
        """The failure this ladder exists to stop is a silent fourth re-promise."""
        self.assertEqual(td.intervention_for(9)[0], 'ESCALATE-FOUNDER')

    def test_a_delivered_late_row_keeps_its_misses(self):
        r = row(slips=[{'status': 'DELIVERED-LATE'}, {'status': 'LATE'}])
        self.assertEqual(td.miss_count(r), 2)

    def test_a_not_due_slip_entry_is_not_a_miss(self):
        r = row(slips=[{'status': 'NOT-DUE'}, {'status': 'AT-RISK'}])
        self.assertEqual(td.miss_count(r), 0)


class SystemicClasses(unittest.TestCase):
    def test_one_class_across_three_rows_is_systemic(self):
        rows = [row('R1', slips=[{'status': 'LATE', 'blocker_class': 'stale-checkout'}]),
                row('R2', slips=[{'status': 'LATE', 'blocker_class': 'stale-checkout'}]),
                row('R3', slips=[{'status': 'LATE', 'blocker_class': 'stale-checkout'}])]
        self.assertEqual(len(td.blocker_classes(rows)['stale-checkout']), 3)

    def test_the_same_row_slipping_three_times_is_NOT_systemic(self):
        """Three misses on ONE row is a row problem. Counting it as systemic
        would manufacture a vault lesson out of a single mis-scoped item."""
        rows = [row('R1', slips=[{'status': 'LATE', 'blocker_class': 'c'},
                                 {'status': 'LATE', 'blocker_class': 'c'},
                                 {'status': 'LATE', 'blocker_class': 'c'}])]
        self.assertEqual(td.blocker_classes(rows)['c'], ['R1'])


class ExitCodes(unittest.TestCase):
    def run_at(self, when, doc=None):
        args = [sys.executable, SCRIPT, '--now', when]
        if doc is None:
            p = subprocess.run(args, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                               universal_newlines=True)
            return p.returncode, p.stdout + p.stderr
        fh = tempfile.NamedTemporaryFile(mode='w', suffix='.json', delete=False)
        json.dump(doc, fh)
        fh.close()
        saved = td.ROADMAP
        try:
            td.ROADMAP = fh.name
            return td.main(['--now', when]), ''
        finally:
            td.ROADMAP = saved
            os.unlink(fh.name)

    def test_nothing_late_exits_zero(self):
        code, out = self.run_at('2026-08-29T13:00:00+09:00')
        self.assertEqual(code, 0, out)

    def test_a_late_row_with_no_blocker_EXITS_ONE(self):
        """The direction that matters. Without it this is a report, not a gate.

        It reads a FIXTURE, not the live board. Until 2026-09-04 it ran the real
        roadmap and asserted exit 1, which held only while some row on that board
        happened to be late with no blocker recorded. Rows recorded their blockers,
        the board went honest, and the unit test went red for the one reason a
        gate must never go red: the estate got better. The live board is not left
        unwatched, it is the delivery-tracking check's own job in check_all.sh;
        this suite's job is the direction, and the fixture is what proves it."""
        doc = {'rows': [row('R1', promised='2026-08-01T00:00:00+09:00')]}
        code, _ = self.run_at('2026-09-05T12:00:00+09:00', doc)
        self.assertEqual(code, 1)

    def test_a_late_row_WITH_its_blocker_recorded_does_not_fail_the_gate(self):
        doc = {'rows': [row('R1', promised='2026-08-01T00:00:00+09:00',
                            blocker='the pinned clones lived in a temp directory')]}
        code, _ = self.run_at('2026-09-05T12:00:00+09:00', doc)
        self.assertEqual(code, 0)

    def test_escalation_three_fails_even_with_a_blocker_recorded(self):
        """A row missed three times is owed to the founder, and a recorded
        blocker does not discharge that."""
        doc = {'rows': [row('R1', promised='2026-08-01T00:00:00+09:00', blocker='b',
                            delivered='2026-08-02T00:00:00+09:00',
                            slips=[{'status': 'LATE'}, {'status': 'LATE'}, {'status': 'LATE'}])]}
        code, _ = self.run_at('2026-09-05T12:00:00+09:00', doc)
        self.assertEqual(code, 1)

    def test_an_unreadable_roadmap_is_NO_DATA_not_a_pass(self):
        saved = td.ROADMAP
        try:
            td.ROADMAP = os.path.join(tempfile.gettempdir(), 'no-such-roadmap-xyz.json')
            self.assertEqual(td.main(['--now', '2026-08-29T13:00:00+09:00']), 2)
        finally:
            td.ROADMAP = saved

    def test_every_row_in_the_real_roadmap_carries_a_promise(self):
        doc = td.load()
        missing = [r['id'] for r in doc['rows'] if not r.get('promised_at')]
        self.assertEqual(missing, [], 'rows with no promised_at: %s' % missing)


if __name__ == '__main__':
    unittest.main()

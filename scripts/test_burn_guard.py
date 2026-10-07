#!/usr/bin/env python3
"""burn_guard's own suite, registered in the battery. Its selftest holds the case table; this wrapper is what
check_all.sh runs, and it also covers the two directions the cap must never fail in."""
import os
import subprocess
import sys
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
TOOL = os.path.join(HERE, "burn_guard.py")
sys.path.insert(0, HERE)
import burn_guard as B  # noqa: E402


class TheSelftestTablePasses(unittest.TestCase):
    def test_it_exits_zero(self):
        r = subprocess.run([sys.executable, "-B", TOOL, "--selftest"], capture_output=True, text=True, timeout=300)
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertIn("OK", r.stdout)


class TheCapAnswersOneQuestionOnly(unittest.TestCase):
    """Simplified by the owner 2026-09-21. Predicting cost per runner failed in both directions in one hour, and
    none of it was load bearing: the dispatcher refuses a call that would pass the grant, so overspend is
    impossible whatever this returns. It answers only whether money remains."""

    def test_money_left_runs_the_planned_width(self):
        self.assertEqual(B.cap(40.0), B.PLANNED_LANES)

    def test_no_money_left_runs_nothing(self):
        self.assertEqual(B.cap(0.0), 0)

    def test_negative_headroom_runs_nothing(self):
        self.assertEqual(B.cap(-5.0), 0)

    def test_the_reserve_keeps_the_last_call_from_dying_mid_flight(self):
        self.assertEqual(B.cap(0.5, reserve=1.0), 0)

    def test_an_unreadable_headroom_is_not_permission_to_spend(self):
        self.assertEqual(B.cap(None), 0)
        self.assertEqual(B.cap(True), 0)

    def test_the_planned_width_is_settable(self):
        self.assertEqual(B.cap(40.0, planned=12), 12)


class TheSpendReadingIsHonest(unittest.TestCase):
    def test_only_reconciled_rows_count_as_spend(self):
        rows = [{"type": "RESERVE", "actual_cost": 9.0}, {"type": "RECONCILE", "actual_cost": 1.0}]
        self.assertAlmostEqual(B.spend(rows), 1.0)

    def test_a_non_numeric_cost_is_skipped_never_guessed(self):
        rows = [{"type": "RECONCILE", "actual_cost": None}, {"type": "RECONCILE", "actual_cost": True},
                {"type": "RECONCILE", "actual_cost": 2.0}]
        self.assertAlmostEqual(B.spend(rows), 2.0)

    def test_a_stop_hour_already_past_means_tomorrow(self):
        import datetime
        self.assertGreater(B.hours_left(datetime.datetime(2026, 9, 21, 7, 30).timestamp(), 7), 23)


class TheHeadroomCountsAbandonedLiability(unittest.TestCase):
    """R2 (independent review of RS1, 2026-09-26): a dead holder's abandoned reservation is real
    liability the ledger already counts against its cap; headroom must too, or it funds lanes past
    money that is already committed."""

    def test_an_abandoned_holds_estimate_is_liability_not_spend(self):
        rows = [{"type": "RESERVE", "reservation_id": "r9", "estimated_cost": 9.5},
                {"type": "ABANDONED", "reservation_id": "r9"}]
        self.assertAlmostEqual(B.abandoned_liability(rows), 9.5)
        self.assertAlmostEqual(B.spend(rows), 0.0)

    def test_a_non_numeric_estimate_is_skipped_never_guessed(self):
        rows = [{"type": "RESERVE", "reservation_id": "z", "estimated_cost": "banana"},
                {"type": "ABANDONED", "reservation_id": "z"}]
        self.assertAlmostEqual(B.abandoned_liability(rows), 0.0)

    def test_headroom_is_the_entry_point_main_calls_and_it_subtracts_both(self):
        rows = [{"type": "RESERVE", "reservation_id": "r9", "estimated_cost": 9.5},
                {"type": "ABANDONED", "reservation_id": "r9"}]
        self.assertEqual(B.cap(B.headroom(rows, 10.0)), 0)
        self.assertGreater(B.cap(10.0 - B.spend(rows)), 0)  # spend alone would wrongly still fund


if __name__ == "__main__":
    unittest.main(verbosity=1)

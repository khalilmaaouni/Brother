"""External test of loop_report.py's R1 fix (independent review of RS1,
~/.claude/evidence/loop-remediation-0926/CODEX-REVIEW-int1-findings.md): ABANDONED closes a hold
for reporting purposes, its estimate is visible as unresolved liability, and it is never counted
as measured spend and never as zero.

Imports loop_report from whichever file is on sys.path and calls its real entry point, money();
this file does not read or pattern-match loop_report.py's source text, so it fails identically
whether the fix is missing, reverted, or renamed away from the tested behaviour.
"""
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import loop_report


class TestAbandonedClosesAHold(unittest.TestCase):
    def test_an_abandoned_hold_is_closed_not_outstanding(self):
        rows = [
            {"type": "RESERVE", "reservation_id": "a1", "estimated_cost": 9.5},
            {"type": "ABANDONED", "reservation_id": "a1"},
        ]
        m = loop_report.money(rows)
        self.assertIsNotNone(m)
        self.assertEqual(m["outstanding"], 0)

    def test_the_abandoned_estimate_is_unresolved_liability_not_measured_spend(self):
        rows = [
            {"type": "RESERVE", "reservation_id": "a1", "estimated_cost": 9.5},
            {"type": "ABANDONED", "reservation_id": "a1"},
        ]
        m = loop_report.money(rows)
        self.assertAlmostEqual(m["actual"], 0.0)
        self.assertAlmostEqual(m.get("abandoned", 0.0), 9.5)

    def test_the_abandoned_estimate_is_never_reported_as_zero(self):
        rows = [
            {"type": "RESERVE", "reservation_id": "a1", "estimated_cost": 9.5},
            {"type": "ABANDONED", "reservation_id": "a1"},
        ]
        m = loop_report.money(rows)
        self.assertNotEqual(m.get("abandoned"), 0)
        self.assertGreater(m.get("abandoned") or 0.0, 0.0)

    def test_a_measured_payment_and_a_different_holders_abandoned_hold_are_both_visible(self):
        rows = [
            {"type": "RESERVE", "reservation_id": "known", "estimated_cost": 0.2},
            {"type": "RECONCILE", "reservation_id": "known", "actual_cost": 0.2},
            {"type": "RESERVE", "reservation_id": "unknown", "estimated_cost": 9.5},
            {"type": "ABANDONED", "reservation_id": "unknown"},
        ]
        m = loop_report.money(rows)
        self.assertAlmostEqual(m["actual"], 0.2)
        self.assertAlmostEqual(m.get("abandoned", 0.0), 9.5)
        self.assertEqual(m["outstanding"], 0)


def _report(ledger_rows, window=(100, 200, "fixture")):
    """report(), the entry point the loop's end note runs, over scratch ledgers: (exit code, text)."""
    import io, json, tempfile
    d = tempfile.mkdtemp(prefix="lr-money-")
    paths = {}
    for name, rows in (("stages", []), ("failures", []), ("ledger", ledger_rows)):
        paths[name] = os.path.join(d, name + ".jsonl")
        with open(paths[name], "w") as fh:
            fh.write("".join(json.dumps(r) + "\n" for r in rows))
    keep = (loop_report.STAGES, loop_report.FAILURES, loop_report.LEDGER)
    loop_report.STAGES, loop_report.FAILURES, loop_report.LEDGER = paths["stages"], paths["failures"], paths["ledger"]
    try:
        buf = io.StringIO()
        code = loop_report.report(plan_path=os.path.join(d, "no-plan.json"), window=window, out=buf)
        return code, buf.getvalue()
    finally:
        loop_report.STAGES, loop_report.FAILURES, loop_report.LEDGER = keep


class TestAnUnknownCostIsNeverZero(unittest.TestCase):
    """Finding 6, 2026-09-27: float(actual_cost or 0) turned a null or missing cost into a measured zero, and the
    report exited clean with neither a warning nor an open hold."""

    def test_a_null_cost_is_counted_unreadable_warned_and_the_report_is_not_clean(self):
        rows = [{"type": "RESERVE", "reservation_id": "r", "at": 110, "estimated_cost": 9},
                {"type": "RECONCILE", "reservation_id": "r", "at": 120, "actual_cost": None}]
        self.assertEqual(loop_report.money(rows)["unreadable"], 1)
        code, text = _report(rows)
        self.assertIn("WARNING: 1 reconciled row(s) carry an unreadable cost", text)
        self.assertNotEqual(code, 0, text)

    def test_a_missing_cost_is_counted_unreadable(self):
        rows = [{"type": "RECONCILE", "reservation_id": "r", "at": 120}]
        self.assertEqual(loop_report.money(rows)["unreadable"], 1)

    def test_a_negative_cost_is_counted_unreadable_never_subtracted(self):
        m = loop_report.money([{"type": "RECONCILE", "reservation_id": "a", "actual_cost": 3.0},
                               {"type": "RECONCILE", "reservation_id": "b", "actual_cost": -5.0}])
        self.assertEqual((m["actual"], m["unreadable"]), (3.0, 1))

    def test_an_abandoned_hold_with_no_readable_estimate_is_warned_and_the_report_is_not_clean(self):
        code, text = _report([{"type": "ABANDONED", "reservation_id": "never-reserved", "at": 120}])
        self.assertIn("WARNING: 1 abandoned hold(s) carry no readable estimate", text)
        self.assertNotEqual(code, 0, text)


class TestTheWindowNeverErasesAnEarlierLiability(unittest.TestCase):
    """Finding 5, 2026-09-27: RESERVE rows before the window were filtered out before the reservation state was
    rebuilt, so an earlier open hold vanished and an in-window ABANDONED lost its estimate. One fixture per guard."""

    def test_a_hold_opened_before_the_window_and_still_open_is_reported(self):
        code, text = _report([{"type": "RESERVE", "reservation_id": "open", "at": 99, "estimated_cost": 8}])
        self.assertIn("1 hold(s) still open", text)

    def test_an_abandoned_hold_reserved_before_the_window_keeps_its_estimate(self):
        code, text = _report([{"type": "RESERVE", "reservation_id": "gone", "at": 99, "estimated_cost": 9.5},
                              {"type": "ABANDONED", "reservation_id": "gone", "at": 120}])
        self.assertIn("UNRESOLVED: 1 hold(s) closed as abandoned (a dead holder, F14b), 9.50 USD", text)
        self.assertIn("0 hold(s) still open", text)

    def test_a_hold_abandoned_before_the_window_is_neither_open_nor_this_windows_liability(self):
        code, text = _report([{"type": "RESERVE", "reservation_id": "old", "at": 40, "estimated_cost": 7},
                              {"type": "ABANDONED", "reservation_id": "old", "at": 50}])
        self.assertIn("0 hold(s) still open", text)
        self.assertNotIn("UNRESOLVED", text)

    def test_spend_outside_the_window_is_not_counted(self):
        code, text = _report([{"type": "RESERVE", "reservation_id": "early", "at": 50, "estimated_cost": 1},
                              {"type": "RECONCILE", "reservation_id": "early", "at": 60, "actual_cost": 4.0}])
        self.assertIn("spend: 0.00 USD over 0 reconciled call(s), 0 hold(s) still open", text)
        self.assertEqual(code, 0, text)


if __name__ == "__main__":
    unittest.main()

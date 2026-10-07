#!/usr/bin/env python3
"""P1.d: the report, the board row and the coverage gate.

RED WITHOUT THE CODE IT TESTS. render(), board_row() and gate() are added by this sub unit,
and every test below names one of them, so deleting them makes this file fail before it can
even build a fixture. The thin fixture test is the one the registered mutation
M-P1D-QUOTE-UNDER-FLOOR must kill: it asserts the words NO-DATA and the ABSENCE of a
brier_skill= line for a predictor with three resolved rows, which is exactly what a report
that quotes a percentage under the resolved floor would break.
"""
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import prediction_ledger as ledger


#: Three resolved rows, well under the 30 row floor. brier_skill is present and non-zero on
#: purpose: a report that only looked at whether a skill number exists would print it, and this
#: fixture is built to catch exactly that.
THIN_STATS = {
    "n": 5, "resolved": 3, "right": 2, "unresolved": 1, "pending": 1,
    "brier_sum": 0.5, "brier_n": 3, "buckets": {},
    "OPEN": 1, "RESOLVED": 3, "EXPIRED": 0, "CANCELLED": 0,
    "SUPERSEDED": 0, "INVALID": 0,
    "accuracy": 2.0 / 3.0, "brier": 0.1666, "coverage": 0.75,
    "base_rate": 2.0 / 3.0, "brier_skill": 0.3,
}

#: Thirty five resolved rows, over the floor, so the same report must print the number.
HEALTHY_STATS = {
    "n": 40, "resolved": 35, "right": 30, "unresolved": 2, "pending": 3,
    "brier_sum": 5.0, "brier_n": 35, "buckets": {},
    "OPEN": 3, "RESOLVED": 35, "EXPIRED": 0, "CANCELLED": 0,
    "SUPERSEDED": 0, "INVALID": 0,
    "accuracy": 30.0 / 35.0, "brier": 0.1428, "coverage": 0.9459,
    "base_rate": 30.0 / 35.0, "brier_skill": 0.1234,
}


def thin():
    return {"grade": dict(THIN_STATS)}


def healthy():
    return {"grade": dict(HEALTHY_STATS)}


class RenderRefusesToQuoteUnderTheFloor(unittest.TestCase):
    """M-P1D-QUOTE-UNDER-FLOOR kills test_thin_predictor_reads_no_data_not_a_percentage."""

    def test_thin_predictor_reads_no_data_not_a_percentage(self):
        text = ledger.render(thin())
        self.assertIn("NO-DATA", text)
        self.assertNotIn("brier_skill=", text)
        self.assertNotIn("%", text)

    def test_render_prints_brier_skill_when_the_sample_clears_the_floor(self):
        text = ledger.render(healthy())
        self.assertIn("brier_skill=0.1234", text)
        self.assertNotIn("NO-DATA", text)


class RenderShowsTheLifecycle(unittest.TestCase):
    """M-P1D-LIFECYCLE-COUNTS kills
    test_render_prints_the_four_non_resolved_counts_beside_resolved."""

    def test_render_prints_the_four_non_resolved_counts_beside_resolved(self):
        stats = dict(THIN_STATS)
        stats["EXPIRED"] = 2
        stats["CANCELLED"] = 1
        stats["SUPERSEDED"] = 4
        stats["INVALID"] = 3
        text = ledger.render({"grade": stats})
        self.assertIn("resolved=3", text)
        self.assertIn("EXPIRED=2", text)
        self.assertIn("CANCELLED=1", text)
        self.assertIn("SUPERSEDED=4", text)
        self.assertIn("INVALID=3", text)


class BoardRowShape(unittest.TestCase):
    """M-P1D-BOARD-VERDICT-ALWAYS-OK kills
    test_board_row_gives_predictor_n_resolved_coverage_and_verdict."""

    def test_board_row_gives_predictor_n_resolved_coverage_and_verdict(self):
        rows = ledger.board_row(thin())
        self.assertEqual(sorted(rows), ["grade"])
        row = rows["grade"]
        self.assertEqual(sorted(row), ["coverage", "n", "predictor", "resolved", "verdict"])
        self.assertEqual(row["predictor"], "grade")
        self.assertEqual(row["n"], 5)
        self.assertEqual(row["resolved"], 3)
        self.assertEqual(row["coverage"], 0.75)
        self.assertEqual(row["verdict"], "NO-DATA")

    def test_board_row_clears_a_predictor_that_is_above_the_floor(self):
        row = ledger.board_row(healthy())["grade"]
        self.assertEqual(row["resolved"], 35)
        self.assertEqual(row["coverage"], 0.9459)
        self.assertEqual(row["verdict"], "OK")


class GateVerdicts(unittest.TestCase):
    """M-P1D-GATE-REFUSES-ON-ACCURACY kills
    test_gate_refuses_on_coverage_never_on_accuracy. M-P1D-GATE-EMPTY-IS-ZERO kills
    test_gate_returns_two_on_an_empty_summary."""

    def test_gate_returns_zero_when_every_predictor_clears_the_floor(self):
        self.assertEqual(ledger.gate({"a": {"n": 10, "resolved": 9, "coverage": 0.9}}), 0)

    def test_gate_returns_one_when_a_predictor_is_below_the_floor(self):
        self.assertEqual(ledger.gate({"a": {"n": 100, "resolved": 5, "coverage": 0.05}}), 1)

    def test_gate_returns_two_on_no_data(self):
        self.assertEqual(ledger.gate({"a": {"n": 10, "resolved": 0, "coverage": None}}), 2)

    def test_gate_returns_two_on_an_empty_summary(self):
        self.assertEqual(ledger.gate({}), 2)

    def test_gate_refuses_on_coverage_never_on_accuracy(self):
        summary = {"a": {"n": 1000, "resolved": 2, "coverage": 0.002, "accuracy": 1.0}}
        self.assertEqual(ledger.gate(summary), 1)

    def test_gate_reads_a_nan_coverage_as_no_data_not_a_crash(self):
        summary = {"a": {"n": 1, "resolved": 1, "coverage": float("nan")}}
        self.assertEqual(ledger.gate(summary), 2)


class HostileInput(unittest.TestCase):
    """Every public function below is fed a wrong type, None, a bool or NaN. Each one must be
    REFUSED with this module's own ValueError, never a raw TypeError and never accepted."""

    def test_render_refuses_a_summary_that_is_not_a_mapping(self):
        for bad in (None, [], "grade", 3, True, 1.5):
            with self.assertRaises(ValueError):
                ledger.render(bad)

    def test_render_refuses_a_predictor_entry_that_is_not_a_mapping(self):
        with self.assertRaises(ValueError):
            ledger.render({"grade": "not a mapping"})
        with self.assertRaises(ValueError):
            ledger.render({"grade": None})

    def test_render_refuses_a_count_of_the_wrong_type(self):
        stats = dict(THIN_STATS)
        stats["resolved"] = "three"
        with self.assertRaises(ValueError):
            ledger.render({"grade": stats})
        stats = dict(THIN_STATS)
        stats["n"] = True
        with self.assertRaises(ValueError):
            ledger.render({"grade": stats})

    def test_render_refuses_a_nan_coverage(self):
        stats = dict(THIN_STATS)
        stats["coverage"] = float("nan")
        with self.assertRaises(ValueError):
            ledger.render({"grade": stats})

    def test_render_refuses_a_bool_nan_text_or_negative_min_resolved(self):
        for bad in (True, float("nan"), "30", -1, None):
            with self.assertRaises(ValueError):
                ledger.render(thin(), bad)

    def test_board_row_refuses_a_summary_that_is_not_a_mapping(self):
        for bad in (None, [], "grade", 3):
            with self.assertRaises(ValueError):
                ledger.board_row(bad)

    def test_gate_refuses_a_summary_that_is_not_a_mapping(self):
        for bad in (None, [], "grade", 3):
            with self.assertRaises(ValueError):
                ledger.gate(bad)

    def test_gate_refuses_a_nan_bool_text_none_or_out_of_range_floor(self):
        summary = {"a": {"n": 1, "resolved": 1, "coverage": 1.0}}
        for bad in (True, float("nan"), "0.2", None, -0.5, 1.5):
            with self.assertRaises(ValueError):
                ledger.gate(summary, bad)


if __name__ == "__main__":
    unittest.main()

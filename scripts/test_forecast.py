"""What the forecast must keep honest.

A forecast is the easiest thing in this estate to make flattering, because
nobody checks an estimate until it is already wrong. The tests here are aimed at
the specific ways this one could lie: a single number instead of a range, a base
rate taken from the wrong sample, coupled work counted separately, and the same
hours billed twice.
"""
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import forecast as F  # noqa: E402


class ItRefusesTheFantasyRate(unittest.TestCase):
    """Three capabilities landed six minutes apart tonight. Dividing by that
    gives forty minutes to parity, and that number is the reason this file
    exists rather than a sentence."""

    def test_the_base_rate_is_hours_not_minutes(self):
        self.assertGreater(F.BASE_HOURS_PER_MODULE, 0.5)

    def test_the_base_rate_names_its_sample_size(self):
        self.assertGreaterEqual(F.BASE_SAMPLE, 5)

    def test_an_optimism_correction_is_applied_and_is_above_one(self):
        self.assertGreater(F.OPTIMISM_LOW, 1.0)
        self.assertGreater(F.OPTIMISM_HIGH, F.OPTIMISM_LOW)

    def test_the_low_estimate_is_never_the_raw_base_rate(self):
        """The unadjusted rate is the inside view, and the inside view is
        systematically optimistic."""
        lo, _hi, _rows = F.hours()
        raw = sum(F.BASE_HOURS_PER_MODULE * p["difficulty"] for p in F.PIECES)
        self.assertGreater(lo, raw)


class ItIsAlwaysARange(unittest.TestCase):
    def test_high_is_meaningfully_above_low(self):
        lo, hi, _ = F.hours()
        self.assertGreater(hi, lo * 1.5)

    def test_every_piece_carries_its_own_range(self):
        for row in F.hours()[2]:
            self.assertLess(row["low"], row["high"])

    def test_difficulty_actually_moves_the_estimate(self):
        """A multiplier nobody consulted is decoration."""
        easy = [{"name": "x", "difficulty": 1.0, "why": "", "unlocks": []}]
        hard = [{"name": "x", "difficulty": 5.0, "why": "", "unlocks": []}]
        self.assertLess(F.hours(pieces=easy)[1], F.hours(pieces=hard)[1])


class CoupledWorkIsNotCountedSeparately(unittest.TestCase):
    """The finding that changes the answer most: five of the nine cells below
    the gate wait on one piece of work."""

    def test_at_least_one_piece_unlocks_several_capabilities(self):
        self.assertTrue(any(len(p["unlocks"]) >= 3 for p in F.PIECES))

    def test_every_piece_says_what_it_unlocks(self):
        for p in F.PIECES:
            self.assertTrue(p["unlocks"], p["name"])

    def test_every_piece_says_why_it_is_needed(self):
        for p in F.PIECES:
            self.assertTrue(str(p.get("why", "")).strip(), p["name"])

    def test_the_pieces_are_fewer_than_the_cells_they_unlock(self):
        unlocked = {c for p in F.PIECES for c in p["unlocks"]}
        self.assertLess(len(F.PIECES), len(unlocked))


class LandedPiecesStopBilling(unittest.TestCase):
    """The 2026-09-01 recalibration. The table kept billing pieces the parity
    evidence said were built (the canonical-Work finding), so landed status is
    now derived from the levels map, and these pin the derivation: all cells
    at L3+ lands the piece at zero hours, one cell below keeps it billed, no
    levels means the old behavior, and a missing level is never a pass."""

    PIECE = [{"name": "x", "difficulty": 5.0, "why": "w",
              "unlocks": ["A", "B"]}]

    def test_a_piece_whose_cells_all_read_l3_is_landed_at_zero_hours(self):
        lo, hi, rows = F.hours(pieces=self.PIECE, levels={"A": 3, "B": 4})
        self.assertEqual((lo, hi), (0.0, 0.0))
        self.assertTrue(rows[0]["landed"])

    def test_one_cell_below_l3_keeps_the_piece_billed(self):
        lo, _hi, rows = F.hours(pieces=self.PIECE, levels={"A": 3, "B": 2})
        self.assertGreater(lo, 0)
        self.assertFalse(rows[0]["landed"])

    def test_no_levels_means_no_landing(self):
        lo, _hi, rows = F.hours()
        self.assertGreater(lo, 0)
        self.assertFalse(any(r["landed"] for r in rows))

    def test_a_missing_level_is_not_a_pass(self):
        _lo, _hi, rows = F.hours(pieces=self.PIECE, levels={"B": 4})
        self.assertFalse(rows[0]["landed"])


class ItRunsAgainstTheRealBoard(unittest.TestCase):
    def test_it_produces_a_forecast_and_exits_zero(self):
        self.assertEqual(F.main(["--hours-per-day", "6"]), 0)

    def test_the_hours_per_day_actually_changes_the_days(self):
        """Otherwise the calendar figure is decoration too."""
        import io
        import contextlib
        outs = []
        for h in ("3", "12"):
            buf = io.StringIO()
            with contextlib.redirect_stdout(buf):
                F.main(["--hours-per-day", h])
            outs.append(buf.getvalue())
        self.assertNotEqual(outs[0], outs[1])

    def test_it_states_what_would_make_it_wrong(self):
        import io
        import contextlib
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            F.main([])
        text = buf.getvalue()
        self.assertIn("WOULD MAKE THIS WRONG", text)
        self.assertIn(F.NODATA, text)

    def test_it_says_the_board_number_deserves_more_suspicion(self):
        import io
        import contextlib
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            F.main([])
        self.assertIn("MORE SUSPICION", buf.getvalue())

    def test_it_says_finishing_the_board_is_not_the_goal(self):
        """A finished board with the delivery number at zero is a completed
        plan and a failed product, and the forecast must not let that be read
        as success."""
        import io
        import contextlib
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            F.main([])
        self.assertIn("failed product", buf.getvalue())


if __name__ == "__main__":
    unittest.main()

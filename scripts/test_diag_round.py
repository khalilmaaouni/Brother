#!/usr/bin/env python3
"""diag_round's own suite: the command shapes it builds and how it reads a round's tally. Dispatches nothing."""
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import diag_round as D  # noqa: E402


class TheTimeoutFloorHolds(unittest.TestCase):
    """A timeout under 300 seconds cuts stragglers that were about to answer. Standing estate rule."""

    def test_a_dry_round_passes_dry_to_the_brief_tool(self):
        """A dry round must not mark a real run as already asked, so the brief tool must be told it is dry."""
        self.assertIn("--dry", D.brief_cmd("/o", ["A.1"], dry=True))
        self.assertNotIn("--dry", D.brief_cmd("/o", ["A.1"]))

    def test_a_short_timeout_is_raised_to_the_floor(self):
        self.assertIn("300", D.fanout_cmd("j", "r", 90))

    def test_a_long_timeout_is_kept(self):
        self.assertIn("900", D.fanout_cmd("j", "r", 900))

    def test_retries_are_capped_at_one(self):
        argv = D.fanout_cmd("j", "r", 600)
        self.assertEqual(argv[argv.index("--retries") + 1], "1")


class TheTallyIsReadHonestly(unittest.TestCase):
    """An unreadable tally must never look like a good round."""

    def test_a_real_tally_line_is_read(self):
        c = D.summarise("PROVEN 6 | UNPROVEN 8 | REFUSED 2 | NO-DATA 1")
        self.assertEqual((c["PROVEN"], c["UNPROVEN"], c["REFUSED"], c["NO-DATA"]), (6, 8, 2, 1))

    def test_no_tally_reads_as_nothing_proven(self):
        self.assertEqual(D.summarise("some other output")["PROVEN"], 0)

    def test_empty_output_reads_as_nothing_proven(self):
        self.assertEqual(D.summarise("")["PROVEN"], 0)

    def test_none_output_reads_as_nothing_proven(self):
        self.assertEqual(D.summarise(None)["PROVEN"], 0)

    def test_a_per_lane_proven_line_is_not_a_tally(self):
        """diag_apply's real per lane line carries a pipe too, so only the leading word separates them."""
        line = "PROVEN   D9.d     the module already exists | note written for the next brief"
        self.assertEqual(D.summarise(line)["PROVEN"], 0)


class TheCommandsPointAtTheRealTools(unittest.TestCase):
    def test_the_brief_command_names_the_out_dir_first(self):
        self.assertEqual(D.brief_cmd("/o", ["A", "B"])[-3:], ["/o", "A", "B"])

    def test_the_brief_command_with_no_subs_diagnoses_everything_stuck(self):
        self.assertTrue(D.brief_cmd("/o", [])[-1].endswith("/o"))

    def test_the_apply_command_names_the_out_dir(self):
        self.assertEqual(D.apply_cmd("/o")[-1], "/o")

    def test_the_apply_command_runs_the_proof_executor(self):
        self.assertTrue(D.apply_cmd("/o")[-2].endswith("diag_apply.py"))


if __name__ == "__main__":
    unittest.main(verbosity=1)

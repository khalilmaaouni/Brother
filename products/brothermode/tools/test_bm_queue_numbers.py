"""What the five queue numbers must keep true.

These five are the adopting team's own agreed measure of success, and their
delivery lead said no gate verdict replaces them. Everything else this estate
computes is a proxy; these are the thing itself. So the tests that matter are
the ones that stop a number being printed when it was not actually counted.
"""
import csv
import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import bm_queue_numbers as Q  # noqa: E402
import bm_private_scan as PS  # noqa: E402

HERE = os.path.dirname(os.path.abspath(__file__))
SYNTH = os.path.join(HERE, "fixtures", "queue-export-synthetic.csv")
NOSTATUS = os.path.join(HERE, "fixtures", "queue-export-no-status-column.csv")
# The machine-level list this file must never spell a term out of; read at
# test time, never copied in here (2026-08-29 failure: the scanner must not
# contain what it forbids). A module constant, not the bare
# PS.DEFAULT_TERMS_PATH call, so a one-off run can point it at a path that
# does not exist and prove the NO-DATA skip fires.
TERMS_PATH = PS.DEFAULT_TERMS_PATH


def write_csv(rows, fieldnames):
    fh = tempfile.NamedTemporaryFile("w", suffix=".csv", delete=False,
                                     encoding="utf-8", newline="")
    w = csv.DictWriter(fh, fieldnames=fieldnames)
    w.writeheader()
    w.writerows(rows)
    fh.close()
    return fh.name


class TheHandCountedFiveAreReproduced(unittest.TestCase):
    """The done-check named in the plan, first branch: reproduce 41, 22, 23, 11,
    48. Against a synthetic export, because the team's own tracker content is
    theirs and does not belong in this repository."""

    def test_all_five_reproduce(self):
        rows, names, problem = Q.read_rows(SYNTH)
        self.assertEqual(problem, "")
        results, _cols = Q.compute(rows, names)
        self.assertEqual([r["value"] for r in results], [41, 22, 23, 11, 48])

    def test_the_command_exits_zero_against_the_expectation(self):
        self.assertEqual(Q.main([SYNTH, "--expect", "41,22,23,11,48"]), 0)

    def test_a_wrong_expectation_fails_rather_than_passing_quietly(self):
        self.assertEqual(Q.main([SYNTH, "--expect", "41,22,23,99,48"]), 1)

    def test_the_tbd_count_is_a_second_dimension_not_a_relabelled_status(self):
        """48 spans the statuses. If it were a status count in disguise it
        would equal one of the four."""
        rows, names, _ = Q.read_rows(SYNTH)
        vals = [r["value"] for r in Q.compute(rows, names)[0]]
        self.assertNotIn(vals[4], vals[:4])


class AMissingColumnIsNeverZero(unittest.TestCase):
    """Zero waiting on development is a triumph. 'I could not find the status
    column' is a broken export. They must never print the same way."""

    def test_a_renamed_status_column_gives_NO_DATA_for_the_four(self):
        rows, names, _ = Q.read_rows(NOSTATUS)
        results, _cols = Q.compute(rows, names)
        self.assertEqual([r["value"] for r in results[:4]], [None] * 4)

    def test_the_fifth_still_computes_from_the_column_that_IS_there(self):
        rows, names, _ = Q.read_rows(NOSTATUS)
        self.assertEqual(Q.compute(rows, names)[0][4]["value"], 48)

    def test_the_note_names_the_accepted_spellings(self):
        rows, names, _ = Q.read_rows(NOSTATUS)
        note = Q.compute(rows, names)[0][0]["note"]
        self.assertIn(Q.NODATA, note)
        self.assertIn("status", note)

    def test_the_second_branch_of_the_done_check_exits_NO_DATA(self):
        """The plan accepts this branch explicitly: name which of the five it
        cannot supply."""
        self.assertEqual(Q.main([NOSTATUS]), 2)

    def test_no_export_at_all_is_NO_DATA_not_a_row_of_zeros(self):
        self.assertEqual(Q.main([]), 2)

    def test_a_missing_file_is_NO_DATA(self):
        self.assertEqual(Q.main(["/no/such/export.csv"]), 2)


class ItNeverGuessesAColumn(unittest.TestCase):
    """A tool that settles for the closest looking header eventually counts the
    wrong field and reports it confidently."""

    def test_an_accepted_spelling_is_found(self):
        for header in ("Status", "State", "Current Status", "stage"):
            self.assertEqual(Q.find_column([header, "Key"], "status"), header)

    def test_a_near_miss_is_a_miss(self):
        self.assertIsNone(Q.find_column(["Status Category", "Key"], "status"))
        self.assertIsNone(Q.find_column(["Workflow Position"], "status"))

    def test_no_columns_at_all_is_None(self):
        self.assertIsNone(Q.find_column([], "status"))

    def test_case_and_punctuation_do_not_defeat_it(self):
        self.assertEqual(Q.find_column(["  END-DATE  "], "end_date"), "  END-DATE  ")


class ItReadsWhatATrackerActuallyExports(unittest.TestCase):
    def test_json_lists_are_accepted_as_well_as_csv(self):
        fh = tempfile.NamedTemporaryFile("w", suffix=".json", delete=False,
                                         encoding="utf-8")
        fh.write('[{"Status": "Waiting on Development", "End Date": "TBD"}]')
        fh.close()
        try:
            rows, names, problem = Q.read_rows(fh.name)
            self.assertEqual(problem, "")
            self.assertEqual(Q.compute(rows, names)[0][0]["value"], 1)
        finally:
            os.unlink(fh.name)

    def test_matching_ignores_case_and_surrounding_space(self):
        p = write_csv([{"Status": "  waiting on DEVELOPMENT ", "End Date": "x"}],
                      ["Status", "End Date"])
        try:
            rows, names, _ = Q.read_rows(p)
            self.assertEqual(Q.compute(rows, names)[0][0]["value"], 1)
        finally:
            os.unlink(p)

    def test_an_empty_export_counts_zero_rather_than_NO_DATA(self):
        """An export with headers and no rows genuinely IS zero, and must not be
        confused with an export whose column is missing."""
        p = write_csv([], ["Status", "End Date"])
        try:
            rows, names, _ = Q.read_rows(p)
            self.assertEqual([r["value"] for r in Q.compute(rows, names)[0]],
                             [0, 0, 0, 0, 0])
        finally:
            os.unlink(p)


class TheFixturesCarryNothingPrivate(unittest.TestCase):
    """The team's tracker content is theirs. These fixtures are synthetic, use
    role words, and must stay that way. The terms scanned for live OUTSIDE
    this repository at TERMS_PATH; reading and matching them reuses
    bm_private_scan.py's own loader and matcher so this file never grows a
    second regex builder (the same reuse rule bm_vault_intake.py's
    deny_list_hit follows). NO-DATA, never a silent pass, when that list is
    absent from this machine."""

    def test_the_fixtures_use_role_words_only(self):
        terms, no_data_reason = PS._load_terms(TERMS_PATH)
        if terms is None:
            self.skipTest("%s: %s" % (Q.NODATA, no_data_reason))
        short_patterns, long_patterns = PS._build_patterns(terms)
        for path in (SYNTH, NOSTATUS):
            with open(path, encoding="utf-8") as fh:
                data = fh.read().encode("utf-8")
            hits = PS._scan_bytes(data, short_patterns, long_patterns)
            # Masked on purpose: a failure message is output too, and this
            # file must never print a term it is refusing.
            masked = ["a term of %d characters" % len(term) for term, _pass in hits]
            self.assertEqual([], masked, path)

    def test_the_fixtures_exist_and_are_readable(self):
        for path in (SYNTH, NOSTATUS):
            self.assertTrue(os.path.isfile(path), path)


if __name__ == "__main__":
    unittest.main()

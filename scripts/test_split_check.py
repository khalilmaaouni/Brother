"""Calibration for scripts/split_check.py.

Every case asserts the EXIT CODE, never only the printed verdict. That is
this estate's most expensive recent lesson (leaf_pin_check's own test file
carries the same note): a gate that prints FAIL but exits 0 manufactures
evidence of a pass for every wrapper and && chain above it. All fixtures are
synthetic, written to a fresh temp directory per test and torn down after.
"""
import contextlib
import io
import pathlib
import tempfile
import unittest

import split_check as sc


def run_main(*args):
    """Return (exit_code, stdout) for split_check.main(args)."""
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        code = sc.main(list(args))
    return code, buf.getvalue()


class FixtureCase(unittest.TestCase):
    """Base class: a fresh temp dir per test, a helper to drop a CSV in it."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.dir = pathlib.Path(self._tmp.name)

    def write(self, name, text):
        path = self.dir / name
        path.write_text(text)
        return str(path)


class Verdicts(FixtureCase):

    def test_clean_pair_passes(self):
        train = self.write("train.csv", "id,x\n1,a\n2,b\n")
        test = self.write("test.csv", "id,x\n3,c\n4,d\n")
        code, out = run_main("--train", train, "--test", test, "--key", "id")
        self.assertEqual(code, 0, out)
        self.assertIn("PASSED", out)

    def test_overlap_fails_naming_the_first_key(self):
        """Two customers overlap; the FIRST one in test file order is named,
        the second is not, so a re-run after fixing only the first key would
        still find a real remaining problem."""
        train = self.write("train.csv", "id,x\n1,a\n2,b\n")
        test = self.write("test.csv", "id,x\n2,c\n1,d\n")
        code, out = run_main("--train", train, "--test", test, "--key", "id")
        self.assertEqual(code, 1, out)
        self.assertIn("FAIL", out)
        self.assertIn("'2'", out)
        self.assertNotIn("'1'", out)

    def test_duplicate_key_inside_train_alone_is_not_an_overlap(self):
        """Two train rows sharing a key is a data-quality question for
        something else, never a train/test overlap on its own."""
        train = self.write("train.csv", "id,x\n1,a\n1,b\n")
        test = self.write("test.csv", "id,x\n2,c\n")
        code, out = run_main("--train", train, "--test", test, "--key", "id")
        self.assertEqual(code, 0, out)
        self.assertIn("PASSED", out)

    def test_row_past_cutoff_fails_naming_the_line(self):
        train = self.write(
            "train.csv", "id,d\n1,2026-01-01\n2,2026-06-01\n")
        test = self.write("test.csv", "id,d\n3,2026-07-01\n")
        code, out = run_main("--train", train, "--test", test, "--key", "id",
                              "--time-col", "d", "--cutoff", "2026-03-01")
        self.assertEqual(code, 1, out)
        self.assertIn("FAIL", out)
        self.assertIn("line 3", out)
        self.assertIn("2026-06-01", out)

    def test_cutoff_checks_train_not_the_holdout_test_side(self):
        """Test rows dated after the cutoff are the ordinary shape of a
        temporal holdout, not a leak; only train is scanned."""
        train = self.write(
            "train.csv", "id,d\n1,2026-01-01\n2,2026-02-01\n")
        test = self.write("test.csv", "id,d\n3,2026-09-01\n")
        code, out = run_main("--train", train, "--test", test, "--key", "id",
                              "--time-col", "d", "--cutoff", "2026-03-01")
        self.assertEqual(code, 0, out)
        self.assertIn("PASSED", out)

    def test_overlap_checked_before_cutoff(self):
        """Both defects present at once: the overlap is reported, since it
        is checked first, and its message never mentions the cutoff."""
        train = self.write(
            "train.csv", "id,d\n1,2026-01-01\n2,2026-06-01\n")
        test = self.write("test.csv", "id,d\n1,2026-07-01\n")
        code, out = run_main("--train", train, "--test", test, "--key", "id",
                              "--time-col", "d", "--cutoff", "2026-03-01")
        self.assertEqual(code, 1, out)
        self.assertIn("'1' appears in both", out)


class Boundaries(FixtureCase):

    def test_empty_file_is_no_data_not_a_pass(self):
        train = self.write("train.csv", "")
        test = self.write("test.csv", "id,x\n1,a\n")
        code, out = run_main("--train", train, "--test", test, "--key", "id")
        self.assertNotEqual(code, 0, "an unreadable file passed by vacancy")
        self.assertEqual(code, 2, out)
        self.assertIn("NO-DATA", out)
        self.assertIn("empty", out)

    def test_header_only_file_passes_with_zero_rows(self):
        """A header with no data rows is a legitimately empty split side,
        never a NO-DATA: the column it needs to check is right there."""
        train = self.write("train.csv", "id,x\n")
        test = self.write("test.csv", "id,x\n1,a\n")
        code, out = run_main("--train", train, "--test", test, "--key", "id")
        self.assertEqual(code, 0, out)
        self.assertIn("0 train row", out)

    def test_one_data_row_each_side_passes(self):
        train = self.write("train.csv", "id,x\n1,a\n")
        test = self.write("test.csv", "id,x\n2,b\n")
        code, out = run_main("--train", train, "--test", test, "--key", "id")
        self.assertEqual(code, 0, out)

    def test_missing_key_column_is_no_data_naming_the_column(self):
        train = self.write("train.csv", "id,x\n1,a\n")
        test = self.write("test.csv", "id,x\n2,b\n")
        code, out = run_main("--train", train, "--test", test,
                              "--key", "customer_id")
        self.assertNotEqual(code, 0, "an absent key column passed by vacancy")
        self.assertEqual(code, 2, out)
        self.assertIn("NO-DATA", out)
        self.assertIn("customer_id", out)

    def test_missing_time_col_is_no_data_naming_the_column(self):
        train = self.write("train.csv", "id,x\n1,a\n")
        test = self.write("test.csv", "id,x\n2,b\n")
        code, out = run_main("--train", train, "--test", test, "--key", "id",
                              "--time-col", "date", "--cutoff", "2026-01-01")
        self.assertEqual(code, 2, out)
        self.assertIn("NO-DATA", out)
        self.assertIn("date", out)

    def test_cutoff_with_no_time_col_is_no_data(self):
        train = self.write("train.csv", "id,x\n1,a\n")
        test = self.write("test.csv", "id,x\n2,b\n")
        code, out = run_main("--train", train, "--test", test, "--key", "id",
                              "--cutoff", "2026-01-01")
        self.assertEqual(code, 2, out)
        self.assertIn("NO-DATA", out)
        self.assertIn("--time-col", out)

    def test_malformed_csv_row_is_no_data_naming_the_line(self):
        train = self.write("train.csv", "id,x\n1,a,extra\n")
        test = self.write("test.csv", "id,x\n2,b\n")
        code, out = run_main("--train", train, "--test", test, "--key", "id")
        self.assertNotEqual(code, 0, "a malformed row passed by vacancy")
        self.assertEqual(code, 2, out)
        self.assertIn("NO-DATA", out)
        self.assertIn("line 2", out)

    def test_missing_file_is_no_data(self):
        test = self.write("test.csv", "id,x\n2,b\n")
        code, out = run_main("--train", str(self.dir / "nope.csv"),
                              "--test", test, "--key", "id")
        self.assertEqual(code, 2, out)
        self.assertIn("NO-DATA", out)


if __name__ == "__main__":
    unittest.main()

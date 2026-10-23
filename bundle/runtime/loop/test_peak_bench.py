"""Tests for the peak bench ledger, sub unit PP1.c."""

import contextlib
import io
import json
import os
import shutil
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import peak_bench


def _row(**overrides):
    row = {
        "task": "task-a",
        "arm": "arm-a",
        "attempt": 1,
        "receipt": "receipt-a",
        "score": 0.5,
        "passed": True,
        "cost_usd": 0.0,
    }
    row.update(overrides)
    return row


class TestPP1C(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _path(self, name):
        return os.path.join(self.tmp, name)

    def test_a_row_with_no_receipt_is_refused(self):
        for bad in (None, "", "   "):
            row = _row(receipt=bad)
            with self.assertRaises(ValueError) as ctx:
                peak_bench.validate_row(row)
            self.assertEqual(str(ctx.exception), "peak_bench: row refused, no receipt")
        row = _row()
        del row["receipt"]
        with self.assertRaises(ValueError) as ctx:
            peak_bench.validate_row(row)
        self.assertEqual(str(ctx.exception), "peak_bench: row refused, no receipt")

    def test_every_missing_row_field_is_refused_by_its_own_name(self):
        for field in peak_bench.ROW_FIELDS:
            row = _row()
            del row[field]
            with self.assertRaises(ValueError) as ctx:
                peak_bench.validate_row(row)
            if field == "receipt":
                expected = "peak_bench: row refused, no receipt"
            else:
                expected = "peak_bench: row refused, " + field
            self.assertEqual(str(ctx.exception), expected)

    def test_a_good_row_round_trips_through_the_ledger(self):
        path = self._path("ledger.jsonl")
        capture = io.StringIO()
        with contextlib.redirect_stdout(capture):
            first = peak_bench.append_row(path, _row())
            second = peak_bench.append_row(path, _row(attempt=2))
        self.assertEqual(capture.getvalue(), "")
        rows = peak_bench.read_ledger(path)
        self.assertEqual(rows, [first, second])
        with open(path, "rb") as handle:
            raw = handle.read()
        text = raw.decode("utf-8")
        self.assertTrue(text.endswith("\n"))
        self.assertEqual(len(text.splitlines()), 2)

    def test_append_row_refuses_a_receiptless_row_and_writes_nothing(self):
        fresh = self._path("fresh.jsonl")
        bad = _row()
        del bad["receipt"]
        with self.assertRaises(ValueError) as ctx:
            peak_bench.append_row(fresh, bad)
        self.assertEqual(str(ctx.exception), "peak_bench: row refused, no receipt")
        self.assertFalse(os.path.exists(fresh))
        one = self._path("one.jsonl")
        peak_bench.append_row(one, _row())
        with open(one, "rb") as handle:
            before = handle.read()
        with self.assertRaises(ValueError) as ctx:
            peak_bench.append_row(one, bad)
        self.assertEqual(str(ctx.exception), "peak_bench: row refused, no receipt")
        with open(one, "rb") as handle:
            after = handle.read()
        self.assertEqual(after, before)
        self.assertEqual(len(after.decode("utf-8").splitlines()), 1)

    def test_append_row_refuses_a_torn_ledger(self):
        path = self._path("torn.jsonl")
        with open(path, "wb") as handle:
            handle.write(json.dumps(_row(), sort_keys=True).encode("utf-8"))
        with open(path, "rb") as handle:
            before = handle.read()
        with self.assertRaises(ValueError) as ctx:
            peak_bench.append_row(path, _row())
        self.assertEqual(str(ctx.exception), "peak_bench: ledger torn")
        with open(path, "rb") as handle:
            after = handle.read()
        self.assertEqual(after, before)

    def test_an_empty_file_reads_as_no_rows(self):
        path = self._path("empty.jsonl")
        with open(path, "wb") as handle:
            handle.write(b"")
        self.assertEqual(peak_bench.read_ledger(path), [])

    def test_a_missing_ledger_and_a_directory_are_refused(self):
        missing = self._path("missing.jsonl")
        with self.assertRaises(ValueError) as ctx:
            peak_bench.read_ledger(missing)
        self.assertEqual(str(ctx.exception), "peak_bench: ledger missing")
        with self.assertRaises(ValueError) as ctx:
            peak_bench.read_ledger(self.tmp)
        self.assertEqual(str(ctx.exception), "peak_bench: ledger unreadable")
        with self.assertRaises(ValueError) as ctx:
            peak_bench.append_row(self.tmp, _row())
        self.assertEqual(str(ctx.exception), "peak_bench: ledger unreadable")

    def test_a_file_whose_last_line_has_no_newline_is_refused(self):
        path = self._path("torn2.jsonl")
        with open(path, "wb") as handle:
            handle.write(json.dumps(_row(), sort_keys=True).encode("utf-8"))
        with self.assertRaises(ValueError) as ctx:
            peak_bench.read_ledger(path)
        self.assertEqual(str(ctx.exception), "peak_bench: ledger torn")

    def test_a_line_that_is_not_json_is_refused(self):
        path = self._path("notjson.jsonl")
        with open(path, "wb") as handle:
            handle.write(b"not json\n")
        with self.assertRaises(ValueError) as ctx:
            peak_bench.read_ledger(path)
        self.assertEqual(str(ctx.exception), "peak_bench: ledger corrupt")

    def test_a_json_line_that_is_not_an_object_is_refused(self):
        path = self._path("listline.jsonl")
        with open(path, "wb") as handle:
            handle.write(b"[1, 2, 3]\n")
        with self.assertRaises(ValueError) as ctx:
            peak_bench.read_ledger(path)
        self.assertEqual(str(ctx.exception), "peak_bench: ledger corrupt")

    def test_a_saved_row_with_no_receipt_is_refused_on_read(self):
        path = self._path("noreceipt.jsonl")
        row = {
            "task": "task-a",
            "arm": "arm-a",
            "attempt": 1,
            "score": 0.5,
            "passed": True,
            "cost_usd": 0.0,
        }
        with open(path, "wb") as handle:
            handle.write((json.dumps(row) + "\n").encode("utf-8"))
        with self.assertRaises(ValueError) as ctx:
            peak_bench.read_ledger(path)
        self.assertEqual(str(ctx.exception), "peak_bench: row refused, no receipt")

    def test_hostile_fields_are_refused_by_name(self):
        cases = (
            ("attempt", True, "attempt"),
            ("attempt", 0, "attempt"),
            ("score", True, "score"),
            ("score", float("nan"), "score"),
            ("score", 2.0, "score"),
            ("cost_usd", -1.0, "cost_usd"),
            ("passed", 1, "passed"),
            ("task", "  ", "task"),
        )
        for field, value, name in cases:
            row = _row()
            row[field] = value
            with self.assertRaises(ValueError) as ctx:
                peak_bench.validate_row(row)
            self.assertEqual(str(ctx.exception), "peak_bench: row refused, " + name)
        with self.assertRaises(ValueError) as ctx:
            peak_bench.validate_row([1, 2, 3])
        self.assertEqual(str(ctx.exception), "peak_bench: row refused, not an object")

    def test_extra_keys_are_kept(self):
        row = _row()
        row["note"] = "hello"
        validated = peak_bench.validate_row(row)
        self.assertEqual(validated["note"], "hello")
        path = self._path("extra.jsonl")
        peak_bench.append_row(path, row)
        rows = peak_bench.read_ledger(path)
        self.assertEqual(rows[0]["note"], "hello")


class TestPP1D(unittest.TestCase):
    # Sub unit PP1.d: shares, the bootstrap interval and the cost ratio.

    def _spread(self):
        return [index / 39.0 for index in range(40)]

    def _ledger(self):
        return [
            peak_bench.new_row("task-a", "arm-a", 1, "receipt-1", 0.1, True, 0.5),
            peak_bench.new_row("task-a", "arm-a", 2, "receipt-2", 0.2, True, 0.0),
            peak_bench.new_row("task-b", "arm-b", 3, "receipt-3", 0.3, False, 1.0),
        ]

    def test_shares_are_fractions_of_attempts_that_sum_to_one(self):
        rows = [
            peak_bench.new_row("task-a", "arm-a", 1, "receipt-1", 0.1, True, 0.0),
            peak_bench.new_row("task-a", "arm-a", 2, "receipt-2", 0.2, True, 0.0),
            peak_bench.new_row("task-a", "arm-a", 3, "receipt-3", 0.3, True, 0.0),
            peak_bench.new_row("task-b", "arm-b", 4, "receipt-4", 0.4, True, 0.0),
        ]
        result = peak_bench.shares(rows, "arm")
        self.assertEqual(result, {"arm-a": 0.75, "arm-b": 0.25})
        self.assertEqual(sum(result.values()), 1.0)

    def test_shares_of_no_rows_is_empty(self):
        for key in ("task", "arm", "receipt"):
            self.assertEqual(peak_bench.shares([], key), {})

    def test_shares_refuses_a_key_that_is_not_a_string_valued_row_field(self):
        rows = self._ledger()
        for key in ("model", "attempt", "score", "passed", "cost_usd"):
            with self.assertRaises(ValueError) as ctx:
                peak_bench.shares(rows, key)
            self.assertEqual(str(ctx.exception), "peak_bench: shares refused, key")
        hostile = (None, True, False, 0, 1, 0.5, float("nan"), "", b"x", [], {}, (), {1, 2}, object())
        for key in hostile:
            with self.assertRaises(ValueError) as ctx:
                peak_bench.shares(rows, key)
            self.assertEqual(str(ctx.exception), "peak_bench: shares refused, key")

    def test_shares_over_each_string_valued_field_sums_to_one(self):
        rows = self._ledger()
        for key in ("task", "arm", "receipt"):
            result = peak_bench.shares(rows, key)
            self.assertAlmostEqual(sum(result.values()), 1.0)
            self.assertEqual(len(result), len(set(row[key] for row in rows)))

    def test_the_bootstrap_interval_is_ordered_and_brackets_the_values(self):
        values = self._spread()
        low, high = peak_bench.bootstrap_interval(values, 1000, 0, 0.05)
        self.assertLessEqual(low, high)
        self.assertGreaterEqual(low, min(values))
        self.assertLessEqual(high, max(values))
        mean = sum(values) / len(values)
        self.assertLess(low, mean)
        self.assertGreater(high, mean)
        one = peak_bench.bootstrap_interval(values, 1, 0, 0.05)
        self.assertLessEqual(one[0], one[1])
        flat_low, flat_high = peak_bench.bootstrap_interval([0.5] * 40, 100, 0, 0.05)
        self.assertEqual((flat_low, flat_high), (0.5, 0.5))
        single_low, single_high = peak_bench.bootstrap_interval([0.25], 100, 0, 0.05)
        self.assertEqual((single_low, single_high), (0.25, 0.25))

    def test_the_bootstrap_interval_is_deterministic_for_a_seed(self):
        values = self._spread()
        first = peak_bench.bootstrap_interval(values, 200, 7, 0.05)
        second = peak_bench.bootstrap_interval(values, 200, 7, 0.05)
        self.assertEqual(first, second)
        self.assertIsInstance(first, tuple)
        third = peak_bench.bootstrap_interval(values, 200, -9999, 0.05)
        self.assertLessEqual(third[0], third[1])

    def test_the_bootstrap_interval_refuses_hostile_arguments(self):
        hostile = (None, 0, True, False, -1, float("nan"), "", "x", b"x", {}, {"a": 1}, (), {1, 2}, object())
        for bad in hostile:
            with self.assertRaises(ValueError) as ctx:
                peak_bench.bootstrap_interval(bad)
            self.assertEqual(str(ctx.exception), "peak_bench: no values")
        with self.assertRaises(ValueError) as ctx:
            peak_bench.bootstrap_interval([])
        self.assertEqual(str(ctx.exception), "peak_bench: no values")
        for bad in ([True], ["x"], [None], [float("nan")], [float("inf")], [[1.0]], [{}]):
            with self.assertRaises(ValueError) as ctx:
                peak_bench.bootstrap_interval(bad)
            self.assertEqual(str(ctx.exception), "peak_bench: values refused")
        for bad in (True, False, 0, -1, 1.5, "10", None):
            with self.assertRaises(ValueError) as ctx:
                peak_bench.bootstrap_interval([1.0, 2.0], bad)
            self.assertEqual(str(ctx.exception), "peak_bench: iterations refused")
        for bad in (True, False, 0.5, "0", None, [0]):
            with self.assertRaises(ValueError) as ctx:
                peak_bench.bootstrap_interval([1.0, 2.0], 10, bad)
            self.assertEqual(str(ctx.exception), "peak_bench: seed refused")
        for bad in (True, False, 0.0, 1.0, float("nan"), "0.05", None, 1):
            with self.assertRaises(ValueError) as ctx:
                peak_bench.bootstrap_interval([1.0, 2.0], 10, 0, bad)
            self.assertEqual(str(ctx.exception), "peak_bench: alpha refused")

    def test_the_cost_ratio_divides_the_two_totals(self):
        rows = [
            peak_bench.new_row("task-a", "arm-a", 1, "receipt-1", 0.1, True, 0.2),
            peak_bench.new_row("task-a", "arm-a", 2, "receipt-2", 0.2, True, 0.3),
            peak_bench.new_row("task-b", "arm-b", 3, "receipt-3", 0.3, False, 1.0),
            peak_bench.new_row("task-b", "arm-b", 4, "receipt-4", 0.4, False, 1.0),
        ]
        self.assertAlmostEqual(peak_bench.cost_ratio(rows, "arm-a", "arm-b"), 0.25)
        self.assertEqual(peak_bench.cost_ratio(rows, "arm-b", "arm-b"), 1.0)
        self.assertEqual(peak_bench.cost_ratio(rows, "arm-a", "arm-a"), 1.0)

    def test_a_zero_baseline_cost_is_refused_not_a_division(self):
        rows = [
            peak_bench.new_row("task-a", "arm-a", 1, "receipt-1", 0.1, True, 1.0),
            peak_bench.new_row("task-b", "arm-b", 2, "receipt-2", 0.2, True, 0.0),
        ]
        with self.assertRaises(ValueError) as ctx:
            peak_bench.cost_ratio(rows, "arm-a", "arm-b")
        self.assertEqual(str(ctx.exception), "peak_bench: cost ratio refused, baseline cost zero")

    def test_an_arm_with_no_rows_is_refused(self):
        rows = self._ledger()
        with self.assertRaises(ValueError) as ctx:
            peak_bench.cost_ratio(rows, "arm-z", "arm-a")
        self.assertEqual(str(ctx.exception), "peak_bench: cost ratio refused, no rows for arm-z")
        with self.assertRaises(ValueError) as ctx:
            peak_bench.cost_ratio(rows, "arm-a", "arm-z")
        self.assertEqual(str(ctx.exception), "peak_bench: cost ratio refused, no rows for arm-z")
        hostile = (None, True, False, 0, 1, 0.5, float("nan"), "", b"x", [], {}, (), {1, 2}, object())
        for bad in hostile:
            with self.assertRaises(ValueError) as ctx:
                peak_bench.cost_ratio(rows, bad, "arm-a")
            self.assertEqual(str(ctx.exception), "peak_bench: cost ratio refused, arm")
            with self.assertRaises(ValueError) as ctx:
                peak_bench.cost_ratio(rows, "arm-a", bad)
            self.assertEqual(str(ctx.exception), "peak_bench: cost ratio refused, arm")

    def test_a_statistic_refuses_a_row_with_no_receipt(self):
        rows = self._ledger()
        receiptless = {
            "task": "task-a",
            "arm": "arm-a",
            "attempt": 1,
            "score": 0.5,
            "passed": True,
            "cost_usd": 0.0,
        }
        with self.assertRaises(ValueError) as ctx:
            peak_bench.shares(rows + [receiptless], "arm")
        self.assertEqual(str(ctx.exception), "peak_bench: row refused, no receipt")
        with self.assertRaises(ValueError) as ctx:
            peak_bench.cost_ratio(rows + [receiptless], "arm-a", "arm-b")
        self.assertEqual(str(ctx.exception), "peak_bench: row refused, no receipt")
        not_a_list = (None, 0, True, -1, float("nan"), "", "x", b"x", {}, {"a": 1}, (), {1, 2}, object())
        for bad in not_a_list:
            with self.assertRaises(ValueError) as ctx:
                peak_bench.shares(bad)
            self.assertEqual(str(ctx.exception), "peak_bench: rows refused")
            with self.assertRaises(ValueError) as ctx:
                peak_bench.cost_ratio(bad, "arm-a", "arm-b")
            self.assertEqual(str(ctx.exception), "peak_bench: rows refused")
        with self.assertRaises(ValueError) as ctx:
            peak_bench.shares(["x"], "arm")
        self.assertEqual(str(ctx.exception), "peak_bench: row refused, not an object")


class TestPP1E(unittest.TestCase):
    # Sub unit PP1.e: the summary, the report writer and the command line.

    def setUp(self):
        self.tmp = tempfile.mkdtemp()

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _path(self, name):
        return os.path.join(self.tmp, name)

    def _four_rows(self):
        return [
            peak_bench.new_row("task-a", "arm-a", 1, "receipt-1", 0.1, True, 0.5),
            peak_bench.new_row("task-a", "arm-a", 2, "receipt-2", 0.3, True, 0.5),
            peak_bench.new_row("task-b", "arm-b", 3, "receipt-3", 0.4, False, 1.0),
            peak_bench.new_row("task-b", "arm-b", 4, "receipt-4", 0.6, True, 1.0),
        ]

    def test_the_summary_counts_shares_and_intervals_per_arm(self):
        summary = peak_bench.summarise(self._four_rows())
        self.assertEqual(set(summary.keys()), {"rows", "shares", "arms", "cost_ratio"})
        self.assertEqual(summary["rows"], 4)
        self.assertEqual(summary["shares"], {"arm-a": 0.5, "arm-b": 0.5})
        self.assertEqual(sum(summary["shares"].values()), 1.0)
        self.assertEqual(sorted(summary["arms"].keys()), ["arm-a", "arm-b"])
        arm_a = summary["arms"]["arm-a"]
        self.assertEqual(arm_a["attempts"], 2)
        self.assertEqual(arm_a["passed"], 2)
        self.assertAlmostEqual(arm_a["score_mean"], 0.2)
        self.assertEqual(len(arm_a["score_interval"]), 2)
        self.assertLessEqual(arm_a["score_interval"][0], arm_a["score_interval"][1])
        self.assertAlmostEqual(arm_a["cost_usd"], 1.0)
        self.assertAlmostEqual(arm_a["cost_per_pass"], 0.5)
        arm_b = summary["arms"]["arm-b"]
        self.assertEqual(arm_b["attempts"], 2)
        self.assertEqual(arm_b["passed"], 1)
        self.assertAlmostEqual(arm_b["score_mean"], 0.5)
        self.assertEqual(len(arm_b["score_interval"]), 2)
        self.assertAlmostEqual(arm_b["cost_usd"], 2.0)
        self.assertAlmostEqual(arm_b["cost_per_pass"], 2.0)

    def test_the_summary_of_no_rows_is_the_empty_shape(self):
        self.assertEqual(
            peak_bench.summarise([]),
            {"rows": 0, "shares": {}, "arms": {}, "cost_ratio": {}},
        )

    def test_the_summary_cost_ratio_uses_the_first_arm(self):
        rows = [
            peak_bench.new_row("task-a", "a", 1, "receipt-1", 0.5, True, 1.0),
            peak_bench.new_row("task-b", "b", 1, "receipt-2", 0.5, True, 4.0),
        ]
        summary = peak_bench.summarise(rows)
        self.assertEqual(summary["cost_ratio"], {"b": 4.0})
        self.assertNotIn("a", summary["cost_ratio"])

    def test_the_summary_cost_ratio_is_none_for_zero_cost_rows(self):
        rows = [
            peak_bench.new_row("task-a", "a", 1, "receipt-1", 0.5, True, 0.0),
            peak_bench.new_row("task-b", "b", 1, "receipt-2", 0.5, True, 0.0),
        ]
        summary = peak_bench.summarise(rows)
        self.assertEqual(summary["cost_ratio"], {"b": None})
        self.assertEqual(summary["arms"]["a"]["cost_per_pass"], 0.0)
        self.assertEqual(summary["arms"]["b"]["cost_per_pass"], 0.0)

    def test_the_summary_refuses_a_row_with_no_receipt(self):
        rows = self._four_rows()
        receiptless = {
            "task": "task-a",
            "arm": "arm-a",
            "attempt": 5,
            "score": 0.5,
            "passed": True,
            "cost_usd": 0.0,
        }
        with self.assertRaises(ValueError) as ctx:
            peak_bench.summarise(rows + [receiptless])
        self.assertEqual(str(ctx.exception), "peak_bench: row refused, no receipt")
        hostile = (
            None, 0, True, False, -1, float("nan"), "", "x", b"x",
            {}, {"a": 1}, (), {1, 2}, object(),
        )
        for bad in hostile:
            with self.assertRaises(ValueError) as ctx:
                peak_bench.summarise(bad)
            self.assertEqual(str(ctx.exception), "peak_bench: rows refused")
        with self.assertRaises(ValueError) as ctx:
            peak_bench.summarise(["x"])
        self.assertEqual(str(ctx.exception), "peak_bench: row refused, not an object")

    def test_write_report_writes_sorted_json_with_a_final_newline(self):
        path = self._path("report.json")
        rows = self._four_rows()
        text = peak_bench.write_report(path, rows)
        self.assertTrue(text.endswith("\n"))
        self.assertEqual(json.loads(text), peak_bench.summarise(rows))
        with open(path, "r", encoding="utf-8") as handle:
            self.assertEqual(handle.read(), text)
        self.assertLess(text.index('"arms"'), text.index('"cost_ratio"'))
        self.assertLess(text.index('"cost_ratio"'), text.index('"rows"'))
        self.assertLess(text.index('"rows"'), text.index('"shares"'))

    def test_write_report_refuses_a_directory_path(self):
        with self.assertRaises(ValueError) as ctx:
            peak_bench.write_report(self.tmp, [])
        self.assertEqual(str(ctx.exception), "peak_bench: report path refused")
        for bad in (None, "", "   ", 0, True, False, -1, float("nan"), b"x", [], {}, (), {1, 2}, object()):
            with self.assertRaises(ValueError) as ctx:
                peak_bench.write_report(bad, [])
            self.assertEqual(str(ctx.exception), "peak_bench: report path refused")

    def test_main_reads_a_ledger_and_writes_a_report_exactly(self):
        ledger = self._path("ledger.jsonl")
        rows = self._four_rows()
        peak_bench.append_row(ledger, rows[0])
        peak_bench.append_row(ledger, rows[1])
        report = self._path("report.json")
        out = io.StringIO()
        err = io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            code = peak_bench.main(["--ledger", ledger, "--report", report])
        self.assertEqual(code, 0)
        self.assertEqual(err.getvalue(), "")
        self.assertEqual(
            out.getvalue(),
            "peak_bench: read 2 rows from %s\npeak_bench: report written to %s\n" % (ledger, report),
        )
        with open(report, "r", encoding="utf-8") as handle:
            parsed = json.loads(handle.read())
        self.assertEqual(parsed["rows"], 2)

    def test_main_refuses_a_missing_ledger_without_a_report(self):
        ledger = self._path("missing.jsonl")
        report = self._path("report.json")
        out = io.StringIO()
        err = io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            code = peak_bench.main(["--ledger", ledger, "--report", report])
        self.assertEqual(code, 2)
        self.assertEqual(out.getvalue(), "")
        self.assertEqual(err.getvalue(), "peak_bench: refused: peak_bench: ledger missing\n")
        self.assertFalse(os.path.exists(report))

    def test_main_refuses_a_ledger_and_report_that_are_the_same_path(self):
        path = self._path("both.jsonl")
        rows = self._four_rows()
        peak_bench.append_row(path, rows[0])
        peak_bench.append_row(path, rows[1])
        with open(path, "rb") as handle:
            before = handle.read()
        out = io.StringIO()
        err = io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            code = peak_bench.main(["--ledger", path, "--report", path])
        self.assertEqual(code, 2)
        self.assertEqual(out.getvalue(), "")
        self.assertEqual(
            err.getvalue(),
            "peak_bench: refused: peak_bench: ledger and report are the same path\n",
        )
        with open(path, "rb") as handle:
            after = handle.read()
        self.assertEqual(after, before)
        self.assertEqual(len(after.decode("utf-8").splitlines()), 2)
        relative = os.path.join(self.tmp, ".", "both.jsonl")
        out2 = io.StringIO()
        err2 = io.StringIO()
        with contextlib.redirect_stdout(out2), contextlib.redirect_stderr(err2):
            code2 = peak_bench.main(["--ledger", path, "--report", relative])
        self.assertEqual(code2, 2)
        self.assertEqual(out2.getvalue(), "")
        self.assertEqual(
            err2.getvalue(),
            "peak_bench: refused: peak_bench: ledger and report are the same path\n",
        )
        with open(path, "rb") as handle:
            after2 = handle.read()
        self.assertEqual(after2, before)


if __name__ == "__main__":
    unittest.main()

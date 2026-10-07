#!/usr/bin/env python3
"""D5.3: prospective error is reported over resolved rows only, with nothing unscoreable counted as one.

WHY THIS FILE. scripts/prediction_ledger.py prints the report a human reads a number off, and D5.3
names the properties that report must never lose, plus the one rule it was missing.

  R1 every row lands in exactly one of resolved, unresolved and pending, so no row takes a fourth
     path into a binary and their sum equals n;
  R2 accuracy and brier are computed over resolved rows only, so an unscoreable row moves neither
     figure in either direction;
  R3 coverage is resolved over the KNOWABLE population (n minus unresolved), and reads NO-DATA
     rather than a zero percent when nothing is knowable yet;
  R5 a resolution whose `actual` is missing or None is NOT a resolution. Before this sub unit such a
     row fell through to resolved and, when the prediction's own answer was also missing or None,
     registered as a HIT, which is the direction that flatters the predictor.

R4, the ordering guard, belongs to D5.2's pair() and is NOT re implemented here. What this file pins
is that score() reads the ordering verdict pair() already computes.

FIXTURE RULE. One condition per fixture, each built in a temporary directory. No test reads the live
ledger, a repository document, or the clock: every timestamp here is a fixture value.

HOSTILE INPUT. score() and pair() are called with None, a str where a list belongs, a dict, an int,
a float, bytes and a bool, and with rows holding a non dict, a bool timestamp, a NaN timestamp, a
missing timestamp and an unhashable id. Each is refused with the module's own ValueError or skipped
as NO-DATA; none may raise a raw TypeError and none may be silently scored.

Run: python3 scripts/test_prediction_ledger_d53.py
"""
import contextlib
import io
import json
import os
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import prediction_ledger as L  # noqa: E402


def pred(pid, answer, at, predictor="grade"):
    """One predict row, built as a dict rather than through predict(), so every timestamp is a fixture
    value and never a wall clock reading."""
    return {"at": at, "kind": "predict", "id": pid, "predictor": predictor,
            "subject": pid, "question": None, "answer": answer, "confidence": None}


def res(pid, actual, at, source="fixture"):
    """One resolve row. `actual` is always passed explicitly, so a fixture meaning an outcome nobody
    can read passes None, and one meaning the key is absent builds its own dict."""
    return {"at": at, "kind": "resolve", "id": pid, "actual": actual, "source": source}


class ThreeCountersPartitionN(unittest.TestCase):
    """R1: every row score() sees lands in exactly one of resolved, unresolved and pending, and their
    sum equals n."""

    def test_every_row_lands_in_exactly_one_counter(self):
        rows = [pred("g:A", "pass", 1), res("g:A", "pass", 2),
                pred("g:B", "pass", 3), res("g:B", "unresolvable", 4),
                pred("g:C", "pass", 5)]
        s = L.score(rows)["grade"]
        self.assertEqual((s["n"], s["resolved"], s["unresolved"], s["pending"]), (3, 1, 1, 1))
        self.assertEqual(s["n"], s["resolved"] + s["unresolved"] + s["pending"])

    def test_a_resolution_that_cannot_be_ordered_is_unresolved_and_stays_attached(self):
        rows = [pred("g:A", "pass", 9), res("g:A", "pass", 4)]
        _p, r, ordered = L.pair(rows)["g:A"]
        self.assertIsNotNone(r, "a resolution the ledger holds was collapsed into no resolution at all")
        self.assertFalse(ordered)
        s = L.score(rows)["grade"]
        self.assertEqual((s["n"], s["resolved"], s["unresolved"], s["pending"]), (1, 0, 1, 0))
        self.assertIsNone(s["accuracy"])

    def test_an_empty_ledger_yields_no_predictor_rather_than_a_zero(self):
        self.assertEqual(L.score([]), {})
        self.assertEqual(L.score(()), {})


class AccuracyAndBrierOverResolvedRowsOnly(unittest.TestCase):
    """R2: an unresolved or pending row never moves either figure in either direction."""

    def test_unresolved_and_pending_rows_do_not_move_accuracy(self):
        one_right = [pred("g:A", "pass", 1), res("g:A", "pass", 2)]
        plus_noise = one_right + [pred("g:B", "pass", 3), res("g:B", "unresolvable", 4),
                                  pred("g:C", "pass", 5)]
        self.assertAlmostEqual(L.score(one_right)["grade"]["accuracy"], 1.0)
        self.assertAlmostEqual(L.score(plus_noise)["grade"]["accuracy"], 1.0)

    def test_an_unresolved_row_does_not_move_brier_either(self):
        rows = [pred("g:A", "pass", 1), res("g:A", "pass", 2),
                pred("g:B", "pass", 3), res("g:B", "unresolvable", 4)]
        rows[0]["confidence"] = 0.5
        s = L.score(rows)["grade"]
        self.assertEqual(s["brier_n"], 1)
        self.assertAlmostEqual(s["brier"], 0.25)

    def test_a_wrong_resolved_row_moves_accuracy_down(self):
        rows = [pred("g:A", "pass", 1), res("g:A", "pass", 2),
                pred("g:B", "pass", 3), res("g:B", "fail", 4)]
        s = L.score(rows)["grade"]
        self.assertEqual(s["resolved"], 2)
        self.assertAlmostEqual(s["accuracy"], 0.5)


class CoverageOverTheKnowablePopulation(unittest.TestCase):
    """R3: coverage is resolved over knowable (n minus unresolved), and None, which the report prints
    as NO-DATA, when knowable is 0."""

    def test_coverage_divides_by_knowable_not_by_n(self):
        rows = [pred("probe:A", "pass", 1, predictor="probe"), res("probe:A", "pass", 2),
                pred("probe:B", "pass", 3, predictor="probe"), res("probe:B", "unresolvable", 4)]
        s = L.score(rows)["probe"]
        self.assertEqual((s["n"], s["resolved"], s["unresolved"]), (2, 1, 1))
        self.assertAlmostEqual(s["coverage"], 1.0)

    def test_coverage_over_nothing_knowable_is_none_not_zero(self):
        rows = [pred("probe:A", "pass", 1, predictor="probe"), res("probe:A", "unresolvable", 2)]
        s = L.score(rows)["probe"]
        self.assertEqual((s["n"], s["resolved"], s["unresolved"], s["pending"]), (1, 0, 1, 0))
        self.assertIsNone(s["coverage"],
                          "a knowable population of zero is NO-DATA, never a zero percent")

    def test_every_row_pending_counts_as_pending_and_has_no_accuracy(self):
        s = L.score([pred("probe:A", "pass", 1, predictor="probe")])["probe"]
        self.assertEqual((s["n"], s["resolved"], s["unresolved"], s["pending"]), (1, 0, 0, 1))
        self.assertIsNone(s["accuracy"])
        self.assertFalse(L.trustworthy(s)[0], "a predictor with nothing resolved is never trustworthy")


class AResolutionNobodyCanReadIsUnresolved(unittest.TestCase):
    """R5: a resolution whose `actual` is missing or None counts under unresolved, never resolved, and
    never registers as a hit."""

    def test_a_null_actual_is_unresolved_never_resolved_and_never_a_hit(self):
        rows = [pred("g:A", None, 1), res("g:A", None, 2)]
        s = L.score(rows)["grade"]
        self.assertEqual((s["n"], s["resolved"], s["unresolved"], s["pending"]), (1, 0, 1, 0))
        self.assertEqual(s["right"], 0)
        self.assertIsNone(s["accuracy"])

    def test_a_resolution_with_no_actual_key_at_all_is_unresolved(self):
        rows = [pred("g:A", None, 1),
                {"at": 2, "kind": "resolve", "id": "g:A", "source": "fixture"}]
        s = L.score(rows)["grade"]
        self.assertEqual((s["n"], s["resolved"], s["unresolved"], s["pending"]), (1, 0, 1, 0))
        self.assertEqual(s["right"], 0)
        self.assertIsNone(s["accuracy"])

    def test_the_refusal_does_not_swallow_a_readable_neighbour(self):
        rows = [pred("g:A", "pass", 1), res("g:A", "pass", 2),
                pred("g:B", None, 3), res("g:B", None, 4)]
        s = L.score(rows)["grade"]
        self.assertEqual((s["n"], s["resolved"], s["unresolved"], s["pending"]), (2, 1, 1, 0))
        self.assertEqual(s["right"], 1)
        self.assertAlmostEqual(s["accuracy"], 1.0)


class OneResolvedRowIsDefinedButNotTrustworthy(unittest.TestCase):
    """The sample size floor is part of a verdict, and it is what keeps a single resolved row from
    being quoted as a measurement."""

    def test_accuracy_is_defined_and_the_floor_still_withholds_it(self):
        rows = [pred("g:A", "pass", 1), res("g:A", "pass", 2)]
        s = L.score(rows)["grade"]
        self.assertAlmostEqual(s["accuracy"], 1.0)
        ok, why = L.trustworthy(s)
        self.assertFalse(ok)
        self.assertIn("need 30", why)


class HostileInputIsRefusedNotCrashedOn(unittest.TestCase):
    """Every public entry point is called with a wrong type: None, a str where a list belongs, bytes,
    a bool, an int, a float, a dict. Each is refused with the module's own ValueError or skipped as
    NO-DATA; none may raise a raw TypeError."""

    BAD = (None, "rows", 42, {"kind": "predict"}, b"rows", 3.5, True)

    def test_score_refuses_a_non_list_with_its_own_error(self):
        for bad in self.BAD:
            with self.assertRaises(ValueError):
                L.score(bad)

    def test_pair_refuses_a_non_list_with_its_own_error(self):
        for bad in self.BAD:
            with self.assertRaises(ValueError):
                L.pair(bad)

    def test_a_row_that_is_not_a_dict_is_no_data_not_a_crash(self):
        self.assertEqual(L.score([None, "rows", 42, b"x", 3.5, True]), {})
        self.assertEqual(L.pair([None, "rows", 42, b"x", 3.5, True]), {})

    def test_a_bool_timestamp_is_no_timestamp_rather_than_second_one(self):
        rows = [{"at": True, "kind": "predict", "id": "x", "predictor": "grade",
                 "answer": "pass"}, res("x", "pass", 2)]
        self.assertEqual(L.score(rows), {})

    def test_a_nan_timestamp_leaves_the_row_pending_not_scored(self):
        rows = [pred("g:A", "pass", 1), res("g:A", "pass", float("nan"))]
        s = L.score(rows)["grade"]
        self.assertEqual((s["n"], s["resolved"], s["unresolved"], s["pending"]), (1, 0, 0, 1))

    def test_a_missing_timestamp_is_no_timestamp_not_epoch_zero(self):
        rows = [pred("g:A", "pass", 1),
                {"kind": "resolve", "id": "g:A", "actual": "pass", "source": "fixture"}]
        s = L.score(rows)["grade"]
        self.assertEqual((s["n"], s["resolved"], s["unresolved"], s["pending"]), (1, 0, 0, 1))

    def test_an_unhashable_id_is_skipped_rather_than_raising(self):
        rows = [{"at": 1, "kind": "predict", "id": ["x"], "predictor": "grade",
                 "answer": "pass"},
                {"at": 2, "kind": "resolve", "id": ["x"], "actual": "pass",
                 "source": "fixture"},
                pred("g:A", "pass", 3), res("g:A", "pass", 4)]
        s = L.score(rows)
        self.assertEqual(sorted(s), ["grade"])
        self.assertEqual(s["grade"]["n"], 1)

    def test_a_predict_row_with_no_predictor_is_dropped_not_counted(self):
        rows = [{"at": 1, "kind": "predict", "id": "x", "answer": "pass"},
                pred("g:A", "pass", 3), res("g:A", "pass", 4)]
        s = L.score(rows)
        self.assertEqual(sorted(s), ["grade"])
        self.assertEqual(s["grade"]["n"], 1)


class ALedgerPathThatCannotBeReadIsNoData(unittest.TestCase):
    """A missing file and a directory where a file belongs are both NO-DATA, never a crash and never a
    ledger that silently reads as an empty verdict with zero accuracy. A non-path argument is refused
    with the module's own ValueError; a binary file is NO-DATA."""

    def test_a_missing_file_and_a_directory_both_read_as_no_data(self):
        with tempfile.TemporaryDirectory(prefix="pred-d53-path-") as d:
            self.assertEqual(L.load(os.path.join(d, "absent.jsonl")), [])
            self.assertEqual(L.load(d), [])
            self.assertEqual(L.score(L.load(d)), {})

    def test_a_non_path_argument_is_refused_with_the_module_own_error(self):
        for bad in (["rows"], {"a": 1}, 42, 3.5, True):
            with self.assertRaises(ValueError):
                L.load(bad)

    def test_a_binary_ledger_reads_as_no_data_not_a_crash(self):
        with tempfile.TemporaryDirectory(prefix="pred-d53-bin-") as d:
            p = os.path.join(d, "bin.jsonl")
            with open(p, "wb") as fh:
                fh.write(b"\xff\xfe\x00not utf-8")
            self.assertEqual(L.load(p), [])

    def test_harvest_refuses_a_non_path_argument(self):
        with self.assertRaises(ValueError):
            L.harvest(path=["rows"])


class ThePrintedReportReadsNoDataNotZeroPercent(unittest.TestCase):
    """R3 at the report boundary, in process, because the printed table is the control a human reads a
    number off. L.LEDGER is swapped for a fixture and restored afterwards; no subprocess is spawned
    and no live ledger or repository document is read."""

    def setUp(self):
        holder = tempfile.TemporaryDirectory(prefix="pred-d53-report-")
        self.addCleanup(holder.cleanup)
        self.dir = holder.name
        self.ledger = os.path.join(self.dir, "brother-predictions.jsonl")
        self.saved = L.LEDGER
        L.LEDGER = self.ledger
        self.addCleanup(self.restore_ledger)

    def restore_ledger(self):
        L.LEDGER = self.saved

    def write_ledger(self, rows):
        with open(self.ledger, "w", encoding="utf-8") as fh:
            for r in rows:
                fh.write(json.dumps(r) + "\n")

    def report(self):
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            rc = L.main(["score"])
        return rc, buf.getvalue()

    def row_for(self, text, name):
        # THE DATA LINE SPECIFICALLY, never assertIn over the whole page: the explanatory paragraph
        # under the table also contains the words NO-DATA and coverage, and a guard masked by
        # unrelated text on the same page proves nothing.
        return [l for l in text.splitlines() if l.startswith(name)][0].split()

    def test_the_coverage_column_reads_no_data_when_nothing_is_knowable(self):
        self.write_ledger([pred("probe:A", "pass", 1, predictor="probe"),
                           res("probe:A", "unresolvable", 2)])
        rc, text = self.report()
        self.assertEqual(rc, 0, text)
        cols = self.row_for(text, "probe")
        self.assertEqual(cols[1:5], ["1", "0", "1", "0"], text)
        self.assertEqual(cols[5], "NO-DATA", text)

    def test_the_coverage_column_is_a_percentage_when_something_is_knowable(self):
        self.write_ledger([pred("probe:A", "pass", 1, predictor="probe"), res("probe:A", "pass", 2),
                           pred("probe:B", "pass", 3, predictor="probe"),
                           res("probe:B", "unresolvable", 4)])
        rc, text = self.report()
        self.assertEqual(rc, 0, text)
        cols = self.row_for(text, "probe")
        self.assertEqual(cols[1:5], ["2", "1", "1", "0"], text)
        self.assertEqual(cols[5], "100%", text)

    def test_a_resolution_with_no_readable_actual_is_not_counted_resolved(self):
        self.write_ledger([pred("grade:A", "pass", 1),
                           {"at": 2, "kind": "resolve", "id": "grade:A", "source": "fixture"}])
        rc, text = self.report()
        self.assertEqual(rc, 0, text)
        cols = self.row_for(text, "grade")
        self.assertEqual(cols[1:5], ["1", "0", "1", "0"], text)

    def test_a_binary_ledger_is_no_data_and_not_a_crash(self):
        with open(self.ledger, "wb") as fh:
            fh.write(b"\xff\xfe\x00not utf-8")
        rc, text = self.report()
        self.assertEqual(rc, 2, text)
        self.assertIn("NO-DATA", text)


if __name__ == "__main__":
    unittest.main()

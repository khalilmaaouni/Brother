#!/usr/bin/env python3
"""D5.2: a forecast counts only when it was registered strictly before its outcome.

The rule is about the SHAPE of a ledger row, so most fixtures here are literal dicts handed
straight to pair() and score(). Two cases go through the real writers into a temp ledger, so
predict(), resolve() and load() are covered as well, and one case runs the module's own selftest,
so the invariants that already sat beside this rule stay pinned in the same run.

Every fixture is built here. Nothing in this file reads the repository, the running user's home, or
any document outside this folder.
"""
import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import prediction_ledger as PL

_ABSENT = object()


def _predict_row(pid, at, answer="pass", predictor="probe", subject="S"):
    row = {"kind": "predict", "id": pid, "predictor": predictor, "subject": subject, "answer": answer}
    if at is not _ABSENT:
        row["at"] = at
    return row


def _resolve_row(pid, at, actual="pass", source="fixture"):
    row = {"kind": "resolve", "id": pid, "actual": actual, "source": source}
    if at is not _ABSENT:
        row["at"] = at
    return row


class OrderingVerdict(unittest.TestCase):
    """pair() carries the verdict and score() reads it. The resolution itself is never lost."""

    def test_no_rows_at_all(self):
        self.assertEqual(PL.pair([]), {})
        self.assertEqual(PL.score([]), {})

    def test_prediction_with_no_resolution_is_pending(self):
        rows = [_predict_row("x", 1)]
        prediction, resolution, ordered = PL.pair(rows)["x"]
        self.assertIs(resolution, None)
        self.assertTrue(ordered)
        s = PL.score(rows)["probe"]
        self.assertEqual(s["n"], 1)
        self.assertEqual(s["pending"], 1)
        self.assertEqual(s["resolved"], 0)
        self.assertEqual(s["unresolved"], 0)

    def test_prediction_strictly_before_the_outcome_is_scored(self):
        rows = [_predict_row("x", 1, answer="pass"), _resolve_row("x", 2, actual="pass")]
        prediction, resolution, ordered = PL.pair(rows)["x"]
        self.assertIs(resolution, rows[1])
        self.assertTrue(ordered)
        s = PL.score(rows)["probe"]
        self.assertEqual(s["resolved"], 1)
        self.assertEqual(s["unresolved"], 0)
        self.assertEqual(s["right"], 1)

    def test_forecast_written_after_the_outcome_is_unresolved(self):
        rows = [_predict_row("x", 1, answer="pass"),
                _resolve_row("x", 2, actual="fail"),
                _predict_row("x", 3, answer="fail")]
        prediction, resolution, ordered = PL.pair(rows)["x"]
        self.assertIs(prediction, rows[2])
        self.assertIs(resolution, rows[1],
                      "an unordered resolution must stay attached and visible, not be dropped")
        self.assertFalse(ordered)
        s = PL.score(rows)["probe"]
        self.assertEqual(s["resolved"], 0)
        self.assertEqual(s["unresolved"], 1)
        self.assertEqual(s["pending"], 0)
        self.assertEqual(s["right"], 0)
        self.assertIsNone(s["accuracy"])
        self.assertIsNone(s["coverage"], "nothing is knowable, so coverage is NO-DATA, not zero")

    def test_forecast_at_the_same_time_as_the_outcome_is_unresolved(self):
        rows = [_predict_row("x", 5, answer="pass"), _resolve_row("x", 5, actual="pass")]
        prediction, resolution, ordered = PL.pair(rows)["x"]
        self.assertIs(resolution, rows[1])
        self.assertFalse(ordered, "a tie cannot be ordered, so it is never a pass")
        s = PL.score(rows)["probe"]
        self.assertEqual(s["resolved"], 0)
        self.assertEqual(s["unresolved"], 1)
        self.assertEqual(s["right"], 0)

    def test_prediction_at_epoch_zero_is_a_real_time(self):
        rows = [_predict_row("x", 0, answer="pass"), _resolve_row("x", 1, actual="pass")]
        prediction, resolution, ordered = PL.pair(rows)["x"]
        self.assertTrue(ordered, "epoch 0 is a time, not a missing value")
        s = PL.score(rows)["probe"]
        self.assertEqual(s["resolved"], 1)
        self.assertEqual(s["right"], 1)

    def test_prediction_without_a_timestamp_is_no_prediction(self):
        rows = [_predict_row("x", _ABSENT), _resolve_row("x", 1)]
        self.assertNotIn("x", PL.pair(rows))
        self.assertEqual(PL.score(rows), {})

    def test_prediction_with_a_non_numeric_timestamp_is_no_prediction(self):
        for bad in ("abc", None, [1], {"a": 1}, b"12"):
            rows = [_predict_row("x", bad), _resolve_row("x", 1)]
            self.assertNotIn("x", PL.pair(rows), "at=%r is not a usable time" % (bad,))
            self.assertEqual(PL.score(rows), {})

    def test_bool_timestamp_is_not_a_time(self):
        rows = [_predict_row("x", True), _resolve_row("x", 2)]
        self.assertNotIn("x", PL.pair(rows), "True compares as 1 and would take a real time's slot")
        self.assertEqual(PL.score(rows), {})

    def test_nan_timestamp_is_not_a_time(self):
        rows = [_predict_row("x", float("nan")), _resolve_row("x", 1)]
        self.assertNotIn("x", PL.pair(rows))
        self.assertEqual(PL.score(rows), {})

    def test_undatable_resolution_reads_as_pending(self):
        for bad in (_ABSENT, "later", float("nan"), True, None):
            rows = [_predict_row("x", 1), _resolve_row("x", bad)]
            prediction, resolution, ordered = PL.pair(rows)["x"]
            self.assertIsNone(resolution, "a resolution with no usable time cannot be ordered against")
            self.assertTrue(ordered)
            s = PL.score(rows)["probe"]
            self.assertEqual(s["pending"], 1)
            self.assertEqual(s["unresolved"], 0)
            self.assertEqual(s["resolved"], 0)

    def test_undatable_resolution_never_wins_the_first_resolve_slot(self):
        rows = [_predict_row("x", 1, answer="pass"),
                _resolve_row("x", _ABSENT, actual="fail"),
                _resolve_row("x", 2, actual="pass")]
        prediction, resolution, ordered = PL.pair(rows)["x"]
        self.assertIs(resolution, rows[2],
                      "the dated resolution comes first; an undated row is not epoch 0")
        self.assertTrue(ordered)
        s = PL.score(rows)["probe"]
        self.assertEqual(s["resolved"], 1)
        self.assertEqual(s["right"], 1)
        self.assertEqual(s["unresolved"], 0)
        self.assertEqual(s["pending"], 0)

    def test_a_mix_of_dated_and_undatable_rows_does_not_raise(self):
        rows = [_predict_row("a", 1),
                _predict_row("b", "soon"),
                _predict_row("c", float("nan")),
                _predict_row("d", _ABSENT),
                _predict_row("e", True),
                _resolve_row("a", 2),
                _resolve_row("b", 2),
                _resolve_row("d", 2),
                _resolve_row("e", 2)]
        paired = PL.pair(rows)
        self.assertEqual(sorted(paired), ["a"], "only the dated prediction is a prediction at all")
        s = PL.score(rows)["probe"]
        self.assertEqual((s["n"], s["resolved"], s["unresolved"], s["pending"]), (1, 1, 0, 0))

    def test_the_last_prediction_before_the_outcome_still_wins(self):
        rows = [_predict_row("x", 1, answer="pass"),
                _predict_row("x", 2, answer="fail"),
                _resolve_row("x", 3, actual="fail")]
        prediction, resolution, ordered = PL.pair(rows)["x"]
        self.assertIs(prediction, rows[1], "restating a claim before it is settled stays legal")
        self.assertTrue(ordered)
        s = PL.score(rows)["probe"]
        self.assertEqual(s["resolved"], 1)
        self.assertEqual(s["right"], 1)

    def test_a_second_resolution_does_not_overwrite_the_first(self):
        rows = [_predict_row("x", 1, answer="pass"),
                _resolve_row("x", 2, actual="pass", source="first"),
                _resolve_row("x", 3, actual="fail", source="second, later, contradicts")]
        prediction, resolution, ordered = PL.pair(rows)["x"]
        self.assertIs(resolution, rows[1])
        self.assertTrue(ordered)
        s = PL.score(rows)["probe"]
        self.assertEqual(s["resolved"], 1)
        self.assertEqual(s["right"], 1)


class RefusesHostileInput(unittest.TestCase):
    """Wrong type, None, bytes, NaN, a bool in a number's place and an unhashable key are refused
    or denied. None of them may raise a raw interpreter error, and none of them may be accepted."""

    def test_pair_refuses_none(self):
        with self.assertRaises(ValueError):
            PL.pair(None)

    def test_pair_refuses_a_bare_string(self):
        with self.assertRaises(ValueError):
            PL.pair("predict")

    def test_pair_refuses_bytes_and_a_dict(self):
        with self.assertRaises(ValueError):
            PL.pair(b"rows")
        with self.assertRaises(ValueError):
            PL.pair({"kind": "predict"})

    def test_score_refuses_none_and_a_wrong_type(self):
        with self.assertRaises(ValueError):
            PL.score(None)
        with self.assertRaises(ValueError):
            PL.score(42)

    def test_rows_that_are_not_dicts_are_denied_not_crashed(self):
        rows = [None, 5, "x", b"y", [], (), float("nan")]
        self.assertEqual(PL.pair(rows), {})
        self.assertEqual(PL.score(rows), {})

    def test_an_unhashable_id_is_denied_not_crashed(self):
        rows = [_predict_row(["not", "hashable"], 1), _resolve_row({"also": "unhashable"}, 2)]
        self.assertEqual(PL.pair(rows), {})
        self.assertEqual(PL.score(rows), {})

    def test_a_row_with_no_kind_is_denied(self):
        rows = [{"at": 1, "id": "x"}, {"at": 2, "id": "x"}]
        self.assertEqual(PL.pair(rows), {})
        self.assertEqual(PL.score(rows), {})


class ThroughTheRealWriters(unittest.TestCase):

    @staticmethod
    def _ledger_path():
        return os.path.join(tempfile.mkdtemp(), "pred.jsonl")

    def test_forecast_before_the_outcome_is_scored_on_disk(self):
        path = self._ledger_path()
        PL.predict("probe", "D5-2-FIXTURE", "pass", question="ordering", path=path, now=10)
        PL.resolve(PL.pid_for("probe", "D5-2-FIXTURE", "ordering"), "pass", "fixture",
                   path=path, now=11)
        s = PL.score(PL.load(path))["probe"]
        self.assertEqual((s["n"], s["resolved"], s["unresolved"], s["pending"]), (1, 1, 0, 0))
        self.assertEqual(s["right"], 1)

    def test_forecast_after_the_outcome_is_unresolved_on_disk(self):
        path = self._ledger_path()
        PL.predict("probe", "D5-2-FIXTURE", "pass", question="ordering", path=path, now=10)
        PL.resolve(PL.pid_for("probe", "D5-2-FIXTURE", "ordering"), "fail", "fixture",
                   path=path, now=11)
        PL.predict("probe", "D5-2-FIXTURE", "fail", question="ordering", path=path, now=12)
        s = PL.score(PL.load(path))["probe"]
        self.assertEqual(s["resolved"], 0)
        self.assertEqual(s["unresolved"], 1)
        self.assertEqual(s["right"], 0)
        self.assertIsNone(s["accuracy"])

    def test_harvest_still_runs_on_a_ledger_with_no_outcome_on_disk(self):
        path = self._ledger_path()
        PL.predict("probe", "no-such-subject-D5-2", "pass", question="ordering", path=path, now=10)
        self.assertEqual(PL.harvest(path=path), 0)


class TheModulesOwnSelftest(unittest.TestCase):

    def test_the_selftest_that_already_existed_stays_green(self):
        self.assertEqual(PL.selftest(), 0)


if __name__ == "__main__":
    unittest.main()

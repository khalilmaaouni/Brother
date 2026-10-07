#!/usr/bin/env python3
"""P1.b: the lifecycle a prediction actually has, and the states that never divide.

WHAT THIS PINS. scripts/prediction_ledger.py used to have two states, open or closed, and a
subject that evaporated was closed with the magic string `unresolvable` inside the `actual`
field: a state hidden inside a value. Six states now exist, exactly one per id, and only
RESOLVED reaches the accuracy and Brier denominators. EXPIRED, CANCELLED, SUPERSEDED and
INVALID are counted and reported and never divide.

TIME IS A FIXTURE VALUE. `now` is a parameter of lifecycle(), counts() and score(), so every
state here is reproducible from a fixture plus one number. Nothing in this file reads a clock,
a live ledger, the evidence tree or a repository document.

Run: python3 -B scripts/test_prediction_lifecycle.py
"""
import json
import os
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import prediction_ledger as L  # noqa: E402

#: One fixed instant, a long way from every fixture timestamp, so OPEN and EXPIRED are decided by
#: the fixture and never by the wall clock.
NOW = 1000000.0
WEEK = 604800.0


def pred(pid, at, subject="S", predictor="grade", answer="pass"):
    """One predict row as a dict, so every timestamp is a fixture value."""
    return {"at": at, "kind": "predict", "id": pid, "predictor": predictor, "subject": subject,
            "question": None, "answer": answer, "confidence": None, "attempt": None}


def res(pid, actual, at, source="fixture"):
    """One resolve row. `actual` is always explicit, so a fixture meaning an outcome nobody can
    read passes None and one meaning the key is absent builds its own dict."""
    return {"at": at, "kind": "resolve", "id": pid, "actual": actual, "source": source}


def canc(pid, at=0.0, reason="fixture abandoned it"):
    """One cancel row, the shape cancel() appends."""
    return {"at": at, "kind": "cancel", "id": pid, "reason": reason}


class ExpiredNeverEntersTheAccuracyDenominator(unittest.TestCase):
    """R3, and the registered mutation M-P1B-EXPIRED-COUNTS-AS-WRONG: one right, one wrong and
    three expired. Accuracy is 1 of 2, never 1 of 5, and the three expired rows are reported
    under their own name."""

    ROWS = [pred("g:r:-:-", 1, subject="r"), res("g:r:-:-", "pass", 2),
            pred("g:w:-:-", 3, subject="w"), res("g:w:-:-", "fail", 4),
            pred("g:e1:-:-", 5, subject="e1"), res("g:e1:-:-", "unresolvable", 6),
            pred("g:e2:-:-", 7, subject="e2"), res("g:e2:-:-", "unresolvable", 8),
            pred("g:e3:-:-", 9, subject="e3"), res("g:e3:-:-", "unresolvable", 10)]

    def test_expired_never_enters_the_accuracy_denominator(self):
        s = L.score(self.ROWS, now=NOW)["grade"]
        self.assertEqual(s["resolved"], 2)
        self.assertEqual(s["EXPIRED"], 3)
        self.assertAlmostEqual(s["accuracy"], 0.5)

    def test_the_three_expired_rows_are_counted_under_their_own_name_and_never_right(self):
        s = L.score(self.ROWS, now=NOW)["grade"]
        self.assertEqual(s["n"], 5)
        self.assertEqual(s["right"], 1)
        self.assertEqual(s["unresolved"], 3)
        self.assertEqual(s["n"], s["resolved"] + s["unresolved"] + s["pending"])

    def test_the_retired_unresolvable_marker_reads_as_expired(self):
        pairs = {"g:A:-:-": (pred("g:A:-:-", 1), res("g:A:-:-", "unresolvable", 2))}
        self.assertEqual(L.lifecycle(pairs, NOW)["g:A:-:-"], "EXPIRED")
        self.assertEqual(L.counts(pairs, NOW)["EXPIRED"], 1)


class TimeIsAParameter(unittest.TestCase):

    def test_the_same_file_plus_one_number_gives_the_state(self):
        pairs = {"g:A:-:-": (pred("g:A:-:-", 1000.0), None)}
        self.assertEqual(L.lifecycle(pairs, 1000.0)["g:A:-:-"], "OPEN")
        self.assertEqual(L.lifecycle(pairs, 1000.0 + WEEK - 1)["g:A:-:-"], "OPEN")
        self.assertEqual(L.lifecycle(pairs, 1000.0 + WEEK + 1)["g:A:-:-"], "EXPIRED")

    def test_a_ttl_of_its_own_is_honoured_rather_than_the_default(self):
        pairs = {"g:A:-:-": (pred("g:A:-:-", 1000.0), None)}
        self.assertEqual(L.lifecycle(pairs, 1005.0, ttl_seconds=10.0)["g:A:-:-"], "OPEN")
        self.assertEqual(L.lifecycle(pairs, 1010.0, ttl_seconds=10.0)["g:A:-:-"], "EXPIRED")


class OneStateForEveryId(unittest.TestCase):

    def test_counts_partitions_every_id_across_the_six_states(self):
        pairs = {
            "g:open:-:-": (pred("g:open:-:-", 1000.0), None),
            "g:res:-:-": (pred("g:res:-:-", 1000.0), res("g:res:-:-", "pass", 1001.0)),
            "g:exp:-:-": (pred("g:exp:-:-", 1000.0), res("g:exp:-:-", "unresolvable", 1001.0)),
            "g:can:-:-": (pred("g:can:-:-", 1000.0), canc("g:can:-:-", 1002.0)),
            "g:sub:-:-": (pred("g:sub:-:-", 1000.0), None),
            "g:inv:-:-": (pred("g:inv:-:-", 1000.0, subject=""), None),
        }
        c = L.counts(pairs, 1010.0, superseded=("g:sub:-:-",))
        self.assertEqual(sorted(c), sorted(L.STATES))
        self.assertEqual(c["OPEN"], 1)
        self.assertEqual(c["RESOLVED"], 1)
        self.assertEqual(c["EXPIRED"], 1)
        self.assertEqual(c["CANCELLED"], 1)
        self.assertEqual(c["SUPERSEDED"], 1)
        self.assertEqual(c["INVALID"], 1)
        self.assertEqual(sum(c.values()), 6)

    def test_lifecycle_draws_every_state_from_states(self):
        pairs = {"g:r:-:-": (pred("g:r:-:-", 1), res("g:r:-:-", "pass", 2)),
                 "g:o:-:-": (pred("g:o:-:-", 1), None)}
        states = L.lifecycle(pairs, 2.0)
        self.assertEqual(sorted(states), ["g:o:-:-", "g:r:-:-"])
        for state in states.values():
            self.assertIn(state, L.STATES)

    def test_a_resolution_nobody_can_read_is_invalid_and_never_resolved(self):
        for bad in ("missing-key", None):
            row = res("g:A:-:-", "pass", 2)
            if bad == "missing-key":
                row.pop("actual")
            else:
                row["actual"] = bad
            pairs = {"g:A:-:-": (pred("g:A:-:-", 1), row)}
            self.assertEqual(L.lifecycle(pairs, 3.0)["g:A:-:-"], "INVALID")

    def test_a_claim_written_after_its_own_outcome_is_invalid(self):
        pairs = {"g:A:-:-": (pred("g:A:-:-", 3), res("g:A:-:-", "pass", 2), False)}
        self.assertEqual(L.lifecycle(pairs, 4.0)["g:A:-:-"], "INVALID")

    def test_score_reports_the_four_terminal_states_under_their_own_names(self):
        rows = [pred("g:open:-:-", 1000.0, subject="open"),
                pred("g:res:-:-", 1000.0, subject="res"), res("g:res:-:-", "pass", 1001.0),
                pred("g:exp:-:-", 1000.0, subject="exp"), res("g:exp:-:-", "unresolvable", 1001.0),
                pred("g:can:-:-", 1000.0, subject="can"), canc("g:can:-:-", 1002.0),
                pred("g:inv:-:-", 1000.0, subject="")]
        s = L.score(rows, now=1010.0)["grade"]
        self.assertEqual(s["OPEN"], 1)
        self.assertEqual(s["RESOLVED"], 1)
        self.assertEqual(s["EXPIRED"], 1)
        self.assertEqual(s["CANCELLED"], 1)
        self.assertEqual(s["INVALID"], 1)
        self.assertEqual(s["SUPERSEDED"], 0)
        self.assertEqual(sum(s[key] for key in L.STATES), s["n"])


class OpenIsNotUnresolved(unittest.TestCase):

    def test_a_young_unanswered_claim_is_pending_not_unresolved(self):
        s = L.score([pred("g:A:-:-", 1000.0)], now=1005.0)["grade"]
        self.assertEqual(s["OPEN"], 1)
        self.assertEqual(s["pending"], 1)
        self.assertEqual(s["unresolved"], 0)


class CancelRefusesToOverwriteTheTruth(unittest.TestCase):

    def setUp(self):
        holder = tempfile.TemporaryDirectory(prefix="p1b-cancel-")
        self.addCleanup(holder.cleanup)
        self.path = os.path.join(holder.name, "predictions.jsonl")

    def write(self, rows):
        with open(self.path, "w", encoding="utf-8") as handle:
            for row in rows:
                handle.write(json.dumps(row) + "\n")

    def test_an_id_that_is_already_resolved_cannot_be_cancelled(self):
        self.write([pred("grade:A:-:-", 1), res("grade:A:-:-", "pass", 2)])
        with self.assertRaises(L.LedgerError) as caught:
            L.cancel("grade:A:-:-", "abandoned", self.path, now=3.0)
        self.assertEqual(caught.exception.code, "already_resolved")
        self.assertEqual([r for r in L.load(self.path) if r.get("kind") == "cancel"], [])

    def test_an_id_that_is_not_on_the_ledger_cannot_be_cancelled(self):
        self.write([pred("grade:A:-:-", 1)])
        with self.assertRaises(L.LedgerError) as caught:
            L.cancel("grade:nope:-:-", "abandoned", self.path, now=3.0)
        self.assertEqual(caught.exception.code, "unknown_id")

    def test_a_pending_claim_is_cancelled_and_then_reads_as_cancelled(self):
        self.write([pred("grade:B:-:-", 1)])
        row = L.cancel("grade:B:-:-", "the subject was dropped from the plan", self.path, now=3.0)
        self.assertEqual(row["kind"], "cancel")
        self.assertEqual(row["id"], "grade:B:-:-")
        s = L.score(L.load(self.path), now=4.0)["grade"]
        self.assertEqual(s["CANCELLED"], 1)
        self.assertEqual(s["RESOLVED"], 0)
        self.assertEqual(s["resolved"], 0)
        self.assertIsNone(s["accuracy"])

    def test_a_second_cancel_of_one_id_is_refused(self):
        self.write([pred("grade:C:-:-", 1)])
        L.cancel("grade:C:-:-", "abandoned once", self.path, now=2.0)
        with self.assertRaises(L.LedgerError) as caught:
            L.cancel("grade:C:-:-", "abandoned twice", self.path, now=3.0)
        self.assertEqual(caught.exception.code, "already_cancelled")


class HostileInputIsRefused(unittest.TestCase):
    """Every public entry point this sub unit adds is called with None, a wrong type, a str where
    a mapping or a list belongs, a bool where a number belongs, NaN, an unhashable key and an
    entry that is not a pair. Each is refused with this module's own error, never a raw TypeError
    and never a silent accept."""

    def test_lifecycle_refuses_a_non_mapping(self):
        for bad in (None, [], ("pairs",), "pairs", 42, 3.5, True, b"pairs"):
            with self.assertRaises(ValueError):
                L.lifecycle(bad, NOW)

    def test_lifecycle_refuses_a_now_that_is_not_a_real_number(self):
        for bad in (None, "now", [], True, float("nan"), object(), b"1"):
            with self.assertRaises(ValueError):
                L.lifecycle({}, bad)

    def test_lifecycle_refuses_a_ttl_that_is_not_a_positive_number(self):
        for bad in (None, "week", True, float("nan"), 0.0, -1.0, 0):
            with self.assertRaises(ValueError):
                L.lifecycle({}, NOW, bad)

    def test_lifecycle_refuses_a_hostile_superseded_collection(self):
        for bad in (7, 3.5, object(), "abc", b"abc", [["unhashable"]]):
            with self.assertRaises(ValueError):
                L.lifecycle({}, NOW, superseded=bad)

    def test_an_entry_that_is_not_a_pair_is_refused_with_the_modules_own_error(self):
        for bad in (None, 7, "x", {}, [], (1, 2, 3, 4)):
            with self.assertRaises(L.LedgerError):
                L.lifecycle({"a": bad}, NOW)

    def test_counts_refuses_the_same_hostile_input(self):
        for bad in (None, [], "pairs", 42, True):
            with self.assertRaises(ValueError):
                L.counts(bad, NOW)
        with self.assertRaises(ValueError):
            L.counts({}, float("nan"))

    def test_superseded_ids_refuses_a_non_list(self):
        for bad in (None, "rows", 42, {}, 3.5, True):
            with self.assertRaises(ValueError):
                L.superseded_ids(bad)

    def test_a_row_that_is_not_a_dict_is_not_a_restatement(self):
        self.assertEqual(L.superseded_ids([None, "x", 7, b"y"]), set())

    def test_a_bool_or_nan_timestamp_is_not_a_restatement(self):
        rows = [{"at": True, "kind": "predict", "id": "x"},
                {"at": float("nan"), "kind": "predict", "id": "x"},
                {"kind": "predict", "id": "x"}]
        self.assertEqual(L.superseded_ids(rows), set())

    def test_an_unhashable_id_is_skipped_rather_than_raising(self):
        self.assertEqual(L.superseded_ids([{"at": 1, "kind": "predict", "id": ["x"]}]), set())

    def test_score_refuses_a_now_that_is_not_a_real_number(self):
        for bad in ("later", float("nan"), True, object()):
            with self.assertRaises(ValueError):
                L.score([], now=bad)

    def test_cancel_refuses_a_hostile_id_reason_or_path(self):
        holder = tempfile.TemporaryDirectory(prefix="p1b-hostile-")
        self.addCleanup(holder.cleanup)
        path = os.path.join(holder.name, "p.jsonl")
        with open(path, "w", encoding="utf-8") as handle:
            handle.write(json.dumps(pred("grade:A:-:-", 1)) + "\n")
        for bad in (None, "", "   ", 7, [], object(), True, float("nan")):
            with self.assertRaises(ValueError):
                L.cancel(bad, "abandoned", path, now=2.0)
            with self.assertRaises(ValueError):
                L.cancel("grade:A:-:-", bad, path, now=2.0)
        with self.assertRaises(ValueError):
            L.cancel("grade:A:-:-", "abandoned", ["not", "a", "path"], now=2.0)
        with self.assertRaises(ValueError):
            L.cancel("grade:A:-:-", "abandoned", path, now="later")


if __name__ == "__main__":
    unittest.main(verbosity=2)

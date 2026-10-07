#!/usr/bin/env python3
"""Tests for scripts/prediction_ledger.py, sub unit P1.a: one source of truth per predictor.

Run: python3 -B scripts/test_prediction_ledger.py

Every fixture is built in a temporary folder, because this suite also runs on the public export
tree with an empty HOME: it never reads a live repository document, the real evidence directory
or the real ledger. jev's fixture rows are written by scripts/jev_calibration.py itself, which
ships beside this file, so a fixture obeys that module's real schema instead of a copy of it.
"""
import datetime
import json
import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import jev_calibration  # noqa: E402 - the module under test reads it, and this suite is its peer
import prediction_ledger  # noqa: E402 - the module this file is named for

#: The six deterministic predictors scripts/prediction_ledger.py stores itself (R1). jev is
#: deliberately absent: its rows live in the calibration ledger and are read by jev_rows().
SIX = ("spec_score", "grade", "probe", "council", "diagnostic", "done_check")

_BASE = datetime.datetime(2026, 9, 21, 0, 0, 0, tzinfo=datetime.timezone.utc)

#: Timestamp shapes probed against jev_calibration's own writer. The first one that writer
#: accepts is the one this suite then uses, so no fixture guesses `at`'s format.
_AT_CANDIDATES = (
    "%Y-%m-%dT%H:%M:%SZ",
    "%Y-%m-%dT%H:%M:%S.%fZ",
    "%Y-%m-%dT%H:%M:%S+00:00",
    "%Y-%m-%dT%H:%M:%S.%f+00:00",
    "%Y%m%dT%H%M%S%fZ",
    "%Y%m%dT%H%M%SZ",
    "%Y-%m-%dT%H:%M:%S",
    "%Y-%m-%d %H:%M:%S",
)


def _workdir(prefix):
    return tempfile.mkdtemp(prefix=prefix)


def _stamp(fmt, seconds):
    return (_BASE + datetime.timedelta(seconds=seconds)).strftime(fmt)


class _TempHome(object):
    """HOME points at a fresh temporary folder for the block, and goes back afterwards."""

    def __enter__(self):
        self.root = _workdir("p1a-home-")
        self.saved = os.environ.get("HOME")
        os.environ["HOME"] = self.root
        return self.root

    def __exit__(self, exc_type, exc, tb):
        if self.saved is None:
            os.environ.pop("HOME", None)
        else:
            os.environ["HOME"] = self.saved
        return False


def _accepted_stamp_pair():
    """(earlier, later): two `at` strings jev_calibration's own writer accepts."""
    root = _workdir("p1a-stamp-")
    for index, fmt in enumerate(_AT_CANDIDATES):
        try:
            jev_calibration.append_decision(os.path.join(root, "probe-%d.jsonl" % index), {
                "id": "probe-%d" % index,
                "family": "p1a-family",
                "qtype": "noul",
                "framing": "p1a-framing",
                "answer": "pass",
                "prob": 0.9,
                "confidence": 0.9,
                "model": "p1a-model",
                "cost": 0.0,
                "at": _stamp(fmt, 0),
            }, max_segment_bytes=None)
        except ValueError:
            continue
        return _stamp(fmt, 0), _stamp(fmt, 60)
    raise AssertionError("no candidate stamp was accepted by jev_calibration.append_decision: %r"
                         % (_AT_CANDIDATES,))


def _jev_ledger(decisions):
    """A real calibration ledger, written by its OWN writers.

    `decisions` is [(id, answer, correct)], where a `correct` of None writes a decision whose
    outcome was never recorded.
    """
    root = _workdir("p1a-jev-")
    decisions_path = os.path.join(root, "decisions.jsonl")
    outcomes_path = os.path.join(root, "outcomes.jsonl")
    at_decided, at_settled = _accepted_stamp_pair()
    for index, (ident, answer, correct) in enumerate(decisions):
        jev_calibration.append_decision(decisions_path, {
            "id": ident,
            "family": "p1a-family-%d" % index,
            "qtype": "noul",
            "framing": "p1a-framing-%d" % index,
            "answer": answer,
            "prob": 0.9,
            "confidence": 0.9,
            "model": "p1a-model",
            "cost": 0.0,
            "at": at_decided,
        }, max_segment_bytes=None)
        if correct is not None:
            jev_calibration.append_outcome(
                outcomes_path,
                {"id": ident, "correct": correct, "source": "p1a fixture", "at": at_settled},
                decisions_path=decisions_path, max_segment_bytes=None)
    return decisions_path, outcomes_path


def _flat(decisions_path, outcomes_path):
    """jev_rows()'s pairs flattened into the row list score() reads."""
    rows = []
    for prediction, resolution in prediction_ledger.jev_rows(decisions_path, outcomes_path):
        rows.append(prediction)
        if resolution is not None:
            rows.append(resolution)
    return rows


def _local_ledger():
    """One retired jev copy plus one row for each of the six predictors this file owns.

    Call it inside _TempHome(): predict() asks the filesystem whether the subject's outcome is
    already on disk, and a suite must never ask that of a real home directory.
    """
    path = os.path.join(_workdir("p1a-local-"), "predictions.jsonl")
    prediction_ledger.predict("jev", "legacy-subject", "pass", path=path, now=1)
    for index, name in enumerate(SIX):
        prediction_ledger.predict(name, "subject-%d" % index, "pass", path=path, now=2 + index)
    return path


class TestJevRows(unittest.TestCase):

    def test_an_outcome_that_stood_reads_as_a_hit_and_one_that_did_not_reads_as_a_miss(self):
        decisions, outcomes = _jev_ledger([("dec-1", "pass", True), ("dec-2", "pass", False)])
        stats = prediction_ledger.score(_flat(decisions, outcomes))["jev"]
        self.assertEqual(stats["n"], 2)
        self.assertEqual(stats["resolved"], 2)
        self.assertEqual(stats["right"], 1)
        self.assertEqual(stats["accuracy"], 0.5)

    def test_a_jev_prediction_is_shaped_like_this_ledgers_own_pairs(self):
        decisions, outcomes = _jev_ledger([("dec-1", "pass", True)])
        pairs = prediction_ledger.jev_rows(decisions, outcomes)
        self.assertEqual(len(pairs), 1)
        prediction, resolution = pairs[0]
        self.assertEqual(prediction["kind"], "predict")
        self.assertEqual(prediction["predictor"], "jev")
        self.assertEqual(prediction["id"], "dec-1")
        self.assertEqual(prediction["subject"], "dec-1")
        self.assertEqual(prediction["answer"], "pass")
        self.assertEqual(prediction["confidence"], 0.9)
        self.assertIsInstance(prediction["at"], float)
        self.assertEqual(resolution["kind"], "resolve")
        self.assertEqual(resolution["id"], "dec-1")
        self.assertEqual(resolution["actual"], "pass")

    def test_an_answer_that_did_not_stand_is_never_a_hit_whatever_the_answer_was(self):
        # The mapping this replaces wrote a literal an answer could equal, so a WRONG outcome
        # scored as a hit for any predictor whose answer happened to be that literal.
        decisions, outcomes = _jev_ledger([("dec-1", "fail", False)])
        stats = prediction_ledger.score(_flat(decisions, outcomes))["jev"]
        self.assertEqual(stats["resolved"], 1)
        self.assertEqual(stats["right"], 0)
        self.assertEqual(stats["accuracy"], 0.0)

    def test_a_decision_with_no_outcome_yet_is_a_pair_with_no_resolution(self):
        decisions, outcomes = _jev_ledger([("dec-1", "pass", None)])
        pairs = prediction_ledger.jev_rows(decisions, outcomes)
        self.assertEqual(len(pairs), 1)
        self.assertIsNone(pairs[0][1])
        self.assertEqual(prediction_ledger.score(_flat(decisions, outcomes))["jev"]["pending"], 1)

    def test_a_missing_calibration_ledger_is_no_data_not_zero_rows_that_look_clean(self):
        root = _workdir("p1a-missing-")
        self.assertEqual(
            prediction_ledger.jev_rows(os.path.join(root, "no-decisions.jsonl"),
                                       os.path.join(root, "no-outcomes.jsonl")), [])

    def test_a_corrupt_calibration_ledger_blocks_rather_than_scoring_the_readable_part(self):
        root = _workdir("p1a-corrupt-")
        decisions = os.path.join(root, "decisions.jsonl")
        with open(decisions, "w", encoding="utf-8") as handle:
            handle.write('{"kind": "not-a-decision"}\n')
        outcomes = os.path.join(root, "outcomes.jsonl")
        with open(outcomes, "w", encoding="utf-8"):
            pass
        with self.assertRaises(prediction_ledger.LedgerError) as caught:
            prediction_ledger.jev_rows(decisions, outcomes)
        self.assertEqual(caught.exception.code, "jev_ledger_corrupt")

    def test_jev_rows_refuses_a_hostile_path(self):
        for bad in (None, 7, "", "   ", [], object(), True, float("nan")):
            with self.assertRaises(ValueError):
                prediction_ledger.jev_rows(bad, "outcomes.jsonl")
            with self.assertRaises(ValueError):
                prediction_ledger.jev_rows("decisions.jsonl", bad)


class TestAllPairs(unittest.TestCase):

    def test_the_locally_stored_predictors_are_exactly_the_six_deterministic_ones(self):
        with _TempHome():
            path = _local_ledger()
            pairs = prediction_ledger.all_pairs(path, "no-decisions.jsonl", "no-outcomes.jsonl")
        self.assertEqual(pairs["predictors"], sorted(SIX))
        for prediction, _resolution, _ordered in pairs["local"].values():
            self.assertIn(prediction["predictor"], SIX)

    def test_the_retired_jev_copy_is_counted_and_left_on_disk_where_it_was(self):
        with _TempHome():
            path = _local_ledger()
            pairs = prediction_ledger.all_pairs(path, "no-decisions.jsonl", "no-outcomes.jsonl")
        self.assertEqual(pairs["skipped_jev_rows"], 1)
        with open(path, "rb") as handle:
            text = handle.read().decode("utf-8")
        on_disk = [json.loads(line) for line in text.splitlines() if line.strip()]
        self.assertIn("jev", [row.get("predictor") for row in on_disk])

    def test_all_pairs_carries_jev_rows_read_from_their_own_ledger(self):
        decisions, outcomes = _jev_ledger([("dec-1", "pass", True), ("dec-2", "pass", False)])
        root = _workdir("p1a-empty-local-")
        pairs = prediction_ledger.all_pairs(os.path.join(root, "predictions.jsonl"),
                                            decisions, outcomes)
        self.assertEqual(sorted(pairs["jev"]), ["dec-1", "dec-2"])
        for prediction, resolution, ordered in pairs["jev"].values():
            self.assertEqual(prediction["predictor"], "jev")
            self.assertIsNotNone(resolution)
            self.assertTrue(ordered)
        self.assertFalse(pairs["jev_no_data"])
        self.assertIsNone(pairs["jev_refusal"])

    def test_all_pairs_calls_a_calibration_ledger_that_is_not_there_no_data(self):
        root = _workdir("p1a-absent-")
        pairs = prediction_ledger.all_pairs(os.path.join(root, "predictions.jsonl"),
                                            os.path.join(root, "decisions.jsonl"),
                                            os.path.join(root, "outcomes.jsonl"))
        self.assertEqual(pairs["jev"], {})
        self.assertTrue(pairs["jev_no_data"])
        self.assertIsNone(pairs["jev_refusal"])

    def test_all_pairs_names_a_refused_ledger_instead_of_reading_it_as_no_rows(self):
        root = _workdir("p1a-refused-")
        decisions = os.path.join(root, "decisions.jsonl")
        with open(decisions, "w", encoding="utf-8") as handle:
            handle.write('{"kind": "not-a-decision"}\n')
        outcomes = os.path.join(root, "outcomes.jsonl")
        with open(outcomes, "w", encoding="utf-8"):
            pass
        pairs = prediction_ledger.all_pairs(os.path.join(root, "predictions.jsonl"),
                                            decisions, outcomes)
        self.assertEqual(pairs["jev"], {})
        self.assertTrue(pairs["jev_no_data"])
        self.assertEqual(pairs["jev_refusal"], "jev_ledger_corrupt")

    def test_all_pairs_refuses_a_hostile_path(self):
        for bad in (None, 7, object(), float("nan")):
            with self.assertRaises(ValueError):
                prediction_ledger.all_pairs(bad, "decisions.jsonl", "outcomes.jsonl")
        with self.assertRaises(ValueError):
            prediction_ledger.all_pairs("predictions.jsonl", None, "outcomes.jsonl")
        with self.assertRaises(ValueError):
            prediction_ledger.all_pairs("predictions.jsonl", "decisions.jsonl", None)


class TestOutcomeExists(unittest.TestCase):

    def test_a_run_status_on_disk_is_an_outcome_that_already_exists(self):
        with _TempHome() as home:
            run = os.path.join(home, ".claude", "evidence", "unit-runs", "P1.a-260921")
            os.makedirs(run)
            with open(os.path.join(run, "STATUS"), "w", encoding="utf-8") as handle:
                handle.write("READY\n")
            self.assertTrue(prediction_ledger.outcome_exists("P1.a"))
            self.assertFalse(prediction_ledger.outcome_exists("P1.b"))

    def test_a_run_folder_with_no_status_is_not_an_outcome_yet(self):
        with _TempHome() as home:
            os.makedirs(os.path.join(home, ".claude", "evidence", "unit-runs", "P1.c-260921"))
            self.assertFalse(prediction_ledger.outcome_exists("P1.c"))

    def test_outcome_exists_refuses_a_hostile_subject(self):
        for bad in (None, "", "   ", 7, [], object(), True, float("nan")):
            with self.assertRaises(ValueError):
                prediction_ledger.outcome_exists(bad)


class TestPredictRefusesHindsight(unittest.TestCase):

    def test_predict_refuses_a_subject_whose_outcome_is_already_on_disk(self):
        with _TempHome() as home:
            run = os.path.join(home, ".claude", "evidence", "unit-runs", "P1.a-260921")
            os.makedirs(run)
            with open(os.path.join(run, "STATUS"), "w", encoding="utf-8") as handle:
                handle.write("READY\n")
            ledger = os.path.join(_workdir("p1a-hindsight-"), "predictions.jsonl")
            with self.assertRaises(prediction_ledger.LedgerError) as caught:
                prediction_ledger.predict("grade", "P1.a", "pass", path=ledger, now=1)
            self.assertEqual(caught.exception.code, "hindsight")
            self.assertFalse(os.path.exists(ledger))
            prediction_ledger.predict("grade", "P1.b", "pass", path=ledger, now=1)
            self.assertEqual(len(prediction_ledger.load(ledger)), 1)

    def test_predict_refuses_a_hostile_subject_without_writing_anything(self):
        with _TempHome():
            ledger = os.path.join(_workdir("p1a-hostile-"), "predictions.jsonl")
            for bad in (None, 7, [], object(), float("nan")):
                with self.assertRaises(ValueError):
                    prediction_ledger.predict("grade", bad, "pass", path=ledger, now=1)
            self.assertFalse(os.path.exists(ledger))


if __name__ == "__main__":
    unittest.main(verbosity=2)

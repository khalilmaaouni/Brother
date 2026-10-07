"""D1.6 tests: per-child retry recording in bm_repair.

Every assertion here is the D1.6 contract: a repair records one child per
attempt, every child carries the six required fields, and repair_children
refuses a record whose children are absent, short or not a list. Removing
the D1.6 additions from bm_repair.py turns every test in this file red,
which is the point: a test that passed on the unchanged tree would prove
nothing.

No test here reads a repository document, a vault, a real claim store or a
real worker: each builds its worker, its verifier and its verdict in memory.
"""
import os
import sys
import unittest

_HERE = os.path.dirname(os.path.abspath(__file__))
if _HERE not in sys.path:
    sys.path.insert(0, _HERE)

import bm_repair  # noqa: E402


def _no_recall(query, cwd=None):
    """A recall that finds nothing, so no vault is needed to run these."""
    return "", ""


def _failing_verifier(unit, cwd=None):
    return {"verdict": bm_repair.bm_verify.FAIL, "reason": "still red",
            "command": "python3 -m unittest example",
            "exit_code": 1, "output_digest": "ab" * 32,
            "revision": "deadbeef"}


def _healing_verifier(after):
    state = {"seen": 0}

    def _verify(unit, cwd=None):
        state["seen"] += 1
        passed = state["seen"] >= after
        return {"verdict": bm_repair.bm_verify.PASS if passed
                else bm_repair.bm_verify.FAIL,
                "reason": "pass" if passed else "still red",
                "command": "python3 -m unittest example",
                "exit_code": 0 if passed else 1,
                "output_digest": "cd" * 32, "revision": "cafe"}
    return _verify


class _UsageWorker(object):
    """A worker whose every run reports one additive usage block."""

    def __init__(self, usage):
        self.usage = dict(usage)
        self.seen = 0

    def run(self, brief, cwd=None):
        self.seen += 1
        return {"status": "returned",
                "worker_claim": "attempt %d" % self.seen,
                "artifacts": [],
                "cost": {"tokens": 0, "minutes": 0},
                "usage": dict(self.usage)}


class _CountingWorker(object):
    """A worker whose usage names which attempt it was, so the order of the
    recorded children can be read without trusting any id format."""

    def __init__(self):
        self.seen = 0

    def run(self, brief, cwd=None):
        self.seen += 1
        return {"status": "returned",
                "worker_claim": "attempt %d" % self.seen,
                "artifacts": [],
                "cost": {"tokens": 0, "minutes": 0},
                "usage": {"tokens_in": float(self.seen)}}


class _NoLaneWorker(object):
    """A worker whose run() cannot be handed a working directory, which is
    the one case bm_repair already refuses as refused-no-lane."""

    def run(self, brief):
        return {"status": "returned", "worker_claim": "no lane"}


def _good_child():
    return {"attempt_id": "U1#1", "state": "failed", "usage": None,
            "evidence": {"command": "c", "exit_code": 1,
                         "output_digest": "ab" * 32, "revision": "rev"},
            "executed_lane": None, "lane_divergence": None}


class TestD16Repair(unittest.TestCase):
    def test_required_fields_are_the_six_named_by_the_contract(self):
        self.assertEqual(
            tuple(bm_repair.REPAIR_CHILD_REQUIRED_FIELDS),
            ("attempt_id", "state", "usage", "evidence", "executed_lane",
             "lane_divergence"))

    def test_sum_covers_every_repair_child(self):
        record = bm_repair.repair(
            {"objective": "x", "id": "U1"},
            {"verdict": bm_repair.bm_verify.FAIL, "reason": "still red"},
            _UsageWorker({"tokens_in": 100.0}),
            verifier=_failing_verifier, recall=_no_recall, max_attempts=3)
        children = bm_repair.repair_children(record)
        self.assertEqual(len(children), 3)
        total = 0.0
        covered = 0
        for child in children:
            self.assertIsInstance(child["usage"], dict)
            total += child["usage"]["tokens_in"]
            covered += 1
        self.assertEqual(total, 300.0)
        self.assertEqual(covered, 3)

    def test_every_child_carries_the_six_required_fields(self):
        record = bm_repair.repair(
            {"objective": "x", "id": "U8"},
            {"verdict": bm_repair.bm_verify.FAIL, "reason": "still red"},
            _UsageWorker({"tokens_in": 1.0}),
            verifier=_failing_verifier, recall=_no_recall, max_attempts=2)
        for child in bm_repair.repair_children(record):
            for field in bm_repair.REPAIR_CHILD_REQUIRED_FIELDS:
                self.assertIn(field, child)

    def test_children_are_chronological_and_carry_distinct_ids(self):
        record = bm_repair.repair(
            {"objective": "x", "id": "U7"},
            {"verdict": bm_repair.bm_verify.FAIL, "reason": "still red"},
            _CountingWorker(), verifier=_failing_verifier, recall=_no_recall,
            max_attempts=3)
        children = bm_repair.repair_children(record)
        self.assertEqual([c["usage"]["tokens_in"] for c in children],
                         [1.0, 2.0, 3.0])
        ids = [c["attempt_id"] for c in children]
        self.assertEqual(len(set(ids)), 3)
        for attempt_id in ids:
            self.assertIsInstance(attempt_id, str)
            self.assertTrue(attempt_id)

    def test_child_evidence_carries_command_exit_digest_and_revision(self):
        record = bm_repair.repair(
            {"objective": "x", "id": "U3"},
            {"verdict": bm_repair.bm_verify.FAIL, "reason": "still red"},
            _UsageWorker({"tokens_in": 1.0}),
            verifier=_failing_verifier, recall=_no_recall, max_attempts=1)
        evidence = bm_repair.repair_children(record)[0]["evidence"]
        self.assertEqual(evidence["command"], "python3 -m unittest example")
        self.assertEqual(evidence["exit_code"], 1)
        self.assertEqual(evidence["output_digest"], "ab" * 32)
        self.assertEqual(evidence["revision"], "deadbeef")

    def test_child_states_are_done_failed_or_abandoned(self):
        record = bm_repair.repair(
            {"objective": "x", "id": "U4"},
            {"verdict": bm_repair.bm_verify.FAIL, "reason": "still red"},
            _UsageWorker({"tokens_in": 1.0}),
            verifier=_healing_verifier(2), recall=_no_recall, max_attempts=3)
        states = [c["state"] for c in bm_repair.repair_children(record)]
        self.assertEqual(states, ["failed", "done"])

    def test_abandoned_attempt_is_recorded_as_a_child(self):
        record = bm_repair.repair(
            {"objective": "x", "id": "U9"},
            {"verdict": bm_repair.bm_verify.FAIL, "reason": "still red"},
            _NoLaneWorker(), verifier=_failing_verifier, recall=_no_recall,
            cwd="/tmp/example-lane", max_attempts=3)
        children = bm_repair.repair_children(record)
        self.assertEqual([c["state"] for c in children], ["abandoned"])

    def test_no_data_verdict_records_no_child(self):
        record = bm_repair.repair(
            {"objective": "x", "id": "U2"},
            {"verdict": bm_repair.bm_verify.NO_DATA, "reason": "no check"},
            _UsageWorker({"tokens_in": 1.0}),
            verifier=_failing_verifier, recall=_no_recall)
        self.assertEqual(record["outcome"], bm_repair.NOT_REPAIRABLE)
        self.assertEqual(bm_repair.repair_children(record), [])

    def test_missing_required_field_refused_by_name(self):
        for field in bm_repair.REPAIR_CHILD_REQUIRED_FIELDS:
            child = _good_child()
            del child[field]
            record = {"outcome": bm_repair.EXHAUSTED,
                      "attempts": [{"attempt": 1}], "children": [child]}
            with self.assertRaises(ValueError) as caught:
                bm_repair.repair_children(record)
            self.assertIn(field, str(caught.exception))

    def test_empty_children_with_attempts_refused(self):
        record = {"outcome": bm_repair.EXHAUSTED,
                  "attempts": [{"attempt": 1}, {"attempt": 2}],
                  "children": []}
        with self.assertRaises(ValueError) as caught:
            bm_repair.repair_children(record)
        self.assertIn("child", str(caught.exception))

    def test_short_child_list_refused(self):
        record = {"outcome": bm_repair.REPAIRED,
                  "attempts": [{"attempt": 1}, {"attempt": 2}],
                  "children": [_good_child()]}
        with self.assertRaises(ValueError) as caught:
            bm_repair.repair_children(record)
        self.assertIn("child", str(caught.exception))

    def test_non_mapping_child_refused(self):
        for bad in (None, 42, "child", [], True, float("nan")):
            record = {"outcome": bm_repair.REPAIRED,
                      "attempts": [{"attempt": 1}], "children": [bad]}
            with self.assertRaises(ValueError):
                bm_repair.repair_children(record)

    def test_hostile_record_refused_never_raises_a_raw_exception(self):
        for bad in (None, 42, True, False, float("nan"), "text", b"bytes",
                    [], (), set(), object()):
            with self.assertRaises(ValueError):
                bm_repair.repair_children(bad)
        for bad_children in (None, 42, "text", b"bytes", {}, True, (1,)):
            with self.assertRaises(ValueError):
                bm_repair.repair_children(
                    {"outcome": bm_repair.REPAIRED, "attempts": [],
                     "children": bad_children})

    def test_record_without_attempts_and_without_children_is_empty(self):
        record = {"outcome": bm_repair.NOT_REPAIRABLE, "attempts": [],
                  "children": []}
        self.assertEqual(bm_repair.repair_children(record), [])

    def test_repair_refuses_hostile_unit_and_verdict_and_worker(self):
        good_unit = {"objective": "x", "id": "U5"}
        good_verdict = {"verdict": bm_repair.bm_verify.FAIL, "reason": "red"}
        for bad_unit in (None, 42, "text", [], (), float("nan")):
            record = bm_repair.repair(bad_unit, good_verdict,
                                      _UsageWorker({"tokens_in": 1.0}),
                                      verifier=_failing_verifier,
                                      recall=_no_recall)
            self.assertEqual(record["outcome"], bm_repair.NOT_REPAIRABLE)
            self.assertEqual(bm_repair.repair_children(record), [])
        for bad_verdict in (None, 42, "text", [], float("nan")):
            record = bm_repair.repair(good_unit, bad_verdict,
                                      _UsageWorker({"tokens_in": 1.0}),
                                      verifier=_failing_verifier,
                                      recall=_no_recall)
            self.assertEqual(record["outcome"], bm_repair.NOT_REPAIRABLE)
            self.assertEqual(bm_repair.repair_children(record), [])
        for bad_worker in (None, 42, "text", [], object()):
            record = bm_repair.repair(good_unit, good_verdict, bad_worker,
                                      verifier=_failing_verifier,
                                      recall=_no_recall)
            self.assertEqual(record["outcome"], bm_repair.NOT_REPAIRABLE)
            self.assertEqual(bm_repair.repair_children(record), [])


if __name__ == "__main__":
    unittest.main()

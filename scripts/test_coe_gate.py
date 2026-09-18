#!/usr/bin/env python3
"""Tests for coe_gate.py (COE-07). Plain unittest, runnable directly:

    python3 scripts/test_coe_gate.py -v

No subprocess, no sleep, no network. Disk tests use tempfile.TemporaryDirectory
so the suite stays fast and never touches the real repository tree.
"""

import io
import json
import os
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout

import coe_gate
import coe_loop


def _loop_result(outcome, final_score=9, capping_criterion="C7", reason="test reason"):
    return coe_loop.LoopResult(
        outcome=outcome,
        rounds=(),
        final_score=final_score,
        capping_criterion=capping_criterion,
        exploits_accepted=(),
        reason=reason,
    )


def _pass_record(row_id="SEL-1", problem=None):
    return coe_gate.SelectionRecord(
        row_id=row_id,
        problem=problem,
        outcome=coe_loop.OUTCOME_PASS,
        final_score=9,
        capping_criterion="C3",
        reason="two clean rounds",
        closed_at="2026-09-18T00:00:00+00:00",
    )


def _ceiling_record(row_id="SEL-1", problem=None, capping="C7"):
    return coe_gate.SelectionRecord(
        row_id=row_id,
        problem=problem,
        outcome=coe_loop.OUTCOME_CEILING,
        final_score=6,
        capping_criterion=capping,
        reason="stuck three rounds",
        closed_at="2026-09-18T00:00:00+00:00",
    )


class MissingVersusOpenVersusClosed(unittest.TestCase):
    """Rule 4: missing and open are refused for DIFFERENT stated reasons,
    and rule 2's positive case proves the gate is not a universal refuser
    (the bad state named in the module docstring that a green run would
    also pass).
    """

    def test_missing_selection_is_refused(self):
        build_row = {"row_id": "BUILD-1", "selection_row_id": "SEL-1"}
        allowed, reason = coe_gate.may_open(build_row, selections={})
        self.assertFalse(allowed)
        self.assertIn("SEL-1", reason)

    def test_open_selection_is_refused(self):
        build_row = {"row_id": "BUILD-1", "selection_row_id": "SEL-1"}
        allowed, reason = coe_gate.may_open(build_row, selections={"SEL-1": None})
        self.assertFalse(allowed)
        self.assertIn("SEL-1", reason)

    def test_missing_and_open_reasons_are_distinguishable(self):
        # This is the mutation target for M2: a gate that cannot tell a
        # never-started selection from a started-but-unfinished one would
        # make both of these reasons read the same way, and a morning
        # reader could not act on either refusal correctly.
        build_row = {"row_id": "BUILD-1", "selection_row_id": "SEL-1"}
        _, missing_reason = coe_gate.may_open(build_row, selections={})
        _, open_reason = coe_gate.may_open(build_row, selections={"SEL-1": None})
        self.assertNotEqual(missing_reason, open_reason)
        self.assertIn("MISSING", missing_reason)
        self.assertIn("OPEN", open_reason)
        self.assertNotIn("MISSING", open_reason)
        self.assertNotIn("OPEN", missing_reason)

    def test_pass_selection_opens_build_row(self):
        # The one test a "refuse everything, always" gate cannot pass.
        build_row = {"row_id": "BUILD-1", "selection_row_id": "SEL-1"}
        selections = {"SEL-1": _pass_record()}
        allowed, reason = coe_gate.may_open(build_row, selections=selections)
        self.assertTrue(allowed)
        self.assertIn("PASS", reason)


class RejectedAndAbandonedNeverOpen(unittest.TestCase):
    def test_rejected_selection_is_refused(self):
        record = coe_gate.SelectionRecord(
            row_id="SEL-1", problem=None, outcome=coe_loop.OUTCOME_REJECTED,
            final_score=None, capping_criterion=None, reason="withdrawn",
            closed_at="2026-09-18T00:00:00+00:00",
        )
        build_row = {"row_id": "BUILD-1", "selection_row_id": "SEL-1"}
        allowed, reason = coe_gate.may_open(build_row, selections={"SEL-1": record})
        self.assertFalse(allowed)
        self.assertIn("REJECTED", reason)

    def test_abandoned_selection_is_refused(self):
        record = coe_gate.SelectionRecord(
            row_id="SEL-1", problem=None, outcome=coe_loop.OUTCOME_ABANDONED,
            final_score=None, capping_criterion=None, reason="ran out of rounds",
            closed_at="2026-09-18T00:00:00+00:00",
        )
        build_row = {"row_id": "BUILD-1", "selection_row_id": "SEL-1"}
        allowed, reason = coe_gate.may_open(build_row, selections={"SEL-1": record})
        self.assertFalse(allowed)
        self.assertIn("ABANDONED", reason)


class CeilingNeedsAnOwnerDecision(unittest.TestCase):
    """Rule 3, and the mutation target for M1."""

    def test_ceiling_without_decision_is_refused(self):
        build_row = {"row_id": "BUILD-1", "selection_row_id": "SEL-1"}
        selections = {"SEL-1": _ceiling_record()}
        allowed, reason = coe_gate.may_open(build_row, selections=selections)
        self.assertFalse(allowed)
        self.assertIn("owner", reason)

    def test_ceiling_with_named_decision_opens(self):
        build_row = {
            "row_id": "BUILD-1",
            "selection_row_id": "SEL-1",
            "ceiling_decision": {"owner": "Khalil", "reason": "acceptable risk"},
        }
        selections = {"SEL-1": _ceiling_record()}
        allowed, reason = coe_gate.may_open(build_row, selections=selections)
        self.assertTrue(allowed)
        self.assertIn("Khalil", reason)

    def test_ceiling_decision_missing_reason_is_refused(self):
        build_row = {
            "row_id": "BUILD-1",
            "selection_row_id": "SEL-1",
            "ceiling_decision": {"owner": "Khalil", "reason": ""},
        }
        selections = {"SEL-1": _ceiling_record()}
        allowed, _ = coe_gate.may_open(build_row, selections=selections)
        self.assertFalse(allowed)

    def test_ceiling_decision_missing_owner_is_refused(self):
        build_row = {
            "row_id": "BUILD-1",
            "selection_row_id": "SEL-1",
            "ceiling_decision": {"owner": "  ", "reason": "acceptable risk"},
        }
        selections = {"SEL-1": _ceiling_record()}
        allowed, _ = coe_gate.may_open(build_row, selections=selections)
        self.assertFalse(allowed)

    def test_ceiling_decision_wrong_shape_is_refused(self):
        build_row = {
            "row_id": "BUILD-1",
            "selection_row_id": "SEL-1",
            "ceiling_decision": "Khalil said it was fine",
        }
        selections = {"SEL-1": _ceiling_record()}
        allowed, _ = coe_gate.may_open(build_row, selections=selections)
        self.assertFalse(allowed)


class EdgeList(unittest.TestCase):
    def test_build_row_that_is_its_own_selection_row(self):
        build_row = {"row_id": "ROW-1", "selection_row_id": "ROW-1"}
        selections = {"ROW-1": _pass_record(row_id="ROW-1")}
        allowed, _ = coe_gate.may_open(build_row, selections=selections)
        self.assertTrue(allowed)

    def test_selection_closed_for_a_different_problem_is_refused(self):
        build_row = {
            "row_id": "BUILD-1", "selection_row_id": "SEL-1", "problem": "P2",
        }
        selections = {"SEL-1": _pass_record(problem="P1")}
        allowed, reason = coe_gate.may_open(build_row, selections=selections)
        self.assertFalse(allowed)
        self.assertIn("P1", reason)
        self.assertIn("P2", reason)

    def test_matching_problem_is_allowed(self):
        build_row = {
            "row_id": "BUILD-1", "selection_row_id": "SEL-1", "problem": "P1",
        }
        selections = {"SEL-1": _pass_record(problem="P1")}
        allowed, _ = coe_gate.may_open(build_row, selections=selections)
        self.assertTrue(allowed)

    def test_problem_unstated_on_one_side_is_not_a_mismatch(self):
        build_row = {"row_id": "BUILD-1", "selection_row_id": "SEL-1"}
        selections = {"SEL-1": _pass_record(problem="P1")}
        allowed, _ = coe_gate.may_open(build_row, selections=selections)
        self.assertTrue(allowed)

    def test_empty_build_row_id_is_refused(self):
        build_row = {"row_id": "", "selection_row_id": "SEL-1"}
        selections = {"SEL-1": _pass_record()}
        allowed, reason = coe_gate.may_open(build_row, selections=selections)
        self.assertFalse(allowed)
        self.assertIn("empty", reason)

    def test_missing_build_row_id_is_refused(self):
        build_row = {"selection_row_id": "SEL-1"}
        allowed, _ = coe_gate.may_open(build_row, selections={"SEL-1": _pass_record()})
        self.assertFalse(allowed)

    def test_build_row_naming_no_selection_row_is_refused(self):
        build_row = {"row_id": "BUILD-1"}
        allowed, reason = coe_gate.may_open(build_row, selections={})
        self.assertFalse(allowed)
        self.assertIn("BUILD-1", reason)

    def test_unrecognised_selections_value_raises(self):
        build_row = {"row_id": "BUILD-1", "selection_row_id": "SEL-1"}
        with self.assertRaises(coe_gate.GateRefused):
            coe_gate.may_open(build_row, selections={"SEL-1": "not a record"})

    def test_build_row_not_a_mapping_raises(self):
        with self.assertRaises(coe_gate.GateRefused):
            coe_gate.may_open("not a dict", selections={})


class ClosePersistsAndIsIdempotent(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.store_path = os.path.join(self._tmp.name, "selections.json")

    def tearDown(self):
        self._tmp.cleanup()

    def test_close_then_status_round_trips_through_disk(self):
        loop_result = _loop_result(coe_loop.OUTCOME_PASS, final_score=9)
        written = coe_gate.close_selection(
            "SEL-1", loop_result, store_path=self.store_path, problem="P1"
        )
        self.assertEqual(written.outcome, coe_loop.OUTCOME_PASS)

        # Discard the in-memory object and re-read from disk (rule 7: state
        # survives the process).
        read_back = coe_gate.status("SEL-1", store_path=self.store_path)
        self.assertIsNotNone(read_back)
        self.assertEqual(read_back.outcome, coe_loop.OUTCOME_PASS)
        self.assertEqual(read_back.problem, "P1")
        self.assertEqual(read_back.final_score, 9)

    def test_closing_twice_with_the_same_result_is_idempotent(self):
        loop_result = _loop_result(coe_loop.OUTCOME_PASS, final_score=9)
        first = coe_gate.close_selection(
            "SEL-1", loop_result, store_path=self.store_path, problem="P1"
        )
        second = coe_gate.close_selection(
            "SEL-1", loop_result, store_path=self.store_path, problem="P1"
        )
        self.assertEqual(first, second)
        with open(self.store_path, "r", encoding="utf-8") as handle:
            payload = json.load(handle)
        self.assertEqual(len(payload["selections"]), 1)

    def test_closing_twice_with_different_outcomes_raises(self):
        coe_gate.close_selection(
            "SEL-1", _loop_result(coe_loop.OUTCOME_PASS), store_path=self.store_path
        )
        with self.assertRaises(coe_gate.GateRefused):
            coe_gate.close_selection(
                "SEL-1", _loop_result(coe_loop.OUTCOME_CEILING),
                store_path=self.store_path,
            )

    def test_close_rejects_unrecognised_outcome(self):
        loop_result = _loop_result("SOMETHING_ELSE")
        with self.assertRaises(coe_gate.GateRefused):
            coe_gate.close_selection("SEL-1", loop_result, store_path=self.store_path)

    def test_close_rejects_empty_row_id(self):
        with self.assertRaises(coe_gate.GateRefused):
            coe_gate.close_selection(
                "", _loop_result(coe_loop.OUTCOME_PASS), store_path=self.store_path
            )

    def test_close_handles_loop_result_with_no_rounds_at_all(self):
        # An ABANDONED LoopResult with rounds=() (e.g. a pre-loop nomination
        # refusal, or max_rounds=0): close_selection never inspects .rounds,
        # so this closes exactly like any other ABANDONED result.
        loop_result = coe_loop.LoopResult(
            outcome=coe_loop.OUTCOME_ABANDONED, rounds=(), final_score=None,
            capping_criterion=None, exploits_accepted=(),
            reason="max_rounds is 0: no round was permitted to run",
        )
        record = coe_gate.close_selection(
            "SEL-1", loop_result, store_path=self.store_path
        )
        self.assertEqual(record.outcome, coe_loop.OUTCOME_ABANDONED)
        build_row = {"row_id": "BUILD-1", "selection_row_id": "SEL-1"}
        allowed, _ = coe_gate.may_open(
            build_row, selections=coe_gate.load_store(self.store_path)
        )
        self.assertFalse(allowed)

    def test_status_of_unknown_row_is_none(self):
        self.assertIsNone(coe_gate.status("NOPE", store_path=self.store_path))

    def test_status_of_open_row_is_none(self):
        coe_gate.mark_open("SEL-1", store_path=self.store_path)
        self.assertIsNone(coe_gate.status("SEL-1", store_path=self.store_path))

    def test_mark_open_refuses_to_reopen_a_closed_row(self):
        coe_gate.close_selection(
            "SEL-1", _loop_result(coe_loop.OUTCOME_PASS), store_path=self.store_path
        )
        with self.assertRaises(coe_gate.GateRefused):
            coe_gate.mark_open("SEL-1", store_path=self.store_path)


class StoreLoadingIsFailClosed(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.store_path = os.path.join(self._tmp.name, "selections.json")

    def tearDown(self):
        self._tmp.cleanup()

    def test_missing_store_file_is_empty_not_an_error(self):
        self.assertEqual(coe_gate.load_store(self.store_path), {})

    def test_malformed_json_raises(self):
        with open(self.store_path, "w", encoding="utf-8") as handle:
            handle.write("{not valid json")
        with self.assertRaises(coe_gate.GateRefused):
            coe_gate.load_store(self.store_path)

    def test_wrong_top_level_shape_raises(self):
        with open(self.store_path, "w", encoding="utf-8") as handle:
            json.dump(["not", "the", "right", "shape"], handle)
        with self.assertRaises(coe_gate.GateRefused):
            coe_gate.load_store(self.store_path)

    def test_missing_selections_key_raises(self):
        with open(self.store_path, "w", encoding="utf-8") as handle:
            json.dump({"schema_version": "x"}, handle)
        with self.assertRaises(coe_gate.GateRefused):
            coe_gate.load_store(self.store_path)

    def test_entry_missing_required_field_raises(self):
        with open(self.store_path, "w", encoding="utf-8") as handle:
            json.dump({"selections": {"SEL-1": {"outcome": "PASS"}}}, handle)
        with self.assertRaises(coe_gate.GateRefused):
            coe_gate.load_store(self.store_path)

    def test_entry_with_bad_outcome_raises(self):
        with open(self.store_path, "w", encoding="utf-8") as handle:
            json.dump(
                {"selections": {"SEL-1": {
                    "outcome": "MAYBE", "reason": "x", "closed_at": "2026",
                }}},
                handle,
            )
        with self.assertRaises(coe_gate.GateRefused):
            coe_gate.load_store(self.store_path)


class RefusalsAreLoggedAndCountable(unittest.TestCase):
    """Rule 6: the whole reason this gate exists rather than being another
    silent refuser.
    """

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.store_path = os.path.join(self._tmp.name, "selections.json")

    def tearDown(self):
        self._tmp.cleanup()

    def test_no_refusals_logged_before_any_check(self):
        self.assertEqual(coe_gate.count_refusals(self.store_path), 0)

    def test_check_refusal_via_main_is_logged_and_countable(self):
        argv = [
            "check", "--row-id", "BUILD-1", "--selection-row-id", "SEL-1",
            "--store", self.store_path,
        ]
        out, err = io.StringIO(), io.StringIO()
        with redirect_stdout(out), redirect_stderr(err):
            exit_code = coe_gate.main(argv)
        self.assertEqual(exit_code, 1)
        self.assertEqual(coe_gate.count_refusals(self.store_path), 1)

        with redirect_stdout(out), redirect_stderr(err):
            coe_gate.main(argv)
        self.assertEqual(coe_gate.count_refusals(self.store_path), 2)

    def test_allowed_check_via_main_exits_zero_and_does_not_add_a_refusal(self):
        coe_gate.close_selection(
            "SEL-1", _loop_result(coe_loop.OUTCOME_PASS), store_path=self.store_path
        )
        argv = [
            "check", "--row-id", "BUILD-1", "--selection-row-id", "SEL-1",
            "--store", self.store_path,
        ]
        out, err = io.StringIO(), io.StringIO()
        with redirect_stdout(out), redirect_stderr(err):
            exit_code = coe_gate.main(argv)
        self.assertEqual(exit_code, 0)
        self.assertEqual(coe_gate.count_refusals(self.store_path), 0)


class MainCliRoundTrip(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.store_path = os.path.join(self._tmp.name, "selections.json")

    def tearDown(self):
        self._tmp.cleanup()

    def test_close_then_status_via_cli(self):
        close_argv = [
            "close", "--row-id", "SEL-1", "--store", self.store_path,
            "--outcome", "PASS", "--final-score", "9",
            "--capping-criterion", "C3",
        ]
        out = io.StringIO()
        with redirect_stdout(out):
            code = coe_gate.main(close_argv)
        self.assertEqual(code, 0)

        status_argv = ["status", "--row-id", "SEL-1", "--store", self.store_path]
        out2 = io.StringIO()
        with redirect_stdout(out2):
            code2 = coe_gate.main(status_argv)
        self.assertEqual(code2, 0)
        payload = json.loads(out2.getvalue())
        self.assertEqual(payload["outcome"], "PASS")

    def test_check_then_open_via_cli_after_ceiling_decision(self):
        coe_gate.close_selection(
            "SEL-1", _loop_result(coe_loop.OUTCOME_CEILING, final_score=6),
            store_path=self.store_path,
        )
        argv = [
            "check", "--row-id", "BUILD-1", "--selection-row-id", "SEL-1",
            "--store", self.store_path,
            "--ceiling-owner", "Khalil", "--ceiling-reason", "acceptable",
        ]
        out = io.StringIO()
        with redirect_stdout(out):
            code = coe_gate.main(argv)
        self.assertEqual(code, 0)
        self.assertIn("OPEN", out.getvalue())


if __name__ == "__main__":
    unittest.main()

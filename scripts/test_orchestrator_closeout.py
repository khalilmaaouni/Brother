#!/usr/bin/env python3
"""Tests for scripts/orchestrator_closeout.py. Plain unittest, no sleeps,
no real clock: every `now` is a literal string handed in by the test.

Run: python3 scripts/test_orchestrator_closeout.py -v
"""

import copy
import json
import os
import shutil
import tempfile
import unittest

import orchestrator_closeout as closeout
import orchestrator_invariants as invariants

NOW = "2026-09-19T00:00:00+00:00"


def _base_run_state():
    """A realistic, fully valid fixture: two DONE units, one PARKED (with
    a next_action), one AWAITING-HUMAN (with a next_action), one required
    check that FAILs and one required check that reads NO-DATA, one
    optional NO-DATA check, one queued RED item, one provisional decision.
    Deliberately mixed so a bug that swaps or collapses any one field
    cannot hide behind an all-zero or all-equal fixture."""
    return {
        "run_id": "night-2026-09-18",
        "goal": "land the ORCH-1020 control plane units",
        "base_revision": "61a4a4d451f4ac599179184401fa734a1db8bcf5",
        "final_revision": "aa11bb22cc33dd44ee55ff66aa77bb88cc99dd00",
        "hard_stop_reached": True,
        "final_required_gate_verdict": "FAIL",
        "units_planned": 4,
        "units": [
            {"id": "ORCH-01", "state": "DONE", "task_class": "implementation"},
            {"id": "ORCH-02", "state": "DONE", "task_class": "test",
             "added_by_replan": True},
            {"id": "ORCH-03", "state": "PARKED", "task_class": "implementation",
             "blocking_reason": "depends on ORCH-02's schema, which changed "
                                "mid-run", "attempted": "two dispatches, both "
                                "timed out", "failure_detail": "worker never "
                                "returned before the drain window opened",
             "evidence_summary": "attempt logs under run/attempts/ORCH-03",
             "next_action": "re-dispatch ORCH-03 fresh against the new schema"},
            {"id": "ORCH-04", "state": "AWAITING-HUMAN", "task_class": "review",
             "blocking_reason": "risk_class high, no default acceptor",
             "next_action": "founder reviews the diff and accepts or rejects"},
        ],
        "throughput": {
            "successful_integrations": 2,
            "repair_attempts": 3,
            "replans": 1,
            "handoffs": 2,
            "provider_fallbacks": 1,
            "cross_reviews": 4,
        },
        "evidence_checks": [
            {"name": "required_fast.sh", "verdict": "FAIL",
             "obligation": "REQUIRED_FOR_MERGE"},
            {"name": "release_soak", "verdict": "NO-DATA",
             "obligation": "REQUIRED_FOR_RELEASE"},
            {"name": "coverage_report", "verdict": "NO-DATA",
             "obligation": "OPTIONAL"},
        ],
        "provisional_decisions": [
            {"decision": "kept ORCH-03 on the old worktree lane",
             "made_by": "session orch05", "flip_condition": "if ORCH-03 "
                        "still fails after re-dispatch"},
        ],
        "red_queue": [
            {"id": "RED-1", "description": "ORCH-03 timed out twice"},
        ],
        "awaiting_human": [
            {"question": "accept or reject ORCH-04's diff",
             "owner": "founder"},
        ],
    }


def _write_and_load(run_state, tmp_dir, now=NOW):
    out_dir = os.path.join(tmp_dir, "out")
    result = closeout.close_out(run_state, out_dir=out_dir, now=now)
    with open(result.morning_handoff_path, encoding="utf-8") as fh:
        markdown = fh.read()
    with open(result.final_state_path, encoding="utf-8") as fh:
        final_state = json.load(fh)
    return result, markdown, final_state, out_dir


class ValidationTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_valid_fixture_succeeds(self):
        result, _md, _fs, _out = _write_and_load(_base_run_state(), self.tmp)
        self.assertEqual(result.run_id, "night-2026-09-18")

    def test_not_a_dict_raises(self):
        with self.assertRaises(closeout.CloseoutError):
            closeout.close_out("not a dict", out_dir=self.tmp, now=NOW)

    def test_missing_required_field_raises(self):
        rs = _base_run_state()
        del rs["base_revision"]
        with self.assertRaises(closeout.CloseoutError):
            closeout.close_out(rs, out_dir=self.tmp, now=NOW)

    def test_unknown_unit_state_raises(self):
        rs = _base_run_state()
        rs["units"][0]["state"] = "SLEEPING"
        with self.assertRaises(closeout.CloseoutError):
            closeout.close_out(rs, out_dir=self.tmp, now=NOW)

    def test_unknown_task_class_raises(self):
        rs = _base_run_state()
        rs["units"][0]["task_class"] = "vibes"
        with self.assertRaises(closeout.CloseoutError):
            closeout.close_out(rs, out_dir=self.tmp, now=NOW)

    def test_non_done_unit_with_no_next_action_raises(self):
        rs = _base_run_state()
        del rs["units"][2]["next_action"]
        with self.assertRaises(closeout.CloseoutError):
            closeout.close_out(rs, out_dir=self.tmp, now=NOW)

    def test_done_unit_needs_no_next_action(self):
        rs = _base_run_state()
        self.assertNotIn("next_action", rs["units"][0])
        _write_and_load(rs, self.tmp)  # must not raise

    def test_unknown_evidence_verdict_raises(self):
        rs = _base_run_state()
        rs["evidence_checks"][0]["verdict"] = "MOSTLY-FINE"
        with self.assertRaises(closeout.CloseoutError):
            closeout.close_out(rs, out_dir=self.tmp, now=NOW)

    def test_unknown_evidence_obligation_raises(self):
        rs = _base_run_state()
        rs["evidence_checks"][0]["obligation"] = "REQUIRED_SOMETIMES"
        with self.assertRaises(closeout.CloseoutError):
            closeout.close_out(rs, out_dir=self.tmp, now=NOW)

    def test_unknown_gate_verdict_raises(self):
        rs = _base_run_state()
        rs["final_required_gate_verdict"] = "PROBABLY"
        with self.assertRaises(closeout.CloseoutError):
            closeout.close_out(rs, out_dir=self.tmp, now=NOW)

    def test_duplicate_unit_id_raises(self):
        rs = _base_run_state()
        rs["units"].append(dict(rs["units"][0]))
        with self.assertRaises(closeout.CloseoutError):
            closeout.close_out(rs, out_dir=self.tmp, now=NOW)

    def test_missing_throughput_field_raises(self):
        rs = _base_run_state()
        del rs["throughput"]["cross_reviews"]
        with self.assertRaises(closeout.CloseoutError):
            closeout.close_out(rs, out_dir=self.tmp, now=NOW)

    def test_red_queue_item_without_id_raises(self):
        rs = _base_run_state()
        del rs["red_queue"][0]["id"]
        with self.assertRaises(closeout.CloseoutError):
            closeout.close_out(rs, out_dir=self.tmp, now=NOW)

    def test_writes_nothing_on_a_raise(self):
        rs = _base_run_state()
        rs["units"][0]["state"] = "SLEEPING"
        out_dir = os.path.join(self.tmp, "clean")
        with self.assertRaises(closeout.CloseoutError):
            closeout.close_out(rs, out_dir=out_dir, now=NOW)
        self.assertFalse(os.path.exists(out_dir))


class VerdictAndCountsTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_required_fail_and_required_nodata_force_red(self):
        result, _md, fs, _out = _write_and_load(_base_run_state(), self.tmp)
        self.assertEqual(result.overall_verdict, "RED")
        self.assertEqual(fs["overall_verdict"], "RED")

    def test_all_required_pass_and_no_anomalies_is_green(self):
        rs = _base_run_state()
        for unit in rs["units"]:
            unit["state"] = "DONE"
            unit.pop("next_action", None)
        rs["evidence_checks"] = [
            {"name": "required_fast.sh", "verdict": "PASS",
             "obligation": "REQUIRED_FOR_MERGE"},
        ]
        rs["final_required_gate_verdict"] = "PASS"
        result, _md, fs, _out = _write_and_load(rs, self.tmp)
        self.assertEqual(result.overall_verdict, "GREEN")
        self.assertEqual(fs["overall_verdict"], "GREEN")

    def test_required_release_nodata_still_forces_away_from_green_at_merge_stage(self):
        # This module judges at "release" (a morning closeout is read as
        # the final word), so REQUIRED_FOR_RELEASE + NO-DATA is red here
        # even though invariants.may_proceed allows it at stage "merge".
        rs = _base_run_state()
        for unit in rs["units"]:
            unit["state"] = "DONE"
            unit.pop("next_action", None)
        rs["evidence_checks"] = [
            {"name": "release_soak", "verdict": "NO-DATA",
             "obligation": "REQUIRED_FOR_RELEASE"},
        ]
        rs["final_required_gate_verdict"] = "PASS"
        result, _md, _fs, _out = _write_and_load(rs, self.tmp)
        self.assertEqual(result.overall_verdict, "RED")

    def test_the_four_evidence_counts_are_never_summed(self):
        """Rule 2, made concrete: a fixture where the four counts are all
        different numbers, and each one is checked against its own exact
        value, not against a total. A bug that added required_pass and
        required_fail together (or dropped optional_nodata) would fail
        this test even though "some number came out"."""
        rs = _base_run_state()
        rs["evidence_checks"] = [
            {"name": "a", "verdict": "PASS", "obligation": "REQUIRED_FOR_MERGE"},
            {"name": "b", "verdict": "PASS", "obligation": "REQUIRED_FOR_MERGE"},
            {"name": "c", "verdict": "PASS", "obligation": "REQUIRED_FOR_MERGE"},
            {"name": "d", "verdict": "FAIL", "obligation": "REQUIRED_FOR_MERGE"},
            {"name": "e", "verdict": "NO-DATA", "obligation": "REQUIRED_FOR_RELEASE"},
            {"name": "f", "verdict": "NO-DATA", "obligation": "REQUIRED_FOR_RELEASE"},
            {"name": "g", "verdict": "NO-DATA", "obligation": "OPTIONAL"},
        ]
        _result, _md, fs, _out = _write_and_load(rs, self.tmp)
        ev = fs["evidence"]
        self.assertEqual(ev["required_pass"], 3)
        self.assertEqual(ev["required_fail"], 1)
        self.assertEqual(ev["required_nodata"], 2)
        self.assertEqual(ev["optional_nodata"], 1)
        # and never accidentally equal to each other by a lazy shared bug
        self.assertNotEqual(ev["required_pass"], ev["required_nodata"])

    def test_anomaly_forces_red_even_with_all_checks_passing(self):
        rs = _base_run_state()
        rs["evidence_checks"] = [
            {"name": "required_fast.sh", "verdict": "PASS",
             "obligation": "REQUIRED_FOR_MERGE"},
        ]
        rs["final_required_gate_verdict"] = "PASS"
        rs["units"][2]["state"] = "RUNNING"  # non-terminal: the drain missed it
        result, md, fs, _out = _write_and_load(rs, self.tmp)
        self.assertEqual(result.overall_verdict, "RED")
        self.assertIn("ORCH-03", result.anomalies)
        self.assertIn("ANOMALY", md)
        self.assertEqual(fs["anomalies"], ["ORCH-03"])


class MutationSensitivityTests(unittest.TestCase):
    """Rule 4, the unit's headline: the closeout must visibly change (or
    the call must fail) when one true fact about the night changes. Each
    test renders the base fixture, mutates exactly one fact, re-renders,
    and asserts the two outputs differ."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp()

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _render(self, run_state, label):
        out_dir = os.path.join(self.tmp, label)
        result = closeout.close_out(run_state, out_dir=out_dir, now=NOW)
        with open(result.morning_handoff_path, encoding="utf-8") as fh:
            md = fh.read()
        return result, md

    def test_removing_a_failed_unit_changes_the_closeout(self):
        base = _base_run_state()
        base_result, base_md = self._render(base, "base")

        mutated = _base_run_state()
        mutated["units"] = [u for u in mutated["units"] if u["id"] != "ORCH-03"]
        mutated_result, mutated_md = self._render(mutated, "mutated")

        self.assertNotEqual(base_md, mutated_md)
        self.assertNotEqual(base_result.final_state["work"],
                            mutated_result.final_state["work"])
        self.assertNotEqual(base_result.final_state["problems"],
                            mutated_result.final_state["problems"])

    def test_removing_a_red_queue_entry_changes_the_closeout(self):
        base = _base_run_state()
        _base_result, base_md = self._render(base, "base")

        mutated = _base_run_state()
        mutated["red_queue"] = []
        _mutated_result, mutated_md = self._render(mutated, "mutated")

        self.assertNotEqual(base_md, mutated_md)
        self.assertIn("RED-1", base_md)
        self.assertNotIn("RED-1", mutated_md)

    def test_altering_the_final_head_changes_the_closeout(self):
        base = _base_run_state()
        base_result, base_md = self._render(base, "base")

        mutated = _base_run_state()
        mutated["final_revision"] = "0000000000000000000000000000000000000000"
        mutated_result, mutated_md = self._render(mutated, "mutated")

        self.assertNotEqual(base_md, mutated_md)
        self.assertNotEqual(base_result.final_state["final_revision"],
                            mutated_result.final_state["final_revision"])

    def test_a_report_that_never_changes_would_be_decoration_not_evidence(self):
        """The bad state named in the module docstring, made explicit: two
        DIFFERENT run_states must never produce the SAME final_state (apart
        from the fields both fixtures happen to share). This is the test
        that would catch a closeout that only renders headings."""
        rs_a = _base_run_state()
        rs_b = _base_run_state()
        rs_b["units"][2]["state"] = "EXHAUSTED"
        rs_b["units"][2]["next_action"] = "give up: exhausted after re-plan"
        result_a, _ = self._render(rs_a, "a")
        result_b, _ = self._render(rs_b, "b")
        self.assertNotEqual(result_a.final_state["work"],
                            result_b.final_state["work"])


class DeterminismTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_identical_input_gives_byte_identical_output_apart_from_stamp(self):
        rs1 = _base_run_state()
        rs2 = _base_run_state()
        result1 = closeout.close_out(
            rs1, out_dir=os.path.join(self.tmp, "run1"), now="2026-09-19T01:00:00+00:00")
        result2 = closeout.close_out(
            rs2, out_dir=os.path.join(self.tmp, "run2"), now="2026-09-19T02:00:00+00:00")
        with open(result1.morning_handoff_path, encoding="utf-8") as fh:
            md1 = fh.read()
        with open(result2.morning_handoff_path, encoding="utf-8") as fh:
            md2 = fh.read()
        strip = lambda text: "\n".join(
            line for line in text.splitlines() if not line.startswith("generated at:"))
        self.assertEqual(strip(md1), strip(md2))
        self.assertNotEqual(md1, md2)  # the stamp itself really did differ

        fs1 = dict(result1.final_state)
        fs2 = dict(result2.final_state)
        fs1.pop("generated_at")
        fs2.pop("generated_at")
        self.assertEqual(fs1, fs2)

    def test_same_call_twice_over_same_input_is_stable(self):
        rs = _base_run_state()
        result = closeout.close_out(
            rs, out_dir=os.path.join(self.tmp, "runA"), now=NOW)
        again = closeout.close_out(
            copy.deepcopy(rs), out_dir=os.path.join(self.tmp, "runA"), now=NOW)
        with open(result.morning_handoff_path, encoding="utf-8") as fh:
            first = fh.read()
        with open(again.morning_handoff_path, encoding="utf-8") as fh:
            second = fh.read()
        self.assertEqual(first, second)


class EdgeCaseTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_zero_units(self):
        rs = _base_run_state()
        rs["units"] = []
        rs["units_planned"] = 0
        rs["evidence_checks"] = []
        rs["red_queue"] = []
        rs["provisional_decisions"] = []
        rs["awaiting_human"] = []
        rs["final_required_gate_verdict"] = "NO-DATA"
        result, md, fs, _out = _write_and_load(rs, self.tmp)
        self.assertEqual(fs["work"]["done"], 0)
        self.assertIn("none", md)

    def test_every_unit_done(self):
        rs = _base_run_state()
        for unit in rs["units"]:
            unit["state"] = "DONE"
            unit.pop("next_action", None)
        result, md, fs, _out = _write_and_load(rs, self.tmp)
        self.assertEqual(fs["work"]["done"], 4)
        self.assertIn("none: every unit closed DONE", md)

    def test_every_unit_parked(self):
        rs = _base_run_state()
        for unit in rs["units"]:
            unit["state"] = "PARKED"
            unit["next_action"] = "resume %s next run" % unit["id"]
        result, md, fs, _out = _write_and_load(rs, self.tmp)
        self.assertEqual(fs["work"]["parked"], 4)
        self.assertEqual(fs["work"]["done"], 0)

    def test_no_hard_stop_recorded(self):
        rs = _base_run_state()
        rs["hard_stop_reached"] = None
        result, md, fs, _out = _write_and_load(rs, self.tmp)
        self.assertIsNone(fs["hard_stop_reached"])
        self.assertIn("no hard stop was recorded", md)

    def test_final_equals_base_revision_reports_nothing_shipped_plainly(self):
        rs = _base_run_state()
        rs["final_revision"] = rs["base_revision"]
        result, md, fs, _out = _write_and_load(rs, self.tmp)
        self.assertTrue(fs["shipped"] is False)
        self.assertIn("nothing shipped this run", md)

    def test_unreadable_run_state_raises_not_a_partial_document(self):
        with self.assertRaises(closeout.CloseoutError):
            closeout.close_out(None, out_dir=os.path.join(self.tmp, "x"), now=NOW)

    def test_unit_with_state_not_in_task_states_raises(self):
        rs = _base_run_state()
        rs["units"][0]["state"] = "VIBING"
        with self.assertRaises(closeout.CloseoutError):
            closeout.close_out(rs, out_dir=self.tmp, now=NOW)

    def test_out_dir_already_holding_a_different_runs_closeout_raises(self):
        rs = _base_run_state()
        out_dir = os.path.join(self.tmp, "shared")
        closeout.close_out(rs, out_dir=out_dir, now=NOW)

        other = _base_run_state()
        other["run_id"] = "a-different-night"
        with self.assertRaises(closeout.CloseoutError):
            closeout.close_out(other, out_dir=out_dir, now=NOW)

    def test_out_dir_already_holding_the_same_runs_closeout_is_idempotent(self):
        rs = _base_run_state()
        out_dir = os.path.join(self.tmp, "shared2")
        first = closeout.close_out(rs, out_dir=out_dir, now=NOW)
        second = closeout.close_out(copy.deepcopy(rs), out_dir=out_dir, now=NOW)
        self.assertEqual(first.final_state, second.final_state)

    def test_every_task_state_is_a_real_member_of_the_invariants_module(self):
        # Guards against this test file itself drifting from
        # orchestrator_invariants if that module's vocabulary changes.
        rs = _base_run_state()
        for unit in rs["units"]:
            self.assertIn(unit["state"], invariants.TASK_STATES)


class DeliveryEvidenceReuseTests(unittest.TestCase):
    """Proves the reuse claim in the module docstring: when delivery_
    evidence is supplied, close_out() actually calls brother_run.
    build_report (never a second reporter), and the resulting text lands
    in the rendered document. When it is absent, the section reads
    NO-DATA rather than being silently blank."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp()

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_no_delivery_evidence_reads_no_data(self):
        rs = _base_run_state()
        self.assertNotIn("delivery_evidence", rs)
        _result, md, fs, _out = _write_and_load(rs, self.tmp)
        self.assertIn("no delivery evidence was supplied", md)
        self.assertIsNone(fs["evidence"]["receipt_path"])

    def test_delivery_evidence_embeds_the_real_build_report_text(self):
        rs = _base_run_state()
        rs["delivery_evidence"] = {
            "record": {
                "outcome": "ORCH-1020 overnight",
                "work_id": "W-night-2026-09-18",
                "rows": [{"id": "ORCH-01", "status": "DONE",
                         "done_check": "python3 scripts/test_x.py"}],
            },
            "claims": {"ORCH-01": {"state": "done",
                                   "evidence": {"exit_code": 0}}},
            "before": rs["base_revision"],
            "after": rs["final_revision"],
        }
        _result, md, _fs, _out = _write_and_load(rs, self.tmp)
        # brother_run.build_report's own, unmistakable words: proof this
        # module quoted the real function rather than writing its own.
        self.assertIn("brother_run: delivery report for", md)
        self.assertIn("W-night-2026-09-18", md)

    def test_delivery_evidence_failure_degrades_without_crashing_the_closeout(self):
        rs = _base_run_state()
        rs["delivery_evidence"] = {
            "record": None,  # malformed on purpose: record.get() will raise
            "claims": {},
            "before": "a",
            "after": "b",
        }
        result, md, fs, _out = _write_and_load(rs, self.tmp)
        self.assertIsNotNone(result)  # did not raise
        self.assertIn(closeout.NODATA, md)
        self.assertIsNone(fs["evidence"]["receipt_path"])

    def test_receipt_run_dir_writes_a_real_receipt_file(self):
        rs = _base_run_state()
        receipt_run_dir = os.path.join(self.tmp, "receipt_home")
        os.makedirs(receipt_run_dir, exist_ok=True)
        rs["delivery_evidence"] = {
            "record": {
                "outcome": "ORCH-1020 overnight",
                "work_id": "W-night-2026-09-18",
                "rows": [{"id": "ORCH-01", "status": "DONE",
                         "done_check": "python3 scripts/test_x.py"}],
            },
            "claims": {"ORCH-01": {"state": "done",
                                   "evidence": {"exit_code": 0}}},
            "before": rs["base_revision"],
            "after": rs["final_revision"],
        }
        rs["receipt_run_dir"] = receipt_run_dir
        result, _md, fs, _out = _write_and_load(rs, self.tmp)
        # Either a real receipt landed, or this environment's receipt_door
        # honestly degraded with a stated reason: both are acceptable
        # here (this sub-block is never load-bearing for the two required
        # artifacts), but silence is not.
        if result.receipt_path is not None:
            self.assertTrue(os.path.isfile(result.receipt_path))
            self.assertEqual(fs["evidence"]["receipt_path"], result.receipt_path)
        else:
            self.assertTrue(fs["evidence"]["receipt_note"])


if __name__ == "__main__":
    unittest.main()

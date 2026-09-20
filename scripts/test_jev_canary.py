#!/usr/bin/env python3
"""Tests for jev_canary.py. No network: every test monkeypatches
jev_decide.decide directly (jev_canary calls it module-qualified, so
patching jev_decide.decide affects jev_canary's call too), and never
touches a real bridge subprocess."""
import json
import os
import shutil
import sys
import tempfile
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import jev_canary
import jev_decide

PINNED = jev_canary.PINNED_MODEL
OTHER_MODEL = "typesafe/jev-1.14-20261001"

# A small synthetic dataset shaped exactly like jev_eval.items() expects:
# A and B are "noul" tasks (boolean truth), D is "choice" task (a label
# truth). C_routing is present but empty so jev_eval.items() never tries
# to read a spec file for it (matches the real dataset's shape enough for
# _golden_items' own trimming to be exercised, not just relied on).
DATASET = {
    "A_mutation_triage": {
        "instructions": "Would this mutation be caught?",
        "true": "caught", "false": "not caught",
        "items": [
            {"id": "A%d" % i, "caught": (i % 2 == 0), "rule": "rule %d" % i,
             "tests": ["t%d" % i], "mutation": "mutation %d" % i}
            for i in range(1, 7)
        ],
    },
    "B_unknown_reads_safe": {
        "instructions": "Is an unknown read unsafe here?",
        "true": "unsafe", "false": "safe",
        "items": [
            {"id": "B%d" % i, "unsafe": (i % 2 == 0), "rule": "rule %d" % i}
            for i in range(1, 7)
        ],
    },
    "C_routing": {
        "instructions": "route it", "criteria": {"x": "x"}, "spec_dir": "specs", "labels": {},
    },
    "D_log_triage": {
        "instructions": "Triage this log line.",
        "criteria": {"blocking": "a gate failed", "info": "a normal pass"},
        "items": [
            {"id": "D%d" % i, "label": "blocking" if i % 2 == 0 else "info", "line": "line %d" % i}
            for i in range(1, 7)
        ],
    },
}
DATASET_DIR = "/nonexistent"  # never touched: C has no items, A/B/D need no files


def _golden_and_truths():
    golden = jev_canary._golden_items(DATASET, DATASET_DIR)
    truths = dict((iid, truth) for _t, iid, truth, _s, _q in golden)
    return golden, truths


def _correct_fake_decide(golden, model=PINNED, noul_margin=0.4):
    """A fake jev_decide.decide() that answers every golden item
    correctly, reporting `model` for every record. `noul_margin` sets how
    confident (away from 0.5) the noul answers are, so tests can dial
    confidence without touching correctness."""
    def fake(state, questions, family, *, bridge=None, runner=None):
        records = []
        for _task, iid, truth, _state, q in golden:
            key = iid.replace(".", "_").replace("-", "_")
            qtype = q["type"]
            if qtype == "noul":
                answer = (0.5 + noul_margin) if truth else (0.5 - noul_margin)
                probability = answer
            else:
                answer = truth
                probability = 0.5 + noul_margin
            records.append({
                "id": key, "family": family, "type": qtype, "framing_hash": "x",
                "answer": answer, "probability": probability, "confidence": None,
                "model": model, "cost_share": None, "latency_seconds": 0.01,
            })
        return records
    return fake


def _wrong_fake_decide(golden, model=PINNED, confidence=0.9):
    """A fake jev_decide.decide() that answers every golden item WRONG,
    with a stated confidence in that (wrong) answer."""
    def fake(state, questions, family, *, bridge=None, runner=None):
        records = []
        for _task, iid, truth, _state, q in golden:
            key = iid.replace(".", "_").replace("-", "_")
            qtype = q["type"]
            if qtype == "noul":
                answer = (1 - confidence) if truth else confidence
                probability = answer
            else:
                wrong_choices = [c for c in q["criteria"] if c != truth]
                answer = wrong_choices[0]
                probability = confidence
            records.append({
                "id": key, "family": family, "type": qtype, "framing_hash": "x",
                "answer": answer, "probability": probability, "confidence": None,
                "model": model, "cost_share": None, "latency_seconds": 0.01,
            })
        return records
    return fake


class JevCanaryTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.dataset_path = os.path.join(self.tmp, "dataset.json")
        with open(self.dataset_path, "w", encoding="utf-8") as fh:
            json.dump(DATASET, fh)
        self.baseline_path = os.path.join(self.tmp, "baseline.json")
        self.reset_marker_path = os.path.join(self.tmp, "reset.json")
        self.state_path = os.path.join(self.tmp, "state.json")
        self.golden, self.truths = _golden_and_truths()

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _write_baseline(self, accuracy, brier=None):
        payload = {"accuracy": accuracy, "n": len(self.golden), "model": PINNED}
        if brier is not None:
            payload["brier"] = brier
        with open(self.baseline_path, "w", encoding="utf-8") as fh:
            json.dump(payload, fh)

    def _run(self, fake_decide):
        with mock.patch.object(jev_decide, "decide", fake_decide):
            return jev_canary.run_canary(
                self.dataset_path, self.baseline_path,
                reset_marker_path=self.reset_marker_path, state_path=self.state_path)

    # -- all-correct pinned run passes ------------------------------------

    def test_all_correct_pinned_run_passes(self):
        self._write_baseline(1.0)
        code, message = self._run(_correct_fake_decide(self.golden))
        self.assertEqual(jev_canary.EXIT_PASS, code, message)
        self.assertIn("PASS", message)
        self.assertFalse(os.path.exists(self.reset_marker_path))

    # -- one answer from a different model id fails and writes the marker,
    #    immediately (no consecutive-run rule for model id) -----------------

    def test_model_drift_fails_immediately_and_writes_reset_marker(self):
        self._write_baseline(1.0)
        fake = _correct_fake_decide(self.golden)

        def drifted(state, questions, family, *, bridge=None, runner=None):
            records = fake(state, questions, family, bridge=bridge, runner=runner)
            records[0] = dict(records[0], model=OTHER_MODEL)
            return records

        code, message = self._run(drifted)
        self.assertEqual(jev_canary.EXIT_FAIL, code, message)
        self.assertIn("model id changed", message)
        self.assertTrue(os.path.exists(self.reset_marker_path))
        with open(self.reset_marker_path, encoding="utf-8") as fh:
            marker = json.load(fh)
        self.assertIn("model id changed", marker["reason"])
        self.assertIn(OTHER_MODEL, marker["reason"])
        self.assertIn("time", marker)
        self.assertEqual(PINNED, marker["pinned_model"])
        self.assertIn(OTHER_MODEL, marker["models_seen"])

    # -- RULE 1 (NOISE): a single below-threshold run does NOT fail --------

    def test_single_accuracy_dip_does_not_fail_immediately(self):
        self._write_baseline(0.95)  # tolerance default 0.05, floor is 0.90
        code, message = self._run(_wrong_fake_decide(self.golden))
        self.assertEqual(jev_canary.EXIT_PASS, code, message)
        self.assertIn("accuracy below threshold this run (1/2 consecutive", message)
        self.assertFalse(os.path.exists(self.reset_marker_path))

    # -- RULE 1 (NOISE): two CONSECUTIVE below-threshold runs do fail ------

    def test_two_consecutive_accuracy_dips_fail(self):
        self._write_baseline(0.95)
        first_code, first_message = self._run(_wrong_fake_decide(self.golden))
        self.assertEqual(jev_canary.EXIT_PASS, first_code, first_message)
        second_code, second_message = self._run(_wrong_fake_decide(self.golden))
        self.assertEqual(jev_canary.EXIT_FAIL, second_code, second_message)
        self.assertIn("accuracy dropped 2 consecutive runs", second_message)
        self.assertTrue(os.path.exists(self.reset_marker_path))
        with open(self.reset_marker_path, encoding="utf-8") as fh:
            marker = json.load(fh)
        self.assertIn("accuracy dropped 2 consecutive runs", marker["reason"])
        self.assertEqual(PINNED, marker["pinned_model"])

    # -- RULE 1 (NOISE): a good run in between resets the streak ------------

    def test_a_good_run_resets_the_accuracy_streak(self):
        self._write_baseline(0.95)
        bad_code, _msg = self._run(_wrong_fake_decide(self.golden))
        self.assertEqual(jev_canary.EXIT_PASS, bad_code)
        good_code, _msg = self._run(_correct_fake_decide(self.golden))
        self.assertEqual(jev_canary.EXIT_PASS, good_code)
        # A third bad run should be "first strike" again, not "second".
        third_code, third_message = self._run(_wrong_fake_decide(self.golden))
        self.assertEqual(jev_canary.EXIT_PASS, third_code, third_message)
        self.assertIn("1/2 consecutive", third_message)
        self.assertFalse(os.path.exists(self.reset_marker_path))

    # -- RULE 2 (CALIBRATION): Brier worsening over two consecutive runs
    #    fails even while accuracy holds -----------------------------------

    def test_brier_worsening_two_consecutive_runs_fails_while_accuracy_holds(self):
        # Baseline: correct and well-calibrated (confident and right).
        confident_fake = _correct_fake_decide(self.golden, noul_margin=0.45)
        with mock.patch.object(jev_decide, "decide", confident_fake):
            record_code, record_message = jev_canary.run_canary(
                self.dataset_path, self.baseline_path, reset_marker_path=self.reset_marker_path,
                state_path=self.state_path, record_baseline=True)
        self.assertEqual(jev_canary.EXIT_PASS, record_code, record_message)
        with open(self.baseline_path, encoding="utf-8") as fh:
            recorded = json.load(fh)
        self.assertIn("brier", recorded)
        self.assertLess(recorded["brier"], 0.05)  # confident and correct: near-zero Brier

        # Now Jev is still just as ACCURATE, but far less confident about
        # its (still correct) answers: Brier worsens with accuracy
        # unchanged, exactly the case accuracy alone cannot see.
        overcautious = _correct_fake_decide(self.golden, noul_margin=0.02)
        first_code, first_message = self._run(overcautious)
        self.assertEqual(jev_canary.EXIT_PASS, first_code, first_message)
        self.assertIn("Brier worse this run (1/2", first_message)
        second_code, second_message = self._run(overcautious)
        self.assertEqual(jev_canary.EXIT_FAIL, second_code, second_message)
        self.assertIn("Brier worsened 2 consecutive runs", second_message)
        self.assertTrue(os.path.exists(self.reset_marker_path))

    def test_calibration_check_skipped_against_a_legacy_baseline_without_brier(self):
        self._write_baseline(0.0)  # no "brier" key at all, and a floor low
        # enough that accuracy alone cannot fail either, isolating this to
        # a pure "is brier even compared" check.
        code, message = self._run(_wrong_fake_decide(self.golden, confidence=0.99))
        self.assertEqual(jev_canary.EXIT_PASS, code, message)
        self.assertIn("baseline_brier=NO-DATA (legacy baseline)", message)
        self.assertNotIn("Brier worse", message)

    # -- RULE 3 (NO-DATA): a single NO-DATA stays exit 2, no baseline touch

    def test_no_data_from_decide_skips_baseline_write(self):
        def no_data(state, questions, family, *, bridge=None, runner=None):
            return (jev_decide.NO_DATA, "bridge exited 1: simulated failure")

        with mock.patch.object(jev_decide, "decide", no_data):
            code, message = jev_canary.run_canary(
                self.dataset_path, self.baseline_path, reset_marker_path=self.reset_marker_path,
                state_path=self.state_path, record_baseline=True)
        self.assertEqual(jev_canary.EXIT_NO_DATA, code, message)
        self.assertIn("NO-DATA", message)
        self.assertFalse(os.path.exists(self.baseline_path))

    # -- RULE 3 (NO-DATA): three consecutive NO-DATA runs escalate to FAIL -

    def test_three_consecutive_no_data_runs_escalate_to_fail(self):
        self._write_baseline(0.5)  # never reached: every run here is NO-DATA
        with open(self.baseline_path, encoding="utf-8") as fh:
            baseline_before = fh.read()

        def no_data(state, questions, family, *, bridge=None, runner=None):
            return (jev_decide.NO_DATA, "bridge exited 1: simulated failure")

        first_code, first_message = self._run(no_data)
        self.assertEqual(jev_canary.EXIT_NO_DATA, first_code, first_message)
        second_code, second_message = self._run(no_data)
        self.assertEqual(jev_canary.EXIT_NO_DATA, second_code, second_message)
        self.assertFalse(os.path.exists(self.reset_marker_path))
        third_code, third_message = self._run(no_data)
        self.assertEqual(jev_canary.EXIT_FAIL, third_code, third_message)
        self.assertIn("persistent NO-DATA", third_message)
        self.assertTrue(os.path.exists(self.reset_marker_path))
        with open(self.reset_marker_path, encoding="utf-8") as fh:
            marker = json.load(fh)
        self.assertIn("persistent NO-DATA", marker["reason"])
        # "never touches the baseline": the fixture's own file, byte for
        # byte, since nothing in a NO-DATA run may ever write it.
        with open(self.baseline_path, encoding="utf-8") as fh:
            baseline_after = fh.read()
        self.assertEqual(baseline_before, baseline_after)

    # -- RULE 4 (RESET MARKER): a later PASS never clears it, only
    #    --record-baseline does ---------------------------------------------

    def test_reset_marker_persists_through_a_later_pass_and_is_cleared_only_by_record_baseline(self):
        self._write_baseline(0.95)
        # Two consecutive bad runs: FAIL, marker written.
        self._run(_wrong_fake_decide(self.golden))
        fail_code, _msg = self._run(_wrong_fake_decide(self.golden))
        self.assertEqual(jev_canary.EXIT_FAIL, fail_code)
        self.assertTrue(os.path.exists(self.reset_marker_path))
        with open(self.reset_marker_path, encoding="utf-8") as fh:
            marker_before = json.load(fh)

        # A clean run afterwards is a PASS, but the marker is NOT cleared.
        pass_code, pass_message = self._run(_correct_fake_decide(self.golden))
        self.assertEqual(jev_canary.EXIT_PASS, pass_code, pass_message)
        self.assertTrue(os.path.exists(self.reset_marker_path))
        self.assertIn("reset marker still present", pass_message)
        with open(self.reset_marker_path, encoding="utf-8") as fh:
            marker_after = json.load(fh)
        self.assertEqual(marker_before, marker_after)  # untouched, not rewritten

        # Only --record-baseline clears it.
        with mock.patch.object(jev_decide, "decide", _correct_fake_decide(self.golden)):
            record_code, _msg = jev_canary.run_canary(
                self.dataset_path, self.baseline_path, reset_marker_path=self.reset_marker_path,
                state_path=self.state_path, record_baseline=True)
        self.assertEqual(jev_canary.EXIT_PASS, record_code)
        self.assertFalse(os.path.exists(self.reset_marker_path))

    def test_record_baseline_resets_run_history(self):
        self._write_baseline(0.95)
        self._run(_wrong_fake_decide(self.golden))  # one strike recorded
        with open(self.state_path, encoding="utf-8") as fh:
            self.assertEqual(1, json.load(fh)["consecutive_accuracy_below"])
        with mock.patch.object(jev_decide, "decide", _correct_fake_decide(self.golden)):
            jev_canary.run_canary(
                self.dataset_path, self.baseline_path, reset_marker_path=self.reset_marker_path,
                state_path=self.state_path, record_baseline=True)
        with open(self.state_path, encoding="utf-8") as fh:
            fresh = json.load(fh)
        self.assertEqual(0, fresh["consecutive_accuracy_below"])
        self.assertEqual(0, fresh["consecutive_brier_worse"])
        self.assertEqual(0, fresh["consecutive_no_data"])

    # -- state file is written atomically: no leftover temp files ----------

    def test_state_write_leaves_no_temp_file_behind(self):
        self._write_baseline(1.0)
        self._run(_correct_fake_decide(self.golden))
        leftovers = [f for f in os.listdir(self.tmp) if f.startswith(".jev_canary-")]
        self.assertEqual([], leftovers)

    # -- M1: with no --reset-marker, a FAIL writes exactly
    #    DEFAULT_RESET_MARKER (JEV_STATE_DIR-anchored -- item 1,
    #    approved-with-nits review 2026-09-18 -- the same path the seam
    #    resolves by contract), never a real data/ path -------------------

    def test_fail_with_no_reset_marker_flag_writes_default_path(self):
        fake_state_dir = os.path.join(self.tmp, "fake-state-dir")
        fake_default_marker = os.path.join(fake_state_dir, "canary-reset.json")
        os.makedirs(fake_state_dir)
        self._write_baseline(1.0)
        with mock.patch.object(jev_canary, "DEFAULT_RESET_MARKER", fake_default_marker):
            with mock.patch.object(jev_decide, "decide", _correct_fake_decide(self.golden, model="wrong-model")):
                code, message = jev_canary.run_canary(
                    self.dataset_path, self.baseline_path, state_path=self.state_path)
        self.assertEqual(jev_canary.EXIT_FAIL, code, message)
        self.assertTrue(os.path.exists(fake_default_marker))
        with open(fake_default_marker, encoding="utf-8") as fh:
            marker = json.load(fh)
        self.assertIn("model id changed", marker["reason"])

    def test_default_reset_marker_is_jev_state_dir_anchored(self):
        # Item 1 (approved-with-nits review 2026-09-18): moved off
        # REPO_ROOT (M1's original anchor) and onto JEV_STATE_DIR, so a
        # marker written from one checkout is still found from another.
        expected = os.path.join(jev_canary.JEV_STATE_DIR, "canary-reset.json")
        self.assertEqual(expected, jev_canary.DEFAULT_RESET_MARKER)

    def test_default_state_path_is_jev_state_dir_anchored(self):
        # Item 1: the run-history default no longer depends on
        # --baseline's own path (the old "<baseline>.state.json" scheme,
        # _default_state_path(), now removed) -- a fixed,
        # checkout-independent path under JEV_STATE_DIR instead.
        expected = os.path.join(jev_canary.JEV_STATE_DIR, "canary-state.json")
        self.assertEqual(expected, jev_canary.DEFAULT_STATE_PATH)

    def test_no_state_flag_uses_default_state_path(self):
        fake_state_dir = os.path.join(self.tmp, "fake-state-dir-2")
        fake_default_state = os.path.join(fake_state_dir, "canary-state.json")
        os.makedirs(fake_state_dir)
        fake_default_marker = os.path.join(fake_state_dir, "canary-reset.json")
        self._write_baseline(1.0)
        with mock.patch.object(jev_canary, "DEFAULT_STATE_PATH", fake_default_state), \
             mock.patch.object(jev_canary, "DEFAULT_RESET_MARKER", fake_default_marker), \
             mock.patch.object(jev_decide, "decide", _wrong_fake_decide(self.golden)):
            code, message = jev_canary.run_canary(self.dataset_path, self.baseline_path)
        self.assertEqual(jev_canary.EXIT_PASS, code, message)  # one bad run: not yet a FAIL
        self.assertTrue(os.path.exists(fake_default_state))
        with open(fake_default_state, encoding="utf-8") as fh:
            history = json.load(fh)
        self.assertEqual(1, history["consecutive_accuracy_below"])

    # -- M5: a scorable count well below the baseline's n can never PASS,
    #    and counts toward the three-NO-DATA rule ---------------------------

    def test_tiny_sample_is_no_data_not_pass(self):
        self._write_baseline(1.0)  # n = len(self.golden), e.g. 18-40

        def one_scorable(state, questions, family, *, bridge=None, runner=None):
            fake = _correct_fake_decide(self.golden)(state, questions, family, bridge=bridge, runner=runner)
            return fake[:1]  # only one golden item actually answered

        code, message = self._run(one_scorable)
        self.assertEqual(jev_canary.EXIT_NO_DATA, code, message)
        self.assertIn(jev_decide.NO_DATA, message)
        self.assertIn("only 1", message)
        self.assertFalse(os.path.exists(self.reset_marker_path))

    def test_tiny_sample_never_passes_even_when_perfectly_correct(self):
        self._write_baseline(0.0)  # a floor so low ordinary accuracy could never fail
        self.assertGreater(len(self.golden), 10)  # sanity: baseline n is not itself tiny

        def one_scorable(state, questions, family, *, bridge=None, runner=None):
            fake = _correct_fake_decide(self.golden)(state, questions, family, bridge=bridge, runner=runner)
            return fake[:1]

        code, message = self._run(one_scorable)
        self.assertNotEqual(jev_canary.EXIT_PASS, code, message)
        self.assertEqual(jev_canary.EXIT_NO_DATA, code, message)

    def test_three_consecutive_tiny_samples_escalate_to_fail(self):
        self._write_baseline(1.0)

        def one_scorable(state, questions, family, *, bridge=None, runner=None):
            fake = _correct_fake_decide(self.golden)(state, questions, family, bridge=bridge, runner=runner)
            return fake[:1]

        first_code, _msg = self._run(one_scorable)
        self.assertEqual(jev_canary.EXIT_NO_DATA, first_code)
        second_code, _msg = self._run(one_scorable)
        self.assertEqual(jev_canary.EXIT_NO_DATA, second_code)
        self.assertFalse(os.path.exists(self.reset_marker_path))
        third_code, third_message = self._run(one_scorable)
        self.assertEqual(jev_canary.EXIT_FAIL, third_code, third_message)
        self.assertIn("persistent NO-DATA", third_message)
        self.assertTrue(os.path.exists(self.reset_marker_path))

    def test_tiny_sample_skipped_when_baseline_has_no_n(self):
        # A hand-authored legacy baseline with no "n" key: nothing to
        # compare the sample size against, so M5 is skipped (not guessed)
        # exactly like the Brier legacy-skip discipline.
        payload = {"accuracy": 0.0, "model": PINNED}
        with open(self.baseline_path, "w", encoding="utf-8") as fh:
            json.dump(payload, fh)

        def one_scorable(state, questions, family, *, bridge=None, runner=None):
            fake = _correct_fake_decide(self.golden)(state, questions, family, bridge=bridge, runner=runner)
            return fake[:1]

        code, message = self._run(one_scorable)
        self.assertEqual(jev_canary.EXIT_PASS, code, message)

    # -- subset selection is deterministic across two runs ------------------

    def test_subset_selection_is_deterministic(self):
        first = jev_canary._golden_items(DATASET, DATASET_DIR)
        second = jev_canary._golden_items(DATASET, DATASET_DIR)
        self.assertEqual(first, second)
        # And every selected item actually comes from an included task.
        self.assertTrue(all(task in jev_canary.CANARY_TASKS for task, _i, _t, _s, _q in first))
        self.assertFalse(any(task == "C" for task, _i, _t, _s, _q in first))


class LedgerStatusTests(unittest.TestCase):
    """Item 1 (2026-09-19): check_ledger_reliability()/--ledger-status,
    the wiring that lets an operator (or a future board generator) call
    jev_calibration.ledger_reliability() the same PASS/FAIL way this
    module's own golden-set drift check already reports."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.dp = os.path.join(self.tmp, "decisions.jsonl")
        self.op = os.path.join(self.tmp, "outcomes.jsonl")
        self.reset_marker_path = os.path.join(self.tmp, "reset.json")

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_reliable_ledger_passes_and_writes_no_marker(self):
        import jev_calibration
        jev_calibration.append_decision(self.dp, {
            "id": "d1", "family": "f", "qtype": "noul", "framing": "h",
            "answer": True, "prob": 0.9, "confidence": 0.9, "model": "m",
            "cost": 0.0, "at": "2026-09-19T00:00:00Z",
        })
        code, message = jev_canary.check_ledger_reliability(
            self.dp, self.op, reset_marker_path=self.reset_marker_path)
        self.assertEqual(jev_canary.EXIT_PASS, code, message)
        self.assertIn("PASS", message)
        self.assertFalse(os.path.exists(self.reset_marker_path))

    def test_missing_ledger_passes_reliable_empty(self):
        code, message = jev_canary.check_ledger_reliability(
            self.dp, self.op, reset_marker_path=self.reset_marker_path)
        self.assertEqual(jev_canary.EXIT_PASS, code, message)

    def test_corrupt_ledger_fails_and_writes_the_reset_marker(self):
        with open(self.dp, "w", encoding="utf-8") as fh:
            fh.write("not json at all\n")
        code, message = jev_canary.check_ledger_reliability(
            self.dp, self.op, reset_marker_path=self.reset_marker_path)
        self.assertEqual(jev_canary.EXIT_FAIL, code, message)
        self.assertIn("FAIL", message)
        self.assertTrue(os.path.exists(self.reset_marker_path))
        with open(self.reset_marker_path, encoding="utf-8") as fh:
            marker = json.load(fh)
        self.assertIn("calibration ledger unreliable", marker["reason"])

    def test_cli_ledger_status_flag_runs_this_check_not_the_golden_set(self):
        code = jev_canary.main([
            "--ledger-status", "--decisions", self.dp, "--outcomes", self.op,
            "--reset-marker", self.reset_marker_path,
        ])
        self.assertEqual(jev_canary.EXIT_PASS, code)


if __name__ == "__main__":
    unittest.main()

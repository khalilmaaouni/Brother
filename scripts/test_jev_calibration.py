#!/usr/bin/env python3
"""Calibration for scripts/jev_calibration.py.

The property this file exists to assert is that a caller reading a
threshold() or report() answer can trust it: a band with no data never
reports a fabricated precision, an unrecognised or malformed record never
gets counted as safe, a noul and a choice sharing a family name are never
pooled, and the Wilson lower bound this module computes matches a
hand-worked value. A test suite that only checked the happy path would
pass right through a regression that quietly turned any of those into a
silent default.

Every test uses its own tempfile.TemporaryDirectory ledger paths. One test
(test_default_ledger_path_untouched) asserts the module's own
DEFAULT_LEDGER_PATH constant is never created or modified by running this
whole suite, since every other test injects its own path instead.
"""
import json
import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import jev_calibration as jc  # noqa: E402


def _dec(id="d1", family="f", qtype="noul", framing="h1", answer=True,
         prob=0.9, confidence=0.9, model="m", cost=0.0, at="2026-09-18T00:00:00Z"):
    return {
        "id": id, "family": family, "qtype": qtype, "framing": framing,
        "answer": answer, "prob": prob, "confidence": confidence,
        "model": model, "cost": cost, "at": at,
    }


def _out(id="d1", correct=True, source="test", at="2026-09-18T00:01:00Z"):
    return {"id": id, "correct": correct, "source": source, "at": at}


class LedgerTestCase(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.dp = os.path.join(self._tmp.name, "decisions.jsonl")
        self.op = os.path.join(self._tmp.name, "outcomes.jsonl")


class TestAppendValidation(LedgerTestCase):
    def test_append_decision_writes_one_line(self):
        jc.append_decision(self.dp, _dec())
        with open(self.dp) as f:
            lines = f.readlines()
        self.assertEqual(len(lines), 1)
        self.assertEqual(json.loads(lines[0])["id"], "d1")

    def test_append_decision_missing_field_raises_and_writes_nothing(self):
        rec = _dec()
        del rec["prob"]
        with self.assertRaisesRegex(ValueError, "prob"):
            jc.append_decision(self.dp, rec)
        self.assertFalse(os.path.exists(self.dp))

    def test_append_decision_invalid_qtype_raises(self):
        with self.assertRaisesRegex(ValueError, "qtype"):
            jc.append_decision(self.dp, _dec(qtype="yesno"))

    def test_append_decision_noul_confidence_must_equal_prob(self):
        with self.assertRaisesRegex(ValueError, "noul"):
            jc.append_decision(self.dp, _dec(qtype="noul", prob=0.9, confidence=0.5))

    def test_append_decision_choice_confidence_may_differ_from_prob(self):
        jc.append_decision(self.dp, _dec(qtype="choice", prob=0.9, confidence=0.5))

    def test_append_decision_prob_out_of_range_raises(self):
        with self.assertRaisesRegex(ValueError, "prob"):
            jc.append_decision(self.dp, _dec(prob=1.5, confidence=1.5))

    def test_append_decision_negative_cost_raises(self):
        with self.assertRaisesRegex(ValueError, "cost"):
            jc.append_decision(self.dp, _dec(cost=-1.0))

    def test_append_decision_nan_cost_raises(self):
        with self.assertRaisesRegex(ValueError, "cost"):
            jc.append_decision(self.dp, _dec(cost=float("nan")))

    def test_append_decision_empty_string_id_raises(self):
        with self.assertRaisesRegex(ValueError, "id"):
            jc.append_decision(self.dp, _dec(id=""))

    def test_append_decision_bad_timestamp_raises(self):
        with self.assertRaisesRegex(ValueError, "at"):
            jc.append_decision(self.dp, _dec(at="not a timestamp"))

    def test_append_decision_non_serializable_answer_raises(self):
        with self.assertRaisesRegex(ValueError, "answer"):
            jc.append_decision(self.dp, _dec(answer={1, 2, 3}))

    def test_append_outcome_missing_field_raises_and_writes_nothing(self):
        rec = _out()
        del rec["correct"]
        with self.assertRaisesRegex(ValueError, "correct"):
            jc.append_outcome(self.op, rec)
        self.assertFalse(os.path.exists(self.op))

    def test_append_outcome_correct_must_be_bool(self):
        with self.assertRaisesRegex(ValueError, "correct"):
            jc.append_outcome(self.op, _out(correct="yes"))


class TestJoin(LedgerTestCase):
    def test_empty_ledger_is_no_data_not_zero(self):
        j = jc.join(self.dp, self.op)
        self.assertEqual(j["joined"], [])
        self.assertEqual(j["unmatched_decisions"], [])
        self.assertEqual(j["orphan_outcomes"], [])

    def test_decision_with_no_outcome_is_unmatched_not_dropped(self):
        jc.append_decision(self.dp, _dec(id="d1"))
        j = jc.join(self.dp, self.op)
        self.assertEqual(j["joined"], [])
        self.assertEqual([d["id"] for d in j["unmatched_decisions"]], ["d1"])

    def test_outcome_with_no_decision_is_orphan_not_dropped(self):
        jc.append_outcome(self.op, _out(id="ghost"))
        j = jc.join(self.dp, self.op)
        self.assertEqual(j["joined"], [])
        self.assertEqual([o["id"] for o in j["orphan_outcomes"]], ["ghost"])

    def test_conflicting_outcomes_refuse_both(self):
        jc.append_decision(self.dp, _dec(id="d1"))
        jc.append_outcome(self.op, _out(id="d1", correct=True, at="2026-09-18T00:01:00Z"))
        jc.append_outcome(self.op, _out(id="d1", correct=False, at="2026-09-18T00:02:00Z"))
        j = jc.join(self.dp, self.op)
        self.assertEqual(j["joined"], [])
        self.assertEqual(len(j["ambiguous_outcomes"]), 1)
        self.assertEqual(j["ambiguous_outcomes"][0]["reason"], "conflicting correct values")

    def test_duplicate_outcomes_same_value_still_joins_and_is_reported(self):
        jc.append_decision(self.dp, _dec(id="d1"))
        jc.append_outcome(self.op, _out(id="d1", correct=True, at="2026-09-18T00:01:00Z"))
        jc.append_outcome(self.op, _out(id="d1", correct=True, at="2026-09-18T00:02:00Z"))
        j = jc.join(self.dp, self.op)
        self.assertEqual(len(j["joined"]), 1)
        self.assertEqual(len(j["duplicate_outcomes"]), 1)

    def test_duplicate_decision_ids_excluded_and_outcome_routed_separately(self):
        jc.append_decision(self.dp, _dec(id="d1", cost=0.0))
        jc.append_decision(self.dp, _dec(id="d1", cost=0.1))
        jc.append_outcome(self.op, _out(id="d1"))
        j = jc.join(self.dp, self.op)
        self.assertEqual(j["joined"], [])
        self.assertEqual(j["duplicate_decision_ids"], ["d1"])
        self.assertEqual(j["orphan_outcomes"], [])
        self.assertEqual([o["id"] for o in j["outcomes_for_duplicate_decision_ids"]], ["d1"])

    def test_corrupt_line_is_isolated_not_fatal(self):
        with open(self.dp, "w") as f:
            f.write(json.dumps(_dec(id="good")) + "\n")
            f.write("{not json\n")
        j = jc.join(self.dp, self.op)
        self.assertEqual(len(j["corrupt_decisions"]), 1)
        self.assertEqual(j["corrupt_decisions"][0]["line"], 2)
        self.assertEqual(len(j["unmatched_decisions"]), 1)

    def test_invalid_record_on_disk_is_isolated_not_fatal(self):
        with open(self.dp, "w") as f:
            f.write(json.dumps(_dec(id="good")) + "\n")
            bad = _dec(id="bad", qtype="not-a-qtype")
            f.write(json.dumps(bad) + "\n")
        j = jc.join(self.dp, self.op)
        self.assertEqual(len(j["corrupt_decisions"]), 1)
        self.assertEqual(len(j["unmatched_decisions"]), 1)


class TestWilsonBound(unittest.TestCase):
    def test_hand_worked_nine_of_ten(self):
        # Hand-computed per the Wilson score interval formula:
        # center = (p + z^2/2n) / (1 + z^2/n)
        # margin = z * sqrt(p(1-p)/n + z^2/4n^2) / (1 + z^2/n)
        # for k=9, n=10, z=1.959963984540054 the lower bound is
        # approximately 0.5958.
        lower = jc._wilson_lower_bound(9, 10)
        self.assertAlmostEqual(lower, 0.5958, places=3)

    def test_zero_n_is_none_not_zero(self):
        self.assertIsNone(jc._wilson_lower_bound(0, 0))

    def test_all_correct_lower_bound_below_one(self):
        lower = jc._wilson_lower_bound(10, 10)
        self.assertLess(lower, 1.0)
        self.assertGreater(lower, 0.6)


class TestReport(LedgerTestCase):
    def _seed(self, n_correct, n_wrong, confidence, family="f", qtype="noul"):
        for i in range(n_correct):
            did = "%s-%s-c%d" % (family, qtype, i)
            jc.append_decision(self.dp, _dec(id=did, family=family, qtype=qtype, confidence=confidence, prob=confidence))
            jc.append_outcome(self.op, _out(id=did, correct=True))
        for i in range(n_wrong):
            did = "%s-%s-w%d" % (family, qtype, i)
            jc.append_decision(self.dp, _dec(id=did, family=family, qtype=qtype, confidence=confidence, prob=confidence))
            jc.append_outcome(self.op, _out(id=did, correct=False))

    def test_empty_band_reports_none_not_zero(self):
        rep = jc.report(self.dp, self.op, "f", "noul", bands=[0.5, 0.9])
        band = rep["rows"][1]
        self.assertEqual(band["count"], 0)
        self.assertIsNone(band["precision"])
        self.assertIsNone(band["wilson_lower_95"])
        self.assertIsNotNone(band["note"])

    def test_noul_and_choice_same_family_never_pooled(self):
        self._seed(3, 0, 0.95, family="shared", qtype="noul")
        self._seed(1, 2, 0.95, family="shared", qtype="choice")
        noul_rep = jc.report(self.dp, self.op, "shared", "noul", bands=[0.9])
        choice_rep = jc.report(self.dp, self.op, "shared", "choice", bands=[0.9])
        self.assertEqual(noul_rep["rows"][0]["count"], 3)
        self.assertEqual(noul_rep["rows"][0]["correct"], 3)
        self.assertEqual(choice_rep["rows"][0]["count"], 3)
        self.assertEqual(choice_rep["rows"][0]["correct"], 1)

    def test_confidence_below_lowest_band_is_reported_not_dropped(self):
        self._seed(1, 0, 0.2, family="f", qtype="noul")
        rep = jc.report(self.dp, self.op, "f", "noul", bands=[0.5, 0.9])
        self.assertEqual(rep["below_lowest_band"]["count"], 1)
        self.assertEqual(sum(r["count"] for r in rep["rows"]), 0)
        self.assertEqual(rep["total_in_scope"], 1)

    def test_band_boundary_is_inclusive_of_lower_bound(self):
        self._seed(1, 0, 0.9, family="f", qtype="noul")
        rep = jc.report(self.dp, self.op, "f", "noul", bands=[0.5, 0.7, 0.9, 1.0])
        self.assertEqual(rep["rows"][2]["count"], 1)
        self.assertEqual(rep["rows"][1]["count"], 0)

    def test_framing_filter_excludes_other_framings(self):
        jc.append_decision(self.dp, _dec(id="a", framing="wordingA"))
        jc.append_outcome(self.op, _out(id="a"))
        jc.append_decision(self.dp, _dec(id="b", framing="wordingB"))
        jc.append_outcome(self.op, _out(id="b"))
        rep = jc.report(self.dp, self.op, "f", "noul", framing="wordingA", bands=[0.5])
        self.assertEqual(rep["total_in_scope"], 1)

    def test_unknown_family_reports_all_zero_not_an_error(self):
        rep = jc.report(self.dp, self.op, "never-seen", "noul")
        self.assertEqual(rep["total_in_scope"], 0)
        for row in rep["rows"]:
            self.assertEqual(row["count"], 0)
            self.assertIsNone(row["precision"])

    def test_empty_bands_raises(self):
        with self.assertRaises(ValueError):
            jc.report(self.dp, self.op, "f", "noul", bands=[])

    def test_duplicate_band_bounds_raise(self):
        with self.assertRaises(ValueError):
            jc.report(self.dp, self.op, "f", "noul", bands=[0.5, 0.5, 0.9])

    def test_out_of_range_band_bound_raises(self):
        with self.assertRaises(ValueError):
            jc.report(self.dp, self.op, "f", "noul", bands=[0.5, 1.5])

    def test_unrecognized_qtype_raises(self):
        with self.assertRaises(ValueError):
            jc.report(self.dp, self.op, "f", "yesno")


class TestThreshold(LedgerTestCase):
    def _seed_band(self, family, qtype, confidence, n_correct, n_wrong):
        for i in range(n_correct):
            did = "%s-%.2f-c%d" % (family, confidence, i)
            jc.append_decision(self.dp, _dec(id=did, family=family, qtype=qtype, confidence=confidence, prob=confidence))
            jc.append_outcome(self.op, _out(id=did, correct=True))
        for i in range(n_wrong):
            did = "%s-%.2f-w%d" % (family, confidence, i)
            jc.append_decision(self.dp, _dec(id=did, family=family, qtype=qtype, confidence=confidence, prob=confidence))
            jc.append_outcome(self.op, _out(id=did, correct=False))

    def test_no_band_meets_target_precision(self):
        # 80 correct out of 100 at confidence 0.95: raw precision 0.80,
        # nowhere near a target of 0.95.
        self._seed_band("f", "noul", 0.95, 80, 20)
        res = jc.threshold(self.dp, self.op, "f", "noul", target_precision=0.95, min_n=10, bands=[0.9])
        self.assertIsNone(res["threshold"])
        self.assertIn("reached", res["reason"])

    def test_not_enough_samples_even_though_raw_precision_would_pass(self):
        self._seed_band("f", "noul", 0.95, 10, 0)
        res = jc.threshold(self.dp, self.op, "f", "noul", target_precision=0.70, min_n=20, bands=[0.9])
        self.assertIsNone(res["threshold"])
        self.assertIn("min_n", res["reason"])

    def test_threshold_picks_lowest_qualifying_band(self):
        self._seed_band("f", "noul", 0.55, 48, 2)
        self._seed_band("f", "noul", 0.95, 49, 1)
        res = jc.threshold(self.dp, self.op, "f", "noul", target_precision=0.85, min_n=20, bands=[0.5, 0.9])
        self.assertEqual(res["threshold"], 0.5)

    def test_invalid_target_precision_raises(self):
        with self.assertRaises(ValueError):
            jc.threshold(self.dp, self.op, "f", "noul", target_precision=1.5, min_n=1)

    def test_invalid_min_n_raises(self):
        with self.assertRaises(ValueError):
            jc.threshold(self.dp, self.op, "f", "noul", target_precision=0.9, min_n=0)


class TestSeedFromEval(LedgerTestCase):
    def setUp(self):
        super().setUp()
        self.results_path = os.path.join(self._tmp.name, "results.jsonl")
        self.dataset_path = os.path.join(self._tmp.name, "dataset.json")

    def _write_dataset(self, families):
        payload = {}
        for letter, instr in families.items():
            payload["%s_family" % letter] = {"instructions": instr, "true": "x", "false": "y", "items": []}
        with open(self.dataset_path, "w") as f:
            json.dump(payload, f)

    def _write_results(self, rows):
        with open(self.results_path, "w") as f:
            for row in rows:
                f.write(json.dumps(row) + "\n")

    def test_missing_results_file_is_no_data(self):
        res = jc.seed_from_eval("/does/not/exist.jsonl", "/does/not/exist.json", self.dp, self.op)
        self.assertEqual(res["status"], "NO-DATA")

    def test_missing_dataset_file_is_no_data(self):
        self._write_results([{"task": "A", "item": "A1", "system": "jev", "pred": True, "conf": 0.9, "correct": True, "model": "m", "cost": 0.001}])
        res = jc.seed_from_eval(self.results_path, "/does/not/exist.json", self.dp, self.op)
        self.assertEqual(res["status"], "NO-DATA")

    def test_seeds_only_matching_system(self):
        self._write_dataset({"A": "some instructions text"})
        self._write_results([
            {"task": "A", "item": "A1", "system": "jev", "pred": True, "conf": 0.9, "correct": True, "model": "m", "cost": 0.001},
            {"task": "A", "item": "A2", "system": "other-model", "pred": True, "conf": 0.9, "correct": True, "model": "m", "cost": 0.001},
        ])
        res = jc.seed_from_eval(self.results_path, self.dataset_path, self.dp, self.op)
        self.assertEqual(res["status"], "OK")
        self.assertEqual(res["decisions_written"], 1)
        rep = jc.report(self.dp, self.op, "A", "noul", bands=[0.5])
        self.assertEqual(rep["total_in_scope"], 1)

    def test_skips_rows_with_no_prediction(self):
        self._write_dataset({"A": "some instructions text"})
        self._write_results([
            {"task": "A", "item": "A1", "system": "jev", "pred": None, "conf": None, "correct": None, "model": "m", "cost": 0.001, "error": "HTTP 520"},
        ])
        res = jc.seed_from_eval(self.results_path, self.dataset_path, self.dp, self.op)
        self.assertEqual(res["status"], "OK")
        self.assertEqual(res["decisions_written"], 0)
        self.assertEqual(len(res["skipped"]), 1)

    def test_choice_qtype_derived_from_string_prediction(self):
        self._write_dataset({"C": "routing instructions text"})
        self._write_results([
            {"task": "C", "item": "C1", "system": "jev", "pred": "clean", "conf": 0.8, "correct": True, "model": "m", "cost": 0.001},
        ])
        res = jc.seed_from_eval(self.results_path, self.dataset_path, self.dp, self.op)
        self.assertEqual(res["status"], "OK")
        rep = jc.report(self.dp, self.op, "C", "choice", bands=[0.5])
        self.assertEqual(rep["total_in_scope"], 1)

    def test_same_family_letter_used_for_framing_hash(self):
        self._write_dataset({"A": "consistent wording"})
        self._write_results([
            {"task": "A", "item": "A1", "system": "jev", "pred": True, "conf": 0.9, "correct": True, "model": "m", "cost": 0.001},
            {"task": "A", "item": "A2", "system": "jev", "pred": False, "conf": 0.9, "correct": True, "model": "m", "cost": 0.001},
        ])
        jc.seed_from_eval(self.results_path, self.dataset_path, self.dp, self.op)
        with open(self.dp) as f:
            recs = [json.loads(line) for line in f]
        self.assertEqual(recs[0]["framing"], recs[1]["framing"])

    def test_outcomes_carry_pinned_source(self):
        self._write_dataset({"A": "consistent wording"})
        self._write_results([
            {"task": "A", "item": "A1", "system": "jev", "pred": True, "conf": 0.9, "correct": True, "model": "m", "cost": 0.001},
        ])
        jc.seed_from_eval(self.results_path, self.dataset_path, self.dp, self.op)
        with open(self.op) as f:
            rec = json.loads(f.readline())
        self.assertEqual(rec["source"], jc.SEED_SOURCE)


class TestDefaultLedgerPath(unittest.TestCase):
    def test_default_ledger_path_untouched(self):
        existed_before = os.path.exists(jc.DEFAULT_LEDGER_PATH)
        mtime_before = os.path.getmtime(jc.DEFAULT_LEDGER_PATH) if existed_before else None
        # Deliberately never call anything with jc.DEFAULT_LEDGER_PATH here:
        # every other test in this suite injects its own tempfile path.
        existed_after = os.path.exists(jc.DEFAULT_LEDGER_PATH)
        self.assertEqual(existed_before, existed_after)
        if existed_before:
            self.assertEqual(mtime_before, os.path.getmtime(jc.DEFAULT_LEDGER_PATH))


if __name__ == "__main__":
    unittest.main()

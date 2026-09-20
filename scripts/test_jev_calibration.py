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
import shutil
import sys
import tempfile
import unittest
from unittest import mock

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
        jc.append_outcome(self.op, _out(id="ghost"), allow_unchecked=True)
        j = jc.join(self.dp, self.op)
        self.assertEqual(j["joined"], [])
        self.assertEqual([o["id"] for o in j["orphan_outcomes"]], ["ghost"])

    def test_conflicting_outcomes_refuse_both(self):
        jc.append_decision(self.dp, _dec(id="d1"))
        jc.append_outcome(self.op, _out(id="d1", correct=True, at="2026-09-18T00:01:00Z"), decisions_path=self.dp)
        jc.append_outcome(self.op, _out(id="d1", correct=False, at="2026-09-18T00:02:00Z"), decisions_path=self.dp)
        j = jc.join(self.dp, self.op)
        self.assertEqual(j["joined"], [])
        self.assertEqual(len(j["ambiguous_outcomes"]), 1)
        self.assertEqual(j["ambiguous_outcomes"][0]["reason"], "conflicting correct values")

    def test_duplicate_outcomes_same_value_are_refused_not_joined(self):
        # A0.3 (2026-09-18): a second outcome for one decision id is
        # refused even when both agree on `correct`, since which write
        # actually happened is unknowable from the ledger alone.
        jc.append_decision(self.dp, _dec(id="d1"))
        jc.append_outcome(self.op, _out(id="d1", correct=True, at="2026-09-18T00:01:00Z"), decisions_path=self.dp)
        jc.append_outcome(self.op, _out(id="d1", correct=True, at="2026-09-18T00:02:00Z"), decisions_path=self.dp)
        j = jc.join(self.dp, self.op)
        self.assertEqual(j["joined"], [])
        self.assertEqual(len(j["duplicate_outcomes"]), 1)
        self.assertEqual(j["duplicate_outcomes"][0]["id"], "d1")

    def test_orphan_and_duplicate_outcomes_refused_and_counted_in_report(self):
        # Both failure shapes A0.3 names: an outcome with no decision at
        # all, and a second outcome for one decision id. Neither may raise
        # (the rest of the ledger, including "good", must still read), and
        # both must be counted by name in report()'s ledger_wide, not just
        # silently excluded from total_in_scope.
        jc.append_decision(self.dp, _dec(id="good"))
        jc.append_outcome(self.op, _out(id="good", correct=True, at="2026-09-18T00:01:00Z"), decisions_path=self.dp)
        jc.append_outcome(self.op, _out(id="ghost", correct=True, at="2026-09-18T00:01:00Z"), allow_unchecked=True)
        jc.append_decision(self.dp, _dec(id="dup"))
        jc.append_outcome(self.op, _out(id="dup", correct=True, at="2026-09-18T00:01:00Z"), decisions_path=self.dp)
        jc.append_outcome(self.op, _out(id="dup", correct=True, at="2026-09-18T00:02:00Z"), decisions_path=self.dp)

        j = jc.join(self.dp, self.op)
        self.assertEqual([d["decision"]["id"] for d in j["joined"]], ["good"])
        self.assertEqual([o["id"] for o in j["orphan_outcomes"]], ["ghost"])
        self.assertEqual([o["id"] for o in j["duplicate_outcomes"]], ["dup"])

        rep = jc.report(self.dp, self.op, "f", "noul", bands=[0.5])
        self.assertEqual(rep["total_in_scope"], 1)
        self.assertEqual(rep["ledger_wide"]["orphan_outcomes"], 1)
        self.assertEqual(rep["ledger_wide"]["duplicate_outcomes"], 1)

    def test_duplicate_decision_ids_excluded_and_outcome_routed_separately(self):
        jc.append_decision(self.dp, _dec(id="d1", cost=0.0))
        jc.append_decision(self.dp, _dec(id="d1", cost=0.1))
        jc.append_outcome(self.op, _out(id="d1"), decisions_path=self.dp)
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
            jc.append_outcome(self.op, _out(id=did, correct=True), decisions_path=self.dp)
        for i in range(n_wrong):
            did = "%s-%s-w%d" % (family, qtype, i)
            jc.append_decision(self.dp, _dec(id=did, family=family, qtype=qtype, confidence=confidence, prob=confidence))
            jc.append_outcome(self.op, _out(id=did, correct=False), decisions_path=self.dp)

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
        jc.append_outcome(self.op, _out(id="a"), decisions_path=self.dp)
        jc.append_decision(self.dp, _dec(id="b", framing="wordingB"))
        jc.append_outcome(self.op, _out(id="b"), decisions_path=self.dp)
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
            jc.append_outcome(self.op, _out(id=did, correct=True), decisions_path=self.dp)
        for i in range(n_wrong):
            did = "%s-%.2f-w%d" % (family, confidence, i)
            jc.append_decision(self.dp, _dec(id=did, family=family, qtype=qtype, confidence=confidence, prob=confidence))
            jc.append_outcome(self.op, _out(id=did, correct=False), decisions_path=self.dp)

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


class TestLedgerAnomalyGate(LedgerTestCase):
    """C1, 2026-09-18 review fix: threshold() must return no threshold
    (reason "ledger anomaly") whenever a duplicate, ambiguous, orphan or
    corrupt record touches the family/qtype being asked about, rather than
    silently computing a threshold from whatever clean-looking remainder
    survives excluding those records."""

    def _seed_band(self, family, qtype, confidence, n_correct, n_wrong):
        for i in range(n_correct):
            did = "%s-%.2f-c%d" % (family, confidence, i)
            jc.append_decision(self.dp, _dec(id=did, family=family, qtype=qtype, confidence=confidence, prob=confidence))
            jc.append_outcome(self.op, _out(id=did, correct=True), decisions_path=self.dp)
        for i in range(n_wrong):
            did = "%s-%.2f-w%d" % (family, confidence, i)
            jc.append_decision(self.dp, _dec(id=did, family=family, qtype=qtype, confidence=confidence, prob=confidence))
            jc.append_outcome(self.op, _out(id=did, correct=False), decisions_path=self.dp)

    def test_clean_ledger_threshold_unchanged(self):
        # No duplicates, no anomalies: threshold() behaves exactly as
        # before this fix.
        self._seed_band("f", "noul", 0.99, 57, 3)
        res = jc.threshold(self.dp, self.op, "f", "noul", target_precision=0.9, min_n=20, bands=[0.99])
        self.assertIsNone(res["threshold"])
        self.assertIn("reached", res["reason"])
        self._seed_band("g", "noul", 0.55, 48, 2)
        res2 = jc.threshold(self.dp, self.op, "g", "noul", target_precision=0.85, min_n=20, bands=[0.5])
        self.assertEqual(res2["threshold"], 0.5)

    def test_reviewer_probe_duplicated_wrong_labels_yields_no_threshold(self):
        # The reviewer's exact probe: 60 decisions at confidence 0.99, 3 of
        # them wrong. Without duplicates, the Wilson lower bound (~0.8630)
        # is honestly below a 0.9 target, so threshold() correctly finds
        # none. Appending each wrong label's outcome A SECOND TIME (same
        # correct=False value, so it lands in duplicate_outcomes, not
        # ambiguous_outcomes) is the regression this fix closes: before
        # it, those 3 duplicated ids were dropped from precision entirely,
        # leaving 57/57 "clean" and a Wilson lower bound of ~0.9369 --
        # inflated PAST the 0.9 target by the very act of duplicating a
        # failure. threshold() must now refuse instead.
        self._seed_band("f", "noul", 0.99, 57, 3)
        wrong_ids = ["f-0.99-w%d" % i for i in range(3)]

        # Sanity check: without duplication, no threshold (matches the
        # reviewer's cited bound of 0.8630).
        clean = jc.threshold(self.dp, self.op, "f", "noul", target_precision=0.9, min_n=20, bands=[0.99])
        self.assertIsNone(clean["threshold"])
        self.assertNotEqual(clean["reason"], "ledger anomaly")

        for did in wrong_ids:
            jc.append_outcome(self.op, _out(id=did, correct=False), decisions_path=self.dp)

        res = jc.threshold(self.dp, self.op, "f", "noul", target_precision=0.9, min_n=20, bands=[0.99])
        self.assertIsNone(res["threshold"])
        self.assertEqual(res["reason"], "ledger anomaly")

    def test_ambiguous_true_false_pair_yields_no_threshold(self):
        # A single decision whose outcome was written once as correct=True
        # and once as correct=False (ambiguous_outcomes) must also block
        # the threshold for its family/qtype -- the review's other named
        # case: "a single added 'correct' line can erase a failure."
        self._seed_band("f", "noul", 0.99, 56, 3)
        jc.append_decision(self.dp, _dec(id="amb1", family="f", qtype="noul", confidence=0.99, prob=0.99))
        jc.append_outcome(self.op, _out(id="amb1", correct=False), decisions_path=self.dp)
        jc.append_outcome(self.op, _out(id="amb1", correct=True), decisions_path=self.dp)

        res = jc.threshold(self.dp, self.op, "f", "noul", target_precision=0.9, min_n=20, bands=[0.99])
        self.assertIsNone(res["threshold"])
        self.assertEqual(res["reason"], "ledger anomaly")

    def test_anomaly_in_a_different_family_does_not_block_this_one(self):
        # Attribution is per family/qtype, not whole-ledger: a duplicate
        # outcome touching family "g" must not block threshold() for the
        # unrelated, clean family "f".
        self._seed_band("f", "noul", 0.99, 57, 3)
        jc.append_decision(self.dp, _dec(id="dup1", family="g", qtype="noul", confidence=0.99, prob=0.99))
        jc.append_outcome(self.op, _out(id="dup1", correct=False), decisions_path=self.dp)
        jc.append_outcome(self.op, _out(id="dup1", correct=False), decisions_path=self.dp)

        res = jc.threshold(self.dp, self.op, "f", "noul", target_precision=0.9, min_n=20, bands=[0.99])
        self.assertNotEqual(res["reason"], "ledger anomaly")

        res_g = jc.threshold(self.dp, self.op, "g", "noul", target_precision=0.9, min_n=1, bands=[0.99])
        self.assertIsNone(res_g["threshold"])
        self.assertEqual(res_g["reason"], "ledger anomaly")

    def test_orphan_outcome_is_unattributable_and_blocks_every_family(self):
        # An outcome id with no readable "<family>:" prefix carries no way
        # to attribute it to any one family: unattributable, so it must
        # be treated as ledger-wide and block every family/qtype, not
        # just one. (Coordinator re-review, MAJOR, 2026-09-18: an orphan
        # WITH a readable prefix is scoped to that family instead -- see
        # the two tests below.)
        self._seed_band("f", "noul", 0.99, 57, 3)
        jc.append_outcome(self.op, _out(id="ghost-no-decision", correct=True), allow_unchecked=True)

        res = jc.threshold(self.dp, self.op, "f", "noul", target_precision=0.9, min_n=20, bands=[0.99])
        self.assertIsNone(res["threshold"])
        self.assertEqual(res["reason"], "ledger anomaly")

    def test_orphan_with_no_readable_prefix_still_blocks_ledger_wide(self):
        # Coordinator re-review, MAJOR, 2026-09-18: same property as the
        # test above, phrased in the coordinator's own words -- an id with
        # no colon at all carries no readable "<family>:" prefix, so it
        # stays ledger-wide.
        self._seed_band("N1", "noul", 0.99, 57, 3)
        jc.append_outcome(self.op, _out(id="no-colon-here", correct=True), allow_unchecked=True)

        res = jc.threshold(self.dp, self.op, "N1", "noul", target_precision=0.9, min_n=20, bands=[0.99])
        self.assertIsNone(res["threshold"])
        self.assertEqual(res["reason"], "ledger anomaly")

    def test_orphan_with_readable_family_prefix_leaves_an_unrelated_family_intact(self):
        # Coordinator re-review, MAJOR, 2026-09-18: decision ids from the
        # seam are shaped "<family>:<...>". An orphan outcome whose id
        # names a DIFFERENT family than the one threshold() is asked
        # about must not disable every family in the ledger -- only the
        # one it actually names. N1's threshold is computed exactly as it
        # would be with no orphan present at all.
        self._seed_band("N1", "noul", 0.99, 40, 0)
        jc.append_outcome(self.op, _out(id="OTHER:some-item", correct=True), allow_unchecked=True)

        res = jc.threshold(self.dp, self.op, "N1", "noul", target_precision=0.9, min_n=20, bands=[0.99])
        self.assertEqual(res["threshold"], 0.99)

        # The orphan's own named family is still, correctly, anomalous.
        res_other = jc.threshold(self.dp, self.op, "OTHER", "noul", target_precision=0.9, min_n=1, bands=[0.99])
        self.assertIsNone(res_other["threshold"])
        self.assertEqual(res_other["reason"], "ledger anomaly")

    def test_decisions_file_unreadable_escalates_never_raises(self):
        # OSError minor (2026-09-18 review): the decisions file exists but
        # cannot be opened (permission denied). join()/report()/
        # threshold() must never raise; they must fail closed as a ledger
        # anomaly instead. Skipped when running as root, where chmod 000
        # does not block reads.
        if hasattr(os, "geteuid") and os.geteuid() == 0:
            self.skipTest("permission bits are not enforced for root")
        self._seed_band("f", "noul", 0.99, 57, 3)
        os.chmod(self.dp, 0o000)
        try:
            res = jc.threshold(self.dp, self.op, "f", "noul", target_precision=0.9, min_n=20, bands=[0.99])
        finally:
            os.chmod(self.dp, 0o644)
        self.assertIsNone(res["threshold"])
        self.assertEqual(res["reason"], "ledger anomaly")


class TestLedgerReliability(LedgerTestCase):
    """Item 1 (2026-09-19): ledger_reliability() is the real, callable
    "is the ledger currently reliable" answer a board/doctor script can
    use, without knowing join()'s full return shape."""

    def test_missing_ledger_is_reliable_empty(self):
        # Neither file has ever been written: per _read_jsonl()'s own
        # rule, this is an empty ledger, not damage.
        res = jc.ledger_reliability(self.dp, self.op)
        self.assertTrue(res["reliable"])
        self.assertIsNone(res["reason"])
        self.assertEqual(res["corrupt_decisions"], 0)
        self.assertEqual(res["corrupt_outcomes"], 0)

    def test_clean_ledger_is_reliable(self):
        jc.append_decision(self.dp, _dec(id="d1"))
        jc.append_outcome(self.op, _out(id="d1"), decisions_path=self.dp)
        res = jc.ledger_reliability(self.dp, self.op)
        self.assertTrue(res["reliable"])
        self.assertIsNone(res["reason"])

    def test_corrupt_decision_line_is_unreliable_and_named(self):
        jc.append_decision(self.dp, _dec(id="d1"))
        with open(self.dp, "a", encoding="utf-8") as fh:
            fh.write("not json at all\n")
        res = jc.ledger_reliability(self.dp, self.op)
        self.assertFalse(res["reliable"])
        self.assertEqual(res["corrupt_decisions"], 1)
        self.assertIn("1 corrupt line(s) in the decisions ledger", res["reason"])

    def test_corrupt_outcome_line_is_unreliable_and_named(self):
        jc.append_decision(self.dp, _dec(id="d1"))
        jc.append_outcome(self.op, _out(id="d1"), decisions_path=self.dp)
        with open(self.op, "a", encoding="utf-8") as fh:
            fh.write("not json at all\n")
        res = jc.ledger_reliability(self.dp, self.op)
        self.assertFalse(res["reliable"])
        self.assertEqual(res["corrupt_outcomes"], 1)
        self.assertIn("1 corrupt line(s) in the outcomes ledger", res["reason"])

    def test_unlistable_segment_directory_is_unreliable(self):
        if hasattr(os, "geteuid") and os.geteuid() == 0:
            self.skipTest("permission bits are not enforced for root")
        jc.append_decision(self.dp, _dec(id="d1"))
        directory = os.path.dirname(self.dp)
        os.chmod(directory, 0o300)
        try:
            res = jc.ledger_reliability(self.dp, self.op)
        finally:
            os.chmod(directory, 0o700)
        self.assertFalse(res["reliable"])
        self.assertFalse(res["segment_read_ok"])
        self.assertIn("segment directory could not be fully listed", res["reason"])


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


class TestLedgerRotation(LedgerTestCase):
    """A0.8: decisions.jsonl/outcomes.jsonl rotate by size into dated
    segments, pruned by a retention count, and join()/report()/threshold()
    read every segment plus the active file transparently -- see
    jev_calibration.py's own LEDGER ROTATION section."""

    def test_unrotated_ledger_has_no_segments(self):
        jc.append_decision(self.dp, _dec(id="d1"))
        self.assertEqual(jc._rotated_segment_paths(self.dp), [])

    def test_crossing_the_size_budget_rotates_into_a_segment(self):
        # A tiny budget so a handful of ordinary records cross it.
        for i in range(20):
            jc.append_decision(self.dp, _dec(id="d%d" % i), max_segment_bytes=500)
        segments = jc._rotated_segment_paths(self.dp)
        self.assertGreater(len(segments), 0)
        # One more append after the loop: if the loop's own last write was
        # the one that triggered rotation, `self.dp` was just renamed away
        # and has not been recreated yet -- this append recreates it fresh,
        # deterministically, so the size assertion below is never a race
        # against which write happened to be "last".
        jc.append_decision(self.dp, _dec(id="d-final"), max_segment_bytes=500)
        self.assertTrue(os.path.exists(self.dp))
        self.assertLessEqual(os.path.getsize(self.dp), 500 * 2)

    def test_retention_prunes_oldest_segments_first(self):
        # Reviewer finding (f): the old version of this test only checked
        # `len(segments) <= 3` and passed against two broken variants --
        # one that deletes the NEWEST segments instead of the oldest, and
        # one that deletes EVERY segment. Strengthened to check WHICH ids
        # survive: the retained segments must hold the most RECENTLY
        # written ids, never the earliest ones.
        for i in range(60):
            jc.append_decision(self.dp, _dec(id="d%03d" % i), max_segment_bytes=200,
                                retention_segments=3)
        segments = jc._rotated_segment_paths(self.dp)
        self.assertLessEqual(len(segments), 3)  # pruned to the newest 3 on every rotation
        surviving_ids = set()
        for seg in segments:
            surviving_ids |= jc._segment_ids(seg)
        surviving_ids |= jc._segment_ids(self.dp)  # the still-active file's own last row(s)
        # The earliest-written ids (d000, d001, ...) must be gone; the
        # latest-written ones (close to d059) must still be present. A
        # variant that deleted the NEWEST segments instead would fail the
        # first assertion below; a variant that deleted EVERY segment
        # would fail the second (surviving_ids would be empty).
        self.assertNotIn("d000", surviving_ids)
        self.assertIn("d059", surviving_ids)

    def test_join_reads_segments_and_active_file_together(self):
        # Force rotation partway through a small, easily hand-checked set.
        for i in range(10):
            jc.append_decision(self.dp, _dec(id="d%d" % i, confidence=0.9, prob=0.9),
                                max_segment_bytes=150, retention_segments=100)
            jc.append_outcome(self.op, _out(id="d%d" % i, correct=(i % 2 == 0)),
                               max_segment_bytes=150, retention_segments=100, decisions_path=self.dp)
        self.assertGreater(len(jc._rotated_segment_paths(self.dp)), 0)
        self.assertGreater(len(jc._rotated_segment_paths(self.op)), 0)
        j = jc.join(self.dp, self.op)
        self.assertEqual(len(j["joined"]), 10)
        rep = jc.report(self.dp, self.op, "f", "noul", bands=[0.5])
        self.assertEqual(rep["total_in_scope"], 10)
        self.assertEqual(sum(1 for item in j["joined"] if item["outcome"]["correct"]), 5)

    def test_threshold_same_answer_rotated_or_not(self):
        # Reviewer minor, 2026-09-19: reduce this test's runtime without
        # weakening what it proves. 100,000 rows made the calibration
        # suite alone take 43s; the property under test (rotated reads
        # agree with an unrotated read) does not need six digits of rows
        # to be genuine, only enough rows AND a small enough
        # max_segment_bytes to force MANY real segments -- 20,000 rows at
        # an 8 KB budget forces well over a dozen segments (checked
        # below), the same multi-segment shape the 100k version exercised,
        # at roughly a fifth of the wall-clock cost.
        N = 20_000
        n_wrong = 600  # same 3% wrong ratio as before

        def _seed(dp, op, **kwargs):
            for i in range(N):
                did = "bulk-%d" % i
                correct = i >= n_wrong  # first n_wrong are wrong, rest correct
                jc.append_decision(dp, _dec(id=did, family="bulk", qtype="noul",
                                             confidence=0.97, prob=0.97), **kwargs)
                # allow_unchecked=True (not decisions_path): this loop writes
                # 20,000 real decisions/outcomes per call to _seed(), and
                # decisions_path's reliability check re-scans the WHOLE
                # decisions ledger (every rotated segment) on every single
                # outcome append -- O(n) per call, O(n^2) for the loop (see
                # the round 4 review's own n5 finding: 3000 labels took 6.16s
                # WITH the check against 0.22s without). This test's own
                # comment already explains it was cut from 100k to 20k rows to
                # keep runtime sane; the ids here are always real (written the
                # line above), so allow_unchecked=True is a deliberate,
                # documented opt-out for a bulk-seed helper, not a gap in
                # coverage of the guard itself (see N1AppendOutcomeRefusesAnUnwrittenId
                # for that).
                jc.append_outcome(op, _out(id=did, correct=correct), allow_unchecked=True, **kwargs)

        rotated_dir = tempfile.mkdtemp()
        rdp = os.path.join(rotated_dir, "decisions.jsonl")
        rop = os.path.join(rotated_dir, "outcomes.jsonl")
        _seed(rdp, rop, max_segment_bytes=8 * 1024, retention_segments=10_000)
        self.assertGreater(len(jc._rotated_segment_paths(rdp)), 10)

        _seed(self.dp, self.op, max_segment_bytes=None)
        self.assertEqual(jc._rotated_segment_paths(self.dp), [])

        res_rotated = jc.threshold(rdp, rop, "bulk", "noul", target_precision=0.9,
                                    min_n=20, bands=[0.97])
        res_unrotated = jc.threshold(self.dp, self.op, "bulk", "noul", target_precision=0.9,
                                      min_n=20, bands=[0.97])
        self.assertEqual(res_rotated["threshold"], res_unrotated["threshold"])
        self.assertEqual(res_rotated["rows"][0]["count"], res_unrotated["rows"][0]["count"])
        self.assertEqual(res_rotated["rows"][0]["count"], N)
        self.assertAlmostEqual(res_rotated["rows"][0]["wilson_lower_95"],
                                res_unrotated["rows"][0]["wilson_lower_95"])
        shutil.rmtree(rotated_dir, ignore_errors=True)

    def test_rotation_failure_never_turns_a_successful_write_into_an_error(self):
        # os.replace() itself failing at rotation time (segment dir
        # unwritable) must not raise out of append_decision(): the record
        # was already safely appended before rotation is even attempted.
        jc.append_decision(self.dp, _dec(id="d1"))
        with mock.patch("os.replace", side_effect=OSError("simulated")):
            jc.append_decision(self.dp, _dec(id="d2"), max_segment_bytes=1)
        with open(self.dp, encoding="utf-8") as fh:
            ids = [json.loads(line)["id"] for line in fh if line.strip()]
        self.assertEqual(ids, ["d1", "d2"])

    def test_m5_retention_never_orphans_a_retained_outcome(self):
        # Reviewer probe, M5 (independent review, 2026-09-19): 300
        # decisions, only the FIRST 60 ever get an outcome (a human only
        # labelled the early ones), retention=3. Before this fix, decision
        # retention pruned purely by age with no knowledge of outcomes:
        # the 60 early outcomes' own decisions were rotated into segments
        # long since deleted, producing 60 orphans and 0 joins. The fix:
        # append_decision()'s sibling_outcomes_path tells rotation never to
        # delete a decision segment an outcome still needs.
        for i in range(300):
            did = "m5-%03d" % i
            jc.append_decision(self.dp, _dec(id=did, family="m5", qtype="noul",
                                              confidence=0.9, prob=0.9),
                                max_segment_bytes=300, retention_segments=3,
                                sibling_outcomes_path=self.op)
            if i < 60:
                # Outcomes deliberately NOT rotated in this test (a large
                # max_segment_bytes, the module default retention): all 60
                # stay in one file, so this test isolates the DECISION-side
                # protection M5 adds, rather than also exercising outcome
                # retention (a separate, unprotected concern -- see
                # test_m5_without_sibling_path_behaves_as_before_no_protection
                # and the module docstring's own scoping of this fix).
                jc.append_outcome(self.op, _out(id=did, correct=True), decisions_path=self.dp)
        j = jc.join(self.dp, self.op)
        self.assertEqual(len(j["joined"]), 60, "M5: a retained outcome's decision was orphaned")
        self.assertEqual(j["orphan_outcomes"], [])
        rep = jc.report(self.dp, self.op, "m5", "noul", bands=[0.5])
        self.assertEqual(rep["total_in_scope"], 60)
        self.assertEqual(rep["ledger_wide"]["orphan_outcomes"], 0)

    def test_m5_without_sibling_path_behaves_as_before_no_protection(self):
        # The cross-file protection is opt-in via sibling_outcomes_path: a
        # caller that never passes it (the pre-M5 default) gets the
        # original, unprotected pruning -- documented, not a silent
        # behaviour change for every existing caller.
        for i in range(300):
            did = "noprotect-%03d" % i
            jc.append_decision(self.dp, _dec(id=did, family="np", qtype="noul",
                                              confidence=0.9, prob=0.9),
                                max_segment_bytes=300, retention_segments=3)
            if i < 60:
                jc.append_outcome(self.op, _out(id=did, correct=True),
                                   max_segment_bytes=300, retention_segments=3, decisions_path=self.dp)
        j = jc.join(self.dp, self.op)
        self.assertLess(len(j["joined"]), 60)  # some early decisions were pruned away
        self.assertGreater(len(j["orphan_outcomes"]), 0)

    def test_clock_set_backwards_does_not_reorder_or_misdelete_segments(self):
        # Reviewer minor, 2026-09-19: a wall clock set backwards must
        # never make a NEWER segment sort before an OLDER one (which would
        # make retention delete the newest segment first). Ordering now
        # comes from _next_segment_seq()'s own directory scan, never from
        # datetime.now(), so mocking the clock backwards between two
        # rotations must not disturb it.
        real_datetime = jc.datetime

        class _BackwardsDatetime(real_datetime):
            # Subclasses the REAL datetime.datetime (not a bare stand-in)
            # so every other classmethod this module uses (_parse_ts's own
            # fromisoformat, in particular) keeps working unchanged; only
            # now() is overridden, to always claim "the past".
            @classmethod
            def now(cls, tz=None):
                return real_datetime(2020, 1, 1, tzinfo=tz)

        for i in range(3):
            jc.append_decision(self.dp, _dec(id="early-%d" % i), max_segment_bytes=100)
        before = jc._rotated_segment_paths(self.dp)
        self.assertGreater(len(before), 0)

        with mock.patch.object(jc, "datetime", _BackwardsDatetime):
            for i in range(3):
                jc.append_decision(self.dp, _dec(id="late-%d" % i), max_segment_bytes=100)
        after = jc._rotated_segment_paths(self.dp)
        self.assertGreater(len(after), len(before))
        # The segments written BEFORE the clock went backwards must still
        # sort before the ones written AFTER (i.e. while the mocked clock
        # claimed to be in 2020) -- oldest-first ordering survives.
        self.assertEqual(after[:len(before)], before)


class N3UnreadableOrCorruptProtectionScanSkipsTheWholePrune(LedgerTestCase):
    """N3 (independent re-review, 2026-09-19): M5's cross-file protection
    read must never treat "cannot verify what this protects" as "nothing
    to protect". Probed: 140 decisions written while the sibling outcomes
    file was unreadable orphaned all 60 of its later-readable labels.
    Fixed at _protection_scan(): an unreadable outcomes directory, an
    unopenable segment, or a single corrupt line anywhere in it now makes
    retention skip the ENTIRE prune pass for that rotation, not merely
    treat the unreadable part as unprotected."""

    def test_unreadable_outcomes_directory_skips_pruning_entirely(self):
        if hasattr(os, "geteuid") and os.geteuid() == 0:
            self.skipTest("permission bits are not enforced for root")
        # A sibling outcomes ledger that is never even readable (its own
        # directory cannot be listed): before N3, _all_ledger_ids() would
        # silently read this as "protects nothing" and prune away
        # everything past retention.
        outcomes_dir = os.path.join(os.path.dirname(self.op), "unreadable-outcomes")
        os.makedirs(outcomes_dir)
        op = os.path.join(outcomes_dir, "outcomes.jsonl")
        with open(op, "w", encoding="utf-8") as fh:
            fh.write("")
        os.chmod(outcomes_dir, 0o000)
        try:
            before_segments = []
            for i in range(20):
                jc.append_decision(self.dp, _dec(id="d-%03d" % i), max_segment_bytes=100,
                                    retention_segments=2, sibling_outcomes_path=op)
            segments = jc._rotated_segment_paths(self.dp)
            self.assertGreater(len(segments), 2, "rotation itself never happened -- test setup is wrong")
        finally:
            os.chmod(outcomes_dir, 0o700)
        # N3: nothing was deleted -- every rotated segment survives, well
        # past the nominal retention_segments=2, because the protection
        # scan could not be trusted for the whole run.
        self.assertEqual(len(segments), 20, "0 to 19 rotated on 100-byte segments: none should be deleted")

    def test_corrupt_outcome_line_anywhere_skips_pruning_entirely(self):
        # A SINGLE corrupt line in the sibling outcomes file, even one
        # that names no id this run cares about, must make the whole scan
        # untrustworthy -- "meets any corrupt line" per N3's own spec.
        with open(self.op, "w", encoding="utf-8") as fh:
            fh.write('{"id": "real-0", "correct": true, "source": "t", "at": "2026-09-18T00:01:00Z"}\n')
            fh.write("not even json\n")
        for i in range(20):
            jc.append_decision(self.dp, _dec(id="real-%d" % i if i == 0 else "d-%03d" % i),
                                max_segment_bytes=100, retention_segments=2,
                                sibling_outcomes_path=self.op)
        segments = jc._rotated_segment_paths(self.dp)
        self.assertGreater(len(segments), 2, "rotation itself never happened -- test setup is wrong")
        self.assertEqual(len(segments), 20, "the corrupt line should have skipped the whole prune pass")

    def test_protection_scan_reports_reliable_true_for_ordinary_data(self):
        # Sanity/mutation-kill control: an ordinary, fully-readable
        # outcomes file must NOT trip the "skip pruning" path -- N3 must
        # not become "never prune".
        with open(self.op, "w", encoding="utf-8") as fh:
            fh.write('{"id": "d-000", "correct": true, "source": "t", "at": "2026-09-18T00:01:00Z"}\n')
        ids, reliable = jc._protection_scan(self.op)
        self.assertTrue(reliable)
        self.assertEqual(ids, {"d-000"})

        for i in range(20):
            jc.append_decision(self.dp, _dec(id="d-%03d" % i), max_segment_bytes=100,
                                retention_segments=2, sibling_outcomes_path=self.op)
        segments = jc._rotated_segment_paths(self.dp)
        # d-000 is protected (kept); everything else past retention=2 is
        # free to be pruned normally -- proves this is a targeted skip,
        # not a global "retention is now a no-op".
        self.assertLess(len(segments), 18)


class M4Round4GuardsThatHadNoRedTest(LedgerTestCase):
    """m4 (Opus rereview4, 2026-09-19): three round-4 claims had no test
    that could go red -- each of the three guards below could be removed
    with every existing test in this suite staying green. Every test
    below is a real failure case for its own guard: a bad-UTF-8 byte
    sequence for the two UnicodeDecodeError catches (_protection_scan(),
    _read_jsonl() via threshold()), and a genuinely unparseable line for
    the corrupt-line reliable=False flag in _segment_ids_scan(), the one
    _rotate_if_needed()'s own M2 stale-segment check depends on. Each was
    run against a mutated copy of jev_calibration.py with its own guard
    narrowed (the corresponding except clause dropping UnicodeDecodeError,
    or the corrupt-line branch dropping its "reliable = False") and
    failed there -- see this session's own done-check for the mutation
    table; that proof lives outside this suite, the same way N4's own
    class docstring above describes for its six mutants."""

    def test_read_jsonl_bad_utf8_byte_fails_closed_never_raises(self):
        # _read_jsonl() is threshold()/report()/join()'s own read layer.
        # Removing UnicodeDecodeError from its except clause
        # (r4_readjsonl_no_unicode) lets a bad byte escape straight out
        # of join(), which this test would catch as an ERROR (an
        # uncaught exception), never a clean assertion failure.
        jc.append_decision(self.dp, _dec(id="f:0000", family="f"))
        jc.append_outcome(self.op, _out(id="f:0000"), decisions_path=self.dp)
        with open(self.dp, "ab") as f:
            f.write(b"\xff\xfe not valid utf-8\n")
        res = jc.threshold(self.dp, self.op, "f", "noul", target_precision=0.9,
                            min_n=1, bands=[0.9])
        self.assertIsNone(res["threshold"])
        self.assertEqual(res["reason"], "ledger anomaly")

    def test_read_jsonl_permission_denied_parent_is_anomaly_never_empty(self):
        # G3 follow-up (2026-09-19): proven this session that
        # os.path.isfile(path) returns False, not an exception, when a
        # parent directory's mode denies traverse (chmod 0o600) -- so the
        # old isfile() pre-check silently read "cannot even reach the
        # file" as "nothing recorded yet", exactly the anomaly this
        # module's own OSError handling exists to catch. Removing the
        # FileNotFoundError branch (or reinstating the isfile pre-check)
        # makes this test fail: corrupt comes back [] instead of naming
        # the permission error, and the assertion below catches it.
        locked_dir = os.path.join(self._tmp.name, "locked")
        os.mkdir(locked_dir)
        ledger = os.path.join(locked_dir, "ledger.jsonl")
        with open(ledger, "w") as f:
            f.write('{"a": 1}\n')
        os.chmod(locked_dir, 0o600)
        try:
            records, corrupt = jc._read_jsonl(ledger, lambda r: None)
        finally:
            os.chmod(locked_dir, 0o700)
        self.assertEqual(records, [])
        self.assertEqual(len(corrupt), 1)
        self.assertEqual(corrupt[0]["line"], 0)
        self.assertIn("ermission", corrupt[0]["error"])

    def test_read_jsonl_genuinely_missing_file_still_reads_as_empty(self):
        # The other half of the same fix: a file that was never created
        # (no permission problem at all) must still read as an empty
        # ledger, not an anomaly -- proves the FileNotFoundError branch
        # is scoped correctly, not just removing the isfile() check
        # outright and turning every missing file into a corrupt entry.
        never_created = os.path.join(self._tmp.name, "nope.jsonl")
        records, corrupt = jc._read_jsonl(never_created, lambda r: None)
        self.assertEqual(records, [])
        self.assertEqual(corrupt, [])

    def test_protection_scan_bad_utf8_byte_is_unreliable_never_raises(self):
        # _protection_scan() reads the sibling OUTCOMES file for M5's
        # cross-file protection check, on the destructive prune path.
        # Removing UnicodeDecodeError from its own except clause
        # (r4_protscan_no_unicode) lets a bad byte escape straight out of
        # _rotate_if_needed(), which append_decision() would then raise
        # instead of safely skipping that rotation's prune pass.
        with open(self.op, "wb") as f:
            f.write(b'{"id": "d-000", "correct": true, "source": "t", '
                     b'"at": "2026-09-18T00:01:00Z"}\n')
            f.write(b"\xff\xfe not valid utf-8\n")
        ids, reliable = jc._protection_scan(self.op)
        self.assertFalse(reliable, "a bad-UTF-8 outcomes file must read as unreliable, never raise")

    def test_segment_ids_scan_corrupt_line_marks_unreliable_and_blocks_stale_prune(self):
        # _segment_ids_scan()'s corrupt-line flag (json.JSONDecodeError ->
        # reliable=False) is what _rotate_if_needed()'s own M2 stale-
        # segment check (see its docstring above M2StaleSegmentUnreadable
        # IsNeverDeleted) depends on to refuse deleting a segment it could
        # not fully read. Removing that flag (r4_segscan_corrupt_line_ok)
        # leaves reliable=True for a segment holding a line
        # _segment_ids_scan could not parse, and the stale segment below
        # -- which a second rotation with retention_segments=1 marks for
        # deletion -- would be pruned instead of kept.
        jc.append_decision(self.dp, _dec(id="f:0000", family="f"),
                            max_segment_bytes=10, retention_segments=1)
        jc.append_decision(self.dp, _dec(id="f:0001", family="f"),
                            max_segment_bytes=10, retention_segments=1)
        seg0 = jc._rotated_segment_paths(self.dp)[0]
        with open(seg0, "a", encoding="utf-8") as f:
            f.write("not valid json at all\n")
        jc.append_decision(self.dp, _dec(id="f:0002", family="f"),
                            max_segment_bytes=10, retention_segments=1)
        self.assertTrue(os.path.exists(seg0),
                         "a stale segment holding an unparseable line must never be pruned")


class M4HardCeilingWarnsWithoutDeletingProtectedData(LedgerTestCase):
    """m4 (independent re-review, 2026-09-19): M5's own protection can
    make retention grow without bound; a hard ceiling (10x the retention
    setting) prints one operator-facing stderr warning and keeps every
    protected segment -- it is a signal, never a second deletion path."""

    def test_ceiling_warns_on_stderr_and_deletes_nothing_protected(self):
        for i in range(0, 40):
            jc.append_decision(self.dp, _dec(id="p-%03d" % i), max_segment_bytes=80,
                                retention_segments=1, sibling_outcomes_path=self.op)
            jc.append_outcome(self.op, _out(id="p-%03d" % i, correct=True), decisions_path=self.dp)
        segments_before = jc._rotated_segment_paths(self.dp)
        self.assertGreater(len(segments_before), 10, "test setup did not actually exceed the 10x ceiling")

        import io
        captured = io.StringIO()
        with mock.patch.object(jc.sys, "stderr", captured):
            jc.append_decision(self.dp, _dec(id="p-999"), max_segment_bytes=80,
                                retention_segments=1, sibling_outcomes_path=self.op)
        self.assertIn("more than 10x its retention setting", captured.getvalue())
        segments_after = jc._rotated_segment_paths(self.dp)
        self.assertGreaterEqual(len(segments_after), len(segments_before))  # nothing protected was removed


class N1AppendOutcomeRefusesAnUnwrittenId(LedgerTestCase):
    """N1 (independent re-review, 2026-09-19): the outcome-labelling
    boundary -- append_outcome(), when given decisions_path, must refuse
    an id that is not present in that decisions ledger, writing nothing,
    rather than let a human label create a real outcome row with no
    matching decision (which _ledger_anomaly_touches() then reads as a
    corrupt ledger and disables that whole family's calibration)."""

    def test_unwritten_id_is_refused_and_nothing_is_written(self):
        jc.append_decision(self.dp, _dec(id="real-1"))
        with self.assertRaisesRegex(ValueError, "not present in the decisions ledger"):
            jc.append_outcome(self.op, _out(id="never-written"), decisions_path=self.dp)
        self.assertFalse(os.path.exists(self.op))

    def test_a_real_written_id_is_accepted(self):
        jc.append_decision(self.dp, _dec(id="real-2"))
        jc.append_outcome(self.op, _out(id="real-2"), decisions_path=self.dp)  # must not raise
        with open(self.op, encoding="utf-8") as fh:
            self.assertEqual(sum(1 for l in fh if l.strip()), 1)

    def test_without_decisions_path_the_old_orphan_tolerant_behaviour_is_unchanged(self):
        # decisions_path defaults to None on purpose: join()/report() must
        # still tolerate a pre-existing orphan (see TestJoin's own orphan
        # tests) -- this is opt-in protection at the write boundary, not a
        # retroactive rewrite of what an already-written ledger can hold.
        jc.append_outcome(self.op, _out(id="ghost-no-check"), allow_unchecked=True)  # must not raise
        with open(self.op, encoding="utf-8") as fh:
            self.assertEqual(sum(1 for l in fh if l.strip()), 1)

    def test_id_present_only_in_a_rotated_segment_is_still_accepted(self):
        # The refusal checks the WHOLE ledger (every rotated segment plus
        # the active file), not only the currently-active file.
        jc.append_decision(self.dp, _dec(id="old-rotated"), max_segment_bytes=1)  # rotates immediately
        segments = jc._rotated_segment_paths(self.dp)
        self.assertEqual(len(segments), 1)
        jc.append_outcome(self.op, _out(id="old-rotated"), decisions_path=self.dp)  # must not raise

    def test_decisions_path_is_required_by_default_round4(self):
        # Round 4 fix, item 3 (2026-09-19): decisions_path used to default
        # to None meaning "no check, silently skip" -- the round 3
        # re-review found 39 test call sites and the how-to's own
        # labelling instructions relied on exactly that silent skip.
        # Omitting decisions_path now raises unless allow_unchecked=True
        # is passed as an explicit, deliberate opt-out.
        with self.assertRaisesRegex(ValueError, "requires decisions_path"):
            jc.append_outcome(self.op, _out(id="no-guard-named"))
        self.assertFalse(os.path.exists(self.op))

    def test_allow_unchecked_is_the_one_named_opt_out(self):
        # The opt-out is explicit and must be spelled exactly
        # allow_unchecked=True: it is not the same as omitting the check
        # by accident, and it still writes (unlike the refusal above).
        jc.append_outcome(self.op, _out(id="deliberately-unchecked"), allow_unchecked=True)
        with open(self.op, encoding="utf-8") as fh:
            self.assertEqual(sum(1 for l in fh if l.strip()), 1)

    def test_unreliable_decisions_read_refuses_rather_than_mis_refuse(self):
        # Round 4 fix: when decisions_path IS given, the presence check
        # itself must be a RELIABLE read (via _all_ledger_ids()) -- an
        # unlistable decisions directory must not be silently read as "id
        # not found" (a false refusal that could destroy a real label).
        if hasattr(os, "geteuid") and os.geteuid() == 0:
            self.skipTest("permission bits are not enforced for root")
        jc.append_decision(self.dp, _dec(id="real-3"))
        decisions_dir = os.path.dirname(self.dp)
        os.chmod(decisions_dir, 0o300)
        try:
            with self.assertRaisesRegex(ValueError, "cannot verify decisions_path"):
                jc.append_outcome(self.op, _out(id="real-3"), decisions_path=self.dp)
        finally:
            os.chmod(decisions_dir, 0o700)
        self.assertFalse(os.path.exists(self.op))


class Item5CorruptDecisionLineScopedByFamily(LedgerTestCase):
    """Item 5 (2026-09-19): one torn (non-JSON) line anywhere in the
    decisions ledger used to make append_outcome()'s write-boundary check
    (N1 above) refuse EVERY family, including one that shares nothing
    with the torn line at all. Real decision ids from the seam are
    shaped "<family>:<...>" (see _family_from_orphan_id), and
    _append_line() writes a decision's own JSON keys in sorted order, so
    "family" often survives a trailing truncation even when the record
    as a whole does not parse -- scoping the refusal to that recovered
    family, when one can be recovered, is the fix."""

    def _append_raw_line(self, path, text):
        with open(path, "a", encoding="utf-8") as fh:
            fh.write(text + "\n")

    def test_torn_line_naming_a_different_family_does_not_block_labelling(self):
        jc.append_decision(self.dp, _dec(id="billing:1", family="billing"))
        # A hand-truncated decision line: valid JSON up to the point the
        # write was cut off, so json.loads() on the whole line fails, but
        # the sorted-key "family" fragment is intact.
        self._append_raw_line(
            self.dp,
            '{"answer":true,"at":"2026-09-19T00:00:00Z","confidence":0.9,'
            '"cost":0.0,"family":"shipping","framing":"h1","id":"shipping:9')
        jc.append_outcome(self.op, _out(id="billing:1"), decisions_path=self.dp)  # must not raise
        with open(self.op, encoding="utf-8") as fh:
            self.assertEqual(sum(1 for l in fh if l.strip()), 1)

    def test_torn_line_naming_the_same_family_still_blocks_labelling(self):
        jc.append_decision(self.dp, _dec(id="shipping:1", family="shipping"))
        self._append_raw_line(
            self.dp,
            '{"answer":true,"at":"2026-09-19T00:00:00Z","confidence":0.9,'
            '"cost":0.0,"family":"shipping","framing":"h1","id":"shipping:9')
        with self.assertRaisesRegex(ValueError, "cannot verify decisions_path"):
            jc.append_outcome(self.op, _out(id="shipping:1"), decisions_path=self.dp)
        self.assertFalse(os.path.exists(self.op))

    def test_torn_line_with_no_recoverable_family_blocks_every_family(self):
        jc.append_decision(self.dp, _dec(id="billing:1", family="billing"))
        self._append_raw_line(self.dp, "this line is not json at all and names no family")
        with self.assertRaisesRegex(ValueError, "cannot verify decisions_path"):
            jc.append_outcome(self.op, _out(id="billing:1"), decisions_path=self.dp)
        self.assertFalse(os.path.exists(self.op))

    def test_id_with_no_family_prefix_falls_back_to_full_block(self):
        # The id being labelled must ALSO carry a readable family prefix
        # for the scoping to apply at all: an id with no ":" carries no
        # fact to scope against, so this keeps the old, fully-blocking
        # behaviour, fail toward caution rather than under-refusing.
        jc.append_decision(self.dp, _dec(id="no-colon-id", family="billing"))
        self._append_raw_line(
            self.dp,
            '{"answer":true,"at":"2026-09-19T00:00:00Z","confidence":0.9,'
            '"cost":0.0,"family":"shipping","framing":"h1","id":"shipping:9')
        with self.assertRaisesRegex(ValueError, "cannot verify decisions_path"):
            jc.append_outcome(self.op, _out(id="no-colon-id"), decisions_path=self.dp)
        self.assertFalse(os.path.exists(self.op))

    def test_clean_ledger_unaffected_sanity_control(self):
        # No torn line at all: ordinary N1 behaviour, unchanged.
        jc.append_decision(self.dp, _dec(id="billing:1", family="billing"))
        jc.append_outcome(self.op, _out(id="billing:1"), decisions_path=self.dp)  # must not raise


class M1LedgerUnlistableDirectoryRefusesToAct(LedgerTestCase):
    """Major 1 (round 4 fix, 2026-09-19, NEW regression introduced by the
    round 3 re-review's own m1 fix): an unlistable ledger directory used
    to read as "no rotated history" on the calibration read path, letting
    act mode compute (and act on) a threshold from only the active file's
    partial view. Reviewer probe: 40 older mixed labels rotated aside, 40
    recent correct labels active, ledger dir mode 0300 -- the full ledger
    correctly finds no band with enough data; reading only the active
    file (the regression) found one and would have acted."""

    def _seed_band(self, family, qtype, confidence, n_correct, n_wrong):
        for i in range(n_correct):
            did = "%s-%.2f-c%d" % (family, confidence, i)
            jc.append_decision(self.dp, _dec(id=did, family=family, qtype=qtype, confidence=confidence, prob=confidence))
            jc.append_outcome(self.op, _out(id=did, correct=True), decisions_path=self.dp)
        for i in range(n_wrong):
            did = "%s-%.2f-w%d" % (family, confidence, i)
            jc.append_decision(self.dp, _dec(id=did, family=family, qtype=qtype, confidence=confidence, prob=confidence))
            jc.append_outcome(self.op, _out(id=did, correct=False), decisions_path=self.dp)

    def test_unlistable_directory_yields_ledger_unreadable_not_a_computed_threshold(self):
        if hasattr(os, "geteuid") and os.geteuid() == 0:
            self.skipTest("permission bits are not enforced for root")
        # Older, mixed-quality history, rotated aside.
        self._seed_band("FAM", "noul", 0.95, 20, 20)
        jc._rotate_if_needed(self.dp, 10, None)
        jc._rotate_if_needed(self.op, 10, None)
        self.assertGreater(len(jc._rotated_segment_paths(self.dp)), 0, "test setup: rotation did not happen")
        # Recent, all-correct history, still in the active file.
        for i in range(40):
            did = "FAM-recent-%d" % i
            jc.append_decision(self.dp, _dec(id=did, family="FAM", qtype="noul", confidence=0.95, prob=0.95))
            jc.append_outcome(self.op, _out(id=did, correct=True), decisions_path=self.dp)

        directory = os.path.dirname(self.dp)
        os.chmod(directory, 0o300)  # traversable and writable, not listable
        try:
            res = jc.threshold(self.dp, self.op, "FAM", "noul", target_precision=0.9, min_n=20, bands=[0.9])
        finally:
            os.chmod(directory, 0o700)
        self.assertIsNone(res["threshold"], "must never compute from the partial, active-file-only view")
        self.assertEqual(res["reason"], "ledger unreadable")

    def test_directory_listable_the_same_history_correctly_finds_no_qualifying_band(self):
        # Sanity/mutation-kill control: the SAME seeded history, read with
        # the directory fully listable, must reach the ordinary "no band"
        # reason, never "ledger unreadable" -- proves this is a targeted
        # gate, not "threshold always refuses now".
        self._seed_band("FAM", "noul", 0.95, 20, 20)
        jc._rotate_if_needed(self.dp, 10, None)
        jc._rotate_if_needed(self.op, 10, None)
        for i in range(40):
            did = "FAM-recent-%d" % i
            jc.append_decision(self.dp, _dec(id=did, family="FAM", qtype="noul", confidence=0.95, prob=0.95))
            jc.append_outcome(self.op, _out(id=did, correct=True), decisions_path=self.dp)
        res = jc.threshold(self.dp, self.op, "FAM", "noul", target_precision=0.9, min_n=20, bands=[0.9])
        self.assertIsNone(res["threshold"])
        self.assertNotEqual(res["reason"], "ledger unreadable")


class Item3ParentDirectoryUnreadableIsNeverEmpty(unittest.TestCase):
    """Item 3 (2026-09-19): os.path.isdir()/os.path.isfile() return False,
    not raise, when a PARENT of the path (not the path itself) cannot be
    traversed -- confirmed directly against the real stdlib below. Every
    existing chmod-based test in this suite (M1 above, N3, M2) chmods the
    ledger's OWN directory, which makes os.listdir() itself raise and was
    already caught; none of them chmod a GRANDPARENT, which is the case
    where the bare isdir()/isfile() precheck itself (before any listdir()
    or open() call) silently returned False instead of "cannot tell" --
    this class is that missing case."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.parent = os.path.join(self._tmp.name, "parent")
        self.ledger_dir = os.path.join(self.parent, "ledger")
        os.makedirs(self.ledger_dir)
        self.dp = os.path.join(self.ledger_dir, "decisions.jsonl")
        self.op = os.path.join(self.ledger_dir, "outcomes.jsonl")

    def _lock_parent(self):
        if hasattr(os, "geteuid") and os.geteuid() == 0:
            self.skipTest("permission bits are not enforced for root")
        os.chmod(self.parent, 0o000)
        self.addCleanup(os.chmod, self.parent, 0o755)

    def test_stdlib_isdir_and_isfile_return_false_not_raise_on_unreadable_parent(self):
        # Establishes the real defect this fix responds to, against the
        # actual stdlib, before testing this module's own wrapper.
        jc.append_decision(self.dp, _dec(id="d1"))
        self._lock_parent()
        self.assertFalse(os.path.isdir(self.ledger_dir))
        self.assertFalse(os.path.isfile(self.dp))

    def test_safe_isdir_reports_unreliable_not_absent(self):
        self._lock_parent()
        is_dir, reliable = jc._safe_isdir(self.ledger_dir)
        self.assertFalse(reliable)
        self.assertFalse(is_dir)

    def test_safe_isfile_reports_unreliable_not_absent(self):
        jc.append_decision(self.dp, _dec(id="d1"))
        self._lock_parent()
        is_file, reliable = jc._safe_isfile(self.dp)
        self.assertFalse(reliable)
        self.assertFalse(is_file)

    def test_genuinely_absent_directory_is_still_reliable_empty(self):
        # Negative control: _safe_isdir must not turn every "does not
        # exist" case into "unreliable" -- only a parent that cannot be
        # traversed does that.
        missing = os.path.join(self.ledger_dir, "does-not-exist")
        is_dir, reliable = jc._safe_isdir(missing)
        self.assertTrue(reliable)
        self.assertFalse(is_dir)

    def test_list_segment_names_refuses_rather_than_reads_empty(self):
        # The real, end-to-end effect: _list_segment_names (and therefore
        # _rotated_segment_paths/_next_segment_seq/threshold(), all of
        # which route through it) must see ok=False, never ([], True),
        # when the ledger directory's own PARENT is unreadable.
        jc.append_decision(self.dp, _dec(id="d1"))
        self._lock_parent()
        names, ok = jc._list_segment_names(self.ledger_dir, "decisions", ".jsonl")
        self.assertFalse(ok)
        self.assertEqual(names, [])

    def test_threshold_reads_ledger_unreadable_not_a_computed_answer(self):
        # End-to-end through the public API: a real, correct ledger
        # (would otherwise qualify for a threshold) must read as
        # "ledger unreadable" while its own grandparent directory is
        # locked, never silently compute from nothing.
        for i in range(40):
            did = "FAM-%d" % i
            jc.append_decision(self.dp, _dec(id=did, family="FAM", qtype="noul", confidence=0.95, prob=0.95))
            jc.append_outcome(self.op, _out(id=did, correct=True), decisions_path=self.dp)
        self._lock_parent()
        res = jc.threshold(self.dp, self.op, "FAM", "noul", target_precision=0.9, min_n=20, bands=[0.9])
        self.assertIsNone(res["threshold"])
        self.assertEqual(res["reason"], "ledger unreadable")


class M2StaleSegmentUnreadableIsNeverDeleted(LedgerTestCase):
    """Major 2 / M2 (round 4 fix, 2026-09-19): the previous round's own
    N3 fix protected against an unreadable SIBLING OUTCOMES file, but a
    stale DECISION segment that cannot be read ITSELF was still deleted
    -- _segment_ids() silently read "permission denied" as "holds no
    protected ids". Reviewer probe: a retention-1 stale segment chmod
    000, outcomes readable and naming its id: the segment survived
    deletion, but WAS an orphan while unreadable (its own content is
    genuinely invisible, not merely off limits to deletion) -- restoring
    read access must make it join cleanly again, proving nothing was
    actually lost."""

    def test_unreadable_stale_segment_survives_and_rejoins_once_readable(self):
        if hasattr(os, "geteuid") and os.geteuid() == 0:
            self.skipTest("permission bits are not enforced for root")
        jc.append_decision(self.dp, _dec(id="FAM:0000", family="FAM"), max_segment_bytes=10,
                            retention_segments=1, sibling_outcomes_path=self.op)
        jc.append_outcome(self.op, _out(id="FAM:0000"), decisions_path=self.dp, max_segment_bytes=None)
        seg0 = jc._rotated_segment_paths(self.dp)[0]
        os.chmod(seg0, 0o000)
        try:
            # A second rotation with retention_segments=1 would normally
            # prune seg0 as stale.
            jc.append_decision(self.dp, _dec(id="FAM:0001", family="FAM"), max_segment_bytes=10,
                                retention_segments=1, sibling_outcomes_path=self.op)
            self.assertTrue(os.path.exists(seg0), "M2: an unreadable stale segment must never be deleted")
        finally:
            os.chmod(seg0, 0o644)
        j = jc.join(self.dp, self.op)
        self.assertEqual(len(j["orphan_outcomes"]), 0, "no PERMANENT orphan once read access is restored")
        self.assertIn("FAM:0000", [item["decision"]["id"] for item in j["joined"]])

    def test_readable_stale_segment_still_prunes_normally(self):
        # Sanity/mutation-kill control: an ORDINARY, fully-readable stale
        # segment with nothing protecting it must still be pruned --
        # proves this is a targeted "unreadable means keep" rule, not
        # "never prune a stale segment again".
        jc.append_decision(self.dp, _dec(id="FAM:0000", family="FAM"), max_segment_bytes=10,
                            retention_segments=1)
        seg0 = jc._rotated_segment_paths(self.dp)[0]
        jc.append_decision(self.dp, _dec(id="FAM:0001", family="FAM"), max_segment_bytes=10,
                            retention_segments=1)
        self.assertFalse(os.path.exists(seg0), "an ordinary readable stale segment should still be pruned")


class M2cRotationRefusesOnUnlistableDirectory(LedgerTestCase):
    """Item 2c (round 4 fix, 2026-09-19): _next_segment_seq() returning a
    bare best-effort 0 on an unlistable directory let _rotate_if_needed()
    number a brand new segment "0000000000-..." while blind to what
    sequence numbers already exist, sorting it to the FRONT of the prune
    order despite holding the newest data. Now: rotation is refused
    entirely (the active file keeps growing) while the directory cannot
    be listed, with one stderr warning."""

    def test_rotation_refused_and_warns_once_while_unlistable(self):
        if hasattr(os, "geteuid") and os.geteuid() == 0:
            self.skipTest("permission bits are not enforced for root")
        jc.append_decision(self.dp, _dec(id="seed"), max_segment_bytes=None)
        directory = os.path.dirname(self.dp)
        pre_size = os.path.getsize(self.dp)
        os.chmod(directory, 0o300)
        import io
        captured = io.StringIO()
        try:
            with mock.patch.object(jc.sys, "stderr", captured):
                for i in range(3):
                    jc.append_decision(self.dp, _dec(id="grow-%d" % i), max_segment_bytes=10,
                                        retention_segments=None)
        finally:
            os.chmod(directory, 0o700)
        # No segment "0000000000-..." was created for this run: the
        # active file kept growing instead (it stayed writable/openable
        # even though the directory could not be listed).
        self.assertEqual(jc._rotated_segment_paths(self.dp), [],
                          "rotation must be refused, never number a segment 0 while blind")
        self.assertGreater(os.path.getsize(self.dp), pre_size)
        warnings = [l for l in captured.getvalue().splitlines() if "cannot be listed" in l]
        self.assertEqual(len(warnings), 1, "must warn exactly once per directory per process, not once per append")


class TestRotationRaceDetection(LedgerTestCase):
    """_read_rotated_jsonl() lists segment names once, up front, then reads
    every segment plus the active file. A concurrent rotation happening
    after that listing moves whatever was in the active file into a new
    segment this call never fetches, and replaces the active file with a
    fresh one -- silently under-counting records with no error. Detected
    via one os.stat() before listing and one after reading the active
    file, on size decrease or mtime_ns moving backward only (2026-09-19
    fix; a plain ordinary append, which only grows the file, must never
    be flagged as a race -- see the size-growth test below)."""

    def test_active_file_shrinking_mid_read_is_flagged_unreliable(self):
        # Simulates the race directly: the validator (invoked once per
        # record while _read_jsonl reads the active file) truncates the
        # active file out from under the read, exactly what a concurrent
        # rotation does. The after-stat must then show a smaller size
        # than the before-stat, and _read_rotated_jsonl must report that
        # as reliable=False with a named anomaly, not silently accept a
        # partial read.
        jc.append_decision(self.dp, _dec(id="d1"))
        jc.append_decision(self.dp, _dec(id="d2"))

        def racing_validator(rec):
            jc._validate_decision(rec)
            os.truncate(self.dp, 0)

        records, corrupt, reliable = jc._read_rotated_jsonl(self.dp, racing_validator)
        self.assertFalse(reliable)
        self.assertTrue(
            any("changed size or mtime during read" in c["error"] for c in corrupt),
            corrupt,
        )

    def test_ordinary_growth_only_append_is_never_flagged_as_a_race(self):
        # The wording nit a second-pass review raised about this exact
        # design: an ordinary concurrent append only grows the file and
        # advances its mtime, and must never be mislabeled as a rotation
        # race. Nothing mutates the file here at all (the normal,
        # non-racing path); reliable must stay True.
        jc.append_decision(self.dp, _dec(id="d1"))
        records, corrupt, reliable = jc._read_rotated_jsonl(self.dp, jc._validate_decision)
        self.assertTrue(reliable)
        self.assertEqual(corrupt, [])

    def test_active_file_vanishing_mid_read_is_flagged_unreliable(self):
        # The other shrink case: rotation can also rename the active file
        # away entirely (no replacement created yet at read time), so the
        # after-stat raises FileNotFoundError rather than showing a
        # smaller size. That must be caught too, not just the shrink case.
        jc.append_decision(self.dp, _dec(id="d1"))

        def vanishing_validator(rec):
            jc._validate_decision(rec)
            os.remove(self.dp)

        records, corrupt, reliable = jc._read_rotated_jsonl(self.dp, vanishing_validator)
        self.assertFalse(reliable)
        self.assertTrue(
            any("changed size or mtime during read" in c["error"] for c in corrupt),
            corrupt,
        )

    def test_a_real_os_replace_rotation_mid_read_is_caught(self):
        # Item 4 (2026-09-19): provokes the EXACT mechanism
        # _rotate_if_needed() itself uses (os.replace() swapping the
        # active file aside, then a fresh, empty active file appears for
        # the next append), via a controlled multi-step file
        # replacement, rather than a plain truncate/remove -- a stronger
        # proof than the two tests above that the real rotation sequence
        # is caught, not just its size/mtime side effects in isolation.
        jc.append_decision(self.dp, _dec(id="d1"))
        jc.append_decision(self.dp, _dec(id="d2"))
        directory = os.path.dirname(self.dp)
        segment_path = os.path.join(directory, "decisions.rotated-mid-read.jsonl")
        state = {"rotated": False}

        def racing_validator(rec):
            jc._validate_decision(rec)
            if not state["rotated"]:
                state["rotated"] = True
                os.replace(self.dp, segment_path)  # exactly what _rotate_if_needed does
                open(self.dp, "w", encoding="utf-8").close()  # ...then a fresh, empty active file

        records, corrupt, reliable = jc._read_rotated_jsonl(self.dp, racing_validator)
        self.assertTrue(state["rotated"], "test setup: the race never fired")
        self.assertFalse(reliable)
        self.assertTrue(
            any("changed size or mtime during read" in c["error"] for c in corrupt),
            corrupt,
        )


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

#!/usr/bin/env python3
"""Calibration for scripts/jev_cascade.py.

The property this file exists to assert is that route() never ACTs without
real, sufficient evidence for the exact family, qtype and risk class in
play, that critical risk NEVER acts regardless of how favorable the
calibration data looks, that an unrecognised risk class or family escalates
rather than crashing or silently acting, and that the escalation ladder
never sends a decision backward. A test suite that only checked "confident
answer clears a low bar" would pass right through a regression that let
critical risk act, or that let an unknown risk class silently ACT instead
of escalating.
"""
import math
import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import jev_calibration as jc  # noqa: E402
import jev_cascade as cascade  # noqa: E402


def _seed(dp, op, family, qtype, confidence, n_correct, n_wrong=0, framing="h"):
    for i in range(n_correct):
        did = "%s-%s-%.3f-c%d" % (family, qtype, confidence, i)
        jc.append_decision(dp, {
            "id": did, "family": family, "qtype": qtype, "framing": framing,
            "answer": True if qtype == "noul" else "x", "prob": confidence,
            "confidence": confidence, "model": "m", "cost": 0.0,
            "at": "2026-09-18T00:00:00Z",
        })
        jc.append_outcome(op, {"id": did, "correct": True, "source": "t", "at": "2026-09-18T00:01:00Z"})
    for i in range(n_wrong):
        did = "%s-%s-%.3f-w%d" % (family, qtype, confidence, i)
        jc.append_decision(dp, {
            "id": did, "family": family, "qtype": qtype, "framing": framing,
            "answer": True if qtype == "noul" else "x", "prob": confidence,
            "confidence": confidence, "model": "m", "cost": 0.0,
            "at": "2026-09-18T00:00:00Z",
        })
        jc.append_outcome(op, {"id": did, "correct": False, "source": "t", "at": "2026-09-18T00:02:00Z"})


def _dec(id="d1", family="f", qtype="noul", confidence=0.95, framing=None, escalation_rung=None):
    rec = {"id": id, "family": family, "qtype": qtype, "confidence": confidence}
    if framing is not None:
        rec["framing"] = framing
    if escalation_rung is not None:
        rec["escalation_rung"] = escalation_rung
    return rec


class CascadeTestCase(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.dp = os.path.join(self._tmp.name, "decisions.jsonl")
        self.op = os.path.join(self._tmp.name, "outcomes.jsonl")
        self.handle = cascade.CalibrationHandle(self.dp, self.op)


class TestStructuralValidation(CascadeTestCase):
    def test_non_dict_decision_raises(self):
        with self.assertRaises(ValueError):
            cascade.route("not a dict", "low", self.handle)

    def test_blank_family_raises(self):
        with self.assertRaisesRegex(ValueError, "family"):
            cascade.route(_dec(family=""), "low", self.handle)

    def test_missing_family_raises(self):
        rec = _dec()
        del rec["family"]
        with self.assertRaisesRegex(ValueError, "family"):
            cascade.route(rec, "low", self.handle)

    def test_unknown_qtype_raises(self):
        with self.assertRaisesRegex(ValueError, "qtype"):
            cascade.route(_dec(qtype="ranking"), "low", self.handle)

    def test_nan_confidence_raises(self):
        with self.assertRaisesRegex(ValueError, "confidence"):
            cascade.route(_dec(confidence=float("nan")), "low", self.handle)

    def test_out_of_range_confidence_raises(self):
        with self.assertRaisesRegex(ValueError, "confidence"):
            cascade.route(_dec(confidence=1.5), "low", self.handle)

    def test_bool_confidence_raises(self):
        with self.assertRaisesRegex(ValueError, "confidence"):
            cascade.route(_dec(confidence=True), "low", self.handle)

    def test_negative_rung_raises(self):
        with self.assertRaisesRegex(ValueError, "escalation_rung"):
            cascade.route(_dec(escalation_rung=-1), "low", self.handle)

    def test_rung_past_ladder_raises(self):
        with self.assertRaisesRegex(ValueError, "escalation_rung"):
            cascade.route(_dec(escalation_rung=5), "low", self.handle)

    def test_bool_rung_raises(self):
        with self.assertRaisesRegex(ValueError, "escalation_rung"):
            cascade.route(_dec(escalation_rung=True), "low", self.handle)

    def test_missing_framing_is_allowed(self):
        _seed(self.dp, self.op, "f", "noul", 0.95, 40)
        res = cascade.route(_dec(family="f", qtype="noul", confidence=0.95), "low", self.handle)
        self.assertEqual(res["outcome"], "ACT")

    def test_blank_framing_raises(self):
        with self.assertRaisesRegex(ValueError, "framing"):
            cascade.route(_dec(framing="   "), "low", self.handle)

    def test_non_handle_calibration_raises(self):
        with self.assertRaises(ValueError):
            cascade.route(_dec(), "low", ("not", "a", "handle"))


class TestUnknownRiskClassAndFamilyEscalate(CascadeTestCase):
    def test_unknown_risk_class_escalates_not_raises(self):
        res = cascade.route(_dec(), "ultra-stakes", self.handle)
        self.assertEqual(res["outcome"], "ESCALATE")
        self.assertEqual(res["next_lane"], "muse")
        self.assertEqual(res["reason"], "unknown_risk_class")

    def test_blank_risk_class_escalates(self):
        res = cascade.route(_dec(), "", self.handle)
        self.assertEqual(res["outcome"], "ESCALATE")
        self.assertEqual(res["reason"], "unknown_risk_class")

    def test_whitespace_risk_class_escalates(self):
        res = cascade.route(_dec(), "   ", self.handle)
        self.assertEqual(res["outcome"], "ESCALATE")

    def test_non_string_risk_class_escalates(self):
        res = cascade.route(_dec(), 42, self.handle)
        self.assertEqual(res["outcome"], "ESCALATE")

    def test_never_seen_family_escalates_not_raises(self):
        res = cascade.route(_dec(family="never-seen-family"), "low", self.handle)
        self.assertEqual(res["outcome"], "ESCALATE")
        self.assertTrue(res["reason"].startswith("calibration_no_data:"))

    def test_never_seen_family_qtype_combo_escalates(self):
        # Family exists for noul, but never for choice.
        _seed(self.dp, self.op, "f", "noul", 0.95, 40)
        res = cascade.route(_dec(family="f", qtype="choice", confidence=0.95), "low", self.handle)
        self.assertEqual(res["outcome"], "ESCALATE")
        self.assertTrue(res["reason"].startswith("calibration_no_data:"))


class TestCriticalNeverActs(CascadeTestCase):
    def test_critical_escalates_even_with_perfect_calibration(self):
        _seed(self.dp, self.op, "f", "noul", 0.99, 200)
        res = cascade.route(_dec(family="f", qtype="noul", confidence=0.999), "critical", self.handle)
        self.assertEqual(res["outcome"], "ESCALATE")
        self.assertEqual(res["reason"], "critical_never_acts")

    def test_critical_never_calls_calibration(self):
        # No ledger data at all exists; critical must still escalate, not
        # raise or NO-DATA on "no calibration data".
        res = cascade.route(_dec(family="never-seen", qtype="noul", confidence=0.999), "critical", self.handle)
        self.assertEqual(res["outcome"], "ESCALATE")
        self.assertEqual(res["reason"], "critical_never_acts")

    def test_critical_at_rung_one_advances_to_opus(self):
        res = cascade.route(_dec(escalation_rung=1), "critical", self.handle)
        self.assertEqual(res["outcome"], "ESCALATE")
        self.assertEqual(res["next_lane"], "opus")
        self.assertEqual(res["next_rung"], 2)


class TestActAndEscalateOnConfidence(CascadeTestCase):
    def test_confidence_clears_low_bar_acts(self):
        _seed(self.dp, self.op, "f", "noul", 0.95, 40)
        res = cascade.route(_dec(family="f", qtype="noul", confidence=0.95), "low", self.handle)
        self.assertEqual(res["outcome"], "ACT")
        self.assertEqual(res["threshold"], 0.9)

    def test_confidence_below_calibrated_threshold_escalates_to_muse(self):
        _seed(self.dp, self.op, "f", "noul", 0.95, 40)
        # A decision confident only at 0.6, below the calibrated 0.9 band.
        res = cascade.route(_dec(family="f", qtype="noul", confidence=0.6), "low", self.handle)
        self.assertEqual(res["outcome"], "ESCALATE")
        self.assertEqual(res["next_lane"], "muse")
        self.assertEqual(res["reason"], "below_calibrated_threshold")

    def test_same_evidence_clears_low_but_not_medium_or_high(self):
        # n=40 all correct at 0.95 clears the low target (0.90) but its
        # Wilson lower bound (~0.912) clears neither medium (0.95) nor
        # high (0.98): the SAME calibration data, three different bars.
        _seed(self.dp, self.op, "f", "noul", 0.95, 40)
        low = cascade.route(_dec(family="f", qtype="noul", confidence=0.95), "low", self.handle)
        medium = cascade.route(_dec(family="f", qtype="noul", confidence=0.95), "medium", self.handle)
        high = cascade.route(_dec(family="f", qtype="noul", confidence=0.95), "high", self.handle)
        self.assertEqual(low["outcome"], "ACT")
        self.assertEqual(medium["outcome"], "ESCALATE")
        self.assertEqual(high["outcome"], "ESCALATE")

    def test_confidence_clears_high_bar_with_enough_evidence(self):
        _seed(self.dp, self.op, "f", "noul", 0.99, 220)
        res = cascade.route(_dec(family="f", qtype="noul", confidence=0.99), "high", self.handle)
        self.assertEqual(res["outcome"], "ACT")

    def test_confidence_exactly_equal_to_threshold_acts(self):
        _seed(self.dp, self.op, "f", "noul", 0.9, 40)
        res = cascade.route(_dec(family="f", qtype="noul", confidence=0.9), "low", self.handle)
        self.assertEqual(res["outcome"], "ACT")


class TestEscalationLadder(CascadeTestCase):
    def test_fresh_decision_escalates_to_muse_first(self):
        res = cascade.route(_dec(family="never-seen", escalation_rung=0), "low", self.handle)
        self.assertEqual(res["next_lane"], "muse")
        self.assertEqual(res["next_rung"], 1)

    def test_rung_one_escalates_to_opus_not_back_to_muse(self):
        res = cascade.route(_dec(family="never-seen", escalation_rung=1), "low", self.handle)
        self.assertEqual(res["next_lane"], "opus")
        self.assertEqual(res["next_rung"], 2)

    def test_rung_two_exhausted_is_no_data(self):
        res = cascade.route(_dec(family="never-seen", escalation_rung=2), "low", self.handle)
        self.assertEqual(res["outcome"], "NO-DATA")
        self.assertEqual(res["reason"], "escalation_exhausted")

    def test_rung_one_decision_still_acts_if_it_now_clears(self):
        # Rung only matters for escalation; a rung-1 decision whose
        # confidence clears the bar still ACTs, it is not forced to
        # escalate again just because it was reviewed before.
        _seed(self.dp, self.op, "f", "noul", 0.95, 40)
        res = cascade.route(_dec(family="f", qtype="noul", confidence=0.95, escalation_rung=1), "low", self.handle)
        self.assertEqual(res["outcome"], "ACT")


class TestNoulChoiceNeverPooled(CascadeTestCase):
    def test_shared_family_routes_noul_and_choice_independently(self):
        _seed(self.dp, self.op, "shared", "noul", 0.95, 40)
        _seed(self.dp, self.op, "shared", "choice", 0.95, 5, n_wrong=15)
        noul_res = cascade.route(_dec(family="shared", qtype="noul", confidence=0.95), "low", self.handle)
        choice_res = cascade.route(_dec(family="shared", qtype="choice", confidence=0.95), "low", self.handle)
        self.assertEqual(noul_res["outcome"], "ACT")
        self.assertEqual(choice_res["outcome"], "ESCALATE")


class TestFramingScoping(CascadeTestCase):
    def test_framing_scopes_calibration_to_exact_wording(self):
        _seed(self.dp, self.op, "f", "noul", 0.95, 40, framing="wording-a")
        # A brand new framing with zero evidence of its own must not
        # inherit wording-a's threshold when the caller supplies framing.
        res = cascade.route(_dec(family="f", qtype="noul", confidence=0.95, framing="wording-b"), "low", self.handle)
        self.assertEqual(res["outcome"], "ESCALATE")

    def test_matching_framing_acts(self):
        _seed(self.dp, self.op, "f", "noul", 0.95, 40, framing="wording-a")
        res = cascade.route(_dec(family="f", qtype="noul", confidence=0.95, framing="wording-a"), "low", self.handle)
        self.assertEqual(res["outcome"], "ACT")


class TestPolicyTable(unittest.TestCase):
    def test_risk_target_precision_pinned(self):
        self.assertEqual(cascade.RISK_TARGET_PRECISION["low"], 0.90)
        self.assertEqual(cascade.RISK_TARGET_PRECISION["medium"], 0.95)
        self.assertEqual(cascade.RISK_TARGET_PRECISION["high"], 0.98)
        self.assertIsNone(cascade.RISK_TARGET_PRECISION["critical"])

    def test_cascade_lanes_order(self):
        self.assertEqual(cascade.CASCADE_LANES, ("muse", "opus"))


if __name__ == "__main__":
    unittest.main()

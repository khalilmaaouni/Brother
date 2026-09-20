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
import json
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
        jc.append_outcome(op, {"id": did, "correct": True, "source": "t", "at": "2026-09-18T00:01:00Z"}, decisions_path=dp)
    for i in range(n_wrong):
        did = "%s-%s-%.3f-w%d" % (family, qtype, confidence, i)
        jc.append_decision(dp, {
            "id": did, "family": family, "qtype": qtype, "framing": framing,
            "answer": True if qtype == "noul" else "x", "prob": confidence,
            "confidence": confidence, "model": "m", "cost": 0.0,
            "at": "2026-09-18T00:00:00Z",
        })
        jc.append_outcome(op, {"id": did, "correct": False, "source": "t", "at": "2026-09-18T00:02:00Z"}, decisions_path=dp)


def _dec(id="d1", family="f", qtype="noul", confidence=0.95, framing=None, escalation_rung=None, model=None):
    rec = {"id": id, "family": family, "qtype": qtype, "confidence": confidence}
    if framing is not None:
        rec["framing"] = framing
    if escalation_rung is not None:
        rec["escalation_rung"] = escalation_rung
    if model is not None:
        rec["model"] = model
    return rec


class CascadeTestCase(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.dp = os.path.join(self._tmp.name, "decisions.jsonl")
        self.op = os.path.join(self._tmp.name, "outcomes.jsonl")
        self.pp = os.path.join(self._tmp.name, "promotions.jsonl")
        # No promotion store by default: A0.7 means every ACT-path test
        # below must opt in via self._promote(), the same way it must opt
        # in via _seed() for calibration evidence. self.pp does not exist
        # on disk until the first self._promote() call, so this handle
        # exercises the "an empty promotion store" case on its own (see
        # TestSignedPromotion.test_calibrated_but_no_promotion_record_
        # escalates). promotions_path is passed EXPLICITLY here rather
        # than relying on CalibrationHandle's own default (M3, 2026-09-18:
        # the default is now DEFAULT_PROMOTIONS_PATH, a real repo-root
        # path, precisely so an unconfigured caller reads the shared
        # store instead of silently reading nothing; a test suite must
        # stay hermetic against that real path regardless of what, if
        # anything, is ever signed there).
        self.handle = cascade.CalibrationHandle(self.dp, self.op, promotions_path=self.pp)

    def _promote(self, family="f", qtype="noul", risk_class="low", model="m",
                 bound_at_signing=0.0, n_at_signing=1,
                 flip_condition="precision drops below target for two consecutive weeks",
                 signed_at="2026-09-18T00:00:00Z", revoke=False, review_by="2099-01-01"):
        """Appends one promotion (or, with revoke=True, one revoke) record
        to self.pp and returns a CalibrationHandle pointing at it (same
        decisions/outcomes paths as self.handle, promotions_path=self.pp).
        bound_at_signing defaults to 0.0 so a freshly-promoted record never
        itself trips "bound_not_met" against whatever the seeded ledger's
        real Wilson bound turns out to be. review_by defaults to a
        far-future sentinel (2099-01-01, the same convention
        test_battery_verdict.py already uses for "never expires in this
        test") so every existing test that does not care about item 2's
        expiry stays green without change."""
        rec = {
            "family": family, "qtype": qtype, "risk_class": risk_class, "model": model,
            "signed_by": "founder", "signed_at": signed_at,
        }
        if revoke:
            rec["revoke"] = True
        else:
            rec["bound_at_signing"] = bound_at_signing
            rec["n_at_signing"] = n_at_signing
            rec["flip_condition"] = flip_condition
            rec["review_by"] = review_by
        with open(self.pp, "a", encoding="utf-8") as f:
            f.write(json.dumps(rec) + "\n")
        return cascade.CalibrationHandle(self.dp, self.op, promotions_path=self.pp)


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
        handle = self._promote()
        res = cascade.route(_dec(family="f", qtype="noul", confidence=0.95, model="m"), "low", handle)
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
        handle = self._promote()
        res = cascade.route(_dec(family="f", qtype="noul", confidence=0.95, model="m"), "low", handle)
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
        # A promotion is signed per risk_class (see TestSignedPromotion),
        # so only the "low" call below has one: medium and high must stay
        # ESCALATE on the calibration axis alone, same as before A0.7.
        _seed(self.dp, self.op, "f", "noul", 0.95, 40)
        handle = self._promote(risk_class="low")
        low = cascade.route(_dec(family="f", qtype="noul", confidence=0.95, model="m"), "low", handle)
        medium = cascade.route(_dec(family="f", qtype="noul", confidence=0.95, model="m"), "medium", handle)
        high = cascade.route(_dec(family="f", qtype="noul", confidence=0.95, model="m"), "high", handle)
        self.assertEqual(low["outcome"], "ACT")
        self.assertEqual(medium["outcome"], "ESCALATE")
        self.assertEqual(high["outcome"], "ESCALATE")

    def test_confidence_clears_high_bar_with_enough_evidence(self):
        _seed(self.dp, self.op, "f", "noul", 0.99, 220)
        handle = self._promote(risk_class="high")
        res = cascade.route(_dec(family="f", qtype="noul", confidence=0.99, model="m"), "high", handle)
        self.assertEqual(res["outcome"], "ACT")

    def test_confidence_exactly_equal_to_threshold_acts(self):
        _seed(self.dp, self.op, "f", "noul", 0.9, 40)
        handle = self._promote()
        res = cascade.route(_dec(family="f", qtype="noul", confidence=0.9, model="m"), "low", handle)
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
        handle = self._promote()
        res = cascade.route(_dec(family="f", qtype="noul", confidence=0.95, escalation_rung=1, model="m"), "low", handle)
        self.assertEqual(res["outcome"], "ACT")


class TestNoulChoiceNeverPooled(CascadeTestCase):
    def test_shared_family_routes_noul_and_choice_independently(self):
        _seed(self.dp, self.op, "shared", "noul", 0.95, 40)
        _seed(self.dp, self.op, "shared", "choice", 0.95, 5, n_wrong=15)
        handle = self._promote(family="shared", qtype="noul")
        noul_res = cascade.route(_dec(family="shared", qtype="noul", confidence=0.95, model="m"), "low", handle)
        choice_res = cascade.route(_dec(family="shared", qtype="choice", confidence=0.95, model="m"), "low", handle)
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
        handle = self._promote()
        res = cascade.route(_dec(family="f", qtype="noul", confidence=0.95, framing="wording-a", model="m"), "low", handle)
        self.assertEqual(res["outcome"], "ACT")


class TestSignedPromotion(CascadeTestCase):
    """A0.7 (2026-09-18): a calibrated, confident decision still needs a
    founder-signed promotion record naming its exact (family, qtype,
    risk_class, model) before route() will ACT."""

    def test_below_target_precision_escalates_regardless_of_promotion(self):
        # 34 of 34 at confidence 0.95: Wilson lower bound ~0.8985, just
        # under the low target of 0.90, so threshold() finds no
        # qualifying band before promotion is ever consulted. A promotion
        # record is present here specifically to prove IT is not what is
        # escalating this decision.
        _seed(self.dp, self.op, "f", "noul", 0.95, 34)
        handle = self._promote()
        res = cascade.route(_dec(family="f", qtype="noul", confidence=0.95, model="m"), "low", handle)
        self.assertEqual(res["outcome"], "ESCALATE")
        self.assertTrue(res["reason"].startswith("calibration_no_data:"))

    def test_calibrated_but_no_promotion_record_escalates(self):
        # 35 of 35: Wilson lower bound ~0.9011 clears the low target, so
        # this would ACT under the pre-A0.7 rule. self.handle's
        # promotions_path (self.pp) does not exist on disk yet: an empty
        # promotion store means "no promotion" (see the module docstring),
        # never a silent ACT.
        _seed(self.dp, self.op, "f", "noul", 0.95, 35)
        res = cascade.route(_dec(family="f", qtype="noul", confidence=0.95, model="m"), "low", self.handle)
        self.assertEqual(res["outcome"], "ESCALATE")
        self.assertEqual(res["next_lane"], "muse")
        self.assertEqual(res["reason"], "no_promotion")

    def test_valid_promotion_for_the_same_model_acts(self):
        _seed(self.dp, self.op, "f", "noul", 0.95, 35)
        handle = self._promote(family="f", qtype="noul", risk_class="low", model="m", bound_at_signing=0.85)
        res = cascade.route(_dec(family="f", qtype="noul", confidence=0.95, model="m"), "low", handle)
        self.assertEqual(res["outcome"], "ACT")

    def test_promotion_for_a_different_model_escalates(self):
        _seed(self.dp, self.op, "f", "noul", 0.95, 35)
        handle = self._promote(family="f", qtype="noul", risk_class="low", model="m-old", bound_at_signing=0.0)
        res = cascade.route(_dec(family="f", qtype="noul", confidence=0.95, model="m-new"), "low", handle)
        self.assertEqual(res["outcome"], "ESCALATE")
        self.assertEqual(res["reason"], "model_mismatch")

    def test_revoke_cancels_a_promotion(self):
        _seed(self.dp, self.op, "f", "noul", 0.95, 35)
        self._promote(family="f", qtype="noul", risk_class="low", model="m",
                       bound_at_signing=0.0, signed_at="2026-09-18T00:00:00Z")
        handle = self._promote(family="f", qtype="noul", risk_class="low", model="m",
                                signed_at="2026-09-18T01:00:00Z", revoke=True)
        res = cascade.route(_dec(family="f", qtype="noul", confidence=0.95, model="m"), "low", handle)
        self.assertEqual(res["outcome"], "ESCALATE")
        self.assertEqual(res["reason"], "revoked")

    def test_future_dated_promote_before_a_revoke_does_not_beat_it(self):
        # Clock skew (adversarial review, 2026-09-18): append order is
        # promote FIRST (but signed with a FUTURE date), revoke SECOND
        # (appended later, signed with an earlier date). Ordering by
        # signed_at would let the future-dated promote look "latest" and
        # survive; ordering by append order alone means the revoke, which
        # was actually appended last, wins regardless of what either
        # record claims about when it was signed.
        _seed(self.dp, self.op, "f", "noul", 0.95, 35)
        self._promote(family="f", qtype="noul", risk_class="low", model="m",
                       bound_at_signing=0.0, signed_at="2099-01-01T00:00:00Z")
        handle = self._promote(family="f", qtype="noul", risk_class="low", model="m",
                                signed_at="2000-01-01T00:00:00Z", revoke=True)
        res = cascade.route(_dec(family="f", qtype="noul", confidence=0.95, model="m"), "low", handle)
        self.assertEqual(res["outcome"], "ESCALATE")
        self.assertEqual(res["reason"], "revoked")

    def test_back_dated_revoke_appended_after_a_promote_still_cancels_it(self):
        # The other half of the same clock-skew property: promote FIRST
        # at a normal date, revoke SECOND (appended later) but
        # BACK-DATED to before the promote's own signed_at. Still
        # cancels: append order, never signed_at, decides who is latest.
        _seed(self.dp, self.op, "f", "noul", 0.95, 35)
        self._promote(family="f", qtype="noul", risk_class="low", model="m",
                       bound_at_signing=0.0, signed_at="2026-09-18T00:00:00Z")
        handle = self._promote(family="f", qtype="noul", risk_class="low", model="m",
                                signed_at="2010-01-01T00:00:00Z", revoke=True)
        res = cascade.route(_dec(family="f", qtype="noul", confidence=0.95, model="m"), "low", handle)
        self.assertEqual(res["outcome"], "ESCALATE")
        self.assertEqual(res["reason"], "revoked")

    def test_repromotion_after_a_revoke_acts_again(self):
        # Only the LATEST record per key decides: a promote after a
        # revoke is active once more, proving "latest wins" rather than
        # "a revoke sticks forever regardless of what comes after".
        _seed(self.dp, self.op, "f", "noul", 0.95, 35)
        self._promote(family="f", qtype="noul", risk_class="low", model="m",
                       bound_at_signing=0.0, signed_at="2026-09-18T00:00:00Z")
        self._promote(family="f", qtype="noul", risk_class="low", model="m",
                       signed_at="2026-09-18T01:00:00Z", revoke=True)
        handle = self._promote(family="f", qtype="noul", risk_class="low", model="m",
                                bound_at_signing=0.0, signed_at="2026-09-18T02:00:00Z")
        res = cascade.route(_dec(family="f", qtype="noul", confidence=0.95, model="m"), "low", handle)
        self.assertEqual(res["outcome"], "ACT")

    def test_bound_regressed_below_signed_value_escalates(self):
        # Signed off at a bound (0.99) the CURRENT ledger's Wilson lower
        # bound (~0.9011) no longer supports: the promotion is stale, not
        # a permanent license, so route() escalates rather than trusting
        # the sign-off over today's real measurement.
        _seed(self.dp, self.op, "f", "noul", 0.95, 35)
        handle = self._promote(family="f", qtype="noul", risk_class="low", model="m", bound_at_signing=0.99)
        res = cascade.route(_dec(family="f", qtype="noul", confidence=0.95, model="m"), "low", handle)
        self.assertEqual(res["outcome"], "ESCALATE")
        self.assertEqual(res["reason"], "bound_not_met")

    def test_lying_bound_does_not_survive_a_weak_live_ledger(self):
        # A promotion record can CLAIM any bound_at_signing at all --
        # nothing here verifies a signer's claim against history at
        # signing time. What actually protects a caller is that route()
        # never gets past jev_calibration.threshold() in the first place
        # when the LIVE ledger, today, does not clear the risk class's
        # target: 21 of 22 at confidence 0.95 is real, checkable evidence
        # (n=22 clears MIN_SAMPLES=20) whose Wilson lower bound is well
        # under the low target of 0.90, asserted here via the module's own
        # function rather than a hand-typed number, so this test cannot
        # silently drift from what route() itself will compute.
        _seed(self.dp, self.op, "f", "noul", 0.95, 21, n_wrong=1)
        live_bound = jc._wilson_lower_bound(21, 22)
        self.assertLess(live_bound, cascade.RISK_TARGET_PRECISION["low"])
        handle = self._promote(bound_at_signing=0.98, n_at_signing=22)  # the lying claim
        res = cascade.route(_dec(family="f", qtype="noul", confidence=0.95, model="m"), "low", handle)
        self.assertEqual(res["outcome"], "ESCALATE")
        self.assertTrue(res["reason"].startswith("calibration_no_data:"))

    def test_model_match_is_exact_never_normalized(self):
        # A promotion for model "M" must never match a decision reporting
        # "M " (trailing space), "m" (different case) or "M\n" (trailing
        # newline): all three still pass route()'s own non-blank-string
        # check on decision.model (each strips to a non-empty string), so
        # this exercises the promotion lookup's own exact-match behaviour,
        # not the earlier structural-validation guard.
        _seed(self.dp, self.op, "f", "noul", 0.95, 35)
        handle = self._promote(model="M", bound_at_signing=0.0)
        for bad_model in ("M ", "m", "M\n"):
            res = cascade.route(
                _dec(family="f", qtype="noul", confidence=0.95, model=bad_model), "low", handle)
            self.assertEqual(res["outcome"], "ESCALATE", bad_model)
            self.assertEqual(res["reason"], "model_mismatch", bad_model)

    def test_critical_never_acts_even_with_a_promotion_record(self):
        _seed(self.dp, self.op, "f", "noul", 0.99, 200)
        handle = self._promote(family="f", qtype="noul", risk_class="critical", model="m", bound_at_signing=0.0)
        res = cascade.route(_dec(family="f", qtype="noul", confidence=0.999, model="m"), "critical", handle)
        self.assertEqual(res["outcome"], "ESCALATE")
        self.assertEqual(res["reason"], "critical_never_acts")

    def test_missing_model_on_decision_never_matches_a_promotion(self):
        _seed(self.dp, self.op, "f", "noul", 0.95, 35)
        handle = self._promote(family="f", qtype="noul", risk_class="low", model="m", bound_at_signing=0.0)
        res = cascade.route(_dec(family="f", qtype="noul", confidence=0.95), "low", handle)
        self.assertEqual(res["outcome"], "ESCALATE")
        self.assertEqual(res["reason"], "model_mismatch")

    def test_promotion_record_missing_a_field_escalates_promotions_corrupt(self):
        # C2, 2026-09-18 review fix: a record on disk missing
        # flip_condition is malformed. BEFORE this fix it was silently
        # dropped and route() fell through to "no_promotion" as if the
        # line had never been written -- indistinguishable from an empty
        # store. That is exactly the shape of bug that let a corrupt
        # REVOKE get ignored the same way, leaving an earlier promotion in
        # force (see the sibling tests below). Now ANY corrupt promotion
        # line makes the gate refuse the whole store: "promotions_corrupt",
        # not "no_promotion".
        _seed(self.dp, self.op, "f", "noul", 0.95, 35)
        with open(self.pp, "w", encoding="utf-8") as f:
            f.write(json.dumps({
                "family": "f", "qtype": "noul", "risk_class": "low", "model": "m",
                "signed_by": "founder", "signed_at": "2026-09-18T00:00:00Z",
                "bound_at_signing": 0.0, "n_at_signing": 1,
                # flip_condition deliberately omitted
            }) + "\n")
        handle = cascade.CalibrationHandle(self.dp, self.op, promotions_path=self.pp)
        res = cascade.route(_dec(family="f", qtype="noul", confidence=0.95, model="m"), "low", handle)
        self.assertEqual(res["outcome"], "ESCALATE")
        self.assertEqual(res["reason"], "promotions_corrupt")

    def test_corrupt_revoke_bad_signed_at_does_not_leave_earlier_promotion_in_force(self):
        # C2 probe from the review: a valid promote, then a revoke line
        # whose signed_at is not a parseable timestamp. Before this fix,
        # _promotion_gate ignored the `corrupt` bucket entirely, so the
        # corrupt revoke was discarded and the earlier promotion stayed
        # active -- route() returned ACT. Now any corrupt line anywhere in
        # the store fails the gate closed.
        _seed(self.dp, self.op, "f", "noul", 0.95, 35)
        self._promote(family="f", qtype="noul", risk_class="low", model="m",
                      bound_at_signing=0.0, signed_at="2026-09-18T00:00:00Z")
        with open(self.pp, "a", encoding="utf-8") as f:
            f.write(json.dumps({
                "family": "f", "qtype": "noul", "risk_class": "low", "model": "m",
                "signed_by": "founder", "signed_at": "18 Sep 2026",  # not ISO 8601
                "revoke": True,
            }) + "\n")
        handle = cascade.CalibrationHandle(self.dp, self.op, promotions_path=self.pp)
        res = cascade.route(_dec(family="f", qtype="noul", confidence=0.95, model="m"), "low", handle)
        self.assertEqual(res["outcome"], "ESCALATE")
        self.assertEqual(res["reason"], "promotions_corrupt")

    def test_revoke_written_as_string_true_is_corrupt_not_ignored(self):
        # C2 probe: "revoke": "true" (a string, not the boolean True)
        # fails _validate_promotion_record the same way a missing
        # flip_condition does (the record is read as an ordinary promotion
        # attempt, which then fails validation for lacking the promotion
        # fields) -- and must escalate promotions_corrupt, not silently
        # fall through to no_promotion or, worse, be misread as a
        # promotion.
        _seed(self.dp, self.op, "f", "noul", 0.95, 35)
        self._promote(family="f", qtype="noul", risk_class="low", model="m",
                      bound_at_signing=0.0, signed_at="2026-09-18T00:00:00Z")
        with open(self.pp, "a", encoding="utf-8") as f:
            f.write(json.dumps({
                "family": "f", "qtype": "noul", "risk_class": "low", "model": "m",
                "signed_by": "founder", "signed_at": "2026-09-18T01:00:00Z",
                "revoke": "true",
            }) + "\n")
        handle = cascade.CalibrationHandle(self.dp, self.op, promotions_path=self.pp)
        res = cascade.route(_dec(family="f", qtype="noul", confidence=0.95, model="m"), "low", handle)
        self.assertEqual(res["outcome"], "ESCALATE")
        self.assertEqual(res["reason"], "promotions_corrupt")

    def test_unparseable_promotion_line_escalates_promotions_corrupt(self):
        # Not valid JSON at all: the other _read_promotions corrupt path.
        _seed(self.dp, self.op, "f", "noul", 0.95, 35)
        self._promote(family="f", qtype="noul", risk_class="low", model="m", bound_at_signing=0.0)
        with open(self.pp, "a", encoding="utf-8") as f:
            f.write("{not json\n")
        handle = cascade.CalibrationHandle(self.dp, self.op, promotions_path=self.pp)
        res = cascade.route(_dec(family="f", qtype="noul", confidence=0.95, model="m"), "low", handle)
        self.assertEqual(res["outcome"], "ESCALATE")
        self.assertEqual(res["reason"], "promotions_corrupt")

    def test_promotions_file_unreadable_escalates_promotions_corrupt_never_raises(self):
        # OSError minor (2026-09-18 review): the promotions file exists
        # but cannot be opened (permission denied). Must escalate with a
        # named reason, never raise out of route(). Skipped when running
        # as root, where chmod 000 does not block reads.
        if hasattr(os, "geteuid") and os.geteuid() == 0:
            self.skipTest("permission bits are not enforced for root")
        _seed(self.dp, self.op, "f", "noul", 0.95, 35)
        self._promote(family="f", qtype="noul", risk_class="low", model="m", bound_at_signing=0.0)
        os.chmod(self.pp, 0o000)
        try:
            handle = cascade.CalibrationHandle(self.dp, self.op, promotions_path=self.pp)
            res = cascade.route(_dec(family="f", qtype="noul", confidence=0.95, model="m"), "low", handle)
        finally:
            os.chmod(self.pp, 0o644)
        self.assertEqual(res["outcome"], "ESCALATE")
        self.assertEqual(res["reason"], "promotions_corrupt")

    def test_blank_model_on_decision_raises(self):
        with self.assertRaisesRegex(ValueError, "model"):
            cascade.route(_dec(model="   "), "low", self.handle)


class TestPromotionReviewByExpiry(CascadeTestCase):
    """Item 2 (2026-09-19): a promotion with no expiry and no revoke is a
    standing grant nobody is forced to revisit. Every ordinary promotion
    now carries review_by; a promotion whose review_by has passed is no
    longer active, without needing a revoke record, and route() reports
    the distinct reason "review_expired"."""

    def test_promotion_missing_review_by_is_corrupt(self):
        _seed(self.dp, self.op, "f", "noul", 0.95, 35)
        with open(self.pp, "w", encoding="utf-8") as f:
            f.write(json.dumps({
                "family": "f", "qtype": "noul", "risk_class": "low", "model": "m",
                "signed_by": "founder", "signed_at": "2026-09-18T00:00:00Z",
                "bound_at_signing": 0.0, "n_at_signing": 1,
                "flip_condition": "precision drops below target for two consecutive weeks",
                # review_by deliberately omitted
            }) + "\n")
        handle = cascade.CalibrationHandle(self.dp, self.op, promotions_path=self.pp)
        res = cascade.route(_dec(family="f", qtype="noul", confidence=0.95, model="m"), "low", handle)
        self.assertEqual(res["outcome"], "ESCALATE")
        self.assertEqual(res["reason"], "promotions_corrupt")

    def test_unparseable_review_by_is_corrupt(self):
        _seed(self.dp, self.op, "f", "noul", 0.95, 35)
        with open(self.pp, "w", encoding="utf-8") as f:
            f.write(json.dumps({
                "family": "f", "qtype": "noul", "risk_class": "low", "model": "m",
                "signed_by": "founder", "signed_at": "2026-09-18T00:00:00Z",
                "bound_at_signing": 0.0, "n_at_signing": 1,
                "flip_condition": "precision drops below target for two consecutive weeks",
                "review_by": "not a date",
            }) + "\n")
        handle = cascade.CalibrationHandle(self.dp, self.op, promotions_path=self.pp)
        res = cascade.route(_dec(family="f", qtype="noul", confidence=0.95, model="m"), "low", handle)
        self.assertEqual(res["outcome"], "ESCALATE")
        self.assertEqual(res["reason"], "promotions_corrupt")

    def test_revoke_record_needs_no_review_by(self):
        # A revoke cancels, it does not grant, so it carries no review_by
        # at all -- must not be treated as corrupt for lacking one.
        _seed(self.dp, self.op, "f", "noul", 0.95, 35)
        self._promote(family="f", qtype="noul", risk_class="low", model="m", bound_at_signing=0.0)
        self._promote(family="f", qtype="noul", risk_class="low", model="m",
                       signed_at="2026-09-18T01:00:00Z", revoke=True)
        handle = cascade.CalibrationHandle(self.dp, self.op, promotions_path=self.pp)
        res = cascade.route(_dec(family="f", qtype="noul", confidence=0.95, model="m"), "low", handle)
        self.assertEqual(res["outcome"], "ESCALATE")
        self.assertEqual(res["reason"], "revoked")

    def test_review_by_not_yet_past_still_acts(self):
        import datetime as _dt
        _seed(self.dp, self.op, "f", "noul", 0.95, 35)
        handle = self._promote(review_by="2099-01-01")
        res = cascade.route(_dec(family="f", qtype="noul", confidence=0.95, model="m"), "low", handle,
                             today=_dt.date(2026, 9, 19))
        self.assertEqual(res["outcome"], "ACT")

    def test_review_by_already_past_escalates_review_expired(self):
        import datetime as _dt
        _seed(self.dp, self.op, "f", "noul", 0.95, 35)
        handle = self._promote(review_by="2026-09-01")
        res = cascade.route(_dec(family="f", qtype="noul", confidence=0.95, model="m"), "low", handle,
                             today=_dt.date(2026, 9, 19))
        self.assertEqual(res["outcome"], "ESCALATE")
        self.assertEqual(res["reason"], "review_expired")

    def test_review_by_exactly_today_is_still_active_inclusive_deadline(self):
        import datetime as _dt
        _seed(self.dp, self.op, "f", "noul", 0.95, 35)
        handle = self._promote(review_by="2026-09-19")
        res = cascade.route(_dec(family="f", qtype="noul", confidence=0.95, model="m"), "low", handle,
                             today=_dt.date(2026, 9, 19))
        self.assertEqual(res["outcome"], "ACT")

    def test_default_today_is_the_real_clock_not_fabricated(self):
        # No `today` passed at all: route() must use the real, current
        # date, so a promotion whose review_by is safely in the future
        # relative to the ACTUAL clock still acts.
        _seed(self.dp, self.op, "f", "noul", 0.95, 35)
        handle = self._promote(review_by="2099-01-01")
        res = cascade.route(_dec(family="f", qtype="noul", confidence=0.95, model="m"), "low", handle)
        self.assertEqual(res["outcome"], "ACT")


class TestPromotionsPathDefault(unittest.TestCase):
    """M3, 2026-09-18 review (partial fix): the promotions store must not
    live in a seam's ledger_dir. CalibrationHandle's own default now
    points at a fixed repo-root data/ path instead."""

    def test_default_promotions_path_is_repo_root_data_never_none(self):
        repo_root = os.path.dirname(os.path.dirname(os.path.abspath(cascade.__file__)))
        expected = os.path.join(repo_root, "data", "jev-promotions.jsonl")
        self.assertEqual(cascade.DEFAULT_PROMOTIONS_PATH, expected)
        handle = cascade.CalibrationHandle("dp.jsonl", "op.jsonl")
        self.assertEqual(handle.promotions_path, expected)
        self.assertIsNotNone(handle.promotions_path)

    def test_caller_can_still_opt_out_with_explicit_none(self):
        handle = cascade.CalibrationHandle("dp.jsonl", "op.jsonl", promotions_path=None)
        self.assertIsNone(handle.promotions_path)


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

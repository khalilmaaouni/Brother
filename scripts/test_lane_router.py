"""TOKEN-01 calibration: cheap lanes get public draft work, private content
never leaves, and an undeclared content class is private. JEV-06 (absorbs
TOKEN-03) adds: adversarial review defaults to muse, and the decision lane
routes a typed screen through jev_cascade's ACT/ESCALATE/NO-DATA cascade."""
import json
import os
import sys
import tempfile
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import lane_router as R  # noqa: E402
import orchestrator_invariants  # noqa: E402
import jev_cascade  # noqa: E402
import jev_calibration  # noqa: E402


class LaneRouter(unittest.TestCase):
    def test_public_draft_goes_to_the_cheap_lanes(self):
        lane = R.route_lane("implementation", "medium", "public")
        self.assertEqual((lane.draft, lane.review, lane.requires_scan), ("deepseek", "muse", True))

    def test_private_or_unknown_content_never_leaves(self):
        for content in ("private", None, "", "Public", "secret"):
            for tc in sorted(orchestrator_invariants.TASK_CLASSES):
                lane = R.route_lane(tc, "low", content)
                self.assertNotIn(lane.draft, ("deepseek", "muse"), (tc, content))
                self.assertNotIn(lane.review, ("deepseek", "muse"), (tc, content))
                self.assertFalse(lane.requires_scan, (tc, content))

    def test_undeclared_content_says_so(self):
        self.assertIn("undeclared", R.route_lane("implementation").reason)

    def test_critical_draft_stays_on_claude_even_when_public(self):
        lane = R.route_lane("implementation", "critical", "public", codex="ok")
        self.assertEqual((lane.draft, lane.review), ("claude-sonnet", "codex"))

    def test_codex_takes_a_pass_only_on_observed_headroom(self):
        """TOKEN-06: unknown headroom is not 'ok'. A second opinion is worth
        having and not worth a failed pass, so it falls back unless the
        credit state was actually observed."""
        for credits in (None, "exhausted", "maybe"):
            self.assertEqual(R.route_lane("implementation", "critical", "public",
                                          codex=credits).review, "claude-opus", credits)
            self.assertEqual(R.route_lane("planning", "high", "private",
                                          codex=credits).review, "claude-opus", credits)
        self.assertEqual(R.route_lane("planning", "high", "private", codex="ok").review,
                         "codex")

    def test_documentation_stays_with_codex_whatever_the_credits_say(self):
        """The founder's 2026-09-07 law names Codex for documentation; the
        router says so and tells the caller to check the credits first."""
        for credits in (None, "exhausted", "ok"):
            lane = R.route_lane("documentation", "low", "private", codex=credits)
            self.assertEqual(lane.draft, "codex", credits)
        self.assertIn("unknown", R.route_lane("documentation", "low", "private").reason)
        self.assertIn("exhausted", R.route_lane("documentation", "low", "private",
                                                codex="exhausted").reason)

    def test_headroom_record_states(self):
        import json as _json, os as _os, tempfile as _tf, time as _time
        with _tf.TemporaryDirectory() as d:
            path = _os.path.join(d, "codex-credits.json")
            self.assertIsNone(R.codex_headroom(path), "missing record must be unknown")
            with open(path, "w") as fh:
                fh.write("{not json")
            self.assertIsNone(R.codex_headroom(path), "unreadable record must be unknown")
            def write(state, age_s):
                with open(path, "w") as fh:
                    _json.dump({"state": state, "observed_at": _time.time() - age_s}, fh)
            write("ok", 60)
            self.assertEqual(R.codex_headroom(path), "ok")
            write("exhausted", 60)
            self.assertEqual(R.codex_headroom(path), "exhausted")
            write("ok", 48 * 3600)
            self.assertIsNone(R.codex_headroom(path), "a stale record must be unknown")
            write("plenty", 60)
            self.assertIsNone(R.codex_headroom(path), "an unknown word must be unknown")
            with open(path, "w") as fh:
                _json.dump({"state": "ok", "observed_at": _time.time() + 9999}, fh)
            self.assertIsNone(R.codex_headroom(path), "a future record must be unknown")

    def test_documentation_is_drafted_by_codex(self):
        self.assertEqual(R.route_lane("documentation", "low", "private").draft, "codex")

    def test_verification_never_uses_an_outside_lane(self):
        lane = R.route_lane("verification", "low", "public")
        self.assertEqual((lane.draft, lane.review, lane.verify),
                         (None, None, R.VERIFY))

    def test_every_lane_is_verified_deterministically(self):
        for tc in orchestrator_invariants.TASK_CLASSES:
            for content in ("public", "private"):
                self.assertEqual(R.route_lane(tc, "high", content).verify, R.VERIFY)

    def test_unknown_task_class_is_refused(self):
        with self.assertRaises(ValueError):
            R.route_lane("vibes")

    # --- TOKEN-03 / JEV-06: adversarial review defaults to muse ---

    def test_review_defaults_to_muse_when_outside_and_no_opus_gate(self):
        lane = R.route_lane("review", "high", "public")
        self.assertEqual((lane.draft, lane.review, lane.requires_scan), ("muse", None, True))

    def test_review_stays_on_opus_when_checker_names_an_opus_gate(self):
        lane = R.route_lane("review", "high", "public", checker="opus-high")
        self.assertEqual((lane.draft, lane.requires_scan), ("claude-opus", False))

    def test_review_stays_on_muse_when_checker_names_muse_then_opus(self):
        """JEV-06's OWN checker field in the WBS reads 'muse then opus
        (orchestrator mutation)'. That names muse as the review lane and
        opus as a separate orchestrator-run mutation check, not the
        reviewer itself, so a naive 'opus' in checker would misroute this
        exact unit. Named bad state a green substring check would also
        pass: routing this unit's own review to opus and skipping muse."""
        lane = R.route_lane("review", "high", "public",
                            checker="muse then opus (orchestrator mutation)")
        self.assertEqual(lane.draft, "muse")

    def test_review_stays_on_opus_when_content_cannot_leave_the_machine(self):
        for content in ("private", None, "secret"):
            lane = R.route_lane("review", "high", content)
            self.assertEqual(lane.draft, "claude-opus", content)
            self.assertFalse(lane.requires_scan, content)

    def test_opus_gate_checker_beats_private_content_the_same_way(self):
        # Both reasons land on claude-opus; the reason text still tells
        # them apart for anyone reading the record later. The gate case
        # uses public content so its own reason (not privacy's) is the
        # one that actually fires.
        gate = R.route_lane("review", "high", "public", checker="opus-high")
        blocked = R.route_lane("review", "high", "private")
        self.assertEqual(gate.draft, blocked.draft)
        self.assertIn("opus gate", gate.reason)
        self.assertIn("cannot leave", blocked.reason)

    def test_privacy_is_checked_before_the_opus_gate(self):
        """When a unit is BOTH private and opus-gated, privacy is the
        reason recorded: this router checks outside/private first for
        every other lane, and adversarial review is not an exception
        (Muse's own review of this function, JEV-06 evidence)."""
        lane = R.route_lane("review", "high", "private", checker="opus-high")
        self.assertEqual(lane.draft, "claude-opus")
        self.assertIn("cannot leave", lane.reason)

    def test_opus_gate_detection_uses_word_boundaries_not_substrings(self):
        """A bare 'opus' in checker.lower() misreads 'octopus' or 'corpus'
        as a plan-named gate, and a bare 'muse' misreads 'museum' or
        'amusement' as muse already being named (Muse's own finding on
        this function). Both must not happen."""
        # "corpus" contains "opus" as a substring but names no gate.
        lane = R.route_lane("review", "high", "public", checker="see the corpus of evidence")
        self.assertEqual(lane.draft, "muse", "corpus must not be read as an opus gate")
        # "museum" contains "muse" as a substring but does not name muse
        # as the reviewer, so a real, word-boundary "opus" gate must
        # still be honoured.
        lane = R.route_lane("review", "high", "public", checker="opus (see the museum wing)")
        self.assertEqual(lane.draft, "claude-opus",
                         "a real opus gate must not be suppressed by 'museum'")

    def test_non_review_judge_classes_are_unchanged_by_the_checker_field(self):
        for tc in ("planning", "architecture", "research"):
            with_checker = R.route_lane(tc, "medium", "public", checker="opus-high")
            without_checker = R.route_lane(tc, "medium", "public")
            self.assertEqual(with_checker, without_checker, tc)

    # --- JEV-06: the decision lane ---

    def _calibrated_handle(self, tmpdir, family="f", qtype="noul", risk_class="low",
                            confidence=0.95, n=40, model="m", promoted=True):
        """Seeds n correct (decision, outcome) pairs and returns a handle
        pointing at them. promoted=True (the default) also signs a valid
        promotion record for this exact (family, qtype, risk_class, model)
        -- A0.7 (2026-09-18): calibration evidence alone no longer ACTs,
        so a fixture claiming to exercise the ACT path must carry one, the
        same way it must already carry seeded evidence. promoted=False
        returns a handle with no promotions_path at all, for proving the
        escalate-without-a-promotion-store path (see
        test_decision_without_a_promotion_store_still_escalates)."""
        dp = os.path.join(tmpdir, "decisions.jsonl")
        op = os.path.join(tmpdir, "outcomes.jsonl")
        for i in range(n):
            did = "seed%d" % i
            jev_calibration.append_decision(dp, {
                "id": did, "family": family, "qtype": qtype, "framing": "h",
                "answer": True, "prob": confidence, "confidence": confidence, "model": model,
                "cost": 0.0, "at": "2026-09-18T00:00:00Z",
            })
            jev_calibration.append_outcome(op, {"id": did, "correct": True, "source": "t",
                                                "at": "2026-09-18T00:01:00Z"}, decisions_path=dp)
        if not promoted:
            # Explicit promotions_path=None (coordinator re-review,
            # MINOR, 2026-09-18): CalibrationHandle's own default is now
            # a real repo-root path (M3), so "no promotion store at all"
            # must be said explicitly here rather than accidentally
            # depending on whatever, if anything, is signed there.
            return jev_cascade.CalibrationHandle(dp, op, promotions_path=None)
        pp = os.path.join(tmpdir, "promotions.jsonl")
        with open(pp, "w", encoding="utf-8") as f:
            f.write(json.dumps({
                "family": family, "qtype": qtype, "risk_class": risk_class, "model": model,
                "signed_by": "founder", "signed_at": "2026-09-18T00:00:00Z",
                "bound_at_signing": 0.0, "n_at_signing": n,
                "flip_condition": "precision drops below target for two consecutive weeks",
                "review_by": "2099-01-01",
            }) + "\n")
        return jev_cascade.CalibrationHandle(dp, op, promotions_path=pp)

    def test_decision_act_routes_to_jev(self):
        with tempfile.TemporaryDirectory() as d:
            handle = self._calibrated_handle(d)
            lane = R.route_decision(
                {"id": "new1", "family": "f", "qtype": "noul", "confidence": 0.95, "model": "m"},
                "low", handle,
            )
            self.assertEqual((lane.draft, lane.review, lane.requires_scan),
                             (R.DECISION_LANE, None, True))
            self.assertEqual(lane.draft, "jev")

    def test_decision_without_a_promotion_store_still_escalates(self):
        """A0.7: the SAME calibrated evidence that ACTs once promoted
        (test_decision_act_routes_to_jev above) escalates to muse when the
        handle carries no promotions_path at all -- proving route_decision,
        and lane_router generally, inherits jev_cascade's new promotion
        rule for free rather than needing its own copy of the check."""
        with tempfile.TemporaryDirectory() as d:
            handle = self._calibrated_handle(d, promoted=False)
            lane = R.route_decision(
                {"id": "new2", "family": "f", "qtype": "noul", "confidence": 0.95, "model": "m"},
                "low", handle,
            )
            self.assertEqual((lane.draft, lane.requires_scan), ("muse", True))
            self.assertIn("no_promotion", lane.reason)

    def test_decision_escalates_to_muse_first_then_opus(self):
        with tempfile.TemporaryDirectory() as d:
            # No calibration recorded for this family: threshold() has no
            # data, so the cascade escalates rather than acting.
            # promotions_path=None (coordinator re-review, MINOR,
            # 2026-09-18): explicit, hermetic against CalibrationHandle's
            # real repo-root default (M3) -- these tests never reach the
            # promotion gate anyway, but must not depend on the real
            # store's contents to prove that.
            handle = jev_cascade.CalibrationHandle(
                os.path.join(d, "decisions.jsonl"), os.path.join(d, "outcomes.jsonl"),
                promotions_path=None)
            rung0 = R.route_decision(
                {"id": "e1", "family": "uncalibrated", "qtype": "noul", "confidence": 0.99},
                "low", handle,
            )
            self.assertEqual((rung0.draft, rung0.requires_scan), ("muse", True))
            rung1 = R.route_decision(
                {"id": "e1", "family": "uncalibrated", "qtype": "noul", "confidence": 0.99,
                 "escalation_rung": 1},
                "low", handle,
            )
            self.assertEqual((rung1.draft, rung1.requires_scan), ("opus", False))

    def test_decision_no_data_when_escalation_ladder_is_exhausted(self):
        with tempfile.TemporaryDirectory() as d:
            # promotions_path=None (coordinator re-review, MINOR,
            # 2026-09-18): explicit, hermetic against CalibrationHandle's
            # real repo-root default (M3) -- these tests never reach the
            # promotion gate anyway, but must not depend on the real
            # store's contents to prove that.
            handle = jev_cascade.CalibrationHandle(
                os.path.join(d, "decisions.jsonl"), os.path.join(d, "outcomes.jsonl"),
                promotions_path=None)
            # critical never acts; already on rung 2 (both muse and opus
            # have already reviewed it) means nowhere further to go.
            lane = R.route_decision(
                {"id": "e2", "family": "f", "qtype": "noul", "confidence": 0.99,
                 "escalation_rung": 2},
                "critical", handle,
            )
            self.assertEqual(lane.draft, R.NEEDS_HUMAN)
            self.assertIn(R.NEEDS_HUMAN, orchestrator_invariants.ACTIONS)

    def test_decision_raises_on_structurally_invalid_decision(self):
        with tempfile.TemporaryDirectory() as d:
            # promotions_path=None (coordinator re-review, MINOR,
            # 2026-09-18): explicit, hermetic against CalibrationHandle's
            # real repo-root default (M3) -- these tests never reach the
            # promotion gate anyway, but must not depend on the real
            # store's contents to prove that.
            handle = jev_cascade.CalibrationHandle(
                os.path.join(d, "decisions.jsonl"), os.path.join(d, "outcomes.jsonl"),
                promotions_path=None)
            with self.assertRaises(ValueError):
                R.route_decision({"id": "", "family": "f", "qtype": "noul", "confidence": 0.9},
                                 "low", handle)

    def test_decision_raises_on_an_unrecognised_escalation_lane(self):
        """Defensive, same class as the unrecognised-outcome test: a
        next_lane outside jev_cascade.CASCADE_LANES must never be read as
        'internal, no scan needed' by falling through to == 'muse'."""
        with mock.patch.object(R.jev_cascade, "route",
                               return_value={"outcome": "ESCALATE", "id": "x",
                                            "next_lane": "deepseek", "reason": "r"}):
            with tempfile.TemporaryDirectory() as d:
                # promotions_path=None (coordinator re-review, MINOR,
                # 2026-09-18): explicit, hermetic against
                # CalibrationHandle's real repo-root default (M3).
                handle = jev_cascade.CalibrationHandle(
                    os.path.join(d, "decisions.jsonl"), os.path.join(d, "outcomes.jsonl"),
                    promotions_path=None)
                with self.assertRaises(ValueError):
                    R.route_decision({"id": "x", "family": "f", "qtype": "noul",
                                      "confidence": 0.9}, "low", handle)

    def test_decision_raises_on_an_outcome_jev_cascade_never_returns(self):
        """Defensive: jev_cascade.route() only ever returns ACT, ESCALATE
        or NO-DATA, so this branch cannot be reached through real
        arguments. Proven with a mutation of the dependency itself, the
        same way route_lane's own unknown-enum guards are proven."""
        with mock.patch.object(R.jev_cascade, "route",
                               return_value={"outcome": "MAYBE", "id": "x"}):
            with tempfile.TemporaryDirectory() as d:
                # promotions_path=None (coordinator re-review, MINOR,
                # 2026-09-18): explicit, hermetic against
                # CalibrationHandle's real repo-root default (M3).
                handle = jev_cascade.CalibrationHandle(
                    os.path.join(d, "decisions.jsonl"), os.path.join(d, "outcomes.jsonl"),
                    promotions_path=None)
                with self.assertRaises(ValueError):
                    R.route_decision({"id": "x", "family": "f", "qtype": "noul",
                                      "confidence": 0.9}, "low", handle)


class J033NeverChangesTheRoutedLane(unittest.TestCase):
    """J033, wave-2: second-opinions the task_class/risk_class table for
    the calibration ledger only. C1: whatever Jev says, route_lane()'s
    own real Lane is unchanged."""

    def test_a_normal_route_consults_the_seam_and_keeps_the_lane(self):
        real_check = R.jev_checks.check_lane_routing
        seen = {}
        def fake(spec_text, current_answer, **k):
            seen["spec_text"] = spec_text
            seen["current_answer"] = current_answer
            return None
        R.jev_checks.check_lane_routing = fake
        try:
            lane = R.route_lane("implementation", "medium", "public")
        finally:
            R.jev_checks.check_lane_routing = real_check
        self.assertEqual(lane.draft, "deepseek")
        self.assertEqual(seen["current_answer"], "deepseek")
        self.assertIn("implementation", seen["spec_text"])

    def test_an_invalid_task_class_still_raises_before_any_consult(self):
        real_check = R.jev_checks.check_lane_routing
        called = []
        R.jev_checks.check_lane_routing = lambda *a, **k: called.append(1)
        try:
            with self.assertRaises(ValueError):
                R.route_lane("not-a-real-task-class")
        finally:
            R.jev_checks.check_lane_routing = real_check
        self.assertEqual(called, [])

    def test_a_raising_seam_never_loses_the_real_lane(self):
        real_check = R.jev_checks.check_lane_routing
        R.jev_checks.check_lane_routing = \
            lambda *a, **k: (_ for _ in ()).throw(RuntimeError("boom"))
        try:
            lane = R.route_lane("implementation", "medium", "public")
        finally:
            R.jev_checks.check_lane_routing = real_check
        self.assertEqual(lane.draft, "deepseek")

    def test_an_adversarial_seam_answer_never_reroutes_the_unit(self):
        """Rigged the opposite of the real routed lane: Jev answers
        'claude-opus' for a unit the table routed to deepseek. The
        returned lane must still be deepseek."""
        real_check = R.jev_checks.check_lane_routing
        R.jev_checks.check_lane_routing = lambda *a, **k: "claude-opus"
        try:
            lane = R.route_lane("implementation", "medium", "public")
        finally:
            R.jev_checks.check_lane_routing = real_check
        self.assertEqual(lane.draft, "deepseek")

    def test_jev_checks_unavailable_still_routes_correctly(self):
        real_jev_checks = R.jev_checks
        R.jev_checks = None
        try:
            lane = R.route_lane("implementation", "medium", "public")
        finally:
            R.jev_checks = real_jev_checks
        self.assertEqual(lane.draft, "deepseek")


class J051OnlyConsultsOnAnAmbiguousCheckerAndNeverReroutes(unittest.TestCase):
    """J051, wave-2: fires only when the checker field names neither opus
    nor muse, or both -- the two cases _names_opus_gate cannot resolve on
    its own. C1: whatever Jev says, the real routed lane is unchanged."""

    def test_opus_only_is_unambiguous_and_never_consults(self):
        real_check = R.jev_checks.check_adversarial_review_tier
        called = []
        R.jev_checks.check_adversarial_review_tier = lambda *a, **k: called.append(1)
        try:
            lane = R.route_lane("review", "high", "public", checker="opus-high")
        finally:
            R.jev_checks.check_adversarial_review_tier = real_check
        self.assertEqual(lane.draft, "claude-opus")
        self.assertEqual(called, [])

    def test_muse_only_is_unambiguous_and_never_consults(self):
        real_check = R.jev_checks.check_adversarial_review_tier
        called = []
        R.jev_checks.check_adversarial_review_tier = lambda *a, **k: called.append(1)
        try:
            lane = R.route_lane("review", "high", "public", checker="muse-default")
        finally:
            R.jev_checks.check_adversarial_review_tier = real_check
        self.assertEqual(lane.draft, "muse")
        self.assertEqual(called, [])

    def test_neither_named_is_ambiguous_and_consults(self):
        real_check = R.jev_checks.check_adversarial_review_tier
        seen = {}
        def fake(checker_text, current_answer, **k):
            seen["checker_text"] = checker_text
            seen["current_answer"] = current_answer
            return None
        R.jev_checks.check_adversarial_review_tier = fake
        try:
            lane = R.route_lane("review", "high", "public", checker=None)
        finally:
            R.jev_checks.check_adversarial_review_tier = real_check
        self.assertEqual(lane.draft, "muse")
        self.assertEqual(seen["current_answer"], "muse")

    def test_both_named_is_ambiguous_and_consults(self):
        """JEV-06's own real checker string: 'muse then opus (orchestrator
        mutation)' names both words, is routed to muse today, and is
        exactly the case J051 exists to flag for calibration."""
        real_check = R.jev_checks.check_adversarial_review_tier
        called = []
        R.jev_checks.check_adversarial_review_tier = lambda *a, **k: called.append(1)
        try:
            lane = R.route_lane("review", "high", "public",
                                checker="muse then opus (orchestrator mutation)")
        finally:
            R.jev_checks.check_adversarial_review_tier = real_check
        self.assertEqual(lane.draft, "muse")
        self.assertEqual(len(called), 1)

    def test_privacy_gated_case_can_still_be_ambiguous_and_consults(self):
        real_check = R.jev_checks.check_adversarial_review_tier
        called = []
        R.jev_checks.check_adversarial_review_tier = lambda *a, **k: called.append(1)
        try:
            lane = R.route_lane("review", "high", "private", checker=None)
        finally:
            R.jev_checks.check_adversarial_review_tier = real_check
        self.assertEqual(lane.draft, "claude-opus")
        self.assertEqual(len(called), 1)

    def test_an_adversarial_seam_answer_never_reroutes_the_review(self):
        real_check = R.jev_checks.check_adversarial_review_tier
        R.jev_checks.check_adversarial_review_tier = lambda *a, **k: "claude-opus"
        try:
            lane = R.route_lane("review", "high", "public", checker=None)
        finally:
            R.jev_checks.check_adversarial_review_tier = real_check
        self.assertEqual(lane.draft, "muse")

    def test_a_raising_seam_never_loses_the_real_lane(self):
        real_check = R.jev_checks.check_adversarial_review_tier
        R.jev_checks.check_adversarial_review_tier = \
            lambda *a, **k: (_ for _ in ()).throw(RuntimeError("boom"))
        try:
            lane = R.route_lane("review", "high", "public", checker=None)
        finally:
            R.jev_checks.check_adversarial_review_tier = real_check
        self.assertEqual(lane.draft, "muse")


if __name__ == "__main__":
    unittest.main()

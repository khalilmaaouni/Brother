#!/usr/bin/env python3
"""WBS-40.05 Risk-Directed Review Orchestrator.

Routes match candidates from the golden-master matcher (WBS-40.04) into one
of three buckets using the cost-class and threshold fields the parent
contract already carries (scripts/golden_master_contract.py,
docs/schema/golden-master-contract-v1.json): false_merge_cost_class,
missed_match_cost_class, review_policy, merge_threshold, review_threshold.

Routing (roadmap WBS-40.05, "Tomorrow: minimal scoring/routing policy"):
  AUTO_ACCEPT      score >= merge_threshold AND false_merge_cost_class is
                   not high risk.
  CLERICAL_REVIEW  the ambiguous middle (review_threshold <= score <
                   merge_threshold), OR false_merge_cost_class is high
                   risk regardless of score -- a catastrophic-cost-if-wrong
                   candidate never silently auto-accepts on score alone.
  AUTO_REJECT      score < review_threshold.

Muse's hostile review of this design (docs/plan/1.0.17/
WAVE-2-DESIGN-CRITIQUES-2026-09-13.md, "Risk-directed review orchestrator")
names Cost-Class Inversion/Poisoning as the strongest failure mode: the
label that drives routing is wrong, whether by an upstream mistake or by
deliberate gaming of whatever feeds the classification. This module does
not detect poisoning (that needs the audit/monitoring loop the same
critique describes, out of scope for this seam); it defends the narrower,
checkable half: an unrecognized cost-class value is refused rather than
silently folded into the low-risk path, which is exactly the shape a
poisoning attempt, or a plain upstream bug, would take advantage of.

The set of recognized cost-class values is closed and explicit in
COST_CLASSES rather than read off the schema, because
docs/schema/golden-master-contract-v1.json leaves both *_cost_class fields
as open strings on purpose ("this schema does not fix the taxonomy's
values, so a project's own review policy can name its own tiers"). This
orchestrator is that review policy, so it is where the taxonomy is
actually pinned down.
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import evidence_obligation as EO  # noqa: E402 -- EV-3 verdict vocabulary, not a parallel one

# Ordered low to high. HIGH_RISK_COST_CLASSES names the tiers that force
# clerical review regardless of score (the roadmap's own merge-passport
# example uses "catastrophic" as its worked value for false_merge_cost).
COST_CLASSES = ("LOW", "MEDIUM", "HIGH", "CATASTROPHIC")
HIGH_RISK_COST_CLASSES = ("HIGH", "CATASTROPHIC")

ROUTES = ("AUTO_ACCEPT", "AUTO_REJECT", "CLERICAL_REVIEW")

# Reuse the EV-3 three-value verdict vocabulary for "this candidate's
# inputs could not be trusted", instead of inventing a fourth routing
# bucket. The assert catches the day evidence_obligation's tuple drifts.
REFUSED = "NO-DATA"
if REFUSED not in EO.VERDICTS:
    raise AssertionError("evidence_obligation.VERDICTS no longer carries NO-DATA")


class RiskReviewError(ValueError):
    """Raised when a candidate cannot be routed as given: an unrecognized
    cost class, a non-numeric score, or thresholds that do not make sense
    together. route_batch turns this refusal into a bucket instead of a
    crash; route_candidate itself always raises rather than guess."""


def route_candidate(candidate_id, score, false_merge_cost_class,
                     missed_match_cost_class, merge_threshold,
                     review_threshold):
    """Route one match candidate. Returns a dict with candidate_id, routing
    (one of ROUTES), and reason."""
    if false_merge_cost_class not in COST_CLASSES:
        raise RiskReviewError(
            "candidate %r: false_merge_cost_class %r is not one of %s" %
            (candidate_id, false_merge_cost_class, COST_CLASSES))
    if missed_match_cost_class not in COST_CLASSES:
        raise RiskReviewError(
            "candidate %r: missed_match_cost_class %r is not one of %s" %
            (candidate_id, missed_match_cost_class, COST_CLASSES))
    if isinstance(score, bool) or not isinstance(score, (int, float)):
        raise RiskReviewError(
            "candidate %r: score %r is not numeric" % (candidate_id, score))
    if isinstance(merge_threshold, bool) or not isinstance(merge_threshold, (int, float)):
        raise RiskReviewError(
            "candidate %r: merge_threshold %r is not numeric" %
            (candidate_id, merge_threshold))
    if isinstance(review_threshold, bool) or not isinstance(review_threshold, (int, float)):
        raise RiskReviewError(
            "candidate %r: review_threshold %r is not numeric" %
            (candidate_id, review_threshold))
    if review_threshold > merge_threshold:
        raise RiskReviewError(
            "candidate %r: review_threshold %r is above merge_threshold %r" %
            (candidate_id, review_threshold, merge_threshold))

    if false_merge_cost_class in HIGH_RISK_COST_CLASSES:
        # The core anti-silent-auto-accept property: a high or catastrophic
        # false-merge cost never clears on score alone, however high the
        # score is.
        return {
            "candidate_id": candidate_id,
            "routing": "CLERICAL_REVIEW",
            "reason": "false_merge_cost_class=%s forces review regardless of score=%r" %
                      (false_merge_cost_class, score),
        }
    if score >= merge_threshold:
        return {
            "candidate_id": candidate_id,
            "routing": "AUTO_ACCEPT",
            "reason": "score=%r >= merge_threshold=%r, false_merge_cost_class=%s" %
                      (score, merge_threshold, false_merge_cost_class),
        }
    if score < review_threshold:
        return {
            "candidate_id": candidate_id,
            "routing": "AUTO_REJECT",
            "reason": "score=%r < review_threshold=%r" % (score, review_threshold),
        }
    return {
        "candidate_id": candidate_id,
        "routing": "CLERICAL_REVIEW",
        "reason": "score=%r is the ambiguous middle (review_threshold=%r, merge_threshold=%r)" %
                  (score, review_threshold, merge_threshold),
    }


def route_batch(candidates):
    """Route a list of candidate dicts, each carrying candidate_id, score,
    false_merge_cost_class, missed_match_cost_class, merge_threshold and
    review_threshold. Returns (decisions, summary): decisions is a list in
    input order (one dict per candidate, from route_candidate, or a REFUSED
    entry), summary is a dict of routing name -> count covering every
    bucket including REFUSED.

    A candidate route_candidate refuses is never dropped and never folded
    into a routing bucket by accident: it lands in decisions with
    routing=REFUSED and the refusal reason, and summary counts it under
    REFUSED. This is the seam WBS-40.06's clerical review plan is meant to
    consume: it can sample AUTO_ACCEPT/CLERICAL_REVIEW/AUTO_REJECT by
    pathway and score band, and audit REFUSED separately as a data-quality
    signal in its own right (an unexpected cost-class value reaching this
    seam is itself worth investigating, per the Muse poisoning critique)."""
    decisions = []
    summary = {route: 0 for route in ROUTES}
    summary[REFUSED] = 0

    for candidate in candidates:
        try:
            decision = route_candidate(
                candidate.get("candidate_id"),
                candidate.get("score"),
                candidate.get("false_merge_cost_class"),
                candidate.get("missed_match_cost_class"),
                candidate.get("merge_threshold"),
                candidate.get("review_threshold"),
            )
        except RiskReviewError as exc:
            decision = {
                "candidate_id": candidate.get("candidate_id"),
                "routing": REFUSED,
                "reason": str(exc),
            }
        decisions.append(decision)
        summary[decision["routing"]] += 1

    return decisions, summary

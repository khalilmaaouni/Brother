#!/usr/bin/env python3
"""WBS-40.06 Clerical Review Sampling Plan.

Consumes ONLY the CLERICAL_REVIEW bucket scripts/risk_review_orchestrator.py
(WBS-40.05) already produces via route_batch(). This module makes no new
routing decision and reuses that module's own MDM audit/evaluation rather
than building a second one: it decides which of an already-routed bucket of
candidates actually gets reviewed this round (a sampling question), never
whether a candidate was routed correctly (a routing question, out of scope
here).

A "routed candidate record", as build_sampling_plan expects it, is a plain
dict a caller builds by merging one risk_review_orchestrator.route_batch()
decision (routing, reason) into its source candidate record. The fields
this module actually reads are, in order of how they are confirmed:
  candidate_id, score, false_merge_cost_class, missed_match_cost_class
                    confirmed from risk_review_orchestrator.route_candidate,
                    which reads exactly these off every candidate dict it
                    is given.
  routing           confirmed from risk_review_orchestrator.route_batch's
                    own decision dicts (candidate_id, routing, reason).
  pathway           confirmed real from scripts/matcher_boundary.py's
                    MatcherOutput contract (WBS-40.04), the "matching
                    strategy/algorithm name" -- risk_review_orchestrator.py
                    does not read this field itself, but does not drop it
                    either, so a candidate record threaded through from a
                    real MatcherOutput still carries it here.
  unmatched_reason, segment
                    NOT produced by risk_review_orchestrator.py or
                    matcher_boundary.py today (checked: neither name
                    appears in either module). Read only if present on the
                    candidate dict handed in, never defaulted to a made-up
                    value that would misrepresent an absent field as real
                    data -- the roadmap text says "where required", not
                    "always present".

Reproducibility: build_sampling_plan(population, seed) draws with
random.Random(seed) (stdlib, no dependency) after sorting strata and each
stratum's members into a canonical order, so the same (population, seed)
pair always yields byte-identical output regardless of input list order.
plan_identity hashes that same canonical population content together with
the seed and a fixed stratification-rule tag, so two runs are provably the
same plan only when population, seed, and rule all agree.

Never estimate precision from score distribution alone (the roadmap's own
explicit warning): this module has no clerical review OUTCOMES at
plan-creation time, only a not-yet-reviewed population, so any precision
number it produced here would be exactly that forbidden guess. Enforced
structurally, not just documented: the only precision-shaped surface this
module exposes, precision_estimate() and the plan's own "precision" field,
always return the shared NO-DATA verdict (scripts/evidence_obligation.py)
until a future unit that actually has review outcomes replaces it.
"""
import hashlib
import json
import math
import os
import random
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import evidence_obligation as EO  # noqa: E402 -- EV-3 verdict vocabulary, not a parallel one
import risk_review_orchestrator as RRO  # noqa: E402 -- reuse the real routing vocabulary, not a parallel one

NO_DATA = "NO-DATA"
if NO_DATA not in EO.VERDICTS:
    raise AssertionError("evidence_obligation.VERDICTS no longer carries NO-DATA")

CLERICAL_REVIEW = "CLERICAL_REVIEW"
if CLERICAL_REVIEW not in RRO.ROUTES:
    raise AssertionError("risk_review_orchestrator.ROUTES no longer carries CLERICAL_REVIEW")

# ponytail: flat fraction, no per-stratum precision/SLA target has been
# named anywhere in the roadmap text this unit was given. Upgrade to a
# named target sample size (e.g. a stated review-capacity SLA per band)
# once one exists; until then a uniform 20% is the honest default, not a
# statistically derived one.
DEFAULT_SAMPLE_FRACTION = 0.2

SCORE_BAND_WIDTH = 0.1

# Bumped only if the stratification rule itself changes shape, so an old
# plan_identity is never mistaken for a plan built under a new rule.
PLAN_IDENTITY_RULE = "clerical_review_plan.v1"


def _score_band(score):
    if isinstance(score, bool) or not isinstance(score, (int, float)):
        return "UNSCORED"
    lo = math.floor(score / SCORE_BAND_WIDTH) * SCORE_BAND_WIDTH
    hi = lo + SCORE_BAND_WIDTH
    return "%.1f-%.1f" % (lo, hi)


def _candidate_id(candidate):
    cid = candidate.get("candidate_id")
    if cid in (None, ""):
        raise ValueError("routed candidate record missing candidate_id: %r" % (candidate,))
    return cid


def _stratum_key(candidate):
    pathway = candidate.get("pathway") or "UNKNOWN_PATHWAY"
    band = _score_band(candidate.get("score"))
    # Read only if present -- an absent field stays None, it is never
    # given a made-up value standing in for real data.
    unmatched_reason = candidate.get("unmatched_reason") or None
    segment = candidate.get("segment") or None
    return (pathway, band, unmatched_reason, segment)


def _stratum_label(key):
    pathway, band, unmatched_reason, segment = key
    parts = ["pathway=%s" % pathway, "score_band=%s" % band]
    if unmatched_reason is not None:
        parts.append("unmatched_reason=%s" % unmatched_reason)
    if segment is not None:
        parts.append("segment=%s" % segment)
    return "|".join(parts)


def _population_fingerprint(routed_candidates):
    """Canonical, order-independent content for plan_identity: every field
    this module actually reads off each candidate, sorted by candidate_id
    so input list order never changes the hash, only the population's own
    content does."""
    rows = []
    for candidate in routed_candidates:
        rows.append({
            "candidate_id": _candidate_id(candidate),
            "routing": candidate.get("routing"),
            "score": candidate.get("score"),
            "pathway": candidate.get("pathway"),
            "unmatched_reason": candidate.get("unmatched_reason"),
            "segment": candidate.get("segment"),
            "false_merge_cost_class": candidate.get("false_merge_cost_class"),
            "missed_match_cost_class": candidate.get("missed_match_cost_class"),
        })
    rows.sort(key=lambda r: r["candidate_id"])
    return rows


def plan_identity(routed_candidates, seed):
    """Stable hash of population + seed + stratification rule. Two calls
    against the same population with the same seed hash identically; a
    call against a different population (a different candidate set, or the
    same candidate_ids with a changed stratification-relevant field) hashes
    differently even with the same seed."""
    payload = {
        "rule": PLAN_IDENTITY_RULE,
        "seed": seed,
        "population": _population_fingerprint(routed_candidates),
    }
    canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def precision_estimate():
    """Structural refusal, not a comment. This module only ever sees the
    not-yet-reviewed population at plan-creation time, so it has nothing
    to compute a real precision figure from -- the roadmap text forbids
    estimating one from score distribution alone, and score distribution
    is all this module has. Always NO-DATA. A future unit that actually
    holds recorded clerical review outcomes is where a real precision
    figure belongs, not here."""
    return NO_DATA


def build_sampling_plan(routed_candidates, seed, sample_fraction_per_stratum=None):
    """Build a reproducible sampling plan from risk_review_orchestrator's
    CLERICAL_REVIEW bucket.

    routed_candidates: candidate records already routed CLERICAL_REVIEW.
      Each record's own "routing" field, when present, is checked and must
      equal CLERICAL_REVIEW -- this function samples that bucket, it never
      re-routes or re-scores.
    seed: int, recorded verbatim on the plan. The same seed against the
      same population always yields the same sample (reproducibility
      contract this function exists to provide).
    sample_fraction_per_stratum: None (DEFAULT_SAMPLE_FRACTION for every
      stratum), a float (that fraction for every stratum), or a dict of
      stratum label (see the "label" field on each returned stratum) to
      fraction, falling back to DEFAULT_SAMPLE_FRACTION for any stratum not
      named.

    Returns a plan dict: plan_identity, seed, population (total candidate
    count), precision (structural NO-DATA, see precision_estimate), and
    strata (a list of per-stratum dicts: label, pathway, score_band,
    unmatched_reason, segment, population, sampled_count,
    sampled_candidate_ids).
    """
    if not isinstance(seed, int) or isinstance(seed, bool):
        raise ValueError("seed must be an int, got %r" % (seed,))

    for candidate in routed_candidates:
        routing = candidate.get("routing")
        if routing is not None and routing != CLERICAL_REVIEW:
            raise ValueError(
                "build_sampling_plan only samples the CLERICAL_REVIEW bucket, "
                "got candidate_id=%r routing=%r" %
                (candidate.get("candidate_id"), routing))

    strata = {}
    for candidate in routed_candidates:
        key = _stratum_key(candidate)
        strata.setdefault(key, []).append(candidate)

    identity = plan_identity(routed_candidates, seed)
    rng = random.Random(seed)

    stratum_plans = []
    for key in sorted(strata.keys(), key=_stratum_label):
        members = sorted(strata[key], key=_candidate_id)
        population = len(members)
        label = _stratum_label(key)

        fraction = DEFAULT_SAMPLE_FRACTION
        if isinstance(sample_fraction_per_stratum, dict):
            fraction = sample_fraction_per_stratum.get(label, DEFAULT_SAMPLE_FRACTION)
        elif isinstance(sample_fraction_per_stratum, (int, float)) and \
                not isinstance(sample_fraction_per_stratum, bool):
            fraction = sample_fraction_per_stratum
        if fraction < 0 or fraction > 1:
            raise ValueError(
                "sample fraction %r for stratum %s is not in [0, 1]" % (fraction, label))

        # ponytail: round-to-nearest with a floor of 1 for any nonempty
        # stratum at a nonzero fraction, so a small stratum is never
        # silently sampled at zero. Upgrade to a named minimum-sample-size
        # rule if a real review-capacity SLA ever states one.
        sample_count = 0 if fraction <= 0 else min(population, max(1, round(population * fraction)))
        sampled = rng.sample(members, sample_count) if sample_count else []

        pathway, band, unmatched_reason, segment = key
        stratum_plans.append({
            "label": label,
            "pathway": pathway,
            "score_band": band,
            "unmatched_reason": unmatched_reason,
            "segment": segment,
            "population": population,
            "sampled_count": len(sampled),
            "sampled_candidate_ids": [_candidate_id(c) for c in sampled],
        })

    return {
        "plan_identity": identity,
        "seed": seed,
        "population": len(routed_candidates),
        "precision": precision_estimate(),
        "strata": stratum_plans,
    }

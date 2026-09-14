#!/usr/bin/env python3
"""Tests for claim_score.py (WBS-50.03 Verified Claim Rate 2D)."""
import json
import os
import sys
import tempfile
import unittest
from datetime import datetime, timedelta, timezone

sys.path.insert(0, __file__.rsplit("/", 1)[0])
import claim_score as CS
import contract_check as CC

NOW = datetime(2026, 9, 14, 0, 0, 0, tzinfo=timezone.utc)

BASE_INTERVAL = {
    "schema_version": "claim-score-v1", "claim_id": "c1", "revision_id": "r1",
    "claim_type": "interval", "created_at": "2026-09-01T00:00:00Z",
    "resolved_at": None, "stated_interval": {"lower": 10.0, "upper": 20.0},
    "stated_probability": None, "baseline_interval": None,
    "baseline_probability": None, "outcome_value": None,
}
BASE_PROBABILITY = {
    "schema_version": "claim-score-v1", "claim_id": "c2", "revision_id": "r1",
    "claim_type": "probability", "created_at": "2026-09-01T00:00:00Z",
    "resolved_at": None, "stated_interval": None, "stated_probability": 0.7,
    "baseline_interval": None, "baseline_probability": None, "outcome_value": None,
}


def interval_rec(**overrides):
    r = dict(BASE_INTERVAL)
    r.update(overrides)
    return r


def prob_rec(**overrides):
    r = dict(BASE_PROBABILITY)
    r.update(overrides)
    return r


class SchemaShapeTests(unittest.TestCase):
    def setUp(self):
        self.schema = CC.load_json(CS.DEFAULT_SCHEMA, "schema")

    def test_an_unresolved_interval_claim_passes(self):
        self.assertEqual(CS.check(interval_rec(), self.schema), [])

    def test_an_unresolved_probability_claim_passes(self):
        self.assertEqual(CS.check(prob_rec(), self.schema), [])

    def test_a_resolved_interval_claim_passes(self):
        rec = interval_rec(resolved_at="2026-09-10T00:00:00Z", outcome_value=15.0)
        self.assertEqual(CS.check(rec, self.schema), [])

    def test_a_resolved_probability_claim_passes(self):
        rec = prob_rec(resolved_at="2026-09-10T00:00:00Z", outcome_value=True)
        self.assertEqual(CS.check(rec, self.schema), [])

    def test_missing_required_top_level_field_is_refused(self):
        r = interval_rec()
        del r["claim_id"]
        problems = CS.check(r, self.schema)
        self.assertTrue(any("claim_id" in p for p in problems), problems)

    def test_wrong_schema_version_is_refused(self):
        problems = CS.check(interval_rec(schema_version="wrong"), self.schema)
        self.assertTrue(problems)

    def test_unknown_claim_type_is_refused(self):
        problems = CS.check(interval_rec(claim_type="point"), self.schema)
        self.assertTrue(problems)

    def test_additional_top_level_property_is_refused(self):
        r = interval_rec()
        r["not_a_real_field"] = "x"
        problems = CS.check(r, self.schema)
        self.assertTrue(any("unexpected field" in p for p in problems), problems)


class ClaimTypeHandRuleTests(unittest.TestCase):
    def setUp(self):
        self.schema = CC.load_json(CS.DEFAULT_SCHEMA, "schema")

    def test_interval_claim_missing_stated_interval_is_refused(self):
        problems = CS.check(interval_rec(stated_interval=None), self.schema)
        self.assertTrue(any("stated_interval" in p and "required" in p for p in problems), problems)

    def test_interval_claim_carrying_stated_probability_is_refused(self):
        problems = CS.check(interval_rec(stated_probability=0.5), self.schema)
        self.assertTrue(any("stated_probability" in p and "must be null" in p for p in problems), problems)

    def test_interval_with_lower_above_upper_is_refused(self):
        problems = CS.check(interval_rec(stated_interval={"lower": 20.0, "upper": 10.0}), self.schema)
        self.assertTrue(any("lower" in p and "must not exceed" in p for p in problems), problems)

    def test_interval_with_equal_bounds_is_accepted(self):
        problems = CS.check(interval_rec(stated_interval={"lower": 5.0, "upper": 5.0}), self.schema)
        self.assertEqual(problems, [])

    def test_probability_claim_missing_stated_probability_is_refused(self):
        problems = CS.check(prob_rec(stated_probability=None), self.schema)
        self.assertTrue(any("stated_probability" in p and "required" in p for p in problems), problems)

    def test_probability_claim_carrying_stated_interval_is_refused(self):
        problems = CS.check(prob_rec(stated_interval={"lower": 0, "upper": 1}), self.schema)
        self.assertTrue(any("stated_interval" in p and "must be null" in p for p in problems), problems)

    def test_probability_out_of_range_high_is_refused(self):
        problems = CS.check(prob_rec(stated_probability=1.5), self.schema)
        self.assertTrue(any("stated_probability" in p and "[0, 1]" in p for p in problems), problems)

    def test_probability_out_of_range_low_is_refused(self):
        problems = CS.check(prob_rec(stated_probability=-0.1), self.schema)
        self.assertTrue(any("stated_probability" in p and "[0, 1]" in p for p in problems), problems)

    def test_probability_boundary_values_zero_and_one_are_accepted(self):
        self.assertEqual(CS.check(prob_rec(stated_probability=0.0), self.schema), [])
        self.assertEqual(CS.check(prob_rec(stated_probability=1.0), self.schema), [])


class BaselineHandRuleTests(unittest.TestCase):
    def setUp(self):
        self.schema = CC.load_json(CS.DEFAULT_SCHEMA, "schema")

    def test_baseline_interval_on_a_probability_claim_is_refused(self):
        problems = CS.check(prob_rec(baseline_interval={"lower": 0, "upper": 1}), self.schema)
        self.assertTrue(any("baseline_interval" in p and "must be null" in p for p in problems), problems)

    def test_baseline_probability_on_an_interval_claim_is_refused(self):
        problems = CS.check(interval_rec(baseline_probability=0.5), self.schema)
        self.assertTrue(any("baseline_probability" in p and "must be null" in p for p in problems), problems)

    def test_baseline_interval_on_an_interval_claim_is_accepted(self):
        problems = CS.check(interval_rec(baseline_interval={"lower": 0.0, "upper": 30.0}), self.schema)
        self.assertEqual(problems, [])

    def test_baseline_probability_on_a_probability_claim_is_accepted(self):
        problems = CS.check(prob_rec(baseline_probability=0.5), self.schema)
        self.assertEqual(problems, [])

    def test_baseline_probability_out_of_range_is_refused(self):
        problems = CS.check(prob_rec(baseline_probability=2.0), self.schema)
        self.assertTrue(any("baseline_probability" in p for p in problems), problems)


class ResolutionConsistencyTests(unittest.TestCase):
    def setUp(self):
        self.schema = CC.load_json(CS.DEFAULT_SCHEMA, "schema")

    def test_resolved_at_without_outcome_value_is_refused(self):
        problems = CS.check(interval_rec(resolved_at="2026-09-10T00:00:00Z"), self.schema)
        self.assertTrue(any("resolved_at/outcome_value" in p for p in problems), problems)

    def test_outcome_value_without_resolved_at_is_refused(self):
        problems = CS.check(interval_rec(outcome_value=15.0), self.schema)
        self.assertTrue(any("resolved_at/outcome_value" in p for p in problems), problems)

    def test_neither_set_is_accepted(self):
        self.assertEqual(CS.check(interval_rec(), self.schema), [])

    def test_both_set_is_accepted(self):
        rec = interval_rec(resolved_at="2026-09-10T00:00:00Z", outcome_value=15.0)
        self.assertEqual(CS.check(rec, self.schema), [])

    def test_unparseable_created_at_is_refused(self):
        problems = CS.check(interval_rec(created_at="not-a-date"), self.schema)
        self.assertTrue(any("created_at" in p for p in problems), problems)

    def test_unparseable_resolved_at_is_refused(self):
        rec = interval_rec(resolved_at="not-a-date", outcome_value=15.0)
        problems = CS.check(rec, self.schema)
        self.assertTrue(any("resolved_at" in p and "ISO-8601" in p for p in problems), problems)

    def test_interval_outcome_value_must_be_a_number(self):
        rec = interval_rec(resolved_at="2026-09-10T00:00:00Z", outcome_value=True)
        problems = CS.check(rec, self.schema)
        self.assertTrue(any("outcome_value" in p and "number" in p for p in problems), problems)

    def test_probability_outcome_value_must_be_a_boolean(self):
        rec = prob_rec(resolved_at="2026-09-10T00:00:00Z", outcome_value=1.0)
        problems = CS.check(rec, self.schema)
        self.assertTrue(any("outcome_value" in p and "boolean" in p for p in problems), problems)


class CheckMatchesLifecycleTests(unittest.TestCase):
    def test_matching_ids_pass(self):
        score = interval_rec(claim_id="c1", revision_id="r1")
        lifecycle = {"claim_id": "c1", "revision_id": "r1", "state": "SCORED"}
        self.assertEqual(CS.check_matches_lifecycle(score, lifecycle), [])

    def test_mismatched_claim_id_is_refused(self):
        score = interval_rec(claim_id="c1", revision_id="r1")
        lifecycle = {"claim_id": "different", "revision_id": "r1"}
        problems = CS.check_matches_lifecycle(score, lifecycle)
        self.assertTrue(any("claim_id" in p for p in problems), problems)

    def test_mismatched_revision_id_is_refused(self):
        score = interval_rec(claim_id="c1", revision_id="r1")
        lifecycle = {"claim_id": "c1", "revision_id": "r2"}
        problems = CS.check_matches_lifecycle(score, lifecycle)
        self.assertTrue(any("revision_id" in p and "rewritten successor" in p for p in problems), problems)

    def test_both_mismatched_reports_both(self):
        score = interval_rec(claim_id="c1", revision_id="r1")
        lifecycle = {"claim_id": "x", "revision_id": "y"}
        self.assertEqual(len(CS.check_matches_lifecycle(score, lifecycle)), 2)


class AgeDaysTests(unittest.TestCase):
    def test_ten_days_apart(self):
        self.assertAlmostEqual(CS.age_days("2026-09-01T00:00:00Z", NOW), 13.0, places=6)

    def test_z_suffix_and_offset_agree(self):
        self.assertEqual(
            CS.age_days("2026-09-01T00:00:00Z", NOW),
            CS.age_days("2026-09-01T00:00:00+00:00", NOW))

    def test_unparseable_returns_none(self):
        self.assertIsNone(CS.age_days("garbage", NOW))

    def test_none_returns_none(self):
        self.assertIsNone(CS.age_days(None, NOW))


class BrierTests(unittest.TestCase):
    def test_perfect_forecast_true_scores_zero(self):
        self.assertEqual(CS.brier(1.0, True), 0.0)

    def test_perfect_forecast_false_scores_zero(self):
        self.assertEqual(CS.brier(0.0, False), 0.0)

    def test_worst_forecast_scores_one(self):
        self.assertEqual(CS.brier(0.0, True), 1.0)
        self.assertEqual(CS.brier(1.0, False), 1.0)

    def test_fifty_fifty_forecast_scores_a_quarter(self):
        self.assertAlmostEqual(CS.brier(0.5, True), 0.25)
        self.assertAlmostEqual(CS.brier(0.5, False), 0.25)


class ScoreOneTests(unittest.TestCase):
    def test_draft_claim_is_neither_decision_grade_nor_resolved(self):
        row = CS.score_one(interval_rec(), "DRAFT", now=NOW)
        self.assertFalse(row["decision_grade"])
        self.assertFalse(row["resolved"])
        self.assertFalse(row["scored"])
        self.assertIsNotNone(row["age_days"])

    def test_decision_used_unresolved_claim_reports_age_only(self):
        row = CS.score_one(interval_rec(), "DECISION_USED", now=NOW)
        self.assertTrue(row["decision_grade"])
        self.assertFalse(row["resolved"])
        self.assertIsNotNone(row["age_days"])
        self.assertIsNone(row["in_interval"])

    def test_resolved_but_not_scored_withholds_outcome_dimensions(self):
        rec = interval_rec(resolved_at="2026-09-10T00:00:00Z", outcome_value=15.0)
        row = CS.score_one(rec, "RESOLVED", now=NOW)
        self.assertTrue(row["resolved"])
        self.assertFalse(row["scored"])
        self.assertIsNone(row["age_days"])  # resolved: age no longer tracked
        self.assertIsNone(row["in_interval"])  # SCORED gate withholds this
        self.assertIsNone(row["interval_width"])

    def test_scored_interval_claim_inside_bounds(self):
        rec = interval_rec(resolved_at="2026-09-10T00:00:00Z", outcome_value=15.0)
        row = CS.score_one(rec, "SCORED", now=NOW)
        self.assertTrue(row["scored"])
        self.assertTrue(row["in_interval"])
        self.assertEqual(row["interval_width"], 10.0)

    def test_scored_interval_claim_outside_bounds(self):
        rec = interval_rec(resolved_at="2026-09-10T00:00:00Z", outcome_value=25.0)
        row = CS.score_one(rec, "SCORED", now=NOW)
        self.assertFalse(row["in_interval"])
        self.assertEqual(row["interval_width"], 10.0)

    def test_scored_interval_boundary_values_count_as_inside(self):
        rec = interval_rec(resolved_at="2026-09-10T00:00:00Z", outcome_value=10.0)
        self.assertTrue(CS.score_one(rec, "SCORED", now=NOW)["in_interval"])
        rec2 = interval_rec(resolved_at="2026-09-10T00:00:00Z", outcome_value=20.0)
        self.assertTrue(CS.score_one(rec2, "SCORED", now=NOW)["in_interval"])

    def test_scored_probability_claim_computes_brier(self):
        rec = prob_rec(stated_probability=0.8, resolved_at="2026-09-10T00:00:00Z", outcome_value=True)
        row = CS.score_one(rec, "SCORED", now=NOW)
        self.assertAlmostEqual(row["brier"], 0.04)
        self.assertIsNone(row["baseline_brier"])

    def test_scored_probability_claim_with_baseline_computes_baseline_brier(self):
        rec = prob_rec(stated_probability=0.8, baseline_probability=0.5,
                        resolved_at="2026-09-10T00:00:00Z", outcome_value=True)
        row = CS.score_one(rec, "SCORED", now=NOW)
        self.assertAlmostEqual(row["brier"], 0.04)
        self.assertAlmostEqual(row["baseline_brier"], 0.25)

    def test_scored_state_string_from_full_lifecycle_chain_is_accepted(self):
        # score_one must accept every real claim_lifecycle.STATES value,
        # not a private re-declared list that could drift from it.
        for state in CS.STATES:
            row = CS.score_one(interval_rec(), state, now=NOW)
            self.assertEqual(row["lifecycle_state"], state)


class AggregateResolutionRateTests(unittest.TestCase):
    def test_no_decision_grade_claims_is_no_data(self):
        entries = [(interval_rec(), "DRAFT"), (prob_rec(), "CHECKED")]
        result = CS.aggregate(entries, now=NOW)
        self.assertEqual(result["dimensions"]["resolution_rate"]["verdict"], "NO-DATA")

    def test_half_of_decision_grade_claims_scored(self):
        entries = [
            (interval_rec(claim_id="a", resolved_at="2026-09-10T00:00:00Z", outcome_value=15.0), "SCORED"),
            (interval_rec(claim_id="b"), "DECISION_USED"),
        ]
        result = CS.aggregate(entries, now=NOW)
        dim = result["dimensions"]["resolution_rate"]
        self.assertEqual(dim["verdict"], "PASS")
        self.assertAlmostEqual(dim["value"], 0.5)
        self.assertEqual(dim["n"], 2)

    def test_draft_claims_never_count_toward_the_denominator(self):
        entries = [(interval_rec(claim_id="a"), "DRAFT"),
                   (interval_rec(claim_id="b", resolved_at="2026-09-10T00:00:00Z",
                                  outcome_value=15.0), "SCORED")]
        result = CS.aggregate(entries, now=NOW)
        dim = result["dimensions"]["resolution_rate"]
        self.assertEqual(dim["n"], 1)
        self.assertAlmostEqual(dim["value"], 1.0)


class AggregateIntervalTests(unittest.TestCase):
    def test_no_scored_interval_claims_is_no_data_for_coverage_and_sharpness(self):
        entries = [(prob_rec(), "DRAFT")]
        result = CS.aggregate(entries, now=NOW)
        self.assertEqual(result["dimensions"]["interval_coverage"]["verdict"], "NO-DATA")
        self.assertEqual(result["dimensions"]["interval_sharpness"]["verdict"], "NO-DATA")

    def test_coverage_and_sharpness_computed_over_scored_interval_claims_only(self):
        entries = [
            (interval_rec(claim_id="a", stated_interval={"lower": 0.0, "upper": 10.0},
                           resolved_at="2026-09-10T00:00:00Z", outcome_value=5.0), "SCORED"),
            (interval_rec(claim_id="b", stated_interval={"lower": 0.0, "upper": 100.0},
                           resolved_at="2026-09-10T00:00:00Z", outcome_value=5.0), "SCORED"),
            (interval_rec(claim_id="c", stated_interval={"lower": 0.0, "upper": 10.0},
                           resolved_at="2026-09-10T00:00:00Z", outcome_value=99.0), "SCORED"),
            # Not yet SCORED: must not count even though outcome is known.
            (interval_rec(claim_id="d", stated_interval={"lower": 0.0, "upper": 10.0},
                           resolved_at="2026-09-10T00:00:00Z", outcome_value=5.0), "RESOLVED"),
        ]
        result = CS.aggregate(entries, now=NOW)
        coverage = result["dimensions"]["interval_coverage"]
        sharpness = result["dimensions"]["interval_sharpness"]
        self.assertEqual(coverage["n"], 3)
        self.assertAlmostEqual(coverage["value"], 2 / 3)
        self.assertEqual(sharpness["n"], 3)
        self.assertAlmostEqual(sharpness["value"], (10.0 + 100.0 + 10.0) / 3)

    def test_wide_interval_inflates_coverage_but_sharpness_shows_it(self):
        # The roadmap's exact stated purpose for this WBS unit: a wide
        # interval always covers, but the separate sharpness dimension
        # exposes that it is doing so by being uninformatively wide.
        wide = [(interval_rec(claim_id="w%d" % i, stated_interval={"lower": -1000.0, "upper": 1000.0},
                               resolved_at="2026-09-10T00:00:00Z", outcome_value=float(i)), "SCORED")
                for i in range(5)]
        result = CS.aggregate(wide, now=NOW)
        coverage = result["dimensions"]["interval_coverage"]
        sharpness = result["dimensions"]["interval_sharpness"]
        self.assertAlmostEqual(coverage["value"], 1.0)
        self.assertAlmostEqual(sharpness["value"], 2000.0)


class AggregateProperScoreTests(unittest.TestCase):
    def test_no_scored_probability_claims_is_no_data(self):
        entries = [(interval_rec(), "SCORED")]
        result = CS.aggregate(entries, now=NOW)
        self.assertEqual(result["dimensions"]["proper_score_brier"]["verdict"], "NO-DATA")

    def test_interval_claims_never_feed_the_probability_dimension(self):
        entries = [
            (interval_rec(claim_id="a", resolved_at="2026-09-10T00:00:00Z", outcome_value=15.0), "SCORED"),
            (prob_rec(claim_id="b", stated_probability=1.0, resolved_at="2026-09-10T00:00:00Z",
                      outcome_value=True), "SCORED"),
        ]
        result = CS.aggregate(entries, now=NOW)
        dim = result["dimensions"]["proper_score_brier"]
        self.assertEqual(dim["n"], 1)
        self.assertAlmostEqual(dim["value"], 0.0)

    def test_mean_brier_across_multiple_claims(self):
        entries = [
            (prob_rec(claim_id="a", stated_probability=1.0, resolved_at="2026-09-10T00:00:00Z",
                      outcome_value=True), "SCORED"),
            (prob_rec(claim_id="b", stated_probability=0.0, resolved_at="2026-09-10T00:00:00Z",
                      outcome_value=True), "SCORED"),
        ]
        result = CS.aggregate(entries, now=NOW)
        dim = result["dimensions"]["proper_score_brier"]
        self.assertAlmostEqual(dim["value"], 0.5)


class AggregateSkillTests(unittest.TestCase):
    def test_no_baseline_data_is_no_data(self):
        entries = [(prob_rec(stated_probability=0.9, resolved_at="2026-09-10T00:00:00Z",
                              outcome_value=True), "SCORED")]
        result = CS.aggregate(entries, now=NOW)
        self.assertEqual(result["dimensions"]["baseline_relative_skill"]["verdict"], "NO-DATA")

    def test_beating_the_baseline_is_a_positive_skill_score(self):
        entries = [(prob_rec(stated_probability=0.9, baseline_probability=0.5,
                              resolved_at="2026-09-10T00:00:00Z", outcome_value=True), "SCORED")]
        result = CS.aggregate(entries, now=NOW)
        dim = result["dimensions"]["baseline_relative_skill"]
        self.assertEqual(dim["verdict"], "PASS")
        self.assertGreater(dim["value"], 0.0)

    def test_losing_to_the_baseline_is_a_negative_skill_score(self):
        entries = [(prob_rec(stated_probability=0.1, baseline_probability=0.5,
                              resolved_at="2026-09-10T00:00:00Z", outcome_value=True), "SCORED")]
        result = CS.aggregate(entries, now=NOW)
        dim = result["dimensions"]["baseline_relative_skill"]
        self.assertLess(dim["value"], 0.0)

    def test_matching_the_baseline_exactly_is_zero_skill(self):
        entries = [(prob_rec(stated_probability=0.5, baseline_probability=0.5,
                              resolved_at="2026-09-10T00:00:00Z", outcome_value=True), "SCORED")]
        result = CS.aggregate(entries, now=NOW)
        self.assertAlmostEqual(result["dimensions"]["baseline_relative_skill"]["value"], 0.0)


class AggregateAgeTests(unittest.TestCase):
    def test_no_unresolved_claims_is_no_data(self):
        entries = [(interval_rec(resolved_at="2026-09-10T00:00:00Z", outcome_value=15.0), "SCORED")]
        result = CS.aggregate(entries, now=NOW)
        self.assertEqual(result["dimensions"]["age_of_unresolved"]["verdict"], "NO-DATA")

    def test_scored_claims_never_count_as_unresolved(self):
        entries = [(interval_rec(resolved_at="2026-09-10T00:00:00Z", outcome_value=15.0), "SCORED")]
        result = CS.aggregate(entries, now=NOW)
        self.assertEqual(result["dimensions"]["age_of_unresolved"]["n"], 0)

    def test_resolved_but_not_scored_still_counts_as_resolved_not_unresolved(self):
        # The module's own stated inference: age_of_unresolved uses the
        # plain-English "resolved" (RESOLVED or SCORED), not the SCORED
        # gate the outcome dimensions use.
        entries = [(interval_rec(resolved_at="2026-09-10T00:00:00Z", outcome_value=15.0), "RESOLVED")]
        result = CS.aggregate(entries, now=NOW)
        self.assertEqual(result["dimensions"]["age_of_unresolved"]["n"], 0)

    def test_ages_reported_for_unresolved_claims_oldest_first(self):
        entries = [
            (interval_rec(claim_id="young", created_at="2026-09-10T00:00:00Z"), "DRAFT"),
            (interval_rec(claim_id="old", created_at="2026-08-01T00:00:00Z"), "FROZEN"),
        ]
        result = CS.aggregate(entries, now=NOW)
        dim = result["dimensions"]["age_of_unresolved"]
        self.assertEqual(dim["verdict"], "PASS")
        self.assertEqual(dim["n"], 2)
        self.assertEqual(dim["value"]["oldest_claim_id"], "old")
        self.assertGreater(dim["value"]["max_days"], dim["value"]["min_days"])


class NoBlendedCompositeTests(unittest.TestCase):
    def test_the_report_carries_no_top_level_verdict_or_score_field(self):
        # The roadmap's own words: "No blended composite." Structural
        # proof, not a docstring claim: the top-level report has exactly
        # schema_version/computed_at/cohort_size/dimensions, and every
        # dimension is independent under its own key.
        entries = [(interval_rec(resolved_at="2026-09-10T00:00:00Z", outcome_value=15.0), "SCORED")]
        result = CS.aggregate(entries, now=NOW)
        self.assertEqual(
            set(result.keys()),
            {"schema_version", "computed_at", "cohort_size", "dimensions"})
        for forbidden in ("verdict", "score", "composite", "overall"):
            self.assertNotIn(forbidden, result)
        self.assertEqual(
            set(result["dimensions"].keys()),
            {"resolution_rate", "interval_coverage", "interval_sharpness",
             "proper_score_brier", "baseline_relative_skill", "age_of_unresolved"})


class ReportAndMainCliTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()

    def write(self, name, data):
        path = os.path.join(self.tmp, name)
        with open(path, "w") as fh:
            json.dump(data, fh)
        return path

    def test_check_command_exits_0_on_pass(self):
        path = self.write("r.json", interval_rec())
        self.assertEqual(CS.main(["check", path]), 0)

    def test_check_command_exits_2_on_missing_file(self):
        self.assertEqual(CS.main(["check", "/no/such/record.json"]), 2)

    def test_check_command_exits_1_on_structural_fail(self):
        r = interval_rec()
        del r["claim_type"]
        path = self.write("r.json", r)
        self.assertEqual(CS.main(["check", path]), 1)

    def test_check_command_with_matching_lifecycle_record_exits_0(self):
        score_path = self.write("score.json", interval_rec(claim_id="c1", revision_id="r1"))
        lifecycle_path = self.write("lifecycle.json", {
            "schema_version": "claim-lifecycle-v1", "claim_id": "c1",
            "revision_id": "r1", "state": "DRAFT", "supersedes": None,
        })
        self.assertEqual(CS.main(["check", score_path, "--lifecycle-record", lifecycle_path]), 0)

    def test_check_command_with_mismatched_lifecycle_record_exits_1(self):
        score_path = self.write("score.json", interval_rec(claim_id="c1", revision_id="r1"))
        lifecycle_path = self.write("lifecycle.json", {
            "schema_version": "claim-lifecycle-v1", "claim_id": "c1",
            "revision_id": "r2", "state": "DRAFT", "supersedes": None,
        })
        self.assertEqual(CS.main(["check", score_path, "--lifecycle-record", lifecycle_path]), 1)

    def test_report_command_on_a_clean_cohort_exits_0_and_prints_dimensions(self):
        cohort = [
            {"score": interval_rec(claim_id="a", resolved_at="2026-09-10T00:00:00Z", outcome_value=15.0),
             "lifecycle_state": "SCORED"},
            {"score": prob_rec(claim_id="b"), "lifecycle_state": "DRAFT"},
        ]
        path = self.write("cohort.json", cohort)
        out_path = os.path.join(self.tmp, "out.json")
        self.assertEqual(CS.main(["report", path, "--out", out_path]), 0)
        with open(out_path) as fh:
            written = json.load(fh)
        self.assertIn("dimensions", written)
        self.assertEqual(written["dimensions"]["resolution_rate"]["verdict"], "PASS")

    def test_report_command_on_a_malformed_entry_exits_1_and_names_the_index(self):
        cohort = [{"score": interval_rec(stated_interval=None), "lifecycle_state": "DRAFT"}]
        path = self.write("cohort.json", cohort)
        self.assertEqual(CS.main(["report", path]), 1)

    def test_report_command_on_an_unknown_lifecycle_state_exits_1(self):
        cohort = [{"score": interval_rec(), "lifecycle_state": "APPROVED"}]
        path = self.write("cohort.json", cohort)
        self.assertEqual(CS.main(["report", path]), 1)

    def test_report_command_on_missing_file_exits_2(self):
        self.assertEqual(CS.main(["report", "/no/such/cohort.json"]), 2)

    def test_report_command_on_non_list_top_level_exits_2(self):
        path = self.write("cohort.json", {"not": "a list"})
        self.assertEqual(CS.main(["report", path]), 2)

    def test_report_function_returns_none_result_with_problems_on_bad_entry(self):
        cohort = [{"score": "not-an-object", "lifecycle_state": "DRAFT"}]
        path = self.write("cohort.json", cohort)
        schema = CC.load_json(CS.DEFAULT_SCHEMA, "schema")
        result, problems = CS.report(path, schema, now=NOW)
        self.assertIsNone(result)
        self.assertTrue(any("cohort[0].score" in p for p in problems), problems)


if __name__ == "__main__":
    unittest.main()

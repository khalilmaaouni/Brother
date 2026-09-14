#!/usr/bin/env python3
"""Calibration for scripts/clerical_review_plan.py (WBS-40.06).

The property under test is reproducibility and honesty, not sampling
statistics: the same seed against the same population always returns the
identical plan, a different seed changes the sample, plan_identity is a
property of the population (not of incidental list order), and any
precision-shaped output is structurally NO-DATA at plan-creation time
because no clerical review outcomes exist yet. Synthetic fixture data only.
"""
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import clerical_review_plan as CRP  # noqa: E402
import evidence_obligation as EO  # noqa: E402


def _candidate(candidate_id, score, pathway, false_merge_cost_class="MEDIUM",
                missed_match_cost_class="MEDIUM", unmatched_reason=None, segment=None):
    """One CLERICAL_REVIEW-bucketed routed candidate record: the source
    candidate fields route_candidate reads, plus the routing decision
    route_batch attaches, plus pathway (matcher_boundary.py's own field)
    and the optional unmatched_reason/segment pass-through fields."""
    record = {
        "candidate_id": candidate_id,
        "score": score,
        "false_merge_cost_class": false_merge_cost_class,
        "missed_match_cost_class": missed_match_cost_class,
        "merge_threshold": 0.9,
        "review_threshold": 0.5,
        "routing": "CLERICAL_REVIEW",
        "reason": "synthetic fixture",
        "pathway": pathway,
    }
    if unmatched_reason is not None:
        record["unmatched_reason"] = unmatched_reason
    if segment is not None:
        record["segment"] = segment
    return record


def _synthetic_population():
    """Multiple strata, several members each: two pathways, several score
    bands, one stratum carrying unmatched_reason. Large enough per stratum
    that two different seeds are not expected to coincidentally draw the
    same sample."""
    population = []
    for i in range(8):
        population.append(_candidate(
            "fuzzy-%02d" % i, score=0.55 + 0.01 * i, pathway="fuzzy_name_phone_block"))
    for i in range(8):
        population.append(_candidate(
            "exact-%02d" % i, score=0.75 + 0.01 * i, pathway="exact_tax_id_block"))
    for i in range(6):
        population.append(_candidate(
            "unmatched-%02d" % i, score=0.60 + 0.01 * i, pathway="fuzzy_name_phone_block",
            unmatched_reason="address_conflict"))
    return population


class BuildSamplingPlanTests(unittest.TestCase):
    def test_same_seed_same_population_is_byte_identical(self):
        population = _synthetic_population()
        plan_a = CRP.build_sampling_plan(population, seed=42)
        plan_b = CRP.build_sampling_plan(population, seed=42)
        self.assertEqual(plan_a, plan_b)

    def test_different_seed_changes_the_sample(self):
        population = _synthetic_population()
        plan_a = CRP.build_sampling_plan(population, seed=1)
        plan_b = CRP.build_sampling_plan(population, seed=2)
        sampled_a = {cid for s in plan_a["strata"] for cid in s["sampled_candidate_ids"]}
        sampled_b = {cid for s in plan_b["strata"] for cid in s["sampled_candidate_ids"]}
        self.assertNotEqual(sampled_a, sampled_b)
        self.assertNotEqual(plan_a["plan_identity"], plan_b["plan_identity"])

    def test_plan_identity_differs_for_a_different_population(self):
        population = _synthetic_population()
        plan_a = CRP.build_sampling_plan(population, seed=42)
        changed = population + [_candidate("extra-00", score=0.61, pathway="exact_tax_id_block")]
        plan_b = CRP.build_sampling_plan(changed, seed=42)
        self.assertNotEqual(plan_a["plan_identity"], plan_b["plan_identity"])

    def test_plan_identity_differs_when_a_stratification_field_changes(self):
        # Same candidate_ids, one candidate's pathway changed -- still a
        # different population as far as the plan is concerned.
        population = _synthetic_population()
        relabeled = [dict(c) for c in population]
        relabeled[0]["pathway"] = "a_new_pathway_nobody_has_seen"
        plan_a = CRP.build_sampling_plan(population, seed=42)
        plan_b = CRP.build_sampling_plan(relabeled, seed=42)
        self.assertNotEqual(plan_a["plan_identity"], plan_b["plan_identity"])

    def test_input_list_order_never_changes_plan_identity(self):
        population = _synthetic_population()
        shuffled = list(reversed(population))
        plan_a = CRP.build_sampling_plan(population, seed=42)
        plan_b = CRP.build_sampling_plan(shuffled, seed=42)
        self.assertEqual(plan_a["plan_identity"], plan_b["plan_identity"])

    def test_strata_preserve_pathway_score_band_and_unmatched_reason(self):
        population = _synthetic_population()
        plan = CRP.build_sampling_plan(population, seed=42)
        labels = {s["label"] for s in plan["strata"]}
        pathways = {s["pathway"] for s in plan["strata"]}
        self.assertIn("fuzzy_name_phone_block", pathways)
        self.assertIn("exact_tax_id_block", pathways)
        unmatched_strata = [s for s in plan["strata"] if s["unmatched_reason"] == "address_conflict"]
        self.assertTrue(unmatched_strata, "expected at least one stratum carrying unmatched_reason")
        for s in plan["strata"]:
            self.assertIn("score_band=", s["label"])
        self.assertTrue(labels)

    def test_population_and_stratum_counts_are_honest(self):
        population = _synthetic_population()
        plan = CRP.build_sampling_plan(population, seed=42)
        self.assertEqual(plan["population"], len(population))
        self.assertEqual(sum(s["population"] for s in plan["strata"]), len(population))
        for s in plan["strata"]:
            self.assertLessEqual(s["sampled_count"], s["population"])
            self.assertEqual(s["sampled_count"], len(s["sampled_candidate_ids"]))
            self.assertEqual(len(set(s["sampled_candidate_ids"])), s["sampled_count"])

    def test_nonempty_stratum_at_default_fraction_samples_at_least_one(self):
        population = _synthetic_population()
        plan = CRP.build_sampling_plan(population, seed=42)
        for s in plan["strata"]:
            self.assertGreaterEqual(s["sampled_count"], 1)

    def test_sample_fraction_per_stratum_override(self):
        population = _synthetic_population()
        plan = CRP.build_sampling_plan(population, seed=42, sample_fraction_per_stratum=1.0)
        for s in plan["strata"]:
            self.assertEqual(s["sampled_count"], s["population"])

    def test_rejects_a_candidate_routed_outside_clerical_review(self):
        population = _synthetic_population()
        population[0]["routing"] = "AUTO_ACCEPT"
        with self.assertRaises(ValueError):
            CRP.build_sampling_plan(population, seed=42)

    def test_rejects_non_int_seed(self):
        population = _synthetic_population()
        with self.assertRaises(ValueError):
            CRP.build_sampling_plan(population, seed="not-an-int")

    def test_empty_population_yields_an_empty_plan(self):
        plan = CRP.build_sampling_plan([], seed=42)
        self.assertEqual(plan["population"], 0)
        self.assertEqual(plan["strata"], [])
        # Still a real, reproducible identity for the empty population.
        self.assertEqual(plan["plan_identity"], CRP.build_sampling_plan([], seed=42)["plan_identity"])


class PrecisionRefusalTests(unittest.TestCase):
    def test_precision_estimate_is_structurally_no_data(self):
        self.assertEqual(CRP.precision_estimate(), "NO-DATA")
        self.assertIn(CRP.precision_estimate(), EO.VERDICTS)

    def test_plan_precision_field_is_no_data_at_creation_time(self):
        population = _synthetic_population()
        plan = CRP.build_sampling_plan(population, seed=42)
        self.assertEqual(plan["precision"], "NO-DATA")

    def test_plan_precision_field_stays_no_data_even_at_full_sample(self):
        # Sampling 100% of the population still means zero clerical
        # OUTCOMES exist yet (nobody has reviewed anything) -- precision
        # must stay NO-DATA regardless of sample size.
        population = _synthetic_population()
        plan = CRP.build_sampling_plan(population, seed=42, sample_fraction_per_stratum=1.0)
        self.assertEqual(plan["precision"], "NO-DATA")


if __name__ == "__main__":
    unittest.main()

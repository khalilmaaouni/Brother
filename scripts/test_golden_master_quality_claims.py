#!/usr/bin/env python3
"""Tests for golden_master_quality_claims.py (WBS-50.04 MDM quality claims)."""
import json
import os
import sys
import tempfile
import unittest

sys.path.insert(0, __file__.rsplit("/", 1)[0])
import claim_lifecycle as CL
import contract_check as CC
import golden_master_quality_claims as GQC
import risk_review_orchestrator as RRO
import survivorship_lineage as SL

NOW = "2026-09-14T00:00:00+00:00"

BASE_MASTER = {
    "schema_version": "golden-master-contract-v1", "master_id": "m1",
    "entity_type": "customer/location", "business_purpose": "x",
    "source_systems": ["s1"], "downstream_consumers": [],
    "critical_attributes": ["name"], "cardinality_constraints": [],
    "false_merge_cost_class": "high", "missed_match_cost_class": "medium",
    "review_policy": "human review above threshold", "merge_threshold": 0.9,
    "review_threshold": 0.5, "survivorship_policy_ref": "docs/x.md",
    "rollback_required": True, "quality_claim_ids": [],
    "locale_profiles": ["ja-JP"], "publish_authority": "data steward",
}

BASE_QUALITY = {
    "schema_version": "claim-mdm-quality-v1", "claim_id": "m1:precision_floor",
    "revision_id": "m1:precision_floor:r1", "master_id": "m1",
    "quality_dimension": "precision_floor", "comparison": "gte",
    "threshold": 0.95, "measured_value": None, "resolved_at": None,
    "evidence_note": None,
}

BASE_LIFECYCLE = {
    "schema_version": "claim-lifecycle-v1", "claim_id": "m1:precision_floor",
    "revision_id": "m1:precision_floor:r1", "state": "DRAFT", "supersedes": None,
}


def master(**overrides):
    rec = dict(BASE_MASTER)
    rec.update(overrides)
    return rec


def quality(**overrides):
    rec = dict(BASE_QUALITY)
    rec.update(overrides)
    return rec


def lifecycle(**overrides):
    rec = dict(BASE_LIFECYCLE)
    rec.update(overrides)
    return rec


class SchemaShapeTests(unittest.TestCase):
    def setUp(self):
        self.schema = CC.load_json(GQC.DEFAULT_SCHEMA, "schema")

    def test_an_unresolved_claim_passes(self):
        self.assertEqual(GQC.check(quality(), self.schema), [])

    def test_a_resolved_claim_passes(self):
        rec = quality(measured_value=0.97, resolved_at=NOW, evidence_note="x")
        self.assertEqual(GQC.check(rec, self.schema), [])

    def test_missing_required_top_level_field_is_refused(self):
        r = quality()
        del r["threshold"]
        problems = GQC.check(r, self.schema)
        self.assertTrue(any("threshold" in p for p in problems), problems)

    def test_wrong_schema_version_is_refused(self):
        problems = GQC.check(quality(schema_version="wrong"), self.schema)
        self.assertTrue(problems)

    def test_unknown_quality_dimension_is_refused(self):
        problems = GQC.check(quality(quality_dimension="made_up"), self.schema)
        self.assertTrue(problems)

    def test_unknown_comparison_is_refused(self):
        problems = GQC.check(quality(comparison="eq"), self.schema)
        self.assertTrue(problems)

    def test_additional_top_level_property_is_refused(self):
        r = quality()
        r["not_a_real_field"] = "x"
        problems = GQC.check(r, self.schema)
        self.assertTrue(any("unexpected field" in p for p in problems), problems)

    def test_every_dimension_value_is_individually_valid(self):
        for dim in GQC.DIMENSIONS:
            self.assertEqual(GQC.check(quality(quality_dimension=dim), self.schema), [])

    def test_dimensions_match_the_schema_enum(self):
        # Catches drift between the module's own DIMENSIONS tuple and the
        # schema's own quality_dimension enum (the same by-hand pairing
        # claim_lifecycle.py's STATES keeps with claim-lifecycle-v1).
        self.assertEqual(
            set(GQC.DIMENSIONS),
            set(self.schema["properties"]["quality_dimension"]["enum"]))


class ResolutionConsistencyTests(unittest.TestCase):
    def setUp(self):
        self.schema = CC.load_json(GQC.DEFAULT_SCHEMA, "schema")

    def test_measured_value_without_resolved_at_is_refused(self):
        problems = GQC.check(quality(measured_value=0.9), self.schema)
        self.assertTrue(any("measured_value/resolved_at" in p for p in problems), problems)

    def test_resolved_at_without_measured_value_is_refused(self):
        problems = GQC.check(quality(resolved_at=NOW), self.schema)
        self.assertTrue(any("measured_value/resolved_at" in p for p in problems), problems)

    def test_neither_set_is_accepted(self):
        self.assertEqual(GQC.check(quality(), self.schema), [])

    def test_both_set_is_accepted(self):
        rec = quality(measured_value=0.9, resolved_at=NOW)
        self.assertEqual(GQC.check(rec, self.schema), [])

    def test_unparseable_resolved_at_is_refused(self):
        rec = quality(measured_value=0.9, resolved_at="not-a-date")
        problems = GQC.check(rec, self.schema)
        self.assertTrue(any("resolved_at" in p and "ISO-8601" in p for p in problems), problems)

    def test_evidence_note_may_be_set_while_unresolved(self):
        # Honest NO-DATA: a reason recorded without a measured_value.
        rec = quality(evidence_note="no evidence source exists yet")
        self.assertEqual(GQC.check(rec, self.schema), [])


class CheckMatchesLifecycleTests(unittest.TestCase):
    def test_matching_ids_pass(self):
        self.assertEqual(GQC.check_matches_lifecycle(quality(), lifecycle()), [])

    def test_mismatched_claim_id_is_refused(self):
        problems = GQC.check_matches_lifecycle(quality(), lifecycle(claim_id="other"))
        self.assertTrue(any("claim_id" in p for p in problems), problems)

    def test_mismatched_revision_id_is_refused(self):
        problems = GQC.check_matches_lifecycle(quality(), lifecycle(revision_id="other"))
        self.assertTrue(any("revision_id" in p and "rewritten successor" in p for p in problems), problems)


class CheckMatchesMasterTests(unittest.TestCase):
    def test_matching_master_and_listed_claim_id_pass(self):
        m = master(quality_claim_ids=["m1:precision_floor"])
        self.assertEqual(GQC.check_matches_master(quality(), m), [])

    def test_mismatched_master_id_is_refused(self):
        m = master(master_id="different", quality_claim_ids=["m1:precision_floor"])
        problems = GQC.check_matches_master(quality(), m)
        self.assertTrue(any("master_id" in p for p in problems), problems)

    def test_claim_id_absent_from_quality_claim_ids_is_refused(self):
        m = master(quality_claim_ids=["m1:recall_floor"])
        problems = GQC.check_matches_master(quality(), m)
        self.assertTrue(any("quality_claim_ids" in p for p in problems), problems)

    def test_empty_quality_claim_ids_is_refused_for_any_real_claim(self):
        problems = GQC.check_matches_master(quality(), master(quality_claim_ids=[]))
        self.assertTrue(problems)


class RegisterClaimsTests(unittest.TestCase):
    def test_registers_one_pair_per_threshold_entry(self):
        pairs = GQC.register_claims(master(), {
            "precision_floor": {"threshold": 0.95, "comparison": "gte"},
            "recall_floor": {"threshold": 0.90, "comparison": "gte"},
        })
        self.assertEqual(len(pairs), 2)

    def test_claim_id_is_deterministic_from_master_id_and_dimension(self):
        pairs = GQC.register_claims(
            master(master_id="m9"), {"segment_floor": {"threshold": 0.8, "comparison": "gte"}})
        lc, qc = pairs[0]
        self.assertEqual(lc["claim_id"], "m9:segment_floor")
        self.assertEqual(qc["claim_id"], "m9:segment_floor")
        self.assertEqual(lc["revision_id"], "m9:segment_floor:r1")
        self.assertEqual(qc["revision_id"], "m9:segment_floor:r1")

    def test_registered_lifecycle_record_starts_at_draft(self):
        pairs = GQC.register_claims(
            master(), {"precision_floor": {"threshold": 0.95, "comparison": "gte"}})
        lc, _qc = pairs[0]
        self.assertEqual(lc["state"], "DRAFT")
        self.assertIsNone(lc["supersedes"])

    def test_registered_lifecycle_record_validates_as_claim_lifecycle_v1(self):
        schema = CC.load_json(CL.DEFAULT_SCHEMA, "schema")
        pairs = GQC.register_claims(
            master(), {"precision_floor": {"threshold": 0.95, "comparison": "gte"}})
        lc, _qc = pairs[0]
        self.assertEqual(CL.check(lc, schema), [])

    def test_registered_quality_record_validates_as_claim_mdm_quality_v1(self):
        schema = CC.load_json(GQC.DEFAULT_SCHEMA, "schema")
        pairs = GQC.register_claims(
            master(), {"precision_floor": {"threshold": 0.95, "comparison": "gte"}})
        _lc, qc = pairs[0]
        self.assertEqual(GQC.check(qc, schema), [])

    def test_registered_pair_matches_lifecycle_and_master(self):
        m = master()
        pairs = GQC.register_claims(
            m, {"precision_floor": {"threshold": 0.95, "comparison": "gte"}})
        lc, qc = pairs[0]
        self.assertEqual(GQC.check_matches_lifecycle(qc, lc), [])
        m["quality_claim_ids"] = [qc["claim_id"]]
        self.assertEqual(GQC.check_matches_master(qc, m), [])

    def test_unknown_dimension_is_refused(self):
        with self.assertRaises(GQC.RegistrationError):
            GQC.register_claims(master(), {"made_up": {"threshold": 1, "comparison": "gte"}})

    def test_unknown_comparison_is_refused(self):
        with self.assertRaises(GQC.RegistrationError):
            GQC.register_claims(
                master(), {"precision_floor": {"threshold": 0.9, "comparison": "eq"}})

    def test_non_numeric_threshold_is_refused(self):
        with self.assertRaises(GQC.RegistrationError):
            GQC.register_claims(
                master(), {"precision_floor": {"threshold": "high", "comparison": "gte"}})

    def test_boolean_threshold_is_refused(self):
        # bool is a subtype of int in Python; must not silently pass as numeric.
        with self.assertRaises(GQC.RegistrationError):
            GQC.register_claims(
                master(), {"precision_floor": {"threshold": True, "comparison": "gte"}})

    def test_missing_master_id_is_refused(self):
        m = master()
        del m["master_id"]
        with self.assertRaises(GQC.RegistrationError):
            GQC.register_claims(m, {"precision_floor": {"threshold": 0.9, "comparison": "gte"}})

    def test_empty_thresholds_registers_nothing(self):
        self.assertEqual(GQC.register_claims(master(), {}), [])


class FreezeClaimsTests(unittest.TestCase):
    def test_freeze_advances_draft_to_frozen(self):
        pairs = GQC.register_claims(
            master(), {"precision_floor": {"threshold": 0.95, "comparison": "gte"}})
        frozen = GQC.freeze_claims(pairs)
        lc, _qc = frozen[0]
        self.assertEqual(lc["state"], "FROZEN")

    def test_freeze_preserves_claim_and_revision_identity(self):
        pairs = GQC.register_claims(
            master(), {"precision_floor": {"threshold": 0.95, "comparison": "gte"}})
        original_lc, original_qc = pairs[0]
        frozen_lc, frozen_qc = GQC.freeze_claims(pairs)[0]
        self.assertEqual(frozen_lc["claim_id"], original_lc["claim_id"])
        self.assertEqual(frozen_lc["revision_id"], original_lc["revision_id"])
        self.assertEqual(frozen_qc, original_qc)  # quality content untouched by freezing

    def test_frozen_lifecycle_record_validates_as_claim_lifecycle_v1(self):
        schema = CC.load_json(CL.DEFAULT_SCHEMA, "schema")
        pairs = GQC.register_claims(
            master(), {"precision_floor": {"threshold": 0.95, "comparison": "gte"}})
        lc, _qc = GQC.freeze_claims(pairs)[0]
        self.assertEqual(CL.check(lc, schema), [])

    def test_freeze_uses_claim_lifecycles_own_transition_chain(self):
        # Reuse, not reinvent: every intermediate step is a valid
        # claim_lifecycle.py transition (DRAFT -> CHECKED -> FROZEN).
        pairs = GQC.register_claims(
            master(), {"precision_floor": {"threshold": 0.95, "comparison": "gte"}})
        draft = pairs[0][0]
        checked = dict(draft, state="CHECKED")
        self.assertEqual(CL.check_transition(draft, checked), [])
        frozen = dict(checked, state="FROZEN")
        self.assertEqual(CL.check_transition(checked, frozen), [])

    def test_freezing_a_non_draft_claim_is_refused(self):
        pairs = GQC.register_claims(
            master(), {"precision_floor": {"threshold": 0.95, "comparison": "gte"}})
        already_checked = [(dict(lc, state="CHECKED"), qc) for lc, qc in pairs]
        with self.assertRaises(GQC.RegistrationError):
            GQC.freeze_claims(already_checked)

    def test_freeze_multiple_pairs_independently(self):
        pairs = GQC.register_claims(master(), {
            "precision_floor": {"threshold": 0.95, "comparison": "gte"},
            "reconciliation_tolerance": {"threshold": 0.05, "comparison": "lte"},
        })
        frozen = GQC.freeze_claims(pairs)
        self.assertEqual(len(frozen), 2)
        for lc, _qc in frozen:
            self.assertEqual(lc["state"], "FROZEN")


class CheckRegisteredBeforePublicationTests(unittest.TestCase):
    def test_empty_quality_claim_ids_is_trivially_satisfied(self):
        problems = GQC.check_registered_before_publication(master(quality_claim_ids=[]), {})
        self.assertEqual(problems, [])

    def test_all_frozen_claims_pass_the_gate(self):
        pairs = GQC.freeze_claims(GQC.register_claims(
            master(), {"precision_floor": {"threshold": 0.95, "comparison": "gte"}}))
        lc, qc = pairs[0]
        m = master(quality_claim_ids=[qc["claim_id"]])
        problems = GQC.check_registered_before_publication(m, {lc["claim_id"]: lc})
        self.assertEqual(problems, [])

    def test_a_claim_never_registered_fails_the_gate(self):
        m = master(quality_claim_ids=["m1:precision_floor"])
        problems = GQC.check_registered_before_publication(m, {})
        self.assertTrue(any("no claim-lifecycle-v1" in p for p in problems), problems)

    def test_a_draft_claim_fails_the_gate(self):
        m = master(quality_claim_ids=["m1:precision_floor"])
        problems = GQC.check_registered_before_publication(
            m, {"m1:precision_floor": lifecycle(state="DRAFT")})
        self.assertTrue(any("FROZEN or later" in p for p in problems), problems)

    def test_a_checked_but_not_frozen_claim_fails_the_gate(self):
        m = master(quality_claim_ids=["m1:precision_floor"])
        problems = GQC.check_registered_before_publication(
            m, {"m1:precision_floor": lifecycle(state="CHECKED")})
        self.assertTrue(any("FROZEN or later" in p for p in problems), problems)

    def test_a_claim_past_frozen_still_passes_the_gate(self):
        # DECISION_USED etc. are later than FROZEN in the chain; the gate
        # asks "FROZEN or later", not "exactly FROZEN".
        m = master(quality_claim_ids=["m1:precision_floor"])
        problems = GQC.check_registered_before_publication(
            m, {"m1:precision_floor": lifecycle(state="DECISION_USED")})
        self.assertEqual(problems, [])

    def test_malformed_quality_claim_ids_is_refused(self):
        m = master(quality_claim_ids="not-a-list")
        problems = GQC.check_registered_before_publication(m, {})
        self.assertTrue(any("quality_claim_ids" in p for p in problems), problems)


class ResolveCatastrophicFalseMergeToleranceTests(unittest.TestCase):
    def claim(self, threshold=0.0, comparison="lte"):
        return quality(
            claim_id="m1:catastrophic_false_merge_tolerance",
            revision_id="m1:catastrophic_false_merge_tolerance:r1",
            quality_dimension="catastrophic_false_merge_tolerance",
            comparison=comparison, threshold=threshold)

    def test_no_high_risk_candidates_is_no_data(self):
        candidates = [{
            "candidate_id": "c1", "score": 0.99, "false_merge_cost_class": "LOW",
            "missed_match_cost_class": "LOW", "merge_threshold": 0.9, "review_threshold": 0.5,
        }]
        resolved, verdict = GQC.resolve_catastrophic_false_merge_tolerance(
            self.claim(), candidates, now=NOW)
        self.assertEqual(verdict, "NO-DATA")
        self.assertIsNone(resolved["measured_value"])
        self.assertIsNone(resolved["resolved_at"])
        self.assertIn("no HIGH/CATASTROPHIC", resolved["evidence_note"])

    def test_every_high_risk_candidate_routed_to_review_is_zero_violation_rate(self):
        # risk_review_orchestrator's own anti-silent-auto-accept guarantee:
        # a valid CATASTROPHIC candidate always lands in CLERICAL_REVIEW.
        candidates = [{
            "candidate_id": "c1", "score": 0.99, "false_merge_cost_class": "CATASTROPHIC",
            "missed_match_cost_class": "LOW", "merge_threshold": 0.9, "review_threshold": 0.5,
        }]
        resolved, verdict = GQC.resolve_catastrophic_false_merge_tolerance(
            self.claim(threshold=0.0, comparison="lte"), candidates, now=NOW)
        self.assertEqual(verdict, "PASS")
        self.assertEqual(resolved["measured_value"], 0.0)
        self.assertEqual(resolved["resolved_at"], NOW)

    def test_a_refused_high_risk_candidate_counts_as_a_violation(self):
        # A malformed high-risk candidate is refused (routing=NO-DATA) by
        # risk_review_orchestrator rather than routed to CLERICAL_REVIEW --
        # that is real evidence the tolerance was not upheld, not a case
        # to silently exclude.
        candidates = [{
            "candidate_id": "c1", "score": "not-a-number",
            "false_merge_cost_class": "CATASTROPHIC", "missed_match_cost_class": "LOW",
            "merge_threshold": 0.9, "review_threshold": 0.5,
        }]
        resolved, verdict = GQC.resolve_catastrophic_false_merge_tolerance(
            self.claim(threshold=0.0, comparison="lte"), candidates, now=NOW)
        self.assertEqual(resolved["measured_value"], 1.0)
        self.assertEqual(verdict, "FAIL")

    def test_low_risk_candidates_never_count_toward_the_denominator(self):
        candidates = [
            {"candidate_id": "c1", "score": 0.99, "false_merge_cost_class": "LOW",
             "missed_match_cost_class": "LOW", "merge_threshold": 0.9, "review_threshold": 0.5},
            {"candidate_id": "c2", "score": 0.99, "false_merge_cost_class": "CATASTROPHIC",
             "missed_match_cost_class": "LOW", "merge_threshold": 0.9, "review_threshold": 0.5},
        ]
        resolved, verdict = GQC.resolve_catastrophic_false_merge_tolerance(
            self.claim(), candidates, now=NOW)
        self.assertIn("1 HIGH/CATASTROPHIC-cost candidate(s) of 2 total", resolved["evidence_note"])
        self.assertEqual(verdict, "PASS")

    def test_wrong_quality_dimension_is_refused(self):
        with self.assertRaises(GQC.RegistrationError):
            GQC.resolve_catastrophic_false_merge_tolerance(quality(), [])

    def test_real_route_batch_is_actually_invoked_not_reimplemented(self):
        # Cross-check against risk_review_orchestrator.route_batch directly
        # over the same candidates: same violation count either way.
        candidates = [{
            "candidate_id": "c1", "score": 0.99, "false_merge_cost_class": "HIGH",
            "missed_match_cost_class": "LOW", "merge_threshold": 0.9, "review_threshold": 0.5,
        }]
        decisions, _summary = RRO.route_batch(candidates)
        self.assertEqual(decisions[0]["routing"], "CLERICAL_REVIEW")
        resolved, verdict = GQC.resolve_catastrophic_false_merge_tolerance(
            self.claim(), candidates, now=NOW)
        self.assertEqual(resolved["measured_value"], 0.0)
        self.assertEqual(verdict, "PASS")


class RunReconciliationBatchTests(unittest.TestCase):
    def test_successful_reconciliation_is_recorded(self):
        fields = [(
            "email",
            {"crm": {"value": "a@x.test", "timestamp": "2026-01-01T00:00:00Z"},
             "pos": {"value": "b@x.test", "timestamp": "2026-02-01T00:00:00Z"}},
            SL.MOST_RECENT_SOURCE_WINS,
        )]
        attempts = GQC.run_reconciliation_batch(fields)
        self.assertEqual(len(attempts), 1)
        self.assertTrue(attempts[0]["success"])
        self.assertIsNotNone(attempts[0]["lineage"])
        self.assertIsNone(attempts[0]["error"])

    def test_a_reconciliation_that_raises_is_recorded_as_a_failure(self):
        # survivorship_lineage.resolve_field raises ValueError for empty
        # candidates; that raise is the real evidence of a failed
        # reconciliation, never silently swallowed or turned into a crash.
        fields = [("phone", {}, SL.MOST_COMPLETE_VALUE_WINS)]
        attempts = GQC.run_reconciliation_batch(fields)
        self.assertEqual(len(attempts), 1)
        self.assertFalse(attempts[0]["success"])
        self.assertIsNone(attempts[0]["lineage"])
        self.assertIn("no candidates", attempts[0]["error"])

    def test_mixed_batch_reports_each_field_independently(self):
        fields = [
            ("email",
             {"crm": {"value": "a@x.test", "timestamp": "2026-01-01T00:00:00Z"}},
             SL.MOST_RECENT_SOURCE_WINS),
            ("phone", {}, SL.MOST_COMPLETE_VALUE_WINS),
        ]
        attempts = GQC.run_reconciliation_batch(fields)
        self.assertTrue(attempts[0]["success"])
        self.assertFalse(attempts[1]["success"])


class ResolveReconciliationToleranceTests(unittest.TestCase):
    def claim(self, threshold=0.5, comparison="lte"):
        return quality(
            claim_id="m1:reconciliation_tolerance",
            revision_id="m1:reconciliation_tolerance:r1",
            quality_dimension="reconciliation_tolerance",
            comparison=comparison, threshold=threshold)

    def test_no_attempts_is_no_data(self):
        resolved, verdict = GQC.resolve_reconciliation_tolerance(self.claim(), [], now=NOW)
        self.assertEqual(verdict, "NO-DATA")
        self.assertIsNone(resolved["measured_value"])

    def test_all_successful_attempts_is_zero_failure_rate(self):
        attempts = GQC.run_reconciliation_batch([(
            "email",
            {"crm": {"value": "a@x.test", "timestamp": "2026-01-01T00:00:00Z"}},
            SL.MOST_RECENT_SOURCE_WINS,
        )])
        resolved, verdict = GQC.resolve_reconciliation_tolerance(
            self.claim(threshold=0.0, comparison="lte"), attempts, now=NOW)
        self.assertEqual(resolved["measured_value"], 0.0)
        self.assertEqual(verdict, "PASS")

    def test_some_failures_computes_a_real_failure_rate(self):
        attempts = GQC.run_reconciliation_batch([
            ("email",
             {"crm": {"value": "a@x.test", "timestamp": "2026-01-01T00:00:00Z"}},
             SL.MOST_RECENT_SOURCE_WINS),
            ("phone", {}, SL.MOST_COMPLETE_VALUE_WINS),
        ])
        resolved, verdict = GQC.resolve_reconciliation_tolerance(
            self.claim(threshold=0.1, comparison="lte"), attempts, now=NOW)
        self.assertAlmostEqual(resolved["measured_value"], 0.5)
        self.assertEqual(verdict, "FAIL")  # 0.5 > 0.1 tolerance

    def test_failure_rate_within_tolerance_passes(self):
        attempts = GQC.run_reconciliation_batch([
            ("email",
             {"crm": {"value": "a@x.test", "timestamp": "2026-01-01T00:00:00Z"}},
             SL.MOST_RECENT_SOURCE_WINS),
            ("phone", {}, SL.MOST_COMPLETE_VALUE_WINS),
        ])
        resolved, verdict = GQC.resolve_reconciliation_tolerance(
            self.claim(threshold=0.5, comparison="lte"), attempts, now=NOW)
        self.assertEqual(verdict, "PASS")  # 0.5 <= 0.5 tolerance

    def test_wrong_quality_dimension_is_refused(self):
        with self.assertRaises(GQC.RegistrationError):
            GQC.resolve_reconciliation_tolerance(quality(), [])


class ResolveNoEvidenceAvailableTests(unittest.TestCase):
    def test_precision_floor_names_matcher_boundary_as_the_reason(self):
        resolved, verdict = GQC.resolve_no_evidence_available(
            quality(quality_dimension="precision_floor"))
        self.assertEqual(verdict, "NO-DATA")
        self.assertIsNone(resolved["measured_value"])
        self.assertIn("matcher_boundary.py", resolved["evidence_note"])

    def test_recall_floor_names_matcher_boundary_as_the_reason(self):
        resolved, verdict = GQC.resolve_no_evidence_available(
            quality(quality_dimension="recall_floor"))
        self.assertEqual(verdict, "NO-DATA")
        self.assertIn("matcher_boundary.py", resolved["evidence_note"])

    def test_segment_floor_has_an_honest_reason(self):
        resolved, verdict = GQC.resolve_no_evidence_available(
            quality(quality_dimension="segment_floor"))
        self.assertEqual(verdict, "NO-DATA")
        self.assertTrue(resolved["evidence_note"])

    def test_residual_normalization_error_names_normalization_trace(self):
        resolved, verdict = GQC.resolve_no_evidence_available(
            quality(quality_dimension="residual_normalization_error"))
        self.assertEqual(verdict, "NO-DATA")
        self.assertIn("normalization_trace.py", resolved["evidence_note"])

    def test_never_fabricates_a_measured_value(self):
        for dim in ("precision_floor", "recall_floor", "segment_floor",
                    "residual_normalization_error"):
            resolved, verdict = GQC.resolve_no_evidence_available(
                quality(quality_dimension=dim))
            self.assertIsNone(resolved["measured_value"])
            self.assertIsNone(resolved["resolved_at"])
            self.assertEqual(verdict, "NO-DATA")


class MainCliTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()

    def write(self, name, data):
        path = os.path.join(self.tmp, name)
        with open(path, "w") as fh:
            json.dump(data, fh)
        return path

    def test_check_command_exits_0_on_pass(self):
        path = self.write("q.json", quality())
        self.assertEqual(GQC.main(["check", path]), 0)

    def test_check_command_exits_2_on_missing_file(self):
        self.assertEqual(GQC.main(["check", "/no/such/record.json"]), 2)

    def test_check_command_exits_1_on_structural_fail(self):
        r = quality()
        del r["threshold"]
        path = self.write("q.json", r)
        self.assertEqual(GQC.main(["check", path]), 1)

    def test_check_command_with_matching_lifecycle_and_master_exits_0(self):
        q_path = self.write("q.json", quality())
        lc_path = self.write("lc.json", lifecycle())
        m_path = self.write("m.json", master(quality_claim_ids=["m1:precision_floor"]))
        self.assertEqual(GQC.main([
            "check", q_path, "--lifecycle-record", lc_path, "--master-record", m_path]), 0)

    def test_check_command_with_mismatched_master_record_exits_1(self):
        q_path = self.write("q.json", quality())
        m_path = self.write("m.json", master(quality_claim_ids=["something_else"]))
        self.assertEqual(GQC.main(["check", q_path, "--master-record", m_path]), 1)

    def test_register_command_writes_frozen_claims(self):
        m_path = self.write("m.json", master())
        t_path = self.write("t.json", {
            "precision_floor": {"threshold": 0.95, "comparison": "gte"}})
        out_path = os.path.join(self.tmp, "out.json")
        self.assertEqual(GQC.main(["register", m_path, t_path, "--out", out_path]), 0)
        with open(out_path) as fh:
            written = json.load(fh)
        self.assertEqual(len(written), 1)
        self.assertEqual(written[0]["lifecycle"]["state"], "FROZEN")
        self.assertEqual(written[0]["quality"]["quality_dimension"], "precision_floor")

    def test_register_command_on_bad_thresholds_exits_1(self):
        m_path = self.write("m.json", master())
        t_path = self.write("t.json", {"made_up": {"threshold": 1, "comparison": "gte"}})
        self.assertEqual(GQC.main(["register", m_path, t_path]), 1)

    def test_register_command_on_missing_master_exits_2(self):
        t_path = self.write("t.json", {})
        self.assertEqual(GQC.main(["register", "/no/such/master.json", t_path]), 2)

    def test_gate_command_passes_when_every_claim_is_frozen(self):
        m_path = self.write("m.json", master(quality_claim_ids=["m1:precision_floor"]))
        c_path = self.write("c.json", [lifecycle(state="FROZEN")])
        self.assertEqual(GQC.main(["gate", m_path, c_path]), 0)

    def test_gate_command_fails_when_a_claim_is_still_draft(self):
        m_path = self.write("m.json", master(quality_claim_ids=["m1:precision_floor"]))
        c_path = self.write("c.json", [lifecycle(state="DRAFT")])
        self.assertEqual(GQC.main(["gate", m_path, c_path]), 1)

    def test_gate_command_on_non_list_claims_exits_2(self):
        m_path = self.write("m.json", master())
        c_path = self.write("c.json", {"not": "a list"})
        self.assertEqual(GQC.main(["gate", m_path, c_path]), 2)

    def test_end_to_end_register_freeze_then_gate_passes(self):
        m = master()
        m_path = self.write("m.json", m)
        t_path = self.write("t.json", {
            "catastrophic_false_merge_tolerance": {"threshold": 0.0, "comparison": "lte"},
        })
        out_path = os.path.join(self.tmp, "registered.json")
        self.assertEqual(GQC.main(["register", m_path, t_path, "--out", out_path]), 0)
        with open(out_path) as fh:
            registered = json.load(fh)
        claim_id = registered[0]["lifecycle"]["claim_id"]
        m["quality_claim_ids"] = [claim_id]
        m_path2 = self.write("m2.json", m)
        c_path = self.write("c.json", [r["lifecycle"] for r in registered])
        self.assertEqual(GQC.main(["gate", m_path2, c_path]), 0)


if __name__ == "__main__":
    unittest.main()

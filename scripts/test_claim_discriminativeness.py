#!/usr/bin/env python3
"""Tests for claim_discriminativeness.py (DOM-10.02).

Every case asserts the verdict returned by judge_claim/judge_claims.
stdlib unittest only, no network, no filesystem needed beyond argv.
"""
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import claim_discriminativeness as CD


def _claim(**kwargs):
    base = {
        "check": "check_x",
        "risk": "HIGH",
        "property": "the merge is blocked when signatures mismatch",
        "result": "PASS",
    }
    base.update(kwargs)
    return base


def _mutation_evidence(**kwargs):
    base = {
        "kind": "mutation",
        "note": "flipped the comparison operator, watched the check go red",
        "property": "the merge is blocked when signatures mismatch",
    }
    base.update(kwargs)
    return base


class TheDecidingRow(unittest.TestCase):
    """THE ROW: a HIGH RISK claim whose only evidence is the exit code is
    NO-DATA, never PROVEN."""

    def test_high_risk_exit_code_only_is_no_data(self):
        claim = _claim(evidence=[])
        verdict, reason = CD.judge_claim(claim)
        self.assertEqual(verdict, CD.NO_DATA)

    def test_high_risk_no_evidence_key_at_all_is_no_data(self):
        claim = _claim()
        claim.pop("evidence", None)
        verdict, _ = CD.judge_claim(claim)
        self.assertEqual(verdict, CD.NO_DATA)

    def test_high_risk_with_discriminating_evidence_is_proven(self):
        claim = _claim(evidence=[_mutation_evidence()])
        verdict, _ = CD.judge_claim(claim)
        self.assertEqual(verdict, CD.PROVEN)

    def test_estate_real_shape_29_green_tests_deleted_comparison(self):
        """The cited incident: 29 tests stayed green while the deciding
        comparison could be deleted from four functions. "29 tests green"
        is not a discriminating kind, so this must not be PROVEN."""
        claim = _claim(property="the four merge functions all compare the key field", evidence=[{
            "kind": "test_suite_green",
            "note": "29 of 29 unit tests pass",
            "property": "the four merge functions all compare the key field",
        }])
        verdict, _ = CD.judge_claim(claim)
        self.assertNotEqual(verdict, CD.PROVEN)
        self.assertEqual(verdict, CD.NO_DATA)


class RawResultMapping(unittest.TestCase):
    def test_fail_result_is_refuted_regardless_of_risk_or_evidence(self):
        for risk in CD.RISK_CLASSES:
            claim = _claim(risk=risk, result="FAIL", evidence=[_mutation_evidence()])
            verdict, _ = CD.judge_claim(claim)
            self.assertEqual(verdict, CD.REFUTED)

    def test_no_data_result_is_no_data_regardless_of_risk_or_evidence(self):
        for risk in CD.RISK_CLASSES:
            claim = _claim(risk=risk, result="NO-DATA", evidence=[_mutation_evidence()])
            verdict, _ = CD.judge_claim(claim)
            self.assertEqual(verdict, CD.NO_DATA)


class LowRiskExitCodeEdge(unittest.TestCase):
    """Edge: a low risk claim with only an exit code. Decided: accepted,
    because a low risk claim is cheap to be wrong about by definition."""

    def test_low_risk_exit_code_only_is_proven(self):
        claim = _claim(risk="LOW", evidence=[])
        verdict, _ = CD.judge_claim(claim)
        self.assertEqual(verdict, CD.PROVEN)

    def test_low_risk_with_no_evidence_key_is_proven(self):
        claim = _claim(risk="LOW")
        claim.pop("evidence", None)
        verdict, _ = CD.judge_claim(claim)
        self.assertEqual(verdict, CD.PROVEN)


class EmptyEvidenceEdge(unittest.TestCase):
    """Edge: evidence that is present but empty."""

    def test_empty_evidence_list_is_no_data(self):
        claim = _claim(evidence=[])
        verdict, _ = CD.judge_claim(claim)
        self.assertEqual(verdict, CD.NO_DATA)

    def test_evidence_item_with_blank_note_is_invalid_so_claim_is_no_data(self):
        claim = _claim(evidence=[_mutation_evidence(note="   ")])
        verdict, _ = CD.judge_claim(claim)
        self.assertEqual(verdict, CD.NO_DATA)

    def test_evidence_item_missing_property_is_invalid(self):
        item = _mutation_evidence()
        del item["property"]
        claim = _claim(evidence=[item])
        verdict, _ = CD.judge_claim(claim)
        self.assertEqual(verdict, CD.NO_DATA)


class BorrowedEvidenceEdge(unittest.TestCase):
    """Edge: two claims where one borrows the other's evidence."""

    def test_shared_evidence_id_across_claims_is_borrowed_for_both(self):
        shared = _mutation_evidence(id="ev-1")
        claim_a = _claim(check="check_a", id="a", evidence=[shared])
        claim_b = _claim(check="check_b", id="b", evidence=[dict(shared)])
        results = CD.judge_claims([claim_a, claim_b])
        self.assertEqual(results["a"][0], CD.NO_DATA)
        self.assertEqual(results["b"][0], CD.NO_DATA)

    def test_explicit_claim_id_mismatch_is_borrowed(self):
        item = _mutation_evidence(claim_id="some-other-claim")
        claim = _claim(id="this-claim", evidence=[item])
        verdict, _ = CD.judge_claim(claim)
        self.assertEqual(verdict, CD.NO_DATA)

    def test_unshared_evidence_id_is_not_borrowed(self):
        claim_a = _claim(check="check_a", id="a", evidence=[_mutation_evidence(id="ev-a")])
        claim_b = _claim(check="check_b", id="b", evidence=[_mutation_evidence(id="ev-b")])
        results = CD.judge_claims([claim_a, claim_b])
        self.assertEqual(results["a"][0], CD.PROVEN)
        self.assertEqual(results["b"][0], CD.PROVEN)


class WrongPropertyEdge(unittest.TestCase):
    """Edge: evidence naming a property the claim does not."""

    def test_evidence_naming_a_different_property_does_not_count(self):
        item = _mutation_evidence(property="an unrelated property entirely")
        claim = _claim(evidence=[item])
        verdict, _ = CD.judge_claim(claim)
        self.assertEqual(verdict, CD.NO_DATA)


class SyntaxErrorRedEdge(unittest.TestCase):
    """Edge: a red-before whose red came from a syntax error does not
    count, same reason as the mutation gate row."""

    def test_red_before_from_syntax_error_does_not_count(self):
        item = {
            "kind": "red_before",
            "note": "ran the check before the fix",
            "property": "the merge is blocked when signatures mismatch",
            "red_cause": "syntax_error",
        }
        claim = _claim(evidence=[item])
        verdict, _ = CD.judge_claim(claim)
        self.assertEqual(verdict, CD.NO_DATA)

    def test_red_before_from_a_real_failure_counts(self):
        item = {
            "kind": "red_before",
            "note": "ran the check before the fix, it failed on the real property",
            "property": "the merge is blocked when signatures mismatch",
        }
        claim = _claim(evidence=[item])
        verdict, _ = CD.judge_claim(claim)
        self.assertEqual(verdict, CD.PROVEN)


class StaleEvidenceEdge(unittest.TestCase):
    def test_evidence_as_of_a_different_revision_is_stale(self):
        claim = _claim(revision="rev-2", evidence=[_mutation_evidence(as_of="rev-1")])
        verdict, _ = CD.judge_claim(claim)
        self.assertEqual(verdict, CD.STALE)

    def test_evidence_as_of_the_same_revision_is_proven(self):
        claim = _claim(revision="rev-2", evidence=[_mutation_evidence(as_of="rev-2")])
        verdict, _ = CD.judge_claim(claim)
        self.assertEqual(verdict, CD.PROVEN)

    def test_no_revision_stated_never_reads_as_stale(self):
        claim = _claim(evidence=[_mutation_evidence(as_of="rev-1")])
        verdict, _ = CD.judge_claim(claim)
        self.assertEqual(verdict, CD.PROVEN)


class UnrecognizedInputNeverSafe(unittest.TestCase):
    def test_unknown_risk_class_raises(self):
        claim = _claim(risk="MEDIUM")
        with self.assertRaises(ValueError):
            CD.judge_claim(claim)

    def test_unknown_raw_result_raises(self):
        claim = _claim(result="MAYBE")
        with self.assertRaises(ValueError):
            CD.judge_claim(claim)

    def test_missing_required_field_raises(self):
        claim = _claim()
        del claim["property"]
        with self.assertRaises(ValueError):
            CD.judge_claim(claim)


class VerdictVocabularyIsClosed(unittest.TestCase):
    def test_only_four_verdicts_exist(self):
        self.assertEqual(CD.VERDICTS, ("PROVEN", "REFUTED", "NO-DATA", "STALE"))

    def test_no_data_is_the_shared_literal(self):
        import evidence_obligation
        self.assertIn(CD.NO_DATA, evidence_obligation.VERDICTS)


if __name__ == "__main__":
    unittest.main(verbosity=2)

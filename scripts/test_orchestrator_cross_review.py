#!/usr/bin/env python3
"""Calibration for scripts/orchestrator_cross_review.py (ORCH-11).

TestFreshnessInvalidatesOnNewCommit is written first and kept first in
this file, on purpose: the worker brief for this unit names it "the one to
write first," since a freshness check that always answers False would
pass every OTHER staleness test in this file while making the gate useless
(see orchestrator_cross_review's own "THE BAD STATE A GREEN RUN WOULD ALSO
PASS" section). TestFreshReviewGatesThrough is the other half of that same
guard: it proves a review at the CURRENT revision passes through, so the
always-False tautology cannot hide behind this suite.

No subprocess, no filesystem, no network: every fixture is an in-memory
dict or object, and the whole suite is expected to run in well under a
second.
"""
import os
import sys
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import orchestrator_cross_review as R  # noqa: E402
import orchestrator_invariants as I  # noqa: E402


def _task(risk_class="high", task_id="T-1"):
    return {"id": task_id, "risk_class": risk_class}


class TestFreshnessInvalidatesOnNewCommit(unittest.TestCase):
    """Rule 1, written first. A review at revision A must not validate,
    and must not gate through, once the work has moved to revision B."""

    def test_is_valid_false_after_new_commit(self):
        req = R.request(
            _task(), author_family="opus",
            available_families=["opus", "sonnet"], revision="AAA111")
        review = R.record(req, "PASS", revision="AAA111")
        self.assertTrue(R.is_valid(review, current_revision="AAA111"))
        self.assertFalse(R.is_valid(review, current_revision="BBB222"))

    def test_gate_does_not_accept_a_stale_pass(self):
        req = R.request(
            _task(), author_family="opus",
            available_families=["opus", "sonnet"], revision="AAA111")
        review = R.record(req, "PASS", revision="AAA111")
        decision = R.gate(
            _task(), review, current_revision="BBB222",
            obligation="REQUIRED_FOR_MERGE")
        self.assertEqual(decision.verdict, "NO-DATA")
        self.assertFalse(decision.fresh)
        self.assertFalse(decision.may_proceed)
        self.assertTrue(decision.uncertain)


class TestFreshReviewGatesThrough(unittest.TestCase):
    """The guard against the opposite tautology: a freshness check hard
    coded to always return False would pass every test above while
    blocking this one. A review at the CURRENT revision must validate and
    must gate through."""

    def test_fresh_pass_proceeds(self):
        req = R.request(
            _task(), author_family="opus",
            available_families=["opus", "sonnet"], revision="AAA111")
        review = R.record(req, "PASS", revision="AAA111")
        self.assertTrue(R.is_valid(review, current_revision="AAA111"))
        decision = R.gate(
            _task(), review, current_revision="AAA111",
            obligation="REQUIRED_FOR_MERGE")
        self.assertEqual(decision.verdict, "PASS")
        self.assertTrue(decision.fresh)
        self.assertTrue(decision.may_proceed)
        self.assertFalse(decision.uncertain)

    def test_fresh_fail_blocks(self):
        req = R.request(
            _task(), author_family="opus",
            available_families=["opus", "sonnet"], revision="AAA111")
        review = R.record(req, "FAIL", revision="AAA111")
        decision = R.gate(
            _task(), review, current_revision="AAA111",
            obligation="REQUIRED_FOR_MERGE")
        self.assertEqual(decision.verdict, "FAIL")
        self.assertTrue(decision.fresh)
        self.assertFalse(decision.may_proceed)
        self.assertFalse(decision.uncertain)


class TestReviewerNeverAuthorsFamily(unittest.TestCase):
    """Rule 2, across every combination this suite has."""

    def test_picks_a_different_family_deterministically(self):
        req = R.request(
            _task(), author_family="opus",
            available_families=["opus", "sonnet"], revision="AAA111")
        self.assertEqual(req.reviewer_family, "sonnet")

    def test_picks_alphabetically_first_of_several_others(self):
        req = R.request(
            _task(), author_family="sonnet",
            available_families=["outside", "opus", "sonnet"],
            revision="AAA111")
        self.assertEqual(req.reviewer_family, "opus")

    def test_every_combination_of_three_families(self):
        families = ("opus", "sonnet", "outside")
        for author in families:
            req = R.request(
                _task(), author_family=author, available_families=families,
                revision="AAA111")
            self.assertNotEqual(req.reviewer_family, author)

    def test_never_falls_back_to_the_author_when_only_family_available(self):
        with self.assertRaises(R.CrossReviewRefused):
            R.request(
                _task(), author_family="opus",
                available_families=["opus"], revision="AAA111")

    def test_never_falls_back_when_author_repeated_in_pool(self):
        with self.assertRaises(R.CrossReviewRefused):
            R.request(
                _task(), author_family="opus",
                available_families=["opus", "opus", "opus"],
                revision="AAA111")


class TestRequiredPlusUnavailableIsNoDataAndBlocks(unittest.TestCase):
    """Rule 3. request() could not be satisfied, so the caller has no
    Review at all: gate() must see this as NO-DATA against a REQUIRED
    obligation and refuse to proceed, via may_proceed(), never a private
    verdict-versus-obligation table."""

    def test_no_review_required_for_merge_blocks(self):
        decision = R.gate(
            _task(), None, current_revision="AAA111",
            obligation="REQUIRED_FOR_MERGE")
        self.assertEqual(decision.verdict, "NO-DATA")
        self.assertFalse(decision.may_proceed)
        self.assertTrue(decision.uncertain)
        self.assertIsNone(decision.reviewer_family)

    def test_matches_may_proceed_directly(self):
        # This gate must never diverge from the enforced table it is
        # built on top of.
        expected = I.may_proceed("NO-DATA", "REQUIRED_FOR_MERGE", "merge")
        decision = R.gate(
            _task(), None, current_revision="AAA111",
            obligation="REQUIRED_FOR_MERGE")
        self.assertEqual(decision.may_proceed, expected)
        self.assertFalse(expected)


class TestOptionalPlusUnavailableProceedsWithUncertainty(unittest.TestCase):
    """Rule 4. Low or medium risk work with no review on hand still
    proceeds, but the Decision must carry the uncertainty as a field."""

    def test_no_review_optional_proceeds_but_flags_uncertain(self):
        decision = R.gate(
            _task(risk_class="low"), None, current_revision="AAA111",
            obligation="OPTIONAL")
        self.assertEqual(decision.verdict, "NO-DATA")
        self.assertTrue(decision.may_proceed)
        self.assertTrue(decision.uncertain)

    def test_required_for_release_at_merge_stage_proceeds(self):
        # may_proceed's own table: NO-DATA against REQUIRED_FOR_RELEASE
        # does not bind at "merge", only at "release". gate() is fixed at
        # stage "merge", so this must proceed even though the obligation
        # is not OPTIONAL.
        decision = R.gate(
            _task(), None, current_revision="AAA111",
            obligation="REQUIRED_FOR_RELEASE")
        self.assertTrue(decision.may_proceed)
        self.assertTrue(decision.uncertain)


class TestReviewIsReadOnly(unittest.TestCase):
    """Rule 5. Nothing this module exposes edits, merges, or marks a unit
    done."""

    FORBIDDEN_SUBSTRINGS = (
        "merge", "commit", "push", "apply", "mark_done", "close", "write",
        "delete", "rebase",
    )

    def test_no_public_name_suggests_a_mutating_capability(self):
        for name in dir(R):
            if name.startswith("_"):
                continue
            lowered = name.lower()
            for bad in self.FORBIDDEN_SUBSTRINGS:
                self.assertNotIn(
                    bad, lowered,
                    "public name %r suggests a mutating capability (%r); "
                    "this module must stay read only" % (name, bad))


class TestVerdictForUnaskedRevisionIsRefused(unittest.TestCase):
    """Rule 6."""

    def test_mismatched_revision_raises(self):
        req = R.request(
            _task(), author_family="opus",
            available_families=["opus", "sonnet"], revision="AAA111")
        with self.assertRaises(R.CrossReviewRefused):
            R.record(req, "PASS", revision="ZZZ999")

    def test_matching_revision_succeeds(self):
        req = R.request(
            _task(), author_family="opus",
            available_families=["opus", "sonnet"], revision="AAA111")
        review = R.record(req, "PASS", revision="AAA111")
        self.assertEqual(review.revision, "AAA111")


class TestShortShaVersusFullSha(unittest.TestCase):
    """This module's stated decision: exact string equality only, never a
    prefix match. A short SHA and the full SHA of the same commit compare
    unequal."""

    def test_prefix_of_current_revision_does_not_validate(self):
        req = R.request(
            _task(), author_family="opus",
            available_families=["opus", "sonnet"],
            revision="abc123def456")
        review = R.record(req, "PASS", revision="abc123def456")
        self.assertFalse(R.is_valid(review, current_revision="abc123"))

    def test_case_difference_does_not_validate(self):
        req = R.request(
            _task(), author_family="opus",
            available_families=["opus", "sonnet"], revision="ABC123")
        review = R.record(req, "PASS", revision="ABC123")
        self.assertFalse(R.is_valid(review, current_revision="abc123"))


class TestEdgeCases(unittest.TestCase):
    def test_empty_available_families_refused(self):
        with self.assertRaises(R.CrossReviewRefused):
            R.request(
                _task(), author_family="opus", available_families=[],
                revision="AAA111")

    def test_bare_string_available_families_refused(self):
        # A bare string iterates as one "family" per character; this is a
        # classic Python trap and must never silently produce a nonsense
        # single-character family.
        with self.assertRaises(R.CrossReviewRefused):
            R.request(
                _task(), author_family="opus", available_families="sonnet",
                revision="AAA111")

    def test_empty_revision_refused_at_request(self):
        with self.assertRaises(R.CrossReviewRefused):
            R.request(
                _task(), author_family="opus",
                available_families=["opus", "sonnet"], revision="")

    def test_empty_revision_refused_at_record(self):
        req = R.request(
            _task(), author_family="opus",
            available_families=["opus", "sonnet"], revision="AAA111")
        with self.assertRaises(R.CrossReviewRefused):
            R.record(req, "PASS", revision="")

    def test_empty_current_revision_is_ambiguous_and_stale(self):
        req = R.request(
            _task(), author_family="opus",
            available_families=["opus", "sonnet"], revision="AAA111")
        review = R.record(req, "PASS", revision="AAA111")
        self.assertFalse(R.is_valid(review, current_revision=""))
        self.assertFalse(R.is_valid(review, current_revision=None))

    def test_two_reviews_for_the_same_request_is_out_of_scope(self):
        # This module holds no ledger of requests already answered (see
        # its own docstring edge list): calling record() twice against
        # the same request is not refused here, deliberately. A caller
        # wanting "only once" wires this through scripts/claim_store.py.
        req = R.request(
            _task(), author_family="opus",
            available_families=["opus", "sonnet"], revision="AAA111")
        first = R.record(req, "PASS", revision="AAA111")
        second = R.record(req, "FAIL", revision="AAA111")
        self.assertEqual(first.verdict, "PASS")
        self.assertEqual(second.verdict, "FAIL")

    def test_unknown_verdict_refused(self):
        req = R.request(
            _task(), author_family="opus",
            available_families=["opus", "sonnet"], revision="AAA111")
        with self.assertRaises(R.CrossReviewRefused):
            R.record(req, "MAYBE", revision="AAA111")

    def test_unknown_obligation_refused(self):
        review = None
        with self.assertRaises(R.CrossReviewRefused):
            R.gate(
                _task(), review, current_revision="AAA111",
                obligation="REQUIRED_FOR_LUNCH")

    def test_task_with_no_risk_class_refused_by_required_for(self):
        with self.assertRaises(R.CrossReviewRefused):
            R.required_for({"id": "T-2"})

    def test_task_with_unknown_risk_class_refused(self):
        with self.assertRaises(R.CrossReviewRefused):
            R.required_for({"id": "T-2", "risk_class": "vibes"})

    def test_required_for_matches_coe_nominate_thresholds(self):
        import coe_nominate
        for risk_class in coe_nominate.KNOWN_RISK_CLASSES:
            expected = risk_class in coe_nominate.RISK_CLASSES_REQUIRING_FAMILY_DIVERSITY
            self.assertEqual(
                R.required_for({"id": "T-3", "risk_class": risk_class}),
                expected)

    def test_findings_and_evidence_are_defensively_copied(self):
        req = R.request(
            _task(), author_family="opus",
            available_families=["opus", "sonnet"], revision="AAA111")
        findings = ["issue one"]
        evidence = {"log": "path/to/log"}
        review = R.record(
            req, "FAIL", revision="AAA111", findings=findings,
            evidence=evidence)
        findings.append("issue two")
        evidence["log"] = "mutated"
        self.assertEqual(review.findings, ("issue one",))
        self.assertEqual(review.evidence, {"log": "path/to/log"})

    def test_gate_handles_task_with_no_id(self):
        decision = R.gate(
            {"risk_class": "high"}, None, current_revision="AAA111",
            obligation="REQUIRED_FOR_MERGE")
        self.assertIsNone(decision.task_id)

    def test_reviewer_own_no_data_verdict_is_uncertain_and_blocks(self):
        req = R.request(
            _task(), author_family="opus",
            available_families=["opus", "sonnet"], revision="AAA111")
        review = R.record(req, "NO-DATA", revision="AAA111")
        decision = R.gate(
            _task(), review, current_revision="AAA111",
            obligation="REQUIRED_FOR_MERGE")
        self.assertEqual(decision.verdict, "NO-DATA")
        self.assertTrue(decision.uncertain)
        self.assertFalse(decision.may_proceed)
        self.assertTrue(decision.fresh)


if __name__ == "__main__":
    unittest.main()

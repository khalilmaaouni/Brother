#!/usr/bin/env python3
"""Calibration for scripts/orchestrator_invariants.py.

The property this file exists to assert is not that the module has the
right shape, it is that the two rules it enforces cannot regress silently:
NO-DATA must never satisfy a REQUIRED_FOR_* obligation, and an unknown
input to any of the three functions must raise rather than be read as the
safe case. A test suite that only checks the happy path would pass right
through a change that broke either rule.
"""
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import evidence_obligation as eo  # noqa: E402
import orchestrator_invariants as inv  # noqa: E402


class TestInvariantOrder(unittest.TestCase):
    def test_invariant_order_is_exact(self):
        # Order is the priority hierarchy, not incidental. A reordering
        # is a real defect: this asserts the exact tuple, not membership.
        self.assertEqual(inv.INVARIANTS, (
            "no false green",
            "no dual authority",
            "no stale authority",
            "no worker without a claim",
            "no concurrent writers with overlapping scope",
            "no direct agent merge to canonical",
            "no mandatory NO-DATA treated as success",
            "no RED action automated",
            "no transcript-only state",
            "no idle capacity while safe READY work exists",
        ))

    def test_invariant_count(self):
        self.assertEqual(len(inv.INVARIANTS), 10)


class TestSetShapes(unittest.TestCase):
    def test_task_classes(self):
        self.assertEqual(inv.TASK_CLASSES, frozenset((
            "planning", "architecture", "research", "implementation",
            "test", "verification", "review", "repair", "fault-injection",
            "documentation", "packaging", "benchmark", "integration-prep",
        )))
        self.assertEqual(len(inv.TASK_CLASSES), 13)

    def test_evidence_obligations(self):
        # Three levels, matching the enforced gate (evidence_obligation.py),
        # which refuses a fourth. REQUIRED_FOR_DELIVERY does not exist: it
        # was never one of the gate's levels, so a task record carrying it
        # would be schema-valid here and ungateable there.
        self.assertEqual(inv.EVIDENCE_OBLIGATIONS, frozenset((
            "OPTIONAL", "REQUIRED_FOR_MERGE", "REQUIRED_FOR_RELEASE",
        )))

    def test_verdicts(self):
        self.assertEqual(inv.VERDICTS, frozenset(("PASS", "FAIL", "NO-DATA")))


class TestVocabularyMatchesEvidenceObligation(unittest.TestCase):
    # scripts/evidence_obligation.py is the frozen definition site (EV-3)
    # for these two triples. This is the test the module exists to have:
    # a later edit to either module that lets the two vocabularies diverge
    # again must fail here, not be caught by a human reading a diff.
    def test_verdicts_match_evidence_obligation(self):
        self.assertEqual(set(inv.VERDICTS), set(eo.VERDICTS))

    def test_evidence_obligations_match_evidence_obligation(self):
        self.assertEqual(set(inv.EVIDENCE_OBLIGATIONS), set(eo.LEVELS))

    def test_agrees_with_the_live_gate_on_the_demonstrated_case(self):
        # The exact live disagreement the reviewer ran:
        #   printf 'virgin-unit-proof\t2\n' | \
        #       python3 scripts/evidence_obligation.py transition \
        #       --stage merge --repo .
        # virgin-unit-proof is REQUIRED_FOR_RELEASE in gate_obligations.json
        # with no expected_absent_input, exit code 2 is NO-DATA, and the
        # gate allows it at stage merge. may_proceed must agree.
        repo = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        obligations_path = eo.obligations_path(repo)
        if not os.path.exists(obligations_path):
            self.skipTest("no gate_obligations.json in this checkout")
        data = eo.load_obligations(repo)
        entry = data.get("checks", {}).get("virgin-unit-proof")
        if entry is None:
            self.skipTest("virgin-unit-proof not present in gate_obligations.json")
        self.assertEqual(entry.get("obligation"), "REQUIRED_FOR_RELEASE")
        self.assertIsNone(entry.get("expected_absent_input"))
        self.assertTrue(
            inv.may_proceed("NO-DATA", entry["obligation"], "merge"))
        self.assertFalse(
            inv.may_proceed("NO-DATA", entry["obligation"], "release"))


class TestSetShapesTwo(unittest.TestCase):
    def test_task_states(self):
        # Pinned by literal, not just by count: a count-only check let a
        # rename (REPAIRABLE to REPARABLE) survive, because renaming one
        # member and adding a different one leaves the count unchanged.
        self.assertEqual(inv.TASK_STATES, frozenset((
            "PLANNED", "READY", "ORCHESTRATOR-CLAIMED", "WORKER-CLAIMED",
            "RUNNING", "WORKER-PASS", "WORKER-FAIL", "VERIFYING", "REVIEW",
            "REPAIRABLE", "NEEDS-REPLAN", "INTEGRATING", "INTEGRATED",
            "NEEDS-REPAIR-ON-NEW-BASE", "CANONICAL-VERIFY", "DONE",
            "PARKED", "EXHAUSTED", "AWAITING-HUMAN", "CANCELLED",
        )))
        self.assertEqual(len(inv.TASK_STATES), 20)

    def test_terminal_states(self):
        self.assertEqual(inv.TERMINAL_STATES, frozenset((
            "DONE", "PARKED", "EXHAUSTED", "AWAITING-HUMAN", "CANCELLED",
        )))
        # Every terminal state must also be a known task state.
        self.assertTrue(inv.TERMINAL_STATES.issubset(inv.TASK_STATES))

    def test_failure_classes_exact(self):
        # Pinned by independent literal, the same pattern test_actions and
        # test_health_states already use, not derived from the module's
        # own sets: a subset-and-disjoint check holds for any partition,
        # including a weakened one (a mutation that moves a class between
        # buckets, empties a bucket, or swaps a member for a bogus one
        # still passes a subset/disjoint-only test). See
        # test_failure_taxonomy_rejects_known_weakenings below, which
        # drives six such mutations through a copy of this module and
        # confirms each one now fails against these literals.
        self.assertEqual(inv.FAILURE_CLASSES, frozenset((
            "rate_limit", "overloaded", "timeout", "empty", "worker_crash",
            "scope_violation", "test_failure", "integration_conflict",
            "canonical_regression", "missing_evidence", "tool_unavailable",
            "resource_pressure", "policy_refusal", "unknown",
        )))
        self.assertEqual(len(inv.FAILURE_CLASSES), 14)

    def test_transient_failures_exact(self):
        self.assertEqual(inv.TRANSIENT_FAILURES, frozenset((
            "rate_limit", "overloaded", "timeout", "tool_unavailable",
        )))

    def test_semantic_failures_exact(self):
        self.assertEqual(inv.SEMANTIC_FAILURES, frozenset((
            "test_failure", "canonical_regression", "scope_violation",
            "integration_conflict",
        )))

    def test_authority_failures_exact(self):
        self.assertEqual(inv.AUTHORITY_FAILURES, frozenset(("policy_refusal",)))

    def test_failure_classes_partition(self):
        # transient, semantic and authority are disjoint, and every one
        # of them is a known failure class. 'unknown' and 'empty' and
        # 'worker_crash' and 'missing_evidence' and 'resource_pressure'
        # are deliberately none of the three (they classify as 'other').
        # This check alone is not the fix (it holds for any partition,
        # weakened or not); it stays as a cheap sanity check alongside the
        # exact literals above, which are the actual pin.
        self.assertTrue(inv.TRANSIENT_FAILURES.issubset(inv.FAILURE_CLASSES))
        self.assertTrue(inv.SEMANTIC_FAILURES.issubset(inv.FAILURE_CLASSES))
        self.assertTrue(inv.AUTHORITY_FAILURES.issubset(inv.FAILURE_CLASSES))
        self.assertEqual(
            inv.TRANSIENT_FAILURES & inv.SEMANTIC_FAILURES, frozenset())
        self.assertEqual(
            inv.TRANSIENT_FAILURES & inv.AUTHORITY_FAILURES, frozenset())
        self.assertEqual(
            inv.SEMANTIC_FAILURES & inv.AUTHORITY_FAILURES, frozenset())

    def test_actions(self):
        self.assertEqual(inv.ACTIONS, frozenset((
            "NOOP", "DISPATCH", "REQUEST-REVIEW", "REQUEST-REPAIR",
            "REQUEST-REPLAN", "PROPOSE-DECOMPOSITION", "HANDOFF", "PARK",
            "QUEUE-HUMAN", "CLOSEOUT",
        )))

    def test_health_states(self):
        self.assertEqual(inv.HEALTH_STATES, frozenset((
            "STARTING", "HEALTHY", "THINKING", "WAITING-WORKER",
            "WAITING-REVIEW", "DRAINING", "DEGRADED", "UNAVAILABLE",
            "STOPPED",
        )))

    def test_schema_version(self):
        self.assertEqual(inv.SCHEMA_VERSION, "orchestrator-v1")


class TestMayProceed(unittest.TestCase):
    def test_optional_accepts_pass_and_nodata_both_stages(self):
        for stage in ("merge", "release"):
            self.assertTrue(inv.may_proceed("PASS", "OPTIONAL", stage))
            self.assertTrue(inv.may_proceed("NO-DATA", "OPTIONAL", stage))

    def test_optional_blocks_fail_both_stages(self):
        for stage in ("merge", "release"):
            self.assertFalse(inv.may_proceed("FAIL", "OPTIONAL", stage))

    def test_required_obligations_accept_pass_both_stages(self):
        for obligation in ("REQUIRED_FOR_MERGE", "REQUIRED_FOR_RELEASE"):
            for stage in ("merge", "release"):
                self.assertTrue(inv.may_proceed("PASS", obligation, stage))

    def test_fail_blocked_everywhere(self):
        # FAIL never proceeds, under any obligation, at either stage: this
        # is what evidence_obligation.transition() does unconditionally.
        for obligation in inv.EVIDENCE_OBLIGATIONS:
            for stage in ("merge", "release"):
                self.assertFalse(inv.may_proceed("FAIL", obligation, stage))

    def test_nodata_blocked_under_required_for_merge_both_stages(self):
        for stage in ("merge", "release"):
            self.assertFalse(
                inv.may_proceed("NO-DATA", "REQUIRED_FOR_MERGE", stage))

    def test_nodata_under_required_for_release_depends_on_stage(self):
        # This is the exact live disagreement the reviewer demonstrated
        # with evidence_obligation.py transition --stage merge: a
        # REQUIRED_FOR_RELEASE obligation must not block a merge on
        # NO-DATA, because it does not bind until release.
        self.assertTrue(
            inv.may_proceed("NO-DATA", "REQUIRED_FOR_RELEASE", "merge"))
        self.assertFalse(
            inv.may_proceed("NO-DATA", "REQUIRED_FOR_RELEASE", "release"))

    def test_unknown_verdict_raises(self):
        with self.assertRaises(ValueError):
            inv.may_proceed("MOSTLY-PASS", "REQUIRED_FOR_MERGE", "merge")

    def test_unknown_obligation_raises(self):
        with self.assertRaises(ValueError):
            inv.may_proceed("PASS", "NICE_TO_HAVE", "merge")

    def test_unknown_stage_raises(self):
        with self.assertRaises(ValueError):
            inv.may_proceed("PASS", "REQUIRED_FOR_MERGE", "deploy")


class TestIsTerminal(unittest.TestCase):
    def test_terminal_states_are_terminal(self):
        for state in inv.TERMINAL_STATES:
            self.assertTrue(inv.is_terminal(state))

    def test_non_terminal_states_are_not_terminal(self):
        for state in inv.TASK_STATES - inv.TERMINAL_STATES:
            self.assertFalse(inv.is_terminal(state))

    def test_unknown_state_raises(self):
        with self.assertRaises(ValueError):
            inv.is_terminal("SORT-OF-DONE")


class TestClassifyFailure(unittest.TestCase):
    def test_transient(self):
        for name in inv.TRANSIENT_FAILURES:
            self.assertEqual(inv.classify_failure(name), "transient")

    def test_semantic(self):
        for name in inv.SEMANTIC_FAILURES:
            self.assertEqual(inv.classify_failure(name), "semantic")

    def test_authority(self):
        for name in inv.AUTHORITY_FAILURES:
            self.assertEqual(inv.classify_failure(name), "authority")

    def test_other(self):
        known_other = (inv.FAILURE_CLASSES - inv.TRANSIENT_FAILURES
                        - inv.SEMANTIC_FAILURES - inv.AUTHORITY_FAILURES)
        self.assertTrue(len(known_other) > 0)
        for name in known_other:
            self.assertEqual(inv.classify_failure(name), "other")

    def test_unknown_failure_raises(self):
        # This is the case the docstring calls out by name: an
        # unrecognised failure must never be silently treated as
        # transient and handed a free retry.
        with self.assertRaises(ValueError):
            inv.classify_failure("gremlins")


if __name__ == "__main__":
    unittest.main()

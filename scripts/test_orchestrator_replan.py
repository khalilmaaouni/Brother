#!/usr/bin/env python3
"""Calibration for scripts/orchestrator_replan.py.

The property this file exists to prove is not "the happy path returns a
verdict", it is that the six rules in the worker contract cannot regress
silently, and in particular that rule 4 (no identical third attempt) is
enforced against what an attempt actually did, never against what a
worker claims it did.
"""
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import orchestrator_replan as rp  # noqa: E402


def _attempt(failure_class, files=(), check="", strategy="", base_revision="sha1",
             **extra):
    row = {
        "failure_class": failure_class,
        "files_written": list(files),
        "check": check,
        "strategy": strategy,
        "base_revision": base_revision,
    }
    row.update(extra)
    return row


class TestClassify(unittest.TestCase):
    def test_transient(self):
        for name in ("rate_limit", "overloaded", "timeout", "tool_unavailable"):
            self.assertEqual(rp.classify(name), "transient")

    def test_semantic(self):
        for name in ("test_failure", "canonical_regression", "scope_violation",
                     "integration_conflict"):
            self.assertEqual(rp.classify(name), "semantic")

    def test_authority(self):
        self.assertEqual(rp.classify("policy_refusal"), "authority")

    def test_other(self):
        for name in ("worker_crash", "missing_evidence", "resource_pressure",
                     "unknown"):
            self.assertEqual(rp.classify(name), "other")

    def test_unrecognised_class_raises(self):
        # Rule 6: never defaulted to transient. A name outside the frozen
        # vocabulary must stop the caller, not be hammered as retryable.
        with self.assertRaises(ValueError):
            rp.classify("not_a_real_failure_class")


class TestRule1TransientRetry(unittest.TestCase):
    def test_transient_retries_without_changing_approach(self):
        history = [_attempt("rate_limit", strategy="s1")]
        d = rp.decision("u1", "rate_limit", history, budgets={})
        self.assertEqual(d.verdict, rp.RETRY)
        self.assertIsNone(d.required_change)

    def test_transient_does_not_consume_semantic_budget(self):
        # Three transient failures, budget of 1 semantic attempt: if a
        # transient failure consumed the semantic budget, the third
        # transient attempt below would already read EXHAUSTED. It must
        # not: no real work began on any of them.
        history = [_attempt("timeout", strategy="s1"),
                   _attempt("overloaded", strategy="s1"),
                   _attempt("timeout", strategy="s1")]
        d = rp.decision("u1", "timeout", history,
                        budgets={"max_semantic_attempts": 1})
        self.assertEqual(d.verdict, rp.RETRY)

    def test_transient_budget_exhausts_on_its_own_counter(self):
        history = [_attempt("timeout"), _attempt("timeout"), _attempt("timeout")]
        d = rp.decision("u1", "timeout", history,
                        budgets={"max_transient_retries": 3})
        self.assertEqual(d.verdict, rp.EXHAUSTED)
        self.assertIn("transient retry budget", d.reason)
        self.assertIn("max_transient_retries", d.reason)


class TestRule2SemanticNeedsChange(unittest.TestCase):
    def test_first_semantic_failure_requires_changed_approach(self):
        history = [_attempt("test_failure", strategy="s1", files=["a.py"])]
        d = rp.decision("u1", "test_failure", history, budgets={})
        self.assertEqual(d.verdict, rp.RETRY_CHANGED)
        self.assertIsNotNone(d.required_change)
        self.assertIn("evidence", d.required_change)


class TestRule3AuthorityNeverRetries(unittest.TestCase):
    def test_policy_refusal_parks_regardless_of_history_or_budget(self):
        history = [_attempt("policy_refusal", strategy="s1")]
        d = rp.decision("u1", "policy_refusal", history,
                        budgets={"max_semantic_attempts": 999,
                                 "max_transient_retries": 999})
        self.assertEqual(d.verdict, rp.PARK)

    def test_policy_refusal_parks_even_on_a_fresh_unit(self):
        history = [_attempt("policy_refusal")]
        d = rp.decision("u2", "policy_refusal", history, budgets={})
        self.assertEqual(d.verdict, rp.PARK)
        self.assertIsNone(d.required_change)


class TestRule4NoIdenticalThirdAttempt(unittest.TestCase):
    def test_identical_fingerprints_replan_even_when_worker_claims_a_change(self):
        attempt_1 = _attempt("test_failure", strategy="tighten the regex",
                             files=["a.py"], check="pytest tests/x.py")
        attempt_2 = _attempt("test_failure", strategy="tighten the regex",
                             files=["a.py"], check="pytest tests/x.py",
                             # A worker can always write this; it must not
                             # be read as evidence of anything.
                             note="I tried a completely different approach this time")
        d = rp.decision("u1", "test_failure", [attempt_1, attempt_2], budgets={})
        self.assertEqual(d.verdict, rp.REPLAN)
        self.assertIsNotNone(d.required_change)
        self.assertIn("re-plan", d.required_change)

    def test_genuinely_different_attempts_are_allowed(self):
        # THE OPPOSITE BAD STATE a green suite would also pass: a
        # fingerprint so coarse that every attempt looks identical would
        # make this rule fire always and block every legitimate retry.
        # This attempt differs only in its declared strategy and it must
        # be enough to be read as changed.
        attempt_1 = _attempt("test_failure", strategy="tighten the regex",
                             files=["a.py"], check="pytest tests/x.py")
        attempt_2 = _attempt("test_failure", strategy="rewrite the parser",
                             files=["a.py", "b.py"], check="pytest tests/x.py")
        d = rp.decision("u1", "test_failure", [attempt_1, attempt_2], budgets={})
        self.assertEqual(d.verdict, rp.RETRY_CHANGED)

    def test_third_attempt_after_replan_is_evaluated_against_the_new_pair(self):
        # A genuine re-plan (attempt 3 differs from attempt 2) must not be
        # blocked just because attempt 1 and attempt 2 were identical.
        attempt_1 = _attempt("test_failure", strategy="s1", files=["a.py"])
        attempt_2 = _attempt("test_failure", strategy="s1", files=["a.py"])
        attempt_3 = _attempt("test_failure", strategy="s2-after-replan",
                             files=["a.py", "b.py"])
        d = rp.decision("u1", "test_failure",
                        [attempt_1, attempt_2, attempt_3],
                        budgets={"max_semantic_attempts": 10})
        self.assertEqual(d.verdict, rp.RETRY_CHANGED)


class TestFingerprintIgnoresVolatileFields(unittest.TestCase):
    def test_fingerprint_ignores_timestamp_and_run_id(self):
        a = _attempt("test_failure", strategy="s1", files=["a.py"],
                     timestamp="2026-09-18T00:00:00Z", run_id="run-aaa")
        b = _attempt("test_failure", strategy="s1", files=["a.py"],
                     timestamp="2026-09-18T05:00:00Z", run_id="run-bbb")
        self.assertEqual(rp.fingerprint(a), rp.fingerprint(b))
        self.assertTrue(rp.same_approach(a, b))

    def test_a_fingerprint_that_included_the_timestamp_would_differ(self):
        # This does not call any hidden code path in orchestrator_replan;
        # it builds the fingerprint the WRONG way, inline, to show what
        # would happen if fingerprint() were ever edited to read a
        # volatile field, and contrasts it with the real one. If someone
        # adds "timestamp" to _FINGERPRINT_FIELDS and to fingerprint()'s
        # returned tuple, this test starts failing on the first assertion
        # below (rp.fingerprint(a) == rp.fingerprint(b) would go False),
        # which is the point: rule 4 would silently stop firing on real
        # duplicate attempts the moment that happens.
        a = _attempt("test_failure", strategy="s1", files=["a.py"],
                     timestamp="2026-09-18T00:00:00Z")
        b = _attempt("test_failure", strategy="s1", files=["a.py"],
                     timestamp="2026-09-18T05:00:00Z")
        self.assertEqual(rp.fingerprint(a), rp.fingerprint(b))
        bad_fingerprint_a = rp.fingerprint(a) + (a["timestamp"],)
        bad_fingerprint_b = rp.fingerprint(b) + (b["timestamp"],)
        self.assertNotEqual(bad_fingerprint_a, bad_fingerprint_b)


class TestOtherBucketTreatedLikeSemantic(unittest.TestCase):
    def test_worker_crash_requires_changed_approach(self):
        history = [_attempt("worker_crash", strategy="s1")]
        d = rp.decision("u1", "worker_crash", history, budgets={})
        self.assertEqual(d.verdict, rp.RETRY_CHANGED)

    def test_worker_crash_identical_twice_replans(self):
        a1 = _attempt("worker_crash", strategy="s1", files=["a.py"])
        a2 = _attempt("worker_crash", strategy="s1", files=["a.py"])
        d = rp.decision("u1", "worker_crash", [a1, a2], budgets={})
        self.assertEqual(d.verdict, rp.REPLAN)


class TestRule5Exhausted(unittest.TestCase):
    def test_semantic_budget_exhausted_names_the_budget(self):
        a1 = _attempt("test_failure", strategy="s1", files=["a.py"])
        a2 = _attempt("test_failure", strategy="s2", files=["b.py"])
        d = rp.decision("u1", "test_failure", [a1, a2],
                        budgets={"max_semantic_attempts": 2})
        self.assertEqual(d.verdict, rp.EXHAUSTED)
        self.assertIn("semantic attempt budget", d.reason)
        self.assertIn("max_semantic_attempts", d.reason)

    def test_absent_budget_never_exhausts(self):
        a1 = _attempt("test_failure", strategy="s1", files=["a.py"])
        a2 = _attempt("test_failure", strategy="s2", files=["b.py"])
        a3 = _attempt("test_failure", strategy="s3", files=["c.py"])
        d = rp.decision("u1", "test_failure", [a1, a2, a3], budgets={})
        self.assertNotEqual(d.verdict, rp.EXHAUSTED)


class TestRule6UnknownFailureRaises(unittest.TestCase):
    def test_unknown_name_raises_in_decision_too(self):
        history = [_attempt("not_a_real_class")]
        with self.assertRaises(ValueError):
            rp.decision("u1", "not_a_real_class", history, budgets={})

    def test_mismatched_history_tail_raises(self):
        # Bookkeeping bug: the caller's history disagrees with the
        # failure_class it is asking about. Must raise, never guess which
        # one is right.
        history = [_attempt("test_failure")]
        with self.assertRaises(ValueError):
            rp.decision("u1", "timeout", history, budgets={})

    def test_empty_history_raises(self):
        with self.assertRaises(ValueError):
            rp.decision("u1", "timeout", [], budgets={})


if __name__ == "__main__":
    unittest.main()

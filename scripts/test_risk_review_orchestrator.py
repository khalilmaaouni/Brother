#!/usr/bin/env python3
"""Calibration for scripts/risk_review_orchestrator.py (WBS-40.05).

The property under test is the anti-silent-auto-accept guarantee: a high
or catastrophic false-merge-cost candidate never auto-accepts on score
alone, and an unrecognized cost-class value is refused rather than
silently treated as low-risk. Synthetic fixture data only.
"""
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import risk_review_orchestrator as RRO  # noqa: E402


class RouteCandidateTests(unittest.TestCase):
    def test_high_score_low_risk_auto_accepts(self):
        decision = RRO.route_candidate(
            "cand-1", score=0.97,
            false_merge_cost_class="LOW", missed_match_cost_class="LOW",
            merge_threshold=0.9, review_threshold=0.5,
        )
        self.assertEqual(decision["routing"], "AUTO_ACCEPT")

    def test_high_score_catastrophic_cost_goes_to_review(self):
        # The core anti-silent-auto-accept property: score alone is never
        # enough when false_merge_cost_class is high risk.
        decision = RRO.route_candidate(
            "cand-2", score=0.99,
            false_merge_cost_class="CATASTROPHIC", missed_match_cost_class="LOW",
            merge_threshold=0.9, review_threshold=0.5,
        )
        self.assertEqual(decision["routing"], "CLERICAL_REVIEW")
        self.assertIn("CATASTROPHIC", decision["reason"])

    def test_high_score_high_cost_also_goes_to_review(self):
        decision = RRO.route_candidate(
            "cand-2b", score=1.0,
            false_merge_cost_class="HIGH", missed_match_cost_class="LOW",
            merge_threshold=0.9, review_threshold=0.5,
        )
        self.assertEqual(decision["routing"], "CLERICAL_REVIEW")

    def test_low_score_auto_rejects(self):
        decision = RRO.route_candidate(
            "cand-3", score=0.1,
            false_merge_cost_class="LOW", missed_match_cost_class="LOW",
            merge_threshold=0.9, review_threshold=0.5,
        )
        self.assertEqual(decision["routing"], "AUTO_REJECT")

    def test_mid_band_goes_to_review(self):
        decision = RRO.route_candidate(
            "cand-4", score=0.7,
            false_merge_cost_class="MEDIUM", missed_match_cost_class="MEDIUM",
            merge_threshold=0.9, review_threshold=0.5,
        )
        self.assertEqual(decision["routing"], "CLERICAL_REVIEW")

    def test_unrecognized_cost_class_is_refused(self):
        with self.assertRaises(RRO.RiskReviewError) as ctx:
            RRO.route_candidate(
                "cand-5", score=0.99,
                false_merge_cost_class="basically-fine",
                missed_match_cost_class="LOW",
                merge_threshold=0.9, review_threshold=0.5,
            )
        self.assertIn("basically-fine", str(ctx.exception))

    def test_unrecognized_missed_match_cost_class_is_refused(self):
        with self.assertRaises(RRO.RiskReviewError):
            RRO.route_candidate(
                "cand-6", score=0.99,
                false_merge_cost_class="LOW",
                missed_match_cost_class="whatever",
                merge_threshold=0.9, review_threshold=0.5,
            )

    def test_non_numeric_score_is_refused(self):
        with self.assertRaises(RRO.RiskReviewError):
            RRO.route_candidate(
                "cand-7", score="high",
                false_merge_cost_class="LOW", missed_match_cost_class="LOW",
                merge_threshold=0.9, review_threshold=0.5,
            )

    def test_inverted_thresholds_are_refused(self):
        with self.assertRaises(RRO.RiskReviewError):
            RRO.route_candidate(
                "cand-8", score=0.8,
                false_merge_cost_class="LOW", missed_match_cost_class="LOW",
                merge_threshold=0.5, review_threshold=0.9,
            )


class RouteBatchTests(unittest.TestCase):
    def test_batch_produces_correct_bucket_counts(self):
        candidates = [
            {  # AUTO_ACCEPT
                "candidate_id": "b1", "score": 0.95,
                "false_merge_cost_class": "LOW", "missed_match_cost_class": "LOW",
                "merge_threshold": 0.9, "review_threshold": 0.5,
            },
            {  # AUTO_ACCEPT
                "candidate_id": "b2", "score": 0.91,
                "false_merge_cost_class": "MEDIUM", "missed_match_cost_class": "LOW",
                "merge_threshold": 0.9, "review_threshold": 0.5,
            },
            {  # AUTO_REJECT
                "candidate_id": "b3", "score": 0.2,
                "false_merge_cost_class": "LOW", "missed_match_cost_class": "LOW",
                "merge_threshold": 0.9, "review_threshold": 0.5,
            },
            {  # CLERICAL_REVIEW: ambiguous middle
                "candidate_id": "b4", "score": 0.6,
                "false_merge_cost_class": "MEDIUM", "missed_match_cost_class": "MEDIUM",
                "merge_threshold": 0.9, "review_threshold": 0.5,
            },
            {  # CLERICAL_REVIEW: high score but catastrophic cost forces it
                "candidate_id": "b5", "score": 0.99,
                "false_merge_cost_class": "CATASTROPHIC", "missed_match_cost_class": "LOW",
                "merge_threshold": 0.9, "review_threshold": 0.5,
            },
            {  # REFUSED: unrecognized cost class
                "candidate_id": "b6", "score": 0.95,
                "false_merge_cost_class": "super-low", "missed_match_cost_class": "LOW",
                "merge_threshold": 0.9, "review_threshold": 0.5,
            },
        ]
        decisions, summary = RRO.route_batch(candidates)

        self.assertEqual(len(decisions), 6)
        self.assertEqual(summary["AUTO_ACCEPT"], 2)
        self.assertEqual(summary["AUTO_REJECT"], 1)
        self.assertEqual(summary["CLERICAL_REVIEW"], 2)
        self.assertEqual(summary[RRO.REFUSED], 1)

        by_id = {d["candidate_id"]: d["routing"] for d in decisions}
        self.assertEqual(by_id["b1"], "AUTO_ACCEPT")
        self.assertEqual(by_id["b5"], "CLERICAL_REVIEW")
        self.assertEqual(by_id["b6"], RRO.REFUSED)

    def test_missing_threshold_in_batch_is_refused_not_a_crash(self):
        # H3: a candidate missing merge_threshold/review_threshold entirely
        # (or carrying None/a wrong type) must not throw a bare TypeError
        # out of route_batch and crash the whole batch -- it is refused
        # like any other malformed candidate, and its siblings still route.
        candidates = [
            {  # AUTO_ACCEPT, valid
                "candidate_id": "m1", "score": 0.95,
                "false_merge_cost_class": "LOW", "missed_match_cost_class": "LOW",
                "merge_threshold": 0.9, "review_threshold": 0.5,
            },
            {  # REFUSED: merge_threshold missing entirely
                "candidate_id": "m2", "score": 0.95,
                "false_merge_cost_class": "LOW", "missed_match_cost_class": "LOW",
                "review_threshold": 0.5,
            },
            {  # REFUSED: review_threshold is None
                "candidate_id": "m3", "score": 0.95,
                "false_merge_cost_class": "LOW", "missed_match_cost_class": "LOW",
                "merge_threshold": 0.9, "review_threshold": None,
            },
            {  # REFUSED: merge_threshold is a string
                "candidate_id": "m4", "score": 0.95,
                "false_merge_cost_class": "LOW", "missed_match_cost_class": "LOW",
                "merge_threshold": "high", "review_threshold": 0.5,
            },
        ]
        decisions, summary = RRO.route_batch(candidates)

        self.assertEqual(len(decisions), 4)
        self.assertEqual(summary["AUTO_ACCEPT"], 1)
        self.assertEqual(summary[RRO.REFUSED], 3)

        by_id = {d["candidate_id"]: d["routing"] for d in decisions}
        self.assertEqual(by_id["m1"], "AUTO_ACCEPT")
        self.assertEqual(by_id["m2"], RRO.REFUSED)
        self.assertEqual(by_id["m3"], RRO.REFUSED)
        self.assertEqual(by_id["m4"], RRO.REFUSED)
        m2_reason = next(d["reason"] for d in decisions if d["candidate_id"] == "m2")
        self.assertIn("merge_threshold", m2_reason)

    def test_empty_batch_still_reports_every_bucket(self):
        decisions, summary = RRO.route_batch([])
        self.assertEqual(decisions, [])
        for route in RRO.ROUTES:
            self.assertEqual(summary[route], 0)
        self.assertEqual(summary[RRO.REFUSED], 0)


if __name__ == "__main__":
    unittest.main(verbosity=2)

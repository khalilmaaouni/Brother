#!/usr/bin/env python3
"""Tests for scripts/coe_arbitrate.py: the five rules (A1-A5), the closed
criteria set, and the edge list.

Plain unittest, runnable directly: python3 scripts/test_coe_arbitrate.py -v
"""

import unittest

from coe_arbitrate import (
    MISSING,
    NO_DATA,
    ArbitrationError,
    OUTCOME_CEILING,
    OUTCOME_EXTRA_ROUND_REQUIRED,
    OUTCOME_PASS,
    OUTCOME_REJECT,
    arbitrate,
)

TWELVE = tuple(f"C{i}" for i in range(1, 13))
SEATS_5 = ("architect", "assurance", "operations", "evidence", "owner-proxy")


def cell(seat, criterion, score):
    return {"seat": seat, "criterion": criterion, "score": score}


class A1MinimumNotAverage(unittest.TestCase):
    def test_single_low_score_rejects_despite_high_average(self):
        # Eleven 10s and one 4: average is 9.5, minimum is 4.
        matrix = []
        for c in TWELVE:
            score = 4 if c == "C7" else 10
            matrix.append(cell("architect", c, score))
        verdict = arbitrate(matrix, seats=("architect",), criteria=TWELVE)
        self.assertEqual(verdict.outcome, OUTCOME_REJECT)
        self.assertEqual(verdict.score, 4)
        self.assertEqual(verdict.capping_criterion, "C7")
        avg = sum(v for v in verdict.per_criterion.values()) / len(verdict.per_criterion)
        self.assertAlmostEqual(avg, 9.5)


class A2DisagreementResolvesDownward(unittest.TestCase):
    def test_unanswered_disagreement_resolves_low(self):
        matrix = [cell("assurance", "C2", 3), cell("architect", "C2", 9)]
        verdict = arbitrate(matrix, seats=("assurance", "architect"), criteria=("C2",))
        self.assertEqual(verdict.per_criterion["C2"], 3)
        self.assertNotIn("C2", verdict.raises_permitted)
        self.assertEqual(verdict.outcome, OUTCOME_REJECT)

    def test_answered_disagreement_permits_raise_and_records_answer(self):
        matrix = [cell("assurance", "C2", 3), cell("architect", "C2", 9)]
        objections = [
            {
                "id": "R1",
                "criterion": "C2",
                "seat": "architect",
                "objection": "no mutation test was shown for the deleted comparison",
                "answer": "R1: retested with the comparison deleted, suite went red as expected",
            }
        ]
        verdict = arbitrate(
            matrix,
            seats=("assurance", "architect"),
            criteria=("C2",),
            objections=objections,
        )
        self.assertEqual(verdict.per_criterion["C2"], 9)
        self.assertIn("C2", verdict.raises_permitted)
        self.assertEqual(verdict.raises_permitted["C2"]["id"], "R1")
        self.assertIn("retested", verdict.raises_permitted["C2"]["answer"])

    def test_raise_with_answer_not_referencing_objection_is_refused(self):
        matrix = [cell("assurance", "C2", 3), cell("architect", "C2", 9)]
        objections = [
            {
                "id": "R1",
                "criterion": "C2",
                "seat": "architect",
                "objection": "no mutation test was shown",
                "answer": "trust me, it is fine",
            }
        ]
        verdict = arbitrate(
            matrix,
            seats=("assurance", "architect"),
            criteria=("C2",),
            objections=objections,
        )
        self.assertEqual(verdict.per_criterion["C2"], 3)
        self.assertNotIn("C2", verdict.raises_permitted)


class A3AuthorityBeatsHeadcount(unittest.TestCase):
    def test_authoritative_seat_not_outvoted_by_majority(self):
        matrix = [cell(f"seat{i}", "C8", 10) for i in range(5)]
        matrix.append(cell("security", "C8", 5))
        seats = tuple(f"seat{i}" for i in range(5)) + ("security",)
        verdict = arbitrate(
            matrix, seats=seats, criteria=("C8",), authoritative_map={"C8": "security"}
        )
        self.assertEqual(verdict.per_criterion["C8"], 5)

    def test_authoritative_seat_no_data_caps_regardless_of_others(self):
        matrix = [cell(f"seat{i}", "C8", 10) for i in range(5)]
        matrix.append(cell("security", "C8", NO_DATA))
        seats = tuple(f"seat{i}" for i in range(5)) + ("security",)
        verdict = arbitrate(
            matrix, seats=seats, criteria=("C8",), authoritative_map={"C8": "security"}
        )
        self.assertEqual(verdict.outcome, OUTCOME_CEILING)
        self.assertEqual(verdict.capping_criterion, "C8")


class A4NoDataNeverPassNeverZero(unittest.TestCase):
    def test_mixed_no_data_and_numeric_resolves_from_numeric(self):
        matrix = [cell("architect", "C9", NO_DATA), cell("evidence", "C9", 9)]
        verdict = arbitrate(matrix, seats=("architect", "evidence"), criteria=("C9",))
        self.assertEqual(verdict.per_criterion["C9"], 9)
        self.assertNotEqual(verdict.outcome, OUTCOME_CEILING)

    def test_every_seat_no_data_caps_and_is_not_zero(self):
        matrix = [cell("architect", "C9", NO_DATA), cell("evidence", "C9", NO_DATA)]
        verdict = arbitrate(matrix, seats=("architect", "evidence"), criteria=("C9",))
        self.assertEqual(verdict.outcome, OUTCOME_CEILING)
        self.assertEqual(verdict.per_criterion["C9"], NO_DATA)
        self.assertNotEqual(verdict.per_criterion["C9"], 0)
        self.assertIsNone(verdict.score)

    def test_no_seat_no_data_resolves_normally(self):
        matrix = [cell("architect", "C9", 9), cell("evidence", "C9", 10)]
        verdict = arbitrate(matrix, seats=("architect", "evidence"), criteria=("C9",))
        self.assertEqual(verdict.per_criterion["C9"], 9)
        self.assertEqual(verdict.outcome, OUTCOME_PASS)


class A5UnanimousTenIsAFinding(unittest.TestCase):
    def test_all_tens_triggers_extra_round(self):
        matrix = [cell(seat, c, 10) for seat in SEATS_5 for c in TWELVE]
        verdict = arbitrate(matrix, seats=SEATS_5, criteria=TWELVE)
        self.assertEqual(verdict.outcome, OUTCOME_EXTRA_ROUND_REQUIRED)

    def test_one_nine_among_tens_does_not_trigger_extra_round(self):
        matrix = []
        for seat in SEATS_5:
            for c in TWELVE:
                score = 9 if (seat == SEATS_5[0] and c == "C1") else 10
                matrix.append(cell(seat, c, score))
        verdict = arbitrate(matrix, seats=SEATS_5, criteria=TWELVE)
        self.assertNotEqual(verdict.outcome, OUTCOME_EXTRA_ROUND_REQUIRED)
        self.assertEqual(verdict.outcome, OUTCOME_PASS)
        self.assertEqual(verdict.score, 9)


class ClosedCriteriaSet(unittest.TestCase):
    """The fix for the false-green hole: a matrix scoring only part of the
    declared criteria must not be indistinguishable from a complete one.
    """

    def test_partial_matrix_is_not_pass_and_names_the_missing_nine(self):
        scored = ("C1", "C2", "C3")
        matrix = [cell("architect", c, 10) for c in scored]
        verdict = arbitrate(matrix, seats=("architect",), criteria=TWELVE)
        self.assertNotEqual(verdict.outcome, OUTCOME_PASS)
        self.assertEqual(verdict.outcome, OUTCOME_CEILING)
        expected_missing = sorted(set(TWELVE) - set(scored))
        self.assertEqual(verdict.missing_criteria, expected_missing)
        for c in expected_missing:
            self.assertIn(c, verdict.reason)

    def test_supplying_the_nine_makes_pass_reachable(self):
        matrix = [cell("architect", c, 10) for c in TWELVE]
        verdict = arbitrate(matrix, seats=("architect",), criteria=TWELVE)
        self.assertEqual(verdict.missing_criteria, [])
        # All 10s across every seat and criterion is A5's unanimity finding,
        # not a blanket refusal caused by the closed-set rule itself: prove
        # the rule is specific by breaking unanimity with one 9.
        matrix[0] = cell("architect", TWELVE[0], 9)
        verdict = arbitrate(matrix, seats=("architect",), criteria=TWELVE)
        self.assertEqual(verdict.outcome, OUTCOME_PASS)

    def test_criterion_in_matrix_but_not_in_criteria_raises(self):
        matrix = [cell("architect", "C1", 9), cell("architect", "C13", 9)]
        with self.assertRaises(ArbitrationError):
            arbitrate(matrix, seats=("architect",), criteria=("C1",))

    def test_missing_no_data_and_zero_are_three_distinct_states(self):
        matrix = [
            # C_B: every seat NO-DATA.
            cell("architect", "C_b", NO_DATA),
            # C_C: a measured zero.
            cell("architect", "C_c", 0),
            # C_a is in criteria but has no cell at all: MISSING.
        ]
        verdict = arbitrate(
            matrix, seats=("architect",), criteria=("C_a", "C_b", "C_c")
        )
        missing_value = verdict.per_criterion["C_a"]
        no_data_value = verdict.per_criterion["C_b"]
        zero_value = verdict.per_criterion["C_c"]
        self.assertEqual(missing_value, MISSING)
        self.assertEqual(no_data_value, NO_DATA)
        self.assertEqual(zero_value, 0)
        # The assertion that matters most: pairwise distinct, not just
        # individually correct.
        self.assertNotEqual(missing_value, no_data_value)
        self.assertNotEqual(missing_value, zero_value)
        self.assertNotEqual(no_data_value, zero_value)
        self.assertIn("C_a", verdict.missing_criteria)
        self.assertNotIn("C_b", verdict.missing_criteria)
        self.assertNotIn("C_c", verdict.missing_criteria)


class EdgeList(unittest.TestCase):
    def test_empty_matrix_raises(self):
        with self.assertRaises(ArbitrationError):
            arbitrate([], seats=("architect",), criteria=("C1",))

    def test_exactly_one_seat(self):
        matrix = [cell("architect", "C1", 9)]
        verdict = arbitrate(matrix, seats=("architect",), criteria=("C1",))
        self.assertEqual(verdict.outcome, OUTCOME_PASS)

    def test_seat_scored_only_some_criteria(self):
        matrix = [cell("architect", "C1", 9), cell("evidence", "C1", 9), cell("evidence", "C9", 9)]
        verdict = arbitrate(
            matrix, seats=("architect", "evidence"), criteria=("C1", "C9")
        )
        self.assertEqual(verdict.per_criterion["C1"], 9)
        self.assertEqual(verdict.per_criterion["C9"], 9)

    def test_score_outside_range_raises(self):
        with self.assertRaises(ArbitrationError):
            arbitrate([cell("architect", "C1", 11)], seats=("architect",), criteria=("C1",))
        with self.assertRaises(ArbitrationError):
            arbitrate([cell("architect", "C1", -1)], seats=("architect",), criteria=("C1",))

    def test_non_integer_score_raises(self):
        with self.assertRaises(ArbitrationError):
            arbitrate([cell("architect", "C1", 9.5)], seats=("architect",), criteria=("C1",))
        with self.assertRaises(ArbitrationError):
            arbitrate([cell("architect", "C1", "9")], seats=("architect",), criteria=("C1",))

    def test_bool_score_raises(self):
        with self.assertRaises(ArbitrationError):
            arbitrate([cell("architect", "C1", True)], seats=("architect",), criteria=("C1",))

    def test_seat_in_matrix_not_in_seats_raises(self):
        with self.assertRaises(ArbitrationError):
            arbitrate([cell("intruder", "C1", 9)], seats=("architect",), criteria=("C1",))

    def test_seat_in_seats_absent_from_matrix_is_allowed(self):
        matrix = [cell("architect", "C1", 9)]
        verdict = arbitrate(
            matrix, seats=("architect", "bystander"), criteria=("C1",)
        )
        self.assertEqual(verdict.outcome, OUTCOME_PASS)

    def test_authoritative_map_names_seat_that_did_not_score_criterion_raises(self):
        matrix = [cell("architect", "C1", 9)]
        with self.assertRaises(ArbitrationError):
            arbitrate(
                matrix,
                seats=("architect", "security"),
                criteria=("C1",),
                authoritative_map={"C1": "security"},
            )

    def test_duplicate_entry_for_seat_and_criterion_raises(self):
        matrix = [cell("architect", "C1", 9), cell("architect", "C1", 5)]
        with self.assertRaises(ArbitrationError):
            arbitrate(matrix, seats=("architect",), criteria=("C1",))

    def test_objection_referencing_criterion_nobody_scored_raises(self):
        matrix = [cell("architect", "C1", 9)]
        objections = [
            {
                "id": "R1",
                "criterion": "C99",
                "seat": "architect",
                "objection": "irrelevant",
                "answer": "R1: irrelevant",
            }
        ]
        with self.assertRaises(ArbitrationError):
            arbitrate(
                matrix, seats=("architect",), criteria=("C1",), objections=objections
            )

    def test_seats_empty_raises(self):
        with self.assertRaises(ArbitrationError):
            arbitrate([cell("architect", "C1", 9)], seats=(), criteria=("C1",))

    def test_criteria_empty_raises(self):
        with self.assertRaises(ArbitrationError):
            arbitrate([cell("architect", "C1", 9)], seats=("architect",), criteria=())


if __name__ == "__main__":
    unittest.main()

#!/usr/bin/env python3
"""Tests for scripts/coe_score.py: independent scoring, isolated seats.

Plain unittest, runnable directly: python3 scripts/test_coe_score.py -v

RECORDED MUTATION (per the worker contract, not part of the suite itself):
copy this repo under /tmp, edit _build_prompt() in the copy's
scripts/coe_score.py so the prompt for every seat after the first also
includes the previous seat's returned score, then rerun
IsolationIsMechanicallyProven.test_no_prompt_contains_another_seats_score
against the copy. That test must go red; if it stays green the isolation
rule is unproven, not proven.
"""

import sys
import unittest

from coe_arbitrate import arbitrate
from coe_score import (
    NO_DATA,
    ScoreMatrix,
    ScoringError,
    score_council,
)


class RecordingStub:
    """An `ask` double that records every (seat, criterion, prompt) it was
    called with, and answers from a fixed table keyed by (seat, criterion).

    This is the isolation-proving instrument: score_council() never sees
    this object's recorded calls, so recording them here cannot itself leak
    information back into a later prompt. Only score_council()'s own
    construction of `prompt` could do that, which is exactly what these
    tests are checking.
    """

    def __init__(self, table, raise_on=frozenset()):
        self.table = table
        self.raise_on = raise_on
        self.calls = []

    def __call__(self, seat_id, criterion, prompt):
        self.calls.append((seat_id, criterion, prompt))
        if (seat_id, criterion) in self.raise_on:
            raise RuntimeError("stub seat exploded on %s/%s" % (seat_id, criterion))
        return self.table[(seat_id, criterion)]


def cells_as_set(matrix):
    """Order-independent view of a matrix's contents, for comparing two
    matrices produced by scoring the same council in a different order.
    """
    return {(c["seat"], c["criterion"], c["score"]) for c in matrix}


class FakeCouncil:
    def __init__(self, seats):
        self.seats = tuple(seats)


class IsolationIsMechanicallyProven(unittest.TestCase):
    """Rule 1: no seat's prompt may contain another seat's returned score.

    Proven mechanically, not by reading the code: distinct scores per seat,
    then a substring search over every recorded prompt.
    """

    def test_no_prompt_contains_another_seats_score(self):
        # The bad state a green run would also pass, named per the worker
        # contract: if every seat scored the same value, isolation would be
        # trivially true because there would be nothing distinct to leak.
        # Distinct values per seat make this a real assertion.
        table = {
            ("seatA", "alpha"): 3,
            ("seatA", "beta"): 6,
            ("seatB", "alpha"): 9,
            ("seatB", "beta"): 2,
            ("seatC", "alpha"): 7,
            ("seatC", "beta"): 4,
        }
        stub = RecordingStub(table)
        council = FakeCouncil(["seatA", "seatB", "seatC"])
        matrix = score_council(
            council, "the methodology text", ask=stub, criteria=("alpha", "beta")
        )
        self.assertEqual(len(matrix), 6)

        scores_by_seat = {}
        for (seat_id, _criterion, _prompt) in stub.calls:
            pass
        for cell in matrix:
            scores_by_seat.setdefault(cell["seat"], set()).add(cell["score"])

        for (seat_id, criterion, prompt) in stub.calls:
            for other_seat, other_scores in scores_by_seat.items():
                if other_seat == seat_id:
                    continue
                for score in other_scores:
                    self.assertNotIn(
                        str(score),
                        prompt,
                        "prompt for %s/%s leaked %s's score %r"
                        % (seat_id, criterion, other_seat, score),
                    )

    def test_prompt_never_names_another_seat(self):
        table = {("seatA", "alpha"): 5, ("seatB", "alpha"): 8}
        stub = RecordingStub(table)
        council = FakeCouncil(["seatA", "seatB"])
        score_council(council, "text", ask=stub, criteria=("alpha",))
        for (seat_id, _criterion, prompt) in stub.calls:
            other = "seatB" if seat_id == "seatA" else "seatA"
            self.assertNotIn(other, prompt)


class OrderIndependence(unittest.TestCase):
    """Rule 2: the same council scored in a different seat order produces
    the same matrix (compared as a set, since cell order itself may differ).
    """

    def test_reversed_seat_order_produces_same_matrix(self):
        table = {
            ("seatA", "alpha"): 1,
            ("seatA", "beta"): 2,
            ("seatB", "alpha"): 9,
            ("seatB", "beta"): 8,
            ("seatC", "alpha"): 4,
            ("seatC", "beta"): NO_DATA,
        }
        forward = score_council(
            FakeCouncil(["seatA", "seatB", "seatC"]),
            "m",
            ask=RecordingStub(table),
            criteria=("alpha", "beta"),
        )
        backward = score_council(
            FakeCouncil(["seatC", "seatB", "seatA"]),
            "m",
            ask=RecordingStub(table),
            criteria=("beta", "alpha"),
        )
        self.assertEqual(cells_as_set(forward), cells_as_set(backward))


class ProseIsFailureNotInterpretation(unittest.TestCase):
    """Rule 3: prose is a failure, never a number parsed out of it."""

    def test_prose_answer_becomes_a_failure_not_a_guessed_score(self):
        stub = RecordingStub({("seatA", "alpha"): "this looks like a solid 9 to me"})
        matrix = score_council(
            FakeCouncil(["seatA"]), "m", ask=stub, criteria=("alpha",)
        )
        self.assertEqual(len(matrix), 0)
        self.assertEqual(len(matrix.failures), 1)
        self.assertEqual(matrix.failures[0]["seat"], "seatA")
        self.assertEqual(matrix.failures[0]["criterion"], "alpha")
        self.assertIn("prose", matrix.failures[0]["reason"])


class ScoreRangeAndTypeRefused(unittest.TestCase):
    """Rule 4: outside 0-10, non-integer, or bool is refused."""

    def test_out_of_range_high_is_a_failure(self):
        stub = RecordingStub({("seatA", "alpha"): 11})
        matrix = score_council(FakeCouncil(["seatA"]), "m", ask=stub, criteria=("alpha",))
        self.assertEqual(len(matrix), 0)
        self.assertEqual(len(matrix.failures), 1)

    def test_out_of_range_low_is_a_failure(self):
        stub = RecordingStub({("seatA", "alpha"): -1})
        matrix = score_council(FakeCouncil(["seatA"]), "m", ask=stub, criteria=("alpha",))
        self.assertEqual(len(matrix), 0)

    def test_non_integer_is_a_failure(self):
        stub = RecordingStub({("seatA", "alpha"): 7.5})
        matrix = score_council(FakeCouncil(["seatA"]), "m", ask=stub, criteria=("alpha",))
        self.assertEqual(len(matrix), 0)

    def test_bool_is_refused_never_coerced_to_one_or_zero(self):
        stub = RecordingStub({("seatA", "alpha"): True})
        matrix = score_council(FakeCouncil(["seatA"]), "m", ask=stub, criteria=("alpha",))
        self.assertEqual(len(matrix), 0)
        self.assertIn("bool", matrix.failures[0]["reason"])

        stub2 = RecordingStub({("seatA", "alpha"): False})
        matrix2 = score_council(FakeCouncil(["seatA"]), "m", ask=stub2, criteria=("alpha",))
        self.assertEqual(len(matrix2), 0)


class NoDataIsPermittedAndNeverCoerced(unittest.TestCase):
    """Rule 5: NO-DATA is a permitted score value, kept as-is."""

    def test_no_data_survives_into_the_matrix_uncoerced(self):
        stub = RecordingStub({("seatA", "alpha"): NO_DATA})
        matrix = score_council(FakeCouncil(["seatA"]), "m", ask=stub, criteria=("alpha",))
        self.assertEqual(len(matrix), 1)
        self.assertEqual(matrix[0]["score"], NO_DATA)
        self.assertNotEqual(matrix[0]["score"], 0)
        self.assertEqual(matrix.failures, [])


class OneDeadSeatDoesNotLoseOthers(unittest.TestCase):
    """Rule 6: a seat that fails or times out does not fail the whole run."""

    def test_ask_raising_for_one_seat_leaves_other_seats_scored(self):
        table = {("seatB", "alpha"): 6, ("seatB", "beta"): 7}
        stub = RecordingStub(table, raise_on={("seatA", "alpha"), ("seatA", "beta")})
        matrix = score_council(
            FakeCouncil(["seatA", "seatB"]), "m", ask=stub, criteria=("alpha", "beta")
        )
        self.assertEqual(len(matrix), 2)
        self.assertEqual({c["seat"] for c in matrix}, {"seatB"})
        self.assertEqual(len(matrix.failures), 2)
        self.assertEqual({f["seat"] for f in matrix.failures}, {"seatA"})
        self.assertTrue(all("raised" in f["reason"] for f in matrix.failures))

    def test_ask_raising_on_first_call_still_asks_the_rest(self):
        table = {("seatA", "beta"): 5}
        stub = RecordingStub(table, raise_on={("seatA", "alpha")})
        matrix = score_council(
            FakeCouncil(["seatA"]), "m", ask=stub, criteria=("alpha", "beta")
        )
        self.assertEqual(len(matrix), 1)
        self.assertEqual(matrix[0]["criterion"], "beta")
        self.assertEqual(len(stub.calls), 2)


class EveryCellIsAttributed(unittest.TestCase):
    """Rule 7: every cell records which seat produced it for which
    criterion.
    """

    def test_every_cell_names_its_seat_and_criterion(self):
        table = {
            ("seatA", "alpha"): 3,
            ("seatB", "alpha"): 8,
        }
        matrix = score_council(
            FakeCouncil(["seatA", "seatB"]), "m", ask=RecordingStub(table), criteria=("alpha",)
        )
        for cell in matrix:
            self.assertIn("seat", cell)
            self.assertIn("criterion", cell)
            self.assertIn("score", cell)
        by_seat = {c["seat"]: c["score"] for c in matrix}
        self.assertEqual(by_seat["seatA"], 3)
        self.assertEqual(by_seat["seatB"], 8)


class MatrixShapeAcceptedByArbitrate(unittest.TestCase):
    """The contract's own requirement: produce EXACTLY the shape
    coe_arbitrate.arbitrate() expects. Proven by actually calling it.
    """

    def test_score_council_output_feeds_arbitrate_directly(self):
        table = {
            ("seatA", "C1"): 9,
            ("seatB", "C1"): 9,
        }
        matrix = score_council(
            FakeCouncil(["seatA", "seatB"]), "m", ask=RecordingStub(table), criteria=("C1",)
        )
        verdict = arbitrate(matrix, seats=("seatA", "seatB"), criteria=("C1",))
        self.assertEqual(verdict.score, 9)

    def test_matrix_is_a_list_subclass(self):
        self.assertTrue(issubclass(ScoreMatrix, list))


class EdgeList(unittest.TestCase):
    def test_empty_council_raises(self):
        with self.assertRaises(ScoringError):
            score_council(FakeCouncil([]), "m", ask=RecordingStub({}), criteria=("alpha",))

    def test_empty_bare_list_council_raises(self):
        with self.assertRaises(ScoringError):
            score_council([], "m", ask=RecordingStub({}), criteria=("alpha",))

    def test_council_of_exactly_one_seat(self):
        stub = RecordingStub({("seatA", "alpha"): 7})
        matrix = score_council(FakeCouncil(["seatA"]), "m", ask=stub, criteria=("alpha",))
        self.assertEqual(len(matrix), 1)

    def test_seat_appearing_twice_is_deduplicated_and_asked_once(self):
        stub = RecordingStub({("seatA", "alpha"): 7})
        matrix = score_council(
            FakeCouncil(["seatA", "seatA"]), "m", ask=stub, criteria=("alpha",)
        )
        self.assertEqual(len(matrix), 1)
        self.assertEqual(len(stub.calls), 1)

    def test_empty_criteria_raises(self):
        with self.assertRaises(ScoringError):
            score_council(FakeCouncil(["seatA"]), "m", ask=RecordingStub({}), criteria=())

    def test_ask_returning_none_is_a_failure(self):
        stub = RecordingStub({("seatA", "alpha"): None})
        matrix = score_council(FakeCouncil(["seatA"]), "m", ask=stub, criteria=("alpha",))
        self.assertEqual(len(matrix), 0)
        self.assertEqual(len(matrix.failures), 1)

    def test_ask_returning_a_dict_naming_a_different_criterion_is_a_failure(self):
        # "ask returning a score for a criterion nobody asked about": this
        # module never trusts a returned structure's own claim about which
        # criterion it covers. A dict answer of any shape is simply not a
        # valid scalar and is refused like any other malformed answer.
        stub = RecordingStub({("seatA", "alpha"): {"criterion": "beta", "score": 7}})
        matrix = score_council(FakeCouncil(["seatA"]), "m", ask=stub, criteria=("alpha",))
        self.assertEqual(len(matrix), 0)
        self.assertEqual(len(matrix.failures), 1)
        self.assertEqual(matrix.failures[0]["criterion"], "alpha")

    def test_seat_scoring_only_some_criteria(self):
        # seatA fails alpha, answers beta; seatB answers both.
        stub = RecordingStub(
            {
                ("seatA", "beta"): 6,
                ("seatB", "alpha"): 4,
                ("seatB", "beta"): 5,
            },
            raise_on={("seatA", "alpha")},
        )
        matrix = score_council(
            FakeCouncil(["seatA", "seatB"]), "m", ask=stub, criteria=("alpha", "beta")
        )
        self.assertEqual(len(matrix), 3)
        seatA_criteria = {c["criterion"] for c in matrix if c["seat"] == "seatA"}
        self.assertEqual(seatA_criteria, {"beta"})

    def test_council_with_non_string_seat_id_raises(self):
        with self.assertRaises(ScoringError):
            score_council(FakeCouncil([1, 2]), "m", ask=RecordingStub({}), criteria=("alpha",))

    def test_criteria_with_non_string_entry_raises(self):
        with self.assertRaises(ScoringError):
            score_council(
                FakeCouncil(["seatA"]), "m", ask=RecordingStub({}), criteria=(1,)
            )


if __name__ == "__main__":
    unittest.main(verbosity=2 if "-v" in sys.argv else 1)

#!/usr/bin/env python3
"""Tests for coe_loop.py (COE-06). Plain unittest, runnable directly:

    python3 scripts/test_coe_loop.py -v

Every test injects `ask`, `attack` and `repair` (and, where useful,
`nominate`) so the suite runs offline, deterministically, with no
subprocess, no sleep, and no dependency on the real seat registry file.
"""

import unittest

import coe_arbitrate
import coe_loop
import coe_nominate


CRITERIA = ["C1", "C2", "C3"]


class _SoloCouncil(object):
    """The smallest council a test needs: one seat, so every criterion has
    exactly one cell and arbitrate()'s minimum-not-average rule reduces to
    "whatever that one seat said."
    """

    seats = ("solo",)


def _nominate_solo(problem):
    return _SoloCouncil()


def _ask_constant(score):
    """An `ask` that answers every (seat, criterion) with the same score."""
    def _ask(seat_id, criterion, prompt):
        return score
    return _ask


def _attack_never_finds_anything(methodology, verdict, round_num):
    return []


def _repair_unchanged(methodology, accepted_exploits, round_num):
    return methodology


class TerminationRequiresTwoCleanRounds(unittest.TestCase):
    """Rule 1: one clean round does not pass; two consecutive rounds do.
    Both directions are asserted so a change to the threshold constant is
    caught by both a behavioural test and a direct assertion on it.
    """

    def test_constant_is_two_not_one(self):
        # If someone changes CONSECUTIVE_CLEAN_ROUNDS_REQUIRED to 1, this
        # assertion fails directly, independent of the behavioural test
        # below (which would also flip from ABANDONED to PASS).
        self.assertEqual(coe_loop.CONSECUTIVE_CLEAN_ROUNDS_REQUIRED, 2)

    def test_one_clean_round_does_not_pass(self):
        result = coe_loop.run_loop(
            {"id": "p1"},
            "methodology v1",
            criteria=CRITERIA,
            ask=_ask_constant(10),
            attack=_attack_never_finds_anything,
            repair=_repair_unchanged,
            max_rounds=1,
            nominate=_nominate_solo,
        )
        # A single round of all-10s is unanimous, so coe_arbitrate forces
        # EXTRA_ROUND_REQUIRED rather than PASS on round 1 anyway; either
        # way, one round can never satisfy CONSECUTIVE_CLEAN_ROUNDS_REQUIRED
        # = 2, so the loop must abandon at max_rounds=1, never pass.
        self.assertEqual(result.outcome, coe_loop.OUTCOME_ABANDONED)
        self.assertEqual(len(result.rounds), 1)

    def test_two_consecutive_clean_rounds_pass(self):
        # Use a score of 9 (not 10) so this is an ordinary clean round, not
        # the A5 unanimous-10 special case covered by its own test below.
        result = coe_loop.run_loop(
            {"id": "p1"},
            "methodology v1",
            criteria=CRITERIA,
            ask=_ask_constant(9),
            attack=_attack_never_finds_anything,
            repair=_repair_unchanged,
            max_rounds=2,
            nominate=_nominate_solo,
        )
        self.assertEqual(result.outcome, coe_loop.OUTCOME_PASS)
        self.assertEqual(len(result.rounds), 2)
        self.assertTrue(result.rounds[0]["clean"])
        self.assertTrue(result.rounds[1]["clean"])
        self.assertEqual(result.final_score, 9)


class MalformedFindingsNeverMoveAScore(unittest.TestCase):
    """Rule 2: a red finding that is not a concrete exploit does not lower
    any score, and is rejected rather than counted.
    """

    def test_vague_finding_is_rejected_and_score_unaffected(self):
        def _attack_vague(methodology, verdict, round_num):
            # A plain string: a doubt or a concern, not a finding. This is
            # the exact shape the brief calls out: "a vague finding must
            # not move a number."
            return ["I have concerns about C2, it feels risky"]

        result = coe_loop.run_loop(
            {"id": "p1"},
            "methodology v1",
            criteria=CRITERIA,
            ask=_ask_constant(9),
            attack=_attack_vague,
            repair=_repair_unchanged,
            max_rounds=2,
            nominate=_nominate_solo,
        )
        self.assertEqual(result.outcome, coe_loop.OUTCOME_PASS)
        for round_record in result.rounds:
            self.assertEqual(round_record["exploits_accepted"], [])
            self.assertEqual(len(round_record["exploits_rejected"]), 1)
            for criterion in CRITERIA:
                self.assertEqual(round_record["resolved_per_criterion"][criterion], 9)
        self.assertEqual(result.exploits_accepted, ())

    def test_exploit_missing_required_fields_is_rejected(self):
        def _attack_incomplete(methodology, verdict, round_num):
            # Has a criterion and a basis, but no control_says and no
            # demonstrated_score: still not a concrete exploit.
            return [{"criterion": "C2", "input": "malformed json"}]

        result = coe_loop.run_loop(
            {"id": "p1"},
            "methodology v1",
            criteria=CRITERIA,
            ask=_ask_constant(9),
            attack=_attack_incomplete,
            repair=_repair_unchanged,
            max_rounds=2,
            nominate=_nominate_solo,
        )
        self.assertEqual(result.outcome, coe_loop.OUTCOME_PASS)
        self.assertEqual(result.exploits_accepted, ())

    def test_exploit_targeting_unknown_criterion_is_rejected(self):
        def _attack_unknown_criterion(methodology, verdict, round_num):
            return [{
                "criterion": "C99",
                "input": "some crafted input",
                "control_says": "reports success",
                "demonstrated_score": 2,
            }]

        result = coe_loop.run_loop(
            {"id": "p1"},
            "methodology v1",
            criteria=CRITERIA,
            ask=_ask_constant(9),
            attack=_attack_unknown_criterion,
            repair=_repair_unchanged,
            max_rounds=2,
            nominate=_nominate_solo,
        )
        self.assertEqual(result.outcome, coe_loop.OUTCOME_PASS)
        self.assertEqual(result.exploits_accepted, ())
        self.assertEqual(len(result.rounds[0]["exploits_rejected"]), 1)


class ValidExploitsLowerExactlyOneCriterion(unittest.TestCase):
    """Rule 3: a valid exploit lowers exactly the criterion it targets, to
    the value red demonstrated, and the history records it beside the new
    score.
    """

    def test_valid_exploit_lowers_only_its_own_criterion(self):
        def _attack_once(methodology, verdict, round_num):
            if round_num == 1:
                return [{
                    "criterion": "C2",
                    "sequence": "restart the worker mid-write, twice",
                    "control_says": "the lock is held, refusing the second writer",
                    "demonstrated_score": 3,
                }]
            return []

        # Green withdraws after round 1 so the test only needs to inspect
        # that one round's resolved scores; termination behaviour is
        # covered by its own tests.
        def _repair_withdraw(methodology, accepted_exploits, round_num):
            return None

        result = coe_loop.run_loop(
            {"id": "p1"},
            "methodology v1",
            criteria=CRITERIA,
            ask=_ask_constant(10),
            attack=_attack_once,
            repair=_repair_withdraw,
            max_rounds=3,
            nominate=_nominate_solo,
        )
        self.assertEqual(result.outcome, coe_loop.OUTCOME_REJECTED)
        round1 = result.rounds[0]
        self.assertEqual(round1["resolved_per_criterion"]["C2"], 3)
        self.assertEqual(round1["resolved_per_criterion"]["C1"], 10)
        self.assertEqual(round1["resolved_per_criterion"]["C3"], 10)
        self.assertFalse(round1["clean"])
        self.assertEqual(len(round1["exploits_accepted"]), 1)
        accepted = round1["exploits_accepted"][0]
        self.assertEqual(accepted["criterion"], "C2")
        self.assertEqual(accepted["demonstrated_score"], 3)
        self.assertEqual(accepted["new_score"], 3)
        self.assertEqual(len(result.exploits_accepted), 1)


class CeilingAfterThreeStuckRounds(unittest.TestCase):
    """Rule 4: three rounds with a criterion stuck below 9 yields CEILING,
    and PASS is unreachable. Asserted on the outcome value, never a message
    string.
    """

    def test_stuck_criterion_ceilings_at_three_rounds(self):
        def _attack_c2_forever(methodology, verdict, round_num):
            return [{
                "criterion": "C2",
                "incentive": "an operator paid per merge, not per correct merge",
                "control_says": "the merge succeeded",
                "demonstrated_score": 5,
            }]

        result = coe_loop.run_loop(
            {"id": "p1"},
            "methodology v1",
            criteria=CRITERIA,
            ask=_ask_constant(10),
            attack=_attack_c2_forever,
            repair=_repair_unchanged,
            max_rounds=6,
            nominate=_nominate_solo,
        )
        self.assertEqual(result.outcome, coe_loop.OUTCOME_CEILING)
        self.assertNotEqual(result.outcome, coe_loop.OUTCOME_PASS)
        self.assertEqual(result.capping_criterion, "C2")
        self.assertEqual(result.final_score, 5)
        self.assertEqual(len(result.rounds), 3)


class StandardCannotChangeMidRound(unittest.TestCase):
    """Rule 5: changing `criteria` between rounds raises."""

    def test_criteria_mutated_by_repair_raises(self):
        criteria = list(CRITERIA)

        def _attack_c2(methodology, verdict, round_num):
            return [{
                "criterion": "C2",
                "input": "an oversized payload",
                "control_says": "processed normally",
                "demonstrated_score": 4,
            }]

        def _repair_that_grows_the_standard(methodology, accepted_exploits, round_num):
            # Exactly the incident this rule exists for: the rubric grew
            # from ten criteria to twelve mid-process.
            criteria.append("C4")
            return methodology

        with self.assertRaises(coe_loop.LoopError):
            coe_loop.run_loop(
                {"id": "p1"},
                "methodology v1",
                criteria=criteria,
                ask=_ask_constant(10),
                attack=_attack_c2,
                repair=_repair_that_grows_the_standard,
                max_rounds=4,
                nominate=_nominate_solo,
            )


class UnanimityForcesAnExtraRound(unittest.TestCase):
    """Rule 6: EXTRA_ROUND_REQUIRED from arbitrate causes another round,
    never a pass. This also names the bad state a fully cooperative,
    always-agreeing stub set would otherwise pass: see the module
    docstring's own section on this.
    """

    def test_all_tens_stub_never_passes(self):
        result = coe_loop.run_loop(
            {"id": "p1"},
            "methodology v1",
            criteria=CRITERIA,
            ask=_ask_constant(10),
            attack=_attack_never_finds_anything,
            repair=_repair_unchanged,
            max_rounds=4,
            nominate=_nominate_solo,
        )
        # Every round is unanimous 10, so every round is forced "not clean"
        # by A5, no matter how many rounds run: two consecutive clean
        # rounds can never accumulate, and the loop must abandon.
        self.assertEqual(result.outcome, coe_loop.OUTCOME_ABANDONED)
        self.assertNotEqual(result.outcome, coe_loop.OUTCOME_PASS)
        for round_record in result.rounds:
            self.assertFalse(round_record["clean"])
            self.assertTrue(round_record["forced_not_clean"])
            self.assertEqual(round_record["base_outcome"], coe_arbitrate.OUTCOME_EXTRA_ROUND_REQUIRED)


class MaxRoundsIsARealBound(unittest.TestCase):
    """Rule 7: max_rounds is a real bound. Hitting it is ABANDONED with the
    reason, never a pass, and never an infinite loop.
    """

    def test_repair_that_never_improves_abandons_before_ceiling(self):
        # A score of 7 (below 9, but never NO-DATA/MISSING) with max_rounds
        # below CEILING_ROUNDS so the run exhausts its budget before the
        # stuck-criterion counter could ever reach a ceiling: this isolates
        # ABANDONED-by-exhaustion from CEILING-by-a-stuck-criterion.
        result = coe_loop.run_loop(
            {"id": "p1"},
            "methodology v1",
            criteria=CRITERIA,
            ask=_ask_constant(7),
            attack=_attack_never_finds_anything,
            repair=_repair_unchanged,
            max_rounds=2,
            nominate=_nominate_solo,
        )
        self.assertEqual(result.outcome, coe_loop.OUTCOME_ABANDONED)
        self.assertEqual(len(result.rounds), 2)
        self.assertLess(coe_loop.CEILING_ROUNDS, 4)  # sanity on the fixture's own assumption

    def test_zero_max_rounds_is_abandoned_with_no_rounds(self):
        result = coe_loop.run_loop(
            {"id": "p1"},
            "methodology v1",
            criteria=CRITERIA,
            ask=_ask_constant(10),
            attack=_attack_never_finds_anything,
            repair=_repair_unchanged,
            max_rounds=0,
            nominate=_nominate_solo,
        )
        self.assertEqual(result.outcome, coe_loop.OUTCOME_ABANDONED)
        self.assertEqual(result.rounds, ())


class GreenCanWithdraw(unittest.TestCase):
    """Rule 8: green withdrawing a methodology ends the loop as REJECTED,
    cleanly, with the history intact.
    """

    def test_withdrawal_ends_as_rejected_with_history(self):
        def _attack_c1(methodology, verdict, round_num):
            return [{
                "criterion": "C1",
                "input": "a corrupt store read as free",
                "control_says": "the lease is available",
                "demonstrated_score": 2,
            }]

        def _repair_withdraws(methodology, accepted_exploits, round_num):
            return None

        result = coe_loop.run_loop(
            {"id": "p1"},
            "methodology v1",
            criteria=CRITERIA,
            ask=_ask_constant(10),
            attack=_attack_c1,
            repair=_repair_withdraws,
            max_rounds=5,
            nominate=_nominate_solo,
        )
        self.assertEqual(result.outcome, coe_loop.OUTCOME_REJECTED)
        self.assertEqual(len(result.rounds), 1)
        self.assertEqual(result.rounds[0]["resolved_per_criterion"]["C1"], 2)


class NoOpRepairIsNotSpeciallyDetected(unittest.TestCase):
    """The 'interesting' edge: a repair that changes nothing at all is not
    special-cased. It is left to the ordinary stuck-criterion mechanism,
    which still reaches CEILING honestly.
    """

    def test_no_op_repair_reaches_ceiling(self):
        def _attack_c3(methodology, verdict, round_num):
            return [{
                "criterion": "C3",
                "sequence": "resubmit the same unit under the same identity",
                "control_says": "already done, skipping",
                "demonstrated_score": 6,
            }]

        result = coe_loop.run_loop(
            {"id": "p1"},
            "the same methodology, never repaired",
            criteria=CRITERIA,
            ask=_ask_constant(10),
            attack=_attack_c3,
            repair=_repair_unchanged,  # returns the identical text every time
            max_rounds=6,
            nominate=_nominate_solo,
        )
        self.assertEqual(result.outcome, coe_loop.OUTCOME_CEILING)
        self.assertEqual(result.capping_criterion, "C3")
        self.assertEqual(len(result.rounds), 3)
        # Every round scored the byte-identical methodology string.
        self.assertEqual(
            {r["methodology"] for r in result.rounds},
            {"the same methodology, never repaired"},
        )


class MachineryFailuresRaiseLoopError(unittest.TestCase):
    """attack/repair raising, and other machinery-shaped breaks, raise
    LoopError rather than being read as any of the four legitimate
    outcomes.
    """

    def test_attack_raising_raises_loop_error(self):
        def _attack_raises(methodology, verdict, round_num):
            raise RuntimeError("red team's own tooling crashed")

        with self.assertRaises(coe_loop.LoopError):
            coe_loop.run_loop(
                {"id": "p1"},
                "methodology v1",
                criteria=CRITERIA,
                ask=_ask_constant(9),
                attack=_attack_raises,
                repair=_repair_unchanged,
                max_rounds=2,
                nominate=_nominate_solo,
            )

    def test_repair_raising_raises_loop_error(self):
        def _attack_c1(methodology, verdict, round_num):
            return [{
                "criterion": "C1",
                "input": "x",
                "control_says": "y",
                "demonstrated_score": 3,
            }]

        def _repair_raises(methodology, accepted_exploits, round_num):
            raise RuntimeError("green team's own tooling crashed")

        with self.assertRaises(coe_loop.LoopError):
            coe_loop.run_loop(
                {"id": "p1"},
                "methodology v1",
                criteria=CRITERIA,
                ask=_ask_constant(10),
                attack=_attack_c1,
                repair=_repair_raises,
                max_rounds=2,
                nominate=_nominate_solo,
            )

    def test_attack_returning_a_bare_string_raises_loop_error(self):
        def _attack_bare_string(methodology, verdict, round_num):
            return "no exploits this round"  # not a list: a protocol bug

        with self.assertRaises(coe_loop.LoopError):
            coe_loop.run_loop(
                {"id": "p1"},
                "methodology v1",
                criteria=CRITERIA,
                ask=_ask_constant(9),
                attack=_attack_bare_string,
                repair=_repair_unchanged,
                max_rounds=2,
                nominate=_nominate_solo,
            )

    def test_repair_returning_non_string_non_none_raises_loop_error(self):
        def _attack_c1(methodology, verdict, round_num):
            return [{
                "criterion": "C1",
                "input": "x",
                "control_says": "y",
                "demonstrated_score": 3,
            }]

        def _repair_returns_garbage(methodology, accepted_exploits, round_num):
            return 42

        with self.assertRaises(coe_loop.LoopError):
            coe_loop.run_loop(
                {"id": "p1"},
                "methodology v1",
                criteria=CRITERIA,
                ask=_ask_constant(10),
                attack=_attack_c1,
                repair=_repair_returns_garbage,
                max_rounds=2,
                nominate=_nominate_solo,
            )


class EmptyCriteriaRaises(unittest.TestCase):
    """An empty criteria list is refused before anyone is nominated."""

    def test_empty_criteria_raises_loop_error(self):
        with self.assertRaises(coe_loop.LoopError):
            coe_loop.run_loop(
                {"id": "p1"},
                "methodology v1",
                criteria=[],
                ask=_ask_constant(9),
                attack=_attack_never_finds_anything,
                repair=_repair_unchanged,
                max_rounds=2,
            )


class NominationRefusalIsAbandoned(unittest.TestCase):
    """A nomination that refuses (a high-risk problem with only one model
    family, per coe_nominate's own D4) is folded into ABANDONED at zero
    rounds, not raised as LoopError: see the module docstring's
    CONTINGENCY section for why this one case is different.
    """

    def test_nomination_refusal_abandons_at_zero_rounds(self):
        def _nominate_refuses(problem):
            raise coe_nominate.NominationRefused(
                "high risk problem could not reach two distinct model "
                "families", refusals=[]
            )

        result = coe_loop.run_loop(
            {"id": "p1", "risk_class": "high"},
            "methodology v1",
            criteria=CRITERIA,
            ask=_ask_constant(9),
            attack=_attack_never_finds_anything,
            repair=_repair_unchanged,
            max_rounds=3,
            nominate=_nominate_refuses,
        )
        self.assertEqual(result.outcome, coe_loop.OUTCOME_ABANDONED)
        self.assertEqual(result.rounds, ())
        self.assertIn("high risk", result.reason)

    def test_empty_council_from_a_stub_raises_loop_error(self):
        class _EmptyCouncil(object):
            seats = ()

        with self.assertRaises(coe_loop.LoopError):
            coe_loop.run_loop(
                {"id": "p1"},
                "methodology v1",
                criteria=CRITERIA,
                ask=_ask_constant(9),
                attack=_attack_never_finds_anything,
                repair=_repair_unchanged,
                max_rounds=2,
                nominate=lambda problem: _EmptyCouncil(),
            )


class InvalidMaxRoundsRaises(unittest.TestCase):

    def test_negative_max_rounds_raises(self):
        with self.assertRaises(coe_loop.LoopError):
            coe_loop.run_loop(
                {"id": "p1"},
                "methodology v1",
                criteria=CRITERIA,
                ask=_ask_constant(9),
                attack=_attack_never_finds_anything,
                repair=_repair_unchanged,
                max_rounds=-1,
            )

    def test_non_int_max_rounds_raises(self):
        with self.assertRaises(coe_loop.LoopError):
            coe_loop.run_loop(
                {"id": "p1"},
                "methodology v1",
                criteria=CRITERIA,
                ask=_ask_constant(9),
                attack=_attack_never_finds_anything,
                repair=_repair_unchanged,
                max_rounds=2.5,
            )


class AskFailurePropagatesThroughComposition(unittest.TestCase):
    """An `ask` that raises mid-round is already handled by coe_score (a
    failure cell) and coe_arbitrate (a MISSING criterion, capped below
    PASS): this test proves that composed behaviour still holds through
    run_loop, without this module reimplementing any of it.
    """

    def test_ask_failure_prevents_pass(self):
        def _ask_fails_on_c2(seat_id, criterion, prompt):
            if criterion == "C2":
                raise RuntimeError("seat's own transport timed out")
            return 10

        result = coe_loop.run_loop(
            {"id": "p1"},
            "methodology v1",
            criteria=CRITERIA,
            ask=_ask_fails_on_c2,
            attack=_attack_never_finds_anything,
            repair=_repair_unchanged,
            max_rounds=3,
            nominate=_nominate_solo,
        )
        self.assertNotEqual(result.outcome, coe_loop.OUTCOME_PASS)
        self.assertEqual(result.outcome, coe_loop.OUTCOME_CEILING)
        self.assertEqual(result.capping_criterion, "C2")


if __name__ == "__main__":
    unittest.main()

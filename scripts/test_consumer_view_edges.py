#!/usr/bin/env python3
"""UI1.a: the consumer card's edges that the frozen contract (scripts/test_consumer_view_contract.py) never reaches.

The frozen class feeds one plain state with short text. Each case here isolates one guard of
proof_card.workflow_card that a mutation could delete with the frozen class still green: a recipe inside a field
the card does read, a verdict short of PASS, a machine word from stage_log.current_run, a missing fact, a long
record, and the line that keeps acceptance with the person.

Run: python3 -B scripts/test_consumer_view_edges.py   (and /usr/bin/python3)
"""
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import proof_card as PC  # noqa: E402


def card(**kw):
    state = dict(outcome="Correct the report", state="WAITING", phase="verification",
                 summary="Waiting for enough space", next_action="Resume when space is available",
                 owner_action=None, verdict="NO-DATA")
    state.update(kw)
    return PC.workflow_card(state)


class RecipesAreWithheld(unittest.TestCase):
    def test_a_script_path_in_the_next_action_is_withheld(self):
        got = card(next_action="run scripts/cut.py now")
        self.assertNotIn("scripts/", got)
        self.assertIn(PC.WITHHELD, got)

    def test_a_flag_in_the_summary_is_withheld(self):
        got = card(summary="retry with --cold")
        self.assertNotIn("--cold", got)
        self.assertIn(PC.WITHHELD, got)

    def test_a_single_dash_flag_is_withheld(self):
        self.assertNotIn("-m", card(next_action="run it with -m unittest").split("Next:")[1])

    def test_a_recipe_in_the_owner_action_is_withheld(self):
        got = card(owner_action=dict(action="type python3 approve", scope="version 1.2.0", reason="it ships"))
        self.assertNotIn("python3", got)

    def test_plain_words_with_a_hyphen_are_kept(self):
        self.assertIn("Re-run the well-known report", card(next_action="Re-run the well-known report"))


class ProofIsNeverOverstated(unittest.TestCase):
    def test_pass_says_proven_and_leaves_acceptance_to_the_person(self):
        got = card(state="DONE", verdict="PASS")
        self.assertIn("checked and proven", got)
        self.assertIn("your decision", got)

    def test_unit_complete_is_not_proven(self):
        got = card(state="DONE", verdict="UNIT-COMPLETE")
        self.assertNotIn("proven", got)
        self.assertIn("not verified", got)

    def test_an_unproven_finish_does_not_ask_for_acceptance(self):
        self.assertNotIn("your decision", card(state="DONE", verdict="NO-DATA"))

    def test_fail_says_a_check_failed(self):
        got = card(state="DONE", verdict="FAIL")
        self.assertIn("a check failed", got)
        self.assertNotIn("proven", got)

    def test_an_unknown_verdict_is_no_data(self):
        self.assertIn("not verified yet (NO-DATA)", card(state="DONE", verdict="GREEN"))

    def test_an_unknown_state_is_not_finished(self):
        got = card(state="SHIPPED")
        self.assertIn("Unknown (NO-DATA)", got)
        self.assertNotIn("Finished", got)


class MachineWordsArePlain(unittest.TestCase):
    def test_every_current_run_next_action_reads_in_plain_words(self):
        for token in ("none", "wait_lease", "renew_monitor", "escalate", "ask_owner"):
            got = card(next_action=token).split("Next:")[1]
            self.assertNotIn(token, got.split("\n")[0], token)

    def test_a_wake_on_phase_names_the_wait(self):
        got = card(next_action="wake_on:capacity")
        self.assertIn("Next: Continue once the capacity wait clears", got)
        self.assertNotIn("wake_on", got)


class MissingFactsSayNoData(unittest.TestCase):
    def test_no_state_at_all(self):
        got = PC.workflow_card(None)
        self.assertIn("Outcome: NO-DATA", got)
        self.assertIn("Next: NO-DATA", got)

    def test_an_owner_action_without_scope_or_reason(self):
        got = card(owner_action=dict(action="Approve publication"))
        self.assertIn("scope not recorded", got)
        self.assertIn("no reason recorded", got)

    def test_an_owner_action_given_as_words(self):
        got = card(owner_action="Approve publication")
        self.assertIn("Needs you: Approve publication", got)
        self.assertIn("no reason recorded", got)


class NothingIsHidden(unittest.TestCase):
    def test_a_problem_and_a_cost_bound_are_shown(self):
        got = card(error="The disk is full", cost="at most 3 dollars")
        self.assertIn("Problem: The disk is full", got)
        self.assertIn("Cost: at most 3 dollars", got)

    def test_completed_work_is_shown(self):
        self.assertIn("Done so far: Wrote the fix; Ran the report", card(completed=["Wrote the fix", "Ran the report"]))

    def test_a_long_record_stays_inside_the_card(self):
        long = "word " * 400
        got = card(outcome=long, summary=long, next_action=long, error=long, cost=long, completed=[long],
                   owner_action=dict(action=long, scope=long, reason=long), state="DONE", verdict="PASS")
        self.assertLessEqual(len(got), 1200)
        self.assertIn("Why:", got)


if __name__ == "__main__":
    unittest.main()

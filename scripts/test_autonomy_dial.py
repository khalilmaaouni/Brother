#!/usr/bin/env python3
"""Tests for the autonomy dial (docs/plan/AUTONOMY-POLICY-V1.md wired).
Every class in section 2's table proven to fire, the safety floor proven to
hold at the most permissive dial position, and the dial proven to change
the decision for one fixed action. No em or en dashes."""

import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from autonomy_dial import (classify, gate, dial_level, effective_class,
                           ORDER, ACTIONS, A3_FLAGS, DEFAULT_DIAL)

A0_ACTION = {
    "single_file_or_named_target": True,
    "contract_change": "none",
    "crosses_boundary": False,
    "reversible_under_hour": True,
    "blast_radius": "small_and_named",
}

A1_ACTION = {
    "single_file_or_named_target": False,
    "interpretation_ambiguous": False,
    "multi_file_or_shared_module": False,
    "blast_radius": "small_and_named",
}

A2_ACTION = {
    "interpretation_ambiguous": True,
    "blast_radius": "large_or_unnamed",
}


class TestClassify(unittest.TestCase):

    def test_a0_micro_reversible_localized(self):
        self.assertEqual(classify(A0_ACTION), "A0")

    def test_a1_bounded_low_risk_grounded(self):
        self.assertEqual(classify(A1_ACTION), "A1")

    def test_a2_moderate_risk_or_ambiguity(self):
        self.assertEqual(classify(A2_ACTION), "A2")

    def test_unnamed_action_defaults_to_a2_never_a0(self):
        # An action nobody described anything about must not read as the
        # most permissive class; the cautious default is A2.
        self.assertEqual(classify({}), "A2")
        self.assertEqual(classify(None), "A2")

    def test_every_a3_flag_alone_forces_a3(self):
        for flag in A3_FLAGS:
            with self.subTest(flag=flag):
                action = dict(A0_ACTION)
                action[flag] = True
                self.assertEqual(classify(action), "A3")

    def test_a3_wins_even_over_a0_shaped_action(self):
        # Adversarial example 2 from the policy: a production migration
        # that "looks" like one small bounded change is still A3.
        action = dict(A0_ACTION)
        action["production_deploy"] = True
        action["destructive_data_change"] = True
        self.assertEqual(classify(action), "A3")


class TestDialReading(unittest.TestCase):

    def setUp(self):
        self._prior = os.environ.pop("BROTHER_AUTONOMY_DIAL", None)

    def tearDown(self):
        if self._prior is None:
            os.environ.pop("BROTHER_AUTONOMY_DIAL", None)
        else:
            os.environ["BROTHER_AUTONOMY_DIAL"] = self._prior

    def test_unset_reads_as_default(self):
        self.assertEqual(dial_level(), DEFAULT_DIAL)

    def test_set_value_is_read_back_uppercased(self):
        os.environ["BROTHER_AUTONOMY_DIAL"] = "a2"
        self.assertEqual(dial_level(), "A2")

    def test_garbage_value_falls_back_to_default_not_most_permissive(self):
        os.environ["BROTHER_AUTONOMY_DIAL"] = "banana"
        self.assertEqual(dial_level(), DEFAULT_DIAL)


class TestEffectiveClassIsAlwaysTheStricter(unittest.TestCase):

    def test_permissive_dial_does_not_loosen_a_stricter_action(self):
        self.assertEqual(effective_class("A2", "A0"), "A2")

    def test_strict_dial_adds_ceremony_to_a_looser_action(self):
        self.assertEqual(effective_class("A0", "A2"), "A2")

    def test_matching_class_and_dial_is_a_no_op(self):
        self.assertEqual(effective_class("A1", "A1"), "A1")


class TestGateChangesWithTheDial(unittest.TestCase):
    """DONE-CHECK 2: same input (A0_ACTION), two different dial settings,
    two different outcomes."""

    def test_same_action_two_dial_settings_two_outcomes(self):
        permissive = gate(A0_ACTION, dial="A0")
        strict = gate(A0_ACTION, dial="A2")
        self.assertEqual(permissive, "execute_then_check")
        self.assertEqual(strict, "ask_one_blocking_question")
        self.assertNotEqual(permissive, strict)


class TestSafetyFloorNeverAdjustable(unittest.TestCase):
    """DONE-CHECK 3: the most permissive dial position still refuses a
    credential action and a destructive action."""

    def test_most_permissive_dial_still_refuses_credentials(self):
        for dial in ORDER:
            with self.subTest(dial=dial):
                decision = gate({"credentials": True}, dial=dial)
                self.assertEqual(decision, "refuse_until_approved")

    def test_most_permissive_dial_still_refuses_destructive_change(self):
        for dial in ORDER:
            with self.subTest(dial=dial):
                decision = gate({"destructive_data_change": True}, dial=dial)
                self.assertEqual(decision, "refuse_until_approved")

    def test_a0_dial_cannot_be_paired_with_a3_action_to_execute(self):
        # A0 is the most permissive dial value in ORDER. Even there, an
        # action naming any A3 flag must still refuse.
        for flag in A3_FLAGS:
            with self.subTest(flag=flag):
                action = dict(A0_ACTION)
                action[flag] = True
                self.assertEqual(gate(action, dial="A0"),
                                 "refuse_until_approved")


class TestActionsCoverEveryClass(unittest.TestCase):

    def test_actions_map_names_every_order_value(self):
        self.assertEqual(set(ACTIONS), set(ORDER))


if __name__ == "__main__":
    unittest.main()

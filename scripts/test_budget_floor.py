#!/usr/bin/env python3
"""Plain unittest for scripts/budget_floor.py. Run directly:
python3 scripts/test_budget_floor.py -v

Each test names the bad state a weaker check would also let through, per the
worker contract's rule 5: a test that cannot fail is not evidence.
"""
import unittest

import budget_floor as bf


REASONING_PROFILE = {"context_tokens": 1_000_000, "reasoning": True}
PLAIN_PROFILE = {"context_tokens": 1_000_000, "reasoning": False}
SMALL_PROFILE = {"context_tokens": 1000, "reasoning": False}


class EstimateInputTokens(unittest.TestCase):
    def test_zero_length_input_returns_zero_not_an_error(self):
        # A bad implementation might treat 0 as falsy/missing and raise, or
        # round it up to 1. Neither is correct: empty input is a real,
        # valid call shape.
        self.assertEqual(bf.estimate_input_tokens(0), 0)

    def test_rounds_up_so_a_partial_token_is_never_dropped(self):
        # 5 chars at 4 chars/token is 1.25 tokens; a floor() here would
        # silently under-count by a token on every non-multiple-of-4 input.
        self.assertEqual(bf.estimate_input_tokens(5), 2)

    def test_negative_char_count_raises(self):
        with self.assertRaises(bf.InvalidBudgetInput):
            bf.estimate_input_tokens(-1)

    def test_non_int_char_count_raises(self):
        with self.assertRaises(bf.InvalidBudgetInput):
            bf.estimate_input_tokens(3.5)

    def test_bool_char_count_raises(self):
        # bool is an int subclass; True must not silently become 1.
        with self.assertRaises(bf.InvalidBudgetInput):
            bf.estimate_input_tokens(True)


class FloorUnknownModel(unittest.TestCase):
    def test_none_profile_refuses_rather_than_defaulting(self):
        with self.assertRaises(bf.UnknownModel):
            bf.floor("some/unlisted-model", None, 10, 50, 500)

    def test_non_dict_profile_refuses(self):
        with self.assertRaises(bf.UnknownModel):
            bf.floor("m", "not-a-dict", 10, 50, 500)

    def test_profile_missing_context_tokens_refuses(self):
        with self.assertRaises(bf.UnknownModel):
            bf.floor("m", {"reasoning": True}, 10, 50, 500)

    def test_profile_missing_reasoning_refuses(self):
        with self.assertRaises(bf.UnknownModel):
            bf.floor("m", {"context_tokens": 1000}, 10, 50, 500)


class FloorInvalidInputs(unittest.TestCase):
    def test_negative_input_tokens_raises(self):
        with self.assertRaises(bf.InvalidBudgetInput):
            bf.floor("m", PLAIN_PROFILE, -1, 50, 500)

    def test_non_numeric_input_tokens_raises(self):
        with self.assertRaises(bf.InvalidBudgetInput):
            bf.floor("m", PLAIN_PROFILE, "100", 50, 500)

    def test_zero_expected_answer_tokens_raises(self):
        # A zero-token answer floor is not a completion call.
        with self.assertRaises(bf.InvalidBudgetInput):
            bf.floor("m", PLAIN_PROFILE, 10, 0, 500)

    def test_negative_expected_answer_tokens_raises(self):
        with self.assertRaises(bf.InvalidBudgetInput):
            bf.floor("m", PLAIN_PROFILE, 10, -5, 500)

    def test_zero_attempt_ceiling_raises(self):
        with self.assertRaises(bf.InvalidBudgetInput):
            bf.floor("m", PLAIN_PROFILE, 10, 50, 0)

    def test_bool_expected_answer_tokens_raises(self):
        with self.assertRaises(bf.InvalidBudgetInput):
            bf.floor("m", PLAIN_PROFILE, 10, True, 500)

    def test_profile_context_tokens_wrong_type_raises(self):
        with self.assertRaises(bf.InvalidBudgetInput):
            bf.floor("m", {"context_tokens": "big", "reasoning": False}, 10, 50, 500)

    def test_profile_reasoning_wrong_type_raises(self):
        with self.assertRaises(bf.InvalidBudgetInput):
            bf.floor("m", {"context_tokens": 1000, "reasoning": "yes"}, 10, 50, 500)

    def test_attempt_ceiling_too_small_to_try_once_raises(self):
        # answer_floor 50 needs at least 50 (plain model); a ceiling of 10
        # cannot fund even a single try and must refuse, not silently shrink.
        with self.assertRaises(bf.InvalidBudgetInput):
            bf.floor("m", PLAIN_PROFILE, 10, 50, 10)


class FloorZeroLengthInput(unittest.TestCase):
    def test_zero_input_tokens_is_a_valid_call(self):
        result = bf.floor("m", PLAIN_PROFILE, 0, 50, 500)
        self.assertEqual(result["input_floor"], 0)
        self.assertEqual(result["first_try_max"], 50)


class FloorHugeInput(unittest.TestCase):
    def test_huge_input_within_context_succeeds(self):
        huge_profile = {"context_tokens": 10_000_000, "reasoning": False}
        result = bf.floor("m", huge_profile, 9_999_000, 50, 500)
        self.assertEqual(result["input_floor"], 9_999_000)

    def test_huge_input_over_context_raises_capacity_not_price(self):
        # This must be an InputCapacityExceeded refusal, never a computed
        # (however large) first_try_max: the call cannot fit at any price.
        with self.assertRaises(bf.InputCapacityExceeded):
            bf.floor("m", SMALL_PROFILE, 10_000_000, 50, 500)

    def test_input_plus_answer_floor_exceeding_context_raises(self):
        # Input alone fits; input plus the reserved answer floor does not.
        tight_profile = {"context_tokens": 100, "reasoning": False}
        with self.assertRaises(bf.InputCapacityExceeded):
            bf.floor("m", tight_profile, 90, 50, 500)


class FloorReasoningOverhead(unittest.TestCase):
    def test_reasoning_model_doubles_the_answer_floor(self):
        # Names the bad state a weaker check would also pass: a floor() that
        # forgot the reasoning reserve would still return SOME first_try_max
        # (just too small), so this asserts the exact doubled value, not
        # merely that a value was returned.
        result = bf.floor("m", REASONING_PROFILE, 10, 50, 500)
        self.assertEqual(result["reasoning_reserve"], 50)
        self.assertEqual(result["first_try_max"], 100)

    def test_plain_model_has_no_reasoning_reserve(self):
        result = bf.floor("m", PLAIN_PROFILE, 10, 50, 500)
        self.assertEqual(result["reasoning_reserve"], 0)
        self.assertEqual(result["first_try_max"], 50)

    def test_reasoning_and_plain_differ_for_identical_call_shape(self):
        # The same input/answer/ceiling triple must NOT produce the same
        # first_try_max across the two profiles; if it did, the reasoning
        # branch is dead code even though both calls return successfully.
        plain = bf.floor("m", PLAIN_PROFILE, 10, 50, 500)
        reasoning = bf.floor("m", REASONING_PROFILE, 10, 50, 500)
        self.assertNotEqual(plain["first_try_max"], reasoning["first_try_max"])


class FloorAttemptCeilingSeparateFromAnswerFloor(unittest.TestCase):
    def test_first_try_max_never_exceeds_the_attempt_ceiling(self):
        # answer_floor 400 needs 400, but the ceiling only allows 300 total
        # for the whole attempt: this must refuse (can't run at all) rather
        # than silently cap first_try_max at 300, a budget too small for the
        # try it was supposedly computed for.
        with self.assertRaises(bf.InvalidBudgetInput):
            bf.floor("m", PLAIN_PROFILE, 10, 400, 300)

    def test_first_try_max_equals_ceiling_when_they_match_exactly(self):
        result = bf.floor("m", PLAIN_PROFILE, 10, 300, 300)
        self.assertEqual(result["first_try_max"], 300)

    def test_one_large_payload_does_not_raise_the_floor_for_a_small_one(self):
        # The deciding property of ORCH-28: a huge earlier input must not
        # leak into a later, small call's floor. Since floor() takes no
        # remembered state, a fresh small call must compute a fresh small
        # result regardless of what a previous huge call computed.
        big = bf.floor("m", PLAIN_PROFILE, 900_000, 50, 500)
        small = bf.floor("m", PLAIN_PROFILE, 10, 50, 500)
        self.assertEqual(big["first_try_max"], small["first_try_max"])
        self.assertNotEqual(big["input_floor"], small["input_floor"])


class RetryBudget(unittest.TestCase):
    def test_doubles_the_previous_try(self):
        self.assertEqual(bf.retry_budget(100, 1000), 200)

    def test_caps_at_the_attempt_ceiling(self):
        self.assertEqual(bf.retry_budget(600, 1000), 1000)

    def test_never_identical_to_the_first_attempt(self):
        # If the previous try already sits at the ceiling, doubling and
        # capping would both collapse to the same number as before; that
        # must raise, never be returned as a "retry".
        with self.assertRaises(bf.BudgetExhausted):
            bf.retry_budget(1000, 1000)

    def test_over_ceiling_previous_value_raises(self):
        with self.assertRaises(bf.BudgetExhausted):
            bf.retry_budget(1500, 1000)

    def test_negative_previous_max_raises(self):
        with self.assertRaises(bf.InvalidBudgetInput):
            bf.retry_budget(-5, 1000)

    def test_zero_previous_max_raises(self):
        with self.assertRaises(bf.InvalidBudgetInput):
            bf.retry_budget(0, 1000)

    def test_non_numeric_ceiling_raises(self):
        with self.assertRaises(bf.InvalidBudgetInput):
            bf.retry_budget(100, "1000")


if __name__ == "__main__":
    unittest.main()

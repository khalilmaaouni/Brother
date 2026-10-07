"""What scripts/jev_tool_gate.py must keep true.

Every forbidden term used below is FAKE and made up for this test file.
No test here touches the network, spawns a process, or reads the real
repository: the content gate's term config is built in a temp folder and
patched in for the duration of each test, and the ask path is always an
injected fake.
"""
import json
import os
import sys
import tempfile
import time
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import coe_outside_gate as outside_gate  # noqa: E402
import jev_tool_gate as gate  # noqa: E402


#: A fake category and a fake term, chosen so no real client or team term
#: is ever written into this file.
FAKE_TERMS = {"fake-j1a-terms": ["zzzznotpresent"]}

RECORDS = {"order-1": {"balance": 100.0}}


class _GateCase(unittest.TestCase):
    """Puts a fake terms file in front of the content gate and shortens
    the ask deadline, so no test here depends on the operator's own home
    or waits seconds for a deliberately hung ask."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.terms_path = os.path.join(self._tmp.name, "terms.json")
        with open(self.terms_path, "w", encoding="utf-8") as handle:
            json.dump(FAKE_TERMS, handle)
        self._patch(outside_gate, "DEFAULT_TERMS_PATH", self.terms_path)
        self._patch(gate, "ASK_DEADLINE_S", 0.15)

    def _patch(self, target, name, value):
        patcher = mock.patch.object(target, name, value)
        patcher.start()
        self.addCleanup(patcher.stop)


class PrecheckRefusesBeforeAnyModel(_GateCase):

    def test_empty_call_is_blocked(self):
        result = gate.gate_tool_call({}, RECORDS, "policy text", mode="off")
        self.assertEqual(result["outcome"], "block")
        self.assertIs(result["consulted"], False)

    def test_call_naming_no_record_is_blocked(self):
        result = gate.gate_tool_call({"amount": 5.0}, RECORDS, "policy text", mode="off")
        self.assertEqual(result["outcome"], "block")

    def test_call_naming_an_absent_record_is_blocked(self):
        ok, reason = gate.precheck({"record": "order-9", "amount": 5.0}, RECORDS)
        self.assertFalse(ok)
        self.assertIn("absent", reason)

    def test_amount_given_as_text_is_blocked(self):
        ok, _ = gate.precheck({"record": "order-1", "amount": "5.0"}, RECORDS)
        self.assertFalse(ok)

    def test_negative_amount_is_blocked(self):
        ok, _ = gate.precheck({"record": "order-1", "amount": -1.0}, RECORDS)
        self.assertFalse(ok)

    def test_amount_over_the_balance_is_blocked(self):
        ok, _ = gate.precheck({"record": "order-1", "amount": 100.01}, RECORDS)
        self.assertFalse(ok)

    def test_amount_equal_to_balance_is_allowed(self):
        ok, reason = gate.precheck({"record": "order-1", "amount": 100.0}, RECORDS)
        self.assertTrue(ok, reason)


class GateVerdictUsesFixedThresholds(_GateCase):

    def test_probability_exactly_at_approve_at(self):
        self.assertEqual(gate.gate_verdict(0.9), "approve")

    def test_probability_exactly_at_block_at(self):
        self.assertEqual(gate.gate_verdict(0.1), "block")

    def test_probability_between_the_thresholds_is_review(self):
        self.assertEqual(gate.gate_verdict(0.5), "review")

    def test_equal_thresholds_are_review(self):
        self.assertEqual(gate.gate_verdict(0.5, approve_at=0.5, block_at=0.5), "review")

    def test_crossed_thresholds_are_review(self):
        self.assertEqual(gate.gate_verdict(0.95, approve_at=0.1, block_at=0.9), "review")

    def test_none_is_review(self):
        self.assertEqual(gate.gate_verdict(None), "review")

    def test_nan_is_review(self):
        self.assertEqual(gate.gate_verdict(float("nan")), "review")

    def test_out_of_range_is_review(self):
        self.assertEqual(gate.gate_verdict(1.5), "review")
        self.assertEqual(gate.gate_verdict(-0.5), "review")

    def test_text_is_review(self):
        self.assertEqual(gate.gate_verdict("0.95"), "review")


class GateToolCallModes(_GateCase):

    def test_off_mode_approves_without_consulting(self):
        calls = []
        result = gate.gate_tool_call(
            {"record": "order-1", "amount": 5.0}, RECORDS, "policy text",
            ask=lambda state: calls.append(state) or 0.99, mode="off")
        self.assertEqual(result["outcome"], "approve")
        self.assertIs(result["consulted"], False)
        self.assertEqual(calls, [])

    def test_shadow_records_the_answer_and_keeps_the_outcome(self):
        result = gate.gate_tool_call(
            {"record": "order-1", "amount": 5.0}, RECORDS, "policy text",
            ask=lambda state: 0.0, mode="shadow")
        self.assertEqual(result["outcome"], "approve")
        self.assertEqual(result["probability"], 0.0)
        self.assertIs(result["consulted"], True)

    def test_review_only_downgrades_a_passing_precheck(self):
        result = gate.gate_tool_call(
            {"record": "order-1", "amount": 5.0}, RECORDS, "policy text",
            ask=lambda state: 0.5, mode="review-only")
        self.assertEqual(result["outcome"], "review")
        self.assertIs(result["consulted"], True)

    def test_review_only_keeps_an_approving_second_opinion(self):
        result = gate.gate_tool_call(
            {"record": "order-1", "amount": 5.0}, RECORDS, "policy text",
            ask=lambda state: 0.95, mode="review-only")
        self.assertEqual(result["outcome"], "approve")

    def test_review_only_never_upgrades_a_block(self):
        calls = []
        result = gate.gate_tool_call(
            {"record": "order-1", "amount": 500.0}, RECORDS, "policy text",
            ask=lambda state: calls.append(state) or 1.0, mode="review-only")
        self.assertEqual(result["outcome"], "block")
        self.assertEqual(calls, [])
        self.assertIs(result["consulted"], False)

    def test_ask_that_raises_is_review(self):
        def boom(state):
            raise ValueError("no answer")
        result = gate.gate_tool_call(
            {"record": "order-1", "amount": 5.0}, RECORDS, "policy text",
            ask=boom, mode="review-only")
        self.assertEqual(result["outcome"], "review")
        self.assertIs(result["consulted"], True)
        self.assertIsNone(result["probability"])

    def test_ask_that_returns_after_the_deadline(self):
        def slow(state):
            time.sleep(1.0)
            return 0.99
        result = gate.gate_tool_call(
            {"record": "order-1", "amount": 5.0}, RECORDS, "policy text",
            ask=slow, mode="review-only")
        self.assertEqual(result["outcome"], "review")
        self.assertIs(result["consulted"], True)
        self.assertIn("deadline", result["reason"])

    def test_missing_ask_path_is_review_in_review_only(self):
        result = gate.gate_tool_call(
            {"record": "order-1", "amount": 5.0}, RECORDS, "policy text",
            ask=None, mode="review-only")
        self.assertEqual(result["outcome"], "review")
        self.assertIs(result["consulted"], False)

    def test_outside_gate_refusal_is_never_sent(self):
        sent = []
        result = gate.gate_tool_call(
            {"record": "order-1", "amount": 5.0}, RECORDS,
            "policy mentions zzzznotpresent",
            ask=lambda state: sent.append(state) or 0.99, mode="review-only")
        self.assertEqual(result["outcome"], "review")
        self.assertEqual(sent, [])
        self.assertIs(result["consulted"], False)

    def test_same_call_gated_twice_consults_twice_with_the_same_outcome(self):
        calls = []

        def ask(state):
            calls.append(state)
            return 0.95

        first = gate.gate_tool_call(
            {"record": "order-1", "amount": 5.0}, RECORDS, "policy text",
            ask=ask, mode="review-only")
        second = gate.gate_tool_call(
            {"record": "order-1", "amount": 5.0}, RECORDS, "policy text",
            ask=ask, mode="review-only")
        self.assertEqual(len(calls), 2)
        self.assertEqual(first["outcome"], second["outcome"])


class HostileInputsAreRefusedNotCrashed(_GateCase):

    def test_bool_amount_is_refused(self):
        ok, _ = gate.precheck({"record": "order-1", "amount": True}, RECORDS)
        self.assertFalse(ok)

    def test_nan_amount_is_refused(self):
        ok, _ = gate.precheck({"record": "order-1", "amount": float("nan")}, RECORDS)
        self.assertFalse(ok)

    def test_infinite_amount_is_refused(self):
        ok, _ = gate.precheck({"record": "order-1", "amount": float("inf")}, RECORDS)
        self.assertFalse(ok)

    def test_none_call_is_refused(self):
        self.assertFalse(gate.precheck(None, RECORDS)[0])

    def test_wrong_type_call_is_refused(self):
        self.assertFalse(gate.precheck("order-1", RECORDS)[0])
        self.assertFalse(gate.precheck(7, RECORDS)[0])

    def test_wrong_type_records_is_refused(self):
        self.assertFalse(gate.precheck({"record": "order-1", "amount": 1.0}, None)[0])
        self.assertFalse(gate.precheck({"record": "order-1", "amount": 1.0}, ["order-1"])[0])

    def test_unhashable_record_name_is_refused(self):
        ok, _ = gate.precheck({"record": ["order-1"], "amount": 1.0}, RECORDS)
        self.assertFalse(ok)

    def test_record_that_is_not_a_mapping_is_refused(self):
        ok, _ = gate.precheck({"record": "order-1", "amount": 1.0}, {"order-1": 5.0})
        self.assertFalse(ok)

    def test_altered_record_loses_its_balance_and_is_refused(self):
        records = {"order-1": {"balance": 100.0}}
        records["order-1"].pop("balance")
        ok, _ = gate.precheck({"record": "order-1", "amount": 1.0}, records)
        self.assertFalse(ok)

    def test_bool_probability_is_review(self):
        self.assertEqual(gate.gate_verdict(True), "review")

    def test_gate_tool_call_never_raises_on_hostile_inputs(self):
        for bad_call in (None, [], "x", 3, {"record": None, "amount": None},
                         {"record": "order-1", "amount": float("nan")}):
            result = gate.gate_tool_call(bad_call, RECORDS, "policy text", mode="review-only")
            self.assertEqual(result["outcome"], "block")
        for bad_mode in (None, 3, "no-such-mode"):
            result = gate.gate_tool_call({"record": "order-1", "amount": 1.0},
                                         RECORDS, "policy text", mode=bad_mode)
            self.assertEqual(result["outcome"], "review")


if __name__ == "__main__":
    unittest.main()

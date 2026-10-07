#!/usr/bin/env python3
"""Contract tests for scripts/loop/adapters/__init__.py (FX-31.1).

Every case drives the public contract directly: adapter_for, judge and normalize_cost. Nothing
here reads a repository document, starts a process or needs a home directory, so the suite runs
in an export copy with an empty HOME.
"""
import json
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import adapters as A


class AdapterForTests(unittest.TestCase):
    """The one door to a transport. An unknown or unhashable one refuses BEFORE any process."""

    def test_every_named_transport_returns_a_usable_adapter(self):
        for name in A.TRANSPORTS:
            adapter = A.adapter_for(name)
            self.assertEqual(adapter.transport, name)
            self.assertTrue(callable(adapter.argv))
            self.assertTrue(callable(adapter.judge))
            self.assertTrue(callable(adapter.cost))

    def test_unknown_transport_is_refused_with_the_reason(self):
        with self.assertRaises(A.Refused) as caught:
            A.adapter_for("smoke-signal")
        self.assertIn("unknown transport", str(caught.exception))

    def test_missing_or_unhashable_transport_is_refused_without_a_process(self):
        for bad in (None, [], {}, 1, True, 2.5, float("nan"), ("bridge",), b"bridge"):
            with self.assertRaises(A.Refused):
                A.adapter_for(bad)

    def test_a_transport_is_matched_across_case_and_spacing(self):
        self.assertEqual(A.adapter_for("  BRIDGE  ").transport, "bridge")

    def test_an_adapter_refuses_to_invent_a_command_line(self):
        with self.assertRaises(A.Refused):
            A.adapter_for("bridge").argv("m", "prompt", 30, {"id": "m"})

    def test_the_adapter_judges_through_the_same_contract(self):
        got = A.adapter_for("bridge").judge("m", {"id": "m"}, {"returncode": 0, "stdout": "hi", "stderr": ""})
        self.assertTrue(got.ok)
        self.assertEqual(got.answer, "hi")


class NormalizeCostTests(unittest.TestCase):
    """R-FX-31-2: a missing cost stays UNKNOWN, a measured zero stays a measured zero."""

    def test_a_missing_cost_is_not_measured_and_never_zero(self):
        self.assertIs(A.normalize_cost(None), A.NOT_MEASURED)
        self.assertIsNone(A.normalize_cost(None))
        self.assertIsNot(A.normalize_cost(None), 0.0)

    def test_a_reported_and_priced_zero_stays_a_measured_zero(self):
        self.assertEqual(A.normalize_cost(0), 0.0)
        self.assertIsInstance(A.normalize_cost(0.0), float)
        self.assertEqual(A.normalize_cost(0, {"input_tokens": 2}), 0.0)

    def test_a_fractional_measured_cost_is_kept_exactly(self):
        self.assertEqual(A.normalize_cost(0.0086), 0.0086)

    def test_hostile_cost_values_are_refused_not_coerced(self):
        for bad in (True, False, "0", "0.0086", [], {}, b"0", float("nan"), float("inf"),
                    float("-inf"), 10 ** 400):
            with self.assertRaises(ValueError):
                A.normalize_cost(bad)

    def test_hostile_usage_is_refused_not_coerced(self):
        for bad in ("usage", 1, [], True):
            with self.assertRaises(ValueError):
                A.normalize_cost(1.0, bad)


class JudgeTests(unittest.TestCase):
    """The shared status vocabulary: OK, FAILED, PROVIDER_REFUSED."""

    def setUp(self):
        self.bridge = A.adapter_for("bridge")

    def test_a_nonzero_exit_with_a_full_body_is_failed(self):
        got = A.judge(self.bridge, "m", {"id": "x"},
                      {"returncode": 1, "stdout": "a plausible answer", "stderr": ""})
        self.assertFalse(got.ok)
        self.assertEqual(got.status, A.STATUS_FAILED)
        self.assertIn("exit 1", got.detail)

    def test_an_empty_body_at_exit_zero_is_failed(self):
        got = A.judge(self.bridge, "m", {"id": "x"},
                      {"returncode": 0, "stdout": "   \n  ", "stderr": ""})
        self.assertFalse(got.ok)
        self.assertEqual(got.status, A.STATUS_FAILED)
        self.assertIn("EMPTY", got.detail)

    def test_a_provider_refusal_is_its_own_status_and_never_ok(self):
        got = A.judge(self.bridge, "m", {"id": "x"},
                      {"returncode": 1, "stdout": "", "stderr": "provider refused: quota"})
        self.assertFalse(got.ok)
        self.assertEqual(got.status, A.STATUS_PROVIDER_REFUSED)

    def test_an_answer_at_exit_zero_is_ok_and_carried_verbatim(self):
        got = A.judge(self.bridge, "m", {"id": "x"},
                      {"returncode": 0, "stdout": "hello\n", "stderr": ""})
        self.assertTrue(got.ok)
        self.assertEqual(got.status, A.STATUS_OK)
        self.assertEqual(got.answer, "hello")
        self.assertIs(got.cost_usd, A.NOT_MEASURED)

    def test_a_result_with_no_returncode_fails_closed(self):
        got = A.judge(self.bridge, "m", {"id": "x"}, {"stdout": "hello", "stderr": ""})
        self.assertFalse(got.ok)

    def test_the_first_party_transport_judges_its_json_record(self):
        first = A.adapter_for("claude")
        row = {"id": "first-party-x"}
        good = A.judge(first, "c", row, {"returncode": 0,
                                          "stdout": json.dumps({"result": "12", "total_cost_usd": 0.0086}),
                                          "stderr": ""})
        self.assertTrue(good.ok)
        self.assertEqual(good.answer, "12")
        error = A.judge(first, "c", row, {"returncode": 0,
                                           "stdout": json.dumps({"is_error": True, "result": "overloaded"}),
                                           "stderr": ""})
        self.assertFalse(error.ok)
        self.assertEqual(error.status, A.STATUS_FAILED)
        prose = A.judge(first, "c", row, {"returncode": 0, "stdout": "12", "stderr": ""})
        self.assertFalse(prose.ok)
        empty = A.judge(first, "c", row, {"returncode": 0,
                                           "stdout": json.dumps({"result": "   "}), "stderr": ""})
        self.assertFalse(empty.ok)

    def test_hostile_results_and_adapters_are_refused_not_crashed(self):
        for bad in (None, [], "a result", 1, True):
            with self.assertRaises(ValueError):
                A.judge(self.bridge, "m", {"id": "x"}, bad)
        with self.assertRaises(ValueError):
            A.judge(None, "m", {"id": "x"}, {"returncode": 0, "stdout": "hello", "stderr": ""})
        with self.assertRaises(ValueError):
            A.judge(object(), "m", {"id": "x"}, {"returncode": 0, "stdout": "hello", "stderr": ""})
        with self.assertRaises(ValueError):
            A.judge(self.bridge, None, {"id": "x"}, {"returncode": 0, "stdout": "hello", "stderr": ""})
        with self.assertRaises(ValueError):
            A.judge(self.bridge, "m", [], {"returncode": 0, "stdout": "hello", "stderr": ""})
        with self.assertRaises(ValueError):
            A.judge(self.bridge, "m", {"id": "x"}, {"returncode": 0, "stdout": b"hello", "stderr": ""})
        with self.assertRaises(ValueError):
            A.judge(self.bridge, "m", {"id": "x"}, {"returncode": 0, "stdout": "hello", "stderr": ["x"]})


class AdapterCostTests(unittest.TestCase):
    """R-FX-31-2 at the adapter boundary: each transport's own cost method."""

    def test_a_missing_cost_stays_not_measured_and_a_zero_stays_zero(self):
        bridge = A.adapter_for("bridge")
        self.assertIs(bridge.cost({"returncode": 0, "cost_usd": None}), A.NOT_MEASURED)
        self.assertEqual(bridge.cost({"returncode": 0, "cost_usd": 0}), 0.0)
        self.assertEqual(bridge.cost({"returncode": 0, "cost_usd": 0.0086}), 0.0086)

    def test_the_first_party_transport_reads_its_cost_from_its_result_record(self):
        first = A.adapter_for("claude")
        self.assertEqual(first.cost({"stdout": json.dumps({"result": "12", "total_cost_usd": 0.0086})}),
                         0.0086)
        self.assertIs(first.cost({"stdout": "not the record"}), A.NOT_MEASURED)
        self.assertIs(first.cost({"stdout": json.dumps({"result": "12", "total_cost_usd": None})}),
                     A.NOT_MEASURED)

    def test_a_result_that_is_not_a_mapping_is_refused(self):
        for bad in (None, [], "x", 1):
            with self.assertRaises(ValueError):
                A.adapter_for("bridge").cost(bad)


if __name__ == "__main__":
    unittest.main()

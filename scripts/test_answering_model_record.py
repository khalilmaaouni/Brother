#!/usr/bin/env python3
"""Calibration for scripts/answering_model_record.py.

The property this file exists to assert is not that the happy path works,
it is the two fail-closed defaults: a response with no usable structured
model field must read NO-DATA, never PASS and never silently FAIL either;
and a requested model absent from the caller's policy must accept nothing
but an exact match. A suite that only exercises the exact-match PASS case
would stay green through a regression in either default.
"""
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import answering_model_record as amr  # noqa: E402


class TestExtractAnsweringModel(unittest.TestCase):
    def test_missing_key_is_none(self):
        self.assertIsNone(amr.extract_answering_model({}))

    def test_none_value_is_none(self):
        self.assertIsNone(amr.extract_answering_model({"model": None}))

    def test_empty_string_is_none(self):
        self.assertIsNone(amr.extract_answering_model({"model": ""}))

    def test_whitespace_only_is_none(self):
        self.assertIsNone(amr.extract_answering_model({"model": "   "}))

    def test_non_string_value_is_none(self):
        self.assertIsNone(amr.extract_answering_model({"model": 7}))
        self.assertIsNone(amr.extract_answering_model({"model": ["a"]}))

    def test_valid_string_is_stripped_and_returned(self):
        self.assertEqual(
            amr.extract_answering_model({"model": "  deepseek/deepseek-v4.1-flash  "}),
            "deepseek/deepseek-v4.1-flash",
        )

    def test_non_dict_payload_raises_type_error(self):
        with self.assertRaises(TypeError):
            amr.extract_answering_model("not a dict")
        with self.assertRaises(TypeError):
            amr.extract_answering_model(None)
        with self.assertRaises(TypeError):
            amr.extract_answering_model(["model", "x"])


class TestEvaluate(unittest.TestCase):
    def test_no_data_when_model_field_absent(self):
        v = amr.evaluate("meta/muse-spark-1.3-contributor", {})
        self.assertEqual(v.verdict, amr.NO_DATA)
        self.assertIsNone(v.answering_model)
        self.assertEqual(v.requested_model, "meta/muse-spark-1.3-contributor")

    def test_no_data_is_never_read_as_the_requested_model(self):
        # The bad state a careless check would also pass: treating an
        # absent field as "the requested model, presumably" instead of
        # refusing to guess.
        v = amr.evaluate("model-a", {"model": ""})
        self.assertEqual(v.verdict, amr.NO_DATA)
        self.assertNotEqual(v.answering_model, "model-a")

    def test_exact_match_passes(self):
        v = amr.evaluate("model-a", {"model": "model-a"})
        self.assertEqual(v.verdict, amr.PASS)
        self.assertEqual(v.answering_model, "model-a")

    def test_substitution_with_no_policy_entry_fails_closed(self):
        v = amr.evaluate("model-a", {"model": "model-b"},
                          policy={"model-c": ["model-b"]})
        self.assertEqual(v.verdict, amr.FAIL)
        self.assertEqual(v.answering_model, "model-b")

    def test_substitution_with_no_policy_at_all_fails_closed(self):
        v = amr.evaluate("model-a", {"model": "model-b"}, policy=None)
        self.assertEqual(v.verdict, amr.FAIL)

    def test_substitution_permitted_by_policy_passes(self):
        v = amr.evaluate("model-a", {"model": "model-b"},
                          policy={"model-a": ["model-b", "model-c"]})
        self.assertEqual(v.verdict, amr.PASS)
        self.assertEqual(v.answering_model, "model-b")

    def test_policy_entry_for_another_model_never_leaks_over(self):
        # A permitted substitute list scoped to a different requested
        # model must not grant permission here.
        v = amr.evaluate("model-a", {"model": "model-b"},
                          policy={"model-x": ["model-b"]})
        self.assertEqual(v.verdict, amr.FAIL)

    def test_policy_entry_as_bare_string_is_one_id_not_a_char_sequence(self):
        # "b" in "model-b" style character membership would wrongly pass
        # here; a bare string entry must be treated as one substitute id.
        v = amr.evaluate("model-a", {"model": "b"},
                          policy={"model-a": "model-b"})
        self.assertEqual(v.verdict, amr.FAIL)
        v2 = amr.evaluate("model-a", {"model": "model-b"},
                           policy={"model-a": "model-b"})
        self.assertEqual(v2.verdict, amr.PASS)

    def test_requested_model_empty_string_raises_value_error(self):
        with self.assertRaises(ValueError):
            amr.evaluate("", {"model": "model-a"})
        with self.assertRaises(ValueError):
            amr.evaluate("   ", {"model": "model-a"})

    def test_requested_model_non_string_raises_value_error(self):
        with self.assertRaises(ValueError):
            amr.evaluate(None, {"model": "model-a"})
        with self.assertRaises(ValueError):
            amr.evaluate(7, {"model": "model-a"})

    def test_payload_non_dict_raises_type_error(self):
        with self.assertRaises(TypeError):
            amr.evaluate("model-a", "not a dict")

    def test_policy_non_dict_raises_type_error(self):
        with self.assertRaises(TypeError):
            amr.evaluate("model-a", {"model": "model-a"}, policy=["model-b"])

    def test_requested_model_is_stripped_before_comparison(self):
        v = amr.evaluate("  model-a  ", {"model": "model-a"})
        self.assertEqual(v.verdict, amr.PASS)
        self.assertEqual(v.requested_model, "model-a")

    def test_comparison_is_case_sensitive(self):
        # A case-insensitive match here would silently widen what counts
        # as "the requested model answered".
        v = amr.evaluate("Model-A", {"model": "model-a"})
        self.assertEqual(v.verdict, amr.FAIL)

    def test_verdict_is_one_of_the_three_known_strings(self):
        for requested, payload, policy in (
            ("model-a", {}, None),
            ("model-a", {"model": "model-a"}, None),
            ("model-a", {"model": "model-b"}, None),
        ):
            v = amr.evaluate(requested, payload, policy)
            self.assertIn(v.verdict, (amr.PASS, amr.FAIL, amr.NO_DATA))


if __name__ == "__main__":
    unittest.main()

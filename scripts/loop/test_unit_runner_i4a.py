#!/usr/bin/env python3
"""I4.a: the lane rule is one constant in scripts/loop/unit_runner.py, and both remedies splice it.

unit_runner.py is a script, not importable, so the source is read as bytes and parsed with ast. Every
case is RED on the base tree and GREEN after the runner edit. Run from the repository root:

    python3 -B scripts/loop/test_unit_runner_i4a.py
"""
import ast
import os
import sys
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
UNIT_RUNNER = os.path.join(HERE, "unit_runner.py")


def _source():
    """The runner's own bytes, decoded as utf-8. No caching: the file is small."""
    with open(UNIT_RUNNER, "rb") as fh:
        return fh.read().decode("utf-8")


def _module_constant(name):
    """A module level constant assigned a literal, read from the parsed source. A name the source
    does not define returns None, which is a failure in every case below, never a skip."""
    for node in ast.parse(_source()).body:
        if isinstance(node, ast.Assign):
            for target in node.targets:
                if isinstance(target, ast.Name) and target.id == name:
                    try:
                        return ast.literal_eval(node.value)
                    except (ValueError, SyntaxError):
                        return None
    return None


def _rendered_strings(tree):
    """Every string the parsed tree renders: a literal, an implicit adjacent literal (the ast joins
    those into one Constant), and every '+' of two string constants."""
    out = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Constant) and isinstance(node.value, str):
            out.append(node.value)
        elif (isinstance(node, ast.BinOp) and isinstance(node.op, ast.Add)
              and isinstance(node.left, ast.Constant) and isinstance(node.left.value, str)
              and isinstance(node.right, ast.Constant) and isinstance(node.right.value, str)):
            out.append(node.left.value + node.right.value)
    return out


class TheLaneRuleInOnePlace(unittest.TestCase):
    """One condition per case."""

    def test_LANE_RULE_is_one_non_empty_str_single_line_unpadded(self):
        rule = _module_constant("LANE_RULE")
        self.assertIsInstance(rule, str, "unit_runner.LANE_RULE is missing or is not a string")
        self.assertTrue(rule, "unit_runner.LANE_RULE is empty")
        self.assertNotIn("\n", rule, "LANE_RULE is more than one line")
        self.assertNotIn("\r", rule, "LANE_RULE is more than one line")
        self.assertEqual(rule, rule.strip(), "LANE_RULE is padded with leading or trailing whitespace")

    def test_the_lane_rule_is_spelled_once_in_the_runner(self):
        rule = _module_constant("LANE_RULE")
        self.assertIsInstance(rule, str, "unit_runner.LANE_RULE is missing or is not a string")
        self.assertTrue(rule, "unit_runner.LANE_RULE is empty")
        want = " ".join(rule.split())
        hits = [s for s in _rendered_strings(ast.parse(_source())) if want in " ".join(s.split())]
        self.assertEqual(len(hits), 1,
                         "expected exactly one rendered string to carry the lane rule, found %d" % len(hits))

    def test_the_brief_gate_remedy_is_the_literal_plus_the_rule(self):
        tree = ast.parse(_source())
        found = None
        for node in ast.walk(tree):
            if not (isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
                    and node.func.id == "with_remedy" and len(node.args) >= 2):
                continue
            first = node.args[0]
            text = None
            if isinstance(first, ast.Constant) and isinstance(first.value, str):
                text = first.value
            elif (isinstance(first, ast.BinOp) and isinstance(first.op, ast.Mod)
                  and isinstance(first.left, ast.Constant) and isinstance(first.left.value, str)):
                text = first.left.value
            if text and text.startswith("WITHHELD brief could not be built"):
                found = node
                break
        self.assertIsNotNone(found, "the brief gate's with_remedy call was not found")
        second = found.args[1]
        self.assertIsInstance(second, ast.BinOp, "the brief gate remedy is not the literal plus LANE_RULE")
        self.assertIsInstance(second.op, ast.Add)
        self.assertIsInstance(second.left, ast.Constant)
        self.assertEqual(second.left.value, "repair the spec section it names; ")
        self.assertIsInstance(second.right, ast.Name)
        self.assertEqual(second.right.id, "LANE_RULE")

    def test_the_grader_remedy_splices_the_literal_plus_the_rule(self):
        ok = False
        for node in ast.walk(ast.parse(_source())):
            if not (isinstance(node, ast.FunctionDef) and node.name == "grade_nothing_line"):
                continue
            for sub in ast.walk(node):
                if (isinstance(sub, ast.BinOp) and isinstance(sub.op, ast.Add)
                        and isinstance(sub.left, ast.Constant) and isinstance(sub.left.value, str)
                        and sub.left.value.endswith("fix the grader or machine fact it names; ")
                        and isinstance(sub.right, ast.Name) and sub.right.id == "LANE_RULE"):
                    ok = True
        self.assertTrue(ok, "grade_nothing_line does not splice its last clause from LANE_RULE")


if __name__ == "__main__":
    unittest.main(verbosity=2)

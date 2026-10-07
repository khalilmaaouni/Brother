"""Tests for L5b.2 silent-failure lint rules."""
import ast
import types
import unittest

from tools.l5b_audit.lint_rules import (
    lint_call,
    rule_bare_except,
    rule_except_pass,
    rule_discard_return,
    rule_force_unwrap,
    RULE_BARE_EXCEPT,
    RULE_EXCEPT_PASS,
    RULE_DISCARD_RETURN,
    RULE_FORCE_UNWRAP,
    LintHit,
    hit_id,
)
from tools.l5b_audit.scanner import BoundaryCall


def _find_call(source, func_name):
    tree = ast.parse(source)
    for node in ast.walk(tree):
        if isinstance(node, ast.Call):
            if isinstance(node.func, ast.Name) and node.func.id == func_name:
                return node.lineno, node.col_offset
            if isinstance(node.func, ast.Attribute) and node.func.attr == func_name:
                return node.lineno, node.col_offset
    raise AssertionError(f"call {func_name} not found")


def _make_call(source, func_name, symbol, kind="JSON", file="test.py", enclosing="f"):
    line, col = _find_call(source, func_name)
    return BoundaryCall(
        file=file,
        line=line,
        column=col,
        kind=kind,
        symbol=symbol,
        enclosing_function=enclosing,
        callee_defined_here=False,
    )


def _valid_call(**overrides):
    data = {
        "file": "test.py",
        "line": 3,
        "column": 8,
        "kind": "JSON",
        "symbol": "json.loads",
        "enclosing_function": "f",
        "callee_defined_here": False,
    }
    data.update(overrides)
    return types.SimpleNamespace(**data)


class TestLintRules(unittest.TestCase):
    def test_T_BARE_EXCEPT_HIT(self):
        source = """
def f():
    try:
        json.loads('{}')
    except:
        pass
"""
        call = _make_call(source, "loads", "json.loads", kind="JSON")
        hits = lint_call(source, call)
        self.assertIn(RULE_BARE_EXCEPT, [h.rule_id for h in hits])

    def test_T_EXCEPT_PASS_HIT(self):
        source = """
def f():
    try:
        open('x')
    except OSError:
        pass
"""
        call = _make_call(source, "open", "open", kind="FILE_IO")
        hits = lint_call(source, call)
        self.assertIn(RULE_EXCEPT_PASS, [h.rule_id for h in hits])

    def test_T_DISCARD_RETURN_HIT(self):
        source = """
import subprocess
def f():
    subprocess.run(['ls'])
"""
        call = _make_call(source, "run", "subprocess.run", kind="SUBPROCESS")
        hits = lint_call(source, call)
        self.assertIn(RULE_DISCARD_RETURN, [h.rule_id for h in hits])

    def test_T_FORCE_UNWRAP_HIT(self):
        source = """
import json
def f():
    json.loads('{}')['k']
"""
        call = _make_call(source, "loads", "json.loads", kind="JSON")
        hits = lint_call(source, call)
        self.assertIn(RULE_FORCE_UNWRAP, [h.rule_id for h in hits])

    def test_T_ANNOTATION_CLEARS_HIT(self):
        source = """
import json
def f():
    # l5b: PROPAGATE_SAFE: the caller owns this failure and re-raises it
    json.loads('{}')['k']
"""
        call = _make_call(source, "loads", "json.loads", kind="JSON")
        hits = lint_call(source, call)
        self.assertEqual(hits, ())

    def test_a_bare_annotation_with_no_reason_clears_nothing(self):
        """Found by a council attack on the landed unit: a bare marker cleared ANY rule with no reason read, a hidden
        waiver. The spec's own flip condition for open decision 2. An exemption carries a reason that is read."""
        for marker in ("# l5b: PROPAGATE_SAFE", "# l5b: PROPAGATE_SAFE:", "# l5b: PROPAGATE_SAFE:    ", "# l5b: PROPAGATE_SAFE: ok"):
            with self.subTest(marker=marker):
                source = "\nimport json\ndef f():\n    " + marker + "\n    json.loads('{}')['k']\n"
                call = _make_call(source, "loads", "json.loads", kind="JSON")
                self.assertIn(RULE_FORCE_UNWRAP, [h.rule_id for h in lint_call(source, call)])

    def test_hit_id_format(self):
        call = _valid_call()
        h = hit_id(call, RULE_BARE_EXCEPT)
        self.assertEqual(h, f"{call.file}:{call.line}:{call.column}:{RULE_BARE_EXCEPT}")

    def test_hit_id_rejects_path_traversal(self):
        with self.assertRaises(ValueError):
            hit_id(_valid_call(file="../x"), RULE_BARE_EXCEPT)

    def test_hit_id_rejects_unknown_rule(self):
        with self.assertRaises(ValueError):
            hit_id(_valid_call(), "UNKNOWN-RULE")

    def test_lint_call_rejects_wrong_line_type(self):
        with self.assertRaises(ValueError):
            lint_call("def f(): pass", _valid_call(line="3"))

    def test_lint_call_rejects_non_str_source(self):
        with self.assertRaises(ValueError):
            lint_call(b"\xff\xfe", _valid_call())

    def test_rule_bare_except_rejects_non_ast_fn(self):
        with self.assertRaises(ValueError):
            rule_bare_except(None, _valid_call())

    def test_rule_discard_return_rejects_unhashable_symbol(self):
        fn = ast.parse("def f(): pass")
        with self.assertRaises(ValueError):
            rule_discard_return(fn, _valid_call(symbol=["x"]))

    def test_hostile_input_refused(self):
        valid_call = _valid_call()
        valid_fn = ast.parse("def f(): pass")
        with self.assertRaises(ValueError):
            hit_id(None, RULE_BARE_EXCEPT)
        with self.assertRaises(ValueError):
            hit_id(valid_call, None)
        with self.assertRaises(ValueError):
            hit_id(valid_call, "UNKNOWN-RULE")
        with self.assertRaises(ValueError):
            hit_id(_valid_call(file="../x"), RULE_BARE_EXCEPT)
        with self.assertRaises(ValueError):
            lint_call(None, valid_call)
        with self.assertRaises(ValueError):
            lint_call(b"\xff\xfe", valid_call)
        with self.assertRaises(ValueError):
            lint_call("def f(): pass", None)
        with self.assertRaises(ValueError):
            lint_call("def f(): pass", _valid_call(line="3"))
        with self.assertRaises(ValueError):
            lint_call("def f(): pass", _valid_call(line=True))
        with self.assertRaises(ValueError):
            lint_call("def f(): pass", _valid_call(column=True))
        with self.assertRaises(ValueError):
            lint_call("def f(): pass", _valid_call(symbol=["x"]))
        with self.assertRaises(ValueError):
            lint_call("def f(): pass", _valid_call(kind="BAD"))
        with self.assertRaises(ValueError):
            lint_call("def f(): pass", _valid_call(callee_defined_here=1))
        with self.assertRaises(ValueError):
            rule_bare_except(None, valid_call)
        with self.assertRaises(ValueError):
            rule_bare_except((), valid_call)
        with self.assertRaises(ValueError):
            rule_bare_except("str", valid_call)
        with self.assertRaises(ValueError):
            rule_bare_except(b"\xff", valid_call)
        with self.assertRaises(ValueError):
            rule_except_pass(None, valid_call)
        with self.assertRaises(ValueError):
            rule_except_pass(b"\xff", valid_call)
        with self.assertRaises(ValueError):
            rule_discard_return(None, valid_call)
        with self.assertRaises(ValueError):
            rule_force_unwrap(None, valid_call)
        with self.assertRaises(ValueError):
            rule_force_unwrap(valid_fn, None)
        with self.assertRaises(ValueError):
            rule_force_unwrap(valid_fn, _valid_call(symbol=["x"]))


if __name__ == "__main__":
    unittest.main()

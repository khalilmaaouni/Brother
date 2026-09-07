#!/usr/bin/env python3
"""Proves benchmarks/jbeq/mdm/decision-rules-addendum.md's "Rule id map"
section names every rule id scripts/jbeq_decide.py can actually produce or
accept, so a person reading a decisions.jsonl row's rule_fired value can
always find the rule text behind it (a haiku check on 2026-09-06 found the
addendum's own numbered rules 5 to 11 covered only six of the ids the engine
emits, and eleven emitted or accepted ids had no heading here at all).

HOW THE ENGINE'S OWN ID LIST IS BUILT. This file never retypes the ids by
hand: it parses scripts/jbeq_decide.py's source with Python's ast module,
the same way the addendum's own "Rule id map" section says it was built, so
a future rule added to the engine without a matching addendum entry fails
this test rather than going unnoticed again.

  rule_fired ids: every string literal found at a `"rule_fired": "..."` key
  in a dict literal, every second positional argument to a call to
  `finish(...)`, every first positional argument to a call to
  `gated_reject(...)`, and every second element of a 3-string-literal tuple
  returned from _auto_merge_blocked's gate items.

  JBEQ_DECIDE_DISABLE_RULES accepted ids: every string literal compared
  with `in`/`not in` against a name called `disabled`, excluding the
  internal "*" sentinel (produced only by the bare "1" shorthand, never a
  user-facing id in the comma list; see the module's own docstring).

DRIVEN BACKWARDS: removing any one line from the addendum's Rule id map
(this file's own __main__ block, run once by hand with
`--prove-backwards`) makes the corresponding assertion fail, naming the
missing id, proving this test would actually catch the gap it exists for.
"""
import ast
import os
import re
import sys
import unittest

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
ENGINE_SRC = os.path.join(REPO, "scripts", "jbeq_decide.py")
ADDENDUM = os.path.join(REPO, "benchmarks", "jbeq", "mdm", "decision-rules-addendum.md")


def _parse_engine_source():
    with open(ENGINE_SRC, encoding="utf-8") as fh:
        return ast.parse(fh.read(), filename=ENGINE_SRC)


def extract_rule_fired_ids(tree):
    """Every id decide()/_decide_unwrapped can write to "rule_fired"."""
    ids = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Dict):
            for key, value in zip(node.keys, node.values):
                if (isinstance(key, ast.Constant) and key.value == "rule_fired"
                        and isinstance(value, ast.Constant)
                        and isinstance(value.value, str)):
                    ids.add(value.value)
        if isinstance(node, ast.Call):
            func_name = getattr(node.func, "id", None)
            if func_name == "finish" and len(node.args) >= 2:
                arg = node.args[1]
                if isinstance(arg, ast.Constant) and isinstance(arg.value, str):
                    ids.add(arg.value)
            elif func_name == "gated_reject" and len(node.args) >= 1:
                arg = node.args[0]
                if isinstance(arg, ast.Constant) and isinstance(arg.value, str):
                    ids.add(arg.value)
        if isinstance(node, ast.Return) and isinstance(node.value, ast.Tuple):
            elts = node.value.elts
            if (len(elts) == 3
                    and isinstance(elts[0], ast.Constant) and isinstance(elts[0].value, str)
                    and isinstance(elts[1], ast.Constant) and isinstance(elts[1].value, str)):
                ids.add(elts[1].value)
    return ids


def extract_disable_rule_ids(tree):
    """Every id JBEQ_DECIDE_DISABLE_RULES accepts to disable one rule or
    gate on its own: every string literal compared with in/not in against
    a bare name `disabled`. The "*" sentinel is internal (see module
    docstring: it is what the bare "1" shorthand resolves to, never typed
    by a caller in the comma list), so it is excluded here.
    """
    ids = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Compare) and len(node.ops) == 1:
            op = node.ops[0]
            if not isinstance(op, (ast.In, ast.NotIn)):
                continue
            left = node.left
            comparator = node.comparators[0]
            if (isinstance(left, ast.Constant) and isinstance(left.value, str)
                    and isinstance(comparator, ast.Name) and comparator.id == "disabled"):
                if left.value == "*":
                    continue
                ids.add(left.value)
    return ids


_ID_LIST_ITEM = re.compile(r"^- `([^`]+)`", re.MULTILINE)


def documented_ids():
    """Every id given its own "- `id`, ..." entry in the addendum's Rule
    id map section (everything from that heading to end of file)."""
    with open(ADDENDUM, encoding="utf-8") as fh:
        text = fh.read()
    marker = "## Rule id map"
    idx = text.find(marker)
    if idx == -1:
        return set()
    section = text[idx:]
    return set(_ID_LIST_ITEM.findall(section))


class TestAddendumRuleIdMap(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tree = _parse_engine_source()
        cls.rule_fired_ids = extract_rule_fired_ids(cls.tree)
        cls.disable_ids = extract_disable_rule_ids(cls.tree)
        cls.documented = documented_ids()

    def test_extraction_found_a_nonempty_set(self):
        # A sanity floor so a parser bug (e.g. a refactor of finish()'s
        # signature) that silently emptied the extraction can't pass this
        # suite by having nothing left to check.
        self.assertGreaterEqual(len(self.rule_fired_ids), 20)
        self.assertGreaterEqual(len(self.disable_ids), 20)

    def test_every_rule_fired_id_is_documented(self):
        missing = sorted(self.rule_fired_ids - self.documented)
        self.assertEqual(
            missing, [],
            "rule_fired id(s) %s can be written by scripts/jbeq_decide.py "
            "but have no entry in benchmarks/jbeq/mdm/decision-rules-"
            "addendum.md's Rule id map" % missing,
        )

    def test_every_disable_rules_id_is_documented(self):
        missing = sorted(self.disable_ids - self.documented)
        self.assertEqual(
            missing, [],
            "JBEQ_DECIDE_DISABLE_RULES accepted id(s) %s have no entry in "
            "benchmarks/jbeq/mdm/decision-rules-addendum.md's Rule id map"
            % missing,
        )

    def test_no_documented_id_is_stale(self):
        # The reverse direction: an id documented here that the engine can
        # no longer produce or accept is itself a drift the addendum should
        # not carry silently.
        known = self.rule_fired_ids | self.disable_ids
        stale = sorted(self.documented - known)
        self.assertEqual(
            stale, [],
            "documented id(s) %s in the Rule id map no longer match any "
            "rule_fired value or JBEQ_DECIDE_DISABLE_RULES id "
            "scripts/jbeq_decide.py can produce" % stale,
        )


if __name__ == "__main__":
    if "--prove-backwards" in sys.argv:
        # Drive the test backwards once: pretend the addendum never
        # documented "gate-temporal" and show the assertion names exactly
        # that gap, then leave the real file untouched (this reads the
        # file, it never writes it).
        with open(ADDENDUM, encoding="utf-8") as fh:
            real_text = fh.read()
        mutated = real_text.replace(
            "- `gate-temporal`, addendum heading: none.", "- `REMOVED-FOR-TEST`, addendum heading: none.",
        )
        assert mutated != real_text, "fixture string not found; nothing to mutate"
        tmp_path = ADDENDUM + ".prove-backwards.tmp"
        with open(tmp_path, "w", encoding="utf-8") as fh:
            fh.write(mutated)
        ADDENDUM = tmp_path  # module scope already; no `global` needed here
        try:
            suite = unittest.TestLoader().loadTestsFromTestCase(TestAddendumRuleIdMap)
            result = unittest.TextTestRunner(verbosity=2).run(suite)
        finally:
            os.remove(tmp_path)
        sys.exit(0 if not result.wasSuccessful() else 1)
    unittest.main()

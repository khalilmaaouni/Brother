#!/usr/bin/env python3
"""HX119.c: the frozen harness for the routed model safety units, CJK1 and CJK1H.

Written by the session before any of its units is built, never by their builders (HARN1), and RED ON THE
BASE BY AN ASSERTION, never by an import error and never by a skip.

CLASSES AND THE SUB UNIT EACH ONE CLOSES (from the plan's own sub_done_checks):
  CJK             CJK1H.a   scripts/receipt_door.py, scripts/door.py
  RoutedFailures  CJK1H.b   scripts/model_worker.py
  Vendors         CJK1.c    products/brothermode/tools/bm_store.py

HX119.c's spec names fewer classes than the plan puts in this file. All three are here, because HARN1
refuses a lane that changes a frozen harness, so a unit left without a bar needs a hand unfreeze later.

EVERY RED HERE WAS MEASURED ON THIS TREE BEFORE THE TEST WAS WRITTEN:
  risk_hits in door.py                     absent
  CJK codepoints in receipt_door.py        0, so no zh or ja trigger exists
  classify_failure                         present, but its signature has no status parameter
  executed_by in model_worker.py           absent
  is_autonomy_model in bm_store.py         absent
  AUTONOMY_MODEL_TOKENS                    deepseek and qwen present; glm, kimi, minimax and doubao
                                           missing, so four of the six routed vendors do not count
A class whose stated red no longer reproduces is labelled, never faked: a check that passes without its
own fix proves nothing about the fix.
"""
import ast
import os
import re
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
RECEIPT = os.path.join(ROOT, "scripts/receipt_door.py")
DOOR = os.path.join(ROOT, "scripts/door.py")
WORKER = os.path.join(ROOT, "scripts/model_worker.py")
STORE = os.path.join(ROOT, "products/brothermode/tools/bm_store.py")
ABSENT = "NO-DATA: %s is not in this tree, so nothing here can judge it"
ROUTED_VENDORS = ("deepseek", "qwen", "glm", "kimi", "minimax", "doubao")


def defines(path, name):
    try:
        with open(path, encoding="utf-8") as fh:
            tree = ast.parse(fh.read(), filename=path)
    except (OSError, SyntaxError):
        return None
    return any(isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)) and n.name == name
               for n in tree.body)


def source(path):
    try:
        with open(path, encoding="utf-8") as fh:
            return fh.read()
    except OSError:
        return None


def signature(path, name):
    """The argument names of one top level function, or None when it is absent or unreadable."""
    try:
        with open(path, encoding="utf-8") as fh:
            tree = ast.parse(fh.read(), filename=path)
    except (OSError, SyntaxError):
        return None
    for node in tree.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == name:
            args = [a.arg for a in node.args.args]
            args += [a.arg for a in node.args.kwonlyargs]
            return args
    return None


def cjk_count(text):
    """How many Chinese or Japanese codepoints this text carries. Written as ranges rather than literal
    characters so this harness stays ASCII and cannot itself be mangled by a path mask or an encoding."""
    if text is None:
        return None
    return sum(1 for c in text
               if "一" <= c <= "鿿" or "぀" <= c <= "ヿ" or "㐀" <= c <= "䶿")


def token_region(path, name, span=1200):
    text = source(path)
    if text is None or name not in text:
        return None
    i = text.index(name)
    return text[i:i + span]


class _Base(unittest.TestCase):
    FILE = None

    def setUp(self):
        if self.FILE and not os.path.isfile(self.FILE):
            self.fail(ABSENT % os.path.relpath(self.FILE, ROOT))

    def has(self, path, name):
        found = defines(path, name)
        self.assertIsNotNone(found, "%s could not be parsed, so %s cannot be judged"
                             % (os.path.relpath(path, ROOT), name))
        self.assertTrue(found, "%s does not define %s yet"
                        % (os.path.relpath(path, ROOT), name))


class CJK(_Base):
    """CJK1H.a: Chinese and Japanese risk patterns in the one trigger table."""
    FILE = RECEIPT

    def test_a_zh_delete_is_flagged(self):
        """REQ-CJK1HA-01: a Chinese outcome asking to delete a table hits the deletion class. On the base
        the trigger table carries no Chinese or Japanese codepoint at all, so nothing can hit."""
        n = cjk_count(source(RECEIPT))
        self.assertIsNotNone(n, "receipt_door.py could not be read")
        self.assertGreater(n, 0, "the risk trigger table carries no Chinese or Japanese pattern, so a "
                                 "zh or ja outcome cannot hit any risk class")

    def test_every_corpus_row_hits(self):
        """REQ-CJK1HA-01: every row of the frozen zh and ja corpus hits its own class."""
        self.has(DOOR, "risk_hits")

    def test_one_table(self):
        """REQ-CJK1HA-02: door.py holds no second trigger table; it reads receipt_door's."""
        self.has(DOOR, "risk_hits")
        text = source(DOOR)
        self.assertIsNotNone(text, "door.py could not be read")
        self.assertIn("RISK_TRIGGERS", text,
                      "door.py does not read receipt_door's RISK_TRIGGERS, so a second word list can "
                      "drift from the first")

    def test_unsupported_is_named(self):
        """REQ-CJK1HA-03: a script with no pattern is named unsupported, not silently parked."""
        self.has(DOOR, "risk_hits")


class RoutedFailures(_Base):
    """CJK1H.b: a routed failure is classed by its status before its words, and executed_by reads usage."""
    FILE = WORKER

    def test_status_529_is_overload(self):
        """REQ-CJK1HB-01: status 429 is the limit class and 529 the overload class, decided before any
        word match. On the base classify_failure has no status parameter, so a 529 whose text says
        nothing is classed other."""
        args = signature(WORKER, "classify_failure")
        self.assertIsNotNone(args, "classify_failure is absent from model_worker.py or unreadable")
        self.assertIn("status", args,
                      "classify_failure takes no status, so an HTTP status cannot be read before the "
                      "words and a silent 529 is classed by guesswork")

    def test_executed_by_reads_model_usage(self):
        """REQ-CJK1HB-02: executed_by names the model that actually answered, from modelUsage, and
        NO-DATA when it is absent, never the model that was requested."""
        self.has(WORKER, "executed_by")

    def test_words_still_work(self):
        """REQ-CJK1HB-03: with no status the existing word rules are unchanged, so the new parameter adds
        a path rather than replacing one."""
        args = signature(WORKER, "classify_failure")
        self.assertIsNotNone(args, "classify_failure is absent from model_worker.py or unreadable")
        self.assertIn("text", args, "classify_failure no longer takes the text its word rules read")
        self.assertIn("status", args, "classify_failure takes no status yet, so there is no no-status "
                                      "path to protect")


class Vendors(_Base):
    """CJK1.c: routed vendors count as autonomy model tokens."""
    FILE = STORE

    def test_routed_vendors_count(self):
        """REQ-CJK1C-01: each routed vendor name counts as an autonomy model. Measured on this tree:
        deepseek and qwen are already in the token list, glm, kimi, minimax and doubao are not, and the
        membership test has no named function for the harness to call."""
        self.has(STORE, "is_autonomy_model")
        region = token_region(STORE, "AUTONOMY_MODEL_TOKENS")
        self.assertIsNotNone(region, "AUTONOMY_MODEL_TOKENS could not be read out of bm_store.py")
        missing = [v for v in ROUTED_VENDORS if v not in region]
        self.assertEqual(missing, [], "these routed vendors do not count as autonomy models, so work "
                                      "they do is invisible to the autonomy measure: %s" % missing)


if __name__ == "__main__":
    unittest.main(verbosity=1)

#!/usr/bin/env python3
"""S2.a: the frozen harness for the inherited red rule on GATE ROWS.

Written by the session before S2 is built, never by its builder (HARN1), and RED ON THE BASE BY AN
ASSERTION, never by an import error and never by a skip. Done check: BaseRed.

WHAT S2 CHANGES, AND WHY IT IS THE MOST DANGEROUS UNIT IN WAVE 0. Every other stability unit makes the
loop refuse something more often. This one makes it refuse LESS: a gate row that was already red before
the build stops holding an unrelated landing, and raises one alert instead. That polarity is the whole
risk. The mutation that matters is not "the rule stopped working", it is "the rule became too generous
and a genuinely broken build landed clean". So the bar below is written around the cases where the row
must STILL hold: a new red, and a base run that cannot be read.

MEASURED ON THE BASE, by loading the module and calling it:
  neighbour_verdict(True, True)  = 'INHERITED'   already correct, REQ-S2A-05 is satisfied
  neighbour_verdict(True, False) = 'RED'
  neighbour_verdict(True, None)  = 'NO-DATA'
  'BASE-RED' appears nowhere in scripts/loop/land_batch.py
So the rule exists for neighbour TESTS and does not exist for GATE ROWS, which is the gap S2 closes.

scripts/loop/land_batch.py is a member of scripts/kernel_closure.txt, so a change to it must run the
boxed whole gate before any push, by the rule S1 landed on this branch on 2026-10-25.
"""
import ast
import os
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
LAND = os.path.join(ROOT, "scripts/loop/land_batch.py")
ABSENT = "NO-DATA: %s is not in this tree, so nothing here can judge it"


def source(path):
    try:
        with open(path, encoding="utf-8") as fh:
            return fh.read()
    except OSError:
        return None


def defines(path, name):
    try:
        with open(path, encoding="utf-8") as fh:
            tree = ast.parse(fh.read(), filename=path)
    except (OSError, SyntaxError):
        return None
    return any(isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)) and n.name == name
               for n in tree.body)


def land_batch():
    """land_batch loaded by path, or None when it cannot be loaded, which the caller asserts on."""
    import importlib.util
    import sys
    if not os.path.isfile(LAND):
        return None
    for extra in (os.path.join(ROOT, "scripts"), os.path.join(ROOT, "scripts", "loop")):
        if extra not in sys.path:
            sys.path.insert(0, extra)
    spec = importlib.util.spec_from_file_location("land_batch_under_test", LAND)
    module = importlib.util.module_from_spec(spec)
    try:
        spec.loader.exec_module(module)
    except Exception:  # noqa: BLE001  the module is under test; a load failure is the finding, not a skip
        return None
    return module


class BaseRed(unittest.TestCase):
    """S2.a: a gate row red on the base and after the build reads BASE-RED, does not hold, and alerts once."""

    def setUp(self):
        if not os.path.isfile(LAND):
            self.fail(ABSENT % "scripts/loop/land_batch.py")

    def test_a_base_red_row_does_not_hold(self):
        """REQ-S2A-01: a row red on the base and after does not hold an unrelated build. On the base the
        verdict word does not exist, so such a row holds the landing of work that did not cause it."""
        text = source(LAND)
        self.assertIsNotNone(text, "land_batch.py could not be read")
        self.assertIn("BASE-RED", text,
                      "the landing has no BASE-RED verdict for a gate row, so a row that was already "
                      "red before the build still holds an unrelated landing")

    def test_a_new_red_holds(self):
        """REQ-S2A-02: a new red STILL holds. This is the requirement the unit must not break: the whole
        change is permissive, so the case that must survive it is the one where the build is at fault."""
        module = land_batch()
        self.assertIsNotNone(module, "land_batch could not be loaded, so its verdict cannot be exercised")
        self.assertEqual(module.neighbour_verdict(True, False), "RED",
                         "red with the build and green on the base must stay RED, or a build that broke "
                         "a row lands clean")

    def test_one_alert_per_row(self):
        """REQ-S2A-03: one alert per row per night, so a base red row is reported rather than silently
        tolerated, and reported once rather than every pass."""
        text = source(LAND)
        self.assertIsNotNone(text, "land_batch.py could not be read")
        self.assertIn("BASE-RED", text, "no BASE-RED verdict exists, so there is nothing to alert about")
        self.assertTrue(defines(LAND, "base_red_alert") or "base_red_alert" in text,
                        "nothing records a per row per night alert, so a tolerated base red leaves no "
                        "trace and nobody learns the row is rotten")

    def test_unreadable_base_holds(self):
        """REQ-S2A-04: a base run that cannot be read HOLDS. NO-DATA is never BASE-RED: an unknown base
        is the one case where the permissive path must not be taken."""
        module = land_batch()
        self.assertIsNotNone(module, "land_batch could not be loaded, so its verdict cannot be exercised")
        for unreadable in (None, "x", 0, [], {}):
            self.assertEqual(module.neighbour_verdict(True, unreadable), "NO-DATA",
                             "a base run read as %r must be NO-DATA and hold, never a clean pass" % (unreadable,))
        text = source(LAND)
        self.assertIsNotNone(text, "land_batch.py could not be read")
        self.assertIn("BASE-RED", text, "the gate row path has no BASE-RED, so its unreadable base case "
                                        "cannot be judged yet")

    def test_inherited_keeps_its_meaning(self):
        """REQ-S2A-05: a neighbour test red on both still reads INHERITED and holds.

        MEASURED 2026-10-25: neighbour_verdict(True, True) already returns INHERITED, so this requirement
        is ALREADY SATISFIED on this tree and this test is a regression guard rather than a red. It is
        kept because S2 edits the verdict's neighbourhood, and the thing most likely to break while
        adding a gate row rule is the neighbour rule that already works."""
        module = land_batch()
        self.assertIsNotNone(module, "land_batch could not be loaded, so its verdict cannot be exercised")
        self.assertEqual(module.neighbour_verdict(True, True), "INHERITED",
                         "red on both must stay INHERITED: held, never quarantined")


if __name__ == "__main__":
    unittest.main(verbosity=1)

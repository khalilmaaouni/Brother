#!/usr/bin/env python3
"""LA5.c: after_held reads the unit's after list and holds admission like depends_on.

The pool already routes admission through depends_held, which on this tree reads the union of depends_on and
after with the exact strings the landed union suite locks. This module adds after_held beside it with the
after specific reasons the LA5.c spec names, and proves the after list holds. No process is started.

Run: python3 -B scripts/loop/test_runner_pool_after.py
"""
import os
import sys
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import runner_pool as RP  # noqa: E402


def _unit(uid, state="OPEN", subs=None, evidence="", **extra):
    subs = [uid + ".1"] if subs is None else subs
    return dict({"id": uid, "state": state, "sub_units": subs, "evidence": evidence}, **extra)


def _landed(uid, **extra):
    return _unit(uid, state="DONE", **extra)


class AfterHeldTests(unittest.TestCase):
    def held(self, a, *others):
        return RP.after_held("A.1", a, [a] + list(others))

    def test_an_after_unit_not_landed_holds_the_sub_unit_by_name(self):
        why = self.held(_unit("A", after=["B"]), _unit("B"))
        self.assertEqual(why, "A.1 held: after B (OPEN), not yet landed")

    def test_an_after_unit_done_releases_it(self):
        self.assertEqual(self.held(_unit("A", after=["B"]), _landed("B")), "")

    def test_every_sub_unit_of_the_after_unit_landed_releases_it(self):
        b = _unit("B", subs=["B.1"], evidence="B.1 landed at 1")
        self.assertEqual(self.held(_unit("A", after=["B"]), b), "")

    def test_an_after_unit_the_plan_does_not_hold_holds(self):
        why = self.held(_unit("A", after=["GHOST"]))
        self.assertEqual(why, "A.1 held: after GHOST, which the plan does not hold")

    def test_a_unit_naming_itself_in_after_holds(self):
        why = self.held(_unit("A", after=["A"]))
        self.assertEqual(why, "NO-DATA unit A names itself in after")

    def test_an_unreadable_after_list_holds_with_no_data(self):
        for bad in ("B", ["B", 7], [""], ["  "], [None], [["B"]], {"B": 1}, 7, True):
            with self.subTest(after=bad):
                why = self.held(_unit("A", after=bad), _landed("B"))
                self.assertEqual(why, "NO-DATA unreadable after on unit A")

    def test_a_retired_after_unit_releases_and_a_deferred_one_holds(self):
        self.assertEqual(self.held(_unit("A", after=["B"]), _unit("B", state="RETIRED")), "")
        why = self.held(_unit("A", after=["B"]), _unit("B", state="DEFERRED"))
        self.assertEqual(why, "A.1 held: after B, DEFERRED to a later release")

    def test_depends_on_text_is_unchanged(self):
        a = _unit("A", depends_on=["B"])
        why = RP.depends_held("A.1", a, [a, _unit("B")])
        self.assertEqual(why, "A.1 held: unit A depends on B (OPEN), not yet landed")

    def test_the_pool_pass_prints_the_held_after(self):
        plan = {"units": [_unit("A", after=["B"]), _unit("B")]}
        why = RP.admissible(plan, "A.1")
        self.assertIn("held:", why)
        self.assertIn("B", why)

    def test_after_held_never_crashes_on_hostile_input(self):
        nasty = (None, 0, True, -1, float("nan"), "", "x", b"x", [], ["x"], {}, {"a": 1}, (), {1, 2}, object())
        for value in nasty:
            for args in ((value, _unit("A"), [_unit("A")]),
                         ("A.1", value, [_unit("A")]),
                         ("A.1", _unit("A"), value)):
                try:
                    out = RP.after_held(*args)
                except ValueError:  # sbe: allow-silent the refusal is one of the two outcomes this hostile value test accepts
                    continue
                self.assertIsInstance(out, str)


if __name__ == "__main__":
    unittest.main()

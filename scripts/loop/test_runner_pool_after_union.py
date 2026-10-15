#!/usr/bin/env python3
"""AFTER-UNION: the pool holds a unit on the UNION of its depends_on and its after (2026-10-14).

Two release plans in a row (1.1.3, 1.1.4) were written with `after` filled and `depends_on` empty, and
runner_pool.depends_held read only depends_on, so the pool admitted units out of order on shared files. The fix is at
the one function every admission routes through (admissible calls depends_held once). Entry points under test:
runner_pool.admissible (where the hold lives) and depends_held beside it. Fixtures are plain dicts and one temp spec
file; no HOME file is read.

Run: python3 -B scripts/loop/test_runner_pool_after_union.py
"""
import os
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.dirname(HERE))

import runner_pool as RP  # noqa: E402  (the module under test, beside this file)


def unit(uid, state="OPEN", subs=None, evidence="", **extra):
    subs = [uid + ".1"] if subs is None else subs
    return dict({"id": uid, "state": state, "sub_units": subs, "evidence": evidence}, **extra)


def landed(uid, **extra):
    return unit(uid, state="DONE", **extra)


class DependsHeldReadsTheUnion(unittest.TestCase):
    def held(self, a, *others):
        return RP.depends_held("A.1", a, [a] + list(others))

    def test_a_predecessor_only_in_after_holds_the_unit(self):
        why = self.held(unit("A", after=["B"]), unit("B"))
        self.assertEqual(why, "A.1 held: unit A depends on B (OPEN), not yet landed")

    def test_a_predecessor_only_in_depends_on_still_holds_the_unit(self):
        why = self.held(unit("A", depends_on=["B"]), unit("B"))
        self.assertEqual(why, "A.1 held: unit A depends on B (OPEN), not yet landed")

    def test_the_same_name_in_both_lists_is_one_hold(self):
        why = self.held(unit("A", after=["B"], depends_on=["B"]), unit("B"))
        self.assertEqual(why, "A.1 held: unit A depends on B (OPEN), not yet landed")

    def test_two_different_predecessors_one_per_list_both_count(self):
        a = unit("A", after=["B"], depends_on=["C"])
        self.assertIn(" B ", self.held(a, unit("B"), landed("C")))          # only after is unlanded
        self.assertIn(" C ", self.held(a, landed("B"), unit("C")))          # only depends_on is unlanded
        self.assertEqual(self.held(a, landed("B"), landed("C")), "")        # control: both landed releases

    def test_a_landed_predecessor_in_after_releases_the_unit(self):
        self.assertEqual(self.held(unit("A", after=["B"]), landed("B")), "")
        done_by_sub_units = unit("B", subs=["B.1"], evidence="B.1 landed at 1")
        self.assertEqual(self.held(unit("A", after=["B"]), done_by_sub_units), "")

    def test_a_retired_predecessor_in_after_releases_the_unit(self):
        self.assertEqual(self.held(unit("A", after=["B"]), unit("B", state="RETIRED")), "")

    def test_a_deferred_predecessor_in_after_holds_with_its_reason(self):
        why = self.held(unit("A", after=["B"]), unit("B", state="DEFERRED"))
        self.assertEqual(why, "A.1 held: unit A depends on B, DEFERRED to a later release")

    def test_a_predecessor_in_after_the_plan_does_not_hold_holds_the_unit(self):
        why = self.held(unit("A", after=["GHOST"]))
        self.assertEqual(why, "A.1 held: unit A depends on 'GHOST', which the plan does not hold")

    def test_empty_and_absent_lists_hold_nothing(self):
        self.assertEqual(self.held(unit("A")), "")
        self.assertEqual(self.held(unit("A", after=[], depends_on=[])), "")
        self.assertEqual(self.held(unit("A", after=None, depends_on=None)), "")

    def test_an_unreadable_after_holds_and_names_the_field(self):
        for bad in ("B", ["B", 7], [""], ["  "], [None], [["B"]], {"B": 1}, 7, True):
            with self.subTest(after=bad):
                why = self.held(unit("A", after=bad), landed("B"))
                self.assertEqual(why, "NO-DATA unreadable after on unit A")

    def test_an_unreadable_depends_on_keeps_its_message_whatever_after_says(self):
        why = self.held(unit("A", depends_on="B", after=["B"]), landed("B"))
        self.assertEqual(why, "NO-DATA unreadable depends_on on unit A")

    def test_a_good_depends_on_does_not_hide_an_unreadable_after(self):
        why = self.held(unit("A", depends_on=["B"], after=[3]), landed("B"))
        self.assertEqual(why, "NO-DATA unreadable after on unit A")

    def test_both_lists_unreadable_reports_depends_on_first(self):
        why = self.held(unit("A", depends_on="B", after="B"), landed("B"))
        self.assertEqual(why, "NO-DATA unreadable depends_on on unit A")

    def test_with_both_lists_unlanded_the_depends_on_predecessor_is_named_first(self):
        why = self.held(unit("A", depends_on=["B"], after=["C"]), unit("B"), unit("C"))
        self.assertEqual(why, "A.1 held: unit A depends on B (OPEN), not yet landed")


class AdmissibleHoldsOnAfter(unittest.TestCase):
    """The entry point: the pool's one admission function, with a spec the landing gate accepts (the control)."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory(prefix="rootfix_pool_")
        self.addCleanup(self._tmp.cleanup)
        self.spec = os.path.join(self._tmp.name, "A.md")
        with open(self.spec, "w", encoding="utf-8") as fh:
            fh.write("# A\n### A.1\n`scripts/a_1.py` existing\nDone check: `python3 -B scripts/test_a_1.py`\n")

    def plan(self, a, *others):
        return {"units": [dict(a, spec=self.spec)] + list(others)}

    def test_the_control_admits_a_unit_with_no_predecessor(self):
        self.assertEqual(RP.admissible(self.plan(unit("A")), "A.1"), "")

    def test_a_unit_whose_predecessor_is_only_in_after_is_not_admitted(self):
        why = RP.admissible(self.plan(unit("A", after=["B"]), unit("B")), "A.1")
        self.assertEqual(why, "A.1 held: unit A depends on B (OPEN), not yet landed")

    def test_the_same_unit_is_admitted_once_the_predecessor_landed(self):
        self.assertEqual(RP.admissible(self.plan(unit("A", after=["B"]), landed("B")), "A.1"), "")

    def test_an_after_naming_a_unit_the_plan_does_not_hold_is_not_admitted(self):
        why = RP.admissible(self.plan(unit("A", after=["GHOST"])), "A.1")
        self.assertIn("which the plan does not hold", why)

    def test_a_deferred_predecessor_in_after_is_not_admitted(self):
        why = RP.admissible(self.plan(unit("A", after=["B"]), unit("B", state="DEFERRED")), "A.1")
        self.assertIn("DEFERRED to a later release", why)

    def test_a_retired_predecessor_in_after_does_not_hold(self):
        self.assertEqual(RP.admissible(self.plan(unit("A", after=["B"]), unit("B", state="RETIRED")), "A.1"), "")

    def test_an_unreadable_after_is_no_data_and_admits_nothing(self):
        why = RP.admissible(self.plan(unit("A", after="B")), "A.1")
        self.assertEqual(why, "NO-DATA unreadable after on unit A")


if __name__ == "__main__":
    unittest.main()

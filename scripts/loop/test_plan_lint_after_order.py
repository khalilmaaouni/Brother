#!/usr/bin/env python3
"""AFTER-ORDER: a unit's `after` must be a list of unit ids the plan holds, all of them also in its `depends_on`
(2026-10-14). The pool holds a unit on depends_on (and, since the same day, on the union); a plan whose `after` and
`depends_on` disagree states its order twice and means neither. Entry points under test: plan_lint.lint_plan and
plan_lint.admission (where the rule is wired), with after_order_findings beside them. Fixtures are temp files; no HOME
file is read.

Run: python3 -B scripts/loop/test_plan_lint_after_order.py
"""
import json
import os
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(os.path.dirname(HERE))
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.dirname(HERE))

import plan_lint  # noqa: E402  (the module under test, beside this file)

RULE = "AFTER-ORDER"


OK_TEST = ("import unittest\n\n\nclass T(unittest.TestCase):\n    def test_x(self):\n        self.assertTrue(True)\n\n\n"
           "if __name__ == \"__main__\":\n    unittest.main()\n")


def unit(uid, state="OPEN", **extra):
    """A unit with no sub units (so no section is judged) and a done check the closer accepts, so AFTER-ORDER is the
    only rule that can answer."""
    return dict({"id": uid, "state": state, "sub_units": [], "evidence": "", "owns": ["scripts/%s.py" % uid.lower()],
                 "done_check": "python3 -B scripts/test_t.py"}, **extra)


class Fixture(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory(prefix="rootfix_after_")
        self.addCleanup(self._tmp.cleanup)
        self.root = self._tmp.name
        self.ledger = os.path.join(self.root, "absent-ledger.jsonl")   # absent: the prior, never a HOME file
        os.makedirs(os.path.join(self.root, "scripts"))
        with open(os.path.join(self.root, "scripts", "test_t.py"), "w", encoding="utf-8") as fh:
            fh.write(OK_TEST)

    def report(self, units, scope="."):
        path = os.path.join(self.root, "plan.json")
        with open(path, "w", encoding="utf-8") as fh:
            json.dump({"units": units}, fh)
        return plan_lint.lint_plan(path, root=self.root, scope=scope, ledger=None, env={})

    def report_hits(self, units, scope="."):
        return [f for f in self.report(units, scope)["findings"] if f["rule"] == RULE]

    def admit_hits(self, units, uid):
        by_id = {u["id"]: u for u in units}
        lines, why = plan_lint.admission({"units": units}, by_id[uid], uid + ".1", self.root, {}, ledger=self.ledger)
        return [l for l in lines if " %s: " % RULE in l], why


class TestCleanPlans(Fixture):
    def test_after_equal_to_depends_on_is_clean(self):
        units = [unit("A"), unit("B", after=["A"], depends_on=["A"]),
                 unit("C", after=["A", "B"], depends_on=["B", "A"])]   # order inside the lists is not the rule
        self.assertEqual(self.report_hits(units), [])
        self.assertEqual(self.admit_hits(units, "C"), ([], ""))

    def test_empty_absent_and_null_lists_are_clean(self):
        units = [unit("A"), unit("B", after=[], depends_on=[]), unit("C", after=None, depends_on=None),
                 unit("D", depends_on=["A"])]   # a depends_on with no after states one order: nothing to disagree with
        self.assertEqual(self.report_hits(units), [])

    def test_after_inside_a_longer_depends_on_is_clean(self):
        units = [unit("A"), unit("B"), unit("C", after=["A"], depends_on=["A", "B"])]
        self.assertEqual(self.report_hits(units), [])

    def test_predecessors_in_any_state_are_judged_by_presence_only(self):
        units = [unit("A", state="DONE"), unit("B", state="RETIRED"), unit("C", state="DEFERRED"),
                 unit("D", after=["A", "B", "C"], depends_on=["A", "B", "C"])]
        self.assertEqual(self.report_hits(units), [])

    def test_a_duplicate_name_in_after_is_one_name(self):
        units = [unit("A"), unit("B", after=["A", "A"], depends_on=["A"])]
        self.assertEqual(self.report_hits(units), [])

    def test_a_done_unit_is_not_judged(self):
        units = [unit("A"), unit("B", state="DONE", after=["A"], depends_on=[]), unit("C", state="DONE", after="A")]
        self.assertEqual(self.report_hits(units), [])


class TestBlocking(Fixture):
    FIX = "copy the unit's after list into its depends_on in the plan"

    def test_after_not_in_depends_on_is_blocking_and_names_the_fix(self):
        units = [unit("A"), unit("B", after=["A"], depends_on=[])]
        hits = self.report_hits(units)
        self.assertEqual([(f["unit"], f["severity"]) for f in hits], [("B", "BLOCKING")])
        self.assertEqual(hits[0]["text"], "the unit after names A, which its depends_on does not")
        self.assertTrue(hits[0]["fix"].startswith(self.FIX), hits[0]["fix"])
        lines, why = self.admit_hits(units, "B")
        self.assertEqual(len(lines), 1, lines)
        self.assertTrue(lines[0].startswith("LINT BLOCKING B - AFTER-ORDER: the unit after names A"), lines[0])
        self.assertTrue(why.startswith("LINT AFTER-ORDER: the unit after names A"), why)

    def test_one_missing_name_among_present_ones_names_only_the_missing(self):
        units = [unit("A"), unit("B"), unit("C", after=["A", "B"], depends_on=["A"])]
        hits = self.report_hits(units)
        self.assertEqual([f["text"] for f in hits], ["the unit after names B, which its depends_on does not"])

    def test_every_missing_name_is_listed_once(self):
        units = [unit("A"), unit("B"), unit("C", after=["A", "B", "A"], depends_on=[])]
        self.assertEqual([f["text"] for f in self.report_hits(units)],
                         ["the unit after names A, B, which its depends_on does not"])

    def test_after_naming_a_unit_the_plan_does_not_hold_is_blocking(self):
        units = [unit("A", after=["GHOST"], depends_on=["GHOST"])]   # copied into depends_on: still no such unit
        hits = self.report_hits(units)
        self.assertEqual([(f["severity"], f["text"]) for f in hits],
                         [("BLOCKING", "the unit after names GHOST, which the plan does not hold")])
        self.assertTrue(self.admit_hits(units, "A")[1].startswith("LINT AFTER-ORDER: "))

    def test_a_ghost_missing_from_depends_on_too_is_two_findings(self):
        hits = self.report_hits([unit("A", after=["GHOST"], depends_on=[])])
        self.assertEqual(len(hits), 2, hits)
        self.assertEqual({f["severity"] for f in hits}, {"BLOCKING"})

    def test_after_that_is_not_a_list_of_text_is_blocking(self):
        for bad in ("A", ["A", 3], [""], ["  "], [None], [["A"]], {"A": 1}, 7, True):
            with self.subTest(after=bad):
                units = [unit("A"), unit("B", after=bad, depends_on=["A"])]
                hits = self.report_hits(units)
                self.assertEqual([(f["unit"], f["severity"], f["text"]) for f in hits],
                                 [("B", "BLOCKING", "the unit after is not a list of unit ids (text entries)")])
                lines, why = self.admit_hits(units, "B")   # SHARED-FILE answers NO-DATA first for this shape: `why` is its
                self.assertEqual(len(lines), 1, lines)
                self.assertTrue(lines[0].startswith("LINT BLOCKING B - AFTER-ORDER: "), lines)
                self.assertNotEqual(why, "")

    def test_a_depends_on_that_is_not_a_list_cannot_hold_the_after(self):
        for bad in ("A", 7, {"A": 1}, None):
            with self.subTest(depends_on=bad):
                hits = self.report_hits([unit("A"), unit("B", after=["A"], depends_on=bad)])
                self.assertEqual([f["severity"] for f in hits], ["BLOCKING"])

    def test_a_deferred_or_retired_unit_still_needs_its_after_copied(self):
        units = [unit("A", state="DEFERRED"), unit("B", state="DEFERRED", after=["A"], depends_on=[])]
        self.assertEqual(len(self.report_hits(units)), 1)   # the rule judges the order, not the release

    def test_a_unit_with_no_text_id_is_left_to_the_per_unit_rules(self):
        self.assertEqual(self.report_hits([dict(unit("A"), id=None, after=["X"])]), [])


class TestEntryPoints(Fixture):
    def test_lint_plan_scope_filters_by_unit(self):
        units = [unit("A"), unit("B", after=["A"]), unit("C", after=["A"])]
        self.assertEqual([f["unit"] for f in self.report_hits(units)], ["B", "C"])
        self.assertEqual([f["unit"] for f in self.report_hits(units, scope="^B$")], ["B"])
        self.assertEqual(self.report_hits(units, scope="^A$"), [])

    def test_admission_reports_only_the_admitted_units_own_finding(self):
        units = [unit("A"), unit("B", after=["A"]), unit("C", after=["A"])]
        lines, why = self.admit_hits(units, "B")
        self.assertEqual(len(lines), 1, lines)
        self.assertIn(" B ", lines[0])
        self.assertEqual(self.admit_hits(units, "A"), ([], ""))   # a clean unit beside two flagged ones

    def test_admission_reads_the_plan_fresh_each_call(self):
        units = [unit("A"), unit("B", after=["A"], depends_on=["A"])]
        self.assertEqual(self.admit_hits(units, "B"), ([], ""))
        units[1]["depends_on"] = []   # edited by a second actor between two calls
        self.assertTrue(self.admit_hits(units, "B")[1].startswith("LINT AFTER-ORDER"))

    def test_the_summary_counts_the_finding_as_blocking(self):
        report = self.report([unit("A"), unit("B", after=["A"])])
        self.assertEqual(len([f for f in report["findings"] if f["severity"] == "BLOCKING" and f["rule"] == RULE]), 1)

    def test_hostile_plans_never_raise_from_the_helper_or_pass_through_admission(self):
        with self.assertRaises(ValueError):
            plan_lint.after_order_findings({"units": {}})
        with self.assertRaises(ValueError):
            plan_lint.after_order_findings(None)
        self.assertEqual([f["severity"] for f in plan_lint.after_order_findings({"units": [unit("B", after=("A",))]})],
                         ["BLOCKING"])   # a tuple is no list (a JSON plan cannot carry one; a caller's dict can)
        self.assertEqual(plan_lint.after_order_findings({"units": [None, 7, "x", []]}), [])   # non records: other rules'
        lines, why = plan_lint.admission({"units": {}}, unit("A"), "A.1", self.root, {}, ledger=self.ledger)
        self.assertEqual(why, "")   # admission ignores a plan whose units are no list; it never raises

    def test_every_finding_carries_its_unit_in_pair(self):
        for f in plan_lint.after_order_findings({"units": [unit("A"), unit("B", after=["A", "Z"])]}):
            self.assertEqual(f["pair"], ["B"])


@unittest.skipUnless(os.path.isdir(os.path.join(ROOT, "docs", "plan", "specs")), "docs/plan/specs is hub only")
class TestRealPlan(unittest.TestCase):
    def test_the_1_1_4_plan_has_no_after_order_finding(self):
        plan = os.path.join(ROOT, "docs", "plan", "BROTHER-1.1.4-WBS.json")
        if not os.path.isfile(plan):
            self.skipTest("the 1.1.4 plan is not in this tree")
        report = plan_lint.lint_plan(plan, root=ROOT, scope=".", ledger=None, env={})
        self.assertEqual([f for f in report["findings"] if f["rule"] == RULE], [])
        with open(plan, encoding="utf-8") as fh:
            units = json.load(fh)["units"]
        for u in units:
            if u.get("state") != "DONE":
                self.assertTrue(set(u.get("after") or []) <= set(u.get("depends_on") or []), u["id"])


if __name__ == "__main__":
    unittest.main()

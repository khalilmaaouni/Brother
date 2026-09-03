"""What the Work contract must refuse.

Refusal is the feature. A Work record that accepts anything is a file format
rather than a contract, and every clause here exists because something
downstream breaks without it, usually invisibly and usually at run time.
"""
import json
import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import work_record as W  # noqa: E402


def unit(**kw):
    d = {"id": "U1", "title": "a unit", "done_check": "pytest",
         "owns": ["a.py"], "depends_on": []}
    d.update(kw)
    return d


class EveryClauseExistsBecauseSomethingBreaksWithoutIt(unittest.TestCase):
    def test_a_unit_with_no_done_check_is_refused(self):
        rec, problems = W.create("o", [unit(done_check="")])
        self.assertIsNone(rec)
        self.assertIn("nothing to verify", problems[0])

    def test_a_unit_with_no_write_scope_is_refused(self):
        """The scope audit returns NO-DATA without one, which blocks
        integration, so an undeclared unit should never be dispatched."""
        rec, problems = W.create("o", [unit(owns=[])])
        self.assertIsNone(rec)
        self.assertIn("NO-DATA", problems[0])

    def test_a_write_scope_escaping_the_repository_is_refused(self):
        """git status cannot see a write outside the tree, so an escaping
        scope would be invisible to the audit and every control after it.
        Found live: a unit declaring ../escaped.txt reported INTEGRATED at
        exit 0 while its file landed outside the repository."""
        for bad in ("../escaped.txt", "/tmp/escaped.txt", "a/../../up.txt"):
            rec, problems = W.create("o", [unit(owns=[bad])])
            self.assertIsNone(rec, bad)
            self.assertIn("escaping the repository", problems[0])

    def test_a_dotdot_inside_the_tree_is_still_accepted(self):
        rec, problems = W.create("o", [unit(owns=["a/../b.txt"])])
        self.assertEqual(problems, [])
        self.assertIsNotNone(rec)

    def test_a_dangling_dependency_is_refused(self):
        """Invisible at run time: it drops the unit from the ready set forever
        and nothing says so."""
        rec, problems = W.create("o", [unit(depends_on=["GHOST"])])
        self.assertIsNone(rec)
        self.assertIn("GHOST", problems[0])

    def test_a_cycle_is_refused_and_the_cycle_is_named(self):
        rec, problems = W.create("o", [
            unit(id="A", owns=["a"], depends_on=["B"]),
            unit(id="B", owns=["b"], depends_on=["A"])])
        self.assertIsNone(rec)
        self.assertTrue(any("A -> B -> A" in p for p in problems), problems)

    def test_a_longer_cycle_is_also_caught(self):
        rec, problems = W.create("o", [
            unit(id="A", owns=["a"], depends_on=["C"]),
            unit(id="B", owns=["b"], depends_on=["A"]),
            unit(id="C", owns=["c"], depends_on=["B"])])
        self.assertIsNone(rec)
        self.assertTrue(any("cycle" in p for p in problems))

    def test_two_units_sharing_an_id_are_refused(self):
        """Claims are keyed by id, so this is two workers on one lease."""
        rec, problems = W.create("o", [unit(id="U1"), unit(id="U1", owns=["b"])])
        self.assertIsNone(rec)
        self.assertIn("twice", problems[0])

    def test_no_units_at_all_is_refused(self):
        rec, problems = W.create("o", [])
        self.assertIsNone(rec)
        self.assertIn("not Work yet", problems[0])

    def test_no_outcome_is_refused(self):
        rec, problems = W.create("", [unit()])
        self.assertIsNone(rec)
        self.assertIn("nothing says what this Work is for", problems[0])


class ItReportsEVERYProblemNotTheFirst(unittest.TestCase):
    """A caller fixing one at a time learns the contract slowly, by being
    refused over and over."""

    def test_several_problems_come_back_together(self):
        _rec, problems = W.create("o", [
            unit(id="", done_check="", owns=[])])
        self.assertGreaterEqual(len(problems), 3)


class AWellFormedUnitIsAccepted(unittest.TestCase):
    def test_it_is_accepted_with_no_problems(self):
        rec, problems = W.create("a real outcome", [unit()])
        self.assertEqual(problems, [])
        self.assertIsNotNone(rec)

    def test_the_record_carries_the_outcome_and_a_work_id(self):
        rec, _ = W.create("invoices stop double posting", [unit()])
        self.assertEqual(rec["outcome"], "invoices stop double posting")
        self.assertTrue(rec["work_id"].startswith("W-"))

    def test_it_is_written_in_the_shape_the_scheduler_already_reads(self):
        """A second shape would need a second scheduler."""
        rec, _ = W.create("o", [unit()])
        self.assertIn("rows", rec)
        self.assertIn("features", rec)
        for key in ("id", "title", "status", "depends_on", "owns", "done_check"):
            self.assertIn(key, rec["rows"][0])

    def test_storing_it_writes_a_file_the_scheduler_can_load(self):
        d = tempfile.mkdtemp()
        rec, _ = W.create("o", [unit()], store=d)
        with open(rec["path"], encoding="utf-8") as fh:
            self.assertEqual(json.load(fh)["rows"][0]["id"], "U1")


class TheRealSchedulerReadsIt(unittest.TestCase):
    """The point of the whole module. If graph_loop cannot compute a ready set
    from this, the contract is the wrong shape and everything else is moot."""

    def test_the_scheduler_computes_a_ready_set_and_holds_the_dependent_unit(self):
        import graph_loop
        d = tempfile.mkdtemp()
        rec, problems = W.create("an outcome", [
            unit(id="U1", owns=["a.py"]),
            unit(id="U2", owns=["b.py"]),
            unit(id="U3", owns=["c.py"], depends_on=["U1", "U2"])], store=d)
        self.assertEqual(problems, [])
        plan = graph_loop.plan(graph_loop.load(rec["path"]), slots=3)
        self.assertEqual(sorted(n["id"] for n in plan["batch"]), ["U1", "U2"])
        blocked = [b[0]["id"] for b in plan["blocked"]]
        self.assertEqual(blocked, ["U3"])

    def test_nothing_is_silently_dropped(self):
        """A unit that is neither ready nor reported is the failure the dangling
        edge clause exists to prevent, so it is checked here too."""
        import graph_loop
        d = tempfile.mkdtemp()
        rec, _ = W.create("o", [unit(id="U1", owns=["a"]),
                                unit(id="U2", owns=["b"], depends_on=["U1"])],
                          store=d)
        plan = graph_loop.plan(graph_loop.load(rec["path"]), slots=3)
        accounted = ({n["id"] for n in plan["batch"]}
                     | {b[0]["id"] for b in plan["blocked"]}
                     | {n["id"] for n in plan.get("deferred", [])})
        self.assertEqual(accounted, {"U1", "U2"})
        self.assertEqual(plan.get("unknown_deps"), [])


class ItDoesNotPretendToDecomposeEnglish(unittest.TestCase):
    """The opposite claim would be the easiest lie on this board. A script that
    turned a sentence into units would produce confident nonsense with a
    done_check attached, which is worse than refusing."""

    def test_an_outcome_with_no_units_is_NO_DATA_from_the_command(self):
        self.assertEqual(W.main(["an outcome with no decomposition"]), 2)

    def test_no_outcome_at_all_is_NO_DATA(self):
        self.assertEqual(W.main([]), 2)

    def test_the_module_exposes_no_decomposer(self):
        for name in ("decompose", "split", "infer_units", "plan_from_outcome"):
            self.assertFalse(hasattr(W, name), name)


if __name__ == "__main__":
    unittest.main()

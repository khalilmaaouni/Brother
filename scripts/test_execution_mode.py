"""What the DIRECT versus SWARM verdict must keep true.

The dangerous failure here is not a wrong verdict, it is a FLATTERING one: a
tool that says SWARM x3 because three sounds like progress would send three
agents at work one agent could do, which is the exact theatre reviewers punish.
Several tests exist only to prove the degree comes from the real batch, and that
a machine with one slot can never produce a swarm however parallel the graph is.

The other half is NO-DATA. An empty batch supports neither answer, and rounding
it to DIRECT would be just as invented as rounding it to SWARM.
"""
import json
import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import execution_mode as E  # noqa: E402
import graph_loop as G  # noqa: E402

# E100: one sandbox for every temp tree this process makes, removed at exit.
import os as _e100_os, sys as _e100_sys  # noqa: E402
_e100_sys.path.append(_e100_os.path.join(
    _e100_os.path.dirname(_e100_os.path.abspath(__file__)), '.'))
try:  # noqa: E402
    import tmp_sandbox as _e100_tmp
    _e100_tmp.install()
except ImportError:
    # A packager (scripts/export_public.py, make_benchmark_bundle.py)
    # can copy this test without scripts/tmp_sandbox.py beside it. Say
    # so rather than dying: the sandbox is hygiene, not the subject.
    _e100_sys.stderr.write(
        "tmp_sandbox absent: %s leaves its temp trees behind\n"
        % _e100_os.path.basename(__file__))


def node(nid, owns=None, depends=None, status="SCHEDULED", **extra):
    """One unit, shaped the way graph_loop.nodes() reads it."""
    row = {"id": nid, "title": nid, "status": status,
           "depends_on": list(depends or []),
           "owns": [] if owns is None else list(owns),
           "effort_hours": 1, "in_ship_v1": False, "owner": ""}
    row.update(extra)
    return row


def board(rows):
    return {"rows": rows, "features": []}


def verdict(rows, slots):
    return E.decide(G.plan(board(rows), slots))


class TheDegreeComesFromTheRealBatch(unittest.TestCase):
    """Never a constant. Three independent units on three slots is x3; the same
    three units on two slots is x2, because only two of them would actually run."""

    THREE = [node("A", ["a.py"]), node("B", ["b.py"]), node("C", ["c.py"])]

    def test_three_independent_write_sets_on_three_slots_is_swarm_x3(self):
        v = verdict(self.THREE, 3)
        self.assertEqual((v["mode"], v["degree"]), ("SWARM", 3))
        self.assertEqual(E.headline(v), "Execution: SWARM x3")

    def test_the_same_graph_on_two_slots_is_swarm_x2(self):
        v = verdict(self.THREE, 2)
        self.assertEqual((v["mode"], v["degree"]), ("SWARM", 2))

    def test_the_degree_never_exceeds_the_capacity(self):
        for slots in (1, 2, 3):
            self.assertLessEqual(verdict(self.THREE, slots)["degree"], slots)

    def test_a_swarm_names_the_units_it_would_run(self):
        self.assertEqual(verdict(self.THREE, 3)["units"], ["A", "B", "C"])


class OneCoupledChangeIsDirect(unittest.TestCase):
    """Slots are not parallelism. Three units writing one file serialize however
    many agents are free, and dispatching three of them buys a merge conflict."""

    def test_units_sharing_a_write_set_produce_direct(self):
        v = verdict([node("A", ["core.py"]), node("B", ["core.py"]),
                     node("C", ["core.py"])], 3)
        self.assertEqual((v["mode"], v["degree"]), ("DIRECT", 1))

    def test_the_reason_names_the_overlapping_units_not_the_machine(self):
        v = verdict([node("A", ["core.py"]), node("B", ["core.py"])], 3)
        self.assertIn("tightly coupled", v["reason"])
        self.assertIn("B", v["reason"])

    def test_a_lone_ready_unit_says_so_rather_than_blaming_a_conflict(self):
        v = verdict([node("A", ["a.py"])], 3)
        self.assertEqual(v["mode"], "DIRECT")
        self.assertIn("only one unit is ready", v["reason"])


class ACapacityOfOneCanNeverSwarm(unittest.TestCase):
    """The machine this was written on reports one slot because free disk sat in
    graph_loop's cleanup band. A perfectly parallel graph on that machine is
    still DIRECT, and saying otherwise would promise agents that cannot run."""

    def test_a_parallel_graph_on_one_slot_is_direct(self):
        v = verdict([node("A", ["a.py"]), node("B", ["b.py"])], 1)
        self.assertEqual((v["mode"], v["degree"]), ("DIRECT", 1))

    def test_the_reason_names_the_capacity_limit(self):
        v = verdict([node("A", ["a.py"]), node("B", ["b.py"])], 1)
        self.assertIn("capacity is 1", v["reason"])

    def test_the_disk_note_outranks_the_core_note_as_the_cause(self):
        """Naming "8 core(s) ... so 6 slot(s)" when the disk gate did the
        capping sends somebody to buy cores over a full drive."""
        note = E.constraining_note(["8 core(s), reserving 2, so 6 slot(s)",
                                    "CLEANUP BAND: 8.6 GiB free is under 15 GiB"])
        self.assertIn("CLEANUP BAND", note)

    def test_with_no_constraining_note_the_last_note_is_still_named(self):
        self.assertEqual(E.constraining_note(["9.0 GiB free, above the band"]),
                         "9.0 GiB free, above the band")

    def test_an_absent_note_list_does_not_crash(self):
        self.assertEqual(E.constraining_note([]), "capacity note absent")


class UnlocksAreCountedNotClaimed(unittest.TestCase):
    def test_a_unit_waiting_only_on_the_batch_is_reported_as_unlocking(self):
        v = verdict([node("A", ["a.py"]), node("B", ["b.py"]),
                     node("D", ["d.py"], depends=["A", "B"])], 2)
        self.assertEqual(v["unlocks"], ["D"])
        self.assertIn("D", v["reason"])

    def test_a_unit_also_waiting_on_something_outside_the_batch_is_not_counted(self):
        """Half a prerequisite unlocks nothing, and claiming it would oversell
        the swarm with work that stays blocked."""
        rows = [node("A", ["a.py"]), node("B", ["b.py"]),
                node("Z", ["z.py"], status="AWAITING FOUNDER", owner="FOUNDER"),
                node("D", ["d.py"], depends=["A", "Z"])]
        self.assertEqual(verdict(rows, 3)["unlocks"], [])

    def test_a_swarm_with_no_unlocks_says_so_rather_than_inventing_one(self):
        v = verdict([node("A", ["a.py"]), node("B", ["b.py"])], 2)
        self.assertEqual(v["unlocks"], [])
        self.assertIn("no unit waits on another", v["reason"])


class UndispatchableUnitsNeverCountTowardTheDegree(unittest.TestCase):
    """graph_loop already refuses these; the verdict must not quietly re-admit
    them by counting ready units instead of the batch."""

    def test_a_founder_gated_unit_is_not_a_swarm_member(self):
        v = verdict([node("A", ["a.py"]),
                     node("F", ["f.py"], owner="FOUNDER")], 3)
        self.assertEqual((v["mode"], v["units"]), ("DIRECT", ["A"]))

    def test_an_event_waiting_unit_is_not_a_swarm_member(self):
        v = verdict([node("A", ["a.py"]),
                     node("E", ["e.py"], event="the v1.0 tag")], 3)
        self.assertEqual((v["mode"], v["units"]), ("DIRECT", ["A"]))

    def test_a_unit_with_no_declared_scope_is_not_a_swarm_member(self):
        undeclared = node("U")
        undeclared["owns"] = None
        v = verdict([node("A", ["a.py"]), undeclared], 3)
        self.assertEqual((v["mode"], v["units"]), ("DIRECT", ["A"]))


class NoDataIsNeverAPass(unittest.TestCase):
    def test_a_graph_with_nothing_dispatchable_is_NO_DATA(self):
        """Every unit gated. Neither one agent nor three has anything to take,
        and DIRECT would be as invented as SWARM."""
        v = verdict([node("F", ["f.py"], owner="FOUNDER"),
                     node("E", ["e.py"], event="the v1.0 tag")], 3)
        self.assertEqual((v["mode"], v["degree"]), ("NO-DATA", 0))

    def test_NO_DATA_exits_non_zero_through_main(self):
        path = os.path.join(tempfile.mkdtemp(), "r.json")
        with open(path, "w", encoding="utf-8") as fh:
            json.dump(board([node("F", ["f.py"], owner="FOUNDER")]), fh)
        self.assertEqual(E.main(["--roadmap", path]), 2)

    def test_an_empty_graph_is_NO_DATA(self):
        path = os.path.join(tempfile.mkdtemp(), "r.json")
        with open(path, "w", encoding="utf-8") as fh:
            json.dump({"rows": [], "features": []}, fh)
        self.assertEqual(E.main(["--roadmap", path]), 2)

    def test_an_unreadable_graph_is_NO_DATA(self):
        self.assertEqual(E.main(["--roadmap", "/no/such/file.json"]), 2)

    def test_a_graph_that_is_not_json_is_NO_DATA_rather_than_a_traceback(self):
        path = os.path.join(tempfile.mkdtemp(), "r.json")
        with open(path, "w", encoding="utf-8") as fh:
            fh.write("{not json")
        self.assertEqual(E.main(["--roadmap", path]), 2)


class TheMachineReadableVerdictMatchesTheLine(unittest.TestCase):
    """A later screen renders the JSON. If it can disagree with the printed
    line, one of the two is wrong and nobody can tell which."""

    def test_json_carries_every_field_the_line_states(self):
        path = os.path.join(tempfile.mkdtemp(), "r.json")
        with open(path, "w", encoding="utf-8") as fh:
            json.dump(board([node("A", ["a.py"]), node("B", ["b.py"])]), fh)
        self.assertEqual(E.main(["--roadmap", path, "--slots", "2", "--json"]), 0)
        v = verdict([node("A", ["a.py"]), node("B", ["b.py"])], 2)
        self.assertEqual(sorted(v), ["capacity", "degree", "mode", "reason",
                                     "units", "unlocks"])
        self.assertEqual(json.loads(json.dumps(v))["degree"], v["degree"])

    def test_a_direct_headline_never_carries_a_degree(self):
        self.assertEqual(E.headline({"mode": "DIRECT", "degree": 1}),
                         "Execution: DIRECT")


@unittest.skipUnless(os.path.isfile(G.ROADMAP),
                     "the live board docs/plan/READINESS-ROADMAP-2026-08-29.json is not shipped here")
class TheRealBoardDecides(unittest.TestCase):
    def test_the_live_board_produces_one_of_the_three_answers(self):
        p = G.plan(G.load(None), None)
        v = E.decide(p)
        self.assertIn(v["mode"], ("DIRECT", "SWARM", "NO-DATA"))
        self.assertEqual(v["degree"], len(p["batch"]))

    def test_the_live_board_never_swarms_on_a_single_slot(self):
        p = G.plan(G.load(None), None)
        if p["capacity"] <= 1:
            self.assertNotEqual(E.decide(p)["mode"], "SWARM")

    def test_the_exit_code_agrees_with_the_verdict(self):
        rc = E.main([])
        expected = 2 if E.decide(G.plan(G.load(None), None))["mode"] == "NO-DATA" else 0
        self.assertEqual(rc, expected)


if __name__ == "__main__":
    unittest.main()

#!/usr/bin/env python3
"""A landing that creates a brother.loop component names it in unit BL's owns, in the same locked plan write as its
evidence, and a refused landing takes that back with the rest (FLOW-1.1.0 queue item 5, 2026-09-30).

WHY. Four times on the night of 2026-09-29 a landing put a new scripts/loop file on the run line that unit BL did not
name (the last: FX-13.1 left scripts/loop/plan_lint.py and scripts/loop/test_plan_lint.py unowned), so
scripts/test_bl_owns_complete.py went red on the run line until the conductor added them by hand (b90fb6fce). The lander
wrote only evidence, so every new loop file was an ownership gap the moment it landed.

THE ENTRY POINT, NOT A HELPER. Every landing case runs land_batch.py's main as the loop runs it, through
test_land_batch_landings.Fixture: a scratch landing tree with a bare upstream, a fresh copy of land_batch.py and
plan_store.py, stubs only at the tool boundary. ONE CONDITION PER FIXTURE: a new loop tool (owned), a new file outside the
loop's shape (not owned), a path BL already names (not doubled), a plan without BL (lands, says so), a landing refused at a
gate and one refused at the commit scan (the plan left byte for byte as committed). The undo's own contract (another
writer's owns kept) is tested on plan_store directly, since no landing can interleave a second writer on demand.
Run: python3 -B scripts/loop/test_land_batch_bl_owns.py"""
import json, os, shutil, sys, tempfile, unittest

HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.insert(0, HERE)
import land_batch as LB  # noqa: E402
import plan_store  # noqa: E402
from test_land_batch_landings import Fixture, SUB, UNIT, write  # noqa: E402

PLAN = "docs/plan/BROTHER-1.1.0-LAUNCH-WBS.json"
OLD = "scripts/loop/old_tool.py"
NEW = "scripts/loop/new_tool.py"
NEW2 = "scripts/loop/test_new_tool.py"
U1_OWN = "scripts/u1_tool.py"   # the landing unit has an owns list of its own: only BL's may change (B12, 2026-09-30)
U1 = {"id": UNIT, "sub_units": [SUB], "spec": "docs/spec-u1.md", "evidence": "", "state": "OPEN",
      "done_check": "python3 scripts/test_u1a.py", "owns": [U1_OWN]}
BL = {"id": "BL", "sub_units": [], "evidence": "BL base.", "state": "OPEN", "owns": [OLD]}


def plan_text(units):
    return json.dumps({"units": units}, indent=1)


class Landing(unittest.TestCase):
    def fixture(self, units=(U1, BL), tree_files=None, edits=None):
        root = tempfile.mkdtemp(prefix="bl-owns-")
        self.addCleanup(shutil.rmtree, root, True)
        files = {PLAN: plan_text(list(units))}
        files.update(tree_files or {})
        fx = Fixture(root, tree_files=files)
        if edits is not None:
            fx.edits({"edits": edits, "tests": []})
        return fx

    def committed_owns(self, fx, unit="BL"):
        plan = json.loads(fx.git("show", "HEAD:" + PLAN))
        return next((u.get("owns") for u in plan["units"] if u["id"] == unit), None)

    def assert_refused_and_restored(self, fx, rc, out):
        self.assertNotEqual(rc, 0, out)
        with open(os.path.join(fx.tree, PLAN), "rb") as fh:
            on_disk = fh.read()
        self.assertEqual(on_disk, fx.git("show", "HEAD:" + PLAN).encode("utf-8"),
                         "the refused landing left the plan changed (its owns entry or evidence): " + out[-600:])
        self.assertEqual(fx.git("status", "--porcelain").strip(), "", "the refused landing left the tree dirty: " + out[-600:])
        self.assertEqual(fx.rev("HEAD"), fx.base, out[-600:])

    def test_a_landed_new_loop_tool_is_named_by_bl(self):
        fx = self.fixture(edits=[{"path": NEW, "new_file_content": "print('tool')\n"}])
        rc, out = fx.land()
        self.assertEqual(rc, 0, out[-800:])
        self.assertEqual(self.committed_owns(fx), [OLD, NEW], out[-800:])
        self.assertEqual(self.committed_owns(fx, UNIT), [U1_OWN], "only unit BL is given the new owns, never the landing unit")

    def test_a_landed_file_outside_the_loop_is_not_named(self):
        fx = self.fixture(edits=[{"path": "docs/landed-u1a.txt", "new_file_content": "landed\n"}])
        rc, out = fx.land()
        self.assertEqual(rc, 0, out[-800:])
        self.assertEqual(self.committed_owns(fx), [OLD], out[-800:])

    def test_a_path_bl_already_names_is_not_named_twice(self):
        fx = self.fixture(edits=[{"path": OLD, "new_file_content": "print('old')\n"}])
        rc, out = fx.land()
        self.assertEqual(rc, 0, out[-800:])
        self.assertEqual(self.committed_owns(fx), [OLD], out[-800:])

    def test_an_edited_loop_file_that_was_already_tracked_is_not_named(self):
        """only what this landing CREATED is registered; its plan change never claims a file it did not make"""
        tracked = "scripts/loop/tracked_tool.py"
        fx = self.fixture(tree_files={tracked: "print('v1')\n"}, edits=[{"path": tracked, "new_file_content": "print('v2')\n"}])
        rc, out = fx.land()
        self.assertEqual(rc, 0, out[-800:])
        self.assertEqual(self.committed_owns(fx), [OLD], out[-800:])

    def test_a_plan_without_bl_still_lands_and_says_so(self):
        fx = self.fixture(units=(U1,), edits=[{"path": NEW, "new_file_content": "print('tool')\n"}])
        rc, out = fx.land()
        self.assertEqual(rc, 0, out[-800:])
        self.assertIn("unit BL is not in the plan", out)

    def test_two_new_loop_files_are_both_named_in_order(self):
        """FX-13.1's own shape: a tool and its test landed together (the gate on dfd97cee4: a lander claiming only the
        first new file survived every one file fixture)"""
        fx = self.fixture(edits=[{"path": NEW, "new_file_content": "print('tool')\n"},
                                 {"path": NEW2, "new_file_content": "print('test')\n"}])
        rc, out = fx.land()
        self.assertEqual(rc, 0, out[-800:])
        self.assertEqual(self.committed_owns(fx), [OLD, NEW, NEW2], out[-800:])

    def test_a_refused_landing_of_two_new_loop_files_takes_both_back(self):
        fx = self.fixture(tree_files={"scripts/test_battery_registration.py": "import sys\nsys.exit(1)\n"},
                          edits=[{"path": NEW, "new_file_content": "print('tool')\n"},
                                 {"path": NEW2, "new_file_content": "print('test')\n"}])
        rc, out = fx.land()
        self.assert_refused_and_restored(fx, rc, out)

    def test_a_landing_refused_at_a_gate_takes_its_owns_entry_back(self):
        fx = self.fixture(tree_files={"scripts/test_battery_registration.py": "import sys\nsys.exit(1)\n"},
                          edits=[{"path": NEW, "new_file_content": "print('tool')\n"}])
        rc, out = fx.land()
        self.assert_refused_and_restored(fx, rc, out)

    def test_a_landing_refused_at_the_commit_scan_takes_its_owns_entry_back(self):
        fx = self.fixture(edits=[{"path": NEW, "new_file_content": "print('tool')\n"}])
        write(os.path.join(fx.bin, "commit_scan.py"), "import sys\nprint('commit_scan: BLOCK')\nsys.exit(1)\n")
        rc, out = fx.land()
        self.assert_refused_and_restored(fx, rc, out)


class Shape(unittest.TestCase):
    """The component rule is test_bl_owns_complete's own: a tool or test directly in scripts/loop, scripts/test_loop*.py,
    scripts/test_unit_runner*.py."""

    def test_the_loops_own_shapes_are_components(self):
        paths = ["scripts/loop/a.py", "scripts/loop/b.sh", "scripts/test_loop_x.py", "scripts/test_unit_runner_y.py"]
        self.assertEqual(LB.loop_components(paths), paths)

    def test_anything_else_is_not(self):
        paths = ["scripts/loop/abc/c.sh", "scripts/loop/d.json", "scripts/loop/README.md", "scripts/test_other.py",
                 "docs/scripts/loop/e.py", "scripts/looper/f.py", "bundle/runtime/loop/g.py",
                 "products/x/test_loop_a.py", "docs/test_unit_runner_b.py", "scripts/loop/test_loop_c.txt"]
        self.assertEqual(LB.loop_components(paths), [])


class Undo(unittest.TestCase):
    def plan(self, units):
        d = tempfile.mkdtemp(prefix="bl-owns-undo-")
        self.addCleanup(shutil.rmtree, d, True)
        p = os.path.join(d, "plan.json")
        with open(p, "w", encoding="utf-8") as fh:
            fh.write(plan_text(units))
        return p

    def bl(self, p):
        return next(u for u in plan_store.load(p)["units"] if u["id"] == "BL")

    def test_the_undo_takes_back_only_this_landings_owns_and_keeps_another_writers(self):
        p = self.plan([dict(U1), dict(BL, owns=[OLD])])
        appended = {}
        plan_store.update_units(p, LB.landing_records({"U1": [" U1.a landed."]}, appended, owns=[NEW]), fields=("evidence", "owns"))
        self.assertEqual(self.bl(p)["owns"], [OLD, NEW])
        other = "scripts/loop/other_writer.py"
        plan_store.update_units(p, {"BL": lambda u: dict(u, owns=u["owns"] + [other])}, fields=("owns",))
        self.assertEqual(plan_store.undo_appended(p, appended), [])
        self.assertEqual(self.bl(p)["owns"], [OLD, other])
        self.assertEqual(self.bl(p)["evidence"], "BL base.")

    def test_an_owner_that_also_landed_its_own_evidence_gets_both_back_byte_for_byte(self):
        """BL landing a sub unit of its own AND gaining owns in one write: the undo takes back the evidence line and the
        owns entry (the gate on dfd97cee4: skipping the evidence undo whenever owns were added survived)"""
        p = self.plan([dict(U1), dict(BL, owns=[OLD])])
        with open(p, "rb") as fh:
            committed = fh.read()
        appended = {}
        plan_store.update_units(p, LB.landing_records({"BL": [" BL.x landed."]}, appended, owns=[NEW]), fields=("evidence", "owns"))
        self.assertEqual((self.bl(p)["evidence"], self.bl(p)["owns"]), ("BL base. BL.x landed.", [OLD, NEW]))
        self.assertEqual(plan_store.undo_appended(p, appended, committed=committed), [])
        with open(p, "rb") as fh:
            self.assertEqual(fh.read(), committed)

    def test_with_no_other_writer_the_plan_returns_byte_for_byte(self):
        p = self.plan([dict(U1), dict(BL, owns=[OLD])])
        with open(p, "rb") as fh:
            committed = fh.read()
        appended = {}
        plan_store.update_units(p, LB.landing_records({"U1": [" U1.a landed."]}, appended, owns=[NEW]), fields=("evidence", "owns"))
        self.assertEqual(plan_store.undo_appended(p, appended, committed=committed), [])
        with open(p, "rb") as fh:
            self.assertEqual(fh.read(), committed)

    def test_an_owner_with_no_evidence_still_gets_its_owns_back(self):
        """the owner appended no evidence (only owns), so the evidence check must not refuse its undo"""
        p = self.plan([dict(U1), {"id": "BL", "sub_units": [], "state": "OPEN", "owns": [OLD]}])
        appended = {}
        plan_store.update_units(p, LB.landing_records({"U1": [" U1.a landed."]}, appended, owns=[NEW]), fields=("evidence", "owns"))
        self.assertEqual(self.bl(p)["owns"], [OLD, NEW])
        self.assertNotIn("evidence", self.bl(p), "an owns only write never invents an evidence field (B10)")
        self.assertEqual(plan_store.undo_appended(p, appended), [])
        self.assertEqual(self.bl(p)["owns"], [OLD])

    def test_only_the_owner_is_given_the_owns_never_the_landing_unit(self):
        """B12 (2026-09-30): the landing unit carries an owns list of its own, and it must come out unchanged"""
        p = self.plan([dict(U1), dict(BL, owns=[OLD])])
        appended = {}
        plan_store.update_units(p, LB.landing_records({"U1": [" U1.a landed."]}, appended, owns=[NEW]), fields=("evidence", "owns"))
        u1 = next(u for u in plan_store.load(p)["units"] if u["id"] == UNIT)
        self.assertEqual((u1["owns"], self.bl(p)["owns"]), ([U1_OWN], [OLD, NEW]))
        self.assertEqual(len(appended["U1"]), 2, "no owns item for the landing unit: %r" % (appended["U1"],))

    def test_an_owns_that_is_none_or_a_dict_is_left_alone_on_the_write_side(self):
        """B15 (2026-09-30): the write must not crash on, or overwrite, an owns that is not a list"""
        for bad in (None, {"x": 1}):
            p = self.plan([dict(U1), dict(BL, owns=bad)])
            appended = {}
            plan_store.update_units(p, LB.landing_records({"U1": [" U1.a landed."]}, appended, owns=[NEW]), fields=("evidence", "owns"))
            self.assertEqual(self.bl(p)["owns"], bad)
            self.assertNotIn("BL", appended)

    def test_an_undo_after_another_writer_already_removed_the_entry_does_not_raise(self):
        """B14 (2026-09-30): a second writer took the entry out first; the undo must not raise inside the lock"""
        p = self.plan([dict(U1), dict(BL, owns=[OLD])])
        appended = {}
        plan_store.update_units(p, LB.landing_records({"U1": [" U1.a landed."]}, appended, owns=[NEW]), fields=("evidence", "owns"))
        plan_store.update_units(p, {"BL": lambda u: dict(u, owns=[OLD])}, fields=("owns",))
        self.assertEqual(plan_store.undo_appended(p, appended), [])
        self.assertEqual(self.bl(p)["owns"], [OLD])

    def test_an_owns_that_is_not_a_list_is_left_alone(self):
        p = self.plan([dict(U1), dict(BL, owns="scripts/loop/old_tool.py")])
        appended = {}
        plan_store.update_units(p, LB.landing_records({"U1": [" U1.a landed."]}, appended, owns=[NEW]), fields=("evidence", "owns"))
        self.assertEqual(self.bl(p)["owns"], "scripts/loop/old_tool.py")
        self.assertNotIn("BL", appended)


if __name__ == "__main__":
    unittest.main(verbosity=1)

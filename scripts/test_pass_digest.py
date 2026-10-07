#!/usr/bin/env python3
"""H3.c of unit H3: REQ-H-SKIPPED. The pass digest NAMES the READY builds it skips, because their unit is
closed or their build already landed, instead of hiding them behind a zero. The same digest and its
helpers refuse hostile input with their own ValueError, never a raw TypeError, AttributeError or OSError.
Run: python3 -B scripts/test_pass_digest.py
No subprocess, no network and no live repository document: every fixture is built in a temp folder."""
import contextlib
import io
import json
import os
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
LOOP_DIR = os.path.join(HERE, "loop")
if LOOP_DIR not in sys.path:
    sys.path.insert(0, LOOP_DIR)
import pass_digest as PD


class SkippedReadyNamesTheSkips(unittest.TestCase):
    def test_names_ready_builds_of_closed_units_and_landed_builds(self):
        last = {"Z.1": ("READY", "/b/Z.1-r1-build.json"),
                "D1.2": ("READY", "/b/D1.2-r1-build.json"),
                "Y.1": ("READY", "/b/Y.1-r1-build.json")}
        self.assertEqual(PD.skipped_ready(last, {"D1.2"}, {"Z.1"}), ["D1.2", "Z.1"])

    def test_an_open_unit_that_is_not_landed_is_never_named(self):
        self.assertEqual(PD.skipped_ready({"Y.1": ("READY", "/b/Y.1-r1-build.json")}, set(), set()), [])

    def test_a_ready_line_with_no_path_is_not_counted(self):
        self.assertEqual(PD.skipped_ready({"Z.1": ("READY", "   ")}, set(), {"Z.1"}), [])

    def test_only_ready_lines_are_counted(self):
        last = {"Z.1": ("RUNNING", ""), "Z.2": ("READY-UNPROBED", "/b/x.json"),
                "Z.3": ("EXHAUSTED", "after 3 rounds"), "Z.4": ("WITHHELD", "/b/y.json")}
        self.assertEqual(PD.skipped_ready(last, set(), {"Z.1", "Z.2", "Z.3", "Z.4"}), [])

    def test_an_empty_last_map_names_nothing(self):
        self.assertEqual(PD.skipped_ready({}, set(), set()), [])

    def test_the_names_are_sorted(self):
        last = {"b": ("READY", "/b/b.json"), "a": ("READY", "/b/a.json"), "c": ("READY", "/b/c.json")}
        self.assertEqual(PD.skipped_ready(last, set(), {"a", "b", "c"}), ["a", "b", "c"])

    def test_hostile_input_is_refused_and_never_reads_as_nothing_skipped(self):
        good = {"Z.1": ("READY", "/b/Z.1-r1-build.json")}
        cases = ((None, set(), set()), ({}, None, set()), ({}, set(), None), (good, None, None),
                 ([], set(), set()), ("Z.1", set(), set()), ({}, [], set()), ({}, set(), "Z.1"),
                 ({}, (), {"Z.1"}), ({"Z.1": None}, set(), set()),
                 ({"Z.1": ("READY",)}, set(), set()), ({"Z.1": ("READY", "/b", "extra")}, set(), set()),
                 ({"Z.1": ("READY", float("nan"))}, set(), set()), ({"Z.1": (None, "/b")}, set(), set()),
                 ({1: ("READY", "/b")}, set(), set()), ({float("nan"): ("READY", "/b")}, set(), set()),
                 (good, float("nan"), set()), (good, {1}, set()), (good, set(), {1}))
        for args in cases:
            with self.assertRaises(ValueError):
                PD.skipped_ready(*args)


class HostileInputIsRefusedNotCrashed(unittest.TestCase):
    def test_first3_refuses_none_and_a_wrong_type(self):
        for bad in (None, 5, 5.0, {"a": 1}, object()):
            with self.assertRaises(ValueError):
                PD.first3(bad)

    def test_landed_subs_refuses_none_and_a_plan_that_is_not_a_mapping(self):
        for bad in (None, [], "units", 5):
            with self.assertRaises(ValueError):
                PD.landed_subs(bad)
        for bad in ({"units": "X1"}, {"units": [None]},
                    {"units": [{"sub_units": [1], "evidence": ""}]},
                    {"units": [{"sub_units": ["X1"], "evidence": 5}]}):
            with self.assertRaises(ValueError):
                PD.landed_subs(bad)

    def test_closed_subs_refuses_an_unhashable_sub_unit_id(self):
        for bad in (None, [], "units",
                    {"units": [{"state": "DONE", "sub_units": [["Z.1"]]}]},
                    {"units": [{"state": "DONE", "sub_units": [1]}]}):
            with self.assertRaises(ValueError):
                PD.closed_subs(bad)

    def test_newest_status_refuses_an_unreadable_status_and_none(self):
        with self.assertRaises(ValueError):
            PD.newest_status(None, lambda p: "")
        with self.assertRaises(ValueError):
            PD.newest_status(["Z.1-120000"], None)
        with self.assertRaises(ValueError):
            PD.newest_status([None], lambda p: "")
        sandbox = tempfile.TemporaryDirectory(prefix="pass-digest-h3c-status-")
        self.addCleanup(sandbox.cleanup)
        run_dir = os.path.join(sandbox.name, "Z.1-120000")
        os.makedirs(os.path.join(run_dir, "STATUS"))

        def read_like_main(d):
            with open(os.path.join(d, "STATUS"), encoding="utf-8") as handle:
                return handle.read()

        with self.assertRaises(ValueError):
            PD.newest_status([run_dir + "/"], read_like_main)

    def test_partition_refuses_none_and_a_row_that_is_not_a_pair(self):
        with self.assertRaises(ValueError):
            PD.partition(None, set(), set())
        with self.assertRaises(ValueError):
            PD.partition({}, None, set())
        with self.assertRaises(ValueError):
            PD.partition({"Z.1": None}, set(), set())
        with self.assertRaises(ValueError):
            PD.partition({"Z.1": ("READY",)}, set(), set())


class MainRefusesAHostilePlanPath(unittest.TestCase):
    def test_main_refuses_a_plan_path_that_is_not_text(self):
        old_plan, old_argv = PD.PLAN, sys.argv

        def restore():
            PD.PLAN, sys.argv = old_plan, old_argv

        self.addCleanup(restore)
        sys.argv = ["pass_digest.py", "--no-fetch"]
        for bad in (["not", "a", "path"], None, 5):
            PD.PLAN = bad
            buffer = io.StringIO()
            with contextlib.redirect_stdout(buffer):
                code = PD.main()
            self.assertEqual(code, 1)
            self.assertIn("PLAN    NO-DATA", buffer.getvalue())


class SkippedReadyIsPrinted(unittest.TestCase):
    def test_main_prints_the_skipped_line_for_a_ready_build_of_a_closed_unit(self):
        sandbox = tempfile.TemporaryDirectory(prefix="pass-digest-h3c-")
        self.addCleanup(sandbox.cleanup)
        root = sandbox.name
        plan_path = os.path.join(root, "plan.json")
        with open(plan_path, "w", encoding="utf-8") as handle:
            json.dump({"units": [{"id": "Z", "state": "DONE", "sub_units": ["Z.1"]}]}, handle)
        runs = os.path.join(root, "unit-runs")
        run_dir = os.path.join(runs, "Z.1-120000")
        os.makedirs(run_dir)
        with open(os.path.join(run_dir, "STATUS"), "w", encoding="utf-8") as handle:
            handle.write("READY /b/Z.1-r1-build.json (round 1)")
        old_plan, old_runs, old_argv = PD.PLAN, PD.RUNS, sys.argv

        def restore():
            PD.PLAN, PD.RUNS, sys.argv = old_plan, old_runs, old_argv

        self.addCleanup(restore)
        PD.PLAN, PD.RUNS, sys.argv = plan_path, runs, ["pass_digest.py", "--no-fetch"]
        buffer = io.StringIO()
        with contextlib.redirect_stdout(buffer):
            code = PD.main()
        self.assertEqual(code, 0)
        self.assertIn("READY on closed units: 1 skipped: Z.1", buffer.getvalue())


class AScopedRunOffersOnlyItsScopeToLand(unittest.TestCase):
    """2026-10-03: a run scoped ^(CV1|ACC2)$ offered an older HP1.d READY build to the lander. Through main(): one
    in scope and one out of scope READY build; only the in scope one is offered, the other is named, never hidden."""
    def run_main(self, scope):
        sandbox = tempfile.TemporaryDirectory(prefix="pass-digest-scope-")
        self.addCleanup(sandbox.cleanup)
        root = sandbox.name
        plan_path = os.path.join(root, "plan.json")
        with open(plan_path, "w", encoding="utf-8") as handle:
            json.dump({"units": [{"id": "CV1", "state": "SPECIFIED", "sub_units": ["CV1.b"]},
                                 {"id": "HP1", "state": "SPECIFIED", "sub_units": ["HP1.d"]}]}, handle)
        runs = os.path.join(root, "unit-runs")
        for sub in ("CV1.b", "HP1.d"):
            d = os.path.join(runs, sub + "-120000"); os.makedirs(d)
            with open(os.path.join(d, "STATUS"), "w", encoding="utf-8") as handle:
                handle.write("READY /b/%s-r0-build.json (round 0)" % sub)
        old = PD.PLAN, PD.RUNS, sys.argv, os.environ.get("BROTHER_SCOPE")
        def restore():
            PD.PLAN, PD.RUNS, sys.argv = old[:3]
            if old[3] is None: os.environ.pop("BROTHER_SCOPE", None)
            else: os.environ["BROTHER_SCOPE"] = old[3]
        self.addCleanup(restore)
        PD.PLAN, PD.RUNS, sys.argv = plan_path, runs, ["pass_digest.py", "--no-fetch"]
        if scope is None: os.environ.pop("BROTHER_SCOPE", None)
        else: os.environ["BROTHER_SCOPE"] = scope
        buffer = io.StringIO()
        with contextlib.redirect_stdout(buffer):
            code = PD.main()
        return code, buffer.getvalue()

    def test_an_out_of_scope_ready_build_is_not_offered_and_is_named(self):
        code, out = self.run_main("^(CV1|ACC2)$")
        self.assertEqual(code, 0, out)
        self.assertIn("READY   1 to land:", out)
        self.assertIn("/b/CV1.b-r0-build.json", out)
        self.assertNotIn("/b/HP1.d-r0-build.json", out)
        self.assertIn("READY outside this run's scope, not offered to land: HP1.d", out)

    def test_no_scope_offers_both_as_before(self):
        code, out = self.run_main(None)
        self.assertIn("READY   2 to land:", out)
        self.assertNotIn("outside this run's scope", out)

    def test_an_unreadable_scope_offers_nothing(self):
        code, out = self.run_main("(")
        self.assertIn("READY   0 to land", out)


class TheLandedMatchIsExact(unittest.TestCase):
    """Finding 2, 2026-09-27: A.1 matched inside A.10 and "A.1 not landed" read as landed, so an unlanded build
    vanished from READY. One fixture per guard."""

    def test_a_longer_id_never_lands_its_prefix_and_its_build_stays_ready(self):
        plan = {"units": [{"id": "A", "sub_units": ["A.1", "A.10"], "evidence": "A.10 landed 2026-09-27."}]}
        self.assertEqual(PD.landed_subs(plan), {"A.10"})
        self.assertEqual(PD.partition({"A.1": ("READY", "/b/A.1.json")}, PD.landed_subs(plan), set())[0], ["/b/A.1.json"])

    def test_a_negated_landing_is_not_a_landing(self):
        plan = {"units": [{"id": "A", "sub_units": ["A.1"], "evidence": "A.1 not landed: gate refused."}]}
        self.assertEqual(PD.landed_subs(plan), set())


class AnUnreadableRunsSourceIsNoData(unittest.TestCase):
    """Finding 8, 2026-09-27: glob() swallowed a permission error on the runs folder, so the digest printed READY 0,
    STALLED 0 and exited 0 over a folder holding a READY build. Unreadable is NO-DATA and a non zero exit."""

    def run_main(self, runs, plan_path):
        old_plan, old_runs, old_argv = PD.PLAN, PD.RUNS, sys.argv

        def restore():
            PD.PLAN, PD.RUNS, sys.argv = old_plan, old_runs, old_argv

        self.addCleanup(restore)
        PD.PLAN, PD.RUNS, sys.argv = plan_path, runs, ["pass_digest.py", "--no-fetch"]
        buffer = io.StringIO()
        with contextlib.redirect_stdout(buffer):
            code = PD.main()
        return code, buffer.getvalue()

    def fixture(self):
        sandbox = tempfile.TemporaryDirectory(prefix="pass-digest-g8-")
        self.addCleanup(sandbox.cleanup)
        plan_path = os.path.join(sandbox.name, "plan.json")
        with open(plan_path, "w", encoding="utf-8") as handle:
            json.dump({"units": [{"id": "A", "state": "OPEN", "sub_units": ["A.1"]}]}, handle)
        runs = os.path.join(sandbox.name, "unit-runs")
        os.makedirs(os.path.join(runs, "A.1-120000"))
        status = os.path.join(runs, "A.1-120000", "STATUS")
        with open(status, "w", encoding="utf-8") as handle:
            handle.write("READY /b/A.1-r0-build.json\n")
        return runs, status, plan_path

    def test_an_unlistable_runs_folder_is_no_data_never_zero(self):
        runs, _, plan_path = self.fixture()
        os.chmod(runs, 0)
        self.addCleanup(os.chmod, runs, 0o700)
        code, text = self.run_main(runs, plan_path)
        self.assertNotEqual(code, 0, text)
        self.assertIn("NO-DATA", "\n".join(l for l in text.splitlines() if l.startswith("RUNS")), text)
        self.assertNotIn("READY   0", text)

    def test_an_unreadable_status_is_no_data_never_running(self):
        runs, status, plan_path = self.fixture()
        os.chmod(status, 0)
        self.addCleanup(os.chmod, status, 0o600)
        code, text = self.run_main(runs, plan_path)
        self.assertNotEqual(code, 0, text)
        self.assertIn("NO-DATA", "\n".join(l for l in text.splitlines() if l.startswith("RUNS")), text)
        self.assertNotIn("STALLED 1", text)

    def test_a_missing_runs_folder_is_no_runs_yet_and_exits_0(self):
        runs, _, plan_path = self.fixture()
        code, text = self.run_main(os.path.join(os.path.dirname(runs), "never-created"), plan_path)
        self.assertEqual(code, 0, text)
        self.assertIn("READY   0", text)


if __name__ == "__main__":
    unittest.main()

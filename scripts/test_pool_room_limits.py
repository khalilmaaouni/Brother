#!/usr/bin/env python3
"""LOOP1.a, the integrator's cases beside the frozen Room harness (scripts/test_loop_delivery.py Room): each property of
the room decision that the frozen cases leave unpinned, one condition per case. room_of is called directly where the
property lives in it; everything the pass prints or records is read from a real --dry pass of runner_pool.py in a
subprocess, over an empty plan in a throwaway launch tree and a scratch HOME, so no runner can start and nothing outside
the scratch folder is written.

    python3 -B scripts/test_pool_room_limits.py

Python 3.9, standard library only."""
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest

HERE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "loop")   # lives in scripts/, like LOOP1.f's tests: no loop unit owns it
POOL_PATH = os.path.join(HERE, "runner_pool.py")
sys.path.insert(0, HERE)


def pool():
    import importlib
    return importlib.import_module("runner_pool")


class RoomOf(unittest.TestCase):
    """Shape A of room_of, the pass's own keyword call."""

    def test_the_seat_cap_is_compared_as_it_is_not_minus_alive(self):
        # burn 9 - 3 = 6, wip 9 - 0 = 9, seats 2: the seat cap itself binds at 2; seat_cap - alive would read -1, room 0
        self.assertEqual((2, "seats"), pool().room_of(alive=3, in_progress=0, burn_cap=9, wip=9, seat_cap=2, busy=""))

    def test_room_is_clamped_at_zero(self):
        self.assertEqual((0, "burn"), pool().room_of(alive=7, in_progress=0, burn_cap=5, wip=9, seat_cap=None, busy=""))

    def test_a_tie_names_the_earlier_of_burn_wip_seats(self):
        self.assertEqual((2, "burn"), pool().room_of(alive=1, in_progress=1, burn_cap=3, wip=3, seat_cap=2, busy=""))
        self.assertEqual((2, "wip"), pool().room_of(alive=0, in_progress=1, burn_cap=5, wip=3, seat_cap=2, busy=""))

    def test_a_bool_count_or_cap_is_refused(self):
        base = dict(alive=0, in_progress=0, burn_cap=5, wip=5, seat_cap=None, busy="")
        for name in ("alive", "in_progress", "burn_cap", "wip"):
            with self.subTest(field=name):
                with self.assertRaises(ValueError):
                    pool().room_of(**dict(base, **{name: True}))

    def test_a_count_or_cap_that_is_not_an_int_is_refused(self):
        base = dict(alive=0, in_progress=0, burn_cap=5, wip=5, seat_cap=None, busy="")
        for name in ("alive", "in_progress", "burn_cap", "wip"):
            with self.subTest(field=name):
                with self.assertRaises(ValueError):
                    pool().room_of(**dict(base, **{name: 1.5}))

    def test_a_bool_or_non_int_seat_cap_is_refused(self):
        for bad in (True, 1.5, "1"):
            with self.subTest(seat_cap=bad):
                with self.assertRaises(ValueError):
                    pool().room_of(alive=0, in_progress=0, burn_cap=5, wip=5, seat_cap=bad, busy="")

    def test_a_busy_that_is_not_a_string_is_refused(self):
        with self.assertRaises(ValueError):
            pool().room_of(alive=0, in_progress=0, burn_cap=5, wip=5, seat_cap=None, busy=1)


class RoomReason(unittest.TestCase):
    """The binding text carries the binding input's own numbers, never another input's."""

    def test_burn_binding_names_alive_and_the_burn_cap(self):
        binding, _r = pool().room_reason(0, "burn", 4, 1, 4, 9, None, "")
        self.assertEqual("4 alive of 4", binding)

    def test_wip_binding_names_in_progress_and_the_wip_cap(self):
        binding, _r = pool().room_reason(0, "wip", 1, 3, 9, 3, None, "")
        self.assertEqual("3 in progress of 3", binding)

    def test_an_unknown_limit_is_refused(self):
        with self.assertRaises(ValueError):
            pool().room_reason(0, "budget cap", 0, 0, 5, 5, None, "")


class Pass(unittest.TestCase):
    """A real --dry pass, read from its stdout and its pool-room.jsonl record."""

    def setUp(self):
        self.dir = tempfile.mkdtemp(prefix="pool-room-")
        self.addCleanup(shutil.rmtree, self.dir, True)
        self.home = os.path.join(self.dir, "home")
        self.ev = os.path.join(self.home, ".claude", "evidence")
        os.makedirs(self.ev)
        os.makedirs(os.path.join(self.dir, "docs", "plan"))
        with open(os.path.join(self.dir, "docs", "plan", "ROOM-TEST-WBS.json"), "w", encoding="utf-8") as fh:
            json.dump({"units": []}, fh)
        self.bin = os.path.join(self.dir, "bin")   # a stand in sandbox-exec, so the native switch reads on here
        os.makedirs(self.bin)
        fake = os.path.join(self.bin, "sandbox-exec")
        with open(fake, "w") as fh:
            fh.write("#!/bin/sh\nexit 0\n")
        os.chmod(fake, 0o755)

    def fund(self, lanes):
        """The money guard the pass reads its burn cap from, stated by the case."""
        b = os.path.join(self.home, ".claude", "bin")
        os.makedirs(b, exist_ok=True)
        with open(os.path.join(b, "burn_guard.py"), "w", encoding="utf-8") as fh:
            fh.write("print(%d)\n" % lanes)

    def run_pass(self, env):
        base = {k: v for k, v in os.environ.items() if not k.startswith(("BROTHER_", "GIT_"))}
        base.update(HOME=self.home, BROTHER_WBS="docs/plan/ROOM-TEST-WBS.json", BROTHER_WIP_GATE="off",
                    BROTHER_CLAUDE_NATIVE="off", PATH=self.bin + os.pathsep + base.get("PATH", ""))
        base.update(env)
        p = subprocess.run([sys.executable, "-B", POOL_PATH, "--dry"], cwd=self.dir, env=base,
                           capture_output=True, text=True, timeout=600)
        out = p.stdout + p.stderr
        path = os.path.join(self.ev, "pool-room.jsonl")
        self.assertTrue(os.path.isfile(path), "no room record: %s" % out[-600:])
        with open(path, encoding="utf-8") as fh:
            lines = fh.read().splitlines()
        self.assertEqual(1, len(lines), "one pass must append one record: %s" % lines)
        return out, json.loads(lines[0])

    def test_each_pass_appends_its_own_record(self):
        self.fund(5)
        self.run_pass({"BROTHER_WIP": "5"})
        second = subprocess.run([sys.executable, "-B", POOL_PATH, "--dry"], cwd=self.dir, capture_output=True, text=True,
                       timeout=600, env=dict({k: v for k, v in os.environ.items() if not k.startswith(("BROTHER_", "GIT_"))},
                                             HOME=self.home, BROTHER_WBS="docs/plan/ROOM-TEST-WBS.json",
                                             BROTHER_WIP_GATE="off", BROTHER_CLAUDE_NATIVE="off", BROTHER_WIP="2"))
        self.assertEqual(0, second.returncode, second.stdout[-400:] + second.stderr[-400:])
        with open(os.path.join(self.ev, "pool-room.jsonl"), encoding="utf-8") as fh:
            limits = [json.loads(line)["limit"] for line in fh.read().splitlines()]
        self.assertEqual(["burn", "wip"], limits)

    def test_a_dry_pass_creates_no_runs_folder(self):
        self.fund(5)
        self.run_pass({"BROTHER_WIP": "5"})
        self.assertFalse(os.path.exists(os.path.join(self.ev, "unit-runs")), "a dry pass created the runs folder")

    def test_burn_limit_names_the_burn_cap_and_its_recovery(self):
        self.fund(3)
        out, rec = self.run_pass({"BROTHER_WIP": "7"})
        self.assertIn("room for 3 | limit: burn (0 alive of 3); room grows when a runner ends or the money guard funds "
                      "more lanes", out)
        self.assertEqual(("burn", "0 alive of 3"), (rec["limit"], rec["binding"]))
        self.assertIn("a runner ends", rec["recovery"])

    def test_wip_limit_names_a_landing_and_its_recovery(self):
        self.fund(5)
        out, rec = self.run_pass({"BROTHER_WIP": "2"})
        self.assertIn("room for 2 | limit: wip (0 in progress of 2); room grows when a unit lands", out)
        self.assertEqual(("wip", "0 in progress of 2"), (rec["limit"], rec["binding"]))
        self.assertIn("a unit lands", rec["recovery"])

    def test_seats_limit_names_a_seat_and_its_recovery(self):
        self.fund(5)
        out, rec = self.run_pass({"BROTHER_WIP": "5", "BROTHER_CLAUDE_NATIVE": "on", "BROTHER_PIN_MODEL": "m",
                                  "BROTHER_NATIVE_SEATS": "2", "WORKERS_PER_ROUND": "2"})
        self.assertIn("room for 1 | limit: seats (seat cap 1); room grows when more native seats are set", out)
        self.assertEqual(("seats", "seat cap 1"), (rec["limit"], rec["binding"]))
        self.assertIn("native seats", rec["recovery"])

    def test_load_limit_names_the_load_and_its_recovery(self):
        self.fund(5)
        out, rec = self.run_pass({"BROTHER_WIP": "5", "BROTHER_LOAD_CAP": "100000"})
        self.assertIn("room for 0 | limit: load (machine busy: one minute load 100000.0 is at or above", out)
        self.assertIn("room returns when the one minute load reads below the core count", out)
        self.assertEqual("load", rec["limit"])
        self.assertTrue(rec["binding"].startswith("machine busy: one minute load 100000.0"), rec["binding"])
        self.assertIn("load", rec["recovery"])

    def test_the_record_carries_the_real_inputs(self):
        self.fund(3)
        _out, rec = self.run_pass({"BROTHER_WIP": "7"})
        self.assertEqual({"alive": 0, "in_progress": 0, "burn_cap": 3, "wip": 7, "seat_cap": None, "busy": ""},
                         rec["inputs"])
        self.assertEqual((3, True), (rec["room"], rec["dry"]))

    def test_a_report_only_limit_is_never_enforced_nor_the_limit(self):
        """The contract's named mutation: the finish-first gate under report only prints, so the record lists it under
        report_only and never under enforced, and it is never the limit."""
        self.fund(5)
        _out, rec = self.run_pass({"BROTHER_WIP": "5", "BROTHER_WIP_GATE": "report"})
        self.assertIn("finish-first limits (BROTHER_WIP_GATE=report)", rec["report_only"])
        self.assertEqual(["burn", "wip"], rec["enforced"])
        self.assertNotIn(rec["limit"], rec["report_only"])

    def test_the_finish_first_gate_off_is_not_listed_as_report_only(self):
        self.fund(5)
        _out, rec = self.run_pass({"BROTHER_WIP": "5"})
        self.assertNotIn("finish-first limits (BROTHER_WIP_GATE=report)", rec["report_only"])

    def test_the_plan_lint_under_report_is_report_only(self):
        self.fund(5)
        _out, rec = self.run_pass({"BROTHER_WIP": "5"})
        self.assertIn("plan lint (mode report)", rec["report_only"])

    def test_the_plan_lint_under_block_is_not_report_only(self):
        self.fund(5)
        _out, rec = self.run_pass({"BROTHER_WIP": "5", "BROTHER_RUNFLOW_LINT": "block"})
        self.assertNotIn("plan lint (mode report)", rec["report_only"])

    def test_seats_and_load_are_enforced_when_the_pass_reads_them(self):
        self.fund(5)
        _out, rec = self.run_pass({"BROTHER_WIP": "5", "BROTHER_CLAUDE_NATIVE": "on", "BROTHER_PIN_MODEL": "m",
                                   "BROTHER_NATIVE_SEATS": "2", "WORKERS_PER_ROUND": "2", "BROTHER_LOAD_CAP": "0.0"})
        self.assertEqual(["burn", "wip", "seats", "load"], rec["enforced"])


if __name__ == "__main__":
    unittest.main()

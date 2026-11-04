#!/usr/bin/env python3
"""STAB1.j: the run monitor has one production caller, and it is the loop pass.

THE CONTRACT. stage_log.py `monitor` (LOOP1.f's expiry marks, STAB1.h's idle_with_work row) used to be called only by
its own CLI, so no loop pass ran it and the idle row had no writer. Each pass now calls it ONCE, with --driver-pid-file
(the driver's own `driver.pid`, written by loop_until.sh beside `RUN PID`) and --admissible (read from runner_pool's dry
look), and the morning report shows the row.

THE ENTRY POINT IS THE LOOP PASS, never the helper. Every case runs the real scripts/loop/loop_pass.sh through the
whole pass harness of scripts/test_loop_pass_code_root.py (scratch tree with a bare upstream, a frozen code root, a HOME
whose bin holds stubs), with the REAL stage_log.py behind a recording wrapper and a runner_pool stub that answers the
dry look from a file. The report case runs the real loop_report.py over a driver log.

ONE CONDITION PER FIXTURE: the idle row is written by exactly one fixture shape (no driver alive, admissible work) and
each refusal below differs from it in exactly one thing.

Run: python3 -B scripts/test_monitor_wired.py LoopPassCallsMonitor   (and /usr/bin/python3)
"""
import io
import json
import os
import subprocess
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import export_public  # noqa: E402
import test_loop_pass_code_root as P  # noqa: E402  the whole pass harness: tree, code root, bin stubs

STAGE_LOG = os.path.join(HERE, "loop", "stage_log.py")
LOOP_REPORT = os.path.join(HERE, "loop", "loop_report.py")
MARK = "idle_with_work"
RUN = "run-20261101-000000-1"

POOL_STUB = '''import os, sys
h = os.environ["HOME"]
dry = "--dry" in sys.argv
open(os.path.join(h, "pool-calls"), "a").write(("dry" if dry else "real") + "\\n")
if not dry:
    print("started 1")
    sys.exit(0)
f = os.path.join(h, "dry-says")
if os.path.isfile(f):
    text = open(f).read()
    if text.startswith("FAIL"):
        print("boom"); sys.exit(3)
    sys.stdout.write(text)
'''
# the real stage_log behind a recorder: every monitor call is logged with its flags; one file makes the verb fail
WRAPPER = '''import os, sys
REAL = %r
h = os.environ["HOME"]
if sys.argv[1:2] == ["monitor"]:
    open(os.path.join(h, "monitor-calls"), "a").write(" ".join(sys.argv[2:]) + "\\n")
    if os.path.isfile(os.path.join(h, "monitor-fails")):
        print("boom: the monitor verb failed"); sys.exit(1)
os.execv(sys.executable, [sys.executable, "-B", REAL] + sys.argv[1:])
''' % STAGE_LOG


def scratch(prefix):
    """A throwaway directory under HOME's own scratch root, made if absent: an empty HOME (the export tree's hermetic run) has none."""
    root = export_public.scratch_root()
    os.makedirs(root, exist_ok=True)
    return tempfile.mkdtemp(prefix=prefix, dir=root)

def dry_look(n):
    return "SCOPE   all\nBUDGET cap 5 runner(s)\nstarted %d | runners alive before this pass 0 | in scope units with no sub units yet (need a spec lane): []\n" % n


class LoopPassCallsMonitor(unittest.TestCase):
    _base_setup = P.PassCodeRoot.setUp
    tearDown = P.PassCodeRoot.tearDown
    run_pass = P.PassCodeRoot.run_pass

    def setUp(self):
        self._base_setup()
        P.w(os.path.join(self.bin, "runner_pool.py"), POOL_STUB, 0o755)
        P.w(os.path.join(self.bin, "stage_log.py"), WRAPPER, 0o755)
        self.run_dir = os.path.join(self.box, "runs", RUN)
        self.pidf = os.path.join(self.run_dir, "driver.pid")

    def put(self, name, text):
        P.w(os.path.join(self.home, name), text)

    def read(self, name):
        p = os.path.join(self.home, name)
        if not os.path.isfile(p):
            return ""
        with open(p, encoding="utf-8") as fh:
            return fh.read()

    def go(self, **env):
        base = dict(BROTHER_CODE_ROOT=self.code, BROTHER_RUN_DIR=self.run_dir)
        base.update(env)
        return self.run_pass(**base)

    def rows(self):
        p = os.path.join(self.run_dir, "run-state.jsonl")
        if not os.path.isfile(p):
            return []
        with open(p, encoding="utf-8") as fh:
            return [json.loads(l) for l in fh if l.strip()]

    def idle_rows(self):
        return [r for r in self.rows() if r.get("monitor") == MARK]

    def driver(self, pid):
        os.makedirs(self.run_dir, exist_ok=True)
        with open(self.pidf, "w", encoding="utf-8") as fh:
            fh.write("%d\n" % pid)

    # THE FIXTURE THAT WRITES THE ROW: no driver pid file, two admissible units
    def test_no_driver_and_admissible_work_writes_the_idle_row(self):
        self.put("dry-says", dry_look(2))
        r = self.go()
        out = r.stdout + r.stderr
        self.assertEqual(1, len(self.idle_rows()), out)
        self.assertIn("MONITOR %s" % MARK, r.stdout, out)
        self.assertIn("no driver alive and 2 admissible unit(s)", r.stdout, out)
        self.assertEqual("RUNNING", self.idle_rows()[0]["state"], "the mark never changes the run's state")

    def test_a_stale_driver_pid_writes_the_idle_row(self):
        dead = subprocess.Popen(["true"])
        dead.wait()
        self.driver(dead.pid)
        self.put("dry-says", dry_look(2))
        r = self.go()
        self.assertEqual(1, len(self.idle_rows()), r.stdout + r.stderr)

    def test_a_live_driver_writes_no_idle_row(self):
        self.driver(os.getpid())
        self.put("dry-says", dry_look(2))
        r = self.go()
        self.assertEqual([], self.idle_rows(), r.stdout + r.stderr)
        self.assertEqual(1, len(self.read("monitor-calls").splitlines()), "the monitor is still looked at once")
        self.assertNotIn("dry", self.read("pool-calls").splitlines(), "a provably live driver costs no dry look")

    def test_zero_admissible_writes_no_idle_row(self):
        self.put("dry-says", dry_look(0))
        r = self.go()
        self.assertEqual([], self.idle_rows(), r.stdout + r.stderr)
        self.assertIn("MONITOR ok", r.stdout)

    def test_a_pid_file_holding_no_pid_is_unknown_and_writes_no_row(self):
        os.makedirs(self.run_dir, exist_ok=True)
        with open(self.pidf, "w", encoding="utf-8") as fh:
            fh.write("not a pid\n")
        self.put("dry-says", dry_look(2))
        r = self.go()
        self.assertEqual([], self.idle_rows(), r.stdout + r.stderr)

    def test_pid_zero_is_not_a_live_driver(self):
        os.makedirs(self.run_dir, exist_ok=True)
        with open(self.pidf, "w", encoding="utf-8") as fh:
            fh.write("0\n")
        self.put("dry-says", dry_look(2))
        r = self.go()
        self.assertIn("dry", self.read("pool-calls").splitlines(), "pid 0 is no proof of life, so the dry look is taken")
        self.assertEqual([], self.idle_rows(), r.stdout + r.stderr)

    # THE DRY LOOK CANNOT ANSWER: NO-DATA, never zero, and never a row
    def test_a_dry_look_that_fails_is_no_data_and_the_pass_goes_on(self):
        self.put("dry-says", "FAIL\n")
        r = self.go()
        out = r.stdout + r.stderr
        self.assertEqual([], self.idle_rows(), out)
        self.assertIn("MONITOR NO-DATA", r.stdout, out)
        self.assertIn("real", self.read("pool-calls").splitlines(), "the real pool still ran")
        self.assertNotIn("--admissible", self.read("monitor-calls"), "an unread count is never handed in as a number")

    def test_a_dry_look_that_prints_nothing_is_no_data_not_zero(self):
        r = self.go()          # no dry-says file: the stub exits 0 with no output
        out = r.stdout + r.stderr
        self.assertEqual([], self.idle_rows(), out)
        self.assertIn("MONITOR NO-DATA", r.stdout, out)
        self.assertNotIn("--admissible", self.read("monitor-calls"))

    # THE MONITOR ITSELF FAILS: said, and the pass is not stopped by its watcher
    def test_a_monitor_failure_is_said_and_never_stops_the_pass(self):
        self.put("dry-says", dry_look(2))
        self.put("monitor-fails", "1\n")
        r = self.go()
        out = r.stdout + r.stderr
        self.assertIn("MONITOR NO-DATA exit=1", r.stdout, out)
        self.assertNotEqual(44, r.returncode, out)
        self.assertIn("real", self.read("pool-calls").splitlines(), "the real pool still ran")

    def test_the_pass_calls_the_monitor_exactly_once_with_both_flags(self):
        self.put("dry-says", dry_look(3))
        r = self.go()
        calls = self.read("monitor-calls").splitlines()
        self.assertEqual(1, len(calls), r.stdout + r.stderr)
        self.assertIn("--run %s" % RUN, calls[0])
        self.assertIn("--driver-pid-file %s" % self.pidf, calls[0])
        self.assertIn("--admissible 3", calls[0])
        self.assertIn("--path %s" % os.path.join(self.run_dir, "run-state.jsonl"), calls[0])

    def test_the_dry_look_runs_before_the_real_pool(self):
        self.put("dry-says", dry_look(1))
        self.go()
        self.assertEqual(["dry", "real"], self.read("pool-calls").splitlines())

    def test_a_hand_run_with_no_run_directory_says_so_and_calls_nothing(self):
        self.put("dry-says", dry_look(2))
        r = self.run_pass(BROTHER_CODE_ROOT=self.code)
        self.assertIn("MONITOR none", r.stdout, r.stdout + r.stderr)
        self.assertEqual("", self.read("monitor-calls"))
        self.assertNotIn("LOOP BLOCKED", r.stdout)


class DriverWritesPid(unittest.TestCase):
    """loop_until.sh writes the pid file the pass's monitor look reads, right after RUN PID. Reads the shipped driver as text
    and runs the shipped line in a temp shell, as scripts/loop/test_run_summary_calls.py does for the RUN PID line."""

    def lines(self):
        with open(os.path.join(HERE, "loop", "loop_until.sh"), encoding="utf-8") as fh:
            return [l.strip() for l in fh.read().splitlines() if l.strip() and not l.strip().startswith("#")]

    def test_the_driver_pid_file_follows_run_pid_and_holds_the_shell_pid(self):
        ls = self.lines()
        i = next(k for k, l in enumerate(ls) if l.startswith('echo "RUN PID $$"'))
        line = ls[i + 1]
        self.assertIn("driver.pid", line)
        box = scratch("monitor-wired-driver-")
        self.addCleanup(subprocess.run, ["rm", "-rf", box])
        log = os.path.join(box, "driver.log")
        r = subprocess.run(["bash", "-c", "BROTHER_RUN_DIR=%s\nLOG=%s\n%s\necho \"SHELL $$\"" % (box, log, line)],
                           capture_output=True, text=True, timeout=60)
        self.assertEqual(0, r.returncode, r.stderr)
        with open(os.path.join(box, "driver.pid"), encoding="utf-8") as fh:
            self.assertEqual(r.stdout.split()[-1], fh.read().strip())

    def test_an_unwritable_run_directory_is_said_not_silent(self):
        line = next(l for l in self.lines() if "driver.pid" in l)
        r = subprocess.run(["bash", "-c", "BROTHER_RUN_DIR=/nonexistent-run-dir\nLOG=/dev/null\n%s" % line],
                           capture_output=True, text=True, timeout=60)
        self.assertIn("NOTE the driver pid file could not be written", r.stdout, r.stdout + r.stderr)


class ReportShowsIdleRow(unittest.TestCase):
    """loop_report.py counts the run's run-state.jsonl idle_with_work rows (STAB1.l: not the driver log's echo of them), and
    says NO-DATA when it has no log. The wider cases, through the report's CLI, are scripts/test_run_state.py IdleSeenFromOutside."""

    def setUp(self):
        self.d = scratch("monitor-wired-report-")
        self.addCleanup(subprocess.run, ["rm", "-rf", self.d])
        sys.path.insert(0, os.path.join(HERE, "loop"))
        import loop_report
        self.LR = loop_report
        self.runs = os.path.join(self.d, "runs")
        self.run_dir = os.path.join(self.runs, "run-20261101-010000-4242")
        os.makedirs(self.run_dir)
        old = os.environ.get("BROTHER_RUNS_ROOT")
        os.environ["BROTHER_RUNS_ROOT"] = self.runs
        self.addCleanup(lambda: os.environ.__setitem__("BROTHER_RUNS_ROOT", old) if old is not None else os.environ.pop("BROTHER_RUNS_ROOT", None))

    def log(self, body):
        p = os.path.join(self.d, "loop-until-0100.log")
        with open(p, "w", encoding="utf-8") as fh:
            fh.write(body)
        return p

    def state(self, *rows):
        with open(os.path.join(self.run_dir, "run-state.jsonl"), "w", encoding="utf-8") as fh:
            fh.write("".join(json.dumps(r) + "\n" for r in rows))

    ROW = {"schema": 1, "run": "r1", "sequence": 2, "candidate": "c" * 40, "state": "RUNNING", "phase": "land", "lease_until": 9, "monitor": MARK}
    LOG = "RUN START 2026-11-01T01:00:00\nRUN PID 4242\nRUN END 2026-11-01T02:00:00\n"

    def test_the_row_is_listed_with_its_count(self):
        self.state(dict(self.ROW, monitor=None), self.ROW)
        p = self.log(self.LOG)
        rows, _ = self.LR.idle_rows(self.run_dir)
        self.assertEqual(1, len(rows))
        out = io.StringIO()
        self.LR.report(24, self.LR.PLAN, out=out, window=(0.0, 4102444800.0, "test window"), log_path=p)
        self.assertIn("IDLE WITH WORK", out.getvalue())
        self.assertIn("1 idle_with_work row(s)", out.getvalue())

    def test_a_log_with_no_row_says_none_seen_not_nothing(self):
        self.state(dict(self.ROW, monitor=None))
        p = self.log(self.LOG)
        out = io.StringIO()
        self.LR.report(24, self.LR.PLAN, out=out, window=(0.0, 4102444800.0, "test window"), log_path=p)
        self.assertIn("0 idle_with_work row(s)", out.getvalue())

    def test_no_log_is_no_data_not_zero(self):
        out = io.StringIO()
        self.LR.report(24, self.LR.PLAN, out=out)
        text = out.getvalue()
        self.assertIn("IDLE WITH WORK", text)
        self.assertIn("NO-DATA: no run is named", text[text.index("IDLE WITH WORK"):])


if __name__ == "__main__":
    unittest.main()

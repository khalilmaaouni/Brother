#!/usr/bin/env python3
"""STAB1.h's FROZEN harness: LOOP1.f's run state reports IDLE-WITH-WORK once, to the orchestrator and the board, when no
driver is alive and admission would start a unit; and reports nothing when nothing is admissible.

    python3 -B scripts/test_stage_log_idle.py IdleWithWork

RED BY ASSERTION, hermetic. Python 3.9, standard library. Written before the code and never edited by its builder.

THE ENTRY POINT. scripts/loop/stage_log.py `monitor` verb (run_main), the watcher's one look at a run, driven as a
subprocess exactly as the pass drives it. The run is written by the real `run-event` verb. The `current` verb is the
board's plain read. Nothing reaches a network, a real ledger or the person's HOME: HOME and the run log are scratch.

THE INTERFACE THIS HARNESS FREEZES (the contract names none, so the harness is where it is written down):
  monitor --run R --path P --driver-pid-file F --admissible N
    F  a file holding the driver's pid. A driver is alive when that pid names a live process. An absent file is no
       driver. A file that does not hold a pid is an UNKNOWN: it is never read as "no driver", so it reports nothing.
    N  the number of units admission would start now, a whole number of zero or more, handed in by the caller (the
       pass reads it from the pool's dry look). Anything else is a malformed call: exit 2 and nothing written.
  The first look that sees no driver alive AND N above zero appends ONE run log row whose monitor mark is
  "idle_with_work" (the record the board reads) and prints it as `"appended": "idle_with_work"` (the report the
  orchestrator reads). Every later look appends nothing. N of zero, or a live driver, reports nothing. A `current`
  read never writes, and the mark never changes the run's state.
Not frozen here, named so nobody thinks they are covered: re-reporting after the driver has come back and died again,
and a run whose state is already DONE or FAILED."""
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
STAGE_LOG = os.path.join(HERE, "loop", "stage_log.py")
CANDIDATE = "c" * 40
MARK = "idle_with_work"
FAKE_PS = """#!/bin/sh
# a scratch process table: `ps -p PID` answers a row only for a live pid, anything else answers the header alone
echo "  PID COMMAND"
if [ "$1" = "-p" ] && kill -0 "$2" 2>/dev/null; then echo "$2 sleep"; exit 0; fi
[ "$1" = "-p" ] && exit 1
exit 0
"""


class IdleWithWork(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp(prefix="stab1h-")
        self.addCleanup(shutil.rmtree, self.dir, True)
        self.log = os.path.join(self.dir, "run-state.jsonl")
        self.bin = os.path.join(self.dir, "bin")
        os.makedirs(self.bin)
        ps = os.path.join(self.bin, "ps")
        with open(ps, "w", encoding="utf-8") as fh:
            fh.write(FAKE_PS)
        os.chmod(ps, 0o755)
        self.procs = []
        self.addCleanup(self.reap)
        self.run_event()

    def reap(self):
        for p in self.procs:
            if p.poll() is None:
                p.kill()
            p.wait()

    def env(self):
        env = {k: v for k, v in os.environ.items() if not k.startswith("BROTHER_")}
        env.update(HOME=self.dir, PATH=self.bin + os.pathsep + env.get("PATH", ""))
        return env

    def call(self, *args):
        p = subprocess.run([sys.executable, "-B", STAGE_LOG] + list(args), capture_output=True, text=True,
                           timeout=120, env=self.env(), cwd=self.dir)
        return p.returncode, p.stdout.strip(), p.stderr

    def run_event(self):
        code, out, err = self.call("run-event", "--run", "r1", "--candidate", CANDIDATE, "--state", "RUNNING",
                                   "--phase", "land", "--lease-s", "1800", "--path", self.log)
        self.assertEqual(0, code, out + err)

    def monitor(self, pid_file, admissible):
        """(exit code, the printed JSON or None, raw output) of one watcher look."""
        code, out, err = self.call("monitor", "--run", "r1", "--path", self.log,
                                   "--driver-pid-file", pid_file, "--admissible", str(admissible))
        try:
            return code, json.loads(out.splitlines()[-1]), out + err
        except (ValueError, IndexError):
            return code, None, out + err

    def rows(self):
        with open(self.log, encoding="utf-8") as fh:
            return [json.loads(line) for line in fh if line.strip()]

    def idle_rows(self):
        return [r for r in self.rows() if r.get("monitor") == MARK]

    def pid_file(self, text):
        path = os.path.join(self.dir, "driver.pid-%d" % len(os.listdir(self.dir)))
        with open(path, "w", encoding="utf-8") as fh:
            fh.write(text)
        return path

    def dead_driver(self):
        gone = subprocess.Popen([sys.executable, "-c", "pass"])
        gone.wait()
        return self.pid_file("%d\n" % gone.pid)

    def live_driver(self):
        alive = subprocess.Popen(["sleep", "120"])
        self.procs.append(alive)
        return self.pid_file("%d\n" % alive.pid)

    # the positive case: no driver, work admissible
    def test_no_driver_and_admissible_work_is_reported_once_to_the_orchestrator_and_the_board(self):
        code, got, raw = self.monitor(self.dead_driver(), 2)
        self.assertEqual(0, code, raw)
        self.assertIsNotNone(got, raw)
        self.assertEqual(MARK, got.get("appended"), "the orchestrator was not told: %s" % raw)
        self.assertEqual(1, len(self.idle_rows()), "the board's record is not exactly one row: %r" % self.rows())

    def test_the_report_is_not_repeated_on_later_looks(self):
        pid_file = self.dead_driver()
        self.monitor(pid_file, 2)
        for look in range(3):
            code, got, raw = self.monitor(pid_file, 2)
            self.assertEqual(0, code, raw)
            self.assertIsNone(got.get("appended"), "look %d reported again: %s" % (look + 2, raw))
        self.assertEqual(1, len(self.idle_rows()), "later looks added rows: %r" % self.rows())

    def test_the_report_is_not_repeated_when_the_number_of_admissible_units_changes(self):
        pid_file = self.dead_driver()
        self.monitor(pid_file, 2)
        self.monitor(pid_file, 5)
        self.assertEqual(1, len(self.idle_rows()), self.rows())

    def test_a_plain_read_of_the_board_never_writes(self):
        self.monitor(self.dead_driver(), 2)
        self.assertEqual(1, len(self.idle_rows()), "no idle record to read back: %r" % self.rows())
        before = self.rows()
        for _ in range(3):
            code, out, err = self.call("current", "--run", "r1", "--path", self.log)
            self.assertEqual(0, code, out + err)
        self.assertEqual(before, self.rows(), "a read wrote a row")

    def test_the_report_row_is_a_valid_run_event_that_keeps_the_state(self):
        """Control: a row the reader would skip as malformed is no record at all; the mark is not a state change."""
        self.monitor(self.dead_driver(), 2)
        self.assertEqual(1, len(self.idle_rows()), "no idle record to read back: %r" % self.rows())
        code, out, err = self.call("current", "--run", "r1", "--path", self.log)
        self.assertEqual(0, code, out + err)
        cur = json.loads(out.splitlines()[-1])
        self.assertEqual(0, cur["skipped"], cur)
        self.assertEqual("RUNNING", cur["state"], "an idle report changed the run's state: %r" % cur)
        self.assertEqual(CANDIDATE, cur["candidate"])

    # the silent cases, each the positive case with ONE condition changed
    def test_nothing_admissible_reports_nothing_however_often_it_is_read(self):
        pid_file = self.dead_driver()
        for _ in range(3):
            code, got, raw = self.monitor(pid_file, 0)
            self.assertEqual(0, code, raw)
            self.assertIsNone(got.get("appended"), raw)
        self.assertEqual([], self.idle_rows(), self.rows())

    def test_a_live_driver_with_admissible_work_reports_nothing(self):
        pid_file = self.live_driver()
        for _ in range(2):
            code, got, raw = self.monitor(pid_file, 2)
            self.assertEqual(0, code, raw)
            self.assertIsNone(got.get("appended"), raw)
        self.assertEqual([], self.idle_rows(), self.rows())

    def test_an_absent_pid_file_is_no_driver(self):
        code, got, raw = self.monitor(os.path.join(self.dir, "never-written.pid"), 2)
        self.assertEqual(0, code, raw)
        self.assertEqual(MARK, got.get("appended"), raw)
        self.assertEqual(1, len(self.idle_rows()))

    def test_a_pid_file_that_holds_no_pid_is_unknown_and_reports_nothing(self):
        for text in ("", "not a pid\n", "-3\n", "0\n"):
            with self.subTest(text=text):
                code, got, raw = self.monitor(self.pid_file(text), 2)
                self.assertEqual(0, code, raw)
                self.assertIsNone(got.get("appended"), raw)
        self.assertEqual([], self.idle_rows(), self.rows())

    # a malformed count is refused and writes nothing
    def test_a_count_that_is_not_a_whole_number_is_refused_and_writes_nothing(self):
        pid_file = self.dead_driver()
        before = self.rows()
        for bad in ("banana", "-1", "1.5", "", "True"):
            with self.subTest(admissible=bad):
                code, _got, raw = self.monitor(pid_file, bad)
                self.assertEqual(2, code, raw)
                self.assertEqual(before, self.rows())

    # the old call shape: a look that names neither fact is the old look, and reports no idleness
    def test_a_look_that_names_neither_fact_is_the_old_look(self):
        code, out, err = self.call("monitor", "--run", "r1", "--path", self.log)
        self.assertEqual(0, code, out + err)
        self.assertIsNone(json.loads(out.splitlines()[-1]).get("appended"))
        self.assertEqual([], self.idle_rows())

    # a second actor: two watchers looking at the same moment still leave one record
    def test_two_simultaneous_looks_leave_exactly_one_record(self):
        pid_file = self.dead_driver()
        argv = [sys.executable, "-B", STAGE_LOG, "monitor", "--run", "r1", "--path", self.log,
                "--driver-pid-file", pid_file, "--admissible", "2"]
        looks = [subprocess.Popen(argv, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
                                  env=self.env(), cwd=self.dir) for _ in range(2)]
        self.procs.extend(looks)
        outs = [p.communicate(timeout=120)[0] for p in looks]
        reports = [o for o in outs if '"appended": "%s"' % MARK in o]
        self.assertEqual(1, len(reports), "two looks reported %d times: %r" % (len(reports), outs))
        self.assertEqual(1, len(self.idle_rows()), self.rows())


if __name__ == "__main__":
    unittest.main()

#!/usr/bin/env python3
"""X50.a: loop_report.py --summary LOG writes one small summary of ONE driver run, from that run's log alone.

The summary carries one state word and one next action, chosen by a fixed precedence, and is written beside the log
as <log stem>.summary.json and <log stem>.summary.md, each through a temp file and os.replace (never torn).
An unreadable or startless log reads NO-DATA (exit 2), never a zero.
Run: python3 -B scripts/loop/test_run_summary.py
"""
import datetime
import io
import json
import os
import shutil
import sys
import tempfile
import unittest
from contextlib import redirect_stdout

HERE = os.path.dirname(os.path.abspath(__file__))
_SCRATCH = os.path.expanduser("~/.claude/brother-scratch")
os.makedirs(_SCRATCH, exist_ok=True)
_HOME = tempfile.mkdtemp(prefix="run-summary-home-", dir=_SCRATCH)
os.environ["HOME"] = _HOME          # the module reads HOME at import: never this machine's evidence
os.environ.pop("BROTHER_WBS", None)
sys.path.insert(0, HERE)
import loop_report as LR  # noqa: E402

START = "RUN START %s\n" % datetime.datetime(2026, 10, 16, 1).isoformat()
END = "RUN END %s\n" % datetime.datetime(2026, 10, 16, 2).isoformat()
DEAD, ALIVE = (lambda pid: False), (lambda pid: True)


class RunSummary(unittest.TestCase):
    def setUp(self):
        self.d = tempfile.mkdtemp(prefix="run-summary-", dir=_HOME)

    def tearDown(self):
        shutil.rmtree(self.d, ignore_errors=True)

    def log(self, body, name="loop-until-20261016-0100.log"):
        p = os.path.join(self.d, name)
        with open(p, "w", encoding="utf-8") as fh:
            fh.write(body)
        return p

    def test_a_landed_run_reads_delivered(self):
        p = self.log("LOOP UNTIL 07h00\n" + START + "RUN PID %d\n" % os.getpid()
                     + "LANDED  A1.a | units with every sub unit landed (run the unit done-check): none | log x\n"
                     + "LANDED  A1.a, B2.c | units with every sub unit landed (run the unit done-check): A1 | log x\n"
                     + "ALARM DEADLINE: the run reached its deadline\n" + END)
        s = LR.run_summary(p)
        self.assertEqual(s["state"], "DELIVERED")
        self.assertEqual(s["landed"], ["A1.a", "B2.c"])
        self.assertEqual(s["next_action"], "review the landed units")

    def test_each_state_from_its_log(self):
        pid = "RUN PID 4242\n"
        cases = [
            ("NO-DATA", "LOOP UNTIL 07h00\n", ALIVE),
            ("RUNNING", START + pid + "LANDED  A1.a | log\n", ALIVE),
            ("ABORTED", START + pid + "LANDED  A1.a | log\n", DEAD),
            ("FINISHED_NO_WORK", START + pid + "LANDED  A1.a | log\nALARM FINISHED: every unit in scope is DONE\n" + END, DEAD),
            ("BLOCKED_NEEDS_HUMAN", START + pid + "LANDED  A1.a | log\nALARM STALLED: not one sub unit is admissible\n" + END, DEAD),
            ("DELIVERED", START + pid + "LANDED  A1.a | log\nALARM DEADLINE: reached\n" + END, DEAD),
            ("PROGRESSED_UNLANDED", START + pid + "READY   1 to land:\nALARM DEADLINE: reached\n" + END, DEAD),
            ("WAITING", START + pid + "ALARM DISK: the disk is full\n" + END, DEAD),
        ]
        for want, body, alive in cases:
            s = LR.run_summary(self.log(body), pid_alive=alive)
            self.assertEqual(s["state"], want, body)
            self.assertIn(s["state"], LR.STATES)
        for word in ("UNPRODUCTIVE", "BLOCKED"):
            s = LR.run_summary(self.log(START + pid + "ALARM %s: why\n" % word + END), pid_alive=DEAD)
            self.assertEqual(s["state"], "BLOCKED_NEEDS_HUMAN", word)

    def test_one_next_action_per_state(self):
        actions = set()
        for state in LR.STATES:
            self.assertTrue(LR.NEXT_ACTIONS.get(state), state)
            actions.add(LR.NEXT_ACTIONS[state])
        self.assertEqual(len(actions), len(LR.STATES), "each state names its own action")
        s = LR.run_summary(self.log(START + "ALARM BLOCKED: hub is ahead by 3 commits. NOTHING further will run\n" + END),
                           pid_alive=DEAD)
        self.assertEqual(s["next_action"], "hub is ahead by 3 commits. NOTHING further will run")
        self.assertEqual(s["alarm"], "ALARM BLOCKED: hub is ahead by 3 commits. NOTHING further will run")

    def test_a_killed_run_reads_aborted(self):
        p = self.log(START + "RUN PID 4242\n===== pass 1 (lanes 6) =====\n")
        self.assertEqual(LR.run_summary(p, pid_alive=DEAD)["state"], "ABORTED")
        self.assertEqual(LR.run_summary(p, pid_alive=ALIVE)["state"], "RUNNING")
        # no RUN PID line, or one with letters, has no pid to ask: the run is not known to be alive
        for body in (START, START + "RUN PID 42x\n", START + "RUN PID 0\n"):
            self.assertEqual(LR.run_summary(self.log(body), pid_alive=ALIVE)["state"], "ABORTED", body)
        # the default asks the operating system: this test's own pid is alive
        self.assertEqual(LR.run_summary(self.log(START + "RUN PID %d\n" % os.getpid()))["state"], "RUNNING")

    def test_summary_files_are_replaced_atomically(self):
        p = self.log(START + "RUN PID 4242\nLANDED  A1.a | log\nALARM DEADLINE: reached\n" + END)
        stem = os.path.join(self.d, "loop-until-20261016-0100")
        with open(stem + ".summary.json", "w") as fh:
            fh.write("old")
        replaced, real = [], os.replace

        def spy(src, dst):
            replaced.append((src, dst))
            self.assertEqual(os.path.dirname(src), self.d, "the temp file sits beside the log")
            return real(src, dst)
        LR.os.replace = spy
        try:
            out = LR.write_summary(p, pid_alive=DEAD)
        finally:
            LR.os.replace = real
        self.assertEqual(out, stem + ".summary.json")
        self.assertEqual(sorted(d for _, d in replaced), [stem + ".summary.json", stem + ".summary.md"])
        with open(out) as fh:
            self.assertEqual(json.load(fh)["state"], "DELIVERED")
        with open(stem + ".summary.md") as fh:
            self.assertIn("DELIVERED", fh.read())
        self.assertEqual(sorted(os.listdir(self.d)), sorted([os.path.basename(p), os.path.basename(stem) + ".summary.json",
                                                             os.path.basename(stem) + ".summary.md"]), "no temp file left")

    def test_unreadable_log_reads_no_data(self):
        missing = os.path.join(self.d, "missing.log")
        binary = os.path.join(self.d, "binary.log")
        with open(binary, "wb") as fh:
            fh.write(b"\x00\xff\xfe" * 50)
        for p in (missing, self.d, binary, self.log(""), self.log(END + START, name="backwards.log"),
                  self.log(START.replace("T01", "T03") + END, name="ends-first.log"),
                  self.log("RUN START yesterday\n", name="badstamp.log")):
            s = LR.run_summary(p, pid_alive=ALIVE)
            self.assertEqual(s["state"], "NO-DATA", p)
            self.assertEqual(s["landed"], [], p)
        buf = io.StringIO()
        with redirect_stdout(buf):
            rc = LR.main(["loop_report.py", "--summary", missing])
        self.assertEqual(rc, 2)
        self.assertIn("NO-DATA", buf.getvalue())

    def test_the_cli_writes_beside_the_log_and_exits_0(self):
        p = self.log(START + "RUN PID 4242\nREADY   2 to land:\nREJECTED A1.a gate\nQUARANTINE B1.b\nREJECTED C1.a\n"
                     "W1.a START\nALARM DEADLINE: reached\n" + END)
        buf = io.StringIO()
        with redirect_stdout(buf):
            rc = LR.main(["loop_report.py", "--summary", p])
        self.assertEqual(rc, 0)
        self.assertIn("PROGRESSED_UNLANDED", buf.getvalue())
        with open(os.path.join(self.d, "loop-until-20261016-0100.summary.json")) as fh:
            s = json.load(fh)
        self.assertEqual(s["refused"], {"REJECTED": 2, "QUARANTINE": 1})
        self.assertEqual(s["started"], 1)
        self.assertEqual(s["log"], p)

    def test_a_torn_last_line_is_ignored(self):
        p = self.log(START + "RUN PID 4242\nLANDED  A1.a | log\nALARM DEADLINE: reached\n" + END + "LANDED  B9.z, C")
        self.assertEqual(LR.run_summary(p, pid_alive=DEAD)["landed"], ["A1.a"])


if __name__ == "__main__":
    try:
        unittest.main()
    finally:
        shutil.rmtree(_HOME, ignore_errors=True)

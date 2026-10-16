#!/usr/bin/env python3
"""X50.b: the driver writes its pid after RUN START and calls loop_report.py --summary after every pass and at its end.

Reads scripts/loop/loop_until.sh as text (what ships), asserts each line sits at its anchor, and runs the extracted
lines in a temp shell: the pid line writes the shell's own pid, the summary line writes the summary beside a temp log
through the real loop_report.py, and a summary that cannot run never fails the shell that called it, and one that hangs is killed after 5 s.
Run: python3 -B scripts/loop/test_run_summary_calls.py
"""
import datetime
import json
import os
import shutil
import subprocess
import tempfile
import time
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
DRIVER = os.path.join(HERE, "loop_until.sh")
SUMMARY = ('{ perl -e \'alarm shift; exec @ARGV\' 5 python3 -B "$HOME/.claude/bin/loop_report.py" --summary "$LOG" '
           '</dev/null >/dev/null 2>&1; } 2>/dev/null || true')
RUN_PID = 'echo "RUN PID $$" >> "$LOG"'


def driver_lines(path):
    """The driver's lines, stripped, with comment-only and blank lines removed (a comment never runs)."""
    with open(path, encoding="utf-8") as fh:
        return [l.strip() for l in fh.read().splitlines() if l.strip() and not l.strip().startswith("#")]


def _index(lines, prefix, start=0):
    for i in range(start, len(lines)):
        if lines[i].startswith(prefix):
            return i
    raise AssertionError("no line starts with %r after line %d" % (prefix, start))


class SummaryCalls(unittest.TestCase):
    def setUp(self):
        self.lines = driver_lines(DRIVER)
        self.home = tempfile.mkdtemp(prefix="summary-calls-", dir=os.path.expanduser("~/.claude/brother-scratch"))
        self.log = os.path.join(self.home, "loop-until-20261016-0100.log")

    def tearDown(self):
        shutil.rmtree(self.home, ignore_errors=True)

    def shipped_calls(self):
        """The two summary lines exactly as the driver ships them: after the pass, then at the end."""
        calls = [l for l in self.lines if "loop_report.py\" --summary" in l]
        self.assertEqual(len(calls), 2, calls)
        return calls

    def sh(self, line, **env):
        return subprocess.run(["bash", "-c", "set -e\nLOG=%s\n%s\necho \"SHELL $$\"" % (self.log, line)],
                              capture_output=True, text=True, timeout=60, env=dict(os.environ, HOME=self.home, **env))

    def test_run_pid_is_logged_after_run_start(self):
        i = _index(self.lines, 'echo "RUN START ${RUN_START_ISO}"')
        self.assertEqual(self.lines[i + 1].split("   #")[0].strip(), RUN_PID)
        r = self.sh(RUN_PID)
        self.assertEqual(r.returncode, 0, r.stderr)
        shell_pid = r.stdout.split()[-1]
        with open(self.log) as fh:
            self.assertEqual(fh.read(), "RUN PID %s\n" % shell_pid)

    def test_summary_runs_after_each_pass_and_at_the_end(self):
        # after every pass: inside run_pass, right after the pass's exit code is read and its pid cleared
        f = _index(self.lines, "run_pass() {")
        w = _index(self.lines, 'wait "$PASS_PID"; RC=$?', f)
        self.assertEqual(self.lines[w + 1], 'PASS_PID=""')
        self.assertEqual(self.lines[w + 2], SUMMARY)
        self.assertEqual(self.lines[w + 3], "}")
        # at the end: inside raise, after RUN END and after this end's ALARM line, before the receipt hashes the log
        r = _index(self.lines, "raise() {")
        end = _index(self.lines, 'echo "RUN END $(date', r)
        alarm = _index(self.lines, 'echo "ALARM $1: $2${END_NOTE}" | tee -a "$LOG"', end)
        self.assertEqual(self.lines[alarm + 1], SUMMARY)
        self.assertLess(alarm + 1, _index(self.lines, "RECEIPT_OUT=$(", alarm))
        self.assertEqual(sum(1 for l in self.lines if l == SUMMARY), 2, "exactly the two calls, no third")

    def test_the_summary_line_writes_beside_the_log(self):
        os.makedirs(os.path.join(self.home, ".claude"))
        os.symlink(HERE, os.path.join(self.home, ".claude", "bin"))     # the deployed tool, read from this tree
        with open(self.log, "w") as fh:
            fh.write("RUN START %s\nRUN PID 4242\nLANDED  A1.a | log\nRUN END %s\nALARM DEADLINE: reached\n"
                     % (datetime.datetime(2026, 10, 16, 1).isoformat(), datetime.datetime(2026, 10, 16, 2).isoformat()))
        r = self.sh(SUMMARY)
        self.assertEqual(r.returncode, 0, r.stderr)
        with open(os.path.join(self.home, "loop-until-20261016-0100.summary.json")) as fh:
            self.assertEqual(json.load(fh)["state"], "DELIVERED")

    def test_a_summary_that_cannot_run_never_fails_the_driver(self):
        r = self.sh(SUMMARY)                       # no tool at $HOME/.claude/bin, under set -e
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("SHELL ", r.stdout)
        self.assertEqual(r.stderr, "", "a failing summary prints nothing into the driver's output")

    def test_a_hung_summary_holds_the_driver_five_seconds_at_most(self):
        # U12: the end report must never hold the driver; a summary that hangs is killed by its own bound
        os.makedirs(os.path.join(self.home, ".claude", "bin"))
        with open(os.path.join(self.home, ".claude", "bin", "loop_report.py"), "w") as fh:
            fh.write("import time\ntime.sleep(40)\n")
        t = time.time()
        r = self.sh(self.shipped_calls()[0])
        r2 = self.sh(self.shipped_calls()[1])
        took = time.time() - t
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(r2.returncode, 0, r2.stderr)
        self.assertLess(took, 16, "the driver waited out a hung summary (two calls, 5 s bound each)")
        self.assertEqual(r.stderr + r2.stderr, "", "the kill notice never reaches the driver's stderr")


if __name__ == "__main__":
    unittest.main()

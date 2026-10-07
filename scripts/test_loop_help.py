#!/usr/bin/env python3
"""A help flag must print help and DO NOTHING: scripts/loop/stop_loop.sh and scripts/loop/loop_until.sh.

Both ship in bundle/runtime/loop and the door names them. Before 2026-10-06 `stop_loop.sh --help` stopped the driver
and every runner of a live loop, and `loop_until.sh --help` created run scratch, pruned older scratch and overwrote
the heartbeat before refusing.

HOW THIS STAYS SAFE ON A MACHINE WITH A REAL LOOP: every run here has HOME and BROTHER_SCRATCH inside a temp folder
and a PATH whose first entry holds a stub `python3` that records its arguments and prints nothing. The scripts find
processes only through python3 (loop_procs.py), so even a script with its guard removed finds no process to stop.
The stub's record is also the proof: a guarded script never reaches python3 at all.

usage: python3 -B scripts/test_loop_help.py
"""
import os
import shutil
import subprocess
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
LOOP = os.path.join(HERE, "loop")
STUB = "#!/bin/sh\nprintf '%s\\n' \"$*\" >> \"$STUB_RECORD\"\nexit 0\n"


class HelpDoesNothing(unittest.TestCase):

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="loop-help-")
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.home = os.path.join(self.tmp, "home")
        self.scratch = os.path.join(self.tmp, "scratch")
        self.bin = os.path.join(self.tmp, "bin")
        self.record = os.path.join(self.tmp, "python3-calls.txt")
        for d in (self.home, self.scratch, self.bin):
            os.makedirs(d)
        stub = os.path.join(self.bin, "python3")
        with open(stub, "w", encoding="utf-8") as fh:
            fh.write(STUB)
        os.chmod(stub, 0o755)

    def run_script(self, name, *args):
        env = {"HOME": self.home, "BROTHER_SCRATCH": self.scratch, "STUB_RECORD": self.record,
               "PATH": self.bin + os.pathsep + "/usr/bin:/bin:/usr/sbin:/sbin",
               "BROTHER_LAUNCH_WORKTREE": self.tmp, "STOP_LOOP_ONLY": "no-such-loop-" + os.path.basename(self.tmp),
               "STOP_DRIVER_GRACE": "1"}
        try:
            proc = subprocess.run(["/bin/bash", os.path.join(LOOP, name)] + list(args), cwd=self.tmp, env=env,
                                  capture_output=True, text=True, timeout=20, stdin=subprocess.DEVNULL)
        except subprocess.TimeoutExpired as exc:
            self.fail("%s %s did not return in 20 s: it started doing its work (%s)" % (name, " ".join(args), exc))
        return proc

    def touched(self):
        """Everything the run left behind outside the stub folder: any file is a side effect."""
        found = []
        for base in (self.home, self.scratch):
            for dirpath, dirnames, filenames in os.walk(base):
                found.extend(os.path.relpath(os.path.join(dirpath, n), self.tmp) for n in dirnames + filenames)
        return sorted(found)

    def assert_no_side_effect(self, proc, what):
        self.assertFalse(os.path.exists(self.record),
                         "%s reached python3, so it went past its argument check: %s"
                         % (what, open(self.record).read() if os.path.exists(self.record) else ""))
        self.assertEqual(self.touched(), [], "%s wrote under HOME or the scratch root" % what)

    def test_help_prints_the_usage_and_exits_zero_having_done_nothing(self):
        for name, needle in (("stop_loop.sh", "usage: stop_loop.sh"), ("loop_until.sh", "usage: loop_until.sh")):
            for flag in ("--help", "-h"):
                with self.subTest(script=name, flag=flag):
                    proc = self.run_script(name, flag)
                    self.assertEqual(proc.returncode, 0, proc.stderr[-300:])
                    self.assertIn(needle, proc.stdout)
                    self.assert_no_side_effect(proc, "%s %s" % (name, flag))

    def test_an_unknown_flag_is_refused_before_anything_is_touched(self):
        for name, flag in (("stop_loop.sh", "--dry-run"), ("stop_loop.sh", "--stop-everything"),
                           ("loop_until.sh", "--dry-run"), ("loop_until.sh", "--until")):
            with self.subTest(script=name, flag=flag):
                proc = self.run_script(name, flag)
                self.assertEqual(proc.returncode, 2, proc.stdout[-200:] + proc.stderr[-200:])
                self.assertIn("unknown argument", proc.stderr)
                self.assert_no_side_effect(proc, "%s %s" % (name, flag))

    def test_the_known_stop_arguments_still_reach_the_process_reader(self):
        """The guard must not swallow real use: a plain stop and --runners-only still ask loop_procs.py (the stub
        here) who is alive. Without this case a guard that refused everything would pass the two tests above."""
        for args in ((), ("--runners-only",), ("--dry",), ("--reason", "a stated reason")):
            with self.subTest(args=args):
                if os.path.exists(self.record):
                    os.remove(self.record)
                self.run_script("stop_loop.sh", *args)
                self.assertTrue(os.path.exists(self.record), "stop_loop.sh %s never asked for the process list" % (args,))
                self.assertIn("loop_procs.py", open(self.record).read())

    def test_the_bundle_ships_the_guarded_scripts(self):
        root = os.path.dirname(HERE)
        for name in ("stop_loop.sh", "loop_until.sh"):
            with open(os.path.join(LOOP, name), "rb") as a, open(os.path.join(root, "bundle", "runtime", "loop", name), "rb") as b:
                self.assertEqual(a.read(), b.read(), "bundle/runtime/loop/%s is not the guarded script" % name)


if __name__ == "__main__":
    unittest.main()

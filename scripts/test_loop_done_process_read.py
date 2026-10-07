#!/usr/bin/env python3
"""loop_done never says FINISHED on a process table it did not actually read. Driven at its entry point.

WHY (finding 9 of the landing audit, 2026-09-27). live_runners() caught an exception from `ps` but
ignored its exit status, so a refused read returned empty output, empty output counted zero runners,
and a board whose last unit had closed read FINISHED while a runner might still be alive. The
unattended loop stops on FINISHED, so this is the one wrong answer that ends a run early.

Each case puts a stand in `ps` first on PATH and runs loop_done.py as the loop does, in a scratch
tree whose plan reads finished and whose run history is present and empty. The first case proves the
fixture CAN finish, so every NO-DATA below is the process table and nothing else. The refused read is
tested twice on purpose: once printing a plausible table at exit 1 (only the exit status guard can
refuse it) and once printing nothing at exit 0 (only the empty table guard can).
"""
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
LOOP_DONE = os.path.join(HERE, "loop", "loop_done.py")
# Rows are "pid ppid pgid command"; the stand in `ps` prints them whole for loop_procs' read (-o pid=,ppid=,pgid=,command=)
# and as a COMMAND column for a `ps -eo command` read, so one fixture drives either reader.
TABLE = b"1 0 1 /sbin/launchd\n99 1 99 ps -ax -ww -o pid=,ppid=,pgid=,command=\n"
# A runner is the loop's only from the deployed bin copy, <HOME>/.claude/bin (or a run of this loop): the row is built per
# fixture HOME by LoopDoneProcessRead.runner().
RUNNER_ROW = b"4242 1 4242 python3 %s/.claude/bin/unit_runner.py D1 D1.2\n"
VIEWER = b"41001 1 41001 python3 /fixture/viewer.py /fixture/bin/unit_runner.py X X.1\n"


class LoopDoneProcessRead(unittest.TestCase):
    def setUp(self):
        self.root = tempfile.mkdtemp(prefix="loop-done-ps-")
        self.addCleanup(shutil.rmtree, self.root, ignore_errors=True)
        self.home = os.path.join(self.root, "home")
        os.makedirs(os.path.join(self.home, ".claude", "evidence", "unit-runs"))
        self.tree = os.path.join(self.root, "tree")
        os.makedirs(os.path.join(self.tree, "docs", "plan"))
        with open(os.path.join(self.tree, "docs", "plan", "BROTHER-1.1.0-LAUNCH-WBS.json"), "w") as fh:
            json.dump({"units": [{"id": "U1", "state": "DONE", "evidence": "checked"}]}, fh)
        self.bin = os.path.join(self.root, "bin")
        os.makedirs(self.bin)

    def runner(self):
        """a runner of this loop: the deployed bin copy under the fixture HOME, the one place the count owns a runner by"""
        return RUNNER_ROW % self.home.encode()

    def run_with_ps(self, out, code, path=None):
        """loop_done.py with a `ps` that prints `out` (bytes) and exits `code`."""
        with open(os.path.join(self.bin, "table"), "wb") as fh:
            fh.write(out)
        with open(os.path.join(self.bin, "ps"), "w") as fh:
            fh.write("#!/bin/sh\ncase \"$*\" in *pid=*) cat '%s';; *) echo COMMAND; LC_ALL=C cut -d' ' -f4- '%s';; esac\nexit %d\n"
                     % (os.path.join(self.bin, "table"), os.path.join(self.bin, "table"), code))
        os.chmod(os.path.join(self.bin, "ps"), 0o755)
        env = dict(os.environ, HOME=self.home, PATH=path or (self.bin + os.pathsep + os.environ["PATH"]))
        r = subprocess.run([sys.executable, "-B", LOOP_DONE], cwd=self.tree, env=env,
                           capture_output=True, text=True, timeout=120)
        return r.returncode, r.stdout + r.stderr

    def assertNoData(self, got):
        code, out = got
        self.assertEqual(code, 2, out)
        self.assertIn("NO-DATA", out)
        self.assertIn("process table", out)

    def test_the_fixture_can_finish(self):
        code, out = self.run_with_ps(TABLE, 0)
        self.assertEqual(code, 0, out)
        self.assertIn("FINISHED", out)

    def test_a_refused_read_is_no_data_even_when_it_printed_a_table(self):
        self.assertNoData(self.run_with_ps(TABLE, 1))

    def test_an_empty_table_at_exit_0_is_no_data(self):
        self.assertNoData(self.run_with_ps(b"", 0))

    def test_a_header_only_table_is_no_data(self):
        self.assertNoData(self.run_with_ps(b"COMMAND\n", 0))

    def test_a_refused_read_with_nothing_printed_is_no_data(self):
        self.assertNoData(self.run_with_ps(b"", 1))

    def test_a_missing_ps_is_no_data(self):
        self.assertNoData(self.run_with_ps(TABLE, 0, path=os.path.join(self.root, "empty-path")))

    def test_a_live_runner_keeps_it_working(self):
        code, out = self.run_with_ps(TABLE + self.runner(), 0)
        self.assertEqual(code, 1, out)
        self.assertIn("unit runner", out)

    def test_an_undecodable_byte_in_the_table_still_counts_the_runner(self):
        code, out = self.run_with_ps(TABLE + b"7 1 7 odd \xff\xfe name\n" + self.runner(), 0)
        self.assertEqual(code, 1, out)
        self.assertIn("unit runner", out)

    def test_a_process_that_only_names_a_runner_is_not_one(self):
        """X2 finding 7: loop_procs owns a loop tool only at the program position (the executable, or an interpreter's
        script), and a stop never signals a viewer whose ARGUMENT names bin/unit_runner.py. This count read any
        command line mentioning the file, so that viewer kept a finished board WORKING after every runner had gone."""
        code, out = self.run_with_ps(TABLE + VIEWER, 0)
        self.assertEqual(code, 0, out)
        self.assertIn("FINISHED", out)


class SelftestRefusesWhenItRaises(unittest.TestCase):
    """The selftest's wrapper turns a raising case into a readable refusal that still exits non zero (a survivor of
    the X2 mutation sweep: its `return 1` could become `return 0` with every check green)."""

    def test_a_selftest_that_raises_fails(self):
        import contextlib, io
        from unittest import mock
        sys.path.insert(0, os.path.join(HERE, "loop"))
        import loop_done
        out = io.StringIO()
        with mock.patch.object(loop_done, "_selftest_body", side_effect=RuntimeError("boom")), contextlib.redirect_stdout(out):
            code = loop_done.selftest()
        self.assertEqual(code, 1, out.getvalue())
        self.assertIn("FAILED before it could finish", out.getvalue())


if __name__ == "__main__":
    unittest.main()

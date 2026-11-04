"""One unreadable stranger row stays one unknown row (STAB1.d, frozen harness, the implementer never edits this file).

WHAT IT PROTECTS: loop_procs.snapshot() reads the environment (`ps -E`) of every loop tool running outside the loop's bin,
and one such read that fails made the WHOLE table None. A stranger session running scripts/diag_round.py from a worktree
then nulled the live unit runner count, loop_done read "the process table could not be read", and a push was refused
(2026-11-01, scripts/test_one_landed_test.py EntryPoints red while other sessions ran loop scripts).
CONTRACT: snapshot() keeps a row whose environment cannot be read, with run_dir None (run_dir_of's own "could not be
read" value); owned() answers None (unknown) only when such a row matches the pattern being asked; an unknown
unit_runner.py row still makes the unit runner count unknown. A malformed row still makes the whole table None.
Driven at snapshot(), owned() and live_runner_count() over a faked `ps` (subprocess.run patched, the table and each -E read
answered from fixed data), HOME the placeholder /Users/you with no BROTHER_* variable: nothing real is read or spawned.
Run from the repository root: python3 -B scripts/test_procs_row_unknown.py
"""
import os, subprocess, sys, unittest
from unittest import mock

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "loop"))
import loop_procs as LP  # noqa: E402

HOME = "/Users/you"   # never created: loop_bin() only resolves it (scripts/test_export_public.py allows this home only)
RUNNER = "/usr/bin/python3 %s/.claude/bin/unit_runner.py U1 U1.a" % HOME            # owned: its program is in the bin
STRANGER_DIAG = "/usr/bin/python3 %s/Brother-wt/other/scripts/diag_round.py --round 3" % HOME   # a worktree's helper
STRANGER_RUNNER = "/usr/bin/python3 %s/.claude/brother-scratch/straggler-1/bin/unit_runner.py U9 U9.a" % HOME
VIEWER = "/usr/bin/python3 /v/viewer.py %s/.claude/bin/unit_runner.py" % HOME        # an argument, never a program


def env():
    e = {k: v for k, v in os.environ.items() if not k.startswith("BROTHER_") and k != "STOP_LOOP_ONLY"}
    e["HOME"] = HOME
    return e


def fake_ps(lines, unreadable=()):
    """subprocess.run answering snapshot()'s two shapes: the table read prints `lines`; `ps -E ... -p PID` fails like a
    permission refusal (exit 1, a message on stderr) for a pid in `unreadable`, and answers 'gone' for any other."""
    def run(argv, **kw):
        if "-E" in argv:
            pid = argv[argv.index("-p") + 1]
            if int(pid) in unreadable:
                return subprocess.CompletedProcess(argv, 1, "", "ps: operation not permitted\n")
            return subprocess.CompletedProcess(argv, 1, "", "")
        return subprocess.CompletedProcess(argv, 0, "".join(l + "\n" for l in lines), "")
    return run


class RowScopedUnknown(unittest.TestCase):
    def setUp(self):
        p = mock.patch.dict(os.environ, env(), clear=True)
        p.start(); self.addCleanup(p.stop)

    def read(self, lines, unreadable=()):
        """snapshot() and live_runner_count() at their entry points, both over the same faked ps"""
        with mock.patch.object(LP.subprocess, "run", side_effect=fake_ps(lines, unreadable)):
            return LP.snapshot(errors="replace"), LP.live_runner_count()

    def test_1_an_unreadable_stranger_helper_is_kept_unknown_and_the_count_stands(self):
        snap, count = self.read(["1 0 1 /sbin/launchd", "4242 1 4242 " + RUNNER, "5151 1 5151 " + STRANGER_DIAG],
                                unreadable={5151})
        self.assertIsNotNone(snap, "one unreadable stranger row made the whole table unknown")
        rows = {r["pid"]: r for r in snap}
        self.assertEqual(sorted(rows), [1, 4242, 5151], "the table lost or invented a row")
        self.assertIn("run_dir", rows[5151], "the stranger row carries no run_dir verdict at all")
        self.assertIsNone(rows[5151]["run_dir"], "an unreadable environment must read as unknown (None), never as 'no run'")
        self.assertEqual(count, 1, "the live unit runner count must stand when the unknown row is not a unit runner")

    def test_2_an_unreadable_unit_runner_outside_the_bin_makes_the_count_unknown(self):
        snap, count = self.read(["1 0 1 /sbin/launchd", "4242 1 4242 " + RUNNER, "6161 1 6161 " + STRANGER_RUNNER],
                                unreadable={6161})
        self.assertIsNotNone(snap, "the table itself was readable; only one row's environment was not")
        self.assertIsNone(count, "an unknown unit_runner.py row was counted as a number (it may be a live runner)")

    def test_3_owned_is_unknown_only_for_a_pattern_the_unknown_row_matches(self):
        snap = [dict(pid=1, ppid=0, pgid=1, command="/sbin/launchd"),
                dict(pid=4242, ppid=1, pgid=4242, command=RUNNER),
                dict(pid=5151, ppid=1, pgid=5151, command=STRANGER_DIAG, run_dir=None)]   # snapshot()'s unknown row
        self.assertEqual(LP.owned(snap, LP.UNIT_RUNNER_RX, generic=False), {4242},
                         "a pattern the unknown row does not match must answer normally")
        self.assertIsNone(LP.owned(snap, LP.RUNNERS_RX), "a pattern the unknown row matches must answer unknown (None)")

    def test_4_a_malformed_row_still_makes_the_whole_table_unknown(self):
        snap, count = self.read(["1 0 1 /sbin/launchd", "4242 1 4242 " + RUNNER, "4343 unreadable 4343 " + RUNNER])
        self.assertIsNone(snap, "a malformed row was accepted: a partial table is an unknown")
        self.assertIsNone(count, "a malformed table produced a runner count")

    def test_5_no_stranger_rows_counts_the_owned_runners(self):
        snap, count = self.read(["1 0 1 /sbin/launchd", "4242 1 4242 " + RUNNER,
                                 "4244 1 4244 " + RUNNER.replace("U1.a", "U1.b"), "4343 1 4343 " + VIEWER])
        self.assertEqual(sorted(r["pid"] for r in snap), [1, 4242, 4244, 4343])
        self.assertEqual(count, 2)


if __name__ == "__main__":
    unittest.main()

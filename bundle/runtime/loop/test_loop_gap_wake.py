#!/usr/bin/env python3
"""SP5: the gap between passes ends early when a run STATUS reading READY is newer than the pass start; nothing else does.

The gap block runs from loop_until.sh's own source, between its PASS GAP BEGIN and END markers, in a temp shell with a
fake HOME, a fake unit-runs folder and a 1 s slice, so the test reads what ships. A STATUS it cannot read (a folder,
bytes that are not UTF-8, an empty file), any word but exactly READY, a READY older than the pass start, a missing pass
mark or a missing runs folder never wakes the driver; a pause or hold is still read every slice, before the READY look.
Run: python3 -B scripts/loop/test_loop_gap_wake.py
"""
import os
import shutil
import subprocess
import tempfile
import time
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
SRC = open(os.path.join(HERE, "loop_until.sh"), encoding="utf-8").read()
GAP = 4
EARLY, FULL = 2.0, GAP - 1.2   # date +%s truncates, so a full gap lasts between GAP - 1 and GAP + one slice


def block(name):
    start = SRC.index("# %s BEGIN" % name)
    return SRC[start:SRC.index("# %s END" % name, start)]


def nap_line():
    return next(l for l in SRC.splitlines() if l.startswith("nap() {"))


class GapWake(unittest.TestCase):
    def setUp(self):
        self.home = tempfile.mkdtemp(prefix="gap-wake-", dir=os.path.expanduser("~/.claude/brother-scratch"))
        self.ev = os.path.join(self.home, ".claude", "evidence")
        self.runs = os.path.join(self.ev, "unit-runs")
        os.makedirs(self.runs)
        self.log = os.path.join(self.home, "driver.log")
        self.mark = os.path.join(self.home, "pass-start")
        open(self.mark, "w").close()
        self.t0 = os.stat(self.mark).st_mtime

    def tearDown(self):
        shutil.rmtree(self.home, ignore_errors=True)

    def status(self, run, data, age=None):
        d = os.path.join(self.runs, run)
        os.makedirs(d, exist_ok=True)
        p = os.path.join(d, "STATUS")
        with open(p, "wb") as fh:
            fh.write(data)
        when = self.t0 + 5 if age is None else self.t0 - age   # newer than the pass start, or older by age seconds
        os.utime(p, (when, when))
        return p

    def gap(self, slice_s="1", pre=""):
        """Runs the shipped gap block once; answers (seconds it took, the driver log text)."""
        script = "\n".join([
            nap_line(),
            "control_clear() { CONTROL_WHY=test; return 1; }",   # a control file present is a control in force
            "GAP=%d; DRAIN=0; PASS=7; LOG=%s; PASS_MARK=%s; STOP_EPOCH=$(( $(date +%%s) + 600 ))" % (GAP, self.log, self.mark),
            pre,
            "while :; do",
            block("PASS GAP"),
            "break; done",
        ])
        t = time.time()
        r = subprocess.run(["bash", "-c", script], capture_output=True, text=True, timeout=60,
                           env=dict(os.environ, HOME=self.home, BROTHER_GAP_SLICE_S=slice_s))
        self.assertEqual(r.returncode, 0, r.stderr)
        text = open(self.log).read() if os.path.exists(self.log) else ""
        return time.time() - t, text

    def test_a_ready_newer_than_the_pass_start_ends_the_gap_in_one_slice(self):
        for i in range(30):
            self.status("A%d.a-0100%02d" % (i, i), b"EXHAUSTED no repair\n")
        self.status("B1.a-010203", b"READY /x/B1.a-r0-build.json\n")
        took, log = self.gap()
        self.assertLess(took, EARLY, "the gap ended early")
        self.assertIn("GAP WAKE: B1.a-010203 reached READY after pass 7 began", log)

    def test_nothing_else_wakes_the_gap(self):
        self.status("old-ready", b"READY /x/old.json\n", age=60)            # stale: written before this pass
        self.status("unprobed", b"READY-UNPROBED /x/u.json\n")             # never a prefix
        self.status("exhausted", b"EXHAUSTED why\n")
        self.status("empty", b"")
        self.status("torn", b"READY \xff\xfe /x\n")                         # not UTF-8: unreadable
        os.makedirs(os.path.join(self.runs, "folder", "STATUS"))           # a STATUS that is not a file
        open(os.path.join(self.runs, "loose-file"), "w").close()
        took, log = self.gap()
        self.assertGreaterEqual(took, FULL, "the gap ran its full length")
        self.assertNotIn("GAP WAKE", log)

    def test_an_unreadable_mark_or_runs_folder_never_wakes(self):
        self.status("B1.a-010203", b"READY /x/b.json\n")
        os.unlink(self.mark)
        took, log = self.gap()
        self.assertGreaterEqual(took, FULL)
        self.assertNotIn("GAP WAKE", log)
        open(self.mark, "w").close()
        shutil.rmtree(self.runs)
        took, log = self.gap()
        self.assertGreaterEqual(took, FULL)
        self.assertNotIn("GAP WAKE", log)

    def test_a_pause_is_still_read_every_slice_before_the_ready_look(self):
        self.status("B1.a-010203", b"READY /x/b.json\n")
        with open(os.path.join(self.ev, "LOOP-PAUSE.txt"), "w") as fh:
            fh.write("owner pause\n")
        took, log = self.gap()
        self.assertLess(took, EARLY, "the pause ends the gap as it always did")
        self.assertNotIn("GAP WAKE", log, "the pause is read first, and the pause handling owns what happens next")

    def test_a_bad_slice_value_falls_back_to_ten_seconds(self):
        for bad in ("0", "abc", "-1", "05"):
            script = "\n".join([l for l in block("PASS GAP").splitlines() if "GAP_SLICE=" in l] + ['echo "$GAP_SLICE"'])
            r = subprocess.run(["bash", "-c", script], capture_output=True, text=True, timeout=30,
                               env=dict(os.environ, BROTHER_GAP_SLICE_S=bad))
            self.assertEqual(r.stdout.strip(), "10", bad)

    def test_the_pass_mark_is_written_right_before_each_pass(self):
        lines = [l.strip() for l in SRC.splitlines() if l.strip() and not l.strip().startswith("#")]
        i = lines.index("run_pass")
        self.assertTrue(lines[i - 1].startswith('PASS_MARK="${RUN_TMP}/pass-start"; { : > "$PASS_MARK"; } 2>/dev/null'),
                        lines[i - 1])
        self.assertLess(SRC.index("run_pass\n  # THE PULSE"), SRC.index("# PASS GAP BEGIN"))


if __name__ == "__main__":
    unittest.main()

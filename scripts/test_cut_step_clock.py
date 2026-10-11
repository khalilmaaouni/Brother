#!/usr/bin/env python3
"""FC1.c (docs/plan/specs/FC1.md): every step of scripts/cut_v1.0.0.sh prints
its own seconds beside its own id, the failing step included, and the clock
never changes the script's exit status.

Measured 2026-10-11: the old single clock line printed the previous step's
seconds under the next step's id (the 2,395 s of step 2d read "2t +2395s"),
and a failing step printed nothing.

HOW. The live clock block is cut out of the script between its two marker
lines and run under `set -e` with a small body of steps, in a throwaway
folder, the way test_cut_bump_commit.py runs its block.

Python 3.9, standard library only. No network. No em or en dashes.
"""
import os
import re
import shutil
import subprocess
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
CUT_SCRIPT = os.path.join(HERE, "cut_v1.0.0.sh")
BEGIN = "# --- step clock: begin"
END = "# --- step clock: end"
START_RE = re.compile(r"^\[step \d\d:\d\d:\d\d\] start (\S+), total (\d+)s$", re.M)
END_RE = re.compile(r"^\[step \d\d:\d\d:\d\d\] end (\S+) after (\d+)s, exit (\d+), total (\d+)s$", re.M)
STEP_IDS = ("1", "1b", "2", "2r", "2s", "2b", "2b2", "2c", "2d", "2t", "3", "4", "STOP")
BANNERS = ("== 2s.", "== 2b.", "== 2b2.", "== 2c.", "== 2t.", "== 3.")


def _text(path=CUT_SCRIPT):
    with open(path, encoding="utf-8") as fh:
        return fh.read()


def clock_block(path=CUT_SCRIPT):
    """The live lines from BEGIN to END; each marker exactly once, and step 1's clock after END."""
    text = _text(path)
    lines = text.splitlines()
    for marker in (BEGIN, END):
        found = [l for l in lines if l.strip() == marker]
        if len(found) != 1:
            raise AssertionError("%r occurs %d times in %s" % (marker, len(found), path))
    start = next(i for i, l in enumerate(lines) if l.strip() == BEGIN)
    end = next(i for i, l in enumerate(lines) if l.strip() == END)
    if 'step_clock "1"' not in "\n".join(lines[end:]):
        raise AssertionError('step_clock "1" does not follow the clock block')
    return "\n".join(lines[start:end + 1]) + "\n"


class ClockCase(unittest.TestCase):

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="test-cut-step-clock-")
        self.addCleanup(shutil.rmtree, self.tmp, True)

    def run_fixture(self, body):
        path = os.path.join(self.tmp, "fixture.sh")
        with open(path, "w", encoding="utf-8") as fh:
            fh.write("set -e\n" + clock_block() + body)
        return subprocess.run(["sh", path], cwd=self.tmp, capture_output=True, text=True, timeout=60)

    @staticmethod
    def ends(text):
        return dict((m.group(1), (int(m.group(2)), int(m.group(3)))) for m in END_RE.finditer(text))

    def test_each_step_prints_its_own_seconds(self):
        proc = self.run_fixture("step_clock A\nsleep 2\nstep_clock B\necho done\n")
        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
        self.assertEqual([m.group(1) for m in START_RE.finditer(proc.stdout)], ["A", "B"])
        ends = self.ends(proc.stdout)
        self.assertGreaterEqual(ends["A"][0], 2, proc.stdout)
        self.assertEqual(ends["A"][1], 0)
        self.assertLessEqual(ends["B"][0], 1, proc.stdout)
        self.assertEqual(ends["B"][1], 0)
        last = [l for l in proc.stdout.splitlines() if l.startswith("[step ")][-1]
        self.assertRegex(last, r"\] end B after ")

    def test_a_failing_step_prints_its_own_end_line(self):
        proc = self.run_fixture("step_clock A\nsleep 2\nstep_clock B\nfalse\necho never\n")
        self.assertEqual(proc.returncode, 1, proc.stdout + proc.stderr)
        self.assertNotIn("never", proc.stdout)
        ends = self.ends(proc.stdout)
        self.assertEqual(ends["B"][1], 1, proc.stdout)
        self.assertLessEqual(ends["B"][0], 1, proc.stdout)
        self.assertEqual(ends["A"][1], 0)
        self.assertGreaterEqual(ends["A"][0], 2, proc.stdout)

    def test_the_trap_keeps_the_exit_status(self):
        proc = self.run_fixture("step_clock A\nstep_clock B\n(exit 3)\necho never\n")
        self.assertEqual(proc.returncode, 3, proc.stdout + proc.stderr)
        self.assertEqual(self.ends(proc.stdout)["B"][1], 3, proc.stdout)

    def test_every_step_id_and_banner_is_kept(self):
        text = _text()
        for sid in STEP_IDS:
            with self.subTest(step=sid):
                self.assertIn('step_clock "%s"' % sid, text)
        for banner in BANNERS:
            with self.subTest(banner=banner):
                self.assertIn('echo "%s' % banner, text)


if __name__ == "__main__":
    unittest.main()

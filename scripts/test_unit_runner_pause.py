"""A pause or the owner's HOLD reaches every loop route that buys model calls (review item 1, 2026-09-26).

Measured the morning this was written: the driver honoured ~/.claude/evidence/LOOP-PAUSE.txt, but unit_runner.py had
no check, so a paused loop kept a probe wave with two paid calls alive until a hand stop. The contract:
  - scripts/loop/loop_hold.py is the ONE reader: LOOP-PAUSE.txt or LOOP-HOLD.txt present means held, and a control file
    that cannot be read (a directory in its place, a permission error) is held too, never "not held";
  - every entry point that buys calls (unit_runner, probe_wave, check_wave, finish_run) exits 5 (HELD) before it
    imports its model seams or writes anything, and unit_runner checks again before each paid stage of every round.
Run from the repository root: python3 -B scripts/test_unit_runner_pause.py
"""
import os, re, subprocess, sys, tempfile, unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
LOOP = os.path.join(ROOT, "scripts", "loop")
sys.path.insert(0, LOOP)


def clip(text, n=300):
    """Quoted child output, with the shell's own missing-file wording reworded: the engine's broken-check screen
    (brother_run BROKEN_CHECK_STDERR_PATTERNS) substring matches stderr, so a quoted phrase would read as a check that
    never ran rather than the failing assertion it is (engine finding 25, 2026-09-26)."""
    for a, b in (("No such file or directory", "no such path"), ("SyntaxError", "syntax error"), ("command not found", "command missing")):
        text = text.replace(a, b)
    return text[-n:]


def home_with(**files):
    h = tempfile.mkdtemp(prefix="hold-home-")
    ev = os.path.join(h, ".claude", "evidence")
    os.makedirs(ev)
    for name, body in files.items():
        p = os.path.join(ev, name.replace("_", "-") + ".txt")
        if body is None:
            os.makedirs(p)                 # a directory where the control file belongs: unreadable
        else:
            with open(p, "w") as f:
                f.write(body)
    return h


class Reader(unittest.TestCase):
    def reason(self, home):
        import importlib, loop_hold
        importlib.reload(loop_hold)
        return loop_hold.reason(os.path.join(home, ".claude", "evidence"))

    def test_neither_file_is_not_held(self):
        self.assertIsNone(self.reason(home_with()))

    def test_pause_file_holds_and_names_itself(self):
        r = self.reason(home_with(LOOP_PAUSE="owner pause for a presentation\n"))
        self.assertIn("PAUSE", r)
        self.assertIn("presentation", r)

    def test_hold_file_holds_and_names_itself(self):
        r = self.reason(home_with(LOOP_HOLD="owner stop 07:01\n"))
        self.assertIn("HOLD", r)

    def test_unreadable_control_file_is_held(self):
        r = self.reason(home_with(LOOP_HOLD=None))
        self.assertIsNotNone(r, "a control file that cannot be read must hold, never read as absent")

    def test_an_unsearchable_control_folder_is_held(self):
        # port attack 2026-09-26 (port-inventory/port-muse.md item 2): os.path.lexists() answers False both for "absent"
        # and for "cannot look", so a control folder nobody may search read as not held. Only "not found" is absent.
        home = home_with(LOOP_HOLD="owner stop\n")
        ev = os.path.join(home, ".claude", "evidence")
        os.chmod(ev, 0)
        try:
            self.assertIsNotNone(self.reason(home), "a control folder that cannot be searched must hold")
        finally:
            os.chmod(ev, 0o700)

    def test_a_control_root_that_is_a_file_is_held(self):
        home = home_with()
        ev = os.path.join(home, ".claude", "evidence")
        for name in os.listdir(ev):
            os.remove(os.path.join(ev, name))
        os.rmdir(ev)
        with open(ev, "w") as fh:
            fh.write("not a folder\n")
        self.assertIsNotNone(self.reason(home), "a control root that is not a folder must hold")

    def test_a_control_root_that_is_a_link_to_nothing_is_held(self):
        # DeepSeek adversary 2026-09-26 (or-lanes/b4f-phold-ds2.md F1): a dangling link in the folder's place made every
        # control file read as "not found", so the reader said not held. A link to nothing is corrupt state, not absence.
        home = home_with()
        ev = os.path.join(home, ".claude", "evidence")
        for name in os.listdir(ev):
            os.remove(os.path.join(ev, name))
        os.rmdir(ev)
        os.symlink(os.path.join(home, "no-such-folder"), ev)
        self.assertIsNotNone(self.reason(home), "a control root that is a dangling link must hold")

    def test_a_control_root_that_is_a_link_loop_is_held(self):
        home = home_with()
        ev = os.path.join(home, ".claude", "evidence")
        for name in os.listdir(ev):
            os.remove(os.path.join(ev, name))
        os.rmdir(ev)
        os.symlink(ev, ev)
        self.assertIsNotNone(self.reason(home), "a control root that is a link loop must hold")

    def test_a_missing_control_root_is_not_held(self):
        # today's meaning, kept: no folder means no HOLD or PAUSE file can exist; the port design decides whether a
        # missing root should hold on a fresh host
        home = home_with()
        ev = os.path.join(home, ".claude", "evidence")
        for name in os.listdir(ev):
            os.remove(os.path.join(ev, name))
        os.rmdir(ev)
        self.assertIsNone(self.reason(home))


class EntryPoints(unittest.TestCase):
    def run_held(self, script, args):
        h = home_with(LOOP_HOLD="owner stop (fixture)\n")
        work = tempfile.mkdtemp(prefix="hold-work-")
        argv = [sys.executable, "-B", os.path.join(LOOP, script)] + [a.format(w=work) for a in args]
        r = subprocess.run(argv, cwd=work, env=dict(os.environ, HOME=h), capture_output=True, text=True, timeout=60)
        return r, work

    def check(self, script, args, untouched):
        r, work = self.run_held(script, args)
        out = r.stdout + r.stderr
        self.assertEqual(r.returncode, 5, "%s under a HOLD exited %d: %s" % (script, r.returncode, clip(out)))
        self.assertIn("HELD", out)
        for rel in untouched:
            self.assertFalse(os.path.exists(os.path.join(work, rel)), "%s wrote %s while held" % (script, rel))

    def test_unit_runner(self):
        self.check("unit_runner.py", ["H3", "H3.d"], [])

    def test_probe_wave(self):
        self.check("probe_wave.py", ["{w}/wave", "{w}/probes", "^X$"], ["probes"])

    def test_check_wave(self):
        self.check("check_wave.py", ["{w}/build.json"], [])

    def test_finish_run(self):
        self.check("finish_run.py", ["--model", "deepseek"], [])


class EveryPaidStageIsGated(unittest.TestCase):
    """Structural, because unit_runner is a top level script: each launch of a paid stage inside the round loop has a
    hold check in the lines just before it. Deleting any one of those checks turns this red."""
    PAID = (r"core\.or_fanout", r'"probe_wave\.py"', r'"check_wave\.py"')

    def test_each_paid_launch_is_preceded_by_a_hold_check(self):
        lines = open(os.path.join(LOOP, "unit_runner.py"), encoding="utf-8").read().splitlines()
        start = next(i for i, l in enumerate(lines) if l.startswith("for rnd in range("))
        misses = []
        for i in range(start, len(lines)):
            if any(re.search(p, lines[i]) for p in self.PAID) and ("Popen" in lines[i] or "run_bounded" in lines[i]):
                window = "\n".join(lines[max(start, i - 12):i])
                if "hold_gate(" not in window:
                    misses.append("%d: %s" % (i + 1, lines[i].strip()[:90]))
        self.assertEqual(misses, [], "paid launches with no hold check before them")
        self.assertTrue(any("hold_gate(" in l for l in lines[:start]), "no hold check before the first round")


if __name__ == "__main__":
    unittest.main(verbosity=1)

#!/usr/bin/env python3
"""Plan E step 2c: automatic probe generation is off unless BROTHER_PROBES=on; off means not run, never clean.

Over every build on this machine that reached landing, a CLEAN probe verdict was followed by a landing refusal 136 times in
202 and a DIRTY one 35 times in 66, so the verdict did not predict the outcome. Off by default; the code stays for an
explicit "on". Run: python3 -B scripts/loop/test_probes_off.py
"""
import os
import re
import shutil
import subprocess
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import loop_switches as SW  # noqa: E402


class Switch(unittest.TestCase):
    def test_only_an_exact_on_turns_probes_on(self):
        for v, want in ((None, False), ("", False), ("yes", False), ("onn", False), ("off", False), ("on", True), (" ON ", True)):
            env = {} if v is None else {"BROTHER_PROBES": v}
            self.assertEqual(SW.probes_on(env), want, v)


class ProbeWaveHonoursTheSwitch(unittest.TestCase):
    def test_off_generates_and_writes_nothing(self):
        d = tempfile.mkdtemp(prefix="probes-off-", dir=os.path.expanduser("~/.claude/brother-scratch"))
        try:
            wave, pw = os.path.join(d, "wave"), os.path.join(d, "pw"); os.makedirs(wave)
            env = {k: v for k, v in os.environ.items() if k != "BROTHER_PROBES"}
            r = subprocess.run([sys.executable, "-B", os.path.join(HERE, "probe_wave.py"), wave, pw], capture_output=True, text=True, env=env, timeout=120)
            self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
            self.assertIn("PROBES OFF", r.stdout)
            self.assertFalse(os.path.exists(pw), "an off probe wave writes nothing")
        finally:
            shutil.rmtree(d, ignore_errors=True)


class RunnerSaysNotRun(unittest.TestCase):
    SRC = open(os.path.join(HERE, "unit_runner.py"), encoding="utf-8").read()

    def test_the_probe_launch_sits_under_the_switch(self):
        launch = self.SRC.index('os.path.join(BIN, "probe_wave.py")')
        self.assertIn("if not probes_off:", self.SRC[self.SRC.rfind("\n    if ", 0, launch):launch])

    def test_off_records_not_run_and_never_claims_clean(self):
        self.assertIn('verdict = "NOT RUN" if probes_off', self.SRC)
        self.assertIn('probe_word = "probes NOT RUN (off by plan E)" if probes_off else "probes clean"', self.SRC)
        self.assertEqual(len(re.findall(r'"[^"\n]*probes clean[^"\n]*"', self.SRC)), 1, "the only literal claiming clean is the probes-on word")


class LandingAdmitsNotRun(unittest.TestCase):
    def test_the_landing_gate_admits_a_graded_build_whose_probes_did_not_run(self):
        import land_batch as LB
        plan = {"units": [{"id": "D4", "sub_units": ["D4.c", "D4.d"], "evidence": "D4.d landed 2026-09-20"}]}
        p = "/x/D4.c-1/round2/out/D4.c-r1-build.json"
        ok = {"D4": {"state": "FIX-FIRST"}}; s9 = {"D4.c": {"score": 9}, "D4.d": {"score": 10}}
        self.assertEqual(LB.gate(p, "READY %s (round 2, grader PASS, probes NOT RUN (off by plan E))" % p, ok, s9, plan), "")
        self.assertNotEqual(LB.gate(p, "READY-UNPROBED %s" % p, ok, s9, plan), "", "READY-UNPROBED is still never landed")


if __name__ == "__main__":
    unittest.main()

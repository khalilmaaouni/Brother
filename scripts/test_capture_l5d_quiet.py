#!/usr/bin/env python3
"""scripts/capture_l5d_quiet.sh at its entry point, against a stub interpreter: every guard red under its own case.

python3 scripts/test_capture_l5d_quiet.py
"""
import os
import re
import subprocess
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
SCRIPT = os.path.join(HERE, "capture_l5d_quiet.sh")
STUB = """#!/bin/sh
printf '%s\\n' "$*" >> "$STUB_LOG"
case "$3" in
  quiet-check) exit "${STUB_QC_RC:-0}" ;;
  quiet) exit "${STUB_Q_RC:-0}" ;;
esac
exit 97
"""


class CaptureL5dQuietTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.out = os.path.join(self.tmp.name, "captures")
        os.makedirs(self.out)
        self.stub = os.path.join(self.tmp.name, "stub-python")
        with open(self.stub, "w") as fh:
            fh.write(STUB)
        os.chmod(self.stub, 0o755)
        self.log = os.path.join(self.tmp.name, "calls.log")

    def tearDown(self):
        self.tmp.cleanup()

    def run_script(self, qc_rc=0, q_rc=0):
        env = dict(os.environ, L5D_PYTHON=self.stub, STUB_LOG=self.log, STUB_QC_RC=str(qc_rc), STUB_Q_RC=str(q_rc))
        proc = subprocess.run(["sh", SCRIPT, self.out], env=env, capture_output=True, text=True, timeout=60)
        calls = open(self.log).read().splitlines() if os.path.exists(self.log) else []
        return proc, calls

    def with_loaded(self):
        with open(os.path.join(self.out, "loaded-l1-capture.json"), "w") as fh:
            fh.write("{}")

    def test_no_loaded_capture_is_no_data_before_any_interpreter_runs(self):
        proc, calls = self.run_script()
        self.assertEqual(proc.returncode, 2, proc.stdout + proc.stderr)
        self.assertIn("NO-DATA", proc.stdout)
        self.assertIn("perf_audit_run.py loaded", proc.stdout)
        self.assertEqual(calls, [])

    def test_not_quiet_refuses_with_2_and_never_runs_the_gate(self):
        self.with_loaded()
        proc, calls = self.run_script(qc_rc=2)
        self.assertEqual(proc.returncode, 2, proc.stdout + proc.stderr)
        self.assertIn("NOT DONE", proc.stdout)
        self.assertEqual(len(calls), 1)
        self.assertIn("quiet-check --gap 60", calls[0])
        self.assertFalse(any(" quiet --out-dir" in c for c in calls))

    def test_quiet_runs_the_capture_under_a_dated_label_and_names_the_l4_command(self):
        self.with_loaded()
        proc, calls = self.run_script()
        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
        self.assertEqual(len(calls), 2)
        m = re.search(r" quiet --out-dir (\S+) --label (quiet-\d{14}) --budget 119 --gap 60$", calls[1])
        self.assertIsNotNone(m, calls[1])
        self.assertEqual(m.group(1), self.out)
        self.assertIn("capture_l4_measurement.py \"%s/%s-gate.log\"" % (self.out, m.group(2)), proc.stdout)

    def test_the_capture_exit_code_is_the_scripts_exit_code(self):
        self.with_loaded()
        proc, calls = self.run_script(q_rc=1)
        self.assertEqual(proc.returncode, 1, proc.stdout + proc.stderr)
        self.assertEqual(len(calls), 2)

    def test_script_carries_no_dash_characters(self):
        text = open(SCRIPT, encoding="utf-8").read()
        self.assertNotRegex(text, "[%s%s]" % (chr(0x2013), chr(0x2014)))


if __name__ == "__main__":
    sys.exit(unittest.main())

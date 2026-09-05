"""Proof for scripts/next_cut.py (row S29, docs/plan/READINESS-ROADMAP-
2026-08-29.json): a policy naming a cut weekday prints the next cut date,
the version it would be and the closeout command; a policy naming none
reads NO-DATA and exits 3, never a pass.

Exit contract, same shape as this estate's other suites: 0 all assertions
pass, 1 an assertion failed.

Python 3, stdlib only. No network. No em or en dashes anywhere in this file.
"""
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
NEXT_CUT = os.path.join(HERE, "next_cut.py")

# E100: one sandbox for every temp tree this process makes, removed at exit.
import os as _e100_os, sys as _e100_sys  # noqa: E402
_e100_sys.path.append(_e100_os.path.join(
    _e100_os.path.dirname(_e100_os.path.abspath(__file__)), '.'))
try:  # noqa: E402
    import tmp_sandbox as _e100_tmp
    _e100_tmp.install()
except ImportError:
    _e100_sys.stderr.write(
        "tmp_sandbox absent: %s leaves its temp trees behind\n"
        % _e100_os.path.basename(__file__))

FRIDAY_POLICY = """# Release policy

## Cadence

Cuts run on a fixed weekday: Friday, closeout matrix mandatory.
"""

NO_WEEKDAY_POLICY = """# Release policy

Cuts land whenever a lane finishes; there is no fixed schedule yet.
"""


class NextCutTest(unittest.TestCase):
    def setUp(self):
        self.scratch = tempfile.mkdtemp(prefix="next-cut-test-")
        self.addCleanup(shutil.rmtree, self.scratch, ignore_errors=True)
        self.manifest = os.path.join(self.scratch, "plugin.json")
        with open(self.manifest, "w", encoding="utf-8") as fh:
            json.dump({"name": "brother", "version": "1.0.3"}, fh)

    def _write_policy(self, text):
        path = os.path.join(self.scratch, "RELEASE-POLICY.md")
        with open(path, "w", encoding="utf-8") as fh:
            fh.write(text)
        return path

    def _run(self, policy_path, today):
        return subprocess.run(
            [sys.executable, NEXT_CUT,
             "--policy", policy_path,
             "--manifest", self.manifest,
             "--today", today],
            capture_output=True, text=True)

    def test_a_named_friday_prints_the_next_friday_version_and_command(self):
        policy = self._write_policy(FRIDAY_POLICY)
        # 2026-09-07 is a Monday; the next Friday is 2026-09-11.
        proc = self._run(policy, "2026-09-07")
        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
        self.assertIn("next cut weekday: Friday", proc.stdout)
        self.assertIn("next cut date: 2026-09-11", proc.stdout)
        self.assertIn("next cut version: 1.0.4", proc.stdout)
        self.assertIn(
            "closeout command: python3 scripts/release_closeout.py all "
            "--version 1.0.4", proc.stdout)

    def test_today_being_the_named_weekday_returns_today(self):
        policy = self._write_policy(FRIDAY_POLICY)
        # 2026-09-11 is itself a Friday.
        proc = self._run(policy, "2026-09-11")
        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
        self.assertIn("next cut date: 2026-09-11", proc.stdout)

    def test_a_policy_naming_no_weekday_is_no_data_and_exits_3(self):
        policy = self._write_policy(NO_WEEKDAY_POLICY)
        proc = self._run(policy, "2026-09-07")
        self.assertEqual(proc.returncode, 3, proc.stdout + proc.stderr)
        self.assertIn(
            "NO-DATA: RELEASE-POLICY.md names no cut weekday (S29, founder)",
            proc.stdout)

    def test_a_missing_policy_file_is_no_data_and_exits_3(self):
        proc = self._run(os.path.join(self.scratch, "absent.md"), "2026-09-07")
        self.assertEqual(proc.returncode, 3, proc.stdout + proc.stderr)
        self.assertTrue(proc.stdout.startswith("NO-DATA:"), proc.stdout)


if __name__ == "__main__":
    unittest.main()

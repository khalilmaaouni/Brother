#!/usr/bin/env python3
"""scratch_prune: the pruner's own selftest cannot police its exit code.

WHY THIS FILE IS SEPARATE from the module's --selftest. On 2026-09-30 the
mutation sweep flipped the one line that turns a failure count into an exit
code, and every one of the module's own cases still reported OK, because a
selftest returns its verdict through the very function being mutated. That
circularity is not fixable from inside: the check has to run the module as a
CHILD PROCESS and read its real exit code from outside.

So these cases run the real script, and one of them runs a deliberately
broken COPY of it to prove the green can actually go red. A check that has
only ever been seen green is not known to be able to fail.
"""
import os
import shutil
import subprocess
import sys
import tempfile
import time
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
SCRIPT = os.path.join(HERE, "scratch_prune.py")
sys.path.insert(0, HERE)
import scratch_prune as sp  # noqa: E402


def run_script(path, *args):
    p = subprocess.run([sys.executable, "-B", path] + list(args),
                       stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    return p.returncode, p.stdout.decode(), p.stderr.decode()


class SelftestExitCodeIsReadFromOutside(unittest.TestCase):
    def test_the_real_selftest_exits_zero(self):
        code, out, _ = run_script(SCRIPT, "--selftest")
        self.assertEqual(code, 0)
        self.assertIn("OK over", out)

    def test_a_broken_guard_makes_the_selftest_exit_nonzero(self):
        """The red half. Without this, a selftest that can never fail would
        read exactly like one that passes."""
        tmp = tempfile.mkdtemp(prefix="scratch-prune-red-")
        self.addCleanup(shutil.rmtree, tmp, True)
        broken = os.path.join(tmp, "scratch_prune.py")
        with open(SCRIPT, encoding="utf-8") as fh:
            src = fh.read()
        mutated = src.replace("    if age < max_age:", "    if age > max_age:")
        self.assertNotEqual(mutated, src, "the age guard text moved")
        with open(broken, "w", encoding="utf-8") as fh:
            fh.write(mutated)
        code, _, err = run_script(broken, "--selftest")
        self.assertNotEqual(code, 0)
        self.assertIn("failure(s)", err)


class EntryPointRefusals(unittest.TestCase):
    """main() is where the control lives, so main() is what is tested. A
    module whose helpers are covered and whose entry point is not has shipped
    green on this estate before."""

    def test_unreadable_root_is_no_data_not_a_pass(self):
        code, _, err = run_script(SCRIPT, "--root",
                                  "/definitely/absent/scratch/root")
        self.assertEqual(code, 2)
        self.assertIn("NO-DATA", err)

    def test_max_age_not_a_number_refused(self):
        code, _, err = run_script(SCRIPT, "--max-age-hours", "six")
        self.assertEqual(code, 2)
        self.assertIn("NO-DATA", err)

    def test_max_age_zero_refused(self):
        code, _, err = run_script(SCRIPT, "--max-age-hours", "0")
        self.assertEqual(code, 2)
        self.assertIn("NO-DATA", err)

    def test_max_age_negative_refused(self):
        code, _, err = run_script(SCRIPT, "--max-age-hours", "-5")
        self.assertEqual(code, 2)
        self.assertIn("NO-DATA", err)

    def test_a_flag_with_no_value_refused(self):
        code, _, err = run_script(SCRIPT, "--root")
        self.assertEqual(code, 2)
        self.assertIn("usage", err)

    def test_dry_run_over_a_real_stale_entry_removes_nothing(self):
        root = tempfile.mkdtemp(prefix="scratch-prune-dry-")
        self.addCleanup(shutil.rmtree, root, True)
        lane = os.path.join(root, "attack9-A")
        os.makedirs(lane)
        stamp = time.time() - (sp.DEFAULT_MAX_AGE + 3600)
        os.utime(lane, (stamp, stamp))
        code, out, _ = run_script(SCRIPT, "--root", root, "--max-age-hours",
                                  "24", "--dry-run")
        self.assertEqual(code, 0)
        self.assertIn("dry run, nothing touched", out)
        self.assertTrue(os.path.isdir(lane))

    def test_a_real_stale_named_lane_is_removed_without_dry_run(self):
        """The defect in one sentence: the old one liner matched run-* only,
        so this lane would have survived forever."""
        root = tempfile.mkdtemp(prefix="scratch-prune-live-")
        self.addCleanup(shutil.rmtree, root, True)
        lane = os.path.join(root, "spec-mg1")
        os.makedirs(os.path.join(lane, "inner"))
        with open(os.path.join(lane, "inner", "f.txt"), "w") as fh:
            fh.write("stale")
        stamp = time.time() - (sp.DEFAULT_MAX_AGE + 3600)
        for p in (os.path.join(lane, "inner", "f.txt"), os.path.join(lane, "inner"), lane):   # the WHOLE lane is stale
            os.utime(p, (stamp, stamp))
        young = os.path.join(root, "spec-mg2")
        os.makedirs(young)
        code, out, _ = run_script(SCRIPT, "--root", root, "--max-age-hours",
                                  "24")
        self.assertEqual(code, 0)
        self.assertFalse(os.path.isdir(lane), out)
        self.assertTrue(os.path.isdir(young), out)


if __name__ == "__main__":
    unittest.main()

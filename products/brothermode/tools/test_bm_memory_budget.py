#!/usr/bin/env python3
"""Tests for bm_memory_budget, the auto-memory byte-budget scan.

Run: python3 tools/test_bm_memory_budget.py      (unittest output, exit 0 or 1)
"""
import os
import shutil
import subprocess
import sys
import tempfile
import unittest

# E100: one sandbox for every temp tree this process makes, removed at exit.
import os as _e100_os, sys as _e100_sys  # noqa: E402
_e100_sys.path.append(_e100_os.path.join(
    _e100_os.path.dirname(_e100_os.path.abspath(__file__)), '../../../scripts'))
try:  # noqa: E402
    import tmp_sandbox as _e100_tmp
    _e100_tmp.install()
except ImportError:
    # A packager (scripts/export_public.py, make_benchmark_bundle.py)
    # can copy this test without scripts/tmp_sandbox.py beside it. Say
    # so rather than dying: the sandbox is hygiene, not the subject.
    _e100_sys.stderr.write(
        "tmp_sandbox absent: %s leaves its temp trees behind\n"
        % _e100_os.path.basename(__file__))

TOOL = os.path.join(os.path.dirname(os.path.abspath(__file__)), "bm_memory_budget.py")


def run(argv):
    p = subprocess.run([sys.executable, TOOL] + argv,
                       stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    return p.returncode, (p.stdout + p.stderr).decode("utf-8", "replace")


def write_memory(root, project, size_bytes):
    d = os.path.join(root, project, "memory")
    os.makedirs(d, exist_ok=True)
    path = os.path.join(d, "MEMORY.md")
    with open(path, "w", encoding="utf-8") as f:
        f.write("x" * size_bytes)
    return path


def write_memory_lines(root, project, n_lines):
    d = os.path.join(root, project, "memory")
    os.makedirs(d, exist_ok=True)
    path = os.path.join(d, "MEMORY.md")
    with open(path, "w", encoding="utf-8") as f:
        f.write("x\n" * n_lines)
    return path


class MemoryBudget(unittest.TestCase):
    """Each case builds and destroys its own root, so these are independent and
    need no ordering: unittest may run them in any order it likes."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="bm-membudget-")

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_an_empty_root_is_no_data_never_a_silent_pass(self):
        # zero MEMORY.md files under root: NO-DATA, exit 3
        code, out = run(["--root", self.tmp])
        self.assertTrue(code == 3 and "NO-DATA" in out,
                        "empty root is NO-DATA: exit %d: %s" % (code, out[:90]))

    def test_every_file_under_budget_passes(self):
        write_memory(self.tmp, "proj-a", 100)
        write_memory(self.tmp, "proj-b", 200)
        code, out = run(["--root", self.tmp, "--budget", "1000"])
        self.assertEqual(code, 0, "all under budget passes: exit %d: %s" % (code, out[:120]))

    def test_one_file_over_budget_fails_and_names_the_offender(self):
        over_path = write_memory(self.tmp, "proj-c", 5000)
        write_memory(self.tmp, "proj-d", 100)
        code, out = run(["--root", self.tmp, "--budget", "1000"])
        self.assertEqual(code, 2, "one over budget fails: exit %d: %s" % (code, out[:120]))
        self.assertIn(over_path, out, "offender path is named: %s" % out[:200])

    def test_the_default_budget_matches_the_documented_24000(self):
        write_memory(self.tmp, "proj-e", 24500)
        code, out = run(["--root", self.tmp])
        self.assertEqual(code, 2, "default budget is 24000: exit %d: %s" % (code, out[:120]))

    def test_under_the_byte_budget_but_over_the_line_budget_still_fails(self):
        # The byte-only check would have missed this one entirely.
        lines_path = write_memory_lines(self.tmp, "proj-f", 250)  # 500 bytes, 250 lines
        code, out = run(["--root", self.tmp, "--budget", "24000", "--max-lines", "200"])
        self.assertEqual(code, 2,
                         "under bytes, over lines still fails: exit %d: %s" % (code, out[:200]))
        self.assertTrue(lines_path in out and "lines" in out,
                        "offender names the lines breach: %s" % out[:300])


if __name__ == "__main__":
    unittest.main(verbosity=1)

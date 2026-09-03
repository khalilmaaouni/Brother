#!/usr/bin/env python3
"""Regression tests for tools/bm_lint_walltime.py, the AST lint that refuses
the wall-clock stopwatch assertion pattern.
Standard library only. Run: python3 tools/test_bm_lint_walltime.py

WHAT THIS SUITE IS ACTUALLY DEFENDING
  The lint exists because seven people in ten days each fixed the same
  defect and review caught none of them before it shipped: a test asserting
  `assertLess(time.time() - started, 5.0)` measures the machine, not the
  code. A lint with false positives gets disabled by the first person it
  annoys, so this suite proves BOTH directions: every shape that must be
  caught, one test each, and every shape verified legitimate today that must
  NOT be caught, one test each, named so the shape is obvious from the test
  name alone. The last test runs the lint against this repository's own
  tools/ and scripts/ trees and demands the violation set equal exactly the
  one known instance, so calibration is proven on real code, not only on
  fixtures written to pass.

Every fixture is a tempfile.TemporaryDirectory(), never a real repo file.
"""
import io
import os
import subprocess
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
TOOL_PATH = os.path.join(HERE, "bm_lint_walltime.py")

#: The one violation this suite knows exists in the live tree today.
#: test_the_real_tree_matches_the_known_calibration below fails loudly if
#: the tree changes underneath this number without this test being updated.
KNOWN_REAL_VIOLATION = (
    "tools/test_brothermode_cli.py", 1510,
    "        self.assertLess(time.time() - started, 5.0,")


def run_cli(*args, cwd=None):
    return subprocess.run([sys.executable, TOOL_PATH] + list(args),
                          stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                          universal_newlines=True, cwd=cwd or ROOT)


def write_fixture(directory, relpath, content):
    path = os.path.join(directory, relpath)
    parent = os.path.dirname(path)
    if parent and not os.path.isdir(parent):
        os.makedirs(parent)
    with io.open(path, "w", encoding="utf-8") as handle:
        handle.write(content)
    return path


class MustCatchTests(unittest.TestCase):
    """Every shape the lint exists to refuse."""

    def test_the_one_line_stopwatch_assertion_is_caught(self):
        with tempfile.TemporaryDirectory() as tmp:
            write_fixture(tmp, "case.py", (
                "import time\n"
                "import unittest\n"
                "class T(unittest.TestCase):\n"
                "    def test_x(self):\n"
                "        started = time.time()\n"
                "        self.assertLess(time.time() - started, 5.0,\n"
                "                        'must be bounded')\n"))
            result = run_cli(os.path.join(tmp, "case.py"))
            self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
            self.assertIn("case.py:6:", result.stdout)

    def test_the_two_line_elapsed_equals_idiom_is_caught(self):
        with tempfile.TemporaryDirectory() as tmp:
            write_fixture(tmp, "case.py", (
                "import time\n"
                "import unittest\n"
                "class T(unittest.TestCase):\n"
                "    def test_x(self):\n"
                "        start = time.time()\n"
                "        elapsed = time.time() - start\n"
                "        self.assertLess(elapsed, 5.0)\n"))
            result = run_cli(os.path.join(tmp, "case.py"))
            self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
            self.assertIn("case.py:7:", result.stdout)

    def test_the_reversed_operand_order_is_caught(self):
        with tempfile.TemporaryDirectory() as tmp:
            write_fixture(tmp, "case.py", (
                "import time\n"
                "import unittest\n"
                "class T(unittest.TestCase):\n"
                "    def test_x(self):\n"
                "        started = time.time()\n"
                "        self.assertLess(started - time.time(), 5.0)\n"))
            result = run_cli(os.path.join(tmp, "case.py"))
            self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
            self.assertIn("case.py:6:", result.stdout)


class MustNotCatchTests(unittest.TestCase):
    """Every shape verified legitimate today. One test per shape, so a
    future false positive names the exact pattern it broke."""

    def _assert_clean(self, source):
        with tempfile.TemporaryDirectory() as tmp:
            path = write_fixture(tmp, "case.py", source)
            result = run_cli(path)
            self.assertEqual(result.returncode, 0,
                             "expected no violations, got:\n%s\nstderr:\n%s"
                             % (result.stdout, result.stderr))

    def test_a_ratio_of_two_timings_against_a_constant_is_not_caught(self):
        # The approved fix shape: the constant sits against a BinOp(Div),
        # never against a timing difference.
        self._assert_clean(
            "import time\n"
            "import unittest\n"
            "class T(unittest.TestCase):\n"
            "    def _time(self, n):\n"
            "        return 0.001 * n\n"
            "    def test_x(self):\n"
            "        small = max(self._time(8000), 0.001)\n"
            "        large = self._time(32000)\n"
            "        self.assertLess(large / small, 8.0,\n"
            "                        'scaling looks superlinear')\n")

    def test_assert_equal_on_a_timing_difference_is_not_caught(self):
        # assertEqual is never inspected, by name: a deterministic count
        # comparison is not this defect no matter what its operands are.
        self._assert_clean(
            "import time\n"
            "import unittest\n"
            "class T(unittest.TestCase):\n"
            "    def test_x(self):\n"
            "        started = time.time()\n"
            "        self.assertEqual(time.time() - started, 5.0)\n")

    def test_a_deadline_value_never_asserted_against_is_not_caught(self):
        # tools/test_all.py's own shape: a timing difference compared inside
        # an `if`, never inside an assert at all.
        self._assert_clean(
            "import time\n"
            "def wait_for(silence_limit, last):\n"
            "    if time.time() - last[0] > silence_limit:\n"
            "        return True\n"
            "    return False\n")

    def test_a_timing_difference_used_to_fabricate_an_mtime_is_not_caught(self):
        # tools/test_bm_session_cap.py's own shape: the timing difference is
        # assigned and used to build a fixture, never compared to a constant
        # in an assert.
        self._assert_clean(
            "import os\n"
            "import time\n"
            "import unittest\n"
            "class T(unittest.TestCase):\n"
            "    def test_x(self):\n"
            "        age_seconds = 3600\n"
            "        stamp = time.time() - age_seconds\n"
            "        os.utime('some/path', (stamp, stamp))\n")

    def test_assertless_on_a_length_count_is_not_caught(self):
        self._assert_clean(
            "import unittest\n"
            "class T(unittest.TestCase):\n"
            "    def test_x(self):\n"
            "        trace = list(range(3))\n"
            "        self.assertLess(len(trace), 20)\n")

    def test_assertless_on_a_text_ordering_check_is_not_caught(self):
        self._assert_clean(
            "import unittest\n"
            "class T(unittest.TestCase):\n"
            "    def test_x(self):\n"
            "        s = 'a comes before b'\n"
            "        self.assertLess(s.index('a'), s.index('b'))\n")

    def test_a_sleep_call_never_asserted_is_not_caught(self):
        self._assert_clean(
            "import time\n"
            "import unittest\n"
            "class T(unittest.TestCase):\n"
            "    def test_x(self):\n"
            "        time.sleep(1.1)\n"
            "        self.assertTrue(True)\n")


class AllowlistAndInlineExemptionTests(unittest.TestCase):

    def test_a_file_matching_the_allowlist_is_skipped_even_with_the_bad_shape(self):
        with tempfile.TemporaryDirectory() as tmp:
            write_fixture(tmp, "tools/bm_hookbench.py", (
                "import time\n"
                "import unittest\n"
                "class T(unittest.TestCase):\n"
                "    def test_x(self):\n"
                "        started = time.time()\n"
                "        self.assertLess(time.time() - started, 5.0)\n"))
            # A file with the identical shape but NOT on the allowlist, in
            # the same tree, so this test also proves the allowlist match is
            # by path, not "the lint gave up on the whole run".
            write_fixture(tmp, "tools/other_file.py", (
                "import time\n"
                "import unittest\n"
                "class T(unittest.TestCase):\n"
                "    def test_x(self):\n"
                "        started = time.time()\n"
                "        self.assertLess(time.time() - started, 5.0)\n"))
            result = run_cli("tools/", cwd=tmp)
            self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
            self.assertNotIn("bm_hookbench.py", result.stdout)
            self.assertIn("other_file.py:6:", result.stdout)

    def test_the_benchmark_directory_prefix_is_skipped(self):
        with tempfile.TemporaryDirectory() as tmp:
            write_fixture(tmp, "benchmark/some_suite.py", (
                "import time\n"
                "import unittest\n"
                "class T(unittest.TestCase):\n"
                "    def test_x(self):\n"
                "        started = time.time()\n"
                "        self.assertLess(time.time() - started, 5.0)\n"))
            result = run_cli("benchmark/", cwd=tmp)
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_an_inline_exemption_with_a_reason_is_honoured(self):
        with tempfile.TemporaryDirectory() as tmp:
            write_fixture(tmp, "case.py", (
                "import time\n"
                "import unittest\n"
                "class T(unittest.TestCase):\n"
                "    def test_x(self):\n"
                "        started = time.time()\n"
                "        self.assertLess(  # walltime-lint: allow this really is a benchmark\n"
                "            time.time() - started, 5.0)\n"))
            result = run_cli(os.path.join(tmp, "case.py"))
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_an_inline_exemption_with_an_empty_reason_is_refused(self):
        with tempfile.TemporaryDirectory() as tmp:
            write_fixture(tmp, "case.py", (
                "import time\n"
                "import unittest\n"
                "class T(unittest.TestCase):\n"
                "    def test_x(self):\n"
                "        started = time.time()\n"
                "        self.assertLess(  # walltime-lint: allow\n"
                "            time.time() - started, 5.0)\n"))
            result = run_cli(os.path.join(tmp, "case.py"))
            self.assertEqual(result.returncode, 1,
                             "an empty reason must not exempt the line:\n%s"
                             % result.stdout)


class ZeroFilesTests(unittest.TestCase):

    def test_opening_zero_files_is_no_data_not_a_clean_pass(self):
        with tempfile.TemporaryDirectory() as tmp:
            os.makedirs(os.path.join(tmp, "empty"))
            result = run_cli(os.path.join(tmp, "empty"))
            self.assertEqual(result.returncode, 2, result.stdout + result.stderr)
            self.assertIn("NO-DATA: opened no files", result.stdout)


class UncheckableFilesFailClosedTests(unittest.TestCase):
    """2026-08-11 cross-family audit: read and parse failures were printed
    to stderr and skipped, so a tree of unreadable files exited 0 while
    claiming to have been checked. A file the lint cannot check is a
    finding, never a skip."""

    def test_an_unparsable_file_is_a_violation_not_a_skip(self):
        with tempfile.TemporaryDirectory() as tmp:
            bad = os.path.join(tmp, "test_broken.py")
            with open(bad, "w") as handle:
                handle.write("def broken(:\n")
            result = run_cli(tmp)
            self.assertNotEqual(result.returncode, 0,
                                result.stdout + result.stderr)
            self.assertIn("UNCHECKABLE", result.stdout)

    def test_an_unreadable_file_is_a_violation_not_a_skip(self):
        with tempfile.TemporaryDirectory() as tmp:
            bad = os.path.join(tmp, "test_secret.py")
            with open(bad, "w") as handle:
                handle.write("x = 1\n")
            os.chmod(bad, 0)
            try:
                result = run_cli(tmp)
            finally:
                os.chmod(bad, 0o644)
            self.assertNotEqual(result.returncode, 0,
                                result.stdout + result.stderr)
            self.assertIn("UNCHECKABLE", result.stdout)


class RealTreeCalibrationTests(unittest.TestCase):
    """The test that proves calibration on real code, not only on fixtures
    written to pass.

    CHANGED 2026-08-10, and the reason matters more than the change. This
    asserted the tree held EXACTLY ONE violation, naming it. That was correct
    for about an hour: the violation it pinned,
    test_a_missing_log_times_out_rather_than_raising in
    tools/test_brothermode_cli.py, was then fixed, and this test went red for
    the best possible reason, that the defect it described no longer exists.

    A test pinned to a known-bad line has to be rewritten every time somebody
    does the right thing, which trains people to edit the test rather than
    read it. So the assertion is now the invariant the project actually wants
    to hold forever: THE TREE IS CLEAN. It goes red when a violation is
    INTRODUCED, which is the direction that matters, and it never goes red
    because a violation was removed.

    The "must catch" half is not lost by this change. It is proven by the
    fixture tests above, which construct each violating shape deliberately
    and assert the lint flags it. Those cannot rot, because their inputs are
    written by the test rather than found in the tree."""

    def test_the_real_tree_is_clean(self):
        result = run_cli("tools/", "scripts/")
        self.assertEqual(
            result.returncode, 0,
            "the tree carries an absolute wall-clock assertion outside the "
            "benchmark allow list. That measures the machine, not the code, "
            "and it is the defect found and fixed seven separate times in "
            "ten days here. Assert a RATIO between two input sizes using the "
            "MINIMUM of N samples, or a deterministic operation count:\n%s"
            % (result.stdout + result.stderr))
        self.assertEqual([line for line in result.stdout.splitlines()
                          if line.strip()], [])

    def test_the_clean_verdict_is_not_a_scan_that_opened_nothing(self):
        """A clean result means nothing unless files were actually read. The
        NO-DATA guard exits 2 rather than 0 on an empty scan, so this asserts
        the real-tree run is distinguishable from a run that walked no
        Python at all, which is the failure mode that would make the test
        above pass forever while the lint quietly stopped working."""
        with tempfile.TemporaryDirectory() as tmp:
            with io.open(os.path.join(tmp, "notes.md"), "w",
                         encoding="utf-8") as handle:
                handle.write("no python here\n")
            empty = run_cli(tmp)
        self.assertEqual(empty.returncode, 2, empty.stdout + empty.stderr)
        self.assertIn("NO-DATA", empty.stdout)
        real = run_cli("tools/", "scripts/")
        self.assertEqual(real.returncode, 0, real.stdout + real.stderr)
        self.assertNotIn("NO-DATA", real.stdout,
                         "the real-tree run opened no files, so its clean "
                         "verdict says nothing about the tree")


if __name__ == "__main__":
    unittest.main()

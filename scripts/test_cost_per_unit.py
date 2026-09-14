"""Tests for cost_per_unit.py: the arithmetic, and the refuse-to-guess paths.

Mirrors scripts/test_tiny_task_cost.py's style: unittest.TestCase classes,
each proving one property against a fixture. token_totals() and
commit_count() are the two calls that actually touch token-shield and git;
this file patches them to fixed fixture numbers rather than depending on a
live token-shield install or a live repo, so the arithmetic and the NO-DATA
refusals are proven on their own, deterministically.
"""
import contextlib
import io
import os
import sys
import unittest
from unittest import mock

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import cost_per_unit as cpu  # noqa: E402


class TokensPerCommitArithmetic(unittest.TestCase):
    """The pure division, on fixed fixture numbers."""

    def test_a_normal_case_divides_measured_tokens_by_commits(self):
        self.assertEqual(cpu.tokens_per_commit(1000, 4), 250.0)

    def test_fractional_result_is_not_rounded_away(self):
        self.assertAlmostEqual(cpu.tokens_per_commit(1000, 3), 333.3333, places=3)

    def test_zero_commits_is_none_not_a_zero_division_crash(self):
        self.assertIsNone(cpu.tokens_per_commit(1000, 0))

    def test_zero_tokens_is_none(self):
        self.assertIsNone(cpu.tokens_per_commit(0, 5))

    def test_missing_tokens_is_none(self):
        self.assertIsNone(cpu.tokens_per_commit(None, 5))

    def test_missing_commits_is_none(self):
        self.assertIsNone(cpu.tokens_per_commit(1000, None))


class MainRefusesToGuess(unittest.TestCase):
    """main() prints NO-DATA and exits 2 on every path that would otherwise
    divide by zero or invent a number, driven with token_totals()/
    commit_count() patched to fixed fixture values -- never against a live
    token-shield install or a live git repo."""

    def _run_main(self, days=30):
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            code = cpu.main(["--days", str(days)])
        return code, buf.getvalue()

    def test_no_token_data_is_no_data_exit_2(self):
        with mock.patch.object(cpu, "token_totals",
                               return_value=(None, None, "no transcripts")):
            code, out = self._run_main()
        self.assertEqual(code, 2)
        self.assertIn("NO-DATA", out)
        self.assertIn("no transcripts", out)

    def test_zero_commits_is_no_data_exit_2(self):
        with mock.patch.object(cpu, "token_totals",
                               return_value=(1000, 900, None)), \
             mock.patch.object(cpu, "commit_count", return_value=0):
            code, out = self._run_main()
        self.assertEqual(code, 2)
        self.assertIn("NO-DATA", out)

    def test_commit_count_failure_is_no_data_exit_2(self):
        with mock.patch.object(cpu, "token_totals",
                               return_value=(1000, 900, None)), \
             mock.patch.object(cpu, "commit_count", return_value=None):
            code, out = self._run_main()
        self.assertEqual(code, 2)
        self.assertIn("NO-DATA", out)

    def test_zero_measured_tokens_with_real_commits_is_no_data_exit_2(self):
        with mock.patch.object(cpu, "token_totals",
                               return_value=(0, 0, None)), \
             mock.patch.object(cpu, "commit_count", return_value=4):
            code, out = self._run_main()
        self.assertEqual(code, 2)
        self.assertIn("NO-DATA", out)

    def test_real_numbers_print_one_line_and_exit_0(self):
        with mock.patch.object(cpu, "token_totals",
                               return_value=(1000, 900, None)), \
             mock.patch.object(cpu, "commit_count", return_value=4):
            code, out = self._run_main()
        self.assertEqual(code, 0)
        self.assertIn("tokens_measured=1000", out)
        self.assertIn("tokens_normalized_estimated=900", out)
        self.assertIn("commits=4", out)
        self.assertIn("tokens_per_commit=250.0", out)
        self.assertEqual(len(out.strip().splitlines()), 1)

    def test_missing_normalized_estimate_prints_no_data_for_that_field_only(self):
        """token-shield can measure real usage while still lacking the TTL
        split normalized_input_total needs; that half reads NO-DATA without
        blocking the tokens-per-commit answer, which never depends on it."""
        with mock.patch.object(cpu, "token_totals",
                               return_value=(1000, None, None)), \
             mock.patch.object(cpu, "commit_count", return_value=4):
            code, out = self._run_main()
        self.assertEqual(code, 0)
        self.assertIn("tokens_normalized_estimated=NO-DATA", out)
        self.assertIn("tokens_per_commit=250.0", out)


if __name__ == "__main__":
    unittest.main(verbosity=2)

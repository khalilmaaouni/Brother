"""Tests for the L5b.5 rubric scorer in tools/l5b_audit/score.py.

Every test asserts the sub unit specification: the 4.0/3.0/3.0 formula, the
two forced-zero reasons, fail-closed probe evidence, and refusal of hostile
input. Nothing here reads a live repository document, so the suite also runs
in an export copy with an empty HOME.
"""

import contextlib
import io
import os
import runpy
import unittest

from tools.l5b_audit.score import (
    FailClosedProbe,
    ScoreResult,
    compute,
    fail_closed_fraction,
    score_docstring,
)


HERE = os.path.dirname(os.path.abspath(__file__))
PROBES = os.path.join(HERE, "probes")


def probe(name, exit_code=0, output="BLOCK"):
    """Build one probe record the way the rubric expects to receive it."""
    return FailClosedProbe(name=name, exit_code=exit_code, output=output)


class FailClosedProbeTest(unittest.TestCase):
    def test_block_line_is_a_pass(self):
        self.assertTrue(probe("empty", 0, "BLOCK").passed)
        self.assertTrue(probe("empty", 0, "BLOCK empty input refused\n").passed)

    def test_any_other_observation_is_not_a_pass(self):
        self.assertFalse(probe("silent", 0, "").passed)
        self.assertFalse(probe("wrong", 0, "PASS").passed)
        self.assertFalse(probe("sneaky", 0, "BLOCKING").passed)
        self.assertFalse(probe("crashed", 3, "BLOCK").passed)

    def test_hostile_probe_fields_refused(self):
        bad_cases = (
            (None, 0, "BLOCK"),
            ("", 0, "BLOCK"),
            ("p", True, "BLOCK"),
            ("p", 0.0, "BLOCK"),
            ("p", 0, None),
            ("p", 0, "BLOCK".encode("utf-8")),
        )
        for case in bad_cases:
            with self.assertRaises(ValueError):
                FailClosedProbe(name=case[0], exit_code=case[1], output=case[2])


class FailClosedFractionTest(unittest.TestCase):
    def test_no_probes_is_no_evidence(self):
        self.assertEqual(fail_closed_fraction(()), 0.0)

    def test_fraction_counts_passes_over_all_probes(self):
        self.assertEqual(fail_closed_fraction((probe("a"),)), 1.0)
        three = (probe("a"), probe("b", 0, "PASS"), probe("c"))
        self.assertEqual(fail_closed_fraction(three), 2.0 / 3.0)

    def test_non_tuple_container_refused(self):
        for bad in (None, [], "BLOCK", 4, True, float("nan")):
            with self.assertRaises(ValueError):
                fail_closed_fraction(bad)

    def test_foreign_probe_entry_refused(self):
        with self.assertRaises(ValueError):
            fail_closed_fraction((object(),))
        with self.assertRaises(ValueError):
            fail_closed_fraction(("BLOCK",))


class ComputeTest(unittest.TestCase):
    def test_full_credit_is_ten(self):
        result = compute(10, 10, 10, (probe("a"),), 0)
        self.assertIsInstance(result, ScoreResult)
        self.assertEqual(result.score, 10.0)
        self.assertEqual(result.reason, "SCORED")
        self.assertTrue(result.meets_bar)

    def test_axes_weigh_four_three_three(self):
        result = compute(10, 5, 0, (probe("a"),), 0)
        self.assertEqual(result.score, 4.0 * 0.5 + 3.0 * 1.0 + 3.0 * 0.0)

    def test_fractional_axes_are_valid(self):
        probes = (probe("a"), probe("b", 0, "PASS"))
        result = compute(3, 2, 1, probes, 0)
        self.assertEqual(result.coverage, 2.0 / 3.0)
        self.assertEqual(result.fired, 0.5)
        self.assertEqual(result.fail_closed, 0.5)
        self.assertEqual(result.score, 4.0 * (2.0 / 3.0) + 3.0 * 0.5 + 3.0 * 0.5)

    def test_no_probe_evidence_earns_no_fail_closed_credit(self):
        result = compute(10, 10, 10, (), 0)
        self.assertEqual(result.score, 7.0)
        self.assertFalse(result.meets_bar)

    def test_total_zero_is_no_data(self):
        result = compute(0, 0, 0, (probe("a"),), 0)
        self.assertEqual(result.score, 0.0)
        self.assertEqual(result.reason, "NO_DATA")
        self.assertFalse(result.meets_bar)

    def test_unexempted_hit_forces_zero(self):
        result = compute(10, 10, 10, (probe("a"),), 1)
        self.assertEqual(result.score, 0.0)
        self.assertEqual(result.reason, "UNRESOLVED_HITS")
        self.assertFalse(result.meets_bar)

    def test_zero_stated_does_not_divide_by_zero(self):
        result = compute(4, 0, 0, (probe("a"),), 0)
        self.assertEqual(result.fired, 0.0)
        self.assertEqual(result.score, 3.0)

    def test_counts_that_cannot_all_be_true_are_corrupt(self):
        cases = (
            (-1, 0, 0, (), 0),
            (4, 5, 0, (), 0),
            (4, 3, 4, (), 0),
            (4, 4, 4, (), -1),
            (4, 4, 4, (), "1"),
        )
        for case in cases:
            with self.assertRaises(ValueError):
                compute(case[0], case[1], case[2], case[3], case[4])

    def test_hostile_count_types_refused(self):
        cases = (
            (None, 0, 0, (), 0),
            (True, 1, 1, (), 0),
            (4.0, 1, 1, (), 0),
            (float("nan"), 0, 0, (), 0),
            ("4", 0, 0, (), 0),
            (4, True, 1, (), 0),
            (4, 4, False, (probe("a"),), 0),
        )
        for case in cases:
            with self.assertRaises(ValueError):
                compute(case[0], case[1], case[2], case[3], case[4])

    def test_hostile_probe_container_refused(self):
        for bad in (None, [], "BLOCK", 4, True):
            with self.assertRaises(ValueError):
                compute(4, 4, 4, bad, 0)

    def test_foreign_probe_entry_refused(self):
        with self.assertRaises(ValueError):
            compute(4, 4, 4, (object(),), 0)


class ScoreDocstringTest(unittest.TestCase):
    def test_docstring_names_the_axes(self):
        text = score_docstring()
        self.assertIsInstance(text, str)
        self.assertTrue(text.strip())
        self.assertIn("fail_closed_fraction", text)
        self.assertIn("4.0", text)
        self.assertIn("3.0", text)


class ProbeScriptTest(unittest.TestCase):
    """The spec probe commands, run in process so the done_check stays one
    command. Each probe must exit 0 and print a line beginning BLOCK, which is
    exactly what the spec greps for (^BLOCK and $? -eq 0).
    """

    def run_probe(self, filename):
        path = os.path.join(PROBES, filename)
        if not os.path.isfile(path):
            self.fail("probe script is missing: %s" % path)
        buffer = io.StringIO()
        code = 0
        try:
            with contextlib.redirect_stdout(buffer):
                runpy.run_path(path, run_name="__main__")
        except SystemExit as exc:
            code = 0 if exc.code is None else exc.code
        return code, buffer.getvalue()

    def assert_blocks(self, filename):
        code, out = self.run_probe(filename)
        self.assertEqual(code, 0, "probe %s exited %r" % (filename, code))
        lines = [line for line in out.splitlines() if line.strip()]
        self.assertTrue(lines, "probe %s printed nothing" % filename)
        self.assertTrue(lines[0].startswith("BLOCK"), "probe %s printed %r" % (filename, lines[0]))

    def test_empty_probe_blocks(self):
        self.assert_blocks("empty.py")

    def test_corrupt_probe_blocks(self):
        self.assert_blocks("corrupt.py")

    def test_unknown_probe_blocks(self):
        self.assert_blocks("unknown.py")


if __name__ == "__main__":
    unittest.main()

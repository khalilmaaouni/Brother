"""What the verifier must keep true.

The tests that matter here are the ones about NO-DATA, because NO-DATA is the
verdict that gets quietly folded into a neighbour when nobody is watching.
Folded into PASS, a unit with no done_check closes green and the board counts
work nobody checked. Folded into FAIL, a missing interpreter reads as broken
code and the repair loop starts rewriting something that was never wrong.

Both foldings pass a suite that only checks the happy paths, which is why the
assertions below are written against the folding rather than against the
feature.
"""
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import bm_verify as V  # noqa: E402


def runner(exit_code=0, stdout="", stderr=""):
    class _R(object):
        def run(self, command, cwd):
            return {"exit_code": exit_code, "stdout": stdout, "stderr": stderr}
    return _R()


def raising_runner(exc):
    class _R(object):
        def run(self, command, cwd):
            raise exc
    return _R()


UNIT = {"done_check": "some command"}


class TheTwoOrdinaryVerdicts(unittest.TestCase):
    def test_exit_zero_is_PASS(self):
        self.assertEqual(V.verify(UNIT, runner=runner(0))["verdict"], V.PASS)

    def test_any_non_zero_is_FAIL_and_the_code_is_kept(self):
        got = V.verify(UNIT, runner=runner(3))
        self.assertEqual(got["verdict"], V.FAIL)
        self.assertEqual(got["exit_code"], 3)

    def test_a_silent_check_that_exits_zero_is_still_a_PASS(self):
        """Plenty of good checks print nothing. Treating silence as absence
        would make NO-DATA the usual answer and destroy its meaning."""
        got = V.verify(UNIT, runner=runner(0, stdout="", stderr=""))
        self.assertEqual(got["verdict"], V.PASS)


class TheVerdictComesFromTheExitCode(unittest.TestCase):
    def test_a_check_that_PRINTS_pass_but_exits_one_has_FAILED(self):
        """An exit code is the one part of a check's output a confused script
        cannot fake by accident."""
        got = V.verify(UNIT, runner=runner(1, stdout="PASS: everything fine"))
        self.assertEqual(got["verdict"], V.FAIL)

    def test_a_check_that_PRINTS_failure_but_exits_zero_has_PASSED(self):
        got = V.verify(UNIT, runner=runner(0, stdout="FAILED: 3 errors"))
        self.assertEqual(got["verdict"], V.PASS)


class NoDataIsNeverAPass(unittest.TestCase):
    """Folded into PASS, a unit nobody checked closes green."""

    def test_a_unit_with_no_done_check_is_NO_DATA(self):
        self.assertEqual(V.verify({})["verdict"], V.NO_DATA)

    def test_a_blank_done_check_is_NO_DATA(self):
        self.assertEqual(V.verify({"done_check": "   "})["verdict"], V.NO_DATA)

    def test_is_pass_is_False_for_NO_DATA(self):
        self.assertFalse(V.is_pass(V.verify({})))

    def test_NO_DATA_blocks_a_close_exactly_as_FAIL_does(self):
        self.assertTrue(V.blocks_close(V.verify({})))
        self.assertTrue(V.blocks_close(V.verify(UNIT, runner=runner(1))))

    def test_a_truthiness_test_on_the_verdict_string_would_be_wrong(self):
        """The reason is_pass exists at all: every verdict string is truthy, so
        any caller reaching for `if result['verdict']:` treats NO-DATA as good
        news. This asserts the trap is real so nobody re-opens it."""
        self.assertTrue(bool(V.NO_DATA))
        self.assertFalse(V.is_pass({"verdict": V.NO_DATA}))


class NoDataIsNeverAFailureEither(unittest.TestCase):
    """Folded into FAIL, a missing tool starts a repair loop against code that
    was never wrong."""

    def test_command_not_found_is_NO_DATA_and_not_FAIL(self):
        got = V.verify(UNIT, runner=runner(V.EXIT_COMMAND_NOT_FOUND))
        self.assertEqual(got["verdict"], V.NO_DATA)
        self.assertIn("127", got["reason"])

    def test_command_not_executable_is_NO_DATA_and_not_FAIL(self):
        got = V.verify(UNIT, runner=runner(V.EXIT_COMMAND_NOT_EXECUTABLE))
        self.assertEqual(got["verdict"], V.NO_DATA)

    def test_a_runner_that_raises_is_NO_DATA_and_the_error_is_kept(self):
        got = V.verify(UNIT, runner=raising_runner(OSError("no shell")))
        self.assertEqual(got["verdict"], V.NO_DATA)
        self.assertIn("no shell", got["reason"])

    def test_the_three_verdicts_are_three_distinct_strings(self):
        self.assertEqual(len({V.PASS, V.FAIL, V.NO_DATA}), 3)


class EveryVerdictHasTheSameShape(unittest.TestCase):
    def test_no_caller_needs_to_know_which_branch_answered(self):
        for got in (V.verify(UNIT, runner=runner(0)),
                    V.verify(UNIT, runner=runner(2)),
                    V.verify({}),
                    V.verify(UNIT, runner=raising_runner(OSError("x")))):
            for key in ("verdict", "reason", "command", "exit_code",
                        "stdout", "stderr"):
                self.assertIn(key, got)

    def test_the_reason_is_never_empty(self):
        """A verdict a person cannot act on is half a verdict."""
        for got in (V.verify(UNIT, runner=runner(0)),
                    V.verify(UNIT, runner=runner(2)),
                    V.verify({})):
            self.assertTrue(got["reason"].strip())


class AgainstTheRealShell(unittest.TestCase):
    """The mapped exit codes are claims about this platform, so at least once
    they are checked against it rather than against a fake."""

    def test_a_real_missing_command_really_does_give_NO_DATA(self):
        got = V.verify({"done_check": "definitely-not-a-real-command-xyz"})
        self.assertEqual(got["verdict"], V.NO_DATA)
        self.assertEqual(got["exit_code"], V.EXIT_COMMAND_NOT_FOUND)

    def test_a_real_failing_command_really_does_give_FAIL(self):
        self.assertEqual(V.verify({"done_check": "exit 3"})["verdict"], V.FAIL)

    def test_a_real_passing_command_really_does_give_PASS(self):
        self.assertEqual(V.verify({"done_check": "exit 0"})["verdict"], V.PASS)


class TheSelftestIsRunnableByHand(unittest.TestCase):
    def test_selftest_exits_zero(self):
        self.assertEqual(V.main(["--selftest"]), 0)

    def test_a_bare_invocation_refuses_rather_than_pretending(self):
        self.assertEqual(V.main([]), 2)


if __name__ == "__main__":
    unittest.main()

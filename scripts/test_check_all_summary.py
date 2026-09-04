"""Drive scripts/check_all.sh's run_check summary line backwards.

WHY THIS EXISTS. run_check already learned once that a summary line must
name the check's own verdict rather than whatever printed last: the FAIL
branch says so in its own comment, because a failing check once displayed
an unrelated "OK" beside a FAIL verdict. The exit-0 branch never got the
same treatment, and it cost the estate three separate readings. On the
1.0.2 cut battery the passing product-brothersbe-tests suite displayed

  migrate: outcomes.jsonl SHRANK while this rewrite held the writer lock

which is a fixture-driven refusal one of its own tests deliberately
provokes, printed to stdout after unittest wrote "OK (skipped=2)" to
stderr. Three reviewers in three verdict rounds read that line as a live
concurrency incident on the operator's machine and recorded it as foreign
contamination. The suite was green the whole time.

This test extracts run_check from the shell script and runs it against
fake commands, so the rule is proven by driving it, not by reading it.
"""

import os
import re
import subprocess
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
CHECK_ALL = os.path.join(HERE, "check_all.sh")


def _run_check_body():
    """The text of run_check(), lifted out of check_all.sh.

    Sourcing check_all.sh is not an option: sourcing it runs the whole
    battery. The function is read between its own opening line and the
    first closing brace at column zero, and a missing marker raises rather
    than silently returning an empty body that would make every assertion
    below pass against nothing.
    """
    with open(CHECK_ALL, encoding="utf-8") as f:
        lines = f.read().splitlines()
    try:
        start = lines.index("run_check() {")
    except ValueError:
        raise AssertionError("check_all.sh no longer defines run_check() at column zero")
    for i in range(start + 1, len(lines)):
        if lines[i] == "}":
            return "\n".join(lines[start:i + 1])
    raise AssertionError("run_check() in check_all.sh has no closing brace at column zero")


class RunCheckSummary(unittest.TestCase):

    def _drive(self, script_body, expect_code=0):
        """Run run_check against a fake check and return its printed line."""
        with tempfile.TemporaryDirectory() as d:
            fake = os.path.join(d, "fake_check.py")
            with open(fake, "w", encoding="utf-8") as f:
                f.write(script_body)
            driver = os.path.join(d, "driver.sh")
            with open(driver, "w", encoding="utf-8") as f:
                f.write("#!/bin/sh\n"
                        "pass=0; fail=0; nodata=0\n"
                        "failed_names=\"\"\n"
                        "nodata_names=\"\"\n"
                        "%s\n"
                        "run_check \"fake-check\" \"$1\" \"$2\"\n"
                        % _run_check_body())
            done = subprocess.run(["sh", driver, sys.executable, fake],
                                  stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                  cwd=d)
            self.assertEqual(expect_code, done.returncode,
                             "the driver itself failed: %r"
                             % done.stderr.decode(errors="replace"))
            return done.stdout.decode(errors="replace")

    def test_a_green_suite_is_summarised_by_its_own_verdict_not_a_trailing_print(self):
        """The exact shape of the 1.0.2 cut line: unittest reports OK on
        stderr, then a module the suite exercised prints a refusal on
        stdout. The refusal is the fixture working as designed and must
        never become the battery's one line about the whole check."""
        out = self._drive(
            "import sys\n"
            "sys.stderr.write('Ran 175 tests in 409.183s\\n\\nOK (skipped=2)\\n')\n"
            "print('migrate: outcomes.jsonl SHRANK while this rewrite held the "
            "writer lock (69 bytes read, 19 on disk)')\n")
        self.assertIn("PASS", out, "a green suite lost its PASS verdict: %r" % out)
        self.assertIn("OK (skipped=2)", out,
                      "the summary does not carry the suite's own verdict: %r" % out)
        self.assertNotIn("SHRANK", out,
                         "the battery still summarises a green suite with a "
                         "fixture's refusal line: %r" % out)

    def test_a_check_with_no_unittest_verdict_keeps_its_last_line(self):
        """Most checks in this battery are not unittest suites and their
        last line already is their verdict. The rule must not reach past
        them and invent a different line."""
        out = self._drive("print('547 evals: 547 passed, 0 regressions.')\n")
        self.assertIn("547 evals: 547 passed, 0 regressions.", out,
                      "a plain check lost its own summary line: %r" % out)

    def test_a_no_data_skip_still_reads_no_data_with_an_honest_line(self):
        """The verdict column is decided by the skip's own reason, and this
        change touches only the text beside it: a suite that skips for a
        NO-DATA reason stays NO-DATA and now says what the suite reported."""
        out = self._drive(
            "import sys\n"
            "sys.stderr.write(\"test_x (m.C) ... skipped 'NO-DATA: nothing to "
            "compare'\\n\\nRan 3 tests in 0.1s\\n\\nOK (skipped=1)\\n\")\n"
            "print('some module chattered here')\n")
        self.assertIn("NO-DATA", out, "the NO-DATA skip rule was broken: %r" % out)
        self.assertIn("OK (skipped=1)", out,
                      "the summary does not carry the suite's own verdict: %r" % out)
        self.assertNotIn("chattered", out,
                         "a trailing print still stands in for the verdict: %r" % out)

    def test_a_failing_suite_still_names_its_failure(self):
        """Regression guard on the branch that already learned this lesson:
        a FAIL keeps naming the failure, not the trailing print."""
        out = self._drive(
            "import sys\n"
            "sys.stderr.write('FAIL: test_y (m.C)\\n\\nFAILED (failures=1)\\n')\n"
            "print('unrelated closing chatter')\n"
            "sys.exit(1)\n", expect_code=0)
        self.assertIn("FAIL", out, "a failing check lost its FAIL verdict: %r" % out)
        self.assertNotIn("unrelated closing chatter", out,
                         "a failing check is summarised by trailing chatter: %r" % out)


if __name__ == "__main__":
    unittest.main(verbosity=2)

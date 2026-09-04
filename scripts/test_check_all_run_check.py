"""check_all.sh's run_check() summary line, driven in isolation.

E78, security review 2026-09-03. run_check() used to summarize a FAILING
check with its output's generic LAST line, whatever that happened to be.
A check whose real failure printed earlier, then some unrelated component
logged its own benign line on the way out, read as e.g. "FAIL exit 1
drift OK the board still matches the world": a FAIL verdict sitting
beside a line that says everything is fine. The fix keeps the LAST line
that actually carries a failure word (FAIL, REFUSED, BLOCK, or Error) for
a failing check, falling back to the generic last line only when nothing
in the output says so.

The whole script is not run here (it executes this repository's real
battery, dozens of minutes): only run_check() itself is extracted with
sed and driven against small fake commands, exactly the function under
test and nothing else.
"""
import os
import subprocess
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
CHECK_ALL = os.path.join(HERE, "check_all.sh")


def extract_run_check():
    """The literal text of run_check() { ... }, lines 28 to 73 in the real
    file as of this writing; extracted by matching the function's own
    opening and closing braces rather than a hardcoded line range, so a
    later edit that shifts the function still gets the current body."""
    with open(CHECK_ALL, encoding="utf-8") as fh:
        lines = fh.readlines()
    start = next(i for i, l in enumerate(lines)
                if l.startswith("run_check() {"))
    end = next(i for i in range(start, len(lines))
              if lines[i].rstrip("\n") == "}")
    return "".join(lines[start:end + 1])


def run_isolated(fake_cmd_body, name="fake"):
    """Runs ONLY run_check(), against a fake command script it writes,
    never the real battery. Returns (stdout, exit_of_the_shell)."""
    run_check_src = extract_run_check()
    tmp = tempfile.mkdtemp(prefix="check-all-run-check-")
    cmd_path = os.path.join(tmp, "fakecmd.sh")
    with open(cmd_path, "w", encoding="utf-8") as fh:
        fh.write("#!/bin/sh\n" + fake_cmd_body)
    os.chmod(cmd_path, 0o755)
    harness = os.path.join(tmp, "harness.sh")
    with open(harness, "w", encoding="utf-8") as fh:
        fh.write("#!/bin/sh\n" + run_check_src +
                 "\nrun_check \"%s\" sh %s\n" % (name, cmd_path))
    os.chmod(harness, 0o755)
    proc = subprocess.run(["sh", harness], cwd=tmp, capture_output=True,
                          text=True, timeout=30)
    return proc.stdout, proc.returncode


class FailingCheckSummaryNamesTheFailureNotTheLastLine(unittest.TestCase):

    def test_a_benign_trailing_line_is_replaced_by_the_failure_line(self):
        # The exact shape found live: the real failure prints first, then
        # an unrelated, innocuous OK line prints last, and the check still
        # exits nonzero.
        out, _rc = run_isolated(
            "echo 'FAIL: test_x (module.Case)'\n"
            "echo 'OK drift the board still matches the world'\n"
            "exit 1\n")
        self.assertIn("FAIL", out, out)
        self.assertIn("exit 1", out, out)
        self.assertIn("FAIL: test_x (module.Case)", out, out)
        self.assertNotIn("board still matches the world", out, out)

    def test_no_failure_wording_falls_back_to_the_generic_last_line(self):
        # Nothing in the output says FAIL/REFUSED/BLOCK/Error at all: the
        # fallback (the old, generic behavior) still applies, so this
        # never goes silent about a check with unusual output.
        out, _rc = run_isolated(
            "echo 'something went sideways, no standard wording'\n"
            "exit 1\n")
        self.assertIn("FAIL", out, out)
        self.assertIn("exit 1", out, out)
        self.assertIn("something went sideways", out, out)

    def test_a_passing_check_is_unaffected(self):
        out, _rc = run_isolated("echo 'all good'\nexit 0\n")
        self.assertIn("PASS", out, out)
        self.assertIn("all good", out, out)

    def test_a_no_data_check_is_unaffected(self):
        out, _rc = run_isolated("echo 'NO-DATA: nothing to check'\nexit 2\n")
        self.assertIn("NO-DATA", out, out)
        self.assertIn("nothing to check", out, out)


if __name__ == "__main__":
    unittest.main()

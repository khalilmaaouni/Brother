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

# E100: one sandbox for every temp tree this process makes, removed at exit.
import os as _e100_os, sys as _e100_sys  # noqa: E402
_e100_sys.path.append(_e100_os.path.join(
    _e100_os.path.dirname(_e100_os.path.abspath(__file__)), '.'))
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


def run_isolated(fake_cmd_body, name="fake", call=None):
    """Runs ONLY run_check(), against a fake command script it writes,
    never the real battery. `call` replaces the default invocation
    (`sh <fake script>`); "{cmd}" in it names the fake script's path.
    Returns (stdout, exit_of_the_shell)."""
    run_check_src = extract_run_check()
    tmp = tempfile.mkdtemp(prefix="check-all-run-check-")
    cmd_path = os.path.join(tmp, "fakecmd.sh")
    with open(cmd_path, "w", encoding="utf-8") as fh:
        fh.write("#!/bin/sh\n" + fake_cmd_body)
    os.chmod(cmd_path, 0o755)
    call = (call or "sh {cmd}").replace("{cmd}", cmd_path)
    harness = os.path.join(tmp, "harness.sh")
    with open(harness, "w", encoding="utf-8") as fh:
        fh.write("#!/bin/sh\n" + run_check_src +
                 "\nrun_check \"%s\" %s\n" % (name, call))
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


class AbsentInterpreterIsNoDataNeverFail(unittest.TestCase):
    """A machine without a check's interpreter (Windows has no
    /usr/bin/python3, and may have no python3 on PATH) did not run the
    check, so the check has not said anything is broken: NO-DATA. Only
    the interpreter being absent qualifies. A 127 a check returns from
    inside itself, or a script path that does not exist, is a broken
    repository, and stays FAIL."""

    def test_an_absent_interpreter_reads_no_data_and_is_never_started(self):
        out, _rc = run_isolated("echo 'must never print'\nexit 0\n",
                                call="brother-no-such-interpreter-x9 {cmd}")
        self.assertIn("NO-DATA exit 127", out, out)
        self.assertIn("command not found: brother-no-such-interpreter-x9",
                      out, out)
        self.assertNotIn("must never print", out, out)

    def test_a_127_from_inside_a_check_stays_fail(self):
        out, _rc = run_isolated("brother-no-such-tool-x9\n")
        self.assertIn("FAIL", out, out)
        self.assertIn("exit 127", out, out)
        self.assertNotIn("NO-DATA", out, out)

    def test_a_missing_script_path_stays_fail(self):
        out, _rc = run_isolated("exit 0\n",
                                call="./brother-no-such-dir/check.sh")
        self.assertIn("FAIL", out, out)
        self.assertNotIn("NO-DATA", out, out)


PIN = "/usr/bin/python3"
PATH_PY = '"$(command -v python3 || echo python3)"'
# The fake check is a Python program; its "#!/bin/sh" first line is a
# comment to Python, so run_isolated's file serves as-is.
PRINT_INTERPRETER = "import sys\nprint('ran under ' + sys.executable)\n"


def run_pinned(pin, swaps=()):
    """run_check() with the pin it knows about swapped for `pin` (every
    mention, so its note names the swapped path too), called the way the
    battery calls a pinned check. `swaps` are further (old, new) text
    replacements, each required to hit exactly once."""
    src = extract_run_check()
    assert src.count(PIN) >= 2, src
    src = src.replace(PIN, pin)
    for old, new in swaps:
        assert src.count(old) == 1, (old, src)
        src = src.replace(old, new)
    tmp = tempfile.mkdtemp(prefix="check-all-run-check-")
    cmd_path = os.path.join(tmp, "fakecheck.py")
    with open(cmd_path, "w", encoding="utf-8") as fh:
        fh.write(PRINT_INTERPRETER)
    harness = os.path.join(tmp, "harness.sh")
    with open(harness, "w", encoding="utf-8") as fh:
        fh.write("#!/bin/sh\n" + src +
                 '\nrun_check "fake" %s %s\n' % (pin, cmd_path))
    proc = subprocess.run(["sh", harness], cwd=tmp, capture_output=True,
                          text=True, timeout=30)
    return proc.stdout


class PinnedInterpreterIsUsedWhenPresentOnly(unittest.TestCase):
    """PROJECT.md and docs/CHARTER.md pin /usr/bin/python3 for the checks
    that start with it, on purpose. run_check keeps the pin wherever it
    exists; only a machine without it (Windows) runs those checks under
    python3 from PATH, and says so. The check lines themselves never
    change, so every tool that reads them reads what it always did."""

    def test_a_present_pin_is_used_and_nothing_is_noted(self):
        out = run_pinned(sys.executable)
        self.assertIn("PASS", out, out)
        self.assertIn("ran under " + sys.executable, out, out)
        self.assertNotIn("NOTE", out, out)

    def test_an_absent_pin_runs_under_python3_on_path_and_says_so(self):
        out = run_pinned("/brother-no-such-dir/python3")
        self.assertIn("PASS", out, out)
        self.assertIn("ran under ", out, out)
        self.assertIn("NOTE: /brother-no-such-dir/python3 is absent", out, out)

    def test_an_absent_pin_and_no_python3_at_all_reads_no_data(self):
        out = run_pinned("/brother-no-such-dir/python3", swaps=[
            (PATH_PY, '"$(command -v brother-no-such-py-x9 || '
                      'echo brother-no-such-py-x9)"')])
        self.assertIn("NO-DATA exit 127", out, out)
        self.assertIn("command not found: brother-no-such-py-x9", out, out)
        self.assertNotIn("ran under", out, out)

    def test_the_pinned_check_lines_are_unchanged(self):
        # The fallback lives in run_check, not in the lines, because
        # battery_verdict.py, system_doc.py and brother_run.py read a
        # check's command text as written.
        with open(CHECK_ALL, encoding="utf-8") as fh:
            pinned = [l for l in fh
                      if l.startswith("run_check ") and " %s " % PIN in l]
        self.assertGreaterEqual(len(pinned), 1, "no check names the pin")


if __name__ == "__main__":
    unittest.main()

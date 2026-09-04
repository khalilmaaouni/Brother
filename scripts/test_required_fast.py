"""test_required_fast.py: drives scripts/required_fast.sh BACKWARDS.

Never runs the real check suites (that would make this test itself minutes
long, defeating its own purpose as a fast pre-merge guard's own regression
test). Instead it builds a TEMP COPY of the script with the real run_check
invocations swapped for stub commands (`true` / `false` / `sh -c "exit 2"`),
reusing the script's own run_check function and summary logic verbatim, and
asserts the summary line and the exit code for three cases: all pass, one
fail, and a mix that includes NO-DATA (exit 2).

This is the same "drive it backwards, never trust a single green" method the
rest of this estate's self-tests use (see scripts/test_battery_verdict.py).
"""
import os
import re
import stat
import subprocess
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
SCRIPT = os.path.join(HERE, "required_fast.sh")

with open(SCRIPT) as f:
    _SOURCE = f.read()

# The script is HEADER (shebang, comments, cd, counters, the run_check
# function, the two intro echo lines) + a fixed block of real run_check
# calls + FOOTER (the summary block and exit). Split on the script's own
# literal anchors so a stub build never depends on which real checks are
# currently listed.
_CHECKS_START = 'run_check "version-truth"'
_FOOTER_START = '\necho\necho "pass $pass   fail $fail   no-data $nodata"'

_start = _SOURCE.index(_CHECKS_START)
_end = _SOURCE.index(_FOOTER_START)
HEADER = _SOURCE[:_start]
FOOTER = _SOURCE[_end:]

assert HEADER.strip(), "could not locate the header before the first real check"
assert FOOTER.strip(), "could not locate the summary footer"


def build_stub_script(stub_lines):
    """stub_lines: list of 'run_check "name" <stub command>' strings."""
    body = HEADER + "\n".join(stub_lines) + "\n" + FOOTER
    fd, path = tempfile.mkstemp(prefix="required-fast-stub-", suffix=".sh")
    with os.fdopen(fd, "w") as f:
        f.write(body)
    os.chmod(path, os.stat(path).st_mode | stat.S_IEXEC)
    return path


def run(path):
    proc = subprocess.run(["sh", path], capture_output=True, text=True, timeout=20)
    return proc.returncode, proc.stdout + proc.stderr


class RequiredFastScript(unittest.TestCase):
    def test_script_exists_and_is_shell(self):
        self.assertTrue(os.path.exists(SCRIPT))
        with open(SCRIPT) as f:
            self.assertTrue(f.readline().startswith("#!/bin/sh"))

    def test_all_pass_exits_zero_and_counts_match(self):
        path = build_stub_script([
            'run_check "stub-a" true',
            'run_check "stub-b" true',
            'run_check "stub-c" true',
        ])
        try:
            code, out = run(path)
        finally:
            os.remove(path)
        self.assertEqual(code, 0, out)
        self.assertIn("pass 3   fail 0   no-data 0", out)
        self.assertNotIn("FAILED:", out)

    def test_one_fail_exits_one_and_is_named(self):
        path = build_stub_script([
            'run_check "stub-a" true',
            'run_check "stub-fails" false',
            'run_check "stub-c" true',
        ])
        try:
            code, out = run(path)
        finally:
            os.remove(path)
        self.assertEqual(code, 1, out)
        self.assertIn("pass 2   fail 1   no-data 0", out)
        self.assertIn("FAILED:", out)
        self.assertIn("stub-fails", out)
        # the failing check's full output is captured to a named file
        m = re.search(r"\[full: (\S+)\]", out)
        self.assertIsNotNone(m, out)
        self.assertTrue(os.path.exists(m.group(1)), out)
        os.remove(m.group(1))

    def test_no_data_is_reported_and_never_fails_the_run(self):
        path = build_stub_script([
            'run_check "stub-a" true',
            'run_check "stub-nodata" sh -c "exit 2"',
            'run_check "stub-c" true',
        ])
        try:
            code, out = run(path)
        finally:
            os.remove(path)
        self.assertEqual(code, 0, out)
        self.assertIn("pass 2   fail 0   no-data 1", out)
        self.assertIn("NO-DATA:", out)
        self.assertIn("stub-nodata", out)
        self.assertNotIn("FAILED:", out)

    def test_fail_and_no_data_together_still_fails_on_the_fail(self):
        path = build_stub_script([
            'run_check "stub-fails" false',
            'run_check "stub-nodata" sh -c "exit 2"',
        ])
        try:
            code, out = run(path)
        finally:
            os.remove(path)
        self.assertEqual(code, 1, out)
        self.assertIn("pass 0   fail 1   no-data 1", out)


if __name__ == "__main__":
    unittest.main(verbosity=2)

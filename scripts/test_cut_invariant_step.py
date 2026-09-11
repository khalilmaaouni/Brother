#!/usr/bin/env python3
"""test_cut_invariant_step: CDX-4, scripts/cut_v1.0.0.sh's release-invariant
line (row "== 4. release invariant and export dry run").

WHAT WAS WRONG. The line read:

    python3 scripts/release_invariant.py || echo "NOTE: ..."

`set -e` is active in cut_v1.0.0.sh (line 15), so an ordinary failing
command would stop the script on its own -- but `|| echo ...` is itself a
command that always exits 0, so the `set -e` trap never fires: a real FAIL
(exit 1, a genuine identity contradiction) was swallowed exactly like the
expected pre-tag NO-DATA (exit 2) the comment says it is there for. The fix
drops the `|| echo` so only `set -e` governs the line: exit 0 (PASS or a
permissible pre-tag NO-DATA text on stdout) continues, exit 1 or exit 2
stops the script.

HOW THIS IS TESTED, never by running the real release_invariant.py: the
release-invariant line is extracted from the live script by grep, so a
future edit to that line is tested as it actually reads, not as a frozen
copy. It is wrapped in a two-line `set -e` shell snippet and driven with a
fake `python3` placed first on PATH (the same stub-on-PATH shape as
test_release_closeout_virgin.py's write_fake_gh): one stub exits 1 (a real
FAIL) and the snippet must stop before printing the marker; one stub prints
a NO-DATA line and exits 0 (the honest pre-tag state) and the snippet must
continue past it.
"""
import os
import stat
import subprocess
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
CUT_SCRIPT = os.path.join(HERE, "cut_v1.0.0.sh")

MARKER = "STEP_CONTINUED"


def invariant_line():
    """The exact release_invariant.py line from the live cut script, so
    this test is coupled to what the script actually runs, not a copy."""
    with open(CUT_SCRIPT, encoding="utf-8") as fh:
        for line in fh:
            if "scripts/release_invariant.py" in line:
                return line.rstrip("\n")
    raise AssertionError("no release_invariant.py line found in %s" % CUT_SCRIPT)


def write_stub_python3(bin_dir, exit_code, stdout=""):
    path = os.path.join(bin_dir, "python3")
    body = "#!/bin/sh\n"
    if stdout:
        body += "echo %r\n" % stdout
    body += "exit %d\n" % exit_code
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(body)
    st = os.stat(path)
    os.chmod(path, st.st_mode | stat.S_IEXEC | stat.S_IXGRP | stat.S_IXOTH)
    return path


def run_step(bin_dir):
    """Run `set -e` plus the extracted line plus a continuation marker,
    with bin_dir's fake python3 first on PATH. Returns (returncode,
    stdout)."""
    snippet = "set -e\n%s\necho %s\n" % (invariant_line(), MARKER)
    env = dict(os.environ)
    env["PATH"] = bin_dir + os.pathsep + env.get("PATH", "")
    proc = subprocess.run(
        ["sh", "-c", snippet], cwd=ROOT, env=env,
        stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
    return proc.returncode, proc.stdout


class TheInvariantStepStopsOnAFail(unittest.TestCase):
    def setUp(self):
        self.work = tempfile.mkdtemp(prefix="cut-invariant-step.")

    def tearDown(self):
        import shutil
        shutil.rmtree(self.work, ignore_errors=True)

    def test_a_real_fail_stops_the_script(self):
        """exit 1 (release_invariant.py's FAIL) must stop the line before
        the marker prints. On the unfixed `|| echo NOTE` line this
        assertion FAILS: the swallow always exits 0, so the marker prints
        anyway and the script never stops."""
        write_stub_python3(self.work, exit_code=1)
        code, out = run_step(self.work)
        self.assertNotEqual(code, 0, "script must stop on a real FAIL")
        self.assertNotIn(MARKER, out,
                         "marker printed: the FAIL was swallowed")

    def test_a_pretag_no_data_continues(self):
        """exit 0 with a NO-DATA line on stdout for the tag link only (the
        honest between-releases state release_invariant.py's own docstring
        names, links 1 and 2 still checked) must not stop the script:
        NO-DATA is not a contradiction."""
        write_stub_python3(self.work, exit_code=0,
                           stdout="NO-DATA: public repository carries no "
                                  "tag yet; the cut precedes the tag")
        code, out = run_step(self.work)
        self.assertEqual(code, 0, "a pre-tag NO-DATA read at exit 0 must "
                                  "let the script continue: %r" % out)
        self.assertIn(MARKER, out)


if __name__ == "__main__":
    unittest.main(verbosity=2)

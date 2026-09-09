#!/usr/bin/env python3
"""D-003 (2026-09-08 persona dogfood, register A3-S3/A3-S4): "report first,
require an explicit fence before refusing". `sbe fences` already told the
truth about an unfenced repository ("no fence is enforceable here, so every
write would be ALLOWED"), but only to whoever thought to run it on its own;
`sbe verify` said nothing about it at all. This is the second named place the
D-003 decision calls for: one line in `sbe verify`'s own output, printed
whether the rest of that run PASSed or FAILed, never gating `worst`.

Run standalone: python3 tools/test_sbe_verify_fence_report.py
"""
import io
import os
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
    _e100_sys.stderr.write(
        "tmp_sandbox absent: %s leaves its temp trees behind\n"
        % _e100_os.path.basename(__file__))

HERE = os.path.dirname(os.path.abspath(__file__))
SBE = os.path.join(HERE, "..", "bin", "sbe")

FENCED_STATE_MD = (
    "# STATE\n\n"
    "## Fence registry\n\n"
    "- agent: tester (sole writer, session s1) | tier T1 | objective: x | "
    "files: README.md |\n"
    "\n## Decisions\n"
)


def write(path, text):
    with io.open(path, "w", encoding="utf-8") as f:
        f.write(text)


def run_verify(cwd):
    return subprocess.run([sys.executable, SBE, "verify", "."], cwd=cwd,
                          capture_output=True, text=True,
                          stdin=subprocess.DEVNULL, timeout=120)


class TestVerifyReportsAnUnfencedRepository(unittest.TestCase):

    def test_no_registry_at_all_prints_the_named_line(self):
        with tempfile.TemporaryDirectory() as d:
            r = run_verify(d)
            self.assertIn(
                "sbe verify: NO FENCE REGISTRY", r.stdout,
                "an unfenced repository must be NAMED in sbe verify's own "
                "output, not left to sbe fences alone. stdout: %s" % r.stdout)
            self.assertIn("no fence registry was opened", r.stdout)
            self.assertIn("ALLOWED", r.stdout)

    def test_a_live_fence_registry_prints_nothing_about_it(self):
        """The calibration case: a STATE.md carrying a live fence line, and
        the named-line addition is silent, exactly like the two informational
        blocks it sits beside in _cmd_verify."""
        with tempfile.TemporaryDirectory() as d:
            write(os.path.join(d, "STATE.md"), FENCED_STATE_MD)
            write(os.path.join(d, "README.md"), "x\n")
            r = run_verify(d)
            self.assertNotIn("NO FENCE REGISTRY", r.stdout,
                             "a fenced repository must not get the unfenced "
                             "report. stdout: %s" % r.stdout)


if __name__ == "__main__":
    unittest.main()

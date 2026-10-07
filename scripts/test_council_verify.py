#!/usr/bin/env python3
"""The council verifier's own suite, registered in the battery.
Its selftest holds the table of cases; this wrapper is what check_all.sh runs, and it fails loudly rather than
reporting a skip if the module cannot even be imported."""
import os
import subprocess
import sys
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
TOOL = os.path.join(HERE, "council_verify.py")


class CouncilVerifierSelftest(unittest.TestCase):
    def test_the_selftest_table_passes(self):
        r = subprocess.run([sys.executable, "-B", TOOL, "--selftest"],
                           capture_output=True, text=True, timeout=300)
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertIn("OK", r.stdout)

    def test_the_selftest_reports_a_real_case_count(self):
        r = subprocess.run([sys.executable, "-B", TOOL, "--selftest"],
                           capture_output=True, text=True, timeout=300)
        self.assertRegex(r.stdout, r"selftest: [1-9]\d* cases")


class TheScreenRefusesWhatIsNotAPlainGrep(unittest.TestCase):
    """The one control that matters here: a seat proposes the command, so the screen decides what may run."""

    def setUp(self):
        sys.path.insert(0, HERE)
        import council_verify
        self.allowed = council_verify.allowed

    def test_a_plain_grep_is_allowed(self):
        self.assertIsNotNone(self.allowed("grep -n foo scripts/a.py"))

    def test_a_shell_metacharacter_is_refused(self):
        for bad in ("grep foo a.py | sh", "grep foo a.py; rm -rf x", "grep $(id) a.py", "grep a b\nrm x"):
            self.assertIsNone(self.allowed(bad), bad)

    def test_another_program_is_refused(self):
        self.assertIsNone(self.allowed("python3 -c 'print(1)'"))

    def test_a_path_outside_the_repository_is_refused(self):
        for bad in ("grep -R foo /etc", "grep foo ~/.ssh/id", "grep foo a/../../b"):
            self.assertIsNone(self.allowed(bad), bad)

    def test_an_option_that_reads_another_file_is_refused(self):
        self.assertIsNone(self.allowed("grep -f /etc/passwd a.py"))

    def test_a_non_string_is_refused(self):
        self.assertIsNone(self.allowed(None))


if __name__ == "__main__":
    unittest.main(verbosity=1)

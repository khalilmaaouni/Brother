#!/usr/bin/env python3
"""Fixtures for the behaviour revision log (P9's confirmed remainder): a row
reworded since it was last accepted must name itself in 08-behaviour.md's
appended Revision log section, or `sbe_design.py behaviour` FAILs naming it.

Run: python3 tools/test_sbe_behaviour_revision_log.py

Same "no mocked filesystem" rule as tools/test_sbe_design_fingerprint.py:
every test runs the real tools/sbe_design.py as a subprocess against a real
temporary directory, and this file mirrors that one's fixture shape (a single
"widget" domain row, so check_behaviour's shipped-example refusal never fires
and no cell text collides with the wording a test just changed).
"""
import io
import os
import shutil
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
    # A packager (scripts/export_public.py, make_benchmark_bundle.py)
    # can copy this test without scripts/tmp_sandbox.py beside it. Say
    # so rather than dying: the sandbox is hygiene, not the subject.
    _e100_sys.stderr.write(
        "tmp_sandbox absent: %s leaves its temp trees behind\n"
        % _e100_os.path.basename(__file__))

HERE = os.path.dirname(os.path.abspath(__file__))
DESIGN = os.path.join(HERE, "sbe_design.py")

ROW_TEMPLATE = ("| B1 | The widget service is deployed | Something calls `widget()` | %s | %s |\n")
OUTCOME = "It returns the string ok"
PROOF = "Unit test: call `widget()`, assert the return value equals ok"


def row(outcome=OUTCOME, proof=PROOF):
    return ROW_TEMPLATE % (outcome, proof)


def behaviour_md(row_text, log_text=""):
    body = ("# 08. Behaviour table\n\n## Rules\n"
            "| ID | Starting point | Trigger | Required outcome | Proof |\n"
            "|---|---|---|---|---|\n" + row_text)
    if log_text:
        body += ("\n## Revision log\n"
                  "| Date | Row | Change |\n"
                  "|---|---|---|\n" + log_text)
    return body


def write(path, body):
    directory = os.path.dirname(path)
    if directory and not os.path.isdir(directory):
        os.makedirs(directory)
    with io.open(path, "w", encoding="utf-8") as fh:
        fh.write(body)


def verdict_line(text, name):
    for line in text.splitlines():
        parts = line.split()
        if len(parts) >= 2 and parts[0] == name:
            return line
    return "NO VERDICT LINE for %r in:\n%s" % (name, text)


def verdict_of(text, name):
    line = verdict_line(text, name)
    parts = line.split()
    return parts[1] if len(parts) >= 2 and parts[0] == name else line


class RevisionLogFixture(unittest.TestCase):

    def setUp(self):
        self.dir = tempfile.mkdtemp(prefix="sbe-behaviour-revlog-")
        self.path = os.path.join(self.dir, "08-behaviour.md")

    def tearDown(self):
        shutil.rmtree(self.dir, ignore_errors=True)

    def write_row(self, outcome=OUTCOME, proof=PROOF, log_text=""):
        write(self.path, behaviour_md(row(outcome, proof), log_text))

    def run_check(self):
        out = subprocess.run([sys.executable, DESIGN, "behaviour", self.dir],
                             capture_output=True, text=True)
        return out.returncode, out.stdout + out.stderr

    def run_accept(self):
        out = subprocess.run([sys.executable, DESIGN, "behaviour-accept", self.dir],
                             capture_output=True, text=True)
        return out.returncode, out.stdout + out.stderr


class TestRevisionLog(RevisionLogFixture):

    def test_untouched_dossier_passes(self):
        self.write_row()
        code, out = self.run_check()
        self.assertEqual(code, 0, out)
        self.assertEqual(verdict_of(out, "behaviour"), "PASS", out)

    def test_edited_row_with_no_log_entry_fails_naming_the_row(self):
        self.write_row()
        code, out = self.run_accept()
        self.assertEqual(code, 0, out)
        self.write_row(outcome=OUTCOME + " within one second")
        code, out = self.run_check()
        self.assertEqual(code, 0, out)
        line = verdict_line(out, "behaviour")
        self.assertEqual(verdict_of(out, "behaviour"), "FAIL", out)
        self.assertIn("revision log", line, out)
        self.assertIn("B1", line, out)

    def test_edited_row_with_log_entry_passes(self):
        self.write_row()
        code, out = self.run_accept()
        self.assertEqual(code, 0, out)
        self.write_row(outcome=OUTCOME + " within one second",
                       log_text="| 2026-08-30 | B1 | tightened the timing | \n")
        code, out = self.run_check()
        self.assertEqual(code, 0, out)
        line = verdict_line(out, "behaviour")
        self.assertEqual(verdict_of(out, "behaviour"), "PASS", out)
        self.assertNotIn("revision log", line, out)

    def test_no_behaviour_file_reads_no_data(self):
        code, out = self.run_check()
        self.assertEqual(code, 0, out)
        self.assertEqual(verdict_of(out, "behaviour"), "NO-DATA", out)


if __name__ == "__main__":
    unittest.main()

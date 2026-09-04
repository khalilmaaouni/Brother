#!/usr/bin/env python3
"""Fixtures for the behaviour-row fingerprint (Band 1 item 1): a per-row digest
of what 08-behaviour.md's row REQUIRES, a record of it kept beside the
dossier (.sbe/behaviour-fingerprints.json), and the SUSPECT note
check_behaviour prints in tools/sbe_design.py when the two disagree.

Run: python3 tools/test_sbe_design_fingerprint.py

Every test runs the real `tools/sbe_design.py` as a subprocess against a real
temporary directory, the same "no mocked filesystem" rule
tools/test_sbe_bypass.py already states: the defect this feature exists to
catch lives at the seam between the table an analyst edits and the record a
machine keeps beside it, and a mocked seam would test the mock.

ISOLATION: every dossier here carries exactly one behaviour row, in a
"widget" domain that shares nothing with the shipped template's
checkout/order/warehouse example (the same domain tools/test_sbe_verify_
converge.py already uses for the same reason), so check_behaviour's
shipped-example refusal never fires and the ID and Proof cells never carry
text that could accidentally collide with the wording a test just changed.
That keeps every verdict here PASS, so a SUSPECT or NO-DATA note is never
mixed up with an unrelated FAIL the check would have produced anyway.
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

#: The one row every fixture starts from. %s is the Required outcome cell in
#: some tests and the Proof cell in others, so each test edits exactly one
#: cell and nothing else moves.
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
    """The one report line for a named check, or a sentence saying it is
    absent. Mirrors tools/test_sbe_bypass.py's helper of the same name."""
    for line in text.splitlines():
        parts = line.split()
        if len(parts) >= 2 and parts[0] == name:
            return line
    return "NO VERDICT LINE for %r in:\n%s" % (name, text)


def verdict_of(text, name):
    line = verdict_line(text, name)
    parts = line.split()
    return parts[1] if len(parts) >= 2 and parts[0] == name else line


class FingerprintFixture(unittest.TestCase):
    """A throwaway dossier directory per test, holding only 08-behaviour.md
    (and, once accepted, .sbe/behaviour-fingerprints.json)."""

    def setUp(self):
        self.dir = tempfile.mkdtemp(prefix="sbe-behaviour-fp-")
        self.path = os.path.join(self.dir, "08-behaviour.md")

    def tearDown(self):
        shutil.rmtree(self.dir, ignore_errors=True)

    def write_row(self, outcome=OUTCOME, proof=PROOF, log_text=""):
        write(self.path, behaviour_md(row(outcome, proof), log_text))

    def run_check(self, extra=()):
        argv = [sys.executable, DESIGN, "behaviour"] + list(extra) + [self.dir]
        out = subprocess.run(argv, capture_output=True, text=True)
        return out.returncode, out.stdout + out.stderr

    def run_accept(self):
        argv = [sys.executable, DESIGN, "behaviour-accept", self.dir]
        out = subprocess.run(argv, capture_output=True, text=True)
        return out.returncode, out.stdout + out.stderr


class TestBehaviourFingerprintSuspect(FingerprintFixture):

    def test_first_run_with_no_record_is_nodata_not_suspect_or_clean(self):
        self.write_row()
        code, out = self.run_check()
        self.assertEqual(code, 0, out)
        line = verdict_line(out, "behaviour")
        self.assertEqual(verdict_of(out, "behaviour"), "PASS", out)
        self.assertIn("NO-DATA", line, out)
        self.assertNotIn("SUSPECT", line, out)
        # The "clean" sentence is specific ("match their last accepted
        # wording"); a first run must never print it, or a reader could not
        # tell "never accepted" apart from "accepted and unchanged".
        self.assertNotIn("match their last accepted wording", line, out)

    def test_reworded_rule_with_a_log_entry_reads_suspect_but_still_passes(self):
        # An unlogged rework now FAILs (P9's revision-log check, see
        # tools/test_sbe_behaviour_revision_log.py): the SUSPECT note stays
        # PASS-compatible only once the rework is logged.
        self.write_row()
        code, out = self.run_accept()
        self.assertEqual(code, 0, out)
        self.write_row(outcome=OUTCOME + " within one second",
                       log_text="| 2026-08-30 | B1 | tightened the timing | \n")
        code, out = self.run_check()
        self.assertEqual(code, 0, out)
        line = verdict_line(out, "behaviour")
        self.assertEqual(verdict_of(out, "behaviour"), "PASS", out)
        self.assertIn("SUSPECT", line, out)
        self.assertIn("B1", line, out)

    def test_accepting_again_clears_suspect(self):
        self.write_row()
        self.run_accept()
        self.write_row(outcome=OUTCOME + " within one second")
        code, out = self.run_check()
        self.assertIn("SUSPECT", verdict_line(out, "behaviour"), out)
        code, out = self.run_accept()
        self.assertEqual(code, 0, out)
        code, out = self.run_check()
        self.assertEqual(code, 0, out)
        line = verdict_line(out, "behaviour")
        self.assertNotIn("SUSPECT", line, out)
        self.assertNotIn("NO-DATA", line, out)
        self.assertIn("1 of 1 row(s) match their last accepted wording", line, out)

    def test_proof_only_edit_does_not_read_suspect(self):
        self.write_row()
        code, out = self.run_accept()
        self.assertEqual(code, 0, out)
        self.write_row(proof=PROOF + ", twice, to be sure")
        code, out = self.run_check()
        self.assertEqual(code, 0, out)
        line = verdict_line(out, "behaviour")
        self.assertEqual(verdict_of(out, "behaviour"), "PASS", out)
        self.assertNotIn("SUSPECT", line, out)
        self.assertNotIn("NO-DATA", line, out)
        self.assertIn("1 of 1 row(s) match their last accepted wording", line, out)

    def test_exit_code_identical_with_and_without_a_logged_suspect_row(self):
        # The SUSPECT note by itself must never move the exit code (a rework
        # is not a missing Required outcome or Proof); it is an UNLOGGED
        # rework that now blocks under --strict, which is a different,
        # separate problem this fixture does not create.
        self.write_row()
        self.run_accept()
        code_clean, out_clean = self.run_check(extra=("--strict",))
        self.assertNotIn("SUSPECT", verdict_line(out_clean, "behaviour"), out_clean)
        self.write_row(outcome=OUTCOME + " within one second",
                       log_text="| 2026-08-30 | B1 | tightened the timing | \n")
        code_suspect, out_suspect = self.run_check(extra=("--strict",))
        self.assertIn("SUSPECT", verdict_line(out_suspect, "behaviour"), out_suspect)
        self.assertEqual(code_clean, code_suspect,
                         "a SUSPECT row moved the exit code: clean=%r (%s) vs suspect=%r (%s)"
                         % (code_clean, out_clean, code_suspect, out_suspect))
        self.assertEqual(code_suspect, 0, out_suspect)


if __name__ == "__main__":
    unittest.main()

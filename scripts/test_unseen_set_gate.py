#!/usr/bin/env python3
"""Calibration for scripts/unseen_set_gate.py (row M8).

Drives all three verdicts backwards against synthetic RECORD fixtures: PASS
(hash, agreement line and every answer-level correction marked applied),
FAIL (an unapplied answer-level correction), and NO-DATA (no RECORD file,
and a RECORD with no "## Blind audit" section at all).
"""
import os
import shutil
import subprocess
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import unseen_set_gate  # noqa: E402

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SCRIPT = os.path.join(REPO, "scripts", "unseen_set_gate.py")

HASH_64 = "a" * 64
HASH_32 = "b" * 32

PASSING_SECTION = """## Blind audit

Auditor scratch hash (sha256, locked before the seed was opened): `%s`

Agreement: 38 of 40.

Corrections (answer-level, field `expected`):
- W-01: expected, KEEP SEPARATE to LINK AS RELATED (APPLIED)
""" % HASH_64


def run(*args):
    proc = subprocess.run([sys.executable, SCRIPT] + list(args),
                          capture_output=True, text=True)
    return proc.returncode, proc.stdout + proc.stderr


class UnseenSetGate(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp(prefix="unseen-gate-")
        self.seed_path = os.path.join(self.dir, "unseen-9-2026-09-07.json")
        with open(self.seed_path, "w", encoding="utf-8") as fh:
            fh.write("{}")
        self.record_path = os.path.join(self.dir, "unseen-9-2026-09-07-RECORD.md")

    def tearDown(self):
        shutil.rmtree(self.dir, ignore_errors=True)

    def _write_record(self, text):
        with open(self.record_path, "w", encoding="utf-8") as fh:
            fh.write(text)

    def test_record_path_for_derives_the_sibling_record(self):
        self.assertEqual(
            unseen_set_gate.record_path_for(
                "benchmarks/jbeq/mdm/unseen-4-2026-09-06.json"),
            "benchmarks/jbeq/mdm/unseen-4-2026-09-06-RECORD.md",
        )

    def test_pass_names_the_hash_and_agreement(self):
        self._write_record("# Record\n\n" + PASSING_SECTION)
        status, message = unseen_set_gate.gate(self.seed_path)
        self.assertEqual(status, unseen_set_gate.PASS, message)
        self.assertIn(HASH_64, message)
        self.assertIn("38 of 40", message)
        code, out = run(self.seed_path)
        self.assertEqual(code, unseen_set_gate.EXIT_PASS, out)
        self.assertIn("PASS", out)

    def test_a_32_hex_md5_style_hash_also_passes(self):
        section = PASSING_SECTION.replace(HASH_64, HASH_32)
        self._write_record("# Record\n\n" + section)
        status, message = unseen_set_gate.gate(self.seed_path)
        self.assertEqual(status, unseen_set_gate.PASS, message)
        self.assertIn(HASH_32, message)

    def test_fail_names_an_unapplied_answer_level_correction(self):
        section = PASSING_SECTION + (
            "- W-02: expected, ESCALATE to REJECT MATCH (PENDING)\n"
        )
        self._write_record("# Record\n\n" + section)
        status, message = unseen_set_gate.gate(self.seed_path)
        self.assertEqual(status, unseen_set_gate.FAIL, message)
        self.assertIn("W-02", message)
        self.assertIn("unapplied", message)
        code, out = run(self.seed_path)
        self.assertEqual(code, unseen_set_gate.EXIT_FAIL, out)
        self.assertIn("FAIL", out)

    def test_fail_names_a_missing_hash(self):
        section = "## Blind audit\n\nAgreement: 38 of 40.\n"
        self._write_record("# Record\n\n" + section)
        status, message = unseen_set_gate.gate(self.seed_path)
        self.assertEqual(status, unseen_set_gate.FAIL, message)
        self.assertIn("hash", message)

    def test_fail_names_a_missing_agreement_line(self):
        section = "## Blind audit\n\nHash: `%s`\n" % HASH_64
        self._write_record("# Record\n\n" + section)
        status, message = unseen_set_gate.gate(self.seed_path)
        self.assertEqual(status, unseen_set_gate.FAIL, message)
        self.assertIn("agreement", message)

    def test_nodata_when_the_record_file_is_absent(self):
        status, message = unseen_set_gate.gate(self.seed_path)
        self.assertEqual(status, unseen_set_gate.NODATA, message)
        code, out = run(self.seed_path)
        self.assertEqual(code, unseen_set_gate.EXIT_NODATA, out)
        self.assertIn("NO-DATA", out)

    def test_nodata_when_the_record_carries_no_blind_audit_section(self):
        self._write_record("# Record\n\nNo audit ran on this set yet.\n")
        status, message = unseen_set_gate.gate(self.seed_path)
        self.assertEqual(status, unseen_set_gate.NODATA, message)
        self.assertIn("Blind audit", message)

    def test_a_line_saying_not_applied_is_never_read_as_applied(self):
        # A bare substring check for "APPLIED" also matches "NOT APPLIED"
        # and "PENDING, not applied", both of which contain that word; the
        # gate must key on the exact "(APPLIED)" marker, nothing looser.
        section = PASSING_SECTION + (
            "- W-04: expected, ESCALATE to NO-DATA (NOT APPLIED)\n"
        )
        self._write_record("# Record\n\n" + section)
        status, message = unseen_set_gate.gate(self.seed_path)
        self.assertEqual(status, unseen_set_gate.FAIL, message)
        self.assertIn("W-04", message)

    def test_a_non_expected_correction_is_never_checked_for_applied(self):
        # A correction to input/rationale/critical_class is not an
        # "answer-level" item under M8's own contract; it is never checked
        # here, applied or not, and must never block a PASS.
        section = PASSING_SECTION + (
            "- W-03: critical_class, WRONG PAYER to HIERARCHY REVERSAL "
            "(PENDING)\n"
        )
        self._write_record("# Record\n\n" + section)
        status, _message = unseen_set_gate.gate(self.seed_path)
        self.assertEqual(status, unseen_set_gate.PASS)


if __name__ == "__main__":
    unittest.main()

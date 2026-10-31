#!/usr/bin/env python3
"""The age bar is superseded by a PROVEN content binding, and by nothing else.

This holds readiness_gate._check_record in the direction it must fail in. The
defect it exists for: on 2026-10-20, 2026-10-21 and again on the 1.1.9 line on
2026-10-28, the restore-drill row read NO-DATA on trees nobody had changed,
purely because days had passed, and that red blocked every landing. Measured on
2026-10-28: 66 gate rows PASS, this row NO-DATA, drill_date 8 days old, and all
9 of the record's covered files byte identical to the commit it names.

A content binding (`covered`: path plus sha256, still byte identical here) is a
STRICTLY STRONGER reading of the same property the age bar approximates: it
proves the record ran against this code rather than inferring it from a
calendar. So where that binding is proven, the age bar is skipped.

Everywhere else the bar applies in full. The cases below are the whole point:
one fixture per condition, each refusable by exactly one rule, so no fixture can
certify a rule it did not exercise.

    python3 -B scripts/test_readiness_gate_clock_binding.py
"""
import hashlib
import json
import os
import subprocess
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "scripts"))
import readiness_gate as rg  # noqa: E402

RECORD = os.path.join("docs", "plan", "A-RECORD.json")
COVERED = os.path.join("docs", "plan", "covered-thing.txt")
STALE_DATE = "2026-10-20"   # 8 days before TODAY, over any 7 day bar
TODAY = "2026-10-28"
BAR = 7


def _git(root, *args):
    return subprocess.run(["git", "-C", root] + list(args),
                          capture_output=True, text=True)


def _repo(covered_body=b"the thing the record certifies\n", drift=False,
          declare_covered=True, drill_date=STALE_DATE, passed=True):
    """A real one commit repository carrying a record and the file it covers.

    A real repository, not a stub: the gate resolves the record's commit with
    git, and a fixture that fakes that would be testing the fake.
    """
    root = tempfile.mkdtemp(prefix="rgclock-")
    os.makedirs(os.path.join(root, "docs", "plan"))
    with open(os.path.join(root, COVERED), "wb") as fh:
        fh.write(covered_body)
    _git(root, "init", "-q")
    _git(root, "config", "user.email", "t@example.invalid")
    _git(root, "config", "user.name", "t")
    _git(root, "config", "commit.gpgsign", "false")
    _git(root, "config", "tag.forceSignAnnotated", "false")
    _git(root, "add", "-A")
    _git(root, "commit", "-q", "-m", "the commit the record names")
    sha = _git(root, "rev-parse", "HEAD").stdout.strip()

    doc = {"commit": sha, "passed": passed}
    if drill_date is not None:
        doc["drill_date"] = drill_date
    if declare_covered:
        doc["covered"] = [{"path": COVERED,
                           "sha256": hashlib.sha256(covered_body).hexdigest()}]
    with open(os.path.join(root, RECORD), "w", encoding="utf-8") as fh:
        json.dump(doc, fh)
    if drift:
        # The covered file changes AFTER the record was written, so the
        # record no longer describes the code under test.
        with open(os.path.join(root, COVERED), "wb") as fh:
            fh.write(covered_body + b"a later change\n")
    return root


def verdict(root):
    return rg._check_record(root, RECORD, key="passed", today=TODAY, max_age_days=BAR)


class AgeBarSuperseded(unittest.TestCase):
    def test_a_proven_content_binding_supersedes_a_stale_date(self):
        """The one case that changed: binding proven, date long past the bar."""
        root = _repo()
        got, why = verdict(root)
        self.assertEqual(rg.PASS, got, why)
        self.assertIn("superseded", why)


class AgeBarStillApplies(unittest.TestCase):
    """Each case below differs from the passing one in exactly ONE way."""

    def test_covered_file_changed_since_the_record(self):
        root = _repo(drift=True)
        got, why = verdict(root)
        self.assertEqual(rg.NODATA, got, why)
        self.assertIn("8 day(s) old", why)

    def test_record_declares_no_covered_set(self):
        root = _repo(declare_covered=False)
        got, why = verdict(root)
        self.assertEqual(rg.NODATA, got, why)
        self.assertIn("8 day(s) old", why)

    def test_a_false_record_is_still_a_failure(self):
        """The skip must not convert a failed drill into a pass."""
        root = _repo(passed=False)
        got, why = verdict(root)
        self.assertEqual(rg.FAIL, got, why)

    def test_no_drill_date_with_no_binding_is_still_no_data(self):
        root = _repo(declare_covered=False, drill_date=None)
        got, why = verdict(root)
        self.assertEqual(rg.NODATA, got, why)
        self.assertIn("drill_date", why)


class TheBarIsStillReachable(unittest.TestCase):
    def test_a_fresh_date_with_no_binding_passes_on_age(self):
        """Proves the age branch can still reach PASS, so the NO-DATA cases
        above are refusals by the bar and not by a broken branch."""
        root = _repo(declare_covered=False, drill_date=TODAY)
        got, why = verdict(root)
        self.assertEqual(rg.PASS, got, why)
        self.assertIn("age 0 days", why)


if __name__ == "__main__":
    unittest.main(verbosity=2)

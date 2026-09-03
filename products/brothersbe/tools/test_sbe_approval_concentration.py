#!/usr/bin/env python3
"""Tests for tools/sbe_approval_concentration.py, driven backwards from the
build acceptance in docs/handover/2026-08-16-complete/01-TEAM-ISSUES-AND-WHAT-IS-DONE.md
(P13): three changes approved by one person print a concentration line
naming that person; no signed approvals print NO-DATA.

Run: python3 tools/test_sbe_approval_concentration.py
"""
import importlib.util
import os
import subprocess
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.abspath(os.path.join(HERE, ".."))

_spec = importlib.util.spec_from_file_location(
    "sbe_approval_concentration", os.path.join(HERE, "sbe_approval_concentration.py"))
sac = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(sac)

F = sac.FIELD_SEP


def record(sha, sig="G", trailer=""):
    return F.join([sha, "2026-08-16 00:00:00 +0000", sig, trailer])


class ParseRecordsCase(unittest.TestCase):
    """The pure parser, exercised without touching git at all."""

    def test_the_sweeps_own_fixture_case_three_and_two_prints_the_split(self):
        lines = (
            [record("a%d" % i, trailer="Alice <alice@example.com>") for i in range(3)]
            + [record("b%d" % i, trailer="Bob <bob@example.com>") for i in range(2)]
        )
        counts, warnings = sac.parse_records("\n".join(lines))
        self.assertEqual(counts, {"Alice <alice@example.com>": 3,
                                   "Bob <bob@example.com>": 2})
        self.assertEqual(warnings, [])

    def test_empty_window_produces_zero_counts(self):
        counts, warnings = sac.parse_records("")
        self.assertEqual(counts, {})
        self.assertEqual(warnings, [])

    def test_a_commit_with_no_approved_by_trailer_is_not_counted_and_not_warned(self):
        counts, warnings = sac.parse_records(record("c0", trailer=""))
        self.assertEqual(counts, {})
        self.assertEqual(warnings, [])

    def test_an_unsigned_approval_is_excluded_without_a_warning(self):
        counts, warnings = sac.parse_records(
            record("d0", sig="N", trailer="Carol <carol@example.com>"))
        self.assertEqual(counts, {})
        self.assertEqual(warnings, [])

    def test_a_malformed_record_shape_is_skipped_with_a_named_warning_never_a_crash(self):
        text = "not-enough-fields" + F + "only-two"
        counts, warnings = sac.parse_records(text)
        self.assertEqual(counts, {})
        self.assertEqual(len(warnings), 1)
        self.assertIn("malformed", warnings[0])

    def test_a_placeholder_approver_identity_is_skipped_with_a_named_warning(self):
        counts, warnings = sac.parse_records(record("e0", trailer="TBD"))
        self.assertEqual(counts, {})
        self.assertEqual(len(warnings), 1)
        self.assertIn("e0", warnings[0])
        self.assertIn("malformed", warnings[0])

    def test_mixed_good_and_malformed_records_never_crash_and_count_only_the_good_one(self):
        text = "\n".join([
            record("f0", trailer="Dana <dana@example.com>"),
            "garbage" + F + "too" + F + "few",
            record("f1", trailer="TBD"),
        ])
        counts, warnings = sac.parse_records(text)
        self.assertEqual(counts, {"Dana <dana@example.com>": 1})
        self.assertEqual(len(warnings), 2)


class RenderCase(unittest.TestCase):
    """render()/safe_render() over a monkeypatched git_log_records, so the
    report layer is tested without needing a real signed commit."""

    def setUp(self):
        self._orig = sac.git_log_records
        self.addCleanup(setattr, sac, "git_log_records", self._orig)

    def test_no_signed_approvals_prints_no_data_naming_the_window(self):
        sac.git_log_records = lambda root, days: ("", None)
        lines = sac.render("/some/repo", 30)
        text = "\n".join(lines)
        self.assertIn("NO-DATA", text)
        self.assertIn("30 day", text)

    def test_three_and_two_prints_a_concentration_line_naming_the_majority_approver(self):
        recs = (
            [record("a%d" % i, trailer="Alice <alice@example.com>") for i in range(3)]
            + [record("b%d" % i, trailer="Bob <bob@example.com>") for i in range(2)]
        )
        sac.git_log_records = lambda root, days: ("\n".join(recs), None)
        lines = sac.render("/some/repo", 30)
        text = "\n".join(lines)
        self.assertIn("5 signed approval(s) from 2 approver(s)", text)
        self.assertIn("CONCENTRATION: Alice <alice@example.com> holds 3/5 (60%)", text)

    def test_unreadable_git_history_prints_no_data_not_a_crash(self):
        sac.git_log_records = lambda root, days: ("", "not a git repository")
        lines = sac.render("/some/repo", 30)
        self.assertIn("NO-DATA", "\n".join(lines))

    def test_a_crash_inside_render_is_caught_by_safe_render_never_raises(self):
        def boom(root, days):
            raise RuntimeError("boom")
        sac.git_log_records = boom
        lines = sac.safe_render("/some/repo", 30)
        text = "\n".join(lines)
        self.assertIn("NO-DATA", text)
        self.assertIn("boom", text)


class MainSubprocessCase(unittest.TestCase):
    """One real subprocess run against a real (unsigned) git repo: proves the
    tool actually shells out to git and always exits 0."""

    def test_a_real_unsigned_repo_reports_no_data_at_exit_0(self):
        with tempfile.TemporaryDirectory() as tmp:
            subprocess.run(["git", "init", "-q", tmp], check=True)
            subprocess.run(["git", "-C", tmp, "config", "user.email", "t@example.com"],
                           check=True)
            subprocess.run(["git", "-C", tmp, "config", "user.name", "Test"], check=True)
            open(os.path.join(tmp, "f.txt"), "w").close()
            subprocess.run(["git", "-C", tmp, "add", "f.txt"], check=True)
            subprocess.run(["git", "-C", tmp, "commit", "-q", "-m", "no approval here"],
                           check=True)

            proc = subprocess.run(
                [sys.executable, os.path.join(ROOT, "tools", "sbe_approval_concentration.py"),
                 tmp, "--days", "3650"],
                capture_output=True, text=True)
            self.assertEqual(proc.returncode, 0)
            self.assertIn("NO-DATA", proc.stdout)


if __name__ == "__main__":
    unittest.main()

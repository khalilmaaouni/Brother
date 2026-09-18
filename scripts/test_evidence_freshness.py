#!/usr/bin/env python3
"""Tests for evidence_freshness.py (DOM-10.04).

Plain unittest, runnable directly: python3 scripts/test_evidence_freshness.py -v
"""

import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

import evidence_freshness


HERE = Path(__file__).resolve().parent
SCRIPT = str(HERE / "evidence_freshness.py")
GIT_AVAILABLE = shutil.which("git") is not None


class ParseTimestampTests(unittest.TestCase):
    def test_bare_int(self):
        self.assertEqual(evidence_freshness.parse_timestamp(1600000000), 1600000000.0)

    def test_bare_float(self):
        self.assertEqual(evidence_freshness.parse_timestamp(1234.5), 1234.5)

    def test_numeric_string(self):
        self.assertEqual(evidence_freshness.parse_timestamp("1600000000"), 1600000000.0)

    def test_iso_string_with_offset(self):
        self.assertEqual(
            evidence_freshness.parse_timestamp("2020-09-13T12:26:40+00:00"),
            1600000000.0,
        )

    def test_iso_string_with_z(self):
        self.assertEqual(
            evidence_freshness.parse_timestamp("2020-09-13T12:26:40Z"),
            1600000000.0,
        )

    def test_invalid_string_raises(self):
        with self.assertRaises(ValueError):
            evidence_freshness.parse_timestamp("not-a-timestamp")

    def test_none_raises(self):
        with self.assertRaises(ValueError):
            evidence_freshness.parse_timestamp(None)

    def test_bool_raises(self):
        # bool is an int subclass in Python: True would otherwise silently
        # parse as 1.0, which is never a real timestamp a caller meant.
        with self.assertRaises(ValueError):
            evidence_freshness.parse_timestamp(True)


class ClassifyTests(unittest.TestCase):
    def test_fresh_when_no_rules_trigger(self):
        status, reason = evidence_freshness.classify(1000.0, 900.0, 1100.0)
        self.assertEqual(status, "FRESH")
        self.assertEqual(reason, "evidence is current")

    def test_stale_because_evidence_predates_source(self):
        status, reason = evidence_freshness.classify(900.0, 1000.0, 1100.0)
        self.assertEqual(status, "STALE")
        self.assertIn("source", reason.lower())

    def test_stale_because_max_age_exceeded(self):
        status, reason = evidence_freshness.classify(1000.0, None, 2000.0, max_age_s=500.0)
        self.assertEqual(status, "STALE")
        self.assertIn("max_age_s", reason)

    def test_fresh_when_age_exactly_equals_max_age(self):
        status, _ = evidence_freshness.classify(1000.0, None, 1500.0, max_age_s=500.0)
        self.assertEqual(status, "FRESH")

    def test_no_data_when_evidence_missing(self):
        status, _ = evidence_freshness.classify(None, 900.0, 1100.0)
        self.assertEqual(status, "NO-DATA")

    def test_refused_when_evidence_in_future(self):
        status, reason = evidence_freshness.classify(2000.0, None, 1000.0)
        self.assertEqual(status, "REFUSED")
        self.assertIn("ahead of now", reason)

    def test_refused_when_source_in_future(self):
        status, reason = evidence_freshness.classify(900.0, 2000.0, 1000.0)
        self.assertEqual(status, "REFUSED")
        self.assertIn("ahead of now", reason)

    def test_evidence_equal_to_source_is_fresh(self):
        status, _ = evidence_freshness.classify(1000.0, 1000.0, 1000.0)
        self.assertEqual(status, "FRESH")

    def test_now_none_raises(self):
        with self.assertRaises(ValueError):
            evidence_freshness.classify(1000.0, 900.0, None)

    def test_negative_max_age_raises(self):
        with self.assertRaises(ValueError):
            evidence_freshness.classify(1000.0, 900.0, 1100.0, max_age_s=-0.1)


class ExitCodeForTests(unittest.TestCase):
    def test_known_statuses(self):
        self.assertEqual(evidence_freshness.exit_code_for("FRESH"), 0)
        self.assertEqual(evidence_freshness.exit_code_for("STALE"), 1)
        self.assertEqual(evidence_freshness.exit_code_for("NO-DATA"), 2)
        self.assertEqual(evidence_freshness.exit_code_for("REFUSED"), 3)

    def test_unknown_status_raises(self):
        with self.assertRaises(ValueError):
            evidence_freshness.exit_code_for("MAYBE")


@unittest.skipUnless(GIT_AVAILABLE, "git is not installed")
class GitLastEditTimeTests(unittest.TestCase):
    def _git(self, cwd, *args, env=None):
        return subprocess.run(
            ["git", *args], cwd=cwd, capture_output=True, text=True, env=env, check=True
        )

    def _init_repo(self, path):
        self._git(path, "init")
        self._git(path, "config", "user.email", "tester@example.invalid")
        self._git(path, "config", "user.name", "Test User")

    def test_returns_commit_time_for_committed_file(self):
        known_epoch = 1600000000
        with tempfile.TemporaryDirectory() as tmp:
            self._init_repo(tmp)
            target = Path(tmp) / "evidence.txt"
            target.write_text("hello\n", encoding="utf-8")
            env = dict(os.environ)
            env["GIT_AUTHOR_DATE"] = "%d +0000" % known_epoch
            env["GIT_COMMITTER_DATE"] = "%d +0000" % known_epoch
            self._git(tmp, "add", "evidence.txt")
            self._git(tmp, "commit", "-m", "first", env=env)
            result = evidence_freshness.git_last_edit_time("evidence.txt", cwd=tmp)
            self.assertEqual(result, float(known_epoch))

    def test_uncommitted_path_returns_none(self):
        with tempfile.TemporaryDirectory() as tmp:
            self._init_repo(tmp)
            committed = Path(tmp) / "committed.txt"
            committed.write_text("data\n", encoding="utf-8")
            self._git(tmp, "add", "committed.txt")
            self._git(tmp, "commit", "-m", "first")
            result = evidence_freshness.git_last_edit_time("never_committed.txt", cwd=tmp)
            self.assertIsNone(result)

    def test_non_git_directory_returns_none(self):
        with tempfile.TemporaryDirectory() as tmp:
            result = evidence_freshness.git_last_edit_time("anything.txt", cwd=tmp)
            self.assertIsNone(result)


class CliTests(unittest.TestCase):
    def _run_cli(self, *extra):
        return subprocess.run(
            [sys.executable, SCRIPT, "check", *extra], capture_output=True, text=True
        )

    def test_fresh_exit_zero(self):
        result = self._run_cli(
            "--evidence-at", "1500000000",
            "--max-age-seconds", "1000",
            "--now", "1500000500",
        )
        self.assertEqual(result.returncode, 0)
        lines = result.stdout.strip().splitlines()
        self.assertEqual(len(lines), 1)
        self.assertTrue(lines[0].startswith("evidence_freshness check: FRESH ("), lines[0])
        self.assertTrue(lines[0].endswith(")"), lines[0])

    def test_stale_exit_one(self):
        result = self._run_cli(
            "--evidence-at", "1500000000",
            "--max-age-seconds", "100",
            "--now", "1500000500",
        )
        self.assertEqual(result.returncode, 1)
        self.assertIn("evidence_freshness check: STALE", result.stdout)

    def test_no_data_exit_two(self):
        result = self._run_cli("--now", "1500000500")
        self.assertEqual(result.returncode, 2)
        self.assertIn("evidence_freshness check: NO-DATA", result.stdout)

    def test_refused_future_evidence_exit_three(self):
        result = self._run_cli("--evidence-at", "2000000000", "--now", "1500000000")
        self.assertEqual(result.returncode, 3)
        self.assertIn("evidence_freshness check: REFUSED", result.stdout)

    def test_bad_record_is_no_data_with_stderr(self):
        missing = os.path.join(tempfile.gettempdir(), "no_such_record_xyz_12345.json")
        result = self._run_cli("--evidence-record", missing, "--now", "1500000500")
        self.assertEqual(result.returncode, 2)
        self.assertIn("NO-DATA", result.stdout)
        self.assertIn("NO-DATA", result.stderr)

    def test_bad_source_is_no_data_with_stderr(self):
        with tempfile.TemporaryDirectory() as tmp:
            result = self._run_cli(
                "--evidence-at", "1500000000",
                "--source", os.path.join(tmp, "nothing.txt"),
                "--now", "1500000500",
            )
            self.assertEqual(result.returncode, 2)
            self.assertIn("NO-DATA", result.stdout)
            self.assertIn("NO-DATA", result.stderr)

    def test_evidence_record_reads_named_field(self):
        with tempfile.TemporaryDirectory() as tmp:
            record = Path(tmp) / "record.json"
            record.write_text('{"at": "1500000000"}', encoding="utf-8")
            result = self._run_cli(
                "--evidence-record", str(record),
                "--now", "1500000500",
                "--max-age-seconds", "3600",
            )
            self.assertEqual(result.returncode, 0)
            self.assertIn("evidence_freshness check: FRESH", result.stdout)

    def test_no_source_and_no_max_age_is_no_data_not_fresh(self):
        """Orchestrator finding: this used to print FRESH and exit 0 with
        nothing to compare against, a pass about nothing."""
        with tempfile.TemporaryDirectory() as tmp:
            record = Path(tmp) / "record.json"
            record.write_text('{"at": "1500000000"}', encoding="utf-8")
            result = self._run_cli(
                "--evidence-record", str(record),
                "--now", "1500000500",
            )
            self.assertEqual(result.returncode, 2)
            self.assertIn("NO-DATA", result.stdout)


if __name__ == "__main__":
    unittest.main()

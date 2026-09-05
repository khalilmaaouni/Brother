"""The Acceptance Time benchmark harness, driven both ways (S11; roadmap row
S11; protocol benchmarks/ACCEPTANCE-TIME.md).

Mirrors scripts/test_acceptance_compression.py's shape: prepare() is driven
through its own subprocess CLI so the nine packets are proven as a user
would actually get them, and score() is driven with a real fixture CSV
rather than a hand typed expectation, including the honest floor (fewer
than five reviewers refuses to report a comparison).
"""
import csv
import os
import shutil
import subprocess
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
SCRIPT = os.path.join(HERE, "acceptance_time.py")


def _write_csv(path, rows):
    with open(path, "w", newline="", encoding="utf-8") as fh:
        writer = csv.writer(fh)
        writer.writerow(["reviewer", "change", "condition", "seconds",
                         "decision"])
        for row in rows:
            writer.writerow(row)


def _six_reviewer_rows():
    """Six reviewers, one row per condition each (18 rows total), seconds
    and decisions chosen so every condition's median and correctness rate
    is easy to check by hand: raw_diff slowest and half wrong,
    ordinary_summary faster and mostly right, brother_receipt fastest and
    always right."""
    reviewers = ["r1", "r2", "r3", "r4", "r5", "r6"]
    raw_seconds = [120, 130, 140, 150, 160, 170]
    raw_decisions = ["reject", "reject", "reject", "accept", "accept",
                     "accept"]
    summary_seconds = [80, 85, 90, 95, 100, 105]
    summary_decisions = ["reject", "reject", "reject", "reject", "accept",
                         "accept"]
    receipt_seconds = [40, 45, 50, 55, 60, 65]
    receipt_decisions = ["reject"] * 6
    rows = []
    for i, reviewer in enumerate(reviewers):
        rows.append((reviewer, "medium-feature", "raw_diff",
                    raw_seconds[i], raw_decisions[i]))
        rows.append((reviewer, "auth-security", "ordinary_summary",
                    summary_seconds[i], summary_decisions[i]))
        rows.append((reviewer, "schema-migration", "brother_receipt",
                    receipt_seconds[i], receipt_decisions[i]))
    return rows


class PreparationWritesNinePackets(unittest.TestCase):
    def test_prepare_writes_nine_packets_in_a_temp_dir(self):
        tmp = tempfile.mkdtemp(prefix="acceptance-time-test-")
        try:
            result = subprocess.run(
                [sys.executable, SCRIPT, "prepare", tmp],
                capture_output=True, text=True, timeout=120)
            self.assertEqual(result.returncode, 0, result.stderr)
            written = result.stdout.strip().splitlines()
            self.assertEqual(len(written), 9, written)
            for change_id in ("medium-feature", "auth-security",
                              "schema-migration"):
                for name in ("raw_diff.txt", "ordinary_summary.txt",
                            "brother_receipt.txt"):
                    path = os.path.join(tmp, change_id, name)
                    self.assertTrue(os.path.isfile(path), path)
                    with open(path, encoding="utf-8") as fh:
                        content = fh.read()
                    self.assertTrue(content.strip(), path)
        finally:
            shutil.rmtree(tmp, ignore_errors=True)

    def test_the_raw_diff_is_a_real_git_diff_not_a_hand_typed_string(self):
        tmp = tempfile.mkdtemp(prefix="acceptance-time-test-")
        try:
            result = subprocess.run(
                [sys.executable, SCRIPT, "prepare", tmp],
                capture_output=True, text=True, timeout=120)
            self.assertEqual(result.returncode, 0, result.stderr)
            path = os.path.join(tmp, "medium-feature", "raw_diff.txt")
            with open(path, encoding="utf-8") as fh:
                diff_text = fh.read()
            if diff_text.startswith("NO-DATA"):
                self.skipTest("NO-DATA: %s" % diff_text)
            self.assertIn("diff --git", diff_text)
            self.assertIn("is_strong_password", diff_text)
        finally:
            shutil.rmtree(tmp, ignore_errors=True)

    def test_the_brother_receipt_names_review_first(self):
        tmp = tempfile.mkdtemp(prefix="acceptance-time-test-")
        try:
            subprocess.run([sys.executable, SCRIPT, "prepare", tmp],
                           capture_output=True, text=True, timeout=120,
                           check=True)
            path = os.path.join(tmp, "auth-security", "brother_receipt.txt")
            with open(path, encoding="utf-8") as fh:
                receipt_text = fh.read()
            self.assertIn("REVIEW FIRST", receipt_text)
            self.assertIn("src/middleware/rate_limit.py", receipt_text)
        finally:
            shutil.rmtree(tmp, ignore_errors=True)


class ScoringEnforcesTheHonestFloor(unittest.TestCase):
    def test_six_reviewers_prints_the_three_medians(self):
        tmp = tempfile.mkdtemp(prefix="acceptance-time-test-")
        try:
            csv_path = os.path.join(tmp, "results.csv")
            _write_csv(csv_path, _six_reviewer_rows())
            result = subprocess.run(
                [sys.executable, SCRIPT, "score", csv_path],
                capture_output=True, text=True, timeout=60)
            self.assertEqual(result.returncode, 0,
                             result.stdout + result.stderr)
            self.assertIn("raw_diff: median 145.0s", result.stdout)
            self.assertIn("ordinary_summary: median 92.5s", result.stdout)
            self.assertIn("brother_receipt: median 52.5s", result.stdout)
            self.assertIn("n=6", result.stdout)
            self.assertIn("correct 50%", result.stdout)
            self.assertIn("correct 100%", result.stdout)
        finally:
            shutil.rmtree(tmp, ignore_errors=True)

    def test_two_reviewers_prints_no_data_and_exits_three(self):
        tmp = tempfile.mkdtemp(prefix="acceptance-time-test-")
        try:
            csv_path = os.path.join(tmp, "results.csv")
            _write_csv(csv_path, [
                ("r1", "medium-feature", "raw_diff", 100, "reject"),
                ("r2", "medium-feature", "raw_diff", 110, "reject"),
            ])
            result = subprocess.run(
                [sys.executable, SCRIPT, "score", csv_path],
                capture_output=True, text=True, timeout=60)
            self.assertEqual(result.returncode, 3,
                             result.stdout + result.stderr)
            self.assertIn("NO-DATA", result.stdout)
        finally:
            shutil.rmtree(tmp, ignore_errors=True)


if __name__ == "__main__":
    unittest.main()

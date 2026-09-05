"""Tests for the Acceptance Compression trial assignment and validation
harness (E3; scripts/acceptance_trial_assign.py) and for the frozen
success rule (E1; benchmarks/gauntlets/acceptance-compression/
SUCCESS-RULE-FROZEN.md) staying byte for byte what it was when frozen.

Tempfile only: no fixture here is written under the repository tree.
"""
import csv
import hashlib
import os
import subprocess
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, HERE)
SCRIPT = os.path.join(HERE, "acceptance_trial_assign.py")
RULE_PATH = os.path.join(
    ROOT, "benchmarks", "gauntlets", "acceptance-compression",
    "SUCCESS-RULE-FROZEN.md")

import acceptance_trial_assign as ATA  # noqa: E402
import acceptance_time as AT  # noqa: E402

#: The frozen rule's own SHA-256 at the moment it was written (2026-09-05).
#: A later edit to the file changes this hash and this test catches it.
FROZEN_RULE_SHA256 = (
    "cb00b796b5c1f38d17af905118d822cc308855038b8814454686f4bc936e1497")


class TheFrozenSuccessRuleIsUnchanged(unittest.TestCase):
    def test_success_rule_file_exists_and_hashes_to_the_frozen_value(self):
        self.assertTrue(os.path.isfile(RULE_PATH), RULE_PATH)
        with open(RULE_PATH, "rb") as fh:
            digest = hashlib.sha256(fh.read()).hexdigest()
        self.assertEqual(
            digest, FROZEN_RULE_SHA256,
            "SUCCESS-RULE-FROZEN.md changed since it was frozen: "
            "expected sha256 %s, got %s. If this edit was intentional, "
            "update FROZEN_RULE_SHA256 here in the same change and say "
            "so; a silent drift here is exactly what this test exists "
            "to catch." % (FROZEN_RULE_SHA256, digest))


class AssignmentIsBalancedAndReproducible(unittest.TestCase):
    def test_five_reviewers_each_see_each_change_exactly_once(self):
        rows = ATA.assignment_table(5, seed=1)
        self.assertEqual(len(rows), 15)
        by_reviewer = {}
        for reviewer, change, condition in rows:
            by_reviewer.setdefault(reviewer, set()).add(change)
        self.assertEqual(len(by_reviewer), 5)
        change_ids = set(c["id"] for c in AT.CHANGES)
        for reviewer, changes_seen in by_reviewer.items():
            self.assertEqual(changes_seen, change_ids, reviewer)

    def test_no_reviewer_sees_a_change_under_two_conditions(self):
        rows = ATA.assignment_table(8, seed=7)
        pairs = [(reviewer, change) for reviewer, change, _ in rows]
        self.assertEqual(len(pairs), len(set(pairs)))

    def test_conditions_are_balanced_across_reviewers_per_change(self):
        rows = ATA.assignment_table(6, seed=3)
        by_change = {}
        for reviewer, change, condition in rows:
            by_change.setdefault(change, []).append(condition)
        for change, conditions in by_change.items():
            counts = dict((c, conditions.count(c)) for c in AT.CONDITIONS)
            self.assertEqual(len(conditions), 6, change)
            # six reviewers, three conditions divides evenly: exactly two
            # reviewers per condition for every change.
            for condition, count in counts.items():
                self.assertEqual(count, 2, (change, condition, counts))

    def test_same_seed_is_reproducible_different_seed_can_differ(self):
        first = ATA.assignment_table(5, seed=99)
        second = ATA.assignment_table(5, seed=99)
        self.assertEqual(first, second)
        third = ATA.assignment_table(5, seed=100)
        self.assertNotEqual(first, third)

    def test_cli_assign_refuses_below_the_honest_floor(self):
        result = subprocess.run(
            [sys.executable, SCRIPT, "assign", "3"],
            capture_output=True, text=True, timeout=30)
        self.assertEqual(result.returncode, 3, result.stdout + result.stderr)
        self.assertIn("NO-DATA", result.stdout)

    def test_cli_assign_writes_a_template_csv_the_scorer_can_read(self):
        tmp = tempfile.mkdtemp(prefix="acceptance-trial-assign-test-")
        try:
            csv_path = os.path.join(tmp, "template.csv")
            result = subprocess.run(
                [sys.executable, SCRIPT, "assign", "5", "--seed", "42",
                 "--out-csv", csv_path],
                capture_output=True, text=True, timeout=30)
            self.assertEqual(result.returncode, 0,
                             result.stdout + result.stderr)
            self.assertTrue(os.path.isfile(csv_path), csv_path)
            with open(csv_path, newline="", encoding="utf-8") as fh:
                reader = csv.DictReader(fh)
                self.assertEqual(
                    reader.fieldnames,
                    ["reviewer", "change", "condition", "seconds",
                     "decision"])
                rows = list(reader)
            self.assertEqual(len(rows), 15)
            for row in rows:
                self.assertEqual(row["seconds"], "")
                self.assertEqual(row["decision"], "")
        finally:
            import shutil
            shutil.rmtree(tmp, ignore_errors=True)


class ValidatorRefusesBadResults(unittest.TestCase):
    def _run(self, rows, tmp):
        csv_path = os.path.join(tmp, "results.csv")
        with open(csv_path, "w", newline="", encoding="utf-8") as fh:
            writer = csv.writer(fh)
            writer.writerow(["reviewer", "change", "condition", "seconds",
                             "decision"])
            for row in rows:
                writer.writerow(row)
        result = subprocess.run(
            [sys.executable, SCRIPT, "validate", csv_path],
            capture_output=True, text=True, timeout=30)
        return result

    def test_clean_csv_passes(self):
        tmp = tempfile.mkdtemp(prefix="acceptance-trial-assign-test-")
        try:
            result = self._run([
                ("reviewer-1", "medium-feature", "raw_diff", "120",
                 "reject"),
                ("reviewer-1", "auth-security", "brother_receipt", "45",
                 "reject"),
            ], tmp)
            self.assertEqual(result.returncode, 0,
                             result.stdout + result.stderr)
            self.assertIn("clean", result.stdout)
        finally:
            import shutil
            shutil.rmtree(tmp, ignore_errors=True)

    def test_missing_time_is_refused(self):
        tmp = tempfile.mkdtemp(prefix="acceptance-trial-assign-test-")
        try:
            result = self._run([
                ("reviewer-1", "medium-feature", "raw_diff", "", "reject"),
            ], tmp)
            self.assertEqual(result.returncode, 1,
                             result.stdout + result.stderr)
            self.assertIn("no recorded time", result.stdout)
        finally:
            import shutil
            shutil.rmtree(tmp, ignore_errors=True)

    def test_impossible_time_is_refused_both_directions(self):
        tmp = tempfile.mkdtemp(prefix="acceptance-trial-assign-test-")
        try:
            result = self._run([
                ("reviewer-1", "medium-feature", "raw_diff", "-5",
                 "reject"),
                ("reviewer-2", "auth-security", "raw_diff", "999999",
                 "reject"),
            ], tmp)
            self.assertEqual(result.returncode, 1,
                             result.stdout + result.stderr)
            self.assertIn("impossible time", result.stdout)
        finally:
            import shutil
            shutil.rmtree(tmp, ignore_errors=True)

    def test_reviewer_seeing_a_change_twice_is_refused(self):
        tmp = tempfile.mkdtemp(prefix="acceptance-trial-assign-test-")
        try:
            result = self._run([
                ("reviewer-1", "medium-feature", "raw_diff", "120",
                 "reject"),
                ("reviewer-1", "medium-feature", "brother_receipt", "40",
                 "reject"),
            ], tmp)
            self.assertEqual(result.returncode, 1,
                             result.stdout + result.stderr)
            self.assertIn("already saw change", result.stdout)
        finally:
            import shutil
            shutil.rmtree(tmp, ignore_errors=True)

    def test_missing_csv_file_is_nodata_not_a_crash(self):
        result = subprocess.run(
            [sys.executable, SCRIPT, "validate", "/no/such/path.csv"],
            capture_output=True, text=True, timeout=30)
        self.assertEqual(result.returncode, 2, result.stdout + result.stderr)
        self.assertIn("NO-DATA", result.stdout)


if __name__ == "__main__":
    unittest.main()

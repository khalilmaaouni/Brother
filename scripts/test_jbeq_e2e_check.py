"""Calibration for scripts/jbeq_e2e_check.py.

Every case asserts the EXIT CODE, never only the printed verdict, for the
reason split_check's own test file records: a gate that prints FAIL and exits
0 manufactures a pass for every wrapper above it.

The three cases are the three states the checker is allowed to reach. The
ground truth copied into a run directory passes. The same copy with ONE
historical transaction moved to the new store reads critical integrity FAIL,
which is the class section 28 of the morning steering calls
HISTORICAL REASSIGNMENT. An empty run directory exits 3, so an unexecuted
scenario can never be mistaken for a green one.

Fixtures are copies written to a fresh temp directory per test and torn down
after; nothing here writes into the real benchmark tree.
"""
import contextlib
import io
import json
import os
import shutil
import tempfile
import unittest

import jbeq_e2e_check as jc

SCENARIO = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    "benchmarks", "jbeq", "mdm", "e2e-001")

# The ground truth file that stands in for each run artefact of the same
# role. handover.ja.md has a section skeleton on the ground truth side; the
# checker asks it for the eleven sections, not for prose.
COPIES = [
    ("expected_golden.csv", "golden.csv"),
    ("expected_links.csv", "links.csv"),
    ("expected_mapping.json", "mapping.json"),
    ("expected_decisions.json", "decisions.json"),
    ("expected_reconciliation.json", "reconciliation.json"),
    ("expected_handover.ja.md", "handover.ja.md"),
]


def run_main(*args):
    """Return (exit_code, stdout) for jbeq_e2e_check.main(args)."""
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        code = jc.main(list(args))
    return code, buf.getvalue()


class FixtureCase(unittest.TestCase):
    """A fresh temp run directory per test, plus the real ground truth."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.run_dir = os.path.join(self._tmp.name, "run")
        os.makedirs(self.run_dir)
        self.truth = os.path.join(SCENARIO, "ground-truth")
        self.source = os.path.join(SCENARIO, "data")

    def populate(self):
        for src, dst in COPIES:
            shutil.copy(os.path.join(self.truth, src),
                        os.path.join(self.run_dir, dst))

    def check(self):
        return run_main(self.run_dir, "--ground-truth", self.truth,
                        "--fixture", self.source)


class Verdicts(FixtureCase):

    def test_ground_truth_copied_into_a_run_directory_passes(self):
        self.populate()
        code, out = self.check()
        self.assertEqual(code, 0, out)
        self.assertIn("critical integrity: PASS", out)
        self.assertIn("jbeq-mdm e2e: PASS", out)
        self.assertNotIn("FAIL", out)
        self.assertNotIn("NO-DATA", out)

    def test_one_reassigned_historical_transaction_fails_critical_integrity(self):
        """The closed store S002 loses one of its three transactions to the
        new store S004, which is exactly what the requirement forbids."""
        self.populate()
        path = os.path.join(self.run_dir, "reconciliation.json")
        with open(path, encoding="utf-8") as fh:
            recon = json.load(fh)
        self.assertEqual(recon["transactions_by_store"]["S002"], 3)
        recon["transactions_by_store"]["S002"] = 2
        recon["transactions_by_store"]["S004"] = 1
        recon["transaction_rows_reassigned"] = 1
        with open(path, "w", encoding="utf-8") as fh:
            json.dump(recon, fh, ensure_ascii=False, indent=1)
        code, out = self.check()
        self.assertEqual(code, 1, out)
        self.assertIn("critical integrity: FAIL", out)
        self.assertIn("historical reassignment", out)

    def test_empty_run_directory_exits_three(self):
        code, out = self.check()
        self.assertEqual(code, 3, out)
        self.assertIn("NO-DATA", out)
        self.assertNotIn(": PASS", out)


class CriticalClasses(FixtureCase):
    """The other three critical classes, each driven backwards once. A
    control nobody drove backwards is a claim, not a control."""

    def test_a_false_merge_fails(self):
        self.populate()
        path = os.path.join(self.run_dir, "golden.csv")
        with open(path, encoding="utf-8") as fh:
            lines = fh.readlines()
        # Drop the store customer C002, the shape a merge into its parent
        # C001 would leave behind.
        kept = [ln for ln in lines if not ln.startswith("C002,")]
        self.assertEqual(len(kept), len(lines) - 1)
        with open(path, "w", encoding="utf-8") as fh:
            fh.writelines(kept)
        code, out = self.check()
        self.assertEqual(code, 1, out)
        self.assertIn("critical integrity: FAIL", out)
        self.assertIn("false merge", out)

    def test_a_reversed_hierarchy_fails(self):
        self.populate()
        path = os.path.join(self.run_dir, "links.csv")
        with open(path, encoding="utf-8") as fh:
            text = fh.read()
        # Swap parent and child on the first link: the store becomes the
        # parent of its own corporate record.
        text = text.replace("C001,C002,", "C002,C001,", 1)
        with open(path, "w", encoding="utf-8") as fh:
            fh.write(text)
        code, out = self.check()
        self.assertEqual(code, 1, out)
        self.assertIn("critical integrity: FAIL", out)
        self.assertIn("hierarchy reversal", out)

    def test_a_survivorship_precedence_violation_fails(self):
        self.populate()
        path = os.path.join(self.run_dir, "golden.csv")
        with open(path, encoding="utf-8") as fh:
            text = fh.read()
        # The register's name overwritten by the sales facing name, the
        # precedence the requirement reverses.
        text = text.replace("株式会社青葉ホールディングス,アオバHD",
                            "アオバHD,アオバHD", 1)
        with open(path, "w", encoding="utf-8") as fh:
            fh.write(text)
        code, out = self.check()
        self.assertEqual(code, 1, out)
        self.assertIn("critical integrity: FAIL", out)
        self.assertIn("survivorship precedence", out)


class ArtefactVerdicts(FixtureCase):

    def test_a_missing_artefact_reads_no_data_and_never_passes(self):
        self.populate()
        os.remove(os.path.join(self.run_dir, "links.csv"))
        code, out = self.check()
        self.assertEqual(code, 1, out)
        self.assertIn("links.csv: NO-DATA", out)

    def test_an_unknown_rule_downgraded_to_decided_fails(self):
        """The two open questions are the point of the scenario. A run that
        answers one of them on its own authority must not score green."""
        self.populate()
        path = os.path.join(self.run_dir, "decisions.json")
        with open(path, encoding="utf-8") as fh:
            decisions = json.load(fh)
        for rule in decisions["rules"]:
            if rule["id"] == "R10":
                self.assertEqual(rule["status"], "UNKNOWN")
                rule["status"] = "DECIDED"
        with open(path, "w", encoding="utf-8") as fh:
            json.dump(decisions, fh, ensure_ascii=False, indent=1)
        code, out = self.check()
        self.assertEqual(code, 1, out)
        self.assertIn("decisions.json: FAIL", out)
        self.assertIn("R10", out)

    def test_a_handover_missing_one_section_names_it(self):
        self.populate()
        path = os.path.join(self.run_dir, "handover.ja.md")
        with open(path, encoding="utf-8") as fh:
            text = fh.read()
        text = text.replace("未解決の業務上の疑問", "その他")
        with open(path, "w", encoding="utf-8") as fh:
            fh.write(text)
        code, out = self.check()
        self.assertEqual(code, 1, out)
        self.assertIn("handover sections: FAIL", out)
        self.assertIn("未解決の業務上の疑問", out)


if __name__ == "__main__":
    unittest.main(verbosity=2)

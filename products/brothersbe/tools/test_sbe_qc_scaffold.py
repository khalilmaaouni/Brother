#!/usr/bin/env python3
"""Tests for tools/sbe_qc_scaffold.py (pilot feedback item P13).

Run: python3 tools/test_sbe_qc_scaffold.py

The bar this project holds every generator to: an honest absence must say
NO-DATA and never render as a confident, empty-looking scaffold. The
calibration test that matters here hollows a real behaviour table (copies
it, deletes its rows, keeps the header) and asserts `cases` reports NO-DATA
rather than a silent "0 cases generated" that could be mistaken for "every
case is covered".
"""
import os
import subprocess
import sys
import tempfile
import unittest
import importlib.util

HERE = os.path.dirname(os.path.abspath(__file__))
TOOL = os.path.join(HERE, "sbe_qc_scaffold.py")

_spec = importlib.util.spec_from_file_location(
    "sbe_qc_scaffold", TOOL)
qc = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(qc)

FIELDS_TABLE = ("id", "starting point", "trigger", "required outcome", "proof")

REAL_TABLE = """# 08. Behaviour table

## Rules

| ID | Starting point | Trigger | Required outcome | Proof |
|---|---|---|---|---|
| B1 | A checkout with a valid payment method | The customer submits checkout | An order is created with status placed | Integration test: submit checkout, assert the order row exists |
| B2 | An order that has been placed | The warehouse does not acknowledge within five minutes | The order service retries and raises an alert | Failover drill: stop the consumer, confirm the alert fires |

## What this does not do
This does not run the checks.
"""

TEMPLATE_TABLE = """# 08. Behaviour table

<!-- SBE-TEMPLATE-UNFILLED 08-behaviour: this section is still the shipped example.
     Replace it with your own design, then delete this comment. -->

## Rules

| ID | Starting point | Trigger | Required outcome | Proof |
|---|---|---|---|---|
| B1 | A checkout with a valid payment method | The customer submits checkout | An order is created with status "placed" | Integration test |
"""


def write_text(path, text):
    d = os.path.dirname(path)
    if d and not os.path.isdir(d):
        os.makedirs(d)
    with open(path, "w") as fh:
        fh.write(text)


def strip_table_rows(text):
    """A real table with its header kept and every row deleted: the
    reinjection fixture the honesty gate demands (a header-with-zero-rows
    file, not a made-up one)."""
    out, in_table = [], False
    for line in text.splitlines():
        s = line.strip()
        if s.startswith("| ID |"):
            in_table = True
            out.append(line)
            continue
        if in_table and s.startswith("|") and set(s.strip("|")) <= set("-| "):
            out.append(line)
            continue
        if in_table and s.startswith("|"):
            continue  # the row itself: dropped
        in_table = False
        out.append(line)
    return "\n".join(out) + "\n"


class ScaffoldCase(unittest.TestCase):

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.root = self._tmp.name

    def tearDown(self):
        self._tmp.cleanup()

    def behaviour_path(self):
        return os.path.join(self.root, "08-behaviour.md")

    def run_cases(self):
        return subprocess.run(
            [sys.executable, TOOL, "cases", "--root", self.root],
            capture_output=True, text=True, timeout=60)

    def run_finding(self, starting_point="A user opens the export screen",
                    trigger="They click export with zero rows selected",
                    required_outcome="The button stays disabled and no file downloads",
                    proof="Exploratory: open the screen, select nothing, click export"):
        return subprocess.run(
            [sys.executable, TOOL, "finding", "--root", self.root,
             "--starting-point", starting_point, "--trigger", trigger,
             "--required-outcome", required_outcome, "--proof", proof],
            capture_output=True, text=True, timeout=60)

    def run_stale(self, sheet_path):
        return subprocess.run(
            [sys.executable, TOOL, "stale", "--root", self.root, "--sheet", sheet_path],
            capture_output=True, text=True, timeout=60)


class TestCasesFromARealTable(ScaffoldCase):

    def test_one_draft_case_per_row_and_the_count_matches(self):
        write_text(self.behaviour_path(), REAL_TABLE)
        proc = self.run_cases()
        self.assertEqual(proc.returncode, 0, proc.stderr)
        out = proc.stdout
        self.assertIn("2 row(s) in the table, 2 draft test case(s)", out)
        self.assertIn("Draft test case for B1", out)
        self.assertIn("Draft test case for B2", out)
        self.assertIn("submit checkout, assert the order row exists", out,
                      "the Proof column's own text must carry into the draft")


class TestNoDataIsNeverASilentPass(ScaffoldCase):

    def test_no_file_at_all_is_no_data_named_by_reason(self):
        proc = self.run_cases()
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertIn("NO-DATA", proc.stdout)
        self.assertIn("no 08-behaviour.md", proc.stdout)

    def test_a_hollowed_table_is_no_data_not_a_confident_empty_scaffold(self):
        """The reinjection calibration test: copy a real table, delete every
        row, keep the header. This must never read as "0 cases needed"."""
        write_text(self.behaviour_path(), strip_table_rows(REAL_TABLE))
        proc = self.run_cases()
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertIn("NO-DATA", proc.stdout)
        self.assertIn("zero rows", proc.stdout)
        self.assertNotIn("Draft test case", proc.stdout)

    def test_the_shipped_template_marker_is_no_data_not_the_demo_cases(self):
        write_text(self.behaviour_path(), TEMPLATE_TABLE)
        proc = self.run_cases()
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertIn("NO-DATA", proc.stdout)
        self.assertIn("shipped example", proc.stdout)
        self.assertNotIn("Draft test case", proc.stdout)

    def test_a_malformed_row_is_fail_not_a_silent_skip(self):
        broken = REAL_TABLE.replace(
            "| B2 | An order that has been placed | The warehouse does not "
            "acknowledge within five minutes | The order service retries "
            "and raises an alert | Failover drill: stop the consumer, "
            "confirm the alert fires |",
            "| B2 | only three cells | broken |")
        write_text(self.behaviour_path(), broken)
        proc = self.run_cases()
        self.assertEqual(proc.returncode, 1, proc.stdout)
        self.assertIn("FAIL", proc.stdout)
        self.assertIn("could not be read", proc.stdout)


class TestFindingAddsARow(ScaffoldCase):

    def test_a_finding_is_appended_after_the_last_row_with_the_next_id(self):
        write_text(self.behaviour_path(), REAL_TABLE)
        proc = self.run_finding()
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertIn("PASS", proc.stdout)
        self.assertIn("B3", proc.stdout)
        text = open(self.behaviour_path()).read()
        lines = [l for l in text.splitlines() if l.strip().startswith("|")]
        self.assertEqual(lines[-1].split("|")[1].strip(), "B3",
                         "the new row must be the LAST row, not inserted anywhere else")
        self.assertIn("export screen", text)
        # The dossier's own parser must still read the file cleanly after
        # the append: reusing _behaviour_rows means this cannot drift.
        rows, malformed = qc._behaviour_rows(text)
        self.assertEqual(malformed, [])
        self.assertEqual(len(rows), 3)

    def test_a_finding_can_start_an_otherwise_empty_real_table(self):
        write_text(self.behaviour_path(), strip_table_rows(REAL_TABLE))
        proc = self.run_finding()
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertIn("B1", proc.stdout)
        rows, malformed = qc._behaviour_rows(open(self.behaviour_path()).read())
        self.assertEqual(malformed, [])
        self.assertEqual(len(rows), 1)

    def test_an_empty_field_is_refused_not_written_as_a_blank_row(self):
        write_text(self.behaviour_path(), REAL_TABLE)
        proc = self.run_finding(trigger="   ")
        self.assertEqual(proc.returncode, 1, proc.stdout)
        self.assertIn("FAIL", proc.stdout)
        self.assertIn("trigger", proc.stdout)
        text = open(self.behaviour_path()).read()
        self.assertEqual(text, REAL_TABLE, "a refused finding must not touch the file")

    def test_no_table_to_append_to_is_refused_by_name(self):
        proc = self.run_finding()
        self.assertEqual(proc.returncode, 1, proc.stdout)
        self.assertIn("cannot add a row", proc.stdout)

    def test_a_pipe_in_a_field_is_escaped_so_the_table_still_parses(self):
        write_text(self.behaviour_path(), REAL_TABLE)
        proc = self.run_finding(required_outcome="Column A | Column B stay separate")
        self.assertEqual(proc.returncode, 0, proc.stderr)
        text = open(self.behaviour_path()).read()
        rows, malformed = qc._behaviour_rows(text)
        self.assertEqual(malformed, [], "an unescaped pipe would corrupt the table's columns")
        self.assertEqual(len(rows), 3)


class TestStaleDetection(ScaffoldCase):
    """H6: a generated sheet is stamped with the id set it was drafted from,
    and a later `stale` check diffs that stamp against 08-behaviour.md's
    CURRENT rows."""

    def sheet_path(self):
        return os.path.join(self.root, "sheet.md")

    def generate_sheet(self):
        write_text(self.behaviour_path(), REAL_TABLE)
        proc = subprocess.run(
            [sys.executable, TOOL, "cases", "--root", self.root, "--out", self.sheet_path()],
            capture_output=True, text=True, timeout=60)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        return proc

    def test_a_freshly_generated_sheet_is_fresh(self):
        self.generate_sheet()
        proc = self.run_stale(self.sheet_path())
        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
        self.assertIn("FRESH", proc.stdout)

    def test_a_behaviour_row_added_after_generation_reports_stale_naming_it(self):
        """The done-check this hole was built against, verbatim: add a
        behaviour row after generating, the sheet reports stale naming that
        id."""
        self.generate_sheet()
        finding = self.run_finding()
        self.assertEqual(finding.returncode, 0, finding.stdout)
        self.assertIn("B3", finding.stdout)

        proc = self.run_stale(self.sheet_path())
        self.assertEqual(proc.returncode, 1, proc.stdout + proc.stderr)
        self.assertIn("STALE", proc.stdout)
        self.assertIn("B3", proc.stdout)

    def test_a_sheet_with_no_stamp_is_no_data_not_a_false_fresh(self):
        write_text(self.behaviour_path(), REAL_TABLE)
        write_text(self.sheet_path(), "a hand-written sheet with no stamp at all\n")
        proc = self.run_stale(self.sheet_path())
        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
        self.assertIn("NO-DATA", proc.stdout)
        self.assertNotIn("FRESH", proc.stdout)

    def test_a_missing_sheet_file_is_fail_not_a_silent_pass(self):
        write_text(self.behaviour_path(), REAL_TABLE)
        proc = self.run_stale(os.path.join(self.root, "does-not-exist.md"))
        self.assertEqual(proc.returncode, 1, proc.stdout)
        self.assertIn("FAIL", proc.stdout)


if __name__ == "__main__":
    unittest.main()

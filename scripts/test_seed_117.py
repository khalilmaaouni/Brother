#!/usr/bin/env python3
"""SEED17 (docs/plan/specs/SEED17.md): the 1.1.7 seed register is complete and honest.

One judge, seed_problems(text), over docs/plan/seeds/1.1.7/REGISTER.md:
  the five sections exist: MEASURED NIGHT, ISSUES, SPEED AND COST, PLANNING PIPELINE, WHAT 1.1.6 DID NOT FINISH;
  the ISSUES table is not empty, every row has six cells, a root cause naming a function or a file, and a test unit
  under scripts/ (test_*.py; a test under another tree is refused);
  every MEASURED NIGHT row with a figure in its minutes cell names a file in its source cell;
  no em or en dash, no credit line (Co-Authored-By, generated with, drafted by, written by, made with, produced by).
Anything unreadable (None, empty text, a number, a path outside the tree) reads NO-DATA, never a clean list.
Fixtures are strings; nothing is written. Python 3.9, standard library only.

  python3 -B scripts/test_seed_117.py
"""
import os
import re
import sys
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
REGISTER = os.path.join("docs", "plan", "seeds", "1.1.7", "REGISTER.md")
SECTIONS = ("MEASURED NIGHT", "ISSUES", "SPEED AND COST", "PLANNING PIPELINE", "WHAT 1.1.6 DID NOT FINISH")
DASHES = (chr(0x2014), chr(0x2013))   # em and en dash, spelled by code point so this file carries neither
CREDIT = re.compile(r"(?i)\b(co-authored-by|generated with|drafted by|written by|made with|produced by|authored by)\b")
TEST_FILE = re.compile(r"\bscripts/(?:loop/)?test_[a-z0-9_]+\.py\b")
ANY_TEST = re.compile(r"\btest_[a-z0-9_]+\.py\b")
NAMES_CODE = re.compile(r"[A-Za-z0-9_]+\.(?:py|sh|c|json|md)\b|[A-Za-z_][A-Za-z0-9_]*\(\)")
SOURCE_FILE = re.compile(r"\.(?:csv|log|txt|json|jsonl|md|out)\b")
DIGIT = re.compile(r"[0-9]")
ISSUE_CELLS = 6
MEASURED_CELLS = 5


def register_text(path):
    """The register's text, or a NO-DATA string: no path, a path outside the tree, unreadable or empty."""
    if not isinstance(path, str) or not path.strip():
        return "NO-DATA: no register path"
    full = os.path.realpath(path if os.path.isabs(path) else os.path.join(ROOT, path))
    if not full.startswith(os.path.realpath(ROOT) + os.sep):
        return "NO-DATA: %s is outside this tree" % path
    try:
        with open(full, "rb") as fh:
            text = fh.read().decode("utf-8")
    except (OSError, UnicodeDecodeError) as exc:
        return "NO-DATA: %s unreadable (%s)" % (path, type(exc).__name__)
    return text if text.strip() else "NO-DATA: %s is empty" % path


def sections(text):
    """{heading: body} for every '## ' heading; text before the first heading is dropped."""
    out, name, body = {}, None, []
    for line in text.splitlines():
        if line.startswith("## "):
            if name is not None:
                out[name] = "\n".join(body)
            name, body = line[3:].strip(), []
        elif name is not None:
            body.append(line)
    if name is not None:
        out[name] = "\n".join(body)
    return out


def table_rows(body):
    """Body rows of the first table in a section: lines starting with '|', minus the header and the separator."""
    rows = []
    for line in body.splitlines():
        if not line.lstrip().startswith("|"):
            continue
        cells = [c.strip() for c in line.strip().strip("|").split("|")]
        if all(re.fullmatch(r":?-{3,}:?", c) for c in cells):
            continue
        rows.append(cells)
    return rows[1:]   # the header


def issue_rows(text):
    """The ISSUES table's body rows as lists of cells; [] when the section or the table is missing."""
    if not isinstance(text, str):
        return []
    return table_rows(sections(text).get("ISSUES", ""))


def seed_problems(text):
    """Every way the register falls short, one string each; a NO-DATA string for an unreadable input."""
    if not isinstance(text, str) or not text.strip():
        return ["NO-DATA: the register text is %s" % ("empty" if isinstance(text, str) else type(text).__name__)]
    if text.startswith("NO-DATA:"):
        return [text]
    problems = []
    found = sections(text)
    for name in SECTIONS:
        if name not in found:
            problems.append("missing section: ## %s" % name)
    rows = issue_rows(text)
    if "ISSUES" in found and not rows:
        problems.append("ISSUES: the table is empty")
    for row in rows:
        label = row[0] if row else "?"
        if len(row) != ISSUE_CELLS:
            problems.append("ISSUES %s: %d cells, %d expected" % (label, len(row), ISSUE_CELLS))
            continue
        if not NAMES_CODE.search(row[2]):
            problems.append("ISSUES %s: the root cause names no function or file" % label)
        if not TEST_FILE.search(row[3]):
            if ANY_TEST.search(row[3]):
                problems.append("ISSUES %s: the test unit is not under scripts/" % label)
            else:
                problems.append("ISSUES %s: no test unit (test_*.py)" % label)
    for row in table_rows(found.get("MEASURED NIGHT", "")):
        label = row[0][:40] if row else "?"
        if len(row) != MEASURED_CELLS:
            problems.append("MEASURED NIGHT %r: %d cells, %d expected" % (label, len(row), MEASURED_CELLS))
            continue
        if DIGIT.search(row[3]) and not SOURCE_FILE.search(row[4]):
            problems.append("MEASURED NIGHT %r: a figure with no cited source file" % label)
    for n, line in enumerate(text.splitlines(), 1):
        if any(d in line for d in DASHES):
            problems.append("dash at line %d" % n)
        if CREDIT.search(line):
            problems.append("credit at line %d" % n)
    return problems


GOOD = """# fixture

## MEASURED NIGHT

| phase | start JST | end JST | minutes | source |
| --- | --- | --- | --- | --- |
| launch | 00h55 | 00h58 | 3 | redeploy-launch1.txt lines 784 to 789 |
| gate a2 | NO-DATA | 00h54 | NO-DATA (no duration printed) | gate-plan116-a2.txt mtime |

## ISSUES

| id | symptom (evidence path) | root cause (function or boundary) | failing test that would have caught it | fix | order |
| --- | --- | --- | --- | --- | --- |
| I1 | the seat refused writes (loop.log line 65) | scripts/loop/native_worker.py seat_root() defaults under ~/.claude | scripts/loop/test_native_worker.py NEW case test_a_seat_that_cannot_write_refuses_the_launch | default outside ~/.claude | A |
| I2 | the gate row read NO-DATA (gate.txt) | scripts/required_fast.sh line 235 writes the raw id | scripts/test_gate_ledger_cut_id.py | one writer | C |

## SPEED AND COST

1. gates: 16 min each.

## PLANNING PIPELINE

21 of 37.

## WHAT 1.1.6 DID NOT FINISH

- S2 OPEN.
"""


def _swap(text, old, new):
    assert old in text, old
    return text.replace(old, new, 1)


class TheRealSeed(unittest.TestCase):
    def test_the_real_seed_is_complete(self):
        # docs/plan is not part of the public export: where the register is not shipped there is
        # nothing to read, a named skip (NO-DATA for this one fact), never a pass or an error.
        if not os.path.exists(os.path.join(ROOT, REGISTER)):
            self.skipTest("%s is not shipped in this tree (export tree)" % REGISTER)
        text = register_text(REGISTER)
        self.assertFalse(text.startswith("NO-DATA"), text[:120])
        self.assertEqual(seed_problems(text), [])
        self.assertGreaterEqual(len(issue_rows(text)), 6, "the known rows exist (SEED17 section 1)")


class TheJudge(unittest.TestCase):
    def test_the_fixture_is_clean(self):
        self.assertEqual(seed_problems(GOOD), [])
        self.assertEqual(len(issue_rows(GOOD)), 2)

    def test_a_missing_section_is_refused(self):
        for name in SECTIONS:
            problems = seed_problems(_swap(GOOD, "## %s\n" % name, "## %s gone\n" % name))
            self.assertIn("missing section: ## %s" % name, problems)

    def test_an_empty_root_cause_is_refused(self):
        text = _swap(GOOD, "scripts/loop/native_worker.py seat_root() defaults under ~/.claude", "")
        self.assertEqual(seed_problems(text), ["ISSUES I1: the root cause names no function or file"])
        text = _swap(GOOD, "scripts/loop/native_worker.py seat_root() defaults under ~/.claude", "it was slow")
        self.assertEqual(seed_problems(text), ["ISSUES I1: the root cause names no function or file"])

    def test_a_row_without_a_test_unit_is_refused(self):
        text = _swap(GOOD, "scripts/test_gate_ledger_cut_id.py", "a manual check")
        self.assertEqual(seed_problems(text), ["ISSUES I2: no test unit (test_*.py)"])

    def test_a_test_under_another_tree_is_refused(self):
        text = _swap(GOOD, "scripts/test_gate_ledger_cut_id.py", "products/x/tests/test_gate_ledger_cut_id.py")
        self.assertEqual(seed_problems(text), ["ISSUES I2: the test unit is not under scripts/"])

    def test_a_corrupt_row_and_an_empty_table_are_refused(self):
        text = _swap(GOOD, "| I2 | the gate row read NO-DATA (gate.txt) | scripts/required_fast.sh line 235 writes the raw id | scripts/test_gate_ledger_cut_id.py | one writer | C |",
                     "| I2 | three | cells |")
        self.assertEqual(seed_problems(text), ["ISSUES I2: 3 cells, 6 expected"])
        empty = GOOD
        for row in issue_rows(GOOD):
            empty = _swap(empty, "| " + " | ".join(row) + " |\n", "")
        self.assertEqual(seed_problems(empty), ["ISSUES: the table is empty"])

    def test_a_measured_figure_without_a_source_is_refused(self):
        text = _swap(GOOD, "redeploy-launch1.txt lines 784 to 789", "the launch log")
        self.assertEqual(seed_problems(text), ["MEASURED NIGHT 'launch': a figure with no cited source file"])
        text = _swap(GOOD, "gate-plan116-a2.txt mtime", "a gate file")   # a NO-DATA row cites nothing and passes
        self.assertEqual(seed_problems(text), [])

    def test_no_dash_no_credit(self):
        self.assertEqual(seed_problems(GOOD + "\nfast " + chr(0x2014) + " slow\n"), ["dash at line %d" % (GOOD.count("\n") + 2)])
        self.assertEqual(seed_problems(GOOD + "\n2 " + chr(0x2013) + " 3\n"), ["dash at line %d" % (GOOD.count("\n") + 2)])
        self.assertEqual(seed_problems(GOOD + "\nDrafted by a model\n"), ["credit at line %d" % (GOOD.count("\n") + 2)])

    def test_hostile_inputs_read_no_data(self):
        for bad in (None, "", "   ", 5):
            problems = seed_problems(bad)
            self.assertEqual(len(problems), 1, bad)
            self.assertTrue(problems[0].startswith("NO-DATA:"), problems)
        self.assertTrue(register_text("/etc/hosts").startswith("NO-DATA:"))
        self.assertTrue(register_text(os.path.join("docs", "plan", "seeds", "1.1.7", "NOPE.md")).startswith("NO-DATA:"))
        self.assertTrue(register_text(None).startswith("NO-DATA:"))
        self.assertEqual(seed_problems(register_text(None)), [register_text(None)])
        self.assertEqual(issue_rows(None), [])


if __name__ == "__main__":
    sys.exit(unittest.main(verbosity=1))

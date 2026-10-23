#!/usr/bin/env python3
"""SEED17 (docs/plan/specs/SEED17.md): the 1.1.7 seed register is complete and honest.

One judge, seed_problems(text), over docs/plan/seeds/1.1.7/REGISTER.md:
  the five sections exist: MEASURED NIGHT, ISSUES, SPEED AND COST, PLANNING PIPELINE, WHAT 1.1.6 DID NOT FINISH;
  the ISSUES table is not empty, every row has six cells, a root cause naming a function or a file, and a test unit
  under scripts/ (test_*.py; a test under another tree is refused);
  every MEASURED NIGHT row with a figure in its minutes cell names a file in its source cell;
  no em or en dash, no credit line (Co-Authored-By, generated with, drafted by, written by, made with, produced by).
Anything unreadable (None, empty text, a number, a path outside the tree) reads NO-DATA, never a clean list.
The owner's order of 2026-10-18 10h55 added four files beside the register (CUT-SPEED.md, ISSUES-1.1.6.md,
LOOP-CERTIFICATION.md, PEAK-PERFORMANCE.md): file_problems(name, text) checks each one's required sections, the
merged ISSUES table (seven cells a row, a root cause naming code, a test unit under scripts/), and
register_points_to(text) that the register names them. The owner's delegation order of 2026-10-18 added DELEGATION.md
(twelve required sections; the last is DECISIONS since the owner delegated the seed's six class 2 decisions the same
day, so a file that still carries an UNRESOLVED DECISIONS section, or a decision record missing one of the five fields
of the delegation law, is refused) and UNITS-1.1.7.json: unit_problems(doc) checks unique ids, resolving acyclic depends_on,
one runnable scripts/ acceptance command, a proof on both hosts, a known epic and rollout stage, and the integration
acceptance unit with its negative case (the prototype alone fails it) and the records it reads back. Where docs/plan
is not shipped every real file case skips by name.
The council of 2026-10-18 (five members, eleven points) added STABILIZE.md, the plan of record in the seven section shape
plus a COUNCIL section (every member named, the rule stated, one record per point with its BALLOT, OUTCOME and CHOSEN
lines, the five decision fields on a MAJORITY or SPLIT record), and COUNCIL-1.1.7.json: council_problems(doc) recomputes
every point's outcome from its five ballots under the rule (4 or 5 of 5 CONSENSUS, 3 of 5 MAJORITY, less SPLIT, ABSTAIN
and NO-DATA counting for no option), so a hand edited outcome, a chosen option that is not the leading one, a missing
ballot, a dissent list that does not match, or a MAJORITY without its five fields is refused. unit_problems(doc) gained the
lane field, the certification gate unit, and the merged, deferred and dropped lists, each entry with its reason.
The owner's ruling of 2026-10-18 on STABILIZE.md OPEN ITEMS 1 added two checks: an OPEN ITEMS record marked RULED carries
the ruling quoted and dated and the five decision fields (an open one keeps its QUESTION, RECOMMENDED, ALTERNATIVES and
OWNER lines), and COUNCIL-1.1.7.json's owner_amendments entries each name a point, the ruling, the option, the date and
what they amend, beside ballots that are still recomputed; item 1's quoted words equal the D2 amendment's.
The cut council of 2026-10-18 evening (eight points K1 to K8 on the owner's complaint that the cut is too long, too many
actions and repeats the whole process on a failure) added three checks: COUNCIL-1.1.7.json carries a second block,
cut_council, judged by council_problems under the same rule with the first council's eleven points untouched;
CUT-SPEED.md carries the section CUT IN MINUTES with a numeric owner facing target on one line (cut_target_of); and
UNITS-1.1.7.json carries CS1 to CS13 each with one runnable scripts/test_*.py acceptance, CS11 behind every other CS
unit and CF1 behind CS11 and CS12 (cut_unit_problems; CS13 is the second review's unit of 2026-10-18 late evening).
The owner's ruling of 2026-10-19 on STABILIZE.md OPEN ITEMS 4 (option B, the mutation rows run as units land) added
three checks: item 4 is RULED with his words (RULING_4), COUNCIL-1.1.7.json cut_council carries his amendment of K5
beside K5's untouched ballots, and UNITS-1.1.7.json carries CS14, the per landing mutation unit, on the loop lane behind
A2 and CS1, owning release_note_perturb.py, perturb_pool.py and its own acceptance test, with CS11 and CF1 behind it;
CUT-SPEED.md's WARM PROOF line names the first rehearsal of a candidate as the certified warm run.
Fixtures are strings; nothing is written. Python 3.9, standard library only.

  python3 -B scripts/test_seed_117.py
"""
import json
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


#: The four files the owner's order of 2026-10-18 10h55 added beside the register (REGISTER.md FILES OF THE SEED),
#: each with the sections it must carry. ISSUES-1.1.6.md's ISSUES table is the merged one: seven cells a row.
SEED_DIR = os.path.join("docs", "plan", "seeds", "1.1.7")
ISSUES_FILE = "ISSUES-1.1.6.md"
#: The cut council of 2026-10-18 evening: its design lives in CUT-SPEED.md under this section, which must carry the
#: figure the owner feels on one line, "- OWNER WAIT: at most N s" (K1: from the typed app code to the public tag and
#: the release page); a section with no such line is a budget nobody can hold the cut to.
CUT_FILE = "CUT-SPEED.md"
CUT_SECTION = "CUT IN MINUTES (council of 2026-10-18 evening)"
CUT_TARGET = re.compile(r"(?m)^- OWNER WAIT: at most (\d+) s\b")
FILE_SECTIONS = {
    CUT_FILE: ("MEASURED PHASES", "THE TEN TIMES BUDGET", "ACCELERATIONS", "THE HONEST FLOOR",
               "RECONCILIATION WITH ASTRA", CUT_SECTION),
    ISSUES_FILE: ("SOURCES AND DEDUPLICATION", "ISSUES", "REGRESSION GUARD"),
    "LOOP-CERTIFICATION.md": ("WHAT CERTIFIED MEANS", "ACCEPTANCE MATRIX", "FIELD TRIAL"),
    "PEAK-PERFORMANCE.md": ("BY CONSTRUCTION", "BENCHMARK DESIGN", "WHAT COUNTS AS BEATING"),
    # the owner's delegation order of 2026-10-18 (relayed twice): the design mapped onto the one Brother path
    "DELEGATION.md": ("THE ONE PATH", "STAGE BY STAGE", "CREDENTIAL BROKER", "PRIVACY GATE", "LIFECYCLE",
                      "WRITE WORKERS", "THE THIN ALIAS", "RELIABILITY TARGETS", "FAILURE INJECTION AND THE BENCHMARK",
                      "ROLLOUT", "REVIEW OF THE INDEPENDENT READING",
                      "DECISIONS (owner delegated 2026-10-18 to Fable and Astra)"),
}
MERGED_ISSUE_CELLS = 7
#: The plan of record after the council of 2026-10-18 (STABILIZE.md): the seven sections the owner's order named, then the
#: COUNCIL section that names the five members, states the rule and records every point. The rule is checked in words here
#: (RULE_WORDS) and in numbers over COUNCIL-1.1.7.json (outcome_of below).
STABILIZE_FILE = "STABILIZE.md"
PLAN_SECTIONS = ("DIAGNOSIS", "KEEP", "ARCHITECTURE", "USER READINESS", "SCOPE", "CERTIFICATION", "RISKS", "OPEN ITEMS",
                 "COUNCIL")
FILE_SECTIONS[STABILIZE_FILE] = PLAN_SECTIONS
MEMBERS = ("fable", "opus", "astra", "deepseek", "jev")
NON_VOTES = ("ABSTAIN", "NO-DATA")
CONSENSUS_MIN, MAJORITY_MIN = 4, 3
RULE_WORDS = ("4 or 5 of 5", "3 of 5")
COUNCIL_LINES = ("BALLOT", "OUTCOME", "CHOSEN")
#: STABILIZE.md OPEN ITEMS: a numbered "### N. question" record is either open (it carries the lines below) or marked
#: RULED in its heading, and then it carries the owner's ruling verbatim in quotes with its date on a "- RULING:" line
#: and the five decision fields of the delegation law. The owner ruled item 1 on 2026-10-18 (about 18h05 JST, in the
#: question UI after a rendered decision screen); his choice, verbatim:
RULING_1 = "Merge now, switch at cut (Recommended)"
#: The owner's ruling on OPEN ITEMS 4, 2026-10-19 after midnight JST, in the question UI after a rendered decision screen,
#: verbatim: option B, the release note's mutation rows run as units land (CS14), an amendment of K5 (CONSENSUS A).
RULING_4 = "Yes, per landing (Recommended)"
#: The owner's ruling of 2026-10-19 on STABILIZE.md OPEN ITEMS 5 (the reviewed route), his words verbatim: his amendment
#: of D5 (the certificate judges the loop built remainder at 100 percent) and of D11 (by hand landings counted separately).
#: The owner's ruling of 2026-10-19 on STABILIZE.md OPEN ITEMS 6 (OPEN ITEMS 1 flipped to B), the one time code removed.
RULING_6 = "Here is the code activate now"
RULING_7 = "Finish the rest by hand and make sure the loop works fully next time"
RULING_8 = "Cut core now (Recommended)"
RULING_9 = "1.1.7 has to be the version where the loop starts to work perfectly I am tired of this"
OWNER_WORDS_10 = ('Figure out the blockers and remove them yourself', 'Hold for full proof (Recommended)', 'I shut down most of it just go', 'Start the work and then do a handover as you do not have enough tokens')
RULING_10 = 'Hold for full proof: the loop lands all 15 proof sub units, then one unattended run with zero unexplained HELDs, then the cut.'
RULING_5 = ("What needs to be landed by hand should be landed by hand the rest would then become 100% and we judge "
            "against it")
OPEN_ITEM_LINES = ("QUESTION", "RECOMMENDED", "ALTERNATIVES", "OWNER")
RULING_LINE = re.compile(r'(?m)^- RULING: "([^"]+)"[^\n]*\b(\d{4}-\d{2}-\d{2})\b')
#: The decision records of DELEGATION.md. The owner delegated the seed's six class 2 decisions on 2026-10-18, so the
#: section is DECISIONS and a file that still carries UNRESOLVED DECISIONS is refused. A record is a "### N. title"
#: block, and each carries the five fields of the delegation law, one per "- FIELD: text" line; a label with nothing
#: after it is a missing field.
DELEGATION_FILE = "DELEGATION.md"
DECISIONS_SECTION = FILE_SECTIONS[DELEGATION_FILE][-1]
UNRESOLVED_SECTION = "UNRESOLVED DECISIONS"
DECISION_FIELDS = ("OPTION CHOSEN", "ALTERNATIVES REJECTED", "EVIDENCE", "COST OF BEING WRONG", "FLIP CONDITION")


def decision_records(body):
    """[(label, text)] for every '### ' record in a DECISIONS body, in order; [] for no record or a non text body."""
    if not isinstance(body, str):
        return []
    records = []
    for chunk in re.split(r"(?m)^### ", body)[1:]:
        label, _, text = chunk.partition("\n")
        records.append((label.strip(), text))
    return records


def decision_problems(name, found):
    """Every way the decisions of DELEGATION.md fall short, each prefixed with the file's name: the UNRESOLVED
    DECISIONS section still present, a DECISIONS section with no record, a record missing one of the five fields."""
    problems = []
    if UNRESOLVED_SECTION in found:
        problems.append("%s: ## %s still present, every decision is recorded under ## %s"
                        % (name, UNRESOLVED_SECTION, DECISIONS_SECTION))
    records = decision_records(found.get(DECISIONS_SECTION, ""))
    if DECISIONS_SECTION in found and not records:
        problems.append("%s DECISIONS: no decision record (### N. title)" % name)
    for label, text in records:
        number = label.split(".", 1)[0]
        for field in DECISION_FIELDS:
            if not re.search(r"(?m)^- %s: \S" % re.escape(field), text):
                problems.append("%s decision %s: missing %s" % (name, number, field))
    return problems


def plan_council_problems(name, found):
    """Every way STABILIZE.md's COUNCIL section falls short, each prefixed with the file's name: a member not named, the
    rule not stated in words, no point record, a record missing its BALLOT, OUTCOME or CHOSEN line, a MAJORITY or SPLIT
    record missing one of the five decision fields. A missing section is reported by the caller, not here."""
    body = found.get("COUNCIL")
    if body is None:
        return []
    problems = []
    low = body.lower()
    for member in MEMBERS:
        if member not in low:
            problems.append("%s COUNCIL: member %s not named" % (name, member))
    for words in RULE_WORDS:
        if words not in body:
            problems.append("%s COUNCIL: the rule does not say %r" % (name, words))
    records = decision_records(body)
    if not records:
        problems.append("%s COUNCIL: no point record (### Dn. question)" % name)
    for label, text in records:
        number = label.split(".", 1)[0]
        for line in COUNCIL_LINES:
            if not re.search(r"(?m)^- %s: \S" % line, text):
                problems.append("%s COUNCIL %s: missing %s" % (name, number, line))
        outcome = re.search(r"(?m)^- OUTCOME: ([A-Z-]+)", text)
        if outcome and outcome.group(1) in ("MAJORITY", "SPLIT"):
            for field in DECISION_FIELDS:
                if not re.search(r"(?m)^- %s: \S" % re.escape(field), text):
                    problems.append("%s COUNCIL %s: missing %s" % (name, number, field))
    return problems


def open_item_problems(name, found):
    """Every way STABILIZE.md's OPEN ITEMS records fall short, each prefixed with the file's name: an open item missing
    one of its lines, a RULED item without the ruling quoted and dated or missing one of the five decision fields, a
    ruling recorded on an item not marked RULED. A record whose label is not numbered (the findings list) is prose."""
    problems = []
    for label, text in decision_records(found.get("OPEN ITEMS", "")):
        number = label.split(".", 1)[0]
        if not number.isdigit():
            continue
        ruled = "RULED" in label
        if ruled:
            if not RULING_LINE.search(text):
                problems.append('%s OPEN ITEMS %s: RULED without the ruling quoted and dated (- RULING: "..." YYYY-MM-DD)'
                                % (name, number))
            for field in DECISION_FIELDS:
                if not re.search(r"(?m)^- %s: \S" % re.escape(field), text):
                    problems.append("%s OPEN ITEMS %s: RULED, missing %s" % (name, number, field))
        else:
            if re.search(r"(?m)^- RULING:", text):
                problems.append("%s OPEN ITEMS %s: a ruling recorded on an item not marked RULED" % (name, number))
            for line in OPEN_ITEM_LINES:
                if not re.search(r"(?m)^- %s: \S" % line, text):
                    problems.append("%s OPEN ITEMS %s: missing %s" % (name, number, line))
    return problems


def ruling_of(text, number):
    """The quoted ruling of OPEN ITEMS record `number` in STABILIZE.md's text, or None when it is not RULED."""
    if not isinstance(text, str):
        return None
    for label, body in decision_records(sections(text).get("OPEN ITEMS", "")):
        if label.split(".", 1)[0] == str(number) and "RULED" in label:
            m = RULING_LINE.search(body)
            return m.group(1) if m else None
    return None


def cut_target_of(text):
    """The owner facing target of CUT-SPEED.md's CUT IN MINUTES section, in seconds, or None when the section or its
    "- OWNER WAIT: at most N s" line is missing."""
    if not isinstance(text, str):
        return None
    m = CUT_TARGET.search(sections(text).get(CUT_SECTION, ""))
    return int(m.group(1)) if m else None


def file_problems(name, text):
    """Every way one seed file falls short, one string each, each prefixed with the file's name; a NO-DATA string
    for a file this check does not know or an unreadable text. The ISSUES file's rows must each carry seven cells,
    a root cause naming a function or a file, and a test unit under scripts/. The cut file's CUT IN MINUTES section
    must carry its numeric owner facing target."""
    if name not in FILE_SECTIONS:
        return ["NO-DATA: %s is not a seed file this check knows" % name]
    if not isinstance(text, str) or not text.strip():
        return ["NO-DATA: %s is %s" % (name, "empty" if isinstance(text, str) else type(text).__name__)]
    if text.startswith("NO-DATA:"):
        return [text]
    problems = []
    found = sections(text)
    for section in FILE_SECTIONS[name]:
        if section not in found:
            problems.append("%s: missing section: ## %s" % (name, section))
    if name == ISSUES_FILE:
        rows = table_rows(found.get("ISSUES", ""))
        if "ISSUES" in found and not rows:
            problems.append("%s ISSUES: the table is empty" % name)
        for row in rows:
            label = row[0] if row else "?"
            if len(row) != MERGED_ISSUE_CELLS:
                problems.append("%s %s: %d cells, %d expected" % (name, label, len(row), MERGED_ISSUE_CELLS))
                continue
            if not NAMES_CODE.search(row[2]):
                problems.append("%s %s: the root cause names no function or file" % (name, label))
            if not TEST_FILE.search(row[3]):
                if ANY_TEST.search(row[3]):
                    problems.append("%s %s: the test unit is not under scripts/" % (name, label))
                else:
                    problems.append("%s %s: no test unit (test_*.py)" % (name, label))
    if name == DELEGATION_FILE:
        problems.extend(decision_problems(name, found))
    if name == CUT_FILE and CUT_SECTION in found and cut_target_of(text) is None:
        problems.append("%s %s: no numeric owner facing target (- OWNER WAIT: at most N s)" % (name, CUT_SECTION))
    if name == STABILIZE_FILE:
        problems.extend(plan_council_problems(name, found))
        problems.extend(open_item_problems(name, found))
    for n, line in enumerate(text.splitlines(), 1):
        if any(d in line for d in DASHES):
            problems.append("%s: dash at line %d" % (name, n))
        if CREDIT.search(line):
            problems.append("%s: credit at line %d" % (name, n))
    return problems


def register_points_to(text):
    """The seed files the register does not name by their tree path; [] when it names all four; a NO-DATA list
    for an unreadable text."""
    if not isinstance(text, str) or not text.strip() or text.startswith("NO-DATA:"):
        return ["NO-DATA: the register text is unreadable"]
    return [name for name in FILE_SECTIONS if (SEED_DIR + "/" + name) not in text.replace(os.sep, "/")]


GOOD_ISSUE_ROWS = [
    "| id | symptom (evidence path) | root cause (source function) | failing unit test (file, case, assertion) | fix | regression guard | state |",
    "| --- | --- | --- | --- | --- | --- | --- |",
    "| I1 | the seat refused writes (loop.log line 65) | scripts/loop/native_worker.py seat_root() defaults under ~/.claude | scripts/loop/test_native_worker.py NEW test_a_seat_that_cannot_write_refuses_the_launch: READY is never printed | default outside ~/.claude | the probe case runs in the gate | open |",
    "| I2 | the gate row read NO-DATA (gate.txt) | scripts/required_fast.sh line 235 writes the raw id | scripts/test_gate_ledger_cut_id.py: a non hex id is written as none | one writer | the test runs in the gate | landed |",
]


GOOD_DECISION = """### 1. A fixture decision

- OPTION CHOSEN: (a) the first option.
- ALTERNATIVES REJECTED: (b) the second option, for a reason.
- EVIDENCE: a file and a line.
- COST OF BEING WRONG: one measured cost.
- FLIP CONDITION: one observable fact.
"""


GOOD_COUNCIL = """Members: Fable (chair), Opus, Astra, DeepSeek, Jev. Rule: 4 or 5 of 5 is CONSENSUS, 3 of 5 is MAJORITY, less is SPLIT.

### D1. A fixture question

- BALLOT: fable A, opus A, astra A, deepseek A, jev ABSTAIN
- OUTCOME: CONSENSUS (A 4 of 5)
- CHOSEN: A

### D2. A second fixture question

- BALLOT: fable A, opus A, astra B, deepseek A, jev ABSTAIN
- OUTCOME: MAJORITY (A 3 of 5, B 1), dissent astra
- CHOSEN: A
- OPTION CHOSEN: (A) the first option.
- ALTERNATIVES REJECTED: (B) the second option, for a reason.
- EVIDENCE: a file and a line.
- COST OF BEING WRONG: one measured cost.
- FLIP CONDITION: one observable fact.
"""


GOOD_OPEN_ITEMS = """### 1. RULED 2026-10-18: A fixture question the owner answered

- RULING: "Option one (Recommended)", the owner's words, 2026-10-18.
- OPTION CHOSEN: (A) the ruled option.
- ALTERNATIVES REJECTED: (B) the option the owner did not take, for a reason.
- EVIDENCE: a ruled item's file and line.
- COST OF BEING WRONG: the ruled item's measured cost.
- FLIP CONDITION: the ruled item's observable fact.

### 2. A fixture question still open

- QUESTION: what is asked.
- RECOMMENDED: (A) the first option.
- ALTERNATIVES: (B) the second option.
- OWNER: HIS HAND.

### The findings and their dispositions

prose, not a record.
"""


CUT_TARGET_LINE = "- OWNER WAIT: at most 600 s from the typed app code to the public tag and the release page."


def _file_fixture(name):
    """A minimal clean fixture for `name`: every required heading, for the ISSUES file a two row table, for the
    delegation file one decision record carrying the five fields, for the plan of record a two point COUNCIL section,
    for the cut file the owner facing target line."""
    body = ["# fixture", ""]
    for section in FILE_SECTIONS[name]:
        body.append("## %s" % section)
        body.append("")
        if name == ISSUES_FILE and section == "ISSUES":
            body.extend(GOOD_ISSUE_ROWS)
        elif name == DELEGATION_FILE and section == DECISIONS_SECTION:
            body.append(GOOD_DECISION)
        elif name == STABILIZE_FILE and section == "COUNCIL":
            body.append(GOOD_COUNCIL)
        elif name == STABILIZE_FILE and section == "OPEN ITEMS":
            body.append(GOOD_OPEN_ITEMS)
        elif name == CUT_FILE and section == CUT_SECTION:
            body.append(CUT_TARGET_LINE)
        else:
            body.append("one line of prose.")
        body.append("")
    return "\n".join(body)


class TheRealSeedFiles(unittest.TestCase):
    def _text(self, name):
        # docs/plan is not shipped in the public export: a missing file is a named skip there, never a pass.
        rel = os.path.join(SEED_DIR, name)
        if not os.path.exists(os.path.join(ROOT, rel)):
            self.skipTest("%s is not shipped in this tree (export tree)" % rel)
        text = register_text(rel)
        self.assertFalse(text.startswith("NO-DATA"), text[:120])
        return text

    def test_each_seed_file_is_complete(self):
        for name in FILE_SECTIONS:
            self.assertEqual(file_problems(name, self._text(name)), [], name)

    def test_the_issues_file_carries_the_known_rows_each_with_a_test(self):
        rows = table_rows(sections(self._text(ISSUES_FILE)).get("ISSUES", ""))
        self.assertGreaterEqual(len(rows), 37, "the merged table holds the night's rows (I1 to I37)")
        for row in rows:
            self.assertTrue(TEST_FILE.search(row[3]), row[0])

    def test_the_register_names_the_four_files(self):
        self.assertEqual(register_points_to(self._text("REGISTER.md")), [])

    def test_open_item_1_carries_the_owners_ruling_verbatim(self):
        text = self._text(STABILIZE_FILE)
        self.assertEqual(ruling_of(text, 1), RULING_1)
        self.assertIsNone(ruling_of(text, 2), "item 2 is still the owner's to answer")
        self.assertIsNone(ruling_of(text, 3), "item 3 is still the owner's to answer")

    def test_the_cut_section_carries_the_owner_facing_target(self):
        # K1 (MAJORITY A, 2026-10-18 evening): at most 600 s from the typed app code to the public tag and the release page
        self.assertEqual(cut_target_of(self._text(CUT_FILE)), 600)

    def test_the_delegation_file_records_the_six_decisions(self):
        records = decision_records(sections(self._text(DELEGATION_FILE)).get(DECISIONS_SECTION, ""))
        self.assertEqual([label.split(".", 1)[0] for label, _ in records], ["1", "2", "3", "4", "5", "6"])


class TheFileJudge(unittest.TestCase):
    def test_every_fixture_is_clean(self):
        for name in FILE_SECTIONS:
            self.assertEqual(file_problems(name, _file_fixture(name)), [], name)

    def test_a_missing_section_is_refused_in_every_file(self):
        for name, wanted in FILE_SECTIONS.items():
            for section in wanted:
                text = _swap(_file_fixture(name), "## %s\n" % section, "## %s gone\n" % section)
                self.assertIn("%s: missing section: ## %s" % (name, section), file_problems(name, text))

    def test_an_issues_row_that_loses_its_test_is_refused(self):
        good = _file_fixture(ISSUES_FILE)
        text = _swap(good, "scripts/test_gate_ledger_cut_id.py: a non hex id is written as none", "a manual check")
        self.assertEqual(file_problems(ISSUES_FILE, text), ["%s I2: no test unit (test_*.py)" % ISSUES_FILE])
        text = _swap(good, "scripts/test_gate_ledger_cut_id.py", "products/x/tests/test_gate_ledger_cut_id.py")
        self.assertEqual(file_problems(ISSUES_FILE, text), ["%s I2: the test unit is not under scripts/" % ISSUES_FILE])

    def test_an_issues_row_with_six_cells_or_no_root_cause_is_refused(self):
        good = _file_fixture(ISSUES_FILE)
        text = _swap(good, " | the test runs in the gate | landed |", " | landed |")
        self.assertEqual(file_problems(ISSUES_FILE, text), ["%s I2: 6 cells, 7 expected" % ISSUES_FILE])
        text = _swap(good, "scripts/required_fast.sh line 235 writes the raw id", "it was slow")
        self.assertEqual(file_problems(ISSUES_FILE, text),
                         ["%s I2: the root cause names no function or file" % ISSUES_FILE])
        empty = good
        for row in GOOD_ISSUE_ROWS[2:]:
            empty = _swap(empty, row + "\n", "")
        self.assertEqual(file_problems(ISSUES_FILE, empty), ["%s ISSUES: the table is empty" % ISSUES_FILE])

    def test_dashes_credits_unknown_files_and_hostile_inputs(self):
        good = _file_fixture("CUT-SPEED.md")
        self.assertEqual(file_problems("CUT-SPEED.md", good + "\nfast " + chr(0x2013) + " slow\n"),
                         ["CUT-SPEED.md: dash at line %d" % (good.count("\n") + 2)])
        self.assertEqual(file_problems("CUT-SPEED.md", good + "\nDrafted by a model\n"),
                         ["CUT-SPEED.md: credit at line %d" % (good.count("\n") + 2)])
        self.assertTrue(file_problems("NOPE.md", good)[0].startswith("NO-DATA:"))
        for bad in (None, "", "   ", 5):
            problems = file_problems("CUT-SPEED.md", bad)
            self.assertEqual(len(problems), 1, bad)
            self.assertTrue(problems[0].startswith("NO-DATA:"), problems)
        self.assertEqual(file_problems("CUT-SPEED.md", register_text(None)), [register_text(None)])

    def test_a_cut_section_without_a_numeric_target_is_refused(self):
        good = _file_fixture(CUT_FILE)
        self.assertEqual(cut_target_of(good), 600)
        want = ["%s %s: no numeric owner facing target (- OWNER WAIT: at most N s)" % (CUT_FILE, CUT_SECTION)]
        for bad in ("- OWNER WAIT: fast.", "- OWNER WAIT: at most ten minutes.", "OWNER WAIT: at most 600 s", ""):
            text = _swap(good, CUT_TARGET_LINE, bad)
            self.assertEqual(file_problems(CUT_FILE, text), want, bad)
            self.assertIsNone(cut_target_of(text), bad)
        self.assertIsNone(cut_target_of(None))
        self.assertIsNone(cut_target_of(_swap(good, "## %s\n" % CUT_SECTION, "## %s gone\n" % CUT_SECTION)))

    def test_an_unresolved_decisions_section_is_refused(self):
        text = _file_fixture(DELEGATION_FILE) + "\n## %s\n\n1. still open.\n" % UNRESOLVED_SECTION
        self.assertEqual(file_problems(DELEGATION_FILE, text),
                         ["%s: ## %s still present, every decision is recorded under ## %s"
                          % (DELEGATION_FILE, UNRESOLVED_SECTION, DECISIONS_SECTION)])

    def test_a_decision_missing_one_field_is_refused(self):
        good = _file_fixture(DELEGATION_FILE)
        for field in DECISION_FIELDS:
            line = next(l for l in GOOD_DECISION.splitlines() if l.startswith("- %s:" % field))
            want = ["%s decision 1: missing %s" % (DELEGATION_FILE, field)]
            self.assertEqual(file_problems(DELEGATION_FILE, _swap(good, line + "\n", "")), want, field)
            self.assertEqual(file_problems(DELEGATION_FILE, _swap(good, line, "- %s:" % field)), want, field)

    def test_a_decisions_section_without_a_record_is_refused(self):
        text = _swap(_file_fixture(DELEGATION_FILE), GOOD_DECISION, "nothing decided yet.\n")
        self.assertEqual(file_problems(DELEGATION_FILE, text),
                         ["%s DECISIONS: no decision record (### N. title)" % DELEGATION_FILE])
        self.assertEqual(decision_records(None), [])
        self.assertEqual(decision_records(""), [])

    def test_the_register_pointer_check(self):
        names = "\n".join("- %s/%s" % (SEED_DIR, n) for n in FILE_SECTIONS)
        self.assertEqual(register_points_to(GOOD + "\n## FILES OF THE SEED\n\n" + names + "\n"), [])
        self.assertEqual(register_points_to(GOOD), list(FILE_SECTIONS))
        self.assertEqual(register_points_to(None), ["NO-DATA: the register text is unreadable"])

    def test_a_plan_council_section_missing_a_member_or_the_rule_is_refused(self):
        good = _file_fixture(STABILIZE_FILE)
        self.assertEqual(file_problems(STABILIZE_FILE, good.replace("Opus, ", "").replace(", opus A", "")),
                         ["%s COUNCIL: member opus not named" % STABILIZE_FILE])
        self.assertEqual(file_problems(STABILIZE_FILE, good.replace("3 of 5", "three of five")),   # the OUTCOME lines too
                         ["%s COUNCIL: the rule does not say '3 of 5'" % STABILIZE_FILE])
        self.assertEqual(file_problems(STABILIZE_FILE, _swap(good, GOOD_COUNCIL, "Members: fable, opus, astra, deepseek, jev; 4 or 5 of 5, 3 of 5.\n")),
                         ["%s COUNCIL: no point record (### Dn. question)" % STABILIZE_FILE])

    def test_an_open_item_marked_ruled_carries_the_ruling_and_its_five_fields(self):
        good = _file_fixture(STABILIZE_FILE)
        self.assertEqual(file_problems(STABILIZE_FILE, good), [])
        self.assertEqual(ruling_of(good, 1), "Option one (Recommended)")
        self.assertIsNone(ruling_of(good, 2))
        self.assertEqual(file_problems(STABILIZE_FILE, _swap(good, '- RULING: "Option one (Recommended)", the owner\'s words, 2026-10-18.\n', "")),
                         ['%s OPEN ITEMS 1: RULED without the ruling quoted and dated (- RULING: "..." YYYY-MM-DD)' % STABILIZE_FILE])
        self.assertEqual(file_problems(STABILIZE_FILE, _swap(good, "the owner's words, 2026-10-18.", "the owner's words.")),
                         ['%s OPEN ITEMS 1: RULED without the ruling quoted and dated (- RULING: "..." YYYY-MM-DD)' % STABILIZE_FILE])
        for field in DECISION_FIELDS:
            line = next(l for l in GOOD_OPEN_ITEMS.splitlines() if l.startswith("- %s:" % field))
            self.assertEqual(file_problems(STABILIZE_FILE, _swap(good, line + "\n", "")),
                             ["%s OPEN ITEMS 1: RULED, missing %s" % (STABILIZE_FILE, field)], field)
        unmarked = _swap(good, "### 1. RULED 2026-10-18: A fixture", "### 1. A fixture")
        self.assertIn("%s OPEN ITEMS 1: a ruling recorded on an item not marked RULED" % STABILIZE_FILE,
                      file_problems(STABILIZE_FILE, unmarked))
        self.assertIsNone(ruling_of(unmarked, 1))
        for line in OPEN_ITEM_LINES:
            text = _swap(good, next(l for l in GOOD_OPEN_ITEMS.splitlines() if l.startswith("- %s:" % line)) + "\n", "")
            self.assertEqual(file_problems(STABILIZE_FILE, text), ["%s OPEN ITEMS 2: missing %s" % (STABILIZE_FILE, line)], line)
        self.assertEqual(file_problems(STABILIZE_FILE, _swap(good, "## OPEN ITEMS\n", "")),
                         ["%s: missing section: ## OPEN ITEMS" % STABILIZE_FILE])
        self.assertIsNone(ruling_of(None, 1))

    def test_a_plan_council_record_missing_a_line_or_a_field_is_refused(self):
        good = _file_fixture(STABILIZE_FILE)
        for line in COUNCIL_LINES:
            text = _swap(good, "- %s: " % line, "- %s:" % line)   # the first record (D1) loses the line's value
            self.assertEqual(file_problems(STABILIZE_FILE, text), ["%s COUNCIL D1: missing %s" % (STABILIZE_FILE, line)], line)
        for field in DECISION_FIELDS:
            line = next(l for l in GOOD_COUNCIL.splitlines() if l.startswith("- %s:" % field))
            self.assertEqual(file_problems(STABILIZE_FILE, _swap(good, line + "\n", "")),
                             ["%s COUNCIL D2: missing %s" % (STABILIZE_FILE, field)], field)
        # a CONSENSUS record needs no decision fields: D1 carries none and the fixture is clean
        self.assertEqual(file_problems(STABILIZE_FILE, good), [])


#: The 1.1.7 work units (UNITS-1.1.7.json): every epic of the plan, each unit with one runnable acceptance command and
#: a proof on both hosts. unit_problems(doc) is the judge; the integration acceptance unit is named in the document.
UNITS = os.path.join(SEED_DIR, "UNITS-1.1.7.json")
UNIT_KEYS = ("id", "epic", "title", "depends_on", "files", "acceptance", "host_proof", "rollout", "lane")
HOSTS = ("claude_code", "codex")
ROLLOUTS = ("gate", "canary", "lanes", "on")
#: The council's lanes (STABILIZE.md SCOPE): loop is the certificate's denominator, bootstrap the two session landed units,
#: kernel the judge closure landed behind the session gate with the owner's approval, field a trial that is not a landing.
LANES = ("loop", "bootstrap", "kernel", "field")
#: The three lists the council's SCOPE adds beside units; every entry carries its id and its reason, a merged entry its targets.
SCOPE_LISTS = ("merged", "deferred_to_1.1.8", "dropped")
ACCEPTANCE = re.compile(r"^(?:python3 -B|/usr/bin/python3 -B|sh|bash) scripts/[A-Za-z0-9_/.-]+\.(?:py|sh)(?: [A-Za-z0-9_.=/-]+)*$")
ACCEPTANCE_TEST = "scripts/test_delegation_acceptance.py"


def units_document(text):
    """The parsed units document, or a NO-DATA string: not text, not JSON, or not an object carrying a units list."""
    if not isinstance(text, str) or not text.strip():
        return "NO-DATA: the units text is %s" % ("empty" if isinstance(text, str) else type(text).__name__)
    if text.startswith("NO-DATA:"):
        return text
    try:
        doc = json.loads(text)
    except ValueError as exc:
        return "NO-DATA: the units file is not JSON (%s)" % type(exc).__name__
    if not isinstance(doc, dict) or not isinstance(doc.get("units"), list):
        return "NO-DATA: the units document is not an object with a units list"
    return doc


def _cycle(graph):
    """One dependency cycle as a list of ids, or [] when the graph is acyclic. Iterative, so a long chain cannot
    exhaust the interpreter's recursion limit."""
    state = {}
    for start in graph:
        if state.get(start):
            continue
        stack = [(start, iter(graph.get(start, ())))]
        path = [start]
        state[start] = 1
        while stack:
            node, it = stack[-1]
            nxt = next(it, None)
            if nxt is None:
                state[node] = 2
                stack.pop()
                path.pop()
                continue
            if state.get(nxt) == 1:
                return path[path.index(nxt):] + [nxt]
            if not state.get(nxt) and nxt in graph:
                state[nxt] = 1
                stack.append((nxt, iter(graph[nxt])))
                path.append(nxt)
    return []


def scope_list_problems(doc, known):
    """Every way the merged, deferred_to_1.1.8 and dropped lists fall short: not a list, an entry with no id, an entry
    that is still a unit, an entry with no reason, a merged entry whose targets are not units. A list that is absent is
    not a problem (the seed before the council had none)."""
    problems = []
    for key in SCOPE_LISTS:
        if key not in doc:
            continue
        entries = doc[key]
        if not isinstance(entries, list):
            problems.append("%s is not a list" % key)
            continue
        for n, e in enumerate(entries):
            if not isinstance(e, dict) or not isinstance(e.get("id"), str) or not e.get("id").strip():
                problems.append("%s entry %d: no id" % (key, n))
                continue
            eid = e["id"]
            if eid in known:
                problems.append("%s %s: still a unit" % (key, eid))
            if not isinstance(e.get("reason"), str) or not e.get("reason").strip():
                problems.append("%s %s: no reason" % (key, eid))
            if key == "merged":
                into = e.get("into")
                if not isinstance(into, list) or not into or any(t not in known for t in into):
                    problems.append("merged %s: into names no unit" % eid)
    return problems


def unit_problems(doc):
    """Every way the units document falls short, one string each; a one element NO-DATA list for an unreadable one."""
    if isinstance(doc, str):
        return [doc if doc.startswith("NO-DATA:") else "NO-DATA: the units document is a string"]
    if not isinstance(doc, dict) or not isinstance(doc.get("units"), list):
        return ["NO-DATA: the units document is not an object with a units list"]
    problems = []
    epics = doc.get("epics") if isinstance(doc.get("epics"), dict) else {}
    if not epics:
        problems.append("no epics named")
    ids, graph = [], {}
    for n, u in enumerate(doc["units"]):
        if not isinstance(u, dict):
            problems.append("unit %d is not an object" % n)
            continue
        uid = u.get("id") if isinstance(u.get("id"), str) and u.get("id").strip() else None
        label = uid or ("unit %d" % n)
        if uid is None:
            problems.append("%s: no id" % label)
        elif uid in ids:
            problems.append("%s: duplicate id" % uid)
        else:
            ids.append(uid)
        for key in UNIT_KEYS:
            if key not in u:
                problems.append("%s: missing %s" % (label, key))
        if u.get("epic") not in epics:
            problems.append("%s: epic %r is not one the document names" % (label, u.get("epic")))
        if not isinstance(u.get("title"), str) or not u.get("title", "").strip():
            problems.append("%s: no title" % label)
        deps = u.get("depends_on")
        if not isinstance(deps, list) or not all(isinstance(d, str) and d for d in deps):
            problems.append("%s: depends_on is not a list of ids" % label)
            deps = []
        if uid:
            graph[uid] = list(deps)
        files = u.get("files")
        if not isinstance(files, list) or not files or not all(isinstance(f, str) and f.strip() for f in files):
            problems.append("%s: files is not a non empty list of paths" % label)
        acc = u.get("acceptance")
        if not isinstance(acc, str) or not ACCEPTANCE.match(acc):
            problems.append("%s: acceptance is not one runnable scripts/ command" % label)
        hp = u.get("host_proof")
        if not isinstance(hp, dict) or any(not isinstance(hp.get(h), str) or not hp.get(h, "").strip() for h in HOSTS):
            problems.append("%s: host_proof does not name both hosts (%s)" % (label, ", ".join(HOSTS)))
        if u.get("rollout") not in ROLLOUTS:
            problems.append("%s: rollout %r is not one of %s" % (label, u.get("rollout"), ", ".join(ROLLOUTS)))
        if u.get("lane") not in LANES:
            problems.append("%s: lane %r is not one of %s" % (label, u.get("lane"), ", ".join(LANES)))
    known = set(ids)
    problems.extend(scope_list_problems(doc, known))
    gate = doc.get("certification_gate_unit")
    if not isinstance(gate, str) or gate not in known:
        problems.append("certification_gate_unit %r names no unit" % (gate,))
    for uid, deps in graph.items():
        for d in deps:
            if d not in known:
                problems.append("%s: depends_on names %s, which is no unit" % (uid, d))
            elif d == uid:
                problems.append("%s: depends on itself" % uid)
    cycle = _cycle(graph)
    if cycle:
        problems.append("dependency cycle: %s" % " -> ".join(cycle))
    want = doc.get("integration_acceptance_unit")
    acceptance = next((u for u in doc["units"] if isinstance(u, dict) and u.get("id") == want), None)
    if not isinstance(want, str) or acceptance is None:
        problems.append("integration_acceptance_unit %r names no unit" % (want,))
    else:
        if ACCEPTANCE_TEST not in str(acceptance.get("acceptance", "")):
            problems.append("%s: the acceptance does not run %s" % (want, ACCEPTANCE_TEST))
        neg = acceptance.get("negative_case")
        if not isinstance(neg, str) or "prototype" not in neg.lower() or "fail" not in neg.lower():
            problems.append("%s: no negative case saying the prototype alone fails it" % want)
        if not isinstance(acceptance.get("records_read_back"), list) or not acceptance.get("records_read_back"):
            problems.append("%s: names no existing record it reads the cost and status back from" % want)
    text = json.dumps(doc, ensure_ascii=False)
    if any(d in text for d in DASHES):
        problems.append("dash in the units document")
    if CREDIT.search(text):
        problems.append("credit in the units document")
    return problems


#: The cut council's units (CUT-SPEED.md CUT IN MINUTES section 5): CS1 to CS12 and CF1, the run's last unit, plus CS13
#: (section 9, the second review: the exporter's push time proofs as keyed rows) and CS14 (the owner's ruling on
#: STABILIZE.md OPEN ITEMS 4, 2026-10-19: the mutation rows run as units land, so the first rehearsal runs warm).
CUT_UNITS = ["CS%d" % i for i in range(1, 15)]
CUT_LAST = "CF1"
CUT_CERT = "CS11"
PER_LANDING = "CS14"
PER_LANDING_DEPS = ("A2", "CS1")
PER_LANDING_FILES = ("scripts/release_note_perturb.py", "scripts/perturb_pool.py")


def cut_unit_problems(doc):
    """Every way the units file does not carry the cut council's units: a CS unit or CF1 missing, a CS unit whose
    acceptance is not one runnable scripts/test_*.py command, CS11 (the cut speed certificate) not behind every other CS
    unit, CF1 (the cut) not behind CS11, CS12 (the commit it cuts is the one the rehearsal proved, K2) and CS14, and
    CS14 (the per landing mutation rows, OPEN ITEMS 4 RULED) not on the loop lane, not behind A2 and CS1, or not owning
    release_note_perturb.py, perturb_pool.py and the test its acceptance runs."""
    if not isinstance(doc, dict) or not isinstance(doc.get("units"), list):
        return ["NO-DATA: the units document is not an object with a units list"]
    units = {u.get("id"): u for u in doc["units"] if isinstance(u, dict)}
    problems = []
    for uid in CUT_UNITS + [CUT_LAST]:
        if uid not in units:
            problems.append("%s: missing" % uid)
    for uid in CUT_UNITS:
        if uid not in units:
            continue
        acc = units[uid].get("acceptance")
        if not (isinstance(acc, str) and ACCEPTANCE.match(acc) and TEST_FILE.search(acc)):
            problems.append("%s: acceptance is not one runnable scripts/test_*.py command: %r" % (uid, acc))
    if CUT_CERT in units:
        deps = set(units[CUT_CERT].get("depends_on") or [])
        problems.extend("%s: does not depend on %s" % (CUT_CERT, uid) for uid in CUT_UNITS if uid != CUT_CERT and uid not in deps)
    if CUT_LAST in units:
        deps = set(units[CUT_LAST].get("depends_on") or [])
        problems.extend("%s: does not depend on %s" % (CUT_LAST, uid) for uid in (CUT_CERT, "CS12", PER_LANDING)
                        if uid not in deps)
    if PER_LANDING in units:
        u = units[PER_LANDING]
        deps, files = set(u.get("depends_on") or []), set(u.get("files") or [])
        if u.get("lane") != "loop":
            problems.append("%s: lane %r is not loop" % (PER_LANDING, u.get("lane")))
        problems.extend("%s: does not depend on %s" % (PER_LANDING, d) for d in PER_LANDING_DEPS if d not in deps)
        problems.extend("%s: does not own %s" % (PER_LANDING, f) for f in PER_LANDING_FILES if f not in files)
        test = TEST_FILE.search(str(u.get("acceptance")))
        if test and test.group(0) not in files:
            problems.append("%s: does not own its acceptance test %s" % (PER_LANDING, test.group(0)))
    return problems


def _unit(uid, deps=(), **over):
    u = {"id": uid, "epic": "E-X", "title": "unit " + uid, "depends_on": list(deps), "files": ["scripts/x.py"],
         "acceptance": "python3 -B scripts/test_x.py", "host_proof": {"claude_code": "run it", "codex": "run it"},
         "rollout": "gate", "lane": "loop"}
    u.update(over)
    return u


GOOD_CUT_UNITS = {"units": [_unit(uid) for uid in CUT_UNITS if uid not in (CUT_CERT, PER_LANDING)]
                  + [_unit(PER_LANDING, PER_LANDING_DEPS, acceptance="python3 -B scripts/test_landing_rows.py",
                           files=list(PER_LANDING_FILES) + ["scripts/test_landing_rows.py"]),
                     _unit(CUT_CERT, [uid for uid in CUT_UNITS if uid != CUT_CERT]),
                     _unit(CUT_LAST, [CUT_CERT, "CS12", PER_LANDING])]}


GOOD_UNITS = {
    "epics": {"E-X": "a fixture epic"},
    "integration_acceptance_unit": "DG11",
    "certification_gate_unit": "B",
    "units": [
        _unit("A"), _unit("B", ["A"]),
        _unit("DG11", ["B"], acceptance="python3 -B " + ACCEPTANCE_TEST,
              negative_case="the prototype skill alone: the test FAILS", records_read_back=["the ledger row"]),
    ],
}


class TheRealUnits(unittest.TestCase):
    def test_the_units_file_is_complete(self):
        if not os.path.exists(os.path.join(ROOT, UNITS)):
            self.skipTest("%s is not shipped in this tree (export tree)" % UNITS)
        doc = units_document(register_text(UNITS))
        self.assertIsInstance(doc, dict, doc)
        self.assertEqual(unit_problems(doc), [])
        epics = {u["epic"] for u in doc["units"]}
        self.assertEqual(epics, set(doc["epics"]), "every named epic has units and every unit an epic")
        self.assertGreaterEqual(len([u for u in doc["units"] if u["epic"] == "E-DELEG"]), 15)
        # every issue row of the night is accounted for: a unit of its own, or merged into one with its reason
        accounted = {u["id"] for u in doc["units"]} | {e["id"] for e in doc.get("merged", [])}
        for n in range(1, 38):
            self.assertIn("I%d" % n, accounted)

    def test_the_units_file_carries_the_cut_councils_units(self):
        if not os.path.exists(os.path.join(ROOT, UNITS)):
            self.skipTest("%s is not shipped in this tree (export tree)" % UNITS)
        doc = units_document(register_text(UNITS))
        self.assertIsInstance(doc, dict, doc)
        self.assertEqual(cut_unit_problems(doc), [])

    def test_the_register_names_both_delegation_files(self):
        if not os.path.exists(os.path.join(ROOT, REGISTER)):
            self.skipTest("%s is not shipped in this tree (export tree)" % REGISTER)
        text = register_text(REGISTER)
        self.assertIn(UNITS.replace(os.sep, "/"), text.replace(os.sep, "/"))
        self.assertEqual(register_points_to(text), [])


class TheUnitsJudge(unittest.TestCase):
    def _copy(self):
        return json.loads(json.dumps(GOOD_UNITS))

    def test_the_fixture_is_clean(self):
        self.assertEqual(unit_problems(GOOD_UNITS), [])
        self.assertEqual(unit_problems(units_document(json.dumps(GOOD_UNITS))), [])

    def test_a_missing_dependency_is_refused(self):
        doc = self._copy()
        doc["units"][1]["depends_on"] = ["NOPE"]
        self.assertEqual(unit_problems(doc), ["B: depends_on names NOPE, which is no unit"])

    def test_a_duplicate_id_and_a_cycle_are_refused(self):
        doc = self._copy()
        doc["units"].append(_unit("A"))
        self.assertEqual(unit_problems(doc), ["A: duplicate id"])
        doc = self._copy()
        doc["units"][0]["depends_on"] = ["B"]
        self.assertEqual(unit_problems(doc), ["dependency cycle: A -> B -> A"])
        doc = self._copy()
        doc["units"][0]["depends_on"] = ["A"]
        self.assertEqual(unit_problems(doc), ["A: depends on itself", "dependency cycle: A -> A"])

    def test_an_acceptance_that_is_not_one_command_is_refused(self):
        for bad in ("run the tests by hand", "pytest", "python3 -B scripts/test_x.py && echo ok || true", "", None):
            doc = self._copy()
            doc["units"][0]["acceptance"] = bad
            self.assertEqual(unit_problems(doc), ["A: acceptance is not one runnable scripts/ command"], bad)

    def test_a_unit_proving_one_host_is_refused(self):
        doc = self._copy()
        del doc["units"][0]["host_proof"]["codex"]
        self.assertEqual(unit_problems(doc), ["A: host_proof does not name both hosts (claude_code, codex)"])
        doc = self._copy()
        doc["units"][0]["host_proof"]["claude_code"] = ""
        self.assertEqual(unit_problems(doc), ["A: host_proof does not name both hosts (claude_code, codex)"])

    def test_a_missing_key_a_bad_rollout_and_an_unknown_epic_are_refused(self):
        doc = self._copy()
        del doc["units"][0]["files"]
        self.assertEqual(unit_problems(doc), ["A: missing files", "A: files is not a non empty list of paths"])
        doc = self._copy()
        doc["units"][0]["rollout"] = "someday"
        self.assertEqual(unit_problems(doc), ["A: rollout 'someday' is not one of gate, canary, lanes, on"])
        doc = self._copy()
        doc["units"][0]["epic"] = "E-Y"
        self.assertEqual(unit_problems(doc), ["A: epic 'E-Y' is not one the document names"])

    def test_the_integration_acceptance_unit_needs_its_negative_case(self):
        doc = self._copy()
        doc["integration_acceptance_unit"] = "A"
        self.assertEqual(unit_problems(doc), ["A: the acceptance does not run %s" % ACCEPTANCE_TEST,
                                              "A: no negative case saying the prototype alone fails it",
                                              "A: names no existing record it reads the cost and status back from"])
        doc = self._copy()
        del doc["units"][2]["negative_case"]
        self.assertEqual(unit_problems(doc), ["DG11: no negative case saying the prototype alone fails it"])
        doc = self._copy()
        doc["units"][2]["negative_case"] = "the prototype skill alone passes"
        self.assertEqual(unit_problems(doc), ["DG11: no negative case saying the prototype alone fails it"])
        doc = self._copy()
        doc["integration_acceptance_unit"] = "ZZ"
        self.assertEqual(unit_problems(doc), ["integration_acceptance_unit 'ZZ' names no unit"])

    def test_dashes_credits_and_hostile_inputs(self):
        doc = self._copy()
        doc["units"][0]["title"] = "fast " + chr(0x2014) + " slow"
        self.assertEqual(unit_problems(doc), ["dash in the units document"])
        doc = self._copy()
        doc["units"][0]["title"] = "Drafted by a model"
        self.assertEqual(unit_problems(doc), ["credit in the units document"])
        for bad in (None, "", "   ", 5, "not json", "[1, 2]", '{"units": 3}'):
            doc = units_document(bad)
            self.assertIsInstance(doc, str, bad)
            self.assertTrue(doc.startswith("NO-DATA:"), doc)
            problems = unit_problems(doc)
            self.assertEqual(len(problems), 1)
            self.assertTrue(problems[0].startswith("NO-DATA:"), problems)
        self.assertEqual(unit_problems([]), ["NO-DATA: the units document is not an object with a units list"])
        self.assertEqual(unit_problems({"units": [7]}), ["no epics named", "unit 0 is not an object",
                                                          "certification_gate_unit None names no unit",
                                                          "integration_acceptance_unit None names no unit"])

    def test_a_lane_outside_the_four_and_a_missing_gate_unit_are_refused(self):
        doc = self._copy()
        doc["units"][0]["lane"] = "builder"
        self.assertEqual(unit_problems(doc), ["A: lane 'builder' is not one of loop, bootstrap, kernel, field"])
        doc = self._copy()
        del doc["units"][0]["lane"]
        self.assertEqual(unit_problems(doc), ["A: missing lane", "A: lane None is not one of loop, bootstrap, kernel, field"])
        doc = self._copy()
        doc["certification_gate_unit"] = "ZZ"
        self.assertEqual(unit_problems(doc), ["certification_gate_unit 'ZZ' names no unit"])

    def test_the_cut_units_judge(self):
        self.assertEqual(cut_unit_problems(GOOD_CUT_UNITS), [])
        doc = json.loads(json.dumps(GOOD_CUT_UNITS))
        doc["units"] = [u for u in doc["units"] if u["id"] != "CS12"]   # the seed before the cut council: no CS12 anywhere
        for u in doc["units"]:
            u["depends_on"] = [d for d in u["depends_on"] if d != "CS12"]
        self.assertEqual(cut_unit_problems(doc), ["CS12: missing", "CS11: does not depend on CS12", "CF1: does not depend on CS12"])
        for bad in ("sh scripts/x.sh", "python3 -B scripts/test_x.py && echo ok", "run it by hand", None):
            doc = json.loads(json.dumps(GOOD_CUT_UNITS))
            doc["units"][2]["acceptance"] = bad
            self.assertEqual(cut_unit_problems(doc),
                             ["CS3: acceptance is not one runnable scripts/test_*.py command: %r" % (bad,)], bad)
        doc = json.loads(json.dumps(GOOD_CUT_UNITS))
        doc["units"][-1]["depends_on"] = ["CS11", PER_LANDING]
        self.assertEqual(cut_unit_problems(doc), ["CF1: does not depend on CS12"])

    def test_the_per_landing_mutation_unit_judge(self):
        def edited(fn):
            doc = json.loads(json.dumps(GOOD_CUT_UNITS))
            fn({u["id"]: u for u in doc["units"]})
            return cut_unit_problems(doc)
        # the units file before the owner's ruling on OPEN ITEMS 4: no CS14 anywhere
        doc = json.loads(json.dumps(GOOD_CUT_UNITS))
        doc["units"] = [u for u in doc["units"] if u["id"] != PER_LANDING]
        for u in doc["units"]:
            u["depends_on"] = [d for d in u["depends_on"] if d != PER_LANDING]
        self.assertEqual(cut_unit_problems(doc), ["CS14: missing", "CS11: does not depend on CS14",
                                                  "CF1: does not depend on CS14"])
        # each guard alone
        self.assertEqual(edited(lambda u: u[CUT_LAST].update(depends_on=[CUT_CERT, "CS12"])), ["CF1: does not depend on CS14"])
        self.assertEqual(edited(lambda u: u[PER_LANDING].update(lane="kernel")), ["CS14: lane 'kernel' is not loop"])
        for dep in PER_LANDING_DEPS:
            self.assertEqual(edited(lambda u: u[PER_LANDING]["depends_on"].remove(dep)),
                             ["CS14: does not depend on %s" % dep], dep)
        for path in PER_LANDING_FILES:
            self.assertEqual(edited(lambda u: u[PER_LANDING]["files"].remove(path)), ["CS14: does not own %s" % path], path)
        self.assertEqual(edited(lambda u: u[PER_LANDING]["files"].remove("scripts/test_landing_rows.py")),
                         ["CS14: does not own its acceptance test scripts/test_landing_rows.py"])
        self.assertEqual(edited(lambda u: u[PER_LANDING].update(acceptance="run it by hand")),
                         ["CS14: acceptance is not one runnable scripts/test_*.py command: 'run it by hand'"])
        for bad in (None, [], {"units": 3}):
            self.assertEqual(cut_unit_problems(bad), ["NO-DATA: the units document is not an object with a units list"])

    def test_a_deferred_or_dropped_unit_without_a_reason_is_refused(self):
        for key in SCOPE_LISTS:
            doc = self._copy()
            entry = {"id": "Z1", "reason": "the council deferred it"}
            if key == "merged":
                entry["into"] = ["A"]
            doc[key] = [entry]
            self.assertEqual(unit_problems(doc), [], key)
            doc[key][0]["reason"] = ""
            self.assertEqual(unit_problems(doc), ["%s Z1: no reason" % key], key)
            del doc[key][0]["reason"]
            self.assertEqual(unit_problems(doc), ["%s Z1: no reason" % key], key)
            doc[key][0]["reason"] = "back"
            doc[key][0]["id"] = "A"
            self.assertEqual(unit_problems(doc), ["%s A: still a unit" % key], key)
            doc[key] = [{"reason": "no id"}]
            self.assertEqual(unit_problems(doc), ["%s entry 0: no id" % key], key)
            doc[key] = "PP3"
            self.assertEqual(unit_problems(doc), ["%s is not a list" % key], key)
        doc = self._copy()
        doc["merged"] = [{"id": "Z1", "reason": "folded", "into": ["NOPE"]}]
        self.assertEqual(unit_problems(doc), ["merged Z1: into names no unit"])
        doc["merged"] = [{"id": "Z1", "reason": "folded"}]
        self.assertEqual(unit_problems(doc), ["merged Z1: into names no unit"])


#: The council record (COUNCIL-1.1.7.json): five members, eleven points, and an outcome that must follow from the ballots.
#: The cut council of 2026-10-18 evening is a second block of the same file under CUT_COUNCIL, eight points K1 to K8,
#: judged by council_problems under the same rule; the first council's eleven points are untouched by it.
COUNCIL = os.path.join(SEED_DIR, "COUNCIL-1.1.7.json")
CUT_COUNCIL = "cut_council"
CUT_POINTS = ["K%d" % i for i in range(1, 9)]
DECISION_KEYS = ("option", "alternatives_rejected", "evidence", "cost_of_being_wrong", "flip_condition")
#: The owner may amend a CONSENSUS point and the chair may not, so an owner's amendment is recorded beside the point,
#: never by rewriting its ballots or outcome (which council_problems recomputes either way). Each entry names an existing
#: point, the owner's ruling verbatim, the option he chose, the date, and what it amends.
AMENDMENT_KEYS = ("point", "ruling", "option", "date", "amends")


def outcome_of(ballots):
    """(outcome, leading option, counts) for one point under the rule: 4 or 5 of 5 CONSENSUS, 3 of 5 MAJORITY, less SPLIT;
    ABSTAIN and NO-DATA count for no option. leading is None on a tie or with no vote (a SPLIT the chair decides)."""
    counts = {}
    for vote in (ballots or {}).values():
        if vote in NON_VOTES:
            continue
        counts[vote] = counts.get(vote, 0) + 1
    if not counts:
        return "SPLIT", None, counts
    top = max(counts.values())
    leaders = [o for o, n in counts.items() if n == top]
    leading = leaders[0] if len(leaders) == 1 else None
    if top >= CONSENSUS_MIN:
        return "CONSENSUS", leading, counts
    if top >= MAJORITY_MIN:
        return "MAJORITY", leading, counts
    return "SPLIT", leading, counts


def council_document(text):
    """The parsed council document, or a NO-DATA string: not text, not JSON, or not an object carrying a points list."""
    if not isinstance(text, str) or not text.strip():
        return "NO-DATA: the council text is %s" % ("empty" if isinstance(text, str) else type(text).__name__)
    if text.startswith("NO-DATA:"):
        return text
    try:
        doc = json.loads(text)
    except ValueError as exc:
        return "NO-DATA: the council file is not JSON (%s)" % type(exc).__name__
    if not isinstance(doc, dict) or not isinstance(doc.get("points"), list):
        return "NO-DATA: the council document is not an object with a points list"
    return doc


def council_problems(doc):
    """Every way the council record falls short, one string each; a one element NO-DATA list for an unreadable one.
    The outcome, the leading option, the counts and the dissents are RECOMPUTED from the five ballots, so a hand edited
    outcome is refused by name."""
    if isinstance(doc, str):
        return [doc if doc.startswith("NO-DATA:") else "NO-DATA: the council document is a string"]
    if not isinstance(doc, dict) or not isinstance(doc.get("points"), list):
        return ["NO-DATA: the council document is not an object with a points list"]
    problems = []
    members = doc.get("members")
    if not isinstance(members, list) or sorted(members) != sorted(MEMBERS):
        problems.append("members are not the five: %s" % ", ".join(MEMBERS))
    if not doc["points"]:
        problems.append("no point recorded")
    seen = []
    for n, p in enumerate(doc["points"]):
        if not isinstance(p, dict):
            problems.append("point %d is not an object" % n)
            continue
        pid = p.get("id") if isinstance(p.get("id"), str) and p.get("id").strip() else None
        label = pid or ("point %d" % n)
        if pid is None:
            problems.append("%s: no id" % label)
        elif pid in seen:
            problems.append("%s: duplicate id" % pid)
        else:
            seen.append(pid)
        options = p.get("options") if isinstance(p.get("options"), dict) else {}
        if len(options) < 2:
            problems.append("%s: fewer than two options" % label)
        ballots = p.get("ballots")
        if not isinstance(ballots, dict) or sorted(ballots) != sorted(MEMBERS):
            problems.append("%s: not one ballot per member" % label)
            ballots = {}
        for member, vote in ballots.items():
            if vote not in options and vote not in NON_VOTES:
                problems.append("%s: %s voted %r, which is no option" % (label, member, vote))
        outcome, leading, counts = outcome_of(ballots)
        if p.get("outcome") != outcome:
            problems.append("%s: outcome %r does not follow from the ballots (%s)" % (label, p.get("outcome"), outcome))
        if "counts" in p and p.get("counts") != counts:
            problems.append("%s: counts do not match the ballots" % label)
        chosen = p.get("chosen")
        if outcome in ("CONSENSUS", "MAJORITY"):
            if chosen != leading:
                problems.append("%s: chosen %r is not the leading option %r" % (label, chosen, leading))
        else:
            if chosen not in options:
                problems.append("%s: SPLIT chosen %r is no option" % (label, chosen))
            if p.get("owner_review") is not True:
                problems.append("%s: a SPLIT is not flagged for the owner's review" % label)
        if outcome in ("MAJORITY", "SPLIT"):
            decision = p.get("decision")
            if not isinstance(decision, dict):
                problems.append("%s: no decision record" % label)
            else:
                for key in DECISION_KEYS:
                    if not isinstance(decision.get(key), str) or not decision.get(key).strip():
                        problems.append("%s: decision missing %s" % (label, key))
        dissents = sorted(m for m, v in ballots.items() if v not in NON_VOTES and v != chosen)
        if p.get("dissents") != dissents:
            problems.append("%s: dissents do not match the ballots" % label)
    amendments = doc.get("owner_amendments", [])
    if not isinstance(amendments, list):
        problems.append("owner_amendments is not a list")
        amendments = []
    for n, a in enumerate(amendments):
        if not isinstance(a, dict):
            problems.append("owner amendment %d is not an object" % n)
            continue
        label = "owner amendment %d" % n
        for key in AMENDMENT_KEYS:
            if not isinstance(a.get(key), str) or not a.get(key).strip():
                problems.append("%s: missing %s" % (label, key))
        if isinstance(a.get("point"), str) and a["point"].strip() and a["point"] not in seen:
            problems.append("%s: point %r is no point of the council" % (label, a["point"]))
        if isinstance(a.get("date"), str) and a["date"].strip() and not re.fullmatch(r"\d{4}-\d{2}-\d{2}", a["date"]):
            problems.append("%s: date %r is not YYYY-MM-DD" % (label, a["date"]))
    text = json.dumps(doc, ensure_ascii=False)
    if any(d in text for d in DASHES):
        problems.append("dash in the council document")
    if CREDIT.search(text):
        problems.append("credit in the council document")
    return problems


FOUR_A = {"fable": "A", "opus": "A", "astra": "A", "deepseek": "A", "jev": "ABSTAIN"}
THREE_A = {"fable": "A", "opus": "A", "astra": "B", "deepseek": "A", "jev": "ABSTAIN"}
TWO_TWO = {"fable": "A", "opus": "B", "astra": "A", "deepseek": "B", "jev": "ABSTAIN"}


def _point(pid, ballots, **over):
    """A fixture point whose outcome fields are the ones the rule gives; `over` is what a test hand edits."""
    p = {"id": pid, "question": "q " + pid, "options": {"A": "first", "B": "second"}, "ballots": dict(ballots)}
    outcome, leading, counts = outcome_of(p["ballots"])
    chosen = leading if leading is not None else "A"
    p.update({"counts": counts, "outcome": outcome, "chosen": chosen, "owner_review": outcome == "SPLIT",
              "dissents": sorted(m for m, v in p["ballots"].items() if v not in NON_VOTES and v != chosen)})
    if outcome != "CONSENSUS":
        p["decision"] = {k: "fixture " + k for k in DECISION_KEYS}
    p.update(over)
    return p


GOOD_COUNCIL_DOC = {"members": list(MEMBERS), "points": [_point("D1", FOUR_A), _point("D2", THREE_A)],
                    "owner_amendments": [{"point": "D1", "ruling": "Option one (Recommended)", "option": "A",
                                          "date": "2026-10-18", "amends": "one word of D1"}]}


class TheRealCouncil(unittest.TestCase):
    def _doc(self):
        if not os.path.exists(os.path.join(ROOT, COUNCIL)):
            self.skipTest("%s is not shipped in this tree (export tree)" % COUNCIL)
        doc = council_document(register_text(COUNCIL))
        self.assertIsInstance(doc, dict, doc)
        return doc

    def test_the_council_record_is_complete_and_every_outcome_follows_its_ballots(self):
        doc = self._doc()
        self.assertEqual(council_problems(doc), [])
        self.assertEqual([p["id"] for p in doc["points"]], ["D%d" % i for i in range(1, 12)])
        for p in doc["points"]:
            self.assertEqual(sorted(p["ballots"]), sorted(MEMBERS), p["id"])
            self.assertEqual(p["outcome"], outcome_of(p["ballots"])[0], p["id"])

    def test_the_cut_council_block_follows_its_ballots_beside_the_untouched_first_council(self):
        doc = self._doc()
        self.assertEqual([p["id"] for p in doc["points"]], ["D%d" % i for i in range(1, 12)], "the first council is untouched")
        block = doc.get(CUT_COUNCIL)
        self.assertIsInstance(block, dict, "COUNCIL-1.1.7.json carries the cut council under %r" % CUT_COUNCIL)
        self.assertEqual(council_problems(block), [])
        self.assertEqual([p["id"] for p in block["points"]], CUT_POINTS)
        for words in RULE_WORDS:
            self.assertIn(words, json.dumps(block.get("rule")), words)
        for p in block["points"]:
            self.assertEqual(sorted(p["ballots"]), sorted(MEMBERS), p["id"])
            self.assertEqual(p["outcome"], outcome_of(p["ballots"])[0], p["id"])

    def test_the_owners_amendment_of_d2_is_recorded_beside_its_untouched_ballots(self):
        doc = self._doc()
        d2 = next(p for p in doc["points"] if p["id"] == "D2")
        self.assertEqual((d2["outcome"], d2["chosen"], d2["counts"]), ("CONSENSUS", "B", {"B": 4}))
        amendments = [a for a in doc.get("owner_amendments", []) if a.get("point") == "D2"]
        self.assertEqual(len(amendments), 2, "two owner amendments of D2: OPEN ITEMS 1, then its flip, OPEN ITEMS 6")
        self.assertEqual((amendments[1]["ruling"], amendments[1]["date"]), (RULING_6, "2026-10-19"))
        self.assertTrue(amendments[1]["option"].startswith("B"), amendments[1]["option"])
        self.assertEqual(amendments[0]["ruling"], RULING_1)
        self.assertEqual(amendments[0]["date"], "2026-10-18")
        stabilize = os.path.join(ROOT, SEED_DIR, STABILIZE_FILE)
        if os.path.exists(stabilize):
            self.assertEqual(ruling_of(register_text(os.path.join(SEED_DIR, STABILIZE_FILE)), 1), amendments[0]["ruling"],
                             "STABILIZE.md OPEN ITEMS 1 and the council record carry the same words")

    def test_the_owners_ruling_on_open_item_4_amends_k5_beside_its_untouched_ballots(self):
        doc = self._doc()
        block = doc.get(CUT_COUNCIL)
        self.assertIsInstance(block, dict)
        k5 = next(p for p in block["points"] if p["id"] == "K5")
        self.assertEqual((k5["outcome"], k5["chosen"], k5["counts"]), ("CONSENSUS", "A", {"A": 4}), "K5's ballots stand")
        self.assertEqual(k5["ballots"], {"fable": "A", "opus": "A", "astra": "A", "deepseek": "A", "jev": "ABSTAIN"})
        amendments = [a for a in block.get("owner_amendments", []) if a.get("point") == "K5"]
        self.assertEqual(len(amendments), 1, "one owner amendment of K5 in cut_council")
        self.assertEqual(amendments[0]["ruling"], RULING_4)
        self.assertEqual(amendments[0]["date"], "2026-10-19")
        self.assertTrue(amendments[0]["option"].startswith("B"), amendments[0]["option"])
        # the first council's own amendments: D2 (OPEN ITEMS 1), then D5 and D11 (OPEN ITEMS 5), never K5's
        self.assertEqual([a.get("point") for a in doc.get("owner_amendments", [])], ["D2", "D5", "D11", "D2", "D5"])
        stabilize = os.path.join(SEED_DIR, STABILIZE_FILE)
        if os.path.exists(os.path.join(ROOT, stabilize)):
            text = register_text(stabilize)
            self.assertEqual(ruling_of(text, 4), RULING_4, "STABILIZE.md OPEN ITEMS 4 is RULED with his words")
            self.assertEqual(file_problems(STABILIZE_FILE, text), [])
        cut = os.path.join(SEED_DIR, CUT_FILE)
        if os.path.exists(os.path.join(ROOT, cut)):
            warm = re.search(r"(?m)^- WARM PROOF: .*$", sections(register_text(cut)).get(CUT_SECTION, ""))
            self.assertIsNotNone(warm, "CUT IN MINUTES carries its WARM PROOF line")
            self.assertIn("FIRST rehearsal of a candidate", warm.group(0))
            self.assertIn(PER_LANDING, warm.group(0))

    def test_the_owners_ruling_on_open_item_5_amends_d5_and_d11_beside_their_untouched_ballots(self):
        doc = self._doc()
        points = {p["id"]: p for p in doc["points"]}
        self.assertEqual((points["D5"]["outcome"], points["D5"]["chosen"], points["D5"]["counts"]), ("CONSENSUS", "B", {"B": 4}))
        self.assertEqual((points["D11"]["outcome"], points["D11"]["chosen"], points["D11"]["counts"]), ("CONSENSUS", "A", {"A": 4}))
        for point in ("D5", "D11"):
            amendments = [a for a in doc.get("owner_amendments", []) if a.get("point") == point]
            # D5 carries a second amendment, OPEN ITEMS 7 (2026-10-20), checked in its own test
            self.assertEqual(len(amendments), 2 if point == "D5" else 1, "owner amendments of %s" % point)
            self.assertEqual((amendments[0]["ruling"], amendments[0]["date"]), (RULING_5, "2026-10-19"), point)
            self.assertTrue(amendments[0]["option"].startswith("A"), amendments[0]["option"])
        stabilize = os.path.join(SEED_DIR, STABILIZE_FILE)
        if os.path.exists(os.path.join(ROOT, stabilize)):
            text = register_text(stabilize)
            self.assertEqual(ruling_of(text, 5), RULING_5, "STABILIZE.md OPEN ITEMS 5 is RULED with his words")
            self.assertEqual(file_problems(STABILIZE_FILE, text), [])

    def test_open_item_6_records_the_flip_of_open_item_1_without_the_code(self):
        stabilize = os.path.join(SEED_DIR, STABILIZE_FILE)
        if not os.path.exists(os.path.join(ROOT, stabilize)):
            self.skipTest("STABILIZE.md is hub only")
        text = register_text(stabilize)
        self.assertEqual(ruling_of(text, 6), RULING_6)
        self.assertEqual(ruling_of(text, 1), RULING_1, "item 1 keeps the ruling it had; its flip is item 6")
        self.assertIn("- FLIPPED 2026-10-19:", text)
        self.assertEqual(file_problems(STABILIZE_FILE, text), [])

    def test_open_item_7_is_the_second_amendment_of_d5(self):
        doc = self._doc()
        d5 = [a for a in doc.get("owner_amendments", []) if a.get("point") == "D5"]
        self.assertEqual([(a["ruling"], a["date"]) for a in d5], [(RULING_5, "2026-10-19"), (RULING_7, "2026-10-20")])
        stabilize = os.path.join(SEED_DIR, STABILIZE_FILE)
        if not os.path.exists(os.path.join(ROOT, stabilize)):
            self.skipTest("STABILIZE.md is hub only")
        text = register_text(stabilize)
        self.assertEqual(ruling_of(text, 7), RULING_7)
        self.assertEqual(ruling_of(text, 5), RULING_5, "item 5 keeps the ruling it had; its amendment is item 7")
        self.assertEqual(file_problems(STABILIZE_FILE, text), [])

    def test_open_item_8_amends_item_7_and_cuts_the_core(self):
        stabilize = os.path.join(SEED_DIR, STABILIZE_FILE)
        if not os.path.exists(os.path.join(ROOT, stabilize)):
            self.skipTest("STABILIZE.md is hub only")
        text = register_text(stabilize)
        self.assertEqual(ruling_of(text, 8), RULING_8)
        self.assertEqual(ruling_of(text, 7), RULING_7, "item 7 keeps the ruling it had; its flip is item 8")
        self.assertIn("- FLIPPED 2026-10-20:", text)
        self.assertEqual(file_problems(STABILIZE_FILE, text), [])

    def test_open_item_9_amends_item_8(self):
        stabilize = os.path.join(SEED_DIR, STABILIZE_FILE)
        if not os.path.exists(os.path.join(ROOT, stabilize)):
            self.skipTest("STABILIZE.md is hub only")
        text = register_text(stabilize)
        self.assertEqual(ruling_of(text, 9), RULING_9)
        self.assertEqual(ruling_of(text, 8), RULING_8, "item 8 keeps the ruling it had; its widening is item 9")
        self.assertEqual(file_problems(STABILIZE_FILE, text), [])

    def test_open_item_10_is_ruled_hold_with_his_earlier_words_and_keeps_item_9(self):
        stabilize = os.path.join(SEED_DIR, STABILIZE_FILE)
        if not os.path.exists(os.path.join(ROOT, stabilize)):
            self.skipTest("STABILIZE.md is hub only")
        text = register_text(stabilize)
        self.assertEqual(ruling_of(text, 10), RULING_10)
        for words in OWNER_WORDS_10:
            self.assertIn('"%s"' % words, text)
        self.assertEqual(ruling_of(text, 9), RULING_9, "item 9 stands as ruled")
        self.assertNotIn("- FLIPPED 2026-10-21:", text)
        self.assertEqual(file_problems(STABILIZE_FILE, text), [])

    def test_the_units_file_applies_the_council(self):
        if not os.path.exists(os.path.join(ROOT, UNITS)):
            self.skipTest("%s is not shipped in this tree (export tree)" % UNITS)
        doc = units_document(register_text(UNITS))
        self.assertIsInstance(doc, dict, doc)
        ids = {u["id"]: u for u in doc["units"]}
        self.assertIn(doc.get("certification_gate_unit"), ids)
        self.assertEqual([u["id"] for u in doc["units"] if u["lane"] == "bootstrap"], ["A1", "A2"])
        self.assertEqual(sorted(e["id"] for e in doc["deferred_to_1.1.8"]), ["PP3", "PP4"])
        # OPEN ITEMS 7 seeded the loop working fully for 1.1.8; OPEN ITEMS 9 moved LR1 into 1.1.7's units (counts added 9)
        seeds = doc.get("seeds_for_1.1.8", [])
        self.assertEqual([e["id"] for e in seeds], [])
        self.assertIn("LR1", ids)
        self.assertEqual((doc["counts"]["added"], doc["counts"]["total"]), (9, 76))
        for e in seeds:
            self.assertNotIn(e["id"], ids, e["id"])
            self.assertTrue(e["reason"].strip() and e["unit"]["acceptance"].startswith("python3 -B scripts/"), e["id"])
        for e in doc["merged"] + doc["deferred_to_1.1.8"] + doc["dropped"]:
            self.assertNotIn(e["id"], ids, e["id"])
            self.assertTrue(e["reason"].strip(), e["id"])
        self.assertIn("prototype", ids["DG11"]["negative_case"].lower(), "DG11's negative case is intact")


class TheCouncilJudge(unittest.TestCase):
    def _copy(self):
        return json.loads(json.dumps(GOOD_COUNCIL_DOC))

    def test_the_fixture_is_clean_and_the_rule_counts(self):
        self.assertEqual(council_problems(GOOD_COUNCIL_DOC), [])
        self.assertEqual(council_problems(council_document(json.dumps(GOOD_COUNCIL_DOC))), [])
        self.assertEqual(outcome_of(FOUR_A), ("CONSENSUS", "A", {"A": 4}))
        self.assertEqual(outcome_of(THREE_A), ("MAJORITY", "A", {"A": 3, "B": 1}))
        self.assertEqual(outcome_of(TWO_TWO), ("SPLIT", None, {"A": 2, "B": 2}))
        self.assertEqual(outcome_of(dict.fromkeys(MEMBERS, "A")), ("CONSENSUS", "A", {"A": 5}))
        self.assertEqual(outcome_of(dict.fromkeys(MEMBERS, "NO-DATA")), ("SPLIT", None, {}))

    def test_a_hand_edited_outcome_is_refused(self):
        doc = self._copy()
        doc["points"][0]["outcome"] = "MAJORITY"   # the ballots say 4 of 5
        self.assertEqual(council_problems(doc), ["D1: outcome 'MAJORITY' does not follow from the ballots (CONSENSUS)"])
        doc = self._copy()
        doc["points"][1]["outcome"] = "CONSENSUS"   # the ballots say 3 of 5
        self.assertEqual(council_problems(doc), ["D2: outcome 'CONSENSUS' does not follow from the ballots (MAJORITY)"])
        doc = self._copy()
        doc["points"][1]["counts"] = {"A": 4}
        self.assertEqual(council_problems(doc), ["D2: counts do not match the ballots"])

    def test_a_chosen_option_that_is_not_the_leading_one_is_refused(self):
        doc = self._copy()
        doc["points"][1]["chosen"] = "B"
        doc["points"][1]["dissents"] = ["deepseek", "fable", "opus"]
        self.assertEqual(council_problems(doc), ["D2: chosen 'B' is not the leading option 'A'"])
        doc = self._copy()
        doc["points"][1]["dissents"] = []
        self.assertEqual(council_problems(doc), ["D2: dissents do not match the ballots"])

    def test_a_missing_ballot_or_a_vote_for_no_option_is_refused(self):
        doc = self._copy()
        del doc["points"][0]["ballots"]["opus"]
        self.assertIn("D1: not one ballot per member", council_problems(doc))
        doc = self._copy()
        doc["points"][0]["ballots"]["opus"] = "Z"
        self.assertIn("D1: opus voted 'Z', which is no option", council_problems(doc))
        doc = self._copy()
        doc["members"] = ["fable", "astra", "deepseek", "jev"]
        self.assertEqual(council_problems(doc), ["members are not the five: %s" % ", ".join(MEMBERS)])

    def test_a_majority_or_split_needs_its_five_fields_and_a_split_its_flag(self):
        doc = self._copy()
        for key in DECISION_KEYS:
            copy = json.loads(json.dumps(doc))
            copy["points"][1]["decision"][key] = ""
            self.assertEqual(council_problems(copy), ["D2: decision missing %s" % key], key)
        del doc["points"][1]["decision"]
        self.assertEqual(council_problems(doc), ["D2: no decision record"])
        split = self._copy()
        split["points"].append(_point("D3", TWO_TWO))
        self.assertEqual(council_problems(split), [])
        split["points"][2]["owner_review"] = False
        self.assertEqual(council_problems(split), ["D3: a SPLIT is not flagged for the owner's review"])
        split["points"][2]["owner_review"] = True
        split["points"][2]["chosen"] = "C"
        split["points"][2]["dissents"] = ["deepseek", "fable", "opus"]   # the ballots, none for C
        self.assertEqual(council_problems(split), ["D3: SPLIT chosen 'C' is no option",
                                                   "D3: dissents do not match the ballots"])

    def test_an_owner_amendment_names_a_point_its_ruling_and_its_date(self):
        for key in AMENDMENT_KEYS:
            doc = self._copy()
            doc["owner_amendments"][0][key] = ""
            self.assertEqual(council_problems(doc), ["owner amendment 0: missing %s" % key], key)
        doc = self._copy()
        doc["owner_amendments"][0]["point"] = "D9"
        self.assertEqual(council_problems(doc), ["owner amendment 0: point 'D9' is no point of the council"])
        doc = self._copy()
        doc["owner_amendments"][0]["date"] = "about 18h05"
        self.assertEqual(council_problems(doc), ["owner amendment 0: date 'about 18h05' is not YYYY-MM-DD"])
        doc = self._copy()
        doc["owner_amendments"] = {"D1": "x"}
        self.assertEqual(council_problems(doc), ["owner_amendments is not a list"])
        doc = self._copy()
        doc["owner_amendments"] = [3]
        self.assertEqual(council_problems(doc), ["owner amendment 0 is not an object"])
        doc = self._copy()
        del doc["owner_amendments"]
        self.assertEqual(council_problems(doc), [], "a council the owner never amended")

    def test_dashes_credits_and_hostile_inputs(self):
        doc = self._copy()
        doc["points"][0]["question"] = "fast " + chr(0x2014) + " slow"
        self.assertEqual(council_problems(doc), ["dash in the council document"])
        doc = self._copy()
        doc["points"][0]["question"] = "Drafted by a model"
        self.assertEqual(council_problems(doc), ["credit in the council document"])
        for bad in (None, "", "   ", 5, "not json", "[1, 2]", '{"points": 3}'):
            doc = council_document(bad)
            self.assertIsInstance(doc, str, bad)
            self.assertTrue(doc.startswith("NO-DATA:"), doc)
            problems = council_problems(doc)
            self.assertEqual(len(problems), 1)
            self.assertTrue(problems[0].startswith("NO-DATA:"), problems)
        self.assertEqual(council_problems([]), ["NO-DATA: the council document is not an object with a points list"])
        self.assertEqual(council_problems({"points": []}), ["members are not the five: %s" % ", ".join(MEMBERS),
                                                            "no point recorded"])
        self.assertIn("point 0 is not an object", council_problems({"members": list(MEMBERS), "points": [7]}))


if __name__ == "__main__":
    sys.exit(unittest.main(verbosity=1))

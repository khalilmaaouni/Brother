#!/usr/bin/env python3
"""Thirteen INDEPENDENT identity-versus-relation test classes for
scripts/jbeq_decide.py decide(), written test first per sections 15 to 20 of
~/.claude/evidence/FIX-DIRECTIVE-2026-09-06.md.

WHAT THIS DRIVES BACKWARDS. Every one of the 13 invented classes in
benchmarks/jbeq/mdm/identity-classes-2026-09-06.json is run through
decide() and checked against that class's own expected answer. None of the
13 is copied from the frozen benchmark benchmarks/jbeq/mdm/seed-2026-09-05.json,
which this file (and the fixture it loads) reads only to learn the fact-sheet
SHAPE and the decision vocabulary, never a case.

THE DIRECTIVE'S PRINCIPLE, tested class by class: legal identity does not
imply operational-object identity. AUTO-MERGE only with same object type,
compatible lifecycle, no contradictory identifiers, aligned authoritative
identifiers, no site or store distinction, no temporal conflict, no
keep-separate rule; relationship without identity is LINK AS RELATED;
insufficient evidence is ESCALATE; contradiction is KEEP SEPARATE or REJECT
MATCH.

ALL THIRTEEN ARE GREEN as of the round 5 schema growth (2026-09-06). IC-10
(temporal identity change) used to be the one known red case: decide()'s
track-unsupported branch returned NO-DATA for every "temporal" track case,
because UNSUPPORTED_TRACKS carried it. Round 5 (design-p0-3-mdm-merge-
safety-2026-09-06 section B) removed "temporal" from that set and grew the
fact sheet with an effective_dates field the merge-safety gate reads, so the
temporal track is decided by the ordinary rules like any other track; IC-10
itself carries no stated relation and no effective_dates conflict, so it
now reaches the same default rule (boundary rule 1) that answers KEEP
SEPARATE for "nothing stated". The fixture's 13 fact sheets were also grown
with the round 5 identity-kind fields (object_type_a/object_type_b,
lifecycle, requested_action, requested_relation_type,
authoritative_identifier, contradicted_attributes,
distinct_operational_attributes, location_comparison, effective_dates), at
a default value that does not change which rule fires for any of the
twelve already-passing classes. Two of those twelve (IC-09, IC-11) still
have no field in fact-sheet-schema.json to state their scenario directly
(no commercial-group-versus-legal-group distinction, no manual-override
flag), so they are modeled as the nearest fact pattern the engine's own
rules already read (a blank corroborated field, weak unvalidated evidence)
rather than inventing a field decide() does not read; their "why" fields
say so.
"""
import json
import os
import subprocess
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import jbeq_decide  # noqa: E402

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
IDENTITY_CLASSES = os.path.join(
    REPO, "benchmarks", "jbeq", "mdm", "identity-classes-2026-09-06.json"
)
SEED_PATH = os.path.join(
    REPO, "benchmarks", "jbeq", "mdm", "seed-2026-09-05.json"
)


def load_identity_classes():
    with open(IDENTITY_CLASSES, encoding="utf-8") as fh:
        return json.load(fh)


def load_seed_raw_text():
    with open(SEED_PATH, encoding="utf-8") as fh:
        return fh.read()


class TestIdentityClasses(unittest.TestCase):
    """One test per IC-01..IC-13 class, each asserting decide() against
    that class's expected answer from the directive's principle, not from
    today's rule table (see the module docstring for the one known RED)."""

    @classmethod
    def setUpClass(cls):
        cls.cases = {c["id"]: c for c in load_identity_classes()["cases"]}

    def _check(self, case_id):
        case = self.cases[case_id]
        result = jbeq_decide.decide(case["fact_sheet"])
        self.assertEqual(
            result["answer"], case["expected"],
            "%s (%s): expected %s, got %s (rule %s: %s)"
            % (case_id, case["name"], case["expected"], result["answer"],
               result["rule_fired"], result["why"]),
        )

    def test_IC_01_same_corporate_number_two_stores(self):
        self._check("IC-01")

    def test_IC_02_same_address_different_legal_entities(self):
        self._check("IC-02")

    def test_IC_03_same_legal_entity_multiple_commercial_accounts(self):
        self._check("IC-03")

    def test_IC_04_closed_store_and_newly_opened_nearby_store(self):
        self._check("IC-04")

    def test_IC_05_parent_and_subsidiary(self):
        self._check("IC-05")

    def test_IC_06_payer_and_sold_to(self):
        self._check("IC-06")

    def test_IC_07_ship_to_and_store(self):
        self._check("IC-07")

    def test_IC_08_branch_and_head_office(self):
        self._check("IC-08")

    def test_IC_09_commercial_group_versus_legal_group_disagree(self):
        self._check("IC-09")

    def test_IC_10_temporal_identity_change(self):
        # Fixed round 5 (2026-09-06): "temporal" left UNSUPPORTED_TRACKS, so
        # this case now reaches the ordinary rules instead of the old
        # blanket NO-DATA. See the module docstring for the full history.
        self._check("IC-10")

    def test_IC_11_manual_override_present(self):
        self._check("IC-11")

    def test_IC_12_identifier_reuse(self):
        self._check("IC-12")

    def test_IC_13_store_relocation(self):
        self._check("IC-13")


class TestNonReuseOfFrozenSeed(unittest.TestCase):
    """Guards the benchmark's non-reuse rule: this file is invented, never
    copied from the frozen seed. Checked against the seed's own raw text so
    a future edit to either file cannot silently drift past the check."""

    @classmethod
    def setUpClass(cls):
        cls.cases = load_identity_classes()["cases"]
        with open(SEED_PATH, encoding="utf-8") as fh:
            seed = json.load(fh)
        cls.seed_ids = {c["id"] for c in seed["cases"]}
        cls.seed_raw = load_seed_raw_text()

    def test_no_case_id_name_or_why_reused_from_the_frozen_seed(self):
        for case in self.cases:
            with self.subTest(case=case["id"]):
                self.assertNotIn(
                    case["id"], self.seed_ids,
                    "%s: case id collides with a frozen seed case id" % case["id"],
                )
                self.assertNotIn(
                    case["name"], self.seed_raw,
                    "%s: class name %r appears verbatim in the frozen seed"
                    % (case["id"], case["name"]),
                )
                # "expected label string": the one-sentence rationale that
                # names the expected answer for THIS case, checked whole
                # (never the bare answer word, which is shared vocabulary
                # repeated across the whole seed and would always match).
                self.assertNotIn(
                    case["why"], self.seed_raw,
                    "%s: why-sentence appears verbatim in the frozen seed"
                    % case["id"],
                )


class TestValidatesAgainstSchema(unittest.TestCase):
    """jbeq_decide.py's own validator (cmd_validate, invoked as the CLI
    'validate' subcommand) is the validator named in scripts/jbeq_decide.py;
    no separate validator exists in scripts/jbeq_mdm.py. Every fact sheet in
    the identity-classes file must pass it: every REQUIRED_FIELDS key
    present on every one of the 13 cases."""

    def test_cli_validate_accepts_every_fact_sheet(self):
        cases = load_identity_classes()["cases"]
        sheets = {c["id"]: c["fact_sheet"] for c in cases}
        with tempfile.TemporaryDirectory() as tmp:
            sheets_path = os.path.join(tmp, "fact-sheets.json")
            with open(sheets_path, "w", encoding="utf-8") as fh:
                json.dump(sheets, fh, ensure_ascii=False)
            proc = subprocess.run(
                [sys.executable, os.path.join(REPO, "scripts", "jbeq_decide.py"),
                 "validate", sheets_path],
                capture_output=True, text=True,
            )
            self.assertEqual(
                proc.returncode, jbeq_decide.EXIT_OK,
                "validate refused: %s%s" % (proc.stdout, proc.stderr),
            )
            self.assertIn("13 fact sheet(s) OK", proc.stdout)


if __name__ == "__main__":
    unittest.main()

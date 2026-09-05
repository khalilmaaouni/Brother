#!/usr/bin/env python3
"""Tests for scripts/jbeq_decide.py, the JBEQ-MDM decision engine.

WHAT THIS DRIVES BACKWARDS. Every one of the 9 invented cases in
benchmarks/jbeq/mdm/generalization-cases-2026-09-05.json gets a fact sheet
authored BY HAND from that case's own prompt text (never from its expected
answer or rationale), run through decide(), and checked against the case's
own expected answer. This never touches the frozen JBEQ-MDM seed.

Plus negative tests for the rules the diagnosis this module was built from
named by hand: a weak-evidence irreversible merge never yields a merge term,
a stated tenant boundary always yields REJECT MATCH, a blank field with a
corroborating fact yields ESCALATE and without one yields NO-DATA, three
hierarchy parents of different types yield KEEP SEPARATE, and a fact sheet
missing a required field yields NO-DATA naming it.

MUTATION CONTROL. This suite is also run once with JBEQ_DECIDE_DISABLE_RULES
set, which is the standing hook jbeq_decide.decide() checks (see its
docstring) to skip the rule table entirely. That run is not part of this
file's own pass/fail contract: it is invoked separately
(`JBEQ_DECIDE_DISABLE_RULES=1 python3 scripts/test_jbeq_decide.py -v`) to
prove the rule table matters, and at least one test here is expected to fail
under it.
"""
import json
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import jbeq_decide  # noqa: E402

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
GENERALIZATION_CASES = os.path.join(
    REPO, "benchmarks", "jbeq", "mdm", "generalization-cases-2026-09-05.json"
)

ALL_ANSWERS = ["AUTO-MERGE", "SUGGEST MERGE", "LINK AS RELATED", "KEEP SEPARATE",
               "REJECT MATCH", "ESCALATE", "NO-DATA"]

# E100: one base fact sheet every test starts from and overrides. This keeps
# every REQUIRED_FIELDS key present without repeating all fourteen of them in
# every test, and it is verified (test_base_sheet_has_every_required_field)
# to actually carry them all so the shortcut cannot silently rot.
BASE_SHEET = {
    "track": "entity-object",
    "allowed_answers": list(ALL_ANSWERS),
    "identifiers": [],
    "stated_relation": "none",
    "stated_difference": "none",
    "tenant_boundary": "none",
    "evidence_strength": None,
    "evidence_reasons": [],
    "corroborating_facts": [],
    "blank_fields": [],
    "irreversible": False,
    "history_exists": False,
    "hierarchy_parents": [],
    "one_to_many_object": False,
}


def sheet(**overrides):
    s = dict(BASE_SHEET)
    s.update(overrides)
    return s


def load_generalization_cases():
    with open(GENERALIZATION_CASES, encoding="utf-8") as fh:
        return json.load(fh)


class TestBaseSheet(unittest.TestCase):
    def test_base_sheet_has_every_required_field(self):
        missing = jbeq_decide._missing_fields(BASE_SHEET)
        self.assertEqual(missing, [], "BASE_SHEET must carry every required field")


class TestGeneralizationCases(unittest.TestCase):
    """One fact sheet per invented case in generalization-cases-2026-09-05.json,
    hand-authored from that case's own 'input' text. No sibling A-to-D
    generalization file was found pushed to origin/wbs/jbeq-rules-a-to-d at
    the time this suite was written (checked via `git show` against that
    branch, which still matched origin/main), so this file covers the 9
    cases that exist and the A-to-D rules are exercised by the negative
    tests below instead, built from the diagnosis text's own rule
    statements.
    """

    @classmethod
    def setUpClass(cls):
        cls.cases = {c["id"]: c for c in load_generalization_cases()["cases"]}

    def _check(self, case_id, fact_sheet):
        case = self.cases[case_id]
        result = jbeq_decide.decide(fact_sheet)
        self.assertEqual(
            result["answer"], case["expected"],
            "%s: expected %s, got %s (rule %s: %s)"
            % (case_id, case["expected"], result["answer"],
               result["rule_fired"], result["why"]),
        )

    # G5: same address only, one side a one-to-many object -> KEEP SEPARATE.
    def test_G5_01_bookstore_shared_delivery_hub(self):
        self._check("G5-01", sheet(
            track="entity-object",
            allowed_answers=self.cases["G5-01"]["allowed"],
            stated_relation="same_site_only",
            one_to_many_object=True,
        ))

    def test_G5_02_factory_shared_tech_center(self):
        self._check("G5-02", sheet(
            track="entity-object",
            allowed_answers=self.cases["G5-02"]["allowed"],
            stated_relation="same_site_only",
            one_to_many_object=True,
        ))

    def test_G5_03_pharmacy_shared_call_center(self):
        self._check("G5-03", sheet(
            track="entity-object",
            allowed_answers=self.cases["G5-03"]["allowed"],
            stated_relation="same_site_only",
            one_to_many_object=True,
        ))

    # G6: shared reading only, no stated refuting fact -> KEEP SEPARATE.
    def test_G6_01_hayashi_reading_match_only(self):
        self._check("G6-01", sheet(
            track="match-or-no-merge",
            allowed_answers=self.cases["G6-01"]["allowed"],
            stated_relation="none",
            stated_difference="none",
        ))

    def test_G6_02_tanaka_department_differs(self):
        self._check("G6-02", sheet(
            track="match-or-no-merge",
            allowed_answers=self.cases["G6-02"]["allowed"],
            stated_relation="none",
            stated_difference="none",
        ))

    def test_G6_03_minami_one_digit_address(self):
        self._check("G6-03", sheet(
            track="match-or-no-merge",
            allowed_answers=self.cases["G6-03"]["allowed"],
            stated_relation="none",
            stated_difference="none",
        ))

    # G7: same external entity, separate company-code records -> LINK AS RELATED.
    def test_G7_01_hibari_two_company_codes(self):
        self._check("G7-01", sheet(
            track="hierarchy",
            allowed_answers=self.cases["G7-01"]["allowed"],
            stated_relation="group_company_code_shared_entity",
        ))

    def test_G7_02_watari_three_local_entities(self):
        self._check("G7-02", sheet(
            track="hierarchy",
            allowed_answers=self.cases["G7-02"]["allowed"],
            stated_relation="group_company_code_shared_entity",
        ))

    def test_G7_03_sekino_two_subsidiaries(self):
        self._check("G7-03", sheet(
            track="hierarchy",
            allowed_answers=self.cases["G7-03"]["allowed"],
            stated_relation="group_company_code_shared_entity",
        ))


class TestNegatives(unittest.TestCase):
    MERGE_TERMS = {"AUTO-MERGE", "SUGGEST MERGE"}

    def test_weak_evidence_irreversible_never_merges(self):
        result = jbeq_decide.decide(sheet(
            track="match-or-no-merge",
            evidence_strength="weak",
            evidence_reasons=["score_only", "missing_identifier"],
            irreversible=True,
        ))
        self.assertNotIn(result["answer"], self.MERGE_TERMS)
        self.assertEqual(result["answer"], "ESCALATE")
        self.assertEqual(result["rule_fired"], "B")

    def test_stated_tenant_boundary_always_rejects(self):
        result = jbeq_decide.decide(sheet(
            track="match-or-no-merge",
            tenant_boundary="stated",
            evidence_strength="strong",
        ))
        self.assertEqual(result["answer"], "REJECT MATCH")
        self.assertEqual(result["rule_fired"], "C")

    def test_blank_field_with_corroboration_escalates(self):
        result = jbeq_decide.decide(sheet(
            track="address",
            blank_fields=["prefecture"],
            corroborating_facts=["banchi_and_postal_code_match"],
        ))
        self.assertEqual(result["answer"], "ESCALATE")
        self.assertEqual(result["rule_fired"], "A")

    def test_blank_field_without_corroboration_is_nodata(self):
        result = jbeq_decide.decide(sheet(
            track="address",
            blank_fields=["prefecture"],
            corroborating_facts=[],
        ))
        self.assertEqual(result["answer"], "NO-DATA")
        self.assertEqual(result["rule_fired"], "A")

    def test_three_hierarchy_types_keep_separate(self):
        result = jbeq_decide.decide(sheet(
            track="hierarchy",
            hierarchy_parents=[
                {"type": "capital", "parent": "A"},
                {"type": "trade_flow", "parent": "B"},
                {"type": "reporting", "parent": "C"},
            ],
        ))
        self.assertEqual(result["answer"], "KEEP SEPARATE")
        self.assertEqual(result["rule_fired"], "D")

    def test_missing_required_field_is_nodata_naming_it(self):
        s = sheet()
        del s["tenant_boundary"]
        result = jbeq_decide.decide(s)
        self.assertEqual(result["answer"], "NO-DATA")
        self.assertEqual(result["rule_fired"], "validation")
        self.assertIn("tenant_boundary", result["why"])

    def test_unsupported_track_is_honest_nodata(self):
        result = jbeq_decide.decide(sheet(track="survivorship"))
        self.assertEqual(result["answer"], "NO-DATA")
        self.assertEqual(result["rule_fired"], "track-unsupported")

    def test_answer_outside_allowed_set_is_nodata(self):
        result = jbeq_decide.decide(sheet(
            track="hierarchy",
            allowed_answers=["ESCALATE", "NO-DATA"],
            stated_relation="group_company_code_shared_entity",
        ))
        self.assertEqual(result["answer"], "NO-DATA")
        self.assertEqual(result["rule_fired"], "safety-net")


class TestCLI(unittest.TestCase):
    def test_validate_and_decide_round_trip(self):
        import subprocess
        import tempfile

        cases = load_generalization_cases()["cases"]
        sheets = {}
        for case in cases:
            relation = "same_site_only" if case["track"] == "entity-object" else (
                "group_company_code_shared_entity" if case["track"] == "hierarchy"
                else "none"
            )
            sheets[case["id"]] = sheet(
                track=case["track"],
                allowed_answers=case["allowed"],
                stated_relation=relation,
                one_to_many_object=(relation == "same_site_only"),
            )
        with tempfile.TemporaryDirectory() as tmp:
            sheets_path = os.path.join(tmp, "fact-sheets.json")
            out_path = os.path.join(tmp, "answers.json")
            decisions_path = os.path.join(tmp, "decisions.jsonl")
            with open(sheets_path, "w", encoding="utf-8") as fh:
                json.dump(sheets, fh)

            rc = subprocess.call(
                [sys.executable, os.path.join(REPO, "scripts", "jbeq_decide.py"),
                 "validate", sheets_path]
            )
            self.assertEqual(rc, 0)

            rc = subprocess.call(
                [sys.executable, os.path.join(REPO, "scripts", "jbeq_decide.py"),
                 "decide", sheets_path, "--out", out_path,
                 "--decisions", decisions_path]
            )
            self.assertEqual(rc, 0)
            with open(out_path, encoding="utf-8") as fh:
                answers = json.load(fh)
            self.assertEqual(set(answers), set(sheets))
            with open(decisions_path, encoding="utf-8") as fh:
                lines = [json.loads(line) for line in fh if line.strip()]
            self.assertEqual(len(lines), len(sheets))

    def test_validate_refuses_missing_field(self):
        import subprocess
        import tempfile

        s = sheet()
        del s["blank_fields"]
        with tempfile.TemporaryDirectory() as tmp:
            sheets_path = os.path.join(tmp, "fact-sheets.json")
            with open(sheets_path, "w", encoding="utf-8") as fh:
                json.dump({"X-01": s}, fh)
            rc = subprocess.call(
                [sys.executable, os.path.join(REPO, "scripts", "jbeq_decide.py"),
                 "validate", sheets_path]
            )
            self.assertEqual(rc, jbeq_decide.EXIT_NOT_DECIDED)


if __name__ == "__main__":
    unittest.main()

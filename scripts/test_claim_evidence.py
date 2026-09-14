#!/usr/bin/env python3
"""Tests for claim_evidence.py (WBS-50.02)."""
import json
import os
import sys
import tempfile
import unittest

sys.path.insert(0, __file__.rsplit("/", 1)[0])
import claim_evidence as CE
import contract_check as CC

BASE = {
    "schema_version": "claim-evidence-v1", "claim_id": "c1",
    "revision_id": "r1", "evidence": [],
}


def rec(**overrides):
    r = dict(BASE)
    r.update(overrides)
    return r


def item(**overrides):
    it = {"id": "e1", "ref": "file:docs/plan/foo.md:1-10", "note": "explains the claim"}
    it.update(overrides)
    return it


class SchemaShapeTests(unittest.TestCase):
    def setUp(self):
        self.schema = CC.load_json(CE.DEFAULT_SCHEMA, "schema")

    def test_an_empty_evidence_list_passes(self):
        self.assertEqual(CE.check(rec(), self.schema), [])

    def test_a_well_formed_evidence_entry_passes(self):
        self.assertEqual(CE.check(rec(evidence=[item()]), self.schema), [])

    def test_multiple_well_formed_entries_pass(self):
        entries = [item(id="e1"), item(id="e2", ref="evidence:some-name"),
                   item(id="e3", ref="url:https://example.com/x")]
        self.assertEqual(CE.check(rec(evidence=entries), self.schema), [])

    def test_missing_required_top_level_field_is_refused(self):
        r = rec()
        del r["claim_id"]
        problems = CE.check(r, self.schema)
        self.assertTrue(any("claim_id" in p for p in problems), problems)

    def test_wrong_schema_version_is_refused(self):
        problems = CE.check(rec(schema_version="wrong"), self.schema)
        self.assertTrue(problems)

    def test_additional_top_level_property_is_refused(self):
        r = rec()
        r["not_a_real_field"] = "x"
        problems = CE.check(r, self.schema)
        self.assertTrue(problems)

    def test_evidence_entry_missing_required_field_is_refused(self):
        it = item()
        del it["ref"]
        problems = CE.check(rec(evidence=[it]), self.schema)
        self.assertTrue(any("ref" in p for p in problems), problems)

    def test_evidence_entry_with_raw_artifact_field_is_refused(self):
        # additionalProperties:false on the evidence item is the mechanical
        # guard against pasting a raw artifact body in place of a
        # reference: any field beyond id/ref/note is refused outright.
        it = item(content="the entire raw artifact bytes go here")
        problems = CE.check(rec(evidence=[it]), self.schema)
        self.assertTrue(any("unexpected field" in p for p in problems), problems)

    def test_evidence_key_present_but_empty_still_passes(self):
        self.assertEqual(CE.check(rec(evidence=[]), self.schema), [])


class RefGrammarTests(unittest.TestCase):
    def setUp(self):
        self.schema = CC.load_json(CE.DEFAULT_SCHEMA, "schema")

    def test_file_prefix_is_accepted(self):
        problems = CE.check(rec(evidence=[item(ref="file:a/b.md:1-2")]), self.schema)
        self.assertEqual(problems, [])

    def test_evidence_prefix_is_accepted(self):
        problems = CE.check(rec(evidence=[item(ref="evidence:some-capture")]), self.schema)
        self.assertEqual(problems, [])

    def test_url_prefix_is_accepted(self):
        problems = CE.check(rec(evidence=[item(ref="url:https://example.com/x")]), self.schema)
        self.assertEqual(problems, [])

    def test_bare_path_with_no_prefix_is_refused(self):
        problems = CE.check(rec(evidence=[item(ref="docs/plan/foo.md")]), self.schema)
        self.assertTrue(any("ref" in p and "must start with" in p for p in problems), problems)

    def test_unknown_prefix_is_refused(self):
        problems = CE.check(rec(evidence=[item(ref="s3:bucket/key")]), self.schema)
        self.assertTrue(any("ref" in p and "must start with" in p for p in problems), problems)

    def test_prefixes_match_contract_check_exactly(self):
        # This module must reuse contract_check's grammar, never redeclare
        # a second copy that could drift from it.
        self.assertIs(CE.RECEIPT_REF_PREFIXES, CC.RECEIPT_REF_PREFIXES)


class NonEmptyNoteTests(unittest.TestCase):
    def setUp(self):
        self.schema = CC.load_json(CE.DEFAULT_SCHEMA, "schema")

    def test_empty_note_is_refused(self):
        problems = CE.check(rec(evidence=[item(note="")]), self.schema)
        self.assertTrue(any("note" in p for p in problems), problems)

    def test_whitespace_only_note_is_refused(self):
        problems = CE.check(rec(evidence=[item(note="   ")]), self.schema)
        self.assertTrue(any("note" in p for p in problems), problems)

    def test_non_empty_note_is_accepted(self):
        problems = CE.check(rec(evidence=[item(note="reconciliation ran clean")]), self.schema)
        self.assertEqual(problems, [])


class UniqueIdsTests(unittest.TestCase):
    def setUp(self):
        self.schema = CC.load_json(CE.DEFAULT_SCHEMA, "schema")

    def test_duplicate_ids_are_refused(self):
        entries = [item(id="dup", ref="file:a.md:1-1"), item(id="dup", ref="file:b.md:1-1")]
        problems = CE.check(rec(evidence=entries), self.schema)
        self.assertTrue(any("not unique" in p for p in problems), problems)

    def test_distinct_ids_are_accepted(self):
        entries = [item(id="e1"), item(id="e2")]
        problems = CE.check(rec(evidence=entries), self.schema)
        self.assertEqual(problems, [])

    def test_three_way_duplicate_reports_the_repeats_not_the_first(self):
        entries = [item(id="dup"), item(id="dup"), item(id="dup")]
        problems = CE.check(rec(evidence=entries), self.schema)
        dup_problems = [p for p in problems if "not unique" in p]
        self.assertEqual(len(dup_problems), 2, problems)


class CheckMatchesClaimTests(unittest.TestCase):
    def test_matching_claim_id_and_revision_id_passes(self):
        evidence_record = rec(claim_id="c1", revision_id="r1")
        claim_record = {"claim_id": "c1", "revision_id": "r1", "state": "FROZEN"}
        self.assertEqual(CE.check_matches_claim(evidence_record, claim_record), [])

    def test_mismatched_claim_id_is_refused(self):
        evidence_record = rec(claim_id="c1", revision_id="r1")
        claim_record = {"claim_id": "c2", "revision_id": "r1"}
        problems = CE.check_matches_claim(evidence_record, claim_record)
        self.assertTrue(any("claim_id" in p for p in problems), problems)

    def test_mismatched_revision_id_is_refused(self):
        evidence_record = rec(claim_id="c1", revision_id="r1")
        claim_record = {"claim_id": "c1", "revision_id": "r2"}
        problems = CE.check_matches_claim(evidence_record, claim_record)
        self.assertTrue(any("revision_id" in p for p in problems), problems)

    def test_a_corrections_evidence_record_never_matches_the_corrected_revision(self):
        # WBS-50.01: a correction gets a new revision_id. Its own evidence
        # record must name that new revision_id, not the corrected one.
        evidence_record = rec(claim_id="c1", revision_id="r2")
        corrected_claim_record = {"claim_id": "c1", "revision_id": "r1"}
        problems = CE.check_matches_claim(evidence_record, corrected_claim_record)
        self.assertTrue(any("revision_id" in p for p in problems), problems)

    def test_both_mismatched_reports_both_problems(self):
        evidence_record = rec(claim_id="c1", revision_id="r1")
        claim_record = {"claim_id": "different", "revision_id": "different"}
        problems = CE.check_matches_claim(evidence_record, claim_record)
        self.assertEqual(len(problems), 2, problems)


class MainCliTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()

    def write(self, name, record):
        path = os.path.join(self.tmp, name)
        with open(path, "w") as fh:
            json.dump(record, fh)
        return path

    def test_main_exits_0_on_pass(self):
        path = self.write("r.json", rec())
        self.assertEqual(CE.main([path]), 0)

    def test_main_exits_2_on_no_data_for_missing_record(self):
        self.assertEqual(CE.main(["/no/such/record.json"]), 2)

    def test_main_exits_1_on_structural_fail(self):
        r = rec()
        del r["evidence"]
        path = self.write("r.json", r)
        self.assertEqual(CE.main([path]), 1)

    def test_main_exits_1_on_hand_rule_fail(self):
        path = self.write("r.json", rec(evidence=[item(ref="no-prefix-here")]))
        self.assertEqual(CE.main([path]), 1)

    def test_main_with_matching_claim_record_exits_0(self):
        evidence_path = self.write("evidence.json", rec(claim_id="c1", revision_id="r1"))
        claim_path = self.write("claim.json", {
            "schema_version": "claim-lifecycle-v1", "claim_id": "c1",
            "revision_id": "r1", "state": "FROZEN", "supersedes": None,
        })
        self.assertEqual(CE.main([evidence_path, "--claim-record", claim_path]), 0)

    def test_main_with_mismatched_claim_record_exits_1(self):
        evidence_path = self.write("evidence.json", rec(claim_id="c1", revision_id="r1"))
        claim_path = self.write("claim.json", {
            "schema_version": "claim-lifecycle-v1", "claim_id": "c1",
            "revision_id": "r2", "state": "FROZEN", "supersedes": None,
        })
        self.assertEqual(CE.main([evidence_path, "--claim-record", claim_path]), 1)

    def test_main_exits_2_on_missing_claim_record_path(self):
        evidence_path = self.write("evidence.json", rec())
        self.assertEqual(
            CE.main([evidence_path, "--claim-record", "/no/such/claim.json"]), 2)


if __name__ == "__main__":
    unittest.main()

#!/usr/bin/env python3
"""Tests for merge_passport.py (WBS-40.07).

Feeds real outputs from the real sibling modules (master_source_snapshot,
normalization_trace, reversibility_gate, golden_master_contract) into
compose_passport(), rather than hand-typing a fake evidence dict that does
not match what those modules actually return.
"""
import copy
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

import merge_passport as mp  # noqa: E402
import master_source_snapshot as mss  # noqa: E402
import normalization_trace as nt  # noqa: E402
import reversibility_gate as rg  # noqa: E402
import golden_master_contract as GMC  # noqa: E402
import contract_check as CC  # noqa: E402
from test_master_source_snapshot import make_fixture_xlsx  # noqa: E402

SCRIPT = os.path.join(HERE, "merge_passport.py")

# Synthetic fixture data only -- fake customer records with placeholder
# names, never anything resembling a real client's data (same convention as
# test_reversibility_gate.py).
PRE_MERGE_RECORDS = {
    "src-crm-001": {"customer_name": "Acme Test Corp", "phone": "03-1234-5678"},
    "src-pos-042": {"customer_name": "Acme Test Corp Ltd.", "phone": "0312345678"},
}
SURVIVORSHIP_DECISIONS = {
    "customer_name": {"value": "Acme Test Corp", "winner_source_id": "src-crm-001"},
    "phone": {"value": "0312345678", "winner_source_id": "src-pos-042"},
}

VALID_OUTCOME = {
    "schema_version": "outcome-contract-v1", "project": {
        "project_id": "p", "name": "p",
        "provenance": {"project_id": "ask", "name": "ask"}},
    "language": "en", "question": "q", "success_checks": [
        {"id": "s", "command": "true", "expect": "exit-0"}],
    "must_answer": [], "affected_products": ["brother"], "ticket": None,
    "audit": {"required": False, "manifest": None}, "persona": "developer",
    "state": "contracted", "receipts": [], "questions": [], "history": [],
    "decision": None,
}

BASE_MASTER = {
    "schema_version": "golden-master-contract-v1", "master_id": "m1",
    "entity_type": "customer/location", "business_purpose": "x",
    "source_systems": ["src-crm-001", "src-pos-042"], "downstream_consumers": [],
    "critical_attributes": ["customer_name", "phone"], "cardinality_constraints": [],
    "false_merge_cost_class": "high", "missed_match_cost_class": "medium",
    "review_policy": "human review above threshold", "merge_threshold": 0.9,
    "review_threshold": 0.5, "survivorship_policy_ref": "docs/x.md",
    "rollback_required": True, "quality_claim_ids": [],
    "locale_profiles": ["ja-JP"], "publish_authority": "HUMAN",
}


class ComposeFixtureMixin:
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.outcome_path = os.path.join(self.tmp, "outcome.json")
        with open(self.outcome_path, "w", encoding="utf-8") as fh:
            json.dump(VALID_OUTCOME, fh)
        self.schema = CC.load_json(GMC.DEFAULT_SCHEMA, "schema")

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def contract(self, **overrides):
        rec = dict(BASE_MASTER, outcome_contract_ref=self.outcome_path)
        rec.update(overrides)
        return rec

    def source_snapshots(self):
        path = os.path.join(self.tmp, "generic-source.xlsx")
        make_fixture_xlsx(path, sheet1_rows=3, sheet2_rows=5)
        return [mss.build_snapshot(path, source_system_id="src-crm-001")]

    def normalization_traces(self):
        return [
            nt.trace("Ａｃｍｅ　Ｔｅｓｔ　Ｃｏｒｐ　株式会社", nt.default_chain(), "ja-JP"),
            nt.trace("03ー1234ー5678", nt.default_chain(), "ja-JP"),
        ]

    def reversibility_candidate(self, propagated=False, compensation_plan=None):
        return rg.build_reversible_action(
            affected_entity_set=["src-crm-001", "src-pos-042"],
            pre_merge_records=copy.deepcopy(PRE_MERGE_RECORDS),
            survivorship_decisions=SURVIVORSHIP_DECISIONS,
            downstream_propagation_status="propagated" if propagated else "not_propagated",
            compensation_plan=compensation_plan,
        )

    def candidate_generation(self):
        return {"pathway": "blocking+ml", "model": "generic-matcher-v1", "score": 0.93}

    def risk(self):
        return {"false_merge_cost": "catastrophic", "shared_key_hub": True, "cluster_size_after": 4, "bridge_edge": False}

    def review(self):
        return {"required": True, "result": "approved by data steward"}

    def survivorship(self):
        return {"attributes": [
            {"value": "Acme Test Corp", "source_system": "src-crm-001", "rule": "most recent wins"},
            {"value": "0312345678", "source_system": "src-pos-042", "rule": "digits-only wins"},
        ]}

    def compose_all(self, **overrides):
        kwargs = dict(
            master_id="m1",
            golden_master_contract_record=self.contract(),
            source_snapshots=self.source_snapshots(),
            normalization_traces=self.normalization_traces(),
            candidate_generation=self.candidate_generation(),
            risk_assessment=self.risk(),
            review_record=self.review(),
            reversibility_candidate=self.reversibility_candidate(),
            survivorship_decision=self.survivorship(),
        )
        kwargs.update(overrides)
        return mp.compose_passport(**kwargs)


class FullCompositionTests(ComposeFixtureMixin, unittest.TestCase):
    def test_full_composition_from_real_sibling_outputs_passes(self):
        passport = self.compose_all()
        self.assertEqual(passport["verdict"], "PASS")
        self.assertFalse(passport["refused"])
        self.assertEqual(passport["status"], "COMPOSED")
        for field in ("source_snapshot", "normalization_trace", "candidate_generation",
                      "risk", "review", "rollback", "survivorship", "authority"):
            self.assertEqual(passport["field_verdicts"][field], "PASS", field)

    def test_authority_reads_from_the_real_contract_never_a_raw_string(self):
        passport = self.compose_all()
        self.assertEqual(passport["authority"]["publish"], "HUMAN")
        self.assertEqual(passport["authority"]["verdict"], "PASS")
        # compose_passport has no publish_authority parameter at all.
        self.assertNotIn("publish_authority", mp.compose_passport.__code__.co_varnames)

    def test_invalid_contract_record_fails_authority_never_trusted(self):
        bad_contract = self.contract()
        del bad_contract["publish_authority"]  # required by the schema
        passport = self.compose_all(golden_master_contract_record=bad_contract)
        self.assertEqual(passport["authority"]["verdict"], "FAIL")
        self.assertEqual(passport["authority"]["publish"], "NO-DATA")
        self.assertEqual(passport["verdict"], "FAIL")
        self.assertTrue(passport["refused"])

    def test_rollback_supported_tracks_the_real_reversibility_gate_verdict(self):
        clean = self.reversibility_candidate()
        expected_verdict, expected_reason = rg.evaluate_merge_candidate(clean)
        passport = self.compose_all(reversibility_candidate=clean)
        self.assertEqual(passport["rollback"]["verdict"], expected_verdict)
        self.assertEqual(passport["rollback"]["verdict_reason"], expected_reason)
        self.assertTrue(passport["rollback"]["supported"])
        self.assertEqual(passport["verdict"], "PASS")

    def test_propagated_with_no_compensation_plan_fails_rollback_and_overall(self):
        propagated = self.reversibility_candidate(propagated=True, compensation_plan=None)
        expected_verdict, _ = rg.evaluate_merge_candidate(propagated)
        self.assertEqual(expected_verdict, "FAIL")
        passport = self.compose_all(reversibility_candidate=propagated)
        self.assertEqual(passport["rollback"]["verdict"], "FAIL")
        self.assertFalse(passport["rollback"]["supported"])
        self.assertEqual(passport["verdict"], "FAIL")
        self.assertTrue(passport["refused"])


class MissingRequiredEvidenceTests(ComposeFixtureMixin, unittest.TestCase):
    def test_missing_reversibility_candidate_is_no_data_not_silent_pass(self):
        passport = self.compose_all(reversibility_candidate=None)
        self.assertEqual(passport["field_verdicts"]["rollback"], "NO-DATA")
        self.assertEqual(passport["verdict"], "NO-DATA")
        self.assertTrue(passport["refused"])
        self.assertEqual(passport["status"], "REFUSED")

    def test_missing_candidate_generation_is_no_data_not_silent_pass(self):
        passport = self.compose_all(candidate_generation=None)
        self.assertEqual(passport["field_verdicts"]["candidate_generation"], "NO-DATA")
        self.assertEqual(passport["verdict"], "NO-DATA")
        self.assertTrue(passport["refused"])

    def test_missing_risk_is_no_data_not_silent_pass(self):
        passport = self.compose_all(risk_assessment=None)
        self.assertEqual(passport["field_verdicts"]["risk"], "NO-DATA")
        self.assertEqual(passport["verdict"], "NO-DATA")

    def test_missing_source_snapshots_is_no_data_not_silent_pass(self):
        passport = self.compose_all(source_snapshots=[])
        self.assertEqual(passport["field_verdicts"]["source_snapshot"], "NO-DATA")
        self.assertEqual(passport["verdict"], "NO-DATA")

    def test_malformed_review_required_true_with_no_result_fails(self):
        passport = self.compose_all(review_record={"required": True, "result": ""})
        self.assertEqual(passport["field_verdicts"]["review"], "FAIL")
        self.assertEqual(passport["verdict"], "FAIL")

    def test_optional_evidence_field_missing_never_blocks_overall_pass(self):
        passport = self.compose_all(evidence=None)
        self.assertEqual(passport["field_verdicts"]["evidence"], "NO-DATA")
        self.assertEqual(mp.FIELD_OBLIGATIONS["evidence"], "OPTIONAL")
        self.assertEqual(passport["verdict"], "PASS")


class HollowPassDefenseTests(ComposeFixtureMixin, unittest.TestCase):
    """The concrete defense against a passport that looks complete but
    misrepresents what it actually composed from: recompute a digest of
    the real evidence, and refuse to call it a match if it drifted."""

    def test_unchanged_evidence_verifies_as_a_match(self):
        risk = self.risk()
        passport = self.compose_all(risk_assessment=risk)
        result = mp.verify_passport_evidence(passport, risk_assessment=risk)
        self.assertEqual(result["checks"]["risk"], "MATCH")
        self.assertEqual(result["verdict"], "PASS")

    def test_evidence_that_changed_after_composition_is_refused_not_accepted(self):
        risk = self.risk()
        passport = self.compose_all(risk_assessment=risk)

        tampered_risk = dict(risk, bridge_edge=True, cluster_size_after=400)
        result = mp.verify_passport_evidence(passport, risk_assessment=tampered_risk)
        self.assertIn("MISMATCH", result["checks"]["risk"])
        self.assertEqual(result["verdict"], "FAIL")
        self.assertIn("risk", result["verdict_reason"])

    def test_tampered_contract_claiming_a_different_authority_is_refused(self):
        contract = self.contract()
        passport = self.compose_all(golden_master_contract_record=contract)

        forged_contract = dict(contract, publish_authority="AUTOMATED-NO-REVIEW")
        result = mp.verify_passport_evidence(passport, golden_master_contract_record=forged_contract)
        self.assertIn("MISMATCH", result["checks"]["authority"])
        self.assertEqual(result["verdict"], "FAIL")

    def test_tampered_reversibility_candidate_is_refused(self):
        candidate = self.reversibility_candidate()
        passport = self.compose_all(reversibility_candidate=candidate)

        forged = copy.deepcopy(candidate)
        # candidate already verified matches_pre_state=True; a genuine
        # tamper has to actually change a byte, not restate the same value.
        forged["downstream_propagation_status"] = "propagated"
        result = mp.verify_passport_evidence(passport, reversibility_candidate=forged)
        self.assertIn("MISMATCH", result["checks"]["rollback"])
        self.assertEqual(result["verdict"], "FAIL")

    def test_source_snapshot_list_digest_catches_a_swapped_record(self):
        snapshots = self.source_snapshots()
        passport = self.compose_all(source_snapshots=snapshots)

        other_path = os.path.join(self.tmp, "different-source.xlsx")
        make_fixture_xlsx(other_path, sheet1_rows=99, sheet2_rows=1)
        different = [mss.build_snapshot(other_path, source_system_id="src-crm-001")]
        result = mp.verify_passport_evidence(passport, source_snapshots=different)
        self.assertIn("MISMATCH", result["checks"]["source_snapshot"])
        self.assertEqual(result["verdict"], "FAIL")

    def test_no_evidence_supplied_to_verify_is_no_data_not_a_pass(self):
        passport = self.compose_all()
        result = mp.verify_passport_evidence(passport)
        self.assertEqual(result["verdict"], "NO-DATA")

    def test_digest_recorded_at_compose_time_is_sha256_hex(self):
        passport = self.compose_all()
        digest = passport["risk"]["digest"]
        self.assertEqual(len(digest), 64)
        int(digest, 16)  # raises ValueError if not hex


class CLITests(ComposeFixtureMixin, unittest.TestCase):
    def write(self, name, obj):
        path = os.path.join(self.tmp, name)
        with open(path, "w", encoding="utf-8") as fh:
            json.dump(obj, fh)
        return path

    def run_cli(self, *args):
        return subprocess.run([sys.executable, SCRIPT] + list(args), capture_output=True, text=True)

    def test_cli_full_composition_exits_zero(self):
        contract_path = self.write("contract.json", self.contract())
        candidate_path = self.write("candidate.json", self.reversibility_candidate())
        candidate_gen_path = self.write("cand_gen.json", self.candidate_generation())
        risk_path = self.write("risk.json", self.risk())
        review_path = self.write("review.json", self.review())
        survivorship_path = self.write("survivorship.json", self.survivorship())

        xlsx_path = os.path.join(self.tmp, "generic-source.xlsx")
        make_fixture_xlsx(xlsx_path)
        snapshot_path = self.write("snapshot.json", mss.build_snapshot(xlsx_path, source_system_id="src-crm-001"))
        trace_path = self.write("trace.json", nt.trace("Acme Test Corp", nt.default_chain(), "ja-JP"))

        result = self.run_cli(
            "--master-id", "m1", "--contract", contract_path,
            "--source-snapshot", snapshot_path,
            "--normalization-trace", trace_path,
            "--candidate-generation", candidate_gen_path,
            "--risk", risk_path, "--review", review_path,
            "--reversibility", candidate_path,
            "--survivorship", survivorship_path,
        )
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        passport = json.loads(result.stdout)
        self.assertEqual(passport["verdict"], "PASS")

    def test_cli_missing_contract_file_is_no_data(self):
        result = self.run_cli("--master-id", "m1", "--contract", os.path.join(self.tmp, "missing.json"))
        self.assertEqual(result.returncode, 2)
        self.assertIn("NO-DATA", result.stdout)


if __name__ == "__main__":
    unittest.main(verbosity=2)

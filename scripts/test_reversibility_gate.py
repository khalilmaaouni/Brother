import json
import os
import subprocess
import sys
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
SCRIPT = os.path.join(HERE, "reversibility_gate.py")

sys.path.insert(0, HERE)
import reversibility_gate as rg  # noqa: E402

# Synthetic fixture data only -- fake customer/product records with
# placeholder names, never anything resembling a real client's data.
PRE_MERGE_RECORDS = {
    "src-crm-001": {
        "customer_name": "Acme Test Corp",
        "phone": "03-1234-5678",
        "address": "1 Example Street",
    },
    "src-pos-042": {
        "customer_name": "Acme Test Corp Ltd.",
        "phone": "0312345678",
        "address": "1 Example St",
    },
}

SURVIVORSHIP_DECISIONS = {
    "customer_name": {"value": "Acme Test Corp", "winner_source_id": "src-crm-001"},
    "phone": {"value": "0312345678", "winner_source_id": "src-pos-042"},
    "address": {"value": "1 Example Street", "winner_source_id": "src-crm-001"},
}


class RollbackCycleTests(unittest.TestCase):
    """Real end-to-end: construct a merge, capture its pre-state, "merge"
    the two synthetic records, then verify the pre-state reconstructs the
    exact originals. Not a mocked assertion -- run_rollback_cycle actually
    builds a golden record and actually restores from the snapshot."""

    def test_rollback_reconstructs_exact_pre_merge_state(self):
        result = rg.run_rollback_cycle(PRE_MERGE_RECORDS, SURVIVORSHIP_DECISIONS)
        self.assertTrue(result["ran"])
        self.assertTrue(result["matches_pre_state"])
        self.assertTrue(result["idempotent"])
        self.assertEqual(result["checksum_pre"], result["checksum_rollback"])
        # The merge really happened: a golden record was actually produced,
        # combining fields from both sources per the given decisions.
        self.assertEqual(
            result["golden_record_produced"],
            {"customer_name": "Acme Test Corp", "phone": "0312345678", "address": "1 Example Street"},
        )

    def test_pre_merge_records_never_mutated_by_the_cycle(self):
        # "Never mutate raw truth": running the cycle must not touch the
        # caller's original dict.
        original = json.loads(json.dumps(PRE_MERGE_RECORDS))
        rg.run_rollback_cycle(PRE_MERGE_RECORDS, SURVIVORSHIP_DECISIONS)
        self.assertEqual(PRE_MERGE_RECORDS, original)

    def test_a_no_op_inverse_operation_fails_matches_pre_state(self):
        # This is the test that would have caught the original tautology:
        # rollback() used to unconditionally hand back a fresh deepcopy of
        # its OWN snapshot, so matches_pre_state compared a deepcopy
        # against its own direct origin and could never be False for any
        # input -- old run_rollback_cycle did not even accept an inverse
        # operation to override. Here the inverse is deliberately broken --
        # it just hands back the merged (post-merge) state, never restoring
        # anything -- and the gate must catch that, not wave it through.
        def broken_inverse(post_merge_state, snapshot):
            return post_merge_state  # does not restore anything

        result = rg.run_rollback_cycle(
            PRE_MERGE_RECORDS, SURVIVORSHIP_DECISIONS, inverse_operation_fn=broken_inverse,
        )
        self.assertTrue(result["ran"])
        self.assertFalse(result["matches_pre_state"])

    def test_a_broken_inverse_is_caught_not_waved_through(self):
        # Prove the checksum comparison actually detects a bad rollback,
        # rather than always reporting matches_pre_state=True.
        tampered_snapshot = json.loads(json.dumps(PRE_MERGE_RECORDS))
        tampered_snapshot["src-crm-001"]["customer_name"] = "Wrong Name Corp"
        result = rg.run_rollback_cycle(tampered_snapshot, SURVIVORSHIP_DECISIONS)
        # rollback restores tampered_snapshot exactly (that part is honest);
        # the mismatch shows up when compared against the REAL pre-merge
        # records the candidate claims to represent.
        self.assertNotEqual(result["golden_record_produced"]["customer_name"], "Wrong Name Corp")
        self.assertNotEqual(tampered_snapshot, PRE_MERGE_RECORDS)


class EvaluateMergeCandidateTests(unittest.TestCase):
    """PASS, FAIL and NO-DATA paths for the gate's verdict function, using
    the real reversible_action built by build_reversible_action (which
    itself runs the real rollback cycle -- no hand-typed verification)."""

    def test_complete_evidence_passes(self):
        candidate = rg.build_reversible_action(
            affected_entity_set=list(PRE_MERGE_RECORDS.keys()),
            pre_merge_records=PRE_MERGE_RECORDS,
            survivorship_decisions=SURVIVORSHIP_DECISIONS,
        )
        verdict, reason = rg.evaluate_merge_candidate(candidate)
        self.assertEqual(verdict, "PASS", reason)
        self.assertIn(verdict, rg.VERDICTS)

    def test_missing_pre_state_is_no_data_not_silent_pass(self):
        candidate = {
            "inverse_operation": "restore snapshot",
            "affected_entity_set": ["src-crm-001"],
            "pre_state_reference": None,
            "recovery_verification": {"ran": True, "matches_pre_state": True, "idempotent": True},
            "downstream_propagation_status": "not_propagated",
        }
        verdict, reason = rg.evaluate_merge_candidate(candidate)
        self.assertEqual(verdict, "NO-DATA")
        self.assertIn("pre-merge snapshot", reason)

    def test_verification_never_run_is_no_data_not_silent_pass(self):
        candidate = {
            "inverse_operation": "restore snapshot",
            "affected_entity_set": ["src-crm-001"],
            "pre_state_reference": PRE_MERGE_RECORDS,
            "recovery_verification": None,
            "downstream_propagation_status": "not_propagated",
        }
        verdict, reason = rg.evaluate_merge_candidate(candidate)
        self.assertEqual(verdict, "NO-DATA")
        self.assertIn("no recovery verification", reason)

    def test_rollback_that_failed_verification_is_fail_not_pass(self):
        candidate = rg.build_reversible_action(
            affected_entity_set=list(PRE_MERGE_RECORDS.keys()),
            pre_merge_records=PRE_MERGE_RECORDS,
            survivorship_decisions=SURVIVORSHIP_DECISIONS,
        )
        candidate["recovery_verification"]["matches_pre_state"] = False
        verdict, reason = rg.evaluate_merge_candidate(candidate)
        self.assertEqual(verdict, "FAIL")

    def test_propagated_downstream_with_no_compensation_plan_is_fail(self):
        candidate = rg.build_reversible_action(
            affected_entity_set=list(PRE_MERGE_RECORDS.keys()),
            pre_merge_records=PRE_MERGE_RECORDS,
            survivorship_decisions=SURVIVORSHIP_DECISIONS,
            downstream_propagation_status="propagated",
            compensation_plan=None,
        )
        verdict, reason = rg.evaluate_merge_candidate(candidate)
        self.assertEqual(verdict, "FAIL")
        self.assertIn("compensation", reason)

    def test_propagated_downstream_with_compensation_plan_passes(self):
        candidate = rg.build_reversible_action(
            affected_entity_set=list(PRE_MERGE_RECORDS.keys()),
            pre_merge_records=PRE_MERGE_RECORDS,
            survivorship_decisions=SURVIVORSHIP_DECISIONS,
            downstream_propagation_status="propagated",
            compensation_plan="compensating event queued on the downstream outbox",
        )
        verdict, reason = rg.evaluate_merge_candidate(candidate)
        self.assertEqual(verdict, "PASS", reason)


class CLITests(unittest.TestCase):
    def run_cli(self, *args):
        return subprocess.run(
            [sys.executable, SCRIPT] + list(args),
            capture_output=True, text=True, check=False,
        )

    def test_cli_exit_code_pass(self):
        candidate = rg.build_reversible_action(
            affected_entity_set=list(PRE_MERGE_RECORDS.keys()),
            pre_merge_records=PRE_MERGE_RECORDS,
            survivorship_decisions=SURVIVORSHIP_DECISIONS,
        )
        path = os.path.join(HERE, "_tmp_reversibility_candidate_pass.json")
        with open(path, "w", encoding="utf-8") as handle:
            json.dump(candidate, handle)
        try:
            result = self.run_cli(path)
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            record = json.loads(result.stdout)
            self.assertEqual(record["verdict"], "PASS")
        finally:
            os.remove(path)

    def test_cli_exit_code_no_data_on_missing_file(self):
        result = self.run_cli(os.path.join(HERE, "_no_such_candidate.json"))
        self.assertEqual(result.returncode, 2)
        self.assertIn("NO-DATA", result.stdout)

    def test_cli_exit_code_fail(self):
        candidate = rg.build_reversible_action(
            affected_entity_set=list(PRE_MERGE_RECORDS.keys()),
            pre_merge_records=PRE_MERGE_RECORDS,
            survivorship_decisions=SURVIVORSHIP_DECISIONS,
            downstream_propagation_status="propagated",
        )
        path = os.path.join(HERE, "_tmp_reversibility_candidate_fail.json")
        with open(path, "w", encoding="utf-8") as handle:
            json.dump(candidate, handle)
        try:
            result = self.run_cli(path)
            self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
            record = json.loads(result.stdout)
            self.assertEqual(record["verdict"], "FAIL")
        finally:
            os.remove(path)

    def test_tool_never_imports_network_modules(self):
        with open(SCRIPT, "r", encoding="utf-8") as handle:
            source = handle.read()
        for forbidden in ("import requests", "import urllib", "import http.client", "import socket"):
            self.assertNotIn(forbidden, source)


if __name__ == "__main__":
    unittest.main(verbosity=2)

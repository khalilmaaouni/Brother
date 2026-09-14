#!/usr/bin/env python3
"""Test for mdm_canary_smoke.py (WBS-40.11): the full ten-module WBS-40
(Golden Master / MDM) pipeline run as ONE real test, not ten separate mocked
assertions. Proves the synthetic canary golden-master journey's own real
output threads through every real sibling function and lands in a
well-formed Merge Passport and Publish Reconciliation record, whose
completeness objects are honest about what is real evidence, what is a
synthetic stand-in, and what is genuine NO-DATA, including the one
adjacent-stage shape mismatch this run surfaced (survivorship_lineage's real
per-field decision shape vs. merge_passport's original survivorship_decision
shape, fixed in merge_passport.py itself)."""
import sys
import unittest

sys.path.insert(0, __file__.rsplit("/", 1)[0])
import mdm_canary_smoke as CCS  # noqa: E402


class MDMCanarySmokeTests(unittest.TestCase):

    def setUp(self):
        self.passport, self.reconciliation, self.tmp = CCS.run_pipeline()

    def test_passport_is_well_formed(self):
        self.assertEqual(self.passport["schema_version"], "merge-passport-v1")
        self.assertEqual(self.passport["master_id"], CCS.MASTER_ID)
        for key in ("source_snapshot", "normalization_trace", "candidate_generation",
                    "risk", "review", "rollback", "survivorship", "authority",
                    "evidence", "field_verdicts"):
            self.assertIn(key, self.passport)

    def test_the_real_sibling_stages_actually_connect(self):
        # Each of these fields is fed the REAL output of a REAL upstream
        # function: master_source_snapshot.build_snapshot(),
        # normalization_trace.trace(), reversibility_gate.build_reversible_
        # action() (which itself consumes survivorship_lineage.resolve_
        # field()'s real decisions), and golden_master_contract.check() via
        # _compose_authority(). A PASS here proves the shapes genuinely fit
        # end to end, not just that each module works standalone.
        self.assertEqual(self.passport["source_snapshot"]["verdict"], "PASS")
        self.assertEqual(self.passport["normalization_trace"]["verdict"], "PASS")
        self.assertEqual(self.passport["rollback"]["verdict"], "PASS")
        self.assertEqual(self.passport["authority"]["verdict"], "PASS")
        self.assertEqual(self.passport["authority"]["publish"], "HUMAN")

    def test_survivorship_dimension_composes_the_real_per_field_decision_shape(self):
        # THE FINDING: survivorship_lineage.resolve_field()'s real output is
        # {field: {"value": ..., "winner_source_id": ...}}, not the flat
        # {"attributes": [...]} shape merge_passport.py originally expected.
        # Fed straight through with no reshaping in mdm_canary_smoke.py,
        # this proves the FIXED, honest behavior: merge_passport.py's own
        # _normalize_survivorship_decision() now recognizes the real
        # per-field shape and composes it to PASS, rather than the false
        # FAIL an unreshaped feed would have produced against the original
        # code.
        survivorship = self.passport["survivorship"]
        self.assertEqual(survivorship["verdict"], "PASS", survivorship)
        attributes = survivorship["record"]["attributes"]
        fields = {a["field"]: a for a in attributes}
        self.assertEqual(fields["customer_name"]["source_system"], CCS.SOURCE_B)
        self.assertEqual(fields["phone"]["source_system"], CCS.SOURCE_A)

    def test_no_sibling_module_dimension_is_honest_no_data_not_fabricated(self):
        # "evidence" has no sibling module in this repo yet (per merge_
        # passport.py's own docstring); left unsupplied in mdm_canary_
        # smoke.py rather than given evidence that would fabricate a PASS.
        self.assertEqual(self.passport["evidence"]["verdict"], "NO-DATA")

    def test_overall_merge_passport_verdict_passes(self):
        # Every REQUIRED_FOR_MERGE field (source_snapshot, normalization_
        # trace, candidate_generation, risk, review, rollback, survivorship,
        # authority) is real, connected evidence in this canary run, so the
        # overall composed verdict is a real PASS, not a fabricated one.
        self.assertEqual(self.passport["verdict"], "PASS")
        self.assertFalse(self.passport["refused"])
        self.assertEqual(self.passport["status"], "COMPOSED")

    def test_publish_reconciliation_is_downstream_and_honest(self):
        # publish_reconciliation.py never reads the merge passport itself
        # (its own documented boundary); this run gives it real, consistent
        # evidence for six of seven checks and leaves duplication_recurrence
        # honestly unsupplied (no prior resolved-duplicate population to
        # check recurrence against in this canary run).
        completeness = self.reconciliation["completeness"]
        self.assertEqual(completeness["total_checks"], 7)
        self.assertEqual(completeness["failed"], [])
        self.assertEqual(completeness["no_data"], ["duplication_recurrence"])
        self.assertEqual(completeness["failed_count"], 0)
        self.assertEqual(completeness["no_data_count"], 1)
        self.assertTrue(completeness["headline"].startswith("INCOMPLETE"))
        self.assertEqual(self.reconciliation["counts"]["verdict"], "PASS")
        self.assertEqual(self.reconciliation["publish_timestamp"]["verdict"], "PASS")

    def test_exit_code_for_publish_reconciliation_reports_the_real_no_data_state(self):
        import publish_reconciliation as PR
        self.assertEqual(
            PR.exit_code_for_completeness(self.reconciliation["completeness"]), 2)


if __name__ == "__main__":
    unittest.main()

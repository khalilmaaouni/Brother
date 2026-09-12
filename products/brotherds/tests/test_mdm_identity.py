"""Behavioral and adversarial regressions for identifier and metadata gates."""
import copy
import pathlib
import sys
import unittest
from collections import namedtuple

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))
import pack_mdm_identity as P

Finding = namedtuple("Finding", "gate verdict detail")


class Registry:
    def __init__(self):
        self.calls = []
    def register(self, kind, fn):
        self.calls.append((kind, fn))


class IdentityEvidence(unittest.TestCase):
    def setUp(self):
        registry = Registry()
        P.register(registry, Finding)
        self.assertEqual(len(registry.calls), 1)
        self.assertEqual(registry.calls[0][0], "MASTER_DATA")
        self.fn = registry.calls[0][1]
        self.block = {"n": 4, "empty": 1, "valid": 3, "invalid": 0,
                      "duplicates": 1, "matched_invalid": 0, "reasons": {}}
        self.audit = {"computed": {"n_rows": 4, "identifiers": {"corporate_number": self.block},
                                   "input_sha256": "0123456789abcdef" * 4,
                                   "provenance": {"present": ["reference_snapshot"], "missing": []}}}

    def gates(self, audit):
        findings = self.fn({"master_data": {"audit": audit}}, Finding)
        self.assertEqual([f.gate for f in findings], [P.GATE31, P.GATE32])
        return findings

    def test_legacy_applicability(self):
        for claim in ({}, None, [], {"master_data": None}, {"master_data": {}},
                      {"master_data": {"audit": None}}, {"master_data": {"audit": []}},
                      {"master_data": {"audit": "pending"}}):
            with self.subTest(claim=claim):
                self.assertEqual(self.fn(claim, Finding), [])

    def test_empty_audit_is_no_data(self):
        self.assertEqual([f.verdict for f in self.gates({})], ["NO-DATA", "NO-DATA"])

    def test_valid_identifiers_and_metadata(self):
        findings = self.gates(self.audit)
        self.assertEqual([f.verdict for f in findings], ["PASS", "PASS"])
        self.assertIn("3 valid, 1 empty, 1 duplicate(s)", findings[0].detail)
        self.assertIn("supplied metadata consistent", findings[1].detail)
        self.assertIn("independent replay and authenticity not checked", findings[1].detail)

    def test_absent_identifier_columns_is_no_data(self):
        for value in (None, {}):
            self.audit["computed"]["identifiers"] = value
            self.assertEqual(self.gates(self.audit)[0].verdict, "NO-DATA")

    def test_bad_identifier_container_fails(self):
        for value in ([], "", False, 1):
            self.audit["computed"]["identifiers"] = value
            self.assertEqual(self.gates(self.audit)[0].verdict, "FAIL")

    def test_strict_all_counter_types(self):
        for field in ("n", "empty", "valid", "invalid", "duplicates", "matched_invalid"):
            for value in (True, -1, 1.0, "1", None):
                audit = copy.deepcopy(self.audit)
                audit["computed"]["identifiers"]["corporate_number"][field] = value
                with self.subTest(field=field, value=value):
                    finding = self.gates(audit)[0]
                    self.assertEqual(finding.verdict, "FAIL")
                    self.assertIn(field, finding.detail)

    def test_total_is_all_rows_including_empty(self):
        self.block["n"] = 3
        self.assertEqual(self.gates(self.audit)[0].verdict, "FAIL")

    def test_identifier_counts_match_table_population(self):
        self.audit["computed"]["n_rows"] = 5
        self.assertEqual(self.gates(self.audit)[0].verdict, "FAIL")
        self.audit["computed"]["n_rows"] = True
        self.assertEqual(self.gates(self.audit)[0].verdict, "FAIL")

    def test_identifier_columns_disagree_on_population(self):
        self.audit["computed"].pop("n_rows")
        self.audit["computed"]["identifiers"]["invoice_number"] = dict(self.block, n=5, valid=4)
        self.assertEqual(self.gates(self.audit)[0].verdict, "FAIL")

    def test_matched_invalid_cannot_exceed_invalid(self):
        self.block["matched_invalid"] = 1
        self.assertEqual(self.gates(self.audit)[0].verdict, "FAIL")

    def test_duplicate_bound(self):
        self.block["duplicates"] = 4
        self.assertEqual(self.gates(self.audit)[0].verdict, "FAIL")

    def test_invalid_match_cannot_be_acknowledged(self):
        self.block.update(valid=2, invalid=1, matched_invalid=1, reasons={"check digit mismatch": 1})
        self.audit["identifiers_acknowledged"] = True
        finding = self.gates(self.audit)[0]
        self.assertEqual(finding.verdict, "FAIL")
        self.assertIn("1 MATCH row(s)", finding.detail)

    def test_acknowledged_nonmatched_invalids(self):
        self.block.update(valid=2, invalid=1, reasons={"check digit mismatch": 1})
        finding = self.gates(self.audit)[0]
        self.assertEqual(finding.verdict, "FAIL")
        self.assertIn("1 of 3 non-empty", finding.detail)
        self.audit["identifiers_acknowledged"] = True
        self.assertEqual(self.gates(self.audit)[0].verdict, "PASS")
        self.audit["identifiers_acknowledged"] = "true"
        self.assertEqual(self.gates(self.audit)[0].verdict, "FAIL")

    def test_reasons_are_reconciled_strict_counts(self):
        self.block.update(valid=2, invalid=1)
        for value in (None, [], {"bad": True}, {"bad": -1}, {"bad": "1"}, {"bad": 0}, {"": 1}):
            self.block["reasons"] = value
            self.audit["identifiers_acknowledged"] = True
            self.assertEqual(self.gates(self.audit)[0].verdict, "FAIL", repr(value))

    def test_malformed_computed_is_failure(self):
        for value in ([], False, 42, "pending"):
            self.audit["computed"] = value
            self.assertEqual([f.verdict for f in self.gates(self.audit)], ["FAIL", "FAIL"])

    def test_digest_must_be_exact_hex(self):
        for value in (None, "", "a" * 63, "a" * 65, "z" * 64, "a" * 64 + "\n", True, 123):
            self.audit["computed"]["input_sha256"] = value
            finding = self.gates(self.audit)[1]
            self.assertEqual(finding.verdict, "FAIL", repr(value))
            self.assertIn("input_sha256", finding.detail)
        self.audit["computed"]["input_sha256"] = "ABCDEF01" * 8
        self.assertEqual(self.gates(self.audit)[1].verdict, "PASS")

    def test_review_seed_is_strict_integer(self):
        for key in ("pathway_review", "unmatched_review"):
            for value in (None, True, "7", 7.0):
                audit = copy.deepcopy(self.audit)
                audit.update({key: {}, "plan_seed": value})
                self.assertEqual(self.gates(audit)[1].verdict, "FAIL")
            for value in (0, -1, 7):
                audit = copy.deepcopy(self.audit)
                audit.update({key: {}, "plan_seed": value})
                self.assertEqual(self.gates(audit)[1].verdict, "PASS")

    def test_review_container_cannot_be_malformed(self):
        self.audit.update(pathway_review=[], plan_seed=7)
        self.assertEqual(self.gates(self.audit)[1].verdict, "FAIL")

    def test_reference_must_be_affirmatively_present(self):
        for provenance in (None, {}, {"present": [], "missing": []},
                           {"present": ["reference_snapshot"], "missing": ["reference_snapshot"]},
                           {"present": "reference_snapshot", "missing": []}):
            self.audit["computed"]["provenance"] = provenance
            self.assertEqual(self.gates(self.audit)[1].verdict, "FAIL")

    def test_provenance_lists_are_strict(self):
        for field in ("present", "missing"):
            for value in (None, "", [False], [""], ["reference_snapshot", "reference_snapshot"]):
                audit = copy.deepcopy(self.audit)
                audit["computed"]["provenance"][field] = value
                self.assertEqual(self.gates(audit)[1].verdict, "FAIL")

    def test_model_verifier_requires_version(self):
        for kind in ("llm", "model", "LLM"):
            self.audit["verifier"] = {"kind": kind}
            self.assertEqual(self.gates(self.audit)[1].verdict, "FAIL")
        self.audit["computed"]["provenance"]["present"].append("model_version")
        self.assertEqual(self.gates(self.audit)[1].verdict, "PASS")
        self.audit["computed"]["provenance"]["missing"].append("model_version")
        self.assertEqual(self.gates(self.audit)[1].verdict, "FAIL")

    def test_human_verifier_does_not_require_model_version(self):
        self.audit["verifier"] = {"kind": "human"}
        self.assertEqual(self.gates(self.audit)[1].verdict, "PASS")

    def test_verified_flag_does_not_establish_provenance(self):
        self.audit["computed"].pop("input_sha256")
        self.audit["verified"] = True
        self.audit["computed"]["verified"] = True
        self.assertEqual(self.gates(self.audit)[1].verdict, "FAIL")

    def test_pack_is_registered_in_real_cli_seam(self):
        import bds
        self.assertEqual(bds._PACK_STATUS["pack_mdm_identity"], "OK")
        verdict, findings, values = bds.check({"claim_type": "MASTER_DATA", "master_data": {"audit": self.audit}})
        chosen = [f for f in findings if f.gate in (P.GATE31, P.GATE32)]
        self.assertEqual([f.gate for f in chosen], [P.GATE31, P.GATE32])
        self.assertEqual([f.verdict for f in chosen], ["PASS", "PASS"])
        unused = bds.packs.run_packs("EXPERIMENT", {"master_data": {"audit": self.audit}}, Finding)
        self.assertFalse(any(f.gate in (P.GATE31, P.GATE32) for f in unused))

    def test_selftest_exercises_at_least_twenty_assertions(self):
        calls = []
        def expect(condition, message):
            calls.append((condition, message))
            return bool(condition)
        self.assertTrue(P.selftest(expect))
        self.assertGreaterEqual(len(calls), 20)
        self.assertTrue(all(condition for condition, message in calls))


if __name__ == "__main__":
    unittest.main()

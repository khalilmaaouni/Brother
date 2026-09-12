"""Independent regressions for population evidence and Japanese CSV exports."""
import copy
import csv
import hashlib
import json
import pathlib
import subprocess
import sys
import tempfile
import unittest
from collections import namedtuple

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
import mdm_audit as A
import pack_mdm_audit as P
import pack_mdm_coverage as C

Finding = namedtuple("Finding", "gate verdict detail")


class AuditIntegrity(unittest.TestCase):
    def pathway(self, strata=None):
        entry = {"population": 1000, "sampled": 10, "positive": 10}
        if strata is not None:
            entry["strata"] = strata
        return {"reported": {"pathway_counts": {"a": 1000}},
                "pathway_review": {"a": entry},
                "claimed_pathway_precision": {"a": 1.0}}

    def test_m25_population_mismatch(self):
        claim = self.pathway([{"stratum": "s", "population": 10, "sampled": 10, "positive": 10}])
        self.assertEqual(P._gate_m25(claim, {}, Finding).verdict, "FAIL")

    def test_m25_aggregate_contradiction_never_falls_back(self):
        claim = self.pathway([{"stratum": "s", "population": 1000, "sampled": 0, "positive": 0}])
        self.assertEqual(P._gate_m25(claim, {}, Finding).verdict, "FAIL")

    def test_m25_incomplete_consistent_population(self):
        claim = self.pathway([{"stratum": "s", "population": 10, "sampled": 10, "positive": 10},
                              {"stratum": "u", "population": 990, "sampled": 0, "positive": 0}])
        self.assertEqual(P._gate_m25(claim, {}, Finding).verdict, "NO-DATA")

    def test_m25_legacy_and_complete_strata(self):
        for strata in (None, [], [{"stratum": "s", "population": 1000, "sampled": 10, "positive": 10}],
                       [{"stratum": "s", "population": 1000, "sampled": 10, "positive": 10},
                        {"stratum": "empty", "population": 0, "sampled": 0, "positive": 0}]):
            with self.subTest(strata=strata):
                self.assertEqual(P._gate_m25(self.pathway(strata), {}, Finding).verdict, "PASS")

    def test_m25_duplicate_strata(self):
        claim = self.pathway([{"stratum": "s", "population": 500, "sampled": 5, "positive": 5}] * 2)
        self.assertEqual(P._gate_m25(claim, {}, Finding).verdict, "FAIL")

    def test_m25_overall_wrong_weights(self):
        claim = self.pathway([{"stratum": "s", "population": 100, "sampled": 10, "positive": 2}])
        claim["pathway_review"]["a"]["positive"] = 2
        claim["reported"]["pathway_counts"]["b"] = 1000
        claim["pathway_review"]["b"] = {"population": 1000, "sampled": 10, "positive": 10}
        claim.pop("claimed_pathway_precision")
        self.assertEqual(P._gate_m25(claim, {"claimed_precision": .8}, Finding).verdict, "FAIL")

    def unmatched(self):
        return {"population": 1000, "sampled": 10, "reference_absent": 5, "matchable_missed": 5,
                "strata": [{"stratum": "s", "population": 10, "sampled": 10,
                            "reference_absent": 5, "matchable_missed": 5},
                           {"stratum": "u", "population": 990, "sampled": 0,
                            "reference_absent": 0, "matchable_missed": 0}]}

    def m28(self, ur):
        return C._gate_m28({"audit": {"unmatched_review": ur,
                    "computed": {"reconciliation": {"status_counts": {"MATCH": 90, "NO_MATCH": 1000}}}},
                           "evaluation": {"claimed_recall": .97}})

    def test_m28_does_not_extrapolate_unobserved_strata(self):
        verdict, detail = self.m28(self.unmatched())
        self.assertEqual(verdict, "NO-DATA")
        self.assertIn("990", detail)
        self.assertNotIn("0.275", detail)

    def test_m28_contradictions(self):
        for field in ("population", "sampled", "reference_absent", "matchable_missed"):
            ur = self.unmatched()
            ur[field] += 1
            self.assertEqual(self.m28(ur)[0], "FAIL", field)

    def test_m28_sample_larger_than_population(self):
        ur = {"population": 1, "sampled": 10, "reference_absent": 5, "matchable_missed": 5}
        self.assertEqual(self.m28(ur)[0], "FAIL")

    def plan_row(self, name, pop, label):
        return {"plan_id": name, "stratum": "unmatched:" + name,
                "stratum_population": str(pop), "stratum_sampled": "1", "label": label}

    def test_evaluator_names_partial_population(self):
        ur = A.evaluate_plan([self.plan_row("a", 10, "1"), self.plan_row("b", 990, "")])["unmatched_review"]
        self.assertEqual((ur["coverage_state"], ur["reviewed_population"], ur["unreviewed_population"]),
                         ("NO-DATA", 10, 990))
        self.assertIsNone(ur["matchable_missed_weighted"])
        self.assertIsNone(ur["reference_absent_weighted"])
        self.assertEqual(ur["sampled_subpopulation_matchable_missed"]["population"], 10)

    def test_evaluator_weighted_shares(self):
        ur = A.evaluate_plan([self.plan_row("a", 900, "0"), self.plan_row("b", 100, "1")])["unmatched_review"]
        self.assertEqual(ur["coverage_state"], "PASS")
        self.assertAlmostEqual(ur["matchable_missed_weighted"]["estimate"], .1)
        self.assertAlmostEqual(ur["reference_absent_weighted"]["estimate"], .9)
        self.assertAlmostEqual(ur["reference_absent_weighted"]["estimate"] + ur["matchable_missed_weighted"]["estimate"], 1)

    def test_evaluator_rejects_conflicting_or_missing_plan_metadata(self):
        one = self.plan_row("a", 100, "1")
        two = dict(one, stratum_population="101")
        with self.assertRaises(ValueError):
            A.evaluate_plan([one, two])
        with self.assertRaises(ValueError):
            A.evaluate_plan([dict(one, stratum_sampled="2")])
        with self.assertRaises(ValueError):
            A.evaluate_plan([dict(one, stratum_population="nonsense")])

    def test_results_expose_an_omitted_plan_stratum(self):
        rows = [{"source_id": "S1", "pathway": "a", "status": "NO_MATCH"},
                {"source_id": "S2", "pathway": "b", "status": "NO_MATCH"}]
        ur = A.evaluate_plan([self.plan_row("a", 1, "1")], rows)["unmatched_review"]
        self.assertEqual((ur["population"], ur["coverage_state"], ur["unreviewed_population"]),
                         (2, "NO-DATA", 1))
        with self.assertRaises(ValueError):
            A.evaluate_plan([self.plan_row("a", 10, "1")], rows)

    def test_demo_uses_complementary_population_weights(self):
        sys.path.insert(0, str(ROOT / "examples"))
        import mdm_audit_demo as demo
        report = demo.run(seed=11)
        estimates = report["estimates"]
        self.assertAlmostEqual(estimates["coverage_gap_share"] + estimates["missed_share"]["estimate"], 1)

    def test_decoding_and_identifier_counts(self):
        with tempfile.TemporaryDirectory() as td:
            path = pathlib.Path(td) / "results.csv"
            fields = A.REQUIRED_COLUMNS + ["corporate_number", "invoice_number"]
            rows = [dict(zip(fields, ["カナ", "R1", "a", "MATCH", ".9", "7000012050002", "T7000012050002"])),
                    dict(zip(fields, ["S2", "R2", "a", "MATCH", ".9", "123", "bad"])),
                    dict(zip(fields, ["S3", "", "a", "NO_MATCH", "", "", ""]))]
            for codec in ("cp932", "utf-8-sig"):
                with path.open("w", encoding=codec, newline="") as fh:
                    writer = csv.DictWriter(fh, fieldnames=fields)
                    writer.writeheader()
                    writer.writerows(rows)
                audit = A.audit_path(path)
                self.assertEqual(audit["input_encoding"], codec)
                self.assertEqual(audit["input_sha256"], hashlib.sha256(path.read_bytes()).hexdigest())
                self.assertEqual(A.read_results(path, encoding=codec)[0]["source_id"], "カナ")
                for kind in ("corporate_number", "invoice_number"):
                    block = audit["identifiers"][kind]
                    self.assertEqual((block["n"], block["valid"], block["invalid"], block["empty"], block["matched_invalid"]),
                                     (3, 1, 1, 1, 1))
            self.assertIsNone(A.audit(rows)["input_encoding"])
            path.write_bytes(b"source_id,reference_id,pathway,status,score\n\xff")
            with self.assertRaises(ValueError):
                A.read_results(path, encoding="utf-8")
            with self.assertRaises(ValueError):
                A.read_results(path, encoding="nonexistent-codec")
            self.assertEqual(A.audit([])["identifiers"], {})

    def test_cli_encoding_on_audit_and_evaluate(self):
        with tempfile.TemporaryDirectory() as td:
            path = pathlib.Path(td) / "results.csv"
            path.write_text("source_id,reference_id,pathway,status,score\nカナ,R1,a,MATCH,0.9\n", encoding="cp932")
            result = subprocess.run([sys.executable, str(ROOT / "mdm_audit.py"), "audit", str(path), "--encoding", "cp932"],
                                    capture_output=True, text=True)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(json.loads(result.stdout)["input_encoding"], "cp932")
            plan = pathlib.Path(td) / "plan.csv"
            plan.write_text("plan_id,stratum,stratum_population,stratum_sampled,label\nP1,unmatched:カナ,10,1,1\n", encoding="cp932")
            result = subprocess.run([sys.executable, str(ROOT / "mdm_audit.py"), "evaluate", str(plan), "--encoding", "cp932"],
                                    capture_output=True, text=True)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(json.loads(result.stdout)["unmatched_review"]["population"], 10)


if __name__ == "__main__":
    unittest.main()

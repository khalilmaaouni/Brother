"""Independent checks of executed demo replay and the real gate registry."""
import copy
import csv
import hashlib
import json
import pathlib
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "examples"))
import bds
import mdm_audit_demo as demo


class DemoBinding(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temporary = tempfile.TemporaryDirectory(prefix="demo-binding-")
        cls.directory = pathlib.Path(cls.temporary.name)
        cls.report = demo.run(seed=11, plan_seed=13, workdir=str(cls.directory))

    @classmethod
    def tearDownClass(cls):
        cls.temporary.cleanup()

    def replay(self, results=None, labelled=None, snapshot=None):
        return subprocess.run([sys.executable, str(ROOT / "bds.py"), "mdm-audit", "evaluate",
                               str(labelled or self.directory / "plan-labelled.csv"),
                               "--results", str(results or self.directory / "results.csv"),
                               "--audit", str(snapshot or self.directory / "audit.json")],
                              capture_output=True, text=True)

    def test_digest_is_actual_export_bytes(self):
        digest = hashlib.sha256((self.directory / "results.csv").read_bytes()).hexdigest()
        audit = self.report["claims"]["audited"]["master_data"]["audit"]
        self.assertEqual(audit["computed"]["input_sha256"], digest)
        self.assertEqual(audit["computed"]["review_plan"]["input_sha256"], digest)
        self.assertEqual(audit["plan_seed"], 13)
        binding = self.report["provenance_binding"]
        self.assertEqual(binding["input_sha256"], digest)
        self.assertEqual(binding["state"], "PASS")
        self.assertEqual(binding["source_authenticity"], "NO-DATA")
        self.assertNotIn("verified", binding)

    def test_replay_executes_again_through_cli(self):
        result = self.replay()
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        evaluation = json.loads(result.stdout)
        self.assertEqual(evaluation, self.report["evaluation"])

    def test_real_registry_matches_every_demo_gate(self):
        for name in ("overclaim", "audited"):
            claim = self.report["claims"][name]
            verdict, findings, values = bds.check(claim)
            by_gate = {f.gate: f.verdict for f in findings if
                       f.gate.split(".")[0] in {"M" + str(n) for n in range(23, 33)}}
            self.assertEqual(len(by_gate), 10)
            self.assertEqual(by_gate, self.report["verdicts"][name])
        audited = self.report["verdicts"]["audited"]
        self.assertEqual(audited["M31.identifier_validity"], "NO-DATA")
        self.assertEqual(audited["M32.audit_provenance"], "PASS")

    def test_tampered_export_bytes_are_refused(self):
        with tempfile.TemporaryDirectory() as td:
            altered = pathlib.Path(td) / "results.csv"
            altered.write_bytes((self.directory / "results.csv").read_bytes().replace(b"\n", b"\r\n"))
            result = self.replay(results=altered)
            self.assertEqual(result.returncode, 2)
            self.assertIn("export digest differs", result.stdout)
            self.assertNotIn('"state": "PASS"', result.stdout)

    def test_tampered_review_membership_is_refused(self):
        with (self.directory / "plan-labelled.csv").open(newline="", encoding="utf-8") as stream:
            reader = csv.DictReader(stream)
            fields = reader.fieldnames
            rows = list(reader)
        rows[0]["source_id"] = "not-the-selected-record"
        with tempfile.TemporaryDirectory() as td:
            altered = pathlib.Path(td) / "labelled.csv"
            with altered.open("w", newline="", encoding="utf-8") as stream:
                writer = csv.DictWriter(stream, fieldnames=fields)
                writer.writeheader()
                writer.writerows(rows)
            result = self.replay(labelled=altered)
            self.assertEqual(result.returncode, 2)
            self.assertIn("membership or metadata differs", result.stdout)

    def test_tampered_recorded_seed_is_refused(self):
        snapshot = json.loads((self.directory / "audit.json").read_text())
        snapshot["review_plan"]["parameters"]["seed"] += 1
        with tempfile.TemporaryDirectory() as td:
            altered = pathlib.Path(td) / "audit.json"
            altered.write_text(json.dumps(snapshot))
            result = self.replay(snapshot=altered)
            self.assertEqual(result.returncode, 2)
            self.assertIn("regenerated plan", result.stdout)

    def test_modified_claim_provenance_fails_m32(self):
        for change in ("digest", "seed", "reference", "model"):
            claim = copy.deepcopy(self.report["claims"]["audited"])
            audit = claim["master_data"]["audit"]
            if change == "digest":
                audit["computed"]["input_sha256"] = "malformed"
            elif change == "seed":
                audit["plan_seed"] = True
            else:
                field = "reference_snapshot" if change == "reference" else "model_version"
                audit["computed"]["provenance"]["present"].remove(field)
            verdict, findings, values = bds.check(claim)
            finding = next(f for f in findings if f.gate == "M32.audit_provenance")
            self.assertEqual(finding.verdict, "FAIL", change)

    def test_real_invalid_identifier_anchor_fails_m31(self):
        with (self.directory / "results.csv").open(newline="", encoding="utf-8") as stream:
            reader = csv.DictReader(stream)
            fields = list(reader.fieldnames) + ["corporate_number"]
            rows = list(reader)
        assigned = False
        for row in rows:
            row["corporate_number"] = ""
            if row["status"] == "MATCH" and not assigned:
                row["corporate_number"] = "123"
                assigned = True
        self.assertTrue(assigned)
        with tempfile.TemporaryDirectory() as td:
            path = pathlib.Path(td) / "identifiers.csv"
            with path.open("w", newline="", encoding="utf-8") as stream:
                writer = csv.DictWriter(stream, fieldnames=fields)
                writer.writeheader()
                writer.writerows(rows)
            computed = demo._audit_cli(["audit", str(path)])
        self.assertEqual(computed["identifiers"]["corporate_number"]["matched_invalid"], 1)
        claim = {"claim_type": "MASTER_DATA", "master_data": {"audit": {
            "computed": computed, "identifiers_acknowledged": True}}}
        verdict, findings, values = bds.check(claim)
        finding = next(f for f in findings if f.gate == "M31.identifier_validity")
        self.assertEqual(finding.verdict, "FAIL")
        self.assertIn("1 MATCH row(s)", finding.detail)

    def test_cli_output_directory_contains_replayable_files(self):
        with tempfile.TemporaryDirectory() as td:
            command = [sys.executable, str(ROOT / "examples/mdm_audit_demo.py"),
                       "--write-examples", "--output-dir", td, "--json", "--plan-seed", "17"]
            result = subprocess.run(command, capture_output=True, text=True)
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            report = json.loads(result.stdout)
            output = pathlib.Path(td)
            for name in ("example-mdm-audit-overclaim.json", "example-mdm-audit-repaired.json",
                         "results.csv", "audit.json", "plan.csv", "plan-labelled.csv"):
                self.assertTrue((output / name).is_file(), name)
            self.assertEqual(report["claims"]["audited"]["master_data"]["audit"]["plan_seed"], 17)
            replay = self.replay(output / "results.csv", output / "plan-labelled.csv", output / "audit.json")
            self.assertEqual(replay.returncode, 0, replay.stdout + replay.stderr)

    def test_render_names_new_gates_and_replay_limit(self):
        text = demo.render(self.report)
        self.assertIn("M31.identifier_validity", text)
        self.assertIn("M32.audit_provenance", text)
        self.assertIn("Executed export and plan replay: PASS", text)
        self.assertIn("source authenticity: NO-DATA", text)
        self.assertIn("labels are not verified", text)

    def test_legacy_example_destination_is_preserved(self):
        with tempfile.TemporaryDirectory() as td:
            with mock.patch.object(demo, "_HERE", td):
                paths = demo._write_examples(self.report)
            self.assertEqual(len(paths), 2)
            self.assertTrue(all(pathlib.Path(path).parent == pathlib.Path(td) for path in paths))

    def test_default_run_cleans_temporary_exports(self):
        paths = []
        original = demo.tempfile.TemporaryDirectory
        def temporary(*args, **kwargs):
            manager = original(*args, **kwargs)
            paths.append(manager.name)
            return manager
        with mock.patch.object(demo.tempfile, "TemporaryDirectory", side_effect=temporary):
            report = demo.run(seed=11, plan_seed=13)
        self.assertEqual(report, self.report)
        self.assertTrue(paths)
        self.assertTrue(all(not pathlib.Path(path).exists() for path in paths))


if __name__ == "__main__":
    unittest.main()

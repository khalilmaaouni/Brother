"""Behavioral replay tests using independent file fixtures and CLI processes."""
import contextlib
import copy
import csv
import hashlib
import io
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

sys.dont_write_bytecode = True
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
import mdm_audit as audit


class ReplayTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.export = self.root / "results.csv"
        self.plan = self.root / "plan.csv"
        self.snapshot = self.root / "audit.json"
        self.export.write_text(
            "source_id,reference_id,pathway,status,score,verifier,reference_snapshot\n"
            "S1,R1,key_phone,MATCH,.91,CONFIRMED,v1\n"
            "S2,R2,key_phone,MATCH,.92,CONFIRMED,v1\n"
            "S3,R3,key_phone,MATCH,.93,CONFIRMED,v1\n"
            "S4,R4,key_phone,MATCH,.94,CONFIRMED,v1\n"
            "S5,,no_candidate,NO_MATCH,,,v1\n"
            "S6,,no_candidate,NO_MATCH,,,v1\n", encoding="utf-8")
        result = self.cli("audit", self.export, "--plan", self.plan, "--min-n", "1", "--max-n", "2")
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.record = json.loads(result.stdout)
        self.snapshot.write_text(result.stdout, encoding="utf-8")
        with self.plan.open(newline="") as stream:
            self.rows = list(csv.DictReader(stream))
        for row in self.rows:
            row["label"] = "1" if row["reference_id"] else "0"
        self.write_rows(self.rows)

    def cli(self, *args):
        return subprocess.run([sys.executable, str(ROOT / "mdm_audit.py"), *map(str, args)],
                              capture_output=True, text=True, timeout=15,
                              env=dict(os.environ, PYTHONDONTWRITEBYTECODE="1"))

    def evaluate(self):
        return self.cli("evaluate", self.plan, "--audit", self.snapshot, "--results", self.export)

    def write_rows(self, rows, columns=None):
        with self.plan.open("w", newline="", encoding="utf-8") as stream:
            writer = csv.DictWriter(stream, fieldnames=columns or audit.PLAN_COLUMNS, lineterminator="\n")
            writer.writeheader()
            writer.writerows(rows)

    def save(self, value):
        self.snapshot.write_text(json.dumps(value), encoding="utf-8")

    def refusal(self, result):
        self.assertEqual(result.returncode, 2, result.stdout + result.stderr)
        self.assertTrue(result.stdout.startswith("NO-DATA:"), result.stdout)
        self.assertNotIn('"provenance_binding"', result.stdout)
        self.assertNotIn("Traceback", result.stderr)

    def test_valid_replay_and_independent_digest(self):
        result = self.evaluate()
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        value = json.loads(result.stdout)
        self.assertEqual(value["provenance_binding"]["state"], "PASS")
        self.assertEqual(value["provenance_binding"]["source_authenticity"], "NO-DATA")
        self.assertEqual(value["unlabelled"], 0)
        record = self.record["review_plan"]
        self.assertEqual(record["input_sha256"], hashlib.sha256(self.export.read_bytes()).hexdigest())
        payload = {k: v for k, v in record.items() if k != "plan_sha256"}
        payload["rows"] = sorted([{k: v for k, v in r.items() if k != "label"} for r in self.rows],
                                 key=lambda r: r["plan_id"])
        expected = hashlib.sha256(json.dumps(payload, sort_keys=True, separators=(",", ":"),
                                             ensure_ascii=False, allow_nan=False).encode()).hexdigest()
        self.assertEqual(record["plan_sha256"], expected)

    def test_sort_and_label_changes_are_allowed(self):
        changed = list(reversed(copy.deepcopy(self.rows)))
        changed[0]["label"] = ""
        self.write_rows(changed)
        result = self.evaluate()
        self.assertEqual(result.returncode, 0, result.stdout)
        self.assertEqual(json.loads(result.stdout)["unlabelled"], 1)

    def test_legacy_evaluation_still_works(self):
        result = self.cli("evaluate", self.plan, "--results", self.export)
        self.assertEqual(result.returncode, 0, result.stdout)
        self.assertNotIn("provenance_binding", json.loads(result.stdout))
        result = self.cli("evaluate", self.plan)
        self.assertEqual(result.returncode, 0, result.stdout)

    def test_requires_explicit_results(self):
        self.refusal(self.cli("evaluate", self.plan, "--audit", self.snapshot))

    def test_export_tampering_fails_even_when_plan_values_unchanged(self):
        self.export.write_bytes(self.export.read_bytes().replace(b",v1", b",v2"))
        self.refusal(self.evaluate())

    def test_exact_membership_and_all_metadata(self):
        mutations = [self.rows[:-1], self.rows + [self.rows[0]]]
        for column in audit.PLAN_COLUMNS:
            if column == "label":
                continue
            altered = copy.deepcopy(self.rows)
            altered[0][column] += "changed"
            mutations.append(altered)
        for altered in mutations:
            with self.subTest(altered=altered[0].get("plan_id"), size=len(altered)):
                self.write_rows(altered)
                self.refusal(self.evaluate())

    def test_invalid_label_is_not_accepted_by_binding(self):
        self.rows[0]["label"] = "yes"
        self.write_rows(self.rows)
        self.refusal(self.evaluate())

    def test_changed_seed_fails_even_for_census_sample(self):
        result = self.cli("audit", self.export, "--plan", self.plan)
        value = json.loads(result.stdout)
        self.assertEqual(value["review_plan"]["row_count"], 6)
        value["review_plan"]["parameters"]["seed"] += 1
        self.save(value)
        self.refusal(self.evaluate())

    def test_malformed_record_types_versions_and_unknown_fields(self):
        cases = [("schema", "future"), ("algorithm", "future"), ("input_sha256", "bad"),
                 ("plan_sha256", None), ("row_count", True), ("row_count", -1),
                 ("columns", []), ("input_encoding", "utf-16"), ("parameters", [])]
        for key, value in cases:
            record = copy.deepcopy(self.record)
            record["review_plan"][key] = value
            with self.subTest(field=key):
                self.save(record)
                self.refusal(self.evaluate())
        record = copy.deepcopy(self.record)
        record["review_plan"]["command"] = "must never execute"
        self.save(record)
        self.refusal(self.evaluate())
        self.save({})
        self.refusal(self.evaluate())

    def test_parameters_reject_bool_nonfinite_and_impossible_limits(self):
        for key, value in [("seed", True), ("min_n", True), ("max_n", 0),
                           ("margin", True), ("margin", float("nan")),
                           ("margin", float("inf")), ("margin", 0),
                           ("margin", 1e-300), ("min_n", 99)]:
            with self.subTest(field=key, value=value):
                record = copy.deepcopy(self.record)
                record["review_plan"]["parameters"][key] = value
                self.save(record)
                self.refusal(self.evaluate())

    def test_duplicate_json_keys_and_invalid_json(self):
        for text in ['{"review_plan": {},' + json.dumps(self.record)[1:], "{bad", "[]"]:
            self.snapshot.write_text(text)
            self.refusal(self.evaluate())

    def test_strict_csv_headers_and_width(self):
        good = self.plan.read_text()
        header, body = good.split("\n", 1)
        for text in ["plan_id," + good, header.replace("score", "unknown") + "\n" + body,
                     good.rstrip("\n") + ",extra\n", header + '\n"unterminated']:
            self.plan.write_text(text)
            self.refusal(self.evaluate())
        altered = copy.deepcopy(self.rows)
        altered[0]["score"] += " "
        self.write_rows(altered)
        self.refusal(self.evaluate())

    def test_audit_plan_errors_emit_no_success_json(self):
        self.refusal(self.cli("audit", self.export, "--plan", self.root / "absent" / "plan.csv"))
        self.refusal(self.cli("audit", self.export, "--plan", self.plan, "--margin", "nan"))
        before = self.export.read_bytes()
        self.refusal(self.cli("audit", self.export, "--plan", self.export))
        self.assertEqual(self.export.read_bytes(), before)

    def test_audit_plan_reads_export_once(self):
        original_open = open
        reads = []
        def opening(path, *args, **kwargs):
            if os.fspath(path) == str(self.export):
                reads.append(args[0] if args else kwargs.get("mode", "r"))
            return original_open(path, *args, **kwargs)
        args = audit.build_parser().parse_args(["audit", str(self.export), "--plan", str(self.plan)])
        with mock.patch("builtins.open", side_effect=opening), contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
            self.assertEqual(audit._cli_audit(args), 0)
        self.assertEqual(reads, ["rb"])

    def test_cp932_export_replays_with_utf8_labelled_plan(self):
        self.export.write_bytes(self.export.read_text().replace("S1", "カナ").encode("cp932"))
        result = self.cli("audit", self.export, "--plan", self.plan)
        self.assertEqual(result.returncode, 0, result.stdout)
        self.snapshot.write_text(result.stdout)
        self.assertEqual(json.loads(result.stdout)["input_encoding"], "cp932")
        self.assertEqual(self.evaluate().returncode, 0)

    def test_encoding_alias_is_recorded_canonically(self):
        result = self.cli("audit", self.export, "--plan", self.plan, "--encoding", "utf8")
        self.assertEqual(result.returncode, 0, result.stdout)
        self.snapshot.write_text(result.stdout)
        self.assertEqual(json.loads(result.stdout)["review_plan"]["input_encoding"], "utf-8")
        self.assertEqual(self.evaluate().returncode, 0)

    def test_nonfinite_scores_are_inconsistent(self):
        for score in ("nan", "inf", "-inf"):
            result = audit.audit([{"source_id": "S", "reference_id": "R", "pathway": "a",
                                   "status": "MATCH", "score": score}])
            self.assertFalse(result["reconciliation"]["consistent"])
            self.assertIn("score out of range", " ".join(result["reconciliation"]["problems"]))


if __name__ == "__main__":
    unittest.main()

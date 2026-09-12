"""Command-line steward journey, checked against source CSV and truth CSV.

Only the synthetic input generator is imported. Counts, labels and report
expectations are calculated here without importing production statistics.
Both native clients run in isolated homes and leave real Vault indexes alone.
"""
import csv
import hashlib
import json
import math
import os
from pathlib import Path
import re
import subprocess
import sys
import tempfile
import unicodedata
from collections import Counter, defaultdict

PRODUCT = Path(__file__).resolve().parents[1]
BDS = PRODUCT / "bds.py"
EXAMPLES = PRODUCT / "examples"
sys.path.insert(0, str(EXAMPLES))
import mdm_archetype

PLAN_COLUMNS = "plan_id,stratum,source_id,reference_id,pathway,score,verifier,stratum_population,stratum_sampled,label".split(",")
AUDIT_GATES = {"M%d" % n for n in range(23, 33)}


def read_csv(path):
    with path.open(encoding="utf-8-sig", newline="") as stream:
        return list(csv.DictReader(stream))


def write_csv(path, rows, fields):
    with path.open("w", encoding="utf-8", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=fields, lineterminator="\n")
        writer.writeheader()
        writer.writerows(rows)


def write_json(path, payload):
    path.write_text(json.dumps(payload, indent=1, sort_keys=True), encoding="utf-8")


def index_fingerprints():
    home = Path.home()
    configs = {home / ".claude", home / ".codex"}
    configs.update(Path(os.environ[key]).expanduser() for key in
                   ("BROTHER_CONFIG_DIR", "CLAUDE_CONFIG_DIR", "CODEX_HOME")
                   if os.environ.get(key))
    result = {}
    for config in configs:
        for suffix in ("", "-wal", "-shm"):
            path = config / ("bm_vault_index.sqlite3" + suffix)
            if path.exists():
                stat = path.stat()
                result[str(path)] = (stat.st_size, stat.st_mtime_ns,
                                     hashlib.sha256(path.read_bytes()).hexdigest())
            else:
                result[str(path)] = None
    return result


def phone_key(raw):
    return "".join(c for c in unicodedata.normalize("NFKC", raw) if c in "0123456789")


def stratum(row):
    if row["status"] == "NO_MATCH":
        return "unmatched:" + row["pathway"]
    if not row["score"]:
        band = "no_score"
    else:
        value = float(row["score"])
        band = ("lt_0.80" if value < .80 else "0.80_0.90" if value < .90
                else "0.90_0.95" if value < .95 else "ge_0.95")
    return "match:" + row["pathway"] + ":" + band


def gate_lines(text):
    result = {}
    for line in text.splitlines():
        match = re.match(r"^\s*(PASS|FAIL|NO-DATA)\s+(M\d+\.[^\s]+)", line)
        if match:
            result[match.group(2)] = match.group(1)
    return result


class Journey:
    def __init__(self, root, client):
        self.work = root / (client + "-work")
        self.home = root / (client + "-home")
        self.work.mkdir()
        self.home.mkdir()
        config = self.home / ("." + client)
        config.mkdir()
        self.env = {key: os.environ[key] for key in ("PATH", "LANG", "LC_ALL", "SYSTEMROOT") if key in os.environ}
        self.env.update(HOME=str(self.home), BROTHER_CLIENT=client,
                        BROTHER_CONFIG_DIR=str(config), TMPDIR=str(self.work),
                        TMP=str(self.work), TEMP=str(self.work), PYTHONDONTWRITEBYTECODE="1")

    def cli(self, *args):
        return subprocess.run([sys.executable, str(BDS)] + list(args), cwd=self.work,
                              env=self.env, capture_output=True, text=True, timeout=90)

    def successful_json(self, *args):
        result = self.cli(*args)
        assert result.returncode == 0, (result.returncode, result.stdout, result.stderr)
        return json.loads(result.stdout)

    def generate(self):
        mdm_archetype.write_csvs(str(self.work), mdm_archetype.generate(11))
        self.rows = read_csv(self.work / "results.csv")
        self.truth = {row["source_id"]: row["true_reference_id"] for row in read_csv(self.work / "truth.csv")}
        assert len(self.rows) == len(self.truth) > 0
        self.status = dict(Counter(row["status"] for row in self.rows))
        self.pathways = dict(Counter(row["pathway"] for row in self.rows if row["status"] == "MATCH"))
        self.groups = defaultdict(list)
        for row in self.rows:
            self.groups[stratum(row)].append(row)

    def audit_counts(self):
        self.audit = self.successful_json("mdm-audit", "audit", "results.csv", "--plan", "plan.csv",
                                          "--margin", "0.10", "--seed", "7")
        write_json(self.work / "audit.json", self.audit)
        assert self.audit["n_rows"] == len(self.rows)
        assert self.audit["reconciliation"]["consistent"] is True
        assert self.audit["reconciliation"]["status_counts"] == self.status
        assert self.audit["reconciliation"]["pathway_counts"] == self.pathways
        assert self.audit["input_sha256"] == hashlib.sha256((self.work / "results.csv").read_bytes()).hexdigest()

    def collisions_and_hubs(self):
        references = Counter(row["reference_id"] for row in self.rows if row["status"] == "MATCH" and row["reference_id"])
        colliding = {key: count for key, count in references.items() if count > 1}
        expected = {"reference_ids_with_multiple_sources": len(colliding),
                    "rows_in_collisions": sum(colliding.values()),
                    "top": [list(item) for item in sorted(colliding.items(), key=lambda item: (-item[1], item[0]))[:10]]}
        assert self.audit["collisions"] == expected
        phones = Counter(phone_key(row["key_phone"]) for row in self.rows if phone_key(row["key_phone"]))
        hubs = {key for key, count in phones.items() if count >= 3}
        on_hubs = [row for row in self.rows if phone_key(row["key_phone"]) in hubs]
        assert self.audit["key_hubs"]["hubs"] == len(hubs)
        assert self.audit["key_hubs"]["rows_on_hubs"] == len(on_hubs)
        assert self.audit["key_hubs"]["matched_rows_on_hubs"] == sum(row["status"] == "MATCH" for row in on_hubs)
        assert self.audit["key_hubs"]["top"] == [list(item) for item in sorted(
            ((key, phones[key]) for key in hubs), key=lambda item: (-item[1], item[0]))[:10]]

    def plan_metadata(self):
        self.plan = read_csv(self.work / "plan.csv")
        assert list(self.plan[0]) == PLAN_COLUMNS
        assert len({row["plan_id"] for row in self.plan}) == len(self.plan)
        counts = Counter(row["stratum"] for row in self.plan)
        sources = {row["source_id"]: row for row in self.rows}
        assert set(counts) == set(self.groups)
        for row in self.plan:
            population = len(self.groups[row["stratum"]])
            selected = min(population, max(10, min(400, math.ceil(1.96 ** 2 * .25 / .1 ** 2))))
            assert int(row["stratum_population"]) == population
            assert int(row["stratum_sampled"]) == counts[row["stratum"]] == selected
            source = sources[row["source_id"]]
            assert stratum(source) == row["stratum"]
            for field in ("reference_id", "pathway", "score", "verifier"):
                assert row[field] == source[field]
            assert row["label"] == ""
        assert self.audit["review_plan"]["parameters"] == {"margin": .1, "min_n": 10, "max_n": 400, "seed": 7}
        assert self.audit["review_plan"]["row_count"] == len(self.plan)

    def seeds(self):
        self.successful_json("mdm-audit", "audit", "results.csv", "--plan", "repeat.csv", "--seed", "7")
        self.successful_json("mdm-audit", "audit", "results.csv", "--plan", "other.csv", "--seed", "8")
        assert (self.work / "plan.csv").read_bytes() == (self.work / "repeat.csv").read_bytes()
        assert {(row["stratum"], row["source_id"]) for row in self.plan} != {
            (row["stratum"], row["source_id"]) for row in read_csv(self.work / "other.csv")}

    def label_from_truth(self):
        self.labels = []
        for row in self.plan:
            truth = self.truth[row["source_id"]]
            positive = bool(truth) if row["stratum"].startswith("unmatched:") else bool(truth and truth == row["reference_id"])
            self.labels.append(dict(row, label="1" if positive else "0"))
        write_csv(self.work / "plan-labelled.csv", self.labels, PLAN_COLUMNS)
        assert sum(int(row["label"]) for row in self.labels) > 0

    def evaluate_bound(self):
        self.evaluation = self.successful_json("mdm-audit", "evaluate", "plan-labelled.csv", "--results", "results.csv", "--audit", "audit.json")
        binding = self.evaluation["provenance_binding"]
        assert binding["state"] == "PASS" and binding["source_authenticity"] == "NO-DATA"
        assert binding["input_sha256"] == hashlib.sha256((self.work / "results.csv").read_bytes()).hexdigest()
        assert self.evaluation["unlabelled"] == 0

    def independent_evaluation_counts(self):
        labelled = defaultdict(list)
        for row in self.labels:
            labelled[row["stratum"]].append(row)
        expected = [{"stratum": name, "population": len(self.groups[name]), "sampled": len(rows),
                     "labelled": len(rows), "positive": sum(int(row["label"]) for row in rows)}
                    for name, rows in sorted(labelled.items())]
        assert self.evaluation["strata"] == expected
        unmatched = [row for row in self.labels if row["stratum"].startswith("unmatched:")]
        ur = self.evaluation["unmatched_review"]
        assert ur["population"] == self.status["NO_MATCH"]
        assert ur["sampled"] == len(unmatched)
        assert ur["matchable_missed"] == sum(int(row["label"]) for row in unmatched)
        assert ur["reference_absent"] == sum(row["label"] == "0" for row in unmatched)
        self.expected_strata = expected

    def refused_changed_bytes(self):
        (self.work / "altered.csv").write_bytes((self.work / "results.csv").read_bytes().replace(b"\n", b"\r\n"))
        assert read_csv(self.work / "altered.csv") == self.rows
        result = self.cli("mdm-audit", "evaluate", "plan-labelled.csv", "--results", "altered.csv", "--audit", "audit.json")
        assert result.returncode == 2 and "export digest differs" in result.stdout

    def refused_plan_metadata(self):
        altered = [dict(row) for row in self.labels]
        altered[0]["stratum_population"] = str(int(altered[0]["stratum_population"]) + 1)
        write_csv(self.work / "altered-plan.csv", altered, PLAN_COLUMNS)
        result = self.cli("mdm-audit", "evaluate", "altered-plan.csv", "--results", "results.csv", "--audit", "audit.json")
        assert result.returncode == 2 and "membership or metadata differs" in result.stdout

    def hurried_claim(self):
        claim = {"id": "JOURNEY-HURRIED", "claim_type": "MASTER_DATA", "master_data": {"audit": {
            "computed": self.audit, "precision_basis": "similarity"}}}
        write_json(self.work / "hurried.json", claim)
        result = self.cli("check", "hurried.json")
        gates = gate_lines(result.stdout)
        assert result.returncode == 1
        assert gates["M25.pathway_precision"] == gates["M24.assignment_uniqueness"] == "FAIL"

    def audited_claim(self):
        strata = [entry for entry in self.expected_strata if entry["stratum"].startswith("match:")]
        precision = sum(entry["population"] * entry["positive"] / entry["sampled"] for entry in strata) / sum(entry["population"] for entry in strata)
        segments = defaultdict(lambda: {"n": 0, "matched": 0})
        for row in self.rows:
            entry = segments[row["segment"] or "(none)"]
            entry["n"] += 1
            entry["matched"] += row["status"] == "MATCH"
        verifier = dict(self.evaluation["verifier"], kind="llm")
        claim = {"id": "JOURNEY-AUDITED", "claim_type": "MASTER_DATA", "master_data": {
            "evaluation": {"claimed_precision": round(precision, 3)}, "audit": {
                "reported": {"n_input": len(self.rows), "status_counts": self.status, "pathway_counts": self.pathways},
                "computed": self.audit, "plan_seed": 7, "precision_basis": "review",
                "pathway_review": self.evaluation["pathway_review"], "unmatched_review": self.evaluation["unmatched_review"],
                "collisions_reviewed": True, "hubs_reviewed": True, "segment_disclosure": True,
                "segments": [dict(value, segment=name) for name, value in segments.items()], "verifier": verifier}}}
        write_json(self.work / "audited.json", claim)
        result = self.cli("check", "audited.json")
        assert result.returncode in (0, 1)
        gates = gate_lines(result.stdout)
        for number in list(range(23, 29)) + [32]:
            entries = [value for key, value in gates.items() if key.startswith("M%d." % number)]
            assert entries == ["PASS"], (number, entries, result.stdout)
        assert gates["M31.identifier_validity"] == "NO-DATA"

    def receipt(self):
        result = self.cli("receipt", "audited.json", "receipt.md")
        assert result.returncode == 0, result.stdout + result.stderr
        text = (self.work / "receipt.md").read_text()
        assert "M32.audit_provenance" in text and "M31.identifier_validity" in text

    def missing_and_legacy(self):
        result = self.cli("mdm-audit", "audit", "missing.csv")
        assert result.returncode == 2 and result.stdout.startswith("NO-DATA")
        write_json(self.work / "legacy.json", {"id": "JOURNEY-LEGACY", "claim_type": "MASTER_DATA", "master_data": {}})
        assert not any(key.split(".")[0] in AUDIT_GATES for key in gate_lines(self.cli("check", "legacy.json").stdout))

    def integrated_demo(self):
        destination = self.work / "demo"
        result = subprocess.run([sys.executable, str(EXAMPLES / "mdm_audit_demo.py"), "--json", "--write-examples",
                                 "--output-dir", str(destination)], cwd=self.work, env=self.env,
                                capture_output=True, text=True, timeout=90)
        assert result.returncode == 0, result.stdout + result.stderr
        report = json.loads(result.stdout)
        for name, filename in (("overclaim", "example-mdm-audit-overclaim.json"), ("audited", "example-mdm-audit-repaired.json")):
            actual = gate_lines(self.cli("check", str(destination / filename)).stdout)
            selected = {key: value for key, value in actual.items() if key.split(".")[0] in AUDIT_GATES}
            assert len(selected) == 10 and selected == report["verdicts"][name]


def main():
    before = index_fingerprints()
    steps = ["generate", "audit_counts", "collisions_and_hubs", "plan_metadata", "seeds", "label_from_truth",
             "evaluate_bound", "independent_evaluation_counts", "refused_changed_bytes", "refused_plan_metadata",
             "hurried_claim", "audited_claim", "receipt", "missing_and_legacy", "integrated_demo"]
    count = 0
    failures = []
    with tempfile.TemporaryDirectory(prefix="audit-journey-") as directory:
        for client in ("claude", "codex"):
            journey = Journey(Path(directory), client)
            for step in steps:
                count += 1
                try:
                    getattr(journey, step)()
                except Exception as exc:
                    failures.append((count, client, step, str(exc)))
                    print("FAIL %d %s/%s: %s" % (count, client, step, exc))
                    break
                print("ok %d %s/%s" % (count, client, step))
    count += 1
    if before != index_fingerprints():
        failures.append((count, "both", "real-index-isolation", "real Vault index changed"))
        print("FAIL %d real-index-isolation" % count)
    else:
        print("ok %d real-index-isolation" % count)
    if failures:
        print("JOURNEY FAIL (%d failure(s))" % len(failures))
        return 1
    print("JOURNEY PASS (%d checks)" % count)
    return 0


if __name__ == "__main__":
    sys.exit(main())

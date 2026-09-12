"""Synthetic benchmark generator for location-anchored account master matching.

Each rule in this generator stands in for a specific failure that shows up in real
master data matching runs. Shared headquarters phone numbers make the two hub chains
emit many-to-one key_phone matches, where one RHQ record is claimed for many distinct
outlets and is therefore wrong for every one of them. The three coverage-gap chains
keep the unmatched share dominated by genuine reference gaps, so a tool that treats
every unmatched row as a gap looks accurate on exactly those chains and nowhere else.
The LLM verifier is deliberately imperfect: it confirms some wrong candidates drawn
from low-score pairs and rejects some true candidates, so verifier CONFIRMED is not by
itself proof of correctness. Finally the two hit flags are positively dependent, which
means a capture-recapture estimate built from them overstates recall.
"""

from __future__ import annotations

import argparse
import csv
import json
import os
import random
import re
import tempfile

COLUMNS = [
    "source_id",
    "reference_id",
    "pathway",
    "status",
    "score",
    "verifier",
    "segment",
    "key_phone",
    "hit_a",
    "hit_b",
    "reference_snapshot",
    "model_version",
]

GAP_CHAIN_LIMIT = 3
GAP_COVERAGE = 0.35
DEFAULT_COVERAGE = 0.95
HUB_CHAINS = (4, 5)
HUB_PHONE_RATE = 0.60
REFERENCE_SNAPSHOT = "ref-2026-08"
MODEL_VERSION = "m-2026-09"

PATHWAYS = (
    "key_phone",
    "vector_auto",
    "llm_verified",
    "rejected_by_verifier",
    "no_candidate",
)


def _score_text(value):
    """Format a float score as a fixed three decimal string."""
    return "%.3f" % value


def generate(seed=11, n_chains=24, min_outlets=8, max_outlets=160):
    """Build one synthetic matching run with known ground truth.

    All randomness comes from a single random.Random(seed) and the draws are made in
    a fixed order, so the same arguments always produce byte-identical output.
    """
    rng = random.Random(seed)
    results = []
    truth = {}
    outlet_number = 0

    for chain_number in range(1, n_chains + 1):
        segment = "C%02d" % chain_number
        coverage = GAP_COVERAGE if chain_number <= GAP_CHAIN_LIMIT else DEFAULT_COVERAGE
        is_hub = chain_number in HUB_CHAINS

        n_outlets = rng.randint(min_outlets, max_outlets)

        for _ in range(n_outlets):
            outlet_number += 1
            source_id = "S%06d" % outlet_number

            covered = rng.random() < coverage
            true_reference_id = ("R%07d" % outlet_number) if covered else ""
            truth[source_id] = true_reference_id

            if is_hub:
                hub_phone = rng.random() < HUB_PHONE_RATE
                if hub_phone:
                    phone = "03-9000-%04d" % chain_number
                else:
                    phone = "03-%04d-%04d" % (outlet_number // 10000, outlet_number % 10000)
            else:
                hub_phone = False
                phone = "03-%04d-%04d" % (outlet_number // 10000, outlet_number % 10000)

            pathway = "no_candidate"
            status = "NO_MATCH"
            score = ""
            verifier = ""
            reference_id = ""
            hit_a = "0"
            hit_b = "0"

            if hub_phone:
                pathway = "key_phone"
                status = "MATCH"
                reference_id = "RHQ%02d" % chain_number
                score = _score_text(0.970)
                verifier = "SKIPPED"
                hit_a = "1"
                hit_b = "1" if rng.random() < 0.5 else "0"
            elif covered and rng.random() < 0.45:
                pathway = "key_phone"
                status = "MATCH"
                reference_id = true_reference_id
                score = _score_text(rng.uniform(0.95, 1.0))
                verifier = "SKIPPED"
                hit_a = "1"
                hit_b = "1" if rng.random() < 0.8 else "0"
            elif covered:
                u = rng.random()
                if u < 0.55:
                    pathway = "vector_auto"
                    status = "MATCH"
                    score = _score_text(rng.uniform(0.95, 1.0))
                    verifier = ""
                    hit_a = "0"
                    hit_b = "1"
                    if rng.random() < 0.97:
                        reference_id = true_reference_id
                    else:
                        reference_id = "RX%06d" % outlet_number
                elif u < 0.90:
                    score = _score_text(rng.uniform(0.80, 0.9499))
                    hit_a = "0"
                    hit_b = "1"
                    if rng.random() < 0.75:
                        pathway = "llm_verified"
                        status = "MATCH"
                        verifier = "CONFIRMED"
                        if rng.random() < 0.90:
                            reference_id = true_reference_id
                        else:
                            reference_id = "RX%06d" % outlet_number
                    else:
                        pathway = "rejected_by_verifier"
                        status = "NO_MATCH"
                        verifier = "REJECTED"
                        reference_id = ""
                else:
                    pathway = "no_candidate"
                    status = "NO_MATCH"
                    score = ""
                    verifier = ""
                    reference_id = ""
                    hit_a = "0"
                    hit_b = "0"
            else:
                if rng.random() < 0.15:
                    score = _score_text(rng.uniform(0.80, 0.9499))
                    hit_a = "0"
                    hit_b = "1"
                    if rng.random() < 0.30:
                        pathway = "llm_verified"
                        status = "MATCH"
                        verifier = "CONFIRMED"
                        reference_id = "RX%06d" % outlet_number
                    else:
                        pathway = "rejected_by_verifier"
                        status = "NO_MATCH"
                        verifier = "REJECTED"
                        reference_id = ""
                else:
                    pathway = "no_candidate"
                    status = "NO_MATCH"
                    score = ""
                    verifier = ""
                    reference_id = ""
                    hit_a = "0"
                    hit_b = "0"

            model_version = MODEL_VERSION if verifier in ("CONFIRMED", "REJECTED") else ""

            results.append(
                {
                    "source_id": source_id,
                    "reference_id": reference_id,
                    "pathway": pathway,
                    "status": status,
                    "score": score,
                    "verifier": verifier,
                    "segment": segment,
                    "key_phone": phone,
                    "hit_a": hit_a,
                    "hit_b": hit_b,
                    "reference_snapshot": REFERENCE_SNAPSHOT,
                    "model_version": model_version,
                }
            )

    return {
        "results": results,
        "truth": truth,
        "summary": summary(results, truth),
    }


def _is_correct_match(row, truth):
    reference_id = row["reference_id"]
    if not reference_id:
        return False
    return reference_id == truth.get(row["source_id"], "")


def summary(results, truth):
    """Return plain Python statistics for one matching run."""
    n_source = len(results)
    match_rows = [row for row in results if row["status"] == "MATCH"]
    no_match_rows = [row for row in results if row["status"] == "NO_MATCH"]
    n_match = len(match_rows)
    n_no_match = len(no_match_rows)

    covered_ids = [source_id for source_id, reference_id in truth.items() if reference_id]
    covered = len(covered_ids)

    correct_rows = [row for row in match_rows if _is_correct_match(row, truth)]
    n_correct = len(correct_rows)

    true_precision = (n_correct / n_match) if n_match else 0.0

    pathway_totals = {}
    pathway_correct = {}
    for row in match_rows:
        pathway = row["pathway"]
        pathway_totals[pathway] = pathway_totals.get(pathway, 0) + 1
        if _is_correct_match(row, truth):
            pathway_correct[pathway] = pathway_correct.get(pathway, 0) + 1
    true_precision_by_pathway = {
        pathway: (pathway_correct.get(pathway, 0) / total) for pathway, total in pathway_totals.items()
    }

    true_recall = (n_correct / covered) if covered else 0.0

    correct_ids = {row["source_id"] for row in correct_rows}
    missed_covered = sum(1 for source_id in covered_ids if source_id not in correct_ids)

    reference_counts = {}
    for row in match_rows:
        reference_id = row["reference_id"]
        if reference_id:
            reference_counts[reference_id] = reference_counts.get(reference_id, 0) + 1
    reference_ids_with_multiple_sources = sum(
        1 for count in reference_counts.values() if count >= 2
    )

    return {
        "n_source": n_source,
        "n_match": n_match,
        "n_no_match": n_no_match,
        "covered": covered,
        "coverage": (covered / n_source) if n_source else 0.0,
        "true_precision": true_precision,
        "true_precision_by_pathway": true_precision_by_pathway,
        "true_recall": true_recall,
        "missed_covered": missed_covered,
        "reference_ids_with_multiple_sources": reference_ids_with_multiple_sources,
    }


def label_plan(plan_rows, truth):
    """Return fresh dicts with a ground truth label added per plan stratum."""
    labeled = []
    for row in plan_rows:
        new_row = dict(row)
        stratum = new_row.get("stratum", "")
        source_id = new_row.get("source_id")
        true_reference_id = truth.get(source_id, "")
        if stratum.startswith("match:"):
            reference_id = new_row.get("reference_id", "")
            if reference_id and reference_id == true_reference_id:
                new_row["label"] = "1"
            else:
                new_row["label"] = "0"
        elif stratum.startswith("unmatched:"):
            new_row["label"] = "1" if true_reference_id else "0"
        else:
            new_row["label"] = ""
        labeled.append(new_row)
    return labeled


def write_csvs(out_dir, gen):
    """Write results.csv and truth.csv under out_dir, returning both paths."""
    os.makedirs(out_dir, exist_ok=True)
    results_path = os.path.join(out_dir, "results.csv")
    truth_path = os.path.join(out_dir, "truth.csv")

    with open(results_path, "w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=COLUMNS, lineterminator="\n")
        writer.writeheader()
        for row in gen["results"]:
            writer.writerow(row)

    with open(truth_path, "w", encoding="utf-8", newline="") as handle:
        writer = csv.writer(handle, lineterminator="\n")
        writer.writerow(["source_id", "true_reference_id"])
        for source_id, true_reference_id in gen["truth"].items():
            writer.writerow([source_id, true_reference_id])

    return (results_path, truth_path)


def _selftest():
    failures = []

    def check(condition, message):
        if not condition:
            failures.append(message)

    gen_a = generate(seed=11)
    gen_b = generate(seed=11)
    gen_c = generate(seed=12)

    check(gen_a["results"] == gen_b["results"], "results differ for the same seed")
    check(gen_a["truth"] == gen_b["truth"], "truth differs for the same seed")
    check(gen_a["summary"] == gen_b["summary"], "summary differs for the same seed")
    check(gen_a["results"] != gen_c["results"], "different seeds produced identical results")
    check(gen_a["truth"] != gen_c["truth"], "different seeds produced identical truth")

    results = gen_a["results"]
    truth = gen_a["truth"]
    gen_summary = gen_a["summary"]

    check(len(results) > 0, "no rows produced")
    check(all(list(row.keys()) == COLUMNS for row in results), "row columns do not match COLUMNS")
    check(
        all(isinstance(value, str) for row in results for value in row.values()),
        "a row carries a non string value",
    )
    check(
        all(row["reference_snapshot"] == REFERENCE_SNAPSHOT for row in results),
        "unexpected reference snapshot",
    )
    check(all(row["status"] in ("MATCH", "NO_MATCH") for row in results), "unexpected status")
    check(
        all(row["hit_a"] in ("0", "1") and row["hit_b"] in ("0", "1") for row in results),
        "unexpected hit flag",
    )
    check(
        all(row["verifier"] in ("", "SKIPPED", "CONFIRMED", "REJECTED") for row in results),
        "unexpected verifier",
    )
    check(all(row["pathway"] in PATHWAYS for row in results), "unexpected pathway")
    score_pattern = re.compile(r"^[0-9]+\.[0-9]{3}$")
    check(
        all(row["score"] == "" or score_pattern.match(row["score"]) for row in results),
        "unexpected score format",
    )
    check(
        all(
            (row["model_version"] == "") == (row["verifier"] not in ("CONFIRMED", "REJECTED"))
            for row in results
        ),
        "model version does not follow the verifier",
    )
    check(
        sorted(truth) == sorted(row["source_id"] for row in results),
        "truth keys and result source ids disagree",
    )
    check(
        all(row["status"] == "MATCH" or row["reference_id"] == "" for row in results),
        "a NO_MATCH row carries a reference id",
    )
    check(
        all(row["status"] == "NO_MATCH" or row["reference_id"] != "" for row in results),
        "a MATCH row carries an empty reference id",
    )

    hq_rows = [
        row
        for row in results
        if row["pathway"] == "key_phone" and row["reference_id"].startswith("RHQ")
    ]
    check(len(hq_rows) > 0, "no headquarters key_phone match was produced")
    check(
        all(row["segment"] in ("C04", "C05") for row in hq_rows),
        "a headquarters match sits outside the hub chains",
    )
    check(
        all(row["reference_id"] == "RHQ" + row["segment"][1:] for row in hq_rows),
        "a headquarters reference does not follow its chain",
    )
    check(
        all(row["reference_id"] != truth[row["source_id"]] for row in hq_rows),
        "a headquarters match equals the outlet truth",
    )
    check(
        all(row["status"] == "MATCH" and row["verifier"] == "SKIPPED" for row in hq_rows),
        "a headquarters match is not a skipped verifier match",
    )

    def coverage_of(segments):
        total = 0
        covered = 0
        for row in results:
            if row["segment"] in segments:
                total += 1
                if truth[row["source_id"]]:
                    covered += 1
        return covered, total

    rest_segments = {"C%02d" % number for number in range(4, 25)}
    gap_covered, gap_total = coverage_of({"C01", "C02", "C03"})
    rest_covered, rest_total = coverage_of(rest_segments)
    check(gap_total > 0 and rest_total > 0, "a segment group is missing from the run")
    gap_coverage = gap_covered / gap_total
    rest_coverage = rest_covered / rest_total
    check(gap_coverage < 0.6, "coverage gap chains are not below the target band")
    check(rest_coverage > 0.8, "non gap chains are not above the target band")
    check(gap_coverage < rest_coverage, "gap chains are not below the other chains")

    check(gen_summary["n_source"] == len(results), "n_source mismatch")
    check(
        gen_summary["n_match"] + gen_summary["n_no_match"] == len(results),
        "status counts do not add up",
    )
    check(
        gen_summary["covered"] == sum(1 for value in truth.values() if value),
        "covered count mismatch",
    )
    check(
        gen_summary["coverage"] == gen_summary["covered"] / gen_summary["n_source"],
        "coverage mismatch",
    )
    check(
        gen_summary["reference_ids_with_multiple_sources"] >= 1,
        "no many to one reference id was found",
    )
    check(summary(results, truth) == gen_summary, "summary is not reproducible")

    plan_rows = [
        {"source_id": "S000001", "stratum": "match:key_phone", "reference_id": "R0000001"},
        {"source_id": "S000001", "stratum": "match:vector_auto", "reference_id": "RX000001"},
        {"source_id": "S000002", "stratum": "unmatched:no_candidate", "reference_id": ""},
        {"source_id": "S000003", "stratum": "unmatched:no_candidate", "reference_id": ""},
    ]
    plan_truth = {"S000001": "R0000001", "S000002": "", "S000003": "R0000003"}
    plan_snapshot = [dict(row) for row in plan_rows]
    labeled = label_plan(plan_rows, plan_truth)
    check(plan_rows == plan_snapshot, "label_plan mutated its input")
    check(
        [row["label"] for row in labeled] == ["1", "0", "0", "1"],
        "label_plan produced wrong labels",
    )
    check(
        all(new is not old for new, old in zip(labeled, plan_rows)),
        "label_plan reused its input dicts",
    )

    with tempfile.TemporaryDirectory() as tmp_dir:
        results_path, truth_path = write_csvs(tmp_dir, gen_a)
        check(os.path.basename(results_path) == "results.csv", "results path has a wrong name")
        check(os.path.basename(truth_path) == "truth.csv", "truth path has a wrong name")
        check(
            os.path.exists(results_path) and os.path.exists(truth_path),
            "a csv file was not written",
        )
        with open(results_path, "r", encoding="utf-8") as handle:
            header = handle.readline().rstrip("\n")
        check(header == ",".join(COLUMNS), "results header mismatch")
        with open(results_path, "r", encoding="utf-8") as handle:
            written_results = list(csv.reader(handle))[1:]
        check(len(written_results) == len(results), "results row count mismatch")
        with open(truth_path, "r", encoding="utf-8") as handle:
            written_truth = list(csv.reader(handle))[1:]
        check(len(written_truth) == len(truth), "truth row count mismatch")
        with open(results_path, "rb") as handle:
            raw_results = handle.read()
        with open(truth_path, "rb") as handle:
            raw_truth = handle.read()
        check(b"\r" not in raw_results, "results.csv contains carriage returns")
        check(b"\r" not in raw_truth, "truth.csv contains carriage returns")

    return failures


def main(argv=None):
    parser = argparse.ArgumentParser(
        description="Generate a synthetic location-anchored account master matching run."
    )
    parser.add_argument("--out", help="directory for results.csv and truth.csv")
    parser.add_argument("--seed", type=int, default=11, help="seed for the single RNG")
    parser.add_argument("--chains", type=int, default=24, help="number of chains to generate")
    parser.add_argument("--selftest", action="store_true", help="run the internal checks")
    args = parser.parse_args(argv)

    if args.selftest:
        failures = _selftest()
        if failures:
            for failure in failures:
                print(failure)
            return 1
        print("SELFTEST PASS")
        return 0

    if not args.out:
        parser.error("--out DIR is required unless --selftest is given")

    gen = generate(seed=args.seed, n_chains=args.chains)
    write_csvs(args.out, gen)
    print(json.dumps(gen["summary"], indent=1, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

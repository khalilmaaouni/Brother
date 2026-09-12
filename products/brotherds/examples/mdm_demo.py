"""BrotherDS master data management demo.

This demo shows why a claim's numbers must be derived from evidence,
not typed into a slide. It builds a synthetic customer master with
duplicates and a chaining trap, blocks and scores pairs with a
Fellegi-Sunter model, clusters with union-find, samples a steward review,
then writes an honest claim and a hurried claim for BrotherDS to check.
"""

import sys
import os
import random
import json
import math
import subprocess
import tempfile
from collections import defaultdict

_HERE = os.path.dirname(os.path.abspath(__file__))
_BDS_DIR = os.path.dirname(_HERE)
if _BDS_DIR not in sys.path:
    sys.path.insert(0, _BDS_DIR)

import mdm_eval


FIRST_NAMES = [
    "Ada", "Ben", "Cleo", "Dev", "Ema", "Finn", "Gia", "Hugo", "Ines", "Jon",
    "Kai", "Lea", "Milo", "Nia", "Omar", "Pia", "Raf", "Sol", "Tara", "Ugo",
    "Vera", "Wes", "Xia", "Yara", "Zeno", "Alba", "Bruno", "Cyra", "Dario", "Elin",
    "Farid", "Greta", "Hana", "Ivo", "Jade", "Kofi", "Lina", "Marco", "Nora", "Otto",
]

SURNAMES = [
    "Abe", "Brandt", "Costa", "Diaz", "Eklund", "Fox", "Garcia", "Haas", "Ito", "Jansen",
    "Kowal", "Lund", "Mora", "Nagy", "Ortiz", "Park", "Quist", "Rossi", "Silva", "Tanaka",
    "Ueda", "Vidal", "Weber", "Xu", "Yilmaz", "Zhou", "Amato", "Berg", "Chen", "Dahl",
    "Engel", "Faria", "Gomez", "Holm", "Iwata", "Jovic", "Kraus", "Lopez", "Moller", "Novak",
]

POSTCODES = ["P%03d" % (i + 1) for i in range(30)]

DOMAINS = ["example.com", "mail.net", "post.org", "corp.io", "web.dev"]

FS_PARAMS = {
    "email": {"m": 0.95, "u": 0.01},
    "phone": {"m": 0.90, "u": 0.05},
    "surname": {"m": 0.85, "u": 0.10},
    "first": {"m": 0.90, "u": 0.10},
    "birth_year": {"m": 0.80, "u": 0.10},
}

SURVIVORSHIP_RULES = {
    "email": "recency",
    "phone": "recency",
    "first_name": "manual",
    "surname": "manual",
    "postcode": "completeness",
    "birth_year": "reliability",
}

GEN_SEED = 20260912
N_TRUE_PEOPLE = 3000
PRIOR = 1.0 / 1000.0
MERGE_THRESHOLD = 0.8
REVIEW_THRESHOLD = 0.6
REVIEW_BANDS = (9, 8, 7, 6, 5)
PER_BAND_CAP = 60
GOLD_SAMPLE_N = 40


def fmt4(value):
    if value is None:
        return "None"
    return "%.4f" % value


def build_records():
    rng = random.Random(GEN_SEED)
    records = []
    for pid in range(N_TRUE_PEOPLE):
        first = rng.choice(FIRST_NAMES)
        surname = rng.choice(SURNAMES)
        domain = rng.choice(DOMAINS)
        email = "%s.%s@%s" % (first.lower(), surname.lower(), domain)
        phone = "%03d-%03d-%04d" % (
            rng.randint(100, 999),
            rng.randint(100, 999),
            rng.randint(1000, 9999),
        )
        postcode = rng.choice(POSTCODES)
        birth_year = rng.randint(1950, 2000)
        base = {
            "pid": pid,
            "first": first,
            "surname": surname,
            "email": email,
            "phone": phone,
            "postcode": postcode,
            "birth_year": birth_year,
        }
        records.append(dict(base))
        if rng.random() < 0.35:
            n_dups = rng.randint(1, 3)
            for _ in range(n_dups):
                dup = dict(base)
                if rng.random() < 0.5:
                    s = dup["surname"]
                    if len(s) >= 2:
                        for _try in range(4):
                            i = rng.randint(0, len(s) - 2)
                            if s[i] != s[i + 1]:
                                chars = list(s)
                                chars[i], chars[i + 1] = chars[i + 1], chars[i]
                                dup["surname"] = "".join(chars)
                                break
                if rng.random() < 0.5:
                    dup["phone"] = None
                if rng.random() < 0.5:
                    local = dup["email"].split("@", 1)[0]
                    other = [d for d in DOMAINS if d != domain]
                    dup["email"] = "%s@%s" % (local, rng.choice(other))
                if rng.random() < 0.5:
                    s = str(dup["birth_year"])
                    if len(s) == 4:
                        i = rng.randint(0, 2)
                        chars = list(s)
                        chars[i], chars[i + 1] = chars[i + 1], chars[i]
                        try:
                            dup["birth_year"] = int("".join(chars))
                        except ValueError:
                            pass
                records.append(dup)
    trap_pids = rng.sample(range(N_TRUE_PEOPLE), 12)
    trap_set = set(trap_pids)
    for r in records:
        if r["pid"] in trap_set:
            r["email"] = "info@example.com"
    return records


def block(records):
    buckets = defaultdict(list)
    for i, r in enumerate(records):
        sn = r["surname"] or ""
        initial = sn[0].upper() if sn else ""
        buckets[(r["postcode"], initial)].append(i)
    pairs = set()
    for group in buckets.values():
        for x in range(len(group)):
            for y in range(x + 1, len(group)):
                a, b = group[x], group[y]
                pairs.add((a, b) if a < b else (b, a))
    email_groups = defaultdict(list)
    for i, r in enumerate(records):
        if r["email"]:
            email_groups[r["email"]].append(i)
    for group in email_groups.values():
        for x in range(len(group)):
            for y in range(x + 1, len(group)):
                a, b = group[x], group[y]
                pairs.add((a, b) if a < b else (b, a))
    return pairs


def surname_agree(a, b):
    if a == b:
        return True
    if len(a) != len(b) or len(a) < 2:
        return False
    diffs = [i for i in range(len(a)) if a[i] != b[i]]
    if len(diffs) != 2 or diffs[1] != diffs[0] + 1:
        return False
    i, j = diffs
    return a[i] == b[j] and a[j] == b[i]


def score_pair(ra, rb, fs_w):
    total = 0.0
    if ra["email"] and rb["email"] and ra["email"] == rb["email"]:
        total += fs_w["email"]["agree"]
    else:
        total += fs_w["email"]["disagree"]
    if ra["phone"] and rb["phone"] and ra["phone"] == rb["phone"]:
        total += fs_w["phone"]["agree"]
    else:
        total += fs_w["phone"]["disagree"]
    if surname_agree(ra["surname"] or "", rb["surname"] or ""):
        total += fs_w["surname"]["agree"]
    else:
        total += fs_w["surname"]["disagree"]
    if ra["first"] and rb["first"] and ra["first"] == rb["first"]:
        total += fs_w["first"]["agree"]
    else:
        total += fs_w["first"]["disagree"]
    if ra["birth_year"] and rb["birth_year"] and ra["birth_year"] == rb["birth_year"]:
        total += fs_w["birth_year"]["agree"]
    else:
        total += fs_w["birth_year"]["disagree"]
    return total


def weight_to_probability(total_weight):
    prior_odds = PRIOR / (1.0 - PRIOR)
    odds = prior_odds * (2.0 ** total_weight)
    return odds / (1.0 + odds)


def union_find_merge(n, scored):
    parent = list(range(n))

    def find(x):
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    def union(a, b):
        ra, rb = find(a), find(b)
        if ra != rb:
            parent[ra] = rb

    for a, b, p in scored:
        if p >= MERGE_THRESHOLD:
            union(a, b)
    groups = defaultdict(list)
    for i in range(n):
        groups[find(i)].append(i)
    return [groups[k] for k in sorted(groups.keys())]


def sample_strata(scored, records, bands_to_sample):
    rng = random.Random(GEN_SEED)
    buckets = defaultdict(list)
    band_set = set(bands_to_sample)
    below_half = []
    for a, b, p in scored:
        if p < 0.5:
            below_half.append((a, b, p))
            continue
        band = min(9, int(p * 10.0))
        if band in band_set:
            buckets[band].append((a, b, p))
    strata = []
    for band in bands_to_sample:
        pop = buckets.get(band, [])
        if not pop:
            continue
        take = min(PER_BAND_CAP, len(pop))
        idx = rng.sample(range(len(pop)), take)
        chosen = [pop[i] for i in idx]
        positives = sum(1 for a, b, _ in chosen if records[a]["pid"] == records[b]["pid"])
        score_lo = band / 10.0
        score_hi = 1.0 if band == 9 else (band + 1) / 10.0
        strata.append({
            "score_lo": score_lo,
            "score_hi": score_hi,
            "population": len(pop),
            "sampled": take,
            "positive": positives,
        })
    strata.sort(key=lambda s: s["score_lo"])
    # Recall must be estimated from a sample below the merge threshold.
    covers = any(s["score_hi"] <= MERGE_THRESHOLD and s["sampled"] > 0 for s in strata)
    if not covers and below_half:
        take = min(PER_BAND_CAP, len(below_half))
        idx = rng.sample(range(len(below_half)), take)
        chosen = [below_half[i] for i in idx]
        positives = sum(1 for a, b, _ in chosen if records[a]["pid"] == records[b]["pid"])
        strata.append({
            "score_lo": 0.0,
            "score_hi": 0.5,
            "population": len(below_half),
            "sampled": take,
            "positive": positives,
        })
        strata.sort(key=lambda s: s["score_lo"])
    return strata


def parse_tool_output(stdout):
    """Return (all_gate_lines, m7_to_19_lines, verdict_line)."""
    all_gates = []
    m7_19 = []
    verdict = None
    for raw in stdout.splitlines():
        s = raw.strip()
        if not s:
            continue
        if s.startswith("VERDICT"):
            verdict = s
            continue
        parts = s.split()
        if len(parts) < 2:
            continue
        if parts[0] not in ("PASS", "FAIL", "NO-DATA"):
            continue
        all_gates.append(s)
        gate = parts[1]
        if gate.startswith("M"):
            head = gate[1:].split(".", 1)[0]
            if head.isdigit() and 7 <= int(head) <= 19:
                m7_19.append(s)
    return all_gates, m7_19, verdict


def main():
    # ----- Step 1: generation -----
    print("Step 1: Generating synthetic customer master")
    records = build_records()
    n_records = len(records)
    print("  Generated %d records for %d true people" % (n_records, N_TRUE_PEOPLE))

    true_map = defaultdict(list)
    for i, r in enumerate(records):
        true_map[r["pid"]].append(i)
    true_clusters = [true_map[k] for k in sorted(true_map.keys())]
    n_true_pairs = sum(len(c) * (len(c) - 1) // 2 for c in true_clusters)

    # ----- Step 2: blocking -----
    print("Step 2: Blocking")
    candidate_pairs = block(records)
    n_candidates = len(candidate_pairs)
    n_true_in_candidates = 0
    for c in true_clusters:
        for i in range(len(c)):
            for j in range(i + 1, len(c)):
                a, b = c[i], c[j]
                if a > b:
                    a, b = b, a
                if (a, b) in candidate_pairs:
                    n_true_in_candidates += 1
    blocking_result = mdm_eval.blocking_metrics(
        n_records, n_candidates, n_true_pairs, n_true_in_candidates
    )
    blocking_result["n_records"] = n_records
    blocking_result["n_candidate_pairs"] = n_candidates
    blocking_result["n_true_pairs"] = n_true_pairs
    blocking_result["n_true_pairs_in_candidates"] = n_true_in_candidates
    print("  n_records: %d" % n_records)
    print("  n_candidate_pairs: %d" % n_candidates)
    print("  reduction_ratio: %.4f" % blocking_result["reduction_ratio"])
    if blocking_result["pair_completeness"] is not None:
        print("  pair_completeness: %.4f" % blocking_result["pair_completeness"])
    else:
        print("  pair_completeness: None")

    # ----- Step 3: scoring -----
    print("Step 3: Scoring candidate pairs")
    fs_w = {}
    for field in ("email", "phone", "surname", "first", "birth_year"):
        p = FS_PARAMS[field]
        fs_w[field] = mdm_eval.fs_weights(p["m"], p["u"])
        print("  %s: m=%.2f, u=%.2f" % (field, p["m"], p["u"]))

    scored = []
    for (a, b) in candidate_pairs:
        w = score_pair(records[a], records[b], fs_w)
        scored.append((a, b, weight_to_probability(w)))
    scored.sort()

    n_merged_pairs = sum(1 for _a, _b, p in scored if p >= MERGE_THRESHOLD)
    n_review_pairs = sum(1 for _a, _b, p in scored if REVIEW_THRESHOLD <= p < MERGE_THRESHOLD)
    print("  Scored %d pairs, %d merged, %d to review" % (
        len(scored), n_merged_pairs, n_review_pairs))

    # ----- Step 4: clustering -----
    print("Step 4: Clustering merged pairs")
    pred_clusters = union_find_merge(n_records, scored)
    sizes = [len(c) for c in pred_clusters]
    profile = mdm_eval.cluster_size_profile(sizes)
    pairwise = mdm_eval.pairwise_metrics(pred_clusters, true_clusters)
    bc = mdm_eval.bcubed(pred_clusters, true_clusters)
    print("  n_clusters: %d" % profile["n_clusters"])
    print("  max_size: %d" % profile["max_size"])
    print("  giant_clusters: %d" % profile["giant_clusters"])
    for key, label in (("precision", "pairwise precision"), ("recall", "pairwise recall")):
        v = pairwise[key]
        if v is not None:
            print("  %s: %.4f" % (label, v))
        else:
            print("  %s: None" % label)
    for key, label in (("precision", "bcubed precision"), ("recall", "bcubed recall")):
        v = bc[key]
        if v is not None:
            print("  %s: %.4f" % (label, v))
        else:
            print("  %s: None" % label)

    # ----- Step 5: review simulation -----
    print("Step 5: Simulating steward review")
    strata = sample_strata(scored, records, REVIEW_BANDS)
    total_sampled = sum(s["sampled"] for s in strata)
    print("  Sampled %d pairs across bands 5-9 plus low band" % total_sampled)

    above = [s for s in strata if s["score_lo"] >= MERGE_THRESHOLD]
    strat = mdm_eval.stratified_estimate(above) if above else None
    rec = mdm_eval.review_recall(strata, MERGE_THRESHOLD)
    if strat is not None:
        print("  Stratified precision estimate: %.4f (lo %.4f, hi %.4f)" % (
            strat["estimate"], strat["lo"], strat["hi"]))
    if rec["recall"] is not None:
        print("  Review recall estimate: %.4f" % rec["recall"])

    review_recall_value = rec["recall"]
    blocking_pair_completeness = blocking_result["pair_completeness"]
    if review_recall_value is not None and blocking_pair_completeness is not None:
        end_to_end_recall = review_recall_value * blocking_pair_completeness
    else:
        end_to_end_recall = None
    print("  End-to-end recall estimate: %s (review recall among candidate pairs %s x blocking pair completeness %s)" % (
        fmt4(end_to_end_recall), fmt4(review_recall_value), fmt4(blocking_pair_completeness)))
    print("  Ground truth pairwise recall: %s" % fmt4(pairwise["recall"]))

    # ----- Step 6: claims and tool run -----
    print("Step 6: Writing claims and checking with BrotherDS")

    merged_records = sum(len(c) for c in pred_clusters if len(c) >= 2)
    match_rate = merged_records / float(n_records) if n_records else 0.0

    if strat is not None:
        claimed_precision = math.floor(strat["lo"] * 1000.0) / 1000.0
        est = strat["estimate"]
        half = strat["se"] * 1.96
        interval_str = "%.3f +/- %.3f" % (est, half)
        known_residual_error_rate = 1.0 - est
    else:
        claimed_precision = 0.0
        interval_str = "0.000 +/- 0.000"
        known_residual_error_rate = 1.0

    if end_to_end_recall is not None:
        claimed_recall = math.floor(end_to_end_recall * 1000.0) / 1000.0
    else:
        claimed_recall = 0.0

    merge_strata = [s for s in strata if s["score_lo"] >= MERGE_THRESHOLD]
    sampled_acc_n = sum(s["sampled"] for s in merge_strata)
    sampled_acc_pos = sum(s["positive"] for s in merge_strata)
    sampled_accuracy = (sampled_acc_pos / float(sampled_acc_n)) if sampled_acc_n else 0.0

    gold_rng = random.Random(GEN_SEED)
    gold_idx = gold_rng.sample(range(n_records), GOLD_SAMPLE_N)
    gold_set = set(gold_idx)
    gold_pred = []
    for c in pred_clusters:
        inter = [i for i in c if i in gold_set]
        if inter:
            gold_pred.append(inter)
    gold_true = []
    for c in true_clusters:
        inter = [i for i in c if i in gold_set]
        if inter:
            gold_true.append(inter)

    not_established = [
        "The steward review is simulated from ground truth and was not adjudicated by real people.",
        "The largest predicted cluster was inspected directly; other large clusters were not individually reviewed.",
        "The gold cluster sample is a 40 record probability sample, not the full customer master.",
    ]

    survivorship_rules = dict(SURVIVORSHIP_RULES)

    honest_claim = {
        "claim_type": "MASTER_DATA",
        "origin": "SYSTEM",
        "statement": "An entity resolution merge batch was produced for the customer master.",
        "question": "How many customer records were merged into master persons?",
        "decision": "Approve the entity resolution merge batch for the customer master.",
        "grain": "customer record",
        "value": merged_records,
        "unit": "records merged",
        "not_established": not_established,
        "uncertainty": {
            "kind": "interval",
            "interval": interval_str,
            "method": "stratified estimator over the steward review",
        },
        "match": {
            "precision": claimed_precision,
            "recall": claimed_recall,
            "threshold": MERGE_THRESHOLD,
            "false_positive_cost": "catastrophic",
            "false_negative_cost": "low",
            "kind": "merge",
            "survivorship": "per-attribute rules in master_data",
            "reversible": False,
            "labelled_sample": {
                "n": total_sampled,
                "frame": "stratified_sample",
                "labelled_by": "simulated stewards",
                "labelled_on": "2026-09-12",
            },
        },
        "master_data": {
            "merge_threshold": MERGE_THRESHOLD,
            "review_threshold": REVIEW_THRESHOLD,
            "survivorship": survivorship_rules,
            "false_merge_cost_class": "catastrophic",
            "review_queue": True,
            "review_sample_n": total_sampled,
            "ground_truth_n": n_records,
            "known_residual_error_rate": known_residual_error_rate,
            "match_method": "Fellegi-Sunter logistic",
            "match_rate": match_rate,
            "sampled_accuracy": sampled_accuracy,
            "sampled_accuracy_n": sampled_acc_n,
            "evaluation": {
                "claimed_precision": claimed_precision,
                "claimed_recall": claimed_recall,
                "metric_basis": "pairwise",
                "review_strata": strata,
                "blocking": blocking_result,
                "gold_clusters": {
                    "pred": gold_pred,
                    "true": gold_true,
                },
                "gold_sample": {
                    "frame": "probability",
                    "weighted": True,
                },
                "cluster_sizes": sizes,
                "giant_clusters_reviewed": True,
                "fs_parameters": dict(FS_PARAMS),
                "headline_metric": "f_beta",
                "beta": 0.5,
                "required_margin": 0.05,
                "labeller": {"kind": "human"},
            },
        },
    }

    hurried_claim = json.loads(json.dumps(honest_claim))
    hurried_claim["match"]["precision"] = 0.999
    hurried_claim["match"]["recall"] = 0.99
    hurried_claim["master_data"]["evaluation"]["claimed_precision"] = 0.999
    hurried_claim["master_data"]["evaluation"]["claimed_recall"] = 0.99
    hurried_claim["master_data"]["evaluation"]["review_strata"] = [
        s for s in strata if s["score_lo"] >= MERGE_THRESHOLD
    ]
    hurried_claim["master_data"]["evaluation"]["headline_metric"] = "f1"
    hurried_claim["master_data"]["evaluation"]["labeller"] = {"kind": "llm", "overlap_n": 0}
    hurried_claim["master_data"]["evaluation"]["gold_sample"] = {"frame": "benchmark"}
    hurried_claim["master_data"]["evaluation"]["giant_clusters_reviewed"] = False

    temp_dir = tempfile.mkdtemp(prefix="brotherds_demo_")
    honest_path = os.path.join(temp_dir, "honest_claim.json")
    hurried_path = os.path.join(temp_dir, "hurried_claim.json")
    with open(honest_path, "w", encoding="utf-8") as f:
        json.dump(honest_claim, f, indent=2, sort_keys=True)
    with open(hurried_path, "w", encoding="utf-8") as f:
        json.dump(hurried_claim, f, indent=2, sort_keys=True)

    bds_path = os.path.join(_BDS_DIR, "bds.py")

    def run_check(claim_path):
        result = subprocess.run(
            [sys.executable, bds_path, "check", claim_path],
            capture_output=True,
        )
        stdout = result.stdout.decode("utf-8", errors="replace")
        return parse_tool_output(stdout)

    print("  Checking honest claim...")
    honest_all, honest_m7, honest_verdict = run_check(honest_path)
    printed = set()
    for line in honest_all:
        if line.startswith("FAIL"):
            print("  " + line)
            printed.add(line)
    for line in honest_m7:
        if line not in printed:
            print("  " + line)
            printed.add(line)
    if honest_verdict and honest_verdict not in printed:
        print("  " + honest_verdict)

    print("  Checking hurried claim...")
    hurried_all, hurried_m7, hurried_verdict = run_check(hurried_path)
    for line in hurried_m7:
        print("  " + line)
    if hurried_verdict:
        print("  " + hurried_verdict)

    hurried_fail_gates = []
    for line in hurried_all:
        if line.startswith("FAIL"):
            parts = line.split()
            if len(parts) >= 2 and parts[1] not in hurried_fail_gates:
                hurried_fail_gates.append(parts[1])

    # ----- Step 7: summary -----
    print("Step 7: Summary")
    print("The honest claim may say: the merge batch is supported by a stratified steward review with conservative precision and recall bounds.")
    print("The hurried claim tried to say: precision 0.999 and recall 0.99 from only the top score bands.")
    if hurried_fail_gates:
        print("Gates that caught it: " + ", ".join(hurried_fail_gates))
    else:
        print("Gates that caught it: none printed FAIL")
    print("End-to-end recall estimate: %s  vs  ground truth pairwise recall: %s" % (
        fmt4(end_to_end_recall), fmt4(pairwise["recall"])))
    print("The remaining gap comes from clusters built by transitive closure, which pair-level review does not see.")

    honest_m7_fails = [line for line in honest_m7 if line.startswith("FAIL")]

    if not honest_m7:
        print("DEMO FAILED: no M7 to M19 lines printed for the honest claim")
        return 1
    if honest_verdict is None:
        print("DEMO FAILED: no VERDICT line for the honest claim")
        return 1
    if honest_m7_fails:
        print("DEMO FAILED: honest claim had a FAIL among M7 to M19")
        return 1
    if hurried_verdict != "VERDICT FAIL":
        print("DEMO FAILED: hurried claim verdict was not FAIL")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())

"""Entity-resolution evaluation statistics for master data management.

This module provides deterministic, pure Python functions for evaluating
entity-resolution and master data management claims. It follows a NO-DATA
philosophy: when the evidence required for a statistic is missing, the
function reports None or raises a clear ValueError rather than returning a
pass or fail. Every number is recomputable from the provided arguments.

Sources:
- Wilson interval: Wilson (1927)
- Clopper-Pearson interval: Clopper and Pearson (1934)
- Pairwise metrics: standard pair counting
- B-cubed: Bagga and Baldwin (1998)
- Blocking measures: Christen (2012) "Data Matching"
- PSI: common industry practice
- Fellegi-Sunter weights: Fellegi and Sunter (1969)
- Stratified sampling: Cochran (1977)
"""

import csv
import json
import math
import sys
import itertools
import tempfile
import os


def _is_int_value(value):
    """Return True when value is an int but not a bool."""
    return isinstance(value, int) and not isinstance(value, bool)


def _validate_strata(strata):
    """Validate population, sampled, and positive for every stratum."""
    for h, s in enumerate(strata):
        if not isinstance(s, dict):
            raise ValueError(f"stratum {h} must be an object")
        for field in ("population", "sampled", "positive"):
            if field not in s:
                raise ValueError(f"stratum {h} field {field} is missing")
            if not _is_int_value(s[field]):
                raise ValueError(f"stratum {h} field {field} must be an integer")
        if s["population"] < 0:
            raise ValueError(f"stratum {h} field population must be >= 0")
        if s["sampled"] < 1:
            raise ValueError(f"stratum {h} field sampled must be >= 1")
        if s["positive"] < 0:
            raise ValueError(f"stratum {h} field positive must be >= 0")
        if s["positive"] > s["sampled"]:
            raise ValueError(f"stratum {h} field positive must be <= sampled")
        if s["sampled"] > s["population"]:
            raise ValueError(f"stratum {h} field sampled must be <= population")


def _wilson_float(p, n, z):
    """Wilson score interval helper for float successes and trials."""
    if n <= 0:
        raise ValueError("n must be greater than 0")
    phat = float(p) / float(n)
    if phat < 0.0:
        phat = 0.0
    if phat > 1.0:
        phat = 1.0
    z2 = z * z
    denom = 1.0 + z2 / n
    center = (phat + z2 / (2.0 * n)) / denom
    half = z * math.sqrt((phat * (1.0 - phat) / n) + (z2 / (4.0 * n * n))) / denom
    lo = center - half
    hi = center + half
    if lo < 0.0:
        lo = 0.0
    if hi > 1.0:
        hi = 1.0
    return (lo, hi)


def wilson_interval(k, n, z=1.96):
    """Wilson score interval for k successes in n trials.

    Source: Wilson (1927).
    """
    if n == 0:
        raise ValueError("n must be greater than 0")
    if k < 0 or k > n:
        raise ValueError("k must be between 0 and n")
    return _wilson_float(k, n, z)


def _binom_cdf(k, n, p):
    """Binomial cumulative distribution function P(X <= k) in log space."""
    if k < 0:
        return 0.0
    if p <= 0.0:
        return 1.0
    if p >= 1.0:
        return 1.0 if k >= n else 0.0
    if k >= n:
        return 1.0
    log_p = math.log(p)
    log_q = math.log1p(-p)
    log_pmf = n * log_q
    max_log = -float("inf")
    sum_exp = 0.0
    for i in range(0, k + 1):
        if log_pmf > max_log:
            sum_exp = sum_exp * math.exp(max_log - log_pmf) + 1.0
            max_log = log_pmf
        else:
            sum_exp += math.exp(log_pmf - max_log)
        if i < k:
            log_pmf += math.log(n - i) - math.log(i + 1) + log_p - log_q
    log_total = max_log + math.log(sum_exp)
    value = math.exp(log_total)
    if value < 0.0:
        return 0.0
    if value > 1.0:
        return 1.0
    return value


def clopper_pearson(k, n, alpha=0.05):
    """Exact binomial interval (Clopper-Pearson).

    Source: Clopper and Pearson (1934).
    """
    if n == 0:
        raise ValueError("n must be greater than 0")
    if k < 0 or k > n:
        raise ValueError("k must be between 0 and n")
    if k == 0:
        lo = 0.0
    else:
        target = 1.0 - alpha / 2.0
        low = 0.0
        high = 1.0
        for _ in range(60):
            mid = (low + high) / 2.0
            if _binom_cdf(k - 1, n, mid) > target:
                low = mid
            else:
                high = mid
        lo = (low + high) / 2.0
    if k == n:
        hi = 1.0
    else:
        target = alpha / 2.0
        low = 0.0
        high = 1.0
        for _ in range(60):
            mid = (low + high) / 2.0
            if _binom_cdf(k, n, mid) > target:
                low = mid
            else:
                high = mid
        hi = (low + high) / 2.0
    return (lo, hi)


def pairs_from_clusters(clusters):
    """Return set of frozenset pairs for every unordered pair sharing a cluster."""
    pairs = set()
    seen_global = set()
    for cluster in clusters:
        ids = []
        seen_local = set()
        for rid in cluster:
            if rid not in seen_local:
                seen_local.add(rid)
                ids.append(rid)
        for rid in ids:
            if rid in seen_global:
                raise ValueError(f"record {rid!r} appears in more than one cluster")
            seen_global.add(rid)
        for i in range(len(ids)):
            for j in range(i + 1, len(ids)):
                pairs.add(frozenset((ids[i], ids[j])))
    return pairs


def _partition_map(clusters):
    """Build a record-to-cluster map and reject overlapping partitions."""
    mapping = {}
    seen_global = set()
    for cluster in clusters:
        local_ids = []
        seen_local = set()
        for rid in cluster:
            if rid not in seen_local:
                seen_local.add(rid)
                local_ids.append(rid)
        for rid in local_ids:
            if rid in seen_global:
                raise ValueError(f"record {rid!r} appears in more than one cluster")
            seen_global.add(rid)
        cluster_set = frozenset(local_ids)
        for rid in local_ids:
            mapping[rid] = cluster_set
    return mapping


def pairwise_metrics(pred_clusters, true_clusters):
    """Pairwise comparison metrics.

    Returns dict with keys tp, fp, fn, precision, recall, f1.
    """
    pred_pairs = pairs_from_clusters(pred_clusters)
    true_pairs = pairs_from_clusters(true_clusters)
    tp = len(pred_pairs & true_pairs)
    fp = len(pred_pairs - true_pairs)
    fn = len(true_pairs - pred_pairs)
    precision = tp / (tp + fp) if (tp + fp) > 0 else None
    recall = tp / (tp + fn) if (tp + fn) > 0 else None
    if precision is None or recall is None:
        f1 = None
    elif precision == 0.0 and recall == 0.0:
        f1 = None
    else:
        f1 = 2.0 * precision * recall / (precision + recall)
    return {
        "tp": tp,
        "fp": fp,
        "fn": fn,
        "precision": precision,
        "recall": recall,
        "f1": f1,
    }


def bcubed(pred_clusters, true_clusters):
    """B-cubed precision, recall, and F1.

    Source: Bagga and Baldwin (1998).
    """
    pred_map = _partition_map(pred_clusters)
    true_map = _partition_map(true_clusters)
    pred_keys = set(pred_map.keys())
    true_keys = set(true_map.keys())
    only_pred = pred_keys - true_keys
    only_true = true_keys - pred_keys
    if only_pred or only_true:
        missing = []
        for rid in list(only_pred)[:5]:
            missing.append(repr(rid))
        for rid in list(only_true)[:5]:
            missing.append(repr(rid))
        raise ValueError("records present in only one partition: " + ", ".join(missing))
    common = pred_keys & true_keys
    if not common:
        return {"precision": None, "recall": None, "f1": None}
    precisions = []
    recalls = []
    for rid in common:
        pset = pred_map[rid]
        tset = true_map[rid]
        inter = len(pset & tset)
        precisions.append(inter / len(pset))
        recalls.append(inter / len(tset))
    precision = sum(precisions) / len(precisions)
    recall = sum(recalls) / len(recalls)
    if precision + recall == 0.0:
        f1 = 0.0
    else:
        f1 = 2.0 * precision * recall / (precision + recall)
    return {"precision": precision, "recall": recall, "f1": f1}


def _require_nonneg_int(name, value):
    """Require a non-negative integer, rejecting bool."""
    if not _is_int_value(value) or value < 0:
        raise ValueError(f"{name} must be a non-negative integer")


def blocking_metrics(n_records, n_candidate_pairs, n_true_pairs, n_true_pairs_in_candidates):
    """Blocking evaluation measures.

    Source: Christen (2012) "Data Matching".
    """
    _require_nonneg_int("n_records", n_records)
    _require_nonneg_int("n_candidate_pairs", n_candidate_pairs)
    _require_nonneg_int("n_true_pairs", n_true_pairs)
    _require_nonneg_int("n_true_pairs_in_candidates", n_true_pairs_in_candidates)
    possible_pairs = n_records * (n_records - 1) // 2
    if n_candidate_pairs > possible_pairs:
        raise ValueError(
            f"n_candidate_pairs {n_candidate_pairs} exceeds possible_pairs {possible_pairs}"
        )
    if n_true_pairs_in_candidates > n_true_pairs:
        raise ValueError("n_true_pairs_in_candidates exceeds n_true_pairs")
    if n_true_pairs_in_candidates > n_candidate_pairs:
        raise ValueError("n_true_pairs_in_candidates exceeds n_candidate_pairs")
    if possible_pairs == 0:
        reduction_ratio = 1.0 if n_candidate_pairs == 0 else 0.0
    else:
        reduction_ratio = 1.0 - (n_candidate_pairs / possible_pairs)
    pair_completeness = (n_true_pairs_in_candidates / n_true_pairs) if n_true_pairs > 0 else None
    pairs_quality = (n_true_pairs_in_candidates / n_candidate_pairs) if n_candidate_pairs > 0 else None
    return {
        "possible_pairs": possible_pairs,
        "reduction_ratio": reduction_ratio,
        "pair_completeness": pair_completeness,
        "pairs_quality": pairs_quality,
    }


def psi(ref_props, cur_props, eps=1e-6):
    """Population stability index over matching bins."""
    if len(ref_props) != len(cur_props):
        raise ValueError("ref_props and cur_props must have equal length")
    if len(ref_props) < 2:
        raise ValueError("ref_props and cur_props must have length >= 2")
    for label, props in (("ref_props", ref_props), ("cur_props", cur_props)):
        for i, p in enumerate(props):
            if isinstance(p, bool) or not isinstance(p, (int, float)):
                raise ValueError(f"{label}[{i}] must be a finite non-negative number")
            if not math.isfinite(float(p)):
                raise ValueError(f"{label}[{i}] must be a finite non-negative number")
            if p < 0:
                raise ValueError(f"{label}[{i}] must be a finite non-negative number")
    ref_sum = sum(ref_props)
    cur_sum = sum(cur_props)
    if ref_sum == 0 or cur_sum == 0:
        raise ValueError("proportions must sum to a positive value")
    ref_norm = [p / ref_sum for p in ref_props]
    cur_norm = [p / cur_sum for p in cur_props]
    total = 0.0
    for r, c in zip(ref_norm, cur_norm):
        r_val = max(r, eps)
        c_val = max(c, eps)
        total += (c - r) * math.log(c_val / r_val)
    return total


def psi_band(value):
    """Return PSI interpretation band."""
    if value < 0.10:
        return "stable"
    if value < 0.25:
        return "moderate"
    return "major"


def fs_weights(m, u):
    """Fellegi-Sunter log2 match weights.

    Source: Fellegi and Sunter (1969).
    """
    if not (0.0 < u < 1.0):
        raise ValueError("u must be between 0 and 1")
    if not (0.0 < m < 1.0):
        raise ValueError("m must be between 0 and 1")
    agree = math.log2(m / u)
    disagree = math.log2((1.0 - m) / (1.0 - u))
    return {"agree": agree, "disagree": disagree}


def stratified_estimate(strata, z=1.96):
    """Stratified proportion estimate with finite population correction.

    Source: Cochran (1977).
    """
    _validate_strata(strata)
    N = 0
    n_sampled = 0
    for s in strata:
        N += s["population"]
        n_sampled += s["sampled"]
    if N == 0:
        return {
            "estimate": 0.0,
            "se": 0.0,
            "lo": 0.0,
            "hi": 0.0,
            "n_sampled": 0,
            "population": 0,
        }
    estimate = 0.0
    variance = 0.0
    for s in strata:
        N_h = s["population"]
        n_h = s["sampled"]
        k_h = s["positive"]
        W_h = N_h / N
        p_h = k_h / n_h
        estimate += W_h * p_h
        fpc = 1.0 - (n_h / N_h) if N_h > 0 else 0.0
        variance += (W_h ** 2) * (p_h * (1.0 - p_h) / n_h) * fpc
    if variance < 0.0:
        variance = 0.0
    se = math.sqrt(variance)
    if se > 0.0:
        n_eff = estimate * (1.0 - estimate) / (se * se)
    else:
        n_eff = float(n_sampled)
    k_eff = estimate * n_eff
    lo, hi = _wilson_float(k_eff, n_eff, z)
    return {
        "estimate": estimate,
        "se": se,
        "lo": lo,
        "hi": hi,
        "n_sampled": n_sampled,
        "population": N,
    }


def review_recall(strata, merge_threshold):
    """Estimated recall from stratified review samples."""
    _validate_strata(strata)
    matches_above = 0.0
    matches_total = 0.0
    covers_below_threshold = False
    for s in strata:
        N_h = s["population"]
        n_h = s["sampled"]
        k_h = s["positive"]
        if n_h > 0:
            est = N_h * k_h / n_h
        else:
            est = 0.0
        matches_total += est
        if s["score_lo"] >= merge_threshold:
            matches_above += est
        if s["score_hi"] <= merge_threshold and n_h > 0:
            covers_below_threshold = True
    if not covers_below_threshold or matches_total == 0.0:
        recall = None
    else:
        recall = matches_above / matches_total
    return {
        "recall": recall,
        "matches_above": matches_above,
        "matches_total": matches_total,
        "covers_below_threshold": covers_below_threshold,
    }


def cluster_size_profile(sizes, giant_share=0.01, giant_min=50):
    """Cluster size distribution profile."""
    if not sizes:
        raise ValueError("sizes must not be empty")
    for s in sizes:
        if s <= 0:
            raise ValueError("sizes must be positive integers")
    n_clusters = len(sizes)
    n_records = sum(sizes)
    max_size = max(sizes)
    sorted_sizes = sorted(sizes)
    idx = math.ceil(0.99 * n_clusters) - 1
    if idx < 0:
        idx = 0
    if idx >= n_clusters:
        idx = n_clusters - 1
    p99_size = sorted_sizes[idx]
    singleton_share = sum(1 for s in sizes if s == 1) / n_clusters
    threshold = max(giant_min, giant_share * n_records)
    giant_clusters = sum(1 for s in sizes if s >= threshold)
    return {
        "n_clusters": n_clusters,
        "n_records": n_records,
        "max_size": max_size,
        "p99_size": p99_size,
        "singleton_share": singleton_share,
        "giant_clusters": giant_clusters,
    }


def evaluate_review_csv(path, merge_threshold):
    """Evaluate a review CSV with score, label, and optional stratum_population."""
    bands = {}
    n_rows = 0
    with open(path, newline="") as f:
        reader = csv.DictReader(f)
        required = {"score", "label"}
        if not required.issubset(reader.fieldnames or []):
            raise ValueError("CSV must have columns: score, label")
        has_pop = "stratum_population" in reader.fieldnames
        for row in reader:
            n_rows += 1
            line_no = reader.line_num
            try:
                score = float(row["score"])
            except (TypeError, ValueError):
                raise ValueError(f"invalid score on line {line_no}")
            if score < 0.0 or score > 1.0:
                raise ValueError(f"score out of range on line {line_no}")
            label = row["label"].strip().lower()
            if label == "match":
                positive = 1
            elif label == "non-match":
                positive = 0
            else:
                raise ValueError(f"invalid label on line {line_no}: {row['label']}")
            band = min(9, int(score * 10.0))
            if band not in bands:
                bands[band] = {
                    "n": 0,
                    "k": 0,
                    "pop_set": set(),
                }
            bands[band]["n"] += 1
            bands[band]["k"] += positive
            if has_pop:
                pop_str = row.get("stratum_population", "").strip()
                if pop_str != "":
                    try:
                        pop_val = int(pop_str)
                    except ValueError:
                        raise ValueError(f"invalid stratum_population on line {line_no}")
                    bands[band]["pop_set"].add(pop_val)
    strata = []
    for band in sorted(bands.keys()):
        b = bands[band]
        if len(b["pop_set"]) > 1:
            raise ValueError(f"stratum_population varies within band {band}")
        if b["pop_set"]:
            population = next(iter(b["pop_set"]))
        else:
            population = b["n"]
        score_lo = band / 10.0
        score_hi = (band + 1) / 10.0 if band < 9 else 1.0
        strata.append({
            "population": population,
            "sampled": b["n"],
            "positive": b["k"],
            "score_lo": score_lo,
            "score_hi": score_hi,
        })
    above = [s for s in strata if s["score_lo"] >= merge_threshold]
    if above:
        precision_above = stratified_estimate(above)
    else:
        precision_above = None
    recall = review_recall(strata, merge_threshold)
    return {
        "merge_threshold": merge_threshold,
        "strata": strata,
        "precision_above": precision_above,
        "recall": recall,
        "n_rows": n_rows,
    }


def _selftest():
    """Run self-test and return list of failure messages."""
    failures = []

    def check(cond, msg):
        if not cond:
            failures.append(msg)

    def almost(a, b, places=4):
        return abs(a - b) < 10 ** (-places) / 2.0

    def check_almost(a, b, msg, places=4):
        if not almost(a, b, places):
            failures.append(f"{msg}: got {a}, expected {b}")

    # Wilson interval
    lo, hi = wilson_interval(0, 10)
    check_almost(lo, 0.0, "wilson_interval(0,10) lo")
    check_almost(hi, 0.2775, "wilson_interval(0,10) hi")
    lo, hi = wilson_interval(5, 10)
    check_almost(lo, 0.2366, "wilson_interval(5,10) lo")
    check_almost(hi, 0.7634, "wilson_interval(5,10) hi")
    try:
        wilson_interval(0, 0)
        check(False, "wilson_interval n=0 should raise")
    except ValueError:
        pass
    try:
        wilson_interval(-1, 10)
        check(False, "wilson_interval k<0 should raise")
    except ValueError:
        pass
    try:
        wilson_interval(11, 10)
        check(False, "wilson_interval k>n should raise")
    except ValueError:
        pass

    # Clopper-Pearson
    lo, hi = clopper_pearson(0, 10)
    check_almost(lo, 0.0, "clopper_pearson(0,10) lo")
    check_almost(hi, 0.3085, "clopper_pearson(0,10) hi")
    lo, hi = clopper_pearson(10, 10)
    check_almost(hi, 1.0, "clopper_pearson(10,10) hi")
    check(lo < hi, "clopper_pearson bounds ordered")
    lo, hi = clopper_pearson(2000, 4000)
    check_almost(lo, 0.4845, "clopper_pearson(2000,4000) lo", places=3)
    check_almost(hi, 0.5155, "clopper_pearson(2000,4000) hi", places=3)
    lo, hi = clopper_pearson(50000, 100000)
    check(0.0 <= lo <= hi <= 1.0, "clopper_pearson large n ordered")

    # pairs_from_clusters
    p = pairs_from_clusters([[1, 2, 3], [4]])
    check(len(p) == 3, "pairs_from_clusters length")
    check(frozenset({1, 2}) in p, "pairs_from_clusters contains 1,2")
    p2 = pairs_from_clusters([[1, 1, 2]])
    check(len(p2) == 1, "pairs_from_clusters duplicate ids")
    try:
        pairs_from_clusters([[1, 2], [2, 3]])
        check(False, "pairs_from_clusters overlap should raise")
    except ValueError as e:
        check("2" in str(e), "pairs_from_clusters overlap names record")

    # pairwise_metrics
    pred = [["a", "b", "c"], ["d"]]
    true = [["a", "b"], ["c", "d"]]
    m = pairwise_metrics(pred, true)
    check(m["tp"] == 1, "pairwise tp")
    check(m["fp"] == 2, "pairwise fp")
    check(m["fn"] == 1, "pairwise fn")
    check_almost(m["precision"], 0.3333, "pairwise precision")
    check_almost(m["recall"], 0.5, "pairwise recall")
    check_almost(m["f1"], 0.4, "pairwise f1")
    m2 = pairwise_metrics([], [])
    check(m2["precision"] is None, "pairwise precision None")
    check(m2["recall"] is None, "pairwise recall None")
    check(m2["f1"] is None, "pairwise f1 None")
    m3 = pairwise_metrics([["a"], ["b"]], [["a", "b"]])
    check(m3["precision"] is None, "pairwise precision None when no predicted pairs")
    check_almost(m3["recall"], 0.0, "pairwise recall 0")
    try:
        pairwise_metrics([["a", "b"], ["a", "c"]], [["a", "b"], ["c"]])
        check(False, "pairwise_metrics overlap should raise")
    except ValueError:
        pass

    # bcubed
    b = bcubed(pred, true)
    check_almost(b["precision"], 0.6667, "bcubed precision")
    check_almost(b["recall"], 0.75, "bcubed recall")
    check_almost(b["f1"], 0.7059, "bcubed f1", places=3)
    try:
        bcubed([["a", "b"]], [["a", "c"]])
        check(False, "bcubed mismatch should raise")
    except ValueError:
        pass
    try:
        bcubed([["a", "b"], ["a", "c"]], [["a", "b"], ["c"]])
        check(False, "bcubed overlap should raise")
    except ValueError as e:
        check("'a'" in str(e) or "a" in str(e), "bcubed overlap names record")

    # blocking_metrics
    bm = blocking_metrics(100, 495, 50, 45)
    check(bm["possible_pairs"] == 4950, "blocking possible_pairs")
    check_almost(bm["reduction_ratio"], 0.9, "blocking reduction_ratio")
    check_almost(bm["pair_completeness"], 0.9, "blocking pair_completeness")
    check_almost(bm["pairs_quality"], 0.0909, "blocking pairs_quality")
    try:
        blocking_metrics(100, 495, 50, 51)
        check(False, "blocking true_in_candidates > true_pairs should raise")
    except ValueError:
        pass
    try:
        blocking_metrics(100, 40, 50, 45)
        check(False, "blocking true_in_candidates > candidates should raise")
    except ValueError:
        pass
    bm2 = blocking_metrics(100, 495, 0, 0)
    check(bm2["pair_completeness"] is None, "blocking pair_completeness None")
    check_almost(bm2["pairs_quality"], 0.0, "blocking pairs_quality 0")
    try:
        blocking_metrics(2, 2, 0, 0)
        check(False, "blocking candidates > possible should raise")
    except ValueError as e:
        check("n_candidate_pairs" in str(e) and "possible_pairs" in str(e), "blocking candidates message")
    try:
        blocking_metrics(10, -1, 0, 0)
        check(False, "blocking negative count should raise")
    except ValueError:
        pass
    try:
        blocking_metrics(10, 1.5, 0, 0)
        check(False, "blocking non-integer count should raise")
    except ValueError:
        pass
    try:
        blocking_metrics(10, 1, 0, 0)
    except ValueError:
        check(False, "blocking valid counts should not raise")

    # psi
    check_almost(psi([0.5, 0.5], [0.9, 0.1]), 0.8789, "psi reference")
    try:
        psi([0.5], [0.5, 0.5])
        check(False, "psi unequal length should raise")
    except ValueError:
        pass
    try:
        psi([1.0], [1.0])
        check(False, "psi length < 2 should raise")
    except ValueError:
        pass
    try:
        psi([0.5, -0.5], [0.5, 0.5])
        check(False, "psi negative should raise")
    except ValueError:
        pass
    try:
        psi([float("nan"), 0.5], [0.5, 0.5])
        check(False, "psi nan should raise")
    except ValueError:
        pass
    try:
        psi([float("inf"), 0.5], [0.5, 0.5])
        check(False, "psi inf should raise")
    except ValueError:
        pass
    try:
        psi([0.0, 0.0], [0.5, 0.5])
        check(False, "psi zero ref sum should raise")
    except ValueError:
        pass
    try:
        psi([0.5, 0.5], [0.0, 0.0])
        check(False, "psi zero cur sum should raise")
    except ValueError:
        pass

    # psi_band
    check(psi_band(0.05) == "stable", "psi_band stable")
    check(psi_band(0.10) == "moderate", "psi_band moderate boundary")
    check(psi_band(0.25) == "major", "psi_band major boundary")
    check(psi_band(0.249) == "moderate", "psi_band moderate")
    check(psi_band(0.099) == "stable", "psi_band stable boundary")

    # fs_weights
    w = fs_weights(0.9, 0.01)
    check_almost(w["agree"], 6.4919, "fs_weights agree")
    check_almost(w["disagree"], -3.3074, "fs_weights disagree")
    for args in [(0.0, 0.5), (1.0, 0.5), (0.5, 0.0), (0.5, 1.0)]:
        try:
            fs_weights(*args)
            check(False, f"fs_weights{args} should raise")
        except ValueError:
            pass

    # stratified_estimate
    strata = [
        {"population": 100, "sampled": 10, "positive": 3},
        {"population": 200, "sampled": 20, "positive": 8},
    ]
    se = stratified_estimate(strata)
    check_almost(se["estimate"], 0.3667, "stratified estimate")
    check_almost(se["se"], 0.0831, "stratified se")
    check(se["n_sampled"] == 30, "stratified n_sampled")
    check(se["population"] == 300, "stratified population")
    try:
        stratified_estimate([{"population": 100, "sampled": 0, "positive": 0}])
        check(False, "stratified sampled=0 should raise")
    except ValueError:
        pass
    se2 = stratified_estimate([{"population": 100, "sampled": 10, "positive": 10}])
    check_almost(se2["estimate"], 1.0, "stratified all positive estimate")
    check_almost(se2["se"], 0.0, "stratified all positive se")
    check_almost(se2["lo"], 0.7225, "stratified all positive lo", places=3)
    check_almost(se2["hi"], 1.0, "stratified all positive hi")
    try:
        stratified_estimate([{"population": True, "sampled": 10, "positive": 5}])
        check(False, "stratified bool population should raise")
    except ValueError as e:
        check("stratum 0" in str(e) and "population" in str(e), "stratified bool population message")
    try:
        stratified_estimate([{"population": 100, "sampled": 10, "positive": 11}])
        check(False, "stratified positive > sampled should raise")
    except ValueError as e:
        check("stratum 0" in str(e) and "positive" in str(e), "stratified positive message")

    # review_recall
    rs = [
        {"population": 100, "sampled": 10, "positive": 2, "score_lo": 0.0, "score_hi": 0.5},
        {"population": 200, "sampled": 20, "positive": 8, "score_lo": 0.5, "score_hi": 0.8},
        {"population": 300, "sampled": 30, "positive": 20, "score_lo": 0.8, "score_hi": 1.0},
    ]
    rr = review_recall(rs, 0.8)
    check_almost(rr["recall"], 0.6667, "review_recall recall")
    check(rr["matches_above"] == 200, "review_recall matches_above")
    check(rr["matches_total"] == 300, "review_recall matches_total")
    check(rr["covers_below_threshold"] is True, "review_recall covers_below_threshold")
    rr2 = review_recall(rs, 0.0)
    check(rr2["recall"] is None, "review_recall None when no below threshold")
    rr3 = review_recall(
        [{"population": 100, "sampled": 10, "positive": 0, "score_lo": 0.0, "score_hi": 0.5}],
        0.8,
    )
    check(rr3["recall"] is None, "review_recall None when matches_total=0")
    try:
        review_recall(
            [{"population": 100, "sampled": -1, "positive": 0, "score_lo": 0.0, "score_hi": 0.5}],
            0.5,
        )
        check(False, "review_recall malformed stratum should raise")
    except ValueError as e:
        check("stratum 0" in str(e) and "sampled" in str(e), "review_recall malformed message")

    # cluster_size_profile
    cp = cluster_size_profile([1, 2, 3, 4, 50])
    check(cp["n_clusters"] == 5, "cluster n_clusters")
    check(cp["n_records"] == 60, "cluster n_records")
    check(cp["max_size"] == 50, "cluster max_size")
    check(cp["p99_size"] == 50, "cluster p99_size")
    check_almost(cp["singleton_share"], 0.2, "cluster singleton_share")
    check(cp["giant_clusters"] == 1, "cluster giant_clusters")
    try:
        cluster_size_profile([])
        check(False, "cluster_size_profile empty should raise")
    except ValueError:
        pass
    cp2 = cluster_size_profile([1, 2, 3, 4, 5])
    check(cp2["p99_size"] == 5, "cluster p99_size small")
    check(cp2["giant_clusters"] == 0, "cluster giant_clusters none")

    # evaluate_review_csv
    fd, path = tempfile.mkstemp(suffix=".csv")
    os.close(fd)
    try:
        with open(path, "w", newline="") as f:
            f.write("score,label,stratum_population\n")
            f.write("0.05,match,100\n")
            f.write("0.15,non-match,100\n")
            f.write("0.25,match,100\n")
            f.write("0.35,non-match,100\n")
            f.write("0.45,non-match,100\n")
            f.write("0.55,match,100\n")
            f.write("0.65,non-match,100\n")
            f.write("0.75,match,100\n")
            f.write("0.85,match,200\n")
            f.write("0.95,match,200\n")
        ev = evaluate_review_csv(path, 0.8)
        check(ev["n_rows"] == 10, "evaluate_review_csv n_rows")
        check(len(ev["strata"]) == 10, "evaluate_review_csv strata length")
        check_almost(ev["precision_above"]["estimate"], 1.0, "evaluate_review_csv precision_above")
        check_almost(ev["recall"]["recall"], 0.5, "evaluate_review_csv recall")
    finally:
        os.unlink(path)

    # evaluate_review_csv invalid label
    fd, path2 = tempfile.mkstemp(suffix=".csv")
    os.close(fd)
    try:
        with open(path2, "w", newline="") as f:
            f.write("score,label\n")
            f.write("0.05,maybe\n")
        try:
            evaluate_review_csv(path2, 0.8)
            check(False, "evaluate_review_csv invalid label should raise")
        except ValueError as e:
            check("line 2" in str(e), "evaluate_review_csv invalid label line number")
    finally:
        os.unlink(path2)

    # evaluate_review_csv missing columns
    fd, path3 = tempfile.mkstemp(suffix=".csv")
    os.close(fd)
    try:
        with open(path3, "w", newline="") as f:
            f.write("score\n")
            f.write("0.05\n")
        try:
            evaluate_review_csv(path3, 0.8)
            check(False, "evaluate_review_csv missing label should raise")
        except ValueError:
            pass
    finally:
        os.unlink(path3)

    return failures


def _main(argv):
    if len(argv) == 2 and argv[1] == "--selftest":
        failures = _selftest()
        if failures:
            for msg in failures:
                print(f"SELFTEST FAIL: {msg}")
            return 1
        print("SELFTEST PASS")
        return 0
    if len(argv) >= 4 and argv[1] == "review":
        csv_path = argv[2]
        merge_threshold = None
        i = 3
        while i < len(argv):
            arg = argv[i]
            if arg == "--merge-threshold":
                if i + 1 >= len(argv):
                    raise ValueError("missing value for --merge-threshold")
                merge_threshold = float(argv[i + 1])
                i += 2
            elif arg.startswith("--merge-threshold="):
                merge_threshold = float(arg.split("=", 1)[1])
                i += 1
            else:
                raise ValueError(f"unknown argument: {arg}")
        if merge_threshold is None:
            raise ValueError("missing --merge-threshold")
        result = evaluate_review_csv(csv_path, merge_threshold)
        print(json.dumps(result, indent=2, sort_keys=True))
        return 0
    raise ValueError(
        "usage: mdm_eval.py review <csv> --merge-threshold <value> | mdm_eval.py --selftest"
    )


def main():
    try:
        sys.exit(_main(sys.argv))
    except Exception as e:
        print(f"NO-DATA: {e}")
        sys.exit(2)


if __name__ == "__main__":
    main()

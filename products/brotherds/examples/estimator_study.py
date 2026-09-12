"""Why a claim-checking tool must validate its own estimators by simulation.

A master data management claim is only as trustworthy as the statistic that
supports it, and finite samples can push precision or recall far from truth.
Stratified review and blocking loss interact, so closed-form error rates are
hard to derive and easy to misstate. Simulation with known ground truth
measures coverage, bias, and interval width before real unlabeled data is used.
"""

import os
import random
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
_PARENT = os.path.dirname(_HERE)
if _PARENT not in sys.path:
    sys.path.insert(0, _PARENT)

import mdm_eval


DEFAULT_REPS = 200
N_PAIRS = 20000
P_MATCH = 0.08
MERGE_THRESHOLD = 0.8
SAMPLE_PER_BAND = 60
BLOCKING_LOSS = 0.10


def parse_args(argv):
    reps = DEFAULT_REPS
    i = 1
    while i < len(argv):
        arg = argv[i]
        if arg == "--reps":
            if i + 1 >= len(argv):
                raise ValueError("missing value for --reps")
            try:
                reps = int(argv[i + 1])
            except ValueError:
                raise ValueError("invalid value for --reps: " + argv[i + 1])
            i += 2
        elif arg.startswith("--reps="):
            value = arg.split("=", 1)[1]
            try:
                reps = int(value)
            except ValueError:
                raise ValueError("invalid value for --reps: " + value)
            i += 1
        else:
            raise ValueError("unknown argument: " + arg)
    if reps < 1:
        raise ValueError("reps must be at least 1")
    return reps


def run_replication(rng):
    band_population = [0] * 10
    band_positive = [0] * 10
    band_flags = [[] for _ in range(10)]

    for _ in range(N_PAIRS):
        if rng.random() < P_MATCH:
            score = rng.betavariate(8.0, 2.0)
            flag = 1
        else:
            score = rng.betavariate(2.0, 8.0)
            flag = 0
        band = min(9, int(score * 10.0))
        band_population[band] += 1
        band_positive[band] += flag
        band_flags[band].append(flag)

    true_matches_in_candidates = sum(band_positive)
    blocked_out = 0
    for _ in range(true_matches_in_candidates):
        if rng.random() < BLOCKING_LOSS:
            blocked_out += 1

    merged_total = band_population[8] + band_population[9]
    merged_true = band_positive[8] + band_positive[9]
    true_precision = merged_true / merged_total if merged_total > 0 else 0.0
    total_true = true_matches_in_candidates + blocked_out
    true_recall = merged_true / total_true if total_true > 0 else 0.0
    pair_completeness = (
        true_matches_in_candidates / total_true if total_true > 0 else 0.0
    )

    strata = []
    for band in range(10):
        n_h_pop = band_population[band]
        if n_h_pop == 0:
            continue
        n_h = min(SAMPLE_PER_BAND, n_h_pop)
        if n_h == n_h_pop:
            sample_flags = band_flags[band]
        else:
            chosen = rng.sample(range(n_h_pop), n_h)
            sample_flags = [band_flags[band][idx] for idx in chosen]
        positive = sum(sample_flags)
        score_lo = band / 10.0
        score_hi = (band + 1) / 10.0 if band < 9 else 1.0
        strata.append({
            "population": n_h_pop,
            "sampled": n_h,
            "positive": positive,
            "score_lo": score_lo,
            "score_hi": score_hi,
        })

    above = [s for s in strata if s["score_lo"] >= MERGE_THRESHOLD]
    precision_result = mdm_eval.stratified_estimate(above)
    precision_estimate = precision_result["estimate"]
    precision_lo = precision_result["lo"]
    precision_hi = precision_result["hi"]

    recall_result = mdm_eval.review_recall(strata, MERGE_THRESHOLD)
    review_recall_value = recall_result["recall"]
    if review_recall_value is None:
        review_recall_value = 0.0
    end_to_end_recall = review_recall_value * pair_completeness

    return {
        "covered": precision_lo <= true_precision <= precision_hi,
        "width": precision_hi - precision_lo,
        "precision_error": precision_estimate - true_precision,
        "recall_error": end_to_end_recall - true_recall,
        "review_only_error": review_recall_value - true_recall,
    }


def main(argv=None):
    if argv is None:
        argv = sys.argv
    try:
        reps = parse_args(argv)
    except ValueError as exc:
        print("NO-DATA: " + str(exc))
        return 2

    covered_count = 0
    width_sum = 0.0
    precision_error_sum = 0.0
    precision_abs_sum = 0.0
    recall_error_sum = 0.0
    recall_abs_sum = 0.0
    review_only_sum = 0.0

    for r in range(reps):
        seed = 20260912 + r
        rng = random.Random(seed)
        result = run_replication(rng)
        if result["covered"]:
            covered_count += 1
        width_sum += result["width"]
        precision_error_sum += result["precision_error"]
        precision_abs_sum += abs(result["precision_error"])
        recall_error_sum += result["recall_error"]
        recall_abs_sum += abs(result["recall_error"])
        review_only_sum += result["review_only_error"]

    coverage = covered_count / reps
    mean_width = width_sum / reps
    mean_precision_error = precision_error_sum / reps
    mean_precision_abs = precision_abs_sum / reps
    mean_recall_error = recall_error_sum / reps
    mean_recall_abs = recall_abs_sum / reps
    mean_review_only = review_only_sum / reps

    if coverage < 0.90:
        verdict = "intervals under-cover"
    elif coverage > 0.99:
        verdict = "intervals over-cover"
    else:
        verdict = "intervals calibrated"

    print(f"replications {reps}")
    print(f"precision interval coverage {coverage:.4f} (nominal 0.95)")
    print(f"precision interval mean width {mean_width:.4f}")
    print(
        f"precision mean error {mean_precision_error:.4f} , "
        f"mean absolute error {mean_precision_abs:.4f}"
    )
    print(
        f"end-to-end recall mean error {mean_recall_error:.4f} , "
        f"mean absolute error {mean_recall_abs:.4f}"
    )
    print(f"review-only recall mean error {mean_review_only:.4f}")
    print(f"verdict {verdict}")
    return 0


if __name__ == "__main__":
    sys.exit(main())

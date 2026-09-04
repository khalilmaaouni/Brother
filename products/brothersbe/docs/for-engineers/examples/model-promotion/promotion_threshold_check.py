#!/usr/bin/env python3
"""B1's runnable check: does the challenger beat the baseline on the
holdout by the margin the ADR (03-adr.md) fixed at 0.05 AUC.

Reads holdout.csv (label, challenger_score, baseline_score), computes AUC
for each score column by the same rank-sum formula compute_auc_rank.py
uses, and exits 0 only when challenger_auc - baseline_auc >= 0.05.

Usage: python3 promotion_threshold_check.py holdout.csv [--margin 0.05]
Exit 0  PASS  challenger clears the margin over the baseline
Exit 1  FAIL  challenger does not clear the margin
Exit 2  NO-DATA  the file could not be read or is missing a needed column
"""
import csv
import sys


def auc_rank_sum(rows, score_key):
    scored = sorted(rows, key=lambda r: r[score_key])
    n = len(scored)
    ranks = [0.0] * n
    i = 0
    while i < n:
        j = i
        while j + 1 < n and scored[j + 1][score_key] == scored[i][score_key]:
            j += 1
        avg_rank = (i + 1 + j + 1) / 2.0
        for k in range(i, j + 1):
            ranks[k] = avg_rank
        i = j + 1
    r_pos = sum(rank for rank, row in zip(ranks, scored) if row["label"] == 1)
    n_pos = sum(1 for row in scored if row["label"] == 1)
    n_neg = n - n_pos
    if n_pos == 0 or n_neg == 0:
        raise SystemExit("promotion-threshold: NO-DATA: holdout has no rows of one class")
    return (r_pos - n_pos * (n_pos + 1) / 2.0) / (n_pos * n_neg)


def main(argv):
    args = [a for a in argv[1:] if not a.startswith("--margin")]
    margin = 0.05
    for a in argv[1:]:
        if a.startswith("--margin="):
            margin = float(a.split("=", 1)[1])
    if len(args) != 1:
        print("usage: promotion_threshold_check.py holdout.csv [--margin=0.05]", file=sys.stderr)
        return 2
    try:
        with open(args[0], newline="") as f:
            rows = [{"label": int(r["label"]),
                      "challenger_score": float(r["challenger_score"]),
                      "baseline_score": float(r["baseline_score"])}
                     for r in csv.DictReader(f)]
    except (OSError, KeyError, ValueError) as exc:
        print("promotion-threshold: NO-DATA: %s" % exc)
        return 2
    challenger_auc = auc_rank_sum(rows, "challenger_score")
    baseline_auc = auc_rank_sum(rows, "baseline_score")
    lift = challenger_auc - baseline_auc
    if lift >= margin:
        print("promotion-threshold: PASSED: challenger AUC %.6f beats baseline AUC %.6f "
              "by %.6f (margin %.2f)" % (challenger_auc, baseline_auc, lift, margin))
        return 0
    print("promotion-threshold: FAILED: challenger AUC %.6f does not beat baseline AUC %.6f "
          "by the required margin %.2f (lift %.6f)" % (challenger_auc, baseline_auc, margin, lift))
    return 1


if __name__ == "__main__":
    sys.exit(main(sys.argv))

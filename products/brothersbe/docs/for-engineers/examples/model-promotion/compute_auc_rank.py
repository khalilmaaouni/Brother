#!/usr/bin/env python3
"""Primary derivation of the promotion metric: holdout AUC by the
Mann-Whitney rank-sum formula.

Reads holdout.csv (label, challenger_score columns), ranks every row's
challenger_score across the whole holdout (rank 1 = lowest score, ties
would share the average rank; this fixture carries no tied score), and
computes AUC = (sum of positive-class ranks - n_pos*(n_pos+1)/2) /
(n_pos * n_neg). This is the numbers-manifest "query" derivation for the
figure model_promotion_challenger_holdout_auc.

Usage: python3 compute_auc_rank.py holdout.csv
Prints the AUC to 6 decimal places on stdout and exits 0.
"""
import csv
import sys


def auc_rank_sum(rows):
    scored = sorted(rows, key=lambda r: r["score"])
    n = len(scored)
    ranks = [0.0] * n
    i = 0
    while i < n:
        j = i
        while j + 1 < n and scored[j + 1]["score"] == scored[i]["score"]:
            j += 1
        avg_rank = (i + 1 + j + 1) / 2.0
        for k in range(i, j + 1):
            ranks[k] = avg_rank
        i = j + 1
    r_pos = sum(rank for rank, row in zip(ranks, scored) if row["label"] == 1)
    n_pos = sum(1 for row in scored if row["label"] == 1)
    n_neg = n - n_pos
    if n_pos == 0 or n_neg == 0:
        raise SystemExit("compute_auc_rank: NO-DATA: holdout has no rows of one class")
    return (r_pos - n_pos * (n_pos + 1) / 2.0) / (n_pos * n_neg)


def main(argv):
    if len(argv) != 2:
        print("usage: compute_auc_rank.py holdout.csv", file=sys.stderr)
        return 2
    with open(argv[1], newline="") as f:
        rows = [{"label": int(r["label"]), "score": float(r["challenger_score"])}
                for r in csv.DictReader(f)]
    print("%.6f" % auc_rank_sum(rows))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))

#!/usr/bin/env python3
"""Independent second derivation of the promotion metric: holdout AUC by
trapezoidal integration of the ROC curve.

Reads holdout.csv (label, challenger_score columns), sweeps every distinct
challenger_score as a decision threshold from high to low, plots
(false-positive-rate, true-positive-rate) at each threshold, and integrates
the resulting staircase with the trapezoid rule. This is a different
computation from compute_auc_rank.py's rank-sum formula (no ranking, no
sum-of-ranks arithmetic); on data with no tied score the two are the same
statistic and must land on the same number, which is the numbers-manifest
"second_derivation" this dossier's figure is checked against.

Usage: python3 compute_auc_trapezoid.py holdout.csv
Prints the AUC to 6 decimal places on stdout and exits 0.
"""
import csv
import sys


def auc_trapezoid(rows):
    n_pos = sum(1 for r in rows if r["label"] == 1)
    n_neg = len(rows) - n_pos
    if n_pos == 0 or n_neg == 0:
        raise SystemExit("compute_auc_trapezoid: NO-DATA: holdout has no rows of one class")
    thresholds = sorted({r["score"] for r in rows}, reverse=True)
    thresholds = [thresholds[0] + 1.0] + thresholds + [thresholds[-1] - 1.0]
    points = []
    for t in thresholds:
        tp = sum(1 for r in rows if r["label"] == 1 and r["score"] >= t)
        fp = sum(1 for r in rows if r["label"] == 0 and r["score"] >= t)
        points.append((fp / n_neg, tp / n_pos))
    area = 0.0
    for (fpr0, tpr0), (fpr1, tpr1) in zip(points, points[1:]):
        area += (fpr1 - fpr0) * (tpr0 + tpr1) / 2.0
    return area


def main(argv):
    if len(argv) != 2:
        print("usage: compute_auc_trapezoid.py holdout.csv", file=sys.stderr)
        return 2
    with open(argv[1], newline="") as f:
        rows = [{"label": int(r["label"]), "score": float(r["challenger_score"])}
                for r in csv.DictReader(f)]
    print("%.6f" % auc_trapezoid(rows))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))

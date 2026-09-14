#!/usr/bin/env python3
"""Thin harness for the Acceptance Time benchmark (roadmap row S11), living
at the path the roadmap names: benchmarks/acceptance-time/. See README.md
in this directory for the results-file format and what each field means.

This is NOT a second implementation. Packet preparation, the Latin-square
rotation, and the honest-floor / missing-arm scoring all already live in
scripts/acceptance_time.py and scripts/acceptance_trial_assign.py (both
tested by scripts/test_acceptance_time.py and
scripts/test_acceptance_trial_assign.py) and are only imported here. This
file adds exactly one thing neither of those already does: printing the two
narrative measures (defects found, unnecessary lines inspected) alongside
the two mechanical ones (time, correctness) when -- and only when -- a real
three-arm comparison is being reported, because benchmarks/ACCEPTANCE-TIME.md
is explicit that no instrument on this estate scores prose, so those two
columns are read out, never computed.

Two verbs:

  prepare <out dir> <n reviewers> [--seed N]
      Writes the nine condition packets (scripts/acceptance_time.py
      prepare) plus a Latin-square reviewer assignment and blank results
      template CSV (scripts/acceptance_trial_assign.py assign) into one
      directory, in one call.

  score <results csv>
      Delegates the decision of whether this is a reportable comparison to
      scripts/acceptance_time.py's own score() (median seconds, correctness
      rate per condition, NO-DATA and a non-zero exit code below the
      five-reviewer floor or with any arm missing). Only on a full,
      reportable comparison (exit 0) does it also read out each condition's
      narrative entries.

Nothing here times a human, invents a decision, or fabricates a comparison.
"""
import argparse
import csv
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
REPO_ROOT = os.path.dirname(os.path.dirname(HERE))
SCRIPTS_DIR = os.path.join(REPO_ROOT, "scripts")
sys.path.insert(0, SCRIPTS_DIR)
import acceptance_time as AT  # noqa: E402
import acceptance_trial_assign as ATA  # noqa: E402

NODATA = "NO-DATA"


def prepare(out_dir, n_reviewers, seed=0):
    """Write the nine packets plus a counterbalanced assignment and blank
    results template into out_dir. Returns the list of paths written."""
    written = list(AT.prepare(out_dir))
    if n_reviewers < AT.MIN_REVIEWERS:
        print("%s: %d reviewer(s) requested, fewer than the %d the honest "
              "floor requires. No assignment table or results template "
              "written." % (NODATA, n_reviewers, AT.MIN_REVIEWERS))
        return written
    rows = ATA.assignment_table(n_reviewers, seed=seed)
    csv_path = os.path.join(out_dir, "results.csv")
    ATA.write_template_csv(csv_path, rows)
    written.append(csv_path)
    return written


def _narrative_by_condition(csv_path):
    """Read the two narrative columns per condition, if the CSV carries
    them. Returns {} if the columns are absent (an older or minimal CSV);
    that is not an error, since narrative columns are optional per
    scripts/acceptance_trial_assign.py's own REQUIRED_COLUMNS."""
    with open(csv_path, newline="", encoding="utf-8") as fh:
        reader = csv.DictReader(fh)
        fieldnames = reader.fieldnames or []
        if not set(ATA.NARRATIVE_COLUMNS).issubset(set(fieldnames)):
            return {}
        by_condition = dict((c, []) for c in AT.CONDITIONS)
        for row in reader:
            condition = row.get("condition")
            if condition not in by_condition:
                continue
            defects = (row.get("defects_found") or "").strip()
            lines = (row.get("lines_inspected") or "").strip()
            if defects or lines:
                by_condition[condition].append(
                    "%s: defects_found=%r lines_inspected=%r"
                    % (row.get("reviewer", "?"), defects, lines))
        return by_condition


def score(csv_path):
    """Print the mechanical per-condition scores via
    scripts/acceptance_time.py score(), then -- only when that call reports
    a full three-arm comparison (exit code 0) -- also print each
    condition's narrative entries (defects found, unnecessary lines
    inspected) read straight from the results CSV, never aggregated,
    because no instrument here scores prose."""
    exit_code = AT.score(csv_path)
    if exit_code != 0:
        return exit_code
    by_condition = _narrative_by_condition(csv_path)
    if not by_condition:
        print("(no narrative columns -- defects_found / lines_inspected -- "
              "present in this CSV; nothing to read out)")
        return exit_code
    for condition in AT.CONDITIONS:
        entries = by_condition[condition]
        print("%s narrative (defects found / unnecessary lines inspected, "
              "read by hand, never scored):" % condition)
        if not entries:
            print("  (no narrative recorded)")
        for entry in entries:
            print("  " + entry)
    return exit_code


def main(argv=None):
    argv = sys.argv[1:] if argv is None else argv
    parser = argparse.ArgumentParser(
        description="Acceptance Time benchmark harness "
                    "(benchmarks/acceptance-time)")
    sub = parser.add_subparsers(dest="verb", required=True)

    prepare_parser = sub.add_parser(
        "prepare",
        help="write the nine packets plus a Latin-square assignment and "
             "blank results template")
    prepare_parser.add_argument("out_dir")
    prepare_parser.add_argument("n_reviewers", type=int)
    prepare_parser.add_argument("--seed", type=int, default=0)

    score_parser = sub.add_parser(
        "score", help="score a human trial's results CSV")
    score_parser.add_argument("csv_path")

    args = parser.parse_args(argv)
    if args.verb == "prepare":
        for path in prepare(args.out_dir, args.n_reviewers, seed=args.seed):
            print(path)
        return 0
    if args.verb == "score":
        return score(args.csv_path)
    return 2  # pragma: no cover, argparse already refuses an unknown verb


if __name__ == "__main__":
    sys.exit(main())

#!/usr/bin/env python3
"""Acceptance Compression trial: reviewer assignment and results validation
(E3, founder steering 2026-09-05 sections 19 to 22). Protocol
benchmarks/ACCEPTANCE-TIME.md; frozen spec
benchmarks/gauntlets/acceptance-compression.json; frozen rule
benchmarks/gauntlets/acceptance-compression/SUCCESS-RULE-FROZEN.md.

Two verbs:

  assign <n reviewers> [--seed N] [--out-csv PATH]
      Prints a counterbalanced assignment table: each reviewer sees each of
      the three fixed changes (scripts/acceptance_time.py CHANGES) exactly
      once, under exactly one of the three conditions, conditions balanced
      across reviewers by rotation. Deterministic for a given (n, seed), so
      two runs with the same arguments print the same table. When
      --out-csv is given, also writes the results template CSV a reviewer
      fills in: the columns scripts/acceptance_time.py score() already
      expects (reviewer, change, condition, seconds, decision), with
      seconds and decision left blank.

  validate <results csv>
      Refuses a completed results CSV that is missing a time, carries an
      impossible time, or has one reviewer seeing the same change twice.
      Prints every problem found and exits 1 if any exist, 0 otherwise.
      Never invents or fills a missing value; it only reports what is
      wrong so a human can fix the CSV before scripts/acceptance_time.py
      score reads it.

This script assigns reviewers and shapes the CSV. It never times a human,
never records a decision, and never scores a result: that stays
scripts/acceptance_time.py's job, unchanged.
"""
import argparse
import csv
import os
import random
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import acceptance_time as AT  # noqa: E402

NODATA = "NO-DATA"
CSV_COLUMNS = ["reviewer", "change", "condition", "seconds", "decision"]
VALID_DECISIONS = set(["accept", "reject", "ask"])
#: No single-change review of a diff, a summary, or a receipt plausibly
#: takes longer than this. A recorded time past it means the reviewer's
#: clock was left running, not that the review took four hours.
MAX_PLAUSIBLE_SECONDS = 4 * 3600


def assignment_table(n_reviewers, seed=0):
    """Return a list of (reviewer_id, change_id, condition) rows: each of
    n_reviewers reviewers paired with each of the fixed changes exactly
    once, condition chosen by rotation (CONDITIONS[(slot + k) % 3] for the
    reviewer's rotation slot and the change's position k), so across all
    reviewers each change is read under every condition as evenly as
    n_reviewers divides by three allows. seed only decides which named
    reviewer gets which rotation slot; the balance itself is not random.
    """
    reviewer_ids = ["reviewer-%d" % (i + 1) for i in range(n_reviewers)]
    slots = list(range(n_reviewers))
    random.Random(seed).shuffle(slots)
    rows = []
    for reviewer_id, slot in zip(reviewer_ids, slots):
        for k, change in enumerate(AT.CHANGES):
            condition = AT.CONDITIONS[(slot + k) % 3]
            rows.append((reviewer_id, change["id"], condition))
    return rows


def format_table(rows):
    header = "%-14s %-16s %s" % ("reviewer", "change", "condition")
    lines = [header, "-" * len(header)]
    for reviewer_id, change_id, condition in rows:
        lines.append("%-14s %-16s %s" % (reviewer_id, change_id, condition))
    return "\n".join(lines)


def write_template_csv(path, rows):
    with open(path, "w", newline="", encoding="utf-8") as fh:
        writer = csv.writer(fh)
        writer.writerow(CSV_COLUMNS)
        for reviewer_id, change_id, condition in rows:
            writer.writerow([reviewer_id, change_id, condition, "", ""])


def validate(csv_path):
    """Print every problem in a completed results CSV and return the exit
    code: 0 clean, 1 if any row is missing a time, carries an impossible
    time, has an unrecognized decision, or a reviewer appears against the
    same change more than once. 2 for a file that cannot even be read."""
    if not os.path.isfile(csv_path):
        print("%s: %s does not exist" % (NODATA, csv_path))
        return 2
    try:
        with open(csv_path, newline="", encoding="utf-8") as fh:
            reader = csv.DictReader(fh)
            missing_cols = set(CSV_COLUMNS) - set(reader.fieldnames or [])
            if missing_cols:
                print("%s: %s is missing column(s) %s" % (
                    NODATA, csv_path, ", ".join(sorted(missing_cols))))
                return 2
            rows = list(reader)
    except (OSError, csv.Error) as exc:
        print("%s: could not read %s: %r" % (NODATA, csv_path, exc))
        return 2

    problems = []
    seen_pairs = {}
    for n, row in enumerate(rows, start=2):  # header is line 1
        reviewer = (row.get("reviewer") or "").strip()
        change = (row.get("change") or "").strip()
        seconds_raw = (row.get("seconds") or "").strip()
        decision = (row.get("decision") or "").strip().lower()

        if not reviewer or not change:
            problems.append("line %d: missing reviewer or change" % n)
            continue

        pair = (reviewer, change)
        if pair in seen_pairs:
            problems.append(
                "line %d: reviewer %r already saw change %r at line %d"
                % (n, reviewer, change, seen_pairs[pair]))
        else:
            seen_pairs[pair] = n

        if not seconds_raw:
            problems.append(
                "line %d: %s / %s has no recorded time" % (n, reviewer,
                                                            change))
        else:
            try:
                seconds = float(seconds_raw)
            except ValueError:
                problems.append(
                    "line %d: %s / %s has a non numeric time %r"
                    % (n, reviewer, change, seconds_raw))
            else:
                if seconds <= 0:
                    problems.append(
                        "line %d: %s / %s has an impossible time %s "
                        "seconds (must be greater than zero)"
                        % (n, reviewer, change, seconds_raw))
                elif seconds > MAX_PLAUSIBLE_SECONDS:
                    problems.append(
                        "line %d: %s / %s has an impossible time %s "
                        "seconds (over the %d second plausibility ceiling)"
                        % (n, reviewer, change, seconds_raw,
                           MAX_PLAUSIBLE_SECONDS))

        if decision and decision not in VALID_DECISIONS:
            problems.append(
                "line %d: %s / %s has an unrecognized decision %r "
                "(expected one of %s)"
                % (n, reviewer, change, row.get("decision"),
                   ", ".join(sorted(VALID_DECISIONS))))

    if problems:
        print("%s: %d problem(s) in %s" % (NODATA, len(problems), csv_path))
        for problem in problems:
            print("  " + problem)
        return 1
    print("clean: %d row(s) in %s, no missing time, no impossible time, "
          "no reviewer seeing a change twice" % (len(rows), csv_path))
    return 0


def main(argv=None):
    argv = sys.argv[1:] if argv is None else argv
    parser = argparse.ArgumentParser(
        description="Acceptance Compression trial: assignment and "
                    "results validation (E3)")
    sub = parser.add_subparsers(dest="verb", required=True)

    assign_parser = sub.add_parser(
        "assign", help="print a counterbalanced reviewer assignment table")
    assign_parser.add_argument("n_reviewers", type=int, nargs="?", default=5)
    assign_parser.add_argument("--seed", type=int, default=0)
    assign_parser.add_argument("--out-csv", default=None,
                               help="also write a blank results template "
                                   "CSV to this path")

    validate_parser = sub.add_parser(
        "validate", help="check a completed results CSV")
    validate_parser.add_argument("csv_path")

    args = parser.parse_args(argv)

    if args.verb == "assign":
        if args.n_reviewers < AT.MIN_REVIEWERS:
            print("%s: %d reviewer(s) requested, fewer than the %d the "
                  "honest floor requires. Assignment refused." % (
                      NODATA, args.n_reviewers, AT.MIN_REVIEWERS))
            return 3
        rows = assignment_table(args.n_reviewers, seed=args.seed)
        print(format_table(rows))
        if args.out_csv:
            write_template_csv(args.out_csv, rows)
            print("\nwrote results template: %s" % args.out_csv)
        return 0
    if args.verb == "validate":
        return validate(args.csv_path)
    return 2  # pragma: no cover, argparse already refuses an unknown verb


if __name__ == "__main__":
    sys.exit(main())

#!/usr/bin/env python3
"""Does a train/test split leak: the same entity in both sides, or a row
dated after the cutoff sitting in the training set.

THE DEFECT THIS EXISTS FOR (P8, docs/plan/READINESS-ROADMAP-2026-08-29.json).
E3, DS-02, DS-03 and the golden fixtures DS-G01/DS-G02 all name the same
gap: nothing in this tree checks a split before a model trains on it. A
leaked entity (the same customer in train and test) inflates every metric
downstream, and a feature dated after the declared cutoff means the model
saw the future during training. Both classes are invisible until someone
notices the numbers look too good, which is exactly the failure case doc
19.4 names. This script makes both classes a red line in a receipt instead.

TWO CHECKS, run in this order:
  1. OVERLAP: any --key value present in both --train and --test. Read left
     to right; the report names the FIRST such key, at its --test line
     number (duplicates of a key WITHIN one file, e.g. two rows in train
     sharing a key, are not an overlap on their own and are never reported
     here; only a key crossing the train/test boundary is).
  2. CUTOFF (only when --time-col and --cutoff are both given): any --train
     row whose --time-col value sorts after --cutoff. Train is the side
     checked, not test, because the leak this class describes is a model
     trained on data from after the point it is meant to have been cut off
     at; a holdout dated after the cutoff is the ordinary, intended shape of
     a temporal split, not a defect. Cutoff comparison is a plain string
     compare, so it is exact for ISO 8601 dates and timestamps
     (YYYY-MM-DD[THH:MM:SS]) and NOT a general date parser; a --cutoff or
     --time-col value in another format will compare lexically, which is a
     known ceiling, not a bug. (ponytail: string-compare cutoff, swap for
     datetime parsing if a non-ISO time format shows up)

Exit contract, mirroring scripts/leaf_pin_check.py and this estate's other
gates:
  0  PASS      no overlapping key, no train row past the cutoff
  1  FAIL      an overlap or a past-cutoff train row was found
  2  NO-DATA   a file, a named column, or a well-formed row could not be read

NO-DATA IS NOT A PASS. A file this script could not open, a column it was
asked to key on that is not in the header, or a CSV row whose field count
disagrees with its header are all "could not look", never "looked and found
nothing wrong".
"""
import argparse
import csv
import sys


class NoData(Exception):
    """Raised for any 'could not look' condition: an unreadable file, an
    absent column, or a malformed row. Never raised for a verdict about the
    data itself; that is a return value, not an exception."""


def read_csv(path):
    """Return (header, rows) for path, rows a list of (line_no, dict).

    line_no counts from 2 (the header is line 1), matching what a person
    reading the file in a text editor would call that row. Raises NoData for
    a file that cannot be opened, one with no header row at all (empty
    file), or a data row whose field count disagrees with the header (a
    malformed CSV row) -- csv.reader does not raise on that by itself, a
    short or long row is silently zipped or truncated, so this checks it by
    hand."""
    try:
        with open(path, newline="") as f:
            reader = csv.reader(f)
            try:
                header = next(reader)
            except StopIteration:
                raise NoData(f"{path} is empty (no header row)")
            width = len(header)
            rows = []
            for line_no, raw in enumerate(reader, start=2):
                if len(raw) != width:
                    raise NoData(
                        f"{path} line {line_no}: malformed row, {len(raw)} "
                        f"field(s) for a {width}-column header")
                rows.append((line_no, dict(zip(header, raw))))
            return header, rows
    except OSError as exc:
        raise NoData(f"could not open {path}: {exc.strerror}") from exc


def require_column(path, header, col):
    if col not in header:
        raise NoData(f"{path}: column '{col}' is absent")


def first_overlap(train_rows, test_rows, key):
    """The first (line_no, key_value) in test_rows whose key also appears
    anywhere in train_rows, or None."""
    train_keys = {row[key] for _, row in train_rows}
    for line_no, row in test_rows:
        value = row[key]
        if value in train_keys:
            return line_no, value
    return None


def first_past_cutoff(train_rows, time_col, cutoff):
    """The first (line_no, value) in train_rows whose time_col sorts after
    cutoff, or None."""
    for line_no, row in train_rows:
        value = row[time_col]
        if value > cutoff:
            return line_no, value
    return None


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--train", required=True, help="train CSV path")
    parser.add_argument("--test", required=True, help="test CSV path")
    parser.add_argument("--key", required=True,
                         help="column name that identifies an entity")
    parser.add_argument("--time-col",
                         help="column name to check against --cutoff")
    parser.add_argument("--cutoff",
                         help="train rows with --time-col after this value FAIL "
                              "(ISO 8601 string compare); requires --time-col")
    args = parser.parse_args(argv)

    if args.cutoff and not args.time_col:
        print("split-check: NO-DATA: --cutoff given without --time-col")
        return 2

    try:
        train_header, train_rows = read_csv(args.train)
        test_header, test_rows = read_csv(args.test)
        require_column(args.train, train_header, args.key)
        require_column(args.test, test_header, args.key)
        if args.time_col:
            require_column(args.train, train_header, args.time_col)
    except NoData as exc:
        print(f"split-check: NO-DATA: {exc}")
        return 2

    overlap = first_overlap(train_rows, test_rows, args.key)
    if overlap is not None:
        line_no, value = overlap
        print(f"split-check: FAIL: key '{value}' appears in both train and "
              f"test ({args.test} line {line_no})")
        return 1

    if args.time_col:
        past = first_past_cutoff(train_rows, args.time_col, args.cutoff)
        if past is not None:
            line_no, value = past
            print(f"split-check: FAIL: {args.train} line {line_no} has "
                  f"{args.time_col}='{value}', after the cutoff {args.cutoff}")
            return 1

    tail = f", no train row past cutoff {args.cutoff}" if args.time_col else ""
    print(f"split-check: PASSED: {len(train_rows)} train row(s), "
          f"{len(test_rows)} test row(s), no overlapping '{args.key}'{tail}")
    return 0


if __name__ == "__main__":
    sys.exit(main())

"""Convert a JSONL file to CSV.

The repaired delivery of the invalid UTF-8 fixture (row S32): the read has
a failure path, so an undecodable input is named and refused rather than
raising out of the tool.
"""
import csv
import json
import sys


def read_records(src):
    records = []
    with open(src) as fh:
        for line in fh:
            line = line.strip()
            if line:
                records.append(json.loads(line))
    return records


def write_csv(records, out):
    if not records:
        return
    fields = list(records[0])
    writer = csv.DictWriter(out, fieldnames=fields)
    writer.writeheader()
    for record in records:
        writer.writerow(record)


def main(argv):
    if len(argv) != 1:
        print("usage: jsonl2csv.py INPUT.jsonl", file=sys.stderr)
        return 2
    try:
        records = read_records(argv[0])
    except UnicodeDecodeError as exc:
        print("jsonl2csv: %s carries invalid UTF-8 at byte %d and cannot be "
              "read" % (argv[0], exc.start), file=sys.stderr)
        return 2
    write_csv(records, sys.stdout)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))

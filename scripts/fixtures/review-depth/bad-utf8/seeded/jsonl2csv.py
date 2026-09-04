"""Convert a JSONL file to CSV.

The seeded delivery of the invalid UTF-8 fixture (row S32). The defect is
the missing failure path on the read: a line carrying a byte that is not
valid UTF-8 raises out of the decode, so the tool dies with a traceback
instead of a named failure and an exit code a caller can act on.
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
    write_csv(read_records(argv[0]), sys.stdout)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))

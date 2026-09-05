"""Convert a JSONL file to CSV.

The repaired delivery of the byte order mark fixture (row S32): one line
different from the seeded tree, naming an encoding that tolerates a byte
order mark.
"""
import csv
import json
import sys


def read_records(src):
    records = []
    with open(src, encoding="utf-8-sig") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            try:
                records.append(json.loads(line))
            except ValueError as exc:
                print("jsonl2csv: dropped an unparsable line: %s" % exc,
                      file=sys.stderr)
                continue
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

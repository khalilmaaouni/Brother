"""Convert a JSONL file to CSV.

The seeded delivery of the byte order mark fixture (row S32). The defect is
the plain decode on the read: a file written by a Windows tool carries a
UTF-8 byte order mark, its first line then fails to parse, and the record is
dropped rather than converted.
"""
import csv
import json
import sys


def read_records(src):
    records = []
    with open(src) as fh:
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

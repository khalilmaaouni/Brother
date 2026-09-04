"""The discriminating check the byte order mark finding carries (row S32).

Exit 0 when every input record reaches the CSV under the header the input
declared, exit 1 when it does not. The input is written here, byte order
mark and all, so no committed fixture file has to carry one.
"""
import os
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
INPUT = os.path.join(HERE, "bom-input.jsonl")
RECORDS = (b'{"a": 1, "b": 2}\n', b'{"a": 3, "b": 4}\n', b'{"a": 5, "b": 6}\n')


def main():
    with open(INPUT, "wb") as fh:
        fh.write(b"\xef\xbb\xbf" + b"".join(RECORDS))
    proc = subprocess.run(
        [sys.executable, os.path.join(HERE, "jsonl2csv.py"), INPUT],
        capture_output=True, text=True)
    if proc.returncode != 0:
        print("FAIL: the converter exited %d: %s"
              % (proc.returncode, (proc.stderr or "").strip()))
        return 1
    lines = [line for line in proc.stdout.splitlines() if line.strip()]
    if not lines:
        print("FAIL: the converter wrote no CSV at all")
        return 1
    first = lines[0].split(",")[0]
    if first != "a":
        print("FAIL: the first CSV header field is %r, not 'a'" % first)
        return 1
    if len(lines) - 1 != len(RECORDS):
        print("FAIL: %d input record(s) became %d CSV row(s)"
              % (len(RECORDS), len(lines) - 1))
        return 1
    print("PASS: %d record(s), first header field %r" % (len(RECORDS), first))
    return 0


if __name__ == "__main__":
    sys.exit(main())

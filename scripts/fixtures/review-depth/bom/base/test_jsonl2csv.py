"""The unit's OWN delivered check: the converter turns a clean JSONL file
into CSV.

It passes on the seeded tree and on the fixed one, which is the whole point
of the fixture. The defect an independent reviewer finds is one this check
never looks at, so a green here proves only what it looked at.
"""
import os
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
CLEAN = os.path.join(HERE, "clean-input.jsonl")


def main():
    with open(CLEAN, "wb") as fh:
        fh.write(b'{"a": 1, "b": 2}\n{"a": 3, "b": 4}\n')
    proc = subprocess.run(
        [sys.executable, os.path.join(HERE, "jsonl2csv.py"), CLEAN],
        capture_output=True, text=True)
    if proc.returncode != 0:
        print("FAIL: the converter exited %d: %s"
              % (proc.returncode, (proc.stderr or "").strip()))
        return 1
    lines = [line for line in proc.stdout.splitlines() if line.strip()]
    if lines[:1] != ["a,b"] or len(lines) != 3:
        print("FAIL: the converter wrote %r" % lines)
        return 1
    print("PASS: the converter wrote %d line(s)" % len(lines))
    return 0


if __name__ == "__main__":
    sys.exit(main())

"""The discriminating check the invalid UTF-8 finding carries (row S32).

Exit 0 when the converter refuses an undecodable input with a named failure
and a nonzero exit a caller can act on, exit 1 when it dies with a traceback
instead. The input is written here, invalid byte and all, so no committed
fixture file has to carry one.
"""
import os
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
INPUT = os.path.join(HERE, "bad-input.jsonl")


def main():
    with open(INPUT, "wb") as fh:
        fh.write(b'{"a": 1, "b": 2}\n{"a": "caf\x80", "b": 4}\n')
    proc = subprocess.run(
        [sys.executable, os.path.join(HERE, "jsonl2csv.py"), INPUT],
        capture_output=True, text=True)
    err = (proc.stderr or "").strip()
    if "Traceback" in err:
        print("FAIL: the converter died with a traceback rather than naming "
              "the failure")
        return 1
    if proc.returncode == 0:
        print("FAIL: the converter exited 0 on an undecodable input")
        return 1
    if "invalid UTF-8" not in err:
        print("FAIL: the converter exited %d and named no failure: %r"
              % (proc.returncode, err))
        return 1
    print("PASS: the converter exited %d and named the failure"
          % proc.returncode)
    return 0


if __name__ == "__main__":
    sys.exit(main())

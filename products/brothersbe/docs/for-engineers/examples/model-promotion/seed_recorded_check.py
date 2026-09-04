#!/usr/bin/env python3
"""B3's runnable check: does the training run that produced the challenger
model record the random seed it trained with.

A seed that is never written down cannot be re-run: DS-04 (docs/plan's
persona use-case library) names an irreproducible result as the failure
this class of check exists for. Reads training-run.json and exits 0 only
when "seed" is present and is an integer (a placeholder string like "TBD"
or a missing key both refuse the run as reproducible).

Usage: python3 seed_recorded_check.py training-run.json
Exit 0  PASS  a seed is recorded
Exit 1  FAIL  the run exists but records no usable seed
Exit 2  NO-DATA  the file could not be read or is not a JSON object
"""
import json
import sys


def main(argv):
    if len(argv) != 2:
        print("usage: seed_recorded_check.py training-run.json", file=sys.stderr)
        return 2
    try:
        with open(argv[1], encoding="utf-8") as f:
            data = json.load(f)
    except (OSError, ValueError) as exc:
        print("seed-recorded: NO-DATA: %s" % exc)
        return 2
    if not isinstance(data, dict):
        print("seed-recorded: NO-DATA: %s is not a JSON object" % argv[1])
        return 2
    seed = data.get("seed")
    if isinstance(seed, bool) or not isinstance(seed, int):
        print("seed-recorded: FAILED: run %r records no usable seed (seed=%r)"
              % (data.get("run_id", "(no run_id)"), seed))
        return 1
    print("seed-recorded: PASSED: run %r recorded seed=%d" % (data.get("run_id", "(no run_id)"), seed))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))

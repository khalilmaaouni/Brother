#!/usr/bin/env python3
"""ds-seed golden fixture (P13, doc 21.6 DS-G03: notebook random seed).

This is a stand-in for a data scientist's notebook-style eval cell: it
draws its metric from an unseeded random source, so the number it prints is
different every time it runs and there is no fixed seed value to record.
It never writes the <run_dir>/evidence/<unit_id>.json file the E18
contract (P6, docs/plan/READINESS-ROADMAP-2026-08-29.json) requires for a
metric to count as reproducible statistical evidence, because it has
nothing reproducible to write. Exits 0 always: the point of this fixture
is that a green exit code is not proof of the number, exactly the gap
brother_run.py's own E18 gap check (scripts/receipt_door.py e18_gap) exists
to catch.

Python 3, standard library only. No em or en dashes.
"""
import random
import sys

def main():
    metric = random.random()
    print("ds-seed eval: accuracy=%.4f (no seed recorded, changes every run)"
          % metric)
    return 0

if __name__ == "__main__":
    sys.exit(main())

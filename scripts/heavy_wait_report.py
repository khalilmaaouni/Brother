#!/usr/bin/env python3
"""ACC7.a: measure the runner flip condition from the heavy_slot wait records.

The owner's condition for buying isolated runners is one figure: the median
heavy_slot wait over 10 minutes (600 s). This file computes that figure from
<slot dir>/waits.jsonl, written one line per admission by `record_wait` in
scripts/heavy_slot.py, and prints a verdict that cannot pass on thin data:

  FLIP     enough samples and the median is strictly over the bar
  HOLD     enough samples and it is not
  NO-DATA  fewer than MIN_SAMPLES usable lines, more than MAX_BAD_SHARE of the
           lines unusable, or the file unreadable

The median is computed from the file every time, never quoted. The report
never writes to the wait file. `record_wait` runs only inside `acquire`, so a
run that bypasses the queue writes no line; such a run waited 0 s, so leaving
it out can only overstate the median (the verdict leans toward FLIP, never
toward HOLD), and the output says so.

Exit codes: 0 for HOLD and FLIP (both are answers), 2 for NO-DATA.
"""
import argparse
import json
import math
import os
import sys
from typing import Dict, List, Tuple

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import heavy_slot  # noqa: E402  (slot_dir: the one place the wait file's directory is decided)

MEDIAN_BAR_S = 600.0
MIN_SAMPLES = 200
MAX_BAD_SHARE = 0.01
OUTCOMES = ("admitted", "unqueued")
VERDICTS = ("FLIP", "HOLD", "NO-DATA")


def default_path(env=None):
    return os.path.join(heavy_slot.slot_dir(os.environ if env is None else env), "waits.jsonl")


def _usable(row):
    if not isinstance(row, dict):
        return False
    waited = row.get("waited_s")
    if isinstance(waited, bool) or not isinstance(waited, (int, float)):
        return False
    if not math.isfinite(waited) or waited < 0:
        return False
    if not isinstance(row.get("at"), str) or row.get("outcome") not in OUTCOMES:
        return False
    for key in ("slots", "of"):
        if isinstance(row.get(key), bool) or not isinstance(row.get(key), int):
            return False
    return True


def read_waits(path):
    # type: (str) -> Tuple[List[Dict[str, object]], int]
    """Parsed usable rows and the count of unusable lines. Raises FileNotFoundError
    for a missing file. A partial last line (the file appended by a live run while
    it is read) is one unusable line, never a crash."""
    rows = []
    bad = 0
    with open(path, encoding="utf-8", errors="replace") as fh:
        for line in fh:
            if not line.strip():
                continue
            try:
                row = json.loads(line)
            except ValueError:
                bad += 1
                continue
            if _usable(row):
                rows.append(row)
            else:
                bad += 1
    return rows, bad


def _median(values):
    n = len(values)
    if n % 2:
        return float(values[n // 2])
    return (values[n // 2 - 1] + values[n // 2]) / 2.0


def wait_stats(rows):
    # type: (List[Dict[str, object]]) -> Dict[str, float]
    """n, median_s, p90_s, over_bar, unqueued, first_at. An unqueued line counts at
    its recorded wait: it waited the whole bound. Only waited_s decides; `at` is
    reported, never used for ordering or filtering."""
    waits = sorted(float(r["waited_s"]) for r in rows)
    n = len(waits)
    if n == 0:
        return {"n": 0, "median_s": 0.0, "p90_s": 0.0, "over_bar": 0, "unqueued": 0, "first_at": ""}
    return {
        "n": n,
        "median_s": _median(waits),
        "p90_s": waits[max(0, int(math.ceil(0.9 * n)) - 1)],
        "over_bar": sum(1 for w in waits if w > MEDIAN_BAR_S),
        "unqueued": sum(1 for r in rows if r["outcome"] == "unqueued"),
        "first_at": min(str(r["at"]) for r in rows),
    }


def flip_verdict(stats, bad_lines, total_lines, readable):
    # type: (Dict[str, float], int, int, bool) -> str
    if not readable:
        return "NO-DATA"
    if total_lines > 0 and float(bad_lines) / total_lines > MAX_BAD_SHARE:
        return "NO-DATA"
    if stats.get("n", 0) < MIN_SAMPLES:
        return "NO-DATA"
    if stats["median_s"] > MEDIAN_BAR_S:
        return "FLIP"
    return "HOLD"


def report(path):
    """(verdict, stats, bad, total, readable, note): everything main prints and
    runner_decision_card reads, from one read of the file."""
    try:
        rows, bad = read_waits(path)
        readable = True
        note = ""
    except FileNotFoundError:
        rows, bad, readable = [], 0, False
        note = "%s: no such file" % path
    stats = wait_stats(rows)
    total = len(rows) + bad
    verdict = flip_verdict(stats, bad, total, readable)
    if verdict == "NO-DATA" and not note:
        if total and float(bad) / total > MAX_BAD_SHARE:
            note = "%d of %d lines unusable, over the %g share bar" % (bad, total, MAX_BAD_SHARE)
        else:
            note = "%d usable samples, %d needed" % (stats["n"], MIN_SAMPLES)
    return verdict, stats, bad, total, readable, note


def format_lines(path, verdict, stats, bad, total, note):
    out = ["%s: median heavy_slot wait %.1f s against the %g s bar (strictly over flips)"
           % (verdict, stats["median_s"], MEDIAN_BAR_S)]
    if note:
        out.append("reason: %s" % note)
    out.append("source: %s, %d lines, %d usable, %d unusable, first at %s"
               % (path, total, stats["n"], bad, stats["first_at"] or "NO-DATA"))
    out.append("p90 %.1f s, over %g s: %d (%.2f percent), unqueued: %d"
               % (stats["p90_s"], MEDIAN_BAR_S, stats["over_bar"],
                  100.0 * stats["over_bar"] / stats["n"] if stats["n"] else 0.0, stats["unqueued"]))
    out.append("limit: a run that bypassed the queue wrote no line and waited 0 s, so the median "
               "can only be overstated; a line that could not be written is not counted")
    return out


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--path", default=None, help="the wait file (default: <slot dir>/waits.jsonl)")
    args = ap.parse_args(argv)
    path = args.path or default_path()
    verdict, stats, bad, total, readable, note = report(path)
    for line in format_lines(path, verdict, stats, bad, total, note):
        print(line)
    return 0 if verdict in ("HOLD", "FLIP") else 2


if __name__ == "__main__":
    sys.exit(main())

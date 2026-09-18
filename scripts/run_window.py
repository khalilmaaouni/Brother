#!/usr/bin/env python3
"""LIMIT-05: the one place an unattended tick learns its run window.

A watchdog that carried its own copy of the drain time (10:30) kept it after
the founder moved the stop to 16:00, and would have drained four and a half
hours early. The tick now carries no time at all; it runs this, which reads
the window from the plan of record every time.

Prints one line, PHASE then the facts it came from:
  RUN    before drain_start
  DRAIN  from drain_start to hard_stop: admit no new long unit
  STOP   at or after hard_stop: admit nothing, write the handoff
Failure direction: a missing, unreadable or unparseable window prints DRAIN
with NO-DATA and exits 2. An unknown window never reads as RUN.

Usage: python3 scripts/run_window.py [--plan PATH] [--now ISO]
"""
import argparse
import datetime
import json
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PLAN = os.path.join(ROOT, "docs", "plan", "ORCH-1020-WBS.json")


def phase(window, now):
    drain = datetime.datetime.fromisoformat(window["drain_start"])
    stop = datetime.datetime.fromisoformat(window["hard_stop"])
    if drain > stop:
        raise ValueError("drain_start is after hard_stop")
    if now >= stop:
        return "STOP", 0
    left = int((stop - now).total_seconds() // 60)
    return ("DRAIN" if now >= drain else "RUN"), left


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--plan", default=PLAN)
    ap.add_argument("--now")
    a = ap.parse_args(argv)
    try:
        now = (datetime.datetime.fromisoformat(a.now) if a.now
               else datetime.datetime.now().astimezone())
        with open(a.plan, encoding="utf-8") as fh:
            window = json.load(fh)["window"]
        name, left = phase(window, now)
    except (OSError, ValueError, KeyError, TypeError) as exc:
        print("DRAIN NO-DATA: run window unreadable (%s: %s); admit no new work"
              % (type(exc).__name__, exc))
        return 2
    print("%s now=%s drain_start=%s hard_stop=%s minutes_to_stop=%d source=%s"
          % (name, now.strftime("%H:%M"), window["drain_start"], window["hard_stop"],
             left, os.path.relpath(a.plan, ROOT)))
    return 0


if __name__ == "__main__":
    sys.exit(main())

#!/usr/bin/env python3
"""LIMIT-03 of the 1.0.20 control plane: act BEFORE the account limit.

WHAT WAS MEASURED BEFORE THIS WAS WRITTEN, 2026-09-18, so the next reader
does not rebuild a route already shown dead:
  - usage_check.py (ccusage) reports only CLOCK time left in a 5-hour block,
    never token headroom; it said GO with 232 minutes left on a morning the
    limit hit mid-block.
  - Learning a cap from past hits fails: over the last 12 five-hour hits the
    ccusage block cost at the hit ranged $175.31 to $719.29 and output
    468,938 to 5,407,813 tokens, because ccusage's blocks do not align with
    the account's own window.
  - Transcripts carry no pre-limit warning and no structured quota field.
  - The desktop app's get_usage tool DOES return the account's own percent
    used per window (5-hour, weekly, per-model weekly). Only a live session
    can call it, so the tick calls it and hands the JSON to this module.

POLICY, one pure function, highest window wins:
  RUN         every window under SHIFT_AT
  SHIFT       any window at or over SHIFT_AT (70): no new Claude builders;
              new dispatch goes to non-Claude lanes; in-flight work continues
  CHECKPOINT  any window at or over CHECKPOINT_AT (85): commit what is green,
              arm this run's restart, admit nothing new on Claude
  HOLD        the usage cannot be read: admit nothing new, say NO-DATA.
              An unknown reading never reads as RUN.

Usage: python3 scripts/limit_preempt.py USAGE_JSON   (the get_usage result)
Exit 0 on RUN, 1 on SHIFT or CHECKPOINT, 2 on HOLD.
"""
import json
import sys

SHIFT_AT = 70
CHECKPOINT_AT = 85


def decide(usage):
    """(verdict, reason) from a get_usage result dict."""
    try:
        plan = usage["plan"]
        if not isinstance(plan, dict):
            return "HOLD", "NO-DATA: no plan block in the usage reading"
        if plan.get("status") != "ok":
            return "HOLD", "NO-DATA: plan limits status %r" % plan.get("status")
        windows = [(w["label"], float(w["percentUsed"])) for w in plan["windows"]]
    except (KeyError, TypeError, ValueError) as exc:
        return "HOLD", "NO-DATA: usage unreadable (%s)" % exc
    if not windows:
        return "HOLD", "NO-DATA: no limit windows reported"
    label, worst = max(windows, key=lambda lw: lw[1])
    summary = ", ".join("%s %g%%" % lw for lw in windows)
    if worst >= CHECKPOINT_AT:
        return "CHECKPOINT", "%s at %g%% (>= %d): %s" % (label, worst, CHECKPOINT_AT, summary)
    if worst >= SHIFT_AT:
        return "SHIFT", "%s at %g%% (>= %d): %s" % (label, worst, SHIFT_AT, summary)
    return "RUN", summary


def main(argv=None):
    argv = sys.argv[1:] if argv is None else argv
    try:
        with open(argv[0], encoding="utf-8") as fh:
            usage = json.load(fh)
    except (IndexError, OSError, ValueError) as exc:
        usage = {"plan": {"status": "unreadable: %s" % exc}}
    verdict, reason = decide(usage)
    print("%s %s" % (verdict, reason))
    return {"RUN": 0, "SHIFT": 1, "CHECKPOINT": 1}.get(verdict, 2)


if __name__ == "__main__":
    sys.exit(main())

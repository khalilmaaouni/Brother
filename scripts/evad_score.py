#!/usr/bin/env python3
"""evad_score: the EVAD gauntlet becomes an instrument with a trend, not a one-off review.

THE GAP THIS CLOSES, founder 2026-08-31, his words: "we have terrible score with EVAD we
are not following up on to see if we improve upon". A gauntlet run once is a review. A
gauntlet with a frozen baseline, a recorded history and a staleness clock is a MEASUREMENT,
and only a measurement can show improvement.

Three verdict words, the estate's own: PASS, FAIL, NO-DATA. A trial with no run is NO-DATA
and never a pass. A run older than STALE_DAYS makes the whole score NO-DATA, because a score
nobody has re-measured is an opinion about the past, which is exactly the failure this file
was written for.

Usage:
  python3 scripts/evad_score.py                 # the current standing and the trend
  python3 scripts/evad_score.py --record FILE   # append a new run from a results JSON
  python3 scripts/evad_score.py --selftest      # prove the six cases

Exit 0 when the latest run holds or improves on the previous one, 1 when any trial
regressed or the score is stale, 2 when the store cannot be read. A non-zero exit here is
a finding about the product, never a broken command.
"""
import argparse
import datetime
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
STORE = os.path.join(HERE, "..", "docs", "plan", "evad-gauntlet", "HISTORY.json")

#: a score nobody re-measured within this window stops counting as current.
STALE_DAYS = 7

#: the seven angles, their bar, and whether the bar is a number or a judgement.
TRIALS = {
    "t1": "first ten minutes: a stranger reaches value without an internal noun",
    "t2": "delivery proof: the chain survives a skeptic",
    "t3": "release integrity: byte for byte, tamper caught",
    "t4": "blind Japanese corpus: negative class at or above 90 percent",
    "t5": "failure injection: every control goes red when removed",
    "t6": "docs honesty: every executable claim reproduces",
    "t7": "overhead: a small task inside 1.5x the fast rival, both halves delivered",
}


def _read(path):
    """(runs, problem). A missing store is an EMPTY history, never an error: the
    first recorded run has nothing before it and that is a normal state."""
    if not os.path.exists(path):
        return [], ""
    try:
        with open(path, "r", encoding="utf-8") as fh:
            doc = json.load(fh)
    except (OSError, ValueError) as exc:
        return None, "could not read %s: %s" % (path, exc)
    runs = doc.get("runs")
    if not isinstance(runs, list):
        return None, "%s holds no runs list" % path
    return runs, ""


def _write(path, runs):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as fh:
        json.dump({"schema": 1, "runs": runs}, fh, indent=2, ensure_ascii=False)
        fh.write("\n")


def score(run):
    """(passed, total, fraction). A trial with no verdict counts as NO-DATA and is
    excluded from the denominator, so an unrun trial can never flatter the score and
    can never punish it either. The count of NO-DATA is reported beside the number."""
    verdicts = run.get("trials") or {}
    counted = [v for v in verdicts.values() if v.get("verdict") in ("PASS", "FAIL")]
    passed = [v for v in counted if v.get("verdict") == "PASS"]
    if not counted:
        return 0, 0, None
    return len(passed), len(counted), len(passed) / float(len(counted))


def regressions(prev, cur):
    """Trials that were PASS and are now FAIL. The only thing that makes this tool
    exit non-zero on a comparison, because a NO-DATA is a gap in measurement rather
    than a regression in the product, and conflating them hides both."""
    out = []
    p = (prev or {}).get("trials") or {}
    c = (cur or {}).get("trials") or {}
    for tid, before in p.items():
        after = c.get(tid) or {}
        if before.get("verdict") == "PASS" and after.get("verdict") == "FAIL":
            out.append(tid)
    return sorted(out)


def days_old(run, now=None):
    """Age of a run in days, or None when its stamp is unreadable (NO-DATA, never 0)."""
    now = now or datetime.datetime.now(datetime.timezone.utc)
    try:
        when = datetime.datetime.fromisoformat(str(run.get("measured_at")))
    except (TypeError, ValueError):
        return None
    if when.tzinfo is None:
        when = when.replace(tzinfo=datetime.timezone.utc)
    return (now - when).total_seconds() / 86400.0


def report(runs, now=None):
    """(lines, exit_code). Everything this tool says, and nothing it did not measure."""
    lines = []
    if not runs:
        return ["NO-DATA: no EVAD run has been recorded yet, so there is no score to "
                "improve on. Run the gauntlet and record it."], 1
    cur = runs[-1]
    prev = runs[-2] if len(runs) > 1 else None
    passed, total, frac = score(cur)
    nodata = len(TRIALS) - total

    age = days_old(cur, now)
    if age is None:
        lines.append("NO-DATA: the latest run carries no readable timestamp")
        stale = True
    else:
        stale = age > STALE_DAYS
        lines.append("latest run %s, %.1f day(s) old%s"
                     % (cur.get("measured_at"), age,
                        ", STALE" if stale else ""))

    if total == 0:
        lines.append("NO-DATA: the latest run scored no trial either way")
        return lines, 1

    lines.append("EVAD score %d/%d (%.0f%%)%s"
                 % (passed, total, frac * 100,
                    ", %d trial(s) NO-DATA" % nodata if nodata else ""))
    lines.append("tree under test: %s" % (cur.get("tree") or "NO-DATA"))

    if prev:
        pp, pt, pf = score(prev)
        if pf is not None:
            delta = (frac - pf) * 100
            word = "unchanged" if abs(delta) < 0.5 else ("improved" if delta > 0 else "REGRESSED")
            lines.append("versus the previous run (%d/%d): %s, %+.0f points"
                         % (pp, pt, word, delta))
    else:
        lines.append("this is the first recorded run, so there is no trend yet")

    lines.append("")
    for tid in sorted(TRIALS):
        t = (cur.get("trials") or {}).get(tid) or {}
        v = t.get("verdict") or "NO-DATA"
        note = t.get("measured") or ""
        was = ((prev or {}).get("trials") or {}).get(tid, {}).get("verdict")
        arrow = ""
        if was and was != v:
            arrow = "  (%s -> %s)" % (was, v)
        lines.append("  %-4s %-8s %s%s" % (tid, v, TRIALS[tid], arrow))
        if note:
            lines.append("       %s" % note)

    regs = regressions(prev, cur) if prev else []
    code = 0
    if regs:
        lines.append("")
        lines.append("REGRESSED: %s" % ", ".join(regs))
        code = 1
    if stale:
        lines.append("")
        lines.append("STALE: the last run is older than %d days, so this score describes "
                     "a tree that may no longer exist. Re-run the gauntlet." % STALE_DAYS)
        code = 1
    return lines, code


def _selftest():
    """Six cases, each one a way this tool could lie if it were written carelessly."""
    now = datetime.datetime(2026, 9, 1, tzinfo=datetime.timezone.utc)
    fresh = "2026-08-31T12:00:00+00:00"

    # 1. no runs at all is NO-DATA and non-zero, never a silent zero percent.
    lines, code = report([], now)
    assert code == 1 and "NO-DATA" in lines[0], lines

    # 2. an unrun trial is excluded from the denominator rather than counted as a loss.
    run = {"measured_at": fresh, "tree": "x",
           "trials": {"t3": {"verdict": "PASS"}, "t1": {"verdict": "FAIL"}}}
    p, t, f = score(run)
    assert (p, t) == (1, 2) and abs(f - 0.5) < 1e-9, (p, t, f)

    # 3. a first run reports no trend rather than inventing one.
    lines, code = report([run], now)
    assert any("first recorded run" in l for l in lines), lines
    assert code == 0, (code, lines)

    # 4. a PASS that becomes FAIL is a regression and exits 1.
    worse = {"measured_at": fresh, "tree": "x",
             "trials": {"t3": {"verdict": "FAIL"}, "t1": {"verdict": "FAIL"}}}
    lines, code = report([run, worse], now)
    assert code == 1 and any("REGRESSED" in l for l in lines), lines

    # 5. a PASS that becomes NO-DATA is NOT called a regression: it is a gap in
    #    measurement, and conflating the two hides both.
    gap = {"measured_at": fresh, "tree": "x", "trials": {"t1": {"verdict": "FAIL"}}}
    assert regressions(run, gap) == [], regressions(run, gap)

    # 6. a stale run exits 1 even when nothing regressed, because a score nobody
    #    re-measured is an opinion about the past.
    old = {"measured_at": "2026-01-01T00:00:00+00:00", "tree": "x",
           "trials": {"t3": {"verdict": "PASS"}}}
    lines, code = report([old], now)
    assert code == 1 and any("STALE" in l for l in lines), lines

    print("evad_score selftest OK: 6 case(s) driven, including the two that would "
          "otherwise flatter the score (an unrun trial, and a stale run)")
    return 0


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--store", default=STORE)
    ap.add_argument("--record", help="a results JSON to append as a new run")
    ap.add_argument("--selftest", action="store_true")
    args = ap.parse_args(argv)

    if args.selftest:
        return _selftest()

    runs, problem = _read(args.store)
    if runs is None:
        print(problem)
        return 2

    if args.record:
        try:
            with open(args.record, "r", encoding="utf-8") as fh:
                new = json.load(fh)
        except (OSError, ValueError) as exc:
            print("could not read %s: %s" % (args.record, exc))
            return 2
        runs.append(new)
        _write(args.store, runs)
        print("recorded a run measured %s into %s"
              % (new.get("measured_at"), args.store))

    lines, code = report(runs)
    for line in lines:
        print(line)
    return code


if __name__ == "__main__":
    sys.exit(main())

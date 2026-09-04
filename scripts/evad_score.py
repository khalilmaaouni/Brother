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

P16 (persona plan section 31.2, the DS flywheel; roadmap row P16), a second
and unrelated report bolted onto this same CLI file rather than a sibling
module: the done-check the roadmap wrote names this file and this flag
directly, so the file stays the home even though its subject (three counts
read off run records) has nothing to do with the seven EVAD trials above.
--personas prints three data-science metrics counted from receipts, never
typed: reproducible experiment rate, leakage catches and promotions parked.
See personas_report() below for what each counts and why a count of zero
prints differently from a count of nothing.

Usage:
  python3 scripts/evad_score.py                 # the current standing and the trend
  python3 scripts/evad_score.py --record FILE   # append a new run from a results JSON
  python3 scripts/evad_score.py --selftest      # prove the six cases
  python3 scripts/evad_score.py --personas [--runs-root DIR]  # the DS flywheel counts

Exit 0 when the latest run holds or improves on the previous one, 1 when any trial
regressed or the score is stale, 2 when the store cannot be read. A non-zero exit here is
a finding about the product, never a broken command. --personas always exits 0: it is a
report, not a gate, and a NO-DATA line inside it is not a command failure.
"""
import argparse
import datetime
import glob
import json
import os
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import board_status  # noqa: E402
import receipt_door  # noqa: E402

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


# ---------------------------------------------------------------------------
# P16 (persona plan section 31.2, the DS flywheel): three metrics counted
# from receipts, never typed by hand, so a persona strategy is a number with
# a trend rather than a document (doc 31's own opening line: do not judge by
# pack count). Every reader below walks the same run directories
# board_status.receipts_bound() already walks (RUNS_ROOT and USER_RUNS_ROOT,
# a run name seen under more than one root counted once), so this can never
# report a different universe of runs than the rest of the estate does.
# ---------------------------------------------------------------------------

#: P10 (roadmap row, still OPEN as of this writing) has not yet added a
#: data-science class to receipt_door.RISK_TRIGGERS, so no parked row can
#: yet carry a trigger literally named "promotion". This matches P10's own
#: three named example phrases instead ("promote the model to production",
#: "raise the churn threshold to 0.6", "retrain on the refreshed dataset"),
#: word-bounded the same way RISK_TRIGGERS is, so the count below reads real
#: zeros today and starts counting the instant P10 lands a trigger whose
#: name or matched words use any of these roots -- without this file
#: changing.
_PROMOTION_WORDS = re.compile(
    r"\b(promot\w*|retrain\w*|refresh\w*|threshold\w*)\b", re.IGNORECASE)

#: split_check.py (P8, DONE) prints exactly this prefix on its FAIL branch,
#: never on PASSED or NO-DATA; matching the literal string is simpler and
#: less brittle than re-deriving its two FAIL conditions (overlap, past
#: cutoff) here a second time.
_LEAKAGE_CAUGHT = "split-check: FAIL"


def _run_dirs(roots):
    """Every run directory found under any of `roots`, deduplicated by name
    across roots (a name seen more than once counts once, the first root
    wins) -- the same rule board_status.receipts_bound() applies, so a run
    made through the shipped runtime is counted here exactly as it would be
    there. [] when every root is missing or holds nothing; the caller, not
    this function, decides whether an empty result means NO-DATA."""
    seen = set()
    out = []
    for root in roots:
        if not os.path.isdir(root):
            continue
        for name in sorted(os.listdir(root)):
            if name in seen:
                continue
            seen.add(name)
            run_dir = os.path.join(root, name)
            if os.path.isdir(run_dir):
                out.append(run_dir)
    return out


def _run_rows(run_dir):
    """Every row in this run's own Work document (W-*.json), or [] when
    there is none or it fails to parse -- one malformed run degrades to
    zero rows for it, never a crash for the whole report."""
    wfiles = sorted(glob.glob(os.path.join(run_dir, "W-*.json")))
    if not wfiles:
        return []
    try:
        with open(wfiles[0], "r", encoding="utf-8") as fh:
            doc = json.load(fh)
    except (OSError, ValueError):
        return []
    return doc.get("rows") or doc.get("units") or []


def _run_claims(run_dir):
    """{unit_id: claim, ...} from this run's claims.json, or {} when there
    is none or it fails to parse."""
    path = os.path.join(run_dir, "claims.json")
    if not os.path.isfile(path):
        return {}
    try:
        with open(path, "r", encoding="utf-8") as fh:
            data = json.load(fh)
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def reproducible_experiment_rate(run_dirs):
    """(passed, total). total is every row across `run_dirs` whose
    evidence_family is E18 (P6's own family for a unit that claims a
    measured number, brother_run.py's E18_FIELDS); passed is how many of
    those carry every one of P6's five identity fields, read through
    receipt_door.e18_gap() (gap == "" means full), the exact reader P6's own
    receipt uses rather than a second copy of its rule. total == 0 is a real
    fact (no E18 unit has run yet) and the caller reports it as NO-DATA,
    because a rate with a zero denominator asserts nothing."""
    total = 0
    passed = 0
    for run_dir in run_dirs:
        for row in _run_rows(run_dir):
            if str(row.get("evidence_family") or "") != "E18":
                continue
            total += 1
            if receipt_door.e18_gap(row) == "":
                passed += 1
    return passed, total


def leakage_catches(run_dirs):
    """How many claims across `run_dirs` recorded scripts/split_check.py
    (P8) catching a leak: the claim's own evidence names split_check.py as
    the check_command and the check's own captured output carries its FAIL
    line. Read from claims.json rather than row status, because a caught
    leak is exactly the case where the unit's check exited non-zero and the
    unit never reached DONE -- the check still ran, and still caught it,
    and that is the fact this metric exists to keep, not the row's final
    disposition."""
    count = 0
    for run_dir in run_dirs:
        for claim in _run_claims(run_dir).values():
            if not isinstance(claim, dict):
                continue
            evidence = claim.get("evidence") or {}
            if not isinstance(evidence, dict):
                continue
            command = str(evidence.get("check_command") or "")
            output = str(evidence.get("output") or "")
            if "split_check.py" in command and _LEAKAGE_CAUGHT in output:
                count += 1
    return count


def promotions_parked(run_dirs):
    """How many rows across `run_dirs` loom.py's park_units() has held
    (row["parked"] is set, the persisted marker loom.py writes onto the
    Work document before a risky unit is ever claimed) whose trigger names
    or matched words look like a data-science promotion, threshold change
    or retrain (see _PROMOTION_WORDS above for why the match is on words
    rather than a fixed trigger name). Counted cumulatively: a row that was
    later released still counted here once, because the number this metric
    reports is how many times the gate caught one, not how many are still
    waiting."""
    count = 0
    for run_dir in run_dirs:
        for row in _run_rows(run_dir):
            parked = row.get("parked")
            if not isinstance(parked, dict):
                continue
            triggers = parked.get("triggers") or []
            text = " ".join(
                "%s %s" % (t.get("trigger") or "", t.get("words") or "")
                for t in triggers if isinstance(t, dict))
            if _PROMOTION_WORDS.search(text):
                count += 1
    return count


def personas_report(roots):
    """(lines, exit_code), the three DS flywheel counts read off `roots`.
    exit_code is always 0: this is a report, never a gate, and a NO-DATA
    line inside it is a fact about what has not run yet, not a broken
    command. NO-DATA appears per metric it applies to (a rate whose
    denominator is 0), never folded into one blanket line, so a real zero
    (checked, and it is zero -- leakage catches, promotions parked) is
    never confused with nothing to check (no E18 unit at all)."""
    run_dirs = _run_dirs(roots)
    lines = []
    passed, total = reproducible_experiment_rate(run_dirs)
    if total == 0:
        lines.append("NO-DATA: reproducible experiment rate: no E18 unit "
                     "has run yet under %s" % ", ".join(roots))
    else:
        lines.append("reproducible %d of %d" % (passed, total))
    lines.append("leakage catches %d" % leakage_catches(run_dirs))
    lines.append("promotions parked %d" % promotions_parked(run_dirs))
    return lines, 0


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--store", default=STORE)
    ap.add_argument("--record", help="a results JSON to append as a new run")
    ap.add_argument("--selftest", action="store_true")
    ap.add_argument("--personas", action="store_true",
                    help="P16: print the three DS flywheel counts "
                         "(reproducible experiment rate, leakage catches, "
                         "promotions parked) instead of the EVAD score")
    ap.add_argument("--runs-root",
                    help="P16: read run records from this one directory "
                         "instead of the real default roots "
                         "(board_status.RUNS_ROOT and .USER_RUNS_ROOT)")
    args = ap.parse_args(argv)

    if args.selftest:
        return _selftest()

    if args.personas:
        roots = ([args.runs_root] if args.runs_root
                else [board_status.RUNS_ROOT, board_status.USER_RUNS_ROOT])
        lines, code = personas_report(roots)
        for line in lines:
            print(line)
        return code

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

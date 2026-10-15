#!/usr/bin/env python3
"""PG1.g: the plan pipeline's four bars, read from real timestamps, never from a report.

  python3 scripts/plan_clock.py --version 1.1.5 [--handover-root DIR] [--scores FILE] [--specs DIR] [--runs DIR]
                                [--plan FILE] [--width N] [--floor N]

Bars, in seconds after the newest close pack: first spec at 9 (300), every spec at 9 (300), first loop admission
(600), first landing (3600). One row per bar: PASS under the bar, FAIL over it, NO-DATA when either timestamp is
missing. Exit 0 when every bar is PASS, 1 on any FAIL, 2 on any NO-DATA (NO-DATA outranks FAIL: a bar is never
passed or failed by absence). The score file is JSON {sub unit id: score}; a spec's time is its file's mtime; a run's
admission is the earliest jobs.json mtime under its run directory; a landing is the time on its STATUS LANDED line.
"""
import argparse
import datetime
import json
import math
import os
import re
import sys
from typing import Dict, Optional

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, HERE)

BARS = {"first_spec_9": 300.0, "all_specs_9": 300.0, "first_admission": 600.0, "first_landing": 3600.0}
ORDER = ("first_spec_9", "all_specs_9", "first_admission", "first_landing")
WAVE_SECONDS = 190.0   # one writer on one sub unit, measured 2026-10-14 (PG1 spec, W2)
DEFAULT_WIDTH = 4      # the measured writer wave of 2026-10-14
MAX_SCORE_BYTES = 4 * 1024 * 1024
HANDOVER_ROOT = os.path.expanduser("~/Documents/BrotherModeUp-handovers")
RUNS = os.path.expanduser("~/.claude/evidence/unit-runs")
LANDED_RE = re.compile(r"^LANDED \S+ at (\d{4}-\d\d-\d\d \d\d:\d\d:\d\d)\b")


def close_time(handover_root: str) -> Optional[float]:
    """The newest close pack's time (close_ceremony_check.newest_pack, its directory mtime), None when no pack."""
    try:
        import close_ceremony_check
        pack = close_ceremony_check.newest_pack(handover_root)
        return os.path.getmtime(pack) if pack else None
    except (OSError, ValueError, ImportError):
        return None


def _unit_of(sub):
    return sub.split(".", 1)[0]


def spec_times(score_json: str, specs_dir: str, floor: int, plan_subs=None) -> Dict[str, Optional[float]]:
    """{first_spec_9, all_specs_9, ignored}: the earliest mtime among spec files whose every sub unit is at or above
    the floor, and the latest mtime when every sub unit is; None when the score file is unreadable, when any sub unit
    is under the floor (all_specs_9) or when none is at it (first_spec_9). Sub units the plan does not list are
    ignored and named; a plan sub unit with no score counts as under the floor."""
    out = {"first_spec_9": None, "all_specs_9": None, "ignored": []}
    try:
        if os.path.getsize(score_json) > MAX_SCORE_BYTES:
            return out
        with open(score_json, encoding="utf-8") as fh:
            scores = json.load(fh)
    except (OSError, ValueError):
        return out
    if not isinstance(scores, dict) or not scores:
        return out
    if plan_subs is not None:
        out["ignored"] = sorted(s for s in scores if s not in plan_subs)
        subs = list(plan_subs)
    else:
        subs = list(scores)
    if not subs:
        return out
    by_unit = {}
    for sub in subs:
        sc = scores.get(sub)
        ok = isinstance(sc, (int, float)) and not isinstance(sc, bool) and sc >= floor
        by_unit.setdefault(_unit_of(sub), []).append(ok)
    good, all_ok = [], True
    for unit, oks in by_unit.items():
        try:
            mtime = os.path.getmtime(os.path.join(specs_dir, unit + ".md"))
        except OSError:
            all_ok = False
            continue
        if all(oks):
            good.append(mtime)
        else:
            all_ok = False
    if good:
        out["first_spec_9"] = min(good)
    if all_ok and good:
        out["all_specs_9"] = max(good)
    return out


def _landed_at(status_path):
    try:
        with open(status_path, encoding="utf-8") as fh:
            m = LANDED_RE.match(fh.readline().strip())
    except (OSError, UnicodeDecodeError):
        return None
    if not m:
        return None
    try:
        return datetime.datetime.strptime(m.group(1), "%Y-%m-%d %H:%M:%S").timestamp()
    except ValueError:
        return None


def loop_times(runs_dir: str, plan_subs: list) -> Dict[str, Optional[float]]:
    """{first_admission, first_landing}: the earliest jobs.json mtime under a run directory of a plan sub unit, and
    the earliest landing time read from a run's STATUS LANDED line; None when absent. A run directory is
    <sub>-<stamp>; one whose sub unit the plan does not list is not this plan's."""
    out = {"first_admission": None, "first_landing": None}
    subs = set(plan_subs or [])
    try:
        names = os.listdir(runs_dir)
    except OSError:
        return out
    for name in names:
        run = os.path.join(runs_dir, name)
        sub, dash, stamp = name.rpartition("-")
        if not dash or sub not in subs or not stamp.isdigit() or not os.path.isdir(run):
            continue
        for dirpath, _dirs, files in os.walk(run):
            if "jobs.json" in files:
                try:
                    t = os.path.getmtime(os.path.join(dirpath, "jobs.json"))
                except OSError:
                    continue
                if out["first_admission"] is None or t < out["first_admission"]:
                    out["first_admission"] = t
        t = _landed_at(os.path.join(run, "STATUS"))
        if t is not None and (out["first_landing"] is None or t < out["first_landing"]):
            out["first_landing"] = t
    return out


def verdicts(close: Optional[float], times: Dict[str, Optional[float]], bars: Dict[str, float], width: int,
             subs: int) -> list:
    """One (bar, seconds or None, verdict, detail) per bar: PASS under the bar, FAIL over it, NO-DATA when either
    timestamp is missing; the all_specs_9 detail carries the arithmetic ceil(subs / width) times 190 s."""
    rows = []
    for bar in ORDER:
        t = times.get(bar)
        detail = "bar %d s" % bars[bar]
        if bar == "all_specs_9":
            w = width if isinstance(width, int) and width > 0 else DEFAULT_WIDTH
            waves = int(math.ceil(float(subs) / w)) if isinstance(subs, int) and subs > 0 else 0
            detail += "; width %d over %d sub units is %d wave(s) x %d s = %d s (190 s per wave measured 2026-10-14)" % (
                w, subs if isinstance(subs, int) else 0, waves, WAVE_SECONDS, waves * WAVE_SECONDS)
        if close is None or t is None:
            rows.append((bar, None, "NO-DATA", detail + "; %s" % ("no close pack" if close is None else "no timestamp")))
            continue
        secs = t - close
        if secs < 0:
            rows.append((bar, secs, "FAIL", detail + "; clock before close"))
        elif secs <= bars[bar]:
            rows.append((bar, secs, "PASS", detail))
        else:
            rows.append((bar, secs, "FAIL", detail))
    return rows


def exit_code(rows) -> int:
    kinds = {r[2] for r in rows}
    if not rows or "NO-DATA" in kinds:
        return 2
    return 1 if "FAIL" in kinds else 0


def plan_subs_of(plan_path):
    try:
        with open(plan_path, encoding="utf-8") as fh:
            plan = json.load(fh)
        subs = []
        for u in plan.get("units", []):
            subs.extend(s if isinstance(s, str) else s.get("id") for s in u.get("sub_units", []))
        return [s for s in subs if isinstance(s, str) and s]
    except (OSError, ValueError, AttributeError, TypeError):
        return None


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="plan_clock.py")
    ap.add_argument("--version", required=True)
    ap.add_argument("--handover-root", default=HANDOVER_ROOT)
    ap.add_argument("--scores")
    ap.add_argument("--specs", default=os.path.join(ROOT, "docs", "plan", "specs"))
    ap.add_argument("--runs", default=RUNS)
    ap.add_argument("--plan")
    ap.add_argument("--width", type=int, default=int(os.environ.get("BROTHER_WRITER_WIDTH") or DEFAULT_WIDTH))
    ap.add_argument("--floor", type=int, default=9)
    a = ap.parse_args(argv)
    if not re.match(r"^\d+\.\d+\.\d+$", a.version):
        print("NO-DATA: the version %r is not N.N.N" % a.version)
        return 2
    plan = a.plan or os.path.join(ROOT, "docs", "plan", "BROTHER-%s-WBS.json" % a.version)
    scores = a.scores or os.path.join(ROOT, "docs", "plan", "specs", a.version, "SCORES.json")
    subs = plan_subs_of(plan)
    close = close_time(a.handover_root)
    st = spec_times(scores, a.specs, a.floor, subs)
    lt = loop_times(a.runs, subs or [])
    times = dict(st)
    times.update(lt)
    rows = verdicts(close, times, BARS, a.width, len(subs or []))
    src = {"first_spec_9": scores, "all_specs_9": scores, "first_admission": a.runs, "first_landing": a.runs}
    print("PLAN CLOCK %s | close %s | plan %s (%s sub units)" % (
        a.version, "NO-DATA" if close is None else datetime.datetime.fromtimestamp(close).isoformat(timespec="seconds"),
        plan, "NO-DATA" if subs is None else len(subs)))
    for bar, secs, verdict, detail in rows:
        print("%-7s %-16s %10s  %s | from %s" % (verdict, bar, "-" if secs is None else "%.0f s" % secs, detail, src[bar]))
    if st.get("ignored"):
        print("ignored score entries not in the plan: %s" % ", ".join(st["ignored"]))
    return exit_code(rows)


if __name__ == "__main__":
    sys.exit(main())

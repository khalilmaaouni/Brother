#!/usr/bin/env python3
"""The standard brother.loop report: the same sections, in the same order, every time.

Owner, 2026-09-21: "the reports out of the loop should be standardized and perfect to be clear on
progress and what was done, what were the issues root causes and what to do next".

Until now every loop report was hand written, so each one chose its own sections, its own window
and its own figures, and two reports on the same night could not be compared. Worse, a hand
written report is where a fabricated number gets in: the estate has a standing law about exactly
that, after a progress page carried timestamps invented from narrative feel.

So this tool reads the ledgers and prints the report. Three rules it never breaks:

  1. EVERY FIGURE NAMES ITS SOURCE. The line that prints a number also prints the file it came
     from, so any figure can be re-derived by hand.
  2. UNREADABLE IS NO-DATA, NEVER ZERO. A missing ledger prints NO-DATA and says which file was
     missing. Zero landings and an unreadable landing ledger are opposite facts and must not
     render the same way.
  3. NO NUMBER IS COMPUTED FROM ANOTHER REPORT. Only from the primary ledgers below.

Sections follow the estate's own report law: KEY TAKEAWAY, ACTIONS, BLOCKERS, NEXT STEPS,
FACILITATORS, with the evidence that supports each.

usage:  loop_report.py [--since-hours 24] [--plan PATH]
        loop_report.py --run DRIVER_LOG [--plan PATH]   (exactly that run: from its RUN START line to its RUN END line)
        loop_report.py --selftest
"""
import collections, datetime, json, math, os, re, sys
from typing import List, Optional, Tuple
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import plan_store  # noqa: E402  (the one landed test every loop reader shares: plan_store.sub_landed)
import proof_ledger  # noqa: E402  (the one strict row parser every money reader shares: proof_ledger.loads)

EV = os.path.expanduser("~/.claude/evidence")
STAGES = os.path.join(EV, "brother-stages.jsonl")
FAILURES = os.path.join(EV, "brother-failures.jsonl")
LEDGER = DEFAULT_LEDGER = os.path.expanduser("~/.claude/brother-or-dispatch-state/openrouter-ledger.jsonl")
PLAN = "docs/plan/BROTHER-1.1.0-LAUNCH-WBS.json"


def rows(path, since=None, until=None):
    """Every readable JSON object in a jsonl, newest window only. A corrupt line is skipped and
    counted, never guessed at: a half written line is what a crash leaves behind. A line that
    repeats a member is corrupt too (X1 finding 4, 2026-09-27): json.loads kept the LAST of them,
    so a RECONCILE carrying actual_cost 9 then 0 read as a measured zero. proof_ledger.loads, the
    strict parser every money reader uses, refuses it."""
    out, bad = [], 0
    try:
        with open(path, encoding="utf-8") as f:
            for line in f:
                try:
                    r = proof_ledger.loads(line)
                except ValueError:
                    bad += 1
                    continue
                if isinstance(r, dict) and (since is None or (r.get("at") or 0) >= since) \
                        and (until is None or (r.get("at") or 0) <= until):
                    out.append(r)
    except OSError:
        return None, bad                      # None means UNREADABLE, distinct from an empty list
    return out, bad


def progress(plan_path):
    """Units and sub units landed, read from the plan's own evidence strings, the same way every
    other loop tool reads them (plan_store.sub_landed), so the board and this report cannot disagree."""
    try:
        plan = json.load(open(plan_path, encoding="utf-8"))
    except (OSError, ValueError):
        return None
    subs_total = subs_done = 0
    done_units = 0
    try:
        units = plan.get("units") or []
        for u in units:
            ev = u.get("evidence") or ""
            for s in (u.get("sub_units") or []):
                subs_total += 1
                if plan_store.sub_landed(s, ev):
                    subs_done += 1
            if u.get("state") == "DONE":
                done_units += 1
    except (AttributeError, TypeError, ValueError):
        return None                           # a corrupt plan is NO-DATA, never a count
    return {"units_total": len(units), "units_done": done_units,
            "subs_total": subs_total, "subs_done": subs_done}


def _at(r):
    """A row's timestamp when it is a NUMBER, else None.

    TWO DEFECTS AT ONE SOURCE, both found on 2026-09-21 by fixtures written for other properties.
    (1) `sorted(rows, key=lambda r: r.get("at") or 0)` compared a string to an int the moment a
    ledger carried an `at` of "banana", and the TypeError came out of the SORT, taking the whole
    report with it: the blockers section a reader most needs died for one bad byte in a jsonl.
    (2) `if t0` treated an enter stamped at epoch 0 as no enter at all, because 0 is falsy, so the
    pair silently contributed zero seconds and the stage read as free. A timestamp is either
    present and numeric or it is not, and `is not None` is the test that says so. stage_log made
    exactly this fix one file over; the same guard belongs here, at the one place both the sort and
    the pairing read a stamp."""
    v = r.get("at") if isinstance(r, dict) else None
    return v if isinstance(v, (int, float)) else None


def stage_yield(stage_rows):
    """Per stage: how many finished, how many passed, and the share of effort thrown away.

    Yield is the number that matters and the one nobody computes by hand: a stage that runs a
    hundred times and passes a third of them is spending two thirds of its money on work that is
    discarded. Busy hours come from matched enter/leave pairs, never from wall clock, because
    stages overlap and wall clock would under count them."""
    if stage_rows is None:
        return None
    opened, stats = {}, collections.defaultdict(lambda: {"ran": 0, "ok": 0, "secs": 0.0, "lost_secs": 0.0})
    for r in sorted(stage_rows, key=lambda r: _at(r) if _at(r) is not None else 0):
        key = (r.get("stage"), r.get("sub"), r.get("pid"))
        if r.get("event") == "enter":
            opened[key] = _at(r)
        elif r.get("event") == "leave":
            st = stats[r.get("stage")]
            st["ran"] += 1
            if r.get("ok"): st["ok"] += 1
            t0, t1 = opened.pop(key, None), _at(r)
            # Both stamps numeric, and the leave not before its enter. A negative duration is
            # dropped rather than subtracted: a clock step would otherwise drag the busy total
            # down, which reads as a cheap stage and is the exact bias these two modules exist to
            # remove. `is not None` rather than truthiness, because epoch 0 is a real timestamp.
            # NO CASE COVERS `t1 >= t0`, AND THAT IS REPORTED RATHER THAN HIDDEN. The loop above
            # sorts on this same stamp, so a leave is always visited at or after its own enter and
            # the clause can never reject a pair: fuzzed 2026-09-21 over 20000 random ledgers of
            # None, "banana", negative, zero and positive stamps, it rejected 0. Deleting it
            # therefore SURVIVES the sweep, as an equivalent mutant rather than an untested
            # property. It is kept as the written form of the invariant, so that removing the sort
            # does not silently start subtracting. stage_log.durations() has the same guard and
            # there it IS falsifiable, because that function does not sort.
            if t0 is not None and t1 is not None and t1 >= t0:
                st["secs"] += (t1 - t0)
                # the seconds a FAILED pass really took (finding 10, 2026-09-27), never a share of the total
                if not r.get("ok"): st["lost_secs"] += (t1 - t0)
    return dict(stats)


def _cost(v):
    """A cost as a float, or None when it is UNKNOWN: null, missing, a bool, not a number, not finite or negative.
    FINDING 6, 2026-09-27: float(actual_cost or 0) turned a null or missing cost into a measured zero, uncounted and
    unwarned, and a negative one would make the night read cheaper than it was."""
    if isinstance(v, bool) or not isinstance(v, (int, float, str)):
        return None
    try:
        f = float(v)
    except ValueError:  # sbe: allow-silent a cost that is not a number is UNKNOWN, returned as None, and money() counts and warns every None
        return None
    return f if math.isfinite(f) and f >= 0 else None


def money(ledger_rows, since=None, until=None):
    """Real spend is the sum of RECONCILE actual_cost. A RESERVE is a hold, not a spend, and
    counting holds as money is how a cost report doubles. Outstanding holds are reported
    separately so an unreconciled run is visible rather than silently missing.

    ABANDONED closes a hold too (R1, independent review of RS1, 2026-09-26): the reaper uses it
    for a dead holder whose true cost is unknown, never a RELEASE (nothing spent) and never a
    RECONCILE of 0 (exactly zero spent), since a killed call may already have been billed. That
    estimate is kept in its own "abandoned" figure, added to neither "actual" (which means
    measured) nor left silently at zero: a report that folded it into either would misstate what
    is known.

    STATE FROM THE WHOLE LEDGER, THEN THE WINDOW (finding 5, 2026-09-27). since/until bound the SPEND
    and the abandonments counted; the reservation state is rebuilt from every row up to `until`. The
    report used to filter RESERVE rows by the window first, so a hold opened before the window and
    still open vanished, and an in window ABANDONED lost the estimate its earlier RESERVE carried."""
    if ledger_rows is None:
        return None
    def inside(r):
        t = _at(r)
        return (since is None or (t is not None and t >= since)) and (until is None or (t is not None and t <= until))
    upto = [r for r in ledger_rows if until is None or _at(r) is None or _at(r) <= until]
    # One unparseable cost must not take the whole report with it. Probed 2026-09-21: an
    # actual_cost of "banana" raised ValueError out of float() and killed every other section,
    # including the blockers the reader most needs. Skip the row, COUNT it, and say so, because a
    # silently dropped cost row is money that quietly leaves the total. Null and missing are the
    # same unknown (finding 6), never a zero.
    actual, calls, unreadable = 0.0, 0, 0
    for r in ledger_rows:
        if r.get("type") != "RECONCILE" or not inside(r):
            continue
        calls += 1
        c = _cost(r.get("actual_cost"))
        if c is None:
            unreadable += 1
        else:
            actual += c
    reserved = {r.get("reservation_id"): r for r in upto if r.get("type") == "RESERVE"}
    closed = {r.get("reservation_id") for r in upto if r.get("type") in ("RECONCILE", "RELEASE")}
    abandoned_all = {r.get("reservation_id") for r in upto if r.get("type") == "ABANDONED"}
    abandoned_ids = {r.get("reservation_id") for r in ledger_rows if r.get("type") == "ABANDONED" and inside(r)}
    abandoned, abandoned_unreadable = 0.0, 0
    for rid in abandoned_ids:
        # no RESERVE for it, or an estimate that is not a cost: the liability is UNKNOWN, counted and warned
        c = _cost((reserved.get(rid) or {}).get("estimated_cost"))
        if c is None:
            abandoned_unreadable += 1
        else:
            abandoned += c
    return {"actual": actual, "calls": calls,
            "outstanding": len(set(reserved) - closed - abandoned_all),
            "unreadable": unreadable, "abandoned": abandoned, "abandoned_calls": len(abandoned_ids),
            "abandoned_unreadable": abandoned_unreadable}


def fmt_hours(secs):
    return "%.2f h" % (secs / 3600.0)


RHO_CONSTRAINT = 0.7      # below this a stage is not the constraint, whatever its architecture suggests


def utilisation(stage_rows):
    """Per stage rho, borrowed from stage_log rather than recomputed here.

    THE NUMBER THAT SETTLES A BOTTLENECK ARGUMENT, and until now it was computed by stage_log and
    never carried into the report the loop actually emits when it ends. So the standard report
    could say which stage was slowest and could not say which stage was the CONSTRAINT, and those
    are different questions: this estate spent a full day optimising a stage that turned out to be
    idle five sixths of the time, on an assumed service time roughly three times the real one.

    Reusing stage_log.utilisation is deliberate. A second implementation of the same arithmetic in
    a second file is how two reports come to disagree about one night."""
    if stage_rows is None:
        return None
    try:
        sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__))))
        import stage_log
        # No filter here any more. This caller used to strip rows with a non numeric timestamp
        # because stage_log.durations() raised TypeError on them, and a caller writing the
        # callee's guard for it is the signature of a defect living one level down. stage_log now
        # honours its own stated contract of failing toward silence, so the guard was moved to the
        # source and every future caller inherits it instead of rediscovering the crash.
        return stage_log.utilisation(stage_rows)
    except Exception as exc:  # noqa: BLE001
        print("loop_report: utilisation unreadable (%r): NO-DATA" % (exc,), file=sys.stderr)
        return None       # unreadable is NO-DATA, and the caller prints that rather than a number


def run_window(log_path):
    """(start, end, label) of ONE driver run, read from the dated lines the driver writes into its own log:
    RUN START <iso> at start, RUN END <iso> at its end; end is None while the run is still going. Otherwise
    (None, None, why), always with the reason, never a bare None (2026-09-26): file times are not run times (a
    watcher appends later, a copy resets them), and before the dated lines existed the driver named logs by HH:MM
    only, so a later run at the same minute appended to an older day's log. A log with no RUN START, with more
    than one, with an unreadable stamp, or whose RUN END precedes its RUN START is NO-DATA and says which."""
    name = os.path.basename(log_path)
    starts, ends = [], []
    try:
        with open(log_path, encoding="utf-8", errors="replace") as f:
            for line in f:
                if line.startswith("RUN START "):
                    starts.append(line[len("RUN START "):].strip())
                elif line.startswith("RUN END "):
                    ends.append(line[len("RUN END "):].strip())
    except OSError as exc:
        return None, None, "%s cannot be read (%s)" % (name, type(exc).__name__)
    if not starts:
        return None, None, "%s has no RUN START line, so this run's window is unknown" % name
    if len(starts) > 1:
        return None, None, "%s holds %d runs (%d RUN START lines), so no single run window exists" % (name, len(starts), len(starts))
    try:
        s = datetime.datetime.fromisoformat(starts[0]).timestamp()
        e = datetime.datetime.fromisoformat(ends[-1]).timestamp() if ends else None
    except ValueError as exc:
        return None, None, "%s carries a RUN START or RUN END stamp that is not a date (%s)" % (name, str(exc)[:80])
    if e is not None and e < s:
        return None, None, "%s ends (%s) before it starts (%s)" % (name, ends[-1], starts[0])
    return s, e, "run %s from %s to %s" % (name, starts[0], ends[-1] if ends else "now (still running)")


def wip_section(path: Optional[str], now: float) -> Tuple[List[str], int]:
    """The finish-first limits as the report shows them: lines plus the record's own code.

    Reads the pool's JSON record directly, never importing runner_pool, and applies the same
    rules: missing or unreadable, not a JSON object, at not a finite number, code not 0, 1 or 2,
    mode not a string, rows not three-item lists of strings, future dated or stale is NO-DATA.
    A caller defect (bad path or now) raises ValueError, never a reading."""
    if path is not None and (not isinstance(path, str) or path == ""):
        raise ValueError("path must be None or a non-empty string")
    if isinstance(now, bool) or not isinstance(now, (int, float)) or not math.isfinite(now):
        raise ValueError("now must be a finite number")
    if path is None:
        path = os.path.expanduser("~/.claude/evidence/wip-status.json")
    try:
        with open(path, "r", encoding="utf-8") as fh:
            data = json.load(fh)
    except OSError as exc:
        return ["  finish-first limits: NO-DATA, cannot read the record (%s) (source: %s). Not a pass." % (type(exc).__name__, path)], 2
    except ValueError as exc:
        return ["  finish-first limits: NO-DATA, the record is not valid JSON (%s) (source: %s). Not a pass." % (type(exc).__name__, path)], 2
    if not isinstance(data, dict):
        return ["  finish-first limits: NO-DATA, the record is not a JSON object (source: %s). Not a pass." % path], 2
    at = data.get("at")
    if isinstance(at, bool) or not isinstance(at, (int, float)) or not math.isfinite(at):
        return ["  finish-first limits: NO-DATA, the record's at is not a finite number (source: %s). Not a pass." % path], 2
    code = data.get("code")
    if isinstance(code, bool) or not isinstance(code, int) or code not in (0, 1, 2):
        return ["  finish-first limits: NO-DATA, the record's code is not 0, 1 or 2 (source: %s). Not a pass." % path], 2
    mode = data.get("mode")
    if not isinstance(mode, str):
        return ["  finish-first limits: NO-DATA, the record's mode is not a string (source: %s). Not a pass." % path], 2
    rws = data.get("rows")
    if not isinstance(rws, list):
        return ["  finish-first limits: NO-DATA, the record's rows is not a list (source: %s). Not a pass." % path], 2
    for r in rws:
        if not isinstance(r, (list, tuple)) or len(r) != 3 or not all(isinstance(x, str) for x in r):
            return ["  finish-first limits: NO-DATA, the record's rows are malformed (source: %s). Not a pass." % path], 2
    if at > now + 60:
        return ["  finish-first limits: NO-DATA, the record is %g s in the future (source: %s). Not a pass." % (at - now, path)], 2
    if at < now - 1800:
        return ["  finish-first limits: NO-DATA, the record is %.0f s old (source: %s). Not a pass." % (now - at, path)], 2
    verdict = {0: "PASS", 1: "OVER", 2: "NO-DATA"}[code]
    stamp = datetime.datetime.fromtimestamp(now).strftime("%H:%M:%S")
    lines = ["  finish-first limits: %s at %s, gate %s (source: %s)" % (verdict, stamp, mode, path)]
    for r in rws:
        lines.append("    %s  %s  %s" % (r[0], r[1], r[2]))
    return lines, code


def report(since_hours: int = 24, plan_path: str = PLAN, out=sys.stdout, window=None,
           rederive=None, wip_record: Optional[str] = None) -> int:
    now = datetime.datetime.now()
    since = now.timestamp() - since_hours * 3600
    until = None
    if window is not None:
        since, until = window[0], window[1]
    p = progress(plan_path)
    st_rows, st_bad = rows(STAGES, since, until)
    fa_rows, fa_bad = rows(FAILURES, since, until)
    le_rows, le_bad = rows(LEDGER)            # the WHOLE ledger: money() rebuilds holds, then windows the spend
    if LEDGER == DEFAULT_LEDGER:              # each run's own ledger too (2026-09-27): a run spends in its own money root
        for _p in proof_ledger.run_ledger_paths():
            _r, _b = rows(_p)
            if _r is None:
                continue                      # an unreadable run ledger is skipped, never added to a None total
            if le_rows is None:
                le_rows = []
            le_rows += _r; le_bad += _b
    ys = stage_yield(st_rows)
    mo = money(le_rows, since, until)
    w = out.write

    w("BROTHER.LOOP REPORT\n")
    w("window: %s | generated %s\n" % (window[2] if window is not None else "last %d hour(s)" % since_hours,
                                         now.strftime("%Y-%m-%d %H:%M:%S %Z").strip()))
    w("every figure below names the file it was read from; nothing here is estimated\n\n")

    # ---- KEY TAKEAWAY
    w("KEY TAKEAWAY\n")
    if p is None:
        w("  NO-DATA: the plan %s could not be read, so progress is unknown. That is not zero progress.\n" % plan_path)
    else:
        w("  %d of %d units closed, %d of %d sub units landed (source: %s).\n"
          % (p["units_done"], p["units_total"], p["subs_done"], p["subs_total"], plan_path))
    if ys:
        worst = sorted(ys.items(), key=lambda kv: (kv[1]["ok"] / kv[1]["ran"]) if kv[1]["ran"] else 1.0)
        if worst:
            name, s = worst[0]
            pct = (100.0 * s["ok"] / s["ran"]) if s["ran"] else 0.0
            w("  Worst yield is the %s stage at %.0f%% (%d of %d passed), holding %s of effort (source: %s).\n"
              % (name, pct, s["ok"], s["ran"], fmt_hours(s["secs"]), STAGES))
    elif st_rows is None:
        w("  NO-DATA: %s is unreadable, so no throughput figure can be given.\n" % STAGES)

    # ---- ACTIONS
    w("\nACTIONS, each with its proof\n")
    if ys:
        w("  %-8s %6s %6s %7s %10s\n" % ("stage", "ran", "passed", "yield", "busy"))
        for name, s in sorted(ys.items()):
            pct = ("%.0f%%" % (100.0 * s["ok"] / s["ran"])) if s["ran"] else "NO-DATA"
            w("  %-8s %6d %6d %7s %10s\n" % (name, s["ran"], s["ok"], pct, fmt_hours(s["secs"])))
        u = utilisation(st_rows)
        if u is None:
            w("  NO-DATA: utilisation could not be computed, so no stage can be called the constraint.\n")
        else:
            w("\n  %-8s %8s %10s %8s  %s\n" % ("stage", "items", "mean s", "rho", "constraint"))
            for name, (n, busy, mean, rho) in sorted(u.items(), key=lambda kv: -kv[1][3]):
                w("  %-8s %8d %10.1f %8.2f  %s\n" % (name, n, mean, rho,
                  "YES, at or above %.1f" % RHO_CONSTRAINT if rho >= RHO_CONSTRAINT else "no, below %.1f" % RHO_CONSTRAINT))
            w("  rho is busy time over wall clock. A stage below %.1f is not your bottleneck however\n"
              "  serial it looks; a rho above 1 is concurrency, reported rather than clamped (source: %s)\n"
              % (RHO_CONSTRAINT, STAGES))
        # THE FAILED PAIRS' OWN SECONDS (finding 10, 2026-09-27). This was total time times the failure
        # fraction, which reported 50% discarded when one failed second sat beside 99 passed ones.
        thrown = sum(s["lost_secs"] for s in ys.values())
        total = sum(s["secs"] for s in ys.values())
        if total:
            w("  effort discarded: %s of %s (%.0f%%), source %s\n"
              % (fmt_hours(thrown), fmt_hours(total), 100.0 * thrown / total, STAGES))
    else:
        w("  NO-DATA: no stage ledger, so no action can be evidenced this window.\n")
    if mo is None:
        w("  NO-DATA: the money ledger %s is unreadable; spend is unknown, not zero.\n" % LEDGER)
    else:
        w("  spend: %.2f USD over %d reconciled call(s), %d hold(s) still open (source: %s)\n"
          % (mo["actual"], mo["calls"], mo["outstanding"], LEDGER))
        if mo.get("unreadable"):
            w("  WARNING: %d reconciled row(s) carry an unreadable cost and are MISSING from that total,\n"
              "           so the real spend is higher than the figure above (source: %s)\n" % (mo["unreadable"], LEDGER))
        if mo.get("abandoned_calls"):
            w("  UNRESOLVED: %d hold(s) closed as abandoned (a dead holder, F14b), %.2f USD of UNKNOWN cost,\n"
              "              never counted as measured spend above and never zero: it may already be billed (source: %s)\n"
              % (mo["abandoned_calls"], mo["abandoned"], LEDGER))
        if mo.get("abandoned_unreadable"):
            w("  WARNING: %d abandoned hold(s) carry no readable estimate and are MISSING from the UNRESOLVED figure,\n"
              "           so the real liability is higher than that figure (source: %s)\n" % (mo["abandoned_unreadable"], LEDGER))

    # ---- BLOCKERS and their root causes
    w("\nBLOCKERS, and the root cause behind each\n")
    if fa_rows is None:
        w("  NO-DATA: %s is unreadable, so the failure classes are unknown.\n" % FAILURES)
    elif not fa_rows:
        w("  No failure was recorded in this window. That is only as true as the recorder;\n")
        w("  a stage that never writes a failure row also prints this line.\n")
    else:
        counts = collections.Counter(r.get("class") or "unclassified" for r in fa_rows)
        for cls, n in counts.most_common(6):
            ex = next((r.get("detail") or "" for r in fa_rows if (r.get("class") or "unclassified") == cls), "")
            w("  %-22s %3d  %s\n" % (cls, n, ex[:96]))
        un = counts.get("unclassified", 0)
        if un:
            w("  %d unclassified: no brief can teach a lesson nobody has named. Classify these first.\n" % un)
    for label, bad, path in (("stages", st_bad, STAGES), ("failures", fa_bad, FAILURES), ("ledger", le_bad, LEDGER)):
        if bad:
            w("  WARNING: %d corrupt line(s) skipped in %s (%s), usually a crash mid write\n" % (bad, label, path))
    wip_lines, wip_code = wip_section(wip_record, now.timestamp())
    for _wl in wip_lines:
        w(_wl + "\n")
    _wip_src = wip_record if wip_record is not None else os.path.expanduser("~/.claude/evidence/wip-status.json")
    _wip_over = []
    for _wl in wip_lines:
        if _wl.startswith("    "):
            _sp = _wl.strip().split(None, 2)
            if len(_sp) >= 2 and _sp[1] == "OVER":
                _wip_over.append(_sp[0])

    # ---- NEXT STEPS, ranked, per the advisor law
    w("\nNEXT STEPS, ranked, first one first\n")
    steps = []
    if ys:
        for name, s in ys.items():
            if s["ran"] >= 5 and s["ok"] / s["ran"] < 0.5:
                steps.append((s["secs"], "Raise %s yield, now %d of %d. It holds %s of effort, so a point of "
                                         "yield here is worth more than any scheduling change."
                              % (name, s["ok"], s["ran"], fmt_hours(s["secs"]))))
    if fa_rows:
        c = collections.Counter(r.get("class") or "unclassified" for r in fa_rows)
        if c:
            cls, n = c.most_common(1)[0]
            steps.append((n * 600.0, "Teach the brief about %s, the largest failure class at %d occurrence(s)." % (cls, n)))
    if wip_code == 1 and _wip_over:
        steps.append((float("inf"), "Finish the oldest work before starting new: %s over the limit (source: %s)." % (", ".join(_wip_over), _wip_src)))
    for _, text in sorted(steps, reverse=True):
        w("  - %s\n" % text)
    if not steps:
        w("  Nothing in the ledgers ranks as a next step this window. Say so rather than inventing one.\n")

    # ---- FACILITATORS
    w("\nFACILITATORS, one command each\n")
    w("  loop health now:      python3 ~/.claude/bin/loop_heartbeat.py check\n")
    w("  re-derive this report: python3 scripts/loop/loop_report.py %s\n" % (rederive or "--since-hours %d" % since_hours))
    w("  what is ready to land: python3 ~/.claude/bin/pass_digest.py\n")
    # AN UNKNOWN COST IS NEVER A CLEAN EXIT (finding 6, 2026-09-27): exit 3 means the report was written and a cost
    # in it is unknown, so a caller reading only the exit code cannot take the spend line for the whole truth. A money
    # row that could not be read at all (corrupt, or refused by the strict parser) is an unknown cost too (X1 finding 4).
    return 3 if mo and (mo["unreadable"] or mo["abandoned_unreadable"] or le_bad) else 0


def _no_stage_log_case():
    """utilisation() answers NO-DATA, never a number, when stage_log cannot be reached.

    The existing case fed utilisation(None), which returns on the first line and never reaches the
    except branch at all, so turning `return None` into `return {}` SURVIVED a sweep on 2026-09-21
    with the selftest printing 14 cases OK. An empty dict is the one shape that lies here: report()
    tests `if u is None` to print NO-DATA, so `{}` prints an EMPTY CONSTRAINT TABLE instead, and a
    reader sees a stage list with nothing in it where the truth is that the arithmetic never ran.
    No landings and an unreadable ledger are opposite facts; so are no stages and no reader."""
    class Broken(object):
        @staticmethod
        def utilisation(rows, wall=None):
            raise RuntimeError("stage_log unavailable")
    keep = sys.modules.get("stage_log")
    sys.modules["stage_log"] = Broken
    try:
        return utilisation([{"at": 0, "stage": "grade", "event": "enter", "sub": "A", "pid": 1}]) is None
    finally:
        if keep is None:
            sys.modules.pop("stage_log", None)
        else:
            sys.modules["stage_log"] = keep


def _agreement_case():
    """This report and stage_log must print the SAME rho for the same window.

    loop_report.utilisation borrows stage_log.utilisation rather than recomputing it, deliberately:
    a second implementation of the same arithmetic in a second file is how two reports come to
    disagree about one night. Nothing asserted the agreement, so this case states it directly, and
    it goes red the moment either tool grows its own copy of the formula."""
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    import stage_log
    rows_ = [{"at": 0, "stage": "model", "event": "enter", "sub": "A", "pid": 1},
             {"at": 0, "stage": "model", "event": "enter", "sub": "B", "pid": 2},
             {"at": 100, "stage": "model", "event": "leave", "sub": "A", "pid": 1, "ok": True},
             {"at": 100, "stage": "model", "event": "leave", "sub": "B", "pid": 2, "ok": True}]
    return utilisation(rows_) == stage_log.utilisation(rows_) != {}


def _constraint_bar_case():
    """report() labels a stage at or above rho 0.7 as the constraint, from real ledger files.

    RHO_CONSTRAINT could be raised to 99.0 and every case stayed green, because nothing ran
    report() over a ledger with a busy stage in it: the bar was stored and never applied. The
    globals are pointed at temp files for the length of the call, which is also the only way this
    module's top level report is exercised end to end."""
    import io, tempfile, time
    global STAGES, FAILURES, LEDGER
    d = tempfile.mkdtemp()
    t = time.time()
    sp = os.path.join(d, "stages.jsonl")
    with open(sp, "w", encoding="utf-8") as f:
        f.write(json.dumps({"at": t - 100, "stage": "model", "event": "enter", "sub": "A", "pid": 1}) + "\n")
        f.write(json.dumps({"at": t - 20, "stage": "model", "event": "leave", "sub": "A", "pid": 1, "ok": True}) + "\n")
    keep = (STAGES, FAILURES, LEDGER)
    STAGES, FAILURES, LEDGER = sp, os.path.join(d, "f.jsonl"), os.path.join(d, "l.jsonl")
    try:
        buf = io.StringIO()
        report(since_hours=1, plan_path=os.path.join(d, "missing.json"), out=buf)
        return "YES, at or above 0.7" in buf.getvalue()
    finally:
        STAGES, FAILURES, LEDGER = keep


def _blockers_case(empty_ledger):
    """The BLOCKERS section says which of two OPPOSITE facts it is looking at.

    No failure recorded and an unreadable failure ledger are exactly the pair this module's rule 2
    exists for, and nothing ran report() over either. A sweep on 2026-09-21 neutered the
    `elif not fa_rows` branch and every case stayed green, which would have left BLOCKERS silently
    EMPTY on a clean night: a reader cannot tell a quiet night from a broken recorder by looking at
    nothing. Called twice, once per fact, so neither branch can answer for the other."""
    import io, tempfile
    global STAGES, FAILURES, LEDGER
    d = tempfile.mkdtemp()
    fp = os.path.join(d, "failures.jsonl")
    if empty_ledger:
        open(fp, "w", encoding="utf-8").close()      # readable and empty: a genuinely clean window
    keep = (STAGES, FAILURES, LEDGER)
    STAGES, FAILURES, LEDGER = os.path.join(d, "s.jsonl"), fp, os.path.join(d, "l.jsonl")
    try:
        buf = io.StringIO()
        report(since_hours=1, plan_path=os.path.join(d, "missing.json"), out=buf)
        return buf.getvalue()
    finally:
        STAGES, FAILURES, LEDGER = keep


def selftest():
    """A selftest that RAISES has not reported a verdict, it has abandoned the question.

    Measured 2026-09-21 by an external verdict checker: breaking the NO-DATA guard made this
    function die with a TypeError partway through building its case list, so it exited non zero
    with a traceback and no FAILED line. A pipeline reading the exit code and a human reading the
    text would then describe the same run differently, and the human would get a stack trace where
    a verdict belongs.

    The cases below are evaluated EAGERLY as they are appended, which is why one bad expression
    takes the whole run with it. Rather than rewrite every case into a lambda, the body is wrapped:
    anything that escapes is reported as a FAILED case naming the exception, and the exit code
    still says 1. The verdict and the code agree in every direction."""
    try:
        return _selftest_body()
    except Exception as exc:
        print("selftest: FAILED before it could finish, %s: %s"
              % (type(exc).__name__, str(exc)[:120]))
        return 1


def _selftest_body():
    import io, tempfile
    d = tempfile.mkdtemp()
    empty = os.path.join(d, "none.jsonl")
    cases = []
    # Unreadable stage rows give None AND say so (lint 2026-09-22): NO-DATA is printed, never a silent None.
    import io
    class _Boom(object):
        def __iter__(self):
            raise RuntimeError("boom")
        def __len__(self):
            raise RuntimeError("boom")
    _err, _real_err = io.StringIO(), sys.stderr
    sys.stderr = _err
    try:
        _u = utilisation(_Boom())
    finally:
        sys.stderr = _real_err
    cases.append(("unreadable stage rows give None and name NO-DATA on stderr", _u is None and "NO-DATA" in _err.getvalue()))
    r, bad = rows(empty)
    cases.append(("a missing ledger is None, never an empty list", r is None))
    p = os.path.join(d, "x.jsonl")
    open(p, "w").write('{"a":1}\nNOT JSON\n{"a":2}\n')
    r, bad = rows(p)
    cases.append(("corrupt lines are skipped and counted", len(r) == 2 and bad == 1))
    cases.append(("unreadable stages give no yield", stage_yield(None) is None))
    sr = [{"at": 100, "stage": "grade", "event": "enter", "sub": "A", "pid": 1},
          {"at": 160, "stage": "grade", "event": "leave", "sub": "A", "pid": 1, "ok": False},
          {"at": 200, "stage": "grade", "event": "enter", "sub": "B", "pid": 1},
          {"at": 260, "stage": "grade", "event": "leave", "sub": "B", "pid": 1, "ok": True}]
    y = stage_yield(sr)
    cases.append(("yield counts pairs and seconds", y["grade"]["ran"] == 2 and y["grade"]["ok"] == 1 and abs(y["grade"]["secs"] - 120) < 1e-6))
    cases.append(("unreadable ledger gives no money", money(None) is None))
    cases.append(("unreadable stages give no utilisation", utilisation(None) is None))
    ur = utilisation([{"at": 0, "stage": "grade", "event": "enter", "sub": "A", "pid": 1},
                      {"at": 100, "stage": "grade", "event": "leave", "sub": "A", "pid": 1, "ok": True},
                      {"at": 200, "stage": "grade", "event": "enter", "sub": "B", "pid": 1},
                      {"at": 240, "stage": "grade", "event": "leave", "sub": "B", "pid": 1, "ok": True}])
    cases.append(("rho is busy over wall clock", ur is not None and abs(ur["grade"][3] - (140.0 / 240.0)) < 1e-6))
    cases.append(("a non numeric timestamp does not crash utilisation",
                  utilisation([{"at": "banana", "stage": "grade", "event": "enter", "sub": "A", "pid": 1}]) is not None))
    m = money([{"type": "RESERVE", "reservation_id": "r1", "estimated_cost": 9.0},
               {"type": "RECONCILE", "reservation_id": "r1", "actual_cost": 2.0},
               {"type": "RESERVE", "reservation_id": "r2", "estimated_cost": 5.0}])
    cases.append(("a hold is not a spend", abs(m["actual"] - 2.0) < 1e-9))
    cases.append(("an open hold is reported", m["outstanding"] == 1))
    mb = money([{"type": "RECONCILE", "reservation_id": "a", "actual_cost": "banana"},
                {"type": "RECONCILE", "reservation_id": "b", "actual_cost": 3.0}])
    cases.append(("an unreadable cost does not crash the report", mb is not None))
    cases.append(("an unreadable cost is excluded and counted", abs(mb["actual"] - 3.0) < 1e-9 and mb["unreadable"] == 1))
    # ONLY A RECONCILE IS A CALL. Deleting the RECONCILE-only filter SURVIVED, because the RESERVE
    # rows in the fixture above carry estimated_cost and no actual_cost, so admitting them added
    # 0.0 and the "a hold is not a spend" case never moved. A fixture that can be satisfied by two
    # different guards proves neither, so the count is asserted separately from the total.
    cases.append(("only a reconciled row counts as a call", m["calls"] == 1))
    # AN ESTIMATE IS NOT MONEY, even on a reconciled row. A call reserved at 99 and reconciled at
    # zero is the ordinary shape of a cached or refused call, and falling back to the estimate when
    # the actual is 0.0 is how a cost report invents spend that never happened.
    mz = money([{"type": "RECONCILE", "reservation_id": "z", "actual_cost": 0.0, "estimated_cost": 99.0}])
    cases.append(("a reconciled row's estimate is never counted as money", abs(mz["actual"]) < 1e-9))
    # A RELEASE CLOSES A HOLD. 153 RELEASE rows sit in the live ledger and not one fixture had one,
    # so dropping RELEASE from the closed set SURVIVED and every released hold would have been
    # reported as still outstanding, which reads as an unreconciled run that does not exist.
    mr = money([{"type": "RESERVE", "reservation_id": "r3", "estimated_cost": 4.0},
                {"type": "RELEASE", "reservation_id": "r3"}])
    cases.append(("a released hold is closed, not left outstanding", mr["outstanding"] == 0))
    # R1 (independent review of RS1, 2026-09-26): ABANDONED is a real closer too. The reaper closes a
    # dead holder's reservation this way, never as a RELEASE (nothing spent) or a RECONCILE of 0
    # (exactly zero spent), because the true cost is unknown and may already have been billed.
    ma = money([{"type": "RESERVE", "reservation_id": "a1", "estimated_cost": 9.5},
                {"type": "ABANDONED", "reservation_id": "a1"}])
    cases.append(("an abandoned hold is closed, not left outstanding", ma["outstanding"] == 0))
    cases.append(("an abandoned estimate is never labelled as measured spend", abs(ma["actual"]) < 1e-9))
    cases.append(("an abandoned estimate is kept as its own unknown figure, never zero",
                  abs(ma["abandoned"] - 9.5) < 1e-9 and ma["abandoned_calls"] == 1))
    # A KNOWN PAYMENT AND AN UNKNOWN LIABILITY ARE BOTH REAL. One holder's reconciled call must not
    # make a SEPARATE holder's abandoned hold invisible, and the two figures must never be summed
    # into one number that hides which part is measured and which is a guess.
    mm = money([{"type": "RESERVE", "reservation_id": "known", "estimated_cost": 0.2},
                {"type": "RECONCILE", "reservation_id": "known", "actual_cost": 0.2},
                {"type": "RESERVE", "reservation_id": "unknown", "estimated_cost": 9.5},
                {"type": "ABANDONED", "reservation_id": "unknown"}])
    cases.append(("a measured call and an abandoned hold are both visible, never merged",
                  abs(mm["actual"] - 0.2) < 1e-9 and abs(mm["abandoned"] - 9.5) < 1e-9 and mm["outstanding"] == 0))
    # THE WINDOW IS THE REPORT. Every figure here is scoped to --since-hours, and nothing tested
    # that scoping: deleting the since filter SURVIVED, which would silently widen every number in
    # the report to the whole history of the ledger while the header still claimed 24 hours.
    wp = os.path.join(d, "window.jsonl")
    open(wp, "w").write('{"at": 100, "id": "old"}\n{"at": 1000, "id": "new"}\n')
    wr, _ = rows(wp, since=500)
    cases.append(("a row older than the window is excluded", [r["id"] for r in wr] == ["new"]))
    # PAIRED BY PID, not by stage and sub alone. Two processes can hold the same stage and sub unit
    # at once (that is what the lanes do), and pairing without the pid crosses one process's enter
    # with another's leave, which invents a duration nobody served. Every fixture used pid 1.
    yp = stage_yield([{"at": 0, "stage": "model", "event": "enter", "sub": "A", "pid": 1},
                      {"at": 10, "stage": "model", "event": "enter", "sub": "A", "pid": 2},
                      {"at": 20, "stage": "model", "event": "leave", "sub": "A", "pid": 2, "ok": True},
                      {"at": 100, "stage": "model", "event": "leave", "sub": "A", "pid": 1, "ok": True}])
    cases.append(("two processes in one stage and sub are paired by pid", abs(yp["model"]["secs"] - 110.0) < 1e-6))
    cases.append(("and the wrong pairing would give a different answer, so the case can fail",
                  abs(yp["model"]["secs"] - 30.0) > 1e-6))
    # SORTED BEFORE PAIRED. This ledger is appended by many processes, so a leave can land in the
    # file ahead of its own enter. Removing the sort SURVIVED because every fixture was already in
    # order; unsorted, the leave arrives first, finds nothing open and the pair contributes zero
    # seconds, which reads as a free stage.
    yo = stage_yield([{"at": 260, "stage": "grade", "event": "leave", "sub": "A", "pid": 1, "ok": True},
                      {"at": 200, "stage": "grade", "event": "enter", "sub": "A", "pid": 1}])
    cases.append(("rows arriving out of file order are paired by time", abs(yo["grade"]["secs"] - 60.0) < 1e-6))
    # A LEAVE WITH NO ENTER has no service time. It still ran, so it counts toward ran and ok, but
    # inventing a duration for it is the bias this pair of modules exists to avoid.
    yl = stage_yield([{"at": 260, "stage": "land", "event": "leave", "sub": "Z", "pid": 1, "ok": True}])
    cases.append(("a leave with no enter counts as run but adds no seconds",
                  yl["land"]["ran"] == 1 and abs(yl["land"]["secs"]) < 1e-9))
    # AN ENTER STAMPED AT EPOCH 0 IS AN ENTER. `if t0` read it as no enter and threw the pair away.
    # Caught here only because the pid fixture above happened to start at 0; nothing was testing it.
    yz = stage_yield([{"at": 0, "stage": "close", "event": "enter", "sub": "A", "pid": 1},
                      {"at": 100, "stage": "close", "event": "leave", "sub": "A", "pid": 1, "ok": True}])
    cases.append(("an enter at epoch 0 is a real enter, not a missing one", abs(yz["close"]["secs"] - 100.0) < 1e-6))
    # ONE BAD BYTE MUST NOT TAKE THE REPORT WITH IT. A ledger row with a non numeric `at` raised
    # TypeError out of the SORT itself, before any guard ran, so the blockers and money sections
    # died too. The good pair beside it must still be measured, which is what makes this case fail
    # for the right reason rather than merely not raising.
    yb = stage_yield([{"at": "banana", "stage": "brief", "event": "enter", "sub": "X", "pid": 9},
                      {"at": 10, "stage": "brief", "event": "enter", "sub": "A", "pid": 1},
                      {"at": 70, "stage": "brief", "event": "leave", "sub": "A", "pid": 1, "ok": True}])
    cases.append(("a non numeric timestamp is skipped, never raised out of the sort",
                  abs(yb["brief"]["secs"] - 60.0) < 1e-6))
    cases.append(("an unreachable stage_log is NO-DATA, never an empty table", _no_stage_log_case()))
    cases.append(("this report and stage_log print the same rho for one window", _agreement_case()))
    cases.append(("report() applies the 0.7 constraint bar it documents", _constraint_bar_case()))
    quiet = _blockers_case(empty_ledger=True)
    broken = _blockers_case(empty_ledger=False)
    cases.append(("a readable but empty failure ledger SAYS no failure was recorded",
                  "No failure was recorded in this window." in quiet))
    cases.append(("an unreadable failure ledger says NO-DATA instead, never the same line",
                  "the failure classes are unknown" in broken
                  and "No failure was recorded in this window." not in broken))
    buf = io.StringIO()
    report(since_hours=1, plan_path=os.path.join(d, "missing.json"), out=buf)
    text = buf.getvalue()
    # THE PRECEDENCE BUG, confirmed by reading and then by mutation on 2026-09-21. This line was
    #   "NO-DATA" in text and "not zero progress" in text.lower() or "That is not zero progress." in text
    # which Python parses as (A and B) or C, not the A and (B or C) the author plainly meant. C
    # alone satisfied it, so the NO-DATA marker could be deleted from the plan line and the case
    # stayed green. It also searched the WHOLE report, where a NO-DATA from any other section
    # answered for the plan. Both halves now have to hold ON THE PLAN'S OWN LINE.
    plan_line = next((l for l in text.splitlines() if "could not be read" in l), "")
    cases.append(("a missing plan prints NO-DATA on its own line, never zero",
                  "NO-DATA" in plan_line and "That is not zero progress." in plan_line))
    cases.append(("every section header is present", all(h in text for h in
                  ("KEY TAKEAWAY", "ACTIONS", "BLOCKERS", "NEXT STEPS", "FACILITATORS"))))
    bad = [n for n, ok in cases if not ok]
    print("selftest: %d cases, %s" % (len(cases), "OK" if not bad else "FAILED: " + ", ".join(bad)))
    return 1 if bad else 0


KNOWN_FLAGS = ("--selftest", "--since-hours", "--plan", "--run")


def main(argv=None):
    argv = sys.argv if argv is None else argv
    # AN UNKNOWN FLAG IS REFUSED, NEVER DROPPED (2026-09-26): --run was ignored for days and every proof run's done
    # check silently printed the last 24 hours instead of the run it named.
    unknown = [a for a in argv[1:] if a.startswith("--") and a not in KNOWN_FLAGS]
    if unknown:
        print("loop_report: unknown flag(s) %s; the flags are %s" % (", ".join(unknown), ", ".join(KNOWN_FLAGS))); return 2
    sys.argv = argv
    if "--selftest" in sys.argv:
        return selftest()
    hours = 24
    if "--since-hours" in sys.argv:
        try: hours = int(sys.argv[sys.argv.index("--since-hours") + 1])
        except (IndexError, ValueError): print("--since-hours needs a number"); return 2
    plan = PLAN
    if "--plan" in sys.argv:
        try: plan = sys.argv[sys.argv.index("--plan") + 1]
        except IndexError: print("--plan needs a path"); return 2
    if "--run" in sys.argv:
        if "--since-hours" in sys.argv:
            print("--run and --since-hours name two different windows; pass one"); return 2
        try: log = sys.argv[sys.argv.index("--run") + 1]
        except IndexError: print("--run needs a driver log path"); return 2
        win = run_window(log)
        if win[0] is None:
            print("NO-DATA: %s; no figure is printed rather than one from the wrong window" % win[2]); return 2
        return report(hours, plan, window=win, rederive="--run %s" % log)
    return report(hours, plan)


if __name__ == "__main__":
    sys.exit(main())

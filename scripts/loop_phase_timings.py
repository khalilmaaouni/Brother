#!/usr/bin/env python3
"""PH1.a (docs/plan/specs/PH1.md): one row per phase of a loop night, read from the logs the tools already write.

Nothing is instrumented (PLAN-DECISIONS D22): this reads the existing line shapes only.

  start_run.sh      TIME <step> <seconds>, TIME NO-DATA <step>: <why>, STARTED: watch with: tail -f <driver log>
  loop_until.sh     LOOP UNTIL <HH:MM> | gap <N>s | log <path>, <HH:MM:SS> <text>, RUN END <YYYY-MM-DDTHH:MM:SS>,
                    LOOP <STATE> at <YYYY-MM-DD HH:MM:SS TZ> (the alarm and history), LOOP FINISHED: <why> (loop_pass.sh)
  pass_pulse.py     PULSE pass <n> | <hours> h | ...
  land_batch.py     LANDED  <sub units> | ...
  stop_loop.sh      STOPPED: ..., STOP INCOMPLETE: ..., DRAINED: ... after <N>s ..., DRAIN INCOMPLETE after <N>s, NO-DATA: ...
  gate_pair.sh      GATE py<version> rc=<n> ...
  redeploy.sh       REDEPLOY DONE: ...
  cut_driver.py     PHASE <name> <verdict> <N>s <detail> (phase_line, cut_driver.py line 191)

Rows {run, phase, started, ended, seconds, source, reason}: launch (the TIME lines of one start summed), run (LOOP UNTIL
to its end), land (each gap to a LANDED line), close (stop_loop output), cut <name> (each PHASE line). An unreadable
source, an empty log, a NO-DATA line or a non numeric field gives seconds null with the reason, never 0. A clock that
steps back clamps at 0 and says so in the reason. Whole lines only: a last line with no newline is torn and dropped.

  python3 -B scripts/loop_phase_timings.py (--run DIR | --out DIR) LOG [LOG ...] [--summary]
exits 0, 2 on a usage error, 3 when no source could be read.

Python 3.9, standard library only. No network.
"""
import argparse
import calendar
import datetime
import json
import math
import os
import re
import sys

LEDGER = "phase-timings.jsonl"
ROW_KEYS = ("run", "phase", "started", "ended", "seconds", "source", "reason")
MAX_LINE = 1 << 16             # a longer line is no shape this reader knows; it is skipped, never searched
MAX_READ = 64 << 20            # bytes read from one log at most
HALF_DAY = 43200

FULL_STAMP = re.compile(r"(\d{4})-(\d{2})-(\d{2})[T ](\d{2}):(\d{2}):(\d{2})(?!\d)")
CLOCK_PREFIX = re.compile(r"^(\d{2}):(\d{2}):(\d{2}) ")
TIME_NODATA = re.compile(r"^TIME NO-DATA(?: ([A-Za-z0-9_]+))?:\s*(.*)$")
TIME_LINE = re.compile(r"^TIME ([A-Za-z0-9_]+) (\S+)$")
NUMBER = re.compile(r"^-?[0-9]+(?:\.[0-9]+)?$")
STARTED_LINE = re.compile(r"^STARTED: watch with: tail -f (\S+)")
UNTIL_LINE = re.compile(r"^LOOP UNTIL (\S+(?: \S+)?) \| gap \S+ \| log (\S+)")
END_AT = re.compile(r"^LOOP (DEADLINE|FINISHED) at ")
END_COLON = re.compile(r"^LOOP (FINISHED):")
RUN_END = re.compile(r"^RUN END ")
PULSE_LINE = re.compile(r"^PULSE pass (\d+) \| ([0-9]+(?:\.[0-9]+)?) h \|")
PULSE_NODATA = re.compile(r"^PULSE NO-DATA:")
LANDED_LINE = re.compile(r"^LANDED  ?(\S.*)$")
DRAIN_AFTER = re.compile(r"^(DRAINED): no runner is alive after ([0-9]+)s|^(DRAIN INCOMPLETE) after ([0-9]+)s")
STOP_LINE = re.compile(r"^(STOPPED|STOP INCOMPLETE):")
STOP_NODATA = re.compile(r"^NO-DATA: (.*)$")
GATE_LINE = re.compile(r"^GATE py(\S+) rc=(-?[0-9]+)")
REDEPLOY_LINE = re.compile(r"^REDEPLOY DONE:")
PHASE_LINE = re.compile(r"^PHASE (\S+) (\S+) (-?[0-9]+)s ?(.*)$")


class TimingsError(Exception):
    """A ledger that cannot be written."""


def _epoch(m):
    try:
        return calendar.timegm(datetime.datetime(*[int(g) for g in m.groups()]).timetuple())
    except (ValueError, OverflowError):
        return None


def _iso(epoch):
    if epoch is None:
        return None
    try:
        return (datetime.datetime(1970, 1, 1) + datetime.timedelta(seconds=epoch)).strftime("%Y-%m-%dT%H:%M:%S")
    except (ValueError, OverflowError):
        return None


def _clocks(texts):
    """Each line's own stamp as an epoch or None; HH:MM:SS lines take their day from the nearest dated line."""
    full, tod = [None] * len(texts), [None] * len(texts)
    for i, t in enumerate(texts):
        m = FULL_STAMP.search(t)
        if m:
            full[i] = _epoch(m)
        if full[i] is None:
            c = CLOCK_PREFIX.match(t)
            if c and int(c.group(1)) < 24 and int(c.group(2)) < 60 and int(c.group(3)) < 60:
                tod[i] = int(c.group(1)) * 3600 + int(c.group(2)) * 60 + int(c.group(3))
    out = list(full)
    last = None
    for i in range(len(texts)):
        if full[i] is not None:
            last = full[i]
        elif tod[i] is not None and last is not None:
            cand = last - last % 86400 + tod[i]
            if cand < last - HALF_DAY:
                cand += 86400          # past midnight
            out[i] = last = cand
    anchor = next((i for i in range(len(texts)) if full[i] is not None), None)
    if anchor is not None:
        nxt = full[anchor]
        for i in range(anchor - 1, -1, -1):
            if tod[i] is not None:
                cand = nxt - nxt % 86400 + tod[i]
                if cand > nxt + HALF_DAY:
                    cand -= 86400      # the evening before the first dated line
                out[i] = nxt = cand
    return out


def _run_name(path):
    base = os.path.basename(path.rstrip("/")) or path
    return base[:-4] if base.endswith(".log") else base


def _number(text):
    if not NUMBER.match(text):
        return None
    v = float(text)
    return v if math.isfinite(v) else None


def parse_lines(lines, source):
    """Events {kind, stamp, seconds, source, line, ...} for the known shapes; any other line is ignored."""
    if not isinstance(lines, list):
        raise ValueError("lines must be a list of str")
    if not isinstance(source, str) or not source:
        raise ValueError("source must be a non empty str")
    for x in lines:
        if not isinstance(x, str):
            raise ValueError("every line must be a str")
    whole = lines[:-1] if lines and not lines[-1].endswith("\n") else lines   # a torn last line is dropped
    texts = [x.rstrip("\r\n") if len(x) <= MAX_LINE else "" for x in whole]
    clocks = _clocks(texts)
    events = []
    for n, t in enumerate(texts):
        ev = {"kind": None, "stamp": clocks[n], "seconds": None, "source": source, "line": n + 1}
        m = TIME_NODATA.match(t)
        if m:
            ev.update(kind="time", step=m.group(1), reason="TIME NO-DATA: %s" % m.group(2)[:200])
        elif TIME_LINE.match(t):
            m = TIME_LINE.match(t)
            ev.update(kind="time", step=m.group(1), seconds=_number(m.group(2)))
            if ev["seconds"] is None:
                ev["reason"] = "TIME %s seconds field %r is not a number" % (m.group(1), m.group(2)[:40])
        elif STARTED_LINE.match(t):
            ev.update(kind="started", run=_run_name(STARTED_LINE.match(t).group(1)))
        elif UNTIL_LINE.match(t):
            ev.update(kind="until", run=_run_name(UNTIL_LINE.match(t).group(2)))
        elif END_AT.match(t) or END_COLON.match(t):
            ev.update(kind="end", state=(END_AT.match(t) or END_COLON.match(t)).group(1))
        elif RUN_END.match(t):
            ev.update(kind="end", state="RUN END")
        elif PULSE_LINE.match(t):
            ev.update(kind="pulse", seconds=float(PULSE_LINE.match(t).group(2)) * 3600)
        elif PULSE_NODATA.match(t):
            ev.update(kind="pulse", reason=t[:200])
        elif LANDED_LINE.match(t):
            ev.update(kind="landed", units=LANDED_LINE.match(t).group(1).split(" | ")[0][:200])
        elif DRAIN_AFTER.match(t):
            m = DRAIN_AFTER.match(t)
            ev.update(kind="stop", verdict=m.group(1) or m.group(3), seconds=float(m.group(2) or m.group(4)))
        elif STOP_LINE.match(t):
            ev.update(kind="stop", verdict=STOP_LINE.match(t).group(1),
                      reason="stop_loop printed %s with no duration" % STOP_LINE.match(t).group(1))
        elif STOP_NODATA.match(t):
            ev.update(kind="stop", verdict="NO-DATA", reason="stop_loop NO-DATA: %s" % STOP_NODATA.match(t).group(1)[:200])
        elif GATE_LINE.match(t):
            ev.update(kind="gate", python=GATE_LINE.match(t).group(1), rc=int(GATE_LINE.match(t).group(2)))
        elif REDEPLOY_LINE.match(t):
            ev.update(kind="redeploy")
        elif PHASE_LINE.match(t):
            m = PHASE_LINE.match(t)
            ev.update(kind="phase", name=m.group(1), verdict=m.group(2), detail=m.group(4)[:200])
            if m.group(2) == "NO-DATA":                      # cut_driver writes NO-DATA phases as 0s: that 0 is no time
                ev["reason"] = "cut phase %s NO-DATA: %s" % (m.group(1), m.group(4)[:200])
            else:
                ev["seconds"] = float(m.group(3))
        elif clocks[n] is not None:
            ev.update(kind="clock")
        else:
            continue
        events.append(ev)
    return events


def _row(run, phase, started, ended, seconds, source, reason):
    return {"run": run, "phase": phase, "started": _iso(started), "ended": _iso(ended),
            "seconds": seconds, "source": source, "reason": reason}


def _gap(a, b, what):
    """Seconds from stamp a to stamp b, clamped at 0 with the reason, or None with the reason."""
    if a is None or b is None:
        return None, "%s: a stamp is missing, so the duration cannot be read" % what
    if b < a:
        return 0, "%s: the clock stepped back %d s; clamped at 0" % (what, a - b)
    return b - a, None


def _source_rows(events, source):
    rows, stem = [], _run_name(source)
    launch, run, last_stamp, mark = [], None, None, None   # run: [name, start, until index]
    for i, ev in enumerate(events):
        kind = ev["kind"]
        own = ev["stamp"]
        if kind == "time":
            launch.append(ev)
        elif kind in ("started", "until"):
            if launch:
                rows.append(_launch_row(launch, ev["run"], source))
                launch = []
            if kind == "until":
                if run is not None:
                    rows.append(_row(run[0], "run", run[1], None, None, source,
                                     "a second LOOP UNTIL came before this run's LOOP DEADLINE or LOOP FINISHED"))
                start = own
                if start is None:
                    start = next((e["stamp"] for e in events[i + 1:] if e["stamp"] is not None), None)
                run, mark = [ev["run"], start], start
        elif kind == "end" and run is not None:
            secs, why = _gap(run[1], own, "run")
            rows.append(_row(run[0], "run", run[1], own, secs, source, why))
            run, mark = None, None
        elif kind == "landed":
            at = own if own is not None else last_stamp
            secs, why = _gap(mark, at, "land %s" % ev["units"])
            rows.append(_row(run[0] if run else stem, "land", mark, at, secs, source, why))
            mark = at
        elif kind == "stop":
            rows.append(_row(run[0] if run else stem, "close", None, own, ev["seconds"], source, ev.get("reason")))
        elif kind == "phase":
            secs, why = ev["seconds"], ev.get("reason")
            if secs is not None and secs < 0:
                secs, why = 0, "cut phase %s: a negative duration %d s; clamped at 0" % (ev["name"], ev["seconds"])
            rows.append(_row(run[0] if run else stem, "cut %s" % ev["name"], None, own, secs, source, why))
        if own is not None:
            last_stamp = own
    if launch:
        rows.append(_launch_row(launch, stem, source))
    if run is not None:
        rows.append(_row(run[0], "run", run[1], None, None, source, "no LOOP DEADLINE or LOOP FINISHED yet"))
    return rows


def _launch_row(times, run, source):
    total, why = 0, []
    for ev in times:
        if ev["seconds"] is None:
            why.append(ev.get("reason") or "a TIME line without seconds")
        elif ev["seconds"] < 0:
            why.append("TIME %s: the clock stepped back %g s; clamped at 0" % (ev["step"], -ev["seconds"]))
        else:
            total += ev["seconds"]
    stamps = [e["stamp"] for e in times if e["stamp"] is not None]
    unreadable = any(ev["seconds"] is None for ev in times)
    return _row(run, "launch", stamps[0] if stamps else None, stamps[-1] if stamps else None,
                None if unreadable else total, source, "; ".join(why) or None)


def phases(sources):
    """Rows for {source path: list of lines read, or the str reason it could not be read, or None}."""
    if not isinstance(sources, dict):
        raise ValueError("sources must be a dict of path to lines")
    for k, v in sources.items():
        if not isinstance(k, str) or not k:
            raise ValueError("every source must be named by a non empty str")
        if v is not None and not isinstance(v, (list, str)):
            raise ValueError("source %s must be a list of lines or the reason it could not be read" % k)
    rows = []
    for path, value in sources.items():
        if not isinstance(value, list):
            rows.append(_row(_run_name(path), None, None, None, None, path,
                             "unreadable: %s" % (value or "no reason given")))
            continue
        got = _source_rows(parse_lines(value, path), path)
        if not got:
            got = [_row(_run_name(path), None, None, None, None, path,
                        "the log is empty" if not value else "no line of a known shape")]
        rows.extend(got)
    return rows


def _check_rows(rows):
    if not isinstance(rows, list):
        raise ValueError("rows must be a list")
    for r in rows:
        if not isinstance(r, dict) or any(k not in r for k in ROW_KEYS):
            raise ValueError("every row must be a dict with %s" % ", ".join(ROW_KEYS))
        s = r["seconds"]
        if s is not None and (isinstance(s, bool) or not isinstance(s, (int, float)) or not math.isfinite(s) or s < 0):
            raise ValueError("a row's seconds must be null or a finite number at or above 0")


def write_ledger(rows, out_dir):
    """Append the rows as JSON lines to <out_dir>/phase-timings.jsonl and answer its path."""
    if not isinstance(out_dir, str) or not out_dir:
        raise ValueError("out_dir must be a non empty str")
    _check_rows(rows)
    if not os.path.isdir(out_dir):
        raise ValueError("out_dir %s is not a folder" % out_dir)
    path = os.path.join(out_dir, LEDGER)
    if not rows:
        return path
    try:
        text = "".join(json.dumps({k: r[k] for k in ROW_KEYS}, sort_keys=True, allow_nan=False) + "\n" for r in rows)
        with open(path, "a", encoding="utf-8") as fh:
            fh.write(text)
    except (OSError, TypeError, ValueError) as exc:
        raise TimingsError("the ledger %s could not be written (%s)" % (path, type(exc).__name__))
    return path


def summary(rows):
    """The rows slowest first; a null seconds sorts last, in file order."""
    _check_rows(rows)
    timed = sorted((r for r in rows if r["seconds"] is not None), key=lambda r: -r["seconds"])
    return timed + [r for r in rows if r["seconds"] is None]


def _read_source(path):
    """The whole lines of a log, or the str reason it could not be read."""
    if os.path.isdir(path):
        return "%s is a directory" % path
    try:
        with open(path, "rb") as fh:
            data = fh.read(MAX_READ)
    except OSError as exc:
        return "%s could not be read (%s)" % (path, exc.strerror or type(exc).__name__)
    return data.decode("utf-8", errors="replace").splitlines(True)


def main(argv=None):
    if argv is None:
        argv = sys.argv[1:]
    if not isinstance(argv, list) or not all(isinstance(a, str) for a in argv):
        raise SystemExit(2)
    ap = argparse.ArgumentParser(prog="loop_phase_timings.py", description="phase timings of a loop night")
    where = ap.add_mutually_exclusive_group(required=True)
    where.add_argument("--run", metavar="DIR", help="the run dir: the ledger goes in it")
    where.add_argument("--out", metavar="DIR", help="the folder the ledger goes in")
    ap.add_argument("--summary", action="store_true", help="print the phases slowest first")
    ap.add_argument("logs", nargs="+", metavar="LOG")
    a = ap.parse_args(argv)
    out_dir = a.run if a.run is not None else a.out
    if not os.path.isdir(out_dir):
        print("REFUSED: %s is not a folder; nothing was written" % out_dir)
        return 2
    sources = {}
    for p in a.logs:
        sources[p] = _read_source(p)
    rows = phases(sources)
    try:
        path = write_ledger(rows, out_dir)
    except TimingsError as exc:
        print("NO-DATA: %s" % exc)
        return 2
    readable = sum(1 for v in sources.values() if isinstance(v, list))
    if a.summary:
        for r in summary(rows):
            secs = "NO-DATA" if r["seconds"] is None else "%gs" % r["seconds"]
            print("%-9s %-16s %s | %s%s" % (secs, r["phase"] or "unknown", r["run"], r["source"],
                                            " | %s" % r["reason"] if r["reason"] else ""))
    print("LEDGER %s: %d row(s), %d of %d source(s) read" % (path, len(rows), readable, len(sources)))
    return 0 if readable else 3


if __name__ == "__main__":
    sys.exit(main())

#!/usr/bin/env python3
"""Render GANTT.html, the Brother command center, from the plan data files.

GANTT.html used to be maintained by hand and it drifted from the plan. This
script is the fix: it reads the three plan JSON files and renders the page.
Nobody edits GANTT.html directly again; they edit the JSON and re-run this.

Inputs (read only):
  docs/plan/ROADMAP-2026-08-23.json
  docs/plan/QUEUE.json
  docs/plan/LIVE-STATE.json

Output:
  GANTT.html at the repo root (overwritten every run)

With --md, a separate mode: reads only docs/plan/LIVE-STATE.json and writes
COMMAND-CENTER.md at the repo root instead, the stamp, north star, repos,
open pull requests, risks and advancement entries as plain Markdown. The
default (no flag) invocation is unchanged by this mode's existence.

Standard library only. Run from any directory:
  python3 scripts/gen_command_center.py
  python3 scripts/gen_command_center.py --md
"""
import html
import json
import re
import sys
from datetime import date, timedelta
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
PLAN_DIR = REPO_ROOT / "docs" / "plan"
ROADMAP_PATH = PLAN_DIR / "ROADMAP-2026-08-23.json"
QUEUE_PATH = PLAN_DIR / "QUEUE.json"
LIVE_STATE_PATH = PLAN_DIR / "LIVE-STATE.json"
NARRATIVE_PATH = PLAN_DIR / "NARRATIVE.json"
OUTPUT_PATH = REPO_ROOT / "GANTT.html"
MD_OUTPUT_PATH = REPO_ROOT / "COMMAND-CENTER.md"
WBS_OUTPUT_PATH = PLAN_DIR / "WBS-TODAY.md"
BENCHMARK_RUN_PATH = REPO_ROOT / "docs" / "benchmarks" / "latest-run.json"
BENCHMARK_MD_NAME = "docs/benchmarks/ATOMIC-BENCHMARK.md"
NIGHT_WATCH_PATH = PLAN_DIR / "night-watch.json"

# Roadmap row dispositions, matched robustly: PARKED rows carry a trailing
# "(founder review)" note, so a prefix match is used for that one.
# SCHEDULED-EXPERIMENT exists because the founder abolished PARK SOMEDAY on
# 2026-08-24: an idea not required for the current gate is either REJECTED
# or SCHEDULED with an objective, a wake-up event, metrics, a success
# criterion and an integration decision point. A condition without a
# written trigger is not prioritisation, it is silent deletion.
DISPOSITIONS = ["BLOCKER", "CARRY", "DONE", "PARKED", "FOUNDER-ACT",
                "OBSOLETE", "SCHEDULED-EXPERIMENT"]


def disposition_bucket(disposition):
    """Normalize a roadmap row's disposition to one of DISPOSITIONS."""
    text = (disposition or "").strip()
    for d in DISPOSITIONS:
        if text == d or text.startswith(d):
            return d
    return None

NO_DATA = "NO-DATA"

TICK_CONTRACT = (
    "A box ticks only when its done-check ran after the last edit and the "
    "output is quoted beside it. Percentages are counts of records, never "
    "impressions."
)

TRACKS = [
    "A-trust",
    "B-physical",
    "C-external-proof",
    "D-reality",
    "E-research",
    "F-founder",
]

# The stylesheet is lifted verbatim from the hand-authored GANTT.html this
# script replaces, so the visual system does not change just because the
# page is now generated instead of typed.
STYLE_CSS = """
:root{
  --paper:#F7F8F6; --card:#FFFFFF; --ink:#141B22; --ink-soft:#3D4852; --ink-faint:#6B7681;
  --petrol:#0E7A6F; --petrol-soft:#E3F0EE; --rule:#DDE2DE; --rule-soft:#EDF0EC;
  --good:#2E7D5B; --warn:#9A6B12; --bad:#A63A2E; --nodata:#5C6470;
  --hatch:rgba(14,122,111,.20); --est:rgba(14,122,111,.09);
  --serif:'Iowan Old Style','Palatino Linotype',Palatino,Georgia,serif;
  --sans:Seravek,'Gill Sans Nova',Avenir,'Segoe UI',system-ui,sans-serif;
  --mono:'SF Mono',ui-monospace,Menlo,Consolas,monospace;
}
@media (prefers-color-scheme:dark){
  :root:not([data-theme="light"]){
    --paper:#0F1519; --card:#161E24; --ink:#E8EDEA; --ink-soft:#B3BEBA; --ink-faint:#7F8C88;
    --petrol:#3AA893; --petrol-soft:#16302D; --rule:#26333A; --rule-soft:#1D272D;
    --good:#5FBF8E; --warn:#D2A143; --bad:#E0776A; --nodata:#93A0A8;
    --hatch:rgba(58,168,147,.26); --est:rgba(58,168,147,.10);
  }
}
:root[data-theme="dark"]{
  --paper:#0F1519; --card:#161E24; --ink:#E8EDEA; --ink-soft:#B3BEBA; --ink-faint:#7F8C88;
  --petrol:#3AA893; --petrol-soft:#16302D; --rule:#26333A; --rule-soft:#1D272D;
  --good:#5FBF8E; --warn:#D2A143; --bad:#E0776A; --nodata:#93A0A8;
  --hatch:rgba(58,168,147,.26); --est:rgba(58,168,147,.10);
}
*{box-sizing:border-box}
body{background:var(--paper);color:var(--ink);font-family:var(--sans);font-size:15px;line-height:1.55;
  margin:0;padding:38px 26px 90px;-webkit-font-smoothing:antialiased}
.wrap{max-width:1180px;margin:0 auto}
h1{font-family:var(--serif);font-size:2.7rem;line-height:1.08;margin:.10em 0 .18em;text-wrap:balance;font-weight:600}
h2{font-family:var(--serif);font-size:1.5rem;margin:2.6em 0 .7em;padding-bottom:.28em;
  border-bottom:1px solid var(--rule);text-wrap:balance;font-weight:600}
h3{font-family:var(--serif);font-size:1.08rem;margin:1.5em 0 .4em;font-weight:600}
.eyebrow{font-family:var(--mono);font-size:.72rem;letter-spacing:.15em;text-transform:uppercase;color:var(--petrol)}
.stamp{font-family:var(--mono);font-size:.73rem;color:var(--ink-faint);line-height:1.7}
p{margin:.55em 0;max-width:74ch}
code{font-family:var(--mono);font-size:.83em;background:var(--rule-soft);padding:.10em .34em;border-radius:3px}
a{color:var(--petrol)}
.northstar{background:var(--petrol-soft);border-left:3px solid var(--petrol);padding:18px 22px;margin:26px 0 6px;border-radius:0 6px 6px 0}
.northstar .big{font-family:var(--serif);font-size:1.30rem;line-height:1.35;margin:0 0 .45em}
.glance{display:grid;grid-template-columns:repeat(auto-fit,minmax(250px,1fr));gap:14px;margin:22px 0}
.gcell{background:var(--card);border:1px solid var(--rule);border-left:3px solid var(--rule);border-radius:7px;padding:14px 16px}
.gcell.now{border-left-color:var(--petrol)}
.gcell.wait{border-left-color:var(--warn)}
.gcell.risk{border-left-color:var(--bad)}
.gcell .k{font-family:var(--mono);font-size:.68rem;letter-spacing:.13em;text-transform:uppercase;color:var(--petrol);display:block;margin-bottom:.45em}
.gcell p{font-size:.88rem;margin:.3em 0;max-width:none}
.scroll{overflow-x:auto;-webkit-overflow-scrolling:touch}
.ranges{background:var(--card);border:1px solid var(--rule);border-radius:10px;padding:2px 22px 18px;margin:1.3em 0 2.2em}
.ranges>h2:first-child{margin-top:.5em}
table{border-collapse:collapse;width:100%;font-size:.855rem;margin:.7em 0}
th,td{text-align:left;padding:7px 10px;border-bottom:1px solid var(--rule-soft);vertical-align:top}
th{font-family:var(--mono);font-size:.68rem;letter-spacing:.09em;text-transform:uppercase;color:var(--ink-faint);border-bottom:1px solid var(--rule)}
td.n,th.n{font-variant-numeric:tabular-nums;white-space:nowrap}
.gantt{margin:.9em 0 1.4em}
.grow{display:grid;grid-template-columns:230px 1fr;gap:10px;align-items:center;margin-bottom:5px}
.glabel{font-size:.80rem;color:var(--ink-soft);display:flex;justify-content:space-between;gap:8px;padding-right:6px}
.glabel .cnt{font-family:var(--mono);font-size:.71rem;color:var(--ink-faint);font-variant-numeric:tabular-nums}
.gbar{position:relative;height:19px;background:var(--rule-soft);border-radius:3px;overflow:hidden}
.seg{position:absolute;top:0;height:100%;border-radius:3px}
.done{background:var(--petrol)}
.sched{background:repeating-linear-gradient(45deg,var(--hatch),var(--hatch) 4px,transparent 4px,transparent 8px);border:1px solid var(--hatch)}
.est{background:var(--est)}
.axis{display:grid;grid-template-columns:230px 1fr;gap:10px;margin-bottom:7px}
.ticks{display:flex;justify-content:space-between;font-family:var(--mono);font-size:.65rem;color:var(--ink-faint)}
.cards{display:grid;grid-template-columns:repeat(auto-fit,minmax(285px,1fr));gap:13px;margin:1em 0}
.card{background:var(--card);border:1px solid var(--rule);border-radius:7px;padding:14px 16px}
.card h4{font-family:var(--serif);font-size:1rem;margin:0 0 .35em;font-weight:600}
.card p{font-size:.85rem;margin:.3em 0;max-width:none;color:var(--ink-soft)}
.card .who{font-family:var(--mono);font-size:.67rem;letter-spacing:.1em;text-transform:uppercase;color:var(--petrol)}
.phase{background:var(--card);border:1px solid var(--rule);border-radius:7px;padding:5px 16px 12px;margin:12px 0}
.phase .h{display:flex;justify-content:space-between;align-items:center;gap:12px;flex-wrap:wrap}
.phase .h h3{margin:.7em 0 .5em}
.chip{font-family:var(--mono);font-size:.71rem;padding:2px 9px;border-radius:11px;white-space:nowrap;font-variant-numeric:tabular-nums}
.chip.open{background:var(--rule-soft);color:var(--ink-faint)}
.chip.mixed{background:var(--petrol-soft);color:var(--petrol)}
.chip.full{background:var(--petrol);color:var(--card)}
.item{border-top:1px solid var(--rule-soft);padding:8px 0 8px 26px;position:relative}
.item:before{content:"○";position:absolute;left:4px;top:7px;color:var(--ink-faint);font-size:.85rem}
.item.tick:before{content:"●";color:var(--petrol)}
.t2{font-size:.89rem;display:block}
.ev{display:block;font-family:var(--mono);font-size:.72rem;color:var(--ink-faint);white-space:pre-wrap;margin-top:.3em;line-height:1.55}
.v{font-family:var(--mono);font-size:.72rem;padding:1px 6px;border-radius:3px}
.v.pass{color:var(--good)} .v.fail{color:var(--bad)} .v.nd{color:var(--nodata)} .v.warn{color:var(--warn)} .v.idle{color:var(--ink-faint)}
summary{cursor:pointer}
.phase.collapsed summary{color:var(--ink-faint);font-style:italic}
.risk{border-left:3px solid var(--rule);padding:2px 0 2px 14px;margin:14px 0}
.risk.hot{border-left-color:var(--bad)} .risk.warm{border-left-color:var(--warn)} .risk.cool{border-left-color:var(--petrol)}
.risk h4{font-family:var(--serif);font-size:1.02rem;margin:0 0 .2em;font-weight:600}
.risk p{font-size:.86rem;margin:.22em 0}
.risk .act{color:var(--petrol);font-size:.83rem}
footer{margin-top:56px;padding-top:18px;border-top:1px solid var(--rule)}
.legend{font-family:var(--mono);font-size:.69rem;color:var(--ink-faint);display:flex;gap:16px;flex-wrap:wrap;margin:.5em 0 1em}
.sw{display:inline-block;width:22px;height:9px;border-radius:2px;vertical-align:middle;margin-right:5px}
@media (max-width:720px){.grow,.axis{grid-template-columns:110px 1fr}h1{font-size:2rem}body{padding:24px 15px 70px}}
"""


def load_json(path):
    """Read and parse one JSON file, or exit with a clear message."""
    if not path.is_file():
        print(f"ERROR: missing input file: {path}", file=sys.stderr)
        sys.exit(1)
    try:
        text = path.read_text(encoding="utf-8")
    except OSError as exc:
        print(f"ERROR: could not read {path}: {exc}", file=sys.stderr)
        sys.exit(1)
    try:
        return json.loads(text)
    except json.JSONDecodeError as exc:
        print(f"ERROR: could not parse JSON in {path}: {exc}", file=sys.stderr)
        sys.exit(1)


def load_benchmark_run():
    """Read docs/benchmarks/latest-run.json (scripts/benchmark_atomic.py's
    machine snapshot) if present. Returns None on any absence or read/parse
    failure so render_standings can skip the section silently rather than
    breaking the page over an optional input."""
    if not BENCHMARK_RUN_PATH.is_file():
        return None
    try:
        text = BENCHMARK_RUN_PATH.read_text(encoding="utf-8")
    except OSError:  # sbe: allow-silent optional snapshot, absence renders as no section
        return None
    try:
        return json.loads(text)
    except json.JSONDecodeError:  # sbe: allow-silent optional snapshot, bad json renders as no section
        return None


def load_night_watch():
    """Read docs/plan/night-watch.json, the overnight watchdog's own status
    file (a cron job owns it, this only reads it, per the founder's
    overnight-watchdog contract). Returns None on any absence or read/parse
    failure so render_night_watch can skip the section silently rather than
    breaking the page over a file that only exists on a session where the
    watchdog was actually armed; an older snapshot or another checkout must
    render exactly as before."""
    if not NIGHT_WATCH_PATH.is_file():
        return None
    try:
        text = NIGHT_WATCH_PATH.read_text(encoding="utf-8")
    except OSError:  # sbe: allow-silent optional watchdog file, absence renders unchanged
        return None
    try:
        return json.loads(text)
    except json.JSONDecodeError:  # sbe: allow-silent optional watchdog file, bad json renders unchanged
        return None


def clean(value):
    """Stringify a value and replace any em/en dash with a comma."""
    text = NO_DATA if value is None else str(value)
    text = text.replace(chr(0x2014), ", ").replace(chr(0x2013), ", ")
    return text


def esc(value):
    return html.escape(clean(value), quote=True)


def fmt_hours(value):
    if value is None:
        return NO_DATA
    try:
        num = float(value)
    except (TypeError, ValueError):
        return NO_DATA
    if num == int(num):
        return str(int(num))
    return str(num)


def fmt_hour(value):
    """An integer hour (8, 22) as a clock string ('08:00', '22:00')."""
    if value is None:
        return NO_DATA
    try:
        return f"{int(value):02d}:00"
    except (TypeError, ValueError):
        return NO_DATA


def get(d, key, default=NO_DATA):
    v = d.get(key)
    return default if v is None else v


def render_link_html(item):
    """The optional per row external link (Jira, Asana, Confluence, or
    anything else the founder pastes a URL for), rendered as a plain link.
    Renders nothing when the row carries no link field, so every existing
    caller's output is unchanged when the field is absent."""
    link = item.get("link")
    if not link:
        return ""
    return f'<p><a href="{esc(link)}">{esc(link)}</a></p>'


def build_title_map(roadmap, queue):
    """id -> title, sourced from short/mid/long rows first, then QUEUE.json."""
    title_map = {}
    for section in ("short", "mid", "long"):
        for item in roadmap.get(section, []):
            rid = item.get("id")
            title = item.get("title")
            if rid and title and rid not in title_map:
                title_map[rid] = title
    for q in queue:
        rid = q.get("id")
        title = q.get("title")
        if rid and title and rid not in title_map:
            title_map[rid] = title
    return title_map


def resolve_title(row, title_map):
    """The row's title, or its reason truncated at the first sentence."""
    rid = row.get("id")
    if rid in title_map:
        return title_map[rid]
    reason = row.get("reason") or ""
    idx = reason.find(". ")
    if idx != -1:
        return reason[: idx + 1]
    return reason if reason else NO_DATA


def track_chip_class(ticked, total):
    if total == 0:
        return "open"
    if ticked == 0:
        return "open"
    if ticked == total:
        return "full"
    return "mixed"


def gantt_segments(start, p50, p80):
    """Solid segment start->p50, faint segment p50->p80. Skips zero-width."""
    segs = []
    if p50 is not None and start is not None and p50 > start:
        segs.append(("sched", start, p50))
    if p80 is not None and p50 is not None and p80 > p50:
        segs.append(("est", p50, p80))
    return segs


def render_bar(segments, denom):
    if denom in (None, 0):
        return '<div class="gbar"></div>'
    parts = []
    for cls, s, e in segments:
        left = s / denom * 100
        width = (e - s) / denom * 100
        parts.append(f'<div class="seg {cls}" style="left:{left:.1f}%;width:{width:.1f}%"></div>')
    return f'<div class="gbar">{"".join(parts)}</div>'


def render_track_gantt(section_items, rows_by_id, denom, header, ticks, legend_html, stamp_text,
                        start_key, p50_key, p80_key, cnt_fn):
    """Shared renderer for any track-grouped Gantt chart (short, mid, ...).

    cnt_fn(item, row) returns the right-hand label string for one bar.
    """
    by_track = {t: [] for t in TRACKS}
    for item in section_items:
        by_track.setdefault(item.get("track", NO_DATA), []).append(item)

    done_total = sum(1 for i in section_items
                     if rows_by_id.get(i.get("id"), {}).get("disposition") == "DONE")
    count_total = len(section_items)
    chip_cls = track_chip_class(done_total, count_total)

    out = []
    out.append(f'<h2>{esc(header)} <span class="chip {chip_cls}">{done_total}/{count_total} done</span></h2>')
    if legend_html:
        out.append(legend_html)
    out.append(f'<p class="stamp">{esc(stamp_text)}</p>')
    out.append('<div class="gantt">')
    out.append(f'<div class="axis"><div></div><div class="ticks">{"".join(f"<span>{esc(t)}</span>" for t in ticks)}</div></div>')
    for track in TRACKS:
        items = by_track.get(track, [])
        if not items:
            continue
        ticked = sum(1 for i in items if rows_by_id.get(i["id"], {}).get("disposition") == "DONE")
        chip = track_chip_class(ticked, len(items))
        out.append(f'<h3>{esc(track)} <span class="chip {chip}">{ticked}/{len(items)}</span></h3>')
        for item in items:
            rid = item.get("id", NO_DATA)
            title = item.get("title", NO_DATA)
            row = rows_by_id.get(rid, {})
            cnt = cnt_fn(item, row)
            segs = gantt_segments(item.get(start_key), item.get(p50_key), item.get(p80_key))
            bar = render_bar(segs, denom)
            out.append(
                f'<div class="grow"><div class="glabel"><span>{esc(rid)}: {esc(title)}</span>'
                f'<span class="cnt">{esc(cnt)}</span></div>{bar}</div>'
            )
    out.append('</div>')
    return "\n".join(out)


DAY_LEGEND = ('<div class="legend"><span><i class="sw done"></i>done, actual</span>'
              '<span><i class="sw sched"></i>solid, to P50</span>'
              '<span><i class="sw est"></i>faint tail, P50 to P80</span></div>')


def day_segments(row, hours_start):
    """One day_plan row's bar segments, hours shifted so hours_start is 0.

    A DONE row renders as one solid 'done' segment (what actually happened),
    never the sched/est split, because a completed row has no P50/P80 left
    to forecast. Anything else reuses gantt_segments verbatim, the same
    solid-to-P50/faint-tail-to-P80 encoding every other chart on this page
    uses."""
    if hours_start is None:
        return []
    start = row.get("start_hour")
    if start is None:
        return []
    p50 = row.get("end_hour_p50")
    p80 = row.get("end_hour_p80")
    s = start - hours_start
    e50 = None if p50 is None else p50 - hours_start
    e80 = None if p80 is None else p80 - hours_start
    if clean(row.get("status")).strip().upper() == "DONE":
        end = e50 if e50 is not None else e80
        return [("done", s, end)] if end is not None and end > s else []
    return gantt_segments(s, e50, e80)


def ready_state(rows):
    """The ready set, computed per row (READY-SET-STANDARD-2026-08-28.md).

    A SCHEDULED row is READY when every id in its depends_on names a DONE
    row, EVENT-WAIT when its deps are met but it names an external event
    (a verdict that arrives by notification, never by anyone waiting), and
    BLOCKED-BY otherwise, naming the unmet ids. A dep id that names no row
    can never become DONE and is flagged (unknown) rather than silently
    treated as met, because a typo that unblocks a task is the failure
    direction that matters. Rows in any other status report that status.
    Pure over its input so the test file can feed it directly."""
    status_of = {clean(get(r, "id")): clean(r.get("status")).strip().upper()
                 for r in rows}
    out = {}
    for r in rows:
        rid = clean(get(r, "id"))
        status = clean(r.get("status")).strip().upper()
        if status != "SCHEDULED":
            out[rid] = status
            continue
        unmet = []
        for dep in (r.get("depends_on") or []):
            dep = clean(dep)
            if dep not in status_of:
                unmet.append(dep + " (unknown)")
            elif status_of[dep] != "DONE":
                unmet.append(dep)
        event = r.get("event")
        if unmet:
            out[rid] = "BLOCKED-BY " + ", ".join(unmet)
        elif isinstance(event, str) and event.strip():
            out[rid] = "EVENT-WAIT: " + clean(event)
        else:
            out[rid] = "READY"
    return out


def compute_downstream_dependents(rows):
    """id -> count of rows that transitively depend on it through
    depends_on edges (READY-SET-STANDARD-2026-08-28.md's pull rule: among
    READY nodes, pull the one with the most downstream dependents first,
    because it unblocks the most). Built as a reverse graph (dep -> the ids
    that name it directly) then walked once per row with a visited set, so
    a cycle in depends_on cannot loop this forever even though the graph is
    not expected to carry one. An id that names no row (see ready_state's
    unknown-dep flag) simply has no entry here and cannot itself be counted
    as anyone's downstream dependent."""
    ids = [clean(get(r, "id")) for r in rows]
    direct_dependents = {rid: set() for rid in ids}
    for r in rows:
        rid = clean(get(r, "id"))
        for dep in (r.get("depends_on") or []):
            dep = clean(dep)
            if dep in direct_dependents:
                direct_dependents[dep].add(rid)
    counts = {}
    for rid in ids:
        seen = set()
        stack = list(direct_dependents.get(rid, ()))
        while stack:
            nxt = stack.pop()
            if nxt in seen:
                continue
            seen.add(nxt)
            stack.extend(direct_dependents.get(nxt, ()))
        counts[rid] = len(seen)
    return counts


LOOP_CLOSE_RING = (
    "Every task close runs, in order: (1) the task's own done_check after "
    "the last edit, output quoted into evidence; (2) sh scripts/check_all.sh "
    "green; (3) python3 scripts/task_watchdog.py exit 0; (4) board "
    "regenerated and byte-stable on a second run; (5) gated push where "
    "commits exist; (6) artifact republished; (7) memory milestone written "
    "(vault and auto-memory)."
)

IN_FLIGHT_CAP = 2


def is_founder_lane_row(row):
    """A row the founder lane owns, never pulled by a session: its owner
    names the founder or the team, or it carries a non-empty event field
    (an external verdict, per READY-SET-STANDARD-2026-08-28.md)."""
    owner = clean(row.get("owner")).lower()
    event = row.get("event")
    has_event = isinstance(event, str) and event.strip() != ""
    return "founder" in owner or "team" in owner or has_event


def render_graph_loops(live_state):
    """Graph loops, and how we close them (READY-SET-STANDARD-2026-08-28.md):
    the two lanes actually in flight (with a breach warning past the cap and
    an idle warning when a lane sits empty while work is READY), the ready
    queue ordered by what it unblocks, the founder lane a session never
    pulls from, and the loop-close ring quoted from the standard. Renders
    nothing when day_plan is absent or empty, same convention as
    render_day_gantt: an older LIVE-STATE.json snapshot must still render
    exactly as before."""
    day_plan = live_state.get("day_plan")
    rows = (day_plan or {}).get("rows") or []
    if not rows:
        return ""

    ready = ready_state(rows)
    dependents = compute_downstream_dependents(rows)

    in_flight = [r for r in rows
                 if clean(r.get("status")).strip().upper() == "IN-FLIGHT"]
    ready_ids = {rid for rid, v in ready.items() if v == "READY"}
    ready_rows = [r for r in rows if clean(get(r, "id")) in ready_ids]
    ready_rows.sort(key=lambda r: (-dependents.get(clean(get(r, "id")), 0),
                                    clean(get(r, "id"))))
    founder_rows = [r for r in rows if is_founder_lane_row(r)]

    out = ['<h2>Graph loops, and how we close them</h2>']
    out.append(
        '<p class="stamp">A node waits, a lane never does '
        '(docs/plan/READY-SET-STANDARD-2026-08-28.md). Two lanes, always '
        'full; among READY nodes, pull the one that unblocks the most '
        'first.</p>'
    )
    if len(in_flight) > IN_FLIGHT_CAP:
        out.append(
            f'<p><span class="v warn">BREACH</span> {len(in_flight)} lanes '
            f'in flight, cap is {IN_FLIGHT_CAP}.</p>'
        )
    if not in_flight and ready_ids:
        out.append(
            f'<p><span class="v warn">IDLE</span> zero lanes in flight '
            f'while {len(ready_ids)} row(s) compute READY.</p>'
        )

    out.append('<h3>The two lanes, always full</h3>')
    if in_flight:
        out.append('<div class="cards">')
        for r in in_flight:
            out.append(
                '<div class="card">'
                f'<span class="who">{esc(get(r, "id"))}</span>'
                f'<h4>{esc(get(r, "title"))}</h4>'
                f'<p>Owner: {esc(get(r, "owner"))}</p>'
                f'<p class="stamp">Done check: {esc(get(r, "done_check"))}</p>'
                '</div>'
            )
        out.append('</div>')
    else:
        out.append('<p>No lane is in flight.</p>')

    out.append('<h3>The ready queue</h3>')
    if ready_rows:
        out.append('<div class="cards">')
        for r in ready_rows:
            rid = clean(get(r, "id"))
            out.append(
                '<div class="card">'
                f'<span class="who">{esc(rid)}</span>'
                f'<h4>{esc(get(r, "title"))} '
                f'<span class="chip open">unblocks {dependents.get(rid, 0)}</span></h4>'
                f'<p>Owner: {esc(get(r, "owner"))}</p>'
                '</div>'
            )
        out.append('</div>')
    else:
        out.append('<p>No row currently computes READY.</p>')

    out.append('<h3>The founder lane, never pulled by a session</h3>')
    if founder_rows:
        out.append('<div class="cards">')
        for r in founder_rows:
            rid = clean(get(r, "id"))
            event = r.get("event")
            event_text = clean(event) if isinstance(event, str) and event.strip() else NO_DATA
            out.append(
                '<div class="card">'
                f'<span class="who">{esc(get(r, "owner"))}</span>'
                f'<h4>{esc(get(r, "title"))}</h4>'
                f'<p>{esc(rid)}: {esc(ready.get(rid, clean(r.get("status"))))}</p>'
                f'<p class="stamp">Event: {esc(event_text)}</p>'
                '</div>'
            )
        out.append('</div>')
    else:
        out.append('<p>No founder or team row in the current plan.</p>')

    out.append('<h3>The loop-close control ring</h3>')
    out.append(f'<p class="legend">{esc(LOOP_CLOSE_RING)}</p>')
    return "\n".join(out)


def render_day_gantt(live_state):
    """Today, the full day: an hour grain Gantt for live_state['day_plan'],
    the first step of the living plan (ROADMAP-REPLACE-2026-08-27.md Phase
    2.2). Renders nothing when day_plan is absent, so an older LIVE-STATE.json
    snapshot with no day_plan key still renders exactly as before."""
    day_plan = live_state.get("day_plan")
    if not day_plan or not day_plan.get("rows"):
        return ""
    rows = day_plan["rows"]
    hours_start = day_plan.get("hours_start")
    hours_end = day_plan.get("hours_end")

    denom = None
    ticks = [NO_DATA]
    if isinstance(hours_start, int) and isinstance(hours_end, int) and hours_end > hours_start:
        denom = hours_end - hours_start
        ticks = [fmt_hour(h) for h in range(hours_start, hours_end + 1, 2)]

    header = (f"Today, the full day ({clean(get(day_plan, 'date'))} "
              f"{clean(get(day_plan, 'timezone'))}, {fmt_hour(hours_start)} to {fmt_hour(hours_end)})")
    done_ct = sum(1 for r in rows if clean(r.get("status")).strip().upper() == "DONE")
    stamp = (f"{len(rows)} rows from day_plan, hour grain, {done_ct}/{len(rows)} done. "
             f"{clean(get(day_plan, 'note'))}")
    chip_cls = track_chip_class(done_ct, len(rows))

    ready = ready_state(rows)
    ready_ct = sum(1 for v in ready.values() if v == "READY")
    out = [f'<h2>{esc(header)} <span class="chip {chip_cls}">{done_ct}/{len(rows)} done</span></h2>',
           DAY_LEGEND, f'<p class="stamp">{esc(stamp)}</p>',
           f'<p class="stamp">Ready set: {ready_ct} row(s) READY now; verdicts arrive as events, never as waits (READY-SET-STANDARD-2026-08-28.md).</p>',
           '<div class="gantt">']
    out.append(f'<div class="axis"><div></div><div class="ticks">{"".join(f"<span>{esc(t)}</span>" for t in ticks)}</div></div>')
    for row in rows:
        bar = render_bar(day_segments(row, hours_start), denom)
        label = f'{esc(get(row, "id"))}: {esc(get(row, "title"))} ({esc(get(row, "owner"))})'
        rid = clean(get(row, "id"))
        chip = ready.get(rid, clean(row.get("status")))
        out.append(
            f'<div class="grow"><div class="glabel"><span>{label}</span>'
            f'<span class="cnt">{esc(chip)}</span></div>{bar}</div>'
        )
    out.append('</div>')
    return "\n".join(out)


SHORT_LEGEND = ('<div class="legend"><span><i class="sw sched"></i>solid, to end day P50</span>'
                 '<span><i class="sw est"></i>faint tail, P50 to P80</span></div>')


def render_short_gantt(roadmap, rows_by_id):
    short = roadmap.get("short", [])
    denom = 14
    generated = roadmap.get("generated")
    start_date = date.fromisoformat(generated) if generated else None
    if start_date:
        end_date = start_date + timedelta(days=denom)
        ticks = [(start_date + timedelta(days=d)).strftime("%b %-d") for d in range(0, denom + 1, 2)]
        header = f"Short range, the next fourteen days ({start_date.isoformat()} to {end_date.isoformat()})"
    else:
        ticks = [NO_DATA]
        header = "Short range, the next fourteen days"

    stamp = (f"{len(short)} rows from the JSON's short list, one bar per row, "
             "grouped by track. Each track header carries an n/m counter, done rows over total "
             "rows in that track's short-horizon slice. Label's right column is P50h/P80h from "
             "the matching roadmap row.")

    def cnt_fn(item, row):
        return f"{fmt_hours(row.get('p50_hours'))}h/{fmt_hours(row.get('p80_hours'))}h"

    return render_track_gantt(short, rows_by_id, denom, header, ticks, SHORT_LEGEND, stamp,
                               "start_day", "end_day_p50", "end_day_p80", cnt_fn)


def render_mid_gantt(roadmap, rows_by_id):
    mid = roadmap.get("mid", [])
    weeks = 6
    generated = roadmap.get("generated")
    start_date = date.fromisoformat(generated) if generated else None
    if start_date:
        end_date = start_date + timedelta(weeks=weeks)
        ticks = [(start_date + timedelta(weeks=w)).strftime("%b %-d") for w in range(0, weeks + 1)]
        header = f"Medium range, six weeks ({start_date.isoformat()} to {end_date.isoformat()})"
    else:
        ticks = [NO_DATA]
        header = "Medium range, six weeks"

    stamp = f"{len(mid)} rows from the JSON's mid list. Same solid/faint encoding, weeks instead of days."

    def cnt_fn(item, row):
        return f"wk{fmt_hours(item.get('end_week_p50'))}/wk{fmt_hours(item.get('end_week_p80'))}"

    return render_track_gantt(mid, rows_by_id, weeks, header, ticks, "", stamp,
                               "start_week", "end_week_p50", "end_week_p80", cnt_fn)


def parse_week_range(text):
    m = re.search(r"weeks?\s+(\d+)(?:-(\d+))?", text or "")
    if not m:
        return None, None
    start = int(m.group(1))
    end = int(m.group(2)) if m.group(2) else start
    return start, end


def parse_trailing_date(text):
    hits = re.findall(r"\d{4}-\d{2}-\d{2}", text or "")
    return hits[-1] if hits else None


def render_long_gantt(roadmap):
    long_list = roadmap.get("long", [])
    generated = roadmap.get("generated")
    start_date = date.fromisoformat(generated) if generated else None

    parsed = [parse_week_range(m.get("quarter_window", "")) for m in long_list]
    axis_max = max((e for s, e in parsed if e is not None), default=None)

    end_date = None
    if long_list:
        end_date_str = parse_trailing_date(long_list[-1].get("quarter_window", ""))
        if end_date_str:
            end_date = date.fromisoformat(end_date_str)

    if start_date and end_date:
        header = f"Long range, to {end_date.isoformat()}"
        ticks = []
        m = start_date.month
        y = start_date.year
        while (y, m) < (end_date.year, end_date.month):
            ticks.append(date(y, m, 1).strftime("%b"))
            m += 1
            if m > 12:
                m = 1
                y += 1
        ticks.append(date(y, m, 1).strftime("%b"))
        ticks.append(end_date.strftime("%b %-d"))
    else:
        header = "Long range"
        ticks = [NO_DATA]

    out = []
    out.append(f'<h2>{esc(header)} <span class="chip open">{len(long_list)} milestones</span></h2>')
    out.append(f'<p class="stamp">{len(long_list)} milestones from the JSON\'s long list, each with '
                "its gate. Weeks counted from the plan's generated date (week 0). No per-track split: "
                "the long list carries no track field.</p>")
    out.append('<div class="gantt">')
    out.append(f'<div class="axis"><div></div><div class="ticks">{"".join(f"<span>{esc(t)}</span>" for t in ticks)}</div></div>')
    for milestone, (s, e) in zip(long_list, parsed):
        title = milestone.get("milestone", NO_DATA)
        window = milestone.get("quarter_window", NO_DATA)
        gate = milestone.get("gate", NO_DATA)
        cnt = f"wk{s}-{e}" if s is not None else NO_DATA
        segs = [("sched", s, e)] if s is not None and e is not None and e > s else []
        bar = render_bar(segs, axis_max)
        out.append(
            f'<div class="grow"><div class="glabel"><span>{esc(title)}</span>'
            f'<span class="cnt">{esc(cnt)}</span></div>{bar}</div>'
        )
        out.append(f'<p class="stamp" style="margin:.1em 0 .9em 240px">Window: {esc(window)}. Gate: {esc(gate)}</p>')
    out.append('</div>')
    return "\n".join(out)


def render_work_breakdown(roadmap, narrative):
    """The per-track disposition table. Counts are computed from the 55
    roadmap rows, never hand typed; the track descriptions and intro
    paragraph come from NARRATIVE.json (lifted from the hand written page
    this script replaced)."""
    rows = roadmap.get("rows", [])
    wb = narrative.get("work_breakdown", {})
    descriptions = wb.get("track_descriptions", {})
    intro = wb.get("intro", NO_DATA)

    counts = {t: {d: 0 for d in DISPOSITIONS} for t in TRACKS}
    unmatched = []
    for row in rows:
        track = row.get("track")
        bucket = disposition_bucket(row.get("disposition"))
        if track not in counts or bucket is None:
            unmatched.append(row.get("id", NO_DATA))
            continue
        counts[track][bucket] += 1

    total_rows = sum(sum(counts[t].values()) for t in TRACKS)
    if total_rows != len(rows):
        print(
            f"ERROR: work breakdown counts sum to {total_rows}, not {len(rows)} "
            f"roadmap rows (unmatched: {unmatched})",
            file=sys.stderr,
        )
        sys.exit(1)

    header_cols = ["Track", "What it is", "Rows", "Blocker", "Carry", "Done", "Parked", "Founder-act", "Obsolete"]
    out = ['<h2>The work breakdown, every open item mapped exactly once</h2>']
    out.append(f'<p>{esc(intro)}</p>')
    out.append('<div class="scroll">\n<table>')
    out.append('<tr>' + "".join(
        f'<th class="n">{esc(c)}</th>' if c not in ("Track", "What it is") else f'<th>{esc(c)}</th>'
        for c in header_cols
    ) + '</tr>')
    totals = {d: 0 for d in DISPOSITIONS}
    for track in TRACKS:
        c = counts[track]
        row_total = sum(c.values())
        for d in DISPOSITIONS:
            totals[d] += c[d]
        desc = descriptions.get(track, NO_DATA)
        out.append(
            f'<tr><td>{esc(track)}</td><td>{esc(desc)}</td><td class="n">{row_total}</td>'
            f'<td class="n">{c["BLOCKER"]}</td><td class="n">{c["CARRY"]}</td>'
            f'<td class="n">{c["DONE"]}</td><td class="n">{c["PARKED"]}</td>'
            f'<td class="n">{c["FOUNDER-ACT"]}</td><td class="n">{c["OBSOLETE"]}</td></tr>'
        )
    out.append(
        f'<tr><td><strong>Total</strong></td><td></td><td class="n"><strong>{total_rows}</strong></td>'
        f'<td class="n"><strong>{totals["BLOCKER"]}</strong></td><td class="n"><strong>{totals["CARRY"]}</strong></td>'
        f'<td class="n"><strong>{totals["DONE"]}</strong></td><td class="n"><strong>{totals["PARKED"]}</strong></td>'
        f'<td class="n"><strong>{totals["FOUNDER-ACT"]}</strong></td><td class="n"><strong>{totals["OBSOLETE"]}</strong></td></tr>'
    )
    out.append('</table>\n</div>')
    out.append(f'<p class="stamp">{len(rows)} roadmap rows, six dispositions, every row\'s own track and '
                'hours in <code>docs/plan/ROADMAP-2026-08-23.json</code>.</p>')
    return "\n".join(out)


NOT_RECORDED_RETEST_TOKENS = {"NOT YET", "BLOCKED", "N/A", "", NO_DATA.upper()}
AFFIRMATIVE_STATE_MARKERS = ("DELIVERED", "LANDED", "ANSWERED", "SHIPPED", "CLOSED", "RESOLVED")


def classify_complaint(state_text, retest_text):
    """Derive a complaint row's colour from the closure rule at render
    time, never from a stored class token, so the two cannot drift apart
    again. The rule: a row may show the good (pass) token only when its
    retest is actually recorded, a retest of NOT YET, BLOCKED, n/a, empty
    or missing does not count as recorded. HALF OPEN and TRACKED states
    always stay warn. OPEN (and otherwise negative) states stay fail. An
    affirmative state (DELIVERED, LANDED, ANSWERED, or similar) is pass
    only with a recorded retest, otherwise it drops to warn because work
    was delivered but the rule is not yet satisfied.

    Returns (state_cls, retest_cls, delivered_not_retested)."""
    state_up = clean(state_text).strip().upper()
    retest_up = clean(retest_text).strip().upper()
    retest_recorded = retest_up not in NOT_RECORDED_RETEST_TOKENS
    retest_cls = "pass" if retest_recorded else "nd"

    delivered_not_retested = False
    if "HALF OPEN" in state_up or "TRACKED" in state_up:
        state_cls = "warn"
    elif "OPEN" in state_up:
        state_cls = "fail"
    elif any(marker in state_up for marker in AFFIRMATIVE_STATE_MARKERS):
        if retest_recorded:
            state_cls = "pass"
        else:
            state_cls = "warn"
            delivered_not_retested = True
    else:
        state_cls = "fail"
    return state_cls, retest_cls, delivered_not_retested


def render_complaints(narrative):
    """The team's complaints table, lifted from NARRATIVE.json except for
    colour: the pass/warn/fail chip for each row is derived at render
    time from the closure rule (see classify_complaint), never trusted
    from the stored state_class/retest_class fields, which is what let
    the rendering drift from the rule in the first place."""
    c = narrative.get("complaints", {})
    closure_rule = c.get("closure_rule", NO_DATA)
    intro = c.get("intro", NO_DATA)
    honest_reading = c.get("honest_reading")
    rows = c.get("rows", [])

    out = ["<h2>The team's complaints, and whether they are actually closed</h2>"]
    out.append(f'<p><strong>{esc(closure_rule)}</strong> {esc(intro)}</p>')
    out.append('<div class="scroll">\n<table>')
    out.append('<tr><th>Id</th><th>The complaint, in the complainant\'s terms</th><th>State</th>'
                '<th>Retest recorded</th><th>Where it closes</th></tr>')
    not_retested_count = 0
    for r in rows:
        state_cls, retest_cls, delivered_not_retested = classify_complaint(
            r.get("state"), r.get("retest"))
        if delivered_not_retested:
            not_retested_count += 1
        state_text = esc(r.get("state", NO_DATA))
        state_html = f'<span class="v {state_cls}">{state_text}</span>' if state_cls else state_text
        retest_text = esc(r.get("retest", NO_DATA))
        retest_html = f'<span class="v {retest_cls}">{retest_text}</span>' if retest_cls else retest_text
        out.append(
            f'<tr><td class="n">{esc(r.get("id", NO_DATA))}</td><td>{esc(r.get("complaint", NO_DATA))}</td>'
            f'<td>{state_html}</td><td>{retest_html}</td><td>{esc(r.get("closes", NO_DATA))}</td></tr>'
        )
    out.append('</table>\n</div>')
    out.append(f'<p>{not_retested_count} of these are delivered but not retested, '
                'so by the rule above they are not closed.</p>')
    if honest_reading:
        out.append(f'<p class="stamp">{esc(honest_reading)}</p>')
    return "\n".join(out)


BENCHMARK_FIELD_ORDER = ["axis", "superpowers", "gsd", "bmad", "speckit", "compound",
                          "ruflo", "brother_today", "brother_target", "label"]


def render_benchmark(narrative):
    """Self benchmarking table, lifted verbatim from NARRATIVE.json. The
    JUDGED/MEASURED labels and the competitor-verification caveat are load
    bearing and always render alongside the table, never dropped."""
    b = narrative.get("benchmark", {})
    labels = b.get("labels", {})
    caveat = b.get("caveat", NO_DATA)
    columns = b.get("columns", [])
    rows = b.get("rows", [])

    label_bits = " ".join(
        f'<strong>{esc(name)}</strong> means {esc(desc)}' for name, desc in labels.items()
    )
    out = ["<h2>Self benchmarking: where we actually stand against the field</h2>"]
    out.append(f'<p>Two honesty labels apply to everything in this section. {label_bits} {esc(caveat)}</p>')
    out.append('<div class="scroll">\n<table>')
    out.append('<tr>' + "".join(
        f'<th>{esc(col)}</th>' if col in ("Axis", "Label") else f'<th class="n">{esc(col)}</th>'
        for col in columns
    ) + '</tr>')
    for r in rows:
        cells = []
        for key in BENCHMARK_FIELD_ORDER:
            val = r.get(key, NO_DATA)
            cls = "" if key in ("axis", "label") else ' class="n"'
            cells.append(f'<td{cls}>{esc(val)}</td>')
        out.append('<tr>' + "".join(cells) + '</tr>')
    out.append('</table>\n</div>')
    return "\n".join(out)


def render_competitive(narrative):
    """The evidence-backed competitive table, lifted verbatim from
    NARRATIVE.json's `competitive` key. Deliberately SEPARATE from
    render_benchmark: that table scores this project against workflow
    plugins for its own host harness on judged opinion, and this one
    records what named
    products in the actual category do, each row carrying a URL somebody
    opened. Mixing judged scores into an evidenced table is how a reader
    stops being able to tell which cells were measured, so the two stay
    apart and each says which it is.

    Renders nothing when the key is absent, per the source data rather than
    an empty box."""
    c = narrative.get("competitive")
    if not c:
        return ""
    rows = c.get("rows", [])
    if not rows:
        return ""
    out = ['<h2>The field, as its own documentation describes it</h2>']
    out.append(f'<p>{esc(c.get("intro", NO_DATA))}</p>')
    out.append('<div class="scroll">\n<table>')
    out.append('<tr><th>Product</th><th>What it does, in its own words</th>'
               '<th class="n">Chain coverage</th><th class="n">Insufficient evidence?</th>'
               '<th class="n">Human gate?</th><th>Maturity signal</th></tr>')
    for r in rows:
        out.append(
            '<tr><td>{name}</td><td>{does}</td><td class="n">{cov}</td>'
            '<td class="n">{ins}</td><td class="n">{hum}</td><td>{mat}</td></tr>'.format(
                name=esc(r.get("name", NO_DATA)),
                does=esc(r.get("does", NO_DATA)),
                cov=esc(r.get("coverage", NO_DATA)),
                ins=esc(r.get("insufficient", NO_DATA)),
                hum=esc(r.get("human_gate", NO_DATA)),
                mat=esc(r.get("maturity", NO_DATA)),
            )
        )
    out.append('</table>\n</div>')
    for f in c.get("findings", []):
        out.append(
            '<div class="risk {tone}"><h4>{h}</h4><p>{b}</p></div>'.format(
                tone=esc(f.get("tone", "cool")),
                h=esc(f.get("heading", NO_DATA)),
                b=esc(f.get("body", NO_DATA)),
            )
        )
    return "\n".join(out)


def render_three_scores(narrative):
    """The three score cards that follow the benchmark table, lifted
    verbatim from NARRATIVE.json's benchmark.three_scores. Same card markup
    as render_decisions_waiting, reused rather than a new class."""
    ts = narrative.get("benchmark", {}).get("three_scores", {})
    heading = ts.get("heading", NO_DATA)
    cards = ts.get("cards", [])

    out = [f'<h3>{esc(heading)}</h3>', '<div class="cards">']
    for c in cards:
        value = c.get("value")
        value_html = f'<strong>{esc(value)}</strong> ' if value else ""
        out.append(
            '<div class="card">'
            f'<span class="who">{esc(get(c, "who"))}</span>'
            f'<h4>{esc(get(c, "label"))}</h4>'
            f'<p>{value_html}{esc(get(c, "text"))}</p>'
            f'<p class="stamp">{esc(get(c, "stamp"))}</p>'
            '</div>'
        )
    out.append('</div>')
    return "\n".join(out)


def md_cell(value):
    """One table cell of Markdown text: no raw pipe or newline, both of
    which would break the row out of its table."""
    text = clean(value)
    text = text.replace("|", "\\|").replace("\n", " ")
    return text


def md_link_cell(item):
    link = item.get("link")
    return md_cell(link) if link else NO_DATA


def md_link_line(item):
    """The optional per row external link, as its own Markdown line.
    Renders nothing when the row carries no link field."""
    link = item.get("link")
    if not link:
        return ""
    return f"\nLink: {clean(link)}\n"


def render_wbs_day_table(day_plan):
    """The day_plan rows as one markdown table: id, title, hours, owner,
    status, done check, evidence, plus Unblocks, the same downstream
    dependents count the HTML graph loops section's ready queue sorts by
    (compute_downstream_dependents). Same source day_plan the HTML day
    gantt reads, so the two can never drift apart."""
    rows = (day_plan or {}).get("rows") or []
    ready = ready_state(rows)
    dependents = compute_downstream_dependents(rows)
    lines = ["| Id | Title | Hours | Owner | Status | Ready | Unblocks | Done check | Evidence |",
             "|---|---|---|---|---|---|---|---|---|"]
    for r in rows:
        hours = f"{fmt_hour(r.get('start_hour'))} to {fmt_hour(r.get('end_hour_p50'))}"
        rid = clean(get(r, "id"))
        lines.append(
            "| {id} | {title} | {hours} | {owner} | {status} | {ready} | {unblocks} | {check} | {ev} |".format(
                id=md_cell(get(r, "id")), title=md_cell(get(r, "title")),
                hours=md_cell(hours), owner=md_cell(get(r, "owner")),
                status=md_cell(get(r, "status")), ready=md_cell(ready.get(rid, NO_DATA)),
                unblocks=md_cell(dependents.get(rid, 0)),
                check=md_cell(get(r, "done_check")),
                ev=md_cell(get(r, "evidence")),
            )
        )
    return lines


def render_wbs_short_table(roadmap):
    """The roadmap's short track as one markdown table, so the day WBS sits
    inside the standing WBS rather than replacing it."""
    short = roadmap.get("short") or []
    lines = ["| Id | Title | Track | Owner | Day window |",
             "|---|---|---|---|---|"]
    for item in short:
        window = f"day {get(item, 'start_day')} to day {get(item, 'end_day_p50')}"
        lines.append(
            "| {id} | {title} | {track} | {owner} | {window} |".format(
                id=md_cell(get(item, "id")), title=md_cell(get(item, "title")),
                track=md_cell(get(item, "track")), owner=md_cell(get(item, "owner")),
                window=md_cell(window),
            )
        )
    return lines


def render_wbs_markdown(live_state, roadmap):
    """docs/plan/WBS-TODAY.md: the day_plan and the roadmap's short track,
    both read from the same JSON files the Gantt reads, so the markdown WBS
    regenerates from the same single source rather than being typed by hand
    (Phase 2.2 of ROADMAP-REPLACE-2026-08-27.md). Deterministic: every
    timestamp comes from the JSON's own fields, never datetime.now()."""
    day_plan = live_state.get("day_plan") or {}
    lines = [
        "Generated by scripts/gen_command_center.py from docs/plan/LIVE-STATE.json "
        f"and {ROADMAP_PATH.name}. Never hand edited.",
        "",
        f"# The day WBS, {clean(get(day_plan, 'date'))}",
        "",
        f"Sources: docs/plan/LIVE-STATE.json (day_plan) and docs/plan/{ROADMAP_PATH.name} "
        "(short track). Regenerate with python3 scripts/gen_command_center.py.",
        "",
        "## Today, hour grain",
        "",
    ]
    lines.extend(render_wbs_day_table(day_plan))
    lines.append("")
    lines.append("## Standing WBS, short range (the next fourteen days)")
    lines.append("")
    lines.extend(render_wbs_short_table(roadmap))
    return "\n".join(lines).rstrip() + "\n"


def render_markdown(live_state):
    """The command center's substance as plain Markdown: the stamp and
    session, the north star, the repos and their state, the open pull
    requests, the risks with their action, and the advancement entries with
    their evidence. Sourced from LIVE-STATE.json only, same data the HTML
    board reads for these same sections. Any row carrying a `link` field
    gets it rendered as a plain link; a row without one renders with no
    link, same as the HTML."""
    lines = []
    lines.append("# Brother Command Center")
    lines.append("")
    lines.append(f"Refreshed {clean(get(live_state, 'measured_at'))}, session "
                  f"{clean(get(live_state, 'session'))}. Generated (not hand edited) from "
                  "docs/plan/LIVE-STATE.json by scripts/gen_command_center.py --md.")
    lines.append("")

    lines.append("## North star")
    lines.append("")
    lines.append(clean(get(live_state, "north_star")))
    lines.append("")

    lines.append("## Repos")
    lines.append("")
    repos = live_state.get("repos") or []
    lines.append("| Repo | Head | Origin main | In sync | Public | Link | Note |")
    lines.append("|---|---|---|---|---|---|---|")
    for r in repos:
        lines.append(
            f"| {md_cell(get(r, 'name'))} | {md_cell(get(r, 'head'))} | "
            f"{md_cell(get(r, 'origin_main'))} | {md_cell(get(r, 'in_sync'))} | "
            f"{md_cell(get(r, 'public'))} | {md_link_cell(r)} | {md_cell(get(r, 'note'))} |"
        )
    lines.append("")

    lines.append("## Open pull requests")
    lines.append("")
    prs = live_state.get("pull_requests") or []
    lines.append("| Repo | Number | State | Link | Note |")
    lines.append("|---|---|---|---|---|")
    for p in prs:
        lines.append(
            f"| {md_cell(get(p, 'repo'))} | {md_cell(get(p, 'number'))} | "
            f"{md_cell(get(p, 'state'))} | {md_link_cell(p)} | {md_cell(get(p, 'note'))} |"
        )
    lines.append("")

    lines.append("## Risks, with their action")
    lines.append("")
    for r in live_state.get("risks") or []:
        lines.append(f"### {clean(get(r, 'title'))} ({clean(get(r, 'level'))})")
        lines.append("")
        lines.append(f"What: {clean(get(r, 'what'))}")
        lines.append("")
        lines.append(f"Why it matters: {clean(get(r, 'why'))}")
        lines.append("")
        lines.append(f"Action: {clean(get(r, 'action'))}")
        lines.append("")
        lines.append(f"When: {clean(get(r, 'when'))}")
        lines.append(md_link_line(r))
        lines.append("")

    lines.append("## Advancement today")
    lines.append("")
    for a in live_state.get("advancement_today") or []:
        lines.append(f"### {clean(get(a, 'what'))}")
        lines.append("")
        lines.append(f"State: {clean(get(a, 'state'))}")
        lines.append("")
        lines.append(f"Evidence: {clean(get(a, 'evidence'))}")
        lines.append(md_link_line(a))
        lines.append("")

    return "\n".join(lines).rstrip() + "\n"


def render_header(live_state):
    session = get(live_state, "session")
    measured_at = get(live_state, "measured_at")
    return (
        '<p class="eyebrow">Brother, the command center</p>\n'
        '<h1>One thread, one plan, and the proof beside every claim</h1>\n'
        f'<p class="stamp">Refreshed {esc(measured_at)}, session {esc(session)}, generated (not hand '
        f'edited) from docs/plan/ROADMAP-2026-08-23.json, docs/plan/QUEUE.json and '
        f'docs/plan/LIVE-STATE.json<br>\nTick contract: {esc(TICK_CONTRACT)}</p>'
    )


def render_northstar(live_state):
    north_star = get(live_state, "north_star")
    return (
        '<div class="northstar">\n'
        f'  <p class="big">{esc(north_star)}</p>\n'
        '</div>'
    )


def render_glance(roadmap, live_state):
    critical_path = roadmap.get("critical_path") or []
    now_head = critical_path[0] if critical_path else NO_DATA
    battery = live_state.get("battery", {})
    run2 = battery.get("run2", {})
    target = get(battery, "target")

    founder_acts = live_state.get("founder_acts", [])
    waiting_lines = "".join(
        f'<p>{i}. {esc(get(fa, "what"))} ({esc(get(fa, "arbitration"))})</p>'
        for i, fa in enumerate(founder_acts, start=1)
    )

    remaining = battery.get("remaining", [])
    corrections = live_state.get("corrections", [])
    risk_lines = "".join(
        f'<p><span class="v warn">{esc(get(r, "kind"))}</span> <strong>{esc(get(r, "suite"))}:</strong> '
        f'{esc(get(r, "detail"))}</p>'
        for r in remaining
    )
    risk_lines += "".join(
        f'<p><strong>Correction:</strong> {esc(get(c, "finding"))}</p>' for c in corrections
    )

    totals = roadmap.get("critical_path_totals", {})
    p50 = fmt_hours(totals.get("p50_hours"))
    p80 = fmt_hours(totals.get("p80_hours"))
    note = get(totals, "note")

    waiting_body = waiting_lines if founder_acts else "<p>Nothing waiting on the founder right now.</p>"
    risk_body = risk_lines if (remaining or corrections) else "<p>No open risk items in the current data.</p>"

    return f"""<div class="glance">
  <div class="gcell now"><span class="k">Now</span>
    <p><strong>{esc(now_head)}</strong></p>
    <p>Battery target: {esc(target)}. Latest run: {esc(get(run2, "verdict"))}, {esc(get(run2, "failed_suites"))} of {esc(get(run2, "suites"))} suites failed, {esc(get(run2, "tests"))} tests, {esc(get(run2, "wall_s"))}s wall.</p></div>
  <div class="gcell wait"><span class="k">Waiting on Khalil</span>
    <p>{len(founder_acts)} founder acts pending:</p>
    {waiting_body}</div>
  <div class="gcell risk"><span class="k">Risk watch</span>
    {risk_body}</div>
  <div class="gcell"><span class="k">Forecast</span>
    <p>Critical path total: {p50}h P50 / {p80}h P80.</p>
    <p>{esc(note)}</p></div>
</div>"""


def render_standings(bench_data):
    """Where brother stands against the competition benchmark wise, read from
    docs/benchmarks/latest-run.json. Renders nothing when the file was
    absent or unparseable (bench_data is None), per the founder's own atomic
    benchmark convention: an optional input missing is not an empty box, it
    is no box.

    Dispatches on whether the loaded JSON carries a top level "scores" key
    (scripts/benchmark_atomic.py's 10 scale score, added alongside the
    borrow flow): when present, the new scored table renders, sorted purely
    by score; when absent (an older snapshot from before that field
    existed), this renders exactly the legacy PASS-count table as before,
    so an old snapshot never breaks the page."""
    if not bench_data:
        return ""
    results = bench_data.get("results") or []
    if not results:
        return ""
    scores = bench_data.get("scores")
    if scores:
        return _render_standings_scored(bench_data, scores)
    return _render_standings_legacy(bench_data, results)


def _render_standings_scored(bench_data, scores):
    """The new standings table: a subject's 10 scale score (NO-DATA excluded
    from the denominator, scripts/benchmark_atomic.py's own formula), sorted
    purely by score so brother's position is never flattered; brother is
    highlighted in bold, never repositioned ahead of a subject that
    outscored it. Followed by the reasons brother loses where it loses, and
    an open-borrow-items line pointing at docs/benchmarks/BORROW-QUEUE.md."""
    ordered = sorted(
        scores.values(),
        key=lambda r: (-(r.get("score") if r.get("score") is not None else -1), r.get("subject") or "")
    )

    row_html = []
    for r in ordered:
        subj = r.get("subject") or NO_DATA
        score = r.get("score")
        covered = r.get("covered") or 0
        total = r.get("total") or 0
        p = r.get("pass") or 0
        score_str = f"{score}/10 over {covered} of {total}" if score is not None else f"NO-DATA/10 (0 of {total} covered)"
        chip_cls = track_chip_class(p, total) if total else "open"
        name_html = f"<strong>{esc(subj)}</strong>" if subj == "brother" else esc(subj)
        row_html.append(
            f'<tr><td>{name_html}</td>'
            f'<td class="n">{esc(score_str)}</td>'
            f'<td class="n"><span class="chip {chip_cls}">PASS {p} of {total}</span></td></tr>'
        )

    out = ['<h2>Where we stand, benchmark wise</h2>']
    out.append(
        '<p class="stamp">Equal weights over 9 mechanical text checks '
        '(scripts/benchmark_atomic.py), NO-DATA excluded from the '
        'denominator: a proxy for shipped capability, not a quality '
        'verdict. A score over fewer covered checks is not one to one '
        'comparable with a fuller one, because a NO-DATA cell shrinks '
        'the denominator for that subject, which is why the covered '
        'count sits beside every score.</p>'
    )
    out.append('<div class="scroll">\n<table>')
    out.append('<tr><th>Subject</th><th class="n">Score</th><th class="n">Standings</th></tr>')
    out.extend(row_html)
    out.append('</table>\n</div>')

    reasons = bench_data.get("reasons") or []
    if reasons:
        out.append('<h3>Why they beat us where they do</h3>')
        for r in reasons:
            crit = r.get("criterion") or NO_DATA
            leaders = ", ".join(r.get("leaders") or []) or NO_DATA
            sentence = r.get("sentence") or NO_DATA
            out.append(f'<p>{esc(crit)}, {esc(leaders)}: {esc(sentence)}</p>')

    borrow_items = bench_data.get("borrow_items")
    if borrow_items is not None:
        open_items = [b for b in borrow_items if b.get("stage") != "BEATEN"]
        stage_counts = {}
        for b in borrow_items:
            st = b.get("stage") or NO_DATA
            stage_counts[st] = stage_counts.get(st, 0) + 1
        stage_line = ", ".join(
            f"{stage_counts[st]} {st}" for st in BORROW_STAGE_ORDER if st in stage_counts
        )
        out.append(
            f'<p class="stamp">Open borrow items: {len(open_items)}'
            f'{" (" + esc(stage_line) + ")" if stage_line else ""}. '
            'See docs/benchmarks/BORROW-QUEUE.md.</p>'
        )

    return "\n".join(out)


BORROW_STAGE_ORDER = ("RESEARCH", "DESIGN", "BUILD", "RE-MEASURE", "BEATEN")


def _render_standings_legacy(bench_data, results):
    """The original PASS-count table, unchanged: kept for any latest-run.json
    snapshot from before scripts/benchmark_atomic.py grew score fields, so
    an older snapshot renders exactly as it always did rather than breaking.

    Standings order is brother first, then the rest by PASS count
    descending; the honest-loss and only-brother-wins lines are computed
    from the loaded verdicts, never hardcoded, so a rerun with a changed
    result set changes what these lines name."""
    criteria = sorted({r.get("criterion") for r in results if r.get("criterion")})
    total = len(criteria)
    subjects = sorted({r.get("subject") for r in results if r.get("subject")})
    if not criteria or not subjects:
        return ""

    by_subject = {}
    for r in results:
        s, c, v = r.get("subject"), r.get("criterion"), r.get("verdict")
        if s and c:
            by_subject.setdefault(s, {})[c] = v

    def counts(subject):
        verdicts = by_subject.get(subject, {})
        p = sum(1 for c in criteria if verdicts.get(c) == "PASS")
        f = sum(1 for c in criteria if verdicts.get(c) == "FAIL")
        n = total - p - f
        return p, f, n

    ordered = [s for s in subjects if s == "brother"]
    ordered += sorted((s for s in subjects if s != "brother"), key=lambda s: (-counts(s)[0], s))

    row_html = []
    for s in ordered:
        p, f, n = counts(s)
        chip_cls = track_chip_class(p, total)
        row_html.append(
            f'<tr><td>{esc(s)}</td>'
            f'<td class="n"><span class="chip {chip_cls}">PASS {p} of {total}</span></td>'
            f'<td class="n">{f} FAIL</td><td class="n">{n} NO-DATA</td></tr>'
        )

    brother_verdicts = by_subject.get("brother", {})
    others = [s for s in subjects if s != "brother"]
    only_brother = [
        c for c in criteria
        if brother_verdicts.get(c) == "PASS"
        and all(by_subject.get(s, {}).get(c) != "PASS" for s in others)
    ]
    brother_loses = [c for c in criteria if brother_verdicts.get(c) == "FAIL"]

    win_line = (
        "Only brother passes: " + ", ".join(esc(c) for c in only_brother) + "."
        if only_brother else "No criterion in this run is a brother-only pass."
    )
    loss_line = (
        "Brother loses on: " + ", ".join(esc(c) for c in brother_loses) + "."
        if brother_loses else "Brother has no FAIL cells in this run."
    )

    caveat = (
        "Two checks (options-with-recommendation, diagram-by-default) pass "
        "competitors on loose word co-occurrence and are weaker proxies, per "
        f"{BENCHMARK_MD_NAME}; closed products are doc level only and never "
        "scored here."
    )

    sha_keys = ("sha", "commit", "commit_sha", "pinned_sha")
    sha_lines = []
    for r in results:
        sha = next((r.get(k) for k in sha_keys if r.get(k)), None)
        subj = r.get("subject")
        if sha and subj and subj != "brother":
            sha_lines.append((subj, str(sha)[:7]))
    if sha_lines:
        seen = []
        for subj, sha7 in sha_lines:
            if subj not in [s for s, _ in seen]:
                seen.append((subj, sha7))
        sha_line = "Measured at " + ", ".join(f"{sha7} ({subj})" for subj, sha7 in seen) + "."
    else:
        sha_line = f"Pinned commit shas for the cloned subjects are recorded in {BENCHMARK_MD_NAME}, not in this JSON snapshot."

    out = ['<h2>Where we stand, benchmark wise</h2>']
    out.append(
        f'<p>{esc(total)} mechanical checks (scripts/benchmark_atomic.py), each subject scored '
        'PASS, FAIL, or NO-DATA against shipped, publicly readable text only.</p>'
    )
    out.append('<div class="scroll">\n<table>')
    out.append('<tr><th>Subject</th><th class="n">Standings</th><th class="n">Fail</th>'
               '<th class="n">No-data</th></tr>')
    out.extend(row_html)
    out.append('</table>\n</div>')
    out.append(f'<p>{win_line}</p>')
    out.append(f'<p>{loss_line}</p>')
    out.append(f'<p class="stamp">{caveat}</p>')
    out.append(f'<p class="stamp">{esc(sha_line)}</p>')
    return "\n".join(out)


def render_night_watch(nw):
    """Night watch: the overnight watchdog's own last write from
    docs/plan/night-watch.json. Renders nothing when the file was absent or
    unparseable (nw is None), same convention as render_standings: an older
    snapshot or a checkout the watchdog never armed in is not an empty box,
    it is no box.

    Every field here is the watchdog's own last write, never live process
    state, which is why the caveat line at the end is load bearing and
    always renders, never dropped."""
    if not nw:
        return ""
    last_tick = nw.get("last_tick_iso")
    last_tick_text = last_tick if last_tick else "not yet ticked"
    moved = nw.get("what_moved_last_tick")
    moved_text = moved if moved else "nothing yet"
    monitor_items = "".join(
        f'<li><code>{esc(get(m, "id"))}</code>: {esc(get(m, "watches"))}</li>'
        for m in nw.get("monitors") or []
    )

    out = ['<h2>Night watch</h2>']
    out.append(
        f'<p>The overnight watchdog is armed. Cron schedule: {esc(get(nw, "cron_schedule"))}. '
        f'Hard stop: {esc(get(nw, "hard_stop_note"))}</p>'
    )
    out.append('<div class="glance">')
    out.append(f'<div class="gcell"><span class="k">Ticks done</span><p>{esc(get(nw, "ticks_done"))}</p></div>')
    out.append(f'<div class="gcell"><span class="k">Last tick</span><p>{esc(last_tick_text)}</p></div>')
    out.append(f'<div class="gcell"><span class="k">What moved last tick</span><p>{esc(moved_text)}</p></div>')
    out.append('</div>')
    out.append(f'<p>Monitors:</p>\n<ul>{monitor_items}</ul>')
    out.append(f'<p>Stall detector pid: {esc(get(nw, "stall_detector_pid"))}</p>')
    out.append(
        '<p class="stamp">This section reflects the watchdog\'s own last write, never live '
        'process state; a session end silently ages it, so a stale ticks done next to a fresh '
        'page timestamp is itself a signal worth noticing.</p>'
    )
    return "\n".join(out)


def render_advancement(live_state):
    """Advancement today: what moved, its state, and the evidence behind
    it. Renders nothing if the key is absent or empty, per the source data
    rather than an empty box. Colouring is decided by inspecting each
    state string at render time, never by position in the list."""
    items = live_state.get("advancement_today")
    if not items:
        return ""
    out = ['<h2>Advancement today</h2>']
    for a in items:
        state = get(a, "state")
        is_proven = "PROVEN" in clean(state).upper()
        chip_cls = "pass" if is_proven else "warn"
        item_cls = "item tick" if is_proven else "item"
        out.append(
            f'<div class="{item_cls}"><span class="t2"><strong>{esc(get(a, "what"))}</strong> '
            f'<span class="v {chip_cls}">{esc(state)}</span></span>'
            f'<span class="ev">{esc(get(a, "evidence"))}</span>{render_link_html(a)}</div>'
        )
    return "\n".join(out)


WAITING_MARKERS = ("BLOCK", "FOUNDER", "PARTIAL", "WAITING", "READY",
                   "NOT CLEAR", "NAMED", "OUTSTANDING")


def grid_counts(live_state):
    """(done, total) for live_state['grid']['rows'], the one place this count
    is computed, so the header badge and the collapsed-section label used in
    main() can never drift apart."""
    grid = live_state.get("grid") or {}
    rows = grid.get("rows") or []
    done = sum(1 for r in rows if "DONE" in clean(get(r, "state")).upper())
    return done, len(rows)


def wrap_collapsed(html, summary_text):
    """Fold a secondary section's already-rendered HTML behind a native
    details/summary disclosure, so a long list (advancement today, the
    execution grid) does not compete on equal footing with the nine primary
    sections a non engineer scans first. Renders nothing when the wrapped
    HTML is empty, so an absent source section still produces no empty
    shell."""
    if not html:
        return ""
    return (f'<details class="phase collapsed"><summary class="t2">{esc(summary_text)}</summary>\n'
            f'{html}\n</details>')


def render_grid(live_state):
    """The execution grid, P0 to P12: the founder's own day sequence, from
    internal convergence to one scored claim. Renders nothing when the key is
    absent, so an estate that has not adopted a grid shows no empty box.

    The state string decides the colour at render time. DONE ticks. Anything
    naming the founder or a block is warned rather than ticked, because a row
    waiting on a person is not a row in progress and must not read as one."""
    grid = live_state.get("grid")
    if not grid or not grid.get("rows"):
        return ""
    done, total = grid_counts(live_state)
    out = [f'<h2>The execution grid<span class="n">{esc(str(done))} of {esc(str(total))} done</span></h2>']
    seq = clean(grid.get("sequence"))
    if seq:
        out.append(f'<p class="lede">{esc(seq)}</p>')
    src = clean(grid.get("source"))
    if src:
        out.append(f'<p class="stamp">{esc(src)}</p>')
    collapsed_open = False
    for r in grid["rows"]:
        if r.get("collapsed") and not collapsed_open:
            out.append('<details class="phase"><summary class="t2">'
                       + esc(get(grid, "collapsed_note")) + '</summary>')
            collapsed_open = True
        elif collapsed_open and not r.get("collapsed"):
            out.append('</details>')
            collapsed_open = False
        out.append(render_grid_row(r))
    if collapsed_open:
        out.append('</details>')
    return "\n".join(out)


def render_grid_row(r):
    """One grid row. DONE ticks. A row WAITING ON A PERSON is warned, never
    left idle: waiting and not-started are different states and only one of
    them is somebody's to fix. Idle is reserved for rows nobody can start."""
    state = clean(get(r, "state")).upper()
    done = state.startswith("DONE")
    waiting = any(m in state for m in WAITING_MARKERS)
    chip = "pass" if done else ("warn" if waiting else "idle")
    item_cls = "item tick" if done else "item"
    return (
        f'<div class="{item_cls}"><span class="t2"><strong>{esc(get(r, "id"))}. '
        f'{esc(get(r, "what"))}</strong> '
        f'<span class="v {chip}">{esc(get(r, "state"))}</span></span>'
        f'<span class="ev">{esc(get(r, "evidence"))}</span></div>'
    )


def render_decisions_waiting(live_state):
    founder_acts = live_state.get("founder_acts", [])
    cards = []
    for fa in founder_acts:
        cards.append(
            '<div class="card">'
            f'<span class="who">{esc(get(fa, "id"))}</span>'
            f'<h4>{esc(get(fa, "what"))}</h4>'
            f'<p><strong>{esc(get(fa, "arbitration"))}</strong></p>'
            f'<p>{esc(get(fa, "why"))}</p>'
            '</div>'
        )
    return '<h2>Decisions waiting on the founder</h2>\n<div class="cards">\n' + "\n".join(cards) + '\n</div>'


def render_risks(roadmap, live_state, short_by_id, mid_by_id):
    battery = live_state.get("battery", {})
    remaining = battery.get("remaining", [])
    corrections = live_state.get("corrections", [])
    rows = roadmap.get("rows", [])
    blockers = [r for r in rows if r.get("disposition") == "BLOCKER"]

    blocks = []
    for r in live_state.get("risks") or []:
        level = clean(get(r, "level")).lower()
        cls = level if level in ("hot", "warm", "cool") else "warm"
        blocks.append(
            f'<div class="risk {cls}">'
            f'<h4>{esc(get(r, "title"))}</h4>'
            f'<p><strong>What:</strong> {esc(get(r, "what"))}</p>'
            f'<p><strong>Why it matters:</strong> {esc(get(r, "why"))}</p>'
            f'<p class="act"><strong>Action:</strong> {esc(get(r, "action"))}</p>'
            f'<p><strong>When:</strong> {esc(get(r, "when"))}</p>'
            f'{render_link_html(r)}'
            '</div>'
        )
    for item in remaining:
        blocks.append(
            '<div class="risk warm">'
            f'<h4>{esc(get(item, "suite"))} ({esc(get(item, "kind"))})</h4>'
            f'<p><strong>What:</strong> a battery suite in the observed run. </p>'
            f'<p><strong>Why it matters:</strong> {esc(get(item, "detail"))}</p>'
            f'<p class="act"><strong>Action:</strong> {NO_DATA}</p>'
            f'<p><strong>When:</strong> {NO_DATA}</p>'
            '</div>'
        )
    for c in corrections:
        blocks.append(
            '<div class="risk cool">'
            f'<h4>{esc(get(c, "claim"))}</h4>'
            f'<p><strong>What:</strong> a claim checked this session and found wrong.</p>'
            f'<p><strong>Why it matters:</strong> {esc(get(c, "finding"))}</p>'
            f'<p class="act"><strong>Action:</strong> {NO_DATA}</p>'
            f'<p><strong>When:</strong> {NO_DATA}</p>'
            '</div>'
        )
    for row in blockers:
        rid = row.get("id", NO_DATA)
        when = NO_DATA
        if rid in short_by_id:
            s = short_by_id[rid]
            when = f"day {get(s, 'start_day')} to day {get(s, 'end_day_p80')}"
        elif rid in mid_by_id:
            s = mid_by_id[rid]
            when = f"week {get(s, 'start_week')} to week {get(s, 'end_week_p80')}"
        blocks.append(
            '<div class="risk hot">'
            f'<h4>{esc(rid)}</h4>'
            f'<p><strong>What:</strong> disposition BLOCKER, track {esc(get(row, "track"))}.</p>'
            f'<p><strong>Why it matters:</strong> {esc(get(row, "reason"))}</p>'
            f'<p class="act"><strong>Action:</strong> {esc(get(row, "done_check"))}</p>'
            f'<p><strong>When:</strong> {esc(when)}</p>'
            '</div>'
        )
    return '<h2>Risk management, insights and alerts</h2>\n' + "\n".join(blocks)


def render_row_conditions(row):
    """A row that starts on a CONDITION rather than a date carries that
    condition in `trigger`, and what would cancel it in `flip_condition`.
    Both render when present. A trigger recorded where nobody can see it is
    the same as no trigger: it is how a conditional row quietly never starts."""
    out = ""
    if row.get("trigger"):
        out += "\nSTARTS WHEN: " + esc(clean(row["trigger"]))
    if row.get("flip_condition"):
        out += "\nDROP IT IF: " + esc(clean(row["flip_condition"]))
    return out


def render_ledger(roadmap, title_map):
    rows = roadmap.get("rows", [])
    by_track = {t: [] for t in TRACKS}
    for row in rows:
        by_track.setdefault(row.get("track", NO_DATA), []).append(row)

    out = ['<h2>The ledger</h2>']
    out.append(f'<p class="stamp">{len(rows)} roadmap rows grouped by track, A to F, each track folded '
                f'shut by default. {esc(TICK_CONTRACT)}</p>')
    for track in TRACKS:
        items = by_track.get(track, [])
        if not items:
            continue
        ticked = sum(1 for r in items if r.get("disposition") == "DONE")
        chip = track_chip_class(ticked, len(items))
        out.append(f'<details class="phase"><summary class="h"><h3>{esc(track)}</h3>'
                    f'<span class="chip {chip}">{ticked}/{len(items)}</span></summary>')
        for row in items:
            is_tick = row.get("disposition") == "DONE"
            title = resolve_title(row, title_map)
            cls = "item tick" if is_tick else "item"
            out.append(
                f'<div class="{cls}"><span class="t2">{esc(row.get("id", NO_DATA))}: {esc(title)} '
                f'({esc(get(row, "disposition"))})</span>'
                f'<span class="ev">{esc(get(row, "done_check"))}'
                + render_row_conditions(row) + '</span></div>'
            )
        out.append('</details>')
    return "\n".join(out)


def render_decisions_recorded(roadmap):
    decisions = roadmap.get("founder_decisions_taken", [])
    cards = []
    for d in decisions:
        rows_affected = d.get("rows_affected") or []
        extra = ""
        if rows_affected:
            extra += f'<p>Rows affected: {esc(", ".join(rows_affected))}</p>'
        if d.get("disposition_applied"):
            extra += f'<p>Disposition applied: {esc(d["disposition_applied"])}</p>'
        cards.append(
            '<div class="card">'
            f'<span class="who">{esc(get(d, "when"))}</span>'
            f'<h4>{esc(get(d, "decision"))}</h4>'
            f'{extra}'
            '</div>'
        )
    return '<h2>Decisions recorded</h2>\n<div class="cards">\n' + "\n".join(cards) + '\n</div>'


def render_footer(live_state):
    session = get(live_state, "session")
    measured_at = get(live_state, "measured_at")
    return (
        '<footer>\n'
        f'<p class="stamp"><strong>Tick contract, verbatim:</strong> {esc(TICK_CONTRACT)} '
        'A number without a command behind it is a claim, and this page marks it as one.</p>\n'
        f'<p class="stamp">Refreshed {esc(measured_at)}, session {esc(session)}. Source of truth: '
        'docs/plan/ROADMAP-2026-08-23.json, docs/plan/QUEUE.json, docs/plan/LIVE-STATE.json. '
        'Generated by scripts/gen_command_center.py, never hand edited. Sole author: Khalil Maaouni.</p>\n'
        '</footer>'
    )


def run_md_mode():
    """The --md mode: LIVE-STATE.json in, COMMAND-CENTER.md out. Separate
    from the HTML flow, its own read and its own write, so the default
    invocation's output cannot be touched by this mode's existence."""
    live_state = load_json(LIVE_STATE_PATH)
    text = render_markdown(live_state)
    try:
        MD_OUTPUT_PATH.write_text(text, encoding="utf-8")
    except OSError as exc:
        print(f"ERROR: could not write {MD_OUTPUT_PATH}: {exc}", file=sys.stderr)
        sys.exit(1)
    repo_rows = len(live_state.get("repos") or [])
    pr_rows = len(live_state.get("pull_requests") or [])
    risk_rows = len(live_state.get("risks") or [])
    advancement_rows = len(live_state.get("advancement_today") or [])
    print(
        f"gen_command_center --md: {repo_rows} repos, {pr_rows} pull requests, "
        f"{risk_rows} risks, {advancement_rows} advancement rows rendered -> {MD_OUTPUT_PATH}"
    )


def main():
    args = sys.argv[1:]
    allowed_flags = {"--md"}
    unknown = [a for a in args if a not in allowed_flags]
    if unknown:
        print(f"ERROR: unknown argument(s): {' '.join(unknown)}. Allowed: --md", file=sys.stderr)
        sys.exit(1)
    if "--md" in args:
        run_md_mode()
        return

    roadmap = load_json(ROADMAP_PATH)
    queue = load_json(QUEUE_PATH)
    live_state = load_json(LIVE_STATE_PATH)
    narrative = load_json(NARRATIVE_PATH)

    if not isinstance(queue, list):
        print(f"ERROR: expected {QUEUE_PATH} to be a JSON list", file=sys.stderr)
        sys.exit(1)

    rows = roadmap.get("rows", [])
    rows_by_id = {r["id"]: r for r in rows if "id" in r}
    short_by_id = {i["id"]: i for i in roadmap.get("short", []) if "id" in i}
    mid_by_id = {i["id"]: i for i in roadmap.get("mid", []) if "id" in i}
    title_map = build_title_map(roadmap, queue)

    complaint_rows = narrative.get("complaints", {}).get("rows", [])
    benchmark_rows = narrative.get("benchmark", {}).get("rows", [])
    score_cards = narrative.get("benchmark", {}).get("three_scores", {}).get("cards", [])
    bench_run = load_benchmark_run()
    standings_subjects = len({r.get("subject") for r in (bench_run or {}).get("results", []) if r.get("subject")})
    night_watch = load_night_watch()

    # The four ranges are the page's visual core (the progress page law, item
    # 4): today at hour grain, then short/mid/long, boxed together in one
    # "<div class=ranges>" so they read as one component instead of four
    # sections the same weight as everything else. Advancement today and the
    # execution grid are today-adjacent detail, not named in the law's nine
    # items, so they fold shut by default (wrap_collapsed) rather than
    # burying the four ranges under their own row counts.
    advancement_count = len(live_state.get("advancement_today") or [])
    grid_done, grid_total = grid_counts(live_state)
    ranges_block = (
        '<h2>The four ranges</h2>\n'
        '<p class="stamp">Today at hour grain, then short (days), medium (weeks) and long '
        '(quarters): solid to P50, faint tail to P80, hatched where scheduled.</p>\n'
        '<div class="ranges">\n'
        + render_day_gantt(live_state) + "\n\n"
        + wrap_collapsed(render_advancement(live_state),
                          f"Advancement today ({advancement_count} rows), click to expand") + "\n\n"
        + wrap_collapsed(render_grid(live_state),
                          f"Execution grid, P0 to P12 ({grid_done}/{grid_total} done), click to expand") + "\n\n"
        + render_short_gantt(roadmap, rows_by_id) + "\n\n"
        + render_mid_gantt(roadmap, rows_by_id) + "\n\n"
        + render_long_gantt(roadmap)
        + '\n</div>'
    )

    # Team complaints, self benchmarking, the competitive field and the three
    # score cards are supporting analysis, not named in the law's nine items
    # either; bundled and folded shut for the same reason.
    supporting_analysis = wrap_collapsed(
        "\n\n".join(s for s in (
            render_complaints(narrative), render_benchmark(narrative),
            render_competitive(narrative), render_three_scores(narrative),
        ) if s),
        "Team complaints and self benchmarking (secondary detail), click to expand"
    )

    sections = [
        render_header(live_state),
        render_northstar(live_state),
        render_glance(roadmap, live_state),
        render_graph_loops(live_state),
        render_standings(bench_run),
        render_night_watch(night_watch),
        ranges_block,
        render_decisions_waiting(live_state),
        render_risks(roadmap, live_state, short_by_id, mid_by_id),
        render_work_breakdown(roadmap, narrative),
        render_ledger(roadmap, title_map),
        supporting_analysis,
        render_decisions_recorded(roadmap),
        render_footer(live_state),
    ]

    body = "\n\n".join(sections)
    page = f"<title>Brother Command Center</title>\n<style>{STYLE_CSS}</style>\n\n<div class=\"wrap\">\n\n{body}\n\n</div>\n"

    try:
        OUTPUT_PATH.write_text(page, encoding="utf-8")
    except OSError as exc:
        print(f"ERROR: could not write {OUTPUT_PATH}: {exc}", file=sys.stderr)
        sys.exit(1)

    wbs_text = render_wbs_markdown(live_state, roadmap)
    try:
        WBS_OUTPUT_PATH.write_text(wbs_text, encoding="utf-8")
    except OSError as exc:
        print(f"ERROR: could not write {WBS_OUTPUT_PATH}: {exc}", file=sys.stderr)
        sys.exit(1)

    gantt_bars = len(roadmap.get("short", [])) + len(roadmap.get("mid", [])) + len(roadmap.get("long", []))
    advancement_rows = len(live_state.get("advancement_today") or [])
    day_rows = len((live_state.get("day_plan") or {}).get("rows") or [])
    night_watch_status = "found" if night_watch else "absent"
    print(
        f"gen_command_center: {len(rows)} roadmap rows, {len(queue)} queue rows, "
        f"{gantt_bars} gantt bars, {day_rows} day rows, {len(complaint_rows)} complaint rows, "
        f"{len(benchmark_rows)} benchmark rows, {len(score_cards)} score cards, "
        f"{advancement_rows} advancement rows, {standings_subjects} standings subjects, "
        f"night watch {night_watch_status} "
        f"rendered -> {OUTPUT_PATH}, {WBS_OUTPUT_PATH}"
    )


if __name__ == "__main__":
    main()

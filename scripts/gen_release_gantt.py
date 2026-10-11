#!/usr/bin/env python3
"""Render a release WBS (docs/plan/BROTHER-<version>-WBS.json) into its Gantt page.

Nobody edits the page: edit the JSON and re-run. Two Gantts, as the progress page
law orders: TODAY by the hour (lanes machine, session, owner) and the RELEASE on a
calendar, one bar per unit. The counting rule is board_status.py's: a unit counts
DONE only when it says DONE and carries evidence; DONE without evidence is a CLAIM,
named, never counted. A wave with no units reads NO-DATA, never 0 percent.

  python3 scripts/gen_release_gantt.py <wbs.json> <out.html>
  python3 scripts/gen_release_gantt.py --check <wbs.json>   (schedule: dependencies ordered, no lane overlap)
  python3 scripts/gen_release_gantt.py --selftest
Exit 0 rendered; 2 NO-DATA (WBS unreadable or holds no units). Standard library, Python 3.9 floor.
"""
import datetime as dt
import html
import json
import sys

E = html.escape
STATES = ["DONE", "CLAIM", "IN-FLIGHT", "PARTIAL", "SPECIFIED", "OPEN", "BLOCKED", "DEFERRED"]
TICK = ("A box ticks only when its done check ran after the last edit and its output is quoted "
        "beside it. Percentages are counts of records, never impressions.")


def state(u):
    s = str(u.get("state") or "OPEN").upper()
    if s == "DONE" and not str(u.get("evidence") or "").strip():
        return "CLAIM"
    return s if s in STATES else "OPEN"


def mins(hhmm):
    h, m = str(hhmm).split(":")
    return int(h) * 60 + int(m)


def clock(hhmm):
    """A window may run past midnight ("26:30" is 02:30 tomorrow): the label shows the clock time."""
    t = mins(hhmm) % 1440
    return "%02d:%02d" % (t // 60, t % 60)
def day(s):
    return dt.date.fromisoformat(s)


def gantt_today(today, now):
    rows = today.get("rows") or []
    if not rows:
        return '<p class="nodata">NO-DATA: no rows planned for today</p>'
    t0, t1 = mins(today["window"][0]), mins(today["window"][1])
    span = float(max(t1 - t0, 1))
    pct = lambda t: max(0.0, min(100.0, 100.0 * (mins(t) - t0) / span))
    axis = "".join('<span style="left:%.2f%%">%02d:00</span>' % (100.0 * (h * 60 - t0) / span, h % 24)
                   for h in range(t0 // 60, t1 // 60 + 1, 2))
    if mins(now) < t0 and t1 > 1440:
        now = "%d:%02d" % (mins(now) // 60 + 24, mins(now) % 60)
    mark = ('<i class="now" style="left:%.2f%%"></i>' % pct(now)) if t0 <= mins(now) <= t1 else ""
    out, lane = [], None
    for r in rows:
        if r.get("lane") != lane:
            lane = r.get("lane")
            out.append('<div class="lane">%s</div>' % E(str(lane)))
        a, b = pct(r["start"]), pct(r["end"])
        tip = "%s to %s%s. %s" % (clock(r["start"]), clock(r["end"]), "" if r.get("measured") else " (times after now are estimates)", r.get("note", ""))
        out.append('<div class="grow"><span class="gl">%s</span><div class="gt">%s<b class="bar k-%s" style="left:%.2f%%;width:%.2f%%" title="%s"></b></div><span class="gw">%s to %s</span></div>'
                   % (E(r["label"]), mark, E(r.get("status", "planned")), a, max(b - a, 0.8), E(tip), E(clock(r["start"])), E(clock(r["end"]))))
    return '<div class="axis">%s</div>%s' % (axis, "".join(out))


def gantt_release(wbs):
    cal = wbs.get("calendar") or {}
    units = wbs.get("units") or []
    if not cal.get("start") or not units:
        return '<p class="nodata">NO-DATA: no calendar or no units</p>'
    c0, c1 = day(cal["start"]), day(cal["end"])
    span = float((c1 - c0).days + 1)
    pos = lambda d: 100.0 * (day(d) - c0).days / span
    weeks, d = [], c0
    while d <= c1:
        if d.weekday() == 0 or d == c0:
            weeks.append('<span style="left:%.2f%%">%s</span>' % (pos(d.isoformat()), d.strftime("%b %d")))
        d += dt.timedelta(days=1)
    today = dt.date.today().isoformat()
    mark = ('<i class="now" style="left:%.2f%%"></i>' % pos(today)) if cal["start"] <= today <= cal["end"] else ""
    out = []
    for w in wbs.get("waves") or []:
        us = [u for u in units if u.get("wave") == w["id"]]
        if not us:
            out.append('<div class="lane">%s <em class="nodata">NO-DATA: no units</em></div>' % E(w["title"]))
            continue
        done = sum(state(u) == "DONE" for u in us)
        out.append('<div class="lane">%s <em>%d/%d done, %s</em></div>' % (E(w["title"]), done, len(us), E(w.get("window", ""))))
        for u in us:
            a = pos(u["plan_start"])
            b = 100.0 * ((day(u["plan_end"]) - c0).days + 1) / span
            st = state(u).lower()
            hand = ' <span class="hand">your hand</span>' if u.get("owner_hand") else ""
            out.append('<div class="grow"><span class="gl"><code>%s</code> %s%s</span><div class="gt">%s<b class="bar s-%s lane-%s" style="left:%.2f%%;width:%.2f%%" title="%s"></b></div>'
                       '<span class="gw">%s to %s, %s, lane %s</span></div>' % (
                           E(u["id"]), E(u["title"]), hand, mark, E(st), E(str(u.get("lane", ""))), a, max(b - a, 0.9),
                           E("%s: %s to %s (estimate)" % (u["id"], u["plan_start"], u["plan_end"])),
                           E(u["plan_start"][5:]), E(u["plan_end"][5:]), E(u.get("size", "?")), E(str(u.get("lane", "?")))))
    cut = wbs.get("cut_band")
    band = ""
    if cut:
        a, b = pos(cut[0]), 100.0 * ((day(cut[1]) - c0).days + 1) / span
        band = ('<div class="grow"><span class="gl"><b>Cut window (estimate)</b></span><div class="gt">%s<b class="bar band" style="left:%.2f%%;width:%.2f%%"></b></div><span class="gw">%s to %s</span></div>'
                % (mark, a, b - a, E(cut[0][5:]), E(cut[1][5:])))
    return '<div class="axis">%s</div>%s%s<p class="basis">Basis: %s</p>' % ("".join(weeks), "".join(out), band, E(cal.get("basis", "NO-DATA")))


def check_schedule(wbs):
    """Every violation of the plan's own schedule, as sentences: a unit that starts before a unit it depends on ends,
    or two units sharing lane A or B on one day. DEFERRED units are left out; the spare lane may overlap by design."""
    units = {u["id"]: u for u in wbs.get("units") or [] if state(u) != "DEFERRED"}
    bad = []
    for u in units.values():
        for dep in u.get("depends_on") or []:
            if dep in units and day(u["plan_start"]) <= day(units[dep]["plan_end"]):
                bad.append("ORDER %s starts %s but depends on %s ending %s" % (u["id"], u["plan_start"], dep, units[dep]["plan_end"]))
    for lane in ("A", "B"):
        us = sorted((u for u in units.values() if u.get("lane") == lane), key=lambda u: u["plan_start"])
        for a, b in zip(us, us[1:]):
            if day(b["plan_start"]) <= day(a["plan_end"]):
                bad.append("LANE %s: %s (%s to %s) overlaps %s (%s to %s)" % (lane, a["id"], a["plan_start"], a["plan_end"], b["id"], b["plan_start"], b["plan_end"]))
    return bad


def unit_card(u):
    st = state(u)
    claim = '<p class="claim">CLAIM: says DONE with no evidence, kept out of every count.</p>' if st == "CLAIM" else ""
    hand = '<span class="hand">your hand</span>' if u.get("owner_hand") else ""
    li = lambda xs: "".join("<li><code>%s</code></li>" % E(x) for x in xs or []) or "<li>none declared</li>"
    rows = [("Objective", u.get("objective")), ("User outcome it proves", u.get("user_outcome")), ("Done check", u.get("done_check")),
            ("Evidence", u.get("evidence") or "none yet"), ("Depends on", ", ".join(u.get("depends_on") or []) or "none"),
            ("Remains", u.get("remains")), ("Source", u.get("source"))]
    dl = "".join("<dt>%s</dt><dd>%s</dd>" % (E(k), E(str(v or "NO-DATA"))) for k, v in rows)
    return ('<details class="unit s-%s"><summary><span class="uid">%s</span><span class="ut">%s %s</span><span class="st">%s</span></summary>'
            '<div class="body">%s<dl>%s</dl><p class="lbl">Owns</p><ul>%s</ul></div></details>') % (
        st, E(u["id"]), E(u["title"]), hand, st.lower(), claim, dl, li(u.get("owns")))


def cards(items, keys):
    if not items:
        return '<div class="card"><p class="nodata">NO-DATA: nothing recorded here</p></div>'
    return "".join('<div class="card"><h4>%s</h4>%s</div>' % (E(c.get("title", "")), "".join(
        "<p><b>%s:</b> %s</p>" % (E(k.replace("_", " ").capitalize()), E(str(c[k]))) for k in keys if c.get(k))) for c in items)


def render(wbs, now=None):
    now = now or dt.datetime.now().strftime("%H:%M")
    units = wbs["units"]
    c = {s: sum(state(u) == s for u in units) for s in STATES}
    chips = "".join('<span class="chip">%s <b>%d</b></span>' % (s.lower(), c[s]) for s in STATES if c[s])
    fams = {}
    for u in units:
        fams.setdefault(u.get("family", "Other"), []).append(u)
    ledger = "".join('<section class="fam"><h3>%s <span class="n">%d/%d done</span></h3>%s</section>' % (
        E(f), sum(state(u) == "DONE" for u in us), len(us), "".join(unit_card(u) for u in us)) for f, us in fams.items())
    lst = lambda xs: "".join("<li>%s</li>" % E(x) for x in xs or []) or '<li class="nodata">NO-DATA</li>'
    fold = "".join("<tr><td>%s</td><td>%s</td></tr>" % (E(f["point"]), E(f["ruling"])) for f in wbs.get("critique_fold_in") or [])
    g = wbs.get("glance") or {}
    glance = "".join('<div class="glance"><h4>%s</h4><p>%s</p></div>' % (E(k), E(g.get(k) or "NO-DATA"))
                     for k in ("NOW", "WAITING ON KHALIL", "RISK WATCH", "FORECAST"))
    stamp = dt.datetime.now().astimezone().strftime("%Y-%m-%d %H:%M %Z")
    return PAGE % {
        "title": E(wbs.get("initiative", "Release plan")), "eyebrow": E(wbs.get("eyebrow", "")), "stamp": E(stamp),
        "asof": E(wbs.get("as_of", "NO-DATA")), "ver": E(wbs.get("version_of_plan", "")), "north": E(wbs.get("north_star", "NO-DATA")),
        "journey": E(wbs.get("the_journey", "NO-DATA")), "glance": glance,
        "today_title": E((wbs.get("today") or {}).get("title", "Today")), "g1": gantt_today(wbs.get("today") or {}, now),
        "g2": gantt_release(wbs), "waiting": cards(wbs.get("decisions_waiting"), ("question", "recommended", "why", "flip")),
        "risks": cards(wbs.get("risks"), ("why", "action", "when")), "chips": chips, "n": len(units), "ledger": ledger,
        "made": lst(wbs.get("made")), "deferred": lst(wbs.get("deferred")), "fold": fold or '<tr><td colspan="2">NO-DATA</td></tr>',
        "recorded": cards(wbs.get("decisions_recorded"), ("words", "by", "when", "flip")),
        "sources": lst(wbs.get("sources")), "tick": E(TICK)}


PAGE = """<!doctype html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>%(title)s</title><style>
:root{--paper:#F7F8F6;--ink:#141B22;--mute:#5d6a72;--line:#d9dedb;--card:#fff;--petrol:#0E7A6F;--petrol-soft:#d5ebe7;--amber:#a86a08;--amber-soft:#f6e7c8;--red:#a43a2c;--blue:#2d5f93}
@media (prefers-color-scheme:dark){:root:not([data-theme="light"]){--paper:#141B22;--ink:#e8ecea;--mute:#9aa7ad;--line:#2b353d;--card:#1b242c;--petrol:#3AA893;--petrol-soft:#1d3a36;--amber:#e0ac4a;--amber-soft:#3b3017;--red:#e58574;--blue:#7fb0e3}}
:root[data-theme="dark"]{--paper:#141B22;--ink:#e8ecea;--mute:#9aa7ad;--line:#2b353d;--card:#1b242c;--petrol:#3AA893;--petrol-soft:#1d3a36;--amber:#e0ac4a;--amber-soft:#3b3017;--red:#e58574;--blue:#7fb0e3}
body{margin:0;background:var(--paper);color:var(--ink);font:15px/1.55 Seravek,"Gill Sans Nova","Segoe UI",system-ui,sans-serif;padding:28px 16px 60px}
.wrap{max-width:1180px;margin:0 auto;display:flex;flex-direction:column;gap:28px}
h1,h2,h3,h4{font-family:"Iowan Old Style","Palatino Linotype",Palatino,Georgia,serif;font-weight:600;margin:0}
h1{font-size:clamp(26px,4.4vw,38px);line-height:1.12}h2{font-size:22px;padding-bottom:8px;border-bottom:1px solid var(--line);margin-bottom:10px}h3{font-size:17px;display:flex;justify-content:space-between;gap:12px;margin:14px 0 6px}
.eyebrow{font-size:12px;letter-spacing:.14em;text-transform:uppercase;color:var(--petrol);font-weight:600}.stamp,.basis{color:var(--mute);font-size:13px}
.north,.journey{background:var(--petrol-soft);border-left:4px solid var(--petrol);padding:12px 16px;border-radius:6px}.journey{background:var(--card);border-left-color:var(--amber)}
.glances,.cards{display:grid;grid-template-columns:repeat(auto-fit,minmax(240px,1fr));gap:12px}.glance,.card{background:var(--card);border:1px solid var(--line);border-radius:8px;padding:12px 14px}
.glance h4{font-size:12px;letter-spacing:.1em;text-transform:uppercase;color:var(--mute)}.card h4{font-size:16px;margin-bottom:4px}.card p{margin:4px 0;font-size:14px}
.axis{position:relative;height:18px;margin-left:min(36%%,330px);margin-right:120px;font-size:11px;color:var(--mute)}.axis span{position:absolute;transform:translateX(-10%%);white-space:nowrap}
.lane{font-size:12px;letter-spacing:.1em;text-transform:uppercase;color:var(--petrol);margin:14px 0 2px;font-weight:600}.lane em{text-transform:none;letter-spacing:0;color:var(--mute);font-style:normal;font-weight:400}
.grow{display:flex;align-items:center;gap:10px;margin:3px 0}.gl{flex:0 0 min(36%%,320px);font-size:13px;line-height:1.3}.gw{flex:0 0 110px;font-size:11px;color:var(--mute)}
.gt{position:relative;flex:1 1 auto;height:16px;background:var(--line);border-radius:4px}
.bar{position:absolute;top:2px;bottom:2px;border-radius:3px;min-width:4px}
.k-done,.s-done{background:var(--petrol)}.k-running,.s-in-flight,.s-partial{background:var(--blue)}
.k-planned,.s-open,.s-specified{background:repeating-linear-gradient(45deg,var(--amber-soft),var(--amber-soft) 4px,var(--amber) 4px,var(--amber) 6px)}
.lane-B.s-open{background:repeating-linear-gradient(45deg,var(--petrol-soft),var(--petrol-soft) 4px,var(--petrol) 4px,var(--petrol) 6px)}
.s-claim,.s-blocked{background:var(--red)}.band{background:repeating-linear-gradient(90deg,var(--red),var(--red) 2px,transparent 2px,transparent 6px);opacity:.6}
.now{position:absolute;top:-3px;bottom:-3px;width:2px;background:var(--red);z-index:2}
.legend{font-size:12px;color:var(--mute);display:flex;gap:14px;flex-wrap:wrap}.legend i{display:inline-block;width:18px;height:10px;border-radius:2px;margin-right:4px;vertical-align:middle}
.chip{display:inline-block;margin:0 6px 6px 0;padding:3px 10px;border-radius:99px;background:var(--card);border:1px solid var(--line);font-size:13px}
details.unit{background:var(--card);border:1px solid var(--line);border-radius:8px;margin:6px 0}details.unit summary{display:flex;gap:10px;padding:8px 12px;cursor:pointer;align-items:baseline}
.uid{font-family:ui-monospace,Menlo,monospace;font-size:12px;color:var(--petrol);min-width:44px}.ut{flex:1}.st{font-size:12px;text-transform:uppercase;color:var(--mute)}
.body{padding:0 14px 12px}dl{display:grid;grid-template-columns:max-content 1fr;gap:4px 12px;font-size:13px}dt{color:var(--mute)}code{font-size:12px;word-break:break-word}
.hand{font-size:11px;background:var(--amber-soft);color:var(--amber);padding:1px 6px;border-radius:4px;margin-left:4px;white-space:nowrap}.claim,.nodata{color:var(--red)}
table{border-collapse:collapse;width:100%%;font-size:14px}td{border-top:1px solid var(--line);padding:6px 8px;vertical-align:top}td:first-child{width:40%%}
ul.cols{columns:2 320px;font-size:14px}footer{color:var(--mute);font-size:12px;border-top:1px solid var(--line);padding-top:12px}
@media (max-width:700px){.grow{flex-wrap:wrap}.gl,.gw{flex-basis:100%%}.axis{margin:0}}
</style></head><body><div class="wrap">
<header><div class="eyebrow">%(eyebrow)s</div><h1>%(title)s</h1><p class="stamp">Rendered %(stamp)s from the WBS as of %(asof)s. %(ver)s</p></header>
<div class="north"><b>North star.</b> %(north)s</div>
<div class="journey"><b>The one journey 1.1.1 delivers.</b> %(journey)s</div>
<div class="glances">%(glance)s</div>
<section><h2>%(today_title)s</h2><div class="legend"><span><i class="k-done"></i>done</span><span><i class="k-running"></i>running</span><span><i class="k-planned"></i>planned</span><span><i style="background:var(--red)"></i>now</span></div>%(g1)s</section>
<section><h2>The full 1.1.1 Gantt</h2><div class="legend"><span><i class="s-open"></i>lane A, planned</span><span><i class="lane-B s-open"></i>lane B, planned</span><span><i class="s-partial"></i>partly built</span><span><i class="band"></i>cut window</span></div>%(g2)s</section>
<section><h2>Decisions waiting on you</h2><div class="cards">%(waiting)s</div></section>
<section><h2>Risks and alerts</h2><div class="cards">%(risks)s</div></section>
<section><h2>The ledger: %(n)s units</h2><div>%(chips)s</div>%(ledger)s</section>
<section><h2>How the outside critique changed the plan</h2><table>%(fold)s</table></section>
<section><h2>Already made</h2><ul class="cols">%(made)s</ul></section>
<section><h2>Not in 1.1.1</h2><ul class="cols">%(deferred)s</ul></section>
<section><h2>Decisions recorded</h2><div class="cards">%(recorded)s</div></section>
<section><h2>Sources</h2><ul>%(sources)s</ul></section>
<footer>Tick contract: %(tick)s</footer></div></body></html>"""


def selftest():
    import os
    import tempfile
    base = {"initiative": "T", "calendar": {"start": "2026-10-10", "end": "2026-10-20"},
            "waves": [{"id": 1, "title": "W1"}, {"id": 2, "title": "W2"}],
            "today": {"window": ["12:00", "18:00"], "rows": [{"lane": "m", "label": "r", "start": "12:00", "end": "13:00", "status": "running"}]},
            "units": [{"id": "U1", "title": "a", "state": "DONE", "evidence": "rc=0 quoted", "wave": 1, "plan_start": "2026-10-11", "plan_end": "2026-10-12"},
                      {"id": "U2", "title": "b", "state": "DONE", "evidence": "", "wave": 1, "plan_start": "2026-10-12", "plan_end": "2026-10-13"}]}
    n = 0
    assert state(base["units"][0]) == "DONE" and state(base["units"][1]) == "CLAIM"; n += 1
    page = render(base, now="12:30")
    assert "U1" in page and "U2" in page; n += 1
    assert "CLAIM: says DONE" in page; n += 1
    assert "1/2 done" in gantt_release(base); n += 1
    assert "NO-DATA: no units" in gantt_release(base); n += 1
    assert chr(0x2014) not in page and chr(0x2013) not in page; n += 1
    d = tempfile.mkdtemp()
    bad = os.path.join(d, "bad.json")
    with open(bad, "w") as fh:
        fh.write("{not json")
    assert main([bad, os.path.join(d, "o.html")]) == 2; n += 1
    empty = os.path.join(d, "e.json")
    with open(empty, "w") as fh:
        fh.write('{"units": []}')
    assert main([empty, os.path.join(d, "o.html")]) == 2; n += 1
    ok = {"units": [{"id": "A1", "lane": "A", "plan_start": "2026-10-11", "plan_end": "2026-10-12"},
                    {"id": "A2", "lane": "A", "plan_start": "2026-10-13", "plan_end": "2026-10-14", "depends_on": ["A1"]}]}
    assert check_schedule(ok) == []; n += 1
    early = {"units": [ok["units"][0], dict(ok["units"][1], plan_start="2026-10-12")]}
    assert [v for v in check_schedule(early) if v.startswith("ORDER A2")], "a unit starting before its dependency ends"; n += 1
    clash = {"units": [ok["units"][0], dict(ok["units"][1], depends_on=[], plan_start="2026-10-12")]}
    assert [v for v in check_schedule(clash) if v.startswith("LANE A")], "two units on lane A the same day"; n += 1
    spare = {"units": [dict(u, lane="spare") for u in clash["units"]]}
    assert check_schedule(spare) == [], "the spare lane may overlap"; n += 1
    night = {"window": ["20:00", "42:00"], "rows": [{"lane": "m", "label": "late", "start": "26:30", "end": "27:15", "status": "planned"}]}
    drawn = gantt_today(night, "01:00")
    assert ">02:30 to 03:15<" in drawn and "26:30" not in drawn, "a row past midnight shows its clock time"; n += 1
    assert 'class="now"' in drawn and 'class="now"' not in gantt_today(night, "19:00"), "the now mark wraps past midnight"; n += 1
    print("selftest OK: %d cases" % n)
    return 0


def main(argv):
    if argv == ["--selftest"]:
        return selftest()
    if len(argv) == 2 and argv[0] == "--check":
        try:
            with open(argv[1], encoding="utf-8") as fh:
                wbs = json.load(fh)
        except (OSError, ValueError) as exc:
            print("NO-DATA: WBS unreadable: %s" % exc)
            return 2
        if not isinstance(wbs, dict) or not wbs.get("units"):
            print("NO-DATA: the WBS holds no units")
            return 2
        bad = check_schedule(wbs)
        print("\n".join(bad) or "PASS: %d units, every dependency ordered, no lane overlap" % len(wbs["units"]))
        return 1 if bad else 0
    if len(argv) != 2:
        print(__doc__)
        return 2
    try:
        with open(argv[0], encoding="utf-8") as fh:
            wbs = json.load(fh)
    except (OSError, ValueError) as exc:
        print("NO-DATA: WBS unreadable: %s" % exc)
        return 2
    if not isinstance(wbs, dict) or not wbs.get("units"):
        print("NO-DATA: the WBS holds no units")
        return 2
    page = render(wbs)
    with open(argv[1], "w", encoding="utf-8") as fh:
        fh.write(page)
    c = {s: sum(state(u) == s for u in wbs["units"]) for s in STATES}
    print("wrote %s, %d bytes, %d units: %s" % (argv[1], len(page), len(wbs["units"]), ", ".join("%s %d" % (k, v) for k, v in c.items() if v)))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))

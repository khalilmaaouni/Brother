#!/usr/bin/env python3
"""Render the sub unit board: one square per sub unit, coloured by state."""
import glob, json, os, re, subprocess, sys, html

# THE BOARD BUILDS ITS OWN DATA, 2026-09-21. It used to read /tmp/gantt-data.json, a file NOTHING in the
# repository wrote: the only mention of that path was the line that opened it. So the board was reproducible
# only for as long as somebody's hand made temp file survived, and a reboot that morning deleted it, which left
# a generator that could not be run at all. A page whose input nobody produces is a one off with extra steps.
# The state is derived here from the three sources that already hold it: the plan, the run evidence, and the
# spec scores. Pass --data FILE to render a prepared file instead, which is how the fixtures test it.
PLAN = "docs/plan/BROTHER-1.1.0-LAUNCH-WBS.json"
RUNS = os.path.expanduser("~/.claude/evidence/unit-runs")
SCORES = os.path.expanduser("~/.claude/evidence/spec-scores.json")
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import plan_store  # noqa: E402  (the one landed test: plan_store.sub_landed, finding 2 of 2026-09-27)


def _newest_status(runs_dir=RUNS):
    """Newest STATUS per sub unit, ordered by the filesystem clock. The folder name carries only HHMMSS and
    wraps at midnight, so a name sort reads last night's run as the newest; see scripts/test_run_ordering.py."""
    out = {}
    for d in sorted(glob.glob(os.path.join(runs_dir, "*-*/")),
                    key=lambda q: (os.path.getmtime(q) if os.path.exists(q) else 0.0, q)):
        m = re.match(r"(.+)-(\d{6})$", os.path.basename(d.rstrip("/")))
        if not m:
            continue
        try:
            out[m.group(1)] = open(os.path.join(d, "STATUS"), encoding="utf-8").read().strip()
        except OSError:
            out[m.group(1)] = ""          # a folder with no STATUS is a runner still working
    return out
    # NOTE the asymmetry that matters: a sub unit ABSENT from this map has never had a run folder at all, which
    # is NOT the same as a folder whose STATUS is not written yet. Treating the two alike marked every sub unit
    # that had never been attempted as "building", which read as 169 building and 0 not started: the board
    # showed a machine working on everything while most of the board had never been started. Corrected below.


def build_data(plan_path=PLAN, runs_dir=RUNS, scores_path=SCORES, alive=None):
    """The board's rows, derived rather than handed over. A source that cannot be read degrades that ONE field
    (no score, no status) and never invents a state: an unknown sub unit reads 'todo', which is the state that
    schedules work, never 'landed', which would hide it."""
    plan = json.load(open(plan_path, encoding="utf-8"))
    status = _newest_status(runs_dir)
    try:
        scores = json.load(open(scores_path, encoding="utf-8"))
    except (OSError, ValueError):
        scores = {}
    if alive is None:
        # THE PAIR, NOT THE UNIT. Keying this on the unit id alone marked EVERY sub unit of a working unit as
        # building, which read as 169 building and 0 not started on the first render: a runner works ONE sub
        # unit at a time, so the rest of its unit is still waiting. Measured and corrected 2026-09-21.
        ps = subprocess.run(["ps", "-eo", "command"], capture_output=True, text=True).stdout
        alive = set(re.findall(r"unit_runner\.py (\S+) (\S+)", ps))
    rows = []
    for u in plan.get("units") or []:
        ev = u.get("evidence") or ""
        subs = []
        for s in u.get("sub_units") or []:
            raw = status.get(s)                      # None means no run folder has ever existed for it
            st = (raw or "").split(None, 1)
            word = st[0] if st else ""
            if plan_store.sub_landed(s, ev):
                state = "landed"
            elif word == "READY":
                # EXACTLY READY, NEVER A PREFIX. `word.startswith("READY")` was also true of
                # READY-UNPROBED, the word that means the adversarial probe stage produced nothing
                # runnable, so the "ready to land" tile counted builds land_batch.py refuses by name:
                # the board told a reader work was waiting to land that no pass could ever land.
                # Measured 2026-09-21, the same prefix defect already corrected in runner_pool.py,
                # loop_done.py and pass_digest.py. READY-UNPROBED now falls through to "todo", which
                # is the state that SCHEDULES work, matching partition() in pass_digest.py: an
                # unprobed build is an unknown that probe_round re-probes and a runner otherwise
                # rebuilds, never a finished thing hidden from the pool.
                state = "ready"
            elif word in ("EXHAUSTED", "WITHHELD", "QUARANTINE"):
                state = "blocked"
            elif (u["id"], s) in alive or (raw is not None and word == ""):
                state = "building"               # a live runner, or a run folder whose STATUS is not written yet
            else:
                state = "todo"                   # never attempted, which is the state that schedules work
            sc = scores.get(s, {})
            subs.append({"id": s, "state": state, "word": word,
                         "score": sc.get("score") if isinstance(sc, dict) else None})
        rows.append({"id": u["id"], "title": u.get("title", ""), "state": u.get("state", ""), "subs": subs,
                     "wave": u.get("wave"), "depends_on": list(u.get("depends_on") or [])})
    return rows


stamp = subprocess.run(["date", "+%Y-%m-%d %H:%M %Z"], capture_output=True, text=True).stdout.strip()
head = subprocess.run(["git", "rev-parse", "--short", "HEAD"], capture_output=True, text=True).stdout.strip()
if "--data" in sys.argv:
    d = json.load(open(sys.argv[sys.argv.index("--data") + 1], encoding="utf-8"))
else:
    d = build_data()
E = html.escape
LEG = [("landed","Landed","built, gated and committed"),
       ("ready","Ready to land","a graded, probed build waiting for the serial writer"),
       ("building","Building now","a worker is on it right now"),
       ("blocked","Blocked","last run exhausted its rounds or needs a human fact"),
       ("underspec","Not decomposed","the unit has no sub units defined yet"),
       ("todo","Not started","specified at 9 or 10, eligible for a worker")]
HOLLOW_NOTE = True
counts = {k: sum(1 for u in d for s in u['subs'] if s['state']==k) for k,_,_ in LEG}
# a unit with NO sub units is not 'nearly done': a DONE one is complete, an open one is UNDECOMPOSED, which is
# a real gap and must not sort to the top of a board ordered by how little is left
counts['underspec'] += sum(1 for u in d if not u['subs'] and u['state'] != 'DONE')
tot = sum(len(u['subs']) for u in d)
landed = counts['landed']
done_units = sum(1 for u in d if u['state']=='DONE')
one_away = sum(1 for u in d if u['state']!='DONE' and u['subs'] and
               sum(1 for s in u['subs'] if s['state']!='landed')==1)
undecomposed = [u['id'] for u in d if not u['subs'] and u['state'] != 'DONE']

# THE WBS IS THE PLAN'S OWN SHAPE, and a flat list throws it away. The plan phases every unit into a WAVE and
# 34 of 53 units name a depends_on, so the two questions a work breakdown must answer are "which phase is this"
# and "what is this waiting for". A board sorted only by how little remains answers neither, and it silently
# ranks a unit that CANNOT START above one that is genuinely next.
CLOSED = {u["id"] for u in d if u["state"] == "DONE"}
def loop_banner():
    """The loop's own health, rendered at the top of the board the owner already reads.

    Owner, 2026-09-21: "there has to be a way to see that in the progress of the tasks outside
    of the loop". An alarm file at a path he was never told about is not that. This is: the
    board is the surface he opens anyway, so the pulse is shown where his eyes already go.
    A heartbeat that cannot be read renders ALARM, never a reassuring blank."""
    try:
        sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__))))
        import loop_heartbeat as H
        return H.banner(H.read(H.HB))
    except Exception as exc:                          # the board must render even when the pulse tool is broken
        return ('<div style="border-left:6px solid #A3231B;background:#A3231B14;padding:12px 16px;margin:12px 0">'
                '<strong style="color:#A3231B">ALARM: loop health unknown</strong><br>'
                'the heartbeat could not be read (%s), which is never the same as healthy</div>'
                % type(exc).__name__)


KNOWN = {u["id"] for u in d}


def blockers_of(u):
    """(unmet dependencies, dependencies this plan cannot even see).

    A dependency naming a unit outside this plan is not a mistake to hide. Measured 2026-09-21: six units wait
    on U8, which belongs to the earlier unification epic and carries no state and no evidence there, so those
    six read as permanently blocked from inside this board. Naming it is the honest answer; silently treating
    it as met would manufacture readiness."""
    unmet, foreign = [], []
    for dep in u.get("depends_on") or []:
        if dep not in KNOWN:
            foreign.append(dep)
        elif dep not in CLOSED:
            unmet.append(dep)
    return unmet, foreign


def order(u):
    if not u['subs']:
        return (-1, u['id']) if u['state'] == 'DONE' else (10**6, u['id'])
    return (sum(1 for s in u['subs'] if s['state']!='landed'), u['id'])


def render_row(u):
    n = len(u['subs']); l = sum(1 for s in u['subs'] if s['state']=='landed')
    unmet, foreign = blockers_of(u)
    if not u['subs']:
        st = 'landed' if u['state'] == 'DONE' else 'underspec'
        cells = ('<i class="c ' + st + '" title="' + E(u['id']) +
                 ('  closed with no sub units tracked' if st == 'landed'
                  else '  NO SUB UNITS DEFINED: this unit cannot be scheduled') + '"></i>')
        num = 'CLOSED' if st == 'landed' else 'no subs'   # same word as the sub unit branch, so the eye can scan one column
        tag = 'done' if st == 'landed' else 'gap'
    else:
        cells = "".join('<i class="c ' + s['state'] + '" title="' + E(s['id']) + '  ' + s['state'] +
                        (('  spec ' + str(s['score']) + '/10') if s['score'] is not None else '') + '"></i>'
                        for s in u['subs'])
        num = str(l) + '/' + str(n)
        if u['state'] == 'DONE':
            # SAY CLOSED, DO NOT MAKE IT INFERRED. A closed unit and an all-green-but-open unit rendered
            # almost identically, both a row of petrol squares, and the only difference was the id's colour.
            # The owner read a DONE unit as "full green but not closed" and asked for it to be closed, which
            # is exactly the confusion a board exists to remove. The word is now on the row.
            tag = 'done'
            num = 'CLOSED ' + str(l) + '/' + str(n)
        elif n and l == n:
            tag = 'hollow'; num = str(l) + '/' + str(n) + ' open'
        else:
            tag = 'near' if n-l == 1 else ''
    # a unit still open and waiting on something is marked, so the board never ranks unstartable work as next
    wait = ''
    if u['state'] != 'DONE' and (unmet or foreign):
        bits = [E(x) for x in unmet] + [E(x) + ' (outside this plan)' for x in foreign]
        wait = '<span class="wait">waits on ' + ', '.join(bits) + '</span>'
        if not tag:
            tag = 'waiting'
    return ('<tr class="' + tag + '"><th><b>' + E(u['id']) + '</b><span>' + E(u['title'][:62]) + '</span>' +
            wait + '</th><td class="n">' + num + '</td><td class="bar">' + cells + '</td></tr>')


# A WAVE IS A NUMBER OR A GATE NAME ("owner", "after ACC1", measured 2026-09-27): numbers first, then names, then
# none; comparing an int with a str raised TypeError and the board could not be generated at all.
waves = sorted({u.get('wave') for u in d}, key=lambda x: (x is None, isinstance(x, str), x if isinstance(x, int) else 0, str(x)))
rows = []
for wv in waves:
    us = [u for u in d if u.get('wave') == wv]
    wsubs = sum(len(u['subs']) for u in us)
    wland = sum(1 for u in us for s in u['subs'] if s['state'] == 'landed')
    wdone = sum(1 for u in us if u['state'] == 'DONE')
    pct = (100 * wland // wsubs) if wsubs else 0
    label = ('Wave %s' % wv) if wv is not None else 'No wave assigned'
    rows.append('<tr class="wave"><th colspan="3"><b>' + E(label) + '</b>'
                '<em>' + str(wdone) + ' of ' + str(len(us)) + ' units closed, '
                + str(wland) + ' of ' + str(wsubs) + ' sub units landed, ' + str(pct) + '%</em></th></tr>')
    for u in sorted(us, key=order):
        rows.append(render_row(u))
legend = "".join('<li><i class="c ' + k + '"></i><b>' + E(lab) + '</b> ' + E(desc) +
                 ' <em>' + str(counts[k]) + '</em></li>' for k, lab, desc in LEG)
hollow = [u['id'] for u in d if u['state'] != 'DONE' and u['subs']
          and all(s['state']=='landed' for s in u['subs'])]
note = ('' if not undecomposed else
        '<p class="gapnote">' + str(len(undecomposed)) + ' open unit(s) have NO sub units defined and cannot be '
        'scheduled at all: <b>' + E(", ".join(undecomposed)) + '</b>. They sort last, not first: an empty unit is '
        'undefined work, never nearly finished.</p>')
if hollow:
    note += ('<p class="gapnote" style="border-left-color:var(--blocked)">' + str(len(hollow)) +
             ' unit(s) have EVERY sub unit landed and are still NOT closed: <b>' + E(', '.join(hollow)) +
             '</b>. Their own done check measures the deliverable and it falls short. All squares filled '
             'is not the same as finished, and painting them solid green is the illusion that hid this.</p>')
# THE TEMPLATE LIVES HERE, 2026-09-21. It used to be read from /tmp/gantt-tpl.html, which nothing in the
# repository wrote, so the board could not be regenerated once a reboot cleared the directory. A generator whose
# template is a temp file is a one off with extra steps, whatever the commit message calls it.
# WHY EACH BLOCKED ROW NOW CARRIES ITS OWN REASON. Three different status words land in the
# blocked state (EXHAUSTED, WITHHELD, QUARANTINE) and this table printed ONE sentence for all of
# them, so five rows read as one problem. Measured 2026-09-22 on the five blocked sub units: two
# were EXHAUSTED, one was QUARANTINE (dropped at landing with six fuzz crashes), and the remaining
# two were not in those states at all. A reader acting on the old sentence would have retried the
# quarantined build, which is exactly the thing its own landing gates refused on its merits.
BLOCK_WHY = {
    "EXHAUSTED":  "spent every repair round without producing an acceptable build, so the brief or "
                  "the task, not the worker, is what needs changing",
    "WITHHELD":   "never sent: the private term screen refused its brief, so it is waiting on a "
                  "human fact and no number of retries will move it",
    "QUARANTINE": "built and then REFUSED AT LANDING on its own merits, so retrying it unchanged "
                  "lands the same defect",
}
blockers = []
# WHAT WOULD HAPPEN NEXT, PER ROW (2026-09-22). "EXHAUSTED" said what stopped a run, not what happens to it now: with
# salvage.py and finish_run.py a parked sub unit that holds a grader passing build is not stuck at all. The board asks
# salvage for its view of each blocked sub unit and says that, or NO-DATA when salvage cannot be read.
def next_step_of():
    try:
        sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__))))
        import salvage, grade_build, check_wave
        plan = json.load(open('docs/plan/BROTHER-1.1.0-LAUNCH-WBS.json', encoding='utf-8'))
        subs = salvage.all_subs(plan) - salvage.landed_subs(plan)
        view = {}
        for c in salvage.plan_view(salvage.candidates(salvage.runs_dir(), subs), grade_build.preflight, check_wave.checker_mode(), salvage.runs_dir()):
            view.setdefault(c['sub'], c)   # newest first by construction
        return view
    except Exception as exc:  # noqa: BLE001
        print("gen_subunit_gantt: NEXT column unavailable (%r); the page shows none rather than a guess" % (exc,), file=sys.stderr)
        return None
NEXT = next_step_of()
def next_text(sub_id):
    if NEXT is None: return ' NEXT: NO-DATA, salvage could not be read'
    c = NEXT.get(sub_id)
    if c is None: return ' NEXT: no build of it ever passed the grader; the pool readmits it when its spec or a judging tool changes'
    if c['action'] == 'PROMOTE': return ' NEXT: a clean build that still applies is on disk; salvage promotes it on the next pass or finisher run'
    if c['probes'] == 'DIRTY' and c['applies'] == 'APPLIES': return ' NEXT: a grader passing build with probe findings is on disk; the finisher repairs it (finish_run.py)'
    if c['applies'].startswith('STALE'): return ' NEXT: its best build no longer applies to the tree; the finisher repairs it against the current files'
    if c['probes'] == 'NO-DATA': return ' NEXT: its best build was never probed; the finisher regrades it; with BROTHER_FINISHER_REGRADE_PROBES=crash or strict it re-executes the cached probes first'
    return ' NEXT: ' + c['action']
for u in sorted(d, key=lambda q: q['id']):
    bad = [s for s in u['subs'] if s['state'] == 'blocked']
    if bad:
        for sub in bad:
            why = BLOCK_WHY.get(sub.get('word') or '',
                                'blocked for a reason this board could not read, which is itself '
                                'the finding: an unreadable state is never a known one')
            blockers.append('<tr><th>' + E(u['id']) + '</th><td>' + E(sub['id']) +
                            '</td><td>' + E((sub.get('word') or 'UNREADABLE') + ': ' + why + '.' + next_text(sub['id'])) + '</td></tr>')
for uid in undecomposed:
    blockers.append('<tr><th>' + E(uid) + '</th><td>no sub units</td>'
                    '<td>not decomposed, so nothing can be scheduled against it</td></tr>')
for uid in hollow:
    blockers.append('<tr><th>' + E(uid) + '</th><td>every sub unit landed</td>'
                    "<td>the unit's own done check measures the deliverable and it falls short</td></tr>")
blockers_html = ('<p class="ok">No unit is blocked right now.</p>' if not blockers else
                 '<table class="blk"><thead><tr><th>Unit</th><th>What</th><th>Why it is stuck</th></tr></thead>'
                 '<tbody>' + "".join(blockers) + '</tbody></table>')
pct = (100.0 * landed / tot) if tot else 0.0
upct = (100.0 * done_units / len(d)) if d else 0.0
TPL = """<!doctype html><html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Brother 1.1.0 sub unit board</title>
<style>
:root{--petrol:#0E7A6F;--paper:#F7F8F6;--slate:#141B22;--ink:#141B22;--muted:#5d6b73;
 --landed:#0E7A6F;--ready:#2f8fd0;--building:#d9a41a;--blocked:#c0492f;--underspec:#8a5cc7;--todo:#c9d1d3;
 --line:#dfe4e2;--card:#ffffff;}
@media (prefers-color-scheme: dark){:root:not([data-theme="light"]){--paper:#0f1518;--ink:#e8edee;--muted:#9aa8ae;
 --petrol:#3AA893;--landed:#3AA893;--line:#25313a;--card:#151d22;--todo:#2c383f;}}
:root[data-theme="dark"]{--paper:#0f1518;--ink:#e8edee;--muted:#9aa8ae;--petrol:#3AA893;--landed:#3AA893;
 --line:#25313a;--card:#151d22;--todo:#2c383f;}
*{box-sizing:border-box}
body{margin:0;background:var(--paper);color:var(--ink);
 font-family:Seravek,"Gill Sans Nova",Ubuntu,Calibri,"DejaVu Sans",source-sans-pro,sans-serif;line-height:1.5}
.wrap{max-width:1100px;margin:0 auto;padding:32px 16px 64px}
h1,h2{font-family:"Iowan Old Style","Palatino Linotype",Palatino,Georgia,serif;font-weight:600;letter-spacing:-.01em}
h1{font-size:1.9rem;margin:.2rem 0 .1rem}
h2{font-size:1.15rem;margin:2.2rem 0 .6rem;padding-bottom:.3rem;border-bottom:1px solid var(--line)}
.eyebrow{text-transform:uppercase;letter-spacing:.12em;font-size:.72rem;color:var(--petrol);font-weight:700}
.stamp{color:var(--muted);font-size:.82rem;margin:.3rem 0 0}
.strip{display:grid;grid-template-columns:repeat(auto-fit,minmax(150px,1fr));gap:12px;margin:22px 0 4px}
.tile{background:var(--card);border:1px solid var(--line);border-radius:10px;padding:14px}
.tile b{display:block;font-size:1.7rem;font-family:"Iowan Old Style",Georgia,serif;line-height:1.1}
.tile span{color:var(--muted);font-size:.78rem;text-transform:uppercase;letter-spacing:.06em}
.meter{height:9px;border-radius:6px;background:var(--todo);overflow:hidden;margin:14px 0 6px}
.meter i{display:block;height:100%;background:var(--landed)}
table{width:100%;border-collapse:collapse;font-size:.86rem}
th{text-align:left;font-weight:600}
tbody th{padding:5px 10px 5px 0;white-space:nowrap;vertical-align:top}
tbody th b{font-family:"Iowan Old Style",Georgia,serif}
tbody th span{display:block;color:var(--muted);font-weight:400;font-size:.76rem;max-width:23rem;white-space:normal}
td.n{color:var(--muted);white-space:nowrap;padding-right:10px;font-variant-numeric:tabular-nums}
td.bar{width:99%}
tr.done td.n{color:var(--landed);font-weight:700}
tr.near td.n{color:var(--ready);font-weight:700}
tr.wave th{padding-top:24px;border-bottom:2px solid var(--petrol)}
tr.wave b{font:600 15px/1.3 "Iowan Old Style",Palatino,Georgia,serif;color:var(--petrol)}
tr.wave em{font-style:normal;color:var(--mute);font-size:11px;margin-left:10px;font-variant-numeric:tabular-nums}
tr.waiting{background:color-mix(in srgb,var(--underspec) 7%,transparent)}
span.wait{display:block;color:var(--blocked);font-size:10px;margin-top:3px;font-weight:500}

tr.hollow td.n{color:var(--blocked);font-weight:700}
i.c{display:inline-block;width:12px;height:12px;border-radius:3px;margin:1px;background:var(--todo);vertical-align:middle}
i.c.landed{background:var(--landed)} i.c.ready{background:var(--ready)} i.c.building{background:var(--building)}
i.c.blocked{background:var(--blocked)} i.c.underspec{background:var(--underspec)} i.c.todo{background:var(--todo)}
ul.leg{list-style:none;padding:0;margin:8px 0;display:grid;grid-template-columns:repeat(auto-fit,minmax(250px,1fr));gap:4px}
ul.leg li{font-size:.8rem;color:var(--muted)} ul.leg b{color:var(--ink)} ul.leg em{font-style:normal;color:var(--ink);font-weight:700}
.gapnote{border-left:3px solid var(--underspec);background:var(--card);padding:10px 14px;margin:14px 0;font-size:.84rem;border-radius:0 8px 8px 0}
table.blk{margin-top:8px} table.blk thead th{color:var(--muted);font-size:.76rem;text-transform:uppercase;letter-spacing:.06em;border-bottom:1px solid var(--line);padding-bottom:6px}
table.blk tbody th{color:var(--blocked);font-family:"Iowan Old Style",Georgia,serif}
table.blk td{padding:6px 10px 6px 0;vertical-align:top;color:var(--muted)}
.ok{color:var(--landed);font-weight:600}
footer{margin-top:40px;padding-top:14px;border-top:1px solid var(--line);color:var(--muted);font-size:.8rem}
</style></head><body><div class="wrap">
<p class="eyebrow">Brother 1.1.0 launch</p>
<h1>Sub unit board</h1>
<p class="stamp">@STAMP@ &middot; commit @HEAD@ &middot; one square per sub unit</p>
@LOOPBANNER@

<h2>Completion rate</h2>
<div class="strip">
  <div class="tile"><b>@PCT@%</b><span>sub units landed</span></div>
  <div class="tile"><b>@LANDED@/@TOT@</b><span>sub units</span></div>
  <div class="tile"><b>@UPCT@%</b><span>units closed</span></div>
  <div class="tile"><b>@DONEU@</b><span>units DONE</span></div>
  <div class="tile"><b>@NEAR@</b><span>one sub unit away</span></div>
  <div class="tile"><b>@READY@</b><span>ready to land</span></div>
  <div class="tile"><b>@BLOCKED@</b><span>blocked sub units</span></div>
</div>
<div class="meter"><i style="width:@PCT@%"></i></div>
<p class="stamp">The bar is a RECORD COUNT, never an impression: a square is landed only when its build was
gated, applied and committed. A unit with every square filled is still not closed until its own done check
passes, which is why the two counts above differ.</p>

<h2>Blockers</h2>
@BLOCKERS@

<h2>Units</h2>
<ul class="leg">@LEGEND@</ul>
<table><tbody>@ROWS@</tbody></table>

<footer><p>Generated by scripts/loop/gen_subunit_gantt.py, which derives its own data from the plan, the run
evidence and the spec scores. No hand placed input file.</p><p class="stamp">@STAMP@</p></footer>
</div></body></html>"""
tpl = TPL.replace('<table><tbody>', note + '<table><tbody>')
for tok, val in [("@BLOCKERS@",blockers_html),("@PCT@","%.0f" % pct),("@UPCT@","%.0f" % upct),
                 ("@LOOPBANNER@", loop_banner()),
                 ("@STAMP@",E(stamp)),("@HEAD@",E(head)),("@LANDED@",str(landed)),("@TOT@",str(tot)),
                 ("@DONEU@",str(done_units)),("@NEAR@",str(one_away)),("@READY@",str(counts['ready'])),
                 ("@BLOCKED@",str(counts['blocked'])),("@LEGEND@",legend),("@ROWS@","".join(rows))]:
    tpl = tpl.replace(tok, val)
open('docs/plan/SUBUNIT-GANTT.html','w',encoding='utf-8').write(tpl)
print("wrote %d bytes | landed %d/%d | closed %d | one away %d | undecomposed %s"
      % (len(tpl), landed, tot, done_units, one_away, undecomposed))

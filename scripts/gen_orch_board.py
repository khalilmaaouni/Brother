#!/usr/bin/env python3
"""Render the 1.0.20 overnight control-plane board from this run's own two
documents, and from nothing else.

WHY THIS EXISTS BESIDE gen_readiness_board.py, since the estate's law is that a
key component is used rather than rebuilt. gen_readiness_board.py renders the
ESTATE readiness roadmap: its source path is a module constant, it has no
--source, and it writes one fixed output. This renders a SINGLE RUN's work
breakdown, which is a different artifact with a different source and a lifetime
of one night. Teaching the readiness renderer to accept an arbitrary breakdown
would be a larger change to a load-bearing file than the page is worth, and the
estate already holds several per-initiative Gantt pages of exactly this kind.

THE TICK CONTRACT, which is the only thing on the page that matters. A box ticks
only when its own done check ran AFTER the last edit to the unit and the output
is quoted beside it. A unit with an empty evidence field is a CLAIM, never a
tick, and is counted separately. Percentages are record counts, never
impressions.
"""
import html
import json
import os
import sys

import closure_integrity

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
WBS = os.path.join(ROOT, 'docs', 'plan', 'ORCH-1020-WBS.json')
STATUS = os.path.join(ROOT, 'docs', 'plan', 'ORCH-1020-STATUS.json')
OUTPUT = os.path.join(ROOT, 'docs', 'plan', 'ORCH-1020-GANTT.html')
# The Stop hook keep_pulling_the_backlog reads one 'status' per item and can
# see neither the WBS (no status) nor STATUS.json (no plan-shaped name), so
# the run's open work was invisible to it. This export is that contract.
BACKLOG = os.path.join(ROOT, 'docs', 'plan', 'ORCH-1020-BACKLOG.json')

DONE = 'DONE'
CLAIM_STATES = ('DONE',)
RUNNING_STATES = ('RUNNING', 'VERIFYING', 'REVIEW', 'INTEGRATING', 'CANONICAL-VERIFY')
STOPPED_STATES = ('PARKED', 'EXHAUSTED', 'AWAITING-HUMAN', 'CANCELLED')


def load(path):
    with open(path, 'r', encoding='utf-8') as fh:
        return json.load(fh)


def e(text):
    return html.escape(str(text if text is not None else ''))


def tick_class(state, evidence):
    """The whole tick contract in one function. DONE without evidence is a
    CLAIM, which is deliberately NOT the same class as a tick, so it cannot be
    read off the page as finished."""
    if state == DONE and evidence:
        return 'done'
    if state == DONE and not evidence:
        return 'claim'
    if state in RUNNING_STATES:
        return 'running'
    if state in STOPPED_STATES:
        return 'stopped'
    return 'planned'


def counts(units, status):
    done = claim = running = stopped = 0
    for u in units:
        st = status['units'].get(u['id'], {})
        cls = tick_class(st.get('state', 'PLANNED'), st.get('evidence', ''))
        done += cls == 'done'
        claim += cls == 'claim'
        running += cls == 'running'
        stopped += cls == 'stopped'
    return {'total': len(units), 'done': done, 'claim': claim,
            'running': running, 'stopped': stopped}


CSS = """
:root{--paper:#F7F8F6;--slate:#141B22;--petrol:#0E7A6F;--line:#DDE2DE;
--muted:#5C6B6A;--claim:#B4691A;--stop:#8C2F2F;--hatch:#C7D6D2}
*{box-sizing:border-box}
body{margin:0;background:var(--paper);color:var(--slate);
font-family:Seravek,"Gill Sans Nova",Ubuntu,Calibri,"DejaVu Sans",source-sans-pro,sans-serif;
line-height:1.55;-webkit-text-size-adjust:100%}
.wrap{max-width:1180px;margin:0 auto;padding:40px 16px 80px}
h1,h2,h3{font-family:"Iowan Old Style","Palatino Linotype",Palatino,Georgia,serif;
font-weight:600;letter-spacing:-.01em}
h1{font-size:2.5rem;margin:.1em 0 .2em}
h2{font-size:1.4rem;margin:2.6em 0 .7em;padding-bottom:.3em;border-bottom:1px solid var(--line)}
.eyebrow{text-transform:uppercase;letter-spacing:.14em;font-size:.72rem;
color:var(--petrol);font-weight:600}
.stamp{font-size:.8rem;color:var(--muted);margin:.4em 0 0}
.northstar{border-left:4px solid var(--petrol);background:#EDF4F2;
padding:16px 20px;margin:26px 0;border-radius:0 6px 6px 0}
.northstar strong{display:block;font-family:"Iowan Old Style",Georgia,serif;font-size:1.1rem}
.strip{display:grid;grid-template-columns:repeat(auto-fit,minmax(215px,1fr));gap:12px;margin:22px 0}
.card{background:#fff;border:1px solid var(--line);border-radius:8px;padding:14px 16px}
.card .k{text-transform:uppercase;letter-spacing:.1em;font-size:.66rem;
color:var(--muted);font-weight:700;margin-bottom:6px}
.card .v{font-size:.94rem}
table{width:100%;border-collapse:collapse;font-size:.86rem}
th{text-align:left;font-weight:600;color:var(--muted);font-size:.7rem;
text-transform:uppercase;letter-spacing:.08em;padding:6px 8px;border-bottom:1px solid var(--line)}
td{padding:7px 8px;border-bottom:1px solid #EEF1EF;vertical-align:top}
.bar{position:relative;height:16px;background:#EDF0EE;border-radius:3px;overflow:hidden;min-width:180px}
.seg{position:absolute;top:0;height:100%;border-radius:3px}
.seg.done{background:var(--petrol)}
.seg.running{background:repeating-linear-gradient(45deg,var(--petrol),var(--petrol) 5px,var(--hatch) 5px,var(--hatch) 10px)}
.seg.planned{background:repeating-linear-gradient(45deg,#E4EAE8,#E4EAE8 5px,#F2F5F4 5px,#F2F5F4 10px)}
.seg.claim{background:repeating-linear-gradient(45deg,var(--claim),var(--claim) 5px,#E8C79A 5px,#E8C79A 10px)}
.seg.stopped{background:var(--stop)}
.pill{display:inline-block;font-size:.66rem;font-weight:700;letter-spacing:.06em;
padding:2px 7px;border-radius:99px;text-transform:uppercase}
.pill.done{background:#DCEEE9;color:#0B5A52}
.pill.running{background:#E3EFEC;color:var(--petrol)}
.pill.planned{background:#EEF1EF;color:var(--muted)}
.pill.claim{background:#F7E7D2;color:var(--claim)}
.pill.stopped{background:#F6DEDE;color:var(--stop)}
.ledger{display:grid;grid-template-columns:repeat(auto-fit,minmax(330px,1fr));gap:12px}
.box{background:#fff;border:1px solid var(--line);border-left:4px solid var(--line);
border-radius:6px;padding:12px 14px}
.box.done{border-left-color:var(--petrol)}
.box.claim{border-left-color:var(--claim)}
.box.running{border-left-color:var(--petrol);background:#FBFCFB}
.box.stopped{border-left-color:var(--stop)}
.box h4{margin:0 0 4px;font-size:.9rem;font-family:inherit;font-weight:700}
.box .obj{font-size:.8rem;color:var(--muted);margin:0 0 8px}
pre{background:#12181E;color:#D6E4E0;padding:10px 12px;border-radius:5px;
font-size:.72rem;overflow-x:auto;margin:6px 0 0;white-space:pre-wrap;word-break:break-word}
code{font-size:.76rem;background:#EDF0EE;padding:1px 5px;border-radius:3px}
.risk{background:#fff;border:1px solid var(--line);border-radius:8px;padding:14px 16px;margin-bottom:10px}
.risk .h{font-weight:700;font-size:.92rem}
.risk .m{font-size:.82rem;color:var(--muted);margin:3px 0}
footer{margin-top:60px;padding-top:18px;border-top:1px solid var(--line);
font-size:.78rem;color:var(--muted)}
.none{color:var(--muted);font-style:italic;font-size:.86rem}
@media (prefers-color-scheme:dark){
:root:not([data-theme="light"]){--paper:#0F1519;--slate:#E6ECEA;--petrol:#3AA893;
--line:#26302F;--muted:#8FA09C;--hatch:#2C3A38;--claim:#D9922F;--stop:#D4645F}
:root:not([data-theme="light"]) body{background:var(--paper);color:var(--slate)}
:root:not([data-theme="light"]) .card,
:root:not([data-theme="light"]) .box,
:root:not([data-theme="light"]) .risk{background:#151D21}
:root:not([data-theme="light"]) .northstar{background:#12241F}
:root:not([data-theme="light"]) .bar{background:#1B2427}
:root:not([data-theme="light"]) .seg.planned{background:repeating-linear-gradient(45deg,#1E2A2C,#1E2A2C 5px,#161F21 5px,#161F21 10px)}
:root:not([data-theme="light"]) td{border-bottom-color:#1D2528}
:root:not([data-theme="light"]) code{background:#1B2427}
}
"""


def bar(cls, pct):
    return ('<div class="bar"><span class="seg %s" style="left:0;width:%d%%"></span></div>'
            % (cls, pct))


def gantt_table(units, status, wbs, title, note):
    rows = []
    for u in units:
        st = status['units'].get(u['id'], {})
        state = st.get('state', 'PLANNED')
        cls = tick_class(state, st.get('evidence', ''))
        pct = {'done': 100, 'claim': 100, 'running': 55, 'stopped': 40}.get(cls, 0)
        deps = ', '.join(u['depends_on']) or 'none'
        rows.append(
            '<tr><td><strong>%s</strong></td><td>%s</td><td>%s</td>'
            '<td>%s</td><td>%s</td><td><span class="pill %s">%s</span></td><td>%s</td></tr>'
            % (e(u['id']), e(u['title']), e(u.get('worker', '')),
               e(u.get('checker', '')), e(deps), cls, e(state), bar(cls, pct)))
    return ('<h2>%s</h2><p class="stamp">%s</p><table><thead><tr>'
            '<th>Unit</th><th>What it builds</th><th>Worker</th><th>Checker</th>'
            '<th>Depends on</th><th>State</th><th>Progress</th>'
            '</tr></thead><tbody>%s</tbody></table>'
            % (e(title), e(note), ''.join(rows)))


def build(wbs, status):
    units = wbs['units']
    c = counts(units, status)
    pct = int(round(100.0 * c['done'] / c['total'])) if c['total'] else 0
    win = wbs['window']
    rm = wbs['role_map_tonight']

    p = ['<!doctype html><html lang="en"><head><meta charset="utf-8">',
         '<meta name="viewport" content="width=device-width,initial-scale=1">',
         '<title>Overnight Control Plane</title><style>%s</style></head><body><div class="wrap">' % CSS]

    # 1 header
    p.append('<p class="eyebrow">Brother 1.0.20 &middot; night run %s</p>' % e(status['run_id']))
    p.append('<h1>Overnight Dual-Orchestrator Control Plane</h1>')
    p.append('<p class="stamp">Base revision <code>%s</code>, read from the repository of record. '
             'Page generated %s by <code>scripts/gen_orch_board.py</code> from '
             '<code>docs/plan/ORCH-1020-WBS.json</code> and '
             '<code>docs/plan/ORCH-1020-STATUS.json</code>, and from nothing else.</p>'
             % (e(status['head'][:12]), e(status['generated_at'])))

    # 2 north star
    p.append('<div class="northstar"><strong>North star</strong>'
             'The founder goes offline, both orchestrator processes may die and restart, '
             'bounded subagents keep working, truth stays serial at integration, and the '
             'morning handoff explains exactly what happened without anyone reading a model '
             'transcript. Finishing these boxes is the price of admission, not the goal.</div>')

    # 3 at a glance
    running = [u['id'] for u in units
               if tick_class(status['units'].get(u['id'], {}).get('state', 'PLANNED'),
                             status['units'].get(u['id'], {}).get('evidence', '')) == 'running']
    waiting = status.get('decisions_waiting') or []
    risks = status.get('risks') or []
    p.append('<div class="strip">')
    p.append('<div class="card"><div class="k">Now</div><div class="v">%s</div></div>'
             % (e(', '.join(running)) if running else '<span class="none">nothing running</span>'))
    p.append('<div class="card"><div class="k">Waiting on Khalil</div><div class="v">%s</div></div>'
             % (e('%d decision(s)' % len(waiting)) if waiting
                else '<span class="none">nothing, by design: this is an unattended run</span>'))
    p.append('<div class="card"><div class="k">Risk watch</div><div class="v">%s</div></div>'
             % (e('%d open' % len(risks)) if risks else '<span class="none">none open</span>'))
    p.append('<div class="card"><div class="k">Forecast</div><div class="v">Drain %s, hard stop %s</div></div>'
             % (e(win['drain_start'][11:16]), e(win['hard_stop'][11:16])))
    p.append('<div class="card"><div class="k">Ticked</div><div class="v"><strong>%d of %d</strong> (%d%%)%s</div></div>'
             % (c['done'], c['total'], pct,
                (', plus %d claim(s) with no evidence, excluded' % c['claim']) if c['claim'] else ''))
    p.append('</div>')

    # 4 the two charts
    short = [u for u in units if u['wave'] <= 3]
    longr = [u for u in units if u['wave'] > 3]
    p.append(gantt_table(short, status, wbs, 'Short range: the protocol spine (waves 0 to 3)',
                         'These rows are the critical path. The steering document forbids '
                         'parallelising them against each other where they touch the same '
                         'authority contract, so the graph is deliberately narrow here.'))
    p.append(gantt_table(longr, status, wbs, 'Long range: execution, faults and closeout (waves 4 to 7)',
                         'These widen once the protocol is stable. ORCH-02 and ORCH-10 both '
                         'write the shared spine files and are sequenced against each other, '
                         'never run side by side.'))

    # 5 decisions waiting
    p.append('<h2>Decisions waiting on the founder</h2>')
    if waiting:
        for d in waiting:
            p.append('<div class="risk"><div class="h">%s</div><div class="m">%s</div></div>'
                     % (e(d.get('question', '')), e(d.get('detail', ''))))
    else:
        p.append('<p class="none">None. He said good night and delegated the run, so every '
                 'class 1 decision was taken by the session and recorded below rather than '
                 'queued. Anything genuinely his (a credential, a push to a public remote, an '
                 'acceptance) is refused and queued, never executed.</p>')

    # 6 risks
    p.append('<h2>Risk management, insights and alerts</h2>')
    if risks:
        for r in risks:
            p.append('<div class="risk"><div class="h">%s</div>'
                     '<div class="m"><strong>Why it matters:</strong> %s</div>'
                     '<div class="m"><strong>Action:</strong> %s</div>'
                     '<div class="m"><strong>When:</strong> %s</div></div>'
                     % (e(r.get('what', '')), e(r.get('why', '')),
                        e(r.get('action', '')), e(r.get('when', ''))))
    else:
        p.append('<p class="none">None recorded yet.</p>')

    # 7 the ledger
    p.append('<h2>The ledger</h2>')
    p.append('<p class="stamp">%d ticked, %d claimed without evidence, %d running, %d stopped, '
             '%d of %d total. A box ticks only when its own done check ran after the last edit '
             'and its output is quoted inside the box.</p>'
             % (c['done'], c['claim'], c['running'], c['stopped'], c['done'], c['total']))
    p.append('<div class="ledger">')
    for u in units:
        st = status['units'].get(u['id'], {})
        cls = tick_class(st.get('state', 'PLANNED'), st.get('evidence', ''))
        ev = st.get('evidence', '')
        p.append('<div class="box %s"><h4>%s &middot; %s <span class="pill %s">%s</span></h4>'
                 % (cls, e(u['id']), e(u['title']), cls, e(st.get('state', 'PLANNED'))))
        p.append('<p class="obj">%s</p>' % e(u['objective']))
        p.append('<p class="obj"><strong>Owns:</strong> <code>%s</code></p>'
                 % e(', '.join(u['owns'])))
        p.append('<p class="obj"><strong>Spend:</strong> %s</p>' % e(spend_line(st)))
        if ev:
            p.append('<p class="obj"><strong>Evidence, quoted:</strong></p><pre>%s</pre>' % e(ev))
        else:
            p.append('<p class="obj"><strong>Check that will decide it:</strong> '
                     '<code>%s</code></p>' % e(u['done_check']))
        p.append('</div>')
    p.append('</div>')

    # 8 decisions recorded
    p.append('<h2>Decisions recorded</h2>')
    rec = status.get('decisions_recorded') or []
    if rec:
        for d in rec:
            p.append('<div class="risk"><div class="h">%s</div>'
                     '<div class="m"><strong>Chosen:</strong> %s</div>'
                     '<div class="m"><strong>Rejected:</strong> %s</div>'
                     '<div class="m"><strong>Evidence:</strong> %s</div>'
                     '<div class="m"><strong>Cost if wrong:</strong> %s</div>'
                     '<div class="m"><strong>Flip condition:</strong> %s</div></div>'
                     % (e(d.get('question', '')), e(d.get('chosen', '')),
                        e(d.get('rejected', '')), e(d.get('evidence', '')),
                        e(d.get('cost_if_wrong', '')), e(d.get('flip', ''))))
    else:
        p.append('<p class="none">None recorded yet.</p>')

    # 9 footer
    p.append('<footer><p><strong>Tick contract, verbatim.</strong> A box ticks only when its '
             'done check ran AFTER the last edit to that unit and the output is quoted beside '
             'it. A DONE with an empty evidence field is a CLAIM, is excluded from every '
             'percentage, and is named separately. Percentages are record counts, never '
             'impressions. A section with nothing countable says NO-DATA rather than 0 per '
             'cent, because those look identical on a bar and mean opposite things.</p>')
    p.append('<p><strong>Role map tonight, in the founder\'s own words.</strong> '
             'Orchestrator: %s. Workers: %s. Checkers: %s. Final check: %s. Documentation: %s.</p>'
             % (e(rm['orchestrator']), e(rm['workers']), e(rm['checkers']),
                e(rm['final_check']), e(rm['documentation'])))
    p.append('<p><strong>Privacy boundary that shaped the role map.</strong> %s</p>'
             % e(rm['privacy_constraint']))
    p.append('<p>Window: start %s, drain %s, hard stop %s. %s</p></footer>'
             % (e(win['start']), e(win['drain_start']), e(win['hard_stop']),
                e(win['decided_by'])))
    p.append('</div></body></html>')
    return ''.join(p)


def spend_line(st):
    """TOKEN-05: what a unit cost, or NO-DATA. A unit whose spend was never
    measured must not read as free, so a missing figure is printed as
    NO-DATA rather than left off the box or shown as zero."""
    tok = st.get('tokens')
    lane = st.get('worker', '')
    if isinstance(tok, int) and tok >= 0:
        return '%s tokens, %s' % ('{:,}'.format(tok), lane or 'lane not recorded')
    return 'NO-DATA (not measured), %s' % (lane or 'lane not recorded')


def backlog(units, status):
    """One row per unit in the hook's contract. A planned unit is 'ready'
    only when every dependency is ticked; otherwise it names what it waits on."""
    cls = {u['id']: tick_class(status['units'].get(u['id'], {}).get('state', 'PLANNED'),
                               status['units'].get(u['id'], {}).get('evidence', ''))
           for u in units}
    rows = []
    for u in units:
        c = cls[u['id']]
        waits = [d for d in u.get('depends_on', []) if cls.get(d) != 'done']
        if c == 'planned':
            row = {'id': u['id'], 'status': 'ready' if not waits else 'waiting',
                   'note': 'depends on ' + ', '.join(waits) if waits else ''}
        else:
            row = {'id': u['id'], 'status': c}
        rows.append(row)
    return {'source': 'scripts/gen_orch_board.py', 'rows': rows}


def main(argv=None):
    argv = list(sys.argv[1:] if argv is None else argv)
    try:
        wbs = load(WBS)
        status = load(STATUS)
    except (OSError, ValueError) as exc:
        print('orch-board: NO-DATA, cannot read a source: %s' % exc, file=sys.stderr)
        return 2
    page = build(wbs, status)
    if '--check' in argv:
        c = counts(wbs['units'], status)
        findings = closure_integrity.check(wbs, status)
        print('orch-board: %d unit(s), %d ticked, %d claim(s) without evidence'
              % (c['total'], c['done'], c['claim']))
        for f in findings:
            print('orch-board: closure finding: %s: %s' % (f['id'], f['reason']))
        if findings:
            print('orch-board: %d closure integrity finding(s)' % len(findings))
        return 1 if (c['claim'] or findings) else 0
    with open(OUTPUT, 'w', encoding='utf-8') as fh:
        fh.write(page)
    tmp = BACKLOG + '.tmp'
    with open(tmp, 'w', encoding='utf-8') as fh:
        json.dump(backlog(wbs['units'], status), fh, indent=1)
    os.replace(tmp, BACKLOG)
    c = counts(wbs['units'], status)
    print('orch-board: wrote %s, %d of %d ticked, %d claim(s) without evidence'
          % (OUTPUT, c['done'], c['total'], c['claim']))
    return 0


if __name__ == '__main__':
    sys.exit(main())

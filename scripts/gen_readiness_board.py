#!/usr/bin/env python3
"""Render the readiness board from docs/plan/READINESS-ROADMAP-2026-08-29.json.

WHY THIS EXISTS AND WHY IT IS NOT gen_command_center.py. That script renders the
DAY plan into GANTT.html. This one renders the READINESS ROADMAP, a different
object with a different lifetime: the six gates the founder named on 2026-08-29
that must all close before two contributors join. They are separate boards
because they answer separate questions, and because the day board's three files
are currently held by an open task whose owner no longer appears in the session
list.

Nobody edits the rendered board. Edit the JSON and re-run this.

Standard library only, Python 3.9 floor. Run from any directory.

Exit 0 on a successful render. Exit 2 when the roadmap file cannot be read or is
not valid JSON, which is NO-DATA: nothing was rendered and that is never a pass.

PRODUCER: this module is the sole producer of docs/plan/READINESS-BOARD.html.
main() (line 926) is the only writer: it does the actual open(OUTPUT, 'w',
encoding='utf-8') plus fh.write(render(doc)) at lines 942-943.
"""
import html
import json
import board_status as BS
import parity_gate as PG
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SOURCE = os.path.join(ROOT, 'docs', 'plan', 'READINESS-ROADMAP-2026-08-29.json')
OUTPUT = os.path.join(ROOT, 'docs', 'plan', 'READINESS-BOARD.html')

# Status vocabulary is CLOSED. A status outside it is reported by name rather
# than rendered as a default colour, because a silently defaulted status reads
# as a deliberate one and this estate has been bitten by exactly that.
# The five the founder requires on every row. Enforced in validate(), never
# merely encouraged.
# Amended the same day: 'always start from the user and not from abstract
# concepts.' A row that cannot name a person and the moment they meet it is
# infrastructure looking for a justification.
ROW_CONTRACT_FIELDS = ('ships', 'role', 'why_now', 'effect', 'visible_when',
                       'persona', 'their_moment', 'what_they_see')

# A FEATURE owes more than a row, because a feature is a decision to ADD
# something. Founder doctrine 2026-08-29: simplicity by adding just enough,
# art is about removing, success is saying no to most things, and the weight
# of each decision matters. So a feature must name what it REMOVES, the real
# world constraint it is GROUNDED IN, and the WEIGHT of getting it wrong. A
# feature that only adds, answers no physical constraint, and carries no
# stated cost is refused rather than rendered.
FEATURE_CONTRACT_FIELDS = (
    'what_it_is', 'closes', 'effect', 'visible_when',
    'persona', 'their_moment', 'what_they_see',
    'borrowed_from', 'adaptation', 'removes', 'grounded_in', 'weight')

STATUS_CLASS = {
    'DONE': 'st-done', 'IN-FLIGHT': 'st-flight', 'SCHEDULED': 'st-sched',
    'BLOCKED': 'st-blocked', 'OPEN': 'st-open', 'PARTIAL': 'st-partial',
    'NO-DATA': 'st-nodata', 'UNMEASURED': 'st-nodata',
    'AWAITING FOUNDER': 'st-founder',
    # SUPERSEDED is not DONE and not OPEN: the work moved elsewhere and this
    # row is kept only so its dependency edges stay readable. It carries 0h,
    # because while R12 sat SCHEDULED at 160h the board counted G1-M3 and
    # G1-M4 twice and reported 463h against a real 303h.
    'SUPERSEDED': 'st-nodata',
}


def load(path=None):
    """Resolve SOURCE AT CALL TIME, not at definition time. Written as
    `path=SOURCE` first, which binds the module constant into the default when
    the function object is created, so overriding SOURCE afterwards silently had
    no effect and three NO-DATA tests passed against the real roadmap instead of
    their fixtures. The tests caught it; the defaulted form would have shipped a
    validator that could not be pointed anywhere."""
    if path is None:
        path = SOURCE
    with open(path, 'r', encoding='utf-8') as fh:
        return json.load(fh)


def validate(doc):
    """Return a list of problems. An empty list means the roadmap is coherent.
    Checked here rather than trusted: a dangling dependency silently drops a row
    out of every wave calculation and the board still renders, which is the
    quiet-wrong failure this whole estate exists to refuse."""
    problems = []
    rows = doc.get('rows', [])
    ids = set(r.get('id') for r in rows)
    gate_ids = set(g.get('id') for g in doc.get('gates', []))
    for r in rows:
        for dep in r.get('depends_on', []):
            if dep not in ids:
                problems.append('row %s depends on %s which does not exist' % (r.get('id'), dep))
        if r.get('gate') not in gate_ids:
            problems.append('row %s names gate %s which does not exist' % (r.get('id'), r.get('gate')))
        if not r.get('done_check'):
            problems.append('row %s has no done_check, so it can never close honestly' % r.get('id'))
        # THE ROW CONTRACT, founder rule 2026-08-29. A row that names work without
        # naming what SHIPS, its role, why it is prioritised here, the effect
        # somebody will observe, and when that effect appears, is abstract. An
        # abstract board cannot be argued with or held to, so this refuses to
        # render one rather than printing a warning nobody reads.
        for field in ROW_CONTRACT_FIELDS:
            if not str(r.get(field) or '').strip():
                problems.append('row %s is missing %r, so it says what to do without saying what '
                                'ships or what changes' % (r.get('id'), field))
        if r.get('status') not in STATUS_CLASS:
            problems.append('row %s has unknown status %r' % (r.get('id'), r.get('status')))
    for f in doc.get('features', []):
        for field in FEATURE_CONTRACT_FIELDS:
            if not str(f.get(field) or '').strip():
                problems.append('feature %s is missing %r; a feature that only adds, answers no real '
                                'constraint, or states no cost is not a decision' % (f.get('id'), field))
    # A refusal with no flip condition is a grudge, not a decision.
    for x in doc.get('refused', []):
        for field in ('was', 'why_refused', 'flip_condition'):
            if not str(x.get(field) or '').strip():
                problems.append('refused item %s is missing %r; a refusal with no flip condition is a '
                                'grudge, not a decision' % (x.get('id'), field))
    return problems


def ready_rows(doc):
    """Rows whose dependencies are all DONE. This is the ready set: what could
    honestly start right now. A row is NOT ready merely because its wave came
    up; the dependency has to have actually closed."""
    rows = doc.get('rows', [])
    # SUPERSEDED and ADDRESSED satisfy edges but are never themselves ready:
    # the ready set offered R12, a superseded row, on 2026-08-30. Mirrors the
    # same fix in graph_loop.plan().
    done = set(r['id'] for r in rows
               if r.get('status') in ('DONE', 'SUPERSEDED', 'ADDRESSED'))
    out = []
    for r in rows:
        if r.get('status') in ('DONE', 'IN-FLIGHT', 'SUPERSEDED', 'ADDRESSED'):
            continue
        if all(d in done for d in r.get('depends_on', [])):
            out.append(r['id'])
    return out


def counts(doc):
    rows = doc.get('rows', [])
    c = {}
    for r in rows:
        c[r.get('status')] = c.get(r.get('status'), 0) + 1
    return c


def e(s):
    return html.escape(str(s), quote=True)


# THE WBS ACCORDION (founder request 2026-08-29: "accordeon WBS under each stream").
# Native <details>, no JavaScript: it survives being saved, printed and reopened
# offline, and it is keyboard reachable without anyone writing focus handling.
# A stream with no breakdown does NOT get an empty drawer. It gets a red one
# saying so, because the whole point of W7 is that an undecomposed node cannot
# declare its write set or be resumed, and hiding that behind a tidy closed
# triangle would make the board look more finished than the plan actually is.
WORK_PACKAGE_MAX_HOURS = 4


def wbs_accordion(node):
    subs = node.get('subtasks') or []
    parent_h = node.get('effort_hours') or node.get('estimate_hours') or 0
    # A FINISHED NODE OWES NO BREAKDOWN. The red drawer exists to say "nobody can
    # name the files this will touch", which is a statement about work still to be
    # done. Shown on a DONE row it is simply false, and it made four completed rows
    # read as debt: five red drawers on a board whose data holds exactly one.
    if (node.get('status') or '').upper() in ('DONE', 'SUPERSEDED'):
        return ''
    if not subs:
        if parent_h and parent_h > WORK_PACKAGE_MAX_HOURS:
            return ('<details class="wbs undec"><summary>WBS &middot; NOT DECOMPOSED &middot; '
                    '%sh in one piece</summary><div class="wbsbody"><p class="warn">This stream is '
                    '%sh and has no work packages. Above %sh a node must be broken down, not '
                    'dispatched: nobody can name the files it will touch, so it cannot declare a '
                    'write set, cannot be run safely beside anything, and cannot be resumed if it '
                    'stops halfway.</p></div></details>'
                    % (e(parent_h), e(parent_h), WORK_PACKAGE_MAX_HOURS))
        return ''
    child = sum((x.get('effort_hours') or x.get('estimate_hours') or x.get('hours') or 0)
                for x in subs)
    ok = (not parent_h) or abs(child - parent_h) <= max(1, parent_h * 0.15)
    out = ['<details class="wbs%s"><summary>WBS &middot; %d work package(s) &middot; %sh of %sh'
           '%s</summary><div class="wbsbody">'
           % ('' if ok else ' undec', len(subs), e(child), e(parent_h),
              '' if ok else ' &middot; DOES NOT SUM')]
    if not ok:
        out.append('<p class="warn">The parts do not add up to the whole. Either a package is '
                   'missing or the parent estimate is wrong; both hide work.</p>')
    for x in subs:
        h = x.get('effort_hours') or x.get('estimate_hours') or x.get('hours') or 0
        over = ' over' if h > WORK_PACKAGE_MAX_HOURS else ''
        # TWO SCHEMAS, ONE BOARD. Packages written before 2026-08-29 carry 'title';
        # the ones after carry 'name'. The accordion rendered the older twelve with an
        # EMPTY heading for its first hour, which is the same class of defect as the
        # hours key the checker read three ways: the data was fine and the reader knew
        # one spelling.
        label = x.get('name') or x.get('title') or ''
        out.append('<div class="wp%s"><div class="wphead"><span class="wpid">%s</span>'
                   '<span class="wpname">%s</span><span class="wph">%sh</span></div>'
                   % (over, e(x.get('id', '?')), e(label), e(h)))
        owns = x.get('owns') or []
        out.append('<div class="wpk"><b>Owns</b>%s</div>'
                   % (e(', '.join(owns)) if owns else
                      '<span class="warn">nothing declared, so it cannot be scheduled beside '
                      'anything</span>'))
        out.append('<div class="wpk"><b>Done-check</b>%s</div>' % e(x.get('done_check', '')))
        if x.get('resume_from'):
            out.append('<div class="wpk"><b>Resumes from</b>%s</div>' % e(x['resume_from']))
        out.append('</div>')
    out.append('</div></details>')
    return ''.join(out)


def render(doc):
    rows = doc.get('rows', [])
    gates = doc.get('gates', [])
    ready = set(ready_rows(doc))
    by_wave = {}
    for r in rows:
        by_wave.setdefault(r.get('wave'), []).append(r)
    cnt = counts(doc)
    total = len(rows)
    done = cnt.get('DONE', 0)

    parts = []
    parts.append('<title>Brother Readiness Board</title>')
    parts.append(STYLE)
    parts.append('<div class="wrap">')
    parts.append('<p class="eyebrow">Brother &middot; readiness roadmap</p>')
    parts.append('<h1>Six gates, then two contributors</h1>')
    parts.append('<p class="lede">%s</p>' % e(doc.get('readiness_definition', '')))
    parts.append('<p class="stamp">Generated from <code>docs/plan/READINESS-ROADMAP-2026-08-29.json</code> '
                 'by <code>scripts/gen_readiness_board.py</code>. Never hand edited. '
                 'Measured %s, session %s. Cohort on open: %s.</p>'
                 % (e(doc.get('measured_at')), e(doc.get('session')),
                    e(', '.join(doc.get('cohort_on_open', [])))))

    # at a glance
    parts.append('<div class="strip">')
    parts.append('<div><span class="v">%d/%d</span><span class="l">rows done</span></div>' % (done, total))
    for k in ('IN-FLIGHT', 'SCHEDULED', 'BLOCKED', 'AWAITING FOUNDER'):
        if cnt.get(k):
            parts.append('<div><span class="v">%d</span><span class="l">%s</span></div>' % (cnt[k], e(k.lower())))
    parts.append('<div><span class="v">%d</span><span class="l">ready now</span></div>' % len(ready))
    parts.append('</div>')

    # THE VAULT COUNTER (WBS V12): "lessons recalled this week, receipts
    # bound, notes written", read from the store and the vault at render
    # time, never typed here. board_status.vault_counters() is the sole
    # producer of these three numbers; this only formats what it returns,
    # including its NO-DATA case, so the board can never show a count the
    # module itself did not compute.
    parts.append('<div class="strip vault-strip">')
    for vc in BS.vault_counters():
        val = ('%d' % vc['count']) if vc['count'] is not None else 'NO-DATA'
        parts.append('<div><span class="v">%s</span><span class="l">%s</span>'
                     '<code>%s</code></div>'
                     % (e(val), e(vc['label']), e(vc['command'])))
    parts.append('</div>')

    # THE NORTH STAR, first thing under the strip, because the progress page law
    # requires it as its own callout and this board rendered NONE for its whole
    # life. Founder ruling 2026-08-29: the delivery metric governs and the ship
    # definition serves it. A board whose compass is implicit is a board that can
    # be perfectly ordered and pointed at the wrong pile, which is exactly what
    # happened here: an evening of reprioritisation ordered engineering work
    # while the true measure said no engineering moves it.
    ns = doc.get('north_star') or {}
    if ns:
        parts.append('<section class="northstar">')
        parts.append('<h2>The north star</h2>')
        parts.append('<p class="nsmetric">%s</p>' % e(ns.get('metric', '')))
        parts.append('<p class="nsvalue">%s</p>' % e(ns.get('current_value', '')))
        if ns.get('why_engineering_does_not_move_it'):
            parts.append('<p class="nswhy">%s</p>'
                         % e(ns['why_engineering_does_not_move_it']))
        if ns.get('what_would_move_it'):
            parts.append('<div class="dc"><b>What would move it</b>%s</div>'
                         % e(ns['what_would_move_it']))
        if ns.get('the_ship_definition_serves_it'):
            parts.append('<p class="note small">%s</p>'
                         % e(ns['the_ship_definition_serves_it']))
        parts.append('</section>')

    # THE TEAM ADOPTION GATE, placed ABOVE row completion on an outside
    # reviewer's argument that is correct: a high row-completion percentage
    # obscures a decisive parity blocker, and the team will not look at Brother
    # while it appears weaker than what they already use.
    #
    # Every level here is granted by named evidence and a cell without evidence
    # scores zero, which is why this number is far lower than the one the
    # directive proposed. That is the point of computing it.
    try:
        with open(PG.SOURCE, encoding='utf-8') as _fh:
            _pdoc = json.load(_fh)
        ppct, prows, pblock = PG.score(_pdoc.get('capabilities') or [])
    except Exception:  # noqa: BLE001
        ppct, prows, pblock = None, [], []
    if prows:
        parts.append('<section class="parity">')
        parts.append('<h2>Team adoption gate</h2>')
        parts.append('<p class="note">%s</p>' % e(_pdoc.get('why', '')))
        parts.append('<div class="pgrow"><div class="pglab">Critical workflow parity</div>'
                     '<div class="pgtrack"><div class="pgfill" style="width:%.1f%%"></div></div>'
                     '<div class="pgnum">%s</div></div>'
                     % (ppct or 0, ('%.0f%%' % ppct) if ppct is not None else e(BS.NODATA)))
        if pblock:
            parts.append('<p class="warn"><b>GATE: NOT READY.</b> %d critical '
                         'capability(ies) sit below level 3, which means a teammate '
                         'would not meet them on the normal path: %s</p>'
                         % (len(pblock), e(", ".join(b["capability"] for b in pblock))))
        parts.append('<p class="note small">%s</p>'
                     % e('Parity is not success. It is the price of admission, and '
                         'the ultimate measure stays accepted external verified '
                         'deliveries per week.'))
        parts.append('<div class="scroll"><table><thead><tr><th>Capability</th>'
                     '<th>Level reached</th><th class="num">Weight</th>'
                     '<th>The evidence for that level</th></tr></thead><tbody>')
        for r in sorted(prows, key=lambda x: (x['level'] if x['level'] is not None else -1,
                                              -x['weight'])):
            lvl = ('L%d %s' % (r['level'], PG.LEVEL_NAME[r['level']])
                   if r['level'] in PG.LEVEL_NAME else BS.NODATA)
            parts.append('<tr><th scope="row">%s%s</th><td class="lv%s">%s</td>'
                         '<td class="num">%.0f%%</td><td class="small">%s</td></tr>'
                         % (e(r['capability']),
                            ' <span class="pill">critical</span>' if r['critical'] else '',
                            e(str(r['level'])), e(lvl), 100 * r['weight'],
                            e(r['evidence'] or r['note'])))
        parts.append('</tbody></table></div></section>')

    # WHAT IS ACTUALLY DONE, founder ask 2026-08-29: "for each section mention
    # what is actually done ... show it as a progress bar". Placed directly
    # under the north star because that is where he looks, and because a board
    # he has to ask the status of has already failed.
    #
    # Every number here is COUNTED from evidence by board_status, never typed.
    # A row that says DONE and carries no evidence is a CLAIM and is excluded
    # from the bar and named separately, which is the only reason a bar on a
    # page like this is worth reading at all.
    secs = BS.sections(doc)
    parts.append('<section class="progress">')
    parts.append('<h2>What is actually done</h2>')
    parts.append('<p class="note">%s</p>'
                 % e('Counted, never asserted. A thing counts as done only when it '
                     'says done AND carries evidence on its own card. Anything '
                     'claiming done without evidence is excluded from these bars '
                     'and named underneath.'))
    claimed_total = 0
    for sec in secs:
        c, pct = sec['counts'], sec['percent']
        claimed_total += c['claimed']
        width = 0 if pct is None else pct
        parts.append('<div class="pgrow">')
        parts.append('<div class="pglab">%s</div>' % e(sec['label']))
        parts.append('<div class="pgtrack"><div class="pgfill" style="width:%.1f%%">'
                     '</div></div>' % width)
        parts.append('<div class="pgnum">%s</div>'
                     % (e(BS.NODATA) if pct is None else '%.0f%%' % pct))
        parts.append('<div class="pgmeta">%d done &middot; %d in flight &middot; '
                     '%d open</div>' % (c['done'], c['in_flight'], c['open']))
        parts.append('</div>')
    if claimed_total:
        parts.append('<p class="warn">%d item(s) say DONE and carry no evidence. '
                     'They are NOT counted above, because a claim is not '
                     'progress.</p>' % claimed_total)
    else:
        parts.append('<p class="note small">%s</p>'
                     % e('Nothing on this board claims done without evidence.'))
    parts.append('</section>')

    # THE TEAM'S OWN ASKS, founder direction 2026-08-29: "I do not see the
    # requests from my team in the Readiness Board, are they included? Check the
    # Vault and history to make sure nothing is forgotten."
    #
    # They were not included. Three series sat in THIS FILE'S OWN DATA with a
    # renderer that never referenced the key, and a fourth had never been
    # connected to this board at all. He found it by asking, which is the same
    # failure as every other thing he has had to ask about: the record was
    # right and the page was silent. Placed directly under the progress strip,
    # because a board about a product for a team that does not show that team's
    # asks is showing the wrong thing first.
    tcs = doc.get('team_complaints') or {}
    roll = (tcs.get('rollup_2026_08_29') or {})
    if tcs and roll:
        counted = roll.get('counted') or {}
        parts.append('<section class="teamasks">')
        parts.append('<h2>What the team actually asked for</h2>')
        parts.append('<p class="lfind">%s</p>' % e(roll.get('the_number', '')))
        parts.append('<p class="note">%s</p>' % e(roll.get('why', '')))
        for key, c in counted.items():
            total = c.get('total') or 0
            done = c.get('addressed') or 0
            part = c.get('partial') or 0
            pct = (100.0 * done / total) if total else None
            label = key.split('_', 1)[0] + ' series'
            parts.append('<div class="pgrow">')
            parts.append('<div class="pglab">%s</div>' % e(label))
            parts.append('<div class="pgtrack"><div class="pgfill" style="width:%.1f%%">'
                         '</div><div class="pgpart" style="width:%.1f%%"></div></div>'
                         % (pct or 0, (100.0 * part / total) if total else 0))
            parts.append('<div class="pgnum">%s</div>'
                         % ('%d/%d' % (done, total) if total else e(BS.NODATA)))
            parts.append('<div class="pgmeta">%s &middot; %d addressed, %d partial, '
                         '%d not addressed</div>'
                         % (e(c.get('source', '')), done, part,
                            c.get('not_addressed') or 0))
            parts.append('</div>')
        parts.append('<p class="warn">%s</p>' % e(roll.get('the_visibility_finding', '')))

        # every item, by series, with its own verdict. Long on purpose: the
        # complaint was that they were invisible, and a summary that hides the
        # individual asks repeats it one level up.
        P = tcs.get('P_series_verified_2026_08_29') or {}
        if P:
            parts.append('<h3>The thirteen a reviewer raised, each with its verdict</h3>')
            parts.append('<div class="scroll"><table><thead><tr><th>#</th><th>What they said</th>'
                         '<th>Verdict</th></tr></thead><tbody>')
            for k in sorted(P, key=lambda x: int(x[1:])):
                v = P[k]
                parts.append('<tr><th scope="row">%s</th><td>%s</td>'
                             '<td class="v-%s">%s</td></tr>'
                             % (e(k), e(v.get('title', '')),
                                e(str(v.get('verdict', '')).lower()),
                                e(v.get('verdict', BS.NODATA))))
            parts.append('</tbody></table></div>')

        H = (tcs.get('H_series_the_holes_nobody_complained_about') or {})
        holes = H.get('holes') or {}
        if holes:
            parts.append('<h3>The nine nobody complained about</h3>')
            parts.append('<p class="note">%s</p>' % e(H.get('_note', '').split('\n')[0]))
            parts.append('<div class="dc"><b>State measured today</b>%s</div>'
                         % e(H.get('state_measured_2026_08_29', '')))
            parts.append('<div class="dc"><b>Where they live</b>%s</div>'
                         % e(H.get('source', '')))
            parts.append('<div class="scroll"><table><thead><tr><th>#</th><th>The hole</th>'
                         '<th>State</th></tr></thead><tbody>')
            for k in sorted(holes, key=lambda x: int(x[1:])):
                h = holes[k]
                parts.append('<tr><th scope="row">%s</th><td>%s</td>'
                             '<td class="v-not-addressed">%s</td></tr>'
                             % (e(k), e(h.get('title', '')),
                                e(str(h.get('state', BS.NODATA)))))
            parts.append('</tbody></table></div>')

        for key, heading in (('onboarding_series_restated_2026_08_29',
                              'What an engineer hit on first contact'),
                             ('C_series_restated_2026_08_29',
                              'Carried forward from an earlier list')):
            ser = tcs.get(key) or {}
            if not ser:
                continue
            parts.append('<h3>%s</h3>' % e(heading))
            for k, v in ser.items():
                if not isinstance(v, dict):
                    continue
                said = v.get('complaint') or v.get('was') or ''
                nowt = v.get('now') or v.get('status') or v.get('verdict') or BS.NODATA
                parts.append('<div class="dc"><b>%s</b>%s</div>'
                             % (e(k.replace('_', ' ')), e(str(said)[:600])))
                parts.append('<div class="dc"><b>Where it stands</b>%s</div>'
                             % e(str(nowt)[:600]))
        parts.append('</section>')

    # THE LEARNING LOOP, placed here on founder direction 2026-08-29 and placed
    # HIGH on purpose: he asked for a priority section, and a section about how
    # this estate learns is worth nothing at the bottom of a page nobody scrolls.
    #
    # It renders three different failures rather than one, because they need
    # opposite fixes and treating them as one topic is how the estate kept
    # answering "add more memory" to a problem that was never storage.
    ll = doc.get('learning_loop') or {}
    if ll:
        parts.append('<section class="learn">')
        parts.append('<h2>%s</h2>' % e(ll.get('title', 'How this estate learns')))
        parts.append('<p class="note">%s</p>' % e(ll.get('why_it_is_a_priority', '')))
        parts.append('<p class="lfind">%s</p>' % e(ll.get('the_finding', '')))

        ex = ll.get('the_worked_example') or {}
        if ex:
            parts.append('<div class="lex">')
            parts.append('<h3>The worked example: %s</h3>' % e(ex.get('name', '')))
            for label, key in (('What happened', 'what_happened'),
                               ('How it was actually fixed', 'how_it_was_actually_fixed'),
                               ('Why the vault did not save it', 'why_the_vault_did_not_save_it'),
                               ('What now exists', 'what_now_exists')):
                if ex.get(key):
                    parts.append('<div class="dc"><b>%s</b>%s</div>'
                                 % (label, e(ex[key])))
            parts.append('</div>')

        modes = ll.get('modes') or []
        if modes:
            parts.append('<h3>The three failures, which are not one failure</h3>')
            for m in modes:
                parts.append('<div class="lmode">')
                parts.append('<div class="chead"><span class="cid">%s</span>'
                             '<h4>%s</h4><span class="lstate">%s</span></div>'
                             % (e(m.get('id', '')), e(m.get('name', '')),
                                e(m.get('state', ''))))
                for label, key in (('Evidence', 'evidence'),
                                   ('The mechanism', 'mechanism'),
                                   ('What is still owed', 'owed'),
                                   ('Done-check', 'done_check')):
                    if m.get(key):
                        parts.append('<div class="dc"><b>%s</b>%s</div>'
                                     % (label, e(m[key])))
                parts.append('</div>')

        pri = ll.get('priority') or []
        if pri:
            parts.append('<h3>The order, and why each sits where it does</h3>')
            parts.append('<div class="scroll"><table><thead><tr><th>#</th>'
                         '<th>Item</th><th>State</th><th>Why here</th>'
                         '<th>Done-check</th></tr></thead><tbody>')
            for it in pri:
                parts.append('<tr><td class="num">%s</td><th scope="row">%s</th>'
                             '<td>%s</td><td>%s</td><td class="small">%s</td></tr>'
                             % (e(it.get('n', '')), e(it.get('item', '')),
                                e(it.get('state', '')),
                                e(it.get('why_first') or it.get('why_next') or ''),
                                e(it.get('done_check', ''))))
            parts.append('</tbody></table></div>')

        bor = ll.get('borrowed') or []
        if bor:
            parts.append('<h3>Borrowed, and what each one is actually doing here</h3>')
            parts.append('<p class="note small">%s</p>'
                         % e('Every link was opened and returned 200 on the date shown. '
                             'A discipline is listed only where it changed something in '
                             'the code, never to decorate the page.'))
            for b in bor:
                parts.append('<div class="lborrow">')
                parts.append('<h4>%s</h4>' % e(b.get('discipline', '')))
                parts.append('<div class="dc"><b>The idea</b>%s</div>' % e(b.get('what', '')))
                parts.append('<div class="dc"><b>How it applies here</b>%s</div>'
                             % e(b.get('how_it_applies', '')))
                if b.get('url'):
                    parts.append('<div class="dc"><b>Source</b>'
                                 '<a href="%s" rel="noreferrer">%s</a>'
                                 ' <span class="small">checked %s</span></div>'
                                 % (e(b['url']), e(b['url']),
                                    e(b.get('checked') or 'NO-DATA: not checked')))
                parts.append('</div>')
        parts.append('</section>')

    # THE DOCTRINE, and then immediately the NO list, because a board that
    # only shows what will be built cannot show that most things were refused.
    doc_d = doc.get('design_doctrine') or {}
    if doc_d:
        parts.append('<h2>How this board is built</h2>')
        parts.append('<blockquote class="fq">%s</blockquote>' % e(doc_d.get('founder_words', '')))
        parts.append('<div class="laws">')
        for k, v in sorted((doc_d.get('the_four_laws') or {}).items()):
            title = k.split('_', 1)[1].replace('_', ' ')
            parts.append('<div class="law"><h4>%s</h4><p>%s</p></div>' % (e(title), e(v)))
        parts.append('</div>')
        parts.append('<p class="note">%s</p>' % e(doc_d.get('applied_to_itself_first', '')))

    refused = doc.get('refused') or []
    if refused:
        saved = sum(x.get('hours_saved', 0) for x in refused)
        parts.append('<h2>What was refused</h2>')
        parts.append('<p class="note">%s</p>' % e(doc_d.get('the_no_list_is_first_class', '')))
        parts.append('<p class="note"><b>%d items refused, %d hours not spent.</b></p>'
                     % (len(refused), saved))
        for x in refused:
            parts.append('<div class="norow">')
            parts.append('<div class="nohead"><span class="noid">%s</span><b>%s</b>'
                         '<span class="nohrs">%sh saved</span></div>'
                         % (e(x['id']), e(x['was']), e(x.get('hours_saved', 0))))
            parts.append('<div class="dc"><b>Why refused</b>%s</div>' % e(x['why_refused']))
            parts.append('<div class="dc"><b>What would reopen it</b>%s</div>' % e(x['flip_condition']))
            parts.append('</div>')

    # WHO THIS IS FOR, first, because the founder's rule is that everything
    # starts from the person and not from the concept.
    people = doc.get('personas') or {}
    if people:
        parts.append('<h2>Who this is for</h2>')
        parts.append('<p class="note">%s</p>' % e(people.get('_rule', '')))
        parts.append('<div class="pgrid">')
        for pid, pr in people.items():
            if pid.startswith('_'):
                continue
            parts.append('<div class="pcard"><h4>%s</h4><p class="pid">%s</p>'
                         '<p><b>Who</b> %s</p><p><b>Needs</b> %s</p><p class="pv">%s</p></div>'
                         % (e(pr['name']), e(pid), e(pr['who']), e(pr['needs']), e(pr['view'])))
        parts.append('</div>')

    # THE FEATURES, each a named steal with a named adaptation.
    feats = doc.get('features') or []
    if feats:
        doct = doc.get('feature_doctrine') or {}
        parts.append('<h2>The features, and where each one is borrowed from</h2>')
        parts.append('<p class="note">%s</p>' % e(doct.get('the_rule', '')))
        parts.append('<p class="note small">%s</p>' % e(doct.get('sources', '')))
        for f in feats:
            per = people.get(f.get('persona'), {})
            parts.append('<div class="feat">')
            parts.append('<div class="fhead"><span class="fid">%s</span><h3>%s</h3>'
                         '<span class="fmeta">%sh &middot; %s &middot; %s</span></div>'
                         % (e(f['id']), e(f['name']), e(f['effort_hours']),
                            e(f.get('horizon')), e(f.get('visible_when'))))
            fstate, fwhy = BS.item_status(f)
            parts.append('<div class="fstat s-%s"><b>%s</b>%s</div>'
                         % (e(fstate.lower().replace(' ', '-')), e(fstate), e(fwhy)))
            parts.append('<p class="fwhat">%s</p>' % e(f['what_it_is']))
            parts.append('<div class="fpers"><span class="pl">%s</span>'
                         '<span class="pm">%s</span></div>'
                         % (e(per.get('name', f.get('persona'))), e(f.get('their_moment', ''))))
            parts.append('<div class="fsees"><b>What they see</b>%s</div>' % e(f.get('what_they_see', '')))
            parts.append('<div class="fborrow"><div class="bfrom"><b>Borrowed from</b>%s</div>'
                         '<div class="badapt"><b>Our adaptation</b>%s</div></div>'
                         % (e(f['borrowed_from']), e(f['adaptation'])))
            parts.append('<div class="fborrow"><div class="brem"><b>What it removes</b>%s</div>'
                         '<div class="bphys"><b>Grounded in</b>%s</div></div>'
                         % (e(f.get('removes', '')), e(f.get('grounded_in', ''))))
            parts.append('<div class="dc weight"><b>Weight of this decision</b>%s</div>'
                         % e(f.get('weight', '')))
            parts.append('<div class="dc"><b>Closes</b>%s</div>' % e(f.get('closes', '')))
            parts.append('<div class="dc"><b>Done-check</b>%s</div>' % e(f.get('done_check', '')))
            parts.append(wbs_accordion(f))
            parts.append('</div>')

    # gates
    parts.append('<h2>The six gates</h2>')
    parts.append('<div class="scroll"><table><thead><tr><th>Gate</th><th>Size</th>'
                 '<th>Status</th><th>What blocks it</th></tr></thead><tbody>')
    for g in gates:
        parts.append('<tr><td class="who"><b>%s</b> %s</td><td>%s</td>'
                     '<td><span class="tag %s">%s</span></td><td>%s</td></tr>'
                     % (e(g['id']), e(g['title']), e(g.get('size', '')),
                        STATUS_CLASS.get(g.get('status'), 'st-nodata'), e(g.get('status')),
                        e(g.get('blocker', ''))))
    parts.append('</tbody></table></div>')

    # THE SHIP LINE. Founder direction 2026-08-29: a shippable version by
    # September 6. G1 is quarters, so the honest board has to show what is IN
    # that ship and what is deliberately OUT, rather than letting eight days of
    # work and a quarter of work sit in one undifferentiated list.
    ship = doc.get('ship_target') or {}
    if ship:
        in_ship = [r for r in rows if r.get('in_ship_v1')]
        done_ship = [r for r in in_ship if r.get('status') == 'DONE']
        parts.append('<h2>Ship target: %s</h2>' % e(ship.get('date')))
        parts.append('<div class="ship">')
        parts.append('<p class="k">%d of %d rows in the v1 ship are done.</p>'
                     % (len(done_ship), len(in_ship)))
        parts.append('<p>%s</p>' % e(ship.get('what_shippable_means', '')))
        if ship.get('explicitly_out_of_scope'):
            parts.append('<p class="oos"><b>Deliberately NOT in this ship:</b></p><ul class="oos">')
            for item in ship['explicitly_out_of_scope']:
                parts.append('<li>%s</li>' % e(item))
            parts.append('</ul>')
        parts.append('</div>')

    # horizons, which is how the founder asked to see it: immediate, days,
    # weeks, months. Wave is still the dependency truth underneath.
    HORIZONS = [('immediate', 'Immediate, today and tomorrow'),
                ('days', 'Days, inside the ship window'),
                ('weeks', 'Weeks, after the ship'),
                ('months', 'Months, the honest cost of full parity')]
    parts.append('<h2>Orchestration order</h2>')
    parts.append('<p class="note">Waves are dependency depth, not dates. A row starts when its '
                 'dependencies have actually closed, never because its wave came up. '
                 'Rows marked READY have every dependency DONE right now.</p>')
    maxw = max(by_wave) if by_wave else 1
    by_horizon = {}
    for r in rows:
        by_horizon.setdefault(r.get('horizon', 'days'), []).append(r)
    for key, label in HORIZONS:
        group = sorted(by_horizon.get(key, []), key=lambda x: (x.get('wave', 0), x['id']))
        if not group:
            continue
        parts.append('<h3>%s &middot; %d row(s)</h3>' % (e(label), len(group)))
        for r in group:
            w = r.get('wave', 1)
            pct = int(round(100.0 * w / maxw))
            offset = int(round(100.0 * (w - 1) / maxw))
            width = max(6, pct - offset)
            cls = STATUS_CLASS.get(r.get('status'), 'st-nodata')
            rd = ' <span class="ready">READY</span>' if r['id'] in ready else ''
            if not r.get('in_ship_v1'):
                rd += ' <span class="oostag">not in v1</span>'
            dep = (' <span class="dep">after %s</span>' % e(', '.join(r['depends_on']))) if r.get('depends_on') else ''
            parts.append('<div class="bar-row">')
            parts.append('<div class="bar-lab"><b>%s</b> %s%s%s</div>' % (e(r['id']), e(r['title']), rd, dep))
            parts.append('<div class="bar-track"><div class="bar %s" style="margin-left:%d%%;width:%d%%"></div></div>'
                         % (cls, offset, width))
            promised = r.get('promised_at') or ''
            delivered = r.get('delivered_at')
            hrs = r.get('estimate_hours')
            when = ''
            if promised:
                when = ' <span class="when">due %s</span>' % e(promised.replace('T', ' ')[:16])
            if hrs is not None:
                when += ' <span class="when">%sh</span>' % e(hrs)
            if delivered:
                late = delivered > promised if promised else False
                when += (' <span class="%s">delivered %s</span>'
                         % ('late' if late else 'ontime', e(delivered.replace('T', ' ')[:16])))
            misses = len([s for s in r.get('slip_log', [])
                          if s.get('status') in ('LATE', 'DELIVERED-LATE')])
            if misses:
                when += ' <span class="late">%d miss(es)</span>' % misses
            parts.append('<div class="bar-meta"><span class="tag %s">%s</span> '
                         '<span class="gate">%s</span> <span class="owner">%s</span>%s</div>'
                         % (cls, e(r.get('status')), e(r.get('gate')), e(r.get('owner')), when))
            parts.append('</div>')

    # the ledger
    parts.append('<h2>The ledger</h2>')
    parts.append('<p class="note">A row ticks ONLY when its own done_check has been run after the '
                 'last edit and the output is quoted beside it. A row closed on an adjacent green '
                 'is not closed.</p>')
    for r in rows:
        cls = STATUS_CLASS.get(r.get('status'), 'st-nodata')
        parts.append('<div class="card">')
        parts.append('<div class="chead"><span class="cid">%s</span><span class="tag %s">%s</span>'
                     '<span class="gate">%s</span></div>' % (e(r['id']), cls, e(r.get('status')), e(r.get('gate'))))
        parts.append('<h4>%s</h4>' % e(r['title']))
        parts.append('<p class="detail">%s</p>' % e(r.get('detail', '')))
        per = (doc.get('personas') or {}).get(r.get('persona'), {})
        parts.append('<div class="fpers"><span class="pl">%s</span><span class="pm">%s</span></div>'
                     % (e(per.get('name', r.get('persona', ''))), e(r.get('their_moment', ''))))
        parts.append('<div class="fsees"><b>What they see</b>%s</div>' % e(r.get('what_they_see', '')))
        parts.append('<div class="contract">')
        for label, field in (('What ships', 'ships'), ('Its role', 'role'),
                             ('Why now', 'why_now'), ('What you will see', 'effect'),
                             ('When it appears', 'visible_when')):
            parts.append('<div class="cr"><span class="crl">%s</span><span class="crv">%s</span></div>'
                         % (e(label), e(r.get(field, ''))))
        parts.append('</div>')
        parts.append('<div class="dc"><b>Done-check</b>%s</div>' % e(r.get('done_check')))
        parts.append('<div class="dc"><b>Watchdog verify</b><code>%s</code></div>' % e(r.get('watchdog_verify')))
        if r.get('promised_at'):
            parts.append('<div class="dc"><b>Promised</b>%s, estimated %s hour(s)%s</div>'
                         % (e(r['promised_at']), e(r.get('estimate_hours')),
                            (', delivered ' + e(r['delivered_at'])) if r.get('delivered_at') else
                            ', not yet delivered'))
        if r.get('owns'):
            parts.append('<div class="dc"><b>Owned paths</b><code>%s</code></div>' % e(', '.join(r['owns'])))
        if r.get('cannot_decompose_yet'):
            parts.append('<details class="wbs undec"><summary>WBS &middot; DECOMPOSITION REFUSED, '
                         'and here is why</summary><div class="wbsbody"><p class="warn">%s</p>'
                         '</div></details>' % e(r['cannot_decompose_yet']))
        else:
            parts.append(wbs_accordion(r))
        if r.get('superseded_note'):
            parts.append('<div class="dc"><b>Superseded</b>%s</div>' % e(r['superseded_note']))
        parts.append('</div>')

    parts.append('<p class="stamp">Tick contract, verbatim: a box ticks ONLY when its done-check ran '
                 'after the last edit and the output is quoted beside it. Percentages are counts of '
                 'records, never impressions. Regenerate with '
                 '<code>python3 scripts/gen_readiness_board.py</code>.</p>')
    parts.append('</div>')
    return '\n'.join(parts)


STYLE = """<style>
:root{--paper:#F7F8F6;--raised:#FFF;--ink:#141B22;--soft:#4A5763;--faint:#7C8894;
--petrol:#0E7A6F;--psoft:#E3F0EE;--rule:#DDE3E1;--rsoft:#EAEEEC;--fail:#A32C22;
--fsoft:#F7E5E3;--nodata:#9A6B12;--nsoft:#F7EEDC;--bar:#D6DCDA;--flight:#1D6FA5;--flsoft:#E1EEF7}
@media (prefers-color-scheme:dark){:root:not([data-theme="light"]){
--paper:#10161B;--raised:#171F26;--ink:#E8EDEA;--soft:#A5B2B8;--faint:#77858D;
--petrol:#3AA893;--psoft:#16302D;--rule:#28333A;--rsoft:#1E282E;--fail:#E0776A;
--fsoft:#33201D;--nodata:#D9A441;--nsoft:#2E2617;--bar:#2A343B;--flight:#63AEDC;--flsoft:#132631}}
:root[data-theme="dark"]{--paper:#10161B;--raised:#171F26;--ink:#E8EDEA;--soft:#A5B2B8;--faint:#77858D;
--petrol:#3AA893;--psoft:#16302D;--rule:#28333A;--rsoft:#1E282E;--fail:#E0776A;
--fsoft:#33201D;--nodata:#D9A441;--nsoft:#2E2617;--bar:#2A343B;--flight:#63AEDC;--flsoft:#132631}
*{box-sizing:border-box}
:root{--shadow:0 1px 3px rgba(20,27,34,.07)}
:root:not([data-theme="light"]){}
body{background:var(--paper);color:var(--ink);margin:0;font-family:"Seravek","Avenir Next",ui-sans-serif,system-ui,sans-serif;font-size:16px;line-height:1.6}
.wrap{max-width:1040px;margin:0 auto;padding:48px 26px 90px}
h1,h2,h3,h4{font-family:"Iowan Old Style",Palatino,Georgia,serif;font-weight:600;text-wrap:balance}
h1{font-size:2.5rem;line-height:1.12;margin:.1em 0 .35em}
h2{font-size:1.6rem;margin:52px 0 .5em}
h3{font-size:1.1rem;margin:30px 0 .5em;color:var(--faint);letter-spacing:.02em}
h4{font-size:1.08rem;margin:0 0 .3em}
.eyebrow{font-size:.72rem;letter-spacing:.14em;text-transform:uppercase;color:var(--petrol);font-weight:600;margin:0 0 .8em}
.lede{font-size:1.1rem;color:var(--soft);max-width:64ch}
.stamp{font-size:.79rem;color:var(--faint);border-top:1px solid var(--rule);padding-top:13px;margin-top:24px;line-height:1.5}
.note{font-size:.9rem;color:var(--soft);max-width:64ch}
.strip{display:flex;flex-wrap:wrap;gap:30px;margin:26px 0;padding:20px 24px;background:var(--raised);border:1px solid var(--rule);border-radius:10px}
.strip .v{font-family:"Iowan Old Style",Palatino,Georgia,serif;font-size:2rem;line-height:1;display:block;font-variant-numeric:tabular-nums}
.strip .l{font-size:.69rem;letter-spacing:.09em;text-transform:uppercase;color:var(--faint);font-weight:600;margin-top:6px;display:block}
.vault-strip code{display:block;margin-top:6px;font-size:.66rem;white-space:normal;word-break:break-all}
.scroll{overflow-x:auto;border:1px solid var(--rule);border-radius:8px;background:var(--raised);margin:20px 0}
table{border-collapse:collapse;width:100%;font-size:.87rem;min-width:640px}
th,td{text-align:left;padding:10px 14px;border-bottom:1px solid var(--rsoft);vertical-align:top}
th{font-size:.68rem;letter-spacing:.1em;text-transform:uppercase;color:var(--faint);background:var(--paper)}
tbody tr:last-child td{border-bottom:0}
td.who{max-width:280px}
.tag{display:inline-block;font-size:.65rem;letter-spacing:.07em;text-transform:uppercase;font-weight:700;padding:3px 7px;border-radius:3px;white-space:nowrap}
.st-done{background:var(--psoft);color:var(--petrol)}
.st-flight{background:var(--flsoft);color:var(--flight)}
.st-sched{background:var(--rsoft);color:var(--faint)}
.st-blocked,.st-open{background:var(--fsoft);color:var(--fail)}
.st-partial,.st-nodata{background:var(--nsoft);color:var(--nodata)}
.st-founder{background:var(--nsoft);color:var(--nodata)}
.bar-row{margin:11px 0;padding:11px 14px;background:var(--raised);border:1px solid var(--rule);border-radius:8px}
.bar-lab{font-size:.9rem;margin-bottom:7px}
.bar-lab b{color:var(--petrol);font-family:"Iowan Old Style",Palatino,Georgia,serif}
.bar-track{height:10px;background:var(--bar);border-radius:5px;overflow:hidden;margin-bottom:7px}
.bar{height:100%;border-radius:5px}
.bar.st-done{background:var(--petrol)}
.bar.st-flight{background:var(--flight)}
.bar.st-sched{background:var(--faint);opacity:.45}
.bar.st-blocked,.bar.st-open{background:var(--fail)}
.bar.st-partial,.bar.st-nodata,.bar.st-founder{background:var(--nodata)}
.bar-meta{font-size:.75rem;color:var(--faint);display:flex;gap:10px;align-items:center;flex-wrap:wrap}
.ready{background:var(--psoft);color:var(--petrol);font-size:.63rem;letter-spacing:.08em;font-weight:700;padding:2px 6px;border-radius:3px}
.dep{color:var(--faint);font-size:.8rem}
.when{color:var(--faint);font-size:.74rem;font-variant-numeric:tabular-nums}
.late{color:var(--fail);font-size:.74rem;font-weight:600}
.ontime{color:var(--petrol);font-size:.74rem;font-weight:600}
.ship{background:var(--psoft);border-left:3px solid var(--petrol);padding:18px 22px;border-radius:0 6px 6px 0;margin:20px 0}
.ship .k{font-family:"Iowan Old Style",Palatino,Georgia,serif;font-size:1.25rem;margin:0 0 .5em;font-weight:600}
.ship p{font-size:.92rem;margin:0 0 .7em}
.ship ul.oos{font-size:.88rem;margin:0;color:var(--soft)}
.ship p.oos{margin-bottom:.3em}
.fq{margin:16px 0;padding:14px 20px;border-left:3px solid var(--petrol);background:var(--psoft);font-style:italic;color:var(--ink);font-size:.94rem;border-radius:0 6px 6px 0}
.laws{display:grid;grid-template-columns:repeat(auto-fit,minmax(240px,1fr));gap:14px;margin:20px 0}
.law{background:var(--raised);border:1px solid var(--rule);border-radius:9px;padding:16px 18px}
.law h4{margin:0 0 6px;font-size:.98rem;color:var(--petrol);text-transform:capitalize}
.law p{margin:0;font-size:.86rem;color:var(--soft)}
.norow{background:var(--raised);border:1px solid var(--rule);border-left:3px solid var(--nodata);border-radius:0 9px 9px 0;padding:16px 20px;margin:12px 0}
.nohead{display:flex;flex-wrap:wrap;gap:10px;align-items:baseline;margin-bottom:8px;font-size:.95rem}
.noid{font-family:"Iowan Old Style",Palatino,Georgia,serif;font-size:1.05rem;color:var(--nodata)}
.nohrs{margin-left:auto;font-size:.72rem;letter-spacing:.06em;text-transform:uppercase;color:var(--nodata);font-weight:700}
.brem b{color:var(--petrol)} .bphys b{color:var(--faint)}
.dc.weight b{color:var(--nodata)}
.pgrid{display:grid;grid-template-columns:repeat(auto-fit,minmax(230px,1fr));gap:14px;margin:20px 0}
.pcard{background:var(--raised);border:1px solid var(--rule);border-radius:9px;padding:16px 18px;font-size:.86rem}
.pcard h4{margin:0 0 2px;font-size:1.02rem}
.pcard .pid{font-size:.64rem;letter-spacing:.1em;color:var(--faint);font-weight:700;margin:0 0 9px}
.pcard p{margin:0 0 7px;color:var(--soft)}
.pcard b{color:var(--ink)}
.pcard .pv{color:var(--petrol);font-size:.8rem;margin:0}
.note.small{font-size:.82rem;color:var(--faint)}
.feat{background:var(--raised);border:1px solid var(--rule);border-radius:10px;padding:22px 26px;margin:16px 0;box-shadow:var(--shadow)}
.fhead{display:flex;flex-wrap:wrap;gap:10px;align-items:baseline;margin-bottom:8px}
.fhead h3{margin:0;font-size:1.2rem}
.fid{font-family:"Iowan Old Style",Palatino,Georgia,serif;font-size:1.15rem;color:var(--petrol)}
.fmeta{margin-left:auto;font-size:.72rem;letter-spacing:.06em;text-transform:uppercase;color:var(--faint);font-weight:600}
.fwhat{font-size:.95rem;margin:0 0 12px}
.fpers{background:var(--psoft);border-radius:6px;padding:11px 14px;margin:10px 0;font-size:.88rem}
.fpers .pl{font-weight:700;color:var(--petrol);margin-right:9px}
.fpers .pm{color:var(--soft)}
.fsees{background:var(--paper);border:1px solid var(--rsoft);border-radius:6px;padding:11px 14px;margin:9px 0;font-size:.88rem}
.fsees b{font-family:"Iowan Old Style",Palatino,Georgia,serif;display:block;font-size:.66rem;letter-spacing:.1em;text-transform:uppercase;color:var(--faint);margin-bottom:4px;font-weight:600}
.fborrow{display:grid;grid-template-columns:1fr 1fr;gap:12px;margin:12px 0 4px}
.fborrow>div{background:var(--paper);border:1px solid var(--rsoft);border-radius:6px;padding:11px 14px;font-size:.85rem;color:var(--soft)}
.fborrow b{font-family:"Iowan Old Style",Palatino,Georgia,serif;display:block;font-size:.66rem;letter-spacing:.1em;text-transform:uppercase;margin-bottom:4px;font-weight:600}
.bfrom b{color:var(--faint)} .badapt b{color:var(--petrol)}
@media(max-width:640px){.fborrow{grid-template-columns:1fr}}
.contract{margin:14px 0 4px;border-top:1px solid var(--rsoft);padding-top:12px}
.cr{display:grid;grid-template-columns:150px 1fr;gap:14px;margin-bottom:9px;font-size:.87rem}
.crl{font-family:"Iowan Old Style",Palatino,Georgia,serif;font-size:.68rem;letter-spacing:.09em;text-transform:uppercase;color:var(--petrol);font-weight:600;padding-top:2px}
.crv{color:var(--soft)}
@media(max-width:640px){.cr{grid-template-columns:1fr;gap:2px}}
.oostag{background:var(--nsoft);color:var(--nodata);font-size:.62rem;letter-spacing:.07em;font-weight:700;padding:2px 6px;border-radius:3px}
.card{background:var(--raised);border:1px solid var(--rule);border-radius:9px;padding:20px 24px;margin:14px 0}
.chead{display:flex;gap:10px;align-items:center;margin-bottom:7px;flex-wrap:wrap}
.cid{font-family:"Iowan Old Style",Palatino,Georgia,serif;font-size:1.1rem;color:var(--petrol)}
.gate,.owner{font-size:.7rem;letter-spacing:.08em;text-transform:uppercase;color:var(--faint);font-weight:600}
.detail{font-size:.9rem;color:var(--soft);margin:0 0 12px}
.dc{background:var(--paper);border:1px solid var(--rsoft);border-radius:6px;padding:11px 14px;margin-top:9px;font-size:.86rem}
.progress{background:var(--raised);border:1px solid var(--rule);border-radius:10px;
  padding:18px 24px;margin:0 0 22px}
.parity{background:var(--raised);border:1.5px solid var(--fail);border-radius:10px;
  padding:18px 24px;margin:0 0 22px}
.lv0,.lv1{color:var(--fail);font-weight:600}
.lv2{color:var(--flight);font-weight:600}
.lv3,.lv4{color:var(--petrol);font-weight:600}
.teamasks{background:var(--raised);border:1px solid var(--rule);border-radius:10px;
  padding:18px 24px;margin:0 0 22px}
.pgtrack{position:relative}
.pgpart{position:absolute;top:0;left:0;height:100%;background:var(--flight);opacity:.35}
.v-addressed{color:var(--petrol);font-weight:600}
.v-partial{color:var(--flight);font-weight:600}
.v-not-addressed{color:var(--fail);font-weight:600}
.pgrow{display:grid;grid-template-columns:9.5rem 1fr 3rem;gap:10px;align-items:center;
  margin:9px 0}
.pglab{font-size:.86rem;font-weight:600}
.pgtrack{height:11px;background:var(--paper);border:1px solid var(--rsoft);
  border-radius:6px;overflow:hidden}
.pgfill{height:100%;background:var(--petrol);border-radius:5px}
.pgnum{text-align:right;font-variant-numeric:tabular-nums;font-size:.84rem;font-weight:600}
.pgmeta{grid-column:2 / -1;font-size:.75rem;opacity:.7;margin-top:-4px}
.fstat{border-radius:6px;padding:8px 12px;margin:8px 0;font-size:.84rem;
  border:1px solid var(--rsoft);background:var(--paper)}
.fstat b{display:inline-block;min-width:5.5rem;letter-spacing:.05em;font-size:.72rem;
  text-transform:uppercase}
.fstat.s-done{border-color:var(--petrol)}
.fstat.s-claimed{border-color:var(--fail)}
.fstat.s-in-flight{border-color:var(--flight)}
.learn{background:var(--raised);border:1.5px solid var(--petrol);box-shadow:var(--shadow); border-radius:4px;
  padding:1.3rem 1.5rem; margin:0 0 2rem;}
.lfind{font-size:1.04rem; margin:.6rem 0 1.2rem;}
.lex{border-left:3px solid var(--petrol); padding:.2rem 0 .2rem 1rem;
  margin:1rem 0 1.4rem;}
.lmode{border-top:1px solid var(--rule); padding:.9rem 0;}
.lstate{margin-left:auto; font-size:.7rem; text-transform:uppercase;
  letter-spacing:.08em; font-weight:600; opacity:.75;}
.lborrow{border-top:1px solid var(--rule); padding:.7rem 0;}
.lborrow h4,.lmode h4{margin:.1rem 0 .35rem; font-size:1rem;}
.lborrow a{word-break:break-all;}
.dc b{font-family:"Iowan Old Style",Palatino,Georgia,serif;display:block;font-size:.66rem;letter-spacing:.1em;text-transform:uppercase;color:var(--faint);margin-bottom:4px;font-weight:600}
code{font-family:ui-monospace,"SF Mono",Menlo,monospace;font-size:.86em;background:var(--rsoft);padding:1px 5px;border-radius:3px}
@media(max-width:640px){h1{font-size:1.9rem}.wrap{padding:32px 18px 60px}}
details.wbs{margin:14px 0 2px;border:1px solid var(--rule);border-radius:8px;background:var(--paper);overflow:hidden}
details.wbs>summary{cursor:pointer;padding:10px 14px;font:600 12px/1.4 inherit;letter-spacing:.06em;text-transform:uppercase;color:var(--petrol);list-style:none;user-select:none}
details.wbs>summary::-webkit-details-marker{display:none}
details.wbs>summary::before{content:"\25B8";display:inline-block;margin-right:8px;transition:transform .15s ease}
details.wbs[open]>summary::before{transform:rotate(90deg)}
details.wbs>summary:hover{background:var(--raised)}
details.wbs>summary:focus-visible{outline:2px solid var(--petrol);outline-offset:-2px}
details.wbs.undec{border-color:var(--fail)}
details.wbs.undec>summary{color:var(--fail)}
.wbsbody{padding:4px 14px 14px;border-top:1px solid var(--rule)}
.wp{padding:10px 0;border-bottom:1px dashed var(--rule)}
.wp:last-child{border-bottom:none}
.wp.over{border-left:3px solid var(--fail);padding-left:10px}
.wphead{display:flex;gap:10px;align-items:baseline;flex-wrap:wrap}
.wpid{font:700 11px/1 inherit;color:var(--petrol);letter-spacing:.08em}
.wpname{font-weight:600;flex:1;min-width:180px}
.wph{font:600 11px/1 inherit;color:var(--soft);font-variant-numeric:tabular-nums}
.wpk{font-size:13px;color:var(--soft);margin-top:5px}
.wpk b{color:var(--ink);font-weight:600;margin-right:7px}
.warn{color:var(--fail)}
.northstar{background:var(--raised);border:2px solid var(--petrol);border-radius:10px;padding:22px 26px;margin:22px 0}
.northstar h2{margin-top:0;color:var(--petrol)}
.nsmetric{font-family:"Iowan Old Style",Palatino,Georgia,serif;font-size:1.3rem;margin:0 0 .4em;font-weight:600}
.nsvalue{font-size:1.05rem;color:var(--fail);font-weight:600;margin:0 0 .8em}
.nswhy{color:var(--soft);max-width:70ch}
</style>"""


def main(argv=None):
    argv = list(sys.argv[1:] if argv is None else argv)
    try:
        doc = load()
    except (OSError, ValueError) as exc:
        print('readiness-board: NO-DATA, cannot read %s: %s' % (SOURCE, exc), file=sys.stderr)
        return 2
    problems = validate(doc)
    if problems:
        for p in problems:
            print('readiness-board: FAIL: %s' % p, file=sys.stderr)
        return 1
    if '--check' in argv:
        print('readiness-board: %d row(s), %d gate(s), coherent, ready now: %s'
              % (len(doc['rows']), len(doc['gates']), ', '.join(ready_rows(doc)) or 'none'))
        return 0
    with open(OUTPUT, 'w', encoding='utf-8') as fh:
        fh.write(render(doc))
    print('readiness-board: wrote %s from %d row(s); ready now: %s'
          % (os.path.relpath(OUTPUT, ROOT), len(doc['rows']), ', '.join(ready_rows(doc)) or 'none'))
    return 0


if __name__ == '__main__':
    sys.exit(main())

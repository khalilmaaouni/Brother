#!/usr/bin/env python3
"""swarm_status: one glanceable table saying what a swarm run is doing RIGHT NOW.

WHAT THIS IS FOR. scripts/graph_loop.py already decides what may run and what
may run beside what, and prints that decision as a dispatcher's log. A person
walking up to a live run wants a different thing: one small table, one row per
unit, saying RUNNING / READY / DEFERRED / BLOCKED and, for anything running,
how long it has been running. This is that projection and nothing else. It
DECIDES NOTHING: every verdict here comes from graph_loop.plan() and from
claim_store's own liveness rule, never from arithmetic of its own.

THREE RULES IT EXISTS TO KEEP, because a status table that guesses is worse
than no status table at all: people trust tables.

ONE: SHOW ONLY WHAT IS KNOWN. A unit's elapsed time is real only when
something durable recorded when it started. claims.json records claimed_at;
a roadmap row marked IN-FLIGHT records nothing of the kind. So a unit in
flight with no claim prints NO-DATA in its time cell rather than a plausible
number. Measured on this repository the day this was written: eleven rows
were IN-FLIGHT and NOT ONE had a claim record, so every time cell was
NO-DATA. A version of this that had timed from "when I first saw it" would
have printed eleven confident wrong numbers.

TWO: THE THREE ZEROS ARE DIFFERENT FACTS. "0 conflicts, none found", "0
conflicts, nothing was tested" and "0 conflicts, the conflict data could not
be read" look identical as the character 0 and mean opposite things. They are
reported here as three distinct kinds (NONE, NOTHING-TESTED, NO-DATA) and the
count is None, never 0, for the last one.

THREE: A REFUSED BOARD IS NOT AN IDLE BOARD. When machine_capacity() returns
0 (this estate refuses under an 8 GiB disk floor) nothing is dispatchable, and
an empty DISPATCH section then reads exactly like a calm system with no work.
So capacity 0 prints its own banner carrying the scheduler's own reason.

HOW A DEFERRAL'S REAL REASON IS KEPT REAL. graph_loop defers a ready unit for
exactly five reasons and phrases each one itself. Those phrasings are matched
here by prefix (REASON_* below, copied from scripts/graph_loop.py) so a row
says the real cause: waiting on an event, gated to the founder, missing a
declared write set, out of slots, or genuinely overlapping another unit's
paths. A reason matching none of them is reported as unrecognised and turns
the conflict count into NO-DATA, because a deferral this cannot classify is
one it cannot claim to have tested for conflicts.

Exit 0 a board was rendered. Exit 2 NO-DATA, the graph could not be read.
Standard library only.

Mirrors: the argument parsing and exit-code shape are scripts/board_status.py's
(argparse, --json as store_true, main(argv) returning 0 or 2, sys.exit(main()))
crossed with scripts/graph_loop.py's NO-DATA-on-unreadable-source refusal. The
sys.path guard is scripts/continuity.py's.
"""
import argparse
import json
import os
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.insert(0, HERE)
import claim_store  # noqa: E402
import graph_loop  # noqa: E402

ROOT = os.path.dirname(HERE)

#: The same claim store loop_bridge.py's main() defaults to
#: (scripts/loop_bridge.py: os.path.join(dirname(__file__), "..", "docs",
#: "plan", "claims.json")), spelled through ROOT rather than a relative walk.
CLAIMS = os.path.join(ROOT, 'docs', 'plan', 'claims.json')

NODATA = 'NO-DATA'

#: graph_loop.plan()'s own deferral phrasings, copied verbatim from
#: scripts/graph_loop.py so a change there shows up here as an unrecognised
#: reason rather than as a silently wrong classification.
REASON_EVENT = 'EVENT-WAIT: '
REASON_FOUNDER = 'FOUNDER-GATED: '
REASON_UNDECLARED = 'NO DECLARED SCOPE: '
REASON_NO_SLOT = 'no free slot: capacity is '
REASON_OVERLAP = 'write set overlaps '

#: Sections are capped so this stays a table somebody reads at a glance.
#: graph_loop's own printer caps its blocked list at 12; this matches it.
SECTION_CAP = 12

TITLE_WIDTH = 38


def read_claims(path):
    """(claims, problem). The durable record of what actually started.

    {} with no problem when the file is simply absent: a run that has claimed
    nothing yet is a real state, not a failure. None with a problem when the
    file exists and cannot be read or is not the mapping claim_store writes,
    because a store that cannot be read might be holding every unit and
    treating that as "no claims" is the guess this refuses to make."""
    if not os.path.exists(path):
        return {}, ''
    try:
        with open(path, 'r', encoding='utf-8') as fh:
            data = json.load(fh)
    except (OSError, ValueError) as exc:
        return None, 'the claim store could not be read: %s' % exc
    if not isinstance(data, dict):
        return None, 'the claim store is not a mapping of unit id to claim'
    return data, ''


def elapsed_text(seconds):
    """MM:SS under an hour, H:MM:SS at or above it."""
    total = int(seconds)
    if total < 0:
        return NODATA + ': the recorded start is in the future'
    hours, rest = divmod(total, 3600)
    minutes, secs = divmod(rest, 60)
    if hours:
        return '%d:%02d:%02d' % (hours, minutes, secs)
    return '%02d:%02d' % (minutes, secs)


def running_row(node, claims, claims_problem, now):
    """(state, detail) for one unit the scheduler reports as in flight.

    The liveness verdict is claim_store's, never this module's arithmetic
    over expires_at: claim_store.dead_reason() is the estate's single rule
    and it is the one place that knows a dead pid counts as well as an
    expired lease."""
    uid = node['id']
    if claims is None:
        return 'RUNNING', '%s: %s, so no start time can be trusted' % (
            NODATA, claims_problem)
    claim = claims.get(uid)
    if not claim:
        return 'RUNNING', '%s: no claim records a start' % NODATA
    if claim.get('state') != 'claimed':
        return 'RUNNING', '%s: its claim is in state %r, not claimed' % (
            NODATA, claim.get('state'))
    dead = claim_store.dead_reason(claim, now)
    if dead:
        return 'ABANDONED', dead
    try:
        started = float(claim['claimed_at'])
    except (KeyError, TypeError, ValueError):
        return 'RUNNING', '%s: its claim records no readable claimed_at' % NODATA
    return 'RUNNING', elapsed_text(now - started)


def classify(reason):
    """Which of graph_loop's five deferrals this is, or 'UNRECOGNISED'."""
    for prefix, kind in ((REASON_EVENT, 'EVENT'),
                         (REASON_FOUNDER, 'FOUNDER'),
                         (REASON_UNDECLARED, 'UNDECLARED'),
                         (REASON_NO_SLOT, 'NO-SLOT'),
                         (REASON_OVERLAP, 'OVERLAP')):
        if reason.startswith(prefix):
            return kind
    return 'UNRECOGNISED'


def conflict_summary(deferred, ready_count):
    """The three zeros, told apart.

    A unit only reaches graph_loop's conflict check after it has passed the
    event, founder, declared-scope and slot gates, so those four deferrals
    are units that were NEVER TESTED for a conflict. Counting a zero over an
    untested population is the whole failure this distinguishes."""
    kinds = [classify(why) for _n, why in deferred]
    overlaps = kinds.count('OVERLAP')
    untestable = kinds.count('UNDECLARED')
    unrecognised = kinds.count('UNRECOGNISED')
    # Every unit that reached the check: the ones admitted to the batch plus
    # the ones the check itself turned away.
    tested = ready_count + overlaps
    if unrecognised:
        return {'count': None, 'kind': NODATA,
                'note': ('%d deferral(s) carry a reason this does not '
                         'recognise, so whether they were conflict-tested '
                         'cannot be told' % unrecognised)}
    if untestable:
        return {'count': None, 'kind': NODATA,
                'note': ('%d unit(s) declare no write set, so overlap cannot '
                         'be computed for them and a zero here would be a '
                         'guess' % untestable)}
    if overlaps:
        return {'count': overlaps, 'kind': 'FOUND',
                'note': '%d unit(s) deferred because their paths overlap '
                        'live work' % overlaps}
    if not tested:
        return {'count': 0, 'kind': 'NOTHING-TESTED',
                'note': 'no unit reached the conflict check, so this zero '
                        'means NOT MEASURED, not clear'}
    return {'count': 0, 'kind': 'NONE',
            'note': '%d unit(s) were checked and none overlap' % tested}


def board(doc, claims=None, claims_problem='', now=None, slots=None):
    """The whole projection as a plain dict, so it can be rendered, embedded
    in another page, or emitted as JSON without running any of this twice."""
    now = time.time() if now is None else now
    plan = graph_loop.plan(doc, slots)
    rows = []
    for node in plan['in_flight']:
        state, detail = running_row(node, claims, claims_problem, now)
        rows.append({'state': state, 'id': node['id'],
                     'title': node['title'], 'detail': detail})
    for node in plan['batch']:
        rows.append({'state': 'READY', 'id': node['id'],
                     'title': node['title'], 'detail': 'dispatchable now'})
    for node, why in plan['deferred']:
        rows.append({'state': 'DEFERRED', 'id': node['id'],
                     'title': node['title'], 'detail': why,
                     'reason_kind': classify(why)})
    for node, unmet in plan['blocked']:
        # The waits-for cell is the real unmet dependency set the graph
        # computed, never row order and never a guess.
        rows.append({'state': 'BLOCKED', 'id': node['id'],
                     'title': node['title'],
                     'detail': 'waits for %s' % ', '.join(unmet)})
    counts = {}
    for state in ('RUNNING', 'ABANDONED', 'READY', 'DEFERRED', 'BLOCKED'):
        counts[state] = sum(1 for r in rows if r['state'] == state)
    return {'rows': rows,
            'capacity': plan['capacity'],
            'capacity_notes': list(plan['notes']),
            'refused': plan['capacity'] == 0,
            'conflicts': conflict_summary(plan['deferred'], len(plan['batch'])),
            'claims_problem': claims_problem,
            'unknown_deps': [list(pair) for pair in plan['unknown_deps']],
            'counts': counts}


def render(data):
    """The board as one string. Pure, so a page can embed it later."""
    counts = data['counts']
    out = ['SWARM  %d running, %d ready, %d deferred, %d blocked, %d abandoned'
           % (counts['RUNNING'], counts['READY'], counts['DEFERRED'],
              counts['BLOCKED'], counts['ABANDONED'])]
    for note in data['capacity_notes']:
        out.append('  capacity: %s' % note)
    if data['refused']:
        out.append('  CAPACITY 0: nothing is dispatchable. This board is '
                   'REFUSED, not idle.')
    if data['claims_problem']:
        out.append('  claims: %s: %s' % (NODATA, data['claims_problem']))
    for nid, dep in data['unknown_deps']:
        out.append('  BROKEN GRAPH: %s depends on %r which names no node'
                   % (nid, dep))
    out.append('')
    for state in ('RUNNING', 'ABANDONED', 'READY', 'DEFERRED', 'BLOCKED'):
        rows = [r for r in data['rows'] if r['state'] == state]
        for row in rows[:SECTION_CAP]:
            out.append('%-9s %-6s %-*s  %s'
                       % (state, row['id'], TITLE_WIDTH,
                          row['title'][:TITLE_WIDTH], row['detail']))
        if len(rows) > SECTION_CAP:
            out.append('%-9s ... and %d more'
                       % (state, len(rows) - SECTION_CAP))
    out.append('')
    conf = data['conflicts']
    shown = NODATA if conf['count'] is None else str(conf['count'])
    out.append('CONFLICTS %s  [%s] %s' % (shown, conf['kind'], conf['note']))
    return '\n'.join(out)


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split('\n')[0])
    ap.add_argument('--roadmap', help='project THIS graph instead of the '
                                      'default readiness roadmap')
    ap.add_argument('--claims', default=CLAIMS,
                    help='the claim store holding durable start times')
    ap.add_argument('--slots', type=int,
                    help='override the derived capacity, for tests')
    ap.add_argument('--json', action='store_true')
    args = ap.parse_args(argv)
    try:
        doc = graph_loop.load(args.roadmap)
    except (OSError, ValueError) as exc:
        print('swarm-status: %s, cannot read the graph: %s' % (NODATA, exc),
              file=sys.stderr)
        return 2
    claims, problem = read_claims(args.claims)
    data = board(doc, claims, problem, slots=args.slots)
    if args.json:
        print(json.dumps(data, indent=1, sort_keys=True))
    else:
        print(render(data))
    return 0


if __name__ == '__main__':
    sys.exit(main())

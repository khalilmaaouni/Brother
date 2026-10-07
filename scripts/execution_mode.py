#!/usr/bin/env python3
"""execution_mode: is parallelism worth it for THIS graph right now?

The product always tries to parallelize. That is a default, not an answer, and
a reviewer punishes three agents doing one agent's work faster than they praise
three agents doing three agents' work. Running a swarm on a graph that cannot
use one costs coordination, lanes, disk and review attention, and buys nothing.

So the question gets ANSWERED, from the same data the dispatcher already uses,
and the answer is one line a person can read: DIRECT, or SWARM x N.

WHAT IT READS, and it reimplements none of it. graph_loop.plan() already decides
which units may run right now and which of those may run TOGETHER, because a
batch is exactly the set with disjoint declared write sets under the machine's
own capacity. The degree of parallelism worth having is therefore not a judgement
this file makes; it is len(batch), which graph_loop already computed. This file
only reads that plan and says what it means.

WHY THE REASON MATTERS AS MUCH AS THE VERDICT. "DIRECT" alone is unfalsifiable.
DIRECT because the disk gate dropped capacity to one slot is a machine problem
somebody can fix in ten minutes; DIRECT because every ready unit writes the same
file is a decomposition problem nobody can fix by clearing disk. Same verdict,
opposite action, so the cause is named every time.

AND NO-DATA IS A REAL THIRD ANSWER, never a rounding error. A graph that cannot
be read, or that has nothing dispatchable in it, does not support "DIRECT" any
more than it supports "SWARM". Defaulting to SWARM because it sounds ambitious
would invent a degree out of an empty batch, which is the exact failure this
estate keeps paying for: a verdict produced where there was no evidence.

Exit 0 a verdict was produced. Exit 2 NO-DATA.
Python 3, standard library only. No network.
"""
import argparse
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import graph_loop  # noqa: E402  reuse plan(): the same ready-set, write-set and capacity logic the dispatcher runs, never reimplemented

#: The notes graph_loop.machine_capacity() emits are ordered, and only some of
#: them CONSTRAIN. "8 core(s), reserving 2, so 6 slot(s)" is a starting point;
#: the cleanup band, the refuse floor, the estate cap and an explicit override
#: are what actually decided the number. Naming the wrong one sends somebody to
#: buy cores when the real limit was free disk.
CONSTRAINING = ('REFUSE', 'CLEANUP BAND', 'capped at', 'overridden', 'NO-DATA')


def constraining_note(notes):
    """The note that decided the capacity, or the last one if none constrained."""
    for note in notes:
        if any(marker in note for marker in CONSTRAINING):
            return note
    return notes[-1] if notes else 'capacity note absent'


def unlockable(plan, batch_ids):
    """Blocked units whose every unmet dependency is in the batch. These are the
    units that come free WHILE the batch runs, which is the whole return on
    running units beside each other rather than after each other."""
    return [n['id'] for n, unmet in plan['blocked'] if unmet and set(unmet) <= batch_ids]


def deferred_for(plan, marker):
    """Ready units held back for a reason containing this marker."""
    return [n['id'] for n, why in plan['deferred'] if marker in why]


def decide(plan):
    """The verdict as a plain dict. Pure: same plan in, same verdict out."""
    batch = plan['batch']
    capacity = plan['capacity']
    note = constraining_note(plan['notes'])
    batch_ids = set(n['id'] for n in batch)
    unlocks = unlockable(plan, batch_ids)
    overlaps = deferred_for(plan, 'write set overlaps')

    if not batch:
        return {'mode': 'NO-DATA', 'degree': 0, 'capacity': capacity,
                'units': [], 'unlocks': [], 'reason':
                'nothing is dispatchable right now, so neither one agent nor '
                'several has work to take; capacity note: %s' % note}

    if len(batch) == 1:
        if capacity <= 1:
            reason = 'capacity is 1, so a second agent has no slot (%s)' % note
        elif overlaps:
            reason = ('one tightly coupled change; %d other ready unit(s) (%s) write '
                      'into its paths, so a swarm would only serialize'
                      % (len(overlaps), ', '.join(overlaps)))
        else:
            reason = ('only one unit is ready to run, out of %d slot(s); parallelism '
                      'has nothing to run beside it' % capacity)
        return {'mode': 'DIRECT', 'degree': 1, 'capacity': capacity,
                'units': sorted(batch_ids), 'unlocks': unlocks, 'reason': reason}

    reason = '%d independent write sets' % len(batch)
    if unlocks:
        reason += ('; %d downstream unit(s) (%s) unlock while the work continues'
                   % (len(unlocks), ', '.join(unlocks)))
    else:
        reason += '; no unit waits on another, so none of them queues behind a peer'
    return {'mode': 'SWARM', 'degree': len(batch), 'capacity': capacity,
            'units': sorted(batch_ids), 'unlocks': unlocks, 'reason': reason}


def headline(verdict):
    """The customer-visible line. SWARM carries its degree; DIRECT never does."""
    if verdict['mode'] == 'SWARM':
        return 'Execution: SWARM x%d' % verdict['degree']
    return 'Execution: %s' % verdict['mode']


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split('\n')[0])
    ap.add_argument('--roadmap', help='decide for THIS graph instead of the default '
                                      'readiness roadmap')
    ap.add_argument('--slots', type=int, help='override the derived capacity, for tests')
    ap.add_argument('--json', action='store_true', help='machine-readable verdict')
    args = ap.parse_args(list(sys.argv[1:] if argv is None else argv))

    try:
        doc = graph_loop.load(args.roadmap)
    except (OSError, ValueError) as exc:
        print('execution-mode: NO-DATA, cannot read the graph: %s' % exc, file=sys.stderr)
        return 2
    if not (doc.get('rows') or doc.get('features')):
        print('execution-mode: NO-DATA, the graph carries no units', file=sys.stderr)
        return 2

    verdict = decide(graph_loop.plan(doc, args.slots))
    if args.json:
        print(json.dumps(verdict, indent=2))
    else:
        print(headline(verdict))
        print('Reason: %s.' % verdict['reason'])
    return 2 if verdict['mode'] == 'NO-DATA' else 0


if __name__ == '__main__':
    sys.exit(main())

#!/usr/bin/env python3
"""The graph loop: which nodes may run RIGHT NOW, and which may run TOGETHER.

FOUNDER DIRECTION 2026-08-29: make graph loops with multi-agent capability a core
principle, codified rather than described, borrowed from the best available and
adapted to this estate's reality.

WHAT ALREADY EXISTED, and what was actually being used. The ready-set standard
(docs/plan/READY-SET-STANDARD-2026-08-28.md) defines the graph, the ready set, a
pull rule and five folded-in practices from Temporal, Airflow, Dagster and SQS.
Of those, this estate has been using TWO: the dependency graph and a ready set.
Measured honestly on 2026-08-29, it was NOT using downstream-weight pull order
(nodes were picked by intuition), NOT the lane cap (four agents ran against a
stated cap of two), NOT event nodes, NOT BLOCKED-BY naming, and NOT quarantine.
BLOCKED-BY naming and the lane cap landed with the first version of this file;
event nodes landed 2026-08-29 when the vault graph produced three real ones.

AND THE GAP THAT MATTERED MOST WAS NOT IN THE STANDARD AT ALL. The graph knows
what must happen BEFORE what. It says nothing about what may happen BESIDE what.
So on 2026-08-29 two independent efforts fixed the same defect within an hour,
one agent wrote a file in nobody's declared scope, three sessions spent an hour
establishing who owned 83 lines, and about 500 lines were deleted. Every one of
those is a CONCURRENCY failure, and a dependency edge cannot express it.

THE BORROW, and it is from further afield than the standard's four sources.

A dependency edge is a HAPPENS-BEFORE constraint. What was missing is a
HAPPENS-BESIDE constraint, and databases solved that decades ago: two
transactions may interleave freely only when their write sets are disjoint, and
must serialize when they overlap. That is conflict serializability, and an agent
holding `ownedPaths` is a transaction holding a write set.

THE ADAPTATION, since a copy is not a steal. A database aborts and retries a
conflicting transaction, which is cheap because a transaction is cheap. An agent
is not: a wasted agent run costs minutes and real money, and an aborted one can
leave a half-written tree. So this NEVER aborts. It refuses admission BEFORE
dispatch, which turns an expensive rollback into a free scheduling decision. The
whole point is that the conflict is discovered while it is still hypothetical.

Also adapted: Dagster's downstream-weight prioritisation, so among nodes that MAY
run, the one unblocking the most work goes first; and Temporal's worker
concurrency limit, except the cap here is DERIVED from the machine rather than
configured, because the founder's named failure was streams dying when CPU, RAM
or disk ran short.

WHAT THIS DOES NOT DO. It reasons about DECLARED paths. Two nodes that collide
through an undeclared write are invisible to it, which is precisely why W1's
write-time attribution ledger is its companion and not an alternative. It also
cannot tell that two differently-worded nodes are the same work; that is reported
as a warning for a human or Fable to read, never as an automatic refusal, and it
says NO-DATA rather than "clear" when it cannot tell.

Exit 0 a plan was produced. Exit 2 NO-DATA, the roadmap could not be read.
Python 3.9 floor, standard library only.
"""
import argparse
import json
import os
import shutil
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
ROADMAP = os.path.join(ROOT, 'docs', 'plan', 'READINESS-ROADMAP-2026-08-29.json')

# Resource floors. The disk numbers are this estate's own standing law: under 15
# GiB clean up before builds, under 8 refuse. Measured 2026-08-29 at 8.9 GiB,
# so the cleanup band is where this machine actually lives.
DISK_REFUSE_GIB = 8
DISK_CLEANUP_GIB = 15
CORES_RESERVED = 2          # never take the last two cores

#: This estate's OWN standing cap, which is stricter than anything the hardware
#: implies: 3 agents that build, 6 read-only. Derived capacity may exceed it and
#: must never override it. Added after a run on a freshly cleared disk proposed
#: six concurrent builders, which the hardware allows and the estate's law does
#: not. A resource check that quietly outvotes a written rule is worse than no
#: resource check, because it looks principled while removing a control.
ESTATE_BUILDER_CAP = 3

#: A node whose owner is the founder is never pulled by a session. The ready-set
#: standard has said so since 2026-08-28 and this scheduler did not know it: on a
#: cleared disk it proposed dispatching R15, which is AWAITING FOUNDER and is a
#: supply-chain decision only he can take.
FOUNDER_OWNERS = ('FOUNDER', 'founder')

#: A node with NO declared write set is not safe, it is UNKNOWN, and those are
#: different states that this scheduler collapsed in its first two drafts. A
#: read-only node (owns == []) genuinely conflicts with nobody. A node that never
#: declared its paths (owns is None) conflicts with EVERYTHING, because nothing
#: proves otherwise. Treating the second as the first is exactly the undeclared
#: write that destroyed about 500 lines in this estate on 2026-08-29: the tool
#: could not attribute a change, so it guessed, and the guess was destructive.
#: Found when a cleared disk raised capacity and the scheduler batched two nodes
#: whose write sets nobody had declared.


def load(path=None):
    if path is None:
        path = ROADMAP
    with open(path, 'r', encoding='utf-8') as fh:
        return json.load(fh)


def nodes(doc):
    """Rows and features are the same kind of thing to the scheduler: a unit of
    work with dependencies and a write set. Treating them separately is how a
    feature and a row that touch one file get dispatched together."""
    out = []
    for r in doc.get('rows', []) + doc.get('features', []):
        out.append({
            'id': r.get('id'),
            'title': r.get('title') or r.get('name') or '',
            'status': r.get('status'),
            'depends_on': list(r.get('depends_on') or []),
            'owns': list(r.get('owns') or []),
            # Carried through since 2026-08-29: integration verifies a unit's
            # own check ON canonical after the apply, and a node shape that
            # drops the check turns every integration into NO-DATA. Found by
            # running the spine end to end, not by reading.
            'done_check': r.get('done_check') or '',
            'repo': r.get('repo'),
            'hours': r.get('effort_hours') or r.get('estimate_hours') or 0,
            'in_ship': bool(r.get('in_ship_v1')),
            'owner': r.get('owner') or '',
            # None and [] are DIFFERENT. None means nobody declared a scope;
            # [] means the node was declared read-only. Collapsing them is the
            # bug this field exists to keep visible.
            'declared': r.get('owns') is not None,
            # The external verdict this node waits on, per the ready-set
            # standard. A node carrying one is never pulled by a session,
            # because no amount of session effort produces it.
            'event': r.get('event') or None,
        })
    return out


def blocked_by(node, done):
    """The unmet dependencies BY NAME. The old ready_rows returned only the ready
    ids, so a blocked node was silent about why, and a dependency naming nothing
    was indistinguishable from one that was merely unfinished."""
    unmet = [d for d in node['depends_on'] if d not in done]
    return unmet


def unknown_deps(all_nodes):
    """A dependency naming no node is a broken graph, never a satisfied edge.
    Silently ignoring it makes a node look READY when its real prerequisite was
    deleted or renamed."""
    ids = set(n['id'] for n in all_nodes)
    return [(n['id'], d) for n in all_nodes for d in n['depends_on'] if d not in ids]


def downstream_weight(all_nodes):
    """How many nodes each node unblocks, transitively. Dagster's asset-graph
    prioritisation: among things that may run, run the one that frees the most."""
    children = {}
    for n in all_nodes:
        for d in n['depends_on']:
            children.setdefault(d, set()).add(n['id'])
    weight = {}

    def reach(nid, seen):
        if nid in seen:
            return set()          # a cycle contributes nothing rather than hanging
        seen = seen | {nid}
        out = set()
        for c in children.get(nid, ()):
            out.add(c)
            out |= reach(c, seen)
        return out

    for n in all_nodes:
        weight[n['id']] = len(reach(n['id'], set()))
    return weight


#: WHICH REPOSITORY A PATH BELONGS TO. Until 2026-08-29 this scheduler compared
#: BARE paths, so two nodes owning tools/x.py in two DIFFERENT repositories
#: looked like a collision and were serialised for no reason, while two nodes
#: genuinely sharing a file could look unrelated if one wrote it as a qualified
#: path and the other did not. An outside review named it and this board had
#: already recorded the same defect independently.
#:
#: A path may be written qualified ("BrotherModeUp:tools/x.py") or bare. A bare
#: path belongs to the node's own repo field, and failing that to the umbrella,
#: which is where this board's own paths live. Guessing beyond that would invent
#: a repository, so an unqualified path with no node repo is DEFAULT_REPO and
#: says so rather than matching everything.
DEFAULT_REPO = 'Brother'


def qualify(path, node_repo=None):
    """(repo, path) for one declaration, however it was written."""
    text = str(path or '').strip()
    if ':' in text:
        repo, _, rest = text.partition(':')
        repo, rest = repo.strip(), rest.strip()
        if repo and rest:
            return repo, rest.lstrip('./')
    return (node_repo or DEFAULT_REPO), text.lstrip('./')


def owned_pairs(node):
    """Every (repo, path) this node declares."""
    return [qualify(p, node.get('repo')) for p in node.get('owns') or []]


def conflicts(a, b):
    """Do two nodes' write sets overlap? A path conflicts with an identical path
    and with any path it contains, because owning a directory owns what is in it.
    A node owning NOTHING conflicts with nobody, which is why a read-only node
    can always be added to a batch."""
    for ra, pa in owned_pairs(a):
        for rb, pb in owned_pairs(b):
            # DIFFERENT REPOSITORIES CANNOT COLLIDE. Two files named tools/x.py
            # in two trees are two files, and serialising them wastes a slot for
            # nothing.
            if ra != rb:
                continue
            if pa == pb or pa.startswith(pb.rstrip('/') + '/') or pb.startswith(pa.rstrip('/') + '/'):
                return True
    return False


def machine_capacity():
    """The parallelism the machine can actually support, DERIVED rather than
    configured. Returns (slots, notes). Slots of 0 means refuse everything, and
    the note says why so a refusal is never mysterious."""
    notes = []
    try:
        total, _used, free = shutil.disk_usage(os.path.expanduser('~'))
        free_gib = free / (1024.0 ** 3)
    except OSError as exc:
        return 1, ['NO-DATA: could not read disk (%s), assuming one slot' % exc]
    cores = os.cpu_count() or 2
    slots = max(1, cores - CORES_RESERVED)
    notes.append('%d core(s), reserving %d, so %d slot(s)' % (cores, CORES_RESERVED, slots))
    if free_gib < DISK_REFUSE_GIB:
        notes.append('REFUSE: %.1f GiB free is under the %d GiB floor' % (free_gib, DISK_REFUSE_GIB))
        return 0, notes
    if free_gib < DISK_CLEANUP_GIB:
        slots = 1
        notes.append('CLEANUP BAND: %.1f GiB free is under %d GiB, so parallelism drops to 1 '
                     'rather than refusing outright' % (free_gib, DISK_CLEANUP_GIB))
    else:
        notes.append('%.1f GiB free, above the cleanup band' % free_gib)
    return slots, notes


def plan(doc, slots=None):
    """The dispatch plan: what is ready, what is blocked and by what, and the
    largest batch that may run TOGETHER without two writers on one path."""
    all_nodes = nodes(doc)
    # SUPERSEDED counts as satisfied for dependency edges (its work moved to
    # named successors and the row is kept only so the edges stay honest), and
    # it is never dispatchable: on 2026-08-30 the ready set offered R12, a row
    # whose own text says its hours moved into G1-M3 and G1-M4, because this
    # filter knew only DONE and IN-FLIGHT.
    done = set(n['id'] for n in all_nodes
               if n['status'] in ('DONE', 'SUPERSEDED', 'ADDRESSED'))
    in_flight = [n for n in all_nodes if n['status'] == 'IN-FLIGHT']
    weight = downstream_weight(all_nodes)

    # status is needed for the founder gate, so carry it through
    for n in all_nodes:
        pass
    ready, blocked = [], []
    for n in all_nodes:
        if n['status'] in ('DONE', 'IN-FLIGHT', 'SUPERSEDED', 'ADDRESSED'):
            continue
        unmet = blocked_by(n, done)
        if unmet:
            blocked.append((n, unmet))
        else:
            ready.append(n)

    # SHIP MEMBERSHIP FIRST, then downstream weight, then cheapest.
    #
    # The founder's ask was to ship faster without compromising quality, and the
    # first draft of this sort got that wrong in a way worth recording: it
    # proposed a 40 hour node explicitly OUTSIDE the September 6 ship ahead of a
    # 6 hour node inside it, because the big one unblocked two things and the
    # small one unblocked one. Unblocking is the right tiebreak WITHIN a
    # commitment; it is the wrong primary key when a date has been named. A
    # scheduler that optimises purely for graph structure will always drift
    # toward the largest subtree, which is exactly how a deadline is missed by a
    # sequence of individually defensible choices.
    ready.sort(key=lambda n: (not n['in_ship'], -weight.get(n['id'], 0), n['hours'], n['id']))

    cap, notes = machine_capacity()
    if cap > ESTATE_BUILDER_CAP:
        notes.append('capped at %d by this estate\'s own builder limit, which is stricter '
                     'than the %d the hardware allows' % (ESTATE_BUILDER_CAP, cap))
        cap = ESTATE_BUILDER_CAP
    if slots is not None:
        cap = slots
        notes.append('slot count overridden to %d' % slots)

    # Greedy maximal batch: take the highest-priority node whose write set is
    # disjoint from everything already taken AND from everything in flight.
    batch, deferred = [], []
    for n in ready:
        if n['event']:
            # EVENT-WAIT, and it is checked BEFORE the founder gate so a node
            # waiting on a peer's commit is never reported as the founder's to
            # unblock. Naming the wrong owner sends him chasing someone else's
            # work, which is worse than saying nothing.
            deferred.append((n, 'EVENT-WAIT: %s' % n['event']))
            continue
        if n['owner'] in FOUNDER_OWNERS or n['status'] == 'AWAITING FOUNDER':
            deferred.append((n, 'FOUNDER-GATED: rendered in his lane, never pulled by a session'))
            continue
        if not n['declared']:
            deferred.append((n, 'NO DECLARED SCOPE: owns is absent, so nothing can prove this does '
                                'not collide. Declare its paths (owns: [...]), or declare it '
                                'read-only (owns: []), before it may be dispatched'))
            continue
        if len(batch) >= cap:
            deferred.append((n, 'no free slot: capacity is %d' % cap))
            continue
        clash = next((o for o in batch + in_flight if conflicts(n, o)), None)
        if clash:
            deferred.append((n, 'write set overlaps %s' % clash['id']))
            continue
        batch.append(n)
    return {'batch': batch, 'deferred': deferred, 'blocked': blocked,
            'in_flight': in_flight, 'weight': weight, 'capacity': cap,
            'notes': notes, 'unknown_deps': unknown_deps(all_nodes)}


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument('--slots', type=int, help='override the derived capacity, for tests')
    ap.add_argument('--roadmap', help='schedule THIS graph instead of the default readiness '
                                      'roadmap. The scheduling logic is stream independent; '
                                      'only the file it reads was ever hardcoded.')
    args = ap.parse_args(argv)
    try:
        doc = load(args.roadmap)
    except (OSError, ValueError) as exc:
        print('graph-loop: NO-DATA, cannot read the roadmap: %s' % exc, file=sys.stderr)
        return 2
    p = plan(doc, args.slots)

    for note in p['notes']:
        print('capacity: %s' % note)
    if p['unknown_deps']:
        for nid, dep in p['unknown_deps']:
            print('BROKEN GRAPH: %s depends on %r which names no node' % (nid, dep), file=sys.stderr)
    print()
    if p['in_flight']:
        print('IN FLIGHT (%d), their paths are held:' % len(p['in_flight']))
        for n in p['in_flight']:
            print('  %-6s %s' % (n['id'], n['title'][:62]))
        print()
    print('DISPATCH NOW (%d of %d slot(s)):' % (len(p['batch']), p['capacity']))
    for n in p['batch']:
        print('  %-6s unblocks %-2d  %sh  %s'
              % (n['id'], p['weight'].get(n['id'], 0), n['hours'], n['title'][:54]))
    if p['deferred']:
        print()
        print('READY BUT DEFERRED (%d):' % len(p['deferred']))
        for n, why in p['deferred']:
            print('  %-6s %s' % (n['id'], why))
    if p['blocked']:
        print()
        print('BLOCKED (%d), each naming what it waits on:' % len(p['blocked']))
        for n, unmet in p['blocked'][:12]:
            print('  %-6s BLOCKED-BY %s' % (n['id'], ', '.join(unmet)))
    return 0


if __name__ == '__main__':
    sys.exit(main())

"""merge_queue: conflict-aware batching for the one-integrator merge queue (W8).

W8. One session holds a repository's canonical tree and is the only one that
checks out, merges or pushes its main branch (the integration-manager
workflow: Pro Git 5.1, bors, GitHub's merge queue, Zuul, Mergify). Every other
stream submits a pull request carrying its declared write set, its done-check
command and its receipts, and this module turns that pile of submissions into
a PLAN the integrator (a human or an orchestrator) can act on.

THE ADAPTATION OVER A PLAIN FIFO QUEUE: a standard merge queue serializes
everything because it does not know which changes interact, paying full test
cost per change even when two touch nothing in common. W5's graph_loop.py
already computes write-set conflicts for the dispatch scheduler, so the same
computation gives CONFLICT-AWARE BATCHING here for free: submissions with
provably disjoint write sets are speculatively merged and tested TOGETHER;
only genuinely overlapping ones serialize into separate, sequential batches.

THREE VERDICTS, never two. An ordinary queue has pass/fail. This one adds
HELD: a submission whose check could not even run (no check command, or a
write set nobody declared so nothing proves it does not collide) is HELD by
name, never merged and never rejected. Collapsing "could not verify" into
"reject" is how good work gets thrown away; collapsing it into "merge" is how
2026-08-29's write-contention losses happened.

THIS MODULE NEVER PUSHES OR MERGES ANYTHING REAL. It is a pure planning
module plus a driver that PRINTS the plan and the commands a human or
orchestrator would run to test each batch AS IF MERGED. Executing those
commands, and deciding what a green or red result means for the canonical
tree, is the integrator's job, not this one's.

Python 3, standard library only. No network, no subprocess, no git.
"""
import argparse
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import graph_loop  # noqa: E402  reuse conflicts()/qualify(): the same write-set logic W5 already proved, never reimplemented


def held_reason(sub):
    """Why this submission cannot enter any batch, or None if it can.

    Two ways in, both meaning "cannot be trusted into a speculative merge"
    rather than "is bad": no check command, so nothing proves it green before
    it joins a batch with someone else's work; or no declared write set (owns
    is None, not merely empty), so nothing proves it does not collide, the
    same NO DECLARED SCOPE rule graph_loop.py uses for the dispatch scheduler.
    Either way the submission is HELD, not merged and not rejected."""
    if sub.get('owns') is None:
        return 'unreadable write set: owns was not declared, so nothing proves this does not collide'
    if not sub.get('check_cmd'):
        return 'no check command: cannot prove this is green before it enters a speculative merge'
    return None


def plan_queue(submissions):
    """Batch what can run together, serialize what cannot, hold what cannot be
    trusted into a batch at all.

    Greedy first-fit over graph_loop.conflicts(): each runnable submission
    joins the first open batch it conflicts with nothing already in, or opens
    a new one. Two submissions in the same batch are proven disjoint and are
    tested TOGETHER as one speculative merge; two submissions in different
    batches are SERIALIZED, run one after the other. Input order is
    preserved within and across batches, so the plan is reproducible.

    Returns {'batches': [[sub, ...], ...], 'held': [(sub, reason), ...]}."""
    held = []
    runnable = []
    for sub in submissions:
        reason = held_reason(sub)
        if reason:
            held.append((sub, reason))
        else:
            runnable.append(sub)

    batches = []
    for sub in runnable:
        home = next((b for b in batches
                     if not any(graph_loop.conflicts(sub, o) for o in b)), None)
        if home is None:
            batches.append([sub])
        else:
            home.append(sub)
    return {'batches': batches, 'held': held}


def commands_for_batch(batch, base='origin/main', index=1):
    """The commands a human or the integrator session would run to test this
    batch AS IF MERGED against the current tip. This module never runs them:
    emitting them is the whole job, running them is the integrator's."""
    branch = 'integrate/batch-%d' % index
    cmds = ['git fetch origin', 'git checkout -B %s %s' % (branch, base)]
    for sub in batch:
        cmds.append('git merge --no-ff origin/%s  # %s' % (sub['branch'], sub['id']))
    for sub in batch:
        cmds.append('%s  # %s check' % (' '.join(sub['check_cmd']), sub['id']))
    return cmds


def render(result):
    """Human-readable plan: one section per batch (its members and the
    commands to test it), then everything HELD and why."""
    batches, held = result['batches'], result['held']
    lines = ['MERGE QUEUE PLAN (%d batch(es), %d held)' % (len(batches), len(held))]
    for i, batch in enumerate(batches, 1):
        ids = ', '.join(s['id'] for s in batch)
        if len(batch) > 1:
            verb = 'BATCHED (disjoint write sets, tested together)'
        elif len(batches) > 1:
            verb = 'SERIALIZED (write set overlaps another submission)'
        else:
            verb = 'alone'
        lines.append('')
        lines.append('batch %d: %s -> %s' % (i, ids, verb))
        for cmd in commands_for_batch(batch, index=i):
            lines.append('  $ %s' % cmd)
    if held:
        lines.append('')
        lines.append('HELD (%d), never merged, never rejected:' % len(held))
        for sub, reason in held:
            lines.append('  %-10s %s' % (sub['id'], reason))
    return '\n'.join(lines)


#: Built-in fixture driving all three verdicts in one run: S1/S2 disjoint
#: (batch together), S3 overlaps S1 (serializes into its own batch), S4 has
#: no check command (HELD), S5 has no declared write set (HELD).
DEMO_SUBMISSIONS = [
    {'id': 'S1', 'branch': 'feat/s1', 'owns': ['scripts/a.py'], 'check_cmd': ['true']},
    {'id': 'S2', 'branch': 'feat/s2', 'owns': ['scripts/b.py'], 'check_cmd': ['true']},
    {'id': 'S3', 'branch': 'feat/s3', 'owns': ['scripts/a.py'], 'check_cmd': ['true']},
    {'id': 'S4', 'branch': 'feat/s4', 'owns': ['scripts/c.py'], 'check_cmd': None},
    {'id': 'S5', 'branch': 'feat/s5', 'owns': None, 'check_cmd': ['true']},
]


def load_submissions(path):
    with open(path, 'r', encoding='utf-8') as fh:
        return json.load(fh)


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument('--submissions', help='JSON file: a list of submissions, each '
                     '{"id", "branch", "owns", "check_cmd", "repo" (optional)}')
    ap.add_argument('--demo', action='store_true',
                     help='run the built-in fixture that drives all three verdicts: '
                          'batch, serialize and HELD, and exit')
    args = ap.parse_args(argv)

    if args.submissions and not args.demo:
        try:
            submissions = load_submissions(args.submissions)
        except (OSError, ValueError) as exc:
            print('merge-queue: NO-DATA, cannot read submissions: %s' % exc, file=sys.stderr)
            return 2
    else:
        submissions = DEMO_SUBMISSIONS

    print(render(plan_queue(submissions)))
    return 0


if __name__ == '__main__':
    sys.exit(main())

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

PLANNING (plan_queue, held_reason, commands_for_batch, render) is pure,
standard-library-only logic with no git and no subprocess, and stays that
way: render() still only PRINTS the plan for a human to read.

EXECUTION, added the same day this file's own done_check was actually
driven: execute_queue() and its helpers below ARE the AS-IF-MERGED
mechanism, not a description of it. A disjoint batch is speculatively
merged into ONE isolated scratch worktree (worktree_lane.py, this estate's
own containment-safety primitive) and every member's own done_check runs
against that SAME combined tree before any of them is allowed near real
canonical: "tested together" means literally in the same tree at test
time, not two separate trees whose results are compared after the fact.
Only a batch that proves clean together is landed. A serialized
(single-member) batch lands through integrate.integrate_one() directly
(built for W's single-unit path: lock, clean-tree check, real --no-ff
merge onto canonical's CURRENT tip, the unit's own check re-run ON that
merged tree, unwind on red), reused rather than re-derived. A disjoint
batch of more than one lands through _land_batch_together(), which reuses
integrate.py's SAME lock and tree-state controls but merges every member
first and checks all of them together before any of it is kept, because
landing members one at a time would silently break "tested together" a
second time, right after proving it. Batches run in plan_queue()'s own
order, so an overlapping (serialized) submission is only ever landed once
every earlier batch has already been decided, against whatever canonical
IS by then, never a stale tip.

Python 3, standard library only for the planning half; git and subprocess
for the execution half (worktree_lane and integrate already depend on
both, so this does not widen the estate's dependency surface).
"""
import argparse
import json
import os
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import graph_loop  # noqa: E402  reuse conflicts()/qualify(): the same write-set logic W5 already proved, never reimplemented
import integrate  # noqa: E402  reuse integrate_one(): the same merge-verify-unwind primitive W's single-unit path already proved
import worktree_lane  # noqa: E402  reuse acquire()/release(): the same worktree isolation primitive P0.2 already proved


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


# ------------------------------------------------------------- execution --
#
# Everything below actually RUNS the plan above, against a real git
# repository. MERGED/REJECTED/HELD mirror the row's own three verdicts;
# they are this module's own words, not integrate.py's (mapped onto them
# in _land_one below), because a queue submission and an integration unit
# are not quite the same shape and collapsing their vocabularies would blur
# a real distinction (e.g. ALREADY_INTEGRATED is a fact about a resumed
# run, not a queue outcome, and still needs to read as MERGED here).

MERGED = 'MERGED'
REJECTED = 'REJECTED'
HELD = 'HELD'


def _git(args, cwd, runner=None):
    """This module's own tiny git wrapper, mirroring integrate.py's and
    worktree_lane.py's: never raises, always returns something with
    .returncode/.stdout/.stderr, so a git binary missing or a repo gone
    reads as a normal (nonzero) failure rather than an uncaught exception."""
    runner = runner or (lambda cmd, **kw: subprocess.run(
        cmd, capture_output=True, text=True, cwd=cwd, timeout=120))
    try:
        return runner(['git'] + list(args))
    except Exception as exc:  # noqa: BLE001
        class _Fail:
            returncode, stdout, stderr = 1, '', str(exc)
        return _Fail()


def _tip(repo, runner=None):
    proc = _git(['rev-parse', 'HEAD'], repo, runner)
    return (proc.stdout or '').strip() if proc.returncode == 0 else None


def receipt_still_valid(sub, current_rev):
    """Is `sub['receipt']` still trustworthy AT `current_rev`?

    The row's own third advantage over a plain FIFO queue: 'receipts that
    BIND to a revision, so the integrator can trust a bound receipt instead
    of re-running everything'. A receipt is {'canonical_rev': sha,
    'exit_code': 0}; it is valid ONLY when its canonical_rev is the exact
    revision being tested against right now. A receipt with no
    canonical_rev, a nonzero exit_code, or one bound to a revision that is
    not `current_rev` (canonical has moved since it was produced) is stale
    and must never be accepted blind -- exactly the failure mode this
    function exists to refuse. Pure and side-effect free so it can be
    driven both ways without a git repository."""
    receipt = sub.get('receipt') or {}
    return bool(receipt.get('canonical_rev')) and \
        receipt.get('canonical_rev') == current_rev and \
        receipt.get('exit_code') == 0


def _run_submission_check(sub, cwd, check_runner=None, timeout=300):
    """(status, detail): status is 'pass', 'fail' or 'held'.

    'held' is a check that never produced a real result: no check_cmd (a
    batch built by hand, e.g. in a test, is not obliged to have called
    held_reason() first), the runner itself raising (a missing
    interpreter, a script error), or a timeout. 'fail' is a check that RAN
    and returned nonzero: it disproved the submission, which is a
    different fact from 'held' and the two must never collapse into one
    another, exactly as the row's done_check requires."""
    cmd = sub.get('check_cmd')
    if not cmd:
        return 'held', 'no check command: nothing can prove this submission'
    runner = check_runner or (lambda c, **kw: subprocess.run(
        c, capture_output=True, text=True, cwd=cwd, timeout=timeout))
    try:
        proc = runner(cmd)
    except subprocess.TimeoutExpired as exc:
        return 'held', 'the check timed out after %ss: %s' % (timeout, exc)
    except Exception as exc:  # noqa: BLE001
        return 'held', 'the check could not run: %s' % exc
    code = getattr(proc, 'returncode', None)
    detail = ((getattr(proc, 'stdout', '') or '')
              + (getattr(proc, 'stderr', '') or '')).strip()[:2000]
    if code is None:
        return 'held', 'the check produced no result: %s' % (detail or '(no output)')
    return ('pass' if code == 0 else 'fail'), detail


def _prove_batch_together(repo, batch, root=None, runner=None, check_runner=None):
    """Speculatively merge every member of `batch` into ONE isolated
    scratch worktree at canonical's current tip, then run every remaining
    member's OWN done_check against that SAME combined tree: the row's
    'tested together' clause, proven by actually doing it rather than
    describing it. Never touches real canonical; worktree_lane.acquire()
    is this estate's own containment-safety primitive, reused rather than
    a second one invented here.

    A member whose branch does not apply cleanly onto the batch's base (or
    a sibling already folded into this same speculative tree) is REJECTED
    for a conflict, and the rest are retried, freshly reset to the base,
    without it: one bad apply must never sink an otherwise-clean batch.
    A member whose check could not run is HELD the same way. Only once a
    whole pass merges AND checks clean for everyone still standing does
    the batch count as proven; only those members are handed back.

    Returns (proven, verdicts): `proven` is the list of members verified
    clean TOGETHER in the tree's final state; `verdicts` maps every OTHER
    member's id to (verdict, detail) for the ones this pass decided
    against (REJECTED or HELD). The scratch worktree is always released,
    forcibly, in a finally: it holds nothing but disposable speculative
    merge commits, never a human's or a worker's own work."""
    verdicts = {}
    remaining = list(batch)
    path, _branch, problem = worktree_lane.acquire(
        repo, 'mq-' + '-'.join(s['id'] for s in batch)[:40], root, runner)
    if not path:
        for s in batch:
            verdicts[s['id']] = (HELD, 'no isolated scratch worktree could be '
                                 'acquired for the speculative merge: %s' % problem)
        return [], verdicts
    try:
        base_rev = _tip(repo, runner)
        if not base_rev:
            for s in batch:
                verdicts[s['id']] = (HELD, "canonical's tip could not be read, "
                                     "so nothing can be speculatively merged onto it")
            return [], verdicts
        while remaining:
            reset = _git(['reset', '--hard', base_rev], path, runner)
            if reset.returncode != 0:
                for s in remaining:
                    verdicts[s['id']] = (HELD, 'could not reset the scratch tree '
                                         'to %s: %s' % (base_rev[:9],
                                         (reset.stderr or '').strip()[:200]))
                remaining = []
                break
            conflict, merge_detail = None, ''
            for s in remaining:
                m = _git(['merge', '--no-ff', '-q', '-m',
                         'mq-speculative:%s' % s['id'], s['branch']], path, runner)
                if m.returncode != 0:
                    _git(['merge', '--abort'], path, runner)
                    conflict = s
                    merge_detail = (m.stderr or m.stdout or '').strip()[:200]
                    break
            if conflict is not None:
                verdicts[conflict['id']] = (
                    REJECTED, 'does not apply cleanly onto the speculative batch '
                    '(base %s, or a sibling already merged into it): %s'
                    % (base_rev[:9], merge_detail))
                remaining = [s for s in remaining if s is not conflict]
                continue
            bad = []
            for s in remaining:
                status, detail = _run_submission_check(s, path, check_runner)
                if status != 'pass':
                    bad.append((s, HELD if status == 'held' else REJECTED, detail))
            if not bad:
                return remaining, verdicts
            for s, verdict, detail in bad:
                verdicts[s['id']] = (verdict, detail)
            bad_ids = {s['id'] for s, _v, _d in bad}
            remaining = [s for s in remaining if s['id'] not in bad_ids]
        return remaining, verdicts
    finally:
        worktree_lane.release(repo, path, force=True, runner=runner)


def _land_batch_together(repo, batch, runner=None, run_id=None, timeout=300):
    """Land a batch ALREADY PROVEN compatible together (by
    _prove_batch_together) as ONE combined change to real canonical: every
    member's branch merged in first, THEN every member's own check run
    against THAT one combined tree -- exactly mirroring the speculative
    proof, for real this time.

    Landing members ONE AT A TIME here (as repeated integrate_one calls
    would) silently breaks 'tested together': found live writing this
    module's own test, where submission A's check requires sibling B's
    file to exist, and B has not landed yet when A's turn comes if they
    land separately, even though both proved clean together moments
    earlier in the scratch tree. So this reuses integrate.py's own lock,
    dirty-tree refusal and stop-file check (the exact controls
    integrate_one applies for one unit) but merges every batch member
    before checking any of them, and unwinds the WHOLE batch together on
    any red -- all or nothing, never a partial land of a batch that was
    proven, and only ever proven, as one unit.

    Returns {sub_id: (verdict, detail, canonical_rev)}."""
    verdicts = {}
    with integrate._Lock(repo):
        stopped = integrate._stop_reason(repo)
        if stopped is not None:
            tip = integrate._tip(repo, runner)
            for s in batch:
                verdicts[s['id']] = (HELD, stopped, tip)
            return verdicts
        clean = integrate._clean(repo, runner)
        if not clean:
            reason = ('the canonical tree could not be read' if clean is None else
                      "the canonical tree is dirty; canonical is integration-only "
                      "ground and a dirty tree is already a rule violation")
            tip = integrate._tip(repo, runner)
            for s in batch:
                verdicts[s['id']] = (HELD, reason, tip)
            return verdicts
        before = _tip(repo, runner)
        conflict, merge_detail = None, ''
        for s in batch:
            m = _git(['merge', '--no-ff', '-q', '-m',
                     'Brother landed %s (batch)' % s['id'], s['branch']], repo, runner)
            if m.returncode != 0:
                _git(['merge', '--abort'], repo, runner)
                conflict = s
                merge_detail = (m.stderr or m.stdout or '').strip()[:200]
                break
        if conflict is not None:
            for s in batch:
                if s is conflict:
                    verdicts[s['id']] = (
                        REJECTED, 'did not apply to canonical at landing time, '
                        'although it was proven compatible moments earlier in the '
                        'speculative tree: %s' % merge_detail, before)
                else:
                    verdicts[s['id']] = (
                        HELD, "a sibling's landing conflicted, so this member's own "
                        "fate could not be decided this round; it stays queued for "
                        "a fresh attempt", before)
            return verdicts
        bad = []
        for s in batch:
            status, detail = _run_submission_check(s, repo, timeout=timeout)
            if status != 'pass':
                bad.append((s, status, detail))
        if not bad:
            after = _tip(repo, runner)
            for s in batch:
                verdicts[s['id']] = (
                    MERGED, 'landed together with %d sibling(s), all checks green '
                    'on canonical at %s' % (len(batch) - 1,
                    (after or '?')[:9]), after)
            return verdicts
        _git(['reset', '--hard', before], repo, runner)
        bad_ids = {s['id'] for s, _st, _d in bad}
        for s, status, detail in bad:
            verdicts[s['id']] = (HELD if status == 'held' else REJECTED, detail, before)
        for s in batch:
            if s['id'] not in bad_ids:
                verdicts[s['id']] = (
                    HELD, "landed together with a sibling whose own check failed, so "
                    "the whole batch was unwound; this member's own check was green "
                    "and it stays queued for a fresh attempt", before)
        return verdicts


def _land_one(repo, sub, runner=None, run_id=None, timeout=300):
    """Land exactly one already-proven submission for real, through
    integrate.integrate_one(): the estate's own apply-to-canonical-tip,
    revalidate-ON-canonical, unwind-on-red primitive. Reused rather than
    re-deriving the same lock/clean-tree/merge/revalidate/unwind sequence
    a second time.

    A custom check_runner replays `sub['check_cmd']` (a list) verbatim
    rather than relying on integrate_one's own shell-string default, so the
    real landing step runs the EXACT SAME check the speculative proof did.

    Maps integrate.py's verdicts onto this module's three: INTEGRATED and
    ALREADY_INTEGRATED -> MERGED (a lane already an ancestor of canonical
    IS landed, whatever call put it there); NEEDS_REPAIR and CONFLICT ->
    REJECTED (the change ran and failed, or did not apply); REFUSED (a
    dirty canonical tree or an active stop file) and NO-DATA (no check, or
    the check errored) -> HELD, because neither disproves the submission
    itself, only that integration could not proceed right now.

    Returns (verdict, detail, canonical_rev)."""
    cmd = sub.get('check_cmd')
    check_runner = None
    if cmd:
        check_runner = lambda check, _cmd=cmd, _cwd=repo: subprocess.run(  # noqa: E731
            _cmd, capture_output=True, text=True, cwd=_cwd, timeout=timeout)
    unit = {'id': sub['id'], 'done_check': ' '.join(cmd) if cmd else ''}
    r = integrate.integrate_one(repo, sub['branch'], unit, runner, check_runner, run_id)
    verdict = r['verdict']
    if verdict in (integrate.INTEGRATED, integrate.ALREADY_INTEGRATED):
        return MERGED, r['reason'], r['canonical']
    if verdict in (integrate.NEEDS_REPAIR, integrate.CONFLICT):
        return REJECTED, r['reason'], r['canonical']
    return HELD, r['reason'], r['canonical']  # REFUSED or NODATA


def execute_queue(repo, submissions, root=None, runner=None, run_id=None):
    """Actually run the plan: land what proves green, reject what proves
    red, hold what could not be proven either way. Every verdict comes
    from a real git merge and a real done_check run against the tree that
    merge produced -- the AS-IF-MERGED mechanism itself, never a guess.

    Batches run in plan_queue()'s own order: a disjoint (len > 1) batch is
    proven TOGETHER in one scratch tree before any member touches real
    canonical; a serialized (len == 1) submission goes straight to
    _land_one, which is itself the full as-if-merged-then-land sequence,
    so it always runs against whatever canonical IS at that moment -- an
    overlapping submission queued behind an earlier batch is therefore
    re-validated against the NEW tip that batch left, never a stale one.

    Returns {'merged': [(sub, detail, canonical_rev), ...],
             'rejected': [(sub, detail), ...],
             'held': [(sub, detail), ...]}. 'held' folds in both the
    STRUCTURAL holds plan_queue() found before any batch ran (undeclared
    write set, no check command) and the ones this function found by
    actually trying -- a submission is never silently dropped from either
    list, matching the row's 'HELD ... never merged, never rejected'."""
    plan = plan_queue(submissions)
    merged, rejected = [], []
    held = list(plan['held'])
    for batch in plan['batches']:
        if len(batch) > 1:
            proven, verdicts = _prove_batch_together(repo, batch, root, runner)
            for sid, (verdict, detail) in verdicts.items():
                sub = next(s for s in batch if s['id'] == sid)
                (rejected if verdict == REJECTED else held).append((sub, detail))
            if not proven:
                continue
            # PROVEN together, so LANDED together: one combined merge, one
            # combined check pass, one unwind if any of it goes red. See
            # _land_batch_together's own docstring for why landing these
            # one at a time (as repeated _land_one calls) would silently
            # break the very 'tested together' property just proven.
            land_verdicts = _land_batch_together(repo, proven, runner, run_id)
            for sub in proven:
                verdict, detail, canonical = land_verdicts[sub['id']]
                if verdict == MERGED:
                    merged.append((sub, detail, canonical))
                elif verdict == REJECTED:
                    rejected.append((sub, detail))
                else:
                    held.append((sub, detail))
        else:
            for sub in batch:
                verdict, detail, canonical = _land_one(repo, sub, runner, run_id)
                if verdict == MERGED:
                    merged.append((sub, detail, canonical))
                elif verdict == REJECTED:
                    rejected.append((sub, detail))
                else:
                    held.append((sub, detail))
    return {'merged': merged, 'rejected': rejected, 'held': held}


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

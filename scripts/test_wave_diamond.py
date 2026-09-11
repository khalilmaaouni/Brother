"""WAVE-0: what the estate can PROVE about its own parallel execution.

The scheduler admits work (graph_loop.py), the claim store makes a claim
exclusive and durable (claim_store.py), worktree_lane.py isolates each writer
in its own tree, loop_bridge.py dispatches a batch concurrently, and
integrate.py lands green units on canonical one at a time. Every piece above
already has its own test file. What none of them proves TOGETHER is the
shape the founder actually cares about: a diamond, A and B independent, C
depending on both, D depending on C, run for real through every one of those
modules at once.

THE FIXTURE: A modifies file_a.py, B modifies file_b.py, C depends on A and B
and modifies integration.py, D depends on C. Built as an in-memory roadmap
doc (graph_loop.plan()'s own shape, mirroring test_graph_loop.py's node()/
doc() helpers) plus a throwaway git repository (mirroring test_loop_bridge.py's
own _repo() helper) that the real worktree_lane, claim_store and integrate
modules write into and merge from. Every round below is driven through
run_round(), which is nothing but graph_loop.plan() -> claim_store.acquire()
-> worktree_lane.Lanes() -> loop_bridge.run() -> scope_audit.audit() ->
integrate.integrate() -> claim_store.release(), in that order: the same
sequence loop_bridge.py's own main() already runs, called here as a library
rather than re-implemented.

STUB WORKERS ONLY. Each one traces its own start/end wall time and its
pid/thread identity to a FILE (Trace, below), so a later round of the same
test can read what an earlier round left. A stub can sleep, fail its own
verify (a red done_check, repaired or not), or die.

WHICH SEAM 'DIE' APPLIES TO, stated once here rather than at each call site:
loop_bridge.run() dispatches worker.run() DIRECTLY inside a
ThreadPoolExecutor thread (read from the source: the pool submits run_node,
which calls worker.run() synchronously in that thread; there is no
subprocess boundary at THIS seam for a stub worker). A real SIGKILL applies
one layer up, at LaneWorker/bm_worker_spawn.SpawningWorker's actual
subprocess boundary, which a stub-worker fixture at this seam does not
reach. So 'die' here is an exception raised in-thread, exactly matching
test_loop_bridge.py's own Explodes() worker (DispatchIsActuallyConcurrent,
test_one_worker_that_RAISES_does_not_take_the_batch_down) -- the closest
existing sibling for this exact seam.
"""
import json
import os
import subprocess
import sys
import tempfile
import threading
import time
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import claim_store  # noqa: E402
import graph_loop  # noqa: E402
import integrate  # noqa: E402
import loop_bridge  # noqa: E402
import scope_audit  # noqa: E402
import worktree_lane  # noqa: E402

# E100: one sandbox for every temp tree this process makes, removed at exit.
import os as _e100_os, sys as _e100_sys  # noqa: E402
_e100_sys.path.append(_e100_os.path.join(
    _e100_os.path.dirname(_e100_os.path.abspath(__file__)), '.'))
try:  # noqa: E402
    import tmp_sandbox as _e100_tmp
    _e100_tmp.install()
except ImportError:
    _e100_sys.stderr.write(
        'tmp_sandbox absent: %s leaves its temp trees behind\n'
        % _e100_os.path.basename(__file__))


# ---------------------------------------------------------------------------
# THE FIXTURE
# ---------------------------------------------------------------------------

def node(nid, deps=None, owns=None, status='SCHEDULED', hours=1,
         done_check='true'):
    # owns defaults to [] (declared read-only), never None, mirroring
    # test_graph_loop.py's own node(): a fixture must never accidentally
    # exercise the undeclared-scope path.
    return {'id': nid, 'title': nid, 'status': status,
            'depends_on': deps or [], 'owns': [] if owns is None else owns,
            'effort_hours': hours, 'in_ship_v1': True, 'done_check': done_check}


def doc(rows):
    return {'rows': rows, 'features': []}


#: unit id -> the one file it declares and writes. The whole diamond.
FILES = {'A': 'file_a.py', 'B': 'file_b.py', 'C': 'integration.py', 'D': 'd.py'}


def diamond_doc():
    return doc([
        node('A', owns=[FILES['A']], done_check='test -f %s' % FILES['A']),
        node('B', owns=[FILES['B']], done_check='test -f %s' % FILES['B']),
        node('C', deps=['A', 'B'], owns=[FILES['C']],
             done_check='test -f %s' % FILES['C']),
        node('D', deps=['C'], owns=[FILES['D']],
             done_check='test -f %s' % FILES['D']),
    ])


def _git(args, cwd):
    return subprocess.run(['git'] + args, cwd=cwd, capture_output=True,
                          text=True, timeout=60)


def repo():
    """A throwaway git repository, mirroring test_loop_bridge.py's own
    _repo() helper (AnUndeclaredWriteIsNotIntegrableHoweverGreenItIs)."""
    d = tempfile.mkdtemp(prefix='wave-diamond-')
    _git(['init', '-q', '-b', 'main'], d)
    _git(['config', 'user.email', 'a@b.c'], d)
    _git(['config', 'user.name', 't'], d)
    with open(os.path.join(d, 'README.md'), 'w', encoding='utf-8') as fh:
        fh.write('base\n')
    _git(['add', '-A'], d)
    _git(['commit', '-q', '-m', 'base'], d)
    return d


def head(cwd):
    proc = _git(['rev-parse', 'HEAD'], cwd)
    return (proc.stdout or '').strip()


class Trace(object):
    """Every worker invocation's start/end wall time and its process/thread
    identity, written to a FILE this test reads back, so a later round of
    the same test sees what an earlier round left."""

    def __init__(self, path):
        self.path = path
        self._lock = threading.Lock()

    def mark(self, unit_id, phase):
        rec = {'unit': unit_id, 'phase': phase, 't': time.time(),
               'pid': os.getpid(), 'thread': threading.get_ident()}
        with self._lock:
            with open(self.path, 'a', encoding='utf-8') as fh:
                fh.write(json.dumps(rec) + '\n')

    def read(self):
        if not os.path.isfile(self.path):
            return []
        with open(self.path, encoding='utf-8') as fh:
            return [json.loads(l) for l in fh if l.strip()]

    def _phase(self, unit_id, phase):
        return [r for r in self.read() if r['unit'] == unit_id and r['phase'] == phase]

    def starts(self, unit_id):
        return self._phase(unit_id, 'start')

    def ends(self, unit_id):
        return self._phase(unit_id, 'end')

    def invocations(self, unit_id):
        return len(self.starts(unit_id))


def trace_path():
    return os.path.join(tempfile.mkdtemp(prefix='wave-trace-'), 'trace.jsonl')


def store_path():
    return os.path.join(tempfile.mkdtemp(prefix='wave-claims-'), 'claims.json')


class StubWorker(object):
    """One worker for one unit. Traces its own start/end, optionally sleeps,
    optionally dies (see the module docstring for which crash this seam can
    express), otherwise writes its declared file and commits it in its own
    lane. `last_unit` is the exact unit dict run_node() handed it, so a test
    can check whether a resumed dispatch was really a BARE one."""

    def __init__(self, unit_id, trace, sleep=0.0, die=False):
        self.unit_id = unit_id
        self.trace = trace
        self.sleep = sleep
        self.die = die
        self.last_unit = None

    def run(self, unit, cwd=None):
        self.last_unit = unit
        self.trace.mark(self.unit_id, 'start')
        if self.sleep:
            time.sleep(self.sleep)
        if self.die:
            self.trace.mark(self.unit_id, 'crashed')
            raise RuntimeError('%s crashed at the worker seam (in-thread)'
                              % self.unit_id)
        fname = FILES[self.unit_id]
        with open(os.path.join(cwd, fname), 'w', encoding='utf-8') as fh:
            fh.write(self.unit_id + '\n')
        _git(['add', '-A'], cwd)
        _git(['commit', '-q', '-m', 'work: %s' % self.unit_id], cwd)
        self.trace.mark(self.unit_id, 'end')
        return {'worker_claim': 'wrote %s' % fname, 'artifacts': [fname],
                'status': 'ok', 'cost': {'tokens': 0, 'minutes': 0}}


class FakeVerify(object):
    """PASS by default; a caller can force specific unit ids red."""

    def __init__(self, fail=None):
        self.fail = set(fail or ())

    def verify(self, unit, cwd=None):
        uid = unit.get('unit_id')
        if uid in self.fail:
            return {'verdict': 'FAIL', 'reason': 'seeded failure for %s' % uid}
        return {'verdict': 'PASS', 'reason': 'ok'}

    def is_pass(self, result):
        return result.get('verdict') == 'PASS'


class FakeRepair(object):
    """Repairs everything except the unit ids named in `irreparable`,
    mirroring bm_repair's real contract of trying and sometimes giving up."""

    def __init__(self, irreparable=None):
        self.irreparable = set(irreparable or ())
        self.called = []

    def repair(self, unit, verdict, worker, cwd=None, max_attempts=3):
        uid = unit.get('unit_id')
        self.called.append(uid)
        if uid in self.irreparable:
            return {'outcome': 'GAVE-UP', 'attempts': [1, 2, 3],
                    'final_verdict': verdict, 'reason': 'seeded: irreparable'}
        return {'outcome': 'REPAIRED', 'attempts': [1],
                'final_verdict': {'verdict': 'PASS'}, 'reason': 'fixed'}


class Router(object):
    """Sends each node to ITS OWN stub. run_node() is handed one `worker`
    for the whole dispatch call, so this is what lets one call to
    loop_bridge.run() drive several DIFFERENT stubs at once."""

    def __init__(self, workers):
        self.workers = workers

    def run(self, unit, cwd=None):
        return self.workers[unit['unit_id']].run(unit, cwd=cwd)


def run_round(the_doc, the_repo, store, owner, workers,
              slots=4, max_in_flight=2, fail=None, irreparable=None):
    """One dispatch round through the REAL pieces, in the same order
    loop_bridge.py's own main() runs them: graph_loop admits, claim_store
    claims (claim before spawn), worktree_lane isolates, loop_bridge
    dispatches concurrently, scope_audit measures what each lane actually
    changed (read BEFORE integrate() retires the lane), integrate() lands
    green units on canonical one at a time, and claim_store releases with
    the state the unit actually ended in. A unit that integrates has its
    row's status set to DONE in `the_doc`, so the NEXT call to this function
    (or to graph_loop.plan() directly) sees the real dependency graph state.

    Returns (plan, outcome, integrated, scope_by_unit).
    """
    plan = graph_loop.plan(the_doc, slots=slots)
    batch = loop_bridge.dispatchable(plan)

    claimed = []
    for n in batch:
        claim, _problem = claim_store.acquire(store, n['id'], owner)
        if claim is not None:
            claimed.append(n)

    if not claimed:
        return plan, {'dispatched': [], 'not_dispatched': loop_bridge.refused(plan),
                      'isolation': {}}, [], {}

    before = head(the_repo)
    ws_lanes = worktree_lane.Lanes(the_repo, [n['id'] for n in claimed])
    parts = {'spawn': None, 'verify': FakeVerify(fail=fail),
             'repair': FakeRepair(irreparable=irreparable)}

    outcome = loop_bridge.run({'batch': claimed, 'deferred': [], 'blocked': []},
                              parts, Router(workers), cwd=the_repo,
                              max_in_flight=max_in_flight, lanes=ws_lanes)

    # THE PER-UNIT, PER-FILE PROVENANCE, read from scope_audit -- the same
    # module run_node() itself uses (loop_bridge._audit_scope) -- BEFORE
    # integrate() removes the lane below.
    scope_by_unit = {}
    for n in claimed:
        uid = n['id']
        lane = ws_lanes.lanes.get(uid)
        if not lane:
            continue
        after = head(lane['path'])
        _verdict, detail = scope_audit.audit(
            {'unit_id': uid, 'write_scope': n.get('owns') or []},
            before, after, cwd=lane['path'])
        scope_by_unit[uid] = {'verdict': _verdict, 'changed': detail.get('changed', [])}

    lane_branches = {uid: lane['branch'] for uid, lane in ws_lanes.lanes.items()}
    units_by_id = {n['id']: n for n in claimed}
    by_id = {r['id']: r for r in outcome.get('dispatched', [])}
    results = [by_id.get(n['id']) or {'id': n['id']} for n in claimed]
    integrated = integrate.integrate(the_repo, results, lane_branches, units_by_id)

    verdict_by_uid = {v['unit']: v for v in integrated}
    rows_by_id = {row['id']: row for row in the_doc.get('rows', [])}
    for n in claimed:
        uid = n['id']
        v = verdict_by_uid.get(uid, {})
        merged = v.get('verdict') in (integrate.INTEGRATED, integrate.ALREADY_INTEGRATED)
        state = 'done' if merged else 'failed'
        claim_store.release(store, uid, owner, state=state, evidence=v.get('evidence'))
        if merged and uid in rows_by_id:
            rows_by_id[uid]['status'] = 'DONE'

    return plan, outcome, integrated, scope_by_unit


# ---------------------------------------------------------------------------
# CASE 1: A and B overlap in wall clock when 2 slots are allowed; not with 1.
# ---------------------------------------------------------------------------

class CaseOneConcurrentDispatch(unittest.TestCase):

    def test_a_and_b_overlap_in_wall_clock_with_two_slots(self):
        r, store, trace = repo(), store_path(), Trace(trace_path())
        d = diamond_doc()
        workers = {'A': StubWorker('A', trace, sleep=0.3),
                   'B': StubWorker('B', trace, sleep=0.3)}
        run_round(d, r, store, 'sess', workers, slots=4, max_in_flight=2)
        a0, a1 = trace.starts('A')[0]['t'], trace.ends('A')[0]['t']
        b0, b1 = trace.starts('B')[0]['t'], trace.ends('B')[0]['t']
        print('CASE 1 (max_in_flight=2): A [%.4f, %.4f]  B [%.4f, %.4f]'
              % (a0, a1, b0, b1))
        self.assertLess(b0, a1, 'B started at %.4f, A ended at %.4f: no overlap'
                        % (b0, a1))
        self.assertLess(a0, b1, 'A started at %.4f, B ended at %.4f: no overlap'
                        % (a0, b1))

    def test_a_and_b_do_NOT_overlap_with_one_slot(self):
        r, store, trace = repo(), store_path(), Trace(trace_path())
        d = diamond_doc()
        workers = {'A': StubWorker('A', trace, sleep=0.3),
                   'B': StubWorker('B', trace, sleep=0.3)}
        run_round(d, r, store, 'sess', workers, slots=4, max_in_flight=1)
        a0, a1 = trace.starts('A')[0]['t'], trace.ends('A')[0]['t']
        b0, b1 = trace.starts('B')[0]['t'], trace.ends('B')[0]['t']
        print('CASE 1 (max_in_flight=1): A [%.4f, %.4f]  B [%.4f, %.4f]'
              % (a0, a1, b0, b1))
        self.assertTrue(a1 <= b0 or b1 <= a0,
                        'A [%.4f,%.4f] and B [%.4f,%.4f] overlapped at '
                        'max_in_flight=1' % (a0, a1, b0, b1))


# ---------------------------------------------------------------------------
# CASE 2: claims for A and B never overlap on a path, through the claim
# store's own records.
# ---------------------------------------------------------------------------

class CaseTwoClaimsNeverOverlapOnAPath(unittest.TestCase):

    def test_the_stores_own_records_show_disjoint_paths_claimed_concurrently(self):
        r, store, trace = repo(), store_path(), Trace(trace_path())
        d = diamond_doc()
        workers = {'A': StubWorker('A', trace, sleep=0.2),
                   'B': StubWorker('B', trace, sleep=0.2)}
        run_round(d, r, store, 'sess', workers, slots=4, max_in_flight=2)
        with open(store, encoding='utf-8') as fh:
            claims = json.load(fh)
        a, b = claims['A'], claims['B']
        self.assertNotEqual(a['worker_id'], b['worker_id'])
        # THE PATHS, cross-referenced against the fixture's own declaration,
        # never guessed: A and B never declare the same file.
        owns = {row['id']: row['owns'] for row in d['rows']}
        self.assertEqual(set(owns['A']) & set(owns['B']), set())
        # THE OVERLAP, from the claim store's OWN claimed_at/released_at,
        # independent of the wall-clock trace case 1 used.
        self.assertLess(a['claimed_at'], b['released_at'])
        self.assertLess(b['claimed_at'], a['released_at'])
        self.assertEqual(a['state'], 'done')
        self.assertEqual(b['state'], 'done')


# ---------------------------------------------------------------------------
# CASE 3: C never starts before A and B reach the EXACT state graph_loop's
# own dependency contract requires (read from the source, not assumed).
# ---------------------------------------------------------------------------

class CaseThreeDependencyGate(unittest.TestCase):

    def test_c_is_blocked_until_BOTH_a_and_b_are_done(self):
        d = diamond_doc()
        p1 = graph_loop.plan(d, slots=4)
        self.assertEqual(sorted(n['id'] for n in p1['batch']), ['A', 'B'])
        rows = {r['id']: r for r in d['rows']}

        rows['A']['status'] = 'DONE'
        p2 = graph_loop.plan(d, slots=4)
        blocked2 = dict((n['id'], unmet) for n, unmet in p2['blocked'])
        self.assertEqual(blocked2.get('C'), ['B'])
        self.assertNotIn('C', [n['id'] for n in p2['batch']])

        rows['B']['status'] = 'DONE'
        p3 = graph_loop.plan(d, slots=4)
        self.assertIn('C', [n['id'] for n in p3['batch']])

    def test_the_accepted_state_is_the_TUPLE_graph_loop_reads_not_only_DONE(self):
        """graph_loop.plan()'s own `done` set is built from status in
        ('DONE', 'SUPERSEDED', 'ADDRESSED'), read from the source rather than
        assumed. SUPERSEDED and ADDRESSED must satisfy C's dependency exactly
        as DONE does, or this assumption would be wrong."""
        d = diamond_doc()
        rows = {r['id']: r for r in d['rows']}
        rows['A']['status'] = 'SUPERSEDED'
        rows['B']['status'] = 'ADDRESSED'
        p = graph_loop.plan(d, slots=4)
        self.assertIn('C', [n['id'] for n in p['batch']])

    def test_d_stays_blocked_until_c_is_done(self):
        d = diamond_doc()
        rows = {r['id']: r for r in d['rows']}
        rows['A']['status'] = rows['B']['status'] = 'DONE'
        p = graph_loop.plan(d, slots=4)
        blocked = dict((n['id'], unmet) for n, unmet in p['blocked'])
        self.assertEqual(blocked.get('D'), ['C'])
        rows['C']['status'] = 'DONE'
        p2 = graph_loop.plan(d, slots=4)
        self.assertIn('D', [n['id'] for n in p2['batch']])


# ---------------------------------------------------------------------------
# CASE 4: integration order is deterministic regardless of finish order.
# ---------------------------------------------------------------------------

class CaseFourIntegrationOrderIsDeterministic(unittest.TestCase):

    def test_the_integration_sequence_is_identical_across_finish_orders(self):
        trials = [(0.3, 0.0), (0.0, 0.3), (0.15, 0.15)]
        orders = []
        for sleep_a, sleep_b in trials:
            r, store, trace = repo(), store_path(), Trace(trace_path())
            d = diamond_doc()
            workers = {'A': StubWorker('A', trace, sleep=sleep_a),
                       'B': StubWorker('B', trace, sleep=sleep_b)}
            _plan, _outcome, integrated, _scope = run_round(
                d, r, store, 'sess', workers, slots=4, max_in_flight=2)
            orders.append([v['unit'] for v in integrated])
        print('CASE 4 integration order across 3 trials (sleep_a, sleep_b '
              'varied): %s' % orders)
        self.assertTrue(all(o == orders[0] for o in orders),
                        'integration order varied across trials: %s' % orders)
        self.assertEqual(orders[0], ['A', 'B'])


# ---------------------------------------------------------------------------
# CASE 5: a failure in A blocks C and D only; B still finishes and
# integrates.
# ---------------------------------------------------------------------------

class CaseFiveFailureBlocksOnlyDependents(unittest.TestCase):

    def test_a_failed_a_blocks_c_and_d_but_never_b(self):
        r, store, trace = repo(), store_path(), Trace(trace_path())
        d = diamond_doc()
        workers = {'A': StubWorker('A', trace), 'B': StubWorker('B', trace)}
        _plan, _outcome, integrated, _scope = run_round(
            d, r, store, 'sess', workers, slots=4, max_in_flight=2,
            fail=['A'], irreparable=['A'])
        verdicts = {v['unit']: v['verdict'] for v in integrated}
        self.assertNotEqual(verdicts.get('A'), integrate.INTEGRATED)
        self.assertEqual(verdicts.get('B'), integrate.INTEGRATED)

        rows = {row['id']: row for row in d['rows']}
        self.assertNotEqual(rows['A']['status'], 'DONE')
        self.assertEqual(rows['B']['status'], 'DONE')

        p2 = graph_loop.plan(d, slots=4)
        blocked = dict((n['id'], unmet) for n, unmet in p2['blocked'])
        self.assertIn('C', blocked)
        self.assertIn('A', blocked['C'])
        self.assertNotIn('B', blocked['C'])
        # D is blocked TRANSITIVELY through C, never directly by A.
        self.assertIn('D', blocked)
        self.assertEqual(blocked['D'], ['C'])


# ---------------------------------------------------------------------------
# CASE 6: a crash of B does not cause A to run twice; B's resume is exactly
# one bare invocation.
# ---------------------------------------------------------------------------

class CaseSixCrashOfBDoesNotDuplicateA(unittest.TestCase):

    def test_a_crash_of_b_does_not_double_run_a_and_b_resumes_once_bare(self):
        r, store, trace = repo(), store_path(), Trace(trace_path())
        d = diamond_doc()

        # ROUND 1: A runs normally; B dies at the worker seam (see the
        # module docstring for exactly which crash this seam expresses).
        crashing_b = StubWorker('B', trace, die=True)
        workers1 = {'A': StubWorker('A', trace), 'B': crashing_b}
        plan1, outcome1, _integrated1, _scope1 = run_round(
            d, r, store, 'sess', workers1, slots=4, max_in_flight=2)

        by_id1 = {rec['id']: rec for rec in outcome1['dispatched']}
        self.assertEqual(by_id1['A']['verdict'], 'PASS')
        self.assertEqual(by_id1['B']['verdict'], 'NO-DATA')
        self.assertEqual(trace.invocations('A'), 1)
        self.assertEqual(trace.invocations('B'), 1)

        rows = {row['id']: row for row in d['rows']}
        self.assertEqual(rows['A']['status'], 'DONE')
        self.assertNotEqual(rows['B']['status'], 'DONE')

        # ROUND 2, THE RESUME. A is DONE, so graph_loop must never offer it
        # again. B's round-1 claim was released state=failed by run_round
        # (the exception was caught one layer up in loop_bridge.run(),
        # rather than killing the whole controlling process), so it is
        # claimable again without needing claim_store.reconcile()'s
        # dead-pid path.
        resumed_b = StubWorker('B', trace)
        workers2 = {'B': resumed_b}
        plan2, outcome2, integrated2, _scope2 = run_round(
            d, r, store, 'sess', workers2, slots=4, max_in_flight=2)

        self.assertEqual([n['id'] for n in plan2['batch']], ['B'],
                         'A must not be offered to the scheduler again')
        by_id2 = {rec['id']: rec for rec in outcome2['dispatched']}
        self.assertEqual(by_id2['B']['verdict'], 'PASS')
        verdicts2 = {v['unit']: v['verdict'] for v in integrated2}
        self.assertEqual(verdicts2['B'], integrate.INTEGRATED)

        # THE BARE RESUME: run_node() always builds a FRESH unit dict per
        # dispatch (attempt=1, no prior_failure_note) -- read from the
        # source, scripts/loop_bridge.py's run_node() -- so B's resumed
        # invocation is bare by construction.
        self.assertIsNotNone(resumed_b.last_unit)
        self.assertEqual(resumed_b.last_unit['attempt'], 1)
        self.assertEqual(resumed_b.last_unit['prior_failure_note'], '')

        self.assertEqual(trace.invocations('A'), 1,
                         'A ran a SECOND time during B resume')
        self.assertEqual(trace.invocations('B'), 2,
                         '1 crashed start in round 1 + 1 resumed bare start '
                         'in round 2')


# ---------------------------------------------------------------------------
# CASE 7: two nodes declaring the same file are refused by admission, with
# the refusal reason graph_loop itself records.
# ---------------------------------------------------------------------------

class CaseSevenSameFileRefusedByAdmission(unittest.TestCase):

    def test_two_nodes_owning_the_same_file_are_refused_by_admission(self):
        d = doc([node('X', owns=['shared.py']), node('Y', owns=['shared.py'])])
        p = graph_loop.plan(d, slots=4)
        batch_ids = [n['id'] for n in p['batch']]
        self.assertEqual(len(batch_ids), 1)
        winner = batch_ids[0]
        loser = 'Y' if winner == 'X' else 'X'
        deferred = dict((n['id'], why) for n, why in p['deferred'])
        self.assertIn(loser, deferred)
        self.assertIn('write set overlaps', deferred[loser])
        self.assertIn(winner, deferred[loser])


# ---------------------------------------------------------------------------
# CASE 8: receipt provenance (scope_audit's own per-unit result) stays per
# unit and per file after the run.
# ---------------------------------------------------------------------------

class CaseEightReceiptProvenanceStaysPerUnitPerFile(unittest.TestCase):

    def test_each_units_scope_names_exactly_its_own_declared_file(self):
        r, store, trace = repo(), store_path(), Trace(trace_path())
        d = diamond_doc()
        workers = {'A': StubWorker('A', trace), 'B': StubWorker('B', trace)}
        _plan, _outcome, _integrated, scope = run_round(
            d, r, store, 'sess', workers, slots=4, max_in_flight=2)

        self.assertEqual(scope['A']['verdict'], scope_audit.CLEAN)
        self.assertEqual(scope['A']['changed'], ['file_a.py'])
        self.assertEqual(scope['B']['verdict'], scope_audit.CLEAN)
        self.assertEqual(scope['B']['changed'], ['file_b.py'])
        # NEITHER receipt carries the other's file: provenance stays per unit.
        self.assertNotIn('file_b.py', scope['A']['changed'])
        self.assertNotIn('file_a.py', scope['B']['changed'])

        workers2 = {'C': StubWorker('C', trace)}
        _plan2, _outcome2, _integrated2, scope2 = run_round(
            d, r, store, 'sess', workers2, slots=4, max_in_flight=2)
        self.assertEqual(scope2['C']['verdict'], scope_audit.CLEAN)
        self.assertEqual(scope2['C']['changed'], ['integration.py'])


if __name__ == '__main__':
    unittest.main()

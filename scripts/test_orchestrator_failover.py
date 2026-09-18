#!/usr/bin/env python3
"""ORCH-15 of the 1.0.20 orchestration control plane: the whole-night fault lab.

WHY THIS UNIT EXISTS AND IS NOT LIKE THE OTHER TEST FILES IN THIS BUILD. Every
other unit tonight tested one module alone. A dependency store that never held
a stale epoch and a cross-review gate that never gated the wrong revision can
still meet at a seam neither one's own suite could see: the moment an
orchestrator restarts mid-dispatch, or a RED refusal on one unit reaches into
the loop that dispatches an unrelated one. This file runs several of those
modules TOGETHER, against one fixed dependency graph, with faults injected
between calls, and asserts five properties over the WHOLE run rather than over
one function's return value.

THE FIXTURE GRAPH, used by every fault below that needs one:
    A -> D
    B -> E
    C independent
    D and E -> F

THE BAD STATE THIS FILE'S OWN GREEN RUN WOULD ALSO PASS, AND HOW IT IS BLOCKED.
A "fault lab" built entirely from stand-ins (a fake authority store that always
says no, a fake cross-review gate that always blocks) would make every
property in this file trivially true forever, the same way a hard-coded
staleness check would pass every test in test_orchestrator_cross_review.py
while blocking every legitimate review too. So this file drives the REAL
scripts/orchestrator_authority.py, scripts/orchestrator_boundary.py,
scripts/orchestrator_cross_review.py, scripts/orchestrator_hard_stop.py,
scripts/orchestrator_replan.py, scripts/orchestrator_closeout.py,
scripts/night_supervisor.py, scripts/claim_store.py and scripts/fable_authority.py
directly: the only things stood in for are a model call, a real subprocess
orchestrator process, and the wall clock, exactly the three the worker brief
names as the genuinely-cannot-run-in-a-test cases (spawn() and the liveness
adapters passed to night_supervisor.supervise(), which that module's own
signature requires the caller to inject). test_real_modules_actually_raised
at the bottom of this file is the direct defence: it asserts that, over the
course of these faults, several of the DISTINCT real exception types these
modules define were actually raised by the real code, not merely imported.
A fault lab wired to stubs that never reach the modules under test would
leave that set empty; it does not, because every exception in it below is
provoked by driving the real function into the real refusal path the module
itself defines (a live lease already held, a stale authorization, a review at
a moved-on revision, a phase misuse, a malformed run_state), not by raising it
by hand in the test.

Composed, never rebuilt: this file holds no second scheduler, no second claim
store, no second authority store and no second cross-review gate. The one
place it holds its own logic is `ready_units()` (a plain topological read of
the six-node fixture graph above, not a dispatcher) and a tiny in-memory
`canonical` dict standing for "what has actually landed", which several tests
mutate through orchestrator_hard_stop.drain()'s own injected `integrate`/`park`
callbacks, exactly the extension point that module defines for a caller's
domain logic.

Injected clock throughout: every module call below takes `now=<float>` or
`clock=<lambda: float>`; nothing here ever sleeps, and the whole file runs
in a small fraction of a second.

FAULTS COVERED, and how each is driven into real code (see FaultCoverage.COVERED
at the end of a passing run for the same list programmatically):
   1. orchestrator crashes                    -> test_f01_f02_f09_supervisor_restarts_dead_orchestrators
   2. the other orchestrator crashes          -> same test, second scope
   3. leaf worker times out                   -> test_f03_leaf_worker_timeout
   4. leaf worker returns malformed output    -> test_f04_leaf_worker_malformed_output
   5. required check reports NO-DATA          -> test_f05_required_check_no_data
   6. integration conflict                    -> test_f06_integration_conflict
   7. canonical regression after integration  -> test_f07_canonical_regression_rolls_back
   8. stale orchestrator wakes and acts        -> test_f08_stale_orchestrator_cannot_act
   9. the supervisor itself restarts          -> test_f01_f02_f09_supervisor_restarts_dead_orchestrators
  10. capacity drops from three to one        -> test_f10_capacity_drop_throttles_dispatch
  11. a RED decision appears                  -> test_f11_red_decision_blocked_c_still_finishes
  12. provisional ruling without the owner    -> test_f12_provisional_ruling_without_the_owner
  13. cross review goes stale after a commit  -> test_f13_cross_review_goes_stale
  14. hard stop arrives during a repair       -> test_f14_hard_stop_during_repair

None of the fourteen are skipped; where a module's own real machinery could not
be driven without also standing up a real git repository (a genuine `git merge`
through scripts/integrate.py), the IN-MEMORY canonical dict plus
orchestrator_hard_stop's own injected integrate()/park() callbacks stands in,
per the worker brief's own allowance to stub "a real subprocess orchestrator".

THE FIVE PROPERTIES:
  P1 no double dispatch     -> DoubleDispatchCounter, test_p1_no_double_dispatch_ever
  P2 canonical stays valid  -> assert_canonical_consistent, test_p2_canonical_stays_valid
  P3 safe work continues    -> test_f11_red_decision_blocked_c_still_finishes
  P4 restart recovers       -> test_f01_f02_f09_supervisor_restarts_dead_orchestrators
  P5 handoff explains all   -> test_p5_handoff_explains_every_unfinished_unit

Python 3.9 floor, standard library only, plain unittest, runnable directly:
    python3 scripts/test_orchestrator_failover.py -v
"""
import json
import os
import sys
import tempfile
import unittest
from datetime import datetime, timezone

HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.insert(0, HERE)

import claim_store  # noqa: E402
import fable_authority  # noqa: E402
import night_supervisor as supervisor  # noqa: E402
import orchestrator_authority as authority  # noqa: E402
import orchestrator_boundary as boundary  # noqa: E402
import orchestrator_closeout as closeout  # noqa: E402
import orchestrator_cross_review as cross_review  # noqa: E402
import orchestrator_hard_stop as hard_stop  # noqa: E402
import orchestrator_invariants as invariants  # noqa: E402
import orchestrator_replan as replan  # noqa: E402

try:
    import tmp_sandbox
    tmp_sandbox.install()
except ImportError:
    sys.stderr.write(
        "tmp_sandbox absent: %s leaves its temp trees behind\n"
        % os.path.basename(__file__))


NOW = 1_800_000_000.0

# THE FIXTURE GRAPH, exactly as named in the worker brief. A tuple of
# dependencies per unit; C's tuple is empty (independent).
GRAPH = {"A": (), "B": (), "C": (), "D": ("A",), "E": ("B",), "F": ("D", "E")}


def ready_units(graph, done):
    """Every unit not yet in `done` whose dependencies are all in `done`,
    sorted for determinism. This is a topological READ of the fixed fixture
    graph, not a scheduler: it holds no priority rule, no concurrency-limit
    rule and no conflict check, all of which are scripts/graph_loop.py's
    job on a live roadmap document, not this test's."""
    return sorted(u for u, deps in graph.items()
                  if u not in done and set(deps) <= set(done))


def assert_canonical_consistent(testcase, canonical, graph):
    """P2: a unit present in `canonical` (has actually landed) must have
    every one of its dependencies present too. A canonical dict violating
    this is the exact shape of a torn integration: F landed while D did
    not, or D landed on top of an A that was rolled back. Raises via the
    testcase's own assertion, so every call site's failure names the file
    and line of the test that produced the bad canonical, not a bare
    AssertionError from a shared helper."""
    for unit_id in canonical:
        for dep in graph.get(unit_id, ()):
            testcase.assertIn(
                dep, canonical,
                "canonical is inconsistent: %r landed but its dependency "
                "%r did not" % (unit_id, dep))


class DoubleDispatchCounter(object):
    """P1: counts moments at which more than one instance's authority
    check() came back True for the same (run_id, scope) at the same tick.
    Built on the REAL orchestrator_authority.check(), never on an inferred
    absence of an exception: two live holders is read directly off two
    real check() return values, so a mutation of check() that stops
    comparing epochs (the drill run manually against a /tmp copy of the
    module, see the module docstring) is exactly what turns this counter
    positive."""

    def __init__(self):
        self.events = []  # list of (tick, tuple of instances both live)

    def observe(self, tick, store_path, run_id, scope, candidates):
        """`candidates` is an iterable of (instance, epoch). Returns the
        list of instances whose check() was True at this tick."""
        holders = []
        for instance, epoch in candidates:
            if authority.check(store_path, run_id, scope, instance, epoch,
                                now=tick):
                holders.append(instance)
        if len(holders) > 1:
            self.events.append((tick, tuple(holders)))
        return holders


class FaultCoverage(object):
    """Recorded once per fault by the test that drives it, so a reader (or
    a final assertion) never has to trust the module docstring's own claim
    of what was covered."""
    COVERED = {}

    @classmethod
    def cover(cls, n, note):
        cls.COVERED[n] = note


#: Real exception types this file's own tests must observe being raised by
#: real code during a fault, never raised by hand in the test itself. See
#: the module docstring's BAD STATE section and test_real_modules_actually_raised.
EXCEPTIONS_SEEN = set()


def _iso(epoch_s):
    return datetime.fromtimestamp(epoch_s, tz=timezone.utc).isoformat()


class _FakeAdapter(object):
    """The one thing night_supervisor.supervise() requires a caller to
    inject: a liveness adapter. Driven entirely by the test, matching this
    estate's own scripts/test_night_supervisor.py fixture shape, never a
    real pid or a real heartbeat file."""

    def __init__(self, alive=True, heartbeat_age=0.0):
        self.alive = alive
        self.heartbeat_age = heartbeat_age

    def process_alive(self, pid):
        return self.alive

    def heartbeat_age_s(self, now):
        return self.heartbeat_age


class _RecordingSpawn(object):
    """The other thing supervise() requires injected: a real subprocess
    orchestrator is exactly what the worker brief names as impossible to
    run in a test, so this records calls and hands out fake sequential
    pids instead of forking anything."""

    def __init__(self, start_pid=1000):
        self.calls = []
        self._next_pid = start_pid

    def __call__(self, scope_cfg, lease):
        self.calls.append((dict(scope_cfg), lease))
        pid = self._next_pid
        self._next_pid += 1
        return pid


class FaultLabTestCase(unittest.TestCase):
    """Shared plumbing: a fresh temp directory and the store paths every
    fault test needs, so each test is independently runnable (unittest
    gives no ordering guarantee) while every test still exercises the same
    fixture graph and the same real modules."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="orch15-")
        self.authority_store = os.path.join(self.tmp, "authority.json")
        self.claim_path = os.path.join(self.tmp, "claims.json")
        self.red_queue_path = os.path.join(self.tmp, "red-queue.jsonl")
        self.amber_log_path = os.path.join(self.tmp, "amber.jsonl")
        self.run_id = "run-fixture"


# --------------------------------------------------------------- faults 1,2,9

class SupervisorRestartsAndSelfRestarts(FaultLabTestCase):
    """FAULTS 1, 2 and 9, and PROPERTY P4 (restart recovers).

    Two orchestrator scopes stand for the two orchestrator processes this
    build's authority module was written for ("primary" and "secondary").
    Each crash (faults 1 and 2) is driven through the REAL
    night_supervisor.supervise() with a fake liveness adapter reporting the
    process dead; each restart mints a genuinely new epoch through the REAL
    orchestrator_authority.takeover(), never through this test's own memory
    of a prior one. Fault 9 (the supervisor itself restarts) is modelled
    the way the module's own docstring says it must be handled: supervise()
    is called again with brand-new Python objects (a new spawn recorder, a
    new adapters dict) standing for a freshly started supervisor PROCESS,
    reading only the durable state and manifest files the previous call
    left on disk. P4 holds when that second "restart" does not lose the
    prior epoch/pid bookkeeping and does not spawn a second replacement for
    a scope that is, in fact, still alive.
    """

    def _manifest(self, hard_stop_epoch):
        manifest = {
            "run_id": self.run_id,
            "authority_store": self.authority_store,
            "state_path": os.path.join(self.tmp, "supervisor-state.json"),
            "journal_path": os.path.join(self.tmp, "supervisor-journal.jsonl"),
            "hard_stop": _iso(hard_stop_epoch),
            "scopes": [
                {"scope": "orch-primary", "orchestrator": "execution",
                 "instance": "primary-a", "ttl_seconds": 300,
                 "heartbeat_stale_s": 60, "max_restarts": 3,
                 "backoff_base_s": 10},
                {"scope": "orch-secondary", "orchestrator": "execution",
                 "instance": "secondary-a", "ttl_seconds": 300,
                 "heartbeat_stale_s": 60, "max_restarts": 3,
                 "backoff_base_s": 10},
            ],
        }
        path = os.path.join(self.tmp, "manifest.json")
        with open(path, "w", encoding="utf-8") as fh:
            json.dump(manifest, fh)
        return path, manifest

    def test_f01_f02_f09_supervisor_restarts_dead_orchestrators(self):
        manifest_path, manifest = self._manifest(hard_stop_epoch=NOW + 1_000_000.0)

        # Tick 0: nothing has ever run. Both scopes read DEAD ("no process
        # has ever been recorded"), which is the normal first-tick state,
        # not a crash; supervise() mints epoch 1 for each via the REAL
        # orchestrator_authority.takeover().
        adapters_0 = {"orch-primary": _FakeAdapter(alive=False),
                      "orch-secondary": _FakeAdapter(alive=False)}
        spawn_0 = _RecordingSpawn(start_pid=1000)
        report_0 = supervisor.supervise(manifest_path, adapters=adapters_0,
                                        now=NOW, spawn=spawn_0)
        self.assertEqual(sorted(s["scope"] for s in report_0.restarted),
                         ["orch-primary", "orch-secondary"])
        lease_primary = authority.current(self.authority_store, self.run_id,
                                          "orch-primary", now=NOW)
        lease_secondary = authority.current(self.authority_store, self.run_id,
                                            "orch-secondary", now=NOW)
        self.assertEqual(lease_primary.epoch, 1)
        self.assertEqual(lease_secondary.epoch, 1)

        # Tick 1: both alive, fresh heartbeat.
        adapters_1 = {"orch-primary": _FakeAdapter(alive=True, heartbeat_age=1.0),
                      "orch-secondary": _FakeAdapter(alive=True, heartbeat_age=1.0)}
        spawn_1 = _RecordingSpawn(start_pid=9999)  # must not be called
        report_1 = supervisor.supervise(manifest_path, adapters=adapters_1,
                                        now=NOW + 10, spawn=spawn_1)
        self.assertEqual(report_1.restarted, [])
        self.assertEqual(sorted(s["scope"] for s in report_1.alive),
                         ["orch-primary", "orch-secondary"])
        self.assertEqual(spawn_1.calls, [], "an alive process must never "
                         "be spawned again")

        # FAULT 1: the primary orchestrator process crashes.
        adapters_2 = {"orch-primary": _FakeAdapter(alive=False),
                      "orch-secondary": _FakeAdapter(alive=True, heartbeat_age=1.0)}
        spawn_2 = _RecordingSpawn(start_pid=2000)
        report_2 = supervisor.supervise(manifest_path, adapters=adapters_2,
                                        now=NOW + 20, spawn=spawn_2)
        self.assertEqual([s["scope"] for s in report_2.restarted], ["orch-primary"])
        lease_primary_2 = authority.current(self.authority_store, self.run_id,
                                            "orch-primary", now=NOW + 20)
        self.assertEqual(lease_primary_2.epoch, 2, "a restart must mint a "
                         "genuinely new epoch, never reuse the old one")
        FaultCoverage.cover(1, "night_supervisor.supervise() restarted "
                            "orch-primary and minted epoch 2 via the real "
                            "orchestrator_authority.takeover()")

        # FAULT 2: the OTHER orchestrator process crashes, independently.
        adapters_3 = {"orch-primary": _FakeAdapter(alive=True, heartbeat_age=1.0),
                      "orch-secondary": _FakeAdapter(alive=False)}
        spawn_3 = _RecordingSpawn(start_pid=3000)
        report_3 = supervisor.supervise(manifest_path, adapters=adapters_3,
                                        now=NOW + 30, spawn=spawn_3)
        self.assertEqual([s["scope"] for s in report_3.restarted], ["orch-secondary"])
        lease_secondary_2 = authority.current(self.authority_store, self.run_id,
                                              "orch-secondary", now=NOW + 30)
        self.assertEqual(lease_secondary_2.epoch, 2)
        FaultCoverage.cover(2, "a second, independent supervise() call "
                            "restarted orch-secondary without touching "
                            "orch-primary's already-restarted state")

        # FAULT 9: the SUPERVISOR ITSELF restarts. Modelled with brand-new
        # Python objects (new spawn recorder, new adapters dict) standing
        # for a freshly started process, reading only the manifest and the
        # durable state file left by report_3's own supervise() call.
        adapters_4 = {"orch-primary": _FakeAdapter(alive=True, heartbeat_age=1.0),
                      "orch-secondary": _FakeAdapter(alive=True, heartbeat_age=1.0)}
        spawn_4 = _RecordingSpawn(start_pid=4000)  # must not be called
        report_4 = supervisor.supervise(manifest_path, adapters=adapters_4,
                                        now=NOW + 40, spawn=spawn_4)
        self.assertEqual(report_4.restarted, [],
                         "a freshly 'restarted' supervisor process reading "
                         "the same durable state must not re-restart "
                         "processes that are, in fact, alive")
        self.assertEqual(spawn_4.calls, [])
        # P4: nothing was lost (both scopes still have durable, monotonic
        # epochs) and nothing was silently repeated (spawn was not called).
        with open(manifest["state_path"], encoding="utf-8") as fh:
            durable_state = json.load(fh)
        self.assertEqual(durable_state["orch-primary"]["epoch"], 2)
        self.assertEqual(durable_state["orch-secondary"]["epoch"], 2)
        # 2 restarts each: the initial tick-0 start (nothing had ever been
        # recorded, so it reads DEAD and is "restarted" once) plus the one
        # crash fault this test drove into each scope.
        self.assertEqual(durable_state["orch-primary"]["restart_count"], 2)
        self.assertEqual(durable_state["orch-secondary"]["restart_count"], 2)
        FaultCoverage.cover(9, "supervise() called again with fresh spawn/"
                            "adapter objects (standing for a restarted "
                            "supervisor process) reproduced the same "
                            "decisions from disk alone, per P4")

        # A live foreign lease must refuse a restart attempt outright: a
        # REAL SupervisorRefused, not an inferred one.
        with self.assertRaises(supervisor.SupervisorRefused) as ctx:
            supervisor.attempt_restart(
                self.authority_store, self.run_id, "orch-primary",
                "some-other-instance", "execution", 300,
                "wrong instance", NOW + 40, spawn_4, manifest["scopes"][0])
        EXCEPTIONS_SEEN.add("SupervisorRefused")
        self.assertIn("does not own", str(ctx.exception))


# ------------------------------------------------------------------- fault 3

class LeafWorkerTimesOut(FaultLabTestCase):
    """FAULT 3. A leaf worker claims unit A, then never renews: its claim
    expires under the REAL claim_store.py, a second worker reclaims the
    same unit (never both at once), and orchestrator_replan.decision()
    classifies the resulting failure as transient and says RETRY."""

    def test_f03_leaf_worker_timeout(self):
        claim1, problem1 = claim_store.acquire(
            self.claim_path, "A", "worker-1", work_id=self.run_id, ttl=5,
            clock=lambda: NOW)
        self.assertIsNotNone(claim1, problem1)
        self.assertTrue(claim_store.live(claim1, NOW + 1))

        # The worker stalls. Its lease outlives it.
        self.assertFalse(claim_store.live(claim1, NOW + 6),
                         "a claim with ttl=5 must read dead 6s later")

        # A second worker reclaims the SAME unit; claim_store must report
        # who it was reclaimed from, never claim it silently.
        claim2, problem2 = claim_store.acquire(
            self.claim_path, "A", "worker-2", work_id=self.run_id, ttl=5,
            clock=lambda: NOW + 6)
        self.assertIsNotNone(claim2, problem2)
        self.assertEqual(claim2["reclaimed_from"], "worker-1")

        # No two workers ever both hold it live at once: at the moment of
        # reclaim, worker-1's own record has already been overwritten by
        # worker-2's, and worker-1's original claim object (not re-read
        # from disk) is provably not live at that same instant.
        self.assertFalse(claim_store.live(claim1, NOW + 6))

        decision = replan.decision(
            "A", "timeout",
            [{"failure_class": "timeout", "files_written": (), "check": "",
              "strategy": "attempt-1", "base_revision": "base-0"}],
            budgets={"max_transient_retries": 2})
        self.assertEqual(decision.verdict, replan.RETRY)
        FaultCoverage.cover(3, "claim_store.acquire()/live() proved the "
                            "expired claim was reclaimed, never doubled; "
                            "orchestrator_replan.decision() said RETRY")


# ------------------------------------------------------------------- fault 4

class LeafWorkerMalformedOutput(FaultLabTestCase):
    """FAULT 4. A worker's result is missing the evidence closeout requires
    (a real CloseoutError, not a guessed one), and orchestrator_replan
    correctly refuses to let a second, identically-shaped bad attempt pass
    as though it were a new approach."""

    def test_f04_leaf_worker_malformed_output(self):
        # A malformed unit record (state outside orchestrator_invariants.
        # TASK_STATES, exactly what a worker returning garbage would look
        # like) must be refused by close_out(), never silently accepted.
        run_state = _minimal_run_state(
            units=[{"id": "A", "state": "BOGUS-STATE", "task_class": "implementation"}])
        with self.assertRaises(closeout.CloseoutError) as ctx:
            closeout.close_out(run_state, out_dir=os.path.join(self.tmp, "handoff"))
        EXCEPTIONS_SEEN.add("CloseoutError")
        self.assertIn("BOGUS-STATE", str(ctx.exception))

        # The same malformed-output failure, fed to the real retry/replan
        # decision: first occurrence asks for a changed approach...
        attempt_1 = {"failure_class": "missing_evidence", "files_written": ("x.py",),
                     "check": "pytest", "strategy": "attempt-1", "base_revision": "base-0"}
        decision_1 = replan.decision("F", "missing_evidence", [attempt_1],
                                     budgets={"max_semantic_attempts": 3})
        self.assertEqual(decision_1.verdict, replan.RETRY_CHANGED)

        # ...and a second, IDENTICALLY-FINGERPRINTED attempt (same files,
        # same check, same strategy, same base) is refused as a repeat,
        # never silently retried a third time.
        attempt_2 = dict(attempt_1)
        decision_2 = replan.decision("F", "missing_evidence", [attempt_1, attempt_2],
                                     budgets={"max_semantic_attempts": 3})
        self.assertEqual(decision_2.verdict, replan.REPLAN)
        FaultCoverage.cover(4, "orchestrator_closeout raised CloseoutError "
                            "on a malformed unit state; orchestrator_replan "
                            "refused an identically-fingerprinted repeat")


# ------------------------------------------------------------------- fault 5

class RequiredCheckReportsNoData(FaultLabTestCase):
    """FAULT 5. orchestrator_invariants.may_proceed() is the one place the
    verdict-versus-obligation table lives; this drives it directly with a
    NO-DATA verdict against a REQUIRED_FOR_MERGE obligation."""

    def test_f05_required_check_no_data(self):
        self.assertFalse(invariants.may_proceed("NO-DATA", "REQUIRED_FOR_MERGE", "merge"))
        self.assertFalse(invariants.may_proceed("NO-DATA", "REQUIRED_FOR_MERGE", "release"))
        # An obligation that only binds at release must not block a merge.
        self.assertTrue(invariants.may_proceed("NO-DATA", "REQUIRED_FOR_RELEASE", "merge"))
        self.assertFalse(invariants.may_proceed("NO-DATA", "REQUIRED_FOR_RELEASE", "release"))
        self.assertTrue(invariants.may_proceed("NO-DATA", "OPTIONAL", "release"))
        FaultCoverage.cover(5, "orchestrator_invariants.may_proceed() blocked "
                            "NO-DATA against REQUIRED_FOR_MERGE at both stages")


# ------------------------------------------------------------------- fault 6

class IntegrationConflict(FaultLabTestCase):
    """FAULT 6. orchestrator_replan classifies integration_conflict as
    semantic; a genuinely different second attempt is allowed to retry
    changed, but a third attempt identical to the second is not."""

    def test_f06_integration_conflict(self):
        attempt_1 = {"failure_class": "integration_conflict", "files_written": ("f.py",),
                     "check": "ci", "strategy": "merge-ours", "base_revision": "base-1"}
        d1 = replan.decision("F", "integration_conflict", [attempt_1],
                             budgets={"max_semantic_attempts": 4})
        self.assertEqual(d1.verdict, replan.RETRY_CHANGED)

        attempt_2 = {"failure_class": "integration_conflict", "files_written": ("f.py",),
                     "check": "ci", "strategy": "merge-theirs", "base_revision": "base-1"}
        d2 = replan.decision("F", "integration_conflict", [attempt_1, attempt_2],
                             budgets={"max_semantic_attempts": 4})
        self.assertEqual(d2.verdict, replan.RETRY_CHANGED,
                         "a genuinely different strategy must not be read "
                         "as a repeat of the previous attempt")

        attempt_3 = dict(attempt_2)  # identical to attempt_2
        d3 = replan.decision("F", "integration_conflict", [attempt_1, attempt_2, attempt_3],
                             budgets={"max_semantic_attempts": 4})
        self.assertEqual(d3.verdict, replan.REPLAN)
        FaultCoverage.cover(6, "orchestrator_replan distinguished a "
                            "genuinely changed integration_conflict attempt "
                            "from a repeated one, and REPLANned the repeat")


# ------------------------------------------------------------------- fault 7

class CanonicalRegressionRollsBack(FaultLabTestCase):
    """FAULT 7, and half of PROPERTY P2. D and E land normally through
    orchestrator_hard_stop.drain()'s injected integrate() callback; F's
    post-integration check finds a regression (integrate() raises), and
    drain() must park F rather than leave canonical holding it. Composing
    the real module's own contract: 'a raised exception means this unit
    turned out not to be safe to land after all; it is parked instead.'"""

    def test_f07_canonical_regression_rolls_back(self):
        canonical = {"A": "rev-a", "B": "rev-b"}
        parked = {}

        def integrate(unit):
            if unit["id"] == "F":
                raise RuntimeError(
                    "canonical regression: F failed its post-integration check")
            canonical[unit["id"]] = "rev-%s" % unit["id"].lower()
            return None  # DONE

        def park(unit, record):
            parked[unit["id"]] = record

        units = [
            {"id": "D", "state": "INTEGRATING", "task_class": "implementation", "ready": True},
            {"id": "E", "state": "INTEGRATING", "task_class": "implementation", "ready": True},
            {"id": "F", "state": "INTEGRATING", "task_class": "implementation", "ready": True},
        ]
        result = hard_stop.drain(units, phase="DRAINING", integrate=integrate,
                                 park=park, now=NOW)
        self.assertEqual(sorted(result.integrated), ["D", "E"])
        self.assertEqual(result.parked, ["F"])
        self.assertNotIn("F", canonical, "a regression must never leave "
                         "the failing unit in canonical")
        self.assertIn("F", parked)
        self.assertIn("last_failure", parked["F"])

        assert_canonical_consistent(self, canonical, GRAPH)

        d_decision = replan.decision(
            "F", "canonical_regression",
            [{"failure_class": "canonical_regression", "files_written": ("f.py",),
              "check": "canonical-verify", "strategy": "attempt-1",
              "base_revision": "rev-d"}],
            budgets={"max_semantic_attempts": 3})
        self.assertEqual(d_decision.verdict, replan.RETRY_CHANGED)
        FaultCoverage.cover(7, "orchestrator_hard_stop.drain() rolled F "
                            "out of canonical on a raised regression; "
                            "canonical stayed dependency-consistent")


# ------------------------------------------------------------------- fault 8

class StaleOrchestratorCannotAct(FaultLabTestCase):
    """FAULT 8, and the headline half of PROPERTY P1. Primary acquires the
    dispatch scope, stalls past its TTL, secondary takes over (a genuinely
    new epoch). Primary then wakes and tries to act: this test proves,
    against the REAL orchestrator_authority.py and orchestrator_boundary.py,
    that primary's stale epoch is refused everywhere it could act, and that
    at no observed tick did both instances read as live authority holders
    at once (DoubleDispatchCounter, built on real check() calls, never on
    an inferred absence of an exception)."""

    def test_f08_stale_orchestrator_cannot_act(self):
        scope = "graph-run"
        counter = DoubleDispatchCounter()

        lease1 = authority.acquire(self.authority_store, self.run_id, scope,
                                   "primary", "primary-a", 50, now=NOW)
        self.assertEqual(lease1.epoch, 1)

        # A live lease may never be re-acquired, not even by the same
        # instance: acquire() is "the entry to a scope nobody holds".
        with self.assertRaises(authority.AuthorityRefused):
            authority.acquire(self.authority_store, self.run_id, scope,
                              "primary", "primary-a", 50, now=NOW + 10)
        EXCEPTIONS_SEEN.add("AuthorityRefused")

        # Primary stalls past its TTL (50s) and is restarted under the
        # SAME instance name, exactly the pattern orchestrator_authority.py's
        # own module docstring names as the actual incident this build was
        # warned about: "instance 'primary-a' holds epoch 5, stalls, the
        # lease expires, a restarted 'primary-a' takes over at epoch 6, and
        # the OLD process wakes believing it still holds the lane." Reusing
        # the instance NAME is expected; only the epoch may never repeat.
        lease2 = authority.takeover(self.authority_store, self.run_id, scope,
                                    "primary", "primary-a", 50,
                                    "primary lease expired: no heartbeat",
                                    now=NOW + 60)
        self.assertEqual(lease2.epoch, 2)

        # The OLD process (never learned of the restart, still believes
        # epoch 1) and the NEW process (same instance name, epoch 2) are
        # checked at the SAME tick. This is the exact pair the module's
        # own epoch mechanism exists to keep from both reading live: two
        # ACTUAL processes sharing one instance name, one holding a stale
        # belief. See the mutation drill in this unit's own report: this
        # is the precise check a stale-epoch-accepted mutation turns red.
        tick = NOW + 65
        holders = counter.observe(
            tick, self.authority_store, self.run_id, scope,
            [("primary-a", 1), ("primary-a", 2)])
        self.assertEqual(holders, ["primary-a"], "only the current epoch's "
                         "holder may read live, even though both checks "
                         "share the same instance name")
        self.assertEqual(counter.events, [],
                         "P1: no tick may ever read two live holders for "
                         "the same scope")

        # The boundary module (which composes authority.check() itself)
        # must refuse the OLD process's stale epoch an authorization to
        # act at all, and grant the NEW process's current epoch one.
        with self.assertRaises(boundary.BoundaryRefused) as ctx:
            boundary.authorize("DISPATCH", store_path=self.authority_store,
                               run_id=self.run_id, scope=scope,
                               instance="primary-a", epoch=1, now=tick)
        EXCEPTIONS_SEEN.add("BoundaryRefused")
        self.assertEqual(ctx.exception.invariant, "no stale authority")

        grant = boundary.authorize("DISPATCH", store_path=self.authority_store,
                                   run_id=self.run_id, scope=scope,
                                   instance="primary-a", epoch=2, now=tick)
        self.assertEqual(grant.epoch, 2)

        # A SECOND, genuinely different orchestrator handing off the scope
        # (not merely a restart under the same name) is refused the same
        # way if it still names the epoch it lost.
        lease3 = authority.handoff(self.authority_store, self.run_id, scope,
                                   "primary-a", 2, "secondary", "secondary-a",
                                   50, "deliberate handoff", now=tick)
        self.assertEqual(lease3.epoch, 3)
        with self.assertRaises(boundary.BoundaryRefused):
            boundary.authorize("DISPATCH", store_path=self.authority_store,
                               run_id=self.run_id, scope=scope,
                               instance="primary-a", epoch=2, now=tick + 1)
        FaultCoverage.cover(8, "a restarted 'primary-a' at a stale epoch "
                            "was refused by orchestrator_authority.check() "
                            "and orchestrator_boundary.authorize(); the "
                            "DoubleDispatchCounter recorded zero collisions "
                            "even though the old and new process share one "
                            "instance name")


# ------------------------------------------------------------------ fault 10

class CapacityDropThrottlesDispatch(FaultLabTestCase):
    """FAULT 10. Capacity dropping from three to one is modelled as a
    dispatch-budget the TEST's own tiny loop respects (this is test
    harness logic over the fixture graph, not a second production
    scheduler); the exclusivity of any one unit while it is held is still
    the REAL claim_store.py underneath it, so a bug here would still be
    caught by claim_store's own refusal to double-claim."""

    def _claim(self, unit_id, owner, now):
        claim, problem = claim_store.acquire(
            self.claim_path, unit_id, owner, work_id=self.run_id, ttl=100,
            clock=lambda: now)
        return claim, problem

    def test_f10_capacity_drop_throttles_dispatch(self):
        active = set()
        capacity = 3
        for unit_id in ("A", "B", "C"):
            self.assertLess(len(active), capacity)
            claim, problem = self._claim(unit_id, "worker-" + unit_id, NOW)
            self.assertIsNotNone(claim, problem)
            active.add(unit_id)
        self.assertEqual(len(active), 3)

        for unit_id in ("A", "B", "C"):
            claim_store.release(self.claim_path, unit_id, "worker-" + unit_id,
                               state="done", clock=lambda: NOW + 1)
            active.discard(unit_id)
        self.assertEqual(active, set())

        # Capacity now drops to one. D and E are both ready (their
        # dependencies A and B are done); only one may be admitted at a
        # time even though both are eligible.
        capacity = 1
        ready = ready_units(GRAPH, done={"A", "B", "C"})
        self.assertEqual(ready, ["D", "E"])
        admitted = []
        for unit_id in ready:
            if len(active) >= capacity:
                continue  # refused admission by the capacity budget itself
            claim, problem = self._claim(unit_id, "worker-" + unit_id, NOW + 2)
            self.assertIsNotNone(claim, problem)
            active.add(unit_id)
            admitted.append(unit_id)
        self.assertEqual(admitted, ["D"], "capacity=1 must admit exactly "
                         "one of the two eligible units, never both")
        self.assertLessEqual(len(active), capacity)
        FaultCoverage.cover(10, "a capacity budget of 1 admitted exactly "
                            "one of two simultaneously-ready units, "
                            "verified against real claim_store exclusivity")


# ------------------------------------------------------------------ fault 11

class RedDecisionBlockedCStillFinishes(FaultLabTestCase):
    """FAULT 11, and PROPERTY P3. Unit F's integration would require an
    action fable_authority.classify() reads as RED; orchestrator_boundary
    refuses it and queues it for the founder. Unit C, independent of F in
    the fixture graph, must still reach DONE: a RED that halts unrelated
    work is a worse outcome than the RED itself, per the WBS row for this
    unit."""

    def test_f11_red_decision_blocked_c_still_finishes(self):
        scope = "graph-run"
        lease = authority.acquire(self.authority_store, self.run_id, scope,
                                  "primary", "primary-a", 300, now=NOW)

        red_action = {"name": "DISPATCH",
                     "text": "dispatch F: publish the integration report to "
                             "the public repository"}
        with self.assertRaises(boundary.BoundaryRefused) as ctx:
            boundary.authorize(red_action, store_path=self.authority_store,
                               run_id=self.run_id, scope=scope,
                               instance="primary-a", epoch=lease.epoch,
                               now=NOW + 1, red_queue_path=self.red_queue_path)
        EXCEPTIONS_SEEN.add("BoundaryRefused")
        self.assertEqual(ctx.exception.invariant, "no RED action automated")

        # The refusal must actually be queued for the founder, not merely
        # raised and forgotten.
        self.assertTrue(os.path.exists(self.red_queue_path))
        with open(self.red_queue_path, encoding="utf-8") as fh:
            lines = [json.loads(line) for line in fh if line.strip()]
        self.assertEqual(len(lines), 1)
        self.assertIn("public repository", lines[0]["decision"])

        # P3: this refusal is scoped to F alone. C, an unrelated action
        # (no publication, no RED signal), must be authorized and complete.
        c_action = {"name": "DISPATCH", "text": "dispatch C: run the unit tests"}
        c_grant = boundary.authorize(c_action, store_path=self.authority_store,
                                     run_id=self.run_id, scope=scope,
                                     instance="primary-a", epoch=lease.epoch,
                                     now=NOW + 2, red_queue_path=self.red_queue_path)
        self.assertIsNotNone(c_grant)
        claim, problem = claim_store.acquire(
            self.claim_path, "C", "worker-c", work_id=self.run_id, ttl=100,
            clock=lambda: NOW + 2)
        self.assertIsNotNone(claim, problem)
        released, problem = claim_store.release(
            self.claim_path, "C", "worker-c", state="done",
            clock=lambda: NOW + 3)
        self.assertIsNotNone(released, problem)
        self.assertEqual(released["state"], "done")
        FaultCoverage.cover(11, "a RED-classified action for F was refused "
                            "and queued via fable_authority.queue_red(); C "
                            "was independently authorized, claimed and "
                            "released done (P3)")


# ------------------------------------------------------------------ fault 12

class ProvisionalRulingWithoutTheOwner(FaultLabTestCase):
    """FAULT 12. fable_authority.classify() reads an AMBER decision (wide,
    reversible, not RED); record_amber() writes the provisional ruling,
    made by an orchestrator session rather than the founder, to a real
    file this test reads back."""

    def test_f12_provisional_ruling_without_the_owner(self):
        decision_text = "migrate the shared fixture-graph schema to add a node"
        label, reason = fable_authority.classify(decision_text)
        self.assertEqual(label, fable_authority.AMBER)

        entry = fable_authority.record_amber(
            decision_text, reason, "a few minutes of rework if this is wrong",
            "proceed now; revisit if the founder objects at morning review",
            session="orchestrator-secondary", path=self.amber_log_path)
        self.assertIsNotNone(entry, "an overrule sentence was supplied; "
                             "record_amber() must not refuse it")
        self.assertEqual(entry["status"], "PROVISIONAL-FABLE")
        self.assertEqual(entry["session"], "orchestrator-secondary")

        with open(self.amber_log_path, encoding="utf-8") as fh:
            lines = [json.loads(line) for line in fh if line.strip()]
        self.assertEqual(len(lines), 1)
        self.assertEqual(lines[0]["decision"], decision_text)

        # A record with no overrule sentence is refused outright: the
        # sentence is the entire point of the record.
        refused = fable_authority.record_amber(
            decision_text, reason, "cost", "   ",
            session="orchestrator-secondary", path=self.amber_log_path)
        self.assertIsNone(refused)
        FaultCoverage.cover(12, "fable_authority.record_amber() persisted "
                            "a provisional ruling made by an orchestrator "
                            "session, not the founder, with its overrule "
                            "sentence intact")


# ------------------------------------------------------------------ fault 13

class CrossReviewGoesStale(FaultLabTestCase):
    """FAULT 13. A cross-family review is requested and recorded at one
    revision; a new commit lands; gate() must read the review as stale
    (NO-DATA), never as the PASS it used to be, exactly the freshness
    property orchestrator_cross_review.py exists to enforce."""

    def test_f13_cross_review_goes_stale(self):
        task = {"id": "F", "risk_class": "critical"}
        self.assertTrue(cross_review.required_for(task))

        request = cross_review.request(
            task, author_family="sonnet", available_families=["sonnet", "opus"],
            revision="rev-100")
        self.assertEqual(request.reviewer_family, "opus")

        review = cross_review.record(request, "PASS", revision="rev-100")
        fresh_decision = cross_review.gate(
            task, review, current_revision="rev-100",
            obligation="REQUIRED_FOR_MERGE")
        self.assertTrue(fresh_decision.may_proceed)
        self.assertTrue(fresh_decision.fresh)
        self.assertFalse(fresh_decision.uncertain)

        # A new commit lands after the review; the revision moves on.
        stale_decision = cross_review.gate(
            task, review, current_revision="rev-101",
            obligation="REQUIRED_FOR_MERGE")
        self.assertFalse(stale_decision.may_proceed, "a stale PASS must "
                         "never act like a PASS")
        self.assertFalse(stale_decision.fresh)
        self.assertTrue(stale_decision.uncertain)
        self.assertEqual(stale_decision.verdict, "NO-DATA")

        # Rule 2: a reviewer pool of only the author's own family is
        # refused outright, never silently falls back to self-review.
        with self.assertRaises(cross_review.CrossReviewRefused):
            cross_review.request(task, author_family="sonnet",
                                 available_families=["sonnet"], revision="rev-100")
        EXCEPTIONS_SEEN.add("CrossReviewRefused")
        FaultCoverage.cover(13, "orchestrator_cross_review.gate() read a "
                            "review recorded at rev-100 as NO-DATA against "
                            "current_revision rev-101, and may_proceed "
                            "went from True to False")


# ------------------------------------------------------------------ fault 14

class HardStopDuringRepair(FaultLabTestCase):
    """FAULT 14. A repair unit is mid-flight when the drain window opens.
    admits() must refuse it outright (risk, not duration); when the hard
    stop actually lands, drain() must park it with a concrete next action,
    never leave it ambiguous."""

    def test_f14_hard_stop_during_repair(self):
        drain_start = NOW
        stop_at = NOW + 100
        self.assertEqual(hard_stop.phase(NOW - 1, drain_start=drain_start,
                                         hard_stop=stop_at), "OPEN")
        self.assertEqual(hard_stop.phase(NOW, drain_start=drain_start,
                                         hard_stop=stop_at), "DRAINING")
        self.assertEqual(hard_stop.phase(stop_at, drain_start=drain_start,
                                         hard_stop=stop_at), "STOPPED")

        repair_unit = {"id": "R1", "task_class": "repair", "state": "RUNNING",
                       "ready": False, "attempt_count": 1}
        admitted, reason = hard_stop.admits(repair_unit, "DRAINING",
                                            estimated_cost="SHORT")
        self.assertFalse(admitted, "a repair is refused during DRAINING "
                         "regardless of its declared cost")
        self.assertIn("risky", reason)

        # A genuinely short, non-risky unit IS admitted during DRAINING.
        verify_unit_admit, _ = hard_stop.admits(
            {"id": "Cver", "task_class": "verification"}, "DRAINING",
            estimated_cost="SHORT")
        self.assertTrue(verify_unit_admit)

        def integrate(unit):
            if unit["id"] == "R1":
                raise AssertionError(
                    "R1 is not ready; drain() must never call integrate() "
                    "for a unit it has not proven ready")
            return None

        parked = {}

        def park(unit, record):
            parked[unit["id"]] = record

        units = [repair_unit,
                {"id": "Cver", "state": "RUNNING", "task_class": "verification",
                 "ready": True}]
        result = hard_stop.drain(units, phase="STOPPED", integrate=integrate,
                                 park=park, now=stop_at)
        self.assertEqual(result.parked, ["R1"])
        self.assertEqual(result.integrated, ["Cver"])
        self.assertIn("attempt 2", parked["R1"]["next_action"])

        # drain() while phase is still OPEN is a caller misuse, refused.
        with self.assertRaises(hard_stop.HardStopError):
            hard_stop.drain(units, phase="OPEN", integrate=integrate,
                            park=park, now=NOW - 1)
        EXCEPTIONS_SEEN.add("HardStopError")

        # P5's own building block: after drain, nothing is ambiguous.
        hard_stop.assert_no_ambiguous_state(result.units)
        FaultCoverage.cover(14, "orchestrator_hard_stop refused to admit a "
                            "repair during DRAINING and parked it, with a "
                            "concrete next_action, when the hard stop hit "
                            "mid-repair; assert_no_ambiguous_state() passed")


# --------------------------------------------------------------- property p1

class PropertyP1NoDoubleDispatch(FaultLabTestCase):
    """P1, run a second time independently of test_f08 (unittest gives no
    ordering guarantee, and each property deserves its own, self-contained
    proof): across acquire / expire / takeover / stale-wakeup, and across
    a genuine attempted double-claim through claim_store, the counter of
    moments two actors both held authority for one unit stays at zero."""

    def test_p1_no_double_dispatch_ever(self):
        scope = "graph-run"
        counter = DoubleDispatchCounter()
        authority.acquire(self.authority_store, self.run_id, scope,
                          "primary", "primary-a", 30, now=NOW)
        counter.observe(NOW + 1, self.authority_store, self.run_id, scope,
                        [("primary-a", 1)])
        # Restarted under the SAME instance name (the pattern this module's
        # own docstring names as the real incident): only the epoch changes.
        authority.takeover(self.authority_store, self.run_id, scope,
                           "primary", "primary-a", 30, "expired",
                           now=NOW + 40)
        counter.observe(NOW + 41, self.authority_store, self.run_id, scope,
                        [("primary-a", 1), ("primary-a", 2)])
        self.assertEqual(counter.events, [])

        # claim_store's own exclusivity, exercised directly: two owners
        # cannot both hold the same live unit.
        claim_a, _ = claim_store.acquire(self.claim_path, "F", "owner-x",
                                         work_id=self.run_id, ttl=100,
                                         clock=lambda: NOW)
        self.assertIsNotNone(claim_a)
        claim_b, problem_b = claim_store.acquire(self.claim_path, "F", "owner-y",
                                                 work_id=self.run_id, ttl=100,
                                                 clock=lambda: NOW + 1)
        self.assertIsNone(claim_b, "a live claim must never be handed to a "
                          "second owner")
        self.assertIn("owner-x", problem_b)
        FaultCoverage.cover("P1", "zero double-dispatch events observed "
                            "across an authority takeover and a real "
                            "attempted double-claim through claim_store")


# --------------------------------------------------------------- property p2

class PropertyP2CanonicalStaysValid(FaultLabTestCase):
    """P2, exercised as its own scenario (independent of fault 7's): a
    clean run down the whole fixture graph, checked for dependency
    consistency after every single landing, then one regression and its
    rollback, checked again."""

    def test_p2_canonical_stays_valid(self):
        canonical = {}

        def integrate_ok(unit):
            canonical[unit["id"]] = "rev-%s" % unit["id"].lower()
            return None

        done = set()
        for unit_id in ("A", "B", "C", "D", "E"):
            ready_now = ready_units(GRAPH, done)
            self.assertIn(unit_id, ready_now, "the fixture graph's own "
                          "dependency order must make %r ready before it "
                          "is landed" % unit_id)
            result = hard_stop.drain(
                [{"id": unit_id, "state": "INTEGRATING", "task_class": "implementation",
                  "ready": True}],
                phase="DRAINING", integrate=integrate_ok, park=lambda u, r: None,
                now=NOW)
            self.assertEqual(result.integrated, [unit_id])
            done.add(unit_id)
            assert_canonical_consistent(self, canonical, GRAPH)

        # F regresses and rolls back; canonical must stay consistent with
        # F simply absent (nothing in this graph depends on F).
        def integrate_f_fails(unit):
            raise RuntimeError("regression")

        parked = {}
        result_f = hard_stop.drain(
            [{"id": "F", "state": "INTEGRATING", "task_class": "implementation",
              "ready": True}],
            phase="DRAINING", integrate=integrate_f_fails,
            park=lambda u, r: parked.__setitem__(u["id"], r), now=NOW)
        self.assertEqual(result_f.parked, ["F"])
        self.assertNotIn("F", canonical)
        assert_canonical_consistent(self, canonical, GRAPH)
        self.assertEqual(set(canonical), {"A", "B", "C", "D", "E"})
        FaultCoverage.cover("P2", "canonical stayed dependency-consistent "
                            "after every one of five landings and after "
                            "F's rollback")


# --------------------------------------------------------------- property p5

def _minimal_run_state(units, evidence_checks=None, provisional_decisions=None,
                       red_queue=None, awaiting_human=None,
                       final_required_gate_verdict="PASS", hard_stop_reached=True):
    return {
        "run_id": "run-fixture", "goal": "land the whole-night fixture graph",
        "base_revision": "base-000", "final_revision": "base-000",
        "hard_stop_reached": hard_stop_reached,
        "final_required_gate_verdict": final_required_gate_verdict,
        "units_planned": len(units), "units": units,
        "throughput": {"successful_integrations": 0, "repair_attempts": 0,
                      "replans": 0, "handoffs": 0, "provider_fallbacks": 0,
                      "cross_reviews": 0},
        "evidence_checks": evidence_checks or [],
        "provisional_decisions": provisional_decisions or [],
        "red_queue": red_queue or [],
        "awaiting_human": awaiting_human or [],
    }


class PropertyP5HandoffExplainsEverything(FaultLabTestCase):
    """P5, driven through the REAL orchestrator_closeout.close_out() over a
    run_state built to reflect the whole night's outcome: five units DONE,
    F parked on a canonical regression, a repair unit parked at hard stop,
    a required NO-DATA check (fault 5), a queued RED item (fault 11) and a
    provisional ruling (fault 12). Every non-DONE unit must carry a
    next_action, the overall verdict must read RED (the required NO-DATA
    check alone is enough to force that), and both artifacts must actually
    land on disk."""

    def test_p5_handoff_explains_every_unfinished_unit(self):
        units = [
            {"id": "A", "state": "DONE", "task_class": "implementation"},
            {"id": "B", "state": "DONE", "task_class": "implementation"},
            {"id": "C", "state": "DONE", "task_class": "implementation"},
            {"id": "D", "state": "DONE", "task_class": "implementation"},
            {"id": "E", "state": "DONE", "task_class": "implementation"},
            {"id": "F", "state": "PARKED", "task_class": "implementation",
             "next_action": "resume: re-attempt integration on the next run, "
                            "the canonical regression check must pass first",
             "failure_detail": "canonical regression on post-integration check",
             "blocking_reason": "regression"},
            {"id": "R1", "state": "PARKED", "task_class": "repair",
             "next_action": "resume: attempt the repair again on the next "
                            "run, as attempt 2",
             "blocking_reason": "hard stop arrived mid-repair"},
        ]
        evidence_checks = [
            {"name": "unit-A-tests", "verdict": "PASS", "obligation": "REQUIRED_FOR_MERGE"},
            {"name": "unit-F-canonical-verify", "verdict": "NO-DATA",
             "obligation": "REQUIRED_FOR_MERGE"},
        ]
        provisional_decisions = [
            {"decision": "migrate the shared fixture-graph schema to add a node",
             "made_by": "orchestrator-secondary",
             "flip_condition": "founder objects at morning review"},
        ]
        red_queue = [
            {"id": "F-publish-attempt",
             "description": "publish the integration report to the public "
                            "repository, classified RED and never executed"},
        ]
        run_state = _minimal_run_state(
            units, evidence_checks=evidence_checks,
            provisional_decisions=provisional_decisions, red_queue=red_queue)

        out_dir = os.path.join(self.tmp, "handoff")
        result = closeout.close_out(run_state, out_dir=out_dir, now="2026-09-18T00:00:00Z")

        self.assertEqual(result.overall_verdict, "RED",
                         "a required NO-DATA check alone must turn the "
                         "whole page RED")
        self.assertEqual(result.anomalies, [])
        problem_ids = {p["id"] for p in result.final_state["problems"]}
        self.assertEqual(problem_ids, {"F", "R1"})
        for problem in result.final_state["problems"]:
            self.assertTrue(problem["next_action"],
                            "every unfinished unit must carry a next_action")
            self.assertIn(problem["state"], ("PARKED", "EXHAUSTED", "AWAITING-HUMAN"))

        self.assertTrue(os.path.isfile(result.morning_handoff_path))
        self.assertTrue(os.path.isfile(result.final_state_path))
        with open(result.morning_handoff_path, encoding="utf-8") as fh:
            handoff_text = fh.read()
        self.assertIn("F", handoff_text)
        self.assertIn("R1", handoff_text)
        self.assertIn("resume:", handoff_text)
        FaultCoverage.cover("P5", "close_out() produced MORNING-HANDOFF.md "
                            "and final-state.json naming both unfinished "
                            "units with a next_action, overall verdict RED")


# ------------------------------------------------------------ the defence

class RealModulesActuallyRaised(unittest.TestCase):
    """THE DEFENCE NAMED IN THE WORKER BRIEF. A fault lab wired to stubs
    that never reach the real modules under test would make every property
    above hold trivially, forever, with nothing actually exercised. This
    test provokes six DISTINCT real exception types, each defined by a
    different module under test, through that module's own real refusal
    path (never raised by hand), entirely within this one test method:
    unittest gives no ordering guarantee across TestCase classes, so this
    is self-contained rather than trusting EXCEPTIONS_SEEN populated by
    tests that may or may not have run first. EXCEPTIONS_SEEN is still
    updated here, and cross-checked against every other test's own
    additions where this method DOES happen to run last, as a second,
    non-load-bearing signal, never the load-bearing one.
    """

    def test_real_modules_actually_raised_real_exceptions(self):
        tmp = tempfile.mkdtemp(prefix="orch15-defence-")

        # authority.AuthorityRefused: a live lease may never be re-acquired.
        store = os.path.join(tmp, "authority.json")
        authority.acquire(store, "r", "s", "primary", "inst-a", 30, now=NOW)
        with self.assertRaises(authority.AuthorityRefused):
            authority.acquire(store, "r", "s", "primary", "inst-a", 30, now=NOW + 1)
        EXCEPTIONS_SEEN.add("AuthorityRefused")

        # boundary.BoundaryRefused: a stale epoch is refused an authorization.
        authority.takeover(store, "r", "s", "primary", "inst-b", 30,
                           "reclaim", now=NOW + 40)
        with self.assertRaises(boundary.BoundaryRefused):
            boundary.authorize("DISPATCH", store_path=store, run_id="r", scope="s",
                               instance="inst-a", epoch=1, now=NOW + 41)
        EXCEPTIONS_SEEN.add("BoundaryRefused")

        # supervisor.SupervisorRefused: a restart may never touch a live
        # lease this supervisor does not own.
        with self.assertRaises(supervisor.SupervisorRefused):
            supervisor.attempt_restart(
                store, "r", "s", "someone-else", "execution", 30,
                "wrong instance", NOW + 41, lambda cfg, lease: 1, {"scope": "s"})
        EXCEPTIONS_SEEN.add("SupervisorRefused")

        # closeout.CloseoutError: a malformed run_state is refused, not
        # silently rendered.
        with self.assertRaises(closeout.CloseoutError):
            closeout.close_out(
                _minimal_run_state([{"id": "X", "state": "NOT-A-REAL-STATE",
                                    "task_class": "implementation"}]),
                out_dir=os.path.join(tmp, "handoff"))
        EXCEPTIONS_SEEN.add("CloseoutError")

        # hard_stop.HardStopError: drain() called while phase is OPEN.
        with self.assertRaises(hard_stop.HardStopError):
            hard_stop.drain([{"id": "u", "state": "RUNNING", "ready": False}],
                            phase="OPEN", integrate=lambda u: None,
                            park=lambda u, r: None, now=NOW)
        EXCEPTIONS_SEEN.add("HardStopError")

        # cross_review.CrossReviewRefused: no reviewer family but the
        # author's own is available.
        with self.assertRaises(cross_review.CrossReviewRefused):
            cross_review.request({"id": "F"}, author_family="sonnet",
                                 available_families=["sonnet"], revision="rev-1")
        EXCEPTIONS_SEEN.add("CrossReviewRefused")

        required = {"AuthorityRefused", "BoundaryRefused", "SupervisorRefused",
                   "CloseoutError", "HardStopError", "CrossReviewRefused"}
        self.assertEqual(required - EXCEPTIONS_SEEN, set())


if __name__ == "__main__":
    unittest.main(verbosity=2)

#!/usr/bin/env python3
"""ORCH-10: rolling live execution, proven through brother_run.run_rolling(),
the new composition path this unit adds, rather than through
loop_bridge.rolling_run() directly (test_spine.py already exercises that
lower seam on its own).

WHAT THIS PROVES, and why a wave scheduler cannot pass it. The ordinary
drain (run_loop(), driven from main()'s round loop in brother_run.py)
computes ONE batch, dispatches every member of it, and only computes the
next batch once every member of THIS one has finished: a unit whose only
blocker sits inside that same batch waits for every unrelated sibling in it
to finish first, whether or not it ever touches their files. That is a
wave. run_rolling(), added alongside this test, composes
loop_bridge.rolling_run() instead: the scheduler re-plans the instant any
ONE live unit completes, so a dependent starts the moment its dependency
lands, beside unrelated siblings still running. Four properties, each
proven on OBSERVED STATE (file markers this test alone controls, and an
in-process trace of the real scheduler and dispatcher), never on wall
clock, because a slow, loaded machine and a broken scheduler look identical
to a clock:

  1. IMMEDIATE REFILL: D (depends on A) starts while B and C, independent
     siblings admitted alongside A in its own first batch, are still
     running.
  2. A CONFLICT STILL BLOCKS: a dependent whose declared paths overlap a
     still-live sibling is deferred, even once its one dependency is met
     and a slot is free.
  3. A RED DEPENDENCY STILL BLOCKS: a dependent never starts when its
     dependency's own check never went green.
  4. NOTHING OUTSIDE THE ADMITTED SET IS EVER DISPATCHED: every unit this
     run actually starts had already appeared in some graph_loop.plan()
     batch, strictly before it started.

Plus: graph_loop.machine_capacity(), stubbed here rather than read from
this host, is what run_rolling() falls back to when no explicit slot count
is given, so resource pressure lowers how many units run at once.

THE BAD STATE A GREEN RUN WOULD ALSO PASS, guarded against below: fixture
workers so fast that everything finishes before anything else starts. If
that were how this test were built, A, B, C, D would each run to
completion in whatever order the thread pool happened to schedule them,
the ordering assertions would hold trivially (there being only ever one or
zero things live to order at any moment the test happened to look), and a
FIXED-WAVE scheduler (dispatch a batch, wait for every member of it, only
then compute the next) would produce that exact same trace, because with
instant workers a wave and a rolling front are indistinguishable. So B and
C (and, in the capacity tests, P, Q and R) block on a file marker this
test alone creates: they provably cannot reach their own done_check until
this test writes their ".release" file, which forces a real, unavoidable
overlap between D's start and their own still-pending finish.

VERIFIED AGAINST A FIXED-WAVE IMPLEMENTATION, by hand, once, per the
worker contract's rule 5 ("write the test that catches it"): a copy of
this repository's scripts/ directory under /tmp had run_rolling() edited
to call loop_bridge.run() in a plain round loop (claim the ready batch,
wait for every member, recompute, repeat) instead of
loop_bridge.rolling_run(). Against that copy,
ImmediateRefillAndAdmission.test_dependent_starts_while_unrelated_siblings_still_run
went RED: D never started before B.finished and C.finished existed,
because the wave waited for the whole first batch (A, B, C) before
computing the second. Restoring the real, unedited run_rolling() (this
file's own subject) turned it back to PASS. See this unit's return message
for the exact commands and output.
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
if HERE not in sys.path:
    sys.path.insert(0, HERE)

try:
    import tmp_sandbox
    tmp_sandbox.install()
except ImportError:
    # A packager can copy this test without scripts/tmp_sandbox.py beside
    # it (test_spine.py notes the same). Say so rather than dying: the
    # sandbox is hygiene, not the subject under test.
    sys.stderr.write("tmp_sandbox absent: %s leaves its temp trees behind\n"
                     % os.path.basename(__file__))

import brother_run as BR  # noqa: E402
import graph_loop  # noqa: E402
import loop_bridge  # noqa: E402


def sh(args, cwd=None):
    return subprocess.run(args, cwd=cwd, capture_output=True, text=True,
                          timeout=300)


#: One worker script, shared by every fixture below. It is driven entirely
#: by file markers a test writes under $MARK_DIR: ".block" makes it wait for
#: its own ".release" before doing anything else (bounded at 30s so a test
#: bug hangs instead of wedging the suite forever), ".fail" makes its
#: done_check never go green. Mirrors test_spine.py's own worker.sh, which
#: this estate already trusts for the same class of proof.
WORKER_SCRIPT = (
    '#!/bin/sh\n'
    'brief=$(cat)\n'
    'unit=$(printf \'%s\' "$brief" | python3 -c '
    '"import json,sys; print(json.load(sys.stdin).get(\'unit_id\',\'\'))" '
    '2>/dev/null)\n'
    'touch "$MARK_DIR/$unit.started"\n'
    'if [ -f "$MARK_DIR/$unit.block" ]; then\n'
    '  i=0\n'
    '  while [ ! -f "$MARK_DIR/$unit.release" ]; do\n'
    '    sleep 0.05\n'
    '    i=$((i+1))\n'
    '    if [ "$i" -gt 600 ]; then break; fi\n'
    '  done\n'
    'fi\n'
    'if [ -f "$MARK_DIR/$unit.fail" ]; then\n'
    '  touch "$MARK_DIR/$unit.finished"\n'
    '  exit 0\n'
    'fi\n'
    'echo "made by $unit" > "$unit.out"\n'
    'git add -A && git commit -qm "work for $unit"\n'
    'touch "$MARK_DIR/$unit.finished"\n')


def _wait_for(path, timeout=30.0):
    deadline = time.time() + timeout
    while time.time() < deadline:
        if os.path.exists(path):
            return True
        time.sleep(0.05)
    return False


def _wait_for_true(predicate, timeout=30.0):
    deadline = time.time() + timeout
    while time.time() < deadline:
        if predicate():
            return True
        time.sleep(0.05)
    return False


class _Tracer(object):
    """Records what the REAL scheduler and dispatcher did, read afterward
    instead of raced against a clock. Patches three module-level functions
    for the lifetime of one run: graph_loop.plan (every batch and every
    deferral it ever computed, with its reason), loop_bridge.run_node
    (every unit actually dispatched, its start and its finish), and
    loop_bridge.integrate_mod.integrate_one (the moment a unit's lane
    actually merged into canonical, distinct from its worker merely
    returning). All three are looked up BY NAME from inside their own
    module's global namespace at call time, so patching the module
    attribute here is exactly what a real caller inside that module sees
    too; graph_loop.py and loop_bridge.py are not edited by this."""

    def __init__(self):
        self.events = []
        self._lock = threading.Lock()
        self._orig_plan = None
        self._orig_run_node = None
        self._orig_integrate_one = None

    def _append(self, event):
        with self._lock:
            self.events.append(event)

    def install(self):
        self._orig_plan = graph_loop.plan
        self._orig_run_node = loop_bridge.run_node
        self._orig_integrate_one = (
            loop_bridge.integrate_mod.integrate_one
            if loop_bridge.integrate_mod is not None else None)

        def traced_plan(doc, slots=None, also_in_flight=None):
            p = self._orig_plan(doc, slots=slots, also_in_flight=also_in_flight)
            self._append(("batch", frozenset(n["id"] for n in p["batch"])))
            for n, why in p["deferred"]:
                self._append(("deferred", n["id"], why))
            return p

        def traced_run_node(node, parts, worker, cwd=None, max_attempts=3):
            self._append(("start", node["id"]))
            result = self._orig_run_node(node, parts, worker, cwd,
                                         max_attempts)
            self._append(("finish", node["id"], result.get("verdict")))
            return result

        graph_loop.plan = traced_plan
        loop_bridge.run_node = traced_run_node
        if self._orig_integrate_one is not None:
            def traced_integrate_one(cwd, branch, node, **kw):
                result = self._orig_integrate_one(cwd, branch, node, **kw)
                self._append(("integrated", node["id"],
                             result.get("verdict")))
                return result
            loop_bridge.integrate_mod.integrate_one = traced_integrate_one

    def uninstall(self):
        graph_loop.plan = self._orig_plan
        loop_bridge.run_node = self._orig_run_node
        if self._orig_integrate_one is not None:
            loop_bridge.integrate_mod.integrate_one = self._orig_integrate_one

    def index_of(self, kind, uid):
        for i, e in enumerate(self.events):
            if e[0] == kind and e[1] == uid:
                return i
        return None

    def deferred_reasons(self, uid):
        return [e[2] for e in self.events
                if e[0] == "deferred" and e[1] == uid]

    def started_ids(self):
        return {e[1] for e in self.events if e[0] == "start"}

    def admitted_before(self, uid, start_index):
        """True when `uid` appeared in some batch recorded strictly before
        the given start event's own index, i.e. it was never dispatched
        without first having been admitted."""
        for e in self.events[:start_index]:
            if e[0] == "batch" and uid in e[1]:
                return True
        return False


class _RollingFixture(unittest.TestCase):
    """Shared plumbing for every property below: a real git repository, the
    real spawn/verify/repair adapter (loop_bridge.load_parts(), the same
    sibling-tools adapter the ordinary path uses, never a stand-in), and a
    worker driven entirely by file markers, never a sleep whose length this
    test would have to guess."""

    def setUp(self):
        self.repo = tempfile.mkdtemp(prefix="rolling-")
        for a in (["init", "-q", "-b", "main"],
                  ["config", "user.email", "a@b.c"],
                  ["config", "user.name", "t"]):
            sh(["git"] + a, self.repo)
        with open(os.path.join(self.repo, "base.txt"), "w",
                 encoding="utf-8") as fh:
            fh.write("base\n")
        sh(["git", "add", "-A"], self.repo)
        sh(["git", "commit", "-q", "-m", "R0"], self.repo)

        self.marks = tempfile.mkdtemp(prefix="marks-")
        self.claims = os.path.join(tempfile.mkdtemp(), "claims.json")
        self.plan_path = os.path.join(tempfile.mkdtemp(), "plan.json")

        self.worker_script = os.path.join(tempfile.mkdtemp(), "worker.sh")
        with open(self.worker_script, "w", encoding="utf-8") as fh:
            fh.write(WORKER_SCRIPT)
        os.chmod(self.worker_script, 0o755)

        self.parts, problem = loop_bridge.load_parts()
        self.assertIsNotNone(self.parts, problem)

        self.tracer = _Tracer()
        self.tracer.install()
        self.addCleanup(self.tracer.uninstall)

    def _mark(self, name):
        return os.path.join(self.marks, name)

    def _block(self, unit_id):
        open(self._mark("%s.block" % unit_id), "w", encoding="utf-8").close()

    def _fail(self, unit_id):
        open(self._mark("%s.fail" % unit_id), "w", encoding="utf-8").close()

    def _release(self, unit_id):
        open(self._mark("%s.release" % unit_id), "w",
            encoding="utf-8").close()

    def _write_doc(self, rows):
        with open(self.plan_path, "w", encoding="utf-8") as fh:
            json.dump({"rows": rows}, fh)

    def _worker(self):
        environ = dict(os.environ, MARK_DIR=self.marks)
        return loop_bridge.LaneWorker(self.parts["spawn"],
                                      ["sh", self.worker_script],
                                      environ=environ)

    def _run_async(self, slots, max_attempts=3):
        outcome = {}

        def _go():
            outcome["result"] = BR.run_rolling(
                self.plan_path, self.claims, self.repo, slots,
                owner="rolling-test", max_attempts=max_attempts,
                parts=self.parts, worker=self._worker())

        thread = threading.Thread(target=_go)
        thread.start()
        return thread, outcome


class ImmediateRefillAndAdmission(_RollingFixture):
    """Property 1 (immediate refill) and property 4 (nothing outside the
    admitted set is ever dispatched), on one fixture: A, B and C are
    independent and write-disjoint, so a cap of 3 admits all three
    together in the first batch; D depends on A alone and owns a fourth,
    unrelated path, so once A lands D has nothing left to wait on, and the
    only question is whether the scheduler offers it before B and C, its
    unrelated batch-mates, are done."""

    def setUp(self):
        super().setUp()
        self._write_doc([
            {"id": "A", "depends_on": [], "owns": ["A.out"],
             "done_check": "test -f A.out", "in_ship_v1": True},
            {"id": "B", "depends_on": [], "owns": ["B.out"],
             "done_check": "test -f B.out", "in_ship_v1": True},
            {"id": "C", "depends_on": [], "owns": ["C.out"],
             "done_check": "test -f C.out", "in_ship_v1": True},
            {"id": "D", "depends_on": ["A"], "owns": ["D.out"],
             "done_check": "test -f D.out", "in_ship_v1": True},
        ])
        self._block("B")
        self._block("C")

    def test_dependent_starts_while_unrelated_siblings_still_run(self):
        thread, outcome = self._run_async(slots=3)

        self.assertTrue(_wait_for(self._mark("B.started")),
                        "B's worker never started at all")
        self.assertTrue(_wait_for(self._mark("C.started")),
                        "C's worker never started at all")
        # THE PROOF, read off the trace, never off the clock: D must appear
        # as a "start" event while B and C are still blocked on their own
        # release markers, which only this test can create.
        self.assertTrue(
            _wait_for_true(lambda: self.tracer.index_of("start", "D")
                          is not None, timeout=30.0),
            "D never started while its unrelated siblings B and C were "
            "still running: the scheduler is waving, not rolling")
        self.assertFalse(os.path.exists(self._mark("B.finished")),
                         "B had already finished by the time D started, so "
                         "this proves nothing about rolling admission")
        self.assertFalse(os.path.exists(self._mark("C.finished")),
                         "C had already finished by the time D started, so "
                         "this proves nothing about rolling admission")

        # THE ORDER, read off the trace: D's start comes after A's own
        # INTEGRATION (its lane actually merged), not merely after A's
        # worker process returned.
        idx_integrated_a = self.tracer.index_of("integrated", "A")
        idx_start_d = self.tracer.index_of("start", "D")
        self.assertIsNotNone(idx_integrated_a, "A never integrated: %s"
                             % self.tracer.events)
        self.assertLess(idx_integrated_a, idx_start_d,
                        "D started before A's own lane integrated: %s"
                        % self.tracer.events)

        self._release("B")
        self._release("C")
        thread.join(timeout=60)
        self.assertIn("result", outcome, "run_rolling never returned")
        self.assertFalse(thread.is_alive())

        for fname in ("A.out", "B.out", "C.out", "D.out"):
            self.assertTrue(os.path.exists(os.path.join(self.repo, fname)),
                            fname)

        # PROPERTY 4: every unit actually dispatched had already appeared
        # in some graph_loop.plan() batch, strictly before its own start.
        started = self.tracer.started_ids()
        for uid in started:
            idx = self.tracer.index_of("start", uid)
            self.assertTrue(
                self.tracer.admitted_before(uid, idx),
                "%s was dispatched without ever appearing in a "
                "graph_loop.plan() batch first: %s"
                % (uid, self.tracer.events))
        self.assertEqual(started, {"A", "B", "C", "D"})


class ConflictStillBlocks(_RollingFixture):
    """Property 2: D depends on A (satisfied once A integrates) but owns
    the SAME path as B, which is still live when A lands. The write-set
    conflict rule must still refuse D a slot, exactly as it would refuse
    two nodes inside one static batch, even though D's one dependency is
    met and a slot is free. This is the rolling-specific seam
    (graph_loop.plan()'s also_in_flight parameter): only a rolling re-plan
    can even OFFER D while B is live, since a wave scheduler would not
    compute a fresh batch until the whole first one, B included, had
    finished."""

    def setUp(self):
        super().setUp()
        self._write_doc([
            {"id": "A", "depends_on": [], "owns": ["A.out"],
             "done_check": "test -f A.out", "in_ship_v1": True},
            # B and D each write and declare their OWN output file (so the
            # scope audit, which checks what was actually written against
            # what was declared, never quarantines either of them); the
            # extra shared "CONFLICT.out" entry each declares but never
            # writes is what makes them collide for graph_loop.conflicts(),
            # which reasons about DECLARED paths, not actual writes.
            {"id": "B", "depends_on": [], "owns": ["B.out", "CONFLICT.out"],
             "done_check": "test -f B.out", "in_ship_v1": True},
            {"id": "D", "depends_on": ["A"], "owns": ["D.out", "CONFLICT.out"],
             "done_check": "test -f D.out", "in_ship_v1": True},
        ])
        self._block("B")

    def test_conflicting_dependent_waits_for_the_live_sibling(self):
        thread, outcome = self._run_async(slots=2)

        self.assertTrue(_wait_for(self._mark("B.started")),
                        "B's worker never started at all")
        self.assertTrue(
            _wait_for_true(lambda: self.tracer.index_of("integrated", "A")
                          is not None, timeout=30.0),
            "A never integrated: %s" % self.tracer.events)

        # THE PROOF: with A integrated, B still live and D's owns
        # overlapping B's, D must be DEFERRED for a write-set conflict, not
        # started, however many re-plans go by while B holds "CONFLICT.out".
        self.assertTrue(
            _wait_for_true(
                lambda: any("overlaps" in r for r in
                           self.tracer.deferred_reasons("D")),
                timeout=10.0),
            "D was never deferred for a write-set conflict even though "
            "its own path overlaps B, which is still live: %s"
            % self.tracer.events)
        self.assertIsNone(self.tracer.index_of("start", "D"),
                          "D started while its write set still overlapped "
                          "B, a live sibling: the conflict rule was not "
                          "honoured on a rolling re-plan")

        self._release("B")
        thread.join(timeout=60)
        self.assertIn("result", outcome, "run_rolling never returned")

        # Once B is gone the conflict is gone with it, and D is free to
        # run: this is what proves D was genuinely BLOCKED by the conflict
        # rather than dropped by some unrelated defect.
        self.assertIsNotNone(self.tracer.index_of("start", "D"),
                             "D never ran even after its conflicting "
                             "sibling B finished: %s" % self.tracer.events)
        self.assertTrue(os.path.exists(os.path.join(self.repo, "D.out")))


class RedDependencyStillBlocks(_RollingFixture):
    """Property 3: A's own worker never makes its done_check go green (it
    is told to fail outright), so A never integrates; D depends on A alone
    and must never start, however long the run drains for."""

    def setUp(self):
        super().setUp()
        self._write_doc([
            {"id": "A", "depends_on": [], "owns": ["A.out"],
             "done_check": "test -f A.out", "in_ship_v1": True},
            {"id": "D", "depends_on": ["A"], "owns": ["D.out"],
             "done_check": "test -f D.out", "in_ship_v1": True},
        ])
        self._fail("A")

    def test_dependent_never_starts_behind_a_red_dependency(self):
        thread, outcome = self._run_async(slots=2, max_attempts=1)
        thread.join(timeout=60)
        self.assertIn("result", outcome, "run_rolling never returned")
        self.assertFalse(thread.is_alive())

        self.assertIsNotNone(self.tracer.index_of("start", "A"),
                             "A itself never even ran: %s"
                             % self.tracer.events)
        self.assertIsNone(self.tracer.index_of("integrated", "A"),
                          "A integrated despite its own check never going "
                          "green: %s" % self.tracer.events)
        self.assertIsNone(self.tracer.index_of("start", "D"),
                          "D started behind a dependency (A) whose own "
                          "check never passed: %s" % self.tracer.events)
        self.assertFalse(os.path.exists(os.path.join(self.repo, "D.out")))


class ResourcePressureLowersCapacity(_RollingFixture):
    """Plus: graph_loop.machine_capacity() is what run_rolling() falls back
    to for its cap when no explicit slot count is given, so it is stubbed
    here rather than read from this host: a stubbed pressure reading of 1
    must cap concurrency at 1 on this fixture; a stubbed roomy reading must
    allow more than 1, on the SAME fixture. The two are compared against
    EACH OTHER, never against a specific figure this machine happens to
    produce."""

    def setUp(self):
        super().setUp()
        self._write_doc([
            {"id": "P", "depends_on": [], "owns": ["P.out"],
             "done_check": "test -f P.out", "in_ship_v1": True},
            {"id": "Q", "depends_on": [], "owns": ["Q.out"],
             "done_check": "test -f Q.out", "in_ship_v1": True},
            {"id": "R", "depends_on": [], "owns": ["R.out"],
             "done_check": "test -f R.out", "in_ship_v1": True},
        ])
        self._orig_capacity = graph_loop.machine_capacity
        self.addCleanup(setattr, graph_loop, "machine_capacity",
                        self._orig_capacity)

    def _stub_capacity(self, slots):
        graph_loop.machine_capacity = lambda: (slots, ["stub: %d" % slots])

    def test_pressure_admits_exactly_one_at_a_time(self):
        self._stub_capacity(1)
        self._block("P")
        thread, outcome = self._run_async(slots=None)

        self.assertTrue(_wait_for(self._mark("P.started")),
                        "P's worker never started at all")
        # DETERMINISTIC, not a race: rolling_dispatch's own admission loop
        # (`while len(live) < cap`) fills up to cap BEFORE any worker can
        # possibly finish. Q and R are not themselves blocked, but with cap
        # 1 they cannot even be OFFERED a slot until the one live unit
        # frees it, which nothing does until this test releases P. So
        # exactly one start event can exist at this point, not merely
        # "probably one".
        self.assertEqual(self.tracer.started_ids(), {"P"},
                         "more than one unit ran at once under a stubbed "
                         "capacity of 1: %s" % self.tracer.started_ids())

        self._release("P")
        thread.join(timeout=60)
        self.assertIn("result", outcome, "run_rolling never returned")
        self.assertEqual(self.tracer.started_ids(), {"P", "Q", "R"})
        self.assertEqual(outcome["result"].get("in_flight_cap"), 1)

    def test_no_pressure_allows_more_than_one_at_a_time(self):
        self._stub_capacity(3)
        self._block("P")
        self._block("Q")
        self._block("R")
        thread, outcome = self._run_async(slots=None)

        self.assertTrue(
            _wait_for_true(
                lambda: self.tracer.started_ids() == {"P", "Q", "R"},
                timeout=30.0),
            "not every independent, conflict-free unit started even with "
            "a stubbed capacity of 3: %s" % self.tracer.started_ids())
        # THE COMPARISON this property asks for: strictly more concurrent
        # admission than the pressure scenario's 1, never a specific figure
        # this host happens to produce.
        self.assertGreater(len(self.tracer.started_ids()), 1)

        for uid in ("P", "Q", "R"):
            self._release(uid)
        thread.join(timeout=60)
        self.assertIn("result", outcome, "run_rolling never returned")


if __name__ == "__main__":
    unittest.main()

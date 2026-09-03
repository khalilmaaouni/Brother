#!/usr/bin/env python3
"""Regression tests for tools/bm_controller.py: the U2 durable Full-Auto
controller ENGINE (design docs/superpowers/specs/2026-08-05-l03-
controller-design.md), and, in the CLI section near the end of this file
(Writer B, same loop), the main()/cli() dispatch appended to the same
module.

THE ENGINE TESTS (everything above the CLI section marker) drive the
ControllerEngine class DIRECTLY, in-process, against a throwaway tools/
bm_store.py Store under a fresh tempfile.TemporaryDirectory() root, never
against this repo's own store or vault, and import NO subprocess: every
worker and every done-check/verifier/rollback command is answered by
FakeWorker and FakeCheckRunner below, so every one of those tests is fully
deterministic with no model and no network (design section 4:
"test_bm_controller.py itself imports NO subprocess and stays hermetic,
preserving the repo's test-file no-subprocess rule"). Untouched by this
loop's own change.

THE CLI SECTION drives tools/bm_controller.py as a REAL SUBPROCESS
instead, the same discipline tools/test_bm_autonomy.py already uses for
tools/bm_autonomy.py: this file's own no-subprocess claim above is scoped
to the engine tests, not the whole file, and tools/test_bm.py's own no-
subprocess scan already exempts every test_*.py file by name (it skips
any filename starting with "test_"), so importing subprocess here is the
documented exemption in use, never a bypass of it.

"Kill" is simulated by discarding the ControllerEngine object and
constructing a fresh one against the SAME store file (a true resume from
persisted state, no in-memory carryover: the class docstring in
tools/bm_controller.py states this is the whole point).

Python 3.9, standard library only. No network. The CLI section spawns
only local subprocesses of this repository's own tools, never a network
call.

No em or en dashes anywhere in this file, its comments, or its output.
"""

import ast
import datetime
import importlib.util
import io
import json
import os
import re
import subprocess
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))


def _load(name):
    """Load a sibling module by PATH, the same technique tools/
    bm_autonomy.py and tools/bm_controller.py itself use for tools/
    bm_store.py."""
    spec = importlib.util.spec_from_file_location(
        name, os.path.join(HERE, name + ".py"))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


bs = _load("bm_store")
bc = _load("bm_controller")

CONTROLLER_FILE = os.path.join(HERE, "bm_controller.py")


# ---------------------------------------------------------------------------
# Structural guard: bm_controller.py issues NO SQL of its own (its own
# module docstring states the same D-1 flip condition tools/bm_project.py's
# and tools/bm_autonomy.py's do). Copied structurally from tools/
# test_bm_autonomy.py's TestNoSQLGuard, itself copied from tools/
# test_bm_project.py's, including the calibration test that proves the AST
# approach actually earns its complexity over a naive regex.
# ---------------------------------------------------------------------------

def _docstring_constant_ids(tree):
    """id() of every ast.Constant node ast.get_docstring would return the
    value of: the first statement of a Module/FunctionDef/
    AsyncFunctionDef/ClassDef body, when it is a bare string expression."""
    ids = set()
    doc_holders = (ast.Module, ast.FunctionDef, ast.AsyncFunctionDef,
                  ast.ClassDef)
    for node in ast.walk(tree):
        if isinstance(node, doc_holders) and node.body:
            first = node.body[0]
            if (isinstance(first, ast.Expr)
                    and isinstance(first.value, ast.Constant)
                    and isinstance(first.value.value, str)):
                ids.add(id(first.value))
    return ids


class TestNoSQLGuard(unittest.TestCase):
    """tools/bm_controller.py's own module docstring states the D-1 flip
    condition: the moment this file needs a query the store does not
    already offer, it has become a second writer and must be folded back
    into bm_store.py instead of growing one here."""

    _SQL_RE = re.compile(r"\b(SELECT|INSERT|UPDATE|DELETE|PRAGMA)\b",
                         re.IGNORECASE)

    def test_bm_controller_never_issues_its_own_sql(self):
        with io.open(CONTROLLER_FILE, encoding="utf-8") as fh:
            source = fh.read()
        tree = ast.parse(source, filename="bm_controller.py")
        skip = _docstring_constant_ids(tree)
        hits = []
        for node in ast.walk(tree):
            if (isinstance(node, ast.Constant)
                    and isinstance(node.value, str)
                    and id(node) not in skip
                    and self._SQL_RE.search(node.value)):
                hits.append((node.lineno, node.value))
        self.assertEqual(
            hits, [],
            "bm_controller.py must never contain a string literal shaped "
            "like SQL (found %r); a query the store does not already "
            "offer means this file has become a second writer and the "
            "query belongs in bm_store.py instead." % hits)

    def test_bm_controller_never_imports_sqlite3(self):
        with io.open(CONTROLLER_FILE, encoding="utf-8") as fh:
            source = fh.read()
        tree = ast.parse(source, filename="bm_controller.py")
        offenders = []
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                offenders.extend(a.name for a in node.names
                                 if a.name.split(".")[0] == "sqlite3")
            elif isinstance(node, ast.ImportFrom):
                if (node.module or "").split(".")[0] == "sqlite3":
                    offenders.append(node.module)
        self.assertEqual(
            offenders, [],
            "bm_controller.py must never import sqlite3 directly (found "
            "%r); every query it needs already goes through bs, the "
            "loaded bm_store module." % offenders)

    def test_calibrated_old_guard_missed_a_lowercase_or_split_bypass(self):
        """CALIBRATION. Reproduces the pre-fix guard tools/test_bm_project
        .py itself replaced (a bare uppercase-only regex over raw text)
        against a synthetic snippet, proving it would have said nothing
        was wrong, then proves the current AST-based guard catches both
        bypasses in the same snippet."""
        bypass_snippet = (
            'query = "sel" "ect * from controller_runs"\n'
            'other = "update controller_units set status=?"\n'
        )
        old_style_hits = re.findall(
            r"\b(SELECT|INSERT|UPDATE|DELETE)\b", bypass_snippet)
        self.assertEqual(
            old_style_hits, [],
            "calibration sanity: the OLD guard must find nothing in this "
            "snippet, or it does not actually demonstrate the bypass")
        tree = ast.parse(bypass_snippet)
        hits = [n.value for n in ast.walk(tree)
               if isinstance(n, ast.Constant) and isinstance(n.value, str)
               and self._SQL_RE.search(n.value)]
        self.assertEqual(
            len(hits), 2,
            "the AST-based guard must catch both the split literal and "
            "the lowercase keyword the old regex missed (got %r)" % hits)

    def test_the_only_subprocess_call_site_is_subprocesscheckrunner(self):
        """design section 4: SubprocessCheckRunner.run is one of exactly
        two places in this file that touch `subprocess`. The other,
        _default_fence_canary_runner (added 2026-08-10, the unattended
        preflight's fence canary), is a module-level function whose argv
        is a fixed literal this module writes, never founder- or
        model-authored text; see _EXEC_PRIMITIVE_HOMES and
        TestR6TheExecutionPrimitiveGuardIsStructural below for the full
        structural guard this duplicates in a simpler, older shape. Every
        OTHER call site, whether inside ControllerEngine or any other
        function, is still an offence."""
        with io.open(CONTROLLER_FILE, encoding="utf-8") as fh:
            source = fh.read()
        tree = ast.parse(source, filename="bm_controller.py")
        offenders = []

        class _Visitor(ast.NodeVisitor):
            def __init__(self):
                self.stack = []

            def visit_ClassDef(self, node):
                self.stack.append(node.name)
                self.generic_visit(node)
                self.stack.pop()

            def visit_FunctionDef(self, node):
                self.stack.append(node.name)
                self.generic_visit(node)
                self.stack.pop()

            visit_AsyncFunctionDef = visit_FunctionDef

            def visit_Attribute(self, node):
                if (node.attr in ("run", "call", "Popen", "check_output")
                        and isinstance(node.value, ast.Name)
                        and node.value.id == "subprocess"):
                    enclosing = ".".join(self.stack) if self.stack else "<module>"
                    if enclosing not in _EXEC_PRIMITIVE_HOMES:
                        offenders.append((node.lineno, enclosing))
                self.generic_visit(node)

        _Visitor().visit(tree)
        self.assertEqual(offenders, [])


# ---------------------------------------------------------------------------
# The harness seam's fakes (design section 4).
# ---------------------------------------------------------------------------

class FakeWorker(bc.WorkerAdapter):
    """Constructed with a dict unit_id -> WorkerResult, OR unit_id ->
    callable(attempt_number) -> WorkerResult for a unit whose behaviour
    changes attempt over attempt (a scripted retry, an outage that
    recovers, a hang). Records every call for assertions."""

    def __init__(self, scripts):
        self.scripts = scripts
        self.calls = {}
        self.call_log = []

    def run(self, brief):
        unit_id = brief["unit_id"]
        n = self.calls.get(unit_id, 0) + 1
        self.calls[unit_id] = n
        self.call_log.append((unit_id, n))
        script = self.scripts[unit_id]
        if callable(script):
            return script(n, brief)
        return script


class FakeCheckRunner(bc.CheckRunner):
    """Constructed with a dict command -> CheckOutcome, OR command ->
    callable(call_number) -> CheckOutcome for a command whose result
    changes call over call (the circuit-breaker retry-then-pass
    scenarios). Unlisted commands default to a clean pass, so a test only
    needs to script the commands it cares about."""

    def __init__(self, outcomes=None):
        self.outcomes = outcomes or {}
        self.calls = []
        self._counts = {}

    def run(self, command, cwd):
        self.calls.append(command)
        self._counts[command] = self._counts.get(command, 0) + 1
        if command not in self.outcomes:
            return {"exit_code": 0, "stdout": "", "stderr": ""}
        script = self.outcomes[command]
        if callable(script):
            return script(self._counts[command])
        return script


# ---------------------------------------------------------------------------
# Fixtures.
# ---------------------------------------------------------------------------

def _actor(name="controller"):
    return {"actor_type": "model", "actor_name": name}


def _project(pid="p1"):
    return {"project_id": pid, "name": "Project", "created_at":
            "2026-08-05T00:00:00Z", "updated_at": "2026-08-05T00:00:00Z"}


def _seed(store, pid="p1", actor=None):
    store.upsert_project(_project(pid), actor or _actor())
    return pid


def _sign(store, project_id="p1", risk_classes=None, token_ceiling=None,
          minutes_ceiling=None, actor=None, allowed_paths=None):
    return store.sign_contract(
        project_id, "ship the widget", "the done-definition passes",
        allowed_paths or ["."],
        [], risk_classes or ["file-create", "file-edit", "file-move",
                             "build", "test-run", "local-commit",
                             "read-only-inspect"],
        token_ceiling, minutes_ceiling, "Khalil Maaouni", "sess1",
        actor or _actor(), supersede=False)


def _unit(unit_id, dependencies=None, write_scope=None, read_scope=None,
          risk_class="file-create", lane="default", role="builder",
          done_check="true", verifier="", **kw):
    d = {"unit_id": unit_id, "objective": "unit %s" % unit_id,
         "dependencies": dependencies or [], "read_scope": read_scope or [],
         "write_scope": write_scope or [], "role": role,
         "risk_class": risk_class, "lane": lane, "done_check": done_check,
         "done_check_expect_exit": 0, "verifier": verifier}
    d.update(kw)
    return d


def _worker_result(claim="done", artifacts=None, tokens=1, minutes=1,
                   status="returned"):
    return {"worker_claim": claim, "artifacts": artifacts or [],
            "cost": {"tokens": tokens, "minutes": minutes},
            "status": status}


def _engine(store, worker, checker, controller_id="ctrl1", actor=None,
           **kw):
    return bc.ControllerEngine(store, worker, checker, controller_id,
                               actor or _actor(), **kw)


def _begin_and_plan(engine, store, project_id, units, outcome="ship it",
                    done_definition="echo done", workflow_version=1):
    run = engine.begin(project_id, outcome, done_definition,
                       workflow_version=workflow_version)
    engine.plan(project_id, run["run_id"], units)
    return run


def _dispatch_count(store, unit_id):
    return len(store.list_dispatches(unit_id, raw=True))


def _unit_row(store, run_id, unit_id):
    for u in store.list_units(run_id, raw=True):
        if u["unit_id"] == unit_id:
            return u
    return None


# ---------------------------------------------------------------------------
# The ten fault tests (design section 5), each proving its invariant.
# ---------------------------------------------------------------------------

class TestFault1KilledBetweenResultAndCommit(unittest.TestCase):
    """FakeWorker returns; record_result commits RESULT_IN; the engine is
    discarded BEFORE verification runs. A fresh engine against the same
    store resumes. Invariant: exactly-once recording of the accepted
    result; at-most-once dispatch."""

    def test_resume_verifies_without_re_dispatch_and_reaches_done_exactly_once(self):
        with tempfile.TemporaryDirectory() as d:
            with bs.Store(d) as store:
                _seed(store)
                _sign(store)
                checker = FakeCheckRunner()
                engine1 = _engine(store, FakeWorker({}), checker)
                run = _begin_and_plan(
                    engine1, store, "p1", [_unit("u1", write_scope=["a.py"])])

                # Manually replicate step()'s own sequence up through
                # record_result, then "crash": discard engine1 before
                # verification (step 12) ever runs.
                ready = store.select_ready_units(run["run_id"])
                u = ready[0]
                verdict = store.gate_check(
                    "p1", u["risk_class"], path=u["write_scope"][0])
                store.set_run_state(run["run_id"], "EXECUTING", _actor(),
                                    "dispatching", "sess1")
                rec = store.claim(
                    name="unit-u1", lifetime="ephemeral",
                    files=u["write_scope"], objective=u["objective"],
                    owner="ctrl1", session_id=engine1.session_id)
                store.claim_unit("u1", rec.lifecycle_uuid, _actor())
                dispatch_id = store.record_dispatch(
                    "u1", 1, verdict["revision"], rec.lifecycle_uuid,
                    engine1.session_id, _actor())
                store.record_result(dispatch_id, "created a.py", ["a.py"],
                                   _actor())
                self.assertEqual(
                    _unit_row(store, run["run_id"], "u1")["status"],
                    "RESULT_IN")
                self.assertEqual(_dispatch_count(store, "u1"), 1)

                # A fresh engine, same store: true resume, no in-memory
                # carryover. The SAME driver restarted, so the same
                # session id: since L09 GAP 2 a run has one driver, and a
                # genuinely new session would adopt first
                # (TestDriverAdoption).
                worker2 = FakeWorker({})  # must NOT be called
                engine2 = _engine(store, worker2, checker,
                                  session_id=engine1.session_id)
                summary = engine2.step("p1")

                self.assertEqual(worker2.calls, {},
                                 "resume must go straight to verification, "
                                 "never re-invoke the worker")
                self.assertEqual(_dispatch_count(store, "u1"), 1,
                                 "at-most-once dispatch: no second "
                                 "controller_dispatches row for attempt 1")
                self.assertEqual(
                    _unit_row(store, run["run_id"], "u1")["status"], "DONE")
                self.assertEqual(summary["completed"], ["u1"])
                # Exactly one DONE transition (mark_unit_done refuses a
                # second accept for a non-RESULT_IN/VERIFYING unit).
                with self.assertRaises(bs.OwnershipRefused):
                    store.mark_unit_done("u1", "some-checkpoint", _actor())


class TestFault2DuplicateResult(unittest.TestCase):
    """FakeWorker (or a caller) emits two record-result calls for one
    dispatch. Invariant: at-most-once result recording."""

    def test_second_receive_result_call_refuses_already_resulted(self):
        with tempfile.TemporaryDirectory() as d:
            with bs.Store(d) as store:
                _seed(store)
                _sign(store)
                worker = FakeWorker({"u1": _worker_result()})
                checker = FakeCheckRunner()
                engine = _engine(store, worker, checker)
                run = _begin_and_plan(
                    engine, store, "p1", [_unit("u1", write_scope=["a.py"])])
                engine.step("p1")
                self.assertEqual(
                    _unit_row(store, run["run_id"], "u1")["status"], "DONE")
                dispatch_id = store.list_dispatches("u1", raw=True)[0][
                    "dispatch_id"]

                with self.assertRaises(bs.OwnershipRefused) as ctx:
                    engine.receive_result("p1", dispatch_id, "done again",
                                          [], cost={"tokens": 1, "minutes": 1})
                self.assertEqual(ctx.exception.reason, "already-resulted")
                self.assertEqual(_dispatch_count(store, "u1"), 1)
                # Exactly one DONE, never two.
                self.assertEqual(
                    _unit_row(store, run["run_id"], "u1")["status"], "DONE")


class TestFault3DependencyChangedOutputInvalidatesEvidence(unittest.TestCase):
    """U_down verifies green against U_up's H1 artifact; U_up is then
    re-run (a re-plan with a changed definition) and its output becomes
    H2. Invariant: a final edit invalidates prior evidence."""

    def test_replanning_the_upstream_unit_re_queues_the_downstream_one(self):
        with tempfile.TemporaryDirectory() as d:
            with bs.Store(d) as store:
                _seed(store)
                _sign(store)
                worker = FakeWorker({
                    "up": _worker_result(claim="v1", artifacts=["a.py"]),
                    "down": _worker_result(claim="consumed v1",
                                           artifacts=["b.py"]),
                })
                checker = FakeCheckRunner()
                engine = _engine(store, worker, checker)
                run = _begin_and_plan(engine, store, "p1", [
                    _unit("up", write_scope=["a.py"]),
                    _unit("down", dependencies=["up"], read_scope=["a.py"],
                         write_scope=["b.py"]),
                ])
                engine.step("p1")  # dispatches "up"
                self.assertEqual(
                    _unit_row(store, run["run_id"], "up")["status"], "DONE")
                engine.step("p1")  # dispatches "down"
                self.assertEqual(
                    _unit_row(store, run["run_id"], "down")["status"],
                    "DONE")
                down_checkpoint_v1 = _unit_row(
                    store, run["run_id"], "down")["checkpoint_ref"]
                self.assertIsNotNone(down_checkpoint_v1)

                # Re-plan "up" with a CHANGED definition (H1 -> H2): this
                # is what a redesign/re-run of an upstream unit looks like
                # at the store layer (store-level cascade is proven in
                # tools/test_bm_store.py; this test proves the DOWNSTREAM
                # consequence an engine-driven run actually sees).
                store.upsert_units(
                    run["run_id"],
                    [_unit("up", write_scope=["a.py"],
                          objective="a DIFFERENT a.py")],
                    _actor())

                up_row = _unit_row(store, run["run_id"], "up")
                down_row = _unit_row(store, run["run_id"], "down")
                self.assertEqual(up_row["status"], "READY",
                                 "the redefined upstream unit re-queues")
                self.assertIn(
                    down_row["status"], ("PENDING", "READY"),
                    "the downstream unit's prior H1-based evidence is "
                    "invalidated: it is no longer DONE")
                self.assertIsNone(
                    down_row["checkpoint_ref"],
                    "the downstream unit's stale checkpoint_ref is cleared, "
                    "not left pointing at evidence that no longer holds")

                # Re-run to a new green: the final done-definition run
                # (step 19) reflects H2, not H1.
                worker.scripts["up"] = _worker_result(
                    claim="v2", artifacts=["a.py"])
                trace = engine.run_to_completion("p1")
                self.assertEqual(trace[-1]["state"], "DELIVERABLE_READY")
                self.assertEqual(
                    _unit_row(store, run["run_id"], "down")["status"],
                    "DONE")
                self.assertNotEqual(
                    _unit_row(store, run["run_id"], "down")["checkpoint_ref"],
                    down_checkpoint_v1,
                    "the downstream unit's NEW green checkpoint reflects "
                    "H2, never reuses H1's stale checkpoint id")


class TestFault4WorkerHangs(unittest.TestCase):
    """A unit is dispatched and the worker never synchronously answers
    (an injected clock simulates the deadline passing, since a REAL
    block would hang the test). Invariant: a hang is a recoverable fault,
    not a stall, and independent lanes keep progressing."""

    def test_an_abandoned_dispatch_re_queues_for_one_retry_and_other_lanes_progress(self):
        with tempfile.TemporaryDirectory() as d:
            with bs.Store(d) as store:
                _seed(store)
                _sign(store)
                clock = {"now": bc._default_now()}
                worker = FakeWorker({
                    "hung": _worker_result(status="pending"),
                    "docs": _worker_result(claim="wrote docs",
                                           artifacts=["README.md"]),
                })
                checker = FakeCheckRunner()
                engine = _engine(
                    store, worker, checker, dispatch_timeout_seconds=60,
                    now_fn=lambda: clock["now"])
                run = _begin_and_plan(engine, store, "p1", [
                    _unit("hung", write_scope=["a.py"], lane="code"),
                    _unit("docs", write_scope=["README.md"], lane="docs"),
                ])
                summary = engine.step("p1")
                self.assertIn("hung", summary["dispatched"])
                self.assertIn("docs", summary["dispatched"])
                self.assertEqual(
                    _unit_row(store, run["run_id"], "hung")["status"],
                    "DISPATCHED")
                self.assertEqual(
                    _unit_row(store, run["run_id"], "docs")["status"],
                    "DONE", "the independent docs lane finished while "
                            "'hung' is still in flight")

                # Advance the injected clock past the deadline: the
                # dispatch is now abandoned.
                clock["now"] = clock["now"] + datetime.timedelta(seconds=120)
                abandoned = engine.check_timeouts("p1")
                self.assertEqual(abandoned, ["hung"])
                hung_row = _unit_row(store, run["run_id"], "hung")
                self.assertEqual(hung_row["status"], "READY",
                                 "one retry: the circuit breaker returns "
                                 "it to READY, not FAILED, on the first "
                                 "abandonment")
                self.assertEqual(hung_row["retry_count"], 1)

                # The retry succeeds.
                worker.scripts["hung"] = _worker_result(
                    claim="finally done", artifacts=["a.py"])
                trace = engine.run_to_completion("p1")
                self.assertEqual(trace[-1]["state"], "DELIVERABLE_READY")
                self.assertEqual(
                    _unit_row(store, run["run_id"], "hung")["status"],
                    "DONE")


class TestFault5MalformedOutput(unittest.TestCase):
    """FakeWorker returns a payload missing 'artifacts'. Invariant:
    untrusted worker output never crashes the controller."""

    def test_malformed_payload_is_a_rejection_not_an_exception(self):
        with tempfile.TemporaryDirectory() as d:
            with bs.Store(d) as store:
                _seed(store)
                _sign(store)
                worker = FakeWorker({
                    "bad": {"status": "malformed"},
                    "good": _worker_result(claim="fine",
                                           artifacts=["ok.py"]),
                })
                checker = FakeCheckRunner()
                engine = _engine(store, worker, checker)
                run = _begin_and_plan(engine, store, "p1", [
                    _unit("bad", write_scope=["a.py"], lane="risky"),
                    _unit("good", write_scope=["ok.py"], lane="safe"),
                ])
                try:
                    trace = engine.run_to_completion("p1")
                except Exception as exc:  # pragma: no cover, the assertion
                    self.fail("malformed worker output crashed the "
                             "controller: %r" % (exc,))
                self.assertEqual(
                    _unit_row(store, run["run_id"], "good")["status"],
                    "DONE", "the run stays green elsewhere")
                bad_row = _unit_row(store, run["run_id"], "bad")
                self.assertIn(bad_row["status"], ("FAILED", "READY"))
                self.assertGreaterEqual(bad_row["retry_count"], 1)


class TestFault6CostCeilingReached(unittest.TestCase):
    """record_spend crosses 100 percent mid-fan-out. Invariant: hard-stop
    starts no new work."""

    def test_hard_stop_drains_and_starts_no_new_unit(self):
        with tempfile.TemporaryDirectory() as d:
            with bs.Store(d) as store:
                _seed(store)
                _sign(store, token_ceiling=10)
                worker = FakeWorker({
                    "u1": _worker_result(claim="x", artifacts=["a.py"],
                                         tokens=100, minutes=1),
                })
                checker = FakeCheckRunner()
                engine = _engine(store, worker, checker)
                # u2 depends on u1 so it is NOT selectable in the same
                # wave as u1: the ceiling must already be blown by the
                # time u2 would ever become a candidate for dispatch.
                run = _begin_and_plan(engine, store, "p1", [
                    _unit("u1", write_scope=["a.py"]),
                    _unit("u2", dependencies=["u1"], read_scope=["a.py"],
                         write_scope=["b.py"]),
                ])
                engine.step("p1")  # u1 dispatches, blows the ceiling
                self.assertEqual(
                    store.spend_totals("p1")["verdict"], "hard-stop")
                alerts_before = store.conn.execute(
                    "SELECT COUNT(*) c FROM alerts WHERE "
                    "category='autonomy-breaker' AND severity='critical'"
                ).fetchone()["c"]
                self.assertEqual(alerts_before, 1,
                                 "one critical breaker alert exists")

                # u1 completes synchronously within the FIRST step() call
                # (FakeWorker returns immediately), which is what records
                # the spend that blows the ceiling; the SECOND step() call
                # is what discovers hard-stop, before ever reaching
                # select_ready_units for u2.
                self.assertEqual(
                    _unit_row(store, run["run_id"], "u1")["status"], "DONE")
                summary = engine.step("p1")
                self.assertEqual(summary["state"], "STOPPED")
                self.assertEqual(summary["dispatched"], [])
                self.assertEqual(
                    _unit_row(store, run["run_id"], "u2")["status"],
                    "PENDING", "u2 was never claimed or dispatched")
                self.assertEqual(worker.calls.get("u2"), None)


class TestFault7FounderCancelsDuringFanOut(unittest.TestCase):
    """Three parallel-fenced units are DISPATCHED; the founder stops the
    contract. Invariant: stop starts no new unit and leaves no orphan
    fence."""

    def test_stop_mid_fan_out_records_returned_results_selects_nothing_new_and_releases_every_fence(self):
        with tempfile.TemporaryDirectory() as d:
            with bs.Store(d) as store:
                _seed(store)
                _sign(store)
                worker = FakeWorker({
                    "u1": _worker_result(status="pending"),
                    "u2": _worker_result(status="pending"),
                    "u3": _worker_result(status="pending"),
                })
                checker = FakeCheckRunner()
                engine = _engine(store, worker, checker)
                run = _begin_and_plan(engine, store, "p1", [
                    _unit("u1", write_scope=["a.py"], lane="l1"),
                    _unit("u2", write_scope=["b.py"], lane="l2"),
                    _unit("u3", write_scope=["c.py"], lane="l3"),
                ])
                summary = engine.step("p1")
                self.assertEqual(sorted(summary["dispatched"]),
                                 ["u1", "u2", "u3"])
                for uid in ("u1", "u2", "u3"):
                    self.assertEqual(
                        _unit_row(store, run["run_id"], uid)["status"],
                        "DISPATCHED")

                store.set_contract_state("p1", "stopped", "khalil",
                                         "founder stop", "sess1", _actor())
                s2 = engine.step("p1")
                self.assertEqual(s2["state"], "STOPPED")
                self.assertEqual(s2["dispatched"], [])

                for uid in ("u1", "u2", "u3"):
                    row = _unit_row(store, run["run_id"], uid)
                    if row["fence_uuid"]:
                        rec = store.get(row["fence_uuid"])
                        self.assertNotEqual(
                            rec.state, "active",
                            "unit %s's fence must be released, not left "
                            "orphaned" % uid)
                run_row = store.get_run("p1", raw=True)
                controller_fence = store.get(run_row["fence_uuid"])
                self.assertNotEqual(
                    controller_fence.state, "active",
                    "the controller's own fence must also be released")


class TestFault8RestartWithNewerWorkflowVersion(unittest.TestCase):
    """A run is persisted at workflow_version=1, then resumed with the
    engine reporting version 2. Invariant: resume does not repeat
    completed work across a version change; a DONE unit whose definition
    still matches is never re-run."""

    def test_reuses_unchanged_units_skips_dropped_ones_never_reruns_done(self):
        with tempfile.TemporaryDirectory() as d:
            with bs.Store(d) as store:
                _seed(store)
                _sign(store)
                worker = FakeWorker({
                    "keep": _worker_result(claim="kept", artifacts=["a.py"]),
                    "done_but_dropped": _worker_result(
                        claim="finished before the redesign",
                        artifacts=["b.py"]),
                })
                checker = FakeCheckRunner()
                engine1 = _engine(store, worker, checker)
                # "still_pending_dropped" has NO worker script: it must
                # never be dispatched in this test, proving SKIPPED (not
                # a crash from an unscripted unit_id) is what removes it.
                run = _begin_and_plan(
                    engine1, store, "p1",
                    [_unit("keep", write_scope=["a.py"]),
                     _unit("done_but_dropped", write_scope=["b.py"]),
                     _unit("still_pending_dropped", write_scope=["c.py"],
                          lane="never-selected"),
                    ], workflow_version=1)
                store.block_lane_units(run["run_id"], "never-selected",
                                       _actor())
                # Round 4 pairing law (DESIGN-round4 8.4): BLOCKED is a
                # materialised view of an open founder step in the lane, so
                # an unpaired block would be reversed by the reconcile on
                # the next wave. Pair it, which is what block_lane_units'
                # own docstring requires of every caller.
                store.queue_human_step(
                    "p1", "", "never-selected",
                    "this lane is held for the founder in this test", "",
                    [], "sess1", _actor())
                engine1.step("p1")  # dispatches "keep" and "done_but_dropped"
                self.assertEqual(
                    _unit_row(store, run["run_id"], "keep")["status"],
                    "DONE")
                self.assertEqual(
                    _unit_row(store, run["run_id"],
                             "done_but_dropped")["status"], "DONE")
                self.assertEqual(
                    _unit_row(store, run["run_id"],
                             "still_pending_dropped")["status"], "BLOCKED")
                keep_checkpoint = _unit_row(
                    store, run["run_id"], "keep")["checkpoint_ref"]

                # Restart: a NEW engine reporting workflow_version=2,
                # re-validates the graph with BOTH "done_but_dropped" and
                # "still_pending_dropped" removed, and "keep" UNCHANGED
                # (reused, never re-run).
                worker2 = FakeWorker({})  # "keep" must never be re-dispatched
                # The SAME driver restarted, so the same session id: since
                # L09 GAP 2 a run has one driver, and a genuinely new
                # session would adopt first (TestDriverAdoption).
                engine2 = _engine(store, worker2, checker,
                                  session_id=engine1.session_id)
                engine2.plan("p1", run["run_id"],
                             [_unit("keep", write_scope=["a.py"])])

                keep_row = _unit_row(store, run["run_id"], "keep")
                done_dropped_row = _unit_row(
                    store, run["run_id"], "done_but_dropped")
                pending_dropped_row = _unit_row(
                    store, run["run_id"], "still_pending_dropped")
                self.assertEqual(keep_row["status"], "DONE",
                                 "unchanged definition: never re-run")
                self.assertEqual(keep_row["checkpoint_ref"], keep_checkpoint)
                self.assertEqual(
                    done_dropped_row["status"], "DONE",
                    "a unit that already completed real work is never "
                    "retroactively marked SKIPPED just because a later "
                    "re-plan no longer lists it; the work still happened")
                self.assertEqual(
                    pending_dropped_row["status"], "SKIPPED",
                    "a unit that never ran is safely dropped from the "
                    "graph when a redesign no longer needs it")
                self.assertNotIn("keep", worker2.calls)

                trace = engine2.run_to_completion("p1")
                self.assertEqual(trace[-1]["state"], "DELIVERABLE_READY")
                self.assertNotIn("keep", worker2.calls,
                                 "resume across a version bump must not "
                                 "repeat completed work")


class TestFault9ProviderOutageThenRecovery(unittest.TestCase):
    """FakeWorker returns 'unavailable' for two calls, then a real
    result. Invariant: a transient boundary fault is recoverable and
    idempotent."""

    def test_dispatch_stays_open_across_the_outage_and_recovers_without_a_duplicate(self):
        with tempfile.TemporaryDirectory() as d:
            with bs.Store(d) as store:
                _seed(store)
                _sign(store)

                def scripted(n, brief):
                    if n <= 2:
                        return {"status": "unavailable"}
                    return _worker_result(claim="recovered",
                                          artifacts=["a.py"])

                worker = FakeWorker({"u1": scripted})
                checker = FakeCheckRunner()
                engine = _engine(store, worker, checker)
                run = _begin_and_plan(
                    engine, store, "p1", [_unit("u1", write_scope=["a.py"])])

                s1 = engine.step("p1")
                # Round 4 law L2 (DESIGN-round4 3.2): the summary reports
                # the state the store holds at return time. An outage moves
                # the run to FAILED_RECOVERABLE in the store; the old
                # EXECUTING here pinned the stale pre-funnel summary, which
                # is SM E in miniature.
                self.assertEqual(s1["state"], "FAILED_RECOVERABLE")
                self.assertEqual(
                    _unit_row(store, run["run_id"], "u1")["status"],
                    "DISPATCHED", "the dispatch stays open, not FAILED, "
                                  "across a provider outage")
                self.assertEqual(_dispatch_count(store, "u1"), 1)

                s2 = engine.step("p1")
                self.assertEqual(s2["state"], "FAILED_RECOVERABLE")
                self.assertEqual(_dispatch_count(store, "u1"), 1,
                                 "still no duplicate dispatch")

                # Resume retries the SAME dispatch idempotently.
                s3 = engine.step("p1")
                self.assertEqual(
                    _unit_row(store, run["run_id"], "u1")["status"], "DONE")
                self.assertEqual(_dispatch_count(store, "u1"), 1,
                                 "recovery reused the ORIGINAL dispatch, "
                                 "never opened a second one")
                self.assertEqual(worker.calls["u1"], 3)


class TestFault10RollbackItselfFails(unittest.TestCase):
    """A unit is rejected; FakeCheckRunner returns exit!=0 for the
    rollback command. Invariant: an unrecoverable dirty state is
    surfaced, never papered over."""

    def test_a_failed_rollback_reaches_failed_terminal_and_queues_a_founder_step(self):
        with tempfile.TemporaryDirectory() as d:
            with bs.Store(d) as store:
                _seed(store)
                _sign(store)
                worker = FakeWorker({
                    "u1": _worker_result(claim="claims done",
                                         artifacts=["a.py"]),
                })
                checker = FakeCheckRunner({
                    "true": {"exit_code": 1, "stdout": "", "stderr": "fail"},
                    "git restore -- a.py": {"exit_code": 1, "stdout": "",
                                            "stderr": "rollback failed"},
                })
                engine = _engine(store, worker, checker)
                run = _begin_and_plan(
                    engine, store, "p1", [_unit("u1", write_scope=["a.py"])])

                engine.step("p1")

                self.assertEqual(
                    store.get_run("p1", raw=True)["state"],
                    "FAILED_TERMINAL")
                steps = store.list_human_steps("p1", resolved=False,
                                               raw=True)
                self.assertEqual(len(steps), 1)
                self.assertIn("rollback", steps[0]["what"].lower())
                # No further unit is dispatched from this run.
                summary = engine.step("p1")
                self.assertEqual(summary["dispatched"], [])
                self.assertEqual(summary["note"], "run is terminal; "
                                                   "nothing to do")


# ---------------------------------------------------------------------------
# Additional required-behaviours the design's section 5 coverage map
# names but that are not one of the ten numbered faults above: the
# self-approve/adversarial probe, the stale-heartbeat adoption test, and
# the revoked-contract-mid-unit staleness re-read.
# ---------------------------------------------------------------------------

class TestExecutorCannotSelfApprove(unittest.TestCase):
    """worker_claim says pass but the controller's OWN done-check says
    fail: the controller must reject, proving the two-seam property
    (executor cannot self-approve, design section 4)."""

    def test_a_worker_that_lies_about_passing_is_caught_by_the_real_done_check(self):
        with tempfile.TemporaryDirectory() as d:
            with bs.Store(d) as store:
                _seed(store)
                _sign(store)
                worker = FakeWorker({
                    "u1": _worker_result(claim="all tests pass, trust me",
                                         artifacts=["a.py"]),
                })
                checker = FakeCheckRunner({
                    "true": {"exit_code": 1, "stdout": "", "stderr": ""},
                })
                engine = _engine(store, worker, checker)
                run = _begin_and_plan(
                    engine, store, "p1", [_unit("u1", write_scope=["a.py"])])
                engine.step("p1")
                row = _unit_row(store, run["run_id"], "u1")
                self.assertNotEqual(
                    row["status"], "DONE",
                    "a worker's claim of success must never substitute "
                    "for the controller's own independent done-check")
                dispatch = store.list_dispatches("u1", raw=True)[0]
                self.assertEqual(dispatch["status"], "REJECTED")
                self.assertEqual(dispatch["done_check_exit"], 1)


class TestRevokedContractMidUnit(unittest.TestCase):
    """design step 13's staleness re-read: the contract is revoked
    BETWEEN dispatch and verification. Invariant: the result is not
    accepted under authorisation that no longer holds; the unit
    re-queues."""

    def test_a_revoke_between_dispatch_and_verification_re_queues_the_unit(self):
        with tempfile.TemporaryDirectory() as d:
            with bs.Store(d) as store:
                _seed(store)
                _sign(store)

                def scripted(n, brief):
                    if n == 1:
                        # Revoke the contract from INSIDE the worker call,
                        # simulating "a revoke lands between check and
                        # act": the dispatch already captured the OLD
                        # revision; by the time record_result -> verify
                        # runs, the live revision has moved.
                        store.set_contract_state(
                            "p1", "revoked", "khalil", "mid-unit revoke",
                            "sess1", _actor())
                    return _worker_result(claim="done", artifacts=["a.py"])

                worker = FakeWorker({"u1": scripted})
                checker = FakeCheckRunner()
                engine = _engine(store, worker, checker)
                run = _begin_and_plan(
                    engine, store, "p1", [_unit("u1", write_scope=["a.py"])])
                engine.step("p1")

                row = _unit_row(store, run["run_id"], "u1")
                self.assertNotEqual(
                    row["status"], "DONE",
                    "a result is never accepted under a contract "
                    "revision that has already moved")
                dispatch = store.list_dispatches("u1", raw=True)[0]
                self.assertEqual(dispatch["status"], "REJECTED")
                self.assertIn("stale", dispatch["verifier_verdict"])


class TestHumanBlockedLaneDoesNotStallIndependentLane(unittest.TestCase):
    """design U1 section 6 point 4: a lane behind a founder-gated step is
    the ONLY thing that blocks; every other lane keeps running."""

    def test_release_lane_blocked_while_docs_lane_completes(self):
        with tempfile.TemporaryDirectory() as d:
            with bs.Store(d) as store:
                _seed(store)
                _sign(store)
                worker = FakeWorker({
                    "docs": _worker_result(claim="wrote docs",
                                           artifacts=["README.md"]),
                })
                checker = FakeCheckRunner()
                engine = _engine(store, worker, checker)
                run = _begin_and_plan(engine, store, "p1", [
                    _unit("release_unit", write_scope=["dist/pkg.tar"],
                         lane="release"),
                    _unit("docs", write_scope=["README.md"], lane="docs"),
                ])
                store.block_lane_units(run["run_id"], "release", _actor())
                store.queue_human_step(
                    "p1", "publish-release", "release",
                    "click publish in the dashboard", "", [], "sess1",
                    _actor())

                trace = engine.run_to_completion("p1")
                self.assertEqual(
                    _unit_row(store, run["run_id"], "docs")["status"],
                    "DONE", "the docs lane completed independently")
                self.assertEqual(
                    _unit_row(store, run["run_id"],
                             "release_unit")["status"], "BLOCKED")
                self.assertNotIn("release_unit", worker.calls)


class TestDuplicateControllerAndStaleHeartbeatAdoption(unittest.TestCase):
    """design section 8: a second controller for the same project hits
    the fence's own name-active refusal for free (no new lock);
    ControllerEngine.begin() adds nothing on top, it simply lets that
    refusal surface. The SEPARATE adoption primitive (transition(...,
    'adopted', adopt_from_live_session=...)) is the store's own, reused
    verbatim; WHEN to use it is a policy decision for whoever calls
    begin() (a founder, or a future CLI), made by comparing
    recent_checkpoints(project_id, limit=1) against a staleness
    threshold. transition() itself does not consult heartbeat freshness
    at all: it only distinguishes "the current holder is the same
    session" from "it is a different one", and for a different one,
    always requires the explicit adopt_from_live_session=True regardless
    of how fresh or stale that holder's last heartbeat was. This class
    tests exactly that store-level gate, not an engine-level auto-
    adoption flow ControllerEngine does not implement."""

    def test_a_second_live_controller_refuses_name_active(self):
        with tempfile.TemporaryDirectory() as d:
            with bs.Store(d) as store:
                _seed(store)
                _sign(store)
                engine1 = _engine(store, FakeWorker({}), FakeCheckRunner(),
                                  controller_id="ctrl1")
                engine1.begin("p1", "ship it", "echo done")

                engine2 = _engine(store, FakeWorker({}), FakeCheckRunner(),
                                  controller_id="ctrl2")
                with self.assertRaises(bs.OwnershipRefused) as ctx:
                    engine2.begin("p1", "ship it", "echo done")
                self.assertEqual(ctx.exception.reason, "name-active")

    def test_a_founder_reading_a_stale_heartbeat_may_adopt_the_dead_controllers_fence(self):
        with tempfile.TemporaryDirectory() as d:
            with bs.Store(d) as store:
                _seed(store)
                _sign(store)
                engine1 = _engine(store, FakeWorker({}), FakeCheckRunner(),
                                  controller_id="ctrl1")
                engine1.begin("p1", "ship it", "echo done")
                # begin()'s own return value is {'run_id', 'state'} only
                # (Store.open_run's documented shape); the fence_uuid
                # lives on the persisted run row.
                fence_uuid = store.get_run("p1", raw=True)["fence_uuid"]
                rec = store.get(fence_uuid)

                # The policy decision a founder (or a future CLI) makes
                # BEFORE calling transition(..., 'adopted', ...): read the
                # most recent heartbeat and compare it against a
                # staleness threshold. No fresh checkpoint has been
                # recorded since begin(), so by any reasonable threshold
                # this controller already reads as silent.
                last_beat = store.recent_checkpoints("p1", limit=1)
                self.assertEqual(len(last_beat), 0,
                                 "begin() itself records no heartbeat; "
                                 "the first one is step()'s own job "
                                 "(design step 7), so a controller that "
                                 "crashed before its first step has NO "
                                 "checkpoint at all, the most silent "
                                 "case there is")

                # A fresh, live session: adoption without the explicit
                # flag is refused regardless of heartbeat staleness,
                # because transition() does not consult heartbeats at
                # all; the flag is what a caller passes AFTER making that
                # judgement itself.
                with self.assertRaises(bs.OwnershipRefused) as ctx:
                    store.transition(fence_uuid, rec.version, "adopted",
                                     session_id="rescuer-session")
                self.assertEqual(ctx.exception.reason,
                                 "live-session-adopt-blocked")

                # With the explicit displace flag, a second controller
                # may adopt it.
                adopted = store.transition(
                    fence_uuid, rec.version, "adopted",
                    session_id="rescuer-session",
                    adopt_from_live_session=True)
                self.assertEqual(adopted.state, "adopted")


# ---------------------------------------------------------------------------
# The four findings an independent security refuter raised against this
# loop (report R-L03-security, 2026-08-05), each reproduced here as the
# scenario that broke before the fix. Three of the four live on the
# PRODUCTION async path (receive_result, which `bm-controller
# record-result` wraps) or in the dispatch gate; the ten fault tests above
# only ever reached those code paths through the SYNCHRONOUS FakeWorker,
# which is exactly the gap the refuter walked through.
# ---------------------------------------------------------------------------

def _park_one_async_unit(test, store, checker, units=None, worker=None,
                         unit_ids=("u1",)):
    """Drive one wave through a worker that always parks (precisely what
    the production RecordIntentWorker does: it prints the brief and
    returns 'pending'), leaving the run in EXECUTING with an open
    dispatch, so the test can deliver the result OUT OF BAND through
    receive_result() the way the CLI does. Returns
    (engine, run, {unit_id: dispatch_id})."""
    units = units or [_unit("u1", write_scope=["a.py"])]
    worker = worker or FakeWorker(
        {u["unit_id"]: _worker_result(status="pending") for u in units})
    engine = _engine(store, worker, checker)
    run = _begin_and_plan(engine, store, "p1", units)
    summary = engine.step("p1")
    test.assertEqual(sorted(summary["dispatched"]), sorted(unit_ids))
    test.assertEqual(store.get_run("p1", raw=True)["state"], "EXECUTING",
                     "the async worker parks the run in EXECUTING, which "
                     "is the state receive_result() is called against")
    dispatch_ids = {uid: store.list_dispatches(uid, raw=True)[0]["dispatch_id"]
                    for uid in unit_ids}
    return engine, run, dispatch_ids


class TestF1AsyncRejectWhoseRollbackAlsoFails(unittest.TestCase):
    """R-L03-security F1 (HIGH, safety). Fault 10 on the PRODUCTION path:
    a result arriving through receive_result() is rejected AND its
    rollback command also fails. The async path must walk the same states
    as the synchronous one (EXECUTING -> VERIFYING the moment the result
    is recorded, design step 10), because FAILED_TERMINAL is legal only
    from VERIFYING or FAILED_RECOVERABLE. Without that walk the store
    refuses the move and the call aborts BEFORE the founder is warned
    about the dirty write scope and BEFORE the fence is released, leaving
    the run EXECUTING, holding a fence, and free to re-dispatch the unit."""

    def test_it_reaches_failed_terminal_warns_the_founder_and_releases_the_fence(self):
        with tempfile.TemporaryDirectory() as d:
            with bs.Store(d) as store:
                _seed(store)
                _sign(store)
                checker = FakeCheckRunner({
                    "true": {"exit_code": 1, "stdout": "", "stderr": "fail"},
                    "git restore -- a.py": {"exit_code": 1, "stdout": "",
                                            "stderr": "rollback failed"},
                })
                engine, run, dispatch_ids = _park_one_async_unit(
                    self, store, checker)
                fence_uuid = _unit_row(store, run["run_id"],
                                       "u1")["fence_uuid"]
                self.assertEqual(store.get(fence_uuid).state, "active")

                outcome = engine.receive_result(
                    "p1", dispatch_ids["u1"], "claims done", ["a.py"],
                    cost={"tokens": 1, "minutes": 1})

                self.assertEqual(outcome, "rejected")
                self.assertEqual(
                    store.get_run("p1", raw=True)["state"], "FAILED_TERMINAL",
                    "a failed rollback is an unrecoverable dirty state and "
                    "must halt the run on the async path exactly as it does "
                    "on the synchronous one")
                steps = store.list_human_steps("p1", resolved=False, raw=True)
                self.assertEqual(len(steps), 1,
                                 "the founder is warned exactly once about "
                                 "the dirty write scope")
                self.assertIn("rollback", steps[0]["what"].lower())
                self.assertIn("dirty", steps[0]["what"].lower())
                self.assertNotEqual(
                    store.get(fence_uuid).state, "active",
                    "the unit's fence is released, never left held by a "
                    "run that has already halted")

                # No further unit is dispatched from this run.
                summary = engine.step("p1")
                self.assertEqual(summary["dispatched"], [])
                self.assertEqual(summary["note"],
                                 "run is terminal; nothing to do")
                self.assertEqual(_dispatch_count(store, "u1"), 1,
                                 "the unit is never re-dispatched after the "
                                 "failed rollback")


class TestF2EveryWriteScopePathIsGateChecked(unittest.TestCase):
    """R-L03-security F2 (HIGH, authorization). The dispatch precondition
    (design line 163) is "gate_check ALLOWED for the chosen unit's
    risk_class AND write_scope", the WHOLE scope. Checking only
    write_scope[0] hands the worker a brief authorising every further
    path in the list, and the fence claim and the brief both carry the
    full list, so a forbidden second path escapes the contract's
    allowed_paths with no after-the-fact net (it is INSIDE the unit's own
    write_scope, so bm_bash_audit sees nothing anomalous)."""

    def test_a_forbidden_second_write_path_refuses_the_whole_unit(self):
        with tempfile.TemporaryDirectory() as d:
            with bs.Store(d) as store:
                _seed(store)
                _sign(store, allowed_paths=["src"])
                worker = FakeWorker({"u1": _worker_result()})
                engine = _engine(store, worker, FakeCheckRunner())
                run = _begin_and_plan(engine, store, "p1", [
                    _unit("u1", write_scope=["src/a.py",
                                             "secrets/prod.env"]),
                ])
                self.assertEqual(
                    store.gate_check("p1", "file-create",
                                     path="secrets/prod.env")["verdict"],
                    "REFUSED-SCOPE",
                    "fixture sanity: the second path is refused on its own")

                summary = engine.step("p1")

                self.assertEqual(summary["dispatched"], [],
                                 "a unit is dispatched only when EVERY path "
                                 "in its write_scope is authorised")
                self.assertEqual(worker.calls, {},
                                 "no brief authorising the forbidden path is "
                                 "ever handed to a worker")
                self.assertEqual(_dispatch_count(store, "u1"), 0)
                row = _unit_row(store, run["run_id"], "u1")
                self.assertEqual(row["status"], "READY",
                                 "the refusal routes through the SAME "
                                 "circuit breaker a single-path refusal "
                                 "uses (mark_unit_failed), not the drain")
                self.assertEqual(row["retry_count"], 1)
                self.assertNotEqual(
                    store.get_run("p1", raw=True)["state"], "STOPPED",
                    "REFUSED-SCOPE is an authorisation gap for THIS unit, "
                    "never a whole-run drain")

                # Second wave: the breaker escalates, and the escalation
                # names the path that earned the refusal.
                engine.step("p1")
                self.assertEqual(
                    _unit_row(store, run["run_id"], "u1")["status"], "FAILED")
                questions = [i["question"] for i in
                             store.list_interruptions("p1", raw=True)]
                self.assertTrue(
                    any("secrets/prod.env" in q for q in questions),
                    "the refusal reason must name the forbidden path, not "
                    "just the unit: %r" % (questions,))

    def test_a_unit_whose_paths_are_all_authorised_still_dispatches(self):
        with tempfile.TemporaryDirectory() as d:
            with bs.Store(d) as store:
                _seed(store)
                _sign(store, allowed_paths=["src"])
                worker = FakeWorker({
                    "u1": _worker_result(claim="wrote both",
                                         artifacts=["src/a.py", "src/b.py"]),
                })
                engine = _engine(store, worker, FakeCheckRunner())
                run = _begin_and_plan(engine, store, "p1", [
                    _unit("u1", write_scope=["src/a.py", "src/b.py"]),
                ])

                summary = engine.step("p1")

                self.assertEqual(summary["dispatched"], ["u1"],
                                 "no regression: a multi-path unit entirely "
                                 "inside allowed_paths still dispatches")
                self.assertEqual(
                    _unit_row(store, run["run_id"], "u1")["status"], "DONE")

    def test_an_empty_write_scope_still_gate_checks_the_risk_class_alone(self):
        with tempfile.TemporaryDirectory() as d:
            with bs.Store(d) as store:
                _seed(store)
                _sign(store, risk_classes=["file-create"])
                worker = FakeWorker({"u1": _worker_result()})
                engine = _engine(store, worker, FakeCheckRunner())
                run = _begin_and_plan(engine, store, "p1", [
                    _unit("u1", write_scope=[], risk_class="local-commit"),
                ])

                summary = engine.step("p1")

                self.assertEqual(
                    summary["dispatched"], [],
                    "a unit with no write path at all is still gate-checked "
                    "on its risk class, so an ungranted class never "
                    "dispatches just because there is no path to check")
                self.assertEqual(worker.calls, {})
                engine.step("p1")
                questions = [i["question"] for i in
                             store.list_interruptions("p1", raw=True)]
                self.assertTrue(
                    any("local-commit" in q for q in questions),
                    "the escalation names the ungranted risk class: %r"
                    % (questions,))


class TestF3AsyncSpendAfterARevoke(unittest.TestCase):
    """R-L03-security F3 (MEDIUM, robustness). A revoke landing between
    dispatch and record-result must SKIP spend bookkeeping, never crash
    the whole result-handling path: record_spend refuses on a non-live
    contract, and the synchronous path already guards for it. The
    REJECTION of the now-stale result still happens where the design puts
    it, at the revision comparison (step 13)."""

    def test_no_exception_escapes_spend_is_skipped_and_the_result_is_rejected_as_stale(self):
        with tempfile.TemporaryDirectory() as d:
            with bs.Store(d) as store:
                _seed(store)
                _sign(store)
                engine, run, dispatch_ids = _park_one_async_unit(
                    self, store, FakeCheckRunner())
                store.set_contract_state("p1", "revoked", "khalil",
                                         "founder revoke mid-unit", "sess1",
                                         _actor())

                try:
                    outcome = engine.receive_result(
                        "p1", dispatch_ids["u1"], "done", ["a.py"],
                        cost={"tokens": 9, "minutes": 2})
                except Exception as exc:  # pragma: no cover, the assertion
                    self.fail("a revoke between dispatch and record-result "
                             "crashed the result-handling path: %r" % (exc,))

                self.assertEqual(outcome, "rejected")
                self.assertEqual(
                    store.spend_totals("p1")["tokens"], 0,
                    "spend bookkeeping is SKIPPED against an authorisation "
                    "that no longer exists, never recorded and never "
                    "crashed")
                dispatch = store.list_dispatches("u1", raw=True)[0]
                self.assertEqual(dispatch["status"], "REJECTED")
                self.assertIn(
                    "stale", dispatch["verifier_verdict"],
                    "the rejection happens at the revision comparison, "
                    "which is where the design puts it")
                row = _unit_row(store, run["run_id"], "u1")
                self.assertEqual(row["status"], "READY")
                self.assertEqual(row["retry_count"], 1)

                # The documented next state: the run drains on the next
                # pass, because the contract is no longer live.
                summary = engine.step("p1")
                self.assertEqual(summary["state"], "STOPPED")
                self.assertEqual(summary["dispatched"], [])


class TestF4DependentOfAFailedUnitDoesNotStallTheRun(unittest.TestCase):
    """R-L03-security F4 (LOW). A unit whose dependency reached FAILED can
    never be selected again, so a run holding one used to sit in
    CHECKPOINTED forever: no delivery, no terminal state, and
    run_to_completion burning every one of its max_steps. The dependent
    unit is instead BLOCKED behind a founder step naming the failed
    dependency, which is the design's own pairing (step 18:
    queue_human_step plus block_lane_units, and WAITING_HUMAN once the
    blocked lanes are the ONLY remaining work)."""

    def test_a_chain_whose_first_unit_exhausts_its_breaker_reaches_waiting_human(self):
        with tempfile.TemporaryDirectory() as d:
            with bs.Store(d) as store:
                _seed(store)
                _sign(store)
                worker = FakeWorker({
                    "u1": _worker_result(claim="claims done",
                                         artifacts=["a.py"]),
                    "u2": _worker_result(claim="never runs", artifacts=[]),
                })
                checker = FakeCheckRunner({
                    "check-u1": {"exit_code": 1, "stdout": "",
                                 "stderr": "u1 never passes"},
                })
                engine = _engine(store, worker, checker)
                run = _begin_and_plan(engine, store, "p1", [
                    _unit("u1", write_scope=["a.py"], done_check="check-u1"),
                    _unit("u2", dependencies=["u1"], read_scope=["a.py"],
                         write_scope=["b.py"], done_check="check-u2"),
                ])

                trace = engine.run_to_completion("p1", max_steps=12)

                self.assertLess(
                    len(trace), 12,
                    "run_to_completion must RETURN, not spin through every "
                    "one of its max_steps with nothing selectable")
                self.assertIn(
                    trace[-1]["state"],
                    frozenset(bc._TERMINAL_STATES | {"WAITING_HUMAN"}),
                    "a run whose remaining work is unreachable resolves to a "
                    "terminal state or WAITING_HUMAN, never rests in "
                    "CHECKPOINTED: %r" % (trace[-1],))
                self.assertEqual(
                    _unit_row(store, run["run_id"], "u1")["status"], "FAILED")
                self.assertEqual(
                    _unit_row(store, run["run_id"], "u2")["status"], "BLOCKED",
                    "the dependent unit is itself marked, not left PENDING "
                    "on a dependency that can never become DONE")
                self.assertEqual(worker.calls.get("u2"), None,
                                 "the dependent unit is never dispatched")
                reasons = [s["what"] for s in
                           store.list_human_steps("p1", resolved=False,
                                                  raw=True)
                           if "u2" in s["what"]]
                self.assertTrue(
                    reasons, "the founder is told WHICH unit cannot run")
                self.assertTrue(
                    any("u1" in r for r in reasons),
                    "the reason names the failed dependency: %r" % (reasons,))

    def test_a_transitively_blocked_unit_is_told_which_unit_actually_failed(self):
        """The unit two hops downstream waits on a unit that is itself
        only BLOCKED, never attempted. Its founder step must name the
        FAILED unit at the end of the chain and must not call the middle
        unit failed, which would be a false statement about work that
        never ran."""
        with tempfile.TemporaryDirectory() as d:
            with bs.Store(d) as store:
                _seed(store)
                _sign(store)
                worker = FakeWorker({
                    "u1": _worker_result(claim="claims done",
                                         artifacts=["a.py"]),
                })
                checker = FakeCheckRunner({
                    "check-u1": {"exit_code": 1, "stdout": "",
                                 "stderr": "u1 never passes"},
                })
                engine = _engine(store, worker, checker)
                run = _begin_and_plan(engine, store, "p1", [
                    _unit("u1", write_scope=["a.py"], done_check="check-u1"),
                    _unit("u2", dependencies=["u1"], write_scope=["b.py"]),
                    _unit("u3", dependencies=["u2"], write_scope=["c.py"]),
                ])

                trace = engine.run_to_completion("p1", max_steps=12)

                self.assertEqual(trace[-1]["state"], "WAITING_HUMAN")
                for uid in ("u2", "u3"):
                    self.assertEqual(
                        _unit_row(store, run["run_id"], uid)["status"],
                        "BLOCKED")
                steps = {}
                for s in store.list_human_steps("p1", resolved=False,
                                                raw=True):
                    for uid in ("u2", "u3"):
                        if s["what"].startswith("unit %s " % uid):
                            steps[uid] = s["what"]
                self.assertEqual(sorted(steps), ["u2", "u3"],
                                 "each unreachable unit gets its own step")
                self.assertIn("u1", steps["u3"],
                              "the two-hop unit is told which unit actually "
                              "FAILED: %r" % (steps["u3"],))
                self.assertNotIn(
                    "unit u2, which reached FAILED", steps["u3"],
                    "u2 was never attempted; calling it failed would be a "
                    "false statement to the founder: %r" % (steps["u3"],))


# ---------------------------------------------------------------------------
# ROUND 3: the reproductions a SECOND adversarial pass raised against the
# F1 to F4 fixes above (report docs/program/absolute-lead/evidence/L03/
# REFUTATION-2-fixes.md, 2026-08-05). Each test below is one of that
# report's own probe sequences, restated as a permanent regression test.
# Every one of them FAILED on the tree that report attacked, which is what
# makes it evidence rather than a restatement of the fix.
# ---------------------------------------------------------------------------

def _move_run_along_legal_edges(store, run, states):
    """Walk the run through `states` using set_run_state, which refuses any
    move CONTROLLER_STATE_TRANSITIONS does not allow. This is how the
    refuter's own state matrix reached each row: the question under test is
    what receive_result does FROM that state, never whether the state was
    reachable by an illegal move."""
    for state in states:
        store.set_run_state(run["run_id"], state, _actor(),
                            "state matrix probe", "sess1")


class _StraddlingStore(object):
    """A delegating Store wrapper that fires ONE concurrent contract amend
    immediately after the Nth gate_check call returns, which is exactly
    what a second process running against the same SQLite file does when
    its `bm-autonomy sign --supersede` lands between two of the per-path
    gate_check calls _gate_check_write_scope makes. Everything except
    gate_check is delegated untouched, so the engine under test is the
    real one against a real store."""

    def __init__(self, store, amend_after):
        self._store = store
        self._amend_after = dict(amend_after)
        self.gate_check_calls = []

    def __getattr__(self, name):
        return getattr(self._store, name)

    def gate_check(self, project_id, action_class, path=None, surface=None):
        verdict = self._store.gate_check(project_id, action_class,
                                         path=path, surface=surface)
        self.gate_check_calls.append((path, verdict["verdict"],
                                      verdict["revision"]))
        amend = self._amend_after.pop(len(self.gate_check_calls), None)
        if amend is not None:
            amend(self._store)
        return verdict


def _amend_allowed_paths(allowed_paths):
    """A concurrent writer's supersede amend, narrowing allowed_paths."""
    def _fire(store):
        store.sign_contract(
            "p1", "ship the widget", "the done-definition passes",
            allowed_paths, [],
            ["file-create", "file-edit", "file-move", "build", "test-run",
             "local-commit", "read-only-inspect"],
            None, None, "Khalil Maaouni", "sess1", _actor(), supersede=True)
    return _fire


class TestR2F1LateResultOnARunStateThatCannotReachVerifying(unittest.TestCase):
    """REFUTATION-2 F1. The F1 fix walks CHECKPOINTED and WAITING_HUMAN to
    EXECUTING and leaves four run states in which the original defect
    reproduces verbatim: STOPPED, STOPPING, PAUSED and DELIVERABLE_READY.
    A result arriving for a run in one of those is a REAL situation (a
    founder stop, a re-plan that delivered), not a state-machine accident,
    so receive_result must handle it explicitly: no OwnershipRefused
    escapes, the founder is warned when the rollback left the write scope
    dirty, the fence is parked, an interruption names the late result, the
    run state is left exactly where it was, and a terminal or delivered
    run never gains newly selectable work."""

    _DIRTY = {"true": {"exit_code": 1, "stdout": "", "stderr": "fail"},
              "git restore -- a.py": {"exit_code": 1, "stdout": "",
                                      "stderr": "rollback failed"}}

    def _assert_late_result_handled(self, store, run, fence_uuid, state):
        self.assertEqual(store.get_run("p1", raw=True)["state"], state,
                         "a late result never moves the run state")
        steps = [s["what"] for s in
                 store.list_human_steps("p1", resolved=False, raw=True)]
        self.assertTrue(
            any("rollback" in w.lower() and "dirty" in w.lower()
                for w in steps),
            "the founder is warned about the dirty write scope even when "
            "no state move is legal: %r" % (steps,))
        self.assertNotEqual(store.get(fence_uuid).state, "active",
                            "the unit fence is parked, never left held")
        row = _unit_row(store, run["run_id"], "u1")
        self.assertNotEqual(
            row["status"], "READY",
            "a run that is %s must never gain newly selectable work" % state)
        self.assertEqual(store.select_ready_units(run["run_id"]), [],
                         "nothing becomes selectable on a %s run" % state)
        questions = [i["question"] for i in
                     store.list_interruptions("p1", raw=True)]
        self.assertTrue(
            any("late result" in q and state in q for q in questions),
            "an interruption names the late result and the run state: %r"
            % (questions,))
        # Added 2026-08-05 (DESIGN-round4 section 18.2). SM D: the founder
        # step used to sit behind `if outcome['status'] != 'FAILED'`, and
        # _record_interruption returns None on a contract that is not live,
        # so a unit at its retry ceiling produced NO founder-visible record
        # at all. An open step naming the unit now exists in EVERY case,
        # below the ceiling and at it.
        self.assertTrue(
            any("u1" in w for w in steps),
            "an open founder step names the unit in every case, not only "
            "below the retry ceiling: %r" % (steps,))

    def test_a_founder_stop_then_a_late_result_whose_rollback_also_fails(self):
        """REFUTATION-2 F1 reproduction 1: three shipped CLI commands, no
        contract move at all, so the staleness re-read cannot absorb it."""
        with tempfile.TemporaryDirectory() as d:
            with bs.Store(d) as store:
                _seed(store)
                _sign(store)
                checker = FakeCheckRunner(self._DIRTY)
                engine, run, dispatch_ids = _park_one_async_unit(
                    self, store, checker)
                fence_uuid = _unit_row(store, run["run_id"],
                                       "u1")["fence_uuid"]
                engine.stop("p1", "founder requested stop")
                self.assertEqual(store.get_run("p1", raw=True)["state"],
                                 "STOPPED")

                outcome = engine.receive_result(
                    "p1", dispatch_ids["u1"], "claims done", ["a.py"],
                    cost={"tokens": 1})

                self.assertEqual(outcome, "rejected")
                self._assert_late_result_handled(store, run, fence_uuid,
                                                 "STOPPED")
                self.assertEqual(
                    store.list_dispatches("u1", raw=True)[0]["status"],
                    "REJECTED", "the late result is recorded and rejected, "
                                "never silently dropped")

    def test_a_clean_rollback_on_a_stopped_run_still_requeues_nothing(self):
        """REFUTATION-2 F1 reproduction 1, secondary observation: with a
        CLEAN rollback the old code returned 'rejected' and re-queued the
        unit to READY on an already terminal run."""
        with tempfile.TemporaryDirectory() as d:
            with bs.Store(d) as store:
                _seed(store)
                _sign(store)
                checker = FakeCheckRunner(
                    {"true": {"exit_code": 1, "stdout": "", "stderr": "no"}})
                engine, run, dispatch_ids = _park_one_async_unit(
                    self, store, checker)
                engine.stop("p1", "founder requested stop")

                outcome = engine.receive_result(
                    "p1", dispatch_ids["u1"], "claims done", ["a.py"])

                self.assertEqual(outcome, "rejected")
                self.assertEqual(store.get_run("p1", raw=True)["state"],
                                 "STOPPED")
                self.assertNotEqual(
                    _unit_row(store, run["run_id"], "u1")["status"], "READY",
                    "a terminal run must not grow new selectable work")
                self.assertEqual(store.select_ready_units(run["run_id"]), [])

    # RETIRED on 2026-08-05 (round 4, orchestrator supersession, same
    # pattern as the PAUSED row above per DESIGN-round4 18.1):
    # test_a_late_result_on_deliverable_ready_never_dispatches_a_second_time
    # asserted that a late result for a re-plan-dropped unit is REJECTED
    # after the fact. Design section 8.1 closes that lifecycle one layer
    # earlier: the drop CANCELS the open dispatch in the same transaction,
    # and record_result refuses 'dispatch-cancelled', so the late answer is
    # never processed at all (LV finding 4: processing it could resurrect
    # the dropped unit to DONE). The property this test protected, a
    # delivered run never re-dispatches the dropped unit, is pinned
    # stronger by TestR3TheSkippedLifecycleClosesAtTheSource.
    # test_a_result_for_a_cancelled_dispatch_refuses_instead_of_reviving_
    # the_unit, which additionally asserts the founder's drop stands and no
    # meter is charged.

    def test_the_three_red_rows_of_the_state_matrix_never_raise(self):
        """REFUTATION-2 F1's state matrix: DELIVERABLE_READY, PAUSED,
        STOPPING and STOPPED all raised OwnershipRefused out of
        receive_result, losing the founder warning and leaving the fence
        ACTIVE. The contract is left alone throughout, so the staleness
        branch cannot mask the state-walk question.

        THE PAUSED ROW MOVED OUT on 2026-08-05, into
        TestR3PausedIsAFounderOnlyGate's
        test_a_result_arriving_on_a_paused_run_is_recorded_and_held
        (DESIGN-round4 section 18.1). Every assertion it loses here (the
        founder was warned about a dirty scope, the fence is parked, an
        interruption names the late result) is a CONSEQUENCE OF REJECTING
        the result, and rejecting a real answer because a founder pressed a
        REVERSIBLE pause is the behaviour being removed, so asserting its
        consequences would pin the defect. What replaces it is stronger on
        the property this class exists to protect: the answer survives the
        pause and is verified on its own merits after `bm-controller
        resume`, instead of being rolled back on disk with a retry burned.
        The other three rows stay verbatim, including the whole of
        _assert_late_result_handled."""
        red_rows = {
            "DELIVERABLE_READY": ("VERIFYING", "CHECKPOINTED",
                                  "DELIVERABLE_READY"),
            "STOPPING": ("STOPPING",),
            "STOPPED": ("STOPPING", "STOPPED"),
        }
        for state, edges in sorted(red_rows.items()):
            with self.subTest(run_state=state):
                with tempfile.TemporaryDirectory() as d:
                    with bs.Store(d) as store:
                        _seed(store)
                        _sign(store)
                        checker = FakeCheckRunner(self._DIRTY)
                        engine, run, dispatch_ids = _park_one_async_unit(
                            self, store, checker)
                        fence_uuid = _unit_row(store, run["run_id"],
                                               "u1")["fence_uuid"]
                        _move_run_along_legal_edges(store, run, edges)

                        outcome = engine.receive_result(
                            "p1", dispatch_ids["u1"], "claims done",
                            ["a.py"], cost={"tokens": 1})

                        self.assertEqual(outcome, "rejected")
                        self._assert_late_result_handled(
                            store, run, fence_uuid, state)


class TestR2F1CheckTimeoutsWalksTheSameStatesAsReceiveResult(unittest.TestCase):
    """REFUTATION-2 F1, the sibling path the fix did not cover:
    check_timeouts records a result and a verification and then rejects
    with NO walk in between, so the same FAILED_TERMINAL move is attempted
    from EXECUTING and every subsequent step() raises forever."""

    def test_an_abandoned_dispatch_whose_rollback_fails_never_raises(self):
        """REFUTATION-2 probe_f1b_timeouts.py."""
        with tempfile.TemporaryDirectory() as d:
            with bs.Store(d) as store:
                _seed(store)
                _sign(store)
                clock = {"now": bc._default_now()}
                checker = FakeCheckRunner({
                    "git restore -- a.py": {"exit_code": 1, "stdout": "",
                                            "stderr": "rollback failed"}})
                worker = FakeWorker({"u1": _worker_result(status="pending")})
                engine = _engine(store, worker, checker,
                                 dispatch_timeout_seconds=60,
                                 now_fn=lambda: clock["now"])
                run = _begin_and_plan(engine, store, "p1",
                                      [_unit("u1", write_scope=["a.py"])])
                engine.step("p1")
                fence_uuid = _unit_row(store, run["run_id"],
                                       "u1")["fence_uuid"]
                clock["now"] = clock["now"] + datetime.timedelta(seconds=120)

                abandoned = engine.check_timeouts("p1")

                self.assertEqual(abandoned, ["u1"])
                self.assertEqual(
                    store.get_run("p1", raw=True)["state"], "FAILED_TERMINAL",
                    "check_timeouts walks the run exactly as receive_result "
                    "does, so the failed rollback reaches FAILED_TERMINAL "
                    "instead of raising out of an illegal move")
                steps = [s["what"] for s in store.list_human_steps(
                    "p1", resolved=False, raw=True)]
                self.assertTrue(any("rollback" in w.lower() for w in steps),
                                "the founder is warned: %r" % (steps,))
                self.assertNotEqual(store.get(fence_uuid).state, "active")
                summary = engine.step("p1")
                self.assertEqual(summary["note"],
                                 "run is terminal; nothing to do")

    def test_two_timeouts_exhaust_the_breaker_and_no_later_step_raises(self):
        """REFUTATION-2 probe_f1c_deliver_from_executing.py: with a CLEAN
        rollback the run used to stay wedged in EXECUTING, and every later
        step() raised on _deliver_or_hold's converge-before-delivery, which
        assumes READY is reachable from EXECUTING."""
        with tempfile.TemporaryDirectory() as d:
            with bs.Store(d) as store:
                _seed(store)
                _sign(store)
                clock = {"now": bc._default_now()}
                worker = FakeWorker({"u1": _worker_result(status="pending")})
                engine = _engine(store, worker, FakeCheckRunner(),
                                 dispatch_timeout_seconds=60,
                                 now_fn=lambda: clock["now"])
                run = _begin_and_plan(engine, store, "p1",
                                      [_unit("u1", write_scope=["a.py"])])
                engine.step("p1")
                clock["now"] = clock["now"] + datetime.timedelta(seconds=120)
                self.assertEqual(engine.check_timeouts("p1"), ["u1"])
                self.assertEqual(
                    _unit_row(store, run["run_id"], "u1")["retry_count"], 1)
                engine.step("p1")  # attempt 2, parks again
                clock["now"] = clock["now"] + datetime.timedelta(seconds=120)
                self.assertEqual(engine.check_timeouts("p1"), ["u1"])
                self.assertEqual(
                    _unit_row(store, run["run_id"], "u1")["status"], "FAILED",
                    "the breaker is exhausted after the second abandonment")

                summary = engine.step("p1")
                trace = engine.run_to_completion("p1", max_steps=5)

                legal_rest = frozenset(
                    bc._TERMINAL_STATES
                    | {"WAITING_HUMAN", "DELIVERABLE_READY"})
                self.assertIn(
                    summary["state"], legal_rest,
                    "the step after an exhausted breaker ends in a legal "
                    "terminal or founder-gated state, with zero exceptions")
                self.assertIn(trace[-1]["state"], legal_rest)


class TestR2F2TheRevisionEveryPathWasJudgedUnder(unittest.TestCase):
    """REFUTATION-2 F2, the new hole the multi-path loop opened: the
    dispatch was stamped with the LAST verdict's revision, so an amend
    landing between two per-path gate_check calls left path 1 judged under
    the OLD contract and the staleness protocol seeing no movement."""

    def test_an_amend_straddling_the_per_path_loop_never_authorises_a_forbidden_path(self):
        with tempfile.TemporaryDirectory() as d:
            with bs.Store(d) as store:
                _seed(store)
                _sign(store, allowed_paths=["docs", "src"])
                wrapped = _StraddlingStore(
                    store, {1: _amend_allowed_paths(["src"])})
                worker = FakeWorker({"u1": _worker_result()})
                engine = _engine(wrapped, worker, FakeCheckRunner())
                run = _begin_and_plan(engine, wrapped, "p1", [
                    _unit("u1", write_scope=["docs/x.md", "src/a.py"]),
                ])

                summary = engine.step("p1")

                self.assertEqual(
                    summary["dispatched"], [],
                    "the whole per-path loop is re-run against the newer "
                    "contract, which forbids docs/x.md: %r"
                    % (wrapped.gate_check_calls,))
                self.assertEqual(worker.calls, {})
                self.assertEqual(_dispatch_count(store, "u1"), 0)
                for dispatch in store.list_dispatches("u1", raw=True):
                    self.assertEqual(
                        dispatch["contract_revision"], 2,
                        "a dispatch is stamped only with the revision EVERY "
                        "path was actually judged under")

    def test_a_revision_that_moves_twice_defers_the_unit_instead_of_failing_it(self):
        """Contention, not a violation: two amends straddling both passes
        mean nothing can be judged under one stable contract this wave, so
        the unit is deferred (no drain, no retry burned, no dispatch)."""
        with tempfile.TemporaryDirectory() as d:
            with bs.Store(d) as store:
                _seed(store)
                _sign(store, allowed_paths=["docs", "src"])
                wrapped = _StraddlingStore(store, {
                    1: _amend_allowed_paths(["docs", "src"]),
                    3: _amend_allowed_paths(["docs", "src"]),
                })
                worker = FakeWorker({"u1": _worker_result()})
                engine = _engine(wrapped, worker, FakeCheckRunner())
                run = _begin_and_plan(engine, wrapped, "p1", [
                    _unit("u1", write_scope=["docs/x.md", "src/a.py"]),
                ])

                summary = engine.step("p1")

                self.assertEqual(summary["dispatched"], [])
                self.assertEqual(worker.calls, {})
                row = _unit_row(store, run["run_id"], "u1")
                self.assertEqual(row["status"], "READY",
                                 "a deferred unit is tried again next wave")
                self.assertEqual(row["retry_count"], 0,
                                 "contention burns no retry: it is not an "
                                 "authorisation gap for this unit")
                self.assertNotEqual(store.get_run("p1", raw=True)["state"],
                                    "STOPPED", "contention is never a drain")

    def test_a_write_scope_that_is_the_parent_of_an_allowed_path_is_refused(self):
        """REFUTATION-2 F2's second route: gate_check compared with the
        SYMMETRIC paths_overlap, so declaring the PARENT of an allowed path
        (or '.') passed, and the worker was handed a brief and a fence
        covering every sibling the contract refuses by name."""
        with tempfile.TemporaryDirectory() as d:
            with bs.Store(d) as store:
                _seed(store)
                _sign(store, allowed_paths=["src/app"])
                worker = FakeWorker({"u1": _worker_result()})
                engine = _engine(store, worker, FakeCheckRunner())
                run = _begin_and_plan(engine, store, "p1", [
                    _unit("u1", write_scope=["src"]),
                ])
                self.assertEqual(
                    store.gate_check("p1", "file-create",
                                     path="src/secrets.env")["verdict"],
                    "REFUSED-SCOPE",
                    "fixture sanity: the sibling is refused by name")

                summary = engine.step("p1")

                self.assertEqual(
                    summary["dispatched"], [],
                    "a unit may not widen its way to a sibling the contract "
                    "refuses by naming their common ancestor")
                self.assertEqual(worker.calls, {})
                self.assertEqual(_dispatch_count(store, "u1"), 0)


class TestR2F4DeadDependenciesAndFounderGatedLanes(unittest.TestCase):
    """REFUTATION-2 F4: the escalation recognised only a FAILED dependency
    and judged the whole run by unit status alone, so a SKIPPED dependency
    and any founder-gated lane still parked the run in CHECKPOINTED forever
    and still burned every one of run_to_completion's max_steps."""

    def test_a_skipped_dependency_is_as_dead_as_a_failed_one(self):
        """REFUTATION-2 probe_f4c_skipped_dep.py: a re-plan drops u1, so
        u2's dependency is SKIPPED and select_ready_units can never return
        it again."""
        with tempfile.TemporaryDirectory() as d:
            with bs.Store(d) as store:
                _seed(store)
                _sign(store)
                worker = FakeWorker({
                    "u0": _worker_result(claim="done", artifacts=["z.py"]),
                })
                engine = _engine(store, worker, FakeCheckRunner())
                run = _begin_and_plan(engine, store, "p1", [
                    _unit("u0", write_scope=["z.py"]),
                    _unit("u1", write_scope=["a.py"]),
                    _unit("u2", dependencies=["u1"], write_scope=["b.py"]),
                ])
                engine.plan("p1", run["run_id"], [
                    _unit("u0", write_scope=["z.py"]),
                    _unit("u2", dependencies=["u1"], write_scope=["b.py"]),
                ])
                self.assertEqual(
                    _unit_row(store, run["run_id"], "u1")["status"], "SKIPPED")

                trace = engine.run_to_completion("p1", max_steps=15)

                self.assertLess(
                    len(trace), 15,
                    "run_to_completion must RETURN, not spin through every "
                    "one of its max_steps with nothing selectable")
                self.assertIn(
                    trace[-1]["state"],
                    frozenset(bc._TERMINAL_STATES | {"WAITING_HUMAN"}),
                    "a run whose remainder waits on a SKIPPED unit resolves "
                    "to a terminal state or WAITING_HUMAN: %r" % (trace[-1],))
                self.assertEqual(
                    _unit_row(store, run["run_id"], "u2")["status"], "BLOCKED")
                reasons = [s["what"] for s in store.list_human_steps(
                    "p1", resolved=False, raw=True) if "u2" in s["what"]]
                self.assertTrue(reasons,
                                "the founder is told WHICH unit cannot run")
                self.assertTrue(
                    any("u1" in r and "SKIPPED" in r for r in reasons),
                    "the escalation names the dead dependency AND its "
                    "status: %r" % (reasons,))

    def test_a_failed_dependency_plus_a_gated_lane_reaches_waiting_human(self):
        """REFUTATION-2 probe_f4_stall.py case C: a unit whose LANE holds
        an open human step is not selectable but keeps status READY, so the
        whole-run judgement (every non-terminal unit BLOCKED) was false and
        the run rested in CHECKPOINTED forever."""
        with tempfile.TemporaryDirectory() as d:
            with bs.Store(d) as store:
                _seed(store)
                _sign(store)
                worker = FakeWorker({
                    "u1": _worker_result(claim="claims done",
                                         artifacts=["a.py"]),
                })
                checker = FakeCheckRunner({
                    "check-u1": {"exit_code": 1, "stdout": "",
                                 "stderr": "u1 never passes"}})
                engine = _engine(store, worker, checker)
                run = _begin_and_plan(engine, store, "p1", [
                    _unit("u1", write_scope=["a.py"], done_check="check-u1"),
                    _unit("u2", dependencies=["u1"], write_scope=["b.py"]),
                    _unit("g1", write_scope=["g.py"], lane="release"),
                ])
                store.queue_human_step(
                    "p1", "publish-release", "release",
                    "click publish in the dashboard", "", [], "sess1",
                    _actor())

                trace = engine.run_to_completion("p1", max_steps=20)

                self.assertLess(len(trace), 20,
                                "the run must not spin through max_steps")
                self.assertEqual(trace[-1]["state"], "WAITING_HUMAN")
                self.assertEqual(
                    _unit_row(store, run["run_id"], "u1")["status"], "FAILED")
                self.assertEqual(
                    _unit_row(store, run["run_id"], "u2")["status"], "BLOCKED")

    def test_one_gated_lane_with_nothing_failed_also_reaches_waiting_human(self):
        """REFUTATION-2 probe_f4_stall.py case D, the pre-existing shape:
        a single founder-gated lane, nothing failed at all, still spun
        run_to_completion through every step while resting in READY."""
        with tempfile.TemporaryDirectory() as d:
            with bs.Store(d) as store:
                _seed(store)
                _sign(store)
                engine = _engine(store, FakeWorker({}), FakeCheckRunner())
                run = _begin_and_plan(engine, store, "p1", [
                    _unit("g1", write_scope=["g.py"], lane="release"),
                ])
                store.queue_human_step(
                    "p1", "publish-release", "release",
                    "click publish in the dashboard", "", [], "sess1",
                    _actor())

                trace = engine.run_to_completion("p1", max_steps=20)

                self.assertLess(len(trace), 20,
                                "the run must not spin through max_steps")
                self.assertEqual(trace[-1]["state"], "WAITING_HUMAN",
                                 "a unit unselectable SOLELY because its "
                                 "lane holds an open human step is "
                                 "founder-waiting, not merely waiting")

    def test_run_to_completion_returns_when_a_step_makes_no_progress(self):
        """The belt-and-braces stop: a unit whose files are held by an
        UNRELATED fence can never be claimed, so every wave dispatches
        nothing, completes nothing and changes no state, with no dispatch
        in flight to wait for.

        THE ONE TEST IN THIS FILE THAT BUILDS ITS STORE FROM bc.bs, the
        module tools/bm_controller.py ITSELF loaded, rather than from this
        file's own independent load: it is the only test that exercises an
        engine branch which CATCHES bs.OwnershipRefused (the fence-overlap
        defer in _claim_and_dispatch), and the class-identity trap
        tools/bm_project.py documents means a refusal raised by a second,
        independent load of bm_store is a DIFFERENT class that branch can
        never catch. In production there is exactly one loaded bm_store per
        process, which is what bc.bs reproduces here."""
        with tempfile.TemporaryDirectory() as d:
            with bc.bs.Store(d) as store:
                _seed(store)
                _sign(store)
                store.claim(name="unrelated-holder", lifetime="persistent",
                            files=["a.py"], objective="holds the same file",
                            owner="someone-else", session_id="other-session")
                engine = _engine(store, FakeWorker({}), FakeCheckRunner())
                _begin_and_plan(engine, store, "p1",
                                [_unit("u1", write_scope=["a.py"])])

                trace = engine.run_to_completion("p1", max_steps=25)

                self.assertLess(
                    len(trace), 25,
                    "a step that dispatches nothing, completes nothing, "
                    "moves no state and waits on no open dispatch is a "
                    "parked run, not a reason to burn 25 steps")



# ---------------------------------------------------------------------------
# ROUND 4: the findings a THIRD adversarial pass raised against the round-3
# fixes above, across three independent lenses (reports
# docs/program/absolute-lead/evidence/L03/REFUTATION-3-state-machine.md,
# -authorization.md and -liveness.md, 2026-08-05), closed by
# DESIGN-round4.md. Every class below is one of those reports' own probe
# sequences restated as a permanent regression test, and every one of them
# FAILED on the tree those reports attacked (evidence:
# docs/program/absolute-lead/evidence/L03/RED-round4-controller.txt), which
# is what makes each of them evidence rather than a restatement of a fix.
# ---------------------------------------------------------------------------

def _pause_the_run(store, run):
    """Move a run to PAUSED the way the store's own table allows, so the
    tests below ask what the ENGINE does from PAUSED rather than whether
    PAUSED was reachable (the same discipline
    _move_run_along_legal_edges states for the round-3 matrix)."""
    store.set_run_state(run["run_id"], "PAUSED", _actor(),
                        "founder paused the run", "sess1")


def _resume_the_run(store, run):
    """`bm-controller resume`'s own move (PAUSED to READY), performed the
    way that command performs it: straight through set_run_state, never
    through an engine method, because un-pausing is a founder action."""
    store.set_run_state(run["run_id"], "READY", _actor(), "founder resume",
                        "sess1")


class TestR3PausedIsAFounderOnlyGate(unittest.TestCase):
    """REFUTATION-3 SM findings A and B, and LV's own PAUSED rows. A run
    the founder PAUSED is the founder's, and no engine path may dispatch,
    judge, verify, deliver, abandon or un-pause it (DESIGN-round4 law L1).
    Round 3 left every one of those reachable: _walk_to_executing is a
    silent no-op from PAUSED so step() dispatched anyway (SM A, reproduced
    through four shipped commands), _deliver_or_hold asked only whether
    READY was a LEGAL move and so performed the founder's own resume edge
    (SM B), check_timeouts abandoned a dispatch on a paused run, and plan()
    un-paused it through upsert_units' PLANNING to READY flip."""

    def test_a_paused_run_dispatches_nothing_under_a_live_contract(self):
        """SM A, probes p08_cli_paused_dispatch.py and
        p03_dispatch_from_paused.py: the contract is LIVE and the RUN is
        PAUSED, which is exactly the state the founder is left in after
        `bm-autonomy resume` without `bm-controller resume`."""
        with tempfile.TemporaryDirectory() as d:
            with bs.Store(d) as store:
                _seed(store)
                _sign(store)
                worker = FakeWorker({"u1": _worker_result()})
                engine = _engine(store, worker, FakeCheckRunner())
                run = _begin_and_plan(engine, store, "p1",
                                      [_unit("u1", write_scope=["a.py"])])
                _pause_the_run(store, run)

                summary = engine.step("p1")

                self.assertEqual(summary["dispatched"], [])
                self.assertEqual(summary["state"], "PAUSED")
                self.assertEqual(summary["stop_reason"], "FOUNDER_WAITING")
                self.assertIn("resume", summary["note"])
                self.assertEqual(worker.calls, {},
                                 "no brief is handed to a worker from a "
                                 "PAUSED run")
                self.assertEqual(_dispatch_count(store, "u1"), 0)
                self.assertEqual(
                    _unit_row(store, run["run_id"], "u1")["status"], "READY",
                    "nothing about the unit moved either: no claim, no "
                    "fence, no dispatch row")

    def test_a_paused_run_never_delivers_itself(self):
        """SM B, probe p09_deliver_guard_and_spin.py part a: PAUSED to
        READY is LEGAL, and it is precisely the founder-only edge
        `bm-controller resume` exists to take, so legality was the wrong
        question. Here every unit is terminal while the run is PAUSED,
        which is the shape that reached _deliver_or_hold."""
        with tempfile.TemporaryDirectory() as d:
            with bs.Store(d) as store:
                _seed(store)
                _sign(store)
                checker = FakeCheckRunner(
                    {"check-u1": {"exit_code": 1, "stdout": "",
                                  "stderr": "never passes"}})
                worker = FakeWorker({"u1": _worker_result()})
                engine = _engine(store, worker, checker)
                run = _begin_and_plan(
                    engine, store, "p1",
                    [_unit("u1", write_scope=["a.py"],
                           done_check="check-u1")])
                engine.step("p1")  # one rejection, one retry left
                _pause_the_run(store, run)
                # The founder's pause lands while the last unit is being
                # judged; the breaker then exhausts, so nothing non-terminal
                # remains and delivery is what round 3 attempted next.
                store.mark_unit_failed("u1", _actor(), "ceiling reached")
                self.assertEqual(
                    _unit_row(store, run["run_id"], "u1")["status"], "FAILED")

                summary = engine.step("p1")

                self.assertEqual(store.get_run("p1", raw=True)["state"],
                                 "PAUSED",
                                 "the engine never takes the founder's own "
                                 "resume edge to deliver")
                self.assertEqual(summary["state"], "PAUSED")
                self.assertEqual(summary["stop_reason"], "FOUNDER_WAITING")
                kinds = [c["kind"] for c in store.recent_checkpoints(
                    "p1", limit=200, raw=True)]
                self.assertNotIn("deliverable-ready", kinds,
                                 "no deliverable-ready checkpoint is written "
                                 "for a paused run: %r" % (kinds,))

                _resume_the_run(store, run)
                after = engine.step("p1")
                self.assertEqual(after["state"], "DELIVERABLE_READY",
                                 "the founder's own resume is what unlocks "
                                 "delivery, and it still works")

    def test_a_result_arriving_on_a_paused_run_is_recorded_and_held(self):
        """SM A consequence 3 and DESIGN-round4 section 4.3. Round 3 routed
        this to _handle_late_result, which recorded a rejected
        verification, burned a retry, ran the founder's rollback command
        against the filesystem and parked the fence: a real answer
        destroyed because a founder pressed pause, which is reversible.
        This test also carries the PAUSED row that used to live in
        TestR2F1LateResultOnARunStateThatCannotReachVerifying's red_rows
        (design section 18.1's supersession)."""
        with tempfile.TemporaryDirectory() as d:
            with bs.Store(d) as store:
                _seed(store)
                _sign(store)
                checker = FakeCheckRunner()
                engine, run, dispatch_ids = _park_one_async_unit(
                    self, store, checker)
                fence_uuid = _unit_row(store, run["run_id"],
                                       "u1")["fence_uuid"]
                _pause_the_run(store, run)

                outcome = engine.receive_result(
                    "p1", dispatch_ids["u1"], "claims done", ["a.py"],
                    cost={"tokens": 11, "minutes": 3})

                self.assertEqual(outcome, "held")
                self.assertEqual(
                    store.list_dispatches("u1", raw=True)[0]["status"],
                    "RESULT_IN",
                    "the answer is durable: at-most-once recording still "
                    "holds, nothing is judged")
                self.assertEqual(store.get_run("p1", raw=True)["state"],
                                 "PAUSED")
                self.assertEqual(store.get(fence_uuid).state, "active",
                                 "the fence is NOT parked: the unit is not "
                                 "finished, it is waiting for the founder")
                self.assertEqual(
                    _unit_row(store, run["run_id"], "u1")["retry_count"], 0,
                    "a pause burns no retry")
                self.assertEqual(
                    [c for c in checker.calls if c.startswith("git restore")],
                    [], "no rollback is run against the founder's files")
                self.assertEqual(store.spend_totals("p1")["tokens"], 11,
                                 "the meter is still charged for real work "
                                 "(section 10.1's one spend rule)")

                _resume_the_run(store, run)
                summary = engine.step("p1")

                self.assertEqual(
                    _unit_row(store, run["run_id"], "u1")["status"], "DONE",
                    "the held answer is judged on its own merits after the "
                    "founder resumes: %r" % (summary,))

    def test_check_timeouts_abandons_nothing_on_a_paused_run(self):
        """Abandoning a dispatch records a result AND a rejected
        verification AND runs the founder's rollback command, which is the
        engine acting on a paused run by any reading."""
        with tempfile.TemporaryDirectory() as d:
            with bs.Store(d) as store:
                _seed(store)
                _sign(store)
                clock = {"now": bc._default_now()}
                worker = FakeWorker({"u1": _worker_result(status="pending")})
                engine = _engine(store, worker, FakeCheckRunner(),
                                 dispatch_timeout_seconds=60,
                                 now_fn=lambda: clock["now"])
                run = _begin_and_plan(engine, store, "p1",
                                      [_unit("u1", write_scope=["a.py"])])
                engine.step("p1")
                fence_uuid = _unit_row(store, run["run_id"],
                                       "u1")["fence_uuid"]
                clock["now"] = clock["now"] + datetime.timedelta(seconds=120)
                _pause_the_run(store, run)

                self.assertEqual(engine.check_timeouts("p1"), [])

                self.assertEqual(
                    store.list_dispatches("u1", raw=True)[0]["status"],
                    "DISPATCHED", "the dispatch stays open across a pause")
                self.assertEqual(store.get(fence_uuid).state, "active")
                self.assertEqual(store.get_run("p1", raw=True)["state"],
                                 "PAUSED")

    def test_plan_refuses_run_paused_instead_of_un_pausing_the_run(self):
        """upsert_units always flips a run to READY and PAUSED to READY is
        legal, so `bm-controller plan` un-paused a paused run as a side
        effect of writing a unit graph.

        The refusal is asserted against bc.bs.OwnershipRefused, the class
        tools/bm_controller.py itself loaded, never this file's own
        independent load: the class-identity trap tools/bm_project.py
        documents applies to a caller CATCHING a refusal exactly as it
        applies to an engine branch catching one."""
        with tempfile.TemporaryDirectory() as d:
            with bc.bs.Store(d) as store:
                _seed(store)
                _sign(store)
                engine = _engine(store, FakeWorker({}), FakeCheckRunner())
                run = _begin_and_plan(engine, store, "p1",
                                      [_unit("u1", write_scope=["a.py"])])
                _pause_the_run(store, run)

                with self.assertRaises(bc.bs.OwnershipRefused) as caught:
                    engine.plan("p1", run["run_id"],
                                [_unit("u1", write_scope=["a.py"]),
                                 _unit("u2", write_scope=["b.py"])])

                self.assertEqual(caught.exception.reason, "run-paused")
                self.assertIn("resume", str(caught.exception))
                self.assertEqual(store.get_run("p1", raw=True)["state"],
                                 "PAUSED")
                self.assertIsNone(
                    _unit_row(store, run["run_id"], "u2"),
                    "nothing was written: the refusal precedes upsert_units")

    def test_run_to_completion_stops_on_the_first_step_of_a_paused_run(self):
        """One guard, checked once: run_to_completion needs no PAUSED
        branch of its own because its first step() reports FOUNDER_WAITING
        and the ONE stop predicate reads it."""
        with tempfile.TemporaryDirectory() as d:
            with bs.Store(d) as store:
                _seed(store)
                _sign(store)
                worker = FakeWorker({"u1": _worker_result()})
                engine = _engine(store, worker, FakeCheckRunner())
                run = _begin_and_plan(engine, store, "p1",
                                      [_unit("u1", write_scope=["a.py"])])
                _pause_the_run(store, run)

                trace = engine.run_to_completion("p1", max_steps=10)

                self.assertEqual(len(trace), 1)
                self.assertEqual(trace[0]["stop_reason"], "FOUNDER_WAITING")
                self.assertEqual(worker.calls, {})


class TestR3TheSummaryStateIsTheStoreState(unittest.TestCase):
    """REFUTATION-3 SM E and LV 3. _handle_no_ready_units wrote
    summary['state'] = 'WAITING_HUMAN' unconditionally while the store move
    beside it was guarded, and step() returned that summary to the caller
    with no re-read, so `bm-controller step --json` printed one state and
    `bm-controller status` printed another a second later. The same lie was
    load bearing: run_to_completion stopped on the state the SUMMARY
    claimed."""

    def _drive_and_compare(self, engine, store, project_id, steps):
        summary = None
        for _ in range(steps):
            summary = engine.step(project_id)
            self.assertEqual(
                summary["state"],
                store.get_run(project_id, raw=True)["state"],
                "every summary state is a store read at return time: %r"
                % (summary,))
            if summary.get("stop_reason") is not None:
                return summary
        return summary

    def test_a_founder_gated_remainder_reports_the_state_the_store_holds(self):
        """LV p11 and SM p07 case 4: a run wedged in EXECUTING whose only
        remaining unit sits behind an open founder step. WAITING_HUMAN is
        not legal from EXECUTING, so the store move never happened and the
        summary said it did."""
        with tempfile.TemporaryDirectory() as d:
            with bs.Store(d) as store:
                _seed(store)
                _sign(store)
                engine = _engine(store, FakeWorker({}), FakeCheckRunner())
                run = _begin_and_plan(
                    engine, store, "p1",
                    [_unit("u1", write_scope=["a.py"], lane="release")])
                store.queue_human_step(
                    "p1", "publish-release", "release",
                    "click publish in the dashboard", "", [], "sess1",
                    _actor())
                _move_run_along_legal_edges(store, run, ("EXECUTING",))

                summary = engine.step("p1")

                self.assertEqual(summary["state"],
                                 store.get_run("p1", raw=True)["state"],
                                 "the summary never says anything the store "
                                 "does not hold: %r" % (summary,))
                self.assertEqual(summary["stop_reason"], "FOUNDER_WAITING")

    def test_every_summary_of_a_driven_trace_matches_the_store(self):
        """The whole trajectory, not one row: a multi-wave run driven step
        by step, with the store read back after every single call.

        A CONTROL, and stated as one: it is GREEN on the untouched tree
        too, because the mismatch SM E found lives only on the
        founder-waiting branch. It exists so the one exit funnel cannot buy
        its honesty on that branch by breaking the ordinary one."""
        with tempfile.TemporaryDirectory() as d:
            with bs.Store(d) as store:
                _seed(store)
                _sign(store)
                worker = FakeWorker({
                    "u1": _worker_result(artifacts=["a.py"]),
                    "u2": _worker_result(artifacts=["b.py"]),
                })
                engine = _engine(store, worker, FakeCheckRunner())
                _begin_and_plan(engine, store, "p1", [
                    _unit("u1", write_scope=["a.py"]),
                    _unit("u2", dependencies=["u1"], write_scope=["b.py"]),
                ])

                self._drive_and_compare(engine, store, "p1", 6)

    def test_run_to_completion_stops_on_a_state_the_store_actually_holds(self):
        """SM E's load-bearing half: the loop terminated because of a state
        the run was not in."""
        with tempfile.TemporaryDirectory() as d:
            with bs.Store(d) as store:
                _seed(store)
                _sign(store)
                engine = _engine(store, FakeWorker({}), FakeCheckRunner())
                run = _begin_and_plan(
                    engine, store, "p1",
                    [_unit("u1", write_scope=["a.py"], lane="release")])
                store.queue_human_step(
                    "p1", "publish-release", "release",
                    "click publish in the dashboard", "", [], "sess1",
                    _actor())
                _move_run_along_legal_edges(store, run, ("EXECUTING",))

                trace = engine.run_to_completion("p1", max_steps=8)

                self.assertEqual(trace[-1]["state"],
                                 store.get_run("p1", raw=True)["state"])
                self.assertLess(len(trace), 8)


class TestR3StopReasonDrivesEveryLoopDriver(unittest.TestCase):
    """REFUTATION-3 LV 5 and LV 6, and SM J. Round 3's stop read a NOTE
    STRING, so three zero-progress shapes it did not enumerate spun through
    every max_steps (the soft spend stop, a failing done-definition
    re-running the founder's whole suite once per wasted step, and the
    ORDINARY async park the production RecordIntentWorker always produces),
    while the two shapes it DID cover were transient contention it then
    described to the founder as 'parked until a founder acts'. Control flow
    now reads an enum and never the prose (law L3)."""

    def test_the_soft_spend_stop_parks_in_one_step(self):
        """LV p1 and FIX-round3 residual 4: 12 of 12 steps, every one
        identical."""
        with tempfile.TemporaryDirectory() as d:
            with bs.Store(d) as store:
                _seed(store)
                _sign(store, token_ceiling=10)
                worker = FakeWorker({
                    "u1": _worker_result(artifacts=["a.py"], tokens=9,
                                         minutes=0),
                    "u2": _worker_result(artifacts=["b.py"]),
                })
                engine = _engine(store, worker, FakeCheckRunner())
                _begin_and_plan(engine, store, "p1", [
                    _unit("u1", write_scope=["a.py"]),
                    _unit("u2", dependencies=["u1"], write_scope=["b.py"]),
                ])
                engine.step("p1")
                self.assertEqual(store.spend_totals("p1")["verdict"],
                                 "soft-stop")

                trace = engine.run_to_completion("p1", max_steps=12)

                self.assertEqual(len(trace), 1,
                                 "one wave discovers the soft stop and the "
                                 "loop stops on it: %r" % (trace,))
                self.assertEqual(trace[-1]["stop_reason"], "SPEND_STOP")
                self.assertNotIn("u2", worker.calls)

    def test_a_failing_done_definition_runs_once_per_call(self):
        """LV p8 case C: 15 executions of the founder's WHOLE
        done-definition in ONE run_to_completion call, which with the
        production SubprocessCheckRunner and the default max_steps=500 is
        500 real test-suite runs."""
        with tempfile.TemporaryDirectory() as d:
            with bs.Store(d) as store:
                _seed(store)
                _sign(store)
                checker = FakeCheckRunner(
                    {"final-check": {"exit_code": 1, "stdout": "",
                                     "stderr": "not ready"}})
                worker = FakeWorker({"u1": _worker_result(artifacts=["a.py"])})
                engine = _engine(store, worker, checker)
                _begin_and_plan(engine, store, "p1",
                                [_unit("u1", write_scope=["a.py"])],
                                done_definition="final-check")
                engine.step("p1")
                before = checker.calls.count("final-check")

                trace = engine.run_to_completion("p1", max_steps=15)

                self.assertEqual(
                    checker.calls.count("final-check") - before, 1,
                    "exactly ONE done-definition execution per call")
                self.assertEqual(trace[-1]["stop_reason"], "FOUNDER_WAITING")
                self.assertIn("done-definition", trace[-1]["note"])

    def test_a_provider_outage_parks_after_one_ask(self):
        """LV p8 case D and SM J: 15 of 15 steps, 15 worker asks, no
        backoff and no bound other than max_steps."""
        with tempfile.TemporaryDirectory() as d:
            with bs.Store(d) as store:
                _seed(store)
                _sign(store)
                worker = FakeWorker({"u1": {"status": "unavailable"}})
                engine = _engine(store, worker, FakeCheckRunner())
                _begin_and_plan(engine, store, "p1",
                                [_unit("u1", write_scope=["a.py"])])

                trace = engine.run_to_completion("p1", max_steps=15)

                self.assertEqual(trace[-1]["stop_reason"], "OUTAGE")
                self.assertEqual(worker.calls["u1"], 1,
                                 "the same unavailable worker is not asked "
                                 "max_steps times")
                self.assertEqual(len(trace), 1)

    def test_a_fence_overlap_is_contention_and_says_so(self):
        """LV p2: zero founder steps exist and the event that unblocks the
        run is another agent releasing its fence, yet the note told the
        founder the run was parked until THEY acted.

        BUILDS ITS STORE FROM bc.bs, the module tools/bm_controller.py
        itself loaded, for the reason
        test_run_to_completion_returns_when_a_step_makes_no_progress states
        in full: this exercises the engine branch that CATCHES
        bs.OwnershipRefused (the fence-overlap defer inside
        _authorise_dispatch), and a refusal raised by a second, independent
        load of bm_store is a DIFFERENT class that branch can never
        catch."""
        with tempfile.TemporaryDirectory() as d:
            with bc.bs.Store(d) as store:
                _seed(store)
                _sign(store)
                store.claim(name="unrelated-holder", lifetime="persistent",
                            files=["a.py"], objective="holds the same file",
                            owner="someone-else", session_id="other-session")
                worker = FakeWorker({})
                engine = _engine(store, worker, FakeCheckRunner())
                _begin_and_plan(engine, store, "p1",
                                [_unit("u1", write_scope=["a.py"])])

                trace = engine.run_to_completion("p1", max_steps=20)

                self.assertEqual(trace[-1]["stop_reason"], "CONTENTION")
                self.assertNotIn(
                    "until a founder acts", trace[-1]["note"],
                    "contention is transient: the note may not claim a "
                    "founder is needed, because none is: %r"
                    % (trace[-1]["note"],))
                self.assertIn("no founder action is needed",
                              trace[-1]["note"])
                self.assertEqual(worker.calls, {})

    def test_a_double_contract_amend_is_contention_and_says_so(self):
        """LV p3: the round-3 contention deferral fed the round-3 park stop
        and produced the same false statement about the run."""
        with tempfile.TemporaryDirectory() as d:
            with bc.bs.Store(d) as store:
                _seed(store)
                _sign(store, allowed_paths=["docs", "src"])
                wrapped = _StraddlingStore(store, {
                    1: _amend_allowed_paths(["docs", "src"]),
                    3: _amend_allowed_paths(["docs", "src"]),
                })
                worker = FakeWorker({"u1": _worker_result()})
                engine = _engine(wrapped, worker, FakeCheckRunner())
                _begin_and_plan(engine, wrapped, "p1", [
                    _unit("u1", write_scope=["docs/x.md", "src/a.py"]),
                ])

                summary = engine.step("p1")

                self.assertEqual(summary["stop_reason"], "CONTENTION")
                self.assertNotIn("until a founder acts", summary["note"])
                self.assertEqual(worker.calls, {})

    def test_the_ordinary_async_park_reports_in_flight_after_one_ask(self):
        """SM J probe p09 part c: the note a parked async wave actually
        writes was in neither no-progress constant, so the stop never fired
        for the shape the production RecordIntentWorker ALWAYS produces:
        12 of 12 steps and the same unit brief printed to stdout 12
        times."""
        with tempfile.TemporaryDirectory() as d:
            with bs.Store(d) as store:
                _seed(store)
                _sign(store)
                worker = FakeWorker({"u1": _worker_result(status="pending")})
                engine = _engine(store, worker, FakeCheckRunner())
                _begin_and_plan(engine, store, "p1",
                                [_unit("u1", write_scope=["a.py"])])

                trace = engine.run_to_completion("p1", max_steps=12)

                self.assertEqual(len(trace), 1)
                self.assertEqual(trace[-1]["stop_reason"], "IN_FLIGHT")
                self.assertEqual(worker.calls["u1"], 1)


class TestR3TheExecutingWedgeUnwinds(unittest.TestCase):
    """REFUTATION-3 LV 1. A wave in which every unit is gate-refused walks
    the run to EXECUTING before knowing anything will be claimed, and
    nothing walks it back; from EXECUTING neither WAITING_HUMAN nor
    DELIVERABLE_READY is legal, so the deliverable was ready and could
    never be delivered, no founder step said so, and the wedge note was in
    neither no-progress constant so run_to_completion re-ran the founder's
    whole done-definition once per wasted step (21 executions in one
    call)."""

    def _wedge(self, store):
        _seed(store)
        _sign(store, allowed_paths=["src"])
        worker = FakeWorker({})
        engine = _engine(store, worker, FakeCheckRunner())
        run = _begin_and_plan(engine, store, "p1",
                              [_unit("u1", write_scope=["docs/x.md"])])
        return engine, run, worker

    def test_a_wave_that_claims_nothing_still_reaches_delivery(self):
        """LV p9: DELIVERABLE FOREVER OUT OF REACH: True."""
        with tempfile.TemporaryDirectory() as d:
            with bs.Store(d) as store:
                engine, run, worker = self._wedge(store)

                trace = engine.run_to_completion("p1", max_steps=20)

                self.assertLess(len(trace), 20)
                self.assertEqual(store.get_run("p1", raw=True)["state"],
                                 "DELIVERABLE_READY",
                                 "the breaker exhausts the ungrantable "
                                 "unit, the empty wave unwinds, and the run "
                                 "delivers: %r" % (trace[-1],))
                self.assertEqual(trace[-1]["state"],
                                 store.get_run("p1", raw=True)["state"])
                self.assertEqual(
                    _unit_row(store, run["run_id"], "u1")["status"], "FAILED")
                self.assertEqual(worker.calls, {})

    def test_the_wedge_note_never_appears_in_any_summary(self):
        """The note itself is the artefact of the dead end: 'the
        done-definition passes but the run is EXECUTING, from which
        delivery is not a legal move; staying in place'. LV p9 reaches it
        on the SECOND driver call, once the breaker has exhausted u1 and
        nothing non-terminal is left; that same call is where the wedge
        note, being in neither no-progress constant, burned 20 of 20 steps
        and re-ran the founder's whole done-definition 21 times."""
        with tempfile.TemporaryDirectory() as d:
            with bs.Store(d) as store:
                engine, _run, _worker = self._wedge(store)

                trace = (engine.run_to_completion("p1", max_steps=20)
                         + engine.run_to_completion("p1", max_steps=20))

                for summary in trace:
                    self.assertNotIn(
                        "is not a legal move", summary.get("note") or "",
                        "delivery is refused by an OWNED-EDGE rule, never "
                        "by a legality accident: %r" % (summary,))
                self.assertLess(len(trace), 21,
                                "neither call burns its max_steps: %d"
                                % len(trace))


class TestR3TheSkippedLifecycleClosesAtTheSource(unittest.TestCase):
    """REFUTATION-3 LV 2 and LV 4. A re-plan that drops a unit marked it
    SKIPPED and left everything else about it alive: its dispatch stayed
    DISPATCHED so a late result marked the dead unit DONE and silently
    reversed the founder's re-plan; its fence stayed ACTIVE with nothing
    naming it; check_timeouts could not see it because that filtered on
    unit status; and the drop was IRREVERSIBLE, because upsert_units'
    byte-identical-reuse rule meant the re-plan the escalation itself tells
    the founder to make did not bring the unit back."""

    def _drop_u1(self, store, checker=None, lane="default"):
        """Wave 1 parks u1 async and completes u2, then a re-plan drops
        u1: the exact four-command shape LV finding 4 used."""
        _seed(store)
        _sign(store)
        worker = FakeWorker({
            "u1": _worker_result(status="pending"),
            "u2": _worker_result(artifacts=["b.py"]),
        })
        engine = _engine(store, worker, checker or FakeCheckRunner())
        run = _begin_and_plan(engine, store, "p1", [
            _unit("u1", write_scope=["a.py"], lane=lane),
            _unit("u2", write_scope=["b.py"], lane=lane),
        ])
        engine.step("p1")
        fence_uuid = _unit_row(store, run["run_id"], "u1")["fence_uuid"]
        dispatch_id = store.list_dispatches("u1", raw=True)[0]["dispatch_id"]
        engine.plan("p1", run["run_id"],
                    [_unit("u2", write_scope=["b.py"], lane=lane)])
        return engine, run, worker, fence_uuid, dispatch_id

    def test_a_re_plan_cancels_the_dropped_units_open_dispatch(self):
        """GREEN before this change as well as after: the cancel landed in
        the STORE half. What it pins HERE is that the engine's own plan()
        route still carries it, so a founder using `bm-controller plan`
        gets it rather than only a direct Store caller."""
        with tempfile.TemporaryDirectory() as d:
            with bs.Store(d) as store:
                engine, run, _w, _f, dispatch_id = self._drop_u1(store)

                self.assertEqual(
                    _unit_row(store, run["run_id"], "u1")["status"], "SKIPPED")
                self.assertEqual(
                    store.get_dispatch(dispatch_id, raw=True)["status"],
                    "CANCELLED",
                    "a dropped unit's dispatch is closed AT THE SOURCE, in "
                    "the same transaction that skipped it")

    def test_a_result_for_a_cancelled_dispatch_refuses_instead_of_reviving_the_unit(self):
        """LV finding 4: `receive_result returned: u1` and the dropped unit
        went SKIPPED to DONE, its fence released as complete and its spend
        recorded, silently reversing the founder's re-plan."""
        with tempfile.TemporaryDirectory() as d:
            with bc.bs.Store(d) as store:
                engine, run, _w, _f, dispatch_id = self._drop_u1(store)
                tokens_before = store.spend_totals("p1")["tokens"]

                with self.assertRaises(bc.bs.OwnershipRefused) as caught:
                    engine.receive_result("p1", dispatch_id, "claims done",
                                          ["a.py"], cost={"tokens": 9000})

                self.assertEqual(caught.exception.reason, "dispatch-cancelled")
                self.assertEqual(
                    _unit_row(store, run["run_id"], "u1")["status"], "SKIPPED",
                    "the founder's drop stands")
                self.assertEqual(store.spend_totals("p1")["tokens"],
                                 tokens_before,
                                 "no meter is charged for a unit that is no "
                                 "longer in the graph")
                self.assertIsInstance(
                    caught.exception, bc.bs.BMStoreError,
                    "main() catches BMStoreError, so this is exit 1 with a "
                    "clear line, never a traceback")

    def test_a_re_plan_parks_the_dropped_units_fence(self):
        with tempfile.TemporaryDirectory() as d:
            with bs.Store(d) as store:
                _engine_, _run, _w, fence_uuid, _d = self._drop_u1(store)

                self.assertNotEqual(
                    store.get(fence_uuid).state, "active",
                    "a dropped unit holds no fence over the founder's files")

    def test_a_byte_identical_re_plan_revives_the_skipped_unit(self):
        """LV p5: the reuse-untouched rule made the founder's own repair a
        no-op, so the drop could never be undone.

        GREEN before this change as well as after, because the revival
        itself landed in the STORE half of this round
        (FIX-round4-store-report.md); what it pins HERE is that
        ControllerEngine.plan still reaches it, guard and orphan-fence
        release included."""
        with tempfile.TemporaryDirectory() as d:
            with bs.Store(d) as store:
                engine, run, _w, _f, _d = self._drop_u1(store)

                engine.plan("p1", run["run_id"], [
                    _unit("u1", write_scope=["a.py"]),
                    _unit("u2", write_scope=["b.py"]),
                ])

                row = _unit_row(store, run["run_id"], "u1")
                self.assertEqual(
                    row["status"], "READY",
                    "re-adding the unit is the founder undoing the drop, "
                    "and the identical hash is what makes it the SAME unit")
                self.assertEqual(row["retry_count"], 0)

    def test_the_whole_founder_recovery_ends_deliverable_ready(self):
        """LV p6's own control: before round 3 the founder's repair
        delivered the run; after it, the same repair could not. Drop,
        escalate, re-plan, resolve, deliver."""
        with tempfile.TemporaryDirectory() as d:
            with bs.Store(d) as store:
                _seed(store)
                _sign(store)
                worker = FakeWorker({
                    "u1": _worker_result(artifacts=["a.py"]),
                    "u2": _worker_result(artifacts=["b.py"]),
                })
                engine = _engine(store, worker, FakeCheckRunner())
                run = _begin_and_plan(engine, store, "p1", [
                    _unit("u1", write_scope=["a.py"]),
                    _unit("u2", dependencies=["u1"], write_scope=["b.py"]),
                ])
                engine.plan("p1", run["run_id"],
                            [_unit("u2", dependencies=["u1"],
                                   write_scope=["b.py"])])
                self.assertEqual(
                    _unit_row(store, run["run_id"], "u1")["status"], "SKIPPED")

                engine.run_to_completion("p1", max_steps=10)
                steps = store.list_human_steps("p1", resolved=False, raw=True)
                self.assertTrue(steps, "the founder is told what is stuck")

                # The founder does exactly what that step asks for.
                engine.plan("p1", run["run_id"], [
                    _unit("u1", write_scope=["a.py"]),
                    _unit("u2", dependencies=["u1"], write_scope=["b.py"]),
                ])
                for step_row in store.list_human_steps("p1", resolved=False,
                                                       raw=True):
                    store.resolve_human_step(step_row["step_id"], "p1",
                                             "repaired by re-plan", _actor())

                trace = engine.run_to_completion("p1", max_steps=10)

                self.assertEqual(store.get_run("p1", raw=True)["state"],
                                 "DELIVERABLE_READY",
                                 "RUN DELIVERED: %r" % (trace[-1],))
                for uid in ("u1", "u2"):
                    self.assertEqual(
                        _unit_row(store, run["run_id"], uid)["status"], "DONE")


class TestR3BlockedIsAMaterialisedViewOfTheLaneGate(unittest.TestCase):
    """REFUTATION-3 SM F and LV 2. Round 3 added two producers of BLOCKED
    units and no production caller of Store.unblock_lane_units existed
    anywhere, so every BLOCKED status was a one-way door: resolving the
    founder step did not make the lane selectable again, a healthy unit
    that merely shared the lane was lost as collateral, and a run could
    rest in WAITING_HUMAN reporting 'only founder-gated lanes remain' with
    ZERO open founder steps and nothing a founder could resolve. BLOCKED is
    now a view of one fact ('this lane holds an open founder step') and is
    reconciled in BOTH directions on every wave (law L4)."""

    def _stall(self, store, extra_units=()):
        worker = FakeWorker({
            "u0": _worker_result(artifacts=["z.py"]),
            "u1": _worker_result(artifacts=["a.py"]),
            "u2": _worker_result(artifacts=["b.py"]),
            "u3": _worker_result(artifacts=["c.py"]),
            "g1": _worker_result(artifacts=["g.py"]),
        })
        engine = _engine(store, worker, FakeCheckRunner())
        units = [_unit("u0", write_scope=["z.py"]),
                 _unit("u1", write_scope=["a.py"]),
                 _unit("u2", dependencies=["u1"], write_scope=["b.py"])]
        units.extend(extra_units)
        run = _begin_and_plan(engine, store, "p1", units)
        kept = [u for u in units if u["unit_id"] != "u1"]
        engine.plan("p1", run["run_id"], kept)
        engine.run_to_completion("p1", max_steps=10)
        return engine, run, worker

    def test_resolving_the_step_unblocks_the_lane_on_the_next_step(self):
        """SM p07 case 3: `select_ready_units now: []` forever, whatever
        the founder resolved."""
        with tempfile.TemporaryDirectory() as d:
            with bs.Store(d) as store:
                _seed(store)
                _sign(store)
                engine, run, _w = self._stall(store)
                self.assertEqual(
                    _unit_row(store, run["run_id"], "u2")["status"], "BLOCKED")

                engine.plan("p1", run["run_id"], [
                    _unit("u0", write_scope=["z.py"]),
                    _unit("u1", write_scope=["a.py"]),
                    _unit("u2", dependencies=["u1"], write_scope=["b.py"])])
                for step_row in store.list_human_steps("p1", resolved=False,
                                                       raw=True):
                    store.resolve_human_step(step_row["step_id"], "p1",
                                             "repaired", _actor())

                engine.step("p1")

                self.assertNotEqual(
                    _unit_row(store, run["run_id"], "u2")["status"], "BLOCKED",
                    "a lane whose last step was resolved returns its units "
                    "to PENDING or READY by dependency satisfaction")

    def test_a_healthy_unit_sharing_the_lane_recovers(self):
        """LV p12: block_lane_units flips EVERY PENDING/READY unit of the
        lane, so u3, which had nothing to do with the dropped dependency,
        was permanently lost too (`u3 was HEALTHY and is now: BLOCKED ...
        RUN DELIVERED: False`). u3 here waits on g1, a unit in a DIFFERENT,
        founder-gated lane, so it is PENDING when the escalation fires and
        is picked up purely as collateral."""
        with tempfile.TemporaryDirectory() as d:
            with bs.Store(d) as store:
                _seed(store)
                _sign(store)
                store.queue_human_step(
                    "p1", "publish-release", "release",
                    "click publish in the dashboard", "", [], "sess1",
                    _actor())
                engine, run, _w = self._stall(store, extra_units=[
                    _unit("g1", write_scope=["g.py"], lane="release"),
                    _unit("u3", dependencies=["g1"], write_scope=["c.py"])])
                self.assertEqual(
                    _unit_row(store, run["run_id"], "u3")["status"], "BLOCKED",
                    "the collateral is real while the lane is gated")

                for step_row in store.list_human_steps("p1", resolved=False,
                                                       raw=True):
                    store.resolve_human_step(step_row["step_id"], "p1",
                                             "looked at it", _actor())
                engine.run_to_completion("p1", max_steps=10)

                self.assertEqual(
                    _unit_row(store, run["run_id"], "u3")["status"], "DONE",
                    "the healthy unit runs once its lane is ungated, even "
                    "though u2 beside it is still unreachable")

    def test_resolving_without_repairing_re_queues_exactly_one_step(self):
        """LV p5's dead end: the run rested in WAITING_HUMAN reporting
        founder-gating with zero open steps and nothing to resolve. The
        condition is still true, so it is restated ONCE per founder
        action."""
        with tempfile.TemporaryDirectory() as d:
            with bs.Store(d) as store:
                _seed(store)
                _sign(store)
                engine, run, _w = self._stall(store)
                for step_row in store.list_human_steps("p1", resolved=False,
                                                       raw=True):
                    store.resolve_human_step(step_row["step_id"], "p1",
                                             "acknowledged", _actor())
                self.assertEqual(
                    store.list_human_steps("p1", resolved=False, raw=True), [])

                engine.step("p1")

                open_steps = store.list_human_steps("p1", resolved=False,
                                                    raw=True)
                self.assertEqual(
                    len(open_steps), 1,
                    "one fresh step, because the condition still holds: %r"
                    % ([s["what"] for s in open_steps],))
                self.assertIn("u2", open_steps[0]["what"])

    def test_ten_consecutive_steps_queue_exactly_one_escalation_step(self):
        """The idempotency marker is state-derived (an open founder step in
        the unit's own lane), so no driver, however eager, produces spam.

        A CONTROL: round 3 also queued exactly one, through a DIFFERENT
        mechanism (the BLOCKED status it wrote made the unit invisible to
        the next pass). This class removes that mechanism, so the anti-spam
        property has to be re-earned by the state-derived marker, and this
        is what fails if it is not."""
        with tempfile.TemporaryDirectory() as d:
            with bs.Store(d) as store:
                _seed(store)
                _sign(store)
                worker = FakeWorker({"u0": _worker_result(artifacts=["z.py"])})
                engine = _engine(store, worker, FakeCheckRunner())
                run = _begin_and_plan(engine, store, "p1", [
                    _unit("u0", write_scope=["z.py"]),
                    _unit("u1", write_scope=["a.py"]),
                    _unit("u2", dependencies=["u1"], write_scope=["b.py"]),
                ])
                engine.plan("p1", run["run_id"], [
                    _unit("u0", write_scope=["z.py"]),
                    _unit("u2", dependencies=["u1"], write_scope=["b.py"])])

                for _ in range(10):
                    engine.step("p1")

                open_steps = store.list_human_steps("p1", resolved=False,
                                                    raw=True)
                self.assertEqual(
                    len(open_steps), 1,
                    "ten steps, one escalation: %r"
                    % ([s["what"] for s in open_steps],))


class TestR3EveryDispatchRouteIsGateChecked(unittest.TestCase):
    """REFUTATION-3 AZ F-A1. _resume_dispatched built the brief and called
    the worker with NO gate_check on that route at all, so a founder who
    NARROWED a still-live contract had the worker told a second time to
    write a path they had just forbidden, with the fence over that path
    left active. The fix's own docstring rests its security property on
    gate_check being 'the ONLY component that ever compares a unit's scope
    against allowed_paths'; that sentence was false while a second route
    existed. There is now exactly ONE route from a unit row to a brief
    (law L5)."""

    def _park_then_amend(self, store, amend):
        _seed(store)
        _sign(store, allowed_paths=["docs", "src"])
        worker = FakeWorker({"u1": _worker_result(status="pending")})
        engine = _engine(store, worker, FakeCheckRunner())
        run = _begin_and_plan(engine, store, "p1",
                              [_unit("u1", write_scope=["docs/x.md"])])
        engine.step("p1")
        self.assertEqual(worker.calls["u1"], 1)
        fence_uuid = _unit_row(store, run["run_id"], "u1")["fence_uuid"]
        amend(store)
        return engine, run, worker, fence_uuid

    def test_a_narrowing_supersede_stops_the_re_await(self):
        """AZ F-A1 c1: `worker call count: {'u1': 2}` and `fence still
        held, files: ['docs/x.md'] state: active`."""
        with tempfile.TemporaryDirectory() as d:
            with bs.Store(d) as store:
                engine, run, worker, fence_uuid = self._park_then_amend(
                    store, _amend_allowed_paths(["src"]))

                engine.step("p1")

                self.assertEqual(
                    worker.calls["u1"], 1,
                    "no worker is handed a brief the LIVE contract refuses")
                self.assertNotEqual(store.get(fence_uuid).state, "active",
                                    "the fence over the forbidden path is "
                                    "parked, not left held")
                self.assertEqual(_dispatch_count(store, "u1"), 1)
                self.assertEqual(
                    store.list_dispatches("u1", raw=True)[0]["status"],
                    "REJECTED",
                    "the in-flight dispatch is CLOSED rather than left open "
                    "over a path the founder just forbade")

    def test_the_revoke_control_still_drains_with_one_worker_call(self):
        """AZ's own control C1b, which must stay green: the contract check
        at step 2 catches a REVOKE, so the narrowing case is the gap, not
        the whole branch."""
        with tempfile.TemporaryDirectory() as d:
            with bs.Store(d) as store:
                def _revoke(s):
                    s.set_contract_state("p1", "revoked", "khalil",
                                         "founder revoke", "sess1", _actor())

                engine, _run, worker, _f = self._park_then_amend(
                    store, _revoke)

                engine.step("p1")

                self.assertEqual(worker.calls["u1"], 1)
                self.assertEqual(store.get_run("p1", raw=True)["state"],
                                 "STOPPED")

    def test_a_legitimate_re_await_never_opens_a_second_dispatch_row(self):
        """At-most-once re-dispatch is preserved by the dispatch UNIQUE key
        and by the SAME dispatch_id being reused, not by trust.

        A CONTROL, green before and after: the re-await route is being
        rebuilt around the choke point, and the property it already had
        (one dispatch row per attempt, the id reused) must survive that."""
        with tempfile.TemporaryDirectory() as d:
            with bs.Store(d) as store:
                _seed(store)
                _sign(store)
                worker = FakeWorker({"u1": _worker_result(status="pending")})
                engine = _engine(store, worker, FakeCheckRunner())
                _begin_and_plan(engine, store, "p1",
                                [_unit("u1", write_scope=["a.py"])])
                engine.step("p1")
                first = store.list_dispatches("u1", raw=True)[0]["dispatch_id"]

                engine.step("p1")

                self.assertEqual(worker.calls["u1"], 2,
                                 "the re-await is a real re-ask")
                self.assertEqual(_dispatch_count(store, "u1"), 1)
                self.assertEqual(
                    store.list_dispatches("u1", raw=True)[0]["dispatch_id"],
                    first)

    def test_every_worker_handoff_sits_behind_the_one_choke_point(self):
        """A structural guard, shaped like TestNoSQLGuard above: parse
        tools/bm_controller.py and prove that `self.worker.run` appears in
        exactly two function bodies and that each of them obtains its brief
        from a call to self._authorise_dispatch. A THIRD route added later
        fails here rather than in a founder's run."""
        with io.open(CONTROLLER_FILE, encoding="utf-8") as fh:
            tree = ast.parse(fh.read(), filename="bm_controller.py")
        callers, authorised = set(), set()
        for node in ast.walk(tree):
            if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            for inner in ast.walk(node):
                if not isinstance(inner, ast.Call):
                    continue
                func = inner.func
                if not isinstance(func, ast.Attribute):
                    continue
                if (func.attr == "run"
                        and isinstance(func.value, ast.Attribute)
                        and func.value.attr == "worker"):
                    callers.add(node.name)
                if func.attr == "_authorise_dispatch":
                    authorised.add(node.name)
        self.assertEqual(
            sorted(callers), ["_resume_dispatched", "step"],
            "exactly two functions hand a brief to a worker: %r" % (callers,))
        self.assertTrue(
            callers <= authorised,
            "every function that calls the worker obtains its brief from "
            "_authorise_dispatch: callers=%r authorised=%r"
            % (sorted(callers), sorted(authorised)))


class TestR3ForeignAndCancelledDispatchIdsRefuse(unittest.TestCase):
    """REFUTATION-3 SM K and AZ F-A3, the same defect from two angles:
    receive_result took project_id and dispatch_id as INDEPENDENT arguments
    and never checked that the dispatch belonged to the named project's
    current run. It recorded the result, charged the spend against a
    contract that never authorised the work, moved the run to VERIFYING,
    and only THEN dereferenced a None unit row and raised an uncaught
    TypeError, which main() does not catch."""

    def _two_projects(self, store):
        _seed(store, "p1")
        _sign(store, "p1", token_ceiling=1000)
        _seed(store, "p2")
        _sign(store, "p2", token_ceiling=1000)
        engine1 = _engine(store, FakeWorker({}), FakeCheckRunner(),
                          controller_id="c1")
        engine1.begin("p1", "ship p1", "true")
        run1 = store.get_run("p1", raw=True)
        engine1.plan("p1", run1["run_id"], [_unit("a1", write_scope=["a.py"])])
        worker2 = FakeWorker({"b1": _worker_result(status="pending")})
        engine2 = _engine(store, worker2, FakeCheckRunner(),
                          controller_id="c2")
        engine2.begin("p2", "ship p2", "true")
        run2 = store.get_run("p2", raw=True)
        engine2.plan("p2", run2["run_id"], [_unit("b1", write_scope=["b.py"])])
        engine2.step("p2")
        foreign = store.list_dispatches("b1", raw=True)[0]["dispatch_id"]
        return engine1, run1, foreign

    def test_a_dispatch_from_another_project_refuses_and_changes_nothing(self):
        """AZ F-A3, reproduced through the real shipped CLI: project A's
        meter burned to 70 percent of its ceiling for work project B's
        contract authorised, A's run moved to VERIFYING with nothing in
        flight, B's dispatch left RESULT_IN, and a raw traceback."""
        with tempfile.TemporaryDirectory() as d:
            with bc.bs.Store(d) as store:
                engine1, run1, foreign = self._two_projects(store)
                state_before = store.get_run("p1", raw=True)["state"]

                with self.assertRaises(bc.bs.OwnershipRefused) as caught:
                    engine1.receive_result("p1", foreign, "done", ["b.py"],
                                           cost={"tokens": 700, "minutes": 5})

                self.assertEqual(caught.exception.reason, "foreign-dispatch")
                self.assertEqual(store.spend_totals("p1")["tokens"], 0,
                                 "no meter is charged against a contract "
                                 "that never authorised the work")
                self.assertEqual(store.get_run("p1", raw=True)["state"],
                                 state_before, "no run is moved")
                self.assertEqual(
                    store.get_dispatch(foreign, raw=True)["status"],
                    "DISPATCHED", "no result is recorded")

    def test_a_dispatch_from_an_earlier_terminal_run_refuses(self):
        """SM K probe p10_cross_run_dispatch.py: on the late-result path
        the crash happened AFTER record_verification and mark_unit_failed
        committed, so the old run's unit was resurrected with its retry
        count bumped and the fence park, the rollback and the interruption
        never ran."""
        with tempfile.TemporaryDirectory() as d:
            with bc.bs.Store(d) as store:
                _seed(store)
                _sign(store)
                worker = FakeWorker({"u1": _worker_result(status="pending")})
                engine = _engine(store, worker, FakeCheckRunner())
                run1 = _begin_and_plan(engine, store, "p1",
                                       [_unit("u1", write_scope=["a.py"])])
                engine.step("p1")
                old = store.list_dispatches("u1", raw=True)[0]["dispatch_id"]
                engine.stop("p1", "founder requested stop")
                self.assertEqual(store.get_run("p1", raw=True)["state"],
                                 "STOPPED")
                engine.begin("p1", "a second attempt", "true")
                run2 = store.get_run("p1", raw=True)
                self.assertNotEqual(run2["run_id"], run1["run_id"])

                with self.assertRaises(bc.bs.OwnershipRefused) as caught:
                    engine.receive_result("p1", old, "done", ["a.py"],
                                          cost={"tokens": 3})

                self.assertEqual(caught.exception.reason, "foreign-dispatch")
                self.assertEqual(store.get_run("p1", raw=True)["state"],
                                 run2["state"])
                self.assertEqual(store.spend_totals("p1")["tokens"], 0)

    def test_an_unknown_dispatch_id_refuses_rather_than_tracebacks(self):
        """main() catches bs.BMStoreError and ValueError only, so every
        refusal on this path has to be one of those, never a TypeError.

        A CONTROL: record_result already refuses 'not-found' for an id that
        names no dispatch at all, so this is green before and after. It is
        here because the two tests above INSERT a new read (get_dispatch)
        ahead of that refusal, and the unknown-id case must keep its
        existing answer rather than gain a new one."""
        with tempfile.TemporaryDirectory() as d:
            with bc.bs.Store(d) as store:
                _seed(store)
                _sign(store)
                engine = _engine(store, FakeWorker({}), FakeCheckRunner())
                _begin_and_plan(engine, store, "p1",
                                [_unit("u1", write_scope=["a.py"])])

                with self.assertRaises(bc.bs.BMStoreError) as caught:
                    engine.receive_result("p1", "no-such-dispatch", "done",
                                          [])

                self.assertEqual(caught.exception.reason, "not-found")


class TestR3LateResultKeepsItsSpendAndItsFounderRecord(unittest.TestCase):
    """REFUTATION-3 SM C (a REGRESSION) and SM D. Round 3 moved the spend
    block AFTER the late-result return, so every late result silently
    dropped its spend and the breaker under-counted by exactly that amount;
    and it put the founder step behind `if outcome['status'] != 'FAILED'`
    while _record_interruption returns None on a contract that is not live,
    so a real result at the retry ceiling was discarded with NO new
    founder-visible record at all."""

    def test_spend_lands_on_a_stopped_run(self):
        """SM C probe p14, the four-shipped-command route (`start`, `plan`,
        `step`, `stop`, `record-result --tokens 9000`): `spend tokens 0 to
        0`, so 9000 tokens of real worker cost never reached the meter the
        contract's ceiling is computed from."""
        with tempfile.TemporaryDirectory() as d:
            with bs.Store(d) as store:
                _seed(store)
                _sign(store, token_ceiling=100000)
                engine, _run, dispatch_ids = _park_one_async_unit(
                    self, store, FakeCheckRunner())
                engine.stop("p1", "founder requested stop")
                self.assertEqual(store.spend_totals("p1")["tokens"], 0)

                engine.receive_result("p1", dispatch_ids["u1"], "claims done",
                                      ["a.py"],
                                      cost={"tokens": 9000, "minutes": 120})

                self.assertEqual(store.spend_totals("p1")["tokens"], 9000,
                                 "every result path charges the meter "
                                 "exactly once, under exactly one rule")
                self.assertEqual(store.spend_totals("p1")["minutes"], 120)

    def test_a_late_result_at_the_ceiling_still_reaches_the_founder(self):
        """SM D probe p06 case B: `founder (open human steps,
        interruptions): before=(0, 0) after=(0, 0)`. The founder ran a
        worker, the worker did the work, the answer arrived, and the only
        trace was a REJECTED dispatch row nothing surfaces."""
        with tempfile.TemporaryDirectory() as d:
            with bs.Store(d) as store:
                _seed(store)
                _sign(store)
                checker = FakeCheckRunner(
                    {"check-u1": {"exit_code": 1, "stdout": "",
                                  "stderr": "never passes"}})
                worker = FakeWorker({"u1": _worker_result(status="pending")})
                engine = _engine(store, worker, checker)
                run = _begin_and_plan(
                    engine, store, "p1",
                    [_unit("u1", write_scope=["a.py"],
                           done_check="check-u1")])
                engine.step("p1")
                first = store.list_dispatches("u1", raw=True)[0]["dispatch_id"]
                engine.receive_result("p1", first, "attempt 1", ["a.py"])
                self.assertEqual(
                    _unit_row(store, run["run_id"], "u1")["retry_count"], 1)
                engine.step("p1")  # attempt 2, parks again
                second = store.list_dispatches("u1", raw=True)[-1][
                    "dispatch_id"]
                store.set_contract_state("p1", "stopped", "khalil",
                                         "founder stopped the contract",
                                         "sess1", _actor())
                engine.step("p1")  # drains: the run is now STOPPED
                self.assertEqual(store.get_run("p1", raw=True)["state"],
                                 "STOPPED")
                before = (len(store.list_human_steps("p1", resolved=False,
                                                     raw=True)),
                          len(store.list_interruptions("p1", raw=True)))

                engine.receive_result("p1", second, "attempt 2", ["a.py"])

                self.assertEqual(
                    _unit_row(store, run["run_id"], "u1")["status"], "FAILED",
                    "the retry ceiling is spent, which is the case round 3 "
                    "told the founder nothing about")
                after = (len(store.list_human_steps("p1", resolved=False,
                                                    raw=True)),
                         len(store.list_interruptions("p1", raw=True)))
                self.assertGreater(
                    after[0] + after[1], before[0] + before[1],
                    "a real result is never discarded without at least one "
                    "new founder-visible record: before=%r after=%r"
                    % (before, after))
                whats = [s["what"] for s in store.list_human_steps(
                    "p1", resolved=False, raw=True)]
                self.assertTrue(any("u1" in w for w in whats),
                                "and it names the unit: %r" % (whats,))

    def test_the_blocked_status_the_late_result_causes_is_reversible(self):
        """SM F: round 3's late-result path wrote BLOCKED directly through
        block_lane_units, with no production caller of unblock_lane_units
        anywhere, so the status was a one-way door. It is now a VIEW of the
        founder step queued in the same lane, and the same reconcile that
        derives it also reverses it."""
        with tempfile.TemporaryDirectory() as d:
            with bs.Store(d) as store:
                _seed(store)
                _sign(store)
                engine, run, dispatch_ids = _park_one_async_unit(
                    self, store, FakeCheckRunner())
                engine.stop("p1", "founder requested stop")
                engine.receive_result("p1", dispatch_ids["u1"], "claims done",
                                      ["a.py"])
                self.assertEqual(store.select_ready_units(run["run_id"]), [],
                                 "a stopped run gains no selectable work")

                for step_row in store.list_human_steps("p1", resolved=False,
                                                       raw=True):
                    store.resolve_human_step(step_row["step_id"], "p1",
                                             "founder looked at it", _actor())
                engine._reconcile_lane_blocks("p1", run["run_id"])

                self.assertNotEqual(
                    _unit_row(store, run["run_id"], "u1")["status"], "BLOCKED",
                    "resolving the step reverses the status the step "
                    "produced; nothing about it is permanent")

    def test_the_dirty_scope_warning_lands_in_the_units_own_lane(self):
        """SM observation 4: _warn_dirty_write_scope wrote into the
        hard-coded lane 'default', so a dirty rollback for a unit in lane
        'build' gated lane 'default' project wide through
        select_ready_units."""
        with tempfile.TemporaryDirectory() as d:
            with bs.Store(d) as store:
                _seed(store)
                _sign(store)
                checker = FakeCheckRunner(
                    {"git restore -- a.py": {"exit_code": 1, "stdout": "",
                                             "stderr": "rollback failed"}})
                worker = FakeWorker({"u1": _worker_result(status="pending")})
                engine = _engine(store, worker, checker)
                run = _begin_and_plan(
                    engine, store, "p1",
                    [_unit("u1", write_scope=["a.py"], lane="build")])
                engine.step("p1")
                dispatch_id = store.list_dispatches(
                    "u1", raw=True)[0]["dispatch_id"]
                engine.stop("p1", "founder requested stop")

                engine.receive_result("p1", dispatch_id, "claims done",
                                      ["a.py"])

                lanes = {s["lane"] for s in store.list_human_steps(
                    "p1", resolved=False, raw=True)
                    if "rollback" in s["what"].lower()}
                self.assertEqual(
                    lanes, {"build"},
                    "the warning gates the unit's OWN lane, never a "
                    "hard-coded one: %r" % (lanes,))


class _StateRecordingCheckRunner(FakeCheckRunner):
    """A CheckRunner that reads the run's PERSISTED state at the exact
    moment each founder-authored command runs, which is how SM probe
    p16_verifying_invariant.py measured the invariant."""

    def __init__(self, store, project_id, outcomes=None):
        FakeCheckRunner.__init__(self, outcomes)
        self._store = store
        self._project_id = project_id
        self.seen = []

    def run(self, command, cwd):
        run = self._store.get_run(self._project_id, raw=True)
        self.seen.append((command, run["state"] if run else None))
        return FakeCheckRunner.run(self, command, cwd)


class TestR3FailedRecoverableReachesVerifying(unittest.TestCase):
    """REFUTATION-3 SM I. FAILED_RECOVERABLE was in the walkable set
    because FAILED_TERMINAL is legal from it, not because any edge carried
    it to VERIFYING, so a result was judged from a run state that never
    reached VERIFYING and the run was left in FAILED_RECOVERABLE with the
    unit DONE and no delivery attempted. A crash between the done_check and
    mark_unit_done in that row leaves a durable state saying nothing was
    ever verified, which is the exact durability property the walk exists
    to provide."""

    def test_the_done_check_runs_from_verifying_and_the_run_delivers(self):
        """SM p16's fourth matrix row: `start=FAILED_RECOVERABLE ...
        [('true','FAILED_RECOVERABLE'), ('verify.sh','FAILED_RECOVERABLE')]
        final=FAILED_RECOVERABLE unit=DONE`, beside three rows that all
        reach VERIFYING and DELIVERABLE_READY."""
        with tempfile.TemporaryDirectory() as d:
            with bs.Store(d) as store:
                _seed(store)
                _sign(store)
                checker = _StateRecordingCheckRunner(store, "p1")
                worker = FakeWorker({"u1": _worker_result(status="pending")})
                engine = _engine(store, worker, checker)
                run = _begin_and_plan(
                    engine, store, "p1",
                    [_unit("u1", write_scope=["a.py"], done_check="unit-check",
                           verifier="verify.sh")])
                engine.step("p1")
                dispatch_id = store.list_dispatches(
                    "u1", raw=True)[0]["dispatch_id"]
                _move_run_along_legal_edges(store, run,
                                            ("FAILED_RECOVERABLE",))

                engine.receive_result("p1", dispatch_id, "claims done",
                                      ["a.py"])

                seen = dict(checker.seen)
                self.assertEqual(
                    seen.get("unit-check"), "VERIFYING",
                    "a recorded result implies VERIFYING BEFORE any "
                    "verification runs: %r" % (checker.seen,))
                self.assertEqual(
                    _unit_row(store, run["run_id"], "u1")["status"], "DONE")
                self.assertEqual(store.get_run("p1", raw=True)["state"],
                                 "DELIVERABLE_READY")

    def test_every_walk_edge_is_one_the_store_itself_allows(self):
        """The import-time guard, restated as a test over all three edge
        maps: a walk edge the store's own law does not allow is a defect in
        bm_controller.py and must surface at import, never as an
        OwnershipRefused in the middle of a founder's run."""
        maps = {"_RESULT_WALK_EDGES": bc._RESULT_WALK_EDGES,
                "_DISPATCH_WALK_EDGES": bc._DISPATCH_WALK_EDGES,
                "_DELIVERY_WALK_EDGES": bc._DELIVERY_WALK_EDGES}
        for name, edges in sorted(maps.items()):
            for from_state, to_state in sorted(edges.items()):
                self.assertIn(
                    to_state,
                    bs.CONTROLLER_STATE_TRANSITIONS.get(from_state, ()),
                    "%s walks %s to %s, which the store refuses"
                    % (name, from_state, to_state))
        self.assertEqual(
            bc._DISPATCH_SOURCE_STATES,
            frozenset(("READY", "CHECKPOINTED", "WAITING_HUMAN",
                       "EXECUTING")),
            "PAUSED, DELIVERABLE_READY and FAILED_RECOVERABLE are "
            "deliberately OUT of the set a dispatch may start from")


class TestR3TheClaimedCrashWindowRecovers(unittest.TestCase):
    """REFUTATION-3 LV 7. Two in-flight helpers added in one round defined
    'in flight' two different ways: _IN_FLIGHT_UNIT_STATUSES counted
    CLAIMED so the escalation abstained, while _anything_in_flight read
    DISPATCH rows and did not, so the loop parked. A crash between
    claim_unit and record_dispatch, one Store call earlier than fault 1's
    own window, therefore stranded a unit with an ACTIVE fence and no
    founder step naming it. There is now ONE predicate and it reads
    dispatch rows."""

    def _crash_window(self, store):
        """The crash window itself, built from the two Store calls the
        engine makes in order: claim the fence, link it to the unit, and
        never reach record_dispatch."""
        _seed(store)
        _sign(store)
        worker = FakeWorker({"u1": _worker_result(status="pending")})
        engine = _engine(store, worker, FakeCheckRunner())
        run = _begin_and_plan(engine, store, "p1",
                              [_unit("u1", write_scope=["a.py"])])
        record = store.claim(
            name="unit-u1", lifetime="ephemeral", files=["a.py"],
            objective="unit u1", owner="ctrl1", session_id="sess1")
        store.claim_unit("u1", record.lifecycle_uuid, _actor())
        _move_run_along_legal_edges(store, run, ("EXECUTING",))
        self.assertEqual(_unit_row(store, run["run_id"], "u1")["status"],
                         "CLAIMED")
        self.assertEqual(_dispatch_count(store, "u1"), 0)
        return engine, run, record.lifecycle_uuid

    def test_a_claimed_unit_with_no_dispatch_row_is_recovered(self):
        """LV p13: `fence STILL: active`, `open founder steps: 0`,
        `_anything_in_flight says: False`, `CLAIMED counted as in flight by
        the escalation: True`."""
        with tempfile.TemporaryDirectory() as d:
            with bs.Store(d) as store:
                engine, run, fence_uuid = self._crash_window(store)

                engine.step("p1")

                self.assertNotEqual(store.get(fence_uuid).state, "active",
                                    "the fence a crash left held is parked")
                self.assertNotEqual(
                    _unit_row(store, run["run_id"], "u1")["status"], "CLAIMED",
                    "the unit is returned to the graph, not stranded")

    def test_the_recovery_burns_no_retry_the_unit_never_earned(self):
        """Nothing was ever dispatched, so there was no attempt to fail;
        mark_unit_failed would walk the unit toward FAILED for a crash.

        Green on the untouched tree only because NOTHING touches the
        stranded unit there at all, which is the defect its sibling test
        above reproduces. It is the paired half of that recovery: the fix
        must not buy the unit's freedom with a retry it never earned."""
        with tempfile.TemporaryDirectory() as d:
            with bs.Store(d) as store:
                engine, run, _fence = self._crash_window(store)

                engine.step("p1")

                self.assertEqual(
                    _unit_row(store, run["run_id"], "u1")["retry_count"], 0)


# ---------------------------------------------------------------------------
# The end-to-end E4 fixture (design section 6).
# ---------------------------------------------------------------------------

_E4_ARTIFACT_PATH = os.path.join(
    HERE, "..", "docs", "program", "absolute-lead", "evidence", "L03",
    "E4-endtoend.json")


class TestEndToEndE4(unittest.TestCase):
    """The synthetic project: requirement interpretation, three dependent
    code changes, one parallel docs change, one deliberate test failure,
    one simulated process death, one human-gated step, final
    verification. Deterministic via FakeWorker/FakeCheckRunner, one
    throwaway store. Writes the E4 artifact to a throwaway tempfile.
    TemporaryDirectory() by default; the tracked path the design names
    is written only when a founder deliberately asks for it (see below).

    THE ARTIFACT IS REGENERATED ON EVERY RUN, BY DESIGN, so a published
    copy of E4-endtoend.json always shows the LATEST run's evidence, not
    a golden file: every value in it is read back out of the live store
    this test just drove, which is what makes it evidence at all. The
    only bytes that differ run to run are the four checkpoint_ref
    values, which are uuid4 ids the store mints per checkpoint; every
    status, state, count and verdict in the file is stable, and the
    assertions below key on that structure, never on those ids. Making
    the ids deterministic would mean writing checkpoint ids this run did
    not actually produce, which would weaken the evidence rather than
    stabilise it. That reasoning was never reconciled with
    scripts/verify-install.sh's signed manifest check (M13): writing the
    regenerated bytes to a TRACKED path on every run made every ordinary
    test run fail the manifest comparison, so the default write now
    targets a throwaway directory and the tracked path is opt in, set
    BM_WRITE_E4_EVIDENCE=1 to also write
    docs/program/absolute-lead/evidence/L03/E4-endtoend.json exactly as
    before, for whoever is deliberately publishing this run as evidence."""

    def test_the_synthetic_project_ends_deliverable_ready_with_no_duplicate_work(self):
        with tempfile.TemporaryDirectory() as d:
            with bs.Store(d) as store:
                _seed(store)
                _sign(store, token_ceiling=100000, minutes_ceiling=1000)

                # U2's own done_check fails on attempt 1, passes on
                # attempt 2 (the deliberate test failure, exercising the
                # circuit breaker and a successful single retry).
                checker = FakeCheckRunner({
                    "test -f build.log": lambda n: (
                        {"exit_code": 1, "stdout": "", "stderr": "boom"}
                        if n == 1 else
                        {"exit_code": 0, "stdout": "", "stderr": ""}),
                })

                worker = FakeWorker({
                    "u1": _worker_result(claim="created widget.py",
                                         artifacts=["widget.py"]),
                    "u2": _worker_result(claim="edited widget.py",
                                         artifacts=["widget.py"]),
                    "u3": _worker_result(claim="built the widget",
                                         artifacts=["build.log"]),
                    "u4": _worker_result(claim="wrote docs",
                                         artifacts=["README.md"]),
                })

                engine = _engine(store, worker, checker, controller_id="ctrl1")
                run = engine.begin(
                    "p1", "ship the widget with docs",
                    "test -f build.log && test -f README.md",
                    workflow_version=1)

                # Requirement interpretation: ORIENTING/PLANNING turns the
                # deliberately ambiguous outcome into a five-unit graph.
                # The "navigator" role's job (producing this graph) is the
                # one judgement the design leaves to the builder; here the
                # test harness plays that role deterministically.
                units = [
                    _unit("u1", write_scope=["widget.py"], lane="code"),
                    _unit("u2", dependencies=["u1"], read_scope=["widget.py"],
                         write_scope=["widget.py"], lane="code"),
                    _unit("u3", dependencies=["u2"], read_scope=["widget.py"],
                         write_scope=["build.log"], lane="code",
                         done_check="test -f build.log"),
                    _unit("u4", write_scope=["README.md"], lane="docs"),
                ]
                # U5 (publish-release) is a floor action. "publish-release"
                # is a FLOOR ID, not a risk class: upsert_units refuses it
                # ('risk-class-is-floor'), the store's own I5 invariant
                # ("a floor id smuggled into risk_classes is unreachable").
                # A floor action therefore has NO controller_unit row at
                # all; it exists purely as a queued human step, exactly
                # the design's own "never executed; queued via
                # queue_human_step" framing. Its `blocks` list names a
                # real Loop-1 task (the design's own "blocks=[U3 task]"),
                # not a controller unit: queue_human_step refuses any id
                # in `blocks` that names no real task in the project.
                store.create_task(
                    {"task_id": "u3-release-task", "project_id": "p1",
                     "title": "ship the built widget", "status": "planned"},
                    _actor())
                engine.plan("p1", run["run_id"], units)
                store.queue_human_step(
                    "p1", "publish-release", "release",
                    "click publish in the dashboard", "",
                    ["u3-release-task"], "sess1", _actor())

                states_visited = [store.get_run("p1", raw=True)["state"]]

                # Wave 1: u1 (code, no deps) and u4 (docs, independent)
                # are both selectable; parallel-fenced.
                s1 = engine.step("p1")
                states_visited.append(s1["state"])
                self.assertEqual(sorted(s1["dispatched"]), ["u1", "u4"])
                self.assertEqual(
                    _unit_row(store, run["run_id"], "u4")["status"], "DONE",
                    "the docs lane completed while release stays blocked")

                # Wave 2: u2 becomes selectable.
                s2 = engine.step("p1")
                states_visited.append(s2["state"])
                self.assertEqual(s2["dispatched"], ["u2"])

                # Wave 3: u3 becomes selectable. Its done_check FAILS on
                # attempt 1 (the deliberate test failure).
                s3 = engine.step("p1")
                states_visited.append(s3["state"])
                self.assertEqual(s3["dispatched"], ["u3"])
                u3_after_attempt1 = _unit_row(store, run["run_id"], "u3")
                self.assertEqual(u3_after_attempt1["status"], "READY",
                                 "one retry, a different attempt, not a "
                                 "third identical one")
                self.assertEqual(u3_after_attempt1["retry_count"], 1)

                # Simulated process death: discard and rebuild the engine
                # between u3's dispatch and its checkpoint. Resume must
                # not re-dispatch u3 a THIRD time (it already has
                # retry_count=1; the resumed engine's retry is attempt 2).
                # The SAME driver restarted, so the same session id (L09
                # GAP 2: a run has one driver, and a genuinely new
                # session would adopt first).
                engine = _engine(store, worker, checker, controller_id="ctrl1",
                                 session_id=engine.session_id)
                s4 = engine.step("p1")  # attempt 2: done_check now passes
                states_visited.append(s4["state"])
                self.assertEqual(
                    _unit_row(store, run["run_id"], "u3")["status"], "DONE")
                self.assertEqual(
                    _dispatch_count(store, "u3"), 2,
                    "exactly two dispatches for u3 (attempt 1, attempt "
                    "2), never a third or a duplicate of either")

                trace = engine.run_to_completion("p1")
                for s in trace:
                    states_visited.append(s["state"])
                final_state = store.get_run("p1", raw=True)["state"]

                self.assertEqual(
                    final_state, "DELIVERABLE_READY",
                    "not COMPLETE: the publish-release floor step remains "
                    "founder-gated")

                # Zero duplicate completed units: a query counting DONE
                # transitions per unit returns exactly one each.
                duplicate_work_count = 0
                unit_rows = store.list_units(run["run_id"], raw=True)
                for u in unit_rows:
                    dispatches = store.list_dispatches(u["unit_id"], raw=True)
                    done_dispatches = [dd for dd in dispatches
                                       if dd["status"] == "VERIFIED"]
                    self.assertLessEqual(
                        len(done_dispatches), 1,
                        "unit %s has more than one VERIFIED dispatch"
                        % u["unit_id"])
                    if len(done_dispatches) > 1:
                        duplicate_work_count += len(done_dispatches) - 1
                self.assertEqual(duplicate_work_count, 0)

                for u in unit_rows:
                    self.assertEqual(
                        u["status"], "DONE",
                        "unit %s must be DONE at delivery" % u["unit_id"])

                spend = store.spend_totals("p1")
                self.assertNotEqual(spend["verdict"], "hard-stop",
                                    "spend stayed under the contract "
                                    "ceiling")

                open_steps = store.list_human_steps(
                    "p1", resolved=False, raw=True)
                self.assertEqual(len(open_steps), 1)

                checkpoints = store.recent_checkpoints(
                    "p1", limit=200, raw=True)
                interruption_kinds = [
                    c for c in checkpoints if c["kind"] == "heartbeat"]
                self.assertGreater(
                    len(interruption_kinds), 0,
                    "the heartbeat trail reports the run's own progress, "
                    "including the one simulated death")

                artifact = {
                    "run_states_visited": states_visited,
                    "units": [
                        {"unit_id": u["unit_id"], "status": u["status"],
                         "checkpoint_ref": u["checkpoint_ref"]}
                        for u in unit_rows],
                    "duplicate_work_count": duplicate_work_count,
                    "founder_gated_remainder": {
                        "open_human_steps": len(open_steps),
                        "blocked_task_ids": open_steps[0]["blocks"]
                                           if open_steps else []},
                    "spend_totals": spend,
                    "interruption_count": len(
                        store.list_interruptions("p1")),
                    "final_state": final_state,
                }
                # Default target is a throwaway directory, never the
                # tracked evidence path (M13: a tracked file that changes
                # on every run fails scripts/verify-install.sh's manifest
                # comparison for every ordinary test run).
                with tempfile.TemporaryDirectory() as evidence_dir:
                    artifact_path = os.path.join(
                        evidence_dir, "E4-endtoend.json")
                    with io.open(artifact_path, "w", encoding="utf-8") as fh:
                        json.dump(artifact, fh, indent=2, sort_keys=True)
                        fh.write("\n")

                    if os.environ.get("BM_WRITE_E4_EVIDENCE") == "1":
                        # Opt in publish path, off by default: the same
                        # bytes, ALSO written to the tracked path the
                        # design names, for whoever is deliberately
                        # publishing this run as evidence rather than
                        # merely running the suite.
                        tracked_path = os.path.abspath(_E4_ARTIFACT_PATH)
                        os.makedirs(os.path.dirname(tracked_path),
                                    exist_ok=True)
                        with io.open(tracked_path, "w",
                                     encoding="utf-8") as fh:
                            json.dump(artifact, fh, indent=2, sort_keys=True)
                            fh.write("\n")

                    # The artifact IS the assertion source: re-read it and
                    # assert against the file, so the artifact and the pass
                    # condition cannot diverge.
                    with io.open(artifact_path, encoding="utf-8") as fh:
                        reloaded = json.load(fh)
                    self.assertEqual(reloaded["final_state"],
                                      "DELIVERABLE_READY")
                    self.assertEqual(reloaded["duplicate_work_count"], 0)
                    self.assertEqual(
                        reloaded["founder_gated_remainder"]
                        ["open_human_steps"], 1)


# ---------------------------------------------------------------------------
# ROUND 5: the findings a FOURTH adversarial pass raised against the round-4
# fixes above (reports
# docs/program/absolute-lead/evidence/L03/REFUTATION-4-authorization.md and
# -liveness.md, 2026-08-05). Every class below is one of those reports' own
# probe sequences restated as a permanent regression test, and every one of
# them FAILED on the tree those reports attacked (evidence:
# docs/program/absolute-lead/evidence/L03/RED-round5-controller.txt).
# ---------------------------------------------------------------------------

def _set_contract(store, state, project_id="p1", reason="founder action"):
    """The founder's own contract move, performed the way
    tools/bm_autonomy.py's pause/resume/stop/revoke perform it: straight
    through set_contract_state, which appends a NEW revision every time and
    copies every authorisation column forward verbatim."""
    return store.set_contract_state(project_id, state, "khalil", reason,
                                    "sess1", _actor())


def _checker_call_site_functions():
    """Every enclosing function name of a `self.checker.run(...)` call in
    tools/bm_controller.py, by AST rather than by grep."""
    with io.open(CONTROLLER_FILE, encoding="utf-8") as fh:
        tree = ast.parse(fh.read(), filename="bm_controller.py")
    names, stack = set(), []

    class _Visitor(ast.NodeVisitor):
        def visit_FunctionDef(self, node):
            stack.append(node.name)
            self.generic_visit(node)
            stack.pop()

        def visit_Call(self, node):
            func = node.func
            if (isinstance(func, ast.Attribute) and func.attr == "run"
                    and isinstance(func.value, ast.Attribute)
                    and func.value.attr == "checker"
                    and isinstance(func.value.value, ast.Name)
                    and func.value.value.id == "self"):
                names.add(stack[-1] if stack else "<module>")
            self.generic_visit(node)

    _Visitor().visit(tree)
    return names


# ---------------------------------------------------------------------------
# ROUND 6 / REFUTATION-5-safety.md S5: the structural control the round-5
# guard above only claimed to be. That one matched a SINGLE AST shape,
# `self.checker.run`, so four ungated command sites walked past it. This
# one asks the question the claim actually makes: can any subprocess
# execution primitive be reached from anywhere in tools/bm_controller.py
# except the one runner method, and can the checker object be reached by
# any spelling other than the one gated call?
#
# The names below are the ONLY places in that module where an execution
# primitive is allowed to appear, by qualified name.
# ---------------------------------------------------------------------------

#: `subprocess.*` is legal in exactly two places: the production
#: CheckRunner, whose whole job is to be the process boundary for
#: FOUNDER-AUTHORED commands, and _default_fence_canary_runner (added
#: 2026-08-10, the unattended preflight's fence canary), whose argv is a
#: fixed literal this module writes and never founder- or model-authored
#: text. Two different homes, two different reasons a command site there
#: is safe; a third one anywhere else is still an offence.
_EXEC_PRIMITIVE_HOMES = frozenset((
    "SubprocessCheckRunner.run",
    "_default_fence_canary_runner",
))

#: `self.checker` is legal in exactly two places: bound once in the
#: engine's constructor, and called once behind the authorisation gate.
_CHECKER_BIND_SITE = "ControllerEngine.__init__"
_CHECKER_CALL_SITE = "ControllerEngine._run_command"

#: The runner CLASS may be named in exactly one place: the CLI factory that
#: constructs the one engine, `_engine`. Found by this writer attacking its
#: own round-7 guard (HUNT-14): every rule above gates the checker OBJECT
#: and the primitives, and none of them stopped
#: `SubprocessCheckRunner().run(cmd, cwd='.')`, which builds a second
#: runner and calls it with no gate in front of it at all. The class
#: definition itself is excluded by name, since it must obviously appear
#: there.
_RUNNER_CLASS = "SubprocessCheckRunner"
_RUNNER_CONSTRUCTION_SITE = "_engine"

#: Dotted calls that start a process without going through the CheckRunner
#: at all. `os.system` needs no import bm_controller.py does not already
#: have, which is what made it the cheapest of the refuter's four
#: bypasses.
_BANNED_DOTTED_CALLS = frozenset((
    "os.system", "os.popen", "os.execv", "os.execve", "os.execvp",
    "os.execvpe", "os.execl", "os.execle", "os.execlp", "os.execlpe",
    "os.spawnv", "os.spawnve", "os.spawnvp", "os.spawnvpe", "os.spawnl",
    "os.spawnle", "os.spawnlp", "os.spawnlpe", "os.posix_spawn",
    "os.posix_spawnp", "os.fork", "os.forkpty", "os.openpty",
    "os.startfile", "pty.spawn", "pty.fork", "platform.popen",
    "commands.getoutput", "commands.getstatusoutput",
    "asyncio.create_subprocess_exec", "asyncio.create_subprocess_shell",
    # REFUTATION-6 F3. The refuter's NEW-D reached the gated attribute as a
    # STRING rather than as an ast.Attribute, so neither the dotted-name
    # test nor the self.checker test saw it. These three are the standard
    # ways to turn a name or an object into a call without writing either.
    "operator.attrgetter", "operator.methodcaller", "operator.itemgetter",
    "functools.partial", "functools.partialmethod",
    "importlib.import_module",
))

#: Bare builtins that turn a name into an object at runtime, which is how
#: a gated attribute is reached without ever writing its name. Banning
#: them outright is cheap here: the shipped module uses none of them.
_BANNED_BARE_CALLS = frozenset((
    "getattr", "setattr", "delattr", "vars", "globals", "locals",
    "eval", "exec", "compile", "__import__",
))

#: MODULE NAMES REACHED AS AN ATTRIBUTE, which is the tenth spelling this
#: writer found by attacking its own round-7 guard rather than round 6's.
#: tools/bm_controller.py loads tools/bm_store.py by path and binds it as
#: `bs`, and bm_store.py imports `os` (and more), so `bs.os.system(cmd)` is
#: a working ungated command site that the alias resolution above cannot
#: see: `bs` is not an import binding, it is the return value of a
#: function, so nothing in the source says what it holds. Banning the
#: MODULE NAME wherever it appears as an attribute closes the reachable
#: half of that hop. The other half stays open and is disclosed in
#: docs/KNOWN-LIMITS.md: a sibling module could grow a FUNCTION of its own
#: that starts a process, and calling that would look like any other store
#: call. The shipped module reaches none of these as an attribute
#: (`importlib.util` and `os.path` are a NAME followed by an attribute,
#: which is the other way round).
_BANNED_MODULE_ATTRIBUTE_NAMES = frozenset((
    "subprocess", "os", "pty", "asyncio", "operator", "functools",
    "importlib", "builtins", "ctypes", "multiprocessing", "commands",
    "platform", "shutil", "signal",
))

#: Names that ARE a namespace: reaching either one hands back every
#: builtin, `getattr` and `eval` included, without any of them ever
#: appearing as a name (REFUTATION-6 F3, this writer's own HUNT-2). The
#: shipped module uses neither.
_BANNED_NAMESPACE_ROOTS = frozenset(("__builtins__", "builtins"))

#: Attribute names that reach an object's own namespace, the second way to
#: get at a gated attribute without naming it.
_BANNED_ATTRIBUTE_NAMES = frozenset((
    "__dict__", "__getattribute__", "__class__", "__globals__",
))


#: The COMPLETE import list tools/bm_controller.py is allowed to have
#: (REFUTATION-6-pushgate.md F3). Round 6's guard read no ast.Import and no
#: ast.ImportFrom at all, so `import subprocess as sp` and
#: `from os import system as X` walked straight past a name test that only
#: knew the literal spellings. Pinning the whole list by name is the
#: cheapest total control available here and the refuter's own suggestion:
#: an execution primitive that needs an import cannot get one, whatever it
#: calls itself. Hard-coded on purpose, so a future import is a deliberate
#: edit to this line rather than a silent widening.
_ALLOWED_MODULE_IMPORTS = frozenset((
    "datetime", "importlib.util", "json", "os", "shlex", "subprocess",
    "sys", "tempfile", "uuid",
))


def _module_import_names(source):
    """Every import binding anywhere in `source`, sorted, spelled so an
    ALIASED or `from`-style binding can never compare equal to a plain
    allowed import: 'os', 'subprocess as sp', 'from os import system as X'.
    ast.walk, not tree.body, so an import hidden inside a function body is
    seen exactly like a module-level one."""
    tree = ast.parse(source, filename="bm_controller.py")
    names = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                names.append(alias.name if alias.asname is None
                             else "%s as %s" % (alias.name, alias.asname))
        elif isinstance(node, ast.ImportFrom):
            for alias in node.names:
                names.append(
                    "from %s import %s%s"
                    % (node.module or ".", alias.name,
                       "" if alias.asname is None else " as " + alias.asname))
    return sorted(names)


def _import_alias_targets(tree):
    """{local name: the fully qualified thing it is bound to} for every
    import binding in `tree`, so a dotted chain can be resolved back to
    what it REALLY names before any spelling is tested. `import subprocess
    as sp` binds sp -> subprocess; `from subprocess import run as X` binds
    X -> subprocess.run; `import importlib.util` binds importlib ->
    importlib, which is the name Python actually puts in the namespace."""
    aliases = {}
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                if alias.asname:
                    aliases[alias.asname] = alias.name
                else:
                    root = alias.name.split(".")[0]
                    aliases[root] = root
        elif isinstance(node, ast.ImportFrom):
            for alias in node.names:
                target = ("%s.%s" % (node.module, alias.name)
                          if node.module else alias.name)
                aliases[alias.asname or alias.name] = target
    return aliases


def _resolved_dotted(dotted, aliases):
    """`dotted` with its FIRST segment replaced by whatever that name is
    really bound to, which is what turns 'sp.run' into 'subprocess.run' and
    a bare 'X' into 'os.system'."""
    if dotted is None:
        return None
    parts = dotted.split(".")
    target = aliases.get(parts[0])
    if target is None:
        return dotted
    return ".".join([target] + parts[1:])


def _dotted_name(node):
    """'os.system' for `os.system`, 'self.checker.run' for
    `self.checker.run`, or None for anything that is not a plain dotted
    chain of names."""
    parts = []
    while isinstance(node, ast.Attribute):
        parts.append(node.attr)
        node = node.value
    if not isinstance(node, ast.Name):
        return None
    parts.append(node.id)
    return ".".join(reversed(parts))


def _execution_primitive_offences(source):
    """[(lineno, message), ...] for every place in `source` where a
    subprocess execution primitive is reachable outside one of
    _EXEC_PRIMITIVE_HOMES, or where the checker object is reached by any
    spelling other than the one gated call.

    Takes SOURCE rather than reading the file, so the calibration test can
    hand it a mutated copy held in memory. Nothing is written and nothing
    is executed.

    ROUND 7 (REFUTATION-6-pushgate.md F3). The round-6 version built a
    name from the LOCAL identifier and then compared it against literal
    spellings, and read no ast.Import or ast.ImportFrom at all, so five
    more spellings passed it and four of those really started a process
    under a revoked contract. Four rules were added, in the order they
    kill the most:

      1. THE IMPORT LIST IS PINNED. Every binding, anywhere in the file
         including inside a function body, must be a plain unaliased
         import of a module in _ALLOWED_MODULE_IMPORTS. That alone kills
         `import subprocess as sp`, `from os import system as X` and every
         primitive that needs a module the file does not already have
         (operator, functools, pty, multiprocessing, ctypes, asyncio).
      2. NAMES ARE RESOLVED before they are tested, so a rebinding that
         gets past rule 1 is still read as what it really names.
      3. A BANNED NAME IS AN OFFENCE WHEREVER IT APPEARS, not only where
         it is CALLED: `runner = os.system` followed by `runner(cmd)` is
         two ordinary-looking statements and one ungated process.
      4. THE `checker` ATTRIBUTE IS GATED ON ITS NAME, whatever object it
         hangs off, because `holder = self` is the same bypass as
         `runner = self.checker` with one more step in it.

    WHAT THIS DOES NOT COVER, stated rather than implied: the module's own
    `_load` helper (importlib.util.spec_from_file_location plus
    spec.loader.exec_module) executes a sibling .py file by path, and it
    is how every tool in this repository loads tools/bm_store.py. A future
    edit could reach a command through a sibling module rather than
    through a primitive named here, and this guard would not see it. That
    is a boundary of the AST approach, not a hole this round left open by
    accident; docs/KNOWN-LIMITS.md says so in the founder's words."""
    tree = ast.parse(source, filename="bm_controller.py")
    parent = {}
    for node in ast.walk(tree):
        for child in ast.iter_child_nodes(node):
            parent[child] = node
    aliases = _import_alias_targets(tree)
    offences = []
    stack = []

    def _qualname():
        return ".".join(stack)

    def _named(node, dotted, where):
        """Rules 2 and 3: the SAME test for a name that is CALLED and for
        one merely mentioned, because a mention is one statement away from
        a call."""
        if dotted is None:
            return
        resolved = _resolved_dotted(dotted, aliases)
        if (resolved.split(".")[0] == "subprocess"
                and where not in _EXEC_PRIMITIVE_HOMES):
            offences.append(
                (node.lineno,
                 "%s (which really names %s) is reached in %s; the only "
                 "places this module may start a process are %s"
                 % (dotted, resolved, where or "<module>",
                    ", ".join(sorted(_EXEC_PRIMITIVE_HOMES)))))
        if resolved in _BANNED_DOTTED_CALLS:
            offences.append(
                (node.lineno,
                 "%s (which really names %s) starts a process, or turns a "
                 "name into one, outside the CheckRunner entirely (in %s)"
                 % (dotted, resolved, where or "<module>")))

    class _Visitor(ast.NodeVisitor):
        def visit_ClassDef(self, node):
            stack.append(node.name)
            self.generic_visit(node)
            stack.pop()

        def visit_FunctionDef(self, node):
            stack.append(node.name)
            self.generic_visit(node)
            stack.pop()

        visit_AsyncFunctionDef = visit_FunctionDef

        def visit_Import(self, node):
            for alias in node.names:
                if alias.asname or alias.name not in _ALLOWED_MODULE_IMPORTS:
                    offences.append(
                        (node.lineno,
                         "import %s%s is not one of this module's pinned "
                         "imports (%s); an execution primitive that needs "
                         "an import may not have one"
                         % (alias.name,
                            "" if alias.asname is None
                            else " as " + alias.asname,
                            ", ".join(sorted(_ALLOWED_MODULE_IMPORTS)))))
            self.generic_visit(node)

        def visit_ImportFrom(self, node):
            offences.append(
                (node.lineno,
                 "from %s import ... rebinds a module member under a local "
                 "name, which is exactly how `from subprocess import run as "
                 "X` walked past the round-6 guard; this module imports "
                 "whole modules only" % (node.module or ".",)))
            self.generic_visit(node)

        def visit_Name(self, node):
            where = _qualname()
            if (node.id == _RUNNER_CLASS
                    and where not in (_RUNNER_CONSTRUCTION_SITE,
                                      _RUNNER_CLASS)):
                offences.append(
                    (node.lineno,
                     "%s is named in %s; a second runner built anywhere but "
                     "%s has no authorisation gate in front of it"
                     % (_RUNNER_CLASS, where or "<module>",
                        _RUNNER_CONSTRUCTION_SITE)))
            if node.id in _BANNED_NAMESPACE_ROOTS:
                offences.append(
                    (node.lineno,
                     "%s in %s IS a namespace: it hands back every builtin "
                     "without naming any of them"
                     % (node.id, where or "<module>")))
            if node.id in _BANNED_BARE_CALLS:
                offences.append(
                    (node.lineno,
                     "%s in %s can reach a gated attribute without naming "
                     "it" % (node.id, where or "<module>")))
            if isinstance(parent.get(node), ast.Attribute):
                return   # judged as part of its whole dotted chain instead
            _named(node, node.id, where)
            self.generic_visit(node)

        def visit_Call(self, node):
            _named(node, _dotted_name(node.func), _qualname())
            self.generic_visit(node)

        def visit_Attribute(self, node):
            where = _qualname()
            if node.attr in _BANNED_ATTRIBUTE_NAMES:
                offences.append(
                    (node.lineno,
                     "%s in %s reaches an object's own namespace"
                     % (node.attr, where or "<module>")))
            if node.attr in _BANNED_MODULE_ATTRIBUTE_NAMES:
                offences.append(
                    (node.lineno,
                     "the module %r is reached as an ATTRIBUTE in %s, which "
                     "borrows another module's import of it and resolves "
                     "through no import in this file"
                     % (node.attr, where or "<module>")))
            if not isinstance(parent.get(node), ast.Attribute):
                _named(node, _dotted_name(node), where)
            # Rule 4: the ATTRIBUTE NAME is what is gated, whatever object
            # it hangs off, so `holder = self; holder.checker.run(...)` is
            # an offence too (REFUTATION-6 F3, this writer's HUNT-1).
            if node.attr == "checker":
                up = parent.get(node)
                on_self = (isinstance(node.value, ast.Name)
                           and node.value.id == "self")
                bound = (on_self and where == _CHECKER_BIND_SITE
                         and isinstance(up, ast.Assign)
                         and node in up.targets)
                called = (on_self and where == _CHECKER_CALL_SITE
                          and isinstance(up, ast.Attribute)
                          and up.attr == "run"
                          and isinstance(parent.get(up), ast.Call)
                          and parent[up].func is up)
                if not (bound or called):
                    offences.append(
                        (node.lineno,
                         "the checker attribute is reached in %s; it may "
                         "only be bound as self.checker in %s and called as "
                         "self.checker.run(...) in %s"
                         % (where or "<module>", _CHECKER_BIND_SITE,
                            _CHECKER_CALL_SITE)))
            self.generic_visit(node)

    _Visitor().visit(tree)
    # set(): a call whose func is a dotted chain is judged once as a Call
    # and once as the Attribute at its head, deliberately (a name that is
    # merely BOUND must be caught too), which reports the identical
    # (lineno, message) twice. The duplicate is noise in a failure a reader
    # has to act on, so it is collapsed here rather than at either site.
    return sorted(set(offences))


class TestR5NoCommandRunsUnderADeadContract(unittest.TestCase):
    """REFUTATION-4 AZ F5 (HIGH, SAFETY). After the 3am kill switch
    (`bm-autonomy stop`, or `revoke`), one `bm-controller record-result`
    still executed the unit's own MODEL-AUTHORED done_check AND a
    `git restore` rollback in the project root, and one plain
    `bm-controller step` did the same through the RESULT_IN resume branch,
    which by design runs BEFORE the live-contract read. Round 4's section
    10.6 is what ADDED the rollback to that branch.

    The rule this class pins: no engine path executes ANY subprocess (a
    done_check, a verifier, a rollback, or the founder's done-definition)
    unless the contract is live at the MOMENT of execution, read
    immediately before."""

    def test_record_result_after_bm_autonomy_stop_runs_no_command(self):
        """AZ probe p12, four shipped commands and no crash: start, plan,
        step, `bm-autonomy stop`, `bm-controller record-result`."""
        with tempfile.TemporaryDirectory() as d:
            with bs.Store(d) as store:
                _seed(store)
                _sign(store)
                checker = FakeCheckRunner()
                engine, run, dispatch_ids = _park_one_async_unit(
                    self, store, checker)
                fence_uuid = _unit_row(store, run["run_id"],
                                       "u1")["fence_uuid"]
                _set_contract(store, "stopped", reason="3am kill switch")
                before = list(checker.calls)

                outcome = engine.receive_result(
                    "p1", dispatch_ids["u1"], "claims done", ["a.py"])

                self.assertEqual(
                    checker.calls, before,
                    "the kill switch stops EVERY command, including the "
                    "unit's own model-authored done_check and the rollback: "
                    "%r" % (checker.calls,))
                self.assertEqual(outcome, "rejected")
                self.assertEqual(store.get(fence_uuid).state, "parked",
                                 "the fence is parked, never left active")
                steps = store.list_human_steps("p1", resolved=False, raw=True)
                self.assertTrue(
                    any("u1" in s["what"] and "stopped" in s["what"]
                        for s in steps),
                    "a founder step names the unit and the dead contract: "
                    "%r" % ([s["what"] for s in steps],))

    def test_step_on_a_result_in_dispatch_after_a_revoke_runs_no_command(self):
        """AZ probe p11: plain `bm-controller step`.
        _resume_result_in_and_orphans runs BEFORE the live-contract read by
        design (step 1 before step 2), goes straight to _verify_and_finish
        and executed the same two commands."""
        with tempfile.TemporaryDirectory() as d:
            with bs.Store(d) as store:
                _seed(store)
                _sign(store)
                checker = FakeCheckRunner()
                engine, run, dispatch_ids = _park_one_async_unit(
                    self, store, checker)
                fence_uuid = _unit_row(store, run["run_id"],
                                       "u1")["fence_uuid"]
                store.record_result(dispatch_ids["u1"], "claims done",
                                    ["a.py"], _actor())
                _set_contract(store, "revoked", reason="founder revoke")
                before = list(checker.calls)

                summary = engine.step("p1")

                self.assertEqual(checker.calls, before,
                                 "zero commands: %r" % (checker.calls,))
                self.assertEqual(summary["stop_reason"], "CONTRACT_NOT_LIVE")
                self.assertIn("revoked", summary["note"])
                self.assertEqual(store.get(fence_uuid).state, "parked")

    def test_every_checker_call_sits_behind_one_gated_call_site(self):
        """The structural half, so a THIRD command site added later cannot
        quietly reopen this: every `self.checker.run` in the engine sits
        inside ONE method, and that method reads the contract first."""
        self.assertEqual(
            _checker_call_site_functions(), {"_run_command"},
            "every founder-authored command runs through the one gated "
            "call site")


class TestR5TheHeldAnswerSurvivesAPausedContract(unittest.TestCase):
    """REFUTATION-4 AZ F4 (HIGH). The PAUSED hold worked only for a run
    reached through a route no shipped command has (a direct
    set_run_state). On the ONLY shipped route, the CONTRACT being paused,
    the held answer was REJECTED as stale on resume, ran `git restore`
    against the founder's files and burned a retry, which is exactly what
    design 18.1 says the hold removed."""

    def _park_under_a_paused_contract(self, store, checker):
        engine, run, dispatch_ids = _park_one_async_unit(self, store, checker)
        _set_contract(store, "paused", reason="founder pressed pause")
        engine.step("p1")
        self.assertEqual(store.get_run("p1", raw=True)["state"], "PAUSED",
                         "the only shipped route to a PAUSED run")
        return engine, run, dispatch_ids

    def test_a_result_arriving_under_a_paused_contract_is_held(self):
        with tempfile.TemporaryDirectory() as d:
            with bs.Store(d) as store:
                _seed(store)
                _sign(store)
                checker = FakeCheckRunner()
                engine, run, dispatch_ids = self._park_under_a_paused_contract(
                    store, checker)
                fence_uuid = _unit_row(store, run["run_id"],
                                       "u1")["fence_uuid"]
                before = list(checker.calls)

                outcome = engine.receive_result(
                    "p1", dispatch_ids["u1"], "claims done", ["a.py"],
                    cost={"tokens": 11, "minutes": 3})

                self.assertEqual(outcome, "held")
                self.assertEqual(checker.calls, before,
                                 "nothing is judged and nothing is rolled "
                                 "back: %r" % (checker.calls,))
                self.assertEqual(store.get(fence_uuid).state, "active")
                self.assertEqual(
                    _unit_row(store, run["run_id"], "u1")["retry_count"], 0,
                    "a pause burns no retry")
                kinds = [c["kind"] for c in store.recent_checkpoints(
                    "p1", limit=200, raw=True)]
                self.assertIn(
                    "spend-uncharged", kinds,
                    "record_spend refuses on a contract that is not live, so "
                    "the charge this result earned is DISCLOSED rather than "
                    "silently dropped: %r" % (kinds,))

    def test_the_held_answer_is_accepted_after_the_founder_resumes(self):
        """A pause and a resume append two revisions that copy every
        authorisation column forward verbatim, so the dispatch's own
        stamped revision is still the authorisation it was granted."""
        with tempfile.TemporaryDirectory() as d:
            with bs.Store(d) as store:
                _seed(store)
                _sign(store)
                checker = FakeCheckRunner()
                engine, run, dispatch_ids = self._park_under_a_paused_contract(
                    store, checker)
                engine.receive_result("p1", dispatch_ids["u1"], "claims done",
                                      ["a.py"], cost={"tokens": 11})

                _set_contract(store, "live", reason="founder resumed")
                _resume_the_run(store, run)
                engine.step("p1")

                self.assertEqual(
                    _unit_row(store, run["run_id"], "u1")["status"], "DONE",
                    "the SAME held answer is accepted, not rejected as "
                    "stale")
                self.assertEqual(
                    [c for c in checker.calls if c.startswith("git restore")],
                    [], "no rollback ever ran against the founder's files")
                self.assertEqual(
                    _unit_row(store, run["run_id"], "u1")["retry_count"], 0)

    def test_a_real_amend_across_the_pause_is_still_stale(self):
        """THE CONTROL. A supersede amend is change_kind 'amend' and DOES
        move the authorisation, so it must still reject."""
        with tempfile.TemporaryDirectory() as d:
            with bs.Store(d) as store:
                _seed(store)
                _sign(store)
                checker = FakeCheckRunner()
                engine, run, dispatch_ids = self._park_under_a_paused_contract(
                    store, checker)
                engine.receive_result("p1", dispatch_ids["u1"], "claims done",
                                      ["a.py"])

                _set_contract(store, "live", reason="founder resumed")
                _amend_allowed_paths(["docs"])(store)
                _resume_the_run(store, run)
                engine.step("p1")

                self.assertNotEqual(
                    _unit_row(store, run["run_id"], "u1")["status"], "DONE",
                    "a real narrowing is still an authorisation that moved")
                dispatch = store.list_dispatches("u1", raw=True)[0]
                self.assertEqual(dispatch["status"], "REJECTED")


class TestR5AContractPauseIsNotARunPause(unittest.TestCase):
    """REFUTATION-4 LV L4-F2 (HIGH). A run paused because the CONTRACT is
    paused printed _NOTE_RUN_PAUSED, whose only instruction is
    `bm-controller resume`. Following it loops forever: resume, step,
    PAUSED, resume. Nothing in the controller's output ever named
    `bm-autonomy resume`, the command that actually clears it."""

    def test_a_contract_paused_run_names_the_command_that_clears_it(self):
        with tempfile.TemporaryDirectory() as d:
            with bs.Store(d) as store:
                _seed(store)
                _sign(store)
                engine = _engine(store, FakeWorker({}), FakeCheckRunner())
                _begin_and_plan(engine, store, "p1",
                                [_unit("u1", write_scope=["a.py"])])
                _set_contract(store, "paused")

                first = engine.step("p1")
                second = engine.step("p1")

                for summary in (first, second):
                    self.assertEqual(summary["stop_reason"],
                                     "CONTRACT_PAUSED",
                                     "the enum distinguishes a contract "
                                     "pause from a run pause: %r" % (summary,))
                    self.assertIn("bm-autonomy resume", summary["note"])
                self.assertEqual(second["state"], "PAUSED")

    def test_following_the_note_actually_clears_the_pause(self):
        with tempfile.TemporaryDirectory() as d:
            with bs.Store(d) as store:
                _seed(store)
                _sign(store)
                worker = FakeWorker({"u1": _worker_result(artifacts=["a.py"])})
                engine = _engine(store, worker, FakeCheckRunner())
                run = _begin_and_plan(engine, store, "p1",
                                      [_unit("u1", write_scope=["a.py"])])
                _set_contract(store, "paused")
                engine.step("p1")

                _set_contract(store, "live")
                _resume_the_run(store, run)
                trace = engine.run_to_completion("p1", max_steps=10)

                self.assertEqual(store.get_run("p1", raw=True)["state"],
                                 "DELIVERABLE_READY",
                                 "the two commands the notes name are the "
                                 "two that clear it: %r" % (trace,))

    def test_a_run_paused_under_a_live_contract_is_unchanged(self):
        """THE CONTROL for the enum split: the contract is LIVE and the RUN
        is PAUSED, which is still FOUNDER_WAITING naming
        `bm-controller resume`."""
        with tempfile.TemporaryDirectory() as d:
            with bs.Store(d) as store:
                _seed(store)
                _sign(store)
                engine = _engine(store, FakeWorker({}), FakeCheckRunner())
                run = _begin_and_plan(engine, store, "p1",
                                      [_unit("u1", write_scope=["a.py"])])
                _pause_the_run(store, run)

                summary = engine.step("p1")

                self.assertEqual(summary["stop_reason"], "FOUNDER_WAITING")
                self.assertIn("bm-controller resume", summary["note"])


class TestR5AnUpstreamFounderGateIsFounderWaiting(unittest.TestCase):
    """REFUTATION-4 LV L4-F3 (HIGH). A run whose only blocker is an open
    founder step in an UPSTREAM lane parked as NOTHING_SELECTABLE, the one
    enum value whose documented meaning is 'nothing founder-gated ...
    inspect the graph', with the run left READY instead of WAITING_HUMAN.
    One `resolve` unwedges it."""

    @staticmethod
    def _two_lanes(engine, store):
        run = _begin_and_plan(engine, store, "p1", [
            _unit("u1", write_scope=["a.py"], lane="build"),
            _unit("u2", dependencies=["u1"], write_scope=["b.py"],
                  lane="test"),
        ])
        store.queue_human_step(
            "p1", "", "build",
            "the rollback for some unit in this lane needs inspection", "",
            [], "sess1", _actor())
        return run

    def test_a_gated_upstream_lane_parks_as_founder_waiting(self):
        with tempfile.TemporaryDirectory() as d:
            with bs.Store(d) as store:
                _seed(store)
                _sign(store)
                engine = _engine(store, FakeWorker({}), FakeCheckRunner())
                self._two_lanes(engine, store)

                summary = engine.step("p1")

                self.assertEqual(summary["stop_reason"], "FOUNDER_WAITING")
                self.assertEqual(store.get_run("p1", raw=True)["state"],
                                 "WAITING_HUMAN")
                self.assertEqual(summary["state"], "WAITING_HUMAN")
                self.assertIn("build", summary["note"],
                              "the note names the blocking step's lane: %r"
                              % (summary["note"],))

    def test_resolving_the_one_step_unwedges_the_run(self):
        with tempfile.TemporaryDirectory() as d:
            with bs.Store(d) as store:
                _seed(store)
                _sign(store)
                worker = FakeWorker({
                    "u1": _worker_result(artifacts=["a.py"]),
                    "u2": _worker_result(artifacts=["b.py"]),
                })
                engine = _engine(store, worker, FakeCheckRunner())
                self._two_lanes(engine, store)
                engine.step("p1")
                step_id = store.list_human_steps(
                    "p1", resolved=False, raw=True)[0]["step_id"]

                store.resolve_human_step(step_id, "p1", "done", _actor())
                engine.run_to_completion("p1", max_steps=10)

                self.assertEqual(store.get_run("p1", raw=True)["state"],
                                 "DELIVERABLE_READY")


class TestR5TheSettlePathCarriesItsVerdict(unittest.TestCase):
    """REFUTATION-4 LV L4-F1 (HIGH). _settle_after_wave ended by calling
    _handle_no_ready_units with `summary = {"note": ""}`, a dict nobody
    reads, so the stop_reason, the note and the whole founder_gated block
    were computed and dropped on every wave that recorded a result, which
    is every synchronous worker wave and every `bm-controller
    record-result`: the production route."""

    def test_the_delivering_wave_names_delivered_and_its_remainder(self):
        """LV probe p04: one unit exhausts its retry ceiling, one is DONE,
        so the delivering wave has a founder-gated remainder worth naming.
        The delivery happens THROUGH the settle path."""
        with tempfile.TemporaryDirectory() as d:
            with bs.Store(d) as store:
                _seed(store)
                _sign(store)
                checker = FakeCheckRunner(
                    {"check-u1": {"exit_code": 1, "stdout": "",
                                  "stderr": "never passes"}})
                worker = FakeWorker({
                    "u1": _worker_result(artifacts=["a.py"]),
                    "u2": _worker_result(artifacts=["b.py"]),
                })
                engine = _engine(store, worker, checker)
                _begin_and_plan(engine, store, "p1", [
                    _unit("u1", write_scope=["a.py"], done_check="check-u1"),
                    _unit("u2", write_scope=["b.py"]),
                ])

                trace = engine.run_to_completion("p1", max_steps=10)

                delivering = [s for s in trace
                              if s["state"] == "DELIVERABLE_READY"]
                self.assertTrue(delivering, "%r" % (trace,))
                first = delivering[0]
                self.assertEqual(first["stop_reason"], "DELIVERED",
                                 "the wave that DELIVERS says so: %r"
                                 % (first,))
                self.assertEqual(first["note"], "deliverable ready")
                self.assertEqual(
                    (first.get("founder_gated") or {}).get("failed_units"),
                    ["u1"],
                    "the founder-gated remainder is attached to the wave "
                    "that computed it: %r" % (first,))
                self.assertEqual(len(trace), 2,
                                 "no extra wave whose only job is to repeat "
                                 "DELIVERED: %r" % (trace,))

    def test_a_failing_done_definition_is_named_on_the_wave_that_ran_it(self):
        """LV probe p01: the founder's whole suite ran TWICE per
        run_to_completion call, and the first run's failure was invisible
        (reason None, and a note about the spend ceiling)."""
        with tempfile.TemporaryDirectory() as d:
            with bs.Store(d) as store:
                _seed(store)
                _sign(store)
                checker = FakeCheckRunner(
                    {"final-suite": {"exit_code": 3, "stdout": "",
                                     "stderr": "not ready"}})
                worker = FakeWorker({"u1": _worker_result(artifacts=["a.py"])})
                engine = _engine(store, worker, checker)
                _begin_and_plan(engine, store, "p1",
                                [_unit("u1", write_scope=["a.py"])],
                                done_definition="final-suite")

                trace = engine.run_to_completion("p1", max_steps=15)

                self.assertEqual(checker.calls.count("final-suite"), 1,
                                 "ONE execution of the founder's whole "
                                 "suite: %r" % (checker.calls,))
                self.assertEqual(trace[-1]["stop_reason"], "FOUNDER_WAITING")
                self.assertIn("done-definition", trace[-1]["note"])
                self.assertEqual(len(trace), 1, "%r" % (trace,))

    def test_record_result_reports_the_settled_verdict_to_its_caller(self):
        """The production route: `bm-controller record-result` printed
        `unit u1 accepted` and said nothing about the delivery it had just
        performed, or about what remained founder-gated."""
        with tempfile.TemporaryDirectory() as d:
            with bs.Store(d) as store:
                _seed(store)
                _sign(store)
                engine, run, dispatch_ids = _park_one_async_unit(
                    self, store, FakeCheckRunner())
                settled = {}

                outcome = engine.receive_result(
                    "p1", dispatch_ids["u1"], "claims done", ["a.py"],
                    summary=settled)

                self.assertEqual(outcome, "u1")
                self.assertEqual(settled.get("stop_reason"), "DELIVERED")
                self.assertIn("founder_gated", settled)


class TestR5TheVerifyingParkNamesTheRealRecovery(unittest.TestCase):
    """REFUTATION-4 LV L4-F4 (MEDIUM). A run left VERIFYING with an open
    dispatch parked with a note naming three founder commands: `resume`
    no-ops unless PAUSED, `complete` is refused by the store, and only
    `stop` works, which destroys a run one `record-result` would have
    finished."""

    def test_a_run_left_verifying_with_an_open_dispatch_names_record_result(self):
        with tempfile.TemporaryDirectory() as d:
            with bs.Store(d) as store:
                _seed(store)
                _sign(store)
                engine, run, _ids = _park_one_async_unit(
                    self, store, FakeCheckRunner())
                _move_run_along_legal_edges(store, run, ("VERIFYING",))

                summary = engine.step("p1")

                self.assertEqual(summary["stop_reason"], "FOUNDER_WAITING")
                self.assertIn("record-result", summary["note"],
                              "the note names the command that actually "
                              "recovers it: %r" % (summary["note"],))
                self.assertNotIn("resume, complete or stop",
                                 summary["note"])


class TestR5TheFourthOrphanShapeRecovers(unittest.TestCase):
    """REFUTATION-4 LV L4-F5 (MEDIUM). _resume_result_in_and_orphans models
    three orphan shapes and not the fourth: a unit left DISPATCHED behind a
    dispatch that is already CLOSED, which sits in a two-call crash window
    in five places, three of them new in round 4. Its fence stayed ACTIVE
    over the founder's files, no founder step named it, and the run parked
    as NOTHING_SELECTABLE forever."""

    def test_a_unit_dispatched_behind_a_closed_dispatch_is_recovered(self):
        with tempfile.TemporaryDirectory() as d:
            with bs.Store(d) as store:
                _seed(store)
                _sign(store)
                engine, run, dispatch_ids = _park_one_async_unit(
                    self, store, FakeCheckRunner())
                fence_uuid = _unit_row(store, run["run_id"],
                                       "u1")["fence_uuid"]
                # The FIRST of the two calls in every one of those five
                # windows, with the crash simulated by stopping here.
                store.record_verification(
                    dispatch_ids["u1"], None,
                    "the first of two calls in the crash window", False,
                    _actor())
                self.assertEqual(
                    _unit_row(store, run["run_id"], "u1")["status"],
                    "DISPATCHED")

                engine.step("p1")

                self.assertNotEqual(
                    _unit_row(store, run["run_id"], "u1")["status"],
                    "DISPATCHED",
                    "the unit is returned through the circuit breaker")
                self.assertEqual(store.get(fence_uuid).state, "parked",
                                 "the fence is not left over the founder's "
                                 "files")


class TestR5PlanRefusesAUnitIdTheFenceWouldRefuse(unittest.TestCase):
    """REFUTATION-4 AZ F6 (MEDIUM). A unit_id that upsert_units accepts and
    valid_name refuses wedged the run permanently in EXECUTING: every
    `step` exited 1 on the fence's ValueError, and `plan`, the documented
    recovery, is refused from EXECUTING. `api/pay` is exactly the naming a
    planner produces for a unit that owns a directory."""

    def _begin(self, store):
        engine = _engine(store, FakeWorker({"u1": _worker_result(
            artifacts=["a.py"])}), FakeCheckRunner())
        run = engine.begin("p1", "ship it", "echo done")
        return engine, run

    def test_a_unit_id_with_a_reserved_character_is_refused_at_plan_time(self):
        with tempfile.TemporaryDirectory() as d:
            with bc.bs.Store(d) as store:
                _seed(store)
                _sign(store)
                engine, run = self._begin(store)

                with self.assertRaises(bc.bs.OwnershipRefused) as caught:
                    engine.plan("p1", run["run_id"],
                                [_unit("api/pay", write_scope=["a.py"])])

                self.assertEqual(caught.exception.reason, "bad-unit-id")
                self.assertIn("api/pay", str(caught.exception))

    def test_an_over_long_unit_id_is_refused_at_plan_time(self):
        with tempfile.TemporaryDirectory() as d:
            with bc.bs.Store(d) as store:
                _seed(store)
                _sign(store)
                engine, run = self._begin(store)

                with self.assertRaises(bc.bs.OwnershipRefused) as caught:
                    engine.plan("p1", run["run_id"],
                                [_unit("u" * 56, write_scope=["a.py"])])

                self.assertEqual(caught.exception.reason, "bad-unit-id")

    def test_the_refusal_leaves_no_wedge(self):
        """The wedge is the finding, not the refusal: nothing may be
        written, and a good graph must still plan and run afterwards."""
        with tempfile.TemporaryDirectory() as d:
            with bc.bs.Store(d) as store:
                _seed(store)
                _sign(store)
                engine, run = self._begin(store)
                with self.assertRaises(bc.bs.OwnershipRefused):
                    engine.plan("p1", run["run_id"],
                                [_unit("api/pay", write_scope=["a.py"])])

                engine.plan("p1", run["run_id"],
                            [_unit("u1", write_scope=["a.py"])])
                engine.run_to_completion("p1", max_steps=10)

                self.assertEqual(store.get_run("p1", raw=True)["state"],
                                 "DELIVERABLE_READY")


class TestR5PlanRefusesAForeignRun(unittest.TestCase):
    """REFUTATION-4 AZ F2 (HIGH). `bm-controller plan --project p1 --run
    <p2's run id>` wrote the FOREIGN run: it un-paused a PAUSED run (law
    L1), cancelled its open dispatches, parked its fences and replaced its
    unit graph, because plan's own PAUSED guard checked p1's current run
    and every write landed on the run the argument named. Round 4 closed
    this class for receive_result and did not look at plan."""

    def test_a_plan_aimed_at_one_project_cannot_write_another_run(self):
        """The refuter's own sequence: p2 is a healthy run with work in
        flight, paused by the founder the only way the CLI allows, and one
        `plan` aimed at p1 carrying p2's run id."""
        with tempfile.TemporaryDirectory() as d:
            with bc.bs.Store(d) as store:
                _seed(store, "p1")
                _seed(store, "p2")
                _sign(store, "p1")
                _sign(store, "p2")
                e1 = _engine(store, FakeWorker({}), FakeCheckRunner())
                e2 = _engine(store, FakeWorker({"v1": _worker_result(
                    status="pending")}), FakeCheckRunner(),
                    controller_id="ctrl2")
                _begin_and_plan(e1, store, "p1",
                                [_unit("u1", write_scope=["a.py"])])
                run2 = _begin_and_plan(e2, store, "p2",
                                       [_unit("v1", write_scope=["b.py"])])
                e2.step("p2")
                fence_uuid = _unit_row(store, run2["run_id"],
                                       "v1")["fence_uuid"]
                self.assertEqual(store.get(fence_uuid).state, "active")
                _pause_the_run(store, run2)

                with self.assertRaises(bc.bs.OwnershipRefused) as caught:
                    e1.plan("p1", run2["run_id"],
                            [_unit("u9", write_scope=["c.py"])])

                self.assertEqual(caught.exception.reason,
                                 "run-not-in-project")
                self.assertEqual(store.get_run("p2", raw=True)["state"],
                                 "PAUSED", "the foreign run never left "
                                 "PAUSED (law L1)")
                self.assertEqual(
                    [u["unit_id"] for u in
                     store.list_units(run2["run_id"], raw=True)], ["v1"],
                    "and its unit graph was not replaced")
                self.assertEqual(
                    store.list_dispatches("v1", raw=True)[0]["status"],
                    "DISPATCHED", "its open dispatch was not cancelled")
                self.assertEqual(store.get(fence_uuid).state, "active",
                                 "and its fence was not parked")

    def test_naming_the_projects_own_run_explicitly_still_works(self):
        """THE CONTROL: --run is not removed, it is checked."""
        with tempfile.TemporaryDirectory() as d:
            with bc.bs.Store(d) as store:
                _seed(store)
                _sign(store)
                engine = _engine(store, FakeWorker({}), FakeCheckRunner())
                run = engine.begin("p1", "ship it", "echo done")

                engine.plan("p1", run["run_id"],
                            [_unit("u1", write_scope=["a.py"])])

                self.assertEqual(
                    [u["unit_id"] for u in
                     store.list_units(run["run_id"], raw=True)], ["u1"])


class TestR5ReadScopeIsCanonicalisedLikeWriteScope(unittest.TestCase):
    """REFUTATION-4 AZ F9 (MEDIUM). write_scope is canonicalised and
    refused 'path-escape', read_scope was stored with a bare json.dumps
    and handed to the worker unchanged, so a brief could hand a worker
    `/etc`, `../../../Users` and `~/.ssh/id_rsa` as its read scope. In full
    auto the read scope is model-authored."""

    def test_a_read_scope_that_leaves_the_project_root_is_refused(self):
        with tempfile.TemporaryDirectory() as d:
            with bc.bs.Store(d) as store:
                _seed(store)
                _sign(store, allowed_paths=["docs"])
                engine = _engine(store, FakeWorker({}), FakeCheckRunner())
                run = engine.begin("p1", "ship it", "echo done")

                with self.assertRaises(bc.bs.OwnershipRefused) as caught:
                    engine.plan("p1", run["run_id"], [
                        _unit("u1", write_scope=["docs/a.md"],
                              read_scope=["/etc"])])

                self.assertEqual(caught.exception.reason, "path-escape")

    def test_a_relative_read_scope_is_stored_canonical(self):
        with tempfile.TemporaryDirectory() as d:
            with bc.bs.Store(d) as store:
                _seed(store)
                _sign(store)
                engine = _engine(store, FakeWorker({}), FakeCheckRunner())
                run = engine.begin("p1", "ship it", "echo done")

                engine.plan("p1", run["run_id"], [
                    _unit("u1", write_scope=["a.py"],
                          read_scope=["docs/../src/x.py"])])

                self.assertEqual(
                    _unit_row(store, run["run_id"], "u1")["read_scope"],
                    ["src/x.py"])


class TestR5TheReAwaitChecksTheFenceContentAndOwner(unittest.TestCase):
    """REFUTATION-4 AZ F8 (MEDIUM). The re-await route compared ONE field,
    the fence's state, and never its CONTENT or its owner, so a fence
    emptied under an open dispatch by a same-session reclaim still
    re-awaited and handed out a brief for files the fence no longer
    held."""

    def test_an_emptied_fence_refuses_the_re_await(self):
        with tempfile.TemporaryDirectory() as d:
            with bc.bs.Store(d) as store:
                _seed(store)
                _sign(store)
                worker = FakeWorker({"u1": _worker_result(status="pending")})
                engine = _engine(store, worker, FakeCheckRunner(),
                                 session_id="sess-shared")
                run = _begin_and_plan(engine, store, "p1",
                                      [_unit("u1", write_scope=["a.py"])])
                engine.step("p1")
                fence_uuid = _unit_row(store, run["run_id"],
                                       "u1")["fence_uuid"]
                record = store.get(fence_uuid)
                store.claim(name=record.name, lifetime="ephemeral", files=[],
                            objective="a deliberate release",
                            owner="someone-else", session_id="sess-shared")
                self.assertEqual(store.get(fence_uuid).files, [],
                                 "the fence now holds nothing")

                summary = engine.step("p1")

                self.assertEqual(
                    worker.calls["u1"], 1,
                    "the worker is not asked again under a fence that does "
                    "not hold this unit's files: %r" % (summary,))


class TestR5FounderNotesStateWhatActuallyHappened(unittest.TestCase):
    """REFUTATION-4 AZ F12 (LOW). Two founder-facing notes stated a cause
    that did not happen: _resume_dispatched's default REFUSED note blamed
    the live contract for a FENCE problem, and _NOTE_CONTENTION's 'no
    founder action is needed' was unconditional and false for a fence
    nobody is coming back for (AZ F7: forever, with zero founder steps)."""

    def test_a_fence_problem_is_not_blamed_on_the_live_contract(self):
        with tempfile.TemporaryDirectory() as d:
            with bc.bs.Store(d) as store:
                _seed(store)
                _sign(store)
                worker = FakeWorker({"u1": _worker_result(status="pending")})
                engine = _engine(store, worker, FakeCheckRunner())
                run = _begin_and_plan(engine, store, "p1",
                                      [_unit("u1", write_scope=["a.py"])])
                engine.step("p1")
                fence_uuid = _unit_row(store, run["run_id"],
                                       "u1")["fence_uuid"]
                record = store.get(fence_uuid)
                store.transition(fence_uuid, record.version, "parked",
                                 session_id=record.session_id,
                                 note="a crash released it",
                                 evidence="a crash released it")

                summary = engine.step("p1")

                self.assertNotIn(
                    "the live contract no longer authorises", summary["note"],
                    "the contract never moved; the fence did: %r"
                    % (summary["note"],))
                self.assertIn("fence", summary["note"])

    def test_the_contention_note_says_what_to_do_when_it_repeats(self):
        with tempfile.TemporaryDirectory() as d:
            with bc.bs.Store(d) as store:
                _seed(store)
                _sign(store)
                store.claim(name="unrelated-holder", lifetime="persistent",
                            files=["a.py"], objective="holds the same file",
                            owner="someone-else", session_id="other-session")
                engine = _engine(store, FakeWorker({}), FakeCheckRunner())
                _begin_and_plan(engine, store, "p1",
                                [_unit("u1", write_scope=["a.py"])])

                trace = engine.run_to_completion("p1", max_steps=5)

                note = trace[-1]["note"]
                self.assertEqual(trace[-1]["stop_reason"], "CONTENTION")
                self.assertNotIn("until a founder acts", note)
                self.assertIn("no founder action is needed", note,
                              "a transient overlap still needs nobody")
                self.assertIn(
                    "repeats", note,
                    "and the note says what a fence nobody is coming back "
                    "for looks like: %r" % (note,))


# ---------------------------------------------------------------------------
# ROUND 6: the findings a FIFTH adversarial pass raised against the round-5
# fixes above (report
# docs/program/absolute-lead/evidence/L03/REFUTATION-5-safety.md,
# 2026-08-05). Every class below is one of that report's own probe
# sequences restated as a permanent regression test, and every one of them
# FAILED on the tree that report attacked (evidence:
# docs/program/absolute-lead/evidence/L03/RED-round6-controller.txt).
# ---------------------------------------------------------------------------

class _ScopePoisoningStore(object):
    """A pass-through proxy over a REAL Store that hands the engine one
    poisoned `write_scope`, and nothing else.

    It exists because this round splits one defect across two writers. The
    DECLARATION side (refusing a git pathspec, a bare string and a scalar
    container where a write scope ENTERS the store) belongs to
    tools/bm_store.py and to its own writer. The EXECUTION side, which is
    what this file tests, is the engine's promise that a bad entry which
    reached it by SOME OTHER ROUTE (a hand-written row, a store written by
    an older version, an SDK caller that never went through
    `bm-controller plan`) still cannot become a `git restore` over the
    founder's whole working tree. Proving that needs a unit row the shipped
    plan would refuse, so this proxy manufactures exactly one.

    `poison` is mutable on purpose: set it after a dispatch to model the
    row going bad between the claim and the rollback."""

    def __init__(self, store, poison=None):
        self._store = store
        self.poison = poison

    def __getattr__(self, name):
        return getattr(self._store, name)

    def _poisoned(self, row):
        if self.poison is None:
            return row
        out = dict(row)
        out["write_scope"] = list(self.poison)
        return out

    def select_ready_units(self, run_id):
        return [self._poisoned(u)
                for u in self._store.select_ready_units(run_id)]

    def list_units(self, run_id, raw=False):
        return [self._poisoned(u)
                for u in self._store.list_units(run_id, raw=raw)]


#: The four bypass spellings REFUTATION-5-safety.md S5 walked past the
#: round-5 AST guard, as (label, source fragment) pairs. Each one is
#: inserted into a COPY of tools/bm_controller.py held in memory; the repo
#: file is never written and none of this code is ever executed.
#:
#: SECURITY NOTE, because these fragments LOOK like the thing they are
#: defending against: they are inert TEXT. They are never compiled, never
#: exec'd and never written to disk; the only thing done with them is
#: ast.parse, so the guard can be shown to reject each one. A calibration
#: corpus of the exact spellings an attacker would use is what makes the
#: guard evidence rather than a claim, which is precisely what round 5's
#: version lacked.
_S5_BYPASS_MUTATIONS = (
    ("subprocess.run() added inline in _verify_and_finish",
     "        subprocess.run('echo pwned', shell=True)\n"),
    ("os.system() added inline in _verify_and_finish",
     "        os.system('echo pwned')\n"),
    ("getattr(self.checker, 'run') in _verify_and_finish",
     "        getattr(self.checker, 'run')('echo pwned', cwd='.')\n"),
    ("runner = self.checker; runner.run(...) in _verify_and_finish",
     "        runner = self.checker\n"
     "        runner.run('echo pwned', cwd='.')\n"),
)

_S5_ANCHOR = "    def _verify_and_finish("


def _mutated_controller_source(fragment):
    """tools/bm_controller.py's source with `fragment` spliced in as the
    first statement of _verify_and_finish's body, after its docstring.
    Returned as a STRING; nothing is written and nothing is executed.

    The anchor is the def LINE PREFIX, not the whole signature, so a
    future argument added to that method does not silently turn this
    calibration into a no-op."""
    with io.open(CONTROLLER_FILE, encoding="utf-8") as fh:
        lines = fh.read().split("\n")
    starts = [i for i, line in enumerate(lines)
              if line.startswith(_S5_ANCHOR)]
    assert len(starts) == 1, "the anchor moved; fix _S5_ANCHOR"
    # Walk to the end of the method's docstring, so the splice lands on a
    # syntactically valid statement boundary.
    end = starts[0]
    for i in range(starts[0] + 1, len(lines)):
        if lines[i].rstrip().endswith('"""'):
            end = i
            break
    assert end > starts[0], "no docstring found under the anchor"
    return "\n".join(lines[:end + 1] + [fragment.rstrip("\n")]
                      + lines[end + 1:])


class TestR6WriteScopePathspecMagicCannotReachGit(unittest.TestCase):
    """REFUTATION-5-safety.md S1 (HIGH, PUSH-BLOCKER, DATA DESTRUCTION).

    A `write_scope` entry that is a git PATHSPEC (`:/`, `:!x`,
    `:(exclude)x`) passed the round-5 literal-write-scope rule, whose only
    test was the three glob characters `* ? [`, and the engine's own
    rollback then ran `git restore -- :/`, which restores the WHOLE working
    tree. Every uncommitted founder edit in the project was destroyed, and
    because `:/` exits 0 the engine read the rollback as a SUCCESS and
    queued no dirty-write-scope warning at all.

    Two halves, both tested here:
      (a) the composed command can never hand git a magic pathspec;
      (b) a write scope entry that is not a plain relative path inside the
          root is REFUSED, at plan time and again at dispatch, and is never
          rolled back, with a named founder-visible refusal each time."""

    _MAGIC = (":/", ":", ":!keep.txt", ":^keep.txt", ":(exclude)keep.txt",
              ":(icase)KEEP.TXT", ":(top)")

    def test_the_rollback_never_hands_git_a_magic_pathspec(self):
        """The pure half: git reads a leading colon as pathspec magic, and
        `:(literal)` is git's own escape for it (verified against git
        2.50.1 in a real repo; see the round-6 report)."""
        for entry in self._MAGIC:
            self.assertEqual(
                bc._pathspec_literal(entry), ":(literal)" + entry,
                "%r must reach git as a literal path" % (entry,))

    def test_a_plain_path_is_emitted_byte_for_byte_unchanged(self):
        """THE CONTROL, and a deliberate compatibility pin: a path that
        cannot be read as magic is emitted exactly as it always was, so
        the founder-facing rollback command for ordinary work is
        unchanged."""
        for entry in ("a.py", "src/app.py", ".", "docs/notes.md"):
            self.assertEqual(bc._pathspec_literal(entry), entry)

    def test_plan_refuses_a_pathspec_write_scope_and_writes_nothing(self):
        for entry in self._MAGIC:
            with tempfile.TemporaryDirectory() as d:
                with bs.Store(d) as store:
                    _seed(store)
                    _sign(store)
                    engine = _engine(store, FakeWorker({}),
                                     FakeCheckRunner())
                    run = engine.begin("p1", "ship it", "echo done")
                    with self.assertRaises(bc.bs.OwnershipRefused) as caught:
                        engine.plan("p1", run["run_id"],
                                    [_unit("u1", write_scope=[entry])])
                    self.assertIn(entry, str(caught.exception))
                    self.assertEqual(
                        store.get_run("p1", raw=True)["state"], "NEW",
                        "nothing is written and the run has not moved")

    def test_a_poisoned_write_scope_is_never_dispatched(self):
        """The EXECUTION side. The row reaches the engine already bad; no
        worker is handed a brief, no fence is claimed, and a founder step
        names the unit and the entry."""
        with tempfile.TemporaryDirectory() as d:
            with bs.Store(d) as store:
                _seed(store)
                _sign(store)
                worker = FakeWorker({"u1": _worker_result()})
                checker = FakeCheckRunner()
                proxy = _ScopePoisoningStore(store)
                engine = _engine(proxy, worker, checker)
                run = _begin_and_plan(engine, store, "p1",
                                      [_unit("u1", write_scope=["a.py"])])
                proxy.poison = [":!keep.txt"]

                summary = engine.step("p1")

                self.assertEqual(summary["dispatched"], [])
                self.assertEqual(worker.call_log, [],
                                 "no worker is handed a poisoned scope")
                self.assertEqual(checker.calls, [])
                steps = [s["what"] for s in store.list_human_steps(
                    "p1", resolved=False, raw=True)]
                self.assertTrue(
                    any("u1" in s and ":!keep.txt" in s for s in steps),
                    "a NAMED founder-visible refusal, never a silent skip: "
                    "%r" % (steps,))
                self.assertEqual(
                    _dispatch_count(store, "u1"), 0,
                    "nothing was dispatched for run %s" % run["run_id"])

    def test_a_scope_that_goes_bad_after_dispatch_is_never_rolled_back(self):
        """The second execution site. The unit dispatched under a clean
        scope, its done_check then fails, and the row the rollback reads is
        the poisoned one. No `git restore` is composed at all, and the
        founder is told the scope was NOT cleaned."""
        with tempfile.TemporaryDirectory() as d:
            with bs.Store(d) as store:
                _seed(store)
                _sign(store)
                checker = FakeCheckRunner(
                    {"true": {"exit_code": 1, "stdout": "", "stderr": "no"}})
                proxy = _ScopePoisoningStore(store)
                engine, run, dispatch_ids = _park_one_async_unit(
                    self, proxy, checker)
                proxy.poison = [":/"]

                engine.receive_result("p1", dispatch_ids["u1"],
                                      "claims done", ["a.py"])

                self.assertEqual(
                    [c for c in checker.calls if "git restore" in c], [],
                    "no rollback is composed from a poisoned scope: %r"
                    % (checker.calls,))
                steps = [s["what"] for s in store.list_human_steps(
                    "p1", resolved=False, raw=True)]
                self.assertTrue(
                    any("u1" in s and "NOT rolled back" in s
                        for s in steps),
                    "the founder is told the write scope was left as the "
                    "worker left it: %r" % (steps,))


class TestR6EngineRefusesMalformedScopeContainers(unittest.TestCase):
    """REFUTATION-5-safety.md S4 and S6, the ENGINE half. `write_scope: 7`
    raised an uncaught TypeError out of the shipped `plan`, and a
    `write_scope` (or `read_scope`) emitted as a bare JSON STRING was
    iterated character by character, so `"a.py"` became `['a', '.', 'p',
    'y']`, the brief handed the worker `.` (the whole project) and the
    unit's fence held the project root. The store writer refuses these
    where they enter the store; the engine refuses them before it writes
    anything at all."""

    def _refusal(self, unit):
        with tempfile.TemporaryDirectory() as d:
            with bs.Store(d) as store:
                _seed(store)
                _sign(store)
                engine = _engine(store, FakeWorker({}), FakeCheckRunner())
                run = engine.begin("p1", "ship it", "echo done")
                with self.assertRaises(bc.bs.OwnershipRefused) as caught:
                    engine.plan("p1", run["run_id"], [unit])
                self.assertEqual(store.get_run("p1", raw=True)["state"],
                                 "NEW", "nothing written, run not moved")
                return caught.exception

    def test_a_scalar_write_scope_is_a_named_refusal_not_a_typeerror(self):
        exc = self._refusal(dict(_unit("u1"), write_scope=7))
        self.assertIn("write_scope", str(exc))
        self.assertIn("int", str(exc))

    def test_a_bare_string_write_scope_is_refused_not_shredded(self):
        exc = self._refusal(dict(_unit("u1"), write_scope="a.py"))
        self.assertIn("a.py", str(exc))
        self.assertIn("one path", str(exc))

    def test_a_bare_string_read_scope_is_refused_not_shredded(self):
        exc = self._refusal(dict(_unit("u1"), read_scope="src"))
        self.assertIn("read_scope", str(exc))

    def test_a_dict_read_scope_is_refused_rather_than_reduced_to_keys(self):
        exc = self._refusal(dict(_unit("u1"), read_scope={"a.py": 1}))
        self.assertIn("read_scope", str(exc))


class TestR6TheSpendBreakerStopsEveryCommand(unittest.TestCase):
    """REFUTATION-5-safety.md S2 (HIGH). After the founder's SPEND CEILING
    tripped and `bm-autonomy gate-check` answered REFUSED-BREAKER, one
    `bm-controller record-result` still ran the unit's own model-authored
    done_check AND the founder's whole done_definition, accepted the unit
    and declared the run DELIVERABLE_READY. Round 5 gated commands on the
    contract's LIVENESS and on nothing else, so the second of the founder's
    two brakes stopped no command at all."""

    def _blown_ceiling(self, store, checker, done_check="true"):
        _seed(store)
        _sign(store, token_ceiling=100, minutes_ceiling=100)
        engine, run, dispatch_ids = _park_one_async_unit(
            self, store, checker,
            units=[_unit("u1", write_scope=["a.py"],
                         done_check=done_check)])
        store.record_spend("p1", 500, 500, "the founder's ceiling is blown",
                           "sess1", _actor())
        self.assertEqual(store.spend_totals("p1")["verdict"], "hard-stop")
        self.assertEqual(
            store.gate_check("p1", "file-create")["verdict"],
            "REFUSED-BREAKER")
        return engine, run, dispatch_ids

    def test_record_result_after_the_breaker_trips_runs_no_command(self):
        with tempfile.TemporaryDirectory() as d:
            with bs.Store(d) as store:
                checker = FakeCheckRunner()
                engine, run, dispatch_ids = self._blown_ceiling(store,
                                                                checker)
                before = list(checker.calls)

                outcome = engine.receive_result(
                    "p1", dispatch_ids["u1"], "claims done", ["a.py"])

                self.assertEqual(
                    checker.calls, before,
                    "the spend breaker stops EVERY command, the unit's own "
                    "done_check and the founder's whole done_definition "
                    "included: %r" % (checker.calls,))
                self.assertNotEqual(outcome, "u1",
                                    "no unit is accepted under a tripped "
                                    "breaker")
                self.assertNotEqual(
                    store.get_run("p1", raw=True)["state"],
                    "DELIVERABLE_READY",
                    "no deliverable is declared under a tripped breaker")

    def test_the_founder_is_told_the_breaker_is_what_stopped_it(self):
        with tempfile.TemporaryDirectory() as d:
            with bs.Store(d) as store:
                checker = FakeCheckRunner()
                engine, run, dispatch_ids = self._blown_ceiling(store,
                                                                checker)
                summary = {}

                engine.receive_result("p1", dispatch_ids["u1"],
                                      "claims done", ["a.py"],
                                      summary=summary)

                self.assertEqual(summary.get("stop_reason"), "SPEND_STOP")
                self.assertIn("ceiling", (summary.get("note") or ""))
                steps = [s["what"] for s in store.list_human_steps(
                    "p1", resolved=False, raw=True)]
                self.assertTrue(any("u1" in s for s in steps),
                                "a founder step names the unit: %r"
                                % (steps,))

    def test_the_rollback_leg_runs_no_command_either(self):
        """The refuter's second sequence: the unit FAILS its done_check, so
        round 5 ran the done_check and then a `git restore` in the
        founder's working tree, both under a tripped ceiling."""
        with tempfile.TemporaryDirectory() as d:
            with bs.Store(d) as store:
                checker = FakeCheckRunner(
                    {"true": {"exit_code": 1, "stdout": "", "stderr": "no"}})
                engine, run, dispatch_ids = self._blown_ceiling(store,
                                                                checker)

                engine.receive_result("p1", dispatch_ids["u1"],
                                      "claims done", ["a.py"])

                self.assertEqual(checker.calls, [],
                                 "zero commands, done_check and rollback "
                                 "alike: %r" % (checker.calls,))


class TestR6AKillLandingDuringACommandRejects(unittest.TestCase):
    """REFUTATION-5-safety.md S3 (MEDIUM). A stop or a revoke landing
    DURING the done_check was classified as a NON-move by
    _authorisation_moved (both are lifecycle change kinds), so the
    staleness branch was skipped and the unit was ACCEPTED under a killed
    contract, with its fence released as complete and no founder step
    naming any of it. Round 4 rejected that same sequence as stale, so
    round 5 was strictly weaker. The founder was also told, in the same
    command, that the result was rejected and that NO command was
    executed. Both sentences were false."""

    def _kill_during_the_done_check(self, store, killed_state):
        """The unit's own done_check IS the kill switch, which is what the
        founder pulling it in another terminal looks like from here."""
        def _kill(_call_number):
            _set_contract(store, killed_state, reason="3am kill switch")
            return {"exit_code": 0, "stdout": "", "stderr": ""}
        return FakeCheckRunner({"true": _kill})

    def _drive(self, store, killed_state):
        _seed(store)
        _sign(store)
        checker = self._kill_during_the_done_check(store, killed_state)
        engine, run, dispatch_ids = _park_one_async_unit(self, store, checker)
        summary = {}
        outcome = engine.receive_result("p1", dispatch_ids["u1"],
                                        "claims done", ["a.py"],
                                        summary=summary)
        return engine, run, checker, summary, outcome

    def test_a_revoke_during_the_done_check_rejects_the_unit(self):
        with tempfile.TemporaryDirectory() as d:
            with bs.Store(d) as store:
                engine, run, checker, summary, outcome = self._drive(
                    store, "revoked")

                self.assertEqual(outcome, "rejected")
                unit = _unit_row(store, run["run_id"], "u1")
                self.assertNotEqual(unit["status"], "DONE",
                                    "no unit is accepted under a contract "
                                    "the founder killed mid-command")
                dispatch = store.list_dispatches("u1", raw=True)[0]
                self.assertNotEqual(dispatch["status"], "VERIFIED")
                self.assertEqual(store.get(unit["fence_uuid"]).state,
                                 "parked")

    def test_a_stop_during_the_done_check_rejects_the_unit(self):
        with tempfile.TemporaryDirectory() as d:
            with bs.Store(d) as store:
                engine, run, checker, summary, outcome = self._drive(
                    store, "stopped")

                self.assertEqual(outcome, "rejected")
                self.assertNotEqual(
                    _unit_row(store, run["run_id"], "u1")["status"], "DONE")

    def test_the_founder_note_does_not_claim_nothing_ran(self):
        """S3's second half: one note constant served two branches whose
        facts differ, so a founder was told 'NO command was executed'
        about a command that had just executed."""
        with tempfile.TemporaryDirectory() as d:
            with bs.Store(d) as store:
                engine, run, checker, summary, outcome = self._drive(
                    store, "revoked")

                self.assertEqual(checker.calls, ["true"],
                                 "the done_check really did run: %r"
                                 % (checker.calls,))
                note = summary.get("note") or ""
                self.assertIn("revoked", note)
                self.assertNotIn(
                    "NO command was executed", note,
                    "the note must not deny the command it just ran: %r"
                    % (note,))


class TestR6TheExecutionPrimitiveGuardIsStructural(unittest.TestCase):
    """REFUTATION-5-safety.md S5 (LOW). Round 5's AST guard matched ONE
    shape, `self.checker.run`, so it passed with `subprocess.run`,
    `os.system`, `getattr(self.checker, "run")` and an aliased
    `runner = self.checker` added inline in `_verify_and_finish`. It was
    the guard's CLAIM ("a command site added OUTSIDE the gate fails that
    test") that was overstated, not the shipped file.

    The guard is now a real structural control: no subprocess execution
    primitive may be called anywhere in the module outside the named homes
    in _EXEC_PRIMITIVE_HOMES, the checker object may not be aliased or
    reached by getattr, and the calibration test below proves each of the
    refuter's four spellings now FAILS it."""

    def test_the_shipped_file_passes_the_guard(self):
        with io.open(CONTROLLER_FILE, encoding="utf-8") as fh:
            source = fh.read()
        self.assertEqual(_execution_primitive_offences(source), [],
                         "the shipped tools/bm_controller.py is clean")

    def test_each_of_the_four_bypass_spellings_fails_the_guard(self):
        """THE CALIBRATION. Without this, a guard that never fails proves
        nothing, which is exactly how the round-5 one survived. The repo
        file is never written and none of the mutant code is executed."""
        for label, fragment in _S5_BYPASS_MUTATIONS:
            offences = _execution_primitive_offences(
                _mutated_controller_source(fragment))
            self.assertNotEqual(
                offences, [],
                "the guard must FAIL for: %s" % (label,))

    def test_the_one_checker_call_site_is_still_the_gated_one(self):
        """The round-5 property is kept, not replaced."""
        self.assertEqual(_checker_call_site_functions(), {"_run_command"})


class TestR6TheMeterCannotReadLowOnADeliveredRun(unittest.TestCase):
    """REFUTATION-5-safety.md S7. docs/KNOWN-LIMITS.md disclosed the
    uncharged meter as "a breaker reading low by one unit's cost". The
    reachable magnitude is the WHOLE RUN's: pause, record-result, resume,
    step, once per unit, and a run reached DELIVERABLE_READY with 270
    claimed tokens against a 100 token ceiling, metered 0, verdict `ok`,
    with zero open founder steps.

    The rule this class pins: a run cannot deliver while any accepted
    unit's cost was never metered."""

    def _hold_and_resume_with_an_uncharged_cost(self, store, checker):
        _seed(store)
        _sign(store, token_ceiling=100, minutes_ceiling=100)
        engine, run, dispatch_ids = _park_one_async_unit(self, store, checker)
        _set_contract(store, "paused", reason="founder pressed pause")
        engine.step("p1")
        outcome = engine.receive_result("p1", dispatch_ids["u1"],
                                        "claims done", ["a.py"],
                                        cost={"tokens": 90, "minutes": 5})
        self.assertEqual(outcome, "held")
        self.assertEqual(store.spend_totals("p1")["tokens"], 0,
                         "record_spend refuses without a live contract, "
                         "which is the gap being closed")
        _set_contract(store, "live", reason="founder resumed")
        _resume_the_run(store, run)
        return engine, run

    def test_the_run_does_not_deliver_while_a_cost_was_never_metered(self):
        with tempfile.TemporaryDirectory() as d:
            with bs.Store(d) as store:
                checker = FakeCheckRunner()
                engine, run = self._hold_and_resume_with_an_uncharged_cost(
                    store, checker)

                summary = engine.step("p1")

                self.assertEqual(
                    _unit_row(store, run["run_id"], "u1")["status"], "DONE",
                    "the held answer is still accepted on its own merits")
                self.assertNotEqual(
                    store.get_run("p1", raw=True)["state"],
                    "DELIVERABLE_READY",
                    "a run whose meter never saw 90 tokens against a 100 "
                    "token ceiling is not a deliverable: %r" % (summary,))
                self.assertEqual(summary["stop_reason"], "SPEND_STOP")
                self.assertNotIn("echo done", checker.calls,
                                 "the founder's own done_definition is not "
                                 "run for a run that cannot deliver")

    def test_a_founder_step_names_the_gap_and_the_command_that_clears_it(self):
        with tempfile.TemporaryDirectory() as d:
            with bs.Store(d) as store:
                checker = FakeCheckRunner()
                engine, run = self._hold_and_resume_with_an_uncharged_cost(
                    store, checker)

                engine.step("p1")

                steps = store.list_human_steps(
                    "p1", lane=bc.SPEND_RECONCILE_LANE, resolved=False,
                    raw=True)
                self.assertEqual(
                    len(steps), 1,
                    "one founder step per uncharged disclosure: %r"
                    % ([s["what"] for s in steps],))
                self.assertIn("90", steps[0]["what"])
                self.assertIn("bm-autonomy spend", steps[0]["what"])
                self.assertIn("human-steps", steps[0]["what"])

    def test_the_reserved_lane_blocks_no_unit_of_the_run(self):
        """Round 5 chose a checkpoint over a founder step precisely because
        `queue_human_step` gates a LANE. The reserved lane holds no unit,
        so `select_ready_units` skips nothing and the held unit is still
        judged and accepted after the resume."""
        with tempfile.TemporaryDirectory() as d:
            with bs.Store(d) as store:
                checker = FakeCheckRunner()
                engine, run = self._hold_and_resume_with_an_uncharged_cost(
                    store, checker)

                engine.step("p1")

                self.assertEqual(
                    _unit_row(store, run["run_id"], "u1")["status"], "DONE")
                self.assertEqual(
                    [u["unit_id"] for u in store.select_ready_units(
                        run["run_id"])], [])

    def test_reconciling_the_gap_lets_the_run_deliver(self):
        """The way out is a SHIPPED command pair, not a wedge: charge the
        spend the disclosure names, resolve the step it queued."""
        with tempfile.TemporaryDirectory() as d:
            with bs.Store(d) as store:
                checker = FakeCheckRunner()
                engine, run = self._hold_and_resume_with_an_uncharged_cost(
                    store, checker)
                engine.step("p1")
                steps = store.list_human_steps(
                    "p1", lane=bc.SPEND_RECONCILE_LANE, resolved=False,
                    raw=True)
                store.resolve_human_step(steps[0]["step_id"], "p1",
                                         "charged by hand", _actor())

                summary = engine.step("p1")

                self.assertEqual(store.get_run("p1", raw=True)["state"],
                                 "DELIVERABLE_READY",
                                 "the run delivers once the founder has "
                                 "reconciled the meter: %r" % (summary,))


# ---------------------------------------------------------------------------
# ROUND 7 / REFUTATION-6-pushgate.md, the LAST fix round before a public
# push. Five findings, each pinned below by a test that encodes the
# refuter's OWN reproduction rather than a paraphrase of it.
# ---------------------------------------------------------------------------

#: F1's WINDOW INVENTORY. Every ControllerEngine method that hands a
#: command to _run_command, with how many command sites it holds. That set
#: IS the complete list of commands the judging path can run, and therefore
#: the complete list of instants at which a kill switch has to be re-asked.
#: Hard-coded rather than derived from the file, deliberately: an inventory
#: computed from the thing it describes always agrees with it.
_R7_COMMAND_SITES = {
    "_handle_late_result": 1,   # the unit's rollback (git restore)
    "_verify_and_finish": 2,    # the unit's done_check, then its verifier
    "_reject_as_stale": 1,      # the unit's rollback (git restore)
    "_reject": 1,               # the unit's rollback (git restore)
    "_deliver_or_hold": 1,      # the founder's whole done-definition
}


def _run_command_call_sites():
    """{enclosing function name: (calls, calls carrying an explicit
    label=)} for every `self._run_command(...)` in tools/bm_controller.py.
    Source only: nothing is imported and nothing is executed."""
    with io.open(CONTROLLER_FILE, encoding="utf-8") as fh:
        tree = ast.parse(fh.read(), filename="bm_controller.py")
    sites = {}
    stack = []

    class _Visitor(ast.NodeVisitor):
        def visit_FunctionDef(self, node):
            stack.append(node.name)
            self.generic_visit(node)
            stack.pop()

        visit_AsyncFunctionDef = visit_FunctionDef

        def visit_Call(self, node):
            func = node.func
            if (isinstance(func, ast.Attribute)
                    and func.attr == "_run_command"
                    and isinstance(func.value, ast.Name)
                    and func.value.id == "self"):
                where = stack[-1] if stack else "<module>"
                calls, labelled = sites.get(where, (0, 0))
                has_label = any(kw.arg == "label" for kw in node.keywords)
                sites[where] = (calls + 1, labelled + (1 if has_label else 0))
            self.generic_visit(node)

    _Visitor().visit(tree)
    return sites


class TestR7EveryCommandTheJudgingPathRunsIsInventoried(unittest.TestCase):
    """REFUTATION-6-pushgate.md F1. The fix is "re-ask after EVERY command",
    which is a claim about a SET, so the set is pinned here. A command site
    added later fails this test, which is the only thing that keeps the
    window inventory in the round-7 report from decaying into prose."""

    def test_the_command_sites_are_exactly_the_inventoried_ones(self):
        sites = {name: calls
                 for name, (calls, _lab) in _run_command_call_sites().items()}
        self.assertEqual(
            sites, _R7_COMMAND_SITES,
            "the judging path's command inventory moved; every new site "
            "needs its own authorisation window and a line in the round-7 "
            "report")

    def test_every_command_site_names_itself_to_the_ledger(self):
        """F2's drift guard. The founder-facing 'what ran' sentence is
        derived from a ledger _run_command writes, so a site that does not
        NAME itself would run a command the founder's note cannot mention."""
        unlabelled = sorted(name for name, (calls, labelled)
                            in _run_command_call_sites().items()
                            if labelled != calls)
        self.assertEqual(
            unlabelled, [],
            "every self._run_command call must pass an explicit label=, or "
            "the note that says what ran cannot name it")


class TestR7TheKillSwitchIsAskedAtEveryWindow(unittest.TestCase):
    """REFUTATION-6-pushgate.md F1 (HIGH, the founder's kill switch).

    Round 6 re-read the authorisation after the done_check and nowhere
    else, so a stop or a revoke landing during a unit's model-authored
    VERIFIER was never re-asked: the unit was accepted under a killed
    contract, its fence released as if the work were sound, its dispatch
    recorded with the verdict 'pass', and ZERO founder steps named any of
    it. The founder's own done-definition, the longest-running command in
    the system, had the same window in front of DELIVERABLE_READY.

    The rule this class pins: the founder pressing stop is never followed
    by silent completion of work, at ANY of the windows in
    _R7_COMMAND_SITES."""

    def _kill_when(self, store, command, killed_state):
        """`command` IS the kill switch, which is what the founder pulling
        it in another terminal looks like from in here (round 6's own
        technique, one command later)."""
        def _kill(_call_number):
            _set_contract(store, killed_state, reason="3am kill switch")
            return {"exit_code": 0, "stdout": "", "stderr": ""}
        return FakeCheckRunner({command: _kill})

    def _kill_during_the_verifier(self, store, killed_state):
        _seed(store)
        _sign(store)
        checker = self._kill_when(store, "verify-u1", killed_state)
        engine, run, dispatch_ids = _park_one_async_unit(
            self, store, checker,
            units=[_unit("u1", write_scope=["a.py"], verifier="verify-u1")])
        summary = {}
        outcome = engine.receive_result("p1", dispatch_ids["u1"],
                                        "claims done", ["a.py"],
                                        summary=summary)
        return engine, run, checker, summary, outcome

    def test_a_revoke_during_the_verifier_is_not_an_acceptance(self):
        with tempfile.TemporaryDirectory() as d:
            with bs.Store(d) as store:
                engine, run, checker, summary, outcome = \
                    self._kill_during_the_verifier(store, "revoked")

                self.assertEqual(
                    checker.calls, ["true", "verify-u1"],
                    "the window under test is the VERIFIER's, so both "
                    "commands must really have run: %r" % (checker.calls,))
                self.assertEqual(outcome, "rejected")
                unit = _unit_row(store, run["run_id"], "u1")
                self.assertNotEqual(
                    unit["status"], "DONE",
                    "no unit is accepted under a contract the founder "
                    "killed while its verifier was running")
                dispatch = store.list_dispatches("u1", raw=True)[0]
                self.assertNotEqual(dispatch["status"], "VERIFIED")
                self.assertEqual(store.get(unit["fence_uuid"]).state,
                                 "parked",
                                 "the fence is parked, never released as "
                                 "sound work")

    def test_a_stop_during_the_verifier_is_not_an_acceptance(self):
        with tempfile.TemporaryDirectory() as d:
            with bs.Store(d) as store:
                engine, run, checker, summary, outcome = \
                    self._kill_during_the_verifier(store, "stopped")

                self.assertEqual(outcome, "rejected")
                self.assertNotEqual(
                    _unit_row(store, run["run_id"], "u1")["status"], "DONE")

    def test_a_kill_during_the_verifier_owes_the_founder_a_step(self):
        """The refuter's sharpest line: 'the founder pulled the switch, the
        unit is green, and no founder step names any of it'."""
        with tempfile.TemporaryDirectory() as d:
            with bs.Store(d) as store:
                engine, run, checker, summary, outcome = \
                    self._kill_during_the_verifier(store, "revoked")

                steps = [s["what"] for s in store.list_human_steps(
                    "p1", resolved=False, raw=True)]
                self.assertTrue(
                    any("u1" in s for s in steps),
                    "a founder step names the unit: %r" % (steps,))
                self.assertEqual(summary.get("stop_reason"),
                                 "CONTRACT_NOT_LIVE")

    def _kill_during_the_done_definition(self, store, killed_state):
        _seed(store)
        _sign(store)
        checker = self._kill_when(store, "echo done", killed_state)
        engine, run, dispatch_ids = _park_one_async_unit(self, store, checker)
        summary = {}
        engine.receive_result("p1", dispatch_ids["u1"], "claims done",
                              ["a.py"], summary=summary)
        return engine, run, checker, summary

    def test_a_revoke_during_the_done_definition_declares_no_deliverable(self):
        """The last window: the founder's WHOLE test suite is the longest
        command in the system, and DELIVERABLE_READY was written straight
        after it with nothing re-asked in between."""
        with tempfile.TemporaryDirectory() as d:
            with bs.Store(d) as store:
                engine, run, checker, summary = \
                    self._kill_during_the_done_definition(store, "revoked")

                self.assertIn("echo done", checker.calls,
                              "the done-definition really ran: %r"
                              % (checker.calls,))
                self.assertNotEqual(
                    store.get_run("p1", raw=True)["state"],
                    "DELIVERABLE_READY",
                    "a deliverable declared under a contract the founder "
                    "revoked mid-suite stands behind nothing: %r"
                    % (summary,))
                self.assertEqual(summary.get("stop_reason"),
                                 "CONTRACT_NOT_LIVE")

    def test_a_ceiling_blown_by_the_done_definition_declares_no_deliverable(self):
        """The same window, the founder's OTHER brake: the suite itself is
        what pushes the meter over."""
        with tempfile.TemporaryDirectory() as d:
            with bs.Store(d) as store:
                _seed(store)
                _sign(store, token_ceiling=100, minutes_ceiling=100)

                def _blow(_call_number):
                    store.record_spend("p1", 500, 0, "the suite itself",
                                       "sess1", _actor())
                    return {"exit_code": 0, "stdout": "", "stderr": ""}

                checker = FakeCheckRunner({"echo done": _blow})
                engine, run, dispatch_ids = _park_one_async_unit(
                    self, store, checker)
                summary = {}

                engine.receive_result("p1", dispatch_ids["u1"], "claims done",
                                      ["a.py"], summary=summary)

                self.assertIn("echo done", checker.calls)
                self.assertNotEqual(
                    store.get_run("p1", raw=True)["state"],
                    "DELIVERABLE_READY",
                    "no deliverable is declared over a blown ceiling: %r"
                    % (summary,))
                self.assertEqual(summary.get("stop_reason"), "SPEND_STOP")

    def test_a_live_contract_still_accepts_and_still_delivers(self):
        """THE CONTROL, and it is the one that matters: every window above
        is a no-op on a run nobody killed."""
        with tempfile.TemporaryDirectory() as d:
            with bs.Store(d) as store:
                _seed(store)
                _sign(store)
                checker = FakeCheckRunner()
                engine, run, dispatch_ids = _park_one_async_unit(
                    self, store, checker,
                    units=[_unit("u1", write_scope=["a.py"],
                                 verifier="verify-u1")])
                summary = {}

                outcome = engine.receive_result(
                    "p1", dispatch_ids["u1"], "claims done", ["a.py"],
                    summary=summary)

                self.assertEqual(outcome, "u1")
                self.assertEqual(checker.calls,
                                 ["true", "verify-u1", "echo done"])
                self.assertEqual(store.get_run("p1", raw=True)["state"],
                                 "DELIVERABLE_READY")
                self.assertEqual(summary.get("stop_reason"), "DELIVERED")


class TestR7TheFounderNoteNamesWhatActuallyRan(unittest.TestCase):
    """REFUTATION-6-pushgate.md F2 (HIGH, honesty). On the carve-out path
    the shipped record-result told the founder "NO command was run for this
    result: not the unit's done_check, not its verifier, not the rollback
    and not the whole done-definition" in the same call in which the unit's
    done_check, its verifier and a `git restore` all ran.
    docs/FULL-AUTO.md says, in as many words: "The controller never tells
    you nothing ran when something did."

    The rule this class pins: the sentence is derived from what THIS CALL
    executed, never from which branch wrote it."""

    def _carve_out(self, store, checker):
        """The refuter's own numbers: ceiling 100, pre-spend 99, and a
        result that claims 5, so the breaker is tripped by the result's OWN
        cost and round 6's exemption judges the one already-paid unit."""
        _seed(store)
        _sign(store, token_ceiling=100, minutes_ceiling=100)
        engine, run, dispatch_ids = _park_one_async_unit(self, store, checker)
        store.record_spend("p1", 99, 0, "prior spend", "sess1", _actor())
        return engine, run, dispatch_ids

    def test_the_carve_out_note_does_not_deny_the_command_it_ran(self):
        with tempfile.TemporaryDirectory() as d:
            with bs.Store(d) as store:
                checker = FakeCheckRunner()
                engine, run, dispatch_ids = self._carve_out(store, checker)
                summary = {}

                outcome = engine.receive_result(
                    "p1", dispatch_ids["u1"], "claims done", ["a.py"],
                    cost={"tokens": 5, "minutes": 0}, summary=summary)

                self.assertEqual(outcome, "u1", "the carve-out is unchanged")
                self.assertEqual(checker.calls, ["true"],
                                 "the done_check really ran: %r"
                                 % (checker.calls,))
                self.assertEqual(summary.get("stop_reason"), "SPEND_STOP")
                note = summary.get("note") or ""
                self.assertNotIn(
                    "NO command was run", note,
                    "the note must not deny the command it just ran: %r"
                    % (note,))
                self.assertIn(
                    "done_check", note,
                    "and it must NAME what ran: %r" % (note,))

    def test_the_same_note_still_says_nothing_ran_when_nothing_ran(self):
        """THE CONTROL. The fix is not "delete the sentence": on the leg
        the refuter agrees with, where the ceiling was blown by a separate
        `bm-autonomy spend` and no cost is claimed, the founder must still
        be told that NOTHING was run."""
        with tempfile.TemporaryDirectory() as d:
            with bs.Store(d) as store:
                checker = FakeCheckRunner()
                _seed(store)
                _sign(store, token_ceiling=100, minutes_ceiling=100)
                engine, run, dispatch_ids = _park_one_async_unit(
                    self, store, checker)
                store.record_spend("p1", 500, 500, "the ceiling is blown",
                                   "sess1", _actor())
                summary = {}

                engine.receive_result("p1", dispatch_ids["u1"], "claims done",
                                      ["a.py"], summary=summary)

                self.assertEqual(checker.calls, [])
                self.assertIn("NO command was run",
                              summary.get("note") or "")

    def test_the_delivery_note_names_the_suite_it_just_ran(self):
        """The third use site of the same sentence: `_deliver_or_hold`,
        where the founder's whole done-definition is what ran."""
        with tempfile.TemporaryDirectory() as d:
            with bs.Store(d) as store:
                _seed(store)
                _sign(store, token_ceiling=100, minutes_ceiling=100)

                def _blow(_call_number):
                    store.record_spend("p1", 500, 0, "the suite itself",
                                       "sess1", _actor())
                    return {"exit_code": 0, "stdout": "", "stderr": ""}

                checker = FakeCheckRunner({"echo done": _blow})
                engine, run, dispatch_ids = _park_one_async_unit(
                    self, store, checker)
                summary = {}

                engine.receive_result("p1", dispatch_ids["u1"], "claims done",
                                      ["a.py"], summary=summary)

                note = summary.get("note") or ""
                self.assertNotIn("NO command was run", note,
                                 "the done_check and the whole "
                                 "done-definition both ran: %r"
                                 % (checker.calls,))
                self.assertIn("done-definition", note)

    def test_the_rejection_sentence_is_derived_too(self):
        """The verification row and the founder step _close_without_running
        writes carry the same claim, and a founder reads those long after
        the summary is gone."""
        with tempfile.TemporaryDirectory() as d:
            with bs.Store(d) as store:
                checker = FakeCheckRunner()
                _seed(store)
                _sign(store, token_ceiling=100, minutes_ceiling=100)
                engine, run, dispatch_ids = _park_one_async_unit(
                    self, store, checker)
                store.record_spend("p1", 500, 500, "the ceiling is blown",
                                   "sess1", _actor())

                engine.receive_result("p1", dispatch_ids["u1"], "claims done",
                                      ["a.py"])

                steps = [s["what"] for s in store.list_human_steps(
                    "p1", resolved=False, raw=True)]
                self.assertTrue(
                    any("u1" in s and "Nothing was executed" in s
                        for s in steps),
                    "nothing DID run here, so the step says so: %r"
                    % (steps,))


class TestR7TheJudgingGateRefusesAMalformedScopeContainer(unittest.TestCase):
    """REFUTATION-6-pushgate.md F4 (LOW). The SCOPE half of the round-6
    authorisation gate calls _gate_check_write_scope, which iterates
    `unit["write_scope"] or []` without first asking the container
    question _authorise_dispatch asks at its own top, so a non-iterable
    write_scope left an uncaught TypeError out of receive_result and main()
    catches only bs.BMStoreError and ValueError.

    Not reachable through `plan`, which refuses it; reachable for exactly
    the population _unsafe_scope_entry's docstring names (an older store, a
    hand-written row, an SDK caller). The rule this class pins: the SCOPE
    refusal is TOTAL, and it is a named refusal rather than a traceback."""

    def _judge_a_poisoned_shape(self, store, poison):
        checker = FakeCheckRunner()
        proxy = _ShapePoisoningStore(store)
        _seed(store)
        _sign(store)
        engine, run, dispatch_ids = _park_one_async_unit(self, proxy, checker)
        proxy.poison = poison
        summary = {}
        outcome = engine.receive_result("p1", dispatch_ids["u1"],
                                        "claims done", ["a.py"],
                                        summary=summary)
        return engine, run, checker, summary, outcome

    def test_a_non_iterable_write_scope_is_a_named_refusal(self):
        with tempfile.TemporaryDirectory() as d:
            with bs.Store(d) as store:
                engine, run, checker, summary, outcome = \
                    self._judge_a_poisoned_shape(store, 7)

                self.assertEqual(outcome, "rejected")
                self.assertEqual(checker.calls, [],
                                 "a scope shape this engine cannot read "
                                 "authorises no command: %r"
                                 % (checker.calls,))
                steps = [s["what"] for s in store.list_human_steps(
                    "p1", resolved=False, raw=True)]
                self.assertTrue(any("u1" in s for s in steps),
                                "a NAMED founder-visible refusal: %r"
                                % (steps,))

    def test_a_bare_string_write_scope_is_refused_not_shredded(self):
        """The same gate, walked character by character: 'a.py' becomes
        ['a', '.', 'p', 'y'] and '.' is the whole project root."""
        with tempfile.TemporaryDirectory() as d:
            with bs.Store(d) as store:
                engine, run, checker, summary, outcome = \
                    self._judge_a_poisoned_shape(store, "a.py")

                self.assertEqual(outcome, "rejected")
                self.assertEqual(checker.calls, [])
                self.assertNotEqual(
                    _unit_row(store, run["run_id"], "u1")["status"], "DONE")

    def test_a_clean_list_is_judged_exactly_as_before(self):
        """THE CONTROL: the container question refuses shapes, never
        paths."""
        with tempfile.TemporaryDirectory() as d:
            with bs.Store(d) as store:
                engine, run, checker, summary, outcome = \
                    self._judge_a_poisoned_shape(store, ["a.py"])

                self.assertEqual(outcome, "u1")
                self.assertEqual(checker.calls, ["true", "echo done"])


class TestR7TheReservedLaneIsMechanicallyReserved(unittest.TestCase):
    """REFUTATION-6-pushgate.md F5 (LOW). `spend-reconciliation` is
    documented as "a reserved lane no unit is ever planned into", and
    nothing reserved it: `plan` accepted a unit there, and
    _unreconciled_uncharged_spend selects by LANE alone, so ANY step in
    that lane blocked the whole run's delivery carrying a note that states
    a fact that did not happen ("N accepted unit(s) cost spend this engine
    could NOT charge") while the meter shows nothing uncharged.

    Both halves are closed here: the name is refused at plan time, and the
    disclosure is selected by the marker it writes rather than by its
    lane."""

    def test_plan_refuses_a_unit_in_the_reserved_lane(self):
        with tempfile.TemporaryDirectory() as d:
            with bs.Store(d) as store:
                _seed(store)
                _sign(store)
                engine = _engine(store, FakeWorker({}), FakeCheckRunner())
                run = engine.begin("p1", "ship it", "echo done")

                with self.assertRaises(bc.bs.OwnershipRefused) as caught:
                    engine.plan("p1", run["run_id"],
                                [_unit("u1", write_scope=["a.py"],
                                       lane=bc.SPEND_RECONCILE_LANE)])

                self.assertIn(bc.SPEND_RECONCILE_LANE, str(caught.exception))
                self.assertEqual(store.get_run("p1", raw=True)["state"],
                                 "NEW",
                                 "nothing is written and the run has not "
                                 "moved")

    def test_a_step_that_is_not_a_spend_disclosure_blocks_no_delivery(self):
        """The refuter's probe D2 in its general form: an ordinary founder
        step that happens to sit in that lane must not block delivery
        citing spend that was never uncharged."""
        with tempfile.TemporaryDirectory() as d:
            with bs.Store(d) as store:
                _seed(store)
                _sign(store)
                checker = FakeCheckRunner()
                engine, run, dispatch_ids = _park_one_async_unit(
                    self, store, checker)
                store.queue_human_step(
                    "p1", "", bc.SPEND_RECONCILE_LANE,
                    "an ordinary founder step that has nothing to do with "
                    "spend", "", [], "sess1", _actor())
                summary = {}

                engine.receive_result("p1", dispatch_ids["u1"], "claims done",
                                      ["a.py"], summary=summary)

                self.assertEqual(store.spend_totals("p1")["tokens"], 0)
                self.assertEqual(
                    store.get_run("p1", raw=True)["state"],
                    "DELIVERABLE_READY",
                    "no spend was ever uncharged, so nothing may block "
                    "delivery citing uncharged spend: %r" % (summary,))

    def test_a_real_disclosure_still_blocks_delivery(self):
        """THE CONTROL, and it is round 6's own property: the marker is a
        narrowing of the selection, never a hole in it."""
        with tempfile.TemporaryDirectory() as d:
            with bs.Store(d) as store:
                checker = FakeCheckRunner()
                _seed(store)
                _sign(store, token_ceiling=100, minutes_ceiling=100)
                engine, run, dispatch_ids = _park_one_async_unit(
                    self, store, checker)
                _set_contract(store, "paused", reason="founder pressed pause")
                engine.step("p1")
                engine.receive_result("p1", dispatch_ids["u1"], "claims done",
                                      ["a.py"],
                                      cost={"tokens": 90, "minutes": 5})
                _set_contract(store, "live", reason="founder resumed")
                _resume_the_run(store, run)

                summary = engine.step("p1")

                self.assertNotEqual(
                    store.get_run("p1", raw=True)["state"],
                    "DELIVERABLE_READY",
                    "90 tokens were claimed and 0 metered: %r" % (summary,))
                self.assertEqual(summary["stop_reason"], "SPEND_STOP")


_R7_NO_POISON = object()


class _ShapePoisoningStore(object):
    """A pass-through proxy over a REAL Store that hands the engine a
    write_scope of any SHAPE, including shapes no `list()` survives.

    _ScopePoisoningStore above cannot do this job: it calls `list(poison)`,
    which is precisely the operation F4 is about. Same purpose as that one
    otherwise, and the same justification: the DECLARATION side refuses
    these, so proving the ENGINE refuses them too needs a row the shipped
    `plan` would never write."""

    def __init__(self, store):
        self._store = store
        self.poison = _R7_NO_POISON

    def __getattr__(self, name):
        return getattr(self._store, name)

    def _poisoned(self, row):
        if self.poison is _R7_NO_POISON:
            return row
        out = dict(row)
        out["write_scope"] = self.poison
        return out

    def select_ready_units(self, run_id):
        return [self._poisoned(u)
                for u in self._store.select_ready_units(run_id)]

    def list_units(self, run_id, raw=False):
        return [self._poisoned(u)
                for u in self._store.list_units(run_id, raw=raw)]


#: REFUTATION-6-pushgate.md F3's five surviving spellings, in the refuter's
#: own order and naming. Same SECURITY NOTE as _S5_BYPASS_MUTATIONS above:
#: these are inert TEXT, never compiled, never exec'd, never written to
#: disk. The only thing done with them is ast.parse.
_R7_BYPASS_MUTATIONS = (
    ("NEW-A  from subprocess import run as X; X(cmd, shell=True)",
     "        from subprocess import run as X\n"
     "        X('echo pwned', shell=True)\n"),
    ("NEW-B  import subprocess as sp; sp.run(cmd, ...)",
     "        import subprocess as sp\n"
     "        sp.run('echo pwned', shell=True)\n"),
    ("NEW-C  from os import system as X; X(cmd)",
     "        from os import system as X\n"
     "        X('echo pwned')\n"),
    ("NEW-D  operator.attrgetter('checker')(self).run(cmd, ...)",
     "        import operator\n"
     "        operator.attrgetter('checker')(self).run('echo pwned', "
     "cwd='.')\n"),
    ("NEW-E  import subprocess as _sp; _sp.Popen(cmd, ...)",
     "        import subprocess as _sp\n"
     "        _sp.Popen('echo pwned', shell=True)\n"),
)

#: The spellings this writer hunted for AFTER closing the refuter's five,
#: by attacking the NEW guard rather than the old one. Each one really
#: reaches an ungated command or the gated object; each must FAIL.
_R7_TENTH_SPELLING_HUNT = (
    ("HUNT-1 an alias of SELF, not of the checker",
     "        holder = self\n"
     "        holder.checker.run('echo pwned', cwd='.')\n"),
    ("HUNT-2 the builtins namespace, subscripted",
     "        __builtins__['getattr'](self, 'checker').run('echo pwned', "
     "cwd='.')\n"),
    ("HUNT-3 importlib.import_module, which needs no new import name",
     "        importlib.import_module('os').system('echo pwned')\n"),
    ("HUNT-4 a banned primitive bound WITHOUT being called",
     "        runner = os.system\n"
     "        runner('echo pwned')\n"),
    ("HUNT-5 the gated object handed back out of the gate",
     "        return self.checker\n"),
    # HUNT-6 and HUNT-7 are the ones that landed: found by attacking the
    # round-7 guard, both PASSED it, and both are closed by
    # _BANNED_MODULE_ATTRIBUTE_NAMES. `bs` is tools/bm_store.py, loaded by
    # path, and it imports os, so the second one really starts a process.
    ("HUNT-6 subprocess borrowed from the loaded sibling module's namespace",
     "        bs.subprocess.run('echo pwned', shell=True)\n"),
    ("HUNT-7 os.system borrowed the same way, which really works",
     "        bs.os.system('echo pwned')\n"),
    ("HUNT-8 a SECOND class in the module, calling subprocess directly",
     "        pass\n\n\nclass Sneaky(object):\n    def go(self, cmd):\n"
     "        subprocess.run(cmd, shell=True)\n"),
    ("HUNT-9 self.__dict__ to reach the checker without naming it",
     "        self.__dict__['checker'].run('echo pwned', cwd='.')\n"),
    # The second hunt round, run after the first five were closed. This one
    # landed too: every other rule gates the checker OBJECT, and a SECOND
    # runner is not that object.
    ("HUNT-14 a second SubprocessCheckRunner, built outside the gate",
     "        SubprocessCheckRunner().run('echo pwned', cwd='.')\n"),
)


class TestR7TheGuardCoversAliasedExecutionPrimitives(unittest.TestCase):
    """REFUTATION-6-pushgate.md F3 (LOW). The round-6 guard built a name
    from the LOCAL identifier and then tested it against literal spellings,
    and read no ast.Import or ast.ImportFrom at all, so five more spellings
    passed it and four of those really started a process under a revoked
    contract.

    The shipped file was clean then and is clean now; what was overstated
    is the CLAIM. This class holds the claim to the higher bar."""

    def test_the_shipped_file_still_passes_the_guard(self):
        with io.open(CONTROLLER_FILE, encoding="utf-8") as fh:
            source = fh.read()
        self.assertEqual(_execution_primitive_offences(source), [],
                         "the shipped tools/bm_controller.py is clean")

    def test_the_round_six_four_still_fail(self):
        """The old calibration is KEPT, never replaced: a guard rebuilt to
        catch five new spellings that stopped catching the four it already
        caught would be a regression wearing a fix's clothes."""
        for label, fragment in _S5_BYPASS_MUTATIONS:
            self.assertNotEqual(
                _execution_primitive_offences(
                    _mutated_controller_source(fragment)), [],
                "the guard must still FAIL for: %s" % (label,))

    def test_each_of_the_five_surviving_spellings_now_fails(self):
        for label, fragment in _R7_BYPASS_MUTATIONS:
            self.assertNotEqual(
                _execution_primitive_offences(
                    _mutated_controller_source(fragment)), [],
                "the guard must FAIL for: %s" % (label,))

    def test_the_tenth_spelling_hunt_fails_too(self):
        """Attacking the NEW guard rather than the old one, which is the
        only way a calibration corpus stays evidence."""
        for label, fragment in _R7_TENTH_SPELLING_HUNT:
            self.assertNotEqual(
                _execution_primitive_offences(
                    _mutated_controller_source(fragment)), [],
                "the guard must FAIL for: %s" % (label,))

    def test_the_import_list_is_pinned_by_name(self):
        """The cheapest half of the fix, and the most total: an execution
        primitive that needs an import cannot get one. Hard-coded, so a
        future import is a deliberate edit here rather than a silent
        widening."""
        with io.open(CONTROLLER_FILE, encoding="utf-8") as fh:
            source = fh.read()
        self.assertEqual(_module_import_names(source),
                         sorted(_ALLOWED_MODULE_IMPORTS))


# ---------------------------------------------------------------------------
# CROSS-FAMILY REFUTATION (2026-08-05), FINDING 2: a worker result that is
# not a dict at all. Engine tests, hermetic, no subprocess: they belong to
# the section above rather than to the CLI section below.
# ---------------------------------------------------------------------------

def _handle_worker_result_shape_check_ordering():
    """(line of the first isinstance(result, ...) test, line of the first
    read of `result`) inside ControllerEngine._handle_worker_result.
    Either is None when that spelling is not there at all. Source only:
    nothing is imported and nothing is executed."""
    with io.open(CONTROLLER_FILE, encoding="utf-8") as fh:
        tree = ast.parse(fh.read(), filename="bm_controller.py")
    target = None
    for node in ast.walk(tree):
        if (isinstance(node, ast.FunctionDef)
                and node.name == "_handle_worker_result"):
            target = node
            break
    if target is None:
        return None, None
    shape_line = None
    read_line = None
    for node in ast.walk(target):
        if (isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
                and node.func.id == "isinstance"
                and node.args and isinstance(node.args[0], ast.Name)
                and node.args[0].id == "result"):
            if shape_line is None or node.lineno < shape_line:
                shape_line = node.lineno
        if (isinstance(node, (ast.Attribute, ast.Subscript))
                and isinstance(node.value, ast.Name)
                and node.value.id == "result"):
            if read_line is None or node.lineno < read_line:
                read_line = node.lineno
    return shape_line, read_line


class TestCrossFamilyF2NonDictWorkerResult(unittest.TestCase):
    """CROSS-FAMILY REFUTATION finding 2 (MEDIUM).

    `_handle_worker_result` read `result.get("status")` BEFORE the
    malformed-result checks below it, so an SDK WorkerAdapter returning
    None, a list, a string or an int raised AttributeError out of the
    engine instead of recording a rejected malformed result. The
    reproduction, before the fix, identical for all four shapes:

        payload=None   -> RAISED AttributeError: 'NoneType' object has no
                          attribute 'get'
           unit status=DISPATCHED retry=0 run=EXECUTING
           dispatches=['DISPATCHED']

    The unit was left DISPATCHED, the run EXECUTING and the fence active,
    which is the wedge: the next step finds an orphaned dispatch rather
    than a rejection it can retry.

    The rule pinned here: the SHAPE question is asked first, and a result
    that is not a dict becomes the SAME recorded rejection every other
    malformed result gets (dispatch REJECTED, verdict 'malformed worker
    output', the unit back through the circuit breaker)."""

    _BASELINE = {"status": "malformed"}

    def _drive(self, payload):
        """One throwaway store, one unit, one worker returning `payload`.
        Returns (outcome, unit row, dispatch rows, run state)."""
        with tempfile.TemporaryDirectory() as d:
            with bs.Store(d) as store:
                _seed(store)
                _sign(store)
                engine = _engine(store, FakeWorker({"u1": payload}),
                                 FakeCheckRunner())
                run = _begin_and_plan(engine, store, "p1", [
                    _unit("u1", write_scope=["a.py"])])
                outcome = engine.step("p1")
                return (outcome, _unit_row(store, run["run_id"], "u1"),
                        store.list_dispatches("u1", raw=True),
                        store.get_run("p1", raw=True)["state"])

    def _assert_same_as_a_malformed_dict(self, payload):
        try:
            _outcome, row, dispatches, state = self._drive(payload)
        except AttributeError as exc:
            self.fail("a worker result of %r crashed the controller with "
                      "%r instead of being recorded as a malformed result; "
                      "the unit is left DISPATCHED, the run EXECUTING and "
                      "the fence active" % (payload, exc))
        _b, b_row, b_dispatches, b_state = self._drive(self._BASELINE)
        self.assertEqual([d["status"] for d in dispatches],
                         [d["status"] for d in b_dispatches],
                         "a non-dict result must reach the SAME recorded "
                         "rejection a malformed dict reaches")
        self.assertEqual([d["verifier_verdict"] for d in dispatches],
                         [d["verifier_verdict"] for d in b_dispatches])
        self.assertEqual(row["status"], b_row["status"])
        self.assertEqual(row["retry_count"], b_row["retry_count"])
        self.assertEqual(state, b_state)
        self.assertNotEqual(row["status"], "DISPATCHED",
                            "the unit must not be left DISPATCHED")

    def test_a_none_result_is_recorded_as_malformed(self):
        self._assert_same_as_a_malformed_dict(None)

    def test_a_list_result_is_recorded_as_malformed(self):
        self._assert_same_as_a_malformed_dict(["artifacts"])

    def test_a_string_result_is_recorded_as_malformed(self):
        self._assert_same_as_a_malformed_dict("done")

    def test_an_int_result_is_recorded_as_malformed(self):
        self._assert_same_as_a_malformed_dict(7)

    def test_the_shape_check_precedes_every_read_of_the_result(self):
        """The behavioural tests above prove the four shapes this refuter
        named. This pins the ORDER, which is the property that makes every
        OTHER shape safe too: a future edit that reads the result before
        asking what it is reopens the whole class."""
        shape_line, read_line = _handle_worker_result_shape_check_ordering()
        self.assertIsNotNone(
            shape_line,
            "_handle_worker_result must ask isinstance(result, ...) before "
            "it reads anything off the result")
        self.assertIsNotNone(read_line)
        self.assertLess(
            shape_line, read_line,
            "_handle_worker_result reads the result at line %s, before the "
            "shape check at line %s; the shape question comes first"
            % (read_line, shape_line))


# ---------------------------------------------------------------------------
# Writer B (loop L03): CLI tests for the main()/cli() dispatch appended to
# tools/bm_controller.py by this same loop. Every test below drives
# tools/bm_controller.py as a REAL SUBPROCESS against a fresh
# tempfile.TemporaryDirectory() root, never in-process and never against
# any of Writer A's engine tests above (none of those are read, modified,
# or shared with this section). HOME and BROTHERMODE_VAULT are ALSO
# pointed at fresh temp locations in every subprocess env, exactly
# tools/test_bm_autonomy.py's own _env discipline, so a test here can
# never read or write the real founder's home directory or vault.
# ---------------------------------------------------------------------------

STORE_CLI = os.path.join(HERE, "bm_store.py")
PROJECT_CLI = os.path.join(HERE, "bm_project.py")
AUTONOMY_CLI = os.path.join(HERE, "bm_autonomy.py")

# One STABLE session id across every invocation of a multi-command CLI
# flow, the documented convention for multi-invocation CLI workflows
# (tools/bm_store.py, _default_cli_session_id's own docstring: "a human
# doing a multi-step CLI workflow ... must pass the SAME --session
# explicitly across those invocations"). Load-bearing since L09 GAP 2: a
# controller run has ONE driver (the recorded session), so the start /
# step / record-result / stop flow these tests drive must arrive as one
# driver, exactly as a founder's own scripted flow would.
# TestControllerCLIAdopt builds its flags by hand instead, because its
# whole subject is two DIFFERENT sessions meeting the same run.
CLI_ACTOR = ("--actor-name", "tester", "--session-id", "sess-cli-tester")

#: A done_check that exits 0, and one that does not, on every platform in
#: this repository's CI matrix.
#:
#: These strings are EXECUTED for real: SubprocessCheckRunner runs the
#: unit's done_check and the contract's whole done-definition with
#: shell=True (tools/bm_controller.py), so anything written here has to be
#: a command the runner's shell can actually resolve. `true` and `false`
#: stood here until the 2026-08-05 Windows audit (F9) and are POSIX shell
#: builtins: cmd.exe has neither, and there is no true.exe in System32.
#: They resolve on a GitHub-hosted Windows runner only because that image
#: happens to put Git for Windows' usr/bin on PATH, which is a property of
#: the runner image rather than of Windows or of anything this repository
#: controls, and which that image's own maintainers have said is slated to
#: change. The day it changes, every CLI test that reaches a done-check
#: flips to a REJECTED unit and reads like a controller bug rather than a
#: shell-portability one.
#:
#: The interpreter running this suite is the one dependency that cannot go
#: missing, so it is the one used, quoted with the project's own helper
#: (tools/bm_store.py::_quote_path_for_local_shell) rather than with
#: shlex, for the reason that helper's docstring gives. One constant each,
#: not four literals, so a later reader cannot reintroduce half of it.
_DONE_CHECK_PASSES = ("%s -c pass"
                      % bs._quote_path_for_local_shell(sys.executable))
_DONE_CHECK_FAILS = ('%s -c "raise SystemExit(1)"'
                     % bs._quote_path_for_local_shell(sys.executable))

_UNIT_ONE = {
    "unit_id": "u1", "objective": "create a file", "dependencies": [],
    "write_scope": ["out.txt"], "role": "builder", "risk_class": "file-edit",
    "lane": "default", "done_check": _DONE_CHECK_PASSES,
    "done_check_expect_exit": 0,
}


def _cli_env(root):
    """A fresh environment for one subprocess call: BROTHERMODE_ROOT,
    BROTHERMODE_VAULT and the HOME DIRECTORY are all pointed at
    directories under this same throwaway root, so nothing this section
    runs can touch the real founder's home directory or vault.
    Structurally identical to tools/test_bm_autonomy.py's own _env.

    USERPROFILE joined HOME on 2026-08-05 (Windows audit, F8). Setting
    HOME alone made the sentence above FALSE on one platform: Python
    resolves `~` through ntpath.expanduser there, which reads USERPROFILE
    first and then HOMEDRIVE plus HOMEPATH, and consults HOME only on
    POSIX. Nothing in tools/bm_store.py or tools/bm_controller.py calls
    expanduser today (grepped, zero hits), so no test was failing over
    it; the guard was simply inert on Windows while its own comment
    claimed otherwise, and the first caller to reach for the home
    directory would have inherited a false sense of isolation."""
    home = os.path.join(root, "home")
    vault = os.path.join(root, "vault")
    os.makedirs(home, exist_ok=True)
    e = dict(os.environ)
    for key in ("BROTHERMODE_ROOT", "BROTHERMODE_VAULT", "HOME",
                "USERPROFILE"):
        e.pop(key, None)
    e["BROTHERMODE_ROOT"] = root
    e["BROTHERMODE_VAULT"] = vault
    e["HOME"] = home
    e["USERPROFILE"] = home
    return e


def _run_cli(args, root, extra_env=None):
    """Drive tools/bm_controller.py's own CLI as a real subprocess against
    `root`."""
    e = _cli_env(root)
    if extra_env:
        e.update(extra_env)
    return subprocess.run([sys.executable, CONTROLLER_FILE] + list(args),
                          cwd=root, capture_output=True, text=True, env=e)


def _run_other(cli_path, args, root):
    """Drive a DIFFERENT tool's CLI (bm_store.py, bm_project.py,
    bm_autonomy.py) as a real subprocess against the same `root`, for the
    setup every CLI test needs before bm_controller.py has anything to
    act on."""
    e = _cli_env(root)
    return subprocess.run([sys.executable, cli_path] + list(args),
                          cwd=root, capture_output=True, text=True, env=e)


def _cli_init(root):
    r = _run_other(STORE_CLI, ["init"], root)
    if r.returncode != 0:
        raise AssertionError("bm_store.py init failed: %s" % r.stderr)
    return r


def _cli_start_project(root, project_id="p1", extra=()):
    args = (["start", "--project-id", project_id, "--name", "Test Project"]
           + list(CLI_ACTOR) + list(extra))
    r = _run_other(PROJECT_CLI, args, root)
    if r.returncode != 0:
        raise AssertionError("bm_project.py start failed: %s" % r.stderr)
    return r


def _cli_sign(root, project_id="p1", extra=()):
    args = (["sign", "--project", project_id, "--outcome", "ship it",
            "--done-definition", _DONE_CHECK_PASSES,
            "--signed-by", "Khalil Maaouni"]
           + list(CLI_ACTOR) + list(extra))
    r = _run_other(AUTONOMY_CLI, args, root)
    if r.returncode != 0:
        raise AssertionError("bm_autonomy.py sign failed: %s" % r.stderr)
    return r


def _cli_bootstrap(root, project_id="p1"):
    """A ready-to-drive store: initialized, one project started, a live
    contract signed with file-edit granted, no ceiling, path '.'
    allowed."""
    _cli_init(root)
    _cli_start_project(root, project_id)
    _cli_sign(root, project_id,
             extra=("--allowed-path", ".", "--risk-class", "file-edit"))


def _cli_begin(root, project_id="p1", controller_id="c1"):
    r = _run_cli(
        ["start", "--project", project_id, "--outcome", "ship it",
         "--done-definition", _DONE_CHECK_PASSES,
         "--controller-id", controller_id]
        + list(CLI_ACTOR), root)
    if r.returncode != 0:
        raise AssertionError("start (begin) failed: %s" % (r.stdout + r.stderr))
    return r


def _cli_plan_one_unit(root, project_id="p1", controller_id="c1"):
    units_path = os.path.join(root, "units.json")
    with io.open(units_path, "w", encoding="utf-8") as fh:
        json.dump([_UNIT_ONE], fh)
    r = _run_cli(
        ["plan", "--project", project_id, "--units-file", units_path,
         "--controller-id", controller_id] + list(CLI_ACTOR), root)
    if r.returncode != 0:
        raise AssertionError("plan failed: %s" % (r.stdout + r.stderr))
    return r


class TestControllerCLIStartResumesWithNoDuplicateWork(unittest.TestCase):
    """The CLI's own start/plan/status/record-result loop, end to end
    through real subprocesses: start BEGINS a fresh run, plan upserts one
    unit, start again RESUMES and dispatches it, parking rather than
    busy-looping (see tools/bm_controller.py's own
    _drive_until_parked docstring: driving the resume path through
    engine.run_to_completion(max_steps=500) directly against the real
    RecordIntentWorker was reproduced printing the SAME unit brief to
    stdout up to 500 times in one `start` call, because a dispatch that
    parks 'pending' never satisfies run_to_completion's own stop-state
    set; _drive_until_parked exists because of that reproduction, and the
    assertion below on brief_lines is the regression guard for it),
    status --json exposes the dispatch id record-result needs (a brief
    carries no dispatch_id of its own: it is the WORKER's input, and
    dispatch_id is the controller's own bookkeeping), record-result
    accepts it, and the run reaches DELIVERABLE_READY with the unit DONE
    exactly once."""

    def test_start_begins_then_resumes_and_completes_with_no_duplicate_work(self):
        with tempfile.TemporaryDirectory() as root:
            _cli_bootstrap(root)

            begin = _cli_begin(root)
            self.assertIn("started for project p1", begin.stdout)
            self.assertIn("state NEW", begin.stdout)

            planned = _cli_plan_one_unit(root)
            self.assertIn("planned 1 unit(s)", planned.stdout)

            resumed = _run_cli(
                ["start", "--project", "p1", "--controller-id", "c1"]
                + list(CLI_ACTOR), root)
            self.assertEqual(resumed.returncode, 0,
                             resumed.stdout + resumed.stderr)
            brief_lines = [l for l in resumed.stdout.splitlines()
                          if "controller_brief" in l]
            self.assertEqual(
                len(brief_lines), 1,
                "start printed the unit brief %d time(s) in one call; it "
                "must dispatch once and park, awaiting record-result, "
                "never busy-loop on its own printed brief: %r"
                % (len(brief_lines), resumed.stdout))
            self.assertIn("dispatched 1 unit(s), completed 0 unit(s)",
                          resumed.stdout)

            status1 = _run_cli(["status", "--project", "p1", "--json"], root)
            self.assertEqual(status1.returncode, 0,
                             status1.stdout + status1.stderr)
            payload1 = json.loads(status1.stdout)
            dispatch_id = payload1["open_dispatches"]["u1"]
            self.assertTrue(dispatch_id)

            recorded = _run_cli(
                ["record-result", "--project", "p1", "--dispatch-id",
                 dispatch_id, "--worker-claim", "created out.txt",
                 "--controller-id", "c1"] + list(CLI_ACTOR), root)
            self.assertEqual(recorded.returncode, 0,
                             recorded.stdout + recorded.stderr)
            self.assertIn("unit u1 accepted", recorded.stdout)

            status2 = _run_cli(["status", "--project", "p1"], root)
            self.assertEqual(status2.returncode, 0,
                             status2.stdout + status2.stderr)
            self.assertIn("state DELIVERABLE_READY", status2.stdout)
            self.assertIn("DONE: 1", status2.stdout)

            # No duplicate work: exactly one dispatch for u1 ever, and it
            # is VERIFIED, never a second attempt.
            raw_store = bs.Store(root, create=False)
            try:
                dispatches = raw_store.list_dispatches("u1", raw=True)
            finally:
                raw_store.close()
            self.assertEqual(len(dispatches), 1)
            self.assertEqual(dispatches[0]["status"], "VERIFIED")

            # start again is a clean no-op: DELIVERABLE_READY has no
            # further autonomous progress, and start must not re-dispatch
            # the already-DONE unit.
            again = _run_cli(
                ["start", "--project", "p1", "--controller-id", "c1"]
                + list(CLI_ACTOR), root)
            self.assertEqual(again.returncode, 0,
                             again.stdout + again.stderr)
            self.assertNotIn("controller_brief", again.stdout)
            raw_store = bs.Store(root, create=False)
            try:
                dispatches_again = raw_store.list_dispatches("u1", raw=True)
            finally:
                raw_store.close()
            self.assertEqual(
                len(dispatches_again), 1,
                "resuming a DELIVERABLE_READY run must never re-dispatch "
                "an already-DONE unit")


class TestControllerCLIStatusReport(unittest.TestCase):
    """status is the CLI's one-screen report: it must carry every field a
    founder or an orchestrating model reads from it, and refuse cleanly,
    naming the fix, when there is nothing to report yet."""

    def test_status_prints_the_one_screen_report(self):
        with tempfile.TemporaryDirectory() as root:
            _cli_bootstrap(root)
            _cli_begin(root)
            r = _run_cli(["status", "--project", "p1"], root)
            self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
            for fragment in ("project p1: run ", "outcome:",
                             "done definition:", "units:",
                             "spend verdict:", "open human steps:",
                             "last checkpoint:"):
                self.assertIn(fragment, r.stdout,
                              "status is missing %r: %r"
                              % (fragment, r.stdout))

    def test_status_json_is_redacted_by_default_and_raw_reveals_the_outcome(self):
        with tempfile.TemporaryDirectory() as root:
            _cli_bootstrap(root)
            _cli_begin(root)
            redacted = _run_cli(["status", "--project", "p1", "--json"],
                                root)
            self.assertEqual(redacted.returncode, 0)
            self.assertNotIn("ship it", redacted.stdout)
            raw = _run_cli(["status", "--project", "p1", "--json", "--raw"],
                           root)
            self.assertEqual(raw.returncode, 0)
            self.assertIn("ship it", raw.stdout)

    def test_status_without_a_run_refuses_and_names_start(self):
        with tempfile.TemporaryDirectory() as root:
            _cli_bootstrap(root)
            r = _run_cli(["status", "--project", "p1"], root)
            self.assertEqual(r.returncode, 1)
            self.assertIn("no controller run", r.stderr)
            self.assertIn("start", r.stderr)


class TestControllerCLIStopDrains(unittest.TestCase):
    """stop is the founder's kill switch on the run itself: it must drain
    (release every fence this run held, including its own controller-
    level fence) rather than abandon them, and it must be idempotent, the
    same 3am-kill-switch posture tools/bm_autonomy.py's own stop takes on
    the contract."""

    def test_stop_drains_releases_every_fence_and_is_idempotent(self):
        with tempfile.TemporaryDirectory() as root:
            _cli_bootstrap(root)
            _cli_begin(root)
            _cli_plan_one_unit(root)
            _run_cli(["start", "--project", "p1", "--controller-id", "c1"]
                     + list(CLI_ACTOR), root)  # dispatch u1, park

            stopped = _run_cli(
                ["stop", "--project", "p1", "--controller-id", "c1"]
                + list(CLI_ACTOR), root)
            self.assertEqual(stopped.returncode, 0,
                             stopped.stdout + stopped.stderr)
            self.assertIn("-> STOPPED", stopped.stdout)

            raw_store = bs.Store(root, create=False)
            try:
                records = raw_store.dump(raw=True)["records"]
            finally:
                raw_store.close()
            active = [r for r in records if r["state"] == "active"]
            self.assertEqual(
                active, [],
                "stop must drain and release every fence this run held, "
                "including the unit fence it dispatched and its own "
                "controller-level fence: still active: %r" % active)

            again = _run_cli(
                ["stop", "--project", "p1", "--controller-id", "c1"]
                + list(CLI_ACTOR), root)
            self.assertEqual(again.returncode, 0,
                             again.stdout + again.stderr)
            self.assertIn("STOPPED -> STOPPED", again.stdout)

    def test_stop_with_no_run_at_all_is_a_clean_no_op(self):
        with tempfile.TemporaryDirectory() as root:
            _cli_bootstrap(root)
            r = _run_cli(
                ["stop", "--project", "p1", "--controller-id", "c1"]
                + list(CLI_ACTOR), root)
            self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
            self.assertIn("nothing to stop", r.stdout)


class TestControllerCLIPauseAndResume(unittest.TestCase):
    """step must notice a contract the founder paused (a store read, not
    a signal this process has to catch) and resume must be the founder's
    own reverse of it; both read straight off tools/bm_autonomy.py's own
    pause/resume, proving the two CLIs agree on the same contract."""

    def test_step_detects_a_paused_contract_and_resume_reverses_it(self):
        with tempfile.TemporaryDirectory() as root:
            _cli_bootstrap(root)
            _cli_begin(root)
            _run_other(AUTONOMY_CLI,
                      ["pause", "--project", "p1", "--reason", "lunch"]
                      + list(CLI_ACTOR), root)

            stepped = _run_cli(
                ["step", "--project", "p1", "--controller-id", "c1"]
                + list(CLI_ACTOR), root)
            self.assertEqual(stepped.returncode, 0,
                             stepped.stdout + stepped.stderr)
            self.assertIn("state PAUSED", stepped.stdout)

            resumed = _run_cli(["resume", "--project", "p1"]
                               + list(CLI_ACTOR), root)
            self.assertEqual(resumed.returncode, 0,
                             resumed.stdout + resumed.stderr)
            self.assertIn("PAUSED -> READY", resumed.stdout)

            again = _run_cli(["resume", "--project", "p1"]
                             + list(CLI_ACTOR), root)
            self.assertEqual(again.returncode, 0, again.stdout + again.stderr)
            self.assertIn("Nothing to do.", again.stdout)

    def test_resume_with_no_run_at_all_refuses(self):
        with tempfile.TemporaryDirectory() as root:
            _cli_bootstrap(root)
            r = _run_cli(["resume", "--project", "p1"] + list(CLI_ACTOR),
                        root)
            self.assertEqual(r.returncode, 1)
            self.assertIn("no controller run", r.stderr)


class TestControllerCLIComplete(unittest.TestCase):
    """complete is DELIVERABLE_READY -> COMPLETE, a founder action the
    design states is never a controller self-move; it must refuse from
    any other state (the store's own CONTROLLER_STATE_TRANSITIONS
    legality, never re-implemented here) and be idempotent once
    COMPLETE."""

    def test_complete_refuses_before_deliverable_ready_then_succeeds_and_is_idempotent(self):
        with tempfile.TemporaryDirectory() as root:
            _cli_bootstrap(root)
            _cli_begin(root)

            too_early = _run_cli(["complete", "--project", "p1"]
                                 + list(CLI_ACTOR), root)
            self.assertEqual(too_early.returncode, 1)

            _cli_plan_one_unit(root)
            _run_cli(["start", "--project", "p1", "--controller-id", "c1"]
                     + list(CLI_ACTOR), root)
            status1 = _run_cli(["status", "--project", "p1", "--json"], root)
            dispatch_id = json.loads(status1.stdout)["open_dispatches"]["u1"]
            _run_cli(
                ["record-result", "--project", "p1", "--dispatch-id",
                 dispatch_id, "--controller-id", "c1"] + list(CLI_ACTOR),
                root)

            completed = _run_cli(["complete", "--project", "p1"]
                                 + list(CLI_ACTOR), root)
            self.assertEqual(completed.returncode, 0,
                             completed.stdout + completed.stderr)
            self.assertIn("-> COMPLETE", completed.stdout)

            again = _run_cli(["complete", "--project", "p1"]
                             + list(CLI_ACTOR), root)
            self.assertEqual(again.returncode, 0, again.stdout + again.stderr)
            self.assertIn("already COMPLETE", again.stdout)


class TestControllerCLIRestore(unittest.TestCase):
    """F3's own done-check, at the CLI, across real OS processes rather
    than Python objects in one interpreter: `start` runs as one
    subprocess and EXITS (this system has no long-lived controller
    daemon to SIGKILL mid-run the way scripts/test_crash_resume.py does
    to loop_bridge.py; a subprocess that has already exited is the
    closest a one-shot CLI process gets to 'the session is gone'), then
    a SECOND, unrelated subprocess invocation of `restore` reads the
    checkpoint that first process recorded automatically (step's own
    heartbeat checkpoint, tools/bm_controller.py step 7) and reconstructs
    the run from nothing but the on-disk store."""

    def test_restore_reads_a_checkpoint_recorded_by_a_process_that_already_exited(self):
        with tempfile.TemporaryDirectory() as root:
            _cli_bootstrap(root)
            _cli_begin(root)
            _cli_plan_one_unit(root)

            stepped = _run_cli(
                ["start", "--project", "p1", "--controller-id", "c1"]
                + list(CLI_ACTOR), root)
            self.assertEqual(stepped.returncode, 0,
                             stepped.stdout + stepped.stderr)
            # `stepped` has already returned: that OS process is gone.

            status = _run_cli(["status", "--project", "p1", "--json"], root)
            self.assertEqual(status.returncode, 0, status.stderr)
            last_checkpoint = json.loads(status.stdout)["last_checkpoint"]
            self.assertIsNotNone(
                last_checkpoint, "start must have recorded a heartbeat "
                "checkpoint before dispatching")
            checkpoint_id = last_checkpoint["checkpoint_id"]

            restored = _run_cli(
                ["restore", "--project", "p1", "--checkpoint",
                 checkpoint_id, "--controller-id", "c2", "--actor-name",
                 "restorer", "--json"], root)
            self.assertEqual(restored.returncode, 0,
                             restored.stdout + restored.stderr)
            payload = json.loads(restored.stdout)
            self.assertEqual(payload["checkpoint"]["checkpoint_id"],
                             checkpoint_id)
            self.assertEqual(payload["run_state"], "EXECUTING")
            unit_ids = [u["unit_id"] for u in payload["units"]]
            self.assertIn("u1", unit_ids)

    def test_restore_of_an_unknown_checkpoint_refuses(self):
        with tempfile.TemporaryDirectory() as root:
            _cli_bootstrap(root)
            r = _run_cli(
                ["restore", "--project", "p1", "--checkpoint", "no-such-id",
                 "--controller-id", "c1", "--actor-name", "restorer"], root)
            self.assertEqual(r.returncode, 1)
            self.assertIn("no checkpoint", r.stderr)


class TestControllerCLIExitCodes(unittest.TestCase):
    """0 success, 1 refusal, 2 usage error, the identical law
    tools/bm_autonomy.py states for itself, checked here command by
    command rather than assumed from one example."""

    def test_bare_invocation_prints_help_and_exits_zero(self):
        with tempfile.TemporaryDirectory() as root:
            r = _run_cli([], root)
            self.assertEqual(r.returncode, 0)
            self.assertIn("commands:", r.stdout)

    def test_an_unknown_command_exits_two_and_names_it(self):
        with tempfile.TemporaryDirectory() as root:
            r = _run_cli(["not-a-command"], root)
            self.assertEqual(r.returncode, 2)
            self.assertIn("not-a-command", r.stderr)

    def test_an_unrecognized_flag_exits_two(self):
        with tempfile.TemporaryDirectory() as root:
            r = _run_cli(["start", "--not-a-real-flag", "x"], root)
            self.assertEqual(r.returncode, 2)

    def test_a_missing_required_flag_exits_two_naming_it(self):
        with tempfile.TemporaryDirectory() as root:
            _cli_bootstrap(root)
            r = _run_cli(["start", "--project", "p1"] + list(CLI_ACTOR),
                        root)
            self.assertEqual(r.returncode, 2)
            self.assertIn("controller-id", r.stderr)

    def test_step_with_no_run_refuses_at_exit_one(self):
        with tempfile.TemporaryDirectory() as root:
            _cli_bootstrap(root)
            r = _run_cli(
                ["step", "--project", "p1", "--controller-id", "c1"]
                + list(CLI_ACTOR), root)
            self.assertEqual(r.returncode, 1)
            self.assertIn("no controller run", r.stderr)

    def test_a_bad_actor_type_exits_two(self):
        with tempfile.TemporaryDirectory() as root:
            _cli_bootstrap(root)
            r = _run_cli(
                ["step", "--project", "p1", "--controller-id", "c1",
                 "--actor-name", "tester", "--actor-type", "robot"], root)
            self.assertEqual(r.returncode, 2)

    def test_a_negative_tokens_flag_on_record_result_is_a_usage_error(self):
        with tempfile.TemporaryDirectory() as root:
            _cli_bootstrap(root)
            r = _run_cli(
                ["record-result", "--project", "p1", "--dispatch-id",
                 "ghost", "--tokens", "-5", "--controller-id", "c1"]
                + list(CLI_ACTOR), root)
            self.assertEqual(r.returncode, 2)

    def test_plan_needs_units_file_or_units_json(self):
        with tempfile.TemporaryDirectory() as root:
            _cli_bootstrap(root)
            _cli_begin(root)
            r = _run_cli(
                ["plan", "--project", "p1", "--controller-id", "c1"]
                + list(CLI_ACTOR), root)
            self.assertEqual(r.returncode, 2)
            self.assertIn("units-file", r.stderr)

    def test_record_result_with_no_run_at_all_refuses_cleanly(self):
        with tempfile.TemporaryDirectory() as root:
            _cli_bootstrap(root)
            r = _run_cli(
                ["record-result", "--project", "p1", "--dispatch-id",
                 "ghost", "--controller-id", "c1"] + list(CLI_ACTOR), root)
            self.assertEqual(r.returncode, 1)
            self.assertIn("no controller run", r.stderr)
            self.assertNotIn("Traceback", r.stderr)


# ---------------------------------------------------------------------------
# CROSS-FAMILY REFUTATION (2026-08-05), FINDING 3 (HIGH, DATA DESTRUCTION):
# an INHERITED git environment redirects the engine's own rollback into an
# unrelated repository.
#
# These tests live in this section, and not with the hermetic engine tests
# above, because they need REAL git and REAL repositories: the whole claim
# is about what an external binary does with an environment, which no fake
# can answer. They spawn git and (in the end-to-end case)
# tools/bm_controller.py itself, always under
# tempfile.TemporaryDirectory(), never against this repository.
# ---------------------------------------------------------------------------

#: The environment variables the orchestrator's reproduction and this
#: writer's enumeration name as REDIRECTING (they move where git reads,
#: where it writes, or which config it obeys, which is the same thing once
#: core.worktree can be injected). Enumerated as the UNION of three
#: sources on this machine's git 2.50.1, never from memory: the
#: ENVIRONMENT VARIABLES section of git(1), the environment section of
#: git-config(1), and `strings` over the shipped git binary (205 distinct
#: GIT_ names in total). Thirteen of the names below appear in NONE of
#: git(1)'s own environment section, which is exactly why the shipped rule
#: is a PREFIX rule rather than a list: a list built from the primary
#: document would have shipped with those thirteen holes in it.
_GIT_REDIRECTION_ENV = (
    "GIT_DIR", "GIT_WORK_TREE", "GIT_INDEX_FILE", "GIT_OBJECT_DIRECTORY",
    "GIT_ALTERNATE_OBJECT_DIRECTORIES", "GIT_COMMON_DIR",
    "GIT_CEILING_DIRECTORIES", "GIT_NAMESPACE", "GIT_CONFIG",
    "GIT_CONFIG_GLOBAL", "GIT_CONFIG_SYSTEM", "GIT_CONFIG_COUNT",
    "GIT_CONFIG_KEY_0", "GIT_CONFIG_VALUE_0", "GIT_CONFIG_PARAMETERS",
    "GIT_CONFIG_NOSYSTEM", "GIT_ATTR_GLOBAL", "GIT_ATTR_SYSTEM",
    "GIT_ATTR_SOURCE", "GIT_GRAFT_FILE", "GIT_SHALLOW_FILE",
    "GIT_QUARANTINE_PATH", "GIT_IMPLICIT_WORK_TREE", "GIT_EXEC_PATH",
    "GIT_TEMPLATE_DIR", "GIT_REPLACE_REF_BASE", "GIT_PREFIX",
    "GIT_DISCOVERY_ACROSS_FILESYSTEM", "GIT_INDEX_VERSION",
)

_COMMITTED = "committed\n"
_FOUNDER_EDIT = "FOUNDER EDIT\n"
_WORKER_EDIT = "WORKER EDIT\n"


def _scrubbed_git_env():
    """The test harness's OWN hygiene, deliberately not the shipped
    helper: every git command this section runs for SETUP must be
    unaffected by whatever the machine running the suite happens to
    export, or a poisoned CI environment would silently build the
    fixtures somewhere else."""
    return {k: v for k, v in os.environ.items() if not k.startswith("GIT_")}


def _git(args, cwd):
    r = subprocess.run(["git"] + list(args), cwd=cwd, capture_output=True,
                       text=True, env=_scrubbed_git_env())
    if r.returncode != 0:
        raise AssertionError("git %s failed in %s: %s"
                             % (" ".join(args), cwd, r.stderr))
    return r


def _write(path, text):
    with io.open(path, "w", encoding="utf-8") as fh:
        fh.write(text)


def _read(path):
    with io.open(path, encoding="utf-8") as fh:
        return fh.read()


def _throwaway_repo(path):
    """A real git repository at `path` with ONE committed file, a.py, and
    a clean tree. Returns `path`."""
    if not os.path.isdir(path):
        os.makedirs(path)
    _git(["init", "-q", "."], path)
    _git(["config", "user.email", "tester@example.invalid"], path)
    _git(["config", "user.name", "tester"], path)
    _write(os.path.join(path, "a.py"), _COMMITTED)
    _git(["add", "a.py"], path)
    _git(["commit", "-qm", "init"], path)
    return path


class _PoisonedGitEnvironment(object):
    """`with _PoisonedGitEnvironment({...}):` exports those names for the
    duration and restores the exact prior state afterwards, including
    names that were not set at all. This is what a controller invoked from
    a git hook, a wrapper script or another repository's tooling really
    inherits."""

    def __init__(self, mapping):
        self.mapping = dict(mapping)
        self.saved = {}

    def __enter__(self):
        for key, value in self.mapping.items():
            self.saved[key] = os.environ.get(key)
            os.environ[key] = value
        return self

    def __exit__(self, *exc):
        for key, prior in self.saved.items():
            if prior is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = prior
        return False


def _env_dump_command(tmp, expression):
    """A done_check-shaped command string that prints ONE json expression
    about its own environment, runnable by the shell the runner really
    has, on POSIX and on Windows.

    NOT `shlex.quote(sys.executable) + " -c " + shlex.quote(payload)`,
    which is what stood here and which the 2026-08-05 Windows audit found
    broken twice over in one string. shlex implements POSIX shell quoting
    and nothing else: on Windows it wraps
    C:\\hostedtoolcache\\...\\python.exe in SINGLE quotes, so cmd.exe looks
    for a program whose name begins with a quote character, and it wraps
    the -c payload the same way, so even a correctly named interpreter
    would receive `'import` as its first token. Either alone makes the
    exit code non-zero, and the assertion on the next line is
    exit_code == 0.

    Two changes, no branch and no skip. The multi-token payload goes to a
    FILE in the same throwaway directory, which takes it out of the
    quoting question entirely, and the two paths that remain are quoted
    with the helper this project built for exactly this
    (tools/bm_store.py::_quote_path_for_local_shell, whose docstring
    carries the external ground truth for why shlex is the wrong tool
    here). What is executed is still one shell command string handed to
    the real SubprocessCheckRunner, which is the thing under test."""
    script = os.path.join(tmp, "env_dump.py")
    _write(script,
           "import json, os, sys\n"
           "sys.stdout.write(json.dumps(%s))\n" % (expression,))
    return "%s %s" % (bs._quote_path_for_local_shell(sys.executable),
                      bs._quote_path_for_local_shell(script))


def _subprocess_run_keywords():
    """The keyword names on the ONE subprocess call in
    tools/bm_controller.py, with the function that holds it. Source only:
    nothing is imported and nothing is executed."""
    with io.open(CONTROLLER_FILE, encoding="utf-8") as fh:
        tree = ast.parse(fh.read(), filename="bm_controller.py")
    found = []
    stack = []

    class _Visitor(ast.NodeVisitor):
        def visit_FunctionDef(self, node):
            stack.append(node.name)
            self.generic_visit(node)
            stack.pop()

        visit_AsyncFunctionDef = visit_FunctionDef

        def visit_Call(self, node):
            if _dotted_name(node.func) == "subprocess.run":
                found.append((stack[-1] if stack else "<module>",
                              sorted(kw.arg for kw in node.keywords)))
            self.generic_visit(node)

    _Visitor().visit(tree)
    return found


class TestCrossFamilyF3InheritedGitEnvironment(unittest.TestCase):
    """CROSS-FAMILY REFUTATION finding 3 (HIGH, DATA DESTRUCTION).

    tools/bm_controller.py ran every command with shell=True and an
    INHERITED environment, and git honours GIT_DIR and GIT_WORK_TREE over
    cwd. The orchestrator reproduced it first hand with two throwaway
    repositories: an uncommitted edit in Q, `GIT_DIR=../Q/.git
    GIT_WORK_TREE=../Q git restore -- a.py` run from inside P, and Q's
    edit was destroyed with EXIT 0, which the engine reads as a successful
    rollback and therefore queues no dirty-write-scope warning about.

    Two independent defences, either of which is enough, which is the same
    shape REFUTATION-5 S1's fix took:

      1. the ONE subprocess site strips the whole GIT_ environment class
         before it starts anything, so nothing the engine runs can be
         redirected by an inherited variable;
      2. the rollback NAMES its repository on the command line, and a
         command-line --git-dir/--work-tree beats the environment, so an
         unstripped variable still cannot move it.

    Both are proved below against real git, separately, so neither can
    quietly stop working behind the other."""

    def _pq(self, tmp):
        """Repositories P and Q, side by side. P carries a WORKER edit
        (what a rejected unit leaves behind) and Q carries a FOUNDER edit
        (the unrelated uncommitted work the refuter destroyed)."""
        p = _throwaway_repo(os.path.join(tmp, "P"))
        q = _throwaway_repo(os.path.join(tmp, "Q"))
        _write(os.path.join(p, "a.py"), _WORKER_EDIT)
        _write(os.path.join(q, "a.py"), _FOUNDER_EDIT)
        return p, q

    def _poison(self, q):
        return {"GIT_DIR": os.path.join(q, ".git"), "GIT_WORK_TREE": q}

    def test_the_orchestrators_P_and_Q_sequence_leaves_Q_untouched(self):
        """THE reproduction, at the shipped runner, with real git."""
        with tempfile.TemporaryDirectory() as tmp:
            p, q = self._pq(tmp)
            with _PoisonedGitEnvironment(self._poison(q)):
                outcome = bc.SubprocessCheckRunner().run(
                    "git restore -- a.py", cwd=p)
            self.assertEqual(
                _read(os.path.join(q, "a.py")), _FOUNDER_EDIT,
                "the rollback destroyed an uncommitted edit in an "
                "UNRELATED repository: git honoured the inherited GIT_DIR "
                "and GIT_WORK_TREE over cwd, and exit %r made the engine "
                "read that as a successful rollback"
                % (outcome["exit_code"],))
            self.assertEqual(
                _read(os.path.join(p, "a.py")), _COMMITTED,
                "the rollback must still do its real job in the project "
                "it was actually run for")

    def test_every_redirecting_variable_is_stripped_from_the_child(self):
        """Not one variable, the whole class: the child sees NO GIT_ name
        at all, whatever the parent exported."""
        with tempfile.TemporaryDirectory() as tmp:
            poison = {name: os.path.join(tmp, "poison")
                      for name in _GIT_REDIRECTION_ENV}
            dump = _env_dump_command(
                tmp,
                "sorted(k for k in os.environ if k.startswith('GIT_'))")
            with _PoisonedGitEnvironment(poison):
                outcome = bc.SubprocessCheckRunner().run(dump, cwd=tmp)
            self.assertEqual(outcome["exit_code"], 0, outcome["stderr"])
            self.assertEqual(
                json.loads(outcome["stdout"]), [],
                "a command this engine runs still inherits git variables "
                "that can redirect where git operates")

    def test_the_environment_a_legitimate_command_needs_survives(self):
        """The strip is a scalpel, not an empty environment: a done_check
        with no PATH and no HOME would fail for reasons that have nothing
        to do with safety, and a founder would read that as the
        controller being broken."""
        with tempfile.TemporaryDirectory() as tmp:
            dump = _env_dump_command(tmp, "sorted(os.environ)")
            outcome = bc.SubprocessCheckRunner().run(dump, cwd=tmp)
            self.assertEqual(outcome["exit_code"], 0, outcome["stderr"])
            names = json.loads(outcome["stdout"])
            for needed in ("PATH", "HOME"):
                if needed in os.environ:
                    self.assertIn(needed, names)

    def test_the_rollback_names_its_repository_when_there_is_one(self):
        """Defence 2, at the composition site: the command says WHICH
        repository it means rather than leaving that to discovery."""
        with tempfile.TemporaryDirectory() as tmp:
            p = _throwaway_repo(os.path.join(tmp, "P"))
            with bs.Store(p) as store:
                _seed(store)
                _sign(store)
                engine = _engine(store, FakeWorker({}), FakeCheckRunner())
                run = _begin_and_plan(engine, store, "p1", [
                    _unit("u1", write_scope=["a.py"])])
                command, refusal = engine._rollback_plan(run["run_id"], "u1")
            self.assertIsNone(refusal)
            root = os.path.realpath(p)
            # Asserted THROUGH bs._quote_path_for_local_shell, which is what
            # the composition site itself uses, so this reads the same on
            # every platform with no branch. Written out rather than left as
            # the bare path (2026-08-05 Windows audit, F1): the bare form is
            # right only while the runner's temporary directory happens to
            # contain no whitespace and no cmd.exe metacharacter, and a test
            # that is correct by accident of the runner image is the exact
            # shape of defect this audit was called to remove. On POSIX the
            # helper IS shlex.quote, so both lines are byte for byte what
            # they were.
            self.assertIn(
                "--git-dir=%s" % bs._quote_path_for_local_shell(
                    os.path.join(root, ".git")), command)
            self.assertIn(
                "--work-tree=%s" % bs._quote_path_for_local_shell(root),
                command)
            self.assertTrue(command.endswith("restore -- a.py"), command)

    def test_the_rollback_keeps_its_plain_shape_with_no_repository_to_name(
            self):
        """The fallback, pinned so it cannot drift silently: with no
        repository at the project root there is nothing to name, and the
        command stays byte for byte the one this engine has always
        composed (which is also what every other fixture in this file
        asserts). Discovery from cwd is then the only answer available,
        and defence 1 is what protects it."""
        with tempfile.TemporaryDirectory() as tmp:
            with bs.Store(tmp) as store:
                _seed(store)
                _sign(store)
                engine = _engine(store, FakeWorker({}), FakeCheckRunner())
                run = _begin_and_plan(engine, store, "p1", [
                    _unit("u1", write_scope=["a.py"])])
                command, refusal = engine._rollback_plan(run["run_id"], "u1")
            self.assertIsNone(refusal)
            self.assertEqual(command, "git restore -- a.py")

    def test_the_named_repository_beats_an_unstripped_variable(self):
        """Defence 2 ALONE, with defence 1 deliberately switched off: the
        composed command is handed to a raw subprocess carrying the full
        poisoned environment. If the two defences ever collapse into one,
        this is the test that notices."""
        with tempfile.TemporaryDirectory() as tmp:
            p, q = self._pq(tmp)
            with bs.Store(p) as store:
                _seed(store)
                _sign(store)
                engine = _engine(store, FakeWorker({}), FakeCheckRunner())
                run = _begin_and_plan(engine, store, "p1", [
                    _unit("u1", write_scope=["a.py"])])
                command, _refusal = engine._rollback_plan(run["run_id"], "u1")
            env = dict(os.environ)
            env.update(self._poison(q))
            subprocess.run(  # sbe: allow-silent test verifies the resulting file content below, not the subprocess exit code; the poisoned attempt may legitimately fail
                command, shell=True, cwd=p, capture_output=True,
                text=True, env=env)
            self.assertEqual(
                _read(os.path.join(q, "a.py")), _FOUNDER_EDIT,
                "the composed rollback command must name its own "
                "repository, so an environment variable that survived the "
                "strip still cannot move it")
            self.assertEqual(_read(os.path.join(p, "a.py")), _COMMITTED)

    def test_the_only_subprocess_site_passes_an_explicit_environment(self):
        """Structural, so the property cannot rot back: there are exactly
        two subprocess.run calls in the shipped module (the founder-command
        runner, and _default_fence_canary_runner added 2026-08-10), and
        BOTH pass env= rather than inheriting, for their own reasons: git-
        redirection stripping for the first, total isolation from the
        founder's real HOME and vault for the second."""
        found = _subprocess_run_keywords()
        self.assertEqual(len(found), 2, found)
        by_function = dict(found)
        self.assertEqual(set(by_function),
                         {"run", "_default_fence_canary_runner"})
        for where, keywords in found:
            self.assertIn(
                "env", keywords,
                "%s must pass an explicit env=; without it the child "
                "inherits the parent's environment" % (where,))

    def test_the_documented_redirection_names_are_all_covered(self):
        """The enumeration above is evidence, so it is checked against the
        shipped rule rather than left as prose."""
        for name in _GIT_REDIRECTION_ENV:
            self.assertNotIn(
                name, bc._sanitised_env({name: "x", "PATH": "/usr/bin"}),
                "%s can redirect where git operates and must be stripped"
                % name)


class TestCrossFamilyF3ShippedRecordResultCannotBeRedirected(
        unittest.TestCase):
    """The same finding, end to end, through the SHIPPED command the
    refuter named: `bm-controller record-result` invoked in an environment
    where GIT_DIR and GIT_WORK_TREE point at an unrelated repository Q.
    The unit's done_check fails, so this is the leg that rolls back."""

    def test_record_result_rolls_back_in_its_own_project_only(self):
        with tempfile.TemporaryDirectory() as tmp:
            p = _throwaway_repo(os.path.join(tmp, "P"))
            q = _throwaway_repo(os.path.join(tmp, "Q"))
            _cli_bootstrap(p)
            _cli_begin(p)

            units_path = os.path.join(p, "units.json")
            with io.open(units_path, "w", encoding="utf-8") as fh:
                json.dump([{
                    "unit_id": "u1", "objective": "edit a.py",
                    "dependencies": [], "write_scope": ["a.py"],
                    "role": "builder", "risk_class": "file-edit",
                    "lane": "default", "done_check": _DONE_CHECK_FAILS,
                    "done_check_expect_exit": 0}], fh)
            planned = _run_cli(
                ["plan", "--project", "p1", "--units-file", units_path,
                 "--controller-id", "c1"] + list(CLI_ACTOR), p)
            self.assertEqual(planned.returncode, 0,
                             planned.stdout + planned.stderr)

            dispatched = _run_cli(
                ["start", "--project", "p1", "--controller-id", "c1"]
                + list(CLI_ACTOR), p)
            self.assertEqual(dispatched.returncode, 0,
                             dispatched.stdout + dispatched.stderr)
            status = _run_cli(["status", "--project", "p1", "--json"], p)
            self.assertEqual(status.returncode, 0,
                             status.stdout + status.stderr)
            dispatch_id = json.loads(status.stdout)["open_dispatches"]["u1"]

            # The worker's edit in P, and the founder's unrelated
            # uncommitted edit in Q.
            _write(os.path.join(p, "a.py"), _WORKER_EDIT)
            _write(os.path.join(q, "a.py"), _FOUNDER_EDIT)

            recorded = _run_cli(
                ["record-result", "--project", "p1", "--dispatch-id",
                 dispatch_id, "--worker-claim", "edited a.py",
                 "--artifact", "a.py", "--controller-id", "c1"]
                + list(CLI_ACTOR), p,
                extra_env={"GIT_DIR": os.path.join(q, ".git"),
                           "GIT_WORK_TREE": q})
            self.assertNotIn("Traceback", recorded.stderr)
            self.assertEqual(
                _read(os.path.join(q, "a.py")), _FOUNDER_EDIT,
                "the shipped record-result destroyed an uncommitted edit "
                "in an unrelated repository: %s"
                % (recorded.stdout + recorded.stderr))
            self.assertEqual(
                _read(os.path.join(p, "a.py")), _COMMITTED,
                "the rollback must still run in the project it was "
                "invoked for: %s" % (recorded.stdout + recorded.stderr))


# ---------------------------------------------------------------------------
# CROSS-FAMILY refuter, findings 1 and 5, the halves that live in this file.
# Finding 5 has an anchor on each side (the stale selection here, the
# unconditional claim in tools/bm_store.py) and was fixed whole in one
# round; finding 1's store-side refusal is pinned in tools/test_bm_store.py
# and its reachability from the SHIPPED CLI is pinned here.
# ---------------------------------------------------------------------------


class _ReplanningStore(object):
    """A delegating Store wrapper that fires ONE concurrent re-plan the
    moment select_ready_units returns, and then hands the engine the
    selection it had ALREADY taken. That is the refuter's own sequence
    exactly: process A selects while the run is READY, process B plans
    without the unit and commits it SKIPPED, and process A resumes with a
    stale list, walks the run to EXECUTING and claims.

    Everything except select_ready_units is delegated untouched, so the
    engine under test is the real one against a real store, and the re-plan
    is a real upsert_units against the same file rather than a hand-written
    row edit."""

    def __init__(self, store, run_id, replacement_units):
        self._store = store
        self._run_id = run_id
        self._replacement = replacement_units
        self.fired = False
        self.stale_selection = None

    def __getattr__(self, name):
        return getattr(self._store, name)

    def select_ready_units(self, run_id):
        selected = self._store.select_ready_units(run_id)
        if not self.fired and selected:
            self.fired = True
            self.stale_selection = [u["unit_id"] for u in selected]
            self._store.upsert_units(self._run_id, self._replacement,
                                     _actor())
        return selected


def _active_fence_names(store):
    return sorted(r["name"] for r in store.dump(raw=True)["records"]
                  if r["state"] == "active")


class TestCrossFamilyF5StaleSelectionIsDeferred(unittest.TestCase):
    """FINDING 5 (MEDIUM), controller half. The engine selected units,
    walked the run to EXECUTING and then claimed from that stale list, so a
    re-plan that landed in between had its removal silently overwritten and
    the dropped unit was dispatched anyway.

    The store now refuses the claim by name; this is the half that says
    what the engine does with that refusal: defer to a later wave, exactly
    as a fence overlap is deferred. No drain, no retry burned, no fence
    left active."""

    def _run_the_race(self, replacement):
        # bc.bs.Store, NOT bs.Store, and the difference is load-bearing.
        # This file loads bm_store by path and tools/bm_controller.py loads
        # it by path again, so the two are DIFFERENT module objects with
        # DIFFERENT OwnershipRefused classes ("the class-identity trap
        # tools/bm_project.py documents", named in bm_controller.py's own
        # comment on that load). In production there is one load and
        # self.store is a Store from it, so an engine-level
        # `except bs.OwnershipRefused` matches; a store built from THIS
        # file's copy raises a class the engine cannot catch, and the
        # refusal escapes step() as an uncaught exception. Building the
        # store from the engine's own module is what reproduces production
        # rather than an artefact of how the tests import.
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        store = bc.bs.Store(tmp.name)
        self.addCleanup(store.close)
        _seed(store)
        _sign(store)
        worker = FakeWorker({"u1": _worker_result(), "u2": _worker_result()})
        engine = _engine(store, worker, FakeCheckRunner())
        run = _begin_and_plan(engine, store, "p1", [_unit("u1")])
        wrapper = _ReplanningStore(store, run["run_id"], replacement)
        engine.store = wrapper
        summary = engine.step("p1")
        return store, run, worker, summary, wrapper

    def test_a_unit_a_concurrent_replan_removed_is_never_dispatched(self):
        store, run, worker, summary, wrapper = self._run_the_race(
            [_unit("u2")])
        self.assertEqual(wrapper.stale_selection, ["u1"],
                         "the race did not set up: the engine never took a "
                         "selection before the re-plan")
        self.assertEqual(
            _unit_row(store, run["run_id"], "u1")["status"], "SKIPPED",
            "the claim overwrote a unit the re-plan had removed")
        self.assertEqual(_dispatch_count(store, "u1"), 0,
                         "a dispatch was opened for a removed unit")
        self.assertEqual(worker.call_log, [],
                         "a worker was handed a brief for a unit the "
                         "re-plan had removed: %r" % (worker.call_log,))
        self.assertEqual(summary["dispatched"], [])

    def test_the_deferral_burns_no_retry_and_drains_nothing(self):
        store, run, _worker, summary, _wrapper = self._run_the_race(
            [_unit("u2")])
        self.assertEqual(
            _unit_row(store, run["run_id"], "u1")["retry_count"], 0,
            "the deferral burned a retry the unit never earned")
        self.assertEqual(
            summary["stop_reason"], "CONTENTION",
            "a stale selection is contention, the same word a fence overlap "
            "gets, never a drain: %r" % (summary,))
        self.assertNotIn(
            store.get_run("p1", raw=True)["state"],
            ("STOPPING", "STOPPED", "FAILED_TERMINAL"),
            "the run was drained for what is only contention")

    def test_the_fence_claimed_for_the_deferred_unit_is_not_left_active(self):
        """The claim of the FILES happens before the claim of the UNIT, so
        a refused unit claim leaves a fence this engine has already taken.
        Leaving it active would block every later wave over those paths
        with nothing naming it."""
        store, _run, _worker, _summary, _wrapper = self._run_the_race(
            [_unit("u2")])
        unit_fence = bc.ControllerEngine.UNIT_FENCE_PREFIX + "u1"
        self.assertNotIn(
            unit_fence, _active_fence_names(store),
            "the fence claimed for the deferred unit is still active: %r"
            % (_active_fence_names(store),))

    def test_the_next_wave_makes_progress_on_the_replanned_graph(self):
        """Deferral means "try again later", so the loop must not be stuck:
        the unit the re-plan ADDED is dispatched by the very next step.
        The next wave is the SAME driver continuing, so the fresh engine
        carries the session the run row records (L09 GAP 2: a run has one
        driver, and a genuinely new session would adopt first)."""
        store, _run, worker, _summary, _wrapper = self._run_the_race(
            [_unit("u2")])
        driver = store.get_run("p1", raw=True)["session_id"]
        second = _engine(store, worker, FakeCheckRunner(),
                         session_id=driver).step("p1")
        self.assertIn("u2", second["dispatched"],
                      "the next wave made no progress: %r" % (second,))
        self.assertEqual(_dispatch_count(store, "u1"), 0)


class TestCrossFamilyF1ShippedPlanRefusesABadNumber(unittest.TestCase):
    """FINDING 1 (HIGH), reachability. The refuter's route is the SHIPPED
    `bm-controller plan --units-json`, so the closure is asked of that
    command as a real subprocess, not only of the store method."""

    def test_plan_refuses_a_string_retry_ceiling_without_a_traceback(self):
        with tempfile.TemporaryDirectory() as root:
            _cli_bootstrap(root)
            _cli_begin(root)
            unit = dict(_UNIT_ONE)
            unit["retry_ceiling"] = "one"
            r = _run_cli(
                ["plan", "--project", "p1", "--units-json",
                 json.dumps([unit]), "--controller-id", "c1"]
                + list(CLI_ACTOR), root)
            self.assertNotIn("Traceback", r.stderr,
                             "the shipped plan command raised rather than "
                             "refused: %s" % (r.stdout + r.stderr))
            self.assertEqual(r.returncode, 1, r.stdout + r.stderr)
            self.assertIn("retry_ceiling", r.stderr)
            self.assertIn("str", r.stderr)

    def test_plan_still_accepts_a_well_typed_retry_ceiling(self):
        with tempfile.TemporaryDirectory() as root:
            _cli_bootstrap(root)
            _cli_begin(root)
            unit = dict(_UNIT_ONE)
            unit["retry_ceiling"] = 2
            r = _run_cli(
                ["plan", "--project", "p1", "--units-json",
                 json.dumps([unit]), "--controller-id", "c1"]
                + list(CLI_ACTOR), root)
            self.assertEqual(r.returncode, 0, r.stdout + r.stderr)


def _directory_write_denial_works(directory):
    """Whether `os.chmod(directory, 0o500)` really stops a file being
    created inside `directory` HERE, measured with a probe file and the
    directory left exactly as it was found (mode 0o700, no probe).

    2026-08-05 Windows audit, F7 (F6 is the same mechanism in
    tools/test_bm_store.py, which carries the same helper; the two files
    do not import each other). Python's own documentation for os.chmod:
    "Although Windows supports chmod(), you can only set the file's
    read-only flag with it (via the stat.S_IWRITE and stat.S_IREAD
    constants or a corresponding integer value). All other bits are
    ignored."  https://docs.python.org/3/library/os.html#os.chmod  A
    read-only ATTRIBUTE on a directory does not stop files being created
    in it, so the test below used to run its whole body against an
    ordinary writable directory on Windows and pass while proving nothing
    there. A test that silently proves nothing on a platform is worse than
    one that says so.

    Asked as a MEASUREMENT rather than as `os.name == "nt"` on purpose:
    the same vacuous pass happens on any POSIX machine running the suite
    as root, where mode 0o500 is ignored too, and it will stop happening
    by itself on any platform where the denial ever starts working."""
    probe = os.path.join(directory, "write_denial_probe.tmp")
    os.chmod(directory, 0o500)
    try:
        try:
            with io.open(probe, "w", encoding="utf-8") as fh:
                fh.write("probe")
        except EnvironmentError:
            return True
        try:
            os.remove(probe)
        except EnvironmentError:  # sbe: allow-silent best-effort probe cleanup, the write-succeeded verdict below is already decided
            pass
        return False
    finally:
        os.chmod(directory, 0o700)


class TestCrossFamilyF4ShippedStatusNeverWritesTheStoreDirectory(
        unittest.TestCase):
    """FINDING 4 (HIGH), reachability. `bm-controller status` is the
    shipped read-only command the finding names, driven here as a real
    subprocess against a store directory made non-writable with its WAL
    sidecars removed."""

    def _sidecars_removed(self, root):
        store_dir = os.path.join(root, bs.STORE_DIRNAME)
        for name in sorted(os.listdir(store_dir)):
            if name.startswith(bs.STORE_FILENAME) and name != bs.STORE_FILENAME:
                os.remove(os.path.join(store_dir, name))
        return store_dir

    def test_status_against_a_non_writable_store_directory_still_reports(self):
        with tempfile.TemporaryDirectory() as root:
            _cli_bootstrap(root)
            _cli_begin(root)
            store_dir = self._sidecars_removed(root)
            before = sorted(os.listdir(store_dir))
            if not _directory_write_denial_works(store_dir):
                self.skipTest(
                    "os.chmod(dir, 0o500) does not deny a write in this "
                    "directory here (measured, not assumed: a probe file "
                    "was created under that mode and removed again), so "
                    "the sharp half of FINDING 4 cannot be posed on this "
                    "platform. What is NOT covered where this skip fires, "
                    "stated plainly: nothing proves that the shipped "
                    "read-only `status` command still reports from a "
                    "store directory it cannot write, so a regression "
                    "that reintroduced a read-write connection would be "
                    "caught on POSIX only. What IS still covered here is "
                    "the sibling test below: `status` creates no sidecar "
                    "in a WRITABLE store directory, which is the same "
                    "regression seen from the other side. On Windows the "
                    "equivalent denial is a directory ACL (icacls), which "
                    "is not in the standard library.")
            os.chmod(store_dir, 0o500)
            try:
                r = _run_cli(["status", "--project", "p1"], root)
                self.assertNotIn("Traceback", r.stderr,
                                 r.stdout + r.stderr)
                self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
                self.assertIn("project p1: run ", r.stdout)
                self.assertEqual(
                    sorted(os.listdir(store_dir)), before,
                    "a read-only command wrote into the store directory")
            finally:
                os.chmod(store_dir, 0o700)

    def test_status_against_a_writable_store_directory_creates_no_sidecar(self):
        with tempfile.TemporaryDirectory() as root:
            _cli_bootstrap(root)
            _cli_begin(root)
            store_dir = self._sidecars_removed(root)
            before = sorted(os.listdir(store_dir))
            r = _run_cli(["status", "--project", "p1"], root)
            self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
            self.assertEqual(
                sorted(os.listdir(store_dir)), before,
                "a read-only command created %r in the store directory"
                % (sorted(set(os.listdir(store_dir)) - set(before)),))


# ---------------------------------------------------------------------------
# L09 GAP 2 (2026-08-06): driver adoption. step(), receive_result() and
# stop() performed no ownership check, so a second process calling step on
# a run it never started bypassed the fence guard that start enforces. The
# run's recorded session_id (written at open_run) is the driver identity;
# a mismatched caller is refused by name, and the ONE deliberate takeover
# path is an explicit adopt operation that records the handover, rhyming
# with the fence store's own cmd_adopt.
# ---------------------------------------------------------------------------

class TestDriverAdoption(unittest.TestCase):
    """L09 GAP 2. One run, one driver: the session that started (or last
    adopted) the run. A different session's step, receive_result and stop
    refuse 'not-driver', naming the owning session and the adopt path,
    and writing NOTHING. Adoption hands the run over durably, records the
    handover in the attribution stream, and flips who is refused."""

    def _begun(self, store, worker=None):
        _seed(store)
        _sign(store)
        engine1 = _engine(store, worker or FakeWorker({}),
                          FakeCheckRunner())
        run = _begin_and_plan(engine1, store, "p1",
                              [_unit("u1", write_scope=["a.py"])])
        return engine1, run

    def _foreign(self, store, worker=None):
        return _engine(store, worker or FakeWorker({}), FakeCheckRunner(),
                       controller_id="ctrl2")

    def test_a_second_session_cannot_step_a_run_it_never_started(self):
        with tempfile.TemporaryDirectory() as d:
            with bs.Store(d) as store:
                engine1, run = self._begun(store)
                engine2 = self._foreign(store)
                # bc.bs.OwnershipRefused, not bs.OwnershipRefused: the
                # not-driver refusal is ENGINE-raised, so it carries the
                # class from bm_controller's own bm_store load (the same
                # class-identity note the run-paused test states).
                with self.assertRaises(bc.bs.OwnershipRefused) as ctx:
                    engine2.step("p1")
                self.assertEqual(ctx.exception.reason, "not-driver")
                self.assertEqual(
                    ctx.exception.details.get("driver_session_id"),
                    engine1.session_id,
                    "the refusal must name the owning session")
                self.assertIn("adopt", str(ctx.exception),
                              "the refusal must name the one deliberate "
                              "takeover path")
                self.assertEqual(
                    store.get_run("p1", raw=True)["state"], "READY",
                    "a refused foreign step must move nothing")
                self.assertEqual(_dispatch_count(store, "u1"), 0)

    def test_a_second_session_cannot_record_a_result(self):
        with tempfile.TemporaryDirectory() as d:
            with bs.Store(d) as store:
                engine1, run = self._begun(
                    store, worker=FakeWorker(
                        {"u1": {"status": "pending"}}))
                engine1.step("p1")
                dispatches = store.list_dispatches("u1", raw=True)
                self.assertEqual(len(dispatches), 1)
                dispatch_id = dispatches[0]["dispatch_id"]
                engine2 = self._foreign(store)
                with self.assertRaises(bc.bs.OwnershipRefused) as ctx:
                    engine2.receive_result("p1", dispatch_id, "done",
                                           ["a.py"])
                self.assertEqual(ctx.exception.reason, "not-driver")
                row = store.list_dispatches("u1", raw=True)[0]
                self.assertEqual(row["status"], "DISPATCHED",
                                 "a refused foreign result must record "
                                 "nothing")
                self.assertIsNone(row["resulted_at"])

    def test_a_second_session_cannot_stop_the_run(self):
        with tempfile.TemporaryDirectory() as d:
            with bs.Store(d) as store:
                engine1, run = self._begun(store)
                engine2 = self._foreign(store)
                with self.assertRaises(bc.bs.OwnershipRefused) as ctx:
                    engine2.stop("p1")
                self.assertEqual(ctx.exception.reason, "not-driver")
                self.assertEqual(
                    store.get_run("p1", raw=True)["state"], "READY",
                    "a refused foreign stop must not drain the run")

    def test_adopt_hands_the_run_over_and_records_the_handover(self):
        with tempfile.TemporaryDirectory() as d:
            with bs.Store(d) as store:
                engine1, run = self._begun(store)
                engine2 = self._foreign(
                    store, worker=FakeWorker({"u1": _worker_result()}))
                out = engine2.adopt("p1", note="engine1's terminal died")
                self.assertTrue(out["adopted"])
                self.assertEqual(out["previous_session_id"],
                                 engine1.session_id)
                self.assertEqual(out["session_id"], engine2.session_id)
                self.assertEqual(
                    store.get_run("p1", raw=True)["session_id"],
                    engine2.session_id,
                    "adoption must be durable: a fresh engine reads the "
                    "new driver from the store")
                rows = store.conn.execute(
                    "SELECT reason FROM attribution "
                    "WHERE event_type='controller.run.adopted'").fetchall()
                self.assertEqual(len(rows), 1,
                                 "the handover must be recorded")
                self.assertIn(engine1.session_id, rows[0]["reason"],
                              "the record must name the displaced driver")
                # The handover flips who is refused: the new driver steps,
                # the old one is now the foreigner.
                engine2.step("p1")
                with self.assertRaises(bc.bs.OwnershipRefused) as ctx:
                    engine1.step("p1")
                self.assertEqual(ctx.exception.reason, "not-driver")
                self.assertEqual(
                    ctx.exception.details.get("driver_session_id"),
                    engine2.session_id)

    def test_the_same_session_resumes_without_adoption(self):
        """The crash-resume the engine's own docstring promises: a fresh
        ControllerEngine carrying the SAME session id is the same driver
        restarted, and resumes with no ceremony."""
        with tempfile.TemporaryDirectory() as d:
            with bs.Store(d) as store:
                engine1, run = self._begun(store)
                engine1b = _engine(store, FakeWorker(
                    {"u1": _worker_result()}), FakeCheckRunner(),
                    session_id=engine1.session_id)
                summary = engine1b.step("p1")
                self.assertIn("u1", summary["completed"])

    def test_a_run_with_no_recorded_driver_is_not_guarded(self):
        """The fence store's own ownership law, restated for runs: a
        non-empty owning session blocks a move; an empty one (a store
        written before this law) has no live driver to protect."""
        with tempfile.TemporaryDirectory() as d:
            with bs.Store(d) as store:
                engine1, run = self._begun(store)
                store.conn.execute(
                    "UPDATE controller_runs SET session_id=''")
                store.conn.commit()
                engine2 = self._foreign(
                    store, worker=FakeWorker({"u1": _worker_result()}))
                summary = engine2.step("p1")
                self.assertIn("u1", summary["completed"])

    def test_adopting_with_no_run_refuses(self):
        with tempfile.TemporaryDirectory() as d:
            with bs.Store(d) as store:
                _seed(store)
                _sign(store)
                engine = _engine(store, FakeWorker({}), FakeCheckRunner())
                with self.assertRaises(bs.OwnershipRefused) as ctx:
                    engine.adopt("p1")
                self.assertEqual(ctx.exception.reason, "no-run")

    def test_adopting_a_terminal_run_refuses(self):
        with tempfile.TemporaryDirectory() as d:
            with bs.Store(d) as store:
                engine1, run = self._begun(store)
                engine1.stop("p1", reason="done here")
                engine2 = self._foreign(store)
                with self.assertRaises(bs.OwnershipRefused) as ctx:
                    engine2.adopt("p1")
                self.assertEqual(ctx.exception.reason, "run-terminal")

    def test_adoption_by_the_current_driver_is_the_idempotent_no_op(self):
        with tempfile.TemporaryDirectory() as d:
            with bs.Store(d) as store:
                engine1, run = self._begun(store)
                out = engine1.adopt("p1")
                self.assertFalse(out["adopted"])
                self.assertEqual(out["session_id"], engine1.session_id)
                rows = store.conn.execute(
                    "SELECT reason FROM attribution "
                    "WHERE event_type='controller.run.adopted'").fetchall()
                self.assertEqual(rows, [],
                                 "a no-op adoption records no handover")

    def test_a_foreign_step_on_a_terminal_run_stays_the_quiet_summary(self):
        """Ownership guards only a LIVE run (the fence store's BLOCKER 2
        law): a finished run has no driver to protect, and asking about
        it writes nothing, so any session may read the TERMINAL answer."""
        with tempfile.TemporaryDirectory() as d:
            with bs.Store(d) as store:
                engine1, run = self._begun(store)
                engine1.stop("p1", reason="done here")
                engine2 = self._foreign(store)
                summary = engine2.step("p1")
                self.assertEqual(summary["stop_reason"], "TERMINAL")


class TestRestoreCheckpoint(unittest.TestCase):
    """F3.2: restore_checkpoint reconstructs a run's position from one
    named checkpoint, read-only, from any engine instance -- proven here
    against the same instance; F3.3 below proves it across a killed and
    rebuilt one."""

    def test_unknown_checkpoint_id_raises_value_error(self):
        with tempfile.TemporaryDirectory() as d:
            with bs.Store(d) as store:
                _seed(store)
                _sign(store)
                engine = _engine(store, FakeWorker({}), FakeCheckRunner())
                with self.assertRaises(ValueError):
                    engine.restore_checkpoint("p1", "no-such-checkpoint")

    def test_restore_returns_units_matching_the_store_exactly(self):
        with tempfile.TemporaryDirectory() as d:
            with bs.Store(d) as store:
                _seed(store)
                _sign(store)
                engine = _engine(
                    store, FakeWorker({"u1": _worker_result()}),
                    FakeCheckRunner())
                run = _begin_and_plan(
                    engine, store, "p1",
                    [_unit("u1", write_scope=["a.py"])])
                cid = store.record_checkpoint(
                    "p1", "ctrl1", "phase", "boundary reached", "sess1",
                    _actor())
                result = engine.restore_checkpoint("p1", cid)
                self.assertEqual(result["checkpoint"]["checkpoint_id"], cid)
                fresh_run = store.get_run("p1", raw=True)
                self.assertEqual(result["run_state"], fresh_run["state"])
                self.assertEqual(
                    result["units"],
                    store.list_units(run["run_id"], raw=True))

    def test_restore_refuses_a_checkpoint_from_a_different_project(self):
        with tempfile.TemporaryDirectory() as d:
            with bs.Store(d) as store:
                _seed(store, pid="p1")
                _sign(store, project_id="p1")
                _seed(store, pid="p2")
                _sign(store, project_id="p2")
                cid = store.record_checkpoint(
                    "p2", "ctrl1", "phase", "note", "sess1", _actor())
                engine = _engine(store, FakeWorker({}), FakeCheckRunner())
                with self.assertRaises(ValueError):
                    engine.restore_checkpoint("p1", cid)

    def test_restore_checkpoint_survives_new_engine_instance(self):
        """F3's own done-check: a checkpoint taken in one session is
        restorable from a different session after the first is killed.
        No process kill needed to prove it -- Store's own docstring says
        it is one open connection to <root>/.brothermode/store.sqlite3,
        so closing store_a and opening a fresh Store against the same
        directory IS a different session reading the same durable file,
        exactly what a killed process and a resumed one look like from
        the store's side."""
        with tempfile.TemporaryDirectory() as d:
            store_a = bs.Store(d)
            _seed(store_a)
            _sign(store_a)
            engine_a = _engine(
                store_a, FakeWorker({"u1": _worker_result()}),
                FakeCheckRunner())
            run = _begin_and_plan(
                engine_a, store_a, "p1", [_unit("u1", write_scope=["a.py"])])
            engine_a.step("p1")  # FakeWorker runs synchronously: u1 -> DONE
            cid = store_a.record_checkpoint(
                "p1", "ctrl1", "phase", "before the kill", "sess1",
                _actor())
            units_before = store_a.list_units(run["run_id"], raw=True)
            store_a.close()
            del engine_a, store_a  # the first session is gone

            store_b = bs.Store(d)
            try:
                engine_b = _engine(store_b, FakeWorker({}),
                                   FakeCheckRunner())
                result = engine_b.restore_checkpoint("p1", cid)
                self.assertEqual(result["checkpoint"]["checkpoint_id"], cid)
                self.assertEqual(result["units"], units_before)
                self.assertTrue(
                    any(u["status"] == "DONE" for u in result["units"]),
                    "the restored units must show the real position "
                    "(DONE, after step ran) not just the planned one "
                    "(READY)")
            finally:
                store_b.close()


def _plan_content(**overrides):
    """Matching tools/bm_plan.py's REQUIRED_FIELDS exactly (see that
    file's own tools/test_bm_plan.py fixture, _plan()): the minimal
    complete content a plan needs before record_redirect has anything to
    bump."""
    content = {
        "options": ["do nothing", "ship the small fix"],
        "recommended": "ship the small fix",
        "will_run": ["apply the patch"],
        "will_not_run": ["the redesign"],
        "blast_radius": "one file, one function",
        "reversibility": "git revert, no data written",
        "evidence_promised": "test output pasted after the edit",
    }
    content.update(overrides)
    return content


class TestInjectRedirect(unittest.TestCase):
    """F1 (mid-stream steering): a redirect issued mid-run bumps this
    project's plan to a new version and drops a liveness checkpoint,
    without touching any unit row -- proven here by advancing a unit to
    DONE first and checking it survives the redirect byte for byte
    (tools/bm_plan.py's own PlanStore.record_redirect carries the version
    -bump half of this claim; TestRecordRedirect there proves it in
    isolation)."""

    def test_redirect_bumps_the_plan_and_leaves_a_done_unit_alone(self):
        with tempfile.TemporaryDirectory() as d:
            with bs.Store(d) as store:
                _seed(store)
                _sign(store)
                plan_store = bc.bm_plan.PlanStore(store.root)
                before = plan_store.record("p1", _plan_content())
                engine = _engine(
                    store, FakeWorker({"u1": _worker_result()}),
                    FakeCheckRunner())
                run = _begin_and_plan(
                    engine, store, "p1",
                    [_unit("u1", write_scope=["a.py"])])
                engine.step("p1")  # FakeWorker runs synchronously: u1->DONE
                done_row = _unit_row(store, run["run_id"], "u1")
                self.assertEqual(done_row["status"], "DONE")

                result = engine.inject_redirect(
                    "p1", "wrong abstraction, steer to the small fix",
                    _actor("founder"))

                self.assertNotEqual(result["new_plan_version"],
                                    before["version"])
                self.assertEqual(
                    plan_store.read("p1")["version"],
                    result["new_plan_version"])
                after_row = _unit_row(store, run["run_id"], "u1")
                self.assertEqual(
                    after_row, done_row,
                    "the completed unit must survive a redirect "
                    "byte for byte")
                checkpoint = store.get_checkpoint(result["checkpoint_id"],
                                                  raw=True)
                self.assertEqual(checkpoint["kind"], "redirect")
                self.assertEqual(checkpoint["project_id"], "p1")

    def test_redirect_with_no_run_at_all_refuses_no_run(self):
        with tempfile.TemporaryDirectory() as d:
            with bs.Store(d) as store:
                _seed(store)
                _sign(store)
                bc.bm_plan.PlanStore(store.root).record(
                    "p1", _plan_content())
                engine = _engine(store, FakeWorker({}), FakeCheckRunner())
                with self.assertRaises(bc.bs.OwnershipRefused) as ctx:
                    engine.inject_redirect("p1", "a note", _actor())
                self.assertEqual(ctx.exception.reason, "no-run")

    def test_redirect_on_a_project_with_no_plan_refuses_no_plan(self):
        with tempfile.TemporaryDirectory() as d:
            with bs.Store(d) as store:
                _seed(store)
                _sign(store)
                engine = _engine(
                    store, FakeWorker({"u1": _worker_result()}),
                    FakeCheckRunner())
                _begin_and_plan(
                    engine, store, "p1",
                    [_unit("u1", write_scope=["a.py"])])
                with self.assertRaises(bc.bs.OwnershipRefused) as ctx:
                    engine.inject_redirect("p1", "a note", _actor())
                self.assertEqual(ctx.exception.reason, "no-plan")

    def test_redirect_on_a_terminal_run_refuses_run_terminal(self):
        """Driven backwards, per the unit's own quality bar: a redirect
        that should NOT discard a run's terminal state must itself be
        refused, not silently accepted."""
        with tempfile.TemporaryDirectory() as d:
            with bs.Store(d) as store:
                _seed(store)
                _sign(store)
                bc.bm_plan.PlanStore(store.root).record(
                    "p1", _plan_content())
                engine = _engine(
                    store, FakeWorker({"u1": _worker_result()}),
                    FakeCheckRunner())
                _begin_and_plan(
                    engine, store, "p1",
                    [_unit("u1", write_scope=["a.py"])])
                engine.stop("p1", reason="test stop")
                run = store.get_run("p1", raw=True)
                self.assertEqual(run["state"], "STOPPED")
                with self.assertRaises(bc.bs.OwnershipRefused) as ctx:
                    engine.inject_redirect("p1", "a note", _actor())
                self.assertEqual(ctx.exception.reason, "run-terminal")

    def test_redirect_mid_run_preserves_completed_units(self):
        """F1.3's own end to end sequence: open a run with TWO units, one
        finished and one still pending, redirect, and assert BOTH survive
        untouched and the plan version moved. Distinct from the single
        -unit test above (which proves the completed-unit half in
        isolation): this one also proves a unit that never ran is not
        somehow swept up by the redirect either."""
        with tempfile.TemporaryDirectory() as d:
            with bs.Store(d) as store:
                _seed(store)
                _sign(store)
                plan_store = bc.bm_plan.PlanStore(store.root)
                plan_store.record("p1", _plan_content())
                before_version = plan_store.read("p1")["version"]
                engine = _engine(
                    store, FakeWorker({"u1": _worker_result(),
                                      "u2": _worker_result()}),
                    FakeCheckRunner())
                run = _begin_and_plan(
                    engine, store, "p1",
                    [_unit("u1", write_scope=["a.py"]),
                     _unit("u2", dependencies=["u1"], write_scope=["b.py"])])
                engine.step("p1")  # u1 dispatched and completed; u2 depends
                                   # on it, so this same wave leaves it
                                   # PENDING, not yet selectable
                units_before = {
                    u["unit_id"]: u
                    for u in store.list_units(run["run_id"], raw=True)}
                self.assertEqual(units_before["u1"]["status"], "DONE")

                engine.inject_redirect(
                    "p1", "course correction", _actor("founder"))

                units_after = {
                    u["unit_id"]: u
                    for u in store.list_units(run["run_id"], raw=True)}
                self.assertEqual(units_after["u1"], units_before["u1"])
                self.assertEqual(units_after["u1"]["status"], "DONE")
                self.assertEqual(units_after["u2"], units_before["u2"])
                self.assertNotEqual(plan_store.read("p1")["version"],
                                    before_version)


class TestRefuteRound2PlanAndTerminalResult(unittest.TestCase):
    """L09 refute round 2. B2: `plan` was the one powerful mutation left
    without the driver guard, so a foreign session injected the unit graph
    the honest driver then dispatched. B3: `receive_result` guarded only
    the non-terminal path, so a foreign session recorded a late result on
    a STOPPED run. Both now apply _refuse_foreign_driver; the run's OWN
    driver (a crash-resume replan, or a late result for work that was in
    flight when stop landed) passes the same-session check unchanged."""

    def _seed_sign(self, store):
        _seed(store)
        _sign(store)

    def _foreign(self, store, worker=None):
        return _engine(store, worker or FakeWorker({}), FakeCheckRunner(),
                       controller_id="ctrl2")

    def test_a_second_session_cannot_plan_a_run_it_never_began(self):
        with tempfile.TemporaryDirectory() as d:
            with bs.Store(d) as store:
                self._seed_sign(store)
                engine1 = _engine(store, FakeWorker({}), FakeCheckRunner())
                run = engine1.begin("p1", "ship it", "echo done")
                engine2 = self._foreign(store)
                with self.assertRaises(bc.bs.OwnershipRefused) as ctx:
                    engine2.plan("p1", run["run_id"],
                                 [_unit("evil", write_scope=["src/pwned.py"])])
                self.assertEqual(ctx.exception.reason, "not-driver")
                self.assertEqual(
                    ctx.exception.details.get("driver_session_id"),
                    engine1.session_id)
                self.assertEqual(
                    store.list_units(run["run_id"], raw=True), [],
                    "a refused foreign plan must inject NO unit graph")

    def test_the_same_driver_can_still_plan(self):
        """Calibration: a crash-resume replan is the SAME driver, which
        passes the guard (or a genuinely new session adopts first)."""
        with tempfile.TemporaryDirectory() as d:
            with bs.Store(d) as store:
                self._seed_sign(store)
                engine1 = _engine(store, FakeWorker({}), FakeCheckRunner())
                run = engine1.begin("p1", "ship it", "echo done")
                result = engine1.plan("p1", run["run_id"],
                                      [_unit("u1", write_scope=["a.py"])])
                self.assertEqual(result["count"], 1)
                engine1b = _engine(store, FakeWorker({}), FakeCheckRunner(),
                                   session_id=engine1.session_id)
                engine1b.plan("p1", run["run_id"],
                              [_unit("u1", write_scope=["a.py"]),
                               _unit("u2", write_scope=["b.py"])])
                self.assertEqual(len(store.list_units(run["run_id"])), 2)

    def _dispatched_then_stopped(self, store):
        engine1 = _engine(
            store, FakeWorker({"u1": {"status": "pending"}}),
            FakeCheckRunner())
        run = _begin_and_plan(engine1, store, "p1",
                              [_unit("u1", write_scope=["a.py"])])
        engine1.step("p1")
        dispatch_id = store.list_dispatches("u1", raw=True)[0]["dispatch_id"]
        engine1.stop("p1", reason="founder stopped mid-flight")
        self.assertEqual(store.get_run("p1", raw=True)["state"], "STOPPED")
        return engine1, run, dispatch_id

    def test_a_foreign_session_cannot_record_a_late_result_on_a_terminal_run(self):
        with tempfile.TemporaryDirectory() as d:
            with bs.Store(d) as store:
                self._seed_sign(store)
                _engine1, _run, dispatch_id = self._dispatched_then_stopped(
                    store)
                engine2 = self._foreign(store)
                with self.assertRaises(bc.bs.OwnershipRefused) as ctx:
                    engine2.receive_result("p1", dispatch_id, "done",
                                           ["a.py"])
                self.assertEqual(ctx.exception.reason, "not-driver")
                row = store.list_dispatches("u1", raw=True)[0]
                self.assertIsNone(
                    row["resulted_at"],
                    "a refused foreign late result must record nothing")

    def test_the_own_driver_still_records_a_late_result_on_a_terminal_run(self):
        """Calibration: the run's OWN driver, whose work was in flight when
        stop landed, still records the late result (the legitimate
        after-stop case the guard must not break)."""
        with tempfile.TemporaryDirectory() as d:
            with bs.Store(d) as store:
                self._seed_sign(store)
                engine1, _run, dispatch_id = self._dispatched_then_stopped(
                    store)
                outcome = engine1.receive_result("p1", dispatch_id, "done",
                                                 ["a.py"])
                self.assertIsInstance(outcome, str)
                row = store.list_dispatches("u1", raw=True)[0]
                self.assertIsNotNone(
                    row["resulted_at"],
                    "the own driver's late result must be recorded")


class TestControllerCLIAdopt(unittest.TestCase):
    """L09 GAP 2 through the shipped CLI: a second session's step is
    refused by name, `adopt` is the recorded takeover, and the adopted
    session then drives. Session ids are passed explicitly, the
    documented convention for every multi-invocation CLI workflow
    (tools/bm_store.py, _default_cli_session_id's own docstring)."""

    def test_step_refuses_a_foreign_session_until_adopt(self):
        # Flags by hand, not CLI_ACTOR: CLI_ACTOR carries the shared
        # stable session, and this test is ABOUT two different sessions.
        actor_a = ("--actor-name", "tester", "--session-id", "sA")
        actor_b = ("--actor-name", "tester", "--session-id", "sB")
        with tempfile.TemporaryDirectory() as root:
            _cli_bootstrap(root)
            r = _run_cli(["start", "--project", "p1", "--outcome", "ship",
                          "--done-definition", _DONE_CHECK_PASSES,
                          "--controller-id", "c1"] + list(actor_a), root)
            self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
            units_path = os.path.join(root, "units.json")
            with io.open(units_path, "w", encoding="utf-8") as fh:
                json.dump([_UNIT_ONE], fh)
            r = _run_cli(["plan", "--project", "p1", "--units-file",
                          units_path, "--controller-id", "c1"]
                         + list(actor_a), root)
            self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
            r = _run_cli(["step", "--project", "p1", "--controller-id",
                          "c1"] + list(actor_b), root)
            self.assertEqual(r.returncode, 1,
                             "a foreign session's step must be refused: "
                             + r.stdout + r.stderr)
            self.assertIn("driver", r.stdout + r.stderr)
            self.assertIn("adopt", r.stdout + r.stderr)
            r = _run_cli(["adopt", "--project", "p1"] + list(actor_b),
                         root)
            self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
            r = _run_cli(["step", "--project", "p1", "--controller-id",
                          "c1"] + list(actor_b), root)
            self.assertEqual(r.returncode, 0, r.stdout + r.stderr)


# ---------------------------------------------------------------------------
# THE UNATTENDED PREFLIGHT (founder posture, 2026-08-06). An unattended
# Full-Auto run must REFUSE to start unless the machine is in a state where
# the damage it could do is bounded. Eight preconditions, eight named reason
# codes, one refusal test each, plus the test that proves interactive use is
# untouched.
#
# Hermetic, like every engine test above: the repository facts and the
# snapshot come through ONE injected seam (bc.UnattendedGitFacts, replaced
# here by FakeGitFacts), the environment is a plain dictionary, and the
# store is a throwaway under tempfile.TemporaryDirectory(). No git binary is
# run and no snapshot ref is written anywhere by this section, except in the
# one end-to-end CLI test at the bottom, which drives the real command line
# and therefore refuses before reaching the snapshot at all.
#
# THE EIGHTH PRECONDITION, the fence canary, is a different kind of check:
# it really spawns tools/bm_fence_hook.py. Left at its real default here,
# every one of the many _preflight() calls below would spawn a subprocess
# and touch disk, which is exactly the hermeticity this section states and
# relies on. So _preflight() defaults `fence_canary_runner` to
# _FENCE_CANARY_ALWAYS_DENIES, a fake that answers "proven" with no process
# spawned, and every test in THIS section that is not specifically about
# the canary stays exactly as fast and hermetic as it always was. The
# canary's own behaviour, fake and real both, is exercised on its own in
# TestUnattendedRefusesUntilTheFenceCanaryProvesADeny below.
# ---------------------------------------------------------------------------

#: The environment in which all eight preconditions are MET, so a test that
#: wants one refusal breaks exactly one thing and nothing else can be the
#: cause of the answer it reads.
_UNATTENDED_GOOD_ENV = {"BM_FENCE_MODE": "enforced", "BM_FENCE_STRICT": "1"}

_UNATTENDED_SESSION = "sess-night-run"


def _FENCE_CANARY_ALWAYS_DENIES(argv, cwd, env, stdin_text):
    """A fake fence-canary runner that answers exactly the shape a real
    deny would: no process spawned, no disk touched. This is _preflight()'s
    default so the seven OTHER preconditions' tests, most of them written
    before the canary existed, stay hermetic and unaffected by it."""
    return {"stdout": json.dumps({"hookSpecificOutput": {
        "hookEventName": "PreToolUse", "permissionDecision": "deny",
        "permissionDecisionReason": "arranged for a hermetic test"}}),
            "stderr": "", "returncode": 0}


class FakeGitFacts(object):
    """The bc.UnattendedGitFacts seam, answered from arranged values.

    Same shape and same discipline as FakeWorker and FakeCheckRunner above:
    it records what it was asked, so a test can assert that the snapshot was
    NOT taken on a run the preflight refused."""

    def __init__(self, toplevel="/arranged/repo", branch="lane-a-stability",
                 modified=(), recovery=(True, "clean")):
        self._toplevel = toplevel
        self._branch = branch
        self._modified = modified
        self._recovery = recovery
        self.recovery_calls = []

    def toplevel(self, start_dir):
        return self._toplevel

    def branch(self, toplevel):
        return self._branch

    def modified_paths(self, toplevel):
        if self._modified is None:
            return None
        return list(self._modified)

    def recovery_point(self, toplevel, session_id):
        self.recovery_calls.append((toplevel, session_id))
        return self._recovery


def _preflight(store, root, session_id=_UNATTENDED_SESSION, env=None,
               git=None, acknowledged=(), project_id="p1",
               fence_canary_runner=_FENCE_CANARY_ALWAYS_DENIES):
    """One preflight call with every seam arranged to PASS unless the test
    overrode it. Returns the findings list.

    fence_canary_runner defaults to the fake that always proves a deny with
    no process spawned, so every existing precondition test here stays
    hermetic; pass fence_canary_runner=None to exercise the real spawn, or
    a different fake to test the canary's OWN refusal codes."""
    return bc.unattended_preflight(
        store, root, project_id, session_id,
        acknowledged=acknowledged,
        env=dict(_UNATTENDED_GOOD_ENV) if env is None else env,
        git=FakeGitFacts() if git is None else git,
        fence_canary_runner=fence_canary_runner)


def _codes(findings):
    return [code for code, _sentence in findings]


class TestUnattendedPreflightAllowsAReadyMachine(unittest.TestCase):
    """The CALIBRATION for the eight refusal tests below. Without it, a
    preflight that refused everything unconditionally would pass all eight
    and prove nothing at all."""

    def test_a_machine_that_meets_every_condition_is_not_refused(self):
        with tempfile.TemporaryDirectory() as d:
            with bs.Store(d) as store:
                _seed(store)
                _sign(store)
                git = FakeGitFacts()
                self.assertEqual(_preflight(store, d, git=git), [])
                self.assertEqual(
                    git.recovery_calls,
                    [("/arranged/repo", _UNATTENDED_SESSION)],
                    "the snapshot is taken exactly once, under the run's "
                    "own stable session id")


class TestUnattendedRefusesWithoutFenceEnforcement(unittest.TestCase):
    """PRECONDITION 1. The write fence must be ENFORCED for the run, not
    advisory. Advisory means a write the protection cannot check is allowed
    through with a warning, which is worth something to a founder reading
    the terminal and nothing at all at 3am.

    The controller REFUSES rather than setting the policy itself, and the
    refusal must carry the exact remediation command, because
    tools/bm_fence_hook.py is a separate process spawned by the runtime and
    nothing this process exports can reach it (bc.unattended_preflight's own
    docstring states the choice)."""

    def test_advisory_mode_refuses_and_names_the_exact_command(self):
        with tempfile.TemporaryDirectory() as d:
            with bs.Store(d) as store:
                _seed(store)
                _sign(store)
                git = FakeGitFacts()
                findings = _preflight(
                    store, d, git=git,
                    env={"BM_FENCE_MODE": "advisory", "BM_FENCE_STRICT": "1"})
                self.assertEqual(_codes(findings),
                                 [bc.UNATTENDED_FENCE_ADVISORY])
                self.assertIn("export BM_FENCE_MODE=enforced",
                              findings[0][1],
                              "the refusal must carry the command that "
                              "really changes what the hook reads")
                self.assertEqual(git.recovery_calls, [],
                                 "a refused preflight writes nothing")

    def test_an_unset_or_misspelled_mode_is_advisory_too(self):
        """tools/bm_fence_hook.fence_mode falls back to ADVISORY for
        anything it does not recognise, so this preflight must read those
        the same way rather than treating 'enfoced' as close enough."""
        for value in ("", "enfoced", "1", "ENFORCE"):
            with tempfile.TemporaryDirectory() as d:
                with bs.Store(d) as store:
                    _seed(store)
                    _sign(store)
                    findings = _preflight(
                        store, d,
                        env={"BM_FENCE_MODE": value, "BM_FENCE_STRICT": "1"})
                    self.assertEqual(_codes(findings),
                                     [bc.UNATTENDED_FENCE_ADVISORY],
                                     "BM_FENCE_MODE=%r must not count as "
                                     "enforced" % (value,))

    def test_the_hook_and_the_preflight_agree_on_what_enforced_means(self):
        """The two readings are pinned against each other, not against a
        hand-written list, the same way tools/test_bm_bash_audit.py pins its
        own second reading of this variable."""
        fh = _load("bm_fence_hook")
        for value in ("enforced", " ENFORCED ", "advisory", "", "1",
                      "enfoced"):
            env = {"BM_FENCE_MODE": value}
            self.assertEqual(
                bc._unattended_fence_mode(env) is None,
                fh.enforced_mode(env),
                "the preflight and the fence hook disagree about "
                "BM_FENCE_MODE=%r" % (value,))


class TestUnattendedRefusesWithoutStrictMode(unittest.TestCase):
    """PRECONDITION 2. BM_FENCE_STRICT=1 must be in effect, so an edit to a
    file no active record has claimed is refused. That is the ordinary shape
    of an unwatched run's mistake, and it is exactly what advisory-plus-
    non-strict lets through."""

    def test_an_unset_strict_flag_refuses_and_names_the_exact_command(self):
        with tempfile.TemporaryDirectory() as d:
            with bs.Store(d) as store:
                _seed(store)
                _sign(store)
                git = FakeGitFacts()
                findings = _preflight(
                    store, d, git=git,
                    env={"BM_FENCE_MODE": "enforced"})
                self.assertEqual(_codes(findings),
                                 [bc.UNATTENDED_FENCE_NOT_STRICT])
                self.assertIn("export BM_FENCE_STRICT=1", findings[0][1])
                self.assertEqual(git.recovery_calls, [])

    def test_zero_is_not_strict_and_any_other_value_is(self):
        """tools/bm_fence_hook.py reads this variable as SET unless it is
        empty or '0' (its own `strict` line), and this reads it the same."""
        for value, refused in (("", True), ("0", True), ("1", False),
                               ("yes", False)):
            with tempfile.TemporaryDirectory() as d:
                with bs.Store(d) as store:
                    _seed(store)
                    _sign(store)
                    findings = _preflight(
                        store, d,
                        env={"BM_FENCE_MODE": "enforced",
                             "BM_FENCE_STRICT": value})
                    self.assertEqual(
                        bc.UNATTENDED_FENCE_NOT_STRICT in _codes(findings),
                        refused,
                        "BM_FENCE_STRICT=%r was read wrongly" % (value,))


# ---------------------------------------------------------------------------
# PRECONDITION 8: the fence canary. Added 2026-08-10, closing an
# independent Codex audit's CRITICAL finding: _unattended_fence_mode and
# _unattended_fence_strict above prove only that two environment variables
# are set to the values tools/bm_fence_hook.py reads; neither proves the
# runtime ever SPAWNS that hook. Under Codex exec, confirmed, the hook
# never runs at all and both other fence preconditions still pass.
#
# THE TEST STYLE HERE IS DELIBERATELY DIFFERENT from the sections above.
# Every other precondition is exercised through _preflight()'s in-process
# helper. This one is exercised directly against bc._unattended_fence_canary,
# because its own `runner` seam IS the thing under test (a-d below), and
# through _preflight() only for the two tests that need to prove it is
# actually WIRED into the preflight's tuple (test_the_precondition_list_
# actually_calls_the_canary) and that it runs a REAL subprocess end to end
# (test_a_real_subprocess_against_a_real_throwaway_store_really_denies,
# the one test in this whole file that would have caught the six-day
# cmd_hook defect: nine green tests once called decide() directly while the
# live bug sat in the stdin-reading entry point in front of it).
# ---------------------------------------------------------------------------

def _canary_deny_runner(argv, cwd, env, stdin_text):
    return {"stdout": json.dumps({"hookSpecificOutput": {
        "hookEventName": "PreToolUse", "permissionDecision": "deny",
        "permissionDecisionReason": "arranged"}}),
            "stderr": "", "returncode": 0}


def _canary_allow_runner(argv, cwd, env, stdin_text):
    """No decision at all: an empty stdout is exactly what
    tools/bm_fence_hook.py's cmd_hook prints on a real ALLOW (nothing is
    written to stdout when decide() returns None), so this is the honest
    shape of 'the hook ran and did not deny', not a synthetic one."""
    return {"stdout": "", "stderr": "", "returncode": 0}


class TestUnattendedRefusesUntilTheFenceCanaryProvesADeny(unittest.TestCase):
    """PRECONDITION 8. Preconditions 1 and 2 only READ BM_FENCE_MODE and
    BM_FENCE_STRICT; this one RUNS tools/bm_fence_hook.py, once, against a
    throwaway claim owned by a session other than the one invoking it, and
    requires an actual deny.

    THE HONESTY BOUNDARY IS THE POINT OF THIS CLASS, not a side property.
    A pass here proves the hook BINARY refuses a foreign write when it is
    actually run. It never proves, and must never be described as proving,
    that the RUNTIME about to go unattended will call that hook on any
    real write; that is exactly the gap an independent Codex audit found
    one layer up, and this canary has no way to see the answer to it.
    TestTheFenceCanaryNeverOverclaimsRuntimeEnforcement below pins that
    limit as a mechanical check on the actual docstrings and messages,
    not only as prose in this one."""

    # -- a: a fake deny proves the condition ------------------------------

    def test_a_fake_deny_proves_the_condition(self):
        self.assertIsNone(
            bc._unattended_fence_canary({}, runner=_canary_deny_runner))

    # -- b: a fake allow is a real, demonstrated defect -------------------

    def test_a_fake_allow_is_a_real_defect_not_a_missing_proof(self):
        finding = bc._unattended_fence_canary({}, runner=_canary_allow_runner)
        self.assertIsNotNone(finding)
        code, sentence = finding
        self.assertEqual(code, bc.UNATTENDED_FENCE_NOT_PROVEN)
        self.assertIn("did NOT refuse", sentence)
        self.assertIn("real, demonstrated defect", sentence,
                      "outcome 2 must read as a defect, never as a missing "
                      "proof (that is outcome 3, tested below)")

    # -- c: a raising runner is caught, not propagated, and names itself --

    def test_a_raising_runner_never_propagates_and_names_the_exception(self):
        def boom(argv, cwd, env, stdin_text):
            raise RuntimeError("no python3 on this machine")
        finding = bc._unattended_fence_canary({}, runner=boom)
        self.assertIsNotNone(finding)
        code, sentence = finding
        self.assertEqual(code, bc.UNATTENDED_FENCE_NOT_PROVEN)
        self.assertIn("could not be DEMONSTRATED", sentence)
        self.assertIn("RuntimeError", sentence)
        self.assertIn("no python3 on this machine", sentence)

    def test_a_raising_runner_really_does_not_propagate(self):
        """The property test_a_raising_runner_never_propagates_and_names_
        the_exception exercises indirectly: this call must simply RETURN,
        never raise, whatever the runner does."""
        def boom(argv, cwd, env, stdin_text):
            raise ValueError("arranged failure")
        try:
            result = bc._unattended_fence_canary({}, runner=boom)
        except Exception as exc:
            self.fail("_unattended_fence_canary propagated %r; it runs in "
                      "front of every unattended start and must never "
                      "raise" % (exc,))
        self.assertEqual(result[0], bc.UNATTENDED_FENCE_NOT_PROVEN)

    # -- d: unparseable output is caught the same way, not a crash --------

    def test_unparseable_stdout_is_not_a_crash(self):
        def garbage(argv, cwd, env, stdin_text):
            return {"stdout": "not json at all", "stderr": "", "returncode": 0}
        finding = bc._unattended_fence_canary({}, runner=garbage)
        self.assertIsNotNone(finding)
        code, sentence = finding
        self.assertEqual(code, bc.UNATTENDED_FENCE_NOT_PROVEN)
        self.assertIn("could not be DEMONSTRATED", sentence,
                      "unparseable output is the 'not demonstrated' shape, "
                      "the same as an unspawnable process, never the "
                      "'real defect' shape a-fake-allow gets")

    def test_a_deny_shaped_but_non_dict_hookoutput_is_not_a_crash(self):
        """A stray shape (a list, a string) inside hookSpecificOutput must
        not raise AttributeError out of a .get() call."""
        def odd(argv, cwd, env, stdin_text):
            return {"stdout": json.dumps(
                {"hookSpecificOutput": ["not", "a", "dict"]}),
                    "stderr": "", "returncode": 0}
        finding = bc._unattended_fence_canary({}, runner=odd)
        self.assertIsNotNone(finding)
        self.assertEqual(finding[0], bc.UNATTENDED_FENCE_NOT_PROVEN)

    # -- f: a hook that never ran is never called a demonstrated defect ---
    # Found by the 2026-08-10 night refute pass: with the hook file absent,
    # the spawn succeeds (python exits 2, empty stdout), no exception fires,
    # and the empty stdout fell into the outcome-2 sentence claiming the
    # hook "was actually run" and is a "real, demonstrated defect". Not
    # knowing whether a fence works and knowing it is broken must never be
    # said the same way; that property is the whole point of outcome 3.

    def test_an_absent_hook_binary_is_not_a_demonstrated_defect(self):
        original = bc._FENCE_HOOK_FILE
        bc._FENCE_HOOK_FILE = os.path.join(
            tempfile.gettempdir(), "bm-no-such-fence-hook.py")
        try:
            finding = bc._unattended_fence_canary(
                dict(os.environ), runner=None)
        finally:
            bc._FENCE_HOOK_FILE = original
        self.assertIsNotNone(finding)
        code, sentence = finding
        self.assertEqual(code, bc.UNATTENDED_FENCE_NOT_PROVEN)
        self.assertIn("could not be DEMONSTRATED", sentence)
        self.assertNotIn("demonstrated defect", sentence,
                         "a hook that never ran must not be reported as a "
                         "defect found by running it")
        self.assertIn("exit", sentence.lower(),
                      "the finding must carry the exit status so the "
                      "founder can see WHY nothing was demonstrated")

    def test_a_deny_without_the_pretooluse_event_name_is_not_proven(self):
        """The runtime honors a decision only in the full PreToolUse shape;
        a deny under a different or missing hookEventName is not a shape
        the runtime would obey, so it must not certify PROVEN (2026-08-11
        cross-family audit, controller finding 2)."""
        def wrong_event(argv, cwd, env, stdin_text):
            return {"stdout": json.dumps({"hookSpecificOutput": {
                "hookEventName": "PostToolUse",
                "permissionDecision": "deny"}}),
                    "stderr": "", "returncode": 0}
        finding = bc._unattended_fence_canary({}, runner=wrong_event)
        self.assertIsNotNone(finding)
        self.assertEqual(finding[0], bc.UNATTENDED_FENCE_NOT_PROVEN)

    def test_a_deny_with_a_nonzero_returncode_does_not_certify_proven(self):
        """A deny printed by a hook that then crashed is not honored by the
        runtime under the PreToolUse contract, so it must not certify
        PROVEN here either."""
        def crashed_deny(argv, cwd, env, stdin_text):
            return {"stdout": json.dumps({"hookSpecificOutput": {
                "hookEventName": "PreToolUse",
                "permissionDecision": "deny",
                "permissionDecisionReason": "arranged"}}),
                    "stderr": "boom after printing", "returncode": 3}
        finding = bc._unattended_fence_canary({}, runner=crashed_deny)
        self.assertIsNotNone(finding)
        code, sentence = finding
        self.assertEqual(code, bc.UNATTENDED_FENCE_NOT_PROVEN)
        self.assertIn("could not be DEMONSTRATED", sentence)

    # -- e: the code has a plain-language rewrite --------------------------

    def test_the_code_has_a_plain_language_rewrite(self):
        self.assertIn(bc.UNATTENDED_FENCE_NOT_PROVEN,
                      bc.UNATTENDED_REFUSAL_HELP)
        context, hint, next_action = bc.UNATTENDED_REFUSAL_HELP[
            bc.UNATTENDED_FENCE_NOT_PROVEN]
        for part in (context, hint, next_action):
            self.assertTrue(part.strip())

    # -- f: the precondition list actually calls the canary -----------------

    def test_the_precondition_list_actually_calls_the_canary(self):
        def always_fails(argv, cwd, env, stdin_text):
            raise AssertionError("arranged: the canary must be reachable")
        with tempfile.TemporaryDirectory() as d:
            with bs.Store(d) as store:
                _seed(store)
                _sign(store)
                findings = _preflight(store, d,
                                      fence_canary_runner=always_fails)
                self.assertIn(bc.UNATTENDED_FENCE_NOT_PROVEN,
                             _codes(findings),
                             "the preflight's own tuple must include the "
                             "canary, or a broken fence can never be "
                             "reported: %r" % (findings,))

    def test_a_ready_machine_needs_the_canary_to_pass_too(self):
        """The calibration for (f): a machine with every OTHER condition met
        is still refused if the canary alone fails, proving the wiring
        matters and is not accidentally bypassed by the other seven already
        passing."""
        with tempfile.TemporaryDirectory() as d:
            with bs.Store(d) as store:
                _seed(store)
                _sign(store)
                findings = _preflight(
                    store, d, fence_canary_runner=_canary_allow_runner)
                self.assertEqual(_codes(findings),
                                 [bc.UNATTENDED_FENCE_NOT_PROVEN])

    # -- g: a REAL subprocess against a REAL throwaway store ---------------

    def test_a_real_subprocess_against_a_real_throwaway_store_really_denies(self):
        """THE test that would have caught the six-day cmd_hook defect: a
        REAL `python3 tools/bm_fence_hook.py hook` process, real JSON on
        real stdin, against a real throwaway store this call builds itself,
        never tools/bm_fence_hook.decide() called in-process. runner=None
        is the real spawn; nothing here is faked."""
        result = bc._unattended_fence_canary(dict(os.environ), runner=None)
        self.assertIsNone(
            result,
            "the shipped tools/bm_fence_hook.py did not deny a real write "
            "to a path a different session's active claim covers: %r"
            % (result,))

    def test_the_real_spawn_is_the_documented_default(self):
        """Calibration for (g): runner=None really does mean "spawn", not
        merely "returns the same thing a fake would"."""
        self.assertIsNone(bc._unattended_fence_canary.__defaults__[0],
                          "_unattended_fence_canary's runner parameter must "
                          "default to None, the documented real-spawn "
                          "signal")


class TestTheFenceCanaryNeverOverclaimsRuntimeEnforcement(unittest.TestCase):
    """The property this whole precondition exists to protect: a passing
    canary proves the HOOK refuses when it is run, never that the RUNTIME
    about to go unattended will call it on a real write. An overclaim here
    would be the exact defect this canary was built to close, committed
    inside its own fix. Checked mechanically against the real docstring and
    the real messages, not trusted as prose."""

    _FORBIDDEN = ("fence proven live", "fence is proven live",
                  "enforcement is proven", "proven end to end",
                  "proven end-to-end", "runtime is enforced",
                  "enforcement is on")

    def test_the_docstring_never_uses_a_forbidden_phrase(self):
        doc = (bc._unattended_fence_canary.__doc__ or "").lower()
        for phrase in self._FORBIDDEN:
            self.assertNotIn(phrase, doc, phrase)

    def test_the_docstring_states_the_limit_in_terms_of_the_runtime(self):
        doc = (bc._unattended_fence_canary.__doc__ or "").lower()
        self.assertIn("cannot prove", doc)
        self.assertIn("runtime", doc)

    def test_the_defect_finding_names_no_forbidden_phrase(self):
        _code, sentence = bc._unattended_fence_canary(
            {}, runner=_canary_allow_runner)
        for phrase in self._FORBIDDEN:
            self.assertNotIn(phrase, sentence.lower(), phrase)

    def test_the_not_demonstrated_finding_names_no_forbidden_phrase(self):
        def boom(argv, cwd, env, stdin_text):
            raise RuntimeError("x")
        _code, sentence = bc._unattended_fence_canary({}, runner=boom)
        for phrase in self._FORBIDDEN:
            self.assertNotIn(phrase, sentence.lower(), phrase)

    def test_the_founder_facing_help_names_the_runtime_limit_too(self):
        _context, _hint, next_action = bc.UNATTENDED_REFUSAL_HELP[
            bc.UNATTENDED_FENCE_NOT_PROVEN]
        self.assertIn("runtime", next_action.lower())
        self.assertIn("cannot prove", next_action.lower())
        for phrase in self._FORBIDDEN:
            self.assertNotIn(phrase, next_action.lower(), phrase)


class TestUnattendedRefusesOutsideARepository(unittest.TestCase):
    """PRECONDITION 3. A repository must be detected and the current branch
    identified. Both halves matter for the same reason: they are what makes
    the run undoable. No repository means no history to restore from, and a
    detached HEAD means the run's own commits are reachable from no branch,
    so a night of work would be unfindable in the morning."""

    def test_a_folder_with_no_repository_refuses(self):
        with tempfile.TemporaryDirectory() as d:
            with bs.Store(d) as store:
                _seed(store)
                _sign(store)
                git = FakeGitFacts(toplevel=None)
                findings = _preflight(store, d, git=git)
                self.assertEqual(_codes(findings),
                                 [bc.UNATTENDED_NO_REPOSITORY])
                self.assertEqual(git.recovery_calls, [],
                                 "with no repository there is nowhere to "
                                 "snapshot, and nothing is attempted")

    def test_a_detached_head_refuses_because_no_branch_is_identified(self):
        with tempfile.TemporaryDirectory() as d:
            with bs.Store(d) as store:
                _seed(store)
                _sign(store)
                findings = _preflight(store, d,
                                      git=FakeGitFacts(branch=None))
                self.assertEqual(_codes(findings),
                                 [bc.UNATTENDED_NO_REPOSITORY])
                self.assertIn("detached", findings[0][1])

    def test_the_real_reader_calls_a_detached_head_no_branch(self):
        """The seam's own implementation, not the fake: `rev-parse
        --abbrev-ref HEAD` answers the literal string 'HEAD' on a detached
        head, and that must be a None here rather than a branch called
        HEAD."""
        class _Result(object):
            def __init__(self, code, out):
                self.returncode = code
                self.stdout = out
                self.stderr = ""

        class _Autosave(object):
            def __init__(self, code, out):
                self._result = _Result(code, out)

            def _run_git(self, toplevel, *args):
                return self._result

        for code, out, expected in ((0, "HEAD\n", None), (0, "main\n", "main"),
                                    (0, "\n", None), (128, "", None)):
            facts = bc.UnattendedGitFacts(autosave=_Autosave(code, out))
            self.assertEqual(facts.branch("/repo"), expected,
                             "rev-parse rc=%s stdout=%r" % (code, out))


class TestUnattendedRefusesADirtyTree(unittest.TestCase):
    """PRECONDITION 4. The working tree is clean, OR every existing
    modification is explicitly acknowledged by path with
    --acknowledge-modified.

    Per path, never blanket: a single "I know it is dirty" flag becomes a
    habit, and a habit acknowledges the file the founder forgot about just
    as readily as the one they meant."""

    def test_unacknowledged_changes_refuse_and_are_all_named(self):
        with tempfile.TemporaryDirectory() as d:
            with bs.Store(d) as store:
                _seed(store)
                _sign(store)
                git = FakeGitFacts(modified=["a.py", "docs/b.md"])
                findings = _preflight(store, d, git=git)
                self.assertEqual(_codes(findings),
                                 [bc.UNATTENDED_DIRTY_TREE])
                self.assertIn("a.py", findings[0][1])
                self.assertIn("docs/b.md", findings[0][1],
                              "every unacknowledged path is printed, not "
                              "only the first, so the acknowledgement can "
                              "be written from the refusal itself")
                self.assertEqual(git.recovery_calls, [])

    def test_acknowledging_every_path_lets_the_run_start(self):
        with tempfile.TemporaryDirectory() as d:
            with bs.Store(d) as store:
                _seed(store)
                _sign(store)
                findings = _preflight(
                    store, d, git=FakeGitFacts(modified=["a.py",
                                                         "docs/b.md"]),
                    acknowledged=["a.py", "docs/b.md"])
                self.assertEqual(findings, [])

    def test_acknowledging_only_some_of_them_still_refuses(self):
        """The half-acknowledgement is the case a blanket flag would have
        waved through, so it is the one that has to fail."""
        with tempfile.TemporaryDirectory() as d:
            with bs.Store(d) as store:
                _seed(store)
                _sign(store)
                findings = _preflight(
                    store, d, git=FakeGitFacts(modified=["a.py",
                                                         "docs/b.md"]),
                    acknowledged=["a.py"])
                self.assertEqual(_codes(findings),
                                 [bc.UNATTENDED_DIRTY_TREE])
                self.assertIn("docs/b.md", findings[0][1])
                self.assertNotIn("--acknowledge-modified a.py",
                                 findings[0][1],
                                 "an already-acknowledged path must not be "
                                 "asked for again")

    def test_a_tree_git_cannot_report_on_is_not_called_clean(self):
        with tempfile.TemporaryDirectory() as d:
            with bs.Store(d) as store:
                _seed(store)
                _sign(store)
                findings = _preflight(store, d,
                                      git=FakeGitFacts(modified=None))
                self.assertEqual(_codes(findings),
                                 [bc.UNATTENDED_DIRTY_TREE])

    def test_the_real_reader_parses_porcelain_including_renames(self):
        """The seam's own implementation. Untracked files COUNT (the
        snapshot captures them and they are something a run can trip over),
        and a rename is acknowledged by its DESTINATION, which is the path
        that exists afterwards."""
        class _Result(object):
            returncode = 0
            stderr = ""
            stdout = (" M a.py\n"
                      "?? scratch.txt\n"
                      "R  old.py -> new.py\n"
                      "A  docs/added.md\n"
                      "UU conflicted.py\n")

        class _Autosave(object):
            def _run_git(self, toplevel, *args):
                return _Result()

        facts = bc.UnattendedGitFacts(autosave=_Autosave())
        self.assertEqual(facts.modified_paths("/repo"),
                         ["a.py", "scratch.txt", "new.py", "docs/added.md",
                          "conflicted.py"])


class TestUnattendedRefusesWithoutARecoverySnapshot(unittest.TestCase):
    """PRECONDITION 5. A recovery snapshot exists, or one is created before
    the first write. tools/bm_autosave.py already creates snapshots and is
    reused verbatim: one mechanism, so there is one story about what is
    recoverable."""

    def test_a_snapshot_that_cannot_be_taken_refuses(self):
        with tempfile.TemporaryDirectory() as d:
            with bs.Store(d) as store:
                _seed(store)
                _sign(store)
                findings = _preflight(
                    store, d,
                    git=FakeGitFacts(recovery=(False, "empty-tree")))
                self.assertEqual(_codes(findings),
                                 [bc.UNATTENDED_NO_SNAPSHOT])
                self.assertIn("empty-tree", findings[0][1],
                              "the reason the snapshot failed is carried "
                              "through, not swallowed")

    def test_the_snapshot_is_the_last_condition_so_a_refusal_writes_nothing(self):
        """Ordering, as a property rather than as a comment: the one
        condition with an effect on disk must never run on a machine the
        preflight is already refusing."""
        with tempfile.TemporaryDirectory() as d:
            with bs.Store(d) as store:
                _seed(store)
                _sign(store)
                git = FakeGitFacts(recovery=(False, "empty-tree"))
                findings = _preflight(
                    store, d, git=git,
                    env={"BM_FENCE_MODE": "advisory"})
                self.assertEqual(
                    _codes(findings),
                    [bc.UNATTENDED_FENCE_ADVISORY,
                     bc.UNATTENDED_FENCE_NOT_STRICT],
                    "the snapshot must not even be attempted, so its own "
                    "failure cannot appear in the findings")
                self.assertEqual(git.recovery_calls, [])

    def test_an_existing_snapshot_satisfies_the_condition(self):
        """"exists OR is created": a worktree whose snapshot attempt did
        not publish but which still has an earlier one on record has a
        recovery point, and the real reader says so."""
        class _Autosave(object):
            def __init__(self, ok, existing):
                self._ok = ok
                self._existing = existing

            def snapshot(self, toplevel, session_id, reason):
                return {"ok": self._ok, "reason": "locked"}

            def worktree_id_for(self, toplevel):
                return "wt1"

            def list_snapshot_refs(self, toplevel, worktree_id):
                return self._existing

        ok, detail = bc.UnattendedGitFacts(
            autosave=_Autosave(False, [("refs/x/1", "abc")])
        ).recovery_point("/repo", "s1")
        self.assertTrue(ok)
        self.assertIn("refs/x/1", detail)

        ok, _detail = bc.UnattendedGitFacts(
            autosave=_Autosave(False, [])).recovery_point("/repo", "s1")
        self.assertFalse(ok, "no new snapshot and no old one is a refusal")

        ok, detail = bc.UnattendedGitFacts(
            autosave=_Autosave(True, [])).recovery_point("/repo", "s1")
        self.assertTrue(ok, "a fresh snapshot satisfies the condition")


class TestUnattendedRefusesAForeignClaim(unittest.TestCase):
    """PRECONDITION 6. No foreign active claim covers the paths the run will
    write. Those paths are the live contract's own allowed_paths, which is
    the only boundary that exists before a unit graph does.

    Two writers over one path is the failure this whole product exists to
    prevent, and an unattended run is the writer least able to notice it."""

    def test_another_sessions_active_claim_refuses(self):
        with tempfile.TemporaryDirectory() as d:
            with bs.Store(d) as store:
                _seed(store)
                _sign(store, allowed_paths=["src/app"])
                store.claim(name="somebody-else", lifetime="ephemeral",
                            files=["src/app/main.py"],
                            session_id="a-different-session")
                git = FakeGitFacts()
                findings = _preflight(store, d, git=git)
                self.assertEqual(_codes(findings),
                                 [bc.UNATTENDED_FOREIGN_CLAIM])
                self.assertIn("somebody-else", findings[0][1])
                self.assertIn("a-different-session", findings[0][1],
                              "the refusal names WHO is holding it, which "
                              "is the only thing the founder can act on")
                self.assertEqual(git.recovery_calls, [])

    def test_this_runs_own_claim_is_not_foreign(self):
        """A resumed run is holding its own fences from the previous
        invocation. Refusing it out of its own fence would make an
        unattended run startable exactly once."""
        with tempfile.TemporaryDirectory() as d:
            with bs.Store(d) as store:
                _seed(store)
                _sign(store, allowed_paths=["src/app"])
                store.claim(name="unit-u1", lifetime="ephemeral",
                            files=["src/app/main.py"],
                            session_id=_UNATTENDED_SESSION)
                self.assertEqual(_preflight(store, d), [])

    def test_our_own_claim_cannot_hide_a_foreign_one_behind_it(self):
        """The reason this walks every active record instead of calling
        Store._find_overlap: that helper answers with the FIRST collision
        and stops, and the first collision is routinely this run's own
        fence. Two claims, ours first, and the foreign one must still be
        found."""
        with tempfile.TemporaryDirectory() as d:
            with bs.Store(d) as store:
                _seed(store)
                _sign(store, allowed_paths=["src/app"])
                store.claim(name="unit-u1", lifetime="ephemeral",
                            files=["src/app/mine.py"],
                            session_id=_UNATTENDED_SESSION)
                store.claim(name="somebody-else", lifetime="ephemeral",
                            files=["src/app/theirs.py"],
                            session_id="a-different-session")
                findings = _preflight(store, d)
                self.assertEqual(_codes(findings),
                                 [bc.UNATTENDED_FOREIGN_CLAIM])
                self.assertIn("theirs.py", findings[0][1])

    def test_a_parked_record_holds_nothing(self):
        with tempfile.TemporaryDirectory() as d:
            with bs.Store(d) as store:
                _seed(store)
                _sign(store, allowed_paths=["src/app"])
                record = store.claim(
                    name="somebody-else", lifetime="ephemeral",
                    files=["src/app/main.py"],
                    session_id="a-different-session")
                store.transition(record.lifecycle_uuid, record.version,
                                 "parked",
                                 session_id="a-different-session",
                                 note="handed back")
                self.assertEqual(_preflight(store, d), [])

    def test_a_claim_outside_the_allowed_paths_is_not_a_collision(self):
        with tempfile.TemporaryDirectory() as d:
            with bs.Store(d) as store:
                _seed(store)
                _sign(store, allowed_paths=["src/app"])
                store.claim(name="somebody-else", lifetime="ephemeral",
                            files=["docs/elsewhere.md"],
                            session_id="a-different-session")
                self.assertEqual(_preflight(store, d), [])


class TestUnattendedRefusesWithoutIdentity(unittest.TestCase):
    """PRECONDITION 7. The store is readable and the run's session identity
    is established.

    One condition, because both halves fail the same way. Without a stable
    --session-id every invocation invents a fresh one, so the next process
    is refused as a foreign driver, the snapshot this one took is filed
    under a name nothing will look for again, and every fence it holds looks
    like somebody else's."""

    def test_an_unattended_run_with_no_session_id_refuses(self):
        with tempfile.TemporaryDirectory() as d:
            with bs.Store(d) as store:
                _seed(store)
                _sign(store)
                git = FakeGitFacts()
                findings = _preflight(store, d, session_id="", git=git)
                self.assertEqual(_codes(findings),
                                 [bc.UNATTENDED_NO_IDENTITY])
                self.assertIn("session-id", findings[0][1])
                self.assertEqual(git.recovery_calls, [])

    def test_a_whitespace_session_id_is_no_session_id(self):
        with tempfile.TemporaryDirectory() as d:
            with bs.Store(d) as store:
                _seed(store)
                _sign(store)
                self.assertEqual(
                    _codes(_preflight(store, d, session_id="   ")),
                    [bc.UNATTENDED_NO_IDENTITY])

    def test_a_store_that_cannot_be_read_refuses_once_not_twice(self):
        """The store probe. It also proves the ordering rule the preflight
        states: a store that cannot answer the contract read is not asked
        about claims either, because that second failure would be the first
        one wearing a different name."""
        class _UnreadableStore(object):
            def latest_contract(self, project_id, raw=False):
                raise bs.BMStoreError("the database file is locked")

            def dump(self, raw=False):
                raise AssertionError(
                    "an unreadable store must not be asked a second "
                    "question")

        findings = bc.unattended_preflight(
            _UnreadableStore(), "/arranged/repo", "p1", "sess-night",
            env=dict(_UNATTENDED_GOOD_ENV), git=FakeGitFacts())
        self.assertEqual(_codes(findings), [bc.UNATTENDED_NO_IDENTITY])
        self.assertIn("locked", findings[0][1])


class TestUnattendedPreflightLeavesInteractiveUseAlone(unittest.TestCase):
    """THE PROPERTY THE WHOLE SECTION IS BUILT AROUND: interactive use is
    completely unchanged.

    Proved twice, from both ends. Structurally, the gate is not even reached
    without the flag (a poisoned bc.unattended_preflight is never called),
    and behaviourally, the real CLI drives a full start/plan/start/status
    flow on a machine arranged to fail every one of the eight conditions
    this arrangement can reach (the fence canary's own throwaway store is
    untouched by it, so it alone would still pass) and behaves exactly as
    it did before."""

    def test_without_the_flag_the_preflight_is_never_called(self):
        calls = []

        def _poisoned(*a, **kw):
            calls.append((a, kw))
            raise AssertionError(
                "the preflight ran on a command that did not ask for it")

        original = bc.unattended_preflight
        bc.unattended_preflight = _poisoned
        try:
            bc._refuse_unless_unattended_ready({}, None, "p1")
            bc._refuse_unless_unattended_ready(
                {"session-id": "s", "acknowledge-modified": ["a.py"]},
                None, "p1")
            self.assertEqual(calls, [])
            # CALIBRATION: without this the test above would also pass
            # against a gate that never calls the preflight at all.
            with self.assertRaises(AssertionError):
                bc._refuse_unless_unattended_ready(
                    {"unattended": True}, None, "p1")
            self.assertEqual(len(calls), 1)
        finally:
            bc.unattended_preflight = original

    def test_the_interactive_cli_flow_is_unchanged_on_a_failing_machine(self):
        """A temporary directory is not a git repository, the fence
        variables are cleared, and a foreign session holds a claim over the
        contract's own allowed path: everything this arrangement can reach
        would refuse (the fence canary is not reachable by it: the canary
        proves the hook refuses inside its own throwaway store, which this
        arrangement never touches). Every interactive command must still
        behave exactly as the CLI tests above expect."""
        with tempfile.TemporaryDirectory() as root:
            _cli_bootstrap(root)
            # A foreign claim over a path INSIDE the contract's allowed '.'
            # but outside the one unit's own write scope ('out.txt'), so it
            # fails the preflight's precondition 6 without changing what the
            # fence store would do to an interactive dispatch. The point of
            # this test is that MY gate changes nothing; arranging a
            # collision the fence store itself acts on would prove the
            # opposite of what it claims.
            claimed = _run_other(
                STORE_CLI, ["claim", "someone-else", "--files",
                            "other-file.txt",
                            "--session", "a-different-session"], root)
            self.assertEqual(claimed.returncode, 0,
                             "the arrangement itself must hold, or this "
                             "test proves nothing about a hostile machine: "
                             + claimed.stdout + claimed.stderr)
            hostile = {"BM_FENCE_MODE": "advisory", "BM_FENCE_STRICT": "0"}

            begin = _run_cli(
                ["start", "--project", "p1", "--outcome", "ship it",
                 "--done-definition", _DONE_CHECK_PASSES,
                 "--controller-id", "c1"] + list(CLI_ACTOR), root,
                extra_env=hostile)
            self.assertEqual(begin.returncode, 0,
                             begin.stdout + begin.stderr)
            self.assertIn("started for project p1", begin.stdout)
            self.assertIn("state NEW", begin.stdout)

            self.assertEqual(_cli_plan_one_unit(root).returncode, 0)

            resumed = _run_cli(
                ["start", "--project", "p1", "--controller-id", "c1"]
                + list(CLI_ACTOR), root, extra_env=hostile)
            self.assertEqual(resumed.returncode, 0,
                             resumed.stdout + resumed.stderr)
            self.assertIn("controller_brief", resumed.stdout,
                          "the resume path still dispatches and prints the "
                          "unit brief, exactly as it did before the gate "
                          "existed")

            stepped = _run_cli(
                ["step", "--project", "p1", "--controller-id", "c1"]
                + list(CLI_ACTOR), root, extra_env=hostile)
            self.assertEqual(stepped.returncode, 0,
                             stepped.stdout + stepped.stderr)

            status = _run_cli(["status", "--project", "p1", "--json"], root,
                              extra_env=hostile)
            self.assertEqual(status.returncode, 0,
                             status.stdout + status.stderr)

            # THE OTHER HALF, on the SAME machine and the SAME store: the
            # identical command WITH the flag is refused, and by more than
            # one condition. Without this the test above would also pass
            # against a gate that never refuses anything.
            refused = _run_cli(
                ["step", "--project", "p1", "--controller-id", "c1",
                 "--unattended"] + list(CLI_ACTOR), root,
                extra_env=hostile)
            out = refused.stdout + refused.stderr
            self.assertEqual(refused.returncode, 1, out)
            for code in (bc.UNATTENDED_FENCE_ADVISORY,
                         bc.UNATTENDED_FENCE_NOT_STRICT,
                         bc.UNATTENDED_NO_REPOSITORY,
                         bc.UNATTENDED_FOREIGN_CLAIM):
                self.assertIn(code, out,
                              "this machine really does fail every one of "
                              "these, so the unflagged flow above really "
                              "did run past all of them")


class TestUnattendedCLIRefusesEndToEnd(unittest.TestCase):
    """The gate through the REAL command line, both doors. `step` is gated
    as well as `start` because an unattended driver is a loop, and a loop
    calling `step` directly would walk past a gate that only guarded
    `start`."""

    def _hostile(self):
        return {"BM_FENCE_MODE": "advisory", "BM_FENCE_STRICT": "0"}

    def test_start_unattended_refuses_and_explains_itself(self):
        with tempfile.TemporaryDirectory() as root:
            _cli_bootstrap(root)
            r = _run_cli(
                ["start", "--project", "p1", "--outcome", "ship it",
                 "--done-definition", _DONE_CHECK_PASSES,
                 "--controller-id", "c1", "--unattended"]
                + list(CLI_ACTOR), root, extra_env=self._hostile())
            out = r.stdout + r.stderr
            self.assertEqual(r.returncode, 1, out)
            self.assertIn(bc.UNATTENDED_FENCE_ADVISORY, out)
            self.assertIn(bc.UNATTENDED_FENCE_NOT_STRICT, out)
            self.assertIn("export BM_FENCE_MODE=enforced", out)
            self.assertIn("export BM_FENCE_STRICT=1", out)
            self.assertIn("what to do:", out,
                          "a reason code never reaches a founder without "
                          "its plain-language rewrite (defect M08)")
            r2 = _run_cli(["status", "--project", "p1", "--json"], root)
            self.assertEqual(r2.returncode, 1,
                             "a refused unattended start opens no run, so "
                             "status still has nothing to report: "
                             + r2.stdout + r2.stderr)
            self.assertIn("no controller run", r2.stdout + r2.stderr)

    def test_step_unattended_is_gated_too(self):
        with tempfile.TemporaryDirectory() as root:
            _cli_bootstrap(root)
            _cli_begin(root)
            _cli_plan_one_unit(root)
            r = _run_cli(
                ["step", "--project", "p1", "--controller-id", "c1",
                 "--unattended"] + list(CLI_ACTOR), root,
                extra_env=self._hostile())
            out = r.stdout + r.stderr
            self.assertEqual(r.returncode, 1, out)
            self.assertIn(bc.UNATTENDED_FENCE_ADVISORY, out)

    def test_acknowledge_modified_is_repeatable_and_reaches_the_gate(self):
        """The flag has to accept more than one path, or "acknowledge every
        modification" is unsayable on a tree with two of them."""
        with tempfile.TemporaryDirectory() as root:
            _cli_bootstrap(root)
            r = _run_cli(
                ["start", "--project", "p1", "--outcome", "ship it",
                 "--done-definition", _DONE_CHECK_PASSES,
                 "--controller-id", "c1", "--unattended",
                 "--acknowledge-modified", "a.py",
                 "--acknowledge-modified", "docs/b.md"]
                + list(CLI_ACTOR), root,
                extra_env={"BM_FENCE_MODE": "enforced",
                           "BM_FENCE_STRICT": "1"})
            out = r.stdout + r.stderr
            self.assertEqual(r.returncode, 1, out)
            self.assertIn(bc.UNATTENDED_NO_REPOSITORY, out,
                          "a temporary directory is no repository, which is "
                          "the condition that must be left refusing once "
                          "the fence and acknowledgement ones are met")
            self.assertNotIn("unrecognized flag", out)


class TestEveryUnattendedRefusalIsRewritten(unittest.TestCase):
    """DEFECT M08 (docs/mistakes/M08-new-refusals-had-no-plain-language-
    twice.md), closed for each of these eight (the original seven, plus the
    fence canary's UNATTENDED_FENCE_NOT_PROVEN added 2026-08-10) at the
    point it was written rather than after a founder met a raw code. The
    list is read from the SHIPPED module, never from a hand list here,
    because a hand list is exactly what goes stale the day somebody adds a
    ninth."""

    def _declared_codes(self):
        """Every module-level constant in tools/bm_controller.py whose name
        starts with UNATTENDED_ and whose value is an 'unattended-' reason
        code, read with ast from the shipped source."""
        with io.open(CONTROLLER_FILE, encoding="utf-8") as fh:
            tree = ast.parse(fh.read(), filename="bm_controller.py")
        codes = {}
        for node in tree.body:
            if not isinstance(node, ast.Assign):
                continue
            if not (isinstance(node.value, ast.Constant)
                    and isinstance(node.value.value, str)
                    and node.value.value.startswith("unattended-")):
                continue
            for target in node.targets:
                if isinstance(target, ast.Name):
                    codes[target.id] = node.value.value
        return codes

    def test_every_code_the_preflight_can_emit_has_a_plain_rewrite(self):
        declared = set(self._declared_codes().values())
        self.assertEqual(len(declared), 8,
                         "eight preconditions, eight reason codes; found "
                         "%s" % (sorted(declared),))
        missing = sorted(declared - set(bc.UNATTENDED_REFUSAL_HELP))
        self.assertEqual(missing, [],
                         "reason code(s) a founder could meet with no "
                         "plain-language rewrite: %s" % (missing,))
        extra = sorted(set(bc.UNATTENDED_REFUSAL_HELP) - declared)
        self.assertEqual(extra, [],
                         "UNATTENDED_REFUSAL_HELP names code(s) nothing can "
                         "emit, so they are dead entries: %s" % (extra,))

    def test_every_constant_is_actually_reachable(self):
        """A code declared and never used would satisfy the map test above
        while being unreachable, which is a different way of lying about
        what the gate checks."""
        with io.open(CONTROLLER_FILE, encoding="utf-8") as fh:
            tree = ast.parse(fh.read(), filename="bm_controller.py")
        loaded = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Name) and isinstance(node.ctx, ast.Load):
                loaded.add(node.id)
        unused = sorted(name for name in self._declared_codes()
                        if name not in loaded)
        self.assertEqual(unused, [],
                         "declared but never read: %s" % (unused,))

    def test_each_rewrite_has_all_three_non_empty_parts(self):
        for code, entry in sorted(bc.UNATTENDED_REFUSAL_HELP.items()):
            self.assertEqual(len(entry), 3,
                             "%s must be (context, hint, next_action)"
                             % (code,))
            for part in entry:
                self.assertTrue(part.strip(),
                                "%s has an empty part" % (code,))

    def test_the_refusal_text_carries_every_finding_with_its_rewrite(self):
        text = bc.unattended_refusal_text(
            [(bc.UNATTENDED_FENCE_ADVISORY, "the engine's own sentence"),
             (bc.UNATTENDED_DIRTY_TREE, "and the second one")])
        for code in (bc.UNATTENDED_FENCE_ADVISORY, bc.UNATTENDED_DIRTY_TREE):
            self.assertIn(code, text)
            for part in bc.UNATTENDED_REFUSAL_HELP[code]:
                self.assertIn(part, text)
        self.assertIn("the engine's own sentence", text)
        self.assertIn("and the second one", text)
        self.assertIn("2 of the eight", text)

    def test_no_em_or_en_dash_reaches_a_founder_from_this_map(self):
        """Written as escapes, not as the characters themselves: this file
        is inside the same no-dash scan it is asserting about, so a literal
        here would be the very defect it exists to catch."""
        for code, entry in sorted(bc.UNATTENDED_REFUSAL_HELP.items()):
            for part in entry:
                self.assertNotIn("\u2014", part, code)
                self.assertNotIn("\u2013", part, code)


# ---------------------------------------------------------------------------
# R7 / F2: the plan gate. Calibrated both ways (a gate that cannot go
# red verifies nothing): refused with no plan, refused when the approved
# plan drifted, and proceeds only when a plan is complete, approved, and
# undrifted. plan_gate is None on every OTHER test in this whole file
# (bc.PlanGate's own docstring in tools/bm_controller.py explains why
# that default exists); this section is the only place that ever
# constructs an engine, or drives the CLI, with one engaged.
# ---------------------------------------------------------------------------

_PLAN_GATE_CONTENT = {
    "options": ["do nothing", "create a.py"],
    "recommended": "create a.py",
    "will_run": ["u1: create a.py"],
    "will_not_run": ["everything else"],
    "blast_radius": "one new file, a.py",
    "reversibility": "git revert, no data written",
    "evidence_promised": "the unit's own done_check exit code",
}


class TestR7PlanGateInvariant(unittest.TestCase):
    """Python API level: the engine's own _authorise_dispatch funnel,
    exercised through step(), never through a printed string."""

    def _record_and_approve(self, root, drift_after_approval=False):
        plan_store = bc.bm_plan.PlanStore(root)
        plan_store.record("p1", _PLAN_GATE_CONTENT)
        plan_store.approve("p1", "khalil")
        if drift_after_approval:
            plan_store.record(
                "p1", dict(_PLAN_GATE_CONTENT,
                          blast_radius="widened after approval, v2"))
        return plan_store

    def test_dispatch_is_refused_when_no_plan_was_ever_recorded(self):
        with tempfile.TemporaryDirectory() as d:
            with bs.Store(d) as store:
                _seed(store)
                _sign(store)
                plan_gate = bc.StorePlanGate(bc.bm_plan.PlanStore(d))
                engine = _engine(store, FakeWorker({}), FakeCheckRunner(),
                                 plan_gate=plan_gate)
                _begin_and_plan(
                    engine, store, "p1", [_unit("u1", write_scope=["a.py"])])
                summary = engine.step("p1")
                self.assertEqual(summary["stop_reason"], "PLAN_GATE_REFUSED")
                self.assertEqual(
                    store.get_run("p1", raw=True)["state"], "STOPPED")

    def test_dispatch_is_refused_when_the_plan_drifted_after_approval(self):
        with tempfile.TemporaryDirectory() as d:
            with bs.Store(d) as store:
                _seed(store)
                _sign(store)
                self._record_and_approve(d, drift_after_approval=True)
                plan_gate = bc.StorePlanGate(bc.bm_plan.PlanStore(d))
                engine = _engine(store, FakeWorker({}), FakeCheckRunner(),
                                 plan_gate=plan_gate)
                _begin_and_plan(
                    engine, store, "p1", [_unit("u1", write_scope=["a.py"])])
                summary = engine.step("p1")
                self.assertEqual(summary["stop_reason"], "PLAN_GATE_REFUSED")

    def test_dispatch_proceeds_when_the_plan_is_complete_and_approved(self):
        with tempfile.TemporaryDirectory() as d:
            with bs.Store(d) as store:
                _seed(store)
                _sign(store)
                self._record_and_approve(d, drift_after_approval=False)
                plan_gate = bc.StorePlanGate(bc.bm_plan.PlanStore(d))
                worker = FakeWorker({"u1": _worker_result()})
                engine = _engine(store, worker, FakeCheckRunner(),
                                 plan_gate=plan_gate)
                _begin_and_plan(
                    engine, store, "p1", [_unit("u1", write_scope=["a.py"])])
                summary = engine.step("p1")
                self.assertNotEqual(summary["stop_reason"],
                                    "PLAN_GATE_REFUSED")
                self.assertEqual(summary["completed"], ["u1"])

    def test_a_none_plan_gate_is_the_untouched_default(self):
        """Every OTHER test in this whole file constructs an engine this
        way. This is the explicit regression pin for it: with no
        plan_gate injected, R7 checks nothing new, so a run with no plan
        at all still dispatches exactly as it always has."""
        with tempfile.TemporaryDirectory() as d:
            with bs.Store(d) as store:
                _seed(store)
                _sign(store)
                worker = FakeWorker({"u1": _worker_result()})
                engine = _engine(store, worker, FakeCheckRunner())
                _begin_and_plan(
                    engine, store, "p1", [_unit("u1", write_scope=["a.py"])])
                summary = engine.step("p1")
                self.assertNotEqual(summary["stop_reason"],
                                    "PLAN_GATE_REFUSED")
                self.assertEqual(summary["completed"], ["u1"])


PLAN_CLI = os.path.join(HERE, "bm_plan.py")


def _cli_plan_record(root, project_id="p1", content=None):
    content = _PLAN_GATE_CONTENT if content is None else content
    plan_path = os.path.join(root, "plan-content-%s.json" % project_id)
    with io.open(plan_path, "w", encoding="utf-8") as fh:
        json.dump(content, fh)
    return _run_other(PLAN_CLI, ["record", "--project", project_id,
                                "--file", plan_path], root)


def _cli_plan_approve(root, project_id="p1"):
    return _run_other(
        PLAN_CLI, ["approve", "--project", project_id, "--actor-name",
                  "khalil"], root)


class TestControllerCLIRequirePlanGate(unittest.TestCase):
    """R7 through the real CLI, exit codes only: the house rule this row
    of the readiness board exists to enforce ("a gate in this estate
    printed FAIL and exited 0 with eleven tests passing over it"). Every
    OTHER CLI test in this file, including
    TestControllerCLIStartResumesWithNoDuplicateWork above, never passes
    --require-plan-gate and stays green untouched (it is opt in; see
    bc.PlanGate's own docstring)."""

    def test_step_exits_refused_with_no_plan_recorded_at_all(self):
        with tempfile.TemporaryDirectory() as root:
            _cli_bootstrap(root)
            _cli_begin(root)
            _cli_plan_one_unit(root)
            r = _run_cli(
                ["step", "--project", "p1", "--controller-id", "c1",
                 "--require-plan-gate"] + list(CLI_ACTOR), root)
            self.assertEqual(r.returncode, bc.EXIT_REFUSED,
                             r.stdout + r.stderr)
            self.assertIn("PLAN_GATE_REFUSED", r.stdout)

    def test_step_exits_refused_when_the_approved_plan_drifted(self):
        with tempfile.TemporaryDirectory() as root:
            _cli_bootstrap(root)
            _cli_begin(root)
            _cli_plan_one_unit(root)
            _cli_plan_record(root)
            approved = _cli_plan_approve(root)
            self.assertEqual(approved.returncode, 0, approved.stderr)
            mutated = dict(_PLAN_GATE_CONTENT,
                          blast_radius="widened after approval, v2")
            _cli_plan_record(root, content=mutated)
            r = _run_cli(
                ["step", "--project", "p1", "--controller-id", "c1",
                 "--require-plan-gate"] + list(CLI_ACTOR), root)
            self.assertEqual(r.returncode, bc.EXIT_REFUSED,
                             r.stdout + r.stderr)

    def test_step_exits_ok_once_the_plan_is_approved_and_undrifted(self):
        with tempfile.TemporaryDirectory() as root:
            _cli_bootstrap(root)
            _cli_begin(root)
            _cli_plan_one_unit(root)
            _cli_plan_record(root)
            approved = _cli_plan_approve(root)
            self.assertEqual(approved.returncode, 0, approved.stderr)
            r = _run_cli(
                ["step", "--project", "p1", "--controller-id", "c1",
                 "--require-plan-gate"] + list(CLI_ACTOR), root)
            self.assertEqual(r.returncode, bc.EXIT_OK, r.stdout + r.stderr)

    def test_the_same_run_still_dispatches_fine_without_the_flag(self):
        """The opt in default, pinned through the real CLI too: the SAME
        setup, no --require-plan-gate, no plan recorded anywhere, still
        exits ok, because R7 checks nothing unless it is engaged."""
        with tempfile.TemporaryDirectory() as root:
            _cli_bootstrap(root)
            _cli_begin(root)
            _cli_plan_one_unit(root)
            r = _run_cli(
                ["step", "--project", "p1", "--controller-id", "c1"]
                + list(CLI_ACTOR), root)
            self.assertEqual(r.returncode, bc.EXIT_OK, r.stdout + r.stderr)


if __name__ == "__main__":
    unittest.main(verbosity=2)

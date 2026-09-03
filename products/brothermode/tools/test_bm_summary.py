#!/usr/bin/env python3
"""Regression tests for tools/bm_summary.py: F10, the run summary as a
product object.

TWO KINDS OF TEST HERE, mirroring tools/test_bm_controller.py's own split
into engine tests and CLI tests:

THE PURE-FUNCTION TESTS drive summarize() directly against hand-built
dicts shaped like load_record()'s return value, no store, no subprocess,
fully deterministic. These are what PROVE the property the feature exists
for: mutate one field of a record and the printed summary must change
(TestSummaryCannotDriftFromItsRecord), and a thin or empty record must
summarise to explicit NO-DATA lines rather than invented content
(TestNoDataIsNeverInvented). Together they are the calibration this
feature's own brief demands: proof the summary tracks the record UP
(changes when the record does) and proof it tracks the record DOWN
(says nothing it was not told).

THE STORE INTEGRATION TEST drives one real unit through a real tools/
bm_store.py Store and a real tools/bm_controller.py ControllerEngine (the
same FakeWorker/FakeCheckRunner harness test_bm_controller.py uses, kept
here rather than imported so this file stays self-contained the way
every other test_*.py in this directory does), then calls load_record()
against that SAME on-disk store and checks the summary reflects what was
actually persisted. This is what proves bm_summary.py's one field-facing
function, load_record(), reads the REAL schema (controller_runs /
controller_units / controller_dispatches / autonomy_checkpoints) rather
than a shape this file merely imagined.

THE CLI TEST drives tools/bm_summary.py as a real subprocess, the same
discipline test_bm_controller.py's own CLI section uses, and asserts
EXIT CODES, never the printed verdict text, per this feature's own hard
rule. Exit codes are read directly off subprocess.run()'s own returncode,
never through a shell pipe, so nothing is lost before the assertion.

Python 3.9, standard library only. No network.

No em or en dash anywhere in this file, its comments, or its output.
"""

import importlib.util
import os
import subprocess
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))


def _load(name):
    """Load a sibling module by PATH, the same technique tools/
    bm_summary.py and tools/test_bm_controller.py already use for tools/
    bm_store.py."""
    spec = importlib.util.spec_from_file_location(
        name, os.path.join(HERE, name + ".py"))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


bs = _load("bm_store")
bc = _load("bm_controller")
bsum = _load("bm_summary")

SUMMARY_FILE = os.path.join(HERE, "bm_summary.py")


# ---------------------------------------------------------------------------
# Minimal harness, copied in miniature from tools/test_bm_controller.py's
# own fixtures rather than imported, so this file has no dependency on
# another test file's internals.
# ---------------------------------------------------------------------------

def _actor(name="controller"):
    return {"actor_type": "model", "actor_name": name}


def _project(pid="p1"):
    return {"project_id": pid, "name": "Project", "created_at":
            "2026-08-05T00:00:00Z", "updated_at": "2026-08-05T00:00:00Z"}


def _seed(store, pid="p1", actor=None):
    store.upsert_project(_project(pid), actor or _actor())
    return pid


def _sign(store, project_id="p1", actor=None):
    return store.sign_contract(
        project_id, "ship the widget", "the done-definition passes",
        ["."], [], ["file-create", "file-edit", "file-move", "build",
                    "test-run", "local-commit", "read-only-inspect"],
        None, None, "Khalil Maaouni", "sess1", actor or _actor(),
        supersede=False)


def _unit(unit_id, write_scope=None, done_check="true"):
    return {"unit_id": unit_id, "objective": "unit %s" % unit_id,
            "dependencies": [], "read_scope": [],
            "write_scope": write_scope or [], "role": "builder",
            "risk_class": "file-create", "lane": "default",
            "done_check": done_check, "done_check_expect_exit": 0,
            "verifier": ""}


def _worker_result(claim="done", artifacts=None):
    return {"worker_claim": claim, "artifacts": artifacts or [],
            "cost": {"tokens": 1, "minutes": 1}, "status": "returned"}


class FakeWorker(bc.WorkerAdapter):
    def __init__(self, scripts):
        self.scripts = scripts

    def run(self, brief):
        return self.scripts[brief["unit_id"]]


class FakeCheckRunner(bc.CheckRunner):
    def __init__(self, outcomes=None):
        self.outcomes = outcomes or {}

    def run(self, command, cwd):
        return self.outcomes.get(
            command, {"exit_code": 0, "stdout": "", "stderr": ""})


def _begin_and_plan(engine, project_id, units, outcome="ship it",
                    done_definition="echo done"):
    run = engine.begin(project_id, outcome, done_definition,
                       workflow_version=1)
    engine.plan(project_id, run["run_id"], units)
    return run


# ---------------------------------------------------------------------------
# Building blocks for the hand-built records the pure-function tests use.
# Every field mirrors what load_record() actually produces (see
# bm_summary.py's own docstring on that function).
# ---------------------------------------------------------------------------

def _bare_record(project_id="p1"):
    """No run at all: the thinnest possible record."""
    return {"project_id": project_id, "run": None, "units": [],
            "dispatches_by_unit": {}, "last_checkpoint": None,
            "spend": {"verdict": "no-data"}, "open_human_steps": []}


def _run_row(state="READY", run_id="run1"):
    return {"run_id": run_id, "project_id": "p1", "state": state,
            "workflow_version": 1, "outcome": "ship it",
            "done_definition": "echo done"}


def _unit_row(unit_id="u1", status="READY", write_scope=None):
    return {"unit_id": unit_id, "status": status,
            "write_scope": write_scope or []}


def _planned_record(unit_status="READY", dispatched=False,
                    write_scope=None):
    """A run with exactly one unit, and no checkpoint, no spend ceiling,
    no open human step: the shape a fresh plan has before anything runs."""
    unit = _unit_row(status=unit_status, write_scope=write_scope)
    dispatches = ([{"dispatch_id": "d1", "status": "DISPATCHED"}]
                  if dispatched else [])
    return {"project_id": "p1", "run": _run_row(),
            "units": [unit], "dispatches_by_unit": {"u1": dispatches},
            "last_checkpoint": None, "spend": {"verdict": "no-data"},
            "open_human_steps": []}


# ---------------------------------------------------------------------------
# The property the feature exists for: the summary cannot say more, or
# less, than the record says.
# ---------------------------------------------------------------------------

class TestSummaryCannotDriftFromItsRecord(unittest.TestCase):
    """summarize() is a pure function of its one argument: change the
    argument, and only the argument, and the printed lines must change
    to match. This is the "mutate the record, the summary changes" proof
    the brief asks for."""

    def test_changing_unit_status_to_done_changes_what_passed(self):
        before = _planned_record(unit_status="READY")
        after = _planned_record(unit_status="DONE",
                                write_scope=["a.py"])
        summary_before = bsum.render(before)
        summary_after = bsum.render(after)
        self.assertNotEqual(summary_before, summary_after)
        self.assertIn("what passed: none yet", summary_before)
        self.assertIn("what passed: u1", summary_after)
        self.assertIn("what changed: a.py", summary_after)

    def test_changing_unit_status_to_failed_changes_what_failed(self):
        record = _planned_record(unit_status="FAILED")
        summary = bsum.render(record)
        self.assertIn("what failed: u1", summary)
        self.assertNotIn("what failed: none", summary)

    def test_adding_a_dispatch_moves_the_unit_out_of_never_dispatched(self):
        never_dispatched = _planned_record(dispatched=False)
        dispatched = _planned_record(dispatched=True)
        self.assertIn("could not be established (never dispatched): u1",
                     bsum.render(never_dispatched))
        self.assertIn(
            "could not be established (never dispatched): none",
            bsum.render(dispatched))
        self.assertNotEqual(bsum.render(never_dispatched),
                            bsum.render(dispatched))

    def test_an_open_human_step_becomes_the_next_action_and_outranks_ready_units(self):
        record = _planned_record(unit_status="READY")
        record["open_human_steps"] = [{"what": "confirm the deploy"}]
        summary = bsum.render(record)
        self.assertIn("waiting on the founder: 1 open step(s)", summary)
        self.assertIn("next action: resolve 1 open human step(s)", summary)

    def test_a_terminal_run_state_reports_no_next_action(self):
        record = _planned_record(unit_status="DONE", write_scope=["a.py"])
        record["run"]["state"] = "COMPLETE"
        summary = bsum.render(record)
        self.assertIn("next action: none, this run is COMPLETE.", summary)

    def test_changing_only_the_project_id_changes_only_that_line(self):
        r1 = _planned_record()
        r2 = _planned_record()
        r2["project_id"] = "p2"
        lines1 = bsum.summarize(r1)
        lines2 = bsum.summarize(r2)
        self.assertNotEqual(lines1[0], lines2[0])
        self.assertEqual(lines1[1:], lines2[1:],
                         "changing project_id alone must not move any "
                         "other line")

    def test_two_records_that_agree_on_every_field_summarize_identically(self):
        r1 = _planned_record(unit_status="DONE", write_scope=["a.py"])
        r2 = _planned_record(unit_status="DONE", write_scope=["a.py"])
        self.assertEqual(bsum.render(r1), bsum.render(r2))


class TestNoDataIsNeverInvented(unittest.TestCase):
    """A thin or empty record must summarise to explicit NO-DATA lines,
    never to a guess dressed up as an answer, and NO-DATA must never be
    folded into "failed" or "passed"."""

    def test_no_run_at_all_is_no_data_not_a_failure(self):
        summary = bsum.render(_bare_record())
        self.assertIn("run: NO-DATA", summary)
        self.assertNotIn("what failed", summary)
        self.assertNotIn("FAILED", summary)

    def test_a_run_with_no_units_yet_is_no_data_on_every_unit_line(self):
        record = {"project_id": "p1", "run": _run_row(state="PLANNING"),
                  "units": [], "dispatches_by_unit": {},
                  "last_checkpoint": None, "spend": {"verdict": "no-data"},
                  "open_human_steps": []}
        summary = bsum.render(record)
        self.assertIn("what changed: NO-DATA", summary)
        self.assertIn("what actually ran: NO-DATA", summary)
        self.assertIn("what passed: NO-DATA", summary)
        # "what failed: none" is a true statement here (no unit exists to
        # have failed), never NO-DATA and never invented detail.
        self.assertIn("what failed: none", summary)

    def test_no_checkpoint_recorded_is_no_data_not_a_default_timestamp(self):
        summary = bsum.render(_planned_record())
        self.assertIn("last checkpoint: NO-DATA", summary)

    def test_no_spend_ceiling_is_no_data_not_ok(self):
        summary = bsum.render(_planned_record())
        self.assertIn("spend: NO-DATA", summary)
        self.assertNotIn("spend: ok", summary)

    def test_never_dispatched_is_named_separately_from_failed(self):
        """A unit nobody ever dispatched and a unit that was dispatched
        and lost are different facts; the summary must not collapse them
        into one line."""
        record = _planned_record(unit_status="READY", dispatched=False)
        summary = bsum.render(record)
        self.assertIn("could not be established (never dispatched): u1",
                     summary)
        self.assertIn("what failed: none", summary)

    def test_render_is_summarize_joined_by_newline(self):
        record = _planned_record()
        self.assertEqual(bsum.render(record), "\n".join(
            bsum.summarize(record)))


# ---------------------------------------------------------------------------
# Store integration: load_record() against a REAL bm_store.py schema.
# ---------------------------------------------------------------------------

class TestLoadRecordReadsTheRealStore(unittest.TestCase):
    """Drives one unit to DONE through a real Store and a real
    ControllerEngine, then proves load_record()/summarize() reads back
    exactly what was persisted: not a shape this file merely assumed the
    schema has."""

    def test_a_completed_unit_summarises_as_passed_with_a_checkpoint(self):
        with tempfile.TemporaryDirectory() as d:
            with bs.Store(d) as store:
                _seed(store)
                _sign(store)
                worker = FakeWorker(
                    {"u1": _worker_result(artifacts=["a.py"])})
                checker = FakeCheckRunner({})
                engine = bc.ControllerEngine(
                    store, worker, checker, "ctrl1", _actor())
                _begin_and_plan(engine, "p1", [_unit("u1",
                                                     write_scope=["a.py"])])
                engine.step("p1")

            record = bsum.load_record(d, "p1", raw=True)
            self.assertEqual(record["run"]["project_id"], "p1")
            self.assertEqual(len(record["units"]), 1)
            self.assertEqual(record["units"][0]["status"], "DONE")
            self.assertTrue(record["dispatches_by_unit"]["u1"],
                            "a unit that ran must carry at least one "
                            "real dispatch row")
            self.assertIsNotNone(record["last_checkpoint"],
                                 "record_checkpoint fired on acceptance; "
                                 "load_record must see it")

            summary = bsum.render(record)
            self.assertIn("what passed: u1", summary)
            self.assertIn("what changed: a.py", summary)
            self.assertNotIn("last checkpoint: NO-DATA", summary)

    def test_an_undispatched_unit_summarises_as_never_established(self):
        with tempfile.TemporaryDirectory() as d:
            with bs.Store(d) as store:
                _seed(store)
                _sign(store)
                engine = bc.ControllerEngine(
                    store, FakeWorker({}), FakeCheckRunner({}), "ctrl1",
                    _actor())
                _begin_and_plan(engine, "p1", [
                    _unit("u1", write_scope=["a.py"]),
                    _unit("u2", write_scope=["b.py"])])
                # Deliberately never call engine.step(): nothing has run.

            record = bsum.load_record(d, "p1", raw=True)
            for uid in ("u1", "u2"):
                self.assertEqual(record["dispatches_by_unit"][uid], [])

            summary = bsum.render(record)
            self.assertIn(
                "could not be established (never dispatched): u1, u2",
                summary)
            self.assertIn("what passed: none yet", summary)
            self.assertIn("what failed: none", summary)


# ---------------------------------------------------------------------------
# CLI: real subprocess, exit codes asserted, never printed verdicts.
# ---------------------------------------------------------------------------

class TestCLI(unittest.TestCase):

    def _run(self, args, cwd):
        # Exit code captured directly off returncode, before any pipe.
        proc = subprocess.run(
            [sys.executable, SUMMARY_FILE] + args, cwd=cwd,
            stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        return proc.returncode, proc.stdout.decode("utf-8")

    def test_help_exits_ok(self):
        code, _out = self._run(["--help"], HERE)
        self.assertEqual(code, bsum.EXIT_OK)

    def test_missing_project_flag_exits_usage(self):
        # Bare empty argv is the documented help shortcut (EXIT_OK,
        # mirroring bm_controller.py's own main()); a real invocation
        # that supplies OTHER flags but omits the required --project is
        # the actual missing-flag case, and that is the one this exit
        # code is asserted against.
        with tempfile.TemporaryDirectory() as d:
            code, _out = self._run(["--json"], d)
            self.assertEqual(code, bsum.EXIT_USAGE)

    def test_unrecognized_flag_exits_usage(self):
        with tempfile.TemporaryDirectory() as d:
            code, _out = self._run(["--project", "p1", "--bogus"], d)
            self.assertEqual(code, bsum.EXIT_USAGE)

    def test_no_root_at_all_exits_refused(self):
        with tempfile.TemporaryDirectory() as d:
            code, _out = self._run(["--project", "p1"], d)
            self.assertEqual(code, bsum.EXIT_REFUSED)

    def test_a_project_with_no_run_exits_ok_with_no_data(self):
        with tempfile.TemporaryDirectory() as d:
            with bs.Store(d, create=True) as store:
                _seed(store)
            code, out = self._run(["--project", "p1"], d)
            self.assertEqual(code, bsum.EXIT_OK)
            self.assertIn("NO-DATA", out)

    def test_json_output_is_valid_json_and_exits_ok(self):
        with tempfile.TemporaryDirectory() as d:
            with bs.Store(d, create=True) as store:
                _seed(store)
            code, out = self._run(["--project", "p1", "--json"], d)
            self.assertEqual(code, bsum.EXIT_OK)
            import json
            payload = json.loads(out)
            self.assertIn("summary", payload)
            self.assertIn("record", payload)


if __name__ == "__main__":
    unittest.main()

#!/usr/bin/env python3
"""The seven unit replay of 2026-09-30: a configuration fault never exhausts a unit, and the units come back by a fact.

THE INCIDENT. A Claude only run pinned `claude` 2.1.251, which answered every call with
`exit 1: [claude-code:unrecognized_model]`. All 78 attempts were counted as the model's losses and 7 sub units parked
EXHAUSTED in 6 minutes while the intake said READY. The mutations that must turn this suite red:

  M_CONFIG_AS_MODEL           pass_pulse.losses counts a CONFIG failure as a model loss again
  M_COUNT_CONFIG_ROUND        a round whose every call was CONFIG_WAIT is not recognised, so it is graded and spent
  M_CLOSE_WITH_STALE_SUCCESS  a proof that is not newer than the trip closes the configuration breaker
  M_PROGRAM_NOT_A_FACT        the resolved program record is not a fact for runner_pool, so parked units never return

Nothing here calls a model. The pool cases run runner_pool.py --dry in a fabricated HOME (the harness of
scripts/test_runner_pool_guard.py, reused, not copied).
Run from the repository root: python3 -B scripts/loop/test_config_recovery.py
"""
import importlib.util, json, os, re, shutil, sys, tempfile, time, unittest

LOOP = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(os.path.dirname(LOOP))
sys.path[:0] = [LOOP, os.path.join(ROOT, "scripts")]

import breaker as BR  # noqa: E402
import fanout_verdict as FV  # noqa: E402
import pass_pulse  # noqa: E402

INCIDENT = 'exit 1: [claude-code:unrecognized_model] {"model":"claude-opus-5-5","query_source":"sdk"}'
SEVEN = ["D3.1", "D4.2", "D5.3", "L2.1", "L3.2", "R4.1", "R5.2"]


def _load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec); spec.loader.exec_module(mod)
    return mod


GUARD = _load("test_runner_pool_guard", os.path.join(ROOT, "scripts", "test_runner_pool_guard.py"))
failure_ledger = _load("failure_ledger", os.path.join(ROOT, "scripts", "failure_ledger.py"))


def incident_round(run_dir, n_calls):
    """One round of tonight's run: n worker calls, each the unrecognized_model exit, as or_fanout wrote it then."""
    rd = os.path.join(run_dir, "round0"); os.makedirs(os.path.join(rd, "out"), exist_ok=True)
    with open(os.path.join(rd, "results.json"), "w", encoding="utf-8") as fh:
        json.dump([{"id": "r%d" % i, "model": "sonnet", "ok": False, "error": INCIDENT} for i in range(n_calls)], fh)
    return rd


class TheLedgers(unittest.TestCase):
    """CONFIG is reported on its own, and never enters the model loss denominator."""

    def setUp(self):
        self.runs = tempfile.mkdtemp(prefix="config-recovery-runs-")
        self.addCleanup(shutil.rmtree, self.runs, True)
        per = [12, 11, 11, 11, 11, 11, 11]   # 78 calls over the seven sub units, as measured
        for sub, n in zip(SEVEN, per):
            incident_round(os.path.join(self.runs, "%s-000001" % sub), n)

    def test_the_78_incident_calls_are_config_not_model(self):
        """M_CONFIG_AS_MODEL: tonight every one of these read as 'model'."""
        counts = pass_pulse.losses(self.runs, 0)
        self.assertEqual(counts.get("config"), 78, counts)
        self.assertNotIn("model", counts, "M_CONFIG_AS_MODEL: a configuration fault was counted as a model loss")

    def test_the_loss_report_keeps_config_out_of_the_denominator_and_names_it(self):
        line, warn = pass_pulse.loss_report({"config": 78, "pass": 1, "model": 1})
        self.assertIn("LOSSES  2 attempts", line)
        self.assertIn("CONFIG 78 call(s) held, not counted", line)
        line0, warn0 = pass_pulse.loss_report({"config": 78})
        self.assertTrue(line0.startswith("LOSSES  NO-DATA") and "CONFIG 78" in line0, line0)
        self.assertTrue(warn0 and warn0.startswith("WARN CONFIG"), warn0)

    def test_the_failure_ledger_names_it_config_and_keeps_it_out_of_builder_briefs(self):
        self.assertEqual(failure_ledger.classify(INCIDENT), "config")
        self.assertEqual(failure_ledger.CONFIG_PATTERN, BR.CONFIG_PATTERN, "the two copies of the CONFIG pattern drifted")
        rows = [{"class": "config", "at": time.time()}] * 5 + [{"class": "patch-stale", "at": time.time()}]
        brief = failure_ledger.brief_rules(rows)
        self.assertFalse(any(l.startswith("- config") for l in brief), brief)

    def test_worker_mix_never_charges_config_to_the_arm(self):
        import worker_mix, unit_ledger
        rows = [{"ok": False, "status": "EXHAUSTED", "call_error": "CONFIG_WAIT: the program does not know this model",
                 "model": "sonnet", "run_at": time.time()}]
        orig = unit_ledger.last_rows
        unit_ledger.last_rows = lambda path: rows
        try:
            self.assertEqual(worker_mix.arm_stats(), {})
        finally:
            unit_ledger.last_rows = orig


class TheRound(unittest.TestCase):
    """A round whose every call is CONFIG_WAIT spends no attempt: unit_runner ends it CONFIG_WAIT before any grade."""

    def test_every_incident_round_is_a_config_wait(self):
        """M_COUNT_CONFIG_ROUND: the runner's own test for such a round."""
        d = tempfile.mkdtemp(prefix="config-round-"); self.addCleanup(shutil.rmtree, d, True)
        new = os.path.join(d, "new.json")
        with open(new, "w") as fh:
            json.dump([{"id": "a", "ok": False, "config": True, "error": "CONFIG_WAIT: held"}] * 5, fh)
        self.assertIs(FV.all_config_wait(new), True, "M_COUNT_CONFIG_ROUND: a CONFIG_WAIT round was not recognised")
        rd = incident_round(os.path.join(d, "D3.1-000001"), 5)
        self.assertIs(FV.all_config_wait(os.path.join(rd, "results.json")), False,
                      "raw pre-fix rows carry no CONFIG_WAIT word; only the boundary's own records hold a round")

    def test_unit_runner_ends_a_config_round_before_grading_teaching_or_counting(self):
        with open(os.path.join(LOOP, "unit_runner.py"), encoding="utf-8") as fh:
            src = fh.read()
        at = src.find("FV.all_config_wait(")
        self.assertGreater(at, 0, "unit_runner no longer checks for a CONFIG_WAIT round")
        block = src[at:at + 600]
        self.assertIn('status("CONFIG_WAIT', block)
        self.assertIn("sys.exit(6)", block)
        self.assertNotIn("teach(", block)
        grade = src.find('B.run_bounded([os.path.join(BIN, "grade_lane.sh")')
        self.assertGreater(grade, 0)
        self.assertLess(at, grade, "the CONFIG_WAIT exit must come before the round is graded")

    def test_no_grade_means_no_ev_history(self):
        import ev_gate
        d = tempfile.mkdtemp(prefix="config-ev-"); self.addCleanup(shutil.rmtree, d, True)
        lp = os.path.join(d, "l.jsonl")
        with open(lp, "w") as fh:
            for sub in SEVEN:
                fh.write(json.dumps({"sub": sub, "run": sub + "-000001", "round": 0, "ok": False,
                                     "call_error": INCIDENT, "grade": None}) + "\n")
        for sub in SEVEN:
            self.assertEqual(ev_gate.history(sub, ledger_path=lp), (0, 0), sub)


class TheBreakerCloses(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="config-close-"); self.addCleanup(shutil.rmtree, self.tmp, True)
        self._s = os.environ.get("BROTHER_OR_STATE_ROOT"); os.environ["BROTHER_OR_STATE_ROOT"] = self.tmp
        self.addCleanup(lambda: os.environ.pop("BROTHER_OR_STATE_ROOT", None) if self._s is None
                        else os.environ.__setitem__("BROTHER_OR_STATE_ROOT", self._s))
        self.key = BR.config_key("claude", "claude-opus-5-5")
        BR.record(self.key, "CONFIG", "trip", INCIDENT, now=1000.0)

    def test_a_stale_success_never_closes_it(self):
        """M_CLOSE_WITH_STALE_SUCCESS: an answer from a call admitted before the trip, or a proof not newer than it."""
        BR.record(self.key, "ANSWERED", "late-answer", "", now=1005.0)
        self.assertEqual(BR.admit([self.key], "x", now=1006.0)[0], "OPEN", "an ANSWERED closed a configuration fault")
        ok, why = BR.close_config(self.key, proved_at=999.0, now=1007.0)
        self.assertFalse(ok, "M_CLOSE_WITH_STALE_SUCCESS: a proof older than the trip closed it")
        ok, why = BR.close_config(self.key, proved_at=1000.0, now=1007.0)
        self.assertFalse(ok, "M_CLOSE_WITH_STALE_SUCCESS: a proof at the trip's own instant closed it")
        self.assertEqual(BR.admit([self.key], "x", now=1008.0)[0], "OPEN")

    def test_a_fresh_proof_closes_it_and_a_new_fault_opens_a_new_generation(self):
        self.assertTrue(BR.close_config(self.key, proved_at=1010.0, now=1011.0)[0])
        self.assertEqual(BR.admit([self.key], "x", now=1012.0)[0], "CLOSED")
        rec = BR.record(self.key, "CONFIG", "again", INCIDENT, now=1020.0)
        self.assertEqual(rec["generation"], 2)
        self.assertFalse(BR.close_config(self.key, proved_at=1015.0, now=1021.0)[0],
                         "a proof from the previous generation closed the new one")


class ThePool(unittest.TestCase):
    """The seven parked sub units at runner_pool's entry point: parked until the resolved program changes, then back."""
    setUp = GUARD.RunnerPoolAdmission.setUp
    fund = GUARD.RunnerPoolAdmission.fund
    run_pool = GUARD.RunnerPoolAdmission.run_pool
    write_fixtures = GUARD.RunnerPoolAdmission.write_fixtures   # run_pool calls it since ACC3.b (e24e8be79)
    started = GUARD.RunnerPoolAdmission.started
    plant_exhausted = GUARD.RunnerPoolAdmission.plant_exhausted
    spec = GUARD.RunnerPoolAdmission.spec
    judge = GUARD.RunnerPoolAdmission.judge

    def units(self):
        units = []
        for sub in SEVEN:
            uid = sub.split(".")[0]
            self.plant_exhausted(sub, age=3600)
            incident_round(os.path.join(self.home, ".claude", "evidence", "unit-runs", "%s-000001" % sub), 11)
            then = time.time() - 3600
            os.utime(os.path.join(self.home, ".claude", "evidence", "unit-runs", "%s-000001" % sub), (then, then))
            self.spec(uid, age=7200, subs=[sub])
            units.append(GUARD.unit(uid, [sub]))
        self.judge("unit_runner.py", age=7200)
        return units

    def write_program_record(self):
        p = os.path.join(self.home, ".claude", "evidence", "loop-programs.json")
        with open(p, "w", encoding="utf-8") as fh:
            json.dump({"claude": {"path": "/new/claude", "version": "2.1.284"}}, fh)

    def test_the_seven_stay_parked_while_nothing_changed(self):
        out = self.run_pool(self.units(), scores={s: 10 for s in SEVEN}, env={"BROTHER_WIP": "10"})
        self.assertEqual(self.started(out), set(), out)
        self.assertEqual(len(re.findall(r"needs a fact", out)), 7, out)

    def test_the_seven_come_back_when_the_resolved_program_changes(self):
        """M_PROGRAM_NOT_A_FACT: the program record is the fact; no mtime of a parked run is touched."""
        units = self.units()
        self.write_program_record()
        out = self.run_pool(units, scores={s: 10 for s in SEVEN}, env={"BROTHER_WIP": "10"})
        self.assertEqual(self.started(out), set(SEVEN), "M_PROGRAM_NOT_A_FACT: parked units did not come back\n" + out)

    def test_a_config_wait_run_parks_until_the_program_changes(self):
        self.plant_exhausted("D3.1", word="CONFIG_WAIT", age=3600); self.spec("D3", age=7200); self.judge("unit_runner.py", age=7200)
        out = self.run_pool([GUARD.unit("D3", ["D3.1"])], scores={"D3.1": 10})
        self.assertNotIn("D3.1", self.started(out), out)
        self.write_program_record()
        out = self.run_pool([GUARD.unit("D3", ["D3.1"])], scores={"D3.1": 10})
        self.assertIn("D3.1", self.started(out), out)


if __name__ == "__main__":
    unittest.main()

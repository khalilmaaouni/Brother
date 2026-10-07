#!/usr/bin/env python3
"""Every transport crosses the configuration gate, and the FIRST CONFIG stops every queued call (2026-09-30).

THE INCIDENT. A Claude only run pinned an older `claude` (2.1.251) that answered every call with
`exit 1: [claude-code:unrecognized_model]`. Nothing classified that text: all 78 attempts were counted as model losses,
retried, and 7 sub units parked EXHAUSTED in 6 minutes. This suite holds the call boundary to three properties, each
with the mutation that must turn it red:

  M_BRIDGE_BYPASS         the bridge branch of or_fanout.run_job skips the configuration admission
  M_TRIP_THREE            the configuration breaker waits for three CONFIG answers instead of opening on the first
  M_SKIP_FINAL_ADMISSION  call_one skips the admission it repeats AFTER the slot wait

Every fixture isolates one condition, and no real program or paid call runs: transports are fake runners.
Run from the repository root: python3 -B scripts/loop/test_config_dispatch.py
"""
import json, os, shutil, sys, tempfile, types, unittest

LOOP = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(os.path.dirname(LOOP))
sys.path[:0] = [LOOP, ROOT]

import breaker as BR  # noqa: E402
import model_call as MC  # noqa: E402
import model_router as R  # noqa: E402

INCIDENT = 'exit 1: [claude-code:unrecognized_model] {"model":"claude-opus-5-5","query_source":"sdk"}'
REG = {"c": {"id": "claude-x", "transport": "claude", "privacy": R.PRIVATE, "quality": {"build": 1},
             "kinds": {"build"}, "cost": 1.0},
       "k": {"id": "gpt-x", "transport": "codex", "privacy": R.PUBLIC, "quality": {"build": 1},
             "kinds": {"build"}, "cost": 1.0}}


class FakeAdmission(object):
    """dispatch_semaphore's four calls; acquire_slot runs `during_wait` to stand for what happens while a call queues."""
    NoSlotAvailable = type("NoSlotAvailable", (Exception,), {})

    def __init__(self, during_wait=None):
        self.during_wait = during_wait

    def state_root(self):
        return os.environ["BROTHER_OR_STATE_ROOT"]

    def effective_slots(self, root, cap):
        return 4

    def acquire_slot(self, root, limit, holder, timeout_seconds=None):
        if self.during_wait:
            self.during_wait()
        return "slot"

    def release_slot(self, slot):
        return None


class Isolated(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="config-dispatch-")
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self._env = {k: os.environ.get(k) for k in ("BROTHER_OR_STATE_ROOT", "BROTHER_CLAUDE_CALLS_LEDGER",
                                                    "BROTHER_BRIDGE_CALLS_LEDGER", "BROTHER_BREAKER")}
        os.environ["BROTHER_OR_STATE_ROOT"] = os.path.join(self.tmp, "state")
        os.environ["BROTHER_CLAUDE_CALLS_LEDGER"] = os.path.join(self.tmp, "claude-calls.jsonl")
        os.environ["BROTHER_BRIDGE_CALLS_LEDGER"] = os.path.join(self.tmp, "bridge-calls.jsonl")
        os.environ.pop("BROTHER_BREAKER", None)   # the configuration breaker is mandatory: it must hold with the mode off
        self.addCleanup(self._restore)
        self._adm, self._claude = MC.admission, MC.CLAUDE
        MC.admission, MC.CLAUDE = FakeAdmission(), "/bin/echo"   # CLAUDE only names argv[0]; the runner is fake
        self.addCleanup(setattr, MC, "admission", self._adm)
        self.addCleanup(setattr, MC, "CLAUDE", self._claude)
        self.runs = []

    def _restore(self):
        for k, v in self._env.items():
            if v is None: os.environ.pop(k, None)
            else: os.environ[k] = v

    def runner(self, rc=0, out="", err=""):
        def run(argv, stdin, timeout):
            self.runs.append(argv)
            return {"returncode": rc, "stdout": out, "stderr": err}
        return run

    def open_key(self, transport, model):
        BR.record(BR.config_key(transport, model), "CONFIG", "fixture", "fixture: " + INCIDENT)


class TheBoundary(Isolated):
    def test_the_incident_text_is_config_at_the_classifier(self):
        self.assertEqual(BR.classify_cli("claude", 1, "", INCIDENT, None), "CONFIG")

    def test_a_generic_404_and_an_answer_about_models_are_not_config(self):
        self.assertEqual(BR.classify_bridge(1, "", "HTTP 404 from OpenRouter: not found"), "MALFORMED")
        self.assertEqual(BR.classify_cli("codex", 0, "the model_not_found error means X", "", None), "ANSWERED")

    def test_a_claude_call_that_answers_unrecognized_model_is_config_and_opens_the_breaker(self):
        a = MC.call_one("c", "p", "build", R.PRIVATE, reg=REG, runner=self.runner(rc=1, err=INCIDENT))
        self.assertEqual((a.ok, a.failure), (False, "CONFIG"), a.detail)
        self.assertTrue(a.detail.startswith("CONFIG_WAIT"), a.detail)
        self.assertEqual(BR.admit([BR.config_key("claude", "claude-x")], "t")[0], "OPEN")

    def test_claude_and_codex_are_refused_before_transmission_when_held(self):
        self.open_key("claude", "claude-x"); self.open_key("codex", "gpt-x")
        for name in ("c", "k"):
            a = MC.call_one(name, "p", "build", R.PRIVATE if name == "c" else R.PUBLIC, reg=REG, runner=self.runner(out="12"))
            self.assertEqual(a.failure, "CONFIG", (name, a.detail))
        self.assertEqual(self.runs, [], "a held model was transmitted")

    def test_config_is_never_counted_as_the_model_reliability(self):
        seen = []
        orig = R.record_outcome
        R.record_outcome = lambda *a, **k: seen.append(a)
        try:
            win, tried = MC.call("p", "build", R.PRIVATE, reg={"c": REG["c"]}, runner=self.runner(rc=1, err=INCIDENT))
        finally:
            R.record_outcome = orig
        self.assertIsNone(win)
        self.assertEqual([t.failure for t in tried], ["CONFIG"])
        self.assertEqual(seen, [], "a CONFIG attempt was recorded against the model")


class FirstConfigOpens(Isolated):
    def test_the_first_config_blocks_the_next_call_without_transmitting_it(self):
        """M_TRIP_THREE: one CONFIG answer must be enough. A breaker that waits for three lets two more calls go out."""
        MC.call_one("c", "p", "build", R.PRIVATE, reg=REG, runner=self.runner(rc=1, err=INCIDENT))
        self.assertEqual(len(self.runs), 1)
        second = MC.call_one("c", "p", "build", R.PRIVATE, reg=REG, runner=self.runner(out="12"))
        self.assertEqual(len(self.runs), 1, "M_TRIP_THREE: the call after the first CONFIG was transmitted")
        self.assertEqual(second.failure, "CONFIG")

    def test_it_opens_with_the_breaker_mode_off_and_in_shadow(self):
        os.environ["BROTHER_BREAKER"] = "off"
        MC.call_one("c", "p", "build", R.PRIVATE, reg=REG, runner=self.runner(rc=1, err=INCIDENT))
        self.assertEqual(BR.admit([BR.config_key("claude", "claude-x")], "t")[0], "OPEN")

    def test_a_queued_call_is_stopped_by_a_trip_that_happened_during_its_slot_wait(self):
        """M_SKIP_FINAL_ADMISSION: the early admission passed; another worker's CONFIG opened the breaker while this
        call waited for a slot. The admission repeated after the wait must stop it before anything is transmitted."""
        MC.admission = FakeAdmission(during_wait=lambda: self.open_key("claude", "claude-x"))
        a = MC.call_one("c", "p", "build", R.PRIVATE, reg=REG, runner=self.runner(out="12"))
        self.assertEqual(self.runs, [], "M_SKIP_FINAL_ADMISSION: a queued call started after the trip")
        self.assertEqual(a.failure, "CONFIG")


class TheBridgeBranch(Isolated):
    """or_fanout.run_job's bridge branch reaches the dispatcher directly, not through call_one; it must still pass the
    same configuration admission and the same classifier."""

    def setUp(self):
        super().setUp()
        from plugin.runtime.brother.core import or_fanout, scratch_root
        self.F = or_fanout
        os.makedirs(scratch_root.SCRATCH_ROOT, exist_ok=True)
        self.ws = scratch_root.mkdtemp("config-dispatch")
        self.addCleanup(shutil.rmtree, self.ws, True)
        self._ws = os.environ.get("BROTHER_WORKSPACE_ROOT")
        os.environ["BROTHER_WORKSPACE_ROOT"] = os.path.realpath(scratch_root.SCRATCH_ROOT)
        self.addCleanup(lambda: os.environ.pop("BROTHER_WORKSPACE_ROOT", None) if self._ws is None
                        else os.environ.__setitem__("BROTHER_WORKSPACE_ROOT", self._ws))
        self.calls = []

    def job(self):
        return {"id": "a", "model": "deepseek", "prompt": "p", "sensitivity": "public", "out": os.path.join(self.ws, "a.md")}

    def dispatch(self, rc=0, out="answer", err=""):
        def d(**kw):
            self.calls.append(kw)
            return types.SimpleNamespace(stdout=out, stderr=err, returncode=rc), kw.get("requested_model")
        return d

    def test_a_held_bridge_model_is_never_dispatched(self):
        """M_BRIDGE_BYPASS: without the admission on this branch the dispatcher is called for a held model."""
        self.open_key("bridge", self.F.REAL_MODEL_IDS["deepseek"])
        rec = self.F.run_job(self.job(), 300, 4, dispatch=self.dispatch())
        self.assertEqual(self.calls, [], "M_BRIDGE_BYPASS: the bridge branch dispatched a held model")
        self.assertTrue(rec.get("config"), rec)
        self.assertTrue(rec["error"].startswith("CONFIG_WAIT"), rec)

    def test_a_bridge_404_for_the_model_is_config_and_opens_its_breaker(self):
        err = "HTTP 404 from OpenRouter: No endpoints found for %s" % self.F.REAL_MODEL_IDS["deepseek"]
        rec = self.F.run_job(self.job(), 300, 4, dispatch=self.dispatch(rc=1, out="", err=err))
        self.assertTrue(rec.get("config"), rec)
        self.assertEqual(BR.admit([BR.config_key("bridge", self.F.REAL_MODEL_IDS["deepseek"])], "t")[0], "OPEN")

    def test_config_is_not_retried_and_spends_no_attempt(self):
        err = "HTTP 404 from OpenRouter: No endpoints found for x"
        rec = self.F.run_job_with_retries(self.job(), 300, 4, retries=2, dispatch=self.dispatch(rc=1, out="", err=err),
                                          sleep=lambda s: None)
        self.assertEqual(len(self.calls), 1, "a CONFIG answer was retried")
        self.assertEqual(len(rec["attempts"]), 1)

    def test_an_ordinary_bridge_failure_is_not_config(self):
        rec = self.F.run_job(self.job(), 300, 4, dispatch=self.dispatch(rc=1, out="", err="HTTP 502 upstream"))
        self.assertFalse(rec.get("config"), rec)


if __name__ == "__main__":
    unittest.main()

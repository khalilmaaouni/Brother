"""Every transport passes proof admission before it runs (money audit 2026-09-27, finding 2).

The Codex transport took a pool slot and ran its child with no admit_locked and no registration, so it ran after the
run's UNFUNDED ending marker and past its deadline (audit repro: provider_calls=1). It now passes the same
proof_ledger.admit_locked barrier the Claude START passes, under the same Claude ledger lock mark_ending takes. It
writes no ledger row: the transport reports no cost to record.

Entry point: model_call.call_one with an injected runner, the real shared admission pool in a scratch state root and
the real proof start fixture (test_proof_dispatch_accounting). Nothing reaches a model or the network. One condition
per case.

Run from the repository root: python3 -B scripts/test_model_call_codex_admission.py
"""
import json, os, sys, tempfile, time, unittest
from pathlib import Path
from unittest import mock

HERE = Path(__file__).resolve().parent
REPO = HERE.parent
LOOP = HERE / "loop"
sys.path.insert(0, str(LOOP))
sys.path.insert(0, str(REPO))
sys.path.insert(0, str(HERE))
os.environ["BROTHER_CODE_ROOT"] = str(REPO)   # model_call resolves its code root at import
import test_proof_dispatch_accounting as F   # a module, so its ProofDispatch tests are not collected here again
import model_call as MC   # noqa: E402

CODEX_REG = {"x": {"id": "gpt-x", "transport": "codex", "privacy": "public", "kinds": {"build"}, "cost": 1,
                   "quality": {"build": 5}}}


class Runner(object):
    def __init__(self):
        self.calls = 0

    def __call__(self, argv, stdin, timeout):
        self.calls += 1
        return {"returncode": 0, "stdout": "offline answer", "stderr": ""}


class Codex(unittest.TestCase):
    proof = True

    def setUp(self):
        if self.proof:
            F.ProofDispatch.setUp(self)
        self.d = Path(tempfile.mkdtemp(prefix="mc-codex-"))
        env = {"BROTHER_CLAUDE_CALLS_LEDGER": str(self.d / "claude-calls.jsonl"),
               "BROTHER_OR_STATE_ROOT": str(self.d / "state"), "BROTHER_CODE_ROOT": str(REPO),
               "BROTHER_BRIDGE_CALLS_LEDGER": str(self.d / "bridge-calls.jsonl")}
        patch = mock.patch.dict(os.environ, env)
        patch.start()
        self.addCleanup(patch.stop)
        if not self.proof:
            for key in ("BROTHER_PROOF_PHASE", "BROTHER_PROOF_BASELINE", "BROTHER_PROOF_BASELINE_SHA256", "BROTHER_RUN_DIR"):
                os.environ.pop(key, None)
        for target, value in ((MC.R, "codex_bin"), (MC, "_codex_root")):
            # the Codex adapter (FX-31.5) builds a line only for an executable program; the runner is fake, so this
            # interpreter stands in for the binary (argv[0] only, never run)
            p = mock.patch.object(target, value, return_value=sys.executable if value == "codex_bin" else str(REPO))
            p.start()
            self.addCleanup(p.stop)

    launch = F.ProofDispatch.launch
    write_start = F.ProofDispatch.write_start

    def call(self, timeout=300):
        runner = Runner()
        return MC.call_one("x", "offline fixture", "build", "public", timeout=timeout, reg=CODEX_REG, runner=runner), runner


class ACodexCallInAProofIsAdmitted(Codex):
    def test_the_audit_case_an_unfunded_ending_and_an_expired_deadline_start_nothing(self):
        (self.run / "proof" / "ending.json").write_text(json.dumps(dict(state="UNFUNDED")))
        self.launch(time.time() - 60)
        a, runner = self.call()
        self.assertEqual(runner.calls, 0, "a Codex call ran after the run ended")
        self.assertFalse(a.ok)

    def test_an_ending_run_starts_no_codex_call(self):
        (self.run / "proof" / "ending.json").write_text("{}")
        a, runner = self.call()
        self.assertEqual(runner.calls, 0)
        self.assertIn("DrainRefused", a.detail)

    def test_a_codex_call_that_would_outlive_the_deadline_is_refused(self):
        self.launch(time.time() + 300 + 90 - 20)
        a, runner = self.call(timeout=300)
        self.assertEqual(runner.calls, 0)
        self.assertIn("deadline", a.detail)

    def test_a_caller_outside_the_code_root_is_refused(self):
        other = tempfile.mkdtemp(dir=str(self.d))
        os.environ["BROTHER_CODE_ROOT"] = other
        a, runner = self.call()
        self.assertEqual(runner.calls, 0)
        self.assertIn(os.path.realpath(other), a.detail)

    def test_an_unreadable_launch_record_is_refused(self):
        (F.P.launch_dir(self.run) / "launch.json").write_text("{torn")
        a, runner = self.call()
        self.assertEqual(runner.calls, 0)
        self.assertIn("launch record", a.detail)

    def test_admission_waits_for_the_ledger_lock_the_ending_holds(self):
        """mark_ending writes the marker while holding the Claude ledger lock: a call admitted under that lock is
        either registered before the marker or refused after it, never admitted in between."""
        import threading
        out = {}
        with F.P.ledger_lock(os.environ["BROTHER_CLAUDE_CALLS_LEDGER"]):
            worker = threading.Thread(target=lambda: out.update(result=self.call()))
            worker.start()
            time.sleep(0.5)
            (self.run / "proof" / "ending.json").write_text("{}")
        worker.join(60)
        a, runner = out["result"]
        self.assertEqual(runner.calls, 0, "a Codex call was admitted while the ending held the ledger lock")
        self.assertIn("DrainRefused", a.detail)

    def test_control_a_call_that_ends_before_the_deadline_runs_once(self):
        self.launch(time.time() + 300 + 90 + 60)
        a, runner = self.call(timeout=300)
        self.assertEqual(runner.calls, 1)
        self.assertTrue(a.ok, a.detail)

    def test_admission_writes_no_ledger_row(self):
        self.call()
        path = Path(os.environ["BROTHER_CLAUDE_CALLS_LEDGER"])
        self.assertFalse(path.exists() and path.read_text().strip(), "a transport with no cost wrote a Claude row")


class ACodexCallOutsideAProofRuns(Codex):
    proof = False

    def test_control_outside_a_proof_phase_nothing_is_refused(self):
        a, runner = self.call()
        self.assertEqual(runner.calls, 1)
        self.assertTrue(a.ok, a.detail)


if __name__ == "__main__":
    unittest.main(verbosity=1)

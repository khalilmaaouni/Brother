#!/usr/bin/env python3
"""The intake proves every model answers through the program the run will use, or the run does not start (2026-09-30).

THE INCIDENT. The intake said READY because the pinned claude existed and was executable; it was 2.1.251, which did
not know claude-opus-5-5, and 78 calls and 7 sub units were spent before anyone saw it. The mutations that must turn
this suite red, each against its own assertion:

  M_EXECUTABLE_IS_PROOF  the proof is replaced by "the program is an executable file"
  M_DROP_FLAGS           the proof builds its own bare command line instead of the production invocation
  M_ACCEPT_ERROR_BODY    an answer that merely contains 12 (an error body) passes
  M_STALE_CACHE          a cached proof is reused after the program behind it changed
  M_UNBOUNDED_PROBE      probes run past the intake's explicit proof allowance

No real program answers a model here: calls go to a fake runner, and the only real processes are fake `--version`
scripts written into a temp directory.
Run from the repository root: python3 -B scripts/loop/test_model_reachability.py
"""
import json, os, shutil, subprocess, sys, tempfile, time, unittest

LOOP = os.path.dirname(os.path.abspath(__file__))
sys.path[:0] = [LOOP, os.path.dirname(os.path.dirname(LOOP))]

import breaker as BR  # noqa: E402
import loop_intake as LI  # noqa: E402
import model_call as MC  # noqa: E402
import model_reachability as MR  # noqa: E402
from test_config_dispatch import FakeAdmission, INCIDENT  # noqa: E402

REG = {n: {"id": i, "transport": t, "privacy": "private" if t == "claude" else "public", "quality": {("decide" if n == "jev" else "build"): 9},
           "kinds": {"decide"} if n == "jev" else {"build"}, "cost": 1.0}
       for n, i, t in (("opus55", "claude-opus-5-5", "claude"), ("sonnet", "claude-sonnet-5", "claude"),
                       ("haiku", "claude-haiku-4-5", "claude"), ("jev", "typesafe/jev-1.13", "bridge"))}


def answer(text, model="claude-opus-5-5", is_error=False):
    return json.dumps({"result": text, "is_error": is_error, "total_cost_usd": 0.01,
                       "modelUsage": {model: {"inputTokens": 3}}})


class Reach(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="reach-")
        self.addCleanup(shutil.rmtree, self.tmp, True)
        keys = ("BROTHER_PROOF_CACHE", "BROTHER_OR_STATE_ROOT", "BROTHER_CLAUDE_CALLS_LEDGER", "BROTHER_BRIDGE_CALLS_LEDGER",
                "BROTHER_PROGRAM_RECORD", "BROTHER_PROOF_ALLOWANCE_USD", "BROTHER_CLAUDE_BIN", "BROTHER_TRANSPORTS")
        saved = {k: os.environ.get(k) for k in keys}
        self.addCleanup(lambda: [os.environ.pop(k, None) if v is None else os.environ.__setitem__(k, v) for k, v in saved.items()])
        for k in keys:
            os.environ.pop(k, None)
        os.environ.update(BROTHER_PROOF_CACHE=os.path.join(self.tmp, "proofs.json"),
                          BROTHER_OR_STATE_ROOT=os.path.join(self.tmp, "state"),
                          BROTHER_CLAUDE_CALLS_LEDGER=os.path.join(self.tmp, "calls.jsonl"),
                          BROTHER_BRIDGE_CALLS_LEDGER=os.path.join(self.tmp, "bridge.jsonl"),
                          BROTHER_PROGRAM_RECORD=os.path.join(self.tmp, "programs.json"))
        adm = MC.admission
        MC.admission = FakeAdmission()
        self.addCleanup(setattr, MC, "admission", adm)
        self.calls = []
        self.old = self.program("claude-old", "2.1.251")
        self.new = self.program("claude-new", "2.1.284")

    def program(self, name, version):
        """A fake program that answers only `--version`: an executable file, never a model."""
        p = os.path.join(self.tmp, name)
        with open(p, "w") as fh:
            fh.write("#!/bin/sh\necho '%s (Claude Code)'\n" % version)
        os.chmod(p, 0o755)
        return p

    def runner(self, knows=("claude-opus-5-5", "claude-sonnet-5", "claude-haiku-4-5"), text="12", by=None):
        """Answers like claude: the program at argv[0] knows the models in `knows`, and says unrecognized_model else."""
        def run(argv, stdin, timeout):
            self.calls.append(list(argv))
            model = argv[argv.index("--model") + 1] if "--model" in argv else None
            if argv[0] == self.old and model == "claude-opus-5-5":
                return {"returncode": 1, "stdout": "", "stderr": INCIDENT}
            if model not in knows:
                return {"returncode": 1, "stdout": "", "stderr": INCIDENT}
            return {"returncode": 0, "stdout": answer(text, by or model), "stderr": ""}
        return run


class TheIntakeRefuses(Reach):
    def prepare(self, program, runner):
        base = {"does": "x", "when": "inside", "kind": "build", "content": "private", "must_be_chosen": False, "default": "opus55"}
        roles = {"worker": dict(base, setting="BROTHER_PIN_MODEL")}
        noon = __import__("datetime").datetime(2026, 1, 1, 12, 0)
        ok = lambda: ("OK", "fixture")
        probes = {k: ok for k in ("canary", "alive", "lease", "parity", "switch", "tree", "digest", "done", "salvage", "pool")}
        probes.update(headroom=lambda: 50.0, hold=lambda: False, now=lambda: noon,
                      reach=lambda plan: MR.require_proof(plan, REG, runner=runner, programs={"claude": program}))
        env = {"BROTHER_WORKER_MIX": "opus55:1", "BROTHER_BUILD_PLAN_MODEL": "off", "BROTHER_REPAIR_ADVISOR_MODEL": "off"}
        return LI.prepare({}, "18:00", 10.0, None, roles, REG, probes, env=env)[0]

    def test_an_old_executable_that_rejects_the_model_is_no_start(self):
        """M_EXECUTABLE_IS_PROOF: the old program is an executable file, and it does not know the model."""
        rec = self.prepare(self.old, self.runner())
        self.assertEqual(rec["verdict"], "NOT READY", "M_EXECUTABLE_IS_PROOF: an executable file read as a proof\n" + "\n".join(rec["lines"]))
        no_start = [l for l in rec["lines"] if "NO START" in l]
        self.assertEqual(len(no_start), 2, rec["lines"])   # one line per role that calls it: worker, and the worker mix arm
        for part in ("role=worker", "model=opus55", "program=" + self.old, "version=2.1.251", "unrecognized_model", "HINT: update Claude Code"):
            self.assertIn(part, no_start[0])

    def test_the_new_program_that_answers_is_ready_and_recorded(self):
        rec = self.prepare(self.new, self.runner())
        self.assertEqual(rec["verdict"], "READY", "\n".join(rec["lines"]))
        self.assertEqual(rec["programs"]["claude"]["version"], "2.1.284")
        self.assertEqual(len(rec["reach"]), 1)
        self.assertEqual(LI.reach_why(rec, resolve=lambda t: self.new), "")

    def test_the_record_refuses_a_start_once_the_program_moves(self):
        rec = self.prepare(self.new, self.runner())
        self.assertIn("production would now run", LI.reach_why(rec, resolve=lambda t: self.old))
        with open(self.new, "a") as fh:
            fh.write("# updated\n")
        self.assertIn("changed since it proved", LI.reach_why(rec, resolve=lambda t: self.new))
        self.assertIn("no reachability proof", LI.reach_why({"verdict": "READY"}))
        self.assertIn("no reachability proof", LI.reach_why({"verdict": "READY", "reach": []}))   # an empty proof is no proof


class TheProofIsTheProductionCall(Reach):
    def test_the_proof_sends_the_production_flags(self):
        """M_DROP_FLAGS: the proof's command line is the one a production call sends, apart from the prompt."""
        MR.prove("opus55", REG, program=self.new, runner=self.runner())
        MC.call_one("opus55", "any production brief", "build", "private", reg=REG, runner=self.runner(), program=self.new)
        proof, prod = self.calls
        self.assertEqual(proof, prod, "M_DROP_FLAGS: the proof and production command lines differ")
        self.assertIn("--effort", proof); self.assertIn("--tools", proof)

    def test_an_error_body_that_contains_12_never_passes(self):
        """M_ACCEPT_ERROR_BODY: an answer is the proof's answer only when it IS 12."""
        p = MR.prove("opus55", REG, program=self.new, runner=self.runner(text="API Error: overloaded, retry in 12 s"))
        self.assertEqual(p["status"], "FAIL", "M_ACCEPT_ERROR_BODY: an error body containing 12 passed: %s" % p)
        p = MR.prove("sonnet", REG, program=self.new,
                     runner=lambda a, s, t: {"returncode": 0, "stdout": answer("12", "claude-sonnet-5", is_error=True), "stderr": ""})
        self.assertEqual(p["status"], "FAIL", p)

    def test_a_substituted_model_fails(self):
        p = MR.prove("opus55", REG, program=self.new, runner=self.runner(by="claude-sonnet-5"))
        self.assertEqual(p["status"], "FAIL"); self.assertIn("another model answered", p["cause"])

    def test_jev_gets_a_typed_question_never_prose(self):
        seen = []
        def run(argv, stdin, t):
            seen.append(argv)
            return {"returncode": 0, "stdout": '{"ok": true}', "stderr": "[usage] prompt=3 completion=2 model=typesafe/jev-1.13"}
        p = MR.prove("jev", REG, runner=run)
        self.assertEqual(p["status"], "OK", p)
        self.assertIn("questions", json.loads(seen[0][-1]))

    def test_a_jev_error_body_fails(self):
        bad = MR.prove("jev", REG, runner=lambda a, s, t: {"returncode": 0, "stdout": "error 12", "stderr": ""})
        self.assertEqual(bad["status"], "FAIL", bad)

    def test_a_timeout_or_an_unreadable_version_is_no_data(self):
        p = MR.prove("opus55", REG, program=self.new,
                     runner=lambda a, s, t: (_ for _ in ()).throw(subprocess.TimeoutExpired("claude", 1)))
        self.assertEqual(p["status"], "NO-DATA", p)
        p = MR.prove("opus55", REG, program=os.path.join(self.tmp, "missing"), runner=self.runner())
        self.assertEqual(p["status"], "NO-DATA", p)

    def test_a_fresh_proof_closes_the_configuration_breaker(self):
        key = BR.config_key("claude", "claude-opus-5-5")
        BR.record(key, "CONFIG", "trip", INCIDENT, now=time.time() - 5)
        self.assertEqual(BR.admit([key], "x")[0], "OPEN")
        self.assertEqual(MR.prove("opus55", REG, program=self.new, runner=self.runner())["status"], "OK")
        self.assertEqual(BR.admit([key], "x")[0], "CLOSED")


class TheCacheAndTheBudget(Reach):
    def test_a_cached_proof_is_reused_until_its_program_changes(self):
        """M_STALE_CACHE: a program replaced behind the same path is a new question."""
        MR.prove("opus55", REG, program=self.new, runner=self.runner())
        MR.prove("opus55", REG, program=self.new, runner=self.runner())
        self.assertEqual(len(self.calls), 1, "a fresh cached proof was not reused")
        with open(self.new, "a") as fh:
            fh.write("# the program was replaced\n" * 3)
        p = MR.prove("opus55", REG, program=self.new, runner=self.runner())
        self.assertEqual(len(self.calls), 2, "M_STALE_CACHE: a proof of the old program was reused for the new one")
        self.assertFalse(p["cached"])

    def test_a_cached_proof_expires(self):
        MR.prove("opus55", REG, program=self.new, runner=self.runner())
        MR.prove("opus55", REG, program=self.new, runner=self.runner(), now=lambda: time.time() + MR.TTL_S + 1)
        self.assertEqual(len(self.calls), 2)

    def test_probes_stop_at_the_allowance(self):
        """M_UNBOUNDED_PROBE: 0.25 USD takes two Claude probes at 0.10 each; the third is NO-DATA and never sent."""
        budget = MR.Budget(usd=0.25)
        plan = [("worker", "opus55"), ("checker", "sonnet"), ("finisher", "haiku")]
        proofs = MR.require_proof(plan, REG, budget=budget, runner=self.runner(), programs={"claude": self.new})
        self.assertEqual(len(self.calls), 2, "M_UNBOUNDED_PROBE: a probe ran past the allowance")
        self.assertEqual([p["status"] for p in proofs], ["OK", "OK", "NO-DATA"])
        self.assertIn("allowance is spent", proofs[2]["cause"])

    def test_one_proof_per_distinct_model(self):
        MR.require_proof([("worker", "opus55"), ("adversary", "opus55")], REG, runner=self.runner(), programs={"claude": self.new})
        self.assertEqual(len(self.calls), 1)


if __name__ == "__main__":
    unittest.main()

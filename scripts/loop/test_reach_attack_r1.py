#!/usr/bin/env python3
"""Every finding of attack R1 on 62ca37807 (2026-10-01), each as the fixture that broke it and the mutation that must
turn it red again.

  M_CACHE_IGNORES_BREAKER     F1  a cached proof answers OK while the configuration breaker is open
  M_OK_WITH_OPEN_BREAKER      F1  a proof returns OK although it could not close the breaker
  M_STARTABLE_IGNORES_BREAKER F1  startable() starts a record whose model's breaker is open
  M_CLOSE_NOT_A_FACT          F2  a closing proof does not re-seat a CONFIG_WAIT sub unit
  M_RECORD_PROGRAMS_ONLY      F2  the program record is rewritten only when a program changes, never the proof set
  M_READ_ANSWER_TEXT          F3  CONFIG is read from the answer or the echoed prompt, not the error channels
  A_config_on_success         F4  (the attacker's) a successful Claude answer can trip CONFIG
  D_learn_config              F4  (the attacker's) a CONFIG record is learned as model quality
  M_NO_ADMIT_AFTER_SLOT       F6  or_fanout does not hand dispatch its admission for after the slot wait
  M_DISPATCH_IGNORES_ADMIT    F6  dispatch() does not ask the admission after the slot wait
  M_MODEL_CALL_NO_ADMIT       F6  model_call's bridge path does not hand dispatch its admission
  M_MIXED_ROUND_GRADED        F7  a round of CONFIG plus drain or budget refusals is graded and spent
  M_FOLLOW_UNPROVEN           c   call() follows the chain to a model the intake did not prove
  M_MISSING_WORDINGS          F8  the five missed wordings read as MALFORMED again
  M_TOP_MTIME                 F5  a lane's age is its top directory's mtime, not its newest file's
  M_IGNORED_CLEAN             F5  a gitignored output does not count against "clean"

No model is called. Run from the repository root: python3 -B scripts/loop/test_reach_attack_r1.py
"""
import inspect, json, os, shutil, subprocess, sys, tempfile, time, types, unittest
from unittest import mock

LOOP = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(os.path.dirname(LOOP))
sys.path[:0] = [LOOP, ROOT]

import breaker as BR  # noqa: E402
import fanout_verdict as FV  # noqa: E402
import loop_intake as LI  # noqa: E402
import model_call as MC  # noqa: E402
import model_reachability as MR  # noqa: E402
import scratch_prune as SP  # noqa: E402
from test_config_dispatch import FakeAdmission, INCIDENT  # noqa: E402
from test_model_reachability import REG, answer  # noqa: E402
import test_config_recovery as TCR  # noqa: E402

# THE REAL 2.1.251 ANSWER, captured 2026-10-01 (evidence/model-reach-2026-09-30/incident-*.txt): the error is on
# stderr; the result record is is_error at api_error_status 400 and says it in prose.
REAL_STDERR = '[claude-code:unrecognized_model] {"model":"claude-opus-5-5","query_source":"sdk"}'
REAL_DOC = {"is_error": True, "subtype": "success", "api_error_status": 400, "terminal_reason": "api_error",
            "total_cost_usd": 0, "modelUsage": {}, "type": "result",
            "result": "API Error: 400 Claude Code 2.1.251 does not support this model; version 2.1.280 or newer is required."}


class Env(unittest.TestCase):
    KEYS = ("BROTHER_PROOF_CACHE", "BROTHER_OR_STATE_ROOT", "BROTHER_CLAUDE_CALLS_LEDGER", "BROTHER_BRIDGE_CALLS_LEDGER",
            "BROTHER_PROGRAM_RECORD", "BROTHER_CLAUDE_BIN", "BROTHER_CODEX_BIN", "BROTHER_TRANSPORTS", "BROTHER_WORKSPACE_ROOT")

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="r1-")
        self.addCleanup(shutil.rmtree, self.tmp, True)
        saved = {k: os.environ.get(k) for k in self.KEYS}
        self.addCleanup(lambda: [os.environ.pop(k, None) if v is None else os.environ.__setitem__(k, v) for k, v in saved.items()])
        for k in self.KEYS:
            os.environ.pop(k, None)
        os.environ.update(BROTHER_PROOF_CACHE=os.path.join(self.tmp, "proofs.json"), BROTHER_OR_STATE_ROOT=os.path.join(self.tmp, "state"),
                          BROTHER_CLAUDE_CALLS_LEDGER=os.path.join(self.tmp, "calls.jsonl"),
                          BROTHER_BRIDGE_CALLS_LEDGER=os.path.join(self.tmp, "bridge.jsonl"))
        adm = MC.admission
        MC.admission = FakeAdmission()
        self.addCleanup(setattr, MC, "admission", adm)
        self.calls = []
        self.prog = os.path.join(self.tmp, "claude")
        with open(self.prog, "w") as fh:
            fh.write("#!/bin/sh\necho '2.1.284 (Claude Code)'\n")
        os.chmod(self.prog, 0o755)
        # a call that names no program resolves this fixture (FX-31.5: the adapters build a line only for an
        # executable program), so the suite passes on a machine with no Claude Code installed
        self.addCleanup(setattr, MC, "CLAUDE", MC.CLAUDE)
        MC.CLAUDE = self.prog

    def runner(self, rc=0, out=None, err=""):
        def run(argv, stdin, timeout):
            self.calls.append(argv)
            model = argv[argv.index("--model") + 1] if "--model" in argv else "?"
            return {"returncode": rc, "stdout": answer("12", model) if out is None else out, "stderr": err}
        return run

    key = BR.config_key("claude", "claude-opus-5-5")


class F1ACachedProofIsNotReadyWhileHeld(Env):
    def test_the_attackers_sequence(self):
        """M_CACHE_IGNORES_BREAKER: proof OK, a production call trips CONFIG, a second proof inside the TTL."""
        self.assertEqual(MR.prove("opus55", REG, program=self.prog, runner=self.runner())["status"], "OK")
        prod = MC.call_one("opus55", "brief", "build", "private", reg=REG, program=self.prog, runner=self.runner(rc=1, out="", err=REAL_STDERR))
        self.assertEqual(prod.failure, "CONFIG")
        p2 = MR.prove("opus55", REG, program=self.prog, runner=self.runner())
        self.assertFalse(p2["cached"], "M_CACHE_IGNORES_BREAKER: a cached proof answered while the breaker was open")
        self.assertEqual(len(self.calls), 3, "the second proof was not a fresh call")
        self.assertEqual(MC.config_admit("claude", "claude-opus-5-5"), "", "the fresh proof did not close the breaker")

    def test_a_proof_that_cannot_close_the_breaker_is_not_ok(self):
        """M_OK_WITH_OPEN_BREAKER: the breaker opened AFTER this proof's time; the proof cannot close it."""
        BR.record(self.key, "CONFIG", "trip", INCIDENT, now=time.time() + 3600)
        p = MR.prove("opus55", REG, program=self.prog, runner=self.runner())
        self.assertEqual(p["status"], "NO-DATA", "M_OK_WITH_OPEN_BREAKER: OK while the breaker stayed open: %s" % p)
        self.assertIn("stays open", p["cause"])

    def test_startable_refuses_while_a_proven_models_breaker_is_open(self):
        """M_STARTABLE_IGNORES_BREAKER: a READY record, its program unchanged, its model's breaker open."""
        p = MR.prove("opus55", REG, program=self.prog, runner=self.runner())
        rec = {"reach": [p["binding"]], "programs": {}}
        self.assertEqual(LI.reach_why(rec), "")
        BR.record(self.key, "CONFIG", "trip", INCIDENT)
        self.assertIn("configuration breaker holds", LI.reach_why(rec), "M_STARTABLE_IGNORES_BREAKER")


class F2CloseIsAFact(unittest.TestCase):
    setUp = TCR.ThePool.setUp
    run_pool, started, plant_exhausted, spec, judge, fund = (TCR.ThePool.run_pool, TCR.ThePool.started,
                                                             TCR.ThePool.plant_exhausted, TCR.ThePool.spec,
                                                             TCR.ThePool.judge, TCR.ThePool.fund)
    write_fixtures = TCR.ThePool.write_fixtures   # run_pool calls it since ACC3.b (e24e8be79)

    def test_a_closing_proof_reseats_a_config_wait_unit_with_the_same_program(self):
        """M_CLOSE_NOT_A_FACT: bridge 'No endpoints found' cleared; no program changed; the proof closed the breaker."""
        state = os.path.join(self.home, "or-state")
        env = {"BROTHER_OR_STATE_ROOT": state}
        self.plant_exhausted("D3.1", word="CONFIG_WAIT", age=3600); self.spec("D3", age=7200); self.judge("unit_runner.py", age=7200)
        with mock.patch.dict(os.environ, env):
            k = BR.config_key("bridge", "deepseek/deepseek-v4.1-flash")
            BR.record(k, "CONFIG", "trip", "HTTP 404 from OpenRouter: No endpoints found for x", now=time.time() - 3500)
        out = self.run_pool([TCR.GUARD.unit("D3", ["D3.1"])], scores={"D3.1": 10}, env=env)
        self.assertNotIn("D3.1", self.started(out), out)
        with mock.patch.dict(os.environ, env):
            self.assertTrue(BR.close_config(k, proved_at=time.time())[0])
        out = self.run_pool([TCR.GUARD.unit("D3", ["D3.1"])], scores={"D3.1": 10}, env=env)
        self.assertIn("D3.1", self.started(out), "M_CLOSE_NOT_A_FACT: the closing proof did not re-seat it\n" + out)


class F2RecordFollowsTheProofSet(unittest.TestCase):
    def test_the_record_is_rewritten_when_the_proof_set_changes(self):
        """M_RECORD_PROGRAMS_ONLY"""
        d = tempfile.mkdtemp(prefix="r1-rec-"); self.addCleanup(shutil.rmtree, d, True)
        path = os.path.join(d, "r.json")
        progs = {"claude": {"path": "/c", "version": "2.1.284", "fingerprint": "f"}, "bridge": {"path": "/or_ask.py", "version": "or_ask-1", "fingerprint": "g"}}
        self.assertTrue(MR.write_record(progs, path, proven=["claude|/c|2.1.284|claude-opus-5-5"]))
        self.assertFalse(MR.write_record(progs, path, proven=["claude|/c|2.1.284|claude-opus-5-5"]))
        self.assertTrue(MR.write_record(progs, path, proven=["claude|/c|2.1.284|claude-opus-5-5", "bridge|/or_ask.py|or_ask-1|deepseek/x"]),
                        "M_RECORD_PROGRAMS_ONLY: a new proof set left the record, and the pool's fact, unchanged")

    def test_bridge_programs_are_recorded(self):
        env = Env("run"); env.setUp(); self.addCleanup(env.doCleanups)
        run = lambda a, s, t: {"returncode": 0, "stdout": '{"ok": true}', "stderr": "[usage] prompt=1 completion=1 model=typesafe/jev-1.13"}
        base = {"does": "x", "when": "inside", "kind": "decide", "content": "public", "must_be_chosen": False, "default": "jev"}
        ok = lambda: ("OK", "fixture")
        probes = {k: ok for k in ("canary", "alive", "lease", "parity", "switch", "tree", "digest", "done", "salvage", "pool")}
        import datetime
        probes.update(headroom=lambda: 50.0, hold=lambda: False, now=lambda: datetime.datetime(2026, 1, 1, 12, 0),
                      reach=lambda plan: MR.require_proof(plan, REG, runner=run))
        rec = LI.prepare({}, "18:00", 10.0, None, {"worker": dict(base, setting="BROTHER_PIN_MODEL")}, REG, probes,
                         env={"BROTHER_WORKER_MIX": "jev:1", "BROTHER_BUILD_PLAN_MODEL": "off", "BROTHER_REPAIR_ADVISOR_MODEL": "off"})[0]
        self.assertIn("bridge", rec["programs"], rec["lines"])
        self.assertTrue(any(k.startswith("bridge|") for k in rec["proven"]))


class F3OnlyErrorChannels(unittest.TestCase):
    def test_the_real_incident_is_config_from_stderr(self):
        self.assertEqual(BR.classify_cli("claude", 1, json.dumps(REAL_DOC), REAL_STDERR, REAL_DOC), "CONFIG")

    def test_a_codex_prompt_echo_is_not_config(self):
        """M_READ_ANSWER_TEXT: codex exec echoes the prompt; a brief quoting breaker.py must not trip its own model."""
        prompt = "read breaker.py: unrecognized_model and model_not_found are CONFIG words"
        self.assertEqual(BR.classify_cli("codex", 1, "", "user\n%s\nERROR: stream disconnected" % prompt, None, prompt=prompt), "MALFORMED")
        self.assertEqual(BR.classify_cli("codex", 1, "the answer says model_not_found", "ERROR: stream disconnected", None), "MALFORMED")

    def test_an_is_error_result_that_mentions_a_phrase_is_not_config(self):
        doc = {"is_error": True, "result": "the tool printed model_not_found while reviewing", "subtype": "error_during_execution"}
        self.assertEqual(BR.classify_cli("claude", 1, json.dumps(doc), "", doc), "MALFORMED")
        doc2 = dict(doc, error={"type": "not_found_error", "message": "model: claude-x"})
        self.assertEqual(BR.classify_cli("claude", 1, json.dumps(doc2), "", doc2), "CONFIG")

    def test_a_successful_answer_never_trips_config(self):
        """A_config_on_success (the attacker's): a successful Claude result with the phrase on stderr noise."""
        ok = {"is_error": False, "result": "12"}
        self.assertEqual(BR.classify_cli("claude", 0, json.dumps(ok), REAL_STDERR, ok), "ANSWERED")

    def test_a_bridge_answer_that_quotes_the_words_is_not_config(self):
        self.assertEqual(BR.classify_bridge(1, "No endpoints found for x, the model said", "HTTP 502 upstream"), "OVERLOAD")


class F4ConfigIsNotLearned(unittest.TestCase):
    def test_a_config_record_is_never_learned(self):
        """D_learn_config (the attacker's)"""
        from plugin.runtime.brother.core import or_fanout
        with mock.patch.object(or_fanout.dream_record, "record_outcome") as ro:
            rec = {"id": "a", "ok": False, "config": True, "error": "CONFIG_WAIT: held", "actual_model": "deepseek/x"}
            or_fanout._record_result(("run", "event"), rec)
        self.assertFalse(ro.called, "D_learn_config: a configuration fault was learned as model quality")
        self.assertFalse(rec["learned"])


class F6AdmitAfterTheSlot(Env):
    def test_or_fanout_hands_dispatch_its_admission(self):
        """M_NO_ADMIT_AFTER_SLOT: the breaker trips while the job waits for its slot; dispatch must be able to ask."""
        from plugin.runtime.brother.core import or_fanout, scratch_root, openrouter_dispatch as D
        os.makedirs(scratch_root.SCRATCH_ROOT, exist_ok=True)
        ws = scratch_root.mkdtemp("r1-f6"); self.addCleanup(shutil.rmtree, ws, True)
        os.environ["BROTHER_WORKSPACE_ROOT"] = os.path.realpath(scratch_root.SCRATCH_ROOT)
        sent = []
        def dispatch(**kw):
            BR.record(BR.config_key("bridge", kw["requested_model"]), "CONFIG", "other", "HTTP 404: No endpoints found for x")
            held = kw.get("admit") and kw["admit"]()
            if held:
                raise D.ConfigHeld(held)
            sent.append(1)
            return types.SimpleNamespace(stdout="answer", stderr="", returncode=0), kw["requested_model"]
        rec = or_fanout.run_job({"id": "a", "model": "deepseek", "prompt": "p", "sensitivity": "public", "out": os.path.join(ws, "a.md")},
                                300, 4, dispatch=dispatch)
        self.assertEqual(sent, [], "M_NO_ADMIT_AFTER_SLOT: a queued bridge call was sent after the trip")
        self.assertTrue(rec.get("config"), rec)

    def test_dispatch_asks_the_admission_before_reserving_or_sending(self):
        """M_DISPATCH_IGNORES_ADMIT"""
        from plugin.runtime.brother.core import openrouter_dispatch as D
        from plugin.runtime.brother.core.or_dispatch_cli import BRIDGE_PATH
        argv = [sys.executable, BRIDGE_PATH, "--model", "deepseek", "--only-model", "--effort", "xhigh", "--max", "4000",
                "--timeout", "300", "--", "p"]
        with mock.patch.object(D, "run_strict", side_effect=AssertionError("M_DISPATCH_IGNORES_ADMIT: sent")):
            with self.assertRaises(D.ConfigHeld):
                D.dispatch(argv, "deepseek/deepseek-v4.1-flash", estimated_cost=0.01, holder_id="t", timeout_seconds=300,
                           max_tokens=4000, state_root=os.path.join(self.tmp, "disp"), model_alias="deepseek",
                           kind="build", admit=lambda: "held by the test")

    def test_model_call_bridge_path_hands_dispatch_its_admission(self):
        """M_MODEL_CALL_NO_ADMIT"""
        from plugin.runtime.brother.core import openrouter_dispatch as D
        reg = {"d": {"id": "x/d", "transport": "bridge", "privacy": "public", "quality": {"build": 1}, "kinds": {"build"}, "cost": 1.0}}
        sent = []
        def dispatch(argv, model, **kw):
            BR.record(BR.config_key("bridge", "x/d"), "CONFIG", "other", "No endpoints found for x/d")
            held = kw.get("admit") and kw["admit"]()
            if held:
                raise D.ConfigHeld(held)
            sent.append(1)
            return types.SimpleNamespace(stdout="12", stderr="", returncode=0), model
        with mock.patch.object(D, "dispatch", side_effect=dispatch):
            a = MC.call_one("d", "p", "build", "public", reg=reg)
        self.assertEqual(sent, [], "M_MODEL_CALL_NO_ADMIT: sent after the trip")
        self.assertEqual(a.failure, "CONFIG", a.detail)

    def test_model_call_bridge_path_names_no_ceiling(self):
        """D2.6 REQ-FAN-5 (docs/plan/specs/D2.md): the bridge path hands dispatch no daily_cap or max_slots, and every
        keyword it does pass is one the real dispatch signature accepts (a restored max_slots=sys.maxsize is a
        TypeError there, which the spy raises before recording anything)."""
        from plugin.runtime.brother.core import openrouter_dispatch as D
        reg = {"d": {"id": "x/d", "transport": "bridge", "privacy": "public", "quality": {"build": 1}, "kinds": {"build"}, "cost": 1.0}}
        real, seen = D.dispatch, []
        def dispatch(argv, model, **kw):
            inspect.signature(real).bind(argv, model, **kw)
            seen.append(kw)
            return types.SimpleNamespace(stdout="12", stderr="", returncode=0), model
        with mock.patch.object(D, "dispatch", side_effect=dispatch):
            a = MC.call_one("d", "p", "build", "public", reg=reg)
        self.assertEqual(len(seen), 1, a.detail)
        self.assertNotIn("max_slots", seen[0])
        self.assertNotIn("daily_cap", seen[0])


class F7MixedRound(unittest.TestCase):
    def test_config_with_drain_and_budget_refusals_is_config_wait(self):
        """M_MIXED_ROUND_GRADED"""
        d = tempfile.mkdtemp(prefix="r1-f7-"); self.addCleanup(shutil.rmtree, d, True)
        p = os.path.join(d, "results.json")
        with open(p, "w") as fh:
            json.dump([{"id": "a", "ok": False, "config": True, "error": "CONFIG_WAIT: held"},
                       {"id": "b", "ok": False, "error": "DrainRefused: ending"},
                       {"id": "c", "ok": False, "error": "BudgetExceeded: cap"}], fh)
        self.assertIs(FV.all_config_wait(p), True, "M_MIXED_ROUND_GRADED: the mixed round was graded and spent")


class CUnprovenFallback(Env):
    def test_call_never_follows_the_chain_to_an_unproven_model(self):
        """M_FOLLOW_UNPROVEN: opus55 proven, sonnet not; the chain would fall over to sonnet."""
        rec = os.path.join(self.tmp, "programs.json")
        with open(rec, "w") as fh:
            json.dump({"programs": {}, "proven": ["claude|%s|2.1.284|claude-opus-5-5" % self.prog]}, fh)
        os.environ["BROTHER_PROGRAM_RECORD"] = rec
        reg = {k: REG[k] for k in ("opus55", "sonnet")}
        win, tried = MC.call("p", "build", "private", reg=reg, record=False, runner=self.runner(rc=1, out="", err="boom"))
        sent = [a[a.index("--model") + 1] for a in self.calls]
        self.assertNotIn("claude-sonnet-5", sent, "M_FOLLOW_UNPROVEN: an unproven fallback was sent")
        self.assertIn("claude-opus-5-5", sent)
        self.assertTrue(any(t.failure == "UNPROVEN" and "not proven" in t.detail for t in tried), [t.detail for t in tried])


class F8Wordings(unittest.TestCase):
    def test_the_missed_wordings_are_config(self):
        """M_MISSING_WORDINGS"""
        for text in ("invalid_model", "unknown model 'x'", "model_not_available", "The requested model is not available",
                     "The model 'claude opus' does not exist"):
            self.assertEqual(BR.classify_bridge(1, "", text), "CONFIG", "M_MISSING_WORDINGS: %r" % text)
        self.assertEqual(BR.classify_bridge(1, "", "Error: 404 Not Found, the file does not exist"), "MALFORMED")


class F5PrunerKeepsLiveWork(unittest.TestCase):
    def setUp(self):
        self.d = tempfile.mkdtemp(prefix="r1-prune-"); self.addCleanup(shutil.rmtree, self.d, True)
        self.repo = os.path.join(self.d, "repo"); self.root = os.path.join(self.d, "scratch")
        os.makedirs(self.root)
        env = dict(os.environ, GIT_AUTHOR_NAME="t", GIT_AUTHOR_EMAIL="t@t", GIT_COMMITTER_NAME="t", GIT_COMMITTER_EMAIL="t@t")
        for k in [k for k in env if k.startswith("GIT_") and k not in ("GIT_AUTHOR_NAME", "GIT_AUTHOR_EMAIL", "GIT_COMMITTER_NAME", "GIT_COMMITTER_EMAIL")]:
            env.pop(k)
        g = lambda *a, cwd=None: subprocess.run(["git"] + list(a), cwd=cwd or self.repo, env=env, capture_output=True, check=True)
        os.makedirs(self.repo); g("init", "-q")
        with open(os.path.join(self.repo, ".gitignore"), "w") as fh: fh.write("*.out\n")
        with open(os.path.join(self.repo, "a.py"), "w") as fh: fh.write("x = 1\n")
        g("add", "."); g("commit", "-qm", "base")
        self.wt = os.path.join(self.root, "wt-idle"); g("worktree", "add", "-q", "--detach", self.wt)

    def age_all(self, seconds):
        stamp = time.time() - seconds
        for top, dirs, files in os.walk(self.wt):
            for n in dirs + files:
                os.utime(os.path.join(top, n), (stamp, stamp), follow_symlinks=False)
        os.utime(self.wt, (stamp, stamp))

    def prune(self):
        return SP.prune(self.root, self.repo, max_age=24 * 3600, handle_reader=lambda p: 0)[0]

    def test_a_file_edited_seconds_ago_keeps_the_worktree(self):
        """M_TOP_MTIME: every entry 72 h old except a.py, edited now; the top directory itself reads 72 h."""
        self.age_all(72 * 3600)
        with open(os.path.join(self.wt, "a.py"), "a") as fh: fh.write("")
        os.utime(os.path.join(self.wt, "a.py"), None)
        stamp = time.time() - 72 * 3600; os.utime(self.wt, (stamp, stamp))
        rows = self.prune()
        self.assertTrue(os.path.isdir(self.wt), "M_TOP_MTIME: removed %s" % rows)
        self.assertEqual(rows[0][1], SP.KEEP_YOUNG, rows)

    def test_a_gitignored_output_keeps_an_old_clean_worktree(self):
        """M_IGNORED_CLEAN: everything 72 h old, tracked files clean, one ignored agent output."""
        with open(os.path.join(self.wt, "agent.out"), "w") as fh: fh.write("an agent's output\n")
        self.age_all(72 * 3600)
        rows = self.prune()
        self.assertTrue(os.path.isfile(os.path.join(self.wt, "agent.out")), "M_IGNORED_CLEAN: removed %s" % rows)
        self.assertEqual(rows[0][1], SP.KEEP_DIRTY, rows)

    def test_a_lone_python_cache_does_not_keep_an_abandoned_worktree(self):
        """M_CACHE_IS_WORK (attack R2-3): only a __pycache__ beside clean tracked files; still removed."""
        os.makedirs(os.path.join(self.wt, "__pycache__"))
        with open(os.path.join(self.wt, "__pycache__", "a.cpython-313.pyc"), "wb") as fh: fh.write(b"x")
        self.age_all(72 * 3600)
        rows = self.prune()
        self.assertEqual(rows[0][1], SP.REMOVED_WORKTREE, "M_CACHE_IS_WORK: %s" % rows)

    def test_regenerable_reads_only_cache_paths(self):
        self.assertTrue(SP.regenerable("?? __pycache__/"))
        self.assertTrue(SP.regenerable("!! pkg/sub/__pycache__/m.cpython-39.pyc"))
        self.assertFalse(SP.regenerable("!! agent.out"))
        self.assertFalse(SP.regenerable(" M a.py"))
        self.assertFalse(SP.regenerable(None))

    def test_a_truly_stale_clean_worktree_is_still_removed(self):
        self.age_all(72 * 3600)
        rows = self.prune()
        self.assertEqual(rows[0][1], SP.REMOVED_WORKTREE, rows)


class R3ClearNeverLeavesTheLane(unittest.TestCase):
    """Attack R3 H1: one fixture per guard of clear_regenerable, each with a sentinel that must survive."""
    def setUp(self):
        self.d = os.path.realpath(tempfile.mkdtemp(prefix="r3-clear-")); self.addCleanup(shutil.rmtree, self.d, True)
        self.lane = os.path.join(self.d, "lane"); os.makedirs(self.lane)

    def sentinel(self, *parts):
        p = os.path.join(*parts); os.makedirs(os.path.dirname(p), exist_ok=True)
        with open(p, "w") as fh: fh.write("keep\n")
        return p

    def runner(self, line):
        return lambda argv: (0, line + "\n")

    def test_a_symlinked_cache_is_never_followed(self):
        """Q_follow_symlink: __pycache__ is a symlink to real work INSIDE the lane (containment alone passes)."""
        s = self.sentinel(self.lane, "keep", "real.txt")
        os.symlink(os.path.join(self.lane, "keep"), os.path.join(self.lane, "__pycache__"))
        SP.clear_regenerable(self.lane, runner=self.runner("?? __pycache__"))
        self.assertTrue(os.path.isfile(s), "Q_follow_symlink: deleted through a symlink")

    def test_a_path_resolving_outside_the_lane_is_never_deleted(self):
        """S_no_containment: a status path that climbs out of the worktree (not a symlink itself)."""
        s = self.sentinel(self.d, "outside", "__pycache__", "x.pyc")
        SP.clear_regenerable(self.lane, runner=self.runner("?? ../outside/__pycache__/"))
        self.assertTrue(os.path.isfile(s), "S_no_containment: deleted outside the lane")

    def test_only_untracked_or_ignored_lines_are_cleared(self):
        """P_any_status_line: a TRACKED modified cache file is work, never cleared."""
        s = self.sentinel(self.lane, "__pycache__", "m.cpython-313.pyc")
        self.assertFalse(SP.regenerable(" M __pycache__/m.cpython-313.pyc"))
        SP.clear_regenerable(self.lane, runner=self.runner(" M __pycache__/m.cpython-313.pyc"))
        self.assertTrue(os.path.isfile(s), "P_any_status_line: a tracked line was cleared")

    def test_real_sources_under_a_cache_name_are_work(self):
        """L1: git reports '!! __pycache__/' for a directory that holds a real .py file."""
        s = self.sentinel(self.lane, "__pycache__", "real.py")
        self.assertFalse(SP.regenerable("!! __pycache__/", self.lane))
        SP.clear_regenerable(self.lane, runner=self.runner("!! __pycache__/"))
        self.assertTrue(os.path.isfile(s), "L1: real source deleted as a cache")

    def test_an_agent_arriving_between_looks_keeps_the_lane(self):
        """L3: the first look sees 0 handles, the recheck sees 1; the lane must be kept."""
        calls = []
        def handles(p):
            calls.append(p); return 0 if len(calls) == 1 else 1
        root = os.path.join(self.d, "scratch"); os.makedirs(os.path.join(root, "idle"))
        stamp = time.time() - 72 * 3600
        os.utime(os.path.join(root, "idle"), (stamp, stamp))
        rows = SP.prune(root, None, max_age=24 * 3600, handle_reader=handles, dirty_reader=lambda p: 0)[0]
        self.assertTrue(os.path.isdir(os.path.join(root, "idle")), "L3: removed after an agent arrived %s" % rows)


if __name__ == "__main__":
    unittest.main()

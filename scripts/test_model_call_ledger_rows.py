"""M2-1 (U7, objections 8, 12 and 13) and Codex check-in 2 finding 3: every paid call model_call makes is on a ledger
before provider contact, and every Claude START gets exactly one terminal row.

Entry point: model_call.call_one, with a real Claude call ledger in a scratch folder, the real shared admission pool
in a scratch state root and, for the proof cases, the real proof start fixture (test_proof_dispatch_accounting).
Claude cases inject a runner or point the CLI at a path that does not exist; bridge cases point model_call.BRIDGE at a
scratch or_ask.py. Nothing here reaches a real model, a real key or the network. One condition per case.

Run from the repository root: python3 -B scripts/test_model_call_ledger_rows.py
"""
import datetime, io, json, os, subprocess, sys, tempfile, textwrap, unittest
from contextlib import redirect_stderr
from pathlib import Path
from unittest import mock

HERE = Path(__file__).resolve().parent
REPO = HERE.parent
LOOP = HERE / "loop"
# the same search order as before (HERE, REPO, LOOP), written as inserts the deploy parity check reads (F47)
sys.path.insert(0, str(LOOP))
sys.path.insert(0, str(REPO))
sys.path.insert(0, str(HERE))
os.environ["BROTHER_CODE_ROOT"] = str(REPO)   # model_call resolves its code root at import
import test_proof_dispatch_accounting as F   # a module, so its ProofDispatch tests are not collected here again
import model_call as MC   # noqa: E402
import claude_ledger as CL   # noqa: E402

CLAUDE_REG = {"k": {"id": "claude-x", "transport": "claude", "privacy": "private", "quality": {"build": 5},
                    "kinds": {"build"}, "cost": 1}}
BRIDGE_REG = {"deepseek": {"id": "deepseek/deepseek-v4.1-flash", "transport": "bridge", "privacy": "public",
                           "quality": {"grade": 5}, "kinds": {"grade"}, "cost": 1}}
ANSWER = json.dumps({"result": "ok", "is_error": False, "total_cost_usd": 0.01, "usage": {"input_tokens": 1, "output_tokens": 1}})


class Transport(object):
    """A fake three argument runner that counts its calls and does one thing."""

    def __init__(self, result=None, raises=None, during=None):
        self.calls, self.result, self.raises, self.during = 0, result, raises, during

    def __call__(self, argv, stdin, timeout):
        self.calls += 1
        if self.during:
            self.during()
        if self.raises is not None:
            raise self.raises
        return self.result or {"returncode": 0, "stdout": ANSWER, "stderr": ""}


class Base(unittest.TestCase):
    def setUp(self):
        self.d = Path(tempfile.mkdtemp(prefix="mc-rows-"))
        self.ledger = self.d / "claude-calls.jsonl"
        env = {"BROTHER_CLAUDE_CALLS_LEDGER": str(self.ledger), "BROTHER_OR_STATE_ROOT": str(self.d / "state"),
               "BROTHER_BRIDGE_CALLS_LEDGER": str(self.d / "bridge-calls.jsonl"), "BROTHER_CODE_ROOT": str(REPO)}
        p = mock.patch.dict(os.environ, env); p.start(); self.addCleanup(p.stop)
        for k in ("BROTHER_PROOF_PHASE", "BROTHER_PROOF_BASELINE", "BROTHER_PROOF_BASELINE_SHA256", "BROTHER_RUN_DIR"):
            os.environ.pop(k, None)
        # the adapters build a line only for an executable program (FX-31.5); the transports here are fake, so argv[0]
        # names this interpreter and the suite passes on a machine with no Claude Code installed
        c = mock.patch.object(MC, "CLAUDE", sys.executable); c.start(); self.addCleanup(c.stop)

    def rows(self):
        return [json.loads(l) for l in self.ledger.read_text().splitlines() if l.strip()] if self.ledger.exists() else []

    def claude(self, transport, timeout=300):
        return MC.call_one("k", "hello", "build", "private", timeout=timeout, reg=CLAUDE_REG, runner=transport)

    def done(self):
        got = [r for r in self.rows() if r.get("event") == "done"]
        self.assertEqual(len(got), 1, self.rows())
        return got[0]


class ClaudeRows(Base):
    def test_a_spawn_that_never_started_is_a_zero_cost_not_started_call(self):
        a = self.claude(Transport(raises=MC.SpawnFailed("no such file")))
        self.assertFalse(a.ok)
        self.assertEqual(len(self.rows()), 2)
        self.assertEqual((self.done()["cost_usd"], self.done()["outcome"]), (0, "not_started"))

    def test_an_error_after_the_spawn_is_an_unknown_cost(self):
        a = self.claude(Transport(raises=OSError("broken pipe")))
        self.assertFalse(a.ok)
        self.assertEqual((self.done()["cost_usd"], self.done()["outcome"]), (None, "exit-unknown"))

    def test_a_timeout_is_an_unknown_cost(self):
        a = self.claude(Transport(raises=subprocess.TimeoutExpired(cmd="claude", timeout=1)))
        self.assertFalse(a.ok)
        self.assertEqual((self.done()["cost_usd"], self.done()["outcome"]), (None, "timeout"))

    def test_a_cost_is_recorded_whatever_the_exit_code(self):
        body = json.dumps({"result": "", "is_error": True, "total_cost_usd": 0.2, "usage": {}})
        a = self.claude(Transport({"returncode": 1, "stdout": body, "stderr": "failed"}))
        self.assertFalse(a.ok)
        self.assertEqual((self.done()["cost_usd"], self.done()["outcome"]), (0.2, "exit 1"))

    def test_a_nonzero_exit_without_a_result_is_an_unknown_cost(self):
        self.claude(Transport({"returncode": 1, "stdout": "", "stderr": "auth"}))
        self.assertEqual((self.done()["cost_usd"], self.done()["outcome"]), (None, "exit 1"))

    def test_an_answered_call_pairs_its_start_through_one_call_id(self):
        a = self.claude(Transport())
        self.assertTrue(a.ok, a.detail)
        start, done = self.rows()
        self.assertEqual(start["call"], done["call"])
        self.assertEqual((done["cost_usd"], done["outcome"]), (0.01, "exit 0"))

    def test_a_result_with_a_repeated_cost_is_an_unknown_cost_never_a_zero(self):
        """Lane X4: plain json kept the LAST of repeated members, so the answer above with total_cost_usd 0.01 then 0
        read as a measured zero. The strict parser refuses the document: an unreadable result, so the call fails and
        its cost is unknown. The test above is this fixture's control."""
        body = ANSWER.replace('"total_cost_usd": 0.01', '"total_cost_usd": 0.01, "total_cost_usd": 0')
        self.assertNotEqual(body, ANSWER)
        a = self.claude(Transport({"returncode": 0, "stdout": body, "stderr": ""}))
        self.assertFalse(a.ok)
        self.assertEqual((self.done()["cost_usd"], self.done()["outcome"]), (None, "exit 0"))

    def test_an_unwritable_ledger_means_the_transport_never_runs(self):
        self.ledger.write_text(""); os.chmod(str(self.ledger), 0o444); self.addCleanup(os.chmod, str(self.ledger), 0o644)
        t = Transport()
        a = self.claude(t)
        self.assertEqual(t.calls, 0, "a Claude call ran with no START row")
        self.assertFalse(a.ok)
        self.assertIn("ledger", a.detail)
        self.assertEqual(self.ledger.read_text(), "")

    def test_a_corrupt_ledger_means_the_transport_never_runs(self):
        """Codex audit 2026-09-27 finding 4: a ledger already reading NO-DATA admitted another paid call."""
        self.ledger.write_text("{torn ledger row\n")
        t = Transport()
        a = self.claude(t)
        self.assertEqual(t.calls, 0, "a Claude call ran on a ledger that reads NO-DATA")
        self.assertFalse(a.ok)
        self.assertIn("ledger", a.detail)
        self.assertEqual(self.ledger.read_text(), "{torn ledger row\n")

    def test_a_call_id_already_on_the_ledger_never_runs(self):
        """Finding 5, at the entry point: a START whose id the ledger already holds is refused, so the call never runs."""
        cid = "%d-7-00000000" % os.getpid()
        self.ledger.write_text(json.dumps({"at": datetime.datetime.now(datetime.timezone.utc).isoformat(), "call": cid,
                                           "expires_at": "2099-01-01T00:00:00+00:00"}) + "\n")
        t = Transport()
        with mock.patch.object(MC.time, "time_ns", return_value=7), mock.patch.object(MC.os, "urandom", side_effect=lambda n: b"\0" * n):
            a = self.claude(t)
        self.assertEqual(t.calls, 0, "a Claude call ran under an id the ledger already holds")
        self.assertFalse(a.ok)
        self.assertEqual(len(self.rows()), 1)

    def test_a_foreign_terminal_for_the_call_is_never_doubled(self):
        """Finding 5, at the entry point: when a terminal for this call's id is already written, model_call's own is
        refused, so the cost is counted once and call_one still returns its Attempt instead of raising."""
        def foreign():
            cid = self.rows()[0]["call"]
            CL.finish(str(self.ledger), {"call": cid, "cost_usd": 0.01})
        err = io.StringIO()
        with redirect_stderr(err):
            a = self.claude(Transport(during=foreign))
        self.assertTrue(a.ok, a.detail)
        self.assertEqual(len([r for r in self.rows() if r.get("event") == "done"]), 1, self.rows())
        t = CL.tally(str(self.ledger))
        self.assertEqual((round(t["usd"], 4), CL.note(t)), (0.01, ""))
        self.assertIn("terminal row", err.getvalue())

    def test_a_terminal_row_that_cannot_be_written_leaves_the_start_pending_then_no_data(self):
        t = Transport(during=lambda: os.chmod(str(self.ledger), 0o444))
        self.addCleanup(os.chmod, str(self.ledger), 0o644)
        err = io.StringIO()
        with redirect_stderr(err):
            self.claude(t)
        self.assertEqual(len(self.rows()), 1, "only the START row exists")
        self.assertIn("terminal row", err.getvalue())
        start = self.rows()[0]
        inside = CL.instant(start["expires_at"]) - datetime.timedelta(seconds=1)
        after = CL.instant(start["expires_at"]) + datetime.timedelta(seconds=1)
        pending, late = CL.tally(str(self.ledger), now=inside.isoformat()), CL.tally(str(self.ledger), now=after.isoformat())
        self.assertEqual((pending["inflight"], pending["uncosted"]), (1, 0))
        self.assertEqual((late["inflight"], late["uncosted"]), (0, 1))
        self.assertIn("NO-DATA", CL.note(late))

    def test_rows_carry_an_aware_utc_stamp_and_an_expiry_of_timeout_plus_ninety(self):
        self.claude(Transport(), timeout=300)
        start, done = self.rows()
        at = CL.instant(start["at"])
        self.assertEqual(at.utcoffset(), datetime.timedelta(0))
        self.assertEqual((CL.instant(start["expires_at"]) - at).total_seconds(), 390.0)
        self.assertEqual(CL.instant(done["at"]).utcoffset(), datetime.timedelta(0))

    def test_a_missing_claude_cli_is_a_zero_cost_not_started_call(self):
        """This Mac has no native Claude CLI. The real transport runs against a path that does not exist."""
        with mock.patch.object(MC, "CLAUDE", str(self.d / "no-such-claude")):
            a = MC.call_one("k", "hello", "build", "private", timeout=300, reg=CLAUDE_REG)
        self.assertFalse(a.ok)
        self.assertEqual((self.done()["cost_usd"], self.done()["outcome"]), (0, "not_started"))
        t = CL.tally(str(self.ledger), since="2000-01-01T00:00:00+00:00")
        self.assertEqual((t["uncosted"], t["inflight"]), (0, 0))
        self.assertEqual(CL.note(t), "", "a missing CLI must never read as unknown spend")


NO_ROOT_CHILD = textwrap.dedent("""
    import json, sys
    sys.path[:0] = [{loop!r}, {repo!r}]
    import model_call as MC
    called = []
    reg = {{"x": {{"id": "gpt-x", "transport": "codex", "privacy": "public", "kinds": {{"build"}}, "cost": 1, "quality": {{"build": 5}}}}}}
    a = MC.call_one("x", "hello", "build", "public", timeout=30, reg=reg,
                    runner=lambda argv, stdin, timeout: called.append(argv) or {{"returncode": 0, "stdout": "fine", "stderr": ""}})
    print(json.dumps({{"ok": a.ok, "why": a.detail, "runner_called": len(called)}}))
""")


class Supervision(Base):
    def test_a_proof_phase_without_a_code_root_runs_nothing(self):
        """The launch worktree is importable (it is on sys.path), yet a proof phase with no BROTHER_CODE_ROOT loads no
        pool from it and runs no call on any transport, codex included (it keeps no ledger of its own)."""
        child = self.d / "child.py"; child.write_text(NO_ROOT_CHILD.format(loop=str(LOOP), repo=str(REPO)))
        env = {k: v for k, v in os.environ.items() if not k.startswith("BROTHER_")}
        env.update(BROTHER_PROOF_PHASE="RB", HOME=str(self.d), BROTHER_OR_STATE_ROOT=str(self.d / "state"))
        r = subprocess.run([sys.executable, "-B", str(child)], capture_output=True, text=True, timeout=120, env=env, cwd=str(REPO))
        self.assertEqual(r.returncode, 0, r.stderr[-600:])
        got = json.loads(r.stdout.strip().splitlines()[-1])
        self.assertEqual((got["ok"], got["runner_called"]), (False, 0), got)
        self.assertIn("BROTHER_CODE_ROOT is not set", got["why"])

    def test_a_selftest_that_raises_is_a_failure(self):
        with mock.patch.object(MC, "_selftest_body", side_effect=RuntimeError("boom")), redirect_stderr(io.StringIO()):
            with mock.patch("sys.stdout", io.StringIO()):
                self.assertEqual(MC.selftest(), 1)

    def test_the_transport_waits_the_grace_the_ledger_expiry_assumes(self):
        """expires_at is timeout + 90 because the transport waits 30 s past the timeout before it kills the child."""
        r = MC._run([sys.executable, "-c", "import time; time.sleep(3); print('late but inside the grace')"], "", 1)
        self.assertEqual(r["returncode"], 0)
        self.assertEqual(CL.EXPIRY_S, CL.COMMUNICATE_GRACE_S + 60)


class ClaudeProofRows(Base):
    """In a proof phase a Claude call is admitted at registration, under the Claude ledger lock (objection 8)."""

    def setUp(self):
        F.ProofDispatch.setUp(self)
        Base.setUp(self)
        os.environ.update(self.env)
        os.environ["BROTHER_OR_STATE_ROOT"] = str(self.state)

    write_start = F.ProofDispatch.write_start
    launch = F.ProofDispatch.launch

    def test_rows_carry_the_runs_tags(self):
        self.claude(Transport())
        self.assertEqual([(r.get("run"), r.get("attempt")) for r in self.rows()], [("RB", "a" * 32)] * 2)
        t = CL.tally(str(self.ledger), run_id="RB")
        self.assertEqual((t["calls"], round(t["usd"], 4), t["uncosted"]), (1, 0.01, 0))

    def test_an_ending_run_starts_no_claude_call(self):
        (self.run / "proof" / "ending.json").write_text("{}")
        t = Transport()
        a = self.claude(t)
        self.assertEqual(t.calls, 0)
        self.assertEqual(self.rows(), [])
        self.assertIn("DrainRefused", a.detail)

    def test_a_call_that_would_outlive_the_deadline_is_refused_before_any_row(self):
        import time
        self.launch(time.time() + 300 + 90 - 20)
        t = Transport()
        a = self.claude(t, timeout=300)
        self.assertEqual((t.calls, self.rows()), (0, []))
        self.assertIn("deadline", a.detail)

    def test_a_call_that_ends_before_the_deadline_is_admitted(self):
        import time
        self.launch(time.time() + 300 + 90 + 60)
        t = Transport()
        self.claude(t, timeout=300)
        self.assertEqual(t.calls, 1)

    def test_a_writer_outside_the_code_root_is_refused(self):
        other = tempfile.mkdtemp(dir=str(self.d))
        os.environ["BROTHER_CODE_ROOT"] = other
        t = Transport()
        a = self.claude(t)
        self.assertEqual((t.calls, self.rows()), (0, []))
        self.assertIn(os.path.realpath(other), a.detail)


FAKE_BRIDGE = textwrap.dedent('''
    import os, sys
    with open(os.environ["FAKE_BRIDGE_SEEN"], "w") as f:
        f.write(os.environ.get("BROTHER_DISPATCH_RESERVATION", ""))
    print("12")
    sys.stderr.write("[usage] prompt=5 completion=3 model=deepseek/deepseek-v4.1-flash\\n[billed] usd=0.0100 attempts=1 known=yes\\n")
''')

# The REAL or_ask behind the dispatcher, with only its key reader stubbed: its proof refusals run for real, and the
# call stops at the key, so nothing can leave the machine. Loaded under another name: this file is itself or_ask.py.
REAL_BRIDGE = textwrap.dedent('''
    import importlib.util, sys
    spec = importlib.util.spec_from_file_location("real_or_ask", %r)
    A = importlib.util.module_from_spec(spec); spec.loader.exec_module(A)
    A.read_key = lambda account=None: None
    sys.exit(A.main())
''') % str(LOOP / "or_ask.py")


class BridgeBase(Base):
    """Codex check-in 2, finding 3: the bridge route reserves on the dispatcher's ledger before the bridge runs."""

    def bridge(self, body):
        d = self.d / "bridge"; d.mkdir(exist_ok=True)
        (d / "or_ask.py").write_text(body)
        os.environ["FAKE_BRIDGE_SEEN"] = str(self.d / "seen")
        return mock.patch.object(MC, "BRIDGE", str(d / "or_ask.py"))

    def ledger_rows(self, state):
        path = Path(state) / "openrouter-ledger.jsonl"
        return [json.loads(l) for l in path.read_text().splitlines()] if path.exists() else []

    def call(self):
        return MC.call_one("deepseek", "7 + 5", "grade", "public", timeout=300, reg=BRIDGE_REG)


class Bridge(BridgeBase):
    def test_outside_a_proof_every_bridge_call_is_reserved_on_the_ledger(self):
        with self.bridge(FAKE_BRIDGE):
            a = self.call()
        self.assertTrue(a.ok, a.detail)
        self.assertEqual(a.answer, "12")
        types = [r["type"] for r in self.ledger_rows(os.environ["BROTHER_OR_STATE_ROOT"])]
        self.assertEqual(types, ["RESERVE", "RECONCILE"])


class BridgeProof(BridgeBase):
    def setUp(self):
        F.ProofDispatch.setUp(self)
        Base.setUp(self)
        os.environ.update(self.env)
        os.environ["BROTHER_OR_STATE_ROOT"] = str(self.state)

    write_start = F.ProofDispatch.write_start
    launch = F.ProofDispatch.launch
    def rows_or(self):
        return self.ledger_rows(self.state)

    def test_a_proof_bridge_call_runs_with_its_reservation_and_settles(self):
        with self.bridge(FAKE_BRIDGE):
            a = self.call()
        self.assertTrue(a.ok, a.detail)
        new = self.rows_or()[1:]
        self.assertEqual([r["type"] for r in new], ["RESERVE", "DISPATCH_START", "RECONCILE"])
        self.assertEqual((self.d / "seen").read_text(), new[0]["reservation_id"], "the bridge did not get its reservation")
        self.assertEqual(new[2]["actual_cost"], 0.01)

    def test_the_codex_repro_passes_the_reservation_refusal_behind_the_dispatcher(self):
        """Codex check-in 2, finding 3 repro: the real or_ask behind model_call no longer refuses for a missing
        reservation; it stops at the stubbed key reader (exit 44 NO-DATA no valid key), nothing sent."""
        with self.bridge(REAL_BRIDGE):
            a = self.call()
        self.assertFalse(a.ok)
        self.assertNotIn("BROTHER_DISPATCH_RESERVATION is unset", a.detail)
        self.assertIn("no valid key", a.detail)
        self.assertEqual([r["type"] for r in self.rows_or()[1:]], ["RESERVE", "DISPATCH_START", "ABANDONED"])

    def test_the_bridges_own_refusal_still_stands_without_the_dispatcher(self):
        """The control: or_ask's proof refusal is not weakened. model_call's argv run directly still exits 44."""
        argv, _, _ = MC._argv("deepseek", "offline probe", 300, BRIDGE_REG)
        shim = self.d / "shim"; shim.mkdir(); (shim / "or_ask.py").write_text(REAL_BRIDGE)
        r = subprocess.run([sys.executable, "-B", str(shim / "or_ask.py")] + argv[2:], capture_output=True, text=True,
                           timeout=60, env=dict(os.environ, BROTHER_DISPATCH_RESERVATION=""))
        self.assertEqual(r.returncode, 44)
        self.assertIn("BROTHER_DISPATCH_RESERVATION is unset", r.stderr)

    def test_an_ending_run_reserves_nothing_and_runs_no_bridge(self):
        (self.run / "proof" / "ending.json").write_text("{}")
        with self.bridge(FAKE_BRIDGE):
            a = self.call()
        self.assertFalse(a.ok)
        self.assertIn("DrainRefused", a.detail)
        self.assertEqual(self.rows_or()[1:], [])
        self.assertFalse((self.d / "seen").exists())


if __name__ == "__main__":
    unittest.main(verbosity=1)

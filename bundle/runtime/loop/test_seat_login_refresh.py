#!/usr/bin/env python3
"""The seat login refresh (owner ruling A, 2026-10-05). A native seat may read this machine's Claude login but not write
it, so it cannot refresh an expired access token: proof pair RB's every seat answered 401 and parked CONFIG_WAIT while the
intake, which ran the program OUTSIDE the sandbox, had read READY. Two parts, one guard each, one fixture per guard:

  G1 refresh() is ONE UNSANDBOXED call of the production command line (never sandbox-exec, never a seat folder), on the
     cheapest Claude model, through main(["refresh"]), the driver's entry point.
  G2 a failed refresh is FAILED with its class, exit 1, and never prints the login text.
  G3 the intake's Claude reach check runs a real seat call (seat_probe, built by the seat's own _seat_session): a plain
     proof that reads OK while the seat call fails is REFUSED, the run NOT READY, the HINT naming the refresh.
  G4 seat_probe runs sandbox-exec in a scratch seat it removes afterwards, and a 401 there is REFUSED (login).

The driver's half (the refresh line before every pass, and a failing refresh reported) is driven for real in
scripts/test_loop_until_lifecycle.py. No real program runs here: every child is a fake runner.
Run: python3 -B scripts/loop/test_seat_login_refresh.py"""
import contextlib, datetime, io, json, os, shutil, sys, tempfile, unittest
from unittest import mock

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path[:0] = [HERE, os.path.dirname(os.path.dirname(HERE))]
import native_worker as NW  # noqa: E402
import loop_intake as LI  # noqa: E402
import loop_switches as SW  # noqa: E402
import model_reachability as MR  # noqa: E402

OK_DOC = json.dumps({"type": "result", "is_error": False, "result": "ok", "total_cost_usd": 0.001})
EXPIRED = json.dumps({"type": "result", "is_error": True, "result": "Failed to authenticate. API Error: 401 OAuth access token has expired"})
SECRETISH = "sk-" + "ant-oat01-" + "LEAK" * 6   # joined so no file holds the key shape at rest


class Base(unittest.TestCase):
    def setUp(self):
        self.root = tempfile.mkdtemp(prefix="seat-login-refresh-")
        self.addCleanup(shutil.rmtree, self.root, True)
        saved = {k: os.environ.get(k) for k in ("BROTHER_CLAUDE_CALLS_LEDGER", "BROTHER_LOOP_TOKEN", "BROTHER_CLAUDE_NATIVE",
                                                 "BROTHER_RUN_DIR", "BROTHER_SCRATCH")}
        self.addCleanup(lambda: [os.environ.pop(k, None) if v is None else os.environ.__setitem__(k, v) for k, v in saved.items()])
        for k in ("BROTHER_LOOP_TOKEN", "BROTHER_CLAUDE_NATIVE", "BROTHER_RUN_DIR"):
            os.environ.pop(k, None)
        os.environ["BROTHER_CLAUDE_CALLS_LEDGER"] = os.path.join(self.root, "calls.jsonl")
        os.environ["BROTHER_SCRATCH"] = os.path.join(self.root, "scratch")
        self.prog = os.path.join(self.root, "claude")   # an executable the adapter accepts; never run, every child is faked
        with open(self.prog, "w") as fh:
            fh.write("#!/bin/sh\nexit 99\n")
        os.chmod(self.prog, 0o700)
        for mod, name, fake in ((NW.R, "claude_bin", lambda: self.prog), (NW.MC, "CLAUDE", None),
                                (SW, "native_on", lambda env=None, which=None: True)):
            old = getattr(mod, name)
            setattr(mod, name, fake)
            self.addCleanup(setattr, mod, name, old)
        self.calls = []

    def plain(self, stdout=OK_DOC, rc=0, stderr=""):
        def run(argv, stdin, timeout):
            self.calls.append(list(argv))
            return {"returncode": rc, "stdout": stdout, "stderr": stderr}
        return run

    def cli(self, *argv):
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            rc = NW.main(list(argv))
        return rc, buf.getvalue()


class Refresh(Base):
    def test_g1_refresh_is_one_unsandboxed_production_call_on_the_cheapest_model(self):
        with mock.patch.object(NW.MC, "_run", self.plain()):
            rc, out = self.cli("refresh")
        self.assertEqual(rc, 0, out)
        self.assertTrue(out.startswith("REFRESH OK haiku answered through " + self.prog), out)
        self.assertEqual(len(self.calls), 1)
        argv = self.calls[0]
        self.assertEqual(argv[0], self.prog, "never sandbox-exec: a seat cannot write the login")
        self.assertNotIn("sandbox-exec", argv)
        self.assertEqual(argv[argv.index("--model") + 1], NW.R.registry()["haiku"]["id"])
        self.assertFalse(os.path.isdir(os.environ["BROTHER_SCRATCH"]), "no seat folder for a refresh")

    def test_g2_a_failed_refresh_names_its_class_and_never_the_login(self):
        with mock.patch.object(NW.MC, "_run", self.plain(stdout=EXPIRED, rc=1, stderr="token " + SECRETISH)):
            rc, out = self.cli("refresh")
        self.assertEqual((rc, out.strip()), (1, "REFRESH FAILED login"))
        self.assertNotIn(SECRETISH, out)

    def test_a_refresh_with_no_result_is_failed_not_ok(self):
        with mock.patch.object(NW.MC, "_run", self.plain(stdout="", rc=1)):
            rc, out = self.cli("refresh")
        self.assertEqual((rc, out.strip()), (1, "REFRESH FAILED no-result"))

    def test_no_seat_means_no_refresh_call(self):
        SW.native_on = lambda env=None, which=None: False
        with mock.patch.object(NW.MC, "_run", self.plain()):
            rc, out = self.cli("refresh")
        self.assertEqual(rc, 0)
        self.assertTrue(out.startswith("REFRESH SKIPPED"), out)
        self.assertEqual(self.calls, [])

    def test_a_draining_run_skips_the_refresh_and_spends_nothing(self):
        def drain(*a, **k):
            raise NW.CL.proof_ledger.DrainRefused("the run is ending")
        with mock.patch.object(NW.MC, "_run", self.plain()), mock.patch.object(NW.CL, "start", drain):
            rc, out = self.cli("refresh")
        self.assertEqual(rc, 0)
        self.assertTrue(out.startswith("REFRESH SKIPPED the run is draining"), out)
        self.assertEqual(self.calls, [])

    def test_a_ledger_that_refuses_the_row_is_failed_not_skipped(self):
        def broken(*a, **k):
            raise OSError("ledger unwritable")
        with mock.patch.object(NW.MC, "_run", self.plain()), mock.patch.object(NW.CL, "start", broken):
            rc, out = self.cli("refresh")
        self.assertEqual((rc, out.strip()), (1, "REFRESH FAILED ledger-refused (OSError)"))
        self.assertEqual(self.calls, [])

    def test_the_token_lane_skips_the_refresh(self):
        os.environ["BROTHER_LOOP_TOKEN"] = "on"
        with mock.patch.object(NW.MC, "_run", self.plain()):
            rc, out = self.cli("refresh")
        self.assertEqual(rc, 0)
        self.assertIn("token lane", out)
        self.assertEqual(self.calls, [])


class SeatProbe(Base):
    def seat_runner(self, stdout=OK_DOC, rc=0):
        def run(argv, stdin, timeout, cwd, env=None):
            self.calls.append((list(argv), cwd, os.path.isdir(cwd)))
            return {"returncode": rc, "stdout": stdout, "stderr": ""}
        return run

    def test_g4_a_seat_call_runs_sandboxed_in_a_scratch_seat_it_removes(self):
        state, why = NW.seat_probe(program="/nonexistent/claude", runner=self.seat_runner())
        self.assertEqual(state, "OK", why)
        argv, cwd, existed = self.calls[0]
        self.assertEqual(argv[0], "sandbox-exec")
        self.assertIn("/nonexistent/claude", argv)
        self.assertTrue(existed)
        self.assertTrue(cwd.startswith(os.path.realpath(os.environ["BROTHER_SCRATCH"])))
        self.assertFalse(os.path.exists(cwd), "the scratch seat is removed")
        self.assertFalse(os.path.exists(NW.tool_dir(cwd)), "and its tool folder")

    def test_g4_a_401_in_the_seat_is_refused_as_login(self):
        state, why = NW.seat_probe(program="/nonexistent/claude", runner=self.seat_runner(stdout=EXPIRED, rc=1))
        self.assertEqual(state, "REFUSED")
        self.assertIn("login", why)
        self.assertFalse(os.listdir(os.environ["BROTHER_SCRATCH"]), "the scratch seat is removed on a failure too")


class IntakeSeatGate(Base):
    """G3 at the intake's entry point: prepare() with the real reach probe; only the plain proof and the seat call are
    stood in for. ONE condition per fixture: the plain proof always reads OK, only the seat answer differs."""

    def prepare(self, seat):
        plain = lambda plan, *a, **k: [{"status": "OK", "role": r, "model": m, "model_id": m, "transport": "claude",
                                         "program": "/p/claude", "version": "9", "cause": "answered 12", "remedy": "",
                                         "cached": False, "proved_at": None, "binding": None, "pinned": False} for r, m in plan]
        self.seat_calls = []

        def probe(program=None):
            self.seat_calls.append(program)
            return seat
        base = {"does": "x", "when": "inside", "kind": "build", "content": "private", "must_be_chosen": False, "default": "opus55"}
        roles = {"worker": dict(base, setting="BROTHER_PIN_MODEL")}
        reg = {"opus55": {"id": "claude-opus-5-5", "transport": "claude", "privacy": "private", "quality": {"build": 9},
                          "kinds": {"build"}, "cost": 1.0}}
        noon = datetime.datetime(2026, 1, 1, 12, 0)
        ok = lambda: ("OK", "fixture")
        probes = {k: ok for k in ("canary", "alive", "lease", "parity", "switch", "tree", "digest", "done", "salvage", "pool")}
        probes.update(headroom=lambda: 50.0, hold=lambda: False, now=lambda: noon, reach=LI.real_probes()["reach"])
        env = {"BROTHER_WORKER_MIX": "opus55:1", "BROTHER_BUILD_PLAN_MODEL": "off", "BROTHER_REPAIR_ADVISOR_MODEL": "off"}
        with mock.patch.object(MR, "resolve_and_prove", plain), mock.patch.object(NW, "seat_probe", probe):
            return LI.prepare({}, "18:00", 10.0, None, roles, reg, probes, env=env)[0]

    def test_g3_control_a_seat_that_answers_reads_ready(self):
        rec = self.prepare(("OK", "a sandboxed native seat answered (haiku)"))
        self.assertEqual(rec["verdict"], "READY", "\n".join(rec["lines"]))
        self.assertEqual(self.seat_calls, ["/p/claude"], "one seat call, on the proven program")

    def test_g3_the_plain_call_answers_but_the_seat_does_not_is_not_ready(self):
        rec = self.prepare(("REFUSED", "the seat call failed: login"))
        self.assertEqual(rec["verdict"], "NOT READY", "\n".join(rec["lines"]))
        bad = [l for l in rec["lines"] if l.startswith("REFUSED") and "sandboxed native seat did not" in l]
        self.assertTrue(bad, rec["lines"])
        self.assertTrue(all("native_worker.py refresh" in l for l in bad), bad)
        self.assertEqual(rec["reach"], [], "a refused seat leaves no proven binding")

    def test_no_seat_runs_when_native_is_off(self):
        SW.native_on = lambda env=None, which=None: False
        rec = self.prepare(("REFUSED", "never asked"))
        self.assertEqual(rec["verdict"], "READY", "\n".join(rec["lines"]))
        self.assertEqual(self.seat_calls, [])


if __name__ == "__main__":
    unittest.main()

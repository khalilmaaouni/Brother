#!/usr/bin/env python3
"""SEAT1.c: a relaunch after a seat failure proves that seat LIVE, never from the 900 s proof cache.

THE INCIDENT (2026-10-11). An intake refused a codex seat out of credits; the relaunch minutes later could still be
answered by a cached OK proof of that seat. loop_intake.fresh_transports names the non Claude transports whose cached
proofs are not trusted, from the previous intake record and the breaker event log, and every doubt widens the set.
The mutations that must turn this suite red:

  M-SEAT1-FRESH   the NOT READY rule is removed (a failed seat in the last record forces nothing)
  M-SEAT1-NOREC   a missing or corrupt previous record forces nothing
  M-SEAT1-EVENTS  the breaker event log is ignored

No real program answers a model here: calls go to fake runners, and the only real processes are fake `--version`
scripts written into a temp directory.
Run from the repository root: python3 -B scripts/loop/test_seat_fresh_proof.py
"""
import json, os, shutil, subprocess, sys, tempfile, time, unittest

LOOP = os.path.dirname(os.path.abspath(__file__))
sys.path[:0] = [LOOP, os.path.dirname(os.path.dirname(LOOP))]

import breaker as BR  # noqa: E402
import loop_intake as LI  # noqa: E402
import model_call as MC  # noqa: E402
import model_reachability as MR  # noqa: E402
from test_config_dispatch import FakeAdmission  # noqa: E402

REG = {"gx": {"id": "gpt-x", "transport": "codex", "privacy": "public", "quality": {"build": 9}, "kinds": {"build"},
              "cost": 1.0}}
PLAN = [("worker", "gx")]
EVERY = {"codex", "bridge"}
KEYS = ("BROTHER_PROOF_CACHE", "BROTHER_OR_STATE_ROOT", "BROTHER_CLAUDE_CALLS_LEDGER", "BROTHER_BRIDGE_CALLS_LEDGER",
        "BROTHER_PROGRAM_RECORD", "BROTHER_PROOF_ALLOWANCE_USD", "BROTHER_CODEX_BIN", "BROTHER_TRANSPORTS",
        "INTAKE_EVIDENCE", "INTAKE_PROBES")


class Fresh(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="seat-fresh-")
        self.addCleanup(shutil.rmtree, self.tmp, True)
        saved = {k: os.environ.get(k) for k in KEYS}
        self.addCleanup(lambda: [os.environ.pop(k, None) if v is None else os.environ.__setitem__(k, v) for k, v in saved.items()])
        for k in KEYS:
            os.environ.pop(k, None)
        os.environ.update(BROTHER_PROOF_CACHE=os.path.join(self.tmp, "proofs.json"),
                          BROTHER_OR_STATE_ROOT=os.path.join(self.tmp, "state"),
                          BROTHER_CLAUDE_CALLS_LEDGER=os.path.join(self.tmp, "calls.jsonl"),
                          BROTHER_BRIDGE_CALLS_LEDGER=os.path.join(self.tmp, "bridge.jsonl"),
                          BROTHER_PROGRAM_RECORD=os.path.join(self.tmp, "programs.json"),
                          INTAKE_EVIDENCE=os.path.join(self.tmp, "evidence"))
        os.makedirs(os.path.join(self.tmp, "evidence", "loop-intake"))
        os.makedirs(os.path.join(self.tmp, "state"))
        adm = MC.admission
        MC.admission = FakeAdmission()
        self.addCleanup(setattr, MC, "admission", adm)
        self.codex = os.path.join(self.tmp, "codex")
        with open(self.codex, "w") as fh:
            fh.write("#!/bin/sh\necho 'codex-cli 0.130.0'\n")
        os.chmod(self.codex, 0o755)
        self.calls = []
        self.epoch = time.time() - 60.0

    def runner(self, rc=0, out="12", err=""):
        def run(argv, stdin, timeout):
            self.calls.append(list(argv))
            return {"returncode": rc, "stdout": out, "stderr": err}
        return run

    def write_current(self, rec=None, raw=None):
        with open(os.path.join(self.tmp, "evidence", "loop-intake", "CURRENT.json"), "w") as fh:
            fh.write(raw if raw is not None else json.dumps(rec))

    def prime(self):
        p = MR.prove("gx", REG, program=self.codex, runner=self.runner())
        self.assertEqual(p["status"], "OK", p)
        del self.calls[:]

    def fresh_now(self):
        prev = LI.read_current()
        epoch = prev.get("epoch") if isinstance(prev, dict) else 0.0
        return LI.fresh_transports(prev, LI.breaker_events(epoch or 0.0))

    def launch(self, fresh, runner=None):
        return MR.resolve_and_prove(PLAN, REG, runner=runner or self.runner(), candidates={"codex": [self.codex]}, env={},
                                    fresh=fresh)

    def test_a_not_ready_record_with_a_codex_failure_forces_a_fresh_codex_proof(self):
        self.prime()
        self.write_current({"verdict": "NOT READY", "epoch": self.epoch, "seat_failures": ["codex"]})
        fresh = self.fresh_now()
        self.assertEqual(fresh, {"codex"}, "M-SEAT1-FRESH: a failed codex seat in the last record forced nothing")
        out = self.launch(fresh)
        self.assertEqual(len(self.calls), 1, "the cached OK answered for a seat that failed last launch: %r" % out)
        self.assertEqual(out[0]["status"], "OK", out)
        self.assertFalse(out[0]["cached"], out)

    def test_a_corrupt_previous_record_forces_every_non_claude_seat_fresh(self):
        self.prime()
        self.write_current(raw='{"verdict": "READY", "epo')   # truncated
        self.assertIsNone(LI.read_current())
        fresh = self.fresh_now()
        self.assertEqual(fresh, EVERY, "M-SEAT1-NOREC: a corrupt previous record forced nothing")
        self.launch(fresh)
        self.assertEqual(len(self.calls), 1)
        os.remove(os.path.join(self.tmp, "evidence", "loop-intake", "CURRENT.json"))
        self.assertEqual(self.fresh_now(), EVERY, "M-SEAT1-NOREC: a missing previous record forced nothing")

    def test_a_trip_after_the_last_record_forces_that_transport_fresh(self):
        self.prime()
        self.write_current({"verdict": "READY", "epoch": self.epoch, "seat_failures": []})
        BR.record(BR.config_key("codex", "gpt-x"), "CONFIG", "c1", "the workspace is out of credits: ERROR")
        BR.record(BR.config_key("claude", "claude-x"), "CONFIG", "c2", "a claude trip is not forced here")
        fresh = self.fresh_now()
        self.assertEqual(fresh, {"codex"}, "M-SEAT1-EVENTS: a trip after the last record forced nothing")
        self.launch(fresh)
        self.assertEqual(len(self.calls), 1)

    def test_a_clean_ready_record_keeps_the_cache(self):
        self.prime()
        BR.record(BR.config_key("codex", "gpt-x"), "CONFIG", "c0", "an old trip")   # older than the record below
        BR.close_config(BR.config_key("codex", "gpt-x"), time.time())
        self.write_current({"verdict": "READY", "epoch": time.time() + 1.0, "seat_failures": []})
        fresh = self.fresh_now()
        self.assertEqual(fresh, set())
        out = self.launch(fresh)
        self.assertEqual(len(self.calls), 0, out)
        self.assertTrue(out[0]["cached"], out)

    def test_an_unreadable_event_log_forces_fresh_and_a_failed_proof_is_no_data(self):
        self.prime()
        self.write_current({"verdict": "READY", "epoch": self.epoch, "seat_failures": []})
        os.makedirs(os.path.join(self.tmp, "state", "breaker-events.jsonl"))   # a directory: it cannot be read
        self.assertIsNone(LI.breaker_events(self.epoch))
        fresh = self.fresh_now()
        self.assertEqual(fresh, EVERY)

        def timed_out(argv, stdin, timeout):
            self.calls.append(list(argv))
            raise subprocess.TimeoutExpired(argv, timeout)
        out = self.launch(fresh, runner=timed_out)
        self.assertEqual(len(self.calls), 1)
        self.assertEqual(out[0]["status"], "NO-DATA", out)
        self.assertIn("NO START", MR.line(out[0]))

    def test_edges_widen(self):
        ok = []
        self.assertEqual(LI.fresh_transports(None, ok), EVERY)
        self.assertEqual(LI.fresh_transports([], ok), EVERY)                 # not a mapping
        self.assertEqual(LI.fresh_transports({}, ok), EVERY)                 # no verdict, no seat_failures
        self.assertEqual(LI.fresh_transports({"verdict": "NOT READY"}, ok), EVERY)   # a 1.1.2 record
        self.assertEqual(LI.fresh_transports({"verdict": "NOT READY", "seat_failures": "codex"}, ok), EVERY)
        self.assertEqual(LI.fresh_transports({"verdict": "WHAT", "seat_failures": ["bridge", "claude"]}, ok), {"bridge"})
        self.assertEqual(LI.fresh_transports({"verdict": "READY", "epoch": 5.0}, None), EVERY)
        self.assertEqual(LI.fresh_transports({"verdict": "READY", "epoch": 5.0},
                                             [{"event": "trip", "key": "bridge:acct:m", "t": 6.0},
                                              {"event": "close", "key": "codex:acct:m", "t": 7.0},
                                              {"event": "trip", "key": "codex:acct:m", "t": 4.0}]), {"bridge"})
        self.assertEqual(LI.fresh_transports({"verdict": "READY", "epoch": 5.0}, [{"event": "trip", "t": 6.0}]), EVERY)
        self.assertEqual(LI.breaker_events(0.0, os.path.join(self.tmp, "absent.jsonl")), [])
        bad = os.path.join(self.tmp, "bad.jsonl")
        with open(bad, "w") as fh:
            fh.write('{"event": "trip", "t": 1}\n{"event": "tri')
        self.assertIsNone(LI.breaker_events(0.0, bad))

    def test_the_intake_wires_fresh_and_writes_seat_failures(self):
        """The entry points: real_probes' reach passes the fresh set, and prepare records the failed seat."""
        self.write_current({"verdict": "NOT READY", "epoch": self.epoch, "seat_failures": ["codex"]})
        seen = []
        real = MR.resolve_and_prove
        MR.resolve_and_prove = lambda plan, **kw: seen.append(kw.get("fresh")) or []
        try:
            LI.real_probes()["reach"](PLAN)
        finally:
            MR.resolve_and_prove = real
        self.assertEqual(seen, [{"codex"}])
        fail = {"status": "FAIL", "role": "worker", "model": "gx", "model_id": "gpt-x", "transport": "codex",
                "program": self.codex, "version": "0.130.0", "cause": "the workspace is out of credits", "remedy": "r",
                "cached": False, "binding": None}
        base = {"does": "x", "when": "inside", "kind": "build", "content": "public", "must_be_chosen": False,
                "default": "gx", "setting": "BROTHER_PIN_MODEL"}
        ok = lambda: ("OK", "fixture")
        probes = {k: ok for k in ("canary", "alive", "lease", "parity", "switch", "tree", "digest", "done", "salvage", "pool")}
        probes.update(headroom=lambda: 50.0, hold=lambda: False, now=lambda: __import__("datetime").datetime(2026, 1, 1, 12),
                      reach=lambda plan: [dict(fail, role=r) for r, _ in plan])
        env = {"BROTHER_WORKER_MIX": "gx:1", "BROTHER_BUILD_PLAN_MODEL": "off", "BROTHER_REPAIR_ADVISOR_MODEL": "off"}
        rec = LI.prepare({}, "18:00", 10.0, None, {"worker": base}, REG, probes, env=env)[0]
        self.assertEqual(rec["verdict"], "NOT READY", rec["lines"])
        self.assertEqual(rec["seat_failures"], ["codex"])


if __name__ == "__main__":
    unittest.main()

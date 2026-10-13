#!/usr/bin/env python3
"""SEAT1.d: the refusal line carries the real cause, the remedy follows it, and a busy proof slot fails in 20 s.

THE INCIDENT (2026-10-11 15:38). The documenter seat was refused with `cause=exit 1: Reading additional input from
stdin...  HINT: the owner pin names a program that does not answer this model: upgrade that program`; the real cause
was `Your workspace is out of credits`, which no upgrade fixes. On 2026-10-03 one intake also waited 120 s for a slot
five times. The mutations that must turn this suite red:

  M-SEAT1-REMEDY     remedy ignores the cause (the old branches only)
  M-SEAT1-FIRSTLINE  real_cause reads the FIRST stderr line
  M-SEAT1-WAIT120    a proof waits its full timeout for a slot again

No real program answers a model here: calls go to fake runners, and the only real processes are fake `--version`
scripts written into a temp directory.
Run from the repository root: python3 -B scripts/loop/test_seat_refusal_line.py
"""
import json, os, sys, types, unittest

LOOP = os.path.dirname(os.path.abspath(__file__))
sys.path[:0] = [LOOP, os.path.dirname(os.path.dirname(LOOP))]

import model_call as MC  # noqa: E402
import model_reachability as MR  # noqa: E402
import model_router as R  # noqa: E402
from test_config_dispatch import FakeAdmission  # noqa: E402
from test_model_reachability import REG as CLAUDE_REG, Reach  # noqa: E402

CREDITS = "Reading additional input from stdin...\nERROR: Your workspace is out of credits. Add credits to continue.\n"
REG = dict(CLAUDE_REG, gx={"id": "gpt-x", "transport": "codex", "privacy": "public", "quality": {"build": 9},
                           "kinds": {"build"}, "cost": 1.0})


class BusyAdmission(FakeAdmission):
    """acquire_slot records the wait it was given, then finds no slot."""

    def __init__(self, message="no slot free among 4 after 20s"):
        FakeAdmission.__init__(self)
        self.message, self.waits = message, []

    def acquire_slot(self, root, limit, holder, timeout_seconds=None):
        self.waits.append(timeout_seconds)
        raise self.NoSlotAvailable(self.message)


class RefusalLine(Reach):
    def setUp(self):
        Reach.setUp(self)
        self.codex = os.path.join(self.tmp, "codex")
        with open(self.codex, "w") as fh:
            fh.write("#!/bin/sh\necho 'codex-cli 0.130.0'\n")
        os.chmod(self.codex, 0o755)

    def fixed(self, rc, out="", err=""):
        def run(argv, stdin, timeout):
            self.calls.append(list(argv))
            return {"returncode": rc, "stdout": out, "stderr": err}
        return run

    def pinned_codex(self, runner):
        p = MR.resolve_and_prove([("documenter", "gx")], REG, runner=runner, env={"BROTHER_CODEX_BIN": self.codex})[0]
        self.assertTrue(p["pinned"], p)
        return p

    def test_the_codex_credits_refusal_names_credits_and_the_remedy_is_not_upgrade(self):
        p = self.pinned_codex(self.fixed(1, err=CREDITS))
        text = MR.line(p)
        self.assertTrue(text.startswith("REFUSED  NO START"), text)
        self.assertIn("cause=exit 1: the workspace is out of credits: ERROR: Your workspace is out of credits", text)
        self.assertNotIn("Reading additional input", text, "M-SEAT1-FIRSTLINE: the banner is the cause")
        hint = text.split("HINT:", 1)[1]
        self.assertNotIn("upgrade that program", hint, "M-SEAT1-REMEDY: credits told to upgrade the program")
        self.assertIn("out of credits", hint)
        self.assertIn("updating the program will not help", MR.remedy("codex", self.codex, p["cause"]))
        self.assertIn("sign in again", MR.remedy("claude", "c", "exit 1: the login is rejected (401): x", pinned=True))

    def test_an_unknown_model_keeps_the_upgrade_remedy(self):
        p = self.pinned_codex(self.fixed(1, err="banner line\nERROR: unknown model gpt-x\n"))
        self.assertEqual(p["status"], "FAIL", p)
        self.assertIn("cause=exit 1: ERROR: unknown model gpt-x", MR.line(p))
        self.assertIn("upgrade that program", p["remedy"])

    def test_an_exit_1_json_stub_prints_its_error_text(self):
        stub = json.dumps({"type": "result", "is_error": True, "duration_api_ms": 0,
                           "result": "API Error: 500 the upstream is overloaded"})
        p = MR.prove("opus55", REG, program=self.new, runner=self.fixed(1, out=stub, err="warming up\n"))
        self.assertEqual(p["status"], "FAIL", p)
        self.assertEqual(p["cause"], "exit 1: API Error: 500 the upstream is overloaded")
        ok_doc = json.dumps({"type": "result", "is_error": False, "result": "x"})   # is_error false at exit 1: stderr
        a = types.SimpleNamespace(raw={"returncode": 1, "stdout": ok_doc, "stderr": "boom\n"}, detail="d")
        self.assertEqual(MR.real_cause(a), "exit 1: boom")

    def test_a_busy_proof_slot_fails_in_20_seconds(self):
        MC.admission = BusyAdmission()
        p = MR.prove("opus55", REG, program=self.new, runner=self.fixed(0, out="12"))
        self.assertEqual(MC.admission.waits, [MC.PROOF_SLOT_WAIT_S], "M-SEAT1-WAIT120: the proof waited its full timeout")
        self.assertEqual(MC.PROOF_SLOT_WAIT_S, 20.0)
        self.assertEqual(p["status"], "NO-DATA", p)
        self.assertEqual(self.calls, [])
        self.assertIn("NO-DATA  NO START", MR.line(p))
        self.assertIn("cause=proof slot busy, a loop or runner holds all 4; stop it or wait", MR.line(p))
        MC.admission = BusyAdmission("held")
        p = MR.prove("sonnet", REG, program=self.new, runner=self.fixed(0, out="12"))
        self.assertEqual(p["cause"], "proof slot busy, a loop or runner holds all of them; stop it or wait")

    def test_a_production_call_keeps_its_full_wait(self):
        MC.admission = BusyAdmission()
        a = MC.call_one("opus55", "build", "build", R.PUBLIC, timeout=120, reg=REG, runner=self.fixed(0, out="12"),
                        program=self.new)
        self.assertEqual(MC.admission.waits, [120])
        self.assertFalse(a.ok)

    def test_an_unclassified_failure_keeps_the_raw_last_line_and_refuses(self):
        p = MR.prove("gx", REG, program=self.codex, runner=self.fixed(1, err="banner\nERROR: stream disconnected\n\n"))
        self.assertEqual(p["status"], "FAIL", p)
        self.assertEqual(p["cause"], "exit 1: ERROR: stream disconnected")
        self.assertIn("update the Codex CLI", p["remedy"])
        p = MR.prove("gx", REG, program=self.codex, runner=self.fixed(1))
        self.assertEqual((p["status"], p["cause"]), ("FAIL", "exit 1: no output"))

    def test_edges_fall_back_to_the_detail(self):
        for raw in (None, "not a mapping", {"returncode": "1", "stdout": "", "stderr": "x"},
                    {"returncode": 1, "stdout": b"x", "stderr": ""}):
            self.assertEqual(MR.real_cause(types.SimpleNamespace(raw=raw, detail="the detail")), "the detail")
        a = types.SimpleNamespace(raw={"returncode": 2, "stdout": "{not json", "stderr": ""}, detail="d")
        self.assertEqual(MR.real_cause(a), "exit 2: {not json")
        long = types.SimpleNamespace(raw={"returncode": 1, "stdout": "", "stderr": "y" * 900}, detail="d")
        self.assertLessEqual(len(MR.real_cause(long)), 240)


if __name__ == "__main__":
    unittest.main()

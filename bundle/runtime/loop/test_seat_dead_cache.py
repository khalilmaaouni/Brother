#!/usr/bin/env python3
"""SEAT1.b: a dead seat (out of credits, login rejected) opens the configuration breaker on its first failed call, so
the next proof of that (transport, model) is FRESH, never the cached OK, and the breaker's reason names the real cause.

THE INCIDENT (2026-10-11 15:38). A codex seat out of credits failed with `Reading additional input from stdin...`
then `ERROR: Your workspace is out of credits.`; that failure classified MALFORMED, no breaker opened, and the cached
OK proof kept answering for 900 s. The mutations that must turn this suite red:

  M-SEAT1-CACHE   prove's cache hit no longer checks the configuration breaker
  M-SEAT1-BANNER  config_outcome records (err + "\\n" + out) again, so the breaker's reason is the banner

No real program answers a model here: calls go to fake runners, and the only real processes are fake `--version`
scripts written into a temp directory.
Run from the repository root: python3 -B scripts/loop/test_seat_dead_cache.py
"""
import os, sys, unittest

LOOP = os.path.dirname(os.path.abspath(__file__))
sys.path[:0] = [LOOP, os.path.dirname(os.path.dirname(LOOP))]

import breaker as BR  # noqa: E402
import model_call as MC  # noqa: E402
import model_reachability as MR  # noqa: E402
import model_router as R  # noqa: E402
from test_config_dispatch import INCIDENT  # noqa: E402
from test_model_reachability import REG, Reach  # noqa: E402

LOGIN = "Failed to authenticate. API Error: 401 OAuth access token is invalid"
CREDITS = "Reading additional input from stdin...\nERROR: Your workspace is out of credits. Add credits to continue.\n"
CODEX_REG = {"k": {"id": "gpt-x", "transport": "codex", "privacy": R.PUBLIC, "quality": {"build": 1},
                   "kinds": {"build"}, "cost": 1.0}}
KEY = BR.config_key("claude", "claude-opus-5-5")


class DeadSeat(Reach):
    def failing(self, stderr=LOGIN):
        def run(argv, stdin, timeout):
            self.calls.append(list(argv))
            return {"returncode": 1, "stdout": "", "stderr": stderr}
        return run

    def prime_and_kill(self):
        """A cached OK proof, then one production call on the dead seat."""
        first = MR.prove("opus55", REG, program=self.new, runner=self.runner())
        self.assertEqual(first["status"], "OK", first)
        self.assertEqual(MR.prove("opus55", REG, program=self.new, runner=self.runner())["cached"], True)
        a = MC.call_one("opus55", "build the thing", "build", R.PUBLIC, timeout=30, reg=REG, runner=self.failing(),
                        program=self.new)
        self.assertFalse(a.ok)
        self.assertEqual(a.failure, "CONFIG", a.detail)
        return a

    def test_a_credits_failure_opens_the_breaker_and_refuses_the_cache(self):
        self.prime_and_kill()
        self.assertTrue(BR.config_open(KEY), "the dead seat opened no configuration breaker")
        before = len(self.calls)
        p = MR.prove("opus55", REG, program=self.new, runner=self.failing())
        self.assertEqual(len(self.calls), before + 1, "M-SEAT1-CACHE: the cached OK answered for a dead seat %r" % p)
        self.assertNotEqual(p["status"], "OK", p)
        self.assertFalse(p["cached"], p)

    def test_a_fresh_proof_after_credits_returns_closes_the_breaker(self):
        self.prime_and_kill()
        p = MR.prove("opus55", REG, program=self.new, runner=self.runner())
        self.assertEqual(p["status"], "OK", p)
        self.assertFalse(p["cached"], p)
        self.assertEqual(BR.config_open(KEY), "")

    def test_the_breaker_reason_is_not_the_banner(self):
        self.assertTrue(MC.config_outcome("codex", "gpt-x", 1, "", CREDITS, "c1"))
        why = BR.config_open(BR.config_key("codex", "gpt-x"))
        self.assertIn("out of credits", why)
        self.assertNotIn("Reading additional input", why, "M-SEAT1-BANNER: the breaker's reason is the banner")
        self.assertFalse(MC.config_outcome("codex", "gpt-y", 1, "", "", "c2"))   # empty stderr is not CONFIG
        self.assertEqual(BR.config_open(BR.config_key("codex", "gpt-y")), "")

    def test_the_attempt_detail_names_the_cause_and_unknown_model_keeps_its_text(self):
        a = MC._judge_body("k", CODEX_REG, "build", "p", {"returncode": 1, "stdout": "", "stderr": CREDITS}, 0.1)
        self.assertEqual(a.failure, "CONFIG")
        self.assertEqual(a.detail, "%s: the seat is out of credits: ERROR: Your workspace is out of credits. "
                                   "Add credits to continue." % MC.CONFIG_WAIT)
        a = MC._judge_body("opus55", REG, "build", "p", {"returncode": 1, "stdout": "", "stderr": LOGIN}, 0.1)
        self.assertEqual(a.detail, "%s: the seat's login is rejected: %s" % (MC.CONFIG_WAIT, LOGIN))
        a = MC._judge_body("sonnet", REG, "build", "p", {"returncode": 1, "stdout": "", "stderr": INCIDENT}, 0.1)
        self.assertTrue(a.detail.startswith("%s: the program does not know this model: " % MC.CONFIG_WAIT), a.detail)
        self.assertIn("unrecognized_model", a.detail)


if __name__ == "__main__":
    unittest.main()

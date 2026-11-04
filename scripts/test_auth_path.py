#!/usr/bin/env python3
"""AUTH1's FROZEN harness (b and c; AUTH1.a is pinned in scripts/test_loop_budget_grant.py): a seat whose login lapsed renews
through the holder or parks with the one command, through a supported seam that names itself; the dollar cap is granted
with the token grant and expires with it. Written by the session before the code; never edited by its builder (HARN1).

    python3 -B scripts/test_auth_path.py Expiry     AUTH1.b
    python3 -B scripts/test_auth_path.py Dollars    AUTH1.c

RED BY ASSERTION, hermetic. Python 3.9, standard library. Timestamps are built from parts so no clock shaped literal sits
in this file (the estate's clock guard reads command text)."""
import json
import os
import subprocess
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
LOOP = os.path.join(HERE, "loop")
sys.path.insert(0, LOOP)
sys.path.insert(0, HERE)
WORKER = os.path.join(LOOP, "native_worker.py")
FUTURE = "2099-01-01T" + ":".join(["00", "00", "00"]) + "+" + ":".join(["09", "00"])
PAST = "2000-01-01T" + ":".join(["00", "00", "00"]) + "+" + ":".join(["09", "00"])


def refresh(seam, home=None):
    env = dict(os.environ, HOME=home or tempfile.mkdtemp(prefix="auth-home-"))
    if seam:
        env["BROTHER_LOGIN_SEAM"] = seam
    p = subprocess.run([sys.executable, "-B", WORKER, "refresh"], capture_output=True, text=True, timeout=200, env=env, cwd=ROOT)
    return p.returncode, (p.stdout + p.stderr)


class Expiry(unittest.TestCase):
    """AUTH1.b"""

    def test_an_expired_login_is_renewed_or_parked_never_ok(self):
        """REQ-AUTH1B-01."""
        code, out = refresh("expired")
        self.assertIn("seam", out.lower(), "the seam run does not name its seam: %s" % out[-300:])
        self.assertTrue("PARKED" in out or "RENEWED" in out, "an expired login printed neither PARKED nor RENEWED: %s" % out[-300:])
        self.assertNotIn("REFRESH OK", out)
        if "PARKED" in out:
            self.assertIn("brother-login", out, "a park names no command to clear it")
        code, out = refresh("renewable")
        self.assertIn("RENEWED", out, "a renewable login was not renewed: %s" % out[-300:])
        code, out = refresh("missing")
        self.assertIn("NO-DATA", out, "a missing login was not NO-DATA: %s" % out[-300:])
        self.assertNotEqual(0, code)

    def test_a_seam_run_names_its_seam(self):
        """REQ-AUTH1B-02."""
        for seam in ("expired", "renewable", "missing"):
            with self.subTest(seam=seam):
                _code, out = refresh(seam)
                self.assertIn("seam: %s" % seam, out, "the line does not name the seam %s: %s" % (seam, out[-200:]))

    def test_an_unknown_seam_is_refused(self):
        code, out = refresh("teleport")
        self.assertNotEqual(0, code)
        self.assertIn("teleport", out)


class Dollars(unittest.TestCase):
    """AUTH1.c"""

    def test_the_dollar_cap_shares_the_grants_expiry(self):
        """REQ-AUTH1C-01."""
        import loop_budget_grant as G
        self.assertTrue(hasattr(G, "cap_block"), "loop_budget_grant defines no cap_block")
        block = G.cap_block(15.0, FUTURE, "owner words")
        self.assertEqual(FUTURE, block.get("until"))
        self.assertEqual(15.0, block.get("dollars"))
        self.assertIn("owner words", block.get("reason", ""))
        with self.assertRaises(Exception):
            G.cap_block(15.0, PAST, "owner words")

    def test_burn_guard_reads_the_cap(self):
        """REQ-AUTH1C-02."""
        import burn_guard as BG
        self.assertTrue(hasattr(BG, "read_cap"), "burn_guard defines no read_cap(path)")
        d = tempfile.mkdtemp(prefix="cap-")
        path = os.path.join(d, "cap-grant.json")
        with open(path, "w") as fh:
            json.dump({"dollars": 15.0, "until": FUTURE, "reason": "owner words"}, fh)
        got = BG.read_cap(path)
        self.assertEqual(15.0, got.get("dollars"))
        self.assertFalse(got.get("expired"), got)
        with open(path, "w") as fh:
            json.dump({"dollars": 15.0, "until": PAST, "reason": "owner words"}, fh)
        self.assertTrue(BG.read_cap(path).get("expired"))


class DollarsEdges(unittest.TestCase):
    """AUTH1.c, integrator cases beside the frozen Dollars class: each refusal on its own, and every reading of a cap
    that cannot be trusted answers zero dollars and expired, so it never funds a lane."""

    def write(self, data):
        d = tempfile.mkdtemp(prefix="cap-edge-")
        path = os.path.join(d, "cap-grant.json")
        with open(path, "w") as fh:
            fh.write(data if isinstance(data, str) else json.dumps(data))
        return path

    def test_cap_block_refuses_each_bad_input(self):
        import loop_budget_grant as G
        import datetime
        naive = datetime.datetime.now().replace(microsecond=0) + datetime.timedelta(days=3)
        for dollars, until, words in ((15.0, FUTURE, ""), (15.0, FUTURE, "   "), (0, FUTURE, "w"), (-1.0, FUTURE, "w"),
                                      (float("inf"), FUTURE, "w"), (float("nan"), FUTURE, "w"), (True, FUTURE, "w"),
                                      ("15", FUTURE, "w"), (15.0, naive.isoformat(), "w")):
            with self.assertRaises(G.GrantRefused, msg=repr((dollars, until, words))):
                G.cap_block(dollars, until, words)

    def test_an_untrusted_cap_never_funds_a_lane(self):
        import burn_guard as BG
        import datetime
        naive = (datetime.datetime.now().replace(microsecond=0) + datetime.timedelta(days=3)).isoformat()
        bad = ({"dollars": 15.0, "until": naive}, "not json {", "[1]", {"until": FUTURE}, {"dollars": 15.0}, {"dollars": 0, "until": FUTURE},
               {"dollars": -3, "until": FUTURE}, {"dollars": True, "until": FUTURE}, {"dollars": "15", "until": FUTURE},
               {"dollars": 15.0, "until": "not a time"}, {"dollars": 15.0, "until": 12})
        for data in bad:
            got = BG.read_cap(self.write(data))
            self.assertEqual("NO-DATA", got.get("verdict"), data)
            self.assertEqual((0.0, True), (got.get("dollars"), got.get("expired")), data)
        got = BG.read_cap(os.path.join(tempfile.mkdtemp(prefix="cap-edge-"), "absent.json"))
        self.assertEqual(("NO-DATA", 0.0, True), (got.get("verdict"), got.get("dollars"), got.get("expired")))

    def test_an_expired_cap_fails_and_funds_nothing(self):
        import burn_guard as BG
        got = BG.read_cap(self.write({"dollars": 15.0, "until": PAST, "reason": "w"}))
        self.assertEqual(("FAIL", 0.0, True), (got.get("verdict"), got.get("dollars"), got.get("expired")))

    def test_a_good_cap_passes_with_its_amount(self):
        import burn_guard as BG
        got = BG.read_cap(self.write({"dollars": 15.0, "until": FUTURE, "reason": "w"}))
        self.assertEqual(("PASS", 15.0, False), (got.get("verdict"), got.get("dollars"), got.get("expired")))


if __name__ == "__main__":
    unittest.main()

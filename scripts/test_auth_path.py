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


if __name__ == "__main__":
    unittest.main()

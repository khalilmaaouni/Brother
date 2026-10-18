#!/usr/bin/env python3
"""S4: the expiry ahead, on both hosts. brother-login save records saved_at beside the login in the same Keychain
item; brother-login status prints the login's age and warns ahead of the documented one year lifetime; the Codex half
(adapters.codex.expiry_claim) reads NO-DATA, because no claim in the Codex auth file bounds a re-login (measured
2026-10-18, docs/plan/specs/S4.md build record).

WHY. 1.1.1 gave the loop a long-lived login, and nothing said when it would lapse: the first sign was every seat of a
night failing at once. The owner's words: the login is secured "Until revoked"; the CLI's page says one year.

The fixtures are test_brother_login's: a FAKE claude, a FAKE reader (a JSON file stands in for the Keychain), main()
driven at its entry point through a pty, and the canary searched for in every stream, argv and file. The clock is
brother_login.CLOCK, pointed at a fake. ONE CONDITION PER FIXTURE: each case breaks one thing.
Run: python3 -B scripts/loop/test_login_expiry_ahead.py"""
import base64, contextlib, io, json, os, sys, unittest
from unittest import mock

HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.insert(0, HERE)
import brother_login as BL  # noqa: E402
import test_brother_login as TBL  # noqa: E402
from adapters import codex as CX  # noqa: E402

CANARY = TBL.CANARY
DAY = 86400
T0 = 1760000000   # a fixed saved_at, epoch seconds
PRESENT = TBL.Status.PRESENT


class Base(TBL.Base, unittest.TestCase):   # TBL.Base is a TestCase; named here so plan_lint's static read sees one
    def setUp(self):
        super().setUp()
        self.addCleanup(setattr, BL, "CLOCK", getattr(BL, "CLOCK", None))
        self.now = T0
        BL.CLOCK = lambda: self.now

    def seed_item(self, saved_at):
        """An item as the reader holds it: the token, then the saved_at field when one is given."""
        self.seed(CANARY if saved_at is None else CANARY + "\nsaved_at=" + saved_at)

    def status(self, at):
        self.now = at
        rc = self.run_main(argv=["status"])
        return rc, self.out, self.err


class ClaudeHost(Base):
    def test_status_prints_the_age(self):
        """REQ-S4A-01: a sign in records saved_at in the same item, and status prints the age from it."""
        self.assertEqual(self.run_main(), 0)
        self.assertEqual(self.stored(), CANARY + "\nsaved_at=%d" % T0)
        rc, out, err = self.status(T0 + 10 * DAY + 5)
        self.assertEqual(rc, 0, err)
        self.assertEqual(out, PRESENT + "\nBrother login saved 10 days ago.\n")
        self.assertEqual(err, "")
        self.assertEqual(self.status(T0 + 10 * DAY + 5)[1], out, "the same status twice prints the same line")

    def test_a_re_save_replaces_saved_at(self):
        self.assertEqual(self.run_main(), 0)
        self.now = T0 + 100 * DAY
        self.assertEqual(self.run_main(), 0)
        self.assertEqual(self.stored(), CANARY + "\nsaved_at=%d" % (T0 + 100 * DAY))
        self.assertIn("saved 0 days ago.", self.status(T0 + 100 * DAY + 60)[1])

    def test_every_caller_still_reads_the_bare_token(self):
        """read_saved is the one route with_login, hold and status take: the saved_at field never reaches them."""
        self.seed_item(str(T0))
        self.assertEqual(BL.read_saved(), CANARY.encode())
        env, fd = BL.with_login({"PATH": "/usr/bin"})
        try:
            self.assertEqual(os.read(fd, 8192), CANARY.encode())
        finally:
            os.close(fd)

    def test_a_lapse_ahead_warns(self):
        """REQ-S4A-02: under WARN_S left of the lifetime, status names the one step; past it, the login has lapsed."""
        self.seed_item(str(T0))
        left = BL.LIFETIME_S - BL.WARN_S
        rc, out, _ = self.status(T0 + left - DAY)   # a day more than the warning window left: no warning
        self.assertEqual(rc, 0)
        self.assertNotIn("renew", out.lower())
        rc, out, _ = self.status(T0 + left + 1)     # one second inside the window
        self.assertEqual(rc, 0)
        self.assertIn("Renew within %d days: run brother-login." % (BL.WARN_S // DAY), out)
        rc, out, err = self.status(T0 + BL.LIFETIME_S + DAY)
        self.assertEqual(rc, 1, "a lapsed login is not a pass")
        self.assertIn("lapsed", err)
        self.assertIn("run brother-login", err)
        self.assertEqual(out, PRESENT + "\n")

    def test_the_warning_reads_its_threshold(self):
        line = BL.age_line(T0, T0 + 50 * DAY, 100 * DAY, 60 * DAY)
        self.assertIn("Renew within 50 days: run brother-login.", line)
        self.assertNotIn("Renew", BL.age_line(T0, T0 + 50 * DAY, 100 * DAY, 40 * DAY))

    def test_bad_saved_at_is_no_data(self):
        """REQ-S4A-03: no field (an item saved before S4), a corrupt one, a future one, zero or not finite: NO-DATA,
        never fresh, and the present login still reads present."""
        for saved_at in (None, "", "abc", "/etc/passwd", str(T0 + DAY), "0", "-5", "nan", "inf", "1e400"):
            if os.path.exists(self.state):
                os.remove(self.state)
            self.seed_item(saved_at)
            rc, out, err = self.status(T0)
            self.assertEqual(rc, 0, (saved_at, err))
            self.assertTrue(out.startswith(PRESENT + "\n"), saved_at)
            self.assertIn("NO-DATA", out, saved_at)
            self.assertNotIn("days ago", out, saved_at)
        for bad in (None, True, b"", "x", float("nan"), T0 + 1):
            self.assertIn("NO-DATA", BL.age_line(bad, T0, BL.LIFETIME_S, BL.WARN_S), bad)

    def test_an_unknown_lifetime_refuses(self):
        for lifetime, warn in ((0, DAY), (-DAY, DAY), (float("nan"), DAY), (True, 0), (None, DAY), (DAY, -1)):
            with self.assertRaises(BL.Failure, msg=(lifetime, warn)):
                BL.age_line(T0, T0 + DAY, lifetime, warn)

    def test_the_lifetime_is_the_documented_year(self):
        self.assertEqual(BL.LIFETIME_S, 365 * DAY)
        self.assertGreater(BL.WARN_S, 0)
        self.assertLess(BL.WARN_S, BL.LIFETIME_S)

class NoTerminalHost(Base):
    """The closer's sandbox refuses a pty ("out of pty devices", 2026-10-18: four cases ERRORED boxed, 10 OK bare).
    status needs no terminal, so it runs for real there; only the sign in skips, by name, as NO-DATA."""
    def setUp(self):
        super().setUp()
        patcher = mock.patch.object(TBL.pty, "openpty", side_effect=OSError("out of pty devices"))
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_status_runs_without_a_terminal(self):
        self.seed_item(str(T0))
        rc, out, err = self.status(T0 + 10 * DAY + 5)
        self.assertEqual((rc, out, err), (0, PRESENT + "\nBrother login saved 10 days ago.\n", ""))

    def test_the_sign_in_skips_as_no_data(self):
        with self.assertRaises(unittest.SkipTest) as ctx:
            self.run_main()
        self.assertIn("NO-DATA", str(ctx.exception))
        self.assertIsNone(self.stored(), "nothing ran")

    def test_the_leak_check_still_runs_without_a_terminal(self):
        with mock.patch.object(BL, "main", lambda *a, **k: print(CANARY) or 0):
            with self.assertRaises(AssertionError):
                self.run_main(argv=["status"])


def jwt(claims):
    part = lambda d: base64.urlsafe_b64encode(json.dumps(d).encode()).decode().rstrip("=")
    return part({"alg": "RS256"}) + "." + part(claims) + "." + "sig" * 40


class CodexHost(Base):
    def test_codex_expiry_is_no_data_until_measured(self):
        """REQ-S4A-04: the file carries exp claims (the tempting guess), and they bound a refresh, not a re-login:
        expiry_claim answers NO-DATA, names the file, and no value of it leaves the function."""
        auth = os.path.join(self.root, "auth.json")
        with open(auth, "w") as fh:
            json.dump({"auth_mode": "chatgpt", "last_refresh": "2026-10-18T00:00:00Z",
                       "tokens": {"id_token": jwt({"exp": T0 + 3600, "iat": T0, "sub": CANARY}),
                                  "access_token": jwt({"exp": T0 + 240 * 3600, "iat": T0, "sub": CANARY}),
                                  "refresh_token": CANARY, "account_id": CANARY}}, fh)
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            verdict = CX.expiry_claim(auth)
            missing = CX.expiry_claim(os.path.join(self.root, "absent.json"))
        self.assertEqual(verdict[0], "NO-DATA", verdict)
        self.assertEqual(len(verdict), 2)
        self.assertIn(auth, verdict[1])
        self.assertIn("re-login", verdict[1])
        self.assertEqual(missing[0], "NO-DATA")
        self.assertIn("absent.json", missing[1])
        everything = repr(verdict) + repr(missing) + out.getvalue() + err.getvalue()
        self.assertNotIn(CANARY, everything)
        for n in (T0 + 3600, T0 + 240 * 3600, 3600, 240 * 3600):
            self.assertNotIn(str(n), everything, "no claim value, and no duration read from one")

    def test_codex_expiry_refuses_a_malformed_file_as_no_data(self):
        auth = os.path.join(self.root, "auth.json")
        with open(auth, "w") as fh:
            fh.write(CANARY + " not json")
        verdict = CX.expiry_claim(auth)
        self.assertEqual(verdict[0], "NO-DATA")
        self.assertNotIn(CANARY, verdict[1])


if __name__ == "__main__":
    unittest.main(verbosity=1)

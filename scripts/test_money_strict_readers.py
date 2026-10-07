#!/usr/bin/env python3
"""Lane X4 (2026-09-27): every money reader in the loop parses ledger, cap, price and budget data through the one strict
parser, proof_ledger.loads, which refuses a repeated JSON member.

Plain json keeps the LAST of repeated members, so a settlement carrying "actual_cost": 9 then "actual_cost": 0 read as a
measured zero, and a cap of 50 then 5000 read as 5000. The money audit of 2026-09-27 (finding 6) closed that for
proof_ledger and openrouter_ledger only; these readers still used plain json. Each reader's refusal is the one it already
gives an unreadable input (NO-DATA, a refusal, an unknown cost), never a smaller amount and never headroom.

One condition per fixture: every refused fixture is its control with ONE member repeated, and the control is shown to
read the money, so the refusal is the repeat's. Every case drives the reader's entry point where it has one (the burn
guard, claude_ledger and loop_intake command lines, the dispatcher, the bridge's main); proof_liability is its caller's
value verbatim. Nothing reaches a network, a key or a real ledger: every root is a scratch directory.
Run from the repository root: python3 -B scripts/test_money_strict_readers.py
"""
import contextlib, io, json, os, shutil, signal, subprocess, sys, tempfile, time, unittest
from types import SimpleNamespace
from unittest import mock

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(HERE)
LOOP = os.path.join(HERE, "loop")
sys.path.insert(0, LOOP)
sys.path.insert(0, REPO)
import loop_receipt as LR  # noqa: E402
import or_ask as A  # noqa: E402
from plugin.runtime.brother.core import openrouter_dispatch as D, openrouter_ledger as L  # noqa: E402

PROOF_KEYS = ("BROTHER_PROOF_PHASE", "BROTHER_PROOF_BASELINE", "BROTHER_PROOF_BASELINE_SHA256", "BROTHER_STOP_HOUR",
              "BROTHER_RUN_DIR", "BROTHER_DISPATCH_RESERVATION")
LIVE = '"until": "2999-01-01T00:00:00+00:00"'
EXPIRED = '"until": "2020-01-01T00:00:00+00:00"'


def clean_env(**extra):
    e = {k: v for k, v in os.environ.items() if k not in PROOF_KEYS}
    e.update(extra)
    return e


class Scratch(unittest.TestCase):
    def setUp(self):
        self.st = tempfile.mkdtemp(prefix="money-strict-")
        self.addCleanup(shutil.rmtree, self.st, True)
        p = mock.patch.dict(os.environ, {}); p.start(); self.addCleanup(p.stop)
        for k in PROOF_KEYS:
            os.environ.pop(k, None)

    def put(self, name, text):
        path = os.path.join(self.st, name)
        with open(path, "w", encoding="utf-8") as f:
            f.write(text)
        return path


class BurnGuard(Scratch):
    """The funding decision: ledger rows, the cap grant and the configured cap it returns to."""
    TOOL = os.path.join(LOOP, "burn_guard.py")
    RESERVE = '{"type": "RESERVE", "reservation_id": "r1", "estimated_cost": 1, "at": 1}'
    SETTLED = '{"type": "RECONCILE", "reservation_id": "r1", "actual_cost": 9, "at": 2}'
    OPEN = '{"type": "RESERVE", "reservation_id": "r2", "estimated_cost": 9, "at": 3}'

    def setUp(self):
        super().setUp()
        self.put("openrouter-ledger.jsonl", "\n".join((self.RESERVE, self.SETTLED, self.OPEN)) + "\n")
        self.put("cap-grant.json", '{"daily_cap": 50, %s}' % LIVE)

    def guard(self):
        return subprocess.run([sys.executable, "-B", self.TOOL], env=clean_env(BROTHER_OR_STATE_ROOT=self.st),
                              capture_output=True, text=True, timeout=120)

    def refused(self, r, reason):
        out = r.stdout + r.stderr
        self.assertNotIn("Traceback", out, out)
        self.assertEqual((r.returncode, r.stdout.strip().splitlines()[-1]), (3, "0"), out)
        self.assertIn("NO-DATA: %s unreadable" % reason, r.stdout, out)
        self.assertNotIn("MONEY", r.stdout, out)

    def test_the_control_funds_and_counts_both_figures(self):
        r = self.guard()
        self.assertEqual((r.returncode, r.stdout.strip().splitlines()[-1]), (0, "8"), r.stdout + r.stderr)
        self.assertIn("spent 9.00 + 9.00 unresolved (abandoned) of 50.00 USD", r.stdout)

    def test_a_repeated_actual_cost_is_an_unreadable_ledger(self):
        self.put("openrouter-ledger.jsonl", "\n".join((self.RESERVE, self.SETTLED.replace(
            '"actual_cost": 9', '"actual_cost": 9, "actual_cost": 0'), self.OPEN)) + "\n")
        self.refused(self.guard(), "ledger")

    def test_a_repeated_estimated_cost_is_an_unreadable_ledger(self):
        self.put("openrouter-ledger.jsonl", "\n".join((self.RESERVE, self.SETTLED, self.OPEN.replace(
            '"estimated_cost": 9', '"estimated_cost": 9, "estimated_cost": 0'))) + "\n")
        self.refused(self.guard(), "ledger")

    def test_a_repeated_grant_cap_is_an_unreadable_grant(self):
        self.put("cap-grant.json", '{"daily_cap": 50, "daily_cap": 5000, %s}' % LIVE)
        self.refused(self.guard(), "cap grant")

    def test_the_configured_cap_an_expired_grant_returns_to_is_read(self):
        self.put("cap-grant.json", '{"daily_cap": 500, %s}' % EXPIRED)
        self.put("dispatch-limits.json", '{"daily_cap": 50}')
        r = self.guard()
        self.assertEqual(r.stdout.strip().splitlines()[-1], "8", r.stdout + r.stderr)
        self.assertIn("of 50.00 USD", r.stdout)

    def test_a_repeated_configured_cap_is_an_unreadable_grant(self):
        self.put("cap-grant.json", '{"daily_cap": 500, %s}' % EXPIRED)
        self.put("dispatch-limits.json", '{"daily_cap": 50, "daily_cap": 5000}')
        self.refused(self.guard(), "cap grant")


class ClaudeLedger(Scratch):
    """The Claude call ledger's one reader: a repeated cost on a DONE row is an unreadable row, never a measured zero."""
    TOOL = os.path.join(LOOP, "claude_ledger.py")
    START = '{"call": "c1", "at": "2099-01-01T00:00:00+00:00"}'
    DONE = '{"call": "c1", "event": "done", "cost_usd": 9, "at": "2099-01-01T00:00:01+00:00"}'
    REPEATED = DONE.replace('"cost_usd": 9', '"cost_usd": 9, "cost_usd": 0')

    def tally(self, done):
        path = self.put("claude-calls.jsonl", self.START + "\n" + done + "\n")
        r = subprocess.run([sys.executable, "-B", self.TOOL, "tally", path, "2000-01-01T00:00:00+00:00"],
                           env=clean_env(), capture_output=True, text=True, timeout=60)
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        return dict(line.split(" ", 1) for line in r.stdout.splitlines())

    def test_the_control_is_costed(self):
        self.assertEqual(self.tally(self.DONE), {"USD": "9.0000", "NOTE": ""})

    def test_a_repeated_cost_reads_no_data_never_a_zero(self):
        out = self.tally(self.REPEATED)
        self.assertTrue(out["NOTE"].startswith("NO-DATA"), out)
        self.assertIn("1 unreadable ledger row(s)", out["NOTE"])
        self.assertIn("1 Claude call(s) without a known cost", out["NOTE"])

    def test_a_ledger_with_a_repeated_cost_admits_no_new_call(self):
        import claude_ledger as CL
        for done, admits in ((self.DONE, True), (self.REPEATED, False)):
            path = self.put("claude-calls.jsonl", self.START + "\n" + done + "\n")
            with self.subTest(admits=admits):
                if admits:
                    self.assertEqual(CL.start(path, {"call": "c2"}, 60, env={})["call"], "c2")
                else:
                    with self.assertRaises(CL.proof_ledger.EvidenceError) as e:
                        CL.start(path, {"call": "c2"}, 60, env={})
                    self.assertIn("unreadable_rows=1", str(e.exception))


class ProofLiability(Scratch):
    """The liability a receipt records outside a proof phase (loop_receipt.proof_finish stores it verbatim)."""

    def liability(self, *rows):
        return LR.proof_liability(self.put("openrouter-ledger.jsonl", "".join(r + "\n" for r in rows)))

    def test_the_control_counts_an_open_estimate(self):
        got = self.liability('{"type": "RESERVE", "reservation_id": "a", "estimated_cost": 9}')
        self.assertEqual((got["unknown_cost_calls"], got["reserved_liability_usd"]), (1, 9))

    def test_a_repeated_estimate_is_no_data_never_a_smaller_liability(self):
        got = self.liability('{"type": "RESERVE", "reservation_id": "a", "estimated_cost": 9, "estimated_cost": 0}')
        self.assertTrue(str(got).startswith("NO-DATA"), got)

    def test_a_repeated_measured_cost_is_no_data(self):
        got = self.liability('{"type": "RESERVE", "reservation_id": "a", "estimated_cost": 9}',
                             '{"type": "RECONCILE", "reservation_id": "a", "actual_cost": 9, "actual_cost": 0}')
        self.assertTrue(str(got).startswith("NO-DATA"), got)

    def test_a_liability_that_sums_past_the_float_range_is_no_data(self):
        one = '{"type": "RESERVE", "reservation_id": "%s", "estimated_cost": 1e308}'
        self.assertEqual(self.liability(one % "a")["reserved_liability_usd"], 1e308)
        got = self.liability(one % "a", one % "b")
        self.assertTrue(str(got).startswith("NO-DATA"), got)


class DispatchLimits(Scratch):
    """The dispatcher's configured cap. An unreadable limits file already means the defaults (20 USD): a repeated
    member is one, so a cap of 50 then 5000 never admits a call the 5000 would have funded."""

    def dispatch(self, cost):
        with mock.patch.object(D, "run_strict", side_effect=D.FallbackDetected("fixture")) as call:
            try:
                D.dispatch(["never-executed"], "fixture/model", estimated_cost=cost, holder_id="fixture",
                           state_root=self.st)
            except D.FallbackDetected:
                pass
            return call.called

    def test_the_control_cap_is_read_and_admits_under_it(self):
        self.put("dispatch-limits.json", '{"daily_cap": 50}')
        with contextlib.redirect_stderr(io.StringIO()):
            self.assertEqual(D.effective_limits(self.st).daily_cap_usd, 50)
            self.assertTrue(self.dispatch(30))

    def test_a_repeated_cap_is_unreadable_and_the_default_refuses(self):
        self.put("dispatch-limits.json", '{"daily_cap": 50, "daily_cap": 5000}')
        err = io.StringIO()
        with contextlib.redirect_stderr(err):
            limits = D.effective_limits(self.st)
            self.assertEqual((limits.daily_cap_usd, limits.source), (20, "default"))
            with self.assertRaises(L.BudgetExceeded):
                self.dispatch(30)
        self.assertIn("deployment limits ignored", err.getvalue())


class PriceCatalog(Scratch):
    """The saved OpenRouter catalog prices a call whose bridge printed no billed line: a repeated price is an
    unreadable catalog, so the call is unmeasured, never a measured zero."""
    USAGE = SimpleNamespace(stderr="[usage] prompt=1000 completion=1000 model=m\n")

    def measured(self, pricing):
        self.put("openrouter-models.json", '{"data": [{"id": "m", "pricing": {%s}}]}' % pricing)
        return D.measured_cost(self.USAGE, self.st)

    def test_the_control_is_priced(self):
        self.assertEqual(self.measured('"prompt": "0.001", "completion": "0"'), (1.0, None))

    def test_a_repeated_price_is_unmeasured_never_zero(self):
        usd, why = self.measured('"prompt": "0.001", "prompt": "0", "completion": "0"')
        self.assertIsNone(usd)
        self.assertIn("pricing unavailable", why)

    def test_a_repeated_price_is_the_catalogs_own_refusal(self):
        # measured_cost catches everything, so this pins load_catalog's contract: an unreadable catalog is CatalogError
        from plugin.runtime.brother.core import openrouter_prices as P
        path = self.put("openrouter-models.json", '{"data": [{"id": "m", "pricing": {"prompt": "1", "prompt": "0"}}]}')
        with self.assertRaises(P.CatalogError):
            P.load_catalog(path)


class Bridge(Scratch):
    """The loop's bridge reads OpenRouter's own charge off the reply: a reply with a repeated charge is a lost reply,
    an attempt of unknown cost (known=no), never a known smaller charge."""

    def reply(self, usage):
        return ('{"model": "deepseek/deepseek-v4.1-flash", "usage": {%s}, '
                '"choices": [{"message": {"content": "12"}, "finish_reason": "stop"}]}' % usage).encode()

    def run_bridge(self, body):
        out, err = io.StringIO(), io.StringIO()
        old = signal.getsignal(signal.SIGTERM)
        try:
            with mock.patch.object(sys, "argv", ["or_ask.py", "--model", "deepseek", "--timeout", "60", "--", "7+5"]), \
                    mock.patch.object(A, "read_key", return_value="offline-fixture"), \
                    mock.patch.object(A.urllib.request, "urlopen", lambda req, timeout: io.BytesIO(body)), \
                    contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
                rc = A.main()
        finally:
            signal.signal(signal.SIGTERM, old)
        return rc, err.getvalue()

    def test_the_control_charge_is_known(self):
        before = list(sys.path)
        rc, err = self.run_bridge(self.reply('"prompt_tokens": 1, "completion_tokens": 1, "cost": 9'))
        self.assertEqual(rc, 0, err)
        self.assertIn("[billed] usd=9.000000 attempts=1 known=yes", err)
        self.assertEqual(sys.path, before, "the bridge's colocated import must leave an importer's sys.path as it was")

    def test_a_repeated_charge_is_an_unknown_bill(self):
        rc, err = self.run_bridge(self.reply('"prompt_tokens": 1, "completion_tokens": 1, "cost": 9, "cost": 0'))
        self.assertEqual(rc, 44, err)
        self.assertNotIn("known=yes", err)
        self.assertRegex(err, r"\[billed\] usd=0\.000000 attempts=\d+ known=no")


class IntakeBudget(Scratch):
    """The run budget the running driver reads each pass (loop_intake.py status --running)."""
    TOOL = os.path.join(LOOP, "loop_intake.py")

    def running(self, record):
        os.makedirs(os.path.join(self.st, "loop-intake"))
        self.put(os.path.join("loop-intake", "CURRENT.json"), record)
        return subprocess.run([sys.executable, "-B", self.TOOL, "status", "--running"],
                              env=clean_env(INTAKE_EVIDENCE=self.st), capture_output=True, text=True, timeout=60)

    def record(self, budget):
        return '{"verdict": "READY", "epoch": %f, "at": "t", %s, "deadline": "23:59"}' % (time.time(), budget)

    def test_the_control_budget_is_read(self):
        r = self.running(self.record('"budget_usd": 0.5'))
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertIn("BUDGET_USD 0.50", r.stdout.splitlines())

    def test_a_repeated_budget_is_an_unreadable_record(self):
        r = self.running(self.record('"budget_usd": 0.5, "budget_usd": 5000'))
        self.assertEqual(r.returncode, 1, r.stdout + r.stderr)
        self.assertIn("UNREADABLE: there is no readable intake record", r.stdout)
        self.assertFalse(any(l.startswith("BUDGET_USD") for l in r.stdout.splitlines()), r.stdout)


if __name__ == "__main__":
    unittest.main()

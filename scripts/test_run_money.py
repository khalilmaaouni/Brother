#!/usr/bin/env python3
"""A run's own money (owner, 2026-09-27): each run has its own budget and ledger, nothing carries over, OpenRouter's own
balance is checked, and every zero names its cause. Each case isolates ONE condition and runs the real entry point:
burn_guard's command line against a run money root, the dispatcher's admission (openrouter_ledger.reserve with the cap
effective_limits gives), and the provider balance reader. Nothing touches the network or the real home.
Run: python3 -B scripts/test_run_money.py"""
import datetime, json, os, shutil, subprocess, sys, tempfile, time, unittest

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(HERE)
sys.path.insert(0, REPO)
sys.path.insert(0, os.path.join(HERE, "loop"))
from plugin.runtime.brother.core import openrouter_ledger as L  # noqa: E402
from plugin.runtime.brother.core import openrouter_dispatch as D  # noqa: E402
import or_balance  # noqa: E402

GUARD = os.path.join(HERE, "loop", "burn_guard.py")
NOW = time.time()
LATER = datetime.datetime.fromtimestamp(NOW + 6 * 3600).astimezone().isoformat(timespec="seconds")
EARLIER = datetime.datetime.fromtimestamp(NOW - 60).astimezone().isoformat(timespec="seconds")


class RunMoney(unittest.TestCase):
    def setUp(self):
        self.d = tempfile.mkdtemp(prefix="run-money-")
        self.root = os.path.join(self.d, "run-t", "money")
        self.balance = os.path.join(self.d, "balance.json")
        self.provider(total=90.0, used=5.0)

    def tearDown(self):
        shutil.rmtree(self.d, ignore_errors=True)

    def provider(self, total, used, key_remaining=None):
        with open(self.balance, "w") as f:
            json.dump({"total_credits": total, "total_usage": used, "limit_remaining": key_remaining}, f)

    def budget(self, usd=100.0, until=LATER):
        L.write_run_budget(self.root, "run-t", usd, until)

    def rows(self, *rows, root=None):
        root = root or self.root
        os.makedirs(root, exist_ok=True)
        with open(os.path.join(root, "openrouter-ledger.jsonl"), "a") as f:
            for r in rows:
                f.write(json.dumps(r) + "\n")

    def reserve(self, rid, usd, at=NOW):
        return {"type": "RESERVE", "reservation_id": rid, "holder_id": "h-" + rid, "estimated_cost": usd, "at": at}

    def guard(self, **env):
        e = dict(os.environ, HOME=self.d, BROTHER_OR_STATE_ROOT=self.root, BROTHER_OR_BALANCE_FILE=self.balance)
        for k in ("BROTHER_PROOF_PHASE", "BROTHER_PROOF_BASELINE", "BROTHER_PROOF_BASELINE_SHA256", "BROTHER_CODE_ROOT"):
            e.pop(k, None)
        e.update(env)
        r = subprocess.run([sys.executable, "-B", GUARD], cwd=REPO, env=e, capture_output=True, text=True, timeout=120)
        return r, r.stdout.strip().splitlines()[-1] if r.stdout.strip() else ""

    # --- the burn guard over a run's own root
    def test_a_fresh_run_is_funded_from_its_own_budget(self):
        self.budget()
        r, lanes = self.guard()
        self.assertIn("FUNDING OK", r.stdout, r.stdout + r.stderr)
        self.assertIn("of 100.00 USD", r.stdout)
        self.assertIn("run run-t", r.stdout)
        self.assertNotEqual(lanes, "0")

    def test_an_abandoned_call_is_counted_once_and_does_not_stop_the_run(self):
        self.budget()
        self.rows(self.reserve("a", 0.05), {"type": "ABANDONED", "reservation_id": "a", "reason": "post_dispatch.TimeoutExpired", "at": NOW})
        r, lanes = self.guard()
        self.assertIn("0.05 abandoned at estimate", r.stdout, r.stdout + r.stderr)
        self.assertIn("FUNDING OK", r.stdout)
        self.assertNotEqual(lanes, "0")

    def test_a_run_budget_below_the_default_cap_is_the_cap(self):
        self.budget(usd=5.0)
        r, _ = self.guard()
        self.assertIn("of 5.00 USD", r.stdout, r.stdout + r.stderr)

    def test_a_run_past_its_deadline_is_expired(self):
        self.budget(until=EARLIER)
        r, lanes = self.guard()
        self.assertIn("FUNDING EXPIRED", r.stdout, r.stdout + r.stderr)
        self.assertEqual(lanes, "0")

    def test_a_committed_budget_is_spent_never_no_data(self):
        self.budget(usd=10.0)
        self.rows(self.reserve("s", 9.5), {"type": "RECONCILE", "reservation_id": "s", "actual_cost": 9.5, "at": NOW})
        r, lanes = self.guard()
        self.assertIn("FUNDING SPENT", r.stdout, r.stdout + r.stderr)
        self.assertNotIn("NO-DATA", r.stdout)
        self.assertEqual(lanes, "0")

    def test_openrouter_unable_to_pay_is_provider_low(self):
        self.budget()
        self.provider(total=10.0, used=9.5)
        r, lanes = self.guard()
        self.assertIn("FUNDING PROVIDER-LOW", r.stdout, r.stdout + r.stderr)
        self.assertEqual(lanes, "0")

    def test_a_key_cap_below_the_account_is_what_is_available(self):
        self.budget()
        self.provider(total=90.0, used=5.0, key_remaining=0.5)
        r, lanes = self.guard()
        self.assertIn("FUNDING PROVIDER-LOW", r.stdout, r.stdout + r.stderr)

    def test_an_unreadable_provider_balance_never_stops_the_run(self):
        self.budget()
        r, lanes = self.guard(BROTHER_OR_BALANCE_FILE=os.path.join(self.d, "missing.json"))
        self.assertIn("PROVIDER NO-DATA", r.stdout, r.stdout + r.stderr)
        self.assertIn("FUNDING OK", r.stdout)
        self.assertNotEqual(lanes, "0")

    def test_another_ledger_never_reaches_this_run(self):
        self.budget()
        shared = os.path.join(self.d, "shared")
        self.rows(self.reserve("x", 50.0), {"type": "ABANDONED", "reservation_id": "x", "at": NOW},
                  self.reserve("y", 500.0), {"type": "RECONCILE", "reservation_id": "y", "actual_cost": 500.0, "at": NOW}, root=shared)
        r, lanes = self.guard()
        self.assertIn("MONEY   spent 0.00 + 0.00 unresolved", r.stdout, r.stdout + r.stderr)
        self.assertIn("FUNDING OK", r.stdout)

    def test_spend_from_an_earlier_utc_day_of_the_same_run_still_counts(self):
        self.budget()
        yesterday = NOW - 26 * 3600
        self.rows(self.reserve("o", 60.0, at=yesterday), {"type": "RECONCILE", "reservation_id": "o", "actual_cost": 60.0, "at": yesterday})
        r, _ = self.guard()
        self.assertIn("headroom 40.00", r.stdout, r.stdout + r.stderr)

    def test_a_corrupt_run_budget_is_no_data_and_admits_nothing(self):
        os.makedirs(self.root)
        with open(os.path.join(self.root, L.RUN_BUDGET_FILENAME), "w") as f:
            f.write('{"schema": "run-budget-v1", "run_id": "run-t", "openrouter_usd": "lots"}')
        r, lanes = self.guard()
        self.assertIn("FUNDING NO-DATA", r.stdout, r.stdout + r.stderr)
        self.assertEqual(lanes, "0")
        self.assertEqual(D.effective_limits(self.root).daily_cap_usd, 0.0)

    # --- the dispatcher's own admission, the control that actually refuses a call
    def test_admission_refuses_past_the_run_budget_across_calls(self):
        self.budget(usd=100.0)
        cap = lambda now: D.effective_limits(self.root, datetime.datetime.fromtimestamp(now, datetime.timezone.utc)).daily_cap_usd
        L.reserve(self.root, cap, 60.0, "h1")
        with self.assertRaises(L.BudgetExceeded):
            L.reserve(self.root, cap, 50.0, "h2")

    def test_admission_after_the_deadline_refuses(self):
        self.budget(until=EARLIER)
        cap = lambda now: D.effective_limits(self.root, datetime.datetime.fromtimestamp(now, datetime.timezone.utc)).daily_cap_usd
        with self.assertRaises(L.BudgetExceeded):
            L.reserve(self.root, cap, 0.01, "h1")

    def test_a_shared_root_keeps_its_day_scope(self):
        shared = os.path.join(self.d, "shared")
        yesterday = NOW - 26 * 3600
        self.rows(self.reserve("o", 60.0, at=yesterday), {"type": "RECONCILE", "reservation_id": "o", "actual_cost": 60.0, "at": yesterday}, root=shared)
        self.assertEqual(L.current_spend(shared), 0.0)

    # --- the provider reader
    def test_a_fresh_reading_is_reused_and_a_failure_is_never_cached(self):
        calls = []
        def fetch():
            calls.append(1)
            return {"credits": {"total_credits": 10.0, "total_usage": 2.0}, "key": {"limit_remaining": None}}
        os.makedirs(self.root)
        self.assertEqual(or_balance.read(self.root, NOW, fetch)["available"], 8.0)
        self.assertEqual(or_balance.read(self.root, NOW + 5, fetch)["available"], 8.0)
        self.assertEqual(len(calls), 1)
        root2 = os.path.join(self.d, "r2"); os.makedirs(root2)
        def boom():
            raise OSError("offline")
        self.assertEqual(or_balance.read(root2, NOW, boom)["status"], "NO-DATA")
        self.assertFalse(os.path.exists(os.path.join(root2, or_balance.CACHE)))

    def test_a_bool_or_negative_balance_is_no_data(self):
        os.makedirs(self.root)
        for bad in ({"total_credits": True, "total_usage": 0}, {"total_credits": 1.0, "total_usage": -1.0}):
            got = or_balance.read(self.root, NOW, lambda bad=bad: {"credits": bad, "key": {}})
            self.assertEqual(got["status"], "NO-DATA", bad)


if __name__ == "__main__":
    unittest.main()

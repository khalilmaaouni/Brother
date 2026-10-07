"""F3 and F4 (D-22): a bad number or a malformed command line reaches the burn guard's refusal, never a traceback.

Reproduced 2026-09-26 on both interpreters. F3: a JSON integer past the float range (10**400) in the ledger or the
grant raised OverflowError out of math.isfinite, exit 1 with a traceback and no lane count. F4: `--planned foo` and
a bare `--stop-hour` raised out of the argument lambda, which sat outside every refusal handler. Both already funded
nothing, but a crash is not a refusal: the driver reads the LAST line as the lane count, and a traceback's last line
is not one. Also measured on the base: `--planned 10**400` printed a 401 digit lane count at exit 0.

Every refusal here must read: exit 3, last line 0, a NO-DATA reason naming the bad input, no traceback. Each fixture
changes ONE input from a state that funds (TheCleanStateFunds proves that state funds), so the refusal is that
input's. D-22 negative control: an expired grant returns to the CONFIGURED baseline cap, never a hardcoded 20 that
would lift a lower configured limit. Every case runs the real entry point in a subprocess.
"""
import json, os, shutil, subprocess, sys, tempfile, unittest
HERE = os.path.dirname(os.path.abspath(__file__))
TOOL = os.path.join(HERE, "loop", "burn_guard.py")
HUGE = "1" + "0" * 400          # 10**400: valid JSON, past the float range, far under the 4300 digit int limit
BAD_AMOUNTS = (HUGE, "-" + HUGE, "1e309", "-1e309", "NaN", "Infinity", "-1", '"5"', "null", "true", "[]")
BAD_CLI = ("foo", "", "1e309", "inf", "nan", "2.5", "-1", HUGE, "-" + HUGE, "0x10")
PROOF_KEYS = ("BROTHER_PROOF_PHASE", "BROTHER_PROOF_BASELINE", "BROTHER_PROOF_BASELINE_SHA256", "BROTHER_STOP_HOUR")
EXPIRED = '"until": "2020-01-01T00:00:00+00:00"'
LIVE = '"until": "2999-01-01T00:00:00+00:00"'


class Guard(unittest.TestCase):
    def setUp(self):
        self.st = tempfile.mkdtemp(prefix="burn-refusal-")
        self.addCleanup(shutil.rmtree, self.st, True)
        self.put("openrouter-ledger.jsonl", "")
        self.put("cap-grant.json", '{"daily_cap": 20}')

    def put(self, name, text):
        with open(os.path.join(self.st, name), "w", encoding="utf-8") as f:
            f.write(text)

    def guard(self, *args, **env):
        e = {k: v for k, v in os.environ.items() if k not in PROOF_KEYS}
        e.update(BROTHER_OR_STATE_ROOT=self.st, **env)
        return subprocess.run([sys.executable, "-B", TOOL] + list(args), env=e, capture_output=True, text=True,
                              timeout=120)

    def refused(self, r, *reason):
        out = r.stdout + r.stderr
        self.assertNotIn("Traceback", out, out)
        self.assertEqual(r.returncode, 3, out)
        self.assertEqual(r.stdout.strip().splitlines()[-1], "0", out)
        self.assertIn("NO-DATA", r.stdout, out)
        self.assertNotIn("MONEY", r.stdout, out)
        for word in reason:
            self.assertIn(word, r.stdout, out)

    def funded(self, r, lanes="8", *shown):
        out = r.stdout + r.stderr
        self.assertNotIn("Traceback", out, out)
        self.assertEqual(r.returncode, 0 if lanes != "0" else 3, out)
        self.assertEqual(r.stdout.strip().splitlines()[-1], lanes, out)
        for word in shown:
            self.assertIn(word, r.stdout, out)


class TheCleanStateFunds(Guard):
    def test_the_fixture_funds_before_any_input_is_spoiled(self):
        self.funded(self.guard(), "8", "of 20.00 USD")

    def test_the_drivers_own_stop_hour_call_still_funds(self):
        self.funded(self.guard("--stop-hour", "10"), "8", "to 10:00")

    def test_a_valid_planned_width_is_still_honoured(self):
        self.funded(self.guard("--planned", "3"), "3")
        self.funded(self.guard("--planned", "24"), "24")

    def test_the_stop_hour_range_ends_are_still_accepted(self):
        self.funded(self.guard("--stop-hour", "0"), "8", "to 00:00")
        self.funded(self.guard("--stop-hour", "24"), "8", "to 24:00")


class F3HugeNumbersInTheLedger(Guard):
    def test_every_bad_actual_cost_refuses(self):
        for v in BAD_AMOUNTS:
            with self.subTest(actual_cost=v[:12]):
                self.put("openrouter-ledger.jsonl", '{"type": "RECONCILE", "actual_cost": %s}\n' % v)
                self.refused(self.guard(), "ledger unreadable")

    def test_every_bad_estimated_cost_refuses(self):
        for v in BAD_AMOUNTS:
            with self.subTest(estimated_cost=v[:12]):
                self.put("openrouter-ledger.jsonl",
                         '{"type": "RESERVE", "reservation_id": "r1", "estimated_cost": %s}\n' % v)
                self.refused(self.guard(), "ledger unreadable")

    def test_a_huge_actual_cost_names_the_amount_not_a_crash(self):
        self.put("openrouter-ledger.jsonl", '{"type": "RECONCILE", "actual_cost": %s}\n' % HUGE)
        self.refused(self.guard(), "ledger unreadable", "finite")


class F3HugeNumbersInTheGrant(Guard):
    def test_every_bad_grant_cap_refuses(self):
        for v in BAD_AMOUNTS:
            with self.subTest(daily_cap=v[:12]):
                self.put("cap-grant.json", '{"daily_cap": %s}' % v)
                self.refused(self.guard(), "cap grant unreadable")

    def test_a_huge_grant_cap_names_the_amount_not_a_crash(self):
        self.put("cap-grant.json", '{"daily_cap": %s}' % HUGE)
        self.refused(self.guard(), "cap grant unreadable", "finite")


class F4MalformedCommandLine(Guard):
    def test_a_flag_with_no_value_refuses_naming_the_flag(self):
        for flag in ("--stop-hour", "--planned"):
            with self.subTest(flag=flag):
                self.refused(self.guard(flag), "command line", flag, "needs a value")

    def test_every_bad_value_refuses_naming_flag_and_value(self):
        for flag in ("--stop-hour", "--planned"):
            for v in BAD_CLI:
                with self.subTest(flag=flag, value=v[:12]):
                    self.refused(self.guard(flag, v), "command line", flag, repr(v[:12]).strip("'"))

    def test_a_value_out_of_range_refuses(self):
        for flag, v in (("--stop-hour", "25"), ("--planned", "25"), ("--planned", "99999999999999")):
            with self.subTest(flag=flag, value=v):
                self.refused(self.guard(flag, v), "command line", flag, v)

    def test_a_flag_given_twice_refuses_rather_than_picking_one(self):
        self.refused(self.guard("--planned", "3", "--planned", "20"), "command line", "--planned", "more than once")

    def test_a_flag_swallowed_as_a_value_refuses(self):
        self.refused(self.guard("--stop-hour", "--planned", "3"), "command line", "--stop-hour", "--planned")


class TheEnvironmentStopHourKeepsItsDocumentedFallback(Guard):
    """Not a refusal, by design: the driver exports BROTHER_STOP_HOUR and an unreadable value keeps the night's 07:00
    (the source says so). The stop hour sizes nothing any more, so this path must merely never crash."""

    def test_every_bad_environment_hour_falls_back_without_a_traceback(self):
        for v in BAD_CLI + ("25",):
            with self.subTest(hour=v[:12]):
                self.funded(self.guard(BROTHER_STOP_HOUR=v), "8", "to 07:00")


class D22TheConfiguredLimitIsTheBaseline(Guard):
    def test_an_expired_grant_never_lifts_a_lower_configured_limit_to_20(self):
        # Negative control: 4.5 spent of a configured 5 leaves 0.50, under the reserve. A hardcoded 20 funded 8.
        self.put("dispatch-limits.json", '{"daily_cap": 5}')
        self.put("cap-grant.json", '{"daily_cap": 999, %s}' % EXPIRED)
        self.put("openrouter-ledger.jsonl", '{"type": "RECONCILE", "actual_cost": 4.5}\n')
        self.funded(self.guard(), "0", "of 5.00 USD", "headroom 0.50")

    def test_an_expired_grant_returns_to_a_higher_configured_baseline(self):
        self.put("dispatch-limits.json", '{"daily_cap": 50}')
        self.put("cap-grant.json", '{"daily_cap": 999, %s}' % EXPIRED)
        self.put("openrouter-ledger.jsonl", '{"type": "RECONCILE", "actual_cost": 30}\n')
        self.funded(self.guard(), "8", "of 50.00 USD")

    def test_no_configured_limit_keeps_the_dispatchers_default(self):
        self.put("cap-grant.json", '{"daily_cap": 999, %s}' % EXPIRED)
        self.funded(self.guard(), "8", "of 20.00 USD")
        self.put("dispatch-limits.json", '{"max_slots": 2}')
        self.funded(self.guard(), "8", "of 20.00 USD")

    def test_a_live_grant_is_unchanged_by_the_configured_limit(self):
        self.put("dispatch-limits.json", '{"daily_cap": 5}')
        self.put("cap-grant.json", '{"daily_cap": 30, %s}' % LIVE)
        self.funded(self.guard(), "8", "of 30.00 USD")

    def test_a_corrupt_configured_limit_refuses_rather_than_reading_as_20(self):
        self.put("cap-grant.json", '{"daily_cap": 999, %s}' % EXPIRED)
        for text in ['{"daily_cap": %s}' % v for v in BAD_AMOUNTS] + ["[]", "{", ""]:
            with self.subTest(limits=text[:24]):
                self.put("dispatch-limits.json", text)
                self.refused(self.guard(), "deployment limits unreadable")


class TheLastRefusalCatchesWhatNoHandlerNamed(Guard):
    """The backstop's own fixture: JSON nested past the recursion limit raises RecursionError, which no per input
    handler lists. Only the entry point's refusal can turn it into 0 lanes."""

    def test_a_ledger_nested_past_the_recursion_limit_refuses(self):
        self.put("openrouter-ledger.jsonl", "[" * 200000 + "\n")
        self.refused(self.guard(), "RecursionError")


sys.path[:0] = [HERE, os.path.join(HERE, "loop"), os.path.dirname(HERE)]
import test_proof_burn_accounting as T  # noqa: E402  (the proof fixture: baseline, start observation, state root)


class F3HugeNumbersOnTheProofPath(T.Funding):
    """The proof path reads amounts through proof_ledger.amount, which has the same math.isfinite overflow. The
    guard's proof handler must turn it into its named refusal rather than a traceback."""

    def test_a_huge_proof_reservation_refuses_naming_proof_funding(self):
        row = dict(type="RESERVE", reservation_id="big", holder_id="holder", at=86399, run_id="RB", attempt_id="a" * 32)
        with open(str(self.ledger), "a", encoding="utf-8") as f:
            f.write(json.dumps(row)[:-1] + ', "estimated_cost": %s}\n' % HUGE)
        r = self.cli()
        out = r.stdout + r.stderr
        self.assertNotIn("Traceback", out, out)
        self.assertEqual(r.returncode, 3, out)
        self.assertEqual(r.stdout.strip().splitlines()[-1], "0", out)
        self.assertIn("NO-DATA: proof funding unreadable", r.stdout, out)


if __name__ == "__main__":
    loader = unittest.TestLoader()
    mine = [TheCleanStateFunds, F3HugeNumbersInTheLedger, F3HugeNumbersInTheGrant, F4MalformedCommandLine,
            TheEnvironmentStopHourKeepsItsDocumentedFallback, D22TheConfiguredLimitIsTheBaseline,
            TheLastRefusalCatchesWhatNoHandlerNamed]
    suite = unittest.TestSuite(loader.loadTestsFromTestCase(c) for c in mine)
    # Only this file's proof case: the inherited Funding cases are test_proof_burn_accounting's own, run there.
    suite.addTests(F3HugeNumbersOnTheProofPath(n) for n in loader.getTestCaseNames(F3HugeNumbersOnTheProofPath)
                   if n in F3HugeNumbersOnTheProofPath.__dict__)
    sys.exit(not unittest.TextTestRunner(verbosity=1).run(suite).wasSuccessful())

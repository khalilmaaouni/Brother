"""The bridge's [billed] line fails closed (money audit 2026-09-27, findings 1, 3 and 7).

The dispatcher settles a proof call at the bridge's billed figure only when the line says known=yes, so every way the
bridge can lose or distort a charge must leave known=no: a reply lost after the request went out (the provider may
already have charged), a negative or non finite per attempt charge (it would offset or poison the sum), and a decision
call, which used to print no billed line at all even when OpenRouter returned its cost. Driven in process through
or_ask.main with urlopen and the keychain replaced (test_or_ask's own fakes), so no key is read and nothing leaves the
machine. One condition per case.

Run from the repository root: python3 -B scripts/loop/test_or_ask_billing.py
"""
import http.client, os, sys, unittest, urllib.error

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import test_or_ask as T   # a module, so its own test classes are not collected here again


class ALostReplyIsAnUnknownBill(unittest.TestCase):
    """Finding 1: a transport exception skipped the attempt count and left known=yes, so a lost reply read as zero."""

    def test_a_dropped_reply_then_an_answer_is_a_floor_not_the_charge(self):
        fake = T.Fake([http.client.IncompleteRead(b"partial billed response"), T.billed("second", "4", 0.01)])
        code, _out, err = T.run(T.load(), fake, [])
        self.assertEqual(code, 0, err)
        self.assertIn("[billed] usd=0.010000 attempts=2 known=no", err)

    def test_the_audit_shape_no_answer_at_all_is_unknown_never_zero(self):
        """The repro: the reply is lost, the fallback answers empty with cost 0, and the call used to settle at 0."""
        mod = T.load()
        empty = {"model": mod.FALLBACK_MODELS[0], "usage": {"cost": 0}, "choices": []}
        code, _out, err = T.run(mod, T.Fake([http.client.IncompleteRead(b"partial"), empty]), [])
        self.assertEqual(code, 44, err)
        self.assertIn("[billed] usd=0.000000 attempts=2 known=no", err)

    def test_a_timeout_is_a_lost_reply_too(self):
        fake = T.Fake([TimeoutError("read timed out"), T.billed("second", "4", 0.01)])
        _code, _out, err = T.run(T.load(), fake, [])
        self.assertIn("known=no", err)

    def test_a_retry_that_loses_its_reply_after_a_charged_attempt_is_unknown(self):
        """SBE audit 2026-09-27, the second shape: attempt one is charged 0.25 and runs out of budget, its retry and
        the fallback both lose their replies; the call used to print known=yes at the partial 0.25."""
        fake = T.Fake([T.billed("m", "", 0.25, finish="length"), TimeoutError("lost response after provider contact"),
                       TimeoutError("lost fallback response")])
        code, _out, err = T.run(T.load(), fake, [])
        self.assertEqual((code, len(fake.calls)), (44, 3), err)
        self.assertIn("[billed] usd=0.250000 attempts=3 known=no", err)

    def test_control_an_answered_call_stays_known(self):
        fake = T.Fake([T.billed("m", "4", 0.01)])
        _code, _out, err = T.run(T.load(), fake, [])
        self.assertIn("[billed] usd=0.010000 attempts=1 known=yes", err)


class ANegativeOrNonFiniteChargeIsNotACharge(unittest.TestCase):
    """Finding 7: a negative attempt cost offset a positive one and the sum read as fully measured."""

    def test_a_negative_charge_makes_the_bill_unknown(self):
        fake = T.Fake([T.billed("m", "", -0.5, finish="length"), T.billed("m", "ok", 0.75)])
        code, _out, err = T.run(T.load(), fake, [])
        self.assertEqual(code, 0, err)
        self.assertIn("known=no", err)
        self.assertNotIn("usd=0.250000", err)

    def test_an_infinite_charge_makes_the_bill_unknown(self):
        fake = T.Fake([T.billed("m", "4", float("inf"))])
        _code, _out, err = T.run(T.load(), fake, [])
        self.assertIn("known=no", err)

    def test_a_sum_that_overflows_is_unknown(self):
        fake = T.Fake([T.billed("m", "", 1e308, finish="length"), T.billed("m", "ok", 1e308)])
        _code, _out, err = T.run(T.load(), fake, [])
        self.assertIn("known=no", err)
        self.assertNotIn("known=yes", err)

    def test_an_integer_past_the_float_range_is_unknown_not_a_crash(self):
        """10**400 is valid JSON; float() of it raises OverflowError, which must read as no figure."""
        fake = T.Fake([T.billed("m", "4", 10 ** 400)])
        code, _out, err = T.run(T.load(), fake, [])
        self.assertEqual(code, 0, err)
        self.assertIn("[billed] usd=0.000000 attempts=1 known=no", err)

    def test_a_bool_charge_is_not_a_number(self):
        fake = T.Fake([T.billed("m", "4", True)])
        _code, _out, err = T.run(T.load(), fake, [])
        self.assertIn("known=no", err)


class ADecisionCallPrintsItsBill(unittest.TestCase):
    """Finding 3, the bridge half: a successful Jev call printed no billed line, so it settled ABANDONED."""

    def answer(self, usage):
        return {"model": "typesafe/jev-1.13", "answers": {"correct": {"type": "noul", "noul": True}}, "usage": usage}

    def test_a_decision_with_a_cost_prints_it_known(self):
        fake = T.FakeDecision([self.answer({"input_tokens": 1, "output_tokens": 1, "cost": 0.25})])
        code, _out, err = T.run_decision(T.load(), fake, T.NOUL_Q)
        self.assertEqual(code, 0, err)
        self.assertIn("[billed] usd=0.250000 attempts=1 known=yes", err)

    def test_a_decision_without_a_cost_is_unknown(self):
        fake = T.FakeDecision([self.answer({"input_tokens": 1, "output_tokens": 1})])
        code, _out, err = T.run_decision(T.load(), fake, T.NOUL_Q)
        self.assertEqual(code, 0, err)
        self.assertIn("[billed] usd=0.000000 attempts=1 known=no", err)

    def test_a_decision_missing_an_answer_still_prints_its_bill(self):
        fake = T.FakeDecision([{"model": "typesafe/jev-1.13", "answers": {}, "usage": {"cost": 0.25}}])
        code, _out, err = T.run_decision(T.load(), fake, T.NOUL_Q)
        self.assertEqual(code, 45, err)
        self.assertIn("[billed] usd=0.250000 attempts=1 known=yes", err)

    def test_a_decision_with_a_negative_cost_is_unknown(self):
        fake = T.FakeDecision([self.answer({"cost": -0.25})])
        _code, _out, err = T.run_decision(T.load(), fake, T.NOUL_Q)
        self.assertIn("known=no", err)


class ADecisionCallIsNeverAChatPlan(unittest.TestCase):
    """Finding 3, the bound half: the decision request sends no max_tokens, so no chat attempt plan bounds it."""

    def test_both_decision_routes_are_recognised(self):
        mod = T.load()
        parse = mod.build_parser().parse_args
        self.assertTrue(mod.decision_call(parse(["--decisions", "--model", "x", "--", "{}"])))
        self.assertTrue(mod.decision_call(parse(["--model", "jev", "--", "{}"])))
        self.assertTrue(mod.decision_call(parse(["--model", "TypeSafe", "--", "{}"])))
        self.assertFalse(mod.decision_call(parse(["--model", "deepseek", "--", "hello"])))

    def test_the_decision_request_carries_no_token_ceiling(self):
        """The fact the dispatcher's unbounded rule rests on: if this ever changes, the bound can be made real."""
        fake = T.FakeDecision([{"model": "typesafe/jev-1.13", "answers": {"correct": {"noul": True}}, "usage": {"cost": 0.1}}])
        T.run_decision(T.load(), fake, T.NOUL_Q)
        self.assertNotIn("max_tokens", fake.calls[0]["body"])


if __name__ == "__main__":
    unittest.main(verbosity=1)

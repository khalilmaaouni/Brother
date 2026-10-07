"""Tests for runner_decision_card.py (ACC7.b).

Each guard has its own fixture that only that guard can refuse. The wait
fixtures are written through heavy_slot.record_wait, the one owner of the line
shape, and every test uses its own directory, never the live wait file.
"""
import io
import os
import shutil
import sys
import tempfile
import unittest
from contextlib import redirect_stdout

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import heavy_slot  # noqa: E402
import heavy_wait_report as hwr  # noqa: E402
import runner_decision_card as rdc  # noqa: E402

try:  # noqa: E402
    import tmp_sandbox as _tmp
    _tmp.install()
except ImportError:
    sys.stderr.write("test_runner_decision_card: tmp_sandbox.py not found; temp trees may be left behind\n")

STATS = {"n": 1708, "median_s": 0.0, "p90_s": 3.0, "over_bar": 13, "unqueued": 3, "first_at": "2026-09-28T13:02:11+0900"}


def recommendation_lines(card):
    return [l for l in card.splitlines() if l.startswith("Recommendation:")]


class TheCardFollowsTheVerdict(unittest.TestCase):
    def test_hold_recommends_a_and_nothing_else_says_buy(self):  # REQ-ACC7-B1, M-ACC7B-BUY-ON-HOLD
        card = rdc.render_card("HOLD", STATS, None)
        self.assertIn(rdc.HOLD_LINE, card)
        self.assertEqual(recommendation_lines(card), [rdc.HOLD_LINE])
        rest = card.replace(rdc.HOLD_LINE, "", 1)
        self.assertNotIn("buy", rest.lower())
        self.assertIn('Owner act, one sentence: "keep the laptop".', card)
        self.assertIn("Verdict: HOLD", card)
        self.assertIn("Median wait: 0.0 s over 1708 admissions", card)

    def test_a_ceiling_given_on_hold_is_ignored_and_not_printed(self):
        card = rdc.render_card("HOLD", STATS, 40.0, 12.5)
        self.assertNotIn("40", card)
        self.assertNotIn("12.5", card)
        self.assertNotIn("$", card)

    def test_flip_always_names_the_ceiling(self):  # REQ-ACC7-B1
        card = rdc.render_card("FLIP", dict(STATS, median_s=601.0), 40.0, 12.5)
        self.assertEqual(recommendation_lines(card), ["Recommendation: buy option C only within $40 per month."])
        self.assertIn('Owner act, one sentence: "buy option C at $40 per month".', card)
        self.assertIn("$12.5 per hour", card)
        self.assertNotIn(rdc.HOLD_LINE, card)

    def test_flip_without_a_ceiling_is_refused(self):  # REQ-ACC7-B2, M-ACC7B-NO-CEILING-OK
        with self.assertRaises(ValueError):
            rdc.render_card("FLIP", dict(STATS, median_s=601.0), None)

    def test_zero_or_negative_ceiling_is_refused(self):
        for bad in (0, -5.0):
            with self.assertRaises(ValueError):
                rdc.render_card("FLIP", dict(STATS, median_s=601.0), bad)

    def test_flip_without_a_rate_marks_the_rate_as_owner_supplied(self):  # REQ-ACC7-B5
        card = rdc.render_card("FLIP", dict(STATS, median_s=601.0), 40.0)
        self.assertIn("owner supplied", card)
        self.assertIn("hours used times", card)
        self.assertNotIn("per hour\n", card.replace("the hourly rate, owner supplied", ""))

    def test_no_data_recommends_nothing_and_names_the_missing_data(self):
        card = rdc.render_card("NO-DATA", dict(STATS, n=12), 40.0)
        self.assertEqual(recommendation_lines(card), [])
        self.assertNotIn("buy", card.lower())
        self.assertIn("Missing: 12 usable samples, 200 needed.", card)

    def test_a_verdict_outside_the_three_words_raises(self):
        with self.assertRaises(ValueError):
            rdc.render_card("PASS", STATS, None)

    def test_a_missing_stats_key_renders_no_data_never_a_partial_card(self):
        card = rdc.render_card("HOLD", {"n": 1708, "median_s": 0.0}, None)
        self.assertIn("Verdict: NO-DATA", card)
        self.assertIn("lack p90_s, over_bar, unqueued, first_at", card)
        self.assertEqual(recommendation_lines(card), [])
        self.assertNotIn("Median wait:", card)


class NoInventedPrice(unittest.TestCase):
    def test_every_price_shape_is_caught_unless_allowed(self):  # REQ-ACC7-B3
        for text in ("$5", "5 USD", "USD 5", "5 per month", "per month 5", "5/month", "5 per hour",
                     "5/hour", "$ 1,200.50", "5 usd"):
            errors = rdc.card_errors("HOLD " + text, [])
            self.assertEqual(len(errors), 1, "%r -> %r" % (text, errors))
        self.assertEqual(rdc.card_errors("FLIP $40 per month at $12.5 per hour", ["40", "12.5"]), [])
        self.assertEqual(rdc.card_errors("FLIP $ 1,200.50", ["1200.50"]), [])

    def test_a_card_with_no_verdict_word_fails(self):
        self.assertEqual(rdc.card_errors("keep the laptop", []), ["the card carries no verdict word"])
        self.assertEqual(rdc.card_errors("HOLD, keep the laptop", []), [])

    def test_the_allowed_list_is_built_from_the_inputs_only(self):
        card = rdc.render_card("FLIP", dict(STATS, median_s=601.0), 40.0, 12.5)
        self.assertEqual(rdc.card_errors(card, ["40", "12.5"]), [])
        self.assertTrue(rdc.card_errors(card, ["40"]), "the rate must count as a price")
        self.assertTrue(rdc.card_errors(card + "\nalso $7 per month\n", ["40", "12.5"]))

    def test_render_refuses_its_own_card_when_options_carry_a_price(self):
        original = rdc.runner_options
        rdc.runner_options = lambda: [dict(o, cost_formula="$3 per month") for o in original()]
        try:
            with self.assertRaises(ValueError):
                rdc.render_card("HOLD", STATS, None)
        finally:
            rdc.runner_options = original


class PrivateSourceNeverGoesPublic(unittest.TestCase):
    def test_option_b_is_limited_to_the_exported_tree(self):  # REQ-ACC7-B4
        options = rdc.runner_options()
        self.assertEqual([o["name"] for o in options], ["A", "B", "C"])
        for o in options:
            self.assertEqual(set(o), {"name", "what", "cost_formula", "footprint", "limit"})
        self.assertIn("exported tree only", options[1]["limit"])

    def test_render_never_recommends_option_b(self):  # REQ-ACC7-B4, M-ACC7B-RECOMMEND-B
        for card in (rdc.render_card("HOLD", STATS, None),
                     rdc.render_card("FLIP", dict(STATS, median_s=601.0), 40.0),
                     rdc.render_card("NO-DATA", dict(STATS, n=0), None)):
            for line in recommendation_lines(card):
                self.assertNotIn("option B", line)
            self.assertNotIn("buy option B", card.lower())


class TheEntryPoint(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="runner-card-test-")
        self.path = os.path.join(self.tmp, "waits.jsonl")
        self.out = os.path.join(self.tmp, "card.md")

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def record(self, waits):
        for w in waits:
            heavy_slot.record_wait(self.tmp, w, "admitted", 1, 3)

    def run_main(self, *extra):
        out = io.StringIO()
        with redirect_stdout(out):
            code = rdc.main(["--path", self.path, "--out", self.out] + list(extra))
        return code, out.getvalue()

    def test_hold_writes_the_card_and_check_matches_it(self):
        self.record([0] * hwr.MIN_SAMPLES)
        code, text = self.run_main()
        self.assertEqual(code, 0, text)
        with open(self.out, encoding="utf-8") as fh:
            card = fh.read()
        self.assertIn(rdc.HOLD_LINE, card)
        self.assertIn(rdc.GENERATED_PREFIX, card)
        self.assertIn("Source: %s, %d lines, 0 unusable" % (rdc._short(self.path), hwr.MIN_SAMPLES), card)
        self.assertEqual(rdc._short(os.path.expanduser("~/x/waits.jsonl")), "~/x/waits.jsonl")
        self.assertEqual(rdc._short("/elsewhere/waits.jsonl"), "/elsewhere/waits.jsonl")
        code, text = self.run_main("--check")
        self.assertEqual((code, text.startswith("HOLD")), (0, True), text)

    def test_check_ignores_only_the_generated_time_line(self):
        self.record([0] * hwr.MIN_SAMPLES)
        self.run_main()
        with open(self.out, encoding="utf-8") as fh:
            card = fh.read()
        retimed = "\n".join(rdc.GENERATED_PREFIX + "1999-01-01T00:00:00+0000" if l.startswith(rdc.GENERATED_PREFIX) else l
                            for l in card.splitlines()) + "\n"
        with open(self.out, "w", encoding="utf-8") as fh:
            fh.write(retimed)
        self.assertEqual(self.run_main("--check")[0], 0)
        self.record([0])   # one more admission: the committed card is stale
        code, text = self.run_main("--check")
        self.assertEqual(code, 3, text)
        self.assertIn("stale", text)

    def test_check_with_no_card_is_3(self):
        self.record([0] * hwr.MIN_SAMPLES)
        code, text = self.run_main("--check")
        self.assertEqual(code, 3, text)
        self.assertIn("missing", text)

    def test_flip_with_a_ceiling_exits_1_and_without_one_is_refused(self):
        self.record([601] * hwr.MIN_SAMPLES)
        code, text = self.run_main("--ceiling", "40")
        self.assertEqual(code, 1, text)
        with open(self.out, encoding="utf-8") as fh:
            self.assertIn("$40 per month", fh.read())
        self.assertEqual(self.run_main("--ceiling", "40", "--check")[0], 1)
        os.remove(self.out)
        code, text = self.run_main()
        self.assertEqual(code, 1, text)
        self.assertTrue(text.startswith("REFUSED"), text)
        self.assertFalse(os.path.exists(self.out), "a refused card is not written")

    def test_thin_data_exits_2_and_writes_a_card_with_no_recommendation(self):
        self.record([0] * 5)
        code, text = self.run_main()
        self.assertEqual(code, 2, text)
        with open(self.out, encoding="utf-8") as fh:
            card = fh.read()
        self.assertEqual(recommendation_lines(card), [])
        self.assertIn("Missing: 5 usable samples, 200 needed.", card)
        self.assertEqual(self.run_main("--check")[0], 2)

    def test_a_failed_write_leaves_the_old_card(self):
        self.record([0] * hwr.MIN_SAMPLES)
        self.run_main()
        with open(self.out, encoding="utf-8") as fh:
            before = fh.read()
        original = rdc.os.replace

        def refuse(a, b):
            raise OSError("replace refused")
        rdc.os.replace = refuse
        try:
            with self.assertRaises(OSError):
                rdc.write_whole(self.out, "garbage")
        finally:
            rdc.os.replace = original
        with open(self.out, encoding="utf-8") as fh:
            self.assertEqual(fh.read(), before)


if __name__ == "__main__":
    unittest.main()

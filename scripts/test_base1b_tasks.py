#!/usr/bin/env python3
"""BASE1.b integrator cases: the app correction's hidden outcome decides, and a hidden test that could not run is never a
PASS. Written by the integrator after the implementer's mutation probe left two survivors (the frozen Tasks class only
cheats the analytics task, and nothing reached score_verdict's unrun branch).

    python3 -B scripts/test_base1b_tasks.py

Python 3.9, standard library."""
import os
import sys
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import benchmark_harness as H  # noqa: E402

FIXED_PRICING = "def apply_discount(price, pct):\n    if not 0 <= pct <= 100:\n        raise ValueError(pct)\n    return price * (100 - pct) / 100.0\n"
#: patches only the caller the task names, so invoice.py still calls the broken shared helper
CALLER_ONLY = ("def compute_total(price, discount_pct):\n"
               "    if not 0 <= discount_pct <= 100:\n        raise ValueError(discount_pct)\n"
               "    return price * (100 - discount_pct) / 100.0\n")


def writer(path, text):
    return [sys.executable, "-c", "open(%r, 'w').write(%r)" % (path, text)]


class AppTaskHiddenOutcome(unittest.TestCase):
    def task(self):
        return H.task_by_id("app-incremental-change")

    def test_the_shared_fix_passes(self):
        got = H.run_task(self.task(), writer("pricing.py", FIXED_PRICING), timeout=60, label="shared-fix")
        self.assertEqual("PASS", got["verdict"], got)

    def test_a_caller_only_patch_passes_the_visible_test_and_fails_the_hidden_one(self):
        got = H.run_task(self.task(), writer("order_total.py", CALLER_ONLY), timeout=60, label="caller-only")
        self.assertEqual("FAIL", got["verdict"], got)
        self.assertIs(True, got["visible_pass"], got)
        self.assertIs(False, got["hidden_pass"], got)


class UnrunHiddenTestIsNeverAPass(unittest.TestCase):
    def test_a_produced_solution_whose_hidden_test_did_not_run_is_no_data(self):
        self.assertEqual("NO-DATA", H.score_verdict({}, None, True)[0])

    def test_the_other_branches_keep_their_answers(self):
        self.assertEqual("PASS", H.score_verdict({}, True, True)[0])
        self.assertEqual("FAIL", H.score_verdict({}, False, True)[0])
        self.assertEqual("FAIL", H.score_verdict({}, None, False)[0])


if __name__ == "__main__":
    unittest.main()

"""LIMIT-03 calibration: the worst window decides, the bands are inclusive,
and an unreadable reading holds rather than runs."""
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import limit_preempt as L  # noqa: E402


def usage(*pcts, status="ok"):
    return {"plan": {"status": status, "windows": [
        {"label": "w%d" % i, "percentUsed": p} for i, p in enumerate(pcts)]}}


class LimitPreempt(unittest.TestCase):
    def test_bands(self):
        self.assertEqual(L.decide(usage(28, 37, 13))[0], "RUN")
        self.assertEqual(L.decide(usage(69.9))[0], "RUN")
        self.assertEqual(L.decide(usage(70))[0], "SHIFT")
        self.assertEqual(L.decide(usage(85))[0], "CHECKPOINT")

    def test_the_worst_window_decides_not_the_first(self):
        self.assertEqual(L.decide(usage(10, 90, 20))[0], "CHECKPOINT")
        self.assertEqual(L.decide(usage(10, 20, 75))[0], "SHIFT")

    def test_unreadable_holds(self):
        for u in ({}, {"plan": None}, usage(status="unavailable"), usage(),
                  {"plan": {"status": "ok", "windows": [{"label": "x"}]}},
                  {"plan": {"status": "ok", "windows": [{"label": "x", "percentUsed": "lots"}]}}):
            self.assertEqual(L.decide(u)[0], "HOLD", u)

    def test_cli_exit_codes(self):
        self.assertEqual(L.main(["/nonexistent/usage.json"]), 2)


if __name__ == "__main__":
    unittest.main()

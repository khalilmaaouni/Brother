"""worker_mix.picks never names a model the registry does not know (finding 28, 2026-09-26).

Its docstring says "Empty when nothing is known", but the base arms were filled round robin without asking `known`, so
a caller handing it an empty or broken registry got the default paid model for every seat. Both callers (repair_wave,
unit_runner) route through picks, so the rule lives here once.
Run from the repository root: python3 -B scripts/test_worker_mix_known.py
"""
import os, sys, unittest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "scripts", "loop"))
import worker_mix as WM  # noqa: E402


class Known(unittest.TestCase):
    def test_nothing_known_is_empty(self):
        self.assertEqual(WM.picks(3, ["deepseek"], set(), env={}), [])

    def test_an_unknown_base_arm_is_never_picked(self):
        got = WM.picks(4, ["deepseek", "muse"], {"muse"}, env={})
        self.assertTrue(got and all(m == "muse" for m in got), got)

    def test_a_known_base_fills_every_seat(self):
        self.assertEqual(WM.picks(3, ["deepseek"], {"deepseek"}, env={}), ["deepseek"] * 3)


if __name__ == "__main__":
    unittest.main(verbosity=1)

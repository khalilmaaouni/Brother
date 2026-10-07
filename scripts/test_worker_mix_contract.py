#!/usr/bin/env python3
"""Offline regression checks for the model selection the worker actually uses."""
import random
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent / "loop"))
import worker_mix as mix


class SelectionContract(unittest.TestCase):
    known = {"deepseek", "muse", "sonnet", "astra", "luna"}

    def test_pins_override_configured_mix_and_history(self):
        for model in self.known:
            for seats in (1, 3, 8):
                with self.subTest(model=model, seats=seats):
                    selected = mix.picks(
                        seats, [model], self.known,
                        env={"BROTHER_PIN_MODEL": model,
                             "BROTHER_WORKER_MIX": "deepseek:5,muse:2,sonnet:1"},
                        stats={"deepseek": [80, 0]}, rng=random.Random(0))
                    self.assertEqual(selected, [model] * seats)

    def test_invalid_pin_refuses_without_base_fallback(self):
        for pin in ("missing", " astra ", True, ["astra"]):
            with self.subTest(pin=pin):
                self.assertEqual(mix.picks(3, ["deepseek"], self.known,
                                          env={"BROTHER_PIN_MODEL": pin}), [])

    def test_small_mix_keeps_diversity_and_full_mix_keeps_weights(self):
        env = {"BROTHER_WORKER_MIX": "deepseek:5,muse:2,sonnet:1"}
        self.assertEqual(mix.picks(3, [], self.known, env, stats={}),
                         ["deepseek", "muse", "sonnet"])
        self.assertEqual(mix.picks(8, [], self.known, env, stats={}),
                         ["deepseek"] * 5 + ["muse"] * 2 + ["sonnet"])

    def test_seat_count_and_membership_for_smaller_and_larger_rounds(self):
        for seats in range(1, 33):
            selected = mix.picks(seats, ["deepseek"], self.known, env={}, stats={})
            self.assertEqual(len(selected), seats)
            self.assertLessEqual(set(selected), self.known)
            if seats >= 3:
                self.assertEqual(set(selected), {"deepseek", "muse", "sonnet"})

    def test_partial_history_keeps_unmeasured_arms_bounded(self):
        selected = mix.picks(3, [], self.known, env={},
                             stats={"deepseek": [100, 0]}, rng=random.Random(0))
        self.assertEqual(selected.count("muse"), 1)
        self.assertEqual(selected.count("sonnet"), 1)
        self.assertEqual(len(selected), 3)


if __name__ == "__main__":
    unittest.main()

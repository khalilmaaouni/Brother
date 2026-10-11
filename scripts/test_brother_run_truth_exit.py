"""TL1.b: the engine's exit code is the mapped state of delivery_conclusion.

_exit_code_for keeps its name, arguments and (code, reason) return, and
reads the whole run's state from TL1.a's helper. The one intended change: a
PASS beside a NO-DATA with nothing refused exits 2 (PARTIAL), not 0. Every
other input keeps its old code and its old reason words. Spec:
docs/plan/specs/TL1.md, section TL1.b.

Done check: python3 -B scripts/test_brother_run_truth_exit.py
"""
import os
import sys
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import brother_run as _br  # noqa: E402


def _r(uid, state):
    return {"id": uid, "state": state}


#: (receipts, refused) pairs covering every state the exit code can read.
INPUTS = {
    "one-pass": ([_r("A1", "verified")], []),
    "many-pass": ([_r("A1", "verified"), _r("A2", "verified")], []),
    "mixed": ([_r("A1", "verified"), _r("N1", "no-data")], []),
    "all-no-data": ([_r("N1", "no-data"), _r("N2", "no-data")], []),
    "refused": ([_r("A1", "verified"), _r("B1", "refused")],
                [("B1", "it was started and ended failed")]),
    "unmatched-fail": ([_r("A1", "verified"), _r("V1", "refused")], []),
    "no-units": ([], []),
    "corrupt-element": ([None, "verified", 3], []),
}


class AMixedRunExitsTwo(unittest.TestCase):
    """REQ-TL1-B-MIXED (M-TL1B-OLD-RULE)."""

    def test_one_pass_beside_a_no_data_exits_2(self):
        code, reason = _br._exit_code_for(*INPUTS["mixed"])
        self.assertEqual(code, 2, reason)
        self.assertIn("NO-DATA", reason)
        self.assertIn("verified", reason)

    def test_a_corrupt_receipt_can_never_exit_0(self):
        code, reason = _br._exit_code_for(*INPUTS["corrupt-element"])
        self.assertEqual(code, 2, reason)


class EveryOldInputKeepsItsCodeAndWords(unittest.TestCase):
    """REQ-TL1-B-PINS (M-TL1B-REASON-LOST): the four inputs of
    TheExitCodeFollowsTheEstatesConvention, re-asserted here with their
    exact reason sentences."""

    def test_the_old_pins(self):
        pins = {
            "one-pass": (0, "1 unit(s) verified and none was refused"),
            "refused": (1, "1 unit(s) refused: B1: it was started and "
                           "ended failed"),
            "all-no-data": (2, "nothing in this run was verified (0 PASS, "
                               "2 NO-DATA); a run that refused nothing but "
                               "proved nothing is not the same thing as "
                               "success"),
            "no-units": (2, "no units were in this run's Work document at "
                            "all"),
            "unmatched-fail": (1, "a unit's receipt read FAIL with no "
                                  "matching entry in this run's own refused "
                                  "list; the verifier itself refused"),
        }
        for name, want in pins.items():
            with self.subTest(input=name):
                self.assertEqual(_br._exit_code_for(*INPUTS[name]), want)

    def test_the_mixed_reason_names_what_is_missing(self):
        self.assertEqual(
            _br._exit_code_for(*INPUTS["mixed"])[1],
            "1 of 2 unit(s) verified and 1 read NO-DATA: the outcome is only "
            "partly proven")


class TheCodeIsTheMappedState(unittest.TestCase):
    """REQ-TL1-B-ONESOURCE (M-TL1B-OWN-MAP): the code and the state come from
    one place, so they cannot drift."""

    def test_code_equals_exit_for_state_of_the_conclusion(self):
        for name, (receipts, refused) in INPUTS.items():
            with self.subTest(input=name):
                state = _br.delivery_conclusion(
                    receipts, refused, (), True, (), None)["state"]
                self.assertEqual(_br._exit_code_for(receipts, refused)[0],
                                 _br.EXIT_FOR_STATE[state])


if __name__ == "__main__":
    unittest.main()

"""COE seam check: registry to nomination to scoring to arbitration to loop
to gate, in ONE pass, with only the model answers stubbed.

WHY THIS EXISTS. Every COE unit is green on its own suite, and that is
exactly the state this estate has been burned by: two green units meeting at
a dead seam, where each side is correct and nothing drives one into the
other. These cases call the REAL nominate, score_council, arbitrate, run_loop
and gate, injecting only `ask` (what a seat answers), `attack` and `repair`,
because those are the model calls. If a signature or vocabulary drifts
between the modules, this goes red; the per-unit suites would not.

Proving command: python3 scripts/test_coe_chain.py
"""
import json
import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import coe_arbitrate  # noqa: E402
import coe_gate  # noqa: E402
import coe_loop  # noqa: E402
import coe_nominate  # noqa: E402
import coe_score  # noqa: E402

PROBLEM = {
    "id": "ORCH-33-selection",
    "title": "distance to the repository of record, with an explicit disposition",
    "risk_class": "high",
    "attributes": ["counting", "enforcement", "observability"],
    "content_class": "public",
    "content_leaves_machine": False,
}
CRITERIA = ["prevents", "measurable", "reversible"]


def answers(score):
    """An `ask` that gives every seat the same score for every criterion.

    A seat answers a bare integer 0 to 10, or the literal "NO-DATA": that is
    coe_score's whole accepted vocabulary, and anything else (prose, a dict,
    a float, a bool) is recorded as a FAILURE cell rather than inferred into
    a score. Writing this stub wrong is how the seam check earned its keep:
    every cell failed and the matrix arrived empty."""
    def ask(seat_id, criterion, prompt):
        return score
    return ask


class TheChainRunsEndToEnd(unittest.TestCase):
    def test_nomination_feeds_scoring_feeds_arbitration(self):
        council = coe_nominate.nominate(PROBLEM)
        self.assertTrue(council.seats, "nomination returned no seats")
        matrix = coe_score.score_council(council, "a methodology", ask=answers(9),
                                         criteria=CRITERIA)
        verdict = coe_arbitrate.arbitrate(matrix, seats=list(council.seats),
                                          criteria=CRITERIA)
        self.assertIn(verdict.outcome, coe_arbitrate.OUTCOMES)

    def test_a_single_low_seat_caps_the_verdict_downward(self):
        council = coe_nominate.nominate(PROBLEM)
        seats = list(council.seats)

        def ask(seat_id, criterion, prompt):
            low = seat_id == seats[0] and criterion == CRITERIA[1]
            return 4 if low else 10

        matrix = coe_score.score_council(council, "m", ask=ask, criteria=CRITERIA)
        verdict = coe_arbitrate.arbitrate(matrix, seats=seats, criteria=CRITERIA)
        self.assertNotEqual(verdict.outcome, "PASS",
                            "one seat scoring 4 must not average away into a PASS")
        self.assertEqual(verdict.capping_criterion, CRITERIA[1])

    def test_loop_terminates_and_the_gate_takes_its_result(self):
        rounds = {"n": 0}

        def ask(seat_id, criterion, prompt):
            rounds["n"] += 1
            return 10

        result = coe_loop.run_loop(
            PROBLEM, "a methodology", criteria=CRITERIA, ask=ask,
            attack=lambda *a, **k: [], repair=lambda *a, **k: "a methodology",
            max_rounds=4)
        self.assertIn(result.outcome, coe_loop.OUTCOMES)
        self.assertGreater(rounds["n"], 0, "the loop never called a seat")
        with tempfile.TemporaryDirectory() as d:
            store = os.path.join(d, "selections.json")
            coe_gate.mark_open("ORCH-33", store_path=store, problem=PROBLEM["id"])
            coe_gate.close_selection("ORCH-33", result, store_path=store, problem=PROBLEM["id"])
            self.assertEqual(coe_gate.status("ORCH-33", store_path=store).outcome,
                             result.outcome)
            # the defect this check was written to catch: a writer that takes
            # the problem MAPPING writes a store the reader refuses forever.
            with self.assertRaises(coe_gate.GateRefused):
                coe_gate.mark_open("ORCH-34", store_path=store, problem=PROBLEM)

    def test_a_build_row_cannot_open_while_its_selection_is_open(self):
        with tempfile.TemporaryDirectory() as d:
            store = os.path.join(d, "selections.json")
            coe_gate.mark_open("ORCH-33", store_path=store, problem=PROBLEM["id"])
            with open(store, encoding="utf-8") as fh:
                self.assertIn("ORCH-33", json.load(fh)["selections"])
            allowed, reason = coe_gate.may_open(
                {"row_id": "ORCH-33-build", "selection_row_id": "ORCH-33",
                 "problem": PROBLEM["id"]},
                selections=coe_gate.load_store(store))
            self.assertFalse(allowed, "a build row opened while its selection was open")
            self.assertIn("OPEN", reason.upper())
            # and MISSING is refused too, never read as "nothing blocking"
            missing, reason2 = coe_gate.may_open(
                {"row_id": "x-build", "selection_row_id": "never-started",
                 "problem": PROBLEM["id"]},
                selections=coe_gate.load_store(store))
            self.assertFalse(missing)
            self.assertIn("MISSING", reason2.upper())


if __name__ == "__main__":
    unittest.main()

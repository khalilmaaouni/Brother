"""S12: the memory recurrence gauntlet's counting, driven by a fake recall.

WHY A FAKE. scripts/gauntlet_memory_recurrence.py's real arm builds a vault,
indexes it and shells out three times per condition, which is the right way to
MEASURE the mechanism and the wrong way to prove ARITHMETIC: a counter tested
only through the real path cannot be shown a population it will never produce
today. The recall seam takes a callable, so this suite hands it fixed
observations and checks the three populations that matter: everything surfaced
reads 5 of 5, silence reads 0, and one NO-DATA condition leaves the denominator
at 4 rather than counting either way.

Structure mirrors scripts/test_recall_revalidation.py, the closest sibling: one
class per behaviour, named for what it asserts, unittest, and any file this
suite writes goes to a temp directory, never the real tree.
"""
import json
import os
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import gauntlet_memory_recurrence as G  # noqa: E402


def surfaced_observation(condition):
    """What the mechanism produces when it surfaces exactly the right lesson."""
    obs = G.empty_observation()
    obs["applied"] = [condition["expected"]]
    obs["recall_said_nothing"] = False
    return obs


def silent_observation(condition):
    """What the mechanism produces when nothing reaches the model at all."""
    return G.empty_observation()


def contradiction_observation(condition):
    """Both sides of a declared contradiction applied, each flagged as
    contradicting the other: the shape the real contradictory arm produces
    today, and the one contradiction_unresolved() reads as NO-DATA."""
    obs = G.empty_observation()
    obs["applied"] = sorted([condition["expected"], "never-validate"])
    obs["contradicts_flagged"] = ["never validate", "validate before return"]
    obs["recall_said_nothing"] = False
    return obs


class EverySurfacedConditionIsCounted(unittest.TestCase):
    def test_all_five_surfaced_reads_five_of_five(self):
        rows = G.run_conditions(recall=surfaced_observation)
        self.assertEqual(len(rows), 5)
        self.assertEqual([r["result"] for r in rows], ["surfaced"] * 5)
        self.assertEqual(G.summarize(rows), (5, 5))
        self.assertEqual(G.summary_line(rows),
                         "recurrence prevented: 5 of 5 conditions")


class SilenceCountsNothingAndStaysInTheDenominator(unittest.TestCase):
    def test_all_five_silent_reads_zero_of_five(self):
        rows = G.run_conditions(recall=silent_observation)
        self.assertEqual([r["result"] for r in rows], ["silent"] * 5)
        self.assertEqual(G.summarize(rows), (0, 5))
        self.assertEqual(G.summary_line(rows),
                         "recurrence prevented: 0 of 5 conditions")


class ANoDataConditionLeavesTheDenominatorAtFour(unittest.TestCase):
    """NO-DATA is never a pass, and never a failure either: the condition the
    mechanism cannot express leaves the denominator, so it can neither flatter
    nor punish the rate."""

    def recall(self, condition):
        if condition.get("nodata_when") is not None:
            return contradiction_observation(condition)
        return surfaced_observation(condition)

    def test_one_no_data_arm_is_excluded_from_both_sides(self):
        rows = G.run_conditions(recall=self.recall)
        by_id = {r["id"]: r for r in rows}
        self.assertEqual(by_id["contradictory memory"]["result"], "NO-DATA")
        self.assertEqual(G.summarize(rows), (4, 4))
        self.assertEqual(G.summary_line(rows),
                         "recurrence prevented: 4 of 4 conditions")

    def test_the_no_data_row_names_the_missing_field(self):
        rows = G.run_conditions(recall=self.recall)
        detail = {r["id"]: r["detail"] for r in rows}["contradictory memory"]
        self.assertIn("no field expresses which side of a contradiction", detail)
        self.assertIn("verified_against", detail)

    def test_one_applied_lesson_scores_like_any_other_arm(self):
        """The NO-DATA verdict is measured, not hardcoded to this condition: an
        observation where the mechanism DID choose one side scores normally."""
        rows = G.run_conditions(recall=surfaced_observation)
        by_id = {r["id"]: r for r in rows}
        self.assertEqual(by_id["contradictory memory"]["result"], "surfaced")


class AnAppliedStaleLessonFailsItsArm(unittest.TestCase):
    """The frozen rubric: a stale, contradictory or superseded lesson that
    reaches the applied section fails the arm outright, whatever else scored."""

    def recall(self, condition):
        if condition["id"] != "stale memory":
            return surfaced_observation(condition)
        obs = G.empty_observation()
        obs["applied"] = sorted([condition["expected"], "old-lexer-rule"])
        obs["recall_said_nothing"] = False
        return obs

    def test_the_arm_reads_wrong_and_names_what_was_applied(self):
        rows = G.run_conditions(recall=self.recall)
        stale = {r["id"]: r for r in rows}["stale memory"]
        self.assertEqual(stale["result"], "wrong")
        self.assertIn("old-lexer-rule", stale["detail"])
        self.assertEqual(G.summarize(rows), (4, 5))


class AnUnobservableArmIsNoDataAboutTheRunItself(unittest.TestCase):
    def test_a_recall_that_raises_is_reported_never_swallowed(self):
        def broken(condition):
            raise RuntimeError("the index refused")

        rows = G.run_conditions(recall=broken)
        self.assertEqual([r["result"] for r in rows], ["NO-DATA"] * 5)
        self.assertTrue(all(r["unobservable"] for r in rows))
        self.assertIn("the index refused", rows[0]["detail"])
        self.assertEqual(G.summarize(rows), (0, 0))


class TheRecordCarriesTheRevisionAndEveryCondition(unittest.TestCase):
    def test_the_json_record_is_written_with_its_summary_line(self):
        rows = G.run_conditions(recall=surfaced_observation)
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "results", "memory-recurrence-test.json")
            G.record(rows, path)
            with open(path, encoding="utf-8") as fh:
                doc = json.load(fh)
        self.assertEqual(doc["gauntlet"], "memory-recurrence")
        self.assertTrue(doc["revision"])
        self.assertEqual(doc["summary"]["line"],
                         "recurrence prevented: 5 of 5 conditions")
        self.assertEqual([c["id"] for c in doc["conditions"]],
                         [c["id"] for c in G.CONDITIONS])
        self.assertEqual(doc["summary"]["no_data"], [])


class TheFiveConditionsAreTheSpecsOwnArmList(unittest.TestCase):
    """The spec froze the arm list; a rename here would silently measure a
    different benchmark than the one published."""

    def test_the_ids_match_the_frozen_specification(self):
        with open(G.SPEC_PATH, encoding="utf-8") as fh:
            spec = json.load(fh)
        self.assertEqual([c["id"] for c in G.CONDITIONS],
                         spec["seeded_conditions"])


class TheExitCodeCarriesTheVerdictNotOnlyThePrint(unittest.TestCase):
    """A gate that prints a verdict and exits 0 is a gate nobody can script
    against, so the exit code is asserted here rather than the printed line.
    main() is driven with run_conditions replaced, because main's own arm is
    the real mechanism and this is an arithmetic check."""

    def drive(self, rows):
        original = G.run_conditions
        G.run_conditions = lambda *a, **k: rows
        try:
            with tempfile.TemporaryDirectory() as tmp:
                return G.main(["--out", os.path.join(tmp, "record.json")])
        finally:
            G.run_conditions = original

    def test_every_condition_observed_exits_zero(self):
        rows = G.run_conditions(recall=surfaced_observation)
        self.assertEqual(self.drive(rows), 0)

    def test_an_arm_that_failed_the_rubric_exits_one(self):
        rows = G.run_conditions(recall=AnAppliedStaleLessonFailsItsArm().recall)
        self.assertEqual(self.drive(rows), 1)

    def test_an_unobservable_arm_exits_two_and_is_not_a_pass(self):
        def broken(condition):
            raise RuntimeError("the index refused")

        rows = G.run_conditions(recall=broken)
        self.assertEqual(self.drive(rows), 2)


if __name__ == "__main__":
    unittest.main()

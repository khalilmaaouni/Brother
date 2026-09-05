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
import contextlib
import io
import json
import os
import shutil
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


def healthy_withheld_row():
    """A passing row for the evidence-absent second-run check, used to stub
    out G.run_contradictory_withheld_check in tests that drive main() to
    prove an unrelated arithmetic point: without this stub main() would call
    the real one, which shells out for real, the exact thing this suite's
    own docstring says is the wrong way to prove arithmetic."""
    return {"id": "contradictory memory (evidence absent)", "result": "withheld",
            "detail": "both sides withheld: no current evidence resolves this conflict",
            "applied": []}


def contradiction_observation(condition):
    """Both sides of a declared contradiction applied, each flagged as
    contradicting the other: the shape a pair with NEITHER evidence_locator
    nor status produces (recall_verdict's own NO_DATA branch, the plain
    CONTRADICTS annotation with no RESOLVED or WITHHELD verdict line beside
    it), and the one contradiction_unresolved() still reads as NO-DATA after
    the fix, as a regression guard."""
    obs = G.empty_observation()
    obs["applied"] = sorted([condition["expected"], "never-validate"])
    obs["contradicts_flagged"] = ["never validate", "validate before return"]
    obs["recall_said_nothing"] = False
    obs["raw"] = (
        "\n  validate before return  [project, gauntlet]\n"
        "    CONTRADICTS: never validate (see both before treating this as settled)\n"
        "\n  never validate  [project, gauntlet]\n"
        "    CONTRADICTS: validate before return (see both before treating this as settled)\n"
    )
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


class TheFrozenCorpusGuardRefusesAMovedSpec(unittest.TestCase):
    """gauntlet_frozen.check() runs on the spec before any condition is
    observed. Drives it against a temp copy of the real spec (never the
    tree's own file), both ways, with run_conditions stubbed the same way
    TheExitCodeCarriesTheVerdictNotOnlyThePrint below drives main(), so this
    proves the guard's own wiring rather than the vault mechanism under it."""

    def setUp(self):
        fd, self.spec_path = tempfile.mkstemp(suffix=".json")
        os.close(fd)
        shutil.copyfile(G.SPEC_PATH, self.spec_path)
        self._real_spec_path = G.SPEC_PATH
        G.SPEC_PATH = self.spec_path

    def tearDown(self):
        G.SPEC_PATH = self._real_spec_path
        os.unlink(self.spec_path)

    def _drive(self, rows):
        original = G.run_conditions
        original_withheld = G.run_contradictory_withheld_check
        G.run_conditions = lambda *a, **k: rows
        G.run_contradictory_withheld_check = lambda *a, **k: healthy_withheld_row()
        try:
            out = io.StringIO()
            with contextlib.redirect_stdout(out):
                with tempfile.TemporaryDirectory() as tmp:
                    code = G.main(["--out", os.path.join(tmp, "record.json")])
            return code, out.getvalue()
        finally:
            G.run_conditions = original
            G.run_contradictory_withheld_check = original_withheld

    def test_unmutated_copy_lets_scoring_proceed(self):
        rows = G.run_conditions(recall=surfaced_observation)
        code, printed = self._drive(rows)
        self.assertIn("frozen: OK", printed)
        self.assertNotIn("REFUSED", printed)
        self.assertEqual(code, 0)

    def test_a_mutated_seed_definition_is_refused_before_any_condition_runs(
            self):
        with open(self.spec_path, encoding="utf-8") as fh:
            spec = json.load(fh)
        spec["seeded_conditions_note"] = (
            spec.get("seeded_conditions_note", "") + " (mutated)")
        with open(self.spec_path, "w", encoding="utf-8") as fh:
            json.dump(spec, fh)
        code, printed = self._drive([])
        self.assertIn("REFUSED: corpus hash moved", printed)
        self.assertNotIn("frozen: OK", printed)
        self.assertEqual(code, 1)


class TheExitCodeCarriesTheVerdictNotOnlyThePrint(unittest.TestCase):
    """A gate that prints a verdict and exits 0 is a gate nobody can script
    against, so the exit code is asserted here rather than the printed line.
    main() is driven with run_conditions replaced, because main's own arm is
    the real mechanism and this is an arithmetic check."""

    def drive(self, rows):
        original = G.run_conditions
        original_withheld = G.run_contradictory_withheld_check
        G.run_conditions = lambda *a, **k: rows
        G.run_contradictory_withheld_check = lambda *a, **k: healthy_withheld_row()
        try:
            with tempfile.TemporaryDirectory() as tmp:
                return G.main(["--out", os.path.join(tmp, "record.json")])
        finally:
            G.run_conditions = original
            G.run_contradictory_withheld_check = original_withheld

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


class TheContradictoryArmResolvesAgainstRealEvidence(unittest.TestCase):
    """Real mechanism, not a fake: drives real_recall() over the actual
    seed_contradictory fixture (bm_vault.py index/check, vault_recall_hook's
    lesson_states, receipt_door's applied_memory), through a fresh temp
    vault and temp tree every time, never the developer's own vault. Proves
    the fix rather than a fake shaped to match it: LESSON_SLUG's
    evidence_locator holds against the fixture's own current source, so the
    resolver's tier 1 applies it and withholds OPPOSITE_SLUG, and the arm no
    longer reports NO-DATA."""

    def setUp(self):
        by_id = {c["id"]: c for c in G.CONDITIONS}
        self.condition = by_id["contradictory memory"]

    def test_evidence_resolves_to_the_correct_lesson(self):
        obs = G.real_recall(self.condition)
        self.assertEqual(obs["applied"], [G.LESSON_SLUG])
        result, detail = G.classify(self.condition, obs)
        self.assertEqual(result, "surfaced", detail)

    def test_the_losing_lesson_is_never_applied(self):
        obs = G.real_recall(self.condition)
        self.assertNotIn(G.OPPOSITE_SLUG, obs["applied"])

    def test_the_arm_no_longer_reports_no_data(self):
        rows = G.run_conditions(conditions=[self.condition])
        self.assertEqual(rows[0]["result"], "surfaced")


class TheEvidenceAbsentVariantWithholdsBothSides(unittest.TestCase):
    """The second acceptable outcome, driven for real the same way: neither
    lesson's evidence_locator holds against the fixture's own current
    source, so the resolver withholds both rather than picking one, and
    neither slug reaches the applied section."""

    def test_both_sides_are_withheld_when_evidence_resolves_neither(self):
        row = G.run_contradictory_withheld_check()
        self.assertEqual(row["result"], "withheld", row["detail"])
        self.assertEqual(row["applied"], [])

    def test_neither_slug_reaches_the_applied_section(self):
        obs = G.real_recall(G.contradictory_withheld_condition())
        self.assertEqual(obs["applied"], [])
        self.assertIn("WITHHELD (contradiction", obs["raw"])


if __name__ == "__main__":
    unittest.main()

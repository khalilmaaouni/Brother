#!/usr/bin/env python3
"""Fixtures for the intake intent floor (F1). Run: python3 tools/test_sbe_intake.py

Most of this exercises `normalize_intent` directly rather than through the
interactive CLI, because `main()`'s own `ask_text` loop structurally cannot
leave a required field unanswered while stdin stays open: the loop re-asks
until `answered()` accepts a value. NO-DATA is therefore reachable in real
use whenever intent is supplied some other way than typing it at this
prompt (a future non-interactive caller, a fixture, a migration), and that
is exactly the seam `normalize_intent` is tested at directly, the same
reason `tools/test_sbe_impact.py` builds a real git repo instead of mocking
a diff: this is where the defect actually lives.

A thin CLI-level class at the bottom confirms `main()` itself does not
crash and writes the `intent` block it now owns.

Mandatory failure cases, each red before the fix and green after:
  1. missing desired outcome (material tier)                -> NO-DATA
  2. a changed requirement recorded without a change_reason  -> FAIL
  3. a high-impact change with no value statement             -> NO-DATA
  9. a low-risk (T0) change stays lightweight, no ceremony     -> PASS

H4: a defect intake with no link to what it fixes. `normalize_origin` is
tested the same way, directly, for the same reason: the CLI's own loop
cannot leave `fixes` unanswered while stdin stays open, so NO-DATA is
reached by a caller supplying origin some other way (a fixture, a
migration, a non-interactive caller).
  H4.1. a defect naming a regression row proceeds (PASS, tier untouched)
  H4.2. a defect naming no row refuses, printing what is missing (NO-DATA)
  H4.3. a feature, or no origin at all, is untouched by this (PASS, "feature")
"""
import io
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.abspath(os.path.join(HERE, ".."))
sys.path.insert(0, HERE)

from sbe_intake import (build_overrides, budget_tier_for, normalize_intent,  # noqa: E402
                        normalize_origin, record_budget_tier, run_budget_report)

# E100: one sandbox for every temp tree this process makes, removed at exit.
import os as _e100_os, sys as _e100_sys  # noqa: E402
_e100_sys.path.append(_e100_os.path.join(
    _e100_os.path.dirname(_e100_os.path.abspath(__file__)), '../../../scripts'))
try:  # noqa: E402
    import tmp_sandbox as _e100_tmp
    _e100_tmp.install()
except ImportError:
    # A packager (scripts/export_public.py, make_benchmark_bundle.py)
    # can copy this test without scripts/tmp_sandbox.py beside it. Say
    # so rather than dying: the sandbox is hygiene, not the subject.
    _e100_sys.stderr.write(
        "tmp_sandbox absent: %s leaves its temp trees behind\n"
        % _e100_os.path.basename(__file__))

SBE_INTAKE = os.path.join(ROOT, "tools", "sbe_intake.py")


class TestMissingDesiredOutcome(unittest.TestCase):
    """Case 1: a material intake with no desired_outcome is NO-DATA, naming
    the human who must supply it -- never a crash, never a silent default."""

    def test_material_tier_with_no_outcome_is_no_data_naming_the_requester(self):
        result = normalize_intent(
            {"requested_by": "Jamie Chen", "value_hypothesis": "cuts support tickets"}, "T1")
        self.assertEqual(result["verdict"], "NO-DATA")
        self.assertTrue(result["problems"], "a missing desired_outcome must be named, not "
                        "silently defaulted")
        self.assertTrue(any("Jamie Chen" in p for p in result["problems"]),
                        "the human who must supply the outcome must be named in the verdict: "
                        "%r" % result["problems"])

    def test_no_requester_named_is_still_no_data_never_a_crash(self):
        result = normalize_intent({}, "T2")
        self.assertEqual(result["verdict"], "NO-DATA")

    def test_desired_outcome_present_but_only_inferred_is_still_no_data(self):
        """An inferred desired_outcome is never treated as a stated one at a
        tier that requires the outcome explicit."""
        result = normalize_intent(
            {"desired_outcome": "guessed from the ticket title",
             "desired_outcome_inferred": True, "requested_by": "Jamie Chen"}, "T1")
        self.assertEqual(result["verdict"], "NO-DATA")


class TestChangedRequirementNotSuperseded(unittest.TestCase):
    """Case 2: a desired_outcome or value_hypothesis that disagrees with what
    is already on record, with no change_reason, is FAIL -- a contradiction
    this tool holds, not an absence."""

    PREVIOUS = {"desired_outcome": "reduce checkout errors", "requested_by": "Jamie Chen",
                "value_hypothesis": "fewer refunds"}

    def test_a_changed_outcome_with_no_reason_fails(self):
        result = normalize_intent(
            {"desired_outcome": "increase upsell revenue", "requested_by": "Jamie Chen",
             "value_hypothesis": "fewer refunds"}, "T1", previous=self.PREVIOUS)
        self.assertEqual(result["verdict"], "FAIL")
        self.assertTrue(result["problems"])

    def test_the_same_changed_outcome_with_a_reason_on_record_is_not_fail(self):
        result = normalize_intent(
            {"desired_outcome": "increase upsell revenue", "requested_by": "Jamie Chen",
             "value_hypothesis": "fewer refunds",
             "change_reason": "the original goal shipped in T-114; this is the follow-on"},
            "T1", previous=self.PREVIOUS)
        self.assertNotEqual(result["verdict"], "FAIL")

    def test_an_unchanged_outcome_against_the_same_previous_is_not_fail(self):
        result = normalize_intent(dict(self.PREVIOUS), "T1", previous=self.PREVIOUS)
        self.assertNotEqual(result["verdict"], "FAIL")

    def test_no_previous_on_record_is_never_a_changed_requirement(self):
        result = normalize_intent(
            {"desired_outcome": "increase upsell revenue", "requested_by": "Jamie Chen",
             "value_hypothesis": "fewer refunds"}, "T1", previous=None)
        self.assertNotEqual(result["verdict"], "FAIL")


class TestHighImpactWithNoValueStatement(unittest.TestCase):
    """Case 3: a high-impact (T2/T3) change with no value_hypothesis is
    NO-DATA, naming who must supply it."""

    def test_t2_with_no_value_hypothesis_is_no_data(self):
        result = normalize_intent(
            {"desired_outcome": "cut latency under 200ms", "requested_by": "Priya Nair"}, "T2")
        self.assertEqual(result["verdict"], "NO-DATA")
        self.assertTrue(any("Priya Nair" in p for p in result["problems"]), result["problems"])

    def test_t3_with_no_value_hypothesis_is_no_data(self):
        result = normalize_intent(
            {"desired_outcome": "stop the data loss", "requested_by": "Priya Nair"}, "T3")
        self.assertEqual(result["verdict"], "NO-DATA")

    def test_t1_does_not_require_a_value_hypothesis(self):
        """The value_hypothesis requirement is scoped to high-impact tiers
        only; a T1 with an explicit outcome and no value statement is fine."""
        result = normalize_intent(
            {"desired_outcome": "cut onboarding time", "requested_by": "Jamie Chen"}, "T1")
        self.assertEqual(result["verdict"], "PASS")


class TestLowRiskStaysLightweight(unittest.TestCase):
    """Case 9: the floor must not inflate ceremony on low-risk work. A T0
    intake with nothing filled in at all is PASS, not NO-DATA."""

    def test_t0_with_nothing_filled_in_is_pass(self):
        result = normalize_intent({}, "T0")
        self.assertEqual(result["verdict"], "PASS")
        self.assertEqual(result["problems"], [])

    def test_t0_permits_an_inferred_outcome_labeled_as_such(self):
        result = normalize_intent(
            {"desired_outcome": "keep the lights on", "desired_outcome_inferred": True}, "T0")
        self.assertEqual(result["verdict"], "PASS")
        self.assertTrue(result["desired_outcome_inferred"])


class TestSoundCase(unittest.TestCase):
    def test_material_tier_fully_answered_passes(self):
        result = normalize_intent(
            {"desired_outcome": "cut onboarding time to under 5 minutes",
             "requested_by": "Jamie Chen", "value_hypothesis": "fewer drop-offs at signup"},
            "T2")
        self.assertEqual(result["verdict"], "PASS")
        self.assertEqual(result["problems"], [])


class TestUnknownTierRefused(unittest.TestCase):
    def test_an_unrecognized_tier_raises_rather_than_guessing(self):
        with self.assertRaises(ValueError):
            normalize_intent({}, "T9")


class TestDefectOriginNamesWhatItFixes(unittest.TestCase):
    """Case H4.1/H4.2: a defect intake naming a regression row proceeds; one
    naming no row refuses and prints what is missing."""

    def test_defect_naming_a_row_proceeds(self):
        result = normalize_origin({"type": "defect", "fixes": "REG-114"})
        self.assertEqual(result["verdict"], "PASS")
        self.assertEqual(result["problems"], [])
        self.assertEqual(result["type"], "defect")
        self.assertEqual(result["fixes"], "REG-114")

    def test_defect_naming_no_row_refuses_printing_what_is_missing(self):
        result = normalize_origin({"type": "defect"})
        self.assertEqual(result["verdict"], "NO-DATA")
        self.assertTrue(result["problems"], "a missing fixes reference must be named, not "
                        "silently accepted")
        self.assertTrue(any("fixes" in p for p in result["problems"]), result["problems"])

    def test_defect_with_a_placeholder_fixes_still_refuses(self):
        """A placeholder like "TBD" reads as no answer at all, same as
        `answered()` treats every other field this file checks."""
        result = normalize_origin({"type": "defect", "fixes": "TBD"})
        self.assertEqual(result["verdict"], "NO-DATA")


class TestFeatureOriginIsUntouched(unittest.TestCase):
    """Case H4.3: a feature intake, or no origin block at all, is never held
    to the defect rule -- the regression guard for this change."""

    def test_feature_with_no_fixes_passes(self):
        result = normalize_origin({"type": "feature"})
        self.assertEqual(result["verdict"], "PASS")
        self.assertEqual(result["problems"], [])

    def test_blank_origin_defaults_to_feature(self):
        """No origin block at all (every dossier written before this field
        existed) is read as a feature, never as a defect with nothing to
        fix, so an old dossier stays valid with no edit."""
        result = normalize_origin({})
        self.assertEqual(result["verdict"], "PASS")
        self.assertEqual(result["type"], "feature")
        self.assertIsNone(result["fixes"])

    def test_none_origin_defaults_to_feature(self):
        result = normalize_origin(None)
        self.assertEqual(result["verdict"], "PASS")
        self.assertEqual(result["type"], "feature")

    def test_an_unrecognized_type_defaults_to_feature_rather_than_guessing_defect(self):
        result = normalize_origin({"type": "enhancement"})
        self.assertEqual(result["verdict"], "PASS")
        self.assertEqual(result["type"], "feature")


class TestCLIWritesIntentBlock(unittest.TestCase):
    """A thin integration check: the CLI writes an `intent` key without
    crashing, at a low-risk tier (no extra prompts) and at a material tier
    (requested_by and desired_outcome asked)."""

    def setUp(self):
        self.dossier = tempfile.mkdtemp()

    def tearDown(self):
        shutil.rmtree(self.dossier, ignore_errors=True)

    def run_intake(self, stdin_text):
        return subprocess.run([sys.executable, SBE_INTAKE, self.dossier], input=stdin_text,
                              capture_output=True, text=True)

    def read_intake(self):
        with io.open(os.path.join(self.dossier, "00-intake.json"), encoding="utf-8") as fh:
            return json.load(fh)

    def test_t0_intake_writes_intent_block_with_no_extra_prompts(self):
        # every answer at its lowest value: T0, then origin=feature
        out = self.run_intake("no\nn\ny\nn\nnone\nfeature\n")
        self.assertEqual(out.returncode, 0, out.stdout + out.stderr)
        data = self.read_intake()
        self.assertEqual(data["tier"], "T0")
        self.assertIn("intent", data)
        self.assertIsNone(data["intent"]["desired_outcome"])
        self.assertEqual(data["origin"], {"type": "feature", "fixes": None})

    def test_material_tier_intake_asks_for_and_writes_the_outcome(self):
        # crosses_boundary=y alone reaches T1, origin=feature
        out = self.run_intake("no\ny\ny\nn\nnone\nfeature\nJamie Chen\ncut onboarding time\n")
        self.assertEqual(out.returncode, 0, out.stdout + out.stderr)
        data = self.read_intake()
        self.assertEqual(data["tier"], "T1")
        self.assertEqual(data["intent"]["requested_by"], "Jamie Chen")
        self.assertEqual(data["intent"]["desired_outcome"], "cut onboarding time")
        self.assertEqual(data["origin"], {"type": "feature", "fixes": None})

    def test_defect_intake_naming_a_row_writes_origin_and_proceeds(self):
        """H4's own done-check, through the real CLI: a defect intake naming
        a regression row proceeds at whatever tier the five questions
        compute (T1 here, from crosses_boundary=y)."""
        out = self.run_intake(
            "no\ny\ny\nn\nnone\ndefect\nREG-114\nJamie Chen\nfix the checkout crash\n")
        self.assertEqual(out.returncode, 0, out.stdout + out.stderr)
        data = self.read_intake()
        self.assertEqual(data["tier"], "T1")
        self.assertEqual(data["origin"], {"type": "defect", "fixes": "REG-114"})

    def test_defect_intake_naming_no_row_refuses_and_prints_what_is_missing(self):
        """H4's refusal path: a defect answer with a blank `fixes` (EOF
        before the re-ask loop gets an answer) refuses rather than writing a
        defect with nothing named on `fixes`."""
        out = self.run_intake("no\nn\ny\nn\nnone\ndefect\n")
        self.assertNotEqual(out.returncode, 0, out.stdout + out.stderr)
        self.assertFalse(os.path.exists(os.path.join(self.dossier, "00-intake.json")),
                         "a refused defect intake must write nothing")

    def test_intake_stamps_an_opened_at_timestamp(self):
        """H8: the earliest honest 'started' moment for the median-duration-
        per-tier report (`brothersbe.decisions.close_durations_by_tier`)."""
        out = self.run_intake("no\nn\ny\nn\nnone\nfeature\n")
        self.assertEqual(out.returncode, 0, out.stdout + out.stderr)
        data = self.read_intake()
        self.assertRegex(data["openedAt"], r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z$",
                         "openedAt must be a UTC ISO-8601 timestamp: %r" % data.get("openedAt"))

    def test_opened_at_survives_a_re_run_rather_than_moving_forward(self):
        """Re-running intake to fix an answer must not move when the change
        was actually declared, the same rule `previous_intent` already
        applies to the intent block."""
        first = self.run_intake("no\nn\ny\nn\nnone\nfeature\n")
        self.assertEqual(first.returncode, 0, first.stdout + first.stderr)
        first_opened_at = self.read_intake()["openedAt"]

        second = self.run_intake("no\ny\ny\nn\nnone\nfeature\nJamie Chen\ncut onboarding time\n")
        self.assertEqual(second.returncode, 0, second.stdout + second.stderr)
        data = self.read_intake()
        self.assertEqual(data["tier"], "T1", "the re-run's own answers must still be honored")
        self.assertEqual(data["openedAt"], first_opened_at,
                         "a re-run must preserve the first run's openedAt, not restamp it")


class TestBudgetTierMapping(unittest.TestCase):
    """budget_tier_for: the three-way bucket a T0..T3 tier reads as for the
    question budget report, and record_budget_tier's backward-compatible
    read of a stored record that predates the field."""

    def test_t0_is_one_line_fix(self):
        self.assertEqual(budget_tier_for("T0"), "one-line-fix")

    def test_t1_and_t2_are_both_routine(self):
        self.assertEqual(budget_tier_for("T1"), "routine")
        self.assertEqual(budget_tier_for("T2"), "routine")

    def test_t3_is_irreversible(self):
        self.assertEqual(budget_tier_for("T3"), "irreversible")

    def test_an_unrecognized_tier_raises_rather_than_guessing(self):
        with self.assertRaises(ValueError):
            budget_tier_for("T9")

    def test_record_budget_tier_reads_the_field_directly_when_present(self):
        self.assertEqual(record_budget_tier({"tier": "T0", "budget_tier": "irreversible"}),
                         "irreversible")

    def test_record_budget_tier_falls_back_to_the_legacy_tier(self):
        """A record written before this feature existed has no budget_tier
        at all, and must still be classified, not skipped."""
        self.assertEqual(record_budget_tier({"tier": "T2"}), "routine")

    def test_record_budget_tier_is_none_when_neither_field_is_readable(self):
        self.assertIsNone(record_budget_tier({"tier": "T9"}))
        self.assertIsNone(record_budget_tier({}))


class TestBuildOverrides(unittest.TestCase):
    """build_overrides: every stated assumption this run's answers
    overrode, read from the disagreement between `previous_intent` and the
    normalized intent this run computed."""

    def test_no_previous_intent_overrides_nothing(self):
        self.assertEqual(build_overrides(None, {"desired_outcome": "x", "value_hypothesis": None},
                                         None), [])

    def test_an_unchanged_field_is_not_an_override(self):
        previous = {"desired_outcome": "cut onboarding time"}
        result = {"desired_outcome": "cut onboarding time", "value_hypothesis": None}
        self.assertEqual(build_overrides(previous, result, None), [])

    def test_a_changed_field_with_a_reason_is_one_override_naming_both_values(self):
        previous = {"desired_outcome": "reduce checkout errors"}
        result = {"desired_outcome": "increase upsell revenue", "value_hypothesis": None}
        overrides = build_overrides(previous, result, "the original goal shipped already")
        self.assertEqual(len(overrides), 1)
        self.assertEqual(overrides[0], {"field": "desired_outcome",
                                        "previous": "reduce checkout errors",
                                        "new": "increase upsell revenue",
                                        "reason": "the original goal shipped already"})

    def test_both_fields_changing_is_two_overrides(self):
        previous = {"desired_outcome": "reduce checkout errors", "value_hypothesis": "fewer refunds"}
        result = {"desired_outcome": "increase upsell revenue", "value_hypothesis": "more revenue"}
        overrides = build_overrides(previous, result, "follow-on work")
        self.assertEqual(sorted(o["field"] for o in overrides),
                         ["desired_outcome", "value_hypothesis"])


class TestBudgetReport(unittest.TestCase):
    """run_budget_report: the --budget mode's own logic, tested directly
    against seeded records rather than through the CLI (the same reason
    normalize_intent and normalize_origin are tested directly above): the
    interesting cases are shapes of stored JSON, not typed answers."""

    def setUp(self):
        self.root = tempfile.mkdtemp()

    def tearDown(self):
        shutil.rmtree(self.root, ignore_errors=True)

    def write(self, subdir, record):
        d = os.path.join(self.root, subdir)
        os.makedirs(d, exist_ok=True)
        path = os.path.join(d, "00-intake.json")
        with io.open(path, "w", encoding="utf-8") as fh:
            json.dump(record, fh)
        return path

    def run_report(self):
        buf = io.StringIO()
        old_stdout = sys.stdout
        sys.stdout = buf
        try:
            rc = run_budget_report(self.root)
        finally:
            sys.stdout = old_stdout
        return rc, buf.getvalue()

    def test_a_missing_root_is_no_data(self):
        buf = io.StringIO()
        old_stdout = sys.stdout
        sys.stdout = buf
        try:
            rc = run_budget_report(os.path.join(self.root, "does-not-exist"))
        finally:
            sys.stdout = old_stdout
        self.assertEqual(rc, 3)
        self.assertIn("NO-DATA", buf.getvalue())

    def test_an_empty_root_is_no_data(self):
        rc, out = self.run_report()
        self.assertEqual(rc, 3)
        self.assertIn("NO-DATA", out)

    def test_seeded_records_give_the_expected_per_tier_sums(self):
        self.write("a", {"tier": "T0", "budget_tier": "one-line-fix",
                         "questions_asked": [{"question": "x"}], "overrides": []})
        self.write("b", {"tier": "T1", "budget_tier": "routine",
                         "questions_asked": [{"question": "x"}, {"question": "y"}],
                         "overrides": [{"field": "desired_outcome"}]})
        self.write("c", {"tier": "T2", "budget_tier": "routine",
                         "questions_asked": [{"question": "x"}], "overrides": []})
        self.write("d", {"tier": "T3", "budget_tier": "irreversible",
                         "questions_asked": [{"question": "x"}, {"question": "y"},
                                             {"question": "z"}], "overrides": []})
        rc, out = self.run_report()
        self.assertEqual(rc, 0)
        self.assertIn("one-line-fix: 1 intakes, 1 questions, 0 overrides", out)
        self.assertIn("routine: 2 intakes, 3 questions, 1 overrides", out)
        self.assertIn("irreversible: 1 intakes, 3 questions, 0 overrides", out)

    def test_a_routine_record_with_two_questions_is_flagged(self):
        path = self.write("risky", {"tier": "T1", "budget_tier": "routine",
                                    "questions_asked": [{"question": "x"}, {"question": "y"}],
                                    "overrides": []})
        rc, out = self.run_report()
        self.assertEqual(rc, 0)
        self.assertIn("FLAG: routine intake %s drew 2 questions" % path, out)

    def test_a_routine_record_with_one_question_is_not_flagged(self):
        self.write("fine", {"tier": "T2", "budget_tier": "routine",
                            "questions_asked": [{"question": "x"}], "overrides": []})
        rc, out = self.run_report()
        self.assertEqual(rc, 0)
        self.assertNotIn("FLAG", out)

    def test_a_one_line_fix_record_is_never_flagged_no_matter_how_many_questions(self):
        """FLAG is scoped to routine only: a one-line-fix or irreversible
        record that drew many questions is still counted, never flagged."""
        self.write("odd", {"tier": "T0", "budget_tier": "one-line-fix",
                           "questions_asked": [{"question": "x"}, {"question": "y"}],
                           "overrides": []})
        rc, out = self.run_report()
        self.assertEqual(rc, 0)
        self.assertNotIn("FLAG", out)

    def test_a_legacy_record_with_no_budget_tier_is_classified_from_tier(self):
        """A record written before this feature existed carries `tier` but
        no `budget_tier`, `questions_asked` or `overrides` at all; it must
        still be counted (as zero questions, zero overrides), never
        skipped."""
        self.write("legacy", {"tier": "T1", "answers": {}, "override": None})
        rc, out = self.run_report()
        self.assertEqual(rc, 0)
        self.assertIn("routine: 1 intakes, 0 questions, 0 overrides", out)


class TestCLIWritesQuestionBudget(unittest.TestCase):
    """A thin integration check, mirroring TestCLIWritesIntentBlock above:
    the real CLI writes questions_asked, overrides and budget_tier without
    crashing, at a low-risk tier and at a material tier."""

    def setUp(self):
        self.dossier = tempfile.mkdtemp()

    def tearDown(self):
        shutil.rmtree(self.dossier, ignore_errors=True)

    def run_intake(self, stdin_text):
        return subprocess.run([sys.executable, SBE_INTAKE, self.dossier], input=stdin_text,
                              capture_output=True, text=True)

    def read_intake(self):
        with io.open(os.path.join(self.dossier, "00-intake.json"), encoding="utf-8") as fh:
            return json.load(fh)

    def test_t0_intake_writes_one_question_and_no_overrides(self):
        out = self.run_intake("no\nn\ny\nn\nnone\nfeature\n")
        self.assertEqual(out.returncode, 0, out.stdout + out.stderr)
        data = self.read_intake()
        self.assertEqual(data["budget_tier"], "one-line-fix")
        self.assertEqual(len(data["questions_asked"]), 1)
        self.assertEqual(data["overrides"], [])

    def test_material_tier_intake_writes_three_questions(self):
        out = self.run_intake("no\ny\ny\nn\nnone\nfeature\nJamie Chen\ncut onboarding time\n")
        self.assertEqual(out.returncode, 0, out.stdout + out.stderr)
        data = self.read_intake()
        self.assertEqual(data["tier"], "T1")
        self.assertEqual(data["budget_tier"], "routine")
        self.assertEqual(len(data["questions_asked"]), 3)
        questions = [q["question"] for q in data["questions_asked"]]
        self.assertIn("Who wants this? (a named human)", questions)
        self.assertIn("What outcome is desired?", questions)


if __name__ == "__main__":
    unittest.main(verbosity=2)

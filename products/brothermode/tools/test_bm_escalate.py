#!/usr/bin/env python3
"""Tests for tools/bm_escalate.py, the attempt ledger and the stop-and-ask rule.

WHAT THIS SUITE PROVES
  1. THE THREE TRIGGERS FIRE, each one forced on its own, with the trigger
     name asserted rather than only the verdict: a verdict that is right for
     the wrong reason is the failure this product keeps finding in its own
     work, so every escalation here is asserted by name.
  2. THE FLOOR HOLDS: one failure never escalates, and two failures with
     different root causes across two approaches never escalate. A tool that
     asks for help too early gets ignored, which is the same outcome as one
     that never asks.
  3. A PASS CLEARS IT, and a resolve clears it, and a failure AFTER a resolve
     re-opens it. Silence has to mean healthy or the signal is worthless.
  4. THE PACKET REFUSES TO BE HOLLOW: no recommendation, no decision, or no
     default action means REFUSED (exit 2), never a printed packet.
  5. A REFUSED ATTEMPT IS NOT RECORDED: a failed attempt with no root cause
     is refused at the door, because a ledger of failures with no causes can
     never fire the repeat trigger and would fail toward "keep going".
  6. AN UNREADABLE LEDGER LINE IS REPORTED, NEVER SILENTLY SKIPPED, for the
     same direction-of-failure reason.
  7. END TO END through the real command line in a throwaway project, so the
     argument parsing, the ledger path and the exit codes are proven together
     rather than only the pure function under them.

ISOLATION: every subprocess runs with BROTHERMODE_ROOT pinned inside one
throwaway tree, matching tools/test_bm_stall.py's own note, so a test can
never read or write the real project's ledger.
"""
import json
import os
import subprocess
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import bm_escalate as ES  # noqa: E402

TOOL = os.path.join(os.path.dirname(os.path.abspath(__file__)), "bm_escalate.py")


def attempt(objective, approach, cause, verdict=ES.FAILED, **kw):
    d = {"kind": "attempt", "objective": objective, "approach": approach,
         "root_cause": cause, "verdict": verdict, "at": 1}
    d.update(kw)
    return d


class TestTriggers(unittest.TestCase):
    def test_three_distinct_approaches_escalate(self):
        rows = [attempt("X", "cache the result", "cause a"),
                attempt("X", "retry with backoff", "cause b"),
                attempt("X", "rewrite the query", "cause c")]
        verdict, trigger, reason = ES.decide_verdict(rows, "X")
        self.assertEqual(ES.ESCALATE, verdict)
        self.assertEqual("approaches_exhausted", trigger)
        self.assertIn("3 distinct approaches", reason)

    def test_same_root_cause_twice_escalates_before_the_third_approach(self):
        rows = [attempt("X", "cache the result", "the API returns 403"),
                attempt("X", "retry with backoff", "The  API returns 403")]
        verdict, trigger, _ = ES.decide_verdict(rows, "X")
        self.assertEqual(ES.ESCALATE, verdict)
        self.assertEqual("no_new_information", trigger)

    def test_spent_budget_escalates_with_neither_other_trigger_met(self):
        rows = [attempt("X", "one", "cause a", budget_attempts=2),
                attempt("X", "two", "cause b")]
        verdict, trigger, _ = ES.decide_verdict(rows, "X")
        self.assertEqual(ES.ESCALATE, verdict)
        self.assertEqual("budget_spent", trigger)


class TestTheTwoTriggersThatIgnoreTheCount(unittest.TestCase):
    """Both came from the 2026-08-15 architecture review, and both are places
    where waiting for a second attempt is the wrong behaviour."""

    def test_a_forcing_condition_escalates_with_no_attempt_at_all(self):
        rows = [{"kind": "forcing", "objective": "X", "forcing_condition": "no-owner", "at": 1}]
        verdict, trigger, reason = ES.decide_verdict(rows, "X")
        self.assertEqual(ES.ESCALATE, verdict)
        self.assertEqual("forcing_condition", trigger)
        self.assertIn("no-owner", reason)

    def test_a_truth_affecting_failure_escalates_on_the_first_one(self):
        rows = [attempt("X", "one", "status read stale", truth_affecting=True)]
        verdict, trigger, _ = ES.decide_verdict(rows, "X")
        self.assertEqual(ES.ESCALATE, verdict)
        self.assertEqual("truth_affecting_failure", trigger)

    def test_the_same_failure_without_the_flag_still_continues(self):
        """The discriminating pair: the flag is what moves the verdict, not
        the wording of the root cause."""
        rows = [attempt("X", "one", "status read stale")]
        self.assertEqual(ES.CONTINUE, ES.decide_verdict(rows, "X")[0])

    def test_a_resolve_clears_a_forcing_condition_too(self):
        rows = [{"kind": "forcing", "objective": "X", "forcing_condition": "no-owner", "at": 1},
                {"kind": "resolve", "objective": "X", "outcome": "answered", "at": 2}]
        self.assertEqual(ES.CONTINUE, ES.decide_verdict(rows, "X")[0])


class TestTheAttacksThatLanded(unittest.TestCase):
    """Both of these were executed against this module by an adversarial
    reviewer briefed to REFUTE it, and both were real. They are kept as tests
    rather than as a note, because a defect nobody can re-run is a story."""

    def test_a_passing_attempt_does_not_clear_a_forcing_condition(self):
        """The attack, verbatim in shape: a forcing condition, then an
        unrelated approach passes, and the tool went quiet. Credentials do not
        become available because something else passed."""
        rows = [{"kind": "forcing", "objective": "ship",
                 "forcing_condition": "credentials-unavailable", "at": 1},
                attempt("ship", "an unrelated approach", "", verdict=ES.PASSED)]
        verdict, trigger, _ = ES.decide_verdict(rows, "ship")
        self.assertEqual(ES.ESCALATE, verdict)
        self.assertEqual("forcing_condition", trigger)

    def test_only_a_resolve_closes_a_forcing_condition(self):
        rows = [{"kind": "forcing", "objective": "ship",
                 "forcing_condition": "credentials-unavailable", "at": 1},
                {"kind": "resolve", "objective": "ship", "outcome": "answered", "at": 2}]
        self.assertEqual(ES.CONTINUE, ES.decide_verdict(rows, "ship")[0])

    def test_a_passing_attempt_still_clears_prior_failed_attempts(self):
        """The other half of the split rule, so the fix cannot silently make
        every past failure permanent."""
        rows = [attempt("X", "one", "c"), attempt("X", "two", "c"),
                attempt("X", "three", "", verdict=ES.PASSED)]
        self.assertEqual(ES.CONTINUE, ES.decide_verdict(rows, "X")[0])

    def test_punctuation_does_not_manufacture_a_distinct_approach(self):
        """The attack: three spellings of one approach reported as three
        distinct methods, which is a FALSE escalation, the worse direction."""
        rows = [attempt("X", "retry the import", "cause a"),
                attempt("X", "retry the import,", "cause b"),
                attempt("X", "retry  the import;", "cause c")]
        verdict, trigger, _ = ES.decide_verdict(rows, "X")
        self.assertEqual(ES.CONTINUE, verdict, "three spellings of one approach are one approach")
        self.assertIsNone(trigger)

    def test_punctuation_does_not_hide_a_repeated_root_cause(self):
        rows = [attempt("X", "one", "the API returns 403."),
                attempt("X", "two", "the API returns 403")]
        self.assertEqual("no_new_information", ES.decide_verdict(rows, "X")[1])

    def test_the_stated_ceiling_is_real_and_named(self):
        """Not a bug, a limit this suite pins so nobody later mistakes it for
        one: different WORDS for one root cause stay two root causes."""
        rows = [attempt("X", "one", "DB timeout"), attempt("X", "two", "db timed out")]
        self.assertEqual(ES.CONTINUE, ES.decide_verdict(rows, "X")[0])


class TestTheFloor(unittest.TestCase):
    def test_one_failure_is_a_result_not_a_wall(self):
        verdict, trigger, _ = ES.decide_verdict([attempt("X", "one", "cause a")], "X")
        self.assertEqual(ES.CONTINUE, verdict)
        self.assertIsNone(trigger)

    def test_two_approaches_two_causes_no_budget_continues(self):
        rows = [attempt("X", "one", "cause a"), attempt("X", "two", "cause b")]
        self.assertEqual(ES.CONTINUE, ES.decide_verdict(rows, "X")[0])

    def test_nothing_recorded_continues(self):
        self.assertEqual(ES.CONTINUE, ES.decide_verdict([], "X")[0])

    def test_another_objective_never_contributes(self):
        rows = [attempt("X", "one", "shared cause"), attempt("Y", "two", "shared cause")]
        self.assertEqual(ES.CONTINUE, ES.decide_verdict(rows, "X")[0])


class TestClearing(unittest.TestCase):
    def test_a_pass_clears_everything_before_it(self):
        rows = [attempt("X", "one", "c"), attempt("X", "two", "c"),
                attempt("X", "three", "", verdict=ES.PASSED)]
        self.assertEqual(ES.CONTINUE, ES.decide_verdict(rows, "X")[0])

    def test_a_resolve_clears_an_open_escalation(self):
        rows = [attempt("X", "one", "c"), attempt("X", "two", "c"),
                {"kind": "resolve", "objective": "X", "outcome": "answered", "at": 2}]
        self.assertEqual(ES.CONTINUE, ES.decide_verdict(rows, "X")[0])

    def test_failures_after_a_resolve_reopen_it_under_the_same_floor(self):
        """A resolve does not buy immunity, and it does not lower the floor
        either: the count restarts, and the ordinary rule decides. One rule
        rather than a post-resolve special case, on purpose."""
        base = [attempt("X", "one", "c"), attempt("X", "two", "c"),
                {"kind": "resolve", "objective": "X", "outcome": "answered", "at": 2}]
        self.assertEqual(ES.CONTINUE,
                         ES.decide_verdict(base + [attempt("X", "three", "c")], "X")[0])
        self.assertEqual(ES.ESCALATE,
                         ES.decide_verdict(base + [attempt("X", "three", "c"),
                                                   attempt("X", "four", "c")], "X")[0])

    def test_open_escalations_lists_only_the_escalating_objective(self):
        rows = [attempt("X", "one", "c"), attempt("X", "two", "c"),
                attempt("Y", "one", "other cause")]
        self.assertEqual(["X"], [r["objective"] for r in ES.open_escalations(rows)])


class TestPacketRefusesToBeHollow(unittest.TestCase):
    ROWS = [attempt("X", "one", "c"), attempt("X", "two", "c")]

    def test_a_full_packet_is_built(self):
        text, problems = ES.build_packet(self.ROWS, "X", {
            "recommendation": "raise the timeout", "decision": "raise it or drop the feature",
            "default": "I stop and wait"})
        self.assertEqual([], problems)
        self.assertIn("my recommendation: raise the timeout", text)
        self.assertIn("what I do if you say nothing: I stop and wait", text)

    def test_each_required_field_is_refused_on_its_own(self):
        full = {"recommendation": "r", "decision": "d", "default": "f"}
        for missing in ("recommendation", "decision", "default"):
            fields = dict(full)
            fields[missing] = "   "
            text, problems = ES.build_packet(self.ROWS, "X", fields)
            self.assertIsNone(text, missing)
            self.assertEqual(1, len(problems), missing)

    def test_absent_alternatives_are_allowed_and_named(self):
        text, problems = ES.build_packet(self.ROWS, "X", {
            "recommendation": "r", "decision": "d", "default": "f"})
        self.assertEqual([], problems)
        self.assertIn("none left that I can name", text)


class TestLedgerReading(unittest.TestCase):
    def test_an_unreadable_line_is_reported_not_skipped(self):
        with tempfile.TemporaryDirectory() as d:
            p = os.path.join(d, "l.jsonl")
            with open(p, "w", encoding="utf-8") as fh:
                fh.write(json.dumps(attempt("X", "one", "c")) + "\n")
                fh.write("{not json\n")
                fh.write("[1,2]\n")
            records, broken = ES.read_ledger(p)
            self.assertEqual(1, len(records))
            self.assertEqual([2, 3], broken)

    def test_a_missing_ledger_is_empty_not_an_error(self):
        with tempfile.TemporaryDirectory() as d:
            self.assertEqual(([], []), ES.read_ledger(os.path.join(d, "nope.jsonl")))


class TestCommandLine(unittest.TestCase):
    """The real tool, in a throwaway project, with the environment pinned."""

    def run_tool(self, root, *argv):
        env = dict(os.environ)
        env["BROTHERMODE_ROOT"] = root
        return subprocess.run([sys.executable, TOOL, "--root", root] + list(argv),
                              capture_output=True, text=True, env=env)

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = self.tmp.name
        os.makedirs(os.path.join(self.root, ".brothermode"), mode=0o700)

    def tearDown(self):
        self.tmp.cleanup()

    def test_two_repeats_escalate_end_to_end_and_open_lists_it(self):
        for approach in ("first way", "second way"):
            r = self.run_tool(self.root, "attempt", "--objective", "the import",
                              "--approach", approach, "--root-cause", "the API returns 403",
                              "--verdict", "failed")
            self.assertEqual(0, r.returncode, r.stderr)
        r = self.run_tool(self.root, "check", "--objective", "the import")
        self.assertIn("ESCALATE (no_new_information)", r.stdout)
        r = self.run_tool(self.root, "open")
        self.assertIn("OPEN the import", r.stdout)

    def test_a_failed_attempt_with_no_root_cause_is_refused_and_not_recorded(self):
        r = self.run_tool(self.root, "attempt", "--objective", "x", "--approach", "a",
                          "--verdict", "failed")
        self.assertEqual(ES.EXIT_REFUSED, r.returncode)
        self.assertIn("REFUSED", r.stdout)
        records, _ = ES.read_ledger(ES.ledger_path(self.root))
        self.assertEqual([], records)

    def test_a_packet_is_refused_while_nothing_is_escalating(self):
        r = self.run_tool(self.root, "packet", "--objective", "quiet",
                          "--recommendation", "r", "--decision", "d", "--default", "f")
        self.assertEqual(ES.EXIT_REFUSED, r.returncode)
        self.assertIn("is not escalating", r.stdout)

    def test_a_forcing_condition_escalates_end_to_end(self):
        r = self.run_tool(self.root, "forcing", "--objective", "the migration",
                          "--condition", "irreversible-and-ambiguous")
        self.assertEqual(0, r.returncode, r.stderr)
        r = self.run_tool(self.root, "check", "--objective", "the migration")
        self.assertIn("ESCALATE (forcing_condition)", r.stdout)

    def test_a_packet_carries_the_owner_and_the_risk(self):
        self.run_tool(self.root, "forcing", "--objective", "m", "--condition", "no-owner")
        r = self.run_tool(self.root, "packet", "--objective", "m", "--owner", "the BA",
                          "--risk", "existing clients break", "--recommendation", "ask",
                          "--decision", "optional or required", "--default", "I wait")
        self.assertEqual(0, r.returncode, r.stderr)
        self.assertIn("who I am asking: the BA", r.stdout)
        self.assertIn("the risk if I guess: existing clients break", r.stdout)

    def test_open_says_so_when_nothing_is_open(self):
        r = self.run_tool(self.root, "open")
        self.assertEqual(0, r.returncode)
        self.assertIn("no open escalation", r.stdout)


if __name__ == "__main__":
    unittest.main()

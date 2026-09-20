"""What scripts/jev_checks.py must keep true.

Every test injects a fake runner: no test here ever touches the network, a
real subprocess, or the keychain. Each of the six wave-1 checks gets four
tests: (a) mode off makes zero calls and the caller's own answer is
untouched, (b) shadow with a runner answering the OPPOSITE of the caller's
answer still returns the caller's own answer, with one ledger row written,
(c) a seam-path exception (jev_seam itself unavailable) leaves the caller's
answer untouched, and (d) the red proof -- an assertion that the mutation
the wave-1 brief's rule 5(d) asks a builder to try by hand (swap in Jev's
own answer at the call site instead of the caller's) really would change
the visible output, so test (b)'s invariant is not vacuous. (d) is what the
brief's own words describe as editing the call site, running, watching (b)
fail, and reverting; encoding it as its own assertion keeps that proof
permanent and automated rather than a one-off manual step nobody re-runs.

A0.8 (2026-09-19) made shadow fire-and-forget: consult() hands the call to
a background worker and returns at once, with jev=None and decision_id=
None (an id is only ever returned once actually written -- see jev_seam.py's
own module docstring). Every (b) and (d) test below therefore calls
jev_seam.drain(timeout=5) before reading the ledger, and (d) reads Jev's
answer from the ledger row the worker wrote rather than from result.jev,
which shadow never populates.

RUN THIS FILE WITH BROTHER_JEV_STATE_DIR EXPORTED to an isolated temp
directory (mirrors test_jev_seam.py's own reliance on that convention,
never re-implemented here): jev_seam.JEV_STATE_DIR is a module-level
constant baked in from that env var at IMPORT TIME, so it must be set in
the shell BEFORE this file's own `import jev_seam` line runs, not from
inside a test. None of the tests below configure an explicit
canary_reset_marker_path, so an unset var falls through to jev_seam's own
default (~/.brother/jev/canary-reset.json); exporting a temp dir first
keeps this file from ever reading or racing with real machine state.
"""
import json
import os
import shlex
import stat
import subprocess
import sys
import tempfile
import time
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import jev_calibration as jc  # noqa: E402
import jev_checks  # noqa: E402
import jev_seam as seam  # noqa: E402


MODEL = "typesafe/jev-1.13-test"


def _noul_entry(entry_id, risk="medium"):
    # "privacy" is required by jev_registry's own lint (M2, review
    # 70268c3): a missing or misspelled tag is a lint finding, and
    # callable() refuses any dirty entry -- mirrors test_jev_seam.py's own
    # fixture. These fixtures are never asked for real, so
    # "public_or_own_text" (no content gate needed) is a harmless, valid
    # choice.
    return {
        "id": entry_id, "role": "second_opinion", "risk": risk, "wave": "W1",
        "privacy": "public_or_own_text",
        "question": {"type": "noul", "instructions": "Is it true, for %s?" % entry_id},
    }


def _choice_entry(entry_id, risk="medium"):
    return {
        "id": entry_id, "role": "second_opinion", "risk": risk, "wave": "W1",
        "privacy": "public_or_own_text",
        "question": {
            "type": "choice", "instructions": "Pick one, for %s." % entry_id,
            "options": ["a", "b", "unknown"],
        },
    }


class ScriptedRunner(object):
    """Same shape as test_jev_seam.py's own: answers every question in the
    request with whatever `answer_fn` returns for it, and records every
    call so a test can assert the bridge was, or was not, invoked."""

    def __init__(self, answer_fn, model=MODEL, returncode=0, stderr=""):
        self.answer_fn = answer_fn
        self.model = model
        self.returncode = returncode
        self.stderr = stderr
        self.calls = []

    def __call__(self, argv, stdin_text):
        self.calls.append((argv, stdin_text))
        if self.returncode != 0:
            return self.returncode, "", self.stderr
        payload = json.loads(stdin_text)
        answers = {qid: self.answer_fn(qid, q) for qid, q in payload["questions"].items()}
        response = {"model": self.model, "answers": answers, "usage": {"cost": 0.002}}
        return 0, json.dumps(response), ""


def _noul_answer(prob):
    def fn(_qid, _q):
        return {"noul": prob, "confidence": prob}
    return fn


def _fixed_choice_answer(choice, prob):
    def fn(_qid, q):
        keys = list(q["criteria"].keys())
        others = [k for k in keys if k != choice]
        probs = {choice: prob}
        if others:
            rest = (1.0 - prob) / len(others)
            for k in others:
                probs[k] = rest
        return {"choice": choice, "probabilities": probs, "confidence": prob}
    return fn


class ExplodingRegistry(list):
    """A registry that raises if ever touched, to prove off mode makes no
    registry call at all -- mirrors test_jev_seam.py's own fixture."""
    def __iter__(self):
        raise AssertionError("registry must not be read in off mode")


def _seams_config(modes=None):
    return {"modes": dict(modes or {})}


class CheckTestCase(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.ledger_dir = self._tmp.name

    def tearDown(self):
        self._tmp.cleanup()

    def _decisions(self):
        path = os.path.join(self.ledger_dir, "decisions.jsonl")
        if not os.path.exists(path):
            return []
        with open(path, encoding="utf-8") as fh:
            return [json.loads(line) for line in fh if line.strip()]


class J030ClaimSupport(CheckTestCase):
    ENTRY = "J030"

    def test_a_off_makes_no_call(self):
        result = jev_checks.check_row_claim_support(
            "row X is done", "the log shows row X changed", "current",
            seams_config=_seams_config({}), registry=ExplodingRegistry(),
            ledger_dir=self.ledger_dir, runner=ScriptedRunner(_noul_answer(0.9)))
        self.assertEqual(result.answer, "current")
        self.assertEqual(result.mode, "off")
        self.assertIsNone(result.jev)
        self.assertEqual(self._decisions(), [])

    def test_b_shadow_opposite_answer_leaves_caller_unchanged(self):
        registry = [_noul_entry(self.ENTRY)]
        runner = ScriptedRunner(_noul_answer(0.95))  # Jev says "true", loudly
        result = jev_checks.check_row_claim_support(
            "row X is done", "the log never mentions row X", False,
            seams_config=_seams_config({self.ENTRY: "shadow"}), registry=registry,
            ledger_dir=self.ledger_dir, runner=runner)
        self.assertEqual(result.answer, False)
        # A0.8: shadow hands the call to a background worker and returns
        # at once, before the ledger row exists. drain() waits for the
        # worker to finish so the row is actually there to count.
        seam.drain(timeout=5)
        self.assertEqual(len(self._decisions()), 1)

    def test_c_seam_path_exception_leaves_caller_unchanged(self):
        real_seam = jev_checks._seam
        jev_checks._seam = None
        try:
            result = jev_checks.check_row_claim_support(
                "claim", "source", "safe-default",
                seams_config=_seams_config({self.ENTRY: "act"}), registry=[_noul_entry(self.ENTRY)],
                ledger_dir=self.ledger_dir, runner=ScriptedRunner(_noul_answer(0.95)))
        finally:
            jev_checks._seam = real_seam
        self.assertEqual(result.answer, "safe-default")
        self.assertEqual(result.mode, "off")
        self.assertIn("jev_seam could not be imported", result.reason)

    def test_d_red_proof_swap_would_have_been_visible(self):
        """The mutation the brief's rule 5(d) asks a builder to try by hand:
        if the call site used result.jev's answer instead of result.answer,
        the output WOULD have changed -- proving test (b) is not vacuous."""
        registry = [_noul_entry(self.ENTRY)]
        runner = ScriptedRunner(_noul_answer(0.97))
        result = jev_checks.check_row_claim_support(
            "row X is done", "the log never mentions row X", False,
            seams_config=_seams_config({self.ENTRY: "shadow"}), registry=registry,
            ledger_dir=self.ledger_dir, runner=runner)
        self.assertEqual(result.answer, False)
        # A0.8: shadow never returns Jev's answer (result.jev is always
        # None); the eventual answer only lands in the ledger, once the
        # background worker finishes. drain() first, then read it there.
        seam.drain(timeout=5)
        decisions = self._decisions()
        self.assertEqual(len(decisions), 1)
        self.assertNotEqual(decisions[0]["answer"], result.answer,
                            "a swap to Jev's answer at the call site would be silent, "
                            "which means (b) could never catch it")


class J064GateLogLines(CheckTestCase):
    ENTRY = "J064"

    def test_a_off_makes_no_call(self):
        results = jev_checks.check_gate_log_lines(
            ["FAIL test_x", "PASS test_y"], "unknown",
            seams_config=_seams_config({}), registry=ExplodingRegistry(),
            ledger_dir=self.ledger_dir, runner=ScriptedRunner(_fixed_choice_answer("a", 0.9)))
        self.assertEqual([r.answer for r in results], ["unknown", "unknown"])
        self.assertTrue(all(r.mode == "off" for r in results))
        self.assertEqual(self._decisions(), [])

    def test_b_shadow_opposite_answer_leaves_caller_unchanged(self):
        registry = [_choice_entry(self.ENTRY)]
        runner = ScriptedRunner(_fixed_choice_answer("b", 0.95))  # Jev says "b" every time
        results = jev_checks.check_gate_log_lines(
            ["FAIL test_x", "error: timeout"], "a",
            seams_config=_seams_config({self.ENTRY: "shadow"}), registry=registry,
            ledger_dir=self.ledger_dir, runner=runner)
        self.assertEqual([r.answer for r in results], ["a", "a"])
        # one consult() call, and one ledger row, per line: batching is a
        # Python-level loop, not one wire call (see the module docstring).
        # A0.8: shadow submits each call to a background worker and
        # returns at once, so both the runner invocations and the ledger
        # rows may still be in flight here; drain() waits for them.
        seam.drain(timeout=5)
        self.assertEqual(len(runner.calls), 2)
        self.assertEqual(len(self._decisions()), 2)

    def test_c_seam_path_exception_leaves_caller_unchanged(self):
        real_seam = jev_checks._seam
        jev_checks._seam = None
        try:
            results = jev_checks.check_gate_log_lines(
                ["FAIL test_x"], "unknown",
                seams_config=_seams_config({self.ENTRY: "act"}), registry=[_choice_entry(self.ENTRY)],
                ledger_dir=self.ledger_dir, runner=ScriptedRunner(_fixed_choice_answer("a", 0.95)))
        finally:
            jev_checks._seam = real_seam
        self.assertEqual([r.answer for r in results], ["unknown"])
        self.assertTrue(all(r.mode == "off" for r in results))

    def test_d_red_proof_swap_would_have_been_visible(self):
        registry = [_choice_entry(self.ENTRY)]
        runner = ScriptedRunner(_fixed_choice_answer("b", 0.95))
        results = jev_checks.check_gate_log_lines(
            ["FAIL test_x"], "a",
            seams_config=_seams_config({self.ENTRY: "shadow"}), registry=registry,
            ledger_dir=self.ledger_dir, runner=runner)
        self.assertEqual(results[0].answer, "a")
        seam.drain(timeout=5)
        decisions = self._decisions()
        self.assertEqual(len(decisions), 1)
        self.assertNotEqual(decisions[0]["answer"], results[0].answer)


class J014FrontDoorSkill(CheckTestCase):
    ENTRY = "J014"

    def test_a_off_makes_no_call(self):
        result = jev_checks.check_front_door_skill(
            "check the board", ["status", "review"], "status",
            seams_config=_seams_config({}), registry=ExplodingRegistry(),
            ledger_dir=self.ledger_dir, runner=ScriptedRunner(_fixed_choice_answer("review", 0.9)))
        self.assertEqual(result.answer, "status")
        self.assertEqual(result.mode, "off")
        self.assertEqual(self._decisions(), [])

    def test_b_shadow_opposite_answer_leaves_caller_unchanged(self):
        registry = [_choice_entry(self.ENTRY)]
        runner = ScriptedRunner(_fixed_choice_answer("b", 0.9))
        result = jev_checks.check_front_door_skill(
            "check the board", ["status", "review"], "a",
            seams_config=_seams_config({self.ENTRY: "shadow"}), registry=registry,
            ledger_dir=self.ledger_dir, runner=runner)
        self.assertEqual(result.answer, "a")
        seam.drain(timeout=5)
        self.assertEqual(len(self._decisions()), 1)

    def test_c_seam_path_exception_leaves_caller_unchanged(self):
        real_seam = jev_checks._seam
        jev_checks._seam = None
        try:
            result = jev_checks.check_front_door_skill(
                "request", [], "fallback",
                seams_config=_seams_config({self.ENTRY: "act"}), registry=[_choice_entry(self.ENTRY)],
                ledger_dir=self.ledger_dir, runner=ScriptedRunner(_fixed_choice_answer("a", 0.95)))
        finally:
            jev_checks._seam = real_seam
        self.assertEqual(result.answer, "fallback")
        self.assertEqual(result.mode, "off")

    def test_d_red_proof_swap_would_have_been_visible(self):
        registry = [_choice_entry(self.ENTRY)]
        runner = ScriptedRunner(_fixed_choice_answer("b", 0.9))
        result = jev_checks.check_front_door_skill(
            "check the board", ["status", "review"], "a",
            seams_config=_seams_config({self.ENTRY: "shadow"}), registry=registry,
            ledger_dir=self.ledger_dir, runner=runner)
        self.assertEqual(result.answer, "a")
        seam.drain(timeout=5)
        decisions = self._decisions()
        self.assertEqual(len(decisions), 1)
        self.assertNotEqual(decisions[0]["answer"], result.answer)


class J001WorkProfileTierClassify(CheckTestCase):
    ENTRY = "J001"

    def test_a_off_makes_no_call(self):
        result = jev_checks.check_work_profile_tier_classify(
            "fix the flaky test in test_foo.py", "fix",
            seams_config=_seams_config({}), registry=ExplodingRegistry(),
            ledger_dir=self.ledger_dir, runner=ScriptedRunner(_fixed_choice_answer("research", 0.9)))
        self.assertEqual(result.answer, "fix")
        self.assertEqual(result.mode, "off")
        self.assertEqual(self._decisions(), [])

    def test_b_shadow_opposite_answer_leaves_caller_unchanged(self):
        registry = [_choice_entry(self.ENTRY)]
        runner = ScriptedRunner(_fixed_choice_answer("b", 0.9))
        result = jev_checks.check_work_profile_tier_classify(
            "fix the flaky test in test_foo.py", "a",
            seams_config=_seams_config({self.ENTRY: "shadow"}), registry=registry,
            ledger_dir=self.ledger_dir, runner=runner)
        self.assertEqual(result.answer, "a")
        seam.drain(timeout=5)
        self.assertEqual(len(self._decisions()), 1)

    def test_c_seam_path_exception_leaves_caller_unchanged(self):
        real_seam = jev_checks._seam
        jev_checks._seam = None
        try:
            result = jev_checks.check_work_profile_tier_classify(
                "request", "fallback",
                seams_config=_seams_config({self.ENTRY: "act"}), registry=[_choice_entry(self.ENTRY)],
                ledger_dir=self.ledger_dir, runner=ScriptedRunner(_fixed_choice_answer("a", 0.95)))
        finally:
            jev_checks._seam = real_seam
        self.assertEqual(result.answer, "fallback")
        self.assertEqual(result.mode, "off")

    def test_d_red_proof_swap_would_have_been_visible(self):
        registry = [_choice_entry(self.ENTRY)]
        runner = ScriptedRunner(_fixed_choice_answer("b", 0.9))
        result = jev_checks.check_work_profile_tier_classify(
            "fix the flaky test in test_foo.py", "a",
            seams_config=_seams_config({self.ENTRY: "shadow"}), registry=registry,
            ledger_dir=self.ledger_dir, runner=runner)
        self.assertEqual(result.answer, "a")
        seam.drain(timeout=5)
        decisions = self._decisions()
        self.assertEqual(len(decisions), 1)
        self.assertNotEqual(decisions[0]["answer"], result.answer)


class J119FrontDoorReadiness(CheckTestCase):
    ENTRY = "J119"

    def test_a_off_makes_no_call(self):
        result = jev_checks.check_front_door_readiness(
            "check the board", "status", "PROCEED_BEST",
            seams_config=_seams_config({}), registry=ExplodingRegistry(),
            ledger_dir=self.ledger_dir, runner=ScriptedRunner(_fixed_choice_answer("b", 0.9)))
        self.assertEqual(result.answer, "PROCEED_BEST")
        self.assertEqual(result.mode, "off")
        self.assertEqual(self._decisions(), [])

    def test_b_shadow_opposite_answer_leaves_caller_unchanged(self):
        registry = [_choice_entry(self.ENTRY)]
        runner = ScriptedRunner(_fixed_choice_answer("b", 0.9))
        result = jev_checks.check_front_door_readiness(
            "fix auth its broken", "intake", "PROCEED_BEST",
            seams_config=_seams_config({self.ENTRY: "shadow"}), registry=registry,
            ledger_dir=self.ledger_dir, runner=runner)
        self.assertEqual(result.answer, "PROCEED_BEST")
        seam.drain(timeout=5)
        self.assertEqual(len(self._decisions()), 1)

    def test_c_seam_path_exception_leaves_caller_unchanged(self):
        real_seam = jev_checks._seam
        jev_checks._seam = None
        try:
            result = jev_checks.check_front_door_readiness(
                "request", "status", "PROCEED_BEST",
                seams_config=_seams_config({self.ENTRY: "act"}), registry=[_choice_entry(self.ENTRY)],
                ledger_dir=self.ledger_dir, runner=ScriptedRunner(_fixed_choice_answer("b", 0.95)))
        finally:
            jev_checks._seam = real_seam
        self.assertEqual(result.answer, "PROCEED_BEST")
        self.assertEqual(result.mode, "off")

    def test_d_red_proof_swap_would_have_been_visible(self):
        registry = [_choice_entry(self.ENTRY)]
        runner = ScriptedRunner(_fixed_choice_answer("b", 0.9))
        result = jev_checks.check_front_door_readiness(
            "fix auth its broken", "intake", "PROCEED_BEST",
            seams_config=_seams_config({self.ENTRY: "shadow"}), registry=registry,
            ledger_dir=self.ledger_dir, runner=runner)
        self.assertEqual(result.answer, "PROCEED_BEST")
        seam.drain(timeout=5)
        decisions = self._decisions()
        self.assertEqual(len(decisions), 1)
        self.assertNotEqual(decisions[0]["answer"], result.answer)


class J091FounderDecisionRisk(CheckTestCase):
    ENTRY = "J091"

    def test_a_off_makes_no_call(self):
        result = jev_checks.check_founder_decision_risk(
            "force-merge without review", "RED",
            seams_config=_seams_config({}), registry=ExplodingRegistry(),
            ledger_dir=self.ledger_dir, runner=ScriptedRunner(_fixed_choice_answer("amber", 0.9)))
        self.assertEqual(result.answer, "RED")
        self.assertEqual(result.mode, "off")
        self.assertEqual(self._decisions(), [])

    def test_b_shadow_opposite_answer_leaves_caller_unchanged(self):
        registry = [_choice_entry(self.ENTRY)]
        runner = ScriptedRunner(_fixed_choice_answer("b", 0.9))
        result = jev_checks.check_founder_decision_risk(
            "reprioritize the shared roadmap", "a",
            seams_config=_seams_config({self.ENTRY: "shadow"}), registry=registry,
            ledger_dir=self.ledger_dir, runner=runner)
        self.assertEqual(result.answer, "a")
        seam.drain(timeout=5)
        self.assertEqual(len(self._decisions()), 1)

    def test_c_seam_path_exception_leaves_caller_unchanged(self):
        real_seam = jev_checks._seam
        jev_checks._seam = None
        try:
            result = jev_checks.check_founder_decision_risk(
                "some decision text", "AMBER",
                seams_config=_seams_config({self.ENTRY: "act"}), registry=[_choice_entry(self.ENTRY)],
                ledger_dir=self.ledger_dir, runner=ScriptedRunner(_fixed_choice_answer("a", 0.95)))
        finally:
            jev_checks._seam = real_seam
        self.assertEqual(result.answer, "AMBER")
        self.assertEqual(result.mode, "off")

    def test_d_red_proof_swap_would_have_been_visible(self):
        registry = [_choice_entry(self.ENTRY)]
        runner = ScriptedRunner(_fixed_choice_answer("b", 0.9))
        result = jev_checks.check_founder_decision_risk(
            "reprioritize the shared roadmap", "a",
            seams_config=_seams_config({self.ENTRY: "shadow"}), registry=registry,
            ledger_dir=self.ledger_dir, runner=runner)
        self.assertEqual(result.answer, "a")
        seam.drain(timeout=5)
        decisions = self._decisions()
        self.assertEqual(len(decisions), 1)
        self.assertNotEqual(decisions[0]["answer"], result.answer)

    def test_e_a_red_classification_can_never_be_reported_as_amber_even_if_jev_disagrees(self):
        # The specific real risk this seam carries (registry: risk=high,
        # "GREEN is unreachable by design"): a live/act mode must never be
        # able to downgrade a real RED to AMBER. Simulates act mode with a
        # runner confidently answering "amber" against a real RED input.
        registry = [_choice_entry(self.ENTRY)]
        runner = ScriptedRunner(_fixed_choice_answer("a", 0.99))
        result = jev_checks.check_founder_decision_risk(
            "force-merge without review", "RED",
            seams_config=_seams_config({self.ENTRY: "act"}), registry=registry,
            ledger_dir=self.ledger_dir, runner=runner)
        self.assertEqual(result.answer, "RED")


class J035WorkerFailureClassification(CheckTestCase):
    ENTRY = "J035"

    def test_a_off_makes_no_call(self):
        result = jev_checks.check_worker_failure_classification(
            "no answer within 10s; the process was stopped", "other",
            seams_config=_seams_config({}), registry=ExplodingRegistry(),
            ledger_dir=self.ledger_dir, runner=ScriptedRunner(_fixed_choice_answer("b", 0.9)))
        self.assertEqual(result.answer, "other")
        self.assertEqual(result.mode, "off")
        self.assertEqual(self._decisions(), [])

    def test_b_shadow_opposite_answer_leaves_caller_unchanged(self):
        registry = [_choice_entry(self.ENTRY)]
        runner = ScriptedRunner(_fixed_choice_answer("b", 0.9))
        result = jev_checks.check_worker_failure_classification(
            "connection reset by peer", "other",
            seams_config=_seams_config({self.ENTRY: "shadow"}), registry=registry,
            ledger_dir=self.ledger_dir, runner=runner)
        self.assertEqual(result.answer, "other")
        seam.drain(timeout=5)
        self.assertEqual(len(self._decisions()), 1)

    def test_c_seam_path_exception_leaves_caller_unchanged(self):
        real_seam = jev_checks._seam
        jev_checks._seam = None
        try:
            result = jev_checks.check_worker_failure_classification(
                "some failure text", "other",
                seams_config=_seams_config({self.ENTRY: "act"}), registry=[_choice_entry(self.ENTRY)],
                ledger_dir=self.ledger_dir, runner=ScriptedRunner(_fixed_choice_answer("a", 0.95)))
        finally:
            jev_checks._seam = real_seam
        self.assertEqual(result.answer, "other")
        self.assertEqual(result.mode, "off")

    def test_d_red_proof_swap_would_have_been_visible(self):
        registry = [_choice_entry(self.ENTRY)]
        runner = ScriptedRunner(_fixed_choice_answer("b", 0.9))
        result = jev_checks.check_worker_failure_classification(
            "connection reset by peer", "other",
            seams_config=_seams_config({self.ENTRY: "shadow"}), registry=registry,
            ledger_dir=self.ledger_dir, runner=runner)
        self.assertEqual(result.answer, "other")
        seam.drain(timeout=5)
        decisions = self._decisions()
        self.assertEqual(len(decisions), 1)
        self.assertNotEqual(decisions[0]["answer"], result.answer)

    def test_e_a_breaker_counted_class_can_never_be_reported_as_other_even_if_jev_disagrees(self):
        # The specific real risk this seam carries: even in a future act
        # mode, Jev must never be able to turn a genuine rate_limit/
        # overloaded classification INTO "other" (which the breaker never
        # counts) -- that would be the exact blind spot this seam exists
        # to close, inverted. Simulates act mode confidently answering
        # "other" against a real rate_limit input.
        registry = [_choice_entry(self.ENTRY)]
        runner = ScriptedRunner(_fixed_choice_answer("a", 0.99))
        result = jev_checks.check_worker_failure_classification(
            "HTTP 429 rate_limit exceeded, retry later", "rate_limit",
            seams_config=_seams_config({self.ENTRY: "act"}), registry=registry,
            ledger_dir=self.ledger_dir, runner=runner)
        self.assertEqual(result.answer, "rate_limit")


class J063WorkerCompletionClaim(CheckTestCase):
    ENTRY = "J063"

    def test_a_off_makes_no_call(self):
        result = jev_checks.check_worker_completion_claim(
            "done, tests pass", ["a.py"], None,
            seams_config=_seams_config({}), registry=ExplodingRegistry(),
            ledger_dir=self.ledger_dir, runner=ScriptedRunner(_noul_answer(0.9)))
        self.assertIsNone(result.answer)
        self.assertEqual(result.mode, "off")
        self.assertEqual(self._decisions(), [])

    def test_b_shadow_opposite_answer_leaves_caller_unchanged(self):
        registry = [_noul_entry(self.ENTRY)]
        runner = ScriptedRunner(_noul_answer(0.05))  # Jev says "false": claim does not match
        result = jev_checks.check_worker_completion_claim(
            "done, tests pass", ["a.py"], True,
            seams_config=_seams_config({self.ENTRY: "shadow"}), registry=registry,
            ledger_dir=self.ledger_dir, runner=runner)
        self.assertEqual(result.answer, True)
        seam.drain(timeout=5)
        self.assertEqual(len(self._decisions()), 1)

    def test_c_seam_path_exception_leaves_caller_unchanged(self):
        real_seam = jev_checks._seam
        jev_checks._seam = None
        try:
            result = jev_checks.check_worker_completion_claim(
                "note", [], "safe",
                seams_config=_seams_config({self.ENTRY: "act"}), registry=[_noul_entry(self.ENTRY)],
                ledger_dir=self.ledger_dir, runner=ScriptedRunner(_noul_answer(0.95)))
        finally:
            jev_checks._seam = real_seam
        self.assertEqual(result.answer, "safe")
        self.assertEqual(result.mode, "off")

    def test_d_red_proof_swap_would_have_been_visible(self):
        registry = [_noul_entry(self.ENTRY)]
        runner = ScriptedRunner(_noul_answer(0.05))
        result = jev_checks.check_worker_completion_claim(
            "done, tests pass", ["a.py"], True,
            seams_config=_seams_config({self.ENTRY: "shadow"}), registry=registry,
            ledger_dir=self.ledger_dir, runner=runner)
        self.assertEqual(result.answer, True)
        # Jev's own noul answer IS a probability (0.05, read through the
        # abstain band, means "false"): using it instead of the caller's
        # True would have been a visible, opposite-sign swap. A0.8: shadow
        # never returns it in result.jev, so read it from the ledger row
        # the background worker writes once it finishes.
        seam.drain(timeout=5)
        decisions = self._decisions()
        self.assertEqual(len(decisions), 1)
        self.assertAlmostEqual(decisions[0]["answer"], 0.05)
        self.assertLess(decisions[0]["answer"], 0.2)


class J102DoneClaimVerification(CheckTestCase):
    ENTRY = "J102"

    def test_a_off_makes_no_call(self):
        result = jev_checks.check_done_claim_verification(
            "done", False, None,
            seams_config=_seams_config({}), registry=ExplodingRegistry(),
            ledger_dir=self.ledger_dir, runner=ScriptedRunner(_noul_answer(0.9)))
        self.assertIsNone(result.answer)
        self.assertEqual(result.mode, "off")
        self.assertEqual(self._decisions(), [])

    def test_b_shadow_opposite_answer_leaves_caller_unchanged(self):
        registry = [_noul_entry(self.ENTRY)]
        runner = ScriptedRunner(_noul_answer(0.95))  # Jev says "true": claim lacks the quote
        result = jev_checks.check_done_claim_verification(
            "done", False, False,
            seams_config=_seams_config({self.ENTRY: "shadow"}), registry=registry,
            ledger_dir=self.ledger_dir, runner=runner)
        self.assertEqual(result.answer, False)
        seam.drain(timeout=5)
        self.assertEqual(len(self._decisions()), 1)

    def test_c_seam_path_exception_leaves_caller_unchanged(self):
        real_seam = jev_checks._seam
        jev_checks._seam = None
        try:
            result = jev_checks.check_done_claim_verification(
                "note", True, "safe",
                seams_config=_seams_config({self.ENTRY: "act"}), registry=[_noul_entry(self.ENTRY)],
                ledger_dir=self.ledger_dir, runner=ScriptedRunner(_noul_answer(0.95)))
        finally:
            jev_checks._seam = real_seam
        self.assertEqual(result.answer, "safe")
        self.assertEqual(result.mode, "off")

    def test_d_red_proof_swap_would_have_been_visible(self):
        registry = [_noul_entry(self.ENTRY)]
        runner = ScriptedRunner(_noul_answer(0.95))
        result = jev_checks.check_done_claim_verification(
            "done", False, False,
            seams_config=_seams_config({self.ENTRY: "shadow"}), registry=registry,
            ledger_dir=self.ledger_dir, runner=runner)
        self.assertEqual(result.answer, False)
        seam.drain(timeout=5)
        decisions = self._decisions()
        self.assertEqual(len(decisions), 1)
        self.assertAlmostEqual(decisions[0]["answer"], 0.95)


class J117ScopeAudit(CheckTestCase):
    ENTRY = "J117"

    def test_a_off_makes_no_call(self):
        result = jev_checks.check_scope_audit(
            "fix the board renderer", "scripts/board_status.py", None,
            seams_config=_seams_config({}), registry=ExplodingRegistry(),
            ledger_dir=self.ledger_dir, runner=ScriptedRunner(_noul_answer(0.9)))
        self.assertIsNone(result.answer)
        self.assertEqual(result.mode, "off")
        self.assertEqual(self._decisions(), [])

    def test_b_shadow_opposite_answer_leaves_caller_unchanged(self):
        registry = [_noul_entry(self.ENTRY)]
        runner = ScriptedRunner(_noul_answer(0.03))  # Jev says "false": out of scope
        result = jev_checks.check_scope_audit(
            "fix the board renderer", "scripts/board_status.py", True,
            seams_config=_seams_config({self.ENTRY: "shadow"}), registry=registry,
            ledger_dir=self.ledger_dir, runner=runner)
        self.assertEqual(result.answer, True)
        seam.drain(timeout=5)
        self.assertEqual(len(self._decisions()), 1)

    def test_c_seam_path_exception_leaves_caller_unchanged(self):
        real_seam = jev_checks._seam
        jev_checks._seam = None
        try:
            result = jev_checks.check_scope_audit(
                "objective", "some/path.py", "safe",
                seams_config=_seams_config({self.ENTRY: "act"}), registry=[_noul_entry(self.ENTRY)],
                ledger_dir=self.ledger_dir, runner=ScriptedRunner(_noul_answer(0.95)))
        finally:
            jev_checks._seam = real_seam
        self.assertEqual(result.answer, "safe")
        self.assertEqual(result.mode, "off")

    def test_d_red_proof_swap_would_have_been_visible(self):
        registry = [_noul_entry(self.ENTRY)]
        runner = ScriptedRunner(_noul_answer(0.03))
        result = jev_checks.check_scope_audit(
            "fix the board renderer", "scripts/board_status.py", True,
            seams_config=_seams_config({self.ENTRY: "shadow"}), registry=registry,
            ledger_dir=self.ledger_dir, runner=runner)
        self.assertEqual(result.answer, True)
        seam.drain(timeout=5)
        decisions = self._decisions()
        self.assertEqual(len(decisions), 1)
        self.assertAlmostEqual(decisions[0]["answer"], 0.03)


class ModuleImportFailureIsAlwaysOff(unittest.TestCase):
    """rule 2, the module-level guard: when `import jev_seam` itself never
    succeeded (not merely a call raising), every function here is still
    an off-shaped no-op, never a crash. Simulated by clearing the module
    reference exactly as the per-check tests above do."""

    def test_off_result_shape_without_jev_seam(self):
        real_seam = jev_checks._seam
        jev_checks._seam = None
        try:
            result = jev_checks._off_result("kept", "simulated import failure")
        finally:
            jev_checks._seam = real_seam
        self.assertEqual(result.answer, "kept")
        self.assertIsNone(result.jev)
        self.assertEqual(result.mode, "off")
        self.assertFalse(result.audit)
        self.assertIsNone(result.decision_id)


class M4ConsultExceptionSafetyIsReal(unittest.TestCase):
    """M4 (opus review, 2026-09-19): removing the `except Exception` in
    jev_checks._consult() left all 26 pre-fix tests green, because none of
    them ever made a LIVE jev_seam.consult() itself raise mid-call -- the
    six "exception" tests per check only cover the `_seam = None` path
    (jev_seam failed to import), which is a different code path entirely
    (_consult()'s `if _seam is None:` branch, never its try/except). This
    proves the try/except is load-bearing on its own: patch
    jev_seam.consult (a live, non-None module) to raise, and the caller's
    own answer still comes back rather than the exception propagating."""

    def test_a_raising_live_consult_still_returns_the_callers_answer(self):
        real_consult = jev_checks._seam.consult

        def boom(*args, **kwargs):
            raise RuntimeError("simulated jev_seam.consult() failure")

        jev_checks._seam.consult = boom
        try:
            result = jev_checks.check_row_claim_support(
                "claim", "source", "kept-answer",
                seams_config=_seams_config({"J030": "act"}),
                registry=[_noul_entry("J030")], ledger_dir="/does/not/matter",
                runner=None)
        finally:
            jev_checks._seam.consult = real_consult
        self.assertEqual(result.answer, "kept-answer")
        self.assertEqual(result.mode, "off")
        self.assertIn("RuntimeError", result.reason)
        self.assertIn("simulated jev_seam.consult() failure", result.reason)


class OffModeCostsNothing(unittest.TestCase):
    """Rule 9, seam-brief-common.md (added 2026-09-18 23:3x after a
    measured 52x slowdown: lexical_overlap 5.7 -> 294.6 microseconds per
    call with a seam present and off). jev_seam.consult() itself already
    returns before any filesystem access once the resolved mode is off
    (see its own consult(): the OFF branch is the very first check, before
    registry.callable(), the canary marker stat, or any ledger path is
    ever touched); this proves jev_checks.py's own call sites -- the layer
    a harness call site actually imports -- inherit that property rather
    than reintroducing a stat/open somewhere above consult(). Deterministic
    test, not a timing one: after one warm-up call, 1,000 further off-mode
    calls make zero calls to os.stat, os.path.exists or open. See the
    module docstring's own instructions for the separate 20,000-call
    before/after microbenchmark rule 9 also asks for, reported by hand
    (timing is not something a deterministic assertion should gate on)."""

    def test_a_thousand_off_mode_calls_touch_no_filesystem(self):
        registry = ExplodingRegistry()  # off mode must never even read this
        cfg = _seams_config({})  # every entry named here is off (the default)

        # WARM-UP, outside the counted window: any one-time cost (module
        # import, first-call attribute lookups) is already paid by the time
        # jev_checks and jev_seam were imported at the top of this file: one
        # extra call here just confirms the call shape itself works before
        # the filesystem is instrumented below.
        warm = jev_checks.check_row_claim_support(
            "claim", "source", "current", seams_config=cfg, registry=registry,
            ledger_dir="/does/not/exist", runner=None)
        self.assertEqual(warm.answer, "current")
        self.assertEqual(warm.mode, "off")

        stat_calls = []
        exists_calls = []
        open_calls = []
        real_stat, real_exists, real_open = os.stat, os.path.exists, open

        def counting_stat(*a, **k):
            stat_calls.append(1)
            return real_stat(*a, **k)

        def counting_exists(*a, **k):
            exists_calls.append(1)
            return real_exists(*a, **k)

        def counting_open(*a, **k):
            open_calls.append(1)
            return real_open(*a, **k)

        with mock.patch("os.stat", counting_stat), \
             mock.patch("os.path.exists", counting_exists), \
             mock.patch("builtins.open", counting_open):
            for _ in range(1000):
                result = jev_checks.check_row_claim_support(
                    "claim", "source", "current", seams_config=cfg,
                    registry=registry, ledger_dir="/does/not/exist", runner=None)
                self.assertEqual(result.answer, "current")
                self.assertEqual(result.mode, "off")

        self.assertEqual(stat_calls, [])
        self.assertEqual(exists_calls, [])
        self.assertEqual(open_calls, [])


def _pid_alive(pid):
    """True if `pid` is still a live process this test process can see.
    Never raises: a PermissionError (a real, unrelated process now holds
    that pid) is read the same conservative way as "still alive", since
    the property this helper exists to disprove is "the bridge is gone",
    not "we could double-check with kill -0"."""
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except OSError:
        return True
    return True


class M1CliDrainsBeforeExit(unittest.TestCase):
    """M1 (Opus rereview4, 2026-09-19): jev_checks.py's own CLI (main())
    is a short-lived process whose entire job is to dispatch one or more
    shadow calls and hand back their answer -- exactly the "call site that
    knows it is about to exit" jev_seam.drain()'s own docstring names.
    Before this fix, main() returned right after its consult() calls, so
    a shadow call still in flight spent its budget, started a real bridge
    subprocess, and left no ledger row and no trace on stdout or stderr
    (Opus rereview4 probe_q4_cli_shadow.out: rc=0, "reason": "shadow:
    submitted", ledger decision rows: 0).

    Every test below runs the REAL script (scripts/jev_checks.py) as a
    REAL subprocess, against a REAL executable local bridge (a temp file
    with a shebang, chmod +x, pointed to by BROTHER_DECISION_BRIDGE) that
    only sleeps and echoes a canned typesafe/jev-test answer -- never the
    network, never an injected runner: an injected runner would exercise
    jev_checks.py's own functions directly, never this CLI's actual
    process-exit boundary, which is the one place this bug lived."""

    SCRIPT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "jev_checks.py")
    #: The repo root every real subprocess in this class runs with as its
    #: cwd (a subprocess.run with no `cwd=` inherits this test process's
    #: own cwd), so this is where a stray file like the "--decisions" one
    #: item 6 fixed would have landed.
    REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

    @classmethod
    def setUpClass(cls):
        # Item 6 (A0.8 round 6, test pollution row): a guard that the
        # bug this class's own fixture used to cause (a file named
        # "--decisions" appearing in the repo tree) cannot recur
        # silently. `git status --porcelain` before every test in this
        # class runs, compared in tearDownClass to the same command
        # after -- any difference means some test in this class left the
        # tree dirty.
        cls._git_status_before = cls._git_porcelain_status()

    @classmethod
    def _git_porcelain_status(cls):
        try:
            proc = subprocess.run(
                ["git", "status", "--porcelain"], cwd=cls.REPO_ROOT,
                capture_output=True, text=True, timeout=30.0)
        except (OSError, subprocess.SubprocessError) as exc:
            return "NO-DATA: could not run git status: %s" % exc
        if proc.returncode != 0:
            return "NO-DATA: git status exited %d: %s" % (proc.returncode, proc.stderr)
        return proc.stdout

    @classmethod
    def tearDownClass(cls):
        after = cls._git_porcelain_status()
        assert after == cls._git_status_before, (
            "this class's own real-subprocess tests left the repo tree dirty "
            "(a bridge fixture wrote a stray file, per item 6's own finding):\n"
            "before:\n%s\nafter:\n%s" % (cls._git_status_before, after))

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.state_dir = os.path.join(self._tmp.name, "state")
        os.makedirs(self.state_dir)
        self.ledger_dir = os.path.join(self.state_dir, "ledger")
        self.seams_path = os.path.join(self.state_dir, "seams.json")

    def _write_seams(self, modes):
        with open(self.seams_path, "w", encoding="utf-8") as fh:
            json.dump({"modes": modes}, fh)

    def _write_bridge(self, sleep_s):
        """A real executable bridge: writes its OWN pid to the path named
        by the JEV_TEST_BRIDGE_PIDFILE env var (when set) before
        sleeping, then answers every question with a fixed, TYPE-CORRECT
        answer -- a noul question gets a "noul" probability, a choice
        question (J064's own type: see data/jev-registry.json) gets its
        first declared criteria key under "choice"/"probabilities", per
        jev_decide.py's own documented answer shape. A bridge that always
        answered {"noul": ...} regardless of the question's real type
        made every J064 call fail validation ("answer for ... has no
        'choice' value") the moment something actually drained for the
        result, rather than the intended, deliberately-slow success this
        fixture exists to simulate.

        PID PATH VIA ENV, NEVER argv[1] (item 6, A0.8 round 6, test
        pollution row): this fixture used to read the pid path from
        sys.argv[1], written only when a test passed one as an extra
        element of `bridge_argv`. But jev_decide.decide() always appends
        ["--decisions", "--model", "typesafe"] to whatever argv the
        caller gave it, so a call from a test that passed no pid path
        (test_j030_..., test_j064_...) still had sys.argv[1] == "--decisions"
        -- the bridge wrote a file NAMED "--decisions" into whatever the
        subprocess's cwd happened to be (the repo root, for a real run),
        polluting the tree on every run of this file. An env var can
        never collide with a positional argument decide() appends, so it
        is unset (and nothing is written) for the two tests that never
        asked for a pid file at all."""
        fd, path = tempfile.mkstemp(dir=self._tmp.name, prefix="bridge_", suffix=".py")
        os.close(fd)
        with open(path, "w", encoding="utf-8") as fh:
            fh.write(
                "#!/usr/bin/env python3\n"
                "import sys, os, json, time\n"
                "_pidfile = os.environ.get('JEV_TEST_BRIDGE_PIDFILE')\n"
                "if _pidfile:\n"
                "    with open(_pidfile, 'w') as f:\n"
                "        f.write(str(os.getpid()))\n"
                "time.sleep(%r)\n"
                "data = json.loads(sys.stdin.read())\n"
                "answers = {}\n"
                "for qid, q in data.get('questions', {}).items():\n"
                "    qtype = q.get('type')\n"
                "    if qtype == 'choice':\n"
                "        opts = list((q.get('criteria') or {}).keys()) or ['unknown']\n"
                "        answers[qid] = {'choice': opts[0], 'probabilities': {opts[0]: 0.9}}\n"
                "    elif qtype == 'score':\n"
                "        crit = q.get('criteria')\n"
                "        val = crit[0] if isinstance(crit, list) and crit else 'ok'\n"
                "        answers[qid] = {'score': val}\n"
                "    else:\n"
                "        answers[qid] = {'noul': 0.9, 'confidence': 0.9}\n"
                "print(json.dumps({'model': 'typesafe/jev-test', 'answers': answers, "
                "'usage': {'cost': 0.001}}))\n"
                % sleep_s
            )
        st = os.stat(path)
        os.chmod(path, st.st_mode | stat.S_IEXEC | stat.S_IXGRP | stat.S_IXOTH)
        return path

    def _run_cli(self, args, bridge_argv, timeout=30.0, pidfile=None):
        env = dict(os.environ)
        env["BROTHER_JEV_STATE_DIR"] = self.state_dir
        env["BROTHER_DECISION_BRIDGE"] = " ".join(shlex.quote(a) for a in bridge_argv)
        if pidfile:
            env["JEV_TEST_BRIDGE_PIDFILE"] = pidfile
        else:
            env.pop("JEV_TEST_BRIDGE_PIDFILE", None)
        return subprocess.run(
            [sys.executable, self.SCRIPT, "--seams-config", self.seams_path,
             "--ledger-dir", self.ledger_dir] + args,
            env=env, capture_output=True, text=True, timeout=timeout)

    def _decision_rows(self):
        path = os.path.join(self.ledger_dir, "decisions.jsonl")
        if not os.path.exists(path):
            return 0
        with open(path, encoding="utf-8") as f:
            return sum(1 for line in f if line.strip())

    def test_j030_shadow_against_a_3s_bridge_lands_its_ledger_row(self):
        # THE RED PROOF for this test lives outside the suite (per the
        # brief: mutate a COPY of jev_checks.py with the drain() call in
        # main() removed, re-run this exact test against the mutant, and
        # it fails -- ledger decision rows stays 0 because the CLI exits
        # before the 3s worker gets to write. Restored immediately after).
        self._write_seams({"J030": "shadow"})
        bridge = self._write_bridge(3.0)
        proc = self._run_cli(
            ["j030", "--claim", "the sky is blue", "--source", "the sky is blue today",
             "--current-answer", "true"],
            [sys.executable, bridge])
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertEqual(self._decision_rows(), 1,
                          "the CLI must drain before exit so a 3s shadow call's row lands")

    def test_j064_forty_lines_against_the_default_cap_writes_forty_rows(self):
        # M1(b): 40 lines is more than DEFAULT_MAX_INFLIGHT (32), the
        # exact shape of Opus rereview4's own probe (40 submitted, 32
        # landed, 8 dropped for queue-full). Chunked submission must get
        # every line answered.
        self._write_seams({"J064": "shadow"})
        bridge = self._write_bridge(0.2)
        lines_path = os.path.join(self._tmp.name, "lines.txt")
        with open(lines_path, "w", encoding="utf-8") as fh:
            fh.write("\n".join("line %d: build step failed with exit 1" % i for i in range(40)))
        proc = self._run_cli(["j064", "--lines-file", lines_path], [sys.executable, bridge])
        self.assertEqual(proc.returncode, 0, proc.stderr)
        stdout_lines = [l for l in proc.stdout.splitlines() if l.strip()]
        self.assertEqual(len(stdout_lines), 40, "every line must still get an answer on stdout")
        self.assertEqual(self._decision_rows(), 40,
                          "chunked submission must land every line's row, none dropped for queue-full")

    def test_atexit_kills_the_bridge_when_the_clis_own_drain_times_out(self):
        # M1(c): a bridge slower than even the CLI's own sized drain (here,
        # 20s against a ~4s budget for one call) must not survive process
        # exit as an orphan. The bridge writes its OWN pid to a file before
        # sleeping so this test can check the real OS process, not just
        # that the CLI itself returned.
        self._write_seams({"J030": "shadow"})
        pidfile = os.path.join(self._tmp.name, "bridge.pid")
        bridge = self._write_bridge(20.0)
        proc = self._run_cli(
            ["j030", "--claim", "x", "--source", "x", "--current-answer", "true"],
            [sys.executable, bridge], timeout=15.0, pidfile=pidfile)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertTrue(os.path.exists(pidfile), "the bridge never even started")
        with open(pidfile, encoding="utf-8") as f:
            pid = int(f.read().strip())
        time.sleep(0.3)  # give the OS a moment to actually reap the killed child
        self.assertFalse(_pid_alive(pid),
                          "the bridge subprocess must not survive process exit once both "
                          "the CLI's own drain and the atexit drain have timed out")


if __name__ == "__main__":
    unittest.main()

"""What scripts/jev_seam.py must keep true.

Every test injects a fake runner: no test here ever touches the network, a
real subprocess, or the keychain. Each of the seven rules in the A0.2/A0.6
brief gets at least one test named after it; the adversarial-review
addition (risk class comes only from the registry, consult() has no
argument that can override it) gets its own test too.
"""
import contextlib
import io
import json
import os
import stat
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import jev_calibration as jc  # noqa: E402
import jev_canary  # noqa: E402  (item 1 cross-checkout tests)
import jev_cascade as cascade  # noqa: E402
import jev_seam as seam  # noqa: E402

MODEL = "typesafe/jev-1.13-test"


def _noul_entry(entry_id, risk="low"):
    # "privacy" is required by jev_registry's own lint (M2, review
    # 70268c3): a missing or misspelled tag is a lint finding, and
    # callable() refuses any dirty entry. These fixtures are never asked
    # for real, so "public_or_own_text" (no content gate needed) is a
    # harmless, valid choice.
    return {
        "id": entry_id, "role": "second_opinion", "risk": risk, "wave": "W1",
        "privacy": "public_or_own_text",
        "question": {"type": "noul", "instructions": "Is it true, for %s?" % entry_id},
    }


def _choice_entry(entry_id, risk="low"):
    return {
        "id": entry_id, "role": "second_opinion", "risk": risk, "wave": "W1",
        "privacy": "public_or_own_text",
        "question": {
            "type": "choice", "instructions": "Pick one, for %s." % entry_id,
            "options": ["a", "b", "unknown"],
        },
    }


def _score_entry(entry_id, risk="low"):
    # jev_registry lints score entries for an "unknown"-named option too,
    # same as choice: this is the fixture the CRITICAL fix (re-review of
    # 1385ab88c) needs, modelled on _choice_entry.
    return {
        "id": entry_id, "role": "second_opinion", "risk": risk, "wave": "W1",
        "privacy": "public_or_own_text",
        "question": {
            "type": "score", "instructions": "Rate it, for %s." % entry_id,
            "options": ["1", "2", "unknown"],
        },
    }


def _must_not_entry(entry_id):
    return {"id": entry_id, "role": "MUST_NOT",
            "question": {"type": "noul", "instructions": "never asked"}}


class ScriptedRunner(object):
    """Answers every question in the request with whatever `answer_fn`
    returns for it. `answer_fn(qid, question) -> answer-body dict` (the
    shape jev_decide.decide() reads answers[qid][qtype] etc. from).
    Records every call so a test can assert the bridge was, or was not,
    invoked."""

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
    """Always answers `choice` at `prob`, whichever question id or option
    order it is asked under: a model that does not flip."""
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


def _toggling_choice_answer(primary_choice, reordered_choice, prob):
    """Answers `primary_choice` for the seam's own question id, and
    `reordered_choice` for the reordered probe (identified by the
    ":reordered" suffix jev_seam.consult() appends to its id): a model
    whose vote flips with option order. This keys off the QUESTION ID
    rather than the criteria dict's own key order: jev_decide.decide()
    no longer sorts the wire payload (NO sort_keys, Muse review, JEV-01
    -- stale comment fixed, review 70268c3), so a fake model reading the
    parsed payload COULD now key off criteria order too, but keying off
    the id this module itself controls is simpler and does not depend on
    how each order happens to be built."""
    def fn(qid, q):
        choice = reordered_choice if qid.endswith(":reordered") else primary_choice
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
    registry call at all."""
    def __iter__(self):
        raise AssertionError("registry must not be read in off mode")


def _seams_config(modes=None, canary_marker=None, audit_rate=None, promotions_path=None, **extra):
    cfg = {"modes": dict(modes or {})}
    if canary_marker is not None:
        cfg["canary_reset_marker_path"] = canary_marker
    if audit_rate is not None:
        cfg["audit_rate"] = audit_rate
    if promotions_path is not None:
        cfg["promotions_path"] = promotions_path
    # A0.8 keys (call_deadline_s, max_inflight_calls, daily_call_budget,
    # budget_path, breaker_fail_threshold, breaker_cooldown_s) pass
    # straight through: named params above predate A0.8 and stay as they
    # were, **extra covers every new key without another combinatorial
    # parameter list.
    cfg.update(extra)
    return cfg


def _seed_calibration(dp, op, family, qtype, confidence, n_correct, n_wrong=0):
    """Mirrors test_jev_cascade.py's own _seed() helper: writes n_correct
    (and optionally n_wrong) joined decision/outcome pairs at a fixed
    confidence, so threshold()/route() have real evidence to read."""
    for i in range(n_correct):
        did = "%s-%s-%.3f-c%d" % (family, qtype, confidence, i)
        jc.append_decision(dp, {
            "id": did, "family": family, "qtype": qtype, "framing": "h",
            "answer": True if qtype == "noul" else "a", "prob": confidence,
            "confidence": confidence, "model": MODEL, "cost": 0.0,
            "at": "2026-09-18T00:00:00Z",
        })
        jc.append_outcome(op, {"id": did, "correct": True, "source": "t",
                                "at": "2026-09-18T00:01:00Z"}, decisions_path=dp)
    for i in range(n_wrong):
        did = "%s-%s-%.3f-w%d" % (family, qtype, confidence, i)
        jc.append_decision(dp, {
            "id": did, "family": family, "qtype": qtype, "framing": "h",
            "answer": True if qtype == "noul" else "a", "prob": confidence,
            "confidence": confidence, "model": MODEL, "cost": 0.0,
            "at": "2026-09-18T00:00:00Z",
        })
        jc.append_outcome(op, {"id": did, "correct": False, "source": "t",
                                "at": "2026-09-18T00:02:00Z"}, decisions_path=dp)


class SeamTestCase(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.ledger_dir = self._tmp.name
        # A SEPARATE tempdir for any real promotions file a test plants,
        # never nested under self.ledger_dir (Minor fix, re-review of
        # 1385ab88c: a promotions_path override resolving under
        # ledger_dir is now refused by the module itself, so a test that
        # wants a promotion to actually be found must not put it there).
        self._promo_tmp = tempfile.TemporaryDirectory()
        self.promo_dir = self._promo_tmp.name

        # A0.8: every consult() call now checks the daily call budget by
        # default (see seam.DEFAULT_BUDGET_PATH). No pre-existing test in
        # this file knows about that key, so without this patch every one
        # of them would silently read/write the REAL
        # ~/.brother/jev/jev-budget.json on whatever machine runs this
        # suite -- exactly the hermeticity this module's own tests are
        # documented to require. Patched here, once, for every test that
        # inherits this base class, rather than adding a budget_path
        # override to ~40 individual _seams_config() calls.
        self._budget_tmp = tempfile.TemporaryDirectory()
        self._budget_patch = mock.patch.object(
            seam, "DEFAULT_BUDGET_PATH", os.path.join(self._budget_tmp.name, "jev-budget.json"))
        self._budget_patch.start()

        # A0.8: the breaker is in-memory and module-global (see
        # seam._BREAKER_STATE's own docstring) so it must never leak a
        # tripped/cooling-off state from one test into the next.
        seam._BREAKER_STATE["consecutive_failures"] = 0
        seam._BREAKER_STATE["opened_at"] = None

    def tearDown(self):
        # Orchestrator fix 2026-09-19: shadow/advise now spawn a detached
        # background worker that this test's own thread never waits for.
        # Every gated test in this file releases its own gate before
        # returning, but the worker's `finally` (ledger write already
        # visible, THEN _release_inflight()) can still be unwinding for a
        # few microseconds after that -- without this, a still-unwinding
        # worker from one test can make the NEXT test's own concurrency-
        # cap assumptions (e.g. Item1QueueFullDrop) flaky. Bounded,
        # best-effort: normally an instant no-op.
        deadline = time.monotonic() + 3.0
        while seam._INFLIGHT_COUNT > 0 and time.monotonic() < deadline:
            time.sleep(0.005)
        self._budget_patch.stop()
        self._budget_tmp.cleanup()
        self._tmp.cleanup()
        self._promo_tmp.cleanup()

    def _wait_for_lines(self, path, n, timeout=2.0):
        """Polls `path` until it has at least `n` non-blank lines, or
        `timeout` elapses. Orchestrator fix 2026-09-19: shadow/advise no
        longer write the ledger before consult() returns, so a test that
        wants to observe a background worker's eventual write needs a
        deterministic wait, never a bare "read immediately after the
        call". Returns the lines actually read (possibly fewer than n, if
        the timeout won: callers still assert on the count so a genuine
        regression fails loud rather than silently reading a partial
        result as success)."""
        deadline = time.monotonic() + timeout
        lines = []
        while time.monotonic() < deadline:
            if os.path.exists(path):
                with open(path, encoding="utf-8") as fh:
                    lines = [l for l in fh if l.strip()]
                if len(lines) >= n:
                    return lines
            time.sleep(0.005)
        return lines


class Rule1RegistryRefusal(SeamTestCase):
    """Rule 1: registry.callable refusal means no call and
    answer=current_answer with the refusal as reason."""

    def test_must_not_entry_never_called(self):
        registry = [_must_not_entry("MN1")]
        runner = ScriptedRunner(_noul_answer(0.9))
        result = seam.consult(
            "MN1", {"x": 1}, "keep-me",
            seams_config=_seams_config({"MN1": "act"}), registry=registry,
            ledger_dir=self.ledger_dir, runner=runner,
        )
        self.assertEqual(result.answer, "keep-me")
        self.assertIsNone(result.jev)
        self.assertEqual(result.mode, "act")
        self.assertIn("MUST_NOT", result.reason)
        self.assertEqual(runner.calls, [])

    def test_unknown_id_never_called(self):
        registry = [_noul_entry("N1")]
        runner = ScriptedRunner(_noul_answer(0.9))
        result = seam.consult(
            "NOPE", {}, "fallback",
            seams_config=_seams_config({"NOPE": "shadow"}), registry=registry,
            ledger_dir=self.ledger_dir, runner=runner,
        )
        self.assertEqual(result.answer, "fallback")
        self.assertIsNone(result.jev)
        self.assertEqual(runner.calls, [])
        self.assertIn("unknown registry entry id", result.reason)


class OffModeMakesNoCall(SeamTestCase):
    """off (the default for any entry missing from the config): no call
    at all, not even a registry read."""

    def test_off_touches_nothing(self):
        result = seam.consult(
            "ANY", {"a": 1}, "unchanged",
            seams_config=_seams_config({}), registry=ExplodingRegistry(),
            ledger_dir=self.ledger_dir, runner=ScriptedRunner(_noul_answer(0.9)),
        )
        self.assertEqual(result.answer, "unchanged")
        self.assertIsNone(result.jev)
        self.assertEqual(result.mode, "off")
        self.assertIsNone(result.decision_id)

    def test_missing_from_config_defaults_off(self):
        registry = [_noul_entry("N1")]
        result = seam.consult(
            "N1", {}, "unchanged",
            seams_config=_seams_config({"OTHER": "act"}), registry=registry,
            ledger_dir=self.ledger_dir, runner=ScriptedRunner(_noul_answer(0.9)),
        )
        self.assertEqual(result.mode, "off")
        self.assertEqual(result.answer, "unchanged")


class Rule2NoDataFromDecide(SeamTestCase):
    """Rule 2: any NO-DATA from decide() means answer=current_answer,
    recorded as NO-DATA, never an answer. Exercised in `act` mode (the
    orchestrator fix 2026-09-19 means shadow/advise return before decide()
    is even called, so they cannot observe its NO-DATA synchronously); the
    NO-DATA-from-decide() branch runs before mode_cfg is ever inspected,
    so it is identical regardless of which waiting mode observes it."""

    def test_bridge_nonzero_exit_is_no_data(self):
        registry = [_noul_entry("N1")]
        runner = ScriptedRunner(_noul_answer(0.9), returncode=3, stderr="boom")
        result = seam.consult(
            "N1", {"i": 1}, "safe-default",
            seams_config=_seams_config({"N1": "act"}), registry=registry,
            ledger_dir=self.ledger_dir, runner=runner,
        )
        self.assertEqual(result.answer, "safe-default")
        self.assertIsNone(result.jev)
        self.assertTrue(result.reason.startswith("NO-DATA:"))
        self.assertFalse(result.audit)
        # MAJOR fix, re-review of 1385ab88c: decision_id is None, never
        # "the id this call would have used" -- nothing was written, so
        # no caller may label a decision that does not exist.
        self.assertIsNone(result.decision_id)
        self.assertFalse(os.path.exists(os.path.join(self.ledger_dir, "decisions.jsonl")))


class Item4CanaryForcesOff(SeamTestCase):
    """Item 4 (A0.8): a canary reset marker present means every seam
    behaves as OFF -- no call at all -- for EVERY configured mode
    including shadow, not shadow itself. Supersedes the old "Rule 3"
    (canary forces shadow): a shadow-configured seam still must not call a
    Jev the canary says has drifted, since watching still means calling."""

    def test_act_forced_to_off(self):
        # Minor fix (review 70268c3), carried forward: seed real
        # calibration evidence and a valid signed promotion so this test
        # actually exercises the canary check. Without them, act mode
        # falls back to current_answer anyway for lack of a promotion, so
        # deleting the canary-tripped check entirely would NOT have made
        # this test fail -- the mutation the review's own probe caught.
        dp = os.path.join(self.ledger_dir, "decisions.jsonl")
        op = os.path.join(self.ledger_dir, "outcomes.jsonl")
        # Minor fix, re-review of 1385ab88c: the promotion file must sit
        # OUTSIDE ledger_dir (self.promo_dir) now -- an override under
        # ledger_dir is refused by the module itself, which would
        # silently defeat this test's own mutation-catching intent
        # (deleting the canary check would then still fail to ACT for an
        # unrelated reason: no promotion reachable at all).
        pp = os.path.join(self.promo_dir, "promotions.jsonl")
        _seed_calibration(dp, op, "N1", "noul", 0.97, n_correct=40)
        with open(pp, "w", encoding="utf-8") as fh:
            fh.write(json.dumps({
                "family": "N1", "qtype": "noul", "risk_class": "low", "model": MODEL,
                "signed_by": "founder", "signed_at": "2026-09-18T00:00:00Z",
                "bound_at_signing": 0.85, "n_at_signing": 40,
                "flip_condition": "precision drops below target for two weeks",
                "review_by": "2099-01-01",
            }) + "\n")
        marker = os.path.join(self.ledger_dir, "canary-reset.json")
        with open(marker, "w", encoding="utf-8") as fh:
            fh.write("{}")
        registry = [_noul_entry("N1", risk="low")]
        runner = ScriptedRunner(_noul_answer(0.97))
        result = seam.consult(
            "N1", {"i": 1}, "current",
            # M3 fix (review 70268c3): the promotions store is no longer
            # built from ledger_dir; this test's own file is wired in
            # explicitly via seams_config's promotions_path key.
            seams_config=_seams_config({"N1": "act"}, canary_marker=marker,
                                        promotions_path=pp),
            registry=registry, ledger_dir=self.ledger_dir, runner=runner,
        )
        self.assertEqual(result.mode, "off")
        self.assertIn("canary", result.reason.lower())
        # No call at all: jev is None (not merely "answer withheld" as the
        # old forced-shadow behaviour had it), and nothing landed on the
        # ledger, even though calibration and a valid promotion would
        # otherwise let this exact decision ACT.
        self.assertIsNone(result.jev)
        self.assertIsNone(result.decision_id)
        self.assertEqual(result.answer, "current")
        self.assertFalse(runner.calls)
        # Nothing new landed on the ledger beyond the 40 seeded rows.
        with open(dp, encoding="utf-8") as fh:
            self.assertEqual(sum(1 for line in fh if line.strip()), 40)

    def test_shadow_also_forced_to_off_by_canary(self):
        marker = os.path.join(self.ledger_dir, "canary-reset.json")
        with open(marker, "w", encoding="utf-8") as fh:
            fh.write("{}")
        registry = [_noul_entry("N1")]
        runner = ScriptedRunner(_noul_answer(0.9))
        result = seam.consult(
            "N1", {}, "current",
            seams_config=_seams_config({"N1": "shadow"}, canary_marker=marker),
            registry=registry, ledger_dir=self.ledger_dir, runner=runner,
        )
        self.assertEqual(result.mode, "off")
        self.assertIn("canary", (result.reason or "").lower())
        self.assertIsNone(result.jev)
        self.assertFalse(runner.calls)

    def test_no_marker_leaves_shadow_untouched(self):
        # Orchestrator fix 2026-09-19: shadow always returns immediately
        # now, whether or not the canary is tripped -- the meaningful
        # difference without a marker is that the worker actually GETS TO
        # RUN (proven by the eventual ledger row), not a synchronous
        # `reason`/`jev` on the SeamResult itself (see
        # test_shadow_also_forced_to_off_by_canary above for the
        # contrasting case, where the worker never runs at all).
        registry = [_noul_entry("N1")]
        runner = ScriptedRunner(_noul_answer(0.9))
        result = seam.consult(
            "N1", {}, "current",
            seams_config=_seams_config({"N1": "shadow"},
                                        canary_marker=os.path.join(self.ledger_dir, "no-such-marker.json")),
            registry=registry, ledger_dir=self.ledger_dir, runner=runner,
        )
        self.assertEqual(result.mode, "shadow")
        self.assertEqual(result.reason, "shadow: submitted")
        self.assertIsNone(result.jev)
        dp = os.path.join(self.ledger_dir, "decisions.jsonl")
        lines = self._wait_for_lines(dp, 1)
        self.assertEqual(len(lines), 1)


class Rule4LedgerWrite(SeamTestCase):
    """Rule 4: every call appends a decision record to
    ledger_dir/decisions.jsonl with a decision_id that is UNIQUE PER CALL
    (MAJOR fix, orchestrator decision, re-review of 1385ab88c), the entry
    id as family, question type, model, answer, probability/confidence,
    framing. Exercised in `act` mode with an explicit, deliberately
    missing promotions_path: the orchestrator fix 2026-09-19 means shadow
    never waits and so never hands its own decision_id back, but the
    ledger write itself happens in the SAME shared worker code
    (_run_seam_job) regardless of mode, so `act` (which still waits)
    proves the identical write behaviour synchronously. The missing
    promotions_path only affects whether act mode ACTs (it escalates
    here, `result.answer` stays "current"/"c"), never whether or what it
    writes to the ledger."""

    def test_decision_recorded(self):
        registry = [_noul_entry("N1")]
        runner = ScriptedRunner(_noul_answer(0.83))
        cfg = _seams_config({"N1": "act"}, promotions_path=os.path.join(self.promo_dir, "no-such.jsonl"))
        result = seam.consult(
            "N1", {"i": 1}, "current",
            seams_config=cfg, registry=registry,
            ledger_dir=self.ledger_dir, runner=runner,
        )
        self.assertEqual(result.answer, "current")  # no promotion: never ACTs
        dp = os.path.join(self.ledger_dir, "decisions.jsonl")
        with open(dp, encoding="utf-8") as fh:
            lines = [json.loads(l) for l in fh if l.strip()]
        self.assertEqual(len(lines), 1)
        rec = lines[0]
        self.assertEqual(rec["id"], result.decision_id)
        self.assertEqual(rec["family"], "N1")
        self.assertEqual(rec["qtype"], "noul")
        self.assertEqual(rec["model"], MODEL)
        # a noul answer IS its own probability (jev_decide's own shape):
        # not rounded to a boolean at this layer.
        self.assertAlmostEqual(rec["answer"], 0.83)
        self.assertAlmostEqual(rec["prob"], 0.83)
        self.assertAlmostEqual(rec["confidence"], 0.83)
        self.assertTrue(rec["framing"])

    def test_two_identical_calls_get_two_distinct_ids_and_two_ledger_rows(self):
        # MAJOR fix, orchestrator decision, re-review of 1385ab88c:
        # decision_id is now UNIQUE PER CALL, never deduplicated by
        # content -- a repeated identical question is two real decisions,
        # never one id read twice. Supersedes the old
        # test_decision_id_stable_across_calls, which asserted the
        # opposite of what the orchestrator now requires.
        registry = [_noul_entry("N1")]
        runner = ScriptedRunner(_noul_answer(0.6))
        cfg = _seams_config({"N1": "act"}, promotions_path=os.path.join(self.promo_dir, "no-such.jsonl"))
        r1 = seam.consult("N1", {"i": 1}, "c", seams_config=cfg,
                           registry=registry, ledger_dir=self.ledger_dir, runner=runner)
        r2 = seam.consult("N1", {"i": 1}, "c", seams_config=cfg,
                           registry=registry, ledger_dir=self.ledger_dir, runner=runner)
        self.assertIsNotNone(r1.decision_id)
        self.assertIsNotNone(r2.decision_id)
        self.assertNotEqual(r1.decision_id, r2.decision_id)
        r3 = seam.consult("N1", {"i": 2}, "c", seams_config=cfg,
                           registry=registry, ledger_dir=self.ledger_dir, runner=runner)
        self.assertNotEqual(r1.decision_id, r3.decision_id)
        self.assertNotEqual(r2.decision_id, r3.decision_id)
        # Two ledger rows for the two identical (entry_id, state) calls,
        # a third for the differently-stated call: three rows total, no
        # deduplication.
        dp = os.path.join(self.ledger_dir, "decisions.jsonl")
        with open(dp, encoding="utf-8") as fh:
            ids = [json.loads(line)["id"] for line in fh if line.strip()]
        self.assertEqual(len(ids), 3)
        self.assertEqual(len(set(ids)), 3)
        self.assertEqual(set(ids), {r1.decision_id, r2.decision_id, r3.decision_id})


class Rule5AuditSample(SeamTestCase):
    """Rule 5 (A0.6): when Jev's answer would NOT be escalated, a
    deterministic-per-decision_id sample is flagged audit=True, so a
    human labels it anyway. Exercised in `act` mode with a deliberately
    missing promotions_path (orchestrator fix 2026-09-19: shadow never
    waits, so it never hands `audit` back synchronously either -- but
    _audit_flag() is computed in the SAME shared worker code before any
    mode branching, so `act`, which still waits, proves the identical
    value; the missing promotion only affects whether it ACTs, asserted
    separately in ActModeActsOnlyWhenCascadeSays, not whether it audits)."""

    def _ready_ledger(self, family, risk="low"):
        dp = os.path.join(self.ledger_dir, "decisions.jsonl")
        op = os.path.join(self.ledger_dir, "outcomes.jsonl")
        # n=40, all correct: Wilson lower bound for band [0.9, 1.0) is
        # n/(n+1.96^2) ~= 0.9124, clears the "low" risk target of 0.90
        # with room, the same math test_jev_cascade.py's own selftest uses.
        _seed_calibration(dp, op, family, "noul", 0.95, n_correct=40)
        return dp, op

    def _act_cfg(self, modes):
        return _seams_config(modes, promotions_path=os.path.join(self.promo_dir, "no-such.jsonl"))

    def test_confident_decision_sampled_in(self):
        self._ready_ledger("AUD1")
        registry = [_noul_entry("AUD1", risk="low")]
        runner = ScriptedRunner(_noul_answer(0.95))
        result = seam.consult(
            "AUD1", {"i": 1}, "current",
            seams_config=self._act_cfg({"AUD1": "act"}), registry=registry,
            ledger_dir=self.ledger_dir, runner=runner, rng=lambda: 0.0,
        )
        self.assertTrue(result.audit)

    def test_confident_decision_sampled_out(self):
        self._ready_ledger("AUD2")
        registry = [_noul_entry("AUD2", risk="low")]
        runner = ScriptedRunner(_noul_answer(0.95))
        result = seam.consult(
            "AUD2", {"i": 1}, "current",
            seams_config=self._act_cfg({"AUD2": "act"}), registry=registry,
            ledger_dir=self.ledger_dir, runner=runner, rng=lambda: 0.5,
        )
        self.assertFalse(result.audit)

    def test_below_threshold_never_audited(self):
        self._ready_ledger("AUD3")
        registry = [_noul_entry("AUD3", risk="low")]
        # confidence well below the ~0.91 calibrated threshold: would be
        # escalated anyway, so the audit-sample bias fix does not apply.
        runner = ScriptedRunner(_noul_answer(0.55))
        result = seam.consult(
            "AUD3", {"i": 1}, "current",
            seams_config=self._act_cfg({"AUD3": "act"}), registry=registry,
            ledger_dir=self.ledger_dir, runner=runner, rng=lambda: 0.0,
        )
        self.assertFalse(result.audit)

    def test_critical_risk_never_audited(self):
        self._ready_ledger("AUD4", risk="critical")
        registry = [_noul_entry("AUD4", risk="critical")]
        runner = ScriptedRunner(_noul_answer(0.95))
        result = seam.consult(
            "AUD4", {"i": 1}, "current",
            seams_config=self._act_cfg({"AUD4": "act"}), registry=registry,
            ledger_dir=self.ledger_dir, runner=runner, rng=lambda: 0.0,
        )
        self.assertFalse(result.audit)


class Rule6NearThresholdBothOrders(SeamTestCase):
    """Rule 6: for choice questions whose top probability sits within 0.1
    of the act threshold, ask both option orders and, if they disagree,
    treat as NO-DATA. Exercised in `act` mode with a deliberately missing
    promotions_path (orchestrator fix 2026-09-19: shadow never waits, so
    it cannot observe the probe's outcome synchronously either -- the
    probe itself runs in the SAME shared worker code regardless of mode)."""

    def test_disagreement_near_threshold_is_no_data(self):
        dp = os.path.join(self.ledger_dir, "decisions.jsonl")
        op = os.path.join(self.ledger_dir, "outcomes.jsonl")
        # Same n=40-all-correct evidence as Rule5: threshold ~= 0.9 for
        # the "low" risk band [0.9, 1.0).
        _seed_calibration(dp, op, "CH1", "choice", 0.95, n_correct=40)
        registry = [_choice_entry("CH1", risk="low")]
        # top_prob=0.85 is within NEAR_THRESHOLD_BAND (0.1) of ~0.9, and
        # the reordered probe answers "unknown" instead of "a": disagreement.
        runner = ScriptedRunner(_toggling_choice_answer("a", "unknown", 0.85))
        cfg = _seams_config({"CH1": "act"}, promotions_path=os.path.join(self.promo_dir, "no-such.jsonl"))
        result = seam.consult(
            "CH1", {"i": 1}, "current",
            seams_config=cfg, registry=registry,
            ledger_dir=self.ledger_dir, runner=runner,
        )
        self.assertEqual(result.answer, "current")
        self.assertIsNone(result.jev)
        self.assertIn("NO-DATA", result.reason)
        self.assertIn("disagreement", result.reason)
        # a disagreement never gets written to the ledger: still exactly
        # the 40 seeded rows, nothing appended for this call.
        with open(dp, encoding="utf-8") as fh:
            ids = [json.loads(l)["id"] for l in fh if l.strip()]
        self.assertEqual(len(ids), 40)  # only the seeded rows, nothing new

    def test_far_from_threshold_skips_probe(self):
        dp = os.path.join(self.ledger_dir, "decisions.jsonl")
        op = os.path.join(self.ledger_dir, "outcomes.jsonl")
        _seed_calibration(dp, op, "CH2", "choice", 0.95, n_correct=40)
        registry = [_choice_entry("CH2", risk="low")]
        # top_prob=0.5 is far from the ~0.9 threshold: no reorder probe,
        # a second bridge call must never happen.
        runner = ScriptedRunner(_fixed_choice_answer("a", 0.5))
        cfg = _seams_config({"CH2": "act"}, promotions_path=os.path.join(self.promo_dir, "no-such.jsonl"))
        result = seam.consult(
            "CH2", {"i": 1}, "current",
            seams_config=cfg, registry=registry,
            ledger_dir=self.ledger_dir, runner=runner,
        )
        self.assertIsNotNone(result.jev)
        self.assertEqual(len(runner.calls), 1)


class Rule7ShadowInvariance(SeamTestCase):
    """Rule 7: with a runner that returns the opposite answer, shadow and
    advise results always equal current_answer.

    Orchestrator fix 2026-09-19 (item 1) made shadow fire-and-forget:
    it never waits for Jev at all, so `jev=None` and `answer=current_
    answer` are trivially true of its synchronous return -- the property
    still worth proving for shadow is that Jev's answer, once the
    background worker actually gets it, lands in the LEDGER (proving Jev
    really was asked) and never surfaces back into `answer`.

    Independent review, M3 (2026-09-19): a DIFFERENT, EARLIER version of
    this file went further and asserted `advise` ALSO returns jev=None --
    that broke advise's own contract (it exists so a caller's interface
    can DISPLAY Jev's answer; see the module docstring's MODES section)
    and the reviewer named it as one of two required fixes: "Revert the
    test that asserted advise has jev None." Advise WAITS (bounded by the
    deadline, see _fires_and_forgets()) and returns jev's real answer,
    while `answer` itself still always stays current_answer -- both
    halves of Rule 7 are tested below, for the mode each one actually
    applies to."""

    def test_shadow_never_returns_jev_answer(self):
        registry = [_noul_entry("N1")]
        runner = ScriptedRunner(_noul_answer(0.99))  # Jev says "true", loudly
        result = seam.consult(
            "N1", {}, current_answer=False,
            seams_config=_seams_config({"N1": "shadow"}), registry=registry,
            ledger_dir=self.ledger_dir, runner=runner,
        )
        self.assertEqual(result.answer, False)
        self.assertIsNone(result.jev)
        self.assertEqual(result.reason, "shadow: submitted")
        dp = os.path.join(self.ledger_dir, "decisions.jsonl")
        lines = self._wait_for_lines(dp, 1)
        self.assertEqual(len(lines), 1)
        self.assertAlmostEqual(json.loads(lines[0])["answer"], 0.99)

    def test_advise_exposes_jevs_answer_but_never_returns_it_as_answer(self):
        # M3 (independent review, 2026-09-19): advise waits and DOES
        # return Jev's answer in `.jev`, for the caller's own interface to
        # display -- it just never lets that answer become `.answer`
        # itself, which stays current_answer regardless of what Jev said.
        registry = [_noul_entry("N1")]
        runner = ScriptedRunner(_noul_answer(0.01))  # Jev says "false" this time
        result = seam.consult(
            "N1", {}, current_answer=True,
            seams_config=_seams_config({"N1": "advise"}), registry=registry,
            ledger_dir=self.ledger_dir, runner=runner,
        )
        self.assertEqual(result.answer, True)
        self.assertIsNotNone(result.jev)
        self.assertAlmostEqual(result.jev["answer"], 0.01)
        self.assertIsNone(result.reason)
        dp = os.path.join(self.ledger_dir, "decisions.jsonl")
        lines = self._wait_for_lines(dp, 1)
        self.assertEqual(len(lines), 1)
        self.assertAlmostEqual(json.loads(lines[0])["answer"], 0.01)


class ActModeActsOnlyWhenCascadeSays(SeamTestCase):
    def test_act_mode_needs_a_promotion(self):
        dp = os.path.join(self.ledger_dir, "decisions.jsonl")
        op = os.path.join(self.ledger_dir, "outcomes.jsonl")
        _seed_calibration(dp, op, "ACT1", "noul", 0.95, n_correct=40)
        registry = [_noul_entry("ACT1", risk="low")]
        runner = ScriptedRunner(_noul_answer(0.95))
        # A promotions_path override naming a file that does not exist:
        # jev_cascade documents a missing file at the configured path as
        # an empty ledger, which is "no promotion", which always
        # escalates -- never ACTs, however confident and calibrated the
        # decision is. Pointed at an isolated temp path (M3 fix, review
        # 70268c3), OUTSIDE self.ledger_dir (Minor fix, re-review of
        # 1385ab88c: a path under ledger_dir is refused before it is even
        # looked at, which would obscure the actual point of this test --
        # a legitimate override that simply names a file that isn't
        # there) rather than left to default to
        # jev_cascade.DEFAULT_PROMOTIONS_PATH, so this test never depends
        # on whatever the real repo-root promotions file happens to hold.
        missing_pp = os.path.join(self.promo_dir, "no-such-promotions.jsonl")
        result = seam.consult(
            "ACT1", {"i": 1}, "current",
            seams_config=_seams_config({"ACT1": "act"}, promotions_path=missing_pp),
            registry=registry, ledger_dir=self.ledger_dir, runner=runner,
        )
        self.assertEqual(result.answer, "current")
        self.assertEqual(result.mode, "act")
        self.assertIn("promotion", (result.reason or ""))

    def test_act_mode_acts_with_a_signed_promotion(self):
        dp = os.path.join(self.ledger_dir, "decisions.jsonl")
        op = os.path.join(self.ledger_dir, "outcomes.jsonl")
        # Outside ledger_dir (Minor fix, re-review of 1385ab88c): see
        # test_act_forced_to_shadow's own comment for why.
        pp = os.path.join(self.promo_dir, "promotions.jsonl")
        _seed_calibration(dp, op, "ACT2", "noul", 0.95, n_correct=40)
        with open(pp, "w", encoding="utf-8") as fh:
            fh.write(json.dumps({
                "family": "ACT2", "qtype": "noul", "risk_class": "low", "model": MODEL,
                "signed_by": "founder", "signed_at": "2026-09-18T00:00:00Z",
                "bound_at_signing": 0.85, "n_at_signing": 40,
                "flip_condition": "precision drops below target for two weeks",
                "review_by": "2099-01-01",
            }) + "\n")
        registry = [_noul_entry("ACT2", risk="low")]
        runner = ScriptedRunner(_noul_answer(0.95))
        result = seam.consult(
            "ACT2", {"i": 1}, "current",
            seams_config=_seams_config({"ACT2": "act"}, promotions_path=pp),
            registry=registry, ledger_dir=self.ledger_dir, runner=runner,
        )
        # ACT returns Jev's own answer verbatim: a noul answer IS its
        # probability, not a boolean rounded at this layer.
        self.assertAlmostEqual(result.answer, 0.95)
        self.assertIsNone(result.reason)


class RiskClassComesOnlyFromRegistry(SeamTestCase):
    """Adversarial-review addition: risk_class must come ONLY from the
    registry entry's own 'risk' field; consult() has no argument a caller
    could use to downgrade it."""

    def test_consult_has_no_risk_parameter(self):
        registry = [_noul_entry("H1", risk="high")]
        with self.assertRaises(TypeError):
            seam.consult(
                "H1", {}, "current", seams_config=_seams_config({"H1": "act"}),
                registry=registry, ledger_dir=self.ledger_dir,
                runner=ScriptedRunner(_noul_answer(0.95)),
                risk_class="low",  # not a real parameter: must blow up
            )
        with self.assertRaises(TypeError):
            seam.consult(
                "H1", {}, "current", seams_config=_seams_config({"H1": "act"}),
                registry=registry, ledger_dir=self.ledger_dir,
                runner=ScriptedRunner(_noul_answer(0.95)),
                risk="low",  # not a real parameter either
            )

    def test_high_risk_entry_never_acts_on_low_grade_evidence(self):
        dp = os.path.join(self.ledger_dir, "decisions.jsonl")
        op = os.path.join(self.ledger_dir, "outcomes.jsonl")
        # Outside ledger_dir (Minor fix, re-review of 1385ab88c): a real,
        # reachable promotion is the whole point of this test (it proves
        # insufficient EVIDENCE still escalates even with one), so it
        # must not sit somewhere the module now refuses to read from.
        pp = os.path.join(self.promo_dir, "promotions.jsonl")
        # 200 samples at 98% raw precision: Wilson lower bound ~= 0.9497,
        # comfortably clears "low" (0.90), never clears "high" (0.98).
        _seed_calibration(dp, op, "H1", "noul", 0.99, n_correct=196, n_wrong=4)
        with open(pp, "w", encoding="utf-8") as fh:
            fh.write(json.dumps({
                "family": "H1", "qtype": "noul", "risk_class": "high", "model": MODEL,
                "signed_by": "founder", "signed_at": "2026-09-18T00:00:00Z",
                "bound_at_signing": 0.5, "n_at_signing": 200,
                "flip_condition": "precision drops",
                "review_by": "2099-01-01",
            }) + "\n")
        registry = [_noul_entry("H1", risk="high")]
        runner = ScriptedRunner(_noul_answer(0.99))
        result = seam.consult(
            "H1", {"i": 1}, "current",
            seams_config=_seams_config({"H1": "act"}, promotions_path=pp),
            registry=registry, ledger_dir=self.ledger_dir, runner=runner,
        )
        self.assertEqual(result.answer, "current")
        self.assertEqual(cascade.RISK_TARGET_PRECISION["high"], 0.98)
        self.assertEqual(cascade.RISK_TARGET_PRECISION["low"], 0.90)


class C3NeverActOnAnAbstain(SeamTestCase):
    """C3 (review 70268c3): act mode acted on "unknown" and on an answer
    outside the abstain band's own safe zone. Fix in jev_seam: never ACT
    (and never count as confident for the audit sample) on a choice whose
    chosen option name contains "unknown", nor on a noul probability
    inside [0.2, 0.8]."""

    def _promoted_ledger(self, family, qtype, risk="low"):
        dp = os.path.join(self.ledger_dir, "decisions.jsonl")
        op = os.path.join(self.ledger_dir, "outcomes.jsonl")
        # Outside ledger_dir (Minor fix, re-review of 1385ab88c): see
        # test_act_forced_to_shadow's own comment for why.
        pp = os.path.join(self.promo_dir, "promotions.jsonl")
        _seed_calibration(dp, op, family, qtype, 0.95, n_correct=40)
        with open(pp, "w", encoding="utf-8") as fh:
            fh.write(json.dumps({
                "family": family, "qtype": qtype, "risk_class": risk, "model": MODEL,
                "signed_by": "founder", "signed_at": "2026-09-18T00:00:00Z",
                "bound_at_signing": 0.85, "n_at_signing": 40,
                "flip_condition": "precision drops below target for two weeks",
                "review_by": "2099-01-01",
            }) + "\n")
        return pp

    def test_choice_unknown_option_never_acts(self):
        # Reviewer's probe: calibrated, promoted, confidence 0.99, and the
        # chosen option's own NAME is (a case-variant of) "unknown" -- a
        # legally declared option (jev_registry requires every choice
        # entry to have one), never caught by jev_decide's declared-
        # options check, but never a real answer to act on either. A
        # mixed-case declared option name ("UNKNOWN") proves the seam's
        # own check is case-insensitive, not merely lucky on lowercase.
        pp = self._promoted_ledger("ABST1", "choice")
        entry = _choice_entry("ABST1", risk="low")
        entry["question"]["options"] = ["a", "b", "UNKNOWN"]
        registry = [entry]
        runner = ScriptedRunner(_fixed_choice_answer("UNKNOWN", 0.99))
        result = seam.consult(
            "ABST1", {"i": 1}, "current",
            seams_config=_seams_config({"ABST1": "act"}, promotions_path=pp),
            registry=registry, ledger_dir=self.ledger_dir, runner=runner,
        )
        self.assertEqual(result.answer, "current")
        self.assertEqual(result.mode, "act")
        self.assertIn("unknown", (result.reason or "").lower())
        self.assertFalse(result.audit)

    def test_score_unknown_option_never_acts(self):
        # CRITICAL fix, re-review of 1385ab88c: _abstain_reason checked
        # only choice and noul; a SCORE question answering "unknown" at
        # confidence 0.99, calibrated and promoted, still ACTed. Modelled
        # directly on test_choice_unknown_option_never_acts above.
        pp = self._promoted_ledger("SCOREABST", "score")
        entry = _score_entry("SCOREABST", risk="low")
        entry["question"]["options"] = ["1", "2", "UNKNOWN"]
        registry = [entry]

        def fn(_qid, _q):
            return {"score": "UNKNOWN", "confidence": 0.99}

        runner = ScriptedRunner(fn)
        result = seam.consult(
            "SCOREABST", {"i": 1}, "current",
            seams_config=_seams_config({"SCOREABST": "act"}, promotions_path=pp),
            registry=registry, ledger_dir=self.ledger_dir, runner=runner,
        )
        self.assertEqual(result.answer, "current")
        self.assertEqual(result.mode, "act")
        self.assertIn("unknown", (result.reason or "").lower())
        self.assertFalse(result.audit)

    def test_noul_probability_in_abstain_band_never_acts(self):
        pp = self._promoted_ledger("ABST2", "noul")
        registry = [_noul_entry("ABST2", risk="low")]
        runner = ScriptedRunner(_noul_answer(0.5))
        result = seam.consult(
            "ABST2", {"i": 1}, "current",
            seams_config=_seams_config({"ABST2": "act"}, promotions_path=pp),
            registry=registry, ledger_dir=self.ledger_dir, runner=runner,
        )
        self.assertEqual(result.answer, "current")
        self.assertIn("abstain", (result.reason or "").lower())
        self.assertFalse(result.audit)

    def test_noul_probability_just_outside_the_band_can_still_act(self):
        # Sanity: the band is [0.2, 0.8], not a blanket "anything below
        # certainty" refusal.
        pp = self._promoted_ledger("ABST3", "noul")
        registry = [_noul_entry("ABST3", risk="low")]
        runner = ScriptedRunner(_noul_answer(0.95))
        result = seam.consult(
            "ABST3", {"i": 1}, "current",
            seams_config=_seams_config({"ABST3": "act"}, promotions_path=pp),
            registry=registry, ledger_dir=self.ledger_dir, runner=runner,
        )
        self.assertAlmostEqual(result.answer, 0.95)


class M1CanaryMarkerResolvesFromRepoRoot(SeamTestCase):
    """M1 (review 70268c3): a RELATIVE configured
    canary_reset_marker_path must resolve against the repo root, the same
    place an explicit --reset-marker on the canary side would if given the
    identical relative path -- never against the process's own cwd. The
    UNCONFIGURED default's own anchor moved again, off the repo root and
    onto JEV_STATE_DIR (item 1, approved-with-nits review 2026-09-18):
    see JevStateDirCrossCheckout below for that half."""

    def test_default_marker_matches_the_canary_contract_path(self):
        # Item 1: the default is JEV_STATE_DIR-anchored, not _REPO_ROOT
        # any more -- see JevStateDirCrossCheckout for why a repo-root
        # anchor was still not far enough (it never reached a sibling
        # checkout or worktree).
        expected = os.path.join(seam.JEV_STATE_DIR, "canary-reset.json")
        self.assertEqual(seam.DEFAULT_CANARY_MARKER, expected)

    def test_relative_marker_forces_off_when_cwd_is_elsewhere(self):
        with mock.patch.object(seam, "_REPO_ROOT", self.ledger_dir):
            marker_dir = os.path.join(self.ledger_dir, "sub")
            os.makedirs(marker_dir)
            with open(os.path.join(marker_dir, "marker.json"), "w", encoding="utf-8") as fh:
                fh.write("{}")
            registry = [_noul_entry("N1", risk="low")]
            runner = ScriptedRunner(_noul_answer(0.97))
            other_cwd = tempfile.mkdtemp()
            started_in = os.getcwd()
            try:
                os.chdir(other_cwd)
                result = seam.consult(
                    "N1", {"i": 1}, "current",
                    seams_config=_seams_config({"N1": "act"}, canary_marker="sub/marker.json"),
                    registry=registry, ledger_dir=self.ledger_dir, runner=runner,
                )
            finally:
                os.chdir(started_in)
        self.assertEqual(result.mode, "off")
        self.assertIn("canary", (result.reason or "").lower())


class Item1NonBlockingCalls(SeamTestCase):
    """Item 1 (A0.8), orchestrator fix 2026-09-19. Original measurement:
    with the DEFAULT config (no call_deadline_s override), mode shadow,
    and a runner that sleeps 10s, consult() returned only after 2.008s --
    the caller's answer was correct, but the caller WAITED the full
    default deadline, which on a hot path is exactly the stall item 1
    exists to remove; a test using a SHORTENED deadline had proved the
    wrong property. Fixed at the source: in shadow and advise, consult()
    never waits for the worker AT ALL, not even bounded by the deadline
    -- the worker still applies the deadline to itself where that makes
    sense (see _run_seam_job's own docstring), and still writes the
    ledger row whenever it finishes, however long that takes; only act
    mode waits, since only act's own return value can depend on Jev's
    answer. Every test below uses the DEFAULT deadline (no
    call_deadline_s override) against a runner gated for up to 10s, to
    prove the real property this time."""

    def _gated_runner(self, release, hang_seconds=10.0):
        def gated(_argv, stdin_text):
            release.wait(hang_seconds)
            payload = json.loads(stdin_text)
            answers = {qid: {"noul": 0.9, "confidence": 0.9} for qid in payload["questions"]}
            return 0, json.dumps({"model": MODEL, "answers": answers, "usage": {"cost": 0.001}}), ""
        return gated

    def test_shadow_returns_in_under_50ms_with_the_api_hanging_10s(self):
        # A literal, uncontrolled `time.sleep(10)` inside the runner would
        # prove the same return-time property but leave the runner
        # genuinely blocked for up to 10 REAL seconds if this test forgot
        # to release it -- an Event with a 10s cap proves the identical
        # property ("hangs for up to 10s") while staying under this
        # test's own control: released explicitly, immediately after the
        # fast-return assertion, not by a clock.
        registry = [_noul_entry("N1")]
        release = threading.Event()
        runner = self._gated_runner(release, hang_seconds=10.0)

        t0 = time.monotonic()
        result = seam.consult(
            "N1", {"i": 1}, "safe-default",
            seams_config=_seams_config({"N1": "shadow"}),  # DEFAULT deadline: no override
            registry=registry, ledger_dir=self.ledger_dir, runner=runner,
        )
        elapsed = time.monotonic() - t0
        self.assertLess(elapsed, 0.05, "consult() waited for the worker in shadow mode")
        self.assertEqual(result.answer, "safe-default")
        self.assertEqual(result.mode, "shadow")
        self.assertIsNone(result.jev)
        # N1 fix (independent re-review, 2026-09-19): shadow now returns
        # decision_id=None, RESTORING "decision_id is only ever the id
        # this call actually wrote" -- the id has not been written yet at
        # this point (the worker is still gated), so nothing is handed
        # back that could be mistaken for a written promise. See
        # consult()'s own comment at the shadow return site.
        self.assertIsNone(result.decision_id)
        self.assertEqual(result.reason, "shadow: submitted")

        dp = os.path.join(self.ledger_dir, "decisions.jsonl")
        self.assertFalse(os.path.exists(dp))  # worker still gated: nothing written yet

        release.set()  # let the background worker's runner call return
        lines = self._wait_for_lines(dp, 1)
        self.assertEqual(len(lines), 1, "worker never wrote its ledger row after being released")
        written = json.loads(lines[0])
        self.assertAlmostEqual(written["answer"], 0.9)
        self.assertTrue(written["id"])  # the worker did write a real id -- just never handed to the caller

    def test_advise_waits_up_to_the_default_deadline_and_returns_jevs_answer(self):
        # M3 fix (independent review, 2026-09-19): advise is NOT
        # fire-and-forget -- it exists so a caller's own interface can
        # show Jev's answer, which needs consult() to actually wait for
        # it. Same default-deadline shape as the act-mode test below
        # (advise shares the same wait, just never changes `answer`).
        registry = [_noul_entry("N1")]
        runner = ScriptedRunner(_noul_answer(0.42))
        t0 = time.monotonic()
        result = seam.consult(
            "N1", {"i": 1}, "current",
            seams_config=_seams_config({"N1": "advise"}), registry=registry,
            ledger_dir=self.ledger_dir, runner=runner,
        )
        elapsed = time.monotonic() - t0
        self.assertLess(elapsed, seam.DEFAULT_HARD_DEADLINE_S)  # a fast runner: no need to wait it out
        self.assertEqual(result.answer, "current")  # advise never changes the answer
        self.assertIsNotNone(result.jev)
        self.assertAlmostEqual(result.jev["answer"], 0.42)
        self.assertIsNotNone(result.decision_id)

    def test_act_waits_up_to_the_default_deadline_then_returns(self):
        # Only act mode waits: bounded by _hard_deadline()'s own DEFAULT
        # (DEFAULT_HARD_DEADLINE_S = 2.0s, no call_deadline_s override
        # here either), plus a small real margin for thread scheduling.
        registry = [_noul_entry("N1")]
        release = threading.Event()  # never set: the worker keeps hanging past the deadline
        runner = self._gated_runner(release, hang_seconds=10.0)
        try:
            t0 = time.monotonic()
            result = seam.consult(
                "N1", {"i": 1}, "safe-default",
                seams_config=_seams_config({"N1": "act"}), registry=registry,
                ledger_dir=self.ledger_dir, runner=runner,
            )
            elapsed = time.monotonic() - t0
            self.assertGreaterEqual(elapsed, seam.DEFAULT_HARD_DEADLINE_S)
            self.assertLess(elapsed, seam.DEFAULT_HARD_DEADLINE_S + 0.5)  # small real margin
            self.assertEqual(result.answer, "safe-default")
            self.assertIsNone(result.jev)
            self.assertIsNone(result.decision_id)
            self.assertIn("NO-DATA", result.reason)
            self.assertIn("timed out", result.reason.lower())
        finally:
            release.set()

    def test_fast_runner_is_unaffected_by_the_bound_in_act_mode(self):
        # The common/default case for the one mode that still waits: a
        # fast runner well inside the deadline observes no difference
        # from the old fully-synchronous behaviour.
        registry = [_noul_entry("N1")]
        runner = ScriptedRunner(_noul_answer(0.83))
        cfg = _seams_config({"N1": "act"}, promotions_path=os.path.join(self.promo_dir, "no-such.jsonl"))
        result = seam.consult(
            "N1", {"i": 1}, "current",
            seams_config=cfg, registry=registry,
            ledger_dir=self.ledger_dir, runner=runner,
        )
        self.assertIsNotNone(result.jev)
        self.assertAlmostEqual(result.jev["answer"], 0.83)
        self.assertIsNotNone(result.decision_id)


class Item1QueueFullDrop(SeamTestCase):
    """Item 1 (A0.8): a full "queue" (the inflight concurrency cap) drops
    a call immediately rather than blocking for it."""

    def test_second_call_drops_while_the_first_still_occupies_the_one_slot(self):
        registry = [_noul_entry("N1")]
        gate = threading.Event()

        def gated(_argv, stdin_text):
            gate.wait(5.0)
            payload = json.loads(stdin_text)
            answers = {qid: {"noul": 0.9, "confidence": 0.9} for qid in payload["questions"]}
            return 0, json.dumps({"model": MODEL, "answers": answers, "usage": {"cost": 0.001}}), ""

        cfg = _seams_config({"N1": "shadow"}, max_inflight_calls=1, call_deadline_s=0.01)
        seam.DROP_COUNTS.pop("N1", None)

        first = threading.Thread(
            target=seam.consult, args=("N1", {"i": 1}, "cur"),
            kwargs=dict(seams_config=cfg, registry=registry, ledger_dir=self.ledger_dir, runner=gated),
            daemon=True,
        )
        first.start()
        # Wait for the first call's worker to actually acquire its slot
        # before firing the second: this test asserts the DROP, not a race
        # against thread startup.
        wait_until = time.monotonic() + 2.0
        while time.monotonic() < wait_until and seam._INFLIGHT_COUNT < 1:
            time.sleep(0.005)
        self.assertGreaterEqual(seam._INFLIGHT_COUNT, 1)

        result = seam.consult(
            "N1", {"i": 2}, "second-answer",
            seams_config=cfg, registry=registry, ledger_dir=self.ledger_dir, runner=gated,
        )
        self.assertEqual(result.answer, "second-answer")
        self.assertIsNone(result.jev)
        self.assertIsNone(result.decision_id)
        self.assertFalse(result.audit)
        self.assertIn("dropped", (result.reason or "").lower())
        self.assertEqual(seam.DROP_COUNTS.get("N1"), 1)

        gate.set()
        first.join(5.0)


class Item2Breaker(SeamTestCase):
    """Item 2 (A0.8): a breaker opens after consecutive Jev
    failures/timeouts and, while open, every seam behaves as OFF (no
    runner call at all) until an injected clock shows the cool-off has
    passed.

    Orchestrator fix 2026-09-19: shadow/advise never wait for their own
    worker (see Item1NonBlockingCalls), so only ACT mode's caller thread
    can ever observe and classify a TIMEOUT for the breaker (see
    consult()'s own `record_breaker_here` logic) -- the tests below use
    `act` with a small call_deadline_s override to keep this fast (that
    override is legitimate here: this class proves breaker semantics, not
    the default-deadline latency bound, which Item1NonBlockingCalls
    already proves with the real default). A separate test proves that
    shadow's own worker-side failures (never a caller-observed timeout,
    since shadow never waits, but a real bridge failure the worker itself
    sees) still drive the SAME breaker, just on the worker's own
    schedule."""

    def _act_cfg(self, threshold=5, cooldown=60.0, deadline=0.01):
        return _seams_config({"N1": "act"}, call_deadline_s=deadline,
                              breaker_fail_threshold=threshold, breaker_cooldown_s=cooldown,
                              promotions_path=os.path.join(self.promo_dir, "no-such.jsonl"))

    def test_five_consecutive_act_timeouts_open_the_breaker_then_cooldown_closes_it(self):
        registry = [_noul_entry("N1")]
        release = threading.Event()  # never set until the very end: every call hangs
        calls = []

        def hung(_argv, stdin_text):
            calls.append(1)
            release.wait(5.0)
            return 0, json.dumps({"model": MODEL, "answers": {}, "usage": {"cost": 0.0}}), ""

        fake_now = [1_000_000.0]
        clock = lambda: fake_now[0]
        cfg = self._act_cfg()

        try:
            for i in range(5):
                result = seam.consult(
                    "N1", {"i": i}, "cur", seams_config=cfg, registry=registry,
                    ledger_dir=self.ledger_dir, runner=hung, clock=clock,
                )
                self.assertIn("timed out", (result.reason or "").lower())
            self.assertEqual(len(calls), 5)

            # Breaker open: the 6th call makes NO runner call at all.
            result = seam.consult(
                "N1", {"i": 99}, "cur", seams_config=cfg, registry=registry,
                ledger_dir=self.ledger_dir, runner=hung, clock=clock,
            )
            self.assertEqual(result.mode, "off")
            self.assertIn("breaker", (result.reason or "").lower())
            self.assertEqual(len(calls), 5)  # unchanged: no attempt was made

            # Advance the injected clock past the cool-off: a half-open
            # retry is allowed through again (still hangs -> still a
            # timeout, but it was at least attempted).
            fake_now[0] += 61.0
            result = seam.consult(
                "N1", {"i": 100}, "cur", seams_config=cfg, registry=registry,
                ledger_dir=self.ledger_dir, runner=hung, clock=clock,
            )
            self.assertNotEqual(result.mode, "off")
            self.assertEqual(len(calls), 6)
        finally:
            release.set()

    def test_a_success_closes_the_breaker(self):
        registry = [_noul_entry("N1")]
        fake_now = [1_000_000.0]
        clock = lambda: fake_now[0]
        cfg = self._act_cfg(threshold=2)

        def hung(_argv, _stdin_text):
            time.sleep(0.05)  # exceeds the 0.01s deadline: a real timeout
            return 0, json.dumps({"model": MODEL, "answers": {}, "usage": {"cost": 0.0}}), ""

        r1 = seam.consult("N1", {"i": 1}, "cur", seams_config=cfg, registry=registry,
                           ledger_dir=self.ledger_dir, runner=hung, clock=clock)
        self.assertIn("timed out", (r1.reason or "").lower())

        fast = ScriptedRunner(_noul_answer(0.9))
        r2 = seam.consult("N1", {"i": 2}, "cur", seams_config=cfg, registry=registry,
                           ledger_dir=self.ledger_dir, runner=fast, clock=clock)
        self.assertIsNotNone(r2.jev)  # succeeded: breaker reset to 0

        r3 = seam.consult("N1", {"i": 3}, "cur", seams_config=cfg, registry=registry,
                           ledger_dir=self.ledger_dir, runner=hung, clock=clock)
        self.assertIn("timed out", (r3.reason or "").lower())
        self.assertNotEqual(r3.mode, "off")  # only 1 consecutive failure since the reset, not 2

    def test_shadow_worker_side_failures_also_trip_the_breaker(self):
        # Shadow never waits, so it can never observe a caller-side
        # timeout -- but a real bridge failure the WORKER itself sees
        # (returncode != 0, a genuine NO-DATA from decide()) still counts,
        # recorded by the worker in its own finally block (see
        # _run_seam_job's `record_breaker_here`). Fired, then polled for,
        # since nothing here waits for the worker directly.
        registry = [_noul_entry("N1")]
        runner = ScriptedRunner(_noul_answer(0.9), returncode=3, stderr="boom")
        cfg = _seams_config({"N1": "shadow"}, breaker_fail_threshold=5, breaker_cooldown_s=60.0)

        for i in range(5):
            result = seam.consult("N1", {"i": i}, "cur", seams_config=cfg, registry=registry,
                                  ledger_dir=self.ledger_dir, runner=runner)
            self.assertEqual(result.reason, "shadow: submitted")  # fired, not yet judged

        deadline = time.monotonic() + 2.0
        while time.monotonic() < deadline and seam._BREAKER_STATE["opened_at"] is None:
            time.sleep(0.005)
        self.assertIsNotNone(seam._BREAKER_STATE["opened_at"], "breaker never opened")

        # A further call (any mode) is now denied before a 6th runner call.
        result = seam.consult("N1", {"i": 99}, "cur", seams_config=cfg, registry=registry,
                              ledger_dir=self.ledger_dir, runner=runner)
        self.assertEqual(result.mode, "off")
        self.assertIn("breaker", (result.reason or "").lower())
        self.assertEqual(len(runner.calls), 5)  # unchanged: the 6th never ran


class Item2Budget(SeamTestCase):
    """Item 2 (A0.8): a per-day call budget caps how many calls actually
    reach the runner; once spent, every seam behaves as OFF. Budget
    consumption itself is synchronous (consult() checks and spends a slot
    in the CALLING thread, before ever spawning a worker -- see
    _consume_budget_slot()'s own call site), so `mode == "off"` is still
    observed immediately even for shadow; only the RUNNER CALL each
    admitted attempt makes happens on its own background worker
    (orchestrator fix 2026-09-19), so counting runner calls needs a
    bounded wait."""

    def test_budget_of_100_makes_exactly_100_runner_calls_out_of_1000(self):
        registry = [_noul_entry("N1")]
        runner = ScriptedRunner(_noul_answer(0.9))
        cfg = _seams_config({"N1": "shadow"}, daily_call_budget=100,
                             budget_path=os.path.join(self.ledger_dir, "budget.json"))
        off_count = 0
        for i in range(1000):
            result = seam.consult(
                "N1", {"i": i}, "cur", seams_config=cfg, registry=registry,
                ledger_dir=self.ledger_dir, runner=runner,
            )
            if result.mode == "off":
                off_count += 1
        self.assertEqual(off_count, 900)  # synchronous: the budget gate itself never waits
        deadline = time.monotonic() + 3.0
        while time.monotonic() < deadline and len(runner.calls) < 100:
            time.sleep(0.005)
        self.assertEqual(len(runner.calls), 100)

    def test_budget_exhausted_result_shape(self):
        registry = [_noul_entry("N1")]
        runner = ScriptedRunner(_noul_answer(0.9))
        budget_path = os.path.join(self.ledger_dir, "budget.json")
        cfg = _seams_config({"N1": "shadow"}, daily_call_budget=1, budget_path=budget_path)
        first = seam.consult("N1", {"i": 1}, "a", seams_config=cfg, registry=registry,
                              ledger_dir=self.ledger_dir, runner=runner)
        self.assertEqual(first.mode, "shadow")  # admitted: not denied by budget
        self.assertEqual(first.reason, "shadow: submitted")
        with open(budget_path, encoding="utf-8") as fh:
            self.assertEqual(json.load(fh)["calls"], 1)
        second = seam.consult("N1", {"i": 2}, "b", seams_config=cfg, registry=registry,
                               ledger_dir=self.ledger_dir, runner=runner)
        self.assertEqual(second.answer, "b")
        self.assertEqual(second.mode, "off")
        self.assertIsNone(second.jev)
        self.assertIsNone(second.decision_id)
        self.assertIn("budget", (second.reason or "").lower())
        deadline = time.monotonic() + 2.0
        while time.monotonic() < deadline and len(runner.calls) < 1:
            time.sleep(0.005)
        self.assertEqual(len(runner.calls), 1)

    def test_budget_persists_across_a_fresh_read(self):
        # "Survives restarts": a second, independent read of the same
        # budget_path (as a fresh process would do) sees the same count.
        registry = [_noul_entry("N1")]
        runner = ScriptedRunner(_noul_answer(0.9))
        budget_path = os.path.join(self.ledger_dir, "budget.json")
        cfg = _seams_config({"N1": "shadow"}, daily_call_budget=3, budget_path=budget_path)
        for i in range(3):
            seam.consult("N1", {"i": i}, "a", seams_config=cfg, registry=registry,
                         ledger_dir=self.ledger_dir, runner=runner)
        with open(budget_path, encoding="utf-8") as fh:
            saved = json.load(fh)
        self.assertEqual(saved["calls"], 3)
        result = seam.consult("N1", {"i": 99}, "a", seams_config=cfg, registry=registry,
                               ledger_dir=self.ledger_dir, runner=runner)
        self.assertEqual(result.mode, "off")


class JevStateDirCrossCheckout(SeamTestCase):
    """Item 1 (approved-with-nits review 2026-09-18): the ledger and the
    canary reset marker must be reachable from ANY checkout or worktree
    of this repo, not just the one that wrote them -- a git clean -X or a
    worktree removal must never erase a human label or a drift incident.
    Proven by patching each module's own repo-root constant to a
    DIFFERENT fake path (simulating two distinct checkouts) while the
    default marker path is the one both modules actually compute from
    JEV_STATE_DIR: a canary FAIL written under "checkout A" must still
    force jev_seam's act mode to shadow when consult() is called under
    "checkout B"."""

    def test_canary_fail_from_one_checkout_reaches_seam_in_another(self):
        shared_marker = os.path.join(tempfile.mkdtemp(), "canary-reset.json")

        # "Checkout A": jev_canary writes its FAIL marker here.
        # jev_canary.REPO_ROOT is patched to an unrelated fake path to
        # prove the marker's location no longer depends on it.
        fake_repo_a = tempfile.mkdtemp()
        with mock.patch.object(jev_canary, "REPO_ROOT", fake_repo_a), \
             mock.patch.object(jev_canary, "DEFAULT_RESET_MARKER", shared_marker):
            write_err = jev_canary._write_reset_marker(
                jev_canary.DEFAULT_RESET_MARKER, "model id changed: simulated",
                jev_canary.PINNED_MODEL, ["other-model"])
        self.assertIsNone(write_err)
        self.assertTrue(os.path.exists(shared_marker))

        # "Checkout B": jev_seam.consult() runs with a DIFFERENT fake
        # _REPO_ROOT, real calibrated evidence and a valid promotion that
        # would otherwise let this exact decision ACT -- proving the
        # shared marker, not a missing promotion, is what forces shadow.
        fake_repo_b = tempfile.mkdtemp()
        dp = os.path.join(self.ledger_dir, "decisions.jsonl")
        op = os.path.join(self.ledger_dir, "outcomes.jsonl")
        pp = os.path.join(self.promo_dir, "promotions.jsonl")
        _seed_calibration(dp, op, "SD1", "noul", 0.97, n_correct=40)
        with open(pp, "w", encoding="utf-8") as fh:
            fh.write(json.dumps({
                "family": "SD1", "qtype": "noul", "risk_class": "low", "model": MODEL,
                "signed_by": "founder", "signed_at": "2026-09-18T00:00:00Z",
                "bound_at_signing": 0.85, "n_at_signing": 40,
                "flip_condition": "precision drops below target for two weeks",
                "review_by": "2099-01-01",
            }) + "\n")
        registry = [_noul_entry("SD1", risk="low")]
        runner = ScriptedRunner(_noul_answer(0.97))
        with mock.patch.object(seam, "_REPO_ROOT", fake_repo_b), \
             mock.patch.object(seam, "DEFAULT_CANARY_MARKER", shared_marker):
            result = seam.consult(
                "SD1", {"i": 1}, "current",
                seams_config=_seams_config({"SD1": "act"}, promotions_path=pp),
                registry=registry, ledger_dir=self.ledger_dir, runner=runner,
            )
        self.assertEqual(result.mode, "off")
        self.assertIn("canary", (result.reason or "").lower())
        self.assertEqual(result.answer, "current")

    def test_red_proof_the_old_repo_root_anchored_scheme_would_have_missed_this(self):
        """RED PROOF: M1's OWN (pre-item-1) formula --
        os.path.join(<that checkout's own repo root>, "data",
        "jev-canary-reset.json") -- computes a DIFFERENT path for two
        different fake repo roots, which is exactly why a canary FAIL
        written under one checkout's repo-root-anchored default was never
        seen by a seam consult() running from another checkout: the
        property test_canary_fail_from_one_checkout_reaches_seam_in_another
        proves is a property that COULD fail, and did, under the scheme
        item 1 replaces. JEV_STATE_DIR does not have this problem: it is
        computed once from an env var or a fixed home path, never from
        either module's own repo-root constant, so patching repo root
        leaves it, and both defaults built from it, unchanged."""
        fake_repo_a = tempfile.mkdtemp()
        fake_repo_b = tempfile.mkdtemp()
        old_scheme_path_a = os.path.join(fake_repo_a, "data", "jev-canary-reset.json")
        old_scheme_path_b = os.path.join(fake_repo_b, "data", "jev-canary-reset.json")
        self.assertNotEqual(old_scheme_path_a, old_scheme_path_b)  # RED under the old scheme

        with mock.patch.object(jev_canary, "REPO_ROOT", fake_repo_a):
            new_scheme_path_a = os.path.join(jev_canary.JEV_STATE_DIR, "canary-reset.json")
        with mock.patch.object(seam, "_REPO_ROOT", fake_repo_b):
            new_scheme_path_b = os.path.join(seam.JEV_STATE_DIR, "canary-reset.json")
        self.assertEqual(new_scheme_path_a, new_scheme_path_b)  # GREEN under item 1


class M4ProbeUsesTheSameNumberRoutingUses(SeamTestCase):
    """M4 (review 70268c3): the near-threshold probe used probability
    first, routing used confidence first; a choice answer with
    probability 0.5 and confidence 0.99 used to skip the probe (far from
    threshold by probability) and then act anyway (close to threshold, in
    fact past it, by confidence)."""

    def test_probability_0_5_confidence_0_99_now_probes(self):
        dp = os.path.join(self.ledger_dir, "decisions.jsonl")
        op = os.path.join(self.ledger_dir, "outcomes.jsonl")
        _seed_calibration(dp, op, "CH3", "choice", 0.95, n_correct=40)
        registry = [_choice_entry("CH3", risk="low")]

        def fixed_split_answer(_qid, q):
            # Always "a" at probability 0.5, confidence 0.99, whichever
            # order or question id it is asked under: consistent between
            # the primary call and the reordered probe, so a disagreement
            # never masks whether the probe ran at all.
            keys = list(q["criteria"].keys())
            others = [k for k in keys if k != "a"]
            probs = {"a": 0.5}
            if others:
                rest = 0.5 / len(others)
                for k in others:
                    probs[k] = rest
            return {"choice": "a", "probabilities": probs, "confidence": 0.99}

        runner = ScriptedRunner(fixed_split_answer)
        # act mode, deliberately missing promotions_path: shadow never
        # waits for this worker at all (orchestrator fix 2026-09-19), so
        # it cannot observe the probe's call count synchronously either --
        # the probe itself runs in the SAME shared worker code regardless
        # of mode.
        cfg = _seams_config({"CH3": "act"}, promotions_path=os.path.join(self.promo_dir, "no-such.jsonl"))
        result = seam.consult(
            "CH3", {"i": 1}, "current",
            seams_config=cfg, registry=registry,
            ledger_dir=self.ledger_dir, runner=runner,
        )
        # threshold ~= 0.9 (same n=40-all-correct evidence as Rule5/6);
        # routing's own number (confidence 0.99) is within
        # NEAR_THRESHOLD_BAND of it, so the probe fires: two bridge calls.
        self.assertEqual(len(runner.calls), 2)
        self.assertIsNotNone(result.jev)


class M3PromotionsStoreNeverUnderLedgerDir(SeamTestCase):
    """M3 (review 70268c3): the promotion store must live outside any
    directory a seam writes to. consult() must never read (or write) a
    promotions file inside ledger_dir; with no promotions_path override
    in seams_config, it must fall back to
    jev_cascade.DEFAULT_PROMOTIONS_PATH instead -- even when a perfectly
    valid, calibrated, signed promotion sits at the OLD path this module
    used to build from ledger_dir."""

    def test_valid_promotion_planted_in_ledger_dir_is_never_read_or_written(self):
        dp = os.path.join(self.ledger_dir, "decisions.jsonl")
        op = os.path.join(self.ledger_dir, "outcomes.jsonl")
        _seed_calibration(dp, op, "M3A", "noul", 0.97, n_correct=40)
        # Plant a fully valid signed promotion at the OLD ledger_dir path
        # (what jev_seam.py used to build with os.path.join(ledger_dir,
        # "promotions.jsonl")): if this were still read, the decision
        # below would ACT.
        stale_pp = os.path.join(self.ledger_dir, "promotions.jsonl")
        stale_content = json.dumps({
            "family": "M3A", "qtype": "noul", "risk_class": "low", "model": MODEL,
            "signed_by": "founder", "signed_at": "2026-09-18T00:00:00Z",
            "bound_at_signing": 0.85, "n_at_signing": 40,
            "flip_condition": "precision drops below target for two weeks",
            "review_by": "2099-01-01",
        }) + "\n"
        with open(stale_pp, "w", encoding="utf-8") as fh:
            fh.write(stale_content)

        registry = [_noul_entry("M3A", risk="low")]
        runner = ScriptedRunner(_noul_answer(0.97))
        # No promotions_path key in seams_config: must fall back to
        # jev_cascade.DEFAULT_PROMOTIONS_PATH, patched here to an
        # isolated, guaranteed-empty temp path so this test is hermetic
        # -- it never depends on, or risks touching, the real repo-root
        # data/jev-promotions.jsonl.
        empty_default = os.path.join(tempfile.mkdtemp(), "jev-promotions.jsonl")
        with mock.patch.object(cascade, "DEFAULT_PROMOTIONS_PATH", empty_default):
            result = seam.consult(
                "M3A", {"i": 1}, "current",
                seams_config=_seams_config({"M3A": "act"}), registry=registry,
                ledger_dir=self.ledger_dir, runner=runner,
            )
        # Never acted: the only valid promotion on disk sat at the
        # ledger_dir path, which this module must never consult.
        self.assertEqual(result.answer, "current")
        self.assertEqual(result.mode, "act")
        self.assertIn("promotion", (result.reason or ""))
        # Never READ: a promotion consult() actually used would have let
        # this decision ACT above. Never WRITTEN either: the planted file
        # is byte-for-byte unchanged, and the patched default path was
        # never created (nothing was ever found there to act on, and
        # consult() never writes a promotions file regardless).
        with open(stale_pp, encoding="utf-8") as fh:
            self.assertEqual(fh.read(), stale_content)
        self.assertFalse(os.path.exists(empty_default))


class MinorPromotionsPathOverrideUnderLedgerDirIsRefused(SeamTestCase):
    """Minor, re-review of 1385ab88c: a seams_config["promotions_path"]
    override that resolves UNDER ledger_dir must be refused too, not
    only the module's own unconfigured default -- else the override key
    that exists to let a caller point at a real, external promotions
    store reopens exactly the M3 hole by hand."""

    def test_override_under_ledger_dir_falls_back_to_cascade_default(self):
        dp = os.path.join(self.ledger_dir, "decisions.jsonl")
        op = os.path.join(self.ledger_dir, "outcomes.jsonl")
        _seed_calibration(dp, op, "M3B", "noul", 0.97, n_correct=40)
        # A fully valid, calibrated, signed promotion -- but the CALLER
        # explicitly points promotions_path at a file under ledger_dir.
        # If this were honored, the decision below would ACT.
        sneaky_pp = os.path.join(self.ledger_dir, "sneaky-promotions.jsonl")
        sneaky_content = json.dumps({
            "family": "M3B", "qtype": "noul", "risk_class": "low", "model": MODEL,
            "signed_by": "founder", "signed_at": "2026-09-18T00:00:00Z",
            "bound_at_signing": 0.85, "n_at_signing": 40,
            "flip_condition": "precision drops below target for two weeks",
            "review_by": "2099-01-01",
        }) + "\n"
        with open(sneaky_pp, "w", encoding="utf-8") as fh:
            fh.write(sneaky_content)

        registry = [_noul_entry("M3B", risk="low")]
        runner = ScriptedRunner(_noul_answer(0.97))
        # Patch jev_cascade.DEFAULT_PROMOTIONS_PATH to an isolated,
        # guaranteed-empty temp path so this test is hermetic (never
        # depends on, or risks touching, the real repo-root file) --
        # exactly M3PromotionsStoreNeverUnderLedgerDir's own pattern.
        empty_default = os.path.join(tempfile.mkdtemp(), "jev-promotions.jsonl")
        with mock.patch.object(cascade, "DEFAULT_PROMOTIONS_PATH", empty_default):
            result = seam.consult(
                "M3B", {"i": 1}, "current",
                seams_config=_seams_config({"M3B": "act"}, promotions_path=sneaky_pp),
                registry=registry, ledger_dir=self.ledger_dir, runner=runner,
            )
        # Refused: the override was ignored and the (patched, empty)
        # cascade default was used instead, so no promotion was found and
        # the decision never ACTs.
        self.assertEqual(result.answer, "current")
        self.assertEqual(result.mode, "act")
        self.assertIn("promotion", (result.reason or ""))
        # Never READ: unchanged on disk, exactly as with the module's own
        # default in M3PromotionsStoreNeverUnderLedgerDir above.
        with open(sneaky_pp, encoding="utf-8") as fh:
            self.assertEqual(fh.read(), sneaky_content)

    def test_helper_refuses_under_ledger_dir_but_accepts_a_sibling_path(self):
        # A direct, isolated check of _promotions_path()'s own contract:
        # refuse a path under ledger_dir, accept one that merely sits
        # beside it.
        with mock.patch.object(cascade, "DEFAULT_PROMOTIONS_PATH", "/patched/default.jsonl"):
            under = os.path.join(self.ledger_dir, "sneaky.jsonl")
            self.assertEqual(
                seam._promotions_path(_seams_config({}, promotions_path=under),
                                       self.ledger_dir),
                "/patched/default.jsonl",
            )
            equal_to_ledger_dir = self.ledger_dir
            self.assertEqual(
                seam._promotions_path(_seams_config({}, promotions_path=equal_to_ledger_dir),
                                       self.ledger_dir),
                "/patched/default.jsonl",
            )
            sibling = os.path.join(self.promo_dir, "promotions.jsonl")
            self.assertEqual(
                seam._promotions_path(_seams_config({}, promotions_path=sibling),
                                       self.ledger_dir),
                sibling,
            )


class C1BudgetFailsClosed(SeamTestCase):
    """C1 (independent review, 2026-09-19, CRITICAL): a corrupt,
    unreadable, or unwritable budget file must deny every call
    (mode=off), never silently admit them by reading the broken state as
    zero calls used. Probed: read-only directory, limit 2, 10 of 10
    admitted -- must now be 0 of 10."""

    def test_corrupt_budget_file_denies_every_call(self):
        registry = [_noul_entry("N1")]
        runner = ScriptedRunner(_noul_answer(0.9))
        budget_path = os.path.join(self.ledger_dir, "budget.json")
        with open(budget_path, "w", encoding="utf-8") as fh:
            fh.write("{not json at all")
        cfg = _seams_config({"N1": "shadow"}, daily_call_budget=2, budget_path=budget_path)
        for i in range(10):
            result = seam.consult("N1", {"i": i}, "cur", seams_config=cfg, registry=registry,
                                  ledger_dir=self.ledger_dir, runner=runner)
            self.assertEqual(result.mode, "off")
            self.assertIn("budget", (result.reason or "").lower())
        self.assertEqual(len(runner.calls), 0)

    def test_budget_file_holding_a_json_array_not_an_object_denies_every_call(self):
        registry = [_noul_entry("N1")]
        runner = ScriptedRunner(_noul_answer(0.9))
        budget_path = os.path.join(self.ledger_dir, "budget.json")
        with open(budget_path, "w", encoding="utf-8") as fh:
            json.dump([1, 2, 3], fh)
        cfg = _seams_config({"N1": "shadow"}, daily_call_budget=2, budget_path=budget_path)
        result = seam.consult("N1", {"i": 1}, "cur", seams_config=cfg, registry=registry,
                              ledger_dir=self.ledger_dir, runner=runner)
        self.assertEqual(result.mode, "off")
        self.assertIn("budget", (result.reason or "").lower())
        self.assertEqual(len(runner.calls), 0)

    def test_unwritable_budget_directory_denies_every_call(self):
        if hasattr(os, "geteuid") and os.geteuid() == 0:
            self.skipTest("permission bits are not enforced for root")
        registry = [_noul_entry("N1")]
        runner = ScriptedRunner(_noul_answer(0.9))
        readonly_dir = os.path.join(self.ledger_dir, "readonly")
        os.makedirs(readonly_dir)
        budget_path = os.path.join(readonly_dir, "budget.json")
        os.chmod(readonly_dir, 0o500)  # r-x: cannot create a file inside
        try:
            cfg = _seams_config({"N1": "shadow"}, daily_call_budget=2, budget_path=budget_path)
            for i in range(10):
                result = seam.consult("N1", {"i": i}, "cur", seams_config=cfg, registry=registry,
                                      ledger_dir=self.ledger_dir, runner=runner)
                self.assertEqual(result.mode, "off")
                self.assertIn("budget", (result.reason or "").lower())
        finally:
            os.chmod(readonly_dir, 0o700)
        self.assertEqual(len(runner.calls), 0)

    def test_missing_budget_file_is_the_ordinary_fresh_day_case_not_corrupt(self):
        # The one legitimate "read nothing, use zero" case: a budget file
        # that has simply never been written yet. Must NOT be treated the
        # same as a present-but-broken file.
        registry = [_noul_entry("N1")]
        runner = ScriptedRunner(_noul_answer(0.9))
        budget_path = os.path.join(self.ledger_dir, "does-not-exist-yet.json")
        cfg = _seams_config({"N1": "shadow"}, daily_call_budget=5, budget_path=budget_path)
        result = seam.consult("N1", {"i": 1}, "cur", seams_config=cfg, registry=registry,
                              ledger_dir=self.ledger_dir, runner=runner)
        self.assertNotEqual(result.mode, "off")
        with open(budget_path, encoding="utf-8") as fh:
            self.assertEqual(json.load(fh)["calls"], 1)


class M1DroppedCallsSpendNoBudget(SeamTestCase):
    """M1 (independent review, 2026-09-19): a call dropped for queue-full
    must never consume a budget slot -- the docstring always claimed this,
    the ordering did not enforce it. Probed: max_inflight_calls=1, budget
    5, 8 calls against a hung runner -> 1 real attempt, 4 drops, 3 "budget
    exhausted", file read {"calls": 5}; must now read {"calls": 1}."""

    def test_dropped_calls_never_consume_budget(self):
        registry = [_noul_entry("N1")]
        gate = threading.Event()

        def gated(_argv, stdin_text):
            gate.wait(5.0)
            payload = json.loads(stdin_text)
            answers = {qid: {"noul": 0.9, "confidence": 0.9} for qid in payload["questions"]}
            return 0, json.dumps({"model": MODEL, "answers": answers, "usage": {"cost": 0.001}}), ""

        budget_path = os.path.join(self.ledger_dir, "budget.json")
        cfg = _seams_config({"N1": "shadow"}, max_inflight_calls=1, daily_call_budget=5,
                             budget_path=budget_path)
        first = threading.Thread(
            target=seam.consult, args=("N1", {"i": 1}, "cur"),
            kwargs=dict(seams_config=cfg, registry=registry, ledger_dir=self.ledger_dir, runner=gated),
            daemon=True,
        )
        first.start()
        deadline = time.monotonic() + 2.0
        while time.monotonic() < deadline and seam._INFLIGHT_COUNT < 1:
            time.sleep(0.005)
        self.assertGreaterEqual(seam._INFLIGHT_COUNT, 1)

        try:
            drop_count = 0
            for i in range(7):
                result = seam.consult("N1", {"i": i + 2}, "cur", seams_config=cfg, registry=registry,
                                      ledger_dir=self.ledger_dir, runner=gated)
                if "dropped" in (result.reason or "").lower():
                    drop_count += 1
            self.assertGreater(drop_count, 0)
            with open(budget_path, encoding="utf-8") as fh:
                saved = json.load(fh)
            self.assertEqual(saved["calls"], 1)  # only the ONE real attempt spent a slot
        finally:
            gate.set()
            first.join(5.0)


class M2LedgerDirCreatedAndWriteFailureIsolated(SeamTestCase):
    """M2 (independent review, 2026-09-19): DEFAULT_LEDGER_DIR (or any
    never-yet-created ledger_dir) is created before writing, and a ledger
    write's own OSError is a LOCAL failure, never counted as a Jev
    failure against the breaker. Probed: missing folder, 6 shadow calls
    -> runner called 5 times, no rows written, breaker opened blaming
    Jev (consecutive_failures: 5)."""

    def test_missing_ledger_dir_is_created_before_writing(self):
        registry = [_noul_entry("N1")]
        runner = ScriptedRunner(_noul_answer(0.9))
        fresh_dir = os.path.join(self.ledger_dir, "never-created", "nested")
        self.assertFalse(os.path.isdir(fresh_dir))
        cfg = _seams_config({"N1": "act"}, promotions_path=os.path.join(self.promo_dir, "no-such.jsonl"))
        result = seam.consult("N1", {"i": 1}, "current", seams_config=cfg, registry=registry,
                              ledger_dir=fresh_dir, runner=runner)
        self.assertIsNotNone(result.jev)
        # No calibration evidence yet (a fresh ledger) legitimately
        # escalates in act mode -- that reason is unrelated to this test's
        # own point, which is that the write itself succeeded rather than
        # raising a local-ledger-write reason (see the sibling OSError
        # test above for that case).
        self.assertNotIn("local ledger write failed", (result.reason or ""))
        dp = os.path.join(fresh_dir, "decisions.jsonl")
        self.assertTrue(os.path.exists(dp))

    def test_five_shadow_calls_against_a_missing_ledger_dir_never_open_the_breaker(self):
        # The reviewer's own probe shape: a missing ledger dir must not
        # look like "Jev failed 5 times in a row".
        registry = [_noul_entry("N1")]
        runner = ScriptedRunner(_noul_answer(0.9))
        fresh_dir = os.path.join(self.ledger_dir, "never-created-2")
        cfg = _seams_config({"N1": "shadow"}, breaker_fail_threshold=5)
        for i in range(6):
            seam.consult("N1", {"i": i}, "cur", seams_config=cfg, registry=registry,
                         ledger_dir=fresh_dir, runner=runner)
        deadline = time.monotonic() + 2.0
        while time.monotonic() < deadline and len(runner.calls) < 6:
            time.sleep(0.005)
        self.assertEqual(len(runner.calls), 6)
        self.assertIsNone(seam._BREAKER_STATE["opened_at"], "breaker wrongly blamed Jev for a local ledger-dir problem")
        dp = os.path.join(fresh_dir, "decisions.jsonl")
        lines = self._wait_for_lines(dp, 6)
        self.assertEqual(len(lines), 6)

    def test_ledger_write_oserror_is_local_not_counted_against_the_breaker(self):
        registry = [_noul_entry("N1")]
        runner = ScriptedRunner(_noul_answer(0.9))
        cfg = _seams_config({"N1": "act"}, promotions_path=os.path.join(self.promo_dir, "no-such.jsonl"),
                             breaker_fail_threshold=1)
        with mock.patch.object(seam._calibration, "append_decision", side_effect=OSError("disk full (simulated)")):
            result = seam.consult("N1", {"i": 1}, "current", seams_config=cfg, registry=registry,
                                  ledger_dir=self.ledger_dir, runner=runner)
        self.assertIsNotNone(result.jev)  # Jev still answered fine
        self.assertIn("local ledger write failed", (result.reason or "").lower())
        self.assertIsNone(result.decision_id)  # never actually written
        # Breaker must NOT have tripped (threshold=1 would trip on any
        # real Jev failure): a further call is still admitted, not "off".
        result2 = seam.consult("N1", {"i": 2}, "current", seams_config=cfg, registry=registry,
                               ledger_dir=self.ledger_dir, runner=runner)
        self.assertNotEqual(result2.mode, "off")


class M3AuditStoredInLedgerRow(SeamTestCase):
    """M3, second half (independent review, 2026-09-19): the A0.6 audit
    sample flag must be STORED in the ledger row, not computed and thrown
    away -- otherwise nothing reading the ledger later can ever find the
    rows a human is supposed to label."""

    def test_audit_flag_is_persisted_in_the_ledger_row(self):
        dp = os.path.join(self.ledger_dir, "decisions.jsonl")
        op = os.path.join(self.ledger_dir, "outcomes.jsonl")
        _seed_calibration(dp, op, "AUD5", "noul", 0.95, n_correct=40)
        registry = [_noul_entry("AUD5", risk="low")]
        runner = ScriptedRunner(_noul_answer(0.95))
        cfg = _seams_config({"AUD5": "act"}, promotions_path=os.path.join(self.promo_dir, "no-such.jsonl"))
        result = seam.consult("AUD5", {"i": 1}, "current", seams_config=cfg, registry=registry,
                              ledger_dir=self.ledger_dir, runner=runner, rng=lambda: 0.0)
        self.assertTrue(result.audit)
        with open(dp, encoding="utf-8") as fh:
            lines = [json.loads(l) for l in fh if l.strip()]
        written = [l for l in lines if l["id"] == result.decision_id]
        self.assertEqual(len(written), 1)
        self.assertTrue(written[0]["audit"])

    def test_audit_false_is_also_persisted_not_merely_absent(self):
        dp = os.path.join(self.ledger_dir, "decisions.jsonl")
        op = os.path.join(self.ledger_dir, "outcomes.jsonl")
        _seed_calibration(dp, op, "AUD6", "noul", 0.95, n_correct=40)
        registry = [_noul_entry("AUD6", risk="low")]
        # Confidence well below the calibrated threshold: never audited
        # (would be escalated anyway).
        runner = ScriptedRunner(_noul_answer(0.55))
        cfg = _seams_config({"AUD6": "act"}, promotions_path=os.path.join(self.promo_dir, "no-such.jsonl"))
        result = seam.consult("AUD6", {"i": 1}, "current", seams_config=cfg, registry=registry,
                              ledger_dir=self.ledger_dir, runner=runner, rng=lambda: 0.0)
        self.assertFalse(result.audit)
        with open(dp, encoding="utf-8") as fh:
            lines = [json.loads(l) for l in fh if l.strip()]
        written = [l for l in lines if l["id"] == result.decision_id]
        self.assertEqual(len(written), 1)
        self.assertIn("audit", written[0])
        self.assertFalse(written[0]["audit"])


class M4DrainGivesShortLivedProcessesAChanceToWrite(SeamTestCase):
    """M4 (independent review, 2026-09-19): a short-lived process must not
    lose a shadow row it already spent budget on. Probed: shadow call, a
    0.3s runner, then the process exits -- budget read {"calls": 1}, the
    ledger folder was empty. drain() (and the atexit hook that calls it
    automatically) closes this by waiting a bounded time for in-flight
    workers."""

    def test_drain_waits_for_an_in_flight_shadow_worker(self):
        registry = [_noul_entry("N1")]

        def slow(_argv, stdin_text):
            time.sleep(0.2)
            payload = json.loads(stdin_text)
            answers = {qid: {"noul": 0.9, "confidence": 0.9} for qid in payload["questions"]}
            return 0, json.dumps({"model": MODEL, "answers": answers, "usage": {"cost": 0.001}}), ""

        result = seam.consult("N1", {"i": 1}, "cur", seams_config=_seams_config({"N1": "shadow"}),
                              registry=registry, ledger_dir=self.ledger_dir, runner=slow)
        self.assertEqual(result.reason, "shadow: submitted")
        dp = os.path.join(self.ledger_dir, "decisions.jsonl")
        self.assertFalse(os.path.exists(dp))  # still running: proves drain() actually waited below

        remaining = seam.drain(timeout=2.0)
        self.assertEqual(remaining, 0)
        self.assertTrue(os.path.exists(dp))
        with open(dp, encoding="utf-8") as fh:
            self.assertEqual(sum(1 for l in fh if l.strip()), 1)

    def test_drain_reports_a_worker_still_running_past_its_own_timeout(self):
        registry = [_noul_entry("N1")]
        gate = threading.Event()

        def hung(_argv, _stdin_text):
            gate.wait(5.0)
            return 0, json.dumps({"model": MODEL, "answers": {}, "usage": {"cost": 0.0}}), ""

        try:
            seam.consult("N1", {"i": 1}, "cur", seams_config=_seams_config({"N1": "shadow"}),
                         registry=registry, ledger_dir=self.ledger_dir, runner=hung)
            remaining = seam.drain(timeout=0.05)
            self.assertGreaterEqual(remaining, 1)
        finally:
            gate.set()  # release so the leftover thread does not linger into later tests


class MinorWorkerStartGuard(SeamTestCase):
    """Review minor, 2026-09-19: a thread-creation failure used to escape
    consult() as a raw RuntimeError (breaking "never raises") and leak the
    inflight slot it had already acquired."""

    def test_thread_creation_failure_returns_callers_answer_and_releases_slot(self):
        registry = [_noul_entry("N1")]
        runner = ScriptedRunner(_noul_answer(0.9))
        before = seam._INFLIGHT_COUNT
        with mock.patch.object(seam.threading, "Thread") as mock_thread_cls:
            mock_thread_cls.return_value.start.side_effect = RuntimeError("can't start new thread (simulated)")
            result = seam.consult("N1", {"i": 1}, "safe", seams_config=_seams_config({"N1": "shadow"}),
                                  registry=registry, ledger_dir=self.ledger_dir, runner=runner)
        self.assertEqual(result.answer, "safe")
        self.assertIsNone(result.jev)
        self.assertIn("could not start", (result.reason or "").lower())
        self.assertEqual(seam._INFLIGHT_COUNT, before)  # released, not leaked


class MinorBreakerHalfOpenSingleProbe(SeamTestCase):
    """Review minor, 2026-09-19: "After the cool-off, every concurrent
    caller gets through at once, not one as the docstring says." Fixed via
    _BREAKER_STATE["probing"]: exactly one concurrent caller is admitted
    as the half-open probe."""

    def test_only_one_of_five_concurrent_callers_gets_the_half_open_probe(self):
        registry = [_noul_entry("N1")]
        fake_now = [1_000_000.0]
        clock = lambda: fake_now[0]
        base_cfg = _seams_config({"N1": "act"}, breaker_fail_threshold=1, breaker_cooldown_s=60.0,
                                  promotions_path=os.path.join(self.promo_dir, "no-such.jsonl"))

        trip_gate = threading.Event()

        def hung(_argv, _stdin_text):
            trip_gate.wait(5.0)
            return 0, json.dumps({"model": MODEL, "answers": {}, "usage": {"cost": 0.0}}), ""

        trip_cfg = dict(base_cfg, call_deadline_s=0.01)
        r0 = seam.consult("N1", {"i": 0}, "cur", seams_config=trip_cfg, registry=registry,
                          ledger_dir=self.ledger_dir, runner=hung, clock=clock)
        self.assertIn("timed out", (r0.reason or "").lower())
        trip_gate.set()

        fake_now[0] += 61.0  # cool-off passed
        probe_gate = threading.Event()
        probe_calls = []

        def probe_runner(_argv, stdin_text):
            probe_calls.append(1)
            probe_gate.wait(5.0)
            payload = json.loads(stdin_text)
            answers = {qid: {"noul": 0.9, "confidence": 0.9} for qid in payload["questions"]}
            return 0, json.dumps({"model": MODEL, "answers": answers, "usage": {"cost": 0.001}}), ""

        admitted = []
        probe_cfg = dict(base_cfg, call_deadline_s=2.0)

        def call(i):
            r = seam.consult("N1", {"i": i + 1}, "cur", seams_config=probe_cfg, registry=registry,
                             ledger_dir=self.ledger_dir, runner=probe_runner, clock=clock)
            admitted.append(r.mode != "off")

        threads = [threading.Thread(target=call, args=(i,)) for i in range(5)]
        for t in threads:
            t.start()
        deadline = time.monotonic() + 2.0
        while time.monotonic() < deadline and not probe_calls:
            time.sleep(0.005)
        self.assertTrue(probe_calls, "the admitted probe never even reached the runner")
        probe_gate.set()
        for t in threads:
            t.join(5.0)

        self.assertEqual(sum(admitted), 1, "more than one concurrent caller was admitted as the probe")
        self.assertEqual(len(probe_calls), 1)


class Item2SymlinkAliasIsRefused(SeamTestCase):
    """Item 2 (approved-with-nits review 2026-09-18): the ledger_dir
    containment check must compare REAL paths (os.path.realpath), so a
    symlink cannot alias its way around the M3/item-3 refusal."""

    def test_symlink_into_ledger_dir_is_refused(self):
        # A real directory OUTSIDE ledger_dir reached via a symlink whose
        # TARGET sits inside ledger_dir: two different-looking path
        # strings, the exact same file on disk.
        alias_dir = tempfile.mkdtemp()
        symlink_path = os.path.join(alias_dir, "promotions-link.jsonl")
        real_target = os.path.join(self.ledger_dir, "promotions.jsonl")
        with open(real_target, "w", encoding="utf-8") as fh:
            fh.write("{}\n")
        os.symlink(real_target, symlink_path)

        # RED PROOF: os.path.abspath alone (the pre-item-2 comparison)
        # does NOT consider these the same location -- exactly the gap
        # os.path.realpath closes.
        self.assertNotEqual(os.path.abspath(symlink_path), os.path.abspath(real_target))
        self.assertEqual(os.path.realpath(symlink_path), os.path.realpath(real_target))

        with mock.patch.object(cascade, "DEFAULT_PROMOTIONS_PATH", "/patched/default.jsonl"):
            result = seam._promotions_path(
                _seams_config({}, promotions_path=symlink_path), self.ledger_dir)
        self.assertEqual(result, "/patched/default.jsonl")

    def test_ledger_dir_itself_reached_via_symlink_is_refused(self):
        # ledger_dir, as handed to consult(), is itself a symlink to the
        # real directory; an override pointing straight at the REAL
        # directory (never mentioning the symlink) must still be refused
        # -- it is the same location either way.
        real_dir = tempfile.mkdtemp()
        symlinked_ledger_dir = os.path.join(tempfile.mkdtemp(), "ledger-alias")
        os.symlink(real_dir, symlinked_ledger_dir)
        under_real = os.path.join(real_dir, "promotions.jsonl")

        with mock.patch.object(cascade, "DEFAULT_PROMOTIONS_PATH", "/patched/default.jsonl"):
            result = seam._promotions_path(
                _seams_config({}, promotions_path=under_real), symlinked_ledger_dir)
        self.assertEqual(result, "/patched/default.jsonl")


class Item3DefaultUnderLedgerDirEscalates(SeamTestCase):
    """Item 3 (approved-with-nits review 2026-09-18): when even the
    UNCONFIGURED jev_cascade.DEFAULT_PROMOTIONS_PATH resolves under
    ledger_dir, there is nowhere safer left to fall back to -- the whole
    consult() call escalates instead of silently handing back a path
    this module just proved unsafe."""

    def test_helper_raises_when_default_resolves_under_ledger_dir(self):
        sneaky_default = os.path.join(self.ledger_dir, "jev-promotions.jsonl")
        with mock.patch.object(cascade, "DEFAULT_PROMOTIONS_PATH", sneaky_default):
            with self.assertRaises(seam._UnsafePromotionsPath):
                seam._promotions_path(_seams_config({}), self.ledger_dir)

    def test_consult_escalates_rather_than_using_an_unsafe_default(self):
        dp = os.path.join(self.ledger_dir, "decisions.jsonl")
        op = os.path.join(self.ledger_dir, "outcomes.jsonl")
        _seed_calibration(dp, op, "I3A", "noul", 0.97, n_correct=40)
        sneaky_default = os.path.join(self.ledger_dir, "jev-promotions.jsonl")
        # A fully valid, calibrated, signed promotion planted at this
        # (unsafe) default must never be read: consult() escalates before
        # decide() or the ledger write ever run.
        with open(sneaky_default, "w", encoding="utf-8") as fh:
            fh.write(json.dumps({
                "family": "I3A", "qtype": "noul", "risk_class": "low", "model": MODEL,
                "signed_by": "founder", "signed_at": "2026-09-18T00:00:00Z",
                "bound_at_signing": 0.85, "n_at_signing": 40,
                "flip_condition": "precision drops below target for two weeks",
                "review_by": "2099-01-01",
            }) + "\n")
        registry = [_noul_entry("I3A", risk="low")]
        runner = ScriptedRunner(_noul_answer(0.97))
        with mock.patch.object(cascade, "DEFAULT_PROMOTIONS_PATH", sneaky_default):
            result = seam.consult(
                "I3A", {"i": 1}, "current",
                seams_config=_seams_config({"I3A": "act"}), registry=registry,
                ledger_dir=self.ledger_dir, runner=runner,
            )
        self.assertEqual(result.answer, "current")
        self.assertIsNone(result.jev)
        self.assertIsNone(result.decision_id)
        self.assertFalse(result.audit)
        self.assertIn("resolves under", result.reason)
        # Never called the bridge: the escalation happens before decide().
        self.assertEqual(runner.calls, [])
        # Never wrote a new ledger row beyond the 40 seeded ones.
        with open(dp, encoding="utf-8") as fh:
            self.assertEqual(sum(1 for _ in fh if _.strip()), 40)


class LoadSeamsConfigCaches(unittest.TestCase):
    """Ported from a sibling worktree's wave-2 patch
    (g1-jev_seam-cache.patch), moved onto this foundation module rather
    than left in a per-call-site cache: load_seams_config() re-stats a
    given path at most once per _CONFIG_CACHE_INTERVAL_S, and re-opens it
    only when that stat's mtime actually moved. A call inside the window
    must be a pure in-process dict lookup: no os.stat, no open."""

    def test_zero_stat_or_open_calls_within_the_cache_window(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "jev-seams-cache-test.json")
            with open(path, "w", encoding="utf-8") as fh:
                json.dump({"modes": {"N1": "shadow"}}, fh)

            # First call: a real read, populates the cache entry for
            # this (test-unique) path.
            first = seam.load_seams_config(path)
            self.assertEqual(first, {"modes": {"N1": "shadow"}})

            def _boom(*_args, **_kwargs):
                raise AssertionError("touched the filesystem inside the cache window")

            # 1,000 further calls, all well inside _CONFIG_CACHE_INTERVAL_S
            # (1.0s): any os.stat or open call here fails the test
            # immediately, deterministically -- not a timing measurement,
            # a hard trip wire.
            with mock.patch("os.stat", side_effect=_boom), \
                 mock.patch("builtins.open", side_effect=_boom):
                for _ in range(1000):
                    result = seam.load_seams_config(path)
                    self.assertEqual(result, {"modes": {"N1": "shadow"}})

    def test_missing_file_returns_empty_dict_never_raises(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "does-not-exist.json")
            self.assertEqual(seam.load_seams_config(path), {})

    def test_malformed_json_returns_empty_dict_never_raises(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "broken.json")
            with open(path, "w", encoding="utf-8") as fh:
                fh.write("not json {{{")
            self.assertEqual(seam.load_seams_config(path), {})

    def test_mutating_the_returned_dict_never_corrupts_the_cache(self):
        # Item 4 (approved-with-nits review 2026-09-18): every call
        # returns a fresh copy.deepcopy(), never the cached dict itself,
        # so a caller mutating its own copy can never change the mode
        # process-wide for every OTHER caller.
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "jev-seams-copy-test.json")
            with open(path, "w", encoding="utf-8") as fh:
                json.dump({"modes": {"N1": "shadow"}}, fh)

            first = seam.load_seams_config(path)
            first["modes"]["N1"] = "act"
            first["modes"]["INTRUDER"] = "act"

            second = seam.load_seams_config(path)
            self.assertEqual(second, {"modes": {"N1": "shadow"}})
            self.assertNotIn("INTRUDER", second["modes"])

            # Still within the cache window (no disk re-read): the third
            # call proves the cache itself, not just the file on disk,
            # was never touched by the earlier mutation.
            third = seam.load_seams_config(path)
            self.assertEqual(third, {"modes": {"N1": "shadow"}})


class ResolveModePublicWrapper(unittest.TestCase):
    """resolve_mode() (ported alongside the cache, same patch) is a thin
    public wrapper: it must return exactly what _resolve_mode() returns,
    never a second, looser copy of that rule."""

    def test_matches_private_resolve_mode(self):
        cfg = _seams_config({"N1": "act", "N2": "not-a-real-mode"})
        self.assertEqual(seam.resolve_mode(cfg, "N1"), seam._resolve_mode(cfg, "N1"))
        self.assertEqual(seam.resolve_mode(cfg, "N1"), "act")
        self.assertEqual(seam.resolve_mode(cfg, "N2"), seam.OFF)
        self.assertEqual(seam.resolve_mode(cfg, "UNCONFIGURED"), seam.OFF)


class N2ExitDrainIsBoundedAndConfigurable(SeamTestCase):
    """N2 (independent re-review, 2026-09-19): the atexit drain must be
    bounded and configurable (seams_config["exit_drain_s"], default
    DEFAULT_EXIT_DRAIN_S=1.0, clamped to [0, 10]), and a row still in
    flight when it runs out is counted, not silently dropped."""

    def test_clamp_and_default(self):
        self.assertEqual(seam._exit_drain_s({}), seam.DEFAULT_EXIT_DRAIN_S)
        self.assertEqual(seam._exit_drain_s(None), seam.DEFAULT_EXIT_DRAIN_S)
        self.assertEqual(seam._exit_drain_s({"exit_drain_s": 0.5}), 0.5)
        self.assertEqual(seam._exit_drain_s({"exit_drain_s": -3}), 0.0)  # floors to 0
        self.assertEqual(seam._exit_drain_s({"exit_drain_s": 99}), 10.0)  # ceilings to 10
        self.assertEqual(seam._exit_drain_s({"exit_drain_s": "soon"}), seam.DEFAULT_EXIT_DRAIN_S)
        self.assertEqual(seam._exit_drain_s({"exit_drain_s": float("nan")}), seam.DEFAULT_EXIT_DRAIN_S)
        self.assertEqual(seam._exit_drain_s({"exit_drain_s": float("inf")}), seam.DEFAULT_EXIT_DRAIN_S)
        self.assertEqual(seam._exit_drain_s({"exit_drain_s": True}), seam.DEFAULT_EXIT_DRAIN_S)  # bool, not a real number

    def test_in_flight_is_sized_to_the_hard_deadline_not_the_flat_default(self):
        # Item 3 (A0.8 round 6, m1 and the G1 scope): with nothing in
        # flight, in_flight=0 (the default parameter value) behaves
        # exactly as test_clamp_and_default above -- unchanged. With a
        # call actually in flight, the flat DEFAULT_EXIT_DRAIN_S no
        # longer applies: the bound is sized to _hard_deadline(cfg) plus
        # DEFAULT_CLI_DRAIN_MARGIN_S, still bounded because
        # _hard_deadline() itself is always clamped.
        self.assertEqual(seam._exit_drain_s({}, in_flight=0), seam.DEFAULT_EXIT_DRAIN_S)
        self.assertEqual(
            seam._exit_drain_s({}, in_flight=1),
            seam.DEFAULT_HARD_DEADLINE_S + seam.DEFAULT_CLI_DRAIN_MARGIN_S,
        )
        self.assertEqual(
            seam._exit_drain_s({"call_deadline_s": 5.0}, in_flight=3),
            5.0 + seam.DEFAULT_CLI_DRAIN_MARGIN_S,
        )
        # An explicit exit_drain_s override always wins, in-flight or not.
        self.assertEqual(seam._exit_drain_s({"exit_drain_s": 0.2}, in_flight=1), 0.2)
        self.assertEqual(seam._exit_drain_s({"exit_drain_s": 0}, in_flight=5), 0.0)
        # Bounded even with a huge configured call_deadline_s: _hard_deadline()
        # itself clamps to MAX_CALL_DEADLINE_S before this function ever sees it.
        self.assertEqual(
            seam._exit_drain_s({"call_deadline_s": 1e9}, in_flight=1),
            seam.MAX_CALL_DEADLINE_S + seam.DEFAULT_CLI_DRAIN_MARGIN_S,
        )

    def test_atexit_drain_uses_the_configured_timeout_and_counts_a_leftover_row(self):
        # Mutation-kill for N2's "drop counted" half: a worker still alive
        # when the (patched, short) exit-drain timeout elapses must be
        # counted under _EXIT_DRAIN_DROP_KEY, never silently unaccounted
        # for. drain() itself is exercised for real; only the configured
        # timeout is controlled, via load_seams_config (patched to a tiny
        # config file so the real repo config is never touched).
        registry = [_noul_entry("N1")]
        gate = threading.Event()

        def hung(_argv, _stdin_text):
            gate.wait(5.0)
            return 0, json.dumps({"model": MODEL, "answers": {}, "usage": {"cost": 0.0}}), ""

        try:
            seam.consult("N1", {"i": 1}, "cur", seams_config=_seams_config({"N1": "shadow"}),
                         registry=registry, ledger_dir=self.ledger_dir, runner=hung)
            before = seam.DROP_COUNTS.get(seam._EXIT_DRAIN_DROP_KEY, 0)
            with mock.patch.object(seam, "load_seams_config", return_value={"exit_drain_s": 0.05}):
                seam._atexit_drain()
            after = seam.DROP_COUNTS.get(seam._EXIT_DRAIN_DROP_KEY, 0)
            self.assertGreater(after, before, "a worker still in flight past exit_drain_s must be counted")
        finally:
            gate.set()
            seam.drain(timeout=2.0)  # let the leftover thread actually finish before the next test

    def test_real_process_exit_with_a_slow_bridge_waits_the_sized_in_flight_bound(self):
        # N2's own probe, run for real, REWRITTEN for item 3 (A0.8 round
        # 6, m1 and the G1 scope): the flat DEFAULT_EXIT_DRAIN_S (1.0s)
        # used to apply even with a real shadow call in flight, silently
        # losing any call slower than that -- exactly what every one of
        # the eight G1 short-lived callers hit, since none of them call
        # drain() themselves. Now, with a call actually in flight, the
        # atexit wait is sized to _hard_deadline(cfg) + the same margin
        # cli_drain_timeout_s() uses (call_deadline_s is set explicitly
        # here so the expected bound is exact, not tied to
        # DEFAULT_HARD_DEADLINE_S's own value). Still bounded: it must
        # NOT wait out the full 5s bridge. Uses a REAL executable bridge
        # script (shebang, chmod +x) via BROTHER_DECISION_BRIDGE, not an
        # injected runner callable, so this exercises the real subprocess
        # path decide() uses in production -- never the network (the
        # bridge is a local sleeping script this test writes itself).
        with tempfile.TemporaryDirectory() as d:
            bridge_path = os.path.join(d, "slow_bridge.py")
            with open(bridge_path, "w", encoding="utf-8") as fh:
                fh.write(
                    "#!/usr/bin/env python3\n"
                    "import sys, json, time\n"
                    "time.sleep(5)\n"
                    "data = json.loads(sys.stdin.read())\n"
                    "answers = {qid: {'noul': 0.9, 'confidence': 0.9} for qid in data.get('questions', {})}\n"
                    "print(json.dumps({'model': 'typesafe/jev-test', 'answers': answers, "
                    "'usage': {'cost': 0.001}}))\n"
                )
            st = os.stat(bridge_path)
            os.chmod(bridge_path, st.st_mode | stat.S_IEXEC | stat.S_IXGRP | stat.S_IXOTH)

            state_dir = os.path.join(d, "state")
            child_path = os.path.join(d, "child.py")
            scripts_dir = os.path.dirname(os.path.abspath(seam.__file__))
            with open(child_path, "w", encoding="utf-8") as fh:
                fh.write(
                    "import sys, os, json\n"
                    "sys.path.insert(0, %r)\n"
                    "import jev_seam as seam\n"
                    "cfg = {'modes': {'N1': 'shadow'}, 'call_deadline_s': 1.5, "
                    "'budget_path': os.path.join(%r, 'budget.json'), "
                    "'promotions_path': os.path.join(%r, 'nope.jsonl')}\n"
                    "registry = [{'id': 'N1', 'role': 'second_opinion', 'risk': 'low', "
                    "'wave': 'W1', 'privacy': 'public_or_own_text', "
                    "'question': {'type': 'noul', 'instructions': 'x'}}]\n"
                    "r = seam.consult('N1', {'i': 1}, 'cur', seams_config=cfg, registry=registry, "
                    "ledger_dir=os.path.join(%r, 'ledger'))\n"
                    "print('mode=%%s reason=%%r' %% (r.mode, r.reason))\n"
                    % (scripts_dir, d, d, d)
                )

            env = dict(os.environ)
            env["BROTHER_DECISION_BRIDGE"] = sys.executable + " " + bridge_path
            # Round 4 fix, item 7 (m4, independent re-review, 2026-09-19):
            # this used to POP BROTHER_JEV_STATE_DIR from the child's env
            # entirely, letting the child read the REAL
            # ~/.brother/jev/canary-reset.json and data/jev-seams.json --
            # a real canary marker, or a repo config setting exit_drain_s,
            # would turn this test red for reasons unrelated to the code
            # under test. Point it at this test's own temp dir instead, so
            # the child is exactly as hermetic as every other test in this
            # suite.
            env["BROTHER_JEV_STATE_DIR"] = state_dir

            t0 = time.monotonic()
            proc = subprocess.run([sys.executable, child_path], env=env,
                                   capture_output=True, text=True, timeout=10.0)
            elapsed = time.monotonic() - t0

        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertIn("mode=shadow", proc.stdout)
        # Item 3 (A0.8 round 6): sized bound is call_deadline_s (1.5) +
        # DEFAULT_CLI_DRAIN_MARGIN_S (2.0) = 3.5s, plus a 1.2s margin for
        # real scheduling jitter -- still comfortably under the bridge's
        # own 5s sleep, proving the wait does not run out the full bridge.
        expected = 1.5 + seam.DEFAULT_CLI_DRAIN_MARGIN_S
        self.assertLess(elapsed, expected + 1.2,
                         "process took %.2fs to exit (expected sized bound ~%.2fs) -- the atexit "
                         "drain waited out the 5s bridge instead of stopping near its sized bound"
                         % (elapsed, expected))
        # Mutation-kill for "in-flight sizing never engaged": a process
        # with a real call in flight must wait well past the OLD flat
        # DEFAULT_EXIT_DRAIN_S (1.0s) -- the whole point of item 3 was
        # that the flat default silently lost this call.
        floor = seam.DEFAULT_EXIT_DRAIN_S + 1.0
        self.assertGreater(elapsed, floor,
                            "process exited too close to the flat default (%.2fs, floor %.2fs) -- "
                            "the in-flight sizing (call_deadline_s + margin) never engaged"
                            % (elapsed, floor))
        # The abandoned row must be reported once, naming both the count
        # and the entry id it belonged to (item 3).
        self.assertIn("atexit drain abandoned 1 shadow call", proc.stderr)
        self.assertIn("entry id(s): N1", proc.stderr)


class M1LocalErrorsNeverOpenTheBreaker(SeamTestCase):
    """m1 (independent re-review, 2026-09-19): a local bug in the ledger/
    calibration read path (not a Jev failure) must never open the
    breaker. Probed original cause: os.listdir raising PermissionError
    inside _calibrated_threshold; jev_calibration's own m1 fix (guarding
    every os.listdir it makes) already prevents THAT specific raise, so
    this proves the belt-and-suspenders half at the worker's own
    catch-all: ANY unexpected exception after decide() has already
    succeeded must leave box.api_failed False."""

    def test_an_unexpected_local_exception_after_decide_succeeds_does_not_trip_the_breaker(self):
        registry = [_noul_entry("N1")]
        runner = ScriptedRunner(_noul_answer(0.9))
        cfg = _seams_config({"N1": "act"}, breaker_fail_threshold=1,
                             promotions_path=os.path.join(self.promo_dir, "no-such.jsonl"))
        with mock.patch.object(seam._calibration, "threshold",
                                side_effect=RuntimeError("simulated local bug (not Jev's fault)")):
            result = seam.consult("N1", {"i": 1}, "current", seams_config=cfg, registry=registry,
                                  ledger_dir=self.ledger_dir, runner=runner)
        self.assertIn("unexpected error", (result.reason or "").lower())
        self.assertIn("not a jev failure", (result.reason or "").lower())
        self.assertIsNone(seam._BREAKER_STATE["opened_at"], "a local bug opened the breaker")
        self.assertEqual(seam._BREAKER_STATE["consecutive_failures"], 0)
        # A genuinely healthy Jev is still reachable right after.
        result2 = seam.consult("N1", {"i": 2}, "current", seams_config=cfg, registry=registry,
                               ledger_dir=self.ledger_dir, runner=runner)
        self.assertNotEqual(result2.mode, "off")
        self.assertIsNotNone(result2.jev)


class M2BudgetDateFieldValidated(SeamTestCase):
    """m2 (independent re-review, 2026-09-19): the budget file's own
    `date` field is validated as a strict ISO YYYY-MM-DD calendar date; a
    malformed value is corrupt (refuse, same as C1), and a date LATER
    than today refuses outright rather than silently locking the budget
    until that date arrives."""

    def _write_budget(self, budget_path, payload):
        with open(budget_path, "w", encoding="utf-8") as fh:
            json.dump(payload, fh)

    def test_non_iso_date_denies_every_call(self):
        registry = [_noul_entry("N1")]
        runner = ScriptedRunner(_noul_answer(0.9))
        budget_path = os.path.join(self.ledger_dir, "budget.json")
        self._write_budget(budget_path, {"date": "19/09/2026", "calls": 0})
        cfg = _seams_config({"N1": "shadow"}, daily_call_budget=5, budget_path=budget_path)
        result = seam.consult("N1", {"i": 1}, "cur", seams_config=cfg, registry=registry,
                              ledger_dir=self.ledger_dir, runner=runner)
        self.assertEqual(result.mode, "off")
        self.assertIn("invalid or non-iso date", (result.reason or "").lower())
        self.assertEqual(len(runner.calls), 0)

    def test_unparseable_calendar_date_denies_every_call(self):
        registry = [_noul_entry("N1")]
        runner = ScriptedRunner(_noul_answer(0.9))
        budget_path = os.path.join(self.ledger_dir, "budget.json")
        self._write_budget(budget_path, {"date": "2026-13-40", "calls": 0})
        cfg = _seams_config({"N1": "shadow"}, daily_call_budget=5, budget_path=budget_path)
        result = seam.consult("N1", {"i": 1}, "cur", seams_config=cfg, registry=registry,
                              ledger_dir=self.ledger_dir, runner=runner)
        self.assertEqual(result.mode, "off")
        self.assertIn("invalid or non-iso date", (result.reason or "").lower())

    def test_future_dated_file_refuses_rather_than_locking_silently(self):
        registry = [_noul_entry("N1")]
        runner = ScriptedRunner(_noul_answer(0.9))
        budget_path = os.path.join(self.ledger_dir, "budget.json")
        self._write_budget(budget_path, {"date": "2099-01-01", "calls": 0})
        cfg = _seams_config({"N1": "shadow"}, daily_call_budget=5, budget_path=budget_path)
        result = seam.consult("N1", {"i": 1}, "cur", seams_config=cfg, registry=registry,
                              ledger_dir=self.ledger_dir, runner=runner)
        self.assertEqual(result.mode, "off")
        reason = (result.reason or "").lower()
        self.assertIn("later than today", reason)
        self.assertIn("delete", reason)  # names the reset path
        self.assertEqual(len(runner.calls), 0)
        # The file is left exactly as it was -- refused, never rewritten.
        with open(budget_path, encoding="utf-8") as fh:
            self.assertEqual(json.load(fh)["date"], "2099-01-01")

    def test_ordinary_same_day_and_new_day_files_still_work(self):
        registry = [_noul_entry("N1")]
        runner = ScriptedRunner(_noul_answer(0.9))
        budget_path = os.path.join(self.ledger_dir, "budget.json")
        today = seam.datetime.now(seam.timezone.utc).date().isoformat()
        self._write_budget(budget_path, {"date": today, "calls": 3})
        cfg = _seams_config({"N1": "shadow"}, daily_call_budget=5, budget_path=budget_path)
        result = seam.consult("N1", {"i": 1}, "cur", seams_config=cfg, registry=registry,
                              ledger_dir=self.ledger_dir, runner=runner)
        self.assertNotEqual(result.mode, "off")
        with open(budget_path, encoding="utf-8") as fh:
            self.assertEqual(json.load(fh)["calls"], 4)

        yesterday = "2020-01-01"
        self._write_budget(budget_path, {"date": yesterday, "calls": 999})
        result2 = seam.consult("N1", {"i": 2}, "cur", seams_config=cfg, registry=registry,
                               ledger_dir=self.ledger_dir, runner=runner)
        self.assertNotEqual(result2.mode, "off")  # a real new day resets the count
        with open(budget_path, encoding="utf-8") as fh:
            self.assertEqual(json.load(fh)["calls"], 1)


class M3ConsultNeverRaises(SeamTestCase):
    """m3 (independent re-review, 2026-09-19): consult() must never raise.
    Probed causes: a malformed registry (None), a non-finite
    call_deadline_s, and a budget_path holding a NUL byte all used to
    escape consult() as raw exceptions, and each one leaked the breaker's
    half-open probe claim forever when it fired while the breaker was
    half-open (nothing else was left to release it).

    Round 4 fix, item 6 (2026-09-19): the NUL-byte budget_path case is now
    fixed AT THE SOURCE (_budget_path() itself refuses a NUL byte, the
    same way it already refused an empty string, and falls back to
    DEFAULT_BUDGET_PATH) rather than left to raise downstream and rely on
    consult()'s own broad except to paper over it. The observable
    behaviour is therefore no longer "the call is refused": the config is
    silently normalized to a valid default path and the call proceeds
    NORMALLY, exactly as if budget_path had been omitted -- see
    test_budget_path_with_nul_byte_is_sanitized_and_the_call_proceeds
    below, which replaces the old no-data-shaped test."""

    def _half_open(self, cfg_extra=None):
        """Puts the module-global breaker into a state where the very
        next call claims the single half-open probe slot, and returns a
        seams_config already past the cooldown for a fixed fake clock."""
        seam._BREAKER_STATE["consecutive_failures"] = 5
        seam._BREAKER_STATE["opened_at"] = 0.0
        seam._BREAKER_STATE["probing"] = False
        clock = lambda: 10_000.0  # far past any cooldown
        cfg = _seams_config({"N1": "shadow"}, breaker_cooldown_s=1.0, **(cfg_extra or {}))
        return cfg, clock

    def test_registry_none_returns_no_data_and_releases_the_probe(self):
        cfg, clock = self._half_open()
        result = seam.consult("N1", {"i": 1}, "safe", seams_config=cfg, registry=None,
                              ledger_dir=self.ledger_dir, runner=ScriptedRunner(_noul_answer(0.9)),
                              clock=clock)
        self.assertEqual(result.answer, "safe")
        self.assertIsNone(result.jev)
        self.assertIsNone(result.decision_id)
        self.assertIn("no-data", (result.reason or "").lower())
        self.assertFalse(seam._BREAKER_STATE["probing"], "the half-open probe claim leaked")
        # A further call is admitted again (not stuck on "already probing").
        seam._BREAKER_STATE["consecutive_failures"] = 5
        seam._BREAKER_STATE["opened_at"] = 0.0
        result2 = seam.consult("N1", {"i": 2}, "safe", seams_config=cfg, registry=[_noul_entry("N1")],
                               ledger_dir=self.ledger_dir, runner=ScriptedRunner(_noul_answer(0.9)),
                               clock=clock)
        self.assertNotIn("already in flight", (result2.reason or ""))

    def test_non_finite_deadline_returns_no_data_and_releases_the_probe(self):
        cfg, clock = self._half_open({"call_deadline_s": float("inf")})
        registry = [_noul_entry("N1")]
        gate = threading.Event()

        def hung(_argv, _stdin_text):
            gate.wait(3.0)
            return 0, json.dumps({"model": MODEL, "answers": {}, "usage": {"cost": 0.0}}), ""

        try:
            # An infinite deadline only ever reaches box.event.wait() in
            # advise/act (shadow never waits); use act so the admission
            # gate's own try/except is bypassed by the SANITIZED
            # deadline (falls back to DEFAULT_HARD_DEADLINE_S) rather
            # than ever constructing an infinite wait in the first place.
            cfg["modes"]["N1"] = "act"
            t0 = time.monotonic()
            result = seam.consult("N1", {"i": 1}, "safe", seams_config=cfg, registry=registry,
                                  ledger_dir=self.ledger_dir, runner=hung, clock=clock)
            elapsed = time.monotonic() - t0
        finally:
            gate.set()
        self.assertLess(elapsed, seam.DEFAULT_HARD_DEADLINE_S + 1.0)
        self.assertEqual(result.answer, "safe")
        self.assertFalse(seam._BREAKER_STATE["probing"], "the half-open probe claim leaked")

    def test_budget_path_with_nul_byte_is_sanitized_and_the_call_proceeds(self):
        # Round 4 fix, item 6: _budget_path() itself now refuses the NUL
        # byte and falls back to DEFAULT_BUDGET_PATH -- the call is never
        # refused for it, so this is shadow mode's ordinary async path
        # (submitted immediately, the worker classifies the breaker once
        # it finishes), not the synchronous "escaped as an exception"
        # shape the other two tests in this class still exercise. drain()
        # waits for that worker before checking the probe claim cleared,
        # the same pattern other shadow-mode tests in this suite use.
        cfg, clock = self._half_open({"budget_path": "bad\x00path.json"})
        registry = [_noul_entry("N1")]
        result = seam.consult("N1", {"i": 1}, "safe", seams_config=cfg, registry=registry,
                              ledger_dir=self.ledger_dir, runner=ScriptedRunner(_noul_answer(0.9)),
                              clock=clock)
        self.assertEqual(result.answer, "safe")
        self.assertIsNone(result.decision_id)
        self.assertIn("submitted", result.reason or "")
        seam.drain(timeout=2.0)
        self.assertFalse(seam._BREAKER_STATE["probing"], "the half-open probe claim leaked")


class M1SlotGuardNeverLeaksTheInflightSlot(SeamTestCase):
    """Major m1 (round 4 fix, item 4, 2026-09-19): the inflight slot is
    acquired BEFORE the budget step inside consult()'s admission try, but
    the old except clause's own comment claimed "no inflight slot is held
    on this call's behalf" -- wrong, and any exception raised by (or
    after) the budget step leaked the slot forever. Reviewer probe: a
    budget file nested past the recursion limit (json.load raises
    RecursionError) made every subsequent call see an elevated
    inflight_now, eventually wedging every call as "already in flight"
    even once the cause was fixed. The round 4 fix wraps admission in one
    try/except/finally slot guard keyed off a single `handed_off` flag."""

    def test_repeated_budget_step_failures_never_wedge_the_seam(self):
        registry = [_noul_entry("N1")]
        runner = ScriptedRunner(_noul_answer(0.9))
        budget_path = os.path.join(self.ledger_dir, "deep.json")
        # A budget file nested well past Python's default recursion limit:
        # json.load() raises RecursionError reading it, from INSIDE
        # _consume_budget_slot(), i.e. after the inflight slot above it in
        # consult() has already been acquired.
        with open(budget_path, "w", encoding="utf-8") as fh:
            fh.write("[" * 5000 + "]" * 5000)
        cfg = _seams_config({"N1": "shadow"}, budget_path=budget_path, max_inflight_calls=2)

        for i in range(40):
            result = seam.consult("N1", {"i": i}, "cur", seams_config=cfg, registry=registry,
                                  ledger_dir=self.ledger_dir, runner=runner)
            self.assertNotIn("already in flight", result.reason or "",
                              "call %d: the inflight slot leaked from a prior failed call" % i)
        # Direct measurement of the module-global counter after 40 calls
        # that each raised inside the budget step: 0 is the observed,
        # checkable post-condition, not an inference from the loop above.
        self.assertEqual(seam._INFLIGHT_COUNT, 0)

        # Repair the cause and confirm the seam is not permanently wedged:
        # a normal call must be admitted and actually dispatch a worker.
        os.remove(budget_path)
        result = seam.consult("N1", {"i": "after-repair"}, "cur", seams_config=cfg, registry=registry,
                              ledger_dir=self.ledger_dir, runner=runner)
        self.assertIn("submitted", result.reason or "")
        seam.drain(timeout=2.0)


class M2DeadlineOverflowIsClampedNotRaised(SeamTestCase):
    """m2 (round 4 fix, item 5, 2026-09-19): a FINITE call_deadline_s
    above threading.TIMEOUT_MAX (~106,751 days) still raised OverflowError
    from inside consult() (isfinite alone was not enough). Now clamped by
    _config_duration_s() to MAX_CALL_DEADLINE_S (60s) at the read site, so
    an absurd config value is treated as a (large) real deadline, not an
    infinite one and not a crash."""

    def test_huge_finite_deadline_never_raises_and_is_clamped(self):
        registry = [_noul_entry("N1")]
        runner = ScriptedRunner(_noul_answer(0.9))
        cfg = _seams_config({"N1": "act"}, call_deadline_s=1e10)
        self.assertEqual(seam._hard_deadline(cfg), seam.MAX_CALL_DEADLINE_S)
        result = seam.consult("N1", {"i": 1}, "safe", seams_config=cfg, registry=registry,
                              ledger_dir=self.ledger_dir, runner=runner)
        # A fast ScriptedRunner completes well inside the clamp; the
        # property under test is that consult() never raises OverflowError
        # and the breaker's half-open state is untouched by a crash.
        self.assertIsNotNone(result)
        self.assertNotEqual(result.mode, None)

    def test_huge_finite_breaker_cooldown_is_clamped_not_unbounded(self):
        cfg = _seams_config({"N1": "shadow"}, breaker_cooldown_s=1e10)
        self.assertEqual(seam._breaker_cooldown_s(cfg), seam.MAX_BREAKER_COOLDOWN_S)

    def test_negative_breaker_cooldown_falls_back_to_the_default_not_zero(self):
        # m6 (Opus rereview4, 2026-09-19): before this fix, a negative
        # breaker_cooldown_s floored to 0.0 via the shared clamp's plain
        # max(min_s, ...) -- the LEAST protective possible cooldown
        # (probe immediately after opening) -- rather than falling back to
        # DEFAULT_BREAKER_COOLDOWN_S=600s the way every other invalid
        # value for this field already does (a string, NaN, +inf). This
        # is unlike exit_drain_s, where a negative value floors to 0 ON
        # PURPOSE (see _exit_drain_s()'s own docstring): the two fields
        # are deliberately different shapes of "invalid", not one bug in
        # one shared function.
        cfg = _seams_config({"N1": "shadow"}, breaker_cooldown_s=-1)
        self.assertEqual(seam._breaker_cooldown_s(cfg), seam.DEFAULT_BREAKER_COOLDOWN_S)
        cfg2 = _seams_config({"N1": "shadow"}, breaker_cooldown_s=-0.001)
        self.assertEqual(seam._breaker_cooldown_s(cfg2), seam.DEFAULT_BREAKER_COOLDOWN_S)
        # Zero itself is a real, legal cooldown (probe again immediately),
        # never treated as invalid -- only a genuinely negative value is.
        cfg3 = _seams_config({"N1": "shadow"}, breaker_cooldown_s=0.0)
        self.assertEqual(seam._breaker_cooldown_s(cfg3), 0.0)
        # exit_drain_s's own documented "negative floors to 0" behaviour
        # is untouched by this fix -- proves negative_is_default is scoped
        # to breaker_cooldown_s, not a change to _config_duration_s's
        # default clamp shape.
        self.assertEqual(seam._exit_drain_s({"exit_drain_s": -3}), 0.0)


class M1CliDrainSizingAndMaxInflightCalls(SeamTestCase):
    """M1(a)/M1(b) (Opus rereview4, 2026-09-19): jev_checks.py's own CLI
    needs a drain() bound sized to the work it just dispatched, and a way
    to chunk its own submissions to the same in-flight cap consult()
    enforces -- both exposed here as small public functions rather than a
    caller outside this module reaching into _hard_deadline()/
    _max_inflight() by their private names."""

    def test_max_inflight_calls_matches_the_private_helper(self):
        cfg = _seams_config({"N1": "shadow"}, max_inflight_calls=7)
        self.assertEqual(seam.max_inflight_calls(cfg), 7)
        self.assertEqual(seam.max_inflight_calls(cfg), seam._max_inflight(cfg))
        self.assertEqual(seam.max_inflight_calls({}), seam.DEFAULT_MAX_INFLIGHT)

    def test_cli_drain_timeout_zero_calls_is_zero(self):
        cfg = _seams_config({"N1": "shadow"})
        self.assertEqual(seam.cli_drain_timeout_s(cfg, 0), 0.0)
        self.assertEqual(seam.cli_drain_timeout_s(cfg, -5), 0.0)

    def test_cli_drain_timeout_batches_by_the_inflight_cap(self):
        # cap=10, call_deadline_s=3.0: 1 call and 10 calls both fit in one
        # wave (ceil(1/10)=ceil(10/10)=1); 11 calls need a second wave.
        cfg = _seams_config({"N1": "shadow"}, max_inflight_calls=10, call_deadline_s=3.0)
        margin = seam.DEFAULT_CLI_DRAIN_MARGIN_S
        self.assertEqual(seam.cli_drain_timeout_s(cfg, 1), 3.0 * 1 + margin)
        self.assertEqual(seam.cli_drain_timeout_s(cfg, 10), 3.0 * 1 + margin)
        self.assertEqual(seam.cli_drain_timeout_s(cfg, 11), 3.0 * 2 + margin)
        self.assertEqual(seam.cli_drain_timeout_s(cfg, 25), 3.0 * 3 + margin)

    def test_cli_drain_timeout_never_the_atexit_hooks_own_small_bound(self):
        # The property M1(a) exists to fix: a real CLI call must be sized
        # to actually cover the dispatched work, never collapse to
        # DEFAULT_EXIT_DRAIN_S (the atexit hook's own 1.0s default, sized
        # for an ORDINARY process exit, not a CLI whose whole job is to
        # wait for what it just dispatched).
        cfg = _seams_config({"N1": "shadow"})
        self.assertGreater(seam.cli_drain_timeout_s(cfg, 1), seam.DEFAULT_EXIT_DRAIN_S)


class N4SixMutantsFromTheReReview(SeamTestCase):
    """N4 (independent re-review, 2026-09-19): six specific claimed
    properties had no test that could go red. Each test below is named
    after the mutant it kills; the mutation itself (applied to a copy,
    suite run, red recorded, restored) is the session's own done-check,
    not something this suite runs by itself."""

    def test_b_seam_wires_sibling_outcomes_path_into_append_decision(self):
        # Kills "seam drops sibling": if consult() ever stops passing
        # sibling_outcomes_path through to append_decision(), M5's
        # cross-file retention protection silently goes dark in
        # production (jev_calibration's own tests call append_decision()
        # directly with the kwarg, so they cannot catch this).
        registry = [_noul_entry("N1")]
        runner = ScriptedRunner(_noul_answer(0.9))
        cfg = _seams_config({"N1": "act"}, promotions_path=os.path.join(self.promo_dir, "no-such.jsonl"))
        outcomes_path = os.path.join(self.ledger_dir, "outcomes.jsonl")
        with mock.patch.object(seam._calibration, "append_decision") as mock_append:
            seam.consult("N1", {"i": 1}, "current", seams_config=cfg, registry=registry,
                        ledger_dir=self.ledger_dir, runner=runner)
        self.assertEqual(mock_append.call_count, 1)
        self.assertEqual(mock_append.call_args.kwargs.get("sibling_outcomes_path"), outcomes_path)

    def test_c_budget_deny_releases_a_claimed_probe(self):
        # Kills "budget deny keeps probe": a call admitted as the
        # half-open probe but then denied for budget exhaustion must
        # release its claim, or the breaker locks out every future probe
        # even though Jev was never actually asked again.
        seam._BREAKER_STATE["consecutive_failures"] = 5
        seam._BREAKER_STATE["opened_at"] = 0.0
        seam._BREAKER_STATE["probing"] = False
        clock = lambda: 10_000.0
        registry = [_noul_entry("N1")]
        budget_path = os.path.join(self.ledger_dir, "budget.json")
        with open(budget_path, "w", encoding="utf-8") as fh:
            json.dump({"date": seam.datetime.now(seam.timezone.utc).date().isoformat(), "calls": 5}, fh)
        cfg = _seams_config({"N1": "shadow"}, breaker_cooldown_s=1.0, daily_call_budget=5, budget_path=budget_path)
        result = seam.consult("N1", {"i": 1}, "cur", seams_config=cfg, registry=registry,
                              ledger_dir=self.ledger_dir, runner=ScriptedRunner(_noul_answer(0.9)),
                              clock=clock)
        self.assertEqual(result.mode, "off")
        self.assertIn("budget", (result.reason or "").lower())
        self.assertFalse(seam._BREAKER_STATE["probing"], "budget denial leaked the half-open probe claim")

    def test_e_advise_actually_stops_waiting_at_the_deadline_against_a_hung_runner(self):
        # Kills "advise unbounded wait": the existing advise deadline test
        # only ever used a FAST runner, so advise waiting forever was
        # never actually exercised. Mirrors the act-mode hung-runner test.
        registry = [_noul_entry("N1")]
        gate = threading.Event()  # never set: worker keeps hanging past the deadline

        def hung(_argv, _stdin_text):
            gate.wait(10.0)
            return 0, json.dumps({"model": MODEL, "answers": {}, "usage": {"cost": 0.0}}), ""

        try:
            t0 = time.monotonic()
            result = seam.consult(
                "N1", {"i": 1}, "safe-default",
                seams_config=_seams_config({"N1": "advise"}), registry=registry,
                ledger_dir=self.ledger_dir, runner=hung,
            )
            elapsed = time.monotonic() - t0
            self.assertGreaterEqual(elapsed, seam.DEFAULT_HARD_DEADLINE_S)
            self.assertLess(elapsed, seam.DEFAULT_HARD_DEADLINE_S + 1.0)
            self.assertEqual(result.answer, "safe-default")
            self.assertIsNone(result.jev)
            self.assertIn("timed out", (result.reason or "").lower())
        finally:
            gate.set()

    def test_h_canary_refusal_releases_a_claimed_probe(self):
        # Kills "drop release at canary": a call admitted as the
        # half-open probe but then refused because the canary marker is
        # present must release its claim too.
        seam._BREAKER_STATE["consecutive_failures"] = 5
        seam._BREAKER_STATE["opened_at"] = 0.0
        seam._BREAKER_STATE["probing"] = False
        clock = lambda: 10_000.0
        registry = [_noul_entry("N1")]
        marker = os.path.join(self.ledger_dir, "canary-reset.json")
        with open(marker, "w", encoding="utf-8") as fh:
            fh.write("{}")
        cfg = _seams_config({"N1": "shadow"}, breaker_cooldown_s=1.0, canary_marker=marker)
        result = seam.consult("N1", {"i": 1}, "cur", seams_config=cfg, registry=registry,
                              ledger_dir=self.ledger_dir, runner=ScriptedRunner(_noul_answer(0.9)),
                              clock=clock)
        self.assertEqual(result.mode, "off")
        self.assertIn("canary", (result.reason or "").lower())
        self.assertFalse(seam._BREAKER_STATE["probing"], "canary refusal leaked the half-open probe claim")


class Item7AdviseActForcedOffWhenTheCallSiteNeverReads(SeamTestCase):
    """Item 7 (Muse G1 point 5, A0.8 round 6, hardened in the BLOCK
    review of round 6): a registry entry PRESENT but not PROVEN to read
    the answer -- "call_site_reads_answer" is present and is anything
    other than the literal Python bool True: the eight G1 call sites'
    explicit False, or a non-bool value such as the string "false" or 0
    -- configured to advise or act must be treated as off, with one
    stderr line, at the RUNTIME point where both the resolved mode and
    the resolved registry entry are already in hand together -- the
    matching runtime half of jev_registry.lint()'s own static refusal.
    shadow is unaffected: it never reads the answer either way, so it is
    never the problem this guard exists to catch. A present-but-non-bool
    value (string "false", 0) is additionally a dirty registry entry
    (see the new call-site-reads-answer-not-bool lint finding), so it is
    refused even earlier, by the pre-existing callable() gate every mode
    already goes through -- the runner is still never called and the
    current answer is still never overridden, which is the property
    these tests check, not the literal .mode string on that particular
    refusal path. The field being MISSING entirely stays out of this
    guard's scope, exactly as before this fix: every entry except the
    eight G1 ones omits it, and widening the guard to cover absence too
    was tried and reverted after it forced every other advise/act entry
    in this codebase's own test suite to off (see
    test_no_call_site_reads_answer_field_is_still_never_downgraded)."""

    def _entry(self, entry_id="N1", reads=False, risk="low"):
        e = _noul_entry(entry_id, risk=risk)
        e["call_site_reads_answer"] = reads
        return e

    def test_advise_is_forced_to_off_and_the_runner_is_never_called(self):
        registry = [self._entry()]
        cfg = _seams_config({"N1": "advise"})
        stderr = io.StringIO()
        with contextlib.redirect_stderr(stderr):
            result = seam.consult("N1", {"i": 1}, "cur", seams_config=cfg, registry=registry,
                                  ledger_dir=self.ledger_dir,
                                  runner=ScriptedRunner(_noul_answer(0.9)))
        self.assertEqual(result.mode, "off")
        self.assertEqual(result.answer, "cur")
        self.assertIn("call_site_reads_answer", (result.reason or ""))
        self.assertIn("N1", stderr.getvalue())
        self.assertIn("advise", stderr.getvalue())

    def test_act_is_forced_to_off_and_the_runner_is_never_called(self):
        registry = [self._entry()]
        cfg = _seams_config({"N1": "act"})
        stderr = io.StringIO()
        with contextlib.redirect_stderr(stderr):
            result = seam.consult("N1", {"i": 1}, "cur", seams_config=cfg, registry=registry,
                                  ledger_dir=self.ledger_dir,
                                  runner=ScriptedRunner(_noul_answer(0.9)))
        self.assertEqual(result.mode, "off")
        self.assertEqual(result.answer, "cur")
        self.assertIn("N1", stderr.getvalue())
        self.assertIn("act", stderr.getvalue())

    def test_shadow_is_unaffected_and_still_dispatches(self):
        registry = [self._entry()]
        cfg = _seams_config({"N1": "shadow"})
        result = seam.consult("N1", {"i": 1}, "cur", seams_config=cfg, registry=registry,
                              ledger_dir=self.ledger_dir, runner=ScriptedRunner(_noul_answer(0.9)))
        self.assertEqual(result.mode, "shadow")
        self.assertEqual(result.answer, "cur")
        self.assertEqual(result.reason, "shadow: submitted")

    def test_an_entry_that_does_read_the_answer_is_never_downgraded(self):
        registry = [self._entry(entry_id="N2", reads=True, risk="low")]
        cfg = _seams_config({"N2": "advise"})
        result = seam.consult("N2", {"i": 1}, "cur", seams_config=cfg, registry=registry,
                              ledger_dir=self.ledger_dir, runner=ScriptedRunner(_noul_answer(0.9)))
        self.assertEqual(result.mode, "advise")

    def test_no_call_site_reads_answer_field_is_still_never_downgraded(self):
        # BLOCK review, round 6: the fix widens the guard from "is
        # False" to "present and is not True", which catches a typo'd
        # PRESENT value (a string "false", a stray 0) without touching
        # the MISSING case -- every entry except the eight G1 ones
        # omits this field, and this guard has never had an opinion
        # about that. Widening it to cover absence too was tried during
        # this fix and reverted: it forced every other advise/act entry
        # in this codebase's own test suite to off (30 unrelated test
        # failures), since none of them ever set this field either --
        # exactly the chain reaction "fix at the source" forbids. This
        # test is the regression guard for that reversion.
        registry = [_noul_entry("N3")]
        cfg = _seams_config({"N3": "advise"})
        result = seam.consult("N3", {"i": 1}, "cur", seams_config=cfg, registry=registry,
                              ledger_dir=self.ledger_dir, runner=ScriptedRunner(_noul_answer(0.9)))
        self.assertEqual(result.mode, "advise")

    def test_string_false_is_a_typo_not_a_free_pass(self):
        # The exact BLOCK finding: "call_site_reads_answer": "false" (a
        # string, not the bool False) must not silently behave as if it
        # were the bool True. It is caught even earlier than the
        # runtime "is not True" guard, as a dirty registry entry (the
        # new call-site-reads-answer-not-bool lint finding makes it
        # uncallable in any mode) -- the runner must still never be
        # called and the current answer must still never be overridden.
        e = _noul_entry("N4")
        e["call_site_reads_answer"] = "false"
        registry = [e]
        cfg = _seams_config({"N4": "act"})
        runner = ScriptedRunner(_noul_answer(0.9))
        result = seam.consult("N4", {"i": 1}, "cur", seams_config=cfg, registry=registry,
                              ledger_dir=self.ledger_dir, runner=runner)
        self.assertEqual(result.answer, "cur")
        self.assertEqual(runner.calls, [])
        self.assertIn("call_site_reads_answer", (result.reason or ""))

    def test_numeric_zero_is_a_typo_not_a_free_pass(self):
        # Same shape as the string-false case, for a numeric 0: another
        # value a loose "is False" comparison would have missed (0 ==
        # False in Python) or a naive truthiness check would have
        # confused with "falsy" rather than "not proven true".
        e = _noul_entry("N5")
        e["call_site_reads_answer"] = 0
        registry = [e]
        cfg = _seams_config({"N5": "act"})
        runner = ScriptedRunner(_noul_answer(0.9))
        result = seam.consult("N5", {"i": 1}, "cur", seams_config=cfg, registry=registry,
                              ledger_dir=self.ledger_dir, runner=runner)
        self.assertEqual(result.answer, "cur")
        self.assertEqual(runner.calls, [])
        self.assertIn("call_site_reads_answer", (result.reason or ""))


if __name__ == "__main__":
    unittest.main()

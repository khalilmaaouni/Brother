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

THIS FILE ISOLATES ITS OWN JEV STATE, with nothing to export: the R4.1 block
below replaces HOME with a fresh temp home and BROTHER_JEV_STATE_DIR with a fresh
temp state dir before any product import (a live, relative or symlinked value is
replaced, never used), R4.2 repatches the seam's path constants to that dir, R4.3
starts every child through scripts/child_isolation.py, and R4.4's
R4NoRealStateProof records every filesystem read during a shadow spend and
proves none reaches real machine state. The suite is green under an empty HOME.
"""
import json
import os
import shlex
import stat
import sys
import tempfile
import time
import unittest
from unittest import mock

# R4.1: self isolate before any import that can transitively import jev_seam.
_LIVE_HOME_SNAPSHOT: str = ""
_TEMP_STATE_DIR: str = ""
_R41_TEMP_HOME_DIR = None
_R41_TEMP_STATE_PARENT = None


def _fail_closed(msg: str) -> None:
    raise unittest.SkipTest("R4.1 isolation failure: " + msg)


def _ensure_isolated_home() -> str:
    global _LIVE_HOME_SNAPSHOT, _R41_TEMP_HOME_DIR
    _LIVE_HOME_SNAPSHOT = os.environ.get("HOME", "")
    try:
        _R41_TEMP_HOME_DIR = tempfile.TemporaryDirectory(prefix="brother-jev-home-test-")
        home = _R41_TEMP_HOME_DIR.name
    except Exception as exc:
        _fail_closed("could not create temp home: %s" % exc)
    os.environ["HOME"] = home
    os.environ["XDG_CONFIG_HOME"] = home
    return home


def _ensure_isolated_state_dir(seed: str) -> str:
    global _TEMP_STATE_DIR, _R41_TEMP_STATE_PARENT
    candidate = seed
    bad = False
    if not isinstance(candidate, str):
        bad = True
    elif not candidate:
        bad = True
    elif not os.path.isabs(candidate):
        bad = True
    else:
        live = _LIVE_HOME_SNAPSHOT
        if live:
            try:
                resolved = os.path.realpath(candidate)
                live_real = os.path.realpath(live)
                if resolved == live_real or resolved.startswith(live_real + os.sep):
                    bad = True
            except Exception:
                bad = True
        if not bad:
            try:
                if os.path.islink(candidate):
                    target = os.readlink(candidate)
                    if not os.path.isabs(target):
                        target = os.path.join(os.path.dirname(candidate), target)
                    resolved_target = os.path.realpath(target)
                    if live:
                        live_real = os.path.realpath(live)
                        if resolved_target == live_real or resolved_target.startswith(live_real + os.sep):
                            bad = True
            except Exception:
                bad = True
    if bad:
        try:
            _R41_TEMP_STATE_PARENT = tempfile.TemporaryDirectory(prefix="brother-jev-state-test-")
            state_dir = os.path.join(_R41_TEMP_STATE_PARENT.name, "state")
            os.makedirs(state_dir, exist_ok=True)
        except Exception as exc:
            _fail_closed("could not create temp state dir: %s" % exc)
        os.environ["BROTHER_JEV_STATE_DIR"] = state_dir
        _TEMP_STATE_DIR = state_dir
        return state_dir
    else:
        _TEMP_STATE_DIR = candidate
        os.environ["BROTHER_JEV_STATE_DIR"] = candidate
        return candidate


_ensure_isolated_home()
_R41_HOME_AT_IMPORT = os.environ.get("HOME", "")   # R4.4: the HOME the product modules were imported under
_TEMP_STATE_DIR = _ensure_isolated_state_dir(os.environ.get("BROTHER_JEV_STATE_DIR"))

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import jev_calibration as jc  # noqa: E402
import jev_checks  # noqa: E402
import jev_seam as seam  # noqa: E402
import child_isolation as ci  # noqa: E402  (R4.3: every child of this suite starts through ci._run_isolated)
import state_proof as sp  # noqa: E402  (R4.4: the proof that this suite reads no real state)


MODEL = "typesafe/jev-1.13-test"


def _r42_require_dir(state_dir):
    if not isinstance(state_dir, str) or not state_dir or "\x00" in state_dir or not os.path.isabs(state_dir):
        raise ValueError("state dir must be a non empty absolute str with no NUL")
    return os.path.normpath(state_dir)


def _r42_is_under(path, parent):
    if not isinstance(path, str) or not isinstance(parent, str):
        return False
    if not path or not parent:
        return False
    p = os.path.normpath(path)
    q = os.path.normpath(parent)
    return p == q or p.startswith(q + os.sep)


def _temp_budget_path(state_dir):
    return os.path.join(_r42_require_dir(state_dir), "jev-budget.json")


def _temp_canary_path(state_dir):
    return os.path.join(_r42_require_dir(state_dir), "canary-reset.json")


def _capture_live_paths():
    home = _LIVE_HOME_SNAPSHOT
    if not home:
        try:
            import pwd
            home = pwd.getpwuid(os.getuid()).pw_dir
        except (ImportError, KeyError, OSError):
            home = ""
    if not home:
        return {"state_dir": "", "budget_path": "", "canary_path": ""}
    state_dir = os.path.join(home, ".brother", "jev")
    return {
        "state_dir": state_dir,
        "budget_path": os.path.join(state_dir, "jev-budget.json"),
        "canary_path": os.path.join(state_dir, "canary-reset.json"),
    }


def _repatch_seam_constants(state_dir):
    d = _r42_require_dir(state_dir)
    tmp = os.path.normpath(_TEMP_STATE_DIR)
    prefix = os.path.normpath(tempfile.gettempdir())
    if d != tmp and not _r42_is_under(d, prefix):
        raise ValueError("state dir is neither the temp state dir nor under the temp prefix")
    ledger = os.path.join(d, "ledger")
    budget = _temp_budget_path(d)
    canary = _temp_canary_path(d)
    for value in (ledger, budget, canary):
        if not _r42_is_under(value, d):
            raise ValueError("computed path is not under the state dir")
    assigned = {}
    if hasattr(seam, "JEV_STATE_DIR"):
        seam.JEV_STATE_DIR = d
        assigned["jev_seam.JEV_STATE_DIR"] = d
    if hasattr(seam, "DEFAULT_LEDGER_DIR"):
        seam.DEFAULT_LEDGER_DIR = ledger
        assigned["jev_seam.DEFAULT_LEDGER_DIR"] = ledger
    if hasattr(seam, "DEFAULT_CANARY_MARKER"):
        seam.DEFAULT_CANARY_MARKER = canary
        assigned["jev_seam.DEFAULT_CANARY_MARKER"] = canary
    if hasattr(seam, "DEFAULT_BUDGET_PATH"):
        seam.DEFAULT_BUDGET_PATH = budget
        assigned["jev_seam.DEFAULT_BUDGET_PATH"] = budget
    if hasattr(jev_checks, "DEFAULT_LEDGER_DIR"):
        jev_checks.DEFAULT_LEDGER_DIR = ledger
        assigned["jev_checks.DEFAULT_LEDGER_DIR"] = ledger
    if hasattr(jev_checks, "_JEV_STATE_DIR_FALLBACK"):
        jev_checks._JEV_STATE_DIR_FALLBACK = d
        assigned["jev_checks._JEV_STATE_DIR_FALLBACK"] = d
    return assigned


_R42_LIVE_PATHS = _capture_live_paths()


def _r43_live_paths():
    """The live paths every child's argv is checked against (R4.3): R4.2's capture plus the live home snapshot."""
    return dict(_R42_LIVE_PATHS, home=_LIVE_HOME_SNAPSHOT)


def _resolvable_live_home():
    """The home a live path could be derived from: the HOME snapshot, else the password database (a function, so the
    import time audit never sees pwd at module level)."""
    if _LIVE_HOME_SNAPSHOT:
        return _LIVE_HOME_SNAPSHOT
    try:
        import pwd
        return pwd.getpwuid(os.getuid()).pw_dir
    except (ImportError, KeyError, OSError):
        return ""

try:
    _R42_REPATCHED = _repatch_seam_constants(_TEMP_STATE_DIR)
    _R42_REPATCH_ERROR = ""
except ValueError as _r42_exc:
    _R42_REPATCHED = {}
    _R42_REPATCH_ERROR = str(_r42_exc)


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
    call with a seam present and off): what an off-mode call through
    jev_checks.py is allowed to cost. Class name kept as-is because
    docs/plan/specs/R4.md's T4 row names it.

    WHAT THIS CLASS USED TO CLAIM, AND WHY IT WAS WRONG (found 2026-09-21).
    The old assertion was "1,000 off-mode calls make ZERO calls to os.stat,
    os.path.exists or open", resting on the stated premise that
    "jev_seam.consult() itself already returns before any filesystem access
    once the resolved mode is off ... the OFF branch is the very first
    check". That premise describes a consult() that no longer exists. D3
    (docs/plan/specs/D3.md line 274) deliberately puts the attempt-ledger
    submission write AHEAD of the off branch -- "mint id, resolve ledger
    paths, write submission best effort, resolve configured mode ..., handle
    off" -- D3's TERMINAL_PHASES (line 86) carries "off_mode", and D3's own
    required test list (line 297) names test_terminal_row_written_for_off_mode.
    An off-mode call writes two attempt rows BY DESIGN, so the zero-filesystem
    claim was stricter than the contract the seam is built to, and the
    assertion was failing on correct code. Measured at the source: the
    touches are os.makedirs at jev_seam.py:845 (_append_submission_best_effort,
    called from consult() at jev_seam.py:2106) and the same makedirs at
    jev_seam.py:873 (_append_terminal_best_effort, reached via
    _pre_dispatch_exit), plus jev_calibration._append_line's os.open per row.

    THE OLD FIXTURE ALSO MEASURED THE WRONG BRANCH. It passed
    ledger_dir="/does/not/exist", so every call failed its submission write
    and left through consult()'s "write_error" branch, never the "off_mode"
    branch it meant to test -- and the three missing path components made
    os.makedirs recurse, inflating the cost to 8 os.stat per call (measured:
    8,000 over 1,000 calls) where a real ledger directory costs 7. One
    fixture tripping two different things proves neither, so this one uses a
    real temporary ledger directory and asserts reason is None, which is the
    off_mode branch and nothing else.

    THE REAL CONTRACT, measured on this tree 2026-09-21 (python3 on darwin,
    constant per call at N=1, 10, 100 and 1,000): an off-mode call makes NO
    Jev call at all -- no registry read (ExplodingRegistry below raises if
    touched), no canary marker stat, no seams-config read, no decide(), no
    bridge subprocess, no thread, no decisions.jsonl row -- and its
    filesystem cost is a fixed pair of best-effort appends to the attempts
    ledger and NOTHING ELSE. At a thousand wired call sites that is a real
    cost, not a free one: the numbers below are a budget, so a change that
    reads one more file per off call goes red here instead of landing
    quietly. The genuinely zero-cost path for a harness call site is
    jev_seam.resolve_mode() BEFORE consult() (see resolve_mode()'s own
    docstring: "exactly as safe, only slower when off"), which is what
    jev_checks.main() already does to skip the registry load.

    os.open is counted here, not only builtins.open: jev_calibration's
    _append_line writes through os.open(), so a counter watching only
    builtins.open can never fire on the one write this path actually makes,
    which is why the old test's open counter was always empty.

    See the module docstring's own instructions for the separate 20,000-call
    before/after microbenchmark rule 9 also asks for, reported by hand
    (timing is not something a deterministic assertion should gate on)."""

    #: Measured 2026-09-21 over 1,000 calls against a real ledger directory:
    #: two os.makedirs (each 1 os.path.exists plus 2 os.stat: the parent
    #: through exists(), the leaf through isdir() after mkdir raises
    #: FileExistsError) plus 3 os.stat and 2 os.open on attempts.jsonl for
    #: the two rows appended. builtins.open is never used on this path.
    PER_CALL = {"stat": 7, "exists": 2, "os.open": 2, "open": 0}
    CALLS = 1000

    def test_a_thousand_off_mode_calls_cost_only_the_attempt_ledger(self):
        registry = ExplodingRegistry()  # off mode must never even read this
        cfg = _seams_config({})  # every entry named here is off (the default)

        with tempfile.TemporaryDirectory() as ledger_dir:
            attempts_path = os.path.join(ledger_dir, "attempts.jsonl")

            # WARM-UP, outside the counted window: this call creates
            # attempts.jsonl, so the counted window below measures the
            # steady-state per-call cost and not a one-time file creation.
            warm = jev_checks.check_row_claim_support(
                "claim", "source", "current", seams_config=cfg, registry=registry,
                ledger_dir=ledger_dir, runner=None)
            self.assertEqual(warm.answer, "current")
            self.assertEqual(warm.mode, "off")
            # reason is None only on consult()'s real off_mode branch; the
            # write_error branch the old fixture hit carries a reason string.
            self.assertIsNone(warm.reason)

            counts = {"stat": 0, "exists": 0, "open": 0, "os.open": 0}
            touched = set()
            real = {"stat": os.stat, "exists": os.path.exists,
                    "open": open, "os.open": os.open}

            def counting(kind):
                def call(path=None, *a, **k):
                    counts[kind] += 1
                    touched.add(str(path))
                    return real[kind](path, *a, **k)
                return call

            with mock.patch("os.stat", counting("stat")), \
                 mock.patch("os.path.exists", counting("exists")), \
                 mock.patch("builtins.open", counting("open")), \
                 mock.patch("os.open", counting("os.open")):
                for _ in range(self.CALLS):
                    result = jev_checks.check_row_claim_support(
                        "claim", "source", "current", seams_config=cfg,
                        registry=registry, ledger_dir=ledger_dir, runner=None)
                    self.assertEqual(result.answer, "current")
                    self.assertEqual(result.mode, "off")

            # (1) THE BUDGET: exactly the measured cost, times the call
            # count. Equality, not a ceiling, so a per-call growth of even
            # one stat is visible, and so is anything that caches on the
            # first call and hides a leak on the rest.
            self.assertEqual(
                counts, {k: v * self.CALLS for k, v in self.PER_CALL.items()})

            # (2) THE SHARP ONE: nothing outside the attempts ledger's own
            # directory chain is touched at all. This is what off mode is
            # FOR -- a wired-in seam that reads no registry, no seams config
            # and no canary marker -- and it holds however the counts above
            # move. os.makedirs walks up to the parent, so ledger_dir's
            # ancestors are allowed; no file other than attempts.jsonl is.
            allowed = {attempts_path}
            node = ledger_dir
            while True:
                allowed.add(node)
                parent = os.path.dirname(node)
                if parent == node:
                    break
                node = parent
            self.assertEqual(touched - allowed, set())

            # (3) What off mode DOES write: the two attempt rows D3 requires
            # (one "submitted", one "off_mode"), per call, both at mode off,
            # and no Jev decision row at all. Stated positively so the
            # budget above cannot be satisfied by silently writing nothing.
            with open(attempts_path, encoding="utf-8") as fh:
                rows = [json.loads(line) for line in fh if line.strip()]
            self.assertEqual(len(rows), 2 * (self.CALLS + 1))  # + the warm-up
            self.assertEqual(sorted({r["phase"] for r in rows}),
                             ["off_mode", "submitted"])
            self.assertEqual({r["mode"] for r in rows}, {"off"})
            self.assertFalse(
                os.path.exists(os.path.join(ledger_dir, "decisions.jsonl")))


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
            with tempfile.TemporaryDirectory() as state:
                proc = ci._run_isolated(["git", "-C", cls.REPO_ROOT, "status", "--porcelain"], ci._child_env(state),
                                        _r43_live_paths(), state, 30.0)
        except (OSError, ValueError, ci.SubprocessError) as exc:
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
        # R4.3: the child's whole environment is _child_env's (a fresh HOME inside this test's state dir, nothing live
        # passed through), its cwd is this test's temp dir, and its argv is checked against the live paths
        env = ci._child_env(self.state_dir, home_files=ci.fixture_home_files(_LIVE_HOME_SNAPSHOT))
        env["BROTHER_DECISION_BRIDGE"] = " ".join(shlex.quote(a) for a in bridge_argv)
        if pidfile:
            env["JEV_TEST_BRIDGE_PIDFILE"] = pidfile
        self.last_env = env
        return ci._run_isolated(
            [sys.executable, self.SCRIPT, "--seams-config", self.seams_path,
             "--ledger-dir", self.ledger_dir] + args,
            env, _r43_live_paths(), self._tmp.name, timeout)

    def _decision_rows(self):
        path = os.path.join(self.ledger_dir, "decisions.jsonl")
        if not os.path.exists(path):
            return 0
        with open(path, encoding="utf-8") as f:
            return sum(1 for line in f if line.strip())

    def test_child_env_helper_never_passes_live_home(self):
        # R4.3 R16: the CLI child gets _child_env's environment: a fresh HOME inside this test's state dir, the state dir
        # as BROTHER_JEV_STATE_DIR, and no live variable passed through (M-R4-CHILD turns this red)
        self._write_seams({})
        proc = self._run_cli(["--help"], [sys.executable, "-c", "pass"], timeout=60.0)
        self.assertIsNotNone(proc)
        env = self.last_env
        self.assertTrue(env["HOME"].startswith(self.state_dir + os.sep), env["HOME"])
        self.assertNotEqual(os.path.realpath(env["HOME"]), os.path.realpath(_LIVE_HOME_SNAPSHOT or "/nonexistent"))
        self.assertEqual(env["BROTHER_JEV_STATE_DIR"], self.state_dir)
        allowed = set(ci.KEEP) | {"HOME", "BROTHER_JEV_STATE_DIR", "PYTHONPATH", "PYTHONDONTWRITEBYTECODE",
                                  "BROTHER_DECISION_BRIDGE", "JEV_TEST_BRIDGE_PIDFILE"}
        self.assertEqual(set(env) - allowed, set(), "a live variable reached the child")

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


# WHAT THESE TESTS NEED FROM THE TREE AND THE HOME, stated once and supplied
# here, so a clean machine gives the same answer as the author's (measured
# 2026-09-20 on an export shaped tree under an empty HOME: 23 and 41 reds).
# 1. data/jev-registry.json. The public export does not ship data/, so on
#    that tree every class below reads NO-DATA (a skip), never a red: the
#    same rule the J099 receipt door tests took that morning.
# 2. The content gate's forbidden terms list, a private file in the
#    operator's home. A fixture list stands in for it, as in the J100 and
#    J094 seam tests; a missing list makes the gate refuse, which is its
#    contract, and that refusal is not what these tests are about.
# A TRACKED FIXTURE, SO THIS SUITE RUNS ON A TREE THAT SHIPS NO data/ DIRECTORY.
# Measured 2026-09-22: the public export omits data/, so every class here skipped, the suite
# reported "OK (skipped=48)", and vacuous_run_guard correctly refused it with exit 1, which
# blocked the push. The guard was right and the skip was the defect: a suite that can only run
# on one checkout is measuring that checkout.
# The fixture is built FROM the real registry (scripts/fixtures/jev-registry-fixture.json) so its
# shape cannot drift from production by hand, and it is tracked so it travels with the export.
# FAIL DIRECTION: if NEITHER the real registry nor the fixture is readable, the skipUnless still
# fires and the guard still refuses. An unreadable input is never read as "nothing to check".
_FIXTURE_REGISTRY = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                 "fixtures", "jev-registry-fixture.json")
# CAPTURED BEFORE THE REBIND BELOW, because a CHILD process computes this same path from its own
# module location and no in process rebind can reach it. Measured 2026-09-22: rebinding first made
# the planting step below a no op (the rebound path exists, so it returned early) while three
# subprocess tests still died on the ORIGINAL path.
_REAL_REGISTRY_PATH = seam.DEFAULT_REGISTRY_PATH
if not os.path.isfile(seam.DEFAULT_REGISTRY_PATH) and os.path.isfile(_FIXTURE_REGISTRY):
    seam.DEFAULT_REGISTRY_PATH = _FIXTURE_REGISTRY

_NEEDS_REGISTRY = unittest.skipUnless(
    os.path.isfile(seam.DEFAULT_REGISTRY_PATH),
    "NO-DATA: neither data/jev-registry.json nor the tracked fixture is readable in this tree")


def _plant_registry_for_children():
    """Put the tracked fixture where a CHILD process will look, when the real registry is absent.

    Measured 2026-09-22: patching seam.DEFAULT_REGISTRY_PATH in THIS process fixed the in process
    tests and left three M1CliDrainsBeforeExit cases failing, because those spawn the real CLI as a
    subprocess and the child resolves the registry from its OWN module location, which no in
    process patch can reach. On an export tree that path does not exist and the child dies with
    FileNotFoundError.

    On the real checkout the registry IS present, so this is a no op and nothing is written.
    FAIL DIRECTION: if the fixture itself cannot be read or copied, nothing is planted, the child
    still fails, and the suite goes red. A tree we cannot prepare is never reported as prepared.
    """
    import shutil
    if os.path.isfile(_REAL_REGISTRY_PATH):
        return                                  # the real thing is here, touch nothing
    if not os.path.isfile(_FIXTURE_REGISTRY):
        return                                  # nothing to plant, the skipUnless will refuse
    try:
        os.makedirs(os.path.dirname(_REAL_REGISTRY_PATH), exist_ok=True)
        shutil.copyfile(_FIXTURE_REGISTRY, _REAL_REGISTRY_PATH)
    except OSError:
        pass                                    # unreadable tree stays unprepared, and red


def setUpModule():
    _plant_registry_for_children()
    import coe_outside_gate
    # A fixture HOME, not only a patched constant: several tests below spawn
    # the real CLI as a child process, and the child resolves the list from
    # its own HOME. The child inherits this one.
    home = tempfile.mkdtemp(prefix="brother-jev-home-test-")
    os.makedirs(os.path.join(home, ".claude"))
    terms = os.path.join(home, ".claude", "coe-outside-gate-terms.json")
    with open(terms, "w", encoding="utf-8") as fh:
        json.dump({"vendor-fixture": ["ACMEWIDGET"]}, fh)
    for patcher in (mock.patch.object(coe_outside_gate, "DEFAULT_TERMS_PATH", terms),
                    mock.patch.dict(os.environ, {"HOME": home})):
        patcher.start()
        unittest.addModuleCleanup(patcher.stop)


for _name, _obj in list(globals().items()):
    if isinstance(_obj, type) and issubclass(_obj, unittest.TestCase):
        globals()[_name] = _NEEDS_REGISTRY(_obj)


class R4ChecksRepatchProof(unittest.TestCase):
    def test_checks_ledger_default_is_under_the_state_dir(self):
        self.assertEqual(_R42_REPATCH_ERROR, "")
        self.assertTrue(_r42_is_under(jev_checks.DEFAULT_LEDGER_DIR, _TEMP_STATE_DIR))

    def test_fallback_is_repatched_only_when_it_exists(self):
        self.assertEqual(_R42_REPATCH_ERROR, "")
        if hasattr(jev_checks, "_JEV_STATE_DIR_FALLBACK"):
            self.assertIn("jev_checks._JEV_STATE_DIR_FALLBACK", _R42_REPATCHED)
            self.assertEqual(jev_checks._JEV_STATE_DIR_FALLBACK, _TEMP_STATE_DIR)
        else:
            self.assertNotIn("jev_checks._JEV_STATE_DIR_FALLBACK", _R42_REPATCHED)

    def test_live_paths_are_captured_for_the_child_guard(self):
        self.assertEqual(sorted(_R42_LIVE_PATHS.keys()),
                         ["budget_path", "canary_path", "state_dir"])



class R4ChildEnvAudit(unittest.TestCase):
    """R4.3 (built 2026-10-03): every child either suite starts goes through child_isolation._run_isolated, its argv is
    checked against the live paths captured at import before it starts, and the check has nothing to compare against
    only when no live home can be resolved, which reads NO-DATA by skip, never a pass."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.state = self._tmp.name
        self.live = _r43_live_paths()
        if not _resolvable_live_home():
            self.skipTest("NO-DATA: no live home resolvable, so no live path to guard against")
        for k in ci.LIVE_KEYS:   # M-R4-LIVE-SNAPSHOT: an empty capture while a home resolves is a defect, never a skip
            self.assertTrue(self.live[k], "captured live %s is empty while a live home resolves" % k)

    def test_every_subprocess_run_site_routes_through_isolated(self):
        here = os.path.dirname(os.path.abspath(__file__))
        for name in ("test_jev_checks.py", "test_jev_seam.py", "child_isolation.py"):
            with open(os.path.join(here, name), encoding="utf-8") as fh:
                ci._assert_no_subprocess_run_directly(fh.read())   # raises naming the line of any direct site
        for hostile in ("import subprocess\nsubprocess.run(['true'])\n",
                        "from subprocess import check_output as c\nc(['true'])\n",
                        "import os\nos.system('true')\n",
                        "import subprocess\ndef _run_isolated():\n    pass\nsubprocess.Popen(['true'])\n"):
            with self.assertRaises(ValueError, msg=hostile):
                ci._assert_no_subprocess_run_directly(hostile)

    def test_argv_never_carries_a_live_path(self):
        marker = os.path.join(self.state, "started")
        env = ci._child_env(self.state)
        for key in ci.LIVE_KEYS:
            for arg in (self.live[key], "--x=" + self.live[key]):
                with self.assertRaises(ValueError, msg=arg):
                    ci._run_isolated([sys.executable, "-c", "open(%r, 'w')" % marker, arg], env, self.live, self.state, 30)
        self.assertFalse(os.path.exists(marker), "a refused child started anyway")
        proc = ci._run_isolated([sys.executable, "-c", "open(%r, 'w')" % marker], env, self.live, self.state, 30)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertTrue(os.path.exists(marker), "an allowed child did not run")

    def test_argv_guard_uses_captured_live_paths(self):
        # the guard compares against R4.2's capture (_R42_LIVE_PATHS), never the module constants, which hold temp values
        self.assertEqual({k: self.live[k] for k in ci.LIVE_KEYS}, {k: _R42_LIVE_PATHS[k] for k in ci.LIVE_KEYS})
        self.assertNotEqual(_R42_LIVE_PATHS["budget_path"], getattr(seam, "DEFAULT_BUDGET_PATH", None))
        for k in ci.LIVE_KEYS:
            with self.assertRaises(ValueError):
                ci._run_isolated(["true", self.live[k]], ci._child_env(self.state), self.live, self.state, 30)
        with self.assertRaises(ValueError):   # a missing captured live path blocks the child
            ci._run_isolated(["true"], ci._child_env(self.state), dict(self.live, budget_path=""), self.state, 30)

    def test_hostile_inputs_are_refused_never_a_crash(self):
        env = ci._child_env(self.state)
        for argv in ([], "true", [b"true"], ["tr\0ue"], [None]):
            with self.assertRaises(ValueError, msg=repr(argv)):
                ci._run_isolated(argv, env, self.live, self.state, 30)
        for bad_env in ({}, dict(env, HOME=self.state), dict(env, HOME="relative"), dict(env, X=self.live["state_dir"])):
            with self.assertRaises(ValueError, msg=repr(bad_env.get("HOME"))):
                ci._run_isolated(["true"], bad_env, self.live, self.state, 30)
        for cwd in ("", "relative", ci.REPO_ROOT, os.path.join(ci.REPO_ROOT, "scripts")):
            with self.assertRaises(ValueError, msg=cwd):
                ci._run_isolated(["true"], env, self.live, cwd, 30)
        for timeout in (0, -1, True, "30", 10 ** 6):
            with self.assertRaises(ValueError, msg=repr(timeout)):
                ci._run_isolated(["true"], env, self.live, self.state, timeout)
        for state in ("", "relative", None, os.path.join(self.state, "missing")):
            with self.assertRaises(ValueError, msg=repr(state)):
                ci._child_env(state)
        for files in ("x", {"/etc/x": ""}, {"../x": ""}, {"a": b"x"}, {"": ""}):
            with self.assertRaises(ValueError, msg=repr(files)):
                ci._child_env(self.state, home_files=files)



class R4NoRealStateProof(CheckTestCase):
    """R4.4 (built 2026-10-03): the in process work of this suite reads no real state. The paths a case runs on
    resolve under no live home (R19), every filesystem read entry point is recorded during a representative shadow
    spend and none reaches the live home or a state file outside temp (R20), an empty HOME still checks the password
    database home (R20, M-R4-EMPTY-HOME), the read entry point list is proven complete by a negative listdir fixture
    (R20a), and the proof class itself is green in a child under a fresh empty HOME (T8)."""

    def _named_paths(self):
        return {"state_dir": seam.JEV_STATE_DIR, "budget_path": seam.DEFAULT_BUDGET_PATH,
                "ledger_dir": self.ledger_dir, "HOME": os.environ.get("HOME", ""),
                "HOME_at_import": _R41_HOME_AT_IMPORT}

    def _shadow_spend(self):
        """One shadow spend with every read entry point recorded: the ledger row lands before the patches lift."""
        with sp.record_reads() as seen:
            result = jev_checks.check_row_claim_support(
                "row X is done", "the log never mentions row X", False,
                seams_config=_seams_config({"J030": "shadow"}), registry=[_noul_entry("J030")],
                ledger_dir=self.ledger_dir, runner=ScriptedRunner(_noul_answer(0.95)))
            seam.drain(timeout=5)
        self.assertEqual(result.answer, False)
        self.assertEqual(len(self._decisions()), 1)
        return seen

    def test_no_resolved_path_under_live_home(self):
        live = sp.live_home_for_check(_LIVE_HOME_SNAPSHOT)
        self.assertEqual(sp.assert_no_real_state(self._named_paths(), live), True if live else sp.NO_DATA)
        seen = self._shadow_spend()
        paths = [path for _, path in seen]
        self.assertTrue(any(path.endswith("decisions.jsonl") for path in paths), "the recorder never saw the spend")
        self.assertTrue(any(path.endswith("jev-budget.json") for path in paths), "the recorder never saw the budget")
        self.assertNotIn(_R42_LIVE_PATHS["budget_path"], paths, "the live budget was opened or statted")
        verdict = sp.recorded_paths_are_temp(seen, live, temp_prefix=(tempfile.gettempdir(), _TEMP_STATE_DIR))
        if not live:
            self.skipTest("NO-DATA: no live home resolvable; the temp prefix check ran and passed")
        self.assertIs(verdict, True)

    def test_empty_home_still_checks_password_db_home(self):
        import pwd
        try:
            expected = pwd.getpwuid(os.getuid()).pw_dir
        except (KeyError, OSError):
            self.skipTest("NO-DATA: the password database names no home for this user")
        with mock.patch.dict(os.environ, {"HOME": ""}):
            home = sp.live_home_for_check("")
        self.assertEqual(home, expected, "an empty HOME must fall back to the password database home")
        self.assertEqual(sp.live_home_for_check("/snapshot/home"), "/snapshot/home")
        live_budget = os.path.join(expected, ".brother", "jev", "jev-budget.json")
        with self.assertRaises(ValueError):   # the live check ran against that home, it did not read NO-DATA
            sp.recorded_paths_are_temp([("builtins.open", live_budget)], home)
        self.assertEqual(sp.recorded_paths_are_temp([("builtins.open", self.ledger_dir)], ""), sp.NO_DATA)
        with self.assertRaises(ValueError):   # the temp check still blocks with no live home at all
            sp.recorded_paths_are_temp([("builtins.open", live_budget)], "")

    def test_listdir_negative_proof(self):
        self.assertTrue(sp._negative_unpatched_listdir_proof(self.ledger_dir),
                        "leaving os.listdir unpatched did not make a listdir read invisible, or the full set misses it")
        names = sp._patched_read_entry_points()
        self.assertEqual(len(names), 14)
        self.assertIn("os.listdir", names)
        with self.assertRaises(ValueError):
            sp._negative_unpatched_listdir_proof(os.path.join(self.ledger_dir, "missing"))

    def test_hostile_inputs_are_refused_never_a_crash(self):
        for bad in (None, "", {}, {"x": ""}, {"x": "relative"}, {"x": "/nul\0"}, {"x": 7}):
            with self.assertRaises(ValueError, msg=repr(bad)):
                sp.assert_no_real_state(bad, "/home")
        with self.assertRaises(ValueError):
            sp.assert_no_real_state({"x": "/x"}, None)
        for bad in (None, "x", [7], [("open",)], [("open", 7)]):
            with self.assertRaises(ValueError, msg=repr(bad)):
                sp.recorded_paths_are_temp(bad, "/home")
        with self.assertRaises(ValueError):
            sp.recorded_paths_are_temp([], "/home", temp_prefix="relative")
        with self.assertRaises(ValueError):
            sp.recorded_paths_are_temp([], "/home", temp_prefix=())
        with self.assertRaises(ValueError):
            sp.live_home_for_check(7)
        with self.assertRaises(ValueError):
            with sp.record_reads(["nodots"]):
                pass

    def test_empty_home_exit_zero(self):
        # T8: this file's proof tests, in a child whose whole environment is _child_env's (a fresh empty HOME inside a
        # temp state dir), must exit 0; the full suite is the unit's done check (donecheck_R4, hermetic), so a test
        # running its whole file would recurse and is not what runs here
        state = tempfile.mkdtemp(prefix="r44-empty-home-", dir=self.ledger_dir)
        module = os.path.splitext(os.path.basename(__file__))[0]
        names = ["%s.R4NoRealStateProof.%s" % (module, name) for name in
                 ("test_no_resolved_path_under_live_home", "test_listdir_negative_proof",
                  "test_empty_home_still_checks_password_db_home")]
        env = ci._child_env(state, home_files=ci.fixture_home_files(_LIVE_HOME_SNAPSHOT))
        try:
            proc = ci._run_isolated([sys.executable, "-B", "-m", "unittest"] + names, env,
                                    _r43_live_paths(), state, 120.0)
        except (OSError, ValueError, ci.SubprocessError) as exc:
            self.fail("the empty home child did not run: %s" % exc)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertIn("Ran 3 tests", proc.stderr)
        self.assertNotIn("skipped", proc.stderr, "the proof read NO-DATA under an empty home")

if __name__ == "__main__":
    # NOT unittest.main(). Every TestCase class in this file is wrapped in one
    # skipUnless above, so a single missing input skips the WHOLE suite, and a
    # suite that skipped everything still exits 0. Measured 2026-09-21: this file
    # printed "OK (skipped=48)" at exit 0 with nothing run, and that exact shape is
    # what unit R4 has on record as its evidence. The guard refuses a run that
    # proved nothing, so a done_check chained on && cannot certify one.
    import vacuous_run_guard
    vacuous_run_guard.run()

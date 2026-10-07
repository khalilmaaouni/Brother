#!/usr/bin/env python3
"""Tests for FX-31.5: model calls route through the adapters, and the router filters row stages.

Every case reaches the public boundary (model_call.call_one, model_call._argv or model_router.chain) with an injected
runner, a fake admission pool, a scratch breaker state and a temporary executable standing in for every program, so
nothing here starts a process, reads a home directory or writes outside a temporary folder. One condition per case:
R-FX-31-3: the adapter's verdict is the attempt's, a provider refusal is its own status and never an answer, and a
result the adapter cannot judge is a failed attempt rather than a raise. R-FX-31-4: a retired or shadow row never
enters the chain, even pinned. Edge: a command line the adapter refuses is a failed attempt the chain moves past.
"""
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.insert(0, HERE)
import model_call as MC  # noqa: E402
import model_router as R  # noqa: E402
from adapters.bridge import BridgeAdapter  # noqa: E402
from adapters.claude import ClaudeAdapter  # noqa: E402
from adapters.codex import CodexAdapter  # noqa: E402

PROGRAM = ""
_TMP = ""


def setUpModule():
    global PROGRAM, _TMP
    _TMP = tempfile.mkdtemp(prefix="adapter-routes-test-")
    PROGRAM = os.path.join(_TMP, "program")
    with open(PROGRAM, "w") as fh:
        fh.write("#!/bin/sh\nexit 3\n")
    os.chmod(PROGRAM, 0o755)


def tearDownModule():
    shutil.rmtree(_TMP, ignore_errors=True)


class _NoSlot(Exception):
    pass


class _FakeAdmission(object):
    """The pool contract only, so a tree without plugin/ still reaches the code under test."""

    NoSlotAvailable = _NoSlot

    def __init__(self, root):
        self.root = root

    def state_root(self):
        return self.root

    def effective_slots(self, root, want):
        return want

    def acquire_slot(self, root, limit, holder, timeout_seconds=None):
        return object()

    def release_slot(self, slot):
        return None


def _registry():
    return {
        "k": {"id": "claude-x", "transport": "claude", "privacy": R.PRIVATE, "quality": {"build": 9},
              "kinds": {"build"}, "cost": 1.0},
        "b": {"id": "x/b", "transport": "bridge", "privacy": R.PUBLIC, "quality": {"build": 5},
              "kinds": {"build"}, "cost": 1.0},
        "c": {"id": "gpt-x", "transport": "codex", "privacy": R.PUBLIC, "quality": {"build": 3},
              "kinds": {"build"}, "cost": 1.0},
    }


def _staged():
    """Three bridge rows: the best scoring is retired, the middle is shadow, only the lowest is seated."""
    return {
        "best": {"id": "x/best", "transport": "bridge", "privacy": R.PUBLIC, "quality": {"build": 9}, "kinds": {"build"},
                 "cost": 1.0, "stage": "retired"},
        "mid": {"id": "x/mid", "transport": "bridge", "privacy": R.PUBLIC, "quality": {"build": 7}, "kinds": {"build"},
                "cost": 1.0, "stage": "shadow"},
        "seated": {"id": "x/seated", "transport": "bridge", "privacy": R.PUBLIC, "quality": {"build": 5},
                   "kinds": {"build"}, "cost": 1.0},
    }


def _spy(result):
    """A runner that records the line it was handed and answers with `result`."""
    seen = []

    def run(argv, stdin, timeout):
        seen.append((list(argv), stdin, timeout))
        return result

    run.seen = seen
    return run


ANSWER = {"returncode": 0, "stdout": "fine", "stderr": ""}


class _Base(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="adapter-routes-")
        self.saved_env = dict(os.environ)
        self.saved = (MC.admission, MC._admit_without_a_row, MC.CLAUDE)
        self.addCleanup(self._tear_down)
        os.environ["BROTHER_OR_STATE_ROOT"] = self.tmp
        os.environ["BROTHER_BREAKER"] = "off"
        for key in ("BROTHER_PROGRAM_RECORD", "BROTHER_PROOF_PHASE", "BROTHER_RUN_DIR", "BROTHER_LOOP_TOKEN",
                    "BROTHER_BRIDGE_EFFORT", "BROTHER_CLAUDE_EFFORT", "BROTHER_TRANSPORTS"):
            os.environ.pop(key, None)
        os.environ["BROTHER_CLAUDE_CALLS_LEDGER"] = os.path.join(self.tmp, "claude-calls.jsonl")
        os.environ["BROTHER_BRIDGE_CALLS_LEDGER"] = os.path.join(self.tmp, "bridge-calls.jsonl")
        MC.admission = _FakeAdmission(self.tmp)
        MC._admit_without_a_row = lambda timeout: None
        MC.CLAUDE = PROGRAM
        for target, name, value in ((MC.R, "codex_bin", PROGRAM), (MC, "_codex_root", self.tmp)):
            p = mock.patch.object(target, name, return_value=value)
            p.start()
            self.addCleanup(p.stop)
        self.reg = _registry()

    def _tear_down(self):
        MC.admission, MC._admit_without_a_row, MC.CLAUDE = self.saved
        os.environ.clear()
        os.environ.update(self.saved_env)
        shutil.rmtree(self.tmp, ignore_errors=True)


class StageFilter(_Base):
    def test_a_retired_row_is_excluded_from_the_chain_even_when_it_scores_highest(self):
        self.assertNotIn("best", R.chain("build", R.PUBLIC, rel={}, registry_arg=_staged()))

    def test_a_shadow_row_is_excluded_from_the_chain(self):
        self.assertNotIn("mid", R.chain("build", R.PUBLIC, rel={}, registry_arg=_staged()))

    def test_the_seated_row_is_what_remains(self):
        self.assertEqual(R.chain("build", R.PUBLIC, rel={}, registry_arg=_staged()), ["seated"])

    def test_a_pinned_retired_row_is_refused_by_name_never_silently_replaced(self):
        with self.assertRaises(R.Refused) as caught:
            R.chain("build", R.PUBLIC, pin="best", rel={}, registry_arg=_staged())
        self.assertIn("not seated", str(caught.exception))

    def test_a_pinned_shadow_row_is_refused_the_same_way(self):
        with self.assertRaises(R.Refused) as caught:
            R.chain("build", R.PUBLIC, pin="mid", rel={}, registry_arg=_staged())
        self.assertIn("not seated", str(caught.exception))


class AdapterLines(_Base):
    def test_the_claude_line_is_the_claude_adapters_line_with_the_prompt_on_stdin(self):
        spy = _spy({"returncode": 0, "stdout": json.dumps({"result": "12"}), "stderr": ""})
        a = MC.call_one("k", "hello", "build", R.PRIVATE, timeout=30, reg=self.reg, runner=spy)
        self.assertTrue(a.ok, a.detail)
        argv, stdin, _ = ClaudeAdapter(PROGRAM, env={}).argv("k", "hello", 30, self.reg["k"])
        self.assertEqual(spy.seen, [(argv, stdin, 30)])

    def test_the_codex_line_is_the_codex_adapters_line_with_stdin_closed(self):
        spy = _spy(ANSWER)
        a = MC.call_one("c", "hello", "build", R.PUBLIC, timeout=30, reg=self.reg, runner=spy)
        self.assertTrue(a.ok, a.detail)
        argv, stdin, _ = CodexAdapter(PROGRAM, self.tmp).argv("c", "hello", 30, self.reg["c"])
        self.assertEqual(spy.seen, [(argv, stdin, 30)])

    def test_the_bridge_line_is_the_bridge_adapters_line_on_the_gated_bridge(self):
        spy = _spy(ANSWER)
        a = MC.call_one("b", "hello", "build", R.PUBLIC, timeout=30, reg=self.reg, runner=spy)
        self.assertTrue(a.ok, a.detail)
        argv, stdin, _ = BridgeAdapter(MC.BRIDGE, env={}).argv("b", "hello", 30, self.reg["b"])
        self.assertEqual(spy.seen, [(argv, stdin, 30)])

    def test_an_unknown_transport_is_refused_by_name_before_any_process(self):
        reg = {"z": {"id": "x", "transport": "smoke-signal", "privacy": R.PUBLIC, "quality": {"build": 1},
                     "kinds": {"build"}, "cost": 1.0}}
        with mock.patch.object(subprocess, "Popen", side_effect=AssertionError("a process was spawned")):
            with self.assertRaises(R.Refused) as caught:
                MC._argv("z", "p", 60, reg)
        self.assertIn("unknown transport", str(caught.exception))
        self.assertIn("smoke-signal", str(caught.exception))


class OneEffortRule(_Base):
    """FX-31.5 review gap 2: model_call.claude_effort IS the Claude adapter's rule, so a one shot call and a native
    session read one variable one way: the floor, lifted by a higher named level, refused for a value naming no level."""

    def test_the_floor_holds_and_a_higher_level_lifts_it(self):
        self.assertEqual(MC.claude_effort({}, "claude-opus-5"), "medium")
        self.assertEqual(MC.claude_effort({"BROTHER_CLAUDE_EFFORT": "low"}, "claude-opus-5"), "medium")
        self.assertEqual(MC.claude_effort({"BROTHER_CLAUDE_EFFORT": "xhigh"}, "claude-opus-5"), "xhigh")
        self.assertEqual(MC.claude_effort({}, "claude-fable-5-1"), "high")

    def test_a_value_naming_no_level_is_refused_never_read_as_the_floor(self):
        for raw in ("bogus", "minimal", "MEDIUM-ISH"):
            with self.assertRaises(R.Refused, msg=raw) as caught:
                MC.claude_effort({"BROTHER_CLAUDE_EFFORT": raw}, "claude-opus-5")
            self.assertIn("BROTHER_CLAUDE_EFFORT", str(caught.exception))

    def test_a_one_shot_call_under_an_unreadable_effort_is_a_failed_attempt_with_no_row(self):
        os.environ["BROTHER_CLAUDE_EFFORT"] = "bogus"
        spy = _spy(ANSWER)
        a = MC.call_one("k", "hello", "build", R.PRIVATE, timeout=30, reg=self.reg, runner=spy)
        self.assertFalse(a.ok)
        self.assertEqual(a.status, "TRANSPORT_DOWN")
        self.assertIn("BROTHER_CLAUDE_EFFORT", a.detail)
        self.assertEqual(spy.seen, [])
        ledger = os.environ["BROTHER_CLAUDE_CALLS_LEDGER"]
        self.assertFalse(os.path.exists(ledger) and open(ledger).read().strip(),
                         "a call that was never registered must leave no row")


class RefusedLines(_Base):
    def test_a_line_the_adapter_refuses_is_a_failed_attempt_the_runner_never_sees(self):
        MC.CLAUDE = os.path.join(self.tmp, "no-such-program")
        spy = _spy(ANSWER)
        a = MC.call_one("k", "hello", "build", R.PRIVATE, timeout=30, reg=self.reg, runner=spy)
        self.assertFalse(a.ok)
        self.assertEqual(a.status, "TRANSPORT_DOWN")
        self.assertIn("no Claude program", a.detail)
        self.assertEqual(spy.seen, [])

    def test_a_refused_claude_line_still_writes_its_not_started_row_at_a_known_zero(self):
        MC.CLAUDE = os.path.join(self.tmp, "no-such-program")
        MC.call_one("k", "hello", "build", R.PRIVATE, timeout=30, reg=self.reg, runner=_spy(ANSWER))
        with open(os.environ["BROTHER_CLAUDE_CALLS_LEDGER"]) as fh:
            rows = [json.loads(line) for line in fh if line.strip()]
        done = [row for row in rows if row.get("event") == "done"]
        self.assertEqual([(row["cost_usd"], row["outcome"]) for row in done], [(0, "not_started")])

    def test_a_refused_bridge_line_without_a_runner_is_a_failed_attempt_not_a_raise(self):
        os.environ["BROTHER_BRIDGE_EFFORT"] = "low"   # the bridge adapter refuses any effort but its floor
        a = MC.call_one("b", "hello", "build", R.PUBLIC, timeout=30, reg=self.reg)
        self.assertFalse(a.ok)
        self.assertEqual(a.status, "TRANSPORT_DOWN")
        self.assertIn("BROTHER_BRIDGE_EFFORT", a.detail)


class AdapterVerdicts(_Base):
    def test_a_result_the_adapter_cannot_judge_is_a_failed_attempt_not_a_raise(self):
        spy = _spy({"returncode": None, "stdout": "a complete and plausible looking answer", "stderr": ""})
        a = MC.call_one("c", "hello", "build", R.PUBLIC, timeout=30, reg=self.reg, runner=spy)
        self.assertFalse(a.ok)
        self.assertEqual(a.answer, "")
        self.assertIn("could not be judged", a.detail)

    def test_the_adapters_provider_refusal_is_the_attempts_status_never_an_answer(self):
        spy = _spy({"returncode": 0, "stdout": "provider refused: the policy says no", "stderr": ""})
        a = MC.call_one("c", "hello", "build", R.PUBLIC, timeout=30, reg=self.reg, runner=spy)
        self.assertFalse(a.ok)
        self.assertEqual(a.status, "PROVIDER_REFUSED")

    def test_a_claude_refusal_record_is_provider_refused_with_no_answer(self):
        spy = _spy({"returncode": 0, "stdout": json.dumps({"result": "I will not", "stop_reason": "refusal"}),
                    "stderr": ""})
        a = MC.call_one("k", "hello", "build", R.PRIVATE, timeout=30, reg=self.reg, runner=spy)
        self.assertEqual((a.ok, a.answer, a.status), (False, "", "PROVIDER_REFUSED"))

    def test_a_non_zero_exit_with_a_full_body_fails(self):
        spy = _spy({"returncode": 1, "stdout": "a complete and plausible looking answer", "stderr": ""})
        a = MC.call_one("c", "hello", "build", R.PUBLIC, timeout=30, reg=self.reg, runner=spy)
        self.assertFalse(a.ok)
        self.assertIn("exit 1", a.detail)

    def test_an_empty_body_at_exit_zero_fails(self):
        a = MC.call_one("c", "hello", "build", R.PUBLIC, timeout=30, reg=self.reg,
                        runner=_spy({"returncode": 0, "stdout": "   \n  ", "stderr": ""}))
        self.assertFalse(a.ok)
        self.assertIn("EMPTY", a.detail)


if __name__ == "__main__":
    unittest.main()

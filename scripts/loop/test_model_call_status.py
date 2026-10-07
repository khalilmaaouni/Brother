#!/usr/bin/env python3
"""FX-11.2: the status on every attempt, one record per call, and the status walker.

Every case builds its own breaker state root and its own registry under a temp folder and injects
its own runner, so no live file is read, no plugin runtime is required and no process is started.
"""
import json
import os
import shutil
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import breaker as BR
import model_call


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
    """Two first party rows (one family) and three bridge rows across three families."""
    return {
        "fast": {"id": "claude-fast", "transport": "claude", "privacy": model_call.R.PRIVATE,
                 "quality": {"build": 9, "grade": 9}, "kinds": {"build", "grade"}, "cost": 1.0},
        "backup": {"id": "claude-backup", "transport": "claude", "privacy": model_call.R.PRIVATE,
                   "quality": {"build": 5, "grade": 5}, "kinds": {"build", "grade"}, "cost": 1.0},
        "dx": {"id": "deepseek/dx", "transport": "bridge", "privacy": model_call.R.PUBLIC,
               "quality": {"build": 3}, "kinds": {"build"}, "cost": 1.0},
        "mx": {"id": "meta/mx", "transport": "bridge", "privacy": model_call.R.PUBLIC,
               "quality": {"build": 3, "grade": 3}, "kinds": {"build", "grade"}, "cost": 1.0},
    }


def _claude_doc(payload, extra=None):
    doc = {"result": payload}
    if extra:
        doc.update(extra)
    return doc


def _answer(argv, stdin, timeout):
    """A claude call that answers plainly."""
    return {"returncode": 0,
            "stdout": json.dumps(_claude_doc("12", {"usage": {"input_tokens": 1, "output_tokens": 1}})),
            "stderr": ""}


def _prose(argv, stdin, timeout):
    """Exit zero, a valid claude result, but the value is NOT JSON: only the expect check can refuse it."""
    return {"returncode": 0, "stdout": json.dumps(_claude_doc("I think probably yes")), "stderr": ""}


def _verdict(argv, stdin, timeout):
    """A valid JSON answer carrying a NEGATIVE verdict: an ANSWER, not a failure."""
    return {"returncode": 0,
            "stdout": json.dumps(_claude_doc(json.dumps({"verdict": "FAIL", "why": "it is wrong"}))),
            "stderr": ""}


def _refusal(argv, stdin, timeout):
    """A claude result with stop_reason=refusal: a PROVIDER_REFUSED."""
    return {"returncode": 0,
            "stdout": json.dumps(_claude_doc("I will not do that", {"stop_reason": "refusal"})),
            "stderr": ""}


def _bridge_answer(argv, stdin, timeout):
    """A bridge call that answers plainly."""
    return {"returncode": 0, "stdout": "12", "stderr": "[usage] prompt=5 completion=3 model=x/b-v2"}


def _limited(argv, stdin, timeout):
    """Bridge 429 with no body: LIMIT."""
    return {"returncode": 1, "stdout": "", "stderr": "HTTP 429 from OpenRouter"}


class _Base(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="fx112-")
        self.saved_env = dict(os.environ)
        self.saved_admission = model_call.admission
        self.saved_admit = model_call._admit_without_a_row
        self.saved_claude = model_call.CLAUDE
        self.addCleanup(self._tear_down)
        # the adapters build a line only for an executable program (FX-31.5); every runner here is fake, so argv[0]
        # names this interpreter and the suite passes on a machine with no Claude Code installed
        model_call.CLAUDE = sys.executable
        os.environ["BROTHER_OR_STATE_ROOT"] = self.tmp
        os.environ["BROTHER_BREAKER"] = "on"
        os.environ.pop("BROTHER_PROGRAM_RECORD", None)
        for name in ("claude-calls.jsonl", "bridge-calls.jsonl"):
            with open(os.path.join(self.tmp, name), "w"):
                pass
        os.environ["BROTHER_CLAUDE_CALLS_LEDGER"] = os.path.join(self.tmp, "claude-calls.jsonl")
        os.environ["BROTHER_BRIDGE_CALLS_LEDGER"] = os.path.join(self.tmp, "bridge-calls.jsonl")
        model_call.admission = _FakeAdmission(self.tmp)
        model_call._admit_without_a_row = lambda timeout: None
        self.reg = _registry()

    def _tear_down(self):
        model_call.admission = self.saved_admission
        model_call._admit_without_a_row = self.saved_admit
        model_call.CLAUDE = self.saved_claude
        os.environ.clear()
        os.environ.update(self.saved_env)
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _state(self):
        with open(os.path.join(self.tmp, "breakers.json")) as f:
            return json.load(f)

    def _trip(self, transport, model):
        account = BR.account_of(transport, [])
        k = BR.key(transport, account, model, "LIMIT")
        for i in range(BR.TRIP_AT):
            BR.record(k, "LIMIT", "trip-%s-%d" % (model, i))
        return k


class AttemptStatusTest(_Base):
    def test_unlabelled_result_is_never_healthy(self):
        self.assertEqual(model_call.Attempt("m", True, "a", "d", 1.0).status, "ANSWERED")
        self.assertEqual(model_call.Attempt("m", False, "a", "d", 1.0).status, "MALFORMED")
        self.assertEqual(model_call.Attempt("m", False, "", "d", 1.0, status="LIMIT").status, "LIMIT")

    def test_unknown_status_is_refused(self):
        with self.assertRaises(ValueError):
            model_call.Attempt("m", False, "", "d", 1.0, None, "BOGUS")
        with self.assertRaises(ValueError):
            model_call.Attempt("m", False, "", "d", 1.0, None, "")
        with self.assertRaises(ValueError):
            model_call.Attempt("m", False, "", "d", 1.0, None, 3)
        with self.assertRaises(ValueError):
            model_call.Attempt("m", False, "", "d", 1.0, None, b"x")


class WalkActionTest(_Base):
    def test_the_table_by_status_and_kind(self):
        self.assertEqual(model_call.walk_action("ANSWERED", "build"), "return")
        self.assertEqual(model_call.walk_action("ANSWERED", "grade"), "return")
        for status in ("EMPTY", "MALFORMED", "TIMEOUT", "LIMIT", "OVERLOAD", "AUTH", "TRANSPORT_DOWN"):
            self.assertEqual(model_call.walk_action(status, "build"), "next")
        self.assertEqual(model_call.walk_action(
            "REFUSED_BY_GATE", "build", "capacity: x open until 10:00 (LIMIT)"), "next")
        self.assertEqual(model_call.walk_action(
            "REFUSED_BY_GATE", "build", "breaker NO-DATA: the lock is held"), "next")
        self.assertEqual(model_call.walk_action(
            "REFUSED_BY_GATE", "build", "the privacy gate refused it"), "return")
        self.assertEqual(model_call.walk_action("PROVIDER_REFUSED", "build"), "other_family")
        self.assertEqual(model_call.walk_action("PROVIDER_REFUSED", "prose"), "other_family")
        self.assertEqual(model_call.walk_action("PROVIDER_REFUSED", "grade"), "return")
        self.assertEqual(model_call.walk_action("PROVIDER_REFUSED", "decide"), "return")
        self.assertEqual(model_call.walk_action("PROVIDER_REFUSED", "plan"), "return")

    def test_hostile_arguments_are_refused(self):
        with self.assertRaises(ValueError):
            model_call.walk_action(None, "build")
        with self.assertRaises(ValueError):
            model_call.walk_action("NOPE", "build")
        with self.assertRaises(ValueError):
            model_call.walk_action("ANSWERED", 3)
        with self.assertRaises(ValueError):
            model_call.walk_action("ANSWERED", "build", 0)
        with self.assertRaises(ValueError):
            model_call.walk_action(float("nan"), "build")


class OffTest(_Base):
    def test_off_reads_nothing(self):
        os.environ["BROTHER_BREAKER"] = "off"
        gate_calls = []
        record_calls = []
        orig_gate = model_call._capacity_gate
        orig_rec = model_call._record_call

        def gate_spy(*a, **k):
            gate_calls.append(a)
            return orig_gate(*a, **k)

        def rec_spy(*a, **k):
            record_calls.append(a)
            return orig_rec(*a, **k)

        model_call._capacity_gate = gate_spy
        model_call._record_call = rec_spy
        try:
            a = model_call.call_one("dx", "p", "build", model_call.R.PUBLIC,
                                    reg=self.reg, runner=_bridge_answer)
        finally:
            model_call._capacity_gate = orig_gate
            model_call._record_call = orig_rec
        self.assertTrue(a.ok)
        self.assertEqual(a.status, "ANSWERED")
        self.assertEqual(gate_calls, [])
        self.assertEqual(record_calls, [])
        self.assertFalse(os.path.exists(os.path.join(self.tmp, "breaker-events.jsonl")))


class AdmitTest(_Base):
    def test_open_key_runner_never_called(self):
        self._trip("bridge", "dx")
        seen = []

        def spy(argv, stdin, timeout):
            seen.append(argv)
            return _bridge_answer(argv, stdin, timeout)

        a = model_call.call_one("dx", "p", "build", model_call.R.PUBLIC, reg=self.reg, runner=spy)
        self.assertEqual(seen, [])
        self.assertFalse(a.ok)
        self.assertEqual(a.status, "REFUSED_BY_GATE")
        self.assertIn("capacity:", a.detail)

    def test_a_limit_on_one_model_leaves_the_sibling_closed(self):
        self._trip("bridge", "dx")
        a = model_call.call_one("mx", "p", "build", model_call.R.PUBLIC, reg=self.reg,
                                runner=_bridge_answer)
        self.assertTrue(a.ok)


class NoDataTest(_Base):
    def test_breaker_nodata_refuses(self):
        os.mkdir(os.path.join(self.tmp, "breakers.json"))
        seen = []

        def spy(argv, stdin, timeout):
            seen.append(argv)
            return _bridge_answer(argv, stdin, timeout)

        a = model_call.call_one("dx", "p", "build", model_call.R.PUBLIC, reg=self.reg, runner=spy)
        self.assertEqual(seen, [])
        self.assertFalse(a.ok)
        self.assertEqual(a.status, "REFUSED_BY_GATE")
        self.assertIn("breaker NO-DATA", a.detail)


class ShapeTest(_Base):
    def test_expect_json_malformed_inside_call_one(self):
        a = model_call.call_one("fast", "p", "build", model_call.R.PRIVATE, reg=self.reg,
                                runner=_prose, expect="json")
        self.assertFalse(a.ok)
        self.assertEqual(a.status, "MALFORMED")
        self.assertIn("JSON", a.detail)

    def test_a_provider_refusal_is_a_provider_refusal(self):
        a = model_call.call_one("fast", "p", "build", model_call.R.PRIVATE, reg=self.reg,
                                runner=_refusal)
        self.assertFalse(a.ok)
        self.assertEqual(a.status, "PROVIDER_REFUSED")


class WalkTest(_Base):
    def test_answer_never_hops(self):
        calls = []

        def run(argv, stdin, timeout):
            calls.append(argv)
            return _verdict(argv, stdin, timeout)

        win, tried = model_call.call("p", "grade", model_call.R.PRIVATE, reg=self.reg, runner=run,
                                     expect="json", record=False, chain=["fast", "backup"])
        self.assertIsNotNone(win)
        self.assertEqual(len(tried), 1)
        self.assertEqual(win.status, "ANSWERED")
        self.assertEqual(len(calls), 1)

    def test_refusal_on_grade_no_hop(self):
        calls = []

        def run(argv, stdin, timeout):
            calls.append(argv)
            return _refusal(argv, stdin, timeout)

        win, tried = model_call.call("p", "grade", model_call.R.PUBLIC, reg=self.reg, runner=run,
                                     record=False, chain=["fast", "mx"])
        self.assertEqual(len(tried), 1)
        self.assertEqual(len(calls), 1)
        self.assertIsNotNone(win)
        self.assertEqual(win.status, "PROVIDER_REFUSED")

    def test_refusal_hops_to_another_family_once(self):
        calls = []

        def run(argv, stdin, timeout):
            calls.append(argv)
            if len(calls) == 1:
                return _refusal(argv, stdin, timeout)
            return _bridge_answer(argv, stdin, timeout)

        win, tried = model_call.call("p", "build", model_call.R.PUBLIC, reg=self.reg, runner=run,
                                     record=False, chain=["fast", "dx"])
        self.assertEqual(len(tried), 2)
        self.assertIsNotNone(win)
        self.assertEqual(win.model, "dx")
        self.assertEqual(win.status, "ANSWERED")

    def test_single_family_refusal_parks(self):
        calls = []

        def run(argv, stdin, timeout):
            calls.append(argv)
            return _refusal(argv, stdin, timeout)

        win, tried = model_call.call("p", "build", model_call.R.PRIVATE, reg=self.reg, runner=run,
                                     record=False, chain=["fast", "backup"])
        self.assertEqual(len(tried), 1)
        self.assertEqual(len(calls), 1)
        self.assertIsNotNone(win)
        self.assertEqual(win.status, "PROVIDER_REFUSED")
        self.assertIn("family anthropic", win.detail)

    def test_limit_falls_over_to_the_next_member(self):
        calls = []

        def run(argv, stdin, timeout):
            calls.append(argv)
            if len(calls) == 1:
                return _limited(argv, stdin, timeout)
            return _bridge_answer(argv, stdin, timeout)

        win, tried = model_call.call("p", "build", model_call.R.PUBLIC, reg=self.reg, runner=run,
                                     record=False, chain=["dx", "mx"])
        self.assertEqual(len(tried), 2)
        self.assertIsNotNone(win)
        self.assertEqual(win.model, "mx")
        self.assertEqual(tried[0].status, "LIMIT")


class RecordOnceTest(_Base):
    def test_one_record_per_call(self):
        seen = []
        orig = model_call._record_call

        def spy(*a, **k):
            seen.append(a)
            return orig(*a, **k)

        model_call._record_call = spy
        try:
            model_call.call_one("dx", "p", "build", model_call.R.PUBLIC, reg=self.reg,
                                runner=_bridge_answer)
        finally:
            model_call._record_call = orig
        self.assertEqual(len(seen), 1)

    def test_a_limit_is_recorded_on_the_model_key(self):
        model_call.call_one("dx", "p", "build", model_call.R.PUBLIC, reg=self.reg, runner=_limited)
        k = BR.key("bridge", BR.account_of("bridge", []), "dx", "LIMIT")
        self.assertEqual(self._state()["keys"][k]["streak"], 1)

    def test_an_answer_resets_the_streak(self):
        for _ in range(2):
            model_call.call_one("dx", "p", "build", model_call.R.PUBLIC, reg=self.reg, runner=_limited)
        model_call.call_one("dx", "p", "build", model_call.R.PUBLIC, reg=self.reg, runner=_bridge_answer)
        k = BR.key("bridge", BR.account_of("bridge", []), "dx", "LIMIT")
        self.assertEqual(self._state()["keys"][k]["streak"], 0)


class HostileTest(_Base):
    def test_call_one_refuses_hostile_keywords(self):
        with self.assertRaises(ValueError):
            model_call.call_one("dx", "p", "build", model_call.R.PUBLIC,
                                reg=self.reg, runner=_bridge_answer, expect=0)
        with self.assertRaises(ValueError):
            model_call.call_one("dx", "p", "build", model_call.R.PUBLIC,
                                reg=self.reg, runner=_bridge_answer, expect=float("nan"))
        with self.assertRaises(ValueError):
            model_call.call_one("dx", "p", "build", model_call.R.PUBLIC,
                                reg=self.reg, runner=_bridge_answer, shadow="yes")
        with self.assertRaises(ValueError):
            model_call.call_one("dx", "p", "build", model_call.R.PUBLIC,
                                reg=self.reg, runner=_bridge_answer, shadow=None)

    def test_call_refuses_a_hostile_chain(self):
        with self.assertRaises(ValueError):
            model_call.call("p", "build", model_call.R.PUBLIC, reg=self.reg,
                            runner=_bridge_answer, chain="dx")
        with self.assertRaises(ValueError):
            model_call.call("p", "build", model_call.R.PUBLIC, reg=self.reg,
                            runner=_bridge_answer, chain=[1, 2])
        with self.assertRaises(ValueError):
            model_call.call("p", "build", model_call.R.PUBLIC, reg=self.reg,
                            runner=_bridge_answer, chain=None, shadow=3)
        with self.assertRaises(ValueError):
            model_call._gated_chain(None, "build", model_call.R.PUBLIC, self.reg)
        with self.assertRaises(ValueError):
            model_call._gated_chain([float("nan")], "build", model_call.R.PUBLIC, self.reg)


if __name__ == "__main__":
    unittest.main()

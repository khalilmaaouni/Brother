#!/usr/bin/env python3
"""Tests for scripts/loop/adapters/claude.py (FX-31.3).

Every case drives ClaudeAdapter.argv, ClaudeAdapter.judge or ClaudeAdapter.cost directly. Nothing here starts a process,
reads a home directory or writes outside a temporary folder: the program the argv names is a temporary file.
R-FX-31-2: an absent or invalid cost is NOT_MEASURED and a priced zero stays 0.0. R-FX-31-3: a provider refusal is its
own status and is never an answer. Edge: malformed JSON or an absent cost cannot turn an empty answer into success.
"""
import ast
import json
import os
import shutil
import sys
import tempfile
import unittest
from unittest import mock

HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.insert(0, HERE)
import adapters as A  # noqa: E402
from adapters import claude as C  # noqa: E402

ROW = {"id": "claude-opus-5", "transport": "claude", "privacy": "private"}
ABSENT = object()
PROGRAM = ""
_TMP = ""


def setUpModule():
    global PROGRAM, _TMP
    _TMP = tempfile.mkdtemp(prefix="claude-adapter-test-")
    PROGRAM = os.path.join(_TMP, "claude")
    with open(PROGRAM, "w") as fh:
        fh.write("#!/bin/sh\nexit 3\n")
    os.chmod(PROGRAM, 0o755)


def tearDownModule():
    shutil.rmtree(_TMP, ignore_errors=True)


def record(**fields):
    """The JSON result record `claude -p --output-format json` prints; a field given ABSENT is left out."""
    doc = {"type": "result", "subtype": "success", "is_error": False, "result": "12", "stop_reason": "end_turn",
           "total_cost_usd": 0.0086, "usage": {"input_tokens": 9, "output_tokens": 3}}
    doc.update(fields)
    return json.dumps({k: v for k, v in doc.items() if v is not ABSENT})


def ran(stdout, returncode=0, stderr=""):
    return {"returncode": returncode, "stdout": stdout, "stderr": stderr}


class TheArgvIsModelCallsClaudeLine(unittest.TestCase):
    """model_call's claude command line moved behind ClaudeAdapter.argv with no flag lost."""

    def setUp(self):
        self.adapter = C.ClaudeAdapter(PROGRAM, env={})

    def test_the_argv_is_model_calls_claude_line(self):
        argv, stdin, extra = self.adapter.argv("opus", "what is 7 plus 5", 300, ROW)
        self.assertEqual(argv, [PROGRAM, "-p", "--model", "claude-opus-5",
                                "--setting-sources", "", "--disable-slash-commands", "--strict-mcp-config",
                                "--exclude-dynamic-system-prompt-sections",
                                "--output-format", "json", "--no-session-persistence",
                                "--tools", "", "--effort", "medium"])
        self.assertEqual((stdin, extra), ("what is 7 plus 5", None))
        self.assertEqual(self.adapter.transport, "claude")

    def test_tools_stay_empty_the_output_is_json_and_no_session_persists(self):
        argv, _stdin, _ = self.adapter.argv("opus", "p", 60, ROW)
        self.assertEqual(argv.count("--tools"), 1)
        self.assertEqual(argv[argv.index("--tools") + 1], "", "no tool, ever")
        self.assertEqual(argv[argv.index("--output-format") + 1], "json")
        self.assertEqual(argv.count("--no-session-persistence"), 1)
        self.assertEqual(argv[argv.index("--setting-sources") + 1], "", "no inherited settings")

    def test_the_prompt_rides_on_stdin_and_never_on_argv(self):
        for prompt in ("--json", "-h", "--dangerously-skip-permissions", "two\nlines"):
            argv, stdin, _ = self.adapter.argv("opus", prompt, 60, ROW)
            self.assertNotIn(prompt, argv, prompt)
            self.assertEqual(stdin, prompt)
            self.assertEqual(argv[-2:], ["--effort", "medium"], prompt)

    def test_the_flags_are_model_calls_own(self):
        with open(os.path.join(HERE, "model_call.py"), encoding="utf-8") as fh:
            src = fh.read()
        names = ("CLAUDE_TRIM", "CLAUDE_IO", "CLAUDE_EFFORTS", "FLOOR_BY_MODEL")
        theirs = {t.id: ast.literal_eval(node.value) for node in ast.parse(src).body if isinstance(node, ast.Assign)
                  for t in node.targets if isinstance(t, ast.Name) and t.id in names}
        if not theirs:
            self.skipTest("NO-DATA: model_call carries no copy of the claude flags to compare")
        ours = {"CLAUDE_TRIM": list(C.CLAUDE_TRIM), "CLAUDE_IO": list(C.CLAUDE_IO),
                "CLAUDE_EFFORTS": C.CLAUDE_EFFORTS, "FLOOR_BY_MODEL": C.FLOOR_BY_MODEL}
        for name, value in theirs.items():
            self.assertEqual(ours[name], value, name)
        if "+ CLAUDE_TRIM + CLAUDE_IO + [" in src:
            self.assertIn('"-p", "--model", m["id"]] + CLAUDE_TRIM + CLAUDE_IO + ["--tools", "", "--effort", _eff]', src)

    def test_the_effort_floor_holds_and_only_a_higher_named_level_lifts_it(self):
        def effort(model_id, env):
            return C.ClaudeAdapter(PROGRAM, env=env).argv("m", "p", 60, dict(ROW, id=model_id))[0][-1]
        cases = [("claude-opus-5", {}, "medium"), ("claude-sonnet-5", {}, "medium"), ("claude-fable-5-1", {}, "high"),
                 ("claude-haiku-4-5-20251001", {}, "medium"), ("claude-x", {}, "medium"),
                 ("claude-opus-5", {"BROTHER_CLAUDE_EFFORT": "low"}, "medium"),
                 ("claude-sonnet-5", {"BROTHER_CLAUDE_EFFORT": "low"}, "medium"),
                 ("claude-opus-5", {"BROTHER_CLAUDE_EFFORT": "xhigh"}, "xhigh"),
                 ("claude-opus-5", {"BROTHER_CLAUDE_EFFORT": " MAX "}, "max"),
                 ("claude-fable-5-1", {"BROTHER_CLAUDE_EFFORT": "medium"}, "high"),
                 ("claude-fable-5-1", {"BROTHER_CLAUDE_EFFORT": "low"}, "high"),
                 ("claude-fable-5-1", {"BROTHER_CLAUDE_EFFORT": "xhigh"}, "xhigh"),
                 ("CLAUDE-FABLE-5-1", {}, "high"),
                 ("claude-opus-5", {"BROTHER_CLAUDE_EFFORT": ""}, "medium"),
                 ("claude-opus-5", {"BROTHER_CLAUDE_EFFORT": "   "}, "medium")]
        for model_id, env, want in cases:
            self.assertEqual(effort(model_id, env), want, (model_id, env))

    def test_the_effort_is_read_from_the_process_environment_at_call_time(self):
        adapter = C.ClaudeAdapter(PROGRAM)
        with mock.patch.dict(os.environ, {"BROTHER_CLAUDE_EFFORT": "high"}):
            self.assertEqual(adapter.argv("opus", "p", 60, ROW)[0][-1], "high")
        with mock.patch.dict(os.environ, {"BROTHER_CLAUDE_EFFORT": "bogus"}):
            with self.assertRaises(A.Refused):
                adapter.argv("opus", "p", 60, ROW)
        with mock.patch.dict(os.environ):
            os.environ.pop("BROTHER_CLAUDE_EFFORT", None)
            self.assertEqual(adapter.argv("opus", "p", 60, ROW)[0][-1], "medium")


class TheVerdictComesFromTheResultRecord(unittest.TestCase):
    """R-FX-31-3: a failure, a refusal or an empty answer is never an answer, and a refusal keeps its own status."""

    def setUp(self):
        self.adapter = C.ClaudeAdapter()

    def judge(self, result):
        return self.adapter.judge("opus", ROW, result)

    def test_an_answer_is_the_records_result_and_carries_its_cost(self):
        got = self.judge(ran(record(), stderr="a warning on stderr"))
        self.assertEqual((got.ok, got.answer, got.status, got.detail, got.cost_usd), (True, "12", "OK", "answered", 0.0086))

    def test_a_provider_refusal_is_never_an_answer(self):
        refusal = record(stop_reason="refusal", result="I can't help with that.")
        for code in (0, 1):
            got = self.judge(ran(refusal, returncode=code))
            self.assertEqual((got.ok, got.answer, got.status, got.detail),
                             (False, "", A.STATUS_PROVIDER_REFUSED, C.REFUSED_WHY), code)
            self.assertEqual(got.cost_usd, 0.0086, "a refused call was still billed")
        marked = self.judge(ran(record(), stderr="provider refused this prompt"))
        self.assertEqual((marked.ok, marked.status), (False, A.STATUS_PROVIDER_REFUSED))
        error = self.judge(ran(record(stop_reason="refusal", is_error=True, result="API Error")))
        self.assertEqual((error.ok, error.status), (False, A.STATUS_FAILED), "an error record is read first, as the breaker does")

    def test_an_error_record_an_empty_result_or_a_failed_exit_is_never_an_answer(self):
        error = self.judge(ran(record(is_error=True, result="API Error: 529")))
        self.assertEqual((error.ok, error.answer, error.status), (False, "", A.STATUS_FAILED))
        self.assertTrue(error.detail.startswith("the call returned an error result"), error.detail)
        for result in ("", "   \n", None, 12, ["12"], ABSENT):
            got = self.judge(ran(record(result=result)))
            self.assertEqual((got.ok, got.answer, got.status, got.detail),
                             (False, "", A.STATUS_FAILED, "the call returned an empty result"), repr(result))
        failed = self.judge(ran(record(), returncode=1, stderr="Error: session expired"))
        self.assertEqual((failed.ok, failed.status, failed.detail), (False, A.STATUS_FAILED, "exit 1: Error: session expired"))
        for stdout in ("12", "[]", "plain text", "null"):
            got = self.judge(ran(stdout))
            self.assertEqual((got.ok, got.answer, got.status, got.detail),
                             (False, "", A.STATUS_FAILED, C.NOT_THE_RECORD), stdout)

    def test_malformed_json_or_an_absent_cost_cannot_turn_an_empty_answer_into_success(self):
        for stdout in ('{"result": ', "{}", "", None, record(result="", total_cost_usd=ABSENT),
                       record(result=None, total_cost_usd=None), '{"result": "", "total_cost_usd": '):
            got = self.judge(ran(stdout))
            self.assertEqual((got.ok, got.answer, got.status), (False, "", A.STATUS_FAILED), repr(stdout))
            self.assertIs(got.cost_usd, A.NOT_MEASURED, repr(stdout))

    def test_a_repeated_member_is_not_the_record(self):
        stdout = '{"is_error": false, "result": "", "result": "12", "total_cost_usd": 9, "total_cost_usd": 0}'
        self.assertTrue(A.judge(A.adapter_for("claude"), "opus", ROW, ran(stdout)).ok,
                        "the contract alone reads the last member; this guard is the adapter's")
        got = self.judge(ran(stdout))
        self.assertEqual((got.ok, got.answer, got.status, got.cost_usd), (False, "", A.STATUS_FAILED, A.NOT_MEASURED))
        self.assertTrue(got.detail.startswith(C.NOT_THE_RECORD), got.detail)

    def test_a_record_nested_past_the_parser_limit_fails_without_a_crash(self):
        for stdout in ("[" * 100000, '{"result": ' + "[" * 100000):
            got = self.judge(ran(stdout))
            self.assertEqual((got.ok, got.answer, got.status, got.detail, got.cost_usd),
                             (False, "", A.STATUS_FAILED, C.NOT_THE_RECORD, A.NOT_MEASURED))


class TheCostIsTheRecordsOwnFigure(unittest.TestCase):
    """R-FX-31-2: an absent, invalid or unreadable cost is NOT_MEASURED; a reported, priced zero stays 0.0."""

    def setUp(self):
        self.adapter = C.ClaudeAdapter()

    def cost(self, stdout, usage=None):
        return self.adapter.cost(ran(stdout), usage)

    def test_an_absent_total_cost_usd_is_not_measured_and_never_zero(self):
        for stdout in (record(total_cost_usd=ABSENT), record(total_cost_usd=None)):
            self.assertIs(self.cost(stdout), A.NOT_MEASURED)
            self.assertIs(self.cost(stdout, {"input_tokens": 9}), A.NOT_MEASURED)
            self.assertIs(self.adapter.judge("opus", ROW, ran(stdout)).cost_usd, A.NOT_MEASURED)
            self.assertTrue(self.adapter.judge("opus", ROW, ran(stdout)).ok, "an absent cost does not fail an answer")

    def test_a_priced_zero_stays_a_measured_zero(self):
        stdout = record(total_cost_usd=0, usage={"input_tokens": 0, "output_tokens": 0})
        for got in (self.cost(stdout), self.cost(stdout, {"input_tokens": 0}),
                    self.adapter.judge("opus", ROW, ran(stdout)).cost_usd):
            self.assertEqual((got, type(got)), (0.0, float), "a measured zero stays a number, never NOT_MEASURED")
        self.assertEqual(self.cost(record(total_cost_usd=0.25)), 0.25)

    def test_malformed_output_is_not_measured(self):
        for stdout in ("", None, "not json", '{"total_cost_usd": 0.5', "[0.5]", "0.5", '"0.5"', "[" * 100000,
                       '{"total_cost_usd": 0.5, "total_cost_usd": 0}'):
            self.assertIs(self.cost(stdout), A.NOT_MEASURED, repr(stdout)[:60])

    def test_an_invalid_figure_is_not_measured(self):
        for value in ("0.5", True, False, -0.01, [0.5], {"usd": 0.5}):
            self.assertIs(self.cost(record(total_cost_usd=value)), A.NOT_MEASURED, repr(value))
        for raw in ("NaN", "Infinity", "-Infinity", "1e400", "1" + "0" * 400):
            self.assertIs(self.cost('{"result": "12", "total_cost_usd": %s}' % raw), A.NOT_MEASURED, raw[:20])

    def test_a_failed_call_keeps_the_cost_it_was_billed(self):
        result = ran(record(is_error=True, result="API Error", total_cost_usd=0.02), returncode=1)
        self.assertEqual(self.adapter.cost(result), 0.02)
        got = self.adapter.judge("opus", ROW, result)
        self.assertEqual((got.ok, got.status, got.cost_usd), (False, A.STATUS_FAILED, 0.02))
        self.assertIs(self.adapter.cost({"returncode": 0, "stdout": "", "stderr": record(total_cost_usd=9.0)}),
                      A.NOT_MEASURED, "stderr is never read for the bill")


class HostileInputIsRefused(unittest.TestCase):
    """Wrong types, unhashables, None, NaN, a str where a mapping is expected and a bool where an int is expected are
    refused by name (Refused before any process, ValueError for a verdict or a cost): never a TypeError, never accepted."""

    def test_hostile_input_is_refused_never_a_type_error_and_never_accepted(self):
        adapter = C.ClaudeAdapter(PROGRAM, env={})
        nan = float("nan")
        cases = [("opus", "p", 60, row) for row in (None, [], "claude", {1, 2}, {}, {"transport": "bridge", "id": "x"},
                                                    {"transport": ["claude"], "id": "claude-x"}, {"transport": "claude"},
                                                    {"transport": "claude", "id": None}, {"transport": "claude", "id": 5},
                                                    {"transport": "claude", "id": ["claude-x"]},
                                                    {"transport": "claude", "id": "--dangerously-skip-permissions"},
                                                    {"transport": "claude", "id": "claude x"},
                                                    {"transport": "claude", "id": ""})]
        cases += [(m, "p", 60, ROW) for m in (None, "", 5, True, ["opus"], b"opus", "--json", "a b", nan, {"opus"})]
        cases += [("opus", p, 60, ROW) for p in (None, "", "   ", 5, ["p"], b"p", nan, {"p": 1})]
        cases += [("opus", "p", t, ROW) for t in (None, True, False, 0, -1, 2.5, nan, "300", [300])]
        for model, prompt, timeout, row in cases:
            with self.assertRaises(A.Refused, msg=repr((model, prompt, timeout, row))):
                adapter.argv(model, prompt, timeout, row)
        plain = os.path.join(_TMP, "not-executable")
        with open(plain, "w") as fh:
            fh.write("#!/bin/sh\n")
        os.chmod(plain, 0o644)
        for program in (None, "", 5, True, b"/x", ["claude"], "relative/claude", os.path.join(_TMP, "missing"),
                        PROGRAM + "\0", _TMP, plain):
            with self.assertRaises(A.Refused, msg=repr(program)):
                C.ClaudeAdapter(program, env={}).argv("opus", "p", 60, ROW)
        for effort in ("bogus", "turbo", "minimal", 5, True, ["high"], nan):
            with self.assertRaises(A.Refused, msg=repr(effort)):
                C.ClaudeAdapter(PROGRAM, env={"BROTHER_CLAUDE_EFFORT": effort}).argv("opus", "p", 60, ROW)
        for env in (["x"], "BROTHER_CLAUDE_EFFORT=high", 5, True):
            with self.assertRaises(ValueError, msg=repr(env)):
                C.ClaudeAdapter(PROGRAM, env=env)
        good = ran(record())
        for row in (None, {}, {"transport": "bridge"}, [ROW], {"transport": None}, "claude", {1, 2}):
            with self.assertRaises(ValueError, msg=repr(row)):
                adapter.judge("opus", row, good)
        for result in (None, [], "12", 0, {1, 2}, ran(b"12"), ran(["12"]), ran(record(), stderr=5)):
            with self.assertRaises(ValueError, msg=repr(result)):
                adapter.judge("opus", ROW, result)
        for code in (False, True, None, "0", 0.0, [0], nan):
            with self.assertRaises(ValueError, msg=repr(code)):
                adapter.judge("opus", ROW, ran(record(), returncode=code))
        for model in (None, "", 5, ["opus"]):
            with self.assertRaises(ValueError, msg=repr(model)):
                adapter.judge(model, ROW, good)
        for result in (None, [], "x", 5, {1, 2}, ran(b"{}"), ran(5)):
            with self.assertRaises(ValueError, msg=repr(result)):
                adapter.cost(result)
        for usage in ("tokens", [], 5, True, {1, 2}):
            with self.assertRaises(ValueError, msg=repr(usage)):
                adapter.cost(good, usage)


if __name__ == "__main__":
    unittest.main(verbosity=2)

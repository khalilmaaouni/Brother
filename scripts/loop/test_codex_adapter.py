#!/usr/bin/env python3
"""Tests for scripts/loop/adapters/codex.py (FX-31.4).

Every case drives CodexAdapter.argv, CodexAdapter.judge or CodexAdapter.cost directly. Nothing here starts a process,
reads a home directory or writes outside a temporary folder: the program the argv names is a temporary file and the
code root is a temporary directory. R-FX-31-1: a row, model, prompt, timeout, program or root the adapter cannot carry
is refused before any process could start. R-FX-31-3: a failed exit, an empty answer or a provider refusal is never an
answer. M-FX-31-5: deleting the --sandbox read-only pair turns this suite red. Edge: an absent code root refuses the
call rather than falling back to an untrusted cwd.
"""
import ast
import os
import shutil
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.insert(0, HERE)
import adapters as A  # noqa: E402
from adapters import codex as X  # noqa: E402

ROW = {"id": "gpt-5-codex", "transport": "codex", "privacy": "public"}
PROGRAM = ""
ROOT = ""
_TMP = ""


def setUpModule():
    global PROGRAM, ROOT, _TMP
    _TMP = tempfile.mkdtemp(prefix="codex-adapter-test-")
    PROGRAM = os.path.join(_TMP, "codex")
    with open(PROGRAM, "w") as fh:
        fh.write("#!/bin/sh\nexit 3\n")
    os.chmod(PROGRAM, 0o755)
    ROOT = os.path.join(_TMP, "trusted-root")
    os.mkdir(ROOT)


def tearDownModule():
    shutil.rmtree(_TMP, ignore_errors=True)


def ran(stdout, returncode=0, stderr="", **extra):
    result = {"returncode": returncode, "stdout": stdout, "stderr": stderr}
    result.update(extra)
    return result


class TheArgvIsModelCallsCodexLine(unittest.TestCase):
    """model_call's codex command line moved behind CodexAdapter.argv with no element lost."""

    def setUp(self):
        self.adapter = X.CodexAdapter(PROGRAM, ROOT)

    def test_every_argv_element_is_model_calls_codex_line(self):
        argv, stdin, extra = self.adapter.argv("luna", "what is 7 plus 5", 300, ROW)
        self.assertEqual(argv, [PROGRAM, "exec", "-m", "gpt-5-codex", "-C", ROOT, "--sandbox", "read-only",
                                "what is 7 plus 5"])
        self.assertEqual(len(argv), 9)
        self.assertEqual(argv[0], PROGRAM, "the program the caller named, never a guessed one")
        self.assertEqual(argv[1], "exec", "the non-interactive subcommand")
        self.assertEqual(argv[2:4], ["-m", "gpt-5-codex"], "the row's id, not the registry name")
        self.assertEqual(argv[4:6], ["-C", ROOT], "the trusted code root")
        self.assertEqual(argv[6:8], ["--sandbox", "read-only"], "read only, always")
        self.assertEqual(argv[8], "what is 7 plus 5", "the prompt is the last element")
        self.assertTrue(all(isinstance(a, str) for a in argv))
        self.assertEqual((stdin, extra), ("", None))
        self.assertEqual(self.adapter.transport, "codex")

    def test_the_sandbox_is_read_only_and_given_once(self):
        argv, _stdin, _ = self.adapter.argv("luna", "p", 60, ROW)
        self.assertEqual(argv.count("--sandbox"), 1)
        self.assertEqual(argv[argv.index("--sandbox") + 1], "read-only")
        self.assertLess(argv.index("--sandbox"), argv.index("p"), "the sandbox is set before the prompt")
        self.assertEqual(argv.count("-C"), 1)
        self.assertEqual(argv[argv.index("-C") + 1], ROOT)

    def test_the_input_is_closed_and_the_prompt_never_rides_on_it(self):
        for prompt in ("what is 7 plus 5", "two\nlines", " -leading space is text", "x" * 5000):
            argv, stdin, _ = self.adapter.argv("luna", prompt, 60, ROW)
            self.assertIsInstance(stdin, str, "None would leave the input to the runner")
            self.assertEqual(stdin, "", "codex hangs reading an open stdin")
            self.assertEqual(argv[-1], prompt)
            self.assertEqual(argv.count(prompt), 1)

    def test_the_line_is_model_calls_own(self):
        with open(os.path.join(HERE, "model_call.py"), encoding="utf-8") as fh:
            tree = ast.parse(fh.read())
        branch = [node for fn in tree.body if isinstance(fn, ast.FunctionDef) and fn.name == "_argv"
                  for node in ast.walk(fn) if isinstance(node, ast.If) and isinstance(node.test, ast.Compare)
                  and any(isinstance(c, ast.Constant) and c.value == "codex" for c in node.test.comparators)]
        if not branch:
            self.skipTest("NO-DATA: model_call carries no codex branch of its own to compare")
        ret = next(n for n in branch[0].body if isinstance(n, ast.Return))
        line, stdin, extra = ret.value.elts
        theirs = [e.value if isinstance(e, ast.Constant) else ast.unparse(e) for e in line.elts]
        self.assertEqual(theirs, ["program or R.codex_bin()", "exec", "-m", "m['id']", "-C", "_codex_root()",
                                  "--sandbox", "read-only", "prompt"])
        self.assertEqual((stdin.value, extra.value), ("", None))
        ours = self.adapter.argv("luna", "PROMPT", 60, ROW)[0]
        names = {PROGRAM: "program or R.codex_bin()", "gpt-5-codex": "m['id']", ROOT: "_codex_root()",
                 "PROMPT": "prompt"}
        self.assertEqual([names.get(a, a) for a in ours], theirs)

    def test_the_timeout_is_checked_and_never_put_on_the_line(self):
        for timeout in (1, 60, 300, 3600):
            argv, _stdin, _ = self.adapter.argv("luna", "p", timeout, ROW)
            self.assertNotIn(str(timeout), argv[:-1], timeout)
        for timeout in (0, -1, True):
            with self.assertRaises(A.Refused, msg=repr(timeout)):
                self.adapter.argv("luna", "p", timeout, ROW)


class AnAbsentCodeRootRefuses(unittest.TestCase):
    """Edge: no trusted root refuses the call; the child's cwd is never the fallback."""

    def setUp(self):
        self.cwd = os.getcwd()
        self.elsewhere = tempfile.mkdtemp(prefix="codex-adapter-cwd-", dir=_TMP)
        os.chdir(self.elsewhere)

    def tearDown(self):
        os.chdir(self.cwd)

    def test_an_absent_code_root_refuses_rather_than_falling_back_to_the_cwd(self):
        missing = os.path.join(_TMP, "missing-root")
        a_file = os.path.join(_TMP, "a-file")
        with open(a_file, "w") as fh:
            fh.write("not a directory\n")
        for root in (None, "", ".", "trusted-root", os.path.basename(self.elsewhere), missing, a_file,
                     ROOT + "\0", b"/tmp", [ROOT], {"root": ROOT}, 5, True, float("nan")):
            with self.assertRaises(A.Refused, msg=repr(root)) as caught:
                X.CodexAdapter(PROGRAM, root).argv("luna", "p", 60, ROW)
            self.assertIn("code root", str(caught.exception))
        self.assertEqual(X.CodexAdapter(PROGRAM, self.elsewhere).argv("luna", "p", 60, ROW)[0][5], self.elsewhere,
                         "a root named by absolute path is carried as named")

    def test_the_root_is_checked_when_the_line_is_built(self):
        root = tempfile.mkdtemp(prefix="codex-adapter-gone-", dir=_TMP)
        adapter = X.CodexAdapter(PROGRAM, root)
        self.assertEqual(adapter.argv("luna", "p", 60, ROW)[0][5], root)
        os.rmdir(root)
        with self.assertRaises(A.Refused):
            adapter.argv("luna", "p", 60, ROW)


class TheVerdictFailsEveryNonAnswer(unittest.TestCase):
    """R-FX-31-3: a failed exit, an empty body at exit zero or a provider refusal is never an answer."""

    def setUp(self):
        self.adapter = X.CodexAdapter()

    def judge(self, result):
        return self.adapter.judge("luna", ROW, result)

    def test_an_answer_is_stdout_at_exit_zero(self):
        got = self.judge(ran("12\n", stderr="Reading additional input from stdin...\n"))
        self.assertEqual((got.ok, got.answer, got.status, got.detail, got.cost_usd),
                         (True, "12", A.STATUS_OK, "answered", A.NOT_MEASURED))

    def test_a_nonzero_exit_is_refused_as_an_answer(self):
        for code, err, why in ((1, "Not inside a trusted directory\n", "exit 1: Not inside a trusted directory"),
                               (2, "error: unexpected argument '--x'\n", "exit 2: error: unexpected argument '--x'"),
                               (-9, "", "exit -9: 12"), (124, "", "exit 124: 12")):
            got = self.judge(ran("12", returncode=code, stderr=err))
            self.assertEqual((got.ok, got.status, got.detail), (False, A.STATUS_FAILED, why), code)
        silent = self.judge(ran("", returncode=1))
        self.assertEqual((silent.ok, silent.detail), (False, "exit 1: no output"))

    def test_an_empty_answer_at_exit_zero_fails(self):
        for stdout in ("", "  \n\t", None):
            got = self.judge(ran(stdout))
            self.assertEqual((got.ok, got.answer, got.status, got.detail),
                             (False, "", A.STATUS_FAILED, "exit 0 with an EMPTY answer"), repr(stdout))

    def test_a_provider_refusal_is_its_own_status_and_never_an_answer(self):
        for result in (ran("12", stderr="provider refused this prompt"), ran("Provider_Refused: policy"),
                       ran("", returncode=1, stderr="provider refusal")):
            got = self.judge(result)
            self.assertEqual((got.ok, got.status), (False, A.STATUS_PROVIDER_REFUSED), result)


class TheCostIsNeverReadFromTheAnswer(unittest.TestCase):
    """R-FX-31-2: codex prints no bill, so an absent cost is NOT_MEASURED and a priced zero stays 0.0."""

    def setUp(self):
        self.adapter = X.CodexAdapter()

    def test_an_absent_cost_is_not_measured_and_never_zero(self):
        for result in (ran("12"), ran("12", cost_usd=None), ran("", returncode=1)):
            self.assertIs(self.adapter.cost(result), A.NOT_MEASURED, result)
            self.assertIs(self.adapter.cost(result, {"input_tokens": 9}), A.NOT_MEASURED, result)
            self.assertIs(self.adapter.judge("luna", ROW, result).cost_usd, A.NOT_MEASURED, result)

    def test_a_priced_zero_stays_a_measured_zero(self):
        for got in (self.adapter.cost(ran("12", cost_usd=0)), self.adapter.cost(ran("12", cost_usd=0.0), {}),
                    self.adapter.judge("luna", ROW, ran("12", cost_usd=0)).cost_usd):
            self.assertEqual((got, type(got)), (0.0, float))
        self.assertEqual(self.adapter.cost(ran("12", cost_usd=0.25)), 0.25)

    def test_the_answer_and_the_progress_are_never_read_for_a_figure(self):
        for out, err in (('{"total_cost_usd": 0.5}', ""), ("12", "[billed] usd=0.5000 attempts=1 known=yes"),
                         ("cost_usd=0", "cost_usd: 0.0")):
            self.assertIs(self.adapter.cost(ran(out, stderr=err)), A.NOT_MEASURED, (out, err))


class HostileInputIsRefused(unittest.TestCase):
    """Wrong types, unhashables, None, NaN, a str where a mapping is expected and a bool where an int is expected are
    refused by name (Refused before any process, ValueError for a verdict or a cost): never a TypeError, never accepted."""

    def test_hostile_input_is_refused_never_a_type_error_and_never_accepted(self):
        adapter = X.CodexAdapter(PROGRAM, ROOT)
        nan = float("nan")
        rows = (None, [], "codex", {1, 2}, {}, [ROW], {"transport": "claude", "id": "gpt-5-codex"},
                {"transport": ["codex"], "id": "gpt-5-codex"}, {"transport": "codex"}, {"transport": "codex", "id": None},
                {"transport": "codex", "id": 5}, {"transport": "codex", "id": ["gpt-5-codex"]},
                {"transport": "codex", "id": "--dangerously-bypass-approvals-and-sandbox"},
                {"transport": "codex", "id": "gpt 5"}, {"transport": "codex", "id": ""})
        cases = [("luna", "p", 60, row) for row in rows]
        cases += [(m, "p", 60, ROW) for m in (None, "", 5, True, ["luna"], b"luna", "--full-auto", "a b", nan, {"luna"})]
        cases += [("luna", p, 60, ROW) for p in (None, "", "   ", 5, True, ["p"], b"p", nan, {"p": 1}, "a\0b",
                                                 "--sandbox=danger-full-access", "-c", "-", "--full-auto do it")]
        cases += [("luna", "p", t, ROW) for t in (None, True, False, 0, -1, 2.5, nan, "300", [300], {300})]
        for model, prompt, timeout, row in cases:
            with self.assertRaises(A.Refused, msg=repr((model, prompt, timeout, row))):
                adapter.argv(model, prompt, timeout, row)
        plain = os.path.join(_TMP, "not-executable")
        with open(plain, "w") as fh:
            fh.write("#!/bin/sh\n")
        os.chmod(plain, 0o644)
        for program in (None, "", 5, True, b"/x", ["codex"], "codex", "relative/codex", os.path.join(_TMP, "missing"),
                        PROGRAM + "\0", _TMP, plain, nan):
            with self.assertRaises(A.Refused, msg=repr(program)):
                X.CodexAdapter(program, ROOT).argv("luna", "p", 60, ROW)
        good = ran("12")
        for row in (None, {}, {"transport": "claude"}, [ROW], {"transport": None}, "codex", {1, 2}):
            with self.assertRaises(ValueError, msg=repr(row)):
                adapter.judge("luna", row, good)
        for result in (None, [], "12", 0, {1, 2}, ran(b"12"), ran(["12"]), ran("12", stderr=5)):
            with self.assertRaises(ValueError, msg=repr(result)):
                adapter.judge("luna", ROW, result)
        for code in (False, True, None, "0", 0.0, [0], nan):
            with self.assertRaises(ValueError, msg=repr(code)):
                adapter.judge("luna", ROW, ran("12", returncode=code))
        for model in (None, "", 5, ["luna"]):
            with self.assertRaises(ValueError, msg=repr(model)):
                adapter.judge(model, ROW, good)
        for result in (None, [], "x", 5, {1, 2}):
            with self.assertRaises(ValueError, msg=repr(result)):
                adapter.cost(result)
        for value in ("0.5", True, False, nan, float("inf"), [0.5], {"usd": 0.5}):
            with self.assertRaises(ValueError, msg=repr(value)):
                adapter.cost(ran("12", cost_usd=value))
            with self.assertRaises(ValueError, msg=repr(value)):
                adapter.judge("luna", ROW, ran("12", cost_usd=value))
        for usage in ("tokens", [], 5, True, {1, 2}):
            with self.assertRaises(ValueError, msg=repr(usage)):
                adapter.cost(good, usage)


if __name__ == "__main__":
    unittest.main(verbosity=2)

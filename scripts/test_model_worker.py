"""Calibration for scripts/model_worker.py.

No network, no real claude: MODEL_WORKER_CMD points every case at a tiny
stub script written to a tempdir, mirroring bm_worker_spawn's own injected-
runner style but at the process boundary rather than the function boundary,
since model_worker's contract is a whole subprocess, not a function.
"""
import json
import os
import shlex
import stat
import subprocess
import sys
import tempfile
import textwrap
import threading
import unittest
from http.server import HTTPServer
from unittest import mock

# E100: one sandbox for every temp tree this process makes, removed at exit.
import os as _e100_os, sys as _e100_sys  # noqa: E402
_e100_sys.path.append(_e100_os.path.join(
    _e100_os.path.dirname(_e100_os.path.abspath(__file__)), '.'))
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

HERE = os.path.dirname(os.path.abspath(__file__))
WORKER = os.path.join(HERE, "model_worker.py")
sys.path.insert(0, HERE)
# The stub provider and the Codex binary path are codex_smoke's, not spelled a
# second time here: TheCodexTurnsSandbox below drives a real `codex exec`.
import codex_smoke  # noqa: E402


def write_stub(tmpdir, body):
    path = os.path.join(tmpdir, "stub_model.py")
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(textwrap.dedent(body))
    os.chmod(path, os.stat(path).st_mode | stat.S_IEXEC)
    return path


def run_worker(stdin_text, cwd, env_extra=None):
    env = dict(os.environ)
    env.pop("MODEL_WORKER_CMD", None)
    env.pop("MODEL_WORKER_TIMEOUT_S", None)
    if env_extra:
        env.update(env_extra)
    return subprocess.run([sys.executable, WORKER], input=stdin_text, cwd=cwd,
                          env=env, capture_output=True, text=True, timeout=60)


def run_git(args, cwd):
    """One git command, never raising: every caller reads the result."""
    return subprocess.run(["git"] + args, cwd=cwd, capture_output=True,
                          text=True, timeout=60)


def git_init_at(cwd):
    """git_init's argument-taking twin, for a repository this file builds
    somewhere other than the case's own cwd."""
    for args in (["init", "-q", "-b", "main"], ["config", "user.email",
                                                "a@b.c"],
                 ["config", "user.name", "t"]):
        run_git(args, cwd)


def git_init(cwd):
    for args in (["init", "-q"], ["config", "user.email", "a@b.c"],
                 ["config", "user.name", "t"]):
        subprocess.run(["git"] + args, cwd=cwd, capture_output=True,
                       text=True, timeout=30, check=True)


class ModelWorkerSuccess(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="mw-")
        git_init(self.tmp)
        self.stub = write_stub(self.tmp, """
            import sys
            sys.stdin.read()
            with open("created.txt", "w") as fh:
                fh.write("made it\\n")
            print("did the thing")
        """)

    def test_creates_file_and_reports_artifact(self):
        brief = json.dumps({"id": "U1", "objective": "make a file",
                            "writes": ["created.txt"]})
        proc = run_worker(brief, self.tmp,
                          {"MODEL_WORKER_CMD": "%s %s" % (shlex.quote(sys.executable), shlex.quote(self.stub))})
        self.assertEqual(proc.returncode, 0, proc.stderr)
        out = json.loads(proc.stdout)
        self.assertIn("worker_claim", out)
        self.assertTrue(out["worker_claim"])
        self.assertIn("created.txt", out["artifacts"])
        self.assertEqual(out["cost"], {})


class ModelWorkerNonzeroExit(unittest.TestCase):
    def test_nonzero_model_exit_is_unavailable(self):
        tmp = tempfile.mkdtemp(prefix="mw-")
        stub = write_stub(tmp, """
            import sys
            sys.stdin.read()
            sys.exit(7)
        """)
        brief = json.dumps({"id": "U2", "objective": "fail"})
        proc = run_worker(brief, tmp,
                          {"MODEL_WORKER_CMD": "%s %s" % (shlex.quote(sys.executable), shlex.quote(stub))})
        self.assertNotEqual(proc.returncode, 0)
        # stdout is not required to be JSON on the unavailable path; the
        # contract only promises one clean JSON object on success.
        self.assertEqual(proc.stdout.strip(), "")


class ModelWorkerGarbageStdin(unittest.TestCase):
    def test_garbage_stdin_exits_nonzero_with_readable_stderr(self):
        tmp = tempfile.mkdtemp(prefix="mw-")
        proc = run_worker("not json at all {{{", tmp)
        self.assertNotEqual(proc.returncode, 0)
        self.assertTrue(proc.stderr.strip())


class ModelWorkerDoneCheck(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="mw-")
        self.stub = write_stub(self.tmp, """
            import sys
            sys.stdin.read()
            print("ok")
        """)
        self.env = {"MODEL_WORKER_CMD": "%s %s" % (shlex.quote(sys.executable), shlex.quote(self.stub))}

    def test_passing_done_check_reports_exit_code_0(self):
        brief = json.dumps({"id": "U3", "objective": "x", "done_check": "true"})
        proc = run_worker(brief, self.tmp, self.env)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        out = json.loads(proc.stdout)
        self.assertIn("exit code: 0", out["worker_claim"])

    def test_failing_done_check_reports_the_code_but_worker_still_exits_0(self):
        brief = json.dumps({"id": "U4", "objective": "x", "done_check": "false"})
        proc = run_worker(brief, self.tmp, self.env)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        out = json.loads(proc.stdout)
        self.assertIn("exit code: 1", out["worker_claim"])

    def test_missing_done_check_is_named_as_such(self):
        brief = json.dumps({"id": "U5", "objective": "x"})
        proc = run_worker(brief, self.tmp, self.env)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        out = json.loads(proc.stdout)
        self.assertIn("no done_check", out["worker_claim"])


class ModelWorkerDefensiveFields(unittest.TestCase):
    def test_missing_fields_never_crash_the_prompt_builder(self):
        import model_worker as mw  # noqa: PLC0415 (path set at module import time)
        prompt = mw.build_prompt({})
        self.assertIn("Unit id:", prompt)
        self.assertIn("Objective:", prompt)


def _mw():
    """model_worker, imported lazily: sys.path gains HERE only when this
    file runs as __main__ (see the bottom of the file), which is how
    ModelWorkerDefensiveFields above already reaches it."""
    import model_worker  # noqa: PLC0415
    return model_worker


class TestVendorAdapterSelection(unittest.TestCase):
    """C3: two clients, one worker. The argv and the stdout parser are chosen
    by the running client, and each is driven with a stub so neither the
    claude CLI nor codex needs to be installed for this to mean anything."""

    def test_claude_argv_is_unchanged_apart_from_the_sr1_fallback_flag(self):
        """UPDATED for SR-1: the base Claude invocation is still byte for
        byte what it was before the adapter existed, but the real argv now
        also carries the native fallback chain by default."""
        self.assertEqual(_mw()._default_argv({"BROTHER_MODEL_CLIENT": "claude"}),
                         ["claude", "-p", "--output-format", "json",
                          "--permission-mode", "acceptEdits",
                          "--fallback-model", "sonnet,haiku"])

    def test_codex_argv_uses_only_flags_quoted_from_its_own_help(self):
        self.assertEqual(_mw()._default_argv({"BROTHER_MODEL_CLIENT": "codex"}),
                         ["codex", "exec", "--json", "--sandbox",
                          "workspace-write"])

    def test_an_unidentified_host_still_runs_the_claude_cli(self):
        """NO-DATA on the client is reported by brother_paths, never turned
        into a refusal here: this function has to return an argv, and the
        pre-C3 answer for an unknown host was the claude CLI."""
        self.assertEqual(_mw().model_client({}), "claude")

    def test_the_explicit_override_beats_a_detected_client(self):
        env = {"BROTHER_MODEL_CLIENT": "codex", "CLAUDECODE": "1"}
        self.assertEqual(_mw().model_client(env), "codex")

    def test_an_unrecognised_override_is_ignored_not_trusted(self):
        self.assertEqual(_mw().model_client({"BROTHER_MODEL_CLIENT": "cursor",
                                          "CLAUDECODE": "1"}), "claude")


class TestCodexOutputParser(unittest.TestCase):
    """The Codex stdout shape is JSONL, not one object. Every case here is
    about the parser refusing to invent a number it did not read."""

    def _line(self, obj):
        return json.dumps(obj)

    def test_last_agent_message_and_token_counts_are_read(self):
        raw = "\n".join([
            self._line({"type": "thread.started", "thread_id": "t1"}),
            self._line({"type": "item.completed.agent_message",
                        "text": "first pass"}),
            self._line({"type": "item.completed.agent_message",
                        "text": "the real answer"}),
            self._line({"type": "token_count",
                        "info": {"input_tokens": 120, "output_tokens": 34,
                                 "cached_input_tokens": 90}}),
        ])
        claim, usage = _mw()._parse_codex_output(raw)
        self.assertEqual(claim, "the real answer")
        self.assertEqual(usage, {"tokens_in": 120, "tokens_out": 34,
                                 "tokens_cached": 90})

    def test_events_without_token_counts_report_no_usage_not_zero(self):
        raw = self._line({"type": "agent_message", "text": "done"})
        claim, usage = _mw()._parse_codex_output(raw)
        self.assertEqual(claim, "done")
        self.assertIsNone(usage,
                          "a worker that did not read a token count must "
                          "report NO usage, never a fabricated zero")

    def test_plain_text_from_a_stub_falls_back_to_the_raw_claim(self):
        claim, usage = _mw()._parse_codex_output("ok\n")
        self.assertEqual(claim, "ok")
        self.assertIsNone(usage)

    def test_empty_stdout_is_named_not_blank(self):
        claim, usage = _mw()._parse_codex_output("")
        self.assertEqual(claim, "(model produced no stdout)")
        self.assertIsNone(usage)

    def test_a_malformed_line_between_good_ones_is_skipped(self):
        raw = "\n".join(["{not json", self._line({"type": "agent_message",
                                                  "text": "survived"})])
        claim, usage = _mw()._parse_codex_output(raw)
        self.assertEqual(claim, "survived")
        self.assertIsNone(usage)

    def test_the_claude_parser_is_untouched_by_any_of_this(self):
        claim, usage = _mw()._parse_model_output(json.dumps(
            {"result": "hello", "usage": {"input_tokens": 5,
                                          "output_tokens": 6,
                                          "cache_read_input_tokens": 7}}))
        self.assertEqual(claim, "hello")
        self.assertEqual(usage, {"tokens_in": 5, "tokens_out": 6,
                                 "tokens_cached": 7})

    def test_the_cache_creation_count_is_forwarded_when_the_cli_sends_it(self):
        """E92: the CLI's cache_creation_input_tokens is the count
        build_cost_block needs to divide by a real denominator, so it is
        renamed and forwarded rather than dropped."""
        _claim, usage = _mw()._parse_model_output(json.dumps(
            {"result": "hello", "usage": {"input_tokens": 2,
                                          "output_tokens": 6,
                                          "cache_read_input_tokens": 22972,
                                          "cache_creation_input_tokens":
                                              70272}}))
        self.assertEqual(usage["tokens_cache_write"], 70272)

    def test_an_answer_without_it_forwards_no_cache_write_at_all(self):
        """The other way: absent from the answer, the key is ABSENT from the
        usage dict, never a zero, so the cost block can say NO-DATA instead
        of dividing by a count nobody measured."""
        _claim, usage = _mw()._parse_model_output(json.dumps(
            {"result": "hello", "usage": {"input_tokens": 2,
                                          "output_tokens": 6,
                                          "cache_read_input_tokens": 9}}))
        self.assertNotIn("tokens_cache_write", usage)

    def test_the_codex_adapter_genuinely_lacks_the_field(self):
        """The NO-DATA in the cost block has to be true of a real adapter,
        not only of a fixture: codex's own field map names no cache-creation
        count, which is why a codex run cannot print a share."""
        self.assertNotIn("tokens_cache_write", _mw().CODEX_USAGE_FIELD_MAP)


class _FakeCompleted(object):
    """A stand-in for subprocess.CompletedProcess, for run_model tests that
    inject their own runner rather than launch a real subprocess."""
    def __init__(self, stdout="", stderr="", returncode=0):
        self.stdout = stdout
        self.stderr = stderr
        self.returncode = returncode


class TestFallbackModelArgv(unittest.TestCase):
    """SR-1(b): the native fallback chain, --fallback-model, is on the real
    Claude argv by default and can be turned off with the empty string."""

    def test_default_env_appends_sonnet_haiku(self):
        self.assertEqual(_mw()._claude_argv({}),
                         _mw().CLAUDE_ARGV + ["--fallback-model",
                                              "sonnet,haiku"])

    def test_empty_env_value_omits_the_flag_entirely(self):
        self.assertEqual(_mw()._claude_argv({"BROTHER_FALLBACK_MODELS": ""}),
                         _mw().CLAUDE_ARGV)

    def test_a_custom_env_value_is_used_verbatim(self):
        self.assertEqual(
            _mw()._claude_argv({"BROTHER_FALLBACK_MODELS": "opus"}),
            _mw().CLAUDE_ARGV + ["--fallback-model", "opus"])

    def test_the_main_model_is_dropped_from_the_chain(self):
        """The binary refuses at startup: "Fallback model cannot be the same
        as the main model". A sonnet main model keeps only haiku."""
        for main in ("sonnet", "claude-sonnet-5"):
            self.assertEqual(_mw()._claude_argv({"ANTHROPIC_MODEL": main}),
                             _mw().CLAUDE_ARGV + ["--fallback-model", "haiku"],
                             main)

    def test_a_chain_left_empty_omits_the_flag(self):
        self.assertEqual(
            _mw()._claude_argv({"ANTHROPIC_MODEL": "sonnet",
                                "BROTHER_FALLBACK_MODELS": "sonnet"}),
            _mw().CLAUDE_ARGV)


class TestFirstByteTimeout(unittest.TestCase):
    """SR-1(c): the child gets CLAUDE_STREAM_FIRST_BYTE_TIMEOUT_MS=120000
    unless the caller's own environment already names one."""

    def test_default_value_set_when_the_caller_env_lacks_it(self):
        with mock.patch.dict(os.environ, {}, clear=False):
            os.environ.pop("CLAUDE_STREAM_FIRST_BYTE_TIMEOUT_MS", None)
            seen = {}

            def runner(_argv, **kwargs):
                seen.update(kwargs)
                return _FakeCompleted(stdout=json.dumps({"result": "hi"}))

            ok, claim, _usage = _mw().run_model("prompt", runner=runner)
            self.assertTrue(ok, claim)
            self.assertEqual(
                seen["env"]["CLAUDE_STREAM_FIRST_BYTE_TIMEOUT_MS"], "120000")

    def test_a_preset_value_is_respected(self):
        with mock.patch.dict(os.environ,
                             {"CLAUDE_STREAM_FIRST_BYTE_TIMEOUT_MS": "5000"}):
            seen = {}

            def runner(_argv, **kwargs):
                seen.update(kwargs)
                return _FakeCompleted(stdout=json.dumps({"result": "hi"}))

            ok, claim, _usage = _mw().run_model("prompt", runner=runner)
            self.assertTrue(ok, claim)
            self.assertEqual(
                seen["env"]["CLAUDE_STREAM_FIRST_BYTE_TIMEOUT_MS"], "5000")


class TheFallbackInheritsTheUnitDeadline(unittest.TestCase):
    """BC1 (founder ruling 2026-09-11, a shared unit deadline that fallback
    inherits): --fallback-model runs inside the ONE claude subprocess, so the
    same timeout=_timeout_s() bounds the main model and every fallback. No
    second process is started for the fallback."""

    def test_one_call_carries_the_fallback_flag_and_the_unit_timeout(self):
        env = {"BROTHER_MODEL_CLIENT": "claude", "MODEL_WORKER_TIMEOUT_S": "77"}
        with mock.patch.dict(os.environ, env):
            for name in ("MODEL_WORKER_CMD", "BROTHER_FALLBACK_MODELS",
                         "ANTHROPIC_MODEL"):
                os.environ.pop(name, None)
            calls = []

            def runner(argv, **kwargs):
                calls.append((list(argv), kwargs))
                return _FakeCompleted(stdout=json.dumps({"result": "did it"}))

            def no_second_spawn(*_a, **_k):
                raise AssertionError("a second process was started")

            mw = _mw()
            with mock.patch.object(mw.subprocess, "run", no_second_spawn), \
                    mock.patch.object(mw.subprocess, "Popen", no_second_spawn):
                ok, claim, _usage = mw.run_model("prompt", runner=runner)
            self.assertTrue(ok, claim)
            self.assertEqual(len(calls), 1, calls)
            argv, kwargs = calls[0]
            self.assertIn("--fallback-model", argv)
            self.assertEqual(argv[argv.index("--fallback-model") + 1],
                             "sonnet,haiku")
            self.assertEqual(kwargs["timeout"], 77)
            self.assertEqual(kwargs["timeout"], mw._timeout_s())

    def test_fallback_uses_remaining_unit_allowance(self):
        with mock.patch.dict(os.environ, {"MODEL_WORKER_TIMEOUT_S": "77",
                                          "BROTHER_UNIT_TIME_LEFT_S": "3.25"}):
            calls = []
            def runner(argv, **kwargs):
                calls.append(kwargs["timeout"])
                return _FakeCompleted(stdout=json.dumps({"result": "did it"}))
            _mw().run_model("prompt", runner=runner)
            self.assertEqual(calls, [3.25])

    def test_invalid_or_spent_unit_allowance_never_spawns(self):
        for value in ("0", "-1", "nan", "inf", "bad"):
            with self.subTest(value=value), mock.patch.dict(
                    os.environ, {"BROTHER_UNIT_TIME_LEFT_S": value}):
                runner = mock.Mock()
                ok, reason, _ = _mw().run_model("prompt", runner=runner)
                self.assertFalse(ok)
                self.assertIn("failure_class=timeout", reason)
                runner.assert_not_called()


class TestClassifyFailure(unittest.TestCase):
    """SR-1(d): classify_failure() reads exactly one class from what the
    call produced, never invents a class from nothing."""

    def test_timed_out_is_timeout_even_with_no_text(self):
        self.assertEqual(_mw().classify_failure(timed_out=True), "timeout")

    def test_empty_is_empty(self):
        self.assertEqual(_mw().classify_failure(empty=True), "empty")

    def test_timeout_wins_over_empty(self):
        self.assertEqual(
            _mw().classify_failure(timed_out=True, empty=True), "timeout")

    def test_the_real_session_limit_message_classes_rate_limit(self):
        """Observed verbatim on 2026-09-11 in a dead subagent's error: "You've
        hit your session limit · resets 12:50am (Asia/Tokyo) (error type
        rate_limit, HTTP 429, ...)". The binary also carries "You've hit your
        limit" and the vendor type rate_limit_error."""
        for text in ("You've hit your session limit · resets 12:50am (Asia/Tokyo)"
                     " (error type rate_limit, HTTP 429)",
                     "You've hit your limit", "rate_limit_error"):
            self.assertEqual(_mw().classify_failure(text=text), "rate_limit",
                             text)

    def test_every_limit_phrase_the_installed_cli_carries_classes_rate_limit(self):
        """PASS3 backend review F1, 2026-09-11: `strings` over the installed
        claude binary shows these three besides "You've hit your limit"; each
        read as "other", so the breaker never paused on them."""
        for text in ("You've hit your fast limit",
                     "You've hit your monthly limit",
                     "You've hit your monthly spend limit · resets Oct 1"):
            self.assertEqual(_mw().classify_failure(text=text), "rate_limit",
                             text)

    def test_a_bare_number_in_an_answer_is_not_a_vendor_error(self):
        """A count, a line number, a hash or a path must never read as a
        rate limit or an overload: only an HTTP status or a vendor error
        type does."""
        for text in ("processed 429 files", "see line 529 of the log",
                     "commit 3f429ab0e failed", "sha256 e529c4290d1f",
                     "/tmp/run-4290/out.log"):
            self.assertEqual(_mw().classify_failure(text=text), "other", text)

    def test_429_text_classes_rate_limit(self):
        self.assertEqual(
            _mw().classify_failure(text="HTTP 429 too many requests"),
            "rate_limit")

    def test_usage_limit_text_classes_rate_limit(self):
        self.assertEqual(
            _mw().classify_failure(text="you have hit your usage limit"),
            "rate_limit")

    def test_overloaded_text_classes_overloaded(self):
        self.assertEqual(
            _mw().classify_failure(text="the model is overloaded right now"),
            "overloaded")

    def test_529_text_classes_overloaded(self):
        for text in ("HTTP 529", "overloaded_error"):
            self.assertEqual(_mw().classify_failure(text=text), "overloaded",
                             text)

    def test_unrecognised_text_classes_other(self):
        self.assertEqual(_mw().classify_failure(text="a disk fell over"),
                         "other")


class TestRunModelEmptyIsFailure(unittest.TestCase):
    """SR-1(a): a model that answered with nothing is a failure, never a
    claim, for both vendors."""

    def test_empty_claude_stdout_is_a_failure_classed_empty(self):
        with mock.patch.dict(os.environ, {"BROTHER_MODEL_CLIENT": "claude"}):
            ok, reason, usage = _mw().run_model(
                "prompt", runner=lambda *_a, **_k: _FakeCompleted(stdout=""))
        self.assertFalse(ok)
        self.assertIn("failure_class=empty", reason)
        self.assertIsNone(usage)

    def test_is_error_true_is_a_failure(self):
        with mock.patch.dict(os.environ, {"BROTHER_MODEL_CLIENT": "claude"}):
            out = json.dumps({"is_error": True,
                              "result": "You've hit your session limit"})
            ok, reason, usage = _mw().run_model(
                "prompt",
                runner=lambda *_a, **_k: _FakeCompleted(stdout=out))
        self.assertFalse(ok)
        self.assertIn("failure_class=rate_limit", reason)
        self.assertIsNone(usage)

    def test_a_blank_result_is_a_failure(self):
        with mock.patch.dict(os.environ, {"BROTHER_MODEL_CLIENT": "claude"}):
            out = json.dumps({"result": "   "})
            ok, reason, usage = _mw().run_model(
                "prompt",
                runner=lambda *_a, **_k: _FakeCompleted(stdout=out))
        self.assertFalse(ok)
        self.assertIn("failure_class=empty", reason)
        self.assertIsNone(usage)

    def test_empty_codex_stdout_is_a_failure_classed_empty(self):
        with mock.patch.dict(os.environ, {"BROTHER_MODEL_CLIENT": "codex"}):
            ok, reason, usage = _mw().run_model(
                "prompt", runner=lambda *_a, **_k: _FakeCompleted(stdout=""))
        self.assertFalse(ok)
        self.assertIn("failure_class=empty", reason)
        self.assertIsNone(usage)

    def _real(self, out):
        with mock.patch.dict(os.environ, {"BROTHER_MODEL_CLIENT": "claude"}):
            os.environ.pop("MODEL_WORKER_CMD", None)
            return _mw().run_model(
                "prompt", runner=lambda *_a, **_k: _FakeCompleted(stdout=out))

    def test_the_review_shapes_are_failures_from_the_real_client(self):
        """The 2026-09-11 review's C1 and M2 inputs, each with its class:
        no usable answer is empty, a foreign shape is other."""
        cases = [(json.dumps({"ok": True, "reason": "x"}), "other"),
                 (json.dumps({"is_error": False}), "empty"),
                 (json.dumps({"result": None}), "empty"),
                 ("plain text on stdout, exit 0", "other")]
        for out, cls in cases:
            ok, reason, usage = self._real(out)
            self.assertFalse(ok, out)
            self.assertIn("failure_class=%s" % cls, reason, out)
            self.assertIsNone(usage)

    def test_a_normal_non_empty_claude_answer_is_still_a_success(self):
        """The regression check: none of the above turned a real answer
        into a failure."""
        with mock.patch.dict(os.environ, {"BROTHER_MODEL_CLIENT": "claude"}):
            out = json.dumps({"result": "the real answer",
                              "usage": {"input_tokens": 1,
                                       "output_tokens": 2}})
            ok, claim, usage = _mw().run_model(
                "prompt",
                runner=lambda *_a, **_k: _FakeCompleted(stdout=out))
        self.assertTrue(ok, claim)
        self.assertEqual(claim, "the real answer")
        self.assertEqual(usage, {"tokens_in": 1, "tokens_out": 2})


class TestRunModelForeignShapeIsFailure(unittest.TestCase):
    """Measured live on 2026-09-11: a headless `claude -p` child answered in a
    stop hook's shape, {"ok": true, "reason": ...}, instead of doing its work.
    From the real claude client that is a failure classed "other", never a
    claim. A MODEL_WORKER_CMD stub keeps the raw-text path every other suite
    relies on."""

    LIVE = {"ok": True, "reason": "Verdict quotes the actor's own transcript "
                                  "line and self-report incidents as its evidence"}

    def _run(self, out, stub=False):
        env = {"BROTHER_MODEL_CLIENT": "claude"}
        if stub:
            env["MODEL_WORKER_CMD"] = "any-stub"
        with mock.patch.dict(os.environ, env):
            if not stub:
                os.environ.pop("MODEL_WORKER_CMD", None)
            return _mw().run_model(
                "prompt", runner=lambda *_a, **_k: _FakeCompleted(stdout=out))

    def test_the_live_hook_shape_as_the_whole_answer_is_a_failure(self):
        ok, reason, usage = self._run(json.dumps(self.LIVE))
        self.assertFalse(ok, reason)
        self.assertIn("failure_class=other", reason)
        self.assertIsNone(usage)

    def test_the_live_hook_shape_inside_result_is_a_failure(self):
        ok, reason, _usage = self._run(json.dumps({"result": json.dumps(self.LIVE)}))
        self.assertFalse(ok, reason)
        self.assertIn("failure_class=other", reason)

    def test_a_json_answer_that_is_not_the_hook_shape_is_still_a_claim(self):
        out = json.dumps({"result": json.dumps({"worker_claim": "did it",
                                                "ok": True})})
        ok, claim, _usage = self._run(out)
        self.assertTrue(ok, claim)

    def test_a_stub_command_keeps_the_raw_text_path(self):
        ok, claim, _usage = self._run(json.dumps(self.LIVE), stub=True)
        self.assertTrue(ok, claim)


class TheBreakerSeesAnAccountLimit(unittest.TestCase):
    """SR-1 D1 and D2, end to end through the real processes: claude exits 1
    with a long is_error envelope, model_worker prints its reason, the real
    bm_worker_spawn.SpawningWorker keeps stderr[:400], and
    loop_bridge.failure_class_of must still read rate_limit. On 233e2c9b1
    the token sat after the envelope's 400 bytes and was cut, and "hit your
    limit" classed other, so SR-4's breaker never tripped."""

    #: The live shape of a claude account limit (2026-09-11), padded the way
    #: the real envelope is by its usage and session fields.
    ENVELOPE = {
        "type": "result", "subtype": "success", "is_error": True,
        "duration_ms": 412, "duration_api_ms": 0, "num_turns": 1,
        "result": "You've hit your limit · resets 3pm (Asia/Tokyo)",
        "stop_reason": "stop_sequence",
        "session_id": "0f9a4c2e-7b1d-4e8a-9c3f-5d6e7f8a9b0c",
        "total_cost_usd": 0,
        "usage": {"input_tokens": 0, "cache_creation_input_tokens": 0,
                  "cache_read_input_tokens": 0, "output_tokens": 0,
                  "server_tool_use": {"web_search_requests": 0,
                                      "web_fetch_requests": 0},
                  "service_tier": "standard"},
        "permission_denials": [],
        "uuid": "3c2b1a09-8f7e-4d6c-b5a4-938271605f4e",
    }

    def test_a_long_limit_envelope_reaches_the_breaker_as_rate_limit(self):
        tools = os.path.join(os.path.dirname(HERE), "products", "brothermode",
                             "tools")
        import loop_bridge  # noqa: PLC0415
        parts, problem = loop_bridge._import_parts(tools)
        if parts is None:
            self.skipTest("NO-DATA: the loop engine is not in this tree: %s"
                          % problem)
        envelope = json.dumps(self.ENVELOPE)
        self.assertGreater(len(envelope), 400, "the envelope must be longer "
                           "than the spawner's cut, or this proves nothing")
        tmp = tempfile.mkdtemp(prefix="mw-")
        stub = write_stub(tmp, """
            import sys
            sys.stdin.read()
            sys.stdout.write(%r)
            sys.exit(1)
        """ % envelope)
        env = dict(os.environ)
        env.pop("MODEL_WORKER_TIMEOUT_S", None)
        env["MODEL_WORKER_CMD"] = "%s %s" % (shlex.quote(sys.executable),
                                             shlex.quote(stub))
        worker = parts["spawn"].SpawningWorker([sys.executable, WORKER],
                                               cwd=tmp, timeout=60,
                                               environ=env)
        result = worker.run({"id": "U-limit", "objective": "x"})
        self.assertEqual(loop_bridge.failure_class_of(result), "rate_limit",
                         result.get("note"))


class TheCodexTurnsSandbox(unittest.TestCase):
    """What CODEX_ARGV's `--sandbox workspace-write` does and does not allow,
    driven against the real Codex binary rather than reasoned about.

    THE QUESTION, raised on 2026-09-05: a lane worker runs its unit inside a
    git worktree, so does the turn CODEX_ARGV starts need the same `.git`
    grant the documented C7 command carries? Three legs answer it, and the
    first is the positive control without which the other two prove nothing.

    ISOLATION, and it is the whole method here: workspace-write announces its
    roots as [workdir, /tmp, $TMPDIR], so a repository under the process temp
    root is granted whole and the question never arises. Each leg therefore
    builds its repository in this process's temp sandbox and hands the Codex
    subprocess a TMPDIR pointing at an EMPTY sibling directory, so the
    repository sits under none of the three roots. Leg 1 landing while leg 2
    does not is the proof that the isolation held."""

    @classmethod
    def setUpClass(cls):
        binary = codex_smoke.DEFAULT_CODEX
        if not (os.path.isfile(binary) and os.access(binary, os.X_OK)):
            raise unittest.SkipTest(
                "NO-DATA: no Codex binary at %s on this machine, so nothing "
                "here says anything about the sandbox" % binary)
        cls.binary = binary

    def _turn(self, command, extra_flags):
        """One real `codex exec` at exactly the CODEX_ARGV shape, with this
        file's stub provider in the model's place issuing `command` as its
        one tool call. Returns (completed_process, worktree)."""
        work = tempfile.mkdtemp(prefix="model-worker-sandbox-")
        main = os.path.join(work, "repo")
        os.makedirs(main)
        git_init_at(main)
        with open(os.path.join(main, "seed.txt"), "w", encoding="utf-8") as fh:
            fh.write("seed\n")
        run_git(["add", "-A"], main)
        run_git(["commit", "-q", "-m", "seed"], main)
        worktree = os.path.join(work, "wt")
        made = run_git(["worktree", "add", "-q", "-b", "u1", worktree], main)
        self.assertEqual(made.returncode, 0, made.stderr)
        # The unit's own edit, already on disk: every leg asks the turn to
        # commit THIS, so the only variable is the sandbox.
        with open(os.path.join(worktree, "mathlib.py"), "w",
                  encoding="utf-8") as fh:
            fh.write("def add(a, b):\n    return a + b\n")

        codex_smoke._Handler.brother_command = command(worktree)
        codex_smoke._Handler.turn = [0]
        server = HTTPServer(("127.0.0.1", 0), codex_smoke._Handler)
        port = server.server_address[1]
        threading.Thread(target=server.serve_forever, daemon=True).start()
        env = dict(os.environ)
        env["CODEX_HOME"] = os.path.join(work, "codex-home")
        env["HOME"] = os.path.join(work, "home")
        # The empty sibling named above: $TMPDIR is a granted root, so it must
        # not be an ancestor of the repository under test.
        env["TMPDIR"] = os.path.join(work, "granted-but-empty")
        env["C7_STUB_KEY"] = "model-worker-not-a-real-key"
        for path in (env["CODEX_HOME"], env["HOME"], env["TMPDIR"]):
            os.makedirs(path, exist_ok=True)
        argv = list(_mw().CODEX_ARGV)
        argv[0] = self.binary
        argv += ["-c", 'model_provider="mwstub"',
                 "-c", 'model="mw-stub-model"',
                 "-c", 'model_providers.mwstub.name="mw stub"',
                 "-c", 'model_providers.mwstub.base_url='
                       '"http://127.0.0.1:%d/v1"' % port,
                 "-c", 'model_providers.mwstub.wire_api="responses"',
                 "-c", 'model_providers.mwstub.env_key="C7_STUB_KEY"',
                 "-c", 'approval_policy="never"']
        argv += extra_flags(worktree)
        argv += ["-C", worktree, "do the one thing you are asked"]
        try:
            proc = subprocess.run(argv, env=env, cwd=worktree,
                                  capture_output=True, text=True, timeout=600)
        finally:
            server.shutdown()
            server.server_close()
        return proc, worktree

    @staticmethod
    def _no_grant(_worktree):
        return []

    @staticmethod
    def _common_dir_grant(worktree):
        """A grant on what `git rev-parse --git-common-dir` prints, which in a
        worktree is the MAIN repository's .git, not `<worktree>/.git`."""
        out = run_git(["rev-parse", "--path-format=absolute",
                       "--git-common-dir"], worktree)
        return ["-c", 'sandbox_workspace_write.writable_roots=["%s"]'
                % (out.stdout or "").strip()]

    @staticmethod
    def _commit(_worktree):
        return ("git add -A && git commit -q -m 'unit U1: model worker' "
                "&& git log --oneline -1")

    def _committed(self, worktree):
        return "unit U1" in (run_git(["log", "--oneline"],
                                     worktree).stdout or "")

    def test_the_turn_can_write_its_unit_with_no_grant_at_all(self):
        """THE POSITIVE CONTROL. Without it the two legs below would also be
        green on a machine where the whole turn was refused for some other
        reason."""
        proc, worktree = self._turn(lambda _wt: "touch inside.txt && echo ok",
                                    self._no_grant)
        self.assertTrue(os.path.isfile(os.path.join(worktree, "inside.txt")),
                        "a plain workspace write was refused, so this whole "
                        "class is measuring something else:\n%s"
                        % ((proc.stdout or "") + (proc.stderr or ""))[-2000:])

    def test_a_git_commit_in_that_turn_is_dropped_and_the_turn_still_exits_0(
            self):
        """The measurement CODEX_ARGV is left alone on. The turn ends GREEN
        and commits nothing, which is why no check anywhere may read a Codex
        turn's exit code as evidence that a write happened."""
        proc, worktree = self._turn(self._commit, self._no_grant)
        self.assertEqual(proc.returncode, 0)
        self.assertFalse(self._committed(worktree),
                         "the commit landed with no grant, so either this "
                         "machine grants the repository's path or Codex "
                         "changed:\n%s"
                         % ((proc.stdout or "") + (proc.stderr or ""))[-2000:])

    def test_the_shared_git_grant_does_not_fix_it_but_the_common_dir_does(
            self):
        """BOTH WAYS, and the surprise is the first half. codex_smoke's
        shared GIT_GRANT names `<workspace>/.git`, which in a worktree is a
        FILE pointing elsewhere, so granting it changes nothing. The grant
        that works names the resolved common directory."""
        proc, worktree = self._turn(
            self._commit,
            lambda wt: ["-c", codex_smoke.GIT_GRANT % wt])
        self.assertFalse(self._committed(worktree),
                         "the shared GIT_GRANT fixed a worktree commit, "
                         "which contradicts the 2026-09-05 measurement:\n%s"
                         % ((proc.stdout or "") + (proc.stderr or ""))[-2000:])
        proc, worktree = self._turn(self._commit, self._common_dir_grant)
        self.assertTrue(self._committed(worktree),
                        "a grant on the resolved --git-common-dir did NOT "
                        "let the commit land:\n%s"
                        % ((proc.stdout or "") + (proc.stderr or ""))[-2000:])

    def test_the_lane_workers_own_commit_is_not_a_write_inside_that_turn(self):
        """WHY CODEX_ARGV carries no grant. commit_changes runs git
        itself, in model_worker's own process, after the model command has
        returned; it is not a tool call inside the sandboxed turn, so a flag
        on that turn could not govern it. Driven with the injected runner, so
        no git and no codex runs here."""
        seen = []

        class _Ran(object):
            returncode = 0
            stdout = " M mathlib.py\n"
            stderr = ""

        def runner(argv, **_kwargs):
            seen.append(argv)
            return _Ran()

        ok, why = _mw().commit_changes("/nowhere", "U1", runner=runner)
        self.assertTrue(ok, why)
        self.assertTrue(seen)
        for argv in seen:
            self.assertEqual(argv[0], "git", argv)
        self.assertIn(["git", "commit", "-q", "-m", "unit U1: model worker"],
                      seen)


if __name__ == "__main__":
    sys.path.insert(0, HERE)
    unittest.main()

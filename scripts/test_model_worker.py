"""Calibration for scripts/model_worker.py.

No network, no real claude: MODEL_WORKER_CMD points every case at a tiny
stub script written to a tempdir, mirroring bm_worker_spawn's own injected-
runner style but at the process boundary rather than the function boundary,
since model_worker's contract is a whole subprocess, not a function.
"""
import json
import os
import stat
import subprocess
import sys
import tempfile
import textwrap
import unittest

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
                          {"MODEL_WORKER_CMD": "%s %s" % (sys.executable, self.stub)})
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
                          {"MODEL_WORKER_CMD": "%s %s" % (sys.executable, stub)})
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
        self.env = {"MODEL_WORKER_CMD": "%s %s" % (sys.executable, self.stub)}

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

    def test_claude_argv_is_unchanged(self):
        """The hard requirement of the whole row: the Claude invocation is
        byte for byte what it was before the adapter existed."""
        self.assertEqual(_mw()._default_argv({"BROTHER_MODEL_CLIENT": "claude"}),
                         ["claude", "-p", "--output-format", "json",
                          "--permission-mode", "acceptEdits"])

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


if __name__ == "__main__":
    sys.path.insert(0, HERE)
    unittest.main()

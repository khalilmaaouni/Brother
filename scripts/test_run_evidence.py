"""What the evidence capture must keep true.

Written after a ten minute battery was captured with `tail -4`, leaving four
lines on disk. The summary said six suites failed and the surviving lines named
two, so four were unknowable, a ready merge stopped, and the run cost another
ten minutes to repeat. The evidence was not lost to a crash: it was discarded at
capture time to keep the output short.

So the property under test is not "it runs a command". It is that nothing this
module does can lose the original.
"""
import os
import re
import shutil
import signal
import subprocess
import sys
import tempfile
import time
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import run_evidence as R  # noqa: E402

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


def runner_for(stdout="", stderr="", returncode=0, midrun=None):
    """A stub with the streaming runner contract: every line of stdout, then
    of stderr (merged order), goes through emit; midrun runs before return."""
    def _run(argv, cwd=None, env=None, emit=None):
        for text in (stdout, stderr):
            for line in text.splitlines(True):
                emit(line)
        if midrun is not None:
            midrun()
        return returncode
    return _run


#: Copied from scripts/lesson_repeat_trial.py line 69 (CAPTURE_EXIT).
CAPTURE_EXIT = re.compile(r"^\[exit (-?\d+) after [\d.]+s\]")

HERE = os.path.dirname(os.path.abspath(__file__))
SCRIPT = os.path.join(HERE, "run_evidence.py")


def isolate_default_store(case):
    """The CLI tests write to DEFAULT_STORE; point it at a temp folder so no
    test writes under the real home."""
    old = R.DEFAULT_STORE
    tmp = tempfile.mkdtemp()
    R.DEFAULT_STORE = tmp

    def _restore():
        R.DEFAULT_STORE = old
        shutil.rmtree(tmp, ignore_errors=True)
    case.addCleanup(_restore)


def read_single(store):
    """The text of the one capture file in store, read through its own handle."""
    names = os.listdir(store)
    if len(names) != 1:
        raise AssertionError("expected one capture file, found %r" % (names,))
    with open(os.path.join(store, names[0]), encoding="utf-8") as fh:
        return fh.read()


class TheOriginalAlwaysSurvives(unittest.TestCase):
    """The one property. Everything else is a convenience."""

    def setUp(self):
        self.store = tempfile.mkdtemp()

    def tearDown(self):
        shutil.rmtree(self.store, ignore_errors=True)

    def test_the_whole_output_reaches_disk_even_when_the_view_is_tiny(self):
        big = "\n".join("line %d" % i for i in range(500))
        r = R.capture(["x"], store=self.store, runner=runner_for(stdout=big))
        with open(r["path"], encoding="utf-8") as fh:
            on_disk = fh.read()
        self.assertIn("line 0", on_disk)
        self.assertIn("line 499", on_disk)
        self.assertEqual(R.view(r, "tail", 2).count("\n"), 1,
                         "the VIEW should be short")

    def test_a_view_never_changes_what_was_captured(self):
        big = "\n".join("line %d" % i for i in range(100))
        r = R.capture(["x"], store=self.store, runner=runner_for(stdout=big))
        before = os.path.getsize(r["path"])
        R.view(r, "tail", 1)
        R.view(r, "head", 1)
        R.view(r, "all")
        self.assertEqual(os.path.getsize(r["path"]), before)

    def test_stderr_is_kept_too_because_that_is_where_refusals_live(self):
        r = R.capture(["x"], store=self.store,
                      runner=runner_for(stdout="fine", stderr="REFUSED: no"))
        with open(r["path"], encoding="utf-8") as fh:
            self.assertIn("REFUSED: no", fh.read())

    def test_a_command_that_cannot_START_still_leaves_a_record(self):
        """The worst case for evidence: nothing ran, so there is nothing to
        capture, and that itself must be written down."""
        def explode(argv, **kw):
            raise OSError("no such executable")
        r = R.capture(["nope"], store=self.store, runner=explode)
        self.assertNotEqual(r["exit_code"], 0)
        self.assertTrue(os.path.exists(r["path"]))
        with open(r["path"], encoding="utf-8") as fh:
            self.assertIn("no such executable", fh.read())

    def test_the_file_records_the_command_and_the_exit_code(self):
        """A capture that does not say what produced it is an orphan."""
        r = R.capture(["echo", "hi"], store=self.store,
                      runner=runner_for(stdout="hi", returncode=5))
        with open(r["path"], encoding="utf-8") as fh:
            body = fh.read()
        self.assertIn("echo hi", body)
        self.assertIn("exit 5", body)


class TheAlertsFireOnTheShapesThatFooledUs(unittest.TestCase):
    def setUp(self):
        self.store = tempfile.mkdtemp()

    def tearDown(self):
        shutil.rmtree(self.store, ignore_errors=True)

    def _sev(self, **kw):
        r = R.capture(["x"], store=self.store, runner=runner_for(**kw))
        return [s for s, _m in R.findings(r)]

    def test_silence_is_an_ALERT_because_it_reads_like_a_clean_pass(self):
        self.assertIn("ALERT", self._sev(stdout="", stderr=""))

    def test_a_printed_verdict_with_exit_zero_is_an_ALERT(self):
        """The shape that let a gate print FAIL and exit 0 here while eleven
        tests passed over it."""
        self.assertIn("ALERT", self._sev(stdout="FAILED: 3", returncode=0))

    def test_each_verdict_word_is_caught(self):
        for word in R.VERDICT_WORDS:
            self.assertIn("ALERT", self._sev(stdout=word + " something",
                                             returncode=0), word)

    def test_a_nonzero_exit_with_no_verdict_word_is_a_WARN_not_silence(self):
        sev = self._sev(stdout="working away", returncode=3)
        self.assertIn("WARN", sev)

    def test_long_output_tells_the_reader_it_is_seeing_a_view(self):
        big = "\n".join("l%d" % i for i in range(R.VIEW_WARN_LINES + 10))
        r = R.capture(["x"], store=self.store, runner=runner_for(stdout=big))
        messages = " ".join(m for _s, m in R.findings(r))
        self.assertIn("VIEW", messages)
        self.assertIn(r["path"], messages)

    def test_an_ordinary_clean_run_still_names_where_the_capture_went(self):
        r = R.capture(["x"], store=self.store, runner=runner_for(stdout="ok"))
        messages = " ".join(m for _s, m in R.findings(r))
        self.assertIn(r["path"], messages)

    def test_colour_is_optional_and_off_by_default(self):
        """A findings list piped into a file must not carry escape codes."""
        r = R.capture(["x"], store=self.store, runner=runner_for(stdout=""))
        plain = " ".join(m for _s, m in R.findings(r, colour=False))
        self.assertNotIn("\033", plain)
        coloured = " ".join(m for _s, m in R.findings(r, colour=True))
        self.assertIn("\033", coloured)


class TheExitCodeIsTheCommandsOwn(unittest.TestCase):
    def setUp(self):
        isolate_default_store(self)

    def test_it_is_passed_through_unchanged(self):
        store = tempfile.mkdtemp()
        try:
            for code in (0, 1, 2, 7, 127):
                r = R.capture(["x"], store=store, runner=runner_for(returncode=code))
                self.assertEqual(r["exit_code"], code)
        finally:
            shutil.rmtree(store, ignore_errors=True)

    def test_the_CLI_returns_it_rather_than_its_own_verdict(self):
        """A wrapper that swallows an exit code turns every check into a
        suggestion."""
        self.assertEqual(R.main(["--", "sh", "-c", "exit 4"]), 4)

    def test_a_bare_invocation_refuses(self):
        self.assertEqual(R.main([]), 2)


class TheStoreIsOutsideAnyRepository(unittest.TestCase):
    def test_the_default_store_is_not_inside_a_working_tree(self):
        """An evidence file that lands in a repository becomes a diff, then gets
        deleted as clutter, and is gone the next time somebody needs it."""
        self.assertNotIn("/Brother/", R.DEFAULT_STORE)
        self.assertIn(".claude", R.DEFAULT_STORE)


class ItFeedsTheAttemptLedgerBecauseNothingElseCan(unittest.TestCase):
    """Measured 2026-08-29: the harness does not tell a PostToolUse hook whether
    a Bash command failed. A Bash tool result carries interrupted, isImage,
    noOutputExpected, stderr and stdout, and no exit code at all. Across 21,596
    recorded attempts on this machine exit_code was None 20,466 times, 0 the
    rest, and never once non-zero.

    So a hook cannot count failures however carefully it is written, and this
    runner can, because it reads proc.returncode itself."""

    def setUp(self):
        isolate_default_store(self)
        self.dir = tempfile.mkdtemp()
        self.store = os.path.join(self.dir, "ledger.jsonl")
        self.old = os.environ.get("ATTEMPT_LEDGER")
        os.environ["ATTEMPT_LEDGER"] = self.store
        import importlib, attempt_ledger
        importlib.reload(attempt_ledger)
        self.ledger = attempt_ledger

    def tearDown(self):
        if self.old is None:
            os.environ.pop("ATTEMPT_LEDGER", None)
        else:
            os.environ["ATTEMPT_LEDGER"] = self.old
        import importlib, attempt_ledger
        importlib.reload(attempt_ledger)

    def run_once(self, code=3):
        return R.main(["--problem", "p", "--class", "k", "--json",
                       "--", "sh", "-c", "exit %d" % code])

    def test_a_failing_run_is_recorded_as_a_failure(self):
        self.run_once(3)
        rows = self.ledger.read(self.store)
        self.assertEqual([r["outcome"] for r in rows], ["failed"])

    def test_a_passing_run_is_recorded_as_a_pass(self):
        self.run_once(0)
        self.assertEqual(self.ledger.read(self.store)[0]["outcome"], "passed")

    def test_the_third_attempt_is_refused_BEFORE_the_command_runs(self):
        """Refusing after running would be a report, and a report is what this
        estate already had for a week while six attempts went by."""
        self.run_once(3)
        self.run_once(3)
        self.assertEqual(self.run_once(3), 1)
        self.assertEqual(len(self.ledger.read(self.store)), 2)

    def test_a_different_class_is_not_refused(self):
        self.run_once(3)
        self.run_once(3)
        code = R.main(["--problem", "p", "--class", "a different one", "--json",
                       "--", "sh", "-c", "exit 0"])
        self.assertEqual(code, 0)

    def test_without_a_declared_class_nothing_is_recorded(self):
        """The class is declared, never inferred. Guessing it is the silent
        failure this whole tool exists to avoid."""
        R.main(["--json", "--", "sh", "-c", "exit 3"])
        self.assertEqual(self.ledger.read(self.store), [])


class TheRecordGrowsWhileTheCommandRuns(unittest.TestCase):
    """N12.a: the capture file is written while the command runs, so a run
    that is stopped still leaves every line it printed."""

    def setUp(self):
        self.store = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.store, True)
        self.seen = []

    def mid_run(self, **kw):
        kw.setdefault("stdout", "first line\nsecond line\n")
        runner = runner_for(midrun=lambda: self.seen.append(read_single(self.store)), **kw)
        R.capture(["echo", "hi"], store=self.store, runner=runner)
        self.assertEqual(len(self.seen), 1)
        return self.seen[0]

    def finished(self, **kw):
        r = R.capture(["echo", "hi"], store=self.store, runner=runner_for(**kw))
        with open(r["path"], encoding="utf-8") as fh:
            return fh.read()

    def test_mid_run_the_first_line_is_already_on_disk(self):
        self.assertIn("first line\n", self.mid_run())

    def test_mid_run_line_two_reads_exit_unfinished(self):
        self.assertTrue(self.mid_run().split("\n")[1].startswith("[exit unfinished]"))

    def test_mid_run_there_is_no_end_marker(self):
        self.assertNotIn(R.END_MARK, self.mid_run())

    def test_after_the_run_line_two_holds_the_exit_code(self):
        line_two = self.finished(stdout="out\n", returncode=5).split("\n")[1]
        self.assertRegex(line_two, r"^\[exit 5 after [\d.]+s\]")

    def test_line_two_still_satisfies_capture_exit_with_the_right_code(self):
        line_two = self.finished(stdout="out\n", returncode=5).split("\n")[1]
        self.assertEqual(CAPTURE_EXIT.match(line_two).group(1), "5")

    def test_the_status_overwrite_keeps_the_structure_intact(self):
        lines = self.finished(stdout="out one\nout two\n", returncode=5).split("\n")
        self.assertEqual(lines[2], "")
        self.assertTrue(lines[3].startswith("--- output"), lines[3])
        self.assertEqual(lines[4], "out one")

    def test_a_finished_file_ends_with_the_end_marker_then_the_exit_line(self):
        body = self.finished(stdout="out\n", returncode=5)
        self.assertRegex(body, r"\n--- end ---\n\[exit 5 after [\d.]+s\]\n\Z")

    def test_output_with_no_final_newline_still_puts_the_end_marker_on_its_own_line(self):
        self.assertIn("abc\n--- end ---", self.finished(stdout="abc"))

    def test_lines_and_bytes_describe_the_whole_file(self):
        r = R.capture(["x"], store=self.store, runner=runner_for(stdout="a\nb\n"))
        with open(r["path"], "rb") as fh:
            raw = fh.read()
        self.assertEqual(r["bytes"], len(raw))
        self.assertEqual(r["lines"], raw.count(b"\n") + 1)
        self.assertEqual(r["stdout"], "a\nb\n")
        self.assertEqual(r["stderr"], "")

    def test_status_line_has_the_same_width_before_and_after(self):
        self.assertEqual(len(R.status_line(None, 0.0)), R.STATUS_WIDTH)
        self.assertEqual(len(R.status_line(5, 1.25)), R.STATUS_WIDTH)
        self.assertTrue(R.status_line(None, 0.0).startswith("[exit unfinished]"))
        self.assertTrue(R.status_line(5, 1.25).startswith("[exit 5 after 1.2s]"))

    PRINT_UNBUF = ["sh", "-c", 'printf %s "$PYTHONUNBUFFERED"']

    def test_the_child_sees_PYTHONUNBUFFERED_1_by_default(self):
        r = R.capture(self.PRINT_UNBUF, store=self.store,
                      env={"PATH": os.environ.get("PATH", "/usr/bin:/bin")})
        self.assertEqual(r["stdout"], "1\n")

    def test_a_caller_supplied_PYTHONUNBUFFERED_wins(self):
        r = R.capture(self.PRINT_UNBUF, store=self.store,
                      env={"PATH": os.environ.get("PATH", "/usr/bin:/bin"),
                           "PYTHONUNBUFFERED": "0"})
        self.assertEqual(r["stdout"], "0\n")

    def test_the_parent_environment_is_never_modified(self):
        saved = os.environ.get("PYTHONUNBUFFERED")
        os.environ.pop("PYTHONUNBUFFERED", None)

        def _restore():
            os.environ.pop("PYTHONUNBUFFERED", None)
            if saved is not None:
                os.environ["PYTHONUNBUFFERED"] = saved
        self.addCleanup(_restore)
        before = dict(os.environ)
        r = R.capture(self.PRINT_UNBUF, store=self.store, env=None)
        self.assertEqual(dict(os.environ), before)
        self.assertEqual(r["stdout"], "1\n")

    def test_a_failing_write_never_stops_the_run(self):
        class FullDisk(object):
            def __init__(self):
                self.writes = 0

            def write(self, data):
                self.writes += 1
                if self.writes > 1:
                    raise OSError(28, "No space left on device")
                return len(data)

            def flush(self):
                pass

            def seek(self, offset):
                return offset

            def close(self):
                pass

        handle = FullDisk()
        old = R.open_capture
        R.open_capture = lambda path: handle

        def _restore():
            R.open_capture = old
        self.addCleanup(_restore)
        r = R.capture(["x"], store=self.store,
                      runner=runner_for(stdout="one\ntwo\nthree\n", returncode=3))
        self.assertEqual(r["exit_code"], 3)
        self.assertEqual(r["stdout"], "one\ntwo\nthree\n")
        self.assertIsNotNone(r["write_error"])
        self.assertIn("No space left on device", r["write_error"])
        first = R.findings(r)[0]
        self.assertEqual(first[0], "ALERT")
        self.assertIn("INCOMPLETE", first[1])
        self.assertNotIn("-", first[1])


class RealProcessesStreamThroughTheDefaultRunner(unittest.TestCase):
    def setUp(self):
        self.store = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.store, True)

    def test_invalid_utf8_is_kept_with_replacement_characters(self):
        r = R.capture(["sh", "-c", "printf '\\377\\n'"], store=self.store)
        self.assertEqual(r["exit_code"], 0, r["stdout"])
        self.assertIn("\ufffd", r["stdout"])
        self.assertTrue(os.path.exists(r["path"]))

    def test_stdout_and_stderr_both_reach_the_file(self):
        r = R.capture(["sh", "-c", "echo to-out; echo to-err >&2"], store=self.store)
        with open(r["path"], encoding="utf-8") as fh:
            body = fh.read()
        self.assertIn("to-out\n", body)
        self.assertIn("to-err\n", body)

    def test_a_store_that_cannot_be_opened_stops_the_run_before_it_starts(self):
        blocker = os.path.join(self.store, "a-regular-file")
        with open(blocker, "w") as fh:
            fh.write("not a directory")
        marker = os.path.join(self.store, "marker")
        code = R.main(["--store", blocker, "--", "sh", "-c", "touch '%s'" % marker])
        self.assertEqual(code, 1)
        self.assertFalse(os.path.exists(marker))

    def test_a_killed_run_keeps_what_it_printed_and_reads_unfinished(self):
        proc = subprocess.Popen([sys.executable, "-B", SCRIPT, "--store", self.store,
                                 "--", "sh", "-c", "echo first; sleep 30"],
                                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                                start_new_session=True)
        body = ""
        deadline = time.time() + 20
        try:
            while time.time() < deadline:
                names = os.listdir(self.store)
                if names:
                    with open(os.path.join(self.store, names[0]), encoding="utf-8") as fh:
                        body = fh.read()
                    if "first" in body:
                        break
                time.sleep(0.1)
        finally:
            try:
                os.killpg(proc.pid, signal.SIGKILL)
            except PermissionError:
                proc.kill()
            proc.wait()
        body = read_single(self.store)
        self.assertIn("first", body)
        self.assertTrue(body.split("\n")[1].startswith("[exit unfinished]"))
        self.assertNotIn(R.END_MARK, body)


class HostileInputIsRefusedNeverCrashes(unittest.TestCase):
    HOSTILE = (None, 0, True, -1, float("nan"), "", "x", b"x", [], ["x"], {},
               {"a": 1}, (), {1, 2}, object())

    def setUp(self):
        isolate_default_store(self)
        self.store = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.store, True)

    def refuses_or_returns(self, fn, *args):
        try:
            fn(*args)
        except (ValueError, LookupError, SystemExit, R.CaptureRefused):
            pass

    def test_every_new_public_function_refuses_hostile_values(self):
        here = os.getcwd()
        os.chdir(self.store)  # open_capture("x") makes its file here
        self.addCleanup(os.chdir, here)
        for bad in self.HOSTILE:
            with self.subTest(bad=bad):
                self.refuses_or_returns(R.status_line, bad, bad)
                self.refuses_or_returns(R.open_capture, bad)
                self.refuses_or_returns(R.stream_runner, bad, bad, bad, bad)
                self.refuses_or_returns(R.main, bad)

    def test_an_unopenable_capture_path_is_refused(self):
        with self.assertRaises(R.CaptureRefused):
            R.open_capture(self.store)

    def test_capture_refuses_a_hostile_value_in_every_parameter(self):
        for bad in self.HOSTILE:
            with self.subTest(bad=bad):
                self.refuses_or_returns(R.capture, bad, bad, bad, bad, bad, bad)
                self.refuses_or_returns(R.capture, ["x"], self.store, bad, None,
                                        runner_for(), None)

    def test_a_wrong_argv_is_refused_not_run(self):
        for bad in (None, "sh", [], [1], ["x", None], ("x",)):
            with self.assertRaises(ValueError):
                R.capture(bad, store=self.store, runner=runner_for())
        for bad in (0, b"x", object(), "x", ["x", 1]):
            with self.assertRaises(SystemExit):
                R.main(bad)

    def test_a_runner_that_returns_no_exit_code_is_not_a_pass(self):
        for bad in (None, True, "0", 0.0):
            r = R.capture(["x"], store=self.store, runner=lambda a, **kw: bad)
            self.assertEqual(r["exit_code"], 127)

    def test_status_line_refuses_bad_codes_and_seconds(self):
        for code, seconds in ((True, 1.0), ("5", 1.0), (5, float("nan")),
                              (5, -1.0), (5, True), (5, None), (5, 1e300)):
            with self.assertRaises(ValueError):
                R.status_line(code, seconds)


class TheEvidenceCheckedLogReducerM36(unittest.TestCase):
    """M3.6 R28-R34: a summary's quotations must exist verbatim in the full text."""

    def test_quoted_spans_extracts_double_and_single_quotes(self):
        self.assertEqual(R.quoted_spans('say "one" then \'two\''), ["one", "two"])

    def test_quoted_spans_quote_free_text_is_empty(self):
        self.assertEqual(R.quoted_spans("no quotes here"), [])

    def test_verbatim_hits_only_true_when_all_found(self):
        self.assertEqual(R.verbatim_hits("alpha beta", ["alpha", "beta"]), (True, []))
        self.assertEqual(R.verbatim_hits("alpha beta", ["alpha", "gamma"]),
                         (False, ["gamma"]))

    def test_all_quotes_verbatim_accepts(self):
        res = R.reduce_log("the value is 42 today", 'it said "42" and \'today\'')
        self.assertEqual(res["verdict"], "ACCEPT")
        self.assertEqual(res["missing"], [])

    def test_missing_quote_blocks(self):
        res = R.reduce_log("the value is 42 today", 'it said "99"')
        self.assertEqual(res["verdict"], "BLOCK")
        self.assertIn("99", res["missing"])

    def test_quote_free_summary_refused(self):
        with self.assertRaises(ValueError):
            R.reduce_log("some words", "some words")

    def test_empty_input_refused(self):
        with self.assertRaises(ValueError):
            R.reduce_log("", 'a "quote"')
        with self.assertRaises(ValueError):
            R.reduce_log("text", "")

    def test_non_string_refused(self):
        for bad in (None, 123, b"bytes", True, float("nan"), ["a"]):
            with self.assertRaises(ValueError):
                R.quoted_spans(bad)
            with self.assertRaises(ValueError):
                R.verbatim_hits(bad, ["x"])
            with self.assertRaises(ValueError):
                R.reduce_log(bad, 'a "quote"')
            with self.assertRaises(ValueError):
                R.reduce_log("text", bad)

    def test_non_string_spans_refused(self):
        for bad in (None, "not a list", 123, True, float("nan")):
            with self.assertRaises(ValueError):
                R.verbatim_hits("text", bad)
        for bad_span in (None, 123, True, float("nan")):
            with self.assertRaises(ValueError):
                R.verbatim_hits("text", [bad_span])


if __name__ == "__main__":
    unittest.main()

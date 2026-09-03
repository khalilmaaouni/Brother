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
import shutil
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import run_evidence as R  # noqa: E402


class Proc(object):
    def __init__(self, stdout="", stderr="", returncode=0):
        self.stdout, self.stderr, self.returncode = stdout, stderr, returncode


def runner_for(**kw):
    def _run(argv, **_ignored):
        return Proc(**kw)
    return _run


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


if __name__ == "__main__":
    unittest.main()

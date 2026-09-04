"""What scripts/attempt_hook.py must keep true.

Mirrors scripts/test_attempt_ledger.py's own style (tempfile store, unittest).
Every test here failed before scripts/attempt_hook.py existed: there was no
module to import and no script to invoke, so every one of these assertions was
an ImportError or a "No such file" from subprocess. Trivially true, stated
because the brief asked for it stated rather than assumed.
"""
import json
import os
import subprocess
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import attempt_hook as H  # noqa: E402
import attempt_ledger as A  # noqa: E402
import test_find_out as TF  # noqa: E402  # reuses its vault()/pattern_store()/memory_file() fixtures

HOOK = os.path.join(HERE, "attempt_hook.py")


def _payload(command, exit_code=None, stdout="", stderr="", timed_out=False,
             success=None):
    resp = {"stdout": stdout, "stderr": stderr, "timed_out": timed_out}
    if exit_code is not None:
        resp["exit_code"] = exit_code
    if success is not None:
        resp["success"] = success
    return {
        "session_id": "test-session",
        "cwd": HERE,
        "hook_event_name": "PostToolUse",
        "tool_name": "Bash",
        "tool_input": {"command": command},
        "tool_response": resp,
    }


def run_hook(payload_obj, store):
    env = dict(os.environ)
    env["ATTEMPT_LEDGER"] = store
    return subprocess.run(
        [sys.executable, HOOK], input=json.dumps(payload_obj),
        capture_output=True, text=True, env=env)


class Fingerprint(unittest.TestCase):
    """Pure function, no subprocess, no store."""

    def test_home_path_variant_and_tilde_form_share_one_class(self):
        under_home = os.path.join(H._HOME, "proj", "report.txt")
        self.assertEqual(
            H.fingerprint("cat %s" % under_home),
            H.fingerprint("cat ~/proj/report.txt"))

    def test_different_shas_of_the_same_technique_share_one_class(self):
        self.assertEqual(
            H.fingerprint("widget-build.sh --sha a1b2c3d4"),
            H.fingerprint("widget-build.sh --sha 998877ff"))

    def test_a_genuinely_different_technique_gets_a_different_class(self):
        self.assertNotEqual(
            H.fingerprint("widget-build.sh --clean"),
            H.fingerprint("widget-poke.sh --clean"))


class ThreeFailuresThenRefusal(unittest.TestCase):
    """The worked example the brief names: three failing runs of one class,
    different sha each time, the third one printing the refusal, a fourth
    still exiting 0 and repeating it."""

    def setUp(self):
        fd, self.store = tempfile.mkstemp(prefix="attempt-hook-test-")
        os.close(fd)
        os.remove(self.store)  # attempt_ledger creates it on first write

    def tearDown(self):
        if os.path.exists(self.store):
            os.remove(self.store)

    def _fail(self, sha):
        return run_hook(_payload("widget-build.sh --sha %s" % sha,
                                 exit_code=1, stderr="boom: exit 1"),
                        self.store)

    def test_third_of_three_failures_prints_refusal_fourth_repeats_it(self):
        r1 = self._fail("a1b2c3d4")
        self.assertEqual(r1.returncode, 0)
        self.assertNotIn("ATTEMPT LEDGER", r1.stdout)

        r2 = self._fail("998877ff")
        self.assertEqual(r2.returncode, 0)
        self.assertNotIn("ATTEMPT LEDGER", r2.stdout)

        r3 = self._fail("00c0ffee")
        self.assertEqual(r3.returncode, 0)
        self.assertIn("ATTEMPT LEDGER", r3.stdout)
        self.assertIn("REFUSE", r3.stdout)
        self.assertIn("already failed", r3.stdout)

        rows = A.read(self.store)
        self.assertEqual(len(rows), 3)
        self.assertTrue(all(row["outcome"] == "failed" for row in rows))
        self.assertEqual(len(set(row["class"] for row in rows)), 1,
                         "three different shas must fingerprint to one class")

        r4 = self._fail("deadbeef")
        self.assertEqual(r4.returncode, 0)
        self.assertIn("ATTEMPT LEDGER", r4.stdout)
        self.assertIn("REFUSE", r4.stdout)
        rows_after_four = A.read(self.store)
        self.assertEqual(len(rows_after_four), 4)

    def test_success_after_failures_records_passed(self):
        self._fail("a1b2c3d4")
        self._fail("998877ff")
        ok = run_hook(_payload("widget-build.sh --sha 00c0ffee",
                               exit_code=0, stdout="built ok"), self.store)
        self.assertEqual(ok.returncode, 0)
        rows = A.read(self.store)
        self.assertEqual(len(rows), 3)
        # "passed" not "succeeded": attempt_ledger.py's own CLI vocabulary,
        # so base-rate's "worked" count (str.startswith("pass")) sees it.
        self.assertEqual(rows[-1]["outcome"], "passed")

    def test_ordinary_success_with_no_prior_failure_writes_nothing(self):
        ok = run_hook(_payload("widget-build.sh --sha a1b2c3d4",
                               exit_code=0, stdout="built ok"), self.store)
        self.assertEqual(ok.returncode, 0)
        self.assertFalse(os.path.exists(self.store))

    def test_malformed_stdin_exits_0_one_stderr_line_writes_nothing(self):
        env = dict(os.environ)
        env["ATTEMPT_LEDGER"] = self.store
        r = subprocess.run([sys.executable, HOOK], input="not json at all",
                           capture_output=True, text=True, env=env)
        self.assertEqual(r.returncode, 0)
        stderr_lines = [ln for ln in r.stderr.splitlines() if ln.strip()]
        self.assertEqual(len(stderr_lines), 1)
        self.assertFalse(os.path.exists(self.store))

    def test_non_bash_tool_is_ignored(self):
        payload = {
            "session_id": "s", "hook_event_name": "PostToolUse",
            "tool_name": "Edit", "tool_input": {"file_path": "/tmp/x"},
            "tool_response": {"filePath": "/tmp/x", "success": False},
        }
        r = run_hook(payload, self.store)
        self.assertEqual(r.returncode, 0)
        self.assertFalse(os.path.exists(self.store))


class ExitCodeNoneReadsOutput(unittest.TestCase):
    """The follow-up: exit_code is None on the overwhelming majority of real
    Bash tool_response payloads (scripts/run_evidence.py, lines 192-193), so a
    hook keyed on the code alone never fires. This is the second signal."""

    def setUp(self):
        fd, self.store = tempfile.mkstemp(prefix="attempt-hook-test-")
        os.close(fd)
        os.remove(self.store)

    def tearDown(self):
        if os.path.exists(self.store):
            os.remove(self.store)

    def _traceback_fail(self):
        # exit_code is deliberately absent: _payload() only sets it when
        # given, and it defaults to None.
        return run_hook(_payload(
            "python3 build.py",
            stderr="Building widget\nTraceback (most recent call last):"),
            self.store)

    def test_traceback_with_no_exit_code_records_inferred_failure(self):
        r = self._traceback_fail()
        self.assertEqual(r.returncode, 0)
        rows = A.read(self.store)
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["outcome"], "failed")
        self.assertTrue(rows[0]["note"].startswith("inferred: "))
        self.assertIn("Traceback", rows[0]["note"])

    def test_three_inferred_failures_print_refusal(self):
        self._traceback_fail()
        self._traceback_fail()
        r3 = self._traceback_fail()
        self.assertEqual(r3.returncode, 0)
        self.assertIn("ATTEMPT LEDGER", r3.stdout)
        self.assertIn("REFUSE", r3.stdout)
        rows = A.read(self.store)
        self.assertEqual(len(rows), 3)
        self.assertTrue(all(row["outcome"] == "failed" for row in rows))

    def test_no_exit_code_clean_output_writes_nothing(self):
        r = run_hook(_payload("python3 build.py",
                              stdout="Building widget\nDone."), self.store)
        self.assertEqual(r.returncode, 0)
        self.assertFalse(os.path.exists(self.store))


class ThirdFailureRunsFindOutItself(unittest.TestCase):
    """learning_loop priority item n=4's SHIP: the third failure's own
    additionalContext carries find_out's own top hits under the refusal,
    with nobody typing "python3 scripts/find_out.py" themselves."""

    def setUp(self):
        fd, self.store = tempfile.mkstemp(prefix="attempt-hook-test-")
        os.close(fd)
        os.remove(self.store)

    def tearDown(self):
        if os.path.exists(self.store):
            os.remove(self.store)

    def _env(self, vault=None, patterns=None, memory=None):
        env = dict(os.environ)
        env["ATTEMPT_LEDGER"] = self.store
        if vault is not None:
            env["FIND_OUT_VAULT"] = vault
        if patterns is not None:
            env["FIND_OUT_PATTERNS"] = patterns
        if memory is not None:
            env["FIND_OUT_MEMORY"] = memory
        return env

    def _fail(self, command, env):
        return subprocess.run(
            [sys.executable, HOOK],
            input=json.dumps(_payload(command, exit_code=1, stderr="boom: exit 1")),
            capture_output=True, text=True, env=env)

    def test_third_failure_hands_back_a_hit_naming_the_fixture_note(self):
        v, pd, m = TF.vault(), TF.pattern_store(), TF.memory_file()
        env = self._env(vault=v, patterns=pd, memory=m)
        cmd = ("echo 'the suite is red and I cannot tell whether my branch "
               "caused it' --sha")
        r1 = self._fail(cmd + " a1b2c3d4", env)
        r2 = self._fail(cmd + " 998877ff", env)
        r3 = self._fail(cmd + " 00c0ffee", env)
        self.assertEqual(r1.returncode, 0)
        self.assertEqual(r2.returncode, 0)
        self.assertEqual(r3.returncode, 0)
        self.assertIn("ATTEMPT LEDGER", r3.stdout)
        self.assertIn("find_out:", r3.stdout)
        self.assertIn("a-suite-went-red-on-a-clean-branch", r3.stdout)
        self.assertRegex(r3.stdout, r"find_out: \d+ of 4 source\(s\) answered")

    def test_first_and_second_failures_carry_no_find_out_lines(self):
        v, pd, m = TF.vault(), TF.pattern_store(), TF.memory_file()
        env = self._env(vault=v, patterns=pd, memory=m)
        cmd = ("echo 'the suite is red and I cannot tell whether my branch "
               "caused it' --sha")
        r1 = self._fail(cmd + " a1b2c3d4", env)
        r2 = self._fail(cmd + " 998877ff", env)
        self.assertNotIn("find_out:", r1.stdout)
        self.assertNotIn("find_out:", r2.stdout)

    def test_all_stores_absent_yields_the_single_no_data_line(self):
        env = self._env(vault="/no/such/vault", patterns="/no/such/patterns",
                        memory="/no/such/memory.md")
        cmd = "widget-ghost.sh --nothing-anywhere-matches --sha"
        self._fail(cmd + " a1b2c3d4", env)
        self._fail(cmd + " 998877ff", env)
        r3 = self._fail(cmd + " 00c0ffee", env)
        self.assertEqual(r3.returncode, 0)
        self.assertIn("ATTEMPT LEDGER", r3.stdout)
        # stdout is one JSON-encoded line (\\n, not a real newline, joins the
        # refusal to the find_out lines), so count the substring rather than
        # splitting lines.
        self.assertEqual(r3.stdout.count("find_out:"), 1)
        self.assertIn("find_out: NO-DATA", r3.stdout)


class AlternatingClassesAreALoop(unittest.TestCase):
    """E57 mechanism 3, borrowed from GSD's sliding-window dispatch-loop
    detector (https://www.opengsd.net/docs/v1/features).

    THE GAP THIS CLOSES, and it is the whole point of the row: attempt_ledger
    counts failures OF ONE CLASS, so two approaches alternating each sit at one
    failure under the two-strike limit and the breaker stays silent while the
    session goes in a circle. The fourth call below (B's SECOND failure, with
    A, B, A already behind it) is refused by nothing in the tree before this
    change: attempt_ledger.check() returns ALLOW for it, which the calibration
    test at the bottom of this class pins directly."""

    def setUp(self):
        fd, self.store = tempfile.mkstemp(prefix="attempt-hook-loop-")
        os.close(fd)
        os.remove(self.store)
        fd, self.outcomes = tempfile.mkstemp(prefix="attempt-hook-outcomes-")
        os.close(fd)
        os.remove(self.outcomes)

    def tearDown(self):
        for path in (self.store, self.outcomes):
            if os.path.exists(path):
                os.remove(path)

    def _env(self):
        env = dict(os.environ)
        env["ATTEMPT_LEDGER"] = self.store
        env["BM_HOOK_OUTCOMES"] = self.outcomes
        # No vault, no patterns, no memory: the find_out branch must not make
        # these tests depend on this machine's real notes.
        env["FIND_OUT_VAULT"] = "/no/such/vault"
        env["FIND_OUT_PATTERNS"] = "/no/such/patterns"
        env["FIND_OUT_MEMORY"] = "/no/such/memory.md"
        return env

    def _fail(self, command):
        return subprocess.run(
            [sys.executable, HOOK],
            input=json.dumps(_payload(command, exit_code=1, stderr="fatal: nope")),
            capture_output=True, text=True, env=self._env())

    def _outcome_rows(self):
        if not os.path.exists(self.outcomes):
            return []
        with open(self.outcomes, encoding="utf-8") as fh:
            return [json.loads(line) for line in fh if line.strip()]

    # Two techniques, deliberately different first tokens so fingerprint()
    # cannot collapse them into one class.
    CMD_A = "widget-alpha.sh --retry"
    CMD_B = "widget-beta.sh --retry"

    def test_a_b_a_b_is_refused_although_neither_class_reached_the_limit(self):
        self._fail(self.CMD_A)
        self._fail(self.CMD_B)
        r3 = self._fail(self.CMD_A)
        self.assertNotIn("ATTEMPT LEDGER", r3.stdout,
                         "A, B, A is one approach tried twice with one thing "
                         "in between; that is work, not a circle")
        r4 = self._fail(self.CMD_B)
        self.assertEqual(r4.returncode, 0, "the detector must never block a call")
        self.assertIn("loop detected across 4 attempts", r4.stdout,
                      "A, B, A, B went unrefused: %r" % r4.stdout)
        self.assertIn("widget-alpha.sh", r4.stdout, "the refusal names neither class")
        self.assertIn("widget-beta.sh", r4.stdout, "the refusal names neither class")

    def test_the_single_class_counter_would_have_allowed_that_fourth_call(self):
        """The calibration that makes the test above mean something: without
        the sliding window, the ledger's own verdict on the fourth call is
        ALLOW, so nothing anywhere would have said a word."""
        self._fail(self.CMD_A)
        self._fail(self.CMD_B)
        self._fail(self.CMD_A)
        rows = A.read(self.store)
        klass_b = H.fingerprint(self.CMD_B)
        verdict, _reason = A.check(rows, klass_b, klass_b)
        self.assertEqual(verdict, A.ALLOW,
                         "the per-class counter already refuses this, so the "
                         "sliding window is not catching anything new")

    def test_four_failures_of_one_class_are_not_reported_as_a_loop(self):
        """The negative half: one class failing four times is the case the
        per-class counter already owns, and it must keep owning it. Exactly one
        refusal prints, and it is the counter's, never the window's."""
        for _ in range(4):
            r = self._fail(self.CMD_A)
        self.assertIn("ATTEMPT LEDGER", r.stdout)
        self.assertNotIn("loop detected", r.stdout,
                         "one class repeated is not an alternation: %r" % r.stdout)

    def test_a_refusal_writes_one_outcome_row_naming_its_kind(self):
        """E57 mechanism 1 on the breaker's side, borrowed from MemOS
        (https://github.com/MemTensor/MemOS): the outcome number, refusals,
        beside the mechanism. Fails before the change because no hook-outcome
        file is written at all."""
        self._fail(self.CMD_A)
        self._fail(self.CMD_B)
        self._fail(self.CMD_A)
        self.assertEqual(self._outcome_rows(), [],
                         "nothing was refused yet, so nothing may be counted")
        self._fail(self.CMD_B)
        rows = self._outcome_rows()
        self.assertEqual(len(rows), 1, "one refusal, one row: %r" % rows)
        self.assertEqual(rows[0]["hook"], "attempt_breaker")
        self.assertEqual(rows[0]["refusals"], 1)
        self.assertEqual(rows[0]["kind"], "alternating_classes")
        self.assertEqual(rows[0]["session"], "test-session")


class AlternationIsAPureFunction(unittest.TestCase):
    """alternating_classes() decided without a store, so every branch is
    readable. Rows are passed in the ledger's own file order, which is its only
    clock."""

    @staticmethod
    def _rows(*pairs):
        return [{"problem": k, "class": k, "outcome": o} for k, o in pairs]

    def test_exactly_a_b_a_b_reports_the_pair(self):
        rows = self._rows(("a", "failed"), ("b", "failed"),
                          ("a", "failed"), ("b", "failed"))
        self.assertEqual(H.alternating_classes(rows), ("a", "b"))

    def test_a_shorter_run_reports_nothing(self):
        rows = self._rows(("a", "failed"), ("b", "failed"), ("a", "failed"))
        self.assertIsNone(H.alternating_classes(rows))

    def test_three_distinct_classes_are_not_this_shape(self):
        rows = self._rows(("a", "failed"), ("b", "failed"),
                          ("c", "failed"), ("a", "failed"))
        self.assertIsNone(H.alternating_classes(rows))

    def test_passed_rows_are_not_failures_and_do_not_close_a_window(self):
        """A passing attempt between two failures is progress, and counting it
        as part of a loop would refuse the run that just worked."""
        rows = self._rows(("a", "failed"), ("b", "failed"), ("a", "failed"),
                          ("c", "passed"), ("b", "failed"))
        # The passed row is skipped, so the last four FAILURES are a, b, a, b.
        self.assertEqual(H.alternating_classes(rows), ("a", "b"))

    def test_only_the_last_window_counts(self):
        """Sliding, not cumulative: an old alternation followed by two failures
        of one class is no longer a live loop."""
        rows = self._rows(("a", "failed"), ("b", "failed"), ("a", "failed"),
                          ("b", "failed"), ("c", "failed"), ("c", "failed"))
        self.assertIsNone(H.alternating_classes(rows))


if __name__ == "__main__":
    unittest.main()

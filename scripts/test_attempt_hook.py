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


if __name__ == "__main__":
    unittest.main()

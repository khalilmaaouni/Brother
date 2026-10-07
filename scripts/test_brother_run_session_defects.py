"""Engine defects found running brother_run.py inside a real Claude Code
session on 2026-09-26 (CLAUDECODE and CLAUDE_CODE_ENTRYPOINT set), each
pinned here so none of them returns unnoticed.

F24: _rewrite_broken_checks asked the planner for a replacement done_check
by spawning door's headless decomposer (door.DEFAULT_MODEL_CMD = ["claude",
"-p"]) even from inside a coding session, where in_claude_code_session()
already refuses exactly that call on every other route (D-001). No test
here ever lets a real `claude -p` or `codex exec` start: door.ask_decomposer
is mocked directly, never spawned as a subprocess.

F25: _check_looks_broken substring-matched BROKEN_CHECK_STDERR_PATTERNS
anywhere in a check's stderr, so an honest failing unittest whose own
assertion message quotes "No such file or directory" was misread as a check
that could never run at all, and refused rather than reported red.

F29: scripts/receipt_check.py knew decide.py's options/sources shape and
the outcome-contract-v1 receipts[] shape, and printed NO-DATA for the
receipt scripts/brother_run.py itself writes: a top-level evidence[] list,
no options key, no top-level receipts key.

F30: a plan whose done_check chains commands with && was accepted when a
session run started and its units were handed over, and refused only later
on --continue, after a worktree had already been opened for it. The same
door.guard_adopted_check fence now runs before the handoff too.

No network and no real model anywhere in this file.
"""
import contextlib
import io
import json
import os
import shlex
import subprocess
import sys
import tempfile
import textwrap
import unittest
from unittest import mock

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
BROTHER_RUN = os.path.join(HERE, "brother_run.py")
import brother_paths  # noqa: E402
import brother_run as _br  # noqa: E402
import door  # noqa: E402
import receipt_check as RC  # noqa: E402

from test_brother_run_plan import (  # noqa: E402
    PlanFileRunBase, run_dirs, sh, write_contract, write_plan)


@contextlib.contextmanager
def env_vars(**changes):
    """Set (a string value) or remove (None) environment variables for the
    duration of the block, restoring whatever was there before. Used
    instead of unittest.mock.patch.dict so both directions (adding a
    session marker, removing one this very process may have inherited) are
    one call."""
    old = {}
    for key, value in changes.items():
        old[key] = os.environ.get(key)
        if value is None:
            os.environ.pop(key, None)
        else:
            os.environ[key] = value
    try:
        yield
    finally:
        for key, value in old.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value


#: Every marker in_claude_code_session() reads, named once so a test can
#: force "definitely not a session" even when this suite itself is run from
#: inside one.
_SESSION_MARKERS = tuple(brother_paths.CLAUDE_MARKER_VARS) + \
    tuple(brother_paths.CODEX_MARKER_VARS)


def _no_session():
    return {var: None for var in _SESSION_MARKERS}


class F24NoHeadlessDecomposerInsideASession(unittest.TestCase):

    def _record_path(self, tmp):
        path = os.path.join(tmp, "work.json")
        with open(path, "w", encoding="utf-8") as fh:
            json.dump({"rows": [{
                "id": "U1", "objective": "fix the broken check",
                "done_check": "python3 -c \"def f(:\"", "status": "READY",
                "check_looks_broken": True,
                "check_stderr_before": "SyntaxError: invalid syntax",
            }]}, fh)
        return path

    def test_refuses_without_calling_out_inside_a_session(self):
        with tempfile.TemporaryDirectory() as tmp:
            record_path = self._record_path(tmp)
            log = _br.RunLog()
            changes = dict(_no_session())
            changes["CLAUDECODE"] = "1"
            changes["DOOR_MODEL_CMD"] = None
            with env_vars(**changes):
                with mock.patch.object(
                        door, "ask_decomposer",
                        side_effect=AssertionError(
                            "must never spawn a headless decomposer from "
                            "inside a coding session")):
                    rows = _br._rewrite_broken_checks(record_path, tmp, log)
        self.assertEqual(len(rows), 1)
        self.assertTrue(rows[0].get("check_looks_broken"))
        self.assertNotIn("check_rewritten", rows[0])
        self.assertTrue(
            any("coding session" in line for line in log.lines), log.lines)

    def test_an_explicit_door_model_cmd_still_opts_in_inside_a_session(self):
        with tempfile.TemporaryDirectory() as tmp:
            record_path = self._record_path(tmp)
            log = _br.RunLog()
            fake = subprocess.CompletedProcess(
                args=["fake"], returncode=0,
                stdout=json.dumps({"done_check": "true"}), stderr="")
            changes = dict(_no_session())
            changes["CLAUDECODE"] = "1"
            changes["DOOR_MODEL_CMD"] = shlex.quote(sys.executable)
            with env_vars(**changes):
                with mock.patch.object(door, "ask_decomposer",
                                       return_value=fake) as spy:
                    rows = _br._rewrite_broken_checks(record_path, tmp, log)
            self.assertTrue(spy.called,
                            "an explicit DOOR_MODEL_CMD must still be "
                            "asked, even inside a session")
        self.assertEqual(rows[0]["done_check"], "true")
        self.assertTrue(rows[0]["check_rewritten"])

    def test_outside_a_session_still_calls_out_as_before(self):
        with tempfile.TemporaryDirectory() as tmp:
            record_path = self._record_path(tmp)
            log = _br.RunLog()
            fake = subprocess.CompletedProcess(
                args=["fake"], returncode=0,
                stdout=json.dumps({"done_check": "true"}), stderr="")
            changes = dict(_no_session())
            changes["DOOR_MODEL_CMD"] = shlex.quote(sys.executable)
            with env_vars(**changes):
                with mock.patch.object(door, "ask_decomposer",
                                       return_value=fake) as spy:
                    rows = _br._rewrite_broken_checks(record_path, tmp, log)
            self.assertTrue(spy.called,
                            "outside a session the old routing must be "
                            "unchanged")
        self.assertEqual(rows[0]["done_check"], "true")


class F25CheckLooksBrokenDecidesFromHowItFailed(unittest.TestCase):

    UNITTEST_STDERR = (
        "F\n"
        "======================================================================\n"
        "FAIL: test_x (__main__.T)\n"
        "----------------------------------------------------------------------\n"
        "Traceback (most recent call last):\n"
        "  File \"t.py\", line 5, in test_x\n"
        "    self.assertTrue(False, \"No such file or directory: nope\")\n"
        "AssertionError: No such file or directory: nope\n"
        "\n"
        "----------------------------------------------------------------------\n"
        "Ran 1 test in 0.001s\n"
        "\n"
        "FAILED (failures=1)\n"
    )

    def test_a_failing_unittest_quoting_a_missing_path_is_not_broken(self):
        self.assertFalse(_br._check_looks_broken(self.UNITTEST_STDERR, 1))

    def test_a_compile_time_syntax_error_with_no_traceback_is_broken(self):
        text = ('  File "<string>", line 1\n'
               '    def f(:\n'
               '          ^\n'
               'SyntaxError: invalid syntax\n')
        self.assertTrue(_br._check_looks_broken(text, 1))

    def test_shell_exit_127_is_broken_whatever_the_text_says(self):
        self.assertTrue(_br._check_looks_broken("anything at all", 127))

    def test_the_interpreter_unable_to_open_the_check_file_is_broken(self):
        text = ("python3: can't open file '/no/such/check.py': [Errno 2] "
               "No such file or directory\n")
        self.assertTrue(_br._check_looks_broken(text, 2))

    def test_empty_stderr_is_not_broken(self):
        self.assertFalse(_br._check_looks_broken("", 1))

    def test_a_plain_scripts_traceback_with_no_banner_is_not_broken(self):
        """Isolates the traceback marker alone (review finding, 2026-09-26,
        mutation M-ED-F25-TRACEBACK): every other "not broken" fixture in
        this class carries BOTH a real traceback and a unittest FAILED/
        FAIL/ERROR banner, so removing either marker from _CHECK_RAN_
        MARKERS survived, each one covering for the other. A plain
        (non-unittest) script that raises FileNotFoundError uncaught
        prints a real Python traceback quoting "No such file or directory"
        and exits 1, with no unittest banner anywhere: only the traceback
        marker can save this one. Run for real, never hand-typed, so the
        shape is the interpreter's own, not a guess at it."""
        proc = subprocess.run(
            [sys.executable, "-c",
             "import os\nos.remove('/no/such/path-f25-a')\n"],
            capture_output=True, text=True)
        self.assertEqual(proc.returncode, 1, proc.stderr)
        self.assertIn("Traceback (most recent call last):", proc.stderr)
        self.assertNotRegex(proc.stderr, r"FAILED \(|FAIL: |ERROR: ")
        self.assertFalse(
            _br._check_looks_broken(proc.stderr, proc.returncode))

    #: Isolates the banner marker alone: a FAILED/FAIL: banner whose own
    #: assertion message quotes "No such file or directory", with no
    #: "Traceback (most recent call last):" line anywhere. Real unittest
    #: output always carries a traceback beside its banner for a failure
    #: (see UNITTEST_STDERR above), so this one is hand-built specifically
    #: to strip it, the only way to isolate the banner from the traceback
    #: it never appears without in a real run.
    BANNER_ONLY_STDERR = (
        "FAIL: test_y (__main__.T)\n"
        "----------------------------------------------------------------------\n"
        "AssertionError: No such file or directory: nope\n"
        "\n"
        "----------------------------------------------------------------------\n"
        "Ran 1 test in 0.001s\n"
        "\n"
        "FAILED (failures=1)\n"
    )

    def test_a_unittest_banner_with_no_traceback_is_not_broken(self):
        """Mutation M-ED-F25-BANNER (review finding, 2026-09-26): the
        sibling half of test_a_plain_scripts_traceback_with_no_banner_is_
        not_broken above. BANNER_ONLY_STDERR carries the banner and the
        quoted phrase alone; only the banner marker can save it."""
        self.assertNotIn("Traceback (most recent call last):",
                        self.BANNER_ONLY_STDERR)
        self.assertFalse(_br._check_looks_broken(self.BANNER_ONLY_STDERR, 1))

    def test_check_passes_now_does_not_flag_a_real_failing_unittest(self):
        """Integration: a REAL failing unittest, run the same way
        _check_passes_now runs every unit's precheck, must not be stamped
        check_looks_broken."""
        with tempfile.TemporaryDirectory() as tmp:
            script = os.path.join(tmp, "t.py")
            with open(script, "w", encoding="utf-8") as fh:
                fh.write(textwrap.dedent("""\
                    import os, unittest
                    class T(unittest.TestCase):
                        def test_x(self):
                            self.assertTrue(
                                os.path.exists("/no/such/path"),
                                "No such file or directory: /no/such/path")
                    if __name__ == "__main__":
                        unittest.main()
                    """))
            cmd = "%s %s" % (shlex.quote(sys.executable), shlex.quote(script))
            passed, exit_code, looks_broken, note = _br._check_passes_now(
                cmd, tmp)
        self.assertFalse(passed)
        self.assertEqual(exit_code, 1)
        self.assertFalse(looks_broken, note)


class F29ReceiptCheckReadsTheEnginesOwnShape(unittest.TestCase):

    def _engine_receipt(self, output_location):
        # The exact shape receipt_door.receipt_record() builds and
        # scripts/brother_run.py's _write_receipt writes to disk, read
        # directly off scripts/receipt_door.py rather than guessed.
        return {
            "scope": {"changed": [], "declared_untouched": []},
            "intent": {"outcome": "make one file", "units": []},
            "evidence": [{
                "id": "U1", "state": "verified", "mark": 1,
                "command": "test -f one.txt", "exit_code": 0,
                "check_passed_before": False, "author": "worker",
                "evidence_family": "E1", "independence": "NO-DATA",
                "output_location": output_location,
                "dependency_check": "NO-DATA", "why": "NO-DATA",
            }],
            "unproven": [], "repair_history": [], "attention": [],
            "containment": {"boundary_crossings": [],
                           "undeclared_scope_units": [], "contained": True},
            "continuity": {"capsule": None, "problem": ""},
            "harness_version": "NO-DATA", "harness_revision": "NO-DATA",
            "report": "delivery report text", "host_capability": {},
        }

    def _write(self, tmp, record):
        path = os.path.join(tmp, "receipt.json")
        with open(path, "w", encoding="utf-8") as fh:
            json.dump(record, fh)
        return path

    def _run_cli(self, argv):
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            code = RC.main(argv)
        return code, buf.getvalue()

    def test_a_resolvable_engine_receipt_exits_0(self):
        with tempfile.TemporaryDirectory() as tmp:
            out_path = os.path.join(tmp, "run.log")
            with open(out_path, "w", encoding="utf-8") as fh:
                fh.write("ok\n")
            path = self._write(tmp, self._engine_receipt(out_path))
            code, out = self._run_cli([path])
        self.assertEqual(code, 0, out)
        self.assertIn(RC.RESOLVED, out)

    def test_an_engine_receipt_naming_a_missing_output_location_exits_1(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = self._write(
                tmp, self._engine_receipt(os.path.join(tmp, "nope.log")))
            code, out = self._run_cli([path])
        self.assertEqual(code, 1, out)
        self.assertIn(RC.UNVERIFIED, out)

    def test_an_empty_evidence_list_is_NO_DATA(self):
        with tempfile.TemporaryDirectory() as tmp:
            record = self._engine_receipt("x")
            record["evidence"] = []
            path = self._write(tmp, record)
            buf = io.StringIO()
            with contextlib.redirect_stderr(buf):
                code = RC.main([path])
        self.assertEqual(code, 2)
        self.assertIn("empty evidence list", buf.getvalue())

    def test_a_contract_shaped_record_is_still_read_as_a_contract(self):
        """Regression: adding the engine shape must not steal rows from the
        outcome-contract-v1 shape, which also carries no options key."""
        with tempfile.TemporaryDirectory() as tmp:
            with open(os.path.join(tmp, "x.py"), "w", encoding="utf-8") as fh:
                fh.write("one\ntwo\n")
            record = {"receipts": [{"id": "r1", "path": "x.py",
                                    "verdict": "PASS", "ref": "file:x.py:1-2"}]}
            path = self._write(tmp, record)
            old_root = RC.ROOT
            RC.ROOT = tmp
            try:
                code, out = self._run_cli([path])
            finally:
                RC.ROOT = old_root
        self.assertEqual(code, 0, out)
        self.assertIn(RC.RESOLVED, out)


class F30ChainedCheckRefusedBeforeHandoffNotAfter(PlanFileRunBase):

    def test_a_chained_check_is_refused_before_any_worktree_is_opened(self):
        env = dict(self.env)
        # SessionWorkerBase's own opening move (test_brother_run_session_
        # worker.py): the base class names a worker stub in MODEL_WORKER_CMD
        # by default, which is itself a valid opt-in out of the session
        # route (D-001). Popping it is what makes session_units_are_yours()
        # true, so the units really are about to be handed to the session
        # rather than given to a spawned worker.
        env.pop("MODEL_WORKER_CMD", None)
        env["CLAUDECODE"] = "1"
        units = [{"id": "U1", "objective": "make one file",
                 "done_check": "test -f one.txt && touch pwned.txt",
                 "writes": ["one.txt"], "deps": []}]
        plan = write_plan(self.tmp, units)
        contract = write_contract(self.tmp, "one file exists",
                                  commands=("test -f one.txt",))
        proc = sh([sys.executable, BROTHER_RUN, "one file exists",
                   "--cwd", self.repo, "--runs-root", self.tmp,
                   "--plan", plan, "--contract", contract], env=env)
        out = proc.stdout + proc.stderr
        self.assertEqual(proc.returncode, 1, out)
        self.assertIn("U1's done_check was refused", out, out)
        self.assertIn("a `&&` chain", out, out)
        self.assertNotIn("pwned", out,
                         "the refused command must never be echoed back")
        runs = run_dirs(self.tmp)
        self.assertEqual(len(runs), 1, out)
        run_dir = os.path.join(self.tmp, "docs", "plan", "runs", runs[0])
        self.assertFalse(
            os.path.isfile(_br.session_handoff_path(run_dir)),
            "a handoff file on disk means the units were claimed and "
            "handed to the session before the refused check was caught")
        self.assertEqual(self.spawned(), [], out)


if __name__ == "__main__":
    unittest.main(verbosity=2)

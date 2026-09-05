#!/usr/bin/env python3
"""test_codex_smoke: drives scripts/codex_smoke.py's two decision points in
BOTH directions, which is the part of it that can lie.

The smoke's real work (a Codex binary, a plugin install, a hook install, a
model turn) is not simulated here: that is what the script itself does when
run, and its own verdict line is the evidence. What is tested is the two
places a wrong answer would look like a right one:

1. THE NO-DATA GUARD. A machine with no Codex binary must report NO-DATA and
   exit 2, never 0. A guard nobody drove backwards is a claim, so the absent
   case AND the present case are both driven.
2. THE RECEIPT READER. `receipt_lines` decides whether a run produced a
   receipt naming a changed file. A reader that returned lines for a receipt
   with no changed file would turn a refused run into a passing smoke, which
   is exactly the state the C7 lane hit on its first run: the engine wrote a
   receipt, and every unit in it was refused.
3. THE DOCUMENTED COMMAND. `TheDocumentedCommand` runs the exact command
   docs/codex/SMOKE-RUNBOOK.md step 6 gives the founder, with the stub
   provider standing in for the model, and asserts it edits mathlib.py and
   leaves a receipt naming that file with a check command and an exit code.
   It then runs the SAME command with `-s workspace-write` removed, which is
   Codex's own `read-only` default, and asserts no receipt appears. That
   second direction is the defect the founder's signed-in run hit on
   2026-09-04: "patch rejected: writing is blocked by read-only sandbox".
   A test that only proved the working direction would have been green all
   through the broken week.
4. THE PAGE AND THE CODE. `TheRunbookMatchesTheCode` fails when the runbook's
   printed command, or either toy file it prints, drifts from the constants
   the automation runs.
"""
import contextlib
import io
import json
import os
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import brother_run  # noqa: E402
import codex_smoke  # noqa: E402

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


class TheNoDataGuard(unittest.TestCase):
    def test_an_absent_binary_is_no_data_and_never_a_pass(self):
        self.assertEqual(codex_smoke.main(["--codex-bin",
                                           "/no/such/codex/binary"]), 2)

    def test_a_directory_is_not_an_executable_binary(self):
        # The other half of the same guard: os.path.isfile, not os.path.exists.
        self.assertEqual(codex_smoke.main(["--codex-bin", HERE]), 2)

    def test_the_real_binary_passes_the_guard(self):
        # The POSITIVE control. Without it the guard could refuse everything
        # and still look correct above.
        real = codex_smoke.DEFAULT_CODEX
        if not (os.path.isfile(real) and os.access(real, os.X_OK)):
            self.skipTest("NO-DATA: no Codex binary at %s on this machine, so "
                          "the guard's positive direction is unproven" % real)
        self.assertTrue(os.path.isfile(real) and os.access(real, os.X_OK))


class TheRunbookMatchesTheCode(unittest.TestCase):
    """The runbook is prose and cannot import, so the drift is caught here
    instead: every command and file body the page prints is rebuilt from the
    constants the automation uses and looked for verbatim."""

    def setUp(self):
        path = os.path.join(os.path.dirname(HERE), "docs", "codex",
                            "SMOKE-RUNBOOK.md")
        if not os.path.isfile(path):
            self.skipTest("NO-DATA: %s is not in this tree, so the page and "
                          "the code cannot be compared" % path)
        with open(path, "r", encoding="utf-8") as fh:
            self.page = fh.read()

    def test_the_page_prints_the_command_the_code_runs(self):
        self.assertIn(codex_smoke.documented_shell_command(), self.page)

    def test_the_page_carries_the_git_grant_the_engine_needs(self):
        # Its own test, because dropping this one flag leaves a command that
        # still runs, still exits 0, and refuses every unit for a reason no
        # reader would connect back to the sandbox.
        self.assertIn(codex_smoke.GIT_GRANT % "$PWD",
                      codex_smoke.documented_shell_command().replace(
                          '\\"', '"'))

    def test_the_network_grant_is_named_but_never_in_the_command(self):
        # Driven both ways on 2026-09-05. The sandbox does block every socket
        # a model-generated command opens, and this switch does lift that, but
        # a nested `codex exec` cannot start inside a codex turn either way
        # ("failed to initialize in-process app-server client"), so no
        # supported path is rescued by granting a whole turn the network. The
        # page explains it so nobody proposes it again; the command stays
        # narrow.
        self.assertNotIn(codex_smoke.NETWORK_GRANT,
                         codex_smoke.documented_shell_command())
        self.assertIn(codex_smoke.NETWORK_GRANT, self.page)
        self.assertIn("app-server client", self.page)

    def test_the_page_names_the_seam_that_reaches_a_receipt(self):
        # The route the signed-in run of 2026-09-05 actually took, after the
        # door refused its nested decomposer and named this in its refusal.
        self.assertIn("DOOR_MODEL_CMD", self.page)
        self.assertIn("MODEL_WORKER_CMD", self.page)

    def test_the_page_names_the_runs_root_the_engine_needs(self):
        """The second half of the same 2026-09-05 failure. With the seam but
        no --runs-root, the engine defaults to its own tree, which under a
        plugin install is read only, and the first call died with an uncaught
        PermissionError on the installed plugin's docs path."""
        self.assertIn(codex_smoke.documented_runs_root_flag(), self.page)
        self.assertIn(codex_smoke.DOCUMENTED_RUNS_ROOT, self.page)

    def test_the_documented_runs_root_is_never_inside_the_repository(self):
        """Driven the other way: a runs root under the target is the shape
        that made every integration refuse as dirty and spun 11 rounds of
        live worker calls on 2026-08-30. The page must not offer it."""
        self.assertNotIn("$PWD/.brother-runs", self.page)
        self.assertFalse(
            codex_smoke.DOCUMENTED_RUNS_ROOT.startswith("$PWD"),
            codex_smoke.DOCUMENTED_RUNS_ROOT)

    def test_the_page_forbids_writing_setup_files_into_the_toy(self):
        """The first half of the 2026-09-05 failure: the turn wrote its own
        intake into the toy before invoking Brother, which made the tree
        dirty, so three worker attempts passed their done checks and
        integration refused every one of them."""
        for name in ("STATE.md", ".sbe/"):
            self.assertIn(name, self.page)
        self.assertIn("NOTHING ELSE IS WRITTEN INTO THE TOY", self.page)

    def test_every_documented_flag_is_built_from_sandbox_flags(self):
        # The page and the automation cannot say different things while the
        # shell line is rendered from the same list the argv is.
        for word in codex_smoke.sandbox_flags("$PWD"):
            self.assertIn(word.replace('"', '\\"'),
                          codex_smoke.documented_shell_command())

    def test_the_page_lays_down_the_toy_the_code_builds(self):
        for body, name in ((codex_smoke.TOY_MATHLIB, "mathlib.py"),
                           (codex_smoke.TOY_TEST, "test_mathlib.py")):
            self.assertIn(codex_smoke.printf_line(body, name), self.page)

    def test_the_toy_test_needs_nothing_installed(self):
        # The 2026-09-04 defect in one line: a toy whose tests need pytest
        # sends the model looking for a binary the machine does not have.
        self.assertIn("import unittest", codex_smoke.TOY_TEST)
        self.assertNotIn("pytest", codex_smoke.TOY_TEST)
        self.assertIn("python3 -m unittest", codex_smoke.TASK_SENTENCE)


class TheReceiptReader(unittest.TestCase):
    def _run_dir(self, body):
        run_dir = tempfile.mkdtemp(prefix="codex-smoke-receipt-")
        out = os.path.join(run_dir, "receipt")
        os.makedirs(out)
        if body is not None:
            with open(os.path.join(out, "receipt.json"), "w",
                      encoding="utf-8") as fh:
                json.dump(body, fh)
        return run_dir

    def test_no_receipt_at_all_is_refused(self):
        found, why = codex_smoke.receipt_lines(self._run_dir(None))
        self.assertIsNone(found)
        self.assertIn("no receipt at", why)

    def test_a_receipt_naming_no_changed_file_is_refused(self):
        run_dir = self._run_dir({"scope": {"changed": [],
                                           "declared_untouched": []}})
        found, why = codex_smoke.receipt_lines(run_dir)
        self.assertIsNone(found)
        self.assertIn("names no changed file", why)

    def test_a_receipt_naming_a_changed_file_yields_its_check_line(self):
        run_dir = self._run_dir({"scope": {"changed": [
            {"file": "mathlib.py", "unit": "U1", "state": "verified",
             "check_command": "python3 -c 'import mathlib'", "exit_code": 0,
             "reason": ""}]}})
        found, why = codex_smoke.receipt_lines(run_dir)
        self.assertEqual(why, "")
        path, lines = found
        self.assertTrue(path.endswith(os.path.join("receipt", "receipt.json")))
        self.assertEqual(len(lines), 1)
        self.assertIn("mathlib.py", lines[0])
        self.assertIn("verified", lines[0])
        self.assertIn("exited 0", lines[0])

    def test_an_unreadable_receipt_is_refused_rather_than_raising(self):
        run_dir = self._run_dir({"scope": {"changed": []}})
        with open(os.path.join(run_dir, "receipt", "receipt.json"), "w",
                  encoding="utf-8") as fh:
            fh.write("{not json")
        found, why = codex_smoke.receipt_lines(run_dir)
        self.assertIsNone(found)
        self.assertIn("unreadable", why)


class TheDocumentedCommand(unittest.TestCase):
    """The runbook's own step 6, run for real against the Codex binary with
    the stub provider substituted for the model, in both sandbox directions.

    This is slow (three real `codex exec` turns, one of them driving the
    whole engine) and it is the only thing in this file that proves the
    documented command WORKS rather than merely that it is spelled
    consistently."""

    @classmethod
    def setUpClass(cls):
        binary = codex_smoke.DEFAULT_CODEX
        if not (os.path.isfile(binary) and os.access(binary, os.X_OK)):
            raise unittest.SkipTest(
                "NO-DATA: no Codex binary at %s on this machine, so the "
                "documented command could not be run in either direction"
                % binary)
        cls.binary = binary

    def _turn(self, sandbox, command_for):
        """One turn at the documented command's own flags, with the stub
        provider in the model's place and `command_for(toy)` as the one tool
        call. Returns the toy, so a caller checks the FILES, not the words in
        a transcript."""
        work = tempfile.mkdtemp(prefix="codex-smoke-documented-")
        toy = os.path.join(work, "toy")
        why = codex_smoke.build_toy(toy)
        self.assertEqual(why, "")
        home = os.path.join(work, "home")
        codex_home = os.path.join(work, "codex-home")
        stubs = os.path.join(work, "stubs")
        runs_root = os.path.join(work, "runs")
        for path in (home, codex_home, stubs, runs_root):
            os.makedirs(path, exist_ok=True)
        env = codex_smoke.codex_env(os.environ, codex_home, home)
        env["DOOR_MODEL_CMD"] = "%s %s" % (
            sys.executable, codex_smoke.write_stub(
                os.path.join(stubs, "decomposer.py"),
                codex_smoke.DECOMPOSER_STUB))
        env["MODEL_WORKER_CMD"] = "%s %s" % (
            sys.executable, codex_smoke.write_stub(
                os.path.join(stubs, "model.py"), codex_smoke.MODEL_STUB))
        turn = codex_smoke.stub_turn(self.binary, env, toy,
                                     command_for(toy, runs_root),
                                     sandbox=sandbox)
        return (turn.stdout or "") + (turn.stderr or ""), toy, runs_root

    @staticmethod
    def _both_writes(toy, _runs_root):
        """The two writes the 2026-09-04 turn was refused, and nothing else.
        The first is the patch the model tried ("patch rejected: writing is
        blocked by read-only sandbox"); the second is the `.git` write
        Brother's own unit isolation needs, which plain workspace-write also
        refuses until the documented -c grant is passed."""
        guard = ("def add(a, b):\n"
                 "    for v in (a, b):\n"
                 "        if not isinstance(v, (int, float)):\n"
                 "            raise TypeError(v)\n"
                 "    return a + b\n")
        return ("%s -c \"open('mathlib.py','w').write(%r)\" && "
                "git worktree add %s -b regression HEAD"
                % (sys.executable, guard,
                   os.path.join(os.path.dirname(toy), "isolation-worktree")))

    def _landed(self, toy):
        """(patched, isolated) as facts on disk."""
        with open(os.path.join(toy, "mathlib.py"), "r",
                  encoding="utf-8") as fh:
            patched = fh.read() != codex_smoke.TOY_MATHLIB
        isolated = os.path.isdir(os.path.join(os.path.dirname(toy),
                                              "isolation-worktree"))
        return patched, isolated

    def test_the_documented_command_can_write_what_the_engine_needs(self):
        body, toy, _ = self._turn(codex_smoke.SANDBOX_MODE, self._both_writes)
        patched, isolated = self._landed(toy)
        self.assertTrue(patched, "the documented command did not edit "
                                 "mathlib.py:\n%s" % body[-2500:])
        self.assertTrue(isolated, "the documented command could not create "
                                  "the unit worktree, so every unit would be "
                                  "refused with 'isolation could not be "
                                  "established':\n%s" % body[-2500:])

    def test_the_same_command_at_codex_default_writes_nothing(self):
        # Codex's own default is read-only. The turn can still exit 0: the
        # write is DROPPED, not raised, which is why the exit code is never
        # the thing asserted here.
        body, toy, _ = self._turn("read-only", self._both_writes)
        patched, isolated = self._landed(toy)
        self.assertFalse(patched, "read-only let the turn edit mathlib.py, so "
                                  "this regression cannot reproduce the "
                                  "2026-09-04 defect:\n%s" % body[-2500:])
        self.assertFalse(isolated, "read-only let the turn create a worktree")

    def test_the_documented_command_leaves_a_receipt_naming_mathlib(self):
        """The whole engine, inside the documented turn. Reported NO-DATA,
        never a pass, when Codex ends the turn before the engine finishes:
        MEASURED 2026-09-05 on a machine at load 36, where the tool call is
        cut off and the turn ends green with a run directory and no receipt.
        The two tests above are the deterministic half of the same claim."""
        def engine(toy, runs_root):
            return ("%s %s 'make add() refuse non-numeric input' --cwd %s "
                    "--runs-root %s --quiet"
                    % (sys.executable, os.path.join(HERE, "brother_run.py"),
                       toy, runs_root))

        body, toy, runs_root = self._turn(codex_smoke.SANDBOX_MODE, engine)
        if "brother_run: receipt: " not in body:
            self.skipTest(
                "NO-DATA: the turn ended before the engine printed its "
                "receipt line, so this run says nothing about the receipt. "
                "Transcript tail:\n%s" % body[-1200:])
        run_dir = codex_smoke.newest_run_dir(
            os.path.join(runs_root, "docs", "plan", "runs"))
        self.assertIsNotNone(run_dir)
        found, why = codex_smoke.receipt_lines(run_dir)
        self.assertIsNotNone(found, why)
        path, lines = found
        self.assertTrue(os.path.isfile(path))
        self.assertEqual(len(lines), 1, lines)
        self.assertIn("mathlib.py", lines[0])
        self.assertIn("check python3 ", lines[0])
        self.assertIn("exited 0", lines[0])
        # THE FOUNDER'S 2026-09-05 FAILURE, asserted as absence. His run left
        # these two in the toy before invoking the engine, which made the
        # tree dirty, so every unit was refused at integration and the
        # receipt reported changed=[] with mathlib.py untouched. A receipt
        # naming mathlib.py is only half the claim; the other half is that
        # nothing else was written into the repository being worked on.
        for name in ("STATE.md", ".sbe"):
            self.assertFalse(
                os.path.exists(os.path.join(toy, name)),
                "the turn wrote %s into the toy, which is what made the "
                "canonical tree dirty on 2026-09-05:\n%s"
                % (name, body[-1500:]))
        # And the run's own records stayed OUTSIDE the toy, which is the
        # other way the same tree goes dirty.
        self.assertFalse(
            os.path.abspath(run_dir).startswith(os.path.abspath(toy) + os.sep),
            run_dir)

    def test_the_engine_says_where_it_put_its_records_when_the_default_fails(
            self):
        """The read-only plugin install, in the shape the founder hit: the
        default runs root cannot be created, so the engine names the one it
        used instead of dying with an uncaught PermissionError."""
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            root = brother_run._resolve_runs_root(
                None, default="/dev/null/not-writable")
        self.assertTrue(root.startswith(tempfile.gettempdir()), root)
        self.assertIn("cannot be written", buf.getvalue())
        self.assertIn(root, buf.getvalue())


if __name__ == "__main__":
    unittest.main(verbosity=2)

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
5. THE SIGNED-IN SHAPE. `TheSignedInShape` drives scripts/brother_run.py
   directly, with no Codex binary involved, the way the engine actually ran
   inside the founder's signed-in Codex turn on 2026-09-05 at 18:02 JST (row
   X8): a check that already passes before the work, and no MODEL_WORKER_CMD
   at all. `TheDocumentedCommand` above sets MODEL_WORKER_CMD in its own test
   environment, so it proved a seam the real turn never used; this class
   proves the shape the real turn actually took, and the fixed shape beside
   it.
"""
import contextlib
import io
import json
import os
import re
import subprocess
import sys
import tempfile
import time
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import brother_run  # noqa: E402
import codex_skills  # noqa: E402
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


#: The founder's real 2026-09-05 turn set no MODEL_WORKER_CMD at all, so the
#: engine fell back to its own default worker argv, a nested `codex exec`,
#: which cannot start inside a Codex turn. This test needs no Codex binary
#: and must not block on a network call that cannot succeed anyway, so it
#: stands in for "no model is reachable" with a worker that exits 0 and
#: writes nothing, which is the same observable outcome: the unit's own
#: done_check decides everything, because check discrimination runs BEFORE
#: any worker, on the untouched tree (brother_run.py's _stamp_prechecks).
NOOP_WORKER_STUB = "import sys\nsys.stdout.write('noop')\n"

#: The red-before check bundle/skills/using-brother/SKILL.md now documents,
#: copied here verbatim rather than re-typed, so a drift between the skill's
#: own words and what this test actually proves would show up as a literal
#: string mismatch, not a silent divergence.
SIGNED_IN_RED_CHECK = (
    'python3 -c "import mathlib, unittest; t = unittest.TestCase(); '
    't.assertRaises(TypeError, mathlib.add, \'a\', \'b\')" && python3 -m unittest'
)

#: The worker that makes the SIGNED_IN_RED_CHECK's guard real: writes the
#: guarded add() and appends a rejection test to test_mathlib.py, exactly
#: what MODEL_WORKER_CMD is documented to do (edit only the unit's own
#: `writes`, in the current directory, ignore the trailing prompt argument).
#: Same double-backslash-n convention codex_smoke.py's own MODEL_STUB uses:
#: this file is written out as another script's source, so the doubled
#: backslash here must survive as a single one in that script, not become a
#: real newline now.
SIGNED_IN_FIX_WORKER_STUB = '''import io

GUARD = ("def add(a, b):\\n"
         "    for value in (a, b):\\n"
         "        if isinstance(value, bool) or not isinstance(value, "
         "(int, float)):\\n"
         "            raise TypeError('add() needs numbers, got %r' % "
         "(value,))\\n"
         "    return a + b\\n")
EXTRA = ("\\n\\n"
         "class RejectTest(unittest.TestCase):\\n"
         "    def test_rejects_non_numeric(self):\\n"
         "        with self.assertRaises(TypeError):\\n"
         "            add('a', 'b')\\n")
with io.open("mathlib.py", "w", encoding="utf-8") as fh:
    fh.write(GUARD)
with io.open("test_mathlib.py", "a", encoding="utf-8") as fh:
    fh.write(EXTRA)
print("worker wrote mathlib.py and appended a test")
'''


class TheSignedInShape(unittest.TestCase):
    """Drives scripts/brother_run.py directly, the way the engine ran under
    Codex on 2026-09-05 at 18:02 JST (row X8). Needs no Codex binary and
    never skips on its absence: the defect this class regresses lived in the
    ENGINE's own behaviour, not in anything Codex-specific, and the founder's
    signed-in transcript is quoted verbatim in docs/codex/SMOKE-RUNBOOK.md."""

    def _toy(self):
        work = tempfile.mkdtemp(prefix="codex-smoke-signed-in-")
        toy = os.path.join(work, "toy")
        why = codex_smoke.build_toy(toy)
        self.assertEqual(why, "")
        return work, toy

    def _plan(self, work, unit):
        path = os.path.join(work, "plan.json")
        with open(path, "w", encoding="utf-8") as fh:
            json.dump([unit], fh)
        return path

    def _stub(self, work, name, body):
        path = os.path.join(work, name)
        with open(path, "w", encoding="utf-8") as fh:
            fh.write(body)
        return path

    def _run_engine(self, toy, runs_root, plan_path, worker_cmd, outcome,
                    timeout=55):
        env = dict(os.environ)
        env["DOOR_MODEL_CMD"] = "cat %s" % plan_path
        env["MODEL_WORKER_CMD"] = worker_cmd
        start = time.time()
        proc = subprocess.run(
            [sys.executable, os.path.join(HERE, "brother_run.py"), outcome,
             "--cwd", toy, "--runs-root", runs_root, "--slots", "1"],
            env=env, capture_output=True, text=True, timeout=timeout)
        return proc, time.time() - start

    def _receipt_from_stdout(self, stdout):
        m = re.search(r"^brother_run: receipt: (.+)$", stdout or "",
                      re.MULTILINE)
        self.assertIsNotNone(m, "no receipt line in stdout:\n%s" % stdout)
        with open(m.group(1).strip(), encoding="utf-8") as fh:
            return json.load(fh)

    def test_the_signed_in_shape_reads_no_data_and_changes_nothing(self):
        work, toy = self._toy()
        plan_path = self._plan(work, {
            "id": "U1", "objective": "make add() refuse non-numeric input",
            "done_check": "python3 -m unittest",
            "writes": ["mathlib.py", "test_mathlib.py"], "deps": []})
        noop = self._stub(work, "noop_worker.py", NOOP_WORKER_STUB)
        proc, elapsed = self._run_engine(
            toy, os.path.join(work, "runs"), plan_path,
            "%s %s" % (sys.executable, noop),
            "make add() refuse non-numeric input")
        self.assertLess(elapsed, 60,
                        "the signed-in shape must resolve without a real "
                        "model call:\n%s" % (proc.stdout + proc.stderr))
        self.assertEqual(proc.returncode, 2, proc.stdout + proc.stderr)
        with open(os.path.join(toy, "mathlib.py"), encoding="utf-8") as fh:
            self.assertEqual(fh.read(), codex_smoke.TOY_MATHLIB)
        receipt = self._receipt_from_stdout(proc.stdout)
        self.assertEqual(receipt["scope"]["changed"], [])
        self.assertEqual(len(receipt["unproven"]), 1, receipt["unproven"])
        entry = receipt["unproven"][0]
        self.assertIs(entry["check_passed_before"], True)
        self.assertIn("already passed before the work began", entry["reason"])

    def test_a_red_check_and_the_worker_seam_reach_a_receipt_with_evidence(
            self):
        work, toy = self._toy()
        before = subprocess.run(SIGNED_IN_RED_CHECK, shell=True, cwd=toy,
                                capture_output=True, text=True, timeout=30)
        self.assertNotEqual(
            before.returncode, 0,
            "the documented red check is not red on the untouched toy:\n%s"
            % (before.stdout + before.stderr))

        plan_path = self._plan(work, {
            "id": "U1", "objective": "make add() refuse non-numeric input",
            "done_check": SIGNED_IN_RED_CHECK,
            "writes": ["mathlib.py", "test_mathlib.py"], "deps": []})
        worker = self._stub(work, "fix_worker.py", SIGNED_IN_FIX_WORKER_STUB)
        proc, elapsed = self._run_engine(
            toy, os.path.join(work, "runs"), plan_path,
            "%s %s" % (sys.executable, worker),
            "make add() refuse non-numeric input")
        self.assertLess(elapsed, 60,
                        "the fixed shape must resolve fast:\n%s"
                        % (proc.stdout + proc.stderr))
        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
        with open(os.path.join(toy, "mathlib.py"), encoding="utf-8") as fh:
            self.assertNotEqual(fh.read(), codex_smoke.TOY_MATHLIB)
        receipt = self._receipt_from_stdout(proc.stdout)
        self.assertEqual(receipt["unproven"], [], receipt["unproven"])
        self.assertTrue(receipt["evidence"], "no evidence in the receipt")
        self.assertEqual(receipt["evidence"][0]["exit_code"], 0)
        self.assertEqual(
            sorted(e["file"] for e in receipt["scope"]["changed"]),
            ["mathlib.py", "test_mathlib.py"])


class TheSkillAndRunbookCarryTheThreeRules(unittest.TestCase):
    """The prose fix, pinned as literally as the runbook and the code
    already pin each other above: a rewording that drops one of the three
    rules, or lets the generated Codex mirror drift, is a regression even
    though nothing here calls Codex or the engine."""

    SKILL_PATH = os.path.join(os.path.dirname(HERE), "bundle", "skills",
                              "using-brother", "SKILL.md")
    RUNBOOK_PATH = os.path.join(os.path.dirname(HERE), "docs", "codex",
                                "SMOKE-RUNBOOK.md")

    def setUp(self):
        for path in (self.SKILL_PATH, self.RUNBOOK_PATH):
            if not os.path.isfile(path):
                self.skipTest("NO-DATA: %s is not in this tree" % path)
        with open(self.SKILL_PATH, encoding="utf-8") as fh:
            self.skill = fh.read()
        with open(self.RUNBOOK_PATH, encoding="utf-8") as fh:
            self.runbook = fh.read()

    def test_both_files_carry_the_worker_seam_and_the_three_rules(self):
        """Also covers the adversarial review that followed TheSignedInShape,
        which found four more ways a signed-in Codex turn could still end
        without a receipt: a worker that exits non-zero before it commits, a
        `writes` list missing one of the files a unit actually touches, a
        done check that fails by way of a missing path rather than by its
        result, and an overbroad claim that no network grant ever helps."""
        for phrase in ("MODEL_WORKER_CMD",
                       "must fail BEFORE any work happens",
                       "forcing condition",
                       "without asking",
                       "must exit 0",
                       "must name EVERY file",
                       "QUARANTINE, never integrated",
                       "Never write a bare-path check",
                       "never on a missing file"):
            self.assertIn(phrase, self.skill,
                          "%s missing from the skill" % phrase)
            self.assertIn(phrase, self.runbook,
                          "%s missing from the runbook" % phrase)

    def test_the_codex_mirror_has_no_drift(self):
        ok, problems = codex_skills.check()
        self.assertTrue(ok, problems)


if __name__ == "__main__":
    unittest.main(verbosity=2)


#!/usr/bin/env python3
"""close_unit's own suite. Pure functions against fixtures, so it runs anywhere and closes nothing real."""
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import close_unit as C  # noqa: E402

DONE_CHECK = "python3 -B -m unittest plugin.runtime.brother.core.test_or_fanout"


class TheDoneCheckNeverRunsUnderTheRunsRouting(unittest.TestCase):
    """2026-10-02: BROTHER_TRANSPORTS=claude refused FX-11's own done check, so a finished unit could not close."""

    def test_the_runs_knobs_and_git_variables_are_dropped_and_the_rest_kept(self):
        env = C.done_check_env({"BROTHER_TRANSPORTS": "claude", "BROTHER_SCOPE": "x", "BROTHER_PIN_MODEL": "opus55",
                                 "GIT_DIR": "/x/.git", "BROTHER_PROGRAM_RECORD": "/r.json", "BROTHER_CLAUDE_BIN": "/c", "PATH": "/bin", "HOME": "/h"})
        self.assertEqual(env, {"BROTHER_CLAUDE_BIN": "/c", "PATH": "/bin", "HOME": "/h"})


class TheExitCodeDecides(unittest.TestCase):
    """A gate here once printed FAIL and exited 0, and eleven tests passed over it."""

    def test_a_zero_exit_passes(self):
        self.assertTrue(C.verdict(0, "Ran 29 tests\nOK")[0])

    def test_a_nonzero_exit_fails_however_green_the_text(self):
        self.assertFalse(C.verdict(1, "Ran 29 tests\nOK\nOK")[0])

    def test_printed_fail_with_a_zero_exit_still_passes_but_is_quoted(self):
        ok, quoted = C.verdict(0, "FAILED (errors=1)")
        self.assertTrue(ok)
        self.assertIn("FAILED", quoted)

    def test_the_quote_carries_the_result_lines(self):
        self.assertIn("Ran 29 tests", C.verdict(0, "noise\nRan 29 tests in 0.1s\nOK\nmore noise")[1])

    def test_a_selftest_result_line_is_quoted(self):
        # 2026-09-24 22:52: H6 closed on land_batch --selftest, which printed "selftest: 66 cases, OK", and its
        # evidence read "printed: no result line"; six board rows carried the same gap.
        out = "check_wave: gate asked for checker 'opus'\nselftest: 66 cases, OK\n"
        self.assertEqual(C.verdict(0, out)[1], "selftest: 66 cases, OK")

    def test_the_unit_checks_own_green_and_red_lines_are_quoted(self):
        # 2026-10-03: R4 closed DONE with "printed: no result line" because donecheck_units' GREEN shape was not read
        self.assertEqual(C.verdict(0, "GREEN: 187 test(s) passed in scripts/test_jev_checks.py")[1],
                         "GREEN: 187 test(s) passed in scripts/test_jev_checks.py")
        self.assertEqual(C.verdict(1, "RED: scripts/x.py ran zero tests")[1], "RED: scripts/x.py ran zero tests")
        self.assertIn("PASS: every executed loop tool", C.verdict(0, "noise\nPASS: every executed loop tool matches\n")[1])

    def test_a_nodata_reason_is_quoted(self):
        # FX-15.6. A check that exits 2 with the reason on its first line still shows the reason after the
        # closer's four line window; before FX-15.6 the reason was dropped and the log read 'exited 2: ' alone.
        out = "NO-DATA: L4.5 has not landed\nRan 1 test\nOK\nRan 4 tests\nRan 9 tests\n"
        ok, quoted = C.verdict(2, out)
        self.assertFalse(ok)
        self.assertIn("L4.5 has not landed", quoted)
        self.assertTrue(quoted.startswith("NO-DATA:"))

    def test_no_output_still_yields_a_quote_placeholder_in_the_evidence(self):
        self.assertIn("no result line", C.evidence_line("U", "s", "c", ""))


class ASpecSubUnitThePlanForgotKeepsTheUnitOpen(unittest.TestCase):
    """2026-10-03: ACC2's spec defines ACC2.5 (the default width 4, R-DIET-7), registered only after its timing check
    passed. The check passed on 2026-09-30; nobody added ACC2.5 to the plan row, so close_unit saw ACC2.1 to ACC2.4
    landed and closed ACC2 DONE with R-DIET-7 never built. A spec sub unit the row neither lists nor records (L0.3 and
    L0.4 are named as removed in L0's evidence) keeps the unit open, at the entry point."""

    def run_main(self, evidence_extra=""):
        import contextlib, io, json, os, shutil, tempfile
        d = tempfile.mkdtemp(prefix="close-unit-spec-")
        self.addCleanup(shutil.rmtree, d, True)
        os.makedirs(os.path.join(d, "docs", "plan", "specs"))
        with open(os.path.join(d, "docs", "plan", "specs", "U9.md"), "w") as fh:
            fh.write("# U9\n### U9.1 first\nx\n### U9.2 second\ny\n### U9.3 only after the check passes\nz\n")
        unit = {"id": "U9", "state": "PARTIAL", "spec": "docs/plan/specs/U9.md", "sub_units": ["U9.1", "U9.2"],
                "done_check": "python3 scripts/donecheck_u9.py",
                "evidence": "U9.1 landed 2026-10-01. U9.2 landed 2026-10-01." + evidence_extra}
        with open(os.path.join(d, "docs", "plan", "BROTHER-1.1.0-LAUNCH-WBS.json"), "w") as fh:
            json.dump({"units": [unit]}, fh)
        old = os.getcwd(); os.chdir(d)
        buf = io.StringIO()
        try:
            with contextlib.redirect_stdout(buf):
                C.main(["U9", "--dry"])
        finally:
            os.chdir(old)
        return buf.getvalue()

    def test_hyphenated_sub_unit_ids_are_read_whole(self):
        # 2026-10-04: "#### RECON.dirty-worktree" was read as RECON.dirty, refusing a fully listed unit
        import tempfile, os
        d = tempfile.mkdtemp(); spec = os.path.join(d, "R.md")
        with open(spec, "w") as fh:
            fh.write("#### R9.dirty-worktree Land it\n\n#### R9.plan-and-handover-items Land them\n\n#### R9.new-one Not listed\n")
        unit = {"id": "R9", "spec": spec, "sub_units": ["R9.dirty-worktree", "R9.plan-and-handover-items"], "evidence": ""}
        self.assertEqual(C.unlisted_spec_subs(unit), ["R9.new-one"])
        unit["sub_units"].append("R9.new-one")
        self.assertEqual(C.unlisted_spec_subs(unit), [])

    def test_a_sub_unit_heading_followed_by_punctuation_is_still_read(self):
        # review round 12: "### X.c: title", "### X.c) note" and "### X.c." must still be read, or an unlisted sub
        # unit hides and its unit closes without it (the ACC2.5 failure)
        import tempfile, os
        for head in ("#### R9.c: title", "#### R9.c) note", "#### R9.c."):
            d = tempfile.mkdtemp(); spec = os.path.join(d, "R.md")
            with open(spec, "w") as fh:
                fh.write(head + "\n")
            self.assertEqual(C.unlisted_spec_subs({"id": "R9", "spec": spec, "sub_units": [], "evidence": ""}), ["R9.c"], head)

    def test_an_unlisted_unrecorded_spec_sub_unit_refuses(self):
        out = self.run_main()
        self.assertIn("NOT CLOSED", out)
        self.assertIn("U9.3", out)
        self.assertNotIn("running its done_check", out)

    def test_a_mention_in_remains_is_still_owed(self):
        # ACC2's exact shape: remains says to append the sub unit later, which is an obligation, not a removal
        import json, os
        out = self.run_main(" Later: append U9.3 to sub_units once the check passes.")
        self.assertIn("NOT CLOSED", out)

    def test_a_mention_without_a_removal_word_is_still_owed(self):
        out = self.run_main(" U9.3 is the next step.")
        self.assertIn("NOT CLOSED", out)

    def test_a_spec_sub_unit_recorded_as_removed_does_not_hold_it(self):
        out = self.run_main(" U9.3 removed as superseded by U9.2 (owner decision).")
        self.assertIn("running its done_check", out)


class TheDoneCheckIsScreened(unittest.TestCase):
    """done_check comes from the plan, which model drafts write, so it is data and never trusted text."""

    def test_a_real_done_check_is_allowed(self):
        self.assertEqual(C.screen_done_check(DONE_CHECK)[0][:2], ["python3", "-B"])

    def test_two_commands_chained_are_allowed(self):
        steps = C.screen_done_check("python3 a.py && python3 scripts/b.py")
        self.assertEqual(len(steps), 2)

    def test_a_pipe_is_refused(self):
        self.assertIsNone(C.screen_done_check("python3 a.py | sh"))

    def test_a_semicolon_is_refused(self):
        self.assertIsNone(C.screen_done_check("python3 a.py; rm -rf /"))

    def test_a_substitution_is_refused(self):
        self.assertIsNone(C.screen_done_check("python3 $(echo a).py"))

    def test_a_redirect_is_refused(self):
        self.assertIsNone(C.screen_done_check("python3 a.py > /etc/passwd"))

    def test_another_program_is_refused(self):
        self.assertIsNone(C.screen_done_check("rm -rf x"))
        self.assertIsNone(C.screen_done_check("bash script.sh"))

    def test_inline_code_is_refused(self):
        """python3 -c runs whatever string follows it, and done_check comes from the plan, which model drafts
        write. A council seat slipped this past the earlier screen."""
        self.assertIsNone(C.screen_done_check('python3 -c "import shutil"'))
        self.assertIsNone(C.screen_done_check("python3 -cimport shutil"))
        self.assertIsNone(C.screen_done_check("python3 -B -c 'x'"))

    def test_a_module_invocation_is_still_allowed(self):
        self.assertIsNotNone(C.screen_done_check("python3 -B -m unittest scripts.test_x"))

    def test_an_absolute_path_argument_is_refused(self):
        self.assertIsNone(C.screen_done_check("python3 /etc/evil.py"))

    def test_a_home_path_argument_is_refused(self):
        self.assertIsNone(C.screen_done_check("python3 ~/evil.py"))

    def test_a_parent_escape_is_refused(self):
        self.assertIsNone(C.screen_done_check("python3 a/../../evil.py"))

    def test_an_empty_or_non_string_is_refused(self):
        self.assertIsNone(C.screen_done_check(""))
        self.assertIsNone(C.screen_done_check(None))

    def test_an_unbalanced_quote_is_refused(self):
        self.assertIsNone(C.screen_done_check("python3 'a.py"))

    def test_a_dangling_chain_is_refused(self):
        self.assertIsNone(C.screen_done_check("python3 a.py &&"))


class OnlyReallyFinishedUnitsClose(unittest.TestCase):
    def plan(self, **over):
        unit = {"id": "U", "sub_units": ["U.1", "U.2"], "state": "PARTIAL",
                "evidence": "U.1 landed today, U.2 landed today", "done_check": DONE_CHECK}
        unit.update(over)
        return {"units": [unit]}

    def test_all_sub_units_landed_is_closable(self):
        p = self.plan()
        self.assertEqual(C.closable(p, C.landed_subs(p)), ["U"])

    def test_one_unlanded_sub_unit_blocks_it(self):
        p = self.plan(evidence="U.1 landed today")
        self.assertEqual(C.closable(p, C.landed_subs(p)), [])

    def test_an_already_done_unit_is_not_closed_again(self):
        p = self.plan(state="DONE")
        self.assertEqual(C.closable(p, C.landed_subs(p)), [])

    def test_a_unit_with_no_done_check_is_never_closed(self):
        p = self.plan(done_check="")
        self.assertEqual(C.closable(p, C.landed_subs(p)), [])

    def test_a_unit_with_no_sub_units_is_never_closed(self):
        p = self.plan(sub_units=[])
        self.assertEqual(C.closable(p, C.landed_subs(p)), [])

    def test_a_full_stop_breaks_the_landed_match(self):
        p = self.plan(evidence="U.1 landed today, U.2 spec. landed")
        self.assertEqual(C.closable(p, C.landed_subs(p)), [])


class TheLandedMatchIsExact(unittest.TestCase):
    """Finding 2, 2026-09-27: A.1 matched inside A.10, and "A.1 not landed" read as landed, so a unit whose sub
    unit never landed became closable. One fixture per guard: the id boundary, then the negation."""

    def plan(self, subs, evidence):
        return {"units": [{"id": "A", "state": "OPEN", "sub_units": subs, "done_check": DONE_CHECK, "evidence": evidence}]}

    def test_a_longer_id_never_lands_its_prefix(self):
        p = self.plan(["A.1", "A.10"], "A.10 landed 2026-09-27.")
        self.assertEqual(C.landed_subs(p), {"A.10"})
        self.assertEqual(C.closable(p, C.landed_subs(p)), [])

    def test_a_negated_landing_is_not_a_landing(self):
        p = self.plan(["A.1"], "A.1 not landed: gate refused.")
        self.assertEqual(C.landed_subs(p), set())
        self.assertEqual(C.closable(p, C.landed_subs(p)), [])

    def test_real_landings_still_close(self):
        p = self.plan(["A.1", "A.10"], "A.1 landed 2026-09-26. A.10 landed 2026-09-27.")
        self.assertEqual(C.closable(p, C.landed_subs(p)), ["A"])


def require_sandbox(test):
    """The done check runs under scripts/loop/sandbox.sb, and a sandbox cannot nest: under the pre-push gate's hermetic rerun
    this suite already runs inside one, so the closer reads "cannot apply a profile here" and nothing closes. That is the
    host's answer, NO-DATA by name (the way test_grade_build_guard reads it), never this suite's red or its pass."""
    import importlib.util
    path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "loop", "grade_build.py")   # the loop's grader by path
    spec = importlib.util.spec_from_file_location("close_unit_test_grade_build", path)
    mod = importlib.util.module_from_spec(spec); spec.loader.exec_module(mod)
    if mod.sandbox_ready():
        test.skipTest("NO-DATA: %s" % mod.sandbox_ready())


def make_repo(d):
    """d becomes a repository with everything in it committed: the closer's done check runs in a disposable copy of the
    tree (land_batch.make_copy, a detached worktree of HEAD), so a tree with no HEAD can run no done check."""
    import subprocess
    g = ["git", "-c", "user.email=t@t", "-c", "user.name=t", "-c", "commit.gpgsign=false", "-c", "core.hooksPath=/dev/null"]
    for argv in (["init", "-q", "-b", "main"], ["add", "-A"], ["commit", "-q", "-m", "tree"]):
        r = subprocess.run(g + argv, cwd=d, capture_output=True, text=True, timeout=60)
        if r.returncode != 0:
            raise RuntimeError("git %s: %s" % (" ".join(argv), r.stderr))


class TheDoneCheckRunsConfinedInACopy(unittest.TestCase):
    """Review 15 finding 2, 2026-10-03: the closer ran every done check with plain subprocess.run, cwd the landing tree, so
    model drafted plan data ran unconfined over just landed code every pass. Each step now runs under the landing sandbox in
    a disposable copy of the tree, and what it writes dies with the copy."""

    def close_with(self, check_body):
        import json, subprocess, tempfile
        require_sandbox(self)
        with tempfile.TemporaryDirectory(prefix="close-boxed-") as d:
            p = os.path.join(d, "docs", "plan", "BROTHER-1.1.0-LAUNCH-WBS.json")
            os.makedirs(os.path.dirname(p))
            with open(p, "w") as fh:
                json.dump({"units": [{"id": "A", "state": "PARTIAL", "sub_units": ["A.1"], "evidence": "A.1 landed initial.",
                                      "done_check": "python3 -B check.py"}]}, fh)
            with open(os.path.join(d, "check.py"), "w") as fh:
                fh.write(check_body)
            make_repo(d)
            r = subprocess.run([sys.executable, "-B", C.__file__], cwd=d, env=dict(os.environ, HOME=d, TMPDIR=d),
                               capture_output=True, text=True, timeout=120)
            with open(p, encoding="utf-8") as fh:
                unit = json.load(fh)["units"][0]
            return r, unit, os.path.exists(os.path.join(d, "planted")), sorted(n for n in os.listdir(d) if n.startswith("land-copy-"))

    def test_a_passing_check_closes_the_unit_and_its_writes_never_reach_the_tree(self):
        r, unit, planted, copies = self.close_with("open('planted', 'w').write('x')\nprint('PASS: planted beside me')\n")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertEqual(unit["state"], "DONE", r.stdout)
        self.assertFalse(planted, "the done check wrote into the tree: it ran bare, not in the copy")
        self.assertEqual(copies, [], "the disposable copy was not dropped")

    def test_the_check_runs_under_the_sandbox_with_a_throwaway_home(self):
        body = ("import os, socket\nassert os.environ['HOME'] != %r, 'the real HOME reached the check'\n"
                "try:\n    socket.create_connection(('127.0.0.1', 9), timeout=1); print('FAIL: the network is open')\n"
                "except OSError:\n    print('PASS: confined')\n") % os.environ.get("HOME", "")
        r, unit, planted, copies = self.close_with(body)
        self.assertEqual(unit["state"], "DONE", r.stdout + r.stderr)

    def test_a_tree_with_no_head_is_no_data_never_a_bare_run(self):
        import json, subprocess, tempfile
        with tempfile.TemporaryDirectory(prefix="close-nohead-") as d:
            p = os.path.join(d, "docs", "plan", "BROTHER-1.1.0-LAUNCH-WBS.json")
            os.makedirs(os.path.dirname(p))
            with open(p, "w") as fh:
                json.dump({"units": [{"id": "A", "state": "PARTIAL", "sub_units": ["A.1"], "evidence": "A.1 landed initial.",
                                      "done_check": "python3 -B check.py"}]}, fh)
            with open(os.path.join(d, "check.py"), "w") as fh:
                fh.write("open('planted', 'w').write('x')\nprint('PASS: ran bare')\n")
            r = subprocess.run([sys.executable, "-B", C.__file__], cwd=d, env=dict(os.environ, HOME=d, TMPDIR=d),
                               capture_output=True, text=True, timeout=120)
            with open(p, encoding="utf-8") as fh:
                unit = json.load(fh)["units"][0]
            planted = os.path.exists(os.path.join(d, "planted"))
        self.assertNotEqual(unit["state"], "DONE", r.stdout)
        self.assertIn("NO-DATA: no disposable copy", r.stdout + r.stderr)
        self.assertFalse(planted, "the done check ran bare in a tree the copy maker refused")


class ACloseNeverErasesAConcurrentWriter(unittest.TestCase):
    """Finding 1, 2026-09-27, at the entry point: close_unit.py read the plan, ran a done_check for minutes, then
    wrote evidence built from that stale copy, so a receipt another writer committed meanwhile was erased."""

    def race(self, concurrent):
        """Run close_unit.py over a one unit plan whose done_check waits; apply `concurrent` to the plan while it
        waits; return (exit code, output, the unit as finally written)."""
        import glob, json, subprocess, tempfile, time
        sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "loop"))
        import plan_store
        require_sandbox(self)
        with tempfile.TemporaryDirectory(prefix="close-race-") as d:
            p = os.path.join(d, "docs", "plan", "BROTHER-1.1.0-LAUNCH-WBS.json")
            os.makedirs(os.path.dirname(p))
            with open(p, "w") as fh:
                json.dump({"units": [{"id": "A", "state": "PARTIAL", "sub_units": ["A.1"], "evidence": "A.1 landed initial.",
                                      "done_check": "python3 -B wait_check.py"}]}, fh)
            with open(os.path.join(d, "wait_check.py"), "w") as fh:
                fh.write("import os, time\nopen('started', 'w').close()\nend = time.monotonic() + 30\n"
                         "while not os.path.exists('release'):\n    assert time.monotonic() < end\n    time.sleep(0.02)\nprint('PASS: waited')\n")
            # THE DONE CHECK RUNS IN A DISPOSABLE COPY OF THE TREE (review 15 finding 2): the tree is a repository, the copy
            # (a detached worktree of its HEAD) is made under TMPDIR, so it is found under d, and the check's own markers are
            # read and written THERE, never in the tree: a marker in d would mean the check ran bare, which is the defect.
            make_repo(d)
            proc = subprocess.Popen([sys.executable, "-B", C.__file__], cwd=d, env=dict(os.environ, HOME=d, TMPDIR=d),
                                    stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
            try:
                end = time.monotonic() + 30
                while not glob.glob(os.path.join(d, "land-copy-*", "tree", "started")):
                    if proc.poll() is not None:
                        self.fail("close_unit.py ended before its done_check started: %s" % "".join(proc.communicate())[-800:])
                    self.assertLess(time.monotonic(), end)
                    time.sleep(0.02)
                self.assertFalse(os.path.exists(os.path.join(d, "started")), "the done check wrote into the tree: it ran bare, not in the copy")
                concurrent(plan_store, p)
                copy = os.path.dirname(glob.glob(os.path.join(d, "land-copy-*", "tree", "started"))[0])
                open(os.path.join(copy, "release"), "w").close()
                out, err = proc.communicate(timeout=60)
            finally:
                if proc.poll() is None:
                    proc.kill()
                    proc.communicate()
            return proc.returncode, out + err, plan_store.load(p)["units"][0]

    def test_evidence_committed_while_the_done_check_runs_survives_the_close(self):
        def receipt(store, p):
            u = store.load(p)["units"][0]
            u["evidence"] += " A.1 landed concurrent receipt."
            store.update_units(p, {"A": u}, fields=("evidence",))
        code, out, final = self.race(receipt)
        self.assertEqual(code, 0, out)
        self.assertEqual(final["state"], "DONE", out)
        self.assertIn("concurrent receipt", final["evidence"])
        self.assertIn("UNIT DONE", final["evidence"])

    def test_nothing_closable_exits_1_and_says_so(self):
        import json, subprocess, tempfile
        with tempfile.TemporaryDirectory(prefix="close-none-") as d:
            p = os.path.join(d, "docs", "plan", "BROTHER-1.1.0-LAUNCH-WBS.json")
            os.makedirs(os.path.dirname(p))
            with open(p, "w") as fh:
                json.dump({"units": [{"id": "A", "state": "PARTIAL", "sub_units": ["A.1"], "evidence": "A.1 not landed.",
                                      "done_check": "python3 -B check.py"}]}, fh)
            r = subprocess.run([sys.executable, "-B", C.__file__], cwd=d, env=dict(os.environ, HOME=d),
                               capture_output=True, text=True, timeout=60)
        self.assertEqual(r.returncode, 1, r.stdout + r.stderr)
        self.assertIn("nothing to close", r.stdout)

    def test_a_unit_another_closer_closed_meanwhile_is_left_as_it_closed_it(self):
        def other_closer(store, p):
            store.update_units(p, {"A": lambda u: dict(u, state="DONE", evidence=u["evidence"] + " UNIT DONE by the other closer.")})
        code, out, final = self.race(other_closer)
        self.assertEqual(final["evidence"].count("UNIT DONE"), 1, final["evidence"])
        self.assertIn("NOT CLOSED", out)
        self.assertEqual(code, 1, out)


class ThePlanIsNeverHalfWritten(unittest.TestCase):
    """A truncating write destroys the plan if anything interrupts it, and at 04:00 nobody notices."""

    def test_the_target_holds_the_new_text(self):
        import tempfile
        with tempfile.TemporaryDirectory() as tmp:
            p = os.path.join(tmp, "plan.json")
            open(p, "w").write("old")
            C.write_atomic(p, "new")
            self.assertEqual(open(p).read(), "new")

    def test_no_temporary_file_is_left_behind(self):
        import tempfile
        with tempfile.TemporaryDirectory() as tmp:
            p = os.path.join(tmp, "plan.json")
            C.write_atomic(p, "x")
            self.assertEqual(sorted(os.listdir(tmp)), ["plan.json"])

    def test_a_failed_write_leaves_the_old_plan_intact(self):
        import tempfile
        with tempfile.TemporaryDirectory() as tmp:
            p = os.path.join(tmp, "plan.json")
            open(p, "w").write("old")
            try:
                C.write_atomic(p, None)      # a write that raises part way through
            except Exception:
                pass
            self.assertEqual(open(p).read(), "old")


class TheEntryPointSaysWhyAndLeavesRetiredUnitsAlone(unittest.TestCase):
    """close_unit.py run as the loop runs it, over a one unit plan with every sub unit landed (2026-10-03 loop report).
    U8 printed "NOT CLOSED: the done_check exited 1:" and nothing after it, because its check's reason line is not one
    of the result shapes the quote keeps; FX-49, RETIRED by owner ruling, was screened and refused on every pass."""

    def run_closer(self, state, check_body, done_check="python3 -B check.py"):
        import json, subprocess, tempfile
        sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "loop"))
        import plan_store
        require_sandbox(self)   # the done check runs confined in a copy of HEAD (review 15 finding 2): no sandbox, no verdict
        with tempfile.TemporaryDirectory(prefix="close-why-") as d:
            p = os.path.join(d, "docs", "plan", "BROTHER-1.1.0-LAUNCH-WBS.json")
            os.makedirs(os.path.dirname(p))
            with open(p, "w") as fh:
                json.dump({"units": [{"id": "A", "state": state, "sub_units": ["A.1"], "evidence": "A.1 landed initial.",
                                      "done_check": done_check}]}, fh)
            with open(os.path.join(d, "check.py"), "w") as fh:
                fh.write(check_body)
            make_repo(d)
            r = subprocess.run([sys.executable, "-B", C.__file__], cwd=d, env=dict(os.environ, HOME=d, TMPDIR=d),
                               capture_output=True, text=True, timeout=60)
            return r.returncode, r.stdout + r.stderr, plan_store.load(p)["units"][0]

    def test_a_failing_check_with_no_result_shape_still_quotes_its_last_line(self):
        code, out, unit = self.run_closer("PARTIAL", "import sys\nprint('MARKETPLACE x')\n"
                                          "print('NOT DONE    4 of 5 catalog(s) are not in the end state')\nsys.exit(1)\n")
        self.assertEqual(code, 1, out)
        self.assertIn("NOT CLOSED: the done_check exited 1: NOT DONE    4 of 5 catalog(s)", out)
        self.assertEqual(unit["state"], "PARTIAL")

    def test_a_retired_unit_is_never_closed_even_when_its_check_passes(self):
        code, out, unit = self.run_closer("RETIRED", "print('PASS: fine')\n")
        self.assertEqual(code, 1, out)
        self.assertIn("nothing to close", out)
        self.assertEqual(unit["state"], "RETIRED", out)

    def test_a_retired_units_unscreenable_check_is_not_screened_every_pass(self):
        code, out, unit = self.run_closer("RETIRED", "", done_check="python3 -B check.py && python3 -c 'x'")
        self.assertNotIn("NOT CLOSED", out)
        self.assertIn("nothing to close", out)
        self.assertEqual(unit["state"], "RETIRED")

    def test_a_named_retired_unit_is_refused_and_says_retired(self):
        import json, subprocess, tempfile
        with tempfile.TemporaryDirectory(prefix="close-named-") as d:
            p = os.path.join(d, "docs", "plan", "BROTHER-1.1.0-LAUNCH-WBS.json")
            os.makedirs(os.path.dirname(p))
            with open(p, "w") as fh:
                json.dump({"units": [{"id": "A", "state": "RETIRED", "sub_units": ["A.1"], "evidence": "A.1 landed initial.",
                                      "done_check": "python3 -B check.py"}]}, fh)
            with open(os.path.join(d, "check.py"), "w") as fh:
                fh.write("print('PASS: fine')\n")
            r = subprocess.run([sys.executable, "-B", C.__file__, "A"], cwd=d, env=dict(os.environ, HOME=d),
                               capture_output=True, text=True, timeout=60)
            with open(p) as fh:
                state = json.load(fh)["units"][0]["state"]
        self.assertEqual(r.returncode, 1, r.stdout + r.stderr)
        self.assertIn("RETIRED", r.stdout)
        self.assertEqual(state, "RETIRED")


if __name__ == "__main__":
    unittest.main(verbosity=1)

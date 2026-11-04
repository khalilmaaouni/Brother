#!/usr/bin/env python3
"""close_unit's own suite. Pure functions against fixtures, so it runs anywhere and closes nothing real."""
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
# THE FIXTURES OWN THEIR PLAN (2026-10-19): every fixture writes its plan at the default path, and close_unit.PLAN is
# read at import from BROTHER_WBS, so a caller that selected another plan (a 1.1.7 closer, a loop pass) turned seven
# cases red, found by the builder preflight's hermetic row. Dropped here, before the import, for this process and its
# children alike.
os.environ.pop("BROTHER_WBS", None)
import close_unit as C  # noqa: E402
import export_public  # noqa: E402

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

    def test_a_journey_walk_row_is_quoted(self):
        """A journey walk prints one row per rubric cell, "PASS     C.one-proof-source   V05   PASS: ...",
        and the closer quoted none of them: PC1H and CARD1 closed on 2026-10-29 with "printed: no result
        line", a DONE with no quoted output, which this estate calls a claim. The row shape is a verdict
        word, spaces, the row id."""
        out = ("installing the candidate\nPASS     C.one-proof-source                 V05   PASS: one renderer\n"
               "identity candidate=abc installed=abc\n")
        ok, quoted = C.verdict(0, out)
        self.assertTrue(ok)
        self.assertIn("C.one-proof-source", quoted)

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

    def test_a_skipped_case_leaves_the_unit_partial(self):
        # I12 (A1): C12 closed on "OK (skipped=1)". A zero exit with a skip is PARTIAL, the skipping test named.
        ok, quoted = C.verdict(0, "test_b (m.T) ... skipped 'no home'\nRan 2 tests in 0.1s\n\nOK (skipped=1)\n")
        self.assertFalse(ok)
        self.assertTrue(quoted.startswith("PARTIAL: 1 test(s) skipped: m.T.test_b"), quoted)


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

    def test_a_done_check_changed_while_it_ran_never_closes_on_the_old_check(self):
        """Reproduced 2026-10-30: the closer ran old_check.py, the plan's done_check became new_check.py while it ran,
        and the close callback wrote DONE anyway. THE done_check FIELD IS THE CHECK: a DONE justified by a command the
        plan no longer names is a claim about a check nobody can re-run from the record."""
        def retarget(store, p):
            store.update_units(p, {"A": lambda u: dict(u, done_check="python3 -B new_check.py")})
        code, out, final = self.race(retarget)
        self.assertNotEqual(final["state"], "DONE", out)
        self.assertNotIn("UNIT DONE", final["evidence"])
        self.assertEqual(final["done_check"], "python3 -B new_check.py")
        self.assertIn("done_check changed", out)
        self.assertEqual(code, 1, out)

    def test_a_unit_another_closer_closed_meanwhile_is_left_as_it_closed_it(self):
        def other_closer(store, p):
            store.update_units(p, {"A": lambda u: dict(u, state="DONE", evidence=u["evidence"] + " UNIT DONE by the other closer.")})
        code, out, final = self.race(other_closer)
        self.assertEqual(final["evidence"].count("UNIT DONE"), 1, final["evidence"])
        self.assertIn("NOT CLOSED", out)
        self.assertEqual(code, 1, out)


class AFrozenHarnessThatDriftedNeverCloses(unittest.TestCase):
    """Readiness finding c (2026-10-29): the plan's harness_sha256 pins were read by nothing at close, so a
    frozen harness edited to pass went green silently. The closer now refuses a unit whose pinned harness
    file no longer has the pinned digest, and a pin naming a missing file refuses too (NO-DATA is not a pass)."""

    def test_a_drifted_pin_refuses_and_an_intact_pin_does_not(self):
        import hashlib, tempfile
        with tempfile.TemporaryDirectory(prefix="pin-") as d:
            path = os.path.join(d, "test_x.py")
            with open(path, "w") as fh:
                fh.write("print('x')\n")
            good = hashlib.sha256(open(path, "rb").read()).hexdigest()
            self.assertEqual("", C.harness_drift({"id": "U", "harness_sha256": {path: good}}))
            with open(path, "a") as fh:
                fh.write("# edited\n")
            self.assertIn("drift", C.harness_drift({"id": "U", "harness_sha256": {path: good}}))
            self.assertIn("missing", C.harness_drift({"id": "U", "harness_sha256": {os.path.join(d, "gone.py"): good}}))
            self.assertEqual("", C.harness_drift({"id": "U"}))
            self.assertIn("unreadable", C.harness_drift({"id": "U", "harness_sha256": "not a map"}))


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


SCRATCH = os.path.join(export_public.scratch_root(), "stab1f")   # fixtures live under the one scratch root, never in the system temp folder
SKIP_MODULE = ("import unittest\n\nclass T(unittest.TestCase):\n    def test_a(self):\n        pass\n\n"
               "    def test_b(self):\n        self.skipTest(%r)\n")


class SkippedTestAsksOnce(unittest.TestCase):
    """STAB1.f. A done check that exits 0 with a skipped test used to print a red NOT CLOSED on every pass, forever, and name
    nothing. The closer now names the skipped test with its reason, writes ONE NEEDS_INPUT record, and a second pass over the
    same skip writes nothing and does not go red. Driven at the entry point, the way the loop calls it (a subprocess in a
    repository holding the plan), with the records in a fixture evidence root."""

    def setUp(self):
        os.makedirs(SCRATCH, exist_ok=True)
        self.root = __import__("tempfile").mkdtemp(prefix="asks-once-", dir=SCRATCH)
        self.addCleanup(__import__("shutil").rmtree, self.root, True)
        self.ev = os.path.join(self.root, "evidence")

    def tree(self, name, units, files=(), plan="BROTHER-1.1.0-LAUNCH-WBS.json"):
        """A committed repository holding the plan `units` and `files`; returns its directory."""
        import json
        d = os.path.join(self.root, name)
        p = os.path.join(d, "docs", "plan", plan)
        os.makedirs(os.path.dirname(p))
        with open(p, "w") as fh:
            json.dump({"units": units}, fh)
        for fname, body in files:
            with open(os.path.join(d, fname), "w") as fh:
                fh.write(body)
        make_repo(d)
        return d

    def closer(self, d, *args, ev=None, wbs=None):
        import subprocess
        env = {k: v for k, v in os.environ.items() if k != "BROTHER_WBS"}
        env.update(HOME=d, TMPDIR=d, BROTHER_EVIDENCE=ev or self.ev)
        if wbs:
            env["BROTHER_WBS"] = wbs
        r = subprocess.run([sys.executable, "-B", C.__file__] + list(args), cwd=d, env=env, capture_output=True, text=True, timeout=120)
        return r.returncode, r.stdout + r.stderr

    def records(self, ev=None):
        import json
        try:
            with open(os.path.join(ev or self.ev, "brother-needs-input.jsonl"), encoding="utf-8") as fh:
                return [json.loads(l) for l in fh if l.strip()]
        except FileNotFoundError:
            return []

    def skip_tree(self, name, reason):
        unit = {"id": "A", "state": "PARTIAL", "sub_units": ["A.1"], "evidence": "A.1 landed initial.",
                "done_check": "python3 -B -m unittest skipmod"}
        return self.tree(name, [unit], [("skipmod.py", SKIP_MODULE % reason)])

    def test_the_first_pass_names_the_skipped_test_and_writes_one_record(self):
        require_sandbox(self)
        d = self.skip_tree("one", "needs the owner key")
        code, out = self.closer(d)
        self.assertIn("NEEDS_INPUT", out)
        self.assertIn("skipmod.T.test_b", out)
        self.assertIn("needs the owner key", out)
        self.assertNotIn("NOT CLOSED", out)
        self.assertEqual(code, 1, out)
        recs = self.records()
        self.assertEqual([(r["kind"], r["unit"]) for r in recs], [("skip", "A")], recs)
        self.assertEqual(recs[0]["tests"], [{"test": "skipmod.T.test_b", "reason": "needs the owner key"}])

    def test_a_second_pass_over_the_same_skip_writes_nothing_and_is_not_red(self):
        require_sandbox(self)
        d = self.skip_tree("two", "needs the owner key")
        self.closer(d)
        before = self.records()
        code, out = self.closer(d)
        self.assertEqual(self.records(), before, "the second pass wrote a second record")
        self.assertIn("NEEDS_INPUT", out)
        self.assertIn("already asked", out)
        self.assertNotIn("NOT CLOSED", out)
        self.assertEqual(len(before), 1)

    def test_a_different_skip_is_a_new_ask(self):
        require_sandbox(self)
        self.closer(self.skip_tree("first", "needs the owner key"))
        self.closer(self.skip_tree("second", "needs a different thing"))
        self.assertEqual(len(self.records()), 2)

    def test_an_evidence_root_that_cannot_be_written_still_asks_and_still_closes_nothing(self):
        require_sandbox(self)
        d = self.skip_tree("blocked", "needs the owner key")
        os.makedirs(self.ev)
        bad = os.path.join(self.ev, "not-a-directory")
        open(bad, "w").close()
        code, out = self.closer(d, ev=bad)
        self.assertIn("NEEDS_INPUT", out)
        self.assertIn("unrecorded", out)
        self.assertNotIn("UNIT DONE", out)
        self.assertEqual(code, 1, out)

    # NEEDFACT: one fixture per condition, so only one guard can refuse each
    def needfact(self, name, runs, notes=None, evidence=""):
        """A plan whose only sub unit A.1 has `evidence` (not landed by default), with run folders `runs`
        [(name, STATUS text, diagnosed, age seconds)]."""
        d = self.tree(name, [{"id": "A", "state": "PARTIAL", "sub_units": ["A.1"], "evidence": evidence, "done_check": "python3 -B check.py"}])
        base = __import__("time").time()
        for rname, status, diagnosed, age in runs:
            rd = os.path.join(self.ev, "unit-runs", rname)
            os.makedirs(rd)
            with open(os.path.join(rd, "STATUS"), "w") as fh:
                fh.write(status + "\n")
            if diagnosed:
                open(os.path.join(rd, "DIAGNOSED"), "w").close()
            os.utime(rd, (base - age, base - age))
        if notes:
            os.makedirs(os.path.join(self.ev, "diag-notes"))
            with open(os.path.join(self.ev, "diag-notes", "A.txt"), "w") as fh:
                fh.write(notes)
        return d

    def test_a_diagnosed_exhausted_sub_unit_escalates_once_with_its_note_path(self):
        d = self.needfact("nf", [("A.1-101010", "EXHAUSTED after 5", True, 60)], notes="A.1: VERIFIED FACT: x\n")
        code, out = self.closer(d)
        note = os.path.join(self.ev, "diag-notes", "A.txt")
        self.assertIn("A     NEEDS_INPUT: A.1 needs a fact", out)
        self.assertIn(note, out)
        self.assertEqual([r["kind"] for r in self.records()], ["needfact"])
        code2, out2 = self.closer(d)
        self.assertNotIn("NEEDS_INPUT", out2, "the second pass asked again")
        self.assertEqual(len(self.records()), 1)

    def test_a_sub_unit_never_sent_to_the_diagnostician_is_not_escalated(self):
        d = self.needfact("nf-undiagnosed", [("A.1-101010", "EXHAUSTED after 5", False, 60)])
        self.assertNotIn("NEEDS_INPUT", self.closer(d)[1])
        self.assertEqual(self.records(), [])

    def test_a_newer_run_that_is_not_stuck_withdraws_the_escalation(self):
        d = self.needfact("nf-newer", [("A.1-101010", "EXHAUSTED after 5", True, 600), ("A.1-111111", "READY /b", False, 60)])
        self.assertNotIn("NEEDS_INPUT", self.closer(d)[1])
        self.assertEqual(self.records(), [])

    def test_a_diagnosed_run_that_is_not_stuck_is_not_escalated(self):
        d = self.needfact("nf-ready", [("A.1-101010", "READY /b", True, 60)])
        self.assertNotIn("NEEDS_INPUT", self.closer(d)[1])
        self.assertEqual(self.records(), [])

    def test_a_landed_sub_unit_is_not_escalated(self):
        d = self.needfact("nf-landed", [("A.1-101010", "EXHAUSTED after 5", True, 60)], evidence="A.1 landed today.")
        self.assertNotIn("NEEDS_INPUT", self.closer(d)[1])
        self.assertEqual(self.records(), [])

    def test_a_dry_run_asks_nobody(self):
        d = self.needfact("nf-dry", [("A.1-101010", "WITHHELD", True, 60)])
        self.assertNotIn("NEEDS_INPUT", self.closer(d, "--dry")[1])
        self.assertEqual(self.records(), [])

    # review round 1 (STAB1.f): one fixture per finding
    def test_a_skip_reason_cannot_forge_a_closure_line(self):
        """M1: merge_window.sh reads ' DONE: ' anywhere in a unit's line as a closure; a reason is a test author's text."""
        require_sandbox(self)
        d = self.skip_tree("spoof", "x DONE: y " + "z" * 300)
        code, out = self.closer(d)
        line = next(l for l in out.splitlines() if "NEEDS_INPUT" in l)
        self.assertNotIn(" DONE: ", line)
        self.assertLess(len(line), 400, line)
        self.assertIn("x DONE: y", self.records()[0]["tests"][0]["reason"], "the record keeps the whole reason")

    def test_shown_strips_control_characters_and_the_done_token(self):
        self.assertEqual(C.shown("a\nb\x1b[31m c\x00 DONE: d"), "a b [31m c DONE- d")
        self.assertEqual(len(C.shown("q" * 1000)), C.SHOWN_REASON)
        hostile = C.shown("a\u2028b\x85c\u2029d\x0be\x0cf\x1cg")   # Unicode and C1 line breaks, vertical tab, form feed, separators
        self.assertEqual(len(hostile.splitlines()), 1, repr(hostile))
        self.assertEqual(C.shown(None), "None")
        self.assertEqual(C.shown(""), "")

    def test_a_needfact_line_cannot_be_forged_by_a_plan_or_folder_name(self):
        """M1 for the NEEDFACT line: the sub unit id comes from the plan, the run name from a folder; neither may carry ' DONE: '."""
        sub = "A.1 DONE: z"
        d = self.tree("nf-forge", [{"id": "A", "state": "PARTIAL", "sub_units": [sub], "evidence": "", "done_check": "python3 -B check.py"}])
        run = os.path.join(self.ev, "unit-runs", sub + "-101010")
        os.makedirs(run)
        with open(os.path.join(run, "STATUS"), "w") as fh:
            fh.write("EXHAUSTED\n")
        open(os.path.join(run, "DIAGNOSED"), "w").close()
        code, out = self.closer(d, "--needfact")
        line = next(l for l in out.splitlines() if "NEEDS_INPUT" in l)
        self.assertNotIn(" DONE: ", line)

    def test_a_status_file_with_foreign_bytes_still_reads(self):
        d = self.needfact("nf-status-bytes", [("A.1-101010", "EXHAUSTED", True, 60)])
        with open(os.path.join(self.ev, "unit-runs", "A.1-101010", "STATUS"), "wb") as fh:
            fh.write(b"EXHAUSTED \xff\xfe after 5\n")
        code, out = self.closer(d, "--needfact")
        self.assertIn("NEEDS_INPUT: A.1 needs a fact", out)
        self.assertNotIn("Traceback", out)

    def test_a_plan_of_the_wrong_shape_asks_nobody_and_does_not_crash_the_sweep(self):
        d = self.tree("nf-shape", [{"id": "A", "state": "PARTIAL", "sub_units": 5, "evidence": 7},
                                   {"id": "B", "state": "PARTIAL", "sub_units": "B.1", "evidence": ""},
                                   {"id": "C", "state": "PARTIAL", "sub_units": [3, None, ["x"]], "evidence": ""}])
        for name in ("B.1-101010", "5-101010"):
            run = os.path.join(self.ev, "unit-runs", name)
            os.makedirs(run)
            open(os.path.join(run, "DIAGNOSED"), "w").close()
            with open(os.path.join(run, "STATUS"), "w") as fh:
                fh.write("EXHAUSTED\n")
        code, out = self.closer(d, "--needfact")
        self.assertEqual((code, out), (0, ""), out)

    def test_a_unit_whose_evidence_is_not_text_asks_nobody_and_does_not_crash(self):
        """One guard: sub_units is a good list here, so only the evidence type can refuse it."""
        d = self.tree("nf-evidence", [{"id": "A", "state": "PARTIAL", "sub_units": ["A.1"], "evidence": 7}])
        run = os.path.join(self.ev, "unit-runs", "A.1-101010")
        os.makedirs(run)
        open(os.path.join(run, "DIAGNOSED"), "w").close()
        with open(os.path.join(run, "STATUS"), "w") as fh:
            fh.write("EXHAUSTED\n")
        code, out = self.closer(d, "--needfact")
        self.assertEqual((code, out), (0, ""), out)

    def test_a_plan_whose_units_are_null_asks_nobody_and_does_not_crash(self):
        d = self.tree("nf-null", None)
        code, out = self.closer(d, "--needfact")
        self.assertEqual((code, out), (0, ""), out)

    def test_the_sweep_reads_the_plan_the_dev_line_selects(self):
        """BROTHER_WBS names the plan (1.2.0 lands on the dev line with its own plan file); nothing falls back to the default."""
        d = self.tree("nf-dev", [{"id": "A", "state": "PARTIAL", "sub_units": ["A.1"], "evidence": "", "done_check": "python3 -B check.py"}],
                      plan="DEV-1.2.0-WBS.json")
        run = os.path.join(self.ev, "unit-runs", "A.1-101010")
        os.makedirs(run)
        open(os.path.join(run, "DIAGNOSED"), "w").close()
        with open(os.path.join(run, "STATUS"), "w") as fh:
            fh.write("EXHAUSTED\n")
        self.assertIn("NO-DATA: the plan is unreadable", self.closer(d, "--needfact")[1], "the default plan does not exist here")
        self.assertIn("NEEDS_INPUT: A.1 needs a fact", self.closer(d, "--needfact", wbs="docs/plan/DEV-1.2.0-WBS.json")[1])

    def test_a_sweep_with_nothing_to_ask_creates_nothing(self):
        d = self.needfact("nf-quiet", [("A.1-101010", "READY /b", False, 60)])
        before = sorted(os.listdir(self.ev))
        code, out = self.closer(d, "--needfact")
        self.assertEqual((code, out), (0, ""))
        self.assertEqual(sorted(os.listdir(self.ev)), before)
        fresh = os.path.join(self.root, "never-made")
        self.closer(d, "--needfact", ev=fresh)
        self.closer(d, "--dry", ev=fresh)
        self.assertFalse(os.path.exists(fresh), "a read created the evidence folder")

    def records_valid(self):
        import json
        out = []
        with open(os.path.join(self.ev, "brother-needs-input.jsonl"), "rb") as fh:
            for l in fh.read().splitlines():
                try:
                    out.append(json.loads(l.decode("utf-8")))
                except ValueError:
                    continue
        return out

    def test_a_non_utf8_byte_in_the_log_is_a_bad_line_not_a_crash(self):
        """M3: one foreign byte used to raise UnicodeDecodeError out of the closer, and nothing closed until a person edited the file."""
        require_sandbox(self)
        os.makedirs(self.ev)
        with open(os.path.join(self.ev, "brother-needs-input.jsonl"), "wb") as fh:
            fh.write(b'{"key": "other"}\n\xff\xfe torn\n')
        code, out = self.closer(self.skip_tree("bytes", "needs the owner key"))
        self.assertIn("NEEDS_INPUT", out)
        self.assertNotIn("Traceback", out)
        self.assertEqual(len(self.records_valid()), 2)
        d = self.needfact("nf-bytes", [("A.1-101010", "EXHAUSTED after 5", True, 60)])
        code, out = self.closer(d)
        self.assertIn("A     NEEDS_INPUT: A.1 needs a fact", out)
        self.assertNotIn("Traceback", out)

    def test_a_reason_that_changes_only_in_its_numbers_is_the_same_ask(self):
        """L1: a pid or a port in the reason must not ask again every pass."""
        require_sandbox(self)
        self.closer(self.skip_tree("pid1", "host probe failed: pid 4711 port 8080"))
        self.closer(self.skip_tree("pid2", "host probe failed: pid 99 port 31337"))
        self.assertEqual(len(self.records()), 1, self.records())

    def test_two_units_with_the_identical_skip_are_two_asks(self):
        """R3: the ask is the unit's; a second unit with the same skipped test is its own question."""
        require_sandbox(self)
        mk = lambda uid: {"id": uid, "state": "PARTIAL", "sub_units": [uid + ".1"], "evidence": uid + ".1 landed initial.",
                          "done_check": "python3 -B -m unittest skipmod"}
        d = self.tree("twins", [mk("A"), mk("B")], [("skipmod.py", SKIP_MODULE % "needs the owner key")])
        self.closer(d)
        self.assertEqual(sorted(r["unit"] for r in self.records()), ["A", "B"])

    def test_a_unittest_discover_done_check_still_runs_and_names_its_skip(self):
        """M4: a global -v before discover exits 2 (unrecognized arguments); -v belongs after it."""
        require_sandbox(self)
        import subprocess
        unit = {"id": "A", "state": "PARTIAL", "sub_units": ["A.1"], "evidence": "A.1 landed initial.",
                "done_check": "python3 -B -m unittest discover -s tests"}
        d = self.tree("disc", [unit], [])
        os.makedirs(os.path.join(d, "tests"))
        with open(os.path.join(d, "tests", "test_s.py"), "w") as fh:
            fh.write(SKIP_MODULE % "needs the owner key")
        g = ["git", "-c", "user.email=t@t", "-c", "user.name=t", "-c", "commit.gpgsign=false", "-c", "core.hooksPath=/dev/null"]
        subprocess.run(g + ["add", "-A"], cwd=d, check=True, capture_output=True)
        subprocess.run(g + ["commit", "-q", "-m", "t"], cwd=d, check=True, capture_output=True)
        code, out = self.closer(d)
        self.assertIn("NEEDS_INPUT: PARTIAL: 1 test(s) skipped: test_s.T.test_b (needs the owner key)", out)

    def test_a_script_form_done_check_names_its_skip(self):
        """M5: 'python3 -B scripts/test_x.py' is the shape of 360 of 386 done checks; unittest.main takes -v from argv."""
        require_sandbox(self)
        unit = {"id": "A", "state": "PARTIAL", "sub_units": ["A.1"], "evidence": "A.1 landed initial.",
                "done_check": "python3 -B skiprun.py"}
        body = SKIP_MODULE % "needs the owner key" + "\nif __name__ == '__main__':\n    unittest.main()\n"
        code, out = self.closer(self.tree("script", [unit], [("skiprun.py", body)]))
        self.assertIn("NEEDS_INPUT: PARTIAL: 1 test(s) skipped: __main__.T.test_b (needs the owner key)", out)
        self.assertEqual(self.records()[0]["tests"][0]["reason"], "needs the owner key")

    def test_a_script_that_reads_its_own_arguments_is_left_as_written(self):
        d = self.root
        for name, body, want in (("own.py", "import argparse\nimport unittest\nunittest.main()\n", False),
                                 ("argv.py", "import sys\nimport unittest\nunittest.main(argv=sys.argv[:1])\n", False),
                                 ("plain.py", "import unittest\nunittest.main()\n", True)):
            with open(os.path.join(d, name), "w") as fh:
                fh.write(body)
            self.assertEqual(C.runs_unittest_main(os.path.join(d, name)), want, name)
        self.assertFalse(C.runs_unittest_main(os.path.join(d, "missing.py")))
        own = ["python3", "-B", os.path.join(d, "own.py")]
        self.assertEqual(C.verbose_step(own), own)
        self.assertEqual(C.verbose_step(["python3", "-m", "unittest", "discover", "-s", "t"]),
                         ["python3", "-m", "unittest", "discover", "-v", "-s", "t"])

    def test_a_closer_waits_for_the_lock_another_closer_holds_on_the_log(self):
        """R1: read-then-append is one step under an exclusive lock. A racing sixteen did not reproduce a duplicate on this host
        (the first closer finishes inside its time slice), so the lock is pinned directly: while another holder has the log
        locked, ask_once must NOT complete; once released it completes and asks exactly once."""
        import fcntl, subprocess, time
        path = os.path.join(self.root, "lock", "brother-needs-input.jsonl")
        os.makedirs(os.path.dirname(path))
        code = ("import sys\nsys.path.insert(0, %r)\nimport close_unit as C\nprint(C.ask_once({'key': 'same', 'unit': 'A'}, %r))\n"
                % (os.path.dirname(os.path.abspath(C.__file__)), path))
        env = {k: v for k, v in os.environ.items() if k != "BROTHER_WBS"}
        with open(path, "a+") as holder:
            fcntl.flock(holder, fcntl.LOCK_EX)
            p = subprocess.Popen([sys.executable, "-B", "-c", code], stdout=subprocess.PIPE, text=True, env=env)
            time.sleep(3)
            still_waiting = p.poll() is None
            fcntl.flock(holder, fcntl.LOCK_UN)
        out = p.communicate(timeout=60)[0].strip()
        self.assertTrue(still_waiting, "ask_once finished while another closer held the log: it takes no lock")
        self.assertEqual(out, "asked")
        self.assertEqual(len(self.records_valid_at(path)), 1)

    def records_valid_at(self, path):
        import json
        with open(path, "rb") as fh:
            return [json.loads(l.decode("utf-8")) for l in fh.read().splitlines() if l.strip()]

    def test_the_newest_run_is_the_newest_by_the_clock_not_by_its_name(self):
        """R2: the folder name is HHMMSS and wraps at midnight; 001000 made after midnight is newer than 235000 from the night before."""
        d = self.needfact("nf-midnight", [("A.1-235000", "READY /b", False, 7200), ("A.1-001000", "EXHAUSTED after 5", True, 60)])
        self.assertIn("A     NEEDS_INPUT: A.1 needs a fact", self.closer(d)[1])

    def test_needfact_alone_asks_and_closes_nothing(self):
        d = self.needfact("nf-flag", [("A.1-101010", "WITHHELD", True, 60)])
        code, out = self.closer(d, "--needfact")
        self.assertIn("NEEDS_INPUT: A.1 needs a fact", out)
        self.assertNotIn("nothing to close", out)
        self.assertEqual(code, 0, out)


class ALaneThatNeverRuledPrintsRetiredNotZeroOverZero(unittest.TestCase):
    """STAB1.f. The PULSE line printed "checker LAND 0/0" for a lane that gave no verdict in a whole run while builds reached
    its stage. Driven at pass_pulse.py's entry point (a subprocess, the driver's argv) over a fixture evidence root; each
    fixture isolates one condition of the rename."""

    def pulse(self, probe_ok=True, checker_files=()):
        import json, shutil, subprocess, tempfile, time
        os.makedirs(SCRATCH, exist_ok=True)
        d = tempfile.mkdtemp(prefix="pulse-", dir=SCRATCH)
        self.addCleanup(shutil.rmtree, d, True)
        os.makedirs(os.path.join(d, "unit-runs"))
        with open(os.path.join(d, "brother-stages.jsonl"), "w") as fh:
            fh.write(json.dumps({"event": "leave", "stage": "probe", "ok": probe_ok, "at": time.strftime("%Y-%m-%dT%H:%M:%S")}) + "\n")
        for i, verdict in enumerate(checker_files):
            out = os.path.join(d, "unit-runs", "A.1-0%d0000" % (i + 1), "round0", "out")
            os.makedirs(out)
            with open(os.path.join(out, "A.1-r0-build.json.check.json"), "w") as fh:
                json.dump({"verdict": verdict}, fh)
        log = os.path.join(d, "driver.log")
        open(log, "w").close()
        loop = os.path.join(os.path.dirname(os.path.abspath(__file__)), "loop", "pass_pulse.py")
        r = subprocess.run([sys.executable, "-B", loop, "--start", str(int(time.time() - 3600)), "--passes", "3", "--log", log],
                           capture_output=True, text=True, env=dict(os.environ, PULSE_EVIDENCE=d), timeout=60)
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        return next(l for l in r.stdout.splitlines() if l.startswith("PULSE pass"))

    def test_no_verdict_while_a_build_reached_the_stage_is_retired(self):
        self.assertIn("checker LAND RETIRED (no answer 0)", self.pulse())

    def test_no_build_reached_the_stage_keeps_zero_over_zero(self):
        self.assertIn("checker LAND 0/0 (no answer 0)", self.pulse(probe_ok=False))

    def test_a_lost_call_is_not_a_retired_lane(self):
        self.assertIn("checker LAND 0/0 (no answer 1)", self.pulse(checker_files=["NO-DATA"]))

    def test_a_lane_that_ruled_keeps_its_count(self):
        self.assertIn("checker LAND 1/1 (no answer 0)", self.pulse(checker_files=["LAND"]))


class TheLandersClosingPassHonoursTheAsk(unittest.TestCase):
    """Review round 1 (STAB1.f), H1 and M2, at the entry point the loop uses: land_batch.closing_pass (loop_pass.sh calls
    land_batch.py --close). A skip question must print as NEEDS-INPUT, record ONE attempt so the done check is not run again on an
    unchanged tree, and write no failure ledger row; a diagnosed NEEDFACT sub unit must be asked from this same pass."""

    PLAN = "docs/plan/BROTHER-1.1.0-LAUNCH-WBS.json"

    def setUp(self):
        import json, subprocess
        import land_batch as LB
        os.makedirs(SCRATCH, exist_ok=True)
        self.root = __import__("tempfile").mkdtemp(prefix="closing-pass-", dir=SCRATCH)
        self.addCleanup(__import__("shutil").rmtree, self.root, True)
        self.LB, self.json, self.subprocess = LB, json, subprocess
        self.ev = os.path.join(self.root, "evidence")
        self.repo = os.path.join(self.root, "repo")
        os.makedirs(os.path.join(self.repo, "docs", "plan"))
        self.attempts, self.ledger = os.path.join(self.root, "attempts.jsonl"), os.path.join(self.root, "ledger.jsonl")

        def fake_boxed(argv, log, timeout, env, cwd=None, root=None, seed=()):   # the ledger classifier, faked to one class row
            with open(argv[argv.index("--path") + 1], "a") as fh:
                fh.write('{"class": "close"}\n')
            return subprocess.CompletedProcess(argv, 0, "", "")
        from unittest import mock
        for patch in (mock.patch.object(LB, "code_root", lambda frozen=None: "/code"), mock.patch.object(LB, "boxed", fake_boxed),
                      mock.patch.object(LB, "FAILURE_LEDGER", self.ledger), mock.patch.object(LB, "CLOSE_ATTEMPTS", self.attempts)):
            patch.start()
            self.addCleanup(patch.stop)
        self.here = os.getcwd()
        os.chdir(self.repo)
        self.addCleanup(os.chdir, self.here)

    def commit(self, units):
        with open(self.PLAN, "w") as fh:
            self.json.dump({"units": units}, fh)
        with open("c.py", "w") as fh:
            fh.write("a = 1\n")
        g = ["git", "-c", "user.name=t", "-c", "user.email=t@t", "-c", "commit.gpgsign=false", "-c", "core.hooksPath=/dev/null"]
        for argv in (["init", "-q"], ["add", "-A"], ["commit", "-q", "-m", "c"]):
            self.subprocess.run(g + argv, check=True, capture_output=True)

    def rows(self, path):
        return open(path).read().count("\n") if os.path.exists(path) else 0

    def fake_closer(self, said):
        """sh_ for closing_pass: --dry lists ACC2, --needfact is silent, a named run answers `said` and exits 1."""
        runs = []

        def sh_(cmd, log, timeout=None):
            if any(str(c).endswith("close_unit.py") for c in cmd):
                if "--dry" in cmd:
                    return self.subprocess.CompletedProcess(cmd, 1, "ACC2  running its done_check: python3 -m unittest x\nCLOSED  0 unit(s): none\n", "")
                if "--needfact" in cmd:
                    return self.subprocess.CompletedProcess(cmd, 0, "", "")
                runs.append(cmd[-1])
                return self.subprocess.CompletedProcess(cmd, 1, said, "")
            return self.subprocess.CompletedProcess(cmd, 0, "", "")
        sh_.runs = runs
        return sh_

    ASK = ("ACC2  running its done_check: python3 -m unittest x\n"
           "ACC2  NEEDS_INPUT: PARTIAL: 1 test(s) skipped: x.T.test_b (no network)%s\nCLOSED  0 unit(s): none\n")

    def test_a_skip_question_is_not_red_and_is_not_rerun_on_an_unchanged_tree(self):
        self.commit([{"id": "ACC2", "state": "PARTIAL", "done_check": "python3 -m unittest x"}])
        first, second = self.fake_closer(self.ASK % ""), self.fake_closer(self.ASK % " [already asked, waiting on the owner]")
        out1 = self.LB.closing_pass(["--all-eligible"], first, None, "hub", "b", plan_path=self.PLAN)
        out2 = self.LB.closing_pass(["--all-eligible"], second, None, "hub", "b", plan_path=self.PLAN)
        self.assertEqual(first.runs, ["ACC2"])
        self.assertTrue(any(l.startswith("NEEDS-INPUT ACC2: PARTIAL: 1 test(s) skipped: x.T.test_b") for l in out1), out1)
        self.assertFalse(any(l.startswith("CLOSE-RED") for l in out1 + out2), out1 + out2)
        self.assertEqual(second.runs, [], "pass 2 ran the done check again on an unchanged tree")
        self.assertTrue(any(l.startswith("CLOSE-SKIP ACC2: ") for l in out2), out2)
        self.assertEqual((self.rows(self.attempts), self.rows(self.ledger)), (1, 0))

    def test_a_skip_question_from_the_real_closer_through_the_real_closing_pass(self):
        """No scripted reply: close_unit.py itself runs the unit's done check (confined, in a copy) and the real closing_pass reads it."""
        from unittest import mock
        require_sandbox(self)
        unit = {"id": "A", "state": "PARTIAL", "sub_units": ["A.1"], "evidence": "A.1 landed initial.", "done_check": "python3 -B -m unittest skipmod"}
        with open("skipmod.py", "w") as fh:
            fh.write(SKIP_MODULE % "needs the owner key")
        self.commit([unit])
        env = {k: v for k, v in os.environ.items() if k != "BROTHER_WBS"}
        env.update(HOME=self.root, TMPDIR=self.root, BROTHER_EVIDENCE=self.ev)
        runs = []

        def real_closer(cmd, log, timeout=None):
            if any(str(c).endswith("close_unit.py") for c in cmd) and "--dry" not in cmd and "--needfact" not in cmd:
                runs.append(cmd[-1])
            return self.subprocess.run(cmd, capture_output=True, text=True, env=env, cwd=self.repo, timeout=300)
        argv = lambda code, args=(), py=None: [sys.executable, "-B", C.__file__] + list(args)
        with mock.patch.object(self.LB, "closer_argv", argv):
            out1 = self.LB.closing_pass(["A"], real_closer, None, "hub", "b", plan_path=self.PLAN)
            out2 = self.LB.closing_pass(["A"], real_closer, None, "hub", "b", plan_path=self.PLAN)
        self.assertTrue(any(l.startswith("NEEDS-INPUT A: PARTIAL: 1 test(s) skipped: skipmod.T.test_b (needs the owner key)") for l in out1), out1)
        self.assertFalse(any(l.startswith("CLOSE-RED") for l in out1 + out2), out1 + out2)
        self.assertEqual(runs, ["A"], "the done check ran again on an unchanged tree")
        self.assertTrue(any(l.startswith("CLOSE-SKIP A: ") for l in out2), out2)
        self.assertEqual((self.rows(self.attempts), self.rows(self.ledger)), (1, 0))
        self.assertEqual(len(open(os.path.join(self.ev, "brother-needs-input.jsonl")).read().splitlines()), 1)

    def test_a_sweep_that_cannot_run_is_said_not_swallowed_and_not_red(self):
        self.commit([{"id": "ACC2", "state": "PARTIAL", "done_check": "python3 -m unittest x"}])
        inner = self.fake_closer(self.ASK % "")

        def sh_(cmd, log, timeout=None):
            if "--needfact" in cmd:
                return self.subprocess.CompletedProcess(cmd, 2, "NO-DATA: the plan is unreadable (boom)\n", "")
            return inner(cmd, log, timeout)
        out = self.LB.closing_pass(["--all-eligible"], sh_, None, "hub", "b", plan_path=self.PLAN)
        self.assertTrue(any(l.startswith("NEEDS-INPUT NO-DATA: the NEEDFACT sweep did not run (exit 2") for l in out), out)

    def test_a_red_closure_that_merely_mentions_needs_input_stays_red(self):
        """One guard: only the PARTIAL skip question is exempt; any other NEEDS_INPUT text in a failing closer is still a red."""
        self.commit([{"id": "ACC2", "state": "PARTIAL", "done_check": "python3 -m unittest x"}])
        said = "ACC2  running its done_check: python3 -m unittest x\nACC2  NOT CLOSED: the done_check exited 1: FAIL: x\nACC2  NEEDS_INPUT: unrelated\n"
        out = self.LB.closing_pass(["--all-eligible"], self.fake_closer(said), None, "hub", "b", plan_path=self.PLAN)
        self.assertTrue(any(l.startswith("CLOSE-RED ACC2: the done_check exited 1") for l in out), out)

    def test_a_diagnosed_needfact_sub_unit_is_asked_from_the_closing_pass_the_loop_runs(self):
        self.commit([{"id": "A", "state": "PARTIAL", "sub_units": ["A.1"], "evidence": "", "done_check": "python3 -B check.py"}])
        run = os.path.join(self.ev, "unit-runs", "A.1-101010")
        os.makedirs(run)
        with open(os.path.join(run, "STATUS"), "w") as fh:
            fh.write("EXHAUSTED after 5\n")
        open(os.path.join(run, "DIAGNOSED"), "w").close()
        from unittest import mock
        env = {k: v for k, v in os.environ.items() if k != "BROTHER_WBS"}
        env.update(HOME=self.root, BROTHER_EVIDENCE=self.ev)

        def real_closer(cmd, log, timeout=None):   # close_unit.py itself, as the frozen closer would run it
            return self.subprocess.run(cmd, capture_output=True, text=True, env=env, cwd=self.repo, timeout=120)
        argv = lambda code, args=(), py=None: [sys.executable, "-B", C.__file__] + list(args)
        with mock.patch.object(self.LB, "closer_argv", argv):
            out1 = self.LB.closing_pass(["--all-eligible"], real_closer, None, "hub", "b", plan_path=self.PLAN)
            out2 = self.LB.closing_pass(["--all-eligible"], real_closer, None, "hub", "b", plan_path=self.PLAN)
        note = os.path.join(self.ev, "diag-notes", "A.txt")
        self.assertTrue(any(l.startswith("NEEDS-INPUT A: A.1 needs a fact") and note in l for l in out1), out1)
        self.assertFalse(any("NEEDS-INPUT" in l for l in out2), "the second pass asked again: %s" % out2)
        self.assertFalse(any(l.startswith("CLOSE-RED") for l in out1 + out2), out1 + out2)


if __name__ == "__main__":
    unittest.main(verbosity=1)

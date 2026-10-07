#!/usr/bin/env python3
"""closing_pass runs each unit's done check once per check and tree (owner 2026-10-03): ACC2's 1800 s check timed out, was
started again at once by the --all-eligible sweep, and loop_pass's --close would have started it a third time.

Run: python3 -B scripts/loop/test_one_closure_attempt.py
"""
import contextlib
import io
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import land_batch as LB  # noqa: E402

PLAN = "docs/plan/BROTHER-1.1.0-LAUNCH-WBS.json"
TIMED_OUT = ("ACC2  running its done_check: python3 scripts/donecheck_acc2.py --correctness\n", "NO-DATA: timed out after 1800s\n")


class FakeSh:
    """close_unit stand in: --dry lists ACC2 as eligible, a named ACC2 gives the scripted (rc, stdout, stderr)."""
    def __init__(self, result=(124,) + TIMED_OUT):
        self.result, self.runs = result, []

    def __call__(self, cmd, log, timeout=None):
        if any(str(c).endswith("close_unit.py") for c in cmd):
            if "--dry" in cmd:
                return subprocess.CompletedProcess(cmd, 1, "ACC2  running its done_check: python3 scripts/donecheck_acc2.py --correctness\nCLOSED  0 unit(s): none\n", "")
            self.runs.append(cmd[-1])
            return subprocess.CompletedProcess(cmd, *self.result)
        return subprocess.CompletedProcess(cmd, 0, "", "")


def fake_boxed(argv, log, timeout, env, cwd=None, root=None, seed=()):
    with open(argv[argv.index("--path") + 1], "a", encoding="utf-8") as fh:
        fh.write('{"class": "close"}\n')
    return subprocess.CompletedProcess(argv, 0, "", "")


class OneClosureAttempt(unittest.TestCase):
    def setUp(self):
        self.saved = LB.code_root, LB.boxed, LB.FAILURE_LEDGER, LB.CLOSE_ATTEMPTS, os.getcwd()
        _scope = os.environ.pop("BROTHER_SCOPE", None)   # a run's scope never leaks into these cases (2026-10-04)
        self.addCleanup(lambda: os.environ.__setitem__("BROTHER_SCOPE", _scope) if _scope is not None else None)
        self.tmp = tempfile.mkdtemp(prefix="one-close-")
        LB.code_root = lambda frozen=None: "/code"
        LB.boxed = fake_boxed
        LB.FAILURE_LEDGER = os.path.join(self.tmp, "ledger.jsonl")
        LB.CLOSE_ATTEMPTS = os.path.join(self.tmp, "attempts.jsonl")
        self.repo = os.path.join(self.tmp, "repo")
        os.makedirs(os.path.join(self.repo, "docs/plan"))
        os.chdir(self.repo)
        self.git("init", "-q")
        self.plan("python3 scripts/donecheck_acc2.py --correctness")
        self.commit("code.py", "a = 1\n")

    def tearDown(self):
        LB.code_root, LB.boxed, LB.FAILURE_LEDGER, LB.CLOSE_ATTEMPTS, cwd = self.saved
        os.chdir(cwd)
        shutil.rmtree(self.tmp, True)

    def git(self, *a):
        subprocess.run(["git", "-c", "user.name=t", "-c", "user.email=t@t", "-c", "core.hooksPath=/dev/null"] + list(a), check=True, capture_output=True)

    def plan(self, check, state="PARTIAL"):
        with open(PLAN, "w", encoding="utf-8") as fh:
            json.dump({"units": [{"id": "ACC2", "state": state, "done_check": check}]}, fh)

    def commit(self, path, text):
        with open(path, "w", encoding="utf-8") as fh:
            fh.write(text)
        self.git("add", "-A"); self.git("commit", "-q", "-m", "c")

    def attempts(self):
        with open(LB.CLOSE_ATTEMPTS, encoding="utf-8") as fh:
            return [json.loads(l) for l in fh if l.strip()]

    def ledger(self):
        with open(LB.FAILURE_LEDGER, encoding="utf-8") as fh:
            return [json.loads(l) for l in fh if l.strip()]

    def test_named_then_sweep_runs_the_timed_out_check_once(self):
        sh = FakeSh()
        out = LB.closing_pass(["ACC2"], sh, None, "hub", "b")
        self.assertEqual(sh.runs, ["ACC2"], "the --all-eligible sweep ran the named unit's check again")
        self.assertIn("CLOSE-RED ACC2: NO-DATA: timed out after 1800s", out)
        self.assertEqual([r["unit"] for r in self.ledger()], ["ACC2"])
        self.assertEqual([(r["unit"], r["rc"]) for r in self.attempts()], [("ACC2", 124)])

    def test_the_next_pass_on_the_same_tree_does_not_rerun_it(self):
        LB.closing_pass(["ACC2"], FakeSh(), None, "hub", "b")
        sh = FakeSh()
        out = LB.closing_pass(["--all-eligible"], sh, None, "hub", "b")   # loop_pass's --close
        self.assertEqual(sh.runs, [])
        self.assertTrue(any(l.startswith("CLOSE-SKIP ACC2: its done check already ran on this tree and check") for l in out), out)
        self.assertEqual(len(self.ledger()), 1, "a skip is not a second defect row")

    def test_a_plan_change_runs_it_again(self):
        # review finding 1: donecheck_L5.py reads only the plan, so a sibling's closure (a plan only commit) is new input
        LB.closing_pass(["ACC2"], FakeSh(), None, "hub", "b")
        with open(PLAN, "a", encoding="utf-8") as fh:
            fh.write("\n")
        self.git("add", "-A"); self.git("commit", "-q", "-m", "a sibling closed")
        sh = FakeSh()
        LB.closing_pass(["--all-eligible"], sh, None, "hub", "b")
        self.assertEqual(sh.runs, ["ACC2"])

    def test_a_dirty_tree_has_no_identity(self):
        with open("code.py", "w", encoding="utf-8") as fh:
            fh.write("a = 9\n")
        sh = FakeSh()
        LB.closing_pass(["ACC2"], sh, None, "hub", "b")
        self.assertEqual(sh.runs, ["ACC2"])
        self.assertFalse(os.path.exists(LB.CLOSE_ATTEMPTS))

    def test_a_refusal_where_no_check_ran_records_nothing(self):
        for said in ("ACC2  NOT CLOSED: its spec defines sub unit(s) the plan neither lists nor records as removed: ACC2.6\n",
                     "ACC2  NOT CLOSED: the done_check exited 2: NO-DATA: the done check cannot run confined on this host (no sandbox-exec); nothing ran\n",
                     "ACC2  NOT CLOSED: the done_check exited 2: NO-DATA: no disposable copy of the tree for the done check (x); nothing ran\n",
                     "Traceback (most recent call last):\nOSError: boom\n"):
            out = LB.closing_pass(["ACC2"], FakeSh((1, said, "")), None, "hub", "b")
            self.assertTrue(any(l.startswith("CLOSE-RED ACC2") for l in out), out)
        self.assertFalse(os.path.exists(LB.CLOSE_ATTEMPTS))

    def test_a_checks_own_no_data_answer_ran_and_is_recorded(self):
        said = "ACC2  NOT CLOSED: the done_check exited 2: NO-DATA: a run printed no summary line (serial False, parallel True)\n"
        LB.closing_pass(["ACC2"], FakeSh((2, said, "")), None, "hub", "b")
        self.assertEqual([r["rc"] for r in self.attempts()], [2])
        sh = FakeSh((2, said, ""))
        LB.closing_pass(["ACC2"], sh, None, "hub", "b")
        self.assertEqual(sh.runs, [])

    def test_an_interpreter_that_could_not_start_records_nothing(self):
        said = "ACC2  NOT CLOSED: the done_check exited 1: python3 could not run: [Errno 2] No such file\n"
        LB.closing_pass(["ACC2"], FakeSh((1, said, "")), None, "hub", "b")
        self.assertFalse(os.path.exists(LB.CLOSE_ATTEMPTS))

    def test_a_checks_own_could_not_run_answer_is_recorded(self):
        said = "ACC2  NOT CLOSED: the done_check exited 1: NO-DATA: the L5e audit could not run: 'x'\n"
        LB.closing_pass(["ACC2"], FakeSh((1, said, "")), None, "hub", "b")
        self.assertEqual(len(self.attempts()), 1)

    def test_an_unreadable_attempts_record_runs_the_check_and_says_so(self):
        os.makedirs(LB.CLOSE_ATTEMPTS)   # a directory where the record should be: IsADirectoryError, not absent
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            held = LB.prior_attempt("ACC2", ("c", "t"))
        self.assertIsNone(held)
        self.assertIn("CLOSE-ATTEMPTS NO-DATA", out.getvalue())

    def scoped(self, scope):
        saved = os.environ.get("BROTHER_SCOPE")
        os.environ["BROTHER_SCOPE"] = scope
        self.addCleanup(lambda: os.environ.__setitem__("BROTHER_SCOPE", saved) if saved is not None else os.environ.pop("BROTHER_SCOPE", None))

    def test_a_scoped_sweep_leaves_an_out_of_scope_unit_and_names_it(self):
        self.scoped("^(CV1|MG1|PR1)$")
        sh = FakeSh()
        out = LB.closing_pass(["--all-eligible"], sh, None, "hub", "b")
        self.assertEqual(sh.runs, [], "ACC2's check ran in a run scoped away from it")
        self.assertTrue(any(l.startswith("CLOSE-SCOPE 1 eligible unit(s) outside this run's scope") and "ACC2" in l for l in out), out)
        self.assertFalse(os.path.exists(LB.CLOSE_ATTEMPTS))

    def test_a_scoped_sweep_still_closes_an_in_scope_unit(self):
        self.scoped("^(ACC2)$")
        sh = FakeSh()
        LB.closing_pass(["--all-eligible"], sh, None, "hub", "b")
        self.assertEqual(sh.runs, ["ACC2"])

    def test_a_unit_the_pass_named_is_closed_whatever_the_scope(self):
        self.scoped("^(CV1)$")
        sh = FakeSh()
        LB.closing_pass(["ACC2"], sh, None, "hub", "b")
        self.assertEqual(sh.runs, ["ACC2"])

    def test_no_scope_is_release_wide(self):
        os.environ.pop("BROTHER_SCOPE", None)
        sh = FakeSh()
        LB.closing_pass(["--all-eligible"], sh, None, "hub", "b")
        self.assertEqual(sh.runs, ["ACC2"])

    def test_a_scope_that_is_not_a_regex_sweeps_nothing_and_says_so(self):
        self.scoped("^(CV1")
        sh = FakeSh()
        out = LB.closing_pass(["--all-eligible"], sh, None, "hub", "b")
        self.assertEqual(sh.runs, [])
        self.assertTrue(any("is not a regular expression; the sweep closed nothing" in l for l in out), out)

    def test_a_broken_sweep_is_said(self):
        class Crash(FakeSh):
            def __call__(self, cmd, log, timeout=None):
                if "--dry" in cmd: return subprocess.CompletedProcess(cmd, 1, "Traceback: boom\n", "")
                return FakeSh.__call__(self, cmd, log, timeout)
        out = LB.closing_pass(["ACC2"], Crash(), None, "hub", "b")
        self.assertTrue(any(l.startswith("CLOSE-RED --all-eligible: the sweep could not list") for l in out), out)

    def test_a_changed_tree_runs_it_again(self):
        LB.closing_pass(["ACC2"], FakeSh(), None, "hub", "b")
        self.commit("code.py", "a = 2\n")
        sh = FakeSh()
        LB.closing_pass(["--all-eligible"], sh, None, "hub", "b")
        self.assertEqual(sh.runs, ["ACC2"])

    def test_a_changed_check_runs_it_again(self):
        LB.closing_pass(["ACC2"], FakeSh(), None, "hub", "b")
        self.plan("python3 scripts/donecheck_acc2.py --correctness --quiet")
        self.git("add", "-A"); self.git("commit", "-q", "-m", "check text")
        sh = FakeSh()
        LB.closing_pass(["--all-eligible"], sh, None, "hub", "b")
        self.assertEqual(sh.runs, ["ACC2"])

    def test_timeouts_are_capped_whatever_the_tree(self):
        for text in ("a = 2\n", "a = 3\n"):
            LB.closing_pass(["ACC2"], FakeSh(), None, "hub", "b")
            self.commit("code.py", text)
        sh = FakeSh()
        out = LB.closing_pass(["ACC2"], sh, None, "hub", "b")
        self.assertEqual(sh.runs, [])
        self.assertTrue(any("timed out 2 times (cap 2) and waits for a person: with the loop stopped, run python3 scripts/close_unit.py ACC2" in l for l in out), out)

    def test_a_red_check_is_reported_once_and_not_rerun_unchanged(self):
        red = (1, "ACC2  NOT CLOSED: the done_check exited 1: DIFFERS x\nCLOSED  0 unit(s): none\n", "")
        out = LB.closing_pass(["ACC2"], FakeSh(red), None, "hub", "b")
        self.assertIn("CLOSE-RED ACC2: the done_check exited 1: DIFFERS x", out)
        sh = FakeSh(red)
        LB.closing_pass(["ACC2"], sh, None, "hub", "b")
        self.assertEqual(sh.runs, [])

    def test_a_green_close_records_no_attempt(self):
        sh = FakeSh((0, "ACC2  DONE: PASS\nCLOSED  1 unit(s): ACC2\n", ""))
        LB.closing_pass(["ACC2"], sh, None, "hub", "b")
        self.assertEqual(sh.runs, ["ACC2"])
        self.assertFalse(os.path.exists(LB.CLOSE_ATTEMPTS))

    def test_no_identity_runs_the_check(self):
        os.remove(PLAN)
        sh = FakeSh()
        LB.closing_pass(["ACC2"], sh, None, "hub", "b")
        self.assertEqual(sh.runs, ["ACC2"])
        self.assertFalse(os.path.exists(LB.CLOSE_ATTEMPTS))


if __name__ == "__main__":
    unittest.main()

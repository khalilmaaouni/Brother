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
            if "--needfact" in cmd:   # STAB1.f: the sweep close_unit answers with exit 0 and nothing when no sub unit needs a fact
                return subprocess.CompletedProcess(cmd, 0, "", "")
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
            # FIXTURE ISOLATED 2026-11-01 by the integrator (assertions unchanged): each refusal kind is its own first
            # refusal, so each gets a fresh infrastructure wait ledger; sharing one made the third refusal wait on the
            # second one's row (LOOP1.e slice 2, InfrastructureWaitIsItsOwnLedger), which is that class's subject.
            saved_waits = getattr(LB, "CLOSE_WAITS", None)
            LB.CLOSE_WAITS = os.path.join(self.tmp, "waits-%d.jsonl" % len(said))
            try:
                out = LB.closing_pass(["ACC2"], FakeSh((1, said, "")), None, "hub", "b")
            finally:
                LB.CLOSE_WAITS = saved_waits
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


#: A closer that timed out before its done check started: no "running its done_check" line reached the output.
PRE_LAUNCH = ("", "NO-DATA: timed out after 1800s\n")


class QueueTimeoutIsNotAFailure(unittest.TestCase):
    """LOOP1.e (RC08, 1.2.0 Recovery contract), entry point: a timeout before the check started is a queue wait, not a
    test result. It neither holds the unit on its tree nor counts toward CLOSE_TIMEOUT_CAP, whose value stays 2. A
    timeout after the check started keeps today's meaning (the OneClosureAttempt cases above). Frozen by the integrator
    before the implementation; the fixture is OneClosureAttempt's own."""
    setUp, tearDown = OneClosureAttempt.setUp, OneClosureAttempt.tearDown
    git, plan, commit, attempts, ledger = (OneClosureAttempt.git, OneClosureAttempt.plan, OneClosureAttempt.commit,
                                           OneClosureAttempt.attempts, OneClosureAttempt.ledger)

    def test_a_timeout_before_the_check_started_reruns_on_the_same_tree(self):
        LB.closing_pass(["ACC2"], FakeSh((124,) + PRE_LAUNCH), None, "hub", "b")
        sh = FakeSh()
        out = LB.closing_pass(["ACC2"], sh, None, "hub", "b")
        self.assertEqual(sh.runs, ["ACC2"], "a pre-launch timeout held the unit as if its check had run")
        self.assertFalse(any(l.startswith("CLOSE-SKIP ACC2") for l in out), out)

    def test_pre_launch_timeouts_never_reach_the_cap(self):
        for _ in range(3):
            sh = FakeSh((124,) + PRE_LAUNCH)
            out = LB.closing_pass(["ACC2"], sh, None, "hub", "b")
            self.assertEqual(sh.runs, ["ACC2"], out)
            self.assertFalse(any("waits for a person" in l for l in out), out)
        self.assertEqual(LB.CLOSE_TIMEOUT_CAP, 2, "the existing limit is read, never raised")

    def test_queue_waits_are_bounded_too(self):
        # Recover WITHOUT endless retries: a closer that never prints its running line (an installed closer older than
        # the flush) would otherwise read every real timeout as a queue wait and rerun it forever.
        self.assertEqual(LB.QUEUE_WAIT_CAP, 3)
        for _ in range(LB.QUEUE_WAIT_CAP):
            LB.closing_pass(["ACC2"], FakeSh((124,) + PRE_LAUNCH), None, "hub", "b")
        sh = FakeSh((124,) + PRE_LAUNCH)
        out = LB.closing_pass(["ACC2"], sh, None, "hub", "b")
        self.assertEqual(sh.runs, [], out)
        self.assertTrue(any("queue" in l and "waits for a person" in l for l in out), out)
        self.commit("code.py", "a = 9\n")
        sh = FakeSh((124,) + PRE_LAUNCH)
        LB.closing_pass(["ACC2"], sh, None, "hub", "b")
        self.assertEqual(sh.runs, ["ACC2"], "the queue bound is per check and tree, like the one attempt rule")

    def test_another_units_running_line_does_not_start_this_one(self):
        said = ["ACC9  running its done_check: python3 y.py", "NO-DATA: timed out after 1800s"]
        self.assertFalse(LB.check_started("ACC2", 124, said))
        self.assertTrue(LB.check_started("ACC9", 124, said))
        self.assertTrue(LB.check_started("ACC2", 1, said), "only a timeout can be a queue wait")

    def test_the_closer_flushes_its_running_line_before_the_check(self):
        import ast
        path = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "close_unit.py")
        with open(path, encoding="utf-8") as fh:
            tree = ast.parse(fh.read())
        calls = [n for n in ast.walk(tree) if isinstance(n, ast.Call) and getattr(n.func, "id", "") == "print"
                 and "running its done_check" in ast.dump(n)]
        self.assertEqual(len(calls), 1)
        self.assertTrue(any(k.arg == "flush" and getattr(k.value, "value", None) is True for k in calls[0].keywords),
                        "close_unit's running line must be flushed, or a timeout loses it and reads as a queue wait")

    def test_a_flushed_line_survives_the_timeout_kill_and_an_unflushed_one_does_not(self):
        line = "ACC2  running its done_check: python3 x.py"
        for flush, started in ((True, True), (False, False)):
            child = "import time; print(%r, flush=%r); time.sleep(30)" % (line, flush)
            r = LB.run_group([sys.executable, "-c", child], 2, dict(os.environ))
            self.assertEqual(r.returncode, 124)
            said = ((r.stdout or "") + (r.stderr or "")).splitlines()
            self.assertIs(LB.check_started("ACC2", 124, said), started, (flush, said))

    def test_a_pre_launch_timeout_is_kept_as_history_not_as_a_failure(self):
        LB.closing_pass(["ACC2"], FakeSh((124,) + PRE_LAUNCH), None, "hub", "b")
        rows = self.attempts() if os.path.exists(LB.CLOSE_ATTEMPTS) else []
        for row in rows:
            self.assertEqual(row.get("kind"), "queue", row)
        out = LB.closing_pass(["ACC2"], FakeSh((124,) + PRE_LAUNCH), None, "hub", "b")
        self.assertFalse(any("test failed" in l.lower() for l in out), out)

    def test_a_started_timeout_after_queue_waits_still_counts(self):
        LB.closing_pass(["ACC2"], FakeSh((124,) + PRE_LAUNCH), None, "hub", "b")
        LB.closing_pass(["ACC2"], FakeSh(), None, "hub", "b")
        sh = FakeSh()
        out = LB.closing_pass(["ACC2"], sh, None, "hub", "b")
        self.assertEqual(sh.runs, [], "a check that started and timed out on this tree must not rerun unchanged")
        self.assertTrue(any(l.startswith("CLOSE-SKIP ACC2") for l in out), out)


SANDBOX_REFUSED = "ACC2  NOT CLOSED: the done_check exited 2: NO-DATA: the done check cannot run confined on this host (no sandbox-exec); nothing ran\n"
NO_COPY = "ACC2  NOT CLOSED: the done_check exited 2: NO-DATA: no disposable copy of the tree for the done check (x); nothing ran\n"
CLOSED_OK = "CLOSED  ACC2: ok\n"


class FakeShutil:
    """land_batch's view of the host: sandbox-exec present or not, and the free bytes on the volume."""
    def __init__(self, sandbox=True, free=50 << 30):
        self.sandbox, self.free = sandbox, free

    def which(self, name):
        return "/usr/bin/" + name if (name != "sandbox-exec" or self.sandbox) else None

    def disk_usage(self, path):
        return shutil.disk_usage(path)._replace(free=self.free)

    def __getattr__(self, name):
        return getattr(shutil, name)


class InfrastructureWaitIsItsOwnLedger(unittest.TestCase):
    """LOOP1.e slice 2 (RC08, RC13; class 1 decision 2026-10-31 night). A closure refused because nothing could run (no
    sandbox, no disposable copy) still records NOTHING in close-attempts (owner rule 2026-10-03, pinned above), but its
    environment is appended to a SEPARATE ledger, CLOSE_WAITS. The next pass asks closure_retry: an unchanged environment
    WAITS, a recovered one RUNS, and the retries stop at CLOSE_TIMEOUT_CAP (read, never raised) for a person. Frozen by the
    integrator before the implementation; the fixture is OneClosureAttempt's own."""
    git, plan, commit, attempts, ledger = (OneClosureAttempt.git, OneClosureAttempt.plan, OneClosureAttempt.commit,
                                           OneClosureAttempt.attempts, OneClosureAttempt.ledger)

    def setUp(self):
        OneClosureAttempt.setUp(self)
        self.saved_waits = getattr(LB, "CLOSE_WAITS", None), getattr(LB, "environment_identity", None)
        LB.CLOSE_WAITS = os.path.join(self.tmp, "waits.jsonl")
        self.env = "env-A"
        if self.saved_waits[1] is not None:
            LB.environment_identity = lambda: self.env

    def tearDown(self):
        LB.CLOSE_WAITS, env_fn = self.saved_waits
        if env_fn is not None:
            LB.environment_identity = env_fn
        OneClosureAttempt.tearDown(self)

    def need(self):
        self.assertIsNotNone(self.saved_waits[1], "land_batch defines no environment_identity")

    def waits(self):
        if not os.path.exists(LB.CLOSE_WAITS):
            return []
        with open(LB.CLOSE_WAITS, encoding="utf-8") as fh:
            return [json.loads(l) for l in fh if l.strip()]

    def test_a_sandbox_refusal_writes_one_wait_row_and_no_attempt(self):
        self.need()
        LB.closing_pass(["ACC2"], FakeSh((2, SANDBOX_REFUSED, "")), None, "hub", "b")
        self.assertFalse(os.path.exists(LB.CLOSE_ATTEMPTS), "an infrastructure refusal wrote close-attempts")
        rows = self.waits()
        self.assertEqual(1, len(rows), rows)
        self.assertEqual(("ACC2", "env-A"), (rows[0].get("unit"), rows[0].get("environment")))
        self.assertTrue(rows[0].get("check") and rows[0].get("tree"), rows[0])

    def test_an_unchanged_environment_waits_and_does_not_run(self):
        self.need()
        LB.closing_pass(["ACC2"], FakeSh((2, NO_COPY, "")), None, "hub", "b")
        sh = FakeSh((2, NO_COPY, ""))
        out = LB.closing_pass(["ACC2"], sh, None, "hub", "b")
        self.assertEqual([], sh.runs, "the closer ran again with nothing in its environment changed")
        self.assertTrue(any(l.startswith("CLOSE-SKIP ACC2") and "environment" in l for l in out), out)

    def test_a_recovered_environment_runs_again_on_the_same_tree(self):
        self.need()
        LB.closing_pass(["ACC2"], FakeSh((2, SANDBOX_REFUSED, "")), None, "hub", "b")
        self.env = "env-B"
        sh = FakeSh((0, CLOSED_OK, ""))
        LB.closing_pass(["ACC2"], sh, None, "hub", "b")
        self.assertEqual(["ACC2"], sh.runs, "a recovered environment did not permit the retry")

    def test_recovered_retries_stop_at_the_cap_for_a_person(self):
        self.need()
        for env in ("env-A", "env-B"):
            self.env = env
            LB.closing_pass(["ACC2"], FakeSh((2, SANDBOX_REFUSED, "")), None, "hub", "b")
        self.assertEqual(LB.CLOSE_TIMEOUT_CAP, len(self.waits()))
        self.env = "env-C"
        sh = FakeSh((0, CLOSED_OK, ""))
        out = LB.closing_pass(["ACC2"], sh, None, "hub", "b")
        self.assertEqual([], sh.runs, "retries passed the limit")
        self.assertTrue(any(l.startswith("CLOSE-SKIP ACC2") and "person" in l for l in out), out)
        self.assertEqual(2, LB.CLOSE_TIMEOUT_CAP, "the limit was raised")

    def test_an_unreadable_environment_never_reads_as_recovered(self):
        self.need()
        LB.closing_pass(["ACC2"], FakeSh((2, SANDBOX_REFUSED, "")), None, "hub", "b")
        self.env = ""
        sh = FakeSh((0, CLOSED_OK, ""))
        out = LB.closing_pass(["ACC2"], sh, None, "hub", "b")
        self.assertEqual([], sh.runs, "an unknown environment authorized a retry")
        self.assertTrue(any(l.startswith("CLOSE-SKIP ACC2") for l in out), out)

    def test_a_refusal_that_is_not_infrastructure_writes_no_wait(self):
        self.need()
        for said in ("ACC2  NOT CLOSED: its spec defines sub unit(s) the plan neither lists nor records as removed: ACC2.6\n",
                     "Traceback (most recent call last):\nOSError: boom\n"):
            LB.closing_pass(["ACC2"], FakeSh((1, said, "")), None, "hub", "b")
        self.assertEqual([], self.waits())
        self.assertFalse(os.path.exists(LB.CLOSE_ATTEMPTS))

    def test_an_assertion_failure_is_not_retried_by_an_environment_change(self):
        self.need()
        red = "ACC2  NOT CLOSED: the done_check exited 1: FAIL test_x\n"
        LB.closing_pass(["ACC2"], FakeSh((1, red, "")), None, "hub", "b")
        self.env = "env-B"
        sh = FakeSh((1, red, ""))
        LB.closing_pass(["ACC2"], sh, None, "hub", "b")
        self.assertEqual([], sh.runs, "an unchanged assertion failure ran again because the environment moved")
        self.assertEqual([], self.waits())

    def test_an_unreadable_waits_ledger_runs_the_check_and_says_so(self):
        self.need()
        os.makedirs(LB.CLOSE_WAITS)
        sh = FakeSh((0, CLOSED_OK, ""))
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            LB.closing_pass(["ACC2"], sh, None, "hub", "b")
        self.assertEqual(["ACC2"], sh.runs)
        self.assertIn("NO-DATA", buf.getvalue())

    def test_the_environment_reads_sandbox_and_disk_admission(self):
        self.need()
        fn, saved = self.saved_waits[1], LB.shutil
        seen = {}
        try:
            for name, fake in (("ok", FakeShutil()), ("no-sandbox", FakeShutil(sandbox=False)), ("no-disk", FakeShutil(free=1 << 20))):
                LB.shutil = fake
                seen[name] = fn()
            LB.shutil = FakeShutil()
            again = fn()
        finally:
            LB.shutil = saved
        self.assertTrue(all(isinstance(v, str) and v for v in seen.values()), seen)
        self.assertEqual(3, len(set(seen.values())), "sandbox or disk admission does not move the environment: %r" % seen)
        self.assertEqual(seen["ok"], again, "the environment is not stable on an unchanged host")


class UnreadableDisk(FakeShutil):
    def disk_usage(self, path):
        raise OSError("volume gone")


class InfrastructureWaitEdges(unittest.TestCase):
    """Integrator cases for the two guards the slice 2 implementer's probe left alive: an interpreter that could not
    start is not an infrastructure refusal (it writes no wait), and a host fact that cannot be read gives no identity."""
    setUp, tearDown, waits, need = (InfrastructureWaitIsItsOwnLedger.setUp, InfrastructureWaitIsItsOwnLedger.tearDown,
                                    InfrastructureWaitIsItsOwnLedger.waits, InfrastructureWaitIsItsOwnLedger.need)
    git, plan, commit = OneClosureAttempt.git, OneClosureAttempt.plan, OneClosureAttempt.commit

    def test_an_interpreter_that_could_not_start_writes_no_wait(self):
        self.need()
        said = "ACC2  NOT CLOSED: the done_check exited 1: python3 could not run: [Errno 2] No such file\n"
        LB.closing_pass(["ACC2"], FakeSh((1, said, "")), None, "hub", "b")
        self.assertEqual([], self.waits())

    def test_an_unreadable_host_fact_gives_no_identity(self):
        self.need()
        fn, saved = self.saved_waits[1], LB.shutil
        try:
            LB.shutil = UnreadableDisk()
            got = fn()
        finally:
            LB.shutil = saved
        self.assertEqual("", got)


if __name__ == "__main__":
    unittest.main()

"""What SpawningWorker must keep true.

The single most important assertion in this file is that the adapter never
returns "pending". Everything else here is about the verdict table, but
"pending" is the state that parks a run in EXECUTING and makes a human the
thing that moves it forward. An adapter that spawns and still answers "pending"
would pass every other test and deliver nothing.

The failure paths are driven with an injected runner rather than real
processes, because a missing executable and a timeout cannot be provoked
reliably by running something real, and a test that cannot force the bad state
is not testing it.
"""
import json
import os
import subprocess
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import bm_worker_spawn as S  # noqa: E402


class Completed(object):
    """The two fields of subprocess.CompletedProcess this adapter reads."""

    def __init__(self, returncode=0, stdout="", stderr=""):
        self.returncode = returncode
        self.stdout = stdout
        self.stderr = stderr


def runner_returning(**kw):
    def _run(argv, **_ignored):
        return Completed(**kw)
    return _run


def runner_raising(exc):
    def _run(argv, **_ignored):
        raise exc
    return _run


GOOD = json.dumps({"worker_claim": "did the thing", "artifacts": ["a.py"],
                   "cost": {"tokens": 12, "minutes": 3}})


class TheWholePoint(unittest.TestCase):
    def test_a_real_process_runs_and_the_status_is_returned(self):
        w = S.SpawningWorker([sys.executable, "-c",
                              'import json,sys;sys.stdin.read();'
                              'print(json.dumps({"worker_claim":"ok","artifacts":[]}))'])
        got = w.run({"unit_id": "u1", "objective": "prove it"})
        self.assertEqual(got["status"], "returned")
        self.assertEqual(got["worker_claim"], "ok")

    def test_it_NEVER_returns_pending_on_any_path(self):
        """RecordIntentWorker returns 'pending' and parks the run, which is the
        state this adapter exists to remove. Every branch is walked here: if any
        of them ever answers 'pending', the loop stops closing and a person is
        back in the middle of it."""
        cases = [
            S.SpawningWorker(["x"], runner=runner_returning(stdout=GOOD)),
            S.SpawningWorker(["x"], runner=runner_returning(returncode=3)),
            S.SpawningWorker(["x"], runner=runner_returning(stdout="not json")),
            S.SpawningWorker(["x"], runner=runner_raising(OSError("no such file"))),
            S.SpawningWorker(["x"], runner=runner_raising(
                subprocess.TimeoutExpired(cmd="x", timeout=1))),
        ]
        for w in cases:
            self.assertNotEqual(w.run({"unit_id": "u"})["status"], "pending")

    def test_the_brief_reaches_the_child_on_stdin_not_argv(self):
        """A brief on the command line is both mangled by quoting and readable
        by every other process on the machine."""
        seen = {}

        def _run(argv, **kw):
            seen.update(kw)
            seen["argv"] = argv
            return Completed(stdout=GOOD)

        w = S.SpawningWorker(["x"], runner=_run)
        w.run({"unit_id": "u1", "objective": "o"})
        self.assertIn("u1", seen["input"])
        self.assertEqual(seen["argv"], ["x"])


class CouldNotRunIsNotDidNotWork(unittest.TestCase):
    """unavailable and malformed are different verdicts to the engine's circuit
    breaker. Collapsing a broken PATH into a failing unit is how a machine
    problem gets recorded as a code problem."""

    def test_a_missing_executable_is_unavailable(self):
        w = S.SpawningWorker(["nope"], runner=runner_raising(OSError("nope")))
        self.assertEqual(w.run({})["status"], "unavailable")

    def test_a_timeout_is_unavailable_and_says_so(self):
        w = S.SpawningWorker(["x"], timeout=7, runner=runner_raising(
            subprocess.TimeoutExpired(cmd="x", timeout=7)))
        got = w.run({})
        self.assertEqual(got["status"], "unavailable")
        self.assertIn("7", got["note"])

    def test_a_non_zero_exit_is_unavailable_and_keeps_the_stderr(self):
        w = S.SpawningWorker(["x"], runner=runner_returning(
            returncode=2, stderr="it broke"))
        got = w.run({})
        self.assertEqual(got["status"], "unavailable")
        self.assertIn("it broke", got["note"])


class RanButSaidSomethingUnreadable(unittest.TestCase):
    def test_exit_zero_with_non_json_is_malformed(self):
        w = S.SpawningWorker(["x"], runner=runner_returning(stdout="hello"))
        self.assertEqual(w.run({})["status"], "malformed")

    def test_exit_zero_with_a_json_list_is_malformed(self):
        w = S.SpawningWorker(["x"], runner=runner_returning(stdout="[1,2]"))
        self.assertEqual(w.run({})["status"], "malformed")

    def test_a_missing_required_key_is_malformed_and_names_it(self):
        w = S.SpawningWorker(["x"], runner=runner_returning(
            stdout=json.dumps({"worker_claim": "x"})))
        got = w.run({})
        self.assertEqual(got["status"], "malformed")
        self.assertIn("artifacts", got["note"])

    def test_artifacts_that_are_not_a_list_is_malformed(self):
        w = S.SpawningWorker(["x"], runner=runner_returning(
            stdout=json.dumps({"worker_claim": "x", "artifacts": "a.py"})))
        self.assertEqual(w.run({})["status"], "malformed")

    def test_a_good_answer_carries_its_cost_through(self):
        w = S.SpawningWorker(["x"], runner=runner_returning(stdout=GOOD))
        got = w.run({})
        self.assertEqual(got["status"], "returned")
        self.assertEqual(got["cost"], {"tokens": 12, "minutes": 3})
        self.assertEqual(got["artifacts"], ["a.py"])

    def test_real_usage_in_the_cost_dict_is_forwarded_as_its_own_key(self):
        """T1 follow-up: model_worker.py now puts tokens_in/tokens_out/
        tokens_cached (real numbers off the claude CLI's own answer) into
        its "cost" dict. This adapter must forward them, additively, as
        their own "usage" key, in the names scripts/brother_run.py's
        _sum_usage_field already expects."""
        stdout = json.dumps({"worker_claim": "did the thing", "artifacts": ["a.py"],
                             "cost": {"tokens_in": 100, "tokens_out": 40,
                                      "tokens_cached": 25}})
        w = S.SpawningWorker(["x"], runner=runner_returning(stdout=stdout))
        got = w.run({})
        self.assertEqual(got["usage"], {"tokens_in": 100, "tokens_out": 40,
                                        "tokens_cached": 25})

    def test_the_cache_write_count_rides_the_same_passthrough(self):
        """E92: model_worker.py now also puts tokens_cache_write (the claude
        CLI's cache_creation_input_tokens) in its cost dict, and without it
        reaching brother_run no cache share can be computed at all."""
        stdout = json.dumps({"worker_claim": "did it", "artifacts": ["a.py"],
                             "cost": {"tokens_in": 30, "tokens_out": 40,
                                      "tokens_cached": 22972,
                                      "tokens_cache_write": 70272}})
        w = S.SpawningWorker(["x"], runner=runner_returning(stdout=stdout))
        self.assertEqual(w.run({})["usage"]["tokens_cache_write"], 70272)

    def test_no_usage_in_the_cost_dict_leaves_the_key_off_the_answer(self):
        """The backwards case, and today's ordinary shape (GOOD above): a
        cost dict with no tokens_in/out/cached must never manufacture a
        usage key, empty or otherwise. Absent means "not reported", which
        is what build_cost_block's own NO-DATA reading depends on."""
        w = S.SpawningWorker(["x"], runner=runner_returning(stdout=GOOD))
        got = w.run({})
        self.assertNotIn("usage", got)


class EveryPathReturnsTheSameShape(unittest.TestCase):
    def test_no_caller_needs_to_know_which_branch_it_took(self):
        """A failure that returns a differently shaped dict than a success is
        how a consumer gets a KeyError on the unhappy path only."""
        for w in (S.SpawningWorker(["x"], runner=runner_returning(stdout=GOOD)),
                  S.SpawningWorker(["x"], runner=runner_returning(returncode=1)),
                  S.SpawningWorker(["x"], runner=runner_returning(stdout="no")),
                  S.SpawningWorker(["x"], runner=runner_raising(OSError("x")))):
            got = w.run({})
            for key in ("worker_claim", "artifacts", "cost", "status"):
                self.assertIn(key, got)
            self.assertIsInstance(got["artifacts"], list)
            self.assertIn("tokens", got["cost"])


class TheChildDoesNotInheritGitRedirection(unittest.TestCase):
    """The controller records a first-hand reproduction where an inherited
    GIT_DIR and GIT_WORK_TREE sent a rollback into a different repository and
    destroyed an uncommitted edit there, exiting 0 so nobody was warned. A
    spawner that skipped the sanitiser would reintroduce exactly that."""

    def test_no_GIT_variable_reaches_the_child(self):
        seen = {}

        def _run(argv, **kw):
            seen.update(kw)
            return Completed(stdout=GOOD)

        dirty = {"PATH": "/usr/bin", "HOME": "/home/x",
                 "GIT_DIR": "/elsewhere/.git",
                 "GIT_WORK_TREE": "/elsewhere",
                 "GIT_CONFIG_PARAMETERS": "core.hooksPath=/evil"}
        w = S.SpawningWorker(["x"], runner=_run, environ=dirty)
        w.run({})
        leaked = [k for k in seen["env"] if k.startswith("GIT_")]
        self.assertEqual(leaked, [], "these reached the child: %s" % leaked)

    def test_the_ordinary_environment_still_gets_through(self):
        """An empty environment would break every real done_check for reasons
        that have nothing to do with safety."""
        seen = {}

        def _run(argv, **kw):
            seen.update(kw)
            return Completed(stdout=GOOD)

        w = S.SpawningWorker(["x"], runner=_run,
                             environ={"PATH": "/usr/bin", "GIT_DIR": "/x"})
        w.run({})
        self.assertEqual(seen["env"].get("PATH"), "/usr/bin")


class RefusesToSpawnNothing(unittest.TestCase):
    def test_an_empty_argv_is_refused_at_construction(self):
        """An adapter built with no command would start nothing and report
        success, which is the quietest possible way to do no work."""
        with self.assertRaises(ValueError):
            S.SpawningWorker([])


class TheSelftestIsRunnableByHand(unittest.TestCase):
    def test_selftest_exits_zero(self):
        self.assertEqual(S.main(["--selftest"]), 0)

    def test_a_bare_invocation_refuses_rather_than_pretending(self):
        self.assertEqual(S.main([]), 2)


class TheExecPrimitiveIsPinnedHereToo(unittest.TestCase):
    """bm_controller pins its subprocess sites structurally so that a call site
    added later inherits the sanitised environment BY CONSTRUCTION rather than by
    someone remembering to ask for it. That pin reads bm_controller.py and only
    bm_controller.py, so this module is a SECOND execution site outside it. The
    guarantee was never meant to be per file, so the same discipline is asserted
    here rather than left to the unit tests above, which prove behaviour and
    would not notice a third call site appearing."""

    def _tree(self):
        import ast
        with open(S.__file__, encoding="utf-8") as fh:
            return ast.parse(fh.read(), filename="bm_worker_spawn.py"), ast

    def test_subprocess_is_reached_in_exactly_one_place(self):
        tree, ast = self._tree()
        calls = [n.lineno for n in ast.walk(tree)
                 if isinstance(n, ast.Attribute)
                 and isinstance(n.value, ast.Name) and n.value.id == "subprocess"
                 and n.attr in ("run", "Popen", "call", "check_output",
                                "check_call")]
        self.assertEqual(len(calls), 1,
                         "subprocess execution reached at lines %s; this module "
                         "is allowed exactly one, the injected default" % calls)

    def test_every_spawn_goes_through_the_sanitiser(self):
        """A future edit that drops the sanitiser would leave the behaviour
        tests above still passing on the injected runner, because an injected
        runner never reads env at all unless a test looks.

        Read as CODE, not as text. The first version of this counted the
        substring "_sanitised_env(" and matched the docstring as well as the
        call, so it failed against a module that was entirely correct. A check
        that manufactures a violation costs more than one that misses it,
        because it sends someone to fix work that was already right."""
        tree, ast = self._tree()
        unsanitised = []
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            for kw in node.keywords or []:
                if kw.arg != "env":
                    continue
                ok = (isinstance(kw.value, ast.Call)
                      and isinstance(kw.value.func, ast.Attribute)
                      and kw.value.func.attr == "_sanitised_env")
                if not ok:
                    unsanitised.append(kw.value.lineno)
        self.assertEqual(unsanitised, [],
                         "env= passed without the sanitiser at line(s) %s"
                         % unsanitised)


class Night0912BmWorkerSpawn(unittest.TestCase):
    def test_non_numeric_tokens_is_malformed(self):
        w = S.SpawningWorker(["x"], runner=runner_returning(
            stdout=json.dumps({"worker_claim": "ok", "artifacts": [],
                               "cost": {"tokens": "abc"}})))
        res = w.run({})
        self.assertEqual(res.get("status"), "malformed")


def _alive(pid):
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    # A killed process not yet reaped by its new parent still answers kill(0); ps says Z.
    out = subprocess.run(["ps", "-o", "state=", "-p", str(pid)],
                         stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)
    return out.stdout.strip()[:1] not in (b"", b"Z")


@unittest.skipUnless(os.name == "posix", "POSIX process groups")
class TimeoutStopsDescendants(unittest.TestCase):
    def test_inner_model_timeout_also_cancels_its_descendants(self):
        import pathlib
        import tempfile
        import time
        with tempfile.TemporaryDirectory() as root:
            target = pathlib.Path(root) / "late-write"
            child = "import time,pathlib; time.sleep(1); pathlib.Path(%r).write_text('late')" % str(target)
            parent = ("import subprocess,sys; "
                      "subprocess.Popen([sys.executable,'-c',%r], "
                      "stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL); "
                      "print('failure_class=timeout; model interrupted',file=sys.stderr); "
                      "sys.exit(3)" % child)
            worker = S.SpawningWorker([sys.executable, "-c", parent], timeout=5)
            got = worker.run({"unit_id": "inner-timeout"})
            self.assertEqual(got["status"], "unavailable")
            time.sleep(1.2)
            self.assertFalse(target.exists(), "descendant survived inner model timeout")

    def test_grandchild_cannot_write_after_parent_timeout(self):
        import pathlib
        import tempfile
        import time
        with tempfile.TemporaryDirectory() as root:
            target = pathlib.Path(root) / "late-write"
            pidfile = pathlib.Path(root) / "grandchild.pid"
            # The grandchild inherits the worker's output pipes and writes `target` BEFORE it
            # exits, so a pipe EOF that waited on its natural end always finds `target` present.
            # Events, not a stopwatch: no assertion here depends on how busy this machine is.
            child = ("import os,time,pathlib; t=pathlib.Path(%r); "
                     "t.write_text(str(os.getpid())); os.replace(str(t),%r); "
                     "time.sleep(30); pathlib.Path(%r).write_text('late')"
                     % (str(pidfile) + ".tmp", str(pidfile), str(target)))
            parent = ("import subprocess,sys,time; "
                      "subprocess.Popen([sys.executable,'-c',%r]); "
                      "time.sleep(60)" % child)
            worker = S.SpawningWorker([sys.executable, "-c", parent], timeout=5)
            got = worker.run({"unit_id": "descendants"})
            self.assertEqual(got["status"], "unavailable")
            self.assertTrue(pidfile.exists(),
                            "the grandchild never started inside the timeout, so this run proved nothing")
            self.assertFalse(target.exists(), "descendant held the output pipes open")
            pid = int(pidfile.read_text())
            deadline = time.monotonic() + 10
            while time.monotonic() < deadline and _alive(pid):
                time.sleep(0.05)
            alive = _alive(pid)
            if alive:
                os.kill(pid, 9)
            self.assertFalse(alive, "descendant survived the timeout, free to write after it")


class _SetsidDaemonRun(object):
    """One real run, shared by every test in the class that mixes this in: a
    worker whose descendant calls setsid() (ssh-agent, gpg-agent, a build or
    language-server daemon a tool started), leaving the worker's process group
    so the group kill cannot reach it, and keeps the inherited stdout and
    stderr open. TAIL is what the worker does once the daemon is out."""

    TIMEOUT = 2.0
    GRACE = 0.5
    DAEMON_SECONDS = 30
    TAIL = ""

    @classmethod
    def setUpClass(cls):
        import shutil
        import tempfile
        import time
        from unittest import mock
        cls.root = tempfile.mkdtemp(prefix="bm-spawn-daemon-")
        cls.addClassCleanup(shutil.rmtree, cls.root, True)
        cls.addClassCleanup(cls._kill_daemon)
        cls.pidfile = os.path.join(cls.root, "daemon.pid")
        daemon = ("import os,pathlib,time; os.setsid(); "
                  "pathlib.Path(%r).write_text(str(os.getpid())); time.sleep(%d)"
                  % (cls.pidfile, cls.DAEMON_SECONDS))
        # The worker waits until the daemon has really left the group before
        # it prints and hangs, so a slow start cannot let the group kill take
        # the daemon too and pass this test without the condition it names.
        worker = ("import os, subprocess, sys, time\n"
                  "subprocess.Popen([sys.executable, '-c', %r])\n"
                  "while not os.path.exists(%r): time.sleep(0.02)\n"
                  "%s" % (daemon, cls.pidfile, cls.TAIL))
        made, raised = [], []

        class Recording(subprocess.Popen):
            # Kept alive here: Popen.__del__ polls, so a collected Popen gets
            # reaped by the collector and would hide a missing wait().
            def __init__(self, *a, **kw):
                super(Recording, self).__init__(*a, **kw)
                made.append(self)

        def runner(*a, **kw):
            try:
                return S._run_process(*a, **kw)
            except subprocess.TimeoutExpired as exc:
                raised.append(exc)
                raise

        with mock.patch.object(S, "DRAIN_GRACE_SECONDS", cls.GRACE), \
                mock.patch.object(S.subprocess, "Popen", Recording):
            started = time.monotonic()
            cls.got = S.SpawningWorker([sys.executable, "-c", worker],
                                       timeout=cls.TIMEOUT,
                                       runner=runner).run({"unit_id": "daemon"})
            cls.elapsed = time.monotonic() - started
        cls.proc = made[0] if made else None
        cls.raised = raised[0] if raised else None

    @classmethod
    def _kill_daemon(cls):
        import signal
        try:
            with open(cls.pidfile, encoding="utf-8") as fh:
                pid = int(fh.read().strip() or 0)
        except (OSError, ValueError):
            return  # sbe: allow-silent no daemon started, nothing to stop
        if pid:
            try:
                os.kill(pid, signal.SIGKILL)
            except ProcessLookupError:
                pass  # sbe: allow-silent the daemon already exited

    def test_the_fixture_really_had_a_daemon_outside_the_group(self):
        self.assertTrue(os.path.exists(self.pidfile),
                        "the daemon never left the group, so nothing below "
                        "was tested")


@unittest.skipUnless(os.name == "posix", "POSIX sessions")
class ADaemonHoldingThePipesCannotHangTheCancel(_SetsidDaemonRun,
                                                unittest.TestCase):
    """If the daemon kept the pipes open, the post-kill read waited for EOF
    until it exited: measured 8.1s for a 1s timeout with an 8s daemon, and
    never for a long-lived one. That is capability Area 5's failure, the
    session waiting on a dead worker. Each test reads one property, so each
    guard in the cancel path has a test only it can turn red."""

    TAIL = "print('partial-marker', flush=True)\ntime.sleep(60)\n"

    def test_the_cancel_returns_within_timeout_plus_grace(self):
        self.assertEqual(self.got["status"], "unavailable")
        self.assertIn("failure_class=timeout", self.got["note"])
        self.assertLess(self.elapsed, self.TIMEOUT + self.GRACE + 1.5,
                        "the post-kill read waited on the daemon's pipe")

    def test_the_killed_worker_is_reaped_not_left_a_zombie(self):
        self.assertIsNotNone(self.proc)
        self.assertIsNotNone(self.proc.returncode,
                             "the killed worker was never waited on")

    def test_the_pipes_are_closed_by_the_spawner(self):
        self.assertIsNotNone(self.proc)
        self.assertTrue(self.proc.stdout.closed, "stdout pipe left open")
        self.assertTrue(self.proc.stderr.closed, "stderr pipe left open")

    def test_what_arrived_before_the_kill_is_kept_as_text(self):
        self.assertIsNotNone(self.raised)
        self.assertIsInstance(self.raised.output, str)
        self.assertIn("partial-marker", self.raised.output)


@unittest.skipUnless(os.name == "posix", "POSIX sessions")
class AWorkerThatExitedIsNotReportedAsNeverStarted(_SetsidDaemonRun,
                                                   unittest.TestCase):
    """Same daemon, but the worker itself exits before the deadline. Darwin
    answers os.killpg on a group whose only member is the exited, unreaped
    worker with EPERM, not ESRCH (measured on 3.9.6 and 3.13.14), and that
    PermissionError escaped as "could not start": a worker that ran, recorded
    as one that never did, which is the broken PATH verdict."""

    TAIL = "sys.exit(3)\n"

    def test_it_is_not_read_as_a_worker_that_never_started(self):
        self.assertEqual(self.got["status"], "unavailable")
        self.assertNotIn("could not start", self.got["note"])
        self.assertLess(self.elapsed, self.TIMEOUT + self.GRACE + 1.5)


@unittest.skipUnless(os.name == "posix", "POSIX process groups")
class ALiveWorkerThatRefusesTheKillIsNotWaitedOn(unittest.TestCase):
    """EPERM from os.killpg is benign only when the worker is already dead.
    A live worker that refuses the signal (a setuid program, which a test
    cannot start unprivileged, hence the stand-in) is not stopped by anything
    here, so waiting on it is waiting for it to finish on its own."""

    def test_eperm_on_a_live_worker_does_not_block_the_cancel(self):
        import time
        from unittest import mock
        made = []

        class Recording(subprocess.Popen):
            def __init__(self, *a, **kw):
                super(Recording, self).__init__(*a, **kw)
                made.append(self)

        def refuse(pgid, sig):
            raise PermissionError(1, "Operation not permitted")

        try:
            with mock.patch.object(S.os, "killpg", refuse), \
                    mock.patch.object(S.subprocess, "Popen", Recording), \
                    mock.patch.object(S, "DRAIN_GRACE_SECONDS", 0.5):
                started = time.monotonic()
                got = S.SpawningWorker([sys.executable, "-c",
                                        "import time; time.sleep(30)"],
                                       timeout=1).run({})
                elapsed = time.monotonic() - started
        finally:
            for proc in made:
                proc.kill()
                proc.wait()
                for pipe in (proc.stdin, proc.stdout, proc.stderr):
                    if pipe:
                        pipe.close()
        self.assertEqual(got["status"], "unavailable")
        self.assertLess(elapsed, 1 + 0.5 + 1.5,
                        "the cancel waited on a worker it could not stop")


if __name__ == "__main__":
    unittest.main()

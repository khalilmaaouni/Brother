#!/usr/bin/env python3
"""FC1.b (docs/plan/specs/FC1.md): a rehearsal holds the machine awake from
its lock to its record, by itself.

Measured: the 1.1.1 rehearsal stayed awake only because the session's
wrapper ran it under caffeinate -ims, and a 1.1.0 rehearsal lost about 80
minutes to sleep without one.

HOW. run_rehearsal runs in a throwaway git repository with a scripted runner
(git for real, the worktree add and remove only recorded, the child cut.py
answered) and an injected hold that records when it starts and stops. The
real caffeinate is asked once, on macOS only.

Python 3.9, standard library only. No network. No em or en dashes.
"""
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import cut as C  # noqa: E402
import cut_rehearsal as CR  # noqa: E402

V = "9.9.9"


class FakeProc(object):
    def __init__(self, exits_at_start=None, events=None):
        self.code = exits_at_start
        self.events = events if events is not None else []
        self.terminated = self.killed = False

    def wait(self, timeout=None):
        if self.code is None:
            raise subprocess.TimeoutExpired("caffeinate", timeout)
        return self.code

    def terminate(self):
        self.terminated = True
        self.code = 0
        self.events.append("released")

    def kill(self):
        self.killed = True

    def poll(self):
        return self.code


class FakeAwake(object):
    def __init__(self, why="", events=None):
        self.why = why
        self.events = events if events is not None else []
        self.pids, self.proc = [], None

    def __call__(self, pid):
        self.pids.append(pid)
        self.events.append("hold")
        if self.why:
            return None, self.why
        self.proc = FakeProc(events=self.events)
        return self.proc, ""


class Rehearsal(unittest.TestCase):

    def setUp(self):
        base = tempfile.mkdtemp(prefix="test-awake-")
        self.addCleanup(shutil.rmtree, base, True)
        self.repo, self.ev, self.bs = (os.path.join(base, n) for n in ("repo", "evidence", "scratch"))
        home = os.path.join(base, "home")
        for d in (self.repo, home, self.bs):
            os.makedirs(d)
        env = dict((k, v) for k, v in os.environ.items()
                   if not k.startswith(("GIT_", "BROTHER_")) and k != "XDG_CONFIG_HOME")
        env.update(HOME=home, GIT_CONFIG_NOSYSTEM="1", GIT_AUTHOR_NAME="t", GIT_AUTHOR_EMAIL="t@example.test",
                   GIT_COMMITTER_NAME="t", GIT_COMMITTER_EMAIL="t@example.test", BROTHER_SCRATCH=self.bs,
                   BROTHER_CUT_TEST="1", BROTHER_CUT_EVIDENCE_DIR=self.ev)
        patcher = mock.patch.dict(os.environ, env, clear=True)
        patcher.start()
        self.addCleanup(patcher.stop)
        files = {"scripts/cut.py": "cut\n"}
        for rel in CR.SCRIPT_DIRS:
            files[rel + "/m.py"] = "m\n"
        for rel, text in files.items():
            path = os.path.join(self.repo, rel)
            os.makedirs(os.path.dirname(path), exist_ok=True)
            with open(path, "w", encoding="utf-8") as fh:
                fh.write(text)
        for args in (["init", "-q"], ["add", "-A"], ["commit", "-q", "-m", "base"]):
            self.assertEqual(C._run(["git"] + args, self.repo).returncode, 0)
        self.events = []
        self.child_raises = False

    def runner(self, cmd, **kw):
        cmd = list(cmd)
        if cmd[0] != "git":
            if self.child_raises:
                raise RuntimeError("the child blew up")
            return subprocess.CompletedProcess(cmd, 0, "READY for %s\n" % V, "")
        if cmd[1:3] == ["worktree", "add"]:
            self.events.append("worktree add")
            os.makedirs(cmd[-2])
            return subprocess.CompletedProcess(cmd, 0, "", "")
        if cmd[1:3] == ["worktree", "remove"]:
            shutil.rmtree(cmd[-1], ignore_errors=True)
            return subprocess.CompletedProcess(cmd, 0, "", "")
        return C._run(cmd, kw.get("cwd"), timeout=kw.get("timeout"))

    def rehearse(self, awake):
        real = CR.record_rehearsal

        def record(*a, **k):
            path = real(*a, **k)
            self.events.append("record")
            return path
        with mock.patch.object(CR, "record_rehearsal", record):
            return CR.run_rehearsal(self.repo, V, runner=self.runner, awake=awake)

    def test_the_hold_starts_before_the_worktree_and_ends_after_the_record(self):
        awake = FakeAwake(events=self.events)
        verdict, _code, _path = self.rehearse(awake)
        self.assertEqual(verdict, "READY")
        self.assertEqual(awake.pids, [os.getpid()])
        self.assertEqual(self.events, ["hold", "worktree add", "record", "released"])

    def test_a_hold_that_cannot_start_refuses_before_any_worktree(self):
        awake = FakeAwake("caffeinate is missing", events=self.events)
        with self.assertRaises(CR.RehearsalRefused) as ctx:
            self.rehearse(awake)
        self.assertIn("caffeinate is missing", str(ctx.exception))
        self.assertNotIn("worktree add", self.events)
        self.assertFalse(os.path.exists(os.path.join(self.bs, "cut-rehearsal.lock")))
        self.assertFalse(os.path.isdir(self.ev) and os.listdir(self.ev))

    def test_the_hold_is_released_when_the_run_raises(self):
        self.child_raises = True
        awake = FakeAwake(events=self.events)
        with self.assertRaises(RuntimeError):
            self.rehearse(awake)
        self.assertTrue(awake.proc.terminated)


class HoldAwake(unittest.TestCase):

    def test_a_missing_a_refused_or_a_dying_caffeinate_is_no_hold(self):
        whys = []
        for kw in ({"exists": lambda p: False},
                   {"exists": lambda p: True, "popen": mock.Mock(side_effect=OSError("not executable"))},
                   {"exists": lambda p: True, "popen": lambda *a, **k: FakeProc(0)}):
            handle, why = CR.hold_awake(1234, **kw)
            self.assertIsNone(handle)
            self.assertTrue(why)
            whys.append(why)
        self.assertEqual(len(set(whys)), 3, whys)

    def test_an_alive_caffeinate_is_the_hold_tied_to_the_pid(self):
        seen = []

        def popen(argv, **kw):
            seen.append((argv, kw))
            return FakeProc()
        handle, why = CR.hold_awake(4321, popen=popen, exists=lambda p: True)
        self.assertEqual(why, "")
        self.assertIsInstance(handle, FakeProc)
        self.assertEqual(seen[0][0], CR.awake_command(4321))
        self.assertEqual(seen[0][0][-2:], ["-w", "4321"])
        self.assertEqual(seen[0][1].get("stdin"), subprocess.DEVNULL)

    def test_the_real_run_holds_and_a_scripted_one_never_does(self):
        self.assertIs(CR._awake_for(None, None), CR.hold_awake)
        self.assertIs(CR._awake_for(lambda *a, **k: None, None), CR._no_hold)
        f = lambda pid: (None, "")  # noqa: E731
        self.assertIs(CR._awake_for(lambda *a, **k: None, f), f)

    def test_a_pid_that_is_not_a_positive_int_is_refused(self):
        for bad in (0, -1, True, "12", 1.5, None):
            with self.subTest(pid=bad):
                with self.assertRaises(ValueError):
                    CR.awake_command(bad)

    def test_release_never_raises(self):
        CR.release_awake(None)
        proc = FakeProc()
        CR.release_awake(proc)
        self.assertTrue(proc.terminated)

    def test_the_real_caffeinate_holds_and_releases(self):
        if sys.platform != "darwin" or not os.path.exists(CR.CAFFEINATE):
            self.skipTest("NO-DATA: no %s on this host, the real hold cannot be asked here" % CR.CAFFEINATE)
        handle, why = CR.hold_awake(os.getpid())
        if handle is None:
            self.skipTest("NO-DATA: caffeinate could not start here (%s)" % why)
        try:
            ps = subprocess.run(["ps", "-o", "command=", "-p", str(handle.pid)], capture_output=True, text=True,
                                timeout=10)
        except OSError as exc:
            CR.release_awake(handle)
            self.skipTest("NO-DATA: no process listing here (%s)" % exc)
        CR.release_awake(handle)
        self.assertIn("-w %d" % os.getpid(), ps.stdout)
        self.assertIsNotNone(handle.poll())


if __name__ == "__main__":
    unittest.main()

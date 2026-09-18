#!/usr/bin/env python3
"""Tests for scripts/process_containment.py.

Runnable directly: python3 scripts/test_process_containment.py -v

SAFETY: every process these tests kill or check is one they spawned
themselves (a `sleep` or `python3 -c ...` child under a fresh session). No
test signals a pre-existing pid, and none uses pkill or killall by name.

THE DECIDING TEST is test_cancel_kills_detached_setsid_grandchild: it is the
one a naive "killpg the process group" implementation, or one that only
checks whether the ROOT process exited, would still pass while a fully
detached descendant keeps running. test_mutation_dropping_ppid_walk_is_red
proves that test can fail: with the parent/child (ppid) half of discovery
removed, the same scenario leaves the detached grandchild alive and the
test goes red, which is why that half of process_containment.py exists.
"""
import os
import shlex
import signal
import subprocess
import sys
import tempfile
import time
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import process_containment as pc


def _read_text(path):
    with open(path, encoding="utf-8") as fh:
        return fh.read()


def _wait_for(predicate, timeout=5.0, interval=0.02):
    deadline = time.monotonic() + timeout
    ok = predicate()
    while not ok and time.monotonic() < deadline:
        time.sleep(interval)
        ok = predicate()
    return ok


class DescendantWalkTests(unittest.TestCase):
    """Pure functions, no real processes: the graph logic on its own."""

    def test_descendants_follows_multiple_generations(self):
        rows = [(1, 0, 1), (2, 1, 1), (3, 2, 1), (4, 3, 1), (99, 50, 1)]
        found = pc._descendants(1, rows)
        self.assertEqual(found, {1, 2, 3, 4})
        self.assertNotIn(99, found)  # unrelated process (ppid 50), not a descendant of 1

    def test_descendants_root_not_in_table_is_just_root(self):
        rows = [(5, 1, 1)]
        self.assertEqual(pc._descendants(42, rows), {42})

    def test_pgid_members_matches_only_that_group(self):
        rows = [(1, 0, 1), (2, 1, 1), (3, 2, 99)]
        self.assertEqual(pc._pgid_members(1, rows), {1, 2})
        self.assertEqual(pc._pgid_members(99, rows), {3})


class ReadProcessTableTests(unittest.TestCase):

    def test_real_table_contains_this_process(self):
        rows = pc._read_process_table()
        pids = {pid for pid, _ppid, _pgid in rows}
        self.assertIn(os.getpid(), pids)

    def test_ps_failure_raises_enumeration_error(self):
        def fake_run(*a, **k):
            raise FileNotFoundError("no such command")
        real_run = subprocess.run
        subprocess.run = fake_run
        try:
            with self.assertRaises(pc.EnumerationError):
                pc._read_process_table()
        finally:
            subprocess.run = real_run


class GuardTests(unittest.TestCase):

    def test_refuses_non_positive_pid(self):
        result = pc.cancel_process_tree(0)
        self.assertFalse(result.verified)
        self.assertFalse(result.all_dead)
        self.assertIsNotNone(result.error)

    def test_refuses_its_own_pid(self):
        result = pc.cancel_process_tree(os.getpid())
        self.assertFalse(result.verified)
        self.assertIn("own pid", result.error.lower())

    def test_refuses_own_process_group(self):
        result = pc.cancel_process_tree(os.getpid() + 1, root_pgid=os.getpgid(0))
        self.assertFalse(result.verified)
        self.assertIn("own process group", result.error.lower())

    def test_enumeration_failure_is_never_reported_as_contained(self):
        """Rule 1 of the worker contract: NO-DATA is never a pass. If the
        process table cannot be read, verified must be False even though a
        best-effort kill still ran, and all_dead must not be claimed True."""
        proc = subprocess.Popen(["sleep", "5"], start_new_session=True)
        try:
            def fake_run(*a, **k):
                raise FileNotFoundError("simulated ps outage")
            real_run = subprocess.run
            subprocess.run = fake_run
            try:
                result = pc.cancel_process_tree(proc.pid, grace=0.3, kill_grace=0.2)
            finally:
                subprocess.run = real_run
            self.assertFalse(result.verified)
            self.assertFalse(result.all_dead)
            self.assertIn("unreadable", result.error)
        finally:
            proc.wait(timeout=5)


class CancelSimpleTreeTests(unittest.TestCase):
    """A plain two-generation tree with no detachment: the case every
    killpg-based approach already handles, kept as a baseline."""

    def test_cancel_kills_parent_and_ordinary_child(self):
        with tempfile.TemporaryDirectory() as tmp:
            pidfile = os.path.join(tmp, "child.pid")
            shell = "sleep 20 & echo $! > %s; wait" % shlex.quote(pidfile)
            root = pc.spawn_contained(["/bin/sh", "-c", shell])
            try:
                self.assertTrue(_wait_for(lambda: os.path.exists(pidfile) and
                                           _read_text(pidfile).strip()))
                child_pid = int(_read_text(pidfile).strip())
                self.assertTrue(pc._pid_alive(child_pid))

                result = pc.cancel_process_tree(root.pid, grace=1.0, kill_grace=0.5)

                self.assertTrue(result.verified)
                self.assertTrue(result.all_dead, result)
                self.assertFalse(pc._pid_alive(child_pid))
                self.assertFalse(pc._pid_alive(root.pid))
            finally:
                if root.poll() is None:
                    root.kill()
                root.wait(timeout=5)


class CancelDetachedGrandchildTests(unittest.TestCase):
    """The deciding property: a detached (setsid) grandchild dies too."""

    def _spawn_tree_with_detached_grandchild(self, sleep_for=30):
        """Spawns root (its own session/pgid) -> shell background job ->
        python grandchild that calls os.setsid() and then, only AFTER that
        call returns, touches a flag file. Waiting for the flag file (not
        just for the pid to exist) closes the startup race: without it, a
        caller could snapshot the process table while the grandchild has
        been forked but has not executed os.setsid() yet, in which case it
        is still, briefly and by accident, a member of the original process
        group, and a pgid-only mutation would still find it."""
        tmp = tempfile.mkdtemp(prefix="pc-test-")
        pidfile = os.path.join(tmp, "grandchild.pid")
        flagfile = os.path.join(tmp, "detached.flag")
        detach_code = ("import os, time; os.setsid(); open(%r, 'w').close(); "
                        "time.sleep(%d)" % (flagfile, sleep_for))
        shell = "%s -c %s & echo $! > %s; sleep %d" % (
            shlex.quote(sys.executable), shlex.quote(detach_code),
            shlex.quote(pidfile), sleep_for)
        root = pc.spawn_contained(["/bin/sh", "-c", shell])
        self.assertTrue(_wait_for(lambda: os.path.exists(pidfile) and
                                   _read_text(pidfile).strip()))
        grandchild_pid = int(_read_text(pidfile).strip())
        self.assertTrue(_wait_for(lambda: pc._pid_alive(grandchild_pid)))
        self.assertTrue(_wait_for(lambda: os.path.exists(flagfile)),
                         "grandchild never signalled that os.setsid() completed")
        return root, grandchild_pid

    def test_cancel_kills_detached_setsid_grandchild(self):
        root, grandchild_pid = self._spawn_tree_with_detached_grandchild()
        try:
            result = pc.cancel_process_tree(root.pid, grace=1.0, kill_grace=0.5)

            self.assertTrue(result.verified, result)
            self.assertIn(grandchild_pid, result.signalled)
            self.assertTrue(_wait_for(lambda: not pc._pid_alive(grandchild_pid)),
                             "detached grandchild %s survived cancellation" % grandchild_pid)
            self.assertTrue(result.all_dead, result)
        finally:
            if root.poll() is None:
                root.kill()
            root.wait(timeout=5)
            if pc._pid_alive(grandchild_pid):
                try:
                    os.kill(grandchild_pid, signal.SIGKILL)
                except ProcessLookupError:
                    pass

    def test_mutation_dropping_ppid_walk_is_red(self):
        """Names the bad state a weaker check would also pass: containment
        that only unions process-group membership (the classic killpg
        approach) and skips the parent/child walk. A setsid() call gives the
        grandchild a NEW pgid equal to its own pid, so pgid-only discovery
        never finds it, and this test proves that by mutating the real
        function in place, in-process, for the duration of one call."""
        root, grandchild_pid = self._spawn_tree_with_detached_grandchild()
        real_descendants = pc._descendants
        try:
            pc._descendants = lambda root_pid, rows: {root_pid}  # drop the ppid walk
            result = pc.cancel_process_tree(root.pid, grace=1.0, kill_grace=0.5)
            # Give the (now undiscovered) grandchild every chance to have
            # been killed anyway before concluding the mutation is caught.
            time.sleep(0.5)
            self.assertNotIn(grandchild_pid, result.signalled,
                              "mutation should have stopped the grandchild from being found")
            self.assertTrue(pc._pid_alive(grandchild_pid),
                             "expected the mutated (pgid-only) containment to miss the "
                             "detached grandchild, but it is dead: the mutation was not caught")
        finally:
            pc._descendants = real_descendants
            if root.poll() is None:
                root.kill()
            root.wait(timeout=5)
            try:
                os.kill(grandchild_pid, signal.SIGKILL)
            except ProcessLookupError:
                pass


if __name__ == "__main__":
    unittest.main()

"""What scripts/fault_barrier.py must keep true: this is the one blocking
synchronization point shared by claim_store.py, integrate.py and
loop_bridge.py, the execution spine itself (imported at claim_store.py:78,
integrate.py:78, loop_bridge.py:72). The property under test is not "it can
be created and released": it is (a) it stays a strict no-op unless every one
of its three activation gates holds, so a stray environment variable in a
real run can never make it block a real process, and (b) once it does
activate, a caller that never gets released is bounded: it raises
TimeoutError rather than hanging forever OR silently returning as though the
release had happened (the exact historical shape this module's own
docstring says it replaced).

Note on scope: scripts/test_fault_lab.py already carries a dedicated test
for the O_NOFOLLOW symlink defense on the STARTED marker (FINDING 9), so
that exact scenario is not repeated here. This file covers the gates, the
successful release path, the timeout/holder-dies path under real
concurrency, a stale marker being overwritten rather than trusted, and one
more malformed-path shape (a directory sitting where a marker file should
be).
"""
import contextlib
import io
import os
import shutil
import sys
import tempfile
import threading
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import fault_barrier as FB  # noqa: E402

try:
    import tmp_sandbox as _e100_tmp
    _e100_tmp.install()
except ImportError:
    sys.stderr.write(
        "tmp_sandbox absent: %s leaves its temp trees behind\n"
        % os.path.basename(__file__))


def _new_dir():
    d = tempfile.mkdtemp(prefix="fault-barrier-test-")
    return d


def _wait_for_file(path, timeout=5.0):
    """Poll for `path` to appear. This is the explicit signal the test
    controls: fault_barrier.wait() itself creates STARTED as the first thing
    it does once it is actually blocking, so watching for that file (rather
    than sleeping a guessed duration) is watching the real state change."""
    import time
    deadline = time.time() + timeout
    while time.time() < deadline:
        if os.path.exists(path):
            return True
        time.sleep(0.01)
    return False


class GateOneExactNameMatch(unittest.TestCase):
    """Gate 1: BROTHER_FAULT_BARRIER must equal `name` exactly. Everything
    else in the environment can be perfectly primed and it still must not
    fire, because a name mismatch is the ordinary case (this boundary is not
    the one being awaited right now)."""

    def test_mismatched_name_is_a_no_op_even_with_everything_else_primed(self):
        d = _new_dir()
        self.addCleanup(shutil.rmtree, d, ignore_errors=True)
        started = os.path.join(d, "started")
        release = os.path.join(d, "release")
        env = {
            "BROTHER_FAULT_BARRIER": "some-other-boundary",
            "BROTHER_FAULT_LAB": "1",
            "BROTHER_FAULT_BARRIER_STARTED": started,
            "BROTHER_FAULT_BARRIER_RELEASE": release,
        }
        FB.wait("the-boundary-under-test", env=env)
        self.assertFalse(os.path.exists(started),
                          "a name mismatch must never touch the filesystem")

    def test_empty_env_is_a_no_op(self):
        # The "empty input" edge: nothing set at all.
        FB.wait("anything", env={})


class GateTwoFaultLabMarker(unittest.TestCase):
    """Gate 2: BROTHER_FAULT_LAB=1 must be present. Name-matched but
    unset (or set to something other than "1") must still be a no-op."""

    def test_name_matches_but_fault_lab_unset_is_a_no_op(self):
        d = _new_dir()
        self.addCleanup(shutil.rmtree, d, ignore_errors=True)
        started = os.path.join(d, "started")
        release = os.path.join(d, "release")
        env = {
            "BROTHER_FAULT_BARRIER": "x",
            "BROTHER_FAULT_BARRIER_STARTED": started,
            "BROTHER_FAULT_BARRIER_RELEASE": release,
        }
        FB.wait("x", env=env)
        self.assertFalse(os.path.exists(started))

    def test_fault_lab_set_to_something_other_than_one_is_a_no_op(self):
        d = _new_dir()
        self.addCleanup(shutil.rmtree, d, ignore_errors=True)
        started = os.path.join(d, "started")
        release = os.path.join(d, "release")
        env = {
            "BROTHER_FAULT_BARRIER": "x",
            "BROTHER_FAULT_LAB": "true",  # not the literal "1"
            "BROTHER_FAULT_BARRIER_STARTED": started,
            "BROTHER_FAULT_BARRIER_RELEASE": release,
        }
        FB.wait("x", env=env)
        self.assertFalse(os.path.exists(started))


class GateThreeMarkersRequiredAndContained(unittest.TestCase):
    """Gate 3: both markers must be given, and both must resolve under the
    system temp directory. Refused WITHOUT WRITING, never by raising."""

    def test_missing_started_is_a_no_op(self):
        d = _new_dir()
        self.addCleanup(shutil.rmtree, d, ignore_errors=True)
        env = {
            "BROTHER_FAULT_BARRIER": "x",
            "BROTHER_FAULT_LAB": "1",
            "BROTHER_FAULT_BARRIER_RELEASE": os.path.join(d, "release"),
        }
        FB.wait("x", env=env)  # must return, not raise KeyError/TypeError

    def test_missing_release_is_a_no_op(self):
        d = _new_dir()
        self.addCleanup(shutil.rmtree, d, ignore_errors=True)
        started = os.path.join(d, "started")
        env = {
            "BROTHER_FAULT_BARRIER": "x",
            "BROTHER_FAULT_LAB": "1",
            "BROTHER_FAULT_BARRIER_STARTED": started,
        }
        FB.wait("x", env=env)
        self.assertFalse(os.path.exists(started))

    def test_marker_outside_system_temp_is_refused_without_writing(self):
        # A path a hostile or careless caller pointed at the repository
        # itself, well outside tempfile.gettempdir().
        outside_dir = _new_dir()  # still under temp; we need OUTSIDE temp
        self.addCleanup(shutil.rmtree, outside_dir, ignore_errors=True)
        outside = os.path.join(HERE, "..", "_fault_barrier_test_escape")
        outside = os.path.abspath(outside)
        release = os.path.join(outside_dir, "release")
        env = {
            "BROTHER_FAULT_BARRIER": "x",
            "BROTHER_FAULT_LAB": "1",
            "BROTHER_FAULT_BARRIER_STARTED": outside,
            "BROTHER_FAULT_BARRIER_RELEASE": release,
        }
        buf = io.StringIO()
        with contextlib.redirect_stderr(buf):
            FB.wait("x", env=env)
        self.assertFalse(os.path.exists(outside),
                          "a marker path outside system temp must never be "
                          "created")
        self.assertIn("refusing barrier", buf.getvalue())

    def test_a_directory_where_the_marker_belongs_is_refused_not_raised(self):
        # "corrupt input" shape: STARTED names an existing directory, not a
        # file. os.open(..., O_CREAT|O_WRONLY) on a directory raises
        # IsADirectoryError (an OSError); wait() must absorb that, not crash
        # the caller.
        d = _new_dir()
        self.addCleanup(shutil.rmtree, d, ignore_errors=True)
        started_dir = os.path.join(d, "started-is-a-dir")
        os.mkdir(started_dir)
        release = os.path.join(d, "release")
        env = {
            "BROTHER_FAULT_BARRIER": "x",
            "BROTHER_FAULT_LAB": "1",
            "BROTHER_FAULT_BARRIER_STARTED": started_dir,
            "BROTHER_FAULT_BARRIER_RELEASE": release,
        }
        buf = io.StringIO()
        with contextlib.redirect_stderr(buf):
            FB.wait("x", env=env)  # must not raise
        self.assertIn("could not open", buf.getvalue())


class AlreadyReleasedAndStaleMarkers(unittest.TestCase):
    """"already done" and "stale" states for a barrier: RELEASE already
    present before wait() is even called, and a leftover STARTED marker
    from a previous, unrelated attempt."""

    def test_release_already_present_returns_immediately(self):
        d = _new_dir()
        self.addCleanup(shutil.rmtree, d, ignore_errors=True)
        started = os.path.join(d, "started")
        release = os.path.join(d, "release")
        with open(release, "w", encoding="utf-8") as fh:
            fh.write("")
        env = {
            "BROTHER_FAULT_BARRIER": "x",
            "BROTHER_FAULT_LAB": "1",
            "BROTHER_FAULT_BARRIER_STARTED": started,
            "BROTHER_FAULT_BARRIER_RELEASE": release,
        }
        FB.wait("x", env=env)
        with open(started, encoding="utf-8") as fh:
            self.assertEqual(fh.read(), str(os.getpid()))

    def test_a_stale_started_marker_is_overwritten_not_trusted(self):
        d = _new_dir()
        self.addCleanup(shutil.rmtree, d, ignore_errors=True)
        started = os.path.join(d, "started")
        release = os.path.join(d, "release")
        with open(started, "w", encoding="utf-8") as fh:
            fh.write("99999999")  # a pid from a long-gone previous attempt
        with open(release, "w", encoding="utf-8") as fh:
            fh.write("")
        env = {
            "BROTHER_FAULT_BARRIER": "x",
            "BROTHER_FAULT_LAB": "1",
            "BROTHER_FAULT_BARRIER_STARTED": started,
            "BROTHER_FAULT_BARRIER_RELEASE": release,
        }
        FB.wait("x", env=env)
        with open(started, encoding="utf-8") as fh:
            self.assertEqual(fh.read(), str(os.getpid()),
                              "a stale marker must be truncated and "
                              "replaced with this call's own pid, never "
                              "trusted as already-current")

    def test_calling_twice_in_a_row_from_the_same_process_both_succeed(self):
        # "the actor is the same as last time": nothing here special-cases
        # caller identity beyond logging the pid, so two sequential calls
        # from this one process against two fresh marker pairs must both
        # behave the same way.
        for _ in range(2):
            d = _new_dir()
            self.addCleanup(shutil.rmtree, d, ignore_errors=True)
            started = os.path.join(d, "started")
            release = os.path.join(d, "release")
            with open(release, "w", encoding="utf-8") as fh:
                fh.write("")
            env = {
                "BROTHER_FAULT_BARRIER": "x",
                "BROTHER_FAULT_LAB": "1",
                "BROTHER_FAULT_BARRIER_STARTED": started,
                "BROTHER_FAULT_BARRIER_RELEASE": release,
            }
            FB.wait("x", env=env)
            with open(started, encoding="utf-8") as fh:
                self.assertEqual(fh.read(), str(os.getpid()))


class TimeoutAndContention(unittest.TestCase):
    """The property that actually matters: what happens under contention,
    and what happens when whoever should release it never does. Both
    scenarios patch the module's TIMEOUT_SECONDS down to keep the suite
    fast; both restore it in tearDown."""

    def setUp(self):
        self._orig_timeout = FB.TIMEOUT_SECONDS

    def tearDown(self):
        FB.TIMEOUT_SECONDS = self._orig_timeout

    def test_a_holder_that_never_releases_raises_timeout_not_a_silent_return(self):
        FB.TIMEOUT_SECONDS = 0.2
        d = _new_dir()
        self.addCleanup(shutil.rmtree, d, ignore_errors=True)
        started = os.path.join(d, "started")
        release = os.path.join(d, "release")  # deliberately never created
        env = {
            "BROTHER_FAULT_BARRIER": "x",
            "BROTHER_FAULT_LAB": "1",
            "BROTHER_FAULT_BARRIER_STARTED": started,
            "BROTHER_FAULT_BARRIER_RELEASE": release,
        }
        with self.assertRaises(TimeoutError):
            FB.wait("x", env=env)
        self.assertTrue(os.path.exists(started),
                         "the STARTED marker must still have been written "
                         "before the timeout fired")

    def test_a_concurrent_releaser_unblocks_the_waiting_thread(self):
        # Real contention: one thread is inside wait(), genuinely blocked
        # polling for RELEASE; the main thread only creates RELEASE after
        # it has observed STARTED (the explicit signal fault_barrier.wait
        # itself produces), never after a blind sleep.
        FB.TIMEOUT_SECONDS = 5.0
        d = _new_dir()
        self.addCleanup(shutil.rmtree, d, ignore_errors=True)
        started = os.path.join(d, "started")
        release = os.path.join(d, "release")
        env = {
            "BROTHER_FAULT_BARRIER": "x",
            "BROTHER_FAULT_LAB": "1",
            "BROTHER_FAULT_BARRIER_STARTED": started,
            "BROTHER_FAULT_BARRIER_RELEASE": release,
        }
        errors = []

        def waiter():
            try:
                FB.wait("x", env=env)
            except BaseException as exc:  # noqa: BLE001
                errors.append(exc)

        t = threading.Thread(target=waiter)
        t.start()
        self.assertTrue(_wait_for_file(started, timeout=5.0),
                         "the waiter thread never reached STARTED")
        with open(release, "w", encoding="utf-8") as fh:
            fh.write("")
        t.join(timeout=5.0)
        self.assertFalse(t.is_alive(), "the waiter thread never unblocked")
        self.assertEqual(errors, [], "the waiter must not raise once "
                          "released")


if __name__ == "__main__":
    unittest.main()

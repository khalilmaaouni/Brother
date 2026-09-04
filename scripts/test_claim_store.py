"""What the claim store must keep true.

The failure it prevents is two SESSIONS reading the same ready set and both
starting the same unit, so the exclusion test spawns real processes. A threading
test would pass on a module that guards nothing across process boundaries, which
is exactly the mistake being avoided.
"""
import contextlib
import io
import json
import os
import subprocess
import sys
import tempfile
import time
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import claim_store as C  # noqa: E402


class FakeClock(object):
    def __init__(self, t=1000.0):
        self.t = t

    def __call__(self):
        return self.t

    def advance(self, n):
        self.t += n


def store():
    return os.path.join(tempfile.mkdtemp(), "claims.json")


class TwoWorkersCannotOwnOneUnit(unittest.TestCase):
    def test_a_second_owner_is_refused_while_the_lease_is_live(self):
        p = store()
        first, _ = C.acquire(p, "U1", "session-a")
        self.assertIsNotNone(first)
        second, problem = C.acquire(p, "U1", "session-b")
        self.assertIsNone(second)
        self.assertIn("claimed by session-a", problem)

    def test_the_same_owner_may_reclaim_its_own_unit(self):
        """Otherwise a retry by the rightful owner deadlocks against itself."""
        p = store()
        C.acquire(p, "U1", "session-a")
        again, problem = C.acquire(p, "U1", "session-a")
        self.assertIsNotNone(again, problem)
        self.assertEqual(again["attempt"], 2)

    def test_a_different_unit_is_unaffected(self):
        p = store()
        C.acquire(p, "U1", "session-a")
        other, problem = C.acquire(p, "U2", "session-b")
        self.assertIsNotNone(other, problem)

    def test_TWO_REAL_PROCESSES_cannot_both_win(self):
        """The test that matters. A threading lock would pass a version of this
        that guards nothing between processes, so this uses real ones.

        The winner sleeps briefly after acquiring rather than exiting at once:
        live() now also checks the owning pid (see the dead-owner test class
        below), so a winner that vanished the instant it claimed would be
        correctly, immediately reclaimable and this test would be asserting
        the very bug that check exists to fix. Staying alive through the race
        window keeps this test about EXCLUSIVE LEASE (no two live owners at
        once), not about how fast a dead one gets noticed."""
        p = store()
        code = (
            "import sys, json, time; sys.path.insert(0, %r);"
            "import claim_store as C;"
            "c, why = C.acquire(%r, 'U1', sys.argv[1]);"
            "print(json.dumps({'won': c is not None, 'why': why}));"
            "time.sleep(1)"
            % (HERE, p)
        )
        procs = [subprocess.Popen([sys.executable, "-c", code, "session-%d" % i],
                                  stdout=subprocess.PIPE, text=True)
                 for i in range(6)]
        wins = 0
        for pr in procs:
            out, _ = pr.communicate(timeout=60)
            wins += 1 if json.loads(out.strip())["won"] else 0
        self.assertEqual(wins, 1, "%d processes claimed the same unit" % wins)


class ClaimBeforeSpawnMeansItIsOnDisk(unittest.TestCase):
    def test_the_claim_is_readable_by_another_process_immediately(self):
        p = store()
        C.acquire(p, "U1", "session-a")
        with open(p, encoding="utf-8") as fh:
            self.assertEqual(json.load(fh)["U1"]["owner"], "session-a")

    def test_a_torn_store_is_NO_DATA_and_never_silently_emptied(self):
        """Emptying it would hand every held unit to the next caller."""
        p = store()
        C.acquire(p, "U1", "session-a")
        with open(p, "w", encoding="utf-8") as fh:
            fh.write("{ not json")
        claim, problem = C.acquire(p, "U2", "session-b")
        self.assertIsNone(claim)
        self.assertIn("could not be read", problem)


class EveryRunHasAnIdentity(unittest.TestCase):
    def test_a_claim_carries_work_unit_attempt_and_worker(self):
        p = store()
        c, _ = C.acquire(p, "U1", "session-a", work_id="W7")
        for key in ("work_id", "unit_id", "attempt", "worker_id"):
            self.assertIn(key, c)
        self.assertEqual(c["work_id"], "W7")

    def test_the_attempt_number_increases_rather_than_resetting(self):
        p = store()
        C.acquire(p, "U1", "session-a")
        C.release(p, "U1", "session-a", state="failed")
        again, _ = C.acquire(p, "U1", "session-a")
        self.assertEqual(again["attempt"], 2)

    def test_the_worker_id_distinguishes_two_attempts_at_one_unit(self):
        p = store()
        a, _ = C.acquire(p, "U1", "session-a")
        C.release(p, "U1", "session-a", state="failed")
        b, _ = C.acquire(p, "U1", "session-a")
        self.assertNotEqual(a["worker_id"], b["worker_id"])


class LeasesExpireSoACrashIsNotPermanent(unittest.TestCase):
    def test_an_expired_lease_may_be_taken_by_somebody_else(self):
        clock = FakeClock()
        p = store()
        C.acquire(p, "U1", "session-a", ttl=60, clock=clock)
        clock.advance(61)
        second, problem = C.acquire(p, "U1", "session-b", clock=clock)
        self.assertIsNotNone(second, problem)

    def test_reclaiming_is_never_silent(self):
        clock = FakeClock()
        p = store()
        C.acquire(p, "U1", "session-a", ttl=60, clock=clock)
        clock.advance(61)
        second, _ = C.acquire(p, "U1", "session-b", clock=clock)
        self.assertEqual(second["reclaimed_from"], "session-a")

    def test_renew_keeps_a_long_unit_from_losing_its_claim(self):
        clock = FakeClock()
        p = store()
        C.acquire(p, "U1", "session-a", ttl=60, clock=clock)
        clock.advance(50)
        C.renew(p, "U1", "session-a", ttl=60, clock=clock)
        clock.advance(30)
        second, problem = C.acquire(p, "U1", "session-b", clock=clock)
        self.assertIsNone(second, "a renewed claim was stolen")
        self.assertIn("claimed by session-a", problem)

    def test_only_the_owner_may_renew_or_release(self):
        p = store()
        C.acquire(p, "U1", "session-a")
        self.assertIsNone(C.renew(p, "U1", "session-b")[0])
        self.assertIsNone(C.release(p, "U1", "session-b")[0])

    def test_the_default_lease_matches_the_progress_deadline(self):
        """A lease outliving the stall verdict would keep a unit locked to a
        worker everything else had already given up on."""
        self.assertEqual(C.DEFAULT_TTL_SECONDS, 20 * 60)


class ReconcileReportsAndNeverActs(unittest.TestCase):
    """The crash-recovery seam. Deciding a dead session's unit may be retried is
    a judgement about whether its side effects are safe to repeat."""

    def test_a_live_claim_reads_in_flight(self):
        p = store()
        C.acquire(p, "U1", "session-a")
        found, _ = C.reconcile(p)
        self.assertEqual(found[0]["status"], "in-flight")

    def test_an_expired_claim_reads_abandoned_and_names_its_owner(self):
        clock = FakeClock()
        p = store()
        C.acquire(p, "U1", "session-a", ttl=60, clock=clock)
        clock.advance(120)
        found, _ = C.reconcile(p, clock=clock)
        self.assertEqual(found[0]["status"], "abandoned")
        self.assertEqual(found[0]["owner"], "session-a")

    def test_a_released_unit_is_not_reported_as_outstanding(self):
        p = store()
        C.acquire(p, "U1", "session-a")
        C.release(p, "U1", "session-a")
        self.assertEqual(C.reconcile(p)[0], [])

    def test_a_released_claim_is_kept_as_evidence_not_deleted(self):
        """A completed unit must stay distinguishable from one nobody took."""
        p = store()
        C.acquire(p, "U1", "session-a")
        C.release(p, "U1", "session-a", state="done")
        with open(p, encoding="utf-8") as fh:
            self.assertEqual(json.load(fh)["U1"]["state"], "done")

    def test_release_carries_the_callers_evidence_onto_the_claim(self):
        """Row E1: integrate.py's own account of what it checked (command,
        exit code, output, canonical revision) must survive onto the claim
        record, not only the bare state string, so a delivery record can read
        it back and independently verify it."""
        p = store()
        C.acquire(p, "U1", "session-a")
        ev = {"check_command": "test -f x", "exit_code": 0, "output": "",
             "output_truncated": False, "canonical_rev": "deadbeefdeadbeef"}
        C.release(p, "U1", "session-a", state="done", evidence=ev)
        with open(p, encoding="utf-8") as fh:
            self.assertEqual(json.load(fh)["U1"]["evidence"], ev)

    def test_no_evidence_given_adds_none(self):
        """A caller with nothing to give must never have this module invent
        an evidence key that looks like it proved something."""
        p = store()
        C.acquire(p, "U1", "session-a")
        C.release(p, "U1", "session-a", state="failed")
        with open(p, encoding="utf-8") as fh:
            self.assertNotIn("evidence", json.load(fh)["U1"])

    def test_reconcile_exposes_no_way_to_act(self):
        for name in ("retry", "steal", "kill", "reclaim_all"):
            self.assertFalse(hasattr(C, name), name)

    def test_an_unreadable_store_is_NO_DATA_rather_than_nothing_outstanding(self):
        p = store()
        with open(p, "w", encoding="utf-8") as fh:
            fh.write("{ torn")
        found, problem = C.reconcile(p)
        self.assertIsNone(found)
        self.assertTrue(problem)


class ADeadOwnerNeedsNoWaitAtAll(unittest.TestCase):
    """Gap 1, 2026-08-30 head-to-head: recovery waited ~1200s for a lease that
    could not possibly still be live, because the owning pid was already on
    the claim and never read back. A crash must be visible in seconds, not
    after DEFAULT_TTL_SECONDS (20 minutes)."""

    def test_pid_alive_is_false_for_a_pid_that_is_actually_gone(self):
        p = subprocess.Popen([sys.executable, "-c", "pass"])
        p.wait()  # genuinely, verifiably dead: reaped, not merely a zombie
        self.assertFalse(C.pid_alive(p.pid))

    def test_pid_alive_is_true_for_this_running_process(self):
        self.assertTrue(C.pid_alive(os.getpid()))

    def test_a_dead_owner_reads_abandoned_within_seconds_not_the_full_lease(self):
        """The regression test the recommendation asks for: a real subprocess
        PID, killed, must flip the claim to abandoned while a mocked clock
        proves the TIME axis alone still says in-flight (the lease has
        DEFAULT_TTL_SECONDS on it and the clock only advances a few seconds).
        Only the genuinely dead pid can be what turns this abandoned."""
        base = time.time()
        p = store()
        code = (
            "import sys, time; sys.path.insert(0, %r);"
            "import claim_store as C;"
            "c, why = C.acquire(%r, 'U1', 'crashed-session', "
            "ttl=C.DEFAULT_TTL_SECONDS, clock=lambda: %r);"
            "print(c['worker_id'], flush=True);"
            "time.sleep(120)"
            % (HERE, p, base)
        )
        proc = subprocess.Popen([sys.executable, "-c", code],
                                 stdout=subprocess.PIPE, text=True)
        try:
            self.assertTrue(proc.stdout.readline(), "the claim was never made")
        finally:
            proc.kill()
            proc.wait(timeout=10)
            proc.stdout.close()

        clock = FakeClock(base)
        clock.advance(5)  # nowhere near DEFAULT_TTL_SECONDS (1200s)

        # Sanity: the time axis by itself would still call this in-flight.
        with open(p, encoding="utf-8") as fh:
            claim = json.load(fh)["U1"]
        self.assertTrue(C.live({"expires_at": claim["expires_at"]}, clock()),
                         "the lease itself has not expired yet")

        found, problem = C.reconcile(p, clock=clock)
        self.assertIsNotNone(found, problem)
        self.assertEqual(found[0]["status"], "abandoned")
        self.assertEqual(found[0]["owner"], "crashed-session")

    def test_a_dead_pid_reclaim_names_the_pid_not_a_negative_expiry(self):
        """E85. reconcile() phrased EVERY abandoned claim as elapsed time since
        expiry, so a claim reclaimed because its owner pid died while time
        remained on the lease printed 'the lease expired -3600s ago': a false
        reason carrying a negative number. The detail must name the real cause
        and never print a negative elapsed-seconds figure."""
        dead = subprocess.Popen([sys.executable, "-c", "pass"])
        dead.wait()
        clock = FakeClock()
        p = store()
        C.acquire(p, "U1", "session-a", ttl=3600, clock=clock)
        with open(p, encoding="utf-8") as fh:
            data = json.load(fh)
        data["U1"]["pid"] = dead.pid
        data["U1"]["hostname"] = C._hostname()
        with open(p, "w", encoding="utf-8") as fh:
            json.dump(data, fh)

        found, problem = C.reconcile(p, clock=clock)
        self.assertIsNotNone(found, problem)
        self.assertEqual(found[0]["status"], "abandoned")
        detail = found[0]["detail"]
        self.assertNotIn("-", detail.split("while still")[0],
                         "no negative number may appear in the reason: %r" % detail)
        self.assertIn("pid %d" % dead.pid, detail)
        self.assertIn("dead on this host", detail)
        self.assertNotIn("the lease expired", detail)

    def test_a_time_expired_reclaim_still_says_the_lease_expired(self):
        """The other half of E85: the time direction keeps its own wording, so
        naming the pid case never renamed the case that was already right."""
        clock = FakeClock()
        p = store()
        C.acquire(p, "U1", "session-a", ttl=60, clock=clock)
        clock.advance(61)
        found, _ = C.reconcile(p, clock=clock)
        self.assertIn("the lease expired 1s ago", found[0]["detail"])

    def test_a_live_pid_with_an_expired_lease_still_reclaims_on_schedule(self):
        """Never weaken the time direction: an alive owner past its lease is
        still abandoned, exactly as before this fix."""
        clock = FakeClock()
        p = store()
        C.acquire(p, "U1", "session-a", ttl=60, clock=clock)  # pid = this test
        clock.advance(61)
        found, _ = C.reconcile(p, clock=clock)
        self.assertEqual(found[0]["status"], "abandoned")

    def test_acquire_records_the_hostname(self):
        p = store()
        c, _ = C.acquire(p, "U1", "session-a")
        self.assertEqual(c["hostname"], C._hostname())

    def test_a_claim_from_another_host_is_not_reclaimed_by_pid_alone(self):
        """Cross-machine guard: the owner's pid might exist on a different
        host but not on this one, so the pid check only runs when the
        claim's hostname matches this host. A claim genuinely made elsewhere
        falls back to pure time-based expiry, unchanged."""
        dead = subprocess.Popen([sys.executable, "-c", "pass"])
        dead.wait()
        claim = {"expires_at": time.time() + 999, "pid": dead.pid,
                 "hostname": "some-other-host-entirely"}
        self.assertTrue(C.live(claim, time.time()))

    def test_a_claim_from_this_host_with_a_dead_pid_is_not_live(self):
        dead = subprocess.Popen([sys.executable, "-c", "pass"])
        dead.wait()
        claim = {"expires_at": time.time() + 999, "pid": dead.pid,
                 "hostname": C._hostname()}
        self.assertFalse(C.live(claim, time.time()))


class ADeadLockHolderIsReclaimedNotWaitedOut(unittest.TestCase):
    """Gap: acquire()'s own process lock (the ".lock" file, distinct from a
    claim record) never read its pid back, so a holder that died left the
    lock file forever and every later acquire timed out. Same fix, same
    pattern as ADeadOwnerNeedsNoWaitAtAll above: reuse pid_alive and the
    hostname guard, reclaim explicitly, and never guess when the content
    cannot be trusted."""

    def _spawn_locked_then_die(self, path):
        """A real subprocess that takes C._Lock(path) and then is killed
        without ever reaching __exit__, so the lock file is left behind
        exactly like a crash would leave it."""
        code = (
            "import sys; sys.path.insert(0, %r);"
            "import claim_store as C;"
            "lock = C._Lock(%r);"
            "lock.__enter__();"
            "print('locked', flush=True);"
            "import time; time.sleep(60)"
            % (HERE, path)
        )
        proc = subprocess.Popen([sys.executable, "-c", code],
                                 stdout=subprocess.PIPE, text=True)
        self.assertTrue(proc.stdout.readline(), "the lock was never taken")
        proc.stdout.close()
        proc.kill()
        proc.wait(timeout=10)
        return proc.pid

    def test_a_lock_left_by_a_dead_process_is_reclaimed_well_before_timeout(self):
        p = store()
        dead_pid = self._spawn_locked_then_die(p)

        import io
        from contextlib import redirect_stderr
        stderr = io.StringIO()
        start = time.time()
        with redirect_stderr(stderr):
            with C._Lock(p, timeout=30.0):
                pass
        elapsed = time.time() - start

        self.assertLess(elapsed, 5.0,
                         "a dead holder's lock should be reclaimed in seconds, "
                         "not waited out over the full timeout")
        self.assertIn(str(dead_pid), stderr.getvalue())
        self.assertIn("reclaiming", stderr.getvalue())

    def test_a_lock_held_by_a_live_process_is_never_stolen(self):
        p = store()
        code = (
            "import sys, time; sys.path.insert(0, %r);"
            "import claim_store as C;"
            "lock = C._Lock(%r);"
            "lock.__enter__();"
            "print('locked', flush=True);"
            "time.sleep(5)"
            % (HERE, p)
        )
        proc = subprocess.Popen([sys.executable, "-c", code],
                                 stdout=subprocess.PIPE, text=True)
        try:
            self.assertTrue(proc.stdout.readline(), "the lock was never taken")
            with self.assertRaises(TimeoutError):
                with C._Lock(p, timeout=0.3):
                    pass
        finally:
            proc.kill()
            proc.wait(timeout=10)
            proc.stdout.close()

    def test_a_lock_file_with_garbage_content_keeps_the_timeout_behavior(self):
        """Unreadable or malformed content must never be guessed at: fall
        back to the plain wait, exactly as before this fix existed."""
        p = store()
        lock_path = p + ".lock"
        os.makedirs(os.path.dirname(lock_path), exist_ok=True)
        with open(lock_path, "w", encoding="utf-8") as fh:
            fh.write("not a pid at all")
        with self.assertRaises(TimeoutError):
            with C._Lock(p, timeout=0.3):
                pass
        self.assertTrue(os.path.exists(lock_path),
                         "a lock this code cannot trust must not be removed")

    def test_a_lock_naming_another_host_keeps_the_timeout_behavior(self):
        """A pid from another host's namespace means nothing here, so the
        hostname guard must stop this before pid_alive is even consulted."""
        dead = subprocess.Popen([sys.executable, "-c", "pass"])
        dead.wait()
        p = store()
        lock_path = p + ".lock"
        os.makedirs(os.path.dirname(lock_path), exist_ok=True)
        with open(lock_path, "w", encoding="utf-8") as fh:
            fh.write("%d:some-other-host-entirely" % dead.pid)
        with self.assertRaises(TimeoutError):
            with C._Lock(p, timeout=0.3):
                pass
        self.assertTrue(os.path.exists(lock_path),
                         "a lock from another host must not be reclaimed by pid alone")


class TheLeaseLengthObeysItsEnvironmentOverride(unittest.TestCase):
    """E62's own override, asserted directly. It appeared in tests only as a
    fixture setting inside one end-to-end run, so the fallback that keeps a
    malformed value from crashing every claim in the run was never driven."""

    def setUp(self):
        self._saved = os.environ.get(C.TTL_ENV_VAR)

    def tearDown(self):
        if self._saved is None:
            os.environ.pop(C.TTL_ENV_VAR, None)
        else:
            os.environ[C.TTL_ENV_VAR] = self._saved

    def test_a_malformed_override_falls_back_to_the_default(self):
        os.environ[C.TTL_ENV_VAR] = "abc"
        self.assertEqual(C.effective_ttl(), float(C.DEFAULT_TTL_SECONDS))

    def test_a_numeric_override_is_honoured(self):
        os.environ[C.TTL_ENV_VAR] = "4"
        self.assertEqual(C.effective_ttl(), 4.0)

    def test_an_explicit_argument_beats_the_override(self):
        """The positive control for the two above: the default is not simply
        what this function always returns."""
        os.environ[C.TTL_ENV_VAR] = "4"
        self.assertEqual(C.effective_ttl(9), 9.0)


class RenewalReportsItsFailuresInsteadOfRaising(unittest.TestCase):
    """renew_owned guards a live worker from a background thread, so a
    failure it raised instead of returned would kill that thread silently
    and leave the lease to expire under a still-running unit. The only
    coverage was one wall-clock end-to-end run that never reached the
    problems branch at all."""

    def _seeded_then_torn(self):
        p = store()
        held, problem = C.acquire(p, "U1", "session-a")
        self.assertIsNotNone(held, problem)
        with open(p, "w", encoding="utf-8") as fh:
            fh.write("{ this is not json")
        return p

    def test_an_unreadable_store_comes_back_as_a_problem(self):
        p = self._seeded_then_torn()
        renewed, problems = C.renew_owned(p, "session-a")
        self.assertEqual(renewed, [])
        self.assertEqual(len(problems), 1, problems)
        self.assertIsNone(problems[0][0])
        self.assertIn("could not be read", problems[0][1])

    def test_a_readable_store_renews_and_reports_nothing(self):
        """The positive control: the pair above is about the torn file, not
        about renew_owned reporting a problem no matter what it reads."""
        p = store()
        C.acquire(p, "U1", "session-a")
        renewed, problems = C.renew_owned(p, "session-a")
        self.assertEqual(problems, [])
        self.assertEqual(renewed, ["U1"])

    def test_the_background_loop_surfaces_the_failure_and_stops(self):
        """BackgroundRenewal itself, named in no test until now: a renewal
        against a state that already failed must be reported by stop() and
        must not be retried in a loop against the same bad state."""
        p = self._seeded_then_torn()
        renewal = C.BackgroundRenewal(p, "session-a", ttl=1.0, interval=0.01)
        err = io.StringIO()
        with contextlib.redirect_stderr(err):
            renewal.start()
            deadline = time.time() + 10.0
            while not renewal.failures and time.time() < deadline:
                time.sleep(0.01)
            failures = renewal.stop()
        self.assertTrue(failures,
                        "an unreadable store must reach the caller as a "
                        "recorded failure, not as a silent dead thread")
        self.assertIn("could not be read", failures[0][1])
        self.assertIn("claim_store: renewal failed for (store)",
                      err.getvalue())


if __name__ == "__main__":
    unittest.main()

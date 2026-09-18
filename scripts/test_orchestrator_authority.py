"""What the orchestrator authority lease must keep true.

This module exists for exactly one shape of failure: a process that stalled,
had its lease expire and taken over by someone else, then wakes up and still
believes it is the authority. The whole point of the epoch is to make every
one of that woken process's calls refused, mechanically, whatever the wall
clock or the process's own belief says. See WokenStaleOwnerCannotAct below,
the test that is the reason this unit exists.

The other failure this file is built to catch: a store file that cannot be
parsed must never read as "the scope is free". A green suite that never
wrote a truncated store file would also pass a version of this module that
silently treats corruption as absence, which is the single most dangerous
way this module could fail, so CorruptStoreIsNeverFree exists specifically
to make that bad state fail loudly.
"""
import json
import os
import subprocess
import sys
import tempfile
import threading
import time
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import orchestrator_authority as A  # noqa: E402

try:
    import tmp_sandbox
    tmp_sandbox.install()
except ImportError:
    sys.stderr.write(
        "tmp_sandbox absent: %s leaves its temp trees behind\n"
        % os.path.basename(__file__))


def store():
    return os.path.join(tempfile.mkdtemp(), "authority.json")


class AcquireAndRenew(unittest.TestCase):
    def test_first_acquisition_is_epoch_one_never_zero(self):
        """A falsy check (`if epoch:`) must never confuse "no epoch" with
        "the first epoch is held", so the very first lease is epoch 1."""
        p = store()
        lease = A.acquire(p, "R1", "S1", "primary", "primary-a", 60, now=1000.0)
        self.assertEqual(lease.epoch, 1)

    def test_renew_extends_expiry_for_the_matching_owner_and_epoch(self):
        p = store()
        lease = A.acquire(p, "R1", "S1", "primary", "primary-a", 60, now=1000.0)
        renewed = A.renew(p, "R1", "S1", "primary-a", lease.epoch, 60, now=1050.0)
        self.assertEqual(renewed.expires_at, 1110.0)

    def test_current_reports_the_live_lease(self):
        p = store()
        A.acquire(p, "R1", "S1", "primary", "primary-a", 60, now=1000.0)
        lease = A.current(p, "R1", "S1", now=1010.0)
        self.assertIsNotNone(lease)
        self.assertEqual(lease.instance, "primary-a")

    def test_current_is_none_for_a_scope_never_touched(self):
        p = store()
        self.assertIsNone(A.current(p, "R1", "no-such-scope", now=1000.0))

    def test_current_is_none_once_the_lease_expires(self):
        p = store()
        A.acquire(p, "R1", "S1", "primary", "primary-a", 60, now=1000.0)
        self.assertIsNone(A.current(p, "R1", "S1", now=1100.0))


class TwoInstancesCannotBothHoldOneScope(unittest.TestCase):
    def test_a_second_acquire_is_refused_while_the_first_is_live(self):
        p = store()
        A.acquire(p, "R1", "S1", "primary", "primary-a", 60, now=1000.0)
        with self.assertRaises(A.AuthorityRefused):
            A.acquire(p, "R1", "S1", "secondary", "secondary-a", 60, now=1010.0)

    def test_TWO_REAL_PROCESSES_race_and_exactly_one_wins(self):
        """A threading-only version of this test would pass on a module
        that guards nothing across process boundaries, so this races real
        processes contending on the same store path.

        A first version of this test just launched six processes and let
        each call acquire() as soon as it started. Measured: the six never
        actually overlapped inside the critical section (processes launch
        about 0.3ms apart and their acquire windows are staggered 1.3 to
        13.4ms), so it could not have caught a missing lock; only the
        threaded test below could. This version gives the children a real
        rendezvous first: each writes its own ready file, then spins until
        this process writes one shared start file, so every acquire() call
        fires only after every child has already reached the starting
        line."""
        p = store()
        barrier_dir = tempfile.mkdtemp()
        n = 6
        code = (
            "import sys, json, os, time; sys.path.insert(0, %r);\n"
            "import orchestrator_authority as A\n"
            "ready = os.path.join(%r, 'ready-' + sys.argv[2])\n"
            "start = os.path.join(%r, 'start')\n"
            "open(ready, 'w').close()\n"
            "deadline = time.time() + 30\n"
            "while not os.path.exists(start):\n"
            "    if time.time() > deadline:\n"
            "        print(json.dumps({'won': False, 'timeout': True})); sys.exit(0)\n"
            "won = True\n"
            "try:\n"
            "    A.acquire(%r, 'R1', 'S1', 'primary', sys.argv[1], 60, now=1000.0)\n"
            "except A.AuthorityRefused:\n"
            "    won = False\n"
            "print(json.dumps({'won': won, 'timeout': False}))\n"
            % (HERE, barrier_dir, barrier_dir, p)
        )
        procs = [subprocess.Popen(
            [sys.executable, "-c", code, "instance-%d" % i, str(i)],
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
            for i in range(n)]

        deadline = time.time() + 30
        while True:
            ready_count = sum(
                1 for i in range(n)
                if os.path.exists(os.path.join(barrier_dir, "ready-%d" % i)))
            if ready_count == n:
                break
            if time.time() > deadline:
                self.fail("not all %d children reached the rendezvous in time"
                           % n)
            time.sleep(0.01)
        open(os.path.join(barrier_dir, "start"), "w").close()

        wins = 0
        for pr in procs:
            out, err = pr.communicate(timeout=60)
            self.assertEqual(
                pr.returncode, 0,
                "child process failed (returncode %s): %s" % (pr.returncode, err))
            result = json.loads(out.strip())
            self.assertFalse(result["timeout"], "child never saw the start file")
            wins += 1 if result["won"] else 0
        self.assertEqual(wins, 1, "%d processes acquired the same live scope" % wins)


class StaleEpochIsRefused(unittest.TestCase):
    def test_renew_from_a_stale_epoch_is_refused(self):
        p = store()
        first = A.acquire(p, "R1", "S1", "primary", "primary-a", 60, now=1000.0)
        # Simulate the scope moving on: expire it, then take it over so the
        # epoch advances past what `first` believes it holds.
        A.takeover(p, "R1", "S1", "secondary", "secondary-a", 60, "expired",
                   now=1000.0 + 60.0 + 1)
        with self.assertRaises(A.AuthorityRefused):
            A.renew(p, "R1", "S1", "primary-a", first.epoch, 60,
                    now=1000.0 + 60.0 + 2)

    def test_a_stale_renew_does_not_move_the_live_expiry(self):
        """The refusal alone is not enough: the live lease's expiry must be
        completely untouched by the rejected call."""
        p = store()
        A.acquire(p, "R1", "S1", "primary", "primary-a", 60, now=1000.0)
        winner = A.takeover(p, "R1", "S1", "secondary", "secondary-a", 60, "expired",
                             now=1000.0 + 60.0 + 1)
        before = A.current(p, "R1", "S1", now=1000.0 + 60.0 + 2)
        with self.assertRaises(A.AuthorityRefused):
            A.renew(p, "R1", "S1", "primary-a", winner.epoch - 1, 999999,
                    now=1000.0 + 60.0 + 2)
        after = A.current(p, "R1", "S1", now=1000.0 + 60.0 + 2)
        self.assertEqual(before.expires_at, after.expires_at)
        self.assertEqual(after.instance, "secondary-a")


class TakeoverRules(unittest.TestCase):
    def test_a_live_lease_cannot_be_taken_over(self):
        p = store()
        A.acquire(p, "R1", "S1", "primary", "primary-a", 60, now=1000.0)
        with self.assertRaises(A.AuthorityRefused):
            A.takeover(p, "R1", "S1", "secondary", "secondary-a", 60, "impatient",
                       now=1010.0)

    def test_takeover_cannot_steal_a_live_lease_under_the_real_clock(self):
        """Behavioural, not a spelling check: the old version of this test
        asserted the string "force" was absent from takeover's signature,
        which passes on a `steal=True` argument just as easily as it
        passes on the actual loophole (an injected `now` far ahead of the
        real clock, proven below). What must actually hold, under the
        real clock every production caller uses by default, is that no
        combination of arguments lets takeover() succeed against a lease
        that is genuinely still live right now."""
        p = store()
        A.acquire(p, "R1", "S1", "primary", "primary-a", 3600)  # real clock
        with self.assertRaises(A.AuthorityRefused):
            A.takeover(p, "R1", "S1", "secondary", "secondary-a", 60, "impatient")

    def test_now_is_the_one_documented_way_to_defeat_takeovers_own_guarantee(self):
        """`now` is an injectable clock so tests can drive expiry without
        sleeping (see the module docstring); that same injectability means
        a caller who passes a `now` far in the future can make a live
        lease read as expired and take it over anyway. There genuinely is
        no `force` argument, but `now` acts as one for a caller not using
        the real clock. This is now stated in takeover's own docstring
        rather than claimed impossible; this test pins the documented
        behaviour so nobody removes the caveat without noticing the proof
        that made it necessary."""
        p = store()
        A.acquire(p, "R1", "S1", "primary", "primary-a", 60, now=1000.0)
        stolen = A.takeover(p, "R1", "S1", "secondary", "secondary-a", 60,
                             "now pushed far ahead", now=1000.0 + 10_000_000.0)
        self.assertEqual(stolen.instance, "secondary-a")

    def test_an_expired_lease_can_be_taken_over_and_the_epoch_increments(self):
        p = store()
        first = A.acquire(p, "R1", "S1", "primary", "primary-a", 60, now=1000.0)
        won = A.takeover(p, "R1", "S1", "secondary", "secondary-a", 60, "stalled",
                          now=1000.0 + 60.0 + 1)
        self.assertEqual(won.epoch, first.epoch + 1)
        self.assertEqual(won.instance, "secondary-a")


class WokenStaleOwnerCannotAct(unittest.TestCase):
    """The reason this unit exists. Instance A holds epoch 5 worth of
    history (built up over several acquire/takeover cycles so the epoch
    number is not coincidentally 1), stalls, its lease expires, B takes
    over, and A then wakes up and tries check(), renew() and release().
    Every one of A's calls must be refused, and B's lease must be
    completely untouched afterwards."""

    def test_every_call_from_the_woken_owner_is_refused(self):
        p = store()
        now = 1000.0
        # Build up to epoch 5 through real transitions, not a hand-set number.
        A.acquire(p, "R1", "S1", "primary", "seed", 60, now=now)
        for _ in range(3):
            now += 61.0
            A.takeover(p, "R1", "S1", "primary", "seed", 60, "cycling", now=now)
        now += 61.0
        a_lease = A.acquire(p, "R1", "S1", "primary", "instance-a", 60, now=now)
        self.assertEqual(a_lease.epoch, 5)

        # A stalls. Its lease expires. B takes over.
        now += 61.0
        b_lease = A.takeover(p, "R1", "S1", "secondary", "instance-b", 60,
                              "instance-a stalled past its lease", now=now)
        self.assertEqual(b_lease.epoch, 6)

        # A wakes up and, believing nothing has changed, tries to act.
        now += 1.0
        self.assertFalse(A.check(p, "R1", "S1", "instance-a", a_lease.epoch,
                                  now=now))
        with self.assertRaises(A.AuthorityRefused):
            A.renew(p, "R1", "S1", "instance-a", a_lease.epoch, 60, now=now)
        with self.assertRaises(A.AuthorityRefused):
            A.release(p, "R1", "S1", "instance-a", a_lease.epoch, now=now)

        # B's lease is completely untouched by A's three failed calls.
        still_b = A.current(p, "R1", "S1", now=now)
        self.assertEqual(still_b.instance, "instance-b")
        self.assertEqual(still_b.epoch, 6)
        self.assertEqual(still_b.expires_at, b_lease.expires_at)
        self.assertTrue(A.check(p, "R1", "S1", "instance-b", 6, now=now))


class Handoff(unittest.TestCase):
    def test_a_deliberate_handoff_closes_the_old_lease_and_increments_the_epoch(self):
        p = store()
        first = A.acquire(p, "R1", "S1", "primary", "primary-a", 60, now=1000.0)
        moved = A.handoff(p, "R1", "S1", "primary-a", first.epoch, "secondary",
                           "secondary-a", 60, "planned rotation", now=1005.0)
        self.assertEqual(moved.epoch, first.epoch + 1)
        self.assertEqual(moved.instance, "secondary-a")
        # The old owner can no longer act: its epoch is stale now.
        self.assertFalse(A.check(p, "R1", "S1", "primary-a", first.epoch,
                                  now=1005.0))

    def test_handoff_from_a_stale_epoch_is_refused(self):
        p = store()
        first = A.acquire(p, "R1", "S1", "primary", "primary-a", 60, now=1000.0)
        A.handoff(p, "R1", "S1", "primary-a", first.epoch, "secondary", "secondary-a",
                  60, "rotation one", now=1005.0)
        with self.assertRaises(A.AuthorityRefused):
            A.handoff(p, "R1", "S1", "primary-a", first.epoch, "primary",
                      "primary-b", 60, "rotation two", now=1006.0)


class ReleaseRules(unittest.TestCase):
    def test_duplicate_release_is_idempotent_not_an_error(self):
        p = store()
        lease = A.acquire(p, "R1", "S1", "primary", "primary-a", 60, now=1000.0)
        A.release(p, "R1", "S1", "primary-a", lease.epoch, now=1005.0)
        A.release(p, "R1", "S1", "primary-a", lease.epoch, now=1006.0)  # no raise

    def test_release_of_someone_elses_lease_is_refused(self):
        p = store()
        lease = A.acquire(p, "R1", "S1", "primary", "primary-a", 60, now=1000.0)
        with self.assertRaises(A.AuthorityRefused):
            A.release(p, "R1", "S1", "secondary-a", lease.epoch, now=1005.0)

    def test_a_released_scope_can_be_reacquired(self):
        p = store()
        lease = A.acquire(p, "R1", "S1", "primary", "primary-a", 60, now=1000.0)
        A.release(p, "R1", "S1", "primary-a", lease.epoch, now=1005.0)
        again = A.acquire(p, "R1", "S1", "secondary", "secondary-a", 60, now=1006.0)
        self.assertEqual(again.epoch, lease.epoch + 1)


class CrashRecoverable(unittest.TestCase):
    def test_a_crashed_holder_leaves_a_lease_reclaimable_after_ttl(self):
        """A store written with a live lease and no process behind it
        (nobody ever called release) still yields to takeover once the TTL
        has passed, and the audit trail names the former owner."""
        p = store()
        A.acquire(p, "R1", "S1", "primary", "crashed-instance", 60, now=1000.0)
        won = A.takeover(p, "R1", "S1", "secondary", "rescuer", 60,
                          "crashed-instance never renewed or released",
                          now=1000.0 + 60.0 + 1)
        self.assertEqual(won.instance, "rescuer")
        trail = A.audit(p, "R1")
        takeovers = [e for e in trail if e["type"] == "takeover"]
        self.assertEqual(takeovers[-1]["old_owner"], "crashed-instance")


class AuditTrail(unittest.TestCase):
    def test_every_transition_records_old_new_owner_reason_and_epoch(self):
        p = store()
        lease = A.acquire(p, "R1", "S1", "primary", "primary-a", 60, now=1000.0)
        A.renew(p, "R1", "S1", "primary-a", lease.epoch, 60, now=1010.0)
        A.release(p, "R1", "S1", "primary-a", lease.epoch, now=1020.0)
        won = A.acquire(p, "R1", "S1", "secondary", "secondary-a", 60, now=1030.0)
        A.takeover(p, "R1", "S1", "primary", "primary-b", 60, "test reclaim",
                   now=1030.0 + 60.0 + 1)

        trail = A.audit(p, "R1")
        kinds = [e["type"] for e in trail]
        self.assertEqual(kinds, ["acquire", "renew", "release", "acquire",
                                  "takeover"])
        for entry in trail:
            for key in ("old_owner", "new_owner", "old_epoch", "new_epoch",
                        "reason", "at"):
                self.assertIn(key, entry)
        takeover_entry = trail[-1]
        self.assertEqual(takeover_entry["old_owner"], "secondary-a")
        self.assertEqual(takeover_entry["new_owner"], "primary-b")
        self.assertEqual(takeover_entry["reason"], "test reclaim")
        self.assertEqual(takeover_entry["new_epoch"], won.epoch + 1)

    def test_audit_is_empty_for_a_run_with_no_history(self):
        p = store()
        self.assertEqual(A.audit(p, "never-touched"), [])


class CorruptStoreIsNeverFree(unittest.TestCase):
    """The bad state a green suite could still pass without this class: a
    store file that fails to parse gets read as an empty (therefore free)
    scope, and two orchestrators both acquire it. Every reading and every
    writing entry point is proven here to refuse instead of defaulting to
    free."""

    def _write_truncated(self, p):
        os.makedirs(os.path.dirname(p), exist_ok=True)
        with open(p, "w", encoding="utf-8") as fh:
            fh.write('{"R1": {"leases": {"S1": {"instance": "primary-a"')  # truncated, no closing braces

    def test_acquire_raises_rather_than_treating_corruption_as_free(self):
        p = store()
        self._write_truncated(p)
        with self.assertRaises(A.AuthorityUnreadable):
            A.acquire(p, "R1", "S1", "primary", "primary-a", 60, now=1000.0)

    def test_current_raises_rather_than_returning_none(self):
        p = store()
        self._write_truncated(p)
        with self.assertRaises(A.AuthorityUnreadable):
            A.current(p, "R1", "S1", now=1000.0)

    def test_check_raises_rather_than_returning_false(self):
        p = store()
        self._write_truncated(p)
        with self.assertRaises(A.AuthorityUnreadable):
            A.check(p, "R1", "S1", "primary-a", 1, now=1000.0)

    def test_takeover_raises_rather_than_treating_corruption_as_free(self):
        p = store()
        self._write_truncated(p)
        with self.assertRaises(A.AuthorityUnreadable):
            A.takeover(p, "R1", "S1", "secondary", "secondary-a", 60, "reason",
                       now=1000.0)

    def test_a_store_that_is_valid_json_but_not_an_object_is_also_unreadable(self):
        p = store()
        os.makedirs(os.path.dirname(p), exist_ok=True)
        with open(p, "w", encoding="utf-8") as fh:
            fh.write("[1, 2, 3]")
        with self.assertRaises(A.AuthorityUnreadable):
            A.current(p, "R1", "S1", now=1000.0)


class UnrelatedScopesAndRunsDoNotInterfere(unittest.TestCase):
    def test_a_different_scope_in_the_same_run_is_unaffected(self):
        p = store()
        A.acquire(p, "R1", "S1", "primary", "primary-a", 60, now=1000.0)
        other = A.acquire(p, "R1", "S2", "secondary", "secondary-a", 60, now=1000.0)
        self.assertEqual(other.epoch, 1)

    def test_a_different_run_with_the_same_scope_name_is_unaffected(self):
        p = store()
        A.acquire(p, "R1", "S1", "primary", "primary-a", 60, now=1000.0)
        other = A.acquire(p, "R2", "S1", "secondary", "secondary-a", 60, now=1000.0)
        self.assertEqual(other.epoch, 1)


class ThreadedContentionAlsoSerializes(unittest.TestCase):
    """Belt and braces alongside the real-process race above: several
    threads in this process hammering acquire() on the same scope must
    still yield exactly one winner, since claim_store.Lock's O_EXCL
    primitive is a kernel-level exclusion, not a language-level one."""

    def test_exactly_one_thread_wins_a_tight_race(self):
        p = store()
        results = []
        lock = threading.Lock()
        barrier = threading.Barrier(8)

        def attempt(n):
            barrier.wait()
            try:
                A.acquire(p, "R1", "S1", "primary", "instance-%d" % n, 60,
                          now=1000.0)
                won = True
            except A.AuthorityRefused:
                won = False
            with lock:
                results.append(won)

        threads = [threading.Thread(target=attempt, args=(i,)) for i in range(8)]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=30)
        self.assertEqual(sum(results), 1, results)


class RestartedUnderTheSameInstanceIdStaleEpochIsRefused(unittest.TestCase):
    """The exact scenario the review named and this repair exists for: a
    supervisor restarts a stalled orchestrator UNDER THE SAME INSTANCE ID
    (ORCH-09's night supervisor does precisely this). Every earlier
    stale-epoch test in this file also changed the instance name, so
    `record["instance"] != instance` alone carried every one of them and
    the epoch comparison was never load-bearing: deleting it from check(),
    renew(), release() and handoff() left both suites green. Here the
    instance name is held constant across the restart and only the epoch
    differs, so a version of the module missing any of the four epoch
    comparisons must fail one of these four tests."""

    def _restart_under_same_instance(self):
        """Instance "restarted" acquires epoch 1, is released (standing in
        for the crash), and the same instance name acquires again at
        epoch 2, standing in for the supervisor bringing it back up under
        its old identity. The pre-restart caller only ever learns epoch 1
        and keeps trying to act with it after the restart."""
        p = store()
        stale = A.acquire(p, "R1", "S1", "primary", "restarted", 60, now=1000.0)
        A.release(p, "R1", "S1", "restarted", stale.epoch, now=1005.0)
        current = A.acquire(p, "R1", "S1", "primary", "restarted", 60, now=1006.0)
        self.assertEqual(stale.epoch, 1)
        self.assertEqual(current.epoch, 2)
        return p, stale, current

    def test_check_refuses_the_pre_restart_epoch(self):
        p, stale, current = self._restart_under_same_instance()
        self.assertFalse(A.check(p, "R1", "S1", "restarted", stale.epoch,
                                  now=1010.0))
        self.assertTrue(A.check(p, "R1", "S1", "restarted", current.epoch,
                                 now=1010.0))

    def test_renew_refuses_the_pre_restart_epoch_and_leaves_the_new_one_untouched(self):
        p, stale, current = self._restart_under_same_instance()
        with self.assertRaises(A.AuthorityRefused):
            A.renew(p, "R1", "S1", "restarted", stale.epoch, 60, now=1010.0)
        after = A.current(p, "R1", "S1", now=1010.0)
        self.assertEqual(after.epoch, current.epoch)
        self.assertEqual(after.expires_at, current.expires_at)

    def test_release_refuses_the_pre_restart_epoch_and_the_new_lease_stays_live(self):
        p, stale, current = self._restart_under_same_instance()
        with self.assertRaises(A.AuthorityRefused):
            A.release(p, "R1", "S1", "restarted", stale.epoch, now=1010.0)
        # The headline failure this guards: without the epoch check, this
        # release would have closed the LIVE current-epoch lease, and a
        # third orchestrator could then acquire while "restarted" still
        # believes (wrongly, at the stale epoch) that it holds the scope.
        still_live = A.current(p, "R1", "S1", now=1010.0)
        self.assertIsNotNone(still_live)
        self.assertEqual(still_live.epoch, current.epoch)

    def test_handoff_refuses_the_pre_restart_epoch(self):
        p, stale, current = self._restart_under_same_instance()
        with self.assertRaises(A.AuthorityRefused):
            A.handoff(p, "R1", "S1", "restarted", stale.epoch, "secondary",
                      "secondary-a", 60, "stale handoff attempt", now=1010.0)
        still_live = A.current(p, "R1", "S1", now=1010.0)
        self.assertEqual(still_live.instance, "restarted")
        self.assertEqual(still_live.epoch, current.epoch)


class TtlMustBePositive(unittest.TestCase):
    """ttl=0 makes expires_at == now, already not live by _is_live's strict
    `>`, so a second acquire at the same instant also succeeds and both
    callers hold a Lease that reads like success; negative ttl is worse.
    Checked in the three functions that mint a fresh lease record."""

    def test_acquire_rejects_zero_ttl(self):
        p = store()
        with self.assertRaises(ValueError):
            A.acquire(p, "R1", "S1", "primary", "primary-a", 0, now=1000.0)

    def test_acquire_rejects_negative_ttl(self):
        p = store()
        with self.assertRaises(ValueError):
            A.acquire(p, "R1", "S1", "primary", "primary-a", -5, now=1000.0)

    def test_takeover_rejects_zero_ttl(self):
        p = store()
        A.acquire(p, "R1", "S1", "primary", "primary-a", 60, now=1000.0)
        with self.assertRaises(ValueError):
            A.takeover(p, "R1", "S1", "secondary", "secondary-a", 0, "reason",
                       now=1000.0 + 60.0 + 1)

    def test_handoff_rejects_zero_ttl(self):
        p = store()
        lease = A.acquire(p, "R1", "S1", "primary", "primary-a", 60, now=1000.0)
        with self.assertRaises(ValueError):
            A.handoff(p, "R1", "S1", "primary-a", lease.epoch, "secondary",
                      "secondary-a", 0, "reason", now=1005.0)


class RenewAndHandoffRefuseANonLiveRecord(unittest.TestCase):
    """renew() and handoff() used to check only instance and epoch, never
    liveness. An expired lease nobody has taken over is fully resurrected
    by a later renew, and a renew arriving after release() returns a
    live-looking Lease while the stored state stays "released", so
    current() still reports the scope free while the caller believes it
    holds a valid lease. handoff() had the identical gap."""

    def test_renew_refuses_an_expired_lease_instead_of_resurrecting_it(self):
        p = store()
        lease = A.acquire(p, "R1", "S1", "primary", "primary-a", 60, now=1000.0)
        with self.assertRaises(A.AuthorityRefused):
            A.renew(p, "R1", "S1", "primary-a", lease.epoch, 60,
                    now=1000.0 + 60.0 + 1)
        self.assertIsNone(A.current(p, "R1", "S1", now=1000.0 + 60.0 + 1))

    def test_renew_refuses_a_released_lease_current_still_reports_free(self):
        p = store()
        lease = A.acquire(p, "R1", "S1", "primary", "primary-a", 60, now=1000.0)
        A.release(p, "R1", "S1", "primary-a", lease.epoch, now=1005.0)
        with self.assertRaises(A.AuthorityRefused):
            A.renew(p, "R1", "S1", "primary-a", lease.epoch, 60, now=1006.0)
        self.assertIsNone(A.current(p, "R1", "S1", now=1006.0))

    def test_handoff_refuses_an_expired_lease(self):
        p = store()
        lease = A.acquire(p, "R1", "S1", "primary", "primary-a", 60, now=1000.0)
        with self.assertRaises(A.AuthorityRefused):
            A.handoff(p, "R1", "S1", "primary-a", lease.epoch, "secondary",
                      "secondary-a", 60, "reason", now=1000.0 + 60.0 + 1)

    def test_handoff_refuses_a_released_lease(self):
        p = store()
        lease = A.acquire(p, "R1", "S1", "primary", "primary-a", 60, now=1000.0)
        A.release(p, "R1", "S1", "primary-a", lease.epoch, now=1005.0)
        with self.assertRaises(A.AuthorityRefused):
            A.handoff(p, "R1", "S1", "primary-a", lease.epoch, "secondary",
                      "secondary-a", 60, "reason", now=1006.0)


class ExpiryBoundaryIsExclusive(unittest.TestCase):
    """Changing `>` to `>=` in _is_live leaves both suites green without
    this test. At exactly expires_at, the lease already reads as expired,
    not live for one more instant."""

    def test_current_is_none_at_exactly_expires_at(self):
        p = store()
        A.acquire(p, "R1", "S1", "primary", "primary-a", 60, now=1000.0)
        self.assertIsNone(A.current(p, "R1", "S1", now=1060.0))

    def test_check_is_false_at_exactly_expires_at(self):
        p = store()
        lease = A.acquire(p, "R1", "S1", "primary", "primary-a", 60, now=1000.0)
        self.assertFalse(A.check(p, "R1", "S1", "primary-a", lease.epoch,
                                  now=1060.0))

    def test_current_is_still_live_one_tick_before_expires_at(self):
        p = store()
        A.acquire(p, "R1", "S1", "primary", "primary-a", 60, now=1000.0)
        self.assertIsNotNone(A.current(p, "R1", "S1", now=1059.999999))


class MalformedStoreShapeIsUnreadable(unittest.TestCase):
    """Valid JSON, valid top-level object, wrong internal shape. Without
    _validate_shape this reads as an empty (therefore free) scope from
    current()/check()/audit() while the mutators crash with a bare
    KeyError instead of the documented AuthorityUnreadable, so a caller
    written to the documented contract (catch AuthorityUnreadable, block)
    either wrongly proceeds on a corrupt store or takes an exception it
    was never told about."""

    def _write(self, p, obj):
        os.makedirs(os.path.dirname(p), exist_ok=True)
        with open(p, "w", encoding="utf-8") as fh:
            json.dump(obj, fh)

    def test_a_bucket_missing_leases_is_unreadable(self):
        p = store()
        self._write(p, {"R1": {"audit": []}})
        with self.assertRaises(A.AuthorityUnreadable):
            A.current(p, "R1", "S1", now=1000.0)
        with self.assertRaises(A.AuthorityUnreadable):
            A.acquire(p, "R1", "S1", "primary", "primary-a", 60, now=1000.0)

    def test_a_lease_record_missing_a_required_key_is_unreadable(self):
        p = store()
        self._write(p, {"R1": {"leases": {"S1": {"instance": "primary-a"}},
                                "audit": []}})
        with self.assertRaises(A.AuthorityUnreadable):
            A.current(p, "R1", "S1", now=1000.0)
        with self.assertRaises(A.AuthorityUnreadable):
            A.check(p, "R1", "S1", "primary-a", 1, now=1000.0)

    def test_leases_that_is_not_a_dict_is_unreadable(self):
        p = store()
        self._write(p, {"R1": {"leases": ["not", "a", "dict"], "audit": []}})
        with self.assertRaises(A.AuthorityUnreadable):
            A.audit(p, "R1")


if __name__ == "__main__":
    unittest.main()

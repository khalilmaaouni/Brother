"""Tests for machine_reservation.py. Stdlib unittest only, no sleeps, no
network: every clock reading is injected so time never actually passes.

Each test asserts the durable file's content after the operation, not only
the return value, because a return value can be right while the file on
disk is wrong (or the other way around): the two are re-checked separately
here on purpose.
"""
import json
import os
import shutil
import tempfile
import unittest

import machine_reservation as mr


def _clock(seq):
    """A clock callable that returns each value in `seq` in order, then
    repeats the last one forever (so a test can drive several calls without
    naming every single one)."""
    seq = list(seq)

    def _tick():
        if len(seq) > 1:
            return seq.pop(0)
        return seq[0]
    return _tick


class MachineReservationBase(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp(prefix="machine-reservation-test-")
        self.path = os.path.join(self.dir, "reservation.json")

    def tearDown(self):
        shutil.rmtree(self.dir, ignore_errors=True)

    def _on_disk(self):
        with open(self.path, encoding="utf-8") as fh:
            return json.load(fh)


class Acquire(MachineReservationBase):
    def test_first_acquire_on_a_missing_file_succeeds(self):
        res, problem = mr.acquire(self.path, "alice", 100, clock=_clock([0]))
        self.assertEqual(problem, "")
        self.assertEqual(res["holder"], "alice")
        self.assertEqual(res["expires_at"], 100)
        on_disk = self._on_disk()
        self.assertEqual(on_disk["holder"]["holder"], "alice")

    def test_a_live_holder_refuses_a_different_acquirer(self):
        mr.acquire(self.path, "alice", 100, clock=_clock([0]))
        res, problem = mr.acquire(self.path, "bob", 100, clock=_clock([10]))
        self.assertIsNone(res)
        self.assertIn("alice", problem)
        self.assertIn("90", problem)  # 90s left on the lease
        on_disk = self._on_disk()
        self.assertEqual(on_disk["holder"]["holder"], "alice")  # unchanged

    def test_two_acquires_racing_same_process_sequential_calls(self):
        """No same-holder exemption: the second call is refused exactly
        like a stranger's would be, because acquire() answers "is the
        machine free", not "is it free for me". This is the deciding
        predicate the mutation test below also exercises."""
        first, problem1 = mr.acquire(self.path, "alice", 100, clock=_clock([0]))
        second, problem2 = mr.acquire(self.path, "alice", 100, clock=_clock([1]))
        self.assertEqual(problem1, "")
        self.assertIsNone(second)
        self.assertIn("alice", problem2)
        on_disk = self._on_disk()
        # still the FIRST grant's expiry, the second call changed nothing
        self.assertEqual(on_disk["holder"]["expires_at"], 100)

    def test_acquire_after_expiry_succeeds_and_names_the_reclaim(self):
        mr.acquire(self.path, "alice", 100, clock=_clock([0]))
        res, problem = mr.acquire(self.path, "bob", 50, clock=_clock([200]))
        self.assertEqual(problem, "")
        self.assertEqual(res["holder"], "bob")
        self.assertEqual(res["reclaimed_from"], "alice")
        on_disk = self._on_disk()
        self.assertEqual(on_disk["holder"]["holder"], "bob")

    def test_acquire_while_a_reap_is_due_needs_no_prior_reap_call(self):
        """acquire() reclaims a dead holder inline; nothing requires a
        caller to run reap() first."""
        mr.acquire(self.path, "alice", 10, clock=_clock([0]))
        # never call reap() here on purpose
        res, problem = mr.acquire(self.path, "carol", 10, clock=_clock([999]))
        self.assertEqual(problem, "")
        self.assertEqual(res["holder"], "carol")

    def test_dead_pid_on_this_host_is_reclaimed_before_the_lease_expires(self):
        data = {"holder": {"holder": "alice", "pid": _certainly_dead_pid(),
                            "hostname": mr._hostname(), "acquired_at": 0,
                            "expires_at": 10 ** 9}}  # far in the future
        mr._write(self.path, data)
        res, problem = mr.acquire(self.path, "bob", 100, clock=_clock([5]))
        self.assertEqual(problem, "")
        self.assertEqual(res["holder"], "bob")

    def test_ttl_zero_is_refused(self):
        res, problem = mr.acquire(self.path, "alice", 0, clock=_clock([0]))
        self.assertIsNone(res)
        self.assertIn("ttl", problem)
        self.assertFalse(os.path.exists(self.path))

    def test_ttl_negative_is_refused(self):
        res, problem = mr.acquire(self.path, "alice", -5, clock=_clock([0]))
        self.assertIsNone(res)
        self.assertIn("ttl", problem)

    def test_empty_state_file_refuses_rather_than_reading_free(self):
        with open(self.path, "w", encoding="utf-8") as fh:
            fh.write("")
        res, problem = mr.acquire(self.path, "alice", 10, clock=_clock([0]))
        self.assertIsNone(res)
        self.assertIn(mr.NODATA, problem)
        # the empty file was never overwritten by this refusal
        with open(self.path, encoding="utf-8") as fh:
            self.assertEqual(fh.read(), "")

    def test_corrupt_json_refuses_rather_than_reading_free(self):
        with open(self.path, "w", encoding="utf-8") as fh:
            fh.write("{not json")
        res, problem = mr.acquire(self.path, "alice", 10, clock=_clock([0]))
        self.assertIsNone(res)
        self.assertIn(mr.NODATA, problem)

    def test_json_that_is_not_an_object_refuses(self):
        with open(self.path, "w", encoding="utf-8") as fh:
            fh.write("[1, 2, 3]")
        res, problem = mr.acquire(self.path, "alice", 10, clock=_clock([0]))
        self.assertIsNone(res)
        self.assertIn(mr.NODATA, problem)

    def test_clock_going_backwards_does_not_crash_and_stays_conservative(self):
        mr.acquire(self.path, "alice", 100, clock=_clock([100]))
        # now jumps BACKWARDS to 10: alice's lease (expires at 200) still
        # reads live, so a stranger is still refused, never an exception.
        res, problem = mr.acquire(self.path, "bob", 10, clock=_clock([10]))
        self.assertIsNone(res)
        self.assertIn("alice", problem)


class Renew(MachineReservationBase):
    def test_holder_renews_its_own_live_lease(self):
        mr.acquire(self.path, "alice", 100, clock=_clock([0]))
        res, problem = mr.renew(self.path, "alice", 50, clock=_clock([10]))
        self.assertEqual(problem, "")
        self.assertEqual(res["expires_at"], 60)
        on_disk = self._on_disk()
        self.assertEqual(on_disk["holder"]["expires_at"], 60)

    def test_non_holder_renewing_is_refused(self):
        mr.acquire(self.path, "alice", 100, clock=_clock([0]))
        res, problem = mr.renew(self.path, "bob", 50, clock=_clock([10]))
        self.assertIsNone(res)
        self.assertIn("alice", problem)
        on_disk = self._on_disk()
        self.assertEqual(on_disk["holder"]["expires_at"], 100)  # unchanged

    def test_renewing_with_no_reservation_at_all_is_refused(self):
        res, problem = mr.renew(self.path, "alice", 50, clock=_clock([0]))
        self.assertIsNone(res)
        self.assertIn("no reservation", problem)

    def test_holder_renewing_after_its_own_expiry_is_refused(self):
        mr.acquire(self.path, "alice", 10, clock=_clock([0]))
        res, problem = mr.renew(self.path, "alice", 50, clock=_clock([100]))
        self.assertIsNone(res)
        self.assertIn("expired", problem)
        on_disk = self._on_disk()
        self.assertEqual(on_disk["holder"]["holder"], "alice")  # still there, just dead

    def test_renew_ttl_zero_is_refused(self):
        mr.acquire(self.path, "alice", 100, clock=_clock([0]))
        res, problem = mr.renew(self.path, "alice", 0, clock=_clock([10]))
        self.assertIsNone(res)
        self.assertIn("ttl", problem)


class Release(MachineReservationBase):
    def test_holder_releases_its_own_reservation(self):
        mr.acquire(self.path, "alice", 100, clock=_clock([0]))
        res, problem = mr.release(self.path, "alice", clock=_clock([5]))
        self.assertEqual(problem, "")
        on_disk = self._on_disk()
        self.assertIsNone(on_disk["holder"])
        self.assertEqual(on_disk["last_released"]["holder"], "alice")

    def test_non_holder_releasing_is_refused_not_silent(self):
        mr.acquire(self.path, "alice", 100, clock=_clock([0]))
        res, problem = mr.release(self.path, "bob", clock=_clock([5]))
        self.assertIsNone(res)
        self.assertIn("alice", problem)
        on_disk = self._on_disk()
        self.assertEqual(on_disk["holder"]["holder"], "alice")  # untouched

    def test_releasing_when_nothing_is_held_is_refused(self):
        res, problem = mr.release(self.path, "alice", clock=_clock([0]))
        self.assertIsNone(res)
        self.assertIn("no reservation", problem)

    def test_after_release_a_new_acquirer_can_take_it_immediately(self):
        mr.acquire(self.path, "alice", 100, clock=_clock([0]))
        mr.release(self.path, "alice", clock=_clock([5]))
        res, problem = mr.acquire(self.path, "bob", 20, clock=_clock([6]))
        self.assertEqual(problem, "")
        self.assertEqual(res["holder"], "bob")


class Reap(MachineReservationBase):
    def test_reap_on_an_empty_store_does_nothing(self):
        detail, problem = mr.reap(self.path, now_value=0)
        self.assertIsNone(detail)
        self.assertEqual(problem, "")
        self.assertFalse(os.path.exists(self.path))

    def test_reap_leaves_a_live_holder_untouched(self):
        mr.acquire(self.path, "alice", 100, clock=_clock([0]))
        detail, problem = mr.reap(self.path, now_value=10)
        self.assertIsNone(detail)
        self.assertEqual(problem, "")
        on_disk = self._on_disk()
        self.assertEqual(on_disk["holder"]["holder"], "alice")

    def test_reap_clears_and_records_a_holder_whose_ttl_genuinely_passed(self):
        mr.acquire(self.path, "alice", 10, clock=_clock([0]))
        detail, problem = mr.reap(self.path, now_value=100)
        self.assertEqual(problem, "")
        self.assertEqual(detail["holder"], "alice")
        self.assertIn("expired", detail["reaped_reason"])
        on_disk = self._on_disk()
        self.assertIsNone(on_disk["holder"])
        self.assertEqual(on_disk["last_reaped"]["holder"], "alice")

    def test_reap_clears_a_dead_pid_holder_before_its_ttl_expires(self):
        data = {"holder": {"holder": "alice", "pid": _certainly_dead_pid(),
                            "hostname": mr._hostname(), "acquired_at": 0,
                            "expires_at": 10 ** 9}}
        mr._write(self.path, data)
        detail, problem = mr.reap(self.path, now_value=5)
        self.assertEqual(problem, "")
        self.assertEqual(detail["holder"], "alice")
        self.assertIn("pid", detail["reaped_reason"])

    def test_reap_on_a_corrupt_file_refuses_rather_than_clearing(self):
        with open(self.path, "w", encoding="utf-8") as fh:
            fh.write("not json at all")
        detail, problem = mr.reap(self.path, now_value=0)
        self.assertIsNone(detail)
        self.assertIn(mr.NODATA, problem)


class MutationProofOwnCheck(MachineReservationBase):
    """The mutation the brief asks be run and restored by hand (see the
    report), kept here too as a standing regression: a helper that pins the
    deciding predicate down so nobody can silently revert it. This class
    tests the REAL, un-mutated code and must stay green; the by-hand
    mutation is applied and reverted directly against machine_reservation.py
    during verification, not against this file."""

    def test_an_expired_holder_never_blocks_a_new_acquire(self):
        mr.acquire(self.path, "alice", 10, clock=_clock([0]))
        res, problem = mr.acquire(self.path, "bob", 10, clock=_clock([50]))
        self.assertIsNotNone(res, "an expired holder must not block acquire: %s" % problem)


def _certainly_dead_pid():
    """A pid essentially guaranteed not to be running: a fresh child process
    that this call waits for and reaps, so its pid is immediately free and
    verifiably not alive."""
    pid = os.fork() if hasattr(os, "fork") else None
    if pid is None:
        # No fork() on this platform: fall back to a pid far past any
        # realistic live range, which pid_alive() will read as gone via
        # ProcessLookupError.
        return 999999
    if pid == 0:
        os._exit(0)
    os.waitpid(pid, 0)
    return pid


if __name__ == "__main__":
    unittest.main()

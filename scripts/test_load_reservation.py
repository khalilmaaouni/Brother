"""Tests for load_reservation.py. Stdlib unittest only, no sleeps, no real
disk probing: every reading and reservation is injected, every clock is a
fixed value passed in.

The MUTATION PROOF named in this unit's brief (make a command's NAME grant
admission again, watch this suite fail, revert, confirm green) is applied
and reverted directly against load_reservation.py during verification, not
kept in this file: this class tests the real, un-mutated code and must stay
green.
"""
import json
import os
import shutil
import tempfile
import unittest

import load_reservation as lr


def _certainly_dead_pid():
    """A pid essentially guaranteed not to be running: a fresh child process
    that this call waits for and reaps, so its pid is immediately free and
    verifiably not alive. Mirrors test_machine_reservation.py's own helper
    so the two suites do not drift on how they fake a dead process."""
    pid = os.fork() if hasattr(os, "fork") else None
    if pid is None:
        return 999999
    if pid == 0:
        os._exit(0)
    os.waitpid(pid, 0)
    return pid


class LoadReservationBase(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp(prefix="load-reservation-test-")
        self.log_path = os.path.join(self.dir, "refusals.jsonl")

    def tearDown(self):
        shutil.rmtree(self.dir, ignore_errors=True)

    def _admit(self, work, reading, reservation, now=100.0):
        return lr.admit(work, reading, reservation,
                         clock=lambda: now, log_path=self.log_path)

    def _refusals(self):
        if not os.path.exists(self.log_path):
            return []
        with open(self.log_path, encoding="utf-8") as fh:
            return [json.loads(line) for line in fh if line.strip()]


class TestDiskBands(LoadReservationBase):
    def test_clears_cleanup_band_admits_without_reservation(self):
        r = self._admit({"kind": "battery"}, {"disk_free_gib": 20.0}, None)
        self.assertEqual(r["verdict"], lr.ADMIT)
        self.assertEqual(self._refusals(), [])

    def test_cleanup_band_without_reservation_refuses(self):
        r = self._admit({"kind": "battery"}, {"disk_free_gib": 10.0}, None)
        self.assertEqual(r["verdict"], lr.REFUSE)
        self.assertEqual(len(self._refusals()), 1)

    def test_cleanup_band_with_live_reservation_admits(self):
        res = {"holder": "orch", "pid": os.getpid(), "hostname": "elsewhere",
               "expires_at": 1000.0}
        r = self._admit({"kind": "battery"}, {"disk_free_gib": 10.0}, res)
        self.assertEqual(r["verdict"], lr.ADMIT)
        self.assertEqual(self._refusals(), [])

    def test_refuse_floor_refuses_even_with_a_live_reservation(self):
        res = {"holder": "orch", "pid": os.getpid(), "hostname": "elsewhere",
               "expires_at": 1000.0}
        r = self._admit({"kind": "battery"}, {"disk_free_gib": 5.0}, res)
        self.assertEqual(r["verdict"], lr.REFUSE)
        self.assertEqual(len(self._refusals()), 1)


class TestReservationLiveness(LoadReservationBase):
    """A reservation held by a dead holder defers to machine_reservation's
    own expiry/pid rule, never a copy of it."""

    def test_expired_lease_is_treated_as_not_held(self):
        res = {"holder": "orch", "pid": os.getpid(), "hostname": "elsewhere",
               "expires_at": 50.0}  # now=100.0 in _admit default
        r = self._admit({"kind": "battery"}, {"disk_free_gib": 10.0}, res)
        self.assertEqual(r["verdict"], lr.REFUSE)

    def test_dead_pid_on_this_host_is_treated_as_not_held(self):
        res = {"holder": "orch", "pid": _certainly_dead_pid(),
               "hostname": lr.machine_reservation._hostname(),
               "expires_at": 1000.0}
        r = self._admit({"kind": "battery"}, {"disk_free_gib": 10.0}, res)
        self.assertEqual(r["verdict"], lr.REFUSE)

    def test_dead_pid_on_another_host_still_counts_as_held(self):
        # A foreign hostname falls back to pure time-based expiry, exactly
        # like machine_reservation._dead_reason itself: the pid check never
        # applies across hosts.
        res = {"holder": "orch", "pid": _certainly_dead_pid(),
               "hostname": "some-other-machine", "expires_at": 1000.0}
        r = self._admit({"kind": "battery"}, {"disk_free_gib": 10.0}, res)
        self.assertEqual(r["verdict"], lr.ADMIT)


class TestUnreadableReading(LoadReservationBase):
    def test_missing_disk_field_refuses_with_nodata(self):
        r = self._admit({"kind": "battery"}, {"cores_available": 4}, None)
        self.assertEqual(r["verdict"], lr.REFUSE)
        self.assertTrue(any(lr.NODATA in reason for reason in r["reasons"]))

    def test_explicit_none_disk_refuses_with_nodata(self):
        r = self._admit({"kind": "battery"}, {"disk_free_gib": None}, None)
        self.assertEqual(r["verdict"], lr.REFUSE)
        self.assertTrue(any(lr.NODATA in reason for reason in r["reasons"]))

    def test_reading_present_but_zero_is_not_nodata_and_refuses_on_the_floor(self):
        r = self._admit({"kind": "battery"}, {"disk_free_gib": 0.0}, None)
        self.assertEqual(r["verdict"], lr.REFUSE)
        self.assertFalse(any(lr.NODATA in reason for reason in r["reasons"]))
        self.assertEqual(r["disk_free_gib"], 0.0)

    def test_unreadable_reading_also_refuses_a_persist_request(self):
        r = self._admit({"kind": "persist", "bytes": 1.0}, {}, None)
        self.assertEqual(r["verdict"], lr.REFUSE)
        self.assertTrue(any(lr.NODATA in reason for reason in r["reasons"]))


class TestBoundedPersistence(LoadReservationBase):
    def test_persist_admitted_under_the_refuse_floor_when_it_fits(self):
        r = self._admit({"kind": "persist", "bytes": 1.0 * lr.GIB},
                         {"disk_free_gib": 5.0}, None)
        self.assertEqual(r["verdict"], lr.ADMIT)

    def test_persist_refused_when_budget_exceeds_free_space(self):
        r = self._admit({"kind": "persist", "bytes": 6.0 * lr.GIB},
                         {"disk_free_gib": 5.0}, None)
        self.assertEqual(r["verdict"], lr.REFUSE)
        self.assertEqual(len(self._refusals()), 1)

    def test_persist_requires_a_declared_budget(self):
        r = self._admit({"kind": "persist"}, {"disk_free_gib": 20.0}, None)
        self.assertEqual(r["verdict"], lr.REFUSE)

    def test_two_bounded_operations_at_once_can_together_overrun_the_disk(self):
        reading = {"disk_free_gib": 10.0}
        first = self._admit({"kind": "persist", "bytes": 5.0 * lr.GIB},
                             reading, None)
        self.assertEqual(first["verdict"], lr.ADMIT)
        second = self._admit(
            {"kind": "persist", "bytes": 6.0 * lr.GIB,
             "concurrent_bytes": 5.0 * lr.GIB},
            reading, None)
        self.assertEqual(second["verdict"], lr.REFUSE)

    def test_two_bounded_operations_at_once_can_both_fit(self):
        reading = {"disk_free_gib": 10.0}
        first = self._admit({"kind": "persist", "bytes": 3.0 * lr.GIB},
                             reading, None)
        second = self._admit(
            {"kind": "persist", "bytes": 3.0 * lr.GIB,
             "concurrent_bytes": 3.0 * lr.GIB},
            reading, None)
        self.assertEqual(first["verdict"], lr.ADMIT)
        self.assertEqual(second["verdict"], lr.ADMIT)


class TestRefusalIsRecorded(LoadReservationBase):
    def test_refusal_records_classification_readings_and_gate_version(self):
        self._admit({"kind": "battery", "name": "git commit -m x"},
                     {"disk_free_gib": 3.0}, None)
        recs = self._refusals()
        self.assertEqual(len(recs), 1)
        rec = recs[0]
        self.assertEqual(rec["verdict"], lr.REFUSE)
        self.assertEqual(rec["kind"], "battery")
        self.assertEqual(rec["work"]["name"], "git commit -m x")
        self.assertEqual(rec["disk_free_gib"], 3.0)
        self.assertEqual(rec["gate"], lr.GATE_VERSION)
        self.assertIn("at", rec)

    def test_admitted_work_is_never_logged(self):
        self._admit({"kind": "battery"}, {"disk_free_gib": 20.0}, None)
        self.assertEqual(self._refusals(), [])

    def test_an_unwritable_log_never_raises(self):
        r = lr.admit({"kind": "battery"}, {"disk_free_gib": 3.0}, None,
                      clock=lambda: 100.0,
                      log_path="/dev/null/cannot/exist")
        self.assertEqual(r["verdict"], lr.REFUSE)


class TestNameIsNeverTheDecision(LoadReservationBase):
    """THE DECIDING PROPERTY: a command's name is not its cost. Same low
    disk, no reservation, only the declared `name` differs between a
    scary-looking command and a cheap-looking one; both must refuse
    identically, because admit() never reads work["name"] to decide."""

    def test_a_cheap_looking_name_refuses_exactly_like_a_scary_one(self):
        reading = {"disk_free_gib": 5.0}
        scary = self._admit({"kind": "heavy", "name": "rm -rf /"}, reading, None)
        cheap = self._admit({"kind": "heavy", "name": "git commit"}, reading, None)
        self.assertEqual(scary["verdict"], lr.REFUSE)
        self.assertEqual(cheap["verdict"], lr.REFUSE)


if __name__ == "__main__":
    unittest.main()

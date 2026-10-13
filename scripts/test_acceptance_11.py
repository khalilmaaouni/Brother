"""scripts/acceptance_11.py under a pinned disk reading, so status truthfulness is what is measured, never this machine's disk.

LaneWorker asks load_reservation for admission before it spawns (LOAD-01/02, release 1.0.20). acceptance_11 built its
worker without the reservation every real run holds, so on a machine under the 15 GiB cleanup band the worker was held,
run_node returned before its done check with repair=None, the run read FAIL, and --calibrate read PASS without its forced
lie ever being consulted: a calibration that passes without its red is decoration. The reading is injected here so both
bands are proven on any machine, one fixture per guard: inside the cleanup band this run's own reservation admits the
worker and clause A decides; under the refuse floor the worker is held and that reads NO-DATA; a reservation this run
could not hold, or could not import, is NO-DATA before any worker runs; a calibration whose lie was never consulted is
FAIL "could not run at all", never PASS.
"""
import os
import shutil
import sys
import tempfile
import unittest
from unittest import mock

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import acceptance_11 as A  # noqa: E402
import loop_bridge  # noqa: E402
import resource_gate  # noqa: E402


def _reading(disk):
    return {"disk_free_gib": disk, "cores_available": 8, "load1": 0.0, "mem_free_gib": 8.0, "errors": {}}


class PinnedDisk(unittest.TestCase):
    def pin(self, disk):
        log = tempfile.mkdtemp(prefix="acceptance-11-refusals-")
        self.addCleanup(shutil.rmtree, log, True)
        for patcher in (mock.patch.object(resource_gate, "read", return_value=_reading(disk)),
                        mock.patch.object(loop_bridge, "LOAD_REFUSALS_LOG", os.path.join(log, "refusals.jsonl"))):
            patcher.start()
            self.addCleanup(patcher.stop)

    def test_inside_the_cleanup_band_the_runs_own_reservation_lets_clause_a_decide(self):
        self.pin(resource_gate.DISK_REFUSE_GIB + 1)
        code, evidence = A.run()
        self.assertEqual((code, evidence[:5]), (0, "PASS:"), evidence)
        self.assertIn("EXHAUSTED", evidence)
        code, evidence = A.calibrate()
        self.assertEqual(code, 0, evidence)
        self.assertIn("correctly read the resulting no-repair-attempted record", evidence)

    def test_a_reservation_this_run_could_not_hold_is_no_data_before_any_worker_runs(self):
        import machine_reservation
        self.pin(resource_gate.DISK_REFUSE_GIB + 1)
        with mock.patch.object(machine_reservation, "acquire", return_value=(None, "store unreadable")):
            code, evidence = A.run()
        self.assertEqual(code, 2, evidence)
        self.assertEqual(evidence, "NO-DATA: this run could not hold its own machine reservation: store unreadable")

    def test_a_reservation_module_that_will_not_import_is_no_data_before_any_worker_runs(self):
        self.pin(resource_gate.DISK_REFUSE_GIB + 1)
        with mock.patch.dict(sys.modules, {"machine_reservation": None}):
            code, evidence = A.run()
        self.assertEqual(code, 2, evidence)
        self.assertTrue(evidence.startswith("NO-DATA: could not import the machine reservation:"), evidence)

    def test_under_the_refuse_floor_a_held_worker_is_no_data_never_a_clause_a_fail(self):
        self.pin(resource_gate.DISK_REFUSE_GIB - 1)
        code, evidence = A.run()
        self.assertEqual(code, 2, evidence)
        self.assertTrue(evidence.startswith("NO-DATA: run_node returned before its done check"), evidence)
        self.assertIn("refuse floor", evidence)
        code, evidence = A.calibrate()
        self.assertEqual(code, 1, evidence)
        self.assertIn("could not run at all", evidence)

    def test_a_calibration_whose_lie_was_never_consulted_is_never_a_pass(self):
        # A record that says "no repair, done check red" without run_node ever asking is_pass: the shape a held or
        # refused dispatch takes if a later run_node stops marking it NO-DATA. Only the consulted-lie guard sees it.
        self.pin(resource_gate.DISK_REFUSE_GIB + 1)
        untouched = {"id": "OP1", "worker_status": "unavailable", "verdict": "FAIL",
                     "reason": "returned before the done check", "repair": None}
        with mock.patch.object(loop_bridge, "run_node", return_value=untouched):
            code, evidence = A.calibrate()
        self.assertEqual(code, 1, evidence)
        self.assertIn("could not run at all", evidence)
        self.assertIn("forced lie (bm_verify.is_pass) was never consulted", evidence)


if __name__ == "__main__":
    unittest.main()

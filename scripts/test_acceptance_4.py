"""scripts/acceptance_4.py under a pinned disk reading, so the scope gate is what is measured, never this machine's disk.

LaneWorker asks load_reservation for admission before it spawns (LOAD-01/02, release 1.0.20). acceptance_4 built its
worker without the reservation every real run holds, so on a machine under the 15 GiB cleanup band the worker was held,
the scope gate never ran, and run and --calibrate both read FAIL (verdict=None). The reading is injected here so both
bands are proven on any machine: inside the cleanup band this run's own reservation admits the worker and the scope gate
decides; under the refuse floor the worker is held and that reads NO-DATA, never a FAIL of the scope gate.
"""
import os
import shutil
import sys
import tempfile
import unittest
from unittest import mock

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import acceptance_4 as A  # noqa: E402
import loop_bridge  # noqa: E402
import resource_gate  # noqa: E402


def _reading(disk):
    return {"disk_free_gib": disk, "cores_available": 8, "load1": 0.0, "mem_free_gib": 8.0, "errors": {}}


class PinnedDisk(unittest.TestCase):
    def pin(self, disk):
        log = tempfile.mkdtemp(prefix="acceptance-4-refusals-")
        self.addCleanup(shutil.rmtree, log, True)
        for patcher in (mock.patch.object(resource_gate, "read", return_value=_reading(disk)),
                        mock.patch.object(loop_bridge, "LOAD_REFUSALS_LOG", os.path.join(log, "refusals.jsonl"))):
            patcher.start()
            self.addCleanup(patcher.stop)

    def test_inside_the_cleanup_band_the_runs_own_reservation_lets_the_scope_gate_decide(self):
        self.pin(resource_gate.DISK_REFUSE_GIB + 1)
        code, evidence = A.run()
        self.assertEqual((code, evidence[:5]), (0, "PASS:"), evidence)
        self.assertIn("CLEAN", evidence)
        code, evidence = A.calibrate()
        self.assertEqual(code, 0, evidence)
        self.assertIn("verdict=QUARANTINE", evidence)

    def test_a_reservation_this_run_could_not_hold_is_no_data_before_any_worker_runs(self):
        import machine_reservation
        self.pin(resource_gate.DISK_REFUSE_GIB + 1)
        with mock.patch.object(machine_reservation, "acquire", return_value=(None, "store unreadable")):
            code, evidence = A.run()
        self.assertEqual(code, 2, evidence)
        self.assertEqual(evidence, "NO-DATA: this run could not hold its own machine reservation: store unreadable")

    def test_under_the_refuse_floor_a_held_worker_is_no_data_never_a_scope_gate_fail(self):
        self.pin(resource_gate.DISK_REFUSE_GIB - 1)
        code, evidence = A.run()
        self.assertEqual(code, 2, evidence)
        self.assertTrue(evidence.startswith("NO-DATA: the worker was held"), evidence)
        self.assertIn("refuse floor", evidence)
        code, evidence = A.calibrate()
        self.assertEqual(code, 1, evidence)
        self.assertIn("could not run at all", evidence)


if __name__ == "__main__":
    unittest.main()

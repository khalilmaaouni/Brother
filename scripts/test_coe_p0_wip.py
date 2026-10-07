"""Tests for scripts/coe_p0_wip.py, sub unit P0.3, the work in progress gate.

Run with: python3 -B scripts/test_coe_p0_wip.py
"""

import json
import os
import sys
import tempfile
import threading
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import coe_p0_wip

SCHEMA = "brother-coe-wip-v1"


def state_bytes(active):
    return json.dumps({"schema": SCHEMA, "active": list(active)}).encode("utf-8")


class WipGateTestCase(unittest.TestCase):
    def setUp(self):
        coe_p0_wip.clear_caches()
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.state_path = os.path.join(self.tmp.name, "wip-state.json")

    def write_state(self, payload):
        with open(self.state_path, "wb") as handle:
            handle.write(payload)

    def read_active(self):
        with open(self.state_path, "rb") as handle:
            payload = json.loads(handle.read().decode("utf-8"))
        return list(payload["active"])


class GateCheckTests(WipGateTestCase):
    def test_gate_allows_two_only(self):
        self.assertEqual(coe_p0_wip.WIP_LIMIT, 2)
        self.assertTrue(coe_p0_wip.wip_gate_check(0))
        self.assertTrue(coe_p0_wip.wip_gate_check(1))
        self.assertFalse(coe_p0_wip.wip_gate_check(2))
        self.assertFalse(coe_p0_wip.wip_gate_check(3))

    def test_hostile_active_count_refused(self):
        hostile = [None, True, False, "2", 2.5, float("nan"), [1], {"a": 1}, b"2"]
        for value in hostile:
            with self.subTest(value=repr(value)):
                with self.assertRaises(ValueError):
                    coe_p0_wip.wip_gate_check(value)

    def test_negative_active_count_refused(self):
        with self.assertRaises(ValueError):
            coe_p0_wip.wip_gate_check(-1)


class ActiveLaneCountTests(WipGateTestCase):
    def test_empty_state_file_reads_zero(self):
        self.write_state(b"")
        self.assertEqual(coe_p0_wip.active_lane_count(self.state_path), 0)
        self.write_state(b"   \n\t ")
        self.assertEqual(coe_p0_wip.active_lane_count(self.state_path), 0)
        self.assertTrue(coe_p0_wip.gate_decision(self.state_path))

    def test_count_matches_the_state_file(self):
        self.write_state(state_bytes(["A", "B"]))
        self.assertEqual(coe_p0_wip.active_lane_count(self.state_path), 2)
        self.write_state(state_bytes(["A"]))
        self.assertEqual(coe_p0_wip.active_lane_count(self.state_path), 1)

    def test_missing_state_file_refused(self):
        missing = os.path.join(self.tmp.name, "absent.json")
        with self.assertRaises(ValueError):
            coe_p0_wip.active_lane_count(missing)
        with self.assertRaises(ValueError):
            coe_p0_wip.gate_decision(missing)
        with self.assertRaises(ValueError):
            coe_p0_wip.open_unit(missing, "P0.4")

    def test_directory_as_state_path_refused(self):
        with self.assertRaises(ValueError):
            coe_p0_wip.active_lane_count(self.tmp.name)

    def test_hostile_state_path_refused(self):
        hostile = [None, 3, True, b"/tmp/wip.json", ["wip.json"], {"path": "x"}, ""]
        for value in hostile:
            with self.subTest(value=repr(value)):
                with self.assertRaises(ValueError):
                    coe_p0_wip.active_lane_count(value)

    def test_corrupt_state_file_refused(self):
        corrupt = [
            b"{not json",
            b"[]",
            b"2",
            b"NaN",
            b'{"schema": "brother-coe-wip-v1"}',
            b'{"schema": "other-schema", "active": []}',
            b'{"schema": "brother-coe-wip-v1", "active": "P0.4"}',
            b'{"schema": "brother-coe-wip-v1", "active": [true]}',
            b'{"schema": "brother-coe-wip-v1", "active": [1]}',
            b'{"schema": "brother-coe-wip-v1", "active": [null]}',
            b'{"schema": "brother-coe-wip-v1", "active": [""]}',
            b'{"schema": "brother-coe-wip-v1", "active": [NaN]}',
            b'{"schema": "brother-coe-wip-v1", "active": [], "extra": 1}',
            b'{"schema": "brother-coe-wip-v1", "active": [], "active": []}',
            b"\xff\xfe\x00\x01",
            b'{"schema": "brother-coe-wip-v1", "active": []} trailing',
        ]
        for payload in corrupt:
            self.write_state(payload)
            with self.subTest(payload=payload):
                with self.assertRaises(ValueError):
                    coe_p0_wip.active_lane_count(self.state_path)

    def test_duplicate_unit_ids_refused(self):
        self.write_state(state_bytes(["A", "A"]))
        with self.assertRaises(ValueError):
            coe_p0_wip.active_lane_count(self.state_path)


class DecisionTests(WipGateTestCase):
    def test_decision_follows_the_state(self):
        self.write_state(state_bytes(["A", "B"]))
        self.assertFalse(coe_p0_wip.gate_decision(self.state_path))
        self.write_state(state_bytes(["A"]))
        self.assertTrue(coe_p0_wip.gate_decision(self.state_path))
        self.write_state(state_bytes(["A", "B"]))
        self.assertFalse(coe_p0_wip.gate_decision(self.state_path))

    def test_decision_cached_by_state_hash(self):
        self.write_state(state_bytes(["A"]))
        hits_before, misses_before = coe_p0_wip.decision_cache_info()
        self.assertTrue(coe_p0_wip.gate_decision(self.state_path))
        self.assertTrue(coe_p0_wip.gate_decision(self.state_path))
        hits, misses = coe_p0_wip.decision_cache_info()
        self.assertEqual(hits - hits_before, 1)
        self.assertEqual(misses - misses_before, 1)


class OpenUnitTests(WipGateTestCase):
    def test_open_unit_refuses_over_limit(self):
        self.write_state(state_bytes(["A", "B"]))
        self.assertFalse(coe_p0_wip.open_unit(self.state_path, "C"))
        self.assertEqual(coe_p0_wip.active_lane_count(self.state_path), 2)

    def test_open_unit_adds_units_up_to_the_limit(self):
        self.write_state(b"")
        self.assertTrue(coe_p0_wip.open_unit(self.state_path, "A"))
        self.assertTrue(coe_p0_wip.open_unit(self.state_path, "B"))
        self.assertFalse(coe_p0_wip.open_unit(self.state_path, "C"))
        self.assertEqual(sorted(self.read_active()), ["A", "B"])

    def test_open_unit_uses_a_fresh_count(self):
        self.write_state(state_bytes(["A", "B"]))
        self.assertFalse(coe_p0_wip.open_unit(self.state_path, "C"))
        self.write_state(state_bytes(["A"]))
        self.assertTrue(coe_p0_wip.open_unit(self.state_path, "C"))

    def test_open_unit_refuses_duplicate(self):
        self.write_state(state_bytes(["A"]))
        with self.assertRaises(ValueError):
            coe_p0_wip.open_unit(self.state_path, "A")

    def test_open_unit_refuses_hostile_unit_id(self):
        self.write_state(b"")
        hostile = [None, True, 3, "", "   ", ["A"], {"A": 1}, b"A"]
        for value in hostile:
            with self.subTest(value=repr(value)):
                with self.assertRaises(ValueError):
                    coe_p0_wip.open_unit(self.state_path, value)

    def test_open_unit_leaves_a_corrupt_state_untouched(self):
        self.write_state(b"{not json")
        with self.assertRaises(ValueError):
            coe_p0_wip.open_unit(self.state_path, "A")
        with open(self.state_path, "rb") as handle:
            self.assertEqual(handle.read(), b"{not json")

    def test_concurrent_opens_are_serialized(self):
        self.write_state(b"")
        results = {}
        names = ["U1", "U2", "U3", "U4"]

        def worker(name):
            try:
                results[name] = coe_p0_wip.open_unit(self.state_path, name)
            except ValueError as exc:
                results[name] = exc

        threads = [threading.Thread(target=worker, args=(name,)) for name in names]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()

        opened = sorted(name for name in names if results.get(name) is True)
        self.assertEqual(len(opened), 2)
        for name in names:
            if name not in opened:
                self.assertFalse(results.get(name))
        self.assertEqual(coe_p0_wip.active_lane_count(self.state_path), 2)
        self.assertEqual(sorted(self.read_active()), opened)


if __name__ == "__main__":
    unittest.main()

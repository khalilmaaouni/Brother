import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import loop_bridge


class LoopBridgeStallTests(unittest.TestCase):
    def test_stall_fires_at_3_medians(self):
        state = {"starts": {"worker": {"lane": "lane-A", "now": 0.0}},
                 "samples": {"worker": [1.0, 2.0, 3.0]}}
        reports = loop_bridge.check_stall(state, 7.0)
        self.assertEqual(len(reports), 1)
        self.assertEqual(reports[0]["status"], "STALLED")
        self.assertEqual(reports[0]["stage"], "worker")
        self.assertEqual(reports[0]["lane"], "lane-A")
        reports = loop_bridge.check_stall(state, 6.0)
        self.assertEqual(reports, [])

    def test_no_stall_when_under_threshold(self):
        state = {"starts": {"worker": {"lane": "lane-A", "now": 0.0}},
                 "samples": {"worker": [1.0, 2.0, 3.0]}}
        reports = loop_bridge.check_stall(state, 5.9)
        self.assertEqual(reports, [])

    def test_stall_nodata_when_samples_missing(self):
        state = {"starts": {"worker": {"lane": "lane-A", "now": 0.0}}}
        reports = loop_bridge.check_stall(state, 100.0)
        self.assertEqual(len(reports), 1)
        self.assertEqual(reports[0]["status"], "STALLED-NODATA")
        self.assertIsNone(reports[0]["median"])

    def test_stall_nodata_when_fewer_than_three_samples(self):
        state = {"starts": {"worker": {"lane": "lane-A", "now": 0.0}},
                 "samples": {"worker": [1.0, 2.0]}}
        reports = loop_bridge.check_stall(state, 100.0)
        self.assertEqual(len(reports), 1)
        self.assertEqual(reports[0]["status"], "STALLED-NODATA")

    def test_corrupt_sample_list_gives_none_median(self):
        state = {"samples": {"worker": [1.0, "bad", 3.0]}}
        self.assertIsNone(loop_bridge.stage_median(state, "worker"))

    def test_stage_median_odd_and_even(self):
        state = {"samples": {"worker": [1.0, 2.0, 3.0]}}
        self.assertEqual(loop_bridge.stage_median(state, "worker"), 2.0)
        state = {"samples": {"worker": [1.0, 2.0]}}
        self.assertEqual(loop_bridge.stage_median(state, "worker"), 1.5)

    def test_record_stage_start_overwrites_and_resets_age(self):
        state = {}
        loop_bridge.record_stage_start(state, "worker", "lane-A", 0.0)
        loop_bridge.record_stage_start(state, "worker", "lane-B", 10.0)
        self.assertEqual(state["starts"]["worker"]["lane"], "lane-B")
        self.assertEqual(state["starts"]["worker"]["now"], 10.0)
        state["samples"] = {"worker": [100.0, 100.0, 100.0]}
        reports = loop_bridge.check_stall(state, 20.0)
        self.assertEqual(reports, [])
        reports = loop_bridge.check_stall(state, 400.0)
        self.assertEqual(len(reports), 1)
        self.assertEqual(reports[0]["lane"], "lane-B")

    def test_record_stage_end_ignores_without_start(self):
        state = {}
        result = loop_bridge.record_stage_end(state, "worker", 5.0)
        self.assertEqual(result, state)
        self.assertNotIn("samples", state)

    def test_record_stage_end_stores_sample_and_removes_start(self):
        state = {"starts": {"worker": {"lane": "lane-A", "now": 0.0}}}
        loop_bridge.record_stage_end(state, "worker", 5.0)
        self.assertNotIn("worker", state["starts"])
        self.assertEqual(state["samples"]["worker"], [5.0])

    def test_sample_limit_100(self):
        state = {}
        for i in range(150):
            state = loop_bridge.record_stage_start(state, "worker", "lane-A", float(i))
            loop_bridge.record_stage_end(state, "worker", float(i))
        self.assertEqual(len(state["samples"]["worker"]), 100)
        self.assertEqual(state["samples"]["worker"][0], 50.0)

    def test_check_stall_names_process_and_waited_file(self):
        state = {
            "starts": {"worker": {"lane": "lane-A", "now": 0.0}},
            "samples": {"worker": [1.0, 2.0, 3.0]},
            "processes": {"lane-A": "worker-proc"},
            "waited_files": {"lane-A": "/tmp/waited.txt"},
        }
        reports = loop_bridge.check_stall(state, 100.0)
        self.assertEqual(len(reports), 1)
        self.assertEqual(reports[0]["process"], "worker-proc")
        self.assertEqual(reports[0]["waited_file"], "/tmp/waited.txt")

    def test_check_stall_missing_start_corrupt_time_gives_nodata(self):
        state = {"starts": {"worker": "not-a-dict"}}
        reports = loop_bridge.check_stall(state, 100.0)
        self.assertEqual(len(reports), 1)
        self.assertEqual(reports[0]["status"], "STALLED-NODATA")

    def test_hostile_input_refused(self):
        with self.assertRaises(ValueError):
            loop_bridge.record_stage_start({}, None, "lane", 0.0)
        with self.assertRaises(ValueError):
            loop_bridge.record_stage_start({}, "worker", None, 0.0)
        with self.assertRaises(ValueError):
            loop_bridge.record_stage_start({}, "worker", "lane", True)
        with self.assertRaises(ValueError):
            loop_bridge.record_stage_start({}, "worker", "lane", float("nan"))
        with self.assertRaises(ValueError):
            loop_bridge.record_stage_start([], "worker", "lane", 0.0)
        with self.assertRaises(ValueError):
            loop_bridge.record_stage_end({}, "worker", "bad")
        with self.assertRaises(ValueError):
            loop_bridge.check_stall({}, float("nan"))
        with self.assertRaises(ValueError):
            loop_bridge.record_stage_start({}, ["unhashable"], "lane", 0.0)
        self.assertIsNone(loop_bridge.stage_median("not-a-dict", "worker"))
        self.assertIsNone(loop_bridge.stage_median({}, None))


if __name__ == "__main__":
    unittest.main()

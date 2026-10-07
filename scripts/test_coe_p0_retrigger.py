#!/usr/bin/env python3
"""Tests for coe_p0_retrigger (P0.4)."""

import os
import tempfile
import threading
import unittest
from unittest.mock import patch

import coe_p0_retrigger


class RetriggerDueTest(unittest.TestCase):
    def test_due_when_stale(self):
        with patch("coe_p0_retrigger.time.time", return_value=100000):
            self.assertTrue(coe_p0_retrigger.retrigger_due(100000 - 7200))
            self.assertTrue(coe_p0_retrigger.retrigger_due(100000 - 7201))

    def test_not_due_within_window(self):
        with patch("coe_p0_retrigger.time.time", return_value=100000):
            self.assertFalse(coe_p0_retrigger.retrigger_due(100000 - 7199))
            self.assertFalse(coe_p0_retrigger.retrigger_due(100000))

    def test_empty_state_treated_as_due(self):
        with patch("coe_p0_retrigger.time.time", return_value=100000):
            self.assertTrue(coe_p0_retrigger.retrigger_due(0))

    def test_corrupt_timestamp_refused_toward_due(self):
        with patch("coe_p0_retrigger.time.time", return_value=100000):
            self.assertTrue(coe_p0_retrigger.retrigger_due(-1))

    def test_hostile_input_refused(self):
        for bad in (None, "123", True, 1.5, [], {}):
            with self.assertRaises(ValueError):
                coe_p0_retrigger.retrigger_due(bad)


class RecordCouncilRunTest(unittest.TestCase):
    def test_record_writes_epoch_and_returns_it(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "state")
            with patch("coe_p0_retrigger.time.time", return_value=12345):
                returned = coe_p0_retrigger.record_council_run(path)
            self.assertEqual(returned, 12345)
            with open(path, "rb") as handle:
                self.assertEqual(handle.read(), b"12345\n")
            # And the recorded timestamp is within the window, so not due.
            with patch("coe_p0_retrigger.time.time", return_value=12345):
                self.assertFalse(coe_p0_retrigger.retrigger_due(returned))

    def test_record_hostile_input_refused(self):
        for bad in (None, "", 123, []):
            with self.assertRaises(ValueError):
                coe_p0_retrigger.record_council_run(bad)
        with tempfile.TemporaryDirectory() as tmp:
            with self.assertRaises(ValueError):
                coe_p0_retrigger.record_council_run(tmp)

    def test_concurrent_record_writes_serialized(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "state")
            errors = []
            def worker():
                try:
                    coe_p0_retrigger.record_council_run(path)
                except Exception as exc:
                    errors.append(exc)
            threads = [threading.Thread(target=worker) for _ in range(10)]
            for t in threads:
                t.start()
            for t in threads:
                t.join()
            self.assertEqual(errors, [])
            with open(path, "rb") as handle:
                data = handle.read()
            self.assertTrue(data.endswith(b"\n"))
            value = int(data.decode("ascii").strip())
            self.assertIsInstance(value, int)


if __name__ == "__main__":
    unittest.main()

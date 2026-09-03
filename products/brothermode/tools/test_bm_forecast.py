#!/usr/bin/env python3
"""Regression tests for tools/bm_forecast.py, the forecast-versus-actual
calibration log.

WHAT THIS SUITE IS ACTUALLY DEFENDING
  On 2026-08-11 four time estimates in one overnight plan were all wrong by
  four to five times in the SAME direction (judged estimates ran far longer
  than the work actually took), and nothing recorded forecast against
  actual, so the bias survived the whole night. This tool records both
  sides and computes a correction factor ONLY from real paired history, and
  refuses (NO-DATA, exit 2) rather than invent a multiplier when history is
  too thin. This suite proves that refusal is real: it must be impossible
  to get a calibrated number out of this tool without at least 3 usable
  forecast/actual pairs.

Every fixture uses a fresh tempfile log path, never the real repository
store. Standard library only. Run: python3 tools/test_bm_forecast.py
"""
import io
import json
import os
import subprocess
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
TOOL_PATH = os.path.join(HERE, "bm_forecast.py")


def run_cli(*args):
    """Invoke the CLI as a real subprocess, with BROTHERMODE_ROOT scrubbed
    so a variable set on the developer's own machine can never leak into a
    test that expects an explicit --log/--root to decide the outcome."""
    env = dict(os.environ)
    env.pop("BROTHERMODE_ROOT", None)
    return subprocess.run(
        [sys.executable, TOOL_PATH] + list(args),
        stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        universal_newlines=True, cwd=ROOT, env=env)


def read_lines(log_path):
    if not os.path.exists(log_path):
        return []
    out = []
    with io.open(log_path, "r", encoding="utf-8") as handle:
        for line in handle:
            line = line.strip()
            if line:
                out.append(json.loads(line))
    return out


class RecordAndListTests(unittest.TestCase):

    def test_record_then_list_round_trips_a_forecast(self):
        with tempfile.TemporaryDirectory() as tmp:
            log = os.path.join(tmp, "forecasts-log.jsonl")
            result = run_cli("record", "--task", "L2", "--clock", "agent",
                             "--basis", "judged", "--likely-minutes", "35",
                             "--upper-minutes", "50",
                             "--at", "2026-08-12T01:40:00Z", "--log", log)
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

            records = read_lines(log)
            self.assertEqual(len(records), 1)
            self.assertEqual(records[0]["kind"], "forecast")
            self.assertEqual(records[0]["task"], "L2")
            self.assertEqual(records[0]["likely_minutes"], 35.0)
            self.assertEqual(records[0]["upper_minutes"], 50.0)

            listing = run_cli("list", "--log", log)
            self.assertEqual(listing.returncode, 0, listing.stdout + listing.stderr)
            self.assertIn("L2", listing.stdout)
            self.assertIn("forecast", listing.stdout)

    def test_list_on_empty_log_is_no_data(self):
        with tempfile.TemporaryDirectory() as tmp:
            log = os.path.join(tmp, "forecasts-log.jsonl")
            result = run_cli("list", "--log", log)
            self.assertEqual(result.returncode, 2, result.stdout + result.stderr)
            self.assertTrue(result.stdout.startswith("NO-DATA:"))


class ValidationTests(unittest.TestCase):

    def test_record_refuses_invalid_clock(self):
        with tempfile.TemporaryDirectory() as tmp:
            log = os.path.join(tmp, "forecasts-log.jsonl")
            result = run_cli("record", "--task", "L2", "--clock", "bogus",
                             "--basis", "judged", "--likely-minutes", "10",
                             "--log", log)
            self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
            self.assertFalse(os.path.exists(log))

    def test_record_refuses_invalid_basis(self):
        with tempfile.TemporaryDirectory() as tmp:
            log = os.path.join(tmp, "forecasts-log.jsonl")
            result = run_cli("record", "--task", "L2", "--clock", "agent",
                             "--basis", "bogus", "--likely-minutes", "10",
                             "--log", log)
            self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
            self.assertFalse(os.path.exists(log))


class ActualUnforecastTests(unittest.TestCase):

    def test_actual_for_unforecast_task_warns_but_exits_0(self):
        with tempfile.TemporaryDirectory() as tmp:
            log = os.path.join(tmp, "forecasts-log.jsonl")
            result = run_cli("actual", "--task", "L9", "--minutes", "12",
                             "--at", "2026-08-12T02:00:00Z", "--log", log)
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            self.assertIn("UNFORECAST", result.stdout)
            records = read_lines(log)
            self.assertEqual(len(records), 1)
            self.assertEqual(records[0]["kind"], "actual")


class CalibrateTests(unittest.TestCase):

    def _record(self, log, task, likely, at, clock="agent", basis="judged"):
        result = run_cli("record", "--task", task, "--clock", clock,
                         "--basis", basis, "--likely-minutes", str(likely),
                         "--at", at, "--log", log)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def _actual(self, log, task, minutes, at):
        result = run_cli("actual", "--task", task, "--minutes", str(minutes),
                         "--at", at, "--log", log)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_calibrate_with_zero_pairs_is_no_data(self):
        with tempfile.TemporaryDirectory() as tmp:
            log = os.path.join(tmp, "forecasts-log.jsonl")
            result = run_cli("calibrate", "--log", log)
            self.assertEqual(result.returncode, 2, result.stdout + result.stderr)
            self.assertTrue(result.stdout.startswith("NO-DATA:"))
            self.assertNotIn("median=", result.stdout)

    def test_calibrate_with_one_pair_is_no_data(self):
        with tempfile.TemporaryDirectory() as tmp:
            log = os.path.join(tmp, "forecasts-log.jsonl")
            self._record(log, "L1", 40, "2026-08-12T01:00:00Z")
            self._actual(log, "L1", 20, "2026-08-12T01:30:00Z")
            result = run_cli("calibrate", "--log", log)
            self.assertEqual(result.returncode, 2, result.stdout + result.stderr)
            self.assertTrue(result.stdout.startswith("NO-DATA:"))
            self.assertNotIn("median=", result.stdout)

    def test_calibrate_with_two_pairs_is_no_data(self):
        with tempfile.TemporaryDirectory() as tmp:
            log = os.path.join(tmp, "forecasts-log.jsonl")
            self._record(log, "L1", 40, "2026-08-12T01:00:00Z")
            self._actual(log, "L1", 20, "2026-08-12T01:30:00Z")
            self._record(log, "L2", 40, "2026-08-12T02:00:00Z")
            self._actual(log, "L2", 10, "2026-08-12T02:30:00Z")
            result = run_cli("calibrate", "--log", log)
            self.assertEqual(result.returncode, 2, result.stdout + result.stderr)
            self.assertTrue(result.stdout.startswith("NO-DATA:"))
            self.assertNotIn("median=", result.stdout)

    def test_calibrate_with_three_pairs_returns_exact_median(self):
        with tempfile.TemporaryDirectory() as tmp:
            log = os.path.join(tmp, "forecasts-log.jsonl")
            # ratios: 40/20=2.0, 40/10=4.0, 60/10=6.0 -> median 4.0
            self._record(log, "L1", 40, "2026-08-12T01:00:00Z")
            self._actual(log, "L1", 20, "2026-08-12T01:30:00Z")
            self._record(log, "L2", 40, "2026-08-12T02:00:00Z")
            self._actual(log, "L2", 10, "2026-08-12T02:30:00Z")
            self._record(log, "L3", 60, "2026-08-12T03:00:00Z")
            self._actual(log, "L3", 10, "2026-08-12T03:30:00Z")
            result = run_cli("calibrate", "--log", log)
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            self.assertIn("n=3", result.stdout)
            self.assertIn("median=4.00", result.stdout)
            self.assertIn("min=2.00", result.stdout)
            self.assertIn("max=6.00", result.stdout)

    def test_calibrate_drops_zero_actual_pair_without_crashing(self):
        with tempfile.TemporaryDirectory() as tmp:
            log = os.path.join(tmp, "forecasts-log.jsonl")
            self._record(log, "L1", 40, "2026-08-12T01:00:00Z")
            self._actual(log, "L1", 20, "2026-08-12T01:30:00Z")
            self._record(log, "L2", 40, "2026-08-12T02:00:00Z")
            self._actual(log, "L2", 10, "2026-08-12T02:30:00Z")
            self._record(log, "L3", 60, "2026-08-12T03:00:00Z")
            self._actual(log, "L3", 10, "2026-08-12T03:30:00Z")
            self._record(log, "L4", 60, "2026-08-12T04:00:00Z")
            self._actual(log, "L4", 0, "2026-08-12T04:30:00Z")
            result = run_cli("calibrate", "--log", log)
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            self.assertIn("L4", result.stdout)
            self.assertIn("n=3", result.stdout)

    def test_calibrate_uses_latest_forecast_for_reforecast(self):
        with tempfile.TemporaryDirectory() as tmp:
            log = os.path.join(tmp, "forecasts-log.jsonl")
            # First forecast for L1 would give ratio 100/10=10, but the
            # reforecast gives 40/20=2.0. Pairing must use the LATEST one.
            self._record(log, "L1", 100, "2026-08-12T00:00:00Z")
            self._record(log, "L1", 40, "2026-08-12T00:30:00Z")
            self._actual(log, "L1", 20, "2026-08-12T01:00:00Z")
            self._record(log, "L2", 40, "2026-08-12T02:00:00Z")
            self._actual(log, "L2", 10, "2026-08-12T02:30:00Z")
            self._record(log, "L3", 60, "2026-08-12T03:00:00Z")
            self._actual(log, "L3", 10, "2026-08-12T03:30:00Z")
            result = run_cli("calibrate", "--log", log)
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            self.assertIn("median=4.00", result.stdout)


class ApplyTests(unittest.TestCase):

    def _record(self, log, task, likely, at):
        run_cli("record", "--task", task, "--clock", "agent",
                "--basis", "judged", "--likely-minutes", str(likely),
                "--at", at, "--log", log)

    def _actual(self, log, task, minutes, at):
        run_cli("actual", "--task", task, "--minutes", str(minutes), "--at", at,
                "--log", log)

    def test_apply_on_no_data_log_exits_2_with_no_estimate(self):
        with tempfile.TemporaryDirectory() as tmp:
            log = os.path.join(tmp, "forecasts-log.jsonl")
            result = run_cli("apply", "--likely-minutes", "60", "--log", log)
            self.assertEqual(result.returncode, 2, result.stdout + result.stderr)
            self.assertTrue(result.stdout.startswith("NO-DATA:"))
            self.assertNotIn("likely=", result.stdout)

    def test_apply_on_calibrated_log_prints_exact_estimate(self):
        with tempfile.TemporaryDirectory() as tmp:
            log = os.path.join(tmp, "forecasts-log.jsonl")
            self._record(log, "L1", 40, "2026-08-12T01:00:00Z")
            self._actual(log, "L1", 20, "2026-08-12T01:30:00Z")
            self._record(log, "L2", 40, "2026-08-12T02:00:00Z")
            self._actual(log, "L2", 10, "2026-08-12T02:30:00Z")
            self._record(log, "L3", 60, "2026-08-12T03:00:00Z")
            self._actual(log, "L3", 10, "2026-08-12T03:30:00Z")
            # median 4.0, min 2.0, max 6.0
            result = run_cli("apply", "--likely-minutes", "60", "--log", log)
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            self.assertIn("likely=15.0", result.stdout)  # 60/4.0
            self.assertIn("low=10.0", result.stdout)     # 60/6.0
            self.assertIn("high=30.0", result.stdout)    # 60/2.0
            self.assertIn("n=3", result.stdout)


class CheckTests(unittest.TestCase):

    def test_check_on_absent_log_is_no_data_not_pass(self):
        with tempfile.TemporaryDirectory() as tmp:
            log = os.path.join(tmp, "forecasts-log.jsonl")
            result = run_cli("check", "--log", log)
            self.assertEqual(result.returncode, 2, result.stdout + result.stderr)
            self.assertTrue(result.stdout.startswith("NO-DATA:"))

    def test_check_with_open_forecast_past_window_is_fail(self):
        with tempfile.TemporaryDirectory() as tmp:
            log = os.path.join(tmp, "forecasts-log.jsonl")
            run_cli("record", "--task", "L2", "--clock", "agent",
                    "--basis", "judged", "--likely-minutes", "35",
                    "--at", "2026-08-12T00:00:00Z", "--log", log)
            result = run_cli("check", "--max-open-hours", "12",
                             "--now", "2026-08-12T13:00:00Z", "--log", log)
            self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
            self.assertTrue(result.stdout.startswith("FAIL:"))
            self.assertIn("L2", result.stdout)

    def test_check_with_all_forecasts_closed_is_pass(self):
        with tempfile.TemporaryDirectory() as tmp:
            log = os.path.join(tmp, "forecasts-log.jsonl")
            run_cli("record", "--task", "L2", "--clock", "agent",
                    "--basis", "judged", "--likely-minutes", "35",
                    "--at", "2026-08-12T00:00:00Z", "--log", log)
            run_cli("actual", "--task", "L2", "--minutes", "22",
                    "--at", "2026-08-12T00:30:00Z", "--log", log)
            result = run_cli("check", "--max-open-hours", "12",
                             "--now", "2026-08-12T13:00:00Z", "--log", log)
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            self.assertTrue(result.stdout.startswith("PASS:"))


class MalformedLineTests(unittest.TestCase):

    def test_malformed_line_is_counted_not_a_crash(self):
        with tempfile.TemporaryDirectory() as tmp:
            log = os.path.join(tmp, "forecasts-log.jsonl")
            run_cli("record", "--task", "L2", "--clock", "agent",
                    "--basis", "judged", "--likely-minutes", "35",
                    "--at", "2026-08-12T00:00:00Z", "--log", log)
            with io.open(log, "a", encoding="utf-8") as handle:
                handle.write("not json at all {{{\n")
            result = run_cli("check", "--max-open-hours", "12",
                             "--now", "2026-08-12T00:05:00Z", "--log", log)
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            self.assertIn("malformed=1", result.stdout)

    def test_all_malformed_log_is_no_data(self):
        with tempfile.TemporaryDirectory() as tmp:
            log = os.path.join(tmp, "forecasts-log.jsonl")
            with io.open(log, "w", encoding="utf-8") as handle:
                handle.write("not json {{{\n")
                handle.write("also not json ]]]\n")
            result = run_cli("check", "--log", log)
            self.assertEqual(result.returncode, 2, result.stdout + result.stderr)
            self.assertTrue(result.stdout.startswith("NO-DATA:"))


class HelpTests(unittest.TestCase):
    """The precedent (tools/bm_project.py's _parse) recognizes --help/-h at
    every verb's flag position, never falling through to "unknown
    argument". Before this fix, bm_forecast.py refused --help on every
    verb with "unknown argument: --help" at exit 2."""

    def test_bare_help_flag_exits_0_with_usage(self):
        result = run_cli("--help")
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn("Usage:", result.stdout)

    def test_no_args_exits_0_with_usage(self):
        result = run_cli()
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn("Usage:", result.stdout)

    def test_record_help_flag_exits_0_with_usage(self):
        result = run_cli("record", "--help")
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn("Usage:", result.stdout)
        self.assertIn("--likely-minutes", result.stdout)

    def test_calibrate_help_flag_exits_0_with_usage(self):
        result = run_cli("calibrate", "-h")
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn("Usage:", result.stdout)

    def test_check_help_flag_does_not_require_log(self):
        # --help must win even though `check` normally needs --log/--root
        # to resolve anything; a NO-DATA log-resolution failure here would
        # mean --help fell through to the real command body. (The module
        # docstring itself documents the "NO-DATA:" convention, so this
        # checks the message does not START with it, rather than never
        # containing the substring.)
        result = run_cli("check", "--help")
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertFalse(result.stdout.startswith("NO-DATA:"))


if __name__ == "__main__":
    unittest.main()

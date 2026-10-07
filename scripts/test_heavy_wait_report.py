"""Tests for heavy_wait_report.py (ACC7.a).

Every fixture is written by calling heavy_slot.record_wait itself, so a change
to the line shape in scripts/heavy_slot.py breaks this suite (REQ-ACC7-A4).
Each guard has its own fixture that only that guard can refuse. The live wait
file is never touched: every test uses its own directory.
"""
import io
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from contextlib import redirect_stdout

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import heavy_slot  # noqa: E402
import heavy_wait_report as hwr  # noqa: E402

try:  # noqa: E402
    import tmp_sandbox as _tmp
    _tmp.install()
except ImportError:
    sys.stderr.write("test_heavy_wait_report: tmp_sandbox.py not found; temp trees may be left behind\n")


class Base(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="heavy-wait-report-test-")
        self.path = os.path.join(self.tmp, "waits.jsonl")

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def record(self, waits, outcome="admitted"):
        for w in waits:
            heavy_slot.record_wait(self.tmp, w, outcome, 1, 3)

    def append_raw(self, text):
        with open(self.path, "a", encoding="utf-8") as fh:
            fh.write(text)

    def verdict(self):
        return hwr.report(self.path)[0]


class TheMedianIsComputed(Base):
    def test_300_known_waits_give_the_exact_median(self):  # REQ-ACC7-A1
        self.record(range(300))
        rows, bad = hwr.read_waits(self.path)
        self.assertEqual((len(rows), bad), (300, 0))
        stats = hwr.wait_stats(rows)
        self.assertEqual(stats["median_s"], 149.5)   # even count: mean of the two middle values
        self.assertEqual(stats["n"], 300)
        self.assertEqual(stats["p90_s"], 269.0)
        self.assertEqual(stats["over_bar"], 0)
        self.assertEqual(stats["first_at"], rows[0]["at"])

    def test_odd_count_median_is_the_middle_value(self):
        self.record([5, 1, 3])
        self.assertEqual(hwr.wait_stats(hwr.read_waits(self.path)[0])["median_s"], 3.0)

    def test_order_in_the_file_does_not_matter(self):
        self.record([600, 0, 3600, 2, 1])
        self.assertEqual(hwr.wait_stats(hwr.read_waits(self.path)[0])["median_s"], 2.0)

    def test_unqueued_counts_at_its_recorded_wait_and_is_counted(self):
        self.record([0, 0])
        self.record([3600.7], outcome="unqueued")
        stats = hwr.wait_stats(hwr.read_waits(self.path)[0])
        self.assertEqual(stats["unqueued"], 1)
        self.assertEqual(stats["over_bar"], 1)
        self.assertEqual(stats["median_s"], 0.0)

    def test_duplicate_lines_are_each_an_admission(self):
        self.record([7, 7, 7])
        self.assertEqual(hwr.wait_stats(hwr.read_waits(self.path)[0])["n"], 3)

    def test_empty_rows_give_zero_stats(self):
        self.assertEqual(hwr.wait_stats([])["n"], 0)
        self.assertEqual(hwr.wait_stats([])["first_at"], "")


class TheBarIsStrictlyOver(Base):
    def stats_with_median(self, value, n=hwr.MIN_SAMPLES):
        self.record([value] * n)
        return hwr.wait_stats(hwr.read_waits(self.path)[0])

    def test_exactly_600_holds(self):  # REQ-ACC7-A3, M-ACC7A-BAR-INCLUSIVE
        stats = self.stats_with_median(600.0)
        self.assertEqual(stats["median_s"], 600.0)
        self.assertEqual(hwr.flip_verdict(stats, 0, hwr.MIN_SAMPLES, True), "HOLD")

    def test_600_point_1_flips(self):
        stats = self.stats_with_median(600.1)
        self.assertEqual(hwr.flip_verdict(stats, 0, hwr.MIN_SAMPLES, True), "FLIP")

    def test_the_3600_bound_wait_flips_when_it_is_the_median(self):
        stats = self.stats_with_median(3600.0)
        self.assertEqual(hwr.flip_verdict(stats, 0, hwr.MIN_SAMPLES, True), "FLIP")


class ThinOrDamagedDataIsNoData(Base):
    def test_199_rows_is_no_data(self):  # REQ-ACC7-A2, M-ACC7A-THIN-PASSES
        self.record([0] * (hwr.MIN_SAMPLES - 1))
        self.assertEqual(self.verdict(), "NO-DATA")

    def test_exactly_min_samples_is_an_answer(self):
        self.record([0] * hwr.MIN_SAMPLES)
        self.assertEqual(self.verdict(), "HOLD")

    def test_empty_file_is_no_data(self):
        self.append_raw("")
        self.assertEqual(self.verdict(), "NO-DATA")

    def test_one_line_is_no_data(self):
        self.record([0])
        self.assertEqual(self.verdict(), "NO-DATA")

    def test_missing_file_is_no_data_through_main(self):  # REQ-ACC7-A2, readable False
        out = io.StringIO()
        with redirect_stdout(out):
            code = hwr.main(["--path", self.path])
        self.assertEqual(code, 2)
        self.assertTrue(out.getvalue().startswith("NO-DATA"), out.getvalue())
        self.assertIn("no such file", out.getvalue())
        self.assertFalse(os.path.exists(self.path), "the report must never create the wait file")

    def test_readable_false_is_no_data_whatever_the_stats(self):
        stats = {"n": 1000, "median_s": 0.0}
        self.assertEqual(hwr.flip_verdict(stats, 0, 1000, False), "NO-DATA")

    def test_3_percent_corrupt_lines_is_no_data(self):  # REQ-ACC7-A2
        self.record([0] * 300)
        for _ in range(10):
            self.append_raw("{not json\n")
        rows, bad = hwr.read_waits(self.path)
        self.assertEqual((len(rows), bad), (300, 10))
        self.assertEqual(self.verdict(), "NO-DATA")

    def test_under_one_percent_corrupt_is_still_an_answer(self):
        self.record([0] * 300)
        self.append_raw("{not json\n")
        self.assertEqual(self.verdict(), "HOLD")

    def test_partial_last_line_is_one_unusable_line(self):
        self.record([0, 0])
        self.append_raw('{"at": "2026-09-28T13:02:11+0900", "of": 3, "outc')
        rows, bad = hwr.read_waits(self.path)
        self.assertEqual((len(rows), bad), (2, 1))

    def test_each_field_rule_refuses_its_own_bad_line(self):
        good = {"at": "2026-09-28T13:02:11+0900", "of": 3, "outcome": "admitted", "slots": 1, "waited_s": 0.0}
        bad_rows = [
            dict(good, waited_s=-1.0),
            dict(good, waited_s=float("inf")),
            dict(good, waited_s=float("nan")),
            dict(good, waited_s=True),
            dict(good, waited_s="0"),
            dict(good, outcome="skipped"),
            dict(good, at=5),
            dict(good, slots="1"),
            dict(good, of=1.5),
            dict(good, of=True),
            [1, 2, 3],
        ]
        for row in bad_rows:
            with open(self.path, "w", encoding="utf-8") as fh:
                fh.write(json.dumps(row) + "\n")
            rows, bad = hwr.read_waits(self.path)
            self.assertEqual((len(rows), bad), (0, 1), "accepted: %r" % (row,))
        del good["slots"]
        with open(self.path, "w", encoding="utf-8") as fh:
            fh.write(json.dumps(good) + "\n")
        self.assertEqual(hwr.read_waits(self.path), ([], 1))


class TheReportNeverWritesTheFile(Base):
    def test_main_leaves_the_file_byte_identical(self):
        self.record([0] * hwr.MIN_SAMPLES)
        self.append_raw("{partial")
        with open(self.path, "rb") as fh:
            before = fh.read()
        out = io.StringIO()
        with redirect_stdout(out):
            code = hwr.main(["--path", self.path])
        with open(self.path, "rb") as fh:
            self.assertEqual(fh.read(), before)
        self.assertEqual(code, 0)
        text = out.getvalue()
        self.assertTrue(text.startswith("HOLD: median heavy_slot wait 0.0 s"), text)
        self.assertIn("%d lines, %d usable, 1 unusable" % (hwr.MIN_SAMPLES + 1, hwr.MIN_SAMPLES), text)
        self.assertIn("bypassed the queue", text)   # the completeness limit is stated in the output

    def test_the_entry_point_exit_codes(self):
        self.record([601] * hwr.MIN_SAMPLES)
        proc = subprocess.run([sys.executable, "-B", os.path.join(HERE, "heavy_wait_report.py"), "--path", self.path],
                              stdout=subprocess.PIPE, stderr=subprocess.PIPE, universal_newlines=True)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertTrue(proc.stdout.startswith("FLIP: median heavy_slot wait 601.0 s"), proc.stdout)
        proc = subprocess.run([sys.executable, "-B", os.path.join(HERE, "heavy_wait_report.py"), "--path",
                               os.path.join(self.tmp, "absent.jsonl")],
                              stdout=subprocess.PIPE, stderr=subprocess.PIPE, universal_newlines=True)
        self.assertEqual(proc.returncode, 2, proc.stdout + proc.stderr)
        self.assertTrue(proc.stdout.startswith("NO-DATA"), proc.stdout)

    def test_default_path_is_the_slot_directory(self):
        env = {"BROTHER_SLOT_DIR": self.tmp}
        self.assertEqual(hwr.default_path(env), self.path)


if __name__ == "__main__":
    unittest.main()

"""ruling_ledger.py: a founder ruling must never sit APPLIED on the board
without something on disk that actually landed it. (row M7, 2026-09-07
reflection: attempt-hook-registration-2026-09-03's ruling was recorded, the
settings.json edit that applied it was quietly reverted two days later, and
the roadmap kept reading REGISTERED the whole time because nothing joined the
ruling to a landing.)

Fixtures are written to a fresh temp directory per test so this file never
depends on how many real decisions carry rulings today, and `now` is always
passed explicitly so age math never depends on the wall clock.
"""
import datetime
import json
import os
import shutil
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import board_status as BS  # noqa: E402
import ruling_ledger as RL  # noqa: E402

JST = RL.JST
RULED_AT = "A, the founder 2026-09-01 at 10:00 in the question UI: \"X\"."
RULED_AT_DT = datetime.datetime(2026, 9, 1, 10, 0, tzinfo=JST)


def write_record(dir_path, name, ruling, landing=None, title="A decision"):
    data = {"title": title, "ruling": ruling}
    if landing is not None:
        data["landing"] = landing
    path = os.path.join(dir_path, name + ".json")
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(data, fh)
    return path


class NoRecordsIsNoData(unittest.TestCase):
    def test_an_empty_directory_reports_no_data_and_exits_0(self):
        empty = tempfile.mkdtemp()
        try:
            lines, summary, code = RL.evaluate(decisions_dir=empty)
            self.assertTrue(summary.get("nodata"))
            self.assertEqual(code, 0)
            self.assertTrue(any(RL.NODATA in l for l in lines))
        finally:
            shutil.rmtree(empty)

    def test_a_directory_with_no_ruling_at_all_is_zero_not_no_data(self):
        d = tempfile.mkdtemp()
        try:
            write_record(d, "unruled", None)
            write_record(d, "pending", "PENDING")
            lines, summary, code = RL.evaluate(decisions_dir=d, roadmap_path="/no/such.json")
            self.assertFalse(summary.get("nodata"))
            self.assertEqual(summary["applied"], 0)
            self.assertEqual(summary["unapplied"], 0)
            self.assertEqual(code, 0)
            self.assertEqual(lines, [])
        finally:
            shutil.rmtree(d)


class IsRealRuling(unittest.TestCase):
    def test_null_and_empty_and_pending_are_not_rulings(self):
        for val in (None, "", "  ", "PENDING", "pending", "Pending"):
            self.assertFalse(RL.is_real_ruling(val), repr(val))

    def test_an_actual_ruling_is_real(self):
        self.assertTrue(RL.is_real_ruling(RULED_AT))
        self.assertTrue(RL.is_real_ruling("DELIVERY_TRAIN, the founder said so"))


class ParseRulingTimestamp(unittest.TestCase):
    def test_an_exact_time_parses(self):
        self.assertEqual(RL.parse_ruling_timestamp(RULED_AT), RULED_AT_DT)

    def test_a_placeholder_minute_digit_rounds_down(self):
        got = RL.parse_ruling_timestamp("A, the founder 2026-09-06 at about 19:0x JST: \"A\".")
        self.assertEqual(got, datetime.datetime(2026, 9, 6, 19, 0, tzinfo=JST))
        got2 = RL.parse_ruling_timestamp("Founder, 2026-09-05 17:5x JST, in the question UI: \"A\"")
        self.assertEqual(got2, datetime.datetime(2026, 9, 5, 17, 50, tzinfo=JST))

    def test_no_date_at_all_returns_none(self):
        self.assertIsNone(RL.parse_ruling_timestamp("PENDING"))
        self.assertIsNone(RL.parse_ruling_timestamp(None))


class Evaluate(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp()

    def tearDown(self):
        shutil.rmtree(self.dir)

    def test_a_recorded_landing_reads_applied(self):
        write_record(self.dir, "applied", RULED_AT, landing="PR 999")
        lines, summary, code = RL.evaluate(decisions_dir=self.dir, roadmap_path="/no/such.json",
                                            repo_root=self.dir,
                                            now=RULED_AT_DT + datetime.timedelta(hours=5))
        self.assertEqual(summary["applied"], 1)
        self.assertEqual(summary["unapplied"], 0)
        self.assertEqual(code, 0)
        self.assertTrue(any(l.startswith("APPLIED  applied") and "PR 999" in l for l in lines))

    def test_unapplied_under_the_age_bar_does_not_fail(self):
        write_record(self.dir, "fresh", RULED_AT)
        lines, summary, code = RL.evaluate(decisions_dir=self.dir, roadmap_path="/no/such.json",
                                            repo_root=self.dir,
                                            now=RULED_AT_DT + datetime.timedelta(hours=5),
                                            max_age_hours=24.0)
        self.assertEqual(summary["unapplied"], 1)
        self.assertEqual(code, 0)
        self.assertTrue(any(l.startswith("RULING UNAPPLIED  fresh") for l in lines))

    def test_unapplied_over_the_age_bar_exits_1(self):
        write_record(self.dir, "stale", RULED_AT)
        lines, summary, code = RL.evaluate(decisions_dir=self.dir, roadmap_path="/no/such.json",
                                            repo_root=self.dir,
                                            now=RULED_AT_DT + datetime.timedelta(hours=48),
                                            max_age_hours=24.0)
        self.assertEqual(summary["unapplied"], 1)
        self.assertEqual(code, 1)
        self.assertEqual(len(summary["stale"]), 1)
        self.assertEqual(summary["stale"][0][0], "stale")
        self.assertTrue(any("48.0 hours" in l for l in lines))

    def test_a_pending_ruling_is_never_counted_either_way(self):
        write_record(self.dir, "pending", "PENDING")
        lines, summary, code = RL.evaluate(decisions_dir=self.dir, roadmap_path="/no/such.json",
                                            repo_root=self.dir, now=RULED_AT_DT)
        self.assertEqual(summary["applied"], 0)
        self.assertEqual(summary["unapplied"], 0)
        self.assertEqual(code, 0)
        self.assertEqual(lines, [])

    def test_mixed_applied_and_unapplied_summary_line_names_the_unapplied_one(self):
        write_record(self.dir, "widget", RULED_AT, landing="PR 1")
        write_record(self.dir, "stale", RULED_AT)
        _lines, summary, code = RL.evaluate(decisions_dir=self.dir, roadmap_path="/no/such.json",
                                             repo_root=self.dir,
                                             now=RULED_AT_DT + datetime.timedelta(hours=48),
                                             max_age_hours=24.0)
        line = RL.format_summary_line(summary)
        self.assertTrue(line.startswith("Rulings: 1 applied, 1 unapplied (oldest 48 hours)"))
        self.assertIn("stale", line)
        self.assertNotIn("widget", line)  # named in a line above, not in the unapplied summary
        self.assertEqual(code, 1)


class RoadmapLanding(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp()
        self.roadmap = os.path.join(self.dir, "roadmap.json")

    def tearDown(self):
        shutil.rmtree(self.dir)

    def _write_roadmap(self, doc):
        with open(self.roadmap, "w", encoding="utf-8") as fh:
            json.dump(doc, fh)

    def test_a_done_row_naming_the_record_counts_as_a_landing(self):
        write_record(self.dir, "thing", RULED_AT, title="Thing decision")
        self._write_roadmap({"rows": [{"id": "R9", "status": "DONE",
                                        "evidence": "closed per decision thing-2026 ruling"}]})
        _lines, summary, code = RL.evaluate(decisions_dir=self.dir, roadmap_path=self.roadmap,
                                             now=RULED_AT_DT, repo_root=self.dir)
        self.assertEqual(summary["applied"], 1)
        self.assertEqual(code, 0)

    def test_a_not_built_row_naming_the_record_is_only_a_mention_not_a_landing(self):
        write_record(self.dir, "thing", RULED_AT, title="Thing decision")
        self._write_roadmap({"rows": [{"id": "R9", "status": "NOT BUILT",
                                        "evidence": "founder ruling thing-2026, not yet applied"}]})
        _lines, summary, code = RL.evaluate(decisions_dir=self.dir, roadmap_path=self.roadmap,
                                             now=RULED_AT_DT, repo_root=self.dir,
                                             max_age_hours=0.0)
        self.assertEqual(summary["applied"], 0)
        self.assertEqual(summary["unapplied"], 1)


class BoardStatusIntegration(unittest.TestCase):
    """The line board_status.py actually prints, the way test_founder_queue.py
    proves the founder-queue line by pointing the module constant at a
    fixture rather than the real repository state."""

    def test_the_status_line_renders_from_a_fixture_directory(self):
        d = tempfile.mkdtemp()
        try:
            write_record(d, "applied", RULED_AT, landing="PR 1")
            write_record(d, "open", RULED_AT)
            old_decisions, old_roadmap, old_root = RL.DECISIONS_DIR, RL.ROADMAP_PATH, RL.ROOT
            try:
                RL.DECISIONS_DIR = d
                RL.ROADMAP_PATH = "/no/such/roadmap.json"
                RL.ROOT = d  # no .git here, so the commit-message fallback safely finds nothing
                line = BS.ruling_ledger_status_line()
            finally:
                RL.DECISIONS_DIR = old_decisions
                RL.ROADMAP_PATH = old_roadmap
                RL.ROOT = old_root
            self.assertTrue(line.startswith("Rulings: 1 applied, 1 unapplied"))
            self.assertIn("open", line)
        finally:
            shutil.rmtree(d)

    def test_the_real_board_status_run_prints_a_rulings_line(self):
        """Against the real repository state, not a fixture: just proves the
        line is wired into main()'s output at all."""
        import subprocess
        script = os.path.join(os.path.dirname(os.path.abspath(__file__)), "board_status.py")
        proc = subprocess.run([sys.executable, script], stdout=subprocess.PIPE,
                               stderr=subprocess.PIPE, text=True, timeout=120)
        self.assertIn("Rulings:", proc.stdout)


if __name__ == "__main__":
    unittest.main()

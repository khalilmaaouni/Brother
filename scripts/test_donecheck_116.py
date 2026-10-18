#!/usr/bin/env python3
"""GM16.a: the 1.1.6 night's G1 reading (docs/plan/specs/1.1.6/G1-116.txt) is complete and honest, and donecheck_115 reads
the 1.1.6 plan through donecheck_114's own table: NO-DATA with no ledger (never zero, never PASS), a corrupt results.json
is NO-DATA, a clean landing of a 1.1.6 sub unit passes. Fixtures live in temporary directories; the runs folder layout is
the one test_donecheck_115 uses. The real-record and plan cases skip with a named reason where docs/plan is not shipped."""
import contextlib
import io
import json
import os
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, HERE)
import donecheck_114 as D14  # noqa: E402
import donecheck_115 as D  # noqa: E402

PLAN = os.path.join(ROOT, "docs", "plan", "BROTHER-1.1.6-WBS.json")
READING = os.path.join(ROOT, "docs", "plan", "specs", "1.1.6", "G1-116.txt")
VERDICTS = ("PASS", "FAIL", "NO-DATA")
REQUIRED = ["landed_share", "seat_waits", "timeouts", "plan_defect_refusals", "bare_refusals", "one_next_action",
            "status_read_only", "refusals_name_remedy", "one_start_command", "loop_vs_builder_landings",
            "usd_per_landing", "notify_request_rows", "notify_relay_times", "boot_identity", "phase_timings_launch",
            "phase_timings_run", "phase_timings_close"]


def reading_lines(text: str) -> dict:
    """{bar name: (verdict, figure)} from the 'bar | <name> | <verdict> | <figure or reason>' lines of a reading; any
    other line is ignored. A bar written twice reads as verdict 'DUPLICATE' (refused by reading_problems). Not text: {}."""
    out = {}
    for line in text.splitlines() if isinstance(text, str) else []:
        parts = [p.strip() for p in line.split("|", 3)]
        if len(parts) < 3 or parts[0] != "bar" or not parts[1]:
            continue
        out[parts[1]] = ("DUPLICATE", "") if parts[1] in out else (parts[2], parts[3] if len(parts) > 3 else "")
    return out


def reading_problems(lines: dict, required: list) -> list:
    """What is wrong with a reading: a required bar with no line, a verdict outside PASS, FAIL, NO-DATA, a PASS with no
    figure, a NO-DATA or FAIL with no reason. A bar the required list does not name is ignored. Unreadable input: a problem."""
    if not isinstance(lines, dict) or not isinstance(required, list):
        return ["the reading or the required list is not readable"]
    out = []
    for bar in required:
        if bar not in lines:
            out.append("%s: no line" % bar)
            continue
        verdict, figure = lines[bar]
        if verdict not in VERDICTS:
            out.append("%s: verdict %r is not PASS, FAIL or NO-DATA" % (bar, verdict))
        elif not figure:
            out.append("%s: %s with no %s" % (bar, verdict, "figure" if verdict == "PASS" else "reason"))
    return out


def run_dir(runs, name, rows, status, tag=None):
    d = os.path.join(runs, name)
    os.makedirs(d)
    with open(os.path.join(d, "results.json"), "w", encoding="utf-8") as fh:
        if isinstance(rows, str):
            fh.write(rows)
        else:
            json.dump(rows, fh)
    with open(os.path.join(d, "STATUS"), "w", encoding="utf-8") as fh:
        fh.write(status)
    if tag:
        with open(os.path.join(d, "RUN-TAG"), "w", encoding="utf-8") as fh:
            fh.write(tag)
    return d


def ok_row():
    return {"id": "x", "ok": True, "error": "", "seat_wait": False}


def table_of(runs, since=None):
    """The G1 rows for the real 1.1.6 plan through donecheck_114's own table, without re-running the tree's tests."""
    with open(PLAN, encoding="utf-8") as fh:
        plan = json.load(fh)
    rows = D14.g1_table(plan, runs, run_tree=False, since=since)
    return {r[0]: r[2] for r in rows}, D14.exit_code(rows)


GOOD = "\n".join("bar | %s | PASS | 1" % b for b in REQUIRED)


class TestReadingParser(unittest.TestCase):
    def test_a_complete_reading_has_no_problem(self) -> None:
        self.assertEqual(reading_problems(reading_lines(GOOD), REQUIRED), [])

    def test_a_pass_needs_a_figure(self) -> None:
        bare = GOOD.replace("bar | seat_waits | PASS | 1", "bar | seat_waits | PASS |")
        self.assertEqual(reading_problems(reading_lines(bare), REQUIRED), ["seat_waits: PASS with no figure"])
        nodata = GOOD.replace("bar | seat_waits | PASS | 1", "bar | seat_waits | NO-DATA |")
        self.assertEqual(reading_problems(reading_lines(nodata), REQUIRED), ["seat_waits: NO-DATA with no reason"])

    def test_a_missing_bar_is_named(self) -> None:
        short = "\n".join(l for l in GOOD.splitlines() if "| timeouts |" not in l)
        self.assertEqual(reading_problems(reading_lines(short), REQUIRED), ["timeouts: no line"])

    def test_a_verdict_outside_the_three_is_refused(self) -> None:
        for word in ("OK", "pass", "PARKED", ""):
            bad = GOOD.replace("bar | seat_waits | PASS | 1", "bar | seat_waits | %s | 1" % word)
            self.assertEqual(len(reading_problems(reading_lines(bad), REQUIRED)), 1, word)

    def test_a_bar_written_twice_is_refused_and_an_unknown_bar_is_ignored(self) -> None:
        twice = GOOD + "\nbar | seat_waits | FAIL | 3"
        self.assertEqual(len(reading_problems(reading_lines(twice), REQUIRED)), 1)
        self.assertEqual(reading_problems(reading_lines(GOOD + "\nbar | not_a_bar | PASS | 1"), REQUIRED), [])

    def test_a_figure_may_hold_the_separator_and_the_same_text_reads_the_same_twice(self) -> None:
        text = GOOD.replace("bar | seat_waits | PASS | 1", "bar | seat_waits | PASS | 0 | runs /x")
        self.assertEqual(reading_lines(text)["seat_waits"], ("PASS", "0 | runs /x"))
        self.assertEqual(reading_lines(text), reading_lines(text))

    def test_hostile_inputs_refuse_and_never_pass(self) -> None:
        for bad in (None, "", 7, "/etc/passwd", b"bar | x | PASS | 1"):
            lines = reading_lines(bad)
            self.assertEqual(lines, {})
            self.assertEqual(len(reading_problems(lines, REQUIRED)), len(REQUIRED))
        self.assertEqual(len(reading_problems(None, REQUIRED)), 1)
        self.assertEqual(len(reading_problems({}, None)), 1)


class TestRealReading(unittest.TestCase):
    @unittest.skipUnless(os.path.isfile(READING), "docs/plan/specs/1.1.6/G1-116.txt is not shipped (docs/plan is hub only)")
    def test_the_real_reading_is_complete(self) -> None:
        with open(READING, encoding="utf-8") as fh:
            self.assertEqual(reading_problems(reading_lines(fh.read()), REQUIRED), [])


class TestDonecheck116(unittest.TestCase):
    def setUp(self):
        self._td = tempfile.TemporaryDirectory()
        self.runs = os.path.join(self._td.name, "runs")
        os.makedirs(self.runs)

    def tearDown(self):
        self._td.cleanup()

    def test_a_callers_1_1_6_plan_wins_over_the_1_1_5_default(self) -> None:
        a = D.argv_for(["--goal", "G1", "--plan", PLAN])
        self.assertEqual(a.count("--plan"), 1)
        self.assertEqual(a[a.index("--plan") + 1], PLAN)

    def test_a_plan_that_is_not_there_reads_no_data_at_the_entry_point(self) -> None:
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            rc = D.main(["--goal", "G1", "--plan", os.path.join(self._td.name, "absent.json"), "--runs", self.runs])
        self.assertEqual(rc, 2, out.getvalue())
        self.assertIn("NO-DATA", out.getvalue())

    @unittest.skipUnless(os.path.isfile(PLAN), "docs/plan/BROTHER-1.1.6-WBS.json is not shipped (docs/plan is hub only)")
    def test_no_ledger_is_no_data(self) -> None:
        t, rc = table_of(os.path.join(self._td.name, "absent"))
        self.assertEqual(rc, 2, t)
        for bar in ("landed_share", "seat_waits", "timeouts", "plan_defect_refusals", "bare_refusals"):
            self.assertEqual(t.get(bar), "NO-DATA", t)

    @unittest.skipUnless(os.path.isfile(PLAN), "docs/plan/BROTHER-1.1.6-WBS.json is not shipped (docs/plan is hub only)")
    def test_an_empty_runs_folder_is_no_data_never_pass(self) -> None:
        t, rc = table_of(self.runs)
        self.assertEqual(rc, 2, t)
        self.assertEqual(t.get("landed_share"), "NO-DATA", t)

    @unittest.skipUnless(os.path.isfile(PLAN), "docs/plan/BROTHER-1.1.6-WBS.json is not shipped (docs/plan is hub only)")
    def test_a_results_file_that_does_not_parse_is_no_data(self) -> None:
        run_dir(self.runs, "PH1.a-010101", "{not json", "LANDED x")
        t, _rc = table_of(self.runs)
        for bar in ("landed_share", "seat_waits", "timeouts"):
            self.assertEqual(t.get(bar), "NO-DATA", t)

    @unittest.skipUnless(os.path.isfile(PLAN), "docs/plan/BROTHER-1.1.6-WBS.json is not shipped (docs/plan is hub only)")
    def test_a_clean_landing_of_a_1_1_6_sub_unit_passes_the_loop_bars(self) -> None:
        run_dir(self.runs, "PH1.a-010101", [ok_row()], "LANDED x")   # PH1.a: recorded landed in the 1.1.6 plan's evidence
        t, _rc = table_of(self.runs)
        for bar in ("landed_share", "seat_waits", "timeouts", "plan_defect_refusals", "bare_refusals"):
            self.assertEqual(t.get(bar), "PASS", t)

    @unittest.skipUnless(os.path.isfile(PLAN), "docs/plan/BROTHER-1.1.6-WBS.json is not shipped (docs/plan is hub only)")
    def test_a_bare_refusal_of_a_1_1_6_sub_unit_is_red(self) -> None:
        run_dir(self.runs, "C2.a-010101", [ok_row()], "WITHHELD round 0: the grader said nothing")
        t, rc = table_of(self.runs)
        self.assertNotEqual(rc, 0, t)
        self.assertEqual(t.get("bare_refusals"), "FAIL", t)

    @unittest.skipUnless(os.path.isfile(PLAN), "docs/plan/BROTHER-1.1.6-WBS.json is not shipped (docs/plan is hub only)")
    def test_a_run_before_the_launch_is_filtered_by_since(self) -> None:
        run_dir(self.runs, "PH1.a-010101", [ok_row()], "LANDED x", tag="PH1.a-010101@1000")
        t, _rc = table_of(self.runs, since=2000)
        self.assertEqual(t.get("landed_share"), "NO-DATA", t)


if __name__ == "__main__":
    unittest.main()

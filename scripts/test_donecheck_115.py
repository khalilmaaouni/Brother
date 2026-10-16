#!/usr/bin/env python3
"""GM15.a: donecheck_115 reads the G1 bars against the 1.1.5 plan through donecheck_114's own table: red on a bare
refusal, PASS on a clean landing, NO-DATA with no ledger (never zero), and a caller's own --plan always wins.
Fixtures live in temporary directories only; the runs folder layout is the one test_donecheck_114_loop uses."""
import contextlib
import io
import json
import os
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import donecheck_114 as D14  # noqa: E402
import donecheck_115 as D  # noqa: E402


def run_dir(runs, name, rows, status):
    d = os.path.join(runs, name)
    os.makedirs(d)
    with open(os.path.join(d, "results.json"), "w", encoding="utf-8") as fh:
        json.dump(rows, fh)
    with open(os.path.join(d, "STATUS"), "w", encoding="utf-8") as fh:
        fh.write(status)
    return d


def ok_row():
    return {"id": "x", "ok": True, "error": "", "seat_wait": False}


def table_of(runs):
    """The G1 rows for the real 1.1.5 plan through donecheck_114's own table, without re-running the tree's tests."""
    with open(D.WBS, encoding="utf-8") as fh:
        plan = json.load(fh)
    rows = D14.g1_table(plan, runs, run_tree=False)
    return {r[0]: r[2] for r in rows}, D14.exit_code(rows)


class TestGM15A(unittest.TestCase):
    def setUp(self):
        self._td = tempfile.TemporaryDirectory()
        self.runs = os.path.join(self._td.name, "runs")
        os.makedirs(self.runs)

    def tearDown(self):
        self._td.cleanup()

    def test_the_default_plan_is_the_1_1_5_plan_and_a_callers_plan_wins(self) -> None:
        a = D.argv_for(["--goal", "G1"])
        self.assertEqual(a[a.index("--plan") + 1], D.WBS)
        self.assertTrue(D.WBS.endswith(os.path.join("docs", "plan", "BROTHER-1.1.5-WBS.json")))
        self.assertEqual(a[a.index("--version") + 1], "1.1.5")
        mine = D.argv_for(["--goal", "G1", "--plan", "/x.json", "--version=9"])
        self.assertEqual(mine.count("--plan"), 1)
        self.assertNotIn("--version", mine)

    def test_an_unreadable_plan_reads_no_data_at_the_entry_point(self) -> None:
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            rc = D.main(["--goal", "G1", "--plan", os.path.join(self._td.name, "absent.json"), "--runs", self.runs])
        self.assertEqual(rc, 2, out.getvalue())
        self.assertIn("NO-DATA", out.getvalue())

    @unittest.skipUnless(os.path.isfile(D.WBS), "docs/plan is hub only")
    def test_no_ledger_reads_no_data_never_zero(self) -> None:
        t, rc = table_of(os.path.join(self._td.name, "absent"))
        self.assertEqual(rc, 2, t)
        for bar in ("landed_share", "seat_waits", "timeouts"):
            self.assertEqual(t.get(bar), "NO-DATA", t)

    @unittest.skipUnless(os.path.isfile(D.WBS), "docs/plan is hub only")
    def test_a_clean_landing_of_a_1_1_5_sub_unit_passes_the_loop_bars(self) -> None:
        run_dir(self.runs, "G10.a-010101", [ok_row()], "LANDED x")   # G10.a: recorded landed in the 1.1.5 plan's evidence
        t, _rc = table_of(self.runs)
        for bar in ("landed_share", "seat_waits", "timeouts", "plan_defect_refusals", "bare_refusals"):
            self.assertEqual(t.get(bar), "PASS", t)

    @unittest.skipUnless(os.path.isfile(D.WBS), "docs/plan is hub only")
    def test_a_bare_refusal_of_a_1_1_5_sub_unit_is_red(self) -> None:
        run_dir(self.runs, "X50.a-010101", [ok_row()], "WITHHELD round 0: the grader said nothing")
        t, rc = table_of(self.runs)
        self.assertNotEqual(rc, 0, t)   # 2 while the 1.1.4 only bar one_next_action reads NO-DATA for this plan
        self.assertEqual(t.get("bare_refusals"), "FAIL", t)

    @unittest.skipUnless(os.path.isfile(D.WBS), "docs/plan is hub only")
    def test_a_sub_unit_of_another_plan_is_not_counted(self) -> None:
        run_dir(self.runs, "ZZ9.a-010101", [ok_row()], "WITHHELD round 0: the grader said nothing")
        t, _rc = table_of(self.runs)
        self.assertNotEqual(t.get("bare_refusals"), "FAIL", t)


if __name__ == "__main__":
    unittest.main()

#!/usr/bin/env python3
"""GM1.a: G1 measured from the loop's own ledger; a missing or unreadable ledger is NO-DATA, never zero."""
import contextlib
import io
import json
import os
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import donecheck_114 as D  # noqa: E402

PLAN = {"units": [
    {"id": "AA1", "state": "OPEN", "sub_units": ["AA1.a", "AA1.b"], "evidence": "AA1.a landed 2026-10-14: ok."},
    {"id": "BB1", "state": "OPEN", "sub_units": ["BB1.a"], "evidence": ""},
    {"id": "RD2", "state": "DEFERRED", "sub_units": ["RD2.a"], "evidence": ""}]}
REMEDIED = "WITHHELD round 0: NO-DATA, the grade judged nothing; remedy: read this run folder's STATUS and logs"


class World:
    def __init__(self, root):
        self.root = root
        self.runs = os.path.join(root, "runs")
        os.makedirs(self.runs)

    def run(self, name, rows=None, status=None, lane=None, raw=None, tag=None):
        d = os.path.join(self.runs, name)
        os.makedirs(os.path.join(d, "round0"), exist_ok=True)
        if tag is not None:
            with open(os.path.join(d, "RUN-TAG"), "w") as fh:
                fh.write(tag + "\n")
        if raw is not None or rows is not None:
            with open(os.path.join(d, "round0", "results.json"), "w") as fh:
                fh.write(raw if raw is not None else json.dumps(rows))
        if status is not None:
            with open(os.path.join(d, "STATUS"), "w") as fh:
                fh.write(status + "\n")
        if lane is not None:
            with open(os.path.join(d, "lane.log"), "w") as fh:
                fh.write(lane)
        return d


def row(ok=True, error="", seat=False):
    return {"id": "x", "ok": ok, "error": error, "seat_wait": seat}


class TestGM1A(unittest.TestCase):
    def setUp(self):
        self._td = tempfile.TemporaryDirectory()
        self.w = World(self._td.name)

    def tearDown(self):
        self._td.cleanup()

    def table(self):
        return {r[0]: r for r in D.g1_table(PLAN, self.w.runs, run_tree=False)}

    def test_attempts_are_counted_from_the_plan_sub_units_only(self) -> None:
        self.w.run("AA1.a-010101", [row(), row(False, "NO-DATA TimeoutExpired"), row(False, "x", seat=True)], "LANDED x")
        self.w.run("ZZ9.a-010101", [row(False, "TimeoutExpired")] * 5, "WITHHELD")
        self.w.run("AA1.a-notdigits", [row(False, "TimeoutExpired")])
        att = D.loop_attempts(self.w.runs, D.plan_subs(PLAN))
        self.assertEqual((att["attempts"], att["built"], att["seat_waits"], att["timeouts"]), (3, 1, 1, 1))
        self.assertEqual(att["admitted"], ["AA1.a"])
        self.assertEqual(att["status"], {"LANDED": 1})
        self.w.run("BB1.a-020202", [row(False, "SEAT_WAIT no native seat free")], "ODD thing")
        att = D.loop_attempts(self.w.runs, D.plan_subs(PLAN))
        self.assertEqual(att["seat_waits"], 2)
        self.assertEqual(att["status"].get("unknown:ODD"), 1)

    def test_an_unreadable_results_file_is_no_data_not_zero(self) -> None:
        self.w.run("AA1.a-010101", raw="[{\"ok\": tr")
        t = self.table()
        for bar in ("landed_share", "seat_waits", "timeouts"):
            self.assertEqual(t[bar][2], "NO-DATA", t[bar])
        self.assertIn("results.json", t["seat_waits"][3])
        empty = {r[0]: r for r in D.g1_table(PLAN, os.path.join(self.w.root, "absent"), run_tree=False)}
        self.assertTrue(all(empty[b][2] == "NO-DATA" for b in D.G1_BARS), empty)

    def test_landed_share_reads_sub_landed_and_is_no_data_when_empty(self) -> None:
        self.assertIsNone(D.landed_share(PLAN, []))
        self.assertEqual(D.landed_share(PLAN, ["AA1.a"]), 1.0)
        self.assertEqual(D.landed_share(PLAN, ["AA1.a", "AA1.b", "BB1.a"]), 1.0 / 3)
        self.w.run("AA1.a-010101", [row()], "LANDED x")
        self.w.run("BB1.a-010101", [row()], REMEDIED)
        t = self.table()
        self.assertEqual((t["landed_share"][1], t["landed_share"][2]), (0.5, "FAIL"))

    def test_a_protected_path_refusal_is_a_plan_defect(self) -> None:
        self.w.run("AA1.a-010101", [row()], "QUARANTINE the build wrote scripts/check_all.sh; remedy: fix the spec")
        self.w.run("BB1.a-010101", [row()], "WITHHELD round 1: no file changed; remedy: fix the spec")
        self.w.run("AA1.b-010101", [row()], "LANDED fine")
        d = D.plan_defect_refusals(self.w.runs, D.plan_subs(PLAN))
        self.assertEqual(len(d), 2, d)
        self.assertEqual(self.table()["plan_defect_refusals"][2], "FAIL")

    def test_a_refusal_without_cause_and_remedy_is_bare(self) -> None:
        self.w.run("AA1.a-010101", [row()], REMEDIED, lane="WITHHELD round 0: the grader said nothing\nok line\n")
        self.w.run("BB1.a-010101", [row()], "BLOCKED", lane="REFUSED: x; remedy: y\n")
        b = D.bare_refusals(self.w.runs, D.plan_subs(PLAN))
        self.assertEqual(sorted(b), ["BLOCKED", "WITHHELD round 0: the grader said nothing"])
        self.assertEqual(self.table()["bare_refusals"][1], 2)

    def test_one_selection_reaches_every_counter(self) -> None:
        self.w.run("AA1.a-010101", [row(False, "TimeoutExpired")], "WITHHELD bare old refusal", tag="AA1.a-010101@1000")
        self.w.run("BB1.a-020202", [row(False, "x", seat=True)], "WITHHELD round 1: no file changed; remedy: fix the spec",
                   tag="BB1.a-020202@1000")
        self.w.run("AA1.a-030303", [row()], "LANDED x", tag="AA1.a-030303@2000")
        self.w.run("BB1.a-040404", [row()], "LANDED y")
        subs = D.plan_subs(PLAN)
        self.assertEqual(D.tag_epoch("AA1.a-010101@1000"), 1000)
        self.assertIsNone(D.tag_epoch("AA1.a-010101"))
        late = D.loop_attempts(self.w.runs, subs, since=2000)
        self.assertEqual((late["attempts"], late["timeouts"], late["seat_waits"]), (1, 0, 0))
        self.assertEqual(D.bare_refusals(self.w.runs, subs, since=2000), [])
        self.assertEqual(D.plan_defect_refusals(self.w.runs, subs, since=2000), [])
        self.assertEqual(len(D.bare_refusals(self.w.runs, subs)), 1)
        self.assertEqual(len(D.plan_defect_refusals(self.w.runs, subs)), 1)
        self.assertEqual(len(D.bare_refusals(self.w.runs, subs, run_tag="AA1.a-010101@1000")), 1)
        self.assertEqual(D.plan_defect_refusals(self.w.runs, subs, run_tag="AA1.a-010101@1000"), [])
        t = {r[0]: r for r in D.g1_table(PLAN, self.w.runs, run_tree=False, since=2000)}
        self.assertEqual((t["bare_refusals"][1], t["plan_defect_refusals"][2], t["timeouts"][1]), (0, "PASS", 0))
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            plan_path = os.path.join(self.w.root, "plan.json")
            with open(plan_path, "w") as fh:
                json.dump(PLAN, fh)
            D.main(["--goal", "G1", "--plan", plan_path, "--runs", self.w.runs, "--since", "2000"])
        self.assertIn("since 2000", out.getvalue())

    def test_the_exit_follows_the_worst_verdict_and_parked_is_neutral(self) -> None:
        self.w.run("AA1.a-010101", [row()], "LANDED x")
        t = self.table()
        self.assertEqual(t["one_next_action"][2], "PARKED")
        rows = list(t.values())
        self.assertEqual(D.exit_code(rows), 0, rows)
        self.assertEqual(D.exit_code(rows + [("x", 1, "FAIL", "")]), 1)
        self.assertEqual(D.exit_code(rows + [("x", 1, "FAIL", ""), ("y", None, "NO-DATA", "")]), 2)
        self.assertEqual(D.exit_code([("one_next_action", None, "PARKED", "")]), 2)
        plan = dict(PLAN, units=[dict(u, state="OPEN") if u["id"] == "RD2" else u for u in PLAN["units"]])
        self.assertEqual({r[0]: r for r in D.g1_table(plan, self.w.runs, run_tree=False)}["one_next_action"][2], "NO-DATA")

    def test_the_command_prints_the_table_and_the_one_start_command_row(self) -> None:
        self.w.run("AA1.a-010101", [row()], "LANDED x")
        plan_path = os.path.join(self.w.root, "plan.json")
        with open(plan_path, "w") as fh:
            json.dump(PLAN, fh)
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            rc = D.main(["--goal", "G1", "--plan", plan_path, "--runs", self.w.runs])
        self.assertIn("landed_share", out.getvalue())
        self.assertIn(rc, (0, 1, 2))
        rb = os.path.join(self.w.root, "rb")
        os.makedirs(os.path.join(rb, "docs", "plan", "specs"))
        with open(os.path.join(rb, D.RUNBOOK), "w") as fh:
            fh.write("run `scripts/loop/start_run.sh --dry`\n")
        self.assertEqual(D.one_start_command(rb)[2], "PASS")
        with open(os.path.join(rb, D.RUNBOOK), "a") as fh:
            fh.write("or scripts/loop/loop_until.sh\n")
        self.assertEqual(D.one_start_command(rb)[2], "FAIL")
        self.assertEqual(D.one_start_command(self.w.root)[2], "NO-DATA")
        with contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(D.main(["--goal", "G1", "--plan", os.path.join(self.w.root, "nope.json")]), 2)


if __name__ == "__main__":
    unittest.main()

#!/usr/bin/env python3
"""LOOP1.g: stage_log.delivery_trace at its entry point, over fixture ledgers.

    python3 -B scripts/test_delivery_trace.py

WHY THIS FILE EXISTS. The frozen scripts/test_loop_delivery.py Delivered reads one REAL landing (AUTH1.b), and none exists
(AUTH1 sits in the 1.1.9 plan's `moved` record, not its units, and the stage ledger holds no AUTH1 row), so Delivered
cannot tell a working delivery_trace from a missing one on this tree. Even a landing would not turn it green: its
test_no_rescue_line reads docs/plan/BROTHER-1.1.9-WBS.json literally, so its plan path, watched constants and
BROTHER_WBS must all point at the plan holding the watched unit. These cases give the function the ledgers the loop
writes, in a scratch folder, and hold each stage to its own condition.

ONE CONDITION PER CASE: each differs from the clean delivery in exactly one fact. Hermetic: the ledger and the plan are
scratch files, nothing reads HOME. Python 3.9, standard library."""
import json
import os
import shutil
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "loop"))
import stage_log as SL  # noqa: E402
import plan_store  # noqa: E402

BASE, CLOSE = "a" * 40, "b" * 40
ORDER = list(SL.DELIVERY_STAGES)


def srow(at, stage, event, sub="U1.a", unit="U1", ok=None, pid=1):
    return {"at": at, "stage": stage, "event": event, "sub": sub, "unit": unit, "ok": ok, "pid": pid}


CLEAN = [srow(10, "model", "enter"), srow(20, "model", "leave", ok=True),
         srow(20, "grade", "enter"), srow(30, "grade", "leave", ok=True)]


def landed_event(seq=1, sub="U1.a", kind="landed", commit=BASE):
    return plan_store.landing_event("U1", sub, commit, "%s LANDED line." % sub, seq, kind=kind)


class Trace(unittest.TestCase):
    def setUp(self):
        self.d = tempfile.mkdtemp(prefix="delivery-trace-")
        self.addCleanup(shutil.rmtree, self.d, True)

    def trace(self, rows=CLEAN, events=None, plan=True, unit="U1", sub="U1.a"):
        ledger = os.path.join(self.d, "stages.jsonl")
        with open(ledger, "w", encoding="utf-8") as fh:
            for r in rows:
                fh.write(json.dumps(r) + "\n")
        planf = os.path.join(self.d, "plan.json")
        if plan:
            with open(planf, "w", encoding="utf-8") as fh:
                json.dump({"units": [{"id": "U0"}, {"id": "U1", "landing_events": events if events is not None else [landed_event(),
                           landed_event(2, kind="closed", commit=CLOSE)]}]}, fh)
        return SL.delivery_trace(unit, sub, stages_path=ledger, plan_file=planf)

    def stages(self, rows):
        return [r["stage"] for r in rows]

    def test_a_delivery_reads_every_stage_in_order_each_bound_to_a_tree(self):
        rows = self.trace()
        self.assertEqual(ORDER, self.stages(rows))
        self.assertEqual([BASE] * 4 + [CLOSE], [r["tree"] for r in rows])
        self.assertEqual([10, 20, 30], [r["at"] for r in rows[:3]])

    def test_the_rows_come_from_the_ledger_not_from_a_constant(self):
        later = [dict(r, at=r["at"] + 1000) for r in CLEAN]
        self.assertEqual([1010, 1020, 1030], [r["at"] for r in self.trace(rows=later)[:3]])

    def test_a_landing_commit_comes_from_the_plan_event(self):
        other = "c" * 40
        rows = self.trace(events=[landed_event(commit=other)])
        self.assertEqual(["admitted", "built", "graded", "landed"], self.stages(rows))
        self.assertEqual({other}, {r["tree"] for r in rows})

    def test_no_landing_event_means_no_landed_and_no_tree(self):
        rows = self.trace(events=[])
        self.assertEqual(["admitted", "built", "graded"], self.stages(rows))
        self.assertEqual([None] * 3, [r["tree"] for r in rows])

    def test_a_reverted_landing_is_not_landed_and_not_closed(self):
        rows = self.trace(events=[landed_event(), landed_event(2, kind="reverted"), landed_event(3, kind="closed", commit=CLOSE)])
        self.assertEqual(["admitted", "built", "graded"], self.stages(rows))

    def test_a_closure_with_no_landing_is_not_a_closed_row(self):
        rows = self.trace(events=[landed_event(1, kind="closed", commit=CLOSE)])
        self.assertNotIn("closed", self.stages(rows))

    def test_a_torn_event_removes_the_landing(self):
        torn = dict(landed_event(), commit="HEAD")
        self.assertEqual(["admitted", "built", "graded"], self.stages(self.trace(events=[torn])))

    def test_a_failed_grade_is_not_a_graded_row(self):
        rows = [CLEAN[0], CLEAN[1], CLEAN[2], srow(30, "grade", "leave", ok=False)]
        self.assertEqual([], [s for s in self.stages(self.trace(rows=rows)) if s == "graded"])

    def test_a_verdictless_grade_is_not_a_graded_row(self):
        rows = CLEAN[:3] + [srow(30, "grade", "leave", ok=None)]
        self.assertNotIn("graded", self.stages(self.trace(rows=rows)))

    def test_a_build_finished_after_the_grade_is_not_the_graded_build(self):
        rows = CLEAN + [srow(80, "model", "enter", pid=2), srow(85, "model", "leave", ok=True, pid=2)]
        self.assertEqual([10, 20, 30], [r["at"] for r in self.trace(rows=rows)[:3]])

    def test_the_highest_closed_event_is_the_closure(self):
        newer = "d" * 40
        rows = self.trace(events=[landed_event(), landed_event(2, kind="closed", commit=CLOSE),
                                  landed_event(3, kind="closed", commit=newer)])
        self.assertEqual(newer, rows[-1]["tree"])

    def test_a_failed_build_is_not_a_built_row(self):
        rows = [CLEAN[0], srow(20, "model", "leave", ok=False)] + CLEAN[2:]
        self.assertNotIn("built", self.stages(self.trace(rows=rows)))

    def test_a_grade_with_no_build_before_it_leaves_no_built_or_admitted(self):
        self.assertEqual([], [s for s in self.stages(self.trace(rows=CLEAN[2:])) if s in ("built", "admitted")])

    def test_a_later_failed_attempt_does_not_reorder_the_stages(self):
        rows = CLEAN + [srow(90, "model", "enter", pid=2), srow(95, "model", "leave", ok=False, pid=2)]
        got = self.trace(rows=rows)
        self.assertEqual([10, 20, 30], [r["at"] for r in got[:3]])

    def test_a_retry_reads_the_attempt_that_was_graded(self):
        rows = [srow(1, "model", "enter"), srow(2, "model", "leave", ok=False)] + CLEAN
        self.assertEqual([10, 20, 30], [r["at"] for r in self.trace(rows=rows)[:3]])

    def test_another_sub_units_rows_are_not_read(self):
        rows = [dict(r, sub="U1.b") for r in CLEAN]
        self.assertEqual([], [s for s in self.stages(self.trace(rows=rows)) if s in ("admitted", "built", "graded")])

    def test_another_units_rows_are_not_read(self):
        rows = [dict(r, unit="U2") for r in CLEAN]
        self.assertNotIn("graded", self.stages(self.trace(rows=rows)))

    def test_another_sub_units_landing_is_not_read(self):
        rows = self.trace(events=[landed_event(sub="U1.b"), landed_event(2, sub="U1.b", kind="closed", commit=CLOSE)])
        self.assertEqual(["admitted", "built", "graded"], self.stages(rows))

    def test_a_missing_plan_reads_the_ledger_rows_only(self):
        err, real = __import__("io").StringIO(), sys.stderr
        sys.stderr = err
        try:
            rows = self.trace(plan=False)
        finally:
            sys.stderr = real
        self.assertEqual(["admitted", "built", "graded"], self.stages(rows))
        self.assertIn("plan unreadable", err.getvalue())

    def test_a_missing_ledger_and_plan_is_an_empty_trace(self):
        self.assertEqual([], SL.delivery_trace("U1", "U1.a", stages_path=os.path.join(self.d, "none.jsonl"),
                                               plan_file=os.path.join(self.d, "none.json")))

    def test_a_corrupt_ledger_line_is_skipped_not_raised(self):
        ledger = os.path.join(self.d, "stages.jsonl")
        self.trace()
        with open(ledger, "a", encoding="utf-8") as fh:
            fh.write("NOT JSON\n")
        got = SL.delivery_trace("U1", "U1.a", stages_path=ledger, plan_file=os.path.join(self.d, "plan.json"))
        self.assertEqual(ORDER, self.stages(got))

    def test_a_ledger_written_by_the_real_writer_reads_back(self):
        ledger = os.path.join(self.d, "real.jsonl")
        for stage, ok in (("model", True), ("grade", True)):
            SL.enter(stage, "U1.a", "U1", path=ledger, now=1.0)
            SL.leave(stage, "U1.a", "U1", ok=ok, path=ledger, now=2.0)
        self.trace()
        got = SL.delivery_trace("U1", "U1.a", stages_path=ledger, plan_file=os.path.join(self.d, "plan.json"))
        self.assertEqual(ORDER, self.stages(got))

    def test_a_none_or_empty_id_reads_nothing(self):
        rows = [{"at": 5, "stage": "grade", "event": "leave", "ok": True}, {"at": 4, "stage": "model", "event": "leave", "ok": True}]
        for unit, sub in ((None, None), ("", ""), ("U1", None), (None, "U1.a"), (7, ["U1.a"])):
            self.assertEqual([], self.trace(rows=rows, unit=unit, sub=sub), (unit, sub))

    def test_a_case_variant_or_undeclared_id_reads_nothing(self):
        self.assertEqual([], self.trace(unit="u1"))
        self.assertEqual([], self.trace(sub="U1.A"))
        self.assertEqual([], self.trace(unit="U9", sub="U9.a"))

    def test_non_utf8_bytes_in_the_ledger_lose_one_line_not_the_rest(self):
        self.trace()
        ledger = os.path.join(self.d, "stages.jsonl")
        with open(ledger, "ab") as fh:
            fh.write(b'{"at": 99, "stage": "model", "note": "\xff\xfe"}\n')
        err, real = __import__("io").StringIO(), sys.stderr
        sys.stderr = err
        try:
            got = SL.delivery_trace("U1", "U1.a", stages_path=ledger, plan_file=os.path.join(self.d, "plan.json"))
        finally:
            sys.stderr = real
        self.assertEqual(ORDER, self.stages(got))

    def test_unicode_line_breaks_inside_a_row_do_not_split_it(self):
        rows = [dict(r, note="a\u2028b\u0085c") for r in CLEAN]
        ledger = os.path.join(self.d, "stages.jsonl")
        with open(ledger, "w", encoding="utf-8") as fh:
            for r in rows:
                fh.write(json.dumps(r, ensure_ascii=False) + "\n")
        with open(os.path.join(self.d, "plan.json"), "w", encoding="utf-8") as fh:
            json.dump({"units": [{"id": "U1", "landing_events": [landed_event()]}]}, fh)
        got = SL.delivery_trace("U1", "U1.a", stages_path=ledger, plan_file=os.path.join(self.d, "plan.json"))
        self.assertEqual(["admitted", "built", "graded", "landed"], self.stages(got))

    def test_a_plan_that_is_not_an_object_or_has_no_unit_list_reads_ledger_rows_only(self):
        ledger = os.path.join(self.d, "stages.jsonl")
        self.trace()
        for body in ("[1, 2]", '{"units": "x"}', "not json", ""):
            planf = os.path.join(self.d, "odd plan.json")   # a path with a space, too
            with open(planf, "w", encoding="utf-8") as fh:
                fh.write(body)
            err, real = __import__("io").StringIO(), sys.stderr
            sys.stderr = err
            try:
                got = SL.delivery_trace("U1", "U1.a", stages_path=ledger, plan_file=planf)
            finally:
                sys.stderr = real
            self.assertEqual(["admitted", "built", "graded"], self.stages(got), body)

    def test_a_refused_plan_selection_reads_ledger_rows_only(self):
        ledger = os.path.join(self.d, "stages.jsonl")
        self.trace()
        env = os.environ.get("BROTHER_WBS")
        for bad in ("/abs/plan.json", "../x.json", "docs/plan/x.txt", "-docs/plan/x.json"):
            os.environ["BROTHER_WBS"] = bad
            err, real = __import__("io").StringIO(), sys.stderr
            sys.stderr = err
            try:
                got = SL.delivery_trace("U1", "U1.a", stages_path=ledger)
            finally:
                sys.stderr = real
                os.environ.pop("BROTHER_WBS") if env is None else os.environ.__setitem__("BROTHER_WBS", env)
            self.assertEqual(["admitted", "built", "graded"], self.stages(got), bad)
            self.assertIn("plan unreadable", err.getvalue())

    def test_a_trace_writes_and_creates_nothing(self):
        self.trace()
        before = sorted((b, f, os.path.getsize(os.path.join(b, f))) for b, _d, fs in os.walk(self.d) for f in fs)
        dirs = sorted(b for b, _d, _f in os.walk(self.d))
        SL.delivery_trace("U1", "U1.a", stages_path=os.path.join(self.d, "stages.jsonl"), plan_file=os.path.join(self.d, "plan.json"))
        after = sorted((b, f, os.path.getsize(os.path.join(b, f))) for b, _d, fs in os.walk(self.d) for f in fs)
        self.assertEqual((before, dirs), (after, sorted(b for b, _d, _f in os.walk(self.d))))

    def test_the_unset_selector_is_named_on_stderr(self):
        ledger = os.path.join(self.d, "stages.jsonl")
        self.trace()
        env = os.environ.pop("BROTHER_WBS", None)
        err, real = __import__("io").StringIO(), sys.stderr
        sys.stderr = err
        try:
            SL.delivery_trace("U1", "U1.a", stages_path=ledger)
        finally:
            sys.stderr = real
            if env is not None:
                os.environ["BROTHER_WBS"] = env
        self.assertIn("BROTHER_WBS is unset", err.getvalue())

    def test_the_selected_plan_is_read_when_none_is_named(self):
        ledger = os.path.join(self.d, "stages.jsonl")
        self.trace()
        os.makedirs(os.path.join(self.d, "docs", "plan"))
        shutil.copy(os.path.join(self.d, "plan.json"), os.path.join(self.d, "docs", "plan", "X.json"))
        was, env = os.getcwd(), os.environ.get("BROTHER_WBS")
        os.chdir(self.d)
        os.environ["BROTHER_WBS"] = "docs/plan/X.json"
        try:
            got = SL.delivery_trace("U1", "U1.a", stages_path=ledger)
        finally:
            os.chdir(was)
            os.environ.pop("BROTHER_WBS") if env is None else os.environ.__setitem__("BROTHER_WBS", env)
        self.assertEqual(ORDER, self.stages(got))


if __name__ == "__main__":
    unittest.main()

"""Calibration for scripts/safe_unwatched_time.py, driven in both directions.

The property this file exists to assert is not that a number comes out. It is
that the number SHORTENS when the record says the run stopped being safe, and
that a run with no record produces NO-DATA rather than a flattering zero. A
metric that only ever grows is a marketing figure, and this estate has already
learned that a check which cannot go red is not a check.

Every fixture is built in a temp directory. Nothing here reads or writes the
real run store, except one read-only sweep that asserts no figure of its own.
"""
import datetime
import json
import os
import shutil
import sys
import tempfile
import unittest

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(REPO_ROOT, 'scripts'))
import safe_unwatched_time as sut  # noqa: E402

# E100: one sandbox for every temp tree this process makes, removed at exit.
import os as _e100_os, sys as _e100_sys  # noqa: E402
_e100_sys.path.append(_e100_os.path.join(
    _e100_os.path.dirname(_e100_os.path.abspath(__file__)), '.'))
try:  # noqa: E402
    import tmp_sandbox as _e100_tmp
    _e100_tmp.install()
except ImportError:
    # A packager (scripts/export_public.py, make_benchmark_bundle.py) can copy
    # this test without scripts/tmp_sandbox.py beside it. Say so rather than
    # dying: the sandbox is hygiene, not the subject.
    _e100_sys.stderr.write(
        "tmp_sandbox absent: %s leaves its temp trees behind\n"
        % _e100_os.path.basename(__file__))

# The journal vocabulary these fixtures replay, taken from the instrument
# rather than re-spelled here. One of the two files owns these strings, and it
# is the one whose contract with the journal they are; a test that re-typed
# them could drift from the reader it is meant to calibrate and still pass.
OPENED = sut.EV_RUN_OPENED
FINISHED = sut.EV_UNIT_DONE
VERIFIED = sut.EV_EVIDENCE_VERIFIED
REFUSED = sut.EV_INTEGRATE_REFUSED
ISSUED = sut.EV_RECEIPT_ISSUED
RESUMED = sut.EV_RUN_RESUMED
SCREENED = "acceptance.screened"   # timeline only, the reader gives it no
                                   # meaning, so it is spelled here on purpose

BASE = datetime.datetime(2026, 9, 4, 22, 0, 0, tzinfo=datetime.timezone.utc)


def at(minute):
    """The fixture clock. Minute 0 is the run's first event."""
    return (BASE + datetime.timedelta(minutes=minute)).isoformat()


def event(minute, kind, payload=None, unit_id=None):
    return {"at": at(minute), "type": kind, "unit_id": unit_id,
            "payload": payload or {}}


def claim(unit_id, minute, exit_code=0, state="done"):
    """A claims.json entry, epoch stamped the way the real store writes them."""
    when = (BASE + datetime.timedelta(minutes=minute)).timestamp()
    evidence = {"check_command": "python3 -c pass", "files_changed": ["a.py"]}
    if exit_code is not None:
        evidence["exit_code"] = exit_code
    return {"unit_id": unit_id, "state": state, "claimed_at": when,
            "released_at": when, "evidence": evidence}


def write_run(root, name, events, claims=None):
    """One run directory on disk. Returns its path."""
    run_dir = os.path.join(root, name)
    os.makedirs(run_dir)
    with open(os.path.join(run_dir, "journal.jsonl"), "w",
              encoding="utf-8") as fh:
        for e in events:
            fh.write(json.dumps(e) + "\n")
    if claims is not None:
        with open(os.path.join(run_dir, "claims.json"), "w",
                  encoding="utf-8") as fh:
            json.dump(claims, fh, indent=1)
    return run_dir


#: A ninety minute run that never broke: two units closed, both receipts
#: issued and proven, nothing refused.
UNBROKEN = [
    event(0, OPENED, {"units": 2}),
    event(30, FINISHED, {"files_changed": 1}, "u1"),
    event(31, VERIFIED, {"check_exit": 0}, "u1"),
    event(60, FINISHED, {"files_changed": 1}, "u2"),
    event(61, VERIFIED, {"check_exit": 0}, "u2"),
    event(85, ISSUED, {"receipts": 2, "unproven": 0, "verified": 2}),
    event(90, SCREENED, {"screens": 1}),
]

#: The same run, with u2's own check refuting the claim at minute 40. Every
#: later event is identical, so the ONLY thing that can move the figure is the
#: break itself.
REFUTED_AT_40 = [
    event(0, OPENED, {"units": 2}),
    event(30, FINISHED, {"files_changed": 1}, "u1"),
    event(31, VERIFIED, {"check_exit": 0}, "u1"),
    event(40, VERIFIED, {"check_exit": 1}, "u2"),
    event(60, FINISHED, {"files_changed": 1}, "u2"),
    event(85, ISSUED, {"receipts": 2, "unproven": 1, "verified": 1}),
    event(90, SCREENED, {"screens": 1}),
]


class Sandbox(unittest.TestCase):
    def setUp(self):
        self.root = tempfile.mkdtemp(prefix="sut-")
        self.addCleanup(shutil.rmtree, self.root, True)


class TheSpan(Sandbox):
    def test_an_unbroken_run_spans_its_whole_record(self):
        run = write_run(self.root, "clean", UNBROKEN,
                        {"u1": claim("u1", 30), "u2": claim("u2", 60)})
        report, code = sut.measure(run)
        self.assertEqual(code, 0)
        self.assertEqual(report["broken_by"], "none")
        self.assertAlmostEqual(report["minutes"], 90.0, places=3)
        self.assertEqual(report["units"], 2)

    def test_the_reported_line_has_the_shape_row_s10_asks_for(self):
        run = write_run(self.root, "clean", UNBROKEN,
                        {"u1": claim("u1", 30), "u2": claim("u2", 60)})
        report, _ = sut.measure(run)
        # SUT1.b: the prefix is unchanged; a run with no capture marker (this
        # fixture has none) now says on the line that it is an upper bound.
        self.assertEqual(
            sut.report_line(report),
            "safe unwatched time: 90.0 min over 2 units, broken by none"
            " (upper bound)")

    def test_a_refuted_claim_at_minute_40_of_90_reads_40(self):
        """The whole point. The record runs to minute 90 either way; only the
        break at minute 40 may shorten it, and it must."""
        run = write_run(self.root, "refuted", REFUTED_AT_40,
                        {"u1": claim("u1", 30), "u2": claim("u2", 60)})
        report, code = sut.measure(run)
        self.assertEqual(code, 0)
        self.assertAlmostEqual(report["minutes"], 40.0, places=3)
        self.assertEqual(report["broken_by"], sut.REFUTED)

    def test_only_work_finished_inside_the_span_is_counted(self):
        """u2 closes at minute 60, after the break. Counting it would credit
        the run with work done past the point it stopped being safe."""
        run = write_run(self.root, "refuted", REFUTED_AT_40,
                        {"u1": claim("u1", 30), "u2": claim("u2", 60)})
        report, _ = sut.measure(run)
        self.assertEqual(report["units"], 1)

    def test_the_earliest_break_wins_not_the_worst_one(self):
        events = list(UNBROKEN)
        events.append(event(10, REFUSED, {
            "reason": "QUARANTINE: 1 path(s) changed that u1 never declared: "
                      "stray.txt"}))
        events.append(event(20, VERIFIED, {"check_exit": 2}, "u1"))
        run = write_run(self.root, "two-breaks",
                        sorted(events, key=lambda e: e["at"]), {})
        report, _ = sut.measure(run)
        self.assertAlmostEqual(report["minutes"], 10.0, places=3)
        self.assertEqual(report["broken_by"], sut.SCOPE)


class TheFourBreaks(Sandbox):
    """One fixture per condition named in benchmarks/SAFE-UNWATCHED-TIME.md."""

    def measure_with(self, extra, claims=None, name="run"):
        events = sorted(list(UNBROKEN) + extra, key=lambda e: e["at"])
        run = write_run(self.root, name, events, claims)
        return sut.measure(run)[0]

    def test_a_nonzero_check_exit_is_a_refuted_claim(self):
        report = self.measure_with(
            [event(15, VERIFIED, {"check_exit": 1}, "u1")])
        self.assertEqual(report["broken_by"], sut.REFUTED)

    def test_an_unproven_receipt_breaks_the_span(self):
        report = self.measure_with(
            [event(15, ISSUED, {"receipts": 2, "unproven": 1})])
        self.assertEqual(report["broken_by"], sut.UNPROVEN)

    def test_a_quarantined_undeclared_path_is_a_scope_break(self):
        report = self.measure_with([event(15, REFUSED, {
            "reason": "QUARANTINE: 1 path(s) changed that u1 never declared: "
                      "stray-notes.txt. Held, not discarded."})])
        self.assertEqual(report["broken_by"], sut.SCOPE)

    def test_a_unit_closed_with_no_exit_code_is_a_check_that_never_ran(self):
        report = self.measure_with(
            [], {"u1": claim("u1", 15, exit_code=None)})
        self.assertEqual(report["broken_by"], sut.NO_CHECK)

    def test_a_clean_resume_is_not_a_break(self):
        """E73 proved a killed run resuming with nothing lost. A recovery the
        engine performed itself is continuity, not a failed claim."""
        report = self.measure_with([event(15, RESUMED, {"harness": "abc123"})])
        self.assertEqual(report["broken_by"], "none")


class NoData(Sandbox):
    def test_a_run_with_no_records_at_all_is_no_data_and_exits_3(self):
        run = os.path.join(self.root, "empty")
        os.makedirs(run)
        report, code = sut.measure(run)
        self.assertEqual(code, 3)
        self.assertIn("no journal event", report["nodata"])
        self.assertNotIn("minutes", report)

    def test_no_data_is_never_reported_as_zero_minutes(self):
        """A run nobody recorded is not a run that was safe for zero minutes."""
        run = os.path.join(self.root, "empty2")
        os.makedirs(run)
        report, _ = sut.measure(run)
        line = sut.report_line(report)
        self.assertIn("NO-DATA", line)
        self.assertNotIn("0.0 min", line)

    def test_a_journal_with_no_receipt_is_no_data(self):
        events = [e for e in UNBROKEN if e["type"] != ISSUED]
        run = write_run(self.root, "receiptless", events, {})
        report, code = sut.measure(run)
        self.assertEqual(code, 3)
        self.assertIn("no receipt", report["nodata"])

    def test_a_missing_directory_exits_2_rather_than_pretending(self):
        report, code = sut.measure(os.path.join(self.root, "nope"))
        self.assertEqual(code, 2)
        self.assertIn("not a directory", report["nodata"])


class DamagedRecords(Sandbox):
    def test_a_truncated_final_line_is_skipped_not_fatal(self):
        """What a SIGKILL leaves behind. Refusing to measure a crashed run
        would refuse exactly the runs this metric exists for."""
        run = write_run(self.root, "torn", UNBROKEN, {})
        with open(os.path.join(run, "journal.jsonl"), "a",
                  encoding="utf-8") as fh:
            fh.write('{"at": "2026-09-04T23:40:00+00:00", "type": "unit.d')
        report, code = sut.measure(run)
        self.assertEqual(code, 0)
        self.assertEqual(report["skipped_lines"], 1)
        self.assertAlmostEqual(report["minutes"], 90.0, places=3)

    def test_a_claim_stamped_before_the_run_started_is_not_this_runs_break(self):
        """A claim carried over from an earlier run cannot shorten a span it
        precedes, and treating it as a break would report a negative one."""
        run = write_run(self.root, "carried", UNBROKEN,
                        {"old": claim("old", -30, exit_code=None)})
        report, _ = sut.measure(run)
        self.assertEqual(report["broken_by"], "none")
        self.assertAlmostEqual(report["minutes"], 90.0, places=3)


INTERVENED = sut.EV_OPERATOR_INTERVENTION
CAPTURE = sut.EV_CAPTURE_ON


def operator(minute, action="scope_fix", effect="changed_run", actor="human"):
    return event(minute, INTERVENED, {"actor_class": actor,
                                      "action_kind": action, "effect": effect})


class Interventions(Sandbox):
    """SUT1.b: the span stops at the first intervention that changed the run,
    and the bound says whether a person could have been seen at all."""

    def run_with(self, extra, marker=True, name="run", base=None):
        events = list(UNBROKEN if base is None else base) + list(extra)
        if marker:
            events.append(event(0, CAPTURE))
        events = sorted(events, key=lambda e: e["at"])
        run = write_run(self.root, name, events, {})
        report, code = sut.measure(run)
        self.assertEqual(code, 0)
        return report

    def test_an_uninterrupted_run_with_the_marker_is_strict(self):
        report = self.run_with([])
        self.assertAlmostEqual(report["minutes"], 90.0, places=3)
        self.assertEqual(report["broken_by"], "none")
        self.assertIs(report["upper_bound"], False)
        self.assertEqual(report["interventions"], 0)
        self.assertEqual(report["status_reads"], 0)
        self.assertEqual(
            sut.report_line(report),
            "safe unwatched time: 90.0 min over 2 units, broken by none")

    def test_the_same_run_without_the_marker_is_an_upper_bound(self):
        report = self.run_with([], marker=False)
        self.assertAlmostEqual(report["minutes"], 90.0, places=3)
        self.assertIs(report["upper_bound"], True)
        self.assertTrue(sut.report_line(report).endswith(" (upper bound)"))

    def test_an_operator_fix_at_minute_40_closes_the_span_at_40(self):
        report = self.run_with([operator(40)])
        self.assertAlmostEqual(report["minutes"], 40.0, places=3)
        self.assertEqual(report["broken_by"], sut.INTERVENTION)
        self.assertEqual(report["detail"], "scope_fix by human")
        self.assertEqual(report["units"], 1)
        self.assertEqual(report["interventions"], 1)

    def test_a_status_read_that_changed_nothing_is_not_an_intervention(self):
        report = self.run_with([operator(40, "status_read", "none")])
        self.assertAlmostEqual(report["minutes"], 90.0, places=3)
        self.assertEqual(report["broken_by"], "none")
        self.assertEqual(report["status_reads"], 1)
        self.assertEqual(report["interventions"], 0)

    def test_two_hundred_status_reads_then_one_change_at_80(self):
        reads = [operator(1 + i * 0.25, "status_read", "none")
                 for i in range(200)]
        report = self.run_with(reads + [operator(80, "restart")])
        self.assertAlmostEqual(report["minutes"], 80.0, places=3)
        self.assertEqual(report["status_reads"], 200)
        self.assertEqual(report["interventions"], 1)

    def test_an_earlier_scope_break_wins_over_a_later_fix(self):
        report = self.run_with([event(35, REFUSED, {
            "reason": "QUARANTINE: 1 path(s) changed that u1 never declared: "
                      "x.txt"}), operator(50)])
        self.assertAlmostEqual(report["minutes"], 35.0, places=3)
        self.assertEqual(report["broken_by"], sut.SCOPE)

    def test_an_earlier_fix_wins_over_a_later_scope_break(self):
        report = self.run_with([operator(35), event(50, REFUSED, {
            "reason": "QUARANTINE: 1 path(s) changed that u1 never declared: "
                      "x.txt"})])
        self.assertAlmostEqual(report["minutes"], 35.0, places=3)
        self.assertEqual(report["broken_by"], sut.INTERVENTION)

    def test_on_a_shared_timestamp_the_intervention_is_named_first(self):
        report = self.run_with([event(35, REFUSED, {
            "reason": "QUARANTINE: 1 path(s) changed that u1 never declared: "
                      "x.txt"}), operator(35)])
        self.assertAlmostEqual(report["minutes"], 35.0, places=3)
        self.assertEqual(report["broken_by"], sut.INTERVENTION)

    def test_an_unproven_receipt_still_breaks_as_before(self):
        report = self.run_with([event(15, ISSUED, {"receipts": 2,
                                                   "unproven": 1})])
        self.assertEqual(report["broken_by"], sut.UNPROVEN)
        self.assertAlmostEqual(report["minutes"], 15.0, places=3)

    def test_a_self_recovered_crash_is_not_a_break(self):
        report = self.run_with([event(20, "run.killed", {"signal": 9}),
                                event(21, RESUMED, {"harness": "abc"})])
        self.assertAlmostEqual(report["minutes"], 90.0, places=3)
        self.assertEqual(report["broken_by"], "none")
        self.assertIs(report["upper_bound"], False)

    def test_an_idle_run_reports_zero_productive_units(self):
        idle = [event(0, OPENED, {"units": 0}),
                event(90, ISSUED, {"receipts": 1, "unproven": 0})]
        report = self.run_with([], base=idle)
        self.assertAlmostEqual(report["minutes"], 90.0, places=3)
        self.assertEqual(report["units"], 0)
        self.assertIn(" over 0 units,", sut.report_line(report))

    def test_an_unreadable_operator_event_closes_the_span(self):
        for name, payload in (
                ("string", "human fixed it"),
                ("extra", {"actor_class": "human", "action_kind": "restart",
                           "effect": "none", "prompt": "do the thing"}),
                ("future", {"actor_class": "human",
                            "action_kind": "teleport", "effect": "none"}),
                ("contradiction", {"actor_class": "human",
                                   "action_kind": "status_read",
                                   "effect": "changed_run"})):
            report = self.run_with([event(25, INTERVENED, payload)],
                                   name=name)
            self.assertAlmostEqual(report["minutes"], 25.0, places=3,
                                   msg=name)
            self.assertEqual(report["broken_by"], sut.INTERVENTION, name)
            self.assertEqual(report["detail"],
                             "unreadable intervention event", name)

    def test_a_legacy_human_intervention_event_closes_the_span(self):
        """scripts/intervention_events.py writes human.intervention; a person
        stepped in, so the span cannot run past it."""
        report = self.run_with([event(30, "human.intervention", {
            "category": "correction", "reason": "x",
            "could_continue_without_it": False})])
        self.assertAlmostEqual(report["minutes"], 30.0, places=3)
        self.assertEqual(report["broken_by"], sut.INTERVENTION)

    def test_find_interventions_returns_breaks_reads_and_the_marker(self):
        events = [dict(e, _at=sut._parse_at(e["at"])) for e in (
            event(0, CAPTURE), event(1, CAPTURE),
            operator(5, "status_read", "none"), operator(9))]
        breaks, reads, capture_on = sut.find_interventions(events)
        self.assertEqual(len(breaks), 1)
        self.assertEqual(breaks[0][1:], (sut.INTERVENTION,
                                         "scope_fix by human"))
        self.assertEqual(reads, 1)
        self.assertIs(capture_on, True)
        self.assertEqual(sut.find_interventions([])[2], False)

    def test_measuring_twice_gives_the_same_figure(self):
        events = sorted(list(UNBROKEN) + [operator(40), event(0, CAPTURE)],
                        key=lambda e: e["at"])
        run = write_run(self.root, "twice", events, {})
        self.assertEqual(sut.measure(run), sut.measure(run))

    def test_main_prints_which_bound_the_figure_is(self):
        import io
        from contextlib import redirect_stdout
        for marker, expected in (
                (True, "lower bound: intervention capture was on; "
                       "1 interventions, 1 status read(s)"),
                (False, "upper bound: no intervention capture was on for "
                        "this run")):
            events = list(UNBROKEN) + [operator(40),
                                       operator(10, "status_read", "none")]
            if marker:
                events.append(event(0, CAPTURE))
            run = write_run(self.root, "main-%s" % marker,
                            sorted(events, key=lambda e: e["at"]), {})
            out = io.StringIO()
            with redirect_stdout(out):
                self.assertEqual(sut.main([run]), 0)
            self.assertIn(expected, out.getvalue())
            self.assertNotIn("no operator-input event kind exists yet",
                             out.getvalue())


class TheRealRunStore(unittest.TestCase):
    """Read only, and it asserts nothing about any particular figure: a real
    run's number is a property of that night, not of this suite."""

    def test_every_committed_run_directory_measures_or_says_why(self):
        root = os.path.join(REPO_ROOT, "docs", "plan", "runs")
        if not os.path.isdir(root):
            self.skipTest("no docs/plan/runs in this tree")
        measured = 0
        for name in sorted(os.listdir(root)):
            run = os.path.join(root, name)
            if not os.path.isdir(run):
                continue
            report, code = sut.measure(run)
            self.assertIn(code, (0, 3), "%s returned %d" % (name, code))
            if code == 0:
                self.assertGreaterEqual(report["minutes"], 0.0)
                # SUT1.b: no committed run carries a capture marker, so none
                # may read as a strict figure.
                self.assertIs(report["upper_bound"], True, name)
                measured += 1
            else:
                self.assertTrue(report["nodata"].strip())
        self.assertGreater(measured, 0,
                           "no committed run measured; the instrument reads "
                           "nothing this estate actually writes")


if __name__ == '__main__':
    unittest.main()

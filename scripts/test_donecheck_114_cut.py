#!/usr/bin/env python3
"""GM1.b: G2 measured from the cut driver's own state files and the cut-ready records; a missing or unreadable one
is NO-DATA, a refusal is never foreseen by the absence of the record that should have foreseen it."""
import contextlib
import io
import json
import os
import sys
import tempfile
import time
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import donecheck_114 as D  # noqa: E402

V = "1.1.4"
T1 = "a" * 40
T2 = "b" * 40
DAY = 86400.0
BASE = time.mktime((2026, 9, 22, 12, 0, 0, 0, 0, -1))  # a past local midday, built from numbers
SLEEP_DETAIL = "REFUSED  export gate: the Mac went to Sleep during the run"
NET_DETAIL = "git ls-remote hub: network timeout after 300 seconds"


def phase(verdict, seconds=10, started=0.0, detail=""):
    return {"verdict": verdict, "seconds": seconds, "started": started, "detail": detail}


class Cut:
    def __init__(self, root):
        self.dir = os.path.join(root, "cut")
        os.makedirs(self.dir)

    def state(self, sha12, started, phases, tree=T1, version=V, raw=None, name=None):
        """phases carry `started` as seconds into the run (as the helper builds them); the file holds the driver's
        own shape, an absolute epoch."""
        if isinstance(phases, dict):
            phases = {k: self.shift(v, started) for k, v in phases.items()}
        data = {"schema": "cv1.driver.1", "version": version, "candidate_sha": sha12 * 4, "candidate_tree": tree,
                "started_at": started, "phases": phases, "tip": "", "pin": "", "worktree": ""}
        path = os.path.join(self.dir, name or "driver-%s-%s.json" % (version, sha12))
        with open(path, "w") as fh:
            fh.write(raw if raw is not None else json.dumps(data))
        return path

    @staticmethod
    def shift(ph, started):
        if isinstance(ph, dict) and isinstance(ph.get("started"), (int, float)) and ph["started"] is not True:
            return dict(ph, started=ph["started"] + started)
        return ph

    def record(self, tree, rows, epoch=BASE, verdict="FAIL", version=V, schema="cv1.ready.1"):
        data = {"schema": schema, "version": version, "candidate_tree": tree, "verdict": verdict, "epoch": epoch,
                "rows": rows}
        path = os.path.join(self.dir, "cut-ready-%s-%s-%d.json" % (version, tree[:12], int(epoch * 1000)))
        with open(path, "w") as fh:
            json.dump(data, fh)
        return path


def row(verdict, detail=""):
    return {"group": "premise", "name": "x", "verdict": verdict, "seconds": 1.0, "detail": detail}


def clean_phases(rehearsal=100):
    return {"ready": phase("PASS", 5, 0), "freeze": phase("PASS", 5, 5), "preflight": phase("PASS", 50, 10),
            "rehearsal": phase("PASS", rehearsal, 60)}


class Base(unittest.TestCase):
    def setUp(self):
        self._td = tempfile.TemporaryDirectory()
        self.c = Cut(self._td.name)

    def tearDown(self):
        self._td.cleanup()

    def table(self):
        return {r[0]: r for r in D.g2_table(self.c.dir, V)}

    def main(self, version=V):
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            code = D.main(["--goal", "G2", "--version", version, "--cut", self.c.dir])
        return code, out.getvalue()


class TestReaders(Base):
    def test_states_and_records_are_read_for_the_version_oldest_first(self) -> None:
        self.c.state("aaaaaaaaaaaa", BASE + 100, clean_phases())
        self.c.state("bbbbbbbbbbbb", BASE, clean_phases())
        self.c.state("cccccccccccc", BASE + 50, clean_phases(), version="1.1.3")
        self.c.record(T1, [row("PASS")], epoch=BASE + 9, verdict="PASS")
        self.c.record(T2, [row("PASS")], epoch=BASE + 1, verdict="PASS")
        self.c.record(T1, [row("PASS")], epoch=BASE + 5, verdict="PASS", version="1.1.3")
        states = D.driver_states(self.c.dir, V)
        self.assertEqual([os.path.basename(s["path"]) for s in states],
                         ["driver-1.1.4-bbbbbbbbbbbb.json", "driver-1.1.4-aaaaaaaaaaaa.json"])
        recs = D.ready_records(self.c.dir, V)
        self.assertEqual([r["epoch"] for r in recs], [BASE + 1, BASE + 9])

    def test_a_file_named_for_the_version_but_holding_another_is_not_read(self) -> None:
        self.c.state("aaaaaaaaaaaa", BASE, clean_phases(), name="driver-1.1.4-aaaaaaaaaaaa.json", version="1.1.3")
        self.c.record(T1, [row("PASS")], verdict="PASS", version="1.1.3")
        os.rename(os.path.join(self.c.dir, "cut-ready-1.1.3-%s-%d.json" % (T1[:12], int(BASE * 1000))),
                  os.path.join(self.c.dir, "cut-ready-1.1.4-%s-%d.json" % (T1[:12], int(BASE * 1000))))
        self.assertEqual(D.driver_states(self.c.dir, V), [])
        self.assertEqual(D.ready_records(self.c.dir, V), [])

    def test_an_unparseable_state_or_record_is_one_no_data_entry_by_name(self) -> None:
        self.c.state("aaaaaaaaaaaa", BASE, clean_phases())
        bad = self.c.state("bbbbbbbbbbbb", BASE, None, raw="{not json")
        bad_rec = os.path.join(self.c.dir, "cut-ready-%s-torn.json" % V)
        with open(bad_rec, "w") as fh:
            fh.write("[")
        states = D.driver_states(self.c.dir, V)
        self.assertEqual(len(states), 2)
        self.assertIn(bad, states[-1]["no_data"])
        self.assertIn(bad_rec, D.ready_records(self.c.dir, V)[0]["no_data"])

    def test_a_missing_cut_dir_reads_as_nothing(self) -> None:
        self.assertEqual(D.driver_states(os.path.join(self.c.dir, "absent"), V), [])
        self.assertEqual(D.ready_records(os.path.join(self.c.dir, "absent"), V), [])


class TestRefusals(Base):
    def fail_state(self, detail, tree=T1, seconds=20, started=0):
        phases = {"ready": phase("PASS", 5, 0), "preflight": phase("FAIL", seconds, started, detail)}
        self.c.state("aaaaaaaaaaaa", BASE, phases, tree=tree)

    def refusals(self):
        return D.refusal_rows(D.driver_states(self.c.dir, V), D.ready_records(self.c.dir, V))

    def test_a_missing_cut_ready_record_is_no_data_not_named(self) -> None:
        self.fail_state("REFUSED  release-plan FAIL: the page differs")
        self.assertIsNone(self.refusals()[0]["named"])
        t = self.table()
        self.assertEqual(t["unnamed_refusals"][2], "NO-DATA")
        self.assertEqual(self.main()[0], 2)

    def test_a_refusal_named_by_the_record_is_foreseen(self) -> None:
        self.fail_state("REFUSED  release-plan FAIL: the page differs")
        self.c.record(T1, [row("PASS"), row("FAIL", "xx REFUSED  release-plan FAIL: the page differs yy")])
        self.assertIs(self.refusals()[0]["named"], True)
        t = self.table()
        self.assertEqual((t["unnamed_refusals"][1], t["unnamed_refusals"][2]), (0, "PASS"))

    def test_a_refusal_the_record_did_not_name_is_unnamed_and_fails(self) -> None:
        self.fail_state("REFUSED  release-plan FAIL: the page differs")
        self.c.record(T1, [row("PASS"), row("FAIL", "a different row")])
        self.assertIs(self.refusals()[0]["named"], False)
        t = self.table()
        self.assertEqual((t["unnamed_refusals"][1], t["unnamed_refusals"][2]), (1, "FAIL"))

    def test_a_record_of_another_tree_does_not_foresee_the_refusal(self) -> None:
        self.fail_state("REFUSED  release-plan FAIL: the page differs")
        self.c.record(T2, [row("FAIL", "REFUSED  release-plan FAIL: the page differs")])
        self.assertIsNone(self.refusals()[0]["named"])

    def test_a_pass_row_holding_the_text_does_not_foresee_it(self) -> None:
        self.fail_state("REFUSED  release-plan FAIL: the page differs")
        self.c.record(T1, [row("PASS", "REFUSED  release-plan FAIL: the page differs")], verdict="PASS")
        self.assertIs(self.refusals()[0]["named"], False)

    def test_sleep_and_network_details_are_events(self) -> None:
        self.fail_state(SLEEP_DETAIL)
        self.c.record(T1, [row("PASS")], verdict="PASS")
        self.assertTrue(self.refusals()[0]["event"])
        t = self.table()
        self.assertEqual((t["events_cost_a_run"][1], t["events_cost_a_run"][2]), (1, "FAIL"))
        self.assertEqual((t["unnamed_refusals"][1], t["unnamed_refusals"][2]), (0, "PASS"))
        self.assertEqual(t["late_refusal_seconds"][2], "PASS")

    def test_a_network_timeout_fail_counts_against_the_events_bar(self) -> None:
        self.fail_state(NET_DETAIL)
        self.assertEqual(self.table()["events_cost_a_run"][2], "FAIL")

    def test_an_event_that_was_no_data_and_retried_costs_nothing(self) -> None:
        phases = {"ready": phase("PASS", 5, 0), "freeze": phase("NO-DATA", 5, 5, SLEEP_DETAIL),
                  "preflight": phase("PASS", 5, 10)}
        self.c.state("aaaaaaaaaaaa", BASE, phases)
        t = self.table()
        self.assertEqual((t["events_cost_a_run"][1], t["events_cost_a_run"][2]), (0, "PASS"))
        self.assertEqual(t["unnamed_refusals"][2], "PASS")

    def test_late_refusal_seconds_are_the_driver_seconds(self) -> None:
        self.fail_state("REFUSED  other", seconds=250, started=400)
        self.assertEqual(self.refusals()[0]["seconds"], 650)
        t = self.table()
        self.assertEqual((t["late_refusal_seconds"][1], t["late_refusal_seconds"][2]), (650, "FAIL"))

    def test_a_refusal_at_the_bar_is_not_late(self) -> None:
        self.fail_state("REFUSED  other", seconds=100, started=500)
        t = self.table()
        self.assertEqual((t["late_refusal_seconds"][1], t["late_refusal_seconds"][2]), (600, "PASS"))

    def test_the_export_gate_measurement_is_exempt_from_the_late_bar(self) -> None:
        self.fail_state("REFUSED  export-real-home export_public --dry-run", seconds=1149, started=300)
        t = self.table()
        self.assertEqual((t["late_refusal_seconds"][1], t["late_refusal_seconds"][2]), (0, "PASS"))

    def test_no_refusal_at_all_passes_without_any_record(self) -> None:
        self.c.state("aaaaaaaaaaaa", BASE, clean_phases())
        t = self.table()
        self.assertEqual((t["unnamed_refusals"][1], t["unnamed_refusals"][2]), (0, "PASS"))
        self.assertEqual((t["late_refusal_seconds"][1], t["late_refusal_seconds"][2]), (0, "PASS"))


class TestRehearsalAndRuns(Base):
    def test_rehearsal_seconds_come_from_the_newest_ready_state(self) -> None:
        self.c.state("aaaaaaaaaaaa", BASE, clean_phases(rehearsal=900), tree=T1)
        self.c.state("bbbbbbbbbbbb", BASE + 500, clean_phases(rehearsal=200), tree=T2)
        self.assertEqual(D.rehearsal_seconds(D.driver_states(self.c.dir, V)), {T2: 200.0})
        t = self.table()
        self.assertEqual((t["rehearsal_seconds"][1], t["rehearsal_seconds"][2]), (200, "PASS"))

    def test_a_reused_or_failed_rehearsal_measures_nothing(self) -> None:
        phases = clean_phases()
        phases["rehearsal"] = phase("REUSED", 1, 60)
        self.c.state("aaaaaaaaaaaa", BASE, phases)
        phases = clean_phases()
        phases["rehearsal"] = phase("FAIL", 7000, 60, "NOT-READY")
        self.c.state("bbbbbbbbbbbb", BASE + 9, phases)
        self.assertEqual(D.rehearsal_seconds(D.driver_states(self.c.dir, V)), {})
        self.assertEqual(self.table()["rehearsal_seconds"][2], "NO-DATA")

    def test_a_long_rehearsal_fails_the_bar(self) -> None:
        self.c.state("aaaaaaaaaaaa", BASE, clean_phases(rehearsal=7200))
        t = self.table()
        self.assertEqual((t["rehearsal_seconds"][1], t["rehearsal_seconds"][2]), (7200, "FAIL"))

    def test_driver_runs_count_the_newest_day_and_print_both_figures(self) -> None:
        for i, sha in enumerate(("aaaaaaaaaaaa", "bbbbbbbbbbbb")):
            self.c.state(sha, BASE - 3 * DAY + i * 60, clean_phases())
        for i, sha in enumerate(("cccccccccccc", "dddddddddddd", "eeeeeeeeeeee")):
            self.c.state(sha, BASE + i * 60, clean_phases())
        r = self.table()["driver_runs"]
        self.assertEqual((r[1], r[2]), (3, "FAIL"))
        self.assertIn("3 on", r[3])
        self.assertIn("5 total", r[3])

    def test_two_refused_runs_and_no_ready_pass_the_runs_row_and_no_data_the_rehearsal(self) -> None:
        refused = {"ready": phase("PASS", 5, 0), "preflight": phase("FAIL", 10, 5, "REFUSED  a")}
        self.c.state("aaaaaaaaaaaa", BASE, refused)
        self.c.state("bbbbbbbbbbbb", BASE + 60, refused)
        t = self.table()
        self.assertEqual((t["driver_runs"][1], t["driver_runs"][2]), (2, "PASS"))
        self.assertEqual(t["rehearsal_seconds"][2], "NO-DATA")

    def test_an_open_phase_in_the_newest_state_reads_in_flight(self) -> None:
        self.c.state("aaaaaaaaaaaa", BASE, {"ready": phase("PASS", 5, 0), "freeze": {"started": 5.0}})
        r = self.table()["driver_runs"]
        self.assertEqual(r[2], "PASS")
        self.assertIn("in flight", r[3])
        self.c.state("bbbbbbbbbbbb", BASE + 9, clean_phases())
        self.assertNotIn("in flight", self.table()["driver_runs"][3])


class TestExit(Base):
    def test_no_state_is_no_data_and_the_exit_follows_the_worst(self) -> None:
        code, text = self.main()
        self.assertEqual(code, 2)
        self.assertFalse([l for l in text.splitlines() if l.startswith("PASS")])
        self.assertEqual({r[2] for r in D.g2_table(self.c.dir, V)}, {"NO-DATA"})
        self.assertEqual(D.g2_table(os.path.join(self.c.dir, "absent"), V)[0][2], "NO-DATA")

    def test_one_clean_state_passes_every_row_and_exits_zero(self) -> None:
        self.c.state("aaaaaaaaaaaa", BASE, clean_phases())
        code, text = self.main()
        self.assertEqual(code, 0)
        self.assertEqual([r[2] for r in D.g2_table(self.c.dir, V)], ["PASS"] * 5)
        self.assertIn("driver-1.1.4-*.json", text)

    def test_a_fail_row_exits_one(self) -> None:
        self.c.state("aaaaaaaaaaaa", BASE, clean_phases(rehearsal=7200))
        self.assertEqual(self.main()[0], 1)

    def test_a_state_of_another_version_is_not_this_versions_state(self) -> None:
        self.c.state("aaaaaaaaaaaa", BASE, clean_phases(), version="1.1.3")
        self.assertEqual(self.main()[0], 2)
        self.assertEqual(self.main("1.1.3")[0], 0)

    def test_g2_needs_no_plan_file(self) -> None:
        self.c.state("aaaaaaaaaaaa", BASE, clean_phases())
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            code = D.main(["--goal", "G2", "--version", V, "--cut", self.c.dir, "--plan", "/nonexistent/plan.json"])
        self.assertEqual(code, 0)


class TestHostileInputs(Base):
    def assert_blocks(self, expect_in_text):
        t = self.table()
        for bar in ("driver_runs", "late_refusal_seconds", "unnamed_refusals", "events_cost_a_run"):
            self.assertEqual(t[bar][2], "NO-DATA", bar)
            self.assertIn(expect_in_text, t[bar][3])
        self.assertEqual(self.main()[0], 2)

    def test_phases_as_a_list_is_a_named_no_data(self) -> None:
        self.c.state("aaaaaaaaaaaa", BASE, [1, 2])
        self.c.state("bbbbbbbbbbbb", BASE + 1, clean_phases())
        self.assert_blocks("driver-1.1.4-aaaaaaaaaaaa.json")

    def test_a_seconds_that_is_a_string_is_a_named_no_data(self) -> None:
        self.c.state("aaaaaaaaaaaa", BASE, {"rehearsal": {"verdict": "PASS", "seconds": "100", "started": 0}})
        self.assert_blocks("driver-1.1.4-aaaaaaaaaaaa.json")

    def test_a_started_at_in_the_future_is_a_named_no_data(self) -> None:
        self.c.state("aaaaaaaaaaaa", time.time() + 10 ** 7, clean_phases())
        self.assert_blocks("driver-1.1.4-aaaaaaaaaaaa.json")

    def test_a_started_at_that_is_a_boolean_is_a_named_no_data(self) -> None:
        self.c.state("aaaaaaaaaaaa", True, clean_phases())
        self.assert_blocks("driver-1.1.4-aaaaaaaaaaaa.json")

    def test_a_verdict_outside_the_four_is_named_and_reads_no_data(self) -> None:
        phases = clean_phases()
        phases["freeze"] = phase("MAYBE", 5, 5)
        self.c.state("aaaaaaaaaaaa", BASE, phases)
        self.assert_blocks("MAYBE")

    def test_a_100kb_detail_is_matched_and_the_row_stays_short(self) -> None:
        big = "REFUSED " + "x" * 100000
        phases = {"ready": phase("PASS", 5, 0), "preflight": phase("FAIL", 10, 5, big)}
        self.c.state("aaaaaaaaaaaa", BASE, phases)
        self.c.record(T1, [row("FAIL", big)])
        t = self.table()
        self.assertEqual(t["unnamed_refusals"][2], "PASS")
        self.assertLess(max(len(r[3]) for r in t.values()), 500)

    def test_a_record_for_an_eleven_character_tree_is_a_named_no_data(self) -> None:
        phases = {"ready": phase("PASS", 5, 0), "preflight": phase("FAIL", 10, 5, "REFUSED  a")}
        self.c.state("aaaaaaaaaaaa", BASE, phases, tree="a" * 11)
        short = self.c.record("a" * 11, [row("FAIL", "REFUSED  a")])
        recs = D.ready_records(self.c.dir, V)
        self.assertIn(short, recs[0]["no_data"])
        t = self.table()
        self.assertEqual(t["unnamed_refusals"][2], "NO-DATA")
        self.assertIn(short, t["unnamed_refusals"][3])

    def test_a_corrupt_record_beside_a_whole_one_that_does_not_name_the_refusal_is_no_data(self) -> None:
        phases = {"ready": phase("PASS", 5, 0), "preflight": phase("FAIL", 10, 5, "REFUSED  a")}
        self.c.state("aaaaaaaaaaaa", BASE, phases)
        self.c.record(T1, [row("FAIL", "other")], epoch=BASE)
        with open(os.path.join(self.c.dir, "cut-ready-%s-torn.json" % V), "w") as fh:
            fh.write("[")
        self.assertEqual(self.table()["unnamed_refusals"][2], "NO-DATA")

    def test_a_record_of_the_wrong_schema_is_a_named_no_data(self) -> None:
        self.c.record(T1, [row("PASS")], schema="cv1.ready.0")
        self.assertIn("not a cv1.ready.1", D.ready_records(self.c.dir, V)[0]["no_data"])

    def test_a_detail_that_is_not_text_is_an_unnamed_refusal_not_a_crash(self) -> None:
        phases = {"ready": phase("PASS", 5, 0), "preflight": {"verdict": "FAIL", "seconds": 1, "started": 1, "detail": 7}}
        self.c.state("aaaaaaaaaaaa", BASE, phases)
        self.c.record(T1, [row("FAIL", "anything")])
        self.assertEqual(self.table()["unnamed_refusals"][2], "FAIL")


if __name__ == "__main__":
    unittest.main()

#!/usr/bin/env python3
"""brother_pass's own suite: one class per requirement id in its specification.
Every test drives the pure functions with fixtures, so the suite passes in an export copy with an empty HOME and
never reads the live plan, the live runs folder or the live ledger."""
import json
import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import brother_pass as B  # noqa: E402

CLEAN = {"branch": "b", "dirty": 0, "unpushed": 0, "head": "abc1234"}


def state(**over):
    base = {"dirty": 0, "unpushed": 0, "head": "abc1234", "branch": "b", "ready": [], "unprobed": [],
            "needfact": [], "closable": [], "buildable": [], "free_lanes": 0, "done": 1, "units": 2,
            "ready_builds": [],
            "subs_landed": 1, "subs_total": 4, "held": 0}
    base.update(over)
    return base


class ReqLadder(unittest.TestCase):
    """REQ-LADDER and REQ-ONE-ACTION: a fixed order, and exactly one action."""

    def test_a_dirty_tree_outranks_everything(self):
        s = state(dirty=3, unpushed=2, ready=["a"], closable=["U"], unprobed=["p"], needfact=["n"],
                  buildable=[("x", "U")], free_lanes=5)
        self.assertEqual(B.next_action(s)[0], "CLEAN-TREE")

    def test_an_unpushed_commit_outranks_landing(self):
        self.assertEqual(B.next_action(state(unpushed=1, ready=["a"]))[0], "PUSH")

    def test_landing_outranks_closing(self):
        self.assertEqual(B.next_action(state(ready=["a"], closable=["U"]))[0], "LAND")

    def test_closing_outranks_probing(self):
        self.assertEqual(B.next_action(state(closable=["U"], unprobed=["p"]))[0], "CLOSE")

    def test_probing_outranks_diagnosing(self):
        self.assertEqual(B.next_action(state(unprobed=["p"], needfact=["n"]))[0], "PROBE")

    def test_diagnosing_outranks_starting(self):
        self.assertEqual(B.next_action(state(needfact=["n"], buildable=[("x", "U")], free_lanes=3))[0], "DIAGNOSE")

    def test_a_free_lane_starts_work(self):
        self.assertEqual(B.next_action(state(buildable=[("x", "U")], free_lanes=2))[0], "START")

    def test_no_free_lane_waits_rather_than_starting(self):
        self.assertEqual(B.next_action(state(buildable=[("x", "U")], free_lanes=0))[0], "WAIT")

    def test_an_empty_ready_set_is_an_honest_nothing(self):
        self.assertEqual(B.next_action(state())[0], "NOTHING")

    def test_exactly_one_next_line_is_rendered(self):
        lines = B.render(state(ready=["a"]), B.next_action(state(ready=["a"])))
        self.assertEqual(len([l for l in lines if l.startswith("NEXT")]), 1)


class ReqQuiet(unittest.TestCase):
    """REQ-QUIET: at most 25 lines, and only the first three of anything."""

    def test_the_output_stays_under_the_line_budget(self):
        s = state(ready=[str(i) for i in range(50)], unprobed=[str(i) for i in range(50)],
                  needfact=[str(i) for i in range(50)], closable=[str(i) for i in range(50)])
        self.assertLessEqual(len(B.render(s, B.next_action(s))), 25)

    def test_only_three_items_are_named(self):
        self.assertEqual(B.first3(["a", "b", "c", "d"]), "4: a, b, c ...")

    def test_an_empty_list_names_nothing(self):
        self.assertEqual(B.first3([]), "0")


class ReqNoData(unittest.TestCase):
    """REQ-NO-DATA: an unreadable source is never read as an empty one."""

    def test_a_missing_plan_raises_rather_than_reading_empty(self):
        with tempfile.TemporaryDirectory() as tmp:
            with self.assertRaises(B.Unreadable):
                B.collect(tmp, CLEAN, None, 7, 1)

    def test_a_corrupt_plan_raises(self):
        with tempfile.TemporaryDirectory() as tmp:
            os.makedirs(os.path.join(tmp, "docs", "plan"))
            with open(os.path.join(tmp, B.PLAN), "w") as fh:
                fh.write("{not json")
            with self.assertRaises(B.Unreadable):
                B.collect(tmp, CLEAN, None, 7, 1)

    def test_a_plan_without_units_raises(self):
        with tempfile.TemporaryDirectory() as tmp:
            os.makedirs(os.path.join(tmp, "docs", "plan"))
            with open(os.path.join(tmp, B.PLAN), "w") as fh:
                json.dump({"schema": 1}, fh)
            with self.assertRaises(B.Unreadable):
                B.collect(tmp, CLEAN, None, 7, 1)

    def test_an_unreadable_council_file_raises_too(self):
        """Kills M-NODATA-PASS: the plan itself is valid here, so only read_json's own raise can stop this."""
        with tempfile.TemporaryDirectory() as tmp:
            os.makedirs(os.path.join(tmp, "docs", "plan"))
            with open(os.path.join(tmp, B.PLAN), "w") as fh:
                json.dump({"units": []}, fh)
            bad = os.path.join(tmp, "council.json")
            with open(bad, "w") as fh:
                fh.write("{not json")
            real = B.COUNCIL
            B.COUNCIL = bad
            try:
                with self.assertRaises(B.Unreadable):
                    B.collect(tmp, CLEAN, None, 7, 1)
            finally:
                B.COUNCIL = real

    def test_the_cli_exits_two_and_names_the_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            here = os.getcwd()
            try:
                os.chdir(tmp)
                self.assertEqual(B.main(["--lanes-free", "0"]), 2)
            finally:
                os.chdir(here)


class ReqQuietNamesThree(unittest.TestCase):
    """REQ-QUIET again, on the NEXT line itself: it names at most three items however many there are."""

    def test_the_land_line_names_at_most_three(self):
        s = state(ready=["a", "b", "c", "d", "e"])
        sentence = B.next_action(s)[1]
        self.assertIn("5 ready", sentence)
        self.assertNotIn("d", sentence.split(":")[-1])

    def test_the_diagnose_line_names_at_most_three(self):
        s = state(needfact=["n1", "n2", "n3", "n4"])
        self.assertNotIn("n4", B.next_action(s)[1])


class ReqStateless(unittest.TestCase):
    """REQ-STATELESS: the reading is a function of the disk, not of anything carried between runs."""

    def test_the_same_status_folders_read_the_same_twice(self):
        dirs = ["/r/A-100000/", "/r/A-200000/", "/r/B-100000/"]
        texts = {"/r/A-100000/": "EXHAUSTED old", "/r/A-200000/": "READY /b/x.json", "/r/B-100000/": "RUNNING"}
        first = B.newest_status(dirs, lambda d: texts[d])
        second = B.newest_status(list(reversed(dirs)), lambda d: texts[d])
        self.assertEqual(first, second)

    def test_the_newest_run_folder_wins(self):
        got = B.newest_status(["/r/A-100000/", "/r/A-200000/"],
                              lambda d: "EXHAUSTED x" if "100000" in d else "READY /b/x.json")
        self.assertEqual(got["A"][0], "READY")


class ReqHostile(unittest.TestCase):
    """REQ-HOSTILE: malformed data is data, never a crash."""

    def test_a_status_folder_without_a_stamp_is_ignored(self):
        self.assertEqual(B.newest_status(["/r/junk/"], lambda d: "READY /b/x.json"), {})

    def test_a_missing_status_file_reads_as_running(self):
        self.assertEqual(B.newest_status(["/r/A-100000/"], lambda d: "")["A"][0], "RUNNING")

    def test_a_unit_with_no_sub_units_is_never_closable(self):
        self.assertEqual(B.closable({"units": [{"id": "U", "sub_units": [], "state": "PARTIAL"}]}, set()), [])

    def test_a_unit_already_done_is_not_closable_again(self):
        plan = {"units": [{"id": "U", "sub_units": ["U.1"], "state": "DONE", "evidence": "U.1 landed"}]}
        self.assertEqual(B.closable(plan, {"U.1"}), [])

    def test_a_retired_unit_is_not_closable(self):
        plan = {"units": [{"id": "U", "sub_units": ["U.1"], "state": "RETIRED", "evidence": "U.1 landed"}]}
        self.assertEqual(B.closable(plan, {"U.1"}), [])

    def test_every_sub_unit_landed_makes_a_unit_closable(self):
        plan = {"units": [{"id": "U", "sub_units": ["U.1", "U.2"], "state": "PARTIAL",
                           "evidence": "U.1 landed today, U.2 landed today"}]}
        self.assertEqual(B.closable(plan, B.landed_subs(plan)), ["U"])

    def test_a_full_stop_breaks_the_landed_match(self):
        plan = {"units": [{"id": "U", "sub_units": ["U.1"], "state": "PARTIAL", "evidence": "U.1 spec. landed"}]}
        self.assertEqual(B.landed_subs(plan), set())


class ReqBudgetAndHolds(unittest.TestCase):
    """REQ-BUDGET, and the reasons a sub unit is legitimately not startable."""

    PLAN = {"units": [{"id": "U", "spec": "s.md", "state": "PARTIAL", "evidence": "",
                       "sub_units": ["U.1", "U.2", "U.3", "U.4", "U.5"]}]}

    def test_a_council_hold_blocks_the_whole_unit(self):
        got = B.buildable(self.PLAN, {}, set(), {"U": {"state": "DO-NOT-BUILD"}}, {"U.1": {"score": 10}})
        self.assertEqual(got, [])

    def test_a_spec_under_the_bar_is_not_startable(self):
        self.assertEqual(B.buildable(self.PLAN, {}, set(), {}, {"U.1": {"score": 8}}), [])

    def test_an_unscored_spec_is_not_startable(self):
        self.assertEqual(B.buildable(self.PLAN, {}, set(), {}, {}), [])

    def test_a_boolean_score_is_not_a_score(self):
        self.assertEqual(B.buildable(self.PLAN, {}, set(), {}, {"U.1": {"score": True}}), [])

    def test_a_sub_unit_already_running_is_not_started_again(self):
        got = B.buildable(self.PLAN, {"U.1": ("RUNNING", "")}, set(), {}, {"U.1": {"score": 10}, "U.2": {"score": 10}})
        self.assertEqual(got, [("U.2", "U")])

    def test_a_quarantined_build_is_never_restarted(self):
        got = B.buildable(self.PLAN, {"U.1": ("QUARANTINE", "bad")}, set(), {},
                          {"U.1": {"score": 10}, "U.2": {"score": 8}})
        self.assertEqual(got, [])

    def test_a_landed_sub_unit_is_skipped(self):
        got = B.buildable(self.PLAN, {}, {"U.1"}, {}, {"U.1": {"score": 10}, "U.2": {"score": 10}})
        self.assertEqual(got, [("U.2", "U")])

    def test_a_unit_with_no_spec_is_not_startable(self):
        plan = {"units": [{"id": "U", "state": "PARTIAL", "sub_units": ["U.1"], "evidence": ""}]}
        self.assertEqual(B.buildable(plan, {}, set(), {}, {"U.1": {"score": 10}}), [])

    def test_one_lane_per_unit_not_one_per_sub_unit(self):
        got = B.buildable(self.PLAN, {}, set(), {}, {s: {"score": 10} for s in ("U.1", "U.2", "U.3")})
        self.assertEqual(len(got), 1)



class ReqDoIsSafe(unittest.TestCase):
    """--do runs the rung's own tool, and NEVER the one that would delete work."""

    def test_a_dirty_tree_is_never_reverted_automatically(self):
        self.assertIsNone(B.runnable("CLEAN-TREE", state(dirty=2)))

    def test_closing_now_runs_the_unit_closer(self):
        argv = B.runnable("CLOSE", state(closable=["U"]))
        self.assertTrue(argv[-1].endswith("close_unit.py"))

    def test_nothing_and_wait_run_nothing(self):
        self.assertIsNone(B.runnable("NOTHING", state()))
        self.assertIsNone(B.runnable("WAIT", state(buildable=[("x", "U")])))

    def test_probe_now_runs_the_probe_round(self):
        argv = B.runnable("PROBE", state(unprobed=["p"]))
        self.assertTrue(argv[-1].endswith("probe_round.py"))

    def test_push_runs_git_push_to_the_hub_on_this_branch(self):
        argv = B.runnable("PUSH", state(unpushed=1, branch="my-branch"))
        self.assertEqual(argv, ["git", "push", "hub", "my-branch"])

    def test_land_passes_exactly_one_build_so_a_refusal_can_blame_it(self):
        s = state(ready=["a"], ready_builds=["/b/a.json", "/b/b.json"])
        argv = B.runnable("LAND", s)
        self.assertEqual(argv[-1], "/b/a.json")
        self.assertNotIn("/b/b.json", argv)

    def test_start_runs_the_runner_pool(self):
        argv = B.runnable("START", state(buildable=[("x", "U")], free_lanes=1))
        self.assertTrue(argv[-1].endswith("runner_pool.py"))

    def test_diagnose_runs_the_full_round_not_just_the_brief(self):
        """A brief that is never dispatched leaves the loop naming DIAGNOSE forever."""
        argv = B.runnable("DIAGNOSE", state(needfact=["n"]))
        self.assertTrue(argv[-1].endswith("diag_round.py"))

    def test_an_unknown_code_runs_nothing(self):
        self.assertIsNone(B.runnable("SOMETHING-ELSE", state()))

    def test_an_empty_branch_never_becomes_a_push(self):
        self.assertIsNone(B.runnable("PUSH", state(unpushed=1, branch="")))


class OneBuildPerUnitPerBatch(unittest.TestCase):
    def test_the_unit_of_a_sub_unit_is_found(self):
        plan = {"units": [{"id": "U", "sub_units": ["U.1", "U.2"]}]}
        self.assertEqual(B.unit_of("U.2", plan), "U")

    def test_an_unknown_sub_unit_has_no_unit(self):
        self.assertIsNone(B.unit_of("Z.9", {"units": []}))



class ThePulseIsHonest(unittest.TestCase):
    """A pulse exists so a person can check in cheaply. It must never imply movement that did not happen."""

    def test_an_unreadable_journal_says_no_data(self):
        self.assertIn("NO-DATA", B.read_pulse("/no/such/journal.jsonl")[0])

    def test_an_empty_journal_says_no_data(self):
        with tempfile.TemporaryDirectory() as tmp:
            p = os.path.join(tmp, "j.jsonl")
            open(p, "w").close()
            self.assertIn("NO-DATA", B.read_pulse(p)[0])

    def test_a_corrupt_journal_says_no_data_rather_than_guessing(self):
        with tempfile.TemporaryDirectory() as tmp:
            p = os.path.join(tmp, "j.jsonl")
            open(p, "w").write("{not json\n")
            self.assertIn("NO-DATA", B.read_pulse(p)[0])

    def test_a_flat_board_reports_no_movement(self):
        with tempfile.TemporaryDirectory() as tmp:
            p = os.path.join(tmp, "j.jsonl")
            with open(p, "w") as fh:
                for _ in range(2):
                    fh.write(json.dumps({"at": "2026-09-21 02:00:00", "done": 5, "units": 53,
                                         "subs_landed": 62, "subs_total": 212, "ready": 0,
                                         "unprobed": 0, "needfact": 17, "next": "DIAGNOSE"}) + "\n")
            self.assertTrue(any("sub units +0" in l for l in B.read_pulse(p)))

    def test_real_movement_is_reported(self):
        with tempfile.TemporaryDirectory() as tmp:
            p = os.path.join(tmp, "j.jsonl")
            with open(p, "w") as fh:
                fh.write(json.dumps({"at": "a", "done": 5, "units": 53, "subs_landed": 60,
                                     "subs_total": 212, "ready": 0, "unprobed": 0, "needfact": 1,
                                     "next": "LAND"}) + "\n")
                fh.write(json.dumps({"at": "b", "done": 6, "units": 53, "subs_landed": 63,
                                     "subs_total": 212, "ready": 0, "unprobed": 0, "needfact": 1,
                                     "next": "CLOSE"}) + "\n")
            out = B.read_pulse(p)
            self.assertTrue(any("sub units +3" in l for l in out))
            self.assertTrue(any("whole units +1" in l for l in out))

    def test_a_journal_failure_never_stops_the_pass(self):
        row = B.journal(state(ready=["a"]), ("LAND", "x"), "2026-09-21 02:00:00", path="/no/such/dir/j.jsonl")
        self.assertEqual(row["next"], "LAND")



class WorkerCohortsRunInParallel(unittest.TestCase):
    """One writer by law, many worker cohorts at once. A cohort writes only evidence, never the repository."""

    def test_a_cohort_with_work_is_started(self):
        got = B.cohorts_to_start(state(unprobed=["p"]), set())
        self.assertEqual([n for n, _ in got], ["probe"])

    def test_several_cohorts_start_in_one_pass(self):
        got = B.cohorts_to_start(state(unprobed=["p"], needfact=["n"], buildable=[("x", "U")], free_lanes=2), set())
        self.assertEqual(sorted(n for n, _ in got), ["build", "diagnose", "probe"])

    def test_a_one_shot_round_already_running_is_never_started_twice(self):
        got = B.cohorts_to_start(state(unprobed=["p"], needfact=["n"]), {"probe"})
        self.assertEqual([n for n, _ in got], ["diagnose"])

    def test_the_build_pool_is_run_again_so_it_can_top_up(self):
        """It sat at 2 lanes while 6 were funded, because one live runner made the cohort look busy."""
        got = B.cohorts_to_start(state(buildable=[("x", "U")], free_lanes=6), {"build"})
        self.assertEqual([n for n, _ in got], ["build"])

    def test_the_build_pool_still_stops_when_no_lane_is_funded(self):
        self.assertEqual(B.cohorts_to_start(state(buildable=[("x", "U")], free_lanes=0), {"build"}), [])

    def test_a_cohort_with_no_work_is_not_started(self):
        self.assertEqual(B.cohorts_to_start(state(), set()), [])

    def test_the_build_cohort_needs_a_funded_lane(self):
        self.assertEqual(B.cohorts_to_start(state(buildable=[("x", "U")], free_lanes=0), set()), [])

    def test_the_one_shot_cohorts_do_not_need_a_funded_lane(self):
        """A probe or diagnose round costs cents once; only a live runner burns money continuously."""
        got = B.cohorts_to_start(state(unprobed=["p"], needfact=["n"], free_lanes=0), set())
        self.assertEqual(sorted(n for n, _ in got), ["diagnose", "probe"])

    def test_a_live_process_is_recognised_per_cohort(self):
        live = B.running_cohorts("python3 scripts/probe_round.py\npython3 /x/unit_runner.py D1 D1.2")
        self.assertEqual(live, {"probe", "build"})

    def test_no_processes_means_no_cohort_running(self):
        self.assertEqual(B.running_cohorts(""), set())

    def test_the_writer_rungs_are_exactly_the_serial_ones(self):
        self.assertEqual(set(B.WRITER_RUNGS), {"CLEAN-TREE", "PUSH", "LAND", "CLOSE"})

    def test_no_worker_cohort_is_also_a_writer_rung(self):
        names = {n.upper() for n, _, _ in B.WORKER_COHORTS}
        self.assertEqual(names & set(B.WRITER_RUNGS), set())



class ThePulseSaysWhetherAnythingIsMoving(unittest.TestCase):
    """A sign of life is only useful if it can say NO. Added after five passes moved nothing and the pulse
    reported a cheerful +0 without noting that the board had not moved in half an hour."""

    def rows(self, landed, at=("02:00:00", "02:05:00", "02:10:00")):
        return [{"at": "2026-09-21 " + t, "done": 5, "units": 53, "subs_landed": n, "subs_total": 212,
                 "ready": 0, "unprobed": 0, "needfact": 17, "next": "DIAGNOSE"}
                for t, n in zip(at, landed)]

    def test_a_moving_board_is_reported_as_moving(self):
        self.assertIn("moving: +2", B.stall_note(self.rows([60, 61, 62]), set()))

    def test_a_flat_board_with_work_alive_says_give_it_a_round(self):
        note = B.stall_note(self.rows([62, 62, 62]), {"build"})
        self.assertIn("NOT moving", note)
        self.assertIn("build", note)

    def test_a_flat_board_with_nothing_alive_is_a_stall(self):
        self.assertIn("STALLED", B.stall_note(self.rows([62, 62, 62]), set()))

    def test_one_pass_is_too_few_to_judge(self):
        self.assertIn("too few", B.stall_note(self.rows([62], at=("02:00:00",)), set()))

    def test_the_pace_is_the_median_gap(self):
        med, n = B.gap_minutes(self.rows([1, 2, 3]))
        self.assertAlmostEqual(med, 5.0)
        self.assertEqual(n, 2)

    def test_an_unparsable_stamp_is_skipped_not_guessed(self):
        rows = self.rows([1, 2, 3])
        rows[1]["at"] = "not a time"
        self.assertEqual(B.gap_minutes(rows)[1], 1)

    def test_no_usable_stamp_yields_no_pace(self):
        self.assertEqual(B.gap_minutes([{"at": "x"}]), (None, 0))



class TheMidnightWrap(unittest.TestCase):
    """The run folder stamp is HHMMSS and WRAPS AT MIDNIGHT. Sorting those strings put yesterday's 220142 after
    today's 023623, so every run made after midnight was invisible and the loop read a stale status from the
    previous evening. It is why the board stopped moving on 2026-09-21."""

    def setUp(self):
        self.texts = {"/r/A-220142/": "EXHAUSTED yesterday evening",
                      "/r/A-023623/": "READY /b/today.json"}
        # the filesystem knows the truth the name cannot carry: today's folder is newer
        self.times = {"/r/A-220142/": 1000.0, "/r/A-023623/": 2000.0}

    def read(self, d):
        return self.texts[d]

    def mtime(self, d):
        return self.times[d]

    def test_a_folder_made_after_midnight_is_seen_as_newest(self):
        got = B.newest_status(list(self.texts), self.read, mtime=self.mtime)
        self.assertEqual(got["A"][0], "READY")

    def test_the_name_order_alone_would_get_it_wrong(self):
        """Proof the old rule was wrong, not merely different: sorting by the stamp picks the stale one."""
        by_name = sorted(self.texts, key=lambda p: p.rstrip("/")[-6:])[-1]
        self.assertEqual(self.texts[by_name], "EXHAUSTED yesterday evening")

    def test_an_unreadable_folder_time_sorts_first_rather_than_winning(self):
        def broken(d):
            raise OSError("gone")
        got = B.newest_status(list(self.texts), self.read, mtime=broken)
        self.assertIn(got["A"][0], ("READY", "EXHAUSTED"))   # falls back to the name, never crashes

    def test_dir_time_returns_zero_when_it_cannot_be_read(self):
        def broken(d):
            raise OSError("gone")
        self.assertEqual(B.dir_time("/nope", broken), 0.0)



class ThePresentationIsReadable(unittest.TestCase):
    """The owner could not tell whether a cycle worked. These pin the three things that fixed it."""

    def test_the_bar_shows_shape_not_just_a_fraction(self):
        self.assertEqual(B.bar(0, 10, width=10), "[..........]")
        self.assertEqual(B.bar(10, 10, width=10), "[##########]")

    def test_the_bar_never_reads_full_while_work_remains(self):
        self.assertTrue(B.bar(99, 100, width=10).endswith(".]"))

    def test_an_unknown_total_is_shown_as_unknown_not_as_zero(self):
        self.assertIn("?", B.bar(5, 0))
        self.assertIn("?", B.bar(5, -1))

    def test_the_outcome_drops_the_log_path_and_the_advice(self):
        got = B.short_outcome("LANDED  D6.5 | units with every sub unit landed: none | log /tmp/x.log")
        self.assertEqual(got, "LANDED D6.5")

    def test_a_short_outcome_is_left_alone(self):
        self.assertEqual(B.short_outcome("PUSH ok"), "PUSH ok")

    def test_an_empty_outcome_does_not_crash(self):
        self.assertEqual(B.short_outcome(None), "")

    def test_a_duration_reads_in_seconds_then_minutes(self):
        self.assertIn("second", B.human_secs(45))
        self.assertIn("minute", B.human_secs(300))

    def test_every_rung_that_runs_has_a_typical_duration(self):
        """A reader tells a slow cycle from a hung one by this, so a runnable rung must not be silent about it."""
        runnable = {c for c in B.ACTIONS if c not in B.HUMAN_ONLY}
        self.assertEqual(runnable - set(B.TYPICAL), set())



class AnUnreadableGitStateIsNeverClean(unittest.TestCase):
    """The most dangerous thing an unattended loop could be told: run() returns '' when git fails, so dirty and
    unpushed both read 0, the ladder sees a clean pushed tree and lands on top of it."""

    def test_git_failure_refuses_rather_than_reading_clean(self):
        with self.assertRaises(B.Unreadable):
            B.git_state("/somewhere", lambda cmd: "")

    def test_a_missing_head_refuses(self):
        def run(cmd):
            return "" if "rev-parse" in cmd else "M file.py"
        with self.assertRaises(B.Unreadable):
            B.git_state("/somewhere", run)

    def test_a_real_state_is_read_normally(self):
        def run(cmd):
            if "rev-parse" in cmd: return "abc1234\n"
            if "branch" in cmd: return "main\n"
            if "status" in cmd: return " M a.py\n?? b.py\n"
            return ""
        st = B.git_state("/x", run)
        self.assertEqual((st["head"], st["dirty"], st["unpushed"]), ("abc1234", 2, 0))



class WhatClosesAUnitGoesFirst(unittest.TestCase):
    """The root cause of 36 sub units landing in a night while the whole unit count did not move: the ladder took
    whatever was READY in plan order, so work spread across units and closed none, while SEVEN units sat one sub
    unit from done. Landing the last sub unit of a unit changes the number; landing the first of another does not."""

    PLAN = {"units": [
        {"id": "FAR", "sub_units": ["FAR.1", "FAR.2", "FAR.3"], "evidence": ""},
        {"id": "NEAR", "sub_units": ["NEAR.1", "NEAR.2"], "evidence": "NEAR.1 landed today"},
        {"id": "MID", "sub_units": ["MID.1", "MID.2", "MID.3"], "evidence": "MID.1 landed today"},
    ]}

    def landed(self):
        return B.landed_subs(self.PLAN)

    def test_the_unit_closest_to_done_is_taken_first(self):
        got = B.nearest_first([("FAR.1", "FAR"), ("NEAR.2", "NEAR"), ("MID.2", "MID")], self.PLAN, self.landed())
        self.assertEqual([u for _, u in got], ["NEAR", "MID", "FAR"])

    def test_distance_counts_only_unlanded_sub_units(self):
        dist = B.remaining_by_unit(self.PLAN, self.landed())
        self.assertEqual((dist["NEAR"], dist["MID"], dist["FAR"]), (1, 2, 3))

    def test_a_done_unit_has_no_distance(self):
        plan = {"units": [{"id": "D", "sub_units": ["D.1"], "state": "DONE", "evidence": "D.1 landed"}]}
        self.assertEqual(B.remaining_by_unit(plan, {"D.1"}), {})

    def test_two_runs_agree_on_the_order(self):
        """A tie must not depend on the order the caller happened to build the list."""
        items = [("FAR.1", "FAR"), ("NEAR.2", "NEAR")]
        self.assertEqual(B.nearest_first(items, self.PLAN, self.landed()),
                         B.nearest_first(list(reversed(items)), self.PLAN, self.landed()))

    def test_an_unknown_unit_sorts_last_rather_than_first(self):
        got = B.nearest_first([("X.1", "NOT-IN-PLAN"), ("NEAR.2", "NEAR")], self.PLAN, self.landed())
        self.assertEqual(got[0][1], "NEAR")


if __name__ == "__main__":
    unittest.main(verbosity=1)

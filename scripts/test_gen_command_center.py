"""Calibration for the three board behaviours that decide what a reader
believes, all of which are decided at render time from a state string.

Written 2026-08-24 because none of them had a check: the generator had 978
lines and no test file, so the rule that a row waiting on a person must not
read as idle lived only in a docstring."""

import json
import pathlib
import tempfile
import unittest

import gen_command_center as gen


def _grid(rows, note="collapsed note"):
    return {"grid": {"source": "s", "sequence": "q",
                     "collapsed_note": note, "rows": rows}}


def _row(rid, state, collapsed=False):
    r = {"id": rid, "what": rid + " work", "state": state, "evidence": "e"}
    if collapsed:
        r["collapsed"] = True
    return r


class GridChips(unittest.TestCase):
    def test_done_ticks(self):
        html = gen.render_grid(_grid([_row("P1", "DONE, 3.4.2")]))
        self.assertIn('class="item tick"', html)
        self.assertIn('class="v pass"', html)

    def test_waiting_on_a_person_is_not_idle(self):
        """The whole point of the row. READY TO RUN and NOT CLEAR both used to
        fall through to idle, which reads as nobody-can-start-this when the
        truth is somebody-has-not-done-this."""
        for state in ("WAITING ON A PERSON, packet ready",
                      "NAMED, READY TO RUN",
                      "NOT CLEAR, ONE DRAFT OUTSTANDING",
                      "BLOCKED BY P7",
                      "OPEN, blocked on the FOUNDER"):
            html = gen.render_grid(_grid([_row("Px", state)]))
            self.assertIn('class="v warn"', html, state)
            self.assertNotIn('class="v idle"', html, state)

    def test_not_started_stays_idle(self):
        html = gen.render_grid(_grid([_row("P11", "NOT STARTED")]))
        self.assertIn('class="v idle"', html)

    def test_absent_grid_renders_nothing(self):
        self.assertEqual(gen.render_grid({}), "")
        self.assertEqual(gen.render_grid({"grid": {"rows": []}}), "")


class GridCollapse(unittest.TestCase):
    def test_collapsed_rows_fold_behind_the_note(self):
        html = gen.render_grid(_grid([
            _row("P1", "DONE", collapsed=True),
            _row("P2", "DONE", collapsed=True),
            _row("P3", "NOT STARTED"),
        ], note="both released and tagged"))
        self.assertIn("<details", html)
        self.assertIn("both released and tagged", html)
        self.assertEqual(html.count("<details"), 1)
        self.assertEqual(html.count("</details>"), 1)
        # the open row is outside the fold
        self.assertLess(html.index("</details>"), html.index("P3"))

    def test_trailing_collapsed_run_is_closed(self):
        html = gen.render_grid(_grid([_row("P1", "DONE", collapsed=True)]))
        self.assertEqual(html.count("<details"), html.count("</details>"))

    def test_counter_still_counts_every_record(self):
        """Percentages are counts of records, never impressions: folding a row
        away must not remove it from the denominator."""
        html = gen.render_grid(_grid([
            _row("P1", "DONE", collapsed=True),
            _row("P2", "NOT STARTED"),
        ]))
        self.assertIn("1 of 2 done", html)


class DeclaredRisks(unittest.TestCase):
    def test_declared_risk_is_rendered_first(self):
        live = {"risks": [{"level": "hot", "title": "instrument health",
                           "what": "w", "why": "y", "action": "a", "when": "n"}],
                "battery": {"remaining": [{"suite": "s", "kind": "k", "detail": "d"}]},
                "corrections": []}
        html = gen.render_risks({"rows": []}, live, {}, {})
        self.assertIn("instrument health", html)
        self.assertIn('class="risk hot"', html)
        self.assertLess(html.index("instrument health"), html.index("s ("))

    def test_unknown_level_falls_back_rather_than_emitting_a_dead_class(self):
        live = {"risks": [{"title": "t", "level": "catastrophic"}],
                "battery": {}, "corrections": []}
        html = gen.render_risks({"rows": []}, live, {}, {})
        self.assertIn('class="risk warm"', html)

    def test_no_risks_key_changes_nothing(self):
        live = {"battery": {}, "corrections": []}
        html = gen.render_risks({"rows": []}, live, {}, {})
        self.assertIn("Risk management", html)


class RowConditions(unittest.TestCase):
    """A conditional row's trigger must reach the page. Added 2026-08-24 after
    two rows were written with triggers and flip conditions that the ledger
    rendered nowhere, which is the same as having no trigger at all."""

    def test_trigger_and_flip_render(self):
        html = gen.render_row_conditions(
            {"trigger": "the first user who asks", "flip_condition": "nobody asks"})
        self.assertIn("STARTS WHEN: the first user who asks", html)
        self.assertIn("DROP IT IF: nobody asks", html)

    def test_row_without_conditions_renders_nothing(self):
        self.assertEqual(gen.render_row_conditions({"id": "X"}), "")

    def test_no_data_is_not_printed_as_a_condition(self):
        """get() defaults to NO-DATA; an absent trigger must not become a
        row that claims to start when NO-DATA happens."""
        self.assertEqual(gen.render_row_conditions({"trigger": None}), "")


class DispositionVocabulary(unittest.TestCase):
    """The founder abolished PARK SOMEDAY on 2026-08-24. The board's vocabulary
    has to be able to SAY the disposition that replaced it, or a scheduled
    experiment falls outside every bucket and the generator refuses to render,
    which is what happened when these rows were first added."""

    def test_scheduled_experiment_is_a_bucket(self):
        self.assertEqual(
            gen.disposition_bucket("SCHEDULED-EXPERIMENT, recorded, non-blocking"),
            "SCHEDULED-EXPERIMENT")

    def test_an_unknown_disposition_still_returns_none(self):
        """The refusal is a feature: a row nobody bucketed must not be
        silently dropped from the counts."""
        self.assertIsNone(gen.disposition_bucket("INVENTED BY SOMEONE"))

    def test_the_old_buckets_still_resolve(self):
        for d in ("BLOCKER", "CARRY", "DONE", "PARKED", "FOUNDER-ACT", "OBSOLETE"):
            self.assertEqual(gen.disposition_bucket(d + " (with a suffix)"), d)


def _day_plan(rows=None):
    return {"date": "2026-08-27", "timezone": "JST", "hours_start": 8, "hours_end": 22,
            "note": "a row is done only with its check quoted",
            "rows": rows if rows is not None else [
                {"id": "DAY-01", "title": "morning pack", "start_hour": 8, "end_hour_p50": 9,
                 "end_hour_p80": 9, "owner": "prior session", "status": "DONE",
                 "done_check": "the zip exists", "evidence": "delivered at 08:47"},
                {"id": "DAY-02", "title": "battery re-run", "start_hour": 9, "end_hour_p50": 11,
                 "end_hour_p80": 13, "owner": "assurance holder", "status": "SCHEDULED",
                 "done_check": "receipt reads success", "evidence": None},
            ]}


class DayGantt(unittest.TestCase):
    """Today, the full day: the hour grain Gantt added for the living plan's
    first step (ROADMAP-REPLACE-2026-08-27.md Phase 2.2). Added 2026-08-27."""

    def test_renders_when_day_plan_present(self):
        html = gen.render_day_gantt({"day_plan": _day_plan()})
        self.assertIn("Today, the full day", html)
        self.assertIn("DAY-01", html)
        self.assertIn("DAY-02", html)
        self.assertIn("prior session", html)

    def test_done_row_gets_the_done_segment(self):
        html = gen.render_day_gantt({"day_plan": _day_plan()})
        self.assertIn('class="seg done"', html)

    def test_scheduled_row_gets_sched_and_est_segments(self):
        html = gen.render_day_gantt({"day_plan": _day_plan()})
        self.assertIn('class="seg sched"', html)
        self.assertIn('class="seg est"', html)

    def test_absent_key_renders_nothing(self):
        """Older LIVE-STATE.json snapshots carry no day_plan key at all, and
        must still render exactly as before: no section, no empty box."""
        self.assertEqual(gen.render_day_gantt({}), "")

    def test_empty_rows_renders_nothing(self):
        self.assertEqual(gen.render_day_gantt({"day_plan": _day_plan(rows=[])}), "")


class WbsToday(unittest.TestCase):
    """docs/plan/WBS-TODAY.md regenerates from the same day_plan the HTML
    Gantt reads, plus the roadmap's short track, so the two can never
    disagree about what today looks like."""

    def _roadmap(self):
        return {"short": [
            {"id": "BR-10", "title": "Rotate disclosed key", "track": "F-founder",
             "start_day": 0, "end_day_p50": 1, "owner": "founder"},
        ]}

    def test_contains_every_day_row_id(self):
        live_state = {"day_plan": _day_plan()}
        text = gen.render_wbs_markdown(live_state, self._roadmap())
        self.assertIn("DAY-01", text)
        self.assertIn("DAY-02", text)

    def test_first_line_names_the_source_and_never_hand_edited(self):
        text = gen.render_wbs_markdown({"day_plan": _day_plan()}, self._roadmap())
        first_line = text.splitlines()[0]
        self.assertIn("scripts/gen_command_center.py", first_line)
        self.assertIn("docs/plan/LIVE-STATE.json", first_line)
        self.assertIn("Never hand edited", first_line)

    def test_short_track_rows_present_too(self):
        text = gen.render_wbs_markdown({"day_plan": _day_plan()}, self._roadmap())
        self.assertIn("BR-10", text)
        self.assertIn("Rotate disclosed key", text)


class Determinism(unittest.TestCase):
    """No datetime.now(), no locale dependent formatting: every field the day
    section and the WBS render comes from the JSON's own values, so running
    either render function twice on the same input must be byte identical."""

    def test_day_gantt_is_deterministic(self):
        live_state = {"day_plan": _day_plan()}
        first = gen.render_day_gantt(live_state)
        second = gen.render_day_gantt(live_state)
        self.assertEqual(first, second)

    def test_wbs_markdown_is_deterministic(self):
        live_state = {"day_plan": _day_plan()}
        roadmap = {"short": [{"id": "BR-10", "title": "t", "track": "F-founder",
                               "start_day": 0, "end_day_p50": 1, "owner": "founder"}]}
        first = gen.render_wbs_markdown(live_state, roadmap)
        second = gen.render_wbs_markdown(live_state, roadmap)
        self.assertEqual(first, second)


def _bench_run(rows=None):
    if rows is not None:
        return {"results": rows}
    return {"results": [
        {"criterion": "install-commands-documented", "subject": "brother", "verdict": "FAIL"},
        {"criterion": "install-commands-documented", "subject": "rival", "verdict": "PASS"},
        {"criterion": "level-adaptation", "subject": "brother", "verdict": "PASS"},
        {"criterion": "level-adaptation", "subject": "rival", "verdict": "FAIL"},
    ]}


class Standings(unittest.TestCase):
    """Where we stand, benchmark wise: renders from docs/benchmarks/latest-run.json's
    flat results list, skips silently when that file is absent, and never
    hardcodes which criterion is a brother-only win or a brother loss."""

    def test_absent_file_renders_nothing(self):
        self.assertEqual(gen.render_standings(None), "")

    def test_empty_results_renders_nothing(self):
        self.assertEqual(gen.render_standings({"results": []}), "")

    def test_brother_listed_first(self):
        html = gen.render_standings(_bench_run())
        self.assertLess(html.index(">brother<"), html.index(">rival<"))

    def test_brother_only_win_is_named(self):
        html = gen.render_standings(_bench_run())
        self.assertIn("Only brother passes: level-adaptation.", html)

    def test_brother_loss_is_named_never_softened(self):
        html = gen.render_standings(_bench_run())
        self.assertIn("Brother loses on: install-commands-documented.", html)

    def test_pass_count_chip_renders(self):
        html = gen.render_standings(_bench_run())
        self.assertIn("PASS 1 of 2", html)

    def test_no_data_counted_when_verdict_is_neither_pass_nor_fail(self):
        rows = [
            {"criterion": "c1", "subject": "brother", "verdict": "PASS"},
            {"criterion": "c1", "subject": "rival", "verdict": "NO-DATA"},
        ]
        html = gen.render_standings(_bench_run(rows))
        self.assertIn("1 NO-DATA", html)

    def test_second_subject_sorted_by_pass_count_descending(self):
        rows = [
            {"criterion": "c1", "subject": "brother", "verdict": "PASS"},
            {"criterion": "c1", "subject": "low", "verdict": "FAIL"},
            {"criterion": "c1", "subject": "high", "verdict": "PASS"},
            {"criterion": "c2", "subject": "high", "verdict": "PASS"},
        ]
        html = gen.render_standings(_bench_run(rows))
        self.assertLess(html.index(">high<"), html.index(">low<"))

    def test_md_named_as_sha_source_when_json_carries_none(self):
        html = gen.render_standings(_bench_run())
        self.assertIn("docs/benchmarks/ATOMIC-BENCHMARK.md", html)

    def test_sha_rendered_when_present_in_json(self):
        rows = [
            {"criterion": "c1", "subject": "brother", "verdict": "PASS"},
            {"criterion": "c1", "subject": "rival", "verdict": "PASS", "sha": "abcdef1234567"},
        ]
        html = gen.render_standings(_bench_run(rows))
        self.assertIn("abcdef1", html)

    def test_caveat_line_always_present(self):
        html = gen.render_standings(_bench_run())
        self.assertIn("options-with-recommendation", html)
        self.assertIn("diagram-by-default", html)

    def test_is_deterministic(self):
        first = gen.render_standings(_bench_run())
        second = gen.render_standings(_bench_run())
        self.assertEqual(first, second)


def _scored_bench_run():
    """A bench run in the new shape (scripts/benchmark_atomic.py, post score
    fields): 'brother' loses install-commands-documented to 'rival', wins
    level-adaptation outright, and carries the score/reasons/borrow_items
    the generator is meant to read."""
    return {
        "results": [
            {"criterion": "install-commands-documented", "subject": "brother", "verdict": "FAIL"},
            {"criterion": "install-commands-documented", "subject": "rival", "verdict": "PASS"},
            {"criterion": "level-adaptation", "subject": "brother", "verdict": "PASS"},
            {"criterion": "level-adaptation", "subject": "rival", "verdict": "FAIL"},
        ],
        "scores": {
            "brother": {"subject": "brother", "score": 5.0, "pass": 1, "fail": 1,
                        "no_data": 0, "covered": 2, "total": 2},
            "rival": {"subject": "rival", "score": 5.0, "pass": 1, "fail": 1,
                      "no_data": 0, "covered": 2, "total": 2},
            "leader": {"subject": "leader", "score": 10.0, "pass": 2, "fail": 0,
                       "no_data": 0, "covered": 2, "total": 2},
        },
        "reasons": [
            {"criterion": "install-commands-documented", "dimension": "onboarding",
             "leaders": ["rival"], "sentence": "rival needs only 1 install command(s); brother needs 2."},
        ],
        "borrow_items": [
            {"id": "borrow-install-commands-documented", "stage": "RESEARCH"},
            {"id": "borrow-something-else", "stage": "BEATEN"},
        ],
    }


class ScoredStandings(unittest.TestCase):
    """The new scored standings table (Part 3 of the founder's atomic
    benchmark order): sorted purely by score, brother highlighted but never
    repositioned ahead of a subject that outscored it, reasons and open
    borrow items surfaced beneath it. Falls back to the legacy table when
    the loaded JSON carries no 'scores' key (an older snapshot)."""

    def test_scored_table_sorts_by_score_not_brother_first(self):
        html = gen.render_standings(_scored_bench_run())
        # leader (10.0) must appear before brother/rival (both 5.0)
        self.assertLess(html.index(">leader<"), html.index("<strong>brother</strong>"))

    def test_brother_is_bolded_not_repositioned(self):
        html = gen.render_standings(_scored_bench_run())
        self.assertIn("<strong>brother</strong>", html)

    def test_score_and_covered_weight_shown_together(self):
        html = gen.render_standings(_scored_bench_run())
        self.assertIn("5.0/10 over 2 of 2", html)

    def test_reasons_line_rendered(self):
        html = gen.render_standings(_scored_bench_run())
        self.assertIn("Why they beat us where they do", html)
        self.assertIn("rival needs only 1 install command(s)", html)

    def test_open_borrow_items_line_rendered(self):
        html = gen.render_standings(_scored_bench_run())
        self.assertIn("Open borrow items: 1", html)
        self.assertIn("docs/benchmarks/BORROW-QUEUE.md", html)

    def test_method_line_discloses_no_data_exclusion(self):
        html = gen.render_standings(_scored_bench_run())
        self.assertIn("NO-DATA excluded from the", html)

    def test_older_snapshot_without_score_fields_skips_silently(self):
        """A latest-run.json from before scores existed must render the
        legacy table, never crash, never show a broken score column."""
        html = gen.render_standings(_bench_run())
        self.assertIn("Only brother passes", html)  # legacy-only line
        self.assertNotIn("Why they beat us where they do", html)
        self.assertNotIn("/10", html)

    def test_is_deterministic(self):
        first = gen.render_standings(_scored_bench_run())
        second = gen.render_standings(_scored_bench_run())
        self.assertEqual(first, second)


def _night_watch(**overrides):
    nw = {
        "armed_at": "2026-08-27T21:09:00+09:00",
        "hard_stop": "2026-08-27T22:00:00Z",
        "hard_stop_note": "07:00 JST 2026-08-28; the founder takes over at 08:00 JST",
        "cron_id": "8a04f452",
        "cron_schedule": "7,22,37,52 * * * * (every 15 minutes)",
        "monitors": [
            {"id": "b001w64uz", "watches": "foreign commits on Brother origin/main"},
            {"id": "bklxb680p", "watches": "battery window: 15 minute load under 28"},
        ],
        "stall_detector_pid": 4175,
        "ticks_done": 0,
        "last_tick_iso": None,
        "last_tick_summary": "not yet ticked; armed this session, first tick due within 15 minutes",
        "what_moved_last_tick": None,
    }
    nw.update(overrides)
    return nw


class NightWatch(unittest.TestCase):
    """Night watch: renders docs/plan/night-watch.json, the overnight
    watchdog's own status file. A cron job owns the file; this section only
    reads it, and must never break the page when the file is absent (an
    older snapshot, or a checkout the watchdog was never armed in)."""

    def test_renders_with_the_real_field_shape(self):
        html = gen.render_night_watch(_night_watch())
        self.assertIn("Night watch", html)
        self.assertIn("07:00 JST 2026-08-28; the founder takes over at 08:00 JST", html)
        self.assertIn("7,22,37,52 * * * * (every 15 minutes)", html)
        self.assertIn("b001w64uz", html)
        self.assertIn("foreign commits on Brother origin/main", html)
        self.assertIn("bklxb680p", html)
        self.assertIn("battery window: 15 minute load under 28", html)
        self.assertIn("4175", html)

    def test_absent_renders_nothing(self):
        self.assertEqual(gen.render_night_watch(None), "")
        self.assertEqual(gen.render_night_watch({}), "")

    def test_null_last_tick_and_what_moved_use_fallback_text(self):
        html = gen.render_night_watch(_night_watch())
        self.assertIn("not yet ticked", html)
        self.assertIn("nothing yet", html)
        self.assertNotIn(">None<", html)
        self.assertNotIn(">null<", html)

    def test_present_last_tick_and_what_moved_render_verbatim(self):
        html = gen.render_night_watch(_night_watch(
            last_tick_iso="2026-08-27T21:24:00+09:00",
            what_moved_last_tick="battery re-run started",
        ))
        self.assertIn("2026-08-27T21:24:00+09:00", html)
        self.assertIn("battery re-run started", html)
        self.assertNotIn("not yet ticked", html)
        self.assertNotIn("nothing yet", html)

    def test_caveat_line_always_present(self):
        html = gen.render_night_watch(_night_watch())
        self.assertIn("never live process state", html)

    def test_is_deterministic(self):
        first = gen.render_night_watch(_night_watch())
        second = gen.render_night_watch(_night_watch())
        self.assertEqual(first, second)


class NightWatchLoad(unittest.TestCase):
    """load_night_watch() reads docs/plan/night-watch.json from disk. Same
    isolation shape as any other path constant read at module scope: swap
    the constant for a temp path in setUp, restore it in tearDown, so no
    test ever touches the real file."""

    def setUp(self):
        self._orig_path = gen.NIGHT_WATCH_PATH
        self._tmpdir = tempfile.TemporaryDirectory()
        gen.NIGHT_WATCH_PATH = pathlib.Path(self._tmpdir.name) / "night-watch.json"

    def tearDown(self):
        gen.NIGHT_WATCH_PATH = self._orig_path
        self._tmpdir.cleanup()

    def test_absent_file_returns_none(self):
        self.assertIsNone(gen.load_night_watch())

    def test_present_file_returns_the_parsed_dict(self):
        gen.NIGHT_WATCH_PATH.write_text(json.dumps(_night_watch()), encoding="utf-8")
        loaded = gen.load_night_watch()
        self.assertEqual(loaded["cron_id"], "8a04f452")

    def test_unparseable_file_returns_none(self):
        gen.NIGHT_WATCH_PATH.write_text("{not json", encoding="utf-8")
        self.assertIsNone(gen.load_night_watch())


def _day_row(rid, status, depends_on=None, event=None):
    r = {"id": rid, "title": rid + " work", "owner": "o", "status": status,
         "start_hour": 6, "end_hour_p50": 7, "end_hour_p80": 7,
         "done_check": "c"}
    if depends_on is not None:
        r["depends_on"] = depends_on
    if event is not None:
        r["event"] = event
    return r


class ReadySet(unittest.TestCase):
    """READY-SET-STANDARD-2026-08-28.md: the board computes what is runnable
    now, so a verdict is an event and never a wait."""

    def test_no_deps_scheduled_row_is_ready(self):
        ready = gen.ready_state([_day_row("A", "SCHEDULED")])
        self.assertEqual(ready["A"], "READY")

    def test_met_deps_are_ready_and_unmet_block_by_name(self):
        rows = [_day_row("A", "DONE"), _day_row("B", "SCHEDULED"),
                _day_row("C", "SCHEDULED", depends_on=["A"]),
                _day_row("D", "SCHEDULED", depends_on=["B"])]
        ready = gen.ready_state(rows)
        self.assertEqual(ready["C"], "READY")
        self.assertEqual(ready["D"], "BLOCKED-BY B")

    def test_a_blocked_row_never_reads_ready(self):
        """The failure direction that matters: a dep typo must block, never
        unblock."""
        ready = gen.ready_state(
            [_day_row("X", "SCHEDULED", depends_on=["NO-SUCH-ROW"])])
        self.assertNotEqual(ready["X"], "READY")
        self.assertIn("(unknown)", ready["X"])

    def test_event_wait_only_after_deps_are_met(self):
        rows = [_day_row("A", "DONE"),
                _day_row("E", "SCHEDULED", depends_on=["A"],
                         event="battery verdict")]
        self.assertEqual(gen.ready_state(rows)["E"],
                         "EVENT-WAIT: battery verdict")

    def test_non_scheduled_rows_report_their_own_status(self):
        ready = gen.ready_state([_day_row("F", "IN FLIGHT")])
        self.assertEqual(ready["F"], "IN FLIGHT")

    def test_wbs_table_carries_the_ready_column(self):
        lines = gen.render_wbs_day_table(
            {"rows": [_day_row("A", "SCHEDULED")]})
        self.assertIn("| Ready |", lines[0])
        self.assertTrue(any("READY" in ln for ln in lines[2:]))


def _dep_row(rid, depends_on=None, owner="o", status="SCHEDULED", event=None):
    r = {"id": rid, "title": rid, "owner": owner, "status": status,
         "done_check": "c"}
    if depends_on is not None:
        r["depends_on"] = depends_on
    if event is not None:
        r["event"] = event
    return r


class DownstreamDependents(unittest.TestCase):
    """compute_downstream_dependents: a small hand-computable diamond, A <-
    B <- C, D also depends on B, E independent. Added for the graph loops
    section's ready-queue pull order (READY-SET-STANDARD-2026-08-28.md:
    among READY nodes, pull the one with the most downstream dependents)."""

    def _rows(self):
        return [_dep_row("A"), _dep_row("B", depends_on=["A"]),
                _dep_row("C", depends_on=["B"]), _dep_row("D", depends_on=["B"]),
                _dep_row("E")]

    def test_hand_computable_counts(self):
        counts = gen.compute_downstream_dependents(self._rows())
        self.assertEqual(counts["A"], 3)  # B, C, D
        self.assertEqual(counts["B"], 2)  # C, D
        self.assertEqual(counts["C"], 0)
        self.assertEqual(counts["D"], 0)
        self.assertEqual(counts["E"], 0)

    def test_unknown_dep_id_is_not_counted(self):
        """A dep id naming no row cannot itself be counted as anyone's
        downstream dependent; it just has no entry to add to."""
        counts = gen.compute_downstream_dependents(
            [_dep_row("X", depends_on=["NO-SUCH-ROW"])])
        self.assertEqual(counts["X"], 0)
        self.assertNotIn("NO-SUCH-ROW", counts)


class TwoLaneCapWarnings(unittest.TestCase):
    """The two-lane cap (FINISH FIRST) as a mechanical board condition: a
    breach warning past two IN-FLIGHT rows, an idle warning when nothing is
    in flight while a row computes READY."""

    def test_breach_warning_past_two_in_flight(self):
        rows = [_dep_row(f"F{i}", status="IN-FLIGHT") for i in range(3)]
        html = gen.render_graph_loops({"day_plan": {"rows": rows}})
        self.assertIn('class="v warn">BREACH', html)
        self.assertIn("3 lanes", html)

    def test_no_breach_at_exactly_two(self):
        rows = [_dep_row(f"F{i}", status="IN-FLIGHT") for i in range(2)]
        html = gen.render_graph_loops({"day_plan": {"rows": rows}})
        self.assertNotIn("BREACH", html)

    def test_idle_warning_when_zero_in_flight_and_ready_exists(self):
        html = gen.render_graph_loops({"day_plan": {"rows": [_dep_row("A")]}})
        self.assertIn('class="v warn">IDLE', html)

    def test_no_idle_warning_when_a_lane_is_in_flight(self):
        rows = [_dep_row("F1", status="IN-FLIGHT"), _dep_row("A")]
        html = gen.render_graph_loops({"day_plan": {"rows": rows}})
        self.assertNotIn("IDLE", html)


class FounderLaneMembership(unittest.TestCase):
    """Founder lane membership: owner names the founder or the team, or the
    row carries a non-empty event. These rows are never pulled by a
    session, so they render in their own column, never the ready queue."""

    def test_founder_owner_is_founder_lane(self):
        self.assertTrue(gen.is_founder_lane_row({"owner": "founder"}))

    def test_team_owner_is_founder_lane(self):
        self.assertTrue(gen.is_founder_lane_row({"owner": "team"}))

    def test_event_field_is_founder_lane_even_without_the_words(self):
        self.assertTrue(gen.is_founder_lane_row(
            {"owner": "brother-76", "event": "team retest results arrive"}))

    def test_ordinary_session_owner_is_not_founder_lane(self):
        self.assertFalse(gen.is_founder_lane_row({"owner": "sonnet writer"}))

    def test_founder_row_renders_in_its_own_column(self):
        rows = [_dep_row("F-X", owner="founder", event="founder rules on the redesign")]
        html = gen.render_graph_loops({"day_plan": {"rows": rows}})
        self.assertIn("The founder lane, never pulled by a session", html)
        self.assertIn("founder rules on the redesign", html)


class ReadyQueueOrder(unittest.TestCase):
    """The ready queue is sorted by downstream dependents descending, ties
    broken by id."""

    def test_ready_queue_orders_by_dependents_desc(self):
        rows = [_dep_row("D"), _dep_row("A"),
                _dep_row("B", depends_on=["A"]), _dep_row("C", depends_on=["B"])]
        html = gen.render_graph_loops({"day_plan": {"rows": rows}})
        self.assertIn("unblocks 2", html)
        self.assertLess(html.index(">A<"), html.index(">D<"))

    def test_ties_break_by_id(self):
        html = gen.render_graph_loops(
            {"day_plan": {"rows": [_dep_row("Z"), _dep_row("Y")]}})
        self.assertLess(html.index(">Y<"), html.index(">Z<"))


class GraphLoopsAbsent(unittest.TestCase):
    def test_absent_day_plan_renders_nothing(self):
        self.assertEqual(gen.render_graph_loops({}), "")

    def test_empty_rows_renders_nothing(self):
        self.assertEqual(gen.render_graph_loops({"day_plan": {"rows": []}}), "")


class LoopCloseRing(unittest.TestCase):
    """The loop-close ring is quoted verbatim from
    READY-SET-STANDARD-2026-08-28.md, once, and never depends on the data."""

    def test_ring_wording_matches_the_standard(self):
        html = gen.render_graph_loops({"day_plan": {"rows": [_dep_row("A")]}})
        self.assertIn("scripts/check_all.sh", html)
        self.assertIn("scripts/task_watchdog.py exit 0", html)
        self.assertIn("memory milestone written", html)


class WbsUnblocksColumn(unittest.TestCase):
    """WBS-TODAY.md's day table gains an Unblocks column carrying the same
    downstream dependents count the HTML ready queue sorts by."""

    def test_unblocks_column_present_and_counted(self):
        rows = [_dep_row("A"), _dep_row("B", depends_on=["A"])]
        lines = gen.render_wbs_day_table({"rows": rows})
        self.assertIn("| Unblocks |", lines[0])
        row_a = next(ln for ln in lines[2:] if ln.startswith("| A |"))
        cells = [c.strip() for c in row_a.split("|")]
        self.assertEqual(cells[7], "1")


if __name__ == "__main__":
    # Moved here from mid-file 2026-08-27: it used to sit right after
    # DeclaredRisks, so unittest.main()'s default loader (which reads
    # sys.modules['__main__'] at the moment it is called) never saw any
    # class defined below that point. RowConditions and DispositionVocabulary
    # had been silently skipped by every direct `python3
    # scripts/test_gen_command_center.py` run (the exact invocation
    # scripts/check_all.sh uses) since the day they were added. Discovered
    # while adding DayGantt/WbsToday/Determinism below them and finding the
    # new tests did not run either.
    unittest.main()

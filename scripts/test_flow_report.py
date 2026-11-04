#!/usr/bin/env python3
"""FLOW1.c: effort tiers are earned by measured catch per cost (scripts/flow_report.py, docs/plan/EFFORT-TIERS.json).

Every test runs the report the way its caller does: `python3 scripts/flow_report.py --ledger P --tiers T`, asserting the
exit code and the printed lines. Ledgers are written by session_relay itself (append, and once the `record` CLI with a
text `defect_ids`, the shape the CLI really writes), so the fixtures cannot drift from the relay's event shapes.
One class, each test isolating ONE guard so that only that guard can refuse it:
  CatchPerCost   a defect reported by two stages is counted once, for the later one; missing numbers read NO-DATA, never
                 0; an id-less, disagreeing or unreadable report blocks the figure; an unreadable ledger or tier file
                 reads NO-DATA (exit 2); a tier cannot skip a stage for a floor kind, nor skip review, the whole gate or
                 the frozen harness; a floor kind wins its classification; a skipped stage is JUSTIFIED only at
                 min_units resolvable units with 0 unique catches.
Fixtures live in the folder flow1c under the one scratch root (export_public.scratch_root) and are removed after.
Run: python3 -B scripts/test_flow_report.py
"""
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import export_public  # noqa: E402
import session_relay as sr  # noqa: E402

REPORT = os.path.join(HERE, "flow_report.py")
RELAY = os.path.join(HERE, "session_relay.py")
REAL_TIERS = os.path.join(os.path.dirname(HERE), "docs", "plan", "EFFORT-TIERS.json")
T0 = 1700000000.0
NOT_SKIPPABLE = "only mutation_sweep/simulated_check may be skipped"


def _scratch():
    root = os.path.join(export_public.scratch_root(), "flow1c")
    try:
        os.makedirs(root, exist_ok=True)
        return tempfile.mkdtemp(prefix="catch-", dir=root)
    except OSError:     # a read only home: fall back to the system default rather than skip
        return tempfile.mkdtemp(prefix="catch-")


def tiers(**over):
    """A small tier file: one floor kind, one light kind, a light tier skipping the sweep, and min_units 2."""
    data = {
        "min_units": 2,
        "kinds": {
            "sec": {"floor": True, "prefixes": ["SEC1"], "ids": []},
            "doc": {"floor": False, "prefixes": ["DOC1"], "ids": []},
        },
        "tiers": [
            {"name": "full", "kinds": ["sec"], "skips": [], "measured_catch": "NO-DATA", "flip_condition": "never"},
            {"name": "light", "kinds": ["doc"], "skips": ["mutation_sweep"], "measured_catch": "NO-DATA",
             "flip_condition": "one unique catch"},
        ],
    }
    data.update(over)
    return data


class CatchPerCost(unittest.TestCase):
    def setUp(self):
        self.dir = _scratch()
        self.addCleanup(shutil.rmtree, self.dir, True)
        self.ledger = os.path.join(self.dir, "relay.json")
        self.tiers = os.path.join(self.dir, "tiers.json")
        self.write_tiers(tiers())

    def write_tiers(self, data):
        with open(self.tiers, "w", encoding="utf-8") as fh:
            fh.write(data if isinstance(data, str) else json.dumps(data))

    def put(self, **event):
        sr.append(self.ledger, event, session="S")

    def review(self, unit, rnd=1, verdict="PASS", **extra):
        self.put(kind="stage", stage="review", unit=unit, round=rnd, verdict=verdict, at_unix=T0 + rnd, **extra)

    def gate(self, unit, name="whole", status="PASS", minutes=10, **extra):
        self.put(kind="gate", gate=name, unit=unit, status=status, started_unix=T0, ended_unix=T0 + 60 * minutes, **extra)

    def sweep(self, unit, **extra):
        self.gate(unit, name="mutation_sweep", status="PASS", **extra)

    def run_report(self, ledger=None, tiers_path=None):
        env = dict(os.environ, PYTHONDONTWRITEBYTECODE="1")
        p = subprocess.run([sys.executable, "-B", REPORT, "--ledger", ledger or self.ledger,
                            "--tiers", tiers_path or self.tiers], capture_output=True, text=True, env=env)
        return p.returncode, p.stdout, p.stderr

    def lines(self, **kw):
        rc, out, err = self.run_report(**kw)
        self.assertEqual(rc, 0, err)
        return out.splitlines()

    def stage(self, name, **kw):
        return next(l for l in self.lines(**kw) if l.startswith("stage %s:" % name))

    def verdict(self, tier):
        return next(l for l in self.lines() if l.startswith("  tier %s:" % tier))

    def refused(self, stderr_has):
        rc, out, err = self.run_report()
        self.assertEqual(rc, 2, out + err)
        self.assertTrue(err.startswith("NO-DATA:"), err)
        self.assertEqual(out, "", "a refused report prints no figure")
        self.assertIn(stderr_has, err)

    def skipping(self, stage):
        self.review("DOC1.a", defects=0)
        t = tiers()
        t["tiers"][1]["skips"] = [stage]
        self.write_tiers(t)

    # ---- the catch: counted once, for the later stage -------------------------------------------------------------

    def test_a_defect_two_stages_reported_is_credited_to_the_later_one_only(self):
        self.review("SEC1.a", defect_ids=["X"])
        self.gate("SEC1.a", defect_ids=["X"])
        self.assertIn("unique catch 0, also caught later 1,", self.stage("review"))
        self.assertIn("unique catch 1, also caught later 0,", self.stage("whole_gate"))

    def test_each_stage_keeps_what_only_it_reported(self):
        self.review("SEC1.a", defect_ids=["X", "Y"])
        self.gate("SEC1.a", defect_ids=["Y", "Z"])
        self.assertIn("unique catch 1, also caught later 1,", self.stage("review"))
        self.assertIn("unique catch 2, also caught later 0,", self.stage("whole_gate"))

    def test_the_same_id_in_two_units_is_two_defects(self):
        self.review("SEC1.a", defect_ids=["X"])
        self.review("SEC1.b", defect_ids=["X"])
        self.assertIn("unique catch 2,", self.stage("review"))

    def test_the_same_id_in_two_rounds_of_one_stage_is_one_defect(self):
        self.review("SEC1.a", rnd=1, defect_ids=["X"])
        self.review("SEC1.a", rnd=2, defect_ids=["X"])
        self.assertIn("unique catch 1,", self.stage("review"))

    # ---- missing numbers read NO-DATA, never 0 ---------------------------------------------------------------------

    def test_a_stage_that_left_no_numbers_reads_no_data_not_zero(self):
        self.review("SEC1.a")
        line = self.stage("review")
        self.assertIn("unique catch NO-DATA (0 of 1 units resolvable)", line)
        self.assertIn("minutes NO-DATA, tokens NO-DATA, minutes per unique catch NO-DATA", line)
        self.assertNotIn("unique catch 0", line)

    def test_a_count_above_zero_without_ids_reads_no_data(self):
        self.review("SEC1.a", defects=2)
        self.assertIn("unique catch NO-DATA (0 of 1 units resolvable)", self.stage("review"))

    def test_a_reported_zero_is_a_zero(self):
        self.review("SEC1.a", defects=0)
        self.assertIn("unique catch 0, also caught later 0,", self.stage("review"))

    def test_ids_that_disagree_with_the_count_read_no_data(self):
        self.review("SEC1.a", defects=3, defect_ids=["X"])
        self.assertIn("unique catch NO-DATA (0 of 1 units resolvable)", self.stage("review"))

    def test_an_id_that_is_not_text_reads_no_data(self):
        self.review("SEC1.a", defect_ids=["X", 7])
        self.assertIn("unique catch NO-DATA (0 of 1 units resolvable)", self.stage("review"))

    def test_a_blank_id_reads_no_data(self):
        self.review("SEC1.a", defect_ids=["X", " "])
        self.assertIn("unique catch NO-DATA (0 of 1 units resolvable)", self.stage("review"))

    def test_a_count_that_is_not_a_whole_number_reads_no_data(self):
        self.gate("SEC1.a", defects=True, defect_ids=["X"])      # a gate event does not validate `defects`; the report must
        self.assertIn("unique catch NO-DATA (0 of 1 units resolvable)", self.stage("whole_gate"))

    def test_a_later_stage_without_ids_blocks_the_earlier_stages_credit(self):
        self.review("SEC1.a", defect_ids=["X"])
        self.gate("SEC1.a")                                      # a gate that left no ids may have reported X too
        self.assertIn("unique catch NO-DATA (0 of 1 units resolvable)", self.stage("review"))

    def test_a_round_without_ids_keeps_the_stage_unreadable_even_if_a_later_round_has_ids(self):
        self.review("SEC1.a", rnd=1)
        self.review("SEC1.a", rnd=2, defect_ids=["X"])
        self.assertIn("unique catch NO-DATA (0 of 1 units resolvable)", self.stage("review"))

    def test_one_unreadable_unit_leaves_the_others_counted_and_labeled(self):
        self.review("SEC1.a", defect_ids=["X"])
        self.review("SEC1.b")
        self.assertIn("unique catch NO-DATA (1 of 2 units resolvable, 1 so far)", self.stage("review"))

    def test_a_stage_with_no_events_reads_no_data(self):
        self.review("SEC1.a", defect_ids=["X"])
        self.assertIn("0 events, 0 units, defects reported NO-DATA (no events), unique catch NO-DATA (no events)",
                      self.stage("simulated_check"))

    # ---- cost -----------------------------------------------------------------------------------------------------

    def test_minutes_per_unique_catch_divides_the_stage_minutes(self):
        self.gate("SEC1.a", minutes=30, defect_ids=["X", "Y"])
        line = self.stage("whole_gate")
        self.assertIn("minutes 30,", line)
        self.assertIn("minutes per unique catch 15.0", line)

    def test_a_known_catch_with_unknown_minutes_is_no_data_not_a_rate(self):
        self.review("SEC1.a", defect_ids=["X"])
        self.assertIn("minutes NO-DATA, tokens NO-DATA, minutes per unique catch NO-DATA", self.stage("review"))

    def test_zero_unique_catches_for_known_minutes_says_so(self):
        self.gate("SEC1.a", minutes=30, defects=0)
        self.assertIn("minutes per unique catch none (0 unique catches in 30 minutes)", self.stage("whole_gate"))

    def test_tokens_per_unique_catch_divides_the_stage_tokens(self):
        self.review("SEC1.a", tokens=900, defect_ids=["X", "Y", "Z"])
        line = self.stage("review")
        self.assertIn("tokens 900,", line)
        self.assertIn("tokens per unique catch 300.0", line)

    def test_a_known_catch_with_unknown_tokens_is_no_data_not_a_rate(self):
        self.review("SEC1.a", minutes=5, defect_ids=["X"])
        self.assertIn("tokens NO-DATA, minutes per unique catch 5.0, tokens per unique catch NO-DATA",
                      self.stage("review"))

    def test_zero_unique_catches_for_known_tokens_says_so(self):
        self.review("SEC1.a", tokens=900, defects=0)
        self.assertIn("tokens per unique catch none (0 unique catches in 900 tokens)", self.stage("review"))

    def test_minutes_with_unknown_catch_is_no_data_not_a_rate(self):
        self.gate("SEC1.a", minutes=30)
        line = self.stage("whole_gate")
        self.assertIn("minutes 30,", line)
        self.assertIn("minutes per unique catch NO-DATA", line)

    def test_stage_event_minutes_are_summed_and_a_partial_token_total_is_labeled(self):
        self.review("SEC1.a", minutes=12.5, tokens=1000, defects=0)
        self.review("SEC1.b", minutes=7.5, defects=0)
        line = self.stage("review")
        self.assertIn("minutes 20,", line)
        self.assertIn("tokens NO-DATA (1 of 2 events reported, 1000 so far)", line)

    # ---- the relay's own CLI writes the ledger ---------------------------------------------------------------------

    def record(self, kind, *fields):
        args = [sys.executable, "-B", RELAY, "record", "--ledger", self.ledger, "--session", "S", "--kind", kind]
        for f in fields:
            args += ["--field", f]
        p = subprocess.run(args, capture_output=True, text=True)
        self.assertEqual(p.returncode, 0, p.stderr)

    # ---- a run that did not PASS and named no defect is not a measurement (review round 1, F1) ------------------------

    def test_failed_sweeps_with_no_defect_named_do_not_earn_the_light_tier(self):
        for unit in ("DOC1.a", "DOC1.b"):
            self.record("gate", "gate=mutation_sweep", "unit=" + unit, "status=FAIL", "started_unix=1000",
                        "ended_unix=1600", "defects=0")
        out = "\n".join(self.lines())
        self.assertIn("unique catch NO-DATA (0 of 2 units resolvable)", out)
        self.assertEqual(self.verdict("light"), "  tier light: NO-DATA")

    def test_a_failed_review_with_no_defect_named_is_no_data_not_a_zero(self):
        self.record("stage", "stage=review", "unit=SEC1.a", "round=1", "verdict=FAIL", "at_unix=1", "defects=0")
        self.assertIn("unique catch NO-DATA (0 of 1 units resolvable)", self.stage("review"))

    def test_a_failed_run_that_named_its_defects_is_a_catch(self):
        self.record("gate", "gate=whole", "unit=SEC1.a", "status=FAIL", "started_unix=1", "ended_unix=61",
                    "defect_ids=X")
        self.assertIn("unique catch 1,", self.stage("whole_gate"))

    def test_a_passed_run_with_no_defect_is_a_measured_zero(self):
        self.record("gate", "gate=whole", "unit=SEC1.a", "status=PASS", "started_unix=1", "ended_unix=61", "defects=0")
        self.assertIn("unique catch 0,", self.stage("whole_gate"))

    def test_a_ledger_written_by_the_relay_cli_with_text_ids_reports(self):
        self.record("stage", "stage=review", "unit=SEC1.a", "round=1", "verdict=FAIL", "at_unix=1", "defect_ids=X,Y")
        self.record("gate", "gate=whole", "unit=SEC1.a", "status=FAIL", "started_unix=1", "ended_unix=61", "defect_ids=Y")
        self.assertIn("unique catch 1, also caught later 1,", self.stage("review"))
        self.assertIn("unique catch 1, also caught later 0,", self.stage("whole_gate"))

    def test_an_empty_text_id_list_from_the_relay_cli_is_zero_defects(self):
        self.record("stage", "stage=review", "unit=SEC1.a", "round=1", "verdict=PASS", "at_unix=1", "defect_ids=")
        self.assertIn("unique catch 0, also caught later 0,", self.stage("review"))

    def test_a_text_id_list_with_an_empty_piece_reads_no_data(self):
        self.record("stage", "stage=review", "unit=SEC1.a", "round=1", "verdict=PASS", "at_unix=1", "defect_ids=X,,Y")
        self.assertIn("unique catch NO-DATA (0 of 1 units resolvable)", self.stage("review"))

    # ---- unreadable inputs ----------------------------------------------------------------------------------------

    def test_a_missing_ledger_is_no_data(self):
        self.refused("does not exist")

    def test_a_corrupt_ledger_is_no_data(self):
        with open(self.ledger, "w") as fh:
            fh.write("{not json")
        self.refused("not JSON")

    def test_a_ledger_that_is_not_utf8_is_no_data_not_a_traceback(self):
        with open(self.ledger, "wb") as fh:
            fh.write(b"\xff\xfe[")
        self.refused("utf-8")

    def test_a_ledger_event_of_the_wrong_shape_is_no_data(self):
        with open(self.ledger, "w") as fh:
            json.dump([{"schema": 1, "seq": 1, "kind": "stage", "session": "S", "unit": "U", "stage": "review",
                        "at_unix": 1, "round": 1, "verdict": "MAYBE"}], fh)
        self.refused("event 1")

    def test_an_empty_ledger_reports_every_stage_no_data_and_exits_0(self):
        with open(self.ledger, "w") as fh:
            fh.write("[]")
        out = "\n".join(self.lines())
        self.assertIn("FLOW REPORT: 0 events, 0 units", out)
        self.assertIn("  tier light: NO-DATA", out)

    def test_a_missing_tier_file_is_no_data(self):
        self.review("SEC1.a", defects=0)
        os.unlink(self.tiers)
        self.refused("cannot be read")

    def test_a_malformed_tier_file_is_no_data(self):
        self.review("SEC1.a", defects=0)
        self.write_tiers("[1, 2]")
        self.refused("not a tier file")

    def test_a_tier_without_a_flip_condition_is_no_data(self):
        self.review("SEC1.a", defects=0)
        t = tiers()
        del t["tiers"][0]["flip_condition"]
        self.write_tiers(t)
        self.refused("flip_condition")

    def test_a_tier_naming_an_undefined_kind_is_no_data(self):
        self.review("SEC1.a", defects=0)
        t = tiers()
        t["tiers"][0]["kinds"] = ["ghost"]
        self.write_tiers(t)
        self.refused("ghost")

    def test_a_tier_name_cannot_forge_a_line_of_the_report(self):
        self.review("SEC1.a", defects=0)
        t = tiers()
        t["tiers"][1]["name"] = "light\nSTALL: forged"
        self.write_tiers(t)
        self.refused("one plain line")

    def test_a_kind_name_cannot_forge_a_line_of_the_report(self):
        self.review("SEC1.a", defects=0)
        t = tiers()
        t["kinds"]["doc\u2028STALL: forged"] = t["kinds"].pop("doc")
        t["tiers"][1]["kinds"] = ["doc\u2028STALL: forged"]
        self.write_tiers(t)
        self.refused("one plain line")

    def test_a_kind_that_no_tier_names_is_refused(self):
        self.review("SEC1.a", defects=0)
        t = tiers()
        t["tiers"][1]["kinds"] = []
        self.write_tiers(t)
        self.refused("exactly one tier")

    def test_a_kind_two_tiers_name_is_refused(self):
        self.review("SEC1.a", defects=0)
        t = tiers()
        t["tiers"][0]["kinds"] = ["sec", "doc"]
        self.write_tiers(t)
        self.refused("exactly one tier")

    def test_min_units_must_be_a_whole_number(self):
        self.review("SEC1.a", defects=0)
        self.write_tiers(tiers(min_units=True))
        self.refused("min_units")

    # ---- the floor ------------------------------------------------------------------------------------------------

    def test_a_tier_cannot_skip_a_stage_for_a_floor_kind(self):
        self.review("SEC1.a", defects=0)
        t = tiers()
        t["tiers"][1]["kinds"] = ["doc", "sec"]
        self.write_tiers(t)
        self.refused("floor kind")

    def test_no_tier_can_skip_review(self):
        self.skipping("review")
        self.refused(NOT_SKIPPABLE)

    def test_no_tier_can_skip_the_whole_gate(self):
        self.skipping("whole_gate")
        self.refused(NOT_SKIPPABLE)

    def test_no_tier_can_skip_the_frozen_harness(self):
        self.skipping("harness_frozen")
        self.refused(NOT_SKIPPABLE)

    def test_no_tier_can_skip_a_stage_that_does_not_exist(self):
        self.skipping("coffee_break")
        self.refused("coffee_break")

    def test_a_floor_kind_wins_its_classification_whatever_the_file_order(self):
        t = tiers()
        t["kinds"] = {"doc": {"floor": False, "prefixes": ["DOC1"], "ids": []},          # listed FIRST on purpose
                      "sec": {"floor": True, "prefixes": ["SEC1"], "ids": ["DOC1.c"]}}
        self.write_tiers(t)
        self.review("DOC1.a", defects=0)
        self.review("DOC1.c", defects=0)                                                  # claimed by both kinds
        out = "\n".join(self.lines())
        self.assertIn("tier full: kinds sec, 1 units in the ledger", out)
        self.assertIn("tier light: kinds doc, 1 units in the ledger", out)

    def test_a_prefix_matches_the_whole_family_name_not_a_longer_one(self):
        self.review("SEC10.a", defects=0)
        self.assertIn("unclassified units (take the full path): SEC10.a", "\n".join(self.lines()))

    def test_a_unit_no_kind_claims_is_listed_and_takes_the_full_path(self):
        self.review("ZZZ9.a", defects=0)
        out = "\n".join(self.lines())
        self.assertIn("unclassified units (take the full path): ZZZ9.a", out)
        self.assertIn("tier full: kinds sec, 0 units in the ledger", out)

    # ---- the tier verdict -----------------------------------------------------------------------------------------

    def two_swept_units(self, second_ids=None):
        self.sweep("DOC1.a", defects=0)
        self.sweep("DOC1.b", **({"defect_ids": second_ids} if second_ids else {"defects": 0}))

    def test_a_skipped_stage_with_enough_units_and_no_unique_catch_is_justified(self):
        self.two_swept_units()
        self.assertEqual(self.verdict("light"), "  tier light: JUSTIFIED")

    def test_a_unique_catch_in_the_skipped_stage_is_not_justified(self):
        self.two_swept_units(second_ids=["Q"])
        self.assertEqual(self.verdict("light"), "  tier light: NOT JUSTIFIED")
        self.assertIn("unique catch 1 over 2 resolvable of 2 units (need 2)",
                      next(l for l in self.lines() if l.startswith("  skipped stage mutation_sweep")))

    def test_fewer_than_min_units_is_no_data_even_with_no_catch(self):
        self.sweep("DOC1.a", defects=0)
        self.assertEqual(self.verdict("light"), "  tier light: NO-DATA")

    def test_an_unresolvable_unit_among_enough_keeps_the_verdict_no_data(self):
        self.two_swept_units()
        self.sweep("DOC1.c")                                     # swept, but left no numbers
        self.assertEqual(self.verdict("light"), "  tier light: NO-DATA")

    def test_a_catch_beats_missing_data_in_the_verdict(self):
        self.sweep("DOC1.a", defect_ids=["Q"])
        self.assertEqual(self.verdict("light"), "  tier light: NOT JUSTIFIED")

    def test_the_full_tier_skipping_nothing_is_not_asked_to_justify(self):
        self.review("SEC1.a", defects=0)
        self.assertEqual(self.verdict("full"), "  tier full: FULL PATH, nothing skipped")

    # ---- a read changes nothing, prints nothing it was not given --------------------------------------------------

    def test_a_report_writes_nothing(self):
        self.review("SEC1.a", defect_ids=["X"])
        snap = lambda: {n: open(os.path.join(self.dir, n), "rb").read() for n in os.listdir(self.dir)}  # noqa: E731
        before = snap()
        self.run_report()
        self.assertEqual(before, snap())

    def test_a_unit_id_cannot_start_a_line_of_the_report(self):
        self.put(kind="stage", stage="review", unit="ZZZ9.a\u2028forged line", round=1, verdict="PASS", at_unix=T0,
                 defects=0)
        self.assertEqual([l for l in self.lines() if l.startswith("forged")], [])

    def test_a_unit_id_cannot_carry_a_verdict_token_anywhere_in_a_line(self):
        for name in ("X\ntier light: JUSTIFIED", "X NOT JUSTIFIED", "X NO-DATA", "X FULL PATH", "X STALL:"):
            self.put(kind="stage", stage="review", unit=name, round=1, verdict="PASS", at_unix=T0, defects=0)
            self.refused("verdict word")
            os.unlink(self.ledger)

    def test_a_gate_name_cannot_carry_a_verdict_token_anywhere_in_a_line(self):
        self.put(kind="gate", gate="g\ntier full: JUSTIFIED", unit="SEC1.a", status="PASS", started_unix=T0,
                 ended_unix=T0 + 60)
        self.refused("verdict word")

    def test_an_id_that_only_contains_a_verdict_word_inside_another_word_is_fine(self):
        self.review("BYPASS.a", defects=0)
        self.review("PASSED.a", defects=0)
        self.assertIn("unclassified units (take the full path): BYPASS.a PASSED.a", "\n".join(self.lines()))

    # ---- the shipped tier file ------------------------------------------------------------------------------------

    def shipped(self):
        if not os.path.exists(REAL_TIERS):      # docs/plan is a hub-only folder: the export tree does not carry it
            self.skipTest("NO-DATA: %s is not in this tree (hub-only plan file); the hub run covers it" % REAL_TIERS)

    def test_the_shipped_tier_file_is_readable_and_keeps_the_floor_and_the_review_and_gate(self):
        self.shipped()
        self.review("GUARD3.a", defects=0)
        out = "\n".join(self.lines(tiers_path=REAL_TIERS))
        with open(REAL_TIERS, encoding="utf-8") as fh:
            data = json.load(fh)
        self.assertEqual(data["stages"], ["harness_frozen", "mutation_sweep", "review", "whole_gate", "simulated_check"])
        for t in data["tiers"]:
            self.assertTrue(t["measured_catch"].strip() and t["flip_condition"].strip(), t["name"])
            self.assertTrue(set(t["skips"]) <= {"mutation_sweep", "simulated_check"}, t["name"])
        for kind in ("security", "gate", "release"):
            self.assertTrue(data["kinds"][kind]["floor"], kind)
        self.assertIn("tier full: kinds security gate release engine, 1 units in the ledger", out)
        self.assertIn("  tier light: NO-DATA", out)

    def test_the_shipped_file_keeps_install_integrity_and_verdict_honesty_units_off_the_light_tier(self):
        self.shipped()
        for unit in ("STAB1.a", "STAB1.e", "KEY1.a", "KEY1.b", "KEY1.c", "KEY1.d", "KEY1.e", "FLOW1.e", "STAB1.d"):
            self.review(unit, defects=0)
        self.review("STAB1.f", defects=0)                       # a hygiene unit: the control that stays light
        out = "\n".join(self.lines(tiers_path=REAL_TIERS))
        self.assertIn("tier full: kinds security gate release engine, 9 units in the ledger", out)
        self.assertIn("tier light: kinds records_and_hygiene, 1 units in the ledger", out)

    def test_the_shipped_light_tier_is_not_justified_by_an_empty_ledger(self):
        self.shipped()
        with open(self.ledger, "w") as fh:
            fh.write("[]")
        out = "\n".join(self.lines(tiers_path=REAL_TIERS))
        self.assertIn("  tier light: NO-DATA", out)


if __name__ == "__main__":
    unittest.main(verbosity=2)

#!/usr/bin/env python3
"""failure_ledger's own suite. Every case uses a temporary ledger, so it never touches the real one."""
import json
import os
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import failure_ledger as F  # noqa: E402


class TheRankingIsByWhatItCost(unittest.TestCase):
    def rows(self):
        return [{"at": 1000.0, "class": "not-hermetic"}, {"at": 1000.0, "class": "not-hermetic"},
                {"at": 1000.0, "class": "probe-crash"}, {"at": 1.0, "class": "patch-stale"}]

    def test_the_costliest_class_comes_first(self):
        self.assertEqual(F.tally(self.rows())[0], ("not-hermetic", 2))

    def test_cost_beats_alphabet(self):
        """The earlier case was first alphabetically too, so it passed under a mutation that sorted by name."""
        rows = [{"at": 1.0, "class": "zzz-costly"}] * 5 + [{"at": 1.0, "class": "aaa-rare"}]
        self.assertEqual(F.tally(rows)[0][0], "zzz-costly")
        self.assertEqual(F.brief_rules(rows)[0].split()[1], "zzz-costly")

    def test_a_window_excludes_older_failures(self):
        got = [c for c, _ in F.tally(self.rows(), since=60, now=1000.0)]
        self.assertNotIn("patch-stale", got)

    def test_ties_break_by_name_so_two_runs_agree(self):
        rows = [{"at": 1.0, "class": "bbb"}, {"at": 1.0, "class": "aaa"}]
        self.assertEqual([c for c, _ in F.tally(rows)], ["aaa", "bbb"])

    def test_no_rows_yields_no_rules_rather_than_a_generic_checklist(self):
        self.assertEqual(F.brief_rules([]), [])


class ARuleSaysWhatWouldHavePreventedIt(unittest.TestCase):
    def test_a_known_class_carries_its_rule(self):
        line = F.brief_rules([{"at": 1.0, "class": "not-hermetic"}])[0]
        self.assertIn("EXPORT copy", line)

    def test_the_line_states_what_the_class_has_cost(self):
        line = F.brief_rules([{"at": 1.0, "class": "probe-crash"}] * 3)[0]
        self.assertIn("3 failure(s)", line)

    def test_an_unknown_class_is_named_rather_than_hidden(self):
        line = F.brief_rules([{"at": 1.0, "class": "something-new"}])[0]
        self.assertIn("something-new", line)
        self.assertIn("no rule written yet", line)

    def test_every_written_rule_names_a_preventable_action(self):
        for cls, rule in F.RULES.items():
            self.assertGreater(len(rule), 40, cls)


class RecordingNeverStopsTheWork(unittest.TestCase):
    def test_an_unwritable_path_still_returns_the_row(self):
        self.assertEqual(F.record("x", "y", path="/no/such/dir/f.jsonl")["class"], "x")

    def test_a_long_detail_is_truncated_not_dropped(self):
        self.assertEqual(len(F.record("x", "d" * 900, path="/no/such/dir/f.jsonl")["detail"]), 400)

    def test_a_round_trip_through_a_real_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            p = os.path.join(tmp, "f.jsonl")
            F.record("not-hermetic", "a suite read the live board", unit="L2", sub="L2.1", path=p)
            rows = F.load(p)
            self.assertEqual(len(rows), 1)
            self.assertEqual((rows[0]["class"], rows[0]["unit"]), ("not-hermetic", "L2"))

    def test_a_corrupt_line_is_skipped_and_the_rest_are_read(self):
        with tempfile.TemporaryDirectory() as tmp:
            p = os.path.join(tmp, "f.jsonl")
            with open(p, "w") as fh:
                fh.write("{not json\n")
                fh.write(json.dumps({"at": 1.0, "class": "probe-crash"}) + "\n")
            self.assertEqual(len(F.load(p)), 1)

    def test_a_row_without_a_class_is_not_a_failure(self):
        with tempfile.TemporaryDirectory() as tmp:
            p = os.path.join(tmp, "f.jsonl")
            with open(p, "w") as fh:
                fh.write(json.dumps({"at": 1.0, "detail": "no class here"}) + "\n")
            self.assertEqual(F.load(p), [])

    def test_a_missing_ledger_reads_as_empty_not_an_error(self):
        self.assertEqual(F.load("/no/such/ledger.jsonl"), [])

    def test_an_empty_detail_is_never_stored_empty(self):
        """F15: 162 rows in the real ledger carried an empty detail and no class a caller could act on.
        A blank string reads exactly like a call that never happened; the row must say why it has nothing
        to say, in the same field every reader already looks at."""
        row = F.record("x", "", path="/no/such/dir/f.jsonl")
        self.assertTrue(row["detail"].startswith("NO-DATA:"), row["detail"])

    def test_a_whitespace_only_detail_is_treated_as_empty_too(self):
        row = F.record("x", "   \n\t  ", path="/no/such/dir/f.jsonl")
        self.assertTrue(row["detail"].startswith("NO-DATA:"), row["detail"])

    def test_a_none_detail_is_treated_as_empty_too(self):
        row = F.record("x", None, path="/no/such/dir/f.jsonl")
        self.assertTrue(row["detail"].startswith("NO-DATA:"), row["detail"])

    def test_a_real_detail_is_stored_untouched(self):
        row = F.record("x", "a real reason for this one", path="/no/such/dir/f.jsonl")
        self.assertEqual(row["detail"], "a real reason for this one")


class ClassifyNamesWhatReallyHappened(unittest.TestCase):
    """F15, measured on the real ledger 2026-09-26: 203 of 350 probe-crash rows were really grader
    failures (GREEN-WITH-CODE NO), and of 486 unclassified rows, 162 carried an empty detail, 104 were
    EV-gate stops and 161 were safety screen refusals with no more specific signal."""

    def test_a_grader_failure_that_mentions_traceback_is_never_probe_crash(self):
        text = ("[D0.6-r0] RED-WITHOUT-CODE   yes (exit 1)\n[D0.6-r0] APPLY              3 edits, 0 test items\n"
                "[D0.6-r0] GREEN-WITH-CODE    NO exit 1: ERROR: test_bridge | Traceback (most recent call last): "
                "| Raised")
        self.assertEqual(F.classify(text), "existing-suite-broken")

    def test_a_real_crash_with_no_grader_line_stays_probe_crash(self):
        self.assertEqual(F.classify("CRASH    current_spend_entries_none    TypeError 'NoneType' object is not "
                                     "iterable"), "probe-crash")

    def test_an_ev_gate_stop_gets_its_own_class(self):
        self.assertEqual(F.classify("EXHAUSTED by the expected value gate after 4 rounds: EV 0.143 "
                                     "(p 0.14 x 1.00) < cost 0.16: not worth another round"), "ev-gate-stop")

    def test_a_round_count_exhaustion_stays_the_generic_class(self):
        """The EV-gate stop is a DIFFERENT sentence from the generic one; adding the new signal must not
        swallow the old class, or every existing "exhausted" row would silently move."""
        self.assertEqual(F.classify("EXHAUSTED after 5 rounds; best grader pass: None"), "exhausted")

    def test_a_safety_screen_refusal_with_no_more_specific_signal_gets_its_own_class(self):
        self.assertEqual(F.classify("[D2.1-r0] FAIL safety screen: plugin/runtime/brother/core/x.py calls "
                                     "exec(); read it by hand before anything else"), "safety-screen-refusal")

    def test_a_non_literal_argv_refusal_still_wins_over_the_safety_screen_catch_all(self):
        """The more specific class must keep winning: adding a broad catch all must not swallow it."""
        self.assertEqual(F.classify("FAIL safety screen: tools/x.py (a command runner) run() argv element is "
                                     "not a string literal"), "safety-argv-literal")

    def test_an_unparseable_fragment_still_wins_over_the_safety_screen_catch_all(self):
        self.assertEqual(F.classify("FAIL safety screen: a/b.py fragment does not parse, and"),
                          "patch-unparseable")

    def test_new_signal_classes_carry_a_rule(self):
        for cls in ("ev-gate-stop", "safety-screen-refusal"):
            self.assertIn(cls, F.RULES)
            self.assertGreater(len(F.RULES[cls]), 40, cls)


class ReclassifyCountsIsReadOnly(unittest.TestCase):
    def test_before_and_after_counts_are_both_reported(self):
        rows = [{"class": "probe-crash", "detail": "GREEN-WITH-CODE    NO exit 1: | Traceback"},
                {"class": "unclassified", "detail": ""},
                {"class": "ok:build", "detail": ""}]
        before, after = F.reclassify_counts(rows)
        self.assertEqual(before, {"probe-crash": 1, "unclassified": 1})
        self.assertEqual(after, {"existing-suite-broken": 1, "unclassified": 1})

    def test_a_row_it_still_cannot_read_keeps_its_current_label(self):
        """Reclassification can only sharpen a label from real signal in the text, never guess one and
        never drop a row: an unmatched detail stays under whatever class the row already carried."""
        before, after = F.reclassify_counts([{"class": "unclassified", "detail": "something odd happened"}])
        self.assertEqual(after, {"unclassified": 1})

    def test_a_row_already_correctly_classified_is_unchanged(self):
        before, after = F.reclassify_counts([{"class": "not-hermetic", "detail": "passes here but not in the "
                                                                                  "export tree"}])
        self.assertEqual(before, after)

    def test_successes_are_excluded_from_both_sides(self):
        before, after = F.reclassify_counts([{"class": "ok:probe", "detail": ""}])
        self.assertEqual(before, {})
        self.assertEqual(after, {})

    def test_the_function_takes_rows_already_loaded_and_touches_no_file(self):
        """Pure and read only by construction: it never opens the ledger path itself, so a caller cannot
        accidentally have it write by passing one in."""
        import inspect
        src = inspect.getsource(F.reclassify_counts)
        self.assertNotIn("open(", src)
        self.assertNotIn("os.remove", src)


class ReclassifyReportCliNeverWritesTheLedger(unittest.TestCase):
    def test_the_command_prints_a_report_and_leaves_the_file_untouched(self):
        with tempfile.TemporaryDirectory() as tmp:
            p = os.path.join(tmp, "f.jsonl")
            F.record("probe-crash", "GREEN-WITH-CODE    NO exit 1: | Traceback", path=p)
            F.record("unclassified", "EXHAUSTED by the expected value gate after 2 rounds: EV 0.1 < cost 0.2", path=p)
            with open(p, "rb") as fh:
                before_bytes = fh.read()
            before_mtime = os.stat(p).st_mtime_ns
            import io, contextlib
            buf = io.StringIO()
            with contextlib.redirect_stdout(buf):
                rc = F.main(["reclassify-report", "--path", p])
            self.assertEqual(rc, 0)
            text = buf.getvalue()
            self.assertIn("existing-suite-broken", text)
            self.assertIn("ev-gate-stop", text)
            self.assertIn("read only", text)
            with open(p, "rb") as fh:
                after_bytes = fh.read()
            after_mtime = os.stat(p).st_mtime_ns
            self.assertEqual(before_bytes, after_bytes)
            self.assertEqual(before_mtime, after_mtime)

    def test_no_rows_is_no_data_not_an_empty_report(self):
        with tempfile.TemporaryDirectory() as tmp:
            p = os.path.join(tmp, "empty.jsonl")
            open(p, "w").close()
            import io, contextlib
            buf = io.StringIO()
            with contextlib.redirect_stdout(buf):
                rc = F.main(["reclassify-report", "--path", p])
            self.assertEqual(rc, 1)
            self.assertIn("NO-DATA", buf.getvalue())


class AFailureCountNeedsADenominator(unittest.TestCase):
    """The defect this ledger shipped with on its first day, and the same one Jev has: 1,775 predictions against
    12 recorded outcomes. Seven failures could be 7 of 9 or 7 of 200, and those imply opposite actions."""

    def test_a_rate_is_failures_over_attempts(self):
        rows = [{"class": "probe-crash"}] * 3 + [{"class": "ok:probe-crash"}] * 7
        f, n, rate = F.rates(rows)["probe-crash"]
        self.assertEqual((f, n), (3, 10))
        self.assertAlmostEqual(rate, 0.3)

    def test_no_successes_recorded_reads_as_always_failing(self):
        """Honest rather than flattering: with no denominator we cannot claim better than always failing."""
        self.assertEqual(F.rates([{"class": "not-hermetic"}] * 7)["not-hermetic"], (7, 7, 1.0))

    def test_a_stage_that_only_succeeded_has_a_zero_rate(self):
        self.assertEqual(F.rates([{"class": "ok:landing"}] * 4)["landing"], (0, 4, 0.0))

    def test_a_success_is_recorded_under_its_stage(self):
        row = F.record_success("probe", path="/no/such/dir/f.jsonl")
        self.assertEqual(row["class"], "ok:probe")

    def test_successes_do_not_pollute_the_failure_ranking(self):
        rows = [{"at": 1.0, "class": "ok:probe"}] * 9 + [{"at": 1.0, "class": "probe-crash"}]
        self.assertNotIn("ok:probe", [c for c, _ in F.tally(rows)][:1])


if __name__ == "__main__":
    unittest.main(verbosity=1)

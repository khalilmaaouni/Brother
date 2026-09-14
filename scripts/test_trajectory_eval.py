import json
import os
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import trajectory_eval as te  # noqa: E402


class TrajectoryLogTests(unittest.TestCase):
    def test_all_seven_categories_can_be_represented(self):
        """Each of the roadmap's 7 metrics can be recorded and shows up
        with an accurate count in the summary -- this is the one thing
        the done-check asks this module to prove."""
        log = te.new_log("wf")
        for cat in te.CATEGORIES:
            log.record(cat, detail="observed once")
        summary = log.summarize()
        self.assertEqual(summary["total_events"], len(te.CATEGORIES))
        for cat in te.CATEGORIES:
            self.assertEqual(summary["counts"][cat], 1, cat)

    def test_seven_categories_exactly_named_by_the_roadmap(self):
        self.assertEqual(
            set(te.CATEGORIES),
            {
                "repeated_failed_technique",
                "out_of_scope_attempt",
                "unnecessary_write",
                "repair_loop",
                "human_interruption",
                "verification_after_final_edit",
                "stale_evidence_reuse",
            },
        )

    def test_repeated_events_in_one_category_count_correctly(self):
        log = te.new_log("wf")
        log.record("repair_loop", "attempt 1")
        log.record("repair_loop", "attempt 2")
        log.record("repair_loop", "attempt 3")
        summary = log.summarize()
        self.assertEqual(summary["counts"]["repair_loop"], 3)
        self.assertEqual(summary["total_events"], 3)

    def test_unknown_category_is_rejected(self):
        log = te.new_log("wf")
        with self.assertRaises(ValueError):
            log.record("not_a_real_category")

    def test_empty_log_reports_zero_for_every_category(self):
        summary = te.new_log("wf").summarize()
        self.assertEqual(summary["total_events"], 0)
        for cat in te.CATEGORIES:
            self.assertEqual(summary["counts"][cat], 0)

    def test_summary_never_carries_a_verdict_key(self):
        """The structural-separation guarantee: trajectory cleanliness is
        not product correctness, and this module must never emit the
        product-correctness vocabulary word."""
        log = te.new_log("wf")
        log.record("unnecessary_write")
        summary = log.summarize()
        self.assertNotIn("verdict", summary)

    def test_events_preserve_order_and_detail(self):
        log = te.new_log("wf")
        log.record("out_of_scope_attempt", "touched an unrelated file")
        log.record("human_interruption", "ctrl-c mid-run")
        summary = log.summarize()
        self.assertEqual(len(summary["events"]), 2)
        self.assertEqual(summary["events"][0]["category"], "out_of_scope_attempt")
        self.assertEqual(summary["events"][0]["detail"], "touched an unrelated file")
        self.assertEqual(summary["events"][1]["category"], "human_interruption")


class LoadEventsJsonlTests(unittest.TestCase):
    def _write(self, lines):
        fd, path = tempfile.mkstemp(prefix="trajectory-eval-events-", suffix=".jsonl")
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            for line in lines:
                handle.write(line + "\n")
        self.addCleanup(os.remove, path)
        return path

    def test_loads_events_and_counts_them(self):
        path = self._write([
            json.dumps({"category": "stale_evidence_reuse", "detail": "reused an old test result"}),
            json.dumps({"category": "stale_evidence_reuse", "detail": "reused it again"}),
            json.dumps({"category": "verification_after_final_edit", "detail": "ran the suite"}),
        ])
        log = te.load_events_jsonl(path)
        summary = log.summarize()
        self.assertEqual(summary["counts"]["stale_evidence_reuse"], 2)
        self.assertEqual(summary["counts"]["verification_after_final_edit"], 1)

    def test_blank_lines_are_skipped(self):
        path = self._write(["", json.dumps({"category": "unnecessary_write"}), "  "])
        log = te.load_events_jsonl(path)
        self.assertEqual(log.summarize()["total_events"], 1)

    def test_missing_category_field_raises(self):
        path = self._write([json.dumps({"detail": "no category here"})])
        with self.assertRaises(ValueError):
            te.load_events_jsonl(path)

    def test_invalid_json_line_raises(self):
        path = self._write(["{not json"])
        with self.assertRaises(ValueError):
            te.load_events_jsonl(path)

    def test_unknown_category_in_file_raises(self):
        path = self._write([json.dumps({"category": "bogus"})])
        with self.assertRaises(ValueError):
            te.load_events_jsonl(path)


class CompareWorkflowsTests(unittest.TestCase):
    def test_compares_two_workflows_side_by_side_without_a_combined_score(self):
        clean = te.new_log("clean-workflow")
        messy = te.new_log("messy-workflow")
        messy.record("repair_loop")
        messy.record("repair_loop")
        messy.record("human_interruption")

        comparison = te.compare_workflows({"clean-workflow": clean, "messy-workflow": messy})

        self.assertEqual(comparison["clean-workflow"]["total_events"], 0)
        self.assertEqual(comparison["messy-workflow"]["counts"]["repair_loop"], 2)
        self.assertEqual(comparison["messy-workflow"]["counts"]["human_interruption"], 1)
        # No combined/ranking key anywhere -- comparison is side-by-side data,
        # never a computed verdict or a winner.
        for name in comparison:
            self.assertNotIn("verdict", comparison[name])
        self.assertNotIn("winner", comparison)
        self.assertNotIn("score", comparison)


class CliTests(unittest.TestCase):
    def test_list_categories_exits_zero(self):
        self.assertEqual(te.main(["--list-categories"]), 0)

    def test_missing_events_file_errors(self):
        with self.assertRaises(SystemExit):
            te.main([])

    def test_nonexistent_events_file_is_no_data_exit_2(self):
        exit_code = te.main(["--events-file", "/no/such/file/for/trajectory-eval-test.jsonl"])
        self.assertEqual(exit_code, 2)

    def test_events_file_round_trip_via_cli(self):
        fd, path = tempfile.mkstemp(prefix="trajectory-eval-cli-", suffix=".jsonl")
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as handle:
                handle.write(json.dumps({"category": "repair_loop"}) + "\n")
            self.assertEqual(te.main(["--events-file", path]), 0)
        finally:
            os.remove(path)


if __name__ == "__main__":
    unittest.main()

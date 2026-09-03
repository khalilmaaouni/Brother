#!/usr/bin/env python3
"""Tests for the handover ceremony. Every NO-DATA path proven to fire
(never a false empty), every emitted note proven to carry valid
frontmatter, an invalid status/type proven REFUSED rather than written, and
emission proven idempotent. No em or en dashes."""

import filecmp
import json
import os
import shutil
import sys
import tempfile
import unittest
from datetime import datetime, timezone

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from handover_ceremony import (
    repo_state, sbe_task_summary, day_plan_state, pr_state, limit_state,
    collect_state, state_has_error, build_vault_note, emit_vault_notes,
    emit_pattern_notes, build_handover_markdown, main, ALLOWED_STATUS,
    ALLOWED_TYPE)
import pattern_note

NOW = datetime(2026, 8, 29, 6, 0, 0, tzinfo=timezone.utc)


def lesson(name="a-lesson", **overrides):
    base = {
        "name": name,
        "description": "short description",
        "symptom": "it looked fine and then it was not",
        "what_happened": "the thing that happened",
        "why_it_matters": "the thing that matters",
        "how_to_apply": "the thing to do next time",
    }
    base.update(overrides)
    return base


class TestCollectorNoData(unittest.TestCase):
    """A missing source is NO-DATA, never a false empty."""

    def test_repo_state_no_data_when_head_fails(self):
        out = repo_state("/nowhere", git_run=lambda p, a: None)
        self.assertIn("error", out)
        self.assertIn("NO-DATA", out["error"])

    def test_repo_state_reports_clean_and_dirty_counts(self):
        calls = {"n": 0}

        def git_run(path, args):
            calls["n"] += 1
            if args[0] == "rev-parse":
                return "abc123\n"
            return " M scripts/foo.py\n?? scripts/bar.py\n"

        out = repo_state("/repo", git_run=git_run)
        self.assertEqual(out["head"], "abc123")
        self.assertFalse(out["clean"])
        self.assertEqual(out["dirty_count"], 2)
        self.assertIn("scripts/foo.py", out["dirty_paths"])

    def test_sbe_task_summary_no_data_when_registry_unreadable(self):
        out = sbe_task_summary(registry_reader=lambda: None)
        self.assertIn("error", out)
        self.assertIn("NO-DATA", out["error"])

    def test_sbe_task_summary_real_empty_is_not_no_data(self):
        out = sbe_task_summary(registry_reader=lambda: [])
        self.assertNotIn("error", out)
        self.assertEqual(out["count"], 0)

    def test_sbe_task_summary_reports_id_owner_age(self):
        out = sbe_task_summary(
            now=NOW,
            registry_reader=lambda: [
                {"id": "t1", "agent": "builder",
                 "openedAt": "2026-08-28T06:00:00Z"}])
        self.assertEqual(out["tasks"][0]["id"], "t1")
        self.assertEqual(out["tasks"][0]["owner"], "builder")
        self.assertEqual(out["tasks"][0]["age_hours"], 24)

    def test_day_plan_state_no_data_when_rows_unreadable(self):
        out = day_plan_state(rows_reader=lambda: None)
        self.assertIn("error", out)
        self.assertIn("NO-DATA", out["error"])

    def test_day_plan_state_uses_shared_ready_rule(self):
        rows = [{"id": "a", "status": "DONE"},
                {"id": "b", "status": "SCHEDULED", "depends_on": ["a"]}]
        out = day_plan_state(rows_reader=lambda: rows)
        self.assertEqual(out["ready"], ["b"])

    def test_pr_state_no_data_when_gh_absent(self):
        out = pr_state(available=lambda: False)
        self.assertIn("error", out)
        self.assertIn("NO-DATA", out["error"])
        self.assertIn("gh", out["error"])

    def test_pr_state_real_empty_list_is_not_no_data(self):
        """A working gh call with zero PRs is a genuine empty, never
        collapsed into the same NO-DATA case as gh being absent."""
        out = pr_state(available=lambda: True, gh_list=lambda: [])
        self.assertNotIn("error", out)
        self.assertEqual(out["count"], 0)

    def test_pr_state_no_data_when_gh_call_fails(self):
        out = pr_state(available=lambda: True, gh_list=lambda: None)
        self.assertIn("error", out)

    def test_collect_state_combines_all_four_sources(self):
        state = collect_state(
            ["/r"], git_run=lambda p, a: "x\n",
            registry_reader=lambda: [], day_plan_reader=lambda: [],
            gh_available=lambda: True, gh_list=lambda: [])
        self.assertIn("/r", state["repos"])
        self.assertEqual(state["sbe_tasks"]["count"], 0)
        self.assertFalse(state_has_error(state))

    def test_limit_state_no_data_when_file_unreadable(self):
        out = limit_state("/nope.json", reader=lambda p: None)
        self.assertIn("error", out)
        self.assertIn("NO-DATA", out["error"])

    def test_limit_state_passes_through_real_dict(self):
        out = limit_state("/some.json",
                          reader=lambda p: {"class": "seven_day",
                                            "resets_at": 1788030000})
        self.assertEqual(out["class"], "seven_day")

    def test_collect_state_omits_limit_state_when_no_path_given(self):
        state = collect_state(
            ["/r"], git_run=lambda p, a: "x\n",
            registry_reader=lambda: [], day_plan_reader=lambda: [],
            gh_available=lambda: True, gh_list=lambda: [])
        self.assertNotIn("limit_state", state)
        self.assertFalse(state_has_error(state))

    def test_collect_state_includes_limit_state_when_path_given(self):
        state = collect_state(
            ["/r"], git_run=lambda p, a: "x\n",
            registry_reader=lambda: [], day_plan_reader=lambda: [],
            gh_available=lambda: True, gh_list=lambda: [],
            limit_state_path="/some.json",
            limit_state_reader=lambda p: {"class": "five_hour"})
        self.assertEqual(state["limit_state"]["class"], "five_hour")
        self.assertFalse(state_has_error(state))

    def test_collect_state_limit_state_read_failure_is_error(self):
        state = collect_state(
            ["/r"], git_run=lambda p, a: "x\n",
            registry_reader=lambda: [], day_plan_reader=lambda: [],
            gh_available=lambda: True, gh_list=lambda: [],
            limit_state_path="/nope.json", limit_state_reader=lambda p: None)
        self.assertTrue(state_has_error(state))

    def test_state_has_error_true_when_any_source_no_data(self):
        state = collect_state(
            ["/r"], git_run=lambda p, a: "x\n",
            registry_reader=lambda: None, day_plan_reader=lambda: [],
            gh_available=lambda: True, gh_list=lambda: [])
        self.assertTrue(state_has_error(state))


class TestVaultNoteSchema(unittest.TestCase):
    """Every emitted note carries valid frontmatter per the vault's own
    controlled vocabulary (bm_vault_graph.py check)."""

    def test_note_carries_every_required_frontmatter_key(self):
        filename, content = build_vault_note(lesson(), today="2026-08-29")
        self.assertTrue(content.startswith("---\n"))
        for key in ("type:", "project:", "created:", "status:", "tags:",
                   "verified-by:"):
            self.assertIn(key, content)
        self.assertIn("symptom:", content)  # type failure carries symptom
        self.assertEqual(filename, "a-lesson.md")

    def test_note_never_emits_a_wikilink(self):
        _, content = build_vault_note(lesson(), today="2026-08-29")
        self.assertNotIn("[[", content)

    def test_note_body_carries_the_three_sections(self):
        _, content = build_vault_note(lesson(), today="2026-08-29")
        self.assertIn("## What happened", content)
        self.assertIn("## Why it matters", content)
        self.assertIn("## How to apply", content)

    def test_invalid_status_is_refused(self):
        with self.assertRaises(ValueError) as ctx:
            build_vault_note(lesson(status="in-progress"))
        self.assertIn("status", str(ctx.exception))

    def test_invalid_type_is_refused(self):
        with self.assertRaises(ValueError) as ctx:
            build_vault_note(lesson(type="mistake"))
        self.assertIn("type", str(ctx.exception))

    def test_valid_status_and_type_pass_through(self):
        self.assertIn("closed", ALLOWED_STATUS)
        self.assertIn("finding", ALLOWED_TYPE)
        _, content = build_vault_note(
            lesson(status="closed", type="finding"), today="2026-08-29")
        self.assertIn("status: closed", content)
        self.assertIn("type: finding", content)


class TestEmitVaultNotes(unittest.TestCase):

    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)

    def test_invalid_lesson_is_refused_not_written(self):
        written, refused = emit_vault_notes(
            self.tmp, [lesson(status="bogus")], today="2026-08-29")
        self.assertEqual(written, [])
        self.assertEqual(len(refused), 1)
        self.assertEqual(os.listdir(self.tmp), [])

    def test_valid_lesson_alongside_an_invalid_one_still_writes_the_valid_one(self):
        written, refused = emit_vault_notes(
            self.tmp, [lesson(name="good-one"), lesson(status="bogus")],
            today="2026-08-29")
        self.assertEqual(len(written), 1)
        self.assertEqual(len(refused), 1)
        self.assertTrue(os.path.exists(
            os.path.join(self.tmp, "good-one.md")))

    def test_emission_is_idempotent(self):
        lessons = [lesson(name="lesson-one"), lesson(name="lesson-two")]
        dir_a = tempfile.mkdtemp()
        dir_b = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, dir_a, ignore_errors=True)
        self.addCleanup(shutil.rmtree, dir_b, ignore_errors=True)
        emit_vault_notes(dir_a, lessons, today="2026-08-29")
        emit_vault_notes(dir_b, lessons, today="2026-08-29")
        cmp = filecmp.dircmp(dir_a, dir_b)
        self.assertEqual(cmp.diff_files, [])
        self.assertEqual(cmp.left_only, [])
        self.assertEqual(cmp.right_only, [])


def pattern_vault():
    d = tempfile.mkdtemp()
    os.makedirs(os.path.join(d, pattern_note.FOLDER))
    return d


def green_close(name="a-green-close", **overrides):
    base = {
        "name": name,
        "problem": "the branch fails and nobody can tell why",
        "technique": "drive the check backwards before trusting it",
        "receipt": "scripts/test_x.py",
        "exit_code": 0,
    }
    base.update(overrides)
    return base


class TestEmitPatternNotes(unittest.TestCase):
    """V4: a green close writes one pattern note, through pattern_note.write
    (which itself routes through the vault's hard gate), with the receipt
    linked; a close with no usable receipt writes nothing and says why."""

    def setUp(self):
        self.tmp = pattern_vault()
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)

    def test_a_green_close_writes_one_pattern_note_with_the_receipt_linked(self):
        written, skipped = emit_pattern_notes(self.tmp, [green_close()])
        self.assertEqual(len(written), 1)
        self.assertEqual(skipped, [])
        with open(written[0], encoding="utf-8") as fh:
            content = fh.read()
        self.assertIn("type: pattern", content)
        self.assertIn("receipt: scripts/test_x.py (exit 0)", content)

    def test_a_second_run_over_the_same_green_close_writes_nothing(self):
        emit_pattern_notes(self.tmp, [green_close()])
        written, skipped = emit_pattern_notes(self.tmp, [green_close()])
        self.assertEqual(written, [])
        self.assertEqual(skipped, [])

    def test_a_green_close_with_no_receipt_writes_no_pattern_note(self):
        written, skipped = emit_pattern_notes(
            self.tmp, [green_close(receipt="")])
        self.assertEqual(written, [])
        self.assertEqual(len(skipped), 1)
        self.assertIn("a-green-close", skipped[0])
        self.assertEqual(
            os.listdir(os.path.join(self.tmp, pattern_note.FOLDER)), [])

    def test_a_green_close_with_a_failing_exit_code_writes_no_pattern_note(self):
        written, skipped = emit_pattern_notes(
            self.tmp, [green_close(exit_code=1)])
        self.assertEqual(written, [])
        self.assertEqual(len(skipped), 1)
        self.assertIn("exit_code=1", skipped[0])
        self.assertEqual(
            os.listdir(os.path.join(self.tmp, pattern_note.FOLDER)), [])

    def test_a_green_close_missing_a_required_field_writes_no_pattern_note(self):
        written, skipped = emit_pattern_notes(
            self.tmp, [green_close(problem="")])
        self.assertEqual(written, [])
        self.assertEqual(len(skipped), 1)
        self.assertIn("needs name, problem and technique", skipped[0])

    def test_a_valid_close_alongside_a_bad_one_still_writes_the_valid_one(self):
        written, skipped = emit_pattern_notes(
            self.tmp, [green_close(name="good-close"),
                      green_close(name="bad-close", receipt="")])
        self.assertEqual(len(written), 1)
        self.assertEqual(len(skipped), 1)
        self.assertIn("good-close", written[0])

    def test_main_cli_writes_a_pattern_note_via_emit_patterns(self):
        tmp = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, tmp, ignore_errors=True)
        gcf = os.path.join(tmp, "green-closes.json")
        with open(gcf, "w") as f:
            json.dump([green_close(name="cli-close")], f)
        import io
        import contextlib
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            code = main(["--emit-patterns", "--green-close-file", gcf,
                        "--pattern-vault", self.tmp])
        self.assertEqual(code, 0)
        self.assertIn("wrote 1 pattern note", buf.getvalue())
        self.assertTrue(os.path.exists(
            os.path.join(self.tmp, pattern_note.FOLDER, "cli-close.md")))

    def test_main_cli_without_pattern_vault_is_no_data(self):
        self.assertEqual(main(["--emit-patterns"]), 2)


class TestHandoverMarkdown(unittest.TestCase):

    def test_orders_priority_first(self):
        state = {
            "repos": {"/r": {"head": "abc123def456", "clean": False,
                             "dirty_count": 1, "dirty_paths": ["x.py"]}},
            "sbe_tasks": {"count": 1, "tasks": [
                {"id": "t1", "owner": "builder", "age_hours": 5}]},
            "day_plan": {"ready": ["r1"], "in_flight": [], "event_wait": []},
            "pull_requests": {"count": 1, "pull_requests": [
                {"number": 7, "title": "the pr", "url": "https://x/7"}]},
        }
        md = build_handover_markdown(state, [lesson()])
        self.assertLess(md.index("Uncommitted work"), md.index("Open sbe tasks"))
        self.assertLess(md.index("Open sbe tasks"), md.index("Day-plan ready set"))
        self.assertLess(md.index("Day-plan ready set"),
                        md.index("Open pull requests"))
        self.assertLess(md.index("Open pull requests"),
                        md.index("Lessons captured"))
        self.assertIn("t1", md)
        self.assertIn("#7", md)
        self.assertIn("r1", md)

    def test_no_data_sources_are_named_not_hidden(self):
        state = {"repos": {}, "sbe_tasks": {"error": "NO-DATA: x"},
                "day_plan": {"error": "NO-DATA: y"},
                "pull_requests": {"error": "NO-DATA: z"}}
        md = build_handover_markdown(state, [])
        self.assertIn("NO-DATA: x", md)
        self.assertIn("NO-DATA: y", md)
        self.assertIn("NO-DATA: z", md)


class TestMainCLI(unittest.TestCase):

    def test_no_flags_is_no_data(self):
        self.assertEqual(main([]), 2)

    def test_collect_prints_json_and_exits_clean(self):
        import io
        import contextlib
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            code = main(["--collect"])
        # No --repo given: repos empty, and the real sbe/day_plan/gh state
        # of this checkout is read live, so only assert the shape parses.
        payload = json.loads(buf.getvalue())
        self.assertIn("repos", payload)
        self.assertIn("sbe_tasks", payload)
        self.assertIsInstance(code, int)

    def test_emit_vault_via_main_writes_and_reports(self):
        tmp = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, tmp, ignore_errors=True)
        lf = os.path.join(tmp, "lessons.json")
        with open(lf, "w") as f:
            json.dump([lesson(name="cli-lesson")], f)
        out_dir = os.path.join(tmp, "vault")
        import io
        import contextlib
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            code = main(["--emit-vault", out_dir, "--lesson-file", lf])
        self.assertEqual(code, 0)
        self.assertTrue(os.path.exists(
            os.path.join(out_dir, "cli-lesson.md")))
        self.assertIn("wrote 1 vault note", buf.getvalue())

    def test_emit_vault_via_main_reports_refusal_as_finding_exit(self):
        tmp = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, tmp, ignore_errors=True)
        lf = os.path.join(tmp, "lessons.json")
        with open(lf, "w") as f:
            json.dump([lesson(status="bogus")], f)
        out_dir = os.path.join(tmp, "vault")
        import io
        import contextlib
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            code = main(["--emit-vault", out_dir, "--lesson-file", lf])
        self.assertEqual(code, 1)
        self.assertIn("REFUSED", buf.getvalue())

    def test_missing_lesson_file_is_no_data(self):
        self.assertEqual(
            main(["--emit-vault", "/tmp/x", "--lesson-file", "/nope.json"]),
            2)


if __name__ == "__main__":
    unittest.main()

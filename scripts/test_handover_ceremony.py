#!/usr/bin/env python3
"""Tests for the handover ceremony. Every NO-DATA path proven to fire
(never a false empty), every emitted note proven to carry valid
frontmatter, an invalid status/type proven REFUSED rather than written, and
emission proven idempotent. No em or en dashes."""

import contextlib
import filecmp
import io
import json
import os
import shutil
import sys
import tempfile
import unittest
from datetime import datetime, timezone

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import handover_ceremony as HC
from handover_ceremony import (
    repo_state, sbe_task_summary, day_plan_state, pr_state, limit_state,
    collect_state, state_has_error, build_vault_note, emit_vault_notes,
    emit_pattern_notes, build_handover_markdown, main, ALLOWED_STATUS,
    ALLOWED_TYPE, PROCESS_WAIVER, INDEX_NAME)
import pattern_note

# E100: one sandbox for every temp tree this process makes, removed at exit.
import os as _e100_os, sys as _e100_sys  # noqa: E402
_e100_sys.path.append(_e100_os.path.join(
    _e100_os.path.dirname(_e100_os.path.abspath(__file__)), '.'))
try:  # noqa: E402
    import tmp_sandbox as _e100_tmp
    _e100_tmp.install()
except ImportError:
    # A packager (scripts/export_public.py, make_benchmark_bundle.py)
    # can copy this test without scripts/tmp_sandbox.py beside it. Say
    # so rather than dying: the sandbox is hygiene, not the subject.
    _e100_sys.stderr.write(
        "tmp_sandbox absent: %s leaves its temp trees behind\n"
        % _e100_os.path.basename(__file__))

NOW = datetime(2026, 8, 29, 6, 0, 0, tzinfo=timezone.utc)


def lesson(name="a-lesson", **overrides):
    base = {
        "name": name,
        "description": "short description",
        "symptom": "it looked fine and then it was not",
        "what_happened": "the thing that happened",
        "why_it_matters": "the thing that matters",
        "how_to_apply": "the thing to do next time",
        "applies_to": ["scripts/handover_ceremony.py"],
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
        written, refused, _ = emit_vault_notes(
            self.tmp, [lesson(status="bogus")], today="2026-08-29")
        self.assertEqual(written, [])
        self.assertEqual(len(refused), 1)
        self.assertEqual(os.listdir(self.tmp), [])

    def test_valid_lesson_alongside_an_invalid_one_still_writes_the_valid_one(self):
        written, refused, _ = emit_vault_notes(
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


class TestAnchorsAndFailuresIndex(unittest.TestCase):
    """2026-10-04 owner order: a failure lesson carries applies_to (or the
    process-lesson waiver) into frontmatter, and each written failure appends
    one detailed AREA/SYMPTOM/FIX line to Failures-Index.md, append only.
    One fixture per guard."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)
        self.index = os.path.join(self.tmp, INDEX_NAME)
        self.prior = "# Failures Index\n\n## Old section\n- [[old-one]] 2026-01-01. kept.\n"
        with open(self.index, "w", encoding="utf-8") as fh:
            fh.write(self.prior)

    def read_index(self):
        with open(self.index, encoding="utf-8") as fh:
            return fh.read()

    def test_applies_to_is_written_into_frontmatter(self):
        _, content = build_vault_note(
            lesson(applies_to=["scripts/a.py", "scripts/b.py"]), today="2026-10-04")
        front = content.split("\n---", 1)[0]
        self.assertIn("applies_to: [scripts/a.py, scripts/b.py]", front)

    def test_failure_with_no_anchor_is_refused(self):
        with self.assertRaises(ValueError) as ctx:
            build_vault_note(lesson(applies_to=None), today="2026-10-04")
        self.assertIn("applies_to", str(ctx.exception))

    def test_failure_with_an_empty_anchor_list_is_refused(self):
        with self.assertRaises(ValueError):
            build_vault_note(lesson(applies_to=[]), today="2026-10-04")

    def test_a_non_list_anchor_value_is_refused(self):
        with self.assertRaises(ValueError):
            build_vault_note(lesson(applies_to="scripts/a.py"), today="2026-10-04")

    def test_an_anchor_with_a_comma_is_refused(self):
        with self.assertRaises(ValueError):
            build_vault_note(lesson(applies_to=["a.py, b.py"]), today="2026-10-04")

    def test_process_waiver_writes_an_empty_list_never_the_waiver_text(self):
        _, content = build_vault_note(
            lesson(applies_to=PROCESS_WAIVER), today="2026-10-04")
        front = content.split("\n---", 1)[0]
        self.assertIn("applies_to: []", front)
        self.assertNotIn("applies_to: none", front)
        self.assertIn('anchor_waiver: "none (process lesson)"', front)

    def test_a_non_failure_needs_no_anchor(self):
        _, content = build_vault_note(
            lesson(type="finding", applies_to=None), today="2026-10-04")
        self.assertNotIn("applies_to", content)

    def test_written_failure_appends_one_detailed_index_line(self):
        written, refused, appended = emit_vault_notes(self.tmp, [lesson(
            name="disk ran out", area="loop driver", symptom="every unit stalled",
            fix="hold under 2 GB", applies_to=["scripts/loop/loop_until.sh"])],
            today="2026-10-04")
        self.assertEqual((len(written), refused, appended), (1, [], 1))
        text = self.read_index()
        self.assertIn("## Handover ceremony 2026-10-04\n", text)
        self.assertIn("- [[disk-ran-out]] 2026-10-04. AREA: loop driver. "
                      "SYMPTOM: every unit stalled. FIX: hold under 2 GB.\n", text)

    def test_index_is_append_only(self):
        emit_vault_notes(self.tmp, [lesson(name="n1")], today="2026-10-04")
        self.assertTrue(self.read_index().startswith(self.prior))

    def test_a_slug_already_in_the_index_is_not_repeated(self):
        emit_vault_notes(self.tmp, [lesson(name="n1")], today="2026-10-04")
        _, _, appended = emit_vault_notes(self.tmp, [lesson(name="n1")], today="2026-10-04")
        self.assertEqual(appended, 0)
        self.assertEqual(self.read_index().count("[[n1]]"), 1)

    def test_same_day_reuses_the_dated_section(self):
        emit_vault_notes(self.tmp, [lesson(name="n1")], today="2026-10-04")
        emit_vault_notes(self.tmp, [lesson(name="n2")], today="2026-10-04")
        self.assertEqual(self.read_index().count("## Handover ceremony 2026-10-04"), 1)

    def test_area_falls_back_to_anchors_and_fix_to_how_to_apply(self):
        emit_vault_notes(self.tmp, [lesson(name="n1")], today="2026-10-04")
        self.assertIn("AREA: scripts/handover_ceremony.py. SYMPTOM: it looked fine "
                      "and then it was not. FIX: the thing to do next time.",
                      self.read_index())

    def test_a_finding_appends_no_index_line(self):
        _, _, appended = emit_vault_notes(
            self.tmp, [lesson(name="f1", type="finding")], today="2026-10-04")
        self.assertEqual(appended, 0)
        self.assertEqual(self.read_index(), self.prior)

    def test_a_refused_failure_appends_no_index_line(self):
        _, refused, appended = emit_vault_notes(
            self.tmp, [lesson(name="n1", applies_to=None)], today="2026-10-04")
        self.assertEqual((len(refused), appended), (1, 0))
        self.assertEqual(self.read_index(), self.prior)

    def test_an_index_without_a_final_newline_gets_one_before_the_append(self):
        with open(self.index, "w", encoding="utf-8") as fh:
            fh.write("# Failures Index\n- [[old-one]] kept.")
        emit_vault_notes(self.tmp, [lesson(name="n1")], today="2026-10-04")
        self.assertIn("- [[old-one]] kept.\n\n## Handover ceremony 2026-10-04\n- [[n1]]",
                      self.read_index())

    def test_findings_only_into_a_dir_without_an_index_are_not_flagged(self):
        os.remove(self.index)
        _, _, appended = emit_vault_notes(
            self.tmp, [lesson(name="f1", type="finding")], today="2026-10-04")
        self.assertEqual(appended, 0)

    def test_no_index_file_is_flagged_never_created(self):
        os.remove(self.index)
        written, _, appended = emit_vault_notes(
            self.tmp, [lesson(name="n1")], today="2026-10-04")
        self.assertEqual((len(written), appended), (1, None))
        self.assertFalse(os.path.exists(self.index))

    def test_cli_exits_1_when_the_index_is_missing(self):
        os.remove(self.index)
        lf = os.path.join(self.tmp, "lessons.json")
        with open(lf, "w", encoding="utf-8") as fh:
            json.dump([lesson(name="n1")], fh)
        with contextlib.redirect_stdout(io.StringIO()) as buf:
            code = main(["--emit-vault", self.tmp, "--lesson-file", lf])
        self.assertEqual(code, 1)
        self.assertIn("FLAGGED", buf.getvalue())

    def test_cli_exits_0_and_appends_when_the_index_exists(self):
        lf = os.path.join(self.tmp, "lessons.json")
        with open(lf, "w", encoding="utf-8") as fh:
            json.dump([lesson(name="n1")], fh)
        with contextlib.redirect_stdout(io.StringIO()):
            code = main(["--emit-vault", self.tmp, "--lesson-file", lf])
        self.assertEqual(code, 0)
        self.assertIn("[[n1]]", self.read_index())


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
        # 2026-10-04: a failure now also appends to DIR/Failures-Index.md,
        # and a DIR without one exits 1 (FLAGGED), so the fixture carries it.
        os.makedirs(out_dir)
        open(os.path.join(out_dir, INDEX_NAME), "w").close()
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


class Night0912HandoverCeremony(unittest.TestCase):
    def test_duplicate_lesson_names_do_not_overwrite_or_miscount(self):
        with tempfile.TemporaryDirectory() as d:
            lessons = [
                lesson(name="Same Name", description="first"),
                lesson(name="Same Name", description="second"),
            ]
            written, refused, _ = emit_vault_notes(d, lessons, today="2026-08-29")
            self.assertEqual(len(written), len(set(written)))
            self.assertEqual(len(written), 1)
            self.assertEqual(len(refused), 1)
            files = [os.path.join(d, f) for f in os.listdir(d)]
            self.assertEqual(len(files), 1)
            with open(files[0], encoding="utf-8") as f:
                content = f.read()
            self.assertIn("first", content)
            self.assertNotIn("second", content)


EX_SYMPTOM = ("the nightly export stopped after the packing step because the "
              "manifest listed a file the allowlist never shipped")
RESTATED = ("export stopped after the packing step since the manifest listed "
            "a file the allowlist never shipped")
FOUR_ONLY = ("export stopped after packing, then a long unrelated sentence "
             "about login prompts blocking another build on the first run "
             "of the day")
THREE_SAME = "login prompt blocked"
EX_SLUG = "an-export-stops-when-the-manifest-lists-an-unshipped-file"
EX_RENAMED = "the-export-stops-because-the-manifest-lists-an-unshipped-file"


class DuplicateMatch(unittest.TestCase):
    def test_tokens_are_lowercase_words_of_three_or_more_without_stopwords(self):
        self.assertEqual(
            HC._tokens("The debate runner's first helper call exited 1 in 0 s"),
            frozenset({"call", "debate", "exited", "first", "helper",
                       "runner"}))
        self.assertEqual(HC._tokens("it looked fine and then it was not"),
                         frozenset({"fine", "looked"}))
        self.assertEqual(HC._tokens("EXPORT Export"), frozenset({"export"}))

    def test_tokens_of_a_non_text_value_or_empty_text_are_empty(self):
        for value in (None, 5, b"x", "", "   "):
            self.assertEqual(HC._tokens(value), frozenset(), value)

    def test_similar_needs_the_shared_word_count(self):
        words = HC._tokens(THREE_SAME)
        self.assertEqual(len(words), 3)
        self.assertFalse(HC._similar(words, words))

    def test_similar_needs_the_ratio(self):
        a, b = HC._tokens(FOUR_ONLY), HC._tokens(EX_SYMPTOM)
        self.assertEqual(len(a & b), 4)
        self.assertFalse(HC._similar(a, b))

    def test_similar_holds_for_a_close_restatement(self):
        self.assertTrue(HC._similar(HC._tokens(RESTATED),
                                    HC._tokens(EX_SYMPTOM)))

    def test_a_restated_symptom_under_another_name_is_found(self):
        self.assertEqual(
            HC.find_duplicate("brand-new-name", RESTATED,
                              {EX_SLUG: [EX_SYMPTOM]}),
            (EX_SLUG, "symptom"))

    def test_a_renamed_note_is_found_by_name_alone(self):
        self.assertEqual(
            HC.find_duplicate(EX_RENAMED, "nothing in common with it at all",
                              {EX_SLUG: []}),
            (EX_SLUG, "name"))

    def test_the_notes_own_slug_is_never_its_duplicate(self):
        self.assertIsNone(
            HC.find_duplicate(EX_SLUG, EX_SYMPTOM, {EX_SLUG: [EX_SYMPTOM]}))

    def test_the_smallest_slug_wins(self):
        existing = {}
        existing["zeta-note"] = [EX_SYMPTOM]
        existing["alpha-note"] = [EX_SYMPTOM]
        for _ in range(2):
            self.assertEqual(
                HC.find_duplicate("brand-new-name", RESTATED, existing),
                ("alpha-note", "symptom"))

    def test_nothing_to_compare_matches_nothing(self):
        self.assertIsNone(HC.find_duplicate("brand-new-name", RESTATED, {}))
        for symptom in ("", None, 5):
            self.assertIsNone(HC.find_duplicate(
                "brand-new-name", symptom, {EX_SLUG: [EX_SYMPTOM]}), symptom)
        for existing in (None, 0, True, "x", b"x", [], ["x"], (), {1, 2},
                         {EX_SLUG: "x"}, {EX_SLUG: None}, {1: [EX_SYMPTOM]},
                         {EX_SLUG: [None, 5, b"x"]}):
            self.assertIsNone(HC.find_duplicate(
                "brand-new-name", RESTATED, existing), existing)



FM = b'---\ntype: failure\nsymptom: "alpha bravo charlie delta"\n---\n# n\n'


class ExistingFailures(unittest.TestCase):
    """What a vault directory already holds: each note's front matter
    symptom and each Failures-Index line's text, keyed by slug."""

    def vault_dir(self, files, index=None):
        d = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, d, ignore_errors=True)
        for name, data in files.items():
            with open(os.path.join(d, name), "wb") as f:
                f.write(data)
        if index is not None:
            with open(os.path.join(d, INDEX_NAME), "wb") as f:
                f.write(index)
        return d

    def test_a_note_symptom_is_read_from_its_front_matter(self):
        d = self.vault_dir({"n.md": FM})
        self.assertEqual(HC._existing_failures(d),
                         {"n": ["alpha bravo charlie delta"]})

    def test_an_unterminated_front_matter_is_still_read(self):
        d = self.vault_dir({"n.md": b'---\ntype: failure\n'
                                    b'symptom: "alpha bravo charlie delta"\n'
                                    b'more: text\n'})
        self.assertEqual(HC._existing_failures(d),
                         {"n": ["alpha bravo charlie delta"]})

    def test_a_symptom_line_in_the_body_is_not_read(self):
        d = self.vault_dir({"n.md": FM.replace(b"type: failure\n", b"")
                            + b'symptom: "other words here now"\n'})
        self.assertEqual(HC._existing_failures(d),
                         {"n": ["alpha bravo charlie delta"]})
        d = self.vault_dir({"n.md": b"---\ntype: failure\n---\n# n\n"
                                    b'symptom: "other words here now"\n'})
        self.assertEqual(HC._existing_failures(d), {"n": []})

    def test_text_without_front_matter_gives_no_symptom(self):
        d = self.vault_dir({"n.md": b'# title\nsymptom: "alpha bravo"\n'})
        self.assertEqual(HC._existing_failures(d), {"n": []})

    def test_the_forty_line_cap_holds(self):
        body = b"---\n" + b"pad: x\n" * 39 + b'symptom: "too late"\n---\n'
        d = self.vault_dir({"n.md": body})
        self.assertEqual(HC._existing_failures(d), {"n": []})
        body = b"---\n" + b"pad: x\n" * 38 + b'symptom: "just in"\n---\n'
        d = self.vault_dir({"n.md": body})
        self.assertEqual(HC._existing_failures(d), {"n": ["just in"]})

    def test_a_note_without_a_symptom_still_exists_in_the_dict(self):
        d = self.vault_dir({"n.md": b"---\ntype: failure\n---\n"})
        self.assertEqual(HC._existing_failures(d), {"n": []})
        self.assertEqual(HC._existing_failures(self.vault_dir({})), {})

    def test_a_symptom_quote_pair_is_removed_only_when_both_ends_have_one(self):
        for raw, want in ((b'"a b"', "a b"), (b'"a b', '"a b'),
                          (b"a b", "a b"), (b'a b"', 'a b"')):
            d = self.vault_dir({"n.md": b"---\nsymptom: " + raw + b"\n---\n"})
            self.assertEqual(HC._existing_failures(d), {"n": [want]}, raw)

    def test_index_lines_in_the_new_shape_give_the_symptom_text(self):
        d = self.vault_dir({}, index=(
            b"# Failures\n- [[s]] 2026-10-04. AREA: loop. SYMPTOM: every unit "
            b"stalled. FIX: hold.\n"))
        self.assertEqual(HC._existing_failures(d),
                         {"s": ["every unit stalled."]})

    def test_index_lines_in_the_old_shape_give_the_text_after_the_slug(self):
        d = self.vault_dir({}, index=(
            b"- [[o]] 2026-09-09. Every script passes a flag.\n"
            b"- [[o]] 2026-09-10. A second line for it.\n"
            b"- [[bare]]\n"
            b"not a list line [[x]] here\n"))
        self.assertEqual(HC._existing_failures(d), {
            "o": ["2026-09-09. Every script passes a flag.",
                  "2026-09-10. A second line for it."],
            "bare": []})

    def test_the_index_file_and_other_files_are_not_notes(self):
        d = self.vault_dir({"notes.txt": FM, "a.md.bak": FM}, index=FM)
        self.assertEqual(HC._existing_failures(d), {})

    def test_a_binary_note_and_a_binary_index_do_not_raise(self):
        d = self.vault_dir({"n.md": b"\xff\xfe\x00---"},
                           index=b"\xff\xfe\x00---")
        self.assertEqual(HC._existing_failures(d), {"n": []})

    def test_an_unreadable_directory_raises_oserror(self):
        d = self.vault_dir({})
        with self.assertRaises(OSError):
            HC._existing_failures(os.path.join(d, "missing"))
        os.mkdir(os.path.join(d, "x.md"))
        with self.assertRaises(OSError):
            HC._existing_failures(d)

    def test_hostile_values_are_refused_never_read_or_crashed(self):
        for bad in (None, 0, True, -1, float("nan"), b"x", [], ["x"], {},
                    {"a": 1}, (), {1, 2}, object()):
            with self.assertRaises(ValueError):
                HC._existing_failures(bad)
            with self.assertRaises(ValueError):
                HC._note_symptom(bad)
        for bad in ("", "x"):
            with self.assertRaises(OSError):
                HC._existing_failures(bad + "-no-such-dir-n19b")
            with self.assertRaises(OSError):
                HC._note_symptom(bad + "-no-such-note-n19b")


EX_NOTE = ("---\ntype: failure\nstatus: open\nsymptom: \"%s\"\n---\n"
           "\n# existing\n" % EX_SYMPTOM).encode("utf-8")
EX_INDEX = ("# Failures\n\n## Handover ceremony 2026-10-01\n"
            "- [[existing-note]] 2026-10-01. AREA: export. SYMPTOM: %s. "
            "FIX: ship the file.\n" % EX_SYMPTOM).encode("utf-8")
UNRELATED = "the login prompt blocked a build on its first run of the day"


class DuplicateLessonRefusal(unittest.TestCase):
    """emit_vault_notes refuses a failure lesson that repeats a note already
    in the vault, names that note, writes the rest, and fails closed."""

    vault_dir = ExistingFailures.vault_dir

    def setUp(self):
        self.vault = self.vault_dir({"existing-note.md": EX_NOTE},
                                    index=EX_INDEX)
        self.index_path = os.path.join(self.vault, INDEX_NAME)
        self.index_text = self.read(self.index_path)

    @staticmethod
    def read(path):
        with open(path, encoding="utf-8") as f:
            return f.read()

    def emit(self, lessons, vault=None):
        return emit_vault_notes(vault or self.vault, lessons,
                                today="2026-10-11")

    def test_a_restated_failure_under_another_name_is_refused_and_names_the_note(self):
        written, refused, appended = self.emit(
            [lesson(name="export halts after packing", symptom=RESTATED)])
        self.assertEqual(written, [])
        self.assertEqual(len(refused), 1)
        self.assertIn("existing-note.md", refused[0])
        self.assertIn("symptom", refused[0])
        self.assertFalse(os.path.exists(
            os.path.join(self.vault, "export-halts-after-packing.md")))
        self.assertEqual(self.read(self.index_path), self.index_text)
        self.assertEqual(appended, 0)

    def test_the_rest_of_the_batch_still_writes(self):
        written, refused, appended = self.emit(
            [lesson(name="export halts after packing", symptom=RESTATED),
             lesson(name="login prompt hangs", symptom=UNRELATED)])
        self.assertEqual(written,
                         [os.path.join(self.vault, "login-prompt-hangs.md")])
        self.assertEqual(len(refused), 1)
        self.assertEqual(appended, 1)
        index = self.read(self.index_path)
        self.assertIn("[[login-prompt-hangs]]", index)
        self.assertNotIn("[[export-halts-after-packing]]", index)
        self.assertFalse(os.path.exists(
            os.path.join(self.vault, "export-halts-after-packing.md")))

    def test_a_renamed_note_is_refused_by_name(self):
        vault = self.vault_dir({EX_SLUG + ".md": EX_NOTE}, index=b"")
        written, refused, _ = self.emit(
            [lesson(name=EX_RENAMED, symptom=UNRELATED)], vault=vault)
        self.assertEqual(written, [])
        self.assertEqual(len(refused), 1)
        self.assertIn("name", refused[0])
        self.assertIn(EX_SLUG + ".md", refused[0])
        self.assertFalse(os.path.exists(
            os.path.join(vault, EX_RENAMED + ".md")))

    def test_a_finding_with_a_repeated_symptom_is_written(self):
        written, refused, _ = self.emit(
            [lesson(name="export halts after packing", type="finding",
                    symptom=RESTATED)])
        self.assertEqual(refused, [])
        self.assertEqual(written, [os.path.join(
            self.vault, "export-halts-after-packing.md")])

    def test_a_rerun_of_the_same_lesson_is_not_a_duplicate(self):
        one = [lesson(name="login prompt hangs", symptom=UNRELATED)]
        written, refused, appended = self.emit(one)
        self.assertEqual((len(written), refused, appended), (1, [], 1))
        written, refused, appended = self.emit(one)
        self.assertEqual(written,
                         [os.path.join(self.vault, "login-prompt-hangs.md")])
        self.assertEqual(refused, [])
        self.assertEqual(appended, 0)

    def test_two_lessons_with_one_symptom_in_one_batch_write_once(self):
        vault = self.vault_dir({}, index=b"")
        written, refused, appended = self.emit(
            [lesson(name="export halts after packing", symptom=RESTATED),
             lesson(name="allowlist gap on release", symptom=RESTATED)],
            vault=vault)
        self.assertEqual(written, [os.path.join(
            vault, "export-halts-after-packing.md")])
        self.assertEqual(len(refused), 1)
        self.assertIn("export-halts-after-packing.md", refused[0])
        self.assertEqual(appended, 1)
        self.assertFalse(os.path.exists(
            os.path.join(vault, "allowlist-gap-on-release.md")))

    def test_notes_that_cannot_be_read_refuse_every_failure_and_still_write_findings(self):
        os.mkdir(os.path.join(self.vault, "broken.md"))
        written, refused, appended = self.emit(
            [lesson(name="login prompt hangs", symptom=UNRELATED),
             lesson(name="a plain finding", type="finding")])
        self.assertEqual(written,
                         [os.path.join(self.vault, "a-plain-finding.md")])
        self.assertEqual(len(refused), 1)
        self.assertIn("could not be read", refused[0])
        self.assertIn(self.vault, refused[0])
        self.assertFalse(os.path.exists(
            os.path.join(self.vault, "login-prompt-hangs.md")))
        self.assertEqual(self.read(self.index_path), self.index_text)
        self.assertEqual(appended, 0)

    def test_the_cli_exits_1_names_the_note_and_writes_the_rest(self):
        lf = os.path.join(self.vault_dir({}), "lessons.json")
        with open(lf, "w", encoding="utf-8") as f:
            json.dump([lesson(name="export halts after packing",
                              symptom=RESTATED),
                       lesson(name="login prompt hangs", symptom=UNRELATED)],
                      f)
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            code = main(["--emit-vault", self.vault, "--lesson-file", lf])
        self.assertEqual(code, 1)
        self.assertIn("REFUSED", buf.getvalue())
        self.assertIn("existing-note.md", buf.getvalue())
        self.assertTrue(os.path.exists(
            os.path.join(self.vault, "login-prompt-hangs.md")))

    def test_hostile_symptoms_and_other_refusals_keep_their_own_path(self):
        # no symptom key, None or "": no symptom words, only the name matches
        bare = lesson(name="login prompt hangs")
        del bare["symptom"]
        written, refused, _ = self.emit(
            [bare, lesson(name="tool one stalls", symptom=None),
             lesson(name="tool two stalls", symptom="")])
        self.assertEqual((len(written), refused), (3, []))
        vault = self.vault_dir({EX_SLUG + ".md": EX_NOTE}, index=b"")
        renamed = lesson(name=EX_RENAMED)
        del renamed["symptom"]
        written, refused, _ = self.emit([renamed], vault=vault)
        self.assertEqual(written, [])
        self.assertIn("matched on name", refused[0])
        # a lesson refused for a bad status keeps its own reason
        written, refused, _ = self.emit(
            [lesson(name="export halts", symptom=RESTATED, status="bogus")])
        self.assertEqual(written, [])
        self.assertEqual(len(refused), 1)
        self.assertIn("bogus", refused[0])
        self.assertNotIn("duplicates", refused[0])


if __name__ == "__main__":
    unittest.main()

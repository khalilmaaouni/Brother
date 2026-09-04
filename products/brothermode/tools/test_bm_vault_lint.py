#!/usr/bin/env python3
"""Calibration for tools/bm_vault_lint.py, the frontmatter schema linter.

Each rule gets its own fixture carrying exactly the violation that rule
exists to catch, and a companion assertion that a clean note of the same
shape passes. Every test in TheSixRules was calibrated by hand: comment out
the corresponding call in bm_vault_lint.RULE_NAMES (purging tools/__pycache__
between swaps so the stale .pyc is never what actually ran), confirm the
named test goes red, then restore it.

No em or en dashes anywhere in this file.
"""
import contextlib
import io
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import bm_vault_lint as lint  # noqa: E402


def note(id_="n-0123456789abcdef", type_="reference", status="standing",
         created="2026-08-30", authority=None, promotion=None, symptom=None,
         extra_lines=None, body="\n# a note\n"):
    lines = ["---", "id: %s" % id_, "type: %s" % type_, "status: %s" % status,
              "created: %s" % created]
    if authority is not None:
        lines.append("authority: %s" % authority)
    if promotion is not None:
        lines.append("promotion: %s" % promotion)
    if symptom is not None:
        lines.append("symptom: %s" % symptom)
    if extra_lines:
        lines.extend(extra_lines)
    lines.append("---")
    return "\n".join(lines) + body


class TheSixRules(unittest.TestCase):
    def _findings(self, rule_name, text):
        rules = lint.Rules()
        return getattr(rules, rule_name)([("x.md", text)])

    # required_fields ------------------------------------------------------
    def test_required_fields_catches_a_missing_base_field(self):
        text = note().replace("status: standing\n", "")
        findings = self._findings("required_fields", text)
        self.assertTrue(any("status" in m for _r, m in findings), findings)

    def test_required_fields_catches_failure_missing_symptom(self):
        text = note(type_="failure")
        findings = self._findings("required_fields", text)
        self.assertTrue(any("symptom" in m for _r, m in findings), findings)

    def test_required_fields_clean_note_passes(self):
        text = note(type_="failure", symptom="thing broke")
        self.assertEqual(self._findings("required_fields", text), [])

    # P11: two new types, data_semantic and test_oracle, each requiring
    # source_receipt and human_approved. ------------------------------
    def test_required_fields_catches_data_semantic_missing_both(self):
        text = note(type_="data_semantic")
        findings = self._findings("required_fields", text)
        self.assertTrue(any("source_receipt" in m for _r, m in findings), findings)
        self.assertTrue(any("human_approved" in m for _r, m in findings), findings)

    def test_required_fields_data_semantic_clean_note_passes(self):
        text = note(type_="data_semantic",
                    extra_lines=["source_receipt: run-2026-09-04",
                                 "human_approved: true"])
        self.assertEqual(self._findings("required_fields", text), [])

    def test_required_fields_catches_test_oracle_missing_both(self):
        text = note(type_="test_oracle")
        findings = self._findings("required_fields", text)
        self.assertTrue(any("source_receipt" in m for _r, m in findings), findings)
        self.assertTrue(any("human_approved" in m for _r, m in findings), findings)

    def test_required_fields_test_oracle_clean_note_passes(self):
        text = note(type_="test_oracle",
                    extra_lines=["source_receipt: run-2026-09-04",
                                 "human_approved: false"])
        self.assertEqual(self._findings("required_fields", text), [])

    def test_required_fields_existing_types_never_require_source_receipt(self):
        # The two new fields bind data_semantic/test_oracle only; an
        # ordinary note (the vault's existing 898 notes' shape) is
        # untouched, never re-linted into requiring a field it never had.
        text = note(type_="reference")
        self.assertEqual(self._findings("required_fields", text), [])

    # id_format --------------------------------------------------------
    def test_id_format_catches_a_bad_id(self):
        text = note(id_="not-an-id")
        findings = self._findings("id_format", text)
        self.assertTrue(any("not-an-id" in m for _r, m in findings), findings)

    def test_id_format_clean_note_passes(self):
        self.assertEqual(self._findings("id_format", note()), [])

    # date_format --------------------------------------------------------
    def test_date_format_catches_a_bad_created_date(self):
        text = note(created="30th August 2026")
        findings = self._findings("date_format", text)
        self.assertTrue(any("created" in m for _r, m in findings), findings)

    def test_date_format_catches_a_bad_temporal_field(self):
        text = note(extra_lines=["valid_from: not-a-date"])
        findings = self._findings("date_format", text)
        self.assertTrue(any("valid_from" in m for _r, m in findings), findings)

    def test_date_format_clean_note_passes(self):
        text = note(extra_lines=["valid_from: 2026-08-01"])
        self.assertEqual(self._findings("date_format", text), [])

    # authority_vocab --------------------------------------------------------
    def test_authority_vocab_catches_an_unknown_level(self):
        text = note(authority="very_important")
        findings = self._findings("authority_vocab", text)
        self.assertTrue(any("very_important" in m for _r, m in findings), findings)

    def test_authority_vocab_clean_note_passes(self):
        self.assertEqual(self._findings("authority_vocab", note(authority="casual")), [])

    # lifecycle_vocab --------------------------------------------------------
    def test_lifecycle_vocab_catches_an_unknown_state(self):
        text = note(promotion="blessed")
        findings = self._findings("lifecycle_vocab", text)
        self.assertTrue(any("blessed" in m for _r, m in findings), findings)

    def test_lifecycle_vocab_clean_note_passes(self):
        self.assertEqual(self._findings("lifecycle_vocab", note(promotion="candidate")), [])

    # duplicate_fields --------------------------------------------------------
    def test_duplicate_fields_catches_a_repeated_key(self):
        text = note(extra_lines=["status: closed"])
        findings = self._findings("duplicate_fields", text)
        self.assertTrue(any("'status'" in m for _r, m in findings), findings)

    def test_duplicate_fields_clean_note_passes(self):
        self.assertEqual(self._findings("duplicate_fields", note()), [])


class ANoDataRuleNeverSilentlyPasses(unittest.TestCase):
    """A missing contract module must produce a named NO-DATA finding, never
    an empty (falsely clean) list. Simulated by monkeypatching the loaded
    module attribute rather than touching real files on disk."""

    def test_missing_authority_module_reports_no_data_not_clean(self):
        rules = lint.Rules()
        rules.authority_mod = None
        findings = rules.authority_vocab([("x.md", note(authority="casual"))])
        self.assertEqual(len(findings), 1)
        self.assertIn("NO-DATA", findings[0][1])
        self.assertIn("bm_vault_authority", findings[0][1])

    def test_missing_lifecycle_module_reports_no_data_not_clean(self):
        rules = lint.Rules()
        rules.lifecycle_mod = None
        findings = rules.lifecycle_vocab([("x.md", note())])
        self.assertEqual(len(findings), 1)
        self.assertIn("NO-DATA", findings[0][1])

    def test_missing_ids_module_reports_no_data_not_clean(self):
        rules = lint.Rules()
        rules.ids_mod = None
        findings = rules.id_format([("x.md", note())])
        self.assertEqual(len(findings), 1)
        self.assertIn("NO-DATA", findings[0][1])

    def test_missing_temporal_module_reports_no_data_not_clean(self):
        rules = lint.Rules()
        rules.temporal_mod = None
        findings = rules.date_format([("x.md", note())])
        self.assertEqual(len(findings), 1)
        self.assertIn("NO-DATA", findings[0][1])


class TheCheckReadsARealTree(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="bm-vault-lint-")
        self.vault = os.path.join(self.tmp, "vault")
        os.makedirs(self.vault)

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _write(self, name, text):
        with open(os.path.join(self.vault, name), "w", encoding="utf-8") as fh:
            fh.write(text)

    def test_a_violation_is_reported_and_exits_1(self):
        self._write("bad.md", note(id_="nope"))
        self.assertEqual(lint.cmd_check(self.vault), 1)

    def test_a_clean_tree_exits_0(self):
        self._write("ok.md", note())
        self.assertEqual(lint.cmd_check(self.vault), 0)

    def test_no_markdown_at_all_is_no_data(self):
        self.assertEqual(lint.cmd_check(self.vault), 2)

    def test_missing_vault_directory_is_no_data(self):
        self.assertEqual(lint.cmd_check(os.path.join(self.tmp, "nowhere")), 2)


class FixIsMechanicalOnly(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="bm-vault-lint-fix-")
        self.vault = os.path.join(self.tmp, "vault")
        os.makedirs(self.vault)

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _write(self, name, text):
        path = os.path.join(self.vault, name)
        with open(path, "w", encoding="utf-8") as fh:
            fh.write(text)
        return path

    def test_reorder_diffs_to_ordering_only(self):
        scrambled = ("---\nstatus: standing\ncreated: 2026-08-30\n"
                     "id: n-0123456789abcdef\ntype: reference\n---\nbody text\n")
        path = self._write("scrambled.md", scrambled)
        self.assertEqual(lint.cmd_fix(self.vault, apply_changes=True), 0)
        with open(path, encoding="utf-8") as fh:
            after = fh.read()
        self.assertNotEqual(scrambled, after)
        # same lines, same set, only the order (and hence the file) differs
        self.assertEqual(sorted(scrambled.splitlines()), sorted(after.splitlines()))
        before_field_order = [ln.split(":")[0] for ln in scrambled.splitlines()
                               if ":" in ln and ln != "---"]
        after_field_order = [ln.split(":")[0] for ln in after.splitlines()
                              if ":" in ln and ln != "---"]
        self.assertEqual(after_field_order, ["id", "type", "created", "status"])
        self.assertNotEqual(before_field_order, after_field_order)

    def test_dry_run_writes_nothing(self):
        scrambled = ("---\nstatus: standing\nid: n-0123456789abcdef\n"
                     "type: reference\ncreated: 2026-08-30\n---\nbody\n")
        path = self._write("dry.md", scrambled)
        lint.cmd_fix(self.vault, apply_changes=False)
        with open(path, encoding="utf-8") as fh:
            self.assertEqual(fh.read(), scrambled)

    def test_trailing_whitespace_in_frontmatter_is_stripped(self):
        text = "---\nid: n-0123456789abcdef   \ntype: reference\n---\nbody\n"
        path = self._write("trail.md", text)
        lint.cmd_fix(self.vault, apply_changes=True)
        with open(path, encoding="utf-8") as fh:
            after = fh.read()
        self.assertNotIn("   \n", after)

    def test_verified_by_single_quoted_becomes_double_quoted(self):
        text = ("---\nid: n-0123456789abcdef\ntype: reference\n"
                "verified-by: 'exit 0'\n---\nbody\n")
        path = self._write("quote.md", text)
        lint.cmd_fix(self.vault, apply_changes=True)
        with open(path, encoding="utf-8") as fh:
            after = fh.read()
        self.assertIn('verified-by: "exit 0"', after)

    def test_dataview_inline_field_in_body_survives_byte_identically(self):
        text = ("---\nstatus: standing\nid: n-0123456789abcdef\ntype: reference\n"
                "---\nSome intro.\nKey:: Value\nMore text.\n")
        path = self._write("dataview.md", text)
        _block, end = lint.frontmatter_span(text)
        original_body = text[end:]
        lint.cmd_fix(self.vault, apply_changes=True)
        with open(path, encoding="utf-8") as fh:
            after = fh.read()
        _after_block, after_end = lint.frontmatter_span(after)
        self.assertEqual(after[after_end:], original_body)
        self.assertIn("Key:: Value", after)

    def test_obsidian_tasks_emoji_marker_survives_byte_identically(self):
        text = ("---\nstatus: standing\nid: n-0123456789abcdef\ntype: reference\n"
                "---\n- [ ] Do the thing \U0001F4C5 2026-09-01\n✅ 2026-08-30\n")
        path = self._write("tasks.md", text)
        _block, end = lint.frontmatter_span(text)
        original_body = text[end:]
        lint.cmd_fix(self.vault, apply_changes=True)
        with open(path, encoding="utf-8") as fh:
            after = fh.read()
        _after_block, after_end = lint.frontmatter_span(after)
        self.assertEqual(after[after_end:], original_body)


class FixDerivesWhatItCannotInvent(unittest.TestCase):
    """E55: fix supplies created and id where they are absent, and nowhere
    else. Structure mirrors FixIsMechanicalOnly above (temp vault, one _write
    helper, cmd_fix driven directly), with a real git repository underneath so
    the created rule is proven against history rather than against a stub.

    Driven BACKWARDS on purpose: the two tests that matter most are the ones
    where nothing may be written, an untracked note and a note that already
    carries a value, because a derivation that fires there is a fabrication
    rather than a fix.
    """

    def setUp(self):
        if shutil.which("git") is None:
            self.skipTest("git is not on PATH, so the created rule cannot be driven")
        self.tmp = tempfile.mkdtemp(prefix="bm-vault-lint-derive-")
        self.vault = os.path.join(self.tmp, "vault")
        os.makedirs(self.vault)
        self._git("init", "-q")
        self._git("config", "user.email", "test@example.invalid")
        self._git("config", "user.name", "vault lint test")

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _git(self, *args, **kwargs):
        """git inside the temp vault. A non-zero exit fails the test loudly:
        a silently failed fixture commit would make the created rule look
        like it read NO-DATA correctly when it never had history to read."""
        env = dict(os.environ)
        when = kwargs.pop("when", None)
        if when is not None:
            env["GIT_AUTHOR_DATE"] = when
            env["GIT_COMMITTER_DATE"] = when
        proc = subprocess.run(("git",) + args, cwd=self.vault, env=env,
                              stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
        self.assertEqual(proc.returncode, 0,
                         proc.stdout.decode("utf-8", errors="replace"))
        return proc.stdout.decode("utf-8", errors="replace")

    def _write(self, name, text):
        path = os.path.join(self.vault, name)
        with open(path, "w", encoding="utf-8") as fh:
            fh.write(text)
        return path

    def _read(self, name):
        with open(os.path.join(self.vault, name), encoding="utf-8") as fh:
            return fh.read()

    def _fix(self, apply_changes=True):
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            code = lint.cmd_fix(self.vault, apply_changes=apply_changes)
        self.assertEqual(code, 0, buf.getvalue())
        return buf.getvalue()

    def _fmap(self, name):
        block, _end = lint.frontmatter_span(self._read(name))
        return lint._field_map(lint._iter_fields(block))

    def test_created_comes_from_the_first_add_not_a_later_one(self):
        """The history is built so the two candidate readings disagree: the
        note is added, deleted, then added again, which is the only shape
        where `git log --diff-filter=A` prints more than one line. Reading the
        newest line would stamp the re-add date, so this test is what pins
        the oldest line as the answer."""
        text = note(created="").replace("created: \n", "")
        self._write("dated.md", text)
        self._git("add", "dated.md")
        self._git("commit", "-q", "-m", "add the note", when="2026-07-04T09:00:00+09:00")
        self._git("rm", "-q", "dated.md")
        self._git("commit", "-q", "-m", "remove the note", when="2026-08-01T09:00:00+09:00")
        self._write("dated.md", text)
        self._git("add", "dated.md")
        self._git("commit", "-q", "-m", "add it back", when="2026-08-21T09:00:00+09:00")
        adds = self._git("log", "--diff-filter=A", "--follow", "--format=%ad",
                         "--date=short", "--", "dated.md")
        self.assertEqual([ln for ln in adds.split() if ln],
                          ["2026-08-21", "2026-07-04"], adds)
        out = self._fix()
        self.assertIn("derived created=2026-07-04 from git first commit: dated.md", out)
        self.assertEqual(self._fmap("dated.md")["created"], "2026-07-04")

    def test_created_survives_a_rename_as_the_date_the_note_first_appeared(self):
        text = note(created="").replace("created: \n", "")
        self._write("before-rename.md", text)
        self._git("add", "before-rename.md")
        self._git("commit", "-q", "-m", "add", when="2026-03-09T09:00:00+09:00")
        self._git("mv", "before-rename.md", "after-rename.md")
        self._git("commit", "-q", "-m", "rename", when="2026-08-25T09:00:00+09:00")
        out = self._fix()
        self.assertIn("derived created=2026-03-09 from git first commit: after-rename.md", out)
        self.assertEqual(self._fmap("after-rename.md")["created"], "2026-03-09")

    def test_an_untracked_note_reads_no_data_and_stays_without_created(self):
        text = note(created="").replace("created: \n", "")
        self._write("untracked.md", text)
        out = self._fix()
        self.assertIn("NO-DATA: created for untracked.md: no git first-commit date", out)
        self.assertNotIn("created", self._fmap("untracked.md"))
        self.assertNotIn("derived created=", out)

    def test_a_vault_that_is_not_a_repository_reads_no_data(self):
        shutil.rmtree(os.path.join(self.vault, ".git"))
        text = note(created="").replace("created: \n", "")
        self._write("orphan.md", text)
        out = self._fix()
        self.assertIn("NO-DATA: created for orphan.md", out)
        self.assertNotIn("created", self._fmap("orphan.md"))

    def test_an_existing_created_is_never_overwritten(self):
        self._write("kept.md", note(created="2020-01-02"))
        self._git("add", "kept.md")
        self._git("commit", "-q", "-m", "add", when="2026-08-30T09:00:00+09:00")
        out = self._fix()
        self.assertEqual(self._fmap("kept.md")["created"], "2020-01-02")
        self.assertNotIn("derived created=", out)

    def test_a_template_placeholder_created_is_kept_not_derived(self):
        self._write("template.md", note(created="YYYY-MM-DD"))
        self._git("add", "template.md")
        self._git("commit", "-q", "-m", "add", when="2026-08-30T09:00:00+09:00")
        out = self._fix()
        self.assertEqual(self._fmap("template.md")["created"], "YYYY-MM-DD")
        self.assertIn("kept template placeholder created=YYYY-MM-DD: template.md", out)

    def test_a_non_date_created_such_as_unset_is_derived_over(self):
        self._write("unset.md", note(created="unset"))
        self._git("add", "unset.md")
        self._git("commit", "-q", "-m", "add", when="2026-06-11T09:00:00+09:00")
        self._fix()
        self.assertEqual(self._fmap("unset.md")["created"], "2026-06-11")

    def test_an_existing_id_is_never_overwritten_even_when_malformed(self):
        self._write("hand-id.md", note(id_="n-a-hand-written-id"))
        out = self._fix()
        self.assertEqual(self._fmap("hand-id.md")["id"], "n-a-hand-written-id")
        self.assertNotIn("derived id=", out)

    def test_a_derived_id_matches_the_vault_id_shape(self):
        text = note().replace("id: n-0123456789abcdef\n", "")
        self._write("no-id.md", text)
        out = self._fix()
        new_id = self._fmap("no-id.md")["id"]
        self.assertRegex(new_id, r"^n-[0-9a-f]{16}$")
        self.assertIsNotNone(lint.Rules().ids_mod, "bm_vault_ids must be loadable")
        self.assertTrue(lint.Rules().ids_mod.ID_VALUE_RE.match(new_id), new_id)
        self.assertIn("derived id=%s from the note path: no-id.md" % new_id, out)
        self.assertEqual(new_id, lint.derive_path_id("no-id.md"))

    def test_a_derived_id_does_not_collide_with_one_already_in_use(self):
        taken = lint.derive_path_id("clash.md")
        self._write("holder.md", note(id_=taken))
        self._write("clash.md", note().replace("id: n-0123456789abcdef\n", ""))
        self._fix()
        self.assertEqual(self._fmap("holder.md")["id"], taken)
        self.assertNotEqual(self._fmap("clash.md")["id"], taken)
        self.assertRegex(self._fmap("clash.md")["id"], r"^n-[0-9a-f]{16}$")

    def test_a_second_fix_run_changes_nothing(self):
        text = note().replace("id: n-0123456789abcdef\n", "")
        text = text.replace("created: 2026-08-30\n", "")
        self._write("both.md", text)
        self._git("add", "both.md")
        self._git("commit", "-q", "-m", "add", when="2026-05-05T09:00:00+09:00")
        self._fix()
        after_first = self._read("both.md")
        self.assertIn("created: 2026-05-05", after_first)
        out = self._fix()
        self.assertEqual(self._read("both.md"), after_first)
        self.assertNotIn("derived created=", out)
        self.assertNotIn("derived id=", out)

    def test_a_dry_run_writes_nothing_it_would_derive(self):
        text = note().replace("id: n-0123456789abcdef\n", "")
        path = self._write("dry-derive.md", text)
        before = self._read("dry-derive.md")
        out = self._fix(apply_changes=False)
        self.assertIn("would: derived id=", out)
        self.assertEqual(self._read("dry-derive.md"), before)
        self.assertTrue(os.path.exists(path))


class CheckJson(unittest.TestCase):
    """VB7-02: --json on check. Prose stays byte-identical when --json is
    absent (cmd_check's default json_out=False path is untouched, and every
    prose test above still passes unchanged); this covers what --json adds."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="bm-vault-lint-json-")
        self.vault = os.path.join(self.tmp, "vault")
        os.makedirs(self.vault)

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _write(self, name, text):
        with open(os.path.join(self.vault, name), "w", encoding="utf-8") as fh:
            fh.write(text)

    def _run_json(self, vault):
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            code = lint.cmd_check(vault, json_out=True)
        return code, buf.getvalue()

    def test_pass_json_matches_prose_and_exit_code(self):
        self._write("ok.md", note())
        self.assertEqual(lint.cmd_check(self.vault), 0)
        code, out = self._run_json(self.vault)
        self.assertEqual(code, 0, out)
        data = json.loads(out)
        self.assertEqual(data["verdict"], "PASS", out)
        self.assertEqual(data["findings"], [], out)
        self.assertEqual(data["counts"]["violation_count"], 0, out)

    def test_fail_json_matches_prose_violation_count(self):
        self._write("bad.md", note(id_="nope"))
        prose_code = lint.cmd_check(self.vault)
        self.assertEqual(prose_code, 1)
        code, out = self._run_json(self.vault)
        self.assertEqual(code, 1, out)
        data = json.loads(out)
        self.assertEqual(data["verdict"], "FAIL", out)
        self.assertEqual(data["counts"]["violation_count"], 1, out)
        self.assertEqual(len(data["findings"]), 1, out)
        self.assertEqual(data["findings"][0]["path"], "bad.md", out)

    def test_no_data_json_matches_exit_code(self):
        code, out = self._run_json(self.vault)
        self.assertEqual(code, 2, out)
        data = json.loads(out)
        self.assertEqual(data["verdict"], "NO-DATA", out)


if __name__ == "__main__":
    unittest.main()

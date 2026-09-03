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

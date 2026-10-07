"""Tests for the L1b.6 doc linter (tests/e2e/antigravity/doc_lint.py)."""

import json
import os
import tempfile
import unittest

from tests.e2e.antigravity import doc_lint


class TestDocLinter(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.tmp = self._tmp.name
        self.doc_path = os.path.join(self.tmp, "doc.md")
        self.log_path = os.path.join(self.tmp, "log.json")

    def write(self, path, text):
        with open(path, "w", encoding="utf-8") as handle:
            handle.write(text)

    def write_bytes(self, path, data):
        with open(path, "wb") as handle:
            handle.write(data)

    def entry(self, raw_out_sha256, **overrides):
        record = {
            "run_id": "a" * 32,
            "raw_out_sha256": raw_out_sha256,
            "event_name": "pre_tool",
            "outcome": "pass",
            "host_version": "1.0.0",
            "hooks_sha256": "b" * 64,
            "manifest_sha256": "c" * 64,
        }
        record.update(overrides)
        return record

    def write_log(self, records):
        self.write(self.log_path, json.dumps(records))

    def doc_with_fence(self, anchor=None):
        lines = []
        if anchor:
            lines.append("<!-- evidence: %s -->" % anchor)
        lines.append("```")
        lines.append("ls -la")
        lines.append("```")
        lines.append("")
        return "\n".join(lines)

    def test_clean_doc_and_log_ok(self):
        anchor = "0" * 64
        self.write(self.doc_path, self.doc_with_fence(anchor))
        self.write_log([self.entry(anchor)])
        result = doc_lint.lint_doc(self.doc_path, self.log_path)
        self.assertTrue(result["ok"], result["reason"])

    def test_untraced_claim_fails(self):
        self.write(self.doc_path, self.doc_with_fence())
        self.write_log([self.entry("d" * 64)])
        result = doc_lint.lint_doc(self.doc_path, self.log_path)
        self.assertIsInstance(result, dict)
        self.assertFalse(result["ok"])
        self.assertTrue(result["untraced"])

    def test_untraced_no_anchor_returns_refusal(self):
        self.write(self.doc_path, self.doc_with_fence())
        self.write_log([self.entry("d" * 64)])
        result = doc_lint.lint_doc(self.doc_path, self.log_path)
        self.assertIsInstance(result, dict)
        self.assertFalse(result["ok"])
        self.assertIn("no evidence anchor", result["reason"])

    def test_anchor_not_in_log_returns_refusal(self):
        self.write(self.doc_path, self.doc_with_fence("3" * 64))
        self.write_log([self.entry("4" * 64)])
        result = doc_lint.lint_doc(self.doc_path, self.log_path)
        self.assertIsInstance(result, dict)
        self.assertFalse(result["ok"])
        self.assertIn("not in log", result["reason"])

    def test_sentence_claim_needs_anchor(self):
        self.write(self.doc_path, "The hook was verified against the host.\n")
        self.write_log([self.entry("d" * 64)])
        result = doc_lint.lint_doc(self.doc_path, self.log_path)
        self.assertFalse(result["ok"])
        self.assertTrue(result["untraced"])

    def test_forbidden_arg_inspection_fails(self):
        anchor = "1" * 64
        self.write(self.doc_path, self.doc_with_fence(anchor) + "\nThe adapter performs arg inspection before every call.\n")
        self.write_log([self.entry(anchor)])
        result = doc_lint.lint_doc(self.doc_path, self.log_path)
        self.assertFalse(result["ok"])
        self.assertTrue(result["forbidden"])

    def test_forbidden_mcp_serving_fails(self):
        anchor = "2" * 64
        self.write(self.doc_path, self.doc_with_fence(anchor) + "\nThe plugin serves an MCP endpoint to clients.\n")
        self.write_log([self.entry(anchor)])
        result = doc_lint.lint_doc(self.doc_path, self.log_path)
        self.assertFalse(result["ok"])
        self.assertTrue(result["forbidden"])

    def test_forbidden_stop_enforcement_fails(self):
        anchor = "3" * 64
        self.write(self.doc_path, self.doc_with_fence(anchor) + "\nStop enforcement is active for every run.\n")
        self.write_log([self.entry(anchor)])
        result = doc_lint.lint_doc(self.doc_path, self.log_path)
        self.assertFalse(result["ok"])
        self.assertTrue(result["forbidden"])

    def test_version_pin_mismatch_fails(self):
        anchor = "4" * 64
        self.write(self.doc_path, self.doc_with_fence(anchor))
        self.write_log([
            self.entry("5" * 64, host_version="1.0.0"),
            self.entry(anchor, host_version="2.0.0"),
        ])
        result = doc_lint.lint_doc(self.doc_path, self.log_path)
        self.assertFalse(result["ok"])
        self.assertTrue(result["version_mismatch"])

    def test_version_pin_mismatch_host_version_fails(self):
        anchor = "6" * 64
        self.write(self.doc_path, self.doc_with_fence(anchor))
        self.write_log([
            self.entry("7" * 64, host_version="1.0.0"),
            self.entry(anchor, host_version="9.9.9"),
        ])
        result = doc_lint.lint_doc(self.doc_path, self.log_path)
        self.assertFalse(result["ok"])
        self.assertTrue(result["version_mismatch"])

    def test_version_pin_mismatch_hooks_sha_fails(self):
        anchor = "8" * 64
        self.write(self.doc_path, self.doc_with_fence(anchor))
        self.write_log([
            self.entry("9" * 64, hooks_sha256="b" * 64),
            self.entry(anchor, hooks_sha256="d" * 64),
        ])
        result = doc_lint.lint_doc(self.doc_path, self.log_path)
        self.assertFalse(result["ok"])
        self.assertTrue(result["version_mismatch"])

    def test_version_pin_mismatch_manifest_sha_fails(self):
        anchor = "a" * 64
        self.write(self.doc_path, self.doc_with_fence(anchor))
        self.write_log([
            self.entry("b" * 64, manifest_sha256="c" * 64),
            self.entry(anchor, manifest_sha256="e" * 64),
        ])
        result = doc_lint.lint_doc(self.doc_path, self.log_path)
        self.assertFalse(result["ok"])
        self.assertTrue(result["version_mismatch"])

    def test_corrupt_anchor_pre_invocation_pass_fails(self):
        anchor = "c" * 64
        self.write(self.doc_path, self.doc_with_fence(anchor))
        self.write_log([self.entry(anchor, event_name="pre_invocation", outcome="pass")])
        result = doc_lint.lint_doc(self.doc_path, self.log_path)
        self.assertFalse(result["ok"])
        self.assertTrue(result["corrupt_anchor"])

    def test_corrupt_anchor_post_tool_pass_fails(self):
        anchor = "e" * 64
        self.write(self.doc_path, self.doc_with_fence(anchor))
        self.write_log([self.entry(anchor, event_name="post_tool", outcome="pass")])
        result = doc_lint.lint_doc(self.doc_path, self.log_path)
        self.assertFalse(result["ok"])
        self.assertTrue(result["corrupt_anchor"])

    def test_blocking_cases_return_refusal_not_exception(self):
        anchor = "f" * 64
        cases = (
            (self.doc_with_fence(), [self.entry(anchor)]),
            (self.doc_with_fence(anchor), [self.entry("1" * 64)]),
            (self.doc_with_fence(anchor), [
                self.entry("2" * 64, host_version="1.0.0"),
                self.entry(anchor, host_version="2.0.0"),
            ]),
            (self.doc_with_fence(anchor), [self.entry(anchor, event_name="post_tool", outcome="pass")]),
            (self.doc_with_fence(anchor) + "\nStop enforcement is active.\n", [self.entry(anchor)]),
        )
        for doc_text, records in cases:
            self.write(self.doc_path, doc_text)
            self.write_log(records)
            result = doc_lint.lint_doc(self.doc_path, self.log_path)
            self.assertIsInstance(result, dict)
            self.assertFalse(result["ok"], result["reason"])

    def test_hostile_input_refused(self):
        self.write(self.doc_path, self.doc_with_fence())
        self.write_log([self.entry("1" * 64)])
        for bad in (None, float("nan"), 17, True, " ", self.tmp):
            with self.assertRaises(ValueError):
                doc_lint.lint_doc(bad, self.log_path)
            with self.assertRaises(ValueError):
                doc_lint.lint_doc(self.doc_path, bad)
        with self.assertRaises(ValueError):
            doc_lint.lint_doc(os.path.join(self.tmp, "missing.md"), self.log_path)
        with self.assertRaises(ValueError):
            doc_lint.lint_doc(self.doc_path, os.path.join(self.tmp, "missing.json"))

    def test_null_byte_path_refused(self):
        with self.assertRaises(ValueError):
            doc_lint.lint_doc("bad\x00path", self.log_path)

    def test_non_utf8_refused(self):
        self.write_bytes(self.doc_path, b"\xff\xfe\x00bad")
        self.write_log([self.entry("2" * 64)])
        with self.assertRaises(ValueError):
            doc_lint.lint_doc(self.doc_path, self.log_path)

    def test_bad_log_json_refused(self):
        self.write(self.doc_path, self.doc_with_fence())
        self.write(self.log_path, "{not json")
        with self.assertRaises(ValueError):
            doc_lint.lint_doc(self.doc_path, self.log_path)

    def test_empty_log_refused(self):
        self.write(self.doc_path, self.doc_with_fence())
        self.write(self.log_path, "   \n")
        with self.assertRaises(ValueError):
            doc_lint.lint_doc(self.doc_path, self.log_path)

    def test_log_entry_not_object_refused(self):
        self.write(self.doc_path, self.doc_with_fence())
        self.write_log([1, 2])
        with self.assertRaises(ValueError):
            doc_lint.lint_doc(self.doc_path, self.log_path)

    def test_deeply_nested_log_refused(self):
        self.write(self.doc_path, self.doc_with_fence())
        self.write_bytes(self.log_path, b"[" * 200000)
        with self.assertRaises(ValueError):
            doc_lint.lint_doc(self.doc_path, self.log_path)

    def test_unhashable_run_id_refused(self):
        anchor = "3" * 64
        self.write(self.doc_path, self.doc_with_fence(anchor))
        records = [self.entry(anchor)]
        records[0]["run_id"] = ["unhashable"]
        self.write_log(records)
        result = doc_lint.lint_doc(self.doc_path, self.log_path)
        self.assertIsInstance(result, dict)
        self.assertFalse(result["ok"])

    def test_main_unhashable_run_id(self):
        anchor = "4" * 64
        self.write(self.doc_path, self.doc_with_fence(anchor))
        records = [self.entry(anchor)]
        records[0]["run_id"] = {"nested": 1}
        self.write_log(records)
        code = doc_lint.main([self.doc_path, self.log_path])
        self.assertNotEqual(code, 0)

    def test_main_exit_codes(self):
        anchor = "5" * 64
        self.write(self.doc_path, self.doc_with_fence(anchor))
        self.write_log([self.entry(anchor)])
        self.assertEqual(doc_lint.main([self.doc_path, self.log_path]), 0)
        self.write(self.doc_path, self.doc_with_fence())
        self.assertNotEqual(doc_lint.main([self.doc_path, self.log_path]), 0)

    def test_main_missing_doc_nonzero(self):
        self.write_log([self.entry("6" * 64)])
        code = doc_lint.main([os.path.join(self.tmp, "missing-doc.md"), self.log_path])
        self.assertNotEqual(code, 0)

    def test_main_missing_log_nonzero(self):
        self.write(self.doc_path, self.doc_with_fence("7" * 64))
        code = doc_lint.main([self.doc_path, os.path.join(self.tmp, "missing-log.json")])
        self.assertNotEqual(code, 0)

    def test_main_refuses_bad_arity(self):
        self.assertEqual(doc_lint.main([]), 2)
        self.assertEqual(doc_lint.main("not-a-list"), 2)
        self.assertEqual(doc_lint.main(17), 2)
        self.assertEqual(doc_lint.main([self.doc_path, self.log_path, "extra"]), 2)

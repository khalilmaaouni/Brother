"""L1b.8 install doc tests: rendering, verification and stranger harness.

Every test builds its own evidence log and document in a temporary folder, so
the suite runs with an empty HOME and no repository state. No test imports
subprocess: the stranger harness is exercised through an injected command
runner, and the allow listed runner in run_e2e is only resolved lazily by the
harness itself.
"""

from __future__ import annotations

import hashlib
import json
import os
import tempfile
import unittest

from tests.e2e.antigravity import doc_lint


PINS = {
    "host_name": "agy",
    "host_version": "1.0.0",
    "manifest_sha256": "aa" * 32,
    "hooks_sha256": "bb" * 32,
    "mcp_config_sha256": "cc" * 32,
}

RUN_ID = "d" * 32


def _sha(text):
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _entry(sequence, event_name, tool_name, decision_verbatim, decision_kind, outcome):
    return {
        "run_id": RUN_ID,
        "environment": "sandbox",
        "started_at": "2026-09-20T00:00:00Z",
        "host_name": PINS["host_name"],
        "host_version": PINS["host_version"],
        "os_platform": "linux",
        "plugin_path_abs": "/scratch/plugin",
        "manifest_sha256": PINS["manifest_sha256"],
        "hooks_sha256": PINS["hooks_sha256"],
        "mcp_config_sha256": PINS["mcp_config_sha256"],
        "event_name": event_name,
        "event_sequence": sequence,
        "tool_name": tool_name,
        "tool_input_sha256": _sha("toolin-%d" % sequence),
        "raw_in_path": "/scratch/raw/%d.in" % sequence,
        "raw_in_sha256": _sha("rawin-%d" % sequence),
        "raw_out_path": "/scratch/raw/%d.out" % sequence,
        "raw_out_sha256": _sha("rawout-%d-%s" % (sequence, event_name)),
        "decision_verbatim": decision_verbatim,
        "decision_kind": decision_kind,
        "adapter_invoked": True,
        "adapter_exit_code": 0,
        "adapter_wall_ms": 12,
        "stderr_path": "/scratch/raw/%d.err" % sequence,
        "stderr_sha256": _sha("stderr-%d" % sequence),
        "hook_cwd_abs": "/scratch/plugin",
        "outcome": outcome,
    }


def fixture_log():
    """Five fired events on one pinned run, with the open limits recorded."""
    return [
        _entry(1, "PreToolUse", "run_command", '{"decision": "allow"}', "allow", "pass"),
        _entry(2, "PostToolUse", "no_data", "", "no_data", "fail"),
        _entry(3, "PreInvocation", "no_data", "", "no_data", "fail"),
        _entry(4, "PostInvocation", "no_data",
               '{"injectSteps": [], "terminationBehavior": ""}', "no_data", "pass"),
        _entry(5, "Stop", "no_data", '{"decision": "allow"}', "allow", "pass"),
    ]


class TestInstallDoc(unittest.TestCase):
    """Render, verify and stranger-run the Antigravity install doc."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.tmp = self._tmp.name
        self.log_path = self._write_log("log.json", fixture_log())
        self.out_path = os.path.join(self.tmp, "install-antigravity.md")

    def tearDown(self):
        self._tmp.cleanup()

    def _write_log(self, name, entries):
        path = os.path.join(self.tmp, name)
        with open(path, "w", encoding="utf-8") as handle:
            handle.write(json.dumps(entries))
        return path

    def _write(self, name, text):
        path = os.path.join(self.tmp, name)
        with open(path, "w", encoding="utf-8") as handle:
            handle.write(text)
        return path

    def _render(self):
        doc_lint.render_install_doc(self.log_path, self.out_path)
        with open(self.out_path, "r", encoding="utf-8") as handle:
            return handle.read()

    def test_render_install_doc_produces_traced_sections(self):
        text = self._render()
        positions = []
        for section in doc_lint.REQUIRED_SECTIONS:
            index = text.find(section)
            self.assertGreaterEqual(index, 0, "missing section %s" % section)
            positions.append(index)
        self.assertEqual(positions, sorted(positions), "sections are out of order")
        for statement in doc_lint.REQUIRED_LIMIT_STATEMENTS:
            self.assertIn(statement, text.lower(), statement)
        self.assertIn(doc_lint.MISSING_HOOK_LIMIT, text)
        self.assertIn("confirmed", text.lower(), "no confirmed sentence was rendered")
        self.assertGreaterEqual(text.count("```"), 2, "no fenced command block")
        blocks = doc_lint._fenced_blocks(text)
        self.assertTrue(blocks, "the rendered doc has no command blocks")
        for block in blocks:
            self.assertIsNotNone(block["anchor"], "a command block lost its anchor")
        result = doc_lint.lint_doc(self.out_path, self.log_path)
        self.assertTrue(result["ok"], result["reason"])
        self.assertEqual(result["untraced"], [])

    def test_verify_install_doc_ok_on_traced_doc(self):
        self._render()
        result = doc_lint.verify_install_doc(self.out_path, self.log_path)
        self.assertTrue(result["ok"], result["reason"])
        self.assertEqual(result["missing_limits"], [])
        self.assertTrue(result["section_order_ok"])

    def test_open_limits_required(self):
        text = self._render()
        mutated = self._write("stop-claim.md", text + "\nstop enforces completion\n")
        self.assertFalse(doc_lint.lint_doc(mutated, self.log_path)["ok"])
        with self.assertRaises(ValueError):
            doc_lint.verify_install_doc(mutated, self.log_path)

    def test_verify_refuses_missing_known_limit(self):
        text = self._render()
        mutated = self._write(
            "no-stop-limit.md",
            text.replace("stop allows unconditionally", "stop is fine"),
        )
        with self.assertRaises(ValueError) as caught:
            doc_lint.verify_install_doc(mutated, self.log_path)
        self.assertIn("stop allows unconditionally", str(caught.exception))

    def test_verify_refuses_corrupt_log_contract(self):
        self._render()
        cases = (
            (2, "raw_out_sha256", "not-a-hash"),
            (2, "raw_out_sha256", 12345),
            (0, "run_id", "short"),
            (0, "run_id", []),
            (1, "event_sequence", "3"),
            (1, "event_sequence", True),
            (1, "outcome", 7),
        )
        for index, (entry_index, field, value) in enumerate(cases):
            entries = fixture_log()
            entries[entry_index][field] = value
            path = self._write_log("corrupt-%d.json" % index, entries)
            with self.assertRaises(ValueError):
                doc_lint.verify_install_doc(self.out_path, path)
            with self.assertRaises(ValueError):
                doc_lint.render_install_doc(path, os.path.join(self.tmp, "out-%d.md" % index))

    def test_verify_install_doc_missing_log_refuses(self):
        self._render()
        missing = os.path.join(self.tmp, "absent-log.json")
        with self.assertRaises(ValueError):
            doc_lint.verify_install_doc(self.out_path, missing)

    def test_verify_install_doc_hostile_input_refused(self):
        self._render()
        not_utf8 = os.path.join(self.tmp, "not-utf8.bin")
        with open(not_utf8, "wb") as handle:
            handle.write(b"\xff\xfe\x00\x01")
        bad_paths = (None, [], b"\x00", "", float("nan"), self.tmp, not_utf8)
        for value in bad_paths:
            with self.assertRaises(ValueError):
                doc_lint.verify_install_doc(value, self.log_path)
            with self.assertRaises(ValueError):
                doc_lint.verify_install_doc(self.out_path, value)
            with self.assertRaises(ValueError):
                doc_lint.render_install_doc(value, self.out_path)
        for value in (None, [], b"\x00", "", float("nan"), self.tmp):
            with self.assertRaises(ValueError):
                doc_lint.render_install_doc(self.log_path, value)

    def test_stranger_harness_stops_at_first_failure(self):
        text = (
            "# stranger doc\n\n"
            "<!-- evidence: %s -->\n```bash\nfirst\n```\n"
            "<!-- evidence: %s -->\n```bash\nsecond\n```\n"
            "<!-- evidence: %s -->\n```bash\nthird\n```\n"
        ) % ("a" * 64, "b" * 64, "c" * 64)
        doc_path = self._write("stranger-fail.md", text)
        calls = []

        def command_runner(command, scratch_home):
            calls.append(command)
            return {"exit_code": 3 if command == "second" else 0,
                    "out_sha256": "c" * 64}

        result = doc_lint.run_stranger_harness(doc_path, self.tmp, runner=command_runner)
        self.assertFalse(result["reached_fired_hook"], result)
        self.assertTrue(result["first_attempt"], result)
        self.assertIn("block 2", result["failed_at"])
        self.assertEqual(calls, ["first", "second"])

    def test_stranger_harness_refuses_unanchored_block(self):
        text = (
            "# unanchored doc\n\n"
            "<!-- evidence: %s -->\n```bash\nfirst\n```\n"
            "```bash\nsecond\n```\n"
        ) % ("a" * 64,)
        doc_path = self._write("unanchored.md", text)
        calls = []

        def command_runner(command, scratch_home):
            calls.append(command)
            return {"exit_code": 0, "out_sha256": "a" * 64}

        result = doc_lint.run_stranger_harness(doc_path, self.tmp, runner=command_runner)
        self.assertFalse(result["reached_fired_hook"], result)
        self.assertIn("block 2", result["failed_at"])
        self.assertEqual(calls, ["first"])

    def test_stranger_harness_missing_doc_refuses(self):
        missing = os.path.join(self.tmp, "absent-doc.md")
        empty_doc = self._write("empty-doc.md", "# nothing\n")
        cases = (
            (missing, self.tmp),
            (None, self.tmp),
            ([], self.tmp),
            (float("nan"), self.tmp),
            (self.tmp, self.tmp),
            (empty_doc, self.tmp),
            (empty_doc, None),
            (empty_doc, ""),
            (empty_doc, []),
        )
        for doc_path, scratch_home in cases:
            result = doc_lint.run_stranger_harness(doc_path, scratch_home)
            self.assertIsInstance(result, dict)
            self.assertFalse(result["reached_fired_hook"], result)
            self.assertTrue(result["failed_at"], result)

    def test_stranger_first_attempt(self):
        self._render()
        with open(self.out_path, "r", encoding="utf-8") as handle:
            text = handle.read()
        blocks = doc_lint._fenced_blocks(text)
        self.assertTrue(blocks, "the rendered doc has no command blocks")
        last_anchor = blocks[-1]["anchor"]
        self.assertTrue(last_anchor, "the last command block has no evidence anchor")
        calls = []

        def command_runner(command, scratch_home):
            calls.append(command)
            is_last = len(calls) == len(blocks)
            return {"exit_code": 0,
                    "out_sha256": last_anchor if is_last else "0" * 64}

        result = doc_lint.run_stranger_harness(self.out_path, self.tmp, runner=command_runner)
        self.assertTrue(result["first_attempt"], result)
        self.assertTrue(result["reached_fired_hook"], result)
        self.assertEqual(len(calls), len(blocks))


if __name__ == "__main__":
    unittest.main()

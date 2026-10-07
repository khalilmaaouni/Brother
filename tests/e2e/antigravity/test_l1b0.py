#!/usr/bin/env python3
"""L1b.0 host contract capture tests.

The capture helpers live beside this test in l1b0_capture.py. Nothing here
re-declares the hook contract: every expectation is checked against the real
scripts/brother_antigravity_hook.py and against the captured schema section in
docs/architecture/ANTIGRAVITY-E2E-TEST-LOG.md.
"""

from __future__ import annotations

import importlib.util
import os
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.abspath(os.path.join(HERE, "..", "..", ".."))
CAPTURE_PATH = os.path.join(HERE, "l1b0_capture.py")
HOOK_PATH = os.path.join(ROOT, "scripts", "brother_antigravity_hook.py")
PLUGIN_DIR = os.path.join(ROOT, "bundle", ".antigravity-plugin")
DOC_PATH = os.path.join(ROOT, "docs", "architecture", "ANTIGRAVITY-E2E-TEST-LOG.md")

EXPECTED_TOOLS = (
    "grep_search",
    "list_dir",
    "multi_replace_file_content",
    "replace_file_content",
    "run_command",
    "view_file",
    "write_to_file",
)

EXPECTED_MODES = (
    "PreInvocation",
    "PreToolUse",
    "PostInvocation",
    "PostToolUse",
    "Stop",
    "post_invocation",
    "post_tool",
    "pre_invocation",
    "pre_tool",
    "stop",
)

EXPECTED_HANDLER_OUTPUTS = {
    "pre_tool": {"decision": "allow"},
    "post_tool": {},
    "pre_invocation": {"injectSteps": []},
    "post_invocation": {"injectSteps": [], "terminationBehavior": ""},
    "stop": {"decision": "allow"},
}


def _load_capture():
    if not os.path.isfile(CAPTURE_PATH):
        raise AssertionError("capture module is missing beside this test: %s" % CAPTURE_PATH)
    spec = importlib.util.spec_from_file_location("l1b0_capture_under_test", CAPTURE_PATH)
    if spec is None or spec.loader is None:
        raise AssertionError("cannot load capture module from %s" % CAPTURE_PATH)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class TestHostContractCapture(unittest.TestCase):
    def setUp(self):
        self.capture = _load_capture()

    def _write_facts(self, directory, facts, name="log.md"):
        log_path = os.path.join(directory, name)
        self.capture.capture_schema_section(log_path, facts)
        return log_path

    def test_shape_contract(self):
        module = self.capture.load_hook_module(HOOK_PATH)
        shape = module._EVENT_SHAPE
        self.assertIs(shape["pre_invocation"]["invocationNum"], int)
        self.assertIs(shape["post_invocation"]["invocationNum"], int)
        self.assertIs(shape["stop"]["terminationReason"], str)
        self.assertIs(shape["stop"]["fullyIdle"], bool)

    def test_real_code_facts_present(self):
        facts = self.capture.collect_real_code_facts(HOOK_PATH)
        for tool in EXPECTED_TOOLS:
            self.assertIn(tool, facts["tool_map_keys"])
        self.assertEqual(facts["known_tools"], sorted(EXPECTED_TOOLS))
        self.assertEqual(facts["max_stdin_chars"], 1000000)
        self.assertEqual(facts["event_shape"], {
            "pre_invocation": {"invocationNum": "int"},
            "post_invocation": {"invocationNum": "int"},
            "stop": {"terminationReason": "str", "fullyIdle": "bool"},
        })
        self.assertEqual(facts["handler_outputs"], EXPECTED_HANDLER_OUTPUTS)
        self.assertEqual(facts["main_modes"], sorted(EXPECTED_MODES))

    def test_main_modes(self):
        with open(HOOK_PATH, "rb") as handle:
            source = handle.read().decode("utf-8")
        modes = self.capture.extract_main_modes(source)
        self.assertEqual(set(modes), set(EXPECTED_MODES))
        self.assertEqual(len(modes), len(EXPECTED_MODES))
        self.assertIn("unknown hook mode", source)

    def test_capture_schema_section_roundtrip(self):
        facts = self.capture.build_facts(HOOK_PATH, PLUGIN_DIR)
        with tempfile.TemporaryDirectory() as tmp:
            log_path = self._write_facts(tmp, facts)
            self.assertTrue(os.path.isfile(log_path))
            self.assertEqual(self.capture.check_unknowns(log_path), [])
            read_back = self.capture.read_schema_section(log_path)
            self.assertEqual(read_back["tool_map_keys"], facts["tool_map_keys"])
            self.assertEqual(read_back["max_stdin_chars"], 1000000)
            for key in self.capture.HOST_FACT_KEYS:
                self.assertIn(key, read_back)

    def test_check_unknowns_flags_missing_and_wrong_types(self):
        facts = self.capture.build_facts(HOOK_PATH, PLUGIN_DIR)
        missing = dict(facts)
        missing.pop("max_stdin_chars")
        wrong_type = dict(facts)
        wrong_type["max_stdin_chars"] = "1000000"
        wrong_bool = dict(facts)
        wrong_bool["max_stdin_chars"] = True
        bad_shape = dict(facts)
        bad_shape["event_shape"] = {"pre_invocation": {"invocationNum": "str"}}
        not_a_list = dict(facts)
        not_a_list["main_modes"] = "pre_tool"
        nan_cap = dict(facts)
        nan_cap["max_stdin_chars"] = float("nan")
        with tempfile.TemporaryDirectory() as tmp:
            self.assertIn("max_stdin_chars",
                          self.capture.check_unknowns(self._write_facts(tmp, missing, "missing.md")))
            self.assertIn("max_stdin_chars",
                          self.capture.check_unknowns(self._write_facts(tmp, wrong_type, "wrong_type.md")))
            self.assertIn("max_stdin_chars",
                          self.capture.check_unknowns(self._write_facts(tmp, wrong_bool, "wrong_bool.md")))
            self.assertIn("event_shape",
                          self.capture.check_unknowns(self._write_facts(tmp, bad_shape, "bad_shape.md")))
            self.assertIn("main_modes",
                          self.capture.check_unknowns(self._write_facts(tmp, not_a_list, "not_a_list.md")))
            with self.assertRaises(ValueError):
                self.capture.capture_schema_section(os.path.join(tmp, "nan.md"), nan_cap)

    def test_hostile_arguments_are_refused(self):
        capture = self.capture
        with tempfile.TemporaryDirectory() as tmp:
            good = os.path.join(tmp, "good.md")
            for bad_path in (None, 123, b"bytes", {"a": 1}, ["x"], True, ""):
                with self.assertRaises(ValueError):
                    capture.capture_schema_section(bad_path, {"a": 1})
                self.assertEqual(capture.check_unknowns(bad_path), ["log_path"])
            for bad_facts in (None, [], "facts", 7, True, {}, set()):
                with self.assertRaises(ValueError):
                    capture.capture_schema_section(good, bad_facts)
            for bad_facts in ({"a": float("nan")}, {"a": float("inf")}, {"a": object()},
                              {"a": {"b"}}, {1: "x"}, {"a": b"bytes"}):
                with self.assertRaises(ValueError):
                    capture.capture_schema_section(good, bad_facts)
            with self.assertRaises(ValueError):
                capture.capture_schema_section(tmp, {"a": 1})
            self.assertEqual(capture.check_unknowns(tmp), ["log_path"])
            self.assertEqual(capture.check_unknowns(os.path.join(tmp, "missing.md")), ["log_path"])
            with self.assertRaises(ValueError):
                capture.collect_real_code_facts(None)
            with self.assertRaises(ValueError):
                capture.collect_real_code_facts(tmp)
            with self.assertRaises(ValueError):
                capture.collect_real_code_facts(os.path.join(tmp, "missing_hook.py"))
            with self.assertRaises(ValueError):
                capture.extract_main_modes(None)
            with self.assertRaises(ValueError):
                capture.extract_main_modes("no mode list in here")
            with self.assertRaises(ValueError):
                capture.host_facts(None)
            self.assertEqual(capture.check_host_facts(None), list(capture.HOST_FACT_KEYS))

    def test_unhashable_fact_key_is_refused(self):
        class EvilDict(dict):
            def items(self):
                return [([1, 2], "value")]
        evil = EvilDict()
        with tempfile.TemporaryDirectory() as tmp:
            with self.assertRaises(ValueError):
                self.capture.capture_schema_section(os.path.join(tmp, "evil.md"), evil)

    def test_deep_unhashable_fact_key_is_refused(self):
        class EvilDict(dict):
            def items(self):
                return [([1, 2], "value")]
        evil = EvilDict()
        facts = {"nested": evil}
        with tempfile.TemporaryDirectory() as tmp:
            with self.assertRaises(ValueError):
                self.capture.capture_schema_section(os.path.join(tmp, "evil2.md"), facts)

    def test_corrupt_or_missing_log_is_refused(self):
        capture = self.capture
        with tempfile.TemporaryDirectory() as tmp:
            no_block = os.path.join(tmp, "no-block.md")
            with open(no_block, "w", encoding="utf-8") as handle:
                handle.write("no schema block here\n")
            self.assertEqual(capture.check_unknowns(no_block), ["schema"])
            with self.assertRaises(ValueError):
                capture.read_schema_section(no_block)
            broken = os.path.join(tmp, "broken.md")
            with open(broken, "w", encoding="utf-8") as handle:
                handle.write('```json\n{"tool_map_keys": }\n```\n')
            self.assertEqual(capture.check_unknowns(broken), ["schema"])
            unclosed = os.path.join(tmp, "unclosed.md")
            with open(unclosed, "w", encoding="utf-8") as handle:
                handle.write('```json\n{}\n')
            self.assertEqual(capture.check_unknowns(unclosed), ["schema"])
            bad_bytes = os.path.join(tmp, "bad-bytes.md")
            with open(bad_bytes, "wb") as handle:
                handle.write(b"\xff\xfe\x00not utf-8 text")
            self.assertEqual(capture.check_unknowns(bad_bytes), ["log_path"])
            with self.assertRaises(ValueError):
                capture.read_schema_section(bad_bytes)
            self.assertEqual(capture.check_unknowns(None), ["log_path"])
            self.assertEqual(capture.check_unknowns(9), ["log_path"])
            self.assertEqual(capture.read_schema_section(DOC_PATH)["max_stdin_chars"], 1000000)

    def test_altered_parsed_record_is_revalidated(self):
        facts = self.capture.build_facts(HOOK_PATH, PLUGIN_DIR)
        with tempfile.TemporaryDirectory() as tmp:
            log_path = self._write_facts(tmp, facts)
            parsed = self.capture.read_schema_section(log_path)
            parsed["max_stdin_chars"] = "altered after parsing"
            parsed["event_shape"] = "altered after parsing"
            self.assertEqual(self.capture.check_unknowns(log_path), [])
            again = self.capture.read_schema_section(log_path)
            self.assertEqual(again["max_stdin_chars"], 1000000)
            self.assertEqual(again["event_shape"]["pre_invocation"]["invocationNum"], "int")

    def test_missing_host_facts_are_no_data_and_reported(self):
        facts = self.capture.build_facts(HOOK_PATH, PLUGIN_DIR)
        for key in self.capture.HOST_FACT_KEYS:
            self.assertIn(key, facts)
        manifest = os.path.join(PLUGIN_DIR, "plugin.json")
        mcp = os.path.join(PLUGIN_DIR, "mcp_config.json")
        hooks = os.path.join(PLUGIN_DIR, "hooks.json")
        expected = []
        if not os.path.isdir(PLUGIN_DIR):
            expected.extend(["plugin_path_abs", "manifest_sha256", "mcp_config_sha256",
                             "hooks_sha256", "hooks_byte_length", "skills_count", "rules_count"])
        else:
            if not os.path.isfile(manifest):
                expected.append("manifest_sha256")
            if not os.path.isfile(mcp):
                expected.append("mcp_config_sha256")
            if not os.path.isfile(hooks):
                expected.extend(["hooks_sha256", "hooks_byte_length"])
            if not os.path.isdir(os.path.join(PLUGIN_DIR, "skills")):
                expected.append("skills_count")
            if not os.path.isdir(os.path.join(PLUGIN_DIR, "rules")):
                expected.append("rules_count")
        expected.extend(["host_name", "host_version", "load_argv", "resolved_cwd",
                         "matcher_proof", "benign_pre_tool_stdin", "benign_pre_tool_stdout"])
        self.assertEqual(sorted(self.capture.check_host_facts(facts)), sorted(set(expected)))
        if not os.path.isfile(manifest):
            self.assertEqual(facts["manifest_sha256"], "no_data")
            self.assertIn("manifest_sha256", self.capture.check_host_facts(facts))

    def test_shipped_schema_section_matches_real_code(self):
        self.assertTrue(os.path.isfile(DOC_PATH), "shipped schema section missing: %s" % DOC_PATH)
        facts = self.capture.read_schema_section(DOC_PATH)
        for key in self.capture.REQUIRED_REAL_CODE_FACTS:
            self.assertIn(key, facts)
        for key in self.capture.HOST_FACT_KEYS:
            self.assertIn(key, facts)
        live = self.capture.collect_real_code_facts(HOOK_PATH)
        self.assertEqual(sorted(facts["tool_map_keys"]), sorted(live["tool_map_keys"]))
        self.assertEqual(sorted(facts["known_tools"]), sorted(live["known_tools"]))
        self.assertEqual(facts["max_stdin_chars"], live["max_stdin_chars"])
        self.assertEqual(facts["event_shape"], live["event_shape"])
        self.assertEqual(facts["handler_outputs"], live["handler_outputs"])
        self.assertEqual(sorted(facts["main_modes"]), sorted(live["main_modes"]))
        self.assertEqual(self.capture.check_unknowns(DOC_PATH), [])


if __name__ == "__main__":
    unittest.main()

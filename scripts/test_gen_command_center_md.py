#!/usr/bin/env python3
"""Unit tests for the --md export mode of scripts/gen_command_center.py.

Proves the export both works and fails correctly: the markdown is written
and non-empty, its advancement row count matches the source JSON, a `link`
field renders when present and is absent-safe when it is not, and a
malformed or missing LIVE-STATE.json produces a clear error rather than a
silent empty file.

Fixtures are built inline with tempfile, no external fixture directory.
Runs on /usr/bin/python3 (3.9 floor): no match statements.
"""
import contextlib
import importlib.util
import io
import json
import sys
import tempfile
import unittest
from pathlib import Path

SCRIPT_PATH = Path(__file__).resolve().parent / "gen_command_center.py"
spec = importlib.util.spec_from_file_location("gen_command_center", SCRIPT_PATH)
gcc = importlib.util.module_from_spec(spec)
spec.loader.exec_module(gcc)


def sample_live_state(with_link=True):
    """A small but structurally complete LIVE-STATE.json fixture covering
    every section the --md mode renders."""
    risk = {
        "level": "hot",
        "title": "A sample risk",
        "what": "Something is at risk.",
        "why": "It matters because tests fail.",
        "action": "Fix it.",
        "when": "Now.",
    }
    advancement = {
        "what": "A sample advancement item",
        "state": "PROVEN",
        "evidence": "The done check ran and printed OK.",
    }
    if with_link:
        risk["link"] = "https://example.com/JIRA-1"
        advancement["link"] = "https://example.com/JIRA-2"
    return {
        "measured_at": "2026-08-27T00:00:00Z",
        "session": "test-session",
        "north_star": "Ship the thing.",
        "repos": [
            {"name": "Sample repo", "head": "abc123", "origin_main": "abc123",
             "in_sync": True, "public": True, "note": "clean"},
        ],
        "pull_requests": [
            {"repo": "Sample repo", "number": 1, "state": "MERGED", "note": "landed"},
        ],
        "risks": [risk],
        "advancement_today": [advancement, dict(advancement, what="A second item")],
    }


class TestRenderMarkdownPure(unittest.TestCase):
    """render_markdown(live_state) is a pure function: test it directly
    against inline fixtures, no file I/O required for these cases."""

    def test_output_is_non_empty(self):
        text = gcc.render_markdown(sample_live_state())
        self.assertTrue(text.strip())

    def test_advancement_row_count_matches_source(self):
        live_state = sample_live_state()
        text = gcc.render_markdown(live_state)
        expected = len(live_state["advancement_today"])
        # Each advancement item renders exactly one "State: " line.
        self.assertEqual(text.count("\nState: "), expected)

    def test_link_field_renders_when_present(self):
        text = gcc.render_markdown(sample_live_state(with_link=True))
        self.assertIn("https://example.com/JIRA-1", text)
        self.assertIn("https://example.com/JIRA-2", text)

    def test_absent_link_breaks_nothing(self):
        live_state = sample_live_state(with_link=False)
        text = gcc.render_markdown(live_state)
        self.assertTrue(text.strip())
        self.assertNotIn("Link: NO-DATA", text)
        self.assertNotIn("example.com", text)

    def test_repos_and_pull_requests_appear(self):
        text = gcc.render_markdown(sample_live_state())
        self.assertIn("Sample repo", text)
        self.assertIn("MERGED", text)

    def test_no_dash_characters_in_output(self):
        text = gcc.render_markdown(sample_live_state())
        self.assertNotIn(chr(0x2014), text)
        self.assertNotIn(chr(0x2013), text)


class TestRunMdModeFileWrite(unittest.TestCase):
    """Exercises the real file-writing path (run_md_mode), redirected to a
    temp directory by monkeypatching the module's path constants, so the
    real repo's LIVE-STATE.json and COMMAND-CENTER.md are never touched."""

    def setUp(self):
        self.tmpdir = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmpdir.cleanup)
        tmp_path = Path(self.tmpdir.name)
        self.live_state_path = tmp_path / "LIVE-STATE.json"
        self.md_output_path = tmp_path / "COMMAND-CENTER.md"
        self._orig_live_state_path = gcc.LIVE_STATE_PATH
        self._orig_md_output_path = gcc.MD_OUTPUT_PATH
        gcc.LIVE_STATE_PATH = self.live_state_path
        gcc.MD_OUTPUT_PATH = self.md_output_path
        self.addCleanup(self._restore_paths)

    def _restore_paths(self):
        gcc.LIVE_STATE_PATH = self._orig_live_state_path
        gcc.MD_OUTPUT_PATH = self._orig_md_output_path

    def test_writes_non_empty_md_file(self):
        self.live_state_path.write_text(json.dumps(sample_live_state()), encoding="utf-8")
        gcc.run_md_mode()
        self.assertTrue(self.md_output_path.is_file())
        self.assertGreater(self.md_output_path.stat().st_size, 0)

    def test_missing_live_state_json_fails_clearly(self):
        # live_state_path is never written: the file does not exist.
        stderr = io.StringIO()
        with self.assertRaises(SystemExit) as ctx:
            with contextlib.redirect_stderr(stderr):
                gcc.run_md_mode()
        self.assertEqual(ctx.exception.code, 1)
        message = stderr.getvalue()
        self.assertIn("ERROR", message)
        self.assertIn(str(self.live_state_path), message)
        self.assertFalse(self.md_output_path.exists())

    def test_malformed_live_state_json_fails_clearly(self):
        self.live_state_path.write_text("{not valid json", encoding="utf-8")
        stderr = io.StringIO()
        with self.assertRaises(SystemExit) as ctx:
            with contextlib.redirect_stderr(stderr):
                gcc.run_md_mode()
        self.assertEqual(ctx.exception.code, 1)
        message = stderr.getvalue()
        self.assertIn("ERROR", message)
        self.assertIn(str(self.live_state_path), message)
        self.assertFalse(self.md_output_path.exists())


class TestMainArgumentHandling(unittest.TestCase):
    """main()'s argument boundary: an unrecognised flag fails clearly
    instead of being silently ignored."""

    def test_unknown_flag_fails_clearly(self):
        orig_argv = sys.argv
        sys.argv = ["gen_command_center.py", "--bogus"]
        stderr = io.StringIO()
        try:
            with self.assertRaises(SystemExit) as ctx:
                with contextlib.redirect_stderr(stderr):
                    gcc.main()
        finally:
            sys.argv = orig_argv
        self.assertEqual(ctx.exception.code, 1)
        self.assertIn("ERROR", stderr.getvalue())
        self.assertIn("--bogus", stderr.getvalue())


if __name__ == "__main__":
    unittest.main()

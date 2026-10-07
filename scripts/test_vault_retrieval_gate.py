#!/usr/bin/env python3
"""Regression test for scripts/vault_retrieval_gate.py. Python 3.9 floor, stdlib only.

Every test injects a fake query_runner so the suite never touches the live
vault or shells out to bm_vault.py."""

import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from vault_retrieval_gate import check_retrieval, main  # noqa: E402

GATE = str(Path(__file__).resolve().parent / "vault_retrieval_gate.py")


def _write_fixture(queries):
    fh = tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False, encoding="utf-8")
    json.dump({"queries": queries}, fh)
    fh.close()
    return fh.name


class TestCheckRetrieval(unittest.TestCase):
    def test_no_regressions_when_all_baselines_still_hold(self):
        path = _write_fixture([
            {"id": "Q1", "query": "q one", "expected_note_id_or_title_substring": "1: a.md",
             "baseline_in_top5": True},
            {"id": "Q2", "query": "q two", "expected_note_id_or_title_substring": "2: b.md",
             "baseline_in_top5": False},
        ])
        try:
            runner = lambda q: "hit path .../a.md"  # noqa: E731 -- Q1's expected substring present
            ok, regressions = check_retrieval(path, "unused-tool-path", query_runner=runner)
            self.assertTrue(ok)
            self.assertEqual(regressions, [])
        finally:
            os.unlink(path)

    def test_one_regression_is_named(self):
        path = _write_fixture([
            {"id": "Q1", "query": "q one", "expected_note_id_or_title_substring": "1: a.md",
             "baseline_in_top5": True},
            {"id": "Q2", "query": "q two", "expected_note_id_or_title_substring": "2: b.md",
             "baseline_in_top5": True},
        ])
        try:
            def runner(q):
                # Q1 still finds its note; Q2's note has vanished from the top 5.
                return "hit path .../a.md" if q == "q one" else "no relevant hits at all"
            ok, regressions = check_retrieval(path, "unused-tool-path", query_runner=runner)
            self.assertFalse(ok)
            self.assertEqual(len(regressions), 1)
            self.assertEqual(regressions[0]["id"], "Q2")
        finally:
            os.unlink(path)

    def test_already_failing_baseline_is_not_counted_as_new_regression(self):
        path = _write_fixture([
            {"id": "Q1", "query": "q one", "expected_note_id_or_title_substring": "1: a.md",
             "baseline_in_top5": False},
        ])
        try:
            runner = lambda q: "still nothing relevant"  # noqa: E731
            ok, regressions = check_retrieval(path, "unused-tool-path", query_runner=runner)
            self.assertTrue(ok, "a query that was already failing must not become a regression")
            self.assertEqual(regressions, [])
        finally:
            os.unlink(path)

    def test_id_prefix_is_stripped_from_expected_substring(self):
        path = _write_fixture([
            {"id": "Q1", "query": "q one", "expected_note_id_or_title_substring": "10945: concurrent-session-is-a-hazard.md",
             "baseline_in_top5": True},
        ])
        try:
            seen = {}
            def runner(q):
                return "/vault/40-Failures/concurrent-session-is-a-hazard.md"
            ok, regressions = check_retrieval(path, "unused-tool-path", query_runner=runner)
            self.assertTrue(ok)
        finally:
            os.unlink(path)

    def test_missing_fixture_raises_value_error(self):
        with self.assertRaises(ValueError):
            check_retrieval("/no/such/file.json", "unused-tool-path", query_runner=lambda q: "")

    def test_tool_runner_error_counts_as_regression_when_baseline_was_true(self):
        path = _write_fixture([
            {"id": "Q1", "query": "q one", "expected_note_id_or_title_substring": "1: a.md",
             "baseline_in_top5": True},
        ])
        try:
            def runner(q):
                raise OSError("boom")
            ok, regressions = check_retrieval(path, "unused-tool-path", query_runner=runner)
            self.assertFalse(ok)
            self.assertEqual(regressions[0]["id"], "Q1")
        finally:
            os.unlink(path)


class TestCLI(unittest.TestCase):
    def test_cli_exits_2_on_missing_tool(self):
        result = subprocess.run(
            [sys.executable, GATE, "--tool", "/no/such/bm_vault.py"],
            capture_output=True, text=True,
        )
        self.assertEqual(result.returncode, 2)
        self.assertIn("NO-DATA", result.stdout)

    def test_main_function_directly_reports_no_data_on_missing_fixture(self):
        code = main(["--golden-queries", "/no/such/fixture.json", "--tool", sys.executable])
        self.assertEqual(code, 2)


if __name__ == "__main__":
    unittest.main()

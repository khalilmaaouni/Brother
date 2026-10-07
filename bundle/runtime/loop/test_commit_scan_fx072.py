#!/usr/bin/env python3
"""Tests for scripts/loop/commit_scan.py after FX-07.2.

FX-07.2 makes the commit gate read the ONE shared secret table in scripts/loop/secret_scan.py
instead of a table of its own, print the family names it caught BEFORE the counts line, keep
the counts line last, and report NO-DATA (exit 2) when that module cannot be loaded. Every
secret shaped fixture is assembled at run time from parts and no fixture value is printed.
"""
import contextlib
import io
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import commit_scan
import secret_scan as S


def _fence_free_header():
    """The value row 23 turned on: the private key header WITHOUT its five dash fences. The
    push table matched it and this gate required the fences, so one build committed clean and
    the push refused it. Assembled from parts so this file never carries the shape whole."""
    return "".join(["BEGIN", " RSA ", "PRIVATE", " KEY"])


def _password_assignment():
    return "".join(["pass", "word=", "hunter2"])


def _api_key_assignment():
    return "".join(["api", "_key=", "abcdefgh12345678"])


def _staged_style_diff(payload):
    return ("diff --git a/x.py b/x.py\n--- a/x.py\n+++ b/x.py\n@@ -1 +1 @@\n+" + payload + "\n")


def _log_style_diff(payload):
    """The shape git log -p prints: a hunk, then a commit header and its message. Those lines
    do not start with a space, a plus, a minus or a backslash, so they end the hunk and are
    scanned whole."""
    return ("diff --git a/x.py b/x.py\n--- a/x.py\n+++ b/x.py\n@@ -1 +1 @@\n+ok\n"
            "a commit message line " + payload + "\n")


class CommitGateReadsTheOneTable(unittest.TestCase):
    def test_scan_counts_through_the_shared_module(self):
        for text in (_fence_free_header(), _api_key_assignment(), _password_assignment()):
            self.assertGreaterEqual(S.count(text), 1)
            self.assertEqual(commit_scan.scan(text)["secrets"], S.count(text))
        # The row 23 value is caught: the fence free private key header.
        self.assertGreaterEqual(commit_scan.scan(_fence_free_header())["secrets"], 1)

    def test_added_text_is_the_shared_extraction(self):
        self.assertIs(commit_scan.added_text, S.added_text)
        self.assertIs(commit_scan.NAMELESS, S.NAMELESS)
        self.assertIs(commit_scan.NAMED, S.NAMED)

    def test_log_message_after_a_hunk_is_scanned(self):
        payload = _fence_free_header()
        diff = _log_style_diff(payload)
        self.assertIn(payload, commit_scan.added_text(diff))

    def test_non_string_input_is_refused(self):
        for bad in (None, True, 7, 3.5, float("nan"), [], {}, b"bytes"):
            with self.assertRaises(TypeError):
                commit_scan.scan(bad)


class MainOutputAndNoData(unittest.TestCase):
    def _run_main(self, diff):
        saved = commit_scan.staged_diff
        commit_scan.staged_diff = lambda: (diff, None)
        try:
            out = io.StringIO()
            with contextlib.redirect_stdout(out):
                rc = commit_scan.main()
        finally:
            commit_scan.staged_diff = saved
        return rc, out.getvalue()

    def test_counts_line_stays_last_and_names_precede_it(self):
        payload = _password_assignment()
        diff = _staged_style_diff(payload)
        rc, out = self._run_main(diff)
        lines = out.splitlines()
        self.assertEqual(rc, 1)
        self.assertTrue(lines[-1].startswith("secrets="))
        self.assertIn("private terms=", lines[-1])
        shapes = [i for i, l in enumerate(lines) if l.startswith("secret shapes:")]
        self.assertEqual(len(shapes), 1)
        self.assertLess(shapes[0], len(lines) - 1)
        expected = "secret shapes: " + ", ".join(S.families(commit_scan.added_text(diff)))
        self.assertEqual(lines[shapes[0]], expected)
        self.assertNotIn(payload, out)

    def test_missing_module_commit_is_no_data(self):
        saved_S = commit_scan.S
        saved_diff = commit_scan.staged_diff
        commit_scan.S = None
        commit_scan.staged_diff = lambda: (_staged_style_diff("ordinary text"), None)
        try:
            out = io.StringIO()
            with contextlib.redirect_stdout(out):
                rc = commit_scan.main()
        finally:
            commit_scan.S = saved_S
            commit_scan.staged_diff = saved_diff
        self.assertEqual(rc, 2)
        self.assertIn("NO-DATA", out.getvalue())
        self.assertIn("secret_scan could not be loaded", out.getvalue())

    def test_selftest_reports_failed_when_the_module_is_missing(self):
        saved_S = commit_scan.S
        commit_scan.S = None
        try:
            out = io.StringIO()
            with contextlib.redirect_stdout(out):
                rc = commit_scan.selftest()
        finally:
            commit_scan.S = saved_S
        self.assertEqual(rc, 1)
        self.assertIn("FAILED", out.getvalue())
        self.assertIn("secret_scan could not be loaded", out.getvalue())


if __name__ == "__main__":
    unittest.main()

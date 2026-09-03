"""test_laws_audit.py: proves scripts/laws_audit.py actually catches a law
that claims ENFORCED with no file behind it, rather than reporting a green
list nobody checked against the filesystem.

Two backwards drives, per R28.1.2 (docs/plan/READINESS-ROADMAP-2026-08-29.json):

  (a) a fixture law book with an ENFORCED law naming a file that does not
      exist MUST FAIL BY NAME: the fixture's own law text names the missing
      file, and the audit's FAIL line must name it back.

  (b) a fixture law book with an UNENFORCED law MUST be reported as a
      finding, never a failure: the run over an UNENFORCED-only book still
      exits 0, and the law's section and candidate text appear in the
      output.

A third case proves the auditor is not simply lenient by construction: an
ENFORCED law naming a file that DOES exist (this test file itself) passes.

Every fixture is written to a tempfile, never to a real law book, so this
suite touches no product file and no path outside a temp directory.
"""
import os
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import laws_audit as la  # noqa: E402


class TestParseLawBook(unittest.TestCase):
    def test_enforced_line_extracts_status_and_backtick_path(self):
        text = (
            "## A Fixture Section\n"
            "- ENFORCEMENT: ENFORCED by `scripts/does_not_exist_xyz.py`. "
            "Check: `python3 scripts/does_not_exist_xyz.py` prints PASS.\n"
        )
        laws = la.parse_law_book(text, "fixture")
        self.assertEqual(len(laws), 1)
        law = laws[0]
        self.assertEqual(law["status"], "ENFORCED")
        self.assertEqual(law["section"], "A Fixture Section")
        self.assertIn("scripts/does_not_exist_xyz.py", law["enforcer_files"])
        self.assertTrue(any("python3" in c for c in law["proving_commands"]))

    def test_unenforced_line_extracts_candidate(self):
        text = (
            "## Another Fixture\n"
            "- ENFORCEMENT: UNENFORCED by hook, stated discipline. "
            "Candidate control, not built: a hook that refuses the bad case.\n"
        )
        laws = la.parse_law_book(text, "fixture")
        self.assertEqual(laws[0]["status"], "UNENFORCED")
        self.assertIn("a hook that refuses the bad case", laws[0]["candidate"])

    def test_unenforced_substring_never_reads_as_enforced(self):
        # The word boundary trap this parser exists to avoid: ENFORCED is a
        # substring of UNENFORCED, and a naive search would misread this.
        text = "## X\n- ENFORCEMENT: UNENFORCED today.\n"
        laws = la.parse_law_book(text, "fixture")
        self.assertEqual(laws[0]["status"], "UNENFORCED")

    def test_line_with_neither_word_is_unknown_not_dropped(self):
        text = "## X\n- ENFORCEMENT: each plugin's SessionStart hook.\n"
        laws = la.parse_law_book(text, "fixture")
        self.assertEqual(len(laws), 1)
        self.assertEqual(laws[0]["status"], "UNKNOWN")

    def test_second_law_under_same_heading_is_numbered(self):
        text = (
            "## Shared Section\n"
            "- ENFORCEMENT: UNENFORCED, first.\n"
            "- ENFORCEMENT: UNENFORCED, second.\n"
        )
        laws = la.parse_law_book(text, "fixture")
        self.assertEqual(laws[0]["section"], "Shared Section")
        self.assertEqual(laws[1]["section"], "Shared Section #2")

    def test_glob_backtick_is_never_a_path_candidate(self):
        text = (
            "## Glob Fixture\n"
            "- ENFORCEMENT: ENFORCED by `scripts/real_enforcer.py`, scoped to "
            "`.github/workflows/*.yml`.\n"
        )
        laws = la.parse_law_book(text, "fixture")
        self.assertIn("scripts/real_enforcer.py", laws[0]["enforcer_files"])
        self.assertNotIn(".github/workflows/*.yml", laws[0]["enforcer_files"])

    def test_path_embedded_in_a_command_backtick_is_recovered(self):
        text = (
            "## Command Fixture\n"
            "- ENFORCEMENT: ENFORCED: proving command is "
            "`python3 ~/Brother/scripts/close_ceremony_check.py` prints PASS.\n"
        )
        laws = la.parse_law_book(text, "fixture")
        self.assertIn("~/Brother/scripts/close_ceremony_check.py",
                      laws[0]["enforcer_files"])


class TestResolvePath(unittest.TestCase):
    def test_brother_prefix_resolves_to_this_repo_root_not_the_home_checkout(self):
        # The single-writer fence lesson: an agent runs from a worktree, and
        # ~/Brother on disk can be checked out to any branch at the moment
        # this runs. A `~/Brother/...` law path must resolve against THIS
        # run's own repo root, never the literal home path.
        resolved = la.resolve_path("~/Brother/scripts/laws_audit.py")
        self.assertEqual(resolved, os.path.join(la.ROOT, "scripts", "laws_audit.py"))
        self.assertTrue(os.path.exists(resolved))

    def test_other_home_path_still_expands_to_home(self):
        resolved = la.resolve_path("~/.claude/hooks/repeat_guard.py")
        self.assertEqual(resolved, os.path.expanduser("~/.claude/hooks/repeat_guard.py"))


class TestVerifyDrivenBackwards(unittest.TestCase):
    """The R28.1.2 backwards drive, run end to end through main() against a
    real temp file, exactly as scripts/check_all.sh will invoke it."""

    def _run_with_book(self, text):
        with tempfile.NamedTemporaryFile(
                "w", suffix=".md", delete=False, encoding="utf-8") as fh:
            fh.write(text)
            path = fh.name
        try:
            import io
            import contextlib
            buf = io.StringIO()
            with contextlib.redirect_stdout(buf):
                rc = la.main(["--book", path])
            return rc, buf.getvalue()
        finally:
            os.unlink(path)

    def test_a_enforced_law_naming_a_missing_file_fails_by_name(self):
        text = (
            "## The Fixture That Must Fail\n"
            "- ENFORCEMENT: ENFORCED by `scripts/totally_does_not_exist_r28.py`. "
            "Check: `python3 scripts/totally_does_not_exist_r28.py`.\n"
        )
        rc, out = self._run_with_book(text)
        self.assertEqual(rc, 1)
        self.assertIn("FAIL", out)
        self.assertIn("totally_does_not_exist_r28.py", out)
        self.assertIn("The Fixture That Must Fail", out)

    def test_b_unenforced_law_is_a_finding_never_a_failure(self):
        text = (
            "## The Fixture That Must Not Fail\n"
            "- ENFORCEMENT: UNENFORCED by hook, stated discipline. "
            "Candidate control, not built: a hook nobody wrote yet.\n"
        )
        rc, out = self._run_with_book(text)
        self.assertEqual(rc, 0)
        self.assertIn("UNENFORCED", out)
        self.assertIn("The Fixture That Must Not Fail", out)

    def test_enforced_law_naming_a_real_file_passes(self):
        text = (
            "## The Fixture That Must Pass\n"
            "- ENFORCEMENT: ENFORCED by `scripts/test_laws_audit.py`. "
            "Check: `python3 scripts/test_laws_audit.py`.\n"
        )
        rc, out = self._run_with_book(text)
        self.assertEqual(rc, 0)
        self.assertIn("PASS", out)

    def test_missing_book_is_named_not_a_crash(self):
        rc, out = None, None
        import io
        import contextlib
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            rc = la.main(["--book", "/no/such/law/book/xyz.md"])
        out = buf.getvalue()
        self.assertEqual(rc, 2)
        self.assertIn(la.NODATA, out)
        self.assertIn("/no/such/law/book/xyz.md", out)

    def test_list_mode_never_verifies_and_always_exits_0_on_parsed_laws(self):
        text = (
            "## List Mode Fixture\n"
            "- ENFORCEMENT: ENFORCED by `scripts/totally_missing_list_mode.py`.\n"
        )
        with tempfile.NamedTemporaryFile(
                "w", suffix=".md", delete=False, encoding="utf-8") as fh:
            fh.write(text)
            path = fh.name
        try:
            import io
            import contextlib
            buf = io.StringIO()
            with contextlib.redirect_stdout(buf):
                rc = la.main(["--list", "--book", path])
            self.assertEqual(rc, 0)
            self.assertIn("List Mode Fixture", buf.getvalue())
        finally:
            os.unlink(path)

    def test_real_repo_law_book_scan_exits_0_1_or_2_never_crashes(self):
        # Live smoke check against the real ~/.claude/CLAUDE.md: whatever the
        # verdict, it must be one of the three documented exit codes.
        import io
        import contextlib
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            rc = la.main([])
        self.assertIn(rc, (0, 1, 2))


if __name__ == "__main__":
    unittest.main()

#!/usr/bin/env python3
"""Regression tests for tools/bm_progress_check.py, the mechanical check for
the founder's progress-page rule ("every project shows a Gantt-style page,
and a session never has to be asked for it").

WHAT THIS SUITE IS ACTUALLY DEFENDING
  The rule that a project must SHOW its progress page, not just build it, is
  written down in prose in more than one place already, and prose has never
  once stopped a session from skipping the step. This tool is the mechanical
  half: a plan exists, a page exists, the page is newer than the plan. This
  suite proves each of the four real verdicts (NO-PLAN, OWED-MISSING,
  OWED-STALE, CURRENT) plus the NO-DATA failure mode, proves every glob
  pattern the contract names is actually recognised, and above all proves
  the tool is READ-ONLY: it runs at every session start, so a crash or a
  stray write here would break every session on the machine, not just this
  one check.

Every fixture is a tempfile.TemporaryDirectory(), never the real repository.
mtimes are set explicitly with os.utime so ordering is deterministic and
does not depend on how fast the machine runs the test.

Standard library only. Run: python3 tools/test_bm_progress_check.py
"""
import hashlib
import io
import os
import subprocess
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
TOOL_PATH = os.path.join(HERE, "bm_progress_check.py")

#: Two fixed, ordered instants for mtimes. EARLIER predates LATER by a
#: comfortable margin; no test ever derives a gap from wall-clock timing.
EARLIER = 1700000000
LATER = 1700010000


def run_cli(*args):
    """Invoke the CLI as a real subprocess, with BROTHERMODE_ROOT scrubbed
    from the environment so a variable set on the developer's own machine
    can never leak into a test that expects an explicit --root to decide
    the outcome."""
    env = dict(os.environ)
    env.pop("BROTHERMODE_ROOT", None)
    return subprocess.run(
        [sys.executable, TOOL_PATH] + list(args),
        stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        universal_newlines=True, cwd=ROOT, env=env)


def write_file(directory, relpath, content="placeholder\n"):
    path = os.path.join(directory, relpath)
    parent = os.path.dirname(path)
    if parent and not os.path.isdir(parent):
        os.makedirs(parent)
    with io.open(path, "w", encoding="utf-8") as handle:
        handle.write(content)
    return path


def stamp(path, mtime):
    os.utime(path, (mtime, mtime))


def snapshot_tree(root):
    """Every path under root, paired with its content hash and mtime. Used
    to prove the tool changed nothing: this is the purity contract, and it
    is checked by comparing this structure before and after a run rather
    than by trusting the tool's own exit code."""
    out = {}
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames.sort()
        for name in sorted(filenames):
            path = os.path.join(dirpath, name)
            with io.open(path, "rb") as handle:
                digest = hashlib.sha256(handle.read()).hexdigest()
            out[os.path.relpath(path, root)] = (digest, os.path.getmtime(path))
    return out


class VerdictTests(unittest.TestCase):
    """The four real verdicts, one test each, plus the default verb."""

    def test_no_plan_when_tree_is_empty(self):
        with tempfile.TemporaryDirectory() as tmp:
            result = run_cli("status", "--root", tmp)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn("progress page: no plan yet, nothing owed.",
                      result.stdout)

    def test_status_verb_is_the_default_when_omitted(self):
        with tempfile.TemporaryDirectory() as tmp:
            result = run_cli("--root", tmp)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn("no plan yet", result.stdout)

    def test_owed_missing_when_plan_exists_and_page_does_not(self):
        with tempfile.TemporaryDirectory() as tmp:
            plan = write_file(tmp, "docs/plan/RELEASE-PLAN.md")
            stamp(plan, EARLIER)
            result = run_cli("status", "--root", tmp)
        self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
        self.assertIn("docs/plan/RELEASE-PLAN.md", result.stdout)

    def test_owed_stale_when_page_is_older_than_the_plan(self):
        with tempfile.TemporaryDirectory() as tmp:
            page = write_file(tmp, "docs/plan/RELEASE-GANTT.html")
            stamp(page, EARLIER)
            plan = write_file(tmp, "docs/plan/RELEASE-PLAN.md")
            stamp(plan, LATER)
            result = run_cli("status", "--root", tmp)
        self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
        self.assertIn("docs/plan/RELEASE-GANTT.html", result.stdout)
        self.assertIn("docs/plan/RELEASE-PLAN.md", result.stdout)

    def test_a_single_matching_page_says_nothing_about_others(self):
        """M9 guard: the ambiguity clause must not appear when there is no
        ambiguity, or it becomes noise every session learns to skip."""
        with tempfile.TemporaryDirectory() as tmp:
            plan = write_file(tmp, "docs/plan/RELEASE-PLAN.md")
            stamp(plan, EARLIER)
            page = write_file(tmp, "docs/plan/RELEASE-GANTT.html")
            stamp(page, LATER)
            result = run_cli("status", "--root", tmp)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertNotIn("also match", result.stdout)

    def test_several_matching_pages_name_the_ones_not_judged(self):
        """M9. Measured on the real repository 2026-08-19: four pages matched,
        the newest was judged, a clean CURRENT was printed, and PROJECT.md
        names a DIFFERENT page as the one current board. A pass computed from
        a file nobody considers canonical is worse than the OWED it replaced,
        because an OWED gets investigated and a pass does not."""
        with tempfile.TemporaryDirectory() as tmp:
            plan = write_file(tmp, "docs/plan/RELEASE-PLAN.md")
            stamp(plan, EARLIER)
            judged = write_file(tmp, "docs/plan/RELEASE-GANTT.html")
            stamp(judged, LATER)
            other = write_file(tmp, "docs/plan/NORTH-STAR-BOARD.html")
            stamp(other, EARLIER)
            result = run_cli("status", "--root", tmp)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn("current at docs/plan/RELEASE-GANTT.html", result.stdout)
        self.assertIn("1 other page(s) also match", result.stdout)
        self.assertIn("docs/plan/NORTH-STAR-BOARD.html", result.stdout)
        self.assertIn("wrong file", result.stdout)

    def test_the_stale_verdict_names_them_too(self):
        """The same blindness applies when the verdict is OWED: a session
        told to refresh a page needs to know which page it was told about."""
        with tempfile.TemporaryDirectory() as tmp:
            judged = write_file(tmp, "docs/plan/RELEASE-GANTT.html")
            stamp(judged, EARLIER)
            other = write_file(tmp, "docs/plan/NORTH-STAR-BOARD.html")
            stamp(other, EARLIER)
            plan = write_file(tmp, "docs/plan/RELEASE-PLAN.md")
            stamp(plan, LATER)
            result = run_cli("status", "--root", tmp)
        self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
        self.assertIn("also match", result.stdout)

    def test_a_page_at_the_repository_root_is_seen(self):
        """The Brother umbrella publishes GANTT.html AT ITS ROOT and names it in
        PROJECT.md as the project's one stable artifact. Before 2026-08-24 this
        control globbed only docs/plan/ plus the literal PROJECT-VIEW.html, so it
        reported OWED permanently over a page that was current and freshly
        published, and no session could clear the nag by doing the work it asked
        for.

        This is the SECOND occurrence of that failure: *BOARD* was added on
        2026-08-15 for the same reason one directory down. The test exists so the
        pattern list cannot silently narrow back and take the control blind
        again."""
        with tempfile.TemporaryDirectory() as tmp:
            plan = write_file(tmp, "docs/plan/RELEASE-PLAN.md")
            stamp(plan, EARLIER)
            page = write_file(tmp, "GANTT.html")
            stamp(page, LATER)
            result = run_cli("status", "--root", tmp)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn("current", result.stdout,
                      "a page at the repository root must be seen, not reported "
                      "OWED: %r" % result.stdout)
        self.assertIn("GANTT.html", result.stdout)

    def test_a_board_at_the_repository_root_is_seen(self):
        """The same widening for the word the founder actually uses."""
        with tempfile.TemporaryDirectory() as tmp:
            plan = write_file(tmp, "docs/plan/RELEASE-PLAN.md")
            stamp(plan, EARLIER)
            page = write_file(tmp, "OVERNIGHT-BOARD.html")
            stamp(page, LATER)
            result = run_cli("status", "--root", tmp)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn("current", result.stdout, result.stdout)

    def test_current_when_page_is_newer_than_the_plan(self):
        with tempfile.TemporaryDirectory() as tmp:
            plan = write_file(tmp, "docs/plan/RELEASE-PLAN.md")
            stamp(plan, EARLIER)
            page = write_file(tmp, "docs/plan/RELEASE-GANTT.html")
            stamp(page, LATER)
            result = run_cli("status", "--root", tmp)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn("docs/plan/RELEASE-GANTT.html", result.stdout)


class PlanGlobTests(unittest.TestCase):
    """Each plan glob pattern named in the contract, recognised alone."""

    def test_docs_plan_star_plan_star_md_is_recognised(self):
        with tempfile.TemporaryDirectory() as tmp:
            write_file(tmp, "docs/plan/RELEASE-v3.1.0-PLAN.md")
            result = run_cli("status", "--root", tmp)
        self.assertEqual(result.returncode, 1, result.stdout + result.stderr)

    def test_docs_plan_star_wbs_star_md_is_recognised(self):
        with tempfile.TemporaryDirectory() as tmp:
            write_file(tmp, "docs/plan/RELEASE-WBS.md")
            result = run_cli("status", "--root", tmp)
        self.assertEqual(result.returncode, 1, result.stdout + result.stderr)

    def test_root_plan_md_is_recognised(self):
        with tempfile.TemporaryDirectory() as tmp:
            write_file(tmp, "PLAN.md")
            result = run_cli("status", "--root", tmp)
        self.assertEqual(result.returncode, 1, result.stdout + result.stderr)


class PageGlobTests(unittest.TestCase):
    """Each page glob pattern named in the contract, recognised alone,
    paired with a plan file older than the page so the verdict is CURRENT
    if and only if the page pattern was actually matched."""

    def test_docs_plan_star_gantt_star_html_is_recognised(self):
        with tempfile.TemporaryDirectory() as tmp:
            plan = write_file(tmp, "PLAN.md")
            stamp(plan, EARLIER)
            page = write_file(tmp, "docs/plan/RELEASE-v3.1.0-GANTT.html")
            stamp(page, LATER)
            result = run_cli("status", "--root", tmp)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_project_view_html_at_root_is_recognised(self):
        with tempfile.TemporaryDirectory() as tmp:
            plan = write_file(tmp, "PLAN.md")
            stamp(plan, EARLIER)
            page = write_file(tmp, "PROJECT-VIEW.html")
            stamp(page, LATER)
            result = run_cli("status", "--root", tmp)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_docs_plan_star_board_star_html_is_recognised(self):
        """The regression for a control that had gone blind. A session
        published docs/plan/NORTH-STAR-PUSH-BOARD.html, linked it from
        PROJECT.md as the project's stable artifact, and this check kept
        reporting OWED (stale) against an older GANTT file because no
        pattern matched the word the founder actually uses. The page that
        exists to be SEEN was invisible to the check that exists to make
        sure it gets refreshed."""
        with tempfile.TemporaryDirectory() as tmp:
            plan = write_file(tmp, "PLAN.md")
            stamp(plan, EARLIER)
            page = write_file(tmp, "docs/plan/NORTH-STAR-PUSH-BOARD.html")
            stamp(page, LATER)
            result = run_cli("status", "--root", tmp)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn("NORTH-STAR-PUSH-BOARD.html", result.stdout)

    def test_a_board_page_older_than_the_plan_is_still_owed(self):
        """The control for the test above: matching the new pattern must not
        turn every BOARD file into an automatic pass. A stale board is still
        a stale page, and the verdict must say so."""
        with tempfile.TemporaryDirectory() as tmp:
            page = write_file(tmp, "docs/plan/NORTH-STAR-PUSH-BOARD.html")
            stamp(page, EARLIER)
            plan = write_file(tmp, "PLAN.md")
            stamp(plan, LATER)
            result = run_cli("status", "--root", tmp)
        self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
        self.assertIn("OWED (stale)", result.stdout)


class NoDataTests(unittest.TestCase):
    """The root cannot be resolved: NEVER a pass, and the line names it."""

    def test_unresolvable_root_is_no_data_not_a_pass(self):
        with tempfile.TemporaryDirectory() as tmp:
            bogus = os.path.join(tmp, "does-not-exist")
            result = run_cli("status", "--root", bogus)
        self.assertEqual(result.returncode, 2, result.stdout + result.stderr)
        self.assertTrue(result.stdout.startswith("NO-DATA:"),
                        "NO-DATA must be the first thing on stdout: %r"
                        % result.stdout)


class PurityTests(unittest.TestCase):
    """The tool must write NOTHING, ever: it runs at every session start,
    and a write here is a defect regardless of what verdict it reaches."""

    def test_the_temp_tree_is_byte_for_byte_unchanged_after_a_run(self):
        with tempfile.TemporaryDirectory() as tmp:
            plan = write_file(tmp, "docs/plan/RELEASE-PLAN.md")
            stamp(plan, EARLIER)
            page = write_file(tmp, "docs/plan/RELEASE-GANTT.html")
            stamp(page, LATER)
            extra = write_file(tmp, "notes.txt", "unrelated file\n")
            stamp(extra, EARLIER)

            before = snapshot_tree(tmp)
            result = run_cli("status", "--root", tmp)
            after = snapshot_tree(tmp)

        self.assertIn(result.returncode, (0, 1), result.stdout + result.stderr)
        self.assertEqual(before, after,
                         "bm_progress_check.py changed the tree it inspected; "
                         "this tool must be provably read-only")

    def test_an_empty_tree_stays_empty_after_a_run(self):
        with tempfile.TemporaryDirectory() as tmp:
            before = snapshot_tree(tmp)
            result = run_cli("status", "--root", tmp)
            after = snapshot_tree(tmp)
            listing = os.listdir(tmp)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual(before, after)
        self.assertEqual(listing, [],
                         "an empty root must still be empty: no .brothermode, "
                         "no store, nothing created as a side effect of asking")


if __name__ == "__main__":
    unittest.main()

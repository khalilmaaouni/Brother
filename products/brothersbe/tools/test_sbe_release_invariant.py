#!/usr/bin/env python3
"""Fixtures for tools/sbe_release_invariant.py. Run: python3 tools/test_sbe_release_invariant.py

Every test builds a real git repository under tempfile.mkdtemp(), runs the
real tool as a subprocess against it, and reads the real stdout. Nothing here
mocks git or the checker, because the defect this tool exists to catch lives
exactly at the seam between "bytes moved" and "the version string moved",
and a mocked git would test the mock rather than the seam.

The acceptance case this suite exists to prove, named the way the
release-plan review names the kill criterion it closes ("distributable bytes
change without a version change"):
test_distributable_bytes_change_without_a_version_change_fails.
"""
import io
import os
import shutil
import subprocess
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.abspath(os.path.join(HERE, ".."))
TOOL = os.path.join(HERE, "sbe_release_invariant.py")


def git(cwd, *args):
    """Run git in `cwd`, raising with the real stderr on failure.

    A raised AssertionError, not a swallowed return code: this helper builds
    the FIXTURES the tests reason about, so a git failure here is a broken
    test setup, not a scenario under test, and it must stop the test loudly
    rather than let a half-built repository produce a misleading verdict.
    """
    out = subprocess.run(["git"] + list(args), cwd=cwd, capture_output=True, text=True)
    if out.returncode != 0:
        raise AssertionError("git %s failed in %s: %s" % (" ".join(args), cwd, out.stderr))
    return out.stdout.strip()


def write(cwd, rel, body):
    path = os.path.join(cwd, rel)
    directory = os.path.dirname(path)
    if directory and not os.path.isdir(directory):
        os.makedirs(directory)
    with io.open(path, "w", encoding="utf-8") as fh:
        fh.write(body)
    return path


def commit(cwd, message):
    git(cwd, "add", "-A")
    git(cwd, "commit", "-q", "-m", message)
    return git(cwd, "rev-parse", "HEAD")


def run_tool(*argv):
    """The CompletedProcess, not a pair: evals/test_no_data_class.py reads
    every two-value return under tools/ as a possible (verdict, evidence)
    it must prove is never PASS over nothing, and a bare (code, text) tuple
    here would be exactly that shape for the wrong reason."""
    return subprocess.run([sys.executable, TOOL] + list(argv),
                          capture_output=True, text=True)


def verdict_line(proc):
    lines = [l for l in proc.stdout.splitlines() if l.strip().startswith("release-invariant")]
    assert lines, "no release-invariant verdict line in stdout: %r" % proc.stdout
    return lines[0]


def verdict_token(line):
    """The verdict WORD itself (PASS, FAIL or NO-DATA), read positionally out
    of the fixed-width report line, never by substring search over the whole
    line: the evidence prose says things like "this is not a PASS either"
    inside a NO-DATA line by design, so a plain `"PASS" in line` check reads
    that sentence as the wrong verdict. Mirrors how a human reads the report:
    the word right after the check name is the verdict, and everything past
    it is prose."""
    fields = line.split(None, 2)
    assert len(fields) >= 2, "verdict line has no verdict field: %r" % line
    return fields[1]


class ReleaseInvariantFixture(unittest.TestCase):
    """A fresh repository per test with one base commit, so `base` is a real
    commit id every test can diff HEAD against without touching `origin/main`
    (a temp repo has no remote at all, which is its own fixture below)."""

    def setUp(self):
        self.repo = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.repo, True)
        git(self.repo, "init", "-q")
        git(self.repo, "config", "user.email", "fixture@example.invalid")
        git(self.repo, "config", "user.name", "fixture")
        write(self.repo, "VERSION", "1.0.0-rc.2\n")
        write(self.repo, "tools/widget.py", "def handle():\n    return 1\n")
        write(self.repo, "docs/NOTES.md", "base notes\n")
        self.base = commit(self.repo, "base: rc.2 cut")


class TestSeededDefect(ReleaseInvariantFixture):
    """The acceptance case the direction document names by its own words:
    "distributable bytes change without a version change." """

    def test_distributable_bytes_change_without_a_version_change_fails(self):
        write(self.repo, "tools/widget.py", "def handle():\n    return 2\n")
        commit(self.repo, "fix widget, forget to bump VERSION")
        proc = run_tool(self.repo, self.base)
        self.assertEqual(proc.returncode, 0,
                         "advisory mode (no --strict) must still exit 0: %r" % proc.stdout)
        line = verdict_line(proc)
        self.assertEqual(verdict_token(line), "FAIL", "seeded defect did not FAIL: %s" % line)
        self.assertIn("tools/widget.py", line, "FAIL evidence must name the changed file: %s" % line)
        self.assertIn("VERSION", line, "FAIL evidence must say VERSION did not move: %s" % line)

    def test_the_same_change_with_a_version_bump_passes(self):
        write(self.repo, "tools/widget.py", "def handle():\n    return 2\n")
        write(self.repo, "VERSION", "1.0.0-rc.3\n")
        commit(self.repo, "fix widget and bump VERSION")
        proc = run_tool(self.repo, self.base)
        line = verdict_line(proc)
        self.assertEqual(verdict_token(line), "PASS",
                         "distributable change plus a VERSION bump must PASS: %s" % line)
        self.assertIn("tools/widget.py", line, "PASS evidence must still name what changed: %s" % line)

    def test_a_docs_only_change_is_no_data_not_pass_and_not_fail(self):
        write(self.repo, "docs/NOTES.md", "docs only, nothing a user runs\n")
        commit(self.repo, "docs: clarify notes")
        proc = run_tool(self.repo, self.base)
        line = verdict_line(proc)
        self.assertEqual(verdict_token(line), "NO-DATA", "a docs-only change must be NO-DATA: %s" % line)


class TestMissingBaseRef(ReleaseInvariantFixture):
    def test_a_base_ref_that_does_not_resolve_is_no_data_carrying_the_reason(self):
        write(self.repo, "tools/widget.py", "def handle():\n    return 2\n")
        commit(self.repo, "a real change, but the base ref below never existed here")
        proc = run_tool(self.repo, "origin/main")
        line = verdict_line(proc)
        self.assertEqual(verdict_token(line), "NO-DATA",
                         "an unresolvable base ref must be NO-DATA, never a crash and never a "
                         "silent PASS: %s" % line)
        self.assertIn("origin/main", line, "the reason must name the ref it could not resolve: %s" % line)
        self.assertEqual(proc.returncode, 0, "a NO-DATA verdict must not itself exit nonzero")


class TestStrictMode(ReleaseInvariantFixture):
    def _seed_fail(self):
        write(self.repo, "tools/widget.py", "def handle():\n    return 2\n")
        commit(self.repo, "fix widget, forget to bump VERSION")

    def test_strict_makes_a_fail_exit_nonzero(self):
        self._seed_fail()
        proc = run_tool(self.repo, self.base, "--strict")
        self.assertEqual(verdict_token(verdict_line(proc)), "FAIL")
        self.assertNotEqual(proc.returncode, 0,
                            "--strict over a FAIL must exit nonzero to block a merge: %r" % proc.stdout)

    def test_without_strict_the_same_fail_exits_zero(self):
        self._seed_fail()
        proc = run_tool(self.repo, self.base)
        self.assertEqual(verdict_token(verdict_line(proc)), "FAIL")
        self.assertEqual(proc.returncode, 0,
                         "advisory mode must report the FAIL and still exit 0: %r" % proc.stdout)


class TestDistributableScope(unittest.TestCase):
    """The scope declaration itself, checked against the real repository this
    tool ships beside (Part 2's own requirement: confirm each declared root
    exists before hardcoding it), not merely asserted in a comment."""

    def setUp(self):
        sys.path.insert(0, HERE)
        self.addCleanup(sys.path.remove, HERE)
        import sbe_release_invariant as sri
        self.sri = sri

    def test_every_declared_root_exists_and_scope_is_first_segment_only(self):
        missing = [d for d in self.sri.DISTRIBUTABLE_DIRS
                  if not os.path.isdir(os.path.join(ROOT, d))]
        missing += [f for f in self.sri.DISTRIBUTABLE_FILES
                   if not os.path.isfile(os.path.join(ROOT, f))]
        self.assertEqual(missing, [],
                         "sbe_release_invariant's declared distributable roots include %r, which "
                         "do not exist under %s; the declared scope has drifted from the real "
                         "tree" % (missing, ROOT))
        self.assertFalse(self.sri._is_distributable("docs/RELEASE.md"))
        self.assertFalse(self.sri._is_distributable("VERSION"))
        self.assertTrue(self.sri._is_distributable("tools/sbe_gate.py"))
        self.assertTrue(self.sri._is_distributable("src/brothersbe/evidence.py"))
        self.assertTrue(self.sri._is_distributable("install.sh"))
        self.assertTrue(self.sri._is_distributable(".claude-plugin/plugin.json"))
        # Matched by the FIRST path segment, exactly, not by prefix: a
        # sibling directory that merely starts with a distributable name
        # must not be swept in by a substring match.
        self.assertFalse(self.sri._is_distributable("tools-legacy/old.py"))


if __name__ == "__main__":
    unittest.main()

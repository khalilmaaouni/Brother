#!/usr/bin/env python3
"""Regression tests for tools/bm_release_invariant.py (BM-A6).

WHAT THIS SUITE IS ACTUALLY DEFENDING
  The rule this gate exists to catch is real, not hypothetical: pull
  requests #51 (a9bd96e, fix/containment-message-tells-the-truth) and #52
  (afb6b3d, fix/first-session-says-what-to-do), landed on this project's
  own main branch, together changed tools/bm_sessionstart.py and
  tools/bm_store.py while VERSION stayed at 3.3.2 the whole way through.
  test_the_pr_51_and_52_range_is_flagged_for_real proves this gate FAILs
  that exact real range in the real repository, not a synthetic stand-in.
  Every other test builds a fresh temporary git repository, the same
  discipline tools/test_sbe_release_invariant.py uses in the sibling
  project this gate ports the concept from: nothing here mocks git,
  because the defect lives exactly at the seam between "bytes moved" and
  "the version string moved", and a mocked git tests the mock, not the
  seam.

Standard library only. Run: python3 tools/test_bm_release_invariant.py
"""
import io
import os
import shutil
import subprocess
import sys
import tempfile
import unittest

# E100: one sandbox for every temp tree this process makes, removed at exit.
import os as _e100_os, sys as _e100_sys  # noqa: E402
_e100_sys.path.append(_e100_os.path.join(
    _e100_os.path.dirname(_e100_os.path.abspath(__file__)), '../../../scripts'))
try:  # noqa: E402
    import tmp_sandbox as _e100_tmp
    _e100_tmp.install()
except ImportError:
    # A packager (scripts/export_public.py, make_benchmark_bundle.py)
    # can copy this test without scripts/tmp_sandbox.py beside it. Say
    # so rather than dying: the sandbox is hygiene, not the subject.
    _e100_sys.stderr.write(
        "tmp_sandbox absent: %s leaves its temp trees behind\n"
        % _e100_os.path.basename(__file__))

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
TOOL_PATH = os.path.join(HERE, "bm_release_invariant.py")

# The real, already-landed range this project's own history names by pull
# request number. Confirmed by hand before this file was written:
#   git log --all --oneline --grep="#51" --grep="#52" -i
#     afb6b3d Merge pull request #52 from .../fix/first-session-says-what-to-do
#     a9bd96e Merge pull request #51 from .../fix/containment-message-tells-the-truth
#   git diff --name-only a9bd96e~1 afb6b3d
#     tools/bm_sessionstart.py, tools/bm_store.py,
#     tools/test_bm_consent.py, tools/test_bm_store.py
#   git show a9bd96e~1:VERSION and git show afb6b3d:VERSION both read 3.3.2
PR_51_52_BASE = "a9bd96e~1"
PR_51_52_HEAD = "afb6b3d"


def run_cli(root, base=None, head=None):
    """Invoke the CLI as a real subprocess against `root` (--root) and
    `base` (--base, omitted means the tool's own DEFAULT_BASE)."""
    env = dict(os.environ)
    env.pop("BROTHERMODE_ROOT", None)
    args = ["--root", root]
    if base is not None:
        args += ["--base", base]
    if head is not None:
        args += ["--head", head]
    return subprocess.run(
        [sys.executable, TOOL_PATH] + args,
        stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        universal_newlines=True, cwd=ROOT, env=env)


def verdict_token(stdout):
    """The verdict WORD, read positionally: "release-invariant <VERDICT> ...".
    Mirrors test_sbe_release_invariant.py's own verdict_token: never a
    substring search, because the reason text can legitimately contain the
    words PASS or FAIL inside a NO-DATA sentence."""
    lines = [l for l in stdout.splitlines() if l.startswith("release-invariant ")]
    assert lines, "no release-invariant verdict line in stdout: %r" % stdout
    fields = lines[0].split(None, 2)
    assert len(fields) >= 2, "verdict line has no verdict field: %r" % lines[0]
    return fields[1]


def git(cwd, *args):
    """Run git in `cwd`, raising with the real stderr on failure: this
    builds the FIXTURES the tests reason about, so a failure here is a
    broken test setup, never a scenario under test."""
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


class ReleaseInvariantFixture(unittest.TestCase):
    """A fresh repository per test with one base commit, so `base` is a
    real commit id every test can diff HEAD against, mirroring
    test_sbe_release_invariant.py's own ReleaseInvariantFixture."""

    def setUp(self):
        self.repo = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.repo, True)
        git(self.repo, "init", "-q")
        git(self.repo, "config", "user.email", "fixture@example.invalid")
        git(self.repo, "config", "user.name", "fixture")
        write(self.repo, "VERSION", "3.3.2\n")
        write(self.repo, "tools/widget.py", "def handle():\n    return 1\n")
        write(self.repo, "tools/test_widget.py", "def test_handle():\n    assert True\n")
        write(self.repo, "docs/NOTES.md", "base notes\n")
        self.base = commit(self.repo, "base: 3.3.2 cut")


class TestRefusalCase(ReleaseInvariantFixture):
    """(a) REFUSAL: a distributable file changes, the version does not,
    the check fails."""

    def test_distributable_change_without_a_version_bump_fails(self):
        write(self.repo, "tools/widget.py", "def handle():\n    return 2\n")
        commit(self.repo, "fix widget, forget to bump VERSION")
        proc = run_cli(self.repo, self.base)
        self.assertEqual(proc.returncode, 1,
                         "a FAIL must exit nonzero with NO flag required, or the gate "
                         "refuses nothing: every runner that chains on exit status "
                         "reads exit 0 as a pass: %r" % proc.stdout)
        token = verdict_token(proc.stdout)
        self.assertEqual(token, "FAIL", "seeded defect did not FAIL: %s" % proc.stdout)
        self.assertIn("tools/widget.py", proc.stdout)
        self.assertIn("VERSION", proc.stdout)

    def test_a_fail_blocks_without_any_opt_in_flag(self):
        """The calibration for the exit code itself. An earlier draft made
        FAIL advisory unless --strict was passed, so the gate printed FAIL
        and exited 0, which every chained runner reads as a pass. This test
        fails if that posture ever comes back."""
        write(self.repo, "tools/widget.py", "def handle():\n    return 2\n")
        commit(self.repo, "fix widget, forget to bump VERSION")
        proc = run_cli(self.repo, self.base)
        self.assertEqual(verdict_token(proc.stdout), "FAIL")
        self.assertEqual(proc.returncode, 1,
                         "a FAIL must block on its own: %r" % proc.stdout)


class TestAllowCase(ReleaseInvariantFixture):
    """(b) ALLOW: a test-only change with the version unchanged never
    fails. It reads NO-DATA here, not PASS: a test file is deliberately
    non-distributable, so the honest verdict is "nothing distributable
    changed", never a silent PASS over a claim nobody proved."""

    def test_a_test_only_change_with_version_unchanged_is_allowed(self):
        write(self.repo, "tools/test_widget.py",
              "def test_handle():\n    assert True  # widened\n")
        commit(self.repo, "widen a test, nothing distributable moved")
        proc = run_cli(self.repo, self.base)
        token = verdict_token(proc.stdout)
        self.assertEqual(token, "NO-DATA",
                         "a test-only change must never FAIL: %s" % proc.stdout)

    def test_a_docs_only_change_is_also_allowed(self):
        write(self.repo, "docs/NOTES.md", "docs only, nothing a user runs\n")
        commit(self.repo, "docs: clarify notes")
        proc = run_cli(self.repo, self.base)
        self.assertEqual(verdict_token(proc.stdout), "NO-DATA")

    def test_distributable_change_with_a_version_bump_passes(self):
        write(self.repo, "tools/widget.py", "def handle():\n    return 2\n")
        write(self.repo, "VERSION", "3.4.0\n")
        commit(self.repo, "fix widget and bump VERSION")
        proc = run_cli(self.repo, self.base)
        self.assertEqual(verdict_token(proc.stdout), "PASS",
                         "distributable change plus a VERSION bump must PASS: %s" % proc.stdout)
        self.assertIn("tools/widget.py", proc.stdout)


class TestNoDataUnclassified(ReleaseInvariantFixture):
    """(d) NO-DATA: a file whose classification cannot be decided is
    reported NO-DATA, not passed, even alongside a real distributable
    violation in the same range."""

    def test_an_unclassified_top_level_path_is_no_data_not_pass(self):
        write(self.repo, "totally-new-top-level-thing/mystery.bin", "??\n")
        commit(self.repo, "add a path this gate has never seen before")
        proc = run_cli(self.repo, self.base)
        token = verdict_token(proc.stdout)
        self.assertEqual(token, "NO-DATA",
                         "an unclassifiable path must never be silently treated as "
                         "non-distributable: %s" % proc.stdout)
        self.assertIn("totally-new-top-level-thing", proc.stdout)

    def test_unclassified_path_wins_over_a_real_violation_in_the_same_range(self):
        write(self.repo, "tools/widget.py", "def handle():\n    return 2\n")
        write(self.repo, "totally-new-top-level-thing/mystery.bin", "??\n")
        commit(self.repo, "a real distributable change plus an unclassified path, no VERSION bump")
        proc = run_cli(self.repo, self.base)
        self.assertEqual(verdict_token(proc.stdout), "NO-DATA",
                         "uncertainty must dominate rather than surface a guessed FAIL: %s"
                         % proc.stdout)
        self.assertIn("totally-new-top-level-thing", proc.stdout)


class TestMissingBaseRef(ReleaseInvariantFixture):
    def test_an_unresolvable_base_ref_is_no_data_carrying_the_reason(self):
        write(self.repo, "tools/widget.py", "def handle():\n    return 2\n")
        commit(self.repo, "a real change, but the base ref below never existed here")
        proc = run_cli(self.repo, "origin/main")
        self.assertEqual(verdict_token(proc.stdout), "NO-DATA")
        self.assertIn("origin/main", proc.stdout)
        self.assertEqual(proc.returncode, 0, "a NO-DATA verdict must not itself exit nonzero")


class TestCalibrationAgainstRealHistory(unittest.TestCase):
    """(c) CALIBRATION: the historical range covering pull requests #51 and
    #52 is flagged by this check, run against THIS repository's own real
    commits over --base a9bd96e~1 (HEAD is whatever this checkout has),
    never a substitute fixture. Skips (does not fake a PASS) on a checkout
    that does not carry that history, naming the reason."""

    def test_the_pr_51_and_52_range_is_flagged_for_real(self):
        has_commit = subprocess.run(
            ["git", "cat-file", "-e", PR_51_52_HEAD], cwd=ROOT, capture_output=True)
        if has_commit.returncode != 0:
            self.skipTest("commit %s is not present in this checkout; this worktree "
                          "does not carry the PR #51/#52 history, so the calibration "
                          "cannot run here" % PR_51_52_HEAD)
        is_ancestor = subprocess.run(
            ["git", "merge-base", "--is-ancestor", PR_51_52_HEAD, "HEAD"],
            cwd=ROOT, capture_output=True)
        if is_ancestor.returncode != 0:
            self.skipTest("HEAD in this worktree is not a descendant of %s; the "
                          "#51/#52 range is not on this checkout's own history"
                          % PR_51_52_HEAD)
        # BOTH ends pinned, deliberately. This originally ran base..HEAD and
        # passed, until this repository's own 3.4.0 version bump entered that
        # range and turned the FAIL into a PASS. A calibration whose range keeps
        # growing is measuring a moving tree, so it stops testing what it names.
        proc = run_cli(ROOT, PR_51_52_BASE, head=PR_51_52_HEAD)
        token = verdict_token(proc.stdout)
        self.assertEqual(token, "FAIL",
                         "the real #51/#52 range (tools/bm_sessionstart.py and "
                         "tools/bm_store.py changed, VERSION held at 3.3.2) must FAIL: %s"
                         % proc.stdout)
        self.assertIn("tools/bm_sessionstart.py", proc.stdout)
        self.assertIn("tools/bm_store.py", proc.stdout)


class TestDistributableScope(unittest.TestCase):
    """The scope declaration itself, checked against the real repository
    this tool ships beside, mirroring
    test_sbe_release_invariant.py's own TestDistributableScope."""

    def setUp(self):
        sys.path.insert(0, HERE)
        self.addCleanup(sys.path.remove, HERE)
        import bm_release_invariant as bri
        self.bri = bri

    def test_every_declared_path_exists_in_this_repository(self):
        missing_dirs = [d for d in self.bri.DISTRIBUTABLE_DIRS + self.bri.NON_DISTRIBUTABLE_DIRS
                        if not os.path.isdir(os.path.join(ROOT, d))]
        missing_files = [f for f in self.bri.DISTRIBUTABLE_FILES + self.bri.NON_DISTRIBUTABLE_FILES
                         if not os.path.isfile(os.path.join(ROOT, f))]
        self.assertEqual(missing_dirs, [],
                         "bm_release_invariant's declared directories include %r, which do "
                         "not exist under %s; the declared scope has drifted from the real "
                         "tree" % (missing_dirs, ROOT))
        self.assertEqual(missing_files, [],
                         "bm_release_invariant's declared files include %r, which do not "
                         "exist under %s" % (missing_files, ROOT))

    def test_classification_matches_first_segment_only(self):
        self.assertEqual(self.bri.classify("tools/bm_gate.py"), "distributable")
        self.assertEqual(self.bri.classify("src_missing/whatever.py"), "unclassified")
        self.assertEqual(self.bri.classify("docs/RELEASE.md"), "non-distributable")
        self.assertEqual(self.bri.classify("VERSION"), "version")
        self.assertEqual(self.bri.classify(".claude-plugin/plugin.json"), "distributable")
        # Matched by the FIRST path segment, exactly, not by prefix: a
        # sibling directory that merely starts with a distributable name
        # must not be swept in by a substring match.
        self.assertEqual(self.bri.classify("tools-legacy/old.py"), "unclassified")


if __name__ == "__main__":
    unittest.main()

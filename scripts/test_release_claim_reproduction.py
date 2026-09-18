#!/usr/bin/env python3
"""Tests for release_claim_reproduction.py.

PROPERTY PROTECTED: verify_claim() must report PASS only when a real,
independent `git clone` of the release repository reproduces the claimed
manifest digest at the claimed tag, and it must never report PASS on a
missing tag, an unreachable clone, a commit that does not match the claim,
a digest that does not match the claim, or an export manifest naming zero
files.

EDGE LIST WALKED:
  empty              -> an export manifest with zero rows
                        (test_empty_manifest_is_none_not_zero,
                        test_empty_manifest_is_never_a_pass)
  exactly one        -> a repo with exactly one tag, one manifest row
                        (test_tag_resolves_to_its_commit,
                        test_well_formed_manifest_counts_its_rows)
  unknown value      -> a tag that does not exist in the fresh clone
                        (test_missing_tag_is_none, test_missing_tag_is_nodata)
  corrupt/truncated  -> a manifest present but not the "<sha256>  <path>"
                        shape reproduce_export.py's own parser reads
                        (covered by reproduce_export.py's own suite; this
                        module's manifest_entry_count delegates to
                        RE.parse_manifest rather than re-parsing, so a
                        corrupt manifest here reads the same None NO-DATA
                        as an absent one, test_missing_manifest_is_none)
  concurrent actor   -> N/A: one-shot function, its own unique temp dir per
                        call (tempfile.mkdtemp), no shared or persisted state
  expired/stale      -> N/A: no claim store, no TTL; every call reproduces
                        fresh from whatever the repo names right now
  already/partial    -> N/A: nothing is recorded between calls to compare
                        "done" against
  actor same as last time -> N/A: no identity is tracked across calls
  a real tamper       -> one byte changed in a shipped file after the digest
                        was recorded, caught only by re-cloning and
                        re-verifying, never by trusting the same working
                        tree twice (test_pass_on_untouched_release_fail_after_tamper,
                        the one test in this file with no mocks at all)

OUT OF SCOPE, and why: network transport. `git clone` of a local path or a
bare directory never touches the network, so there is nothing here to prove
about a remote clone failing partway through a fetch; that is git's own
contract, not this module's.
"""
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import release_claim_reproduction as MOD  # noqa: E402
import reproduce_export as RE  # noqa: E402
import e80_release_reproduction_drive as DRIVE  # noqa: E402

# This machine's global ~/.gitconfig sets tag.gpgSign = true, which turns
# even a plain `git tag NAME` into an annotated, signing tag that refuses
# without a message and a key ("fatal: no tag message?"). Every fixture in
# this file is a throwaway repo with no signing need, and
# DRIVE.build_fixture_release (reused here, not owned by this unit) shells
# out to git with no way for this file to pass it a `-c` flag, so the
# override goes through git's own environment-variable config mechanism
# instead: it reaches every git subprocess this test forks, including
# DRIVE's, without writing to any config file, global or repository-local.
os.environ["GIT_CONFIG_COUNT"] = "1"
os.environ["GIT_CONFIG_KEY_0"] = "tag.gpgSign"
os.environ["GIT_CONFIG_VALUE_0"] = "false"


def _git(cwd, *args):
    """A real git command, run for real (never mocked in this helper): the
    fixtures below need an actual object database and actual refs, since
    the whole point of this module is behavior only a real clone exposes."""
    proc = subprocess.run(["git"] + list(args), cwd=cwd, capture_output=True,
                          text=True)
    if proc.returncode != 0:
        raise RuntimeError("git %s failed in %s: %s"
                           % (" ".join(args), cwd, proc.stderr.strip()))
    return proc.stdout


def _init_repo(path):
    os.makedirs(path, exist_ok=True)
    _git(path, "init", "-q")
    _git(path, "config", "user.email", "test@example.invalid")
    _git(path, "config", "user.name", "Test")
    return path


def _commit_all(path, message="commit"):
    _git(path, "add", "-A")
    _git(path, "commit", "-q", "-m", message)
    return _git(path, "rev-parse", "HEAD").strip()


class RunHelper(unittest.TestCase):
    """run(cmd, cwd) -- an environment failure (the binary cannot start, or
    hangs) must never be read as a verdict about a claim: it raises."""

    def test_success_passthrough(self):
        with mock.patch.object(
                MOD.subprocess, "run",
                return_value=subprocess.CompletedProcess(
                    ["x"], 0, stdout="ok\n", stderr="")):
            proc = MOD.run(["x"])
        self.assertEqual((proc.returncode, proc.stdout), (0, "ok\n"))

    def test_oserror_raises_runtimeerror(self):
        with mock.patch.object(MOD.subprocess, "run",
                               side_effect=OSError("no such binary")):
            with self.assertRaises(RuntimeError):
                MOD.run(["git", "clone", "x", "y"])

    def test_timeout_raises_runtimeerror(self):
        with mock.patch.object(
                MOD.subprocess, "run",
                side_effect=subprocess.TimeoutExpired(cmd="git", timeout=1)):
            with self.assertRaises(RuntimeError):
                MOD.run(["git", "clone", "x", "y"])


class FreshClone(unittest.TestCase):
    """fresh_clone(repo, dest) -- must be a real `git clone`, never a
    worktree add: a worktree's `.git` is a FILE pointing back at the
    source, a clone's `.git` is its own directory."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="rcr-test-")
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)

    def test_real_clone_has_its_own_git_directory(self):
        src = _init_repo(os.path.join(self.tmp, "src"))
        with open(os.path.join(src, "a.txt"), "w", encoding="utf-8") as fh:
            fh.write("hello\n")
        _commit_all(src)
        dest = os.path.join(self.tmp, "clone")
        ok, err = MOD.fresh_clone(src, dest)
        self.assertTrue(ok, err)
        self.assertTrue(
            os.path.isdir(os.path.join(dest, ".git")),
            "a real clone must have its own .git directory, not a "
            "worktree's .git file pointer back at the source")

    def test_missing_source_is_not_ok(self):
        ok, err = MOD.fresh_clone(os.path.join(self.tmp, "does-not-exist"),
                                  os.path.join(self.tmp, "clone2"))
        self.assertFalse(ok)
        self.assertTrue(err)


class ResolveTagCommit(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="rcr-test-")
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)
        self.repo = _init_repo(os.path.join(self.tmp, "repo"))
        with open(os.path.join(self.repo, "a.txt"), "w", encoding="utf-8") as fh:
            fh.write("x\n")
        self.commit = _commit_all(self.repo)
        _git(self.repo, "tag", "v9.9.9")

    def test_tag_resolves_to_its_commit(self):
        self.assertEqual(MOD.resolve_tag_commit(self.repo, "v9.9.9"),
                         self.commit)

    def test_missing_tag_is_none(self):
        self.assertIsNone(MOD.resolve_tag_commit(self.repo, "v0.0.1-nope"))


class ManifestEntryCount(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="rcr-test-")
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)
        self.repo = _init_repo(os.path.join(self.tmp, "repo"))

    def _tag_with_manifest(self, text):
        rel = RE.manifest_path_for("9.9.9")
        full = os.path.join(self.repo, rel)
        os.makedirs(os.path.dirname(full), exist_ok=True)
        with open(full, "w", encoding="utf-8") as fh:
            fh.write(text)
        _commit_all(self.repo)
        _git(self.repo, "tag", "-f", "v9.9.9")
        return "v9.9.9"

    def test_well_formed_manifest_counts_its_rows(self):
        tag = self._tag_with_manifest("%s  a.txt\n" % ("a" * 64))
        self.assertEqual(MOD.manifest_entry_count(self.repo, tag), 1)

    def test_empty_manifest_is_none_not_zero(self):
        # RE.parse_manifest returns None for zero rows, never an empty list:
        # this asserts manifest_entry_count inherits that, so an empty
        # manifest reads NO-DATA, never a count of 0 that some caller could
        # mistake for "checked, and there were none".
        tag = self._tag_with_manifest("")
        self.assertIsNone(MOD.manifest_entry_count(self.repo, tag))

    def test_missing_manifest_is_none(self):
        with open(os.path.join(self.repo, "a.txt"), "w", encoding="utf-8") as fh:
            fh.write("x\n")
        _commit_all(self.repo)
        _git(self.repo, "tag", "v1.2.3")
        self.assertIsNone(MOD.manifest_entry_count(self.repo, "v1.2.3"))


class RunVerifyTree(unittest.TestCase):
    """run_verify_tree(clone_dir, tag, digest) -- proves the command shape
    (the clone's OWN script, --verify-tree, --tag, --expect) with a stub
    script, without paying for a real reproduce_export.py run here."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="rcr-test-")
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)

    def test_missing_script_is_nodata(self):
        code, out = MOD.run_verify_tree(self.tmp, "v1.0.0", "deadbeef")
        self.assertIsNone(code)
        self.assertIn("NO-DATA", out)

    def test_present_script_is_run_from_inside_the_clone(self):
        scripts = os.path.join(self.tmp, "scripts")
        os.makedirs(scripts)
        stub = os.path.join(scripts, "reproduce_export.py")
        with open(stub, "w", encoding="utf-8") as fh:
            fh.write("import sys\n"
                     "print('stub saw:', sys.argv[1:])\n"
                     "sys.exit(3)\n")
        code, out = MOD.run_verify_tree(self.tmp, "v1.0.0", "deadbeef")
        self.assertEqual(code, 3)
        self.assertIn("--verify-tree", out)
        self.assertIn("v1.0.0", out)
        self.assertIn("deadbeef", out)


class VerifyClaimVerdict(unittest.TestCase):
    """verify_claim() -- the verdict arithmetic, with the four real-git
    helpers replaced by fast fakes so each of the non-pass conditions can
    be flipped alone. The real-clone versions of these same conditions are
    exercised for real in VerifyClaimRealClone below."""

    def _run(self, clone_ok=(True, ""), resolved="deadbeef", count=2,
             verify=(0, "PASS: ok"), commit="deadbeef"):
        with mock.patch.object(MOD, "fresh_clone", return_value=clone_ok), \
             mock.patch.object(MOD, "resolve_tag_commit", return_value=resolved), \
             mock.patch.object(MOD, "manifest_entry_count", return_value=count), \
             mock.patch.object(MOD, "run_verify_tree", return_value=verify):
            return MOD.verify_claim("repo", "v1.0.0", "digestZ", commit=commit)

    def test_all_conditions_true_is_pass(self):
        code, lines = self._run()
        self.assertEqual(code, 0)
        self.assertTrue(any("PASS" in l for l in lines))

    def test_clone_failure_is_nodata(self):
        code, lines = self._run(clone_ok=(False, "boom"))
        self.assertEqual(code, 2)
        self.assertIn("NO-DATA", lines[0])

    def test_missing_tag_is_nodata(self):
        code, lines = self._run(resolved=None)
        self.assertEqual(code, 2)
        self.assertIn("unreachable", lines[0])

    def test_commit_mismatch_is_fail(self):
        code, lines = self._run(resolved="some-other-commit")
        self.assertEqual(code, 1)
        self.assertIn("FAIL", lines[0])

    def test_commit_check_skipped_when_not_given(self):
        code, _lines = self._run(resolved="whatever", commit=None)
        self.assertEqual(code, 0)

    def test_empty_manifest_is_never_a_pass(self):
        code, lines = self._run(count=0)
        self.assertEqual(code, 2)
        self.assertIn("NO-DATA", lines[0])

    def test_none_manifest_count_is_nodata(self):
        code, lines = self._run(count=None)
        self.assertEqual(code, 2)

    def test_script_missing_in_clone_is_nodata(self):
        code, lines = self._run(verify=(None, "NO-DATA: no script"))
        self.assertEqual(code, 2)

    def test_verify_tree_nodata_stays_nodata(self):
        code, lines = self._run(verify=(2, "NO-DATA: manifest unreadable"))
        self.assertEqual(code, 2)

    def test_digest_mismatch_is_fail(self):
        code, lines = self._run(verify=(1, "FAIL: digest mismatch"))
        self.assertEqual(code, 1)

    def test_temp_dir_is_always_cleaned_up(self):
        captured = {}
        real_mkdtemp = tempfile.mkdtemp

        def spying_mkdtemp(*a, **k):
            path = real_mkdtemp(*a, **k)
            captured["dest"] = path
            return path

        with mock.patch.object(MOD.tempfile, "mkdtemp",
                               side_effect=spying_mkdtemp), \
             mock.patch.object(MOD, "fresh_clone", return_value=(True, "")), \
             mock.patch.object(MOD, "resolve_tag_commit", return_value="c"), \
             mock.patch.object(MOD, "manifest_entry_count", return_value=1), \
             mock.patch.object(MOD, "run_verify_tree",
                               return_value=(0, "PASS")):
            MOD.verify_claim("repo", "v1.0.0", "digestZ")
        self.assertIn("dest", captured)
        self.assertFalse(os.path.exists(captured["dest"]),
                         "verify_claim must remove its clone directory in "
                         "its finally block")


class VerifyClaimRealClone(unittest.TestCase):
    """The one fully real test in this file: no mocks. It builds a real
    fixture release the way e80_release_reproduction_drive.py does, reusing
    its build_fixture_release rather than restating it, then proves this
    module's verify_claim() reproduces the claim from a genuinely
    independent `git clone` of that fixture: PASS on the untouched
    release, FAIL once one byte is tampered with after the digest was
    recorded. Costs roughly what e80's own drive costs (about a minute):
    it builds a real export tree with real git commands. This is the
    exact gap the unit exists to close: e80's own test suite states plainly
    that it never proves a real clone reproduces or a real tamper is
    caught; this test is that proof."""

    def setUp(self):
        self.dest = tempfile.mkdtemp(prefix="rcr-fixture-")
        self.addCleanup(shutil.rmtree, self.dest, ignore_errors=True)

    def test_pass_on_untouched_release_fail_after_tamper(self):
        DRIVE.build_fixture_release(self.dest)
        digest = RE.manifest_digest(RE.manifest_from_dir(self.dest))
        commit = _git(self.dest, "rev-list", "-n", "1", DRIVE.TAG).strip()

        code, lines = MOD.verify_claim(self.dest, DRIVE.TAG, digest,
                                       commit=commit)
        self.assertEqual(code, 0, "\n".join(lines))
        self.assertTrue(any("PASS" in l for l in lines))

        victim = os.path.join(self.dest, DRIVE.VICTIM)
        with open(victim, "rb") as fh:
            data = fh.read()
        with open(victim, "wb") as fh:
            fh.write(data + b"\n# tampered\n")
        _git(self.dest, "add", "-A", "-f")
        _git(self.dest, "commit", "-q", "-m", "tamper")
        _git(self.dest, "tag", "-f", DRIVE.TAG)

        code, lines = MOD.verify_claim(self.dest, DRIVE.TAG, digest,
                                       commit=None)
        self.assertEqual(code, 1, "\n".join(lines))


if __name__ == "__main__":
    unittest.main()

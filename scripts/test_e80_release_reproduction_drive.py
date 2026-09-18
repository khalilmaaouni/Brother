#!/usr/bin/env python3
"""Tests for e80_release_reproduction_drive.py.

PROPERTY PROTECTED: the drive must never report a passing release when any
one of its three real conditions is false -- the untouched fixture tag
reproduces cleanly, a one-byte tamper is refused, and the clone carries its
own export allowlist -- and it must never blame the PRODUCT (a printed
FAIL/PASS verdict) for a broken FIXTURE step (a git command that itself
failed), which is why fixture-step failures raise SystemExit tagged
NO-DATA instead of flowing into the pass/fail verdict.

WHAT THIS SUITE DOES NOT COVER, AND WHY. The module's whole reason to
exist is an end-to-end drive: build a real export tree with
export_public.build_export_tree, commit and tag it with real git, then
shell out to reproduce_export.py from inside that clone with no hub
access, twice (clean and tampered). Running that for real costs about a
minute per the module's own docstring and needs a real git binary; this
machine is shared and this suite must stay fast and offline. So this suite
tests every unit of real, un-mocked logic that does not require the full
build (the git-command shape build_fixture_release emits, the NO-DATA
refusal contract of run(), the pass-through contract of reproduce_from()),
and it drives the full main() verdict logic with build_fixture_release,
EP.load_allowlist and reproduce_from replaced by fast fakes that write only
plain files under a temp directory (no git, no subprocess to
reproduce_export.py). That proves the WIRING and the verdict arithmetic
are correct. It does NOT prove that a real git clone actually reproduces
or that reproduce_export.py actually catches a real tamper: that is only
proven by running the module's own main() for real, which is what this
repo's release done-check does separately.

EDGE LIST WALKED:
  empty / boundary  -> allowlist is None, or copies zero files: both refuse
                       (test_no_allowlist_is_nodata, test_empty_copy_is_nodata)
  unknown value     -> a fixture step (git command) exits nonzero: raises
                       NO-DATA rather than a FAIL (test_run_failure_raises_nodata)
  corrupt/truncated -> N/A at this layer: the module never parses a shipped
                       artifact itself, it only re-runs another script's own
                       parser (reproduce_export.py) as a subprocess, whose
                       corrupt-input handling is that script's own contract
  many              -> the git command sequence has several steps; the one
                       that must never regress to a bare "-A" is asserted by
                       name (test_build_fixture_release_uses_add_dash_f)
  boundary (verdict) -> each of the three PASS conditions is flipped one at
                       a time to prove the verdict needs all three, not "two
                       out of three" (test_main_* below)

OUT OF SCOPE, and why: "concurrent second actor", "expired or stale",
"already done", "partially done" and "actor same as last time" do not
apply to a one-shot drive script with no persisted claim, lock, or state
across runs; each invocation builds its own throwaway directory and
deletes it in a finally block.
"""
import os
import shutil
import sys
import tempfile
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import e80_release_reproduction_drive as DRIVE  # noqa: E402


class FakeCompleted:
    def __init__(self, returncode=0, stdout="", stderr=""):
        self.returncode = returncode
        self.stdout = stdout
        self.stderr = stderr


class RunHelper(unittest.TestCase):
    """run(cmd, cwd) -- the fixture-step wrapper. A broken fixture step is
    NEVER allowed to surface as a product FAIL; it must raise NO-DATA."""

    def test_success_returns_stdout(self):
        with mock.patch.object(DRIVE.subprocess, "run",
                                return_value=FakeCompleted(0, "hello\n", "")):
            out = DRIVE.run(["irrelevant"], cwd="/tmp")
        self.assertEqual(out, "hello\n")

    def test_failure_raises_systemexit_tagged_nodata(self):
        with mock.patch.object(
                DRIVE.subprocess, "run",
                return_value=FakeCompleted(1, "", "boom")):
            with self.assertRaises(SystemExit) as ctx:
                DRIVE.run(["git", "add", "-A", "-f"], cwd="/tmp")
        self.assertIn("NO-DATA", str(ctx.exception))
        self.assertIn("boom", str(ctx.exception))

    def test_failure_message_names_the_command(self):
        with mock.patch.object(
                DRIVE.subprocess, "run",
                return_value=FakeCompleted(2, "", "err")):
            with self.assertRaises(SystemExit) as ctx:
                DRIVE.run(["git", "tag", "-f", "v9.9.9"], cwd="/tmp")
        self.assertIn("git tag -f v9.9.9", str(ctx.exception))


class ReproduceFrom(unittest.TestCase):
    """reproduce_from(dest) -- unlike run(), this one must NEVER raise on a
    nonzero exit: both directions of the trial are meant to be observed,
    not aborted on."""

    def test_passes_through_clean_exit(self):
        with mock.patch.object(DRIVE.subprocess, "run",
                                return_value=FakeCompleted(0, "ok\n", "")):
            code, out = DRIVE.reproduce_from("/tmp")
        self.assertEqual((code, out), (0, "ok"))

    def test_passes_through_nonzero_exit_without_raising(self):
        with mock.patch.object(
                DRIVE.subprocess, "run",
                return_value=FakeCompleted(1, "mismatch\n", "")):
            code, out = DRIVE.reproduce_from("/tmp")
        self.assertEqual((code, out), (1, "mismatch"))


class BuildFixtureRelease(unittest.TestCase):
    """build_fixture_release(dest) -- refuses on a missing or empty
    allowlist, and must commit with 'git add -A -f', never a bare '-A'
    (the exact regression named in the module docstring: a plain 'git add
    -A' obeys the copied hub .gitignore and silently drops shipped files)."""

    def setUp(self):
        self.dest = tempfile.mkdtemp(prefix="e80-test-fixture-")

    def tearDown(self):
        shutil.rmtree(self.dest, ignore_errors=True)

    def test_no_allowlist_is_nodata(self):
        with mock.patch.object(DRIVE.EP, "load_allowlist", return_value=None):
            with self.assertRaises(SystemExit) as ctx:
                DRIVE.build_fixture_release(self.dest)
        self.assertIn("NO-DATA", str(ctx.exception))

    def test_empty_copy_is_nodata(self):
        with mock.patch.object(DRIVE.EP, "load_allowlist",
                                return_value=["x"]), \
             mock.patch.object(DRIVE.EP, "build_export_tree",
                                return_value=[]):
            with self.assertRaises(SystemExit) as ctx:
                DRIVE.build_fixture_release(self.dest)
        self.assertIn("NO-DATA", str(ctx.exception))

    def test_build_fixture_release_uses_add_dash_f(self):
        recorded = []

        def fake_run(cmd, cwd):
            recorded.append(cmd)
            return ""

        with mock.patch.object(DRIVE.EP, "load_allowlist",
                                return_value=["x"]), \
             mock.patch.object(DRIVE.EP, "build_export_tree",
                                return_value=["some/file.py"]), \
             mock.patch.object(DRIVE.RE, "manifest_from_dir",
                                return_value="deadbeef  some/file.py\n"), \
             mock.patch.object(DRIVE.RE, "manifest_digest",
                                return_value="deadbeef"), \
             mock.patch.object(DRIVE.RE, "manifest_path_for",
                                return_value="docs/releases/9.9.9.manifest"), \
             mock.patch.object(DRIVE, "run", side_effect=fake_run):
            count = DRIVE.build_fixture_release(self.dest)

        self.assertEqual(count, 1)
        add_commands = [c for c in recorded if c[:2] == ["git", "add"]]
        self.assertTrue(add_commands, "expected at least one 'git add' call")
        for cmd in add_commands:
            self.assertIn("-f", cmd,
                           "git add must carry -f, or the copied hub "
                           ".gitignore silently drops shipped files: %r"
                           % (cmd,))
        self.assertNotIn(["git", "add", "-A"], recorded)


class MainVerdict(unittest.TestCase):
    """main() -- drives build_fixture_release, EP.load_allowlist and
    reproduce_from through fast fakes (no git, no real subprocess) to prove
    the verdict arithmetic itself: PASS only when the clean tag reproduces
    (exit 0), the tampered tag is refused (exit 1) AND the clone's own
    allowlist is present. Each condition is flipped alone."""

    def _stub_fixture_build(self, dest):
        """Writes only the one file main() itself touches directly."""
        victim_dir = os.path.join(dest, "bundle", "runtime")
        os.makedirs(victim_dir, exist_ok=True)
        with open(os.path.join(victim_dir, "brother_run.py"), "w",
                  encoding="utf-8") as fh:
            fh.write("# fixture runtime\n")
        return 1

    def _run_main_with(self, allowlist, repro_results):
        """repro_results: list of (code, out) consumed in call order."""
        calls = iter(repro_results)
        with mock.patch.object(DRIVE, "build_fixture_release",
                                side_effect=self._stub_fixture_build), \
             mock.patch.object(DRIVE.EP, "load_allowlist",
                                return_value=allowlist), \
             mock.patch.object(DRIVE, "run", return_value=""), \
             mock.patch.object(DRIVE, "reproduce_from",
                                side_effect=lambda dest: next(calls)):
            return DRIVE.main()

    def test_all_three_conditions_true_is_pass(self):
        rc = self._run_main_with(["a", "b"], [(0, "clean-ok"), (1, "caught")])
        self.assertEqual(rc, 0)

    def test_clean_tag_failing_is_fail_even_if_tamper_caught(self):
        rc = self._run_main_with(["a"], [(1, "clean-broke"), (1, "caught")])
        self.assertEqual(rc, 1)

    def test_tamper_not_caught_is_fail_even_if_clean_passes(self):
        rc = self._run_main_with(["a"], [(0, "clean-ok"), (0, "not-caught")])
        self.assertEqual(rc, 1)

    def test_missing_allowlist_is_fail_even_if_both_codes_are_right(self):
        rc = self._run_main_with(None, [(0, "clean-ok"), (1, "caught")])
        self.assertEqual(rc, 1)

    def test_empty_allowlist_is_fail_even_if_both_codes_are_right(self):
        rc = self._run_main_with([], [(0, "clean-ok"), (1, "caught")])
        self.assertEqual(rc, 1)

    def test_temp_dir_is_always_cleaned_up(self):
        captured = {}
        real_mkdtemp = tempfile.mkdtemp

        def spying_mkdtemp(*a, **k):
            path = real_mkdtemp(*a, **k)
            captured["dest"] = path
            return path

        with mock.patch.object(DRIVE.tempfile, "mkdtemp",
                                side_effect=spying_mkdtemp):
            self._run_main_with(["a"], [(0, "ok"), (1, "caught")])
        self.assertIn("dest", captured)
        self.assertFalse(os.path.exists(captured["dest"]),
                          "main() must remove its temp dir in its finally block")


if __name__ == "__main__":
    unittest.main()

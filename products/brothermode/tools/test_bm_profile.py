#!/usr/bin/env python3
"""Tests for bm_profile, on tiny synthetic profile files under tempfile only,
never the real vault (BROTHERMODE_VAULT) and never anything under ~/.claude.

Run: python3 tools/test_bm_profile.py      (unittest output, exit 0 or 1)

The done-check this drives directly: a second session for the same person
and project reads a prior-session profile line and skips a previously asked
question (TwoSessionFact, TwoSessionPromotedPreference); the profile is
plain text the person can read and correct (ProfileIsPlainText,
CorrectionWins); a preference is promoted to a default after three repeats
and not before, driven both ways (PromotionNotYetAtTwo,
PromotionAtThreeRepeats).
"""
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
    _e100_sys.stderr.write(
        "tmp_sandbox absent: %s leaves its temp trees behind\n"
        % _e100_os.path.basename(__file__))

TOOL_DIR = os.path.dirname(os.path.abspath(__file__))
TOOL = os.path.join(TOOL_DIR, "bm_profile.py")


def run(argv):
    p = subprocess.run([sys.executable, TOOL] + argv,
                       stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    return p.returncode, (p.stdout + p.stderr).decode("utf-8", "replace")


def make_profile():
    tmp = tempfile.mkdtemp(prefix="bm-profile-")
    path = os.path.join(tmp, "vault", "10-Projects", "acme", "Profile.md")
    return tmp, path


class NoProfileYet(unittest.TestCase):
    """First session ever for this person and project: no file exists. Every
    verb reports NO-DATA and exits non-zero, never a crash."""

    def test_read_with_no_file_is_no_data(self):
        tmp, path = make_profile()
        self.addCleanup(shutil.rmtree, tmp, ignore_errors=True)
        code, out = run(["read", "--profile", path])
        self.assertEqual(code, 3, out)
        self.assertIn("NO-DATA", out, out)

    def test_promoted_with_no_file_is_no_data(self):
        tmp, path = make_profile()
        self.addCleanup(shutil.rmtree, tmp, ignore_errors=True)
        code, out = run(["promoted", "--profile", path, "--key", "preference: tone"])
        self.assertEqual(code, 3, out)
        self.assertIn("NO-DATA", out, out)


class TwoSessionFact(unittest.TestCase):
    """Session one records a stated fact (role); a second, separate process
    (a fresh session) reads the same file back and sees it right away, no
    repeat count needed. This is the seam start/SKILL.md names: a stated
    fact is read back next session as-is."""

    def test_role_recorded_once_is_read_back_by_a_second_process(self):
        tmp, path = make_profile()
        self.addCleanup(shutil.rmtree, tmp, ignore_errors=True)
        code, _ = run(["record", "--profile", path, "--key", "role", "--value", "analyst"])
        self.assertEqual(code, 0)
        # A brand new subprocess is the second session: nothing carries over
        # except the file on disk.
        code, out = run(["read", "--profile", path])
        self.assertEqual(code, 0, out)
        self.assertIn("role: analyst", out, out)


class PromotionNotYetAtTwo(unittest.TestCase):
    """Two repeats of the same preference must NOT promote: the done-check's
    "and not before" half."""

    def test_two_repeats_is_not_promoted(self):
        tmp, path = make_profile()
        self.addCleanup(shutil.rmtree, tmp, ignore_errors=True)
        run(["record", "--profile", path, "--key", "preference: tone", "--value", "formal"])
        run(["record", "--profile", path, "--key", "preference: tone", "--value", "formal"])
        code, out = run(["promoted", "--profile", path, "--key", "preference: tone"])
        self.assertEqual(code, 3, out)
        self.assertIn("NO-DATA", out, out)
        # read() must not surface an unpromoted preference either.
        code, out = run(["read", "--profile", path])
        self.assertEqual(code, 0, out)
        self.assertNotIn("preference: tone", out, out)


class PromotionAtThreeRepeats(unittest.TestCase):
    """Three repeats of the SAME value promotes: the done-check's "after
    three repeats" half, and the second-session skip it enables."""

    def test_three_repeats_promotes_and_a_second_session_sees_it(self):
        tmp, path = make_profile()
        self.addCleanup(shutil.rmtree, tmp, ignore_errors=True)
        for _ in range(3):
            run(["record", "--profile", path, "--key", "preference: tone", "--value", "formal"])
        code, out = run(["promoted", "--profile", path, "--key", "preference: tone"])
        self.assertEqual(code, 0, out)
        self.assertEqual(out.strip(), "formal", out)
        # A second, fresh process reading the same file sees the promoted default.
        code, out = run(["read", "--profile", path])
        self.assertEqual(code, 0, out)
        self.assertIn("preference: tone: formal", out, out)

    def test_a_different_value_each_time_never_promotes(self):
        tmp, path = make_profile()
        self.addCleanup(shutil.rmtree, tmp, ignore_errors=True)
        run(["record", "--profile", path, "--key", "preference: tone", "--value", "formal"])
        run(["record", "--profile", path, "--key", "preference: tone", "--value", "casual"])
        run(["record", "--profile", path, "--key", "preference: tone", "--value", "blunt"])
        code, out = run(["promoted", "--profile", path, "--key", "preference: tone"])
        self.assertEqual(code, 3, out)


class CorrectionWins(unittest.TestCase):
    """The person's own "correct: <key>: <value>" line beats a promoted
    default, however it was written: by hand in an editor, or through the
    same record verb this test uses for convenience."""

    def test_correction_overrides_a_promoted_default(self):
        tmp, path = make_profile()
        self.addCleanup(shutil.rmtree, tmp, ignore_errors=True)
        for _ in range(3):
            run(["record", "--profile", path, "--key", "preference: tone", "--value", "formal"])
        run(["record", "--profile", path, "--key", "correct: preference: tone", "--value", "casual"])
        code, out = run(["promoted", "--profile", path, "--key", "preference: tone"])
        self.assertEqual(code, 0, out)
        self.assertEqual(out.strip(), "casual", out)
        code, out = run(["read", "--profile", path])
        self.assertEqual(code, 0, out)
        self.assertIn("preference: tone: casual", out, out)
        self.assertNotIn("preference: tone: formal", out, out)

    def test_a_hand_written_correction_line_is_read_the_same_way(self):
        """The person edits the file directly, no CLI call at all: this is the
        actual correction path, plain text in a text editor."""
        tmp, path = make_profile()
        self.addCleanup(shutil.rmtree, tmp, ignore_errors=True)
        run(["record", "--profile", path, "--key", "role", "--value", "dev"])
        with open(path, "a", encoding="utf-8") as f:
            f.write("2026-09-05: correct: role: analyst\n")
        code, out = run(["read", "--profile", path])
        self.assertEqual(code, 0, out)
        self.assertIn("role: analyst", out, out)
        self.assertNotIn("role: dev", out, out)


class NeverRewritesAnExistingLine(unittest.TestCase):
    """Append-only, per the vault constitution: every prior line stays on
    disk, byte for byte, after a later record()."""

    def test_earlier_lines_survive_a_later_record(self):
        tmp, path = make_profile()
        self.addCleanup(shutil.rmtree, tmp, ignore_errors=True)
        run(["record", "--profile", path, "--key", "role", "--value", "analyst"])
        with open(path, encoding="utf-8") as f:
            before = f.read()
        run(["record", "--profile", path, "--key", "level", "--value", "beginner"])
        with open(path, encoding="utf-8") as f:
            after = f.read()
        self.assertTrue(after.startswith(before), (before, after))


class ProfileIsPlainText(unittest.TestCase):
    """No frontmatter, no JSON: a non-engineer opens the file and reads why a
    question would be skipped."""

    def test_the_file_on_disk_is_plain_dated_lines(self):
        tmp, path = make_profile()
        self.addCleanup(shutil.rmtree, tmp, ignore_errors=True)
        run(["record", "--profile", path, "--key", "role", "--value", "analyst"])
        with open(path, encoding="utf-8") as f:
            text = f.read()
        self.assertNotIn("---", text, text)
        self.assertNotIn("{", text, text)
        self.assertIn("role: analyst", text, text)


class VaultAndProjectConvenience(unittest.TestCase):
    """--vault and --project resolve to 10-Projects/<slug>/Profile.md without
    the caller building the path by hand, matching bm_vault_catalog.py's own
    per-project path shape."""

    def test_vault_and_project_flags_resolve_the_same_path(self):
        tmp = tempfile.mkdtemp(prefix="bm-profile-")
        self.addCleanup(shutil.rmtree, tmp, ignore_errors=True)
        vault = os.path.join(tmp, "vault")
        code, _ = run(["record", "--vault", vault, "--project", "acme",
                       "--key", "role", "--value", "analyst"])
        self.assertEqual(code, 0)
        expected = os.path.join(vault, "10-Projects", "acme", "Profile.md")
        self.assertTrue(os.path.isfile(expected))
        code, out = run(["read", "--vault", vault, "--project", "acme"])
        self.assertEqual(code, 0, out)
        self.assertIn("role: analyst", out, out)


if __name__ == "__main__":
    unittest.main(verbosity=1)

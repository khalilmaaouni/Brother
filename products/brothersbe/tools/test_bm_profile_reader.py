#!/usr/bin/env python3
"""Tests for bm_profile_reader, BrotherSBE's own standalone reader of the
shared vault profile file (see bm_profile_reader.py's docstring and the ADR
it cites for why this is a second implementation rather than an import).

Fixtures are built with brothermode's OWN tools/bm_profile.py `record()`, so
the file on disk is genuinely produced by the format's own writer, never a
hand-typed guess. Importing bm_profile.py is allowed HERE ONLY, to build
fixtures and cross-check answers: a test comparing two implementations of one
file format is not the ADR's "one product running the other's tools in
production" case. bm_profile_reader.py itself never imports bm_profile.py.

Run: python3 tools/test_bm_profile_reader.py      (unittest output, exit 0 or 1)

The done-check this drives directly (TwoSessionSkipsAQuestion): a second
session for the same person and project reads a prior-session profile line,
through BrotherSBE's own reader, and the question that fact answers is
skipped.
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
READER_TOOL = os.path.join(TOOL_DIR, "bm_profile_reader.py")

# Test-only cross-product import: brothermode's own writer/reference reader,
# used here solely to build real fixtures and cross-check this product's
# reader. bm_profile_reader.py (the production file) never does this.
BM_TOOLS_DIR = os.path.normpath(os.path.join(TOOL_DIR, "..", "..", "brothermode", "tools"))
sys.path.insert(0, BM_TOOLS_DIR)
import bm_profile as writer  # noqa: E402

sys.path.insert(0, TOOL_DIR)
import bm_profile_reader as reader  # noqa: E402


def make_profile():
    tmp = tempfile.mkdtemp(prefix="bm-profile-reader-")
    path = os.path.join(tmp, "vault", "10-Projects", "acme", "Profile.md")
    return tmp, path


def run_reader_cli(argv):
    """Invoke bm_profile_reader.py as a fresh subprocess: a real second
    session shares nothing but the file on disk."""
    p = subprocess.run([sys.executable, READER_TOOL] + argv,
                       stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    return p.returncode, (p.stdout + p.stderr).decode("utf-8", "replace")


class RawValueAgreesWithTheWriter(unittest.TestCase):
    """Case 1 of 3: a plain stated fact, no promotion involved. The writer's
    record() lays down one line; both readers must agree on it."""

    def test_role_recorded_once_matches_bm_profiles_own_read(self):
        tmp, path = make_profile()
        self.addCleanup(shutil.rmtree, tmp, ignore_errors=True)
        writer.record(path, "role", "analyst")
        self.assertEqual(reader.read(path), writer.read(path))
        self.assertEqual(reader.read(path), {"role": "analyst"})


class PromotedAfterThreeRepeatsAgreesWithTheWriter(unittest.TestCase):
    """Case 2 of 3: a preference promotes to a default only after the SAME
    value is recorded three times, and not before. Both readers must agree
    at two repeats (not promoted) and at three (promoted)."""

    def test_two_repeats_neither_reader_promotes(self):
        tmp, path = make_profile()
        self.addCleanup(shutil.rmtree, tmp, ignore_errors=True)
        writer.record(path, "preference: tone", "formal")
        writer.record(path, "preference: tone", "formal")
        self.assertIsNone(writer.promoted(path, "preference: tone"))
        self.assertIsNone(reader.promoted(path, "preference: tone"))
        self.assertEqual(reader.read(path), writer.read(path))
        self.assertEqual(reader.read(path), {})

    def test_three_repeats_both_readers_promote_the_same_value(self):
        tmp, path = make_profile()
        self.addCleanup(shutil.rmtree, tmp, ignore_errors=True)
        for _ in range(3):
            writer.record(path, "preference: tone", "formal")
        self.assertEqual(writer.promoted(path, "preference: tone"), "formal")
        self.assertEqual(reader.promoted(path, "preference: tone"), "formal")
        self.assertEqual(reader.read(path), writer.read(path))
        self.assertEqual(reader.read(path), {"preference: tone": "formal"})


class CorrectionAgreesWithTheWriter(unittest.TestCase):
    """Case 3 of 3: a hand written "correct: <key>: <value>" line beats both
    the raw latest value and a promoted default, for both readers."""

    def test_correction_beats_a_promoted_default_for_both_readers(self):
        tmp, path = make_profile()
        self.addCleanup(shutil.rmtree, tmp, ignore_errors=True)
        for _ in range(3):
            writer.record(path, "preference: tone", "formal")
        writer.record(path, "correct: preference: tone", "casual")
        self.assertEqual(writer.promoted(path, "preference: tone"), "casual")
        self.assertEqual(reader.promoted(path, "preference: tone"), "casual")
        self.assertEqual(reader.read(path), writer.read(path))
        self.assertEqual(reader.read(path), {"preference: tone": "casual"})

    def test_correction_beats_a_raw_latest_value_for_both_readers(self):
        tmp, path = make_profile()
        self.addCleanup(shutil.rmtree, tmp, ignore_errors=True)
        writer.record(path, "role", "dev")
        with open(path, "a", encoding="utf-8") as f:
            f.write("2026-09-05: correct: role: analyst\n")
        self.assertEqual(writer.read(path), {"role": "analyst"})
        self.assertEqual(reader.read(path), {"role": "analyst"})


class ReaderNeverImportsTheWriter(unittest.TestCase):
    """The ADR's own rule, checked mechanically rather than trusted: the
    production reader file must hold no import statement naming bm_profile.
    Checked over the parsed AST, not a text substring, since the file's own
    docstring legitimately mentions "bm_profile.py" in prose."""

    def test_production_file_has_no_bm_profile_import(self):
        import ast
        path = os.path.join(TOOL_DIR, "bm_profile_reader.py")
        with open(path, encoding="utf-8") as f:
            tree = ast.parse(f.read(), filename=path)
        names = []
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                names.extend(a.name for a in node.names)
            elif isinstance(node, ast.ImportFrom):
                if node.module:
                    names.append(node.module)
        self.assertFalse(
            [n for n in names if "bm_profile" in n],
            "bm_profile_reader.py must never import bm_profile: found %r" % names)


class TwoSessionSkipsAQuestion(unittest.TestCase):
    """THE DONE-CHECK: a second session for the same person and project reads
    a prior-session profile line, through BrotherSBE's own reader, and skips
    a question that fact already answered.

    Session one: BrotherMode's start flow records a fact (role) using its
    own writer. Session two: a brand new process invokes BrotherSBE's
    bm_profile_reader.py CLI (never bm_profile.py), the same way
    products/brothersbe/skills/start/SKILL.md now does before its first
    question. If the reader answers the role question, that question is
    skipped; the assertion below is the same test SKILL.md's own logic runs.
    """

    def test_a_second_process_skips_the_role_question(self):
        tmp, path = make_profile()
        self.addCleanup(shutil.rmtree, tmp, ignore_errors=True)

        # Session one, BrotherMode's own writer: no file exists yet, so the
        # question would have been asked once, then recorded.
        code, out = run_reader_cli(["read", "--profile", path])
        self.assertEqual(code, 3, out)
        self.assertIn("NO-DATA", out, out)
        question_asked_session_one = "NO-DATA" in out
        self.assertTrue(question_asked_session_one)
        writer.record(path, "role", "analyst")

        # Session two, a fresh subprocess sharing nothing but the file:
        # BrotherSBE's start skill runs exactly this command before asking.
        code, out = run_reader_cli(["read", "--profile", path])
        self.assertEqual(code, 0, out)
        self.assertIn("role: analyst", out, out)
        question_skipped_session_two = "role: analyst" in out
        self.assertTrue(question_skipped_session_two,
                         "the role question must be skipped once the profile answers it")

    def test_a_promoted_preference_is_also_skipped_on_the_second_session(self):
        tmp, path = make_profile()
        self.addCleanup(shutil.rmtree, tmp, ignore_errors=True)
        for _ in range(3):
            writer.record(path, "preference: tone", "formal")
        code, out = run_reader_cli(["promoted", "--profile", path, "--key", "preference: tone"])
        self.assertEqual(code, 0, out)
        self.assertEqual(out.strip(), "formal", out)


if __name__ == "__main__":
    unittest.main(verbosity=1)

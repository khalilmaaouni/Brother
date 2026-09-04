#!/usr/bin/env python3
"""Tests for scripts/vault_correct.py (row V13). Every fixture lives under
a fresh tempfile.TemporaryDirectory, never the real vault: each test class
asserts its own root sits under tempfile.gettempdir() before touching it,
so a bug that ever pointed this tool at the real vault fails loudly here
first."""

import io
import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from vault_correct import run, find_note, has_dash

FIXTURE_NOTE = """---
type: failure
project: brother
created: 2026-08-01
status: open
tags: [testing]
verified-by: "a real run"
symptom: "it looked fine and then it was not"
---

# A wrong lesson

## What happened

Something that turned out to be wrong.
"""


class VaultCorrectTestBase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="vault-correct-test-")
        self.vault = self.tmp.name
        # E100 self-check: never let this suite run against a real vault.
        self.assertTrue(
            os.path.commonpath([os.path.realpath(self.vault),
                                 os.path.realpath(tempfile.gettempdir())])
            == os.path.realpath(tempfile.gettempdir()),
            "fixture vault %r must live under the system temp dir" % self.vault)
        self.failures_dir = os.path.join(self.vault, "40-Failures")
        os.makedirs(self.failures_dir)
        self.note_path = os.path.join(self.failures_dir, "a-wrong-lesson.md")
        with open(self.note_path, "w", encoding="utf-8") as f:
            f.write(FIXTURE_NOTE)

    def tearDown(self):
        self.tmp.cleanup()

    def call(self, slug, sentence, supersedes=None):
        out, err = io.StringIO(), io.StringIO()
        code = run(self.vault, slug, sentence, supersedes=supersedes,
                   today="2026-09-05", out=out, err=err)
        return code, out.getvalue(), err.getvalue()

    def read_note(self):
        with open(self.note_path, encoding="utf-8") as f:
            return f.read()


class TestHasDash(unittest.TestCase):
    def test_plain_hyphen_is_a_dash(self):
        self.assertTrue(has_dash("a nine-hour outage"))

    def test_em_and_en_dash_are_dashes(self):
        self.assertTrue(has_dash("wrong" + "\u2014" + "fix it"))
        self.assertTrue(has_dash("wrong" + "\u2013" + "fix it"))

    def test_a_dashless_sentence_passes(self):
        self.assertFalse(has_dash("the claim was measured wrong that day"))


class TestFindNote(VaultCorrectTestBase):
    def test_finds_a_note_nested_under_the_vault_root(self):
        self.assertEqual(find_note(self.vault, "a-wrong-lesson"), self.note_path)

    def test_missing_note_returns_none(self):
        self.assertIsNone(find_note(self.vault, "no-such-note"))


class TestAppendCorrection(VaultCorrectTestBase):
    def test_append_on_a_fixture_note(self):
        original = self.read_note()
        code, out, err = self.call("a-wrong-lesson",
                                    "the claim was measured wrong that day")
        self.assertEqual(code, 0, err)
        self.assertIn(self.note_path, out)
        self.assertIn("## Correction 2026-09-05", out)

        updated = self.read_note()
        self.assertTrue(updated.startswith(original[:original.index("status: open")]))
        self.assertIn("status: corrected", updated)
        self.assertIn("corrected_at: 2026-09-05", updated)
        self.assertIn("## Correction 2026-09-05", updated)
        self.assertIn("the claim was measured wrong that day", updated)

    def test_body_text_is_unchanged_byte_for_byte_above_the_appended_block(self):
        original = self.read_note()
        self.call("a-wrong-lesson", "the claim was measured wrong that day")
        updated = self.read_note()
        marker = "\n## Correction 2026-09-05\n"
        self.assertIn(marker, updated)
        above = updated[:updated.index(marker)]
        # The body (everything after the frontmatter's closing ---) must be
        # byte for byte the original body; only the frontmatter's status
        # and corrected_at lines may have changed.
        original_body = original.split("---\n", 2)[2]
        above_body = above.split("---\n", 2)[2]
        self.assertEqual(above_body, original_body)

    def test_second_correction_appends_a_second_block(self):
        self.call("a-wrong-lesson", "first correction sentence")
        code, out, err = self.call("a-wrong-lesson", "second correction sentence")
        self.assertEqual(code, 0, err)
        updated = self.read_note()
        self.assertEqual(updated.count("## Correction"), 2)
        self.assertIn("first correction sentence", updated)
        self.assertIn("second correction sentence", updated)
        # corrected_at reflects the latest correction, not the first.
        self.assertEqual(updated.count("corrected_at:"), 1)

    def test_supersedes_is_recorded_in_the_appended_block(self):
        code, out, err = self.call(
            "a-wrong-lesson", "the real number is 42",
            supersedes="the number was 41")
        self.assertEqual(code, 0, err)
        self.assertIn("Supersedes: the number was 41", self.read_note())


class TestRefusals(VaultCorrectTestBase):
    def test_missing_note_exits_2(self):
        code, out, err = self.call("no-such-slug", "a fine sentence")
        self.assertEqual(code, 2)
        self.assertIn("NO-DATA", err)
        self.assertIn("no-such-slug", err)

    def test_dash_is_refused(self):
        before = self.read_note()
        code, out, err = self.call("a-wrong-lesson", "a nine-hour outage")
        self.assertEqual(code, 2)
        self.assertIn("NO-DATA", err)
        self.assertIn("dash", err)
        self.assertEqual(self.read_note(), before)  # refused: nothing written

    def test_empty_sentence_is_refused(self):
        code, out, err = self.call("a-wrong-lesson", "   ")
        self.assertEqual(code, 2)
        self.assertIn("NO-DATA", err)


class TestFailuresIndex(VaultCorrectTestBase):
    def test_appends_one_routing_line_when_index_exists_and_lacks_the_slug(self):
        index_path = os.path.join(self.failures_dir, "Failures-Index.md")
        with open(index_path, "w", encoding="utf-8") as f:
            f.write("# Failures Index\n\n## Existing\n- [[some-other-note]] old line.\n")
        self.call("a-wrong-lesson", "the claim was measured wrong that day")
        with open(index_path, encoding="utf-8") as f:
            updated = f.read()
        self.assertIn("[[a-wrong-lesson]]", updated)
        self.assertIn("[[some-other-note]] old line.", updated)  # untouched

    def test_a_second_correction_does_not_duplicate_the_index_line(self):
        index_path = os.path.join(self.failures_dir, "Failures-Index.md")
        with open(index_path, "w", encoding="utf-8") as f:
            f.write("# Failures Index\n")
        self.call("a-wrong-lesson", "first correction sentence")
        self.call("a-wrong-lesson", "second correction sentence")
        with open(index_path, encoding="utf-8") as f:
            updated = f.read()
        self.assertEqual(updated.count("[[a-wrong-lesson]]"), 1)

    def test_no_index_file_is_a_quiet_no_op_not_an_error(self):
        code, out, err = self.call("a-wrong-lesson",
                                    "the claim was measured wrong that day")
        self.assertEqual(code, 0, err)
        self.assertFalse(
            os.path.isfile(os.path.join(self.failures_dir, "Failures-Index.md")))


class TestMain(unittest.TestCase):
    """CLI wiring, exercised once so argparse's own plumbing is proven too."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="vault-correct-cli-test-")
        self.vault = self.tmp.name
        self.assertTrue(self.vault.startswith(tempfile.gettempdir()))
        os.makedirs(os.path.join(self.vault, "50-Reference"))
        self.note_path = os.path.join(self.vault, "50-Reference", "a-note.md")
        with open(self.note_path, "w", encoding="utf-8") as f:
            f.write(FIXTURE_NOTE)

    def tearDown(self):
        self.tmp.cleanup()

    def test_main_exits_0_and_writes_the_note(self):
        from vault_correct import main
        code = main(["--vault", self.vault, "--note", "a-note",
                     "the claim was measured wrong that day"])
        self.assertEqual(code, 0)
        with open(self.note_path, encoding="utf-8") as f:
            self.assertIn("the claim was measured wrong that day", f.read())


if __name__ == "__main__":
    unittest.main()

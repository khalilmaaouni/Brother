#!/usr/bin/env python3
"""Tests for scripts/vault_correct.py (row V13). Every fixture lives under
a fresh tempfile.TemporaryDirectory, never the real vault: each test class
asserts its own root sits under tempfile.gettempdir() before touching it,
so a bug that ever pointed this tool at the real vault fails loudly here
first."""

import io
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from datetime import datetime, timezone

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
from vault_correct import run, find_note, has_dash, gate_text  # noqa: E402

# E100: one sandbox for every temp tree this process makes, removed at exit.
import os as _e100_os, sys as _e100_sys  # noqa: E402
_e100_sys.path.append(_e100_os.path.join(
    _e100_os.path.dirname(_e100_os.path.abspath(__file__)), '.'))
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

BM_VAULT = os.path.join(HERE, "..", "products", "brothermode", "tools", "bm_vault.py")

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

NO_FRONTMATTER_NOTE = "Just a stray note with no frontmatter at all.\n"


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

    def call(self, slug, sentence, supersedes=None, gate=gate_text):
        out, err = io.StringIO(), io.StringIO()
        code = run(self.vault, slug, sentence, supersedes=supersedes,
                   today="2026-09-05", out=out, err=err, gate=gate)
        return code, out.getvalue(), err.getvalue()

    def read_note(self, path=None):
        with open(path or self.note_path, encoding="utf-8") as f:
            return f.read()

    def new_note_path(self, suffix=""):
        return os.path.join(self.failures_dir, "a-wrong-lesson-correction-2026-09-05%s.md" % suffix)


def allow(text, deny_list_path):
    return True, None


def refuse(text, deny_list_path):
    return False, "class=deny-list-term"


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


class ItRoutesThroughTheSameAdmissionGateAdmitUses(VaultCorrectTestBase):
    """The correction sentence must clear bm_vault_intake.hard_gate, the
    same gate `admit` and `capture` run, before a superseding note is
    written -- never a direct ungated write."""

    def test_a_credential_shaped_sentence_is_refused_and_nothing_is_written(self):
        code, out, err = self.call(
            "a-wrong-lesson", "AKIA" + "ABCDEFGHIJKLMNOP" + " was live")
        self.assertEqual(code, 2)
        self.assertIn("NO-DATA", err)
        self.assertIn("admission gate", err)
        self.assertEqual(os.listdir(self.failures_dir), ["a-wrong-lesson.md"])

    def test_an_injected_gate_refusal_writes_nothing_driven_backwards(self):
        code, out, err = self.call("a-wrong-lesson", "a perfectly clean sentence",
                                    gate=refuse)
        self.assertEqual(code, 2)
        self.assertIn("NO-DATA", err)
        self.assertEqual(os.listdir(self.failures_dir), ["a-wrong-lesson.md"])

    def test_an_injected_gate_that_passes_still_writes(self):
        code, out, err = self.call("a-wrong-lesson", "a perfectly clean sentence",
                                    gate=allow)
        self.assertEqual(code, 0, err)
        self.assertTrue(os.path.exists(self.new_note_path()))

    def test_a_gate_that_cannot_load_fails_closed(self):
        ok, reason = gate_text("anything", loader=lambda: None)
        self.assertFalse(ok)
        self.assertIn("NO-DATA", reason)


class TestSupersedingCorrection(VaultCorrectTestBase):
    """Row V13's done_check: a superseding note, never an edit of the old
    body."""

    def test_a_new_note_is_written_beside_the_old_one(self):
        original = self.read_note()
        code, out, err = self.call("a-wrong-lesson",
                                    "the claim was measured wrong that day")
        self.assertEqual(code, 0, err)
        new_path = self.new_note_path()
        self.assertIn(new_path, out)
        self.assertIn(self.note_path, out)  # names what it supersedes too
        self.assertTrue(os.path.exists(new_path))

        new_body = self.read_note(new_path)
        self.assertIn("supersedes: [[a-wrong-lesson]]", new_body)
        self.assertIn("type: correction", new_body)
        self.assertIn("the claim was measured wrong that day", new_body)
        # and the old note is untouched, still on disk
        self.assertTrue(os.path.exists(self.note_path))
        self.assertNotEqual(self.read_note(), original)  # frontmatter did change

    def test_old_note_body_is_unchanged_byte_for_byte(self):
        original = self.read_note()
        original_body = original.split("---\n", 2)[2]
        self.call("a-wrong-lesson", "the claim was measured wrong that day")
        updated_body = self.read_note().split("---\n", 2)[2]
        self.assertEqual(updated_body, original_body)

    def test_old_note_frontmatter_gets_the_reverse_link(self):
        self.call("a-wrong-lesson", "the claim was measured wrong that day")
        updated = self.read_note()
        self.assertIn("status: corrected", updated)
        self.assertIn("corrected_at: 2026-09-05", updated)
        self.assertIn("superseded_by: [[a-wrong-lesson-correction-2026-09-05]]", updated)

    def test_supersedes_text_is_recorded_in_the_new_note(self):
        code, out, err = self.call(
            "a-wrong-lesson", "the real number is 42",
            supersedes="the number was 41")
        self.assertEqual(code, 0, err)
        self.assertIn("Supersedes: the number was 41", self.read_note(self.new_note_path()))

    def test_a_second_correction_writes_a_second_note_not_a_second_edit(self):
        self.call("a-wrong-lesson", "first correction sentence")
        code, out, err = self.call("a-wrong-lesson", "second correction sentence")
        self.assertEqual(code, 0, err)
        first_new = self.new_note_path()
        second_new = self.new_note_path("-2")
        self.assertTrue(os.path.exists(first_new))
        self.assertTrue(os.path.exists(second_new))
        self.assertIn("first correction sentence", self.read_note(first_new))
        self.assertIn("second correction sentence", self.read_note(second_new))
        # the old note's reverse pointer names the latest correction
        old_updated = self.read_note()
        self.assertIn("superseded_by: [[a-wrong-lesson-correction-2026-09-05-2]]", old_updated)
        self.assertNotIn("superseded_by: [[a-wrong-lesson-correction-2026-09-05]]\n", old_updated)

    def test_a_note_with_no_frontmatter_still_gets_a_superseding_note(self):
        no_fm_path = os.path.join(self.failures_dir, "no-frontmatter.md")
        with open(no_fm_path, "w", encoding="utf-8") as f:
            f.write(NO_FRONTMATTER_NOTE)
        code, out, err = self.call("no-frontmatter", "this old note was wrong")
        self.assertEqual(code, 0, err)
        self.assertIn("NOTE:", out)
        self.assertIn("no frontmatter", out)
        # and the note itself is completely untouched
        with open(no_fm_path, encoding="utf-8") as f:
            self.assertEqual(f.read(), NO_FRONTMATTER_NOTE)


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
        self.assertEqual(os.listdir(self.failures_dir), ["a-wrong-lesson.md"])

    def test_empty_sentence_is_refused(self):
        code, out, err = self.call("a-wrong-lesson", "   ")
        self.assertEqual(code, 2)
        self.assertIn("NO-DATA", err)


class TestFailuresIndex(VaultCorrectTestBase):
    def test_appends_one_routing_line_for_the_new_note_when_index_exists(self):
        index_path = os.path.join(self.failures_dir, "Failures-Index.md")
        with open(index_path, "w", encoding="utf-8") as f:
            f.write("# Failures Index\n\n## Existing\n- [[some-other-note]] old line.\n")
        self.call("a-wrong-lesson", "the claim was measured wrong that day")
        with open(index_path, encoding="utf-8") as f:
            updated = f.read()
        self.assertIn("[[a-wrong-lesson-correction-2026-09-05]]", updated)
        self.assertIn("[[some-other-note]] old line.", updated)  # untouched

    def test_a_second_correction_adds_its_own_index_line_not_a_duplicate(self):
        index_path = os.path.join(self.failures_dir, "Failures-Index.md")
        with open(index_path, "w", encoding="utf-8") as f:
            f.write("# Failures Index\n")
        self.call("a-wrong-lesson", "first correction sentence")
        self.call("a-wrong-lesson", "second correction sentence")
        with open(index_path, encoding="utf-8") as f:
            updated = f.read()
        self.assertIn("[[a-wrong-lesson-correction-2026-09-05]]", updated)
        self.assertIn("[[a-wrong-lesson-correction-2026-09-05-2]]", updated)

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

    def test_main_exits_0_and_writes_a_new_superseding_note(self):
        from vault_correct import main
        code = main(["--vault", self.vault, "--note", "a-note",
                     "the claim was measured wrong that day"])
        self.assertEqual(code, 0)
        today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        new_path = os.path.join(self.vault, "50-Reference",
                                 "a-note-correction-%s.md" % today)
        with open(new_path, encoding="utf-8") as f:
            self.assertIn("the claim was measured wrong that day", f.read())
        with open(self.note_path, encoding="utf-8") as f:
            self.assertIn("status: corrected", f.read())


def bm_vault_run(argv, env):
    p = subprocess.run([sys.executable, BM_VAULT] + argv, env=env,
                        stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    return p.returncode, (p.stdout + p.stderr).decode("utf-8", "replace")


@unittest.skipUnless(os.path.isfile(BM_VAULT), "products/brothermode/tools/bm_vault.py not found")
class ACorrectedNoteIsWithheldFromRecallWithItsReason(unittest.TestCase):
    """End to end proof of row V13's whole done_check, on a fresh temp vault:
    run vault_correct.py's real CLI to supersede a note, index that vault
    with the real bm_vault.py, then check that the OLD note is withheld and
    the new one is served. products/brothermode/tools/bm_vault.py already
    withholds a superseded note (test_bm_vault.py's
    ASupersededLessonIsNotServedAsCurrent, work package 16); this test
    proves vault_correct.py's own output is in the shape that mechanism
    reads, not just that vault_correct.py wrote something."""

    CITING_NOTE = """---
type: failure
status: open
created: 2026-01-01
description: "the old ruling about quibblewax handling"
---

# the old quibblewax ruling

Always flarn the quibblewax before serving. See ZorbleWidget.swift.
"""

    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.mkdtemp(prefix="bm-vault-correct-e2e-")
        cls.vault = os.path.join(cls.tmp, "vault")
        cls.failures = os.path.join(cls.vault, "40-Failures")
        os.makedirs(cls.failures)
        with open(os.path.join(cls.failures, "old-quibblewax.md"), "w") as f:
            f.write(cls.CITING_NOTE)
        cls.code_dir = os.path.join(cls.tmp, "code")
        os.makedirs(cls.code_dir)
        with open(os.path.join(cls.code_dir, "ZorbleWidget.swift"), "w") as f:
            f.write("// stub so the citation resolves and freshness is not the variable\n")
        cls.env = dict(os.environ)
        cls.env["HOME"] = cls.tmp
        cls.env["BROTHERMODE_ROOT"] = cls.tmp
        cls.env["BM_FRESHNESS_ROOTS"] = cls.code_dir
        cls.env["BM_FRESHNESS_STATE"] = os.path.join(cls.tmp, "freshness_state.sqlite3")
        os.makedirs(os.path.join(cls.tmp, ".claude"))

        correct_proc = subprocess.run(
            [sys.executable, os.path.join(HERE, "vault_correct.py"),
             "--vault", cls.vault, "--note", "old-quibblewax",
             "the old quibblewax ruling was measured wrong"],
            env=cls.env, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        cls.correct_code = correct_proc.returncode
        cls.correct_out = (correct_proc.stdout + correct_proc.stderr).decode("utf-8", "replace")

        cls.index_code, cls.index_out = bm_vault_run(["index", "--vault", cls.vault], cls.env)

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.tmp, ignore_errors=True)

    def test_01_vault_correct_exited_clean(self):
        self.assertEqual(self.correct_code, 0, self.correct_out)

    def test_02_the_corpus_indexed(self):
        self.assertEqual(self.index_code, 0, self.index_out)

    def test_03_the_old_note_is_withheld_and_names_its_successor(self):
        code, out = bm_vault_run(["check", "--paths", "ZorbleWidget.swift", "--limit", "5"],
                                  self.env)
        self.assertEqual(code, 0, out)
        self.assertIn("WITHHELD (superseded)", out,
                      "the corrected note was served as an ordinary current "
                      "result:\n%s" % out[:900])
        self.assertIn("superseded by", out)

    def test_04_the_old_note_is_still_on_disk(self):
        """Withheld, never deleted."""
        self.assertTrue(os.path.exists(os.path.join(self.failures, "old-quibblewax.md")))


if __name__ == "__main__":
    unittest.main()

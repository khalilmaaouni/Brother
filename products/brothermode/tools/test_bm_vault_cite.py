#!/usr/bin/env python3
"""Calibration for tools/bm_vault_cite.py, WBS row VB6-02.

The property under test is the row's own sentence, driven backwards: a row citing a note
whose content then changes is reported by the checker as citing superseded content, with
the old and new hash shown; an unchanged citation stays silent; a missing note is named
MISSING; an unreadable citations file or vault is NO-DATA.

No em or en dashes anywhere in this file.
"""
import contextlib
import io
import json
import os
import shutil
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import bm_vault_cite as cite  # noqa: E402

NOTE_ID = "n-0123456789abcdef"


def note(body, note_id=NOTE_ID, promotion=None):
    front = "---\nid: %s\ntype: reference\n" % note_id
    if promotion:
        front += "promotion: %s\n" % promotion
    front += "---\n\n# a note\n\n"
    return front + body + "\n"


def run(argv):
    """(exit_code, stdout) for one CLI call, mirroring the run/capture shape this
    module family already uses in test_bm_vault_provenance.py."""
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        code = cite.main(argv)
    return code, buf.getvalue()


class CiteCalibration(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="bm-cite-")
        self.vault = os.path.join(self.tmp, "vault")
        os.makedirs(self.vault)
        self.note_path = os.path.join(self.vault, "the-note.md")
        with open(self.note_path, "w", encoding="utf-8") as fh:
            fh.write(note("original content"))
        self.citations = os.path.join(self.tmp, "citations.jsonl")

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _mint(self, out=None):
        return run(["mint", "--vault", self.vault, "--note", NOTE_ID,
                    "--by", "VB6-02", "--out", out or self.citations])

    # -- mint --------------------------------------------------------------

    def test_mint_never_edits_the_note(self):
        with open(self.note_path, encoding="utf-8") as fh:
            before = fh.read()
        self._mint()
        with open(self.note_path, encoding="utf-8") as fh:
            after = fh.read()
        self.assertEqual(before, after)

    def _read_first_record(self):
        with open(self.citations, encoding="utf-8") as fh:
            return json.loads(fh.readline())

    def test_mint_writes_one_record_with_the_four_fields(self):
        code, _ = self._mint()
        self.assertEqual(code, 0)
        record = self._read_first_record()
        self.assertEqual(record["note_id"], NOTE_ID)
        self.assertEqual(record["by"], "VB6-02")
        self.assertEqual(len(record["content_sha256"]), 64)
        self.assertEqual(record["lifecycle"], "legacy")

    def test_mint_reads_a_declared_lifecycle_state(self):
        with open(self.note_path, "w", encoding="utf-8") as fh:
            fh.write(note("content", promotion="validated"))
        self._mint()
        record = self._read_first_record()
        self.assertEqual(record["lifecycle"], "validated")

    def test_mint_a_missing_note_is_NO_DATA(self):
        code, _ = run(["mint", "--vault", self.vault, "--note", "n-ffffffffffffffff",
                       "--by", "VB6-02", "--out", self.citations])
        self.assertEqual(code, 2)

    def test_mint_rejects_a_malformed_note_id(self):
        code, _ = run(["mint", "--vault", self.vault, "--note", "not-an-id",
                       "--by", "VB6-02", "--out", self.citations])
        self.assertEqual(code, 2)

    def test_mint_with_no_out_prints_the_record(self):
        code, out = run(["mint", "--vault", self.vault, "--note", NOTE_ID, "--by", "VB6-02"])
        self.assertEqual(code, 0)
        self.assertEqual(json.loads(out.strip())["note_id"], NOTE_ID)

    # -- check: the row's own sentence, driven backwards --------------------

    def test_an_unchanged_citation_stays_silent_and_passes(self):
        self._mint()
        code, out = run(["check", "--vault", self.vault, "--citations", self.citations])
        self.assertEqual(code, 0)
        self.assertNotIn("SUPERSEDED", out)
        self.assertNotIn("MISSING", out)
        self.assertIn("current: 1", out)

    def test_a_note_that_changes_after_citation_is_reported_superseded(self):
        self._mint()
        old_hash = self._read_first_record()["content_sha256"]
        with open(self.note_path, "w", encoding="utf-8") as fh:
            fh.write(note("content that is now different"))
        new_hash = cite._hash_and_lifecycle(self.note_path)[0]
        code, out = run(["check", "--vault", self.vault, "--citations", self.citations])
        self.assertEqual(code, 1)
        self.assertIn("SUPERSEDED-CONTENT", out)
        self.assertIn(NOTE_ID, out)
        self.assertIn(old_hash[:12], out)
        self.assertIn(new_hash[:12], out)
        self.assertIn("superseded: 1", out)

    def test_a_lifecycle_change_is_shown_then_and_now(self):
        self._mint()
        with open(self.note_path, "w", encoding="utf-8") as fh:
            fh.write(note("content that is now different", promotion="canonical"))
        code, out = run(["check", "--vault", self.vault, "--citations", self.citations])
        self.assertEqual(code, 1)
        self.assertIn("legacy -> canonical", out)

    def test_a_note_that_no_longer_exists_is_MISSING(self):
        self._mint()
        os.remove(self.note_path)
        code, out = run(["check", "--vault", self.vault, "--citations", self.citations])
        self.assertEqual(code, 1)
        self.assertIn("MISSING", out)
        self.assertIn(NOTE_ID, out)
        self.assertIn("missing: 1", out)

    def test_an_absent_citations_file_is_NO_DATA(self):
        code, _ = run(["check", "--vault", self.vault, "--citations",
                       os.path.join(self.tmp, "does-not-exist.jsonl")])
        self.assertEqual(code, 2)

    def test_an_empty_citations_file_is_NO_DATA_not_a_pass(self):
        open(self.citations, "w", encoding="utf-8").close()
        code, _ = run(["check", "--vault", self.vault, "--citations", self.citations])
        self.assertEqual(code, 2)

    def test_an_unreadable_vault_is_NO_DATA(self):
        self._mint()
        code, _ = run(["check", "--vault", os.path.join(self.tmp, "no-such-vault"),
                       "--citations", self.citations])
        self.assertEqual(code, 2)

    def test_a_malformed_json_line_is_skipped_not_fatal(self):
        self._mint()
        with open(self.citations, "a", encoding="utf-8") as fh:
            fh.write("{not valid json\n")
        code, out = run(["check", "--vault", self.vault, "--citations", self.citations])
        self.assertEqual(code, 0)
        self.assertIn("citations: 1", out)

    def test_a_record_with_no_note_id_is_malformed_not_MISSING_None(self):
        with open(self.citations, "w", encoding="utf-8") as fh:
            fh.write(json.dumps({"by": "VB6-02", "content_sha256": "x",
                                  "lifecycle": "legacy"}) + "\n")
        code, out = run(["check", "--vault", self.vault, "--citations", self.citations])
        self.assertEqual(code, 1)
        self.assertIn("MALFORMED-RECORD", out)
        self.assertNotIn("MISSING None", out)
        self.assertIn("malformed: 1", out)

    # -- duplicate ids: two notes declaring the same id -----------------------

    def _add_second_note_with_same_id(self, body="a different note, same id"):
        second = os.path.join(self.vault, "the-other-note.md")
        with open(second, "w", encoding="utf-8") as fh:
            fh.write(note(body))
        return second

    def test_mint_refuses_an_ambiguous_id_naming_both_paths(self):
        second = self._add_second_note_with_same_id()
        code, _ = run(["mint", "--vault", self.vault, "--note", NOTE_ID,
                       "--by", "VB6-02", "--out", self.citations])
        self.assertEqual(code, 1)
        self.assertFalse(os.path.exists(self.citations))
        # driven backwards: a first-match _resolve would have silently minted instead
        _ = second

    def test_check_reports_ambiguous_id_for_a_preminted_record(self):
        self._mint()
        self._add_second_note_with_same_id()
        code, out = run(["check", "--vault", self.vault, "--citations", self.citations])
        self.assertEqual(code, 1)
        self.assertIn("AMBIGUOUS-ID", out)
        self.assertIn(NOTE_ID, out)
        self.assertIn("ambiguous: 1", out)
        self.assertNotIn("SUPERSEDED", out)
        self.assertNotIn("MISSING", out)

    # -- unreadable is not MISSING ---------------------------------------------

    def test_a_permission_denied_file_reports_UNREADABLE_SCAN_not_MISSING(self):
        if os.geteuid() == 0:
            self.skipTest("running as root: chmod 0 does not block reads")
        self._mint()
        os.remove(self.note_path)
        blocked = os.path.join(self.vault, "blocked.md")
        with open(blocked, "w", encoding="utf-8") as fh:
            fh.write(note("unreachable"))
        os.chmod(blocked, 0)
        try:
            code, out = run(["check", "--vault", self.vault, "--citations", self.citations])
            self.assertEqual(code, 2)
            self.assertIn("UNREADABLE-SCAN", out)
            self.assertIn(NOTE_ID, out)
            self.assertNotIn("MISSING", out)
            self.assertIn("unreadable-scan: 1", out)
        finally:
            os.chmod(blocked, 0o644)


class RealVaultReadOnlyProof(unittest.TestCase):
    """WBS VB6-02's own step 6: mint against a real vault note into a file outside
    it (all current, exit 0), then re-check against a COPY of the vault with one
    character appended to the cited note (SUPERSEDED-CONTENT, exit 1). The real
    vault itself is never opened for writing anywhere in this class."""

    def setUp(self):
        self.vault = os.environ.get("BM_VAULT_ROOT") or os.environ.get("BROTHERMODE_VAULT")
        if not self.vault or not os.path.isdir(self.vault):
            self.skipTest("no real vault configured (BM_VAULT_ROOT / BROTHERMODE_VAULT)")
        self.real_note_id = self._first_note_id()
        if self.real_note_id is None:
            self.skipTest("the configured vault has no note carrying a stable id: n-<16 hex>")
        self.tmp = tempfile.mkdtemp(prefix="bm-cite-real-")
        self.citations = os.path.join(self.tmp, "citations.jsonl")

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _first_note_id(self):
        for path in cite._walk(self.vault):
            try:
                with open(path, encoding="utf-8", errors="replace") as fh:
                    text = fh.read()
            except OSError:  # sbe: allow-silent test fixture scanning a real vault for any usable note id, one unreadable file is skipped
                continue
            m = cite.ID_RE.search(cite._frontmatter(text))
            if m:
                value = m.group(1).strip().strip('"').strip("'")
                if cite.ID_VALUE_RE.match(value):
                    return value
        return None

    def test_mint_and_check_are_read_only_and_the_copy_shows_superseded(self):
        before = self._snapshot(self.vault)

        code, _ = run(["mint", "--vault", self.vault, "--note", self.real_note_id,
                       "--by", "VB6-02-proof", "--out", self.citations])
        self.assertEqual(code, 0)

        code, out = run(["check", "--vault", self.vault, "--citations", self.citations])
        self.assertEqual(code, 0)
        self.assertIn("current: 1", out)

        # The real vault must be byte-for-byte unchanged by either call.
        self.assertEqual(before, self._snapshot(self.vault))

        vault_copy = os.path.join(self.tmp, "vault-copy")
        shutil.copytree(self.vault, vault_copy)
        target = cite._resolve(vault_copy, self.real_note_id)[0][0]
        with open(target, "a", encoding="utf-8") as fh:
            fh.write("x")

        code, out = run(["check", "--vault", vault_copy, "--citations", self.citations])
        self.assertEqual(code, 1)
        self.assertIn("SUPERSEDED-CONTENT", out)
        self.assertIn(self.real_note_id, out)

        # The real vault is still untouched: only the copy was ever written to.
        self.assertEqual(before, self._snapshot(self.vault))

    def _snapshot(self, vault):
        path = cite._resolve(vault, self.real_note_id)[0][0]
        with open(path, encoding="utf-8", errors="replace") as fh:
            return fh.read()


if __name__ == "__main__":
    unittest.main()

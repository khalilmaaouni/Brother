"""What test_erasure_propagation.py's own pure helpers and verdict() must keep
true, driven with synthetic fixtures -- no real vault, no bmu tools directory,
no subprocess. Mirrors test_test_japanese_threshold.py's own shape: import the
checker by file path, exercise its pure functions directly, prove both that a
clean run passes and that a doctored one reports FAIL, not a fake PASS.

Real end-to-end correctness (does forget-execute's own CLI output actually get
parsed right, does the real index actually lose the right rows) is exercised
separately by running scripts/test_erasure_propagation.py itself against a real
$BROTHERMODEUP_TOOLS checkout, forward and with --skip-erase.
"""
import json
import os
import sqlite3
import sys
import tarfile
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import test_erasure_propagation as EP  # noqa: E402


class FileContainsReadsBytesHonestly(unittest.TestCase):
    def test_missing_file_is_no_data_not_absent(self):
        self.assertIsNone(EP.file_contains("/no/such/path/at/all.md", b"needle"))

    def test_present_and_absent(self):
        with tempfile.TemporaryDirectory() as d:
            p = os.path.join(d, "a.md")
            with open(p, "w", encoding="utf-8") as fh:
                fh.write("this note carries THE-CANARY inline")
            self.assertTrue(EP.file_contains(p, b"THE-CANARY"))
            self.assertFalse(EP.file_contains(p, b"NOT-PRESENT"))


class ScanVaultNotesExcludesNamedSurfacesOnPurpose(unittest.TestCase):
    def test_exclusion_list_is_honored(self):
        with tempfile.TemporaryDirectory() as vault:
            os.makedirs(os.path.join(vault, "10-Projects", "p"), exist_ok=True)
            paths = {
                "10-Projects/p/derived.md": "carries CANARY-1",
                "10-Projects/p/Catalog.md": "carries CANARY-1 too",
                "10-Projects/p/other.md": "carries CANARY-1 as well",
                "10-Projects/p/clean.md": "carries nothing interesting",
            }
            for rel, text in paths.items():
                with open(os.path.join(vault, rel), "w", encoding="utf-8") as fh:
                    fh.write(text)
            hits = EP.scan_vault_notes(vault, b"CANARY-1",
                                       {"10-Projects/p/derived.md", "10-Projects/p/Catalog.md"})
            self.assertEqual(hits, ["10-Projects/p/other.md"])


class TarContainsScansEveryMember(unittest.TestCase):
    def test_hit_and_miss(self):
        with tempfile.TemporaryDirectory() as d:
            src = os.path.join(d, "note.md")
            with open(src, "w", encoding="utf-8") as fh:
                fh.write("a backup carries CANARY-2 forever")
            tar_path = os.path.join(d, "backup.tar")
            with tarfile.open(tar_path, "w") as tf:
                tf.add(src, arcname="vault/note.md")
            self.assertTrue(EP.tar_contains(tar_path, b"CANARY-2"))
            self.assertFalse(EP.tar_contains(tar_path, b"CANARY-3"))
            self.assertIsNone(EP.tar_contains(os.path.join(d, "missing.tar"), b"CANARY-2"))


class SqliteHelpersMatchTheRealSchemaShape(unittest.TestCase):
    """A tiny sqlite file shaped exactly like bm_vault.py's own notes/notes_fts/
    anchors/links/vectors/supersessions tables, built here with plain SQL
    (never bm_vault.py's own schema function) so this test proves the
    checker's OWN queries, independent of the real tool ever running."""

    def _build_fixture_db(self, path):
        con = sqlite3.connect(path)
        con.executescript("""
            CREATE TABLE notes (id INTEGER PRIMARY KEY, path TEXT UNIQUE);
            CREATE TABLE notes_fts (rowid INTEGER);
            CREATE TABLE anchors (note_id INTEGER);
            CREATE TABLE links (note_id INTEGER);
            CREATE TABLE vectors (note_id INTEGER);
            CREATE TABLE supersessions (by_note_id INTEGER);
        """)
        con.execute("INSERT INTO notes (id, path) VALUES (1, '/vault/erased.md')")
        con.execute("INSERT INTO notes_fts (rowid) VALUES (1)")
        con.execute("INSERT INTO links (note_id) VALUES (1)")
        con.commit()
        con.close()

    def test_row_id_present_then_gone_after_delete(self):
        with tempfile.TemporaryDirectory() as d:
            db = os.path.join(d, "index.sqlite3")
            self._build_fixture_db(db)
            self.assertEqual(EP.sqlite_note_row_id(db, "/vault/erased.md"), 1)
            self.assertIsNone(EP.sqlite_note_row_id(db, "/vault/never-existed.md"))
            self.assertIsNone(EP.sqlite_note_row_id(os.path.join(d, "no.sqlite3"),
                                                     "/vault/erased.md"))

    def test_child_counts_reflect_the_fixture(self):
        with tempfile.TemporaryDirectory() as d:
            db = os.path.join(d, "index.sqlite3")
            self._build_fixture_db(db)
            counts = EP.sqlite_child_counts(db, 1)
            self.assertEqual(counts, {"notes_fts": 1, "anchors": 0, "links": 1,
                                      "vectors": 0, "supersessions": 0})
            self.assertIsNone(EP.sqlite_child_counts(db, None))
            self.assertIsNone(EP.sqlite_child_counts(os.path.join(d, "no.sqlite3"), 1))


class ReceiptLeakCheckClearsTheDocumentedIdentifierFields(unittest.TestCase):
    """forget-execute's own receipt legitimately carries the erased note's path
    and stable id (bookkeeping, not content). A canary embedded in the FIXTURE
    FILENAME (this suite's own choice, so the catalog surface has something to
    find) must not false-positive the receipt check; a canary anywhere else in
    the receipt must."""

    def test_canary_only_in_note_path_is_not_a_leak(self):
        with tempfile.TemporaryDirectory() as d:
            p = os.path.join(d, "receipt.json")
            with open(p, "w", encoding="utf-8") as fh:
                json.dump({"note_path": "/vault/erase-canary-abc123.md",
                          "note_stable_id": "n-0000000000000001",
                          "removed": {"index": 1}}, fh)
            self.assertFalse(EP.receipt_leaks_content(p, "erase-canary-abc123"))

    def test_canary_in_any_other_field_is_a_leak(self):
        with tempfile.TemporaryDirectory() as d:
            p = os.path.join(d, "receipt.json")
            with open(p, "w", encoding="utf-8") as fh:
                json.dump({"note_path": "/vault/unrelated.md",
                          "manual_followups": ["erase-canary-abc123 lives on"]}, fh)
            self.assertTrue(EP.receipt_leaks_content(p, "erase-canary-abc123"))


class VerdictScoresOnlySTRICTSurfaces(unittest.TestCase):
    """The rule this whole suite exists to get right: a STRICT surface still
    carrying the canary fails the run; RETAINED-IN-PRE-ERASURE-ARTIFACTS,
    NOT-PROPAGATED and INFORMATIONAL surfaces never do, however they read; a
    failed setup command fails the run regardless of what the surfaces say."""

    def _clean_surfaces(self):
        return [
            {"name": "a", "bucket": "STRICT", "status": "ABSENT", "detail": ""},
            {"name": "b", "bucket": "RETAINED-IN-PRE-ERASURE-ARTIFACTS",
             "status": "FOUND", "detail": ""},
            {"name": "c", "bucket": "NOT-PROPAGATED", "status": "FOUND", "detail": ""},
            {"name": "d", "bucket": "INFORMATIONAL", "status": "FOUND", "detail": ""},
        ]

    def test_all_clean_passes(self):
        checks = [{"name": "setup", "passed": True, "detail": ""}]
        passed, setup_failed, strict_failed = EP.verdict(checks, self._clean_surfaces())
        self.assertTrue(passed)
        self.assertEqual(setup_failed, [])
        self.assertEqual(strict_failed, [])

    def test_a_strict_surface_found_fails_the_run(self):
        checks = [{"name": "setup", "passed": True, "detail": ""}]
        surfaces = self._clean_surfaces()
        surfaces[0]["status"] = "FOUND"  # the doctored flip
        passed, setup_failed, strict_failed = EP.verdict(checks, surfaces)
        self.assertFalse(passed)
        self.assertEqual(setup_failed, [])
        self.assertEqual(len(strict_failed), 1)
        self.assertEqual(strict_failed[0]["name"], "a")

    def test_non_strict_surfaces_never_fail_the_run_however_they_read(self):
        checks = [{"name": "setup", "passed": True, "detail": ""}]
        surfaces = self._clean_surfaces()
        for s in surfaces[1:]:
            s["status"] = "ABSENT"  # flip the informational ones the OTHER way too
        passed, _setup_failed, strict_failed = EP.verdict(checks, surfaces)
        self.assertTrue(passed)
        self.assertEqual(strict_failed, [])

    def test_a_failed_setup_command_fails_the_run_even_with_clean_surfaces(self):
        checks = [{"name": "setup", "passed": False, "detail": "boom"}]
        passed, setup_failed, _strict_failed = EP.verdict(checks, self._clean_surfaces())
        self.assertFalse(passed)
        self.assertEqual(len(setup_failed), 1)


if __name__ == "__main__":
    unittest.main()

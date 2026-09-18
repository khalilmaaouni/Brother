"""What handover_pack_scan.py's opt-in cache must keep true.

THE DEFECT THIS SUITE EXISTS FOR: a real close scan takes 51-52 seconds
against ~1,560 directories / 6,321 files / 205 zips, none of which have
changed since the previous close. --cache PATH exists to skip re-reading
anything unchanged; these tests pin that it does so WITHOUT ever changing
what the scan reports, and that a stale or wrong cache can only ever cost
speed, never correctness (a changed file is always re-read; a changed
terms list always invalidates the whole cache; a corrupt cache degrades
to a full fresh scan, never a wrong answer).

Every term used here is FAKE (QZXW, LONGVENDOR), the same pair
test_close_ceremony_check.py already uses: a scanner's own test fixtures
carrying a real term would publish exactly what the scanner exists to stop.
"""
import hashlib
import io
import json
import os
import shutil
import sys
import tempfile
import time
import unittest
import zipfile

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import handover_pack_scan as hps  # noqa: E402

try:
    import tmp_sandbox as _sandbox
    _sandbox.install()
except ImportError:
    pass

SHORT_TERM = "QZXW"
LONG_TERM = "LONGVENDOR"


def _write(path, content):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(content)


def _run(root, terms_path, cache_path=None):
    hits, _sp, _lp, stats, reason = hps.run_scan(root, terms_path, cache_path=cache_path)
    return hits, stats, reason


class CacheNeverChangesWhatIsReported(unittest.TestCase):
    """The load-bearing property: --cache is a speed change, never a
    correctness change. A scan with a cache and a scan without one, over
    the identical tree, must report the identical hits."""

    def setUp(self):
        self.root = tempfile.mkdtemp(prefix="pack-scan-cache-test-")
        # The terms file and the cache file both live OUTSIDE the scanned
        # root, matching production (~/.brothersbe-private-names sits
        # outside ~/Documents/BrotherModeUp-handovers): a terms file
        # placed INSIDE the scanned root would necessarily contain the
        # term text and the scanner would (correctly) flag itself.
        self.side = tempfile.mkdtemp(prefix="pack-scan-cache-side-")
        self.terms_path = os.path.join(self.side, "terms.txt")
        self.cache_path = os.path.join(self.side, "cache.json")
        _write(self.terms_path, SHORT_TERM + "\n" + LONG_TERM + "\n")
        _write(os.path.join(self.root, "packs", "clean.md"), "nothing interesting here\n")
        _write(os.path.join(self.root, "packs", "dirty.md"),
               "this file names " + LONG_TERM + " once\n")
        zpath = os.path.join(self.root, "packs", "bundle.zip")
        with zipfile.ZipFile(zpath, "w") as zf:
            zf.writestr("inner.md", "a clean member")
            zf.writestr(SHORT_TERM.lower() + "-named.md", "member named after a term")

    def test_cached_and_uncached_scans_agree_on_every_hit(self):
        hits_nocache, stats_nocache, _ = _run(self.root, self.terms_path)
        hits_cached, stats_cached, _ = _run(self.root, self.terms_path, self.cache_path)
        self.assertEqual(sorted(hits_nocache), sorted(hits_cached))
        for key in ("dirs", "files", "zips", "zip_members"):
            self.assertEqual(stats_nocache[key], stats_cached[key], key)

    def test_a_second_cached_run_with_nothing_changed_agrees_with_the_first(self):
        first_hits, _s, _ = _run(self.root, self.terms_path, self.cache_path)
        second_hits, _s2, _ = _run(self.root, self.terms_path, self.cache_path)
        self.assertEqual(sorted(first_hits), sorted(second_hits))

    def test_a_changed_file_is_re_read_not_served_stale_from_cache(self):
        _run(self.root, self.terms_path, self.cache_path)  # populate
        clean_path = os.path.join(self.root, "packs", "clean.md")
        time.sleep(0.01)
        _write(clean_path, "now this ALSO names " + LONG_TERM + "\n")
        hits, _s, _ = _run(self.root, self.terms_path, self.cache_path)
        hit_paths = [h[1] for h in hits]
        self.assertIn(os.path.join("packs", "clean.md"), hit_paths,
                     "a file edited after the cache was built must be re-read, "
                     "not answered from a now-stale cache entry")

    def test_a_changed_zip_is_re_scanned_not_served_stale_from_cache(self):
        _run(self.root, self.terms_path, self.cache_path)  # populate
        zpath = os.path.join(self.root, "packs", "bundle.zip")
        time.sleep(0.01)
        with zipfile.ZipFile(zpath, "w") as zf:
            zf.writestr("inner.md", "still clean")
            zf.writestr("new-member.md", "now names " + LONG_TERM)
        hits, _s, _ = _run(self.root, self.terms_path, self.cache_path)
        member_hits = [h[1] for h in hits if h[0] == "zip-member"]
        self.assertTrue(any("new-member.md" in p for p in member_hits),
                        "a rewritten zip must be re-scanned, not served from a stale cache "
                        "entry keyed to the old file's mtime and size")

    def test_a_changed_terms_list_invalidates_the_whole_cache(self):
        _run(self.root, self.terms_path, self.cache_path)  # populate under the old terms
        new_terms_path = os.path.join(self.side, "terms2.txt")
        _write(new_terms_path, SHORT_TERM + "\nBRANDNEWTERM\n")
        _write(os.path.join(self.root, "packs", "clean.md"),
               "this now names BRANDNEWTERM\n")
        hits, _s, _ = _run(self.root, new_terms_path, self.cache_path)
        hit_paths = [h[1] for h in hits]
        self.assertIn(os.path.join("packs", "clean.md"), hit_paths,
                     "a new term must be detected even in a file the OLD cache had already "
                     "marked clean under the previous term list")


class CorruptOrMissingCacheDegradesToAFullScanNeverAWrongAnswer(unittest.TestCase):
    """A cache is an optimization. Every failure mode around it (missing,
    unreadable, corrupt JSON, wrong shape) must fall back to exactly
    today's behavior -- a full fresh scan -- never a crash and never a
    silently wrong (too-clean) result."""

    def setUp(self):
        self.root = tempfile.mkdtemp(prefix="pack-scan-cache-corrupt-test-")
        self.side = tempfile.mkdtemp(prefix="pack-scan-cache-corrupt-side-")
        self.terms_path = os.path.join(self.side, "terms.txt")
        _write(self.terms_path, LONG_TERM + "\n")
        _write(os.path.join(self.root, "packs", "dirty.md"),
               "names " + LONG_TERM + " once\n")

    def test_a_missing_cache_file_scans_fully_and_writes_one(self):
        cache_path = os.path.join(self.root, "does-not-exist-yet.json")
        hits, stats, _ = _run(self.root, self.terms_path, cache_path)
        self.assertEqual(len(hits), 1)
        self.assertTrue(os.path.isfile(cache_path))

    def test_a_corrupt_cache_file_is_treated_as_no_cache_not_a_crash(self):
        cache_path = os.path.join(self.root, "corrupt.json")
        _write(cache_path, "{ this is not valid json")
        hits, stats, reason = _run(self.root, self.terms_path, cache_path)
        self.assertIsNone(reason)
        self.assertEqual(len(hits), 1, "a corrupt cache must not suppress a real hit")

    def test_a_cache_with_the_wrong_shape_is_treated_as_no_cache(self):
        cache_path = os.path.join(self.root, "wrong-shape.json")
        _write(cache_path, json.dumps({"not": "the expected shape"}))
        hits, stats, reason = _run(self.root, self.terms_path, cache_path)
        self.assertIsNone(reason)
        self.assertEqual(len(hits), 1)

    def test_omitting_cache_argument_writes_nothing_new_to_disk(self):
        before = set(os.listdir(self.root))
        _run(self.root, self.terms_path, cache_path=None)
        after = set(os.listdir(self.root))
        self.assertEqual(before, after,
                         "run_scan with no --cache must write nothing, preserving the "
                         "module's own 'nothing written to disk' default")


class EveryFileIsContentScannedWhateverItsExtension(unittest.TestCase):
    """THE DEFECT (measured 2026-09-18): content was read only for an
    allowlist of text extensions, so a 25 MiB .duckdb inside a pack carried
    a term that a byte search found while run_scan reported 0 hits, and
    close_ceremony_check.py printed PASS over it. Zip members followed the
    same allowlist. Every file and every member is now read as raw bytes."""

    def setUp(self):
        self.root = tempfile.mkdtemp(prefix="pack-scan-binary-test-")
        self.side = tempfile.mkdtemp(prefix="pack-scan-binary-side-")
        self.terms_path = os.path.join(self.side, "terms.txt")
        _write(self.terms_path, SHORT_TERM + "\n" + LONG_TERM + "\n")
        self.pack = os.path.join(self.root, "2026-09-18-pack")
        os.makedirs(self.pack)
        self.addCleanup(setattr, hps, "CHUNK_BYTES", getattr(hps, "CHUNK_BYTES", None))

    def _write_bytes(self, name, data):
        with open(os.path.join(self.pack, name), "wb") as fh:
            fh.write(data)

    def _kinds(self, cache_path=None):
        hits, stats, reason = _run(self.root, self.terms_path, cache_path)
        self.assertIsNone(reason)
        return sorted((kind, os.path.basename(p.split("::")[-1])) for kind, p, _n in hits), stats

    def test_a_term_inside_a_binary_database_file_is_a_content_hit(self):
        self._write_bytes("store.duckdb",
                          b"DUCK\x00\xff\xfe\x80" * 64 + b"\x00" + SHORT_TERM.encode() +
                          b"\x00\xc3" + b"\x00" * 256)
        hits, stats = self._kinds()
        self.assertEqual(hits, [("content", "store.duckdb")])
        self.assertEqual(stats["unreadable"], [])

    def test_a_term_in_a_text_file_with_no_allowlisted_extension_is_a_hit(self):
        self._write_bytes("0001-change.patch", ("+ vendor " + LONG_TERM + "\n").encode())
        self._write_bytes("NOTES", ("see " + SHORT_TERM + " below\n").encode())
        hits, _ = self._kinds()
        self.assertEqual(hits, [("content", "0001-change.patch"), ("content", "NOTES")])

    def test_a_term_inside_a_binary_zip_member_is_a_zip_member_hit(self):
        with zipfile.ZipFile(os.path.join(self.pack, "bundle.zip"), "w",
                             zipfile.ZIP_DEFLATED) as zf:
            zf.writestr("clean.png", b"\x89PNG\r\n\x1a\n" + b"\x00\xff" * 500)
            zf.writestr("store.duckdb", b"\x00\xff" * 100 + b"\x00" +
                        SHORT_TERM.encode() + b"\x00" * 100)
        hits, stats = self._kinds()
        self.assertEqual(hits, [("zip-member", "store.duckdb")])
        self.assertEqual(stats["zip_members"], 2)

    def test_a_term_inside_a_zip_nested_in_a_zip_is_a_hit(self):
        inner = io.BytesIO()
        with zipfile.ZipFile(inner, "w", zipfile.ZIP_DEFLATED) as zf:
            zf.writestr("deep.bin", b"\x00\xff" + LONG_TERM.encode() + b"\x00")
        with zipfile.ZipFile(os.path.join(self.pack, "outer.zip"), "w",
                             zipfile.ZIP_DEFLATED) as zf:
            zf.writestr("inner.zip", inner.getvalue())
        hits, _ = self._kinds()
        self.assertEqual(hits, [("zip-member", "deep.bin")])

    def test_a_zip_container_member_without_a_zip_extension_is_opened(self):
        inner = io.BytesIO()
        with zipfile.ZipFile(inner, "w", zipfile.ZIP_DEFLATED) as zf:
            zf.writestr("word/document.xml", "<t>" + SHORT_TERM + "</t>" * 50)
        with zipfile.ZipFile(os.path.join(self.pack, "outer.zip"), "w",
                             zipfile.ZIP_STORED) as zf:
            zf.writestr("report.docx", inner.getvalue())
        hits, _ = self._kinds()
        self.assertEqual(hits, [("zip-member", "document.xml")])

    def test_a_zip_container_without_a_zip_extension_has_its_members_read(self):
        # .xlsx and .docx are zip archives whose members are deflated: a raw
        # byte search of the file itself can never see a term inside them.
        with zipfile.ZipFile(os.path.join(self.pack, "sheet.xlsx"), "w",
                             zipfile.ZIP_DEFLATED) as zf:
            zf.writestr("xl/sharedStrings.xml", "<si>" + SHORT_TERM + "</si>" * 50)
        hits, stats = self._kinds()
        self.assertEqual(hits, [("zip-member", "sharedStrings.xml")])
        self.assertEqual(stats["zips"], 1)

    def test_a_term_split_across_two_read_chunks_is_still_found(self):
        hps.CHUNK_BYTES = 7
        for offset in range(len(SHORT_TERM) + 2):
            payload = b"\x00" * (5 + offset) + SHORT_TERM.encode() + b"\x00" * 9
            self._write_bytes("split.bin", payload)
            hits, _ = self._kinds()
            self.assertEqual(hits, [("content", "split.bin")], msg="offset %d" % offset)

    def test_a_chunk_edge_never_turns_a_longer_word_into_a_short_term_hit(self):
        # QZXW followed by a word character is a different word; a chunk
        # boundary right after QZXW must not read as the word's end.
        hps.CHUNK_BYTES = 7
        for offset in range(len(SHORT_TERM) + 2):
            payload = b"\x00" * (5 + offset) + SHORT_TERM.encode() + b"abc\x00"
            self._write_bytes("longer.bin", payload)
            hits, _ = self._kinds()
            self.assertEqual(hits, [], msg="offset %d" % offset)

    def test_a_cache_written_under_the_old_extension_rule_is_discarded(self):
        """A zip cached clean by the extension-only rule must be re-read:
        same files, same terms, only the scan rule changed."""
        zpath = os.path.join(self.pack, "bundle.zip")
        with zipfile.ZipFile(zpath, "w") as zf:
            zf.writestr("store.duckdb", b"\x00" + SHORT_TERM.encode() + b"\x00")
        st = os.stat(zpath)
        relpath = os.path.relpath(zpath, self.root)
        old_hash = hashlib.sha256("\n".join([SHORT_TERM, LONG_TERM]).encode("utf-8")).hexdigest()
        cache_path = os.path.join(self.side, "cache.json")
        _write(cache_path, json.dumps({
            "terms_hash": old_hash, "files": {},
            "zips": {relpath: {"stat": [st.st_mtime_ns, st.st_size], "hits": [],
                               "member_count": 1, "unreadable_members": []}}}))
        hits, _ = self._kinds(cache_path)
        self.assertEqual(hits, [("zip-member", "store.duckdb")])

    def test_a_path_that_is_not_a_regular_file_is_unreadable_never_opened(self):
        os.mkfifo(os.path.join(self.pack, "pipe"))  # opening it would block forever
        os.symlink(os.path.join(self.side, "gone"), os.path.join(self.pack, "dangling"))
        hits, stats = self._kinds()
        self.assertEqual(hits, [])
        self.assertEqual(sorted(os.path.basename(p) for p in stats["unreadable"]),
                         ["dangling", "pipe"])

    def test_compressed_media_is_checked_for_long_terms_only(self):
        """Measured on the real root 2026-09-18: 832 MiB of PNG members gave
        5 chance matches of a 4 letter term (mixed case, random bytes
        around it), the rate (2/256)^4 predicts. Image and audio payloads
        are compressed noise to a short term, so only terms over
        SHORT_TERM_MAX_LEN characters are checked there."""
        png = b"\x89PNG\r\n\x1a\n\x00\x00\x00\x0dIHDR"
        self._write_bytes("noise.png", png + b"\x00\x9c" + b"qZxW" + b"\x00\x9c")
        self._write_bytes("tagged.m4a", b"\x00\x00\x00\x20ftypM4A \x00" +
                          LONG_TERM.encode() + b"\x00")
        with zipfile.ZipFile(os.path.join(self.pack, "shots.zip"), "w") as zf:
            zf.writestr("shot.jpg", b"\xff\xd8\xff\xe0\x00" + SHORT_TERM.encode() + b"\x00")
        hits, _ = self._kinds()
        self.assertEqual(hits, [("content", "tagged.m4a")])

    def test_a_clean_binary_tree_stays_clean(self):
        self._write_bytes("store.duckdb", bytes(range(256)) * 40)
        self._write_bytes("word.bin", b"\x00" + SHORT_TERM.encode() + b"x\x00")
        hits, stats = self._kinds()
        self.assertEqual(hits, [])
        self.assertEqual(stats["unreadable"], [])


class CacheFileNeverScansItselfAsPackContent(unittest.TestCase):
    def test_the_cache_file_is_skipped_by_the_walk(self):
        root = tempfile.mkdtemp(prefix="pack-scan-cache-selfscan-test-")
        side = tempfile.mkdtemp(prefix="pack-scan-cache-selfscan-side-")
        terms_path = os.path.join(side, "terms.txt")
        _write(terms_path, LONG_TERM + "\n")
        cache_path = os.path.join(root, hps.CACHE_BASENAME)
        _run(root, terms_path, cache_path)  # writes the cache file into root itself
        hits, stats, _ = _run(root, terms_path, cache_path)
        self.assertEqual(stats["files"], 0,
                         "the cache file living inside the scanned root must never be "
                         "counted or scanned as if it were pack content")


class TheUnderscoreIsABoundary20260918(unittest.TestCase):
    """2026-09-18: the short-term lookarounds carried "_" in their word
    class, so an all-capitals client code written as an identifier prefix
    (CODE_APP_DEV, CODE_product_master.csv, path/CODE_app/) was NOT a hit.
    After a first scrub of two packs run_scan reported 0 hits and the
    closing ceremony printed PASS while a plain substring grep still found
    the code in 13 files, all underscore-joined. The sibling
    bm_private_scan.py fixed the same bound as E37 on 2026-09-03. Driven
    both ways: underscore-adjacent is a hit; glued inside a run of letters
    or digits is not (so a four letter term never fires inside an English
    word)."""

    def setUp(self):
        self.short, self.long = hps.build_patterns([SHORT_TERM])

    def test_an_underscore_joined_short_term_is_a_hit(self):
        for text in (SHORT_TERM + "_APP_DEV",
                     SHORT_TERM + "_product_master.csv",
                     "path/" + SHORT_TERM + "_app/",
                     "prefix_" + SHORT_TERM,
                     "a_" + SHORT_TERM.lower() + "_b"):
            self.assertEqual(hps.first_match_len(text, self.short, self.long),
                             len(SHORT_TERM), text)

    def test_a_short_term_embedded_in_a_word_is_not_a_hit(self):
        for text in ("x" + SHORT_TERM + "y",
                     SHORT_TERM + "ly",
                     "9" + SHORT_TERM,
                     SHORT_TERM + "2",
                     "\u00e9" + SHORT_TERM):
            self.assertIsNone(hps.first_match_len(text, self.short, self.long),
                              text)

    def test_run_scan_reports_underscore_joined_content_and_file_names(self):
        root = tempfile.mkdtemp(prefix="pack-scan-underscore-")
        side = tempfile.mkdtemp(prefix="pack-scan-underscore-side-")
        self.addCleanup(shutil.rmtree, root, ignore_errors=True)
        self.addCleanup(shutil.rmtree, side, ignore_errors=True)
        terms_path = os.path.join(side, "terms.txt")
        _write(terms_path, SHORT_TERM + "\n")
        _write(os.path.join(root, "pack", "notes.md"),
               "deploy to " + SHORT_TERM + "_APP_DEV tonight\n")
        _write(os.path.join(root, "pack", SHORT_TERM + "_product_master.csv"),
               "nothing here\n")
        _write(os.path.join(root, "pack", "clean.md"),
               "x" + SHORT_TERM.lower() + "y is not a whole word\n")
        hits, _stats, reason = _run(root, terms_path)
        self.assertIsNone(reason)
        found = {(h[0], h[1]) for h in hits}
        self.assertIn(("content", os.path.join("pack", "notes.md")), found)
        self.assertIn(("name", os.path.join("pack", SHORT_TERM + "_product_master.csv")),
                      found)
        self.assertNotIn(("content", os.path.join("pack", "clean.md")), found)


if __name__ == "__main__":
    unittest.main()

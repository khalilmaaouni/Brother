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
import json
import os
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


if __name__ == "__main__":
    unittest.main()

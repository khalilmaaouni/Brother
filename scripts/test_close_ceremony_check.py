"""What close_ceremony_check.py must keep true after readiness row E35: the
terms-cleanliness step now scans EVERY pack under the handover root, not
only the newest pack's markdown, reusing handover_pack_scan.py's scanner by
import rather than a second copy of the matching rule.

Every term used here is FAKE (QZXW, four characters, the short/whole-word
branch; LONGVENDOR, ten characters, the long/substring branch), the same
pair scripts/test_cleanse.py and scripts/test_private_terms_scan.py already
use: a scanner's own test fixtures carrying a real term would publish
exactly what the scanner exists to stop.
"""
import contextlib
import io
import os
import shutil
import sys
import tempfile
import time
import unittest
import zipfile

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import close_ceremony_check as C  # noqa: E402
import handover_pack_scan as hps  # noqa: E402

SHORT_TERM = "QZXW"
LONG_TERM = "LONGVENDOR"

VALID_START_HERE = (
    "# What Finished\nEverything landed.\n\n"
    "# Priorities In Order\nNext up.\n\n"
    "# Wisdom: Learnings And Mistakes To Avoid\nDon't do that again.\n\n"
    "# Acceleration: First 15 Minutes\nStart here.\n"
)


def _write(path, content):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(content)


def _backdate(path, hours_ago):
    """So a dirty EXTRA pack is never picked as "the newest pack" that
    checks 1, 2, 3 and 5 examine, and only the new all-root terms sweep
    (check 4) is exercised by these fixtures."""
    t = time.time() - hours_ago * 3600
    os.utime(path, (t, t))


def _make_valid_pack(root, name):
    """A complete, clean, FRESH pack: everything the OTHER four checks in
    close_ceremony_check.py's own docstring require, so a test that adds a
    dirty pack elsewhere fails on term-cleanliness alone. mtime is pinned to
    "now" explicitly after every write, so it is always the newest pack
    regardless of write order or filesystem timestamp resolution."""
    pack_dir = os.path.join(root, name)
    start_here = os.path.join(pack_dir, "01-START-HERE.md")
    _write(start_here, VALID_START_HERE)
    board = os.path.join(pack_dir, "readiness-board.html")
    _write(board, "<html>board</html>\n")
    session_log = os.path.join(pack_dir, "session-log.md")
    _write(session_log, "the session log\n")
    zip_path = pack_dir + ".zip"
    with zipfile.ZipFile(zip_path, "w") as zf:
        for f in (start_here, board, session_log):
            zf.write(f, os.path.basename(f))
    now = time.time()
    os.utime(pack_dir, (now, now))
    return pack_dir


def _make_terms_file(terms):
    """A fixture terms file OUTSIDE any scanned root, in its own temp
    directory: the real ~/.brothersbe-private-names lives outside the real
    handovers directory the same way, for the same reason (a terms file
    that sits inside the tree it screens would find and report itself)."""
    terms_dir = tempfile.mkdtemp(prefix="ceremony-terms-")
    path = os.path.join(terms_dir, "fixture-terms.txt")
    _write(path, "\n".join(terms) + "\n")
    return path


class TheTermSweepCoversEveryPackNotOnlyTheNewest(unittest.TestCase):
    """Readiness row E35. Before this change, a term sitting in an OLDER
    pack (a file name, a directory name, a zip member, or a scanned text
    file's content) was invisible to this gate: only the newest pack's
    01-START-HERE.md text was ever read."""

    def setUp(self):
        self.root = tempfile.mkdtemp(prefix="ceremony-root-")
        self.terms_path = _make_terms_file([SHORT_TERM, LONG_TERM])

    def tearDown(self):
        shutil.rmtree(self.root, ignore_errors=True)
        shutil.rmtree(os.path.dirname(self.terms_path), ignore_errors=True)

    def _run(self, terms_path=None):
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            code = C.main(["--root", self.root,
                            "--terms", terms_path or self.terms_path])
        return code, buf.getvalue()

    def test_a_clean_pack_with_no_term_anywhere_passes(self):
        _make_valid_pack(self.root, "2026-09-03-clean-pack")
        code, out = self._run()
        self.assertEqual(code, 0, msg=out)
        self.assertTrue(out.startswith("PASS:"), out)

    def test_b_a_term_in_a_zip_members_name_in_an_older_pack_fails_naming_it(self):
        dirty = os.path.join(self.root, "2026-08-01-dirty-zip-pack")
        os.makedirs(dirty)
        with zipfile.ZipFile(os.path.join(dirty, "payload.zip"), "w") as zf:
            zf.writestr("%s-notes.md" % SHORT_TERM, "clean content\n")
        _backdate(dirty, hours_ago=48)
        _make_valid_pack(self.root, "2026-09-03-clean-pack")

        code, out = self._run()
        self.assertEqual(code, 1, msg=out)
        self.assertIn("FAIL:", out)
        self.assertIn("2026-08-01-dirty-zip-pack", out)
        self.assertIn("private-term hit", out)
        self.assertNotIn(SHORT_TERM, out)
        self.assertNotIn(LONG_TERM, out)

    def test_c_a_term_in_a_file_name_in_an_older_pack_fails_naming_it(self):
        dirty = os.path.join(self.root, "2026-08-02-dirty-name-pack")
        _write(os.path.join(dirty, "notes-%s.md" % SHORT_TERM), "clean\n")
        _backdate(dirty, hours_ago=48)
        _make_valid_pack(self.root, "2026-09-03-clean-pack")

        code, out = self._run()
        self.assertEqual(code, 1, msg=out)
        self.assertIn("FAIL:", out)
        self.assertIn("2026-08-02-dirty-name-pack", out)
        self.assertIn("private-term hit", out)
        self.assertNotIn(SHORT_TERM, out)
        self.assertNotIn(LONG_TERM, out)

    def test_d_a_term_in_a_text_files_content_in_an_older_pack_fails_naming_it(self):
        dirty = os.path.join(self.root, "2026-08-03-dirty-content-pack")
        _write(os.path.join(dirty, "notes.md"),
               "the sponsor was %s this year\n" % LONG_TERM.lower())
        _backdate(dirty, hours_ago=48)
        _make_valid_pack(self.root, "2026-09-03-clean-pack")

        code, out = self._run()
        self.assertEqual(code, 1, msg=out)
        self.assertIn("FAIL:", out)
        self.assertIn("2026-08-03-dirty-content-pack", out)
        self.assertIn("private-term hit", out)
        self.assertNotIn(LONG_TERM.lower(), out)
        self.assertNotIn(LONG_TERM, out)

    def test_e_a_term_in_the_packs_own_top_level_name_is_masked_not_printed(self):
        """The audit's exact third case: the offending path IS the label
        this check prints (the pack name itself), so the masking has to
        apply to that label too, not only to paths inside a pack."""
        dirty = os.path.join(self.root, "2026-08-04-%s-dirty-pack" % SHORT_TERM)
        _write(os.path.join(dirty, "notes.md"), "clean\n")
        _backdate(dirty, hours_ago=48)
        _make_valid_pack(self.root, "2026-09-03-clean-pack")

        code, out = self._run()
        self.assertEqual(code, 1, msg=out)
        self.assertIn("FAIL:", out)
        self.assertIn("2026-08-04-<%d>-dirty-pack" % len(SHORT_TERM), out)
        self.assertNotIn(SHORT_TERM, out)

    def test_f_an_unreadable_terms_list_fails_rather_than_silently_passing(self):
        """NO-DATA on the terms list is a STOP, never a pass: a control that
        never actually screened a pack must not report one clean."""
        _make_valid_pack(self.root, "2026-09-03-clean-pack")
        code, out = self._run(terms_path=os.path.join(self.root, "no-such.txt"))
        self.assertEqual(code, 1, msg=out)
        self.assertIn("FAIL:", out)
        self.assertIn("unreadable", out)

    def test_g_a_hit_nested_inside_a_zips_own_folder_still_groups_by_the_zip(self):
        """Found running the real handovers directory: a TOP-LEVEL zip built
        from a directory (the ordinary shape, `zip -r pack.zip pack/`)
        carries members like "pack/notes.md", so a hit's relpath is
        "pack.zip::pack/notes.md". Since the zip itself sits directly under
        root (no os.sep before the "::"), the first "/" in that whole
        string falls INSIDE the member portion; grouping on os.sep alone,
        without stripping the "::member" suffix first, lands the label one
        level inside the zip's own internal folder ("pack.zip::pack")
        instead of naming the zip."""
        zip_path = os.path.join(self.root, "2026-08-05-dirty-nested-pack.zip")
        with zipfile.ZipFile(zip_path, "w") as zf:
            zf.writestr("2026-08-05-dirty-nested-pack/notes.md",
                        "the sponsor was %s this year\n" % LONG_TERM.lower())
        _backdate(zip_path, hours_ago=48)
        _make_valid_pack(self.root, "2026-09-03-clean-pack")

        code, out = self._run()
        self.assertEqual(code, 1, msg=out)
        self.assertIn("FAIL:", out)
        self.assertIn("pack 2026-08-05-dirty-nested-pack.zip carries 1 "
                       "private-term hit", out)
        self.assertNotIn("::2026-08-05-dirty-nested-pack", out,
                          "the group label must stop at the zip, not reach "
                          "one level into its own internal folder")
        self.assertNotIn(LONG_TERM.lower(), out)
        self.assertNotIn(LONG_TERM, out)


class AnUnreadablePathIsNeverSilentlyTreatedAsClean(unittest.TestCase):
    """SBE law L11 (silent-failure-lints) flagged handover_pack_scan.py:127:
    except-then-return-None dropped the record of a file that could not be
    opened, and the caller silently treated that the same as a file it read
    and found clean. A file, a whole zip, or a zip member this tool could
    not open or decode is now counted in a named "unreadable" bucket,
    printed one line per path, and makes the run NO-DATA (never a clean
    PASS) when it is the only thing the scan found. Runs against
    handover_pack_scan.py directly, since close_ceremony_check.py's own
    aggregated pack-level output does not surface this bucket."""

    def setUp(self):
        self.root = tempfile.mkdtemp(prefix="unreadable-root-")
        self.terms_path = _make_terms_file([SHORT_TERM, LONG_TERM])

    def tearDown(self):
        shutil.rmtree(self.root, ignore_errors=True)
        shutil.rmtree(os.path.dirname(self.terms_path), ignore_errors=True)

    def _run_scanner(self):
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            code = hps.main(["--root", self.root, "--terms", self.terms_path])
        return code, buf.getvalue()

    def test_an_undecodable_zip_member_is_reported_by_path_and_count(self):
        """bad-member.md's payload is corrupted in place (same length, so the
        zip's own structure stays valid) so its stored CRC-32 no longer
        matches: zipfile.read() raises BadZipFile for real, the actual
        production code path in _scan_zip's except clause, never a mock."""
        pack = os.path.join(self.root, "2026-09-03-corrupt-pack")
        os.makedirs(pack)
        zip_path = os.path.join(pack, "payload.zip")
        with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_STORED) as zf:
            zf.writestr("bad-member.md", "original content, no term here")
            zf.writestr("good-member.md", "this one reads fine, no term")
        with zipfile.ZipFile(zip_path) as zf:
            info = zf.getinfo("bad-member.md")
            offset = info.header_offset + len(info.FileHeader())
            size = info.compress_size
        with open(zip_path, "r+b") as fh:
            fh.seek(offset)
            fh.write(b"X" * size)

        code, out = self._run_scanner()
        self.assertEqual(code, hps.EXIT_NO_DATA, msg=out)
        self.assertIn("unreadable", out)
        self.assertIn("bad-member.md", out)
        self.assertIn("unreadable=1", out)
        self.assertIn("hits=0", out)
        self.assertNotIn(SHORT_TERM, out)
        self.assertNotIn(LONG_TERM, out)

    def test_a_clean_fixture_reads_zero_unreadable(self):
        pack = os.path.join(self.root, "2026-09-03-clean-pack")
        os.makedirs(pack)
        with open(os.path.join(pack, "notes.md"), "w") as f:
            f.write("nothing interesting here\n")

        code, out = self._run_scanner()
        self.assertEqual(code, hps.EXIT_CLEAN, msg=out)
        self.assertIn("unreadable=0", out)
        self.assertNotIn("\nunreadable ", out)
        self.assertNotIn(SHORT_TERM, out)
        self.assertNotIn(LONG_TERM, out)


if __name__ == "__main__":
    unittest.main()

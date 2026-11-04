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
            zf.write(f, name + "/" + os.path.basename(f))
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


class Night0912CloseCeremonyCheck(unittest.TestCase):
    def test_zip_completeness_accepts_nested_pack_files(self):
        root = tempfile.mkdtemp(prefix="ceremony-nested-")
        self.addCleanup(shutil.rmtree, root, ignore_errors=True)
        pack = os.path.join(root, "pack1")
        os.makedirs(pack)
        with open(os.path.join(pack, "01-START-HERE.md"), "w") as fh:
            fh.write(VALID_START_HERE)
        with open(os.path.join(pack, "board.html"), "w") as fh:
            fh.write("board")
        with open(os.path.join(pack, "session-log.txt"), "w") as fh:
            fh.write("log")
        os.makedirs(os.path.join(pack, "sub"))
        with open(os.path.join(pack, "sub", "inner.txt"), "w") as fh:
            fh.write("x")
        with zipfile.ZipFile(pack + ".zip", "w") as zf:
            zf.write(os.path.join(pack, "01-START-HERE.md"), "pack1/01-START-HERE.md")
            zf.write(os.path.join(pack, "board.html"), "pack1/board.html")
            zf.write(os.path.join(pack, "session-log.txt"), "pack1/session-log.txt")
            zf.write(os.path.join(pack, "sub", "inner.txt"), "pack1/sub/inner.txt")

        old_run_scan = C.hps.run_scan
        C.hps.run_scan = lambda root, terms, **kw: ([], [], [], {}, None)
        try:
            buf = io.StringIO()
            with contextlib.redirect_stdout(buf):
                rc = C.main(["--root", root, "--terms", "x"])
        finally:
            C.hps.run_scan = old_run_scan
        self.assertEqual(rc, 0, buf.getvalue())


class Night0912HandoverPackScan(unittest.TestCase):
    def test_short_term_boundary_is_not_fooled_by_an_accented_letter(self):
        short, long = hps.build_patterns(["abc"])
        # e-acute (U+00E9) is an accented Latin letter, so 'abc' inside
        # 'eacute-abc' is not a whole-word match; the old ASCII-only
        # lookaround wrongly reported one.
        self.assertIsNone(hps.first_match_len("éabc", short, long))
        # guard the real whole-word behavior this boundary is meant to give
        self.assertEqual(hps.first_match_len("abc", short, long), 3)
        self.assertEqual(hps.first_match_len("x abc y", short, long), 3)
        self.assertIsNone(hps.first_match_len("xabc", short, long))
        # a short term written directly against CJK text with no space must
        # still be caught: CJK stays a boundary character on purpose.
        self.assertEqual(hps.first_match_len("社abc", short, long), 3)


APP_LIKE_TERM = "BRAPPX"  # six characters, like the first target app's name


class ABrotherTreeScopedTermIsNotEnforcedOnHandoverPacks(unittest.TestCase):
    """Founder rule 2026-09-12: the first target app is never named inside
    Brother's OWN tree, but a handover pack is a different, private tree
    and may name it (one real pack is even NAMED with it). The day the rule
    was written the name went into ~/.brothersbe-private-names as a plain
    line and every pack in the estate failed the closing ceremony within
    the hour. The `# scope: brother-tree` directive is the fix: the pack
    scanner (and close_ceremony_check.py through it) skips the block, every
    other term stays enforced everywhere, and the block is invisible to
    every other reader of the list because it is a comment."""

    def setUp(self):
        self.root = tempfile.mkdtemp(prefix="ceremony-root-")
        self.terms_path = _make_terms_file([
            SHORT_TERM, LONG_TERM,
            "# scope: brother-tree", APP_LIKE_TERM, "# scope: all",
        ])

    def tearDown(self):
        shutil.rmtree(self.root, ignore_errors=True)
        shutil.rmtree(os.path.dirname(self.terms_path), ignore_errors=True)

    def _ceremony(self):
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            code = C.main(["--root", self.root, "--terms", self.terms_path])
        return code, buf.getvalue()

    def _scanner(self):
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            code = hps.main(["--root", self.root, "--terms", self.terms_path])
        return code, buf.getvalue()

    def test_a_pack_carrying_only_the_scoped_term_passes_both_gates(self):
        # In the pack's own NAME, in a file name, in content, and in a zip
        # member: every place the sweep looks.
        pack = _make_valid_pack(self.root, "2026-09-12-%s-lanes" % APP_LIKE_TERM.lower())
        _write(os.path.join(pack, "%s-notes.md" % APP_LIKE_TERM),
               "the %s screens fold\n" % APP_LIKE_TERM)
        with zipfile.ZipFile(pack + ".zip", "a") as zf:
            zf.writestr("%s/%s-notes.md" % (os.path.basename(pack), APP_LIKE_TERM),
                        "the %s screens fold\n" % APP_LIKE_TERM)
        code, out = self._scanner()
        self.assertEqual(code, hps.EXIT_CLEAN, msg=out)
        self.assertIn("hits=0", out)
        self.assertIn("terms-scoped-out=1", out)
        code, out = self._ceremony()
        self.assertEqual(code, 0, msg=out)
        self.assertTrue(out.startswith("PASS:"), out)

    def test_the_global_terms_still_fail_a_pack_with_the_directive_present(self):
        _make_valid_pack(self.root, "2026-09-12-clean-pack")
        _write(os.path.join(self.root, "2026-09-01-old-pack", "notes.md"),
               "mentions %s once\n" % LONG_TERM)
        code, out = self._ceremony()
        self.assertEqual(code, 1, msg=out)
        self.assertIn("2026-09-01-old-pack carries 1 private-term hit", out)
        self.assertNotIn(LONG_TERM, out)

    def test_a_short_global_term_still_fails_by_whole_word(self):
        _make_valid_pack(self.root, "2026-09-12-clean-pack")
        _write(os.path.join(self.root, "2026-09-01-old-pack", "notes.md"),
               "client %s figures\n" % SHORT_TERM)
        code, out = self._scanner()
        self.assertEqual(code, hps.EXIT_FOUND, msg=out)
        self.assertIn("(a term of %d characters)" % len(SHORT_TERM), out)
        self.assertNotIn(SHORT_TERM, out)

    def test_the_scoped_term_is_enforced_again_after_scope_all(self):
        # A term listed AFTER `# scope: all` is global, whatever came before.
        terms_path = _make_terms_file([
            "# scope: brother-tree", APP_LIKE_TERM, "# scope: all", LONG_TERM])
        try:
            terms, reason = hps.load_terms(terms_path)
            self.assertIsNone(reason)
            self.assertEqual(terms, [LONG_TERM])
        finally:
            shutil.rmtree(os.path.dirname(terms_path), ignore_errors=True)

    def test_a_list_whose_every_term_is_scoped_out_is_no_data_not_a_pass(self):
        terms_path = _make_terms_file(["# scope: brother-tree", APP_LIKE_TERM])
        try:
            _make_valid_pack(self.root, "2026-09-12-clean-pack")
            buf = io.StringIO()
            with contextlib.redirect_stdout(buf):
                code = hps.main(["--root", self.root, "--terms", terms_path])
            self.assertEqual(code, hps.EXIT_NO_DATA, msg=buf.getvalue())
            self.assertIn("NO-DATA", buf.getvalue())
        finally:
            shutil.rmtree(os.path.dirname(terms_path), ignore_errors=True)


class ANewestDirectoryThatIsNotAPackIsNoDataNeverAFail(unittest.TestCase):
    """Measured 2026-09-17: the newest directory under the real handover
    root was a release-cut handoff scratch directory (two markdown files,
    none of the ceremony's markers, no zip beside it), and the check judged
    it as a close pack that fails the law ("no 01-START-HERE", "no board",
    "no session log", "no zip"), blocking every merge on the machine. A
    directory carrying none of the four pack markers is not a pack the law
    applies to: it is NO-DATA (exit 2, never a pass), named so its owner
    can move it. A directory carrying even ONE marker is a pack that is
    missing the rest, and still FAILS."""

    def setUp(self):
        self.root = tempfile.mkdtemp(prefix="ceremony-root-")
        self.terms_path = _make_terms_file([SHORT_TERM, LONG_TERM])

    def tearDown(self):
        shutil.rmtree(self.root, ignore_errors=True)
        shutil.rmtree(os.path.dirname(self.terms_path), ignore_errors=True)

    def _run(self):
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            code = C.main(["--root", self.root, "--terms", self.terms_path])
        return code, buf.getvalue()

    # HO1 (owner approved 2026-10-31): a scratch directory beside a real close
    # no longer turns the root NO-DATA either; it is kept out of judgment by
    # name and the real close decides. Before HO1 this test expected exit 2.
    def test_a_scratch_directory_newer_than_the_last_pack_is_ignored_naming_it(self):
        _make_valid_pack(self.root, "2026-09-16-clean-pack")
        _backdate(os.path.join(self.root, "2026-09-16-clean-pack"), hours_ago=2)
        scratch = os.path.join(self.root, "2026-09-17-release-cut-scratch")
        _write(os.path.join(scratch, "HANDOVER.md"), "# Handover notes\n")
        _write(os.path.join(scratch, "pr-body.md"), "## Summary\n")
        code, out = self._run()
        self.assertEqual(code, 0, msg=out)
        self.assertIn("IGNORED: 2026-09-17-release-cut-scratch is not a close", out)

    def test_a_root_holding_only_a_scratch_directory_is_no_data(self):
        _write(os.path.join(self.root, "2026-09-17-release-cut-scratch", "HANDOVER.md"), "# notes\n")
        code, out = self._run()
        self.assertEqual(code, 2, msg=out)
        self.assertIn("2026-09-17-release-cut-scratch", out)
        self.assertIn("NO-DATA: no handover record and no legacy pack", out)

    # HO1: a pack's board, log and zip are no longer REQUIRED (design section
    # 7, RETIRED). A directory carrying 01-START-HERE is a legacy close; one
    # carrying only a board copy is not a close at all. Before HO1 the first
    # case expected exit 1 naming the missing board and zip.
    def test_a_start_here_alone_is_a_legacy_close_and_a_board_alone_is_not(self):
        _write(os.path.join(self.root, "2026-09-17-half-pack", "01-START-HERE.md"), VALID_START_HERE)
        code, out = self._run()
        self.assertEqual(code, 0, msg=out)
        self.assertIn("1 legacy pack(s)", out)
        shutil.rmtree(os.path.join(self.root, "2026-09-17-half-pack"))
        _write(os.path.join(self.root, "2026-09-17-board-only", "readiness-board.html"), "<html/>\n")
        code, out = self._run()
        self.assertEqual(code, 2, msg=out)

    def test_a_scratch_directory_never_hides_a_term_hit_in_another_pack(self):
        dirty = os.path.join(self.root, "2026-08-01-dirty-pack")
        _write(os.path.join(dirty, "notes.md"), "mentions %s here\n" % LONG_TERM)
        _backdate(dirty, hours_ago=48)
        scratch = os.path.join(self.root, "2026-09-17-release-cut-scratch")
        _write(os.path.join(scratch, "HANDOVER.md"), "# Handover notes\n")
        code, out = self._run()
        self.assertEqual(code, 1, msg=out)
        self.assertIn("private-term hit", out)
        self.assertIn("kept out of judgment", out)



class Records(unittest.TestCase):
    """HO1: the gate judges DECLARED handover records (handover_record.py
    emit) and the whole root's terms; --require-current adds that the newest
    close for this repository describes HEAD. Records are emitted for real
    from a throwaway repository, never hand built, then edited where a test
    needs a hand edit after emit."""

    GIT = ["git", "-c", "user.name=t", "-c", "user.email=t@example.invalid",
           "-c", "commit.gpgsign=false", "-c", "init.defaultBranch=main"]

    def setUp(self):
        self.tmp = os.path.realpath(tempfile.mkdtemp(prefix="ceremony-records-"))
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.root = os.path.join(self.tmp, "root")
        os.makedirs(self.root)
        self.terms = _make_terms_file([SHORT_TERM, LONG_TERM])
        self.addCleanup(shutil.rmtree, os.path.dirname(self.terms), True)
        self.repo = self.make_repo("repo")
        self.log = os.path.join(self.tmp, "session-log.md")
        _write(self.log, "log\n")
        self.remaining = os.path.join(self.tmp, "remaining.json")
        _write(self.remaining, '[{"order": 1, "what": "next", "done_check": "true", "owner_unit": "HO1"}]')

    def git(self, repo, *args):
        import subprocess
        out = subprocess.run(self.GIT + ["-C", repo] + list(args), capture_output=True, text=True)
        self.assertEqual(out.returncode, 0, out.stderr)
        return out.stdout.strip()

    def make_repo(self, name):
        repo = os.path.join(self.tmp, name)
        os.makedirs(repo)
        self.git(repo, "init", "-q")
        self.commit(repo, "a.txt")
        return repo

    def commit(self, repo, name):
        _write(os.path.join(repo, name), name + "\n")
        self.git(repo, "add", name)
        self.git(repo, "commit", "-q", "-m", name)

    def emit(self, repo=None, slug="close"):
        import handover_record as HR
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf), contextlib.redirect_stderr(io.StringIO()):
            code = HR.main(["emit", "--repo", repo or self.repo, "--slug", slug, "--outcome", "next",
                            "--session-log", self.log, "--evidence-dir", self.tmp,
                            "--remaining", self.remaining, "--root", self.root, "--terms", self.terms,
                            "--remote-of-record", "main", "--session", "s"])
        self.assertEqual(code, 0, buf.getvalue())
        return buf.getvalue().strip().splitlines()[-1]

    def edit(self, path, change):
        import json
        with open(path, encoding="utf-8") as fh:
            rec = json.load(fh)
        change(rec)
        with open(path, "w", encoding="utf-8") as fh:
            json.dump(rec, fh)

    def run_main(self, *extra):
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            code = C.main(["--root", self.root, "--terms", self.terms, "--no-cache",
                           "--repo", self.repo] + list(extra))
        return code, buf.getvalue()

    def test_scratch_bundle_with_zip_is_not_judged(self):
        """The 2026-10-31 shape: a directory with no marker and a zip beside it. Before HO1 the zip made it a pack
        missing its start file, board and log, and every push on the machine failed."""
        _write(os.path.join(self.root, "x", "00-START-PROMPT.md"), "prompt\n")
        with zipfile.ZipFile(os.path.join(self.root, "x.zip"), "w") as zf:
            zf.writestr("x/00-START-PROMPT.md", "prompt\n")
        code, out = self.run_main()
        self.assertEqual(code, 2, out)
        self.assertIn("IGNORED: x is not a close", out)

    def test_a_valid_record_passes(self):
        self.emit()
        code, out = self.run_main()
        self.assertEqual(code, 0, out)
        self.assertIn("PASS: 1 handover record(s) valid", out)

    def test_a_record_file_name_cannot_forge_a_line(self):
        """Round two of the HO1 security review: masked() printed names raw, so a newline in a file name started a
        PASS line of its own and an escape sequence reached the terminal."""
        _write(os.path.join(self.root, "a\nPASS: 9 handover record(s) valid\x1b[2K.handover.json"), "{")
        code, out = self.run_main()
        self.assertEqual(code, 1, out)
        self.assertFalse(any(l.startswith("PASS:") for l in out.splitlines()), out)
        self.assertNotIn("\x1b", out)
        self.assertIn("a\\nPASS: 9", out)

    def test_corrupt_record_fails(self):
        _write(os.path.join(self.root, "2026-10-31-broken.handover.json"), "{ truncated")
        code, out = self.run_main()
        self.assertEqual(code, 1, out)
        self.assertIn("record 2026-10-31-broken.handover.json: does not parse", out)

    def test_a_record_missing_a_field_fails(self):
        path = self.emit()
        self.edit(path, lambda r: r.pop("remaining"))
        code, out = self.run_main()
        self.assertEqual(code, 1, out)
        self.assertIn("missing field remaining", out)

    def test_a_private_term_added_after_emit_fails_and_is_not_printed(self):
        path = self.emit()
        self.edit(path, lambda r: r["first_action"].update(outcome="the %s work" % LONG_TERM))
        code, out = self.run_main()
        self.assertEqual(code, 1, out)
        self.assertIn("private_terms scan FAIL", out)
        self.assertIn("private-term hit", out)
        self.assertNotIn(LONG_TERM, out)

    def test_a_dash_added_after_emit_fails(self):
        path = self.emit()
        self.edit(path, lambda r: r["first_action"].update(outcome="a %s b" % chr(0x2013)))
        code, out = self.run_main()
        self.assertEqual(code, 1, out)
        self.assertIn("dashes scan FAIL", out)

    def test_a_tree_sha_edited_after_emit_fails(self):
        path = self.emit()
        self.edit(path, lambda r: r["project"].update(tree_sha="1" * 40))
        code, out = self.run_main()
        self.assertEqual(code, 1, out)
        self.assertIn("is not the tree of head_sha", out)

    def test_a_moved_branch_is_not_judged_without_require_current(self):
        self.emit()
        self.commit(self.repo, "b.txt")
        self.assertEqual(self.run_main()[0], 0)

    def test_require_current_passes_when_the_record_names_head(self):
        self.emit()
        code, out = self.run_main("--require-current")
        self.assertEqual(code, 0, out)
        self.assertIn("names HEAD", out)

    def test_require_current_finds_the_record_when_the_repo_is_typed_in_another_case(self):
        self.emit()
        variant = os.path.join(os.path.dirname(self.repo), os.path.basename(self.repo).upper())
        if not os.path.isdir(variant):
            self.skipTest("NO-DATA: this disk is case sensitive, so another case is another folder")
        self.repo = variant
        code, out = self.run_main("--require-current")
        self.assertEqual(code, 0, out)
        self.assertIn("names HEAD", out)

    def test_require_current_fails_when_the_branch_moved_since_the_record(self):
        self.emit()
        self.commit(self.repo, "b.txt")
        code, out = self.run_main("--require-current")
        self.assertEqual(code, 1, out)
        self.assertIn("1 commit(s) behind HEAD", out)

    def test_require_current_fails_when_the_record_is_not_an_ancestor(self):
        path = self.emit()
        self.git(self.repo, "checkout", "-q", "-b", "side")
        self.commit(self.repo, "s.txt")
        side = self.git(self.repo, "rev-parse", "HEAD")
        self.git(self.repo, "checkout", "-q", "main")
        tree = self.git(self.repo, "rev-parse", side + "^{tree}")
        self.edit(path, lambda r: r["project"].update(head_sha=side, tree_sha=tree))
        code, out = self.run_main("--require-current")
        self.assertEqual(code, 1, out)
        self.assertIn("not an ancestor of HEAD", out)

    def test_require_current_ignores_another_repositorys_record(self):
        self.emit(repo=self.make_repo("other"))
        code, out = self.run_main("--require-current")
        self.assertEqual(code, 2, out)
        self.assertIn("NO-DATA: no close for", out)
        self.assertEqual(self.run_main()[0], 0)

    def test_a_stale_legacy_pack_keeps_the_line_the_lander_reads(self):
        sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "loop"))
        import land_batch
        _write(os.path.join(self.root, "old-pack", "01-START-HERE.md"), VALID_START_HERE)
        _backdate(os.path.join(self.root, "old-pack"), hours_ago=30)
        code, out = self.run_main("--require-current")
        self.assertEqual(code, 1, out)
        self.assertRegex(out, land_batch.PACK_OLD_RE)

    def test_a_newer_record_makes_an_expired_pack_irrelevant_to_the_clock(self):
        _write(os.path.join(self.root, "old-pack", "01-START-HERE.md"), VALID_START_HERE)
        _backdate(os.path.join(self.root, "old-pack"), hours_ago=30)
        self.assertEqual(len(C.expiry_errors(self.root, time.time())), 1)
        self.emit()
        self.assertEqual(C.expiry_errors(self.root, time.time() + 9 * 86400), [])
        self.assertEqual(C.newest_close(self.root)[1], "record")

    def test_f2_a_junk_file_named_like_a_record_is_never_the_newest_close(self):
        _write(os.path.join(self.root, "old-pack", "01-START-HERE.md"), VALID_START_HERE)
        _backdate(os.path.join(self.root, "old-pack"), hours_ago=30)
        _write(os.path.join(self.root, "scratch.handover.json"), "junk\n")
        self.assertEqual(C.newest_close(self.root), (os.path.join(self.root, "old-pack"), "pack"))
        self.assertEqual(len(C.expiry_errors(self.root, time.time())), 1)
        code, out = self.run_main()
        self.assertEqual(code, 1, out)
        self.assertIn("record scratch.handover.json: does not parse", out)

    def test_f2_another_repositorys_record_does_not_satisfy_this_runs_clock(self):
        _write(os.path.join(self.root, "old-pack", "01-START-HERE.md"), VALID_START_HERE)
        _backdate(os.path.join(self.root, "old-pack"), hours_ago=30)
        self.emit(repo=self.make_repo("other"))
        self.assertEqual(C.expiry_errors(self.root, time.time()), [])
        self.assertEqual(len(C.expiry_errors(self.root, time.time(), repo=self.repo)), 1)

    def test_f9_a_dangling_record_link_fails_by_name_without_a_traceback(self):
        os.symlink(os.path.join(self.tmp, "gone"), os.path.join(self.root, "x.handover.json"))
        code, out = self.run_main()
        self.assertEqual(code, 1, out)
        self.assertIn("record x.handover.json: is not a regular file", out)

    def test_a_moved_log_is_reported_no_data_and_does_not_fail_the_root(self):
        self.emit()
        os.remove(self.log)
        code, out = self.run_main()
        self.assertEqual(code, 0, out)
        self.assertIn("NO-DATA: record", out)



class TheBoxBranch(unittest.TestCase):
    """Owner ruling 2026-10-19 (A2, "Box-aware rows"): in the lander's sandbox the default root is asserted absent under a
    throwaway HOME, never skipped; any other sandbox FAILs so the real check is wired there instead."""

    def run_main(self, home, boxed, argv=()):
        out, saved = io.StringIO(), (C._boxed, os.environ.get("HOME"), C.ROOT)
        C._boxed = lambda: boxed
        os.environ["HOME"] = home
        C.ROOT = os.path.join(home, "Documents", "BrotherModeUp-handovers")
        try:
            with contextlib.redirect_stdout(out):
                code = C.main(list(argv))
        finally:
            C._boxed, C.ROOT = saved[0], saved[2]
            if saved[1] is None:
                os.environ.pop("HOME", None)
            else:
                os.environ["HOME"] = saved[1]
        return code, out.getvalue()

    def test_a_throwaway_home_in_the_box_asserts_the_refusal(self):
        home = tempfile.mkdtemp(prefix="cc-box-")
        self.addCleanup(shutil.rmtree, home, True)
        code, out = self.run_main(home, True)
        self.assertEqual(code, 0, out)
        self.assertTrue(out.startswith("BOXED: "), out)

    def test_a_box_whose_home_holds_the_root_fails(self):
        home = tempfile.mkdtemp(prefix="cc-box-")
        self.addCleanup(shutil.rmtree, home, True)
        os.makedirs(os.path.join(home, "Documents", "BrotherModeUp-handovers"))
        code, out = self.run_main(home, True)
        self.assertEqual(code, 1, out)
        self.assertTrue(out.startswith("FAIL: inside a sandbox"), out)

    def test_outside_the_box_a_missing_root_stays_no_data(self):
        home = tempfile.mkdtemp(prefix="cc-box-")
        self.addCleanup(shutil.rmtree, home, True)
        code, out = self.run_main(home, False)
        self.assertEqual(code, 2, out)
        self.assertTrue(out.startswith("NO-DATA: handover root"), out)

    def test_an_explicit_root_is_never_the_box_branch(self):
        home = tempfile.mkdtemp(prefix="cc-box-")
        self.addCleanup(shutil.rmtree, home, True)
        code, out = self.run_main(home, True, ["--root", os.path.join(home, "absent")])
        self.assertEqual(code, 2, out)


if __name__ == "__main__":
    unittest.main()

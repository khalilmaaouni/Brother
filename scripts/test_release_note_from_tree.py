"""What the release note generator must keep true.

A zero context critic scored docs/releases/1.0.0.md 7 of 10 and found seven
ways this generator's own claims were unmeasured (a skip reading as a pass,
an interpreter dependent zero-test guard, a typed count, a misattributed
citation, an unbound "previous release", no test of its own, and a retyped
paraphrase of receipt_door's scoping sentence). Each gap gets its own test
class here, fed canned unittest output rather than a real subprocess
wherever the logic is pure, so this file proves the same discipline the
generator demands of every other claim in the note.
"""
import contextlib
import io
import json
import os
import shutil
import sys
import tempfile
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import release_note_from_tree as R  # noqa: E402
import receipt_door as RD  # noqa: E402


def make_note(dirpath, name, body):
    p = os.path.join(dirpath, name)
    with open(p, "w", encoding="utf-8") as fh:
        fh.write(body)
    return p


class GapOneASkipReadsAsAPass(unittest.TestCase):
    """"Ran 44 tests ... OK" must read 44 of 44; "OK (skipped=1)" must read
    43 of 44, never 44, and a NO-DATA skip reason must refuse rather than
    print a green count."""

    OK_44 = (
        "test_a (__main__.T.test_a) ... ok\n"
        "test_b (__main__.T.test_b) ... ok\n"
        "\n----------------------------------------------------------------------\n"
        "Ran 44 tests in 12.468s\n\nOK\n"
    )

    OK_SKIPPED_1 = (
        "test_a (__main__.T.test_a) ... skipped 'this machine has python; "
        "the 127 trap needs its absence'\n"
        "test_b (__main__.T.test_b) ... ok\n"
        "\n----------------------------------------------------------------------\n"
        "Ran 44 tests in 1.0s\n\nOK (skipped=1)\n"
    )

    NODATA_SKIP = (
        "test_a (__main__.T.test_a) ... skipped 'NO-DATA: the fixture this "
        "test needs is not on this machine'\n"
        "\n----------------------------------------------------------------------\n"
        "Ran 3 tests in 1.0s\n\nOK (skipped=1)\n"
    )

    def test_ran_44_ok_gives_44_of_44(self):
        r = R.evaluate_suite_output(0, self.OK_44)
        self.assertTrue(r["ok"], r)
        self.assertEqual((r["ran"], r["n"]), (44, 44))
        self.assertEqual(R.format_suite_count(r["ran"], r["n"]), "44 OK")

    def test_ran_44_ok_skipped_1_gives_43_of_44(self):
        r = R.evaluate_suite_output(0, self.OK_SKIPPED_1)
        self.assertTrue(r["ok"], r)
        self.assertEqual((r["ran"], r["n"]), (43, 44))
        self.assertEqual(R.format_suite_count(r["ran"], r["n"]),
                         "43 of 44 ran, OK")

    def test_a_no_data_skip_reason_refuses(self):
        r = R.evaluate_suite_output(0, self.NODATA_SKIP)
        self.assertFalse(r["ok"], r)
        self.assertIsNotNone(r["nodata_skip"])
        self.assertIn(R.NODATA, r["nodata_skip"])


class GapTwoTheZeroTestGuardIsInterpreterIndependent(unittest.TestCase):
    """`n = 0` is not None, so a naive `n is not None` guard would pass an
    empty suite on any interpreter that also exits 0 for one. The guard must
    refuse on the parsed count alone, never on the exit code's word for it."""

    def test_ran_0_tests_ok_refuses_even_with_exit_0_and_a_said_ok_summary(self):
        out = "\n----------------------------------------------------------------------\nRan 0 tests in 0.000s\n\nOK\n"
        r = R.evaluate_suite_output(0, out)
        self.assertFalse(r["ok"], r)
        self.assertEqual(r["n"], 0)

    def test_the_real_interpreter_also_refuses_a_truly_empty_suite(self):
        """Backstop: whatever this machine's own unittest actually prints
        for zero tests, run_suite must not call it a pass."""
        d = tempfile.mkdtemp(prefix="relnote-zero-")
        p = os.path.join(d, "test_empty.py")
        with open(p, "w", encoding="utf-8") as fh:
            fh.write("import unittest\n\n\nclass T(unittest.TestCase):\n    pass\n\n\n"
                     "if __name__ == '__main__':\n    unittest.main()\n")
        rel = os.path.relpath(p, R.ROOT)
        r = R.run_suite(rel)
        self.assertFalse(r["ok"], r)


class GapThreeTheShimsCountIsMeasuredNotTyped(unittest.TestCase):
    def test_a_temp_directory_fixture_is_counted_correctly(self):
        d = tempfile.mkdtemp(prefix="relnote-shims-")
        for name in ["brotherme-start.md", "brotherme-stop.md", "brotherme-view.md",
                     "not-a-shim.md", "brotherme-status.txt"]:
            with open(os.path.join(d, name), "w", encoding="utf-8") as fh:
                fh.write("")
        self.assertEqual(R.shims_count(d), 3)

    def test_an_unreadable_directory_is_None_not_zero(self):
        self.assertIsNone(R.shims_count("/no/such/directory/at/all"))

    def test_the_real_tree_measures_fifteen(self):
        """Pinned to what the tree ships today, per decision D2. The day
        this goes red is the day the shipped count moved and the note's
        typed sentence would have quietly lied."""
        self.assertEqual(R.shims_count(), 15)


class GapFourCitationsNameOnlyTheSuiteThatGuardsTheClaim(unittest.TestCase):
    """test_receipt_door.py is the only suite that asserts RD.SCOPING_SENTENCE,
    the acceptance screen line and the release screen line; test_brother_run.py
    is the only suite with a test for the one-governor-line-per-wait claim.
    The generated note must cite each beside its own claim, not both beside
    every claim."""

    def test_the_real_note_splits_the_two_claims_into_two_paragraphs(self):
        body, problems = R.build()
        self.assertEqual(problems, [], problems)
        self.assertIsNotNone(body)
        scoping_para = [p for p in body.split("\n\n") if "scoping sentence" in p]
        self.assertEqual(len(scoping_para), 1, body)
        self.assertIn("test_receipt_door.py", scoping_para[0])
        self.assertNotIn("test_brother_run.py", scoping_para[0])
        governor_para = [p for p in body.split("\n\n") if "governor line" in p]
        self.assertEqual(len(governor_para), 1, body)
        self.assertIn("test_brother_run.py", governor_para[0])
        self.assertNotIn("test_receipt_door.py", governor_para[0])


class GapFiveThePreviousReleaseLineNeverInventsAHash(unittest.TestCase):
    def setUp(self):
        self.d = tempfile.mkdtemp(prefix="relnote-prev-")

    def test_no_earlier_note_says_so_in_words(self):
        line = R.previous_release_line("1.0.0", self.d)
        self.assertIn("No earlier release note", line)

    def test_a_placeholder_note_says_no_data_in_words_never_a_hash(self):
        make_note(self.d, "0.9.11.md",
                  "# Brother 0.9.11\n\n## Source revision\n\n"
                  "Stamped by the exporter at release time.\n\n## What this release carries\n")
        line = R.previous_release_line("1.0.0", self.d)
        self.assertIn("0.9.11.md", line)
        self.assertIn("no revision stamp", line)
        self.assertNotRegex(line, r"[0-9a-f]{16,}")

    def test_a_really_stamped_note_prints_its_real_hash(self):
        make_note(self.d, "0.9.11.md",
                  "# Brother 0.9.11\n\n## Source revision\n\n"
                  "Cut from hub commit `deadbeefcafef00d`. Reproduce the export "
                  "byte for byte with:\n\n    python3 scripts/reproduce_export.py "
                  "--source-rev deadbeefcafef00d --tag v0.9.11 --public <x>\n\n"
                  "## What this release carries\n")
        line = R.previous_release_line("1.0.0", self.d)
        self.assertIn("0.9.11.md", line)
        self.assertIn("deadbeefcafef00d", line)

    def test_picks_the_highest_version_below_the_target_not_the_newest_file(self):
        make_note(self.d, "0.9.9.md", "# Brother 0.9.9\n")
        make_note(self.d, "0.9.11.md",
                  "# Brother 0.9.11\n\n## Source revision\n\n"
                  "Cut from hub commit `abc123abc123abc1`. more\n")
        path = R.previous_release_note_path("1.0.0", self.d)
        self.assertEqual(os.path.basename(path), "0.9.11.md")

    def test_an_unrecognized_shape_yields_no_sentence_and_warns_on_stderr(self):
        """A zero context critic read the shipped 1.0.0 note's own line for
        this branch ("carries a Source revision section in a shape this
        script does not recognize, so no revision is printed here") and
        correctly called it a cut script talking to itself: a release note
        is for a reader of THIS release, never a diagnostic about this
        generator's own parser. The unrecognized shape now yields the
        empty string here (never that sentence, never any sentence), and
        the reason is printed to stderr instead."""
        make_note(self.d, "0.9.11.md",
                  "# Brother 0.9.11\n\n## Source revision\n\n"
                  "Something about a hub commit, but not in the shape "
                  "this generator parses.\n\n## What this release carries\n")
        with contextlib.redirect_stderr(io.StringIO()) as err:
            line = R.previous_release_line("1.0.0", self.d)
        self.assertEqual(line, "")
        self.assertIn("0.9.11.md", err.getvalue())

    def test_the_generated_note_carries_no_self_referential_sentence_either(self):
        """End to end through build(): the offending sentence never
        reaches the actual note, and no "Previous release" paragraph
        stands in for it (build() simply omits the paragraph, per the
        empty-string contract above)."""
        make_note(self.d, "0.9.11.md",
                  "# Brother 0.9.11\n\n## Source revision\n\n"
                  "Something about a hub commit, but not in the shape "
                  "this generator parses.\n\n## What this release carries\n")
        with mock.patch.object(R, "RELEASES_DIR", self.d):
            body, problems = R.build()
        self.assertEqual(problems, [], problems)
        self.assertIsNotNone(body)
        self.assertNotIn("shape this", body)
        self.assertNotIn("does not recognize", body)
        self.assertNotIn("0.9.11.md", body)


class E80TheNoteIsCheckableFromAPublicClone(unittest.TestCase):
    """The note's headline claim used to be a hub commit a clone cannot
    resolve, so the reproduction could not start. These drive what replaced
    it: a manifest digest, a tag, and a command that runs from the tag."""

    @classmethod
    def setUpClass(cls):
        # setUpClass, not setUp: build() runs all six cited suites, so a
        # per-test build would run them six times over for one note.
        sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
        import reproduce_export as RE
        cls.RE = RE
        cls.body, cls.problems = R.build()

    def setUp(self):
        self.assertEqual(self.problems, [], self.problems)
        self.assertIsNotNone(self.body)

    def test_the_note_states_a_digest_the_reproduction_reads_back(self):
        m = self.RE.NOTE_DIGEST_RE.search(self.body)
        self.assertIsNotNone(
            m, "the note states no digest in the shape reproduce_export reads")
        self.assertEqual(len(m.group(1)), 64)

    def test_the_stated_digest_is_the_real_manifest_digest_not_a_placeholder(self):
        text, digest, count, problem = R.export_manifest()
        self.assertIsNone(problem, problem)
        self.assertIn("`%s`" % digest, self.body)
        self.assertGreater(count, 0)
        self.assertEqual(digest, self.RE.manifest_digest(text))

    def test_the_note_names_the_manifest_file_that_ships_with_the_tag(self):
        self.assertIn(self.RE.manifest_path_for(R.default_version()),
                       self.body)

    def test_the_note_gives_a_command_that_needs_no_hub_access(self):
        self.assertIn("scripts/reproduce_export.py --verify-tree --tag v%s"
                       % R.default_version(), self.body)

    def test_the_hub_commit_is_labelled_private_and_not_the_claim(self):
        # It stays in the note (the private audit trail needs it) but a
        # reader must be told plainly that a clone cannot resolve it.
        self.assertIn("(hub, private", self.body)
        self.assertIn("cannot resolve that revision", self.body)

    def test_the_manifest_never_covers_the_release_notes_it_is_quoted_in(self):
        text, _digest, _count, problem = R.export_manifest()
        self.assertIsNone(problem, problem)
        covered = [l.split("  ", 1)[1] for l in text.splitlines()]
        self.assertEqual(
            [p for p in covered
             if p.startswith(self.RE.MANIFEST_EXCLUDED_PREFIX)], [])
        # and it does cover the real shipped runtime, so the exclusion is a
        # narrow one, not an empty manifest wearing a digest.
        self.assertIn("bundle/runtime/brother_run.py", covered)


class GapSixParsingIsPureAndTested(unittest.TestCase):
    """The parser is exercised directly with canned unittest output; nothing
    here spawns a subprocess."""

    def test_a_suite_that_cannot_run_is_not_ok(self):
        r = R.run_suite("scripts/this_suite_does_not_exist_anywhere.py")
        self.assertFalse(r["ok"], r)
        self.assertIsNone(r["n"])

    def test_a_missing_ran_line_parses_to_total_None(self):
        parsed = R.parse_unittest_output("some unexpected crash output\n")
        self.assertIsNone(parsed["total"])

    def test_a_failed_summary_is_not_ok(self):
        out = ("test_a ... FAIL\n\n----------------------------------------\n"
               "Ran 3 tests in 0.1s\n\nFAILED (failures=1)\n")
        r = R.evaluate_suite_output(1, out)
        self.assertFalse(r["ok"], r)


class GapSevenTheScopingSentenceIsReadNeverRetyped(unittest.TestCase):
    def test_the_generated_note_carries_the_exact_constant(self):
        body, problems = R.build()
        self.assertEqual(problems, [], problems)
        self.assertIsNotNone(body)
        self.assertIn(RD.SCOPING_SENTENCE, body)


class Finding1TheNoteNamesThePublicTagTheHubHashCannotResolveTo(unittest.TestCase):
    """A zero context auditor asking the public repository for the hub commit
    the note stamps got HTTP 422: the hub is private, so a stranger reading
    the note on the public repository has nothing that resolves. The public
    repository resolves TAGS, so the note must also name the tag the cut
    publishes as, read from scripts/cut_v1.0.0.sh's own TAG= and
    PUBLIC_REMOTE= lines, never retyped and never invented."""

    def make_cut_script(self, dirpath, body):
        p = os.path.join(dirpath, "cut_v1.0.0.sh")
        with open(p, "w", encoding="utf-8") as fh:
            fh.write(body)
        return p

    def test_the_real_note_carries_the_published_as_sentence_with_the_real_tag(self):
        """scripts/cut_v1.0.0.sh's own TAG= line reads `TAG=v$VERSION`
        since the cut script took a version argument (2026-09-03): its raw
        source text is no longer a resolved tag, so the expected tag here
        is built from R.default_version() (the version build() actually
        used), never read back off that line. PUBLIC_REMOTE is unaffected
        by versioning and is still read from the script."""
        body, problems = R.build()
        self.assertEqual(problems, [], problems)
        self.assertIsNotNone(body)
        _, remote = R.cut_script_tag_and_remote()
        self.assertIn("Published as tag v%s on" % R.default_version(), body)
        self.assertIn(remote.split("://", 1)[-1], body)

    def test_reads_tag_and_remote_from_the_scripts_own_variable_lines(self):
        d = tempfile.mkdtemp(prefix="relnote-cutscript-")
        p = self.make_cut_script(
            d, "#!/bin/sh\nset -e\nTAG=v9.9.9\nPUBLIC_REMOTE=https://example.com/x/y\necho hi\n")
        self.assertEqual(R.cut_script_tag_and_remote(p), ("v9.9.9", "https://example.com/x/y"))
        line = R.published_as_line(p)
        self.assertIn("Published as tag v9.9.9 on example.com/x/y", line)

    def test_a_scratch_script_without_a_tag_variable_refuses_with_no_data_not_a_guess(self):
        d = tempfile.mkdtemp(prefix="relnote-cutscript-notag-")
        p = self.make_cut_script(
            d, "#!/bin/sh\nset -e\nPUBLIC_REMOTE=https://example.com/x/y\necho hi\n")
        self.assertIsNone(R.cut_script_tag_and_remote(p))
        self.assertIsNone(R.published_as_line(p))

    def test_a_scratch_script_without_a_remote_variable_also_refuses(self):
        d = tempfile.mkdtemp(prefix="relnote-cutscript-noremote-")
        p = self.make_cut_script(d, "#!/bin/sh\nset -e\nTAG=v9.9.9\necho hi\n")
        self.assertIsNone(R.cut_script_tag_and_remote(p))

    def test_an_unreadable_cut_script_path_refuses_rather_than_crash(self):
        self.assertIsNone(R.cut_script_tag_and_remote("/no/such/cut/script/anywhere.sh"))


class Finding2TheCutRefusesWhilePlaceholderNotesShip(unittest.TestCase):
    """The shipped 0.9.11 note kept "Stamped by the exporter at release time."
    because the stamper could not fire, and nothing refused the cut while it
    stood. scripts/release_notes_stamped.py is the guard scripts/cut_v1.0.0.sh
    now calls after regenerating the 1.0.0 note; this drives that guard
    directly against a temp docs/releases directory."""

    def setUp(self):
        sys.path.insert(0, R.HERE)
        import release_notes_stamped as G  # noqa: E402
        self.G = G
        self.d = tempfile.mkdtemp(prefix="relnote-guard-")

    def test_a_note_that_is_still_the_placeholder_block_is_named_and_refused(self):
        make_note(self.d, "0.9.11.md",
                  "# Brother 0.9.11\n\n## Source revision\n\n"
                  "Stamped by the exporter at release time.\n\n## What this release carries\n")
        self.assertEqual(self.G.offending_notes(self.d), ["0.9.11.md"])

    def test_a_fully_stamped_set_of_notes_passes_clean(self):
        make_note(self.d, "0.9.11.md",
                  "# Brother 0.9.11\n\n## Source revision\n\n"
                  "Cut from hub commit `deadbeefcafef00d`.\n\n## What this release carries\n")
        make_note(self.d, "1.0.0.md",
                  "# Brother 1.0.0\n\n## Source revision\n\n"
                  "Cut from hub commit `abc123abc123abc1`.\n\n"
                  "`docs/releases/0.9.11.md` carries no revision stamp (still the "
                  "placeholder \"Stamped by the exporter at release time.\"), so the "
                  "change set since it cannot be enumerated from the record.\n\n"
                  "## What this release carries\n")
        self.assertEqual(self.G.offending_notes(self.d), [])

    def test_a_note_that_only_quotes_the_placeholder_while_stamped_itself_is_not_flagged(self):
        """The exact defect a plain substring search would cause: the real
        docs/releases/1.0.0.md quotes the placeholder inside its own
        Previous release sentence about 0.9.11.md, while its own Source
        revision section carries a real hub commit. That quote must never
        make a correctly stamped note refuse."""
        make_note(self.d, "1.0.0.md",
                  "# Brother 1.0.0\n\n## Source revision\n\n"
                  "Cut from hub commit `abc123abc123abc1`.\n\n"
                  "`docs/releases/0.9.11.md` carries no revision stamp (still the "
                  "placeholder \"Stamped by the exporter at release time.\"), so the "
                  "change set since it cannot be enumerated from the record.\n\n"
                  "## What this release carries\n")
        self.assertEqual(self.G.offending_notes(self.d), [])

    def test_main_exits_nonzero_and_names_the_file_on_stderr_when_refused(self):
        make_note(self.d, "0.9.10.md",
                  "# Brother 0.9.10\n\n## Source revision\n\n"
                  "Stamped by the exporter at release time.\n\n## What this release carries\n")
        real_dir = self.G.RELEASES_DIR
        self.G.RELEASES_DIR = self.d
        try:
            self.assertEqual(self.G.main([]), 1)
        finally:
            self.G.RELEASES_DIR = real_dir

    def test_the_real_docs_releases_verdict_matches_the_tree(self):
        """The truth this guard exists to enforce, right now: offending_notes()
        must agree, filename for filename, with an independent scan of the
        real docs/releases/*.md for the same placeholder block, and 1.0.0.md
        (the note this repository ships stamped) must never appear in it."""
        header = self.G.EXP.SOURCE_REVISION_HEADER
        placeholder = self.G.EXP.SOURCE_REVISION_PLACEHOLDER
        block = "%s\n\n%s\n\n" % (header, placeholder)
        expected = []
        for name in sorted(os.listdir(R.RELEASES_DIR)):
            if not name.endswith(".md"):
                continue
            with open(os.path.join(R.RELEASES_DIR, name), encoding="utf-8") as fh:
                text = fh.read()
            if block in text:
                expected.append(name)
        bad = self.G.offending_notes()
        self.assertEqual(bad, expected)
        self.assertNotIn("1.0.0.md", bad)

    def test_an_unreadable_note_is_refused_and_a_readable_stamped_note_is_not(self):
        """offending_notes() used to skip a note it could not open, in
        silence, so an unreadable file could slip an unverifiable note past
        the guard. It now counts an unreadable file as offending too,
        refusing rather than passing it through."""
        if os.geteuid() == 0:
            self.skipTest("root ignores file permissions, chmod 0 would not "
                          "make the file unreadable")
        make_note(self.d, "1.0.0.md",
                  "# Brother 1.0.0\n\n## Source revision\n\n"
                  "Cut from hub commit `abc123abc123abc1`.\n\n## What this release carries\n")
        unreadable = make_note(self.d, "0.9.11.md",
                  "# Brother 0.9.11\n\n## Source revision\n\n"
                  "Cut from hub commit `deadbeefcafef00d`.\n\n## What this release carries\n")
        os.chmod(unreadable, 0)
        try:
            self.assertEqual(self.G.offending_notes(self.d), ["0.9.11.md"])
        finally:
            os.chmod(unreadable, 0o644)


class TheVersionParameterCutsAnyRelease(unittest.TestCase):
    """scripts/cut_v1.0.0.sh takes VERSION as its own first argument now
    (2026-09-03) and passes --version through; this generator must be able
    to describe any release, not only 1.0.0, and write the matching
    docs/releases/<version>.md rather than a fixed filename."""

    def test_a_non_default_version_names_itself_throughout_the_note(self):
        body, problems = R.build("1.0.1")
        self.assertEqual(problems, [], problems)
        self.assertIsNotNone(body)
        self.assertIn("# Brother 1.0.1", body)
        self.assertIn("Published as tag v1.0.1 on", body)

    def test_main_write_version_1_0_1_writes_docs_releases_1_0_1_md(self):
        # Into a temp releases dir, never the real one: once 1.0.1 was cut
        # for real (2026-09-03) the old form of this test found the real
        # note "left over" and, had that assertion been relaxed, would have
        # deleted the real release note in its cleanup.
        tmp = tempfile.mkdtemp(prefix="release-note-test-")
        try:
            with mock.patch.object(R, "RELEASES_DIR", tmp):
                target = R.notes_path_for("1.0.1")
                self.assertEqual(os.path.dirname(target), tmp)
                code = R.main(["--write", "--version", "1.0.1"])
                self.assertEqual(code, 0)
                self.assertTrue(os.path.isfile(target))
                # E80: the export manifest must land beside the note, in the
                # SAME redirected directory. It used to be built from ROOT,
                # so this test wrote a manifest for an already-cut version
                # into the real docs/releases/ every time it ran.
                manifest = R.manifest_write_path_for("1.0.1")
                self.assertEqual(os.path.dirname(manifest), tmp)
                self.assertTrue(os.path.isfile(manifest), manifest)
                self.assertFalse(
                    os.path.exists(os.path.join(
                        R.ROOT, "docs", "releases",
                        "1.0.1.export-manifest.txt")),
                    "--write leaked an export manifest into the real "
                    "docs/releases/ while RELEASES_DIR was redirected")
                with open(target, encoding="utf-8") as fh:
                    text = fh.read()
                self.assertIn("# Brother 1.0.1", text)
        finally:
            shutil.rmtree(tmp, ignore_errors=True)

    def test_default_version_reads_the_real_marketplace_metadata(self):
        # Compared against the file itself, never a typed version: the
        # typed form went red at the first bump after it was written.
        with open(R.MARKETPLACE_JSON, encoding="utf-8") as fh:
            declared = json.load(fh)["metadata"]["version"]
        self.assertRegex(declared, r"^\d+\.\d+\.\d+$")
        self.assertEqual(R.default_version(), declared)


class TheFilesTableIsTheSuitesOwnImports(unittest.TestCase):
    """BO2, closing the 2026-09-04 delivery-proof skeptic's defect 8: the
    table's own sentence says the files were read from each suite's
    imports, and for scripts/test_brother_run.py it listed scripts/loom.py
    (never imported, and the suite stays green with loom.py's behaviour
    disabled) while omitting scripts/claim_store.py and scripts/decide.py
    (both imported). The reader is now the parsed syntax tree, so the
    sentence and the column cannot disagree."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="subject-files-")

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_every_import_shape_is_read_and_nothing_else_is(self):
        names = R.imported_module_names(
            "import claim_store\n"
            "import receipt_door as RD\n"
            "import os, sys\n"
            "from decide import render\n"
            "from a.b import c\n"
            "from . import sibling\n"
            "HOOK = 'loom.py'\n"
            "fake = os.path.join(tmp, \"bm_vault.py\")\n")
        self.assertEqual(names, ["a", "claim_store", "decide", "os",
                                 "receipt_door", "sys"])

    def test_a_file_that_does_not_parse_yields_nothing_rather_than_a_guess(
            self):
        self.assertEqual(R.imported_module_names("import ((("), [])

    def test_a_name_only_mentioned_in_a_string_is_not_a_subject_file(self):
        """The exact v1.0.1 mechanism, driven backwards: a suite that
        writes a fake `loom.py` beside itself, and never imports it, must
        not appear to be testing loom.py."""
        for name in ("loom.py", "receipt_door.py", "test_thing.py"):
            with open(os.path.join(self.tmp, name), "w",
                      encoding="utf-8") as fh:
                fh.write("x = 1\n")
        with open(os.path.join(self.tmp, "test_thing.py"), "w",
                  encoding="utf-8") as fh:
            fh.write("import receipt_door\n"
                     "FAKE = os.path.join(tmp, 'loom.py')\n")
        with mock.patch.object(R, "ROOT", self.tmp):
            subjects = R.subject_files("test_thing.py")
        self.assertEqual(subjects, ["receipt_door.py"])

    def test_a_suite_is_never_listed_as_its_own_subject(self):
        with open(os.path.join(self.tmp, "test_thing.py"), "w",
                  encoding="utf-8") as fh:
            fh.write("import test_thing\n")
        with mock.patch.object(R, "ROOT", self.tmp):
            self.assertEqual(R.subject_files("test_thing.py"), [])

    def test_the_brother_run_row_names_what_that_suite_really_imports(self):
        """Read against the real suite in this tree, not a fixture: this is
        the row the skeptic disproved."""
        rel = "scripts/test_brother_run.py"
        subjects = R.subject_files(rel)
        self.assertNotIn("scripts/loom.py", subjects,
                         "the table still names a file the suite does not "
                         "import: %s" % subjects)
        self.assertIn("scripts/claim_store.py", subjects, subjects)
        self.assertIn("scripts/decide.py", subjects, subjects)

    def test_every_row_equals_that_suites_own_imports(self):
        """The table and the sentence above it, held together for every
        suite the note cuts, in this tree as it stands."""
        for _label, rel in R.SUITES:
            path = os.path.join(R.ROOT, rel)
            with open(path, encoding="utf-8") as fh:
                imported = set(R.imported_module_names(fh.read()))
            dirn = os.path.dirname(path)
            expected = sorted(
                os.path.relpath(os.path.join(dirn, n + ".py"),
                                R.ROOT).replace(os.sep, "/")
                for n in imported
                if n + ".py" != os.path.basename(rel)
                and os.path.isfile(os.path.join(dirn, n + ".py")))
            self.assertEqual(R.subject_files(rel), expected, rel)


class TheHandWrittenParagraphIsReadFromAFileNeverTypedIntoTheNote(
        unittest.TestCase):
    """The 1.0.2 cut has to say one thing no command in the tree can
    measure: that the Codex package ships while the credentialled Codex
    task (row C7) is still pending the founder's run, so the release makes
    no compatibility claim. Editing that sentence into the generated note
    by hand would break the note's own rule that it is generator output,
    so the generator reads it from docs/releases/<version>.notes.txt. Driven
    both ways: present is carried through, absent changes nothing."""

    def setUp(self):
        self.d = tempfile.mkdtemp(prefix="extra-notes-")

    def tearDown(self):
        shutil.rmtree(self.d, ignore_errors=True)

    def test_no_file_is_empty_string_not_a_refusal(self):
        self.assertEqual(R.extra_notes("9.9.9", self.d), "")

    def test_a_present_file_is_read_and_stripped(self):
        with open(os.path.join(self.d, "9.9.9.notes.txt"), "w",
                  encoding="utf-8") as fh:
            fh.write("\n  Codex ships; C7 is pending.  \n\n")
        self.assertEqual(R.extra_notes("9.9.9", self.d),
                         "Codex ships; C7 is pending.")

    def test_an_unreadable_present_file_is_None_so_build_can_refuse(self):
        path = os.path.join(self.d, "9.9.9.notes.txt")
        with open(path, "w", encoding="utf-8") as fh:
            fh.write("x")
        os.chmod(path, 0)
        try:
            if os.access(path, os.R_OK):  # running as root: the mode is not enforced
                self.skipTest("this user can read a mode 000 file")
            self.assertIsNone(R.extra_notes("9.9.9", self.d))
        finally:
            os.chmod(path, 0o644)

    def test_the_real_note_carries_the_real_file_when_one_exists(self):
        version = R.default_version()
        text = R.extra_notes(version)
        self.assertIsNotNone(text, "the notes file exists but is unreadable")
        if not text:
            self.skipTest("this version ships no %s.notes.txt" % version)
        body, problems = R.build(version)
        self.assertEqual(problems, [])
        self.assertIn(text, body)


if __name__ == "__main__":
    unittest.main()

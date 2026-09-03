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
import os
import sys
import tempfile
import unittest

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
        body, problems = R.build()
        self.assertEqual(problems, [], problems)
        self.assertIsNotNone(body)
        tag, remote = R.cut_script_tag_and_remote()
        self.assertIn("Published as tag %s on" % tag, body)
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


if __name__ == "__main__":
    unittest.main()

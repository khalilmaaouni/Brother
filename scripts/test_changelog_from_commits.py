#!/usr/bin/env python3
"""Drive changelog_from_commits against a real throwaway git repository,
never the live hub: the whole point of this generator is to read git
history honestly, so a fixture with real merge commits is what proves it,
the same pattern scripts/test_refresh_cut.py already uses.
"""
import os
import subprocess
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import changelog_from_commits as CFC  # noqa: E402

try:
    import tmp_sandbox
    tmp_sandbox.install()
except ImportError:
    sys.stderr.write("tmp_sandbox absent: %s leaves its temp trees behind\n"
                     % os.path.basename(__file__))


def _git(args, cwd, check=True):
    proc = subprocess.run(["git"] + args, cwd=cwd, capture_output=True,
                          text=True)
    if check and proc.returncode != 0:
        raise AssertionError("fixture git failed: %s: %s" % (args, proc.stderr))
    return proc


def _write(path, text):
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(text)


def _make_repo():
    """A real git repository, tagged v1.0.0 at the seed commit, with three
    pull-request merges after it (one row-id branch each way, one with no
    row id at all) and one plain, non-merge commit on top, exactly the five
    shapes this module has to tell apart."""
    tmp = tempfile.mkdtemp(prefix="changelog-test-")
    _write(os.path.join(tmp, "README.md"), "fixture\n")
    for args in (["init", "-q", "-b", "main"],
                ["config", "user.email", "t@example.com"],
                ["config", "user.name", "T"],
                ["add", "-A"],
                ["commit", "-q", "-m", "seed"],
                ["tag", "-a", "-m", "v1.0.0", "v1.0.0"]):
        _git(args, tmp)

    # #10: a branch whose slug starts with a roadmap row id (S24).
    _git(["checkout", "-q", "-b", "wbs/s24-codex-guide"], tmp)
    _write(os.path.join(tmp, "a.txt"), "a\n")
    _git(["add", "-A"], tmp)
    _git(["commit", "-q", "-m", "add a"], tmp)
    _git(["checkout", "-q", "main"], tmp)
    _git(["merge", "--no-ff", "-q", "-m",
         "Merge pull request #10 from someone/wbs/s24-codex-guide",
         "wbs/s24-codex-guide"], tmp)

    # #11: a different row id (X8), proving grouping is per branch, not a
    # single guess for the whole range.
    _git(["checkout", "-q", "-b", "wbs/x8-rows-1.0.8"], tmp)
    _write(os.path.join(tmp, "b.txt"), "b\n")
    _git(["add", "-A"], tmp)
    _git(["commit", "-q", "-m", "add b"], tmp)
    _git(["checkout", "-q", "main"], tmp)
    _git(["merge", "--no-ff", "-q", "-m",
         "Merge pull request #11 from someone/wbs/x8-rows-1.0.8",
         "wbs/x8-rows-1.0.8"], tmp)

    # #12: a wbs branch with no row id in its slug, must fall back to the
    # "wbs" prefix rather than being dropped or mis-tagged.
    _git(["checkout", "-q", "-b", "wbs/recurrence-db-root-marker"], tmp)
    _write(os.path.join(tmp, "c.txt"), "c\n")
    _git(["add", "-A"], tmp)
    _git(["commit", "-q", "-m", "add c"], tmp)
    _git(["checkout", "-q", "main"], tmp)
    _git(["merge", "--no-ff", "-q", "-m",
         "Merge pull request #12 from someone/wbs/recurrence-db-root-marker",
         "wbs/recurrence-db-root-marker"], tmp)

    # A plain commit that names no pull request at all: "Merge main into
    # <branch>" housekeeping merges and ordinary commits both take this
    # shape and neither is a changelog entry.
    _write(os.path.join(tmp, "d.txt"), "d\n")
    _git(["add", "-A"], tmp)
    _git(["commit", "-q", "-m", "plain follow-up commit, not a pull request"], tmp)

    return tmp


class TheThreeMergesGroupCorrectly(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = _make_repo()
        cls.lines = CFC.changelog_lines("v1.0.0", "HEAD", root=cls.tmp)

    def test_a_row_id_branch_groups_under_its_uppercased_id(self):
        self.assertIn("## S24", self.lines)
        self.assertIn("- #10 wbs/s24-codex-guide", self.lines)

    def test_a_second_row_id_branch_groups_under_its_own_id_not_the_firsts(self):
        self.assertIn("## X8", self.lines)
        self.assertIn("- #11 wbs/x8-rows-1.0.8", self.lines)

    def test_a_branch_with_no_row_id_falls_back_to_the_wbs_prefix(self):
        self.assertIn("## wbs", self.lines)
        self.assertIn("- #12 wbs/recurrence-db-root-marker", self.lines)

    def test_the_plain_commit_names_no_pull_request_and_is_absent(self):
        text = "\n".join(self.lines)
        self.assertNotIn("plain follow-up commit", text)
        self.assertNotIn("#13", text)

    def test_build_wraps_the_same_lines_in_a_title_and_exits_clean(self):
        text, code = CFC.build("v1.0.0", "HEAD", root=self.tmp)
        self.assertEqual(code, 0)
        self.assertTrue(text.startswith("# Changelog v1.0.0..HEAD"), text)
        self.assertIn("## S24", text)


class AnEmptyRangeReadsNoData(unittest.TestCase):
    def test_the_same_ref_twice_holds_no_merges_to_changelog(self):
        tmp = _make_repo()
        lines = CFC.changelog_lines("v1.0.0", "v1.0.0", root=tmp)
        self.assertEqual(len(lines), 1)
        self.assertTrue(lines[0].startswith(CFC.NODATA), lines)
        self.assertIn("no pull request merges", lines[0])

    def test_build_exits_2_on_an_empty_range(self):
        tmp = _make_repo()
        text, code = CFC.build("v1.0.0", "v1.0.0", root=tmp)
        self.assertEqual(code, 2)
        self.assertTrue(text.startswith(CFC.NODATA), text)


class AMissingRefReadsNoDataRatherThanCrashing(unittest.TestCase):
    def test_an_unknown_from_ref_is_named(self):
        tmp = _make_repo()
        lines = CFC.changelog_lines("v9.9.9-does-not-exist", "HEAD", root=tmp)
        self.assertEqual(len(lines), 1)
        self.assertIn("v9.9.9-does-not-exist", lines[0])
        self.assertTrue(lines[0].startswith(CFC.NODATA), lines)

    def test_an_unknown_to_ref_is_named(self):
        tmp = _make_repo()
        lines = CFC.changelog_lines("v1.0.0", "no-such-ref", root=tmp)
        self.assertEqual(len(lines), 1)
        self.assertIn("no-such-ref", lines[0])

    def test_build_exits_2_on_a_missing_ref(self):
        tmp = _make_repo()
        text, code = CFC.build("nope", "HEAD", root=tmp)
        self.assertEqual(code, 2)
        self.assertTrue(text.startswith(CFC.NODATA), text)


def _make_repo_with_orphan_tag(with_note=True):
    """A "hub" history of three pull-request merges (#19, #20, #21) where
    #20's merge commit is the release the note below names, plus an ORPHAN
    commit tagged v1.0.9 that shares no history with any of it, exactly the
    DEL-13 shape: the public tag points somewhere the hub range subtracts
    nothing against. Returns (root, named_rev, note_path)."""
    tmp = tempfile.mkdtemp(prefix="changelog-tag-test-")
    _write(os.path.join(tmp, "README.md"), "fixture\n")
    for args in (["init", "-q", "-b", "main"],
                ["config", "user.email", "t@example.com"],
                ["config", "user.name", "T"],
                ["add", "-A"],
                ["commit", "-q", "-m", "seed"]):
        _git(args, tmp)

    # #19: lands BEFORE the named revision, must be excluded once resolved.
    _git(["checkout", "-q", "-b", "wbs/s19-old"], tmp)
    _write(os.path.join(tmp, "a.txt"), "a\n")
    _git(["add", "-A"], tmp)
    _git(["commit", "-q", "-m", "add a"], tmp)
    _git(["checkout", "-q", "main"], tmp)
    _git(["merge", "--no-ff", "-q", "-m",
         "Merge pull request #19 from someone/wbs/s19-old",
         "wbs/s19-old"], tmp)

    # #20: this merge commit IS the hub revision the release note names.
    _git(["checkout", "-q", "-b", "wbs/s20-named"], tmp)
    _write(os.path.join(tmp, "b.txt"), "b\n")
    _git(["add", "-A"], tmp)
    _git(["commit", "-q", "-m", "add b"], tmp)
    _git(["checkout", "-q", "main"], tmp)
    _git(["merge", "--no-ff", "-q", "-m",
         "Merge pull request #20 from someone/wbs/s20-named",
         "wbs/s20-named"], tmp)
    named_rev = _git(["rev-parse", "HEAD"], tmp).stdout.strip()

    # #21: lands AFTER the named revision, must survive resolution.
    _git(["checkout", "-q", "-b", "wbs/s21-new"], tmp)
    _write(os.path.join(tmp, "c.txt"), "c\n")
    _git(["add", "-A"], tmp)
    _git(["commit", "-q", "-m", "add c"], tmp)
    _git(["checkout", "-q", "main"], tmp)
    _git(["merge", "--no-ff", "-q", "-m",
         "Merge pull request #21 from someone/wbs/s21-new",
         "wbs/s21-new"], tmp)

    note_dir = os.path.join(tmp, "docs", "releases")
    os.makedirs(note_dir, exist_ok=True)
    note_path = os.path.join(note_dir, "1.0.9.md")
    if with_note:
        _write(note_path,
              "# Brother 1.0.9\n\n## Source revision\n\n"
              "Cut from hub commit `%s` (hub, private).\n" % named_rev)
        _git(["add", "-A"], tmp)
        _git(["commit", "-q", "-m", "docs: 1.0.9 release note"], tmp)

    # The public tag: an ORPHAN commit sharing no history with main, the
    # actual shape a public export cut leaves behind.
    _git(["checkout", "-q", "--orphan", "orphan-v1.0.9"], tmp)
    _git(["rm", "-rf", "-q", "."], tmp)
    _write(os.path.join(tmp, "EXPORT.txt"), "public export only\n")
    _git(["add", "-A"], tmp)
    _git(["commit", "-q", "-m", "public export of v1.0.9"], tmp)
    _git(["tag", "-a", "-m", "v1.0.9", "v1.0.9"], tmp)
    _git(["checkout", "-q", "main"], tmp)

    return tmp, named_rev, note_path


class ATagResolvesToTheHubRevisionItsNoteNames(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp, cls.named_rev, cls.note_path = _make_repo_with_orphan_tag(
            with_note=True)
        cls.lines = CFC.changelog_lines("v1.0.9", "HEAD", root=cls.tmp)

    def test_only_merges_after_the_named_revision_are_listed(self):
        text = "\n".join(self.lines)
        self.assertIn("#21", text)
        self.assertNotIn("#20", text)
        self.assertNotIn("#19", text)

    def test_the_output_says_which_hub_revision_it_resolved_to(self):
        text = "\n".join(self.lines)
        self.assertIn(self.named_rev[:12], text)
        self.assertIn("docs/releases/1.0.9.md", text)
        self.assertIn("v1.0.9", text)

    def test_build_still_wraps_it_in_the_tags_own_title(self):
        text, code = CFC.build("v1.0.9", "HEAD", root=self.tmp)
        self.assertEqual(code, 0)
        self.assertTrue(text.startswith("# Changelog v1.0.9..HEAD"), text)
        self.assertIn("#21", text)
        self.assertNotIn("#20", text)


class WithoutANoteTheOldBehaviourHoldsUnchanged(unittest.TestCase):
    def test_an_orphan_tag_with_no_note_still_diffs_the_whole_history(self):
        # This is the DEL-13 bug itself, left exactly as it was for the case
        # this fix does not touch: no note means no way to resolve, so the
        # orphan tag subtracts nothing and every merge leaks through.
        tmp, named_rev, _ = _make_repo_with_orphan_tag(with_note=False)
        lines = CFC.changelog_lines("v1.0.9", "HEAD", root=tmp)
        text = "\n".join(lines)
        self.assertIn("#19", text)
        self.assertIn("#20", text)
        self.assertIn("#21", text)

    def test_a_release_tag_shape_with_no_note_and_no_tag_reads_no_data(self):
        tmp = _make_repo()
        lines = CFC.changelog_lines("v1.0.9", "HEAD", root=tmp)
        self.assertEqual(len(lines), 1)
        self.assertTrue(lines[0].startswith(CFC.NODATA), lines)
        self.assertIn("v1.0.9", lines[0])


class GroupKeyIsAPureFunction(unittest.TestCase):
    def test_reland_prefix_is_stripped_before_grouping(self):
        self.assertEqual(CFC.group_key("reland/wbs/x8-rows-1.0.8"), "X8")

    def test_a_branch_with_no_slash_at_all_is_other(self):
        self.assertEqual(CFC.group_key("no-slash-here"), "Other")

    def test_a_fix_branch_with_a_row_id_groups_by_the_row_id_too(self):
        self.assertEqual(CFC.group_key("fix/e110-something"), "E110")

    def test_no_branch_at_all_is_other_too(self):
        # A squash or rebase landing names no branch (git records none),
        # exactly the same bucket as a branch matching neither shape above.
        self.assertEqual(CFC.group_key(None), "Other")


def _make_repo_with_three_landing_shapes():
    """A real git repository, tagged v1.0.0 at the seed commit, with one PR
    landed each of the three ways this module has to recognise (DEL-14: the
    1.0.20 cut only recognised the first, silently dropping the other two),
    plus a stray duplicate reference to the squash PR and one plain commit
    naming no pull request at all."""
    tmp = tempfile.mkdtemp(prefix="changelog-shapes-test-")
    _write(os.path.join(tmp, "README.md"), "fixture\n")
    for args in (["init", "-q", "-b", "main"],
                ["config", "user.email", "t@example.com"],
                ["config", "user.name", "T"],
                ["add", "-A"],
                ["commit", "-q", "-m", "seed"],
                ["tag", "-a", "-m", "v1.0.0", "v1.0.0"]):
        _git(args, tmp)

    # #100: the classic GitHub merge-commit shape, unchanged from before.
    _git(["checkout", "-q", "-b", "wbs/s1-merge-shape"], tmp)
    _write(os.path.join(tmp, "a.txt"), "a\n")
    _git(["add", "-A"], tmp)
    _git(["commit", "-q", "-m", "add a"], tmp)
    _git(["checkout", "-q", "main"], tmp)
    _git(["merge", "--no-ff", "-q", "-m",
         "Merge pull request #100 from someone/wbs/s1-merge-shape",
         "wbs/s1-merge-shape"], tmp)

    # #101: a "Squash and merge" landing, no merge commit at all, just a
    # single-parent commit whose subject ends "(#101)".
    _write(os.path.join(tmp, "b.txt"), "b\n")
    _git(["add", "-A"], tmp)
    _git(["commit", "-q", "-m", "Add a squashed feature (#101)"], tmp)

    # A stray duplicate reference to #101, exactly the shape a follow-up
    # commit mentioning the same PR in passing would take: must not
    # produce a second "#101" entry.
    _write(os.path.join(tmp, "b2.txt"), "b2\n")
    _git(["add", "-A"], tmp)
    _git(["commit", "-q", "-m", "follow-up mentioning (#101) again, not a new PR"], tmp)

    # #102: a rebase-merge landing, another single-parent commit, this time
    # with no "(#N)" on its subject at all (rebasing adds none) but the PR
    # number written into the body by hand.
    _write(os.path.join(tmp, "c.txt"), "c\n")
    _git(["add", "-A"], tmp)
    _git(["commit", "-q", "-m",
         "Rebase-landed fix\n\nLands the change from PR (#102) after a "
         "manual rebase onto main."], tmp)

    # A plain commit naming no pull request in any of the three shapes: a
    # direct commit, must stay absent exactly as before this fix.
    _write(os.path.join(tmp, "d.txt"), "d\n")
    _git(["add", "-A"], tmp)
    _git(["commit", "-q", "-m", "plain follow-up commit, not a pull request"], tmp)

    return tmp


class TheThreeLandingShapesAllReachTheChangelog(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = _make_repo_with_three_landing_shapes()
        cls.lines = CFC.changelog_lines("v1.0.0", "HEAD", root=cls.tmp)
        cls.text = "\n".join(cls.lines)

    def test_the_merge_commit_shape_still_groups_by_its_branch(self):
        self.assertIn("## S1", self.text)
        self.assertIn("- #100 wbs/s1-merge-shape", self.text)

    def test_the_squash_shape_lands_with_the_number_stripped_from_its_label(self):
        self.assertIn("- #101 Add a squashed feature", self.text)
        self.assertNotIn("(#101)", self.text)

    def test_the_rebase_shape_lands_from_a_body_reference(self):
        self.assertIn("- #102 Rebase-landed fix", self.text)

    def test_squash_and_rebase_land_under_other_with_no_branch(self):
        self.assertIn("## Other", self.text)

    def test_the_duplicate_reference_to_101_produces_only_one_entry(self):
        self.assertEqual(self.text.count("#101"), 1)

    def test_the_plain_commit_still_names_no_pull_request_and_stays_absent(self):
        self.assertNotIn("plain follow-up commit", self.text)
        self.assertNotIn("follow-up mentioning", self.text)


def _make_repo_with_a_custom_subject_merge():
    """A real git repository, tagged v1.0.0, with one classic GitHub
    merge-commit PR (#100, control) and one merge commit whose subject is
    NOT GitHub's own "Merge pull request #N from owner/branch" shape but
    still ends "(#200)" (DEL-15: the shape a55283573 "Bring the 1.0.20
    release cut back into main (#772)" actually is -- a two-parent merge
    with a hand-written subject, which MERGE_RE alone always misses)."""
    tmp = tempfile.mkdtemp(prefix="changelog-custom-merge-test-")
    _write(os.path.join(tmp, "README.md"), "fixture\n")
    for args in (["init", "-q", "-b", "main"],
                ["config", "user.email", "t@example.com"],
                ["config", "user.name", "T"],
                ["add", "-A"],
                ["commit", "-q", "-m", "seed"],
                ["tag", "-a", "-m", "v1.0.0", "v1.0.0"]):
        _git(args, tmp)

    _git(["checkout", "-q", "-b", "wbs/s1-control"], tmp)
    _write(os.path.join(tmp, "a.txt"), "a\n")
    _git(["add", "-A"], tmp)
    _git(["commit", "-q", "-m", "add a"], tmp)
    _git(["checkout", "-q", "main"], tmp)
    _git(["merge", "--no-ff", "-q", "-m",
         "Merge pull request #100 from someone/wbs/s1-control",
         "wbs/s1-control"], tmp)

    _git(["checkout", "-q", "-b", "release-cut-branch"], tmp)
    _write(os.path.join(tmp, "b.txt"), "b\n")
    _git(["add", "-A"], tmp)
    _git(["commit", "-q", "-m", "cut work"], tmp)
    _git(["checkout", "-q", "main"], tmp)
    _git(["merge", "--no-ff", "-q", "-m",
         "Bring the release cut back into main (#200)",
         "release-cut-branch"], tmp)

    return tmp


class AMergeCommitWithNoGitHubSubjectStillLandsViaTheSameFallback(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = _make_repo_with_a_custom_subject_merge()
        cls.text = "\n".join(CFC.changelog_lines("v1.0.0", "HEAD", root=cls.tmp))

    def test_the_classic_merge_shape_still_works_as_control(self):
        self.assertIn("- #100 wbs/s1-control", self.text)

    def test_a_merge_commit_with_a_custom_subject_lands_under_other(self):
        self.assertIn("## Other", self.text)
        self.assertIn("- #200 Bring the release cut back into main", self.text)


def _make_repo_with_one_pr_landed_twice():
    """A real git repository, tagged v1.0.0, where PR #101 lands TWICE
    under two different shapes: first as a squash commit ("Add a squashed
    feature (#101)"), then, later, as a merge commit whose custom subject
    also ends "(#101)" (the exact repro asked for: a squash subject and a
    later merge referencing it). Proves the seen_prs de-dup guard, not just
    the squash-recognition or merge-fallback paths individually: deleting
    that guard alone (with both recognisers intact) is what this fixture
    is built to catch."""
    tmp = tempfile.mkdtemp(prefix="changelog-dup-shapes-test-")
    _write(os.path.join(tmp, "README.md"), "fixture\n")
    for args in (["init", "-q", "-b", "main"],
                ["config", "user.email", "t@example.com"],
                ["config", "user.name", "T"],
                ["add", "-A"],
                ["commit", "-q", "-m", "seed"],
                ["tag", "-a", "-m", "v1.0.0", "v1.0.0"]):
        _git(args, tmp)

    _write(os.path.join(tmp, "a.txt"), "a\n")
    _git(["add", "-A"], tmp)
    _git(["commit", "-q", "-m", "Add a squashed feature (#101)"], tmp)

    _git(["checkout", "-q", "-b", "reapply-101"], tmp)
    _write(os.path.join(tmp, "b.txt"), "b\n")
    _git(["add", "-A"], tmp)
    _git(["commit", "-q", "-m", "reapply work"], tmp)
    _git(["checkout", "-q", "main"], tmp)
    _git(["merge", "--no-ff", "-q", "-m",
         "Bring PR back (#101)", "reapply-101"], tmp)

    return tmp


class ThePRSeenTwiceUnderTwoDifferentShapesIsListedOnce(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = _make_repo_with_one_pr_landed_twice()
        cls.text = "\n".join(CFC.changelog_lines("v1.0.0", "HEAD", root=cls.tmp))

    def test_101_appears_exactly_once(self):
        self.assertEqual(self.text.count("#101"), 1, self.text)

    def test_the_kept_occurrence_is_the_first_oldest_one(self):
        # The squash commit landed before the merge commit; the guard
        # keeps the first (oldest) occurrence, so the squash's own label
        # survives, not the merge's.
        self.assertIn("- #101 Add a squashed feature", self.text)
        self.assertNotIn("- #101 Bring PR back", self.text)


class TheRealA55283573RangeNowCarriesPR772(unittest.TestCase):
    """DEL-15 itself: a55283573 "Bring the 1.0.20 release cut back into
    main (#772)" is a two-parent merge commit with a subject GitHub never
    wrote, so the merge-only MERGE_RE check dropped it even after DEL-14's
    fix added squash and rebase recognition for single-parent commits.
    Runs against this tree's own real history (root=ROOT, the default),
    the exact range the review's own done-check names, skipping rather
    than failing if that history is ever not reachable from wherever this
    suite runs."""

    FROM_REF = "8c37c6f7dbf043d67af39d65949704ff8893abc5~6"
    TO_REF = "a5528357363deb43d2c3c59d2bb7566154840a2c"

    def setUp(self):
        if not CFC.ref_exists(self.FROM_REF) or not CFC.ref_exists(self.TO_REF):
            self.skipTest("%s or %s not reachable in this checkout's history"
                          % (self.FROM_REF, self.TO_REF))

    def test_772_now_appears(self):
        text = "\n".join(CFC.changelog_lines(self.FROM_REF, self.TO_REF))
        self.assertIn("#772", text, text)


class TheReal1020RangeNowCarriesItsFourMissingPRs(unittest.TestCase):
    """DEL-14 itself: the 1.0.20 release note's changelog, generated by
    `python3 scripts/changelog_from_commits.py v1.0.18 HEAD` at cut time,
    silently dropped #767, #769, #770 and #771 because all four landed as
    squash commits and this module only ever recognised merge commits. Runs
    against this tree's own real history (root=ROOT, the default), the
    same v1.0.18..<hub commit 1.0.20 was cut from> range the release note
    itself names, skipping rather than failing if that history is ever not
    reachable from wherever this suite runs."""

    FROM_REF = "v1.0.18"
    TO_REF = "9674ed8955ada5737d23ee2a1df3367fece0f1e3"

    def setUp(self):
        if not CFC.ref_exists(self.FROM_REF) or not CFC.ref_exists(self.TO_REF):
            self.skipTest("%s or %s not reachable in this checkout's history"
                          % (self.FROM_REF, self.TO_REF))

    def test_all_four_previously_missing_prs_now_appear(self):
        text = "\n".join(CFC.changelog_lines(self.FROM_REF, self.TO_REF))
        for pr in (767, 769, 770, 771):
            self.assertIn("#%d" % pr, text, text)


def main():
    return unittest.main()


if __name__ == "__main__":
    main()

"""N2: a design that stopped being true has to say so.

The complaint, in the reviewer's own words: nothing handles a requirement
changing, there is no staleness warning by default, and a design abandoned
months ago stays green. Until 2026-08-29 an absent binding block returned
SILENCE, so a dossier that had never said anything about its own freshness was
indistinguishable, at the verdict, from one bound and verified current.

Two halves are load bearing here and they pull against each other, which is why
both are pinned: the requirement must actually bite at tier two and above, and
it must NOT bite where a commit could not be named at all. The first version had
only the first half and made every tier two eval fixture unpassable, because
those all run in throwaway directories that are not repositories. A gate that
refuses a design over a fact about its DIRECTORY is a gate that gets switched
off.
"""
import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import sbe_design as D  # noqa: E402

#: A real repository, so the requirement is exercised where freshness is
#: knowable. This file's own tree is the honest choice: it is definitely a
#: repository and it definitely has a resolvable head.
REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


class TheTierRankIsConservativeAboutWhatItCannotRead(unittest.TestCase):
    def test_it_reads_the_ordinary_tiers(self):
        self.assertEqual([D._tier_rank(t) for t in ("T1", "T2", "T3")], [1, 2, 3])

    def test_an_unreadable_tier_ranks_ZERO_and_therefore_requires_nothing(self):
        """The conservative direction. A tier this cannot parse must not
        silently begin requiring a binding and failing dossiers over a string it
        misread: the requirement is added where the tier is KNOWN to be high,
        never where it is merely not known to be low."""
        for bad in ("", "banana", None, "tier two", "T", "TX"):
            self.assertEqual(D._tier_rank(bad), 0, repr(bad))

    def test_case_and_whitespace_do_not_change_the_answer(self):
        self.assertEqual(D._tier_rank("  t2 "), 2)


class ItBitesAtTierTwoAndAbove(unittest.TestCase):
    """In a real repository, where a commit could have been named."""

    def test_an_unbound_T2_dossier_is_NO_DATA(self):
        verdict, note, _ = D._binding_problem(REPO, {}, "T2")
        self.assertEqual(verdict, "NO-DATA")
        self.assertIn("not a pass", note)

    def test_an_unbound_T3_dossier_is_NO_DATA(self):
        self.assertEqual(D._binding_problem(REPO, {}, "T3")[0], "NO-DATA")

    def test_the_note_says_what_is_missing_rather_than_that_it_failed(self):
        """Nobody recorded whether this design still describes the system is a
        different sentence from the design being wrong, and the reader has to be
        able to tell them apart."""
        note = D._binding_problem(REPO, {}, "T2")[1]
        self.assertIn("no binding block", note)
        self.assertIn("still describes the system", note)


class ItStaysOutOfTheWayBelowTierTwo(unittest.TestCase):
    def test_an_unbound_T1_dossier_is_silent(self):
        """A one line reversible change should not owe a freshness ceremony."""
        self.assertEqual(D._binding_problem(REPO, {}, "T1")[0], None)

    def test_a_dossier_with_no_tier_at_all_is_silent(self):
        self.assertEqual(D._binding_problem(REPO, {}, None)[0], None)


class ItNeverDemandsWhatCannotExist(unittest.TestCase):
    """The half the first version got wrong. A binding names a commit, so in a
    tree with no git metadata there is nothing it could name and nothing this
    could check."""

    def test_an_unbound_T3_dossier_OUTSIDE_a_repository_is_silent(self):
        d = tempfile.mkdtemp()
        self.assertIsNone(D._git_dir(d), "this test needs a non-repository")
        self.assertEqual(D._binding_problem(d, {}, "T3")[0], None)

    def test_that_is_why_the_eval_fixtures_still_pass(self):
        """Stated as a test so the reason survives: every eval fixture runs in a
        throwaway directory, and requiring a binding there refused 547 scenarios
        for a fact about the directory rather than about any design."""
        d = tempfile.mkdtemp()
        self.assertEqual(D._binding_problem(d, {}, "T2")[0], None)


class TheRequirementIsNamedRatherThanBuriedInAComparison(unittest.TestCase):
    def test_the_threshold_is_a_named_constant(self):
        self.assertEqual(D.BINDING_REQUIRED_FROM_TIER, 2)

    def test_raising_the_threshold_would_release_T2(self):
        """Proves the constant is the thing actually consulted, rather than a
        number repeated somewhere else in the function."""
        original = D.BINDING_REQUIRED_FROM_TIER
        try:
            D.BINDING_REQUIRED_FROM_TIER = 3
            self.assertEqual(D._binding_problem(REPO, {}, "T2")[0], None)
            self.assertEqual(D._binding_problem(REPO, {}, "T3")[0], "NO-DATA")
        finally:
            D.BINDING_REQUIRED_FROM_TIER = original


class AnExistingBindingIsUnaffected(unittest.TestCase):
    """N2 adds a requirement where there was silence. It must not change any
    verdict a present binding already produced."""

    def test_a_malformed_binding_still_FAILs_at_every_tier(self):
        for tier in ("T1", "T2", "T3"):
            self.assertEqual(D._binding_problem(REPO, {"binding": "not a dict"}, tier)[0],
                             "FAIL", tier)

    def test_a_binding_with_no_usable_head_still_FAILs(self):
        self.assertEqual(
            D._binding_problem(REPO, {"binding": {"head": "nope"}}, "T1")[0], "FAIL")


if __name__ == "__main__":
    unittest.main()

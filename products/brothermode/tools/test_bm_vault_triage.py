#!/usr/bin/env python3
"""Calibration for tools/bm_vault_triage.py, WBS row VB6-05.

The property under test is the row's own sentence: two claims differing
only by a stated scope dimension (effective date, region, entity, source,
as-of) are SCOPED, two same-scope claims that disagree are a real
CONTRADICTION, and both classifications are driven backwards, per the row's
done-check.

No em or en dashes anywhere in this file.
"""
import os
import shutil
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import bm_vault_triage as triage  # noqa: E402


def note(front_extra, body):
    return "---\ntype: reference\nstatus: standing\n%s---\n\n# a note\n\n%s\n" % (
        front_extra, body)


class SplittingSubjectAndValue(unittest.TestCase):
    def test_a_copula_splits_subject_from_value(self):
        subject, value = triage.split_subject_value("the price is 10 dollars")
        self.assertEqual(subject, "the price")
        self.assertEqual(value, "10 dollars")

    def test_no_copula_returns_none(self):
        self.assertEqual(triage.split_subject_value("a claim with no linking verb"),
                         (None, None))

    def test_value_is_case_and_whitespace_normalized(self):
        _, value = triage.split_subject_value("the price is   10 Dollars.")
        self.assertEqual(value, "10 dollars")

    def test_a_subject_with_its_own_copula_keeps_the_whole_pre_copula_clause(self):
        # Adversarial: splitting on the FIRST copula would truncate both of
        # these to the shared fragment "the number that", pairing two
        # claims about different things. Splitting on the LAST copula keeps
        # the full subject clause, so the two subjects differ.
        subject_a, value_a = triage.split_subject_value(
            "the number that was reported is 5")
        subject_b, value_b = triage.split_subject_value(
            "the number that was audited is 7")
        self.assertEqual(subject_a, "the number that was reported")
        self.assertEqual(subject_b, "the number that was audited")
        self.assertNotEqual(subject_a, subject_b)
        self.assertEqual(value_a, "5")
        self.assertEqual(value_b, "7")


class ReadingDimensions(unittest.TestCase):
    def test_frontmatter_effective_date_resolves_to_date(self):
        dims = triage.frontmatter_dimensions("effective_date: 2025-01-01\n")
        self.assertEqual(dims, {"date": "2025-01-01"})

    def test_frontmatter_branch_resolves_to_region(self):
        dims = triage.frontmatter_dimensions("branch: tokyo\n")
        self.assertEqual(dims, {"region": "tokyo"})

    def test_claim_line_tag_resolves(self):
        dims = triage.claim_line_dimensions(" [region: JP]")
        self.assertEqual(dims, {"region": "jp"})

    def test_an_unrecognised_bracket_name_is_ignored(self):
        dims = triage.claim_line_dimensions(" [note: see also]")
        self.assertEqual(dims, {})


class ScanningARealTree(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="bm-triage-")
        self.vault = os.path.join(self.tmp, "vault")
        os.makedirs(self.vault)

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _write(self, name, front_extra, body):
        path = os.path.join(self.vault, name)
        with open(path, "w", encoding="utf-8") as fh:
            fh.write(note(front_extra, body))
        return path

    def test_zero_claims_is_a_clean_pass_not_no_data(self):
        self._write("a.md", "", "just prose, no claim syntax here")
        self.assertEqual(triage.cmd_scan(self.vault), 0)

    def test_two_claims_differing_only_by_effective_date_are_scoped(self):
        self._write("old.md", "effective_date: 2025-01-01\n",
                     "claim: the rate is 5 percent [evidence: a.md]")
        self._write("new.md", "effective_date: 2026-01-01\n",
                     "claim: the rate is 7 percent [evidence: a.md]")
        pairs, scoped, contradictions, unreadable = triage.scan(self.vault)
        self.assertEqual(len(pairs), 1)
        self.assertEqual(len(scoped), 1)
        self.assertEqual(len(contradictions), 0)
        self.assertEqual(unreadable, 0)
        self.assertEqual(scoped[0][2], "date")
        self.assertEqual(triage.cmd_scan(self.vault), 0)

    def test_two_claims_differing_only_by_region_are_scoped(self):
        self._write("tokyo.md", "region: JP\n",
                     "claim: the headcount is 100 [evidence: a.md]")
        self._write("osaka.md", "region: US\n",
                     "claim: the headcount is 200 [evidence: a.md]")
        pairs, scoped, contradictions, _unreadable = triage.scan(self.vault)
        self.assertEqual(len(pairs), 1)
        self.assertEqual(len(scoped), 1)
        self.assertEqual(len(contradictions), 0)
        self.assertEqual(scoped[0][2], "region")

    def test_two_claims_differing_only_by_asof_are_scoped(self):
        self._write("a.md", "as_of: 2025-01-01\n",
                     "claim: the headcount is 100 [evidence: a.md]")
        self._write("b.md", "as_of: 2026-01-01\n",
                     "claim: the headcount is 200 [evidence: a.md]")
        pairs, scoped, contradictions, _unreadable = triage.scan(self.vault)
        self.assertEqual(len(pairs), 1)
        self.assertEqual(len(scoped), 1)
        self.assertEqual(len(contradictions), 0)
        self.assertEqual(scoped[0][2], "asof")

    def test_two_claims_differing_only_by_entity_are_scoped(self):
        self._write("a.md", "legal_entity: acme jp\n",
                     "claim: the headcount is 100 [evidence: a.md]")
        self._write("b.md", "legal_entity: acme us\n",
                     "claim: the headcount is 200 [evidence: a.md]")
        pairs, scoped, contradictions, _unreadable = triage.scan(self.vault)
        self.assertEqual(len(pairs), 1)
        self.assertEqual(len(scoped), 1)
        self.assertEqual(len(contradictions), 0)
        self.assertEqual(scoped[0][2], "entity")

    def test_two_claims_differing_only_by_source_are_scoped(self):
        self._write("a.md", "source_system: sap\n",
                     "claim: the headcount is 100 [evidence: a.md]")
        self._write("b.md", "source_system: workday\n",
                     "claim: the headcount is 200 [evidence: a.md]")
        pairs, scoped, contradictions, _unreadable = triage.scan(self.vault)
        self.assertEqual(len(pairs), 1)
        self.assertEqual(len(scoped), 1)
        self.assertEqual(len(contradictions), 0)
        self.assertEqual(scoped[0][2], "source")

    def test_two_same_scope_claims_collide_as_a_real_contradiction(self):
        self._write("one.md", "", "claim: the rate is 5 percent [evidence: a.md]")
        self._write("two.md", "", "claim: the rate is 7 percent [evidence: a.md]")
        pairs, scoped, contradictions, unreadable = triage.scan(self.vault)
        self.assertEqual(len(pairs), 1)
        self.assertEqual(len(scoped), 0)
        self.assertEqual(len(contradictions), 1)
        self.assertEqual(unreadable, 0)
        self.assertEqual(triage.cmd_scan(self.vault), 1)

    def test_reviewer_demonstrated_false_positive_no_longer_pairs(self):
        # Adversarial regression, the reviewer's exact two sentences: both
        # split (pre-fix) to subject "the number that" on the FIRST copula,
        # becoming a false CONTRADICTION between unrelated prose. Splitting
        # on the LAST copula keeps the full clause, so this must be NO PAIR.
        self._write("one.md", "",
                     "claim: the number that was reported is 5 [evidence: a.md]")
        self._write("two.md", "",
                     "claim: the number that was audited is 7 [evidence: a.md]")
        pairs, scoped, contradictions, _unreadable = triage.scan(self.vault)
        self.assertEqual(len(pairs), 0)
        self.assertEqual(len(scoped), 0)
        self.assertEqual(len(contradictions), 0)
        self.assertEqual(triage.cmd_scan(self.vault), 0)

    def test_agreeing_claims_on_the_same_subject_are_not_a_pair(self):
        self._write("one.md", "", "claim: the rate is 5 percent [evidence: a.md]")
        self._write("two.md", "", "claim: the rate is 5 percent [evidence: a.md]")
        pairs, _scoped, _contradictions, _unreadable = triage.scan(self.vault)
        self.assertEqual(len(pairs), 0)

    def test_two_claims_in_the_same_note_are_never_paired(self):
        self._write("one.md", "",
                     "claim: the rate is 5 percent [evidence: a.md]\n"
                     "claim: the rate is 7 percent [evidence: a.md]\n")
        pairs, _scoped, _contradictions, _unreadable = triage.scan(self.vault)
        self.assertEqual(len(pairs), 0)

    def test_a_dimension_named_on_only_one_side_never_scopes_alone(self):
        self._write("one.md", "region: JP\n",
                     "claim: the rate is 5 percent [evidence: a.md]")
        self._write("two.md", "", "claim: the rate is 7 percent [evidence: a.md]")
        pairs, scoped, contradictions, _unreadable = triage.scan(self.vault)
        self.assertEqual(len(pairs), 1)
        self.assertEqual(len(scoped), 0)
        self.assertEqual(len(contradictions), 1)

    def test_an_unreadable_vault_is_no_data_exit_2(self):
        self.assertEqual(triage.main(["scan", "--vault", "/no/such/path"]), 2)

    def test_an_unreadable_file_is_counted_and_printed_never_silent(self):
        self._write("readable.md", "", "claim: the rate is 5 percent [evidence: a.md]")
        blocked = os.path.join(self.vault, "blocked.md")
        with open(blocked, "w", encoding="utf-8") as fh:
            fh.write(note("", "claim: the rate is 9 percent [evidence: a.md]"))
        os.chmod(blocked, 0)
        try:
            pairs, _scoped, _contradictions, unreadable = triage.scan(self.vault)
            self.assertEqual(unreadable, 1)
            self.assertEqual(len(pairs), 0)  # the unreadable claim never joined a pair
            self.assertEqual(triage.cmd_scan(self.vault), 0)
        finally:
            os.chmod(blocked, 0o644)  # tearDown's rmtree needs this back to delete it


class DrivenBackwards(unittest.TestCase):
    """Both classifications forced to fail by neutering the one check each
    depends on, per the row's done-check: this proves the tests actually
    exercise the dimension comparison rather than passing for free."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="bm-triage-backwards-")
        self.vault = os.path.join(self.tmp, "vault")
        os.makedirs(self.vault)
        with open(os.path.join(self.vault, "old.md"), "w", encoding="utf-8") as fh:
            fh.write(note("effective_date: 2025-01-01\n",
                          "claim: the rate is 5 percent [evidence: a.md]"))
        with open(os.path.join(self.vault, "new.md"), "w", encoding="utf-8") as fh:
            fh.write(note("effective_date: 2026-01-01\n",
                          "claim: the rate is 7 percent [evidence: a.md]"))

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_neutering_the_dimension_check_breaks_the_scoped_verdict(self):
        real_classify = triage.classify
        triage.classify = lambda a, b: ("CONTRADICTION", None)
        try:
            _pairs, scoped, contradictions, _unreadable = triage.scan(self.vault)
            self.assertEqual(len(scoped), 0)
            self.assertEqual(len(contradictions), 1)
        finally:
            triage.classify = real_classify

    def test_forcing_every_dimension_to_differ_breaks_the_contradiction_verdict(self):
        real_classify = triage.classify
        triage.classify = lambda a, b: ("SCOPED", "date")
        try:
            _pairs, scoped, contradictions, _unreadable = triage.scan(self.vault)
            self.assertEqual(len(scoped), 1)
            self.assertEqual(len(contradictions), 0)
        finally:
            triage.classify = real_classify


if __name__ == "__main__":
    unittest.main()

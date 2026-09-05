"""Tests for scripts/morning_pack.py's compute_verdict(), row J9.

Drives section 48's verdict rule with fixture flag dicts rather than a live
tree, mirroring scripts/test_readiness_gate.py's own shape (import the
module directly, assert on a pure function's return value). The three cases
are the three verdict sentences the task names explicitly; a fourth case
exercises the JAPANESE MDM ENGINEERING QUALIFIED branch section 48 also
names, so all four of section 48's sentences are reachable and tested.
"""
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import morning_pack as MP  # noqa: E402


def _base_flags(**overrides):
    """Every flag compute_verdict() reads, defaulted to a fully-green tree,
    then overridden per test so each case states only what differs."""
    flags = {
        "standard_full": True,
        "frozen_full": True,
        "negative_full": True,
        "ruleset_required": True,
        "required_fast_wired": True,
        "manifest_clean": True,
        "tag_signed": True,
        "delegation_truth_pass": True,
        "memory_recurrence_pass": True,
        "any_hidden_fail": False,
        "jbeq_mdm_seed_ready": True,
        "jbeq_mdm_zero_false_merges": True,
        "jbeq_e2e_pass": True,
        "jbeq_reconciliation_pass": True,
    }
    flags.update(overrides)
    return flags


class ComputeVerdictTests(unittest.TestCase):

    def test_ready_to_claim_competitive_lead(self):
        """Every one of section 48's five named conditions holds."""
        flags = _base_flags()
        self.assertEqual(MP.compute_verdict(flags),
                         "READY TO CLAIM COMPETITIVE LEAD")

    def test_japanese_retrieval_qualified_business_engineering_in_progress(self):
        """78/78 (and 245/245, 13/13) holds, but JBEQ-MDM is not qualified
        and CI is not enforced yet, this morning's own real shape."""
        flags = _base_flags(
            ruleset_required=False, required_fast_wired=False,
            jbeq_mdm_seed_ready=False, jbeq_e2e_pass=True)
        self.assertEqual(
            MP.compute_verdict(flags),
            "JAPANESE RETRIEVAL QUALIFIED, BUSINESS ENGINEERING IN PROGRESS")

    def test_technically_strong_not_yet_qualified(self):
        """Japanese retrieval itself is short of 100% (a critical proof is
        NO-DATA/false), so none of the three stronger verdicts apply."""
        flags = _base_flags(frozen_full=False, jbeq_mdm_seed_ready=False,
                            ruleset_required=False)
        self.assertEqual(MP.compute_verdict(flags),
                         "TECHNICALLY STRONG, NOT YET QUALIFIED")

    def test_japanese_mdm_engineering_qualified(self):
        """Retrieval is 100% AND the MDM-specific bar (seed ready, zero
        false merges, end to end and reconciliation both PASS) is met, even
        though CI is not enforced; section 48 does not gate this verdict on
        CI, only on the Japanese MDM engineering conditions."""
        flags = _base_flags(ruleset_required=False, required_fast_wired=False)
        self.assertEqual(MP.compute_verdict(flags),
                         "JAPANESE MDM ENGINEERING QUALIFIED")


if __name__ == "__main__":
    unittest.main()

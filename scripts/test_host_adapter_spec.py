#!/usr/bin/env python3
"""Tests for scripts/host_adapter_spec.py.

Run: python3 scripts/test_host_adapter_spec.py -v

The expected level set below is typed by hand, not read back from the
module under test (host_adapter_spec.LEVELS), so a change that quietly
drops or renames a level fails this suite instead of sailing through with
it.
"""
import os
import sys
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.insert(0, HERE)

import host_adapter_spec as HAS  # noqa: E402

# Pinned independently of HAS.LEVELS: the four levels the deciding
# property names, in the deciding property's own order.
EXPECTED_LEVELS = ("unsupported", "supported", "advisory", "enforced")
EXPECTED_NODATA = "NO-DATA"


class TheFourLevelsAreExactlyThese(unittest.TestCase):
    def test_levels_tuple_matches_the_pinned_set(self):
        self.assertEqual(HAS.LEVELS, EXPECTED_LEVELS)

    def test_the_four_constants_match_the_pinned_strings(self):
        self.assertEqual(HAS.UNSUPPORTED, "unsupported")
        self.assertEqual(HAS.SUPPORTED, "supported")
        self.assertEqual(HAS.ADVISORY, "advisory")
        self.assertEqual(HAS.ENFORCED, "enforced")

    def test_nodata_marker_matches_the_pinned_string(self):
        self.assertEqual(HAS.NODATA, EXPECTED_NODATA)


class CapabilityLevelPerLevel(unittest.TestCase):
    """One case per level in EXPECTED_LEVELS, each built from the pinned
    set rather than from HAS.LEVELS, so this loop still runs even if the
    module's own tuple were ever emptied by a bad edit."""

    def test_unsupported_claim_with_no_evidence_passes_through(self):
        level, reason = HAS.capability_level("net", {"claim": "unsupported"})
        self.assertEqual(level, "unsupported")
        self.assertIn("net", reason)

    def test_supported_claim_with_no_evidence_passes_through(self):
        level, reason = HAS.capability_level("resume", {"claim": "supported"})
        self.assertEqual(level, "supported")
        self.assertIn("resume", reason)

    def test_advisory_claim_with_no_evidence_passes_through(self):
        level, reason = HAS.capability_level("deny", {"claim": "advisory"})
        self.assertEqual(level, "advisory")
        self.assertIn("deny", reason)

    def test_enforced_claim_with_evidence_is_believed(self):
        level, reason = HAS.capability_level(
            "deny", {"claim": "enforced", "evidence": "suite run 2026-09-18"})
        self.assertEqual(level, "enforced")
        self.assertIn("deny", reason)
        self.assertIn("suite run 2026-09-18", reason)


class EnforcedWithoutEvidenceIsTheDecidingProperty(unittest.TestCase):
    def test_enforced_claim_with_no_evidence_key_downgrades_to_advisory(self):
        level, reason = HAS.capability_level("deny", {"claim": "enforced"})
        self.assertEqual(level, "advisory")
        self.assertNotEqual(level, "enforced")
        self.assertIn("deny", reason)

    def test_enforced_claim_with_empty_evidence_downgrades_to_advisory(self):
        level, _ = HAS.capability_level(
            "deny", {"claim": "enforced", "evidence": ""})
        self.assertEqual(level, "advisory")

    def test_enforced_claim_with_whitespace_only_evidence_downgrades(self):
        level, _ = HAS.capability_level(
            "deny", {"claim": "enforced", "evidence": "   \t  "})
        self.assertEqual(level, "advisory")

    def test_enforced_claim_with_non_string_evidence_downgrades(self):
        level, _ = HAS.capability_level(
            "deny", {"claim": "enforced", "evidence": 12345})
        self.assertEqual(level, "advisory")

    def test_downgraded_reason_never_claims_enforced(self):
        _, reason = HAS.capability_level("deny", {"claim": "enforced"})
        self.assertNotIn("claimed enforced, evidence", reason)


class MissingOrUnknownNeverReadsAsEnforced(unittest.TestCase):
    def test_none_report_is_unsupported_nodata(self):
        level, reason = HAS.capability_level("deny", None)
        self.assertEqual(level, "unsupported")
        self.assertTrue(reason.startswith(EXPECTED_NODATA))

    def test_string_report_is_unsupported_nodata(self):
        level, reason = HAS.capability_level("deny", "enforced")
        self.assertEqual(level, "unsupported")
        self.assertTrue(reason.startswith(EXPECTED_NODATA))

    def test_list_report_is_unsupported_nodata(self):
        level, reason = HAS.capability_level("deny", ["enforced"])
        self.assertEqual(level, "unsupported")
        self.assertTrue(reason.startswith(EXPECTED_NODATA))

    def test_missing_claim_key_is_unsupported_nodata(self):
        level, reason = HAS.capability_level("deny", {"evidence": "x"})
        self.assertEqual(level, "unsupported")
        self.assertTrue(reason.startswith(EXPECTED_NODATA))

    def test_unknown_claim_string_is_unsupported_nodata(self):
        level, reason = HAS.capability_level("deny", {"claim": "maybe"})
        self.assertEqual(level, "unsupported")
        self.assertTrue(reason.startswith(EXPECTED_NODATA))
        self.assertIn("maybe", reason)

    def test_wrong_case_claim_is_unsupported_nodata(self):
        # "Enforced" is not "enforced": case matters, nothing is guessed.
        level, reason = HAS.capability_level("deny", {"claim": "Enforced"})
        self.assertEqual(level, "unsupported")
        self.assertTrue(reason.startswith(EXPECTED_NODATA))

    def test_non_string_claim_is_unsupported_nodata(self):
        level, reason = HAS.capability_level("deny", {"claim": 1})
        self.assertEqual(level, "unsupported")
        self.assertTrue(reason.startswith(EXPECTED_NODATA))

    def test_empty_report_mapping_is_unsupported_nodata(self):
        level, reason = HAS.capability_level("deny", {})
        self.assertEqual(level, "unsupported")
        self.assertTrue(reason.startswith(EXPECTED_NODATA))


class CapabilityLevelsOverAMapping(unittest.TestCase):
    def test_empty_mapping_returns_empty_dict(self):
        self.assertEqual(HAS.capability_levels({}), {})

    def test_one_entry_returns_one_result(self):
        out = HAS.capability_levels({"deny": {"claim": "advisory"}})
        self.assertEqual(set(out), {"deny"})
        self.assertEqual(out["deny"][0], "advisory")

    def test_many_entries_each_graded_independently(self):
        out = HAS.capability_levels({
            "deny": {"claim": "enforced", "evidence": "measured"},
            "resume": {"claim": "enforced"},  # no evidence: downgrades
            "isolation": {"claim": "supported"},
            "net": {"claim": "bogus"},
        })
        self.assertEqual(out["deny"][0], "enforced")
        self.assertEqual(out["resume"][0], "advisory")
        self.assertEqual(out["isolation"][0], "supported")
        self.assertEqual(out["net"][0], "unsupported")
        self.assertTrue(out["net"][1].startswith(EXPECTED_NODATA))

    def test_none_reports_input_is_a_single_nodata_entry_not_empty_dict(self):
        out = HAS.capability_levels(None)
        self.assertEqual(set(out), {"reports"})
        level, reason = out["reports"]
        self.assertEqual(level, "unsupported")
        self.assertTrue(reason.startswith(EXPECTED_NODATA))

    def test_non_mapping_reports_input_is_a_single_nodata_entry(self):
        out = HAS.capability_levels(["deny", "resume"])
        self.assertEqual(set(out), {"reports"})
        self.assertTrue(out["reports"][1].startswith(EXPECTED_NODATA))


if __name__ == "__main__":
    unittest.main()

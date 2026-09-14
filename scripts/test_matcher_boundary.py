#!/usr/bin/env python3
"""Calibration for scripts/matcher_boundary.py (WBS-40.04).

The property under test: a structurally complete MatcherOutput whose
normalizer_version is real and current audits PASS; any missing required
field is named and FAILs; a normalizer_version this codebase's own
normalization_trace.py does not currently declare FAILs as stale, not just
as malformed; and a reference_snapshot mismatch is its own reference_drifted
flag, distinct from and not causing a hard FAIL. Synthetic fixture data
only (fake source system, fake candidate ids), never anything resembling
a real client's data.
"""
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import matcher_boundary as MB  # noqa: E402


def valid_output(**overrides):
    """A structurally complete, currently-valid MatcherOutput fixture."""
    output = {
        "source_id": "acme-crm:cust-001",
        "candidate_id": "mdm:ref-777",
        "pathway": "fuzzy_name_phone_block",
        "score": 0.93,
        "model_version": "acme-matcher-2.3.1",
        "normalizer_version": MB.normalizer_version_fingerprint(),
        "blocker": "phone_last4=1234",
        "verifier_state": "UNVERIFIED",
        "reference_snapshot": "sha256:deadbeef",
    }
    output.update(overrides)
    return output


class AuditMatcherOutputTests(unittest.TestCase):
    def test_complete_valid_output_passes(self):
        result = MB.audit_matcher_output(
            valid_output(), MB.current_normalizer_versions())
        self.assertEqual(result["verdict"], MB.PASS)
        self.assertEqual(result["problems"], [])

    def test_reference_id_accepted_in_place_of_candidate_id(self):
        output = valid_output()
        del output["candidate_id"]
        output["reference_id"] = "mdm:ref-777"
        result = MB.audit_matcher_output(output, MB.current_normalizer_versions())
        self.assertEqual(result["verdict"], MB.PASS)

    def test_missing_required_field_fails_and_names_it(self):
        output = valid_output()
        del output["blocker"]
        result = MB.audit_matcher_output(output, MB.current_normalizer_versions())
        self.assertEqual(result["verdict"], MB.FAIL)
        self.assertTrue(
            any("blocker" in p for p in result["problems"]),
            "expected 'blocker' named in problems, got %r" % (result["problems"],))

    def test_missing_both_candidate_and_reference_id_fails(self):
        output = valid_output()
        del output["candidate_id"]
        result = MB.audit_matcher_output(output, MB.current_normalizer_versions())
        self.assertEqual(result["verdict"], MB.FAIL)
        self.assertTrue(
            any("candidate_id" in p and "reference_id" in p for p in result["problems"]))

    def test_non_numeric_score_fails(self):
        output = valid_output(score="high")
        result = MB.audit_matcher_output(output, MB.current_normalizer_versions())
        self.assertEqual(result["verdict"], MB.FAIL)
        self.assertTrue(any("score" in p for p in result["problems"]))

    def test_unreal_normalizer_version_fails_as_stale(self):
        output = valid_output(normalizer_version="nfkc=999.0|made_up=1.0")
        result = MB.audit_matcher_output(output, MB.current_normalizer_versions())
        self.assertEqual(result["verdict"], MB.FAIL)
        self.assertTrue(any("normalizer_version" in p for p in result["problems"]))

    def test_normalizer_version_fingerprint_is_built_from_real_transforms(self):
        # Not a free string: it must be traceable back to the live
        # normalization_trace chain's own declared name=version pairs.
        import normalization_trace as NT
        fingerprint = MB.normalizer_version_fingerprint()
        for t in NT.default_chain():
            self.assertIn("%s=%s" % (t.name, t.version), fingerprint)

    def test_reference_snapshot_match_does_not_flag_drift(self):
        output = valid_output(reference_snapshot="sha256:current")
        result = MB.audit_matcher_output(
            output, MB.current_normalizer_versions(),
            current_reference_digest="sha256:current")
        self.assertEqual(result["verdict"], MB.PASS)
        self.assertFalse(result["reference_drifted"])

    def test_reference_snapshot_mismatch_flags_drift_without_failing(self):
        # The explicit distinction the roadmap calls out: staleness is
        # reported, not treated as a hard FAIL -- the rest of the output
        # is otherwise structurally complete and current.
        output = valid_output(reference_snapshot="sha256:stale-from-match-time")
        result = MB.audit_matcher_output(
            output, MB.current_normalizer_versions(),
            current_reference_digest="sha256:current-reference-state")
        self.assertEqual(result["verdict"], MB.PASS)
        self.assertTrue(result["reference_drifted"])

    def test_no_current_reference_digest_supplied_reports_no_drift(self):
        output = valid_output()
        result = MB.audit_matcher_output(output, MB.current_normalizer_versions())
        self.assertFalse(result["reference_drifted"])

    def test_non_dict_output_fails(self):
        result = MB.audit_matcher_output("not a dict", MB.current_normalizer_versions())
        self.assertEqual(result["verdict"], MB.FAIL)


if __name__ == "__main__":
    unittest.main(verbosity=2)

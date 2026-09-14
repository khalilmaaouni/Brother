#!/usr/bin/env python3
"""Test for canary_pipeline_smoke.py (WBS-30.10): the full ten-module mobile
pipeline run as ONE real test, not ten separate mocked assertions. Proves
the synthetic canary journey's own real output threads through every real
sibling function and lands in a well-formed Journey Passport whose
completeness object is honest about what is real evidence, what is a
synthetic stand-in, and what is genuine NO-DATA -- including the one
adjacent-stage shape mismatch this run surfaces (release_state_tracker's
real output vs. journey_passport's release-dimension expectation)."""
import sys
import unittest

sys.path.insert(0, __file__.rsplit("/", 1)[0])
import canary_pipeline_smoke as CPS  # noqa: E402


class CanaryPipelineSmokeTests(unittest.TestCase):

    def setUp(self):
        self.passport, self.mismatches, self.tmp = CPS.run_pipeline()

    def test_passport_is_well_formed(self):
        self.assertEqual(self.passport["schema_version"], "journey-passport-v1")
        self.assertEqual(self.passport["journey"], "canary-smoke")
        self.assertNotEqual(self.passport["candidate_revision"], "NO-DATA")
        for key in ("contract", "build_identity", "functional", "accessibility",
                    "performance", "visual", "physical_device", "release",
                    "production", "field_verdicts", "completeness"):
            self.assertIn(key, self.passport)

    def test_the_four_stages_with_real_synthetic_evidence_actually_connect(self):
        # Each of these fields is fed the REAL output of the REAL upstream
        # function (mobile_journey_contract.check, mobile_reference_lock.
        # partial_reference, native_evidence_v2.wrap_v2,
        # device_matrix.physical_device_evidence). A PASS here proves the
        # shapes genuinely fit end to end, not just that each module works
        # standalone.
        self.assertEqual(self.passport["contract"]["verdict"], "PASS")
        self.assertEqual(self.passport["build_identity"]["verdict"], "PASS")
        self.assertEqual(self.passport["functional"]["verdict"], "PASS")
        self.assertEqual(self.passport["physical_device"]["verdict"], "PASS")

    def test_release_dimension_now_composes_the_real_states_shape(self):
        # release_state_tracker.compose_release_record()'s real output has
        # no top-level "state" key (it has "states", five of them). This
        # smoke test ORIGINALLY caught journey_passport.py requiring
        # "state" and FAILing on every real release_state_tracker record --
        # a genuine adjacent-stage mismatch, fixed in journey_passport.py
        # by _compose_release() reading the real "states" key. What this
        # test now proves is the FIXED, honest behavior: worst-of across
        # five states that are mostly NO-DATA tonight (no live App Store
        # Connect credential) composes to NO-DATA overall, never a false
        # PASS and never the old false FAIL from the shape mismatch.
        self.assertEqual(self.passport["release"]["verdict"], "NO-DATA")
        self.assertIn("states", self.passport["release"]["record"])

    def test_no_sibling_module_dimensions_are_honest_no_data_not_fabricated(self):
        # accessibility and performance: no sibling module exists at all
        # yet. production: WBS-30.11 proved mobile scores no claim, so no
        # claims module exists either. release: real evidence exists
        # (release_state_tracker) but four of five states are honestly
        # unmeasured tonight (no live credential), so the composed verdict
        # is NO-DATA too -- correct, not a gap. None of these were given
        # evidence that would fabricate a PASS in this smoke run.
        for field in ("accessibility", "performance", "production", "release"):
            self.assertEqual(self.passport[field]["verdict"], "NO-DATA", field)
        # visual.capture_integrity (WBS-30.03 exists) and
        # visual.human_acceptance (no vocabulary/module at all) were both
        # left unexercised in this smoke's explicit scope -- also honest
        # NO-DATA, never a fabricated PASS just because a sibling exists.
        self.assertEqual(self.passport["visual"]["capture_integrity"]["verdict"], "NO-DATA")
        self.assertEqual(self.passport["visual"]["human_acceptance"]["verdict"], "NO-DATA")

    def test_completeness_object_matches_the_per_field_verdicts_exactly(self):
        # Post-fix: release composes to NO-DATA (worst-of five mostly-
        # unmeasured states), not FAIL, so no dimension fails at all in
        # this smoke run and the headline is INCOMPLETE, not BLOCKING
        # FAILURE.
        completeness = self.passport["completeness"]
        self.assertEqual(completeness["total_dimensions"], 10)
        self.assertEqual(sorted(completeness["passed"]),
                         ["build_identity", "contract", "functional", "physical_device"])
        self.assertEqual(completeness["failed"], [])
        self.assertEqual(sorted(completeness["no_data"]),
                         ["accessibility", "performance", "production", "release",
                          "visual.capture_integrity", "visual.human_acceptance"])
        self.assertEqual(completeness["failed_count"], 0)
        self.assertEqual(completeness["no_data_count"], 6)
        self.assertTrue(completeness["headline"].startswith("INCOMPLETE"))
        self.assertIn("release", completeness["headline"])

    def test_exit_code_for_completeness_reports_the_real_no_data_state(self):
        # No FAIL survives the fix (release composes to NO-DATA, not FAIL),
        # so the real exit code is the NO-DATA code, never the old FAIL
        # code and never a false 0.
        import journey_passport as JP
        self.assertEqual(JP.exit_code_for_completeness(self.passport["completeness"]), 2)


if __name__ == "__main__":
    unittest.main()

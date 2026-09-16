#!/usr/bin/env python3
"""Test for canary_pipeline_smoke.py (WBS-30.10): the full ten-module mobile
pipeline run as ONE real test, not ten separate mocked assertions. Proves
the synthetic canary journey's own real output threads through every real
sibling function and lands in a well-formed Journey Passport whose
completeness object is honest about what is real evidence, what is a
synthetic stand-in, and what is genuine NO-DATA -- including the one
adjacent-stage shape mismatch this run surfaces (release_state_tracker's
real output vs. journey_passport's release-dimension expectation)."""
import subprocess
import sys
import unittest
from unittest.mock import patch

sys.path.insert(0, __file__.rsplit("/", 1)[0])
import canary_pipeline_smoke as CPS  # noqa: E402
import release_state_tracker as RST  # noqa: E402


class CanaryPipelineSmokeTests(unittest.TestCase):

    def setUp(self):
        self.passport, self.mismatches, self.tmp = CPS.run_pipeline()

    def test_clean_run_reports_zero_mismatches(self):
        # Direct regression test for the original defect: the mismatch
        # check used to test for a "state" (singular) key that the real
        # release_state_tracker output never carries, so it fired on
        # EVERY clean run, not just a broken one. Nothing above asserted
        # on `self.mismatches` itself, so a clean suite run stayed green
        # even with that stale check back in place (confirmed by
        # reintroducing it in isolation: the other 8 tests still pass
        # while this one alone catches it).
        self.assertEqual(self.mismatches, [])

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


class CanaryTurnsRedOnAdjacentSchemaBreakTests(unittest.TestCase):
    """M0.02: prove the canary is a real gate, not a formality. Mutates
    the one adjacent schema this pipeline is known to depend on
    (release_state_tracker's real 'states' key) and asserts the break
    is both reported and turns the script's own exit code non-zero --
    the exact defect fixed in canary_pipeline_smoke.py: a mismatch used
    to be printed and then main() still hardcoded `return 0`."""

    def test_dropping_the_states_key_is_reported_as_a_mismatch(self):
        real_compose = RST.compose_release_record

        def broken_compose_release_record(*args, **kwargs):
            record = real_compose(*args, **kwargs)
            record = dict(record)
            del record["states"]
            return record

        with patch.object(RST, "compose_release_record",
                           side_effect=broken_compose_release_record):
            _passport, mismatches, _tmp = CPS.run_pipeline()
        self.assertTrue(mismatches, "a dropped 'states' key must be reported")
        self.assertIn("states", mismatches[0])

    def test_the_script_itself_exits_non_zero_on_that_break(self):
        # Full subprocess run, not just run_pipeline(): proves main()'s
        # exit code, the exact place the original defect lived (a
        # mismatch was printed and `return 0` shipped anyway).
        script = CPS.__file__
        script_dir = script.rsplit("/", 1)[0]
        # runpy.run_path executes the module body, and this script calls
        # sys.exit(main()) at import time under __main__, so the
        # SystemExit it raises is what carries the real exit code out.
        driver = (
            "import runpy, sys; sys.path.insert(0, %r); "
            "import release_state_tracker as RST; "
            "_real = RST.compose_release_record; "
            "RST.compose_release_record = lambda *a, **k: "
            "{k2: v for k2, v in _real(*a, **k).items() if k2 != 'states'}; "
            "runpy.run_path(%r, run_name='__main__')"
            % (script_dir, script)
        )
        result = subprocess.run([sys.executable, "-c", driver],
                                capture_output=True, text=True)
        self.assertEqual(result.returncode, 1,
                          "stderr: %s" % result.stderr)
        self.assertIn("INTEGRATION MISMATCH", result.stderr)

    def test_clean_run_exits_with_the_passports_own_completeness_code(self):
        # A second defect found by an independent qa review: main() used
        # to hardcode 0 for any run with no mismatch, even though this
        # canary's own passport is INCOMPLETE (several dimensions have
        # no sibling module yet) and journey_passport.exit_code_for_
        # completeness() already says that is exit code 2. A caller
        # piping this script's exit code into a hard gate was silently
        # told "clean" for a run that was never complete.
        script = CPS.__file__
        result = subprocess.run([sys.executable, script],
                                capture_output=True, text=True)
        self.assertEqual(result.returncode, 2, "stderr: %s" % result.stderr)
        self.assertNotIn("INTEGRATION MISMATCH", result.stderr)


class CanaryStageGuardsAreLiveCodeTests(unittest.TestCase):
    """The six `raise AssertionError` stage guards inside run_pipeline()
    exist to catch a break in stages 1-6 (everything upstream of the
    release/passport seam the mismatches list watches). Nothing proved
    they actually fire rather than being dead code nobody exercises;
    this breaks one on purpose and confirms it does."""

    def test_an_invalid_synthetic_journey_fails_the_stage_1_contract_guard(self):
        broken_journey = dict(CPS.SYNTHETIC_JOURNEY)
        del broken_journey["human_outcome"]  # a required journey-contract field
        with patch.object(CPS, "SYNTHETIC_JOURNEY", broken_journey):
            with self.assertRaisesRegex(AssertionError, "must validate"):
                CPS.run_pipeline()


if __name__ == "__main__":
    unittest.main()

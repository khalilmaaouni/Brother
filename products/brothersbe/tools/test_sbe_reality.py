#!/usr/bin/env python3
"""BAND L THIN: fixtures for `evidence.py`'s release receipt (L1),
`observation.py` (L2 observation contract, L3 observation result adapter),
and `lifecycle.py`'s two new reducers (L4: `reduce_verified_reality`,
`reduce_value_realization`).

Mirrors `tools/test_sbe_readiness.py`'s own shape: hand-built fixtures with
a builder-with-overrides helper, one `TestCase` per surface, tests numbered
in comments against the plan's own case numbers. `contracts.py` is the
single source for every state vocabulary this file pins: every test here
CONSUMES its vocabulary and functions (`REALITY_STATES`,
`VALUE_REALIZATION_STATES`, `MEASURE_MATURITY_STATES`,
`RELEASE_RECEIPT_BINDING`, `validate`, `canonical_digest`,
`declaration_timing`, `reality_binds`) and never re-implements or edits any
of it.

L5 (temporal attacks: clock skew, backdated timestamps, replay) is
deliberately NOT covered here: it folds into the integrated hostile review,
per the brief for this lane.

Run: python3 tools/test_sbe_reality.py
"""
import io
import json
import os
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.abspath(os.path.join(HERE, ".."))

sys.path.insert(0, os.path.join(ROOT, "src"))
try:
    from brothersbe import contracts as contracts_mod
    from brothersbe import evidence as evidence_mod
    from brothersbe import lifecycle as lifecycle_mod
    from brothersbe import observation as observation_mod
finally:
    sys.path.pop(0)

CHANGE_ID = "CHG-2026-08-20-band-l-reality"
COMMIT = "a1b2c3d4e5f60718293a4b5c6d7e8f9012345678"
OTHER_COMMIT = "9876543210fedcba9876543210fedcba98765432"
RELEASED_AT = "2026-08-19T12:00:00Z"
DECLARED_BEFORE = "2026-08-19T09:00:00Z"
DECLARED_AFTER = "2026-08-19T15:00:00Z"
ORIGIN = "git@example.invalid:acme/thing.git"


def a_receipt(**over):
    base = dict(
        change_id=CHANGE_ID, released_commit=COMMIT, artifact_sha256="ab" * 32,
        run_id="run-0001", trust_class="ci-run", source_detail="a real pipeline run",
        producer="release-bot", producer_class="pipeline", origin=ORIGIN,
        released_at=RELEASED_AT)
    base.update(over)
    return evidence_mod.build_release_receipt(**base)


def a_contract(**over):
    base = dict(
        change_id=CHANGE_ID, head_commit=COMMIT, observes="error_rate",
        window={"hours": 24}, producer="watcher", producer_class="tool", origin=ORIGIN,
        declared_at=DECLARED_BEFORE)
    base.update(over)
    return observation_mod.build_observation_contract(**base)


def _write_json(path, value):
    directory = os.path.dirname(path)
    if directory and not os.path.isdir(directory):
        os.makedirs(directory)
    with io.open(path, "w", encoding="utf-8") as fh:
        fh.write(json.dumps(value))
    return path


def _reality_facts(**over):
    base = {
        "releasedCommit": COMMIT, "expectedCommit": COMMIT,
        "observationStatus": "OBSERVED", "windowState": "COMPLETE",
        "observationsRecorded": True, "rollback": False, "incident": False,
        "acceptanceBehaviorFailed": False,
    }
    base.update(over)
    return base


def _value_facts(**over):
    base = {
        "targetEditedAfterRelease": False, "measureMaturity": "MATURE",
        "businessMeasureMet": True, "technicalSuccess": True,
    }
    base.update(over)
    return base


# ---------------------------------------------------------------------------
# L1: evidence.py's release receipt
# ---------------------------------------------------------------------------

class TestReleaseReceipt(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()

    def test_build_refuses_an_unknown_trust_class(self):
        with self.assertRaises(ValueError) as ctx:
            a_receipt(trust_class="vendor-webhook")
        self.assertIn("vendor-webhook", str(ctx.exception))

    def test_a_receipt_validates_against_contracts_release_receipt_surface(self):
        receipt = a_receipt()
        verdict, evidence, problems = contracts_mod.validate("release-receipt", receipt)
        self.assertEqual(verdict, "PASS", (evidence, problems))
        # RELEASE_RECEIPT_BINDING is consumed, never re-typed:
        for field in contracts_mod.RELEASE_RECEIPT_BINDING:
            self.assertIn(field, receipt)

    def test_a_sound_receipt_satisfies_a_live_release_claim(self):
        path = _write_json(os.path.join(self.tmp, "release.json"), a_receipt())
        verdict, evidence, problems = evidence_mod.verify_release_receipt_for_release(
            path, COMMIT)
        self.assertEqual(verdict, "PASS", (evidence, problems))

    # THE CODEX A2 LAW ------------------------------------------------------
    def test_a_fixture_sourced_receipt_never_satisfies_a_live_release_claim(self):
        path = _write_json(
            os.path.join(self.tmp, "fixture-release.json"),
            a_receipt(trust_class="fixture", source_detail="unit test fixture"))
        verdict, evidence, problems = evidence_mod.verify_release_receipt_for_release(
            path, COMMIT)
        self.assertEqual(verdict, "FAIL", (evidence, problems))
        self.assertIn("fixture", evidence)

    def test_a_wrong_commit_receipt_is_rejected(self):
        path = _write_json(os.path.join(self.tmp, "wrong-commit.json"), a_receipt())
        verdict, evidence, problems = evidence_mod.verify_release_receipt_for_release(
            path, OTHER_COMMIT)
        self.assertEqual(verdict, "FAIL", (evidence, problems))
        self.assertIn(COMMIT, evidence)
        self.assertIn(OTHER_COMMIT, evidence)

    def test_a_structurally_broken_receipt_fails_before_trust_is_even_read(self):
        # missing artifactSha256, one of RELEASE_RECEIPT_BINDING's own fields
        receipt = a_receipt()
        del receipt["artifactSha256"]
        path = _write_json(os.path.join(self.tmp, "broken.json"), receipt)
        verdict, evidence, problems = evidence_mod.verify_release_receipt_for_release(
            path, COMMIT)
        self.assertEqual(verdict, "FAIL", (evidence, problems))

    def test_an_absent_receipt_is_no_data_not_fail(self):
        verdict, evidence, problems = evidence_mod.verify_release_receipt_for_release(
            os.path.join(self.tmp, "missing.json"), COMMIT)
        self.assertEqual(verdict, "NO-DATA", (evidence, problems))


# ---------------------------------------------------------------------------
# L2: observation.py's observation contract + declaration timing
# ---------------------------------------------------------------------------

class TestObservationContract(unittest.TestCase):
    def test_a_contract_validates_against_contracts_observation_contract_surface(self):
        contract = a_contract()
        verdict, evidence, problems = observation_mod.validate_observation_contract(contract)
        self.assertEqual(verdict, "PASS", (evidence, problems))

    def test_predeclared_contract_may_present_as_predeclared(self):
        contract = a_contract(declared_at=DECLARED_BEFORE)
        receipt = a_receipt(released_at=RELEASED_AT)
        judged = observation_mod.judge_observation_timing(contract, receipt)
        self.assertEqual(judged["declarationTiming"], "PREDECLARED", judged)
        self.assertTrue(judged["presentedAsPredeclared"], judged)
        self.assertTrue(judged["mayRecordObservations"], judged)

    # LATE DECLARATION --------------------------------------------------
    def test_a_late_declaration_may_still_record_but_is_never_presented_as_predeclared(self):
        contract = a_contract(declared_at=DECLARED_AFTER)
        receipt = a_receipt(released_at=RELEASED_AT)
        judged = observation_mod.judge_observation_timing(contract, receipt)
        self.assertEqual(judged["declarationTiming"], "LATE_DECLARATION", judged)
        self.assertFalse(judged["presentedAsPredeclared"], judged)
        self.assertTrue(judged["mayRecordObservations"], judged)

    def test_an_undetermined_timing_is_also_never_presented_as_predeclared(self):
        contract = a_contract(declared_at="not-a-timestamp")
        receipt = a_receipt(released_at=RELEASED_AT)
        judged = observation_mod.judge_observation_timing(contract, receipt)
        self.assertEqual(judged["declarationTiming"], "UNDETERMINED", judged)
        self.assertFalse(judged["presentedAsPredeclared"], judged)

    def test_declaration_timing_is_consumed_not_reimplemented(self):
        # Same contract/receipt pair, same answer as calling contracts.py directly:
        contract = a_contract(declared_at=DECLARED_AFTER)
        receipt = a_receipt(released_at=RELEASED_AT)
        direct = contracts_mod.declaration_timing(contract, receipt)
        judged = observation_mod.judge_observation_timing(contract, receipt)
        self.assertEqual(judged["declarationTiming"], direct)


# ---------------------------------------------------------------------------
# L3: observation.py's observation result adapter
# ---------------------------------------------------------------------------

class TestObservationResultAdapter(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()

    def test_a_readable_file_is_observed(self):
        path = _write_json(os.path.join(self.tmp, "signal.json"), {"errorRate": 0.01})
        result = observation_mod.read_observation_result(
            path, "REL-1", "error_rate", {"hours": 24}, trust_class="file")
        self.assertEqual(result["status"], "OBSERVED", result)
        self.assertEqual(result["value"], {"errorRate": 0.01})
        self.assertIsNotNone(result["freshnessAt"])
        self.assertEqual(result["source"]["trustClass"], "file")
        self.assertEqual(result["releaseId"], "REL-1")
        self.assertEqual(result["signal"], "error_rate")
        self.assertEqual(result["window"], {"hours": 24})

    # UNAVAILABLE SOURCE, NEVER PASS, NEVER A RAISE -------------------------
    def test_a_missing_source_is_no_data_naming_path_and_remedy(self):
        missing = os.path.join(self.tmp, "does-not-exist.json")
        result = observation_mod.read_observation_result(
            missing, "REL-1", "error_rate", {"hours": 24}, trust_class="file")
        self.assertEqual(result["status"], "NO-DATA", result)
        self.assertIsNone(result["value"])
        self.assertIn(missing, result["remedy"])
        self.assertIn("error_rate", result["remedy"])

    def test_an_unreadable_source_is_no_data_never_a_raise(self):
        path = os.path.join(self.tmp, "broken.json")
        with io.open(path, "w", encoding="utf-8") as fh:
            fh.write("{not valid json")
        result = observation_mod.read_observation_result(
            path, "REL-1", "error_rate", {"hours": 24}, trust_class="fixture")
        self.assertEqual(result["status"], "NO-DATA", result)
        self.assertIn(path, result["remedy"])

    def test_ci_run_trust_class_is_refused_by_this_file_only_adapter(self):
        path = _write_json(os.path.join(self.tmp, "signal.json"), {"x": 1})
        with self.assertRaises(ValueError) as ctx:
            observation_mod.read_observation_result(
                path, "REL-1", "error_rate", {"hours": 24}, trust_class="ci-run")
        self.assertIn("ci-run", str(ctx.exception))

    def test_trust_vocabulary_is_imported_from_l1_not_reinvented(self):
        self.assertLessEqual(set(observation_mod.OBSERVATION_TRUST_CLASSES),
                             set(evidence_mod.RELEASE_SOURCE_CLASSES))
        self.assertIn("fixture", observation_mod.OBSERVATION_TRUST_CLASSES)
        self.assertIn("file", observation_mod.OBSERVATION_TRUST_CLASSES)


# ---------------------------------------------------------------------------
# L4: lifecycle.reduce_verified_reality
# ---------------------------------------------------------------------------

class TestReduceVerifiedReality(unittest.TestCase):
    def test_vocabulary_agrees_with_contracts_reality_states(self):
        # contracts.py is frozen; this is the agreement test its own module
        # docstring promises rather than an import, mirroring
        # `_READINESS_VERDICT_WORDS`'s own stated law.
        self.assertEqual(set(lifecycle_mod._REALITY_STATE_WORDS),
                         set(contracts_mod.REALITY_STATES))

    # case 1: receipt absent -------------------------------------------------
    def test_no_receipt_stays_unobserved(self):
        facts = _reality_facts(releasedCommit=None)
        result = lifecycle_mod.reduce_verified_reality(facts)
        self.assertEqual(result["realityState"], "REALITY_NOT_OBSERVED", result)

    def test_hollow_receipt_commit_stays_unobserved(self):
        for hollow in ("", "   ", "TODO", None):
            facts = _reality_facts(releasedCommit=hollow)
            result = lifecycle_mod.reduce_verified_reality(facts)
            self.assertEqual(result["realityState"], "REALITY_NOT_OBSERVED", (hollow, result))

    # case 2: receipt names the wrong commit ---------------------------------
    def test_wrong_commit_receipt_stays_unobserved(self):
        facts = _reality_facts(releasedCommit=OTHER_COMMIT, expectedCommit=COMMIT)
        result = lifecycle_mod.reduce_verified_reality(facts)
        self.assertEqual(result["realityState"], "REALITY_NOT_OBSERVED", result)

    # case 3: observation source unavailable ---------------------------------
    def test_no_data_observation_source_stays_unobserved(self):
        facts = _reality_facts(observationStatus="NO-DATA")
        result = lifecycle_mod.reduce_verified_reality(facts)
        self.assertEqual(result["realityState"], "REALITY_NOT_OBSERVED", result)
        self.assertTrue(any("NO-DATA" in r for r in result["reasons"]), result["reasons"])

    # case 4: window incomplete ----------------------------------------------
    def test_open_window_is_not_verified_yet(self):
        facts = _reality_facts(windowState="OPEN", observationsRecorded=False)
        result = lifecycle_mod.reduce_verified_reality(facts)
        self.assertEqual(result["realityState"], "REALITY_NOT_OBSERVED", result)

    # EXPIRED window with no observations is unobserved, never auto-verified
    # (Codex risk 3) ----------------------------------------------------------
    def test_expired_window_with_no_observations_never_auto_verifies(self):
        facts = _reality_facts(windowState="EXPIRED", observationsRecorded=False)
        result = lifecycle_mod.reduce_verified_reality(facts)
        self.assertEqual(result["realityState"], "REALITY_NOT_OBSERVED", result)
        self.assertTrue(any("expired" in r for r in result["reasons"]), result["reasons"])
        # calibration: the same window, WITH observations, does verify
        ok_facts = _reality_facts(windowState="EXPIRED", observationsRecorded=True)
        ok_result = lifecycle_mod.reduce_verified_reality(ok_facts)
        self.assertEqual(ok_result["realityState"], "VERIFIED_IN_REALITY", ok_result)

    # case 5: rollback ----------------------------------------------------------
    def test_rollback_is_contradicted_by_reality(self):
        facts = _reality_facts(rollback=True)
        result = lifecycle_mod.reduce_verified_reality(facts)
        self.assertEqual(result["realityState"], "CONTRADICTED_BY_REALITY", result)

    # case 6: incident ------------------------------------------------------
    def test_incident_is_contradicted_by_reality(self):
        facts = _reality_facts(incident=True)
        result = lifecycle_mod.reduce_verified_reality(facts)
        self.assertEqual(result["realityState"], "CONTRADICTED_BY_REALITY", result)

    # case 7: acceptance behavior fails ---------------------------------------
    def test_failed_acceptance_behavior_is_contradicted_by_reality(self):
        facts = _reality_facts(acceptanceBehaviorFailed=True)
        result = lifecycle_mod.reduce_verified_reality(facts)
        self.assertEqual(result["realityState"], "CONTRADICTED_BY_REALITY", result)

    # honest calibration: nothing wrong verifies -----------------------------
    def test_sound_facts_are_verified_in_reality(self):
        result = lifecycle_mod.reduce_verified_reality(_reality_facts())
        self.assertEqual(result["realityState"], "VERIFIED_IN_REALITY", result)

    # late regression: verified is never terminal against a later contrary
    # fact --------------------------------------------------------------------
    def test_a_late_regression_after_verification_reopens_as_contradicted(self):
        verified = lifecycle_mod.reduce_verified_reality(_reality_facts())
        self.assertEqual(verified["realityState"], "VERIFIED_IN_REALITY", verified)
        regressed = lifecycle_mod.reduce_verified_reality(
            _reality_facts(rollback=True, previouslyVerified=True))
        self.assertEqual(regressed["realityState"], "CONTRADICTED_BY_REALITY", regressed)
        self.assertTrue(any("verified once" in r for r in regressed["reasons"]),
                        regressed["reasons"])

    # unknown states raise ValueError by name ---------------------------------
    def test_an_unknown_window_state_raises_by_name(self):
        facts = _reality_facts(windowState="SUSPENDED")
        with self.assertRaises(ValueError) as ctx:
            lifecycle_mod.reduce_verified_reality(facts)
        self.assertIn("SUSPENDED", str(ctx.exception))

    def test_an_unknown_observation_status_raises_by_name(self):
        facts = _reality_facts(observationStatus="MAYBE")
        with self.assertRaises(ValueError) as ctx:
            lifecycle_mod.reduce_verified_reality(facts)
        self.assertIn("MAYBE", str(ctx.exception))


# ---------------------------------------------------------------------------
# L4: lifecycle.reduce_value_realization
# ---------------------------------------------------------------------------

class TestReduceValueRealization(unittest.TestCase):
    def test_vocabulary_agrees_with_contracts_value_realization_states(self):
        # Mirrors `TestReduceVerifiedReality`'s own agreement test above:
        # this vocabulary moved to `contracts.py` as its single source in
        # this lane, and `lifecycle.py` restates it locally rather than
        # importing it (same stated law as `_REALITY_STATE_WORDS`).
        self.assertEqual(set(lifecycle_mod.VALUE_REALIZATION_STATES),
                         set(contracts_mod.VALUE_REALIZATION_STATES))

    def test_vocabulary_agrees_with_contracts_measure_maturity_states(self):
        self.assertEqual(set(lifecycle_mod._MEASURE_MATURITY_STATES),
                         set(contracts_mod.MEASURE_MATURITY_STATES))

    # case 8: technical success, missed business measure --------------------
    def test_technical_success_with_a_missed_business_measure_is_value_not_realized(self):
        facts = _value_facts(technicalSuccess=True, businessMeasureMet=False)
        result = lifecycle_mod.reduce_value_realization(facts)
        self.assertEqual(result["valueState"], "VALUE_NOT_REALIZED", result)
        self.assertTrue(any("despite technical success" in r for r in result["reasons"]),
                        result["reasons"])

    def test_a_met_business_measure_on_a_mature_reading_is_realized(self):
        result = lifecycle_mod.reduce_value_realization(_value_facts())
        self.assertEqual(result["valueState"], "VALUE_REALIZED", result)

    # case 9: not-yet-mature measure ------------------------------------------
    def test_a_not_yet_mature_measure_is_a_not_yet_state_never_realized_by_default(self):
        facts = _value_facts(measureMaturity="NOT_YET_MATURE", businessMeasureMet=None)
        result = lifecycle_mod.reduce_value_realization(facts)
        self.assertEqual(result["valueState"], "VALUE_NOT_YET_MEASURABLE", result)

    # case 10: value target edited after release -------------------------------
    def test_a_target_edited_after_release_is_flagged_naming_the_moved_target(self):
        facts = _value_facts(targetEditedAfterRelease=True,
                             movedTargetDetail="conversion target moved 2% to 5%")
        result = lifecycle_mod.reduce_value_realization(facts)
        self.assertEqual(result["valueState"], "VALUE_TARGET_MOVED", result)
        self.assertTrue(any("conversion target moved 2% to 5%" in r
                            for r in result["reasons"]), result["reasons"])

    def test_target_moved_outranks_maturity_and_measure(self):
        # even a measure that would otherwise read as realized is flagged
        # first when the target itself moved.
        facts = _value_facts(targetEditedAfterRelease=True, movedTargetDetail="moved",
                             measureMaturity="MATURE", businessMeasureMet=True)
        result = lifecycle_mod.reduce_value_realization(facts)
        self.assertEqual(result["valueState"], "VALUE_TARGET_MOVED", result)

    # unknown maturity raises by name ------------------------------------------
    def test_an_unknown_measure_maturity_raises_by_name(self):
        facts = _value_facts(measureMaturity="STALE")
        with self.assertRaises(ValueError) as ctx:
            lifecycle_mod.reduce_value_realization(facts)
        self.assertIn("STALE", str(ctx.exception))

    def test_a_non_boolean_business_measure_met_raises(self):
        facts = _value_facts(businessMeasureMet="false")
        with self.assertRaises(ValueError) as ctx:
            lifecycle_mod.reduce_value_realization(facts)
        self.assertIn("false", str(ctx.exception))


if __name__ == "__main__":
    unittest.main()

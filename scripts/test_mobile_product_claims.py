#!/usr/bin/env python3
"""Tests for mobile_product_claims.py (WBS-50.05 Mobile product claims).

Most tests use a small structurally-shaped fake journey passport dict (this
module only ever reads a passport's "journey" and "production" fields, by
design -- see mobile_product_claims.py's own module docstring for why).
CriticalRuleTests reuses test_journey_passport.py's own ComposeFixtureMixin
to build a REAL all-PASS composed passport from the real sibling modules
(mobile_journey_contract, mobile_reference_lock, native_evidence_v2,
device_matrix, ...), the same discipline test_journey_passport.py itself
documents ("Feeds real outputs from the real sibling modules... rather than
hand-typing fake evidence dicts"), so the critical-rule proof is not built
on a fixture that merely claims to be all-PASS.
"""
import json
import os
import sys
import tempfile
import unittest

sys.path.insert(0, __file__.rsplit("/", 1)[0])
import claim_lifecycle as CL
import contract_check as CC
import mobile_product_claims as MPC

NOW = "2026-09-14T00:00:00+00:00"

BASE_PRODUCT = {
    "schema_version": "claim-mobile-product-v1", "claim_id": "j1:completion",
    "revision_id": "j1:completion:r1", "journey_id": "j1",
    "product_dimension": "completion", "comparison": "gte",
    "threshold": 0.8, "measured_value": None, "resolved_at": None,
    "evidence_note": None,
}

BASE_LIFECYCLE = {
    "schema_version": "claim-lifecycle-v1", "claim_id": "j1:completion",
    "revision_id": "j1:completion:r1", "state": "DRAFT", "supersedes": None,
}

BASE_PASSPORT = {
    "schema_version": "journey-passport-v1", "journey": "j1",
    "production": {"record": {"claim_ids": []}, "digest": "x",
                    "verdict": "PASS", "verdict_reason": "fixture"},
}


def product(**overrides):
    rec = dict(BASE_PRODUCT)
    rec.update(overrides)
    return rec


def lifecycle(**overrides):
    rec = dict(BASE_LIFECYCLE)
    rec.update(overrides)
    return rec


def passport(**overrides):
    rec = json.loads(json.dumps(BASE_PASSPORT))  # deep copy, nested "production" included
    rec.update(overrides)
    return rec


class SchemaShapeTests(unittest.TestCase):
    def setUp(self):
        self.schema = CC.load_json(MPC.DEFAULT_SCHEMA, "schema")

    def test_an_unresolved_claim_passes(self):
        self.assertEqual(MPC.check(product(), self.schema), [])

    def test_a_resolved_claim_passes(self):
        rec = product(measured_value=0.9, resolved_at=NOW, evidence_note="x")
        self.assertEqual(MPC.check(rec, self.schema), [])

    def test_missing_required_top_level_field_is_refused(self):
        r = product()
        del r["threshold"]
        problems = MPC.check(r, self.schema)
        self.assertTrue(any("threshold" in p for p in problems), problems)

    def test_wrong_schema_version_is_refused(self):
        problems = MPC.check(product(schema_version="wrong"), self.schema)
        self.assertTrue(problems)

    def test_unknown_product_dimension_is_refused(self):
        problems = MPC.check(product(product_dimension="made_up"), self.schema)
        self.assertTrue(problems)

    def test_unknown_comparison_is_refused(self):
        problems = MPC.check(product(comparison="eq"), self.schema)
        self.assertTrue(problems)

    def test_additional_top_level_property_is_refused(self):
        r = product()
        r["not_a_real_field"] = "x"
        problems = MPC.check(r, self.schema)
        self.assertTrue(any("unexpected field" in p for p in problems), problems)

    def test_every_dimension_value_is_individually_valid(self):
        for dim in MPC.DIMENSIONS:
            self.assertEqual(MPC.check(product(product_dimension=dim), self.schema), [])

    def test_dimensions_match_the_schema_enum(self):
        # Catches drift between the module's own DIMENSIONS tuple and the
        # schema's own product_dimension enum.
        self.assertEqual(
            set(MPC.DIMENSIONS),
            set(self.schema["properties"]["product_dimension"]["enum"]))


class ResolutionConsistencyTests(unittest.TestCase):
    def setUp(self):
        self.schema = CC.load_json(MPC.DEFAULT_SCHEMA, "schema")

    def test_measured_value_without_resolved_at_is_refused(self):
        problems = MPC.check(product(measured_value=0.9), self.schema)
        self.assertTrue(any("measured_value/resolved_at" in p for p in problems), problems)

    def test_resolved_at_without_measured_value_is_refused(self):
        problems = MPC.check(product(resolved_at=NOW), self.schema)
        self.assertTrue(any("measured_value/resolved_at" in p for p in problems), problems)

    def test_neither_set_is_accepted(self):
        self.assertEqual(MPC.check(product(), self.schema), [])

    def test_both_set_is_accepted(self):
        rec = product(measured_value=0.9, resolved_at=NOW)
        self.assertEqual(MPC.check(rec, self.schema), [])

    def test_unparseable_resolved_at_is_refused(self):
        rec = product(measured_value=0.9, resolved_at="not-a-date")
        problems = MPC.check(rec, self.schema)
        self.assertTrue(any("resolved_at" in p and "ISO-8601" in p for p in problems), problems)

    def test_evidence_note_may_be_set_while_unresolved(self):
        # Honest NO-DATA: a reason recorded without a measured_value.
        rec = product(evidence_note="no evidence source exists yet")
        self.assertEqual(MPC.check(rec, self.schema), [])


class CheckMatchesLifecycleTests(unittest.TestCase):
    def test_matching_ids_pass(self):
        self.assertEqual(MPC.check_matches_lifecycle(product(), lifecycle()), [])

    def test_mismatched_claim_id_is_refused(self):
        problems = MPC.check_matches_lifecycle(product(), lifecycle(claim_id="other"))
        self.assertTrue(any("claim_id" in p for p in problems), problems)

    def test_mismatched_revision_id_is_refused(self):
        problems = MPC.check_matches_lifecycle(product(), lifecycle(revision_id="other"))
        self.assertTrue(any("revision_id" in p and "rewritten successor" in p for p in problems), problems)


class CheckMatchesPassportTests(unittest.TestCase):
    def test_matching_journey_and_listed_claim_id_pass(self):
        p = passport(production={"record": {"claim_ids": ["j1:completion"]}})
        self.assertEqual(MPC.check_matches_passport(product(), p), [])

    def test_mismatched_journey_id_is_refused(self):
        p = passport(journey="different", production={"record": {"claim_ids": ["j1:completion"]}})
        problems = MPC.check_matches_passport(product(), p)
        self.assertTrue(any("journey_id" in p_ for p_ in problems), problems)

    def test_claim_id_absent_from_production_claim_ids_is_refused(self):
        p = passport(production={"record": {"claim_ids": ["j1:latency"]}})
        problems = MPC.check_matches_passport(product(), p)
        self.assertTrue(any("production.record.claim_ids" in p_ for p_ in problems), problems)

    def test_empty_claim_ids_is_refused_for_any_real_claim(self):
        problems = MPC.check_matches_passport(product(), passport())
        self.assertTrue(problems)

    def test_missing_production_record_does_not_crash(self):
        # production present but its own "record" absent (e.g. NO-DATA
        # dimension): treated as no claim_ids to match against, not a crash.
        p = passport(production={"record": None})
        problems = MPC.check_matches_passport(product(), p)
        self.assertEqual(problems, [])  # journey_id still matches; nothing else to check


class RegisterClaimsTests(unittest.TestCase):
    def test_registers_one_pair_per_threshold_entry(self):
        pairs = MPC.register_claims(passport(), {
            "completion": {"threshold": 0.8, "comparison": "gte"},
            "latency": {"threshold": 2.0, "comparison": "lte"},
        })
        self.assertEqual(len(pairs), 2)

    def test_claim_id_is_deterministic_from_journey_id_and_dimension(self):
        pairs = MPC.register_claims(
            passport(journey="j9"), {"crash_free_rate": {"threshold": 0.99, "comparison": "gte"}})
        lc, pc = pairs[0]
        self.assertEqual(lc["claim_id"], "j9:crash_free_rate")
        self.assertEqual(pc["claim_id"], "j9:crash_free_rate")
        self.assertEqual(lc["revision_id"], "j9:crash_free_rate:r1")
        self.assertEqual(pc["revision_id"], "j9:crash_free_rate:r1")

    def test_registered_lifecycle_record_starts_at_draft(self):
        pairs = MPC.register_claims(
            passport(), {"completion": {"threshold": 0.8, "comparison": "gte"}})
        lc, _pc = pairs[0]
        self.assertEqual(lc["state"], "DRAFT")
        self.assertIsNone(lc["supersedes"])

    def test_registered_lifecycle_record_validates_as_claim_lifecycle_v1(self):
        schema = CC.load_json(CL.DEFAULT_SCHEMA, "schema")
        pairs = MPC.register_claims(
            passport(), {"completion": {"threshold": 0.8, "comparison": "gte"}})
        lc, _pc = pairs[0]
        self.assertEqual(CL.check(lc, schema), [])

    def test_registered_product_record_validates_as_claim_mobile_product_v1(self):
        schema = CC.load_json(MPC.DEFAULT_SCHEMA, "schema")
        pairs = MPC.register_claims(
            passport(), {"completion": {"threshold": 0.8, "comparison": "gte"}})
        _lc, pc = pairs[0]
        self.assertEqual(MPC.check(pc, schema), [])

    def test_registered_pair_matches_lifecycle_and_passport(self):
        p = passport()
        pairs = MPC.register_claims(
            p, {"completion": {"threshold": 0.8, "comparison": "gte"}})
        lc, pc = pairs[0]
        self.assertEqual(MPC.check_matches_lifecycle(pc, lc), [])
        p["production"]["record"]["claim_ids"] = [pc["claim_id"]]
        self.assertEqual(MPC.check_matches_passport(pc, p), [])

    def test_unknown_dimension_is_refused(self):
        with self.assertRaises(MPC.RegistrationError):
            MPC.register_claims(passport(), {"made_up": {"threshold": 1, "comparison": "gte"}})

    def test_unknown_comparison_is_refused(self):
        with self.assertRaises(MPC.RegistrationError):
            MPC.register_claims(
                passport(), {"completion": {"threshold": 0.8, "comparison": "eq"}})

    def test_non_numeric_threshold_is_refused(self):
        with self.assertRaises(MPC.RegistrationError):
            MPC.register_claims(
                passport(), {"completion": {"threshold": "high", "comparison": "gte"}})

    def test_boolean_threshold_is_refused(self):
        # bool is a subtype of int in Python; must not silently pass as numeric.
        with self.assertRaises(MPC.RegistrationError):
            MPC.register_claims(
                passport(), {"completion": {"threshold": True, "comparison": "gte"}})

    def test_missing_journey_id_is_refused(self):
        p = passport()
        del p["journey"]
        with self.assertRaises(MPC.RegistrationError):
            MPC.register_claims(p, {"completion": {"threshold": 0.8, "comparison": "gte"}})

    def test_no_data_journey_id_is_refused(self):
        # journey_passport.compose_passport's own honest placeholder for an
        # unresolved journey_id ("NO-DATA") must not silently seed a claim.
        p = passport(journey="NO-DATA")
        with self.assertRaises(MPC.RegistrationError):
            MPC.register_claims(p, {"completion": {"threshold": 0.8, "comparison": "gte"}})

    def test_empty_thresholds_registers_nothing(self):
        self.assertEqual(MPC.register_claims(passport(), {}), [])


class FreezeClaimsTests(unittest.TestCase):
    def test_freeze_advances_draft_to_frozen(self):
        pairs = MPC.register_claims(
            passport(), {"completion": {"threshold": 0.8, "comparison": "gte"}})
        frozen = MPC.freeze_claims(pairs)
        lc, _pc = frozen[0]
        self.assertEqual(lc["state"], "FROZEN")

    def test_freeze_preserves_claim_and_revision_identity(self):
        pairs = MPC.register_claims(
            passport(), {"completion": {"threshold": 0.8, "comparison": "gte"}})
        original_lc, original_pc = pairs[0]
        frozen_lc, frozen_pc = MPC.freeze_claims(pairs)[0]
        self.assertEqual(frozen_lc["claim_id"], original_lc["claim_id"])
        self.assertEqual(frozen_lc["revision_id"], original_lc["revision_id"])
        self.assertEqual(frozen_pc, original_pc)  # product content untouched by freezing

    def test_frozen_lifecycle_record_validates_as_claim_lifecycle_v1(self):
        schema = CC.load_json(CL.DEFAULT_SCHEMA, "schema")
        pairs = MPC.register_claims(
            passport(), {"completion": {"threshold": 0.8, "comparison": "gte"}})
        lc, _pc = MPC.freeze_claims(pairs)[0]
        self.assertEqual(CL.check(lc, schema), [])

    def test_freeze_uses_claim_lifecycles_own_transition_chain(self):
        # Reuse, not reinvent: every intermediate step is a valid
        # claim_lifecycle.py transition (DRAFT -> CHECKED -> FROZEN).
        pairs = MPC.register_claims(
            passport(), {"completion": {"threshold": 0.8, "comparison": "gte"}})
        draft = pairs[0][0]
        checked = dict(draft, state="CHECKED")
        self.assertEqual(CL.check_transition(draft, checked), [])
        frozen = dict(checked, state="FROZEN")
        self.assertEqual(CL.check_transition(checked, frozen), [])

    def test_freezing_a_non_draft_claim_is_refused(self):
        pairs = MPC.register_claims(
            passport(), {"completion": {"threshold": 0.8, "comparison": "gte"}})
        already_checked = [(dict(lc, state="CHECKED"), pc) for lc, pc in pairs]
        with self.assertRaises(MPC.RegistrationError):
            MPC.freeze_claims(already_checked)

    def test_freeze_multiple_pairs_independently(self):
        pairs = MPC.register_claims(passport(), {
            "completion": {"threshold": 0.8, "comparison": "gte"},
            "latency": {"threshold": 2.0, "comparison": "lte"},
        })
        frozen = MPC.freeze_claims(pairs)
        self.assertEqual(len(frozen), 2)
        for lc, _pc in frozen:
            self.assertEqual(lc["state"], "FROZEN")


class CheckReferencedByPassportTests(unittest.TestCase):
    def test_absent_production_is_trivially_satisfied(self):
        p = {"journey": "j1"}  # no "production" key at all (NO-DATA dimension)
        self.assertEqual(MPC.check_referenced_by_passport(p, {}), [])

    def test_empty_claim_ids_is_trivially_satisfied(self):
        problems = MPC.check_referenced_by_passport(passport(), {})
        self.assertEqual(problems, [])

    def test_all_frozen_claims_pass_the_gate(self):
        pairs = MPC.freeze_claims(MPC.register_claims(
            passport(), {"completion": {"threshold": 0.8, "comparison": "gte"}}))
        lc, pc = pairs[0]
        p = passport(production={"record": {"claim_ids": [pc["claim_id"]]}})
        problems = MPC.check_referenced_by_passport(p, {lc["claim_id"]: lc})
        self.assertEqual(problems, [])

    def test_a_claim_never_registered_fails_the_gate(self):
        p = passport(production={"record": {"claim_ids": ["j1:completion"]}})
        problems = MPC.check_referenced_by_passport(p, {})
        self.assertTrue(any("no claim-lifecycle-v1" in p_ for p_ in problems), problems)

    def test_a_draft_claim_fails_the_gate(self):
        p = passport(production={"record": {"claim_ids": ["j1:completion"]}})
        problems = MPC.check_referenced_by_passport(
            p, {"j1:completion": lifecycle(state="DRAFT")})
        self.assertTrue(any("FROZEN or later" in p_ for p_ in problems), problems)

    def test_a_checked_but_not_frozen_claim_fails_the_gate(self):
        p = passport(production={"record": {"claim_ids": ["j1:completion"]}})
        problems = MPC.check_referenced_by_passport(
            p, {"j1:completion": lifecycle(state="CHECKED")})
        self.assertTrue(any("FROZEN or later" in p_ for p_ in problems), problems)

    def test_a_claim_past_frozen_still_passes_the_gate(self):
        p = passport(production={"record": {"claim_ids": ["j1:completion"]}})
        problems = MPC.check_referenced_by_passport(
            p, {"j1:completion": lifecycle(state="DECISION_USED")})
        self.assertEqual(problems, [])

    def test_malformed_claim_ids_is_refused(self):
        p = passport(production={"record": {"claim_ids": "not-a-list"}})
        problems = MPC.check_referenced_by_passport(p, {})
        self.assertTrue(any("claim_ids" in p_ for p_ in problems), problems)


class ResolveNoEvidenceAvailableTests(unittest.TestCase):
    def test_completion_names_no_analytics_source_as_the_reason(self):
        resolved, verdict = MPC.resolve_no_evidence_available(
            product(product_dimension="completion"))
        self.assertEqual(verdict, "NO-DATA")
        self.assertIsNone(resolved["measured_value"])
        self.assertIsNone(resolved["resolved_at"])
        self.assertIn("no analytics", resolved["evidence_note"])

    def test_crash_free_rate_names_native_evidence_v2_as_insufficient(self):
        resolved, verdict = MPC.resolve_no_evidence_available(
            product(product_dimension="crash_free_rate"))
        self.assertEqual(verdict, "NO-DATA")
        self.assertIn("native_evidence_v2.py", resolved["evidence_note"])

    def test_latency_names_performance_dimension_as_insufficient(self):
        resolved, verdict = MPC.resolve_no_evidence_available(
            product(product_dimension="latency"))
        self.assertEqual(verdict, "NO-DATA")
        self.assertIn("performance", resolved["evidence_note"])

    def test_return_usage_has_an_honest_reason(self):
        resolved, verdict = MPC.resolve_no_evidence_available(
            product(product_dimension="return_usage"))
        self.assertEqual(verdict, "NO-DATA")
        self.assertTrue(resolved["evidence_note"])

    def test_never_fabricates_a_measured_value(self):
        for dim in MPC.DIMENSIONS:
            resolved, verdict = MPC.resolve_no_evidence_available(
                product(product_dimension=dim))
            self.assertIsNone(resolved["measured_value"])
            self.assertIsNone(resolved["resolved_at"])
            self.assertEqual(verdict, "NO-DATA")

    def test_passing_an_all_pass_technical_passport_does_not_resolve(self):
        # The fabricated-but-structurally-honest version of the critical
        # rule test: a fake field_verdicts dict where every technical
        # dimension is PASS. CriticalRuleTests below proves the same thing
        # against a REAL composed journey_passport.
        all_pass = {
            "contract": "PASS", "build_identity": "PASS", "functional": "PASS",
            "accessibility": "PASS", "performance": "PASS",
            "visual.capture_integrity": "PASS", "visual.human_acceptance": "PASS",
            "physical_device": "PASS", "release": "PASS", "production": "PASS",
        }
        p = passport(field_verdicts=all_pass)
        resolved, verdict = MPC.resolve_no_evidence_available(
            product(product_dimension="completion"), p)
        self.assertEqual(verdict, "NO-DATA")
        self.assertIsNone(resolved["measured_value"])
        self.assertIsNone(resolved["resolved_at"])
        # The all-PASS technical picture is named in the note for audit
        # transparency, but never used to resolve the claim.
        self.assertIn("PASS", resolved["evidence_note"])
        self.assertIn("not read to resolve", resolved["evidence_note"])


class MainCliTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()

    def write(self, name, data):
        path = os.path.join(self.tmp, name)
        with open(path, "w") as fh:
            json.dump(data, fh)
        return path

    def test_check_command_exits_0_on_pass(self):
        path = self.write("p.json", product())
        self.assertEqual(MPC.main(["check", path]), 0)

    def test_check_command_exits_2_on_missing_file(self):
        self.assertEqual(MPC.main(["check", "/no/such/record.json"]), 2)

    def test_check_command_exits_1_on_structural_fail(self):
        r = product()
        del r["threshold"]
        path = self.write("p.json", r)
        self.assertEqual(MPC.main(["check", path]), 1)

    def test_check_command_with_matching_lifecycle_and_passport_exits_0(self):
        p_path = self.write("p.json", product())
        lc_path = self.write("lc.json", lifecycle())
        pass_path = self.write(
            "pass.json", passport(production={"record": {"claim_ids": ["j1:completion"]}}))
        self.assertEqual(MPC.main([
            "check", p_path, "--lifecycle-record", lc_path, "--passport-record", pass_path]), 0)

    def test_check_command_with_mismatched_passport_record_exits_1(self):
        p_path = self.write("p.json", product())
        pass_path = self.write(
            "pass.json", passport(production={"record": {"claim_ids": ["something_else"]}}))
        self.assertEqual(MPC.main(["check", p_path, "--passport-record", pass_path]), 1)

    def test_register_command_writes_frozen_claims(self):
        pass_path = self.write("pass.json", passport())
        t_path = self.write("t.json", {
            "completion": {"threshold": 0.8, "comparison": "gte"}})
        out_path = os.path.join(self.tmp, "out.json")
        self.assertEqual(MPC.main(["register", pass_path, t_path, "--out", out_path]), 0)
        with open(out_path) as fh:
            written = json.load(fh)
        self.assertEqual(len(written), 1)
        self.assertEqual(written[0]["lifecycle"]["state"], "FROZEN")
        self.assertEqual(written[0]["product"]["product_dimension"], "completion")

    def test_register_command_on_bad_thresholds_exits_1(self):
        pass_path = self.write("pass.json", passport())
        t_path = self.write("t.json", {"made_up": {"threshold": 1, "comparison": "gte"}})
        self.assertEqual(MPC.main(["register", pass_path, t_path]), 1)

    def test_register_command_on_missing_passport_exits_2(self):
        t_path = self.write("t.json", {})
        self.assertEqual(MPC.main(["register", "/no/such/passport.json", t_path]), 2)

    def test_gate_command_passes_when_every_claim_is_frozen(self):
        pass_path = self.write(
            "pass.json", passport(production={"record": {"claim_ids": ["j1:completion"]}}))
        c_path = self.write("c.json", [lifecycle(state="FROZEN")])
        self.assertEqual(MPC.main(["gate", pass_path, c_path]), 0)

    def test_gate_command_fails_when_a_claim_is_still_draft(self):
        pass_path = self.write(
            "pass.json", passport(production={"record": {"claim_ids": ["j1:completion"]}}))
        c_path = self.write("c.json", [lifecycle(state="DRAFT")])
        self.assertEqual(MPC.main(["gate", pass_path, c_path]), 1)

    def test_gate_command_on_non_list_claims_exits_2(self):
        pass_path = self.write("pass.json", passport())
        c_path = self.write("c.json", {"not": "a list"})
        self.assertEqual(MPC.main(["gate", pass_path, c_path]), 2)

    def test_end_to_end_register_freeze_then_gate_passes(self):
        p = passport()
        pass_path = self.write("pass.json", p)
        t_path = self.write("t.json", {
            "return_usage": {"threshold": 0.3, "comparison": "gte"},
        })
        out_path = os.path.join(self.tmp, "registered.json")
        self.assertEqual(MPC.main(["register", pass_path, t_path, "--out", out_path]), 0)
        with open(out_path) as fh:
            registered = json.load(fh)
        claim_id = registered[0]["lifecycle"]["claim_id"]
        p["production"] = {"record": {"claim_ids": [claim_id]}}
        pass_path2 = self.write("pass2.json", p)
        c_path = self.write("c.json", [r["lifecycle"] for r in registered])
        self.assertEqual(MPC.main(["gate", pass_path2, c_path]), 0)


class CriticalRuleTests(unittest.TestCase):
    """Directly proves the roadmap's own critical rule: 'Mobile technical
    PASS never silently resolves product-success claims.' Builds a REAL
    composed journey_passport from the real sibling modules (reusing
    test_journey_passport.py's own ComposeFixtureMixin, not a hand-typed
    fake) where every single technical dimension -- contract, build_identity,
    functional, accessibility, performance, visual.capture_integrity,
    visual.human_acceptance, physical_device, release, production -- reads
    PASS, then registers, freezes, and resolves a real completion claim
    against it. The claim must stay unresolved (NO-DATA), never inferred
    PASS from the passport's own all-PASS technical picture."""

    def setUp(self):
        # Import lazily: pulls in test_journey_passport.py's fixture mixin,
        # which itself needs a real git repo and subprocess-based fixtures
        # (see that module's own ComposeFixtureMixin), only for this class.
        from test_journey_passport import ComposeFixtureMixin

        class _Fixture(ComposeFixtureMixin, unittest.TestCase):
            def runTest(self):
                pass

        self._fixture = _Fixture()
        self._fixture.setUp()
        # ComposeFixtureMixin.setUp registers its own cleanup (tmp dir
        # removal) via self.addCleanup, which only fires when doCleanups
        # runs on that same TestCase instance -- wire it into this test's
        # own teardown so the fixture's temp directory is not leaked.
        self.addCleanup(self._fixture.doCleanups)

    def test_all_pass_technical_passport_never_resolves_a_completion_claim(self):
        passport_record = self._fixture.compose_all()

        # Sanity: this really is the all-PASS scenario the critical rule is
        # about, not an accidental NO-DATA/FAIL passport that would make
        # the assertion below trivially true for the wrong reason.
        for field in ("contract", "build_identity", "functional", "accessibility",
                      "performance", "physical_device", "release", "production"):
            self.assertEqual(passport_record[field]["verdict"], "PASS", field)
        self.assertEqual(passport_record["visual"]["capture_integrity"]["verdict"], "PASS")
        self.assertEqual(passport_record["visual"]["human_acceptance"]["verdict"], "PASS")
        self.assertEqual(
            passport_record["completeness"]["headline"], "ALL 10 DIMENSION(S) ASSESSED: PASS.")

        # Register a real completion product claim for this real journey,
        # matching journey_passport's own production.claim_ids fixture
        # ({"claim_ids": ["claim-001"]}, from ComposeFixtureMixin.production).
        pairs = MPC.register_claims(
            passport_record, {"completion": {"threshold": 0.8, "comparison": "gte"}})
        lifecycle_record, product_record = MPC.freeze_claims(pairs)[0]
        self.assertEqual(lifecycle_record["state"], "FROZEN")

        # THE CRITICAL ASSERTION: resolving this product claim against the
        # real all-PASS passport must NOT mark it resolved/passed. It must
        # stay NO-DATA, exactly as it would with no passport at all.
        resolved, verdict = MPC.resolve_no_evidence_available(product_record, passport_record)
        self.assertEqual(verdict, "NO-DATA")
        self.assertIsNone(resolved["measured_value"])
        self.assertIsNone(resolved["resolved_at"])
        self.assertNotEqual(verdict, "PASS")

        # The claim never validated as anything but its own schema shape,
        # unresolved.
        schema = CC.load_json(MPC.DEFAULT_SCHEMA, "schema")
        self.assertEqual(MPC.check(resolved, schema), [])


if __name__ == "__main__":
    unittest.main()

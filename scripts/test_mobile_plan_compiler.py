#!/usr/bin/env python3
"""Tests for mobile_plan_compiler.py (WBS-30.04 / EPIC M1.04).

Properties covered: a full realistic journey contract produces a traceable
unit for every named category; an empty accessibility_obligations list
produces a stated-gap unit rather than silently skipping the category; an
invalid journey contract is refused before any unit is generated, never
partially compiled; the semantic layer carries no filename or engine-
specific string; the project layer resolves it to the same iOS/SwiftUI
output this module always emitted; a project_profile picks the right
adapter, and an unrecognized adapter/profile falls back to the default
rather than refusing.
"""
import io
import json
import os
import sys
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import contract_check as CC  # noqa: E402
import mobile_journey_contract as MJC  # noqa: E402
import mobile_plan_compiler as MPC  # noqa: E402
import work_record as WR  # noqa: E402

try:
    import tmp_sandbox as _e100_tmp
    _e100_tmp.install()
except ImportError:
    sys.stderr.write("tmp_sandbox absent: %s leaves its temp trees behind\n"
                     % os.path.basename(__file__))

VALID_OUTCOME = {
    "schema_version": "outcome-contract-v1", "project": {
        "project_id": "p", "name": "p",
        "provenance": {"project_id": "ask", "name": "ask"}},
    "language": "en", "question": "q", "success_checks": [
        {"id": "s", "command": "true", "expect": "exit-0"}],
    "must_answer": [], "affected_products": ["brother"], "ticket": None,
    "audit": {"required": False, "manifest": None}, "persona": "developer",
    "state": "contracted", "receipts": [], "questions": [], "history": [],
    "decision": None,
}

# A realistic, fully populated journey contract: every array carries at
# least one real-looking entry, so the traceability test can check every
# named category against a non-trivial source value.
FULL_JOURNEY = {
    "schema_version": "mobile-journey-contract-v1",
    "journey_id": "guest-checkout",
    "human_outcome": "the guest completes checkout without creating an account",
    "entry_state": "cart_reviewed",
    "exit_state": "order_confirmed",
    "supported_device_classes": ["iphone", "ipad"],
    "supported_os_range": "17-18",
    "locales": ["en", "ja"],
    "accessibility_obligations": [
        "VoiceOver reads the total price before the confirm button",
        "the confirm button has a minimum 44x44pt hit target",
    ],
    "network_state_obligations": [
        "a payment request retried after a dropped connection never double-charges",
    ],
    "interruption_obligations": [
        "the cart state survives the app being backgrounded mid-checkout",
    ],
    "privacy_constraints": [
        "the card number is never written to disk unencrypted",
    ],
    "performance_budgets": [
        {"metric": "time_to_confirm_screen", "budget": "300ms"},
    ],
    "visual_reference_ids": ["mobile-design-board-42"],
    "required_native_tests": ["GuestCheckoutTests.testHappyPath"],
    "human_acceptance_items": [
        "a human confirms the confirm button reads correctly in dark mode",
    ],
    "post_release_claims": ["claim-completion-rate"],
}

EXPECTED_CATEGORIES = {
    "domain/state",
    "domain/state (interruption handling)",
    "view/navigation",
    "view/navigation (visual reference)",
    "view/navigation (human acceptance)",
    "network/persistence",
    "network/persistence (privacy)",
    "accessibility",
    "localization",
    "tests",
    "instrumentation",
    "instrumentation (performance budgets)",
}

IOS_FILENAME_BY_CATEGORY = {
    "domain/state": "Domain.swift",
    "domain/state (interruption handling)": "Interruption.swift",
    "view/navigation": "View.swift",
    "view/navigation (visual reference)": "Visuals.swift",
    "view/navigation (human acceptance)": "HumanAcceptance.md",
    "network/persistence": "Network.swift",
    "network/persistence (privacy)": "Privacy.swift",
    "accessibility": "Accessibility.swift",
    "localization": "Localizable.strings",
    "tests": "JourneyTests.swift",
    "instrumentation": "Instrumentation.swift",
    "instrumentation (performance budgets)": "PerformanceBudgets.swift",
}

# No filename, extension, or framework word may appear anywhere in a
# semantic unit -- this is the literal M1.04 requirement, checked below
# against every semantic unit's own repr(), not just a spot check.
ENGINE_SPECIFIC_STRINGS = (
    ".swift", ".kt", ".dart", ".tsx", ".strings", ".md",
    "swift", "kotlin", "flutter", "dart", "react", "android", "ios",
)


class CompilePlanTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.outcome_path = os.path.join(self.tmp, "outcome.json")
        with open(self.outcome_path, "w", encoding="utf-8") as fh:
            json.dump(VALID_OUTCOME, fh)
        self.schema = CC.load_json(MJC.DEFAULT_SCHEMA, "schema")

    def journey(self, base=None, **overrides):
        rec = dict(base or FULL_JOURNEY, outcome_contract_ref=self.outcome_path)
        rec.update(overrides)
        return rec

    def test_full_contract_produces_a_traceable_unit_per_category(self):
        record = self.journey()
        self.assertEqual(MJC.check(record, self.schema), [],
                         "fixture itself must validate before it proves anything")
        units = MPC.compile_plan(record, schema=self.schema)

        by_category = {u["category"]: u for u in units}
        self.assertEqual(set(by_category), EXPECTED_CATEGORIES)

        # Every unit traces to a specific field: the contract's own value
        # for that field must appear verbatim in the unit's objective, not
        # a generic templated sentence.
        a11y = by_category["accessibility"]
        self.assertEqual(a11y["contract_field"], "accessibility_obligations")
        for obligation in record["accessibility_obligations"]:
            self.assertIn(obligation, a11y["objective"])
        self.assertEqual(a11y["contract_values"],
                         record["accessibility_obligations"])

        domain = by_category["domain/state"]
        self.assertIn(record["entry_state"], domain["objective"])
        self.assertIn(record["exit_state"], domain["objective"])

        view = by_category["view/navigation"]
        self.assertIn(record["human_outcome"], view["objective"])

        tests_unit = by_category["tests"]
        self.assertIn(record["required_native_tests"][0], tests_unit["objective"])

        locale_unit = by_category["localization"]
        for locale in record["locales"]:
            self.assertIn(locale, locale_unit["objective"])

        perf = by_category["instrumentation (performance budgets)"]
        budget = record["performance_budgets"][0]
        self.assertIn(budget["metric"], perf["objective"])
        self.assertIn(budget["budget"], perf["objective"])

    def test_units_match_the_real_work_record_unit_shape(self):
        """The compiled units are not an invented shape: they pass this
        estate's own work_record.check_units(), the same validator every
        other Brother unit answers to."""
        record = self.journey()
        units = MPC.compile_plan(record, schema=self.schema)
        problems = WR.check_units(units)
        self.assertEqual(problems, [], problems)

    def test_compile_plan_reproduces_the_pre_split_ios_filenames(self):
        """The backward-compatible entrypoint must still emit the same
        mobile/<journey>/<Name>.<ext> paths this module emitted before the
        M1.04 split, since real callers (canary_pipeline_smoke.py) depend
        on owns/done_check being real, stable paths."""
        record = self.journey()
        units = MPC.compile_plan(record, schema=self.schema)
        by_category = {u["category"]: u for u in units}
        for category, filename in IOS_FILENAME_BY_CATEGORY.items():
            expected = "mobile/guest-checkout/%s" % filename
            self.assertEqual(by_category[category]["owns"], [expected], category)
            self.assertEqual(by_category[category]["writes"], [expected], category)
            self.assertEqual(by_category[category]["done_check"],
                             "test -f %s" % expected, category)

    def test_empty_accessibility_obligations_generates_a_stated_gap_unit(self):
        record = self.journey(accessibility_obligations=[])
        self.assertEqual(MJC.check(record, self.schema), [])
        units = MPC.compile_plan(record, schema=self.schema)
        by_category = {u["category"]: u for u in units}
        a11y = by_category["accessibility"]
        # Still one unit, never silently dropped.
        self.assertEqual(a11y["contract_field"], "accessibility_obligations")
        self.assertEqual(a11y["contract_values"], [])
        self.assertIn("no accessibility_obligations declared", a11y["objective"])
        self.assertIn("confirm this is deliberate, not an oversight",
                      a11y["objective"])
        # The unit is still a real dispatchable unit, not an empty stub.
        self.assertTrue(a11y["done_check"].strip())
        self.assertTrue(a11y["owns"])

    def test_multiple_empty_optional_arrays_each_get_their_own_gap_unit(self):
        record = self.journey(
            network_state_obligations=[], interruption_obligations=[],
            privacy_constraints=[], visual_reference_ids=[],
            human_acceptance_items=[], post_release_claims=[],
            performance_budgets=[])
        self.assertEqual(MJC.check(record, self.schema), [])
        units = MPC.compile_plan(record, schema=self.schema)
        by_category = {u["category"]: u for u in units}
        gap_categories = [
            "domain/state (interruption handling)",
            "view/navigation (visual reference)",
            "view/navigation (human acceptance)",
            "network/persistence",
            "network/persistence (privacy)",
            "instrumentation",
            "instrumentation (performance budgets)",
        ]
        for cat in gap_categories:
            self.assertIn("confirm this is deliberate, not an oversight",
                          by_category[cat]["objective"], cat)
        # All twelve units are still present -- nothing vanished.
        self.assertEqual(len(units), 12)

    def test_invalid_journey_contract_is_refused_before_any_unit_is_built(self):
        record = self.journey()
        del record["required_native_tests"]  # required field, minItems 1
        self.assertNotEqual(MJC.check(record, self.schema), [])
        with self.assertRaises(MPC.PlanCompilerError):
            MPC.compile_plan(record, schema=self.schema)

    def test_invalid_outcome_contract_ref_also_refuses(self):
        record = self.journey(outcome_contract_ref="/no/such/file.json")
        with self.assertRaises(MPC.PlanCompilerError) as ctx:
            MPC.compile_plan(record, schema=self.schema)
        self.assertIn("guest-checkout", str(ctx.exception))

    def test_android_profile_reports_fallback_instead_of_a_silent_pass(self):
        """Major 1 adversarial case (adversarial review of PR #705): a real
        project-profile record whose declared platform (android) matches no
        known adapter must not silently emit .swift files behind a bare
        "PASS: N unit(s) generated" -- the CLI must say the profile fell
        through to the default adapter."""
        record_path = os.path.join(self.tmp, "journey.json")
        with open(record_path, "w", encoding="utf-8") as fh:
            json.dump(self.journey(), fh)
        profile_path = os.path.join(self.tmp, "profile.json")
        with open(profile_path, "w", encoding="utf-8") as fh:
            json.dump({"schema_version": "mobile-project-profile-v1",
                      "platforms": ["android"], "frameworks": []}, fh)
        out, err = io.StringIO(), io.StringIO()
        with redirect_stdout(out), redirect_stderr(err):
            code = MPC.main([record_path, "--profile", profile_path])
        self.assertEqual(code, 0)
        self.assertIn("NO-DATA", err.getvalue())
        self.assertIn("android", err.getvalue())
        units = json.loads(out.getvalue())
        self.assertTrue(units)
        self.assertTrue(all(u["owns"][0].endswith(".swift")
                            or u["owns"][0].endswith(".strings")
                            or u["owns"][0].endswith(".md") for u in units))


class SemanticPlanTests(unittest.TestCase):
    """EPIC M1.04: the semantic layer carries no filename, extension, or
    framework word anywhere -- checked against every unit's own repr(),
    not a spot check of one field."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.outcome_path = os.path.join(self.tmp, "outcome.json")
        with open(self.outcome_path, "w", encoding="utf-8") as fh:
            json.dump(VALID_OUTCOME, fh)
        self.schema = CC.load_json(MJC.DEFAULT_SCHEMA, "schema")
        self.record = dict(FULL_JOURNEY, outcome_contract_ref=self.outcome_path)

    def test_semantic_plan_shape(self):
        plan = MPC.compile_semantic_plan(self.record, schema=self.schema)
        self.assertEqual(plan["journey_id"], "guest-checkout")
        self.assertEqual(len(plan["units"]), 12)
        for unit in plan["units"]:
            self.assertIn("artifact_kind", unit)
            self.assertNotIn("owns", unit)
            self.assertNotIn("done_check", unit)
            self.assertNotIn("writes", unit)

    def test_semantic_plan_carries_no_engine_specific_string(self):
        plan = MPC.compile_semantic_plan(self.record, schema=self.schema)
        for unit in plan["units"]:
            haystack = repr(unit).lower()
            for bad in ENGINE_SPECIFIC_STRINGS:
                self.assertNotIn(bad, haystack,
                                 "semantic unit %r leaked engine-specific "
                                 "string %r: %r" % (unit["id"], bad, unit))

    def test_semantic_plan_refuses_an_invalid_contract_before_any_unit(self):
        record = dict(self.record)
        del record["required_native_tests"]
        with self.assertRaises(MPC.PlanCompilerError):
            MPC.compile_semantic_plan(record, schema=self.schema)

    def test_compile_project_plan_default_adapter_matches_compile_plan(self):
        semantic = MPC.compile_semantic_plan(self.record, schema=self.schema)
        via_project_plan = MPC.compile_project_plan(semantic)
        via_compile_plan = MPC.compile_plan(self.record, schema=self.schema)
        self.assertEqual(via_project_plan, via_compile_plan)

    def test_journey_id_with_traversal_segment_is_refused_at_entry(self):
        """PR #717 follow-up (Muse adversarial review, 2026-09-15, MINOR
        finding): a journey_id carrying '..' or '/' must be refused where
        it enters the compiler, not silently normalized down to a
        colliding owns path later. Refused before compile_project_plan()
        is ever reached, unlike the pre-follow-up behavior."""
        record = dict(self.record, journey_id="../../evil-journey")
        with self.assertRaises(MPC.PlanCompilerError):
            MPC.compile_semantic_plan(record, schema=self.schema)

    def test_journey_id_with_shell_metacharacter_is_refused_not_executed(self):
        """Reproduces the reviewer's exact adversarial probe for the MAJOR
        (shell-injection-via-done_check) finding: journey_id "ok; touch
        /tmp/PWNED_BY_DONE_CHECK" must be refused at compile_semantic_plan()
        so it never reaches a done_check string built anywhere, and the
        touch payload it names is never executed as a side effect of
        running this test."""
        payload_path = os.path.join(
            tempfile.gettempdir(), "PWNED_BY_DONE_CHECK_test_mobile_plan_compiler")
        if os.path.exists(payload_path):
            os.remove(payload_path)
        record = dict(self.record, journey_id="ok; touch %s" % payload_path)
        with self.assertRaises(MPC.PlanCompilerError):
            MPC.compile_semantic_plan(record, schema=self.schema)
        self.assertFalse(
            os.path.exists(payload_path),
            "journey_id shell metacharacter must never reach a shell")


class AdapterResolutionTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.outcome_path = os.path.join(self.tmp, "outcome.json")
        with open(self.outcome_path, "w", encoding="utf-8") as fh:
            json.dump(VALID_OUTCOME, fh)
        self.schema = CC.load_json(MJC.DEFAULT_SCHEMA, "schema")
        self.record = dict(FULL_JOURNEY, outcome_contract_ref=self.outcome_path)
        self.semantic = MPC.compile_semantic_plan(self.record, schema=self.schema)

    def test_no_profile_no_adapter_uses_the_default_ios_adapter(self):
        units = MPC.compile_project_plan(self.semantic)
        view = next(u for u in units if u["id"] == "guest-checkout-view")
        self.assertTrue(view["owns"][0].endswith("View.swift"), view["owns"])

    def test_ios_project_profile_picks_the_ios_adapter(self):
        profile = {"schema_version": "mobile-project-profile-v1",
                  "platforms": ["ios"], "frameworks": ["ios-swift-package"]}
        units = MPC.compile_project_plan(self.semantic, project_profile=profile)
        view = next(u for u in units if u["id"] == "guest-checkout-view")
        self.assertTrue(view["owns"][0].endswith("View.swift"), view["owns"])

    def test_unrecognized_profile_platform_falls_back_to_default_adapter(self):
        profile = {"schema_version": "mobile-project-profile-v1",
                  "platforms": ["android"], "frameworks": []}
        units = MPC.compile_project_plan(self.semantic, project_profile=profile)
        default_units = MPC.compile_project_plan(self.semantic)
        self.assertEqual(units, default_units)

    def test_unrecognized_adapter_id_falls_back_to_default_rather_than_refusing(self):
        units = MPC.compile_project_plan(self.semantic, adapter="flutter")
        default_units = MPC.compile_project_plan(self.semantic)
        self.assertEqual(units, default_units)

    def test_an_explicit_adapter_dict_overrides_project_profile(self):
        custom_adapter = dict(MPC.ADAPTERS[MPC.DEFAULT_ADAPTER_ID])
        custom_adapter["view"] = ("Screen", "swift")
        units = MPC.compile_project_plan(
            self.semantic,
            project_profile={"platforms": ["ios"]},
            adapter=custom_adapter)
        view = next(u for u in units if u["id"] == "guest-checkout-view")
        self.assertTrue(view["owns"][0].endswith("Screen.swift"), view["owns"])

    def test_resolve_adapter_matches_compile_project_plan_default(self):
        adapter, matched = MPC.resolve_adapter()
        self.assertEqual(adapter, MPC.ADAPTERS[MPC.DEFAULT_ADAPTER_ID])
        self.assertTrue(matched, "no profile, no adapter: nothing mismatched")

    def test_malformed_project_profile_falls_back_rather_than_raising(self):
        """Muse adversarial review, EPIC M1.04: a malformed detection result
        is the same kind of gap as a missing one and must never crash the
        plan -- a non-dict profile, "platforms" not a list, and an
        unhashable platform entry all fall back to the default adapter."""
        default = MPC.ADAPTERS[MPC.DEFAULT_ADAPTER_ID]
        for bad_profile in ({"platforms": None}, "ios", {"platforms": [{}]},
                            {"platforms": 5}, 5, ["ios"]):
            adapter, matched = MPC.resolve_adapter(project_profile=bad_profile)
            self.assertEqual(adapter, default, bad_profile)
            # A profile was given and nothing in it matched: the caller
            # must be able to see this was a fallback, not a real match.
            self.assertFalse(matched, bad_profile)

    def test_partial_custom_adapter_falls_back_per_missing_kind(self):
        """Muse adversarial review, EPIC M1.04: a caller-supplied adapter
        dict missing an artifact_kind must not KeyError the whole plan --
        that one kind falls back to the default adapter's own mapping."""
        incomplete = {"domain": ("Domain", "swift")}  # every other kind absent
        units = MPC.compile_project_plan(self.semantic, adapter=incomplete)
        self.assertEqual(len(units), 12)
        interruption = next(u for u in units if u["id"] == "guest-checkout-interruption")
        self.assertTrue(interruption["owns"][0].endswith("Interruption.swift"),
                        interruption["owns"])

    def test_resolve_adapter_refuses_a_traversal_stem_at_the_entry_point(self):
        """Reproduces the reviewer's exact adversarial probe (CRITICAL
        finding, mobile_ownership_resolver.py ~line 390): adapter stem
        "../../../../etc/evil" ext "swift" must be refused by
        resolve_adapter() itself, the one place both this module's own
        compile_project_plan() and mobile_ownership_resolver.py's
        resolve_unit() get their stem/ext pairs from -- so the sibling
        call site inherits the refusal without its own patch."""
        malicious_adapter = dict(MPC.ADAPTERS[MPC.DEFAULT_ADAPTER_ID])
        malicious_adapter["domain"] = ("../../../../etc/evil", "swift")
        with self.assertRaises(MPC.PlanCompilerError):
            MPC.resolve_adapter(adapter=malicious_adapter)

    def test_done_check_path_component_is_shell_quoted(self):
        """Execution-boundary defense in depth (this estate's own rule: an
        explicit failure path at every subprocess boundary), on top of the
        journey_id/stem/ext validator that already excludes shell
        metacharacters: the done_check string wraps its path component in
        shlex.quote()."""
        import shlex
        units = MPC.compile_project_plan(self.semantic)
        for unit in units:
            expected = "test -f %s" % shlex.quote(unit["owns"][0])
            self.assertEqual(unit["done_check"], expected, unit["id"])

    def test_adapter_stem_path_traversal_is_refused(self):
        """Major 2 adversarial case (adversarial review of PR #705):
        resolve_adapter()'s "isinstance(adapter, dict): return adapter"
        branch trusts a caller-supplied adapter dict verbatim, so a stem
        escaping mobile/<journey_id>/ must be refused before it reaches
        owns, never accepted into it."""
        malicious = dict(MPC.ADAPTERS[MPC.DEFAULT_ADAPTER_ID])
        malicious["domain"] = ("../../scripts/mobile_plan_compiler", "py")
        with self.assertRaises(MPC.PlanCompilerError) as ctx:
            MPC.compile_project_plan(self.semantic, adapter=malicious)
        self.assertIn("domain", str(ctx.exception))

    def test_adapter_ext_path_traversal_is_refused(self):
        """Same class of defect as the stem case above, via ext instead."""
        malicious = dict(MPC.ADAPTERS[MPC.DEFAULT_ADAPTER_ID])
        malicious["domain"] = ("Domain", "../../scripts/mobile_plan_compiler")
        with self.assertRaises(MPC.PlanCompilerError):
            MPC.compile_project_plan(self.semantic, adapter=malicious)


if __name__ == "__main__":
    unittest.main(verbosity=2)

#!/usr/bin/env python3
"""Tests for mobile_plan_compiler.py (WBS-30.04).

Three properties, one case each: a full realistic journey contract produces
a traceable unit for every named category; an empty
accessibility_obligations list produces a stated-gap unit rather than
silently skipping the category; an invalid journey contract is refused
before any unit is generated, never partially compiled.
"""
import json
import os
import sys
import tempfile
import unittest

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
        expected_categories = {
            "state/domain logic",
            "state/domain logic (interruption handling)",
            "SwiftUI view",
            "SwiftUI view (visual reference)",
            "SwiftUI view (human acceptance)",
            "persistence/network",
            "persistence/network (privacy)",
            "accessibility",
            "localization",
            "tests",
            "instrumentation",
            "instrumentation (performance budgets)",
        }
        self.assertEqual(set(by_category), expected_categories)

        # Every unit traces to a specific field: the contract's own value
        # for that field must appear verbatim in the unit's objective, not
        # a generic templated sentence.
        a11y = by_category["accessibility"]
        self.assertEqual(a11y["contract_field"], "accessibility_obligations")
        for obligation in record["accessibility_obligations"]:
            self.assertIn(obligation, a11y["objective"])
        self.assertEqual(a11y["contract_values"],
                         record["accessibility_obligations"])

        domain = by_category["state/domain logic"]
        self.assertIn(record["entry_state"], domain["objective"])
        self.assertIn(record["exit_state"], domain["objective"])

        view = by_category["SwiftUI view"]
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
            "state/domain logic (interruption handling)",
            "SwiftUI view (visual reference)",
            "SwiftUI view (human acceptance)",
            "persistence/network",
            "persistence/network (privacy)",
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


if __name__ == "__main__":
    unittest.main(verbosity=2)

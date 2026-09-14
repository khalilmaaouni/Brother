#!/usr/bin/env python3
"""WBS-30.11 Post-release BrotherDS link: the mobile path names claim IDs,
it never scores them. Those claims (completion rate, crash-free sessions,
latency, return usage, helpfulness) belong to BrotherDS, an external
system this repo does not yet implement. This is a boundary test, not a
new module: it proves the boundary holds structurally across the real
mobile-path modules that landed tonight, not just as a schema comment
nobody enforces.
"""
import importlib
import inspect
import json
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# Every module built tonight in the WBS-30 mobile chain that could
# plausibly touch a claim ID. Named explicitly rather than a directory
# scan, so a new module is only covered once someone deliberately adds
# it here -- silent coverage of "whatever exists" would let a real
# scoring function slip in unnoticed by this test.
MOBILE_PATH_MODULES = [
    "mobile_journey_contract",
    "mobile_reference_lock",
    "mobile_design",
    "mobile_plan_compiler",
    "native_evidence_v2",
    "device_matrix",
    "release_state_tracker",
]

# Words that would signal a function computes a claim's VALUE rather than
# just passing its ID string through. Deliberately narrow: catches the
# shape of a real violation (a function computing/scoring/measuring a
# named claim metric) without flagging every unrelated use of "score" or
# "rate" elsewhere in these modules for other purposes.
CLAIM_METRIC_NAMES = (
    "completion_rate", "crash_free", "crash-free", "return_usage",
    "helpfulness_score", "claim_value", "score_claim", "compute_claim",
)


class ClaimScoringBoundaryTests(unittest.TestCase):
    def test_post_release_claims_is_a_plain_string_array_never_an_object(self):
        with open(os.path.join(
                ROOT, "docs", "schema", "mobile-journey-contract-v1.json"),
                encoding="utf-8") as fh:
            schema = json.load(fh)
        field = schema["properties"]["post_release_claims"]
        self.assertEqual(field["type"], "array")
        self.assertEqual(field["items"], {"type": "string"},
                          "post_release_claims must hold ID strings only, "
                          "never a scored object -- scoring belongs to BrotherDS")

    def test_no_mobile_path_module_defines_a_claim_scoring_function(self):
        violations = []
        for name in MOBILE_PATH_MODULES:
            mod = importlib.import_module(name)
            for fn_name, fn in inspect.getmembers(mod, inspect.isfunction):
                if fn.__module__ != mod.__name__:
                    continue  # imported, not defined here
                haystack = fn_name.lower()
                for metric in CLAIM_METRIC_NAMES:
                    if metric in haystack:
                        violations.append("%s.%s" % (name, fn_name))
        self.assertEqual(violations, [],
                          "found function(s) that look like they compute a "
                          "BrotherDS claim value inside the mobile path: %s "
                          "-- mobile names claim IDs, it never scores them" % violations)

    def test_journey_contract_smoke_accepts_claim_ids_as_plain_strings(self):
        import mobile_journey_contract as MJC
        with open(MJC.DEFAULT_SCHEMA, encoding="utf-8") as fh:
            schema = json.load(fh)
        rec = {
            "schema_version": "mobile-journey-contract-v1", "journey_id": "j",
            "outcome_contract_ref": os.path.join(
                ROOT, "docs", "decisions",
                "convergence-1.0.17-baseline-2026-09-13.json"),
            "human_outcome": "x", "entry_state": "x", "exit_state": "x",
            "supported_device_classes": ["iphone"], "supported_os_range": "17-18",
            "locales": ["en"], "accessibility_obligations": [],
            "network_state_obligations": [], "interruption_obligations": [],
            "privacy_constraints": [], "performance_budgets": [],
            "visual_reference_ids": [], "required_native_tests": ["t1"],
            "human_acceptance_items": [],
            "post_release_claims": ["completion_rate", "crash_free_sessions"],
        }
        problems = MJC.check(rec, schema)
        self.assertEqual(problems, [], problems)


if __name__ == "__main__":
    unittest.main()

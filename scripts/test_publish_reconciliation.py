#!/usr/bin/env python3
"""Calibration for scripts/publish_reconciliation.py (WBS-40.10).

The property under test: a complete, internally consistent reconciliation
record has all seven checks PASS; a count mismatch (attempted != published
+ rejected) is a real FAIL, never silently accepted; a rejected_entities
list that disagrees with counts.rejected is its own internal-consistency
FAIL; a downstream consumer that never confirmed receipt is reported as its
own NO-DATA entry in the per-consumer breakdown, never silently omitted; an
orphan reference is detected and named; a duplication-recurrence case is
caught; a placeholder publish_timestamp FAILs rather than PASSing; and the
completeness object cannot be misread as all-fine when several checks are
genuinely NO-DATA (mirrors journey_passport.py's own defense for its
domain). Synthetic fixture data only (fake consumer systems, fake entity
ids), never anything resembling a real client's or its integration vendors' data.
"""
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import publish_reconciliation as PR  # noqa: E402


def complete_kwargs(**overrides):
    """A structurally complete, internally consistent set of evidence: all
    seven checks should independently PASS from this fixture as-is."""
    kwargs = {
        "master_id": "mdm-master-0001",
        "counts_evidence": {"attempted": 10, "published": 8, "rejected": 2},
        "rejected_entities_evidence": [
            {"entity_id": "acme-crm:cust-901", "reason": "duplicate key collision"},
            {"entity_id": "acme-crm:cust-902", "reason": "failed required-field validation"},
        ],
        "downstream_reconciliation_evidence": {
            "fake-warehouse-system": {"confirmed": True, "receipt_id": "recv-1"},
            "fake-reporting-system": {"confirmed": True, "receipt_id": "recv-2"},
        },
        "schema_expectation_evidence": {
            "expected_schema": "golden-master-contract-v1",
            "observed_schema": "golden-master-contract-v1",
        },
        "orphan_references_evidence": {
            "checked_references": ["ref-1", "ref-2", "ref-3"],
            "orphans": [],
        },
        "duplication_recurrence_evidence": {
            "previously_resolved_duplicate_ids": ["dup-1", "dup-2"],
            "recurred_ids": [],
        },
        "publish_timestamp": "2026-09-13T10:00:00+00:00",
    }
    kwargs.update(overrides)
    return kwargs


class VerifyReconciliationCompleteTests(unittest.TestCase):
    def test_complete_consistent_record_all_seven_pass(self):
        record = PR.verify_reconciliation(**complete_kwargs())
        self.assertEqual(
            record["field_verdicts"],
            {
                "counts": "PASS",
                "rejected_entities": "PASS",
                "downstream_reconciliation": "PASS",
                "schema_expectation": "PASS",
                "orphan_references": "PASS",
                "duplication_recurrence": "PASS",
                "publish_timestamp": "PASS",
            },
        )
        self.assertEqual(record["completeness"]["failed_count"], 0)
        self.assertEqual(record["completeness"]["no_data_count"], 0)
        self.assertEqual(record["completeness"]["total_checks"], 7)
        self.assertIn("ALL 7 CHECK(S) ASSESSED: PASS.", record["completeness"]["headline"])
        self.assertEqual(PR.exit_code_for_completeness(record["completeness"]), 0)


class CountsMismatchTests(unittest.TestCase):
    def test_attempted_published_rejected_mismatch_is_a_real_fail(self):
        kwargs = complete_kwargs(counts_evidence={"attempted": 10, "published": 8, "rejected": 1})
        record = PR.verify_reconciliation(**kwargs)
        self.assertEqual(record["counts"]["verdict"], "FAIL")
        self.assertIn("mismatch", record["counts"]["verdict_reason"])
        self.assertEqual(record["field_verdicts"]["counts"], "FAIL")
        self.assertIn("counts", record["completeness"]["failed"])
        self.assertTrue(record["completeness"]["headline"].startswith("BLOCKING FAILURE"))
        self.assertEqual(PR.exit_code_for_completeness(record["completeness"]), 1)

    def test_rejected_entities_count_disagrees_with_counts_rejected(self):
        # counts says 2 rejected, but only one entity is actually listed:
        # an internal-consistency FAIL distinct from the raw counts FAIL above.
        kwargs = complete_kwargs(rejected_entities_evidence=[
            {"entity_id": "acme-crm:cust-901", "reason": "duplicate key collision"},
        ])
        record = PR.verify_reconciliation(**kwargs)
        self.assertEqual(record["counts"]["verdict"], "PASS")
        self.assertEqual(record["rejected_entities"]["verdict"], "FAIL")
        self.assertIn("counts.rejected", record["rejected_entities"]["verdict_reason"])


class DownstreamReconciliationTests(unittest.TestCase):
    def test_consumer_that_never_confirmed_is_its_own_no_data_entry(self):
        kwargs = complete_kwargs(downstream_reconciliation_evidence={
            "fake-warehouse-system": {"confirmed": True, "receipt_id": "recv-1"},
            "fake-reporting-system": {},  # never confirmed receipt
        })
        record = PR.verify_reconciliation(**kwargs)
        per_consumer = record["downstream_reconciliation"]["per_consumer"]
        self.assertIn("fake-reporting-system", per_consumer)
        self.assertEqual(per_consumer["fake-reporting-system"]["verdict"], "NO-DATA")
        self.assertEqual(per_consumer["fake-warehouse-system"]["verdict"], "PASS")
        # worst-of: one NO-DATA consumer pulls the whole check to NO-DATA,
        # never silently rounds up to PASS because most consumers confirmed.
        self.assertEqual(record["downstream_reconciliation"]["verdict"], "NO-DATA")
        self.assertIn("downstream_reconciliation", record["completeness"]["no_data"])

    def test_consumer_that_explicitly_denies_receipt_fails(self):
        kwargs = complete_kwargs(downstream_reconciliation_evidence={
            "fake-warehouse-system": {"confirmed": False, "receipt_id": None},
        })
        record = PR.verify_reconciliation(**kwargs)
        self.assertEqual(record["downstream_reconciliation"]["verdict"], "FAIL")


class OrphanReferenceTests(unittest.TestCase):
    def test_orphan_reference_is_detected_and_named(self):
        kwargs = complete_kwargs(orphan_references_evidence={
            "checked_references": ["ref-1", "ref-2", "ref-3"],
            "orphans": [{"reference_id": "ref-3", "points_to": "master-9999", "reason": "target no longer resolves"}],
        })
        record = PR.verify_reconciliation(**kwargs)
        self.assertEqual(record["orphan_references"]["verdict"], "FAIL")
        self.assertEqual([o["reference_id"] for o in record["orphan_references"]["orphans"]], ["ref-3"])
        self.assertIn("ref-3", record["orphan_references"]["verdict_reason"])


class DuplicationRecurrenceTests(unittest.TestCase):
    def test_previously_resolved_duplicate_reappearing_fails(self):
        kwargs = complete_kwargs(duplication_recurrence_evidence={
            "previously_resolved_duplicate_ids": ["dup-1", "dup-2"],
            "recurred_ids": ["dup-1"],
        })
        record = PR.verify_reconciliation(**kwargs)
        self.assertEqual(record["duplication_recurrence"]["verdict"], "FAIL")
        self.assertIn("dup-1", record["duplication_recurrence"]["verdict_reason"])
        self.assertIn("duplication_recurrence", record["completeness"]["failed"])


class PublishTimestampTests(unittest.TestCase):
    def test_placeholder_timestamp_fails_not_passes(self):
        kwargs = complete_kwargs(publish_timestamp="TBD")
        record = PR.verify_reconciliation(**kwargs)
        self.assertEqual(record["publish_timestamp"]["verdict"], "FAIL")
        self.assertIn("placeholder", record["publish_timestamp"]["verdict_reason"])

    def test_unparseable_timestamp_fails(self):
        kwargs = complete_kwargs(publish_timestamp="not-a-real-date")
        record = PR.verify_reconciliation(**kwargs)
        self.assertEqual(record["publish_timestamp"]["verdict"], "FAIL")

    def test_missing_timestamp_is_no_data_not_fail(self):
        kwargs = complete_kwargs(publish_timestamp=None)
        record = PR.verify_reconciliation(**kwargs)
        self.assertEqual(record["publish_timestamp"]["verdict"], "NO-DATA")


class CompletenessCannotBeMisreadTests(unittest.TestCase):
    def test_several_genuine_no_data_checks_never_read_as_fine(self):
        # Only counts and publish_timestamp supplied; the other five checks
        # are honestly not assessed. The property under test: this must not
        # collapse into a badge that reads "basically fine" -- it must name
        # every missing dimension and its exit code must not be 0.
        record = PR.verify_reconciliation(
            master_id="mdm-master-0002",
            counts_evidence={"attempted": 5, "published": 5, "rejected": 0},
            publish_timestamp="2026-09-13T10:00:00+00:00",
        )
        self.assertEqual(record["completeness"]["failed_count"], 0)
        self.assertEqual(record["completeness"]["no_data_count"], 5)
        self.assertEqual(
            set(record["completeness"]["no_data"]),
            {"rejected_entities", "downstream_reconciliation", "schema_expectation",
             "orphan_references", "duplication_recurrence"},
        )
        self.assertIn("INCOMPLETE", record["completeness"]["headline"])
        self.assertNotIn("ALL 7 CHECK(S) ASSESSED: PASS.", record["completeness"]["headline"])
        # exit code must distinguish "incomplete" from "clean": never 0.
        self.assertEqual(PR.exit_code_for_completeness(record["completeness"]), 2)

    def test_a_single_fail_among_several_no_data_still_reads_as_blocking(self):
        record = PR.verify_reconciliation(
            master_id="mdm-master-0003",
            counts_evidence={"attempted": 5, "published": 4, "rejected": 0},  # mismatch -> FAIL
            publish_timestamp="2026-09-13T10:00:00+00:00",
        )
        self.assertEqual(record["completeness"]["failed_count"], 1)
        self.assertGreater(record["completeness"]["no_data_count"], 0)
        self.assertTrue(record["completeness"]["headline"].startswith("BLOCKING FAILURE"))
        self.assertEqual(PR.exit_code_for_completeness(record["completeness"]), 1)


if __name__ == "__main__":
    unittest.main()

"""Tests for feature_admission.admit(). Plain unittest, no fixtures package.
Run: python3 scripts/test_feature_admission.py -v
"""
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import feature_admission


def valid_record():
    """A fully valid feature record that admits on its own."""
    return {
        "measurable_outcome": "checkout latency under 200 ms",
        "baseline": "checkout latency is 900 ms",
        "expected_mechanism": "cache the pricing lookup before checkout",
        "exact_files": ["checkout.py", "cache.py"],
        "done_check": "python3 -m pytest tests/test_checkout.py",
        "negative_done_check": "python3 -m pytest tests/test_checkout.py::test_cache_miss",
        "evidence_artifact": "reports/checkout_latency.txt",
        "rollback": "remove the cache layer and redeploy the previous build",
    }


class FeatureAdmissionTests(unittest.TestCase):
    def test_empty_dict_refused(self):
        verdict, reason = feature_admission.admit({})
        self.assertEqual(verdict, "REFUSE")
        self.assertIn("missing field", reason)

    def test_missing_done_check_refused(self):
        record = valid_record()
        del record["done_check"]
        verdict, reason = feature_admission.admit(record)
        self.assertEqual(verdict, "REFUSE")
        self.assertIn("done_check", reason)

    def test_every_required_field_missing_is_refused_by_name(self):
        """Added by the orchestrator after its own mutation SURVIVED: making
        evidence_artifact optional (still allowed, no longer required) left
        all 21 tests green, because only three of the eight required fields
        had a missing-field test. The deciding property is 'no named
        evidence, no admission', so every required field is driven here.
        The expected set is pinned here, not read back from the module, so
        dropping a field from REQUIRED_FIELDS cannot shrink this test."""
        expected = set(valid_record())
        self.assertEqual(set(feature_admission.REQUIRED_FIELDS), expected)
        for field in sorted(expected):
            with self.subTest(field=field):
                record = valid_record()
                del record[field]
                verdict, reason = feature_admission.admit(record)
                self.assertEqual(verdict, "REFUSE")
                self.assertIn(field, reason)

    def test_missing_baseline_refused(self):
        record = valid_record()
        del record["baseline"]
        verdict, reason = feature_admission.admit(record)
        self.assertEqual(verdict, "REFUSE")
        self.assertIn("baseline", reason)

    def test_missing_rollback_refused(self):
        record = valid_record()
        del record["rollback"]
        verdict, reason = feature_admission.admit(record)
        self.assertEqual(verdict, "REFUSE")
        self.assertIn("rollback", reason)

    def test_extra_unrecognized_key_refused(self):
        record = valid_record()
        record["priority"] = "high"
        verdict, reason = feature_admission.admit(record)
        self.assertEqual(verdict, "REFUSE")
        self.assertIn("priority", reason)

    def test_id_field_allowed(self):
        record = valid_record()
        record["id"] = "row-1"
        verdict, reason = feature_admission.admit(record)
        self.assertEqual(verdict, "ADMIT")

    def test_exact_files_as_string_refused(self):
        record = valid_record()
        record["exact_files"] = "checkout.py"
        verdict, reason = feature_admission.admit(record)
        self.assertEqual(verdict, "REFUSE")
        self.assertIn("exact_files", reason)

    def test_exact_files_as_empty_list_refused(self):
        record = valid_record()
        record["exact_files"] = []
        verdict, reason = feature_admission.admit(record)
        self.assertEqual(verdict, "REFUSE")
        self.assertIn("exact_files", reason)

    def test_exact_files_with_empty_string_refused(self):
        record = valid_record()
        record["exact_files"] = ["checkout.py", ""]
        verdict, reason = feature_admission.admit(record)
        self.assertEqual(verdict, "REFUSE")
        self.assertIn("exact_files", reason)

    def test_exact_files_with_non_string_entry_refused(self):
        record = valid_record()
        record["exact_files"] = ["checkout.py", 7]
        verdict, reason = feature_admission.admit(record)
        self.assertEqual(verdict, "REFUSE")
        self.assertIn("exact_files", reason)

    def test_whitespace_only_field_refused(self):
        record = valid_record()
        record["rollback"] = "   "
        verdict, reason = feature_admission.admit(record)
        self.assertEqual(verdict, "REFUSE")
        self.assertIn("rollback", reason)

    def test_non_string_field_refused(self):
        record = valid_record()
        record["baseline"] = 900
        verdict, reason = feature_admission.admit(record)
        self.assertEqual(verdict, "REFUSE")
        self.assertIn("baseline", reason)

    def test_valid_record_admits(self):
        record = valid_record()
        verdict, reason = feature_admission.admit(record)
        self.assertEqual(verdict, "ADMIT")
        self.assertIn(record["done_check"], reason)

    def test_exact_duplicate_refused(self):
        existing = [valid_record()]
        incoming = valid_record()
        verdict, reason = feature_admission.admit(incoming, existing=existing)
        self.assertEqual(verdict, "REFUSE")
        self.assertIn("duplicate", reason)

    def test_partial_overlap_still_admits(self):
        existing = [valid_record()]
        incoming = valid_record()
        incoming["exact_files"] = ["other_file.py"]
        verdict, reason = feature_admission.admit(incoming, existing=existing)
        self.assertEqual(verdict, "ADMIT")

    def test_existing_none_admits(self):
        record = valid_record()
        verdict, reason = feature_admission.admit(record, existing=None)
        self.assertEqual(verdict, "ADMIT")

    def test_existing_empty_list_admits(self):
        record = valid_record()
        verdict, reason = feature_admission.admit(record, existing=[])
        self.assertEqual(verdict, "ADMIT")

    def test_existing_with_malformed_prior_record_not_raised(self):
        record = valid_record()
        existing = ["not a record", None, {"measurable_outcome": "x"}]
        verdict, reason = feature_admission.admit(record, existing=existing)
        self.assertEqual(verdict, "ADMIT")

    def test_record_none_refused(self):
        verdict, reason = feature_admission.admit(None)
        self.assertEqual(verdict, "REFUSE")
        self.assertIn("mapping", reason)

    def test_record_string_refused(self):
        verdict, reason = feature_admission.admit("not a record")
        self.assertEqual(verdict, "REFUSE")
        self.assertIn("mapping", reason)

    def test_record_list_refused(self):
        verdict, reason = feature_admission.admit(["not", "a", "record"])
        self.assertEqual(verdict, "REFUSE")
        self.assertIn("mapping", reason)


if __name__ == "__main__":
    unittest.main()

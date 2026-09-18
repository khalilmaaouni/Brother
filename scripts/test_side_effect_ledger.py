#!/usr/bin/env python3
"""Tests for scripts/side_effect_ledger.py (DOM-40.06). stdlib only, temp
dirs, no network, no real subprocess side effects: "process_spawned" and
"package_install" entries below are just labels in a detail dict, nothing
is actually spawned or installed."""
import os
import shutil
import tempfile
import unittest

import side_effect_ledger as sel


class LedgerTestCase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="sel-test-")
        self.repo_root = os.path.join(self.tmp, "repo")
        os.makedirs(self.repo_root)
        self.path = os.path.join(self.tmp, "ledger.jsonl")

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)


class TestBucketing(LedgerTestCase):
    def test_external_kinds_bucket_external(self):
        for kind, detail in [
            ("network", {"url": "https://example.test"}),
            ("api_call", {"endpoint": "https://api.example.test/x"}),
            ("package_install", {"package": "requests==2.0"}),
            ("process_spawned", {"argv": ["echo", "hi"]}),
            ("database_mutation", {"query": "UPDATE t SET x=1"}),
        ]:
            entry = sel.record(self.path, kind, detail, observed=True)
            self.assertEqual(entry["bucket"], sel.EXTERNAL, kind)
        result = sel.summary(self.path)
        self.assertEqual(result["verdict"], "clean")
        self.assertEqual(result["external"]["count"], 5)
        self.assertEqual(result["repository"]["count"], 0)
        self.assertEqual(result["unknown"]["count"], 0)

    def test_filesystem_outside_repo_buckets_external(self):
        outside = os.path.join(self.tmp, "outside.txt")
        entry = sel.record(self.path, sel.FILESYSTEM_KIND, {"path": outside},
                            observed=True, repo_root=self.repo_root)
        self.assertEqual(entry["bucket"], sel.EXTERNAL)

    def test_filesystem_label_mismatch_inside_repo_is_reclassified(self):
        # Declared "outside the repository" but the real path lands inside:
        # the module trusts the path, not the caller's label.
        inside = os.path.join(self.repo_root, "sneaky.txt")
        entry = sel.record(self.path, sel.FILESYSTEM_KIND, {"path": inside},
                            observed=True, repo_root=self.repo_root)
        self.assertEqual(entry["bucket"], sel.REPOSITORY)
        result = sel.summary(self.path)
        self.assertEqual(result["repository"]["count"], 1)
        self.assertEqual(result["external"]["count"], 0)

    def test_filesystem_reverse_case_outside_stays_external(self):
        # Symmetric check: a genuinely outside path is not swept into
        # repository just because the classifier now reclassifies.
        outside = os.path.join(self.tmp, "elsewhere.txt")
        entry = sel.record(self.path, sel.FILESYSTEM_KIND, {"path": outside},
                            observed=True, repo_root=self.repo_root)
        self.assertEqual(entry["bucket"], sel.EXTERNAL)

    def test_filesystem_without_repo_root_is_unknown(self):
        entry = sel.record(self.path, sel.FILESYSTEM_KIND,
                            {"path": "/tmp/whatever"}, observed=True)
        self.assertEqual(entry["bucket"], sel.UNKNOWN)

    def test_unrecognised_kind_is_unknown_not_dropped(self):
        entry = sel.record(self.path, "smoke_signal", {"note": "??"},
                            observed=False)
        self.assertEqual(entry["bucket"], sel.UNKNOWN)
        result = sel.summary(self.path)
        self.assertEqual(result["unknown"]["count"], 1)


class TestObservability(LedgerTestCase):
    def test_observed_vs_declared_is_recorded(self):
        observed = sel.record(self.path, "network", {"url": "x"},
                               observed=True)
        declared = sel.record(self.path, "network", {"url": "y"},
                               observed=False)
        self.assertEqual(observed["evidence"], "observed")
        self.assertEqual(declared["evidence"], "declared")


class TestEmptyLedger(LedgerTestCase):
    def test_empty_ledger_is_not_established_empty_by_default(self):
        # DECISION: zero entries is not proof of zero effects. A ledger
        # nobody wrote to could mean nothing happened, or that nothing was
        # instrumented at all -- indistinguishable from the file alone.
        result = sel.summary(self.path)
        self.assertEqual(result["verdict"], "clean")
        self.assertEqual(result["unknown"]["count"], 0)
        self.assertFalse(result["unknown"]["established_empty"])
        self.assertIn("not proof", result["note"])

    def test_coverage_confirmed_establishes_empty_unknown(self):
        sel.record(self.path, sel.COVERAGE_CONFIRMED_KIND,
                    {"checked": "every subprocess"}, observed=True)
        result = sel.summary(self.path)
        self.assertEqual(result["unknown"]["count"], 0)
        self.assertTrue(result["unknown"]["established_empty"])
        # the marker itself is not an effect and must not inflate any bucket
        self.assertEqual(result["count"], 0)


class TestUnknownNeverSilentlyZero(LedgerTestCase):
    def test_unknown_bucket_reported_even_when_nonzero(self):
        sel.record(self.path, "database_mutation", {"query": "x"},
                    observed=True)
        sel.record(self.path, "mystery", {"note": "uninstrumented subprocess"},
                    observed=False)
        result = sel.summary(self.path)
        self.assertEqual(result["external"]["count"], 1)
        self.assertEqual(result["unknown"]["count"], 1)
        self.assertNotEqual(result["unknown"]["count"], 0)


class TestCorruption(LedgerTestCase):
    def test_unreadable_ledger_is_no_data_not_clean(self):
        os.makedirs(self.path)  # a directory where a file is expected
        result = sel.summary(self.path)
        self.assertEqual(result["verdict"], sel.NODATA)

    def test_truncated_last_line_is_no_data_on_summary(self):
        sel.record(self.path, "network", {"url": "a"}, observed=True)
        with open(self.path, "a", encoding="utf-8") as fh:
            fh.write('{"kind": "network", "detail": {"path": "b"')  # cut off
        result = sel.summary(self.path)
        self.assertEqual(result["verdict"], sel.NODATA)

    def test_truncated_last_line_refuses_append(self):
        sel.record(self.path, "network", {"url": "a"}, observed=True)
        with open(self.path, "a", encoding="utf-8") as fh:
            fh.write('{"kind": "network"')  # cut off
        with self.assertRaises(sel.LedgerError):
            sel.record(self.path, "network", {"url": "b"}, observed=True)

    def test_duplicate_effect_is_recorded_twice_not_merged(self):
        # An effect recorded twice must count twice: silently merging
        # duplicates would hide that it actually happened twice.
        sel.record(self.path, "process_spawned", {"argv": ["x"]}, observed=True)
        sel.record(self.path, "process_spawned", {"argv": ["x"]}, observed=True)
        result = sel.summary(self.path)
        self.assertEqual(result["external"]["count"], 2)


class TestMutationTarget(LedgerTestCase):
    """The property the mutation proof exercises directly: folding the
    unknown bucket into external (or dropping it) must make this fail."""

    def test_unknown_is_a_distinct_bucket_from_external(self):
        sel.record(self.path, "totally_unrecognised", {}, observed=False)
        result = sel.summary(self.path)
        self.assertEqual(result["unknown"]["count"], 1)
        self.assertEqual(result["external"]["count"], 0)


if __name__ == "__main__":
    unittest.main(verbosity=2)

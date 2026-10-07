"""Tests for scripts/check_harness_efficiency_assessment.py (unit L4.5).

Every fixture is built in a temp folder, so this suite also runs on the
public export tree. The done check is:

    python3 -B -m unittest tests.test_harness_efficiency_assessment -v
"""

import io
import json
import os
import sys
import tempfile
import unittest
from contextlib import redirect_stderr
from unittest import mock

_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _REPO_ROOT not in sys.path:
    sys.path.insert(0, _REPO_ROOT)

import scripts.check_harness_efficiency_assessment as module_under_test
from scripts.check_harness_efficiency_assessment import (
    MECHANISM_IDS,
    main,
    validate_assessment,
)

QUOTE = "44.7-49.0% and API cost by about one third"
PIN = "a" * 64
EXCERPT_NAME = "excerpt.md"
EXCERPT_TEXT = "Archived abstract line. " + QUOTE + ", and the cost fell by a third.\n"


def _valid_doc():
    mechanisms = {}
    for mid in MECHANISM_IDS:
        mechanisms[mid] = {
            "paper_ref": "arXiv:2609.20519 (SoL-Pi)",
            "source_path": "https://arxiv.org/abs/2609.20519",
            "source_sha256": PIN,
            "source_quote": QUOTE,
            "retrieved_at": "2026-09-20",
            "source_version": "arXiv:2609.20519 abstract page",
            "excerpt_path": EXCERPT_NAME,
        }
    measurements = {
        "run-before": {
            "task": "task-1",
            "tree_hash": "abc123",
            "counter_name": "tokens",
            "counter_version": "1.0",
            "total_tokens": 1000,
            "raw_log_sha256": "b" * 64,
        },
        "run-after": {
            "task": "task-1",
            "tree_hash": "abc123",
            "counter_name": "tokens",
            "counter_version": "1.0",
            "total_tokens": 500,
            "raw_log_sha256": "c" * 64,
        },
    }
    proposals = []
    for mid in MECHANISM_IDS:
        proposals.append({
            "mechanism_id": mid,
            "applies": True,
            "before_run_id": "run-before",
            "after_run_id": "run-after",
            "delta_tokens": -500,
            "delta_percent": -50.0,
            "preservation_proof": "Result lines preserved byte for byte.",
        })
    return {
        "mechanisms": mechanisms,
        "measurements": measurements,
        "proposals": proposals,
        "human_sign_off": {"name": "Owner", "date": "2026-09-21"},
        "auto_gate_write": False,
    }


def _write_doc(folder, doc, excerpt=True):
    if excerpt:
        with open(os.path.join(folder, EXCERPT_NAME), "w", encoding="utf-8") as fh:
            fh.write(EXCERPT_TEXT)
    path = os.path.join(folder, "assessment.md")
    text = "# Assessment\n\n```json\n" + json.dumps(doc, indent=2) + "\n```\n"
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(text)
    return path


class AssessmentValidatorTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.folder = self.tmp.name

    def _fresh(self):
        doc = _valid_doc()
        path = _write_doc(self.folder, doc)
        return doc, path

    def test_valid_assessment_passes(self):
        _, path = self._fresh()
        self.assertEqual(validate_assessment(path), [])

    def test_main_prints_assessment_ok(self):
        _, path = self._fresh()
        rc = main([path])
        self.assertEqual(rc, 0)

    def test_empty_file_blocks(self):
        path = os.path.join(self.folder, "empty.md")
        with open(path, "w", encoding="utf-8"):
            pass
        errors = validate_assessment(path)
        self.assertTrue(errors)
        self.assertTrue(any("empty" in e for e in errors))

    def test_missing_mechanism_blocks(self):
        doc, _ = self._fresh()
        del doc["mechanisms"]["action_fusion"]
        path = _write_doc(self.folder, doc)
        errors = validate_assessment(path)
        self.assertTrue(any("missing mechanisms" in e for e in errors))

    def test_missing_citation_field_blocks(self):
        doc, _ = self._fresh()
        del doc["mechanisms"]["action_fusion"]["paper_ref"]
        path = _write_doc(self.folder, doc)
        errors = validate_assessment(path)
        self.assertTrue(any("paper_ref" in e for e in errors))

    def test_bad_source_sha256_blocks(self):
        doc, _ = self._fresh()
        doc["mechanisms"]["action_fusion"]["source_sha256"] = "zz"
        path = _write_doc(self.folder, doc)
        errors = validate_assessment(path)
        self.assertTrue(any("source_sha256" in e for e in errors))

    def test_quote_not_in_excerpt_blocks(self):
        doc, _ = self._fresh()
        doc["mechanisms"]["action_fusion"]["source_quote"] = "not archived"
        path = _write_doc(self.folder, doc)
        errors = validate_assessment(path)
        self.assertTrue(any("source_quote" in e for e in errors))

    def test_missing_excerpt_blocks(self):
        doc, _ = self._fresh()
        doc["mechanisms"]["action_fusion"]["excerpt_path"] = "missing-excerpt.md"
        path = _write_doc(self.folder, doc)
        errors = validate_assessment(path)
        self.assertTrue(any("excerpt" in e for e in errors))

    def test_missing_raw_log_sha256_blocks(self):
        doc, _ = self._fresh()
        del doc["measurements"]["run-before"]["raw_log_sha256"]
        path = _write_doc(self.folder, doc)
        errors = validate_assessment(path)
        self.assertTrue(any("raw_log_sha256" in e for e in errors))

    def test_hundred_percent_needs_after_log_sha(self):
        doc, _ = self._fresh()
        doc["measurements"]["run-after"]["total_tokens"] = 0
        doc["measurements"]["run-after"]["raw_log_sha256"] = "not-a-sha"
        for prop in doc["proposals"]:
            prop["delta_tokens"] = -1000
            prop["delta_percent"] = -100.0
        path = _write_doc(self.folder, doc)
        errors = validate_assessment(path)
        self.assertTrue(any("raw_log_sha256" in e for e in errors))

    def test_estimated_number_blocks(self):
        doc, _ = self._fresh()
        doc["mechanisms"]["action_fusion"]["source_quote"] = "estimated 50 percent"
        path = _write_doc(self.folder, doc)
        errors = validate_assessment(path)
        self.assertTrue(any("estimate" in e.lower() for e in errors))

    def test_missing_auto_gate_write_blocks(self):
        doc, _ = self._fresh()
        del doc["auto_gate_write"]
        path = _write_doc(self.folder, doc)
        errors = validate_assessment(path)
        self.assertTrue(any("auto_gate_write" in e for e in errors))

    def test_auto_gate_write_true_blocks(self):
        doc, _ = self._fresh()
        doc["auto_gate_write"] = True
        path = _write_doc(self.folder, doc)
        errors = validate_assessment(path)
        self.assertTrue(any("auto_gate_write" in e for e in errors))

    def test_stale_sign_off_blocks(self):
        doc, _ = self._fresh()
        doc["human_sign_off"]["date"] = "2020-01-01"
        path = _write_doc(self.folder, doc)
        errors = validate_assessment(path)
        self.assertTrue(any("stale" in e.lower() for e in errors))

    def test_missing_sign_off_blocks(self):
        doc, _ = self._fresh()
        del doc["human_sign_off"]
        path = _write_doc(self.folder, doc)
        errors = validate_assessment(path)
        self.assertTrue(any("human_sign_off" in e for e in errors))

    def test_missing_preservation_proof_blocks(self):
        doc, _ = self._fresh()
        doc["proposals"][0]["preservation_proof"] = ""
        path = _write_doc(self.folder, doc)
        errors = validate_assessment(path)
        self.assertTrue(any("preservation_proof" in e for e in errors))

    def test_delta_mismatch_blocks(self):
        doc, _ = self._fresh()
        doc["proposals"][0]["delta_tokens"] = -1
        path = _write_doc(self.folder, doc)
        errors = validate_assessment(path)
        self.assertTrue(any("delta_tokens" in e for e in errors))

    def test_different_tree_hash_blocks(self):
        doc, _ = self._fresh()
        doc["measurements"]["run-after"]["tree_hash"] = "different"
        path = _write_doc(self.folder, doc)
        errors = validate_assessment(path)
        self.assertTrue(any("tree_hash" in e and "REQ-02" in e for e in errors))

    def test_concurrent_write_blocks(self):
        _, path = self._fresh()

        class _Stat(object):
            def __init__(self, size, mtime_ns):
                self.st_size = size
                self.st_mtime_ns = mtime_ns

        stats = [_Stat(10, 1), _Stat(20, 2)]
        calls = {"n": 0}
        real_fstat = os.fstat

        def fake_fstat(fd):
            index = calls["n"]
            calls["n"] = index + 1
            if index < len(stats):
                return stats[index]
            return real_fstat(fd)

        with mock.patch.object(module_under_test.os, "fstat", side_effect=fake_fstat):
            errors = validate_assessment(path)
        self.assertTrue(any("concurrent" in e.lower() for e in errors))

    def test_nan_in_json_blocks(self):
        path = os.path.join(self.folder, "nan.md")
        with open(path, "w", encoding="utf-8") as fh:
            fh.write("# Assessment\n\n```json\nNaN\n```\n")
        errors = validate_assessment(path)
        self.assertTrue(errors)
        self.assertTrue(any("NaN" in e for e in errors))

    def test_hostile_inputs_are_refused(self):
        bad_inputs = [
            None,
            123,
            b"path",
            "",
            "   ",
            "/no/such/file.md",
            self.folder,
            [],
            {},
            float("nan"),
        ]
        for bad in bad_inputs:
            errors = validate_assessment(bad)
            self.assertIsInstance(errors, list)
            self.assertTrue(len(errors) > 0, "expected a refusal for %r" % (bad,))

    def test_main_returns_2_on_bad_doc(self):
        doc, _ = self._fresh()
        del doc["human_sign_off"]
        path = _write_doc(self.folder, doc)
        buf = io.StringIO()
        with redirect_stderr(buf):
            rc = main([path])
        self.assertEqual(rc, 2)
        self.assertIn("NO-DATA", buf.getvalue())

    def test_main_refuses_hostile_argv(self):
        buf = io.StringIO()
        with redirect_stderr(buf):
            rc = main([None])
        self.assertEqual(rc, 2)


if __name__ == "__main__":
    unittest.main()

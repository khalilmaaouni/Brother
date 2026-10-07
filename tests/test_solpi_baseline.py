"""Tests for the L4.1 SoL-Pi baseline lock.

Module under test: scripts/load_solpi_baseline.py. Every test that can run
without the shipped repository document builds its own fixture in a temp
folder, so this suite also runs on the public export tree.
"""

import json
import os
import sys
import tempfile
import unittest
from dataclasses import FrozenInstanceError

_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _REPO_ROOT not in sys.path:
    sys.path.insert(0, _REPO_ROOT)

import scripts.load_solpi_baseline as baseline_module
from scripts.load_solpi_baseline import (
    CorruptLogError,
    MechanismBaseline,
    NoDataError,
    load_solpi_baseline,
    sha256_file,
)

MECHANISMS = (
    "action_fusion",
    "online_context_compact",
    "observation_pack",
    "evidence_preserving_reducer",
)

QUOTE = "44.7-49.0% and API cost by about one third"
PIN = "6f4c8a1d9b2e37c05a7f8e1d3c6b9a0f4e7d2c5b8a1f3e6d9c0b7a4f2e5d8c1b"
EXCERPT_NAME = "excerpt.md"
EXCERPT_TEXT = "Fetched abstract line. " + QUOTE + ", and the cost fell by a third.\n"

SHIPPED_BASELINE = os.path.join(_REPO_ROOT, "scripts", "solpi_baseline.json")
SHIPPED_EXCERPT = os.path.join(
    _REPO_ROOT, "docs", "architecture", "HARNESS-EFFICIENCY-SOLPI-ASSESSMENT.md"
)


def _record(mechanism_id):
    return {
        "mechanism_id": mechanism_id,
        "paper_ref": "arXiv:2609.20519 (SoL-Pi)",
        "metric": "recorded token traffic reduction, paper aggregate over the four mechanisms",
        "value": 44.7,
        "unit": "percent",
        "source_quote": QUOTE,
        "source_path": "https://arxiv.org/abs/2609.20519",
        "source_sha256": PIN,
        "source_version": "arXiv:2609.20519 abstract page, fetched 2026-09-20",
        "retrieved_at": "2026-09-20",
        "excerpt_path": EXCERPT_NAME,
    }


def _document(mechanism_ids=MECHANISMS):
    return {mechanism_id: _record(mechanism_id) for mechanism_id in mechanism_ids}


class BaselineFixtureTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.directory = self.tmp.name
        self.excerpt_path = os.path.join(self.directory, EXCERPT_NAME)
        with open(self.excerpt_path, "w", encoding="utf-8") as handle:
            handle.write(EXCERPT_TEXT)

    def write_baseline(self, document, name="solpi_baseline.json"):
        path = os.path.join(self.directory, name)
        with open(path, "w", encoding="utf-8") as handle:
            json.dump(document, handle, indent=2, sort_keys=True)
        return path

    def test_all_four_mechanisms_load_as_frozen_records(self):
        loaded = load_solpi_baseline(self.write_baseline(_document()))
        self.assertEqual(sorted(loaded), sorted(MECHANISMS))
        for mechanism_id in MECHANISMS:
            record = loaded[mechanism_id]
            self.assertIsInstance(record, MechanismBaseline)
            self.assertEqual(record.mechanism_id, mechanism_id)
            self.assertEqual(record.value, 44.7)
            self.assertEqual(record.unit, "percent")
            self.assertRegex(record.source_sha256, r"\A[0-9a-f]{64}\Z")
            self.assertTrue(record.source_quote.strip())
        with self.assertRaises(FrozenInstanceError):
            loaded["action_fusion"].value = 0.0

    def test_missing_mechanism_blocks(self):
        path = self.write_baseline(_document(MECHANISMS[:-1]))
        with self.assertRaises(NoDataError):
            load_solpi_baseline(path)

    def test_empty_object_blocks(self):
        with self.assertRaises(NoDataError):
            load_solpi_baseline(self.write_baseline({}))

    def test_empty_file_blocks(self):
        path = os.path.join(self.directory, "empty.json")
        with open(path, "wb"):
            pass
        with self.assertRaises(NoDataError):
            load_solpi_baseline(path)

    def test_corrupt_json_raises_nodata(self):
        path = os.path.join(self.directory, "corrupt.json")
        with open(path, "w", encoding="utf-8") as handle:
            handle.write("{ this is not json")
        with self.assertRaises(NoDataError):
            load_solpi_baseline(path)

    def test_non_utf8_bytes_block(self):
        path = os.path.join(self.directory, "bytes.json")
        with open(path, "wb") as handle:
            handle.write(b"\xff\xfe\x00{}")
        with self.assertRaises(NoDataError):
            load_solpi_baseline(path)

    def test_stale_or_corrupt_pin_blocks(self):
        document = _document()
        document["observation_pack"]["source_sha256"] = PIN.upper()
        with self.assertRaises(NoDataError):
            load_solpi_baseline(self.write_baseline(document))

    def test_short_pin_blocks(self):
        document = _document()
        document["observation_pack"]["source_sha256"] = PIN[:-1]
        with self.assertRaises(NoDataError):
            load_solpi_baseline(self.write_baseline(document))

    def test_non_finite_or_wrong_typed_value_blocks(self):
        for bad in (float("nan"), float("inf"), 10 ** 400, True, "44.7", None):
            document = _document()
            document["observation_pack"]["value"] = bad
            with self.assertRaises(NoDataError):
                load_solpi_baseline(self.write_baseline(document, "value.json"))

    def test_missing_excerpt_file_blocks(self):
        os.remove(self.excerpt_path)
        with self.assertRaises(NoDataError):
            load_solpi_baseline(self.write_baseline(_document()))

    def test_empty_excerpt_blocks(self):
        with open(self.excerpt_path, "wb"):
            pass
        with self.assertRaises(NoDataError):
            load_solpi_baseline(self.write_baseline(_document()))

    def test_quote_absent_from_excerpt_blocks(self):
        with open(self.excerpt_path, "w", encoding="utf-8") as handle:
            handle.write("a summary that no longer carries the quoted passage\n")
        with self.assertRaises(NoDataError):
            load_solpi_baseline(self.write_baseline(_document()))

    def test_bad_retrieval_date_blocks(self):
        document = _document()
        document["action_fusion"]["retrieved_at"] = "20-09-2026"
        with self.assertRaises(NoDataError):
            load_solpi_baseline(self.write_baseline(document))

    def test_non_string_field_blocks(self):
        document = _document()
        document["action_fusion"]["source_quote"] = ["not", "a", "string"]
        with self.assertRaises(NoDataError):
            load_solpi_baseline(self.write_baseline(document))

    def test_wrong_mechanism_id_blocks(self):
        document = _document()
        document["online_context_compact"]["mechanism_id"] = "action_fusion"
        with self.assertRaises(NoDataError):
            load_solpi_baseline(self.write_baseline(document))

    def test_concurrent_write_blocks(self):
        path = self.write_baseline(_document())
        real = baseline_module._stat_snapshot
        calls = []

        def fake(path_argument):
            calls.append(path_argument)
            return (1, 1) if len(calls) == 1 else (2, 2)

        baseline_module._stat_snapshot = fake
        try:
            with self.assertRaises(NoDataError):
                load_solpi_baseline(path)
        finally:
            baseline_module._stat_snapshot = real

    def test_hostile_path_arguments_are_refused(self):
        for bad in (None, 0, -1, True, 3.5, float("nan"), "", "   ", b"x", [], {}):
            with self.assertRaises(NoDataError):
                load_solpi_baseline(bad)
            with self.assertRaises(NoDataError):
                sha256_file(bad)

    def test_directory_and_missing_paths_are_refused(self):
        with self.assertRaises(NoDataError):
            load_solpi_baseline(self.directory)
        with self.assertRaises(NoDataError):
            sha256_file(self.directory)
        missing = os.path.join(self.directory, "not-there.json")
        with self.assertRaises(NoDataError):
            load_solpi_baseline(missing)
        with self.assertRaises(NoDataError):
            sha256_file(missing)

    def test_sha256_file_hashes_the_file_bytes(self):
        path = os.path.join(self.directory, "payload.bin")
        with open(path, "wb") as handle:
            handle.write(b"abc")
        self.assertEqual(
            sha256_file(path),
            "ba7816bf8f01cfea414140de5dae2223b00361a396177a9cb410ff61f20015ad",
        )

    def test_module_reexports_the_shared_gate_order_errors(self):
        import scripts.gate_order as gate_order

        self.assertIs(NoDataError, gate_order.NoDataError)
        self.assertIs(CorruptLogError, gate_order.CorruptLogError)


class ShippedBaselineTests(unittest.TestCase):
    @unittest.skipUnless(
        os.path.isfile(SHIPPED_BASELINE),
        "the shipped baseline json is not present in this export",
    )
    def test_shipped_baseline_locks_exactly_four_mechanisms(self):
        with open(SHIPPED_BASELINE, "rb") as handle:
            document = json.loads(handle.read().decode("utf-8"))
        self.assertEqual(sorted(document), sorted(MECHANISMS))

    @unittest.skipUnless(
        os.path.isfile(SHIPPED_BASELINE),
        "the shipped baseline json is not present in this export",
    )
    def test_shipped_baseline_records_carry_pins_and_quotes(self):
        with open(SHIPPED_BASELINE, "rb") as handle:
            document = json.loads(handle.read().decode("utf-8"))
        for mechanism_id in MECHANISMS:
            record = document[mechanism_id]
            self.assertRegex(record["source_sha256"], r"\A[0-9a-f]{64}\Z")
            self.assertTrue(record["source_quote"].strip())

    @unittest.skipUnless(
        os.path.isfile(SHIPPED_BASELINE) and os.path.isfile(SHIPPED_EXCERPT),
        "the shipped baseline or its archived excerpt is not present in this export",
    )
    def test_shipped_baseline_loads_and_its_excerpts_exist(self):
        loaded = load_solpi_baseline(SHIPPED_BASELINE)
        self.assertEqual(sorted(loaded), sorted(MECHANISMS))
        base = os.path.dirname(os.path.abspath(SHIPPED_BASELINE))
        for record in loaded.values():
            self.assertRegex(record.source_sha256, r"\A[0-9a-f]{64}\Z")
            self.assertTrue(record.source_quote.strip())
            resolved = os.path.normpath(os.path.join(base, record.excerpt_path))
            self.assertTrue(os.path.isfile(resolved))


if __name__ == "__main__":
    unittest.main()

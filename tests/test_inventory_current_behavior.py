"""Tests for the L4.3 current behavior inventory.

Module under test: scripts/inventory_current_behavior.py. Every test builds its
own fixture harness under a temp folder, so the suite also runs on the public
export tree. One test writes the unit's named artifact, current.json, into the
platform temp directory and asserts it carries at least four records.
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

import scripts.inventory_current_behavior as inventory_module
from scripts.inventory_current_behavior import (
    ABSENT,
    BEHAVIOR_CATALOG,
    MIN_RECORDS,
    PRESENT,
    SCHEMA_VERSION,
    CurrentBehavior,
    NoDataError,
    inventory_current_behavior,
    main,
    presence,
    read_inventory,
    validate_measurement_run_ids,
    write_inventory,
)

FIELDS = (
    "behavior_id",
    "component",
    "file_path",
    "function_name",
    "observed_metric",
    "before_value",
    "before_unit",
    "measurement_run_id",
)


def _record_document(records):
    return {
        "schema_version": SCHEMA_VERSION,
        "records": [
            {
                "behavior_id": record.behavior_id,
                "component": record.component,
                "file_path": record.file_path,
                "function_name": record.function_name,
                "observed_metric": record.observed_metric,
                "before_value": record.before_value,
                "before_unit": record.before_unit,
                "measurement_run_id": record.measurement_run_id,
            }
            for record in records
        ],
    }


class TempHarnessTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.directory = self.tmp.name

    def build_harness(self, present=True):
        root = os.path.join(self.directory, "harness")
        os.makedirs(root, exist_ok=True)
        expected = {}
        for entry in BEHAVIOR_CATALOG:
            expected[entry["behavior_id"]] = 2.0 if present else 0.0
            if not present:
                continue
            path = os.path.join(root, entry["file_path"])
            parent = os.path.dirname(path)
            if parent:
                os.makedirs(parent, exist_ok=True)
            with open(path, "w", encoding="utf-8") as handle:
                handle.write(entry["function_name"] + "\n")
                handle.write("an unrelated line\n")
                handle.write(entry["function_name"] + "\n")
        return root, expected


class InventoryShapeTests(TempHarnessTest):
    def test_inventory_returns_at_least_four_records_with_the_pinned_fields(self):
        root, expected = self.build_harness()
        records = inventory_current_behavior(root)
        self.assertGreaterEqual(len(records), MIN_RECORDS)
        self.assertEqual(len(records), len(BEHAVIOR_CATALOG))
        for record in records:
            self.assertIsInstance(record, CurrentBehavior)
            for field in FIELDS:
                self.assertTrue(hasattr(record, field), field)
            self.assertTrue(record.behavior_id)
            self.assertTrue(record.component)
            self.assertTrue(record.file_path)
            self.assertTrue(record.function_name)
            self.assertTrue(record.observed_metric)
            self.assertEqual(record.before_unit, "count")
            self.assertEqual(record.before_value, expected[record.behavior_id])

    def test_records_are_frozen(self):
        root, _ = self.build_harness()
        records = inventory_current_behavior(root)
        with self.assertRaises(FrozenInstanceError):
            records[0].before_value = 0.0

    def test_absent_paths_are_recorded_absent_and_not_asserted_to_exist(self):
        root, _ = self.build_harness(present=False)
        records = inventory_current_behavior(root)
        self.assertGreaterEqual(len(records), MIN_RECORDS)
        for record in records:
            self.assertTrue(record.file_path)
            self.assertEqual(record.before_value, 0.0)
            self.assertEqual(presence(root, record.file_path), ABSENT)
            self.assertFalse(os.path.exists(os.path.join(root, record.file_path)))

    def test_present_paths_report_present(self):
        root, _ = self.build_harness()
        for record in inventory_current_behavior(root):
            self.assertEqual(presence(root, record.file_path), PRESENT)

    def test_run_ids_are_stable_and_change_with_the_source(self):
        root, _ = self.build_harness()
        before = [r.measurement_run_id for r in inventory_current_behavior(root)]
        again = [r.measurement_run_id for r in inventory_current_behavior(root)]
        self.assertEqual(before, again)
        self.assertTrue(all(value.startswith("run-") for value in before))
        entry = BEHAVIOR_CATALOG[0]
        path = os.path.join(root, entry["file_path"])
        with open(path, "a", encoding="utf-8") as handle:
            handle.write(entry["function_name"] + "\n")
        after = [r.measurement_run_id for r in inventory_current_behavior(root)]
        self.assertNotEqual(before, after)


class EmptyAndHostileTests(TempHarnessTest):
    def test_empty_inventory_blocks(self):
        root, _ = self.build_harness()
        real = inventory_module.BEHAVIOR_CATALOG
        inventory_module.BEHAVIOR_CATALOG = ()
        try:
            with self.assertRaises(NoDataError):
                inventory_current_behavior(root)
        finally:
            inventory_module.BEHAVIOR_CATALOG = real

    def test_inventory_below_the_minimum_blocks(self):
        root, _ = self.build_harness()
        real = inventory_module.BEHAVIOR_CATALOG
        inventory_module.BEHAVIOR_CATALOG = tuple(real[: MIN_RECORDS - 1])
        try:
            with self.assertRaises(NoDataError):
                inventory_current_behavior(root)
        finally:
            inventory_module.BEHAVIOR_CATALOG = real

    def test_hostile_harness_root_is_refused(self):
        for bad in (
            None,
            0,
            -1,
            True,
            False,
            3.5,
            float("nan"),
            "",
            "   ",
            b"x",
            [],
            {},
            (),
        ):
            with self.assertRaises(NoDataError):
                inventory_current_behavior(bad)

    def test_missing_and_non_directory_roots_are_refused(self):
        with self.assertRaises(NoDataError):
            inventory_current_behavior(os.path.join(self.directory, "nope"))
        file_path = os.path.join(self.directory, "a-file")
        with open(file_path, "w", encoding="utf-8") as handle:
            handle.write("x\n")
        with self.assertRaises(NoDataError):
            inventory_current_behavior(file_path)

    def test_hostile_presence_arguments_are_refused(self):
        root, _ = self.build_harness()
        for bad in (None, 0, True, "", "   ", b"x", [], {}):
            with self.assertRaises(NoDataError):
                presence(root, bad)
        with self.assertRaises(NoDataError):
            presence(None, "scripts/required_fast.sh")

    def test_corrupt_source_file_blocks(self):
        root, _ = self.build_harness()
        entry = BEHAVIOR_CATALOG[0]
        path = os.path.join(root, entry["file_path"])
        with open(path, "wb") as handle:
            handle.write(b"\xff\xfe\x00 not utf-8\n")
        with self.assertRaises(NoDataError):
            inventory_current_behavior(root)


class WriteAndReadTests(TempHarnessTest):
    def test_write_then_read_round_trip(self):
        root, _ = self.build_harness()
        records = inventory_current_behavior(root)
        out = os.path.join(self.directory, "current.json")
        write_inventory(records, out)
        loaded = read_inventory(out)
        self.assertEqual(
            [record.behavior_id for record in loaded],
            [record.behavior_id for record in records],
        )
        with open(out, "rb") as handle:
            document = json.loads(handle.read().decode("utf-8"))
        self.assertEqual(document["schema_version"], SCHEMA_VERSION)
        self.assertEqual(len(document["records"]), len(records))

    def test_write_refuses_hostile_records(self):
        out = os.path.join(self.directory, "current.json")
        for bad in (None, 0, True, "", b"x", {}, [], ()):
            with self.assertRaises(NoDataError):
                write_inventory(bad, out)
        with self.assertRaises(NoDataError):
            write_inventory([{"behavior_id": "x"}], out)

    def test_write_refuses_records_below_the_minimum(self):
        root, _ = self.build_harness()
        records = inventory_current_behavior(root)
        out = os.path.join(self.directory, "current.json")
        with self.assertRaises(NoDataError):
            write_inventory(list(records[: MIN_RECORDS - 1]), out)

    def test_write_refuses_hostile_out_path(self):
        root, _ = self.build_harness()
        records = inventory_current_behavior(root)
        for bad in (None, 0, True, "", "   ", b"x", [], {}):
            with self.assertRaises(NoDataError):
                write_inventory(records, bad)
        with self.assertRaises(NoDataError):
            write_inventory(records, self.directory)

    def test_concurrent_write_to_the_destination_blocks(self):
        root, _ = self.build_harness()
        records = inventory_current_behavior(root)
        out = os.path.join(self.directory, "current.json")
        real = inventory_module._destination_snapshot
        calls = []

        def fake(path):
            calls.append(path)
            return (1, 1) if len(calls) == 1 else (2, 2)

        inventory_module._destination_snapshot = fake
        try:
            with self.assertRaises(NoDataError):
                write_inventory(records, out)
        finally:
            inventory_module._destination_snapshot = real

    def test_read_refuses_missing_directory_bytes_corrupt_and_short(self):
        out = os.path.join(self.directory, "current.json")
        with self.assertRaises(NoDataError):
            read_inventory(out)
        with self.assertRaises(NoDataError):
            read_inventory(self.directory)
        bytes_path = os.path.join(self.directory, "bytes.json")
        with open(bytes_path, "wb") as handle:
            handle.write(b"\xff\xfe\x00{}")
        with self.assertRaises(NoDataError):
            read_inventory(bytes_path)
        corrupt = os.path.join(self.directory, "corrupt.json")
        with open(corrupt, "w", encoding="utf-8") as handle:
            handle.write("{ not json")
        with self.assertRaises(inventory_module.CorruptLogError):
            read_inventory(corrupt)
        array = os.path.join(self.directory, "array.json")
        with open(array, "w", encoding="utf-8") as handle:
            json.dump([1, 2, 3], handle)
        with self.assertRaises(inventory_module.CorruptLogError):
            read_inventory(array)
        wrong = os.path.join(self.directory, "wrong.json")
        with open(wrong, "w", encoding="utf-8") as handle:
            json.dump({"schema_version": "other", "records": []}, handle)
        with self.assertRaises(inventory_module.CorruptLogError):
            read_inventory(wrong)

    def test_read_refuses_too_few_records(self):
        root, _ = self.build_harness()
        records = inventory_current_behavior(root)
        short = _record_document(list(records[: MIN_RECORDS - 1]))
        out = os.path.join(self.directory, "short.json")
        with open(out, "w", encoding="utf-8") as handle:
            json.dump(short, handle)
        with self.assertRaises(NoDataError):
            read_inventory(out)

    def test_read_refuses_non_finite_before_value(self):
        root, _ = self.build_harness()
        records = inventory_current_behavior(root)
        document = _record_document(records)
        document["records"][0]["before_value"] = float("nan")
        out = os.path.join(self.directory, "nan.json")
        with open(out, "w", encoding="utf-8") as handle:
            json.dump(document, handle)
        with self.assertRaises(inventory_module.CorruptLogError):
            read_inventory(out)


class RunIdValidationTests(TempHarnessTest):
    def test_stale_run_id_blocks(self):
        root, _ = self.build_harness()
        records = inventory_current_behavior(root)
        with self.assertRaises(NoDataError):
            validate_measurement_run_ids(records, {"some-other-run": {}})

    def test_matching_run_ids_pass(self):
        root, _ = self.build_harness()
        records = inventory_current_behavior(root)
        known = {record.measurement_run_id for record in records}
        validate_measurement_run_ids(records, known)
        validate_measurement_run_ids(records, {value: {} for value in known})
        validate_measurement_run_ids(
            records, [{"run_id": value} for value in known]
        )

    def test_empty_or_hostile_measurements_block(self):
        root, _ = self.build_harness()
        records = inventory_current_behavior(root)
        for bad in (None, 0, True, "", b"x", {}, [], (), [None], [{"nope": 1}]):
            with self.assertRaises(NoDataError):
                validate_measurement_run_ids(records, bad)

    def test_hostile_records_block(self):
        known = {"run-x": {}}
        for bad in (None, 0, True, "", b"x", {}, [], ()):
            with self.assertRaises(NoDataError):
                validate_measurement_run_ids(bad, known)
        with self.assertRaises(NoDataError):
            validate_measurement_run_ids([{"behavior_id": "x"}], known)


class MainTests(TempHarnessTest):
    def test_main_writes_the_named_artifact_with_at_least_four_records(self):
        root, _ = self.build_harness()
        out = os.path.join(tempfile.gettempdir(), "current.json")
        self.assertEqual(main([root, out]), 0)
        with open(out, "rb") as handle:
            document = json.loads(handle.read().decode("utf-8"))
        self.assertGreaterEqual(len(document["records"]), MIN_RECORDS)
        for record in document["records"]:
            self.assertTrue(record["file_path"])
            self.assertTrue(record["function_name"])
            self.assertTrue(record["measurement_run_id"])

    def test_main_is_idempotent_and_reloadable(self):
        root, _ = self.build_harness()
        out = os.path.join(self.directory, "current.json")
        self.assertEqual(main([root, out]), 0)
        self.assertEqual(main([root, out]), 0)
        self.assertEqual(len(read_inventory(out)), len(BEHAVIOR_CATALOG))

    def test_main_returns_two_on_a_missing_root(self):
        out = os.path.join(self.directory, "current.json")
        self.assertEqual(main([os.path.join(self.directory, "nope"), out]), 2)
        self.assertFalse(os.path.exists(out))

    def test_main_hostile_argv_returns_two(self):
        for bad in (
            0,
            True,
            -1,
            float("nan"),
            "",
            "x",
            b"x",
            {},
            [],
            (1, 2, 3),
            [None],
            ["a", 5],
            [b"a", b"b"],
            ["only-one"],
        ):
            try:
                result = main(bad)
            except BaseException as exc:
                self.fail("main(%r) raised %r" % (bad, exc))
            self.assertEqual(result, 2, "main(%r) returned %r" % (bad, result))


if __name__ == "__main__":
    unittest.main()

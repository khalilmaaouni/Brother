"""Tests for scripts/measure_session_tokens.py (unit L4.2).

Every test that can run without the shipped fixture builds its own fixture in
a temp folder, so this suite also runs on the public export tree. The shipped
fixture under tests/fixtures is only read through a skipUnless path.
"""

import hashlib
import json
import os
import sys
import tempfile
import unittest
from dataclasses import FrozenInstanceError

_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _REPO_ROOT not in sys.path:
    sys.path.insert(0, _REPO_ROOT)

import scripts.measure_session_tokens as module_under_test
from scripts.measure_session_tokens import (
    CorruptLogError,
    NoDataError,
    TokenMeasurement,
    main,
    parse_token_log,
    sha256_file,
    write_measurement,
)

_TEXT_FIELDS = (
    "run_id",
    "session_id",
    "started_at",
    "ended_at",
    "model",
    "counter_name",
    "counter_version",
    "tree_hash",
    "task_path",
    "task_sha256",
    "command",
)

_INT_FIELDS = (
    "input_tokens",
    "output_tokens",
    "cache_read_tokens",
    "cache_write_tokens",
    "total_tokens",
    "exit_code",
)

FIXTURE = os.path.join(
    _REPO_ROOT, "tests", "fixtures", "real_session_token_log.jsonl"
)


def _record(**overrides):
    base = {
        "run_id": "r-1",
        "session_id": "s-1",
        "started_at": "2026-09-20T10:00:00Z",
        "ended_at": "2026-09-20T10:15:00Z",
        "model": "gpt-5.6-sol",
        "counter_name": "session-token-counter",
        "counter_version": "1.0.0",
        "tree_hash": "a" * 64,
        "task_path": "docs/plan/BROTHER-1.1.0-LAUNCH-WBS.json",
        "task_sha256": "b" * 64,
        "input_tokens": 12000,
        "output_tokens": 3400,
        "cache_read_tokens": 5000,
        "cache_write_tokens": 1200,
        "total_tokens": 21600,
        "command": "python3 -B -m unittest tests.test_measure_session_tokens -v",
        "exit_code": 0,
    }
    base.update(overrides)
    return base


class TempDirTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.directory = self.tmp.name

    def write_log(self, body, name="token.jsonl"):
        path = os.path.join(self.directory, name)
        with open(path, "w", encoding="utf-8") as handle:
            if isinstance(body, dict):
                handle.write(json.dumps(body, sort_keys=True) + "\n")
            else:
                handle.write(body)
        return path


class ParseTokenLogTests(TempDirTest):
    def test_parses_a_well_formed_measurement(self):
        path = self.write_log(_record())
        measurement = parse_token_log(path)
        self.assertIsInstance(measurement, TokenMeasurement)
        self.assertEqual(measurement.run_id, "r-1")
        self.assertEqual(measurement.session_id, "s-1")
        self.assertEqual(measurement.model, "gpt-5.6-sol")
        self.assertEqual(measurement.input_tokens, 12000)
        self.assertEqual(measurement.output_tokens, 3400)
        self.assertEqual(measurement.cache_read_tokens, 5000)
        self.assertEqual(measurement.cache_write_tokens, 1200)
        self.assertEqual(measurement.total_tokens, 21600)
        self.assertEqual(
            measurement.total_tokens,
            measurement.input_tokens
            + measurement.output_tokens
            + measurement.cache_read_tokens
            + measurement.cache_write_tokens,
        )
        self.assertEqual(measurement.raw_log_path, path)
        with open(path, "rb") as handle:
            expected = hashlib.sha256(handle.read()).hexdigest()
        self.assertEqual(measurement.raw_log_sha256, expected)
        self.assertEqual(measurement.exit_code, 0)

    def test_measurement_is_frozen_and_keeps_all_fields(self):
        measurement = parse_token_log(self.write_log(_record()))
        with self.assertRaises(FrozenInstanceError):
            measurement.total_tokens = 0
        for field in _TEXT_FIELDS + _INT_FIELDS + ("raw_log_path", "raw_log_sha256"):
            self.assertTrue(hasattr(measurement, field), field)

    def test_total_not_equal_to_sum_blocks(self):
        with self.assertRaises(NoDataError):
            parse_token_log(self.write_log(_record(total_tokens=21601)))
        with self.assertRaises(NoDataError):
            parse_token_log(self.write_log(_record(total_tokens=21599)))

    def test_empty_log_file_blocks(self):
        path = os.path.join(self.directory, "empty.jsonl")
        with open(path, "wb"):
            pass
        with self.assertRaises(NoDataError):
            parse_token_log(path)

    def test_blank_whitespace_log_blocks(self):
        path = self.write_log("   \n\n   \n")
        with self.assertRaises(NoDataError):
            parse_token_log(path)

    def test_missing_file_blocks(self):
        with self.assertRaises(NoDataError):
            parse_token_log(os.path.join(self.directory, "not-there.jsonl"))

    def test_directory_instead_of_file_blocks(self):
        with self.assertRaises(NoDataError):
            parse_token_log(self.directory)

    def test_corrupt_json_raises_corrupt_log_error(self):
        with self.assertRaises(CorruptLogError):
            parse_token_log(self.write_log("{ this is not json\n"))

    def test_json_array_record_raises_corrupt_log_error(self):
        with self.assertRaises(CorruptLogError):
            parse_token_log(self.write_log("[1, 2, 3]\n"))

    def test_two_records_raise_corrupt_log_error(self):
        path = self.write_log(
            json.dumps(_record(), sort_keys=True)
            + "\n"
            + json.dumps(_record(), sort_keys=True)
            + "\n"
        )
        with self.assertRaises(CorruptLogError):
            parse_token_log(path)

    def test_non_utf8_bytes_block(self):
        path = os.path.join(self.directory, "bytes.jsonl")
        with open(path, "wb") as handle:
            handle.write(b"\xff\xfe\x00{}")
        with self.assertRaises(NoDataError):
            parse_token_log(path)

    def test_concurrent_append_blocks(self):
        path = self.write_log(_record())
        real = module_under_test._stat_snapshot
        calls = []

        def fake(_path):
            calls.append(_path)
            return (1, 1) if len(calls) == 1 else (2, 2)

        module_under_test._stat_snapshot = fake
        try:
            with self.assertRaises(NoDataError):
                parse_token_log(path)
        finally:
            module_under_test._stat_snapshot = real

    def test_hostile_path_arguments_are_refused(self):
        for bad in (None, 0, -1, True, 3.5, float("nan"), "", "   ", b"x", [], {}):
            with self.assertRaises(NoDataError):
                parse_token_log(bad)
            with self.assertRaises(NoDataError):
                sha256_file(bad)

    def test_missing_field_blocks(self):
        for field in _TEXT_FIELDS + _INT_FIELDS:
            record = _record()
            del record[field]
            with self.assertRaises(NoDataError):
                parse_token_log(self.write_log(record, name="missing.jsonl"))

    def test_text_fields_must_be_non_empty_strings(self):
        for field in _TEXT_FIELDS:
            for bad in ("", "   ", None, 5, True, 3.5, [], {}):
                record = _record(**{field: bad})
                with self.assertRaises(NoDataError):
                    parse_token_log(self.write_log(record, name="text.jsonl"))

    def test_int_fields_must_be_ints(self):
        for field in _INT_FIELDS:
            for bad in ("10", None, True, False, 1.5, [], {}, float("nan")):
                record = _record(**{field: bad})
                with self.assertRaises(NoDataError):
                    parse_token_log(self.write_log(record, name="int.jsonl"))

    def test_negative_token_counts_block(self):
        for field in (
            "input_tokens",
            "output_tokens",
            "cache_read_tokens",
            "cache_write_tokens",
        ):
            record = _record(**{field: -1})
            with self.assertRaises(NoDataError):
                parse_token_log(self.write_log(record, name="neg.jsonl"))

    def test_nan_and_infinity_in_log_block(self):
        for literal in ("NaN", "Infinity", "-Infinity"):
            body = (
                '{"run_id": "r", "session_id": "s", "started_at": "x", '
                '"ended_at": "y", "model": "m", "counter_name": "c", '
                '"counter_version": "v", "tree_hash": "h", '
                '"task_path": "p", "task_sha256": "t", '
                '"input_tokens": 1, "output_tokens": 1, '
                '"cache_read_tokens": 1, "cache_write_tokens": 1, '
                '"total_tokens": %s, "command": "cmd", "exit_code": 0}'
            ) % literal
            with self.assertRaises(NoDataError):
                parse_token_log(self.write_log(body, name="nan.jsonl"))

    def test_sha256_file_hashes_the_bytes(self):
        path = os.path.join(self.directory, "payload.bin")
        with open(path, "wb") as handle:
            handle.write(b"abc")
        self.assertEqual(
            sha256_file(path),
            "ba7816bf8f01cfea414140de5dae2223b00361a396177a9cb410ff61f20015ad",
        )
        with self.assertRaises(NoDataError):
            sha256_file(self.directory)


class WriteMeasurementTests(TempDirTest):
    def _measurement(self):
        return parse_token_log(self.write_log(_record()))

    def test_writes_sorted_key_json(self):
        measurement = self._measurement()
        out = os.path.join(self.directory, "out.json")
        write_measurement(measurement, out)
        with open(out, "rb") as handle:
            raw = handle.read()
        text = raw.decode("utf-8")
        data = json.loads(text)
        self.assertEqual(data["run_id"], "r-1")
        self.assertEqual(data["total_tokens"], 21600)
        self.assertEqual(data["raw_log_sha256"], measurement.raw_log_sha256)
        self.assertTrue(text.endswith("\n"))

    def test_wrong_measurement_type_is_refused(self):
        out = os.path.join(self.directory, "out.json")
        for bad in (None, 0, True, "x", {}, [], b"x"):
            with self.assertRaises(NoDataError):
                write_measurement(bad, out)

    def test_hostile_out_path_is_refused(self):
        measurement = self._measurement()
        for bad in (None, 0, True, "", "   ", b"x", [], {}):
            with self.assertRaises(NoDataError):
                write_measurement(measurement, bad)

    def test_directory_out_path_is_refused(self):
        measurement = self._measurement()
        with self.assertRaises(NoDataError):
            write_measurement(measurement, self.directory)


class MainTests(TempDirTest):
    def _log(self):
        return self.write_log(_record())

    def test_writes_measurement_and_exits_zero(self):
        log = self._log()
        out = os.path.join(self.directory, "out.json")
        self.assertEqual(main([log, out]), 0)
        self.assertTrue(os.path.isfile(out))
        with open(out, "rb") as handle:
            data = json.loads(handle.read().decode("utf-8"))
        self.assertEqual(data["total_tokens"], 21600)

    def test_already_done_exits_zero(self):
        log = self._log()
        out = os.path.join(self.directory, "out.json")
        self.assertEqual(main([log, out]), 0)
        self.assertEqual(main([log, out]), 0)

    def test_remeasures_when_log_changed(self):
        log = self._log()
        out = os.path.join(self.directory, "out.json")
        self.assertEqual(main([log, out]), 0)
        self.write_log(
            _record(input_tokens=13000, total_tokens=22600),
            name=os.path.basename(log),
        )
        self.assertEqual(main([log, out]), 0)
        with open(out, "rb") as handle:
            data = json.loads(handle.read().decode("utf-8"))
        self.assertEqual(data["input_tokens"], 13000)

    def test_missing_log_exits_two(self):
        out = os.path.join(self.directory, "out.json")
        self.assertEqual(
            main([os.path.join(self.directory, "missing.jsonl"), out]), 2
        )

    def test_corrupt_log_exits_two(self):
        log = self.write_log("{ not json\n")
        out = os.path.join(self.directory, "out.json")
        self.assertEqual(main([log, out]), 2)

    def test_bad_total_exits_two(self):
        log = self.write_log(_record(total_tokens=21601))
        out = os.path.join(self.directory, "out.json")
        self.assertEqual(main([log, out]), 2)

    def test_hostile_argv_is_refused_by_returning_two(self):
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


class ShippedFixtureTests(unittest.TestCase):
    @unittest.skipUnless(
        os.path.isfile(FIXTURE),
        "the shipped L4.2 fixture is not present in this export",
    )
    def test_shipped_fixture_total_matches_its_components(self):
        measurement = parse_token_log(FIXTURE)
        self.assertEqual(
            measurement.total_tokens,
            measurement.input_tokens
            + measurement.output_tokens
            + measurement.cache_read_tokens
            + measurement.cache_write_tokens,
        )

    @unittest.skipUnless(
        os.path.isfile(FIXTURE),
        "the shipped L4.2 fixture is not present in this export",
    )
    def test_shipped_fixture_raw_log_sha256_matches_its_bytes(self):
        measurement = parse_token_log(FIXTURE)
        with open(FIXTURE, "rb") as handle:
            expected = hashlib.sha256(handle.read()).hexdigest()
        self.assertEqual(measurement.raw_log_sha256, expected)


if __name__ == "__main__":
    unittest.main()

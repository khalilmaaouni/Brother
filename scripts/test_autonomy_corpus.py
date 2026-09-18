#!/usr/bin/env python3
"""Tests for scripts/autonomy_corpus.py. Plain unittest, runnable directly.

Every fixture is a tiny, clearly-synthetic dict written to a temp directory
inside setUp and removed in tearDown; nothing here reads or writes anything
under benchmarks/. The one exception is test_real_corpus_dir_is_no_data,
which points at the real (intentionally empty) benchmarks/autonomy_corpus/
workloads/ directory shipped with this change, to prove the shipped harness
reports NO-DATA over it rather than a pass.
"""
import json
import os
import shutil
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
REPO_ROOT = os.path.dirname(HERE)
sys.path.insert(0, HERE)
import autonomy_corpus as AC  # noqa: E402


def _two_unit_workload(workload_id="w1", failures=1):
    """The minimal valid shape: 2 independent units, N injected failures."""
    return {
        "workload_id": workload_id,
        "title": "a minimal workload",
        "units": [
            {"unit_id": "u1", "objective": "do the first thing"},
            {"unit_id": "u2", "objective": "do the second thing"},
        ],
        "injected_failures": [
            {"failure_id": "f%d" % i, "kind": "stale-cache",
             "target_unit": "u1", "description": "a planted fault"}
            for i in range(1, failures + 1)
        ],
    }


class AutonomyCorpusTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="autonomy_corpus_test_")

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _write(self, name, obj_or_text):
        path = os.path.join(self.tmp, name)
        with open(path, "w", encoding="utf-8") as fh:
            if isinstance(obj_or_text, str):
                fh.write(obj_or_text)
            else:
                json.dump(obj_or_text, fh)
        return path

    # ---- load_corpus: NO-DATA cases ----------------------------------

    def test_missing_corpus_dir_is_no_data(self):
        result, code = AC.load_corpus(os.path.join(self.tmp, "nope"))
        self.assertEqual(code, 3)
        self.assertTrue(result["nodata"])

    def test_corpus_dir_not_a_directory_is_no_data(self):
        path = self._write("not_a_dir.json", _two_unit_workload())
        result, code = AC.load_corpus(path)
        self.assertEqual(code, 3)
        self.assertTrue(result["nodata"])

    def test_empty_corpus_dir_is_no_data(self):
        result, code = AC.load_corpus(self.tmp)
        self.assertEqual(code, 3)
        self.assertTrue(result["nodata"])

    def test_corpus_dir_with_non_json_files_only_is_no_data(self):
        self._write("readme.md", "not json")
        result, code = AC.load_corpus(self.tmp)
        self.assertEqual(code, 3)
        self.assertTrue(result["nodata"])

    def test_wrong_type_corpus_dir_is_no_data_not_a_crash(self):
        result, code = AC.load_corpus(123)
        self.assertEqual(code, 2)
        self.assertTrue(result["nodata"])

    # ---- load_corpus: REFUSED cases ----------------------------------

    def test_malformed_json_is_refused(self):
        self._write("bad.json", "{not json")
        result, code = AC.load_corpus(self.tmp)
        self.assertEqual(code, 4)
        self.assertIn("does not parse as JSON", result["refused"])

    def test_top_level_not_object_is_refused(self):
        self._write("bad.json", [1, 2, 3])
        result, code = AC.load_corpus(self.tmp)
        self.assertEqual(code, 4)
        self.assertIn("not a JSON object", result["refused"])

    def test_fewer_than_two_units_is_refused(self):
        workload = _two_unit_workload()
        workload["units"] = workload["units"][:1]
        self._write("bad.json", workload)
        result, code = AC.load_corpus(self.tmp)
        self.assertEqual(code, 4)
        self.assertIn("fewer than 2 entries", result["refused"])

    def test_single_chain_fails_independence_rule(self):
        # A depends on nothing, B depends on A, C depends on B: only one
        # dependency-free unit, so this must be refused even though it has
        # 3 units and 1 injected failure.
        workload = {
            "workload_id": "chain",
            "title": "a serial chain, not independent units",
            "units": [
                {"unit_id": "a", "objective": "first"},
                {"unit_id": "b", "objective": "second", "depends_on": ["a"]},
                {"unit_id": "c", "objective": "third", "depends_on": ["b"]},
            ],
            "injected_failures": [
                {"failure_id": "f1", "kind": "wrong-branch",
                 "target_unit": "a", "description": "planted"},
            ],
        }
        self._write("chain.json", workload)
        result, code = AC.load_corpus(self.tmp)
        self.assertEqual(code, 4)
        self.assertIn("independence rule", result["refused"])

    def test_empty_injected_failures_is_refused(self):
        workload = _two_unit_workload(failures=0)
        workload["injected_failures"] = []
        self._write("bad.json", workload)
        result, code = AC.load_corpus(self.tmp)
        self.assertEqual(code, 4)
        self.assertIn("injected_failures", result["refused"])

    def test_target_unit_not_found_is_refused(self):
        workload = _two_unit_workload()
        workload["injected_failures"][0]["target_unit"] = "does-not-exist"
        self._write("bad.json", workload)
        result, code = AC.load_corpus(self.tmp)
        self.assertEqual(code, 4)
        self.assertIn("target_unit", result["refused"])

    def test_self_dependency_is_refused(self):
        workload = _two_unit_workload()
        workload["units"][0]["depends_on"] = ["u1"]
        self._write("bad.json", workload)
        result, code = AC.load_corpus(self.tmp)
        self.assertEqual(code, 4)
        self.assertIn("its own unit_id", result["refused"])

    def test_duplicate_workload_id_across_files_is_refused(self):
        self._write("a.json", _two_unit_workload(workload_id="dup"))
        self._write("b.json", _two_unit_workload(workload_id="dup"))
        result, code = AC.load_corpus(self.tmp)
        self.assertEqual(code, 4)
        self.assertIn("duplicates", result["refused"])

    def test_non_standard_json_constant_is_refused(self):
        # Infinity is accepted by Python's json.loads by default but is not
        # valid JSON; this module must refuse it at the parse step.
        self._write("bad.json", '{"workload_id": Infinity}')
        result, code = AC.load_corpus(self.tmp)
        self.assertEqual(code, 4)
        self.assertIn("does not parse as JSON", result["refused"])

    # ---- load_corpus: the real pass -----------------------------------

    def test_minimal_valid_workload_passes(self):
        self._write("good.json", _two_unit_workload())
        result, code = AC.load_corpus(self.tmp)
        self.assertEqual(code, 0)
        self.assertEqual(result["nodata"], "")
        self.assertEqual(len(result["workloads"]), 1)

    def test_absent_depends_on_key_counts_as_independent(self):
        # No depends_on key at all, on both units: independence rule reads
        # this the same as an empty list, per the schema's own wording that
        # depends_on need only be checked "if present".
        workload = _two_unit_workload()
        result, code = AC.load_corpus(self.tmp)  # sanity: empty dir first
        self.assertEqual(code, 3)
        self._write("good.json", workload)
        result, code = AC.load_corpus(self.tmp)
        self.assertEqual(code, 0)

    def test_multiple_workloads_and_totals(self):
        self._write("a.json", _two_unit_workload(workload_id="a", failures=1))
        self._write("b.json", _two_unit_workload(workload_id="b", failures=2))
        result, code = AC.load_corpus(self.tmp)
        self.assertEqual(code, 0)
        self.assertEqual(len(result["workloads"]), 2)
        line = AC.report_line(result, code)
        self.assertIn("2 workload(s)", line)
        self.assertIn("4 total unit(s)", line)
        self.assertIn("3 total injected failure(s)", line)

    # ---- report_line and main: shape and refusal on unknown input -----

    def test_report_line_no_data(self):
        line = AC.report_line({"nodata": "why"}, 3)
        self.assertEqual(line, "autonomy corpus: NO-DATA, why")

    def test_report_line_refused(self):
        line = AC.report_line({"refused": "why"}, 4)
        self.assertEqual(line, "autonomy corpus: REFUSED, why")

    def test_report_line_unknown_exit_code_raises(self):
        with self.assertRaises(ValueError):
            AC.report_line({}, 99)

    def test_main_returns_exit_code(self):
        self._write("good.json", _two_unit_workload())
        code = AC.main([self.tmp])
        self.assertEqual(code, 0)

    # ---- against the real, intentionally-empty shipped directory ------

    def test_real_corpus_dir_is_no_data(self):
        real_dir = os.path.join(REPO_ROOT, "benchmarks", "autonomy_corpus",
                                 "workloads")
        result, code = AC.load_corpus(real_dir)
        self.assertEqual(code, 3)
        self.assertTrue(result["nodata"])


if __name__ == "__main__":
    unittest.main()

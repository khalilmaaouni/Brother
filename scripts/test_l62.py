"""L6.2 ledger proof link tests.

Run with: python3 -B scripts/test_l62.py
"""

import importlib.util
import json
import os
import tempfile
import unittest


_HERE = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.dirname(_HERE)
_MODULE_PATH = os.path.join(_ROOT, "tools", "jev_catalogue", "proof.py")
_CATALOGUE_PATH = os.path.join(_ROOT, "docs", "plan", "JEV-USE-CASE-CATALOGUE.md")


def _load_proof():
    """Load the module under test by path; None when the file is absent."""
    if not os.path.isfile(_MODULE_PATH):
        return None
    spec = importlib.util.spec_from_file_location("jev_catalogue_proof_l62", _MODULE_PATH)
    if spec is None or spec.loader is None:
        return None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


_PROOF = _load_proof()


def _write_records(folder, records):
    path = os.path.join(folder, "openrouter-ledger.jsonl")
    with open(path, "w", encoding="utf-8") as handle:
        for record in records:
            handle.write(json.dumps(record) + "\n")
    return path


def _write_text(folder, text):
    path = os.path.join(folder, "openrouter-ledger.jsonl")
    with open(path, "w", encoding="utf-8") as handle:
        handle.write(text)
    return path


def _pair(holder_id):
    return [
        {"kind": "RESERVE", "holder_id": holder_id, "cost": 0.001},
        {"kind": "RECONCILE", "holder_id": holder_id, "cost": 0.001},
    ]


class ProofTestBase(unittest.TestCase):
    def setUp(self):
        if _PROOF is None:
            self.fail("module under test is missing: %s" % _MODULE_PATH)


class FindLedgerLinesTests(ProofTestBase):
    def test_the_real_ledger_shape_is_proven_by_reservation_id(self):
        """2026-09-28: the real ledger writes "type", and its RECONCILE row carries only the reservation_id, which is
        what the catalogue records as holder_id. Read the old way, none of the 70 catalogue entries proved."""
        rid = "jev-catalogue-11-probe-1789962964255-86798-7f7e6c98ba90"
        rows = [{"type": "RESERVE", "reservation_id": rid, "holder_id": "jev-catalogue-11-probe", "estimated_cost": 0.001},
                {"type": "RECONCILE", "reservation_id": rid, "actual_cost": 0.001},
                {"type": "RESERVE", "reservation_id": "other", "holder_id": "jev-catalogue-11-probe"}]
        with tempfile.TemporaryDirectory() as d:
            path = os.path.join(d, "openrouter-ledger.jsonl")
            with open(path, "w", encoding="utf-8") as fh:
                fh.write("".join(json.dumps(r) + "\n" for r in rows))
            lines = _PROOF.find_ledger_lines(path, rid)
        self.assertEqual(len(lines), 2)
        ok, why = _PROOF.verify_proof({"holder_id": rid}, lines)
        self.assertTrue(ok, why)
        self.assertFalse(_PROOF.verify_proof({"holder_id": rid}, lines[:1])[0], "a RESERVE alone never proves")

    def test_missing_file_is_no_data(self):
        with tempfile.TemporaryDirectory() as folder:
            path = os.path.join(folder, "absent.jsonl")
            self.assertEqual(_PROOF.find_ledger_lines(path, "h1"), [])

    def test_directory_is_no_data(self):
        with tempfile.TemporaryDirectory() as folder:
            self.assertEqual(_PROOF.find_ledger_lines(folder, "h1"), [])

    def test_corrupt_jsonl_is_no_data(self):
        with tempfile.TemporaryDirectory() as folder:
            text = json.dumps({"kind": "RESERVE", "holder_id": "h1", "cost": 0.001}) + "\n"
            text += "not json at all\n"
            path = _write_text(folder, text)
            self.assertEqual(_PROOF.find_ledger_lines(path, "h1"), [])

    def test_non_object_json_line_is_no_data(self):
        with tempfile.TemporaryDirectory() as folder:
            text = json.dumps({"kind": "RESERVE", "holder_id": "h1", "cost": 0.001}) + "\n"
            text += "[1, 2, 3]\n"
            path = _write_text(folder, text)
            self.assertEqual(_PROOF.find_ledger_lines(path, "h1"), [])

    def test_non_utf8_bytes_are_no_data(self):
        with tempfile.TemporaryDirectory() as folder:
            path = os.path.join(folder, "openrouter-ledger.jsonl")
            with open(path, "wb") as handle:
                handle.write(b"\xff\xfe\x00 not utf-8")
            self.assertEqual(_PROOF.find_ledger_lines(path, "h1"), [])

    def test_holder_id_must_match_exactly(self):
        with tempfile.TemporaryDirectory() as folder:
            records = _pair("h1x") + [{"kind": "RESERVE", "holder_id": "h1", "cost": 0.002}]
            path = _write_records(folder, records)
            lines = _PROOF.find_ledger_lines(path, "h1")
            self.assertEqual(len(lines), 1)
            self.assertEqual(lines[0]["holder_id"], "h1")

    def test_second_read_sees_the_current_file(self):
        with tempfile.TemporaryDirectory() as folder:
            path = _write_records(folder, _pair("h1"))
            self.assertEqual(len(_PROOF.find_ledger_lines(path, "h1")), 2)
            path = _write_text(folder, "gone rotten\n")
            self.assertEqual(_PROOF.find_ledger_lines(path, "h1"), [])

    def test_hostile_arguments_are_refused(self):
        args = [
            (None, "h1"), ("", "h1"), (123, "h1"), (True, "h1"), (b"/tmp/x", "h1"),
            (["/tmp/x"], "h1"), ("/tmp/x", None), ("/tmp/x", ""), ("/tmp/x", 7),
            ("/tmp/x", False), ("/tmp/x", ["h1"]), ("/tmp/x", {"h": 1}),
        ]
        for pair in args:
            with self.assertRaises(ValueError):
                _PROOF.find_ledger_lines(*pair)


class VerifyProofTests(ProofTestBase):
    def test_proof_requires_both_kinds(self):
        entry = {"holder_id": "h1"}
        reserve_only = [{"kind": "RESERVE", "holder_id": "h1", "cost": 0.001}]
        ok, reason = _PROOF.verify_proof(entry, reserve_only)
        self.assertFalse(ok)
        self.assertIn("RESERVE", reason)
        ok, reason = _PROOF.verify_proof(entry, _pair("h1"))
        self.assertTrue(ok)

    def test_reconcile_alone_is_blocked(self):
        entry = {"holder_id": "h1"}
        lines = [{"kind": "RECONCILE", "holder_id": "h1", "cost": 0.001}]
        ok, _ = _PROOF.verify_proof(entry, lines)
        self.assertFalse(ok)

    def test_other_holder_does_not_count(self):
        entry = {"holder_id": "h1"}
        ok, _ = _PROOF.verify_proof(entry, _pair("h1x"))
        self.assertFalse(ok)

    def test_empty_lines_is_blocked(self):
        ok, _ = _PROOF.verify_proof({"holder_id": "h1"}, [])
        self.assertFalse(ok)

    def test_entry_without_usable_holder_id_is_blocked(self):
        for entry in [{}, {"holder_id": None}, {"holder_id": 7}, {"holder_id": True},
                      {"holder_id": ""}, {"holder_id": ["h1"]}]:
            ok, _ = _PROOF.verify_proof(entry, _pair("h1"))
            self.assertFalse(ok)

    def test_unknown_kinds_are_blocked(self):
        entry = {"holder_id": "h1"}
        lines = [
            {"kind": "RELEASE", "holder_id": "h1", "cost": 0.001},
            {"kind": "reserve", "holder_id": "h1", "cost": 0.001},
        ]
        ok, _ = _PROOF.verify_proof(entry, lines)
        self.assertFalse(ok)

    def test_hostile_arguments_are_refused(self):
        args = [
            (None, []), ("entry", []), ([], []), (7, []),
            ({"holder_id": "h1"}, None), ({"holder_id": "h1"}, "abc"),
            ({"holder_id": "h1"}, {"a": 1}), ({"holder_id": "h1"}, [None]),
            ({"holder_id": "h1"}, [1]), ({"holder_id": "h1"}, [["x"]]),
        ]
        for pair in args:
            with self.assertRaises(ValueError):
                _PROOF.verify_proof(*pair)


class CurrentSpendViewTests(ProofTestBase):
    def test_current_spend_view_sums_reserves_only(self):
        lines = [
            {"kind": "RESERVE", "holder_id": "h1", "cost": 0.001},
            {"kind": "RECONCILE", "holder_id": "h1", "cost": 9.0},
            {"kind": "RESERVE", "holder_id": "h2", "cost": 0.002},
        ]
        self.assertAlmostEqual(_PROOF.current_spend_view(lines), 0.003)

    def test_current_spend_view_reads_the_real_ledger_shape(self):
        lines = [
            {"type": "RESERVE", "holder_id": "h1", "reservation_id": "h1", "estimated_cost": 0.004},
            {"type": "RECONCILE", "reservation_id": "h1", "actual_cost": 9.0},
        ]
        self.assertAlmostEqual(_PROOF.current_spend_view(lines), 0.004)

    def test_current_spend_view_is_read_only(self):
        with tempfile.TemporaryDirectory() as folder:
            path = _write_records(folder, _pair("h1"))
            with open(path, "rb") as handle:
                before = handle.read()
            lines = _PROOF.find_ledger_lines(path, "h1")
            _PROOF.current_spend_view(lines)
            with open(path, "rb") as handle:
                after = handle.read()
            self.assertEqual(before, after)

    def test_nan_and_inf_are_refused(self):
        for value in [float("nan"), float("inf"), float("-inf")]:
            lines = [{"kind": "RESERVE", "holder_id": "h1", "cost": value}]
            with self.assertRaises(ValueError):
                _PROOF.current_spend_view(lines)

    def test_bool_cost_is_refused(self):
        for value in [True, False]:
            lines = [{"kind": "RESERVE", "holder_id": "h1", "cost": value}]
            with self.assertRaises(ValueError):
                _PROOF.current_spend_view(lines)

    def test_non_numeric_cost_is_refused(self):
        for value in [None, "0.5", [0.5], {"v": 1}]:
            lines = [{"kind": "RESERVE", "holder_id": "h1", "cost": value}]
            with self.assertRaises(ValueError):
                _PROOF.current_spend_view(lines)

    def test_hostile_lines_are_refused(self):
        for value in [None, "abc", 7, {"a": 1}, [None], [1], [["x"]]]:
            with self.assertRaises(ValueError):
                _PROOF.current_spend_view(value)


class CatalogueDocumentTests(unittest.TestCase):
    @unittest.skipUnless(os.path.isfile(_CATALOGUE_PATH), "catalogue document not present in this tree")
    def test_catalogue_names_the_proof_link(self):
        with open(_CATALOGUE_PATH, "r", encoding="utf-8") as handle:
            text = handle.read()
        self.assertIn("tools/jev_catalogue/proof.py", text)
        self.assertIn("find_ledger_lines", text)
        self.assertIn("verify_proof", text)
        self.assertIn("current_spend_view", text)


@unittest.skipUnless(os.path.isfile(_CATALOGUE_PATH), "the live catalogue is a plan document the export tree does not ship")
class TheDoneCheckIsTheEntryPoint(unittest.TestCase):
    """scripts/donecheck_L6.py over a fixture state root: all proven is 0, one missing is 1, no ledger is 3."""

    def _run(self, holders):
        import re
        import subprocess
        import sys
        with open(_CATALOGUE_PATH, encoding="utf-8") as handle:
            ids = re.findall(r"jev-catalogue-[A-Za-z0-9_.-]+", handle.read())
        with tempfile.TemporaryDirectory() as root:
            if holders is not None:
                rows = []
                for gid in holders(sorted(set(ids))):
                    rows.append({"type": "RESERVE", "holder_id": gid, "reservation_id": gid, "estimated_cost": 0.001})
                    rows.append({"type": "RECONCILE", "reservation_id": gid, "actual_cost": 0.001})
                _write_records(root, rows)
            env = dict(os.environ, BROTHER_OR_STATE_ROOT=root)
            done = subprocess.run([sys.executable, os.path.join(_HERE, "donecheck_L6.py")], env=env,
                                  capture_output=True, text=True, timeout=60)
        return done.returncode, done.stdout

    def test_every_entry_proven_is_green(self):
        code, out = self._run(lambda ids: ids)
        self.assertEqual(code, 0, out)

    def test_one_entry_missing_is_red(self):
        code, out = self._run(lambda ids: ids[1:])
        self.assertEqual(code, 1, out)

    def test_no_ledger_is_no_data(self):
        code, out = self._run(None)
        self.assertEqual(code, 3, out)

if __name__ == "__main__":
    unittest.main()

#!/usr/bin/env python3
"""L5b.3 test_verify: fire prober loads, validates, probes, and restores."""
import json
import os
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(os.path.dirname(HERE))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from tools.l5b_audit import verify as vf


def _call(kind="network", file="a.py", line=2, column=1, symbol="f"):
    return vf.BoundaryCall(file=file, line=line, column=column,
                           symbol=symbol, kind=kind, snippet="")


def _copy_runner(root):
    pkg = os.path.join(root, "tools", "l5b_audit")
    os.makedirs(pkg, exist_ok=True)
    with open(os.path.join(HERE, "probe_runner.py"), "rb") as src:
        data = src.read()
    with open(os.path.join(pkg, "probe_runner.py"), "wb") as dst:
        dst.write(data)
    for init in (os.path.join(root, "tools", "__init__.py"),
                 os.path.join(pkg, "__init__.py")):
        with open(init, "w", encoding="utf-8") as fh:
            fh.write("")
    return pkg


class _Tmp(unittest.TestCase):
    def tmp(self, prefix="l5b3-"):
        holder = tempfile.TemporaryDirectory(prefix=prefix)
        self.addCleanup(holder.cleanup)
        return holder.name


class TestLoadFireMap(_Tmp):
    def _write(self, text):
        d = self.tmp("l5b3-fm-")
        path = os.path.join(d, "fire_map.json")
        with open(path, "w", encoding="utf-8") as fh:
            fh.write(text)
        return path

    def test_load_fire_map_reads_a_valid_contract(self):
        path = self._write(json.dumps([{
            "entry_id": "a:1:f",
            "test_id": "tools.l5b_audit.test_verify",
            "mutation_id": "M1",
            "expect_substring": "some-unique-string",
        }]))
        got = vf.load_fire_map(path)
        self.assertEqual(len(got), 1)
        self.assertEqual(got[0].entry_id, "a:1:f")
        self.assertEqual(got[0].test_id, "tools.l5b_audit.test_verify")
        self.assertEqual(got[0].mutation_id, "M1")

    def test_load_fire_map_drops_short_expect_substring(self):
        path = self._write(json.dumps([{
            "entry_id": "a:1:f", "test_id": "t",
            "mutation_id": "M1", "expect_substring": "  ",
        }]))
        self.assertEqual(vf.load_fire_map(path), ())

    def test_load_fire_map_drops_under_eight_characters(self):
        path = self._write(json.dumps([{
            "entry_id": "a:1:f", "test_id": "t",
            "mutation_id": "M1", "expect_substring": "abc",
        }]))
        self.assertEqual(vf.load_fire_map(path), ())

    def test_load_fire_map_rejects_non_utf8(self):
        d = self.tmp("l5b3-fm-bytes-")
        path = os.path.join(d, "fire_map.json")
        with open(path, "wb") as fh:
            fh.write(b"\xff\xfe\xff")
        with self.assertRaises(ValueError):
            vf.load_fire_map(path)

    def test_load_fire_map_rejects_corrupt_json(self):
        with self.assertRaises(ValueError):
            vf.load_fire_map(self._write("{not json"))

    def test_load_fire_map_rejects_non_list(self):
        with self.assertRaises(ValueError):
            vf.load_fire_map(self._write('{"a": 1}'))

    def test_load_fire_map_rejects_missing_file(self):
        with self.assertRaises(ValueError):
            vf.load_fire_map("/no/such/file/xyz.json")

    def test_load_fire_map_rejects_hostile_path(self):
        for bad in [None, 1, True, [], {}, float("nan"), b"x", ""]:
            with self.assertRaises(ValueError):
                vf.load_fire_map(bad)

    def test_load_fire_map_rejects_wrong_typed_entry(self):
        for bad in [None, 1, [], "x"]:
            with self.assertRaises(ValueError):
                vf.load_fire_map(self._write(json.dumps([bad])))

    def test_load_fire_map_rejects_missing_key(self):
        with self.assertRaises(ValueError):
            vf.load_fire_map(self._write(json.dumps([{"entry_id": "a"}])))


class TestAnnotationClearances(_Tmp):
    def _tree(self, name, text):
        d = self.tmp("l5b3-ann-")
        full = os.path.join(d, name)
        os.makedirs(os.path.dirname(full), exist_ok=True)
        with open(full, "w", encoding="utf-8") as fh:
            fh.write(text)
        return d

    def test_clearance_covered_with_good_reason(self):
        d = self._tree("a.py", "# l5b: PROPAGATE_SAFE: twelve chars long\nx = 1\n")
        got = vf.annotation_clearances((_call(file="a.py", line=2),), d)
        self.assertEqual(len(got), 1)
        self.assertTrue(got[0].covered)
        self.assertEqual(got[0].call_id, "a.py:<module>:f#1")

    def test_clearance_uncovered_when_short(self):
        d = self._tree("a.py", "# l5b: PROPAGATE_SAFE: hi\nx = 1\n")
        got = vf.annotation_clearances((_call(file="a.py", line=2),), d)
        self.assertFalse(got[0].covered)

    def test_clearance_uncovered_when_no_marker(self):
        d = self._tree("a.py", "x = 1\ny = 2\n")
        got = vf.annotation_clearances((_call(file="a.py", line=2),), d)
        self.assertFalse(got[0].covered)

    def test_clearance_missing_file_blocks(self):
        d = self.tmp("l5b3-ann-empty-")
        with self.assertRaises(ValueError):
            vf.annotation_clearances((_call(file="a.py", line=2),), d)

    def test_clearances_reject_hostile_calls(self):
        for bad in [None, [], "x", 1]:
            with self.assertRaises(ValueError):
                vf.annotation_clearances(bad, "/tmp")

    def test_clearances_reject_hostile_root(self):
        for bad in [None, 1, True, [], {}, b"x", ""]:
            with self.assertRaises(ValueError):
                vf.annotation_clearances((), bad)


class TestCheckerBypass(_Tmp):
    def _tree(self, name, text):
        d = self.tmp("l5b3-cb-")
        full = os.path.join(d, name)
        os.makedirs(os.path.dirname(full), exist_ok=True)
        with open(full, "w", encoding="utf-8") as fh:
            fh.write(text)
        return d

class TestRunProbe(unittest.TestCase):
    def setUp(self):
        holder = tempfile.TemporaryDirectory(prefix="l5b3-probe-")
        self.addCleanup(holder.cleanup)
        self.root = holder.name
        pkg = _copy_runner(self.root)
        with open(os.path.join(self.root, "tiny.py"), "w", encoding="utf-8") as fh:
            fh.write("def f():\n    return 1\n")
        with open(os.path.join(self.root, "test_tiny.py"), "w", encoding="utf-8") as fh:
            fh.write(
                "import unittest\n"
                "import tiny\n"
                "class T(unittest.TestCase):\n"
                "    def test_value(self):\n"
                "        self.assertEqual(tiny.f(), 1)\n"
                "    def test_other(self):\n"
                "        self.assertTrue(True)\n"
            )
        mutations = [{
            "id": "M-VALUE", "path": "tiny.py",
            "find": "return 1", "replace": "return 2",
            "why": "wrong value", "expect_test": "test_value",
        }]
        with open(os.path.join(pkg, "mutations.json"), "w", encoding="utf-8") as fh:
            json.dump(mutations, fh)

    def test_run_probe_fires_and_restores(self):
        entry = vf.FireEntry("e1", "test_tiny", "M-VALUE", "test_value")
        result = vf.run_probe(entry, self.root)
        self.assertTrue(result.fired, msg=str(result))
        self.assertEqual(result.unmutated_code, 0)
        self.assertNotEqual(result.mutated_code, 0)
        with open(os.path.join(self.root, "tiny.py"), encoding="utf-8") as fh:
            self.assertEqual(fh.read(), "def f():\n    return 1\n")

    def test_run_probe_does_not_fire_when_output_lacks_substring(self):
        entry = vf.FireEntry("e1", "test_tiny", "M-VALUE", "no-such-substring")
        result = vf.run_probe(entry, self.root)
        self.assertFalse(result.fired)
        self.assertIn("expected substring", result.reason)

    def test_run_probe_refuses_hostile_entry(self):
        for bad in [None, 1, True, [], {}, "x"]:
            with self.assertRaises(ValueError):
                vf.run_probe(bad, self.root)

    def test_run_probe_refuses_hostile_root(self):
        entry = vf.FireEntry("e", "test_tiny", "M-VALUE", "test_value")
        for bad in [None, 1, True, [], {}, b"x", ""]:
            with self.assertRaises(ValueError):
                vf.run_probe(entry, bad)

    def test_run_probe_no_data_when_mutation_missing(self):
        entry = vf.FireEntry("e", "test_tiny", "M-MISSING", "test_value")
        result = vf.run_probe(entry, self.root)
        self.assertFalse(result.fired)
        self.assertIn("NO-DATA", result.reason)

    def test_run_probe_refuses_short_substring(self):
        entry = vf.FireEntry("e", "test_tiny", "M-VALUE", "  ")
        with self.assertRaises(ValueError):
            vf.run_probe(entry, self.root)

    def test_run_probe_no_data_when_mutations_file_is_corrupt(self):
        path = os.path.join(self.root, "tools", "l5b_audit", "mutations.json")
        with open(path, "w", encoding="utf-8") as fh:
            fh.write("{not json")
        entry = vf.FireEntry("e", "test_tiny", "M-VALUE", "test_value")
        result = vf.run_probe(entry, self.root)
        self.assertFalse(result.fired)
        self.assertIn("NO-DATA", result.reason)

    def test_run_probe_no_data_when_find_is_not_unique(self):
        path = os.path.join(self.root, "tools", "l5b_audit", "mutations.json")
        with open(path, "w", encoding="utf-8") as fh:
            json.dump([{"id": "M-DUP", "path": "tiny.py",
                        "find": "e", "replace": "e"}], fh)
        entry = vf.FireEntry("e", "test_tiny", "M-DUP", "test_value")
        result = vf.run_probe(entry, self.root)
        self.assertFalse(result.fired)
        self.assertIn("NO-DATA", result.reason)


class TestVerifyAll(_Tmp):
    def _manifest(self, root, text):
        path = os.path.join(root, "fire_map.json")
        with open(path, "w", encoding="utf-8") as fh:
            fh.write(text)
        return path

    def test_verify_all_returns_no_data_when_no_entry(self):
        d = self.tmp("l5b3-vall-")
        manifest = self._manifest(d, "[]")
        result = vf.verify_all((_call(file="a.py", line=1),), manifest, d)
        self.assertEqual(len(result), 1)
        self.assertFalse(result[0].fired)
        self.assertIn("NO-DATA", result[0].reason)

    def test_verify_all_refuses_an_id_two_calls_share(self):
        # 2026-10-01: two os.path.abspath calls on one line gave two fired records for one id, and the audit refused
        # the whole fires file. An ambiguous id is NO-DATA for every call that carries it, and no probe runs.
        d = self.tmp("l5b3-vall-dup-")
        first = _call(file="a.py", line=3)
        twin = first._replace(column=first.column + 20)
        cid = vf._call_id(first)
        manifest = self._manifest(d, json.dumps([{"entry_id": cid, "test_id": "pkg.test_a", "mutation_id": "M-X",
                                                  "expect_substring": "test_something_long"}]))
        def no_probe(*_a, **_k):
            raise AssertionError("a probe ran for an ambiguous id")
        old = vf.run_probe
        vf.run_probe = no_probe
        try:
            result = vf.verify_all((first, twin), manifest, d)
        finally:
            vf.run_probe = old
        self.assertEqual(len(result), 2)
        for r in result:
            self.assertFalse(r.fired)
            self.assertIn("NO-DATA: 2 scanned calls share the id", r.reason)

    def test_verify_all_rejects_hostile_calls(self):
        d = self.tmp("l5b3-vall2-")
        manifest = self._manifest(d, "[]")
        for bad in [None, [], "x", 1]:
            with self.assertRaises(ValueError):
                vf.verify_all(bad, manifest, d)

    def test_verify_all_rejects_hostile_manifest(self):
        d = self.tmp("l5b3-vall3-")
        for bad in [None, 1, True, [], {}, b"x", ""]:
            with self.assertRaises(ValueError):
                vf.verify_all((), bad, d)

    def test_verify_all_rejects_hostile_root(self):
        for bad in [None, 1, True, [], {}, b"x", ""]:
            with self.assertRaises(ValueError):
                vf.verify_all((), "/tmp/x.json", bad)

    def test_verify_all_rejects_corrupt_manifest(self):
        d = self.tmp("l5b3-vall4-")
        manifest = self._manifest(d, "{not json")
        with self.assertRaises(ValueError):
            vf.verify_all((), manifest, d)


if __name__ == "__main__":
    unittest.main()

#!/usr/bin/env python3
"""Regression tests for tools/bm_repomap.py, the symbol and import map (F5).
Standard library only. Run: python3 tools/test_bm_repomap.py

Every test builds its own tempfile.mkdtemp() fixture tree rather than reading this repository, so
the expected symbol/import list is exact and does not drift as tools/ grows."""
import importlib.util
import json
import os
import shutil
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
_spec = importlib.util.spec_from_file_location("bm_repomap", os.path.join(HERE, "bm_repomap.py"))
brm = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(brm)


class FixtureTreeCase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="bm_repomap_test_")

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def write(self, rel, content):
        full = os.path.join(self.tmp, rel)
        os.makedirs(os.path.dirname(full), exist_ok=True)
        with open(full, "w", encoding="utf-8") as f:
            f.write(content)
        return full


class TestBuildMap(FixtureTreeCase):
    def test_extracts_functions_classes_and_methods(self):
        a = self.write("a.py",
                        "import os\n"
                        "from json import dumps\n\n"
                        "def foo():\n"
                        "    pass\n\n"
                        "class Bar:\n"
                        "    def baz(self):\n"
                        "        pass\n")
        result = brm.build_map([self.tmp])
        entry = result[a]
        self.assertEqual(entry["symbols"], ["Bar", "Bar.baz", "foo"])
        self.assertEqual(entry["imports"], ["json", "os"])
        self.assertFalse(entry["parse_error"])

    def test_noise_dir_is_skipped(self):
        self.write("__pycache__/junk.py", "def hidden():\n    pass\n")
        self.write("a.py", "def foo():\n    pass\n")
        result = brm.build_map([self.tmp])
        for key in result:
            self.assertNotIn("__pycache__", key)

    def test_dot_dir_is_skipped(self):
        self.write(".venv/lib.py", "def hidden():\n    pass\n")
        self.write("a.py", "def foo():\n    pass\n")
        result = brm.build_map([self.tmp])
        for key in result:
            self.assertNotIn(os.sep + ".venv" + os.sep, key)

    def test_non_python_file_is_ignored(self):
        self.write("notes.md", "# not python\n")
        result = brm.build_map([self.tmp])
        self.assertEqual(result, {})


class TestMapDeterminism(FixtureTreeCase):
    def test_same_tree_produces_identical_json(self):
        self.write("a.py", "def foo():\n    pass\n")
        self.write("pkg/b.py", "class C:\n    def m(self):\n        pass\n")
        m1 = brm.build_map([self.tmp])
        m2 = brm.build_map([self.tmp])
        self.assertEqual(json.dumps(m1, sort_keys=True), json.dumps(m2, sort_keys=True))


class TestParseErrorHandling(FixtureTreeCase):
    def test_unparsable_file_gets_a_flagged_entry_not_a_raise(self):
        bad = self.write("broken.py", "def foo(:\n    pass\n")
        result = brm.build_map([self.tmp])
        self.assertEqual(result[bad], {"symbols": [], "imports": [], "parse_error": True})

    def test_one_bad_file_does_not_drop_the_rest_of_the_map(self):
        self.write("broken.py", "def foo(:\n    pass\n")
        good = self.write("good.py", "def bar():\n    pass\n")
        result = brm.build_map([self.tmp])
        self.assertIn(good, result)
        self.assertEqual(result[good]["symbols"], ["bar"])


class TestCliMain(FixtureTreeCase):
    def test_writes_json_to_out_path_and_exits_zero(self):
        self.write("a.py", "def foo():\n    pass\n")
        out = os.path.join(self.tmp, "map.json")
        rc = brm.main(["--root", self.tmp, "--out", out])
        self.assertEqual(rc, 0)
        with open(out, encoding="utf-8") as fh:
            data = json.load(fh)
        self.assertEqual(len(data), 1)

    def test_missing_root_is_no_data_exit_two(self):
        rc = brm.main(["--root", os.path.join(self.tmp, "does-not-exist"), "--out", "-"])
        self.assertEqual(rc, 2)


if __name__ == "__main__":
    unittest.main()

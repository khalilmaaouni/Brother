"""L5b.7: the fires command records what the probes measured, in a disposable
copy, never in the live tree. Each test builds a fixture repository in a temp
directory holding a module, its suite and the real tools/l5b_audit files."""
import io
import json
import os
import shutil
import sys
import tempfile
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
SCRIPTS = os.path.join(ROOT, "scripts")
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)
if SCRIPTS not in sys.path:
    sys.path.insert(0, SCRIPTS)
from tools.l5b_audit import verify  # noqa: E402
from tools.l5b_audit import scanner  # noqa: E402
import l5b_audit  # noqa: E402

_MOD = '''def read_config(path):
    try:
        with open(path) as fh:
            return fh.read()
    except OSError as exc:
        raise ValueError("config unreadable: %s" % exc)
'''
_TEST = '''import unittest
from pkg import mod


class T(unittest.TestCase):
    def test_unreadable_raises_value_error(self):
        with self.assertRaises(ValueError):
            mod.read_config("/nonexistent/l5b/x")
'''
_RAISE = '        raise ValueError("config unreadable: %s" % exc)\n'


def _write(root, rel, text):
    path = os.path.join(root, rel)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(text)
    return path


class TestFiresCommand(unittest.TestCase):

    def setUp(self):
        self.repo = os.path.realpath(tempfile.mkdtemp(prefix="l5b7-repo-"))
        self.addCleanup(shutil.rmtree, self.repo, True)
        _write(self.repo, "pkg/__init__.py", "")
        self.mod = _write(self.repo, "pkg/mod.py", _MOD)
        _write(self.repo, "pkg/test_mod.py", _TEST)
        src = os.path.join(ROOT, "tools", "l5b_audit")
        dst = os.path.join(self.repo, "tools", "l5b_audit")
        os.makedirs(os.path.join(self.repo, "tools"))
        _write(self.repo, "tools/__init__.py", "")
        shutil.copytree(src, dst, ignore=shutil.ignore_patterns("__pycache__", "*.pyc", "test_*.py"))
        self.entry_id = [
            scanner.call_id("pkg/" + c.file, c.qualname, c.symbol, c.ordinal)
            for c in scanner.scan_tree(os.path.join(self.repo, "pkg")) if c.symbol == "open"][0]
        _write(self.repo, "tools/l5b_audit/mutations.json", json.dumps([
            {"id": "M-DROP-RAISE", "path": "pkg/mod.py", "find": _RAISE, "replace": '        return ""\n',
             "why": "fixture", "expect_test": "test_unreadable_raises_value_error"},
            {"id": "M-TOUCH", "path": "pkg/mod.py", "find": "def read_config(path):\n",
             "replace": "def read_config(path):  # touched\n", "why": "fixture", "expect_test": "x"},
        ]))
        self.fire_map = _write(self.repo, "fixture_fire_map.json", json.dumps([
            {"entry_id": self.entry_id, "test_id": "pkg.test_mod", "mutation_id": "M-DROP-RAISE",
             "expect_substring": "test_unreadable_raises_value_error"}]))
        self.out = os.path.join(self.repo, "fires.json")

    def _main(self, extra=()):
        argv = ["--root", "pkg", "--out", self.out, "--repo", self.repo, "--fire-map", self.fire_map] + list(extra)
        out, err = io.StringIO(), io.StringIO()
        old_out, old_err = sys.stdout, sys.stderr
        sys.stdout, sys.stderr = out, err
        try:
            code = verify.main(argv)
        finally:
            sys.stdout, sys.stderr = old_out, old_err
        return code, out.getvalue(), err.getvalue()

    def _fires(self):
        with open(self.out, encoding="utf-8") as fh:
            return json.load(fh)

    def test_a_fired_entry_is_recorded_and_the_audit_reads_it(self):
        code, out, err = self._main()
        self.assertEqual(code, 0, err)
        data = self._fires()
        self.assertEqual(data["scheme_version"], "l5b-fires-v1")
        fired = [f for f in data["fires"] if f["entry_id"] == self.entry_id]
        self.assertEqual(len(fired), 1)
        self.assertTrue(fired[0]["fired"], fired[0])
        self.assertEqual(fired[0]["test_id"], "pkg.test_mod")
        fires, bypass = l5b_audit._read_fires(self.out)
        self.assertEqual(sum(1 for f in fires if f.fired), 1)
        self.assertTrue(out.startswith("FIRES "), out)
        # option A (2026-10-02): the fixture raises BELOW its open call, the shape the old line-order rule marked as
        # a bypass; a fired call is checked by its fire, so nothing is listed
        self.assertEqual(data["checker_bypass"], [])

    def test_the_live_fixture_is_never_written(self):
        with open(self.mod, "rb") as fh:
            before = fh.read()
        mtime = os.stat(self.mod).st_mtime_ns
        self.assertEqual(self._main()[0], 0)
        with open(self.mod, "rb") as fh:
            self.assertEqual(fh.read(), before)
        self.assertEqual(os.stat(self.mod).st_mtime_ns, mtime)

    def test_a_survived_mutation_is_recorded_unfired(self):
        _write(self.repo, "fixture_fire_map.json", json.dumps([
            {"entry_id": self.entry_id, "test_id": "pkg.test_mod", "mutation_id": "M-TOUCH",
             "expect_substring": "test_unreadable_raises_value_error"}]))
        self.assertEqual(self._main()[0], 0)
        record = [f for f in self._fires()["fires"] if f["entry_id"] == self.entry_id][0]
        self.assertFalse(record["fired"])
        self.assertEqual(record["reason"], "mutation survived")

    def test_a_missing_fire_map_writes_nothing(self):
        os.unlink(self.fire_map)
        code, _out, err = self._main()
        self.assertEqual(code, 2)
        self.assertTrue(err.startswith("NO-DATA:"), err)
        self.assertFalse(os.path.exists(self.out))

    def test_the_copy_is_a_whole_checkout_with_a_repository_marker(self):
        # 2026-10-01: the copy held only the scanned root and tools/, so a module that resolves its repository (a
        # sibling folder no entry names, or the walk up to .git) failed its own tests there and could never fire.
        _write(self.repo, "extra/needed.txt", "a folder no fire map entry names\n")
        _write(self.repo, "pkg/test_repo.py", (
            "import os, unittest\n"
            "from pkg import mod\n"
            "ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))\n"
            "class R(unittest.TestCase):\n"
            "    def test_unreadable_raises_value_error(self):\n"
            "        self.assertTrue(os.path.isfile(os.path.join(ROOT, 'extra', 'needed.txt')), 'extra/ not copied')\n"
            "        self.assertTrue(os.path.isdir(os.path.join(ROOT, '.git')), 'no .git marker')\n"
            "        with self.assertRaises(ValueError):\n"
            "            mod.read_config(os.path.join(ROOT, 'no-such-file'))\n"))
        _write(self.repo, "fixture_fire_map.json", json.dumps([
            {"entry_id": self.entry_id, "test_id": "pkg.test_repo", "mutation_id": "M-DROP-RAISE",
             "expect_substring": "test_unreadable_raises_value_error"}]))
        code, _out, err = self._main()
        self.assertEqual(code, 0, err)
        record = [f for f in self._fires()["fires"] if f["entry_id"] == self.entry_id][0]
        self.assertTrue(record["fired"], record)
        self.assertFalse(os.path.exists(os.path.join(self.repo, ".git")), "the marker belongs to the copy only")

    def test_the_copy_is_removed(self):
        owned = tempfile.mkdtemp(prefix="l5b7-tmp-")
        self.addCleanup(shutil.rmtree, owned, True)
        old = tempfile.tempdir
        tempfile.tempdir = owned
        self.addCleanup(setattr, tempfile, "tempdir", old)
        self.assertEqual(self._main()[0], 0)
        self.assertEqual([n for n in os.listdir(owned) if n.startswith("l5b-fires-")], [])
        os.unlink(self.fire_map)
        self.assertEqual(self._main()[0], 2)
        self.assertEqual([n for n in os.listdir(owned) if n.startswith("l5b-fires-")], [])

    def test_hostile_argv_is_refused(self):
        err = io.StringIO()
        old = sys.stderr
        sys.stderr = err
        try:
            for bad in (0, "x", [1], ["--root"]):
                self.assertEqual(verify.main(bad), 2, repr(bad))
        finally:
            sys.stderr = old


if __name__ == "__main__":
    sys.exit(unittest.main())

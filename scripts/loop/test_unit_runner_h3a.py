#!/usr/bin/env python3
"""H3.a: atomic STATUS writes for scripts/loop/unit_runner.py and scripts/loop/land_batch.py.

write_status is lifted out of the parsed source of unit_runner.py with the same helper
scripts/test_unit_runner_probe_gate.py uses, and land_batch.py is imported normally. The file
never calls getattr: the one stand-in for os exposes exactly the attributes write_status uses,
so no dynamic attribute lookup is needed anywhere below.
"""
import ast
import datetime
import os
import sys
import tempfile
import unittest
from unittest import mock

HERE = os.path.dirname(os.path.abspath(__file__))
PARENT = os.path.dirname(HERE)
if HERE not in sys.path:
    sys.path.insert(0, HERE)
if PARENT not in sys.path:
    sys.path.insert(0, PARENT)

import land_batch as LB
import test_unit_runner_probe_gate as PG

UNIT_RUNNER = os.path.join(HERE, "unit_runner.py")
LAND_BATCH = os.path.join(HERE, "land_batch.py")
NL = "\n"


def read_source(path):
    with open(path, "rb") as fh:
        return fh.read().decode("utf-8")


def lift_write_status(os_standin=None):
    fn = PG.load_fn(UNIT_RUNNER, "write_status")
    if fn is None:
        return None
    if os_standin is not None:
        fn.__globals__["os"] = os_standin
    return fn


class _ReplaceFails(object):
    """A stand-in for os whose replace raises OSError, whose fsync and path are the real ones.

    Only the attributes write_status actually touches are exposed here, so there is no
    __getattr__ and no getattr anywhere in this file at all.
    """

    def __init__(self, real):
        self.path = real.path
        self._fsync = real.fsync

    def fsync(self, fd):
        return self._fsync(fd)

    def replace(self, src, dst):
        raise OSError("simulated replace failure")


def _open_mode(node):
    if not isinstance(node, ast.Call) or not isinstance(node.func, ast.Name) or node.func.id != "open":
        return None
    if len(node.args) > 1 and isinstance(node.args[1], ast.Constant) and isinstance(node.args[1].value, str):
        return node.args[1].value
    for kw in node.keywords:
        if kw.arg == "mode" and isinstance(kw.value, ast.Constant) and isinstance(kw.value.value, str):
            return kw.value.value
    return None


def _is_write_mode(mode):
    return mode is not None and any(c in mode for c in ("w", "a", "x", "+"))


class WriteStatusCases(unittest.TestCase):

    def test_write_status_replaces_and_leaves_no_tmp(self):
        fn = lift_write_status()
        self.assertTrue(callable(fn), "unit_runner.write_status is missing")
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "STATUS")
            fn(path, "one" + NL)
            self.assertEqual(open(path, encoding="utf-8").read(), "one" + NL)
            fn(path, "two" + NL)
            self.assertEqual(open(path, encoding="utf-8").read(), "two" + NL)
            self.assertFalse(os.path.exists(path + ".tmp"))

    def test_kill_between_open_and_replace_leaves_old_text(self):
        fn = lift_write_status()
        self.assertTrue(callable(fn), "unit_runner.write_status is missing")
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "STATUS")
            with open(path, "w", encoding="utf-8") as fh:
                fh.write("OLD" + NL)
            fn.__globals__["os"] = _ReplaceFails(os)
            with self.assertRaises(OSError) as cm:
                fn(path, "NEW" + NL)
            self.assertEqual(open(path, encoding="utf-8").read(), "OLD" + NL)
            self.assertIn(path, str(cm.exception))

    def test_stale_tmp_is_overwritten(self):
        fn = lift_write_status()
        self.assertTrue(callable(fn), "unit_runner.write_status is missing")
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "STATUS")
            with open(path + ".tmp", "w", encoding="utf-8") as fh:
                fh.write("HALF")
            fn(path, "NEW" + NL)
            self.assertEqual(open(path, encoding="utf-8").read(), "NEW" + NL)
            self.assertFalse(os.path.exists(path + ".tmp"))

    def test_vanished_directory_refuses_with_named_oserror(self):
        fn = lift_write_status()
        self.assertTrue(callable(fn), "unit_runner.write_status is missing")
        with tempfile.TemporaryDirectory() as tmp:
            missing = os.path.join(tmp, "missing", "STATUS")
            with self.assertRaises(OSError) as cm:
                fn(missing, "x" + NL)
            self.assertIn(missing, str(cm.exception))
            d = os.path.join(tmp, "adir")
            os.mkdir(d)
            with self.assertRaises(OSError) as cm2:
                fn(d, "x" + NL)
            self.assertIn(d, str(cm2.exception))

    def test_hostile_path_and_text_refused(self):
        fn = lift_write_status()
        self.assertTrue(callable(fn), "unit_runner.write_status is missing")
        lb_fn = LB.write_status
        self.assertTrue(callable(lb_fn), "land_batch.write_status is missing")
        paths = [None, True, 5, b"x", "", ["a"], {}, {1, 2}]
        texts = [None, b"x", 5, float("nan"), ["a"]]
        with tempfile.TemporaryDirectory() as tmp:
            before = sorted(os.listdir(tmp))
            for p in paths:
                with self.assertRaises(ValueError):
                    fn(p, "ok" + NL)
                with self.assertRaises(ValueError):
                    lb_fn(p, "ok" + NL)
            good = os.path.join(tmp, "STATUS")
            for t in texts:
                with self.assertRaises(ValueError):
                    fn(good, t)
                with self.assertRaises(ValueError):
                    lb_fn(good, t)
            self.assertEqual(sorted(os.listdir(tmp)), before)


class RoutingCases(unittest.TestCase):

    def test_status_routes_through_write_status(self):
        src = read_source(UNIT_RUNNER)
        tree = ast.parse(src)
        status_fn = None
        for node in tree.body:
            if isinstance(node, ast.FunctionDef) and node.name == "status":
                status_fn = node
                break
        self.assertIsNotNone(status_fn, "status() is missing")
        calls = [n for n in ast.walk(status_fn) if isinstance(n, ast.Call)
                 and isinstance(n.func, ast.Name) and n.func.id == "write_status"]
        self.assertTrue(calls, "status() does not call write_status")
        for node in ast.walk(tree):
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id == "open":
                if node.args:
                    first = ast.unparse(node.args[0])
                    self.assertNotIn("STATUS", first, "an open() call still names a STATUS path")

    def test_land_batch_status_writers_route(self):
        src = read_source(LAND_BATCH)
        tree = ast.parse(src)
        funcs = {}
        for node in tree.body:
            if isinstance(node, ast.FunctionDef):
                funcs[node.name] = node
        for name in ("mark_landed", "refuse_landing", "unwind"):
            fn = funcs.get(name)
            self.assertIsNotNone(fn, "%s is missing" % name)
            calls = [n for n in ast.walk(fn) if isinstance(n, ast.Call)
                     and isinstance(n.func, ast.Name) and n.func.id == "write_status"]
            self.assertTrue(calls, "%s does not call write_status" % name)
            for node in ast.walk(fn):
                if isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id == "open":
                    mode = _open_mode(node)
                    self.assertFalse(_is_write_mode(mode),
                                     "%s still opens with a literal write mode %r" % (name, mode))
        main_fn = funcs.get("main")
        self.assertIsNotNone(main_fn, "main is missing")
        main_calls = [n for n in ast.walk(main_fn) if isinstance(n, ast.Call)
                      and isinstance(n.func, ast.Name) and n.func.id == "write_status"]
        self.assertTrue(main_calls, "main has STATUS writers that do not route through write_status")
        for node in ast.walk(main_fn):
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id == "open":
                if node.args:
                    first = ast.unparse(node.args[0])
                    if first in ("st", "status_paths[0]"):
                        mode = _open_mode(node)
                        self.assertFalse(_is_write_mode(mode),
                                         "main still opens STATUS %s with write mode %r" % (first, mode))


class LandBatchAtomicCases(unittest.TestCase):

    def test_land_batch_mark_landed_atomic(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "STATUS")
            with open(path, "w", encoding="utf-8") as fh:
                fh.write("OLD" + NL)
            with mock.patch("os.replace", side_effect=OSError("boom")):
                written = LB.mark_landed([path], ["/x/b.json"], "abc1234",
                                         now=datetime.datetime(2026, 9, 24, 11, 0, 0))
            self.assertEqual(written, [])
            self.assertEqual(open(path, encoding="utf-8").read(), "OLD" + NL)
            written = LB.mark_landed([path], ["/x/b.json"], "abc1234",
                                     now=datetime.datetime(2026, 9, 24, 11, 0, 0))
            self.assertEqual(written, [path])
            self.assertEqual(open(path, encoding="utf-8").read(),
                             "LANDED /x/b.json at 2026-09-24 11:00:00 commit abc1234" + NL)
            self.assertFalse(os.path.exists(path + ".tmp"))
        written = LB.mark_landed([None, 5], ["/x/b.json", "/x/c.json"], "abc1234")
        self.assertEqual(written, [])


if __name__ == "__main__":
    unittest.main(verbosity=2)

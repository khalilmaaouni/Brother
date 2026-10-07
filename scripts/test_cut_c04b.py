"""C0.4b tests: dedupe, reuse, the limited cache and the limited resume.

Run: python3 -B -m unittest scripts.test_cut_c04b

Every test asserts what the C0.4b specification says, never what the module
happens to do: a distinct suite is measured once, a hit needs an exact key
plus a clear flake gate plus a clear clock gate, unknown input is a miss, and
a dirty tree or a moved HEAD refuses the resume.
"""
import hashlib
import importlib.util
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
import unittest
from unittest import mock


def _load_cut():
    here = os.path.dirname(os.path.abspath(__file__))
    spec = importlib.util.spec_from_file_location("cut_c04b_under_test",
                                                  os.path.join(here, "cut.py"))
    module = importlib.util.module_from_spec(spec)
    sys.modules["cut_c04b_under_test"] = module
    spec.loader.exec_module(module)
    return module


cut = _load_cut()

POLICY_SHA256 = "ab" * 32
DEADLINE_ENV = "CUT_DEADLINE_UNIX"


def _make_root(tmp):
    root = os.path.join(tmp, "root")
    scripts = os.path.join(root, "scripts")
    os.makedirs(scripts)
    with open(os.path.join(scripts, "required_fast.sh"), "w",
              encoding="utf-8") as handle:
        handle.write("#!/bin/sh\nexit 0\n")
    with open(os.path.join(scripts, "fake_gate.py"), "w",
              encoding="utf-8") as handle:
        handle.write("print('ok')\n")
    return root


def _gate_cmd():
    return [sys.executable, "-B", os.path.join("scripts", "fake_gate.py")]


def _write_ledger(path, gate_name, groups=5, per_group=4):
    lines = []
    for index in range(groups):
        code_hash = "%064x" % (index + 1)
        inputs_hash = "%064x" % (100 + index)
        for _ in range(per_group):
            lines.append(json.dumps({
                "schema": 1,
                "gate_name": gate_name,
                "status": "PASS",
                "code_hash": code_hash,
                "inputs_hash": inputs_hash,
            }, sort_keys=True))
    with open(path, "w", encoding="utf-8") as handle:
        handle.write("\n".join(lines) + "\n")


def _green_entry(key):
    stamp = "2026-09-20T00:00:00Z"
    return {
        "schema_version": cut.CACHE_SCHEMA_VERSION,
        "key": key,
        "verdict": "PASS",
        "exit_code": 0,
        "seconds": 1.5,
        "receipt_id": "%s:%s:%s" % ("d" * 40, "fake-gate", stamp),
        "recorded_at": stamp,
    }


class _Runner(object):
    """A runner that records every command it is handed and answers 0."""

    def __init__(self, code=0):
        self.calls = []
        self.code = code

    def __call__(self, cmd, **kwargs):
        self.calls.append(list(cmd))
        if list(cmd[:3]) == ["git", "rev-parse", "HEAD"]:
            return subprocess.CompletedProcess(cmd, 0, "c" * 40 + "\n", "")
        return subprocess.CompletedProcess(cmd, self.code, "ok\n", "")


class _GitRunner(object):
    """A stand-in for cut._run, so the resume tests read a chosen git state
    without creating a repository on disk."""

    def __init__(self, head="", dirty=None, code=0):
        self.head = head
        self.dirty = list(dirty or [])
        self.code = code
        self.calls = []

    def __call__(self, cmd, root, runner=None, stdin_text=None, timeout=None,
                 env=None):
        self.calls.append(list(cmd))
        if list(cmd[:3]) == ["git", "rev-parse", "HEAD"]:
            return subprocess.CompletedProcess(cmd, self.code,
                                               self.head + "\n", "")
        if list(cmd[:3]) == ["git", "status", "--porcelain"]:
            return subprocess.CompletedProcess(cmd, self.code,
                                               "".join(self.dirty), "")
        return subprocess.CompletedProcess(cmd, self.code, "", "")


class TestCache(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="c04b-cache-")
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)
        self.root = _make_root(self.tmp)
        self.store = os.path.join(self.tmp, "store")
        self.ledger = os.path.join(self.tmp, "ledger.jsonl")
        self.cmd = _gate_cmd()

    def _env(self, deadline=True):
        value = ("%.6f" % (time.time() + 3600.0)) if deadline else ""
        return {"GATE_LEDGER_PATH": self.ledger, DEADLINE_ENV: value}

    def test_hit_and_poison_miss(self):
        _write_ledger(self.ledger, "fake-gate")
        with mock.patch.dict(os.environ, self._env()):
            first = cut.verdict_key(self.root, "fake-gate", self.cmd,
                                    POLICY_SHA256)
            cut.cache_record(self.store, first, _green_entry(first))
            self.assertIsNotNone(cut.cache_lookup(self.store, first))
            hit = _Runner()
            code, note = cut.cached_run_check(self.root, "fake-gate",
                                              self.cmd, self.store,
                                              POLICY_SHA256, runner=hit)
            self.assertEqual(code, 0)
            self.assertIsNotNone(note)
            self.assertIn("HIT", note)
            self.assertEqual(hit.calls, [])
            with open(os.path.join(self.root, "scripts", "fake_gate.py"),
                      "a", encoding="utf-8") as handle:
                handle.write("# one byte more\n")
            second = cut.verdict_key(self.root, "fake-gate", self.cmd,
                                     POLICY_SHA256)
            self.assertNotEqual(first, second)
            self.assertIsNone(cut.cache_lookup(self.store, second))
            miss = _Runner()
            code, note = cut.cached_run_check(self.root, "fake-gate",
                                              self.cmd, self.store,
                                              POLICY_SHA256, runner=miss)
            self.assertEqual(code, 0)
            self.assertIsNone(note)
            self.assertEqual(miss.calls[0], self.cmd)

    def test_flake_default_blocks_hit(self):
        with open(self.ledger, "w", encoding="utf-8") as handle:
            handle.write("")
        with mock.patch.dict(os.environ, self._env()):
            key = cut.verdict_key(self.root, "fake-gate", self.cmd,
                                  POLICY_SHA256)
            cut.cache_record(self.store, key, _green_entry(key))
            runner = _Runner()
            code, note = cut.cached_run_check(self.root, "fake-gate",
                                              self.cmd, self.store,
                                              POLICY_SHA256, runner=runner)
            self.assertEqual(code, 0)
            self.assertIsNone(note)
            self.assertEqual(runner.calls[0], self.cmd)

    def test_clock_unknown_blocks_hit(self):
        _write_ledger(self.ledger, "fake-gate")
        with mock.patch.dict(os.environ, self._env(deadline=False)):
            key = cut.verdict_key(self.root, "fake-gate", self.cmd,
                                  POLICY_SHA256)
            cut.cache_record(self.store, key, _green_entry(key))
            runner = _Runner()
            code, note = cut.cached_run_check(self.root, "fake-gate",
                                              self.cmd, self.store,
                                              POLICY_SHA256, runner=runner)
            self.assertEqual(code, 0)
            self.assertIsNone(note)
            self.assertEqual(runner.calls[0], self.cmd)

    def test_missing_keyed_input_is_miss_never_hit(self):
        _write_ledger(self.ledger, "fake-gate")
        with mock.patch.dict(os.environ, self._env()):
            key = cut.verdict_key(self.root, "fake-gate", self.cmd,
                                  POLICY_SHA256)
            cut.cache_record(self.store, key, _green_entry(key))
            os.remove(os.path.join(self.root, "scripts", "fake_gate.py"))
            runner = _Runner()
            code, note = cut.cached_run_check(self.root, "fake-gate",
                                              self.cmd, self.store,
                                              POLICY_SHA256, runner=runner)
            self.assertEqual(code, 0)
            self.assertIsNone(note)
            self.assertEqual(runner.calls[0], self.cmd)

    def test_hostile_input_refused(self):
        with self.assertRaises(ValueError):
            cut.verdict_key(None, "n", ["x"], None)
        with self.assertRaises(ValueError):
            cut.verdict_key("/tmp", "n", "not-a-list", None)
        with self.assertRaises(ValueError):
            cut.verdict_key("/tmp", "n", ["x", 5], None)
        with self.assertRaises(ValueError):
            cut.verdict_key("/tmp", "n", ["x"], 12345)
        self.assertIsNone(cut.cache_lookup(None, "a" * 64))
        self.assertIsNone(cut.cache_lookup("/tmp", None))
        bad = _green_entry("a" * 64)
        bad["exit_code"] = True
        with self.assertRaises(ValueError):
            cut.cache_record("/tmp", "a" * 64, bad)
        bad = _green_entry("a" * 64)
        bad["seconds"] = float("nan")
        with self.assertRaises(ValueError):
            cut.cache_record("/tmp", "a" * 64, bad)
        with self.assertRaises(ValueError):
            cut.cached_run_check("/tmp", "n", ["x"], None, None)


class TestDedupe(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="c04b-dedupe-")
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)

    def test_rows_preserved(self):
        rows = [("claim-a", "a.md", "scripts/test_one.py"),
                ("claim-b", "b.md", "scripts/test_one.py"),
                ("claim-c", "c.md", "scripts.test_two")]
        runner = _Runner()
        ok, lines = cut.note_suite_step(self.tmp, "1.0.0", (rows, None),
                                        runner=runner)
        self.assertTrue(ok)
        self.assertEqual(len(runner.calls), 2)
        row_lines = [line for line in lines if line.startswith("row ")]
        self.assertEqual(len(row_lines), 3)
        for claim in ("claim-a", "claim-b", "claim-c"):
            self.assertTrue(any(claim in line for line in row_lines))

    def test_no_suite_is_no_data_never_skip(self):
        rows = [("claim-a", "a.md", None)]
        runner = _Runner()
        ok, lines = cut.note_suite_step(self.tmp, "1.0.0", (rows, None),
                                        runner=runner)
        self.assertFalse(ok)
        self.assertEqual(runner.calls, [])
        self.assertTrue(lines)

    def test_hostile_report_refused(self):
        for bad in (None, "not-a-pair", {}, (None, None), ([], None)):
            ok, lines = cut.note_suite_step(self.tmp, "1.0.0", bad)
            self.assertFalse(ok)
            self.assertTrue(lines)

    def test_problem_and_bad_row_refused(self):
        ok, _lines = cut.note_suite_step(self.tmp, "1.0.0",
                                         ([("c", "s", "x.py")], "boom"))
        self.assertFalse(ok)
        ok, _lines = cut.note_suite_step(self.tmp, "1.0.0", ([("c", "s")], None))
        self.assertFalse(ok)


class TestResume(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="c04b-resume-")
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)
        self.root = os.path.join(self.tmp, "root")
        notes_dir = os.path.join(self.root, "docs", "releases")
        os.makedirs(notes_dir)
        with open(os.path.join(notes_dir, "1.0.0.md"), "w",
                  encoding="utf-8") as handle:
            handle.write("release notes\n")
        self.head = "e" * 40
        self.notes_sha256 = hashlib.sha256(b"release notes\n").hexdigest()

    def test_dirty_refuses(self):
        runner = _GitRunner(head=self.head,
                            dirty=[" M docs/releases/1.0.0.md\n"])
        with mock.patch.object(cut, "_run", runner):
            ok, why = cut.resume_guard(self.root, self.head,
                                       self.notes_sha256)
        self.assertFalse(ok)
        self.assertIn("dirty", why)

    def test_clean_matching_head_allows(self):
        runner = _GitRunner(head=self.head)
        with mock.patch.object(cut, "_run", runner):
            ok, why = cut.resume_guard(self.root, self.head,
                                       self.notes_sha256)
        self.assertTrue(ok, why)
        self.assertIn("resume allowed", why)

    def test_moved_head_refuses(self):
        runner = _GitRunner(head="f" * 40)
        with mock.patch.object(cut, "_run", runner):
            ok, why = cut.resume_guard(self.root, self.head,
                                       self.notes_sha256)
        self.assertFalse(ok)
        self.assertIn("not the checked commit", why)

    def test_hostile_input_refused(self):
        for bad_root in (None, 7, b"x", ""):
            ok, _why = cut.resume_guard(bad_root, self.head, None)
            self.assertFalse(ok)
        ok, _why = cut.resume_guard(self.root, None, None)
        self.assertFalse(ok)
        ok, _why = cut.resume_guard(self.root, self.head, 12345)
        self.assertFalse(ok)


if __name__ == "__main__":
    unittest.main()

#!/usr/bin/env python3
"""Tests for scripts/escape_measure.py (BG0.a). Run: python3 -B scripts/test_escape_measure.py

The measured outcomes of the hooks are never asserted here: a later guard may change them without editing this file.
What is asserted is the harness: the classification rules, the three controls, the row set, the sentinel reset, the
sandbox, and that a missing or garbage hook reads NO-DATA. Each test builds its own HOME and world in a temp folder.
"""
import hashlib
import io
import json
import os
import re
import shutil
import sys
import tempfile
import unittest
from contextlib import redirect_stdout
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import escape_measure as em  # noqa: E402

HEX64 = re.compile(r"^[0-9a-f]{64}$")
ORIG_HASH = hashlib.sha256(b"orig").hexdigest()
EXPECTED_IDS = set(
    ["H1-%s-%s" % (c, s) for c in ("write", "patch", "bash") for s in em.SHAPES]
    + ["H1-malformed-type-dotdot", "H1-malformed-json-dotdot", "H1-malformed-type-dotdot-advisory"]
    + ["H2-%s-%s" % (c, s) for c in ("write", "patch") for s in em.SHAPES]
    + ["H2-malformed-type-dotdot", "H2-malformed-json-dotdot"]
    + ["H3-bash-%s" % s for s in em.SHAPES] + ["E4-declare-%s" % s for s in em.SHAPES] + ["REG-write-dotdot"]
    + ["claude", "codex", "cursor", "antigravity"])
_SAVED = {}
_TMP = []


def setUpModule():
    home = tempfile.mkdtemp(prefix="bg0-test-home-")
    _TMP.append(home)
    _SAVED["HOME"] = os.environ.get("HOME")
    os.environ["HOME"] = home


def tearDownModule():
    if _SAVED.get("HOME") is None:
        os.environ.pop("HOME", None)
    else:
        os.environ["HOME"] = _SAVED["HOME"]
    for d in _TMP:
        shutil.rmtree(d, ignore_errors=True)


def new_world():
    parent = os.path.realpath(tempfile.mkdtemp(prefix="bg0-test-"))
    _TMP.append(parent)
    return em.make_world(parent)


class TableBase(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.world = new_world()
        cls.rows = em.run_all(cls.world)
        cls.by_id = {r["id"]: r for r in cls.rows}


class RuleTable(unittest.TestCase):
    """REQ-BG0-01: one input per classification rule."""

    def test_rule_table(self):
        a, b = "a" * 64, "b" * 64
        self.assertEqual(em.classify("error", "", a, a, False), "NO-DATA")
        self.assertEqual(em.classify("allow", "", None, a, True), "NO-DATA")
        self.assertEqual(em.classify("allow", "", a, None, True), "NO-DATA")
        self.assertEqual(em.classify("deny", "why", a, a, False), "denied")
        self.assertEqual(em.classify("deny", "why", a, b, False), "NO-DATA")
        self.assertEqual(em.classify("allow", "a note", a, b, True), "advised")
        self.assertEqual(em.classify("allow", "", a, b, True), "allowed")
        self.assertEqual(em.classify("allow", "", a, a, True), "allowed")
        self.assertEqual(em.classify("allow", "", a, a, False), "NO-DATA")
        self.assertEqual(em.classify("ask", "", a, a, False), "NO-DATA")

    def test_shape_outside_shapes_raises(self):
        world = new_world()
        with self.assertRaises(ValueError):
            em.run_probe(world, "H1", "write", "../x")
        with self.assertRaises(ValueError):
            em.run_declare_probe(world, "tilde")

    def test_empty_world_reads_no_data(self):
        world = new_world()
        os.remove(world["sentinel"])
        os.rmdir(os.path.dirname(world["sentinel"]))
        self.assertEqual(em.run_probe(world, "H1", "write", "abs")["outcome"], "NO-DATA")


class Controls(unittest.TestCase):
    """REQ-BG0-02: the harness sees a write (C0, C1) and a refusal (C2)."""

    def test_controls(self):
        world = new_world()
        c0, c1, c2 = em._controls(world)
        self.assertTrue(HEX64.match(c0["hash_before"] or "") and HEX64.match(c0["hash_after"] or ""))
        self.assertNotEqual(c0["hash_before"], c0["hash_after"], "C0: an outside write must change the hash")
        self.assertTrue(c0["holds"])
        self.assertEqual(c1["decision"], "allow", c1["note"])
        self.assertTrue(os.path.isfile(os.path.join(world["project"], em.CLAIMED)), "C1: the claimed write ran")
        self.assertTrue(c1["holds"])
        self.assertEqual(c2["decision"], "deny", c2["note"])
        self.assertFalse(os.path.exists(os.path.join(world["project"], em.UNCLAIMED)), "C2: the refused write ran")
        self.assertTrue(c2["holds"])


class Table(TableBase):
    def test_completeness(self):
        """REQ-BG0-03: every row, unique ids, a closed outcome, a host key on the adapter rows."""
        ids = [r["id"] for r in self.rows]
        self.assertEqual(len(ids), len(set(ids)))
        self.assertEqual(set(ids), EXPECTED_IDS)
        self.assertEqual(len(ids), 31)
        for r in self.rows:
            self.assertIn(r["outcome"], em.OUTCOMES, r["id"])
        for host in ("claude", "codex", "cursor", "antigravity"):
            self.assertEqual(self.by_id[host]["host"], host)

    def test_reset(self):
        """REQ-BG0-04: every effect row has two 64 hex hashes and began from the same reset sentinel."""
        effect_rows = [r for r in self.rows if r["hook"] in ("H1", "H2", "H3", "REG")]
        self.assertEqual(len(effect_rows), 23 + 1 + 2)   # probes, REG, claude and codex
        for r in effect_rows:
            self.assertTrue(HEX64.match(r["hash_before"] or ""), r["id"])
            self.assertTrue(HEX64.match(r["hash_after"] or ""), r["id"])
            self.assertEqual(r["hash_before"], ORIG_HASH, r["id"])

    def test_adapter_no_data(self):
        """REQ-BG0-05: cursor and antigravity are NO-DATA with the reason."""
        for host in ("cursor", "antigravity"):
            self.assertEqual(self.by_id[host]["outcome"], "NO-DATA")
            self.assertEqual(self.by_id[host]["note"], em.ADAPTER_GAP)

    def test_ran(self):
        """REQ-BG0-05: on a machine with the hook files and bash no other row is NO-DATA."""
        if shutil.which("bash") is None or not all(self.world["hooks"].values()):
            self.skipTest("no bash or a hook file is missing here")
        bad = [(r["id"], r["note"]) for r in self.rows if r["outcome"] == "NO-DATA"
               and r["id"] not in ("cursor", "antigravity")]
        self.assertEqual(bad, [])
        for r in self.rows:
            if r["hook"] in ("H1", "H2", "H3"):
                self.assertIsInstance(r["raw_payload"], (dict, str))
                self.assertIn("stdout", r["raw_answer"])

    def test_sandbox(self):
        """REQ-BG0-06: every hook ran with the sandbox HOME."""
        for r in self.rows:
            if r["hook"] in ("H1", "H2", "H3"):
                self.assertEqual(r["hook_home"], self.world["home"], r["id"])
        self.assertEqual(self.by_id["REG-write-dotdot"]["hook_home"], self.world["reg_home"])
        self.assertNotEqual(self.world["home"], self.world["reg_home"])

    def test_sandbox_env_seen_by_a_hook(self):
        """REQ-BG0-06: what a hook process actually receives: the sandbox HOME and no ambient BM_ or BROTHERSBE_."""
        stub = os.path.join(self.world["base"], "env_stub.py")
        with open(stub, "w") as fh:
            fh.write("import json, os, sys\nsys.stderr.write(json.dumps(dict((k, v) for k, v in os.environ.items() "
                     "if k in ('HOME', 'CLAUDE_CONFIG_DIR') or k.startswith(('BM_', 'BROTHERSBE_')))) + '\\n')\n")
        with mock.patch.dict(os.environ, {"BM_FENCE_BATTERY": "off", "BROTHERSBE_FENCE_HOOK_OFF": "1"}):
            env = em._hook_env(self.world, self.world["home"], "enforced")
            ans = em.hook_decision(stub, {"tool_name": "Write"}, env, self.world["project"])
        seen = json.loads(ans["stderr"])
        self.assertEqual(seen.get("HOME"), self.world["home"])
        self.assertEqual(seen.get("CLAUDE_CONFIG_DIR"), os.path.join(self.world["home"], ".claude"))
        self.assertNotIn("BM_FENCE_BATTERY", seen)
        self.assertNotIn("BROTHERSBE_FENCE_HOOK_OFF", seen)

    def test_not_deferred(self):
        """REQ-BG0-06: no BrotherSBE row deferred, even right after the registration row ran."""
        for r in self.rows:
            if r["hook"] in ("H2", "H3"):
                self.assertNotIn("DEFERRING", json.dumps(r["raw_answer"]), r["id"])
        world = new_world()
        em.run_registration_probe(world)
        self.assertFalse(os.path.exists(os.path.join(world["home"], ".claude", "settings.json")))
        row = em.run_probe(world, "H2", "write", "dotdot")
        self.assertNotIn("DEFERRING", json.dumps(row["raw_answer"]) + row["note"])

    def test_same_rows_twice(self):
        again = em.run_probe(self.world, "H1", "write", "dotdot")
        self.assertEqual(again["outcome"], self.by_id["H1-write-dotdot"]["outcome"])
        self.assertEqual(again["hash_before"], ORIG_HASH)


class GarbageHook(unittest.TestCase):
    """REQ-BG0-07: a hook that does not exist, or prints garbage, is NO-DATA and never allowed."""

    def setUp(self):
        self.dir = os.path.realpath(tempfile.mkdtemp(prefix="bg0-garbage-"))
        _TMP.append(self.dir)
        self.env = {"PATH": os.environ.get("PATH", ""), "HOME": self.dir}

    def script(self, name, body):
        path = os.path.join(self.dir, name)
        with open(path, "w") as fh:
            fh.write(body)
        return path

    def outcome(self, path):
        ans = em.hook_decision(path, {"tool_name": "Write"}, self.env, self.dir)
        return ans["decision"], em.classify(ans["decision"], ans["note"], "a" * 64, "b" * 64, True)

    def test_bogus_path(self):
        self.assertEqual(self.outcome(os.path.join(self.dir, "no_such_hook.py")), ("error", "NO-DATA"))
        self.assertEqual(self.outcome(None), ("error", "NO-DATA"))

    def test_garbage_hook(self):
        self.assertEqual(self.outcome(self.script("text.py", "print('hello')\n")), ("error", "NO-DATA"))
        self.assertEqual(self.outcome(self.script("empty_obj.py", "print('{}')\n")), ("error", "NO-DATA"))
        self.assertEqual(self.outcome(self.script("crash.py", "raise SystemExit(3)\n")), ("error", "NO-DATA"))
        deny = ('import json\nprint(json.dumps({"hookSpecificOutput": {"permissionDecision": "deny", '
                '"permissionDecisionReason": "no"}}))\n')
        self.assertEqual(self.outcome(self.script("deny.py", deny))[0], "deny")
        self.assertEqual(self.outcome(self.script("quiet.py", "pass\n")), ("allow", "allowed"))


class ResultFile(TableBase):
    """REQ-BG0-08: the result file carries every row, raw payload included."""

    def test_file(self):
        path = os.path.join(self.world["base"], "table.json")
        em.write_table(self.rows, path)
        with open(path) as fh:
            back = json.load(fh)
        self.assertEqual([r["id"] for r in back], [r["id"] for r in self.rows])
        for got, want in zip(back, self.rows):
            self.assertEqual(got["raw_payload"], want["raw_payload"])
            self.assertEqual(got["outcome"], want["outcome"])

    def test_main_prints_and_writes(self):
        path = os.path.join(self.world["base"], "main.json")
        buf = io.StringIO()
        with mock.patch.dict(os.environ, {"ESCAPE_MEASURE_OUT": path}), redirect_stdout(buf):
            self.assertEqual(em.main([]), 0)
        lines = buf.getvalue().splitlines()
        self.assertEqual(len([ln for ln in lines if ln.startswith("ESCAPE ")]), 31)
        self.assertEqual(len([ln for ln in lines if ln.startswith("CONTROL ") and "holds=True" in ln]), 3)
        self.assertTrue(lines[-1].startswith("COUNT rows=31"))
        with open(path) as fh:
            back = json.load(fh)
        self.assertEqual({r["id"]: r["outcome"] for r in back}, {r["id"]: r["outcome"] for r in self.rows})


class Hostile(unittest.TestCase):
    """Every public function, each hostile value in every positional parameter: returns or refuses."""

    VALUES = (None, 0, True, -1, float("nan"), "", "x", b"x", [], ["x"], {}, {"a": 1}, (), {1, 2}, object())
    FUNCS = {em.classify: 5, em.hook_decision: 4, em.make_world: 1, em.run_probe: 5, em.run_declare_probe: 2,
             em.run_registration_probe: 1, em.run_adapter_rows: 1, em.run_all: 1, em.write_table: 2, em.main: 1}

    def test_hostile_inputs(self):
        for fn, arity in self.FUNCS.items():
            for v in self.VALUES:
                if fn is em.main and (v is None or v == []):
                    continue   # main([]) is the real measurement (test_main_prints_and_writes); None: the next test
                try:
                    with redirect_stdout(io.StringIO()):
                        fn(*([v] * arity))
                except (ValueError, LookupError, SystemExit):
                    pass

    def test_none_argv_reads_sys_argv(self):
        with mock.patch.object(sys, "argv", ["escape_measure.py", "--bogus"]):
            with self.assertRaises(SystemExit):
                em.main(None)
        for bad in (0, b"x", object(), ("x",), [1]):
            with self.assertRaises(SystemExit):
                em.main(bad)


if __name__ == "__main__":
    unittest.main()

#!/usr/bin/env python3
"""GL1.a: both gate ledger writers take the cut id from gate_ledger.cut_id_or_none, so a row outside a cut appends
with NO_CUT instead of being refused ("cut_id must be 32 hex", 62 of 62 rows of the 1.1.5 whole gate).

HOW. test_a_row_outside_a_cut_appends runs the per row python block sliced verbatim from scripts/required_fast.sh
(its literal heredoc anchors) as a child process, with no CUT_ID, against a fixture ledger in a temporary directory:
the entry point the real gate runs, not the helper alone. The helper cases fire hostile inputs at cut_id_or_none.
test_both_writers_use_the_helper replaces the helper with a sentinel and checks that BOTH writers carry its answer.

Run: python3 -B scripts/test_gate_ledger_cut_id.py
"""
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, HERE)
import gate_ledger  # noqa: E402

with open(os.path.join(HERE, "required_fast.sh"), encoding="utf-8") as _f:
    _SOURCE = _f.read()
_OPEN = "<<'PY' 2>&1 >/dev/null\n"
_START = _SOURCE.index(_OPEN, _SOURCE.index('ledger_note="$(WORKTREE_KEY=')) + len(_OPEN)
ROW_BLOCK = _SOURCE[_START:_SOURCE.index("\nPY\n", _START) + 1]
HEX = "0123456789abcdef" * 2


class CutIdOrNone(unittest.TestCase):
    def test_a_cut_id_is_kept(self):
        self.assertEqual(gate_ledger.cut_id_or_none({"CUT_ID": HEX}), HEX)

    def test_a_bad_cut_id_reads_no_cut(self):
        for bad in (None, "", HEX[:31], HEX + "a", HEX.upper(), "g" * 32, HEX[:31] + "\n",
                    HEX[:31] + "\0", HEX + "\n", 12345):
            env = {} if bad is None else {"CUT_ID": bad}
            self.assertEqual(gate_ledger.cut_id_or_none(env), gate_ledger.NO_CUT, repr(bad))

    def test_a_non_mapping_env_raises(self):
        for env in (None, [("CUT_ID", HEX)], "CUT_ID=" + HEX):
            with self.assertRaises(ValueError, msg=repr(env)):
                gate_ledger.cut_id_or_none(env)

    def test_the_real_environ_is_accepted(self):
        with mock.patch.dict(os.environ, {"CUT_ID": HEX}):
            self.assertEqual(gate_ledger.cut_id_or_none(os.environ), HEX)


class RowWriter(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="gl1a-")
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.ledger = os.path.join(self.tmp, "logs", "gate-ledger.jsonl")
        self.block = os.path.join(self.tmp, "row_block.py")
        with open(self.block, "w", encoding="utf-8") as f:
            f.write(ROW_BLOCK)

    def run_row(self, name, cut_id=None):
        env = {k: v for k, v in os.environ.items() if not k.startswith("CUT_")}
        env["GATE_LEDGER_PATH"] = self.ledger
        env["PYTHONDONTWRITEBYTECODE"] = "1"
        if cut_id is not None:
            env["CUT_ID"] = cut_id
        proc = subprocess.run([sys.executable, self.block, name, "0", "1000000000", "2000000000", "1.0"],
                              cwd=ROOT, env=env, capture_output=True, text=True, timeout=60)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        return proc.stderr

    def rows(self):
        with open(self.ledger, encoding="utf-8") as f:
            return [json.loads(line) for line in f if line.strip()]

    def test_a_row_outside_a_cut_appends(self):
        note = self.run_row("a-row")
        self.assertEqual(note, "", "the row was refused: %s" % note)
        rows = self.rows()
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["cut_id"], gate_ledger.NO_CUT)
        self.assertTrue(gate_ledger.verify_chain(self.ledger))

    def test_three_rows_keep_the_chain_and_a_cut_id_rides_through(self):
        self.assertEqual(self.run_row("a", cut_id=""), "")
        self.assertEqual(self.run_row("b", cut_id=HEX), "")
        self.assertEqual(self.run_row("b", cut_id=HEX[:31]), "")   # the same row again: a new row, never a skip
        self.assertEqual([r["cut_id"] for r in self.rows()], [gate_ledger.NO_CUT, HEX, gate_ledger.NO_CUT])
        self.assertTrue(gate_ledger.verify_chain(self.ledger))


class BothWriters(unittest.TestCase):
    def test_both_writers_use_the_helper(self):
        sentinel = "f" * 32
        # The per row writer: its heredoc must hand os.environ to the helper, and read CUT_ID nowhere else.
        self.assertIn("gate_ledger.cut_id_or_none(os.environ)", ROW_BLOCK)
        self.assertNotIn("CUT_ID", ROW_BLOCK)
        # The whole gate writer, at its entry point: the record carries whatever the helper answers.
        appended = []
        with mock.patch.object(gate_ledger, "cut_id_or_none", lambda env: sentinel), \
                mock.patch.object(gate_ledger, "tree_snapshot", lambda repo: ("a" * 40, "")), \
                mock.patch.object(gate_ledger, "append_ledger", lambda path, rec: appended.append(rec)), \
                mock.patch.dict(os.environ, {"CUT_ID": HEX}):
            ok, why = gate_ledger.record_whole_gate("unused", ROOT, "a\t0\n", "a" * 40, "0", 1.0)
        self.assertTrue(ok, why)
        self.assertEqual([r["cut_id"] for r in appended], [sentinel])


if __name__ == "__main__":
    unittest.main(verbosity=2)

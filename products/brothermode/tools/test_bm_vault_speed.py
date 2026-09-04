#!/usr/bin/env python3
"""Tests for VB5-03 (the speed pack): content-hash incremental indexing, lexical-first routing,
and the warm-embedder query cache.

Two test styles, chosen for what each part actually needs:

  ContentHashIncrementalIndexing drives the real CLI as a subprocess (the same pattern
  test_bm_vault.py uses), because the behavior under test IS the `index` subcommand's on-disk
  effect across repeated runs, and a subprocess is the only way to see what a second, independent
  process actually persists.

  RoutingAndQueryCache imports bm_vault.py by path and calls `_search` directly against an
  in-memory sqlite connection, with `_embed_texts` monkeypatched to a call-counting fake. This
  never spawns the real embedder (7-9 SECONDS per call, measured, and this machine may not even
  have the venv), and call counting is the only way to prove a cache hit skipped it rather than
  merely running fast.

Temp fixtures only; the real Kay Vault is never touched by this file.

Run: python3 tools/test_bm_vault_speed.py      (unittest output, exit 0 or 1)
"""
import hashlib
import importlib.util
import os
import shutil
import sqlite3
import subprocess
import sys
import tempfile
import time
import unittest

# E100: one sandbox for every temp tree this process makes, removed at exit.
import os as _e100_os, sys as _e100_sys  # noqa: E402
_e100_sys.path.append(_e100_os.path.join(
    _e100_os.path.dirname(_e100_os.path.abspath(__file__)), '../../../scripts'))
try:  # noqa: E402
    import tmp_sandbox as _e100_tmp
    _e100_tmp.install()
except ImportError:
    # A packager (scripts/export_public.py, make_benchmark_bundle.py)
    # can copy this test without scripts/tmp_sandbox.py beside it. Say
    # so rather than dying: the sandbox is hygiene, not the subject.
    _e100_sys.stderr.write(
        "tmp_sandbox absent: %s leaves its temp trees behind\n"
        % _e100_os.path.basename(__file__))

HERE = os.path.dirname(os.path.abspath(__file__))
TOOL = os.path.join(HERE, "bm_vault.py")

_spec = importlib.util.spec_from_file_location("bm_vault", TOOL)
bmv = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(bmv)


def run(argv, env):
    p = subprocess.run([sys.executable, TOOL] + argv, env=env,
                       stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    return p.returncode, (p.stdout + p.stderr).decode("utf-8", "replace")


NOTE_A = """---
name: note-a
description: first fixture note
type: project
---
Body of note A, mentioning zephyrine marmalade lighthouse cartography once.
"""

NOTE_B = """---
name: note-b
description: second fixture note
type: project
---
Body of note B, mentioning a completely different topic entirely.
"""


class ContentHashIncrementalIndexing(unittest.TestCase):
    """touch a note (mtime bumped, content identical): only that note is TOUCHED, never
    RE-EMBEDDED or rebuilt as anchors/links/fts, and its sibling is not reprocessed at all.
    corrupt the stored hash: the note is treated as changed and fully re-indexed, because the
    gate must never trust a hash it cannot prove, even when nothing about the file itself moved.
    """

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="bm-vault-speed-")
        self.vault = os.path.join(self.tmp, "vault")
        os.makedirs(self.vault)
        self.path_a = os.path.join(self.vault, "a.md")
        self.path_b = os.path.join(self.vault, "b.md")
        with open(self.path_a, "w") as f:
            f.write(NOTE_A)
        with open(self.path_b, "w") as f:
            f.write(NOTE_B)
        os.makedirs(os.path.join(self.tmp, ".claude"))
        self.env = dict(os.environ)
        self.env["HOME"] = self.tmp
        self.env["BROTHERMODE_ROOT"] = self.tmp  # no store here: NO-DATA correction line, fine
        self.index_path = os.path.join(self.tmp, ".claude", "bm_vault_index.sqlite3")

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _index(self):
        rc, out = run(["index", "--vault", self.vault], self.env)
        self.assertEqual(rc, 0, out)
        return out

    def _row(self, path):
        con = sqlite3.connect(self.index_path)
        con.row_factory = sqlite3.Row
        try:
            return con.execute("SELECT id, mtime, content_hash FROM notes WHERE path=?",
                               (path,)).fetchone()
        finally:
            con.close()

    def _corrupt_hash(self, path):
        con = sqlite3.connect(self.index_path)
        try:
            con.execute("UPDATE notes SET content_hash='deadbeef' WHERE path=?", (path,))
            con.commit()
        finally:
            con.close()

    def test_01_first_index_is_two_new(self):
        out = self._index()
        self.assertIn("2 new", out, out)

    def test_02_true_no_op_reindex_touches_nothing(self):
        self._index()
        out = self._index()
        self.assertIn("0 new, 0 refreshed", out, out)
        self.assertIn("0 touched", out, out)

    def test_03_touch_without_content_change_is_touched_not_refreshed(self):
        self._index()
        row_a_before = self._row(self.path_a)
        row_b_before = self._row(self.path_b)
        future = time.time() + 5
        os.utime(self.path_a, (future, future))
        out = self._index()
        self.assertIn("0 new, 0 refreshed, 0 gone, 1 touched", out, out)
        row_a_after = self._row(self.path_a)
        row_b_after = self._row(self.path_b)
        # A's mtime moved but its stored content_hash is IDENTICAL (same bytes, same hash) --
        # this is the whole point of the gate, proven by reading the hash back rather than
        # trusting the summary line alone.
        self.assertAlmostEqual(row_a_after["mtime"], future, delta=0.01)
        self.assertEqual(row_a_before["content_hash"], row_a_after["content_hash"])
        # B was never touched: same row, unchanged mtime.
        self.assertEqual(row_b_before["mtime"], row_b_after["mtime"])
        self.assertEqual(row_b_before["content_hash"], row_b_after["content_hash"])

    def test_04_corrupted_stored_hash_forces_a_real_reindex(self):
        self._index()
        self._corrupt_hash(self.path_b)
        future = time.time() + 10
        os.utime(self.path_b, (future, future))  # mtime must move too: the cheap pre-filter in
        # cmd_index never even reads a file whose mtime is unchanged, corrupted hash or not --
        # the hash gate is the second, exact check behind that first cheap one, not a
        # replacement for it (see the docstring on _upsert_note).
        out = self._index()
        self.assertIn("0 new, 1 refreshed", out, out)
        self.assertIn("0 touched", out, out)
        row_b = self._row(self.path_b)
        self.assertNotEqual(row_b["content_hash"], "deadbeef")


class RoutingAndQueryCache(unittest.TestCase):
    """Both routing directions, and the query-cache hit, all against an in-memory index and a
    counting fake in place of the real (7-9s per call) embedder subprocess."""

    def setUp(self):
        self.con = sqlite3.connect(":memory:")
        self.con.row_factory = sqlite3.Row
        bmv._schema(self.con)
        bmv._upsert_note(self.con, "/fixture/relevant.md", "relevant note", "",
                         "vault", "lesson", 1.0,
                         "Body about quokka platypus wombat narwhal, four rare words together.")
        bmv._upsert_note(self.con, "/fixture/other.md", "other note", "",
                         "vault", "lesson", 1.0,
                         "Body about something entirely unconnected, ordinary words only.")
        self.calls = []
        self._real_embed_texts = bmv._embed_texts

        def fake_embed(pairs, query=False):
            self.calls.append((list(pairs), query))
            return {i: [0.2, 0.4, 0.6, 0.8] for i, _ in pairs}
        bmv._embed_texts = fake_embed

    def tearDown(self):
        bmv._embed_texts = self._real_embed_texts
        self.con.close()

    def test_01_lexical_satisfies_dense_never_called(self):
        explain = []
        bmv._search(self.con, text="quokka platypus wombat narwhal", limit=1, explain=explain)
        self.assertEqual(self.calls, [], "the fake embedder was called although lexical alone "
                                         "already met the requested limit:\n%s" % explain)
        self.assertTrue(any("dense: skipped" in e for e in explain), explain)

    def test_02_lexical_empty_falls_through_to_dense(self):
        explain = []
        fused, why = bmv._search(self.con, text="zzqxx flibbertigibbet unrelated nonsense",
                                 limit=3, explain=explain)
        self.assertEqual(len(self.calls), 1, "an empty lexical signal must still reach the "
                                             "dense stage (never a silent quality loss):\n%s"
                                             % explain)
        self.assertTrue(any("dense: loading" in e for e in explain), explain)

    def test_03_repeat_query_hits_the_cache_not_the_embedder(self):
        explain1 = []
        bmv._search(self.con, text="zzqxx flibbertigibbet unrelated nonsense", limit=3,
                   explain=explain1)
        self.assertEqual(len(self.calls), 1, explain1)
        explain2 = []
        bmv._search(self.con, text="zzqxx flibbertigibbet unrelated nonsense", limit=3,
                   explain=explain2)
        self.assertEqual(len(self.calls), 1,
                         "a second call with the IDENTICAL query text invoked the embedder "
                         "again; the query cache should have answered it:\n%s" % explain2)
        self.assertTrue(any("dense: query cache hit" in e for e in explain2), explain2)

    def test_04_a_different_query_is_a_cache_miss(self):
        bmv._search(self.con, text="zzqxx flibbertigibbet unrelated nonsense", limit=3,
                   explain=[])
        self.assertEqual(len(self.calls), 1)
        bmv._search(self.con, text="a totally different nonsense phrase here", limit=3,
                   explain=[])
        self.assertEqual(len(self.calls), 2, "a different query text must not reuse another "
                                             "query's cached vector")


if __name__ == "__main__":
    unittest.main()

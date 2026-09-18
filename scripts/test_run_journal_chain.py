#!/usr/bin/env python3
"""Tests for run_journal_chain.py (DOM-10.05). stdlib unittest, temp dirs,
no network. Every case asserts the verdict and, for a break, the index
where it was found -- per the unit brief, not just "it failed"."""
import json
import os
import shutil
import tempfile
import unittest

import run_journal_chain as rjc


class RunJournalChainTest(unittest.TestCase):

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="run_journal_chain_test_")
        self.path = os.path.join(self.tmp, "journal.jsonl")

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _append(self, n, action="run"):
        for i in range(n):
            rjc.append(self.path, actor="a%d" % i, host="h", unit="U1",
                      action=action, result="ok", timestamp="t%d" % i)

    def _lines(self):
        with open(self.path, encoding="utf-8") as fh:
            return [l for l in fh.read().split("\n") if l]

    def _rewrite(self, lines):
        with open(self.path, "w", encoding="utf-8") as fh:
            fh.write("\n".join(lines) + "\n")

    # ---- empty journal ----

    def test_empty_journal_no_file(self):
        result = rjc.verify(self.path)
        self.assertEqual(result["verdict"], "clean")
        self.assertEqual(result["count"], 0)
        self.assertEqual(result["final_hash"], rjc.GENESIS_HASH)

    def test_empty_journal_zero_byte_file(self):
        open(self.path, "w").close()
        result = rjc.verify(self.path)
        self.assertEqual(result["verdict"], "clean")
        self.assertEqual(result["count"], 0)

    # ---- single event ----

    def test_single_event_clean(self):
        self._append(1)
        result = rjc.verify(self.path)
        self.assertEqual(result["verdict"], "clean")
        self.assertEqual(result["count"], 1)
        self.assertNotEqual(result["final_hash"], rjc.GENESIS_HASH)

    def test_single_event_receipt_matches_final_hash(self):
        self._append(1)
        h, problem = rjc.receipt(self.path)
        self.assertEqual(problem, "")
        self.assertEqual(h, rjc.verify(self.path)["final_hash"])

    # ---- append chains correctly ----

    def test_append_links_prev_hash(self):
        self._append(3)
        lines = [json.loads(l) for l in self._lines()]
        self.assertEqual(lines[0]["prev_hash"], rjc.GENESIS_HASH)
        self.assertEqual(lines[1]["prev_hash"], lines[0]["hash"])
        self.assertEqual(lines[2]["prev_hash"], lines[1]["hash"])
        result = rjc.verify(self.path)
        self.assertEqual(result["verdict"], "clean")
        self.assertEqual(result["count"], 3)
        self.assertEqual(result["final_hash"], lines[2]["hash"])

    def test_append_is_atomic_write_not_in_place(self):
        # _atomic_write must go through a temp file + os.replace: prove no
        # stray temp file survives a normal append.
        self._append(2)
        leftovers = [f for f in os.listdir(self.tmp)
                    if f.startswith(".run_journal_chain-")]
        self.assertEqual(leftovers, [])

    def test_append_refuses_on_unreadable_last_line(self):
        self._append(1)
        with open(self.path, "a", encoding="utf-8") as fh:
            fh.write('{"prev_hash": "bad", "truncated\n')
        with self.assertRaises(rjc.JournalError):
            rjc.append(self.path, actor="x", host="h", unit="U1",
                       action="run", result="ok", timestamp="t9")

    # ---- truncated last line (crash mid-write) ----

    def test_truncated_last_line(self):
        self._append(2)
        lines = self._lines()
        lines[-1] = lines[-1][: len(lines[-1]) // 2]  # simulate a crash mid-write
        self._rewrite(lines)
        result = rjc.verify(self.path)
        self.assertEqual(result["verdict"], "broken")
        self.assertEqual(result["index"], 1)

    # ---- modified middle event ----

    def test_modified_middle_event(self):
        self._append(3)
        lines = [json.loads(l) for l in self._lines()]
        lines[1]["result"] = "tampered"  # hash field left stale on purpose
        self._rewrite([json.dumps(e, sort_keys=True) for e in lines])
        result = rjc.verify(self.path)
        self.assertEqual(result["verdict"], "broken")
        self.assertEqual(result["index"], 1)

    # ---- two events swapped ----

    def test_two_events_swapped(self):
        self._append(3)
        lines = [json.loads(l) for l in self._lines()]
        lines[0], lines[1] = lines[1], lines[0]
        self._rewrite([json.dumps(e, sort_keys=True) for e in lines])
        result = rjc.verify(self.path)
        self.assertEqual(result["verdict"], "broken")
        self.assertEqual(result["index"], 0)

    # ---- an event removed ----

    def test_event_removed(self):
        self._append(3)
        lines = [json.loads(l) for l in self._lines()]
        del lines[1]
        self._rewrite([json.dumps(e, sort_keys=True) for e in lines])
        result = rjc.verify(self.path)
        self.assertEqual(result["verdict"], "broken")
        self.assertEqual(result["index"], 1)

    # ---- a duplicated event ----

    def test_event_duplicated(self):
        self._append(3)
        lines = [json.loads(l) for l in self._lines()]
        lines.insert(1, dict(lines[1]))  # duplicate event at index 1
        self._rewrite([json.dumps(e, sort_keys=True) for e in lines])
        result = rjc.verify(self.path)
        self.assertEqual(result["verdict"], "broken")
        self.assertEqual(result["index"], 2)

    def test_reordered_events_relinked_by_hand_still_detected(self):
        # The realistic attack: swap two events AND patch their prev_hash
        # fields to keep the chain structurally consistent, exactly what
        # someone rewriting the file would do rather than leaving an
        # obviously dangling pointer. This is the case that only catches
        # anything because the hash covers prev_hash (see _event_hash's
        # docstring and this module's mutation proof); a hash that ignored
        # prev_hash would call this chain clean.
        self._append(3)
        lines = [json.loads(l) for l in self._lines()]
        lines[0], lines[1] = lines[1], lines[0]
        lines[0]["prev_hash"] = rjc.GENESIS_HASH
        lines[1]["prev_hash"] = lines[0]["hash"]
        lines[2]["prev_hash"] = lines[1]["hash"]
        self._rewrite([json.dumps(e, sort_keys=True) for e in lines])
        result = rjc.verify(self.path)
        self.assertEqual(result["verdict"], "broken")
        self.assertEqual(result["index"], 0)

    # ---- trailing newline vs none, read identically ----

    def test_trailing_newline_and_no_trailing_newline_both_clean(self):
        self._append(2)
        with_nl = rjc.verify(self.path)
        self.assertEqual(with_nl["verdict"], "clean")
        with open(self.path, encoding="utf-8") as fh:
            text = fh.read()
        no_nl_path = os.path.join(self.tmp, "no_trailing_nl.jsonl")
        with open(no_nl_path, "w", encoding="utf-8") as fh:
            fh.write(text.rstrip("\n"))
        without_nl = rjc.verify(no_nl_path)
        self.assertEqual(without_nl["verdict"], "clean")
        self.assertEqual(without_nl["count"], with_nl["count"])
        self.assertEqual(without_nl["final_hash"], with_nl["final_hash"])

    # ---- a non-JSON line in the middle ----

    def test_non_json_line_in_middle(self):
        self._append(3)
        lines = self._lines()
        lines[1] = "not json at all {{{"
        self._rewrite(lines)
        result = rjc.verify(self.path)
        self.assertEqual(result["verdict"], "broken")
        self.assertEqual(result["index"], 1)

    # ---- unreadable file is NO-DATA, never clean ----

    def test_unreadable_file_is_no_data_never_clean(self):
        self._append(1)
        os.chmod(self.path, 0o000)
        try:
            if os.access(self.path, os.R_OK):
                self.skipTest("running as a user that ignores 0o000 (e.g. root)")
            result = rjc.verify(self.path)
            self.assertEqual(result["verdict"], rjc.NODATA)
            self.assertNotEqual(result["verdict"], "clean")
        finally:
            os.chmod(self.path, 0o644)

    # ---- receipt refuses on a broken chain ----

    def test_receipt_refuses_on_broken_chain(self):
        self._append(2)
        lines = [json.loads(l) for l in self._lines()]
        lines[0]["result"] = "tampered"
        self._rewrite([json.dumps(e, sort_keys=True) for e in lines])
        h, problem = rjc.receipt(self.path)
        self.assertIsNone(h)
        self.assertIn("broken", problem)

    def test_receipt_refuses_on_no_data(self):
        h, problem = rjc.receipt(os.path.join(self.tmp, "does-not-exist-dir",
                                              "journal.jsonl"))
        # a missing directory still reads as an empty (clean) journal per
        # _read_lines' own "no file yet" contract, so exercise the real
        # NO-DATA path instead: an unreadable existing file.
        self._append(1)
        os.chmod(self.path, 0o000)
        try:
            if os.access(self.path, os.R_OK):
                self.skipTest("running as a user that ignores 0o000 (e.g. root)")
            h, problem = rjc.receipt(self.path)
            self.assertIsNone(h)
            self.assertIn(rjc.NODATA, problem)
        finally:
            os.chmod(self.path, 0o644)


if __name__ == "__main__":
    unittest.main()

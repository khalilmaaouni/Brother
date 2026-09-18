#!/usr/bin/env python3
"""Tests for dependency_graph_check.py.

PROPERTY PROTECTED: a release dependency graph record that is malformed,
dangling, cyclic, or forks a lane away from the shared "release-gates"
terminal must be REFUSED (FAIL or NO-DATA), never silently accepted as a
well-formed skeleton. If this module were broken so that a forked or
cyclic graph passed, a roadmap row could claim the WBS-00.03 dependency
invariant ("no vertical lane may fork its own evidence or lifecycle") while
the recorded graph actually violates it, and nothing downstream would ever
notice.

EDGE LIST WALKED (see class docstring below for the ones ruled out):
  empty            -> nodes: [] refused (test_empty_nodes_is_refused)
  exactly one      -> a lone terminal node with no edges (test_single_terminal_node_passes)
  many             -> a longer chain and a diamond, both converging on the
                      terminal (test_chain_passes, test_diamond_passes)
  unknown value    -> an edge naming a node id that does not exist
                      (test_edge_to_unknown_node_is_refused)
  corrupt/truncated -> not-JSON on disk, and JSON that is not an object
                      (test_main_nodata_on_unreadable_file,
                       test_main_nodata_on_non_dict_json)
  boundary         -> exactly the terminal id missing entirely
                      (test_missing_terminal_is_refused)

OUT OF SCOPE, and why: this module is a pure function over one JSON
document with no persisted state, so "concurrent second actor", "expired
or stale", "already done", "partially done" and "actor same as last time"
do not apply. There is nothing here that is claimed, timed out, or acted
upon twice; the check either accepts or refuses the document it was handed,
once, and returns.
"""
import json
import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import dependency_graph_check as DGC  # noqa: E402

TERMINAL = DGC.TERMINAL_ID


class CheckRefusals(unittest.TestCase):
    """check(record) -- the pure validator. Every case here is a REFUSAL."""

    def test_empty_nodes_is_refused(self):
        problems = DGC.check({"nodes": [], "edges": []})
        self.assertTrue(problems)
        self.assertIn("non-empty", problems[0])

    def test_nodes_not_a_list_is_refused(self):
        problems = DGC.check({"nodes": "a", "edges": []})
        self.assertTrue(problems)

    def test_edges_not_a_list_is_refused(self):
        problems = DGC.check({"nodes": [{"id": "a"}], "edges": "nope"})
        self.assertTrue(problems)

    def test_node_missing_string_id_is_refused(self):
        problems = DGC.check({"nodes": [{"name": "a"}, {"id": TERMINAL}],
                               "edges": []})
        self.assertTrue(any("string" in p for p in problems))

    def test_edge_to_unknown_node_is_refused(self):
        problems = DGC.check({
            "nodes": [{"id": "a"}, {"id": TERMINAL}],
            "edges": [{"from": "a", "to": "ghost"}],
        })
        self.assertTrue(any("names no node" in p for p in problems))

    def test_edge_from_unknown_node_is_refused(self):
        problems = DGC.check({
            "nodes": [{"id": "a"}, {"id": TERMINAL}],
            "edges": [{"from": "ghost", "to": TERMINAL}],
        })
        self.assertTrue(any("names no node" in p for p in problems))

    def test_edge_entry_not_a_dict_is_refused(self):
        problems = DGC.check({
            "nodes": [{"id": "a"}, {"id": TERMINAL}],
            "edges": ["a -> b"],
        })
        self.assertTrue(any("must be an object" in p for p in problems))

    def test_missing_terminal_is_refused(self):
        problems = DGC.check({
            "nodes": [{"id": "a"}, {"id": "b"}],
            "edges": [{"from": "a", "to": "b"}],
        })
        self.assertTrue(any("no node named" in p for p in problems))

    def test_self_loop_is_a_cycle(self):
        problems = DGC.check({
            "nodes": [{"id": "a"}, {"id": TERMINAL}],
            "edges": [{"from": "a", "to": "a"}, {"from": "a", "to": TERMINAL}],
        })
        self.assertTrue(any("cycle" in p for p in problems))

    def test_two_node_cycle_is_refused(self):
        problems = DGC.check({
            "nodes": [{"id": "a"}, {"id": "b"}, {"id": TERMINAL}],
            "edges": [{"from": "a", "to": "b"}, {"from": "b", "to": "a"},
                      {"from": "a", "to": TERMINAL}],
        })
        self.assertTrue(any("cycle" in p for p in problems))

    def test_forked_lane_is_refused(self):
        """The roadmap's own invariant: a lane with no path to the terminal
        is a lane that forked its own evidence route, and must be caught
        even though the graph is otherwise acyclic and well-formed."""
        problems = DGC.check({
            "nodes": [{"id": "a"}, {"id": "b"}, {"id": TERMINAL}],
            "edges": [{"from": "a", "to": TERMINAL}],  # b reaches nothing
        })
        self.assertTrue(any("forks away" in p and "'b'" in p
                             for p in problems))


class CheckAcceptances(unittest.TestCase):
    """check(record) -- the cases that must PASS, so the refusals above are
    proven against a real baseline rather than against nothing."""

    def test_single_terminal_node_passes(self):
        self.assertEqual(DGC.check({"nodes": [{"id": TERMINAL}], "edges": []}),
                          [])

    def test_chain_passes(self):
        record = {
            "nodes": [{"id": "a"}, {"id": "b"}, {"id": TERMINAL}],
            "edges": [{"from": "a", "to": "b"}, {"from": "b", "to": TERMINAL}],
        }
        self.assertEqual(DGC.check(record), [])

    def test_diamond_passes(self):
        record = {
            "nodes": [{"id": "a"}, {"id": "b"}, {"id": "c"}, {"id": TERMINAL}],
            "edges": [
                {"from": "a", "to": "b"}, {"from": "a", "to": "c"},
                {"from": "b", "to": TERMINAL}, {"from": "c", "to": TERMINAL},
            ],
        }
        self.assertEqual(DGC.check(record), [])


class MainCli(unittest.TestCase):
    """main(argv) -- the exit-code contract other checkers in this repo
    share: 0 PASS, 1 FAIL, 2 NO-DATA."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="dgc-test-")

    def tearDown(self):
        import shutil
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _write(self, name, text):
        path = os.path.join(self.tmp, name)
        with open(path, "w", encoding="utf-8") as fh:
            fh.write(text)
        return path

    def test_main_pass_exit_0(self):
        path = self._write("good.json", json.dumps(
            {"nodes": [{"id": TERMINAL}], "edges": []}))
        self.assertEqual(DGC.main([path]), 0)

    def test_main_fail_exit_1(self):
        path = self._write("bad.json", json.dumps(
            {"nodes": [{"id": "a"}, {"id": TERMINAL}], "edges": []}))
        self.assertEqual(DGC.main([path]), 1)

    def test_main_nodata_on_unreadable_file(self):
        missing = os.path.join(self.tmp, "does-not-exist.json")
        self.assertEqual(DGC.main([missing]), 2)

    def test_main_nodata_on_corrupt_json(self):
        path = self._write("corrupt.json", "{not json at all")
        self.assertEqual(DGC.main([path]), 2)

    def test_main_nodata_on_non_dict_json(self):
        path = self._write("array.json", json.dumps([1, 2, 3]))
        self.assertEqual(DGC.main([path]), 2)

    def test_main_wrong_arg_count_is_usage_error(self):
        self.assertEqual(DGC.main([]), 2)
        self.assertEqual(DGC.main(["a", "b"]), 2)

    def test_main_selftest_passthrough(self):
        self.assertEqual(DGC.main(["--selftest"]), 0)


class SelfTest(unittest.TestCase):
    """The module carries its own _selftest(); it must keep passing, but it
    is not a substitute for the suite above (it never tries corrupt input,
    missing files or the CLI contract)."""

    def test_selftest_still_passes(self):
        self.assertEqual(DGC._selftest(), 0)


if __name__ == "__main__":
    unittest.main()

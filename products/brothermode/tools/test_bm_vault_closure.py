#!/usr/bin/env python3
"""Calibration for tools/bm_vault_closure.py (VB13-02): a maintained closure
table (node-to-descendant paths) over the hierarchy edges
tools/bm_vault_shapes.py owns, kept consistent when a
tools/bm_vault_hierarchy_req.py request commits.

The property under test is the brief's own claims: a ragged tree (different
depths in the same tree) rolls up correctly through the closure; a
multi-parent node under a declared polyhierarchy counts ONCE when the
rollup is path-filtered, and the raw (unfiltered) path count over-counts it
by exactly the number of extra paths; the closure stays consistent after a
request-based reorg once rebuilt; a stale stored closure against edited
edges is reported as DRIFT, never silently served; and an as-of before any
edge answers NO-DATA rather than an empty pass.

No em or en dashes anywhere in this file.
"""
import io
import json
import os
import shutil
import sys
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import bm_vault_closure as cl        # noqa: E402
import bm_vault_hierarchy_req as hr  # noqa: E402

D = cl.datetime.date


def note(entity=None, hierarchy_edges=None):
    lines = ["---", "type: reference", "status: standing"]
    if entity:
        lines.append("entity: %s" % entity)
    if hierarchy_edges:
        lines.append("hierarchy_edges: [%s]" % ", ".join(hierarchy_edges))
    lines += ["---", "", "# a note"]
    return "\n".join(lines) + "\n"


def run(fn, *a):
    out, err = io.StringIO(), io.StringIO()
    with redirect_stdout(out), redirect_stderr(err):
        rc = fn(*a)
    return rc, out.getvalue() + err.getvalue()


class Fixture(unittest.TestCase):
    """Isolates the synthetic vault and BOTH module-level stores (the
    closure file and the hierarchy_req request store): each is a module
    global read at call time, never captured as a default argument, so
    pointing them at tmp files here is honored by every call either module
    makes during the test, and a real ~/.claude file is never touched."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="bm-closure-")
        self.vault = os.path.join(self.tmp, "vault")
        os.makedirs(self.vault)
        self._orig_closure_store = cl.STORE_PATH
        self._orig_req_store = hr.STORE_PATH
        cl.STORE_PATH = os.path.join(self.tmp, "closure.json")
        hr.STORE_PATH = os.path.join(self.tmp, "requests.jsonl")

    def tearDown(self):
        cl.STORE_PATH = self._orig_closure_store
        hr.STORE_PATH = self._orig_req_store
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _write(self, rel, text):
        with open(os.path.join(self.vault, rel), "w", encoding="utf-8") as fh:
            fh.write(text)

    def _read(self, rel):
        with open(os.path.join(self.vault, rel), encoding="utf-8") as fh:
            return fh.read()

    def _stored_rows(self):
        with open(cl.STORE_PATH, encoding="utf-8") as fh:
            return json.load(fh)["rows"]


class RaggedTreeFixture(Fixture):
    """One hierarchy ("org"), one root, two branches of different depth:
    root -> a -> leaf1 (depth 2) and root -> b1 -> b2 -> b3 -> b4 -> leaf2
    (depth 5). The ragged case a fixed-depth level table cannot roll up."""

    def setUp(self):
        super().setUp()
        self._write("root.md", note("root"))
        self._write("a.md", note("a", ["name=org;parent=root;valid_from=2020-01-01"]))
        self._write("leaf1.md", note("leaf1", ["name=org;parent=a;valid_from=2020-01-01"]))
        self._write("b1.md", note("b1", ["name=org;parent=root;valid_from=2020-01-01"]))
        self._write("b2.md", note("b2", ["name=org;parent=b1;valid_from=2020-01-01"]))
        self._write("b3.md", note("b3", ["name=org;parent=b2;valid_from=2020-01-01"]))
        self._write("b4.md", note("b4", ["name=org;parent=b3;valid_from=2020-01-01"]))
        self._write("leaf2.md", note("leaf2", ["name=org;parent=b4;valid_from=2020-01-01"]))
        self.as_of = D(2024, 1, 1)


class TestRaggedRollup(RaggedTreeFixture):
    def test_rebuild_finds_every_depth(self):
        rc, out = run(cl.cmd_rebuild, self.vault, self.as_of)
        self.assertEqual(rc, 0, out)
        rows = self._stored_rows()
        # leaf1 is depth 2 under root, leaf2 is depth 5 under root: both
        # present in the same closure, at their own (ragged) depths.
        leaf1_row = next(r for r in rows if r["descendant"] == "leaf1" and r["ancestor"] == "root")
        leaf2_row = next(r for r in rows if r["descendant"] == "leaf2" and r["ancestor"] == "root")
        self.assertEqual(leaf1_row["depth"], 2)
        self.assertEqual(leaf2_row["depth"], 5)

    def test_rollup_at_root_covers_both_branches(self):
        run(cl.cmd_rebuild, self.vault, self.as_of)
        rc, out = run(cl.cmd_rollup, "root")
        self.assertEqual(rc, 0, out)
        self.assertIn("leaf1", out)
        self.assertIn("leaf2", out)
        # a, leaf1 (branch 1) plus b1, b2, b3, b4, leaf2 (branch 2): 7.
        self.assertIn("path-filtered distinct descendants: 7", out)

    def test_verify_clean_right_after_rebuild(self):
        run(cl.cmd_rebuild, self.vault, self.as_of)
        rc, out = run(cl.cmd_verify, self.vault)
        self.assertEqual(rc, 0, out)
        self.assertIn("clean", out)


class TestAsOfBeforeAnyEdge(RaggedTreeFixture):
    def test_rebuild_before_any_edge_is_no_data(self):
        rc, out = run(cl.cmd_rebuild, self.vault, D(2019, 1, 1))
        self.assertEqual(rc, 2, out)
        self.assertIn("NO-DATA", out)
        self.assertFalse(os.path.exists(cl.STORE_PATH))


class PolyhierarchyFixture(Fixture):
    """X is a declared polyhierarchy node: it carries edges under TWO named
    hierarchies (legal, trade) to two different immediate parents, and both
    parents' own chains reconverge on the same grandparent G. A rollup at G
    must count X once (path-filtered) even though two independent paths
    from X reach G, one per hierarchy name."""

    def setUp(self):
        super().setUp()
        self._write("g.md", note("g"))
        self._write("p1.md", note("p1", ["name=legal;parent=g;valid_from=2020-01-01"]))
        self._write("p2.md", note("p2", ["name=trade;parent=g;valid_from=2020-01-01"]))
        self._write("x.md", note("x", [
            "name=legal;parent=p1;valid_from=2020-01-01",
            "name=trade;parent=p2;valid_from=2020-01-01",
        ]))
        self.as_of = D(2024, 1, 1)


class TestPolyhierarchyCountsOnce(PolyhierarchyFixture):
    def test_raw_rows_double_count_x_under_g(self):
        run(cl.cmd_rebuild, self.vault, self.as_of)
        rows = self._stored_rows()
        raw_for_x_under_g = [r for r in rows if r["ancestor"] == "g" and r["descendant"] == "x"]
        # The unfiltered double count this brief calls out by name: one row
        # per hierarchy path (legal and trade), both landing on the same
        # (ancestor, descendant) pair.
        self.assertEqual(len(raw_for_x_under_g), 2)
        self.assertEqual(sorted(r["hierarchy"] for r in raw_for_x_under_g), ["legal", "trade"])

    def test_rollup_path_filters_x_to_one(self):
        run(cl.cmd_rebuild, self.vault, self.as_of)
        rc, out = run(cl.cmd_rollup, "g")
        self.assertEqual(rc, 0, out)
        # g's descendants: p1, p2, x. Raw rows: p1(1) + p2(1) + x(2) = 4.
        self.assertIn("raw path rows: 4", out)
        self.assertIn("path-filtered distinct descendants: 3", out)
        self.assertIn("x  (2 path(s): legal, trade)", out)


class TestReorgThenVerify(RaggedTreeFixture):
    """A hierarchy_req request (create, approve) is the only sanctioned way
    to change hierarchy_edges. After it commits, a rebuild for the reorg's
    own effective date followed by verify must report no drift: the
    closure and the vault agree because both were read after the change."""

    def test_rebuild_after_approved_reorg_verifies_clean(self):
        # Move leaf1 from under a to under root directly, effective later.
        rc, out = run(hr.cmd_create, self.vault,
                       ["op=close;child=leaf1;hierarchy=org;valid_to=2024-06-30",
                        "op=add;child=leaf1;hierarchy=org;parent=root;valid_from=2024-07-01"])
        self.assertEqual(rc, 0, out)
        req_id = hr._read_rows()[0]["id"]
        rc, out = run(hr.cmd_approve, self.vault, req_id, "khalil", "2024-07-01")
        self.assertEqual(rc, 0, out)

        rc, out = run(cl.cmd_rebuild, self.vault, D(2024, 7, 1))
        self.assertEqual(rc, 0, out)
        rc, out = run(cl.cmd_verify, self.vault)
        self.assertEqual(rc, 0, out)
        self.assertIn("clean", out)
        rows = self._stored_rows()
        leaf1_row = next(r for r in rows if r["descendant"] == "leaf1" and r["ancestor"] == "root")
        self.assertEqual(leaf1_row["depth"], 1)  # leaf1 now a direct child of root


class TestDriftDetection(RaggedTreeFixture):
    def test_edit_outside_the_request_flow_is_reported_as_drift(self):
        rc, out = run(cl.cmd_rebuild, self.vault, self.as_of)
        self.assertEqual(rc, 0, out)

        # Hand-edit leaf1's parent directly, bypassing bm_vault_hierarchy_req
        # entirely -- exactly the case the store can no longer trust itself
        # against without a fresh read.
        self._write("leaf1.md", note("leaf1", ["name=org;parent=root;valid_from=2020-01-01"]))

        rc, out = run(cl.cmd_verify, self.vault)
        self.assertEqual(rc, 1, out)
        self.assertIn("DRIFT", out)

    def test_missing_store_is_no_data(self):
        rc, out = run(cl.cmd_verify, self.vault)
        self.assertEqual(rc, 2, out)
        self.assertIn("NO-DATA", out)


class TestRollupNoData(RaggedTreeFixture):
    def test_rollup_with_no_store_is_no_data(self):
        rc, out = run(cl.cmd_rollup, "root")
        self.assertEqual(rc, 2, out)
        self.assertIn("NO-DATA", out)

    def test_rollup_of_unknown_ancestor_is_no_data(self):
        run(cl.cmd_rebuild, self.vault, self.as_of)
        rc, out = run(cl.cmd_rollup, "no-such-entity")
        self.assertEqual(rc, 2, out)
        self.assertIn("NO-DATA", out)


class TestCLI(RaggedTreeFixture):
    def test_main_rebuild_then_verify_then_rollup(self):
        rc, out = run(cl.main, ["rebuild", "--vault", self.vault, "--as-of", str(self.as_of)])
        self.assertEqual(rc, 0, out)
        rc, out = run(cl.main, ["verify", "--vault", self.vault])
        self.assertEqual(rc, 0, out)
        rc, out = run(cl.main, ["rollup", "--ancestor", "root"])
        self.assertEqual(rc, 0, out)

    def test_main_rebuild_without_as_of_is_no_data(self):
        rc, out = run(cl.main, ["rebuild", "--vault", self.vault])
        self.assertEqual(rc, 2, out)

    def test_main_rollup_without_ancestor_is_no_data(self):
        rc, out = run(cl.main, ["rollup"])
        self.assertEqual(rc, 2, out)


if __name__ == "__main__":
    unittest.main()

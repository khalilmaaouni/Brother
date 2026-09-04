#!/usr/bin/env python3
"""Calibration for tools/bm_vault_hierarchy_req.py (VB13-01): named hierarchy
query modes (direct/transitive) and a request flow for hierarchy_edges
changes, on top of tools/bm_vault_shapes.py (VB12-02).

The property under test is the brief's own two claims: a query always names
which mode answered, and a hierarchy change is a validated, previewable,
atomic request, never a direct edit. The guards are their shadows: a query
missing --mode refuses rather than defaulting, a request with one
contract-violating item is refused whole (nothing stored, nothing written),
an approval requires --by while a rejection does not, a rejected request
stays queryable with its reason, and a preview never touches the request
store.

No em or en dashes anywhere in this file.
"""
import io
import os
import shutil
import sys
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import bm_vault_hierarchy_req as hr  # noqa: E402

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

D = hr.datetime.date


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
    """Isolates both the synthetic vault and the request store: STORE_PATH
    is a module global read at call time (never captured as a default
    argument), so pointing it at a tmp file here is honored by every
    _append/_read_rows call the module makes during the test."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="bm-hreq-")
        self.vault = os.path.join(self.tmp, "vault")
        os.makedirs(self.vault)
        self._orig_store = hr.STORE_PATH
        hr.STORE_PATH = os.path.join(self.tmp, "requests.jsonl")

    def tearDown(self):
        hr.STORE_PATH = self._orig_store
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _write(self, rel, text):
        with open(os.path.join(self.vault, rel), "w", encoding="utf-8") as fh:
            fh.write(text)

    def _read(self, rel):
        with open(os.path.join(self.vault, rel), encoding="utf-8") as fh:
            return fh.read()


class ThreeLevelFixture(Fixture):
    """grandparent <- parent <- child, one hierarchy named 'legal', all
    edges open-ended from 2020-01-01."""

    def setUp(self):
        super().setUp()
        self._write("grandparent.md", note("grandparent"))
        self._write("parent.md", note(
            "parent", hierarchy_edges=["name=legal;parent=grandparent;valid_from=2020-01-01"]))
        self._write("child.md", note(
            "child", hierarchy_edges=["name=legal;parent=parent;valid_from=2020-01-01"]))


class TestQueryModesUp(ThreeLevelFixture):

    def test_direct_gives_one_hop_only(self):
        rc, out = run(hr.cmd_query, self.vault, "child", "legal", D(2024, 1, 1), "direct", "up")
        self.assertEqual(rc, 0)
        self.assertIn("mode: direct", out)
        self.assertIn("parent", out)
        self.assertNotIn("grandparent", out)

    def test_transitive_gives_full_chain(self):
        rc, out = run(hr.cmd_query, self.vault, "child", "legal", D(2024, 1, 1), "transitive", "up")
        self.assertEqual(rc, 0)
        self.assertIn("mode: transitive", out)
        self.assertIn("parent", out)
        self.assertIn("grandparent", out)

    def test_direct_and_transitive_answer_differently(self):
        _, direct_out = run(hr.cmd_query, self.vault, "child", "legal", D(2024, 1, 1), "direct", "up")
        _, trans_out = run(hr.cmd_query, self.vault, "child", "legal", D(2024, 1, 1), "transitive", "up")
        self.assertNotEqual(direct_out, trans_out)


class TestQueryModesDown(ThreeLevelFixture):

    def test_direct_children_only(self):
        rc, out = run(hr.cmd_query, self.vault, "grandparent", "legal", D(2024, 1, 1), "direct", "down")
        self.assertEqual(rc, 0)
        self.assertIn("mode: direct", out)
        self.assertIn("children=parent  as of", out)

    def test_transitive_all_descendants(self):
        rc, out = run(hr.cmd_query, self.vault, "grandparent", "legal", D(2024, 1, 1), "transitive", "down")
        self.assertEqual(rc, 0)
        self.assertIn("mode: transitive", out)
        self.assertIn("descendants=parent, child  as of", out)


class TestQueryNoModeRefuses(ThreeLevelFixture):

    def test_missing_mode_refuses_rather_than_defaulting(self):
        rc, out = run(hr.cmd_query, self.vault, "child", "legal", D(2024, 1, 1), None, "up")
        self.assertEqual(rc, 2)
        self.assertIn("needs --mode", out)

    def test_cli_missing_mode_refuses(self):
        rc, out = run(hr.main, ["query", "--vault", self.vault, "--entity", "child",
                                 "--hierarchy", "legal", "--as-of", "2024-01-01"])
        self.assertEqual(rc, 2)


class TestCreateRefusesInvalidWhole(ThreeLevelFixture):
    """An item that overlaps an existing edge violates the shapes contract:
    the whole request is refused, nothing lands in the store."""

    def test_overlapping_item_refuses_and_stores_nothing(self):
        rc, out = run(hr.cmd_create, self.vault,
                       ["op=add;child=child;hierarchy=legal;parent=grandparent;valid_from=2020-06-01"])
        self.assertEqual(rc, 1)
        self.assertIn("REFUSED", out)
        self.assertIn("nothing stored", out)
        self.assertEqual(hr._read_rows(), [])

    def test_unknown_parent_refuses_and_stores_nothing(self):
        rc, out = run(hr.cmd_create, self.vault,
                       ["op=add;child=child;hierarchy=ops;parent=nobody;valid_from=2025-01-01"])
        self.assertEqual(rc, 1)
        self.assertEqual(hr._read_rows(), [])

    def test_malformed_item_refuses_and_stores_nothing(self):
        rc, out = run(hr.cmd_create, self.vault, ["op=add;child=child;hierarchy=ops"])
        self.assertEqual(rc, 1)
        self.assertIn("needs parent", out)
        self.assertEqual(hr._read_rows(), [])


class TestApprovedRequestLandsAtomically(ThreeLevelFixture):

    def test_approve_needs_by(self):
        rc, out = run(hr.cmd_create, self.vault,
                       ["op=add;child=child;hierarchy=trade;parent=grandparent;valid_from=2025-01-01"])
        self.assertEqual(rc, 0)
        req_id = hr._read_rows()[0]["id"]
        rc, out = run(hr.cmd_approve, self.vault, req_id, None, "2025-01-01")
        self.assertEqual(rc, 2)
        self.assertIn("needs --by", out)
        # nothing was written
        self.assertNotIn("trade", self._read("child.md"))

    def test_approved_request_writes_new_dated_interval(self):
        rc, _ = run(hr.cmd_create, self.vault,
                    ["op=add;child=child;hierarchy=trade;parent=grandparent;valid_from=2025-01-01"])
        self.assertEqual(rc, 0)
        req_id = hr._read_rows()[0]["id"]
        rc, out = run(hr.cmd_approve, self.vault, req_id, "khalil", "2025-01-01")
        self.assertEqual(rc, 0)
        self.assertIn("approved by khalil", out)
        text = self._read("child.md")
        self.assertIn("name=trade;parent=grandparent;valid_from=2025-01-01", text)
        # the original legal edge is untouched
        self.assertIn("name=legal;parent=parent;valid_from=2020-01-01", text)

    def test_as_of_tree_before_request_date_is_unchanged(self):
        rc, _ = run(hr.cmd_create, self.vault,
                    ["op=add;child=child;hierarchy=trade;parent=grandparent;valid_from=2025-01-01"])
        req_id = hr._read_rows()[0]["id"]
        _, before_out = run(hr.cmd_query, self.vault, "child", "trade", D(2024, 6, 1), "direct", "up")
        run(hr.cmd_approve, self.vault, req_id, "khalil", "2025-01-01")
        _, after_out = run(hr.cmd_query, self.vault, "child", "trade", D(2024, 6, 1), "direct", "up")
        self.assertEqual(before_out, after_out)
        self.assertIn("NO-DATA", after_out)

    def test_double_approve_refuses(self):
        rc, _ = run(hr.cmd_create, self.vault,
                    ["op=add;child=child;hierarchy=trade;parent=grandparent;valid_from=2025-01-01"])
        req_id = hr._read_rows()[0]["id"]
        run(hr.cmd_approve, self.vault, req_id, "khalil", "2025-01-01")
        rc, out = run(hr.cmd_approve, self.vault, req_id, "khalil", "2025-02-01")
        self.assertEqual(rc, 1)
        self.assertIn("already has a decision", out)

    def test_move_is_a_close_plus_an_add(self):
        rc, _ = run(hr.cmd_create, self.vault, [
            "op=close;child=child;hierarchy=legal;valid_to=2024-12-31",
            "op=add;child=child;hierarchy=legal;parent=grandparent;valid_from=2025-01-01",
        ])
        self.assertEqual(rc, 0)
        req_id = hr._read_rows()[0]["id"]
        rc, out = run(hr.cmd_approve, self.vault, req_id, "khalil", "2025-01-01")
        self.assertEqual(rc, 0)
        text = self._read("child.md")
        self.assertIn("valid_to=2024-12-31", text)
        self.assertIn("name=legal;parent=grandparent;valid_from=2025-01-01", text)
        # old parent still resolves before the move
        _, out = run(hr.cmd_query, self.vault, "child", "legal", D(2024, 6, 1), "direct", "up")
        self.assertIn("parent", out)
        # new parent resolves after the move
        _, out2 = run(hr.cmd_query, self.vault, "child", "legal", D(2025, 6, 1), "direct", "up")
        self.assertIn("grandparent", out2)


class TestRejectedRequestQueryable(ThreeLevelFixture):

    def test_reject_needs_no_by(self):
        rc, _ = run(hr.cmd_create, self.vault,
                    ["op=add;child=child;hierarchy=ops;parent=grandparent;valid_from=2026-01-01"])
        req_id = hr._read_rows()[0]["id"]
        rc, out = run(hr.cmd_reject, req_id, "not needed right now", None, "2025-06-01")
        self.assertEqual(rc, 0)

    def test_rejected_request_shows_reason_and_no_approver(self):
        rc, _ = run(hr.cmd_create, self.vault,
                    ["op=add;child=child;hierarchy=ops;parent=grandparent;valid_from=2026-01-01"])
        req_id = hr._read_rows()[0]["id"]
        run(hr.cmd_reject, req_id, "not needed right now", None, "2025-06-01")
        rc, out = run(hr.cmd_show, req_id)
        self.assertEqual(rc, 0)
        self.assertIn("status: rejected", out)
        self.assertIn("not needed right now", out)
        self.assertIn("approver: (none)", out)
        # nothing was ever written to the vault for a rejected request
        self.assertNotIn("ops", self._read("child.md"))

    def test_reject_needs_a_reason(self):
        rc, _ = run(hr.cmd_create, self.vault,
                    ["op=add;child=child;hierarchy=ops;parent=grandparent;valid_from=2026-01-01"])
        req_id = hr._read_rows()[0]["id"]
        rc, out = run(hr.cmd_reject, req_id, None, "khalil", "2025-06-01")
        self.assertEqual(rc, 2)
        self.assertIn("needs --reason", out)


class TestPreviewNeverMutatesStore(ThreeLevelFixture):

    def test_preview_renders_before_and_after(self):
        rc, _ = run(hr.cmd_create, self.vault,
                    ["op=add;child=child;hierarchy=trade;parent=grandparent;valid_from=2025-01-01"])
        req_id = hr._read_rows()[0]["id"]
        rc, out = run(hr.cmd_preview, self.vault, req_id)
        self.assertEqual(rc, 0)
        self.assertIn("BEFORE", out)
        self.assertIn("AFTER", out)
        self.assertIn("mode: transitive", out)

    def test_preview_does_not_touch_the_store(self):
        rc, _ = run(hr.cmd_create, self.vault,
                    ["op=add;child=child;hierarchy=trade;parent=grandparent;valid_from=2025-01-01"])
        req_id = hr._read_rows()[0]["id"]
        with open(hr.STORE_PATH, "rb") as fh:
            before_bytes = fh.read()
        run(hr.cmd_preview, self.vault, req_id)
        with open(hr.STORE_PATH, "rb") as fh:
            after_bytes = fh.read()
        self.assertEqual(before_bytes, after_bytes)

    def test_preview_does_not_touch_the_vault(self):
        rc, _ = run(hr.cmd_create, self.vault,
                    ["op=add;child=child;hierarchy=trade;parent=grandparent;valid_from=2025-01-01"])
        req_id = hr._read_rows()[0]["id"]
        before = self._read("child.md")
        run(hr.cmd_preview, self.vault, req_id)
        self.assertEqual(before, self._read("child.md"))


class TestCli(ThreeLevelFixture):

    def test_no_vault_is_nodata(self):
        rc, _ = run(hr.main, ["query", "--vault", os.path.join(self.tmp, "nope"),
                               "--entity", "child", "--hierarchy", "legal",
                               "--as-of", "2024-01-01", "--mode", "direct"])
        self.assertEqual(rc, 2)

    def test_bad_as_of_is_nodata(self):
        rc, out = run(hr.main, ["query", "--vault", self.vault, "--entity", "child",
                                 "--hierarchy", "legal", "--as-of", "not-a-date", "--mode", "direct"])
        self.assertEqual(rc, 2)
        self.assertIn("not an ISO date", out)

    def test_create_and_show_round_trip_via_cli(self):
        rc, out = run(hr.main, ["create", "--vault", self.vault,
                                 "--item", "op=add;child=child;hierarchy=trade;"
                                           "parent=grandparent;valid_from=2025-01-01"])
        self.assertEqual(rc, 0)
        req_id = hr._read_rows()[0]["id"]
        rc, out = run(hr.main, ["show", "--id", req_id])
        self.assertEqual(rc, 0)
        self.assertIn("status: pending", out)


if __name__ == "__main__":
    unittest.main()

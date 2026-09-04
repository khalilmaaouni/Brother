#!/usr/bin/env python3
"""Calibration for tools/bm_vault_shapes.py (VB12-02): named hierarchy edges with
validity intervals, dated placement records, and the competitor namespace's
syndicated-id mapping table.

The property under test is each shape's own sentence: a reorg lands as new
dated intervals with history intact and an as-of query resolves the tree at
any date; two concurrent hierarchies over one entity resolve independently; a
placement conversion is a dated decision that never re-keys the asset; a
competitor mapping resolves by date with its source flag. The guards are their
shadows: overlapping intervals for one child within one hierarchy refuse, an
as-of before the first interval answers NO-DATA, and a mapping query past
valid-to does not resolve.

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
import bm_vault_shapes as sh  # noqa: E402

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

D = sh.datetime.date


def note(entity=None, hierarchy_edges=None, placements=None,
         internal_id=None, syndicated_mappings=None):
    lines = ["---", "type: reference", "status: standing"]
    if entity:
        lines.append("entity: %s" % entity)
    if internal_id:
        lines.append("internal_id: %s" % internal_id)
    if hierarchy_edges:
        lines.append("hierarchy_edges: [%s]" % ", ".join(hierarchy_edges))
    if placements:
        lines.append("placements: [%s]" % ", ".join(placements))
    if syndicated_mappings:
        lines.append("syndicated_mappings: [%s]" % ", ".join(syndicated_mappings))
    lines += ["---", "", "# a note"]
    return "\n".join(lines) + "\n"


def run(fn, *a):
    out, err = io.StringIO(), io.StringIO()
    with redirect_stdout(out), redirect_stderr(err):
        rc = fn(*a)
    return rc, out.getvalue() + err.getvalue()


class Fixture(unittest.TestCase):

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="bm-shapes-")
        self.vault = os.path.join(self.tmp, "vault")
        os.makedirs(self.vault)

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _write(self, rel, text):
        path = os.path.join(self.vault, rel)
        with open(path, "w", encoding="utf-8") as fh:
            fh.write(text)


class TestZeroDeclarations(Fixture):

    def test_no_declarations_is_nodata_at_exit_2(self):
        self._write("plain.md", note())
        rc, out = run(sh.cmd_check, self.vault)
        self.assertEqual(rc, 2)
        self.assertIn("NO-DATA", out)


class TestHierarchyGrammar(unittest.TestCase):

    def test_undated_entry_parses(self):
        entries, problems = sh.parse_hierarchy_edges("[name=legal;parent=hq]")
        self.assertEqual(problems, [])
        self.assertEqual(entries[0]["name"], "legal")
        self.assertEqual(entries[0]["parent"], "hq")
        self.assertIsNone(entries[0]["valid_from"])
        self.assertIsNone(entries[0]["valid_to"])

    def test_missing_parent_is_a_problem(self):
        entries, problems = sh.parse_hierarchy_edges("[name=legal]")
        self.assertEqual(entries, [])
        self.assertIn("missing required field", problems[0])

    def test_unknown_field_is_a_problem(self):
        entries, problems = sh.parse_hierarchy_edges("[name=legal;parent=hq;bogus=x]")
        self.assertEqual(entries, [])
        self.assertIn("unknown field", problems[0])

    def test_malformed_date_is_a_problem(self):
        entries, problems = sh.parse_hierarchy_edges(
            "[name=legal;parent=hq;valid_from=not-a-date]")
        self.assertEqual(entries, [])
        self.assertIn("invalid valid_from date", problems[0])

    def test_from_after_to_is_a_problem(self):
        entries, problems = sh.parse_hierarchy_edges(
            "[name=legal;parent=hq;valid_from=2022-01-01;valid_to=2020-01-01]")
        self.assertEqual(entries, [])
        self.assertIn("after valid_to", problems[0])


class TestPlacementGrammar(unittest.TestCase):

    def test_unknown_model_is_a_problem(self):
        entries, problems = sh.parse_placements("[location=site-a;model=leased]")
        self.assertEqual(entries, [])
        self.assertIn("unknown model", problems[0])

    def test_full_op_and_semi_op_both_parse(self):
        entries, problems = sh.parse_placements(
            "[location=site-a;model=full-op, location=site-a;model=semi-op]")
        self.assertEqual(problems, [])
        self.assertEqual([e["model"] for e in entries], ["full-op", "semi-op"])


class TestMappingGrammar(unittest.TestCase):

    def test_missing_source_is_a_problem(self):
        entries, problems = sh.parse_syndicated_mappings("[external=synd-1]")
        self.assertEqual(entries, [])
        self.assertIn("missing required field", problems[0])


class TestHierarchyReorg(Fixture):
    """A reorg is never an edit in place: the old interval closes and a new
    one opens, both on disk, and as-of resolves the tree correctly on either
    side of the boundary."""

    def setUp(self):
        super().setUp()
        self._write("hq-old.md", note("system"))
        self._write("hq-new.md", note("system"))
        self._write("child.md", note(
            "system",
            hierarchy_edges=[
                "name=legal;parent=hq-old;valid_from=2010-01-01;valid_to=2022-05-31",
                "name=legal;parent=hq-new;valid_from=2022-06-01",
            ]))

    def test_resolves_old_parent_before_reorg(self):
        rc, out = run(sh.cmd_resolve_hierarchy, self.vault, "child", "legal", D(2022, 1, 1))
        self.assertEqual(rc, 0)
        self.assertIn("hq-old", out)

    def test_resolves_new_parent_after_reorg(self):
        rc, out = run(sh.cmd_resolve_hierarchy, self.vault, "child", "legal", D(2023, 1, 1))
        self.assertEqual(rc, 0)
        self.assertIn("hq-new", out)

    def test_history_intact_check_is_clean(self):
        rc, out = run(sh.cmd_check, self.vault)
        self.assertEqual(rc, 0)
        self.assertIn("entities with hierarchy edges: 1 (2 edge(s))", out)

    def test_as_of_before_first_interval_is_nodata(self):
        rc, out = run(sh.cmd_resolve_hierarchy, self.vault, "child", "legal", D(1999, 1, 1))
        self.assertEqual(rc, 1)
        self.assertIn("NO-DATA", out)


class TestConcurrentHierarchies(Fixture):
    """Two named hierarchies over one entity set resolve independently."""

    def setUp(self):
        super().setUp()
        self._write("legal-hq.md", note("system"))
        self._write("trade-hq.md", note("system"))
        self._write("child.md", note(
            "system",
            hierarchy_edges=[
                "name=legal;parent=legal-hq",
                "name=trade;parent=trade-hq",
            ]))

    def test_legal_and_trade_resolve_to_different_parents(self):
        rc1, out1 = run(sh.cmd_resolve_hierarchy, self.vault, "child", "legal", D(2026, 1, 1))
        rc2, out2 = run(sh.cmd_resolve_hierarchy, self.vault, "child", "trade", D(2026, 1, 1))
        self.assertEqual((rc1, rc2), (0, 0))
        self.assertIn("legal-hq", out1)
        self.assertIn("trade-hq", out2)

    def test_check_reports_no_findings(self):
        rc, out = run(sh.cmd_check, self.vault)
        self.assertEqual(rc, 0)
        self.assertNotIn("FINDINGS", out)


class TestHierarchyOverlapRefuses(Fixture):

    def test_overlapping_interval_for_one_child_one_hierarchy_refuses(self):
        self._write("hq-a.md", note("system"))
        self._write("hq-b.md", note("system"))
        self._write("child.md", note(
            "system",
            hierarchy_edges=[
                "name=legal;parent=hq-a;valid_from=2020-01-01;valid_to=2022-12-31",
                "name=legal;parent=hq-b;valid_from=2022-01-01",
            ]))
        rc, out = run(sh.cmd_check, self.vault)
        self.assertEqual(rc, 1)
        self.assertIn("overlaps", out)

    def test_touching_boundary_is_also_an_overlap(self):
        # Both ends inclusive, same rule as the crosswalk: two intervals that
        # merely touch at one shared day still overlap on that day.
        self._write("hq-a.md", note("system"))
        self._write("hq-b.md", note("system"))
        self._write("child.md", note(
            "system",
            hierarchy_edges=[
                "name=legal;parent=hq-a;valid_to=2022-06-01",
                "name=legal;parent=hq-b;valid_from=2022-06-01",
            ]))
        rc, out = run(sh.cmd_check, self.vault)
        self.assertEqual(rc, 1)
        self.assertIn("overlaps", out)


class TestDanglingParent(Fixture):

    def test_dangling_parent_is_named(self):
        self._write("child.md", note("system", hierarchy_edges=["name=legal;parent=ghost"]))
        rc, out = run(sh.cmd_check, self.vault)
        self.assertEqual(rc, 1)
        self.assertIn("DANGLING parent", out)
        self.assertIn("ghost", out)


class TestPlacementConversion(Fixture):
    """A conversion between ownership models is a dated decision: the old
    placement closes, a new one opens, and the asset's own key never
    changes."""

    def setUp(self):
        super().setUp()
        self._write("site-a.md", note("customer-location"))
        self._write("machine-1.md", note(
            "asset",
            placements=[
                "location=site-a;model=full-op;valid_from=2020-01-01;valid_to=2021-05-31",
                "location=site-a;model=semi-op;valid_from=2021-06-01",
            ]))

    def test_asset_key_unchanged_both_versions_present(self):
        rc, out = run(sh.cmd_check, self.vault)
        self.assertEqual(rc, 0)
        self.assertIn("assets with placements: 1 (2 record(s))", out)

    def test_resolves_full_op_before_conversion(self):
        rc, out = run(sh.cmd_resolve_placement, self.vault, "machine-1", D(2021, 1, 1))
        self.assertEqual(rc, 0)
        self.assertIn("model=full-op", out)

    def test_resolves_semi_op_after_conversion(self):
        rc, out = run(sh.cmd_resolve_placement, self.vault, "machine-1", D(2022, 1, 1))
        self.assertEqual(rc, 0)
        self.assertIn("model=semi-op", out)

    def test_as_of_before_first_interval_is_nodata(self):
        rc, out = run(sh.cmd_resolve_placement, self.vault, "machine-1", D(2019, 1, 1))
        self.assertEqual(rc, 1)
        self.assertIn("NO-DATA", out)


class TestPlacementOverlapRefuses(Fixture):

    def test_two_concurrent_placements_for_one_asset_refuses(self):
        self._write("site-a.md", note("customer-location"))
        self._write("site-b.md", note("customer-location"))
        self._write("machine-1.md", note(
            "asset",
            placements=[
                "location=site-a;model=full-op;valid_from=2020-01-01;valid_to=2022-01-01",
                "location=site-b;model=semi-op;valid_from=2021-01-01",
            ]))
        rc, out = run(sh.cmd_check, self.vault)
        self.assertEqual(rc, 1)
        self.assertIn("overlaps", out)


class TestDanglingLocation(Fixture):

    def test_dangling_location_is_named(self):
        self._write("machine-1.md", note(
            "asset", placements=["location=ghost-site;model=full-op"]))
        rc, out = run(sh.cmd_check, self.vault)
        self.assertEqual(rc, 1)
        self.assertIn("DANGLING location", out)
        self.assertIn("ghost-site", out)


class TestCompetitorMapping(Fixture):

    def setUp(self):
        super().setUp()
        self._write("rival-co.md", note(
            "competitor", internal_id="comp-001",
            syndicated_mappings=[
                "external=synd-100;source=vendor-a;valid_to=2021-12-31",
                "external=synd-200;source=vendor-a;valid_from=2022-01-01",
            ]))

    def test_resolves_by_date_with_source_flag(self):
        rc, out = run(sh.cmd_resolve_competitor, self.vault, "rival-co", D(2020, 6, 1))
        self.assertEqual(rc, 0)
        self.assertIn("external=synd-100", out)
        self.assertIn("source=vendor-a", out)

    def test_resolves_new_external_id_after_switch(self):
        rc, out = run(sh.cmd_resolve_competitor, self.vault, "rival-co", D(2023, 1, 1))
        self.assertEqual(rc, 0)
        self.assertIn("external=synd-200", out)

    def test_query_past_valid_to_does_not_resolve(self):
        # The old mapping's valid_to is 2021-12-31 and the new one only opens
        # 2022-01-01: a date in that exact gap must not resolve.
        self._write("rival-gap.md", note(
            "competitor", internal_id="comp-002",
            syndicated_mappings=["external=synd-9;source=vendor-b;valid_to=2020-06-01"]))
        rc, out = run(sh.cmd_resolve_competitor, self.vault, "rival-gap", D(2020, 12, 1))
        self.assertEqual(rc, 1)
        self.assertIn("NO-DATA", out)

    def test_check_is_clean(self):
        rc, out = run(sh.cmd_check, self.vault)
        self.assertEqual(rc, 0)
        self.assertIn("competitors with syndicated mappings: 1 (2 mapping(s))", out)

    def test_two_sources_concurrently_is_not_a_conflict(self):
        self._write("rival-co.md", note(
            "competitor", internal_id="comp-001",
            syndicated_mappings=[
                "external=synd-100;source=vendor-a",
                "external=synd-777;source=vendor-b",
            ]))
        rc, out = run(sh.cmd_check, self.vault)
        self.assertEqual(rc, 0)


class TestCompetitorNamespaceRequiresInternalId(Fixture):

    def test_missing_internal_id_is_a_finding(self):
        self._write("rival-co.md", note(
            "competitor",
            syndicated_mappings=["external=synd-100;source=vendor-a"]))
        rc, out = run(sh.cmd_check, self.vault)
        self.assertEqual(rc, 1)
        self.assertIn("internal_id", out)


class TestDeclarationWithoutEntityRefuses(Fixture):

    def test_hierarchy_edges_without_entity_is_a_finding(self):
        self._write("stray.md", note(hierarchy_edges=["name=legal;parent=hq"]))
        rc, out = run(sh.cmd_check, self.vault)
        self.assertEqual(rc, 1)
        self.assertIn("without entity:", out)


class TestResolveHonestMisses(Fixture):

    def test_unknown_entity_is_nodata_at_exit_2(self):
        self._write("plain.md", note("system"))
        rc, out = run(sh.cmd_resolve_hierarchy, self.vault, "nobody", "legal", D(2026, 1, 1))
        self.assertEqual(rc, 2)
        self.assertIn("NO-DATA", out)

    def test_no_hierarchy_edge_at_all_is_nodata(self):
        self._write("child.md", note("system"))
        rc, out = run(sh.cmd_resolve_hierarchy, self.vault, "child", "legal", D(2026, 1, 1))
        self.assertEqual(rc, 1)
        self.assertIn("NO-DATA", out)


class TestCli(Fixture):

    def test_no_vault_is_nodata(self):
        rc, _ = run(sh.main, ["check", "--vault", os.path.join(self.tmp, "nope")])
        self.assertEqual(rc, 2)

    def test_resolve_hierarchy_needs_entity_and_hierarchy(self):
        self._write("plain.md", note("system"))
        rc, _ = run(sh.main, ["resolve-hierarchy", "--vault", self.vault, "--as-of", "2026-01-01"])
        self.assertEqual(rc, 2)

    def test_resolve_placement_needs_asset(self):
        self._write("plain.md", note("system"))
        rc, _ = run(sh.main, ["resolve-placement", "--vault", self.vault, "--as-of", "2026-01-01"])
        self.assertEqual(rc, 2)

    def test_bad_as_of_is_nodata(self):
        self._write("plain.md", note("system"))
        rc, out = run(sh.main, ["resolve-placement", "--vault", self.vault,
                                 "--asset", "plain", "--as-of", "not-a-date"])
        self.assertEqual(rc, 2)
        self.assertIn("not an ISO date", out)


if __name__ == "__main__":
    unittest.main()

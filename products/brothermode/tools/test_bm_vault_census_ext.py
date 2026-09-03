#!/usr/bin/env python3
"""Calibration for tools/bm_vault_census_ext.py, WBS row VB13-06.

The property under test, driven backwards from the row's own done-check: each of the
seven new dimensions reports a seeded fixture defect AND reports NO-DATA (never a
silent pass) on an empty domain, both asserted per dimension. Findings group per
owner with folder dedupe proven through bm_vault_route's own real route_findings(),
never a second implementation of either. Nothing invents an ERROR/QUEUE gate here: a
legacy-dated incomplete note still surfaces as a QUEUE finding rather than being
dropped, because that split is bm_vault_contract's own classify(), consumed as-is,
never re-derived and never escalated to a block.

No em or en dashes anywhere in this file.
"""
import contextlib
import datetime
import io
import json
import os
import shutil
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import bm_vault_census_ext as ext  # noqa: E402
import bm_vault_shapes as shapes  # noqa: E402


def write(vault, relpath, text):
    path = os.path.join(vault, relpath)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(text)


def frontmatter(fields, body="body\n"):
    lines = ["---"] + ["%s: %s" % (k, v) for k, v in fields] + ["---", "", body]
    return "\n".join(lines)


def entity_note(name, hierarchy_edges=None):
    # type "index" is explicitly out of scope for bm_vault_contract's CONTRACT table
    # (its own docstring names finding/overview/index as unscoped), so these hierarchy
    # fixtures never also pick up an unrelated attribute_completeness finding.
    lines = ["---", "type: index", "entity: %s" % name]
    if hierarchy_edges:
        lines.append("hierarchy_edges: [%s]" % ", ".join(hierarchy_edges))
    lines += ["---", "", "# " + name]
    return "\n".join(lines) + "\n"


def owners_json(vault, domains):
    write(vault, os.path.join("99-System", "owners.json"), json.dumps({"domains": domains}))


TODAY = datetime.date(2026, 8, 30)


# ---------------------------------------------------------------------------
# Hierarchy dimensions: seeded defect and NO-DATA, per dimension, via direct
# function calls against bm_vault_shapes's own load() output (real parsing,
# never a hand-rolled entry dict, so the parsing contract is exercised too).
# ---------------------------------------------------------------------------

class HierarchyFixture(unittest.TestCase):

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="bm-census-ext-")
        self.vault = os.path.join(self.tmp, "vault")
        os.makedirs(self.vault)
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)

    def _load(self):
        return shapes.load(self.vault)


class OrphanNodesDimension(HierarchyFixture):

    def test_seeded_orphan_is_flagged(self):
        write(self.vault, "child.md", entity_note(
            "child", ["name=legal;parent=ghost-parent;valid_from=2020-01-01"]))
        hdecls, _p, _m, entity_stems, _f = self._load()
        total, findings = ext.hierarchy_orphans(hdecls, entity_stems)
        self.assertEqual(total, 1)
        self.assertEqual(len(findings), 1)
        self.assertIn("ghost-parent", findings[0]["detail"])
        self.assertEqual(findings[0]["path"], "child.md")

    def test_empty_domain_is_no_data(self):
        total, findings = ext.hierarchy_orphans([], set())
        self.assertEqual(total, 0)
        self.assertEqual(findings, [])

    def test_a_resolvable_parent_is_not_flagged(self):
        write(self.vault, "root.md", entity_note("root"))
        write(self.vault, "child.md", entity_note(
            "child", ["name=legal;parent=root;valid_from=2020-01-01"]))
        hdecls, _p, _m, entity_stems, _f = self._load()
        total, findings = ext.hierarchy_orphans(hdecls, entity_stems)
        self.assertEqual(total, 1)
        self.assertEqual(findings, [])


class MultiParentDimension(HierarchyFixture):

    def test_two_open_intervals_for_one_child_are_flagged(self):
        write(self.vault, "root.md", entity_note("root"))
        write(self.vault, "root2.md", entity_note("root2"))
        write(self.vault, "multi.md", entity_note("multi", [
            "name=legal;parent=root;valid_from=2020-01-01",
            "name=legal;parent=root2;valid_from=2020-06-01",
        ]))
        hdecls, _p, _m, _stems, _f = self._load()
        total, findings = ext.hierarchy_multi_parent(shapes, hdecls)
        self.assertEqual(total, 2)
        self.assertEqual(len(findings), 1)
        self.assertEqual(findings[0]["path"], "multi.md")

    def test_empty_domain_is_no_data(self):
        total, findings = ext.hierarchy_multi_parent(shapes, [])
        self.assertEqual(total, 0)
        self.assertEqual(findings, [])

    def test_a_closed_then_open_succession_is_not_flagged(self):
        write(self.vault, "root.md", entity_note("root"))
        write(self.vault, "root2.md", entity_note("root2"))
        write(self.vault, "child.md", entity_note("child", [
            "name=legal;parent=root;valid_from=2020-01-01;valid_to=2021-01-01",
            "name=legal;parent=root2;valid_from=2021-01-02",
        ]))
        hdecls, _p, _m, _stems, _f = self._load()
        total, findings = ext.hierarchy_multi_parent(shapes, hdecls)
        self.assertEqual(total, 2)
        self.assertEqual(findings, [])


class ExpiredReferencedDimension(HierarchyFixture):

    def test_expired_edge_whose_parent_is_still_active_elsewhere_is_flagged(self):
        write(self.vault, "root.md", entity_note("root"))
        write(self.vault, "expired.md", entity_note("expired", [
            "name=legal;parent=root;valid_from=2018-01-01;valid_to=2019-01-01"]))
        write(self.vault, "successor.md", entity_note("successor", [
            "name=legal;parent=root;valid_from=2021-01-02"]))
        hdecls, _p, _m, _stems, _f = self._load()
        total, findings = ext.hierarchy_expired_referenced(shapes, hdecls, TODAY)
        self.assertEqual(total, 1)
        self.assertEqual(len(findings), 1)
        self.assertEqual(findings[0]["path"], "expired.md")

    def test_empty_domain_is_no_data(self):
        total, findings = ext.hierarchy_expired_referenced(shapes, [], TODAY)
        self.assertEqual(total, 0)
        self.assertEqual(findings, [])

    def test_an_expired_edge_whose_parent_is_dead_everywhere_is_not_flagged(self):
        write(self.vault, "root.md", entity_note("root"))
        write(self.vault, "expired.md", entity_note("expired", [
            "name=legal;parent=root;valid_from=2018-01-01;valid_to=2019-01-01"]))
        hdecls, _p, _m, _stems, _f = self._load()
        total, findings = ext.hierarchy_expired_referenced(shapes, hdecls, TODAY)
        self.assertEqual(total, 1)
        self.assertEqual(findings, [])


class WalkTestFailureDimension(HierarchyFixture):

    def test_a_cycle_is_flagged(self):
        write(self.vault, "c1.md", entity_note("c1", ["name=trade;parent=c2;valid_from=2020-01-01"]))
        write(self.vault, "c2.md", entity_note("c2", ["name=trade;parent=c1;valid_from=2020-01-01"]))
        hdecls, _p, _m, entity_stems, _f = self._load()
        per_name = ext.hierarchy_walk_failures(shapes, hdecls, entity_stems, TODAY)
        domain_size, findings = per_name["trade"]
        self.assertEqual(domain_size, 2)
        self.assertEqual(len(findings), 2, "both nodes in the cycle must be named")
        self.assertTrue(all("cycle" in f["detail"] for f in findings))

    def test_a_dangling_parent_is_flagged_as_not_traversable(self):
        write(self.vault, "orphan.md", entity_note(
            "orphan", ["name=trade;parent=nowhere;valid_from=2020-01-01"]))
        hdecls, _p, _m, entity_stems, _f = self._load()
        per_name = ext.hierarchy_walk_failures(shapes, hdecls, entity_stems, TODAY)
        domain_size, findings = per_name["trade"]
        self.assertEqual(domain_size, 1)
        self.assertEqual(len(findings), 1)
        self.assertIn("not traversable to a root", findings[0]["detail"])

    def test_reaching_a_node_with_no_further_edge_is_success(self):
        write(self.vault, "root.md", entity_note("root"))
        write(self.vault, "leaf.md", entity_note(
            "leaf", ["name=trade;parent=root;valid_from=2020-01-01"]))
        hdecls, _p, _m, entity_stems, _f = self._load()
        per_name = ext.hierarchy_walk_failures(shapes, hdecls, entity_stems, TODAY)
        domain_size, findings = per_name["trade"]
        self.assertEqual(domain_size, 1)
        self.assertEqual(findings, [])

    def test_empty_domain_is_no_data(self):
        per_name = ext.hierarchy_walk_failures(shapes, [], set(), TODAY)
        self.assertEqual(per_name, {}, "no hierarchy name at all: nothing to key on")


# ---------------------------------------------------------------------------
# Attribute dimensions, reading bm_vault_contract's own frontmatter helpers.
# ---------------------------------------------------------------------------

class AttributeFixture(unittest.TestCase):

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="bm-census-ext-attr-")
        self.vault = os.path.join(self.tmp, "vault")
        os.makedirs(self.vault)
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)
        contract_path = os.path.join(HERE, "bm_vault_contract.py")
        import importlib.util
        spec = importlib.util.spec_from_file_location("bm_vault_contract", contract_path)
        self.contract = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(self.contract)

    def _load_notes(self):
        return self.contract._load_notes(self.vault) or []


class CompletenessDimension(AttributeFixture):

    def test_a_legacy_incomplete_note_queues_never_blocks(self):
        # created well before ADOPTED (2026-08-30): legacy, so classify() marks it
        # QUEUE, never ERROR, and this module never escalates that itself.
        write(self.vault, "d.md", frontmatter([
            ("id", "n-1"), ("type", "decision"), ("owner", "alice"),
            ("created", "2020-01-01"), ("description", "x"),
        ]))  # missing "status"
        notes = self._load_notes()
        per_class = ext.attribute_completeness(self.contract, notes)
        total, findings = per_class["decision"]
        self.assertEqual(total, 1)
        self.assertEqual(len(findings), 1)
        self.assertIn("QUEUE", findings[0]["detail"])

    def test_a_complete_note_is_clean_with_nonzero_domain(self):
        write(self.vault, "f.md", frontmatter([
            ("id", "n-2"), ("type", "failure"), ("owner", "bob"),
            ("symptom", "x"), ("verified-by", "bob"), ("created", "2020-01-01"),
        ]))
        notes = self._load_notes()
        per_class = ext.attribute_completeness(self.contract, notes)
        total, findings = per_class["failure"]
        self.assertEqual(total, 1)
        self.assertEqual(findings, [])

    def test_a_class_with_zero_notes_is_no_data(self):
        write(self.vault, "f.md", frontmatter([
            ("id", "n-2"), ("type", "failure"), ("owner", "bob"),
            ("symptom", "x"), ("verified-by", "bob"), ("created", "2020-01-01"),
        ]))
        notes = self._load_notes()
        per_class = ext.attribute_completeness(self.contract, notes)
        total, findings = per_class["capture"]
        self.assertEqual(total, 0)
        self.assertEqual(findings, [])

    def test_every_contract_class_is_represented_even_when_absent(self):
        per_class = ext.attribute_completeness(self.contract, [])
        self.assertEqual(sorted(per_class), sorted(self.contract.CONTRACT))
        for cls in per_class:
            self.assertEqual(per_class[cls], (0, []))


class PlaceholderDimension(AttributeFixture):

    def test_a_zeroed_phone_is_flagged(self):
        write(self.vault, "a.md", frontmatter([
            ("id", "n-1"), ("type", "reference"), ("phone", "0000000000"),
        ]))
        notes = self._load_notes()
        total, findings = ext.attribute_placeholders(self.contract, notes)
        self.assertEqual(total, 1)
        self.assertEqual(len(findings), 1)
        self.assertIn("0000000000", findings[0]["detail"])

    def test_a_real_looking_phone_is_not_flagged(self):
        write(self.vault, "a.md", frontmatter([
            ("id", "n-1"), ("type", "reference"), ("phone", "090-1234-9876"),
        ]))
        notes = self._load_notes()
        total, findings = ext.attribute_placeholders(self.contract, notes)
        self.assertEqual(total, 1)
        self.assertEqual(findings, [])

    def test_no_phone_like_field_anywhere_is_no_data(self):
        write(self.vault, "a.md", frontmatter([("id", "n-1"), ("type", "reference")]))
        notes = self._load_notes()
        total, findings = ext.attribute_placeholders(self.contract, notes)
        self.assertEqual(total, 0)
        self.assertEqual(findings, [])


class StaleVerificationDimension(AttributeFixture):

    def setUp(self):
        super().setUp()
        staleness_path = os.path.join(HERE, "bm_vault_staleness.py")
        import importlib.util
        spec = importlib.util.spec_from_file_location("bm_vault_staleness", staleness_path)
        self.staleness = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(self.staleness)

    def test_a_far_past_verified_at_is_flagged_stale(self):
        write(self.vault, "c.md", frontmatter([
            ("id", "n-1"), ("type", "reference"), ("status", "standing"),
            ("created", "2020-01-01"), ("verified_at", "2020-01-01"),
        ]))
        per_class = ext.attribute_stale_verifications(self.staleness, self.vault, TODAY)
        total, findings = per_class["reference"]
        self.assertEqual(total, 1)
        self.assertEqual(len(findings), 1)
        self.assertIn("stale", findings[0]["detail"])

    def test_a_recent_verified_at_is_clean_with_nonzero_domain(self):
        write(self.vault, "c.md", frontmatter([
            ("id", "n-1"), ("type", "reference"), ("status", "standing"),
            ("created", "2020-01-01"), ("verified_at", "2026-08-20"),
        ]))
        per_class = ext.attribute_stale_verifications(self.staleness, self.vault, TODAY)
        total, findings = per_class["reference"]
        self.assertEqual(total, 1)
        self.assertEqual(findings, [])

    def test_no_verified_at_anywhere_is_no_data(self):
        write(self.vault, "c.md", frontmatter([
            ("id", "n-1"), ("type", "reference"), ("status", "standing"),
            ("created", "2020-01-01"),
        ]))
        per_class = ext.attribute_stale_verifications(self.staleness, self.vault, TODAY)
        self.assertEqual(per_class, {}, "no dated claim anywhere: nothing to age-check")


# ---------------------------------------------------------------------------
# Routing: real bm_vault_route machinery, folder dedupe proven, nothing dropped.
# ---------------------------------------------------------------------------

class RoutingThroughRealRoute(unittest.TestCase):

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="bm-census-ext-route-")
        self.vault = os.path.join(self.tmp, "vault")
        os.makedirs(self.vault)
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)
        owners_json(self.vault, {"10-Projects/foo": {"owner": "alice", "steward": "carol"}})
        # A dangling edge fires TWO hierarchy dimensions (orphan, then walk_failure
        # since the chain hits that same dangling parent) plus an attribute_placeholder
        # finding on a second note, all in the SAME folder (10-Projects/foo): three
        # findings from three sources across two notes must dedupe to one group,
        # count 3.
        write(self.vault, "10-Projects/foo/child.md", entity_note(
            "child", ["name=legal;parent=ghost;valid_from=2020-01-01"]))
        write(self.vault, "10-Projects/foo/contact.md", frontmatter([
            ("id", "n-1"), ("type", "index"), ("phone", "1111111111"),
        ]))

    def test_check_reports_findings_and_routes_them_to_the_owner(self):
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            rc = ext.cmd_check(self.vault, HERE, "unassigned", False, TODAY)
        self.assertEqual(rc, 1)
        out = buf.getvalue()
        self.assertIn("alice", out)

    def test_folder_dedupe_collapses_two_sources_to_one_group(self):
        dims, no_data, contract_mod, route_mod = ext.collect_dimensions(self.vault, HERE, TODAY)
        self.assertEqual(no_data, [], "every sibling this suite needs must load")
        findings = ext._all_findings(dims)
        owners_map, err = contract_mod.load_owners_map(self.vault)
        self.assertIsNone(err)
        routed = route_mod.route_findings(self.vault, findings, owners_map, contract_mod)
        groups = routed["alice"]
        self.assertEqual(len(groups), 1, groups)
        self.assertEqual(groups[0]["folder"], "10-Projects/foo")
        self.assertEqual(groups[0]["count"], 3)
        sources = sorted(f["source"] for f in groups[0]["findings"])
        self.assertEqual(sources, ["attribute_placeholder", "hierarchy_orphan", "hierarchy_walk_failure"])

    def test_removing_the_folder_dedupe_would_fail_this_count(self):
        # Three findings from three different sources (hierarchy_orphan,
        # hierarchy_walk_failure, attribute_placeholder) all land in one folder. A
        # dedupe grouping by (owner, folder, source) instead of (owner, folder) would
        # report three groups of count 1 each; the real one-group, count-3 shape
        # proves the fold happens by folder alone.
        dims, _no_data, contract_mod, route_mod = ext.collect_dimensions(self.vault, HERE, TODAY)
        findings = ext._all_findings(dims)
        owners_map, _err = contract_mod.load_owners_map(self.vault)
        routed = route_mod.route_findings(self.vault, findings, owners_map, contract_mod)
        self.assertEqual(len(routed["alice"]), 1, "dedupe must group by folder, not by source")
        self.assertNotEqual(len(routed["alice"]), 3, "a per-source grouping would report three")

    def test_removing_no_data_path_would_hide_an_empty_dimension(self):
        # A vault with no hierarchy_edges, placements or phone-like fields at all:
        # every hierarchy dimension and the placeholder dimension must say NO-DATA,
        # never silently read as "0 findings, clean". A dimension whose rendering
        # dropped the domain_size==0 branch would print "domain 0, 0 finding(s)"
        # instead, which this test would catch.
        empty_tmp = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, empty_tmp, ignore_errors=True)
        write(empty_tmp, "plain.md", frontmatter([("id", "n-1"), ("type", "index")]))
        dims, _no_data, _c, _r = ext.collect_dimensions(empty_tmp, HERE, TODAY)
        lines = ext._render_dimensions(dims)
        no_data_names = {line.split(":")[0].strip() for line in lines if "NO-DATA" in line}
        for name in ("hierarchy_orphan", "hierarchy_multi_parent",
                     "hierarchy_expired_referenced", "attribute_placeholder"):
            self.assertIn(name, no_data_names, lines)
        self.assertFalse(any("domain 0" in line for line in lines),
                          "a domain_size of 0 must render as NO-DATA, never a 0-count pass")


class NoDataNeverCrashes(unittest.TestCase):

    def test_a_missing_vault_is_no_data_exit_2(self):
        rc = ext.cmd_check("/no/such/vault/anywhere", HERE, "unassigned", False, TODAY)
        self.assertEqual(rc, 2)

    def test_a_missing_sibling_names_itself_no_data_never_crashes(self):
        tmp = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, tmp, ignore_errors=True)
        for name in ("bm_vault_census_ext.py", "bm_vault_contract.py",
                     "bm_vault_route.py", "bm_vault_lifecycle.py"):
            shutil.copy(os.path.join(HERE, name), os.path.join(tmp, name))
        vault = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, vault, ignore_errors=True)
        write(vault, "a.md", frontmatter([
            ("id", "n-1"), ("type", "reference"), ("status", "standing"),
            ("created", "2020-01-01"), ("owner", "alice"), ("description", "x"),
        ]))
        dims, no_data, contract_mod, route_mod = ext.collect_dimensions(vault, tmp, TODAY)
        self.assertIsNotNone(contract_mod)
        self.assertIsNotNone(route_mod)
        self.assertEqual(sorted(no_data), ["bm_vault_shapes", "bm_vault_staleness"])
        # every hierarchy dimension degrades to an explicit empty domain, never a crash
        self.assertEqual(dims["hierarchy_orphan"], {"domain_size": 0, "findings": []})
        self.assertEqual(dims["hierarchy_walk_failure"], {"per_key": {}})

    def test_all_dimensions_no_data_is_exit_2_overall(self):
        vault = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, vault, ignore_errors=True)
        # No .md files at all: bm_vault_contract._load_notes returns None, shapes
        # finds nothing declared, staleness walks nothing.
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            rc = ext.cmd_check(vault, HERE, "unassigned", False, TODAY)
        self.assertEqual(rc, 2)


if __name__ == "__main__":
    unittest.main()

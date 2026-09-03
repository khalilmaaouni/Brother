#!/usr/bin/env python3
"""Calibration for tools/bm_vault_route.py, WBS row VB10-03.

The property under test is the row's own done_check: fixture findings across three owners
emit three per-owner reports, folder-level dedupe collapses two findings in one folder to
one entry carrying a count, and an unowned finding lands in the named default lane rather
than being dropped. Every source is exercised through the REAL sibling tool, exactly the
survivorship suite's own convention (bm_vault_survivorship.py's test builds real fixture
notes and calls the real triage.scan(), never a mock).

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
import bm_vault_route as route  # noqa: E402


def write(vault, relpath, text):
    path = os.path.join(vault, relpath)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(text)


def owners_json(vault, domains):
    write(vault, os.path.join("99-System", "owners.json"),
          json.dumps({"domains": domains}))


def frontmatter(fields, body="body\n"):
    lines = ["---"] + ["%s: %s" % (k, v) for k, v in fields] + ["---", "", body]
    return "\n".join(lines)


class ThreeOwnersFixtureRoutesToThreeReports(unittest.TestCase):
    """alice: one doctor finding plus one census finding, DIFFERENT notes, SAME folder
    (10-Projects/foo) -> dedupes to one group, count 2. bob: a real triage contradiction
    across two notes, SAME folder (40-Failures) -> also one group, count 2. erin: a real
    stale note (50-Reference) -> one group, count 1. Nobody's fixture leans on more than
    one check class at a time, so a count that comes out wrong points at exactly one
    collector."""

    def setUp(self):
        self.vault = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.vault, ignore_errors=True)
        owners_json(self.vault, {
            "10-Projects/foo": {"owner": "alice", "steward": "carol"},
            "40-Failures": {"owner": "bob", "steward": "dana"},
            "50-Reference": {"owner": "erin", "steward": "frank"},
        })
        # alice, doctor only: BASE_REQUIRED (id, type, status, created) missing "status".
        # Fully compliant with the census contract (owner, description, created present)
        # so census finds nothing here.
        write(self.vault, "10-Projects/foo/a.md", frontmatter([
            ("id", "n-0000000000000001"), ("type", "reference"),
            ("created", "2020-01-01"), ("owner", "alice"), ("description", "x"),
        ]))
        # alice, census only: the census contract (id, owner, description, created)
        # missing "description". Fully compliant with lint's BASE_REQUIRED so doctor
        # finds nothing here.
        write(self.vault, "10-Projects/foo/a2.md", frontmatter([
            ("id", "n-0000000000000002"), ("type", "reference"),
            ("status", "standing"), ("created", "2020-01-01"), ("owner", "alice"),
        ]))
        # bob, triage: two notes, one disagreeing claim on the same subject, both fully
        # compliant with census and lint so only triage fires.
        write(self.vault, "40-Failures/b1.md", frontmatter([
            ("id", "n-0000000000000003"), ("type", "reference"),
            ("status", "standing"), ("created", "2020-01-01"),
            ("owner", "bob"), ("description", "x"),
        ], body="claim: the total is 100 [evidence: b1.md]\n"))
        write(self.vault, "40-Failures/b2.md", frontmatter([
            ("id", "n-0000000000000004"), ("type", "reference"),
            ("status", "standing"), ("created", "2020-01-01"),
            ("owner", "bob"), ("description", "x"),
        ], body="claim: the total is 200 [evidence: b2.md]\n"))
        # erin, rot: verified_at far past the reference horizon (365 days), otherwise
        # fully compliant so census/doctor find nothing here.
        write(self.vault, "50-Reference/c.md", frontmatter([
            ("id", "n-0000000000000005"), ("type", "reference"),
            ("status", "standing"), ("created", "2020-01-01"),
            ("owner", "erin"), ("description", "x"), ("verified_at", "2020-01-01"),
        ]))
        self.contract_mod = route._load_sibling("bm_vault_contract", HERE)
        self.owners_map, err = self.contract_mod.load_owners_map(self.vault)
        self.assertIsNone(err)
        self.findings, self.no_data = route.collect_all(self.vault, HERE)
        self.assertEqual(self.no_data, [], "every sibling this suite needs must load")
        self.routed = route.route_findings(
            self.vault, self.findings, self.owners_map, self.contract_mod)

    def test_three_owners_each_get_a_report(self):
        for owner in ("alice", "bob", "erin"):
            self.assertIn(owner, self.routed,
                          "missing report for %s, got %s" % (owner, sorted(self.routed)))

    def test_alice_folder_dedupes_doctor_and_census_to_one_count(self):
        groups = self.routed["alice"]
        self.assertEqual(len(groups), 1, groups)
        self.assertEqual(groups[0]["folder"], "10-Projects/foo")
        self.assertEqual(groups[0]["count"], 2)
        sources = sorted(f["source"] for f in groups[0]["findings"])
        self.assertEqual(sources, ["census", "doctor"])

    def test_bob_folder_dedupes_two_triage_findings_to_one_count(self):
        groups = self.routed["bob"]
        self.assertEqual(len(groups), 1, groups)
        self.assertEqual(groups[0]["folder"], "40-Failures")
        self.assertEqual(groups[0]["count"], 2)
        self.assertTrue(all(f["source"] == "triage" for f in groups[0]["findings"]))

    def test_erin_gets_the_rot_finding(self):
        groups = self.routed["erin"]
        self.assertEqual(len(groups), 1, groups)
        self.assertEqual(groups[0]["folder"], "50-Reference")
        self.assertEqual(groups[0]["count"], 1)
        self.assertEqual(groups[0]["findings"][0]["source"], "rot")

    def test_removing_the_folder_dedupe_would_fail_this_count(self):
        # The dedupe collapses PER (owner, folder), never per (owner, folder, source):
        # a naive per-source grouping would instead report two groups of count 1 each
        # for alice. Proven directly rather than by mutating the module.
        groups = self.routed["alice"]
        self.assertNotEqual(len(groups), 2, "dedupe must group by folder, not by source")

    def test_cli_route_reports_findings_and_exits_1(self):
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            rc = route.cmd_route(self.vault, None, route.DEFAULT_OWNER, False, HERE)
        self.assertEqual(rc, 1)
        out = buf.getvalue()
        for owner in ("alice", "bob", "erin"):
            self.assertIn(owner, out)

    def test_cli_route_json_mode_is_valid_json(self):
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            rc = route.cmd_route(self.vault, None, route.DEFAULT_OWNER, True, HERE)
        self.assertEqual(rc, 1)
        payload = json.loads(buf.getvalue())
        self.assertEqual(sorted(payload["routed"]), ["alice", "bob", "erin"])


class UnownedFindingsRouteToTheDefaultLane(unittest.TestCase):
    """A governance-queue finding naming no path (a vault-wide warn, not one note's
    problem) has no owner to resolve: it must land in DEFAULT_OWNER, never be dropped."""

    def setUp(self):
        self.vault = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.vault, ignore_errors=True)
        self.tiers_mod = route._load_sibling("bm_vault_tiers", HERE)
        self.assertIsNotNone(self.tiers_mod, "bm_vault_tiers must load for this fixture")
        self.tiers_mod.append_queue(
            self.vault, [({"path": None, "detail": "vault-wide warn"}, "rot")])
        self.contract_mod = route._load_sibling("bm_vault_contract", HERE)

    def test_governance_source_reads_the_pathless_line(self):
        findings = route.collect_governance(self.vault, self.tiers_mod)
        self.assertEqual(len(findings), 1)
        self.assertIsNone(findings[0]["path"])
        self.assertEqual(findings[0]["source"], "governance")

    def test_unowned_finding_lands_in_the_default_lane_never_dropped(self):
        findings = route.collect_governance(self.vault, self.tiers_mod)
        owners_map, err = self.contract_mod.load_owners_map(self.vault)
        self.assertIsNone(err)
        routed = route.route_findings(self.vault, findings, owners_map, self.contract_mod)
        self.assertIn(route.DEFAULT_OWNER, routed)
        unscoped = [g for g in routed[route.DEFAULT_OWNER] if g["folder"] == "UNSCOPED"]
        self.assertEqual(len(unscoped), 1)
        self.assertEqual(unscoped[0]["count"], 1)

    def test_a_named_default_owner_overrides_the_lane_name(self):
        findings = route.collect_governance(self.vault, self.tiers_mod)
        owners_map, _err = self.contract_mod.load_owners_map(self.vault)
        routed = route.route_findings(self.vault, findings, owners_map, self.contract_mod,
                                       default_owner="triage-team")
        self.assertIn("triage-team", routed)
        self.assertNotIn(route.DEFAULT_OWNER, routed)

    def test_removing_the_default_lane_would_drop_the_finding(self):
        # A route_findings that skipped a None owner instead of falling back to
        # default_owner would silently lose this finding. Proven directly: every
        # finding this test fed in must reappear inside SOME group's findings list.
        findings = route.collect_governance(self.vault, self.tiers_mod)
        owners_map, _err = self.contract_mod.load_owners_map(self.vault)
        routed = route.route_findings(self.vault, findings, owners_map, self.contract_mod)
        all_routed = [f for groups in routed.values() for g in groups for f in g["findings"]]
        self.assertEqual(len(all_routed), len(findings))


class ANoteLevelOwnerOverrideBeatsTheFolderTable(unittest.TestCase):
    def setUp(self):
        self.vault = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.vault, ignore_errors=True)
        owners_json(self.vault, {"40-Failures": {"owner": "bob", "steward": "dana"}})
        write(self.vault, "40-Failures/x.md", frontmatter([
            ("id", "n-0000000000000006"), ("type", "reference"),
            ("status", "standing"), ("created", "2020-01-01"),
            ("owner", "zoe"), ("description", "x"),
        ]))
        self.contract_mod = route._load_sibling("bm_vault_contract", HERE)
        self.owners_map, err = self.contract_mod.load_owners_map(self.vault)
        self.assertIsNone(err)

    def test_note_level_owner_wins_over_the_domain_table(self):
        owner, _steward = route.owner_of(
            self.vault, "40-Failures/x.md", self.owners_map, self.contract_mod)
        self.assertEqual(owner, "zoe")

    def test_a_note_with_no_override_falls_back_to_the_domain_table(self):
        write(self.vault, "40-Failures/y.md", frontmatter([
            ("id", "n-0000000000000007"), ("type", "reference"),
            ("status", "standing"), ("created", "2020-01-01"), ("description", "x"),
        ]))
        owner, _steward = route.owner_of(
            self.vault, "40-Failures/y.md", self.owners_map, self.contract_mod)
        self.assertEqual(owner, "bob")

    def test_steward_resolves_independently_and_may_differ_from_owner(self):
        _owner, steward = route.owner_of(
            self.vault, "40-Failures/x.md", self.owners_map, self.contract_mod)
        self.assertEqual(steward, "dana")


class NoDataNeverACrash(unittest.TestCase):
    def test_a_missing_vault_is_no_data_exit_2(self):
        rc = route.cmd_route("/no/such/vault/anywhere", None, route.DEFAULT_OWNER, False)
        self.assertEqual(rc, 2)

    def test_a_missing_sibling_names_itself_no_data_never_crashes(self):
        tmp = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, tmp, ignore_errors=True)
        # Copy only what bm_vault_contract itself needs; every OTHER source's sibling
        # is deliberately absent, so collect_all must report them NO-DATA rather than
        # raise ImportError or AttributeError.
        for name in ("bm_vault_contract.py", "bm_vault_lifecycle.py"):
            shutil.copy(os.path.join(HERE, name), os.path.join(tmp, name))
        vault = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, vault, ignore_errors=True)
        write(vault, "a.md", frontmatter([
            ("id", "n-0000000000000008"), ("type", "reference"),
            ("status", "standing"), ("created", "2020-01-01"),
            ("owner", "alice"), ("description", "x"),
        ]))
        findings, no_data = route.collect_all(vault, tmp)
        self.assertEqual(findings, [])
        self.assertEqual(sorted(no_data),
                          sorted(["triage", "rot", "doctor", "governance", "posture"]))


if __name__ == "__main__":
    unittest.main()

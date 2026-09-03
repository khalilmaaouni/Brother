#!/usr/bin/env python3
"""Fixtures for `sbe review-route`. Run: python3 tools/test_sbe_review_route.py

Every test here builds a real git repository in a temporary directory and runs
the real command against it, the same discipline `tools/test_sbe_impact.py`
follows and for the same reason: the defect this control exists for lives at
the seam between a diff and a reviewer choice, and a mocked diff would test
the mock.

Coverage, by class:

  TestSevenPriorities        one fixture per routing rule (1-7), each firing
                              alone, asserting the exact reviewer it selects.
  TestTierSelectionTable      zero triggers is always zero reviewers; T0/T1
                              caps at one even with two triggers firing; T2/T3
                              allows two.
  TestAdversarialCases         the eight cases the routing brief names by name.
  TestNeverClaimsClean         the router's own law: no PASS, ever, and a
                              mechanical-only result still says so honestly.
  TestDeterminismAndRegistry   same diff twice is byte-identical, and the
                              routing table names exactly the seven reviewers
                              this repository ships as agents.
"""
import io
import json
import os
import subprocess
import sys
import tempfile
import shutil
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.abspath(os.path.join(HERE, ".."))
SBE = os.path.join(ROOT, "bin", "sbe")


def git(cwd, *args):
    out = subprocess.run(["git"] + list(args), cwd=cwd, capture_output=True, text=True)
    if out.returncode != 0:
        raise AssertionError("git %s failed in %s: %s" % (" ".join(args), cwd, out.stderr))
    return out.stdout.strip()


def write(cwd, rel, body):
    path = os.path.join(cwd, rel)
    directory = os.path.dirname(path)
    if directory and not os.path.isdir(directory):
        os.makedirs(directory)
    with io.open(path, "w", encoding="utf-8") as fh:
        fh.write(body)
    return path


class RouteFixture(unittest.TestCase):
    """A fresh repository per test, with one base commit already in it."""

    def setUp(self):
        self.repo = tempfile.mkdtemp()
        git(self.repo, "init", "-q")
        git(self.repo, "config", "user.email", "fixture@example.invalid")
        git(self.repo, "config", "user.name", "fixture")
        write(self.repo, "README.md", "base\n")
        git(self.repo, "add", "-A")
        git(self.repo, "commit", "-qm", "base")
        self.base = git(self.repo, "rev-parse", "HEAD")

    def tearDown(self):
        shutil.rmtree(self.repo, ignore_errors=True)

    def commit(self, message="change"):
        git(self.repo, "add", "-A")
        git(self.repo, "commit", "-qm", message)
        return git(self.repo, "rev-parse", "HEAD")

    def run_route(self, *extra):
        argv = [sys.executable, SBE, "review-route", self.repo, "--base", self.base, "--json"]
        out = subprocess.run(argv + list(extra), capture_output=True, text=True)
        data = json.loads(out.stdout) if out.stdout.strip().startswith("{") else None
        return out.returncode, data, out.stdout + out.stderr

    def write_bad_receipt(self, kind="gate"):
        """A receipt that parses, declares the right kind, and fails its own
        seal check: `_check_seal` in `brothersbe.evidence` recomputes the
        digest over the sealed fields and refuses a mismatch, which is
        exactly the shape a hand-typed or stale receipt takes. Placed at the
        fixed path `sbe status` and `sbe evidence verify` already read."""
        head = git(self.repo, "rev-parse", "HEAD")
        receipt = {
            "schemaVersion": "1.2", "generator": "sbe evidence run", "generatorVersion": "0.0.0",
            "runId": "0" * 64, "argv": ["true"], "argvRedactions": 0,
            "startedAt": "2020-01-01T00:00:00Z", "endedAt": "2020-01-01T00:00:01Z",
            "durationSeconds": 1.0, "exitCode": 0, "headCommit": head,
            "stdoutSha256": "0" * 64, "stderrSha256": "0" * 64, "environment": "test",
            "toolVersions": {"python": "3.9", "sbe": "0.0.0"}, "workingTreeDirty": False,
            "coveredFilesSource": "explicit --covers", "checkKinds": [kind],
            "checkKindsSource": "declared by --kind at generation time: %s" % kind,
            "coveredFiles": [],
        }
        write(self.repo, ".sbe/evidence/%s.json" % kind, json.dumps(receipt))


class TestSevenPriorities(RouteFixture):
    """One fixture per rule, each firing ALONE, so each test pins the exact
    reviewer its own rule selects with nothing else in the diff to confound
    it."""

    def test_rule_1_migration(self):
        # A path-shaped migration hit only: no CREATE/ALTER/DROP keyword in
        # the content, so this stays isolated to the db-migration detector
        # (path alone) and never also trips sql-ddl (content) or this
        # router's own migration-content detector.
        write(self.repo, "migrations/0001_add_users.sql", "-- initial migration, see ADR-12\n")
        self.commit()
        code, data, text = self.run_route()
        self.assertEqual(code, 0, text)
        self.assertEqual(data["primaryReviewer"], "migration-reviewer", text)
        self.assertIsNone(data["secondaryReviewer"], text)

    def test_rule_2_security(self):
        write(self.repo, "config/secret_config.py", "TOKEN = 'placeholder'\n")
        self.commit()
        code, data, text = self.run_route()
        self.assertEqual(code, 0, text)
        self.assertEqual(data["primaryReviewer"], "security-reviewer", text)

    def test_rule_3_data(self):
        write(self.repo, "queries/report.sql", "CREATE VIEW revenue AS SELECT 1;\n")
        self.commit()
        code, data, text = self.run_route()
        self.assertEqual(code, 0, text)
        self.assertEqual(data["primaryReviewer"], "data-reviewer", text)

    def test_rule_4_backend(self):
        write(self.repo, "api/openapi.yaml", "openapi: 3.0.0\npaths:\n  /orders: {}\n")
        self.commit()
        code, data, text = self.run_route()
        self.assertEqual(code, 0, text)
        self.assertEqual(data["primaryReviewer"], "backend-reviewer", text)

    def test_rule_5_infrastructure(self):
        write(self.repo, "infra/main.tf", "resource \"null_resource\" \"x\" {}\n")
        self.commit()
        code, data, text = self.run_route()
        self.assertEqual(code, 0, text)
        self.assertEqual(data["primaryReviewer"], "principal-architect", text)

    def test_rule_6_qa(self):
        """No weakening signal, just a significant test-only diff: 25 fresh
        assertions is enough to cross QA_SIGNIFICANT_LINES on its own."""
        lines = ["import unittest\n", "class T(unittest.TestCase):\n"]
        for i in range(12):
            lines.append("    def test_%d(self):\n        self.assertEqual(%d, %d)\n" % (i, i, i))
        write(self.repo, "tests/test_generated.py", "".join(lines))
        self.commit()
        code, data, text = self.run_route()
        self.assertEqual(code, 0, text)
        self.assertEqual(data["primaryReviewer"], "qa-reviewer", text)
        self.assertEqual(data["tier"], "T0", "a pure test-only diff must not invent a tier "
                         "no impact detector actually raised: %s" % text)

    def test_rule_7_evidence(self):
        self.write_bad_receipt("gate")
        write(self.repo, "README.md", "base\nmore words\n")
        self.commit()
        code, data, text = self.run_route()
        self.assertEqual(code, 0, text)
        self.assertEqual(data["primaryReviewer"], "evidence-auditor", text)


class TestTierSelectionTable(RouteFixture):
    def test_zero_triggers_is_zero_reviewers_at_any_tier(self):
        write(self.repo, "README.md", "base\nan ordinary sentence.\n")
        self.commit()
        code, data, text = self.run_route()
        self.assertEqual(code, 0, text)
        self.assertEqual(data["tier"], "T0", text)
        self.assertIsNone(data["primaryReviewer"], text)
        self.assertIsNone(data["secondaryReviewer"], text)
        self.assertTrue(data["mechanicalOnly"], text)

    def test_t1_caps_at_one_reviewer_even_with_two_triggers(self):
        """`queue-config` (backend, rule 4) and `infrastructure` (rule 5)
        both set the SAME intake answer (`crosses_boundary`), so together
        they still only reach T1, and T1's own cap of one reviewer has to
        win over rule 4's ordinary priority against rule 5: only backend is
        selected, and infrastructure is named lost, not silently dropped."""
        write(self.repo, "src/queue/kafka_consumer.py", "def handle(): pass\n")
        write(self.repo, "infra/main.tf", "resource \"null_resource\" \"x\" {}\n")
        self.commit()
        code, data, text = self.run_route()
        self.assertEqual(code, 0, text)
        self.assertEqual(data["tier"], "T1", text)
        self.assertEqual(data["primaryReviewer"], "backend-reviewer", text)
        self.assertIsNone(data["secondaryReviewer"], text)
        self.assertTrue(
            any("infrastructure" in u["reason"] for u in data["unmeasured"]),
            "the losing trigger must be named in unmeasured, not dropped: %s" % text)

    def test_t2_allows_two_reviewers(self):
        write(self.repo, "api/openapi.yaml", "openapi: 3.0.0\n")
        write(self.repo, "models/revenue.sql", "select 1 as revenue\n")
        self.commit()
        code, data, text = self.run_route()
        self.assertEqual(code, 0, text)
        self.assertEqual(data["tier"], "T2", text)
        self.assertEqual(data["primaryReviewer"], "data-reviewer", text)
        self.assertEqual(data["secondaryReviewer"], "backend-reviewer", text)


class TestAdversarialCases(RouteFixture):
    """The eight cases the routing brief names, one test each, named to match."""

    def test_migration_hidden_in_a_generic_file_name(self):
        write(self.repo, "scripts/cleanup_job.py",
             "def run():\n    execute('ALTER TABLE users ADD COLUMN active BOOLEAN;')\n")
        self.commit()
        code, data, text = self.run_route()
        self.assertEqual(code, 0, text)
        self.assertEqual(data["primaryReviewer"], "migration-reviewer",
                         "a migration hidden in a script must still route to "
                         "migration-reviewer: %s" % text)

    def test_api_contract_removed_rather_than_added(self):
        write(self.repo, "api/openapi.yaml",
             "openapi: 3.0.0\npaths:\n  /orders: {}\n  /refunds: {}\n")
        self.commit()
        base2 = git(self.repo, "rev-parse", "HEAD")
        write(self.repo, "api/openapi.yaml", "openapi: 3.0.0\npaths:\n  /orders: {}\n")
        self.commit("remove refunds endpoint")
        argv = [sys.executable, SBE, "review-route", self.repo, "--base", base2, "--json"]
        out = subprocess.run(argv, capture_output=True, text=True)
        data = json.loads(out.stdout)
        self.assertEqual(data["primaryReviewer"], "backend-reviewer",
                         "removing a contract endpoint must route the same as adding one: %s"
                         % out.stdout)

    def test_sql_embedded_in_python(self):
        write(self.repo, "src/reporting/query.py",
             "def totals(db):\n"
             "    return db.execute(\"SELECT id, total FROM orders WHERE total > 100\")\n")
        self.commit()
        code, data, text = self.run_route()
        self.assertEqual(code, 0, text)
        self.assertEqual(data["primaryReviewer"], "data-reviewer",
                         "SQL embedded in a .py file must route to data-reviewer even though "
                         "the path-only sql-ddl detector requires a .sql extension: %s" % text)

    def test_secret_like_fixture_values(self):
        write(self.repo, "src/config/defaults.py",
             "GITHUB_TOKEN = 'ghp_abcdefghijklmnopqrstuvwxyz012345'\n")
        self.commit()
        code, data, text = self.run_route()
        self.assertEqual(code, 0, text)
        self.assertEqual(data["primaryReviewer"], "security-reviewer",
                         "a secret-shaped value in a normally named file must still route to "
                         "security-reviewer: %s" % text)

    def test_docs_that_change_a_control_promise(self):
        write(self.repo, "docs/SECURITY.md",
             "# Security\nAll requests must be authenticated before reaching the payment "
             "service.\n")
        self.commit()
        base2 = git(self.repo, "rev-parse", "HEAD")
        write(self.repo, "docs/SECURITY.md",
             "# Security\nRequests are typically checked before reaching the payment "
             "service.\n")
        self.commit("soften the security doc")
        argv = [sys.executable, SBE, "review-route", self.repo, "--base", base2, "--json"]
        out = subprocess.run(argv, capture_output=True, text=True)
        data = json.loads(out.stdout)
        self.assertEqual(data["primaryReviewer"], "security-reviewer",
                         "a removed control promise in documentation must route to "
                         "security-reviewer even though impact.py treats .md as quiet: %s"
                         % out.stdout)

    def test_test_only_change_that_weakens_an_assertion(self):
        write(self.repo, "tests/test_orders.py",
             "def test_order_total():\n"
             "    total = compute_total()\n"
             "    assert total == 100\n")
        self.commit()
        base2 = git(self.repo, "rev-parse", "HEAD")
        write(self.repo, "tests/test_orders.py",
             "def test_order_total():\n"
             "    total = compute_total()\n"
             "    # assertion removed during a refactor\n")
        self.commit("weaken the assertion")
        argv = [sys.executable, SBE, "review-route", self.repo, "--base", base2, "--json"]
        out = subprocess.run(argv, capture_output=True, text=True)
        data = json.loads(out.stdout)
        self.assertEqual(data["primaryReviewer"], "qa-reviewer",
                         "a single removed assertion in a test-only diff must still route to "
                         "qa-reviewer, at any size: %s" % out.stdout)

    def test_mixed_backend_and_data_change(self):
        write(self.repo, "api/openapi.yaml", "openapi: 3.0.0\n")
        write(self.repo, "models/revenue.sql", "select 1 as revenue\n")
        self.commit()
        code, data, text = self.run_route()
        self.assertEqual(code, 0, text)
        self.assertEqual(
            sorted([data["primaryReviewer"], data["secondaryReviewer"]]),
            sorted(["backend-reviewer", "data-reviewer"]),
            "a mixed backend and data change must select both, not silently pick one: %s"
            % text)

    def test_three_critical_triggers_and_only_two_reviewer_slots(self):
        write(self.repo, "migrations/0002_add_flag.sql", "ALTER TABLE users ADD COLUMN x INT;\n")
        write(self.repo, "config/secret_keys.py", "KEY = 'placeholder'\n")
        write(self.repo, "reports/kpi.sql", "CREATE VIEW kpi AS SELECT 1;\n")
        self.commit()
        code, data, text = self.run_route()
        self.assertEqual(code, 0, text)
        self.assertEqual(data["primaryReviewer"], "migration-reviewer", text)
        self.assertEqual(data["secondaryReviewer"], "security-reviewer", text)
        self.assertTrue(
            any("data" in u["reason"] and "lost its slot" in u["reason"]
                for u in data["unmeasured"]),
            "the third trigger (data, rule 3) must be named as having lost its slot, never "
            "dropped in silence: %s" % text)


class TestNeverClaimsClean(RouteFixture):
    def test_verdict_is_never_the_gate_vocabulary(self):
        write(self.repo, "README.md", "base\nmore words\n")
        self.commit()
        code, data, text = self.run_route()
        self.assertEqual(code, 0, text)
        self.assertIn(data["verdict"], ("ROUTED", "NO-DATA"), text)
        self.assertNotIn(data["verdict"], ("PASS", "FAIL", "WAIVED", "REVIEW-REQUIRED"), text)

    def test_zero_reviewers_is_a_legal_result_not_an_error(self):
        write(self.repo, "README.md", "base\nmore words\n")
        self.commit()
        code, data, text = self.run_route()
        self.assertEqual(code, 0, text)
        self.assertTrue(data["mechanicalOnly"], text)
        self.assertTrue(
            any("legal result" in r for r in data["reasons"]),
            "a mechanical-only route must say in its own words that zero reviewers is a "
            "legal result, never an unexplained absence: %s" % text)

    def test_no_diff_is_no_data_never_a_crash_or_a_pass(self):
        code, data, text = self.run_route("--base", self.base, "--head", self.base)
        self.assertEqual(code, 0, text)
        self.assertEqual(data["tier"], "T0", text)

    def test_an_unsupported_file_type_is_named_unmeasured(self):
        write(self.repo, "assets/logo.svg", "<svg></svg>\n")
        self.commit()
        code, data, text = self.run_route()
        self.assertEqual(code, 0, text)
        self.assertTrue(
            any(u["file"] == "assets/logo.svg" for u in data["unmeasured"]),
            "a file type nothing here can read must be named by path in unmeasured: %s" % text)


class TestLowRiskFastPath(RouteFixture):
    """R1.2: a small, reversible, single-boundary change (T0/T1, zero
    triggers) must produce one plain-language line saying existing checks
    are sufficient and no specialist review is required. A T2/T3 change, or
    a T0/T1 change that DID trip a trigger, must never carry that line: the
    fast path exists to be trusted, and a line that ever shows up on a
    change that needed a look would make it noise, not a promise.
    """

    def test_a_trivial_doc_only_t0_change_gets_the_sufficiency_line(self):
        write(self.repo, "README.md", "base\nan ordinary sentence, nothing more.\n")
        self.commit()
        code, data, text = self.run_route()
        self.assertEqual(code, 0, text)
        self.assertEqual(data["tier"], "T0", text)
        self.assertTrue(data["mechanicalOnly"], text)
        self.assertTrue(data["lowRiskFastPath"], text)
        argv = [sys.executable, SBE, "review-route", self.repo, "--base", self.base]
        plain_out = subprocess.run(argv, capture_output=True, text=True).stdout
        self.assertIn(
            "LOW-RISK FAST PATH: tier T0, no reviewer trigger fired. This router is not "
            "routing a specialist review for this change; it did not read the check "
            "registry (.sbe/checks.yml) and takes no position on what it would find there "
            "-- the registry itself is the one place that question is answered.",
            plain_out, plain_out)

    def test_a_t2_change_never_gets_the_sufficiency_line(self):
        write(self.repo, "api/openapi.yaml", "openapi: 3.0.0\n")
        write(self.repo, "models/revenue.sql", "select 1 as revenue\n")
        self.commit()
        code, data, text = self.run_route()
        self.assertEqual(code, 0, text)
        self.assertEqual(data["tier"], "T2", text)
        self.assertFalse(data["lowRiskFastPath"], text)
        argv = [sys.executable, SBE, "review-route", self.repo, "--base", self.base]
        plain_out = subprocess.run(argv, capture_output=True, text=True).stdout
        self.assertNotIn("LOW-RISK FAST PATH", plain_out, plain_out)

    def test_a_t1_change_that_did_trip_a_trigger_never_gets_the_line(self):
        """One trigger at T1 already claims the single reviewer slot; the
        fast path is for ZERO triggers, not merely a low tier."""
        write(self.repo, "src/queue/kafka_consumer.py", "def handle(): pass\n")
        self.commit()
        code, data, text = self.run_route()
        self.assertEqual(code, 0, text)
        self.assertEqual(data["tier"], "T1", text)
        self.assertIsNotNone(data["primaryReviewer"], text)
        self.assertFalse(data["lowRiskFastPath"], text)
        argv = [sys.executable, SBE, "review-route", self.repo, "--base", self.base]
        plain_out = subprocess.run(argv, capture_output=True, text=True).stdout
        self.assertNotIn("LOW-RISK FAST PATH", plain_out, plain_out)

    def test_calibration_a_declared_t2_with_zero_triggers_is_not_the_fast_path(self):
        """The real case the gate has to catch: a human DECLARED this T2/T3
        through 00-intake.json (money, partner data, an irreversible step),
        while the diff itself is a trivial doc-only change no detector here
        flags. `mechanicalOnly` is True (zero triggers), but the tier is
        NOT T0/T1, so `lowRiskFastPath` must be False -- gating on
        `mechanicalOnly` alone, without also reading the tier, would wrongly
        call this the fast path. This is the forced-wrong-case check: with
        the tier check removed from `_is_low_risk_fast_path`, this exact
        test goes red (see the calibration note in the return report)."""
        write(self.repo, "00-intake.json", json.dumps({"answers": {
            "changes_contract": False, "crosses_boundary": False,
            "reversible_under_hour": True, "touches_sensitive": True,
            "consumers": "none",
        }}))
        write(self.repo, "README.md", "base\nan ordinary sentence, nothing more.\n")
        self.commit()
        code, data, text = self.run_route()
        self.assertEqual(code, 0, text)
        self.assertEqual(data["tier"], "T3", text)
        self.assertTrue(data["mechanicalOnly"], text)
        self.assertFalse(data["lowRiskFastPath"],
                         "a declared T3 with zero diff-detected triggers must never read as "
                         "the low-risk fast path: %s" % text)
        argv = [sys.executable, SBE, "review-route", self.repo, "--base", self.base]
        plain_out = subprocess.run(argv, capture_output=True, text=True).stdout
        self.assertNotIn("LOW-RISK FAST PATH", plain_out, plain_out)

    def test_the_fast_path_line_never_vouches_for_checks_it_never_read(self):
        """Hostile-review finding C: this router (`grep -c 'checks.yml'
        src/brothersbe/reviewroute.py` is 0) never opens the check registry,
        so the fast-path line must never claim existing checks are
        sufficient -- only that it decided not to route a specialist. Both
        halves are checked: the sufficiency claim must be gone, and the
        routing decision must still be named in plain words, or this
        collapses into a line that says nothing at all."""
        write(self.repo, "README.md", "base\nan ordinary sentence, nothing more.\n")
        self.commit()
        argv = [sys.executable, SBE, "review-route", self.repo, "--base", self.base]
        plain_out = subprocess.run(argv, capture_output=True, text=True).stdout
        self.assertIn("LOW-RISK FAST PATH", plain_out, plain_out)
        self.assertNotIn("checks are sufficient", plain_out.lower(), plain_out)
        self.assertIn(
            "not routing a specialist review", plain_out,
            "the fast-path line must still name the actual routing decision: %s" % plain_out)

    def test_the_fast_path_line_and_an_unmeasured_py_file_cannot_contradict(self):
        """Hostile-review finding C's exact fixture: a plain `.py` file no
        `impact` path detector covers (not a migration, not SQL, not a
        secret, not a test) prints an UNMEASURED line honestly saying this
        tool did not read it -- and, at T0 with zero triggers, the SAME run
        also prints the LOW-RISK FAST PATH line. Before the fix these two
        lines contradicted each other in one output: one said "did not read
        it", the other said "existing checks are sufficient". This pins
        that they cannot contradict any more: the fast-path line never
        claims sufficiency, so nothing here is left for the UNMEASURED line
        to contradict."""
        write(self.repo, "src/brothersbe/moneypath.py", "def handle():\n    pass\n")
        self.commit()
        code, data, text = self.run_route()
        self.assertEqual(code, 0, text)
        self.assertEqual(data["tier"], "T0", text)
        self.assertTrue(data["lowRiskFastPath"], text)
        self.assertTrue(
            any(u["file"] == "src/brothersbe/moneypath.py" and "did not read it" in u["reason"]
                for u in data["unmeasured"]),
            "expected an UNMEASURED entry naming the unread .py file: %s" % text)
        argv = [sys.executable, SBE, "review-route", self.repo, "--base", self.base]
        plain_out = subprocess.run(argv, capture_output=True, text=True).stdout
        self.assertIn("UNMEASURED src/brothersbe/moneypath.py", plain_out, plain_out)
        self.assertIn("did not read it and is not reporting it as clean", plain_out, plain_out)
        self.assertIn("LOW-RISK FAST PATH", plain_out, plain_out)
        self.assertNotIn("checks are sufficient", plain_out.lower(), plain_out)


class TestDeterminismAndRegistry(RouteFixture):
    def test_the_same_diff_routes_byte_identical_twice(self):
        write(self.repo, "api/openapi.yaml", "openapi: 3.0.0\n")
        write(self.repo, "migrations/0001_init.sql", "CREATE TABLE users (id INT);\n")
        self.commit()
        argv = [sys.executable, SBE, "review-route", self.repo, "--base", self.base, "--json"]
        first = subprocess.run(argv, capture_output=True, text=True).stdout
        second = subprocess.run(argv, capture_output=True, text=True).stdout
        self.assertEqual(first, second,
                         "the same diff must produce byte-identical output: no clock, no "
                         "random source, no model in the loop")

    def test_every_named_reviewer_is_one_of_the_seven_agent_files(self):
        sys.path.insert(0, os.path.join(ROOT, "src"))
        try:
            from brothersbe import reviewroute as mod
        finally:
            sys.path.pop(0)
        agents_dir = os.path.join(ROOT, "agents")
        on_disk = set(
            fn[:-3] for fn in os.listdir(agents_dir)
            if fn.endswith(".md") and fn != "implementation-worker.md")
        self.assertEqual(set(mod.REVIEWER_NAMES), on_disk,
                         "REVIEWER_NAMES must name exactly the reviewer agents on disk, "
                         "no more and no fewer")
        self.assertEqual(set(mod.REVIEWER_OF_TRIGGER.values()), set(mod.REVIEWER_NAMES),
                         "every reviewer the routing table can select must be one of the "
                         "seven; adding an eighth is one row in TRIGGER_RULES, never a "
                         "second table")

    def test_a_disposable_work_profile_is_recorded_but_never_decides(self):
        """`--work-profile` is accepted for provenance only: the same diff
        with and without it must select the same reviewers."""
        write(self.repo, "api/openapi.yaml", "openapi: 3.0.0\n")
        self.commit()
        _c1, plain, _t1 = self.run_route()
        _c2, profiled, _t2 = self.run_route("--work-profile", "backend")
        self.assertEqual(plain["primaryReviewer"], profiled["primaryReviewer"])
        self.assertEqual(plain["secondaryReviewer"], profiled["secondaryReviewer"])
        self.assertTrue(any("workProfile" in r for r in profiled["reasons"]))


class TestRoutedCountLedger(RouteFixture):
    """H7: `route()` persists a per-reviewer routed count in
    `.sbe/review-route-counts.json`, beside `.sbe/tasks.json`. The count is
    never folded into `route()`'s own returned dict (see
    TestDeterminismAndRegistry.test_the_same_diff_routes_byte_identical_twice,
    which this class must not break), so it only ever shows up in the
    text (non-JSON) render, read back from the ledger after the call's own
    increment."""

    def run_text_route(self):
        """{"code": exit code, "text": combined stdout+stderr}, never a bare
        2-tuple: this project's honesty meta-test
        (evals/test_no_data_class.py) reads any 2-tuple return as a possible
        (verdict, evidence) pair and refuses a function it cannot prove
        never returns PASS, the same reason `RouteFixture.run_route` above
        returns three named values rather than two."""
        argv = [sys.executable, SBE, "review-route", self.repo, "--base", self.base]
        out = subprocess.run(argv, capture_output=True, text=True)
        return {"code": out.returncode, "text": out.stdout + out.stderr}

    def test_the_third_route_to_one_reviewer_prints_a_count_of_three(self):
        result = {"code": None, "text": ""}
        for i in range(3):
            write(self.repo, "config/secret_config_%d.py" % i, "TOKEN = 'placeholder'\n")
            self.commit()
            result = self.run_text_route()
            self.assertEqual(result["code"], 0, result["text"])
        self.assertIn("security-reviewer (routed 3 time(s) total)", result["text"],
                      "the third route to the same reviewer must print a running "
                      "total of three: %r" % result["text"])

    def test_the_ledger_is_a_small_json_file_beside_tasks_json(self):
        write(self.repo, "config/secret_config.py", "TOKEN = 'placeholder'\n")
        self.commit()
        result = self.run_text_route()
        self.assertEqual(result["code"], 0, result["text"])
        ledger_path = os.path.join(self.repo, ".sbe", "review-route-counts.json")
        self.assertTrue(os.path.isfile(ledger_path),
                        "no ledger file was written: %s" % result["text"])
        with io.open(ledger_path, encoding="utf-8") as fh:
            data = json.load(fh)
        self.assertEqual(data.get("security-reviewer"), 1)

    def test_json_output_stays_byte_identical_even_though_the_ledger_increments(self):
        """The ledger write is a real side effect on disk; the JSON payload
        must not see it, or the pre-existing determinism test breaks."""
        write(self.repo, "config/secret_config.py", "TOKEN = 'placeholder'\n")
        self.commit()
        argv = [sys.executable, SBE, "review-route", self.repo, "--base", self.base, "--json"]
        first = subprocess.run(argv, capture_output=True, text=True).stdout
        second = subprocess.run(argv, capture_output=True, text=True).stdout
        self.assertEqual(first, second)
        self.assertNotIn("routed", first, "the routed count must never leak into --json")


class TestFreshContextReview(RouteFixture):
    """Plan section 6.4: one independent fresh-context review by default,
    fed only the seven named fields (never the implementation conversation),
    asking exactly three fixed questions, combined with an already-selected
    specialist rather than launched as a duplicate.
    """

    def _mod(self):
        sys.path.insert(0, os.path.join(ROOT, "src"))
        try:
            from brothersbe import reviewroute as mod
        finally:
            sys.path.pop(0)
        return mod

    def test_review_input_is_built_from_exactly_the_seven_named_fields(self):
        mod = self._mod()
        payload = mod.build_fresh_context_review_input(
            intent="ship the refund guard",
            behaviorContract="a retried export never duplicates the order",
            businessImpact="stops a double-refund incident class",
            risk="T2",
            diff="--- a/x.py\n+++ b/x.py\n@@\n-old\n+new\n",
            requiredProof="tools/test_sbe_impact.py::test_replay_same_id",
            evidenceSummaries=["gate PASS at 8c2acf35ecdc"])
        self.assertEqual(
            set(payload) - {"questions"}, set(mod.FRESH_CONTEXT_REVIEW_FIELDS),
            "the built input must carry exactly the seven named fields, no more: %r" % payload)

    def test_exactly_three_fixed_questions_verbatim(self):
        mod = self._mod()
        self.assertEqual(mod.FRESH_CONTEXT_QUESTIONS, (
            "Does the implementation satisfy the declared behavior?",
            "What important failure mode has no proof?",
            "Which assurance claim is stronger than its evidence?",
        ))
        payload = mod.build_fresh_context_review_input(
            intent="x", behaviorContract="y", businessImpact="z", risk="T0",
            diff="", requiredProof="p", evidenceSummaries=[])
        self.assertEqual(payload["questions"], list(mod.FRESH_CONTEXT_QUESTIONS))

    def test_smuggled_conversation_field_is_refused_not_silently_accepted(self):
        """The defect this closes: a caller passing the implementation
        conversation through some field name the builder was never told
        about must be refused, by name, in an exception a caller cannot
        miss -- never silently folded into the review input a fresh-context
        reviewer receives."""
        mod = self._mod()
        with self.assertRaises(mod.ReviewInputRejected) as ctx:
            mod.build_fresh_context_review_input(
                intent="x", behaviorContract="y", businessImpact="z", risk="T0",
                diff="", requiredProof="p", evidenceSummaries=[],
                conversation="the whole implementation chat transcript")
        self.assertIn("conversation", str(ctx.exception))
        self.assertIn("never the implementation conversation", str(ctx.exception))

    def test_fresh_context_review_combines_with_a_selected_specialist_never_duplicates(self):
        write(self.repo, "migrations/0001_add_users.sql", "-- initial migration\n")
        self.commit()
        code, data, text = self.run_route()
        self.assertEqual(code, 0, text)
        self.assertEqual(data["primaryReviewer"], "migration-reviewer", text)
        self.assertTrue(data["freshContextReview"]["required"], text)
        self.assertEqual(data["freshContextReview"]["combinedWith"], "migration-reviewer",
                         "risk routing already selected a specialist; the fresh-context review "
                         "must combine with it, never appear as a separate reviewer: %s" % text)
        # No duplicate: combining must never consume the secondary slot, and must never
        # introduce a reviewer name outside the seven this router already knows.
        self.assertIsNone(data["secondaryReviewer"], text)
        self.assertNotIn("fresh-context-reviewer", (data["primaryReviewer"] or "",
                                                     data["secondaryReviewer"] or ""))

    def test_fresh_context_review_runs_standalone_when_mechanical_only(self):
        write(self.repo, "README.md", "base\nmore words\n")
        self.commit()
        code, data, text = self.run_route()
        self.assertEqual(code, 0, text)
        self.assertIsNone(data["primaryReviewer"], text)
        self.assertTrue(data["freshContextReview"]["required"], text)
        self.assertIsNone(data["freshContextReview"]["combinedWith"],
                          "with no specialist selected, the fresh-context review runs "
                          "standalone as the one default review: %s" % text)

    def test_two_specialists_selected_still_combines_with_primary_only(self):
        """T2/T3 can select two specialists; the fresh-context review still
        combines with exactly the primary, never spawning a third reviewer
        of its own for the same change."""
        write(self.repo, "api/openapi.yaml", "openapi: 3.0.0\n")
        write(self.repo, "models/revenue.sql", "select 1 as revenue\n")
        self.commit()
        code, data, text = self.run_route()
        self.assertEqual(code, 0, text)
        self.assertEqual(data["tier"], "T2", text)
        self.assertEqual(data["primaryReviewer"], "data-reviewer", text)
        self.assertEqual(data["secondaryReviewer"], "backend-reviewer", text)
        self.assertEqual(data["freshContextReview"]["combinedWith"], "data-reviewer", text)


class TestSmuggledConversationNestedInPermittedFields(RouteFixture):
    """F-3a, the MECHANICAL half: `build_fresh_context_review_input`'s
    closure was over field NAMES only, so implementation-conversation
    material rode through untouched INSIDE any of the seven allowed fields.
    Two routes are mechanically catchable (a nested dict key, a nested key
    inside a list) and are proven refused here; the third (plain free text
    with no key at all) is proven NOT refused, because no mechanical scan
    can catch it -- see TestOverclaimCannotSilentlyReturn for the docstring
    that says so instead of staying silent about it.
    """

    def _mod(self):
        sys.path.insert(0, os.path.join(ROOT, "src"))
        try:
            from brothersbe import reviewroute as mod
        finally:
            sys.path.pop(0)
        return mod

    def test_conversation_nested_in_a_dict_field_is_refused(self):
        # RED before the fix: build_fresh_context_review_input's **smuggled
        # check only ever saw top-level keyword names, so
        # intent={"goal": ..., "conversation": TRANSCRIPT} sailed straight
        # through as a normal dict value with nothing to catch it.
        mod = self._mod()
        transcript = "user: implement X\nassistant: implemented X\n" * 50
        with self.assertRaises(mod.ReviewInputRejected) as ctx:
            mod.build_fresh_context_review_input(
                intent={"goal": "ship it", "conversation": transcript},
                behaviorContract="y", businessImpact="z", risk="T0", diff="",
                requiredProof="p", evidenceSummaries=[])
        self.assertIn("intent.conversation", str(ctx.exception), str(ctx.exception))

    def test_conversation_nested_in_a_list_field_is_refused(self):
        mod = self._mod()
        transcript = "the whole implementation chat transcript"
        with self.assertRaises(mod.ReviewInputRejected) as ctx:
            mod.build_fresh_context_review_input(
                intent="x", behaviorContract="y", businessImpact="z", risk="T0", diff="",
                requiredProof="p", evidenceSummaries=[{"transcript": transcript}])
        self.assertIn("evidenceSummaries[0].transcript", str(ctx.exception), str(ctx.exception))

    def test_free_text_in_a_permitted_field_cannot_be_and_is_not_refused(self):
        """The HONESTY half of F-3a: `intent=TRANSCRIPT` as a plain string
        carries no key name at all for a mechanical scan to find. This must
        NOT raise -- pretending to catch free text would be exactly the
        overclaim the finding named."""
        mod = self._mod()
        transcript = "the entire implementation conversation, verbatim, as one string"
        payload = mod.build_fresh_context_review_input(
            intent=transcript, behaviorContract="y", businessImpact="z", risk="T0", diff="",
            requiredProof="p", evidenceSummaries=[])
        self.assertEqual(payload["intent"], transcript)


class TestOverclaimCannotSilentlyReturn(RouteFixture):
    """F-3a's honesty half: the docstrings must name EXACTLY what is
    enforced (field-name closure plus the nested key-name scan) and what
    remains the caller's contract (free-text content). Pinned on the
    load-bearing words so a future edit that quietly widens the claim back
    to "solved" fails this test instead of shipping silently."""

    def _mod(self):
        sys.path.insert(0, os.path.join(ROOT, "src"))
        try:
            from brothersbe import reviewroute as mod
        finally:
            sys.path.pop(0)
        return mod

    def test_docstrings_name_the_honesty_boundary(self):
        mod = self._mod()
        for doc in (mod.ReviewInputRejected.__doc__, mod.build_fresh_context_review_input.__doc__):
            self.assertIn("CALLER'S CONTRACT", doc, doc)
            self.assertIn("mechanically indistinguishable", doc, doc)


class TestFreshContextReviewIsRouteProductionPath(RouteFixture):
    """F-3b: `build_fresh_context_review_input` had no production caller --
    `route()` built its own {required, combinedWith, questions} shape
    through a private helper that never touched the builder, so the guard
    was unreachable from any running command. Fixed by making `route()`
    build the freshContextReview payload itself through the builder, fed
    material `route()` already holds (an absent field reads "NO-DATA",
    never invented content)."""

    def _mod(self):
        sys.path.insert(0, os.path.join(ROOT, "src"))
        try:
            from brothersbe import reviewroute as mod
        finally:
            sys.path.pop(0)
        return mod

    def test_route_output_carries_the_builder_produced_payload(self):
        # RED before the fix: data["freshContextReview"] carried only
        # {required, combinedWith, questions} from the private
        # _fresh_context_review(primary) helper -- no "reviewInput" key at
        # all, because build_fresh_context_review_input was never called.
        mod = self._mod()
        write(self.repo, "README.md", "base\nan ordinary sentence, nothing more.\n")
        self.commit()
        data = mod.route(self.repo, base=self.base)
        review_input = data["freshContextReview"]["reviewInput"]
        self.assertEqual(
            set(review_input) - {"questions"}, set(mod.FRESH_CONTEXT_REVIEW_FIELDS),
            "route()'s freshContextReview must carry exactly the builder's seven fields: %r"
            % review_input)
        self.assertEqual(review_input["questions"], list(mod.FRESH_CONTEXT_QUESTIONS),
                         "the three fixed questions must ride through verbatim: %r"
                         % review_input)
        # Backward compatible: the pre-existing keys are unchanged, only added to.
        self.assertTrue(data["freshContextReview"]["required"])
        self.assertIsNone(data["freshContextReview"]["combinedWith"])

    def test_smuggling_refusal_is_reachable_from_route_inputs(self):
        """The reachability claim itself: a hostile 00-intake.json whose own
        `intent` block nests a conversation-class key must be refused --
        visibly, inside the output `route()` returns, the same function
        `sbe review-route` runs for every real command -- not only inside a
        unit test that calls the builder directly. Before F-3b this could
        never fire at all: route() never called the builder. R2-1's fix:
        `route()` itself must never RAISE over this (see
        TestReviewInputRefusalNeverCrashesRoute below for the crash this
        used to be, and the honest degrade that replaced it); the refusal
        instead rides visibly in the returned dict."""
        mod = self._mod()
        transcript = "the entire implementation conversation" * 20
        write(self.repo, "00-intake.json", json.dumps(
            {"intent": {"goal": "ship it", "conversation": transcript}}))
        write(self.repo, "README.md", "base\nan ordinary sentence, nothing more.\n")
        self.commit()
        data = mod.route(self.repo, base=self.base)
        self.assertEqual(data["verdict"], "ROUTED", data)
        review_input = data["freshContextReview"]["reviewInput"]
        self.assertEqual(review_input.get("verdict"), "NO-DATA", review_input)
        self.assertIn("intent.conversation", review_input.get("refused", ""), review_input)


class TestReviewInputRefusalNeverCrashesRoute(RouteFixture):
    """R2-1: round one's F-3b fix made `route()` call
    `build_fresh_context_review_input` in production, but that builder
    RAISES `ReviewInputRejected` on a refused field, and `route()` let that
    exception fly straight out of itself -- `cli.py`'s `_cmd_review_route`
    catches only `reviewroute.DiffUnavailable`, so a hostile 00-intake.json
    crashed the real `sbe review-route` command with exit 1, empty stdout,
    and a raw traceback, never a route at all. This contradicts `route()`'s
    own module-docstring contract: `verdict` is `ROUTED` or `NO-DATA`,
    never an uncaught exception. Fixed inside `reviewroute.py` alone (cli.py
    is out of this fence): `route()`'s own `_fresh_context_review` helper
    catches `ReviewInputRejected` from its own builder call and degrades
    honestly -- the route itself still completes (it is computable without
    the fresh-context payload), and `freshContextReview.reviewInput` becomes
    an explicit `{"verdict": "NO-DATA", "refused": <message>}` record
    instead of a silent drop or a fatal exception.

    The fixture below is the auditor's exact reproduction: a temp git repo,
    a 00-intake.json whose `intent` nests a `conversation` key, one commit
    after base, run through the real `bin/sbe review-route <dir> --base
    <BASE> --json` subprocess -- the same seam `cli.py` sits on, which a
    call directly into `reviewroute.route()` would skip past.
    """

    def test_hostile_intake_no_longer_crashes_the_real_command(self):
        write(self.repo, "00-intake.json", json.dumps(
            {"intent": {"desired_outcome": "ship it", "conversation": "who was in the room"}}))
        self.commit()
        argv = [sys.executable, SBE, "review-route", self.repo, "--base", self.base, "--json"]
        out = subprocess.run(argv, capture_output=True, text=True)
        # GREEN (post-fix): exit 0, stdout is parseable JSON, the route
        # itself still completed, and the refusal is visible rather than
        # silent or fatal.
        self.assertEqual(out.returncode, 0,
                         "a refused fresh-context review input must never crash the real "
                         "command: %s" % (out.stdout + out.stderr))
        data = json.loads(out.stdout)
        self.assertIn(data["verdict"], ("ROUTED", "NO-DATA"), out.stdout)
        review_input = data["freshContextReview"]["reviewInput"]
        self.assertEqual(review_input.get("verdict"), "NO-DATA", review_input)
        self.assertIn("intent.conversation", review_input.get("refused", ""), review_input)


class TestNormalizedKeyBlocklistCatchesNearMisses(RouteFixture):
    """R2-2, the mechanical half: round one's `_CONVERSATION_KEY_NAMES` was
    matched by bare `.lower()`, so a name that only differs from a listed
    one by underscore/hyphen/space (`chatLog` normalizes to `chatlog`,
    which round one's list never carried, even though `chat_log` -- listed
    -- normalizes to the exact same string) rode straight through, and a
    `tuple` nested inside a permitted field was never walked at all (only
    `dict` and `list` were). Both are fixed here: keys are normalized
    (lower-cased, separators stripped) before matching, the name set is
    extended with the plainly conversation-shaped near-misses, and tuples
    are walked the same as lists.
    """

    def _mod(self):
        sys.path.insert(0, os.path.join(ROOT, "src"))
        try:
            from brothersbe import reviewroute as mod
        finally:
            sys.path.pop(0)
        return mod

    def test_camelcase_chatlog_nested_in_a_dict_is_refused(self):
        # RED against round one: "chatLog".lower() == "chatlog", and
        # round one's list carried "chat_log" (which normalizes, under
        # THIS fix, to the same "chatlog") but never the bare "chatlog"
        # string a plain .lower() would produce, so this exact case sailed
        # through untouched even though the refusal message's own example
        # advertised chatLog as caught.
        mod = self._mod()
        transcript = "the whole implementation chat transcript, verbatim"
        with self.assertRaises(mod.ReviewInputRejected) as ctx:
            mod.build_fresh_context_review_input(
                intent={"goal": "ship it", "chatLog": transcript},
                behaviorContract="y", businessImpact="z", risk="T0", diff="",
                requiredProof="p", evidenceSummaries=[])
        self.assertIn("intent.chatLog", str(ctx.exception), str(ctx.exception))

    def test_hyphenated_and_spaced_variants_all_normalize_the_same(self):
        mod = self._mod()
        for variant in ("chat-log", "chat log", "chat_log", "CHATLOG"):
            with self.assertRaises(mod.ReviewInputRejected, msg=variant):
                mod.build_fresh_context_review_input(
                    intent={variant: "material"}, behaviorContract="y", businessImpact="z",
                    risk="T0", diff="", requiredProof="p", evidenceSummaries=[])

    def test_a_tuple_of_dicts_is_walked_the_same_as_a_list(self):
        # RED against round one: _find_conversation_key only recursed into
        # dict and list, so a tuple nested inside a permitted field was
        # never inspected at all, whatever key it carried.
        mod = self._mod()
        transcript = "the whole implementation chat transcript, verbatim"
        with self.assertRaises(mod.ReviewInputRejected) as ctx:
            mod.build_fresh_context_review_input(
                intent="x", behaviorContract="y", businessImpact="z", risk="T0", diff="",
                requiredProof="p", evidenceSummaries=({"transcript": transcript},))
        self.assertIn("evidenceSummaries[0].transcript", str(ctx.exception), str(ctx.exception))

    def test_a_tuple_of_dicts_is_refused_through_the_real_route(self):
        """The same tuple case, reachable from `route()` itself rather than
        only from a direct builder call, mirroring
        TestFreshContextReviewIsRouteProductionPath's reachability
        discipline for the dict/list cases."""
        mod = self._mod()
        transcript = "the entire implementation conversation" * 20
        write(self.repo, "00-intake.json", json.dumps(
            {"intent": {"goal": "ship it", "convo": [transcript]}}))
        write(self.repo, "README.md", "base\nan ordinary sentence, nothing more.\n")
        self.commit()
        data = mod.route(self.repo, base=self.base)
        review_input = data["freshContextReview"]["reviewInput"]
        self.assertEqual(review_input.get("verdict"), "NO-DATA", review_input)
        self.assertIn("intent.convo", review_input.get("refused", ""), review_input)

    def test_the_extended_near_miss_names_are_all_caught(self):
        """Every plainly conversation-shaped name the finding named by name,
        each nested alone so a regression in any single one of them fails
        this test rather than hiding behind the others."""
        mod = self._mod()
        near_misses = ("transcripts", "convo", "conversationlog", "messagehistory",
                      "sessiontranscript", "implementationchat", "dialog")
        for name in near_misses:
            with self.assertRaises(mod.ReviewInputRejected, msg=name):
                mod.build_fresh_context_review_input(
                    intent={name: "material"}, behaviorContract="y", businessImpact="z",
                    risk="T0", diff="", requiredProof="p", evidenceSummaries=[])


class TestHonestBlocklistDocstring(RouteFixture):
    """R2-2, the honesty half: the docstrings must say exactly what the scan
    is (a normalized name blocklist over nested dict/list/tuple keys,
    nothing more) rather than read as a general semantic detector. Pinned
    on the load-bearing words so a future edit that quietly widens the
    claim back toward "solved" fails this test instead of shipping
    silently."""

    def _mod(self):
        sys.path.insert(0, os.path.join(ROOT, "src"))
        try:
            from brothersbe import reviewroute as mod
        finally:
            sys.path.pop(0)
        return mod

    def test_docstrings_state_the_scan_is_a_normalized_name_blocklist_only(self):
        mod = self._mod()
        for doc in (mod.ReviewInputRejected.__doc__, mod.build_fresh_context_review_input.__doc__):
            # Whitespace- and case-normalized so a line-wrap or a caps/lower
            # rewording inside the phrase cannot silently defeat this pin
            # without changing its actual words.
            flat = " ".join(doc.split()).lower()
            self.assertIn("normalized name blocklist", flat, doc)
            self.assertIn("nested dict/list/tuple keys, nothing more", flat, doc)
        # The existing F-3a honesty pin still holds: this must never claim
        # to be a semantic detector of free text.
        for doc in (mod.ReviewInputRejected.__doc__, mod.build_fresh_context_review_input.__doc__):
            self.assertIn("CALLER'S CONTRACT", doc, doc)
            self.assertIn("mechanically indistinguishable", doc, doc)


if __name__ == "__main__":
    unittest.main(verbosity=2)

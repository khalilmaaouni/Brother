#!/usr/bin/env python3
"""Required evidence: the twelve fixtures that prove absence is judged.

Run: python3 tools/test_sbe_policy.py

WHAT IS UNDER TEST, in one sentence. `brothersbe.policy` derives what evidence a
change is REQUIRED to carry from the diff, which the author cannot omit, rather
than from an input somebody remembered to pass, and reports a requirement that
is required and absent as MISSING with a nonzero exit rather than as NO-DATA.

Every fixture here builds a real temporary git repository, writes real files,
runs the real `bin/sbe policy evaluate` through a subprocess, and reads its JSON.
Nothing is mocked, matching `test_sbe_impact.py`, `test_sbe_evidence.py` and
`test_sbe_bypass.py`. A policy engine's whole job lives at the seam between a
diff and a claim about it, and a mocked seam would test the mock.

HOW EACH OF THESE WAS CALIBRATED, because a test nobody watched fail proves
nothing. Each fixture below was run against the defect it exists to catch before
the fix existed, and the defect is named in the test's own docstring in the form
"before X, this fixture saw Y". The two structural ones are worth stating here:

  - `test_an_empty_receipts_dir_cannot_bypass_policy` and its dossier twin were
    run against the previous `.github/actions/sbe-consumer/action.yml`, whose
    receipts and dossier steps each carried `if: inputs.X != ''`. Both assertions
    failed: the step was skipped and the job was green. They pass now because
    the `if:` lines are gone AND because the evaluator itself reports MISSING
    with no receipts directory at all.
  - `test_lowering_a_detected_tier_without_a_protected_decision_fails` was run
    with `_tier_requirement` returning SATISFIED whenever any tier was declared.
    It saw verdict PASS over a T0 declaration on a control plane change, which
    is the exact "declare it small and the gate never fires" bypass.

NO-DATA IS ASSERTED ABSENT, not merely unexpected. `test_a_migration_file_with_no
_receipt_fails` asserts the literal string never appears in any requirement
state, because the whole defect being closed here is one word carrying two
opposite meanings.

Python floor is 3.9. Standard library only. No network.
"""
import json
import os
import subprocess
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.abspath(os.path.join(HERE, ".."))
SBE = os.path.join(ROOT, "bin", "sbe")
CONSUMER_ACTION = os.path.join(ROOT, ".github", "actions", "sbe-consumer", "action.yml")
SHIPPED_POLICY = os.path.join(ROOT, ".sbe", "policy.yml")

sys.path.insert(0, os.path.join(ROOT, "src"))


def git(cwd, *args):
    out = subprocess.run(["git"] + list(args), cwd=cwd, capture_output=True, text=True)
    if out.returncode != 0:
        raise AssertionError("git %s failed in %s: %s" % (" ".join(args), cwd, out.stderr))
    return out.stdout.strip()


def write(cwd, rel, body):
    path = os.path.join(cwd, rel)
    parent = os.path.dirname(path)
    if parent and not os.path.isdir(parent):
        os.makedirs(parent)
    with open(path, "w") as fh:
        fh.write(body)
    return path


def write_json(cwd, rel, obj):
    return write(cwd, rel, json.dumps(obj, indent=2, sort_keys=True))


class PolicyFixture(unittest.TestCase):
    """A real repository with the shipped policy in it, one commit deep."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="sbe-policy-")
        self.addCleanup(self._cleanup)
        self.repo = os.path.join(self.tmp, "repo")
        os.makedirs(self.repo)
        git(self.repo, "init", "-q")
        git(self.repo, "config", "user.email", "fixture@example.invalid")
        git(self.repo, "config", "user.name", "Policy Fixture")
        with open(SHIPPED_POLICY) as fh:
            write(self.repo, os.path.join(".sbe", "policy.yml"), fh.read())
        write(self.repo, "README.md", "a repository\n")
        git(self.repo, "add", "-A")
        git(self.repo, "commit", "-q", "-m", "base")
        self.base = git(self.repo, "rev-parse", "HEAD")

    def _cleanup(self):
        import shutil
        shutil.rmtree(self.tmp, ignore_errors=True)

    def commit(self, message="change"):
        git(self.repo, "add", "-A")
        git(self.repo, "commit", "-q", "-m", message)
        return git(self.repo, "rev-parse", "HEAD")

    def demand_the_full_template(self):
        """Turn the demanding settings ON in THIS fixture's copy of the policy.

        The SHIPPED policy is scoped to what this repository can actually
        satisfy today: no protected evidence level exists to demand
        (BR-1013), the Claude Code end to end runner is not built
        (OWED-10), and a single human cannot approve their own pull
        request. Each omission is written into `.sbe/policy.yml` beside
        the line it removed. The mechanism itself is untouched, and these
        tests are what proves that: they restore the full template in
        their own copy and assert the engine still refuses everything it
        should.
        """
        path = os.path.join(self.repo, ".sbe", "policy.yml")
        with open(path) as handle:
            text = handle.read()
        text = text.replace("protectedEvidenceRequired: false",
                            "protectedEvidenceRequired: true")
        needle = '    requiredChecks:\n      - "control-plane-tests"'
        assert needle in text, "the shipped control-plane rule changed shape"
        text = text.replace(needle, needle
                            + '\n      - "claude-plugin-e2e"'
                            + '\n    requiredApprovals:'
                            + '\n      - "control-plane-owner"', 1)
        write(self.repo, os.path.join(".sbe", "policy.yml"), text)
        self.commit("this fixture demands the full template")

    def register_and_mint_rehearsal(self):
        """Register a migration rehearsal, run it for real, return the receipt path.

        Extracted from the protected-evidence test so more than one case can
        rest on a REAL receipt: minted through the registered path, sealed,
        and written outside the repository so minting it never dirties the
        tree it covers.
        """
        write(self.repo, os.path.join("scripts", "rehearse-migration.py"),
              "import sys\nsys.stdout.write('rehearsed\\n')\n")
        write(self.repo, os.path.join(".sbe", "checks.yml"),
              'schemaVersion: "1.0"\n\n'
              'checks:\n'
              '  migration-rehearsal:\n'
              '    kind: "migration"\n'
              '    command:\n'
              '      executable: "python3"\n'
              '      arguments:\n'
              '        - "scripts/rehearse-migration.py"\n'
              '      cwd: "."\n'
              '    covers:\n'
              '      - "migrations/**"\n'
              '    runnerFiles:\n'
              '      - "scripts/rehearse-migration.py"\n'
              '    protectedEvidence: true\n')
        self.base = self.commit("register the migration rehearsal")
        write(self.repo, os.path.join("migrations", "001_add_orders.sql"),
              "CREATE TABLE orders (id integer primary key);\n")
        self.commit("add a migration")
        receipts = os.path.join(self.tmp, "receipts")
        if not os.path.isdir(receipts):
            os.makedirs(receipts)
        out_path = os.path.join(receipts, "rehearsal.json")
        env = dict(os.environ)
        env.pop("SBE_CI_RUN_ID", None)
        out = subprocess.run(
            [sys.executable, SBE, "evidence", "run", "--check", "migration-rehearsal",
             "--out", out_path],
            cwd=self.repo, capture_output=True, text=True, env=env)
        self.assertTrue(os.path.exists(out_path),
                        "the fixture could not mint a receipt: %s%s"
                        % (out.stdout, out.stderr))
        return receipts

    def evaluate(self, *extra):
        """The JSON report and the exit code, as an object with both on it.

        Deliberately NOT a 2-tuple: every two-value return under tools/ is read
        by `evals/test_no_data_class.py` as a verdict pair it must prove is
        never PASS, and a test helper is not a verdict producer.
        """
        argv = [sys.executable, SBE, "policy", "evaluate", self.repo,
                "--base", self.base, "--head", "HEAD", "--json"] + list(extra)
        out = subprocess.run(argv, cwd=self.repo, capture_output=True, text=True)
        result = Result()
        result.code = out.returncode
        result.stdout = out.stdout
        result.stderr = out.stderr
        try:
            result.report = json.loads(out.stdout)
        except ValueError:
            result.report = None
        return result

    def state_of(self, report, requirement):
        for record in report["requirements"]:
            if record["requirement"] == requirement:
                return record["state"]
        raise AssertionError("no requirement named %r in %s"
                             % (requirement, [r["requirement"] for r in report["requirements"]]))

    def record_of(self, report, requirement):
        for record in report["requirements"]:
            if record["requirement"] == requirement:
                return record
        raise AssertionError("no requirement named %r" % requirement)

    def assert_no_no_data(self, report):
        states = set(r["state"] for r in report["requirements"])
        self.assertNotIn(
            "NO-DATA", states,
            "a requirement reported NO-DATA. That word meaning both 'nothing was required' "
            "and 'what was required is not there' is the entire defect this engine closes: "
            "required and absent is MISSING, and not required is NOT-REQUIRED")


class Result(object):
    """One evaluator run: the exit code, both streams, and the parsed report."""

    code = None
    stdout = ""
    stderr = ""
    report = None


# ---------------------------------------------------------------------------
# 1 to 3: a migration with no receipt, a broken receipt, an advisory receipt
# ---------------------------------------------------------------------------

class MigrationEvidence(PolicyFixture):

    def _add_migration(self):
        write(self.repo, os.path.join("migrations", "001_add_orders.sql"),
              "CREATE TABLE orders (id integer primary key);\n")
        return self.commit("add a migration")

    def test_a_migration_file_with_no_receipt_fails(self):
        """Required test 1.

        Before the policy engine, this diff produced nothing at all: no rule
        said a migration owed a rehearsal, so the strict run completed and the
        only absence anybody could point at was an empty receipts directory,
        which read as NO-DATA and never blocked.
        """
        self._add_migration()
        result = self.evaluate()
        self.assertIsNotNone(result.report, result.stderr)
        self.assertEqual(self.state_of(result.report, "check:migration-rehearsal"), "MISSING")
        self.assertEqual(self.state_of(result.report, "check:migration-reconciliation"),
                         "MISSING")
        self.assertEqual(result.report["verdict"], "BLOCKED")
        self.assertNotEqual(result.code, 0,
                            "a migration with no rehearsal receipt exited 0; MISSING must "
                            "exit nonzero or the requirement is decoration")
        self.assert_no_no_data(result.report)

    def test_a_migration_file_with_a_malformed_receipt_fails(self):
        """Required test 2. A file that is not JSON in the receipts directory is a
        broken claim, and a broken claim is INVALID, never an absence."""
        self._add_migration()
        receipts = os.path.join(self.tmp, "receipts")
        os.makedirs(receipts)
        with open(os.path.join(receipts, "migration.json"), "w") as fh:
            fh.write("{not json\n")
        result = self.evaluate("--receipts-dir", receipts)
        self.assertIsNotNone(result.report, result.stderr)
        self.assertEqual(self.state_of(result.report, "check:migration-rehearsal"), "INVALID")
        self.assertNotEqual(result.code, 0)
        self.assert_no_no_data(result.report)

    def test_a_local_advisory_receipt_fails_when_protected_evidence_is_required(self):
        """Required test 3.

        The receipt here is REAL: minted by `sbe evidence run`, sealed, bound to
        this commit, covering the migration. It is refused anyway, because the
        shipped policy declares protectedEvidenceRequired and a receipt any
        laptop can mint is not the thing that requirement asked for. The state
        is UNPROTECTED, which is its own word: this is not a broken receipt and
        saying INVALID about it would be a lie the operator would waste an hour
        on.
        """
        self.demand_the_full_template()
        # The receipt below is minted through the REGISTERED path, which is the
        # only way to carry a check id since `.sbe/checks.yml` landed: checkId
        # is inside SEALED_FIELDS now, so hand-writing the name of an obligation
        # onto a free-form receipt breaks the seal and `sbe evidence verify`
        # refuses it, which is exactly what a caller-supplied identity should
        # earn. Everything the assertion rests on stays real: the registered
        # command genuinely ran, over the migration this rule matched, and the
        # only thing missing is a CI run id.
        write(self.repo, os.path.join("scripts", "rehearse-migration.py"),
              "import sys\nsys.stdout.write('rehearsed\\n')\n")
        write(self.repo, os.path.join(".sbe", "checks.yml"),
              'schemaVersion: "1.0"\n\n'
              'checks:\n'
              '  migration-rehearsal:\n'
              '    kind: "migration"\n'
              '    command:\n'
              '      executable: "python3"\n'
              '      arguments:\n'
              '        - "scripts/rehearse-migration.py"\n'
              '      cwd: "."\n'
              '    covers:\n'
              '      - "migrations/**"\n'
              '    runnerFiles:\n'
              '      - "scripts/rehearse-migration.py"\n'
              '    protectedEvidence: true\n')
        self.base = self.commit("register the migration rehearsal")
        self._add_migration()
        receipts = os.path.join(self.tmp, "receipts")
        os.makedirs(receipts)
        out_path = os.path.join(receipts, "rehearsal.json")
        env = dict(os.environ)
        env.pop("SBE_CI_RUN_ID", None)
        out = subprocess.run(
            [sys.executable, SBE, "evidence", "run", "--check", "migration-rehearsal",
             "--out", out_path],
            cwd=self.repo, capture_output=True, text=True, env=env)
        self.assertTrue(os.path.exists(out_path),
                        "the fixture could not mint a receipt: %s%s" % (out.stdout, out.stderr))
        verified = subprocess.run([sys.executable, SBE, "evidence", "verify", out_path],
                                  cwd=self.repo, capture_output=True, text=True)
        self.assertEqual(verified.returncode, 0,
                         "the fixture receipt does not verify, so an UNPROTECTED verdict "
                         "below would be about a broken receipt and not about trust: %s%s"
                         % (verified.stdout, verified.stderr))
        result = self.evaluate("--receipts-dir", receipts)
        self.assertIsNotNone(result.report, result.stderr)
        self.assertEqual(self.state_of(result.report, "check:migration-rehearsal"),
                         "UNPROTECTED",
                         "a locally minted receipt satisfied a protected requirement")
        self.assertNotEqual(result.code, 0)
        self.assert_no_no_data(result.report)


# ---------------------------------------------------------------------------
# 4 to 7: what matches, and what honestly does not
# ---------------------------------------------------------------------------

class WhatMatches(PolicyFixture):

    def test_a_documentation_only_change_reports_not_required(self):
        """Required test 4.

        The direction that keeps this engine usable. If a docs change had to
        carry a migration rehearsal, every pull request would be red and people
        would route around the gate within a week. NOT-REQUIRED is a verdict
        printed per rule, not a silence.
        """
        write(self.repo, os.path.join("docs", "NOTES.md"), "# notes\n\nsome prose.\n")
        self.commit("docs only")
        result = self.evaluate()
        self.assertIsNotNone(result.report, result.stderr)
        self.assertEqual(result.report["matchedRules"], [])
        self.assertEqual(result.report["verdict"], "NOT-REQUIRED")
        self.assertEqual(result.code, 0)
        for record in result.report["requirements"]:
            self.assertEqual(record["state"], "NOT-REQUIRED", record["requirement"])
        self.assert_no_no_data(result.report)

    def test_ddl_inside_a_test_file_does_not_demand_a_migration_rehearsal(self):
        """Content signals must not fire on test, fixture and documentation
        surfaces, and this is the direction that keeps the engine usable.

        Found by running this engine against its own repository, which is the
        only place it could have been found: the tests that PROVE a migration
        is caught contain the migration text, so the sniffer demanded a
        migration rehearsal receipt for a change that touches no database at
        all. A rule that fires on every file discussing a migration is a rule
        an operator learns to waive."""
        write(self.repo, os.path.join("tools", "test_sbe_example.py"),
              'SQL = "CREATE TABLE greeting_log (id INTEGER PRIMARY KEY);"\n'
              'DROP = "ALTER TABLE greeting_log DROP COLUMN name;"\n')
        self.commit("a test fixture that quotes DDL")
        result = self.evaluate()
        self.assertIsNotNone(result.report, result.stderr)
        self.assertNotIn("database-migration",
                         [r["id"] for r in result.report["matchedRules"]],
                         "a test file quoting DDL activated the migration rule")

    def test_the_same_ddl_in_a_real_migration_still_fires(self):
        """The calibration for the exclusion above. Without it, the exclusion
        could be silently over-broad and this engine would stop catching the
        thing it exists to catch, with every test still green."""
        write(self.repo, os.path.join("db", "0002_drop_a_column.sql"),
              "ALTER TABLE greeting_log DROP COLUMN name;\n")
        self.commit("a real migration outside every declared directory")
        result = self.evaluate()
        self.assertIsNotNone(result.report, result.stderr)
        self.assertIn("database-migration",
                      [r["id"] for r in result.report["matchedRules"]],
                      "a real migration carrying the same DDL was not caught, so the "
                      "exclusion above is too broad and the gate is now blind")

    def test_a_receipt_stays_current_when_nothing_it_covered_moved(self):
        """Staleness asks whether the covered files moved, not whether the head did.

        Found by dogfooding this engine on its own repository: a receipt
        committed into the tree it describes can never be bound to the
        commit that contains it, because writing it moves the head.
        Judging by the head alone made such evidence permanently stale,
        which teaches an operator not to keep evidence at all.
        """
        receipts = self.register_and_mint_rehearsal()
        write(self.repo, "UNRELATED.md", "a note this receipt never covered\n")
        self.commit("an unrelated file moves the head")
        result = self.evaluate("--receipts-dir", receipts)
        self.assertIsNotNone(result.report, result.stderr)
        self.assertNotEqual(self.state_of(result.report, "check:migration-rehearsal"),
                            "STALE",
                            "a receipt went stale because an unrelated file moved the "
                            "head, so committed evidence could never be current")

    def test_a_receipt_goes_stale_when_a_covered_file_moves(self):
        """The calibration for the rule above. Without it the coverage check
        could be vacuous and every receipt would read as current forever."""
        receipts = self.register_and_mint_rehearsal()
        write(self.repo, os.path.join("migrations", "001_add_orders.sql"),
              "ALTER TABLE t DROP COLUMN c;\n")
        self.commit("the covered migration itself changes")
        result = self.evaluate("--receipts-dir", receipts)
        self.assertIsNotNone(result.report, result.stderr)
        self.assertEqual(self.state_of(result.report, "check:migration-rehearsal"),
                         "STALE",
                         "a covered file changed and the receipt was still accepted")

    def test_an_api_schema_deletion_requires_contract_compatibility_evidence(self):
        """Required test 5.

        Deleting a published contract is the largest compatibility change there
        is, and a name-only diff reader that looked at what a file CONTAINS
        would see nothing at all in a deletion. The status letter D is read here
        and the rule still fires.
        """
        write(self.repo, os.path.join("openapi", "orders.yaml"), "openapi: 3.0.0\n")
        # The contract exists in the MERGE BASE. A diff that adds and then
        # deletes a file inside its own range is a net no-op, and this fixture
        # is about a pull request that removes something the base had.
        self.base = self.commit("add a contract")
        os.remove(os.path.join(self.repo, "openapi", "orders.yaml"))
        self.commit("delete the contract")
        result = self.evaluate()
        self.assertIsNotNone(result.report, result.stderr)
        statuses = dict((e["path"], e["status"]) for e in result.report["changedPaths"])
        self.assertEqual(statuses.get(os.path.join("openapi", "orders.yaml").replace(os.sep, "/")),
                         "D", "the deletion was not read as a deletion: %s" % statuses)
        self.assertEqual(self.state_of(result.report, "check:contract-compatibility"), "MISSING")
        self.assertNotEqual(result.code, 0)

    def test_a_renamed_migration_file_is_still_detected(self):
        """Required test 6.

        Moving a migration into a directory the policy does not name is the
        cheapest possible bypass. Rename detection is on, and BOTH sides of the
        rename are reported, so the rule that matched the old path still fires.
        """
        write(self.repo, os.path.join("migrations", "002_add_items.sql"),
              "CREATE TABLE items (id integer primary key);\n")
        self.base = self.commit("add a migration")
        os.makedirs(os.path.join(self.repo, "attic"))
        # Renamed to a path NO glob in the policy matches, extension included.
        # If only the destination were read, this rule would go quiet and the
        # rename would be the bypass. Both sides of an R record are reported.
        os.rename(os.path.join(self.repo, "migrations", "002_add_items.sql"),
                  os.path.join(self.repo, "attic", "002_add_items.txt"))
        self.commit("move it out of the way")
        result = self.evaluate()
        self.assertIsNotNone(result.report, result.stderr)
        statuses = sorted(set(e["status"] for e in result.report["changedPaths"]))
        self.assertIn("R-from", statuses,
                      "the rename was not read as a rename: %s" % result.report["changedPaths"])
        matched = [r["id"] for r in result.report["matchedRules"]]
        self.assertIn("database-migration", matched,
                      "a renamed migration stopped being a migration: %s"
                      % result.report["changedPaths"])
        self.assertEqual(self.state_of(result.report, "check:migration-rehearsal"), "MISSING")
        self.assertNotEqual(result.code, 0)

    def test_a_deleted_infrastructure_resource_is_still_detected(self):
        """Required test 7. Deleting production infrastructure is a production
        infrastructure change; a rule that only read additions would miss the
        single most destructive shape of one."""
        write(self.repo, os.path.join("terraform", "main.tf"),
              "resource \"aws_s3_bucket\" \"b\" {}\n")
        self.base = self.commit("add infrastructure")
        os.remove(os.path.join(self.repo, "terraform", "main.tf"))
        self.commit("delete infrastructure")
        result = self.evaluate()
        self.assertIsNotNone(result.report, result.stderr)
        matched = [r["id"] for r in result.report["matchedRules"]]
        self.assertIn("production-infrastructure", matched)
        self.assertEqual(self.state_of(result.report, "check:infrastructure-plan"), "MISSING")
        self.assertNotEqual(result.code, 0)


# ---------------------------------------------------------------------------
# 8 to 12: approvals, the two empty-input bypasses, waivers, and the tier floor
# ---------------------------------------------------------------------------

class ApprovalsAndBypasses(PolicyFixture):

    def _touch_control_plane(self):
        write(self.repo, os.path.join("hooks", "hooks.json"), "{\"hooks\": []}\n")
        return self.commit("edit the control plane")

    def test_a_control_plane_change_with_no_approval_fails(self):
        """Required test 8.

        The rule that protects every other rule. A change to hooks/ that carries
        no control-plane-owner approval is MISSING an approval, and MISSING
        blocks: an approval nobody gave is not an approval nobody needed.
        """
        self.demand_the_full_template()
        self._touch_control_plane()
        result = self.evaluate()
        self.assertIsNotNone(result.report, result.stderr)
        self.assertEqual(self.state_of(result.report, "approval:control-plane-owner"), "MISSING")
        self.assertNotEqual(result.code, 0)
        self.assert_no_no_data(result.report)

    def test_an_empty_receipts_dir_cannot_bypass_policy(self):
        """Required test 9, in both places it can be bypassed.

        The evaluator: an empty directory, and no directory at all, both leave
        every required check MISSING. The consumer action: its receipt step
        carried `if: inputs.receipts-dir != ''`, so passing nothing SKIPPED the
        verification and the job went green. Asserting only the Python would
        have left the shipped bypass exactly where it was.
        """
        self._touch_control_plane()
        empty = os.path.join(self.tmp, "empty-receipts")
        os.makedirs(empty)
        for extra in ([], ["--receipts-dir", empty]):
            result = self.evaluate(*extra)
            self.assertIsNotNone(result.report, result.stderr)
            self.assertEqual(self.state_of(result.report, "check:control-plane-tests"),
                             "MISSING", "receipts %r bypassed the requirement" % (extra,))
            self.assertNotEqual(result.code, 0)
        with open(CONSUMER_ACTION) as fh:
            action = fh.read()
        self.assertNotIn("inputs.receipts-dir != ''", action,
                         "the consumer action still skips receipt verification when the "
                         "input is empty, which is the bypass: supply nothing, verify "
                         "nothing, go green")

    def test_an_empty_dossier_input_cannot_bypass_policy(self):
        """Required test 10. Same shape, the other input.

        The design step carried `if: inputs.dossier != ''`. Policy evaluation
        must run whether or not a dossier was named, and it does: this asserts
        the evaluator with no dossier still blocks, and that no step in the
        action is gated on an empty input at all.
        """
        self.demand_the_full_template()
        self._touch_control_plane()
        result = self.evaluate("--dossier", "")
        self.assertIsNotNone(result.report, result.stderr)
        self.assertEqual(self.state_of(result.report, "approval:control-plane-owner"), "MISSING")
        self.assertNotEqual(result.code, 0)
        with open(CONSUMER_ACTION) as fh:
            action = fh.read()
        self.assertNotIn("inputs.dossier != ''", action)
        self.assertNotIn("if: ${{ inputs.", action,
                         "a step in the consumer action is still switched off by an input; "
                         "every one of those is a way to verify nothing and go green")
        self.assertIn("policy evaluate", action,
                      "the consumer action does not run policy evaluation at all")

    def test_a_waiver_without_owner_reason_expiry_and_protected_approval_fails(self):
        """Required test 11.

        Four fixtures in one, each dropping exactly one of the four things an
        exception must carry, plus the complete one that is accepted. The
        accepted one is asserted to render WAIVED and to STILL BLOCK, because
        the control plane rule declares strictWaivers: an exception on it
        records the decision, it does not clear it.
        """
        self.demand_the_full_template()
        self._touch_control_plane()
        complete = {"ruleId": "authority-or-control-plane",
                    "requirement": "approval:control-plane-owner",
                    "reason": "the owner is on leave and the change is a comment fix",
                    "owner": "release-manager",
                    "expiry": "2099-01-01",
                    "approval": {"approver": "control-plane-owner", "protected": True}}
        for dropped in ("reason", "owner", "expiry"):
            record = dict(complete)
            record[dropped] = ""
            path = write_json(self.tmp, "waiver-no-%s.json" % dropped, [record])
            result = self.evaluate("--waivers", path)
            self.assertIsNotNone(result.report, result.stderr)
            entry = self.record_of(result.report, "approval:control-plane-owner")
            self.assertEqual(entry["state"], "MISSING",
                             "a waiver with no %s was accepted" % dropped)
            self.assertIn("REFUSED", entry["waiverRefused"] or "")
            self.assertNotEqual(result.code, 0)

        unprotected = dict(complete)
        unprotected["approval"] = {"approver": "somebody", "protected": False}
        path = write_json(self.tmp, "waiver-unprotected.json", [unprotected])
        result = self.evaluate("--waivers", path)
        entry = self.record_of(result.report, "approval:control-plane-owner")
        self.assertEqual(entry["state"], "MISSING",
                         "a waiver whose approval was not protected was accepted; a local "
                         "process can mint one of those for itself")
        self.assertNotEqual(result.code, 0)

        expired = dict(complete)
        expired["expiry"] = "2020-01-01"
        path = write_json(self.tmp, "waiver-expired.json", [expired])
        result = self.evaluate("--waivers", path)
        self.assertEqual(self.state_of(result.report, "approval:control-plane-owner"),
                         "MISSING", "an expired waiver still waived")
        self.assertNotEqual(result.code, 0)

        path = write_json(self.tmp, "waiver-complete.json", [complete])
        result = self.evaluate("--waivers", path)
        entry = self.record_of(result.report, "approval:control-plane-owner")
        self.assertEqual(entry["state"], "WAIVED",
                         "a complete exception was not recorded as WAIVED")
        self.assertEqual(entry["waivedFrom"], "MISSING")
        self.assertNotEqual(result.code, 0,
                            "an accepted waiver on a strictWaivers rule exited 0; the "
                            "control plane rule records an exception, it does not clear it")

    def test_lowering_a_detected_tier_without_a_protected_decision_fails(self):
        """Required test 12.

        The tier is a floor. A control plane change detects T3; declaring T0 at
        intake is a request to lower it, and a request nobody recorded and
        nobody approved under protected approval is INVALID. The pass direction
        is asserted in the same fixture: declaring T3 satisfies, so this is a
        test of the RULE and not just of a refusal.
        """
        self._touch_control_plane()
        low = write_json(self.tmp, "intake-t0.json",
                         {"tier": "T0",
                          "answers": {"changes_contract": "n", "crosses_boundary": "n",
                                      "reversible_under_hour": "y", "touches_sensitive": "n",
                                      "consumers": "none"}})
        result = self.evaluate("--intake", low)
        self.assertIsNotNone(result.report, result.stderr)
        self.assertEqual(result.report["minimumDetectedTier"], "T3")
        self.assertEqual(result.report["declaredTier"], "T0")
        self.assertEqual(self.state_of(result.report, "tier:T3"), "INVALID",
                         "a declared T0 lowered a detected T3 with no decision record")
        self.assertNotEqual(result.code, 0)

        weak = write_json(self.tmp, "decision-unprotected.json",
                          {"decision": "accept T0", "reason": "it is only a comment",
                           "who": "the author",
                           "headCommit": git(self.repo, "rev-parse", "HEAD"),
                           "approval": {"approver": "the author", "protected": False}})
        result = self.evaluate("--intake", low, "--decision", weak)
        self.assertEqual(self.state_of(result.report, "tier:T3"), "INVALID",
                         "a decision record whose approval was not protected lowered a tier")
        self.assertNotEqual(result.code, 0)

        high = write_json(self.tmp, "intake-t3.json",
                          {"tier": "T3",
                           "answers": {"changes_contract": "y", "crosses_boundary": "y",
                                       "reversible_under_hour": "n", "touches_sensitive": "y",
                                       "consumers": "many"}})
        result = self.evaluate("--intake", high)
        self.assertEqual(self.state_of(result.report, "tier:T3"), "SATISFIED",
                         "declaring the detected tier did not satisfy the floor, so this "
                         "engine cannot be passed at all and the refusals above prove nothing")
        self.assertEqual(result.report["effectiveTier"], "T3")


class PolicyFileItself(PolicyFixture):
    """The policy is a control, so an unreadable one blocks rather than exempts."""

    def test_an_unreadable_policy_blocks_and_never_reads_as_no_requirements(self):
        write(self.repo, os.path.join("migrations", "003.sql"), "CREATE TABLE t (id int);\n")
        write(self.repo, os.path.join(".sbe", "policy.yml"), "schemaVersion: \"1.0\"\n")
        self.commit("break the policy")
        result = self.evaluate()
        self.assertNotEqual(result.code, 0,
                            "a policy missing protectedEvidenceRequired and rules exited 0; "
                            "deleting the control must never read as permission")
        self.assertIn("BLOCKED", result.stderr)

    def test_the_shipped_policy_declares_all_five_categories(self):
        from brothersbe import policy as policy_mod
        loaded = policy_mod.load_policy(SHIPPED_POLICY)
        ids = sorted(r["id"] for r in loaded["rules"])
        self.assertEqual(ids, ["api-or-event-contract", "authority-or-control-plane",
                               "database-migration", "money-personal-or-partner-data",
                               "production-infrastructure"])
        # The shipped file turns the protected demand OFF, and the reason
        # is asserted rather than trusted: no protected trust level exists
        # in this release, so demanding one would block every governed
        # change forever while proving nothing. The mechanism is proven by
        # the tests above, which turn the demand back on in their own copy.
        self.assertFalse(loaded["protectedEvidenceRequired"])
        with open(SHIPPED_POLICY) as handle:
            body = handle.read()
        for phrase in ("protectedEvidenceRequired: false", "stuck", "attestation"):
            self.assertIn(phrase, body,
                          "the shipped policy turns a demand off without recording %r, "
                          "so a reader is told what it does and not why" % phrase)
        for phrase in ("claude-plugin-e2e", "OWED-10", "ONE human"):
            self.assertIn(phrase, body,
                          "the shipped policy omits a requirement without naming %r, and "
                          "an omission nobody explained cannot be told apart from an "
                          "oversight" % phrase)
        for rule in loaded["rules"]:
            if rule["id"] == "api-or-event-contract":
                continue
            self.assertTrue(rule["strictWaivers"],
                            "%s does not enable strict waivers; the spec requires it for "
                            "control plane, infrastructure, migration, money, personal and "
                            "partner data" % rule["id"])


if __name__ == "__main__":
    unittest.main(verbosity=2)

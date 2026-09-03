"""Adversarial fixtures for `sbe converge` (spec:
docs/specs/2026-07-30-sbe-converge.md). Written BEFORE the implementation, so
on the day this file landed every scenario test was red on the missing module
and the missing subcommand, and none was red on a fixture bug.

Every fixture builds its own throwaway git repository: a dossier under
design/chg, an OpenAPI contract, a source file, a migration, a plan derived
by the real `sbe plan --write`, all committed as the BASE commit; then one
scenario-specific implementation commit as HEAD. Nothing here touches the
real repository's tree.
"""
import io
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
SBE = os.path.join(HERE, "..", "bin", "sbe")

INTAKE = {
    "answers": {"changes_contract": "n", "crosses_boundary": "n",
                "reversible_under_hour": "y", "touches_sensitive": "n",
                "consumers": "none"},
    "tier": "T1", "override": None, "override_reason": None,
}

ADR = """# ADR
## Context
The app reads things over an API.
## Decision
Serve reads from `src/app.py` behind the contract in `api/openapi.json`.
## Consequences
Both files move together.
"""

DATA_MODEL = """# Data model
## Conceptual: entities and meanings
- User: a person with an account; system of record: this service.
## Relationships
- User has many Sessions (1..n, optional).
## Attribute roles
| Attribute | Entity | Role (identifier, descriptor, measure, foreign key, temporal, status) |
|---|---|---|
| user_id | User | identifier |
| email | User | descriptor |
## Historization
Not historized.
## Source systems and failover
| Entity | Source | Refresh contract | If unavailable |
|---|---|---|---|
| User | this service | live | reads fail loudly |
## The three lenses
Engineer, analyst, scientist all read the same table.
## Physical
One table, migrated by `migrations/0001_add.sql`.
"""

VERIFICATION = """| Claim this design makes | The check that proves it | When it runs |
|---|---|---|
| the app answers | `python3 -c pass` | CI |
| the migration can be rolled back (reverse tested) | `python3 -c pass` | CI |
| row counts reconcile after the migration | `python3 -c pass` | CI |
"""

OPENAPI = {
    "openapi": "3.0.0",
    "info": {"title": "things", "version": "1"},
    "paths": {
        "/things": {
            "get": {"responses": {"200": {"description": "list"}}},
            "delete": {"responses": {"204": {"description": "gone"}}},
        }
    },
}


def _run(argv, cwd=None):
    out = subprocess.run(argv, capture_output=True, text=True, cwd=cwd,
                         stdin=subprocess.DEVNULL, timeout=120)
    # Three values, not two: a two-value return reads as a possible
    # (verdict, evidence) pair to the honesty meta-test, which refuses any
    # such function sitting outside a check registry.
    return out.returncode, out.stdout + out.stderr, out.stderr


class ConvergeScenario(unittest.TestCase):
    """Shared estate builder. Each test gets a fresh repo."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="sbe-converge-")
        self.repo = os.path.join(self.tmp, "repo")
        self.doss = os.path.join(self.repo, "design", "chg")
        os.makedirs(self.doss)
        self.git("init", "-q")
        self.git("config", "user.email", "fixture@example.invalid")
        self.git("config", "user.name", "fixture")

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def git(self, *args):
        code, text, _err = _run(["git", "-C", self.repo] + list(args))
        self.assertEqual(code, 0, "git %s failed: %s" % (args, text))
        return text

    def sbe(self, *args):
        return _run([sys.executable, SBE] + list(args))

    def _write(self, rel, content):
        path = os.path.join(self.repo, rel)
        parent = os.path.dirname(path)
        if parent and not os.path.isdir(parent):
            os.makedirs(parent)
        if isinstance(content, dict):
            content = json.dumps(content, indent=2, sort_keys=True) + "\n"
        io.open(path, "w", encoding="utf-8").write(content)

    def _seed(self):
        """Dossier, contract, source, migration, derived plan, all in the
        BASE commit. Returns the base sha."""
        self._write("design/chg/00-intake.json", INTAKE)
        self._write("design/chg/03-adr.md", ADR)
        self._write("design/chg/05-data-model.md", DATA_MODEL)
        self._write("design/chg/07-verification.md", VERIFICATION)
        self._write("api/openapi.json", OPENAPI)
        self._write("src/app.py", "def answer():\n    return 41\n")
        self._write("migrations/0001_add.sql",
                    "ALTER TABLE users ADD COLUMN email TEXT;\n")
        code, text, _ = self.sbe("plan", self.doss, "--write", "--cwd", self.repo)
        self.assertEqual(code, 0, "plan --write must succeed on the seed: %s" % text)
        self.git("add", "-A")
        self.git("commit", "-qm", "seed with dossier and plan")
        return self.git("rev-parse", "HEAD").strip()

    def _head(self, message="impl"):
        self.git("add", "-A")
        self.git("commit", "-qm", message)
        return self.git("rev-parse", "HEAD").strip()

    def _converge(self, base, head, *extra):
        return self.sbe("converge", self.doss, "--base", base, "--head", head,
                        "--cwd", self.repo, *extra)

    def _receipt(self, name="r1.json"):
        out_path = os.path.join(self.repo, ".sbe", "evidence", name)
        code, text, _ = self.sbe("evidence", "run", "--out", out_path,
                                 "--cwd", self.repo, "--", "python3", "-c", "pass")
        self.assertEqual(code, 0, "evidence run failed: %s" % text)
        return out_path


class TestUsage(ConvergeScenario):
    def test_missing_base_or_head_is_a_usage_error_and_writes_nothing(self):
        base = self._seed()
        code, text, _ = self.sbe("converge", self.doss, "--head", base,
                                 "--cwd", self.repo)
        self.assertEqual(code, 2, text)
        code, text, _ = self.sbe("converge", self.doss, "--base", base,
                                 "--cwd", self.repo)
        self.assertEqual(code, 2, text)
        self.assertFalse(os.path.exists(os.path.join(self.doss, "09-convergence.json")),
                         "a usage error must not write a report")

    def test_there_is_no_force_flag(self):
        base = self._seed()
        code, text, _ = self._converge(base, base, "--force")
        self.assertEqual(code, 2, "argparse must reject --force: %s" % text)


class TestScope(ConvergeScenario):
    def test_an_unplanned_changed_file_is_review_required_by_name(self):
        base = self._seed()
        self._write("notes/extra.py", "x = 1\n")
        head = self._head("out of plan")
        code, text, _ = self._converge(base, head)
        self.assertEqual(code, 1, text)
        self.assertIn("notes/extra.py", text)
        self.assertIn("REVIEW-REQUIRED", text)

    def test_a_planned_change_with_a_receipt_is_final_pass_exit_zero(self):
        base = self._seed()
        self._write("src/app.py", "def answer():\n    return 42\n")
        head = self._head("planned edit")
        self._receipt()
        code, text, _ = self._converge(base, head)
        self.assertEqual(code, 0, text)
        self.assertIn("FINAL", text)
        self.assertIn("PASS", text)
        self.assertTrue(os.path.exists(os.path.join(self.doss, "09-convergence.json")))

    def test_the_report_is_deterministic_and_binds_base_and_head(self):
        base = self._seed()
        self._write("src/app.py", "def answer():\n    return 42\n")
        head = self._head("planned edit")
        self._receipt()
        code, _text, _ = self._converge(base, head)
        self.assertEqual(code, 0)
        report_path = os.path.join(self.doss, "09-convergence.json")
        first = io.open(report_path, encoding="utf-8").read()
        code, _text, _ = self._converge(base, head)
        self.assertEqual(code, 0)
        second = io.open(report_path, encoding="utf-8").read()
        self.assertEqual(first, second, "two runs over the same tree must be "
                                        "byte-identical: no timestamps in a report")
        report = json.loads(first)
        self.assertEqual(report["base"], base)
        self.assertEqual(report["head"], head)


class TestScopeUnmeasured(ConvergeScenario):
    """F8: an unmeasured file type is a category distinct from unplanned. A
    changed, unowned, undossiered file that matches no impact detector and
    whose extension is outside the tracked source-text set must surface as
    unmeasured (named), never as unplanned REVIEW-REQUIRED noise, and never
    silently folded into a clean PASS."""

    def test_an_opaque_changed_file_is_unmeasured_not_unplanned_review_required(self):
        base = self._seed()
        self._write("artifacts/build.sha256", "deadbeef  build.tar\n")
        head = self._head("add an opaque checksum artifact")
        self._receipt()
        code, text, _ = self._converge(base, head)
        self.assertEqual(code, 0, text)
        self.assertIn("FINAL PASS", text)
        self.assertIn("unmeasured", text.lower())
        self.assertIn("artifacts/build.sha256", text)
        self.assertNotIn("REVIEW-REQUIRED", text)


class TestContracts(ConvergeScenario):
    def test_an_undocumented_removed_operation_is_fail_naming_it(self):
        base = self._seed()
        slim = json.loads(json.dumps(OPENAPI))
        del slim["paths"]["/things"]["delete"]
        self._write("api/openapi.json", slim)
        head = self._head("drop the delete op")
        self._receipt()
        code, text, _ = self._converge(base, head)
        self.assertEqual(code, 1, text)
        self.assertIn("FAIL", text)
        self.assertIn("delete /things", text.lower())

    def test_an_undocumented_added_operation_is_review_required(self):
        base = self._seed()
        wider = json.loads(json.dumps(OPENAPI))
        wider["paths"]["/things"]["post"] = {"responses": {"201": {"description": "made"}}}
        self._write("api/openapi.json", wider)
        head = self._head("add an op")
        self._receipt()
        code, text, _ = self._converge(base, head)
        self.assertEqual(code, 1, text)
        self.assertIn("REVIEW-REQUIRED", text)
        self.assertIn("post /things", text.lower())
        self.assertNotIn("FINAL FAIL", text)

    def test_an_unparseable_contract_file_is_unmeasured_never_pass(self):
        base = self._seed()
        self._write("api/openapi.json", "{ this is not json\n")
        head = self._head("break the contract file")
        self._receipt()
        code, text, _ = self._converge(base, head)
        self.assertEqual(code, 1, text)
        self.assertIn("unmeasured", text.lower())
        self.assertIn("api/openapi.json", text)
        self.assertNotIn("CONTRACTS PASS", text)


class TestData(ConvergeScenario):
    def test_dropping_a_documented_attribute_is_fail_naming_it(self):
        base = self._seed()
        self._write("migrations/0001_add.sql",
                    "ALTER TABLE users ADD COLUMN email TEXT;\n"
                    "ALTER TABLE users DROP COLUMN email;\n")
        head = self._head("drop a documented column")
        self._receipt()
        code, text, _ = self._converge(base, head)
        self.assertEqual(code, 1, text)
        self.assertIn("FINAL FAIL", text)
        self.assertIn("email", text)
        self.assertIn("05-data-model.md", text)


class TestVerification(ConvergeScenario):
    def test_a_missing_receipt_fails_naming_the_command(self):
        base = self._seed()
        self._write("src/app.py", "def answer():\n    return 42\n")
        head = self._head("planned edit, no receipt")
        code, text, _ = self._converge(base, head)
        self.assertEqual(code, 1, text)
        self.assertIn("FINAL FAIL", text)
        self.assertIn("python3 -c pass", text)

    def test_a_receipt_bound_to_another_commit_fails_naming_both_shas(self):
        base = self._seed()
        self._receipt()  # bound to base, before the head commit exists
        self._write("src/app.py", "def answer():\n    return 42\n")
        head = self._head("planned edit after the receipt")
        code, text, _ = self._converge(base, head)
        self.assertEqual(code, 1, text)
        self.assertIn("FINAL FAIL", text)
        self.assertIn(base[:12], text)
        self.assertIn(head[:12], text)


class TestQuotedCommandReceipts(ConvergeScenario):
    """External proof, estates A and C, critical: a verification command with a
    quoted argument could NEVER match its receipt, because the shell strips
    quotes before argv exists while the plan stores the raw markdown text.
    The matcher must canonicalize both sides (shlex) instead of comparing a
    rejoined argv against raw text."""

    def test_a_quoted_command_receipt_binds(self):
        base = self._seed_with_quoted_command()
        self._write("src/app.py", "def answer():\n    return 42\n")
        head = self._head("planned edit")
        out_path = os.path.join(self.repo, ".sbe", "evidence", "q.json")
        code, text, _ = self.sbe("evidence", "run", "--out", out_path,
                                 "--cwd", self.repo, "--",
                                 "python3", "-c", "print('OK')")
        self.assertEqual(code, 0, text)
        code, text, _ = self._converge(base, head)
        self.assertEqual(code, 0,
                         "a receipt for the exact quoted command must bind: %s" % text)
        self.assertNotIn("no receipt for it exists", text)

    def _seed_with_quoted_command(self):
        self._write("design/chg/00-intake.json", INTAKE)
        self._write("design/chg/03-adr.md", ADR)
        self._write("design/chg/07-verification.md",
                    "| Claim this design makes | The check that proves it | When it runs |\n"
                    "|---|---|---|\n"
                    "| the app answers | `python3 -c \"print('OK')\"` | CI |\n")
        self._write("api/openapi.json", OPENAPI)
        self._write("src/app.py", "def answer():\n    return 41\n")
        code, text, _ = self.sbe("plan", self.doss, "--write", "--cwd", self.repo)
        self.assertEqual(code, 0, text)
        self.git("add", "-A")
        self.git("commit", "-qm", "seed quoted")
        return self.git("rev-parse", "HEAD").strip()


class TestSqlModelIsNotAMigration(ConvergeScenario):
    """External proof, estate B, critical: a plain-SELECT dbt model was called
    migration-shaped because the detector's content pattern was ignored, and
    DATA then FAILed a dossier for lacking a data model it never needed. A
    detector that declares a content pattern speaks only when the content
    matches it."""

    def test_a_pure_select_sql_change_is_not_migration_shaped(self):
        self._write("design/chg/00-intake.json", INTAKE)
        self._write("design/chg/03-adr.md", ADR.replace(
            "Serve reads from `src/app.py` behind the contract in `api/openapi.json`.",
            "Rewrite the model `models_dir/customers.sql` for portability."))
        self._write("design/chg/07-verification.md",
                    "| Claim this design makes | The check that proves it | When it runs |\n"
                    "|---|---|---|\n"
                    "| the model still selects | `grep -q select models_dir/customers.sql` | CI |\n")
        self._write("models_dir/customers.sql", "select id from users\n")
        code, text, _ = self.sbe("plan", self.doss, "--write", "--cwd", self.repo)
        self.assertEqual(code, 0, text)
        self.git("add", "-A")
        self.git("commit", "-qm", "seed select model, no data model artifact")
        base = self.git("rev-parse", "HEAD").strip()
        self._write("models_dir/customers.sql",
                    "select id, count(*) as n from users group by id\n")
        head = self._head("rewrite a select-only model")
        out_path = os.path.join(self.repo, ".sbe", "evidence", "s.json")
        code, text, _ = self.sbe("evidence", "run", "--out", out_path,
                                 "--cwd", self.repo, "--",
                                 "grep", "-q", "select", "models_dir/customers.sql")
        self.assertEqual(code, 0, text)
        code, text, _ = self._converge(base, head)
        self.assertEqual(code, 0, text)
        self.assertNotIn("migration-shaped files changed but the dossier has no "
                         "05-data-model.md", text,
                         "a SELECT-only .sql without DDL content is not a "
                         "migration and needs no data model: %s" % text)
        self.assertIn("FINAL PASS", text)


class TestAmendment(ConvergeScenario):
    def test_the_amendment_round_trip_is_the_only_path_from_fail_to_pass(self):
        base = self._seed()
        slim = json.loads(json.dumps(OPENAPI))
        del slim["paths"]["/things"]["delete"]
        self._write("api/openapi.json", slim)
        head = self._head("drop the delete op")
        self._receipt("first.json")
        code, text, _ = self._converge(base, head)
        self.assertEqual(code, 1, "the divergence must FAIL first: %s" % text)
        self.assertIn("FINAL FAIL", text)

        # Amend the dossier to document the removal, regenerate the plan
        # (freshness would refuse a stale one), commit, re-receipt, re-run.
        self._write("design/chg/03-adr.md", ADR.replace(
            "Both files move together.",
            "Both files move together. The operation `delete /things` is "
            "removed on purpose: nothing consumed it."))
        code, text, _ = self.sbe("plan", self.doss, "--write", "--cwd", self.repo)
        self.assertEqual(code, 0, "plan regeneration after the amendment: %s" % text)
        head2 = self._head("amend the dossier and regenerate the plan")
        self._receipt("second.json")
        code, text, _ = self._converge(base, head2)
        self.assertEqual(code, 0, "after a real amendment the same range must "
                                  "converge: %s" % text)
        self.assertIn("PASS", text)


if __name__ == "__main__":
    unittest.main(verbosity=2)

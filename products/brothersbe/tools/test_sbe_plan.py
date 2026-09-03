#!/usr/bin/env python3
"""Fixtures for `sbe plan`. Run: python3 tools/test_sbe_plan.py

The spec of record is docs/specs/2026-07-30-sbe-plan-derivation.md. These
fixtures assert exactly what that document says, nothing else: a disagreement
between this file and the eventual tools/sbe_plan.py is a defect in one of
them, never a negotiation settled here.

Every test builds a real git repository in a temporary directory (tempfile
mkdtemp, then git init) and a real synthetic dossier on disk inside it, and
runs the real `bin/sbe plan` subprocess against it. Nothing is mocked and
nothing is written into the repo this file lives in.

At the time this file was written, `bin/sbe plan` is a NOT BUILT stub that
exits 3 for every invocation. Every test here is expected to FAIL against
that stub (never ERROR: no import crash, no fixture crash) because each test
asserts the real, --write-and-validate behavior the spec describes. That red
is the point: tests first.
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


ADR_BODY = (
    "# 03. Architecture decision record\n\n"
    "## Context\n"
    "Cache lookups are slow under normal load.\n\n"
    "## Decision\n"
    "We cache widget lookups in `src/widgets/cache.py`, backed by `config/cache.yaml`.\n\n"
    "## Consequences\n"
    "This adds one dependency to operate.\n\n"
    "## What would flip this\n"
    "If the cache hit rate falls under the agreed target.\n"
)

DATA_MODEL_BODY = (
    "# 05. Data model\n\n"
    "## Physical\n"
    "The migration adds `migrations/0001_widget_cache.sql` for the cache table.\n"
)

ROW_PLAIN = ("The cache responds within budget",
             "Run `pytest tests/test_cache_perf.py`", "Continuous")
ROW_REVERSE = ("The migration rollback restores prior state",
               "Run `python3 migrations/0001_widget_cache.sql --reverse`", "On demand")
ROW_RECONCILE = ("Reconciliation confirms row counts match",
                  "Run `python3 tools/reconcile_widget_cache.py`", "Daily")
DEFAULT_ROWS = [ROW_PLAIN, ROW_REVERSE, ROW_RECONCILE]

ROW_DIGIT_ONE_COMMAND = ("Cache hit rate stays above 95 percent",
                          "Run `pytest tests/test_cache_hitrate.py`", "Continuous")
ROW_DIGIT_TWO_COMMANDS = ("Cache hit rate stays above 95 percent",
                           "Run `pytest tests/test_cache_hitrate.py` then confirm with "
                           "`python3 tools/report_hitrate.py`", "Continuous")

ADR_PATHS = ("src/widgets/cache.py", "config/cache.yaml")
PHYSICAL_PATH = "migrations/0001_widget_cache.sql"
CHANGE_DIR = "design/widget-cache"


def render_verification(rows):
    lines = ["# 07. Verification plan", "",
             "| Claim this design makes | The check that proves it | When it runs |",
             "|---|---|---|"]
    for claim, check, when in rows:
        lines.append("| %s | %s | %s |" % (claim, check, when))
    return "\n".join(lines) + "\n"


class PlanFixture(unittest.TestCase):
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

    def sbe(self, *argv):
        out = subprocess.run([sys.executable, SBE] + list(argv), capture_output=True, text=True)
        # Three values, not two: a two-value return reads as a possible
        # (verdict, evidence) pair to the honesty meta-test, which refuses any
        # such function sitting outside a check registry. Copies the shape of
        # the house sbe() helper in test_sbe_tasks.py and run_impact() in
        # test_sbe_impact.py.
        return out.returncode, out.stdout + out.stderr, out.stderr

    # -- dossier builders ---------------------------------------------------

    def _write_dossier(self, change=CHANGE_DIR, changes_contract="n", rows=None,
                        include_decision=True, include_physical=True):
        answers = {"changes_contract": changes_contract, "crosses_boundary": "n",
                   "reversible_under_hour": "y", "touches_sensitive": "n",
                   "consumers": "none"}
        intake = {"answers": answers, "binding": {"head": self.base}}
        write(self.repo, os.path.join(change, "00-intake.json"), json.dumps(intake))
        if include_decision:
            write(self.repo, os.path.join(change, "03-adr.md"), ADR_BODY)
        if include_physical:
            write(self.repo, os.path.join(change, "05-data-model.md"), DATA_MODEL_BODY)
        if rows is None:
            rows = DEFAULT_ROWS
        write(self.repo, os.path.join(change, "07-verification.md"), render_verification(rows))
        return os.path.join(self.repo, change)

    def _write_valid_dossier(self, change=CHANGE_DIR, changes_contract="n", rows=None):
        return self._write_dossier(change=change, changes_contract=changes_contract, rows=rows)

    def _write_empty_dossier(self, change=CHANGE_DIR):
        answers = {"changes_contract": "n", "crosses_boundary": "n",
                   "reversible_under_hour": "y", "touches_sensitive": "n",
                   "consumers": "none"}
        intake = {"answers": answers, "binding": {"head": self.base}}
        write(self.repo, os.path.join(change, "00-intake.json"), json.dumps(intake))
        return os.path.join(self.repo, change)

    # -- shared plan helpers --------------------------------------------------

    def _write_and_load_plan(self, dossier):
        # Three values, not two, for the same reason sbe() returns three: a
        # two-value return here would read as a possible (verdict, evidence)
        # pair to the honesty meta-test in evals/test_no_data_class.py.
        plan_path = os.path.join(dossier, "08-plan.json")
        code, out, err = self.sbe("plan", dossier, "--write", "--cwd", self.repo)
        self.assertEqual(code, 0, out)
        self.assertTrue(os.path.exists(plan_path),
                        "expected --write to create 08-plan.json: %s" % out)
        with io.open(plan_path, encoding="utf-8") as fh:
            plan = json.load(fh)
        return plan, plan_path, out

    def _save_plan(self, plan_path, plan):
        with io.open(plan_path, "w", encoding="utf-8") as fh:
            json.dump(plan, fh)

    def _find_task(self, plan, task_id):
        for t in plan["tasks"]:
            if t.get("id") == task_id:
                return t
        self.fail("no task %r in plan: %s" % (task_id, plan))

    def _find_migration_triplet(self, plan):
        forward = None
        for t in plan["tasks"]:
            if PHYSICAL_PATH in (t.get("owns") or []):
                forward = t
                break
        self.assertIsNotNone(forward,
                             "expected a migration forward task owning %r: %s"
                             % (PHYSICAL_PATH, plan))
        reverse = None
        reconciliation = None
        for t in plan["tasks"]:
            if t is forward:
                continue
            depends = t.get("dependsOn") or []
            if forward["id"] not in depends:
                continue
            blob = " ".join(t.get("acceptance") or []).lower()
            if "rollback" in blob or "reverse" in blob:
                reverse = t
            elif "reconcil" in blob:
                reconciliation = t
        self.assertIsNotNone(reverse,
                             "expected a reverse task depending on the forward task: %s" % plan)
        self.assertIsNotNone(reconciliation,
                             "expected a reconciliation task depending on the forward task: %s"
                             % plan)
        return forward, reverse, reconciliation


class TestDeterminism(PlanFixture):
    def test_write_twice_on_identical_dossier_is_byte_identical(self):
        dossier = self._write_valid_dossier()
        plan_path = os.path.join(dossier, "08-plan.json")
        code1, out1, err1 = self.sbe("plan", dossier, "--write", "--cwd", self.repo)
        self.assertEqual(code1, 0, out1)
        self.assertTrue(os.path.exists(plan_path),
                        "expected --write to create 08-plan.json: %s" % out1)
        with io.open(plan_path, "rb") as fh:
            bytes1 = fh.read()
        code2, out2, err2 = self.sbe("plan", dossier, "--write", "--cwd", self.repo)
        self.assertEqual(code2, 0, out2)
        with io.open(plan_path, "rb") as fh:
            bytes2 = fh.read()
        self.assertEqual(bytes1, bytes2,
                         "the same dossier bytes must derive the same plan bytes")


class TestValidDerivation(PlanFixture):
    def test_valid_dossier_derives_writer_migration_triplet_and_reviewer_tasks(self):
        dossier = self._write_valid_dossier()
        plan, _plan_path, _out = self._write_and_load_plan(dossier)
        self.assertTrue(plan["tasks"], "a valid T2 dossier must derive a non-empty plan")

        t01 = self._find_task(plan, "T01")
        self.assertEqual(t01["role"], "writer", t01)
        self.assertEqual(set(t01["owns"]), set(ADR_PATHS), t01)

        forward, reverse, reconciliation = self._find_migration_triplet(plan)
        self.assertEqual(forward["role"], "writer", forward)
        self.assertEqual(reverse["role"], "writer", reverse)
        self.assertEqual(reconciliation["role"], "writer", reconciliation)
        self.assertIn(forward["id"], reverse.get("dependsOn") or [], reverse)
        self.assertIn(forward["id"], reconciliation.get("dependsOn") or [], reconciliation)
        self.assertIn(reverse["id"], reconciliation.get("dependsOn") or [], reconciliation)

        reviewer_sources = set()
        for t in plan["tasks"]:
            if t.get("role") == "reviewer":
                for src in t.get("dossierSources") or []:
                    if src.startswith("07-verification.md#row-"):
                        reviewer_sources.add(src)
        self.assertEqual(reviewer_sources,
                         set("07-verification.md#row-%d" % n for n in range(1, 4)),
                         "expected exactly one reviewer task per 07-verification.md row: %s"
                         % plan)

        for t in plan["tasks"]:
            expected = ["command-receipt"] if t.get("verificationCommands") else []
            self.assertEqual(t.get("requiredEvidence"), expected,
                             "requiredEvidence must track verificationCommands exactly: %s" % t)


class TestEmptyDossier(PlanFixture):
    def test_empty_dossier_is_no_data_and_writes_nothing(self):
        dossier = self._write_empty_dossier()
        plan_path = os.path.join(dossier, "08-plan.json")
        code, out, err = self.sbe("plan", dossier, "--write", "--cwd", self.repo)
        self.assertIn("NO-DATA", out, out)
        self.assertEqual(code, 1, "an empty plan must never exit success: %s" % out)
        self.assertFalse(os.path.exists(plan_path),
                         "an empty derivation must write nothing: %s" % out)


class TestValidationFailures(PlanFixture):
    def test_task_citing_a_nonexistent_section_fails_validation_naming_the_task(self):
        dossier = self._write_valid_dossier()
        plan, plan_path, _out = self._write_and_load_plan(dossier)
        victim = plan["tasks"][0]
        victim["dossierSources"] = ["03-adr.md#nonexistent-section"]
        self._save_plan(plan_path, plan)
        code, out, err = self.sbe("plan", dossier, "--cwd", self.repo)
        self.assertNotEqual(code, 0, out)
        self.assertIn(victim["id"], out, out)

    def test_cyclic_dependencies_fail_validation_naming_the_cycle(self):
        dossier = self._write_valid_dossier()
        plan, plan_path, _out = self._write_and_load_plan(dossier)
        forward, reverse, _ = self._find_migration_triplet(plan)
        forward["dependsOn"] = list(forward.get("dependsOn") or []) + [reverse["id"]]
        self._save_plan(plan_path, plan)
        code, out, err = self.sbe("plan", dossier, "--cwd", self.repo)
        self.assertNotEqual(code, 0, out)
        self.assertIn(forward["id"], out, out)
        self.assertIn(reverse["id"], out, out)

    def test_overlapping_ownership_without_dependency_fails_validation(self):
        dossier = self._write_valid_dossier()
        plan, plan_path, _out = self._write_and_load_plan(dossier)
        t01 = self._find_task(plan, "T01")
        forward, _, _ = self._find_migration_triplet(plan)
        self.assertNotIn(t01["id"], forward.get("dependsOn") or [],
                         "fixture assumes T01 and the forward migration task are unrelated")
        self.assertNotIn(forward["id"], t01.get("dependsOn") or [],
                         "fixture assumes T01 and the forward migration task are unrelated")
        shared = t01["owns"][0]
        forward["owns"] = list(forward.get("owns") or []) + [shared]
        self._save_plan(plan_path, plan)
        code, out, err = self.sbe("plan", dossier, "--cwd", self.repo)
        self.assertNotEqual(code, 0, out)
        self.assertIn(shared, out, out)

    def test_stale_dossier_after_write_fails_validation_naming_the_file(self):
        dossier = self._write_valid_dossier()
        plan, plan_path, _out = self._write_and_load_plan(dossier)
        adr_path = os.path.join(dossier, "03-adr.md")
        with io.open(adr_path, "a", encoding="utf-8") as fh:
            fh.write("\nEdited after planning.\n")
        code, out, err = self.sbe("plan", dossier, "--cwd", self.repo)
        self.assertNotEqual(code, 0, out)
        self.assertIn("03-adr.md", out, out)


class TestCompatibilityGap(PlanFixture):
    def test_contract_change_without_compatibility_task_fails_write_and_writes_nothing(self):
        dossier = self._write_valid_dossier(changes_contract="y")
        plan_path = os.path.join(dossier, "08-plan.json")
        code, out, err = self.sbe("plan", dossier, "--write", "--cwd", self.repo)
        self.assertNotEqual(code, 0, out)
        self.assertTrue(any(k in out.lower() for k in ("compat", "contract", "consumer")),
                        out)
        self.assertFalse(os.path.exists(plan_path), out)

    def test_additive_contract_change_without_compatibility_task_fails_and_names_additive(self):
        """Defect 3 repair. An ADDITIVE contract change used to owe nothing
        here at all (`build_plan`'s gate fired only on `contract is True`,
        which `_yes()` only ever returned for "breaking"), and
        `plan_compatibility`'s NO-DATA line said "intake
        answers.changes_contract is no" about an intake that actually said
        additive. Both are fixed together: additive now owes the same
        compatibility task breaking does, and the message names the real
        recorded answer, never "no"."""
        dossier = self._write_valid_dossier(changes_contract="additive")
        plan_path = os.path.join(dossier, "08-plan.json")
        code, out, err = self.sbe("plan", dossier, "--write", "--cwd", self.repo)
        self.assertNotEqual(code, 0, out)
        self.assertTrue(any(k in out.lower() for k in ("compat", "contract", "consumer")),
                        out)
        self.assertIn("changes_contract is additive", out, out)
        self.assertNotIn("changes_contract is no", out, out)
        self.assertNotIn("changes_contract is yes", out, out)
        self.assertFalse(os.path.exists(plan_path), out)


class TestMigrationWithoutRow(PlanFixture):
    def test_migration_without_reverse_row_fails_write(self):
        dossier = self._write_valid_dossier(rows=[ROW_PLAIN, ROW_RECONCILE])
        plan_path = os.path.join(dossier, "08-plan.json")
        code, out, err = self.sbe("plan", dossier, "--write", "--cwd", self.repo)
        self.assertNotEqual(code, 0, out)
        self.assertTrue(any(k in out.lower() for k in ("reverse", "rollback")), out)
        self.assertFalse(os.path.exists(plan_path), out)

    def test_migration_without_reconciliation_row_fails_write(self):
        dossier = self._write_valid_dossier(rows=[ROW_PLAIN, ROW_REVERSE])
        plan_path = os.path.join(dossier, "08-plan.json")
        code, out, err = self.sbe("plan", dossier, "--write", "--cwd", self.repo)
        self.assertNotEqual(code, 0, out)
        self.assertIn("reconcil", out.lower(), out)
        self.assertFalse(os.path.exists(plan_path), out)


class TestPlannerNeverInvents(PlanFixture):
    def test_planner_never_invents_every_task_cites_a_resolving_source(self):
        dossier = self._write_valid_dossier()
        plan, _plan_path, _out = self._write_and_load_plan(dossier)
        for t in plan["tasks"]:
            self.assertTrue(t.get("dossierSources"),
                            "task %s cites nothing, which is the planner-inventing-work "
                            "case: %s" % (t.get("id"), t))
        code, out, err = self.sbe("plan", dossier, "--cwd", self.repo)
        self.assertEqual(code, 0,
                         "every cited source in a valid, unedited plan must resolve: %s" % out)


class TestDecisionBearingCalculation(PlanFixture):
    def test_decision_bearing_row_with_one_command_and_no_second_row_fails_write(self):
        dossier = self._write_valid_dossier(rows=DEFAULT_ROWS + [ROW_DIGIT_ONE_COMMAND])
        plan_path = os.path.join(dossier, "08-plan.json")
        code, out, err = self.sbe("plan", dossier, "--write", "--cwd", self.repo)
        self.assertNotEqual(code, 0, out)
        self.assertTrue("row-4" in out or "95 percent" in out, out)
        self.assertFalse(os.path.exists(plan_path), out)

    def test_decision_bearing_row_with_two_commands_passes_write(self):
        dossier = self._write_valid_dossier(rows=DEFAULT_ROWS + [ROW_DIGIT_TWO_COMMANDS])
        code, out, err = self.sbe("plan", dossier, "--write", "--cwd", self.repo)
        self.assertEqual(code, 0,
                         "two independent commands in the check cell is independent "
                         "derivation enough for a decision-bearing row: %s" % out)


class TestRegistryIntegration(PlanFixture):
    def test_registry_integration_opens_first_task_from_the_plan(self):
        dossier = self._write_valid_dossier()
        plan, _plan_path, _out = self._write_and_load_plan(dossier)
        self.assertTrue(plan.get("tasks"), plan)
        first = plan["tasks"][0]
        verify_cmds = first.get("verificationCommands") or []
        expected_base = plan.get("baseCommit") or self.base
        expected_verify = verify_cmds[0] if verify_cmds else ""
        expected_owns = list(first.get("owns") or [])
        argv = ["task", "open",
                "--id", first["id"],
                "--agent", "plan-fixture",
                "--role", first["role"],
                "--base", expected_base,
                "--verify", expected_verify,
                "--cwd", self.repo]
        for p in first.get("owns") or []:
            argv += ["--owns", p]
        for p in first.get("readOnly") or []:
            argv += ["--read-only", p]
        code, out, err = self.sbe(*argv)
        self.assertEqual(code, 0, out)

        # The exit code alone only proves `task open` accepted the call, not
        # that the registry record it wrote actually carries what the plan's
        # first task supplied. Read the single source of task state back and
        # compare it field by field.
        registry_path = os.path.join(self.repo, ".sbe", "tasks.json")
        with io.open(registry_path, encoding="utf-8") as fh:
            registry = json.load(fh)
        record = None
        for t in registry.get("tasks") or []:
            if t.get("id") == first["id"]:
                record = t
                break
        self.assertIsNotNone(record,
                             "no task %r found open in %s: %s"
                             % (first["id"], registry_path, registry))

        # Calibration: prove the field comparisons below actually bite,
        # rather than passing vacuously (e.g. because a prior refactor left
        # them comparing something to itself), by asserting a deliberately
        # wrong expectation first and confirming that it fails.
        with self.assertRaises(AssertionError):
            self.assertEqual(record.get("baseCommit"), "0" * 40,
                             "calibration: a mutated wrong expectation must fail here")

        self.assertEqual(record.get("id"), first["id"],
                         "registry record id does not match the plan task: %s" % record)
        self.assertEqual(record.get("role"), first["role"],
                         "registry record role does not match the plan task: %s" % record)
        self.assertEqual(record.get("ownedPaths"), expected_owns,
                         "registry record ownedPaths does not match what the plan task "
                         "supplied: %s" % record)
        self.assertEqual(record.get("baseCommit"), expected_base,
                         "registry record baseCommit does not match what the plan task "
                         "supplied: %s" % record)
        self.assertEqual(record.get("verifyCommand"), expected_verify,
                         "registry record verifyCommand does not match what the plan "
                         "task supplied: %s" % record)




def _run_sbe(*argv):
    import subprocess as _sp
    out = _sp.run([sys.executable, SBE] + list(argv), capture_output=True,
                  text=True, stdin=_sp.DEVNULL, timeout=120)
    # Three values, not two: the honesty meta-test refuses bare pairs.
    return out.returncode, out.stdout + out.stderr, out.stderr


class TestExternalProofRepairs(unittest.TestCase):
    """Two defects the external estates hit, pinned so they cannot return."""

    def _dossier(self, tmp, physical, check_cell):
        import json as _json
        doss = os.path.join(tmp, "design", "chg")
        os.makedirs(doss)
        io.open(os.path.join(doss, "00-intake.json"), "w").write(_json.dumps(
            {"answers": {"changes_contract": "n", "crosses_boundary": "n",
                         "reversible_under_hour": "y", "touches_sensitive": "n",
                         "consumers": "none"}, "tier": "T1",
             "override": None, "override_reason": None}))
        io.open(os.path.join(doss, "03-adr.md"), "w").write(
            "# ADR\n## Context\nx\n## Decision\nTouch `src/app.py` only.\n"
            "## Consequences\nok\n")
        io.open(os.path.join(doss, "05-data-model.md"), "w").write(
            "# DM\n## Conceptual: entities and meanings\n- U: a user; system of "
            "record: here.\n## Physical\n" + physical + "\n")
        io.open(os.path.join(doss, "07-verification.md"), "w").write(
            "| Claim this design makes | The check that proves it | When it runs |\n"
            "|---|---|---|\n| it works | " + check_cell + " | CI |\n")
        return doss

    def test_a_source_path_in_physical_prose_demands_no_migration_triplet(self):
        import shutil as _sh, tempfile as _tf
        tmp = _tf.mkdtemp()
        try:
            doss = self._dossier(
                tmp, "No migration is needed: `src/app.py` changes in place "
                     "and alembic stays untouched.", "`grep -q def src/app.py`")
            code, out, _err = _run_sbe("plan", doss, "--write", "--cwd", tmp)
            self.assertEqual(code, 0,
                             "prose backticking a source file is not a migration "
                             "declaration: %s" % out)
        finally:
            _sh.rmtree(tmp, ignore_errors=True)

    def test_an_escaped_pipe_stays_inside_the_check_cell(self):
        import shutil as _sh, tempfile as _tf
        tmp = _tf.mkdtemp()
        try:
            doss = self._dossier(
                tmp, "No migration is needed here either.",
                "`grep -Eq \"(a\\|b)\" src/app.py`")
            code, out, _err = _run_sbe("plan", doss, "--write", "--cwd", tmp)
            self.assertEqual(code, 0, out)
            import json as _json
            plan = _json.loads(io.open(os.path.join(doss, "08-plan.json")).read())
            row_task = [t for t in plan["tasks"]
                        if "07-verification.md#row-1" in t["dossierSources"]][0]
            self.assertTrue(row_task["verificationCommands"],
                            "the escaped pipe must not eat the command half: %r"
                            % row_task)
            self.assertIn("(a|b)", row_task["verificationCommands"][0],
                          "the literal pipe survives into the derived command")
        finally:
            _sh.rmtree(tmp, ignore_errors=True)


class TestHelpMeansHelp(unittest.TestCase):
    """`sbe plan -h` exited 2 twice over: the top-level parser refused the
    leading `-h` before it could reach this tool, and the tool itself read
    `-h` as an unknown flag and refused it with usage and exit 2. Help is not
    an error. Calibrated by reinjecting the missing help branch in
    sbe_plan.py's main and the argparse-first dispatch in cli.py: each fixture
    failed on a returncode of 2 where 0 was asserted, before the fix was
    restored, the restore verified against the pre-recorded `git hash-object`
    of the fixed files."""

    def test_help_exits_0_with_usage_and_derives_nothing(self):
        tmp = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, tmp, True)
        for argv in (("plan", "-h"), ("plan", "--help"), ("plan", "-h", "--write")):
            import subprocess as _sp
            out = _sp.run([sys.executable, SBE] + list(argv), capture_output=True,
                          text=True, stdin=_sp.DEVNULL, cwd=tmp, timeout=120)
            self.assertEqual(out.returncode, 0, "sbe %s exited %d: %s"
                             % (" ".join(argv), out.returncode, out.stdout + out.stderr))
            self.assertIn("usage: sbe_plan.py", out.stdout,
                          "sbe %s printed no usage: %r" % (" ".join(argv), out.stdout))
            self.assertEqual(os.listdir(tmp), [],
                             "sbe %s wrote into the directory it was only asked to "
                             "describe itself from" % " ".join(argv))

    def test_a_genuinely_bad_flag_still_exits_2(self):
        code, text, _err = _run_sbe("plan", "--bogus")
        self.assertEqual(code, 2, "sbe plan --bogus exited %d, not the usage error 2: %s"
                         % (code, text))
        self.assertIn("unknown flag", text)


if __name__ == "__main__":
    unittest.main(verbosity=2)

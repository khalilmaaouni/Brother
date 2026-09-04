#!/usr/bin/env python3
"""Fixtures for `sbe work brief`. Run: python3 tools/test_sbe_work_brief.py

`sbe work brief` reads a validated plan and an already-open registry, and
turns one plan task into a deterministic, size-capped JSON work order: never
opening a worktree, never mutating the registry, never mutating the plan.
The rules under test come from the LT-101 brief: schemaVersion, taskId,
title, why, baselineCommit, planPath, scope, mustNotTouch, dependencies,
acceptance, verificationCommands, relevantPointers, knownConstraints,
stopConditions, requiredEvidenceKind, model, maxAttemptsPerApproach, sorted
keys, no timestamp anywhere, byte-identical for identical repository state,
refused over an 8192 byte cap naming the largest sections.

Fixture technique copied from tools/test_sbe_work.py rather than imported:
suites in this project stay standalone, so a disagreement between the two
would be a defect to fix in both, never a shared import to chase. The same
two techniques are used here: a real dossier and a real derived plan built
through the landed `sbe plan --write`, and registry records seeded directly
into .sbe/tasks.json where a scenario needs an OPEN record to already exist.
"""
import io
import json
import os
import subprocess
import sys
import tempfile
import shutil
import unittest

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


# -- dossier body constants, copied from tools/test_sbe_work.py (itself copied
# from tools/test_sbe_plan.py) rather than imported: only what `sbe work
# brief` fixtures need is kept.

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

CHANGE_DIR = "design/widget-cache"


def render_verification(rows):
    lines = ["# 07. Verification plan", "",
             "| Claim this design makes | The check that proves it | When it runs |",
             "|---|---|---|"]
    for claim, check, when in rows:
        lines.append("| %s | %s | %s |" % (claim, check, when))
    return "\n".join(lines) + "\n"


class BriefFixture(unittest.TestCase):
    """A fresh repository and a fresh dossier per test, mirroring
    tools/test_sbe_work.py's WorkFixture. The plan is derived inside each
    test body (never in setUp), for the same reason WorkFixture does that:
    a real command failure belongs to the test, not to an opaque setUp
    ERROR."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.repo = os.path.join(self.tmp, "repo")
        os.makedirs(self.repo)
        git(self.repo, "init", "-q")
        git(self.repo, "config", "user.email", "fixture@example.invalid")
        git(self.repo, "config", "user.name", "fixture")
        write(self.repo, "README.md", "base\n")
        git(self.repo, "add", "-A")
        git(self.repo, "commit", "-qm", "base")
        self.base = git(self.repo, "rev-parse", "HEAD")
        self.dossier = self._write_dossier()
        self.change_id = os.path.basename(self.dossier)

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def sbe(self, *argv):
        out = subprocess.run([sys.executable, SBE] + list(argv), capture_output=True, text=True)
        return out.returncode, out.stdout + out.stderr, out.stderr

    def brief(self, *argv):
        return self.sbe("work", "brief", "--cwd", self.repo, *argv)

    # -- dossier and plan -----------------------------------------------------

    def _write_dossier(self):
        answers = {"changes_contract": "n", "crosses_boundary": "n",
                   "reversible_under_hour": "y", "touches_sensitive": "n",
                   "consumers": "none"}
        intake = {"answers": answers, "binding": {"head": self.base}}
        write(self.repo, os.path.join(CHANGE_DIR, "00-intake.json"), json.dumps(intake))
        write(self.repo, os.path.join(CHANGE_DIR, "03-adr.md"), ADR_BODY)
        write(self.repo, os.path.join(CHANGE_DIR, "05-data-model.md"), DATA_MODEL_BODY)
        write(self.repo, os.path.join(CHANGE_DIR, "07-verification.md"),
              render_verification(DEFAULT_ROWS))
        return os.path.join(self.repo, CHANGE_DIR)

    def _plan(self):
        """(plan dict, plan path, the command's own output)."""
        plan_path = os.path.join(self.dossier, "08-plan.json")
        code, out, _err = self.sbe("plan", self.dossier, "--write", "--cwd", self.repo)
        self.assertEqual(code, 0, "fixture setup could not derive a plan through the "
                                  "landed `sbe plan --write`; this is a fixture bug, not "
                                  "a `sbe work brief` defect: %s" % out)
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

    def _find_reviewer(self, plan):
        for t in plan["tasks"]:
            if t.get("role") == "reviewer":
                return t
        self.fail("expected at least one reviewer task: %s" % plan)

    def _find_migration_triplet(self, plan):
        """(forward, reverse, reconciliation), copied from
        tools/test_sbe_work.py's helper of the same name."""
        forward = None
        physical = "migrations/0001_widget_cache.sql"
        for t in plan["tasks"]:
            if physical in (t.get("owns") or []):
                forward = t
                break
        self.assertIsNotNone(forward, "expected a migration forward task owning %r: %s"
                             % (physical, plan))
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

    # -- registry, seeded directly the way tools/test_sbe_work.py does -------

    def _registry_path(self):
        return os.path.join(self.repo, ".sbe", "tasks.json")

    def _seed(self, record):
        path = self._registry_path()
        if os.path.exists(path):
            with io.open(path, encoding="utf-8") as fh:
                data = json.load(fh)
        else:
            data = {"schemaVersion": "1.0", "tasks": []}
        data["tasks"].append(record)
        write(self.repo, os.path.join(".sbe", "tasks.json"), json.dumps(data))

    def _base_record(self, task_id, **overrides):
        record = {
            "id": task_id, "agent": "alpha", "role": "writer", "worktree": None,
            "ownedPaths": [], "readOnlyPaths": [], "baseCommit": self.base,
            "expiry": None, "status": "open", "verifyCommand": "true",
            "evidenceId": None, "openedAt": "2026-07-30T00:00:00Z", "closedAt": None,
        }
        record.update(overrides)
        return record


CONTRACT_FIELDS = {
    "schemaVersion", "taskId", "title", "why", "baselineCommit", "planPath", "scope",
    "mustNotTouch", "dependencies", "acceptance", "verificationCommands",
    "relevantPointers", "knownConstraints", "stopConditions", "requiredEvidenceKind",
    "model", "maxAttemptsPerApproach",
}

COORDINATION_FILES = (".sbe/tasks.json", "CHECKSUMS.sha256", "VERSION",
                      ".claude-plugin/", "08-plan.json")


class TestBriefValid(BriefFixture):
    def test_brief_json_carries_exactly_the_contract_fields_for_a_valid_task(self):
        plan, plan_path, _out = self._plan()
        t01 = self._find_task(plan, "T01")
        code, text, _ = self.brief("--plan", plan_path, "--task", t01["id"], "--json")
        self.assertEqual(code, 0, text)
        brief = json.loads(text)

        self.assertEqual(set(brief.keys()), CONTRACT_FIELDS,
                         "brief fields do not match the LT-101 contract exactly: %s"
                         % sorted(brief.keys()))
        self.assertEqual(brief["schemaVersion"], "1.0")
        self.assertEqual(brief["taskId"], t01["id"])
        self.assertEqual(brief["scope"], t01["owns"])
        self.assertEqual(brief["dependencies"], t01.get("dependsOn") or [])
        self.assertEqual(brief["acceptance"], t01["acceptance"])
        self.assertEqual(brief["verificationCommands"], t01.get("verificationCommands") or [])
        self.assertEqual(brief["relevantPointers"], t01["dossierSources"])
        self.assertEqual(brief["requiredEvidenceKind"], t01.get("requiredEvidence") or [])
        self.assertEqual(brief["model"], "sonnet-5")
        self.assertEqual(brief["maxAttemptsPerApproach"], 2)
        self.assertEqual(brief["planPath"], plan_path)

        head = git(self.repo, "rev-parse", "HEAD")
        self.assertEqual(brief["baselineCommit"], head)

        for coordination in COORDINATION_FILES:
            self.assertIn(coordination, brief["mustNotTouch"],
                          "expected %r among mustNotTouch: %s" % (coordination, brief))

        self.assertNotIn("timestamp", "".join(brief.keys()).lower())
        self.assertNotIn("At\"", text, "no ISO timestamp field should appear in the brief")

    def test_brief_output_is_byte_identical_across_two_runs_of_the_same_state(self):
        plan, plan_path, _out = self._plan()
        t01 = self._find_task(plan, "T01")
        code1, text1, _ = self.brief("--plan", plan_path, "--task", t01["id"], "--json")
        code2, text2, _ = self.brief("--plan", plan_path, "--task", t01["id"], "--json")
        self.assertEqual(code1, 0, text1)
        self.assertEqual(code2, 0, text2)
        self.assertEqual(text1, text2,
                         "identical repository state must produce byte-identical output")

    def test_brief_never_opens_a_worktree_never_touches_the_registry(self):
        plan, plan_path, _out = self._plan()
        t01 = self._find_task(plan, "T01")
        registry_path = self._registry_path()
        self.assertFalse(os.path.exists(registry_path),
                         "fixture precondition: no registry should exist yet")
        before_worktrees = git(self.repo, "worktree", "list")

        code, text, _ = self.brief("--plan", plan_path, "--task", t01["id"], "--json")
        self.assertEqual(code, 0, text)

        self.assertFalse(os.path.exists(registry_path),
                         "sbe work brief must never touch the registry file: %s"
                         % registry_path)
        after_worktrees = git(self.repo, "worktree", "list")
        self.assertEqual(before_worktrees, after_worktrees,
                         "sbe work brief must never open a worktree")


class TestBriefOut(BriefFixture):
    def test_out_writes_atomically_and_matches_the_json_stdout_form(self):
        plan, plan_path, _out = self._plan()
        t01 = self._find_task(plan, "T01")
        code, stdout_text, _ = self.brief("--plan", plan_path, "--task", t01["id"], "--json")
        self.assertEqual(code, 0, stdout_text)

        out_path = os.path.join(self.tmp, "nested", "brief.json")
        code, text, _ = self.brief("--plan", plan_path, "--task", t01["id"], "--out", out_path)
        self.assertEqual(code, 0, text)
        self.assertIn(out_path, text)
        self.assertTrue(os.path.isfile(out_path))

        with io.open(out_path, encoding="utf-8") as fh:
            file_text = fh.read()
        self.assertEqual(file_text, stdout_text,
                         "the --out file must carry exactly the same bytes as --json prints")

    def test_out_leaves_no_temp_file_behind_on_success(self):
        plan, plan_path, _out = self._plan()
        t01 = self._find_task(plan, "T01")
        out_dir = os.path.join(self.tmp, "outdir")
        os.makedirs(out_dir)
        out_path = os.path.join(out_dir, "brief.json")
        code, text, _ = self.brief("--plan", plan_path, "--task", t01["id"], "--out", out_path)
        self.assertEqual(code, 0, text)
        self.assertEqual(os.listdir(out_dir), ["brief.json"],
                         "an atomic write must leave no tempfile sibling behind: %s"
                         % os.listdir(out_dir))


class TestBriefRefusals(BriefFixture):
    def test_refuses_an_unknown_task_id(self):
        _plan, plan_path, _out = self._plan()
        code, text, _ = self.brief("--plan", plan_path, "--task", "T99-not-in-plan")
        self.assertEqual(code, 2, text)
        self.assertIn("T99-not-in-plan", text)

    def test_refuses_when_a_dependency_is_not_yet_closed(self):
        plan, plan_path, _out = self._plan()
        forward, reverse, _recon = self._find_migration_triplet(plan)
        code, text, _ = self.brief("--plan", plan_path, "--task", reverse["id"])
        self.assertNotEqual(code, 0, text)
        self.assertIn(forward["id"], text)

    def test_refuses_when_a_dependency_was_force_closed(self):
        plan, plan_path, _out = self._plan()
        forward, reverse, _recon = self._find_migration_triplet(plan)
        self._seed(self._base_record(forward["id"], status="closed",
                                     ownedPaths=forward["owns"],
                                     forced={"who": "operator", "why": "hotfix",
                                            "at": "2026-07-30T00:00:00Z", "verdict": "FAIL",
                                            "violations": []}))
        code, text, _ = self.brief("--plan", plan_path, "--task", reverse["id"])
        self.assertNotEqual(code, 0, text)
        self.assertIn(forward["id"], text)
        self.assertIn("forced", text.lower())

    def test_refuses_when_the_plan_fails_validation(self):
        plan, _plan_path, _out = self._plan()
        t01 = self._find_task(plan, "T01")
        t01["dossierSources"] = []
        edited_path = os.path.join(self.dossier, "08-plan-broken.json")
        self._save_plan(edited_path, plan)
        code, text, _ = self.brief("--plan", edited_path, "--task", t01["id"])
        self.assertNotEqual(code, 0, text)
        self.assertIn("PLAN-CHECK", text)

    def test_refuses_an_absent_plan_file_by_name_not_a_silent_default(self):
        code, text, _ = self.brief("--plan", os.path.join(self.tmp, "nope-08-plan.json"),
                                   "--task", "T01")
        self.assertEqual(code, 2, text)
        self.assertIn("nope-08-plan.json", text)


class TestBriefClaimedTask(BriefFixture):
    def test_refuses_a_task_with_an_open_registry_record(self):
        plan, plan_path, _out = self._plan()
        t01 = self._find_task(plan, "T01")
        self._seed(self._base_record(t01["id"], status="open", agent="beta",
                                     ownedPaths=t01["owns"]))
        code, text, _ = self.brief("--plan", plan_path, "--task", t01["id"])
        self.assertNotEqual(code, 0, text)
        self.assertIn(t01["id"], text)
        self.assertIn("beta", text)

    def test_allows_a_task_whose_last_registry_record_is_closed(self):
        plan, plan_path, _out = self._plan()
        t01 = self._find_task(plan, "T01")
        self._seed(self._base_record(t01["id"], status="closed", ownedPaths=t01["owns"]))
        code, text, _ = self.brief("--plan", plan_path, "--task", t01["id"], "--json")
        self.assertEqual(code, 0,
                         "a CLOSED last record must not block a fresh brief: %s" % text)


class TestBriefSizeCap(BriefFixture):
    def test_refuses_a_brief_over_the_byte_cap_and_names_the_largest_sections(self):
        plan, plan_path, _out = self._plan()
        t01 = self._find_task(plan, "T01")
        t01["acceptance"] = [
            "padding acceptance entry number %d, long enough on its own to push this "
            "task's JSON payload well past the byte cap without touching anything else "
            "the plan validator checks about this task" % i
            for i in range(200)
        ]
        edited_path = os.path.join(self.dossier, "08-plan-oversized.json")
        self._save_plan(edited_path, plan)
        code, text, _ = self.brief("--plan", edited_path, "--task", t01["id"])
        self.assertNotEqual(code, 0, text)
        self.assertIn("8192", text)
        self.assertIn("acceptance", text)


class TestBriefAdversarial(BriefFixture):
    def test_path_traversal_task_id_is_refused_as_unknown_never_resolved_as_a_path(self):
        _plan, plan_path, _out = self._plan()
        code, text, _ = self.brief("--plan", plan_path, "--task", "../../../etc/passwd")
        self.assertEqual(code, 2, text)
        self.assertIn("no task", text.lower())

    def test_task_id_matching_stays_case_sensitive_never_aliased(self):
        plan, plan_path, _out = self._plan()
        t01 = self._find_task(plan, "T01")
        lowered = t01["id"].lower()
        self.assertNotEqual(lowered, t01["id"],
                            "fixture assumption broken: task id has letters to lowercase")
        code, text, _ = self.brief("--plan", plan_path, "--task", lowered)
        self.assertEqual(code, 2,
                         "%r must not alias to %r: %s" % (lowered, t01["id"], text))

    def test_duplicated_task_ids_in_the_plan_are_refused_by_name_never_guessed_at(self):
        """The plan's own loader refuses a duplicated id before any check body
        runs (every PLAN-CHECK line below names the same id), so a brief is
        never built from an ambiguous plan: rule 1 (any FAIL refuses by
        name) covers this shape too, and brief never has to guess which of
        the two same-id entries a caller meant."""
        plan, plan_path, _out = self._plan()
        reviewer = self._find_reviewer(plan)  # owns nothing: safe to duplicate
        duplicate = dict(reviewer)
        duplicate["title"] = "a distinct second entry sharing the same id"
        plan["tasks"].append(duplicate)
        edited_path = os.path.join(self.dossier, "08-plan-dup-id.json")
        self._save_plan(edited_path, plan)
        code, text, _ = self.brief("--plan", edited_path, "--task", reviewer["id"])
        self.assertNotEqual(code, 0, text)
        self.assertIn("PLAN-CHECK", text)
        self.assertIn(reviewer["id"], text)
        self.assertIn("more than once", text)

    def test_malformed_acceptance_entries_are_dropped_never_crash_the_command(self):
        plan, plan_path, _out = self._plan()
        t01 = self._find_task(plan, "T01")
        original = t01["acceptance"][0]
        t01["acceptance"] = [original, 42, None, {"nested": "object"}, "   ", ["x"]]
        edited_path = os.path.join(self.dossier, "08-plan-malformed-acceptance.json")
        self._save_plan(edited_path, plan)
        code, text, _ = self.brief("--plan", edited_path, "--task", t01["id"], "--json")
        self.assertEqual(code, 0, text)
        brief = json.loads(text)
        self.assertEqual(brief["acceptance"], [original],
                         "only the real string criterion should survive")

    def test_malformed_verification_command_entries_are_dropped_never_crash_the_command(self):
        plan, plan_path, _out = self._plan()
        t01 = self._find_task(plan, "T01")
        t01["verificationCommands"] = [123, None, "pytest tests/test_widget_cache.py",
                                       ["nested", "list"], "   "]
        edited_path = os.path.join(self.dossier, "08-plan-malformed-commands.json")
        self._save_plan(edited_path, plan)
        code, text, _ = self.brief("--plan", edited_path, "--task", t01["id"], "--json")
        self.assertEqual(code, 0, text)
        brief = json.loads(text)
        self.assertEqual(brief["verificationCommands"], ["pytest tests/test_widget_cache.py"])

    def test_quoted_verification_command_survives_byte_identically(self):
        plan, plan_path, _out = self._plan()
        t01 = self._find_task(plan, "T01")
        quoted_cmd = "python3 -c \"print('OK')\" --flag=\"value with spaces\""
        t01["verificationCommands"] = [quoted_cmd]
        edited_path = os.path.join(self.dossier, "08-plan-quoted-cmd.json")
        self._save_plan(edited_path, plan)
        code, text, _ = self.brief("--plan", edited_path, "--task", t01["id"], "--json")
        self.assertEqual(code, 0, text)
        brief = json.loads(text)
        self.assertEqual(brief["verificationCommands"], [quoted_cmd],
                         "the quoted command must round-trip byte-identically through JSON")

    def test_dirty_repository_is_named_in_known_constraints_not_fatal(self):
        plan, plan_path, _out = self._plan()
        t01 = self._find_task(plan, "T01")

        # The dossier and plan files written by _write_dossier()/_plan() are
        # never committed, so the repository is already dirty here.
        code, text, _ = self.brief("--plan", plan_path, "--task", t01["id"], "--json")
        self.assertEqual(code, 0, text)
        dirty_brief = json.loads(text)
        self.assertTrue(any("uncommitted" in c for c in dirty_brief["knownConstraints"]),
                        "a dirty repository must be named under knownConstraints: %s"
                        % dirty_brief["knownConstraints"])

        git(self.repo, "add", "-A")
        git(self.repo, "commit", "-qm", "dossier and plan committed")
        code, text, _ = self.brief("--plan", plan_path, "--task", t01["id"], "--json")
        self.assertEqual(code, 0, text)
        clean_brief = json.loads(text)
        self.assertEqual(clean_brief["knownConstraints"], [],
                         "a clean repository must name no dirty-tree constraint")

    def test_a_written_brief_is_a_snapshot_and_head_moving_later_does_not_rewrite_it(self):
        plan, plan_path, _out = self._plan()
        t01 = self._find_task(plan, "T01")
        out_path_before = os.path.join(self.tmp, "brief-before.json")
        code, text, _ = self.brief("--plan", plan_path, "--task", t01["id"],
                                   "--out", out_path_before)
        self.assertEqual(code, 0, text)
        head_before = git(self.repo, "rev-parse", "HEAD")
        with io.open(out_path_before, encoding="utf-8") as fh:
            brief_before = json.load(fh)
        self.assertEqual(brief_before["baselineCommit"], head_before)

        # HEAD moves in the repository AFTER the brief above was written.
        git(self.repo, "add", "-A")
        git(self.repo, "commit", "-qm", "head moves after the brief was captured")
        head_after = git(self.repo, "rev-parse", "HEAD")
        self.assertNotEqual(head_before, head_after)

        # The brief already on disk is a snapshot: nothing rewrites it later.
        with io.open(out_path_before, encoding="utf-8") as fh:
            brief_still = json.load(fh)
        self.assertEqual(brief_still["baselineCommit"], head_before,
                         "an already-written brief must never be silently updated")

        # A fresh brief taken now reflects the NEW head: HEAD is resolved
        # exactly once per run, not cached stale across the process.
        out_path_after = os.path.join(self.tmp, "brief-after.json")
        code, text, _ = self.brief("--plan", plan_path, "--task", t01["id"],
                                   "--out", out_path_after)
        self.assertEqual(code, 0, text)
        with io.open(out_path_after, encoding="utf-8") as fh:
            brief_fresh = json.load(fh)
        self.assertEqual(brief_fresh["baselineCommit"], head_after)


class TestBriefHelpMeansHelp(unittest.TestCase):
    def test_brief_help_exits_0_with_usage_and_creates_nothing(self):
        tmp = tempfile.mkdtemp()
        try:
            out = subprocess.run([sys.executable, SBE, "work", "brief", "-h"],
                                 capture_output=True, text=True, cwd=tmp)
            self.assertEqual(out.returncode, 0, out.stdout + out.stderr)
            self.assertIn("usage", (out.stdout + out.stderr).lower())
            self.assertEqual(os.listdir(tmp), [])
        finally:
            shutil.rmtree(tmp, ignore_errors=True)


if __name__ == "__main__":
    unittest.main(verbosity=2)

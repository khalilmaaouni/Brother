#!/usr/bin/env python3
"""LT-103: end-to-end team execution fixtures. Run: python3 tools/test_sbe_team_workflow.py

Spec of record: docs/plans/2026-08-04-lean-team-rebase.md (the LT-101/102/103
chain, vertical slice 1, /brothersbe:work) plus docs/specs/2026-07-30-sbe-work-
lifecycle.md, which tools/test_sbe_work.py and tools/test_sbe_work_brief.py
already exercise piece by piece. This file proves the WHOLE chain together,
in one scratch repository per test, driving the real `sbe work` and `sbe
status` surfaces as separate subprocesses, never mocking the engine.

The scratch repository carries a validated 08-plan.json with four tasks:

  T01  backend writer, owns src/widgets/lookup.py
  T02  docs writer, owns docs/widget-lookup.md (disjoint from T01)
  T03  reviewer, dependsOn T01, reads (never owns) src/widgets/lookup.py
  T04  a PLANTED overlap: a writer whose owns is the SAME path as T01's,
       dependsOn T01

The plan is written directly as JSON rather than derived through `sbe plan
--write` from an ADR/data-model/verification dossier: this file's four task
kinds (backend, docs, test, overlap) do not map onto that derivation's fixed
taxonomy (decision / forward-reverse-reconciliation / row), and the plan task
schema is already the documented contract both `sbe plan` and `sbe work` read
(LT-101's inventory in the plan doc above: id, title, role, dependsOn, owns,
readOnly, acceptance, verificationCommands, requiredEvidence,
requiredReviewers, dossierSources), so writing it by hand reads that same
contract rather than inventing a second one. The plan is still validated
through the real `sbe plan` command before any test trusts it (see
`_write_dossier_and_plan` below), the same discipline
tools/test_sbe_work.py's own `_plan()` helper applies to its derived plan.

T04's dependsOn edge on T01 is not decorative. tools/sbe_plan.py's
plan_ownership check refuses a plan where two writer tasks' owns sets share a
path and the dependency graph does not order them (and the derivation side,
build_plan's rule 8, adds exactly this edge automatically whenever it detects
the same overlap) -- so a plan carrying the planted overlap WITHOUT this edge
would refuse validation for every task, backend and docs included, not just
the overlap task. With the edge in place, T01 and T02 are ready (no unmet
dependency) and start freely; T04 is blocked behind T01 by the same
dependency-not-closed refusal that blocks any other unmet dependency, which
is this system's actual mechanism for serializing two writers who would
otherwise collide on one path.

Fixture technique copied from tools/test_sbe_work.py rather than imported:
suites in this project stay standalone. A real git repository in a fresh
tempfile.mkdtemp() (git init, one seed commit), real `sbe work start`
worktrees inside that same tempdir, real subprocess calls to `bin/sbe`,
nothing mocked.
"""
import ast
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


def read(path):
    with io.open(path, encoding="utf-8") as fh:
        return fh.read()


CHANGE_DIR = "design/widget-lookup"

BACKEND_PATH = "src/widgets/lookup.py"
DOCS_PATH = "docs/widget-lookup.md"

# Real, deterministic, runnable evidence commands: no shell, no network, exit
# 0, stable stdout. Kept as plain argv lists at the call site and as their
# shell-quoted string form here, exactly the two spellings
# tools/test_sbe_work.py's TestQuotedVerifyCommandReceipt proves bind to the
# same command through shlex canonicalization.
BACKEND_VERIFY = "python3 -c \"print('BACKEND-OK')\""
BACKEND_VERIFY_ARGV = ["python3", "-c", "print('BACKEND-OK')"]
TEST_VERIFY = "python3 -c \"print('TEST-OK')\""

NOTES_BODY = (
    "# 01. Working notes\n\n"
    "## Backend scope\n"
    "The backend change adds a lookup helper to `src/widgets/lookup.py` that "
    "resolves a widget by id from the in-memory catalog.\n\n"
    "## Docs scope\n"
    "The documentation task explains the lookup helper's contract in "
    "`docs/widget-lookup.md` for downstream consumers.\n\n"
    "## Test scope\n"
    "The test task runs the backend change's own check once the backend task "
    "closes clean, and depends on it directly.\n\n"
    "## Overlap scope\n"
    "A second, later change also needs to touch `src/widgets/lookup.py`; it is "
    "queued behind the backend task on the same path, the one writer per path "
    "rule this project enforces.\n"
)


class TeamWorkflowFixture(unittest.TestCase):
    """A fresh repository, a fresh dossier and a hand-written, validated
    08-plan.json, all inside one tempdir per test."""

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
        self.worktree_dir = os.path.join(self.tmp, "worktrees")
        os.makedirs(self.worktree_dir)
        self.dossier = os.path.join(self.repo, CHANGE_DIR)
        self.change_id = os.path.basename(self.dossier)
        self.plan_path = self._write_dossier_and_plan()

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    # -- process helpers. Three values, not two: the same shape every sbe()
    # helper in this project's other test files uses (see
    # tools/test_sbe_work.py's own docstring on why).

    def sbe(self, *argv):
        out = subprocess.run([sys.executable, SBE] + list(argv), capture_output=True, text=True)
        return out.returncode, out.stdout + out.stderr, out.stderr

    def work(self, *argv):
        return self.sbe("work", *argv, "--cwd", self.repo)

    def brief(self, *argv):
        return self.sbe("work", "brief", "--cwd", self.repo, *argv)

    def status_team(self):
        return self.sbe("status", self.repo, "--team", "--json")

    # -- dossier and plan ----------------------------------------------------

    def _write_dossier_and_plan(self):
        answers = {"changes_contract": "n", "crosses_boundary": "n",
                   "reversible_under_hour": "y", "touches_sensitive": "n",
                   "consumers": "none"}
        intake = {"answers": answers, "binding": {"head": self.base}}
        write(self.repo, os.path.join(CHANGE_DIR, "00-intake.json"), json.dumps(intake))
        write(self.repo, os.path.join(CHANGE_DIR, "01-notes.md"), NOTES_BODY)
        plan = {
            "schemaVersion": "1.0",
            "baseCommit": self.base,
            "tasks": [
                {"id": "T01", "title": "Add a backend widget lookup helper",
                 "role": "writer", "dependsOn": [], "owns": [BACKEND_PATH],
                 "readOnly": [],
                 "acceptance": ["The lookup helper returns the widget for a known id"],
                 "verificationCommands": [BACKEND_VERIFY],
                 "requiredEvidence": ["command-receipt"],
                 "requiredReviewers": ["backend"],
                 "dossierSources": ["01-notes.md#backend-scope"]},
                {"id": "T02", "title": "Document the widget lookup helper",
                 "role": "writer", "dependsOn": [], "owns": [DOCS_PATH],
                 "readOnly": [],
                 "acceptance": ["The docs describe the lookup helper's contract"],
                 "verificationCommands": [],
                 "requiredEvidence": [],
                 "requiredReviewers": ["evidence"],
                 "dossierSources": ["01-notes.md#docs-scope"]},
                {"id": "T03", "title": "Test the widget lookup helper",
                 "role": "reviewer", "dependsOn": ["T01"], "owns": [],
                 "readOnly": [BACKEND_PATH],
                 "acceptance": ["The lookup helper is covered by a passing test"],
                 "verificationCommands": [TEST_VERIFY],
                 "requiredEvidence": ["command-receipt"],
                 "requiredReviewers": ["evidence"],
                 "dossierSources": ["01-notes.md#test-scope"]},
                {"id": "T04",
                 "title": "A second change queued behind the backend task on the same path",
                 "role": "writer", "dependsOn": ["T01"], "owns": [BACKEND_PATH],
                 "readOnly": [],
                 "acceptance": ["The second change lands only after the first closes clean"],
                 "verificationCommands": [],
                 "requiredEvidence": [],
                 "requiredReviewers": ["backend"],
                 "dossierSources": ["01-notes.md#overlap-scope"]},
            ],
        }
        plan_path = os.path.join(self.dossier, "08-plan.json")
        write(self.repo, os.path.join(CHANGE_DIR, "08-plan.json"), json.dumps(plan))
        # The plan must pass `sbe plan`'s own validation before any scenario
        # below trusts it: a plan the validator would refuse is a fixture
        # bug, never an LT-103 finding, exactly the reasoning
        # tools/test_sbe_work.py's `_plan()` applies to its derived plan.
        code, out, _err = self.sbe("plan", self.dossier, "--cwd", self.repo)
        self.assertEqual(code, 0, "fixture setup wrote a plan that `sbe plan` itself "
                                  "refuses; this is a fixture bug, not an LT-103 finding: %s"
                                  % out)
        return plan_path

    # -- registry: read directly, the single source of task state -----------

    def _registry_path(self):
        return os.path.join(self.repo, ".sbe", "tasks.json")

    def _registry(self):
        with io.open(self._registry_path(), encoding="utf-8") as fh:
            return json.load(fh)

    def _record(self, task_id):
        for t in self._registry()["tasks"]:
            if t.get("id") == task_id:
                return t
        return None

    def worktree_path_for(self, task_id):
        """The path `sbe work start` is documented to create the worktree at:
        <worktree-dir>/<repo-name>-sbe-<task-id>, canonicalized the same way
        tools/test_sbe_work.py's helper of the same name canonicalizes it,
        because `src/brothersbe/work.py`'s `_candidate_worktree` calls
        `os.path.realpath` before the path is ever stored or printed.

        This suite was left behind when its two sibling lease suites were
        aligned: on macOS the fixture's tempdir sits under `/var`, which is a
        symlink to `/private/var`, so the raw join and production's stored
        value are two different strings naming one directory, and the
        assertion failed on macOS while passing on Linux."""
        raw = os.path.join(self.worktree_dir,
                           "%s-sbe-%s" % (os.path.basename(self.repo), task_id))
        return os.path.realpath(raw)

    def branch_exists(self, name):
        out = subprocess.run(["git", "rev-parse", "--verify", "--quiet",
                              "refs/heads/" + name],
                             cwd=self.repo, capture_output=True, text=True)
        return out.returncode == 0

    # -- shared multi-step sequences, each a plain wrapper around the real
    # commands under test, never a shortcut around them ---------------------

    def _start(self, task_id, agent):
        code, text, _ = self.work("start", task_id, "--plan", self.plan_path,
                                  "--worktree-dir", self.worktree_dir, "--agent", agent)
        self.assertEqual(code, 0, "sbe work start %s failed: %s" % (task_id, text))
        return self.worktree_path_for(task_id)

    def _start_backend_with_change(self, agent="alpha"):
        """Start T01, make the real in-scope backend edit, commit it. Returns
        the worktree path. Deliberately stops short of evidence and finish:
        each caller decides what happens next."""
        worktree = self._start("T01", agent)
        write(worktree, BACKEND_PATH,
             "def lookup(widget_id, catalog):\n"
             "    return catalog.get(widget_id)\n")
        git(worktree, "add", "-A")
        git(worktree, "commit", "-qm", "backend: add the lookup helper")
        return worktree

    def _run_backend_evidence(self):
        """A real receipt for T01's own verification command, run at the
        task's worktree and written into the REPOSITORY's evidence store
        (never the worktree): `sbe work finish` reads `_evidence_dir(root)`,
        the root repository's .sbe/evidence, exactly as
        tools/test_sbe_work.py's TestFinishIntegration does."""
        worktree = self.worktree_path_for("T01")
        receipt_path = os.path.join(self.repo, ".sbe", "evidence", "T01-receipt.json")
        code, text, _ = self.sbe("evidence", "run", "--out", receipt_path, "--kind", "gate",
                                 "--cwd", worktree, "--", *BACKEND_VERIFY_ARGV)
        self.assertEqual(code, 0, "sbe evidence run for T01 failed: %s" % text)
        return receipt_path


class TestOnlyDisjointReadyTasksStartTogether(TeamWorkflowFixture):
    def test_backend_and_docs_start_together_the_planted_overlap_is_refused_while_backend_is_open(self):
        self._start("T01", "alpha")
        self._start("T02", "beta")

        code, text, _ = self.work("start", "T04", "--plan", self.plan_path,
                                  "--worktree-dir", self.worktree_dir, "--agent", "gamma")
        self.assertNotEqual(code, 0, "starting the planted overlap task while its "
                            "one-writer-per-path dependency T01 is still open must be "
                            "refused, and it exited 0: %s" % text)
        self.assertIn("T01", text, "the refusal must name the open dependency the "
                     "overlap task is queued behind: %s" % text)
        self.assertIn("still open", text.lower(),
                     "the refusal must say the dependency is still open: %s" % text)
        self.assertFalse(self.branch_exists("sbe/%s/T04" % self.change_id),
                         "a refused start must never leave a branch behind")
        self.assertFalse(os.path.isdir(self.worktree_path_for("T04")),
                         "a refused start must never leave a worktree behind")

        # Disjoint scope, undisturbed by the refused sibling attempt.
        record_backend = self._record("T01")
        record_docs = self._record("T02")
        self.assertEqual(record_backend["status"], "open", record_backend)
        self.assertEqual(record_docs["status"], "open", record_docs)


class TestEachStartableTaskGetsADeterministicBrief(TeamWorkflowFixture):
    def test_brief_is_byte_identical_across_runs_and_differs_in_scope_across_tasks(self):
        code1, text1, _ = self.brief("--plan", self.plan_path, "--task", "T01", "--json")
        code2, text2, _ = self.brief("--plan", self.plan_path, "--task", "T01", "--json")
        self.assertEqual(code1, 0, text1)
        self.assertEqual(code2, 0, text2)
        self.assertEqual(text1, text2, "the same repository state must brief the same "
                        "task byte-identically twice in a row")

        code3, text3, _ = self.brief("--plan", self.plan_path, "--task", "T02", "--json")
        self.assertEqual(code3, 0, text3)

        backend_brief = json.loads(text1)
        docs_brief = json.loads(text3)
        self.assertEqual(backend_brief["scope"], [BACKEND_PATH], backend_brief)
        self.assertEqual(docs_brief["scope"], [DOCS_PATH], docs_brief)
        self.assertNotEqual(backend_brief["scope"], docs_brief["scope"],
                            "two disjoint tasks must not brief the same scope")
        self.assertEqual(backend_brief["taskId"], "T01")
        self.assertEqual(docs_brief["taskId"], "T02")


class TestATaskWithoutEvidenceCannotClose(TeamWorkflowFixture):
    def test_finish_on_the_backend_task_before_its_receipt_exists_is_refused_naming_the_missing_receipt(self):
        self._start_backend_with_change()
        code, text, _ = self.work("finish", "T01")
        self.assertNotEqual(code, 0, "finish must refuse a task with no bound receipt, and "
                            "it exited 0: %s" % text)
        self.assertIn("NO-DATA", text, "an absent receipt is NO-DATA, never a silent pass "
                     "and never a FAIL-by-guess: %s" % text)
        self.assertIn(BACKEND_VERIFY, text, "the refusal must name the verification "
                     "command it found no receipt for: %s" % text)
        record = self._record("T01")
        self.assertEqual(record["status"], "open", "a refused finish must leave the task "
                        "open: %s" % record)


class TestEvidenceUnblocksFinishAndTheDependentTestTask(TeamWorkflowFixture):
    def test_after_the_evidence_command_runs_finish_accepts_and_the_test_task_becomes_startable(self):
        self._start_backend_with_change()
        self._run_backend_evidence()

        code, text, _ = self.work("finish", "T01")
        self.assertEqual(code, 0, "finish must accept once a matching receipt is bound to "
                        "the current commit: %s" % text)
        record = self._record("T01")
        self.assertEqual(record["status"], "closed", record)
        self.assertNotIn("forced", record, "a clean finish must never look forced: %s"
                        % record)

        code, text, _ = self.work("start", "T03", "--plan", self.plan_path,
                                  "--worktree-dir", self.worktree_dir, "--agent", "delta")
        self.assertEqual(code, 0, "T01 closed clean, so its dependent T03 must now be "
                        "startable: %s" % text)


class TestABlockedWorkerLeavesRecoverableState(TeamWorkflowFixture):
    def test_a_task_left_open_with_uncommitted_edits_still_checks_as_open_with_its_worktree_and_edits_intact(self):
        worktree = self._start("T02", "beta")
        content = "# Widget lookup\n\nUncommitted draft, the worker went away mid-task.\n"
        doc_path = write(worktree, DOCS_PATH, content)

        code, text, _ = self.work("check", "T02")
        self.assertIn("status open", text, "check must report the task as open: %s" % text)
        self.assertTrue(os.path.isdir(worktree), "the worktree must still exist after check")
        self.assertEqual(read(doc_path), content, "check must never touch the uncommitted "
                        "edit it is reporting on")

        record = self._record("T02")
        self.assertEqual(record["status"], "open", record)
        self.assertEqual(record["worktree"], worktree, record)


class TestAHumanTakeoverPreservesUncommittedChangesAndSingleOwnership(TeamWorkflowFixture):
    def test_repeated_status_and_check_calls_never_mutate_the_dirty_worktree_or_reassign_the_owner(self):
        worktree = self._start("T02", "beta")
        content = "# Widget lookup\n\nA human is taking this over from here.\n"
        doc_path = write(worktree, DOCS_PATH, content)
        registry_path = self._registry_path()
        registry_before = read(registry_path)

        for _ in range(3):
            code, text, _ = self.work("check", "T02")
            self.assertIn(code, (0, 1), "sbe work check must exit its documented "
                         "closable/not-closable code, not crash: %s" % text)
            self.assertEqual(read(doc_path), content, "a dirty worktree must survive "
                            "every inspection untouched")
            self.assertEqual(read(registry_path), registry_before, "check must never "
                            "mutate the registry file")

            code, text, _ = self.status_team()
            self.assertIn(code, (0, 1), "sbe status --team must exit its documented "
                         "control-pass/control-fail code, not crash: %s" % text)
            self.assertEqual(read(doc_path), content, "sbe status --team must never "
                            "touch a task's uncommitted work either")
            self.assertEqual(read(registry_path), registry_before)

        record = self._record("T02")
        self.assertEqual(record["agent"], "beta", "the registry must still name exactly "
                        "one owner after every inspection: %s" % record)
        self.assertEqual(record["status"], "open", record)


#: The scanner below only inspects list/tuple literals that sit inside an
#: actual `subprocess.run(...)` or `git(...)` call, never a bare literal
#: sitting on its own, so naming the forbidden words plainly here does not
#: trip the scan it seeds.
_FORBIDDEN_GIT_SUBCOMMANDS = ("merge", "rebase", "push", "deploy")


def _find_forbidden_git_argv(source):
    """Every git call SITE in this file that could construct a forbidden
    subcommand, not a blind scan of every list/tuple literal in the module.

    A blind literal-head scan (the shape tools/test_sbe_work.py's
    TestNoMergeLaw uses against src/brothersbe/work.py, which never needs to
    name its own forbidden set inside the scanned file) is not reusable
    here unmodified: this file both RUNS the scan and DECLARES the forbidden
    set the scan looks for, and a plain tuple literal naming that set would
    then be a false positive against itself. Scoping the scan to the two
    shapes this file's own helpers actually use for a subprocess call --
    `subprocess.run([...])` and this file's own `git(cwd, *args)` -- is both
    narrower and more accurate: it only ever looks at a real argv a call
    site is about to run, which is exactly what "constructs a git argv"
    means, and it is a NAME the git()/subprocess.run() calls in this file
    do not fall into the pattern that produced the false positive above.
    """
    tree = ast.parse(source)
    hits = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        is_subprocess_run = (isinstance(func, ast.Attribute) and func.attr == "run"
                             and isinstance(func.value, ast.Name)
                             and func.value.id == "subprocess")
        is_git_helper = isinstance(func, ast.Name) and func.id == "git"
        if is_subprocess_run and node.args:
            first = node.args[0]
            if (isinstance(first, (ast.List, ast.Tuple)) and first.elts
                    and isinstance(first.elts[0], ast.Constant)
                    and isinstance(first.elts[0].value, str)):
                word = first.elts[0].value.lower()
                if word in _FORBIDDEN_GIT_SUBCOMMANDS:
                    hits.append("%s (line %d)" % (word, node.lineno))
        elif is_git_helper and len(node.args) >= 2:
            sub = node.args[1]
            if isinstance(sub, ast.Constant) and isinstance(sub.value, str):
                word = sub.value.lower()
                if word in _FORBIDDEN_GIT_SUBCOMMANDS:
                    hits.append("%s (line %d)" % (word, node.lineno))
    return hits


class TestNoMergeOrPushExistsInTheImplementationPath(TeamWorkflowFixture):
    def test_this_file_never_constructs_a_merge_argv_and_the_scratch_repo_reflog_shows_no_merge(self):
        # Source-level: this fixture file itself never builds a merge/rebase/
        # push/deploy git argv, mirroring tools/test_sbe_work.py:717's
        # TestNoMergeLaw, applied to this file rather than to work.py.
        path = os.path.abspath(__file__)
        with io.open(path, encoding="utf-8") as fh:
            source = fh.read()
        hits = _find_forbidden_git_argv(source)
        self.assertEqual(hits, [], "this fixture file itself constructs git argv "
                        "starting with a forbidden subcommand: %s" % ", ".join(hits))

        # Behavioral: a real start/edit/evidence/finish/start sequence run
        # through the scratch repo, then the repository's own history is
        # inspected for a merge that should never have happened.
        self._start_backend_with_change()
        self._run_backend_evidence()
        code, text, _ = self.work("finish", "T01")
        self.assertEqual(code, 0, text)
        self._start("T02", "beta")
        self._start("T03", "delta")

        merges = git(self.repo, "log", "--all", "--merges", "--oneline")
        self.assertEqual(merges, "", "no merge commit may exist anywhere in this "
                        "repository's history: %r" % merges)

        reflog = git(self.repo, "reflog", "--all")
        merge_lines = [line for line in reflog.splitlines() if "merge" in line.lower()]
        self.assertEqual(merge_lines, [], "the reflog must show no merge action beyond "
                        "the plain branch/worktree/commit setup this fixture performs: %s"
                        % merge_lines)


class TestScannerCalibration(unittest.TestCase):
    """Per the audit style tools/test_sbe_work.py's TestNoMergeLaw itself
    uses: a passing scan is not proof the scanner bites unless it can be
    shown going red on a mutated copy. Both call shapes
    `_find_forbidden_git_argv` recognizes are mutated here, on a SCRATCH
    COPY in a tempdir, never on this file itself."""

    def test_calibration_a_subprocess_run_list_literal_starting_with_merge_is_caught(self):
        mutated = ("import subprocess\n"
                  "def scratch(repo, branch):\n"
                  "    subprocess.run(['merge', branch], cwd=repo)\n")
        hits = _find_forbidden_git_argv(mutated)
        self.assertTrue(hits, "calibration failed: a subprocess.run(['merge', ...]) call "
                        "must be caught, and it was not")

    def test_calibration_a_git_helper_call_with_merge_as_the_subcommand_is_caught(self):
        mutated = ("def scratch(repo, branch):\n"
                  "    git(repo, 'merge', branch)\n")
        hits = _find_forbidden_git_argv(mutated)
        self.assertTrue(hits, "calibration failed: a git(cwd, 'merge', ...) call must be "
                        "caught, and it was not")

    def test_calibration_the_forbidden_set_tuple_itself_is_not_a_false_positive(self):
        """The exact shape that motivated scoping the scan to call sites in
        the first place (see _find_forbidden_git_argv's docstring): a bare
        module-level tuple naming the forbidden words is not, itself, a git
        call, and must not be flagged."""
        mutated = "_WORDS = ('merge', 'rebase', 'push', 'deploy')\n"
        hits = _find_forbidden_git_argv(mutated)
        self.assertEqual(hits, [], "calibration failed: a bare tuple literal naming the "
                        "forbidden words, never passed to a git call, must not be flagged: "
                        "%s" % hits)


class TestSessionRestartRecoveryFromDisk(TeamWorkflowFixture):
    def test_a_fresh_sbe_status_team_json_subprocess_reports_open_tasks_owners_and_a_next_action_purely_from_disk(self):
        self._start("T01", "alpha")
        self._start("T02", "beta")

        # A brand new subprocess, no state shared with the calls above beyond
        # what is now sitting on disk (the registry, the plan, the worktrees).
        code, text, _ = self.status_team()
        self.assertIn(code, (0, 1), "sbe status --team --json must exit its documented "
                     "control-pass/control-fail code, not crash: %s" % text)
        data = json.loads(text)
        self.assertIn(self.change_id, data["changes"], "the fresh process must discover "
                     "this dossier from disk: %s" % data["changes"])

        open_findings = {f["evidence"]: f for f in data["findings"]
                         if f["severity"] == 7 and f["change"] == self.change_id}
        self.assertIn("task T01", open_findings, "a fresh process must report T01 open "
                     "purely from what is on disk: %s" % data["findings"])
        self.assertIn("task T02", open_findings, "a fresh process must report T02 open "
                     "purely from what is on disk: %s" % data["findings"])
        self.assertEqual(open_findings["task T01"]["owner"], "alpha",
                         open_findings["task T01"])
        self.assertEqual(open_findings["task T02"]["owner"], "beta",
                         open_findings["task T02"])

        next_actions = [f for f in data["findings"]
                       if f["severity"] == 10 and f["change"] == self.change_id]
        self.assertEqual(len(next_actions), 1, "exactly one next action must be derived "
                        "for this change: %s" % next_actions)
        self.assertTrue(next_actions[0]["nextAction"], "the next action must be a "
                       "nonempty statement, not silence: %s" % next_actions[0])


if __name__ == "__main__":
    unittest.main(verbosity=2)

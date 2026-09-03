#!/usr/bin/env python3
"""LT-501: a deterministic builder for the one end-to-end team scenario.

Not frozen bytes. This module builds the golden scenario repository FRESH,
every run, in a directory the caller owns (normally a fresh
`tempfile.mkdtemp()`), the same discipline every other fixture in this
project already follows (`tools/test_sbe_work.py`, `tools/test_sbe_team_
workflow.py`): a real `git init`, real dossier files, a hand-authored
`08-plan.json` validated through the real `sbe plan` before anything trusts
it. Nothing here is a snapshot of another run; the same call produces the
same bytes because every value is a pure function of its arguments, and
nothing here calls `sbe work start`, `sbe evidence run` or any other
mutating command itself -- that sequencing is `tools/test_sbe_golden_
scenario.py`'s job, driving the real engine, one step at a time, so every
assertion can bind to the exact command that produced the state it reads.

Spec of record: `~/Downloads/BrotherSBE_Lean_Team_Execution_Plan_for_
Fable_2026-08-04.md` section 15, "LT-501: One end-to-end team scenario".

The scenario this module lays out:

  T01  backend service change (writer), owns `src/widgets/service.py`,
       ready (no dependency).
  T02  small SQL change (writer), owns `migrations/0002_widget_index.sql`,
       ready (no dependency), disjoint from every other task's owns.
  T03  the dependent task (reviewer, owns nothing, `readOnly` on T01's
       file), `dependsOn: ["T01"]`.
  T04  the planted security-sensitive configuration edit (writer), owns
       `.claude/settings.local.json`, an authority surface per
       `tools/sbe_instruction_surface.py`'s own nine detected families
       (`.claude/**`). Ready, disjoint from the other three.

Three writers (T01, T02, T04), one reviewer (T03): the "maximum three
writers" pass criterion is a property of THIS PLAN, not something the test
suite enforces after the fact -- the registry can never show a fourth writer
because the plan never declares one, and `tools/test_sbe_golden_scenario.py`
still re-checks the registry mechanically rather than trusting this
docstring's arithmetic.
"""
import io
import json
import os
import subprocess


# ---------------------------------------------------------------------------
# Small process and file helpers, copied from tools/test_sbe_work.py and
# tools/test_sbe_team_workflow.py rather than imported: suites (and their
# builders) in this project stay standalone.
# ---------------------------------------------------------------------------

def git(cwd, *args):
    # Identity pinned in the ENVIRONMENT, not only in each repo's local
    # config: the engine provisions task worktrees whose commits must never
    # depend on the machine's global gitconfig. A laptop with a real
    # identity masked this once; a CI runner with none failed with
    # "Committer identity unknown" (PR 17, both ubuntu legs, first contact).
    env = dict(os.environ)
    env.setdefault("GIT_AUTHOR_NAME", "Golden Scenario Fixture")
    env.setdefault("GIT_AUTHOR_EMAIL", "fixture@example.invalid")
    env.setdefault("GIT_COMMITTER_NAME", "Golden Scenario Fixture")
    env.setdefault("GIT_COMMITTER_EMAIL", "fixture@example.invalid")
    out = subprocess.run(["git"] + list(args), cwd=cwd, capture_output=True, text=True,
                         env=env)
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


def run_sbe(sbe_path, argv, cwd=None, python_exe=None):
    """Run `bin/sbe <argv...>` as a real subprocess.

    Three values, not two: `evals/test_no_data_class.py` reads every 2-tuple
    return anywhere under `tools/` as a possible (verdict, evidence) pair
    unless it can prove the head is never "PASS", and a bare process-result
    pair is exactly the shape that lint exists to catch when it is NOT one of
    those pairs and was never registered as a check. Every `sbe()`-shaped
    helper in this project's other fixtures already returns three values
    (code, combined stdout+stderr, stderr alone) for this reason;
    mirrored here rather than reinvented.
    """
    import sys
    exe = python_exe or sys.executable
    out = subprocess.run([exe, sbe_path] + list(argv), cwd=cwd, capture_output=True, text=True)
    return out.returncode, out.stdout + out.stderr, out.stderr


# ---------------------------------------------------------------------------
# Scenario constants
# ---------------------------------------------------------------------------

CHANGE_DIR = "design/golden-scenario"
CHANGE_ID = "golden-scenario"

BACKEND_PATH = "src/widgets/service.py"
SQL_PATH = "migrations/0002_widget_index.sql"
AUTHORITY_PATH = ".claude/team-config.json"
# Deliberately NOT `.claude/settings.local.json`: this machine's own global
# git excludes file (`~/.config/git/ignore`, a common Claude Code convention,
# confirmed present on this host) ignores that exact name everywhere, which
# would make the planted edit invisible to `git add -A` in the scratch
# scenario repo too and defeat the fixture before `sbe_instruction_surface.py`
# ever saw it. `.claude/team-config.json` still matches the `.claude/**`
# authority family `_matched_surface` detects; it is just not the one name
# this host's own gitignore happens to hide.

BACKEND_VERIFY = "python3 -c \"print('BACKEND-OK')\""
BACKEND_VERIFY_ARGV = ["python3", "-c", "print('BACKEND-OK')"]
SQL_VERIFY = "python3 -c \"print('SQL-OK')\""
SQL_VERIFY_ARGV = ["python3", "-c", "print('SQL-OK')"]
TEST_VERIFY = "python3 -c \"print('TEST-OK')\""
TEST_VERIFY_ARGV = ["python3", "-c", "print('TEST-OK')"]
CONFIG_VERIFY = "python3 -c \"print('CONFIG-OK')\""
CONFIG_VERIFY_ARGV = ["python3", "-c", "print('CONFIG-OK')"]

WRITER_TASK_IDS = ("T01", "T02", "T04")
ALL_TASK_IDS = ("T01", "T02", "T03", "T04")

NOTES_BODY = (
    "# 01. Working notes\n\n"
    "## Backend scope\n"
    "The backend change adds a `%s` service that resolves a widget by id "
    "from the in-memory catalog.\n\n"
    "## Sql scope\n"
    "The SQL change adds `%s`, an index migration on the widgets table.\n\n"
    "## Dependent scope\n"
    "The dependent task runs a check against the backend change once T01 "
    "closes clean, and depends on it directly.\n\n"
    "## Config scope\n"
    "A local settings override at `%s` records the environment this "
    "change was developed against.\n"
) % (BACKEND_PATH, SQL_PATH, AUTHORITY_PATH)

ADR_BODY = (
    "# 03. Architecture decision record\n\n"
    "## Context\n"
    "Widget lookups need a dedicated service and a faster index.\n\n"
    "## Decision\n"
    "Serve widget lookups from `%s`, backed by the index in `%s`.\n\n"
    "## Consequences\n"
    "One more service to operate, one more index to maintain.\n\n"
    "## What would flip this\n"
    "If lookup volume never justifies the index.\n"
) % (BACKEND_PATH, SQL_PATH)

DATA_MODEL_BODY = (
    "# 05. Data model\n\n"
    "## Entities\n"
    "- Widget: a catalog item looked up by id\n"
)

VERIFICATION_BODY = (
    "# 07. Verification plan\n\n"
    "| Claim this design makes | The check that proves it | When it runs |\n"
    "|---|---|---|\n"
    "| The service returns a widget by id | `%s` | Continuous |\n"
    "| The index migration runs cleanly | `%s` | Continuous |\n"
) % (BACKEND_VERIFY, SQL_VERIFY)


def _intake_answers():
    return {"changes_contract": "n", "crosses_boundary": "n",
            "reversible_under_hour": "y", "touches_sensitive": "n",
            "consumers": "none"}


# ---------------------------------------------------------------------------
# Build steps
# ---------------------------------------------------------------------------

def init_repo(repo_dir, user_name="Golden Scenario Fixture",
              user_email="fixture@example.invalid"):
    """git init, one seed commit. Returns the seed commit sha."""
    os.makedirs(repo_dir, exist_ok=True)
    git(repo_dir, "init", "-q")
    git(repo_dir, "config", "user.email", user_email)
    git(repo_dir, "config", "user.name", user_name)
    write(repo_dir, "README.md", "golden scenario base\n")
    git(repo_dir, "add", "-A")
    git(repo_dir, "commit", "-qm", "base")
    return git(repo_dir, "rev-parse", "HEAD")


def write_dossier(repo_dir, change_dir=CHANGE_DIR):
    """Writes 00-intake.json, 01-notes.md, 03-adr.md, 05-data-model.md and
    07-verification.md. Returns the dossier directory (absolute path)."""
    intake = {"answers": _intake_answers()}
    write(repo_dir, os.path.join(change_dir, "00-intake.json"), json.dumps(intake))
    write(repo_dir, os.path.join(change_dir, "01-notes.md"), NOTES_BODY)
    write(repo_dir, os.path.join(change_dir, "03-adr.md"), ADR_BODY)
    write(repo_dir, os.path.join(change_dir, "05-data-model.md"), DATA_MODEL_BODY)
    write(repo_dir, os.path.join(change_dir, "07-verification.md"), VERIFICATION_BODY)
    return os.path.join(repo_dir, change_dir)


def plan_dict(base_sha):
    """The hand-authored 08-plan.json body. Every field this project's own
    plan schema documents (id, title, role, dependsOn, owns, readOnly,
    acceptance, verificationCommands, requiredEvidence, requiredReviewers,
    dossierSources), mirroring the shape `tools/test_sbe_team_workflow.py`
    already hand-writes and validates through the real `sbe plan`."""
    return {
        "schemaVersion": "1.0",
        "baseCommit": base_sha,
        "tasks": [
            {"id": "T01", "title": "Add the widget lookup service",
             "role": "writer", "dependsOn": [], "owns": [BACKEND_PATH],
             "readOnly": [],
             "acceptance": ["The service returns a widget by id"],
             "verificationCommands": [BACKEND_VERIFY],
             "requiredEvidence": ["command-receipt"],
             "requiredReviewers": ["backend"],
             "dossierSources": ["01-notes.md#backend-scope"]},
            {"id": "T02", "title": "Add the widget index migration",
             "role": "writer", "dependsOn": [], "owns": [SQL_PATH],
             "readOnly": [],
             "acceptance": ["The index migration runs cleanly"],
             "verificationCommands": [SQL_VERIFY],
             "requiredEvidence": ["command-receipt"],
             "requiredReviewers": ["migration"],
             "dossierSources": ["01-notes.md#sql-scope"]},
            {"id": "T03", "title": "Check the widget lookup service",
             "role": "reviewer", "dependsOn": ["T01"], "owns": [],
             "readOnly": [BACKEND_PATH],
             "acceptance": ["The backend change is covered by a passing check"],
             "verificationCommands": [TEST_VERIFY],
             "requiredEvidence": ["command-receipt"],
             "requiredReviewers": ["evidence"],
             "dossierSources": ["01-notes.md#dependent-scope"]},
            {"id": "T04", "title": "Record the local settings override",
             "role": "writer", "dependsOn": [], "owns": [AUTHORITY_PATH],
             "readOnly": [],
             "acceptance": ["The local settings override is captured for this "
                            "environment"],
             "verificationCommands": [CONFIG_VERIFY],
             "requiredEvidence": ["command-receipt"],
             "requiredReviewers": ["security"],
             "dossierSources": ["01-notes.md#config-scope"]},
        ],
    }


def write_plan(repo_dir, dossier_dir, base_sha, change_dir=CHANGE_DIR):
    """Writes 08-plan.json. Returns the plan path (absolute)."""
    plan_path = write(repo_dir, os.path.join(change_dir, "08-plan.json"),
                      json.dumps(plan_dict(base_sha)))
    return plan_path


def build(repo_dir, sbe_path, python_exe=None, change_dir=CHANGE_DIR):
    """The full dossier-and-plan build, validated through the real `sbe
    plan`. Returns a dict (never a 2-tuple: see `run_sbe`'s own docstring)
    with keys: base, dossier, plan_path, change_id, validate_code,
    validate_text.
    """
    base = init_repo(repo_dir)
    dossier = write_dossier(repo_dir, change_dir)
    plan_path = write_plan(repo_dir, dossier, base, change_dir)
    git(repo_dir, "add", "-A")
    git(repo_dir, "commit", "-qm", "dossier: golden scenario")
    code, text, _err = run_sbe(sbe_path, ["plan", dossier, "--cwd", repo_dir],
                               python_exe=python_exe)
    return {"base": base, "dossier": dossier, "plan_path": plan_path,
            "change_id": os.path.basename(dossier), "validate_code": code,
            "validate_text": text}

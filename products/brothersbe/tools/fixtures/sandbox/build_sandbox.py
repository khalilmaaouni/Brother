#!/usr/bin/env python3
"""Plan 12.4, Lane C4: the ten minute sandbox, a disposable practice repository.

Not frozen bytes. Like `tools/fixtures/golden-scenario/build_scenario.py`
(the idiom this module mirrors), the sandbox is built FRESH, every run, in a
directory the caller owns (normally a fresh `tempfile.mkdtemp()`): a real
`git init`, a real README, a real one-task dossier and a hand-authored
`08-plan.json` validated through the real `sbe plan` before anything trusts
it. Nothing here is a snapshot of another run; the same call produces the
same bytes because every value is a pure function of its arguments.

Smaller than the golden scenario, and human-facing rather than test-facing:

  - ONE task, not four. The golden scenario proves "at most three writers
    plus a reviewer" as a property of a team plan; the sandbox exists to let
    a single beginner feel `start -> evidence -> finish -> review ->
    handover` without first understanding a team.
  - Friendly names throughout (`say-hello`, `src/hello/greeting.py`,
    `migrations/0001_greeting_log.sql`), not `T01`/`widgets`/`golden-
    scenario`: a reader unfamiliar with this project's own test fixtures
    should recognize every path as something a tiny real service would
    actually have.
  - A README this module writes into the repo's own seed commit, so a
    beginner who opens the built directory in a file browser -- never mind
    a terminal -- sees the whole ten-minute journey named in one file,
    step by step, before running a single command.
  - No `00-intake.json` and no `03-adr.md` written here: those two are the
    beginner's OWN first actions in the walkthrough (`sbe intake`, then
    `sbe decide` and a manual accept), not something this builder does for
    them. Everything this module writes is background the walkthrough
    references (the notes the plan cites, the plan itself), never the
    steps the walkthrough teaches.

This scenario stays intentionally at intake tier T0 (see
`tools/sbe_intake.py:compute_tier`): reversible, no contract change, no
sensitive data, no downstream consumers. T0 requires zero dossier artifacts,
which is the whole point for a ten-minute demo -- the beginner sees a real
tier decision without first being asked to write seven documents to earn it.

Nothing here calls `sbe work start`, `sbe evidence run`, `sbe work finish`,
`sbe review` or `sbe handover prepare` itself -- that sequencing belongs to
whatever drives the sandbox one step at a time (`docs/guides/00-sandbox.md`
for a human, `tools/test_sbe_sandbox.py` for this project's own doc-truth
check), so every assertion or every quoted line can bind to the exact
command that produced the state it reads.

Spec of record: plan 12.4, Lane C4, "the ten minute sandbox".
"""
import io
import os
import subprocess


# ---------------------------------------------------------------------------
# Small process and file helpers, copied from tools/fixtures/golden-scenario/
# build_scenario.py rather than imported: suites (and their builders) in this
# project stay standalone.
# ---------------------------------------------------------------------------

def git(cwd, *args):
    # Identity pinned in the ENVIRONMENT, not only in each repo's local
    # config, for the same reason build_scenario.py pins it: the sandbox
    # must build the same way on a laptop with a real git identity and on a
    # CI runner with none. The DATE is pinned too, the same discipline
    # evals/replay_guide05.py already uses for its own scratch commits: a
    # fixed author and committer date makes every commit hash this builder
    # produces the same on every machine, every run, which is what lets
    # docs/guides/00-sandbox.md quote a commit hash verbatim and
    # tools/test_sbe_sandbox.py hold the guide to it.
    env = dict(os.environ)
    # PINNED SO THE BASE COMMIT IS THE SAME BYTES ON EVERY MACHINE. The pinning
    # is what lets docs/guides/00-sandbox.md quote real commit hashes instead of
    # placeholders, and tools/test_sbe_sandbox.py checks the page against a live
    # run rather than trusting either.
    #
    # IF YOU CHANGE WHAT THIS BUILDER SEEDS, THE BASE HASH MOVES AND THAT PAGE
    # GOES STALE. The suite will fail and say so, which is the design. Refresh
    # the hashes quoted in docs/guides/00-sandbox.md; do not relax the suite to
    # get past it. This happened once and stayed unread for days because a
    # moved fixture hash looks exactly like documentation drift.
    env.setdefault("GIT_AUTHOR_NAME", "Sandbox Fixture")
    env.setdefault("GIT_AUTHOR_EMAIL", "sandbox@example.invalid")
    env.setdefault("GIT_COMMITTER_NAME", "Sandbox Fixture")
    env.setdefault("GIT_COMMITTER_EMAIL", "sandbox@example.invalid")
    env.setdefault("GIT_AUTHOR_DATE", "2026-08-01T00:00:00Z")
    env.setdefault("GIT_COMMITTER_DATE", "2026-08-01T00:00:00Z")
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

    Three values, not two: see `build_scenario.run_sbe`'s own docstring for
    why every `sbe()`-shaped helper in this project's fixtures returns three
    values (code, combined stdout+stderr, stderr alone) rather than a
    (verdict, evidence)-shaped pair `evals/test_no_data_class.py` would flag.
    """
    import sys
    exe = python_exe or sys.executable
    out = subprocess.run([exe, sbe_path] + list(argv), cwd=cwd, capture_output=True, text=True)
    return out.returncode, out.stdout + out.stderr, out.stderr


# ---------------------------------------------------------------------------
# Scenario constants
# ---------------------------------------------------------------------------

CHANGE_DIR = "design/say-hello"
CHANGE_ID = "say-hello"

SERVICE_PATH = "src/hello/greeting.py"
SQL_PATH = "migrations/0001_greeting_log.sql"

TASK_ID = "T01"
VERIFY = "python3 -c \"print('GREETING-OK')\""
VERIFY_ARGV = ["python3", "-c", "print('GREETING-OK')"]

README_BODY = (
    "# Say-hello: a disposable BrotherSBE practice repository\n\n"
    "This repository exists to be broken. Nothing in it is real, nothing you do here\n"
    "touches your own project, and you can delete the whole directory when you are\n"
    "done. It exists so you can feel the whole BrotherSBE journey once, end to end,\n"
    "before you run any of this against a repository that matters.\n\n"
    "The full walkthrough lives at `docs/guides/00-sandbox.md` in the BrotherSBE\n"
    "skill you cloned this from. Run every command from there against THIS\n"
    "directory. The eight steps it walks, in order:\n\n"
    "1. Install health: `sbe doctor` checks the tool itself before you trust it.\n"
    "2. Describe an outcome: `sbe intake` asks five short questions about the\n"
    "   change staged here and writes down what you answer.\n"
    "3. See the risk level: your five answers become one tier, T0 to T3, which\n"
    "   decides how much design documentation this change owes.\n"
    "4. Accept one decision: `sbe decide` scores an architecture question from a\n"
    "   table instead of an opinion, and you record the recommendation it gives.\n"
    "5. Start one task: `sbe work start` opens a dedicated branch and worktree for\n"
    "   the one task staged in this repository's plan.\n"
    "6. Run proof: `sbe evidence run` executes the task's real check and writes\n"
    "   the receipt that proves it ran, rather than just claiming it passed.\n"
    "7. Review: `sbe review` reads the scored surface and the hard gates, and\n"
    "   `--write` records the verdict.\n"
    "8. Reach the prepared handover: `sbe handover prepare` writes down who is\n"
    "   handing this off and to whom, ready for a human to accept.\n\n"
    "The change staged here: a tiny greeting service, `%s`, and a small SQL\n"
    "change beside it, `%s`. Both are named in the plan already (`%s/08-plan.json`)\n"
    "as ONE task, `%s`, ready to start. Neither file exists yet -- writing them is\n"
    "step 5 of the walkthrough, not something this repository did for you.\n"
) % (SERVICE_PATH, SQL_PATH, CHANGE_DIR, TASK_ID)

NOTES_BODY = (
    "# 01. What we're building\n\n"
    "## The change\n"
    "A tiny greeting service at `%s` that says hello back, plus a small SQL\n"
    "change at `%s` that logs each greeting. One task, one writer, disjoint from\n"
    "everything else in this practice repository.\n"
) % (SERVICE_PATH, SQL_PATH)


def plan_dict(base_sha):
    """The hand-authored 08-plan.json body: one task, every field this
    project's plan schema documents, mirroring the shape
    `tools/fixtures/golden-scenario/build_scenario.py` already hand-writes
    and validates through the real `sbe plan`."""
    return {
        "schemaVersion": "1.0",
        "baseCommit": base_sha,
        "tasks": [
            {"id": TASK_ID, "title": "Add the greeting service and its log table",
             "role": "writer", "dependsOn": [], "owns": [SERVICE_PATH, SQL_PATH],
             "readOnly": [],
             "acceptance": ["A visitor gets a friendly greeting back"],
             "verificationCommands": [VERIFY],
             "requiredEvidence": ["command-receipt"],
             "requiredReviewers": ["evidence"],
             "dossierSources": ["01-notes.md#the-change"]},
        ],
    }


# ---------------------------------------------------------------------------
# Build steps
# ---------------------------------------------------------------------------

def init_repo(repo_dir, user_name="Sandbox Fixture", user_email="sandbox@example.invalid"):
    """git init, one seed commit carrying the step-naming README. Returns the
    seed commit sha."""
    os.makedirs(repo_dir, exist_ok=True)
    git(repo_dir, "init", "-q")
    git(repo_dir, "config", "user.email", user_email)
    git(repo_dir, "config", "user.name", user_name)
    write(repo_dir, "README.md", README_BODY)
    git(repo_dir, "add", "-A")
    git(repo_dir, "commit", "-qm", "base: a disposable practice repository")
    return git(repo_dir, "rev-parse", "HEAD")


def write_dossier(repo_dir, change_dir=CHANGE_DIR):
    """Writes 01-notes.md, the one file the plan cites. Deliberately does NOT
    write 00-intake.json or 03-adr.md: those are the walkthrough's own first
    two live actions, not something built ahead of the reader. Returns the
    dossier directory (absolute path)."""
    write(repo_dir, os.path.join(change_dir, "01-notes.md"), NOTES_BODY)
    return os.path.join(repo_dir, change_dir)


def write_plan(repo_dir, base_sha, change_dir=CHANGE_DIR):
    """Writes 08-plan.json. Returns the plan path (absolute)."""
    import json
    plan_path = write(repo_dir, os.path.join(change_dir, "08-plan.json"),
                      json.dumps(plan_dict(base_sha)))
    return plan_path


def build(repo_dir, sbe_path, python_exe=None, change_dir=CHANGE_DIR):
    """The full sandbox build, validated through the real `sbe plan`. Returns
    a dict (never a 2-tuple: see `run_sbe`'s own docstring) with keys: base,
    dossier, plan_path, change_id, validate_code, validate_text.
    """
    base = init_repo(repo_dir)
    # The real first-start step, not a hand-built imitation: `sbe init --apply`
    # writes the project footprint (.brothersbe/config.json among it), so the
    # sandbox's doctor reads project-init PASS the same way a properly started
    # real repository does. A refusal here is a build failure, never skipped.
    code, text, _err = run_sbe(sbe_path, ["init", repo_dir, "--apply"])
    if code != 0:
        raise RuntimeError("sandbox `sbe init --apply` refused (exit %d): %s"
                           % (code, text.strip()[:400]))
    git(repo_dir, "add", "-A")
    git(repo_dir, "commit", "-qm", "init: the BrotherSBE project footprint")
    dossier = write_dossier(repo_dir, change_dir)
    plan_path = write_plan(repo_dir, base, change_dir)
    git(repo_dir, "add", "-A")
    git(repo_dir, "commit", "-qm", "dossier: say-hello change staged")
    code, text, _err = run_sbe(sbe_path, ["plan", dossier, "--cwd", repo_dir],
                               python_exe=python_exe)
    return {"base": base, "dossier": dossier, "plan_path": plan_path,
            "change_id": os.path.basename(dossier), "validate_code": code,
            "validate_text": text}

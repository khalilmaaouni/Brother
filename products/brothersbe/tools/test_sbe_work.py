#!/usr/bin/env python3
"""Fixtures for `sbe work`. Run: python3 tools/test_sbe_work.py

Spec of record: docs/specs/2026-07-30-sbe-work-lifecycle.md. These fixtures
assert exactly what that document says, nothing else: a disagreement between
this file and the eventual src/brothersbe/work.py is a defect in one of them,
never a negotiation settled here.

At the time this file was written, `bin/sbe` has no `work` subcommand at all,
so every invocation below fails at argument parsing (usage, exit 2) before it
reaches anything this suite actually asserts about. Every test here is
expected to FAIL against that gap (never ERROR: no import crash, no fixture
crash) because each test's FIRST assertion checks the `work` call itself, and
a failing assertion there stops the test immediately, before any later line
that assumes infrastructure (a real worktree, a real branch, a real registry
record) `sbe work start` would have created. That red is the point: tests
first, and the same test keeps proving more of the spec as work.py is built
out underneath it, with no rewrite needed here.

Two fixture techniques, both borrowed rather than invented:

  - Real dossiers and a real derived plan, built and read through the landed
    `sbe plan --write` (Loop 1), exactly mirroring the dossier body constants
    and the render_verification()/_find_task()/_find_migration_triplet()
    helpers in tools/test_sbe_plan.py. Copied here rather than imported,
    because suites in this project stay standalone; a disagreement between
    the two copies would be a defect to fix in both, not a shared import to
    chase.
  - Registry records seeded directly into .sbe/tasks.json, the same technique
    tools/test_sbe_tasks.py uses in TestOverlap to test `sbe task check`
    against a collision nothing ever opened honestly. `check`, `finish`, and
    `remove` all read that one registry file, so a scenario that would have
    been produced by `sbe work start` (an open record, a real git worktree)
    can be built directly and inspected on its own, without depending on the
    part of `sbe work` this loop has not written yet.

Every fixture builds a real git repository in a fresh tempfile.mkdtemp() (git
init, one seed commit) and, where a scenario needs one, a real git worktree
inside that same tempdir via `--worktree-dir`. Nothing here ever touches the
real repository this file lives in, and nothing is mocked: the defect this
whole command exists for lives at the seam between what a task declared and
what the tree and the registry actually show, and a mocked diff would test
the mock.
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


# -- dossier body constants, copied from tools/test_sbe_plan.py rather than
# imported: suites in this project stay standalone. Only what `sbe work`
# fixtures need is kept (a single writer decision, a migration triplet, three
# verification rows), and none of it is meant to exercise plan derivation
# itself, only to hand `sbe work` a real, validation-passing plan to read.

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

ADR_PATHS = ("src/widgets/cache.py", "config/cache.yaml")
PHYSICAL_PATH = "migrations/0001_widget_cache.sql"
CHANGE_DIR = "design/widget-cache"

# A SECOND dossier's content, path-disjoint from the one above on purpose:
# `_second_dossier` derives a real plan from this, and a scenario exercising
# TWO dossiers that both start "T01" must not ALSO collide on owned paths
# (the owned-path overlap check stays unscoped by change, by design: "one
# writer per file" regardless of which dossier asked for it), which would
# prove the wrong thing about a worktree-directory or registry-identity fix.
ADR_BODY_B = (
    "# 03. Architecture decision record\n\n"
    "## Context\n"
    "Greeting lookups are slow under normal load.\n\n"
    "## Decision\n"
    "We cache greeting lookups in `src/greetings/cache.py`, backed by "
    "`config/greetings.yaml`.\n\n"
    "## Consequences\n"
    "This adds one dependency to operate.\n\n"
    "## What would flip this\n"
    "If the cache hit rate falls under the agreed target.\n"
)

DATA_MODEL_BODY_B = (
    "# 05. Data model\n\n"
    "## Physical\n"
    "The migration adds `migrations/0001_greeting_cache.sql` for the cache table.\n"
)

ROW_PLAIN_B = ("The cache responds within budget",
              "Run `pytest tests/test_greeting_cache_perf.py`", "Continuous")
ROW_REVERSE_B = ("The migration rollback restores prior state",
                 "Run `python3 migrations/0001_greeting_cache.sql --reverse`", "On demand")
ROW_RECONCILE_B = ("Reconciliation confirms row counts match",
                   "Run `python3 tools/reconcile_greeting_cache.py`", "Daily")
DEFAULT_ROWS_B = [ROW_PLAIN_B, ROW_REVERSE_B, ROW_RECONCILE_B]


def render_verification(rows):
    lines = ["# 07. Verification plan", "",
             "| Claim this design makes | The check that proves it | When it runs |",
             "|---|---|---|"]
    for claim, check, when in rows:
        lines.append("| %s | %s | %s |" % (claim, check, when))
    return "\n".join(lines) + "\n"


class WorkFixture(unittest.TestCase):
    """A fresh repository, a fresh dossier, and a worktree-dir, all inside one
    tempdir per test. The plan itself is derived per-test (never in setUp): a
    real command failure here would be a fixture bug, and unittest reports
    every exception raised inside setUp as an ERROR regardless of its type, so
    the one call this suite genuinely expects to keep succeeding (`sbe plan`,
    landed in Loop 1) is asserted inside the test body instead, exactly like
    tools/test_sbe_plan.py already does for the same reason.
    """

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
        self.dossier = self._write_dossier()
        self.change_id = os.path.basename(self.dossier)

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    # -- process helpers. Three values, not two: a two-value return here
    # would read as a possible (verdict, evidence) pair to the honesty
    # meta-test in evals/test_no_data_class.py, exactly the reason
    # tools/test_sbe_tasks.py's sbe() and tools/test_sbe_plan.py's sbe() are
    # both 3-tuples. This is the same shape, mirrored, not reinvented.

    def sbe(self, *argv):
        out = subprocess.run([sys.executable, SBE] + list(argv), capture_output=True, text=True)
        return out.returncode, out.stdout + out.stderr, out.stderr

    def work(self, *argv):
        return self.sbe("work", *argv, "--cwd", self.repo)

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
        """(plan dict, plan path, the command's own output). Three values, not
        two, for the same reason every process helper here is a 3-tuple: see
        the module docstring on `sbe()`."""
        plan_path = os.path.join(self.dossier, "08-plan.json")
        code, out, _err = self.sbe("plan", self.dossier, "--write", "--cwd", self.repo)
        self.assertEqual(code, 0, "fixture setup could not derive a plan through the "
                                  "landed `sbe plan --write`; this is a fixture bug, not "
                                  "the `sbe work` gap this suite exists to prove: %s" % out)
        with io.open(plan_path, encoding="utf-8") as fh:
            plan = json.load(fh)
        return plan, plan_path, out

    def _save_plan(self, plan_path, plan):
        with io.open(plan_path, "w", encoding="utf-8") as fh:
            json.dump(plan, fh)

    def _second_dossier(self, change_dir):
        """A second, real dossier at `change_dir`, deriving its own T01
        (every derived plan starts fresh there, regardless of dossier path,
        so this collides on task id with `self.dossier`'s own T01 exactly
        the way two sibling dossiers do in practice) that OWNS DIFFERENT
        PATHS from `self.dossier` (the `_B` constants, path-disjoint from
        the module-level ones `_write_dossier` uses): the owned-path overlap
        check stays unscoped by change on purpose ("one writer per file"
        regardless of dossier), so two dossiers used together in one
        scenario must not ALSO collide there, which would prove the wrong
        thing about whichever fix is under test. Used only by scenarios
        that need TWO real, independently-derived plans rather than one
        seeded registry record standing in for the second."""
        answers = {"changes_contract": "n", "crosses_boundary": "n",
                   "reversible_under_hour": "y", "touches_sensitive": "n",
                   "consumers": "none"}
        intake = {"answers": answers, "binding": {"head": self.base}}
        write(self.repo, os.path.join(change_dir, "00-intake.json"), json.dumps(intake))
        write(self.repo, os.path.join(change_dir, "03-adr.md"), ADR_BODY_B)
        write(self.repo, os.path.join(change_dir, "05-data-model.md"), DATA_MODEL_BODY_B)
        write(self.repo, os.path.join(change_dir, "07-verification.md"),
              render_verification(DEFAULT_ROWS_B))
        return os.path.join(self.repo, change_dir)

    def _plan_for(self, dossier):
        """Same as `_plan`, for an arbitrary dossier path rather than
        `self.dossier`."""
        plan_path = os.path.join(dossier, "08-plan.json")
        code, out, _err = self.sbe("plan", dossier, "--write", "--cwd", self.repo)
        self.assertEqual(code, 0, "fixture setup could not derive a plan for %s through "
                                  "the landed `sbe plan --write`: %s" % (dossier, out))
        with io.open(plan_path, encoding="utf-8") as fh:
            plan = json.load(fh)
        return plan, plan_path, out

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
        """(forward, reverse, reconciliation). Copied from
        tools/test_sbe_plan.py's own helper of the same name: forward is
        found by the path it owns, reverse and reconciliation by the
        dependency edge plus what their acceptance text mentions, never by a
        hardcoded task id, because id assignment is the planner's business,
        not this suite's."""
        forward = None
        for t in plan["tasks"]:
            if PHYSICAL_PATH in (t.get("owns") or []):
                forward = t
                break
        self.assertIsNotNone(forward, "expected a migration forward task owning %r: %s"
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

    # -- registry: the single source of task state, per the spec. Some
    # scenarios seed a record directly into .sbe/tasks.json rather than through
    # `sbe work start`, exactly the way tools/test_sbe_tasks.py's TestOverlap
    # seeds a collision directly to test `sbe task check` in isolation: the
    # part of `sbe work` that would have produced this record honestly is not
    # the part under test in those scenarios.

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

    def _record_for_change(self, task_id, change):
        for t in self._registry()["tasks"]:
            if t.get("id") == task_id and t.get("change") == change:
                return t
        return None

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
        # `change` defaults to THIS fixture's own dossier (self.change_id):
        # every scenario that seeds a record without overriding it is
        # implicitly "this dossier's own task", so `cmd_start`'s and
        # `cmd_brief`'s change-scoped dependency and OPEN-collision checks
        # still find it, exactly as they did before those checks existed.
        # Pass `change=...` explicitly to seed a DIFFERENT dossier's record.
        record = {
            "id": task_id, "change": self.change_id, "agent": "alpha", "role": "writer",
            "worktree": None, "ownedPaths": [], "readOnlyPaths": [], "baseCommit": self.base,
            "expiry": None, "status": "open", "verifyCommand": "true",
            "evidenceId": None, "openedAt": "2026-07-30T00:00:00Z", "closedAt": None,
        }
        record.update(overrides)
        return record

    # -- real git worktrees, for scenarios that need one to exist before the
    # `sbe work` call under test runs. Always created inside self.worktree_dir,
    # itself inside self.tmp: the repo law this loop is bound by says a
    # worktree dir lives inside the tempdir, never in the real repo tree.

    def _worktree_for(self, task_id, commit=None):
        path = os.path.join(self.worktree_dir, "wt-%s" % task_id)
        git(self.repo, "worktree", "add", "--detach", "-q", path, commit or self.base)
        return path

    def branch_exists(self, name):
        out = subprocess.run(["git", "rev-parse", "--verify", "--quiet",
                              "refs/heads/" + name],
                             cwd=self.repo, capture_output=True, text=True)
        return out.returncode == 0

    def worktree_path_for(self, task_id):
        """The path `sbe work start` is documented to create the worktree at:
        <worktree-dir>/<repo-name>-sbe-<task-id>, canonicalized the same way
        `src/brothersbe/work.py`'s `_candidate_worktree` canonicalizes it
        (`os.path.realpath`) before that path is ever stored in the registry
        or printed. On macOS `self.worktree_dir` sits under the tempdir, and
        `/var` there is itself a symlink to `/private/var`, so the raw join
        below and production's own stored/printed value would otherwise be
        two different strings naming the same directory. Canonicalizing once
        here means callers get the canonical form for free instead of every
        assertion wrapping this helper in `os.path.realpath` itself."""
        raw = os.path.join(self.worktree_dir,
                           "%s-sbe-%s" % (os.path.basename(self.repo), task_id))
        return os.path.realpath(raw)


class TestStartValid(WorkFixture):
    def test_start_on_a_valid_plan_task_opens_branch_worktree_record_and_prints_the_contract(self):
        plan, plan_path, _out = self._plan()
        reviewer = self._find_reviewer(plan)  # owns nothing: legal for a reviewer to start
        code, text, _ = self.work("start", reviewer["id"], "--plan", plan_path,
                                  "--worktree-dir", self.worktree_dir, "--agent", "alpha")
        self.assertEqual(code, 0, text)

        branch = "sbe/%s/%s" % (self.change_id, reviewer["id"])
        self.assertTrue(self.branch_exists(branch),
                        "expected branch %s to exist: %s" % (branch, text))
        worktree = self.worktree_path_for(reviewer["id"])
        self.assertTrue(os.path.isdir(worktree),
                        "expected a dedicated worktree at %s: %s" % (worktree, text))

        record = self._record(reviewer["id"])
        self.assertIsNotNone(record, "expected an open registry record for %s: %s"
                             % (reviewer["id"], text))
        self.assertEqual(record["status"], "open", record)
        self.assertEqual(record["role"], reviewer["role"], record)
        self.assertEqual(sorted(record["ownedPaths"] or []), sorted(reviewer["owns"] or []),
                         record)
        self.assertEqual(record["worktree"], worktree, record)

        for acceptance in reviewer["acceptance"]:
            self.assertIn(acceptance, text, "expected acceptance criterion printed: %s" % text)
        for cmd in reviewer["verificationCommands"]:
            self.assertIn(cmd, text, "expected verification command printed: %s" % text)
        for src in reviewer["dossierSources"]:
            self.assertIn(src, text, "expected dossier source printed: %s" % text)


class TestControllerLease(WorkFixture):
    def _acquire(self, task_id, plan_path, controller="claude:fable:one",
                 worktree_dir=None, ttl=None):
        argv = ["lease", "acquire", task_id, "--plan", plan_path,
                "--controller", controller, "--json"]
        if worktree_dir is not None:
            argv += ["--worktree-dir", worktree_dir]
        if ttl is not None:
            argv += ["--ttl-seconds", str(ttl)]
        code, text, err = self.work(*argv)
        payload = json.loads(text) if code == 0 else None
        return code, text, err, payload

    def test_acquire_is_idempotent_and_blocks_same_task_or_worktree(self):
        plan, plan_path, _ = self._plan()
        task = self._find_reviewer(plan)
        code, text, _, first = self._acquire(
            task["id"], plan_path, worktree_dir=self.worktree_dir)
        self.assertEqual(code, 0, text)
        self.assertEqual(first["state"], "live")
        self.assertEqual(first["lease"]["worktree"],
                         os.path.realpath(self.worktree_path_for(task["id"])))

        code, text, _, retry = self._acquire(
            task["id"], plan_path, worktree_dir=self.worktree_dir)
        self.assertEqual(code, 0, text)
        self.assertEqual(retry["lease"]["leaseId"], first["lease"]["leaseId"])
        self.assertTrue(retry["idempotent"])

        code, text, _, _ = self._acquire(
            task["id"], plan_path, controller="cursor:other",
            worktree_dir=self.worktree_dir)
        self.assertNotEqual(code, 0, text)
        self.assertIn(first["lease"]["leaseId"], text)

        dossier_b = self._second_dossier("design/greeting-cache")
        plan_b, plan_path_b, _ = self._plan_for(dossier_b)
        same_id = self._find_task(plan_b, task["id"])
        code, text, _, _ = self._acquire(
            same_id["id"], plan_path_b, controller="cursor:other",
            worktree_dir=self.worktree_dir)
        self.assertNotEqual(code, 0, text)
        self.assertIn("worktree", text.lower())

    def test_renew_release_stale_block_and_named_break(self):
        plan, plan_path, _ = self._plan()
        task = self._find_reviewer(plan)
        code, text, _, acquired = self._acquire(
            task["id"], plan_path, worktree_dir=self.worktree_dir)
        self.assertEqual(code, 0, text)
        lease_id = acquired["lease"]["leaseId"]

        code, text, _ = self.work("lease", "renew", lease_id, "--controller",
                                  "claude:fable:one", "--ttl-seconds", "60", "--json")
        self.assertEqual(code, 0, text)
        renewed = json.loads(text)
        self.assertEqual(renewed["lease"]["leaseId"], lease_id)

        data = self._registry()
        data["controllerLeases"][0]["expiresAt"] = "2000-01-01T00:00:00Z"
        write(self.repo, os.path.join(".sbe", "tasks.json"), json.dumps(data))
        code, text, _, _ = self._acquire(
            task["id"], plan_path, controller="cursor:other",
            worktree_dir=self.worktree_dir)
        self.assertNotEqual(code, 0, text)
        self.assertIn("stale", text.lower())
        code, text, _ = self.work("lease", "renew", lease_id, "--controller",
                                  "claude:fable:one")
        self.assertNotEqual(code, 0, text)
        code, text, _ = self.work("lease", "release", lease_id, "--controller",
                                  "claude:fable:one", "--reason", "done")
        self.assertNotEqual(code, 0, text)

        code, text, _ = self.work("lease", "break", lease_id, "--who", "Khalil",
                                  "--why", "worker confirmed stopped", "--json")
        self.assertEqual(code, 0, text)
        broken = json.loads(text)["lease"]
        self.assertEqual(broken["release"]["kind"], "broken")
        self.assertEqual(broken["release"]["actor"], "Khalil")

    def test_start_requires_matching_lease_and_preserves_legacy_without_one(self):
        plan, plan_path, _ = self._plan()
        task = self._find_reviewer(plan)
        code, text, _, acquired = self._acquire(
            task["id"], plan_path, worktree_dir=self.worktree_dir)
        self.assertEqual(code, 0, text)

        code, text, _ = self.work("start", task["id"], "--plan", plan_path,
                                  "--worktree-dir", self.worktree_dir,
                                  "--agent", "alpha")
        self.assertNotEqual(code, 0, text)
        self.assertIn("lease", text.lower())
        self.assertFalse(self.branch_exists("sbe/%s/%s" % (self.change_id, task["id"])))

        code, text, _ = self.work("start", task["id"], "--plan", plan_path,
                                  "--worktree-dir", self.worktree_dir,
                                  "--agent", "alpha", "--lease-id",
                                  acquired["lease"]["leaseId"])
        self.assertEqual(code, 0, text)

    def test_twenty_processes_cannot_acquire_the_same_task_twice(self):
        plan, plan_path, _ = self._plan()
        task = self._find_reviewer(plan)
        procs = []
        for i in range(20):
            procs.append(subprocess.Popen(
                [sys.executable, SBE, "work", "lease", "acquire", task["id"],
                 "--plan", plan_path, "--controller", "controller-%d" % i,
                 "--worktree-dir", self.worktree_dir, "--json", "--cwd", self.repo],
                stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True))
        results = [p.communicate(timeout=120) + (p.returncode,) for p in procs]
        winners = [(out, err) for out, err, code in results if code == 0]
        self.assertEqual(len(winners), 1, results)
        data = self._registry()
        self.assertEqual(len(data["controllerLeases"]), 1, data)


class TestStartRefusals(WorkFixture):
    def test_start_refuses_an_unknown_task_id(self):
        _plan, plan_path, _out = self._plan()
        code, text, _ = self.work("start", "T99-not-in-plan", "--plan", plan_path,
                                  "--worktree-dir", self.worktree_dir, "--agent", "alpha")
        self.assertEqual(code, 2, text)
        self.assertIn("T99-not-in-plan", text)

    def test_start_refuses_when_a_dependency_is_not_yet_closed(self):
        plan, plan_path, _out = self._plan()
        forward, reverse, _recon = self._find_migration_triplet(plan)
        code, text, _ = self.work("start", reverse["id"], "--plan", plan_path,
                                  "--worktree-dir", self.worktree_dir, "--agent", "alpha")
        self.assertNotEqual(code, 0, text)
        self.assertIn(forward["id"], text)

    def test_start_refuses_when_a_dependency_was_force_closed(self):
        plan, plan_path, _out = self._plan()
        forward, reverse, _recon = self._find_migration_triplet(plan)
        self._seed(self._base_record(forward["id"], status="closed",
                                     ownedPaths=forward["owns"],
                                     forced={"who": "operator", "why": "hotfix, accepted "
                                                                       "out of band",
                                            "at": "2026-07-30T00:00:00Z", "verdict": "FAIL",
                                            "violations": []}))
        code, text, _ = self.work("start", reverse["id"], "--plan", plan_path,
                                  "--worktree-dir", self.worktree_dir, "--agent", "alpha")
        self.assertNotEqual(code, 0, text)
        self.assertIn(forward["id"], text)
        self.assertIn("forced", text.lower(),
                      "a FORCED close must not read as a satisfied dependency")

    def test_start_refuses_an_existing_branch_collision(self):
        plan, plan_path, _out = self._plan()
        t01 = self._find_task(plan, "T01")
        branch = "sbe/%s/%s" % (self.change_id, t01["id"])
        git(self.repo, "branch", branch)
        code, text, _ = self.work("start", t01["id"], "--plan", plan_path,
                                  "--worktree-dir", self.worktree_dir, "--agent", "alpha")
        self.assertNotEqual(code, 0, text)
        self.assertIn(branch, text)

    def test_start_refuses_an_existing_worktree_directory_collision(self):
        plan, plan_path, _out = self._plan()
        t01 = self._find_task(plan, "T01")
        worktree = self.worktree_path_for(t01["id"])
        os.makedirs(worktree)
        code, text, _ = self.work("start", t01["id"], "--plan", plan_path,
                                  "--worktree-dir", self.worktree_dir, "--agent", "alpha")
        self.assertNotEqual(code, 0, text)
        self.assertIn(worktree, text)

    def test_start_refuses_an_already_open_registry_record(self):
        plan, plan_path, _out = self._plan()
        t01 = self._find_task(plan, "T01")
        self._seed(self._base_record(t01["id"], ownedPaths=t01["owns"]))
        code, text, _ = self.work("start", t01["id"], "--plan", plan_path,
                                  "--worktree-dir", self.worktree_dir, "--agent", "alpha")
        self.assertNotEqual(code, 0, text)
        self.assertIn(t01["id"], text)


class TestChangeScopedStart(WorkFixture):
    """Card 1: a task's identity is the pair (change, taskId), not taskId
    alone. Every derived plan starts fresh at "T01" (docs/KNOWN-LIMITS.md),
    so sibling dossiers routinely produce a task called "T01"; before this
    fix, `cmd_start`'s Rule 4 OPEN-collision check compared bare ids only,
    and T01 open in one dossier refused starting T01 in an unrelated one.

    The OTHER dossier's own open T01 is SEEDED directly (never through a real
    `sbe work start`), so it creates no branch and no worktree: this isolates
    the registry-identity fix under test from the SEPARATE, still-open
    worktree-directory-by-task-id-only limit `docs/KNOWN-LIMITS.md` now
    names (a worktree PATH collision, not a registry IDENTITY collision:
    `sbe work start`'s worktree naming is unchanged by this fix)."""

    def test_start_is_not_blocked_by_an_open_same_id_task_in_a_different_dossier(self):
        plan, plan_path, _out = self._plan()
        t01 = self._find_task(plan, "T01")
        other_change = "design/some-other-dossier"
        self._seed(self._base_record(t01["id"], change=other_change, status="open",
                                     ownedPaths=["src/unrelated.py"]))

        code, text, _ = self.work("start", t01["id"], "--plan", plan_path,
                                  "--worktree-dir", self.worktree_dir, "--agent", "alpha")
        self.assertEqual(code, 0,
                         "T01 open in a DIFFERENT dossier must not block starting THIS "
                         "dossier's own T01: identity is (change, taskId), not id alone: "
                         "%s" % text)

        records = [t for t in self._registry()["tasks"] if t["id"] == t01["id"]]
        self.assertEqual(len(records), 2, records)
        by_change = {r["change"]: r for r in records}
        self.assertEqual(by_change[other_change]["status"], "open", records)
        self.assertEqual(by_change[self.change_id]["status"], "open", records)
        self.assertEqual(by_change[self.change_id]["worktree"],
                         self.worktree_path_for(t01["id"]), records)

    def test_start_still_refuses_reopening_this_dossiers_own_open_task(self):
        """Calibration precondition for the test above, stated as its own
        assertion: the fix must not weaken the SAME-dossier collision it
        always refused, only the cross-dossier one it should never have
        refused."""
        plan, plan_path, _out = self._plan()
        t01 = self._find_task(plan, "T01")
        code, text, _ = self.work("start", t01["id"], "--plan", plan_path,
                                  "--worktree-dir", self.worktree_dir, "--agent", "alpha")
        self.assertEqual(code, 0, text)
        code, text, _ = self.work("start", t01["id"], "--plan", plan_path,
                                  "--worktree-dir", self.worktree_dir, "--agent", "beta")
        self.assertNotEqual(code, 0, text)
        self.assertIn(t01["id"], text)
        self.assertIn(self.change_id, text)

    def test_start_dependency_check_ignores_a_same_id_closed_record_in_a_different_dossier(self):
        """The dependency-scoping half of card 1's fix. A dependsOn edge
        points at a SIBLING task inside THIS dossier's own plan; a closed
        record sharing that id in a DIFFERENT dossier (every derived plan
        starts fresh at "T01", so this happens routinely) must never
        silently satisfy it, the same collision class the OPEN-collision
        check above closes."""
        plan, plan_path, _out = self._plan()
        forward, reverse, _recon = self._find_migration_triplet(plan)
        other_change = "design/some-other-dossier"
        self._seed(self._base_record(forward["id"], change=other_change, status="closed",
                                     ownedPaths=["src/unrelated.py"]))
        code, text, _ = self.work("start", reverse["id"], "--plan", plan_path,
                                  "--worktree-dir", self.worktree_dir, "--agent", "alpha")
        self.assertNotEqual(code, 0,
                            "a closed record in a DIFFERENT dossier must not satisfy this "
                            "dossier's own unmet dependency: %s" % text)
        self.assertIn(forward["id"], text)


class TestStartTwoDossiersUnderDefaultFlags(WorkFixture):
    """The headline journey this whole card exists for: two SIBLING
    dossiers, each deriving their own plan (every derived plan starts fresh
    at "T01"), both `sbe work start T01` -- under the DOCUMENTED DEFAULT
    flags, no `--worktree-dir` at all. `TestChangeScopedStart` above proves
    the REGISTRY identity half of this (Rule 4's OPEN-collision check,
    scoped to (change, id)) with a SEEDED second record, deliberately never
    exercising a second real `sbe work start`; this class is the other half,
    a REAL second start, because round 1's registry fix never actually
    reached a second dossier under default flags: the WORKTREE-exists check
    ran first, against one fixed default directory keyed by task id alone
    (`os.path.dirname(root)`, `<repo>-sbe-<taskId>`), and refused the second
    dossier before its now-scoped registry check ever ran. Round 2 scopes
    the DEFAULT worktree directory to the dossier's own change id (the same
    string the branch name and the registry record already carry), so two
    dossiers' identical task id now lands in two different default
    directories. An EXPLICIT `--worktree-dir` is untouched: an operator who
    points two dossiers at the SAME directory by hand is still making that
    collision happen themselves, not hitting a default trap."""

    def test_two_dossiers_start_the_same_task_id_under_true_default_flags(self):
        plan_a, plan_path_a, _out = self._plan()
        t01_a = self._find_task(plan_a, "T01")
        second_dossier = self._second_dossier("design/dossier-beta")
        plan_b, plan_path_b, _out = self._plan_for(second_dossier)
        t01_b = self._find_task(plan_b, "T01")
        change_b = os.path.basename(second_dossier)
        self.assertNotEqual(self.change_id, change_b, "fixture precondition: two "
                            "genuinely different dossiers")
        self.assertEqual(t01_a["id"], "T01", t01_a)
        self.assertEqual(t01_b["id"], "T01", t01_b)

        code, text, _ = self.work("start", t01_a["id"], "--plan", plan_path_a,
                                  "--agent", "alpha")
        self.assertEqual(code, 0,
                         "the FIRST dossier's T01 must start under true default flags "
                         "(no --worktree-dir at all): %s" % text)
        code, text, _ = self.work("start", t01_b["id"], "--plan", plan_path_b,
                                  "--agent", "beta")
        self.assertEqual(code, 0,
                         "the headline journey this card exists for: a SECOND "
                         "dossier's own T01 must also start under true default flags, "
                         "not just when an operator manually supplies two different "
                         "--worktree-dir values: %s" % text)

        record_a = self._record_for_change(t01_a["id"], self.change_id)
        record_b = self._record_for_change(t01_b["id"], change_b)
        self.assertIsNotNone(record_a)
        self.assertIsNotNone(record_b)
        self.assertNotEqual(record_a["worktree"], record_b["worktree"],
                            "two dossiers' identical task id must land in DIFFERENT "
                            "default worktree directories: %r" % (record_a["worktree"],))
        self.assertTrue(os.path.isdir(record_a["worktree"]), record_a)
        self.assertTrue(os.path.isdir(record_b["worktree"]), record_b)
        # The repo law this suite is bound by: a worktree dir lives inside
        # the tempdir, never in the real repo tree. The documented default
        # (the repository's parent directory) resolves to `self.tmp` here
        # (self.repo = self.tmp/repo), so a change-scoped SUBDIRECTORY of it
        # still satisfies that law without a dedicated --worktree-dir.
        real_tmp = os.path.realpath(self.tmp)
        self.assertTrue(os.path.realpath(record_a["worktree"]).startswith(real_tmp),
                        record_a)
        self.assertTrue(os.path.realpath(record_b["worktree"]).startswith(real_tmp),
                        record_b)

    def test_an_explicit_shared_worktree_dir_across_dossiers_still_collides(self):
        """Calibration precondition, stated as its own assertion: the fix
        above must not silently protect an operator who explicitly points
        two dossiers at the SAME --worktree-dir by hand. That remains that
        operator's own choice, refused exactly as `TestStartRefusals`
        already proves for the single-dossier case."""
        plan_a, plan_path_a, _out = self._plan()
        t01_a = self._find_task(plan_a, "T01")
        second_dossier = self._second_dossier("design/dossier-beta")
        plan_b, plan_path_b, _out = self._plan_for(second_dossier)
        t01_b = self._find_task(plan_b, "T01")

        code, text, _ = self.work("start", t01_a["id"], "--plan", plan_path_a,
                                  "--worktree-dir", self.worktree_dir, "--agent", "alpha")
        self.assertEqual(code, 0, text)
        code, text, _ = self.work("start", t01_b["id"], "--plan", plan_path_b,
                                  "--worktree-dir", self.worktree_dir, "--agent", "beta")
        self.assertNotEqual(code, 0,
                            "an EXPLICIT, operator-chosen shared --worktree-dir across "
                            "two dossiers must still collide: %s" % text)
        self.assertIn(self.worktree_path_for(t01_a["id"]), text)

    def test_a_second_collision_under_default_flags_names_the_scoped_path_not_the_stale_one(self):
        """When BOTH the plain default path AND this dossier's own scoped
        fallback path are already taken, the refusal must name the SCOPED
        path (the one this attempt actually landed on), not silently revert
        to naming the OTHER dossier's stale default path: a message-
        accuracy edge case in the fallback itself, found in self-review, not
        in the original hostile round. The scoped path is seeded directly
        (`os.makedirs`, the same technique `TestStartRefusals` uses for a
        plain collision), never through a second real `sbe work start`,
        because starting the SAME dossier's SAME task twice would refuse
        earlier still, on the BRANCH collision, before ever reaching the
        worktree-directory logic under test here."""
        plan_a, plan_path_a, _out = self._plan()
        t01_a = self._find_task(plan_a, "T01")
        second_dossier = self._second_dossier("design/dossier-beta")
        plan_b, plan_path_b, _out = self._plan_for(second_dossier)
        t01_b = self._find_task(plan_b, "T01")
        change_b = os.path.basename(second_dossier)

        # alpha takes the plain default path for real.
        code, text, _ = self.work("start", t01_a["id"], "--plan", plan_path_a,
                                  "--agent", "alpha")
        self.assertEqual(code, 0, text)
        record_a = self._record_for_change(t01_a["id"], self.change_id)
        self.assertIsNotNone(record_a)
        default_dir = os.path.dirname(record_a["worktree"])
        scoped_worktree = os.path.join(default_dir, change_b,
                                       "%s-sbe-%s" % (os.path.basename(self.repo),
                                                      t01_b["id"]))
        os.makedirs(scoped_worktree)

        # beta's FIRST attempt: the plain default (alpha's) is taken, AND
        # beta's own scoped fallback path is ALSO already taken (seeded
        # above). The refusal must name the scoped path, not alpha's.
        code, text, _ = self.work("start", t01_b["id"], "--plan", plan_path_b,
                                  "--agent", "beta")
        self.assertNotEqual(code, 0, text)
        refusal_line = [ln for ln in text.splitlines() if ln.startswith("sbe work start: "
                        "refused.")]
        self.assertEqual(len(refusal_line), 1,
                         "expected exactly one 'refused.' line: %s" % text)
        self.assertIn(scoped_worktree, refusal_line[0],
                      "the refusal must name THIS dossier's own colliding scoped "
                      "path: %s" % refusal_line[0])
        self.assertNotIn(record_a["worktree"], refusal_line[0],
                         "the refusal itself must not name a DIFFERENT dossier's "
                         "stale default path (the earlier, informational fallback "
                         "note above it may still mention it): %s" % refusal_line[0])


class TestBriefUnscopedLookupNeverRaises(WorkFixture):
    """Round 2 fix: `sbe work brief` has no `--change` flag (its already-
    claimed check stays unscoped by id alone, on purpose; see
    `_dependency_problem`'s docstring). `cmd_brief`'s Rule 4 used to call
    `_find_record(data, task_id)` with no `change`, and that function now
    RAISES `AmbiguousTaskId` whenever the id spans more than one change.
    `cmd_brief` had no handler for it, so it fell through to `main()`'s
    generic `RegistryUnusable` catch, which told the operator to pass
    `--change` (exit usage) -- a flag `brief` does not have, an impossible
    remedy. `cmd_brief` now looks the id up with `_last_record_any_change`,
    which reproduces the ORIGINAL (pre-`change`) lookup exactly: the last
    registry record with this id, any change, never ambiguous, because a
    read-only briefing has no `--change` to disambiguate with in the first
    place."""

    def test_brief_is_not_blocked_by_a_same_id_record_in_a_different_change(self):
        plan, plan_path, _out = self._plan()
        t01 = self._find_task(plan, "T01")
        other_change = "design/some-other-dossier"
        # Append order matters for `_last_record_any_change` (last wins, the
        # same rule the original pre-`change` `_find_record` used): seed the
        # OTHER change's CLOSED record first, THIS dossier's own CLOSED
        # record second, so a correct fix reads THIS one last and still
        # finds it not-open, exactly mirroring the hostile round-1 repro
        # (two CLOSED T01 records in two changes).
        self._seed(self._base_record(t01["id"], change=other_change, status="closed",
                                     ownedPaths=["src/unrelated.py"]))
        self._seed(self._base_record(t01["id"], change=self.change_id, status="closed",
                                     ownedPaths=t01["owns"]))
        code, text, _ = self.work("brief", "--plan", plan_path, "--task", t01["id"],
                                  "--json")
        self.assertEqual(code, 0,
                         "a task id with CLOSED records in two different changes must "
                         "not crash `sbe work brief`, which has no --change flag to "
                         "recover with: %s" % text)
        brief = json.loads(text)
        self.assertEqual(brief["taskId"], t01["id"], brief)

    def test_brief_still_refuses_a_same_id_task_open_in_any_change(self):
        """Calibration precondition: the fix above must not weaken the
        already-claimed refusal itself, only the crash on the unscoped
        lookup that used to precede it."""
        plan, plan_path, _out = self._plan()
        t01 = self._find_task(plan, "T01")
        self._seed(self._base_record(t01["id"], change=self.change_id, status="open",
                                     ownedPaths=t01["owns"]))
        code, text, _ = self.work("brief", "--plan", plan_path, "--task", t01["id"])
        self.assertNotEqual(code, 0, text)
        self.assertIn(t01["id"], text)
        self.assertIn("OPEN", text)


class TestReviewerCannotOwn(WorkFixture):
    def test_start_refuses_a_reviewer_task_that_owns_paths(self):
        plan, _plan_path, _out = self._plan()
        reviewer = self._find_reviewer(plan)
        reviewer["owns"] = ["docs/should-not-be-owned.md"]
        edited_path = os.path.join(self.dossier, "08-plan-reviewer-owns.json")
        self._save_plan(edited_path, plan)
        code, text, _ = self.work("start", reviewer["id"], "--plan", edited_path,
                                  "--worktree-dir", self.worktree_dir, "--agent", "alpha")
        self.assertNotEqual(code, 0, text)
        self.assertIn(reviewer["id"], text)
        self.assertIn("reviewer", text.lower())


class TestCheck(WorkFixture):
    def test_check_names_an_out_of_scope_shell_write_and_reports_not_closable(self):
        plan, _plan_path, _out = self._plan()
        t01 = self._find_task(plan, "T01")
        worktree = self._worktree_for(t01["id"])
        self._seed(self._base_record(t01["id"], worktree=worktree, ownedPaths=t01["owns"],
                                     baseCommit=self.base, verifyCommand="true"))
        owned_rel = t01["owns"][0]
        write(worktree, owned_rel, "changed, in scope\n")
        git(worktree, "add", "-A")
        git(worktree, "commit", "-qm", "in scope edit")
        write(worktree, "docs/sneaky.md", "never declared\n")  # plain open(), no tool
        code, text, _ = self.work("check", t01["id"])
        self.assertEqual(code, 1, text)
        self.assertIn(owned_rel, text)
        self.assertIn("docs/sneaky.md", text)
        self.assertIn("NOT CLOSABLE", text)

    def test_check_strips_change_the_same_way_open_stores_it(self):
        """Round 2 fix: `cmd_check` used to read `--change` RAW
        (`getattr(args, "change", None)`, no `.strip()`), the same asymmetry
        `tasks.py`'s `cmd_close` had against `cmd_open`'s stored, stripped
        value. A record seeded with change="pad" (what `sbe task open
        --change "  pad  "` actually stores) must still be found by giving
        the identical PADDED string back; before this fix, `code` here was
        2 (usage: 'no registry record'), not 1 (found, but closed)."""
        plan, _plan_path, _out = self._plan()
        t01 = self._find_task(plan, "T01")
        self._seed(self._base_record(t01["id"], change="pad", status="closed",
                                     ownedPaths=t01["owns"]))
        code, text, _ = self.work("check", t01["id"], "--change", "  pad  ")
        self.assertEqual(code, 1,
                         "a padded --change must still find the record `open` stored "
                         "stripped, not read as 'no registry record': %s" % text)
        self.assertNotIn("no registry record", text.lower())


class TestFinishRefusals(WorkFixture):
    def test_finish_refuses_on_out_of_scope_writes_naming_the_paths(self):
        plan, _plan_path, _out = self._plan()
        t01 = self._find_task(plan, "T01")
        worktree = self._worktree_for(t01["id"])
        self._seed(self._base_record(t01["id"], worktree=worktree, ownedPaths=t01["owns"],
                                     baseCommit=self.base, verifyCommand="true"))
        write(worktree, "docs/sneaky.md", "never declared\n")
        git(worktree, "add", "-A")
        git(worktree, "commit", "-qm", "sneaky")
        code, text, _ = self.work("finish", t01["id"])
        self.assertNotEqual(code, 0, text)
        self.assertIn("docs/sneaky.md", text)

    def test_finish_refuses_as_no_data_when_the_verification_receipt_is_absent(self):
        plan, _plan_path, _out = self._plan()
        t01 = self._find_task(plan, "T01")
        worktree = self._worktree_for(t01["id"])
        self._seed(self._base_record(t01["id"], worktree=worktree, ownedPaths=t01["owns"],
                                     baseCommit=self.base, verifyCommand="true"))
        write(worktree, t01["owns"][0], "changed, in scope\n")
        git(worktree, "add", "-A")
        git(worktree, "commit", "-qm", "in scope, no receipt")
        code, text, _ = self.work("finish", t01["id"])
        self.assertNotEqual(code, 0, text)
        self.assertIn("NO-DATA", text)
        self.assertIn("true", text, "the verify command must be named, not just NO-DATA")


class TestFinishIntegration(WorkFixture):
    def test_finish_with_scope_kept_and_a_real_receipt_closes_clean_and_unblocks_a_dependent(self):
        plan, _plan_path, _out = self._plan()
        forward, reverse, _recon = self._find_migration_triplet(plan)
        # The dossier never states a check command for the forward migration
        # task itself: only 07-verification rows carry commands, and those
        # become separate reviewer tasks (see the derivation spec, rule 3 vs
        # rule 2). Adding one here is the general plan SHAPE the spec
        # documents (every task record may carry verificationCommands),
        # never a derivation rule this suite invents.
        forward["verificationCommands"] = ["true"]
        forward["requiredEvidence"] = ["command-receipt"]
        edited_path = os.path.join(self.dossier, "08-plan-with-verify.json")
        self._save_plan(edited_path, plan)

        code, text, _ = self.work("start", forward["id"], "--plan", edited_path,
                                  "--worktree-dir", self.worktree_dir, "--agent", "alpha")
        self.assertEqual(code, 0, text)
        worktree = self.worktree_path_for(forward["id"])

        owned_rel = forward["owns"][0]
        write(worktree, owned_rel, "-- forward migration, in scope\n")
        git(worktree, "add", "-A")
        git(worktree, "commit", "-qm", "forward migration, in scope")

        # The receipt is written to the repository's OWN evidence store, not
        # into the worktree's tracked tree: a receipt is metadata about the
        # run, not a file the task declared it owns, and the postcondition
        # must not have to special-case it the way it special-cases its own
        # registry file.
        receipt_path = os.path.join(self.repo, ".sbe", "evidence", "forward-receipt.json")
        code, text, _ = self.sbe("evidence", "run", "--out", receipt_path,
                                 "--cwd", worktree, "--", "true")
        self.assertEqual(code, 0, text)

        code, text, _ = self.work("finish", forward["id"])
        self.assertEqual(code, 0, text)
        record = self._record(forward["id"])
        self.assertEqual(record["status"], "closed", record)
        self.assertNotIn("forced", record, "a clean finish must not look forced")

        code, text, _ = self.work("start", reverse["id"], "--plan", edited_path,
                                  "--worktree-dir", self.worktree_dir, "--agent", "beta")
        self.assertEqual(code, 0,
                         "the forward task is now closed clean, so its dependent must be "
                         "able to start: %s" % text)


class TestForceFinish(WorkFixture):
    def test_finish_force_closes_marks_forced_visible_in_check_and_never_satisfies_a_dependent(self):
        plan, plan_path, _out = self._plan()
        forward, reverse, _recon = self._find_migration_triplet(plan)
        worktree = self._worktree_for(forward["id"])
        self._seed(self._base_record(forward["id"], worktree=worktree,
                                     ownedPaths=forward["owns"], baseCommit=self.base,
                                     verifyCommand="true"))
        write(worktree, "docs/out-of-scope.md", "never declared\n")
        git(worktree, "add", "-A")
        git(worktree, "commit", "-qm", "out of scope, forced through")

        code, text, _ = self.work("finish", forward["id"], "--force",
                                  "--who", "operator", "--why", "hotfix, accepted out of band")
        self.assertEqual(code, 0, text)
        self.assertIn("FORCED", text)
        record = self._record(forward["id"])
        self.assertEqual(record["status"], "closed", record)

        code, text, _ = self.work("check", forward["id"])
        self.assertIn("FORCED", text, "a forced close must print FORCED loudly in check too")

        code, text, _ = self.work("start", reverse["id"], "--plan", plan_path,
                                  "--worktree-dir", self.worktree_dir, "--agent", "beta")
        self.assertNotEqual(code, 0,
                           "a FORCED close of a dependency must never satisfy a dependent's "
                           "start: %s" % text)
        self.assertIn(forward["id"], text)
        self.assertIn("forced", text.lower())


class TestRemove(WorkFixture):
    def test_remove_refuses_an_open_task(self):
        plan, _plan_path, _out = self._plan()
        t01 = self._find_task(plan, "T01")
        worktree = self._worktree_for(t01["id"])
        self._seed(self._base_record(t01["id"], worktree=worktree, ownedPaths=t01["owns"],
                                     status="open"))
        code, text, _ = self.work("remove", t01["id"])
        self.assertNotEqual(code, 0, text)
        self.assertTrue(os.path.isdir(worktree),
                        "an open task's worktree must never be removed")

    def test_remove_refuses_a_dirty_closed_worktree_without_override(self):
        plan, _plan_path, _out = self._plan()
        t01 = self._find_task(plan, "T01")
        worktree = self._worktree_for(t01["id"])
        self._seed(self._base_record(t01["id"], worktree=worktree, ownedPaths=t01["owns"],
                                     status="closed"))
        write(worktree, "uncommitted.txt", "dirty\n")  # never committed
        code, text, _ = self.work("remove", t01["id"])
        self.assertNotEqual(code, 0, text)
        self.assertTrue(os.path.isdir(worktree),
                        "a dirty worktree must survive a refused remove")

    def test_remove_with_override_dirty_succeeds_and_records_the_reason(self):
        plan, _plan_path, _out = self._plan()
        t01 = self._find_task(plan, "T01")
        worktree = self._worktree_for(t01["id"])
        self._seed(self._base_record(t01["id"], worktree=worktree, ownedPaths=t01["owns"],
                                     status="closed"))
        write(worktree, "uncommitted.txt", "dirty\n")
        reason = "cleanup after an accepted hotfix"
        code, text, _ = self.work("remove", t01["id"], "--override-dirty", reason)
        self.assertEqual(code, 0, text)
        self.assertFalse(os.path.isdir(worktree),
                         "an overridden remove must delete the worktree")
        record = self._record(t01["id"])
        self.assertEqual(record.get("overrideDirty"), reason,
                         "the override reason must be recorded on the registry record, "
                         "permanent visible history: %s" % record)


def _find_unreviewable_git_argv(source):
    """Static analysis, one level deeper than the plain List/Tuple-literal
    scan above. That scan is blind to a call site that builds its argv one
    step removed from a literal, e.g.:

        sub = "mer" + "ge"
        _git([sub, "origin", branch], root)

    where the List IS a literal, but its first element is a concatenation,
    not an ast.Constant string, so the earlier check's `isinstance(head,
    ast.Constant)` guard silently lets it through (the audit's own
    mutation proved this: it goes red only once this function exists).

    Returns a list of human-readable violation strings, empty when every
    call resolves to a str literal head. A call is walked when its func is
    `_git` by Name (as imported and used in src/brothersbe/work.py) or by
    Attribute (module.method access), matching however this codebase might
    spell the call. The argv argument is followed through at most one
    simple `name = <expr>` assignment (and through an `a if cond else b`
    conditional of literals, the one pattern src/brothersbe/work.py itself
    uses at its `sbe work remove` call site) so a legitimate
    variable-holding-a-literal-list is not a false positive; anything this
    resolver cannot trace back to a literal string head (a concatenation,
    a function call, an unresolved name) is reported as unreviewable."""
    tree = ast.parse(source)

    def is_git_call_func(func):
        if isinstance(func, ast.Name) and func.id == "_git":
            return True
        if isinstance(func, ast.Attribute) and func.attr == "_git":
            return True
        return False

    def literal_head_is_safe(list_or_tuple):
        if not isinstance(list_or_tuple, (ast.List, ast.Tuple)) or not list_or_tuple.elts:
            return False
        head = list_or_tuple.elts[0]
        return isinstance(head, ast.Constant) and isinstance(head.value, str)

    # Every simple `name = <expr>` assignment in the module, keyed by name,
    # so a call site passing a variable can be traced to the nearest
    # preceding assignment rather than being invisible to this check. This
    # is a best-effort, whole-module resolution (not scope-aware): sufficient
    # for the straight-line style this module is written in, and it never
    # needs to be more than that to catch the shape the audit found.
    assigns_by_name = {}
    for node in ast.walk(tree):
        if (isinstance(node, ast.Assign) and len(node.targets) == 1
                and isinstance(node.targets[0], ast.Name)):
            assigns_by_name.setdefault(node.targets[0].id, []).append(
                (node.lineno, node.value))

    def resolve_is_safe(expr, before_line, depth=0):
        if depth > 4:
            return False
        if isinstance(expr, (ast.List, ast.Tuple)):
            return literal_head_is_safe(expr)
        if isinstance(expr, ast.IfExp):
            return (resolve_is_safe(expr.body, before_line, depth + 1)
                    and resolve_is_safe(expr.orelse, before_line, depth + 1))
        if isinstance(expr, ast.Name):
            candidates = [pair for pair in assigns_by_name.get(expr.id, [])
                          if pair[0] <= before_line]
            if not candidates:
                return False
            candidates.sort(key=lambda pair: pair[0])
            return resolve_is_safe(candidates[-1][1], before_line, depth + 1)
        return False

    violations = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call) or not is_git_call_func(node.func):
            continue
        if not node.args:
            violations.append(
                "_git call at line %d passes no argv argument at all" % node.lineno)
            continue
        if not resolve_is_safe(node.args[0], node.lineno):
            violations.append(
                "_git call at line %d builds its argv head from something this law "
                "cannot read as a literal string (a concatenation, a function call, "
                "or a variable it cannot trace to one): a variable-built git "
                "subcommand is unreviewable by this law" % node.lineno)
    return violations


class TestQuotedVerifyCommandReceipt(unittest.TestCase):
    """External proof, estate C, critical: finish refused a receipt whose argv,
    head and exit all matched, because the plan's raw quoted command text can
    never equal a rejoined argv. The matcher canonicalizes both sides."""

    def test_shlex_canonicalization_binds_quoted_commands(self):
        import os as _os, sys as _sys
        src = _os.path.join(HERE, "..", "src", "brothersbe")
        if _os.path.abspath(_os.path.join(HERE, "..", "src")) not in _sys.path:
            _sys.path.insert(0, _os.path.abspath(_os.path.join(HERE, "..", "src")))
        from brothersbe import work as work_mod
        import json as _json, tempfile as _tf
        d = _tf.mkdtemp()
        try:
            evdir = _os.path.join(d, ".sbe", "evidence")
            _os.makedirs(evdir)
            _sys.path.insert(0, _os.path.abspath(_os.path.join(HERE, "..", "src")))
            from brothersbe import evidence as ev
            receipt = {"schemaVersion": "1.1", "generatorVersion": "x",
                       "repository": "r", "baseCommit": None,
                       "headCommit": "a" * 40,
                       "argv": ["python3", "-c", "print('OK')"],
                       "argvRedactions": 0,
                       "startedAt": "2026-07-31T00:00:00Z",
                       "endedAt": "2026-07-31T00:00:01Z",
                       "startedAtEpoch": 1.0, "endedAtEpoch": 2.0,
                       "durationSeconds": 1.0, "exitCode": 0,
                       "toolVersions": {}, "environment": "test",
                       "stdoutSha256": "0" * 64, "stderrSha256": "0" * 64,
                       "stdoutBytes": 0, "stderrBytes": 0,
                       "outputPolicy": "digests only",
                       "workingTreeDirty": False, "workingTreeDetail": "",
                       "ciRunId": None, "coveredFiles": ["x.py"],
                       "coveredFilesSource": "declared"}
            receipt["runId"] = ev.compute_seal(receipt)
            _io = __import__("io")
            _io.open(_os.path.join(evdir, "q.json"), "w").write(
                _json.dumps(receipt))
            found = work_mod._matching_receipt(
                d, "python3 -c \"print('OK')\"", "a" * 40)
            self.assertIsNotNone(
                found["match"],
                "the quoted plan text and the shell-split argv are the same "
                "command and must bind: %r" % found)
        finally:
            import shutil as _sh
            _sh.rmtree(d, ignore_errors=True)


class TestNoMergeLaw(unittest.TestCase):
    """Source-level: nothing in the work code path ever runs git merge, rebase,
    push, or deploy (CHANGELOG and KNOWN-LIMITS both say deploy is guarded
    too, so the forbidden set must say so mechanically). Matched against the
    shape this codebase actually constructs git argv in (a list or tuple
    literal whose FIRST element is the subcommand, e.g. ["merge", branch],
    the way src/brothersbe/tasks.py's own _git() is called), never against
    plain substring search: a docstring or comment that merely mentions the
    word "merge" must not trip this, and does not, because neither is a
    List/Tuple AST node."""

    def test_work_module_never_constructs_a_merge_rebase_or_push_argv(self):
        path = os.path.join(ROOT, "src", "brothersbe", "work.py")
        self.assertTrue(os.path.exists(path),
                        "src/brothersbe/work.py does not exist yet; there is nothing to "
                        "grep for the no-merge law against")
        with io.open(path, encoding="utf-8") as fh:
            source = fh.read()
        tree = ast.parse(source, filename=path)
        forbidden = ("merge", "rebase", "push", "deploy")
        hits = []
        for node in ast.walk(tree):
            if not isinstance(node, (ast.List, ast.Tuple)) or not node.elts:
                continue
            head = node.elts[0]
            if isinstance(head, ast.Constant) and isinstance(head.value, str):
                word = head.value.lower()
                if word in forbidden:
                    hits.append("%s (line %d)" % (word, node.lineno))
        self.assertEqual(hits, [],
                         "src/brothersbe/work.py constructs git argv starting with a "
                         "forbidden subcommand: %s. sbe work must never merge, rebase onto "
                         "the default branch, push, or deploy." % ", ".join(hits))

    def test_work_module_never_builds_git_argv_head_from_something_unreadable(self):
        """The gap the audit found: the scan above only recognises an
        ast.Constant string sitting directly as the first element of a
        List/Tuple literal. A subcommand built one step removed (string
        concatenation, a helper call, an unresolved variable) is invisible
        to it and must be caught here instead."""
        path = os.path.join(ROOT, "src", "brothersbe", "work.py")
        self.assertTrue(os.path.exists(path),
                        "src/brothersbe/work.py does not exist yet; there is nothing to "
                        "check against the unreviewable-argv law")
        with io.open(path, encoding="utf-8") as fh:
            source = fh.read()
        violations = _find_unreviewable_git_argv(source)
        self.assertEqual(violations, [],
                         "src/brothersbe/work.py builds a git argv head in a way this "
                         "law cannot read statically, so it cannot be reviewed for a "
                         "hidden merge/rebase/push/deploy: %s" % "; ".join(violations))

    def test_calibration_concat_built_merge_argv_is_caught_by_the_unreviewable_law(self):
        """Calibration, per the audit's own mutation: assign a concat-built
        "merge" subcommand to a plain variable and pass that straight to
        _git(...), on a scratch copy of the real file written to a tempdir,
        never on src/brothersbe/work.py itself. The unreviewable-argv check
        must go red for exactly this shape, proving it actually bites rather
        than passing vacuously."""
        real_path = os.path.join(ROOT, "src", "brothersbe", "work.py")
        with io.open(real_path, encoding="utf-8") as fh:
            real_source = fh.read()
        self.assertEqual(_find_unreviewable_git_argv(real_source), [],
                         "calibration precondition failed: the real file must already "
                         "be clean before a mutated scratch copy of it is checked")

        scratch_dir = tempfile.mkdtemp()
        try:
            mutated_source = real_source + (
                "\n\n"
                "def _scratch_mutation_concat_built_merge(root):\n"
                "    sub = 'mer' + 'ge'\n"
                "    argv = [sub, 'origin', 'main']\n"
                "    return _git(argv, root)\n")
            scratch_path = os.path.join(scratch_dir, "work_mutated_scratch_copy.py")
            with io.open(scratch_path, "w", encoding="utf-8") as fh:
                fh.write(mutated_source)
            with io.open(scratch_path, encoding="utf-8") as fh:
                reloaded = fh.read()
            violations = _find_unreviewable_git_argv(reloaded)
        finally:
            shutil.rmtree(scratch_dir, ignore_errors=True)

        self.assertTrue(violations,
                        "calibration failed: a concat-built merge subcommand assigned "
                        "to a variable and passed straight to _git(...) must be caught, "
                        "and it was not; the mutation never went red")
        self.assertTrue(
            any("unreviewable" in v for v in violations),
            "calibration failed: the violation message must say a variable-built git "
            "subcommand is unreviewable by this law: %s" % violations)


class TestHelpMeansHelp(unittest.TestCase):
    """`sbe work start -h` printed the right usage and exited 2: the module
    caught argparse's SystemExit(0) for help and folded it into exit_usage,
    and a LEADING `-h` (`sbe work -h`) never reached this module at all,
    refused by the top-level parser. Help is not an error. Calibrated by
    reinjecting the bare `except SystemExit: return exit_usage` and the
    argparse-first dispatch in cli.py: each fixture failed on a returncode of
    2 where 0 was asserted, before the fix was restored, the restore verified
    against the pre-recorded `git hash-object` of the fixed files."""

    def run_sbe(self, *argv):
        tmp = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, tmp, True)
        out = subprocess.run([sys.executable, SBE, "work"] + list(argv),
                             capture_output=True, text=True, cwd=tmp)
        return out.returncode, out.stdout + out.stderr, tmp

    def test_help_exits_0_with_usage_and_starts_nothing(self):
        for argv in (("-h",), ("--help",), ("start", "-h"), ("check", "-h"),
                     ("finish", "-h"), ("remove", "-h")):
            code, text, tmp = self.run_sbe(*argv)
            self.assertEqual(code, 0, "sbe work %s exited %d: %s"
                             % (" ".join(argv), code, text))
            self.assertIn("usage", text.lower(),
                          "sbe work %s printed no usage: %r" % (" ".join(argv), text))
            self.assertEqual(os.listdir(tmp), [],
                             "sbe work %s created something in a directory it was only "
                             "asked to describe itself from" % " ".join(argv))

    def test_a_genuinely_bad_flag_still_exits_2(self):
        for argv in (("--bogus",), ("start", "--bogus")):
            code, text, _tmp = self.run_sbe(*argv)
            self.assertEqual(code, 2, "sbe work %s exited %d, not the usage error 2: %s"
                             % (" ".join(argv), code, text))


if __name__ == "__main__":
    unittest.main(verbosity=2)

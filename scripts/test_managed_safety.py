"""Tests for managed_safety.py: Brother-managed execution is safe by
construction, not by an operator remembering a setup ritual.

Every test isolates HOME, CODEX_HOME, TMPDIR, BROTHERMODE_ROOT and
BROTHERMODE_VAULT_ROOT under one mkdtemp, matching the pattern
scripts/test_model_worker.py's own _turn helper already uses: never the
founder's real ~/.claude or ~/.codex, never a real repository.
"""
import json
import os
import shlex
import shutil
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
BROTHER_RUN = os.path.join(HERE, "brother_run.py")

import journal  # noqa: E402
import loop_bridge  # noqa: E402
import managed_safety  # noqa: E402

# E100: one sandbox for every temp tree this process makes, removed at exit.
import os as _e100_os, sys as _e100_sys  # noqa: E402
_e100_sys.path.append(_e100_os.path.join(
    _e100_os.path.dirname(_e100_os.path.abspath(__file__)), '.'))
try:  # noqa: E402
    import tmp_sandbox as _e100_tmp
    _e100_tmp.install()
except ImportError:
    _e100_sys.stderr.write(
        "tmp_sandbox absent: %s leaves its temp trees behind\n"
        % _e100_os.path.basename(__file__))


def _sh(args, cwd=None, env=None):
    return subprocess.run(args, cwd=cwd, env=env, capture_output=True,
                          text=True, timeout=300)


def _make_repo(tmp):
    repo = os.path.join(tmp, "repo")
    os.makedirs(repo)
    for args in (["init", "-q", "-b", "main"],
                 ["config", "user.email", "a@b.c"],
                 ["config", "user.name", "t"]):
        _sh(["git"] + args, cwd=repo)
    with open(os.path.join(repo, "base.txt"), "w", encoding="utf-8") as fh:
        fh.write("base\n")
    _sh(["git", "add", "-A"], cwd=repo)
    _sh(["git", "commit", "-q", "-m", "R0"], cwd=repo)
    return repo


def _unit(unit_id="u1", write_scope=None, objective="do the thing"):
    """The exact shape scripts/loop_bridge.py's run_node() builds and hands
    to worker.run(): unit_id, objective, done_check, write_scope,
    read_scope, role, risk_class, attempt, prior_failure_note. Confirmed
    against loop_bridge.run_node, never invented."""
    return {"unit_id": unit_id, "objective": objective,
           "done_check": "true", "write_scope": list(write_scope or ["one.txt"]),
           "read_scope": [], "role": "builder", "risk_class": "normal",
           "attempt": 1, "prior_failure_note": ""}


class _StubSpawn(object):
    """Stands in for bm_worker_spawn: records the argv/cwd/environ a lane
    worker was actually started with, and marks its own first write in the
    run journal so a test can compare that ordinal against the claim's."""

    def __init__(self, run_dir):
        self.run_dir = run_dir
        self.calls = []

    def SpawningWorker(self, argv, cwd=None, environ=None):
        return _StubWorker(self, argv, cwd, environ)


class _StubWorker(object):
    def __init__(self, spawn, argv, cwd, environ):
        self._spawn = spawn
        self.argv = list(argv)
        self.cwd = cwd
        self.environ = environ

    def run(self, unit):
        self._spawn.calls.append({"cwd": self.cwd,
                                  "environ": dict(self.environ or {})})
        journal.append(self._spawn.run_dir, "worker.wrote",
                       parent_ids=journal.previous(self._spawn.run_dir),
                       unit_id=unit.get("unit_id"),
                       payload={"cwd": self.cwd})
        return {"worker_claim": "did it", "artifacts": ["one.txt"],
               "cost": {"tokens": 0, "minutes": 0}, "status": "returned"}


class _IsolatedTestCase(unittest.TestCase):
    """One mkdtemp sandbox per test; HOME, CODEX_HOME, TMPDIR,
    BROTHERMODE_ROOT and BROTHERMODE_VAULT_ROOT all point inside it."""

    def setUp(self):
        # realpath up front: on macOS /var and /tmp are symlinks, and
        # bm_store.py's own symlink-escape guard refuses a store whose path
        # resolves differently than it was given, exactly the mismatch an
        # un-realpath'd mkdtemp() produces here (measured: FenceHookBase in
        # products/brothermode/tools/test_bm_fence_hook.py already applies
        # the same realpath for the same reason).
        self.sandbox = os.path.realpath(
            tempfile.mkdtemp(prefix="managed-safety-test-"))
        self.addCleanup(shutil.rmtree, self.sandbox, ignore_errors=True)
        home = os.path.join(self.sandbox, "home")
        codex_home = os.path.join(self.sandbox, "codex-home")
        tmp = os.path.join(self.sandbox, "tmp")
        bm_root = os.path.join(self.sandbox, "bm-root")
        vault_root = os.path.join(self.sandbox, "vault-root")
        for p in (home, codex_home, tmp, bm_root, vault_root):
            os.makedirs(p, exist_ok=True)
        self.env_overrides = {"HOME": home, "CODEX_HOME": codex_home,
                              "TMPDIR": tmp, "BROTHERMODE_ROOT": bm_root,
                              "BROTHERMODE_VAULT_ROOT": vault_root}
        patcher = mock.patch.dict(os.environ, self.env_overrides, clear=False)
        patcher.start()
        self.addCleanup(patcher.stop)
        # This machine may run BM_FENCE_MODE=enforced session-wide (a
        # separate, deliberate decision this file does not touch); every
        # test here that means "default/advisory" must mean it regardless
        # of the machine it runs on, matching FenceHookBase's own scrub in
        # products/brothermode/tools/test_bm_fence_hook.py.
        for k in ("BM_FENCE_MODE", "BM_FENCE_STRICT", "BM_FENCE_SESSION_ID"):
            old = os.environ.pop(k, None)
            if old is not None:
                self.addCleanup(os.environ.__setitem__, k, old)

    def subprocess_env(self, **extra):
        env = dict(os.environ)
        env.update(extra)
        return env


class ManagedRunNeedsNoSetup(_IsolatedTestCase):
    """Steering 7.4/7.5: a clean lane gets a store and a claim before the
    worker runs, with no operator setup ritual in between."""

    def test_a_clean_repo_gets_a_store_and_a_claim_before_the_worker_runs(self):
        lane = os.path.join(self.sandbox, "lane")
        os.makedirs(lane)
        run_dir = os.path.join(self.sandbox, "run")
        os.makedirs(run_dir)
        unit = _unit()

        spawn = _StubSpawn(run_dir)
        worker = loop_bridge.LaneWorker(spawn, ["irrelevant-argv"])
        with mock.patch.dict(os.environ, {journal.RUN_DIR_ENV_VAR: run_dir}):
            result = worker.run(unit, cwd=lane)

        self.assertEqual(result.get("status"), "returned", result)
        self.assertEqual(len(spawn.calls), 1)
        env_seen = spawn.calls[0]["environ"]
        self.assertEqual(env_seen.get("BM_FENCE_MODE"), "enforced")
        self.assertEqual(env_seen.get("BROTHERMODE_ROOT"), lane)
        self.assertTrue(env_seen.get("BM_FENCE_SESSION_ID"),
                        "the child must carry a real session id, not an "
                        "empty one")

        # The claim genuinely persisted and is independently readable,
        # through the same path the fence hook itself reads at write time.
        bs, fh, problem = managed_safety._load_fence_modules()
        self.assertIsNone(problem, problem)
        rows = fh.active_claims(lane)
        names = {r["name"] for r in rows}
        self.assertIn("u1", names)

        # THE ORDINAL: the claim was journalled before the worker's first
        # write, in the same journal. This is the mutant "claim-after-
        # worker" is written to kill: move materialize() after inner.run()
        # and this assertion fails because the order in the journal flips.
        events = journal.read(run_dir)
        self.assertIsNotNone(events)
        types = [e["type"] for e in events]
        self.assertIn("safety.claim", types)
        self.assertIn("worker.wrote", types)
        self.assertLess(types.index("safety.claim"), types.index("worker.wrote"),
                        "the claim must be journalled before the worker's "
                        "first write: %r" % types)

    def test_the_child_carries_the_unit_id_its_worker_was_started_for(self):
        """VN3b: journal.UNIT_ID_ENV_VAR is exported HERE and nowhere else,
        because this is the only place in the estate that starts a process
        for exactly one unit. Everything brother_run exports is run-scoped,
        which is why every vault.recall event a hook journalled from inside
        a worker carried unit_id None, and why
        brother_run._recalled_records_for_unit (which matches on unit_id)
        found nothing for every unit on every run
        (docs/plan/research/vault-night-2026-09-08/VN4c-felt-surface-installed.md,
        gap G3). Drop the export and that receipt goes empty again."""
        lane = os.path.join(self.sandbox, "lane-unit-id")
        os.makedirs(lane)
        run_dir = os.path.join(self.sandbox, "run-unit-id")
        os.makedirs(run_dir)
        spawn = _StubSpawn(run_dir)
        worker = loop_bridge.LaneWorker(spawn, ["irrelevant-argv"])
        with mock.patch.dict(os.environ, {journal.RUN_DIR_ENV_VAR: run_dir}):
            worker.run(_unit(unit_id="VN3b-1"), cwd=lane)

        self.assertEqual(len(spawn.calls), 1)
        self.assertEqual(
            spawn.calls[0]["environ"].get(journal.UNIT_ID_ENV_VAR), "VN3b-1",
            "the child was not told which unit it is working on: %r"
            % spawn.calls[0]["environ"].get(journal.UNIT_ID_ENV_VAR))


class TwoUnitsCannotShareAPath(_IsolatedTestCase):
    """Steering 7.9's overlapping-claim case: worker A holds a path, worker
    B wants the same path, B is held, A's ownership is unchanged."""

    def test_the_second_lane_is_held_and_the_first_keeps_ownership(self):
        lane = os.path.join(self.sandbox, "shared-lane")
        os.makedirs(lane)
        unit_a = _unit(unit_id="a1", write_scope=["shared.txt"])
        unit_b = _unit(unit_id="b1", write_scope=["shared.txt"])

        session_a, why_a = managed_safety.materialize(lane, unit_a)
        self.assertIsNotNone(session_a, why_a)
        self.assertEqual(why_a, "")

        session_b, why_b = managed_safety.materialize(lane, unit_b)
        self.assertIsNone(session_b)
        self.assertNotEqual(why_b, "")

        bs, fh, problem = managed_safety._load_fence_modules()
        self.assertIsNone(problem, problem)
        rows = fh.active_claims(lane)
        names = {r["name"] for r in rows}
        self.assertIn("a1", names, "A's ownership must be unchanged")
        self.assertNotIn("b1", names, "B must never have been granted the "
                         "path A already holds")


class CapabilityFloorRefusesAutonomy(_IsolatedTestCase):
    """Steering 7.7/7.8: an empty Codex home forces the floor to A3, and
    brother_run.py's own preflight refuses BEFORE the door is ever asked,
    so the worker never starts and the repository stays untouched."""

    def test_capability_floor_alone_reads_a3_on_an_empty_codex_home(self):
        env = self.subprocess_env(BROTHER_MODEL_CLIENT="codex")
        result = managed_safety.probe(self.sandbox, self.sandbox, env=env)
        self.assertNotEqual(result["hooks"]["state"], managed_safety.PRESENT,
                            result["hooks"])
        self.assertEqual(managed_safety.capability_floor(result), "A3")

    def test_an_empty_codex_home_forces_a3_and_the_worker_never_starts(self):
        """P1, 2026-09-08: only an EXPLICIT A0 (execute_then_check) request
        is refused for a floor it cannot back; the request must say so
        itself, via BROTHER_AUTONOMY_DIAL, not rely on the A1 default."""
        repo = _make_repo(self.sandbox)
        env = self.subprocess_env(BROTHER_MODEL_CLIENT="codex",
                                  BROTHER_AUTONOMY_DIAL="A0")
        proc = _sh([sys.executable, BROTHER_RUN, "one file exists",
                   "--cwd", repo, "--runs-root", self.sandbox, "--quiet"],
                  env=env)
        self.assertNotEqual(proc.returncode, 0,
                            proc.stdout + proc.stderr)
        self.assertIn("REFUSED", proc.stdout, proc.stdout + proc.stderr)
        self.assertIn("A3", proc.stdout, proc.stdout + proc.stderr)
        status = _sh(["git", "status", "--porcelain"], cwd=repo)
        self.assertEqual(status.stdout.strip(), "",
                         "the repository must be untouched: the worker "
                         "never started")

    def test_a_resumed_run_on_an_empty_codex_home_is_refused_too(self):
        """Codex finding 2, 2026-09-07: the caller gates the launch,
        INCLUDING a resumed run, so an A0 request on a machine that
        cannot enforce a fence cannot reach a worker just by resuming
        instead of starting fresh. The gate now sits before the whole
        --resume/--continue/fresh dispatch (brother_run.main, right after
        `resumed = False`), so this drives --resume with a hand-built
        Work document the way
        AFileSourcedCheckIsFencedLikeARewrittenOne._run_dir does in
        scripts/test_brother_run.py, on the SAME empty-Codex-home
        environment the sibling test above proves forces A3 for a fresh
        run."""
        repo = _make_repo(self.sandbox)
        runs_root = os.path.join(self.sandbox, "runs")
        run_dir = os.path.join(runs_root, "docs", "plan", "runs", "r1")
        os.makedirs(run_dir)
        doc = os.path.join(run_dir, "work.json")
        with open(doc, "w", encoding="utf-8") as fh:
            json.dump({"outcome": "one file exists", "work_id": "w1",
                      "rows": [{"id": "U1", "title": "make it",
                                "done_check": "test -f one.txt",
                                "owns": ["one.txt"], "depends_on": []}]}, fh)
        env = self.subprocess_env(BROTHER_MODEL_CLIENT="codex",
                                  BROTHER_AUTONOMY_DIAL="A0")
        proc = _sh([sys.executable, BROTHER_RUN, "--resume", run_dir,
                   "--cwd", repo, "--runs-root", runs_root, "--quiet"],
                  env=env)
        self.assertNotEqual(proc.returncode, 0,
                            proc.stdout + proc.stderr)
        self.assertIn("REFUSED", proc.stdout, proc.stdout + proc.stderr)
        self.assertIn("A3", proc.stdout, proc.stdout + proc.stderr)
        status = _sh(["git", "status", "--porcelain"], cwd=repo)
        self.assertEqual(status.stdout.strip(), "",
                         "the repository must be untouched: the worker "
                         "never started")


class DefaultRequestProceedsOnAMissingFloor(_IsolatedTestCase):
    """P1, 2026-09-08: STEERING 7.1 (never globally break an ordinary
    manual session) and 7.7/7.8 (downgrade or refuse BY NAME; only
    high-autonomy work refuses on missing enforcement). The A1 default
    is advisory/manual work, so an empty Codex home must never refuse
    it the way CapabilityFloorRefusesAutonomy proves for an explicit A0
    request; it proceeds to the worker, and the journal still carries
    the true, unenforced safety state rather than staying silent about
    it."""

    def test_a_default_a1_run_on_an_empty_codex_home_proceeds_and_journals_missing(self):
        repo = _make_repo(self.sandbox)
        # A stub decomposer and model, the same seam
        # scripts/test_brother_run.py's write_stub uses: this test proves
        # the preflight lets an A1 request THROUGH to the worker, not that
        # a real `codex` binary answers, so no real model is called.
        decomposer = os.path.join(self.sandbox, "decomposer.py")
        with open(decomposer, "w", encoding="utf-8") as fh:
            fh.write(
                "#!/usr/bin/env python3\n"
                "import json, sys\n"
                "sys.stdin.read()\n"
                "print(json.dumps([{'id': 'U1', 'objective': 'make it',\n"
                "    'done_check': 'test -f one.txt',\n"
                "    'writes': ['one.txt'], 'deps': []}]))\n")
        os.chmod(decomposer, 0o755)
        model = os.path.join(self.sandbox, "writer_model.py")
        with open(model, "w", encoding="utf-8") as fh:
            fh.write(
                "#!/usr/bin/env python3\n"
                "import re, sys\n"
                "prompt = sys.argv[-1] if len(sys.argv) > 1 else ''\n"
                "m = re.search(r'Declared write scope: ([^\\n]+)', prompt)\n"
                "for path in (p.strip() for p in "
                "(m.group(1).split(',') if m else [])):\n"
                "    if path:\n"
                "        with open(path, 'w') as out:\n"
                "            out.write('written by the stub model\\n')\n"
                "print('stub model wrote')\n")
        os.chmod(model, 0o755)
        env = self.subprocess_env(
            BROTHER_MODEL_CLIENT="codex",
            DOOR_MODEL_CMD="%s %s" % (shlex.quote(sys.executable), shlex.quote(decomposer)),
            MODEL_WORKER_CMD="%s %s" % (shlex.quote(sys.executable), shlex.quote(model)))
        self.assertNotIn("BROTHER_AUTONOMY_DIAL", env,
                         "this test proves the A1 DEFAULT, not a set dial")
        proc = _sh([sys.executable, BROTHER_RUN, "one file exists",
                   "--cwd", repo, "--runs-root", self.sandbox, "--quiet"],
                  env=env)
        out = proc.stdout + proc.stderr
        self.assertNotIn("REFUSED", out, out)
        run_dirs = [os.path.join(dirpath, name)
                   for dirpath, dirnames, filenames in os.walk(self.sandbox)
                   for name in filenames if name == "journal.jsonl"]
        self.assertEqual(len(run_dirs), 1, out + "\n" + repr(run_dirs))
        with open(run_dirs[0], "r", encoding="utf-8") as fh:
            entries = [json.loads(line) for line in fh if line.strip()]
        safety_entries = [e for e in entries
                          if e.get("type") == "safety.enforcement"]
        self.assertEqual(len(safety_entries), 1, entries)
        payload = safety_entries[0]["payload"]
        self.assertEqual(payload["requested"], "A1", payload)
        self.assertEqual(payload["floor"], "A3", payload)
        self.assertNotEqual(payload["capabilities"]["hooks"],
                            managed_safety.PRESENT, payload)


class EstablishedFailOpenIsNotReversed(_IsolatedTestCase):
    """A regression guard, not a new claim: this file never touches
    bm_fence_hook.py, so its established fail-open behaviour on a payload
    it cannot read must be exactly what it always was. What P1 adds is the
    OTHER half: managed_safety's own probe must say NO-DATA, never PRESENT,
    about a fence copy that cannot answer for itself, so the run's
    preflight refuses the higher-autonomy mode instead of trusting it."""

    def test_an_unreadable_hook_leaves_the_hook_allowing_and_the_preflight_refusing(self):
        bs, fh, problem = managed_safety._load_fence_modules()
        self.assertIsNone(problem, problem)

        # (1) unchanged: a payload this hook cannot even read as an object
        # still ALLOWS (returns no decision), exactly as
        # products/brothermode/tools/test_bm_fence_hook.py's own
        # CalibratedFailOpen class already proves against the real hook.
        decision, notes = fh.decide(["not", "a", "dict"])
        self.assertIsNone(decision)

        # (2) new: a fence copy that raises answering its own sanity check
        # must be reported NO-DATA, not PRESENT.
        class _BrokenFenceHook(object):
            @staticmethod
            def enforced_mode(env=None):
                raise RuntimeError("simulated: this copy cannot be trusted")

        with mock.patch.object(managed_safety, "_load_fence_modules",
                              return_value=(bs, _BrokenFenceHook, None)):
            entry = managed_safety._check_fence(dict(os.environ))
        self.assertEqual(entry["state"], managed_safety.NODATA, entry)

        result = {"hooks": {"state": managed_safety.PRESENT, "detail": "",
                            "remedy": ""},
                 "fence": entry,
                 "claims": {"state": managed_safety.PRESENT, "detail": "",
                           "remedy": ""},
                 "git": {"state": managed_safety.PRESENT, "detail": "",
                        "remedy": ""},
                 "worktree": {"state": managed_safety.PRESENT, "detail": "",
                             "remedy": ""},
                 "worker": {"state": managed_safety.PRESENT, "detail": "",
                           "remedy": ""},
                 "receipt_path": {"state": managed_safety.PRESENT,
                                  "detail": "", "remedy": ""}}
        self.assertEqual(managed_safety.capability_floor(result), "A2",
                         "a fence that cannot be trusted must not leave the "
                         "floor at A0")


if __name__ == "__main__":
    unittest.main()

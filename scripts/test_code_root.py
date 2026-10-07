#!/usr/bin/env python3
"""model_router.code_root(): the one place a loop tool learns which tree its code runs from (B5-08, U3 item 3).

WHY. repo_root() is `git rev-parse --show-toplevel` of the cwd, and the loop runs inside the launch worktree it lands
commits into, so every execution site that used it ran unfrozen code from the landing tree. A proof run must execute
the frozen candidate named by BROTHER_CODE_ROOT, and in a proof phase an unset or unusable value is a refusal, never
a quiet fall back to the landing tree.

One condition per case. The module's own selftest runs last, so a sweep of model_router.py with this file as its
check measures the whole module, not only the function added here.

Run: python3 -B scripts/test_code_root.py
"""
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

LOOP = Path(__file__).resolve().parent / "loop"
sys.path.insert(0, str(LOOP))
import model_router as R  # noqa: E402

KEYS = ("BROTHER_CODE_ROOT", "BROTHER_PROOF_PHASE", "BROTHER_CODEX_ROOT", "BROTHER_REPO_ROOT")


class CodeRoot(unittest.TestCase):
    def setUp(self):
        t = tempfile.TemporaryDirectory(prefix="code-root-")
        self.addCleanup(t.cleanup)
        self.dir = os.path.realpath(t.name)

    def env(self, **values):
        clean = {k: v for k, v in os.environ.items() if k not in KEYS}
        clean.update(values)
        return mock.patch.dict(os.environ, clean, clear=True)

    def test_a_named_directory_is_the_code_root(self):
        with self.env(BROTHER_CODE_ROOT=self.dir):
            self.assertEqual(R.code_root(), self.dir)

    def test_a_named_directory_is_the_code_root_in_a_proof_phase(self):
        with self.env(BROTHER_CODE_ROOT=self.dir, BROTHER_PROOF_PHASE="RB"):
            self.assertEqual(R.code_root(), self.dir)

    def test_a_proof_phase_without_a_code_root_refuses(self):
        with self.env(BROTHER_PROOF_PHASE="RC"):
            with self.assertRaises(R.Refused) as caught:
                R.code_root()
        self.assertIn("BROTHER_CODE_ROOT", str(caught.exception))

    def test_a_proof_phase_with_a_missing_code_root_refuses(self):
        with self.env(BROTHER_CODE_ROOT=os.path.join(self.dir, "gone"), BROTHER_PROOF_PHASE="RB"):
            with self.assertRaises(R.Refused):
                R.code_root()

    def test_outside_a_proof_phase_unset_is_the_checkout_root(self):
        with self.env():
            self.assertEqual(R.code_root(), R.repo_root())

    def test_a_named_code_root_that_is_not_a_directory_refuses_outside_a_proof_too(self):
        # A value somebody set and got wrong is corrupt input: it blocks, never reads as "unset".
        with self.env(BROTHER_CODE_ROOT=os.path.join(self.dir, "gone")):
            with self.assertRaises(R.Refused):
                R.code_root()

    def test_a_relative_code_root_refuses(self):
        # A relative root means a different tree for every cwd a launch site picks.
        with self.env(BROTHER_CODE_ROOT=os.path.relpath(self.dir)):
            with self.assertRaises(R.Refused):
                R.code_root()

    def test_the_repo_root_override_is_not_a_code_root(self):
        # BROTHER_REPO_ROOT steers the checkout the router reads its data from; it never names frozen code.
        with self.env(BROTHER_REPO_ROOT=self.dir, BROTHER_PROOF_PHASE="RB"):
            with self.assertRaises(R.Refused):
                R.code_root()

    def test_the_module_selftest_still_passes(self):
        p = subprocess.run([sys.executable, "-B", str(LOOP / "model_router.py"), "--selftest"],
                           capture_output=True, text=True, timeout=300)
        self.assertEqual(p.returncode, 0, p.stdout + p.stderr)
        self.assertIn("OK", p.stdout)


class GuardsTheModuleSweepReaches(unittest.TestCase):
    """This file is the check the lane runs `mutation_sweep.py --module scripts/loop/model_router.py` with, and its
    generic mutations land on the module's FIRST guards, which the module's selftest does not reach. Each is a real
    property, pinned here rather than left as a survivor nobody tests."""

    def test_a_model_the_registry_does_not_name_may_receive_nothing(self):
        reg = {"known": {"id": "x/known", "transport": "claude", "privacy": R.PRIVATE,
                         "quality": {"build": 5}, "cost": 1.0}}
        self.assertTrue(R.may_receive("known", R.PUBLIC, reg=reg))
        self.assertFalse(R.may_receive("unknown", R.PUBLIC, reg=reg))

    def test_a_selftest_that_cannot_finish_exits_nonzero(self):
        with tempfile.TemporaryDirectory(prefix="code-root-registry-") as d:
            bad = os.path.join(d, "registry.json")
            with open(bad, "w") as fh:
                fh.write('{"models": 7}')
            p = subprocess.run([sys.executable, "-B", str(LOOP / "model_router.py"), "--selftest"],
                               env=dict(os.environ, BROTHER_MODEL_REGISTRY=bad),
                               capture_output=True, text=True, timeout=300)
        self.assertNotEqual(p.returncode, 0, p.stdout)
        self.assertIn("FAILED", p.stdout)


class ClaudeBinFindsTheInstalledCli(unittest.TestCase):
    """The loop called ~/.local/bin/claude, absent on the owner's Mac (2026-09-27 02:5x), so every Claude advisor
    call was a not_started row. The desktop app ships Claude Code under a versioned folder that moves with every
    update (2.1.280 and 2.1.281 side by side), so claude_bin() resolves it at call time."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(); self.addCleanup(self.tmp.cleanup); self.home = Path(self.tmp.name)

    def exe(self, path, version=None):
        """A fake CLI that answers only --version (a program that says nothing is one whose version is unknown)."""
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("#!/bin/sh\n" + ("echo '%s (Claude Code)'\n" % version if version else "")); path.chmod(0o755); return str(path)

    def app(self, version):
        return self.exe(self.home / "Library/Application Support/Claude/claude-code" / version / "claude.app/Contents/MacOS/claude", version)

    def resolve(self, **env):
        """HERMETIC (2026-09-30): PATH, the package directories and the program record all point inside this HOME, so the
        machine's own installed claude never answers a case."""
        brother_paths = R._paths()   # the code root's module, the one claude_bin() reads
        with mock.patch.dict(os.environ, dict({"HOME": str(self.home), "PATH": str(self.home / "empty-path"),
                                               "BROTHER_PROGRAM_RECORD": str(self.home / "no-record.json")}, **env)), \
                mock.patch.object(brother_paths, "PACKAGE_BIN_DIRS", (os.path.join("~", ".local", "bin"),)):
            os.environ.pop("BROTHER_CLAUDE_BIN", None) if "BROTHER_CLAUDE_BIN" not in env else None
            return R.claude_bin()

    def test_the_named_binary_wins(self):
        self.app("2.1.281")
        self.assertEqual(self.resolve(BROTHER_CLAUDE_BIN="/opt/claude"), "/opt/claude")

    def test_a_relative_name_is_refused(self):
        with self.assertRaises(R.Refused):
            self.resolve(BROTHER_CLAUDE_BIN="claude")

    def test_the_newest_version_wins_over_the_local_bin(self):
        """2026-09-30: ~/.local/bin/claude came first by PLACE; it was 2.1.251 and did not know claude-opus-5-5 while the
        desktop's 2.1.284 did. The program's own version decides, never where it is installed."""
        self.exe(self.home / ".local/bin/claude", "2.1.251"); newest = self.app("2.1.284")
        self.assertEqual(self.resolve(), os.path.realpath(newest))
        local = self.exe(self.home / ".local/bin/claude", "2.1.290")
        self.assertEqual(self.resolve(), os.path.realpath(local))

    def test_the_newest_app_version_by_number_not_by_text(self):
        self.app("2.1.9"); newest = self.app("2.1.10")
        self.assertEqual(self.resolve(), os.path.realpath(newest))

    def test_nothing_installed_names_the_local_path_so_the_call_is_not_started(self):
        self.assertEqual(self.resolve(), str(self.home / ".local/bin/claude"))

class CodexBinFollowsTheCodeRoot(unittest.TestCase):
    """codex_bin() loaded brother_paths by a file path computed inside the function from its own location: the
    freeze could not resolve it (the deploy canary refused the candidate), and in the deployed flat bin two levels
    up is ~/.claude, which holds no brother_paths.py. It resolves through the code root now: the frozen candidate in
    a proof, the checkout otherwise."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(); self.addCleanup(self.tmp.cleanup)
        scripts = Path(self.tmp.name, "scripts"); scripts.mkdir()
        (scripts / "brother_paths.py").write_text("def codex_bin(env=None):\n    return '/stand-in/codex'\n")
        saved = sys.modules.pop("brother_paths", None)
        self.addCleanup(lambda: sys.modules.__setitem__("brother_paths", saved) if saved else sys.modules.pop("brother_paths", None))
        path_before = list(sys.path); self.addCleanup(lambda: sys.path.__setitem__(slice(None), path_before))

    def test_the_resolver_under_the_code_root_is_the_one_used(self):
        with mock.patch.dict(os.environ, {"BROTHER_CODE_ROOT": self.tmp.name}):
            self.assertEqual(R.codex_bin(), "/stand-in/codex")

    def test_the_path_is_left_as_it_was(self):
        before = list(sys.path)
        with mock.patch.dict(os.environ, {"BROTHER_CODE_ROOT": self.tmp.name}):
            R.codex_bin()
        self.assertEqual(sys.path, before, "scripts/ is on the path only for this one import: a loop module never loses")

    def test_a_different_copy_already_imported_is_refused(self):
        import types
        other = types.ModuleType("brother_paths"); other.__file__ = "/landing/tree/scripts/brother_paths.py"
        other.codex_bin = lambda env=None: "/unfrozen/codex"
        sys.modules["brother_paths"] = other
        with mock.patch.dict(os.environ, {"BROTHER_CODE_ROOT": self.tmp.name}):
            with self.assertRaises(R.Refused):
                R.codex_bin()


class LearningReportRunsTheCodeRoot(unittest.TestCase):
    """mix_advice.gather launched dream_report with `python -m` from the inherited working directory, so in a proof the
    report that picks the cheapest valid model ran the landing tree's copy (Codex audit D2, 2026-09-27: gather before
    LANDING_BEFORE, after LANDING_AFTER, freeze still PASS). It runs from the code root for every caller now."""

    REPORT = "plugin/runtime/brother/core/dream_report.py"

    def setUp(self):
        t = tempfile.TemporaryDirectory(prefix="mix-root-")
        self.addCleanup(t.cleanup)
        self.box = Path(os.path.realpath(t.name))
        for tree in ("landing", "candidate"):
            p = self.box / tree / self.REPORT
            p.parent.mkdir(parents=True)
            p.write_text('import json, sys\nprint(json.dumps({"pass_rate": [{"kind": "openrouter.model", '
                         '"chosen": "%s " + sys.argv[1], "n": 20, "pass": 20}]}))\n' % tree)
        cwd = os.getcwd()
        os.chdir(str(self.box / "landing"))       # the loop runs inside the tree it lands into
        self.addCleanup(os.chdir, cwd)

    def gather(self, run="run-fixture", **values):
        import mix_advice as MA
        clean = {k: v for k, v in os.environ.items() if k not in KEYS}
        clean.update(values)
        with mock.patch.dict(os.environ, clean, clear=True):
            return sorted(MA.gather([str(self.box / "landing" / run) if run == "run-fixture" else run]))

    def proof(self):
        return dict(BROTHER_CODE_ROOT=str(self.box / "candidate"), BROTHER_PROOF_PHASE="RB")

    def test_in_a_proof_the_report_is_the_candidates(self):
        self.assertEqual(self.gather(**self.proof()), ["candidate %s" % (self.box / "landing/run-fixture")])

    def test_a_relative_run_directory_still_names_the_run_the_caller_meant(self):
        self.assertEqual(self.gather(run="run-rel", **self.proof()), ["candidate %s" % (self.box / "landing/run-rel")])

    def test_a_proof_without_a_code_root_refuses_rather_than_skipping_every_run(self):
        # A refusal swallowed per run would read as "no evidence" and the advice would quietly name nothing.
        with self.assertRaises(R.Refused):
            self.gather(BROTHER_PROOF_PHASE="RB")


TICK = LOOP / "brother_night_tick.py"
WATCHDOG = '''import json, os, sys
LIVE = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "docs", "plan", "LIVE-STATE.json")
def read_day_plan_rows(path=LIVE):
    try:
        return json.load(open(path))["rows"]
    except (OSError, ValueError):
        return None
def ready_set_summary(rows):
    return {"ready": [%(label)r] + [r["id"] for r in rows], "in_flight": [], "event_wait": []}
def read_registry():
    return [%(label)r] * %(tasks)d
if __name__ == "__main__":
    open(os.environ["TRIPWIRE_LOG"], "a").write(%(label)r + "\\n")
    print(%(label)r)
'''


class NightwatchRunsTheCodeRoot(unittest.TestCase):
    """brother_night_tick imported task_watchdog from the WATCHED checkout (BROTHER_NIGHTWATCH_ROOT), so in a proof the
    tick ran landing tree code the freeze never saw (Codex audit D4, 2026-09-27: ready before 1, after 2, freeze still
    PASS). With a code root or a proof phase set it loads the candidate's copy and hands it the watched tree's board.
    Each call runs in a fresh interpreter: a module cached by an earlier case would decide the next one."""

    def setUp(self):
        t = tempfile.TemporaryDirectory(prefix="night-root-")
        self.addCleanup(t.cleanup)
        self.box = Path(os.path.realpath(t.name))
        self.landing, self.candidate = self.box / "landing", self.box / "candidate"
        for tree, tasks in ((self.landing, 1), (self.candidate, 3)):
            (tree / "scripts").mkdir(parents=True)
            (tree / "scripts/task_watchdog.py").write_text(WATCHDOG % {"label": tree.name, "tasks": tasks})
        (self.landing / "docs/plan").mkdir(parents=True)
        (self.landing / "docs/plan/LIVE-STATE.json").write_text('{"rows": [{"id": "R1"}]}')
        self.log = self.box / "ran.log"

    def tick(self, call, pre_import=False, **values):
        env = {k: v for k, v in os.environ.items() if k not in KEYS}
        env.update(BROTHER_NIGHTWATCH_ROOT=str(self.landing), TRIPWIRE_LOG=str(self.log), **values)
        code = "import json, sys\n"
        if pre_import:
            code += "sys.path.insert(0, %r)\nimport task_watchdog\n" % str(self.landing / "scripts")
        code += "sys.path.insert(0, %r)\nimport brother_night_tick as T\nprint(json.dumps(T.%s()))\n" % (str(LOOP), call)
        p = subprocess.run([sys.executable, "-B", "-c", code], env=env, cwd=str(self.box),
                           capture_output=True, text=True, timeout=120)
        self.assertEqual(p.returncode, 0, p.stdout + p.stderr)
        return json.loads(p.stdout.strip().splitlines()[-1])

    def ready_ids(self, **values):
        got = self.tick("ready_state", **values)
        return None if got is None else got["ready_ids"]

    def ran(self):
        return self.log.read_text().split() if self.log.exists() else []

    def test_in_a_proof_the_candidate_watchdog_reads_the_watched_board(self):
        self.assertEqual(self.ready_ids(BROTHER_CODE_ROOT=str(self.candidate), BROTHER_PROOF_PHASE="RB"),
                         ["candidate", "R1"])

    def test_a_code_root_outside_a_proof_is_still_the_candidate(self):
        self.assertEqual(self.ready_ids(BROTHER_CODE_ROOT=str(self.candidate)), ["candidate", "R1"])

    def test_a_proof_without_a_code_root_is_no_data_never_the_landing_copy(self):
        self.assertIsNone(self.ready_ids(BROTHER_PROOF_PHASE="RB"))

    def test_outside_a_proof_the_watched_checkout_runs_as_before(self):
        self.assertEqual(self.ready_ids(), ["landing", "R1"])

    def test_a_copy_already_imported_from_elsewhere_is_no_data(self):
        got = self.tick("ready_state", pre_import=True, BROTHER_CODE_ROOT=str(self.candidate), BROTHER_PROOF_PHASE="RB")
        self.assertIsNone(got)

    def test_the_open_task_count_comes_from_the_candidate(self):
        self.assertEqual(self.tick("open_task_count", BROTHER_CODE_ROOT=str(self.candidate), BROTHER_PROOF_PHASE="RB"), 3)

    def test_in_a_proof_the_watchdog_script_is_no_data_and_neither_copy_runs(self):
        # Its script reads only the tree beside its own file and takes no root: the landing copy is unfrozen code and
        # the candidate's copy would report on the candidate's own empty tree.
        got = self.tick("run_watchdog", BROTHER_CODE_ROOT=str(self.candidate), BROTHER_PROOF_PHASE="RB")
        self.assertEqual((got, self.ran()), ({"exit": None, "headline": None}, []))

    def test_outside_a_proof_the_watchdog_script_runs_as_before(self):
        self.assertEqual((self.tick("run_watchdog"), self.ran()), ({"exit": 0, "headline": "landing"}, ["landing"]))


class FanoutRouterIsTheCandidates(unittest.TestCase):
    """or_fanout._router() APPENDED scripts/loop, then ~/.claude/bin, to sys.path and imported model_router, so in a
    proof any earlier entry holding a model_router.py won (a PYTHONPATH naming the landing tree's scripts/loop loaded
    the landing copy) and a candidate missing its router fell through to the live bin (measured 2026-09-27 in a
    candidate staged as the deploy stages it, both interpreters). The router decides which models the paid fan out
    accepts. One staged candidate for the class; each case asks a fresh interpreter which file it loaded."""

    @classmethod
    def setUpClass(cls):
        import shutil
        cls.tmp = tempfile.TemporaryDirectory(prefix="fanout-router-")
        cls.box = Path(os.path.realpath(cls.tmp.name))
        try:
            home = cls.box / "home"
            (home / ".claude/hooks").mkdir(parents=True)
            (home / ".claude/hooks/bm_session_cap.py").write_text("import json\n")
            cls.bin = home / ".claude/bin"
            cls.candidate = cls.bin / "candidate"
            import deploy_stamped as D
            with mock.patch.dict(os.environ, {"HOME": str(home)}):
                D.stage_candidate(str(LOOP.parents[1]), str(cls.candidate))
            cls.landing = cls.box / "landing/scripts/loop"
            for d in (cls.landing, cls.bin):
                d.mkdir(parents=True, exist_ok=True)
                shutil.copy2(str(LOOP / "model_router.py"), str(d / "model_router.py"))
            cls.home = home
        except BaseException:
            cls.tmp.cleanup()
            raise

    @classmethod
    def tearDownClass(cls):
        cls.tmp.cleanup()

    def loaded(self, pre_import=None, cwd=None, **values):
        env = {k: v for k, v in os.environ.items() if k not in KEYS and not k.startswith("PYTHON")}
        env.update(HOME=str(self.home), **values)
        code = "import sys\n"
        if pre_import:
            code += "sys.path.insert(0, %r)\nimport model_router\nsys.path.pop(0)\n" % str(pre_import)
        code += ("sys.path.insert(0, %r)\nfrom plugin.runtime.brother.core import or_fanout as O\nbefore = list(sys.path)\n"
                 "R = O._router()\nprint('PATH', 'kept' if sys.path == before else 'changed')\n"
                 "print(R.__file__ if R else None)\n" % str(self.candidate))
        p = subprocess.run([sys.executable, "-B", "-c", code], env=env, cwd=str(cwd or self.candidate),
                           capture_output=True, text=True, timeout=120)
        self.assertEqual(p.returncode, 0, p.stdout + p.stderr)
        path_line, line = p.stdout.strip().splitlines()[-2:]
        # The router's directory is on sys.path for its one import only: left in front, it would shadow the scripts/
        # copies of burn_guard, grade_build, probe_brief and run_window for every later import in the fan out.
        self.assertEqual(path_line, "PATH kept")
        return None if line == "None" else os.path.realpath(line)

    def proof(self):
        return dict(BROTHER_CODE_ROOT=str(self.candidate), BROTHER_PROOF_PHASE="RB")

    def want(self):
        return str(self.candidate / "scripts/loop/model_router.py")

    def test_in_a_proof_the_candidates_router_loads(self):
        self.assertEqual(self.loaded(**self.proof()), self.want())

    def test_an_earlier_path_entry_holding_a_router_never_wins(self):
        self.assertEqual(self.loaded(PYTHONPATH=str(self.landing), **self.proof()), self.want())

    def test_a_candidate_missing_its_router_is_none_never_the_live_bin(self):
        router = self.candidate / "scripts/loop/model_router.py"
        router.rename(self.box / "router.parked")
        try:
            got = self.loaded(**self.proof())
        finally:
            (self.box / "router.parked").rename(router)
        self.assertIsNone(got)

    def test_a_code_root_outside_a_proof_still_refuses_the_live_bin(self):
        router = self.candidate / "scripts/loop/model_router.py"
        router.rename(self.box / "router.parked")
        try:
            got = self.loaded(BROTHER_CODE_ROOT=str(self.candidate))
        finally:
            (self.box / "router.parked").rename(router)
        self.assertIsNone(got)

    def test_a_router_already_imported_from_elsewhere_is_none_in_a_proof(self):
        self.assertIsNone(self.loaded(pre_import=self.landing, **self.proof()))

    def test_a_proof_without_a_code_root_is_none(self):
        self.assertIsNone(self.loaded(BROTHER_PROOF_PHASE="RB"))

    def test_outside_a_proof_the_router_beside_the_fanout_loads_whatever_the_cwd_checkout(self):
        # The cwd is a checkout holding its own router: outside a proof the fan out's own tree still decides.
        self.assertEqual(self.loaded(cwd=LOOP.parents[1]), self.want())

    def test_a_router_already_imported_from_elsewhere_is_none_outside_a_proof_too(self):
        self.assertIsNone(self.loaded(pre_import=self.landing))


class NativeDispatchLoadsTheCandidatesModelCall(unittest.TestCase):
    """X3 finding 1 (Codex cross lane review, 2026-09-27, blocked RB): pinning the router removed the candidate's
    scripts/loop from sys.path, and the native (Claude, Codex) dispatch still did a bare `import model_call`, so in a
    staged candidate every native job failed with ModuleNotFoundError before any model started. model_call now comes
    from the directory the router was pinned to, loaded by its file, and any other copy refuses. Each case runs one
    native job at the entry point run_job() in a fresh interpreter, inside a candidate staged as the deploy stages it,
    against a stub `claude` (BROTHER_CLAUDE_BIN) that records it ran and answers; a stray model_call.py records its
    own import, so "never loaded" is read from disk, not inferred."""

    @classmethod
    def setUpClass(cls):
        import shutil
        cls.tmp = tempfile.TemporaryDirectory(prefix="native-model-call-")
        cls.box = Path(os.path.realpath(cls.tmp.name))
        try:
            cls.home = cls.box / "home"
            (cls.home / ".claude/hooks").mkdir(parents=True)
            (cls.home / ".claude/hooks/bm_session_cap.py").write_text("import json\n")
            cls.candidate = cls.home / ".claude/bin/candidate"
            import deploy_stamped as D
            with mock.patch.dict(os.environ, {"HOME": str(cls.home)}):
                D.stage_candidate(str(LOOP.parents[1]), str(cls.candidate))
            cls.stray = cls.box / "stray"
            cls.stray.mkdir()
            (cls.stray / "model_call.py").write_text(
                "import os, types\nopen(os.environ['X3_MARKS'], 'a').write('STRAY model_call imported\\n')\n"
                "def call_one(*a, **k):\n    return types.SimpleNamespace(ok=True, answer='STRAY ANSWER', detail='stray', seconds=0)\n")
            cls.stub = cls.box / "stub-claude"
            cls.stub.write_text(
                "#!%s\nimport os, sys\nopen(os.environ['X3_MARKS'], 'a').write('MODEL %%s\\n' %% ' '.join(sys.argv[1:4]))\n"
                "sys.stdin.read()\nprint('{\"type\": \"result\", \"result\": \"STUB ANSWER\", \"is_error\": false, "
                "\"total_cost_usd\": 0}')\n" % sys.executable)
            cls.stub.chmod(0o755)
            # a proof run the Claude call ledger admits: its start record and its launch record with a far deadline
            cls.run_dir = cls.box / "runs" / "RB-x3"
            (cls.run_dir / "proof").mkdir(parents=True)
            (cls.run_dir / "proof/start.json").write_text(json.dumps(
                {"schema": "loop-proof-start-v1", "run_id": "RB-x3", "attempt_id": "a" * 32, "phase": "RB"}))
            launch = cls.box / "runs" / ".proof-launches" / "RB-x3"
            launch.mkdir(parents=True)
            import time
            (launch / "launch.json").write_text(json.dumps({"schema": "loop-proof-launch-v1",
                                                            "run_dir": str(cls.run_dir), "deadline_epoch": time.time() + 86400}))
            cls.shutil = shutil
        except BaseException:
            cls.tmp.cleanup()
            raise

    @classmethod
    def tearDownClass(cls):
        cls.tmp.cleanup()

    def setUp(self):
        self.marks = self.box / ("marks-%s" % self._testMethodName)
        self.out = self.box / ("out-%s" % self._testMethodName) / "answer.txt"

    def job(self, pre_import=False, **values):
        """(record, model_call file or None, lines of the marks file) after one native job."""
        env = {k: v for k, v in os.environ.items() if not k.startswith(("BROTHER_", "PYTHON"))}
        # A TRACKED FIXTURE, never the checkout's own docs/plan/model-registry.json: that file is
        # not on the export allowlist and is never staged into a candidate (stage_candidate follows
        # the python import closure only, never a data file), so it exists on the author's machine
        # and nowhere the hermetic check or a staged candidate can see it. The fixture ships with
        # every export and lists 'sonnet' with the same shape a real registry row needs.
        env.update(HOME=str(self.home),
                   BROTHER_MODEL_REGISTRY=str(LOOP.parent / "fixtures/model-registry-fixture.json"),
                   BROTHER_CLAUDE_BIN=str(self.stub), X3_MARKS=str(self.marks),
                   BROTHER_WORKSPACE_ROOT=str(self.box), **values)
        code = "import json, sys\n"
        if pre_import:
            code += "sys.path.insert(0, %r)\nimport model_call\nsys.path.pop(0)\n" % str(self.stray)
        code += ("sys.path.insert(0, %r)\nfrom plugin.runtime.brother.core import or_fanout as F\n"
                 "r = F.run_job({'id': 'X-r0', 'model': 'sonnet', 'prompt': 'offline fixture', 'out': %r, "
                 "'sensitivity': 'public'}, 300, 1)\n"
                 "m = sys.modules.get('model_call')\n"
                 "print(json.dumps({'ok': r.get('ok'), 'error': r.get('error'), 'file': getattr(m, '__file__', None)}))\n"
                 % (str(self.candidate), str(self.out)))
        p = subprocess.run([sys.executable, "-B", "-c", code], env=env, cwd=str(self.candidate),
                           capture_output=True, text=True, timeout=120)
        self.assertEqual(p.returncode, 0, p.stdout + p.stderr)
        got = json.loads(p.stdout.strip().splitlines()[-1])
        marks = self.marks.read_text().splitlines() if self.marks.exists() else []
        return got, (os.path.realpath(got["file"]) if got["file"] else None), marks

    def proof(self):
        return dict(BROTHER_CODE_ROOT=str(self.candidate), BROTHER_PROOF_PHASE="RB", BROTHER_RUN_DIR=str(self.run_dir))

    def want(self):
        return str(self.candidate / "scripts/loop/model_call.py")

    def reached_the_model(self, got, loaded, marks):
        self.assertEqual((got["ok"], got["error"]), (True, None), got)
        self.assertEqual(loaded, self.want())
        self.assertEqual(marks, ["MODEL -p --model claude-sonnet-5"])
        self.assertEqual(self.out.read_text().strip(), "STUB ANSWER")

    def test_in_a_proof_the_native_job_reaches_the_model_through_the_candidates_model_call(self):
        self.reached_the_model(*self.job(**self.proof()))

    def test_a_stray_model_call_earlier_on_the_path_is_never_loaded_in_a_proof(self):
        self.reached_the_model(*self.job(PYTHONPATH=str(self.stray), **self.proof()))

    def test_a_stray_model_call_already_imported_refuses_and_no_model_runs(self):
        got, loaded, marks = self.job(pre_import=True, **self.proof())
        self.assertIs(got["ok"], False)
        self.assertIn("model_call", got["error"])
        self.assertEqual(marks, ["STRAY model_call imported"])     # the pre import itself, and nothing after it
        self.assertFalse(self.out.exists())

    def test_a_candidate_missing_its_model_call_refuses_and_never_loads_a_stray(self):
        mc = self.candidate / "scripts/loop/model_call.py"
        mc.rename(self.box / "model_call.parked")
        try:
            got, loaded, marks = self.job(PYTHONPATH=str(self.stray), **self.proof())
        finally:
            (self.box / "model_call.parked").rename(mc)
        self.assertIs(got["ok"], False)
        self.assertIn("model_call", got["error"])
        self.assertEqual((loaded, marks), (None, []))
        self.assertFalse(self.out.exists())

    def test_a_code_root_outside_a_proof_reaches_the_model_through_the_candidates_model_call(self):
        self.reached_the_model(*self.job(BROTHER_CODE_ROOT=str(self.candidate)))

    def test_control_outside_any_code_root_the_model_call_beside_the_fanout_answers(self):
        self.reached_the_model(*self.job())


if __name__ == "__main__":
    unittest.main()

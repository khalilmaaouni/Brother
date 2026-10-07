#!/usr/bin/env python3
"""F47 deploy canary: the candidate runtime stages, deploys, freezes, refuses drift and rolls back.

WHY. The driver runs ~/.claude/bin/burn_guard.py, and on 2026-09-26 that file was byte different from
scripts/loop/burn_guard.py: nothing proved that a deploy of the candidate puts the loop sources into the
executed directory, that the frozen closure of that directory passes, or that a rollback returns the
previous deployment byte for byte. This suite proves it offline, before any proof run starts.

HOW. Every step runs the REAL entry points as subprocesses: scripts/loop/deploy_stamped.sh for deploy and
its printed RESTORE command for rollback, and the DEPLOYED copy of freeze_manifest.py for write and verify.
Each class builds its own scratch run under ~/.claude/brother-scratch/run-f47-*, with an empty HOME and
TMPDIR inside it, removed afterwards. No PYTHONPATH points at this repository, the process table is a
fixture (stop_loop.sh reads a fake ps), and the real ~/.claude is never read or written.

ONE SEAM, named, never hidden. The staged loop canary (loop_canary.py replays landed builds through the
grader, the landing tool and the spec gate: minutes per fixture, and it needs the build evidence of a real
HOME) is answered for the positive deploys by a sitecustomize stub that says WOULD LAND and logs the path
it was asked to run. test_a_staged_canary_gate_is_live runs the real canary unstubbed: in an empty HOME it
answers NO-DATA and the deploy refuses with the target unchanged. This file therefore does not prove the
judging path; loop_canary.py does.

MACHINE STATE, named. The cap grant, the intake, the launch settings and the session cap hook that
brother_night_tick.py loads by path (~/.claude/hooks/bm_session_cap.py) are not loop deploy artifacts.
They are seeded as scratch fixtures and the freeze pins their bytes like any configuration.

Run: python3 -B scripts/test_deploy_canary.py -v     (TIMING lines go to stderr)
"""
import hashlib
import json
import os
from pathlib import Path
import re
import shlex
import shutil
import subprocess
import sys
import tempfile
import time
import unittest

ROOT = Path(__file__).resolve().parents[1]
LOOP = ROOT / "scripts/loop"
DEPLOY = LOOP / "deploy_stamped.sh"
DRIVER = LOOP / "loop_until.sh"
CONFIGS = ("model-registry.json", "loop-roles.json", "loop-canary.json")
STAMP = ".deploy-stamp.json"
# The repository carries .brother-edition (tracked); the export omits it (precedent: test_attacker_role_limit).
# The deploy refuses without the loop's registry and roles configs, which the public export does not ship yet,
# so on that tree there is no loop deploy to canary. In the repository a missing config is a defect: the suite
# runs and fails. loop-canary.json is optional to the deploy itself.
IN_REPOSITORY = (ROOT / ".brother-edition").is_file()
UNSHIPPED = [n for n in CONFIGS[:2] if not (ROOT / "docs/plan" / n).is_file()]


def setUpModule():
    if UNSHIPPED and not IN_REPOSITORY:
        raise unittest.SkipTest("not shipped in this tree: docs/plan/%s; the loop deploy canary runs in the repository"
                                % ", docs/plan/".join(UNSHIPPED))
# The freeze command the proof procedure runs, minus the output path. The module roots are the candidate the
# deploy staged into bin (U3): bin/candidate (plugin.runtime...) and bin/candidate/scripts (brother_night_tick.py
# imports task_watchdog from there), never the launch worktree the loop lands into, plus the hooks root. duckdb is
# the optional rollups capability (unit_ledger imports it lazily), and land_apply.py's main imports a build's new
# modules by a computed name; the candidate's own copy of it (native_worker reaches land_apply, 2026-10-05) carries
# its relocated declaration from deploy_stamped.candidate_work_inputs, the list proof_pair.sh passes.
def freeze_flags(s):
    candidate = s.bin / "candidate"
    sys.path.insert(0, str(LOOP))
    import deploy_stamped  # noqa: E402, the one list of relocated declarations
    return ["--module-root", str(candidate), "--module-root", str(candidate / "scripts"),
            "--module-root", str(s.home / ".claude/hooks"),
            "--optional-import", "duckdb=rollups",
            "--work-input", "land_apply.py:main=build-receipt-covers-new-modules"] + [
            "--work-input=" + w for w in deploy_stamped.candidate_work_inputs(candidate)]
ROLLUPS_NO_DATA = ("NO-DATA optional capability rollups unavailable: "
                   "duckdb is absent under the frozen interpreter")
STALE_BURN_GUARD = b"# previous deployment: a burn guard the loop sources no longer hold\n"
OWNER_TOOL = b"# a bin only tool the loop does not own; every deploy must keep it\n"
# Built, never written as one quoted literal: the parity gate reads every quoted *.py name in scripts/ as a
# reference, and a referenced bin only tool is exactly what it refuses.
OWNER_TOOL_NAME = "owner_local_tool" + os.extsep + "py"
HOOK = b'"""Scratch stand in for the machine installed session cap hook."""\nimport json\n'
CLI = b"#!/bin/sh\necho %s scratch stand in\n"
STUB = '''import os, subprocess
_real = subprocess.run
def _run(argv, *a, **kw):
    log = os.environ.get("F47_CANARY_STUB_LOG")
    if log and isinstance(argv, (list, tuple)) and argv and str(argv[-1]).endswith("/loop_canary.py"):
        with open(log, "a") as fh:
            fh.write("%s\\t%s\\n" % (argv[-1], kw.get("cwd")))
        return subprocess.CompletedProcess(argv, 0, "CANARY WOULD LAND: F47 stub, judging path not replayed\\n", "")
    return _real(argv, *a, **kw)
subprocess.run = _run
'''
PS = ('#!/bin/sh\ncase "$*" in\n'
      '  *pgid=,command=*) printf "1 0 1 /sbin/launchd\\n"; exit 0;;\n'
      '  *) exit 1;;\nesac\n')


def sha(data):
    return hashlib.sha256(data).hexdigest()


def timing(label, seconds):
    sys.stderr.write("TIMING %-28s %6.2fs  (%s)\n" % (label, seconds, sys.executable))


def shipped_sources():
    """What a deploy must publish, restated from D-21 rather than imported from the code under test:
    every regular file of scripts/loop except caches, backups and test_*.py files."""
    return sorted(p.name for p in LOOP.iterdir()
                  if p.is_file() and not p.name.endswith(".pyc") and ".bak" not in p.name
                  and not (p.name.startswith("test_") and p.name.endswith(".py")))


def mismatches(deployed):
    """Loop sources whose deployed bytes are absent or different. deployed maps name to bytes."""
    return [n for n in shipped_sources() if deployed.get(n) != (LOOP / n).read_bytes()]


def driver_tools():
    """Every ~/.claude/bin/<tool> the driver calls, read from the driver itself."""
    return sorted(set(re.findall(r"~/\.claude/bin/([A-Za-z0-9_.-]+)", DRIVER.read_text(encoding="utf-8"))))


class Scratch:
    """One scratch run: an empty HOME holding a previous deployment and the machine state fixtures."""

    def __init__(self):
        root = Path.home() / ".claude/brother-scratch"
        root.mkdir(parents=True, exist_ok=True)
        self.box = Path(tempfile.mkdtemp(prefix="run-f47-", dir=str(root)))
        self.home = self.box / "home"
        self.tmp = self.box / "tmp"
        self.fakebin = self.box / "fakebin"
        self.inject = self.box / "inject"
        self.bin = self.home / ".claude/bin"
        for p in (self.bin, self.tmp, self.fakebin, self.inject):
            p.mkdir(parents=True)
        (self.fakebin / "ps").write_text(PS)
        (self.fakebin / "ps").chmod(0o755)
        (self.inject / "sitecustomize.py").write_text(STUB)
        self.stub_log = self.box / "canary-stub.log"
        # The previous deployment: a plain directory whose burn guard is not the loop source (the F47
        # state), a tool the loop does not own, and a configuration beside it.
        (self.bin / "burn_guard.py").write_bytes(STALE_BURN_GUARD)
        (self.bin / OWNER_TOOL_NAME).write_bytes(OWNER_TOOL)
        for name in CONFIGS:
            (self.bin.parent / name).write_text(json.dumps({"previous": name}) + "\n")
        state = self.home / ".claude/brother-or-dispatch-state"
        intake = self.home / ".claude/evidence/loop-intake"
        hooks = self.home / ".claude/hooks"
        for p in (state, intake, hooks):
            p.mkdir(parents=True)
        (state / "cap-grant.json").write_text('{"fixture": "scratch cap grant"}\n')
        (intake / "CURRENT.json").write_text('{"fixture": "scratch intake"}\n')
        (intake / "launch-env.sh").write_text("# scratch launch settings\n")
        (hooks / "bm_session_cap.py").write_bytes(HOOK)
        # The Claude and Codex executables the deployed model_router selects are frozen by their bytes (Codex audit
        # D1): scratch stand ins, named the way a launch names them.
        self.cli = {}
        for name in ("claude", "codex"):
            p = self.box / "cli" / name
            p.parent.mkdir(exist_ok=True)
            p.write_bytes(CLI % name.encode())
            p.chmod(0o755)
            self.cli[name] = p

    def env(self, home=None, stub=False):
        env = {k: v for k, v in os.environ.items()
               if not k.startswith(("BROTHER_", "PYTHON", "GIT_"))
               and k not in ("STOP_LOOP_ONLY", "F47_CANARY_STUB_LOG")}
        env.update(HOME=str(home or self.home), TMPDIR=str(self.tmp), PYTHONDONTWRITEBYTECODE="1",
                   GIT_OPTIONAL_LOCKS="0", BROTHER_DEPLOY_PYTHON=sys.executable,
                   PATH=str(self.fakebin) + os.pathsep + os.environ.get("PATH", ""))
        if stub:
            env.update(PYTHONPATH=str(self.inject), F47_CANARY_STUB_LOG=str(self.stub_log))
        return env

    def run(self, argv, env, label=None):
        start = time.monotonic()
        result = subprocess.run([str(a) for a in argv], env=env, cwd=str(self.box),
                                capture_output=True, text=True, timeout=600)
        if label:
            timing(label, time.monotonic() - start)
        return result

    def deploy(self, stub=True, label=None):
        return self.run(["bash", DEPLOY], self.env(stub=stub), label)

    def restore(self, deploy_result, label=None):
        line = next(l for l in deploy_result.stdout.splitlines() if l.startswith("RESTORE "))
        return self.run(shlex.split(line[len("RESTORE "):]), self.env(), label)

    def freeze(self, verb, manifest, *flags, home=None, label=None):
        """The freeze as a proof launch runs it: the candidate as the code root, the model executables named."""
        tool = self.bin / "freeze_manifest.py"
        env = self.env(home=home)
        env.update(BROTHER_CODE_ROOT=str(self.bin / "candidate"), BROTHER_CLAUDE_BIN=str(self.cli["claude"]),
                   BROTHER_CODEX_BIN=str(self.cli["codex"]))
        return self.run([sys.executable, "-B", tool, verb, manifest] + list(flags), env, label)

    def deployed_files(self):
        real = Path(os.path.realpath(str(self.bin)))
        return {p.name: p.read_bytes() for p in real.iterdir() if p.is_file()}

    def tree(self):
        """Every byte a deploy or restore may change: the executed directory, followed through its link,
        and the configurations beside it, plus whether the directory is a link."""
        real = Path(os.path.realpath(str(self.bin)))
        out = {"bin/" + str(p.relative_to(real)): sha(p.read_bytes())
               for p in sorted(real.rglob("*")) if p.is_file()}
        out.update({n: sha((self.bin.parent / n).read_bytes())
                    for n in CONFIGS if (self.bin.parent / n).exists()})
        out["kind"] = "symlink" if self.bin.is_symlink() else "directory"
        return out

    def remove(self):
        shutil.rmtree(str(self.box), ignore_errors=True)


def refusal(result):
    """The lines that say why a deploy refused, without its long binding table."""
    return "\n".join(l for l in (result.stdout + result.stderr).splitlines()
                     if l.startswith(("FAIL", "REFUSED", "NO-DATA", "CANARY")) or "refused" in l)


class DeployCanary(unittest.TestCase):
    """One scratch run: the real staged canary refuses, the stubbed deploy publishes, the freeze holds."""

    @classmethod
    def setUpClass(cls):
        cls.s = Scratch()
        try:
            cls.scenario(cls.s)
        except BaseException:
            cls.s.remove()     # tearDownClass never runs after a failed setUpClass
            raise

    @classmethod
    def scenario(cls, s):
        cls.before = s.tree()
        cls.previous = s.deployed_files()
        cls.unstubbed = s.deploy(stub=False, label="deploy, real canary")
        cls.after_unstubbed = s.tree()
        cls.deployed = s.deploy(stub=True, label="deploy (stage+stub+publish)")
        cls.manifest = s.box / "freeze.json"
        cls.written = None
        if cls.deployed.returncode == 0:
            cls.written = s.freeze("write", cls.manifest, *freeze_flags(s), label="freeze write")

    @classmethod
    def tearDownClass(cls):
        cls.s.remove()

    def require_deploy(self):
        self.assertEqual(self.deployed.returncode, 0,
                         "the candidate deploy refused:\n" + refusal(self.deployed))

    def require_freeze(self):
        self.require_deploy()
        self.assertEqual(self.written.returncode, 0, self.written.stdout + self.written.stderr)

    def verify(self, home=None, label=None, *flags):
        return self.s.freeze("verify", self.manifest, *flags, home=home, label=label)

    def failures(self, result):
        return [l for l in result.stdout.splitlines() if l.startswith(("FAIL", "NO-DATA"))
                and not l.startswith("NO-DATA optional capability")]

    def test_a_staged_canary_gate_is_live(self):
        out = self.unstubbed.stdout + self.unstubbed.stderr
        self.assertNotEqual(self.unstubbed.returncode, 0, out)
        self.assertIn("CANARY NO-DATA", out)
        self.assertIn("staged canary refused; target unchanged", out)
        self.assertEqual(self.after_unstubbed, self.before)

    def test_b_deploy_publishes_exactly_the_loop_sources(self):
        # The check can fail: on the previous deployment it names the stale burn guard (the F47 state).
        self.assertIn("burn_guard.py", mismatches(self.previous))
        self.require_deploy()
        self.assertIn("PASS: deployed", self.deployed.stdout)
        self.assertTrue(self.s.bin.is_symlink())
        self.assertEqual(mismatches(self.s.deployed_files()), [])
        self.assertEqual(self.s.deployed_files()[OWNER_TOOL_NAME], OWNER_TOOL)
        # The canary really ran FROM the staged tree, once, with the checkout as its working directory.
        rows = [l.split("\t") for l in self.s.stub_log.read_text().splitlines()]
        stage = Path(os.path.realpath(str(self.s.bin)))
        self.assertEqual(rows, [[str(stage / "loop_canary.py"), str(ROOT)]])

    def test_c_every_tool_the_driver_calls_is_its_loop_source(self):
        self.require_deploy()
        tools = driver_tools()
        self.assertIn("burn_guard.py", tools)
        for name in tools:
            with self.subTest(tool=name):
                called = self.s.home / ".claude/bin" / name     # ~/.claude/bin/<name> under this HOME
                self.assertTrue(called.is_file(), "the driver calls %s and the deploy has none" % called)
                self.assertEqual(sha(called.read_bytes()), sha((LOOP / name).read_bytes()))

    def test_d_freeze_write_passes_and_rollups_stay_no_data(self):
        self.require_freeze()
        lines = self.written.stdout.splitlines()
        self.assertEqual(lines[-1], "PASS manifest %s" % self.manifest)
        self.assertIn(ROLLUPS_NO_DATA, lines)
        self.assertFalse([l for l in lines if "rollups" in l and not l.startswith("NO-DATA")])
        m = json.loads(self.manifest.read_text())
        self.assertNotIn("duckdb", m["imports"], "duckdb is present under this interpreter: D-23 requires the "
                         "N2 capability check before rollups may be frozen as available")
        self.assertIn("duckdb", m["absent"])
        self.assertEqual(m["optional_imports"], ["duckdb=rollups"])
        self.assertEqual(m["work_inputs"], ["candidate/scripts/loop/land_apply.py:main=build-receipt-covers-new-modules",
                                            "land_apply.py:main=build-receipt-covers-new-modules"])
        self.assertEqual(m["home"], str(self.s.home))
        # The burn guard contract: the path the driver calls, the loop source's hash, a staged file.
        record = m["files"][str(self.s.home / ".claude/bin/burn_guard.py")]
        self.assertEqual(record["sha256"], sha((LOOP / "burn_guard.py").read_bytes()))
        self.assertTrue(record["resolved"].startswith(str(self.s.home / ".claude/brother-deploys") + os.sep))
        hook = str(self.s.home / ".claude/hooks/bm_session_cap.py")
        self.assertIn(hook, [site["target"] for site in m["resolved_loaders"]])
        self.assertEqual(m["files"][hook]["sha256"], sha(HOOK))

    def test_e_each_freeze_declaration_is_load_bearing(self):
        self.require_freeze()
        cases = {"--optional-import": "unresolved import duckdb in ",
                 "--work-input": "computed import has no frozen resolution in "}
        for flag, why in cases.items():
            with self.subTest(dropped=flag):
                flags = freeze_flags(self.s)
                i = flags.index(flag)
                out = self.s.box / ("without%s.json" % flag)
                r = self.s.freeze("write", out, *(flags[:i] + flags[i + 2:]))
                self.assertEqual(r.returncode, 2, r.stdout + r.stderr)
                self.assertTrue(r.stdout.startswith("NO-DATA: " + why), r.stdout)
                self.assertFalse(out.exists())

    def test_f_verify_passes_and_writes_its_receipt(self):
        self.require_freeze()
        receipt = self.s.box / "freeze-start.json"
        r = self.verify(None, "freeze verify", "--receipt", receipt, "--run-id", "f47", "--phase", "start")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertEqual(r.stdout.splitlines()[-1], "PASS frozen runtime unchanged")
        self.assertIn(ROLLUPS_NO_DATA, r.stdout.splitlines())
        row = json.loads(receipt.read_text())
        self.assertEqual((row["verdict"], row["phase"], row["run_id"]), ("PASS", "start", "f47"))
        self.assertEqual(row["manifest_sha256"], sha(self.manifest.read_bytes()))
        self.assertEqual(row["checked_files"], len(json.loads(self.manifest.read_text())["files"]))

    def test_g_one_byte_drift_in_the_driver_burn_guard_refuses_and_restores(self):
        self.require_freeze()
        called = self.s.home / ".claude/bin/burn_guard.py"
        original = called.read_bytes()
        first = original.split(b"\n", 1)[0]
        self.assertTrue(first.startswith(b"#!") and b"python3" in first, "no shebang byte to flip")
        i = first.index(b"python3")          # a comment byte: bytes change, imports do not
        tree = self.s.tree()
        called.write_bytes(original[:i] + b"P" + original[i + 1:])
        try:
            r = self.verify()
        finally:
            called.write_bytes(original)
        self.assertEqual(r.returncode, 1, r.stdout)
        self.assertEqual(self.failures(r), ["FAIL changed: %s" % called])
        self.assertEqual(self.s.tree(), tree)
        self.assertEqual(self.verify().returncode, 0)

    def test_h_a_changed_home_at_verify_is_drift(self):
        self.require_freeze()
        # Isolated: the same directory spelled with a trailing slash moves no path, only the pinned home.
        r = self.verify(str(self.s.home) + os.sep)
        self.assertEqual((r.returncode, self.failures(r)), (1, ["FAIL drift in home"]), r.stdout)
        # Realistic: another HOME holding identical machine state is still not the frozen runtime's HOME.
        moved = self.s.box / "home-moved"
        shutil.copytree(str(self.s.home / ".claude/hooks"), str(moved / ".claude/hooks"))
        r = self.verify(moved)
        self.assertEqual(r.returncode, 1, r.stdout)
        self.assertIn("FAIL drift in home", self.failures(r))


    def test_i_the_candidate_the_loop_runs_outside_bin_is_frozen(self):
        # F-6 (B5-04): a script the pass runs by path is inside the frozen runtime, so one byte is a FAIL.
        self.require_freeze()
        staged = self.s.bin / "candidate/scripts/probe_round.py"
        self.assertTrue(staged.is_file(), "the deploy staged no candidate: %s" % staged)
        original = staged.read_bytes()
        staged.write_bytes(original + b"#")
        try:
            r = self.verify()
        finally:
            staged.write_bytes(original)
        self.assertEqual(r.returncode, 1, r.stdout)
        self.assertEqual(self.failures(r), ["FAIL changed: %s" % staged])
        self.assertEqual(self.verify().returncode, 0)

    def test_j_a_model_executable_replaced_at_its_path_refuses(self):
        # Codex audit D1: the deployed router selects these files and the loop runs them by path, so new bytes at
        # an unchanged path are drift, whichever of the two it is.
        self.require_freeze()
        m = json.loads(self.manifest.read_text())
        self.assertEqual(m["model_executables"], {n: str(p) for n, p in self.s.cli.items()})
        for name, path in sorted(self.s.cli.items()):
            with self.subTest(executable=name):
                original = path.read_bytes()
                path.write_bytes(original.replace(b"stand in", b"stand in, replaced"))
                try:
                    r = self.verify()
                finally:
                    path.write_bytes(original)
                self.assertEqual(r.returncode, 1, r.stdout)
                self.assertEqual(self.failures(r), ["FAIL changed: %s" % path])
        self.assertEqual(self.verify().returncode, 0)


class Rollback(unittest.TestCase):
    """Two deploys, then each printed RESTORE command in turn: every step back is byte identical."""

    @classmethod
    def setUpClass(cls):
        cls.s = Scratch()
        try:
            cls.scenario(cls.s)
        except BaseException:
            cls.s.remove()
            raise

    @classmethod
    def scenario(cls, s):
        cls.s0 = s.tree()
        cls.first = s.deploy(label="deploy 1")
        cls.s1 = s.tree() if cls.first.returncode == 0 else None
        cls.second = s.deploy(label="deploy 2") if cls.s1 else None
        cls.s2 = s.tree() if cls.second and cls.second.returncode == 0 else None
        cls.back1 = s.restore(cls.second, label="restore to deploy 1") if cls.s2 else None
        cls.r1 = s.tree() if cls.back1 else None
        cls.back0 = s.restore(cls.first, label="restore to pre-deploy") if cls.back1 else None
        cls.r0 = s.tree() if cls.back0 else None

    @classmethod
    def tearDownClass(cls):
        cls.s.remove()

    def test_a_restore_returns_the_previous_deployment_byte_identical(self):
        self.assertEqual(self.first.returncode, 0, "deploy 1 refused:\n" + refusal(self.first))
        self.assertEqual(self.second.returncode, 0, "deploy 2 refused:\n" + refusal(self.second))
        self.assertNotEqual(self.s2, self.s1)      # each stamp names its own rollback, so there is a step back
        self.assertEqual(self.back1.returncode, 0, self.back1.stdout + self.back1.stderr)
        self.assertEqual(self.r1, self.s1)
        self.assertEqual(self.r1["kind"], "symlink")

    def test_b_restore_returns_the_pre_deploy_tree_byte_identical(self):
        self.assertIsNotNone(self.back0, "an earlier step refused; see test_a")
        self.assertEqual(self.back0.returncode, 0, self.back0.stdout + self.back0.stderr)
        self.assertEqual(self.r0, self.s0)
        self.assertEqual(self.r0["kind"], "directory")
        self.assertEqual((self.s.bin / "burn_guard.py").read_bytes(), STALE_BURN_GUARD)


if __name__ == "__main__":
    unittest.main()

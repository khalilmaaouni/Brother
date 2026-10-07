#!/usr/bin/env python3
"""Frozen deploys have recoverable, hashed snapshots and publish only complete tools.

All commands use a fixture source, target and initially empty HOME. The real
stop_loop.sh reads a fake ps listing: no loop or model process is started.
"""
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest
import shlex


ROOT = Path(__file__).resolve().parents[1]
DEPLOY = ROOT / "scripts/loop/deploy_stamped.sh"
CONFIGS = ("model-registry.json", "loop-roles.json", "loop-canary.json")


def digest(data):
    return hashlib.sha256(data).hexdigest()


def files(path):
    return {str(p.relative_to(path)): p.read_bytes()
            for p in path.rglob("*") if p.is_file()}


class DeployStamped(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="deploy fixture ")
        self.addCleanup(self.tmp.cleanup)
        self.box = Path(self.tmp.name)
        self.home = self.box / "empty-home"
        # loop_procs.owned (2026-09-30): a process is the loop's only when its program sits in HOME's ~/.claude/bin or its
        # environment carries a BROTHER_RUN_DIR under the runs root. This fixture's paths hold a space on purpose and ps
        # prints argv and environment space separated (loop_procs' named limit), so only the run marker route can own a
        # fake here: the fake ps answers -E with a marker under a space free runs root (found 2026-10-04: every
        # live-process case read "nothing of the loop is alive" since the ownership rule changed).
        self.runs_root = tempfile.mkdtemp(prefix="deployruns-")
        self.addCleanup(shutil.rmtree, self.runs_root, True)
        self.source = self.box / "source"
        self.loop = self.source / "scripts/loop"
        self.plan = self.source / "docs/plan"
        self.target = self.box / "install/bin"
        self.fakebin = self.box / "fakebin"
        for p in (self.home, self.loop, self.plan, self.target, self.fakebin):
            p.mkdir(parents=True)
        self.assertEqual(list(self.home.iterdir()), [])
        self.env = dict(os.environ, HOME=str(self.home),
                        BROTHER_DEPLOY_SOURCE=str(self.source),
                        BROTHER_DEPLOY_TARGET=str(self.target),
                        BROTHER_DEPLOY_PYTHON=sys.executable, BROTHER_RUNS_ROOT=self.runs_root,
                        PYTHONDONTWRITEBYTECODE="1", PYTHONPATH="",
                        GIT_CONFIG_NOSYSTEM="1", GIT_CONFIG_GLOBAL=os.devnull,
                        PATH=str(self.fakebin) + os.pathsep + os.environ["PATH"])
        for key in ("STOP_LOOP_ONLY", "DEPLOY_FAKE_PROCESS", "DEPLOY_FAIL_COPY",
                    "DEPLOY_COPY_LOG", "DEPLOY_TEST_LOG"):
            self.env.pop(key, None)
        ps = self.fakebin / "ps"
        # A real process table is never empty (launchd is always pid 1), and stop_loop.sh reads ownership through
        # loop_procs.py's four column snapshot (pid, parent, group, command); an empty or unparseable table is
        # unreadable and the stop refuses, correctly. `-p` for a pid that is gone exits 1, as real ps does.
        ps.write_text('#!/bin/sh\ncase "$*" in\n'
                      '  *pgid=,command=*) printf "1 0 1 /sbin/launchd\\n"\n'
                      '    [ -n "${DEPLOY_FAKE_PROCESS:-}" ] && printf "424242 1 424242 %s\\n" "$DEPLOY_FAKE_PROCESS"\n'
                      '    exit 0;;\n'
                      '  *-E*) [ -n "${DEPLOY_FAKE_PROCESS:-}" ] || exit 1\n'
                      '    printf "%s BROTHER_RUN_DIR=%s/run-fake\\n" "$DEPLOY_FAKE_PROCESS" "$BROTHER_RUNS_ROOT"; exit 0;;\n'
                      '  *) [ -n "${DEPLOY_FAKE_PROCESS:-}" ] || exit 1\n'
                      '    printf "%s\\n" "$DEPLOY_FAKE_PROCESS"; exit 0;;\nesac\n')
        ps.chmod(0o755)
        (self.target / "a.py").write_bytes(b"old tool\x00\xff\n")
        (self.target / "bridge.py").write_bytes(b"# retained local bridge\n")
        (self.target / "a.py.bak-old").write_bytes(b"old backup\n")
        (self.target / "__pycache__").mkdir()
        (self.target / "__pycache__/old.pyc").write_bytes(b"stale cache")
        (self.loop / "a.py").write_text("# new tool\n")
        (self.loop / "a.py").chmod(0o755)
        (self.loop / "brief.md").write_text("fixture brief\n")
        (self.loop / "loop_canary.py").write_text(
            "import json, os\nfrom pathlib import Path\n"
            "here = Path(__file__).resolve()\n"
            "target = Path(os.environ['BROTHER_DEPLOY_TARGET'])\n"
            "artifacts = sorted(target.parent.glob('brother-deploys/*/rollback/manifest.json'))\n"
            "row = {'canary': str(here), 'old': target.joinpath('a.py').read_bytes().hex(),\n"
            "       'backups': [str(p) for p in artifacts],\n"
            "       'config': (here.parents[2] / 'docs/plan/model-registry.json').read_text()}\n"
            "Path(os.environ['DEPLOY_TEST_LOG']).write_text(json.dumps(row))\n"
            "print('CANARY WOULD LAND: fixture')\n")
        for name in CONFIGS:
            (self.plan / name).write_text(json.dumps({"version": "new", "name": name}))
            (self.target.parent / name).write_text(json.dumps({"version": "old", "name": name}))
        for name in ("test_loop_tool_parity.py", "install_loop_tools.py"):
            shutil.copy2(ROOT / "scripts" / name, self.source / "scripts" / name)
        self.env["DEPLOY_TEST_LOG"] = str(self.box / "canary.json")
        self.git("init", "--quiet")
        self.git("add", ".")
        self.git("-c", "user.name=Fixture", "-c", "user.email=fixture@example.invalid",
                 "-c", "core.hooksPath=/dev/null", "commit", "--quiet", "-m", "fixture")
        self.revision = self.git("rev-parse", "HEAD").stdout.strip()
        self.before = files(self.target)
        self.config_before = {n: (self.target.parent / n).read_bytes() for n in CONFIGS}

    def git(self, *args):
        return subprocess.run(["git", "-C", str(self.source)] + list(args), env=self.env,
                              check=True, capture_output=True, text=True)

    def deploy(self, *args):
        self.assertTrue(DEPLOY.is_file(), "the versioned deploy entry point is missing")
        return subprocess.run(["bash", str(DEPLOY)] + [str(a) for a in args],
                              env=self.env, cwd=str(self.box), capture_output=True,
                              text=True, timeout=30)

    def success(self, result):
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def unchanged(self):
        self.assertEqual(files(self.target), self.before)
        self.assertEqual({n: (self.target.parent / n).read_bytes() for n in CONFIGS},
                         self.config_before)

    def artifact(self):
        paths = list(self.target.parent.glob("brother-deploys/*/rollback/manifest.json"))
        self.assertEqual(len(paths), 1)
        return paths[0].parent

    def inject_copy_failure(self, filename):
        injection = self.box / "injection"
        injection.mkdir()
        (injection / "sitecustomize.py").write_text(
            "import os, shutil\nfrom pathlib import Path\n"
            "original = shutil.copy2\n"
            "def copy(src, dst, *a, **kw):\n"
            "    src = Path(src)\n"
            "    if str(src.resolve()).startswith(str(Path(os.environ['BROTHER_DEPLOY_SOURCE']).resolve()) + os.sep):\n"
            "        with open(os.environ['DEPLOY_COPY_LOG'], 'a') as log: log.write(src.name + '\\n')\n"
            "        if src.name == os.environ['DEPLOY_FAIL_COPY']:\n"
            "            Path(dst).write_bytes(b'partial write')\n"
            "            raise OSError('fixture partial copy failure')\n"
            "    return original(src, dst, *a, **kw)\n"
            "shutil.copy2 = copy\n")
        self.env.update(PYTHONPATH=str(injection), DEPLOY_FAIL_COPY=filename,
                        DEPLOY_COPY_LOG=str(self.box / "copies.txt"))

    def test_backup_precedes_publish_and_has_hashes(self):
        self.success(self.deploy())
        row = json.loads(Path(self.env["DEPLOY_TEST_LOG"]).read_text())
        backup = self.artifact()
        self.assertEqual(row["backups"], [str(backup / "manifest.json")])
        self.assertEqual(row["old"], self.before["a.py"].hex())
        expected = {"bin/" + n: digest(b) for n, b in self.before.items()}
        expected.update({n: digest(b) for n, b in self.config_before.items()})
        manifest = json.loads((backup / "manifest.json").read_text())
        self.assertEqual(manifest["files"], expected)
        for name, sha in expected.items():
            self.assertEqual(digest((backup / "files" / name).read_bytes()), sha)
        self.assertEqual(list(self.home.iterdir()), [])

    def test_restore_plain_target_byte_for_byte(self):
        self.success(self.deploy())
        self.success(self.deploy("restore", self.artifact()))
        self.unchanged()
        self.assertFalse(self.target.is_symlink())

    def test_restore_symlink_target_byte_for_byte(self):
        prior = self.target.parent / "prior"
        self.target.rename(prior)
        self.target.symlink_to("prior")
        self.success(self.deploy())
        # A rollback is a copy, not merely an old link that might have drifted.
        (prior / "a.py").write_bytes(b"changed since deployment")
        self.success(self.deploy("restore", self.artifact()))
        self.unchanged()
        self.assertTrue(self.target.is_symlink())
        self.assertEqual(files(prior)["a.py"], b"changed since deployment")

    def test_owner_hold_allows_frozen_deploy(self):
        hold = self.home / ".claude/evidence/LOOP-HOLD.txt"
        hold.parent.mkdir(parents=True)
        hold.write_bytes(b"owner hold, keep frozen\n")
        self.success(self.deploy())
        self.assertEqual(hold.read_bytes(), b"owner hold, keep frozen\n")

    def refuse_process(self, command):
        self.env["DEPLOY_FAKE_PROCESS"] = command
        result = self.deploy()
        self.assertNotEqual(result.returncode, 0, result.stdout)
        self.assertIn("STOP INCOMPLETE", result.stdout + result.stderr)
        self.unchanged()
        self.assertFalse((self.target.parent / "brother-deploys").exists())

    def test_live_driver_refuses(self):
        self.refuse_process("/bin/bash /fixture/bin/loop_until.sh")

    def test_live_runner_refuses(self):
        self.refuse_process("python3 /fixture/bin/unit_runner.py")

    def test_process_filter_cannot_hide_live_runner(self):
        self.env["STOP_LOOP_ONLY"] = "unrelated scope"
        self.refuse_process("python3 /fixture/bin/unit_runner.py")

    def test_stamp_records_revision_and_every_deployed_hash(self):
        self.success(self.deploy())
        stamp = json.loads((self.target / ".deploy-stamp.json").read_text())
        self.assertEqual(stamp["revision"], self.revision)
        expected = {"bin/" + n: digest(b) for n, b in files(self.target).items()
                    if n != ".deploy-stamp.json"}
        expected.update({n: digest((self.target.parent / n).read_bytes()) for n in CONFIGS})
        self.assertEqual(stamp["files"], expected)
        self.assertTrue(self.target.is_symlink())

    def test_partial_tool_copy_failure_keeps_target_unchanged(self):
        (self.loop / "z_fail.py").write_text("# last source file\n")
        self.inject_copy_failure("z_fail.py")
        result = self.deploy()
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("partial copy failure", result.stdout + result.stderr)
        self.assertIn("a.py", (self.box / "copies.txt").read_text().splitlines())
        self.unchanged()

    def test_partial_config_copy_failure_keeps_target_unchanged(self):
        self.inject_copy_failure("loop-roles.json")
        result = self.deploy()
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("partial copy failure", result.stdout + result.stderr)
        self.unchanged()

    def test_canary_runs_from_stamp_with_staged_config(self):
        self.success(self.deploy())
        row = json.loads(Path(self.env["DEPLOY_TEST_LOG"]).read_text())
        self.assertEqual(Path(row["canary"]), self.target.resolve() / "loop_canary.py")
        self.assertEqual(row["config"], (self.plan / "model-registry.json").read_text())

    def test_canary_refusal_keeps_target_unchanged(self):
        (self.loop / "loop_canary.py").write_text("print('CANARY REFUSED: fixture')\nraise SystemExit(1)\n")
        result = self.deploy()
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("CANARY REFUSED", result.stdout + result.stderr)
        self.unchanged()

    def test_parity_refusal_restores_target(self):
        # The canary mutates staged bytes after copying. The real parity gate must catch it.
        with (self.loop / "loop_canary.py").open("a") as fh:
            fh.write("here.with_name('a.py').write_text('# drift introduced by canary\\n')\n")
        result = self.deploy()
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("FAIL drift", result.stdout + result.stderr)
        self.unchanged()

    def test_preserves_bridge_modes_and_config_skips_caches(self):
        self.success(self.deploy())
        self.assertEqual((self.target / "bridge.py").read_bytes(), self.before["bridge.py"])
        self.assertEqual((self.target / "a.py").stat().st_mode & 0o777, 0o755)
        self.assertEqual((self.target / "brief.md").read_bytes(), (self.loop / "brief.md").read_bytes())
        self.assertFalse((self.target / "__pycache__").exists())
        self.assertFalse((self.target / "a.py.bak-old").exists())
        for name in CONFIGS:
            self.assertEqual((self.target.parent / name).read_bytes(), (self.plan / name).read_bytes())

    def test_a_package_under_the_loop_reaches_the_bin_whole(self):
        """2026-10-04: scripts/loop/adapters/ is imported by model_call, and the deploy copied files only, so the
        deployed bin could not import it. A package is deployed whole (minus caches and test files), its files are in
        the stamp's hashes, and a plain directory under scripts/loop is not a tool."""
        pkg = self.loop / "pkg"
        (pkg / "__pycache__").mkdir(parents=True)
        (pkg / "__init__.py").write_text("# package\n")
        (pkg / "m.py").write_text("# module\n")
        (pkg / "__pycache__/m.pyc").write_bytes(b"cache")
        (pkg / "test_m.py").write_text("# test only\n")
        (self.loop / "arms").mkdir()
        (self.loop / "arms/run.sh").write_text("#!/bin/sh\n")
        self.success(self.deploy())
        self.assertEqual((self.target / "pkg/__init__.py").read_bytes(), b"# package\n")
        self.assertEqual((self.target / "pkg/m.py").read_bytes(), b"# module\n")
        self.assertFalse((self.target / "pkg/__pycache__").exists())
        self.assertFalse((self.target / "pkg/test_m.py").exists())
        self.assertFalse((self.target / "arms").exists())
        stamp = json.loads((self.target / ".deploy-stamp.json").read_text())
        self.assertIn("bin/pkg/m.py", stamp["files"])

    def test_restore_refuses_corrupt_backup_before_changes(self):
        self.success(self.deploy())
        backup = self.artifact()
        (backup / "files/bin/a.py").write_bytes(b"corrupt backup")
        deployed = files(self.target)
        result = self.deploy("restore", backup)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("hash", result.stdout + result.stderr)
        self.assertEqual(files(self.target), deployed)

    def test_restore_refuses_live_runner(self):
        self.success(self.deploy())
        deployed = files(self.target)
        self.env["DEPLOY_FAKE_PROCESS"] = "python3 /fixture/bin/unit_runner.py"
        result = self.deploy("restore", self.artifact())
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(files(self.target), deployed)

    def test_printed_restore_command_works(self):
        result = self.deploy()
        self.success(result)
        line = next(line for line in result.stdout.splitlines() if line.startswith("RESTORE "))
        self.success(subprocess.run(shlex.split(line[len("RESTORE "):]), env=self.env,
                                    capture_output=True, text=True, timeout=30))
        self.unchanged()

    def crash_between_migration_renames(self):
        """Kill the deploy after the plain bin is parked and before the new link takes its name: the
        interruption between the migration's two renames, which leaves no target at all."""
        injection = self.box / "injection"
        injection.mkdir()
        (injection / "sitecustomize.py").write_text(
            "import os\nfrom pathlib import Path\noriginal = os.replace\n"
            "def replace(src, dst):\n"
            "    if Path(src).name == 'next-bin': os._exit(9)\n"
            "    return original(src, dst)\n"
            "os.replace = replace\n")
        self.env["PYTHONPATH"] = str(injection)
        result = self.deploy()
        self.env["PYTHONPATH"] = ""
        self.assertEqual(result.returncode, 9, result.stdout + result.stderr)
        self.assertFalse(os.path.lexists(str(self.target)))
        return result

    def test_printed_restore_recovers_a_bin_lost_mid_migration(self):
        result = self.crash_between_migration_renames()
        line = next(line for line in result.stdout.splitlines() if line.startswith("RESTORE "))
        self.success(subprocess.run(shlex.split(line[len("RESTORE "):]), env=self.env,
                                    capture_output=True, text=True, timeout=30))
        self.unchanged()
        self.assertFalse(self.target.is_symlink())

    def test_restore_of_a_missing_bin_still_refuses_a_tampered_snapshot(self):
        self.crash_between_migration_renames()
        backup = self.artifact()
        (backup / "files/bin/a.py").write_bytes(b"corrupt backup")
        result = self.deploy("restore", backup)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("rollback hash mismatch", result.stdout + result.stderr)
        self.assertFalse(os.path.lexists(str(self.target)))

    def test_restore_refuses_a_target_that_exists_but_is_not_a_directory(self):
        self.success(self.deploy())
        backup = self.artifact()
        self.target.unlink()
        self.target.write_bytes(b"not a directory\n")
        result = self.deploy("restore", backup)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("directory", result.stdout + result.stderr)
        self.assertEqual(self.target.read_bytes(), b"not a directory\n")

    def test_deploy_refuses_a_missing_target_before_writing_anything(self):
        shutil.rmtree(str(self.target))
        result = self.deploy()
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("existing directory", result.stdout + result.stderr)
        self.assertFalse((self.target.parent / "brother-deploys").exists())

    def test_publish_rename_failure_restores_configs_and_bin(self):
        injection = self.box / "injection"
        injection.mkdir()
        (injection / "sitecustomize.py").write_text(
            "import os\nfrom pathlib import Path\noriginal = os.replace\n"
            "def replace(src, dst):\n"
            "    if Path(src).name == 'next-bin': raise OSError('fixture rename failure')\n"
            "    return original(src, dst)\n"
            "os.replace = replace\n")
        self.env["PYTHONPATH"] = str(injection)
        result = self.deploy()
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("fixture rename failure", result.stdout + result.stderr)
        self.unchanged()

    def test_backup_write_failure_keeps_target_unchanged(self):
        injection = self.box / "injection"
        injection.mkdir()
        (injection / "sitecustomize.py").write_text(
            "from pathlib import Path\noriginal = Path.open\n"
            "def fopen(self, *a, **kw):\n"
            "    if self.name == 'manifest.json': raise OSError('fixture manifest failure')\n"
            "    return original(self, *a, **kw)\n"
            "Path.open = fopen\n")
        self.env["PYTHONPATH"] = str(injection)
        result = self.deploy()
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("fixture manifest failure", result.stdout + result.stderr)
        self.unchanged()

    def test_missing_optional_canary_config_keeps_previous_copy(self):
        (self.plan / "loop-canary.json").unlink()
        self.success(self.deploy())
        self.assertEqual((self.target.parent / "loop-canary.json").read_bytes(),
                         self.config_before["loop-canary.json"])


class ToolStampSwap(unittest.TestCase):
    """tool_stamp.py swap and rollback through the real CLI under a scratch HOME: the way back is on disk
    before the switch, and a switch that fails leaves or puts back the old stamp and says which."""

    TOOL = ROOT / "scripts/loop/tool_stamp.py"

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="stamp fixture ")
        self.addCleanup(self.tmp.cleanup)
        box = Path(os.path.realpath(self.tmp.name))
        self.home = box / "home"
        self.stamps = self.home / ".claude/brother-tools"
        self.bin = self.home / ".claude/bin"
        self.a, self.b = self.stamps / "A", self.stamps / "B"
        for stamp in (self.a, self.b):
            stamp.mkdir(parents=True)
            (stamp / "tool.py").write_text(stamp.name + "\n")
        self.bin.symlink_to(self.a)
        self.previous = self.stamps / "PREVIOUS"
        self.injection = box / "injection"
        self.injection.mkdir()
        self.env = dict(os.environ, HOME=str(self.home), PYTHONDONTWRITEBYTECODE="1", PYTHONPATH="",
                        STAMP_BIN=str(self.bin))

    def inject(self, body):
        (self.injection / "sitecustomize.py").write_text("import os\noriginal = os.rename\n" + body)
        self.env["PYTHONPATH"] = str(self.injection)

    def tool(self, *args):
        result = subprocess.run([sys.executable, "-B", str(self.TOOL)] + list(args), env=self.env,
                                capture_output=True, text=True, timeout=30)
        self.env["PYTHONPATH"] = ""
        return result

    def active(self):
        return Path(os.path.realpath(str(self.bin)))

    def test_swap_and_rollback_round_trip(self):
        self.assertEqual(self.tool("swap", str(self.b)).returncode, 0)
        self.assertEqual(self.active(), self.b)
        self.assertEqual(self.tool("rollback").returncode, 0)
        self.assertEqual(self.active(), self.a)
        self.assertEqual(self.tool("rollback").returncode, 0)
        self.assertEqual(self.active(), self.b)

    def test_a_crash_right_after_the_switch_still_rolls_back(self):
        self.inject("def rename(src, dst):\n"
                    "    original(src, dst)\n"
                    "    if os.fspath(dst) == os.environ['STAMP_BIN']: os._exit(9)\n"
                    "os.rename = rename\n")
        self.assertEqual(self.tool("swap", str(self.b)).returncode, 9)
        self.assertEqual(self.active(), self.b)
        result = self.tool("rollback")
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual(self.active(), self.a)

    def test_a_previous_that_cannot_be_recorded_refuses_before_the_switch(self):
        self.previous.mkdir()  # the audit's shape: only the PREVIOUS write can fail
        result = self.tool("swap", str(self.b))
        self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
        self.assertIn("REFUSED", result.stdout)
        self.assertIn("unchanged", result.stdout)
        self.assertEqual(self.active(), self.a)
        self.assertEqual(sorted(os.listdir(str(self.stamps))), ["A", "B", "PREVIOUS"])

    def test_a_failed_record_keeps_the_prior_previous_whole(self):
        self.previous.write_text("prior way back\n")
        self.inject("def fsync(fd): raise OSError('fixture fsync failure')\nos.fsync = fsync\n")
        result = self.tool("swap", str(self.b))
        self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
        self.assertIn("fixture fsync failure", result.stdout)
        self.assertEqual(self.previous.read_text(), "prior way back\n")
        self.assertEqual(self.active(), self.a)
        self.assertEqual(sorted(os.listdir(str(self.stamps))), ["A", "B", "PREVIOUS"])

    def test_a_switch_that_fails_leaves_the_old_stamp_and_says_so(self):
        self.inject("def rename(src, dst):\n"
                    "    if os.fspath(dst) == os.environ['STAMP_BIN']: raise OSError('fixture switch failure')\n"
                    "    return original(src, dst)\n"
                    "os.rename = rename\n")
        result = self.tool("swap", str(self.b))
        self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
        self.assertIn("unchanged at A", result.stdout)
        self.assertEqual(self.active(), self.a)

    def test_a_switch_that_took_effect_then_failed_is_put_back(self):
        self.inject("state = []\n"
                    "def rename(src, dst):\n"
                    "    original(src, dst)\n"
                    "    if os.fspath(dst) == os.environ['STAMP_BIN'] and not state:\n"
                    "        state.append(1); raise OSError('fixture failure after the switch')\n"
                    "os.rename = rename\n")
        result = self.tool("swap", str(self.b))
        self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
        self.assertIn("restored to A", result.stdout)
        self.assertEqual(self.active(), self.a)

    def test_a_failed_put_back_is_a_fail_naming_where_bin_points(self):
        self.inject("state = []\n"
                    "def rename(src, dst):\n"
                    "    if os.fspath(dst) == os.environ['STAMP_BIN']:\n"
                    "        if state: raise OSError('fixture put back failure')\n"
                    "        state.append(1); original(src, dst); raise OSError('fixture failure after the switch')\n"
                    "    return original(src, dst)\n"
                    "os.rename = rename\n")
        result = self.tool("swap", str(self.b))
        self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
        self.assertIn("FAIL", result.stdout)
        self.assertIn(str(self.b), result.stdout)
        self.assertEqual(self.active(), self.b)

    def test_migrate_of_an_absent_bin_is_no_data(self):
        self.bin.unlink()
        result = self.tool("migrate")
        self.assertEqual(result.returncode, 3, result.stdout + result.stderr)
        self.assertIn("NO-DATA", result.stdout)

    def test_rollback_refuses_when_previous_names_the_active_stamp(self):
        # A crash between recording PREVIOUS and the switch leaves exactly this; rolling "back" to the
        # active stamp is not a rollback and must not report one.
        self.previous.write_text(str(self.a) + "\n")
        result = self.tool("rollback")
        self.assertEqual(result.returncode, 3, result.stdout + result.stderr)
        self.assertIn("NO-DATA", result.stdout)
        self.assertEqual(self.active(), self.a)
        self.assertEqual(self.previous.read_text(), str(self.a) + "\n")


class TheConfigSeam(unittest.TestCase):
    """BROTHER_DEPLOY_CONFIG_DIR lets a hermetic test point a deploy at fixture configs; it must never reach the
    live bin, where it would install placeholder models and prices into the deployment the loop runs from."""

    def setUp(self):
        sys.path.insert(0, str(ROOT / "scripts/loop"))
        import deploy_stamped
        self.D = deploy_stamped
        self.saved = os.environ.pop("BROTHER_DEPLOY_CONFIG_DIR", None)
        self.addCleanup(self.restore)
        self.source = Path(tempfile.mkdtemp(prefix="deploy-seam-"))
        self.addCleanup(shutil.rmtree, str(self.source), True)

    def restore(self):
        os.environ.pop("BROTHER_DEPLOY_CONFIG_DIR", None)
        if self.saved is not None:
            os.environ["BROTHER_DEPLOY_CONFIG_DIR"] = self.saved

    def live_bin(self):
        import pwd
        return Path(pwd.getpwuid(os.getuid()).pw_dir) / ".claude" / "bin"

    def test_unset_reads_the_source_docs_plan(self):
        self.assertEqual(self.D.config_dir(self.source, self.source / "bin"), self.source / "docs/plan")

    def test_set_points_a_scratch_deploy_at_the_fixture_folder(self):
        os.environ["BROTHER_DEPLOY_CONFIG_DIR"] = str(self.source / "fixtures")
        self.assertEqual(self.D.config_dir(self.source, self.source / "bin"), self.source / "fixtures")

    def test_set_refuses_the_live_bin(self):
        os.environ["BROTHER_DEPLOY_CONFIG_DIR"] = str(self.source / "fixtures")
        with self.assertRaises(RuntimeError):
            self.D.config_dir(self.source, self.live_bin())


if __name__ == "__main__":
    unittest.main()

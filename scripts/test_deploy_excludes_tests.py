#!/usr/bin/env python3
"""The deployed bin never carries a test-only file (D-21).

Measured on a scratch staging of the loop bin: scripts/loop/freeze_manifest.py's static import
closure refuses on test_or_ask.py and test_or_ask_effort.py (each loads or_ask.py through
importlib.util.spec_from_file_location, an opaque loader) and on test_unit_runner_h3a.py (it
imports test_unit_runner_probe_gate, which lives in scripts/, not scripts/loop, so the deployed
bin has no file to resolve it against). Removing test_*.py files from staging is safe only if
neither the driver (loop_until.sh) nor the canary (tool_stamp.prove, loop_canary.py's stages,
deploy_stamped.py's own steps) requires one; NoTestFileRequired proves that statically, as a
runnable guard rather than a claim, and goes red the day a production tool starts importing one.

DeployExcludesTests fixture mirrors test_deploy_stamped.py: a fixture source (its own
scripts/loop), a fixture target bin, an empty HOME. The old bin carries a leftover test-only
file from a previous (pre-fix) deploy; the source scripts/loop carries another. Neither may
reach the new bin; parity must not report either as drift or as bin-only, and must still catch a
real drift; restore must still return the pre-deploy bin byte for byte, test file included.

Run: python3 -B scripts/test_deploy_excludes_tests.py -v
"""
import ast
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
DEPLOY = ROOT / "scripts/loop/deploy_stamped.sh"
LOOP = ROOT / "scripts/loop"
CONFIGS = ("model-registry.json", "loop-roles.json", "loop-canary.json")


def files(path):
    return {str(p.relative_to(path)): p.read_bytes() for p in path.rglob("*") if p.is_file()}


class NoTestFileRequired(unittest.TestCase):
    """Task 1's reachability check, as code: nothing the driver or the canary runs needs a
    test_*.py file. Mutate either side to require one and this class is what turns red."""

    def production_tools(self):
        return sorted(p for p in LOOP.glob("*.py") if not p.name.startswith("test_"))

    def test_no_production_tool_imports_a_test_module(self):
        offenders = []
        for path in self.production_tools():
            tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
            for node in ast.walk(tree):
                names = []
                if isinstance(node, ast.Import):
                    names = [a.name for a in node.names]
                elif isinstance(node, ast.ImportFrom) and node.module:
                    names = [node.module]
                for name in names:
                    if name.split(".")[0].startswith("test_"):
                        offenders.append("%s imports %s" % (path.name, name))
        self.assertEqual(offenders, [],
                         "a deployed tool now requires a test module: %s" % offenders)

    def test_driver_and_deploy_entry_never_name_a_test_file(self):
        for shell in ("loop_until.sh", "deploy_stamped.sh"):
            text = (LOOP / shell).read_text(encoding="utf-8")
            found = re.findall(r"\btest_[A-Za-z0-9_]*\.py\b", text)
            self.assertEqual(found, [], "%s now names a test file: %s" % (shell, found))


class DeployExcludesTests(unittest.TestCase):
    """Fixture deploy: source scripts/loop has test_x.py, the old bin has test_y.py."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="deploy-excludes-tests ")
        self.addCleanup(self.tmp.cleanup)
        self.box = Path(self.tmp.name)
        self.home = self.box / "empty-home"
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
                        BROTHER_DEPLOY_PYTHON=sys.executable,
                        PYTHONDONTWRITEBYTECODE="1", PYTHONPATH="",
                        GIT_CONFIG_NOSYSTEM="1", GIT_CONFIG_GLOBAL=os.devnull,
                        PATH=str(self.fakebin) + os.pathsep + os.environ["PATH"])
        for key in ("STOP_LOOP_ONLY", "DEPLOY_FAKE_PROCESS", "DEPLOY_FAIL_COPY",
                    "DEPLOY_COPY_LOG", "DEPLOY_TEST_LOG"):
            self.env.pop(key, None)
        ps = self.fakebin / "ps"
        ps.write_text('#!/bin/sh\ncase "$*" in\n'
                      '  *pgid=,command=*) printf "1 0 1 /sbin/launchd\\n"; exit 0;;\n'
                      '  *) exit 1;;\nesac\n')
        ps.chmod(0o755)
        # OLD BIN: a real tool plus a leftover test-only file from a previous, pre-fix deploy.
        (self.target / "a.py").write_bytes(b"old tool\n")
        (self.target / "test_y.py").write_bytes(b"import unittest  # leftover from an old deploy\n")
        # SOURCE scripts/loop: the same real tool, newer, plus its own test-only file.
        (self.loop / "a.py").write_text("# new tool\n")
        (self.loop / "test_x.py").write_text("import unittest  # a unit test, never a deployed tool\n")
        (self.loop / "loop_canary.py").write_text("print('CANARY WOULD LAND: fixture')\n")
        for name in CONFIGS:
            (self.plan / name).write_text(json.dumps({"version": "new", "name": name}))
            (self.target.parent / name).write_text(json.dumps({"version": "old", "name": name}))
        for name in ("test_loop_tool_parity.py", "install_loop_tools.py"):
            shutil.copy2(ROOT / "scripts" / name, self.source / "scripts" / name)
        self.git("init", "--quiet")
        self.git("add", ".")
        self.git("-c", "user.name=Fixture", "-c", "user.email=fixture@example.invalid",
                 "-c", "core.hooksPath=/dev/null", "commit", "--quiet", "-m", "fixture")
        self.before = files(self.target)

    def git(self, *args):
        return subprocess.run(["git", "-C", str(self.source)] + list(args), env=self.env,
                              check=True, capture_output=True, text=True)

    def deploy(self, *args):
        return subprocess.run(["bash", str(DEPLOY)] + [str(a) for a in args],
                              env=self.env, cwd=str(self.box), capture_output=True,
                              text=True, timeout=30)

    def success(self, result):
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def artifact(self):
        paths = list(self.target.parent.glob("brother-deploys/*/rollback"))
        self.assertEqual(len(paths), 1)
        return paths[0]

    def parity(self):
        return subprocess.run([sys.executable, "-B", str(self.source / "scripts/test_loop_tool_parity.py")],
                              env=self.env, cwd=str(self.source), capture_output=True, text=True, timeout=60)

    def test_neither_source_nor_old_bin_test_file_reaches_the_target(self):
        # This is also the mutation guard for the tool_stamp._skip change: revert it and this
        # test goes red because test_x.py and test_y.py land in the new bin.
        self.success(self.deploy())
        names = set(files(self.target))
        self.assertNotIn("test_x.py", names, "the source's test-only file was deployed")
        self.assertNotIn("test_y.py", names, "the old bin's test-only file was carried forward")
        self.assertIn("a.py", names, "the real tool must still deploy")

    def test_canary_and_parity_pass_with_test_files_excluded(self):
        # deploy_stamped.py runs the canary (tool_stamp.prove) and the parity check itself; a
        # nonzero exit here means one of them refused.
        self.success(self.deploy())

    def test_parity_reports_neither_absence_as_drift_nor_bin_only(self):
        self.success(self.deploy())
        result = self.parity()
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn("PASS", result.stdout)
        self.assertNotIn("test_x.py", result.stdout)
        self.assertNotIn("test_y.py", result.stdout)

    def test_parity_still_catches_a_real_drift(self):
        self.success(self.deploy())
        (self.target / "a.py").write_text("# drifted after deploy, never versioned\n")
        result = self.parity()
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("FAIL drift", result.stdout)

    def test_restore_returns_the_old_bin_byte_for_byte_test_file_included(self):
        self.success(self.deploy())
        backup = self.artifact()
        self.success(self.deploy("restore", backup))
        self.assertEqual(files(self.target), self.before)
        self.assertEqual((self.target / "test_y.py").read_bytes(), self.before["test_y.py"])


if __name__ == "__main__":
    unittest.main()

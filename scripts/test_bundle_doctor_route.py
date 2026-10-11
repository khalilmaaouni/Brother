#!/usr/bin/env python3
"""DR1.a: the installed doctor route, proven on a mirror this tree's generator produces.

products/brothermode/tools/brothermode_cli.py `cmd_doctor` loads the doctor through
`os.path.join("scripts", "doctor.py")` under the directory above tools/. Installed, that is
bundle/runtime/hooks/brothermode/scripts/doctor.py. Until DR1 the generator's path-join reader
(scripts/bundle_runtime.py `_package_join_targets`) took the FIRST argument of every join as a root,
so a join made only of constants read as the tail `doctor.py`, resolved nowhere, and the one plugin
shipped `scripts/setup.py` alone: `brothermode_cli.py doctor` crashed before its first check.

The mirror under test is regenerated here into a scratch copy of bundle/runtime by THIS tree's
generator (`fresh_mirror`), never read from the committed copy: a grader sandbox does not regenerate
the mirror, the landing does, so a test that read the committed bytes would be red with the fix and
green without it. Everything written lands under one tempfile.mkdtemp; the real ~/.claude, ~/.codex
and the committed bundle are never touched. NO-DATA is never a pass: a copy that cannot be made, a
generator that raises, or a CLI that does not start fails naming the path.
"""
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from typing import Dict, List, Tuple

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(HERE)
sys.path.insert(0, HERE)
import bundle_runtime as BR  # noqa: E402

ALLOWED_STATUS = ("PASS", "FAIL", "SKIP")
PRODUCT_SCRIPTS = os.path.join(REPO, "products", "brothermode", "scripts")


def fresh_mirror(tmp: str) -> str:
    """Copy this checkout's bundle/runtime to <tmp>/bundle/runtime and regenerate the brothermode hook
    mirror there with this tree's generator. Returns that runtime directory."""
    runtime = os.path.join(tmp, "bundle", "runtime")
    shutil.copytree(os.path.join(REPO, "bundle", "runtime"), runtime, symlinks=True)
    # The copy's package files go before the regeneration: the generator never removes a package file
    # it no longer computes, so a committed mirror that already carries scripts/doctor.py would hide a
    # generator that stopped producing it. What this suite reads must come from this tree's generator.
    shutil.rmtree(os.path.join(runtime, "hooks", "brothermode", "scripts"), True)
    BR.generate_hooks(products=("brothermode",), runtime_dir=runtime)
    return runtime


def run_installed_doctor(runtime_dir: str, home: str) -> Tuple[int, str, str]:
    """`brothermode_cli.py doctor --json` from the mirror, as a real subprocess under an isolated
    environment (PATH and PYTHONDONTWRITEBYTECODE from this process, every home under `home`), cwd
    `home` so no project store is found. Returns (returncode, stdout, stderr)."""
    os.makedirs(os.path.join(home, "tmp"), exist_ok=True)
    env = {"PATH": os.environ.get("PATH", ""), "PYTHONDONTWRITEBYTECODE": "1",
           "HOME": home, "CLAUDE_CONFIG_DIR": os.path.join(home, ".claude"),
           "CODEX_HOME": os.path.join(home, "codex"), "TMPDIR": os.path.join(home, "tmp")}
    cli = os.path.join(runtime_dir, "hooks", "brothermode", "tools", "brothermode_cli.py")
    proc = subprocess.run([sys.executable, "-B", cli, "doctor", "--json"], cwd=home, env=env,
                          capture_output=True, text=True, timeout=180)
    return proc.returncode, proc.stdout, proc.stderr


def check_statuses(payload: Dict[str, object]) -> List[str]:
    """The key of every check whose status is not PASS, FAIL or SKIP; [] is the clean answer."""
    checks = payload.get("checks") if isinstance(payload, dict) else None
    out = []
    for c in checks or []:
        if not isinstance(c, dict) or c.get("status") not in ALLOWED_STATUS:
            out.append(str(c.get("key") if isinstance(c, dict) else c))
    return out


class TheGeneratorReadsAnAllConstantJoin(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="doctor-route-")
        self.addCleanup(shutil.rmtree, self.tmp, True)

    def test_an_all_constant_join_reads_as_the_whole_relative_path(self):
        path = os.path.join(self.tmp, "fixture.py")
        with open(path, "w", encoding="utf-8") as fh:
            fh.write('import os\n'
                     'A = os.path.join("scripts", "doctor.py")\n'
                     'B = os.path.join(root, "scripts", "setup.py")\n'
                     'C = os.path.join("scripts", "notes.md")\n')
        self.assertEqual(BR._package_join_targets(path), {"scripts/doctor.py", "scripts/setup.py"})

    def test_the_generator_names_doctor_and_install_as_package_files(self):
        tools_dir, closure = BR.compute_hook_closure("brothermode")
        pkgs = BR.compute_hook_package_files("brothermode", tools_dir, closure)
        for tail in ("scripts/doctor.py", "scripts/install.py", "brotherme/core/schema.py"):
            self.assertIn(tail, pkgs, pkgs)

    def test_check_statuses_names_a_status_outside_the_three(self):
        self.assertEqual(check_statuses({"checks": [{"key": "x", "status": "CRASHED"},
                                                    {"key": "y", "status": "SKIP"}]}), ["x"])
        self.assertEqual(check_statuses({"checks": []}), [])
        self.assertEqual(check_statuses({}), [])


class TheInstalledDoctorRoute(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.mkdtemp(prefix="doctor-route-")
        cls.runtime = fresh_mirror(cls.tmp)

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.tmp, True)

    def test_the_regenerated_mirror_ships_doctor_py_beside_setup_py(self):
        mirror_scripts = os.path.join(self.runtime, "hooks", "brothermode", "scripts")
        for name in ("setup.py", "doctor.py", "install.py"):
            shipped = os.path.join(mirror_scripts, name)
            self.assertTrue(os.path.isfile(shipped),
                            "%s missing: brothermode_cli.py cannot load scripts/%s from the plugin" % (shipped, name))
            with open(shipped, "rb") as fh:
                shipped_bytes = fh.read()
            with open(os.path.join(PRODUCT_SCRIPTS, name), "rb") as fh:
                source_bytes = fh.read()
            self.assertEqual(shipped_bytes, source_bytes, name)

    def test_the_installed_doctor_runs_end_to_end_under_a_fake_home(self):
        home = os.path.join(self.tmp, "home")
        os.makedirs(home)
        code, out, err = run_installed_doctor(self.runtime, home)
        self.assertIn(code, (0, 1), "exit %d\n%s\n%s" % (code, out[-800:], err[-1200:]))
        self.assertNotIn("Traceback", err, err[-1500:])
        try:
            payload = json.loads(out)
        except ValueError as exc:
            self.fail("stdout is not JSON (%s): %r" % (exc, out[:300]))
        checks = payload.get("checks")
        self.assertTrue(isinstance(checks, list) and checks, "no checks in %r" % out[:300])
        self.assertEqual(check_statuses(payload), [], [(c["key"], c["status"]) for c in checks])
        crashed = [c["key"] for c in checks if str(c.get("message", "")).startswith("FAIL: this check crashed")]
        self.assertEqual(crashed, [], "checks that crashed inside the installed copy: %s" % crashed)
        by_key = {c["key"]: c["status"] for c in checks}
        for key in ("version", "checksums", "install_identity"):
            self.assertEqual(by_key.get(key), "SKIP", "%s read %s, absent files must read SKIP" % (key, by_key.get(key)))


if __name__ == "__main__":
    unittest.main()

#!/usr/bin/env python3
"""Packaging install suite: C-06. Every console script pyproject.toml
declares must reach an installed user's PATH and actually run, not merely
appear to build. Builds a real venv, installs the real package into it with
pip (never a git checkout added to sys.path), and drives the installed
commands as subprocesses exactly the way a stranger who ran
`pip install brothermode` would.

Deliberately separate from tools/test_bm.py's
TestP17PackagingManifestMatchesTheRepository: that suite is a fast, offline,
text-only read of pyproject.toml (its own docstring says so) and can never
catch a module that is declared but unreachable at runtime, which is
exactly how this item was opened (brotherme/core/schema.py shipped absent,
then shipped but unreachable, from a flat install). This suite is the
register's own adversarial test: "install into a fresh virtualenv and
invoke each documented command."

Needs network egress once, to upgrade pip/setuptools/wheel inside its own
throwaway venv: this machine's system pip (21.2.4) silently mis-builds and
says nothing (docs/PACKAGING.md, "One build command on this machine is
broken, and it fails quietly"). Skips rather than fails if that upgrade
cannot reach PyPI, because a missing network is an environment fact, not a
packaging regression.

Slow (builds and installs a real package, tens of seconds) and therefore
not part of the always-run fast gate by default; run directly:
    python3 tools/test_bm_packaging_install.py
"""
import io
import os
import re
import shutil
import subprocess
import sys
import tempfile
import unittest
import venv

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
PYPROJECT = os.path.join(ROOT, "pyproject.toml")


def _read(path):
    with io.open(path, encoding="utf-8") as fh:
        return fh.read()


def _scripts():
    """Same narrow parse as TestP17PackagingManifestMatchesTheRepository's
    own _scripts() in tools/test_bm.py, duplicated rather than imported:
    this file must be runnable standalone without pulling in that suite's
    whole fixture surface for one helper."""
    text = _read(PYPROJECT)
    m = re.search(r"^\[project\.scripts\]\s*$(.*?)(?=^\[|\Z)", text,
                  re.S | re.M)
    assert m is not None, "[project.scripts] not found in pyproject.toml"
    out = {}
    for line in m.group(1).splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        name, _, target = line.partition("=")
        out[name.strip()] = target.strip().strip('"')
    assert out, "[project.scripts] is empty"
    return out


class TestPipInstalledCopyExposesEveryDocumentedCommand(unittest.TestCase):
    """C-06. Builds the package fresh, installs it with pip into an
    ephemeral venv, and proves two things a static text check of
    pyproject.toml cannot: that the console scripts actually land on PATH,
    and that running them does not crash on the very first line."""

    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.mkdtemp(prefix="bm_pkg_install_")
        cls.venv_dir = os.path.join(cls.tmp, "venv")
        # SKIP RATHER THAN ERROR when the HOST cannot build a virtualenv with
        # pip in it (2026-08-10). This line used to raise
        # CalledProcessError straight out of setUpClass, which unittest
        # reports as an ERROR with "Ran 0 tests", so the whole gate went red
        # over a property of the machine rather than a property of the
        # package. Measured: it fails identically on the commit before this
        # one, so it never was a regression, and it is the same shape as the
        # pip-upgrade guard twenty lines below, which the author already
        # wrote as a SkipTest and simply did not apply one step earlier.
        #
        # The SKIP vocabulary here is DELIBERATELY narrow, because this
        # project has been bitten by a SKIP that covered both "cannot be
        # checked here" and "a genuine regression" with the same word. Only
        # the environment's inability to construct a venv is skipped. Every
        # later step, the build, the install, the console scripts and their
        # first line of output, still FAILS on a defect, because those are
        # properties of the package and this machine has nothing to do with
        # them.
        # PER-TEST, NOT PER-CLASS, and that distinction is the whole point.
        # Raising SkipTest from setUpClass makes the suite report "Ran 0
        # tests", and tools/test_all.py REFUSES a suite that exits 0 having
        # run nothing ("import error or empty suite?"), which is a guard
        # worth keeping: it is how a suite that quietly stopped testing gets
        # caught instead of reading as green forever. So the reason is
        # recorded here and every test skips itself in setUp below, which
        # leaves a real, countable test total with an explicit skip on each
        # one.
        cls.unrunnable = None
        try:
            venv.EnvBuilder(with_pip=True).create(cls.venv_dir)
        except Exception as exc:
            cls.unrunnable = (
                "this host cannot build a virtualenv with pip in it, so the "
                "packaged install cannot be exercised here. This is a "
                "property of the MACHINE, not of the package: nothing below "
                "was checked, and nothing below is claimed. Underlying "
                "failure: %s: %s" % (type(exc).__name__, exc))
            return
        cls.bin = os.path.join(cls.venv_dir,
                               "Scripts" if os.name == "nt" else "bin")
        cls.python = os.path.join(
            cls.bin, "python.exe" if os.name == "nt" else "python")

        # CRITICAL ISOLATION. bm_ledger.py resolves its storage as
        # os.environ.get("BROTHERMODE_VAULT", "~/BrotherModeVault"): the
        # env var wins over HOME if it is set in the ambient shell, which
        # it is on any machine that has ever run this project's own
        # tooling. A throwaway HOME alone does NOT isolate that path.
        # Every subprocess this test spawns gets a from-scratch env with
        # BOTH HOME and BROTHERMODE_VAULT (and BROTHERSBE_VAULT, the
        # sibling var the same install may export) pinned inside cls.tmp,
        # so a real founder vault can never be reached no matter what the
        # invoking shell happens to have exported. Reproduced directly
        # while writing this suite: a HOME-only override still wrote a
        # live row into a real, non-throwaway vault because
        # BROTHERMODE_VAULT was already exported ambient.
        cls.fake_home = os.path.join(cls.tmp, "home")
        os.makedirs(cls.fake_home, exist_ok=True)
        cls.env = {
            "PATH": os.environ.get("PATH", ""),
            "HOME": cls.fake_home,
            "BROTHERMODE_VAULT": os.path.join(cls.tmp, "vault"),
            "BROTHERSBE_VAULT": os.path.join(cls.tmp, "vault"),
        }

        upgrade = subprocess.run(
            [cls.python, "-m", "pip", "install", "--quiet",
             "--upgrade", "pip", "setuptools", "wheel"],
            cwd=ROOT, capture_output=True, text=True, timeout=180,
            env=cls.env)
        if upgrade.returncode != 0:
            raise unittest.SkipTest(
                "could not upgrade pip/setuptools/wheel inside the "
                "throwaway venv (no network egress?): %s"
                % (upgrade.stderr or upgrade.stdout))
        # BUILD FROM A COPY, NEVER FROM THE REPOSITORY ITSELF. `pip install
        # <local dir>` builds IN TREE, so installing straight from ROOT left
        # a build/ directory and a .egg-info inside the working copy. Both
        # are gitignored, so nothing showed up in git status and it looked
        # harmless, and it was not: scripts/verify-install.sh compares the
        # tree against CHECKSUMS.sha256 and reports every one of those files
        # as EXTRA, which it describes as exactly the shape of a planted
        # backdoor. Running the test suite therefore made the project's own
        # integrity check report FAILED, which is the worst possible way to
        # spend a security control's credibility. The copy also keeps
        # compiler output out of a directory that is synced and backed up.
        #
        # The copy excludes .git deliberately: it is by far the largest thing
        # here and no build step reads it. Everything else is copied verbatim
        # so the build sees the real tree.
        cls.build_src = os.path.join(cls.tmp, "src")
        shutil.copytree(
            ROOT, cls.build_src,
            ignore=shutil.ignore_patterns(
                ".git", "build", "dist", "*.egg-info", "__pycache__",
                ".brothermode"))
        install = subprocess.run(
            [cls.python, "-m", "pip", "install", "--quiet", cls.build_src],
            cwd=cls.build_src, capture_output=True, text=True, timeout=300,
            env=cls.env)
        cls.install_result = install

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.tmp, ignore_errors=True)

    def setUp(self):
        # The environment skip comes FIRST and is the only skip in this file.
        # Everything after it is a property of the package and fails rather
        # than skips, because no machine can excuse a broken install.
        if getattr(self.__class__, "unrunnable", None):
            self.skipTest(self.__class__.unrunnable)
        if self.install_result.returncode != 0:
            self.fail("pip install of the real package failed:\n%s\n%s"
                      % (self.install_result.stdout,
                         self.install_result.stderr))

    def _script_path(self, name):
        exe = name + (".exe" if os.name == "nt" else "")
        return os.path.join(self.bin, exe)

    def test_every_declared_console_script_lands_on_path(self):
        missing = [name for name in _scripts()
                   if not os.path.isfile(self._script_path(name))]
        self.assertEqual(
            [], missing,
            "pyproject.toml declares these console scripts but pip install "
            "did not put them on PATH: %s" % missing)

    def test_every_console_script_runs_without_crashing_on_startup(self):
        """Runs each installed command with no arguments; every one of
        them is documented to print usage and exit on an empty argv. A
        traceback on stderr here means the command imported code pip did
        not ship, or shipped at a path the installed layout does not
        resolve: exactly the brotherme/core/schema.py defect this item
        exists to close."""
        failures = []
        for name in sorted(_scripts()):
            path = self._script_path(name)
            if not os.path.isfile(path):
                continue  # already reported by the presence test above
            r = subprocess.run([path], capture_output=True, text=True,
                               timeout=30, env=self.env)
            if "Traceback (most recent call last)" in r.stderr:
                failures.append("%s: %s" % (
                    name, r.stderr.strip().splitlines()[-1]))
        self.assertEqual(
            [], failures,
            "these installed commands crashed on startup instead of "
            "printing usage:\n" + "\n".join(failures))

    def test_bm_project_and_bm_ledger_and_bm_sentinel_run_a_real_workflow(self):
        """The two commands this item's register entry names explicitly
        (bm_project.py's 18 subcommands, bm_ledger.py), plus bm_sentinel.py
        (merged after the register entry was written, the same undocumented
        gap: a cmd_ dispatch and a main(), never wired to a console
        script). Runs each through a real project lifecycle against a
        fresh store, in an installed copy rather than a git checkout."""
        project_dir = os.path.join(self.tmp, "project")
        os.makedirs(project_dir, exist_ok=True)
        subprocess.run(["git", "init", "-q", "."], cwd=project_dir,
                       check=True, timeout=30, env=self.env)

        def run(name, *args):
            return subprocess.run(
                [self._script_path(name)] + list(args), cwd=project_dir,
                capture_output=True, text=True, timeout=30, env=self.env)

        r = run("bm-store", "init")
        self.assertEqual(0, r.returncode, r.stdout + r.stderr)

        r = run("bm-project", "start", "--project-id", "demo",
                "--name", "Demo", "--goal", "prove the install works",
                "--actor-type", "human", "--actor-name", "test")
        self.assertEqual(0, r.returncode, r.stdout + r.stderr)

        r = run("bm-project", "status", "--project-id", "demo")
        self.assertEqual(0, r.returncode, r.stdout + r.stderr)
        self.assertIn("demo", r.stdout)

        r = run("bm-ledger", "declare", "run1", "loopA",
                "--minutes", "10", "--tokens", "1000")
        self.assertEqual(0, r.returncode, r.stdout + r.stderr)

        r = run("bm-ledger", "report")
        self.assertEqual(0, r.returncode, r.stdout + r.stderr)

        r = run("bm-sentinel", "remember-knowledge", "--project", "demo",
                "--kind", "requirement", "--content", "prove the install",
                "--source", "test")
        self.assertEqual(0, r.returncode, r.stdout + r.stderr)
        memory_id = r.stdout.strip()
        self.assertTrue(memory_id, "remember-knowledge printed no id")

        r = run("bm-sentinel", "check", "--project", "demo",
                "--trigger", "resume")
        self.assertEqual(0, r.returncode, r.stdout + r.stderr)
        self.assertIn("MEMORY:", r.stdout)


if __name__ == "__main__":
    unittest.main()

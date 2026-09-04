#!/usr/bin/env python3
"""Tests for the Python hook entry points (tools/sbe_autosave.py,
tools/sbe_sessionstart.py) and the hook contract in hooks/hooks.json.

WHY THIS FILE EXISTS, separately from tools/test_sbe.py: the Windows
engineer's first-round report (2026-08-17, relayed by the founder) showed the
two POSIX sh hook scripts dying on Windows, silently for autosave (killed at
the harness's 60 s default with no snapshot and no log line) and structurally
for both (sh is not on the Windows PATH; the hooks only ran because Claude
Code spawned them through Git Bash). The fix is Python ports, and these are
their tests. They live in a new file because tools/test_sbe.py was fenced to
another writer when this lane opened; that file's existing autosave tests were
re-pointed at the ports when the fence lifted, and the sh files are gone.

Every test that runs the autosave entry does so through a subprocess with a
scratch repository and a scratch vault, the same way the harness runs it:
stdin carries the hook JSON payload, and the process must ALWAYS exit 0.
"""
import importlib.util
import json
import os
import subprocess
import sys
import tempfile
import time
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

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
AUTOSAVE = os.path.join(ROOT, "tools", "sbe_autosave.py")
SESSIONSTART = os.path.join(ROOT, "tools", "sbe_sessionstart.py")
HOOKS_JSON = os.path.join(ROOT, "hooks", "hooks.json")
REF_NS = "refs/brothersbe/autosave"


def _init_repo(path):
    for args in (["git", "init", "-q"],
                 ["git", "config", "user.email", "t@example.invalid"],
                 ["git", "config", "user.name", "t"]):
        subprocess.run(args, cwd=path, check=True, capture_output=True)


def _commit_all(path, msg="seed"):
    subprocess.run(["git", "add", "-A"], cwd=path, check=True, capture_output=True)
    subprocess.run(["git", "commit", "-q", "-m", msg], cwd=path, check=True,
                   capture_output=True)


def _autosave(repo, vault, mode, *extra, env_extra=None):
    """Run the autosave entry the way the hook does. Returns the completed
    process; the payload carries the repo as the hook cwd."""
    env = {k: v for k, v in os.environ.items() if not k.startswith("GIT_")}
    env["BROTHERSBE_VAULT"] = vault
    if env_extra:
        env.update(env_extra)
    payload = json.dumps({"cwd": repo})
    return subprocess.run(
        [sys.executable, AUTOSAVE, mode, *extra],
        input=payload, text=True, capture_output=True, cwd=repo, env=env)


def _autosave_ref_sha(repo):
    out = subprocess.run(["git", "for-each-ref", "--format=%(objectname)", REF_NS],
                         cwd=repo, capture_output=True, text=True).stdout.strip()
    return out.splitlines()[0] if out else ""


def _ref_tree_paths(repo, sha):
    out = subprocess.run(["git", "ls-tree", "-r", "--name-only", sha],
                         cwd=repo, capture_output=True, text=True).stdout
    return set(out.splitlines())


def _read(path):
    try:
        with open(path, encoding="utf-8", errors="replace") as f:
            return f.read()
    except OSError:
        return ""


class _Scratch(unittest.TestCase):
    def setUp(self):
        self.repo = tempfile.mkdtemp(prefix="sbeh-repo-")
        self.vault = tempfile.mkdtemp(prefix="sbeh-vault-")
        _init_repo(self.repo)

    @property
    def autosave_log(self):
        return _read(os.path.join(self.vault, "99-System", "telemetry",
                                  "autosave.log"))

    @property
    def excl_log(self):
        return _read(os.path.join(self.vault, "99-System", "telemetry",
                                  "autosave-exclusions.log"))


class TestPrecompactSnapshot(_Scratch):
    def test_precompact_saves_untracked_work_and_logs_the_save(self):
        with open(os.path.join(self.repo, "unlanded.txt"), "w") as f:
            f.write("work that only exists here\n")
        p = _autosave(self.repo, self.vault, "precompact")
        self.assertEqual(p.returncode, 0, p.stderr)
        sha = _autosave_ref_sha(self.repo)
        self.assertTrue(sha, "no autosave ref was written; log: %r" % self.autosave_log)
        self.assertIn("unlanded.txt", _ref_tree_paths(self.repo, sha))
        self.assertIn("saved", self.autosave_log)
        self.assertIn("precompact", self.autosave_log)

    def test_a_worktree_matching_head_says_so_instead_of_snapshotting(self):
        with open(os.path.join(self.repo, "a.txt"), "w") as f:
            f.write("landed\n")
        _commit_all(self.repo)
        p = _autosave(self.repo, self.vault, "precompact")
        self.assertEqual(p.returncode, 0, p.stderr)
        self.assertEqual(_autosave_ref_sha(self.repo), "")
        self.assertIn("matches HEAD", self.autosave_log)

    def test_secret_named_file_is_kept_out_with_its_reason(self):
        with open(os.path.join(self.repo, ".env"), "w") as f:
            f.write("TOKEN=abcdefghijklmnop\n")
        with open(os.path.join(self.repo, "ok.txt"), "w") as f:
            f.write("plain\n")
        p = _autosave(self.repo, self.vault, "precompact")
        self.assertEqual(p.returncode, 0, p.stderr)
        sha = _autosave_ref_sha(self.repo)
        self.assertTrue(sha)
        tree = _ref_tree_paths(self.repo, sha)
        self.assertIn("ok.txt", tree)
        self.assertNotIn(".env", tree)
        self.assertIn("secret-shaped names", self.excl_log)

    def test_content_matching_a_secret_shape_is_kept_out(self):
        with open(os.path.join(self.repo, "config.py"), "w") as f:
            f.write('KEY = "AKIA' + "A" * 16 + '"\n')
        with open(os.path.join(self.repo, "ok.txt"), "w") as f:
            f.write("plain\n")
        p = _autosave(self.repo, self.vault, "precompact")
        self.assertEqual(p.returncode, 0, p.stderr)
        sha = _autosave_ref_sha(self.repo)
        self.assertTrue(sha)
        tree = _ref_tree_paths(self.repo, sha)
        self.assertNotIn("config.py", tree)
        self.assertIn("content matched a secret shape", self.excl_log)

    def test_binary_content_is_kept_out_as_unscannable(self):
        with open(os.path.join(self.repo, "blob.bin"), "wb") as f:
            f.write(b"x\x00y" * 10)
        with open(os.path.join(self.repo, "ok.txt"), "w") as f:
            f.write("plain\n")
        p = _autosave(self.repo, self.vault, "precompact")
        self.assertEqual(p.returncode, 0, p.stderr)
        sha = _autosave_ref_sha(self.repo)
        self.assertTrue(sha)
        self.assertNotIn("blob.bin", _ref_tree_paths(self.repo, sha))
        self.assertIn("binary content", self.excl_log)

    def test_the_deadline_skips_loudly_and_writes_no_ref(self):
        """The Windows finding: a scan that cannot finish inside the hook
        window must say so and save nothing, never die in silence."""
        with open(os.path.join(self.repo, "unlanded.txt"), "w") as f:
            f.write("work\n")
        p = _autosave(self.repo, self.vault, "precompact",
                      env_extra={"BROTHERSBE_AUTOSAVE_DEADLINE_S": "0"})
        self.assertEqual(p.returncode, 0, p.stderr)
        self.assertEqual(_autosave_ref_sha(self.repo), "")
        self.assertIn("deadline", self.autosave_log)
        self.assertIn("NO-DATA", self.autosave_log)
        self.assertIn("nothing was saved", self.autosave_log)

    def test_every_path_exits_zero(self):
        not_a_repo = tempfile.mkdtemp(prefix="sbeh-plain-")
        for args, cwd in ((["precompact"], not_a_repo),
                          (["no-such-mode"], self.repo),
                          ([], self.repo)):
            env = {k: v for k, v in os.environ.items() if not k.startswith("GIT_")}
            env["BROTHERSBE_VAULT"] = self.vault
            p = subprocess.run([sys.executable, AUTOSAVE, *args],
                               input=json.dumps({"cwd": cwd}), text=True,
                               capture_output=True, cwd=cwd, env=env)
            self.assertEqual(p.returncode, 0,
                             "args=%r rc=%s stderr=%r" % (args, p.returncode, p.stderr))

    def test_production_repo_is_opt_in_and_the_skip_names_both_knobs(self):
        with open(os.path.join(self.repo, ".brothersbe-production"), "w") as f:
            f.write("declared\n")
        with open(os.path.join(self.repo, "unlanded.txt"), "w") as f:
            f.write("work\n")
        p = _autosave(self.repo, self.vault, "precompact")
        self.assertEqual(p.returncode, 0, p.stderr)
        self.assertEqual(_autosave_ref_sha(self.repo), "")
        self.assertIn("production", self.autosave_log)
        self.assertIn("BROTHERSBE_AUTOSAVE_PRODUCTION", self.autosave_log)


class TestTick(_Scratch):
    def test_tick_is_off_unless_opted_in(self):
        with open(os.path.join(self.repo, "w.txt"), "w") as f:
            f.write("w\n")
        p = _autosave(self.repo, self.vault, "tick", "sess1")
        self.assertEqual(p.returncode, 0, p.stderr)
        self.assertEqual(_autosave_ref_sha(self.repo), "")

    def test_tick_snapshots_on_the_interval(self):
        with open(os.path.join(self.repo, "w.txt"), "w") as f:
            f.write("w\n")
        env = {"BROTHERSBE_AUTOSAVE": "1", "BROTHERSBE_AUTOSAVE_EVERY": "2"}
        p1 = _autosave(self.repo, self.vault, "tick", "sess1", env_extra=env)
        self.assertEqual(p1.returncode, 0, p1.stderr)
        self.assertEqual(_autosave_ref_sha(self.repo), "", "snapshotted too early")
        p2 = _autosave(self.repo, self.vault, "tick", "sess1", env_extra=env)
        self.assertEqual(p2.returncode, 0, p2.stderr)
        self.assertTrue(_autosave_ref_sha(self.repo),
                        "no snapshot at the interval; log: %r" % self.autosave_log)


class TestRecover(_Scratch):
    def test_recover_checks_out_a_new_worktree_and_touches_nothing(self):
        live = os.path.join(self.repo, "live.txt")
        with open(live, "w") as f:
            f.write("unlanded\n")
        _autosave(self.repo, self.vault, "precompact")
        self.assertTrue(_autosave_ref_sha(self.repo))
        with open(live, "w") as f:
            f.write("changed after the snapshot\n")
        p = _autosave(self.repo, self.vault, "recover", self.repo)
        self.assertEqual(p.returncode, 0, p.stderr)
        self.assertIn("recovered snapshot", p.stdout)
        self.assertIn("never touched", p.stdout)
        with open(live) as f:
            self.assertEqual(f.read(), "changed after the snapshot\n")

    def test_recover_with_no_snapshot_says_so(self):
        p = _autosave(self.repo, self.vault, "recover", self.repo)
        self.assertEqual(p.returncode, 0, p.stderr)
        self.assertIn("no autosave found", p.stdout)


class TestHookContract(unittest.TestCase):
    """Every hook command must survive a Windows box: python3 only, no sh, and
    NEVER bash: C:\\WINDOWS\\system32\\bash.exe is WSL, where
    ${CLAUDE_PLUGIN_ROOT} does not resolve (Windows engineer report,
    2026-08-17)."""

    def _commands(self):
        with open(HOOKS_JSON) as f:
            data = json.load(f)
        return [h["command"] for group in data["hooks"].values()
                for entry in group for h in entry["hooks"]]

    def test_hook_commands_are_python3_only(self):
        cmds = self._commands()
        self.assertTrue(cmds)
        for c in cmds:
            self.assertTrue(c.startswith("python3 "),
                            "non-python3 hook command: %r" % c)
            # bash as an INTERPRETER is the W3 landmine (system32 bash.exe is
            # WSL); the substring alone is legal in file names such as
            # sbe_bash_write_guard.py.
            self.assertNotIn("bash -c", c)
            self.assertNotIn("sh -c", c)

    def test_precompact_autosave_declares_a_timeout(self):
        with open(HOOKS_JSON) as f:
            data = json.load(f)
        auto = [h for entry in data["hooks"]["PreCompact"] for h in entry["hooks"]
                if "sbe_autosave" in h["command"]]
        self.assertTrue(auto, "no autosave entry under PreCompact")
        self.assertTrue(all("timeout" in h for h in auto),
                        "PreCompact autosave hook declares no timeout; the "
                        "harness default (60 s on the engineer's build, 600 s "
                        "in current docs) is what killed it silently on the "
                        "Windows engineer's repo")


class _FakeCompleted(object):
    def __init__(self, stdout="", returncode=0):
        self.stdout, self.stderr, self.returncode = stdout, "", returncode


class _FakeGit(object):
    """A scripted stand-in for the gate module's own subprocess seam. Only the
    git invocations gate_approval reaches are answered; anything else gets an
    empty success, so a new call site fails a test here rather than silently
    reading this fixture's real machine."""

    def __init__(self, version, log_stdout, raw_commit, ssh_program=""):
        self.version = version
        self.log_stdout = log_stdout
        self.raw_commit = raw_commit
        self.ssh_program = ssh_program

    def run(self, cmd, **kwargs):
        if cmd[:2] == ["git", "--version"]:
            return _FakeCompleted("git version %s\n" % self.version)
        if "cat-file" in cmd:
            return _FakeCompleted(self.raw_commit)
        if "config" in cmd:
            if self.ssh_program:
                return _FakeCompleted(self.ssh_program + "\n", 0)
            return _FakeCompleted("", 1)
        if "log" in cmd:
            return _FakeCompleted(self.log_stdout)
        return _FakeCompleted("", 0)

    # subprocess module attributes some call sites reference
    PIPE = subprocess.PIPE
    SubprocessError = subprocess.SubprocessError


class _FakeShutil(object):
    def __init__(self, keygen_path):
        self._keygen = keygen_path

    def which(self, name):
        return self._keygen if name == "ssh-keygen" else None


class TestGateApprovalNamesTheWindowsBlockers(unittest.TestCase):
    """B1, the Bitbucket signed-trailer path: on the engineer's box git was
    2.33.0 and ssh-keygen absent from the shell PATH, so %G? could only ever
    answer E, and the E message said "import the signer's public key", which
    is not the fix on either count. The E branch stays NO-DATA (never a pass,
    never a block); these pin that the SENTENCE now names the real blocker.
    The gate module's own subprocess and shutil attributes are replaced, so
    the real logic runs end to end against a scripted environment."""

    SSH_ARMOR = ("tree 0000000000000000000000000000000000000000\n"
                 "author Dana Author <dana@example.com> 1700000000 +0000\n"
                 "committer Dana Author <dana@example.com> 1700000000 +0000\n"
                 "gpgsig -----BEGIN SSH SIGNATURE-----\n"
                 " U1NIU0lHfake\n"
                 " -----END SSH SIGNATURE-----\n"
                 "\nFix the widget\n")
    PGP_ARMOR = SSH_ARMOR.replace("SSH SIGNATURE", "PGP SIGNATURE")
    LOG_E = ("Fix the widget\n\nApproved-by: Rex Reviewer <rex@example.com>\n"
             "\n---\nE\n\nDana Author\ndana@example.com\n"
             "Dana Author\ndana@example.com\n")

    def _gate(self, fake_git, keygen_path):
        spec = importlib.util.spec_from_file_location(
            "sbe_gate_for_windows_msgs", os.path.join(ROOT, "tools", "sbe_gate.py"))
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        mod.subprocess = fake_git
        mod.shutil = _FakeShutil(keygen_path)
        return mod

    def _verdict(self, version, raw_commit, keygen_path, ssh_program=""):
        fake = _FakeGit(version, self.LOG_E, raw_commit, ssh_program=ssh_program)
        gate = self._gate(fake, keygen_path)
        with tempfile.TemporaryDirectory() as root:
            return gate.gate_approval(root)

    def test_old_git_with_an_ssh_signature_names_the_234_floor(self):
        verdict, message = self._verdict("2.33.0", self.SSH_ARMOR,
                                         "/usr/bin/ssh-keygen")
        self.assertEqual("NO-DATA", verdict)
        self.assertIn("2.34", message)
        self.assertIn("cannot verify an SSH signature", message)
        self.assertIn("Upgrade git", message)

    def test_missing_ssh_keygen_names_both_remedies(self):
        verdict, message = self._verdict("2.45.0", self.SSH_ARMOR, None)
        self.assertEqual("NO-DATA", verdict)
        self.assertIn("ssh-keygen", message)
        self.assertIn("gpg.ssh.program", message)
        self.assertIn("OpenSSH", message)

    def test_a_configured_ssh_program_keeps_the_generic_message(self):
        verdict, message = self._verdict("2.45.0", self.SSH_ARMOR, None,
                                         ssh_program="C:/Progra~1/OpenSSH/ssh-keygen.exe")
        self.assertEqual("NO-DATA", verdict)
        self.assertIn("import the signer's public key", message)

    def test_a_findable_ssh_keygen_keeps_the_generic_message(self):
        verdict, message = self._verdict("2.45.0", self.SSH_ARMOR,
                                         "/usr/bin/ssh-keygen")
        self.assertEqual("NO-DATA", verdict)
        self.assertIn("import the signer's public key", message)

    def test_a_non_ssh_signature_never_gets_the_ssh_remedies(self):
        """Old git plus a PGP-armored signature: the 2.34 floor and the
        ssh-keygen remedies are about the SSH format only, and printing them
        over a PGP signature would send a GPG user hunting the wrong fix,
        the exact defect this message change exists to close."""
        verdict, message = self._verdict("2.33.0", self.PGP_ARMOR, None)
        self.assertEqual("NO-DATA", verdict)
        self.assertNotIn("2.34", message)
        self.assertNotIn("ssh-keygen", message)
        self.assertIn("import the signer's public key", message)

    def test_a_message_quoting_ssh_armor_does_not_select_the_ssh_remedies(self):
        """The armor check reads the gpgsig header only. A PGP-signed commit
        whose MESSAGE quotes an SSH armor line (revert messages and docs
        commits do this legitimately) must keep the generic sentence. Two
        independent reviews found the substring form of the check firing on
        exactly this shape; calibrated red against it, this test then failed
        with the 2.34 floor sentence appearing for a PGP signature."""
        quoting = self.PGP_ARMOR.replace(
            "\nFix the widget\n",
            "\nFix the widget, quoting -----BEGIN SSH SIGNATURE----- in prose\n")
        self.assertIn("BEGIN SSH SIGNATURE", quoting,
                      "fixture must carry the phrase in its message half")
        verdict, message = self._verdict("2.33.0", quoting, None)
        self.assertEqual("NO-DATA", verdict)
        self.assertNotIn("2.34", message)
        self.assertNotIn("ssh-keygen", message)
        self.assertIn("import the signer's public key", message)

    def test_the_floor_outranks_the_missing_keygen(self):
        """Both blockers at once: the version floor is the deeper cause (no
        ssh-keygen can help a git that cannot verify the format at all), so
        that is the sentence the reader gets."""
        verdict, message = self._verdict("2.33.0", self.SSH_ARMOR, None)
        self.assertEqual("NO-DATA", verdict)
        self.assertIn("2.34", message)


if __name__ == "__main__":
    unittest.main(verbosity=1)

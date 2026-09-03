#!/usr/bin/env python3
"""Suite for scripts/install.py and scripts/uninstall.py.

Every test here runs the real CLI as a subprocess against a FAKE HOME under a
temporary directory. Nothing in this file writes to the real ~/.claude, and
nothing imports the installer into this process, because an installer that is
only ever tested in-process is not tested at the layer a founder uses it.

The cases are the attack list from the loop spec, not a happy path with
decoration: existing user hooks, malformed JSON, a prior install, a symlinked
target, spaces and non-ASCII in paths, a partially failed install, and the one
that matters most, an install that reports success while the fence hook is not
actually wired.

Python 3.9, standard library only.

No em or en dashes anywhere in this file, its comments, or its output.
"""

import hashlib
import io
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
INSTALL = os.path.join(ROOT, "scripts", "install.py")
UNINSTALL = os.path.join(ROOT, "scripts", "uninstall.py")
DOCTOR = os.path.join(ROOT, "scripts", "doctor.py")
SHELL = os.path.join(ROOT, "scripts", "bm_shell.py")
HOOKS_JSON_PATH = os.path.join(ROOT, "hooks", "hooks.json")

EXIT_REFUSED = 4


def write_consented_config(dirpath):
    """Write a completed Loop 3 consent config plus a writable vault dir
    under dirpath, returning the config path. Doctor's consent and vault
    checks (D-3 checks 4 and 5) FAIL without one, which is their tested
    job; these fixtures exercise everything downstream of consent, so
    they consent first, the same order the QUICKSTART documents."""
    vault = os.path.join(dirpath, "vault")
    if not os.path.isdir(vault):
        os.makedirs(vault)
    cfg = os.path.join(dirpath, "consent.json")
    with io.open(cfg, "w", encoding="utf-8") as fh:
        json.dump({"setup_complete": True, "vault_path": vault,
                   "privacy_notice_version": "2026-08-01",
                   "installation_mode": "clone",
                   "security_mode": "standard"}, fh)
    return cfg


class InstallerCase(unittest.TestCase):

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="bm-install-test-")
        self.home = os.path.join(self.tmp, "home")
        self.claude = os.path.join(self.home, ".claude")
        os.makedirs(self.claude)
        self.settings = os.path.join(self.claude, "settings.json")
        self.target = os.path.join(self.claude, "skills", "brothermode")

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    # --- helpers -----------------------------------------------------------

    def run_install(self, *args, **kw):
        return self._run(INSTALL, args, kw)

    def run_uninstall(self, *args, **kw):
        return self._run(UNINSTALL, args, kw)

    def _run(self, script, args, kw):
        target = kw.pop("target", self.target)
        settings = kw.pop("settings", self.settings)
        env = dict(os.environ)
        env["HOME"] = self.home
        env.pop("BROTHERMODE_VAULT", None)
        env.update(kw.pop("env", {}))
        cmd = [sys.executable, script, "--target", target,
               "--settings", settings] + list(args)
        return subprocess.run(cmd, env=env, cwd=self.tmp,
                              stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                              universal_newlines=True, timeout=300)

    def write_settings(self, obj_or_text):
        text = obj_or_text if isinstance(obj_or_text, str) else \
            json.dumps(obj_or_text, indent=2)
        with io.open(self.settings, "w", encoding="utf-8") as fh:
            fh.write(text)
        return text

    def read_settings(self):
        return json.loads(self.raw_settings())

    def raw_settings(self):
        return self.read_text(self.settings)

    def read_text(self, path):
        with io.open(path, encoding="utf-8") as fh:
            return fh.read()

    def user_hook(self, marker="my-own-script.sh"):
        return {"hooks": [{"type": "command", "command": "sh /home/me/" + marker}]}

    def all_commands(self, settings, event):
        out = []
        for group in settings.get("hooks", {}).get(event, []):
            for h in group.get("hooks", []):
                out.append(h.get("command", ""))
        return out


class TestCleanInstall(InstallerCase):

    def test_wires_all_six_events_including_the_fence(self):
        """A1 (loop6 refuter finding): PreToolUse now carries TWO groups
        (the fence, plus the Bash-audit pre phase), and PostToolUse (the
        Bash-audit post phase) is wired for the first time on this path.
        This assertion used to require exactly ONE group per event,
        including PreToolUse; that was the bug (docs/HOOKS.md's own
        wording: "a clone install gets the fence hook but not the Bash
        audit hook"), so PreToolUse now asserts 2 and PostToolUse is added
        to the set of wired events, both tightened to the new true shape
        rather than weakened."""
        r = self.run_install()
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        s = self.read_settings()
        for event in ("SessionStart", "SessionEnd", "Stop", "PreCompact",
                      "PostToolUse"):
            self.assertIn(event, s["hooks"], "%s was not wired" % event)
            self.assertEqual(len(s["hooks"][event]), 1)
        self.assertIn("PreToolUse", s["hooks"], "PreToolUse was not wired")
        self.assertEqual(len(s["hooks"]["PreToolUse"]), 2,
                         "PreToolUse must carry both the fence group and "
                         "the Bash-audit pre group")

    def test_fence_hook_group_has_matcher_and_a_file_that_exists(self):
        """The failure this guards: an install that prints success while the
        PreToolUse fence is absent or points at nothing. The fence is the only
        hook that can refuse a write, so a wired-but-dead fence is the same as
        no fence while looking installed."""
        self.run_install()
        s = self.read_settings()
        # PreToolUse now carries two groups (the fence, plus the Bash-audit
        # pre phase): select the fence one by its matcher rather than by
        # index, since index alone would tie this test to install.py's
        # internal group ORDER, which is not the contract worth pinning.
        fence_groups = [g for g in s["hooks"]["PreToolUse"]
                        if "Edit" in g.get("matcher", "")]
        self.assertEqual(len(fence_groups), 1, s["hooks"]["PreToolUse"])
        group = fence_groups[0]
        self.assertIn("matcher", group)
        self.assertIn("Edit", group["matcher"])
        cmd = group["hooks"][0]["command"]
        self.assertEqual(group["hooks"][0].get("timeout"), 10)
        path = cmd.split("python3 ", 1)[1].strip().strip("'")
        self.assertTrue(os.path.isfile(path),
                        "the fence hook command points at %s, which is not "
                        "there" % path)

    def test_bash_audit_pair_is_wired_pre_and_post_and_uninstall_removes_both(
            self):
        """A1 (loop6 refuter finding, THE BIG ONE): scripts/install.py used
        to wire only PreToolUse, so tools/bm_bash_audit.py's PostToolUse
        entrypoint was never invoked on the clone-install path and the
        Loop 6 Bash-write detection did not exist for those users. Both
        entrypoints must now be wired, both must name bm_bash_audit.py,
        and uninstall must remove both cleanly."""
        r = self.run_install()
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        s = self.read_settings()

        pre_bash = [g for g in s["hooks"]["PreToolUse"]
                   if g.get("matcher") == "Bash"]
        self.assertEqual(len(pre_bash), 1, s["hooks"]["PreToolUse"])
        pre_cmd = pre_bash[0]["hooks"][0]["command"]
        self.assertIn("bm_bash_audit.py", pre_cmd)
        self.assertIn(" pre", pre_cmd)
        path = pre_cmd.split("python3 ", 1)[1].rsplit(None, 1)[0].strip("'")
        self.assertTrue(os.path.isfile(path),
                        "the Bash-audit pre command points at %s, which is "
                        "not there" % path)

        post_bash = [g for g in s["hooks"].get("PostToolUse", [])
                    if g.get("matcher") == "Bash"]
        self.assertEqual(len(post_bash), 1, s["hooks"].get("PostToolUse"))
        post_cmd = post_bash[0]["hooks"][0]["command"]
        self.assertIn("bm_bash_audit.py", post_cmd)
        self.assertIn(" post", post_cmd)
        path = post_cmd.split("python3 ", 1)[1].rsplit(None, 1)[0].strip("'")
        self.assertTrue(os.path.isfile(path),
                        "the Bash-audit post command points at %s, which "
                        "is not there" % path)

        r2 = self.run_uninstall()
        self.assertEqual(r2.returncode, 0, r2.stdout + r2.stderr)
        after = self.read_settings()
        self.assertEqual(after.get("hooks", {}), {},
                         "uninstall left BrotherMode hook entries behind")

    def test_files_land_and_record_names_the_version(self):
        self.run_install()
        self.assertTrue(os.path.isfile(os.path.join(self.target, "SKILL.md")))
        self.assertTrue(os.path.isfile(
            os.path.join(self.target, "tools", "bm_fence_hook.py")))
        rec = json.loads(self.read_text(
            os.path.join(self.claude, "brothermode-install.json")))
        version = self.read_text(os.path.join(ROOT, "VERSION")).strip()
        self.assertEqual(rec["version"], version)
        self.assertEqual(rec["target"], self.target)

    def test_the_installed_copy_can_check_its_own_fence(self):
        """An install that ships no doctor leaves the founder with no way to
        tell a live fence from a wired-but-dead one, which is the failure the
        installer's own smoke test cannot see."""
        r = self.run_install()
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        doctor = os.path.join(self.target, "scripts", "doctor.py")
        self.assertTrue(os.path.isfile(doctor), "doctor.py was not installed")
        self.assertIn("doctor.py", r.stdout,
                      "the installer never told the founder to run it")
        env = dict(os.environ)
        env["HOME"] = self.home
        env.pop("BROTHERMODE_ROOT", None)
        env.pop("BM_FENCE_STRICT", None)
        env["BROTHERME_CONFIG"] = write_consented_config(self.tmp)
        d = subprocess.run(
            [sys.executable, doctor, "--settings", self.settings, "--json"],
            env=env, cwd=self.tmp, stdout=subprocess.PIPE,
            stderr=subprocess.PIPE, universal_newlines=True, timeout=300)
        # This test's subject is the FENCE check of the installed copy, so
        # it asserts that check by name from the machine output. Overall
        # exit 0 is deliberately NOT demanded here: an install copied from
        # a mid-development tree cannot match the release CHECKSUMS.sha256
        # (check 9 measures release integrity), and demanding it would make
        # this test red on every commit between release cuts.
        report = json.loads(d.stdout)
        fence = [c for c in report["checks"] if c["key"] == "fence"]
        self.assertEqual(len(fence), 1, d.stdout)
        self.assertEqual(fence[0]["status"], "PASS", d.stdout + d.stderr)

    def test_machine_state_is_not_copied(self):
        for name in (".git", "__pycache__", "threads"):
            self.assertFalse(os.path.exists(os.path.join(self.target, name)))
        self.run_install()
        for name in (".git", "__pycache__", "threads", ".brothermode"):
            self.assertFalse(os.path.exists(os.path.join(self.target, name)),
                             "%s was copied into the install" % name)

    def test_no_hooks_flag_leaves_settings_absent(self):
        r = self.run_install("--no-hooks")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertFalse(os.path.exists(self.settings))
        self.assertTrue(os.path.isfile(os.path.join(self.target, "SKILL.md")))


class TestHooksJsonAgreesWithInstaller(InstallerCase):
    """C-07 (2026-08-03): hooks/hooks.json and scripts/install.py's
    hook_groups() are two hand-maintained copies of one hook wiring, and they
    had drifted. hooks.json sets an explicit timeout and statusMessage on every
    group; the installer's shared _group() helper never set statusMessage
    anywhere, and set no timeout at all on SessionStart, SessionEnd, Stop or
    PreCompact. So the two documented install paths produced different
    effective configurations, and nothing compared them. This is the same drift
    class the packaging suite already guards for pyproject.toml: compare field
    by field rather than trusting two files to agree."""

    def _manifest_groups(self):
        with io.open(HOOKS_JSON_PATH, encoding="utf-8") as fh:
            return json.load(fh)["hooks"]

    def test_every_group_agrees_on_timeout_and_status_message(self):
        r = self.run_install()
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        installed = self.read_settings()["hooks"]
        manifest = self._manifest_groups()
        mismatches = []
        for event, groups in manifest.items():
            installed_groups = installed.get(event, [])
            for want in groups:
                matcher = want.get("matcher")
                got = next((g for g in installed_groups
                            if g.get("matcher") == matcher), None)
                if got is None:
                    mismatches.append(
                        "%s[%s]: hooks.json wires this group; the installed "
                        "settings.json has no matching group"
                        % (event, matcher))
                    continue
                want_entry = want["hooks"][0]
                got_entry = got["hooks"][0]
                for field in ("timeout", "statusMessage"):
                    w = want_entry.get(field)
                    g = got_entry.get(field)
                    if w != g:
                        mismatches.append(
                            "%s[%s].%s: hooks.json=%r, installer=%r"
                            % (event, matcher, field, w, g))
        self.assertEqual([], mismatches, "\n".join(mismatches))


class TestRefusals(InstallerCase):

    def test_second_install_is_refused_and_changes_nothing(self):
        self.run_install()
        before = self.raw_settings()
        r = self.run_install()
        self.assertEqual(r.returncode, EXIT_REFUSED, r.stdout + r.stderr)
        self.assertIn("--upgrade", r.stderr)
        self.assertEqual(self.raw_settings(), before,
                         "a refused install still rewrote settings.json")

    def test_malformed_settings_are_refused_byte_for_byte(self):
        broken = '{\n  "hooks": {\n    "Stop": [ ,,, ]\n'
        self.write_settings(broken)
        r = self.run_install()
        self.assertEqual(r.returncode, EXIT_REFUSED, r.stdout + r.stderr)
        self.assertIn("not valid JSON", r.stderr)
        self.assertEqual(self.raw_settings(), broken,
                         "a broken settings.json was rewritten; that destroys "
                         "whatever the user was editing")

    def test_settings_whose_top_level_is_a_list_is_refused(self):
        self.write_settings("[1, 2, 3]")
        r = self.run_install()
        self.assertEqual(r.returncode, EXIT_REFUSED, r.stdout + r.stderr)
        self.assertEqual(self.raw_settings(), "[1, 2, 3]")

    def test_hooks_key_of_the_wrong_type_is_refused(self):
        raw = self.write_settings({"hooks": "please"})
        r = self.run_install()
        self.assertEqual(r.returncode, EXIT_REFUSED, r.stdout + r.stderr)
        self.assertEqual(self.raw_settings(), raw)

    def test_non_empty_foreign_target_is_refused(self):
        os.makedirs(self.target)
        with io.open(os.path.join(self.target, "important.txt"), "w") as fh:
            fh.write("mine")
        r = self.run_install()
        self.assertEqual(r.returncode, EXIT_REFUSED, r.stdout + r.stderr)
        self.assertEqual(self.read_text(os.path.join(self.target, "important.txt")),
                         "mine")


class TestUpgrade(InstallerCase):

    def test_upgrade_is_idempotent(self):
        self.run_install()
        first = self.read_settings()
        r = self.run_install("--upgrade")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        second = self.read_settings()
        self.assertEqual(first, second,
                         "re-running the installer changed the configuration")
        for event in ("SessionStart", "PostToolUse"):
            self.assertEqual(len(second["hooks"][event]), 1,
                             "%s was duplicated by the upgrade" % event)
        # PreToolUse carries two of our own groups (fence, Bash-audit pre);
        # idempotence means still exactly two after re-running, not one.
        self.assertEqual(len(second["hooks"]["PreToolUse"]), 2,
                         "PreToolUse was duplicated (or dropped a group) "
                         "by the upgrade")

    def test_upgrade_replaces_a_stale_entry_rather_than_stacking(self):
        stale = {"hooks": {"SessionEnd": [{"hooks": [{
            "type": "command",
            "command": "python3 " + os.path.join(self.target, "tools",
                                                 "bm_telemetry.py") + " OLD-ARG",
        }]}]}}
        self.write_settings(stale)
        r = self.run_install("--upgrade")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        cmds = self.all_commands(self.read_settings(), "SessionEnd")
        self.assertEqual(len(cmds), 1)
        self.assertNotIn("OLD-ARG", cmds[0])

    def test_a_backup_of_the_previous_settings_is_kept(self):
        self.write_settings({"model": "opus", "hooks": {}})
        self.run_install()
        backups = [n for n in os.listdir(self.claude)
                   if n.startswith("settings.json.brothermode-backup-")]
        self.assertEqual(len(backups), 1, os.listdir(self.claude))
        old = json.loads(self.read_text(os.path.join(self.claude, backups[0])))
        self.assertEqual(old["model"], "opus")


class TestUserHooksSurvive(InstallerCase):

    def test_unrelated_hooks_and_keys_survive_install_and_uninstall(self):
        original = {
            "model": "sonnet",
            "env": {"MY_VAR": "1"},
            "hooks": {
                "SessionStart": [self.user_hook("theirs-start.sh")],
                "PostToolUse": [self.user_hook("theirs-post.sh")],
            },
        }
        self.write_settings(original)
        self.assertEqual(self.run_install().returncode, 0)
        s = self.read_settings()
        self.assertEqual(s["model"], "sonnet")
        self.assertEqual(s["env"], {"MY_VAR": "1"})
        self.assertIn("theirs-start.sh", " ".join(
            self.all_commands(s, "SessionStart")))
        self.assertEqual(len(s["hooks"]["SessionStart"]), 2)
        # PostToolUse now also gets our own Bash-audit post group (A1
        # fix), alongside the user's own pre-existing group: 2, not 1.
        self.assertEqual(len(s["hooks"]["PostToolUse"]), 2)
        self.assertIn("theirs-post.sh", " ".join(
            self.all_commands(s, "PostToolUse")))

        self.assertEqual(self.run_uninstall().returncode, 0)
        after = self.read_settings()
        self.assertEqual(after["hooks"]["SessionStart"],
                         original["hooks"]["SessionStart"])
        self.assertEqual(after["hooks"]["PostToolUse"],
                         original["hooks"]["PostToolUse"])
        self.assertEqual(after["model"], "sonnet")

    def test_a_group_the_user_shares_with_us_is_never_deleted(self):
        """The destructive-merge case. If the user has put their own hook into
        the SAME matcher group as ours, removing the group would take their
        hook with it. Ownership therefore requires EVERY command in the group
        to be ours."""
        shared = {"hooks": {"Stop": [{"hooks": [
            {"type": "command",
             "command": "python3 " + os.path.join(self.target, "tools",
                                                  "bm_telemetry.py") + " stop-warn"},
            {"type": "command", "command": "sh /home/me/mine.sh"},
        ]}]}}
        self.write_settings(shared)
        self.assertEqual(self.run_install("--upgrade").returncode, 0)
        self.assertEqual(self.run_uninstall().returncode, 0)
        cmds = self.all_commands(self.read_settings(), "Stop")
        self.assertIn("sh /home/me/mine.sh", cmds,
                      "the user's hook was deleted as collateral")

    def test_a_hook_pointing_at_a_different_install_is_left_alone(self):
        other = os.path.join(self.tmp, "other-brothermode")
        foreign = {"hooks": {"Stop": [{"hooks": [{
            "type": "command",
            "command": "python3 " + os.path.join(other, "tools",
                                                 "bm_telemetry.py") + " stop-warn",
        }]}]}}
        self.write_settings(foreign)
        self.assertEqual(self.run_install().returncode, 0)
        self.assertEqual(self.run_uninstall().returncode, 0)
        cmds = self.all_commands(self.read_settings(), "Stop")
        self.assertEqual(len(cmds), 1)
        self.assertIn(other, cmds[0])


class TestOwnershipBoundaries(InstallerCase):
    """Ownership was a substring test, and every one of these got past it.

    All four were reproduced against the previous commit before this class
    existed: two installations whose paths share a prefix, an install path
    containing an apostrophe, a user hook that chains their script after ours,
    and a user script whose name contains one of our tool names."""

    def all_hook_commands(self, settings):
        out = []
        for event in settings.get("hooks", {}):
            out.extend(self.all_commands(settings, event))
        return out

    def test_an_install_sharing_a_path_prefix_is_not_claimed_as_ours(self):
        # 8, not the old 5 (A1 fix) or 7 (row V1, 2026-09-02): SessionStart,
        # SessionEnd, Stop, PreCompact, PreToolUse-fence, PreToolUse-vault-
        # recall, PreToolUse-bash-audit, PostToolUse-bash-audit, every one
        # of which names the target path.
        long_target = self.target + "-work"
        r = self.run_install(target=long_target)
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        before = [c for c in self.all_hook_commands(self.read_settings())
                  if long_target in c]
        self.assertEqual(len(before), 8)

        r = self.run_install("--upgrade")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        after = [c for c in self.all_hook_commands(self.read_settings())
                 if long_target in c]
        self.assertEqual(len(after), 8,
                         "installing at a prefix path deleted the other "
                         "installation's hooks")

    def test_uninstall_at_a_prefix_path_removes_nothing_and_says_so(self):
        self.assertEqual(self.run_install().returncode, 0)
        raw_before = self.raw_settings()
        record = os.path.join(self.claude, "brothermode-install.json")
        self.assertTrue(os.path.exists(record))

        prefix_target = os.path.join(self.claude, "skills", "brother")
        r = self.run_uninstall(target=prefix_target)
        self.assertEqual(r.returncode, 4, r.stdout + r.stderr)
        self.assertEqual(self.raw_settings(), raw_before,
                         "a uninstall aimed at a prefix path edited settings")
        self.assertTrue(os.path.exists(record),
                        "the install record was deleted by a run that removed "
                        "no hooks")
        self.assertIn("refusing to finish", r.stderr)

    def test_an_apostrophe_in_the_install_path_survives_the_round_trip(self):
        target = os.path.join(self.tmp, "Repertoire d'installation", "brothermode")
        r = self.run_install(target=target)
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        # 8, not the old 5 (A1 fix) or 7 (row V1, 2026-09-02): see the
        # prefix-path test above.
        self.assertEqual(len(self.all_hook_commands(self.read_settings())), 8)

        r = self.run_uninstall(target=target)
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertEqual(self.all_hook_commands(self.read_settings()), [],
                         "uninstall could not recognise the hooks the "
                         "installer had just written")

    def test_a_user_hook_that_chains_their_script_after_ours_is_kept(self):
        ours = "python3 " + os.path.join(self.target, "tools", "bm_fence_hook.py")
        wrapped = ours + " && python3 /home/me/my_audit.py"
        self.write_settings({"hooks": {"PreToolUse": [
            {"matcher": "Write", "hooks": [
                {"type": "command", "command": wrapped}]}]}})
        r = self.run_install("--upgrade")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertIn("my_audit.py", " ".join(
            self.all_hook_commands(self.read_settings())),
            "the user's own auditor was deleted with the hook that wrapped ours")

    def test_a_user_script_named_like_one_of_ours_next_door_is_kept(self):
        theirs = os.path.join(self.claude, "skills", "brothermode-mine")
        os.makedirs(theirs)
        cmd = "python3 " + os.path.join(theirs, "my_bm_fence_hook.py")
        self.write_settings({"hooks": {"PreToolUse": [
            {"matcher": "Write", "hooks": [{"type": "command", "command": cmd}]}]}})
        r = self.run_install("--upgrade")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertIn("my_bm_fence_hook.py", " ".join(
            self.all_hook_commands(self.read_settings())))


class TestSymlinkedSettings(InstallerCase):
    """A dotfile-managed settings.json is a symlink into a tracked repo."""

    def link_settings(self):
        real = os.path.join(self.tmp, "dotfiles", "claude-settings.json")
        os.makedirs(os.path.dirname(real))
        with io.open(real, "w", encoding="utf-8") as fh:
            fh.write(json.dumps({"model": "opus"}, indent=2))
        try:
            os.symlink(real, self.settings)
        except (OSError, NotImplementedError, AttributeError):
            self.skipTest("this filesystem does not support symlinks")
        return real

    def test_the_link_survives_and_the_real_file_is_what_changes(self):
        real = self.link_settings()
        r = self.run_install()
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertTrue(os.path.islink(self.settings),
                        "settings.json was replaced by a regular file, "
                        "detaching it from the dotfiles repository")
        written = json.loads(self.read_text(real))
        self.assertEqual(written["model"], "opus")
        # 6 top-level hook keys, not the old 5 (A1 fix): PostToolUse is now
        # wired alongside SessionStart, SessionEnd, Stop, PreCompact and
        # PreToolUse.
        self.assertEqual(len(written.get("hooks", {})), 6)
        self.assertIn("is a symlink", r.stdout)

        r = self.run_uninstall()
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertTrue(os.path.islink(self.settings))
        self.assertNotIn("hooks", json.loads(self.read_text(real)))


class TestDryRun(InstallerCase):

    def test_dry_run_writes_absolutely_nothing(self):
        raw = self.write_settings({"model": "opus"})
        r = self.run_install("--dry-run")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertIn("dry-run", r.stdout)
        self.assertEqual(self.raw_settings(), raw)
        self.assertFalse(os.path.exists(self.target))
        self.assertEqual(
            sorted(os.listdir(self.claude)), ["settings.json"],
            "dry-run left something behind in %s" % self.claude)

    def test_dry_run_uninstall_changes_nothing(self):
        self.run_install()
        raw = self.raw_settings()
        r = self.run_uninstall("--dry-run")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertEqual(self.raw_settings(), raw)
        self.assertTrue(os.path.exists(
            os.path.join(self.claude, "brothermode-install.json")))


class TestUninstall(InstallerCase):

    def test_uninstall_removes_our_entries_and_the_record(self):
        self.run_install()
        r = self.run_uninstall()
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        s = self.read_settings()
        self.assertEqual(s.get("hooks", {}), {})
        self.assertFalse(os.path.exists(
            os.path.join(self.claude, "brothermode-install.json")))

    def test_uninstall_leaves_the_files_unless_asked(self):
        self.run_install()
        self.run_uninstall()
        self.assertTrue(os.path.isfile(os.path.join(self.target, "SKILL.md")))

    def test_remove_files_deletes_only_a_real_checkout(self):
        self.run_install()
        self.run_uninstall("--remove-files")
        self.assertFalse(os.path.exists(self.target))

    def test_remove_files_refuses_a_directory_that_is_not_ours(self):
        os.makedirs(self.target)
        with io.open(os.path.join(self.target, "notes.txt"), "w") as fh:
            fh.write("mine")
        r = self.run_uninstall("--remove-files")
        self.assertEqual(r.returncode, EXIT_REFUSED, r.stdout + r.stderr)
        self.assertTrue(os.path.isfile(os.path.join(self.target, "notes.txt")))

    def test_the_vault_is_never_deleted_and_is_named_in_the_output(self):
        vault = os.path.join(self.home, "BrotherModeVault")
        os.makedirs(vault)
        with io.open(os.path.join(vault, "Home.md"), "w") as fh:
            fh.write("mine")
        self.run_install()
        r = self.run_uninstall("--remove-files",
                               env={"BROTHERMODE_VAULT": vault})
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertTrue(os.path.isfile(os.path.join(vault, "Home.md")),
                        "the uninstaller deleted the vault")
        self.assertIn(vault, r.stdout)

    def test_uninstall_without_a_record_or_target_refuses_to_guess(self):
        self.run_install()
        os.unlink(os.path.join(self.claude, "brothermode-install.json"))
        env = dict(os.environ)
        env["HOME"] = self.home
        r = subprocess.run(
            [sys.executable, UNINSTALL, "--settings", self.settings],
            env=env, cwd=self.tmp, stdout=subprocess.PIPE,
            stderr=subprocess.PIPE, universal_newlines=True, timeout=120)
        self.assertEqual(r.returncode, EXIT_REFUSED, r.stdout + r.stderr)
        self.assertIn("--target", r.stderr)


class TestAwkwardPaths(InstallerCase):

    def test_spaces_and_non_ascii_in_the_target_path(self):
        target = os.path.join(self.tmp, "My Skills", "brothermode café")
        r = self.run_install(target=target)
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        s = self.read_settings()
        cmd = s["hooks"]["PreCompact"][0]["hooks"][0]["command"]
        # The whole point of quoting: the path must survive as ONE shell word.
        self.assertIn("'", cmd)
        r2 = self.run_uninstall(target=target)
        self.assertEqual(r2.returncode, 0, r2.stdout + r2.stderr)
        self.assertEqual(self.read_settings().get("hooks", {}), {})

    def test_installing_into_a_symlinked_directory_is_recognized(self):
        real = os.path.join(self.tmp, "real-install")
        link = os.path.join(self.tmp, "linked-install")
        os.makedirs(real)
        try:
            os.symlink(real, link)
        except (OSError, NotImplementedError):
            self.skipTest("this filesystem does not support symlinks")
        r = self.run_install(target=link)
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertTrue(os.path.isfile(os.path.join(real, "SKILL.md")),
                        "the copy did not follow the symlink to the real dir")
        r2 = self.run_install(target=link)
        self.assertEqual(r2.returncode, EXIT_REFUSED,
                         "a second install through the symlink was not refused")

    def test_running_the_installer_from_inside_the_install_directory(self):
        """The documented path in QUICKSTART: the user already cloned into
        ~/.claude/skills/brothermode and runs the installer from there. Source
        and target are then the same place and nothing should be copied."""
        os.makedirs(os.path.dirname(self.target))
        # ".claude" joined this list on 2026-08-16 after a BrotherSBE
        # architecture review found the real cause of three starved gate runs
        # in one night: .claude/worktrees held 678 MB of agent worktrees on
        # this machine, and this copytree walked all of it, silently and
        # untimed, inside a test method that then runs a 300 second
        # subprocess. The 900 second silence budget was never the problem
        # (the worst declared timeout here totals 360s); gate cost was
        # coupled to local scratch volume, so the same suite passed alone
        # and starved under load. An install fixture has no business
        # carrying the client's own working directories.
        shutil.copytree(ROOT, self.target,
                        ignore=shutil.ignore_patterns(
                            ".git", "__pycache__", ".brothermode", "threads",
                            ".claude"))
        r = subprocess.run(
            [sys.executable, os.path.join(self.target, "scripts", "install.py"),
             "--settings", self.settings],
            env=dict(os.environ, HOME=self.home), cwd=self.target,
            stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            universal_newlines=True, timeout=300)
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertIn("same directory", r.stdout)
        s = self.read_settings()
        # 2, not the old 1 (A1 fix): the fence group plus the Bash-audit
        # pre group.
        self.assertEqual(len(s["hooks"]["PreToolUse"]), 2)
        fence_groups = [g for g in s["hooks"]["PreToolUse"]
                        if "Edit" in g.get("matcher", "")]
        self.assertEqual(len(fence_groups), 1)
        self.assertIn(self.target, fence_groups[0]["hooks"][0]["command"])


class TestPartialPriorInstall(InstallerCase):

    def test_hooks_present_but_files_missing_is_refused_then_repaired(self):
        """A prior install that failed halfway: the hook entries landed, the
        directory did not. The refusal must fire (there IS a prior install),
        and --upgrade must then repair it rather than stacking duplicates."""
        self.run_install()
        shutil.rmtree(self.target)
        r = self.run_install()
        self.assertEqual(r.returncode, EXIT_REFUSED, r.stdout + r.stderr)
        r2 = self.run_install("--upgrade")
        self.assertEqual(r2.returncode, 0, r2.stdout + r2.stderr)
        self.assertTrue(os.path.isfile(
            os.path.join(self.target, "tools", "bm_fence_hook.py")))
        # 2, not the old 1 (A1 fix): see the equivalent assertion above.
        self.assertEqual(len(self.read_settings()["hooks"]["PreToolUse"]), 2)

    def test_a_broken_install_is_reported_as_not_done(self):
        """A prior install left the directory unreadable. The installer must
        report the failure rather than printing a success summary anyway."""
        self.run_install()
        tools = os.path.join(self.target, "tools")
        os.unlink(os.path.join(tools, "bm_fence_hook.py"))
        os.chmod(tools, 0o500)
        try:
            r = self.run_install("--upgrade")
        finally:
            os.chmod(tools, 0o700)
        if r.returncode == 0:
            self.skipTest("this filesystem or user ignores directory mode 0500")
        self.assertNotEqual(r.returncode, 0)
        self.assertNotIn("Still manual", r.stdout,
                         "a failed install still printed its success summary")


class TestSmokeTestItself(unittest.TestCase):
    """Calibration of the guard, not of the happy path.

    scripts/install.py only claims success after smoke_test() returns no
    problems. If smoke_test cannot FAIL, that claim is decoration, so these
    reinject the two failures it exists to catch: a hook target that is not on
    disk, and a fence hook that is on disk but does not run."""

    def setUp(self):
        import importlib.util
        spec = importlib.util.spec_from_file_location("bm_install_under_test",
                                                      INSTALL)
        self.mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(self.mod)
        self.tmp = tempfile.mkdtemp(prefix="bm-smoke-calib-")
        self.tools = os.path.join(self.tmp, "tools")
        os.makedirs(self.tools)

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _copy_real_tools(self):
        for name in self.mod.OWNED_TOOLS:
            shutil.copy2(os.path.join(HERE, name), os.path.join(self.tools, name))

    def test_passes_on_a_real_installation(self):
        self._copy_real_tools()
        self.assertEqual(self.mod.smoke_test(self.tmp), [])

    def test_fails_when_the_fence_hook_is_missing(self):
        self._copy_real_tools()
        os.unlink(os.path.join(self.tools, "bm_fence_hook.py"))
        problems = self.mod.smoke_test(self.tmp)
        self.assertTrue(problems)
        self.assertIn("bm_fence_hook.py", " ".join(problems))

    def test_fails_when_the_fence_hook_does_not_exit_zero(self):
        self._copy_real_tools()
        with io.open(os.path.join(self.tools, "bm_fence_hook.py"), "w",
                     encoding="utf-8") as fh:
            fh.write("import sys\nsys.stderr.write('boom\\n')\nsys.exit(1)\n")
        problems = self.mod.smoke_test(self.tmp)
        self.assertTrue(problems, "a fence hook that exits 1 was accepted")
        self.assertIn("exited 1", " ".join(problems))

    def test_passes_again_once_the_real_hook_is_restored(self):
        self._copy_real_tools()
        self.assertEqual(self.mod.smoke_test(self.tmp), [])


# ---------------------------------------------------------------------------
# scripts/doctor.py: is the fence WIRED, and is it LIVE?
#
# The installer's own smoke test runs the hook from an empty directory, where
# it fails open, so it proves the hook executes and nothing more. These tests
# are the calibration for the check that proves it REFUSES: pass on a healthy
# install, fail on each way the fence can be dead, pass again once restored.
# ---------------------------------------------------------------------------

class DoctorCase(unittest.TestCase):

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="bm-doctor-test-")
        self.settings = os.path.join(self.tmp, "settings.json")
        self.tools = os.path.join(self.tmp, "install", "tools")
        os.makedirs(self.tools)
        # test_all.py is named like a test file but is a runtime dependency
        # of bm_fence_hook.py's battery fence (_load_gate_module loads it
        # beside the hook to read the gate lock), so a real clone install
        # always ships it next to the hook. Excluding every "test_" name
        # here used to drop it from this throwaway install too, so the
        # simulated hook could never read gate-lock state and, in enforced
        # mode, denied the owner's own write with "the battery state could
        # not be established" even though a real install never hits that
        # path. Root cause, not the BM_FENCE_MODE env var: fixed by keeping
        # this one name out of the test-file exclusion.
        for name in sorted(os.listdir(HERE)):
            if name.endswith(".py") and (name == "test_all.py"
                                          or not name.startswith("test_")):
                shutil.copy2(os.path.join(HERE, name),
                             os.path.join(self.tools, name))
        self.fence = os.path.join(self.tools, "bm_fence_hook.py")
        self.consent_cfg = write_consented_config(self.tmp)
        # A private HOME for doctor's home-relative reads (duplicate-install
        # check, settings discovery). Made in setUp so the leaves-nothing-
        # behind test's before-snapshot already contains it; macOS drops a
        # Library dir inside any HOME a subprocess uses, and inside this
        # subdir that noise stays out of the top-level listing.
        self.home = os.path.join(self.tmp, "home")
        os.makedirs(self.home)

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def write_settings(self, obj):
        with io.open(self.settings, "w", encoding="utf-8") as fh:
            fh.write(json.dumps(obj, indent=2))

    def wired(self, command=None, matcher="Edit|Write|MultiEdit|NotebookEdit|Bash"):
        return {"hooks": {"PreToolUse": [{
            "matcher": matcher,
            "hooks": [{"type": "command",
                       "command": command or ("python3 " + self.fence),
                       "timeout": 10}]}]}}

    def run_doctor(self):
        env = dict(os.environ)
        env.pop("BROTHERMODE_ROOT", None)
        env.pop("BM_FENCE_STRICT", None)
        env["BROTHERME_CONFIG"] = self.consent_cfg
        env["HOME"] = self.home
        return subprocess.run(
            [sys.executable, DOCTOR, "--settings", self.settings],
            env=env, cwd=self.tmp, stdout=subprocess.PIPE,
            stderr=subprocess.PIPE, universal_newlines=True, timeout=300)

    def test_calibrated_1_healthy_install_passes(self):
        self.write_settings(self.wired())
        r = self.run_doctor()
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertIn("denied a foreign write", r.stdout)

    def test_loader_managed_copy_satisfies_the_fence_check(self):
        """Finalization run 2026-08-08: a BrotherMode copy under
        ~/.claude/skills/ is auto-loaded by Claude Code as a plugin and
        registers its own hooks/hooks.json, so an empty settings.json is
        not a dead fence. Doctor must find the loader-managed copy, say
        so, and prove liveness against that copy's own hook instead of
        failing on the missing settings block."""
        skills = os.path.join(self.home, ".claude", "skills", "brothermode")
        os.makedirs(os.path.join(skills, "hooks"))
        os.symlink(self.tools, os.path.join(skills, "tools"))
        with io.open(os.path.join(skills, "hooks", "hooks.json"), "w",
                     encoding="utf-8") as fh:
            fh.write(json.dumps({"hooks": {"PreToolUse": [{
                "matcher": "Edit|Write|MultiEdit|NotebookEdit|Bash",
                "hooks": [{
                    "type": "command",
                    "command": "python3 \"${CLAUDE_PLUGIN_ROOT}/tools/"
                               "bm_fence_hook.py\""}]}]}}))
        self.write_settings({"hooks": {"SessionStart": []}})
        r = self.run_doctor()
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertIn("auto-loaded", r.stdout)
        self.assertIn("denied a foreign write", r.stdout)

    def test_calibrated_2_missing_fence_hook_is_detected(self):
        """The required case from the loop spec: doctor notices that no fence
        is wired at all, which is exactly what every install before this one
        shipped."""
        self.write_settings({"hooks": {"SessionStart": []}})
        r = self.run_doctor()
        self.assertEqual(r.returncode, 1)
        self.assertIn("NO FENCE HOOK IS WIRED", r.stdout)

    def test_wired_at_a_path_that_does_not_exist_is_detected(self):
        self.write_settings(self.wired(
            command="python3 " + os.path.join(self.tmp, "gone",
                                              "bm_fence_hook.py")))
        r = self.run_doctor()
        self.assertEqual(r.returncode, 1)
        self.assertIn("not a file", r.stdout)

    def test_a_matcher_that_leaves_write_tools_ungated_is_detected(self):
        self.write_settings(self.wired(matcher="Edit"))
        r = self.run_doctor()
        self.assertEqual(r.returncode, 1)
        self.assertIn("UNGATED", r.stdout)

    def test_a_matcher_that_omits_only_edit_is_detected(self):
        """Regression, fix-round 2026-07-29. The coverage test used to be
        `tool not in matcher`, a SUBSTRING check, and 'Edit' is a substring of
        both 'MultiEdit' and 'NotebookEdit'. So this exact matcher, which
        leaves the primary write tool ungated, printed OK and exited 0. The
        older ungated test above passes either way, which is why it did not
        catch this: it drops names that are not substrings of the others."""
        self.write_settings(self.wired(matcher="Write|MultiEdit|NotebookEdit"))
        r = self.run_doctor()
        self.assertEqual(r.returncode, 1, r.stdout + r.stderr)
        self.assertIn("does not cover Edit", r.stdout)

    def test_a_matcher_that_is_not_a_valid_regex_is_detected(self):
        self.write_settings(self.wired(matcher="Edit|Write|(("))
        r = self.run_doctor()
        self.assertEqual(r.returncode, 1, r.stdout + r.stderr)
        self.assertIn("not a valid regular expression", r.stdout)

    def test_calibrated_4_a_hook_that_gates_only_edit_is_detected(self):
        """Regression, fix-round 2026-07-29. The simulation only ever sent
        tool_name 'Edit', so a hook whose WRITE_TOOLS set had lost Write,
        MultiEdit and NotebookEdit passed and reported the fence healthy while
        three of the four supported write tools went straight through."""
        with io.open(self.fence, encoding="utf-8") as fh:
            src = fh.read()
        cut = src.index("WRITE_TOOLS = frozenset((")
        end = src.index("))", cut) + 2
        with io.open(self.fence, "w", encoding="utf-8") as fh:
            fh.write(src[:cut] + 'WRITE_TOOLS = frozenset(("Edit",))'
                     + src[end:])
        self.write_settings(self.wired())
        r = self.run_doctor()
        self.assertEqual(r.returncode, 1, r.stdout + r.stderr)
        for tool in ("Write", "MultiEdit", "NotebookEdit"):
            self.assertIn("SIMULATION FAILED for %s" % tool, r.stdout)

        # Restore: the same install passes again, so the failure above was the
        # injected defect and not the harness.
        with io.open(self.fence, "w", encoding="utf-8") as fh:
            fh.write(src)
        self.assertEqual(self.run_doctor().returncode, 0)

    def test_calibrated_5_a_hook_that_bricks_by_exit_code_is_detected(self):
        """Regression, fix-round 2026-07-29. The owner half read stdout only.
        Claude Code's PreToolUse contract BLOCKS a call on exit code 2 with
        stderr, not only on deny JSON, so a hook that denied a foreign write
        correctly and exited 2 on every allowed write blocked the owner from
        their own claimed file while doctor printed OK and exited 0."""
        with io.open(self.fence, encoding="utf-8") as fh:
            src = fh.read()
        marker = ("    if decision is not None:\n"
                  "        _out(json.dumps(decision))\n"
                  "    return 0")
        self.assertIn(marker, src)
        with io.open(self.fence, "w", encoding="utf-8") as fh:
            fh.write(src.replace(marker,
                                 "    if decision is not None:\n"
                                 "        _out(json.dumps(decision))\n"
                                 "        return 0\n"
                                 "    return 2"))
        self.write_settings(self.wired())
        r = self.run_doctor()
        self.assertEqual(r.returncode, 1, r.stdout + r.stderr)
        self.assertIn("the hook exited 2", r.stdout)

        # Restore.
        with io.open(self.fence, "w", encoding="utf-8") as fh:
            fh.write(src)
        self.assertEqual(self.run_doctor().returncode, 0)

    def test_calibrated_3_a_hook_that_never_denies_is_detected(self):
        """The failure this whole command exists for: the fence is present,
        executable, exits 0 on every payload, and refuses nothing. The
        installer's smoke test passes on this. Doctor must not."""
        with io.open(self.fence, encoding="utf-8") as fh:
            src = fh.read()
        neutered = src.replace("return deny_payload(reason), notes",
                               "return None, notes")
        self.assertNotEqual(src, neutered, "the deny path was not found")
        with io.open(self.fence, "w", encoding="utf-8") as fh:
            fh.write(neutered)
        self.write_settings(self.wired())
        r = self.run_doctor()
        self.assertEqual(r.returncode, 1, r.stdout + r.stderr)
        self.assertIn("BLOCKED-WRITE SIMULATION FAILED", r.stdout)

        # Restore: the same install passes again, so the failure above was the
        # injected defect and not the harness.
        with io.open(self.fence, "w", encoding="utf-8") as fh:
            fh.write(src)
        self.assertEqual(self.run_doctor().returncode, 0)

    def test_a_hook_that_denies_everything_is_detected(self):
        """The opposite defect, and the reason doctor runs two halves: a hook
        that answers deny to everything blocks a foreign write AND the owner's
        own write. That is a brick, not a fence."""
        with io.open(self.fence, encoding="utf-8") as fh:
            src = fh.read()
        marker = "        return None, notes\n    except _FailOpen as e:"
        self.assertIn(marker, src)
        with io.open(self.fence, "w", encoding="utf-8") as fh:
            fh.write(src.replace(
                marker,
                "        return deny_payload('always deny'), notes\n"
                "    except _FailOpen as e:"))
        self.write_settings(self.wired())
        r = self.run_doctor()
        self.assertEqual(r.returncode, 1, r.stdout + r.stderr)
        self.assertIn("CALIBRATION FAILED", r.stdout)

    def test_the_simulation_leaves_nothing_behind(self):
        self.write_settings(self.wired())
        before = sorted(os.listdir(self.tmp))
        self.assertEqual(self.run_doctor().returncode, 0)
        self.assertEqual(sorted(os.listdir(self.tmp)), before,
                         "the simulation wrote into the working directory")

    def test_the_fail_open_limit_is_stated_in_the_output(self):
        """Acceptance criterion: no unqualified guarantee. A green doctor must
        say what it did not prove."""
        self.write_settings(self.wired())
        out = self.run_doctor().stdout
        self.assertIn("FAILS OPEN", out)
        self.assertIn("Bash is NOT gated", out)


class TestChecksumsDirtyTreeSkip(unittest.TestCase):
    """A9 (loop6 refuter finding): a dirty git working tree makes doctor's
    checksums check (check 9) SKIP rather than compare, and the run still
    exits 0 without --strict (SKIP is not a failure by default). The SKIP
    message must say, in one plain sentence, that the integrity check did
    NOT run, so a reader cannot mistake the SKIP for a PASS. Same fixture
    technique as tools/test_bm_consent.py's own dirty-tree checksums test:
    doctor.py resolves its own `root` from its own file location
    (os.path.dirname(os.path.dirname(__file__))), so a copy of it under a
    throwaway git repository lets this control exactly what git sees."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="bm-checksums-dirty-test-")
        self.home = os.path.join(self.tmp, "home")
        os.makedirs(self.home)
        vault = os.path.join(self.tmp, "vault")
        os.makedirs(vault)
        self.consent_cfg = os.path.join(self.tmp, "consent.json")
        with io.open(self.consent_cfg, "w", encoding="utf-8") as fh:
            json.dump({"setup_complete": True, "vault_path": vault,
                      "privacy_notice_version": "2026-08-01",
                      "installation_mode": "clone",
                      "security_mode": "standard"}, fh)

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_dirty_tree_skip_message_says_the_check_did_not_run(self):
        fake_root = os.path.join(self.tmp, "fake-install")
        fake_scripts = os.path.join(fake_root, "scripts")
        os.makedirs(fake_scripts)
        for name in ("doctor.py", "setup.py"):
            shutil.copy2(os.path.join(ROOT, "scripts", name),
                        os.path.join(fake_scripts, name))
        payload = os.path.join(fake_root, "payload.txt")
        with io.open(payload, "w", encoding="utf-8") as fh:
            fh.write("committed content\n")
        with io.open(payload, "rb") as fh:
            digest = hashlib.sha256(fh.read()).hexdigest()
        with io.open(os.path.join(fake_root, "CHECKSUMS.sha256"), "w",
                    encoding="utf-8") as fh:
            fh.write("%s  payload.txt\n" % digest)

        git_env = dict(os.environ)
        git_env.update({"GIT_AUTHOR_NAME": "t",
                        "GIT_AUTHOR_EMAIL": "t@example.com",
                        "GIT_COMMITTER_NAME": "t",
                        "GIT_COMMITTER_EMAIL": "t@example.com"})
        for cmd in (["git", "init"], ["git", "add", "-A"],
                    ["git", "commit", "-m", "initial"]):
            r = subprocess.run(cmd, cwd=fake_root, env=git_env,
                               stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                               universal_newlines=True, timeout=60)
            self.assertEqual(r.returncode, 0, r.stdout + r.stderr)

        # Dirty the tree WITHOUT committing: an ordinary live edit, not a
        # half-finished release checkout.
        with io.open(payload, "a", encoding="utf-8") as fh:
            fh.write("an uncommitted local edit\n")

        run_env = dict(os.environ)
        run_env.pop("BROTHERMODE_ROOT", None)
        run_env.pop("BM_FENCE_STRICT", None)
        run_env["BROTHERME_CONFIG"] = self.consent_cfg
        run_env["HOME"] = self.home
        r = subprocess.run(
            [sys.executable, os.path.join(fake_scripts, "doctor.py"), "--json"],
            cwd=fake_root, env=run_env, stdout=subprocess.PIPE,
            stderr=subprocess.PIPE, universal_newlines=True, timeout=300)
        report = json.loads(r.stdout)
        checksums = [c for c in report["checks"] if c["key"] == "checksums"]
        self.assertEqual(len(checksums), 1, r.stdout)
        self.assertEqual(checksums[0]["status"], "SKIP")
        self.assertIn(
            "did NOT run", checksums[0]["message"],
            "the SKIP message must say plainly that the integrity check "
            "did not run, so it cannot be misread as a pass")
        # SKIP alone must never fail the run (A8b's claim): a FULLY wired
        # install, where checksums is the only SKIP, still exits 0 on
        # exactly this SKIP, proven by
        # DoctorCase.test_calibrated_1_healthy_install_passes against this
        # actual checkout's own (routinely dirty) working tree. This
        # fixture is deliberately minimal (no settings.json, no fence) and
        # has other, unrelated FAILs of its own, so asserting the overall
        # exit code here would test the wrong thing.

    def test_a_dirty_linked_worktree_skips_exactly_like_a_normal_checkout(self):
        """The same dirty-tree SKIP, but in a LINKED worktree (created by
        git worktree add), where .git is a FILE holding a gitdir: pointer
        instead of a directory. doctor's tree-state helper used to accept
        only a .git DIRECTORY, so it reported 'cannot tell' for a worktree
        whose state git reports perfectly well, the SKIP guard never fired,
        and check 9 compared a live edited tree against a release manifest
        anyway. Every agent worktree on this project is a linked worktree,
        so that made the guard dead code exactly where it is used most."""
        fake_root = os.path.join(self.tmp, "wt-origin")
        fake_scripts = os.path.join(fake_root, "scripts")
        os.makedirs(fake_scripts)
        for name in ("doctor.py", "setup.py"):
            shutil.copy2(os.path.join(ROOT, "scripts", name),
                        os.path.join(fake_scripts, name))
        payload = os.path.join(fake_root, "payload.txt")
        with io.open(payload, "w", encoding="utf-8") as fh:
            fh.write("committed content\n")
        with io.open(payload, "rb") as fh:
            digest = hashlib.sha256(fh.read()).hexdigest()
        with io.open(os.path.join(fake_root, "CHECKSUMS.sha256"), "w",
                    encoding="utf-8") as fh:
            fh.write("%s  payload.txt\n" % digest)

        git_env = dict(os.environ)
        git_env.update({"GIT_AUTHOR_NAME": "t",
                        "GIT_AUTHOR_EMAIL": "t@example.com",
                        "GIT_COMMITTER_NAME": "t",
                        "GIT_COMMITTER_EMAIL": "t@example.com"})
        linked = os.path.join(self.tmp, "wt-linked")
        for cmd in (["git", "init"], ["git", "add", "-A"],
                    ["git", "commit", "-m", "initial"],
                    ["git", "worktree", "add", linked, "HEAD", "--detach"]):
            r = subprocess.run(cmd, cwd=fake_root, env=git_env,
                               stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                               universal_newlines=True, timeout=60)
            self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertTrue(
            os.path.isfile(os.path.join(linked, ".git")),
            "the fixture is only meaningful if .git here is a FILE, which "
            "is what git worktree add creates")

        # Dirty the LINKED worktree the way a founder does: edit a tracked
        # file and do not commit it.
        with io.open(os.path.join(linked, "payload.txt"), "a",
                    encoding="utf-8") as fh:
            fh.write("an uncommitted local edit\n")

        run_env = dict(os.environ)
        run_env.pop("BROTHERMODE_ROOT", None)
        run_env.pop("BM_FENCE_STRICT", None)
        run_env["BROTHERME_CONFIG"] = self.consent_cfg
        run_env["HOME"] = self.home
        r = subprocess.run(
            [sys.executable, os.path.join(linked, "scripts", "doctor.py"),
             "--json"],
            cwd=linked, env=run_env, stdout=subprocess.PIPE,
            stderr=subprocess.PIPE, universal_newlines=True, timeout=300)
        report = json.loads(r.stdout)
        checksums = [c for c in report["checks"] if c["key"] == "checksums"]
        self.assertEqual(len(checksums), 1, r.stdout)
        self.assertEqual(
            checksums[0]["status"], "SKIP",
            "a dirty LINKED worktree must SKIP the integrity check exactly "
            "as a dirty ordinary checkout does, not compare against the "
            "manifest and report the founder's own uncommitted edit as a "
            "tampered file: %s" % checksums[0]["message"])
        self.assertIn(
            "did NOT run", checksums[0]["message"],
            "the SKIP message must say plainly that the integrity check "
            "did not run, so it cannot be misread as a pass")


class TestInstallIdentityStampOnARealCheckout(unittest.TestCase):
    """docs/QUICKSTART.md's Path 2 is: clone into ~/.claude/skills/brothermode,
    then run that clone's own scripts/install.py from inside it (the exact
    shape test_running_the_installer_from_inside_the_install_directory above
    covers). Every existing check_install_identity-adjacent test, including
    that one, builds its fixture with shutil.copytree(..., ignore=[".git",
    ...]), so the fixture has no git history at all and INSTALLED-FROM is
    stamped "unknown": check 11 (install_identity) can only ever SKIP there,
    which means no test in this suite had ever driven it to PASS. This class
    gives the copied checkout its own real git history, the same technique
    TestChecksumsDirtyTreeSkip already uses, so install.py's source_commit()
    and doctor.py's own _git_head_commit() both read a real 40-character
    commit id, and proves the documented path actually clears the SKIP."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="bm-install-identity-test-")
        self.home = os.path.join(self.tmp, "home")
        self.claude = os.path.join(self.home, ".claude")
        os.makedirs(self.claude)
        self.settings = os.path.join(self.claude, "settings.json")
        self.target = os.path.join(self.claude, "skills", "brothermode")
        self.consent_cfg = write_consented_config(self.tmp)

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_a_clone_then_install_run_moves_check_11_from_skip_to_pass(self):
        os.makedirs(os.path.dirname(self.target))
        shutil.copytree(ROOT, self.target,
                        ignore=shutil.ignore_patterns(
                            ".git", "__pycache__", ".brothermode", "threads",
                            ".claude"))
        git_env = dict(os.environ)
        git_env.update({"GIT_AUTHOR_NAME": "t",
                        "GIT_AUTHOR_EMAIL": "t@example.com",
                        "GIT_COMMITTER_NAME": "t",
                        "GIT_COMMITTER_EMAIL": "t@example.com"})
        for cmd in (["git", "init"], ["git", "add", "-A"],
                    ["git", "commit", "-m", "initial"]):
            r = subprocess.run(cmd, cwd=self.target, env=git_env,
                               stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                               universal_newlines=True, timeout=60)
            self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        expected_commit = subprocess.run(
            ["git", "-C", self.target, "rev-parse", "HEAD"],
            stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            universal_newlines=True, timeout=30).stdout.strip()
        self.assertEqual(len(expected_commit), 40, expected_commit)

        # Doctor's own root, before install.py has run, has no
        # INSTALLED-FROM stamp: check 11 must SKIP, not PASS or FAIL, and
        # nothing above must be able to fake a PASS by coincidence.
        run_env = dict(os.environ)
        run_env.pop("BROTHERMODE_ROOT", None)
        run_env.pop("BM_FENCE_STRICT", None)
        run_env["BROTHERME_CONFIG"] = self.consent_cfg
        run_env["HOME"] = self.home
        before = subprocess.run(
            [sys.executable, os.path.join(self.target, "scripts", "doctor.py"),
             "--json"],
            cwd=self.target, env=run_env, stdout=subprocess.PIPE,
            stderr=subprocess.PIPE, universal_newlines=True, timeout=300)
        before_report = json.loads(before.stdout)
        before_identity = [c for c in before_report["checks"]
                           if c["key"] == "install_identity"]
        self.assertEqual(len(before_identity), 1, before.stdout)
        self.assertEqual(before_identity[0]["status"], "SKIP",
                         before_identity[0]["message"])

        # docs/QUICKSTART.md step 3: run the clone's OWN install.py, from
        # inside it, exactly as test_running_the_installer_from_inside_the_
        # install_directory does above.
        install_r = subprocess.run(
            [sys.executable, os.path.join(self.target, "scripts", "install.py"),
             "--settings", self.settings],
            env=dict(os.environ, HOME=self.home), cwd=self.target,
            stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            universal_newlines=True, timeout=300)
        self.assertEqual(install_r.returncode, 0,
                         install_r.stdout + install_r.stderr)
        stamp_path = os.path.join(self.target, "INSTALLED-FROM")
        self.assertTrue(os.path.isfile(stamp_path))
        with io.open(stamp_path, encoding="utf-8") as fh:
            stamped = fh.read().splitlines()[0]
        self.assertEqual(stamped, expected_commit,
                         "install.py stamped a commit that does not match "
                         "the clone's own git HEAD")

        after = subprocess.run(
            [sys.executable, os.path.join(self.target, "scripts", "doctor.py"),
             "--json"],
            cwd=self.target, env=run_env, stdout=subprocess.PIPE,
            stderr=subprocess.PIPE, universal_newlines=True, timeout=300)
        after_report = json.loads(after.stdout)
        after_identity = [c for c in after_report["checks"]
                          if c["key"] == "install_identity"]
        self.assertEqual(len(after_identity), 1, after.stdout)
        self.assertEqual(
            after_identity[0]["status"], "PASS",
            "check 11 (install_identity) did not move from SKIP to PASS "
            "after running the documented clone-then-install.py path: %s"
            % after_identity[0]["message"])
        self.assertIn(expected_commit[:12], after_identity[0]["message"])


# ---------------------------------------------------------------------------
# scripts/bm_shell.py: the declared-path wrapper for unavoidable shell writes.
# ---------------------------------------------------------------------------

class ShellWrapperCase(unittest.TestCase):

    OWNER = "bm-shell-owner"
    OTHER = "bm-shell-other"

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="bm-shell-test-")
        self.root = os.path.realpath(self.tmp)
        os.makedirs(os.path.join(self.root, "src"))
        for name in ("mine.txt", "theirs.txt"):
            with io.open(os.path.join(self.root, "src", name), "w",
                         encoding="utf-8") as fh:
                fh.write("original\n")
        self.env = dict(os.environ)
        self.env["BROTHERMODE_ROOT"] = self.root
        self.env.pop("BM_FENCE_STRICT", None)
        self.env.pop("BM_FENCE_SESSION_ID", None)
        self.env.pop("CLAUDE_SESSION_ID", None)
        # BM_FENCE_MODE is scrubbed here too: ~/.claude/settings.json can set
        # it machine-wide (BM_FENCE_MODE=enforced), and every test in this
        # class that means "default mode" must mean it regardless of the
        # machine it runs on.
        self.env.pop("BM_FENCE_MODE", None)
        self._store("init")

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _store(self, *args):
        r = subprocess.run(
            [sys.executable, os.path.join(HERE, "bm_store.py")] + list(args),
            cwd=self.root, env=self.env, stdout=subprocess.PIPE,
            stderr=subprocess.PIPE, universal_newlines=True, timeout=120)
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        return r

    def label(self, session_id):
        r = subprocess.run(
            [sys.executable, os.path.join(HERE, "bm_fence_hook.py"),
             "session-label", "--session-id", session_id],
            cwd=self.root, env=self.env, input="", stdout=subprocess.PIPE,
            stderr=subprocess.PIPE, universal_newlines=True, timeout=120)
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        return r.stdout.strip().splitlines()[-1]

    def claim(self, name, rel_path, session_id):
        self._store("claim", name, "--lifetime", "ephemeral",
                    "--objective", "shell wrapper test",
                    "--files", rel_path, "--session", self.label(session_id))

    def shell(self, *args):
        return subprocess.run(
            [sys.executable, SHELL] + list(args), cwd=self.root, env=self.env,
            stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            universal_newlines=True, timeout=120)

    def content(self, name):
        with io.open(os.path.join(self.root, "src", name),
                     encoding="utf-8") as fh:
            return fh.read()

    def test_required_no_path_declaration_is_refused(self):
        r = self.shell("--session-id", self.OWNER, "--",
                       "echo appended >> src/mine.txt")
        self.assertEqual(r.returncode, 4, r.stdout + r.stderr)
        self.assertIn("No --path was declared", r.stderr)
        self.assertEqual(self.content("mine.txt"), "original\n")

    def test_required_declare_none_on_an_obvious_write_is_refused(self):
        r = self.shell("--declare-none", "--", "echo x > src/mine.txt")
        self.assertEqual(r.returncode, 4)
        self.assertIn("REFUSED", r.stderr)
        self.assertEqual(self.content("mine.txt"), "original\n")

    def test_declare_none_on_a_read_only_command_runs(self):
        r = self.shell("--declare-none", "--", "echo hello")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertIn("hello", r.stdout)

    def test_a_declared_path_this_session_owns_runs(self):
        self.claim("mine", "src/mine.txt", self.OWNER)
        r = self.shell("--session-id", self.OWNER, "--record", "mine",
                       "--path", "src/mine.txt", "--",
                       "echo appended >> src/mine.txt")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertIn("appended", self.content("mine.txt"))

    def test_another_sessions_claim_blocks_the_command(self):
        self.claim("theirs", "src/theirs.txt", self.OTHER)
        r = self.shell("--session-id", self.OWNER, "--path", "src/theirs.txt",
                       "--", "echo appended >> src/theirs.txt")
        self.assertEqual(r.returncode, 4, r.stdout + r.stderr)
        self.assertIn("not the writer for that path", r.stderr)
        self.assertEqual(self.content("theirs.txt"), "original\n")

    def test_an_unclaimed_path_is_refused_because_the_wrapper_is_strict(self):
        """Stricter than the hook's default on purpose: for a shell write,
        'nobody claimed it' is not good enough. A second, unrelated claim
        exists so the store has active records: with none at all the fence
        fails open, which the next test covers separately."""
        self.claim("elsewhere", "src/theirs.txt", self.OTHER)
        r = self.shell("--session-id", self.OWNER, "--path", "src/mine.txt",
                       "--", "echo appended >> src/mine.txt")
        self.assertEqual(r.returncode, 4, r.stdout + r.stderr)
        self.assertIn("strict mode", r.stderr)
        self.assertEqual(self.content("mine.txt"), "original\n")

    def test_a_fence_that_fails_open_refuses_here_instead_of_running(self):
        """The hook fails open so a broken store cannot brick editing. This
        wrapper is invoked deliberately for one command, so it does the
        opposite: unchecked is not the same answer as approved."""
        r = self.shell("--session-id", self.OWNER, "--path", "src/mine.txt",
                       "--", "echo appended >> src/mine.txt")
        self.assertEqual(r.returncode, 4, r.stdout + r.stderr)
        self.assertIn("FAILING OPEN", r.stderr)
        self.assertIn("not checked at all", r.stderr)
        self.assertEqual(self.content("mine.txt"), "original\n")

    def test_no_session_id_is_refused_rather_than_invented(self):
        self.claim("mine", "src/mine.txt", self.OWNER)
        r = self.shell("--path", "src/mine.txt", "--", "echo x")
        self.assertEqual(r.returncode, 4)
        self.assertIn("No session id", r.stderr)

    def test_missing_separator_is_a_usage_error_not_a_run(self):
        r = self.shell("--path", "src/mine.txt", "echo x > src/mine.txt")
        self.assertEqual(r.returncode, 2)
        self.assertEqual(self.content("mine.txt"), "original\n")

    def test_a_declared_path_outside_the_project_root_is_refused(self):
        """Regression, fix-round 2026-07-29. canonical_target returns None for
        anything outside the root, decide() skips it with no decision and no
        note, and the wrapper read that silence as approval: it printed '1
        declared path(s) are inside this session's own claim. Running.' for a
        path that was in no claim and not even in the project."""
        self.claim("mine", "src/mine.txt", self.OWNER)
        elsewhere = os.path.realpath(tempfile.mkdtemp(prefix="bm-outside-"))
        try:
            outside = os.path.join(elsewhere, "outside-the-root.txt")
            r = self.shell("--session-id", self.OWNER, "--path", outside,
                           "--", "echo pwned > %s" % outside)
            self.assertEqual(r.returncode, 4, r.stdout + r.stderr)
            self.assertIn("OUTSIDE this project root", r.stderr)
            self.assertNotIn("inside this session's own claim", r.stderr)
            self.assertFalse(os.path.exists(outside))
        finally:
            shutil.rmtree(elsewhere, ignore_errors=True)

    def test_a_relative_escape_above_the_root_is_refused_too(self):
        self.claim("mine", "src/mine.txt", self.OWNER)
        # The escape target is named after THIS root, because the directory
        # above a mkdtemp root is the shared system temp directory: a fixed
        # name there is asserted against whatever any other run left behind.
        rel = "../escape-%s.txt" % os.path.basename(self.root)
        landed = os.path.join(os.path.dirname(self.root),
                              os.path.basename(rel))
        self.addCleanup(lambda: os.path.exists(landed) and os.remove(landed))
        r = self.shell("--session-id", self.OWNER, "--path", rel,
                       "--", "echo esc > %s" % rel)
        self.assertEqual(r.returncode, 4, r.stdout + r.stderr)
        self.assertIn("OUTSIDE this project root", r.stderr)
        self.assertFalse(os.path.exists(landed))

    def test_allow_outside_root_runs_but_calls_those_paths_not_checked(self):
        """The escape hatch has to stay honest: it runs, and it says the fence
        judged nothing, rather than reporting an unchecked path as claimed."""
        self.claim("mine", "src/mine.txt", self.OWNER)
        elsewhere = os.path.realpath(tempfile.mkdtemp(prefix="bm-outside-"))
        try:
            outside = os.path.join(elsewhere, "outside-allowed.txt")
            r = self.shell("--session-id", self.OWNER, "--allow-outside-root",
                           "--path", outside, "--", "echo ok > %s" % outside)
            self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
            self.assertIn("NOT CHECKED", r.stderr)
            self.assertTrue(os.path.exists(outside))
        finally:
            shutil.rmtree(elsewhere, ignore_errors=True)

    def test_declare_none_on_a_descriptor_qualified_redirect_is_refused(self):
        """Regression, fix-round 2026-07-29. The redirection pattern excluded
        a preceding DIGIT, which is exactly the '1>' and '2>>' forms, so this
        command ran under --declare-none while the same command with a bare
        '>' was refused. That is a miss inside the coverage the signal list
        claims, not one of the accepted misses outside it."""
        for command in ("echo bypassed 1> src/mine.txt",
                        "echo bypassed 2>> src/mine.txt"):
            r = self.shell("--declare-none", "--", command)
            self.assertEqual(r.returncode, 4, command + r.stderr)
            self.assertIn("output redirection", r.stderr)
            self.assertEqual(self.content("mine.txt"), "original\n")

    def test_descriptor_duplication_alone_is_not_called_a_write(self):
        """Restore half of the pair above: '2>&1' redirects one descriptor
        onto another and writes no file, so widening the pattern must not
        start refusing it."""
        r = self.shell("--declare-none", "--", "echo hello 2>&1")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertIn("hello", r.stdout)

    def test_the_wrapper_does_not_claim_to_understand_shell(self):
        """Honesty check on the copy itself. The signal list is short and the
        refusal has to say so, because a founder who reads 'checked' as
        'parsed' will trust it further than it can carry."""
        r = self.shell("--declare-none", "--", "tee src/mine.txt")
        self.assertEqual(r.returncode, 4)
        self.assertIn("not a shell parser", r.stderr)


class VenvEmbedExclusionCase(unittest.TestCase):
    """The vault lane's local venv (.venv-embed, tools/bm_embed_bge.py,
    created at the repository root since 2026-08-28) is full of symlinks by
    construction. install.py's own source walk refuses any symlink it
    meets (see the refusal in copy_tree), so on any machine that had run
    the embed tooling, every install preflight exited 1 and
    migrate_install.py correctly refused its plan
    (test_apply_with_confirmation_repairs_the_fixture_home in
    tools/test_bm.py is the exact test that went red). COPY_EXCLUDE_NAMES
    now excludes the name before the walk ever sees the symlinks inside it.

    This class builds ONE fixture (a full copy of ROOT, minus machine
    state, with a REAL venv planted at its root via `python3 -m venv`) and
    reuses it read-only across every test method, because a venv is
    expensive enough to build that doing it per-method would make this
    suite slow for no gain: nothing here mutates the shared source tree
    except the backwards-proof test, which restores it in a finally
    block."""

    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.mkdtemp(prefix="bm-venv-embed-test-")
        cls.source = os.path.join(cls.tmp, "source")
        shutil.copytree(ROOT, cls.source, ignore=shutil.ignore_patterns(
            ".git", "__pycache__", ".brothermode", "threads",
            ".superpowers"))
        venv_dir = os.path.join(cls.source, ".venv-embed")
        r = subprocess.run([sys.executable, "-m", "venv", venv_dir],
                           stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                           universal_newlines=True, timeout=120)
        if r.returncode != 0:
            shutil.rmtree(cls.tmp, ignore_errors=True)
            raise unittest.SkipTest(
                "python3 -m venv could not build the fixture: %s"
                % (r.stdout + r.stderr))
        cls.install_py = os.path.join(cls.source, "scripts", "install.py")

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.tmp, ignore_errors=True)

    def setUp(self):
        self.work = tempfile.mkdtemp(prefix="bm-venv-embed-run-")
        self.home = os.path.join(self.work, "home")
        os.makedirs(os.path.join(self.home, ".claude"))
        self.settings = os.path.join(self.home, ".claude", "settings.json")
        self.target = os.path.join(self.home, ".claude", "skills",
                                   "brothermode")

    def tearDown(self):
        shutil.rmtree(self.work, ignore_errors=True)

    def _install(self, install_py, *args):
        env = dict(os.environ)
        env["HOME"] = self.home
        env.pop("BROTHERMODE_VAULT", None)
        cmd = [sys.executable, install_py, "--target", self.target,
               "--settings", self.settings] + list(args)
        return subprocess.run(cmd, env=env, cwd=self.work,
                              stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                              universal_newlines=True, timeout=300)

    def _venv_files_in_target(self):
        hits = []
        if not os.path.isdir(self.target):
            return hits
        for dirpath, _dirs, filenames in os.walk(self.target):
            if ".venv-embed" in dirpath.split(os.sep):
                hits.extend(os.path.join(dirpath, f) for f in filenames)
        return hits

    def test_preflight_and_full_install_exclude_the_venv(self):
        """Proof 1 and 2: a clean install off a source tree that carries a
        real .venv-embed must pass, and not one byte of it may land in the
        installed copy."""
        r = self._install(self.install_py)
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertNotIn("is a symlink", r.stdout + r.stderr)
        self.assertEqual(self._venv_files_in_target(), [],
                         "the installed tree must contain no file from "
                         ".venv-embed")
        self.assertFalse(
            os.path.exists(os.path.join(self.target, ".venv-embed")))

    def test_upgrade_over_an_existing_install_with_the_venv_present(self):
        """Proof 3: the upgrade path over an existing install must not
        regress just because the source tree it upgrades from still
        carries the venv."""
        r1 = self._install(self.install_py)
        self.assertEqual(r1.returncode, 0, r1.stdout + r1.stderr)
        r2 = self._install(self.install_py, "--upgrade")
        self.assertEqual(r2.returncode, 0, r2.stdout + r2.stderr)
        self.assertEqual(self._venv_files_in_target(), [])

    def test_backwards_removing_the_exclusion_reproduces_the_old_refusal(
            self):
        """Driven backwards, per the loop discipline: without the
        .venv-embed entry this fix added to COPY_EXCLUDE_NAMES, the
        installer must refuse exactly the way it did on every
        embed-capable machine before this fix landed. This is what proves
        the two tests above are actually guarding the fix, not merely
        exercising a path that always passed."""
        with io.open(self.install_py, encoding="utf-8") as fh:
            original = fh.read()
        marker = '    ".venv-embed",\n'
        self.assertIn(marker, original,
                     "fixture assumption broke: the fix line is missing "
                     "from the copied install.py")
        unfixed = original.replace(marker, "", 1)
        with io.open(self.install_py, "w", encoding="utf-8") as fh:
            fh.write(unfixed)
        try:
            r = self._install(self.install_py)
        finally:
            with io.open(self.install_py, "w", encoding="utf-8") as fh:
                fh.write(original)
        self.assertNotEqual(
            r.returncode, 0,
            "removing the .venv-embed exclusion must reproduce the old "
            "refusal, not silently keep passing")
        self.assertIn("is a symlink", r.stdout + r.stderr)
        self.assertIn(".venv-embed", r.stdout + r.stderr)


if __name__ == "__main__":
    unittest.main(verbosity=1)

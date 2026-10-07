#!/usr/bin/env python3
"""brother_install, pinned without a network or a real Codex binary: the
pure parts (hook pruning, staleness, the marketplace ref reader, the atomic
write) and the honest verbs (a second uninstall is NO-DATA at exit 0, a
rollback with no snapshot is NO-DATA) through a fake --codex-bin."""

import json
import os
import pwd
import subprocess
import sys
import tempfile
import unittest
from unittest import mock
from pathlib import Path

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

import brother_install as BI  # noqa: E402

# The account's own home, read before any test mocks getuid, and never "~":
# the push gate runs this file under a throwaway HOME, where "~" is not it.
REAL_UID = os.getuid()
ACCOUNT = pwd.getpwuid(REAL_UID).pw_dir
ACCOUNT_CODEX = os.path.join(ACCOUNT, ".codex")
# The firmlink alias of the data volume: the same folder as the path it
# wraps (device and inode agree) under a realpath that does not.
FIRMLINK = "/System/Volumes/Data"

FAKE_CODEX = """#!/bin/sh
case "$*" in
  *"plugin list --json"*) echo '{"installed": []}' ;;
  *"plugin list --available --json"*) echo '{"available": []}' ;;
  *) echo '{}' ;;
esac
exit 0
"""


def write_fake_codex(tmp):
    path = os.path.join(tmp, "codex")
    with open(path, "w") as fh:
        fh.write(FAKE_CODEX)
    os.chmod(path, 0o755)
    return path


class HookPruning(unittest.TestCase):
    def doc(self, brother_cmd):
        return {"hooks": {"PreToolUse": [{"matcher": "", "hooks": [
            {"type": "command", "command": brother_cmd},
            {"type": "command", "command": "python3 /Users/someone/other-tool.py"}]}]}}

    def test_temp_brother_hook_removed_foreign_hook_kept(self):
        out = BI.prune_brother_hooks(self.doc('python3 "/private/tmp/brother-x/tools/bm_fence_hook.py"'), only_stale=True)
        self.assertEqual(len(out["removed"]), 1)
        kept = out["document"]["hooks"]["PreToolUse"][0]["hooks"]
        self.assertEqual([h["command"] for h in kept], ["python3 /Users/someone/other-tool.py"])

    def test_missing_target_is_stale(self):
        self.assertTrue(BI.is_stale_target('python3 "/no/such/brother/tools/bm_fence_hook.py"'))

    def test_live_durable_target_is_not_stale(self):
        # Hermetic (2026-09-26): this used to place the tool under the real
        # HOME and so passed only where HOME is not itself a temp directory;
        # the pre-push export check runs with an empty HOME under $TMPDIR. The
        # temp roots are pinned instead, so "durable" means what it says.
        with tempfile.TemporaryDirectory() as home:
            tool = os.path.join(home, "brother_tool.py")
            with open(tool, "w") as fh:
                fh.write("pass\n")
            with mock.patch.object(BI, "_tempdirs", return_value={"/no/such/temp-root"}):
                self.assertFalse(BI.is_stale_target('python3 "%s"' % tool))
            with mock.patch.object(BI, "_tempdirs", return_value={os.path.realpath(home)}):
                self.assertTrue(BI.is_stale_target('python3 "%s"' % tool),
                                "the same file under a temp root must read stale")

    def test_non_brother_command_never_pruned_even_when_stale(self):
        out = BI.prune_brother_hooks(
            {"hooks": {"Stop": [{"hooks": [{"type": "command", "command": "python3 /no/such/other.py"}]}]}},
            only_stale=False)
        self.assertEqual(out["removed"], [])


class MarketplaceRef(unittest.TestCase):
    def test_reads_the_ref_of_the_named_marketplace(self):
        text = ('[marketplaces.other]\nsource_type = "git"\nref = "v9"\n\n'
                '[marketplaces.brother]\nsource_type = "git"\n'
                'source = "https://github.com/khalilmaaouni/Brother.git"\nref = "v1.0.8"\n')
        self.assertEqual(BI.read_marketplace_ref(text, "brother"), "v1.0.8")

    def test_absent_marketplace_is_none(self):
        self.assertIsNone(BI.read_marketplace_ref("[plugins.x]\nenabled = true\n", "brother"))


class LocalCheckoutSource(unittest.TestCase):
    """A checkout passed as --marketplace: Codex 0.157 refuses --ref for a
    local source ("--ref is only supported for git marketplace sources") and
    names the marketplace from marketplace.json, not from the folder name
    (measured 2026-09-30: a checkout in folder host-parity added as
    "brother")."""

    def make_checkout(self, tmp, folder="host-parity", name="brother"):
        root = os.path.join(tmp, folder)
        os.makedirs(os.path.join(root, ".agents", "plugins"))
        with open(os.path.join(root, ".agents", "plugins", "marketplace.json"), "w") as fh:
            json.dump({"name": name, "plugins": []}, fh)
        return root

    def test_local_checkout_is_named_by_its_marketplace_json(self):
        with tempfile.TemporaryDirectory() as d:
            self.assertEqual(BI.marketplace_name_from_source(self.make_checkout(d)), "brother")

    def test_url_is_still_named_by_its_basename(self):
        self.assertEqual(BI.marketplace_name_from_source(
            "https://github.com/khalilmaaouni/Brother.git"), "brother")

    def test_local_checkout_with_unreadable_marketplace_json_falls_back_to_basename(self):
        with tempfile.TemporaryDirectory() as d:
            root = os.path.join(d, "Checkout")
            os.makedirs(os.path.join(root, ".agents", "plugins"))
            with open(os.path.join(root, ".agents", "plugins", "marketplace.json"), "w") as fh:
                fh.write("{not json")
            self.assertEqual(BI.marketplace_name_from_source(root), "checkout")

    # One condition per fixture (host-parity attack 2026-09-30, survivors M3,
    # M4, M6, M8, M10, M11). Each would pass with a different mutation of
    # marketplace_name_from_source alive; together they pin its contract.

    def test_m3_mixed_case_name_is_kept_as_codex_keeps_it(self):
        """Measured 2026-09-30 on Codex 0.157: marketplaceName "Brother-Mixed",
        config table [marketplaces.Brother-Mixed]. Not lowercased."""
        with tempfile.TemporaryDirectory() as d:
            self.assertEqual(BI.marketplace_name_from_source(
                self.make_checkout(d, name="Brother-Mixed")), "Brother-Mixed")

    def test_m4_claude_plugin_catalog_is_the_fallback(self):
        with tempfile.TemporaryDirectory() as d:
            root = os.path.join(d, "host-parity")
            os.makedirs(os.path.join(root, ".claude-plugin"))
            with open(os.path.join(root, ".claude-plugin", "marketplace.json"), "w") as fh:
                json.dump({"name": "from-claude-plugin", "plugins": []}, fh)
            self.assertEqual(BI.marketplace_name_from_source(root), "from-claude-plugin")

    def test_m6_agents_catalog_wins_over_claude_plugin(self):
        with tempfile.TemporaryDirectory() as d:
            root = self.make_checkout(d, name="from-agents")
            os.makedirs(os.path.join(root, ".claude-plugin"))
            with open(os.path.join(root, ".claude-plugin", "marketplace.json"), "w") as fh:
                json.dump({"name": "from-claude-plugin", "plugins": []}, fh)
            self.assertEqual(BI.marketplace_name_from_source(root), "from-agents")

    def test_m8_a_json_list_does_not_crash_and_falls_back(self):
        with tempfile.TemporaryDirectory() as d:
            root = os.path.join(d, "Checkout")
            os.makedirs(os.path.join(root, ".agents", "plugins"))
            with open(os.path.join(root, ".agents", "plugins", "marketplace.json"), "w") as fh:
                fh.write("[1, 2]")
            self.assertEqual(BI.marketplace_name_from_source(root), "checkout")

    def test_m10_a_non_string_or_empty_name_falls_back(self):
        for bad in (7, "", None, ["brother"]):
            with tempfile.TemporaryDirectory() as d:
                root = os.path.join(d, "Checkout")
                os.makedirs(os.path.join(root, ".agents", "plugins"))
                with open(os.path.join(root, ".agents", "plugins", "marketplace.json"), "w") as fh:
                    json.dump({"name": bad, "plugins": []}, fh)
                self.assertEqual(BI.marketplace_name_from_source(root), "checkout", bad)

    def test_m11_a_file_path_is_not_a_checkout_and_keeps_ref(self):
        ok = {"returncode": 0, "stdout": '{"alreadyAdded": false}', "stderr": "", "problem": None}
        with tempfile.TemporaryDirectory() as d:
            path = os.path.join(d, "marketplace.tar")
            with open(path, "w") as fh:
                fh.write("x")
            with mock.patch.object(BI, "run_codex", return_value=ok) as cli:
                BI.ensure_marketplace("fake", "/home", path, "v1.1.0")
        self.assertIn("--ref", cli.call_args_list[0].args[1])

    def test_local_checkout_label_is_head_sha_and_dirty_state_never_the_ref(self):
        """Follow-up 1 of the same attack: a local source installs what is
        checked out, so the printed label is the checkout's HEAD, not the
        asked ref."""
        ok = {"returncode": 0, "stdout": '{"alreadyAdded": false}', "stderr": "", "problem": None}
        with tempfile.TemporaryDirectory() as d:
            root = self.make_checkout(d)
            subprocess.run(["git", "-C", root, "init", "-q"], check=True)
            subprocess.run(["git", "-C", root, "add", "-A"], check=True)
            subprocess.run(["git", "-C", root, "-c", "user.email=t@t", "-c", "user.name=t",
                            "commit", "-q", "-m", "x"], check=True)
            sha = subprocess.run(["git", "-C", root, "rev-parse", "--short", "HEAD"],
                                 capture_output=True, text=True).stdout.strip()
            self.assertEqual(BI.source_label(root, "v1.1.0"),
                             "checkout %s (HEAD %s, clean)" % (root, sha))
            with open(os.path.join(root, "scratch.txt"), "w") as fh:
                fh.write("dirt")
            self.assertEqual(BI.source_label(root, "v1.1.0"),
                             "checkout %s (HEAD %s, dirty)" % (root, sha))
            with mock.patch.object(BI, "run_codex", return_value=ok):
                detail = BI.ensure_marketplace("fake", "/home", root, "v1.1.0")["detail"]
            self.assertNotIn("v1.1.0", detail)
            self.assertIn(sha, detail)

    def test_local_directory_without_git_says_so(self):
        with tempfile.TemporaryDirectory() as d:
            root = self.make_checkout(d)
            self.assertEqual(BI.source_label(root, "v1.1.0"),
                             "checkout %s (not a git repository)" % root)

    def test_git_source_label_is_the_ref(self):
        self.assertEqual(BI.source_label(BI.MARKETPLACE_URL_DEFAULT, "v1.1.0"), "v1.1.0")

    def test_install_from_local_checkout_omits_ref(self):
        ok = {"returncode": 0, "stdout": '{"alreadyAdded": false}', "stderr": "", "problem": None}
        with tempfile.TemporaryDirectory() as d:
            root = self.make_checkout(d)
            with mock.patch.object(BI, "run_codex", return_value=ok) as cli:
                BI.ensure_marketplace("fake", "/home", root, "v1.1.0")
        add = cli.call_args_list[0].args[1]
        self.assertEqual(add[:4], ["plugin", "marketplace", "add", root])
        self.assertNotIn("--ref", add)

    def test_install_from_git_url_keeps_ref(self):
        ok = {"returncode": 0, "stdout": '{"alreadyAdded": false}', "stderr": "", "problem": None}
        with mock.patch.object(BI, "run_codex", return_value=ok) as cli:
            BI.ensure_marketplace("fake", "/home", BI.MARKETPLACE_URL_DEFAULT, "v1.1.0")
        self.assertIn("--ref", cli.call_args_list[0].args[1])


class AtomicWrite(unittest.TestCase):
    def test_write_lands_and_leaves_no_temp_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "hooks.json")
            BI.atomic_write(path, '{"a": 1}')
            with open(path) as fh:
                self.assertEqual(json.load(fh), {"a": 1})
            self.assertEqual(sorted(os.listdir(tmp)), ["hooks.json"])


class HonestVerbs(unittest.TestCase):
    def run_cli(self, tmp, *args):
        fake = write_fake_codex(tmp)
        home = os.path.join(tmp, "home")
        argv = [sys.executable, os.path.join(HERE, "brother_install.py")] + list(args) + [
            "--codex-home", home, "--codex-bin", fake]
        return subprocess.run(argv, capture_output=True, text=True), home

    def test_second_uninstall_is_no_data_at_exit_zero(self):
        with tempfile.TemporaryDirectory() as tmp:
            os.makedirs(os.path.join(tmp, "home"))
            first, home = self.run_cli(tmp, "uninstall")
            second, _ = self.run_cli(tmp, "uninstall")
            body = second.stdout + second.stderr
            self.assertEqual(second.returncode, 0, body)
            self.assertIn("NO-DATA", body)
            self.assertNotIn("verdict=PASS", body)

    def test_rollback_without_snapshot_is_no_data(self):
        with tempfile.TemporaryDirectory() as tmp:
            os.makedirs(os.path.join(tmp, "home"))
            proc, _ = self.run_cli(tmp, "rollback")
            self.assertIn("NO-DATA", proc.stdout + proc.stderr)

    def test_status_on_empty_home_is_absent(self):
        with tempfile.TemporaryDirectory() as tmp:
            os.makedirs(os.path.join(tmp, "home"))
            proc, _ = self.run_cli(tmp, "status")
            self.assertIn("ABSENT", proc.stdout + proc.stderr)

    def test_a_filesystem_root_or_the_users_home_is_refused_as_a_codex_home(self):
        for named in ("/", os.path.expanduser("~")):
            got = BI.resolve_home(named, True)
            self.assertIsNone(got["path"], named)
            self.assertIn("filesystem root or the user's own home", got["problem"])
        with tempfile.TemporaryDirectory() as tmp:
            self.assertEqual(BI.resolve_home(tmp, False)["path"], os.path.realpath(tmp))

    def test_real_home_refused_without_the_flag(self):
        with tempfile.TemporaryDirectory() as tmp:
            fake = write_fake_codex(tmp)
            # The default resolves under HOME, so HOME is the account's own
            # here (read only: the refusal comes before status writes); under
            # a throwaway HOME the default would be that throwaway's .codex.
            env = {k: v for k, v in os.environ.items() if k != "CODEX_HOME"}
            env["HOME"] = ACCOUNT
            proc = subprocess.run([sys.executable, os.path.join(HERE, "brother_install.py"),
                                   "status", "--codex-bin", fake],
                                  capture_output=True, text=True, env=env)
            self.assertNotEqual(proc.returncode, 0)


class CodexHomeIdentity(unittest.TestCase):
    """The real Codex home is decided by identity, never by the spelling of
    a string derived from $HOME (2026-10-04: a throwaway HOME's .codex was
    refused as the real one, and a different-case spelling of the real one
    was accepted)."""

    def assert_refused_as_real(self, got):
        self.assertIsNone(got["path"], got)
        self.assertIn("the real Codex home", got["problem"])

    def test_a_throwaway_home_s_own_codex_dir_is_accepted(self):
        with tempfile.TemporaryDirectory() as tmp:
            codex = os.path.join(tmp, ".codex")
            os.makedirs(codex)
            with mock.patch.dict(os.environ, {"HOME": tmp, "CODEX_HOME": codex}):
                for named in (codex, os.path.join("~", ".codex"), None):
                    got = BI.resolve_home(named, False)
                    self.assertEqual(got["path"], os.path.realpath(codex), (named, got))

    def test_the_account_s_codex_is_refused_whatever_its_spelling(self):
        spelled = ACCOUNT_CODEX.swapcase()   # the same folder: this volume is case insensitive
        self.assertNotEqual(spelled, ACCOUNT_CODEX)
        self.assert_refused_as_real(BI.resolve_home(spelled, False))
        with mock.patch.dict(os.environ, {"HOME": ACCOUNT.swapcase()}):
            self.assert_refused_as_real(BI.resolve_home(os.path.join("~", ".codex"), False))
        with mock.patch.dict(os.environ, {"CODEX_HOME": spelled}):
            self.assert_refused_as_real(BI.resolve_home(None, False))
        # the positive control: the same spelling, meant
        self.assertIsNone(BI.resolve_home(spelled, True)["problem"])

    def test_a_symlink_to_the_account_s_codex_is_refused(self):
        with tempfile.TemporaryDirectory() as tmp:
            link = os.path.join(tmp, "codex-link")
            os.symlink(ACCOUNT_CODEX, link)
            self.assert_refused_as_real(BI.resolve_home(link, False))

    def test_a_real_home_that_does_not_exist_yet_is_matched_casefolded(self):
        with tempfile.TemporaryDirectory() as tmp:
            account = os.path.join(tmp, "acct")
            os.makedirs(account)
            with mock.patch.object(BI, "account_home", return_value=account):
                self.assert_refused_as_real(
                    BI.resolve_home(os.path.join(tmp, "ACCT", ".CODEX"), False))
                other = BI.resolve_home(os.path.join(account, "other"), False)
                self.assertEqual(other["path"], os.path.realpath(os.path.join(account, "other")))

    def test_identity_beats_spelling_for_a_home_that_exists(self):
        with tempfile.TemporaryDirectory() as tmp:
            account = os.path.join(tmp, "acct")
            codex = os.path.join(account, ".codex")
            os.makedirs(codex)
            alias = FIRMLINK + os.path.realpath(codex)
            if not os.path.isdir(alias):
                self.skipTest("NO-DATA: no firmlink alias for %s on this volume" % codex)
            self.assertNotEqual(os.path.realpath(alias), os.path.realpath(codex))
            with mock.patch.object(BI, "account_home", return_value=account):
                self.assert_refused_as_real(BI.resolve_home(alias, False))

    def test_the_firmlink_spelling_of_a_real_home_that_does_not_exist_yet_is_refused(self):
        """realpath does not see through the firmlink, so a casefolded
        realpath compare accepted /System/Volumes/Data/<home>/.codex before
        ~/.codex existed and created the real one; the nearest existing
        ancestor is compared by identity instead."""
        with tempfile.TemporaryDirectory() as tmp:
            account = os.path.join(tmp, "acct")
            os.makedirs(account)
            alias_home = FIRMLINK + os.path.realpath(account)
            if not os.path.isdir(alias_home):
                self.skipTest("NO-DATA: no firmlink alias for %s on this volume" % account)
            alias = os.path.join(alias_home, ".codex")
            self.assertFalse(os.path.exists(alias))
            with mock.patch.object(BI, "account_home", return_value=account):
                self.assert_refused_as_real(BI.resolve_home(alias, False))
                with mock.patch.dict(os.environ, {"HOME": alias_home}):
                    self.assert_refused_as_real(BI.resolve_home(os.path.join("~", ".codex"), False))
                # a throwaway's not-yet-existing .codex is still its own
                with mock.patch.dict(os.environ, {"HOME": tmp}):
                    got = BI.resolve_home(os.path.join("~", ".codex"), False)
                    self.assertEqual(got["path"], os.path.realpath(os.path.join(tmp, ".codex")), got)

    def test_under_sudo_the_invoking_user_s_codex_is_protected(self):
        with tempfile.TemporaryDirectory() as tmp:
            with mock.patch.object(os, "getuid", return_value=0), \
                    mock.patch.dict(os.environ, {"SUDO_UID": str(REAL_UID)}):
                self.assertEqual(BI.account_home(), ACCOUNT)
                self.assert_refused_as_real(BI.resolve_home(ACCOUNT_CODEX, False))
                got = BI.resolve_home(os.path.join(tmp, ".codex"), False)
                self.assertEqual(got["path"], os.path.realpath(os.path.join(tmp, ".codex")))

    def test_uid_zero_without_sudo_uid_refuses_every_home(self):
        with tempfile.TemporaryDirectory() as tmp:
            with mock.patch.object(os, "getuid", return_value=0), mock.patch.dict(os.environ):
                os.environ.pop("SUDO_UID", None)
                self.assertIsNone(BI.account_home())
                got = BI.resolve_home(os.path.join(tmp, ".codex"), True)
        self.assertIsNone(got["path"], got)
        self.assertIn("account's home could not be read", got["problem"])

    def test_an_unreadable_account_home_refuses_rather_than_guessing(self):
        with tempfile.TemporaryDirectory() as tmp:
            with mock.patch.object(pwd, "getpwuid", side_effect=KeyError(REAL_UID)):
                self.assertIsNone(BI.account_home())
                got = BI.resolve_home(os.path.join(tmp, ".codex"), True)
        self.assertIsNone(got["path"], got)
        self.assertIn("account's home could not be read", got["problem"])


class ProductSkillLifecycle(unittest.TestCase):
    def test_opt_in_installs_retains_and_verifies_companions(self):
        from test_codex_product_skills import fixture
        with tempfile.TemporaryDirectory() as d:
            source = fixture(Path(d) / "export")
            home = str(Path(d) / "home")
            Path(home).mkdir()
            installed = {}
            roots = {"brother": source}
            calls = []

            def cli(_binary, args, _home, timeout=120):
                calls.append(args)
                data = {}
                if args[:3] == ["plugin", "list", "--json"]:
                    data = {"installed": list(installed.values())}
                elif args[:3] == ["plugin", "marketplace", "add"]:
                    root = source if args[3] == BI.MARKETPLACE_URL_DEFAULT else Path(args[3])
                    catalog = json.loads((root / ".agents/plugins/marketplace.json").read_text())
                    name = catalog["name"]
                    roots[name] = root
                    data = {"marketplaceName": name, "installedRoot": str(root)}
                elif args[:2] == ["plugin", "add"]:
                    name, market = args[2].split("@")
                    path = roots[market] / ("bundle" if name == "brother" else "plugins/" + name)
                    manifest = json.loads((path / ".codex-plugin/plugin.json").read_text())
                    data = {"pluginId": args[2], "name": name, "marketplaceName": market,
                            "version": manifest["version"], "installedPath": str(path)}
                    installed[args[2]] = data
                elif args[:2] == ["plugin", "remove"]:
                    installed.pop(args[2], None)
                return {"returncode": 0, "stdout": json.dumps(data), "stderr": "", "problem": None}

            with mock.patch.object(BI, "run_codex", side_effect=cli):
                result = BI.do_install("fake", home, BI.MARKETPLACE_URL_DEFAULT, "v1.0.14", product_skills=True)
                self.assertEqual(result["verdict"], "PASS", result)
                self.assertEqual(set(installed), {"brother@brother", "brothermode@brother-product-skills", "brothersbe@brother-product-skills"})
                self.assertEqual(BI.do_status("fake", home, BI.MARKETPLACE_URL_DEFAULT, product_skills=True)["verdict"], "END-STATE")
                self.assertEqual(BI.do_install("fake", home, BI.MARKETPLACE_URL_DEFAULT, "v1.0.14")["verdict"], "PASS")
                self.assertIn("brothermode@brother-product-skills", installed)
                before = dict(installed["brothermode@brother-product-skills"])
                snapshot = BI.make_snapshot("fake", home, BI.MARKETPLACE_URL_DEFAULT, "v1.0.14")
                self.assertIsNone(snapshot["problem"])
                (source / "products/brothermode/tools/helper.py").write_text("new support")
                self.assertEqual(BI.do_install("fake", home, BI.MARKETPLACE_URL_DEFAULT, "v1.0.14", product_skills=True)["verdict"], "PASS")
                self.assertNotEqual(installed["brothermode@brother-product-skills"]["version"], before["version"])
                self.assertEqual(BI.do_rollback("fake", home, snapshot["dir"])["verdict"], "PASS")
                self.assertEqual(installed["brothermode@brother-product-skills"]["version"], before["version"])
                root = Path(installed["brothermode@brother-product-skills"]["installedPath"])
                (root / "tools/helper.py").write_text("tampered")
                self.assertEqual(BI.do_status("fake", home, BI.MARKETPLACE_URL_DEFAULT, product_skills=True)["verdict"], "PARTIAL")
                BI.do_uninstall("fake", home, BI.MARKETPLACE_URL_DEFAULT)
                self.assertEqual(installed, {})
            local_adds = [a for a in calls if a[:3] == ["plugin", "marketplace", "add"] and a[3] != BI.MARKETPLACE_URL_DEFAULT]
            self.assertTrue(local_adds)
            self.assertTrue(all("--ref" not in args for args in local_adds))

    def test_local_marketplace_requires_explicit_export_input(self):
        with tempfile.TemporaryDirectory() as d:
            result = BI.install_product_skills("fake", d, "/private/source", "v1", {"installed_root": d})
            self.assertEqual(result["status"], "FAIL")
            self.assertIn("explicit", result["detail"])

    def test_local_marketplace_repoint_omits_ref_on_both_attempts(self):
        responses = [
            {"returncode": 1, "stdout": "", "stderr": "already added from a different source", "problem": None},
            {"returncode": 0, "stdout": "{}", "stderr": "", "problem": None},
            {"returncode": 0, "stdout": '{"installedRoot":"/new/brother-product-skills"}', "stderr": "", "problem": None}]
        with mock.patch.object(BI, "run_codex", side_effect=responses) as cli:
            result = BI.ensure_marketplace("fake", "/home", "/new/brother-product-skills", None)
        adds = [call.args[1] for call in cli.call_args_list if call.args[1][:3] == ["plugin", "marketplace", "add"]]
        self.assertEqual(len(adds), 2)
        self.assertEqual(adds[0], adds[1])
        self.assertNotIn("--ref", adds[1])
        self.assertEqual(result["installed_root"], "/new/brother-product-skills")

    def test_product_skills_flags_are_explicit(self):
        args = BI.build_argparser().parse_args(["install", "--ref", "v1.0.14", "--product-skills", "--product-skills-export", "/export"])
        self.assertTrue(args.product_skills)
        self.assertEqual(args.product_skills_export, "/export")


if __name__ == "__main__":
    unittest.main()

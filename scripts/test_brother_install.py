#!/usr/bin/env python3
"""brother_install, pinned without a network or a real Codex binary: the
pure parts (hook pruning, staleness, the marketplace ref reader, the atomic
write) and the honest verbs (a second uninstall is NO-DATA at exit 0, a
rollback with no snapshot is NO-DATA) through a fake --codex-bin."""

import json
import os
import subprocess
import sys
import tempfile
import unittest
from unittest import mock
from pathlib import Path

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

import brother_install as BI  # noqa: E402

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
        with tempfile.TemporaryDirectory(dir=os.path.expanduser("~")) as home:
            tool = os.path.join(home, "brother_tool.py")
            with open(tool, "w") as fh:
                fh.write("pass\n")
            self.assertFalse(BI.is_stale_target('python3 "%s"' % tool))

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
            proc = subprocess.run([sys.executable, os.path.join(HERE, "brother_install.py"),
                                   "status", "--codex-bin", fake],
                                  capture_output=True, text=True)
            self.assertNotEqual(proc.returncode, 0)


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

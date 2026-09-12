#!/usr/bin/env python3
"""Tests for the Brother Cursor plugin port.

Covers the .cursor-plugin marketplace, the bundle and product manifests,
the Cursor hook file, Deepseek/Muse Claude Desktop routing, local
install, and a smoke of the one door. No live Cursor Agent is required.

Python 3.9, standard library only. No network.
No em or en dashes anywhere in this file, its comments, or its output.
"""

from __future__ import annotations

import io
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(HERE)
sys.path.insert(0, HERE)

import cursor_plugin_install as inst  # noqa: E402


REQUIRED_HOOK_EVENTS = (
    "sessionStart",
    "sessionEnd",
    "preCompact",
    "stop",
    "preToolUse",
    "beforeShellExecution",
    "postToolUse",
    "afterShellExecution",
    "afterFileEdit",
)


def _read(rel):
    with io.open(os.path.join(REPO, rel), encoding="utf-8") as fh:
        return fh.read()


def _has_dash(text):
    return ("\u2014" in text) or ("\u2013" in text)


class TestCursorPluginPackage(unittest.TestCase):
    def test_validate_bundle_passes(self):
        self.assertEqual([], inst.validate_bundle(os.path.join(REPO, "bundle")))

    def test_marketplace_lists_the_three_plugins(self):
        doc = json.loads(_read(os.path.join(".cursor-plugin",
                                            "marketplace.json")))
        self.assertEqual(doc["name"], "brother")
        names = [p["name"] for p in doc["plugins"]]
        self.assertEqual(names, ["brother", "brothermode", "brothersbe"])
        source = json.loads(_read(".claude-plugin/marketplace.json"))
        self.assertEqual(doc["metadata"]["version"], source["metadata"]["version"])
        self.assertFalse(_has_dash(json.dumps(doc)))

    def test_bundle_manifest_version_matches_umbrella(self):
        manifest = json.loads(_read(os.path.join(
            "bundle", ".cursor-plugin", "plugin.json")))
        market = json.loads(_read(os.path.join(
            ".claude-plugin", "marketplace.json")))
        brother = next(p for p in market["plugins"] if p["name"] == "brother")
        self.assertEqual(manifest["version"], brother["version"])
        self.assertEqual(manifest["name"], "brother")
        self.assertIn("cursor-hooks/hooks.json", manifest["hooks"])
        self.assertFalse(_has_dash(json.dumps(manifest)))

    def test_product_manifests_match_marketplace_versions(self):
        market = json.loads(_read(os.path.join(
            ".claude-plugin", "marketplace.json")))
        for name in ("brothermode", "brothersbe"):
            entry = next(p for p in market["plugins"] if p["name"] == name)
            manifest = json.loads(_read(os.path.join(
                "products", name, ".cursor-plugin", "plugin.json")))
            self.assertEqual(manifest["name"], name)
            self.assertEqual(manifest["version"], entry["version"])

    def test_hooks_cover_every_required_event_and_use_plugin_root(self):
        doc = json.loads(_read(os.path.join(
            "bundle", "cursor-hooks", "hooks.json")))
        hooks = doc["hooks"]
        for event in REQUIRED_HOOK_EVENTS:
            self.assertIn(event, hooks)
            self.assertTrue(hooks[event])
            cmd = hooks[event][0].get("command", "")
            self.assertIn("PLUGIN_ROOT", cmd)
            self.assertNotIn("CLAUDE_PLUGIN_ROOT", cmd)

    def test_door_and_skill_name_plugin_root(self):
        command = _read(os.path.join("bundle", "commands", "brother.md"))
        skill = _read(os.path.join(
            "bundle", "skills", "using-brother", "SKILL.md"))
        self.assertIn("BROTHER_PLUGIN_ROOT", skill)
        self.assertIn("PLUGIN_ROOT", skill)
        self.assertIn("name: brother", command)
        self.assertFalse(_has_dash(command))
        self.assertFalse(_has_dash(skill))

    def test_rules_do_not_ship_deepseek_or_muse(self):
        rules = os.path.join(REPO, "bundle", "rules")
        offenders = []
        if os.path.isdir(rules):
            for dirpath, dirnames, filenames in os.walk(rules):
                for fn in filenames:
                    path = os.path.join(dirpath, fn)
                    with io.open(path, encoding="utf-8",
                                 errors="ignore") as fh:
                        text = fh.read().lower()
                    if "deepseek" in text or "muse" in text:
                        offenders.append(os.path.relpath(path, REPO))
        self.assertEqual([], offenders)

    def test_docs_page_exists(self):
        text = _read(os.path.join("docs", "how-to", "install-cursor.md"))
        self.assertIn("cursor_plugin_install.py", text)
        self.assertIn("ADVISORY", text)
        self.assertFalse(_has_dash(text))

    def test_readme_points_at_the_cursor_guide(self):
        text = _read("README.md")
        self.assertIn("docs/how-to/install-cursor.md", text)


class TestCursorPluginInstall(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="brother-cursor-plugin-")
        self.addCleanup(lambda: shutil.rmtree(self.tmp, ignore_errors=True))

    def test_install_validate_uninstall(self):
        dest = os.path.join(self.tmp, "brother")
        code, copied = inst.install(dest, force=False, dry=False)
        self.assertEqual(code, 0)
        self.assertGreater(copied, 20)
        self.assertTrue(os.path.isfile(os.path.join(
            dest, ".cursor-plugin", "plugin.json")))
        self.assertTrue(os.path.isfile(os.path.join(
            dest, "cursor-hooks", "hooks.json")))
        self.assertTrue(os.path.isfile(os.path.join(
            dest, "commands", "brother.md")))
        self.assertEqual([], inst.validate_bundle(dest))
        refused, _ = inst.install(dest, force=False, dry=False)
        self.assertEqual(refused, inst.EXIT_REFUSED)
        self.assertEqual(inst.uninstall(dest), 0)
        self.assertFalse(os.path.isdir(dest))

    def test_cli_validate_and_dry_run(self):
        script = os.path.join(HERE, "cursor_plugin_install.py")
        v = subprocess.run(
            [sys.executable, script, "validate"],
            cwd=REPO, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            universal_newlines=True)
        self.assertEqual(v.returncode, 0, v.stdout + v.stderr)
        dest = os.path.join(self.tmp, "dry")
        d = subprocess.run(
            [sys.executable, script, "install", "--target", dest,
             "--dry-run"],
            cwd=REPO, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            universal_newlines=True)
        self.assertEqual(d.returncode, 0, d.stdout + d.stderr)
        self.assertFalse(os.path.isdir(dest))


class TestCursorPluginDoorSmoke(unittest.TestCase):
    def test_brother_run_help_from_the_bundle(self):
        runner = os.path.join(REPO, "bundle", "runtime", "brother_run.py")
        if not os.path.isfile(runner):
            self.skipTest("bundle/runtime/brother_run.py is not in this tree")
        proc = subprocess.run(
            [sys.executable, runner, "--help"],
            cwd=REPO, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            universal_newlines=True, timeout=45)
        combined = (proc.stdout or "") + (proc.stderr or "")
        self.assertIn(proc.returncode, (0, 1, 2), combined)
        self.assertNotIn("Traceback (most recent call last)", combined)


if __name__ == "__main__":
    unittest.main()

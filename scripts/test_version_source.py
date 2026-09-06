#!/usr/bin/env python3
"""Tests for scripts/version_source.py, the umbrella version drift checker
and writer. Every case builds a fixture tree in a temp dir and drives the
script by --root, never against this repository's own working tree.

No em or en dashes.
"""
import json
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SCRIPT = ROOT / "scripts" / "version_source.py"

MARKETPLACE = {
    "name": "brother",
    "owner": {"name": "Khalil Maaouni"},
    "metadata": {"description": "d", "version": "1.0.9"},
    "plugins": [
        {
            "name": "brothermode",
            "source": {
                "source": "git-subdir",
                "url": "https://github.com/khalilmaaouni/Brother",
                "path": "products/brothermode",
                "ref": "v1.0.9",
            },
            "description": "d",
            "version": "3.4.4",
            "author": {"name": "Khalil Maaouni"},
            "category": "productivity",
        },
        {
            "name": "brothersbe",
            "source": {
                "source": "git-subdir",
                "url": "https://github.com/khalilmaaouni/Brother",
                "path": "products/brothersbe",
                "ref": "v1.0.9",
            },
            "description": "d",
            "version": "3.7.3",
            "author": {"name": "Khalil Maaouni"},
            "category": "engineering",
        },
        {
            "name": "brother",
            "source": {
                "source": "git-subdir",
                "url": "https://github.com/khalilmaaouni/Brother",
                "path": "bundle",
                "ref": "v1.0.9",
            },
            "description": "d",
            "version": "1.0.9",
            "author": {"name": "Khalil Maaouni"},
            "category": "productivity",
        },
    ],
}

BUNDLE_CLAUDE = {
    "name": "brother",
    "version": "1.0.9",
    "description": "d",
    "author": {"name": "Khalil Maaouni"},
    "dependencies": ["brothermode@^3.4.2", "brothersbe@^3.7.0"],
    "repository": "https://github.com/khalilmaaouni/Brother",
    "license": "MIT",
    "keywords": ["a", "b"],
}

BUNDLE_CODEX = {
    "name": "brother",
    "version": "1.0.9",
    "description": "d",
    "author": {"name": "Khalil Maaouni"},
    "repository": "https://github.com/khalilmaaouni/Brother",
    "license": "MIT",
    "skills": "./skills/",
}

VERSIONING_MD = (
    "# Versioning\n\n"
    "Some prose.\n\n"
    "Current version: 1.0.9.\n\n"
    "More prose.\n"
)

PRODUCT_CLAUDE = {
    "name": "brothermode",
    "version": "3.4.4",
    "description": "d",
    "author": {"name": "Khalil Maaouni"},
}


def write_json(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2) + "\n")


def write_text(path, text):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text)


def build_fixture(root):
    write_json(root / ".claude-plugin" / "marketplace.json", MARKETPLACE)
    write_json(root / "bundle" / ".claude-plugin" / "plugin.json", BUNDLE_CLAUDE)
    write_json(root / "bundle" / ".codex-plugin" / "plugin.json", BUNDLE_CODEX)
    write_text(root / "docs" / "VERSIONING.md", VERSIONING_MD)
    write_json(root / "products" / "brothermode" / ".claude-plugin" / "plugin.json", PRODUCT_CLAUDE)
    write_json(root / "products" / "brothermode" / ".codex-plugin" / "plugin.json", PRODUCT_CLAUDE)
    write_text(root / "products" / "brothermode" / "VERSION", "3.4.4\n")
    write_json(
        root / "products" / "brothersbe" / ".claude-plugin" / "plugin.json",
        {"name": "brothersbe", "version": "3.7.3", "description": "d"},
    )
    write_text(root / "products" / "brothersbe" / "VERSION", "3.7.3\n")
    # deliberately no products/brothersbe/.codex-plugin/plugin.json


def run(args):
    return subprocess.run(
        [sys.executable, str(SCRIPT)] + args,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
    )


class VersionSourceTests(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="version_source_test_"))
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)
        build_fixture(self.tmp)

    def test_clean_fixture_checks_pass(self):
        result = run(["--check", "--root", str(self.tmp)])
        self.assertEqual(result.returncode, 0, result.stdout)
        self.assertNotIn("DRIFT", result.stdout)

    def test_drifted_bundle_manifest_reports_both_values(self):
        path = self.tmp / "bundle" / ".claude-plugin" / "plugin.json"
        doc = json.loads(path.read_text())
        doc["version"] = "9.9.9"
        write_json(path, doc)

        result = run(["--check", "--root", str(self.tmp)])
        self.assertEqual(result.returncode, 1, result.stdout)
        self.assertIn("DRIFT: bundle/.claude-plugin/plugin.json:version", result.stdout)
        self.assertIn("source=1.0.9", result.stdout)
        self.assertIn("carrier=9.9.9", result.stdout)

    def test_drifted_versioning_md_reports_both_values(self):
        path = self.tmp / "docs" / "VERSIONING.md"
        path.write_text(VERSIONING_MD.replace("1.0.9", "0.0.1"))

        result = run(["--check", "--root", str(self.tmp)])
        self.assertEqual(result.returncode, 1, result.stdout)
        self.assertIn("DRIFT: docs/VERSIONING.md:Current version", result.stdout)
        self.assertIn("source=1.0.9", result.stdout)
        self.assertIn("carrier=0.0.1", result.stdout)

    def test_drifted_ref_reports_both_values(self):
        path = self.tmp / ".claude-plugin" / "marketplace.json"
        doc = json.loads(path.read_text())
        for plugin in doc["plugins"]:
            if plugin["name"] == "brothermode":
                plugin["source"]["ref"] = "v0.0.1"
        write_json(path, doc)

        result = run(["--check", "--root", str(self.tmp)])
        self.assertEqual(result.returncode, 1, result.stdout)
        self.assertIn("DRIFT: marketplace:brothermode.source.ref", result.stdout)

    def test_write_then_check_is_clean(self):
        write_result = run(["--write", "--version", "1.0.10", "--root", str(self.tmp)])
        self.assertEqual(write_result.returncode, 0, write_result.stdout)

        check_result = run(["--check", "--root", str(self.tmp)])
        self.assertEqual(check_result.returncode, 0, check_result.stdout)
        self.assertNotIn("DRIFT", check_result.stdout)
        self.assertIn("1.0.10", check_result.stdout)

    def test_write_bumps_every_umbrella_carrier(self):
        run(["--write", "--version", "2.1.3", "--root", str(self.tmp)])

        bundle_claude = json.loads((self.tmp / "bundle" / ".claude-plugin" / "plugin.json").read_text())
        self.assertEqual(bundle_claude["version"], "2.1.3")
        bundle_codex = json.loads((self.tmp / "bundle" / ".codex-plugin" / "plugin.json").read_text())
        self.assertEqual(bundle_codex["version"], "2.1.3")

        marketplace = json.loads((self.tmp / ".claude-plugin" / "marketplace.json").read_text())
        self.assertEqual(marketplace["metadata"]["version"], "2.1.3")
        brother = next(p for p in marketplace["plugins"] if p["name"] == "brother")
        self.assertEqual(brother["version"], "2.1.3")
        for plugin in marketplace["plugins"]:
            self.assertEqual(plugin["source"]["ref"], "v2.1.3")

        versioning = (self.tmp / "docs" / "VERSIONING.md").read_text()
        self.assertIn("Current version: 2.1.3.", versioning)

    def test_write_never_bumps_product_versions(self):
        run(["--write", "--version", "5.0.0", "--root", str(self.tmp)])

        marketplace = json.loads((self.tmp / ".claude-plugin" / "marketplace.json").read_text())
        brothermode = next(p for p in marketplace["plugins"] if p["name"] == "brothermode")
        self.assertEqual(brothermode["version"], "3.4.4", "product version must not follow the umbrella bump")

        product_claude = json.loads(
            (self.tmp / "products" / "brothermode" / ".claude-plugin" / "plugin.json").read_text()
        )
        self.assertEqual(product_claude["version"], "3.4.4")

        version_file = (self.tmp / "products" / "brothermode" / "VERSION").read_text().strip()
        self.assertEqual(version_file, "3.4.4")

    def test_missing_codex_product_manifest_is_no_data_not_drift(self):
        result = run(["--check", "--root", str(self.tmp)])
        self.assertEqual(result.returncode, 0, result.stdout)
        self.assertIn("NO-DATA: products/brothersbe/.codex-plugin/plugin.json", result.stdout)

    def test_key_order_and_indentation_survive_a_write_apart_from_version(self):
        path = self.tmp / "bundle" / ".claude-plugin" / "plugin.json"
        before = path.read_text()

        run(["--write", "--version", "1.0.10", "--root", str(self.tmp)])

        after = path.read_text()
        before_lines = before.splitlines()
        after_lines = after.splitlines()
        self.assertEqual(len(before_lines), len(after_lines))
        differing = [i for i, (b, a) in enumerate(zip(before_lines, after_lines)) if b != a]
        self.assertEqual(differing, [2], "only the version line should differ: %r" % differing)
        self.assertTrue(after.endswith("\n"))
        # indentation width (2 spaces) preserved on an untouched key line
        self.assertTrue(after_lines[0].startswith("{"))
        self.assertTrue(after_lines[1].startswith("  \""))

    def test_missing_umbrella_carrier_is_drift_not_no_data(self):
        (self.tmp / "bundle" / ".codex-plugin" / "plugin.json").unlink()
        result = run(["--check", "--root", str(self.tmp)])
        self.assertEqual(result.returncode, 1, result.stdout)
        self.assertIn("DRIFT: bundle/.codex-plugin/plugin.json:version", result.stdout)
        self.assertIn("carrier=missing", result.stdout)
        self.assertNotIn("NO-DATA: bundle/.codex-plugin", result.stdout)

    def test_missing_versioning_line_is_drift_not_no_data(self):
        write_text(self.tmp / "docs" / "VERSIONING.md", "# Versioning\n\nno version line here\n")
        result = run(["--check", "--root", str(self.tmp)])
        self.assertEqual(result.returncode, 1, result.stdout)
        self.assertIn("DRIFT: docs/VERSIONING.md:Current version", result.stdout)

    def test_a_write_that_fails_part_way_restores_every_carrier(self):
        # docs/VERSIONING.md is the LAST carrier written; breaking it makes
        # the run fail after marketplace.json and both bundle manifests were
        # already rewritten, which is exactly the half bump to refuse.
        broken = "# Versioning\n\nno version line here\n"
        write_text(self.tmp / "docs" / "VERSIONING.md", broken)
        watched = [
            self.tmp / ".claude-plugin" / "marketplace.json",
            self.tmp / "bundle" / ".claude-plugin" / "plugin.json",
            self.tmp / "bundle" / ".codex-plugin" / "plugin.json",
            self.tmp / "docs" / "VERSIONING.md",
        ]
        before = {p: p.read_bytes() for p in watched}
        result = run(["--write", "--version", "1.0.10", "--root", str(self.tmp)])
        self.assertEqual(result.returncode, 1, result.stdout)
        self.assertIn("restored", result.stdout)
        for p in watched:
            self.assertEqual(p.read_bytes(), before[p], "%s was left bumped" % p.name)
        self.assertNotIn("1.0.10", (self.tmp / ".claude-plugin" / "marketplace.json").read_text())

    def test_refuses_when_source_missing(self):
        shutil.rmtree(self.tmp / ".claude-plugin")

        check_result = run(["--check", "--root", str(self.tmp)])
        self.assertEqual(check_result.returncode, 2, check_result.stdout)
        self.assertIn("NO-DATA", check_result.stdout)

        write_result = run(["--write", "--version", "1.0.10", "--root", str(self.tmp)])
        self.assertEqual(write_result.returncode, 2, write_result.stdout)
        self.assertIn("NO-DATA", write_result.stdout)


if __name__ == "__main__":
    unittest.main()

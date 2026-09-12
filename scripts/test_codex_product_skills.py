"""Product adapters preserve the shipped skills and their support roots."""
import json
import hashlib
from pathlib import Path
import sys
import tempfile
import unittest

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import codex_product_skills as CPS


def fixture(root):
    root = Path(root)
    manifest = {"name": "brother", "version": "1.0.14", "description": "Checked work",
                "skills": "./skills/", "author": {"name": "Khalil Maaouni"},
                "interface": {"displayName": "Brother", "developerName": "Khalil Maaouni",
                "category": "Productivity", "capabilities": ["Interactive", "Write"],
                "defaultPrompt": ["Check the work."],
                "shortDescription": "Checked work", "longDescription": "Checked work"}}
    for product in ("brothermode", "brothersbe"):
        p = root / "products" / product
        (p / ".claude-plugin").mkdir(parents=True)
        (p / ".claude-plugin/plugin.json").write_text(json.dumps(dict(manifest, name=product)))
        leaf = p / "skills/start"
        leaf.mkdir(parents=True)
        leaf.joinpath("SKILL.md").write_text("---\nname: start\ndescription: Start checked work.\n"
            + ("disable-model-invocation: true\n" if product == "brothermode" else "")
            + "user-invocable: false\n---\nRead ../../SKILL.md and tools/helper.py.\n")
        (p / "tools").mkdir()
        (p / "tools/helper.py").write_text("print('support')\n")
        (p / "SKILL.md").write_text("Support instructions.\n")
        (p / "hooks").mkdir()
        (p / "hooks/hooks.json").write_text('{"hooks": {}}')
    (root / "bundle/.codex-plugin").mkdir(parents=True)
    (root / "bundle/.codex-plugin/plugin.json").write_text(json.dumps(manifest))
    (root / "bundle/skills/using-brother").mkdir(parents=True)
    (root / "bundle/skills/using-brother/SKILL.md").write_text("---\nname: using-brother\ndescription: Route work.\n---\n")
    (root / ".agents/plugins").mkdir(parents=True)
    (root / ".agents/plugins/marketplace.json").write_text('{"name":"brother","plugins":[]}')
    return root


class ProductPackages(unittest.TestCase):
    def test_complete_support_root_and_restricted_invocation_survive(self):
        with tempfile.TemporaryDirectory() as d:
            src = fixture(Path(d) / "export")
            original = (src / "products/brothermode/skills/start/SKILL.md").read_bytes()
            built = CPS.build(src, Path(d) / "packages")
            package = Path(built["marketplace_root"]) / "plugins/brothermode"
            adapted = (package / "skills/start/SKILL.md").read_text()
            self.assertNotIn("disable-model-invocation", adapted)
            self.assertIn("user-invocable: false", adapted)
            self.assertIn("Read ../../SKILL.md", adapted)
            self.assertIn("allow_implicit_invocation: false", (package / "skills/start/agents/openai.yaml").read_text())
            self.assertTrue((package / "tools/helper.py").is_file())
            self.assertTrue((package / "SKILL.md").is_file())
            self.assertFalse((package / "hooks/hooks.json").exists())
            self.assertEqual((src / "products/brothermode/skills/start/SKILL.md").read_bytes(), original)
            self.assertEqual(built["skills"], {"brothermode": ["start"], "brothersbe": ["start"]})
            self.assertEqual(CPS.build(src, Path(d) / "packages"), built)
            self.assertEqual(CPS.verify(built["marketplace_root"]), [])
            (package / "tools/helper.py").write_text("changed")
            self.assertTrue(CPS.verify(built["marketplace_root"]))

    def test_private_checkout_and_symlink_inputs_are_refused(self):
        with tempfile.TemporaryDirectory() as d:
            src = fixture(Path(d) / "export")
            marker = src / "docs/plan/EXPORT-ALLOWLIST.txt"
            marker.parent.mkdir(parents=True)
            marker.write_text("products/\n")
            with self.assertRaisesRegex(ValueError, "export"):
                CPS.build(src, Path(d) / "packages")
            marker.unlink()
            (src / "products/brothermode/tools/outside").symlink_to(Path(d))
            with self.assertRaisesRegex(ValueError, "symlink"):
                CPS.build(src, Path(d) / "packages")

    def test_destination_cannot_overwrite_source(self):
        with tempfile.TemporaryDirectory() as d:
            src = fixture(Path(d) / "export")
            with self.assertRaises(ValueError):
                CPS.build(src, src / "packages")


class ShippedSkillInventory(unittest.TestCase):
    def test_actual_export_contains_all_34_core_skills(self):
        root = HERE.parent
        with tempfile.TemporaryDirectory() as d:
            source = root
            allowlist = root / "docs/plan/EXPORT-ALLOWLIST.txt"
            if allowlist.is_file():
                import export_public as EP
                source = Path(d) / "export"
                source.mkdir()
                entries = [p for p in EP.load_allowlist(str(allowlist))
                           if p.rstrip("/") in (".agents", "bundle", "products/brothermode", "products/brothersbe") or p.startswith((".agents/", "bundle/", "products/brothermode/", "products/brothersbe/"))]
                EP.build_export_tree(str(source), entries, root=str(root))
            built = CPS.build(source, Path(d) / "packages")
            expected = {name: sorted(p.parent.name for p in (source / "products" / name / "skills").glob("*/SKILL.md"))
                        for name in CPS.PRODUCTS}
            self.assertEqual(built["skills"], expected)
            self.assertEqual(sum(map(len, expected.values())) + len(list((source / "bundle/skills").glob("*/SKILL.md"))), 34)
            policies = list(Path(built["marketplace_root"]).glob("plugins/*/skills/*/agents/openai.yaml"))
            self.assertEqual(len(policies), 7)
            for name in CPS.PRODUCTS:
                package = Path(built["marketplace_root"]) / "plugins" / name
                manifest = (package / "CHECKSUMS.sha256").read_text()
                skill = "skills/start/SKILL.md"
                digest = hashlib.sha256((package / skill).read_bytes()).hexdigest()
                self.assertIn(digest + "  " + skill, manifest)
                self.assertNotIn("  hooks/hooks.json", manifest)
            self.assertEqual(CPS.verify(built["marketplace_root"]), [])


if __name__ == "__main__":
    unittest.main()

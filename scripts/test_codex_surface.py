#!/usr/bin/env python3
"""Checks that product skills and Claude command shims are visible to Codex."""
import json
import subprocess
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
import codex_surface  # noqa: E402


class CodexSurface(unittest.TestCase):
    def test_generated_aliases_are_current(self):
        problems = codex_surface.check(ROOT)
        self.assertEqual(problems, [], "run python3 scripts/codex_surface.py: " + "; ".join(problems))

    def test_every_alias_has_codex_frontmatter(self):
        expected = codex_surface.expected(ROOT)
        self.assertEqual(len(expected), 48)
        for rel in expected:
            path = ROOT / "bundle" / "skills" / rel
            text = path.read_text(encoding="utf-8")
            self.assertTrue(text.startswith("---\nname: "))
            self.assertNotIn("disable-model-invocation", text)
            canonical = rel.split("/")[0]
            if canonical in codex_surface.REAL_CONTENT_SKILLS:
                # WBS-70 U4: these two carry the real mailbox harness
                # instructions, never the generic brother_run.py stub, so
                # they never carry the generated-stub marker.
                self.assertNotIn(codex_surface.MARKER, text)
            else:
                self.assertIn(codex_surface.MARKER, text)

    def test_real_content_skills_mirror_the_product_skill(self):
        """WBS-70 U4: Cursor reads real harness instructions here, not a
        Codex brother_run.py stub. Both bundle aliases in
        REAL_CONTENT_SKILLS must carry the actual mailbox commands from
        their source product skill, byte-identical apart from the
        frontmatter `name`."""
        self.assertEqual(
            set(codex_surface.REAL_CONTENT_SKILLS),
            {"brothermode-cursor-execute", "brothermode-cursor-dispatch"})
        for canonical, (product, skill_dir) in (
                codex_surface.REAL_CONTENT_SKILLS.items()):
            bundle_path = ROOT / "bundle" / "skills" / canonical / "SKILL.md"
            text = bundle_path.read_text(encoding="utf-8")
            self.assertNotIn(codex_surface.MARKER, text)
            self.assertNotIn("brother_run.py", text)
            self.assertIn("--project", text)
            if skill_dir == "cursor-execute":
                self.assertIn("claim-next", text)
            else:
                self.assertIn("dispatch", text)
            source_path = (ROOT / "products" / product / "skills"
                            / skill_dir / "SKILL.md")
            source_text = source_path.read_text(encoding="utf-8")
            _, source_body = codex_surface.split_frontmatter(source_text)
            self.assertIn(source_body, text)

    def test_bundle_manifest_points_at_the_visible_skill_directory(self):
        manifest = json.loads((ROOT / "bundle/.codex-plugin/plugin.json").read_text())
        self.assertEqual(manifest.get("skills"), "./skills/")
        self.assertTrue((ROOT / "bundle/skills/brothersbe-review/SKILL.md").is_file())
        self.assertTrue((ROOT / "bundle/skills/brothermode-start/SKILL.md").is_file())
        self.assertTrue((ROOT / "bundle/skills/brotherme-start/SKILL.md").is_file())

    def test_runtime_mirror_is_current(self):
        proc = subprocess.run(
            [sys.executable, "scripts/codex_skills.py", "--check"],
            cwd=ROOT, capture_output=True, text=True,
        )
        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)


if __name__ == "__main__":
    unittest.main()

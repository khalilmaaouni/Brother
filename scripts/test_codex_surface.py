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
            self.assertIn(codex_surface.MARKER, text)

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

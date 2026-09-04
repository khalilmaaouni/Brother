"""The Codex package gate (rows C1 and C2).

Two things this repository ships for Codex have to stay true together, and
neither is checked by anything else here:

1. `bundle/.codex-plugin/plugin.json` passes the CANONICAL validator, the one
   installed with the Codex plugin-creator skill, not a reimplementation of it.
   A second copy of the ingestion schema written here would drift from the real
   one and then pass while Codex refused the package, which is the failure
   shape this estate keeps paying for: a green check that never read the thing
   it claims to measure. So this test SHELLS OUT to the installed validator.
   The validator imports `yaml`, which the bare interpreter here may lack, so
   the invocation is the same `uv run --with pyyaml` form the workstream brief
   names.
2. `bundle/codex-skills/` still matches a fresh generation from
   `bundle/skills/`, and the strip it performs is the thing that turns a real
   validator refusal into a pass. Seven BrotherMode skills set
   `disable-model-invocation: true` (Claude's way of keeping a delivery, an
   update or a kill switch off model auto-invocation) and the canonical
   validator refuses that value outright, so the two clients cannot read the
   same bytes. The generated mirror is checked here BOTH ways: it must not be
   stale, and the canonical validator must refuse a skill carrying the key and
   accept the same skill after `codex_skills` has been through it. A strip
   nobody drove backwards is a claim.
3. `bundle/skills/` itself, which is what a Codex install of `./bundle`
   actually reads, carries no frontmatter the canonical validator refuses.
   `plugin.json`'s `skills` field cannot redirect this: `validate_plugin.py`
   requires it to resolve to exactly `skills`, and `validate_skill_manifests`
   opens `<plugin_root>/skills` with the name hardcoded (measured 2026-09-04,
   verbatim refusal: "plugin.json field `skills` must resolve to `skills`").
   So the mirror cannot be pointed at, and this guard is what keeps the
   directory Codex does read installable.
4. Every plugin entry in `.agents/plugins/marketplace.json` resolves to a real
   directory holding a real `.codex-plugin/plugin.json`, with the entry's name
   matching that manifest's own name. A marketplace pointing at a path that
   does not exist installs nothing, and `codex plugin list --available` reports
   the entry anyway.

NO-DATA, NEVER A PASS. When the canonical validator is not installed on this
machine, the validator case SKIPS with a reason carrying the string NO-DATA,
which scripts/check_all.sh reads as its own verdict rather than as a pass. The
marketplace case needs nothing outside this repository and always runs.
"""
import json
import os
import pathlib
import shutil
import subprocess
import sys
import tempfile
import unittest

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))
import codex_skills as CS  # noqa: E402
#: The canonical validator, resolved from the RUNNING USER's home rather than
#: written down. It was an absolute path under one person's home until
#: 2026-09-04, which made this file the one Codex artifact that leaked a
#: machine-local path into the public export (ship gate 6 found it here, in
#: its own lane's file). BROTHER_CODEX_VALIDATOR overrides it for a machine
#: that installed the plugin-creator skill somewhere else; an absent
#: validator is already a named NO-DATA below, never a pass.
_CODEX_HOME = pathlib.Path(
    os.path.expanduser(os.environ.get("CODEX_HOME") or "~/.codex")
)
VALIDATOR = pathlib.Path(
    os.environ.get(
        "BROTHER_CODEX_VALIDATOR",
        str(_CODEX_HOME / "skills/.system/plugin-creator/scripts/validate_plugin.py"),
    )
)
MARKETPLACE = ROOT / ".agents" / "plugins" / "marketplace.json"


class CanonicalValidator(unittest.TestCase):

    def test_bundle_passes_the_installed_validator(self):
        if not VALIDATOR.is_file():
            self.skipTest(
                f"NO-DATA: the canonical Codex validator is not installed at {VALIDATOR}"
            )
        if shutil.which("uv") is None:
            self.skipTest("NO-DATA: uv is not on PATH, so pyyaml cannot be supplied")
        try:
            proc = subprocess.run(
                ["uv", "run", "--with", "pyyaml", "python3", str(VALIDATOR), "bundle"],
                cwd=str(ROOT),
                capture_output=True,
                text=True,
                timeout=300,
            )
        except (OSError, subprocess.SubprocessError) as error:
            self.fail(f"could not run the canonical validator: {error}")
        self.assertEqual(
            proc.returncode, 0, f"stdout:\n{proc.stdout}\nstderr:\n{proc.stderr}"
        )
        self.assertIn("Plugin validation passed", proc.stdout)


class CodexSkillsMirror(unittest.TestCase):
    """bundle/codex-skills is generated; bundle/skills is Claude's and is
    hand maintained. Nothing here writes: every case reads the tree or works
    in a temporary directory."""

    def test_generated_mirror_is_not_stale(self):
        ok, problems = CS.check()
        self.assertTrue(ok, "run python3 scripts/bundle_runtime.py: " + "; ".join(problems))

    def test_generator_never_writes_into_claude_skills(self):
        """The source directory is not the destination, and every path the
        generator produces lands under bundle/codex-skills. A generator that
        rewrote bundle/skills would silently strip the key Claude needs."""
        source = pathlib.Path(CS.SOURCE_DIR).resolve()
        dest = pathlib.Path(CS.DEST_DIR).resolve()
        self.assertNotEqual(source, dest)
        self.assertNotIn(source, dest.parents)
        files, problems = CS.build()
        self.assertEqual(problems, [], f"the generator cannot read its source: {problems}")
        self.assertTrue(files, "the generator produced nothing at all")
        for rel in files:
            self.assertFalse(
                (source / rel).is_dir(), f"{rel} would be written over a source directory"
            )

    def test_the_strip_turns_a_real_validator_refusal_into_a_pass(self):
        """FAILS BEFORE, PASSES AFTER, against the canonical validator, not a
        reimplementation of it. The fixture carries the exact frontmatter the
        seven BrotherMode skills carry."""
        if not VALIDATOR.is_file():
            self.skipTest(f"NO-DATA: the canonical Codex validator is not installed at {VALIDATOR}")
        if shutil.which("uv") is None:
            self.skipTest("NO-DATA: uv is not on PATH, so pyyaml cannot be supplied")
        blocking = (
            "---\n"
            "name: deliver\n"
            "description: Close the work with evidence in hand.\n"
            "disable-model-invocation: true\n"
            "allowed-tools: Bash, Read\n"
            "---\n\n# Deliver\n"
        )
        lines, body = CS.split_frontmatter(blocking)
        self.assertIsNotNone(lines, "the fixture must have readable frontmatter")
        kept, stripped = CS.strip_frontmatter(lines)
        self.assertIn("disable-model-invocation", stripped)
        self.assertIn("allowed-tools", stripped)
        cleaned = CS.render(kept, body)
        self.assertNotIn("disable-model-invocation", cleaned)
        self.assertIn("name: deliver", cleaned)
        self.assertIn("description:", cleaned)
        verdicts = {}
        for label, text in (("before", blocking), ("after", cleaned)):
            with tempfile.TemporaryDirectory() as tmp:
                root = pathlib.Path(tmp)
                manifest_dir = root / ".codex-plugin"
                manifest_dir.mkdir()
                # The repository's OWN manifest, so this fixture cannot pass
                # or fail for a manifest reason unrelated to the skill: the
                # only thing that differs between the two runs is the
                # SKILL.md below.
                fixture = json.loads(
                    (ROOT / "bundle" / ".codex-plugin" / "plugin.json").read_text(
                        encoding="utf-8"
                    )
                )
                fixture["skills"] = "./skills/"
                (manifest_dir / "plugin.json").write_text(
                    json.dumps(fixture), encoding="utf-8"
                )
                leaf = root / "skills" / "deliver"
                leaf.mkdir(parents=True)
                (leaf / "SKILL.md").write_text(text, encoding="utf-8")
                try:
                    proc = subprocess.run(
                        ["uv", "run", "--with", "pyyaml", "python3", str(VALIDATOR), str(root)],
                        capture_output=True,
                        text=True,
                        timeout=300,
                    )
                except (OSError, subprocess.SubprocessError) as error:
                    self.fail(f"could not run the canonical validator: {error}")
                verdicts[label] = (proc.returncode, proc.stdout + proc.stderr)
        self.assertNotEqual(
            verdicts["before"][0],
            0,
            "the validator accepted disable-model-invocation: true, so this "
            "gate is measuring nothing:\n" + verdicts["before"][1],
        )
        self.assertIn("disable-model-invocation", verdicts["before"][1])
        self.assertEqual(
            verdicts["after"][0], 0, "the stripped skill was still refused:\n" + verdicts["after"][1]
        )

    def test_claude_skills_carry_nothing_codex_refuses(self):
        """What a Codex install of ./bundle actually reads is bundle/skills.
        A skill landing there with the key would refuse the whole package at
        install time; this turns that into a red gate at authoring time."""
        source = pathlib.Path(CS.SOURCE_DIR)
        self.assertTrue(source.is_dir(), f"{source} is missing")
        seen = 0
        for skill_md in sorted(source.glob("*/SKILL.md")):
            seen += 1
            text = skill_md.read_text(encoding="utf-8")
            lines, _ = CS.split_frontmatter(text)
            self.assertIsNotNone(lines, f"{skill_md} has no closed frontmatter")
            _, stripped = CS.strip_frontmatter(lines)
            for refused in ("disable-model-invocation", "disable_model_invocation"):
                self.assertNotIn(
                    refused,
                    stripped,
                    f"{skill_md} sets {refused}, which the canonical Codex "
                    f"validator refuses; bundle/skills is the directory Codex "
                    f"installs, and its name cannot be redirected",
                )
        self.assertTrue(seen, "bundle/skills holds no skill at all")


class RepoMarketplace(unittest.TestCase):

    def load(self):
        try:
            return json.loads(MARKETPLACE.read_text(encoding="utf-8"))
        except OSError as error:
            self.fail(f"cannot read {MARKETPLACE}: {error}")
        except json.JSONDecodeError as error:
            self.fail(f"{MARKETPLACE} is not valid JSON: {error}")

    def test_entries_resolve_to_real_codex_plugins(self):
        payload = self.load()
        plugins = payload.get("plugins")
        self.assertIsInstance(plugins, list)
        self.assertTrue(plugins, "the repo marketplace lists no plugins")
        for entry in plugins:
            name = entry.get("name")
            source = entry.get("source") or {}
            self.assertEqual(
                source.get("source"), "local", f"entry {name} is not a local source"
            )
            rel = source.get("path")
            self.assertIsInstance(rel, str, f"entry {name} has no source.path")
            self.assertTrue(
                rel.startswith("./"), f"entry {name} path must be relative and begin ./"
            )
            # A repo/team marketplace at <root>/.agents/plugins/marketplace.json
            # resolves its entry paths against <root>, proven live on
            # 2026-09-04: `codex plugin list --available --json` reported
            # source.path as <root>/bundle for the entry "./bundle".
            plugin_root = (ROOT / rel[2:]).resolve()
            self.assertTrue(
                plugin_root.is_dir(), f"entry {name} points at a missing directory"
            )
            manifest_path = plugin_root / ".codex-plugin" / "plugin.json"
            self.assertTrue(
                manifest_path.is_file(), f"entry {name} has no .codex-plugin/plugin.json"
            )
            try:
                manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError) as error:
                self.fail(f"{manifest_path} is unreadable: {error}")
            self.assertEqual(
                manifest.get("name"),
                name,
                f"entry {name} does not match the manifest name it points at",
            )

    def test_marketplace_carries_the_fields_codex_reads(self):
        payload = self.load()
        self.assertIsInstance(payload.get("name"), str)
        self.assertIsInstance((payload.get("interface") or {}).get("displayName"), str)
        for entry in payload.get("plugins") or []:
            policy = entry.get("policy") or {}
            self.assertIn(
                policy.get("installation"),
                {"NOT_AVAILABLE", "AVAILABLE", "INSTALLED_BY_DEFAULT"},
            )
            self.assertIn(policy.get("authentication"), {"ON_INSTALL", "ON_USE"})
            self.assertIsInstance(entry.get("category"), str)

    def test_claude_marketplace_is_untouched_by_this_lane(self):
        """The Codex marketplace is a SEPARATE file. Regressing Claude's own
        marketplace is the one thing ship gate 1 forbids by name, so this
        asserts the Claude file still exists and still lists its three
        plugins; a lane that moved Brother into .agents/ and deleted the
        Claude entry would go red here rather than silently."""
        claude = ROOT / ".claude-plugin" / "marketplace.json"
        self.assertTrue(claude.is_file(), "the Claude marketplace is missing")
        try:
            payload = json.loads(claude.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as error:
            self.fail(f"{claude} is unreadable: {error}")
        names = {entry.get("name") for entry in payload.get("plugins") or []}
        self.assertEqual(names, {"brothermode", "brothersbe", "brother"})


if __name__ == "__main__":
    unittest.main()

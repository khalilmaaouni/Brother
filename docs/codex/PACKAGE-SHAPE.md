# Codex package shape

The Codex plugin root in this repository is `bundle/`. Its manifest is
[bundle/.codex-plugin/plugin.json](../../bundle/.codex-plugin/plugin.json).
The same directory also carries a distinct
[Claude Code manifest](../../bundle/.claude-plugin/plugin.json).
This page describes those files as shipped in this checkout, not a proposed
package or evidence that a signed-in host has run it.

## The Codex manifest

| Field | Current value or contents |
| :-- | :-- |
| `name` | `brother` |
| `version` | `1.0.18` |
| `description` | `Turn a plain-language outcome into checked work, a rerunnable receipt, useful local memory, and a human acceptance decision.` |
| `author` | An object whose `name` is `Khalil Maaouni`. |
| `homepage`, `repository` | Both point to `https://github.com/khalilmaaouni/Brother`. |
| `license` | `MIT` |
| `keywords` | `verified delivery`, `receipts`, `execution provenance`, `change assurance`, `local memory`, `codex` |
| `skills` | `./skills/`, relative to the plugin root, hence `bundle/skills/`. |
| `interface` | Display metadata, capabilities and starter prompts, detailed below. |

The `interface` object contains:

- `displayName`: `Brother`.
- `shortDescription`: `Checked work with a receipt you can rerun.`
- `longDescription`: prose describing checked work, a rerunnable receipt,
  local memory, a human acceptance decision, and the distinction between
  `PASS`, `FAIL` and `NO-DATA`.
- `developerName`: `Khalil Maaouni`.
- `category`: `Productivity`.
- `capabilities`: `Interactive` and `Write`.
- `websiteURL`: the same public repository URL as `homepage`.
- `defaultPrompt`: the three strings `Check this migration before I merge it.`,
  `Explain why last week's report number looks wrong.`, and
  `Set up a receipt for the work I am about to start.`

These are manifest declarations. In particular, the capability labels do not
establish hook enforcement. The Codex manifest has no `hooks` or
`dependencies` field and does not point `skills` at `codex-skills/`.

## Skills and the generated companion mirror

The relevant paths are:

```text
bundle/
  .codex-plugin/plugin.json
  .claude-plugin/plugin.json
  skills/<skill>/SKILL.md
  codex-skills/<skill>/SKILL.md
  codex-skills/STRIPPED.json
  runtime/
  hooks/union.json
```

`bundle/skills/` is the skill directory the Codex manifest selects. The
package tests record that the canonical validator requires `skills` to
resolve to that exact directory and reads `<plugin_root>/skills`. Changing
the field to `./codex-skills/` is not the implemented installation path.
The bundle's routing skill is `skills/using-brother/SKILL.md`.

[scripts/codex_skills.py](../../scripts/codex_skills.py) generates
`bundle/codex-skills/` from `bundle/skills/`. It preserves the top-level
frontmatter fields `name` and `description`, removes other top-level fields
with their continuation lines, and keeps the skill body. Supporting files
beside each skill are copied byte for byte. Missing required fields,
unreadable inputs or unclosed frontmatter are reported as problems instead
of being repaired by guessing. Generation also removes stale output files.

[STRIPPED.json](../../bundle/codex-skills/STRIPPED.json) records the generator,
source directory, accepted frontmatter keys, and each removed key's original
source lines grouped by skill. The current record is:

```json
{
  "accepted_frontmatter_keys": ["name", "description"],
  "generated_by": "scripts/codex_skills.py",
  "skills": {},
  "source": "bundle/skills"
}
```

The empty `skills` object means this generation removed no frontmatter
fields. It does not mean the mirror contains no skills. There is no
timestamp in the record, so unchanged input produces identical output.

The mirror exists to preserve compatibility without editing product source
skills. Seven BrotherMode product skills carry
`disable-model-invocation: true` for Claude Code. The package test exercises
the canonical validator refusing that value and accepting the stripped
fixture. Those product protections stay in place. Separately, the bundle's
actual `skills/` directory must itself remain acceptable to Codex; a clean
companion mirror cannot rescue incompatible frontmatter in that directory.

Regenerate through the normal generator rather than editing the mirror or
its record:

```sh
python3 scripts/bundle_runtime.py
```

The narrower read-only mirror check is:

```sh
python3 scripts/codex_skills.py --check
```

`python3 scripts/bundle_runtime.py --check` also checks generated runtime
and hook artifacts along with the skill mirror.

## Differences from the Claude Code bundle

Both manifests name `brother` at version `1.0.18`, and share the description,
author, public repository and homepage, license, and the first five keywords.
Their remaining fields differ as follows:

| Surface | Codex manifest | Claude Code manifest |
| :-- | :-- | :-- |
| Skills declaration | Explicit `"skills": "./skills/"`. | No `skills` field. |
| Interface metadata | The `interface` object above. | No `interface` field. |
| Product dependencies | No `dependencies` field. | `brothermode@^3.4.2` and `brothersbe@^3.7.0`. |
| Host keyword | `codex` | `claude code` |
| Hook declaration | No `hooks` field. | No `hooks` field in the umbrella manifest; product plugins supply their conventional `hooks/hooks.json`. |

The generated `bundle/hooks/union.json` is deliberately not named
`bundle/hooks/hooks.json`: Claude Code would otherwise load the combined
hooks alongside the dependent products' hooks and duplicate execution.
The Codex package validator's refusal of a `hooks` manifest field is a
separate boundary. Codex's manual user hooks installer reads the product
definitions and writes a chosen Codex home's `hooks.json`; package installation
alone does not perform that step. See [HOOKS-MAPPING.md](HOOKS-MAPPING.md)
for the installer, trust read-back, host detection, and verdict limits.

## Verification boundaries

[scripts/test_codex_package.py](../../scripts/test_codex_package.py) checks
the actual bundle with the installed canonical validator, mirror freshness,
the refused-then-stripped fixture, the frontmatter in `bundle/skills/`, and
the marketplace paths in `.agents/plugins/marketplace.json`. Run it with:

```sh
python3 scripts/test_codex_package.py -v
```

The canonical-validator cases report a skip carrying `NO-DATA` if the
validator or `uv` is unavailable. `BROTHER_CODEX_VALIDATOR` can select the
validator path. A skipped validator check is not proof of package acceptance.

The export test's `CODEX_ARTIFACTS` list requires `AGENTS.md`, the marketplace
catalog, the Codex manifest, `STRIPPED.json`, and these two documentation
pages to reach the exported tree. The requested artifact presence check is:

```sh
python3 -m pytest scripts/test_export_public.py -q -k test_every_codex_artifact_reaches_the_exported_tree
```

That test establishes file presence in the export. Package validation checks
package shape. Neither result establishes a signed-in run, hook trust, or a
live denial of a forbidden action.

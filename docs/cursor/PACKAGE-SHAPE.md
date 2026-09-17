# Cursor package shape

The Cursor plugin root in this repository is `bundle/`, the same directory
Codex and Claude Code share. Its manifest is
[bundle/.cursor-plugin/plugin.json](../../bundle/.cursor-plugin/plugin.json).
This page describes that file as shipped in this checkout, not a proposed
package or evidence that a signed-in host has run it. See
[../codex/PACKAGE-SHAPE.md](../codex/PACKAGE-SHAPE.md) for the Codex twin.

## The Cursor manifest

| Field | Current value or contents |
| :-- | :-- |
| `name` | `brother` |
| `version` | `1.0.18` |
| `description` | `Turn a plain-language outcome into checked work, a rerunnable receipt, useful local memory, and a human acceptance decision.` |
| `author` | An object whose `name` is `Khalil Maaouni`. |
| `homepage`, `repository` | Both point to `https://github.com/khalilmaaouni/Brother`. |
| `license` | `MIT` |
| `keywords` | `verified delivery`, `receipts`, `execution provenance`, `change assurance`, `local memory`, `cursor` |
| `skills` | `["./skills/"]`, relative to the plugin root, hence `bundle/skills/`. |
| `commands` | `["./commands/"]` |
| `agents` | `["./agents/"]` |
| `rules` | `./rules/` |
| `hooks` | `./cursor-hooks/hooks.json` |

The Cursor manifest declares a `hooks` field pointing at
[bundle/cursor-hooks/hooks.json](../../bundle/cursor-hooks/hooks.json), unlike
the Codex manifest, which has neither `hooks` nor `dependencies`. Cursor's
plugin loader wires that file's commands automatically when the bundle is
installed as a local plugin; this is a manifest declaration, not evidence
that a hook fired or denied a forbidden action in a live session.

## Two Cursor install paths, not one

This repository ships two separate routes into Cursor, and this page
describes both rather than assuming the bundle route covers everything:

1. **Bundle local plugin.** [scripts/cursor_plugin_install.py](../../scripts/cursor_plugin_install.py)
   copies `bundle/` into `~/.cursor/plugins/local/brother`. The copied
   manifest's `hooks` field points Cursor at `cursor-hooks/hooks.json`
   inside that copy, so hooks load through the plugin loader the same way
   `skills`, `commands`, `agents` and `rules` do. The script's own docstring
   states enforcement stays advisory: a live canary that these hooks fire
   and honor `permission: deny` has not been measured.
2. **Standalone product install.** [products/brothermode/tools/bm_cursor.py](../../products/brothermode/tools/bm_cursor.py)
   `emit-hooks` writes a Cursor `hooks.json` directly for a checkout that is
   running BrotherMode independently of the umbrella bundle, using
   [products/brothermode/hooks/cursor.hooks.json](../../products/brothermode/hooks/cursor.hooks.json)
   as its template (`${BROTHERMODE_ROOT}` substituted for the real checkout
   path). Default paths: `~/.cursor/hooks.json`,
   `~/.cursor/brothermode-install.json`, install target
   `~/.cursor/brothermode`.

The two paths use different event names from Claude Code's and Codex's:
`preToolUse`, `beforeShellExecution`, `postToolUse`, `afterShellExecution`,
and `sessionStart`/`sessionEnd` rather than `PreToolUse`/`PostToolUse`/
`SessionStart`/`SessionEnd`. The bundle route's commands run through
`bm_cursor_hook.py` as a wrapper naming the real product script with
`--run`; the standalone route's template calls `bm_cursor_hook.py` directly
with the underlying event name as its own argument. Neither mapping is
implemented by the Codex installer described in
[../codex/HOOKS-MAPPING.md](../codex/HOOKS-MAPPING.md); they are separate
code paths for a separate host.

## Skills and the generated companion mirror

`bundle/skills/` is the skill directory both the Cursor and Codex manifests
select; Cursor's `skills` field is a list (`["./skills/"]`) where Codex's is
a bare string (`"./skills/"`). Cursor does not read
`bundle/codex-skills/`, the stripped mirror
[scripts/codex_skills.py](../../scripts/codex_skills.py) generates for
Codex's stricter frontmatter validator; Cursor reads `bundle/skills/`
directly, unedited. There is no Cursor-specific skill mirror to regenerate.

## Differences from the Codex and Claude Code bundles

All three manifests name `brother` at version `1.0.18` and share the
description, author, public repository and homepage, license, and the first
five keywords. Cursor's remaining fields differ from Codex's as follows
(Codex's own table is in [../codex/PACKAGE-SHAPE.md](../codex/PACKAGE-SHAPE.md)):

| Surface | Cursor manifest | Codex manifest |
| :-- | :-- | :-- |
| Skills declaration | List `["./skills/"]`. | String `"./skills/"`. |
| Additional surfaces | `commands`, `agents`, `rules`. | None of these fields. |
| Interface metadata | No `interface` field. | The `interface` object (display name, description, capabilities, starter prompts). |
| Hook declaration | `"hooks": "./cursor-hooks/hooks.json"`. | No `hooks` field. |
| Host keyword | `cursor` | `codex` |

## Verification boundaries

[scripts/test_cursor_plugin.py](../../scripts/test_cursor_plugin.py) covers
the bundle's Cursor manifest and the standalone installer/emitter paths.
Run it with:

```sh
python3 scripts/test_cursor_plugin.py -v
```

The export test's `CODEX_ARTIFACTS` list requires the Codex-named
artifacts and these two documentation pages to reach the exported tree;
there is no equivalent `CURSOR_ARTIFACTS` list gating this page's own
presence in export today. Package validation checks package shape.
Neither this file's presence nor a passing test above establishes a
signed-in Cursor session, hook trust, or a live denial of a forbidden
action.

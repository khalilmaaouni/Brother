# The Codex package shape, and why it is one manifest and not three

Ship gates 1 and 2 of the Codex workstream (board rows C1 and C2), decided
2026-09-04. Measured against Codex CLI `codex-cli 0.153.0-alpha.5`, the
app-bundled binary at `/Applications/ChatGPT.app/Contents/Resources/codex`.

## What ships

| Path | What it is |
| --- | --- |
| `bundle/.codex-plugin/plugin.json` | The native Codex manifest for the umbrella `brother` plugin, beside the Claude one at `bundle/.claude-plugin/plugin.json`. Same name, same version string (1.0.1), same author, repository, homepage, license. |
| `.agents/plugins/marketplace.json` | The repo/team marketplace. One entry, `brother`, with a local source at `./bundle`. |
| `scripts/test_codex_package.py` | The gate. Shells out to the canonical validator and resolves every marketplace entry. Registered in `scripts/check_all.sh` as `codex-package-self`. |

`.claude-plugin/marketplace.json` is NOT touched by this lane. That is ship
gate 1's own wording, and `test_codex_package.py` asserts it directly: the
Claude marketplace must still exist and still list `brothermode`,
`brothersbe` and `brother`, so a later lane that moved Brother into `.agents/`
and dropped the Claude entry goes red here instead of silently.

## The decision: the umbrella only, not the two products

The founder gate says "a native Codex plugin package exists alongside the
Claude package". The Claude package is three plugins (`brothermode`,
`brothersbe`, and the `brother` umbrella that depends on both). This lane
gives Codex ONE, the umbrella, and here is why.

1. **The umbrella is where the Codex-visible capability already lives.**
   Codex reads `skills`. The umbrella carries `bundle/skills/` and
   `bundle/runtime/`, mirrored from both products by
   `scripts/bundle_runtime.py`. A product-level Codex manifest would add a
   second discovery surface over content the umbrella already carries.
2. **Most of what a product manifest exists to carry, Codex has no concept
   for.** `products/brothermode` and `products/brothersbe` each ship
   `hooks/hooks.json`, `commands/` and `agents/`. Codex 0.153's plugin
   manifest accepts none of those three (see HOOKS-MAPPING.md for the hooks
   measurement). Shipping two more manifests would mostly ship fields Codex
   ignores.
3. **The validator walks `skills/` and would then be validating 33 more skill
   manifests this lane has not read.** `validate_plugin.py` opens every
   `skills/*/SKILL.md`, parses its frontmatter, and rejects any that is
   missing `name` or `description` or that sets `disable-model-invocation`
   true. Two product manifests would put those 33 files inside a ship gate in
   the same change that first writes the gate. That is work with its own
   findings, not a free line in a manifest.
4. **It is additive to reverse.** Adding `products/<name>/.codex-plugin/` and
   two more marketplace entries later changes nothing written here: the test
   loops over `plugins[]`, so new entries are checked the moment they exist.

The flip condition: if a Codex user needs `brothermode` or `brothersbe`
installable on its own, without the umbrella, that is when the two product
manifests get written, and the entry test above already covers them.

## The git source shape for the public repository

Read the spec before assuming this one. `references/plugin-json-spec.md`
documents exactly one plugin-entry source type, `local`:

    "source": { "source": "local", "path": "./plugins/<plugin-name>" }

There is NO documented per-entry git source. Codex reaches a Git-hosted
marketplace by adding the MARKETPLACE itself from Git, not by writing a git
source into `plugins[]`. `codex plugin marketplace add --help` on 0.153 says
the source argument is "a local path, owner/repo[@ref], HTTPS Git URL, or SSH
Git URL", with `--ref <REF>` and `--sparse <PATH>`. So the public repository's
shape is the same `marketplace.json` this repository already carries, reached
by:

    codex plugin marketplace add khalilmaaouni/Brother --ref v1.0.1
    codex plugin add brother@brother

The `./bundle` entry then resolves inside the fetched checkout, exactly as it
resolves inside a local one. Anything beyond that (a per-entry git source, a
subdirectory pin like Claude's `git-subdir`) is NO-DATA against this
version's own spec and help, and this lane did not invent one.

## Marketplace roots resolve to the repository root, measured

The spec says an entry path is "relative plugin path based on the marketplace
root", and its only worked example is the personal marketplace, where
`~/.agents/plugins/marketplace.json` plus `./plugins/x` resolves to
`~/plugins/x`. So the root is two levels above the file, not the file's own
directory. Proven live rather than reasoned: with the marketplace added from
the repository root, `codex plugin list --available --json` reported

    "source": { "source": "local", "path": "<repo-root>/bundle" }

for the entry written as `"./bundle"`.

## What the manifest deliberately omits

- `hooks`: rejected by the validator. See HOOKS-MAPPING.md.
- `dependencies`: the Claude manifest carries
  `["brothermode@^3.4.2", "brothersbe@^3.7.0"]`. Codex's validator rejects
  every top-level field outside its allowed set, and `dependencies` is not in
  it. The umbrella carries the mirrored runtime instead of declaring a
  dependency Codex cannot resolve.
- `mcpServers` and `apps`: the validator requires the companion file to exist
  when the field is present, and neither `bundle/.mcp.json` nor
  `bundle/.app.json` exists. Omitted rather than stubbed.
- `interface.composerIcon`, `logo`, `logoDark`, `screenshots`, `brandColor`:
  no asset exists in `bundle/`, and the validator rejects an asset path
  pointing at a missing file. No placeholder assets were invented.
- `interface.privacyPolicyURL` and `termsOfServiceURL`: this project publishes
  neither. An absent optional field is honest; a URL that 404s is not.

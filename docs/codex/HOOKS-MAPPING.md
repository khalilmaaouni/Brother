# Hooks under Claude Code and Codex

Brother's product hooks are delivered by the Claude Code plugin loader.
The Codex package does not deliver those hooks: Codex needs a separate
installation into its user hooks configuration. Installing the package or
validating its skills does not prove that a hook runs or refuses a write.

This page describes the implementation in this checkout. The host measurement
recorded in [sbe_hooks_wiring.py](../../products/brothersbe/tools/sbe_hooks_wiring.py)
and [codex_hooks_install.py](../../scripts/codex_hooks_install.py) was made
against Codex `0.153.0-alpha.5`; it is not a new measurement of every Codex
release. The recorded `codex features list` output distinguishes:

```text
hooks stable true
plugin_hooks removed false
```

The same source records the canonical plugin validator refusing a manifest
with a `hooks` field, exit 1. Codex has a hooks mechanism, but there is no
installed plugin copy of `hooks.json` for the Claude shipped-versus-installed
comparison. `CODEX_NO_DATA_DETAIL` states that boundary and names the manual
installer below. It does not mean that Codex has no hooks of any kind.

## Product definitions and Claude Code delivery

The source definitions are
[BrotherMode hooks/hooks.json](../../products/brothermode/hooks/hooks.json)
and [BrotherSBE hooks/hooks.json](../../products/brothersbe/hooks/hooks.json).
Their command paths use `${CLAUDE_PLUGIN_ROOT}/tools/`, resolved by the
Claude Code plugin loader to the installed product directory. The files use
an outer `hooks` object, event names, optional tool matchers, and command
entries. `timeout`, when present, is in seconds.

The current event wiring is below. Names in command columns are scripts
under the corresponding product's `tools/` directory; arguments are included.

| Event | BrotherMode commands | BrotherSBE commands |
| :-- | :-- | :-- |
| `SessionStart` | `bm_sessionstart.py`, `bm_vault.py refresh` | `sbe_sessionstart.py` |
| `SessionEnd` | `bm_telemetry.py outcomes-append` | `sbe_telemetry.py outcomes-append` |
| `PreCompact` | `bm_hookchain.py precompact` | `sbe_autosave.py precompact`, `sbe_telemetry.py precompact-brief` |
| `Stop` | `bm_hookchain.py stop` | `sbe_session_reconcile.py` |
| `PreToolUse` | `bm_fence_hook.py`, `vault_recall_hook.py check`; for `Bash`, also `bm_bash_audit.py pre`, `bm_session_cap.py` | `sbe_authority_hook.py`, `sbe_fence_hook.py`; for `Bash`, `sbe_bash_write_guard.py` |
| `PostToolUse` | For `Bash`, `bm_bash_audit.py post`, `attempt_hook.py` | No entry |

BrotherMode's first `PreToolUse` block matches
`Edit|Write|MultiEdit|NotebookEdit|Bash`. BrotherSBE's authority and fence
blocks match `Edit|Write|MultiEdit|NotebookEdit|CreateDirectory|Delete|apply_patch`.
The installer preserves these strings; it does not translate tool names or
add missing matchers. Declared wiring alone cannot establish coverage of a
host's tool calls.

[bm_hookchain.py](../../products/brothermode/tools/bm_hookchain.py) defines
the commands behind BrotherMode's two chain entries. `stop` runs telemetry
`stop-warn`, lead `watchdog --tick`, view `render --if-stale`, and view
`alert --tick`. `precompact` runs autosave `precompact` and telemetry
`precompact-brief`. The chain feeds each program the same stdin payload,
runs each despite earlier failures, and returns the last program's exit code.

The umbrella Claude manifest declares the two products as dependencies.
The generated [bundle/hooks/union.json](../../bundle/hooks/union.json)
contains their combined wiring, but is deliberately not named
`bundle/hooks/hooks.json`. [bundle_runtime.py](../../scripts/bundle_runtime.py)
explains and checks that choice: the conventional name would let Claude Code
load the umbrella copy as well as the product hooks and fire them twice.
The separate `products/brothermode/hooks/cursor.hooks.json` is another host's
adapter, not an input to the Codex installer.

## Manual Codex wiring

Run from the repository root, replacing `<dir>` with the intended Codex home:

```sh
python3 scripts/codex_hooks_install.py --codex-home <dir> --trust
```

The script reads both product `hooks/hooks.json` files by default and merges
their event blocks in product order, BrotherMode then BrotherSBE. It writes
`<dir>/hooks.json`, asks `codex app-server` for `hooks/list`, and filters the
reply by the canonical path of that file. This is user configuration wiring,
not plugin hook delivery. Run subsequent Codex sessions with the same
`CODEX_HOME` to use that configuration.

The translation expands `${CLAUDE_PLUGIN_ROOT}` to each product's absolute
directory, changes `timeout` to `timeoutSec`, sets `async` to `false`, and
preserves nonempty matchers and `statusMessage` values. The checkout must
remain at those paths for the commands to resolve. Unknown events and empty
commands are skipped with a named `NO-DATA` message. The accepted event set
is `CODEX_EVENTS` in the installer, recorded from the measured host schema.

Installation replaces the entire target `hooks.json`; it does not merge
existing user hooks. Use a dedicated home or preserve existing configuration
before running it. The script canonicalizes the home path and refuses the
default `~/.codex` unless `--allow-default-home` is supplied. It may have
written the hooks file before a later read-back or trust step fails.

With `--trust`, the script writes a marked section in `<dir>/config.toml`.
For each hook sourced from its own file, it writes a `[hooks.state."<key>"]`
table with `enabled = true` and `trusted_hash` taken from the host's
`currentHash`. It then calls `hooks/list` again. Its trust `PASS` requires a
nonempty set of own entries, all reporting `trustStatus == "trusted"`.
It leaves unrelated configuration lines intact and refuses to overwrite a
`[hooks.state.` table outside its markers. Trust decisions do not include
entries from other source paths. Warnings about other files are reported
separately; warnings about its own file prevent a successful read-back.

| Flag | Implemented behavior |
| :-- | :-- |
| `--codex-home <dir>` | Select the home; if omitted, use `CODEX_HOME`. Refuse if neither supplies a path. |
| `--allow-default-home` | Permit targeting the real `~/.codex`. |
| `--product <dir>` | Repeatable product directories containing `hooks/hooks.json`; replaces the default product list. |
| `--trust` | Persist host-reported hashes and confirm trust with a second read-back. Without it, untrusted hooks produce `NO-DATA` but can still return exit 0. |
| `--codex-bin <path>` | Select the binary for read-back. The default is `/Applications/ChatGPT.app/Contents/Resources/codex`. |
| `--cwd <dir>` | Set the directory queried by `hooks/list`; defaults to the current working directory. |
| `--check` | Write nothing. Compare the entire parsed hooks document with the translated product definitions. `PASS` exits 0; `FAIL` or absent-file `NO-DATA` exits 1. Does not query trust or execute hooks. |
| `--uninstall` | Remove commands exactly matching the selected products' current translated commands and remove the marked trust section. Preserve other hooks; delete the hooks file if no hooks remain after removal. Cannot be combined with `--check`. |
| `--help` | Print the actual option reference. |

To inspect the file after installation or remove the wiring:

```sh
python3 scripts/codex_hooks_install.py --codex-home <dir> --check
python3 scripts/codex_hooks_install.py --codex-home <dir> --uninstall
```

Use the same `--product` selection when checking or uninstalling a custom
installation. Uninstall identifies commands by their exact current paths and
arguments, not by a general Brother label.

## Host detection and the doctor's verdict

`sbe_hooks_wiring.running_client()` delegates to `brother_paths.client()`;
if that module cannot be imported, it returns an empty string. The shared
helper ships in both products:
[BrotherMode brother_paths.py](../../products/brothermode/tools/brother_paths.py)
and [BrotherSBE brother_paths.py](../../products/brothersbe/tools/brother_paths.py).
Its detection order is a valid `BROTHER_CLIENT` override, Codex turn markers
(`CODEX_SESSION_ID`, `CODEX_THREAD_ID`, `CODEX_SANDBOX`), Claude markers
(`CLAUDECODE`, `CLAUDE_CODE_ENTRYPOINT`), then an unambiguous package manifest.
Codex turn markers take precedence over an inherited Claude session marker.
`CODEX_HOME` alone does not identify a host; ambiguous or absent evidence
returns an empty string.

The helper resolves a plugin root from `BROTHER_PLUGIN_ROOT`, then
`CLAUDE_PLUGIN_ROOT`, then `PLUGIN_ROOT`, then its own package location.
The source explicitly treats the Codex `PLUGIN_ROOT` compatibility evidence
as a binary inspection, not proof from a live hook. It does not use an
invented `CODEX_PLUGIN_ROOT` variable. Configuration resolves from
`BROTHER_CONFIG_DIR`, then `CLAUDE_CONFIG_DIR`, then `CODEX_HOME` only for a
Codex or unknown host, then the per-host default. Claude and unknown hosts
default to `~/.claude`; Codex defaults to `~/.codex`.

For Claude Code, `hooks_wiring_check()` compares the shipped product file
with `SBE_HOOKS_JSON` if set, or the same-version BrotherSBE installation
recorded in `~/.claude/plugins/installed_plugins.json`.
`SBE_INSTALLED_PLUGINS_JSON` overrides that registry path. It checks event,
matcher and command wiring plus referenced script existence. No discoverable
installed copy yields `NO-DATA` when the shipped definition has no problems.

For Codex, the function takes a separate path before consulting that Claude
registry. `_codex_wiring_verdict()` looks for
`brother_paths.config_path("hooks.json")`. No resolvable or existing file
yields `NO-DATA` with `CODEX_NO_DATA_DETAIL`. An unreadable file or a missing
shipped event or command yields `FAIL`. Presence of every shipped event,
matcher and command signature yields `PASS`; extra hooks are allowed.
Signatures compare from `tools/` onward so expanded absolute roots do not
cause a false mismatch. This is a check of the manually wired user file,
not a comparison against an installed Codex plugin hook file.

That doctor's `PASS` checks file contents. The installer's `--check` checks
exact document equality, and `--trust` checks host-reported trust. None of
these alone proves an actual hook fired and denied a forbidden action.
Live enforcement remains `NO-DATA` without evidence from the running host.

The existing local checks are `python3 scripts/test_codex_hooks_install.py`
and `BROTHER_CLIENT=claude python3 products/brothersbe/tools/test_sbe_doctor_wiring.py`.
The latter pins the default host for its Claude fixtures; its Codex cases
set their own `BROTHER_CLIENT` and isolated configuration directory.
Package layout is documented in [PACKAGE-SHAPE.md](PACKAGE-SHAPE.md).

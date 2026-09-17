# Hooks under Claude Code and Cursor

Brother's product hooks are delivered by the Claude Code plugin loader.
Cursor does not read that same `hooks/hooks.json` shape or those event
names; it needs its own wiring, described below. See
[../codex/HOOKS-MAPPING.md](../codex/HOOKS-MAPPING.md) for the Codex twin
and for the shared Claude Code product definitions
([BrotherMode](../../products/brothermode/hooks/hooks.json) and
[BrotherSBE](../../products/brothersbe/hooks/hooks.json)) this page does not
repeat.

## Two Cursor routes, different wiring shape

This repository ships two ways to reach Cursor, covered in more detail in
[PACKAGE-SHAPE.md](PACKAGE-SHAPE.md):

1. **Bundle local plugin**
   ([scripts/cursor_plugin_install.py](../../scripts/cursor_plugin_install.py)).
   The manifest at `bundle/.cursor-plugin/plugin.json` declares
   `"hooks": "./cursor-hooks/hooks.json"`, so Cursor's own plugin loader
   wires [bundle/cursor-hooks/hooks.json](../../bundle/cursor-hooks/hooks.json)
   automatically once the bundle is copied into
   `~/.cursor/plugins/local/brother`. That file's commands run through
   `${PLUGIN_ROOT}/runtime/hooks/brothermode/tools/bm_cursor_hook.py`,
   naming the real product script with a `--run` argument, for events
   including `sessionStart`, `sessionEnd`, `preToolUse` and others the
   generated file lists.
2. **Standalone product install**
   ([products/brothermode/tools/bm_cursor.py](../../products/brothermode/tools/bm_cursor.py)
   `emit-hooks`). Writes a Cursor `hooks.json` for a checkout running
   BrotherMode independently of the bundle, from the template at
   [products/brothermode/hooks/cursor.hooks.json](../../products/brothermode/hooks/cursor.hooks.json)
   with `${BROTHERMODE_ROOT}` substituted for the real checkout path.
   Default target: `~/.cursor/hooks.json`. The template's own `_comment`
   field warns against copying it into place with the placeholder still
   literal in it.

Cursor's event names differ from both Claude Code's and Codex's:
`preToolUse`, `postToolUse`, `beforeShellExecution`, `afterShellExecution`,
`sessionStart`, `sessionEnd`, matcher `Write|Shell|Delete|Edit` on the
standalone template's `preToolUse` entry. Declared wiring alone does not
establish coverage of Cursor's actual tool-call surface, the same limit
[../codex/HOOKS-MAPPING.md](../codex/HOOKS-MAPPING.md) states for Codex.

## The doctor's verdict, and what it does and does not smoke

`python3 tools/bm_cursor.py doctor` (or `bm-cursor doctor`) runs two checks,
neither of which is a Claude-registry comparison the way BrotherSBE's
Claude-host `hooks_wiring_check()` is:

- It locates the checkout's `tools/bm_cursor_hook.py` adapter and invokes it
  directly with a real, Read-shaped `preToolUse` payload
  (`hook_event_name`, `tool_name: "Read"`, a harmless `tool_input`). A
  nonzero exit, unparseable stdout, or a `permission` other than `allow`/
  absent is reported `FAIL`. This is a genuine, live invocation of the
  adapter, not a static file comparison, but it only proves the adapter
  does not wrongly deny one benign probe; it is not evidence that the
  adapter denies a forbidden action.
- It reads the installed `hooks.json` (`--hooks`, default
  `~/.cursor/hooks.json`) and requires a `hooks` object containing at
  least a `preToolUse` or `beforeShellExecution` entry. A missing file is
  `WARN`, not `FAIL`, because project-level hooks may still apply.

There is no `CODEX_NO_DATA_DETAIL`-shaped constant for Cursor and no
documented comparison against an installed plugin's own shipped hooks the
way `sbe_hooks_wiring.py` runs for Claude Code. Live enforcement, meaning
that a real Cursor session actually ran a hook and denied a forbidden
action, remains unmeasured by either the bundle route's manifest
declaration or the standalone doctor's adapter smoke.

## Host detection

Host detection for Cursor is not implemented the same way as the Claude
and Codex detection `sbe_hooks_wiring.running_client()` and
`brother_paths.client()` perform (documented in
[../codex/HOOKS-MAPPING.md](../codex/HOOKS-MAPPING.md)): those functions'
detection order names Claude and Codex markers only. `bm_cursor.py`'s own
`find_checkout()` locates a BrotherMode checkout by `BROTHERMODE_ROOT` or a
flat/umbrella layout probe, which is a different question (where the
product lives) from which host is running it.

## Local checks

```sh
python3 scripts/test_cursor_plugin.py -v
python3 tools/bm_cursor.py doctor
```

The first checks manifest and installer shape; the second is the live
adapter smoke described above, run against whatever `hooks.json` and
checkout it finds or is told about. Neither proves a live host session
denied a forbidden action end to end.

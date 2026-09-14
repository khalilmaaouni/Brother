# Install Brother on Cursor

Use this guide for the Cursor plugin. The compatibility installer is
described separately below.

## Prerequisites

Cursor with Agent enabled; `git` and `python3`; a Brother checkout for
the installer. Headless work also needs `cursor-agent`. Enterprise teams may need "Allow Local Plugin Imports"
turned on before `~/.cursor/plugins/local` is read.

## Install

From a Brother checkout:

```bash
python3 scripts/cursor_plugin_install.py
```

That copies `bundle/` to `~/.cursor/plugins/local/brother`. Reload Cursor
(Developer: Reload Window). Then invoke `/brother` or say Brother in
plain language.

The bundle declares three native agents: `brother-planner`,
`brother-executor`, and `brother-reviewer`. Their scope restrictions are
prompt instructions, not frontmatter-enforced permissions. Read
`bundle/skills/using-brother/references/cursor-native.md` for their roles.
The dispatch and execute aliases now carry the real mailbox instructions.
Checkout discovery recognizes the local umbrella install and resolves to
its nested `runtime/hooks/brothermode` root.

For the model worker, `BROTHER_MODEL_CLIENT=cursor` selects the adapter in
`scripts/model_worker.py`. Its default uses `--mode ask` (read-only);
workers that must edit need an appropriate `MODEL_WORKER_CMD` override.

Optional MCP ships as `bundle/mcp.json` plus
`bundle/runtime/hooks/brothermode/mcp/bm_mcp_server.py`. The configuration
runs the server from the nested checkout. Both may be absent; validation
refuses a configuration/server mismatch. If sibling tools cannot load, the
server returns an explicit tool error rather than crashing.

These are WBS-70 U1 through U7 source changes. U7 also reserves additional
agent hook events without wiring them or adding gates. U8 is the current
documentation pass; U9, final regeneration, has not started.

The older BrotherMode compatibility installer
(`products/brothermode/scripts/install_cursor.py`, default
`~/.cursor/brothermode`) stays supported. This page is the marketplace-
shaped plugin.

## Verify

```bash
python3 scripts/cursor_plugin_install.py validate
python3 scripts/test_cursor_plugin.py
```

Confirm `/brother` is reachable, unfinished-work discovery makes sense,
and a small run can produce a receipt.

## Honest limit

The founder confirmed the Cursor install/adapter works on his real machine
on 2026-09-13: "Cursor is fine mark is as tested", recorded in the
[decision record](../decisions/cursor-live-canary-2026-09-13.json).
This replaces the blanket ADVISORY status with that bounded confirmation.
It does not establish an automated signed-in smoke PASS or a measured
`permission: deny`. The signed-in smoke test has not been run this session;
measured denial remains NO-DATA on this evidence. Prefer worktrees for
isolation.

## Uninstall

```bash
python3 scripts/cursor_plugin_install.py uninstall
```

## Proving it

The default smoke is `python3 scripts/cursor_smoke.py`; it needs no
login and proves isolation and the auth boundary. After `cursor-agent
login`, the real one is `python3 scripts/cursor_smoke.py --signed-in`.
See the [smoke runbook](../cursor/SMOKE-RUNBOOK.md) for the separate
hook, edit, and receipt verdicts. That smoke does not measure a denial.

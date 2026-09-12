# Install Brother on Cursor

Use this guide for Cursor. Claude Code keeps its own marketplace add.
Codex keeps docs/how-to/install-codex.md.

## Prerequisites

Cursor with Agent enabled; `git` and `python3`; a Brother checkout for
the installer. Enterprise teams may need "Allow Local Plugin Imports"
turned on before `~/.cursor/plugins/local` is read.

## Install

From a Brother checkout:

```bash
python3 scripts/cursor_plugin_install.py
```

That copies `bundle/` to `~/.cursor/plugins/local/brother`. Reload Cursor
(Developer: Reload Window). Then invoke `/brother` or say Brother in
plain language.

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

Fence hooks under Cursor are ADVISORY until a live Cursor Agent canary
proves they fire and honour `permission: deny`. Prefer worktrees for
isolation. As of 1.0.14 no signed-in Cursor Agent turn has been observed
reaching `/brother`, and hook output that Claude Code shows at session
start is not delivered to a Cursor agent.

## Uninstall

```bash
python3 scripts/cursor_plugin_install.py uninstall
```

## Proving it

The default smoke is `python3 scripts/cursor_smoke.py`; it needs no
login and proves isolation and the auth boundary. After `cursor-agent
login`, the real one is `python3 scripts/cursor_smoke.py --signed-in`.
Fence enforcement stays ADVISORY until a deny is measured live.

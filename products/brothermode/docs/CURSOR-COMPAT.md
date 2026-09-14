# BrotherMode Cursor compatibility mode

Status: CURRENT, updated 2026-09-13. Cursor install/adapter tested on the
founder's real machine, confirmed in the
[decision record](../../../docs/decisions/cursor-live-canary-2026-09-13.json):
"Cursor is fine mark is as tested". This is verbal real-machine confirmation,
not an automated smoke result or a measured hook denial.

## What you get

1. **Independent run in Cursor.** BrotherMode's store, project CLI, rules,
   and native hook adapter install under `~/.cursor/brothermode`.
2. **Install / manage / uninstall.** `scripts/install_cursor.py`,
   `tools/bm_cursor.py status|doctor|emit-rules|emit-hooks`, and
   `scripts/uninstall_cursor.py` (paths relative to the BrotherMode product).
3. **Local mailbox harness.** A planner dispatches and adopts work packets
   under `.brothermode/cursor-mailbox/`; Cursor claims and executes them.
4. **Umbrella plugin.** See the [install guide](../../../docs/how-to/install-cursor.md)
   for the bundle install, native personas, and optional MCP server.

WBS-70 U1 through U7 have landed in the source tree:

| Units | Landed behavior and source (repository-relative paths) |
|---|---|
| U1/U2, `babde7a93` | `scripts/model_worker.py` selects `cursor-agent` with `BROTHER_MODEL_CLIENT=cursor`, parses its JSON result and camelCase usage fields. Default argv is `cursor-agent -p --output-format json --trust --mode ask`, a read-only mode; write workers need the `MODEL_WORKER_CMD` override. |
| U3, `50d0bbf72` | `products/brothermode/tools/bm_cursor.py` discovers the umbrella layout, including `~/.cursor/plugins/local/brother`, and returns its nested `runtime/hooks/brothermode` checkout root. Callers append `tools` themselves. Flat compatibility checkouts still work. |
| U4, `ea43e1b69` | `scripts/codex_surface.py` preserves the real mailbox instructions for the dispatch and execute skill aliases, instead of replacing them with generic engine stubs. |
| U5, `543589507` | `bundle/.cursor-plugin/plugin.json` declares agents. `bundle/agents/brother-executor.md`, `bundle/agents/brother-planner.md`, and `bundle/agents/brother-reviewer.md` carry execution, planning, and review instructions. Their limits are prompt-level instructions, not agent permissions enforced by frontmatter. See `bundle/skills/using-brother/references/cursor-native.md`. |
| U6, `b3b257b05` | Optional `bundle/mcp.json` launches the mirrored `bundle/runtime/hooks/brothermode/mcp/bm_mcp_server.py` from the nested checkout. Validation accepts both absent, refuses a half-shipped pair. Missing or unparseable sibling tools produce an explicit tool error without crashing the server. |
| U7, `fe2b16343` / `35c9288f3` | `products/brothermode/tools/bm_cursor_hook.py` recognizes ten additional reserved agent events. They remain non-gating and unwired in the bundle hook configuration. |

This lists source changes, not a final regeneration result. U8 is the current
documentation pass; U9, the final regeneration pass, has not started.

## Install

From `products/brothermode` in a Brother checkout:

```
python3 scripts/install_cursor.py
python3 ~/.cursor/brothermode/tools/bm_cursor.py doctor
```

Optional, for one project (rules + project hooks that Cloud Agents can see):

```
python3 scripts/install_cursor.py --project /path/to/project --upgrade
```

Consent (once per machine, before any telemetry write):

```
python3 ~/.cursor/brothermode/scripts/setup.py
```

Uninstall:

```
python3 scripts/uninstall_cursor.py
# or also delete the checkout:
python3 scripts/uninstall_cursor.py --remove-files
```

## Manage

```
python3 <checkout>/tools/bm_cursor.py status
python3 <checkout>/tools/bm_cursor.py doctor
python3 <checkout>/tools/bm_cursor.py emit-rules --force
python3 <checkout>/tools/bm_cursor.py emit-hooks --write ~/.cursor/hooks.json --force
```

Packaged console script name: `bm-cursor` (see `pyproject.toml`).

## Harness: planner dispatches, Cursor executes

Use `skills/cursor-dispatch/SKILL.md` for the planner and
`skills/cursor-execute/SKILL.md` for the executor (paths relative to the
BrotherMode product). The bundle aliases preserve those instructions at
`bundle/skills/brothermode-cursor-dispatch/SKILL.md` and
`bundle/skills/brothermode-cursor-execute/SKILL.md` (repository-relative).

The sequence in `tools/bm_cursor.py` is `dispatch`, `claim-next`,
`record-result`, then `adopt`. Dispatch declares read/write scope and a
`--done-check`; adoption re-runs that check. Use `--with-worktree` for
isolation and pin `--project` to the absolute project root when working in
a worktree: discovery walks for a `.git` directory, while a worktree has a
`.git` file. The shipped skills include the headless `cursor-agent` route.

Controller seam: `tools/bm_cursor.py:CursorMailboxWorker` implements the
same `run(brief) -> pending` shape as `RecordIntentWorker` in
`tools/bm_controller.py`, so a Full-Auto run can dispatch Cursor packets
without a second control plane.

## Hooks

Cursor events wired (native `hooks.json` version 1):

| Cursor event | Adapter action |
|---|---|
| `preToolUse` (Write\|Shell\|Delete\|Edit) | Translate to the shared PreToolUse contract; run fence; bash-audit pre on Shell |
| `beforeShellExecution` | Treat as Bash PreToolUse (apply_patch path + audit pre) |
| `postToolUse` / `afterShellExecution` | Bash-audit post |
| `afterFileEdit` | Observe only (too late to refuse) |
| `sessionStart` / `preCompact` / `stop` | Reserved; quiet by default |

Adapter: `tools/bm_cursor_hook.py`. Template: `hooks/cursor.hooks.json`.

The umbrella plugin uses `bundle/cursor-hooks/hooks.json` (repository-relative).
Its lifecycle entries wrap real scripts through the adapter's `--run` mode;
the quiet compatibility-template lifecycle rows above do not describe those
bundle entries.

The ten newly reserved events are `postToolUseFailure`, `subagentStart`,
`subagentStop`, `beforeMCPExecution`, `afterMCPExecution`, `beforeReadFile`,
`beforeSubmitPrompt`, `afterAgentResponse`, `afterAgentThought`, and
`workspaceOpen`. Recognition alone adds no hook behavior. Only `preToolUse`
and `beforeShellExecution` are gate events. Tab events remain unrecognized.

### Honest limit on enforcement

The [2026-09-13 decision record](../../../docs/decisions/cursor-live-canary-2026-09-13.json)
satisfies the previous condition to record a real-machine canary: the founder
confirmed the install/adapter works. It does not contain hook-denial output
or the signed-in smoke's individual verdicts. The signed-in smoke test has
not been run this session. Measured `permission: deny` enforcement remains
NO-DATA on this evidence; use worktrees for isolation.

Cloud Agent scope, as documented in the shipped execution skill: project `.cursor/hooks.json` runs in cloud
agents; user `~/.cursor/hooks.json` does not. Prefer `--project` when the
executor is a Cloud Agent.

## Layout

| Path | Role |
|---|---|
| `~/.cursor/brothermode/` | Default Cursor checkout |
| `~/.cursor/hooks.json` | User-scope Cursor hooks |
| `~/.cursor/brothermode-install.json` | Install record |
| `<project>/.cursor/rules/brothermode.mdc` | Always-on rules (frontmatter included) |
| `<project>/.cursor/hooks.json` | Project hooks (Cloud Agent visible) |
| `<project>/.brothermode/cursor-mailbox/` | Harness packets |

## Related pages

- `docs/RUNTIMES.md` (generated runtime registry; Cursor row)
- `docs/HOOKS.md` (shared hook contract the adapter targets)
- `docs/proposals/2026-08-02-full-auto-and-codex-execution-modes.md` (packet shape ancestor)
- `docs/FULL-AUTO.md` (controller harness)

# BrotherMode Cursor compatibility mode

Status: CURRENT. Built 2026-08-10. Independent Cursor install, manage,
uninstall, plus a local Fable-to-Cursor harness. Fence enforcement under
Cursor is ADVISORY until a live canary is recorded.

## What you get

1. **Independent run in Cursor.** BrotherMode's store, project CLI, rules,
   and a Cursor-native hook adapter install under `~/.cursor/brothermode`
   without Claude Code being present.
2. **Install / manage / uninstall.** `scripts/install_cursor.py`,
   `tools/bm_cursor.py status|doctor|emit-rules|emit-hooks`, and
   `scripts/uninstall_cursor.py`.
3. **Remote control from Claude Code (Fable or Opus).** A local mailbox
   under `.brothermode/cursor-mailbox/` carries work packets. Planner
   dispatches and adopts; Cursor claims and executes. No network.

## Install

From a BrotherMode checkout:

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

## Harness: Fable plans, Cursor executes

Planner (Claude Code, Fable or Opus), from the project:

```
python3 <checkout>/tools/bm_cursor.py dispatch \
  --objective "Add the healthz route and a failing-then-passing test" \
  --read-scope app/ \
  --write-scope app/routes.py \
  --write-scope tests/test_healthz.py \
  --done-check 'python -m pytest tests/test_healthz.py' \
  --with-worktree
```

Executor (Cursor Agent), from the same project:

```
python3 <checkout>/tools/bm_cursor.py claim-next
# ... do the work inside write_scope / worktree ...
python3 <checkout>/tools/bm_cursor.py record-result \
  --packet-id <id> \
  --worker-claim "added healthz and test" \
  --artifact app/routes.py \
  --done-output /tmp/done.txt
```

Planner adopts (re-runs done_check itself):

```
python3 <checkout>/tools/bm_cursor.py adopt --packet-id <id>
```

Skills:

- Claude Code planner: `/brothermode:cursor-dispatch` (`skills/cursor-dispatch`)
- Cursor executor: `skills/cursor-execute` (also summarized in the rules file)

Controller seam: `tools/bm_cursor.py:CursorMailboxWorker` implements the
same `run(brief) -> pending` shape as `RecordIntentWorker` in
`tools/bm_controller.py`, so a Full-Auto run can dispatch Cursor packets
without a second control plane.

## Hooks

Cursor events wired (native `hooks.json` version 1):

| Cursor event | Adapter action |
|---|---|
| `preToolUse` (Write\|Shell\|Delete\|Edit) | Translate to Claude PreToolUse; run fence; bash-audit pre on Shell |
| `beforeShellExecution` | Treat as Bash PreToolUse (apply_patch path + audit pre) |
| `postToolUse` / `afterShellExecution` | Bash-audit post |
| `afterFileEdit` | Observe only (too late to refuse) |
| `sessionStart` / `preCompact` / `stop` | Reserved; quiet by default |

Adapter: `tools/bm_cursor_hook.py`. Template: `hooks/cursor.hooks.json`.

Cursor can also load Claude Code hooks from `~/.claude/settings.json` when
third-party skills are enabled (vendor doc:
https://cursor.com/docs/reference/third-party-hooks). The native install
path above does not depend on that setting.

### Honest limit on enforcement

Payload shapes were read from Cursor's Hooks documentation on 2026-08-10.
A LIVE canary that proves Cursor Agent or Cloud Agent actually executes
these hooks and honours `permission: deny` has NOT been measured in this
tree. Codex taught the same lesson (docs/mistakes/M19): a fence that looks
installed and does not fire is worse than no fence. Until a dated canary
lands, treat Cursor fences as ADVISORY and use worktrees for isolation.

Cloud Agent note (vendor doc): project `.cursor/hooks.json` runs in cloud
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
- `docs/HOOKS.md` (Claude Code hook contract the adapter targets)
- `docs/proposals/2026-08-02-full-auto-and-codex-execution-modes.md` (packet shape ancestor)
- `docs/FULL-AUTO.md` (controller harness)

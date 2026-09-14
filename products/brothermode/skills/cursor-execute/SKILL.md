---
name: cursor-execute
description: Inside Cursor, claim and execute the next BrotherMode harness packet, then return results
---

Outcome to produce: the claimed packet's write_scope changed exactly as specified, the done_check run after the last edit, and `bm-cursor record-result` written so Fable can adopt.

You are the executor. You do not plan the project. You do not merge. You do not push. The planner that wrote this packet may have been Claude Code, Codex, or Cursor's own Plan mode; this skill covers the executor side regardless of which one it was.

## Resolve `<checkout>`

`bm_cursor.py find_checkout()` tries, in order: `BROTHERMODE_ROOT` if set, then the plugin root (`BROTHER_PLUGIN_ROOT`, `CLAUDE_PLUGIN_ROOT`, or the unprefixed `PLUGIN_ROOT` a Cursor or Codex host process exports), then the compat install target `~/.cursor/brothermode`, then your client's own config directory under `skills/brothermode`, then the local plugin Cursor itself loads (`~/.cursor/plugins/local/brother/runtime/hooks/brothermode`), then this repository checkout. Confirm what it actually found:

```
python3 <checkout>/tools/bm_cursor.py status
```

Use the printed checkout for every command below.

## Pin the mailbox project

`project_root()` walks up for a `.git` DIRECTORY. A git worktree's `.git` is a FILE, so once you are working inside a packet's own worktree, resolving the project from the current directory can pick the wrong mailbox. Pass an absolute `--project <path>` on every mailbox command below rather than relying on cwd; `<path>` is the project root you were dispatched against, not the worktree.

## First commands

```
python3 <checkout>/tools/bm_cursor.py claim-next --project <absolute project path>
```

If the inbox is empty, stop and say so. Do not invent work.

Read the packet. Quote the freshness assertion, then run:

```
git status
git rev-parse HEAD
```

If the tree contradicts the packet (missing write_scope path, done_check already green when the packet says red), HALT and record:

```
python3 <checkout>/tools/bm_cursor.py record-result \
  --packet-id <id> \
  --worker-claim "halt: <reason>" \
  --status halted \
  --project <absolute project path>
```

## Do the work

- If the packet names a worktree, `cd` there and stay there.
- Touch only write_scope paths.
- After the last edit, run the packet's done_check. Capture its output to a file.

```
python3 <checkout>/tools/bm_cursor.py record-result \
  --packet-id <id> \
  --worker-claim "<one paragraph of what changed>" \
  --artifact <path> \
  --done-output /tmp/bm-cursor-done.txt \
  --status returned \
  --project <absolute project path>
```

## Headless execute (`cursor-agent` CLI)

When a script, not an interactive Cursor session, is doing the claiming and
running, drive `cursor-agent` directly:

```
cursor-agent -p --trust "<the packet objective, verbatim>" --worktree <path>
```

`--worktree` is optional: only pass it when the packet actually attached one. Never add `--plugin-dir` to a nested `cursor-agent` invocation unless the packet explicitly asks for it; a nested worker that loads its own plugin directory can pick up a second, unrelated plugin set and lose this harness's hooks.

## Cloud Agent note

A Cursor Cloud Agent has no access to this project's local `.brothermode/cursor-mailbox/`. It only sees what the repository ships: the project's `.cursor/` directory, meaning `rules/brothermode.mdc` (from `emit-rules`) and `hooks.json` (from `emit-hooks`). Do not route a harness packet to a Cloud Agent expecting it to run `bm_cursor.py` mailbox commands; a Cloud Agent follows the rules and hooks only.

## Hard stops

- Never merge.
- Never push.
- Never widen write_scope.
- Never call adopt. That is the planner's job.

---
name: brothermode-cursor-dispatch
description: From Claude Code (Fable or Opus), dispatch a work packet for Cursor to execute under the BrotherMode harness
---

Outcome to produce: one harness packet sitting in the project's Cursor mailbox, ready for a Cursor session to claim, with a done-check you will re-run yourself on adopt.

This skill is the planner side of Cursor compatibility mode. You stay in Claude Code. Cursor executes. You verify.

## When to use this

Use when mechanical implementation should run inside Cursor (Agent or Cloud Agent) while judgment, planning, and acceptance stay here. The planner running this skill may itself be Claude Code, Codex, or Cursor's own Plan mode; whichever one it is, it stays the planner and never becomes the executor.

## Resolve `<checkout>`

`bm_cursor.py find_checkout()` tries, in order: `BROTHERMODE_ROOT` if set, then the plugin root (`BROTHER_PLUGIN_ROOT`, `CLAUDE_PLUGIN_ROOT`, or the unprefixed `PLUGIN_ROOT` a Cursor or Codex host process exports), then the compat install target `~/.cursor/brothermode`, then your client's own config directory under `skills/brothermode`, then the local plugin Cursor itself loads (`~/.cursor/plugins/local/brother/runtime/hooks/brothermode`), then this repository checkout. Confirm what it actually found:

```
python3 <checkout>/tools/bm_cursor.py status
```

## Mechanical commands

Pin an absolute `--project <path>` on every mailbox command: `project_root()` only recognises a `.git` DIRECTORY, and a git worktree's `.git` is a FILE, so cwd resolution can silently pick the wrong mailbox once execution has moved into a worktree. From the user's project:

```
python3 <checkout>/tools/bm_cursor.py dispatch \
  --objective "..." \
  --read-scope <path> \
  --write-scope <path> \
  --done-check '<shell command that exits 0 only when done>' \
  --actor fable \
  --with-worktree \
  --project <absolute project path>
```

Hand the printed `packet_id` to the Cursor side (ask the user to open Cursor on the project, or continue if a Cursor agent is already watching the mailbox). The `cursor-execute` skill covers the claim, and it is Cursor's job to run, not yours.

When Cursor returns:

```
python3 <checkout>/tools/bm_cursor.py poll --packet-id <id> --project <absolute project path>
python3 <checkout>/tools/bm_cursor.py adopt --packet-id <id> --project <absolute project path>
```

Adopt re-runs the done-check itself. A pasted green line from Cursor is a claim; the re-run is the evidence. On reject, escalate: do not loop the same packet a third time after two failures. Rewrite the packet or do the work here.

## Cloud Agent note

A Cursor Cloud Agent cannot reach this project's local `.brothermode/cursor-mailbox/`, so dispatch here never targets one. A Cloud Agent only sees what the repository ships: the project's `.cursor/` directory, meaning `rules/brothermode.mdc` (from `emit-rules`) and `hooks.json` (from `emit-hooks`). Route mailbox packets to a local Cursor Agent session only.

## Law you must keep

1. Executor never merges or pushes, and never calls `adopt` itself.
2. Prefer `--with-worktree` so Cursor edits land in an isolated tree.
3. Fence under Cursor is ADVISORY until a live canary exists. Isolation is the real fence.
4. Halt-and-report is already in the packet. Do not strip it.
5. Never tell Cursor to skip adopt.

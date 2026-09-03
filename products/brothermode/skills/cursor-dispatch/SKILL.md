---
name: cursor-dispatch
description: From Claude Code (Fable or Opus), dispatch a work packet for Cursor to execute under the BrotherMode harness
---

Outcome to produce: one harness packet sitting in the project's Cursor mailbox, ready for a Cursor session to claim, with a done-check you will re-run yourself on adopt.

This skill is the planner side of Cursor compatibility mode. You stay in Claude Code. Cursor executes. You verify.

## When to use this

Use when mechanical implementation should run inside Cursor (Agent or Cloud Agent) while judgment, planning, and acceptance stay here. Prefer Opus or Fable for this planner role.

## Mechanical commands

Resolve the BrotherMode checkout first (`BROTHERMODE_ROOT`, or `~/.cursor/brothermode`, or `~/.claude/skills/brothermode`, or this repository). Then, from the user's project:

```
python3 <checkout>/tools/bm_cursor.py dispatch \
  --objective "..." \
  --read-scope <path> \
  --write-scope <path> \
  --done-check '<shell command that exits 0 only when done>' \
  --actor fable \
  --with-worktree
```

Hand the printed `packet_id` to the Cursor side (ask the user to open Cursor on the project, or continue if a Cursor agent is already watching the mailbox):

```
python3 <checkout>/tools/bm_cursor.py claim-next
```

When Cursor returns:

```
python3 <checkout>/tools/bm_cursor.py poll --packet-id <id>
python3 <checkout>/tools/bm_cursor.py adopt --packet-id <id>
```

Adopt re-runs the done-check itself. A pasted green line from Cursor is a claim; the re-run is the evidence. On reject, escalate: do not loop the same packet a third time after two failures. Rewrite the packet or do the work here.

## Law you must keep

1. Executor never merges or pushes.
2. Prefer `--with-worktree` so Cursor edits land in an isolated tree.
3. Fence under Cursor is ADVISORY until a live canary exists. Isolation is the real fence.
4. Halt-and-report is already in the packet. Do not strip it.
5. Never tell Cursor to skip adopt.

Docs: `docs/CURSOR-COMPAT.md`.

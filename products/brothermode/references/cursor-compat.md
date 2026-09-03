LOAD WHEN: the founder asks to run BrotherMode inside Cursor, to install or uninstall the Cursor compatibility mode, or to have Fable or Opus dispatch execution into Cursor through the harness.

# Cursor compatibility mode

BrotherMode can run independently in Cursor, and Claude Code (Fable or Opus)
can drive Cursor execution through a local mailbox. There is no network
control plane.

## Install and manage

```
python3 scripts/install_cursor.py
python3 ~/.cursor/brothermode/tools/bm_cursor.py status
python3 ~/.cursor/brothermode/tools/bm_cursor.py doctor
python3 scripts/uninstall_cursor.py
```

Project rules (alwaysApply frontmatter included):

```
python3 ~/.cursor/brothermode/tools/bm_cursor.py emit-rules --force
```

## Harness

Planner (Claude Code):

```
python3 <checkout>/tools/bm_cursor.py dispatch \
  --objective "..." --write-scope <path> --done-check '<cmd>' --with-worktree
```

Executor (Cursor):

```
python3 <checkout>/tools/bm_cursor.py claim-next
python3 <checkout>/tools/bm_cursor.py record-result --packet-id <id> \
  --worker-claim "..." --artifact <path> --done-output <file>
```

Planner adopts (re-runs done_check):

```
python3 <checkout>/tools/bm_cursor.py adopt --packet-id <id>
```

## Honest limit

Fence hooks under Cursor are ADVISORY until a live canary proves they fire
and honour deny. Prefer worktrees for isolation. Full page: docs/CURSOR-COMPAT.md.

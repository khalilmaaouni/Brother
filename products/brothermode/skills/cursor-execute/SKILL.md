---
name: cursor-execute
description: Inside Cursor, claim and execute the next BrotherMode harness packet, then return results
---

Outcome to produce: the claimed packet's write_scope changed exactly as specified, the done_check run after the last edit, and `bm-cursor record-result` written so Fable can adopt.

You are the executor. You do not plan the project. You do not merge. You do not push.

## First commands

```
python3 <checkout>/tools/bm_cursor.py claim-next
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
  --status halted
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
  --status returned
```

## Hard stops

- Never merge.
- Never push.
- Never widen write_scope.
- Never call adopt. That is the planner's job.

`<checkout>` is usually `~/.cursor/brothermode`. Confirm with `python3 <checkout>/tools/bm_cursor.py status`.

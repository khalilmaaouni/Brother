# The worker-brief preamble, mandatory sections

Copy this into every dispatch brief and fill it in. It exists because on
2026-08-10 two workers died holding good work and both deaths traced to the
BRIEF, not the worker: one had no plan file because its worktree sat on a
stale base, and one was handed a done-check its author had never run. Every
section below is a mistake class that actually happened, with the rule that
closes it. The 2026-08-11 self-learning sweep collapsed ten mistakes and
forty vault failure classes into these sections; the full ledger is
LEARNED.md.

## 1. Step Zero, before any work (stale-base class)

```
Print your branch. Assert `git merge-base --is-ancestor <base> HEAD`.
Then `git merge --ff-only <target>`. If the ancestor check fails, STOP and
report; never rebase, never force. Compare your fence files' mtimes against
this brief's dispatch timestamp and abort on any foreign write.
```

## 2. Identity and tier, declared (never inherited by omission)

```
lane: <main | FCC-free>, model <tier>, because <reason>.
session: <orchestrator session id>. fence: <the STATE.md fence line, verbatim>.
```

## 3. Exact paths (one-writer class)

```
You may write ONLY: <explicit list>.
You must NOT touch: <explicit list, name production paths by name>.
If completing the task seems to need a path outside your list, STOP and
report the boundary problem instead of working around it. Workers who report
boundary gaps have caught orchestrator defects twice; that report is worth
more than the task output.
```

## 4. Done-checks the author personally executed (untested-check class)

```
<command> ran by the brief's author on <date> and printed: <last lines>.
```
A done-check the author never ran is worse than none: the 2026-08-10 dash
scan matched ordinary hyphens and could catch no real violation. A PLAN is
code for this purpose; verify every flag and path in it exists before
dispatch (`--help`, `ls`, `grep`), because a worker executes a plan
literally. The 2026-08-11 baseline died five times on one flag the CLI had
grown a requirement for.

## 5. Verification rules the worker inherits

- A test that cannot fail proves nothing: force the condition you claim to
  test, and calibrate by re-injecting the defect and watching it go red.
- Verify a control with the control: if a hook decides something, ask the
  hook; never reimplement its parser and trust your copy.
- Counts and comparisons in Python, not shell arithmetic: `grep -c || echo 0`
  yields a two-line value, and command substitution clobbers exit codes.
- Never send a monitoring command's stderr to /dev/null.
- Secret patterns anchored with realistic length (`\bsk-[A-Za-z0-9]{16}`),
  tested against a known-clean input before use.
- Compression is for reading, never for proving: quote the raw line for any
  count, error string, or figure a claim rests on.

## 6. After committing (history-is-evidence class)

```
Check `git show --stat` against what your message claims; amend on any
disagreement. Never `git add -A` in a shared tree; stage by explicit path.
```

## 7. Return format

```
Capped at <N> tokens. Lead with the outcome. Every claim of green names the
command run AFTER your last edit and quotes its last lines. State Remaining
and Unverified explicitly; an empty Remaining section is a claim too.
```

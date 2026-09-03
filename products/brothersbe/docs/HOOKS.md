# The fence hook

`tools/sbe_fence_hook.py` is a PreToolUse hook that refuses a file write outside
an active claim. It is the point where L13, "one writer per file", stops being a
line in a markdown registry and becomes an enforcement boundary.

It **fails open and says why**, so a refusal always means a real ownership
conflict rather than a broken hook. It does **not** gate `Bash`.

Everything else in this document is a consequence of those two sentences.

---

## Why it exists

`references/laws-parallel-writers.md` (L13) says a fence line is written BEFORE
the writer starts, names the exact files that writer may touch, and is closed
only by appending its evidence. Two tools already read those lines:

- `tools/sbe_score.py` scores fence HYGIENE. Is a live fence tier-tagged, is the
  registry it lives in stale.
- `tools/sbe_telemetry.py fence-lint` prints live fences as a DISPATCH AID before
  a writer launches.

Both run beside the work. Neither can refuse anything, because neither sits in
front of a write. L13 says so about itself:

> The rest of the fence discipline is human review, because nothing here computes
> it ... queueing rather than running in parallel when two writers overlap in file
> scope (no check compares scopes)

This hook compares scopes, at the only moment where comparing them can still stop
a collision.

---

## What it enforces, exactly

The fence shape is BrotherSBE's own, read from `STATE.template.md`:

```
- agent: <id> (sole writer, session <id>) | tier T1 | TTL <date> |
  objective: <one line> |
  files: <the exact files it may write> |
  output: <what done looks like> |
  boundaries: ... | termination: ... | check: ... |
```

A fence is **live** while it carries no `LANDED` and no `ADOPTED` marker. That
rule is not re-typed here: the hook imports `sbe_score._is_live_fence` and applies
it unchanged, so the fence the hook refuses over and the fence the scorer measures
can never drift into two different rules.

Registries are discovered exactly as `fence-lint` discovers them: the project's
own `STATE.md`, plus every colon-separated glob pattern in
`BROTHERSBE_REGISTRIES`. The hook adds one path fence-lint does not need, the
PROJECT ROOT's `STATE.md` when it differs from cwd, because fence-lint is run by a
human standing in the project root while this hook fires on whatever cwd the
session holds. Without it, an edit issued from a subdirectory would find no
registry and sail past a fence one level up.

A write is refused when its target falls inside the `files:` scope of a live fence
whose declared session is not this session. Scope covers three ordinary shapes: an
exact path, a directory prefix (`docs/` covers `docs/SETUP.md`), and a glob
(`docs/*.md` covers `docs/SETUP.md`, and deliberately does NOT cover
`docs/guides/01-quickstart.md`, which its author never named).

Every target path is realpath'd and expressed root-relative before comparison.
Comparing unresolved strings is bypassed by `..`, by a symlink, by a relative path
typed from a subdirectory, or by case on a case-insensitive filesystem.

---

## The wire contract

Claude Code fires a PreToolUse hook with the tool call as JSON on stdin. The
fields this hook reads:

| field | used for |
|---|---|
| `tool_name` | is this a write tool at all |
| `tool_input` | the target path or paths |
| `session_id` | this session's identity, supplied by the harness |
| `cwd` | registry discovery and relative-path resolution |
| `project_dir` | the fence root; falls back to `cwd` when absent |

A refusal is this object on stdout, at exit code 0:

```json
{"hookSpecificOutput": {"hookEventName": "PreToolUse",
                        "permissionDecision": "deny",
                        "permissionDecisionReason": "..."}}
```

An allow writes **nothing** to stdout. Exit 2 would also block, feeding stderr
back as the reason, but it is the wrong instrument here: exit 2 means "the hook
itself failed", and every failure this hook has is a fail-open. It exits 0 on
every path.

Stdout is the decision channel and carries nothing else. Every diagnostic goes to
stderr. That is why the file has exactly two output funnels and no bare `print`.

If that contract moves, this document is the one to update, and the hook's
docstring points here.

---

## Install

Add a `PreToolUse` block beside the hooks in [SETUP.md](SETUP.md):

```json
{
  "hooks": {
    "PreToolUse": [
      {"matcher": "Edit|Write|MultiEdit|NotebookEdit|CreateDirectory|Delete|apply_patch",
       "hooks": [{"type": "command",
         "command": "python3 ~/.claude/skills/brothersbe/tools/sbe_fence_hook.py"}]}
    ]
  }
}
```

Check what it can see before you trust it. The `fences` subcommand prints the
live fences it would enforce, and names anything it could not read:

```
python3 tools/sbe_fence_hook.py fences .
```

Two environment variables change its answer:

- `BROTHERSBE_FENCE_HOOK_OFF=1` turns enforcement off for a session. The hook then
  says so on stderr on every write, so the bypass is never silent.
- `BROTHERSBE_FENCE_SESSION` overrides the identity compared against a fence line,
  for a manual run or a test. It never invents one: an invented id would own
  nothing and would deny the operator out of their own work.

---

## It fails open

This hook sits in front of every edit the operator makes. A hook that failed
closed on its own bug would stop the operator's own work, which is strictly worse
than having no hook. So **every** failure path allows the write and prints the
reason to stderr:

| condition | behavior |
|---|---|
| no registry found at all | allow, name the cwd and `BROTHERSBE_REGISTRIES` |
| registry file absent | allow, say nothing was opened |
| registry undecodable or carrying no readable fence | allow, say so |
| registry exists and cannot be read | allow, name the file and the error |
| a registry directory cannot be entered | allow, name the directory |
| a live fence declares no readable `files:` scope | allow, quote the line |
| `sbe_checks.py` or `sbe_score.py` unimportable | allow, name the import error |
| payload not an object, or `tool_input` not an object | allow, say so |
| no target path in the payload | allow, list the keys that were there |
| no `session_id` and `BROTHERSBE_FENCE_SESSION` unset | allow, say so |
| any unexpected exception | allow, print the type and message |

Every one of those lines says `FAILING OPEN` and `the fence was NOT checked`. A
silent fail-open would be only half a fail-open: the operator has to be able to
tell "no fence owns this" apart from "the fence machinery is broken".

**This is a deliberate divergence from the scorer.**
`sbe_score.check_fence_hygiene` FAILs over an unreadable registry, on the rule
that a broken record is not an absent one. That is right for a scorer, whose
output is a verdict a human reads at their leisure. It is wrong for a gate in
front of the keyboard, whose output is a refusal that stops work now. Same
evidence, opposite safe direction, both stated on purpose.

---

## It says why, and the escape works

A refusal names the file, the registry the fence lives in, the agent that opened
it, the session that owns it, the fence line verbatim, and three escapes:

1. **Report it to the owner.** L13 says overlapping writers queue, they do not run
   in parallel.
2. **Close the fence**, in the registry where it lives, by appending its evidence
   block to that line: the marker `LANDED`, the exact command run, and its last
   lines. This is `STATE.template.md`'s own closing rule, and the hook stops
   refusing the moment the line reads `LANDED`.
3. **Take it over deliberately**: append `ADOPTED` to the line and write a new
   fence naming your session as sole writer, before editing anything.

The project's standing rule is that a named escape must be one that actually
works. So escape 2 is not merely asserted to appear in the string.
`tools/test_sbe_fence_hook.py::test_the_named_escape_actually_releases_the_fence`
takes the refusal, performs the close exactly as the refusal describes it, replays
the identical payload, and asserts it is now allowed. A refusal naming a door that
does not open is worse than one naming none, because the reader spends their time
on it.

---

## Identity

A BrotherSBE fence names its writer in plain text: `(sole writer, session <id>)`.
That is not a weakness and no secret token file is needed, because the id the hook
compares against is the one the **harness** puts in the payload, not one the model
types. A model cannot write its own `session_id` field into a PreToolUse payload,
so reading a declared id out of `STATE.md` and claiming to be it buys nothing.

Matching is a prefix in both directions, with a four character floor. A registry
is hand-written, and an operator abbreviates a UUID to its first eight characters
as often as they paste the whole thing. Matching generously is the safe direction
here: a false match allows the write, which is this hook's bias anyway, while a
false miss would refuse the rightful owner out of their own fence.

A live fence that declares no session at all is treated as owned by somebody else,
and refuses. L13's rule is one writer per file, and a fence whose writer is
anonymous is still a fence somebody opened.

---

## Known gaps, declared rather than papered over

**It does not gate `Bash`.** A shell command can write any file, and no reliable
parse of arbitrary shell exists, so gating it would be a guarantee this file
cannot keep. `Bash` is absent from `WRITE_TOOLS` deliberately, and a test asserts
it stays absent so the gap cannot quietly become an undeclared one. A writer
determined to cross a fence can do it with `sh`. This hook stops the ordinary
accident, not a determined bypass.

Since BR-1014 that gap is covered by two OTHER hooks rather than by widening this
one, and the split is the point:

- `tools/sbe_bash_write_guard.py`, a `PreToolUse` hook matching `Bash`, refuses a
  positively identified protected write (`printf > CLAUDE.md`, `python3 -c
  open(...)`, `sed -i hooks/hooks.json`, `rm -f .sbe/tasks.json`). It reads
  command TEXT, so it is an early warning and an immediate blocker, never a
  proof. It fails open on anything it cannot classify, says so on stderr, and
  never claims an allowed command was read only.
- `tools/sbe_session_reconcile.py`, a `Stop` hook, is the AUTHORITATIVE control.
  It reads the repository changes that actually survived against the session
  baseline `tools/sbe_session_baseline.py` wrote at SessionStart, so a generated
  script, a rename, a deletion or a symlink reaches it the same way an edit does.
  It fails CLOSED, which is the opposite direction from this file and for the
  opposite reason: it runs at the end, where failing open means the session
  finishes with an undeclared protected change and nothing said.

The same reconciliation runs in CI as `sbe scope verify --base <ref> --head HEAD
--strict`, which reads no baseline and does not depend on Claude Code having run
locally.

**A human who edits the registry can hand themselves any fence.** The registry is
a markdown file the operator owns, and an operator may rewrite what they own. That
is the design. The hook makes crossing a fence a deliberate, logged edit instead of
an invisible accident; it is not an access control system.

**It reads whole bullets where the other checks read single lines.** A fence in
`STATE.template.md` continues onto indented lines: `files:` is on the third line
and `LANDED` on the last. `sbe_score` and `sbe_telemetry` apply their liveness rule
to one stripped line at a time, which is fine for what they measure, because the
tier tag sits on the first line. It cannot work here: a line-wise reader would find
no file scope on any fence written the way the template writes them, and would read
a closed fence as still open. So the hook applies the project's own liveness rule,
unchanged, to the whole bullet. The visible consequence: a fence closed with
`LANDED` on a continuation line is CLOSED to this hook while `sbe_score` still
reads it as live. That is a hygiene false alarm in the scorer, never an unenforced
fence here.

**The two liveness parses in this project disagree, and the hook uses the broader
one.** `sbe_score._is_live_fence` accepts both markdown bullets (`- ` and `* `);
the two copies inside `sbe_telemetry.py` accept only `- `. The hook imports the
scorer's, because the narrow parse misses a real fence written with an asterisk
bullet, and a missed fence is an unprotected file.

**It fences a project, not the filesystem.** A write above the project root is
outside every fence and is allowed.

---

## Tests

```
python3 tools/test_sbe_fence_hook.py
python3 tools/test_sbe_bash_guard.py
python3 tools/test_sbe_session_reconcile.py
```

`TestFailOpen` is the fail-open property as a class, not as a list of instances:
it includes a sweep asserting that no malformed payload it can construct produces
a deny. `TestGenuineConflictIsRefused` covers the refusal and the working escape.
`TestToolSurface` asserts the declared tool surface, including that `Bash` stays
outside it. `TestWireProtocol` runs the hook as a subprocess with JSON on stdin,
the way Claude Code actually invokes it.

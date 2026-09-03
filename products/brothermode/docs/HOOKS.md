# Hooks: turning the fence from a ledger into a boundary

BrotherMode's headline promise is one writer per file. Until `tools/bm_fence_hook.py`
existed, that promise was a **coordination ledger**: `bm_store.py` recorded who claimed
what and refused an overlapping claim, but nothing stood between an agent and an actual
file write. The installed hooks were SessionStart, SessionEnd, Stop and PreCompact, and
none of them can refuse anything, because all of them run after or beside the write and
never in front of it.

So an agent could edit before claiming, claim an incomplete set of paths and edit the
rest, edit a path another record owned, or skip BrotherMode entirely. That is audit
finding 8, and it was confirmed by execution, not theory.

`tools/bm_fence_hook.py` is the missing gate. It is a **PreToolUse** hook: Claude Code
hands it the tool name and the tool's input before the tool runs, and it can deny the
call.

## The contract this hook implements

Read from <https://code.claude.com/docs/en/hooks> on **2026-07-27**. If that page moves,
this section is the thing to re-check first.

**Input**, delivered as one JSON object on stdin:

```json
{
  "session_id": "abc123",
  "prompt_id": "550e8400-e29b-41d4-a716-446655440000",
  "transcript_path": "/home/user/.claude/projects/.../transcript.jsonl",
  "cwd": "/home/user/my-project",
  "permission_mode": "default",
  "hook_event_name": "PreToolUse",
  "tool_name": "Bash",
  "tool_input": { "command": "npm test" },
  "tool_use_id": "toolu_01ABC123..."
}
```

The four fields this hook reads are `session_id`, `cwd`, `tool_name` and `tool_input`.

**Output**, to deny a call: exit code 0, with exactly this object on stdout.

```json
{
  "hookSpecificOutput": {
    "hookEventName": "PreToolUse",
    "permissionDecision": "deny",
    "permissionDecisionReason": "Database writes are not allowed"
  }
}
```

`permissionDecision` accepts `"allow"`, `"deny"`, `"ask"` and `"defer"`. This hook only
ever emits `"deny"`, and only ever on a real ownership conflict.

**Exit codes.** 0 means success, and stdout is parsed as JSON; no JSON on stdout means no
decision, and the normal permission flow applies. 2 is a blocking error: stdout is
ignored and stderr becomes the reason shown to the model. Any other code is a
non-blocking error, execution continues, and the first stderr line is surfaced as a hook
error notice.

This hook **always exits 0**. An allow is exit 0 with empty stdout. A deny is exit 0 with
the JSON object above. It never uses exit 2, because exit 2 means "the hook itself
failed", and a failure in this hook must never block a write (see Fail open, below).

## Installation

The supported way is `python3 scripts/install.py`, which writes this entry along with
the other four hooks and does not report success until the fence has executed once. See
docs/QUICKSTART.md step 3.

However you install it, check it with:

```
python3 scripts/doctor.py            # add --settings PATH for a non-default file
```

Since 2026-08-01 (Loop 3) doctor runs eleven checks, not one: the fence simulation
below, version identity, python3 and git availability, setup consent, vault
writability, duplicate-install detection, project store health, hook wiring
against the installation mode, the release checksum self-check, and settings
validity. The table lives in docs/SETUP.md. The fence simulation stays the
deepest of the ten and is described here in full.

Doctor's fence check answers a narrower question than the installer's smoke test,
and the narrow question is the one that matters. The smoke test runs the hook from an empty directory,
where it takes its fail-open path, so it proves the hook executes. Doctor runs a
**blocked-write simulation**: a throwaway project under a temporary directory, its own
store, one file claimed under one session's label, then a write to that file requested
by a different session, judged by the exact command string in your settings file. A
healthy fence denies that, and then allows the same write when the owner asks, because a
hook that denies everything passes half the check and is a brick rather than a fence.
Each of `Edit`, `Write`, `MultiEdit` and `NotebookEdit` is simulated in its own real
input shape (the path key differs per tool), so a fence that gates one of them and not
the other three cannot report itself healthy. The owner half checks the hook's EXIT CODE
as well as its output, because a `PreToolUse` hook blocks a call by exiting non-zero,
not only by printing a deny. The temporary directory is deleted at the end; your
project, your store and your STATE.md are never touched.

Exit 1 names the specific defect: no `PreToolUse` entry naming `bm_fence_hook.py`, an
entry whose command points at a file that is not there, a matcher that leaves some write
tools ungated or is not a valid regular expression, a hook that refuses nothing, or a
hook that refuses everything (by deny JSON or by exit code). The matcher check treats
the matcher as a REGEX tested against each tool name, the way Claude Code does, and not
as a substring search: `Edit` is a substring of both `MultiEdit` and `NotebookEdit`, so
a substring test silently passed a matcher that left `Edit` ungated. Doctor still
cannot tell you whether Claude Code has LOADED that settings file: hooks are read at
session start, so a file corrected mid-session is live at the next one, not this one.

### By hand

Add this to `.claude/settings.json` (project scope) or `~/.claude/settings.json` (user
scope). Use the absolute path to your checkout.

```json
{
  "hooks": {
    "PreToolUse": [
      {
        "matcher": "Edit|Write|MultiEdit|NotebookEdit|Bash",
        "hooks": [
          {
            "type": "command",
            "command": "python3 /absolute/path/to/BrotherModeUp/tools/bm_fence_hook.py",
            "timeout": 10
          }
        ]
      }
    ]
  }
}
```

The `matcher` is a belt-and-braces duplicate of the check the hook does itself: the hook
re-reads `tool_name` and passes through anything that is not a file-writing tool, so a
looser matcher is safe, just slightly more wasteful.

Verify the installation without touching your real project:

```
cd /absolute/path/to/BrotherModeUp
python3 tools/bm_fence_hook.py whoami --session-id test-session
python3 tools/test_bm_fence_hook.py
```

## How a session proves who it is

This is audit finding **8B**, and it is the part where honesty matters more than
cleverness.

The defect: a record's `session_id` was a caller-supplied string, **and** it is printed in
plaintext into `STATE.md`, which every session reads. So the ownership guard compared a
public value against itself, and anyone could pass anyone else's session id.

The fix raises the bar without pretending to solve it:

1. On first use, a session gets a **secret token**: 32 random bytes, hex encoded, written
   to `.brothermode/fence/<slot>.token` with mode `0600` inside a `0700` directory. The
   slot filename is `sha256("bm-fence-slot-v1|" + session_id)`, so the file is found from
   the harness-supplied session id and a session id containing a path separator cannot
   escape the directory. The file is created with `O_CREAT|O_EXCL`, so two processes
   racing on the same session id cannot both write it.
2. The **public label** is a one-way hash of that token:
   `"bm1-" + sha256("bm-fence-label-v1|" + token)[:24]`.
3. `STATE.md` keeps showing the label, and only the label. The token is printed by no
   command in this file, including `whoami`.
4. Ownership is `record.session_id == label_derived_from_the_token_this_process_opened`.

The consequence that matters: **reading a label out of `STATE.md` and presenting it as
your own session buys nothing.** The hook never takes your word for which label is yours,
it computes it from a file you had to be able to open. Writing a stolen label into the
store as the owner of your own claim buys nothing either, for the same reason.

Claim under your own label like this:

```
python3 tools/bm_store.py claim my-work --lifetime ephemeral \
  --objective "..." --files src/thing.py \
  --session "$(python3 tools/bm_fence_hook.py session-label --session-id "$CLAUDE_SESSION_ID")"
```

`session-label` takes the session id from `--session-id`, then `BM_FENCE_SESSION_ID`, then
a hook payload on stdin. It refuses rather than inventing one, because a made up session
id would mint a token nobody could reproduce and quietly lock the session out of its own
records.

### The residual limitation, stated plainly

**Any process running as this user can read the token files.** They are `0600`, not
sealed. A hostile local process can list `.brothermode/fence/`, read any token, derive
that session's label, and impersonate it completely.

So what this actually defends against is:

- accidental collision (two sessions picking the same human-readable name),
- casual forging (an agent copying the owner label it read in `STATE.md` or a thread file
  and passing it as its own),
- a confused agent that believes it holds a fence it never claimed.

What it does **not** defend against is a determined local attacker, or any code running
under this user account that decides to read the token directory. Perfect unforgeability
is not achievable here: same machine, same user, no network, standard library only. There
is no secret this process can hold that another process under the same user cannot read.
Anything stronger needs an OS keychain, a separate uid, or a broker process, none of which
this project has.

Two smaller notes in the same spirit:

- The `0600` and `0700` modes are a real guarantee on POSIX. On Windows they are a
  courtesy: `os.chmod` there can only toggle the read-only bit, and the actual protection
  is whatever ACL the directory inherits. This mirrors `bm_store._chmod_best_effort`,
  which documents the same limit for the store file.
- The redactor that scrubs generated views does **not** recognize a 64 character hex
  string as a secret. That is precisely why the token is never printed anywhere rather
  than being printed and relied upon to be redacted.

## What the hook checks, exactly

1. Not a file-writing tool? Pass through, silently.
2. Pull every path out of `tool_input`, including nested per-edit paths, so a fenced file
   hidden in `edits[1]` is not missed. Bounded in depth and width, because a hostile
   payload must not hang the gate in front of every edit.
3. Resolve the project root with `bm_store.resolve_root`, imported, not reimplemented, so
   the hook and the store can never disagree about where the project is.
4. Canonicalize every target path: `realpath` (symlinks resolved, `..` collapsed,
   relative paths resolved against the payload `cwd`), then express it root-relative.
   Comparing unresolved strings is trivially bypassed by `..`, by a symlink, or by
   claiming from a different directory.
5. Compare with `bm_store.paths_overlap`, the same function the store uses to admit a
   claim, so a directory claim covers its children and a glob claim covers the files it
   spans. On macOS and Windows that comparison folds CASE and UNICODE NORMALIZATION,
   because those filesystems do: `src/café.py` written NFC and the same name written NFD
   are one inode and two different strings, and before 2026-07-29 a claim in one spelling
   did not cover a write in the other, so a foreign session went straight through the
   default (non-strict) path. Linux is normalization sensitive, where the two really are
   different files, so nothing is folded there.
6. Covered by an active record owned by someone else: **deny**, naming the record, its
   lifecycle uuid, the owning label, and the exact takeover command.
7. Covered by an active record this session owns: allow.
8. Covered by nothing: allow, unless strict mode is on.

Between steps 3 and the claims comparison sits one independent check, the battery
fence, added after the 2026-08-17 incident in which one session edited a tracked
file nineteen minutes into another session's `python3 tools/test_all.py` baseline
run, invalidating it; the second session only discovered the run through pgrep.
The gate lock `test_all.py` has always taken (crash-safe `O_CREAT|O_EXCL` in the
system temp directory, staleness decided by the holder pid's liveness first, age
only where a pid cannot be probed) now doubles as the announcement: its token
records pid, start time, HEAD sha and the starting session (`BM_FENCE_SESSION_ID`,
then `CLAUDE_SESSION_ID`, else `unrecorded`), and the hook asks `test_all.py`'s
own `lock_path` and `lock_state` rather than parsing the file itself, so the
format keeps exactly one owner. While the lock is LIVE, a write to a git-TRACKED
target is denied for **every** session, including the one that started the gate,
because a same-session edit invalidates the baseline identically (the recorded
failure class of measuring a tree you are still editing). Untracked scratch paths
stay writable. A stale lock (holder provably gone) is reported and ignored; a
lock or a trackedness question the hook cannot read is reported loudly and, in
advisory mode, never blocks; enforced mode refuses it, C-01's direction, through
the `battery-unreadable` reason. `BM_FENCE_BATTERY=off` disables the check and is
announced on stderr on every write while set. It runs before the claims load on
purpose: a store with zero claims fails open below, and a running gate deserves
its refusal even in a project with no fences declared. The battery classes in
`tools/test_bm_fence_hook.py` hold all of this in place.

### Taking a fence over

The deny message names the command. It is `bm_store.py`'s own documented door for a record
that is active under a different live session:

```
python3 tools/bm_store.py adopt <lifecycle_uuid> --version <N> \
  --session <your-label> --adopt-from-live-session
```

Then re-claim the paths you need. This is deliberately a two-step, explicit act. Taking a
fence is allowed; taking it silently is not.

### Strict mode (opt in)

`BM_FENCE_STRICT=1` additionally denies a write to any path inside the project that **no**
active record covers, turning "do not cross a fence" into "claim before you edit". Off by
default because it changes the working rhythm for everyone in the repo. It can only ever
tighten: it cannot override the fail-open rule below.

One consequence of that last sentence is worth stating outright, because a reader can
easily assume the opposite (C-01, 2026-08-03). Strict mode is read AFTER every fail-open
path, so on a store with **no active claims at all** it does nothing whatsoever: the
"no active claims" fail-open fires first and the write is allowed. That is the state a
fresh project sits in, which is exactly where "claim before you edit" would matter most.
Strict tightens which PATHS are covered while the fence is working; it has never had
anything to say about what happens when the fence CANNOT work.

### Enforced mode (opt in, and the one that fails closed)

`BM_FENCE_MODE=enforced` is the answer to that gap. It changes one thing: every condition
that would otherwise allow a write **without having checked ownership** now refuses it
instead. An adversarial probe drove nine of them, and before this every single one allowed
a write straight through another session's active fence with exit 0: store missing, store
corrupt, store zero bytes, store present with zero active claims, five shapes of malformed
payload, an unrecognized path key, an internal exception mid-decision, `bm_store.py`
unimportable, and an underivable session identity.

Enforced mode does not touch the happy path: the session holding a claim still writes
freely, and a genuine cross-fence write is refused with the same message as before. It is
opt-in and stays that way, because a hook that starts refusing edits on a machine whose
owner never asked for it is a worse failure than an unenforced fence.

What it still cannot do is gate Bash. A shell command that writes across a fence is
detected afterwards by the `Bash` audit pair, never refused, in either mode.

## Fail open, loudly

This hook sits in front of every edit the founder makes. A hook that failed closed on its
own bug would brick editing entirely. So a refusal must always come from a real ownership
conflict, and never from the hook being broken.

Every one of these allows the write and prints the reason to stderr:

- no BrotherMode project root found,
- no store file,
- store unreadable, busy, zero bytes, or corrupt,
- the store holds **no active claims at all**,
- the payload is missing, not JSON, or missing `session_id` or `tool_input`,
- a write tool arrived with no recognizable path,
- `bm_store.py` could not be imported,
- any unexpected exception at all (there is a blanket catch, and it is deliberate).

The stderr line always begins `bm_fence_hook: FAILING OPEN`, so a fence that has quietly
stopped enforcing is visible rather than silent.

## Known gaps

These are real, and listing them is better than implying coverage that does not exist.

- **Bash is still not gated. Since 2026-08-01 (Loop 6, D-1) it is DETECTED.**
  `rm`, `sed -i`, `>`, `tee`, `git checkout` and a hundred other shell forms write
  files, and no reliable parse of arbitrary shell exists, so `tools/bm_bash_audit.py`
  cannot and does not block any of them. Since L06 (2026-08-06) the fence matcher is
  `Edit|Write|MultiEdit|NotebookEdit|Bash`, and the one Bash shape that is a
  structured write in a shell costume, an apply_patch envelope, is parsed and
  refused before it runs; every other shell form still cannot be refused up
  front, only detected after the fact. What changed on 2026-08-05 is that a
  fenced file changed BY a Bash call FROM a session that does not own that fence is no
  longer invisible: see "The Bash-write detection hook" below for what it actually
  proves and what it still cannot see. The policy for shell writes in general is in
  the next section, unchanged.
- **Unclaimed paths are allowed by default.** The hook enforces "not across someone
  else's fence", not "everything must be claimed", unless strict mode is on.
- **The hook cannot verify the store side.** An agent can still write any label into the
  store via `bm_store.py claim --session X`. It gains nothing at write time (see above),
  but it can occupy a name or a path set under a label that is not its own.
- **A filename containing a literal `*`, `?` or `[`** is read as a glob by
  `paths_overlap`, so its fence coverage is widened. It errs toward refusing rather than
  allowing, and such filenames are illegal on Windows anyway, but it is a real edge.
- **Per-process cost is roughly 45 ms**, almost entirely Python interpreter startup plus
  importing `bm_store.py`. The decision itself is about 0.5 ms. If that ever matters, the
  fix is a resident helper, not a thinner check.

## The Bash boundary, and the policy that replaces a guarantee

Three strategies were on the table for shell writes. Only one of them is a mechanism,
and none of them is a guarantee, so all three are stated rather than the strongest one
being implied.

**1. Use Edit or Write for ordinary file changes.** These pass through the hook above.
This is a rule in the constitution (SKILL.md), not a mechanism: nothing stops a session
from reaching for Bash instead. It is first because it is the only one of the three that
makes the fence cover the common case.

**2. Use `scripts/bm_shell.py` for an unavoidable generated write.**

```
python3 scripts/bm_shell.py --record my-work --path src/generated.py \
  --session-id "$CLAUDE_SESSION_ID" -- 'python3 codegen.py > src/generated.py'
```

The wrapper makes the caller name the paths before the command runs, and hands each one
to `bm_fence_hook.decide()`, the imported function, so the wrapper and the hook cannot
drift. It refuses when a declared path is inside another session's fence, and, because a
deliberate shell write is a stronger act than an ordinary edit, it also refuses when the
path is inside nobody's fence (it checks in strict mode) and when the fence FAILS OPEN
at all. That last one is the deliberate inversion: the hook fails open because failing
closed would brick editing, and the wrapper fails closed because "could not be checked"
and "was approved" must not produce the same outcome for a command you invoked on
purpose.

A declared path that resolves OUTSIDE the project root is a third answer, not a pass.
The fence declines to judge anything outside the root on purpose (BrotherMode fences a
project, not the machine), so `decide()` returns nothing for it, which is the same value
as approval. The wrapper therefore refuses such a path by default and names it. Add
`--allow-outside-root` when the write really does belong outside the project: the command
runs and the summary reports those paths as NOT CHECKED rather than as claimed. Until
2026-07-29 the wrapper printed "N declared path(s) are inside this session's own claim"
for a path that was in no claim and not even in the project.

`--declare-none` runs a command that writes nothing. It is checked against a short,
explicit list of obvious write forms (`>`, `>>`, file-descriptor redirection such as `1>`
and `2>>`, `tee`, `sed -i`, `rm`, `mv`, `cp`, `patch`, a rewriting `git` subcommand, an
inline interpreter script, and a few more), and refuses if one matches. That list is not
a shell parser and the refusal says so. Passing it is not evidence that a command writes
nothing.

What the wrapper does NOT do: confine the command. It is a declaration channel, not a
sandbox. A command that writes a path the caller did not declare writes it, and nothing
here stops that.

**3. Everything else is outside mechanical protection, and the next best evidence is the
ledger.** A bare `Bash` write, a write from a subprocess the wrapper launched, a write by
any other program on the machine: none of these reach a PreToolUse hook, because Claude
Code only offers the hook the tool call it is about to make, and `Bash` carries a command
string rather than a path set. What remains in that case is not nothing: the store still
records who claimed what, `bm_store.py verify` still reports the state, and git still
shows the diff. It is a ledger and an audit trail, not a refusal. That is the exact
limit of the one-writer promise, and it is stated here, in SKILL.md, and in the doctor's
own output rather than left for someone to discover.

Closing that last gap would need a capability this project does not have: a PreToolUse
payload for `Bash` that names the files a command will touch (the harness does not
provide one), or an OS-level write mediator such as a sandbox profile or a FUSE layer,
both of which are far outside "Python 3.9, standard library only".

## The Bash-write detection hook (D-1, Loop 6, added 2026-08-01)

`tools/bm_bash_audit.py` does not close the gap above. It answers a narrower, honest
question instead: **after** a `Bash` call finishes, did it change a file another
session's fence covers? It is a PAIR of hooks, both wired to the `Bash` matcher, both
pointed at the same file:

- **PreToolUse** (`bm_bash_audit.py pre`): before the shell command runs, read every
  ACTIVE record's claims through `tools/bm_fence_hook.py`'s own `active_claims`
  (the same function the fence hook itself calls, so "what is fenced right now" can
  never disagree between this hook and the fence hook), and for every claimed path
  that is a real, existing file, record its size, mtime and sha256 into a snapshot
  file under `.brothermode/bash-audit/`.
- **PostToolUse** (`bm_bash_audit.py post`): after the shell command finishes, re-read
  the same snapshot and re-hash every path in it. For any path whose sha256 changed (or
  that was deleted) and whose recorded owner is not this session's own fence label
  (derived through `bm_fence_hook.session_label`, the identical identity system
  docs/HOOKS.md describes above), raise ONE alert row through the store's service layer
  (`severity: high`, `category: fence-breach`, `requires_human: true`, the changed
  path named in the message) and print one plain sentence to stderr. The alert's
  message is run through the store's own export-policy masking functions
  (`redact_text`, then `mask_absolute_paths`) before it is stored, so a founder-typed
  record name cannot smuggle a secret or an absolute path into the row unmasked.

**Which project an alert is filed under.** An alert's attribution row has to name a
project, and a fence is not project-scoped: records and claims carry no `project_id` at
all, so there is no project a fence-breach alert intrinsically belongs to. If the store
holds exactly one project, that one is used, because one project per folder is the model
`tools/bm_project.py` documents and that is where the founder's own pages already look.
Otherwise, with no project or with several, the hook registers its own bookkeeping row
(`brothermode-bash-audit`) and files the alert there. That row is not the founder's work,
so `bm_store.py`'s `SYSTEM_PROJECT_IDS` names it and `Store.list_projects()` hides it by
default: without that, a breach on a fresh install would make the bookkeeping row the
only project in the store, and `bm_project start` would refuse the founder's own first
project as a second one, while the status line and the progress page named the
bookkeeping row as their work. `list_projects(include_system=True)` still returns every
row, and `dump()` and `verify()` read the table directly, so nothing is hidden from an
audit or an export.

This hook is **detection, not prevention, for every ordinary write** (D-1's own words):
by the time the alert exists, the write already happened. It has a decision to make in
exactly one situation, added by C-02 and described below (BM_FENCE_MODE=enforced, and
only inside a BrotherMode project), and in that one situation the `pre` phase writes a
deny object to stdout; otherwise it never writes to stdout at all, on either entrypoint,
and every diagnostic goes to stderr. Both entrypoints always exit 0, whatever went wrong.
It is consent-gated like `tools/bm_autosave.py`: pre-consent, nothing is read from disk
and nothing is written, not a snapshot file and not an alert row. The one thing that runs
ahead of that gate is the enforced-mode refusal check, which reads only the payload the
harness already handed the hook, resolves whether that payload's cwd is a BrotherMode
project before doing anything else, and writes nothing at all.

**What it can see:** a fenced file whose bytes are different, or gone, after a Bash
call from a session that is not that fence's owner.

**What it cannot see, stated rather than implied:**

- **A write that restores the original bytes before PostToolUse fires.** The
  comparison is END STATE against snapshot, not "did anything happen in between". A
  command that overwrites a fenced file and then overwrites it back to the original
  content, all inside one `Bash` call, leaves no trace: the hash matches, and nothing
  is reported.
- **A Bash call that also removes its own snapshot.** The snapshot file lives under
  `.brothermode/bash-audit/` inside the project root the command is already running
  in; a command sophisticated enough to target it (`rm -rf .brothermode`, for
  instance) removes the evidence along with covering its tracks, and PostToolUse then
  fails open with "no snapshot was recorded" rather than reporting a breach.
- **Timing races.** The snapshot is taken once, at PreToolUse, and compared once, at
  PostToolUse. A second, concurrent process (a background job, a second terminal, a
  parallel session) that changes the same fenced file in the gap between those two
  moments is indistinguishable from the Bash call itself having changed it: this hook
  attributes the whole delta to the Bash call it wrapped, because that is the only
  thing it observed.
- **A claim on a directory or a glob-shaped path.** The snapshot only covers a claim
  path that resolves to a real, existing FILE at PreToolUse time (a literal
  `os.path.isfile` check); it does not expand a directory or glob-shaped claim into
  the files it would cover, so a new file created inside a claimed directory during
  the Bash call is invisible to it.
- **A session claimed under a `--session` string bm_fence_hook.py never issued.** The
  ownership comparison is the identical one `bm_fence_hook.py` already makes
  (`records.session_id == the label this process derives from its own token`); a
  record claimed by hand with an arbitrary `--session` value that is not a real
  derived label will never match, on either hook.

### Refusing the obvious destructive forms (C-02, 2026-08-03, enforced mode, inside a
### BrotherMode project only)

The gap closure item C-02 measured was narrower and worse than "Bash is not gated". With
a proven DENY in place, `rm -f .brothermode/store.sqlite3` through Bash turned that DENY
into an ALLOW, and produced no alert row and no stderr line at all, because the store is
not itself a claimed path and nothing in this pair looked at it. Enforcement state that
the ungated channel can delete is not enforcement.

Two halves landed, and only one of them is a refusal.

**Always on, in both modes, in every BrotherMode project: the enforcement state is
watched.** The `pre` phase now records, alongside the fenced files, whether the store
exists, whether it is non-empty, whether it still begins with the SQLite file header, and
which session token files are present. The `post` phase reports every one of those that
was LOST during the call: one sentence on stderr and one high-severity
`fence-control-loss` alert row per finding, checked before ownership is even derived.
Growth and ordinary mutation are invisible on purpose, because a Bash call that runs
`bm_store.py` is normal work and a check that alarmed on it would be switched off within a
day. When the store itself is what went missing, the row cannot be written at all, and the
hook says exactly that.

**Opt in, enforced mode, AND ONLY INSIDE A BROTHERMODE PROJECT: the obvious forms are
refused before they run.** With `BM_FENCE_MODE=enforced`, the `pre` phase resolves the
Bash call's cwd against `bm_store.resolve_root` first. If that resolves to no BrotherMode
project, the refusal check does nothing at all and the call proceeds exactly as it would
under the default mode. If it resolves to a project, and the command TEXT matches a small
literal list of destructive shell forms while also containing the literal string
`.brothermode` or `store.sqlite3`, or matches one of exactly two whole-directory forms
that name nothing (`git clean` with `-x`, and `rm -r` aimed at `.` or `*`), the `pre`
phase writes a deny object to stdout and the command does not run. The deny reason comes
from a literal table and names no path and no part of the command, for the same reason
`bm_fence_hook.py`'s `_FAIL_REASONS` are literal.

**Why the project check exists at all, stated plainly.** This hook installs at
USER-GLOBAL scope (`~/.claude/settings.json`, per `scripts/install.py`), so it runs on
every Bash call in every Claude Code session on this machine, not only inside BrotherMode
projects. An earlier draft of this refusal ran before resolving a project root; under that
draft, setting `BM_FENCE_MODE=enforced` and then running `git clean -xfd` in any unrelated
git repository anywhere on the machine would have been refused with a BrotherMode-specific
deny reason, in a directory that has nothing to do with BrotherMode. The project check
closes that: outside a BrotherMode project this refusal is inert, the same way
`bm_fence_hook.py` itself already refuses to act outside a project it recognizes.

**THE DELIBERATE LIMIT THIS CREATES.** When `tools/bm_store.py` cannot be imported at
all, the project check itself cannot run, so nothing is refused, even under enforced
mode, anywhere. That is a fail-open path inside a fail-closed feature, and it is chosen on
purpose: the alternative is refusing every Bash command in every directory on the machine,
which is not shippable. Someone who can break that import can therefore disable the
refusal.

**This is not containment, and the difference is the whole point.** It is a substring
match plus a regex list, not a shell parser, and `refusal_for`'s own docstring lists what
it misses: a path assembled at runtime or held in a variable, a destructive command
inside a script file or a Makefile target, any form not on the list, and any program that
deletes the file without the name appearing in the command. It also over-refuses,
deliberately, inside a BrotherMode project: `ls .brothermode > /tmp/x` is refused, and so
is `git clean -xfd` anywhere in the tree. Real containment needs an operating-system write
mediator (a sandbox profile, a container, a FUSE layer), which is out of scope here and
recorded as such in docs/KNOWN-LIMITS.md.

What enforced mode adds beyond the refusal is the aftermath. If the store does go missing
by a route the refusal misses, `bm_fence_hook.py` in the same mode then DENIES instead of
allowing (C-01), so the one-command bypass the register recorded now needs both halves to
fail rather than one.

### Installing the Bash audit hook

Both install paths wire both entrypoints today. The Claude Code plugin manifest,
`hooks/hooks.json`, and `scripts/install.py`'s clone-install path (the one
QUICKSTART documents, and the one `tools/test_install.py` exercises end to end)
carry the same audit shape: `PreToolUse` holds two matcher groups (the fence hook
at `Edit|Write|MultiEdit|NotebookEdit|Bash`, which since L06 also reads
apply_patch envelopes out of Bash payloads, and this audit's `pre` phase at
`Bash`), and a `PostToolUse` key wires the `post` entrypoint. The clone path is
six wired events and seven hook commands, and `tools/test_install.py`
hard-asserts exactly that shape, so removing either entrypoint turns the suite
red. The plugin manifest carries one command more since 2026-08-09: the
machine-wide session cap (next section) rides beside the audit's `pre` phase,
making its count eight. That divergence is DELIBERATE AND TEMPORARY, recorded in
docs/KNOWN-LIMITS.md the day it was created: the clone installer and its suite
sit outside the session-cap change's fence, exactly the situation the Loop 6
history below describes, and the same closure applies, a later loop updates
`scripts/install.py` and `tools/test_install.py` together.

History, dated so the old claim cannot be read as current: when this hook first
landed, the clone installer deliberately did NOT wire it, because
`tools/test_install.py` then asserted a five-event shape and sat outside that
change's fence; the gap was recorded in docs/KNOWN-LIMITS.md. The Loop 6 refuter
pass (2026-08-01) closed it: the installer and its suite were updated together,
and a test now reads `hooks/hooks.json`, so deleting the wiring fails a test
instead of failing a user.

## The machine-wide session cap (added 2026-08-09, after the 8 August runaway)

`tools/bm_session_cap.py` answers a different question from every hook above.
The fence asks "may this session write this file"; the audit asks "did that
shell command cross a fence"; this one asks "should another SESSION exist at
all". On 2026-08-08 an unattended relay chain ran fifteen concurrent sessions
against caps that existed only as sentences, and spent millions of tokens with
nobody watching. This hook is the sentence turned into a control.

- **PreToolUse** (`Bash` matcher): when the command would start another Claude
  Code session (`claude -p` or `claude --print`, anywhere in the command), it
  counts session transcripts written to in the last ten minutes across every
  project on the machine, and DENIES at or over the cap (default 4, override
  per launch with `BROTHERMODE_SESSION_CAP=N`). Everything that is not a
  session spawn passes untouched at any count.
- It writes NOTHING: two output funnels only (the deny object on stdout, the
  fail-open reason on stderr), reviewed in `tools/write_sites.json`. It is
  consent-EXEMPT for the same reason the fence hook is, encoded with its
  reason in `tools/test_bm_consent.py`: its scope is the machine, not any one
  project, so a project's setup consent cannot be the thing that switches
  fleet protection on.
- It fails OPEN when it cannot count (unreadable directory, malformed
  envelope), loudly, on stderr. It never fails open on its own verdict.
- Known over-refusal, deliberate: at the cap, a command that merely CONTAINS
  spawn-shaped text (an echo quoting it, a grep for it) is refused too. The
  match is textual; the safe direction is refusal; the escape is the env
  override, typed per launch.

## What a follow-up change to bm_store.py would need to add

`tools/bm_store.py` was **not** modified by this work: it is owned by another record, and
a needed store change is reported, never edited across the fence. The hook therefore works
entirely within today's schema, by reusing the existing free-text `records.session_id`
column to hold the derived label. That works, but it leaves three things a store change
could improve:

1. **A first-class `owner_token_hash` column on `records`**, written at claim time and
   never rendered into any view. Today the label doubles as both the human-visible handle
   and the ownership proof; splitting them would let the label be a short friendly name
   ("api-work-3") while the proof stays a full hash, instead of the label having to be a
   hash for the proof to work.
2. **Refusing a claim whose `--session` is a well-formed label the caller cannot prove.**
   The store currently accepts any string. If it verified the token at claim time, the
   forger could not even record a stolen label, which is the one thing the hook cannot
   prevent from where it sits.
3. **A shared `owns(record, path)` primitive exported from the store**, so the hook's
   check and the store's `_find_overlap` are literally the same function rather than two
   callers of `paths_overlap` that could drift.

None of the three is required for the hook to enforce the fence today.

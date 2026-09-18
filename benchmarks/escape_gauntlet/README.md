# Escape gauntlet (DOM-40.08)

This directory holds the schema for the escape attempt corpus and nothing
else tonight. `scripts/escape_gauntlet.py` is the harness that reads it;
`scripts/test_escape_gauntlet.py` is its test.

## Why the corpus is empty

Same reason as its sibling row, DOM-30.02: the harness is dispatchable now,
but the row only means something once the attempts are real. Writing seven
plausible-looking JSON files here to make a count go green would be
fabrication, not evidence. Nobody has researched and written real hostile
attempts yet, so `corpus/` does not exist in this commit, and the harness
reports NO-DATA rather than a fabricated PASS.

## What the harness checks

Every attempt in the corpus is run against one of three real boundary
modules already in this tree: `scripts/filesystem_enforcement.py`,
`scripts/network_policy.py`, `scripts/process_containment.py`. Those
modules are what refuses; this harness only asks each of them the hostile
question a real attempt poses, and checks the answer was REFUSE. An
attempt the boundary allows is an escape: it is scored FAIL, not NO-DATA,
because an empty corpus and an escaped attempt are different kinds of bad
news and must never be reported the same way.

An escape that is ever caught is a permanent case: once a real technique
is found to defeat a boundary, its corpus entry is never deleted, even
after the boundary is fixed, so the fixed behaviour stays under test
forever. Record why in the entry's own `note` field.

## Layout, once real attempts exist

```
benchmarks/escape_gauntlet/
  SCHEMA.json          the manifest schema, read by scripts/escape_gauntlet.py
  README.md            this file
  corpus/
    <id>.json           one manifest per attempt, filename stem == id
```

## The three boundary kinds and their `call` shape

**filesystem** (`scripts/filesystem_enforcement.decide`): the harness
creates a fresh temporary sandbox directory for the whole run, and every
path in `call` is a path RELATIVE to that sandbox, never an absolute or
real path. `call.roots` is a mapping with the same four keys
`filesystem_enforcement.decide` accepts (`read`, `write`, `immutable`,
`temporary`), each a list of relative directory names; the harness creates
each declared directory under the sandbox before deciding, then resolves
it to an absolute path. `call.path` is the relative path the attempt tries
to reach, which may include `..` traversal, a path built from parts, or
anything else `filesystem_enforcement.py`'s own docstring names as an
escape technique; it is resolved against the sandbox the same way. Real
files are only ever written under this throwaway per-run sandbox, deleted
after the run: no real path is ever touched. `call.action` is `"read"` or
`"write"`, default `"write"`.

**network** (`scripts/network_policy.decide`): `call.host` and
`call.config` pass straight through to `decide()`; `call.high_autonomy` is
optional. `decide()` makes no real network call of any kind (its own
module docstring says so), so no sandboxing is needed here: nothing this
harness does ever reaches an actual network.

**process** (`scripts/process_containment.py`): `call.technique_id` must
name an entry in `escape_gauntlet.PROCESS_TECHNIQUES`, a small fixed
registry of already-written, already-sandboxed technique implementations
(for example, spawning a child that detaches a grandchild with
`os.setsid()` before the harness cancels the tree, mirroring the pattern
`scripts/test_process_containment.py` already proves against
`cancel_process_tree`). A corpus file never supplies a raw command; only a
name selecting a fixed implementation. Every technique spawns its own
processes inside its own temporary directory and cleans them up itself; it
never signals a process it did not spawn.

## Running the harness

```
python3 scripts/escape_gauntlet.py
```

Prints one line per attempt (refused or ESCAPED) and an overall verdict:
PASS when the corpus is non-empty and every attempt was refused, FAIL when
at least one attempt escaped, NO-DATA when the corpus is empty or missing.
NO-DATA is not a pass; the harness exits non-zero for both FAIL and
NO-DATA.

## Adding a real attempt

Add `corpus/<id>.json`, matching `id` to the filename stem, then re-run the
harness. Do not add an attempt whose technique is invented for the sake of
a passing count; an attempt that is not a real hostile technique is worse
than no entry, because it reads as tested when nothing real was tried.

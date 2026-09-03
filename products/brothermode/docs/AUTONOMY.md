# The autonomy contract (U1)

Status: CURRENT as of 2026-08-05.

This page explains `tools/bm_autonomy.py`, the command line for U1: the
layer that records what a founder authorised an autonomous session to do,
answers whether one specific action is inside that authorisation, tracks
spend against a budget, and gives the founder a kill switch. It is the
contract, the gate, the breaker, the switch and the record. It is written
for whoever operates the command line directly today, and for the
controller loop (U2, `tools/bm_controller.py`, described in
`docs/FULL-AUTO.md`), which is built and which calls this layer before
every unit of work it dispatches.

No em or en dashes anywhere in this page.

## The contract model, in plain language

Before an autonomous session does any real work on a project, a human
signs a contract for it: what outcome the work is for, how "done" is
defined, which folders and surfaces it may touch, which broad classes of
action it may take (editing a file, running a build, making a local
commit, and so on), and, optionally, a ceiling on tokens and minutes it
may spend.

A contract is never edited. Every change, including pausing it, resuming
it, stopping it or revoking it, appends a brand new revision that carries
the FULL authorisation forward. Asking "what was I allowed to do" is
always answered by reading one row, the highest revision on file for that
project, never by replaying a history. If that row's state is not `live`,
nothing is authorised.

The states are `live`, `paused`, `stopped` and `revoked`. `paused` can go
back to `live`. `stopped` can only be revoked. `revoked` is the end: the
only way back is a brand new `sign`.

## The six floors, never grantable by any contract

No contract, ever, no matter what its risk classes say, can authorise:

- `credential-entry`: typing credentials, passwords or 2FA codes
- `payment`: executing any payment or movement of funds
- `account-signin`: creating an account or completing a sign-in
- `permanent-delete`: permanent deletion, or any write to production state
- `publish-release`: publishing or releasing
- `governance-write`: writing to the authorisation machinery itself:
  the project's own `.brothermode` store, its `.git` directory, or
  `.claude/settings.json`

`gate-check` refuses a floor WITHOUT even reading the contract. That
matters: if somebody edited the store file directly and slipped a floor
id into a contract's granted classes, `gate-check` still refuses it,
because the floor check runs before the contract is ever consulted.

`governance-write` is the one PATH-shaped floor (landed 2026-08-06,
founder decision 2026-08-05). It is enforced twice: `sign` refuses an
`allowed_paths` entry that names one of the three surfaces, and
`gate-check` refuses any candidate path inside one before the contract's
`allowed_paths` are consulted, so no spelling of a broad allowance
(`.`, `*`, `**`, a covering glob) can reach them. A legitimate
`local-commit` or `local-branch` is unaffected: those are ACTION classes
going through git's own porcelain, not path grants over `.git`.

`sign` also refuses a contract that grants any writing risk class while
declaring no `--allowed-path` (`no-write-scope`): risk class alone is
not a boundary. Genuinely read-only work signs with no paths by granting
only `read-only-inspect` and/or `browser-read`, which is the schema's
one way to say read-only.

## The signer check is a denylist, not an authentication check

`sign --signed-by` refuses a name that contains a model name, an obvious
generic word for one, or a vendor's own product word, case-folded and
Unicode-normalized (about thirty tokens: `claude`, `opus`, `sonnet`,
`gpt`, `gemini`, `llama`, `assistant`, `agent`, `bot`, `ai`, and the
rest). That is real, and it catches a model that fills the field with its
own name because that is what it is.

It is not, and never claims to be, an identity check. Stated in full so
nobody reads it as stronger than it is, it does NOT catch:

- a model deliberately told to sign as a real person's name (an initial
  like `K.` reads no differently from any other short token, and this
  check has no way to tell them apart, so it does not try)
- a vendor or model name not yet on the list
- a name written in a non-Latin script
- a deliberate misspelling (`cl4ude` does not fold to `claude`)

The only real answer to "did a human actually sign this" is a human-held
signing secret, and U1 does not have one. Treat the signer check as a
speed bump against the accidental case, never as proof of who actually
typed the name. The full limit is repeated in `docs/KNOWN-LIMITS.md`.

The controller's driver-ownership check has the same shape and the same
boundary. `step`, `plan`, `record-result` and `stop` refuse a caller
whose `--session-id` does not match the run's recorded driver
(`not-driver`), and `adopt` is the one audited takeover. But the session
id is self-asserted and readable (`status --json --raw` prints it), so it
keeps two COOPERATING sessions from colliding and records who did what; it
is not a defence against a hostile local process, which already holds the
founder's filesystem. A real trust boundary is a separate OS account or
machine, not a separate session. `docs/KNOWN-LIMITS.md` states this in
full beside the closed L09 items.

## The fourteen commands, one example each

Every mutating command needs `--actor-name` and takes an optional
`--actor-type human|model` (default `model`) and `--session-id`. Exit
codes: 0 success, 1 a refusal, 2 a usage error (produced before the store
is ever opened, so a bad flag never touches the database).

**sign**: authorise a project for the first time, or amend a live
contract with `--supersede`.

```
python3 tools/bm_autonomy.py sign --project my-app \
  --outcome "ship the export feature" \
  --done-definition "tests green, docs updated" \
  --allowed-path src --risk-class file-edit --risk-class build \
  --token-ceiling 500000 --minutes-ceiling 240 \
  --signed-by "Khalil Maaouni" --actor-name founder
```

**show**: the live contract, or the whole revision chain with `--all`.

```
python3 tools/bm_autonomy.py show --project my-app --all
```

**gate-check**: would this action be authorised right now. Never writes;
exits 1 for every refusal, not only for a usage error, because the
intended caller is a shell loop that must never read a refusal as a yes.

```
python3 tools/bm_autonomy.py gate-check --project my-app \
  --action-class file-edit --path src/export.py
```

**assume**: record one reversible assumption against the live contract.

```
python3 tools/bm_autonomy.py assume --project my-app \
  --text "the export API will not change shape mid-session" \
  --reversal "revert the export client to its last commit" \
  --actor-name founder
```

**interrupt**: raise one forcing-condition question. `--condition` must
be one of `design-ambiguity`, `contradiction`, `hard-gate-collision` or
`disproven-assumption`; anything else is an assumption to record, not a
question to ask.

```
python3 tools/bm_autonomy.py interrupt --project my-app \
  --condition contradiction \
  --question "the spec says CSV only, the ticket says CSV and JSON" \
  --actor-name founder
```

**spend**: record tokens and/or minutes spent, and print the breaker
verdict (`ok`, `soft-stop` at 80 percent, `hard-stop` at 100 percent, or
`no-data` when no ceiling was ever set).

```
python3 tools/bm_autonomy.py spend --project my-app --tokens 42000 \
  --minutes 12 --actor-name founder
```

**pause**: suspend authorisation without losing it.

```
python3 tools/bm_autonomy.py pause --project my-app \
  --reason "stepping away for lunch" --actor-name founder
```

**resume**: restore authorisation from paused back to live, unchanged.

```
python3 tools/bm_autonomy.py resume --project my-app \
  --reason "back at the desk" --actor-name founder
```

**stop**: the 3am kill switch. `--reason` is optional on purpose:
requiring a sentence from somebody stopping a runaway session is a
usability defect dressed up as rigour. Calling it twice is harmless: the
second call writes nothing new and still exits 0.

```
python3 tools/bm_autonomy.py stop --project my-app --actor-name founder
```

**revoke**: end the contract for good. Terminal; only a fresh `sign`
restarts authorisation.

```
python3 tools/bm_autonomy.py revoke --project my-app \
  --reason "feature shipped" --actor-name founder
```

**status**: one screen, always readable even while paused: state, spend
against each ceiling or `NO-DATA`, open interruptions against the
zero-to-three target, assumption count, open human steps by lane, and the
most recent controller checkpoint.

```
python3 tools/bm_autonomy.py status --project my-app
```

**queue-human-step**: queue one step only a human can complete. It blocks
the lane it names and no other.

```
python3 tools/bm_autonomy.py queue-human-step --project my-app \
  --what "click Publish in the App Store Connect dashboard" \
  --floor publish-release --lane release --actor-name founder
```

**human-steps**: list open (default) or all human steps, or resolve one.

```
python3 tools/bm_autonomy.py human-steps --project my-app --resolve <id> \
  --resolution "published, build 42" --actor-name founder
```

**checkpoint**: record one controller liveness beacon. Not in the
founder's original named list (it lives in the field list, the data a
contract carries, rather than the command list), added anyway because a
store method that exists for the controller to call needs a door on this
command line or U2 would have to invent its own way in.

```
python3 tools/bm_autonomy.py checkpoint --project my-app \
  --controller-id session-abc123 --kind phase-boundary \
  --actor-name controller --actor-type model
```

## The U1/U2 boundary

U1 answers questions and keeps records. It drives nothing: no loop, no
dispatcher, no worktree, no tool invocation. `gate-check` ANSWERS "would
this be allowed"; it never blocks anything by itself, and a caller that
never asks it is never refused. Enforcement at the file level is the
fence hook's job (`docs/HOOKS.md`), and the fence's own fail-open
behaviour is unchanged by U1.

U1 also does not measure spend on its own. `spend` records what it is
TOLD was spent; nothing here counts tokens or minutes by itself, so a
controller that forgets to call it leaves an accurate contract sitting
behind a meaningless breaker.

The controller loop that will call `gate-check` before every unit of
work is a later loop (U2), and this page states what it needs so that
loop has nothing further to design:

- `gate-check`'s own JSON output carries the `revision` it judged
  against. A controller captures that number, does the work, then
  re-reads `show --project X --json` before the write lands: if the
  revision moved, the authorisation it acted on is stale.
- `spend`'s verdict is one of `ok`, `soft-stop`, `hard-stop` or
  `no-data`. The rule is mechanical: `ok` starts new units, `soft-stop`
  finishes work already in flight and starts nothing new, `hard-stop`
  checkpoints and halts, `no-data` starts units and says so.
- `status --project X --json` reports the contract's state on every
  read, straight from the store, never from a flag held in memory: the
  kill switch is a store read, not a signal a process has to catch.
- `human-steps --project X --lane L` lets a controller skip one blocked
  lane and continue every other one.

## A signed contract is not enough to start a run nobody is watching

Landed 2026-08-07. The contract answers "is this action authorised". It does
not answer "is this machine in a state where an unwatched run's mistakes
could be seen and undone", and those are different questions. A perfectly
live contract on a repository with unsaved work, a fence in advise-only
mode, and no recovery point is a fully authorised way to lose an evening.

So the controller asks the second question separately, and only when it is
told the run is unattended. `bm-controller start --unattended` (and `step
--unattended`) refuses unless seven conditions hold:

1. The write fence is ENFORCED (`BM_FENCE_MODE=enforced`), not advisory.
2. `BM_FENCE_STRICT=1`, so an edit to a file nothing has claimed is refused.
3. A repository is detected and a branch is named, so the run is undoable.
4. The working tree is clean, or every change in it was acknowledged by
   name on the command line.
5. A recovery snapshot exists, or one is taken before anything is written.
6. No OTHER session holds an active claim over the paths the contract lets
   this run write.
7. The records are readable and the run carries a stable `--session-id`.

Each failure is its own named reason code with its own plain-language
explanation. `docs/FULL-AUTO.md` is the operating page: it lists the codes,
shows the commands, and states what the gate does not do.

Two things worth knowing here rather than there. The controller REFUSES on
conditions 1 and 2; it does not switch them on for you, because the fence
hook is a separate process started by your runtime and nothing the
controller sets in its own process can reach it. And condition 6 reads the
contract's own `allowed_paths` as "the paths this run may write", because
before a unit graph exists that is the only boundary there is.

Nothing in this section applies without the flag. An interactive session
behaves exactly as it did before this landed.

## Paths are project-root relative, not relative to where you typed the command

`--allowed-path` and `--path` are always resolved against the project
root, exactly as if the command had been run from there. Running `sign`
or `gate-check` from a subdirectory does not change how a path is
interpreted: the store's own path-canonicalization function accepts no
"current directory" argument at all for these calls.

## What this page does not claim

Not a description of anything beyond what `tools/test_bm_autonomy.py` and
`tools/test_bm_store.py` exercise; see `docs/KNOWN-LIMITS.md` for the
autonomy layer's own dated limits section, including the signer-check
denylist boundary above, a path-scope race between signing and acting,
and the serialization behaviour when two signers race to amend the same
project's contract at once.

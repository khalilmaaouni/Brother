# The durable Full-Auto controller (U2)

Status: CURRENT as of 2026-08-05.

This page explains `tools/bm_controller.py`, the command line and the engine
behind it for U2: the loop that reads the signed autonomy contract (U1,
`docs/AUTONOMY.md`) on every pass, sequences durable units of work from an
outcome to a checked deliverable, survives a process death without repeating
completed work, and keeps independent lanes moving while one lane is
human-blocked. Store schema 15, `tools/bm_store.py`. U1 exposes the seven
store surfaces U2 consumes (`references/autonomy.md` names each one); U2
adds no enforcement of its own, only sequencing and durability.

No em or en dashes anywhere in this page.

## READ THIS BEFORE ANY UNATTENDED RUN (2026-08-06, gate landed 2026-08-07)

The write fence defaults to ADVISORY, for unattended runs exactly as for
interactive use: when the protection cannot do its job it allows the write
and says why. For a run nobody is watching that is too weak, so an
unattended run now has to ASK to be one, and the controller refuses to start
it unless the machine is in a state where the damage it could do is bounded.

An unattended run is one started with the `--unattended` flag:

```bash
export BM_FENCE_MODE=enforced
export BM_FENCE_STRICT=1
bm-controller start --project P --controller-id C --actor-name NAME \
  --session-id my-night-run --unattended
```

Without that flag NOTHING below applies and nothing about the controller
changes. Interactive use is exactly what it always was; the gate is not even
reached. That is deliberate: a safety posture that made ordinary work harder
would be turned off, and a posture that is turned off protects nobody.

### The seven conditions, and what each refusal means

With `--unattended`, the run refuses to start unless all seven hold. Every
refusal names its own reason code and says, in plain language, what was
being checked, what is wrong, and what to do next. The first six are
read-only; the seventh is the only one that writes anything, and it runs
last, so a refused start leaves nothing behind.

| Condition | Reason code if it fails |
| --- | --- |
| The write fence is ENFORCED, not advisory | `unattended-fence-advisory` |
| `BM_FENCE_STRICT` is set, so unclaimed edits are refused | `unattended-fence-not-strict` |
| A repository is detected and a branch is named | `unattended-no-repository` |
| The working tree is clean, or every change is acknowledged | `unattended-dirty-tree` |
| No other session holds a claim over the paths this run may write | `unattended-foreign-claim` |
| The records are readable and the run has a stable session id | `unattended-no-identity` |
| A recovery snapshot exists, or one is taken now | `unattended-no-snapshot` |

**The two fence conditions REFUSE; the controller does not switch them on
for you.** That is a deliberate choice and not an omission. Those two
variables are read by the fence hook, which your runtime starts as its own
separate process from its own environment, so nothing the controller sets in
its own process could ever reach it. A controller that "turned enforcement
on" would be reporting a protection nobody has, which is worse than one that
stops and tells you the truth. The refusal prints the exact command instead.

**The dirty-tree condition is acknowledged per file, never in bulk.** There
is no "yes, I know" switch. The refusal prints every unacknowledged path in
the exact spelling you must repeat back:

```bash
bm-controller start ... --unattended \
  --acknowledge-modified src/app/main.py \
  --acknowledge-modified docs/notes.md
```

A blanket flag becomes a habit, and a habit acknowledges the file you forgot
about as readily as the one you meant. A file that appears after you read
the list still stops the run.

**The recovery snapshot is the one already in the product.** It is the same
mechanism the autosave surface uses, reused rather than reinvented, so there
is one story about what is recoverable rather than two. If a snapshot cannot
be published but an earlier one for this worktree is still on record, that
counts: the condition is "a recovery point exists", not "a new one was
written". A tree that already matches its last commit counts too, because
the commit itself is the recovery point.

**A stable `--session-id` is required.** Without one every invocation
invents a fresh identity, so the next process would be refused as a foreign
driver of its own run, the snapshot this one took would be filed under a
name nothing looks for again, and every fence it holds would look like
somebody else's. Pass the SAME `--session-id` on every call of one run.

`bm-controller step` is gated the same way and by the same flag, because an
unattended driver is a loop, and a loop calling `step` directly would walk
straight past a gate that only guarded `start`.

### What this gate does NOT do

Stated plainly, because a safety feature that is trusted further than it
reaches is worse than none. Hooks are COOPERATIVE enforcement: arbitrary
shell writes are detected where possible, not contained, and no operating
system sandbox is provided (docs/KNOWN-LIMITS.md states the exact boundary).
The gate bounds the state the run STARTS from; it does not sandbox what the
run then does. Running on a dedicated branch or worktree is still the right
habit, and the branch condition above is the reason the controller now
insists you are at least on a named one.

## The controller model, in plain language

Once a founder has signed a contract for a project (U1), the controller can
begin a RUN against it: one run drives one project from an outcome to a
delivered, checked result. A run is made of UNITS, small pieces of work
(create a file, edit a file, run a build, and so on) with dependencies
between them, a role, a risk class, and a done-check command the controller
runs ITSELF to decide whether the unit is actually finished. The controller
never trusts a worker's own claim of success; it re-runs the done-check and
reads the real exit code.

Before a unit is handed out, the controller checks it against the signed
contract: its risk class, and EVERY path it declares it will write, not just
the first one. One forbidden path refuses the whole unit, because the brief
the worker receives carries the whole list. Every path in one unit is judged
under ONE contract revision: if the contract is amended part way through
that check, the whole check is re-run against the newer contract, and if the
contract moves again during the re-run, the unit is set aside for the next
pass rather than judged under two different authorisations.

"Inside the contract's allowed paths" means INSIDE, not merely overlapping.
A contract that allows `src/app` authorises `src/app` itself and anything
under it. It does not authorise `src`, and it does not authorise `.`, even
though both of those contain an allowed path: a unit cannot widen its own
authorisation by naming a parent directory. A contract that allows `.` still
allows the whole project, which is what signing `.` means.

Every forward step the controller takes is written to the store BEFORE the
action it authorises, so a crash never loses the controller's place. A fresh
process picking the run back up reads exactly where the last one left off,
and never repeats a unit that already reached DONE.

## The state machine, NEW to COMPLETE

One run holds exactly one of these states at a time:

- `NEW`: the run exists; nothing has happened yet.
- `ORIENTING`: reading the repository and the contract.
- `PLANNING`: the unit graph is being built.
- `READY`: a graph exists and something in it may be selectable.
- `EXECUTING`: at least one unit has been dispatched and the controller is
  waiting on it.
- `VERIFYING`: a result has come back and the controller is running its own
  done-check and verifier against it.
- `CHECKPOINTED`: the last wave was judged (accepted or rejected) and a
  liveness beacon was written.
- `WAITING_HUMAN`: every remaining unit is waiting on the founder; every
  other lane already finished. Three things put a unit there: an action
  inside one of the five safety floors, a unit whose dependency is DEAD
  (either `FAILED` after its retry ceiling or `SKIPPED` by a later re-plan,
  neither of which the controller can move on its own), and a unit whose
  lane already holds an open founder step. In every case the queued step
  names what is stuck, which unit it is stuck behind and what state that
  unit is in, and the run waits for the founder instead of resting in
  `CHECKPOINTED` forever.
- `DELIVERABLE_READY`: every non-founder-gated unit is DONE and the
  contract's whole done-definition passed one more time, after the last
  edit.
- `COMPLETE`: the founder accepted the deliverable. Terminal.
- `PAUSED`: the underlying contract was paused, or the founder asked the
  controller to pause. Reversible, and **the founder's alone**. While a run
  is `PAUSED` the controller starts nothing, judges nothing, verifies
  nothing, delivers nothing, and abandons no hung dispatch; `plan` refuses
  outright rather than un-pausing the run as a side effect of writing a
  unit graph. Only `bm-controller resume` leaves that state. A result that
  arrives during a pause is RECORDED AND HELD, never rejected: the answer
  is durable, nothing is judged and no rollback command touches your files,
  so a pause never destroys real work. `bm-autonomy resume` (the contract)
  and then one `bm-controller step` verifies the held answer on its own
  merits.

  The meter is the one thing a pause cannot keep whole. Spend is only
  recordable against a live authorisation, so a cost reported while the
  contract is paused CANNOT be charged, and it is not carried on the
  recorded result either, so it cannot be charged later. It is disclosed
  instead: a checkpoint records the exact tokens and minutes, and a founder
  step in the reserved lane `spend-reconciliation` names them, names the
  `bm-autonomy spend` command that charges them, and names the
  `bm-autonomy human-steps --resolve` command that closes it. **The run
  will not be declared `DELIVERABLE_READY` while any of those steps is
  open**, so a ceiling can never be met on paper by spend that was never
  counted. That lane holds no units, so it blocks no work: `bm-controller
  plan` refuses a unit whose lane is that name, by name, before anything is
  written, and the delivery block reads only the steps the disclosure
  itself wrote, so an unrelated step that happens to sit in that lane
  blocks nothing.
- `STOPPING` then `STOPPED`: draining in flight work, then done for this
  run. Terminal once `STOPPED`; a fresh contract and a fresh run are what
  restart work, never a reopened `STOPPED` row.
- `FAILED_RECOVERABLE`: a fault happened (a unit exhausted its retries, a
  worker outage, a crash detected on resume) but the run itself can still
  continue. Reversible back to `READY`.
- `FAILED_TERMINAL`: an unrecoverable state, most often a rollback that
  itself failed, leaving a write scope that needs a human to look at it
  before any further work touches it. Terminal.

Each unit underneath the run has its own, finer state (`PENDING`, `READY`,
`CLAIMED`, `DISPATCHED`, `RESULT_IN`, `VERIFYING`, `DONE`, `FAILED`,
`BLOCKED`, `SKIPPED`): a run can be `EXECUTING` while some units are still
`PENDING` on a dependency and others sit `BLOCKED` behind a founder-only
step, which is exactly how one blocked lane never stalls the rest of the
run.

`BLOCKED` is a VIEW of one fact and nothing else: **your lane holds an open
founder step.** It is recomputed on every pass, in both directions. Queue a
step in a lane and that lane's waiting units show `BLOCKED`; resolve the
last open step in a lane and its units come straight back to `PENDING` or
`READY` on the next `step`, by whether their dependencies are done. There
is no separate un-block command and there is nothing to remember to undo.
A unit dropped from the graph by a re-plan (`SKIPPED`) can be brought back
the same way: re-add it, unchanged, and it returns to the graph.

## Why a pass stopped: the `reason` line

`bm-controller step` and `bm-controller start` print a `reason:` line
whenever a pass has stopped for a reason, and `--json` carries the same
value as `stop_reason`. It is one word out of a fixed list, and it is the
one thing to read first, because it says whether you need to do anything:

| reason | what it means | do you need to act? |
|---|---|---|
| `TERMINAL` | the run is `COMPLETE`, `STOPPED` or `FAILED_TERMINAL` | no, it is over |
| `DELIVERED` | the run is `DELIVERABLE_READY` | yes, `bm-controller complete` |
| `FOUNDER_WAITING` | paused, every remaining unit waits on a human, or your done-definition failed | yes |
| `SPEND_STOP` | the soft spend stop is on and nothing is in flight | yes, raise the ceiling or accept the stop |
| `OUTAGE` | the worker reported itself unavailable | no, try again later |
| `CONTENTION` | another writer holds a fence over the files, or the contract was amended twice mid-check | **no**, the next pass tries the same unit again |
| `IN_FLIGHT` | a dispatch is open, waiting for `record-result` | no |
| `NOTHING_SELECTABLE` | nothing selectable, nothing in flight, nothing founder-gated | yes, inspect the graph |
| `CONTRACT_PAUSED` | you paused the CONTRACT, and that is what paused this run | yes, `bm-autonomy resume` first, then `bm-controller resume` |
| `CONTRACT_NOT_LIVE` | the contract is stopped, revoked or gone; nothing is authorised and nothing was run | yes, sign a fresh contract or stop the run |

No line is empty and no reason is missing. A pass with no `reason:` line
made progress and the loop kept going. `CONTENTION` in particular does not
mean a founder is needed for a transient overlap: it clears when the other
writer releases its fence or the contract stops moving. If every pass
reports it, that is the other case the note names, and then somebody does
have to find the stale claim and release it.

`CONTRACT_PAUSED` and `FOUNDER_WAITING` are deliberately different words
for two pauses that look identical from outside. If you paused the
CONTRACT, `bm-controller resume` on its own will not clear it: the next
pass re-reads the contract, finds it still paused, and pauses the run
again. Clear the contract first, then resume the run. If only the RUN is
paused, `bm-controller resume` is the whole answer, and the reason is
`FOUNDER_WAITING`.

## The kill switch stops commands, not just new work

`bm-autonomy stop` and `bm-autonomy revoke` end the contract, and from that
instant the controller runs NOTHING on your machine. Not a unit's own
done-check, not its verifier, not the `git restore` rollback, not your
final done-definition. The contract is re-read immediately before every
single one of those, so a result that arrives after you pulled the switch
is recorded, rejected, its fence parked, and a founder step queued naming
the unit and the dead contract, with no command executed at all.

That matters because in full auto the unit graph, including each unit's
done-check and verifier, is written by the orchestrating model, not by you.
Stopping the contract has to stop those, and it does.

A kill that lands WHILE a command is running is handled too, and honestly.
If you stop or revoke the contract during a unit's done-check, that check
had already started under a live authorisation, so it finishes; the
authorisation it ran under is gone by the time its exit code is read, so
the result is REJECTED exactly as a stale one is, the fence is parked, and
a founder step says so.

That holds at every point where a command can be running, not just the
first one. The authorisation is re-asked after the unit's done-check, after
its verifier, once more immediately before anything is written about
whether the unit passed, and again after your whole done-definition and
before the run is called `DELIVERABLE_READY`. So a stop pressed while your
test suite is running does not end with the run declared ready a second
later. **Pressing stop is never followed by work quietly completing.**

The controller never tells you nothing ran when something did. Every
sentence it writes about what did or did not run is built from the list of
commands it actually executed in that call, not from which rule stopped it,
so it names them: the unit's done-check, its verifier, the rollback, your
done-definition, whichever of them really ran.

**Your spend ceiling is the second brake, and it stops commands too.** Once
`bm-autonomy gate-check` answers `REFUSED-BREAKER`, the controller runs
nothing further for that project: not a done-check, not a verifier, not a
rollback, and not your whole done-definition, which is the most expensive
command in the system. The one exception is stated rather than hidden: if
the unit whose result is in hand is itself what pushed the meter over, that
one already-paid unit is still judged, because refusing to read an answer
you have already paid for destroys it without saving a token. Nothing new
starts, the run drains on the next `step`, and no deliverable is declared.

Pausing is the reversible version and behaves differently on purpose. A
result arriving while the contract is paused is HELD: recorded, nothing
judged, nothing rolled back, no retry burned, the fence still held. When
you resume, that same answer is judged on its own merits rather than thrown
away. Pausing and resuming does not count as your authorisation changing,
so a held answer is not rejected as stale for it; a real amend to the
contract still is.

## What a write scope may be, and what is refused

A unit's `write_scope` is the single most consequential field in a unit
graph, because three separate things read it: the file claim that fences
the unit, the brief the worker is authorised by, and the `git restore`
rollback the controller runs when the unit is rejected. So it is held to
one narrow rule, and `bm-controller plan` refuses anything else BEFORE it
writes a thing, leaving the run exactly where it was:

- it must be a LIST of paths. A bare string like `"a.py"` is refused rather
  than walked character by character, and a number or an object is refused
  rather than crashing;
- every entry must be a plain relative path inside the project. No
  patterns (`*`, `?`, `[`), nothing absolute, nothing starting with `~` or
  `-`, and in particular **nothing starting with a colon**: git reads a
  leading colon as pathspec magic rather than as a file name, so `:/` means
  the whole repository and `:!x` means everything except `x`. A rollback
  built from either would have restored files the unit never named,
  destroying uncommitted work elsewhere in your tree.

The refusal names the unit, the entry and the reason, and the fix is
always the same: name the files, or name the directory they live in, which
grants its whole subtree. The same check runs a second time at dispatch,
on the row as the engine actually reads it, so a unit graph that reached
the store by some other route is refused there too rather than acted on.

## The harness and the seam, stated honestly

The controller is a HARNESS, not a process that spawns real subagents on its
own. It persists and sequences units; the ORCHESTRATING MODEL executes
them. "Dispatch" means record-intent-and-await: the controller writes a
unit's brief (its objective, its read and write scope, its role, its risk
class) to standard output and returns control immediately. The run parks in
`EXECUTING` until a result arrives out of band, through
`bm-controller record-result`. This process never calls a model and never
blocks waiting for one.

Two boundaries are genuinely impure and cannot be made deterministic from
inside this project: whether the worker actually made the right change, and
whether a real shell command actually passes. Both are isolated behind a
single, narrow interface each (a worker adapter and a check runner), so the
loop itself is fully testable with no live model and no network, and the
production implementations are thin: one prints a brief and returns, the
other runs the founder's own done-check, verifier, and rollback commands
and nothing else.

## The at-most-once limit on external side effects, stated honestly

SQLite writes inside the store are transactional, and a dispatch's own
`(unit_id, attempt)` pair is unique, so the RECORDING of an accepted result
is exactly-once: the store can never end up with two accepted results for
one attempt. A real worker's real file edit or real commit is not something
this project can make exactly-once the same way. What the controller
actually provides is at-most-once RE-DISPATCH (a held fence plus that same
unique key stop a crash-and-retry from opening a second live dispatch),
idempotent recording, and a post-crash re-verification through the SAME
independent done-check rather than trusting that a prior side effect
happened. This limit holds fully where the underlying operation is
idempotent (creating the same file twice is the same file; running a build
twice is the same build) or where the worker is the record-intent kind
described above. It is not claimed to hold for every conceivable side
effect a worker could take, and `docs/KNOWN-LIMITS.md` states this dated,
alongside the other limit this loop found while building it.

## The founder-gated remainder, what DELIVERABLE_READY actually names

`DELIVERABLE_READY` is not a claim that everything is finished; it is a
claim that every unit the controller was authorised to run on its own IS
finished, and it names exactly what remains: any open human step (an
action inside one of the five safety floors, never grantable by any
contract: credential entry, payment, account sign-in, permanent deletion,
publish or release) and any unit that reached `FAILED` after its retry
ceiling. Only a founder action, `bm-controller complete`, moves a run from
`DELIVERABLE_READY` to `COMPLETE`; the controller never makes that move on
its own.

## The nine commands, one example each

Every mutating command needs `--actor-name` and takes an optional
`--actor-type human|model` (default `model`) and `--session-id`. Exit
codes: 0 success, 1 a refusal, 2 a usage error, the same law
`tools/bm_autonomy.py` states for itself.

**start**: begins a fresh run when the project has no controller run at
all, or resumes an existing one otherwise, whatever state it is in.

```
python3 tools/bm_controller.py start --project my-app \
  --outcome "ship the export feature" \
  --done-definition "python3 tools/test_bm.py" \
  --controller-id session-abc123 --actor-name founder
```

**plan**: carries the unit graph the orchestrating model already built into
the run. This is the one judgement the design leaves to the model, never to
the harness.

```
python3 tools/bm_controller.py plan --project my-app \
  --units-file units.json --controller-id session-abc123 \
  --actor-name founder
```

**step**: one pass of the resumable loop.

```
python3 tools/bm_controller.py step --project my-app \
  --controller-id session-abc123 --actor-name founder
```

**record-result**: the far end of record-intent-and-await; the
orchestrating model, having done a unit's work, hands the result back. The
dispatch id comes from `status --json`'s own `open_dispatches` map, since a
printed brief carries no dispatch id of its own.

```
python3 tools/bm_controller.py record-result --project my-app \
  --dispatch-id <id> --worker-claim "created src/export.py" \
  --artifact src/export.py --controller-id session-abc123 \
  --actor-name founder
```

A result can arrive LATE: after the founder stopped the run, or after a
re-plan dropped that unit. That is a real situation, not an error, and the
command handles it rather than refusing. Three outcomes, and the command
prints which one happened:

- **accepted**, the ordinary case.
- **rejected**, on a stopped, delivered or terminal run, which cannot judge
  new work. The result is recorded and rejected, the unit's rollback runs,
  the founder is warned if that rollback left the write scope dirty, the
  file claim is released, and the run state is left exactly where the
  founder put it. A founder step naming the unit is queued in the UNIT'S
  OWN lane, **every time**, including when the unit's retry ceiling is
  already spent, so a real answer is never discarded with nothing to show
  for it. That lane gate is what stops a stopped or delivered run from
  growing new selectable work; re-planning or a fresh run is what attempts
  the unit again. Spend is charged for the work that really happened,
  accepted or not, WHENEVER the contract is live enough to charge it: if
  the contract was paused, stopped or revoked when the result arrived, the
  charge cannot be recorded at all and is disclosed as described under
  `PAUSED` above.
- **held**, when the run is `PAUSED`. Nothing is judged, nothing is rolled
  back, the file claim stays held; `bm-controller resume` plus one `step`
  verifies it.

A result for a unit a re-plan DROPPED is refused outright, naming
`dispatch-cancelled`: dropping a unit closes its open dispatch in the same
breath, so a late answer can no longer quietly bring a dropped unit back
and reverse the re-plan. `bm-controller plan` tells you when it cancelled a
dispatch, so the refusal is never a surprise.

**status**: one screen: run id and state, a unit count by status, any open
dispatch waiting on a result, the spend verdict, open human steps, and the
most recent checkpoint.

```
python3 tools/bm_controller.py status --project my-app
```

**stop**: the founder's kill switch on the run itself (distinct from
`bm-autonomy stop`, which ends the contract): drains in flight work and
releases every held fence.

```
python3 tools/bm_controller.py stop --project my-app --actor-name founder
```

**resume**: the founder's own reverse of a pause.

```
python3 tools/bm_controller.py resume --project my-app --actor-name founder
```

**complete**: `DELIVERABLE_READY` to `COMPLETE`, a founder action, never a
controller self-move.

```
python3 tools/bm_controller.py complete --project my-app \
  --actor-name founder
```

**adopt** (L09, 2026-08-06): the one deliberate takeover of a run another
session drives. Since L09, `step`, `record-result` and `stop` refuse a
caller whose session is not the run's recorded driver (`not-driver`);
adopt records the handover durably (attribution event
`controller.run.adopted`) and makes this session the driver. Refused on a
run that is already finished, and a no-op when you already drive it.

```
python3 tools/bm_controller.py adopt --project my-app \
  --actor-name founder --note "night orchestrator taking over"
```

## What this page does not claim

Not a description of anything beyond what `tools/test_bm_controller.py`
exercises. Five limits, restated here in the founder's own reading order;
all of them are also recorded, with the rest of this loop's residuals, in
`docs/KNOWN-LIMITS.md`:

- **Stale-heartbeat adoption is a store primitive only, not wired into
  `begin()`.** A dead controller is recovered by the SAME identity
  resuming, and a duplicate live controller is refused by the fence, but
  automatic cross-identity adoption of a dead controller's run needs a
  later store method and a founder displace flag; `bm-controller start`
  does not attempt it today.
- **The at-most-once limit above holds only where the operation is
  idempotent, or the worker is the record-intent kind.** It is not a
  claim that every external side effect a worker could ever take is
  made exactly-once by this project.
- **A plain path in `allowed_paths` grants its whole subtree; a pattern
  grants the FILES it matches at its own depth, and no directory.** `api`
  authorises `api/pay.py` and `api/sub/deep/secrets.env`. `api/*.py`
  authorises `api/pay.py` and nothing else: not `api/notes.md`, not `api`
  itself, not anything deeper. A pattern will not authorise a DIRECTORY
  either, even one it matches: `src/*` does not authorise `src/app`,
  because claiming a directory fences its whole subtree, and that would
  hand out `src/app/deep/keys.pem`, which the same contract refuses when
  you name it. A name carrying an extension is read as a file and an
  extensionless name as a directory, so a pattern also skips `Makefile`
  and `.env`. `**` is not recursive; the recursive spelling is the plain
  directory. Write the plain directory when you mean the subtree, and
  write the file's own name when you mean one file.
- **`allowed_paths` has no floor.** A contract signed with `.` authorises
  the whole project, including `.git/`, `.claude/` and BrotherMode's own
  `.brothermode/store.db`. Grant the directories the work actually needs.
- **The duplicate-controller refusal is on `begin()` only.** Two drivers
  calling `step` against one project at the same moment are not refused.
  Run one controller per project.

See `references/autonomy.md` for the seven U1 surfaces the controller
reads and writes and how `gate-check` is consulted on every path-bearing
action.

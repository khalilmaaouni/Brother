# The `sbe` command line

One entry point instead of nine script paths.

```
bin/sbe doctor
```

No install step. `bin/sbe` puts `src/` on the path itself and calls the package, because this
project's promise is that a clone works with nothing else installed, and requiring
`pip install` to reach the command line would quietly retract that for anyone on a locked-down
machine or in a CI image with no package index. Put `bin/` on your PATH or call it by path.

## What it is, and what it is not

It is a **facade**. Every built subcommand delegates to the tool in `tools/` that already
carries the behavior, the evals and the unit tests, and it hands back that tool's exit code.
Nothing was reimplemented to build this surface, and nothing in `tools/` changed. The old
invocations still work exactly as documented everywhere else in this repository, and they are
not deprecated: deprecating a command that 509 evals and a dozen pasted doc examples point at
is a separate change with its own risk, and it is not being smuggled into a packaging wave.

## Commands

| Command | Does |
|---|---|
| `doctor` | checks this installation and the environment it will run in |
| `verify` | design completeness check, then the hard gates, then the scored surface. WRITES into the target directory by default, on every run, with no dry-run flag: it mints evidence receipts under `.sbe/evidence` and, for every FAIL or WAIVED line, a decision package (suppress the decision packages only with `--no-decisions`; the evidence receipts still write) |
| `review` | the scored surface including soft findings, plus the hard gates. Prints only unless `--write` is given, which persists `11-review.json` into the dossier |
| `design` | delegates to `tools/sbe_design.py` |
| `gate` | delegates to `tools/sbe_gate.py` (a gate name, or a directory for all of them). WRITES a decision package into the target directory for every FAIL or WAIVED line, unless `--no-decisions` is given |
| `score` | delegates to `tools/sbe_score.py`. WRITES a decision package into the target directory for every FAIL or WAIVED line, unless `--no-decisions` is given |
| `intake` | delegates to `tools/sbe_intake.py` |
| `decide` | delegates to `tools/sbe_decide.py` |
| `fences` | prints the live fences the write hook would enforce |
| `impact` | reads the git diff and reconciles it with the declared intake tier |
| `inspect-change` | alias of `impact`, the name the finalization brief uses |
| `review-route` | deterministic reviewer selection from a diff: no model chooses, at most two specialists, zero is a legal result, never claims a clean review |
| `evidence` | generate a receipt by running the command, verify it, or show its trust level |
| `task` | the write-scope registry: open, list, fence, check, and close with the diff-against-declaration postcondition |
| `adopt` | inspect a repository for installation readiness, dry run by default |
| `status` | blocker-first summary of where a change stands, read from recorded state; `--team` reads every change under `design/` into one ten-severity view with a `basis` honesty field per finding |
| `init` | install BrotherSBE's local footprint into a repository, dry run by default |
| `version` | the version and the evidence schema version |
| `plan` | derive `08-plan.json` from a dossier mechanically and validate it; an empty plan never exits 0 (delegates to `tools/sbe_plan.py`) |
| `instruction-surface` | did a changed CLAUDE.md, `.claude/**`, `.mcp.json`, `.claude-plugin/**`, `hooks/**`, agent or skill definition, CODEOWNERS or CI workflow stay inside declared, reviewed scope (delegates to `tools/sbe_instruction_surface.py`) |
| `work` | isolated lifecycle for one plan task: `start` (branch, worktree, fenced registry record), `check`, `finish` (postcondition AND a head-bound receipt, never an agent statement), `remove`, `brief` (a deterministic JSON work order for one task, read-only) |
| `handover` | `prepare`/`show`/`acknowledge`/`reject`: ownership transfer is complete only after a named human receiver acknowledges; the outgoing owner stays the owner until then |
| `pr` | `pr verify <number> --repo owner/name`: live GitHub approval evidence bound to the head sha; no credentials is NO-DATA with a remedy, never PASS |
| `converge` | does base..head still match the approved dossier: scope, contracts, data, architecture, verification; no force flag exists |
| `explain` | print the decision package for a decision id, or for a gate or check name; with no recorded run it regenerates one from the shipped registry and marks the verdict NO-DATA, and it never overwrites a package bound to another commit |
| `lineage` | walk the chain for one artifact oldest to newest: binding, receipts, decisions, notes and commits, an evidence pointer on every hop; an absent store is a named NO-DATA hop, never a shorter chain |
| `scope` | did the changes that survived stay inside declared scope: `scope verify --base REF [--head REF] [--strict]` is the CI backstop for the Bash and Stop write boundary, `scope report` says what the Stop hook would decide right now (delegates to `tools/sbe_session_reconcile.py`) |
| `protections` | is the repository itself protecting the control plane: `protections verify --repository owner/name --branch main` reads CODEOWNERS locally and the branch ruleset through `gh api` |
| `map` | a deterministic, offline HTML status page for ONE CHANGE, built from canonical state only: `sbe map --out FILE`. WRITES the named output file. Reach for this when the question is "what is the state of the work in front of me" |
| `program` | program-wide status from `program/PROGRAM.yaml` and `program/work-items/`: gantt, finished, in flight, blocked, risks, docs, budget; `check` fails when `STATUS.md` drifted; `board --out FILE` WRITES a self-contained HTML board rendering the same ledger. Reach for this when the question is "where is the whole programme of work", across many changes |

`sbe work brief --plan <08-plan.json> --task <id> [--out <path>] [--json]` runs every `start`
refusal (plan validation, unknown task, an open dependency, a task another OPEN registry record
already owns) without opening a branch, a worktree, or writing to the registry: it only reads.
On success it emits one JSON object with sorted keys and no timestamp field anywhere
(`schemaVersion`, `taskId`, `title`, `why`, `baselineCommit`, `planPath`, `scope`,
`mustNotTouch`, `dependencies`, `acceptance`, `verificationCommands`, `relevantPointers`,
`knownConstraints`, `stopConditions`, `requiredEvidenceKind`, `model`,
`maxAttemptsPerApproach`), so two calls against the same repository state produce byte-identical
output. `mustNotTouch` always carries the task's own `readOnly` paths plus the coordination
files no task may ever declare as its own scope: `.sbe/tasks.json`, `CHECKSUMS.sha256`,
`VERSION`, `.claude-plugin/`, and `08-plan.json`. A brief that would serialize past 8192 bytes
is refused by name, naming its largest section, rather than silently truncated. `--out` writes
the brief atomically (temp file, then rename); a dirty repository is named under
`knownConstraints` rather than refused. `agents/implementation-worker.md` is the paired agent
that reads a brief and does the work.

**Identity**: a task's identity is the pair (`change`, `id`), not `id` alone (`sbe task`, above).
`start` stamps `change` on the registry record it opens from the dossier's own basename (the same
string its branch name, `sbe/<change>/<taskId>`, already carried), so its dependency check and its
already-OPEN check are both scoped to THIS dossier: a same-named task open in a different one no
longer blocks starting this one, and a same-named task's closed record in a different one no
longer silently satisfies this one's own unmet dependency. `check`, `finish` and `remove` each
take an optional `--change`, needed only when a bare task id resolves to more than one record
across different changes; when it does, the ambiguity is refused, every colliding `(change, id)`
pair named, rather than a guess at which one was meant, and the value given IS STRIPPED before
comparison, the same way `sbe task open --change` strips it before storing, so a padded value that
opened cleanly stays addressable by the identical padded string. `brief`'s own dependency and
already-claimed checks are the one exception, left unscoped (global, by `id` alone, the same
collision class `start` closes) and never raise ambiguity: `brief` has no `--change` flag to
recover with, so its already-claimed lookup always resolves to the LAST matching record
(append order), the exact pre-`change` behavior, rather than refusing over a flag that does not
exist on this subcommand. `tools/test_sbe_work_brief.py` pins that unscoped shape with fixtures
that predate `change`. Full limits: `docs/KNOWN-LIMITS.md` ("Task identity becomes (change,
taskId)").

**Worktree directory**: `start`'s default worktree directory is still, primarily, the
repository's parent directory, exactly as before this pair existed, so a single dossier lands at
the same path it always did. Only when that plain default path is ALREADY TAKEN (the common case
after upgrading: a sibling dossier's own task with the same id, since every derived plan starts
fresh at "T01") does it fall back, automatically and out loud on stdout, to a subdirectory of the
same parent named for THIS dossier's own change id, so the headline journey (two dossiers running
the same task id, one after another, no `--worktree-dir` given by either) now succeeds under
these documented default flags. An EXPLICIT `--worktree-dir` never falls back: two dossiers an
operator points at the identical directory by hand still collide there, exactly as before, because
that is the operator's own choice, not a default trap.

Two more are **present and refuse**: `policy` and `exceptions`.
Each names what is missing and which wave builds it, and exits 3.

`policy evaluate` reads `.sbe/policy.yml`, applies every matching rule to the diff, and reports one
state per requirement: `SATISFIED`, `MISSING`, `INVALID`, `STALE`, `UNPROTECTED` or `NOT-REQUIRED`.
Required and absent is `MISSING` and exits 1. Not required is `NOT-REQUIRED` and exits 0 without
manufacturing a pass. Neither is `NO-DATA`, and that separation is the point: one word carrying both
meanings is how a missing receipt kept reading as a clean bill of health. The minimum detected tier
is a floor, so a declared tier below it is `INVALID` unless a decision record with a protected
approval says otherwise, and an accepted exception renders `WAIVED`, never `PASS`.

One more is **present and refuses**: `exceptions`.
It names what is missing and which wave builds it, and exits 3.
They are listed rather than hidden so nobody has to guess whether they exist, and they refuse
rather than printing an empty result, because a command that succeeds at nothing is the exact
failure this project exists to stop.

## `--cwd`, the flag every subcommand shares

Every `sbe` subcommand, at every depth (`sbe work lease acquire`, `sbe evidence run`, a bare
`sbe status`), accepts `--cwd <repo>`: an alternate way to name the repository or dossier the
command operates on, for a caller running from somewhere else entirely (a wrapper script, a
CI step whose own working directory is not the checkout, an agent driving several repositories
from one shell). `./bin/sbe status --cwd .` is the exact repro this closed: before it, `--cwd`
was wired by hand into four subcommands (`work start`, `work finish`, `task open`,
`evidence run`) and refused everywhere else.

The flag is spelled ONCE, in `brothersbe.cwd` (`add_cwd_argument`, `resolve`,
`resolve_relative`, `split`), and every command resolves its target through that one place
rather than carrying its own copy:

- A command that already has an optional positional or a `--path`-style flag for its target
  (`sbe status [path]`, `sbe explain --path DIR`) treats an explicit value there as winning:
  type a path on the command line and it wins over `--cwd`; give neither and the current
  directory is used, exactly as before this flag existed.
- A command whose positional names a FILE or a DOSSIER rather than a repository
  (`sbe handover prepare <dossier>`) anchors a RELATIVE positional at `--cwd` the way a shell's
  own working directory already anchors any relative path; an absolute positional is
  unaffected, because a caller who already knows exactly where the thing is is never
  overridden.
- A command whose whole argv belongs to a script under `tools/` this package does not own
  (`design`, `gate`, `score`, `intake`, `decide`, `fences`, `plan`, `instruction-surface`,
  `scope`) never teaches that script a flag it has never heard of: `--cwd` is read out of the
  argv before the script ever sees it, and the script is run with its OS-level working
  directory set to that value instead. Every one of those scripts already defaults its own
  notion of "the directory to examine" to the process's actual working directory the moment
  none is named on the command line, so this reaches the identical result without a second,
  competing definition of `--cwd` inside a file this package cannot edit.

A handful of subcommands have no repository or file to anchor at all (`sbe decide` with a
built-in table name, `sbe version` printing the version rather than bumping it): `--cwd` is
still accepted there, and harmlessly ignored, rather than refused as an unrecognized flag.

`tools/test_sbe_cwd.py` enumerates every subcommand from the parsers themselves (`cli.COMMANDS`
plus each nested module's own `_parser()`), never a hand-typed list, so a command added later
without `--cwd` cannot dodge the fixture; a second class of test there proves the observable
commands (`status`, `review`, `handover prepare`, `plan`, `evidence run`, `task open`,
`work start`) actually read the named repository, never the caller's own working directory.

## Exit codes

| Code | Meaning |
|---|---|
| 0 | the command ran and no control FAILED |
| 1 | a control FAILED, or the underlying tool exited nonzero |
| 2 | usage error: unknown command, missing argument, bad path |
| 3 | the command exists and is not built yet |

Read 0 precisely. It means nothing failed. It does **not** mean something passed: a run where
every check reported NO-DATA also exits 0, because nothing failed and nothing was examined
either. `verify` and `review` print a closing line saying so, since an exit code cannot.

Help means help, on every subcommand: `-h`/`--help` prints the owning surface's usage and
exits 0 before anything is read, scanned or written. For the passthrough commands (design,
gate, score, intake, decide, fences, plan, instruction-surface, evidence, task, work,
handover, pr, explain, lineage) the whole argv,
including `-h`, goes to the tool or module that owns the parsing, so the usage you see is
that surface's own; for everything else the CLI answers directly. A flag a surface does not
know is refused with usage and exit 2, never silently ignored: a typo must not run as if
nothing were wrong. The one deliberate exception is `tools/sbe_fence_hook.py`'s bare hook
invocation, which stays fail-open by law; its explicit `-h` still exits 0, with usage on
stderr because that tool's stdout is the hook decision channel.

## Machine-readable output

`doctor --json` emits the tool version and the evidence schema version alongside its checks,
so a consumer can tell which contract it is reading:

```json
{
  "tool": "sbe",
  "toolVersion": "1.0.0-rc.28",
  "schemaVersion": "1.0",
  "command": "doctor",
  "result": "PASS",
  "checks": [{"name": "python", "result": "PASS", "detail": "3.9.6 (floor is 3.9)"}]
}
```

JSON output on the other commands arrives with the evidence work, not before: emitting a JSON
envelope around a verdict whose provenance is not yet bound to a commit would dress an
advisory result as a machine-readable authoritative one.

## `brothersbe.contracts`, one schema registry for every JSON surface

Five things this tool writes each carry a `schemaVersion` and a promised set of fields: the
task registry (`.sbe/tasks.json`), `sbe status --json`, `sbe status --team --json`, the work
brief (`sbe work brief --json`), and the handover record (`12-handover.json`). Before LP-0201
each of those five producers checked its own version and its own fields, independently, with
no single place naming what any of the five actually promise. `brothersbe.contracts` is that
place: a small, versioned Python module (no `jsonschema` dependency, standard library only)
that a consumer imports and calls directly, not a new `sbe` subcommand.

```python
from brothersbe import contracts

verdict, evidence, problems = contracts.validate_task_registry(registry_dict)
# verdict:  "PASS" | "FAIL" | "NO-DATA"
# evidence: one human-readable summary line
# problems: a tuple of the individual named failures, empty on PASS
```

Every `validate_*` function (`validate_task_registry`, `validate_status`,
`validate_status_team`, `validate_work_brief`, `validate_handover`) takes an already-parsed
Python object, the same object `json.load`/`json.loads` would hand back, and returns that same
three-value shape. `NO-DATA` means no document was given at all (`data is None`); a document
that exists but is the wrong JSON shape, is missing a required field, or names a
`schemaVersion` this build does not know is `FAIL`, never `NO-DATA`: absence and a broken
claim are different findings. Unknown fields are always ALLOWED (a document from a newer build
is forward compatible, not broken); an unknown `schemaVersion` is always refused, by name.
`contracts.validate(surface, data)` dispatches by surface name (one of `contracts.SURFACES`)
for a caller that wants to look one up instead of importing five function names by hand.

One surface is a deliberate, named exception: `sbe status --team --json` carries no
`schemaVersion` field in the running tool (`status.build_team_report` writes none as of
1.0.0-rc.16), so `validate_status_team` accepts its absence rather than manufacturing a
requirement the real command does not meet, and starts checking it strictly the day that
producer starts writing one. `contracts.CONTRACTS_SCHEMA_VERSION`, an integer, is this
registry's OWN version (starting at 1, the same major generation every one of the five
producers is already on), separate from any one surface's `schemaVersion` string.

## `sbe doctor`, and what WARNING means

Every check returns one of four results, never folded together: `PASS`, `FAIL`, `NO-DATA` (an
environment question nobody could answer, never counted as a pass), and `WARNING` (something
worth a person's attention that is not, by itself, grounds to block a run). Only `FAIL` moves
the exit code; a `WARNING` is doctor observing, not doctor failing.

The `identity` check reads this repository's `git config user.email` and `user.name` and flags
a fixture identity, an `@example.com` email or the literal name `ci`, as a `WARNING`, quoting
the value it found. That shape of identity authoring real commits is a leak that goes
unnoticed until someone reads the log by hand: a CI or scratch git config left active in a real
checkout is exactly how it happens. `doctor` surfaces it rather than passing silently over it,
and rather than hard-failing an otherwise-healthy environment over a question this command can
only observe, not adjudicate.

The `install-identity` check compares this source tree's own `VERSION` and `CHECKSUMS.sha256`
against every marketplace-installed copy under the plugin cache root
(`~/.claude/plugins/cache/brothersbe/brothersbe/*`, overridable with `SBE_PLUGIN_CACHE_ROOT` for
an isolated test run). A copy at the same version with an identical manifest hash is genuinely
this code and `PASS`es, naming the copy. A copy at the same version with a different manifest
hash is the defect this check exists to catch: the version string a person checks would read as
current while different code actually runs, so this is a `FAIL`, never folded into a silent pass.
A copy at a different version is a lagging install, not a lie, and `PASS`es with a note. No
installed copies, an unreadable cache root, or a copy missing `CHECKSUMS.sha256` to compare is
`NO-DATA`, never `PASS`: an unexamined install is never reported clean. The check only ever reads
`VERSION` and `CHECKSUMS.sha256`; it never executes anything from an installed copy.

## `sbe version bump`, one command for every declaration site

```bash
bin/sbe version bump 1.0.0-rc.9            # edit all sites, re-read, print reminders
bin/sbe version bump 1.0.0-rc.9 --dry-run  # show the edits, write nothing
```

`sbe version` alone still prints the version and schema. With `bump <new>` it moves every
declaration site the release invariant reads in one pass: `VERSION`,
`.claude-plugin/plugin.json`, `.claude-plugin/marketplace.json` (which carries the version
twice), and `DIGEST.md` line 1. After writing it re-reads all five declarations and fails
its own run if any one disagrees, because an edit that is not re-read is a claim rather
than a fact.

Refusals, each by name: a malformed target (the accepted shape is
`MAJOR.MINOR.PATCH` with an optional `-rc.N` tail, no leading `v`); declaration sites that
ALREADY disagree, every site and its current value printed, because bumping over a
disagreement would bury its evidence; a target equal to the current version, refused as a
no-op rather than reported as success; a missing site file, named with the root it was
expected under.

What it deliberately does NOT do, printed as reminders on every successful run: the
`CHANGELOG.md` heading (prose a person writes), `evals/replay_book.py --write` (book
echoes regenerate from live runs, never from substitution), and `CHECKSUMS.sha256`
(regenerated LAST in the seal order, after `git add`). Proven by
`tools/test_sbe_version_bump.py`.

## `sbe impact`, and the one rule that makes it safe

```bash
bin/sbe impact . --base main --intake design/my-change/00-intake.json
```

It converts detector hits into the same five intake answers a person gives, and hands them to
the intake's own `compute_tier`. One rule, one table, two inputs, so a tier derived from code
and a tier declared by a human cannot drift apart.

- It may **raise** a declared tier. It may **never lower** one.
- Disagreements are resolved by a **disposition**, not by an argument: a record naming the
  detector, the decision, the reason, who decided, and the head commit it was decided against.
  A disposition written against a different commit resolves nothing, and a disposition with no
  reason is an off switch rather than a decision.
- The proposed tier is a **floor**. `consumers` cannot be read from a diff and is assumed at
  its lowest value; every file no detector covers is listed under `unmeasured` by name.
- `--strict` and the exit code, in full. `REVIEW-REQUIRED` and `FAIL` exit 1 with or without
  the flag. `NO-DATA` exits 0 without it. Under `--strict`, a `NO-DATA` exits 1 only when the
  tool actually holds something nobody declared: detector hits proposing a tier above T0 with
  no intake to reconcile them against, or a diff it could not read at all. A `NO-DATA` whose
  derived answers are all at their lowest values (a docs, data or test-only diff no detector
  covers, or an empty diff) exits 0 even under `--strict`, and says so on stderr, because this
  project's law is that NO-DATA never decides an exit code: absence is reported, never graded.
  Grading it used to fail every docs-only pull request in the consumer workflow.

Verdicts: `PASS` (nothing in the diff contradicts the declared tier), `REVIEW-REQUIRED` (the
diff shows more than was declared, with no current disposition), `FAIL` (the intake is
malformed or unreadable), `NO-DATA` (no diff, or no intake to compare against).

Limits, in full, beside the behavior: `docs/KNOWN-LIMITS.md`. Maturity: **INTERNAL-EVAL**, run
on this repository's fixtures and its own diff, and on no other estate.

## Python floor

3.9, which is the system Python on the machine that maintains this, so it is the floor that is
actually exercised rather than the one that sounds modern. `doctor` checks it and FAILs below
it.

## `sbe evidence`, and the rule that makes a receipt mean something

```bash
bin/sbe evidence run --check control-plane-tests --out evidence/control-plane.json
bin/sbe evidence run --out evidence/tests.json -- pytest -q
bin/sbe evidence verify evidence/tests.json
bin/sbe evidence show evidence/tests.json
```

The invariant, in one sentence: **a receipt only counts as evidence for the commit it was
generated against, by a wrapper that ran the command itself.**

Before this, every receipt the gates read could be typed by hand by the same agent whose work
it verified. A fabricated duration, exit code, row count or rerun id satisfied the schema, so a
gate could PASS on a run nobody's command ever performed, and nothing bound a receipt to a
commit either, so one written against older code still passed after that code changed.

`run` **executes the command**, over the real, unredacted argv it was given. There is no flag
that accepts a duration, an exit code or an output digest; those come from the run or the
receipt does not exist. It records repository identity, base and head commit, argv, start and
end in ISO 8601 UTC, duration, exit code, python and `sbe` versions, the platform, whether the
tree was dirty, and the files the receipt covers with their content digests. Its own exit code
is the command's, so a failing command cannot be laundered into a passing evidence step by the
fact that a receipt got written about it.

**The recorded argv is redacted, not raw.** Before the receipt is written, every argv token is
checked against the same `SECRET_PATTERNS` `tools/sbe_telemetry.py` already uses to redact an
operator's own messages (imported, not a second list). A match becomes a named marker,
`[REDACTED:<shape>]` (`[REDACTED:api-key]`, `[REDACTED:aws-key-id]`, and so on), and the receipt
records `argvRedactions`, the count, so a reader can tell at a glance whether argv is verbatim
(`0`) or not. The command that RAN is untouched; only the copy written to the receipt is
masked. This narrows the old limit, it does not close it: the pattern list is finite, so a
secret in a shape none of these patterns know still reaches the receipt whole. Full statement:
`docs/KNOWN-LIMITS.md`.

**`--check <id>` runs the check REGISTERED in `.sbe/checks.yml`, and nothing on the command
line can substitute any part of it.** The executable, the exact argument vector, the working
directory, the covered paths and the environment all come from the registry; `--`, `--covers`
and `--kind` beside it are refused rather than quietly overridden. The receipt records
`checkId`, `checkKind`, `checkSpecSha256` (the digest of that check's specification), the
executable and its hash when it is a repository file, every `runnerFiles` hash, the argument
vector, the working directory, and the covered paths with their hashes, all sealed. `verify`
recomputes every one of them against the registry as it stands now, so editing the check,
renaming or modifying a runner, or changing an argument invalidates every receipt minted
before the edit: those receipts describe a check that no longer exists. Only the allowlisted
environment (`brothersbe.checks.ENV_ALLOWLIST`) reaches the process, and the receipt records
how many variables were dropped.

Why it exists: `run` proved a command ran, and `-- true` therefore minted a clean, sealed,
commit-bound receipt whose only claimed identity was the `--kind` word typed beside it. A
run WITHOUT `--check` is free form and stays available for local experimentation, and it is
always `LOCAL-ADVISORY` however clean the tree and whatever CI run id is set, and it satisfies
no check `.sbe/policy.yml` requires.

**`--kind {design,gate,score}` records WHICH obligation this run is evidence for.** Repeatable,
and written into the receipt as `checkKinds`, sealed with everything else. Without it the
receipt declares no kind, and `sbe status` will not let it clear a design, gate or score
obligation. This replaced an inference that was a live bypass: the consumer used to work the
identity out by substring-matching the recorded command line, so a receipt for `/bin/cat
tests/test_design_of_gate_score.txt` named all three words and cleared all three obligations,
for a command that ran no check at all.

What the field is: a declaration bound to a run that actually happened, sealed so it cannot be
typed into a receipt afterwards. What it is not: proof of what the command did. This wrapper
starts a process and times it; it does not understand it. An operator who declares `--kind
design` over a command that checks nothing has written a false statement, and the receipt
records the argv beside the declaration so a reader can see the two disagree. That residual is
the reason `show` prints the provenance sentence on every receipt.

Receipts written before this field existed stay readable: `1.2` adds `checkKinds` and
`checkKindsSource` and is judged only against receipts that declare `1.2`, the same way `1.1`
added `argvRedactions`. The bump is forward only. Nothing rewrites a receipt already on disk,
and a `1.0` or `1.1` receipt still verifies. It just clears no obligation, because it says
nothing about which check it was, and that is NO-DATA rather than a silent pass.

`--covers <path>` is repeatable and names the files this run is evidence for. Without it, the
files changed between base and head are used. `verify` re-hashes those files, which is how it
tells you the code moved after the evidence was made.

`--timeout SECONDS` bounds the command. Past it, the child is killed and no receipt is written:
no exit code was ever observed, so there is nothing honest to seal, and `sbe evidence run` exits
non-zero with the timeout named on stderr instead. **There is no default.** A silent timeout
would kill a legitimate long-running test suite and hand back nothing to show for it, which is
the exact false-positive pattern this project's own kill criteria warn against, so a run with no
`--timeout` can hang exactly as far as the command itself hangs. Pass it explicitly when the
command is untrusted or has hung before.

`verify` refuses a receipt path it cannot safely open **before** it opens it: a FIFO, a socket, a
device, or a file with no read permission is named and refused in bounded time, in both text and
`--json` mode, rather than read directly, which used to block the command forever on a FIFO with
no verdict printed at all.

**stdout and stderr are recorded as SHA256 digests and byte counts, never as text.** A receipt
gets committed, pasted into a pull request and handed to whoever asks for evidence, and a
command that prints a token would otherwise persist it in the one artifact everybody is
encouraged to share.

`verify` verdicts:

| Verdict | When |
|---|---|
| `PASS` | the seal matches, the head commit is current, every covered file still holds the bytes recorded, and the tree was clean at generation time |
| `FAIL` | the receipt path could not be safely opened (a FIFO, socket, device or unreadable file), the receipt does not parse, its schema version is unknown, a required field records nothing, its `checkKinds` is not a list of kinds this build knows, the seal does not match, the head commit has moved, or a covered file changed or vanished |
| `NO-DATA` | the receipt is sound but was generated on a dirty tree, or covers no file at all. Advisory is NO-DATA here, never a pass |

`--strict` makes NO-DATA block too. Every verdict line names what it inspected, because a
verdict that does not say what it read is not trustworthy output.

`show` prints the receipt and names its **trust level** every time: `CI-CLAIMED` needs three
things (the run was a check REGISTERED in `.sbe/checks.yml`, `SBE_CI_RUN_ID` was set by the
environment, and the tree was clean), `LOCAL-ADVISORY` otherwise. A CI job over uncommitted
edits is a local run wearing a badge, and a free-form command under a CI run id is not a
registered check, however clean the tree; both are labelled as what they are. (The
registration condition applies from schema 1.3 onward; older receipts are judged by their
own version's contract.)

The `runId` seal is **tamper evidence, not a signature**: it catches a plausible receipt nobody
produced, and it does not stop somebody who read `src/brothersbe/evidence.py`. That is why a
local receipt is never more than advisory. Limits in full: `docs/KNOWN-LIMITS.md`. Maturity:
**INTERNAL-EVAL**.

## `sbe task`, and the postcondition that survives Bash

```bash
bin/sbe task open --id wave5 --agent alpha --role writer \
  --base $(git rev-parse HEAD) --verify "python3 tools/test_sbe_tasks.py" \
  --owns src/brothersbe/tasks.py --owns tools/test_sbe_tasks.py
bin/sbe task list
bin/sbe task close wave5
```

The write fence is a PreToolUse hook that fails open and cannot govern Bash, because shell
cannot be parsed reliably. `sbe task` is the after-the-fact layer: `open` records who owns
what in `.sbe/tasks.json` (one file, atomic rewrite, no service), and `close` computes what
actually changed, the union of `git diff --name-only <base>...HEAD` and `git status
--porcelain`, inside the task's `--worktree` if one was declared, else the shared tree, and
compares it against the declaration. Uncommitted edits count, and a rename counts both sides.
The shell is never parsed; the diff is simply read.

- Every changed path outside the owned paths is a **violation, listed by name**: verdict FAIL,
  exit nonzero, the task stays open. Changed paths inside ownership print as the evidence of
  scope kept. A declared `--read-only` path that changed is a violation too, flagged as such:
  read-only means read.
- A base commit that no longer resolves is **NO-DATA with the reason, never a pass**.
- `close --force` requires `--who` and `--why`, records that disposition in the record, and
  marks the close **FORCED**, never silently clean.
- **A task's identity is the pair (`change`, `id`), not `id` alone.** Every derived plan starts
  fresh at "T01" (`sbe plan`), so two dossiers routinely produce a task called "T01"; `--change`
  (default the empty, unscoped string) records which change a task belongs to, and `sbe work
  start` stamps it automatically from the dossier basename. `open` refuses (exit 2, reason on
  stderr, the colliding `(change, id)` pair named) when the SAME `(change, id)` pair is already
  open, or when any owned path overlaps an owned path of another open task regardless of either
  task's change (a file collides with itself no matter which dossier asked for it). `close` takes
  an optional `--change` to say which one: a bare id that resolves to exactly one open task (the
  overwhelming common case) needs no `--change` at all; a bare id open in MORE than one change is
  refused as ambiguous, every colliding `(change, id)` pair named, never guessed at.
- `check` re-runs the overlap scan across all open tasks, so a collision injected into the
  JSON by hand is caught the same way `open` would have caught it. The overlap scan itself stays
  by path, not by `(change, id)`: two writers over one file collide regardless of change.
- `fence` renders the markdown fence view from the registry, one direction only, JSON to
  markdown, printed for a human to paste into a STATE.md style registry. Nothing reads
  markdown fences back into the registry, and the hand-written fence flow keeps working
  untouched.
- Reviewer separation: a `--role reviewer` task cannot open owning any path under the evidence
  store (default `.sbe/evidence`, `--evidence-dir` to override), and a reviewer whose diff
  touches a receipt FAILs at close **even under `--force`**; force may not waive that class.
  This separates roles inside the registry; it cannot stop an actor who never registers.

The registry file itself (`.sbe/tasks.json`) is exempted by exact name from the comparison,
because `open` writes it and counting it would make an ordinary single-writer flow unable to
close clean, which is this control's own kill criterion. `expiry` is informational: nothing
deletes a task on a clock. Concurrent writers of the registry file are out of scope (atomic
rename, last write wins, no lock). Limits in full: `docs/KNOWN-LIMITS.md`. Maturity:
**INTERNAL-EVAL**, exercised on this repository's fixtures and on no other estate.

**Schema**: `schemaVersion` moved from `1.0` (identity: `id` alone) to `1.1` (identity: the
`(change, id)` pair) when this pair landed. A `1.0` registry is still READ as-is by `list`,
`fence` and `check`; the shape change to `1.1` (every record gains `change`, defaulting to the
empty, unscoped string, since a pre-migration record carries nothing that says which dossier
produced it) happens on the FIRST `open` or `close` after upgrading, never merely by being read.
A registry `migrate_registry` cannot interpret (an unknown predecessor version, or a `change`
field of a type it was never told to expect) is refused by name and left byte-for-byte on disk,
never silently rewritten. The flip side of the empty, unscoped change a migrated record adopts:
identity is now the `(change, id)` pair, so that migrated record's `""` no longer collides with a
DIFFERENT, non-empty change stamped onto a fresh `open`/`start` for the SAME id. An operator's
first change-scoped start after upgrading, for an id that already has a legacy OPEN record, SUCCEEDS
rather than refusing, leaving TWO OPEN records for one id; a bare `close`/`check`/`finish`/`remove`
on that id becomes ambiguous, and `--change ''` is the working escape hatch that still addresses
the legacy record on its own. Full limits: `docs/KNOWN-LIMITS.md`.

## `tools/sbe_authority_hook.py`, the guard beside `sbe task`

`sbe task` proves scope AFTER the fact, at `close`. `tools/sbe_authority_hook.py` is the same
question asked BEFORE the write, for the narrower set of files that can grant authority rather
than every file a task might own: CLAUDE.md, `.claude/**`, `.mcp.json`, `.claude-plugin/**`,
`hooks/**`, `agents/*.md`, `skills/*/SKILL.md`, `CODEOWNERS`, `.github/workflows/**`, the same
nine families `tools/sbe_instruction_surface.py` reads after a commit lands. It is a Claude
Code PreToolUse hook, wired in `hooks/hooks.json` beside `tools/sbe_fence_hook.py`, never
replacing it: the fence hook enforces "one writer per file" against a hand-written registry for
every fenced file, this one enforces "an authority file moves only inside a task's declared
`ownedPaths`" against `.sbe/tasks.json`, for authority files only.

It refuses a write only when all three are true: the target resolves (symlinks followed,
case-insensitive-filesystem collisions confirmed by the same method
`tools/sbe_fence_hook.py::paths_overlap` uses) to one of the nine authority families, no OPEN
task's `ownedPaths` declares that path, and a worker context is detectable, either an open task
existing at all or a linked git worktree. Every other condition (an absent or unreadable
registry, an unimportable helper, a malformed hook payload, no worker context detected) FAILS
OPEN and prints why; this is the one control in this project's hook layer that fails CLOSED,
and only for that one rule. The refusal names the open task state (or that none is open), the
path, and the recovery: open a task record naming this path, or edit outside a worker context.

```
python3 tools/sbe_authority_hook.py surfaces .
```

prints, on stderr, the open tasks and the worker-context signal this hook would enforce from a
directory, the same diagnostic role `sbe_fence_hook.py fences` plays for the fence registry.
`BROTHERSBE_AUTHORITY_HOOK_OFF=1` turns enforcement off for a session and says so on stderr on
every write, so the bypass is never silent. Limits in full, including exactly what "worker
context" can and cannot detect: `docs/KNOWN-LIMITS.md`. Maturity: **INTERNAL-EVAL**.

## `sbe handover`, and why a chat message is not a handover

```bash
bin/sbe handover prepare design/my-change --outgoing "Alice <alice@example.com>" \
  --receiver "Bob <bob@example.com>"
bin/sbe handover show design/my-change              # human-readable
bin/sbe handover show design/my-change --json
bin/sbe handover acknowledge design/my-change --receiver "Bob <bob@example.com>"
bin/sbe handover reject design/my-change --receiver "Bob <bob@example.com>" \
  --reason "tests are still red"
```

Ownership transfer is complete only after a named human receiver explicitly
**acknowledges** it. A completion message in chat is not evidence: nobody can `sbe status`
it, and "I told them" cannot be checked later. `prepare` writes one artifact,
`12-handover.json`, inside the dossier, bound to the commit it ran at; the **outgoing owner
stays the owner** until `acknowledge` succeeds, and rejecting keeps ownership with them too,
with the reason on record.

`prepare` asks only for what the engine cannot know: `--outgoing` and `--receiver`
(an identity or a role). Everything else is derived from state other commands already
recorded, the same stores `sbe status --team` reads:

- **done / inFlight / notStarted**: from the dossier's `08-plan.json` task graph
  cross-referenced against `.sbe/tasks.json` (an open record makes a task inFlight; a
  closed, not-`forced`, record makes it done; a `forced` close is a disposition, never a
  completion, and stays notStarted).
- **activeTasks**: full ownership detail (id, agent, role, worktree, base commit) for
  every inFlight task.
- **worktrees**: the repository root plus every distinct worktree an active task
  declares, each checked for uncommitted state through `evidence.working_tree_dirty` and
  named through the same `-uall` porcelain read `sbe task close` and `sbe work remove`
  already use. The handover file and its own lock sidecar are exempted by exact name from
  this comparison, the same way `.sbe/tasks.json` is exempted from `sbe task close`'s
  postcondition: preparing a handover must never report itself as the dirty state.
- **evidence**: one array covering everything the spec calls evidence, tagged by `kind`
  (`receipt-store`, `receipt`, `convergence`, `approval`, `review`), each `current`,
  `stale` (bound to a commit that has since moved, or a covered file that changed) or
  `unavailable` (absent evidence store, unreadable review record, a verified receipt
  recording a nonzero exit code).
- **nextAction**: one deterministic sentence, priority-ordered (convergence, then
  approval, then review, then a dirty worktree, then a stale receipt, then the first
  inFlight task, then the first notStarted task, then "review and acknowledge or
  reject").

`preparedBy` is read from `git config user.name`/`user.email` in the repository, never
asked for; it falls back to `--outgoing` only when git carries no configured identity.
`openQuestions`, `decisions` and `requiredAccess` are genuinely unknowable to the engine
and are written empty; a human may add to `requiredAccess` by hand (or a future tool may),
and a non-empty one **blocks acceptance**, naming every outstanding item, until it is
cleared.

**Identity comparison is not reinvented.** Receiver-versus-outgoing-owner and
receiver-versus-registered-agent comparisons reuse `tools/sbe_gate.py`'s self-approval
machinery verbatim (`_identity_parts`, `_canonical_email`, `_names_overlap`): the same
case-fold, gmail dot-fold, initial-expansion and homoglyph resistance the approval gate
already earned. A receiver that reads as the outgoing owner is refused at both `prepare`
and `acknowledge`/`reject`; a receiver that reads as any agent identity the task registry
has ever recorded (open or closed) is refused at `acknowledge`/`reject`, never at
`prepare`, because `--receiver` there may legitimately be a role rather than a person.

**Overwrite refusal.** `prepare` refuses to silently replace an existing, still-`prepared`
handover bound to a different commit (re-run once the receiver has acted, or `reject`
first), and never overwrites an `acknowledged` handover at all: that record is a completed
acceptance. A `rejected` handover, and a `prepared` one re-run at the SAME head, are always
freely re-preparable. `prepare`, `acknowledge` and `reject` all hold one `fcntl.flock`
sidecar (`12-handover.json.lock`, the same locking pattern `tasks._registry_lock` already
uses) across their whole read-check-write, so two receivers racing to acknowledge the same
file can no longer both read `status: prepared`: the first atomic write (temp file, then
`os.replace`) wins, and the second is refused, never a torn file.

**Staleness.** `show` recomputes staleness at read time by comparing the bound `headSha`
against the current HEAD; a `prepared` record is never rewritten to match a tree that
moved on, exactly the way `11-review.json` is never silently rewritten (see `sbe review`
above). `acknowledge` refuses outright on a stale handover, naming both shas, rather than
recording an acceptance of code nobody looked at. Nothing here writes `.sbe/tasks.json`: no
task owner changes until `acknowledge` succeeds, and this module only ever reads that
registry.

Exit codes: `0` on success (including a `show` of a missing handover, which is NO-DATA, not
a failure: absence never blocks anything when ownership is not changing), `1` a refusal
(stale, overwrite, identity, missing access, malformed file), `2` usage (a bad flag, a
missing `--reason` on `reject`, a blank `--receiver`). Maturity: **INTERNAL-EVAL**.

## `sbe adopt`, and the line it will never cross

```bash
bin/sbe adopt .                 # dry run (the default): every proposal as a unified diff, writes nothing
bin/sbe adopt . --apply         # writes what was proposed; never overwrites an existing file
bin/sbe adopt . --apply --force # overwrites an existing file that differs from the proposal
```

Detects the stack by walking the tree (languages by extension, a migrations directory, dbt
models, API contract files, existing CI workflows), reusing the SAME path patterns `sbe impact`
already carries (`brothersbe.impact.DETECTORS`), so a pattern that means "this is an OpenAPI
document" to one tool means the same thing to the other. From that it proposes a provisional
`.brothersbe/policy.json` (wave 3's own repository policy schema has not shipped; this is a
smaller shape built from what `sbe adopt` can detect, and the file says so) and a
`.github/CODEOWNERS` generated from that same policy, protecting the manifest, the hooks, this
repository's own policy and config, where the evidence schema is declared, product and consumer
CI, and release files. Both proposals are **deterministic**, which is what makes a second
`--apply` a no-op: nothing about them changes between two runs over an unchanged tree.

The adoption report also names three protections that live on GitHub, not on a filesystem:
branch protection, required status checks, and whether review from a code owner is *required*.
**None of the three can ever read `PRESENT` from this command.** They report `UNVERIFIABLE-HERE`
unconditionally, naming what checking them for real would take (a GitHub token with repo scope,
plus admin rights), because this tool holds no credentials and asks for none. A CODEOWNERS file
merely *existing* in the tree is a separate, locally-checkable fact under `localFacts`, never
folded into a claim about GitHub's settings. `tools/test_sbe_adopt.py`'s kill-criterion fixture
pins exactly this: a report that ever claims one of the three PRESENT from a local read is worse
than the refusal this command used to print instead of a result.

Full checklist of what only a human with admin rights can turn on: `docs/ADOPTION.md`. Limits in
full: `docs/KNOWN-LIMITS.md`. Maturity: **INTERNAL-EVAL**.

## `sbe review --write --findings-json`, and the structured findings a review record can carry

```bash
bin/sbe review design/chg-a --write --reviewer "Independent Reviewer" \
  --reviewer-type human --result approved \
  --findings-json review-findings.json     # optional; normalizes and dedupes findings
```

CR-09 gave every review a durable record, `11-review.json`, bound to the reviewed commit:
reviewer, reviewer type, result, the raw FAIL/WAIVED verdict lines the run printed, and any
accepted risks. LT-202 adds ONE more field pair to that record, additively: `--findings-json
<path>` reads a JSON file, a list of findings in this minimal shape, and persists the
normalized, deduplicated result as `structuredFindings`, next to `findingsSchemaVersion`
(currently `"1.0"`), the explicit tag a reader checks before trusting the shape of that list:

```json
{
  "reviewer": "backend-reviewer",
  "category": "idempotency",
  "severity": "critical",
  "confidence": "high",
  "introducedByChange": "yes",
  "location": "src/api.py:123",
  "failure": "A retried request can create two orders.",
  "evidence": ["tests/test_orders.py::test_duplicate reproduces it"],
  "verification": "pytest tests/test_orders.py -k duplicate",
  "status": "open",
  "disposition": null
}
```

`fingerprint` is never supplied by the caller: it is computed, deterministically, from
`category`, the normalized path half of `location`, the line-or-symbol half, and a slug of
`failure` (its failure class), so the same finding always fingerprints the same way regardless
of which reviewer reported it or in what order. `confidence` is one of `high`, `medium`, `low`.
`introducedByChange` is one of `yes`, `no`, `unknown`. `status` is one of `open`, `fixed`,
`accepted`, `rejected` (`arbitration` is reserved: only deduplication assigns it, never accepted
as input). One conceptual issue spanning several lines is written as several entries sharing one
`conceptId` (each with its own `locations` list) instead of one `location`; they fold into a
single parent finding whose `locations` names every line.

Rules a raw finding is refused for failing, before anything is written (the same "a refused write
leaves no partial record" law `--write` already keeps for a missing `--reviewer`, so a `--write
--findings-json` run that fails validation writes NOTHING, never a half-populated record):

- `status: "accepted"` needs `disposition.by` (a named human), `disposition.reason` and
  `disposition.scope`, and `disposition.by` may never equal the finding's own `reviewer`: a
  reviewer can never accept its own risk;
- `status: "rejected"` needs `disposition.evidence`, the refuting evidence;
- `status: "fixed"` needs a verification command (`verification`, or `disposition.verification`)
  or a linked receipt (`disposition.receipt`): no finding marks itself fixed without proof.

Deduplication, applied once per `--findings-json` run:

- identical fingerprint folds into one finding, `sources` naming every reviewer that reported it,
  `reviewer` staying the first for a plain single-source reader;
- a severity disagreement within a fold keeps the highest severity and sets
  `severityDisagreement` rather than silently picking one;
- confidence within a fold is the HIGHEST any single source already claimed on its own, never
  boosted past that by how many sources agree: two `"low"` reports of the same finding never
  become `"medium"` by vote count alone;
- a status disagreement that mixes two or more of `fixed`/`accepted`/`rejected` within one fold is
  never auto-resolved: `status` becomes `"arbitration"` and `contradiction` carries every source's
  own status, evidence and disposition, in the adjudication protocol shape below, for a human or
  Fable to resolve instead.

Each stored finding also carries a computed `blocking` boolean, LT-202's own blocking rule stated
once rather than re-derived by every reader: pre-existing findings (`introducedByChange` is not
`"yes"`) never block; `confidence: "low"` never blocks, at any severity, because a model-only
low-confidence finding cannot block a merge, taken here at its most conservative since no field
records which findings are human-sourced; only `severity: "critical"` can ever block; and a
critical finding blocks only with `confidence: "high"` or a non-empty `verification` command
standing in for LT-202's "mechanical proof". A critical finding that clears neither bar stays
recorded, just not blocking.

### The stored `result` is DERIVED, never the reviewer's claim taken on faith

The moment `--findings-json` is also given, the `result` a record stores is computed FROM
`structuredFindings`, not copied verbatim from `--result`: a review record used to be able to
assert `approved` while its own findings recorded an open, blocking problem, and every reader of
`result` (`sbe status --team`'s pass/fail judgement, `sbe status`'s plain review-ladder check) took
that claim at face value. The rule is the one bar this schema already computes, never a second,
competing one: any finding still `status: "open"` AND already marked `blocking` forces the stored
`result` to `"changes-required"`, regardless of what `--result` said. A finding left `"arbitration"`
by a status disagreement never forces one either, because `blocking` is already `false` for it (see
above); an unresolved contradiction between reviewers stays its own NO-DATA, read at `sbe status
--team` time, not silently turned into a verdict here.

The derivation only ever moves a result DOWN, from `"approved"` toward `"changes-required"`; it
never moves one up. An absence of open blocking findings is not proof the other way, that a
reviewer's own `"unverifiable"` or a hand-entered `"changes-required"` for a reason this schema
cannot see should be overridden to `"approved"` because nothing here happened to block. With no
open blocking finding, `result` is exactly what `--result` said.

The reviewer's own claim is never discarded: it is kept verbatim in a new `rawClaim` field, and a
new `resultDisagreement` boolean states outright whether `result` and `rawClaim` differ, so a
reader never has to notice a self-contradicting record by diffing two fields on their own. A write
that lands a disagreement also says so on stdout, immediately, in the same sentence that already
names where the record was written. Both new fields are written ONLY alongside `structuredFindings`
(the identical `--findings-json` gate `findingsSchemaVersion` already uses): with no structured
findings there is nothing to derive a result FROM, so a plain `--write` with no `--findings-json`
still writes exactly what it always wrote, `result` included, byte-for-byte.

Because `result` itself carries the derived value, and every existing reader already reads that one
key, `sbe status --team`'s severity-11 finding and `sbe status`'s own review-ladder check both see
the derived result automatically, with no change needed on the read side: THE RECORD NEVER JUDGES
ITSELF remains true (`_record_review`'s own module law) because the judging is still `_derive_
review_result`, a pure function of the findings already on the record, computed once at write time,
not a verdict `sbe status` invents at read time either.

```bash
bin/sbe review design/chg-a --write --reviewer "Independent Reviewer" --reviewer-type human \
  --result approved --findings-json review-findings.json
# if review-findings.json carries an open, blocking finding, 11-review.json stores:
#   "result": "changes-required", "rawClaim": "approved", "resultDisagreement": true
```

`sbe status --team` reads `structuredFindings` back, inside the existing severity-11 "review
record" section, as one further finding beside the record's own pass/fail judgement: absent
`structuredFindings` (every record CR-09 wrote before LT-202, and any LT-202-era record written
without `--findings-json`) reads NO-DATA, stated honestly rather than invented; a
`findingsSchemaVersion` this installation does not recognize, a `structuredFindings` value that
will not parse as a list, or any one entry missing a required field or carrying an enum value
outside this schema is FAIL, named by the entry's own index, never silently dropped as though it
were merely absent; a record that reads clean states its counts, exactly the way the record's own
pass finding already states its finding and accepted-risk counts.

### The adjudication protocol, as a DATA SHAPE (LT-202.B)

When deduplication marks a finding `"arbitration"` (or a human reviewer is otherwise weighing
reviewers who disagree), the disagreement is recorded in this shape. This is a DATA SHAPE for a
human or Fable to fill in and paste into the review, not a new agent and not a tool this
repository runs for you:

```text
Disagreement:
Finding:
Evidence for:
Evidence against:
Recommendation:
What would falsify the recommendation:
Decision owner:
Result: accepted | rejected | needs human decision
```

Fable may draft every line above except one: `Decision owner` and a `Result` of `accepted` on a
business-risk acceptance belong to the named human alone. The same rule the write-side validation
already enforces mechanically for `status: "accepted"`, a reviewer can never accept its own risk,
holds here too, only unenforced by a schema check: Fable may not resolve a business-risk
acceptance on the human owner's behalf.

## `sbe program`, the program-wide ledger and its board

```bash
bin/sbe program status .                      # renders the program report (gantt, finished, in
                                               # flight, still to do, blocked, risks, docs, budget)
bin/sbe program status . --json               # the same report, machine-readable
bin/sbe program status . --write              # regenerates program/STATUS.md between its markers
bin/sbe program check .                       # exits 1 when the committed STATUS.md drifted
bin/sbe program board . --out board.html      # WRITES one self-contained HTML board
```

Reads only `program/PROGRAM.yaml` and `program/work-items/*.yaml`, never source code, never git,
never a suite run: if a truthful answer needs any of those, this command stops and says so rather
than inventing one. A status word never becomes a percentage; progress is `declared`
(`percent_complete`), `derived from acceptance` (`acceptance_met` against `acceptance`), or `not
measured`, and every aggregate names which of the three it used. An item naming a `wave` the
record itself never declared renders anyway, under its own value, and is also named in
`undeclaredWaves` rather than silently dropped or silently accepted.

`board` is the one subcommand here that WRITES: it renders the identical ledger to one
self-contained HTML file at the path named by `--out` (`--out` is required), inline CSS only, zero
network references, readable in light and dark. Per loop it shows the loop's name, and per item its
title, its recorded status, a tick per `acceptance` criterion `acceptance_met` names as met, and
its recorded `evidence` lines; a field the ledger never recorded renders the literal text NO-DATA,
never a blank and never a guess. It refuses rather than overwriting a file at `--out` that already
exists and does not carry the marker this command stamps on every board it writes, so a board can
be regenerated in place but a file this command never wrote is never silently replaced.

## `sbe status`, and the rule that keeps it from becoming a second gate runner

```bash
bin/sbe status .                 # blocker-first summary of the current repository
bin/sbe status . --base main     # diff-derived sections read against a stated base
bin/sbe status . --json          # the same six sections, machine-readable
```

One truthful, blocker-first answer to "where does this change stand", read from state
other commands already recorded. It **never runs the suites itself**: nothing in this
command starts a subprocess, and nothing in it computes a new verdict over source code.
It reads `sbe evidence` receipts, the `sbe task` registry, an intake file, a disposition
file, and the diff (the same way `sbe impact` already reads it, by calling that module
rather than re-deriving the git plumbing). If a truthful summary could not be produced
without running the suites or becoming a second `sbe verify`, this command would refuse
rather than run them; it does not need to, because every finding below is something
recorded state already answers.

Six sections, blocker-first, and **every positive or empty line names what it inspected**:

1. **BROKEN CLAIMS**: an evidence receipt under `.sbe/evidence/` that fails `sbe evidence
   verify` (stale, wrong commit, malformed), and a disposition bound to a commit that is
   not the current HEAD.
2. **MERGE BLOCKERS**: an intake tier disagreeing with the diff-derived tier `sbe impact`
   proposes, with no disposition recorded; an intake that cannot be read; a task closed
   `--force`d; and a receipt that verifies as trustworthy but recorded a nonzero exit
   code, meaning a check actually ran and failed and evidence of that failure already
   exists.
3. **ACTIVE CONFLICTS**: open tasks in `.sbe/tasks.json` with overlapping owned paths,
   read by calling `tasks.load_registry`, `tasks.open_tasks` and `tasks.claims_overlap`
   directly, the same functions `sbe task check` itself runs; there is no second copy of
   the overlap rule here.
4. **MISSING EVIDENCE**: for a declared tier above T0, a design/gate/score kind no
   verified receipt DECLARES in its own `checkKinds` field, each naming the command that
   would fill it. Receipts in the store that declare no kind are named in the same
   finding, so a reader is never told an obligation is unmet without being told that
   evidence exists which says nothing about which check it was.
5. **COMPLETED EVIDENCE**: receipts that verify clean with a zero exit code, printed with
   their trust label (`LOCAL-ADVISORY` or `CI-CLAIMED`) every time.
6. **NEXT ACTION**: one line, derived mechanically from the first nonempty section above,
   plus the scope sentence naming exactly which stores this run read.

Where it looks, and this is stated rather than left to be discovered: the flat
single-dossier conventions first (`<path>/00-intake.json`, `<path>/disposition.json`,
`<path>/.sbe/evidence/` recursively, `<path>/.sbe/tasks.json`, the same ones
`tools/test_sbe_impact.py`'s own fixtures write to), and when no flat intake exists,
the dossier layout through the same walker the team report uses (`design/<change>/`
plus any `designRoots` the team profile declares, escapes refused by name). When both
layouts exist the flat one wins and the report says so. A repository with neither
still reads NO-DATA per section rather than guessing at a path it was never told.

A design/gate/score obligation is cleared only by a receipt whose `sbe evidence verify`
verdict is PASS (sealed, current, every covered file intact) AND which declares that kind
in its own `checkKinds` field, written by `sbe evidence run --kind`. A nonzero recorded
`exitCode` on such a receipt is the MERGE BLOCKER, declared kind or not: the run was made
and it failed.

**Nothing here reads the command line to decide which check ran, and that is a fix, not a
preference.** It used to: the kind was inferred by substring-matching the joined `argv`
(`verify` counted as all three, `review` as gate and score), so a receipt recording
`/bin/cat tests/test_design_of_gate_score.txt` cleared the design, gate and score
obligations at once, on a command that ran no check. A receipt that declares no kind is
NO-DATA for obligation purposes, never a silent pass, and each shape of that (a receipt
older than the field, a field that does not parse as a kind list, an honest run that
declared nothing) is named in the MISSING EVIDENCE finding rather than dropped. The limit
that remains, stated rather than papered over: a declared kind is the operator's
statement, bound to a real run and sealed against later editing, and not a proof that the
command performs the check it names.

Exit codes: `0` when BROKEN CLAIMS, MERGE BLOCKERS, ACTIVE CONFLICTS and MISSING EVIDENCE
are all empty, `1` when any of them carries an item, `2` usage. Exit 0 is never printed as
a claim that everything was inspected; the closing line and every empty section's NO-DATA
line say what was not. Limits in full, beside the behavior, live in this section rather
than `docs/KNOWN-LIMITS.md`, which this wave did not touch. Maturity: **INTERNAL-EVAL**.

## `sbe init`, and the idempotence it is built on

```bash
bin/sbe init .                        # dry run (the default): every mutation as a diff, writes nothing
bin/sbe init . --apply                # writes: config, dossier directory, install receipt
bin/sbe init . --apply --with-consumer-ci  # also copies the consumer CI workflow and action, only when asked
```

Writes `.brothersbe/config.json` (the schema version, tool version, and the dossier root,
`design/`), a `design/.gitkeep` marker so the dossier directory is trackable before it holds
anything, and, only with `--with-consumer-ci`, a copy of this installation's own
`.github/workflows/consumer-check.yml` and `.github/actions/sbe-consumer/action.yml`. Refuses
outside a git repository, naming the reason: there is nowhere for the config, the dossier
directory or the receipt to be versioned.

It also ensures `.gitignore` carries one line, `.brothersbe/install-receipt.json`, under a
one-line comment explaining why: the receipt records this machine's absolute install path, a
local, personal fact with no business being tracked. That mutation is **appended, never owned**:
`sbe init` reads whatever is already in `.gitignore` and adds the comment and the line only
when they are missing, leaving every other line in the file untouched. Present means untouched,
the same idempotence rule as everything else here, and dry-run shows it as a proposed diff like
every other mutation.

Every proposal is deterministic content, compared byte for byte against what is already on disk,
which is what makes **running it twice under `--apply` change nothing the second time**: the
second run finds every proposal already matches and writes nothing, and the install receipt
(the one file that legitimately carries a timestamp) is left untouched rather than rewritten,
because nothing happened this run for it to describe. When something IS written, `sbe init`
writes or refreshes `.brothersbe/install-receipt.json`: the schema and tool version, when it
ran, every path it has ever written (across this call and any prior one), and exact uninstall
instructions, `rm -f <path>` for each, printed at the end and saved into the same receipt.
`.gitignore` is the one exception to that written set: it is written like any other proposal
when its line is missing, but it is never listed in `writtenPaths` or the uninstall
instructions, because `sbe init` only appended a line to a file it does not own, and an
uninstall instruction of `rm -f .gitignore` would delete every other line a real project keeps
in that file.

Limits in full: `docs/KNOWN-LIMITS.md`. Maturity: **INTERNAL-EVAL**.

## `tools/sbe_telemetry.py` data commands: `data-show`, `data-export`, `data-purge`

```bash
python3 tools/sbe_telemetry.py data-show
python3 tools/sbe_telemetry.py data-export [--out PATH]
python3 tools/sbe_telemetry.py data-purge [--category NAME] [--yes]
```

These three are not on `sbe`, and they are not going to be. `sbe` is the facade in front of the
assurance tools in the table above (`design`, `gate`, `score`, `intake`, `decide`, `verify`, `review`,
`impact`, `status`, and the rest): commands a CI job or a reviewer runs routinely, over a directory,
on somebody else's schedule. `data-show`, `data-export` and `data-purge` read and delete what
BrotherSBE has captured about the operator's own sessions, ratings, and messages. That is a privacy
surface, not an assurance surface, and folding it into a menu a script tabs through would make
"delete what was captured about me" one habitual keystroke away instead of something a person runs
deliberately, by name, on purpose. They live only on `tools/sbe_telemetry.py`, invoked directly, the
same way `intent`, `rate` and `purge-corrections` already do.

All three read the SAME inventory. The tool's own comment states the invariant: "a file that
`data-show` lists is a file `data-export` copies and `data-purge` removes," built from this module's
path constants plus a glob of the telemetry directory, so a per-project file cannot be listed by one
of the three and missed by the other two.

**Where the vault is:** `BROTHERSBE_VAULT`, default `~/BrotherSBEVault`. The telemetry directory
these three commands read and write is `<vault>/99-System/telemetry`.

### `data-show`

Read-only, never writes. Prints the capture policy in force for each category (`corrections`,
`metrics`, `transcript`, gated respectively by `BROTHERSBE_TELEMETRY_CORRECTIONS`,
`BROTHERSBE_TELEMETRY_METRICS`, `BROTHERSBE_TELEMETRY_TRANSCRIPT`, every category off by default),
then every file the tool can write: category, path, record count, byte size, file mode, and what it
holds, or "absent, so nothing is stored at this path" for one that does not exist. `-h`/`--help`
anywhere in its argv prints usage and exits 0 instead of running; any other flag is refused
nonzero rather than ignored. Real
output against this repository's own vault, paths abbreviated below, capture off:

```
BROTHERSBE STORED DATA (vault ~/BrotherSBEVault)
  policy: corrections capture is off: BROTHERSBE_TELEMETRY_CORRECTIONS is not set, and every category is off by default
  policy: metrics capture is off: BROTHERSBE_TELEMETRY_METRICS is not set, and every category is off by default
  policy: transcript capture is off: BROTHERSBE_TELEMETRY_TRANSCRIPT is not set, and every category is off by default
  [metrics] .../telemetry/outcomes.jsonl: absent, so nothing is stored at this path
  [metrics] .../telemetry/ratings.jsonl: absent, so nothing is stored at this path
  [metrics] .../telemetry/reviews.jsonl: absent, so nothing is stored at this path
  [corrections] .../telemetry/corrections.jsonl: absent, so nothing is stored at this path
  [housekeeping] .../telemetry/installed-skill-version-brothersbe: 1 record(s), 41 bytes, mode 644 -- the git sha of the installed skill at the last check
  [autosave] .../telemetry/autosave.log: 5 record(s), 1408 bytes, mode 644 -- one line per autosave snapshot, skip or lock event
  [autosave] .../telemetry/autosave-exclusions.log: 5 record(s), 487 bytes, mode 600 -- paths the autosave content scan kept out of a snapshot, and why (paths and reasons only, never the matched content)
read 3 file(s), 4 path(s) absent, 0 that could not be measured, under .../telemetry.
This lists this vault only. A backup, a mirror or a sync client may hold copies of any of it, and nothing here can see those.
```

### `data-export`

Writes ONE owner-only JSON bundle, file mode 600, named `brothersbe-telemetry-export.json` in the
current directory by default, `--out PATH` to name another location. The bundle carries the actual
file CONTENT for every path in the same inventory `data-show` lists, not just the counts, so it
"holds the stored data itself" in the tool's own words, and it says so on every run: "treat as
sensitive; redaction was applied at capture time and is best effort." This is how a person gets their
own captured data out of the vault to read somewhere else, not a routine export a script should
schedule. `-h`/`--help` anywhere in its argv prints usage (what it reads, what it writes, its
flags) and exits 0 without writing a bundle; any other unrecognized flag is refused with usage and
a nonzero exit rather than ignored, so a typo can no longer run a real export.

### `data-purge`

Deletes what is stored. Same dry-run shape as `sbe adopt` and `sbe init`: prints the inventory that
currently exists on disk and stops there unless `--yes` is also given. With `--yes`, it deletes each
file, then RE-CHECKS the filesystem afterward and reports removed / failed / still-present counts,
because, in the tool's own words, "a purge that reports success from its own intention has proven
nothing." `--category NAME` narrows the purge to one category.

Categories are not a fixed enum. They are read from whatever `data-show`'s own inventory currently
finds on disk, so an unrecognized name is refused with the live list, not a hardcoded one. Real
output against this repository's own vault, where only `autosave`, `corrections`, `housekeeping` and
`metrics` currently have a matching file to glob:

```
data-purge: no category named 'nope'; categories are autosave, corrections, housekeeping, metrics
```

A file that fails to remove, or that still exists after `os.remove` reported success, is named on its
own line rather than folded into a clean exit. `-h`/`--help` anywhere in its argv prints usage and
exits 0 without deleting anything; any other unrecognized flag, including a typo of `--category`, is
refused with usage and a nonzero exit rather than run as if the flag had never been given.

### The one law that governs all three, and everything else on this tool

`tools/sbe_telemetry.py` never blocks work: every hook and automatic path exits 0, and an unhandled
exception is caught and printed rather than propagated ("sbe_telemetry: swallowed error (never
blocks)"). The one deliberate exception is the three data-* commands above: a bad flag on one of
them refuses with usage and a nonzero exit instead of running past it, because a mistyped flag on a
command that reads or deletes the vault must not run as if nothing were wrong. Numbers are parsed
or absent; the tool never invents one.

One command remains **present and refuses**: `exceptions`. `policy` shipped and now evaluates
`.sbe/policy.yml` against a diff (see `sbe policy evaluate` above).

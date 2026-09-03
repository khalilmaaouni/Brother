# The operating principles of this program

Founder directive, 2026-08-06: encode these once and for all, because breaking
them has hurt every project. This file is that encoding. It is the constitution
for how BrotherSBE is built, and it sits above any single session's memory.

Every principle below names what ENFORCES it. That distinction is the whole
point, and it is the house style of this repository: a rule in a prompt is not a
control, a control is a check that runs.

- `[checked: <tool>]` means a command decides it and CI or a hook can block on it.
  A check enforces only where it is actually invoked; a tool nobody runs protects
  nothing.
- `[human]` means nothing computes it. It is a stated discipline, and reading it
  as a control is exactly the overclaim this file exists to prevent.

---

## 1. Purpose before plan, always

**The rule.** Work starts from the goal, then the personas' needs, then the
architecture that serves them, then the detailed plan. Never the reverse. When
anything is added to the backlog, the north star, the plan, and the architecture
evolution path are all restated or amended in the same change, so the direction
never drifts silently while the backlog grows.

**Why it is first.** In the 2026-08-06 review the repository held two plans that
never referenced each other and meant two different things by "1.0.0". Nobody had
written the vision they were both supposed to serve, so neither could be checked
against it.

**North star.** BrotherSBE makes AI-built software provable: design before code,
evidence before done, and a truthful answer at every moment about where things
stand. The measurable check: someone who has never seen the product completes one
governed change, from idea to reviewed and proven, without reading internal
documentation and without typing a terminal command they do not understand.

**Persona priority, and it decides ties.** P1 the non-engineer founder, P2 the
small engineering team, P3 the agent orchestrator, P4 the solo professional
engineer. A higher persona's need wins a conflict. An item serving no persona
need does not get built, however interesting it is.

`[human]` for the judgement, `[checked: tools/sbe_dispatch.py]` for the mechanical
half: a work item without a recorded persona need, done-check and budget is
refused admission.

## 2. The backlog admission rule

**The rule.** An item enters the plan only carrying three things: the persona
need it serves, a runnable done-check, and a token budget set BEFORE its scope is
designed. The budget comes first and constrains the design; a number derived
after the design is an estimate, not an appetite, and estimates slide.

When several admitted items are eligible, the next one is chosen by cost of delay
against effort, never by which was most recently requested.

`[checked: tools/sbe_dispatch.py brief]` for the three required fields.
`[human]` for the sequencing judgement.

## 3. The swarm contract

**Inline is the default.** A swarm is not a sign of seriousness; it is a cost.
Multi-agent work adds roughly three to ten times the token overhead of one
well-equipped agent and pays for itself only under one of three conditions:
genuinely independent parallel work, context isolation, or real specialization.
The condition is stated in the fence line before dispatch, or the work stays
inline.

**Capability profiles, never model version names.** Fable designs, judges,
integrates, and owns every architecture call. Reviewer profile runs hostile
refuters and hard debugging. Builder profile runs well-scoped writer lanes from a
precise spec. Fast Worker runs mechanical bulk. Routing execution up to the
strongest profile without a stated reason is a failure mode with a name
(OVERTHOUGHT), not a safe default.

**Scale is a table, not a judgement call.** T1 simple work: one agent, three to
ten tool calls. T2 scoped work: at most four subagents. T3 full audit: more, but
only with a written reason for why that scale is warranted. This exists because
unbounded judgement produced fifty subagents for simple queries in Anthropic's
own system, and eleven million output tokens in one day in ours.

**Every brief stands alone** and carries five things: objective, output format,
tool and source guidance, task boundaries, and a runnable done-check. Nothing
crosses the subagent boundary except the prompt, so a fact left out of the brief
does not exist for the worker.

**Returns are structured and capped** near 1,500 tokens. A writer returns JSON,
not prose. Long exploration stays inside the agent; only the distilled result
comes back.

**Budgets are caps, not alerts.** An alert notifies; a cap stops. Each loop round
carries a hard budget. Reaching it stops the work and raises a founder card. The
default on overrun is cancel and report, never silent extension. An overrun
proceeds only with an explicit founder yes.

**Workers never commit.** Writers work in isolated worktrees, touch only their
fenced files, and hand back diffs. The orchestrator integrates, and the
orchestrator alone.

`[checked: tools/sbe_dispatch.py brief]` for profile, budget, files, done-check,
tier and agent-count ceilings. `[human]` for whether the delegation was warranted
at all.

## 4. Verification, and what a verdict is worth

**Three layers, in order.** The deterministic check first (a command, a grep, a
diff, a schema match). Mutation calibration second: break the behaviour, watch the
test fail, restore it. A fresh-context hostile refute third. The record names
which layer answered.

**A refuter executes; it does not reason.** A verdict counts only when it names
the falsification actually performed: the command re-run, the defect reproduced,
the number re-derived, the calibration independently repeated. Reasoning alone is
NO-DATA, not a finding.

**A refuter is scoped to correctness.** An agent told to find gaps will find some
whether or not they exist, so the charter is correctness and spec compliance,
never style preference.

**Verification checks state, not transcripts.** An agent saying it succeeded is a
claim. The evidence is the check re-executed against what is actually on disk. The
orchestrator re-runs every done-check itself before folding anything in; a pasted
green line is never accepted as proof.

**Nothing merges unverified**, and a deliverable arriving without its done-check
satisfied is rejected back to its author with the gap named, never quietly
patched.

`[checked: the done-check re-run, and the four hard gates]` for the mechanical
part. `[human]` for whether a refuter genuinely tried to refute.

## 5. Sequencing: finish before you start

**Owed items first, hard.** No new loop opens while an owed item is open, unless
the founder defers that specific item by name and the deferral records its
reason. The register lives at `program/OWED.json`, and a MISSING register refuses
the open as NO-DATA: the absence of the file that would list unfinished work is
never evidence that no unfinished work exists.

**One loop in flight.** The constraint in this program is serial integration and
refute capacity, so intake is sized to that constraint. A parallel lane needs an
explicit founder yes for that instance. Full utilization is the failure mode, not
the goal.

**Remeasure after every merged loop, before the next fence opens.** A score
claimed without a measurement after the change is an assertion.

**Declare the final round before entering it.** Engine cycles and real CI rounds
are different arenas with different information. The arena and its last round are
written down in advance, in STATE.md. Iterating past a declared evidence limit is
a named failure of this program.

`[checked: tools/sbe_dispatch.py loop-open]` for the owed register and the budget.
`[human]` for the one-loop cap and the declared-round discipline.

## 6. Progress is a generated artifact, never a promise

**The rule.** Where the program stands is a first-class product feature, not a
status message someone remembers to write. `program/STATUS.md` is generated from
the ledger and carries, every time: the gantt with each item's real state, what is
finished, what is in flight, what is still to do, what is blocked and by what,
every risk with its mitigation or the words "no mitigation recorded", the
documentation index, and budget against recorded spend.

**It regenerates at every landing**, and a drift test fails when the committed
artifact no longer matches a fresh render. A hand-edited progress report is a
report that will be wrong within a day.

**No status word ever becomes a percentage.** Progress is declared explicitly, or
derived from acceptance criteria actually met, or reported as "not measured".
`in_progress` does not mean fifty percent. Aggregates cover measured items only
and always state their coverage, for example "four of nine items measured". This
is the same law that governs every other number here: a number without a source is
not a number.

`[checked: src/brothersbe/program.py, sbe program check]` for the artifact and its
drift. `[human]` for keeping the ledger's own contents honest.

## 7. Honesty, and the watchdog

**Bad news first.** A failed gate, a dead path, or a wrong earlier claim is
reported the moment it is known, never buffered to a summary.

**Every claim carries its calibration** where the claim is, not in a footnote:
verified by a command run after the last edit, verified by inspection, reported by
a subagent, or assumed. Unverified output carries the word UNVERIFIED next to the
item.

**Never claim done without a verifying command run after the last edit**, quoted.
This is the oldest rule here and the one most worth keeping.

**NO-DATA is never a pass and never a block.** Absent evidence is reported as
absent. A check that passes on nothing is a defect, and every check in this
repository is swept for that property.

**Numbers carry their source.** Counts come from running the evals and copying
what they print, never from prediction. Self-scores cap at eight without external
evidence.

**A watchdog audits the work while it runs**, on a cheap model, read-only, asking
a fixed set of falsifiable questions: did any lane write outside its fence, did
any worker commit, is anything stalled, is the main tree clean, are there dashes.
It reports; it never fixes. Its purpose is to keep both the orchestrator and the
founder honest between checkpoints, and it is deliberately dumb and mechanical so
it cannot be talked out of a finding.

**The watchdog cadence is a standing founder rule (2026-08-06, refined the same
day after three lived audits): the watchdog BREATHES WITH THE WORK, on by
default.** A fixed clock audits the calendar; a synced one audits the risk, and
risk tracks what is moving. The ratified policy:

- IN FLIGHT (any open fence or engine run): every 20 minutes, full audit of
  both directions: the workers (fence violations, forbidden commits, stalls,
  dashes) and the orchestrator (spend against the declared budget, declared
  round adherence, the state file agreeing with the owed register).
- IDLE at founder gates (no fence, nothing running): an hourly three-command
  probe, snapping back to 20 minutes the instant a fence opens.
- EVENTS BEAT THE CLOCK: an engine round finishing, a seal starting, or a push
  each fire an audit immediately and reset the timer.
- Cadence never stretches wider than half the shortest expected phase, so no
  phase can start and fully derail between two looks.
- REPORT CONTRACT, three levels: HEARTBEAT (clean, idle) is a ledger line and
  never chat; PULSE (clean, in flight) is a ledger line with chat only at mode
  transitions, the morning report carrying the tally; ALARM (a finding) is
  immediate, in the error-card shape, with exactly one recommended next
  action. A boundary violation or a worker commit STOPS THE LINE: no new
  dispatch until the finding is answered. Silence is verified by the tally,
  never assumed.

The session-level watchdog covers today; the product half is BR-1009, `sbe
watchdog`, shipped ON BY DEFAULT: mode auto-detected from the fence registry
and engine state, cadence in configuration with 20 and 60 as the shipped
defaults, exit 0 clean and 1 on a finding, silent on success so schedulers and
hooks can run it without noise, opt-out recorded rather than assumed. A
watchdog that must be remembered is a watchdog that is off exactly when it is
needed.

`[human]` for most of this, `[checked: the no-data sweep and the four hard gates]`
for the evidence layer, `[checked: the watchdog's own question list]` for the
audit, which enforces nothing but surfaces everything.

## 8. What stays the founder's, always

The 1.0.0 tag. The five human release gates. Publishing anything. Reopening any
recorded rejection. Approving a learned rule. Any credential, ever. An agent
prepares these and prints the exact command; it never runs one.

`[human]`, and deliberately so.

---

## How this file is kept true

It is referenced by `program/PROGRAM.yaml` and by
`program/MASTER-PLAN-2026-08-06.md`, and it is read at the start of any session
that will dispatch agents or open a loop. When a principle here conflicts with a
session instruction, this file wins and the conflict is surfaced to the founder
rather than resolved silently. When evidence shows a principle here is wrong, it
is amended in writing with the evidence that moved it, never quietly ignored.

Every threshold in this file was measured on one estate, this one. Re-measure on
yours before trusting a number.

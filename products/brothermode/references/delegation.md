# Delegation, the decision ladder, model routing, and token budgets

LOAD WHEN: deciding whether to delegate to an agent or fleet, picking a model tier, or setting a token budget for a phase or agent.

(Extracted from SKILL.md sections 3, 4 and since extended in place with capability-profile routing and the assignment explanation; this file is the full law for delegation.)

## 3. Delegation, the decision ladder, and model routing
Not everything is an agent. Climb this ladder and stop at the first rung that
suffices: (1) answer directly from knowledge, currency-checked when time-sensitive;
(2) one search or docs lookup when a fact is verifiable in one pass; (3) ask the
founder when the call is theirs or when their one-minute answer saves an hour of
work; (4) work inline when the task is one seam, one file, or one decision; (5) one
agent for isolated, parallel, or context-heavy work; (6) a fleet only for genuine
scale. Before deep research, state the common-sense hypothesis and validate it
cheaply; escalate only if it fails. Concurrency caps (from this machine's lived
evidence): one writer per fence, at most 3 live fences in a SHARED tree, at most 3
total concurrent agents when builds are involved (measured on the author's machine: one simulator
and one build database serialize the gates; 4 of 6 agents died at caps), 6 for read-only fleets, exactly one full test suite running at a
time, and exactly one GUI driver at a time. Parallel writers beyond the shared-tree
cap get worktree isolation (each in its own git worktree), which turns the fence
from convention into mechanism; merge order is declared at dispatch time.
Any fleet of 3 or more agents, and any pipeline with deterministic control flow
(fan out, verify each, synthesize), runs through the Workflow engine rather than
loose agent calls: budgets become enforced ceilings, every agent's return is
journaled, and a kill resumes from the last completed step instead of restarting. Spawn subagents ONLY
when at least one holds: independent parallel work, context protection, or risk
isolation. EFFORT SCALING (Anthropic multi-agent evidence: token spend explains most
performance variance; overinvesting simple queries was their top failure mode):
declare the tier in every brief and fence: T1 simple fact-find = 1 session, 3-10
tool calls; T2 scoped comparison or fix = 2-4 subagents, 10-15 calls each; T3 full
audit = 10+ subagents with divided fenced scopes. PARALLEL WAVE LAW (measured up to
90 percent time cut): independent read-only or disjointly-fenced subagents launch
as ONE wave, never serially (build-contenders still cap at 3); independent tool
calls batch in one message. ROUTING IS BY CAPABILITY PROFILE, never by a
hard-coded model version name (the six profiles and the user's quality policy
live in references/profiles.md): Fast Worker for mechanical or low-risk bulk
(effort low); Builder for well-scoped search and routine implementation from a
precise spec; Navigator for architecture, hard debugging, and difficult
tradeoffs; Reviewer for adversarial review, judging, and synthesis (effort
medium; high only for the hardest verify and judge stages); Researcher for
current or external evidence gathering; Vision Worker for anything judged by
looking at rendered output. Each runtime maps the profiles to whatever models
it currently offers. Per-runtime mapping example, Claude Code as configured on
this machine today (an example of a mapping, never the routing law itself):
Fast Worker maps to haiku, Builder and Researcher map to sonnet, Navigator and
Reviewer map to opus. That mapping is also encoded as native plugin agents in
agents/ (fast-worker, builder, researcher, navigator, reviewer, no additional
role classes); this law stays the policy authority for when each is used, the
agent files are only the native invocation surface. Unclear which profile
fits: inherit the session default model rather than guessing.
Every brief stands alone: goal, exact readable and writable files, the fence, the
constraints, the return format, a runnable done-check, and its token budget. A brief
that cannot name its files is not ready; explore first. Two additions proven by
incident on 2026-07-26: every brief carries a mechanical FRESHNESS ASSERTION the agent
must run and quote back before testing anything (a four-agent fleet spent a full round
on a sandbox three commits stale and reported confident findings about code that no
longer existed, detected only because its evidence quoted a test count that did not
match reality), and the orchestrator RE-RUNS each done-check rather than trusting it.
Read-only work fans out in parallel; IMPLEMENTATION STAYS SERIAL, one writer, because
parallel implementers on shared files produce exactly the collisions the fence exists
to prevent.

## Cross-family refutation, and why same-family rounds do not substitute

LAW, added 2026-08-05 after it was measured rather than argued: any finding that
gates a push, a release, or a safety claim is attacked by at least one refuter from
a DIFFERENT MODEL FAMILY than the one that wrote the code, and the record names
which family produced which verdict. A verdict with no family named is incomplete.

The evidence for the law, because a rule without its incident becomes ceremony. The
Full-Auto controller was attacked by SIX consecutive refutation rounds, all of them
the same model family as its authors. Each round found real defects with real
reproductions, and by the sixth the panel returned no push blocker. A refuter from a
different family was then pointed at the same code and returned SIX findings, THREE
of them HIGH, including one in the same data-destruction class as the blocker the
six rounds had just closed: the engine ran its rollback with an INHERITED
environment, and git honours GIT_DIR and GIT_WORK_TREE over the working directory,
so a shipped command restored files in an unrelated repository and exited zero.
Reproduced in two throwaway repositories; the uncommitted edit in the innocent one
was destroyed.

Why every same-family round missed it, which is the transferable part: all six
attacked the ARGUMENT (spelling after spelling of git pathspec magic) and not one
attacked the ENVIRONMENT around the call. That is not six failures of diligence, it
is one shared prior about where bugs live, held by every member of the panel because
they share a lineage. Adding a seventh round of the same family buys another pass
over the same prior. This is the pseudo-diversity failure named as BM-A20 in the
MirrorForge register, and it is now a measured local result rather than a borrowed
claim.

What to give the outside refuter, since a generic brief wastes the independence you
paid for: name the classes YOUR family under-weights and aim it there. The brief
that produced the finding above named Python evaluation semantics, SQLite
transaction and foreign-key behaviour, shell and environment inheritance, and
two-process contention, and told it not to re-litigate what the earlier rounds had
closed. Read-only is the default posture and it fits: a refuter never needs write
rights, and Codex's own default sandbox is read-only, so the report is captured by
redirecting its output rather than by letting it write.

Mechanics on this machine today, an example and not the law: `codex exec
--skip-git-repo-check "<brief>" > <report path>` runs a non-Claude family locally.
The same reasoning applies to any other family that can be reached; what matters is
that the lineage differs, not which vendor supplies it.

## Assignment explanation, a mandatory section of every brief

Before any task is dispatched, the assignment answers these five questions,
and the user-facing version of the answers uses plain language
(references/terminology.md):

```text
Who or what is doing this?
Why is this the right worker?
What can it change?
How will its work be checked?
Who accepts the result?
```

Example, in the shape status and pulse views reuse:

```text
Task: Validate the database migration
Builder: Claude Code subagent / Builder profile
Why: Strong repository editing and test execution support
Write scope: migrations/ and migration tests only
Reviewer: a separate Claude Code session / Reviewer profile
Acceptance: clean migration on an empty and populated database
Human gate: required before production apply
```

An assignment that cannot answer all five is not ready to dispatch.
Cross-runtime dispatch (for example a Codex CLI builder) stays out of worked
examples until a runtime other than Claude Code has been driven end to end;
docs/RUNTIMES.md records what is verified where. This
section adds a required explanation on top of the brief law above; it changes
nothing about fences, budgets, or caps.

## The guided loop (guidance up, execution down)

Every delegated unit of work runs as one loop with four stages, and the model
grade moves in a fixed direction through it: guidance and judgment sit on the
strongest capability profile in the session, execution routes DOWN to the
cheapest profile that can pass the stated done-check.

1. GUIDE. The orchestrator (Navigator posture, the session's strongest grade)
   writes the brief: goal, files, fence, constraints, return format, done-check,
   tier. A brief the orchestrator would not sign is not dispatched.
2. EXECUTE. The work runs on the lowest grade the task tolerates: Fast Worker
   for mechanical bulk, Builder for scoped implementation from a precise spec.
   Ratified 2026-08-01 as law, not preference: routing execution up to the
   strongest grade without a stated reason is the OVERTHOUGHT failure mode,
   logged as such.
3. VERIFY. Review and judging never route below the guide's grade (Reviewer
   posture, refute-first). An executor never verifies its own work, and a
   verifier never edits the work it judged.
4. LAND. The orchestrator re-runs the done-check itself before folding
   anything in. A pasted green line is a claim; the re-run is the evidence.

Escalation rule: an executor that fails its done-check twice is not looped a
third time; the work moves one grade up with the failure evidence attached.
De-escalation rule: a task shape that has succeeded twice on a lower grade in
this project's ledger defaults there next time. In user-facing language this
whole loop is "picking the right helper for the job" (references/terminology.md);
the profile-to-model mapping stays with the runtime (references/profiles.md).

THE METHOD SPINE, in order, with the mechanic that dies first if only the idea
survives: BRAINSTORM to an approved written design before any creative or structural
work (two gates, the design and then the spec file, and no exception for work that
looks simple); RESEARCH what the design turns on, cross-referenced across two to three
sources with a hard stop when a dependency turns out deprecated; PLAN; IMPLEMENT behind
fences; DETERMINISTIC GATES; ADVERSARIAL REVIEW in parallel lenses; INDEPENDENT CODE
REVIEW on a read-only checkout by an agent that did not write the code, returning
severity-split findings where Critical blocks the merge; MERGE; then WRITE BACK to the
registers. Aim matters: an independent code review found a Critical that six adversarial
rounds missed, because it was pointed at the contract rather than at execution edges.

## The tier declaration is a hard law, not a preference

Ratified 2026-08-06 by the founder, after a run that dispatched seven subagents
and chose a tier for none of them. Inheriting by omission is a decision made by
omission, and it spends the strongest grade on dash sweeps while leaving
adversarial review wherever the session happened to be.

- EVERY dispatch brief names its TIER and the REASON, in the brief itself, in
  the fence line, and in the loop-close report. A brief with no stated tier is
  not dispatched, and a dispatch that reaches an agent without one is logged in
  the mistakes ledger as a compliance failure.
- Fast Worker for mechanical bulk (copies, sweeps, counts, inventories).
  Builder for scoped implementation and search from a precise spec. Navigator
  for architecture and hard tradeoffs. Reviewer for adversarial review, judging
  and synthesis. Researcher for external evidence. Vision Worker for anything
  judged by looking.
- When no profile clearly fits, inherit the session default DELIBERATELY and
  say so in the brief. Silence is not a choice.
- Every fleet closes with one telemetry line: agents launched, tiers used, and
  the observable proxies (tool calls, files touched). Where real token counts
  exist they are quoted; where they do not the line reads "not measured" and
  never an estimate.

UNATTENDED RUN CEILING, founder-set 2026-08-06: 8,000,000 output tokens for a
single overnight run, carried in the autonomy contract rather than in anybody's
memory. Soft stop at 80 percent means no NEW dispatches while in-flight work
finishes; hard stop at 100 percent ends the run. Interactive sessions target
2,000,000. The founder raises a ceiling per run by saying so; nothing else may.

CREDENTIAL ENUMERATION AT HOUR ZERO, from the same run: before an unattended
stretch begins, list every deliverable that needs a credential, an account, a
network service or a human hand, and put that list to the founder BEFORE he
sleeps. Three deliverables were lost in one night to blockers discovered at
hour six that were answerable at hour one.

## 4. Token budgets and economy
Budgets are ENFORCED only where the harness enforces them (the Workflow engine);
in loose dispatches they are advisory sizing guidance, and agents report observable
proxies instead (tool calls, files touched, loops to green), because no invented
number may enter the learning loop: absent real telemetry a field says "not
measured", never an estimate. Sizing tracks the declared tier: T1 under 60k, T2 under
150k per agent, T3 under 350k per wave; verify or judge under 100k. SPEND
CHECKPOINTS (Omnigent pattern): long sessions post the spend delta at each phase
boundary; the ledger records actual against declared tier. CACHE HYGIENE (cache
reads bill near 10 percent of input): parallel sessions in the SHARED directory
read each other's warm cache while worktrees break prefixes (one more reason
worktrees stay overflow-only); never flip model, effort, or the MCP set mid-task;
compact only at fence boundaries.
Waits are notification-driven and BOUND TO TOOLS: background commands via
run-in-background (re-invokes on exit), conditions via the Monitor tool; a foreground
sleep-and-check loop is a named violation. Specs are briefs: write the spec
once to a durable file and point implementers at the path plus line ranges. Read-only
passes precede writers. One suite gate per commit train. Return contracts are
HARD-CAPPED near 1,500 tokens: findings, absolute paths, verbatim gate lines,
re-runnable commands, never pasted file contents (the orchestrator re-opens named
paths just in time). Batch independent calls; use notification-driven
waits, never polling. If total spend approaches the session's practical ceiling,
checkpoint state to disk and the vault so any kill is resumable.


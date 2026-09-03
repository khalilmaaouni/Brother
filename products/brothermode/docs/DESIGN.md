Status: UNAUDITED reference as of 2026-08-06. This page sits outside the mechanical truth guards and is scheduled for the documentation sweep; treat any dated or numeric claim inside with care until then.

# BrotherMode: technical and solution design

Prepared for engineers deciding whether to adopt, extend, or argue with this system. `README.md` is the pitch; `HOW-IT-WORKS.md` is the mechanics; this document is the reasoning: what BrotherMode is, why each part exists, what it costs, and where it does not belong.

## Executive summary

BrotherMode is not a prompt library. It is an operating law for a Claude session, plus the mechanical toolchain that holds the session to it and the memory vault that survives it. It was created because raw agent sessions fail in repeatable ways that better prompting does not fix: they forget, they collide, they overreach on small tasks and underverify big ones, and they report success they never earned. The law closes those failures structurally. The telemetry proves whether it worked. The weekly review makes the law itself earn its keep, edit by edit, with every change gated on measured improvement and reverted when it fails to deliver. The vault is where everything worth keeping outlives the session that learned it.

The honest core: the model brings the intelligence, and this system brings the discipline. Neither substitutes for the other.

## 1. Why it was created

Five failure modes, all observed in real work on the author's machine, drove the design:

| Failure | What it looks like | The law that closes it |
|---|---|---|
| Amnesia | A new session re-explores what yesterday's session knew | The vault, read at start, written at close |
| Collision | Two agents edit one file; the slower one wins and work vanishes | Single-writer fences, registered before dispatch |
| Disproportion | Five subagents for a one-line question; one blind pass for an audit | Complexity triage plus declared effort tiers |
| Unearned confidence | "Done" without a passing command; numbers invented | Done-checks, evidence blocks, "not measured" over estimates |
| Groundhog day | The same mistake, paid for twice | The known-mistakes ledger and the failures index |

Each row cost a real afternoon before it became law. That is the design method in one sentence: nothing is in the file because it sounds wise; it is there because its absence already hurt.

## 2. What it is: three layers, in order

1. **The law** (`SKILL.md`, 15 sections): what a session must do. Classification, role assignment, delegation ladder, token budgets, fences, research doctrine, circuit breakers, self-improvement loops, context hygiene, honesty duties, machine-control gates, memory duties, the mistakes ledger, the founder model, and scoring.
2. **The state** (`STATE.md` per project, plus the vault): what is true right now. On disk, so a crashed, compacted, or killed session resumes from files instead of recollection.
3. **The telemetry** (`tools/`): what actually happened. Written by hooks the model does not control, so the numbers cannot be flattered.

The order matters. Law without state forgets. State without telemetry lies. All three together make claims checkable, which is the property everything else depends on.

## 3. How a session runs

Invoke `/brothermode` and the sequence is mechanical: classify the work (which profile, simple or complex), assign only the roles the task needs, read the relevant vault memory, map the ground (git status, live writers, disk space, the project's documented commands), set the loop (rubric, phase plan with kill criteria, budgets), open the state file, execute, close with a scorecard and the vault write-back.

Two rules sit above the sequence. The safety floor (ground map, fence registration, state on disk) runs whenever anything writes, no matter how trivial the task looks, and the learning loop is forbidden from training it away. And simple work legitimately skips the ceremony: a one-seam, cheap-to-undo task with precedent takes the direct path. Proportionality is scored, in both directions, and logged when it misses.

Delegation climbs a ladder and stops at the first sufficient rung: answer directly, one search, ask the human, work inline, one agent, a fleet. Fleets are fenced, tiered (T1, T2, T3), launched as one wave when independent, and nothing an agent claims is merged until the orchestrator re-runs its done-check against the actual files.

## 4. The vault: role, structure, and why

The vault is the system's long-term memory: a plain folder of markdown and JSONL, owned by you, readable without any tool. Its role is singular: everything worth keeping outlives the session that learned it. Sessions die constantly (context limits, crashes, compactions); the vault is why dying costs minutes instead of days.

```
<vault>/
  Home.md                      the front door: projects, open threads, system links
  AGENTS.md                    the constitution: how sessions read and write memory
  10-Projects/<name>/          one space per project
    Overview.md                what this project is, current stage, key facts
    Open-Items.md              dated, living to-do; closed items leave same session
    Sessions/*.md              one log per work session, the resumption trail
    OUTCOMES.md                one human line per substantial run, with its lesson
  40-Failures/                 the failures index: check BEFORE working in an area
  50-Reference/                cross-project knowledge; the pending-amendments note
  90-Archive/                  superseded notes, each leaving a pointer behind
  99-System/telemetry/         machine-written JSONL ledgers; hooks only
```

Why this shape and not a database: markdown survives every tool change, diffs cleanly in git, and stays legible to a human at 2 a.m. Why numbered folders: they sort stably and make the reading order obvious (projects, then failures, then reference, then archive, then machinery). Why the split between human-written notes and machine-written telemetry: the human record may editorialize; the machine record cannot, and keeping them apart means neither contaminates the other.

Three disciplines keep the vault useful instead of hoarding:

- **Selective recall.** A session reads what the task needs (this project's Overview, the failures for this area), never the whole vault. Recall is a query, not a tour.
- **Active forgetting.** Superseded notes move to `90-Archive/` with a pointer left behind; resolved items close the same session. A memory system that only grows becomes noise itself.
- **Linking.** Every note connects with [[wiki-links]] to what it builds on, supersedes, or contradicts, so recall follows edges instead of scanning folders.

## 5. Connecting Obsidian, and optionally Mem0

**Obsidian** is the native fit and needs no configuration: the vault IS an Obsidian vault. Open the folder in [Obsidian](https://obsidian.md) (free), and the [[wiki-links]] become clickable, the graph view draws how projects, failures, and decisions connect, and backlinks show every note that references the one you are reading. The repo ships `vault-template/` with the full layout; `docs/SETUP.md` step 3 is the one copy command. Nothing breaks without Obsidian; the links stay readable as plain text in any editor.

**Mem0** is an optional semantic layer, not a replacement. The vault is deliberate memory: structured, curated, human-legible. Mem0 adds the other kind: automatic capture and similarity-based recall of small facts and preferences across sessions. The two current paths, per Mem0's own documentation ([docs.mem0.ai/integrations/claude-code](https://docs.mem0.ai/integrations/claude-code), [mem0.ai/openmemory](https://mem0.ai/openmemory)): the hosted Platform MCP (an HTTP endpoint you register with `claude mcp add`, zero local setup, cloud-stored), or OpenMemory MCP (self-hosted, local-first, nothing leaves your machine). If you try either, keep one rule firm: the vault remains the source of truth for decisions, failures, and laws; Mem0 holds recall candidates, never canon. Two memory systems that both claim authority will eventually disagree, and an agent with two contradicting memories is worse than an agent with one. BrotherMode ships no Mem0 wiring; this is a documented extension point, not a shipped feature.

## 6. How it keeps improving

The skill treats itself as a product under the same law it applies to everything else, with a loop borrowed from three named sources and adapted:

1. **Observe.** The moment a weakness shows, one line goes to the vault's append-only pending-amendments note. Session death cannot lose it.
2. **Measure.** Hooks write every real session's tokens, tool calls, agents, and duration to `outcomes.jsonl`. A regex scans your short messages for correction candidates. Absent data reads "not measured", never an estimate.
3. **Consolidate weekly.** A 20-minute review (`tools/WEEKLY-REVIEW.md`) scores the week against a frozen rubric, with a fresh-context judge that sees evidence but not the sessions that produced it, and lands at most one consolidation commit of small delta edits to `SKILL.md`. The file must stay near its size: a new law merges with or displaces an old one, never just piles on.
4. **Gate and revert.** Every amendment names the measured signal it should move. The next review compares that signal against the pre-amendment record and reverts the change if it did not strictly improve, keeping the best version of the law rather than the latest. Rejected and reverted edits stay in the note with their reasons, as negative feedback, never re-proposed without new evidence.

Credit where the ideas came from: the eval severity model and judge economy from Vercel's eve agent framework, cross-model review consensus and post-release canary checks from Garry Tan's gstack, and validation-gated skill editing with a rejected-edit buffer from Microsoft's SkillOpt. Convergent detail worth noting: all three arrived independently at checkpointed durability and bounded edits, which is some evidence the shape is right.

## 7. How it avoids repeating mistakes

Four mechanisms, layered from fast to slow:

- **The known-mistakes ledger** (SKILL.md section 13): the never-again list, read as law every session. Entries are promoted from repeated failures, each phrased as the rule that prevents the repeat.
- **The failures index** (`40-Failures/` in the vault): checked before working in any area, so a session entering familiar territory inherits its scars.
- **Settled findings stay settled.** Refuted and by-design findings are recorded; reopening one requires new evidence, not a fresh session's fresh confidence.
- **The rejected-edit buffer** (section 6 above): even the improvement process remembers what it already tried and declined.

The common thread: a mistake is only expensive the first time if the system writes it down where the next session must trip over the note.

## 8. Versus baseline Fable and Opus

Honest framing first: BrotherMode adds no intelligence. Fable and Opus with no skill produce the same quality of reasoning on a single, self-contained question, faster and cheaper. What the baseline does not have is everything between sessions and between agents:

| Dimension | Baseline model session | With BrotherMode |
|---|---|---|
| Memory across sessions | Starts near zero each time | Vault read at start, written at close |
| Multi-agent writes | Collisions possible, silent | Overlap between declared file sets refused by name; an undeclared file is still unprotected |
| Effort calibration | Model judgment per prompt | Declared tiers, scored proportionality |
| Verification | Whatever the prompt demands | Done-checks and evidence blocks by law |
| Honest claims | Persuasive by training | Calibration labels, self-score cap, refuters |
| Recovery from crash | Lost context | STATE.md and resume-by-id |
| Learning from mistakes | None between sessions | Ledger, failures index, correction capture |
| Cost | Baseline | Meaningfully higher: ceremony, refuters, telemetry |

That last row is real and belongs in the open: an early audit of this system on the author's machine scored its coordination high and its learning loop low, with negative token return at that date. The validation-gated amendment loop exists precisely because of that finding. Run the system where the overhead buys something: multi-session projects, multi-agent work, work where being wrong is expensive. A quick question does not need a constitution.

## 9. Pros and cons

**Pros.** Work survives session death. Parallel agents cannot destroy each other's work. Claims arrive with evidence, and bad news arrives first. Mistakes are paid for once. The system improves on measured outcomes and reverts its own bad ideas. Everything is plain files: no lock-in, no server, auditable end to end.

**Cons.** Token and time overhead on every nontrivial run, and the overhead is only worth it above a certain task size. The weekly review needs a human twenty minutes; skip it and the learning loop silently stops. Defaults are calibrated to one machine's lived history and must be re-measured on yours. Several newer laws shipped verified by inspection, not yet by long use. And the discipline itself can be miscalibrated: too much ceremony on simple work is a failure mode the system scores but cannot fully prevent.

## 10. When to use it, and when not

Use it for: multi-session projects, anything with parallel agents or fleets, releases and other irreversible steps, data work where a wrong number costs money, any codebase where two sessions might ever touch the same files, and any working relationship where you want the agent's claims to be auditable.

Do not use it for: one-shot questions, throwaway scripts, brainstorming where verification ceremony kills momentum, or any context where nobody will ever run the weekly review. An unreviewed BrotherMode is just a long prompt with extra steps; the loop is the point.

## Sources

- Vercel eve: vercel.com/blog/introducing-eve, eve.dev/docs
- gstack: github.com/garrytan/gstack
- Microsoft SkillOpt: microsoft.com/en-us/research/blog/skillopt-agent-skills-as-trainable-parameters, arxiv.org/abs/2605.23904
- Mem0: docs.mem0.ai/integrations/claude-code, mem0.ai/openmemory
- Obsidian: obsidian.md

Created by Khalil Maaouni.

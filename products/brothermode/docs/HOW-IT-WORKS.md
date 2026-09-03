# How BrotherMode works, exactly

This document explains every moving part: the law, the state on disk, and the three Python-and-shell tools that make the learning loop mechanical. Read `README.md` first for the short version. Nothing here requires reading code, but every claim below is checkable against the code, and the code is commented for the same reason.

## 1. The shape of the system

BrotherMode has three layers, and the order matters.

1. **The law** (`SKILL.md`): what a session must do. Loaded when you invoke `/brothermode`.
2. **The state** (`STATE.md` per project, plus the vault): what is true right now. Lives on disk so a crashed or compacted session resumes from files, never from memory.
3. **The telemetry** (`tools/`): what actually happened. Written by hooks, not by the model, so the numbers cannot be flattered.

The design bet is simple: models drift, disks do not. Anything that must survive (laws, fences, decisions, evidence) is written down the moment it exists. The model is allowed to forget everything else. That is not a limitation. It is the feature.

## 2. The law in one pass

`SKILL.md` has 15 sections. What each one buys you:

| Section | Law | The failure it prevents |
|---|---|---|
| 0 | Invocation sequence | Sessions that skip straight to code without mapping the ground |
| 1 | Work-nature profiles | One-size-fits-all process applied to code, data, copy, and design alike |
| 2 | Role assignment | Vague "helpful assistant" posture instead of named responsibilities |
| 3 | Decision ladder and delegation | Spawning five agents for a question one search answers |
| 4 | Token budgets | Unbounded spend, unbounded returns, polluted context |
| 5 | Single-writer fences | Two agents editing the same file; work lost on session death |
| 6 | Research doctrine | Claims typed from memory; single sources dressed as facts |
| 7 | Probes, candidates, circuit breakers | Sunk-cost spirals and third identical retries |
| 8 | Self-improvement loops | A process that never learns from its own record |
| 9 | Context hygiene | The orchestrator drowning in its own transcripts |
| 10 | Honesty and push-back | Sycophancy; bad news buffered to the summary |
| 11 | Computer control and founder gates | The model touching credentials, releases, or destructive ops alone |
| 12 | Structured memory | Knowledge that dies with the session |
| 13 | Known-mistakes ledger | Repeating a failure the system already paid for |
| 14 | The founder model | Alignment measured by agreement instead of by diverged predictions |
| 15 | Scoring every run | Self-graded 10s with no external evidence |

Two rules sit above all of it. The safety floor (ground map, fence registration, state file) runs whenever any write occurs, no matter how simple the task looks. And the never-forget list (safety invariants, founder gates, live fences, unmerged work, open asks) is exempt from every forgetting mechanism.

## 3. Sessions, tiers, and fences

**Classification first.** Every run opens by naming its work profile (product build, data analysis, research, content, design, or ops) and its complexity (simple or complex). Simple work skips ceremony. Complex work gets probes, independent candidate solutions, and kill criteria written before the work starts.

**Effort tiers.** Every delegated brief declares a tier: T1 (one session, 3 to 10 tool calls), T2 (2 to 4 subagents), or T3 (a fleet with divided, fenced scopes). The tier is written into the fence line, and the weekly check flags any fence without one. Spend is judged against the declared tier, not against a vibe.

**The fence registry.** Before any writer launches, a fence line goes into `STATE.md`. The format (see `STATE.template.md`):

```
- agent: <who> | tier T2 | TTL <date> |
  objective: <one line> |
  files: <exact writable set> |
  boundaries: <what it must not touch> |
  termination: <observable end state> |
  check: <a runnable command that proves it worked> |
```

A fence closes only with an evidence block: the exact command run and its last lines, inline in the registry. A fence past its TTL is treated as released. A killed agent is resumed by its id, never respawned fresh while a transcript exists, because respawn redoes the exploration and loses the state.

**Checks have severity.** A check is either a gate (blocks the landing) or soft (tracked as a score, blocking only in a strict loop). And a deterministic check (a command, a grep, a diff) is always tried before spending an LLM judge, because a judge burns tokens and can waffle where a command cannot.

## 4. The vault

The vault is a plain folder of markdown and JSONL, default `~/BrotherModeVault`, movable with the `BROTHERMODE_VAULT` environment variable. It is designed to be opened as an Obsidian vault: notes connect with [[wiki-links]] so recall follows edges instead of scanning folders, and `vault-template/` in this repo ships the whole starting layout including the vault constitution (AGENTS.md). Layout:

```
<vault>/
  99-System/telemetry/outcomes.jsonl      one line per real session (machine-written)
  99-System/telemetry/ratings.jsonl       felt-outcome scores from the founder
  99-System/telemetry/reviews.jsonl       weekly review markers
  99-System/telemetry/corrections.jsonl   correction candidates (machine-scanned)
  50-Reference/founder-model.md           the living founder model (optional)
  10-Projects/<name>/Sessions/*.md        one human-written log per work session
  10-Projects/<name>/OUTCOMES.md          one human line per substantial run
```

Session logs and OUTCOMES lines are written by the model as part of its close-out duties. The telemetry JSONL files are written only by hooks. That split is deliberate: the human-readable record can editorialize, the machine record cannot.

## 5. `bm_telemetry.py`, subcommand by subcommand

This is the mechanical half of the learning loop. Its one law, enforced in `main()`: it never blocks work. Every path, including crashes, exits 0.

**`outcomes-append`** is the SessionEnd hook target. It reads the hook payload from stdin, opens the session transcript (a JSONL file the harness writes), and aggregates it in `parse_transcript()`:

- Token counts are taken per API message id, keeping the maximum value seen per message. Transcripts flush usage progressively, so the last flush per message is the true count; summing every flush would double-count.
- Tool calls, subagent spawns, and workflow calls are counted from `tool_use` blocks.
- Human messages are counted only from real user turns: tool results and system-reminder injections are filtered out.
- Subagent transcripts under the session's `subagents/` folder are parsed the same way and their output tokens recorded separately (`sub_out_tokens`).

Then three guards run before anything is written. An activity floor (fewer than 5 API messages or zero tool calls means the session was trivial and is not recorded). A duplicate-flush guard (the hook can fire more than once per session; an identical metric set is never appended twice). And the append itself is atomic (single `os.write` of one line, so concurrent sessions cannot interleave).

The record is schema 2, using OpenTelemetry GenAI attribute names for token counts, with a `token_basis: "as-flushed"` label because the transcript may lag the final turn. Honest labels beat pretty ones.

Finally, the same command scans the main transcript's short human messages against a correction regex ("no, that", "i said", "from now on", "never do", and similar) and appends up to 5 candidates per session to `corrections.jsonl`. These are candidates, not verdicts: the weekly review filters them by hand, because a regex cannot tell a correction from a quotation.

**`scorecard`** renders the 9-metric dashboard from the ledgers. Mechanical fields are computed (session counts, token totals, cache warm-read ratio, rating averages); judgment fields print what the weekly review must decide. It computes on `real_sessions()`: hook self-tests are excluded and only the last flush per session id counts, so "session" means the same thing in every output.

**`rate`** appends one founder felt-outcome rating (1 to 5) with the task it applies to. Skipped ratings stay unrated. Nothing is inferred.

**`review-mark`** appends a weekly-review marker; the startup nag reads it to know when the review is overdue.

**`startup-nags`** prints at most a few lines for session-start injection: overdue review, yesterday's spend, telemetry heartbeat silence, and any active day missing its session log.

**`stop-warn`** is a Stop-hook target that fires on every assistant turn, so it is built to be near-free: it short-circuits on a per-session marker file kept under the vault's telemetry folder, then on transcript size, then on whether any session log was written today. It never parses the transcript and it only warns; exit 0 cannot block.

**`registry-check`** flags fence lines that look live inside a file that has not been touched for days: the signature of a dead agent whose work was never adopted.

**`fence-lint`** is a dispatch aid: it prints the live fences from the project's STATE.md and the BROTHERMODE_REGISTRIES globs so no writer launches into an occupied file set, and flags any fence line missing its tier tag.

**`bm_autosave.py`** is a separate hook, not part of the telemetry tool, because it runs git and the telemetry tool is deliberately subprocess-free. On the PreCompact hook (which fires right before Claude Code compacts context, the thing that happens when you run low on tokens) it snapshots your entire working tree, untracked files included, into a private git ref `refs/brothermode/autosave`, using a throwaway index so your branch, index, and working tree are never touched. It runs git locally only and never pushes. This is the same "the model is not the one writing it" philosophy applied to your work rather than only to telemetry: the rule "save before you die" cannot be trusted to the dying context, so a hook runs it instead. `bm_autosave.py recover` prints exactly how to restore a snapshot, and an opt-in `tick` mode (off unless `BROTHERMODE_AUTOSAVE` is set) autosaves every N tool calls for a crash that is not a compaction. The git snapshot saves your files; two companions save the *thread*, because losing your files was never the whole pain, losing the momentum was. `precompact-brief` (in the telemetry tool, so pure) distills the dying transcript's tail, the last instruction, recent decisions, recent commands, into a resume brief the session-start hook hands back after a compaction. And `intent` is a write-ahead log: log `next: X, because Y` *before* a risky action, and a death always leaves a forward-looking record, the way a database writes its log before its data. Files, thread, and next intent: a resumed session continues instead of restarting.

**`prediction-audit`** counts sealed predictions in the founder model's prediction ledger. Zero sealed predictions is reported as a red flag: predictions must be sealed before recommendations are formed, or the alignment metric rewards hindsight.

**`speed`**, **`migrate`**, and **`dedup`** are maintenance views and one-time cleanups; each prints exactly what it did, backs up before rewriting, and rechecks line counts after.

## 6. `bm_score.py`: code grades first, judgment second

The weekly review could ask an LLM to score everything. It does not, on purpose. Ten checks are graded by code (ledger coverage, schema uniformity, cache economy per session, session-log coverage, fence hygiene, correction latency, tier tagging, prediction seals, rating counts, review cadence), each printing PASS, FAIL, or NO-DATA with its evidence inline. The LLM judge scores only the residue code cannot decide.

The honesty rule is structural here too: a check without data says NO-DATA, never PASS. Run it on a fresh machine and you get 7 NO-DATA lines, which is the correct answer for a system with no history yet.

## 7. The weekly review

`tools/WEEKLY-REVIEW.md` is an 11-step procedure, about 20 minutes. The steps that make it work:

- **Judge isolation.** The scorer is a fresh session given only the evidence bundle, never the sessions that did the work. Models prefer their own output; this removes the opportunity.
- **Anchored scoring.** Each metric is scored better, same, or worse against last week's evidence, and the absolute number follows from the comparison. Naked absolute scores drift.
- **An anti-Goodhart spot-check.** Two random claims from the week's logs are verified against raw evidence before any scoring. A fabricated claim voids the week.
- **Exactly one consolidation commit.** Corrections that survive the filter become laws in `SKILL.md`, as delta edits under a hard size cap: a new law merges with or displaces an existing one, never just accretes. A fresh-context critic gates the diff before it lands. No-change weeks record an explicit no-change.

## 8. What you should change, and what you should not

Change freely: the rubric baselines, the vault location, the registry globs, the work-nature profiles, the tier thresholds. These are calibrated to one machine's lived history (the author's), and the law itself says the measured record on YOUR machine overrides the defaults.

Change carefully, with your team's eyes open: the safety floor, the single-writer law, the honesty gates, the never-forget list. These are the load-bearing walls. Every one of them exists because its absence already cost a real afternoon.

The system improves itself weekly by design. The one thing it is forbidden to learn is that the floor is optional.

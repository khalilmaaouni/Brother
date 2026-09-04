---
name: next
description: "Use when someone asks what to do next in their work. Evaluates a fixed priority ladder against observable state and returns exactly one recommended action with a one sentence reason, never a menu of options. Invoke as /brothersbe:next."
---

Plugin root: a Claude Code install exports `${CLAUDE_PLUGIN_ROOT}` and a Codex install exports `${BROTHER_PLUGIN_ROOT}`; both name this plugin's own directory, so read whichever variable appears below as the one your client set. On a clone install neither is set: run the same commands from the checkout root instead.

# Next

One question, one answer. This skill exists so the user never has to hold the lifecycle in
their head.

## LANE C1 (B-003): one canonical next action

`src/brothersbe/lifecycle.py`'s `reduce_next_action` is now the ONE place that decides which
outstanding fact is most urgent for a change. Plain `sbe status --json`'s `nextAction`
(a sentence) and `nextActionDetail` (`{actionId, label, reason, basis}`), and `sbe status
--team --json`'s own severity-10 finding for each discovered change, are both derived through
that SAME reducer, so they can no longer read the same recorded state two different ways. This
skill's own job shrank to match: read that field and recommend exactly what it says, rather
than re-deriving a priority ladder by hand from the rest of the JSON. Do not re-implement the
ladder here; if it ever needs to change, it changes in `lifecycle.py`, once, for every reader.

## Rungs 1-3: checks the reducer cannot run

`sbe status` never starts a subprocess and never runs a NEW check over source code (its own
module docstring names this as the reason a truthful summary stops and says so rather than run
one); it reads state other commands already recorded. These three rungs are genuinely outside
what `nextAction` can ever cover for exactly that reason, so they stay separate, live probes.
Evaluate them, in this order, BEFORE trusting anything `nextAction` says, and stop at the first
one that matches:

1. **Environment broken, or not yet set up.** Run `"${CLAUDE_PLUGIN_ROOT}/bin/sbe" doctor
   --json` and read `result`. `FAIL` means at least one `checks[]` entry reads `FAIL`; name
   it (`name`, `detail`) and recommend repairing that specific check, before anything else
   downstream can be trusted. `SETUP` means nothing is broken: the repository simply has not
   been set up yet, the normal state of a new project. Say so in plain language, never as a
   failure, and recommend `/brothersbe:start`, which sets up the workspace in one step
   (preview with `sbe init`, apply on a yes). Either way, stop at this rung: neither state
   is ready for anything downstream.
2. **No intake recorded.** Run `"${CLAUDE_PLUGIN_ROOT}/bin/sbe" status --json` and read
   `scope.storesInspected`. `intake` null and `dossiers` null together mean no flat
   `00-intake.json` exists at the root and dossier discovery found none under the design
   roots either: the work was never scored. Recommend `/brothersbe:kickoff`.
3. **Dossier incomplete for the tier.** Run `"${CLAUDE_PLUGIN_ROOT}/bin/sbe" design --strict
   <dir>` against the root `scope.storesInspected.intake` names, or a dossier
   `scope.storesInspected.dossiers` names. A nonzero exit means the run printed a FAIL line
   somewhere; read that line for the missing or malformed artifact. Recommend
   `/brothersbe:design`.

## Rung 4: everything the reducer already knows about

When none of the three rungs above matches, read `nextAction` and `nextActionDetail` from the
`sbe status --json` output rung 2 already ran (no need to run it twice). When
`scope.storesInspected.dossiers` names a discovered dossier, also run
`"${CLAUDE_PLUGIN_ROOT}/bin/sbe" status --team --json` and read the severity-10 finding for
that change (`actionId`, `label`, `nextAction`, `verdict`, `owner`), the same per-change detail
the single-project report rolls up into one line. Recommend exactly what `nextActionDetail`
(or the matching severity-10 finding) says, naming its `actionId` and quoting its `nextAction`
text; never re-derive a different answer from the rest of the JSON by hand.

What each `actionId` means, kept here ONLY as explanation of what the reducer considers, never
re-evaluated by hand:

- `resolve-broken-claim`, `resolve-merge-blocker`, `resolve-active-conflict`: sections 1-3 of
  the plain report, or the matching team severity (1, 2, 3). A hard gate that WAS evidenced and
  FAILED lands here (`resolve-merge-blocker`), naming the failing receipt.
- `provide-missing-evidence`: the declared tier owes a design, gate or score run and no receipt
  declares one yet. Recommend `/brothersbe:verify`, which is where that run actually happens
  and where a FAIL, if there is one, gets reported with full detail; this skill does not run
  the check itself to preview the answer.
- `continue-active-task`: a task is claimed and in flight. Recommend continuing it, named
  specifically, through the `work` subcommands (`work start`, `work check`, `work finish`).
- `start-ready-task`: a task's dependencies are all closed clean and it carries no registry
  record yet. Recommend the `sbe work start` command the finding names.
- `run-review`: the plan's tasks are done and evidence is clean, but review has not cleared
  (missing, stale, self-reviewed, or not approved). Recommend `/brothersbe:review`.
- `finish`: nothing outstanding that this tool can see. Recommend finish guidance: write the
  summary, open the pull request, and hand the merge decision to a human. The merge is never
  this skill's call.

## How to answer

State the one recommended action, then why in exactly one sentence grounded in the field or
verdict word you actually read: name it and its value first (for example, "nextActionDetail
names run-review: resolve what the review flagged, then record a fresh 11-review.json" rather
than a paraphrase), then the plain-language meaning. Do not speculate about rungs you did not
probe.

## Always close with the response contract

End every answer with, in order: where you are, what is complete, what needs attention, the
ONE recommended next action, why, what BrotherSBE will do automatically, what decision the
user owns, and how success will be verified. Omit an element only when it is genuinely empty,
never because it is inconvenient.

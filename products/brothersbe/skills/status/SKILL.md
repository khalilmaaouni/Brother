---
name: status
description: "Use when someone wants to know where their work stands. Wraps the status command and the fence registry, and reframes the raw output as a plain answer with one next action, with technical detail kept below the summary rather than leading it. Invoke as /brothersbe:status."
---

Plugin root: a Claude Code install exports `${CLAUDE_PLUGIN_ROOT}` and a Codex install exports `${BROTHER_PLUGIN_ROOT}`; both name this plugin's own directory, so read whichever variable appears below as the one your client set. On a clone install neither is set: run the same commands from the checkout root instead.

# Status

Report where the work stands in language a person can act on. The engine's JSON is the input
to your answer, never the answer itself.

## Gather

Run the status command and read the whole JSON document before writing a word:

```
"${CLAUDE_PLUGIN_ROOT}/bin/sbe" status --json
```

When `scope.storesInspected.dossiers` names a discovered dossier, also run
`"${CLAUDE_PLUGIN_ROOT}/bin/sbe" status --team --json` and read its `findings` and `changes`
for the per-dossier detail the single-project report rolls up into one summary.

Also run `"${CLAUDE_PLUGIN_ROOT}/bin/sbe" task list` to see the live task ownership. An open
task tells the user which files are currently claimed for editing and by what work, which
matters to anyone deciding what to touch next.

If either command fails, report the failure plainly, say what you could still observe, and
recommend `"${CLAUDE_PLUGIN_ROOT}/bin/sbe" doctor --json` as the next action. When that
doctor run's `result` reads `SETUP`, nothing is broken: the repository has not been set up
yet, the normal state of a new project. Say so in those plain terms, never as a failure,
and recommend `/brothersbe:start` for the one setup step. See
`${CLAUDE_PLUGIN_ROOT}/docs/CLI.md` for what each command covers.

## Reframe, do not relay

Lead with the guided shape, in plain sentences, each sourced from a named field rather than a
paraphrase of the rendered text:

1. **Where you are**: the five sections, `brokenClaims`, `mergeBlockers`, `activeConflicts`,
   `missingEvidence`, `soundEvidence`, read in that order; the first one holding an item names
   the stage. All five empty: read `notes` for the clean or NO-DATA line behind that, and
   `scope.storesInspected` for what was searched.
2. **What is complete**: `soundEvidence`, the COMPLETED EVIDENCE section; `notes.soundEvidence`
   carries the clean or NO-DATA line when it is empty.
3. **What needs attention**: `brokenClaims`, `mergeBlockers`, `activeConflicts` and
   `missingEvidence`, in that order, the same priority `nextAction` itself reads; a live fence
   from the second command is attention too, even when every section above is clean.
4. **The single next action**: `nextAction` (a sentence) and `nextActionDetail` (`{actionId,
   label, reason, basis}`), verbatim.

LANE C1 (B-003): `nextAction` and `nextActionDetail` are now TRUE BY CONSTRUCTION the same
answer `/brothersbe:next` reads for the same state, and the same answer `sbe status --team
--json`'s own severity-10 finding gives for the matching change, because all three are derived
through the single reducer `src/brothersbe/lifecycle.py` owns
(`lifecycle.reduce_next_action`). Before this, `sbe status`'s blocker-first sections and `sbe
status --team`'s severity-10 finding were two independent derivations that could name a
different next action for the identical dossier (a temp-dir reproduction proved it: a change
whose only outstanding obligation was review read as "nothing blocking here" from the plain
report and "nothing left to do, open a pull request" from team's own severity-10, because
neither surface had ever looked at review or task-readiness state, and team's raw severity
numbering let "completed" outrank "review record" besides). That gap is closed at the source,
not papered over here: this skill still just reads `nextAction`/`nextActionDetail` verbatim,
it no longer has to disclaim that another surface might say something else about the same
change.

Technical detail (raw verdict lines, receipt paths, fence entries, exit codes) goes under a
clearly separated section titled "Details", after the summary, never first. Include it: the
detail is how a skeptical reader checks your summary. Just never make anyone read it to
learn where they stand.

## Always close with the response contract

End with, in order: where you are, what is complete, what needs attention, the ONE
recommended next action, why, what BrotherSBE will do automatically, what decision the user
owns, and how success will be verified. Omit an element only when it is genuinely empty,
never because it is inconvenient.

---
description: Start a project with a short guided conversation that ends in one clear project brief
argument-hint: <what you want to build or achieve>
---

> This command works and is supported. Its current name is `/brothermode:start`, and both names do exactly the same thing.

The user wants to start a project. Their goal, in their own words: $ARGUMENTS

Outcome to produce: one clear project brief (the Project Canvas) and one recommended first decision, in plain language, with a realistic time and cost range.

Enter the guided kickoff flow of the brotherme skill. Follow the kickoff instructions at references/kickoff.md: size up the goal, ask only the questions whose answers change the scope, one decision at a time with a recommended option first.

## The first minute: one block, one question, and NOTHING written

Before anything is created anywhere, print the opening block and ask one question. Run `python3 "${CLAUDE_PLUGIN_ROOT}/tools/bm_view.py" doorway` (the packaged console script is `bm-view doorway`) and read its block out: it writes no file, creates no folder and needs nothing to have been set up, which is what makes it safe to run in a folder the user has not yet agreed to. Same install-path rule as below.

The block answers three questions and no others: what is about to happen, what it will cost in time and money as a range with a confidence level, and what the user will have to decide. It carries one recommended action and one thing they can ask for instead. It does not list the commands and it names no machinery: every word obeys references/terminology.md, so they meet the product before they meet its parts.

If the user typed a goal above, say it back in their own words in one line. If they did not, ask for it in one sentence and wait.

## Minutes one to four: their goal, in their own words, one question at a time

Every question travels as a decision card in the shape of references/kickoff.md, through the AskUserQuestion window, recommended option first. Chat text carries the evidence and the context around the window, never the option list. Ask only what changes the scope. Anything else becomes a stated assumption they can correct, which costs one line now instead of a question.

## Minute four: their yes, then the first thing written, then the first page

FIRST RUN IN A PROJECT, do this before anything mechanical: a brand new project folder has no records yet, and `bm_project.py start` REFUSES rather than creating them, with a message naming `bm_store.py init`. That refusal is correct engine behaviour and it is the wrong thing for a beginner's first minute, so never let them see it. Run `python3 "${CLAUDE_PLUGIN_ROOT}/tools/bm_store.py" init` in the user's project folder yourself, once, before the first `bm_project.py` command, and say one plain sentence about what it did ("set this folder up to remember your project"). If it is already set up, the command says so and nothing is harmed. Same install-path rule as below.

Say two things at that moment, in this order, and never skip the second:

1. What was created, in one plain sentence, and that it lives in their own folder and goes nowhere else.
2. That when they later approve the page being published as a private page they can keep open in a browser, they are asked ONCE. Publishing that same page again afterwards does not ask again. That is the behaviour they want, and it is a thing to be told at the start rather than discovered later.

RECORD THE OUTCOME FIRST, once the user has said in their own words what they want: run `python3 "${CLAUDE_PLUGIN_ROOT}/tools/bm_lead.py" outcome --project-id <id> --set "<their words>"` (the packaged console script is `bm-lead outcome`), then continue the guided kickoff. There is one command that records what the user is trying to achieve, and this is it, so the goal the status view reads back later is the goal they actually stated rather than a paraphrase gathered twice. Same install-path rule as below.

Then, and only then, write the first page: run `python3 "${CLAUDE_PLUGIN_ROOT}/tools/bm_view.py" render --project-id <id>` (the packaged console script is `bm-view render`) and offer it to them. It will be nearly empty, and that is the point: every section says what will fill it and names the one thing that fills it, and the counter at the top says how many of the eight setup steps are genuinely done, counted from real records rather than claimed. At this moment it reads 2 of 8, and saying so is honest in both directions: something real has happened, and most of it has not.

## Minutes four to nine: one real piece of their own project, finished

Do not describe what this product will do for them. Take the smallest genuine piece of their actual project, run it to completion, and show them the thing that came out. They should be able to point at something that exists and say that came from what I said five minutes ago. A demonstration on a made up example teaches nothing anybody remembers.

## Minutes nine to fifteen: the page, the catch-up, and the way out

Rewrite the page once the first piece is done, and let it do the explaining: the drawing of the stages shows where they are, the stages not reached yet read as waiting rather than missing, and every section still empty says what will be there. The first catch-up arrives at the first change of phase rather than on a clock, so a first run gets one without waiting half an hour for it.

Offer three commands during this whole stretch and no more: `/brotherme-start`, `/brotherme-status`, `/brotherme-next`. The others introduce themselves when they become useful, and a user who has met three commands and used all three is further along than one who was handed fourteen.

The first time a decision is put to them, the last option on the card is their own: they can take the decision and the work under it back. That option is not a closing courtesy, it is on every card and on the page, and it is offered before they think to ask.

## The mechanical command that records the brief

Once the goal, scope, and first decision are settled, run the mechanical command `python3 "${CLAUDE_PLUGIN_ROOT}/tools/bm_project.py" start` with the gathered details: it creates the project record in that project's own records and regenerates CANVAS.md from those rows. A plugin install exports `${CLAUDE_PLUGIN_ROOT}` for skill and command content, so that path resolves on its own; on a clone install, where the variable is unset, run `python3 tools/bm_project.py start` instead, from the BrotherMode root (`~/.claude/skills/brothermode`). Either way, run it from the user's project folder so it reads and writes that project's own records. Never fill CANVAS.md by hand and never answer from memory of this conversation about what the project record holds; the command's own output is the only source of truth. If the goal above is empty, ask for it in one sentence before anything else.

---

## Maintainer note, not for the reader above

Kept verbatim from where it used to sit at the top of this file. It was moved on 2026-08-29 because it was the first thing anybody read: the team reported finding fifteen commands and every one of them declaring itself a legacy compatibility shim, which reads as an abandoned product. The mechanism is unchanged and nothing was removed.

> DOCUMENTATION NOTICE, 2026-08-11 (V3 Final, task A2). This command file is not part of the six-name public surface. It keeps working exactly as it does today and is not deprecated in behaviour; only its documented status changed. Physical consolidation of these shims is a later tranche, so nothing here is removed in this release.

> LEGACY v2 COMPATIBILITY SHIM (the founder's 2026-08-07 night rename decision, recorded in this project's working history rather than a file this repository ships). Legacy surface: `/brotherme-start` under the pre-rename `brotherme` plugin id. Replacement: `/brothermode:start` at `skills/start/SKILL.md`. Reason: the founder's 2026-08-07 night namespace rename retired the flat `commands/` layout as the canonical public surface; this file is kept, unchanged below, only so a v2 install or a v2 habit still resolves during the migration window. Test: `tools/test_bm.py`'s `TestTheSeventhCommandAndTheDeepTourAreWired` (the fifteen-command inventory pin) and the naming/ACTIVE_DOCS scan in `tools/test_bm_docs.py` still exercise this exact file and path; do not rename or delete it without updating both. Removal condition: the v3.0.0 tag, at the release court described in freeze answer 14, once `claude plugin validate` and a repository grep show no live consumer of `/brotherme-start` remains.

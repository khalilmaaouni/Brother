---
name: start
description: Start a project with a short guided conversation that ends in one clear project brief
argument-hint: <what you want to build or achieve>
disable-model-invocation: true
---

The user wants to start a project. Their goal, in their own words: $ARGUMENTS

Outcome to produce: one clear project brief (the Project Canvas) and one recommended first decision, in plain language, with a realistic time and cost range.

Enter the guided kickoff flow of the brotherme skill. Follow the kickoff instructions at references/kickoff.md: size up the goal, ask only the questions whose answers change the scope, one decision at a time with a recommended option first.

## The first minute: three beats, then the block, and NOTHING written

Before anything is created anywhere, and before any tool runs, say three things in this order, plain language, no command names, no machinery:

1. ASK. If the user already typed a goal above ($ARGUMENTS), say it back in their own words in one line: "Got it: [their words]." If they did not, ask exactly: "You're building something. One sentence: what does done look like?" and wait.
2. ROUTINE OR RISKY. Once you have their words, say plainly whether this looks routine (zero more questions) or risky (at most two). Infer risk silently from what the ask touches (a migration, money, auth, a partner API, personal data); never hand them a risk questionnaire and never ask them to name a tier.
3. THE FREE THING. Name what they get for free at the end: a record of what actually happened, rerunnable, that they did nothing extra to produce.

The question budget this beat names is a real number, not a mood, and it belongs on the intake record so it can be counted later rather than remembered. Whenever this flow writes or hands off an intake record (BrotherSBE's own `sbe_intake.py` for the risky path in the paragraph below, or this project's own record where it keeps one), write down `questions_asked` (each question actually put to the person, and, where an answer replaced something already stated on record, that prior assumption alongside it), `overrides` (every stated assumption the person's answer actually moved), and the tier the intake landed at. That is the whole of it: no extra question is asked to fill these in, they are a record of what already happened.

Read the level silently from the ask, never asked as a question unless genuinely ambiguous: file paths, code terms, or a pasted diff say dev; outcome language with no artifact names says BA. Which product surface they invoked (BrotherMode) is a weaker third signal leaning BA. When the signals are absent or conflict, ask ONE plain working-style question, once per person per project: "Want me to read the project and tell you what I think you are asking for, or would you rather talk it through first?" (the first answer leans dev, the second leans BA). Never say the words role, level, persona, or mode to the user. Write the answer to session state; that is the seam for a persistent vault profile, not the profile itself, so nothing is faked here until the profile exists. Adapt every sentence from here on to the detected level: plain outcomes for a BA, exact paths and commands for a dev, with zero leakage either way. Offer the switch any time, one utterance, no ceremony: "say 'show me the files' or 'just the outcomes' any time."

If the ask is clearly an engineer's, asking for change assurance on a risky change to code, say so in one line and point them at BrotherSBE's own start (`/brothersbe:start`) instead of walking them through this guided flow here: one estate, two on-ramps, one doorway, never two products competing to answer the same ask.

Then print the opening block: run `python3 "${CLAUDE_PLUGIN_ROOT}/tools/bm_view.py" doorway` (the packaged console script is `bm-view doorway`) and read its block out: it writes no file, creates no folder and needs nothing to have been set up, which is what makes it safe to run in a folder the user has not yet agreed to. Same install-path rule as below.

The block answers three questions and no others: what is about to happen, what it will cost in time and money as a range with a confidence level, and what the user will have to decide. It carries one recommended action and one thing they can ask for instead. It does not list the commands and it names no machinery: every word obeys references/terminology.md, so they meet the product before they meet its parts.

## Minutes one to four: their goal, in their own words, one question at a time

Every question travels as a decision card in the shape of references/kickoff.md, through the AskUserQuestion window, recommended option first. Chat text carries the evidence and the context around the window, never the option list. Ask only what changes the scope. Anything else becomes a stated assumption they can correct, which costs one line now instead of a question.

## Minute four: their yes, then the first thing written, then the first page

FIRST RUN IN A PROJECT, do this before anything mechanical: a brand new project folder has no records yet, and `bm_project.py start` REFUSES rather than creating them, with a message naming `bm_store.py init`. That refusal is correct engine behaviour and it is the wrong thing for a beginner's first minute, so never let them see it. Run `python3 "${CLAUDE_PLUGIN_ROOT}/tools/bm_store.py" init` in the user's project folder yourself, once, before the first mechanical `start` command, and say one plain sentence about what it did ("set this folder up to remember your project"). If it is already set up, the command says so and nothing is harmed. Same install-path rule as below.

Say two things at that moment, in this order, and never skip the second:

1. What was created, in one plain sentence, and that it lives in their own folder and goes nowhere else.
2. That when they later approve the page being published as a private page they can keep open in a browser, they are asked ONCE. Publishing that same page again afterwards does not ask again. That is the behaviour they want, and it is a thing to be told at the start rather than discovered later.

RECORD THE OUTCOME FIRST, once the user has said in their own words what they want: run `python3 "${CLAUDE_PLUGIN_ROOT}/tools/bm_lead.py" outcome --project-id <id> --set "<their words>"` (the packaged console script is `bm-lead outcome`), then continue the guided kickoff. There is one command that records what the user is trying to achieve, and this is it, so the goal the status view reads back later is the goal they actually stated rather than a paraphrase gathered twice. Same install-path rule as below.

Then, and only then, write the first page: run `python3 "${CLAUDE_PLUGIN_ROOT}/tools/brothermode_cli.py" view --project-id <id>` (the packaged console script is `brothermode view`) and offer it to them. It will be nearly empty, and that is the point: every section says what will fill it and names the one thing that fills it, and the counter at the top says how many of the eight setup steps are genuinely done, counted from real records rather than claimed. At this moment it reads 2 of 8, and saying so is honest in both directions: something real has happened, and most of it has not.

## Minutes four to nine: one real piece of their own project, finished

Do not describe what this product will do for them. Take the smallest genuine piece of their actual project, run it to completion, and show them the thing that came out. They should be able to point at something that exists and say that came from what I said five minutes ago. A demonstration on a made up example teaches nothing anybody remembers.

## Minutes nine to fifteen: the page, the catch-up, and the way out

Rewrite the page once the first piece is done, and let it do the explaining: the drawing of the stages shows where they are, the stages not reached yet read as waiting rather than missing, and every section still empty says what will be there. The first catch-up arrives at the first change of phase rather than on a clock, so a first run gets one without waiting half an hour for it.

Offer three commands during this whole stretch and no more: `/brothermode:start`, `/brothermode:status`, `/brothermode:next`. The others introduce themselves when they become useful, and a user who has met three commands and used all three is further along than one who was handed nine.

The first time a decision is put to them, the last option on the card is their own: they can take the decision and the work under it back. That option is not a closing courtesy, it is on every card and on the page, and it is offered before they think to ask.

## The mechanical command that records the brief

Once the goal, scope, and first decision are settled, run the mechanical command `python3 "${CLAUDE_PLUGIN_ROOT}/tools/brothermode_cli.py" start` (the packaged console script is `brothermode start`) with the gathered details: it creates the project record in that project's own records and regenerates CANVAS.md from those rows. A plugin install exports `${CLAUDE_PLUGIN_ROOT}` for skill and command content, so that path resolves on its own; on a clone install, where the variable is unset, run `python3 tools/brothermode_cli.py start` instead, from the BrotherMode root (`~/.claude/skills/brothermode`). Either way, run it from the user's project folder so it reads and writes that project's own records. Never fill CANVAS.md by hand and never answer from memory of this conversation about what the project record holds; the command's own output is the only source of truth. If the goal above is empty, ask for it in one sentence before anything else.

## The task that keeps the walkthrough from dead-ending

A brief on its own leaves `/brothermode:next` with nothing to say: ask it what to do next right after the brief and it reports that no task is ready, because none exists yet. That is the walkthrough's one real dead end, and closing it is part of finishing the kickoff, not a later chore.

Take the first concrete step the user just agreed to, in their own words, never a guess, and record it as a task with the mechanical command `python3 "${CLAUDE_PLUGIN_ROOT}/tools/bm_project.py" task add --project-id <id> --title "<their words>" --status ready --actor-type model --actor-name brothermode-start` (the packaged console script is `bm-project task add`). Same install-path rule as above. The `--status ready` part is the one that matters: leave it off and the task is created but stuck in a waiting state, and `/brothermode:next` will still skip it, so the dead end reopens with nothing telling you why.

Then say one plain sentence: they now have a task the system can track, and asking what to do next will hand it straight back to them.

v3 note: this skill is the canonical replacement for the legacy `/brotherme-start` command (the founder's 2026-08-07 night rename decision). It calls only `tools/brothermode_cli.py`, the one deterministic public runtime boundary (freeze answer 4), for the one verb the boundary owns (`start`). The doorway, store-init failsafe and outcome-recording calls above stay direct calls to their own internal tools: none of the three is a CLI verb, and none is new work this run adds; freeze answer 4 names `bm_project.py`, `bm_lead.py` and `bm_view.py` as the internal adapters behind the boundary, not surfaces the boundary itself must cover on day one.

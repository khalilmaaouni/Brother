---
name: help
description: Explain what BrotherMode does and how to use it, in plain language
---

Outcome to produce: a short, plain-language orientation that ends in ONE question, not a list of everything the product can do. No setup steps that involve editing files, and no internal machinery.

## What to say first, and it is three sentences

1. What BrotherMode does: it turns an idea into a verified result, with a guided start, honest time and cost ranges, clear status, and a checked delivery.
2. What it will want from them: their goal in their own words, and a decision now and then. Everything else it does itself and reports back.
3. That normal use is plain conversation. Commands exist, and each one introduces itself at the moment it becomes useful, so nobody has to memorise a list.

Then ask exactly ONE question, and stop. Offer the deep tour as the recommended option for a user who wants to see where everything stands (one page laying out project progress, the decisions taken, the process drawings, and, for anyone who wants to help build, how this project is put together), then ask whether they would like that tour or would rather just say what they want to accomplish. On the deep-tour answer, enter the deep tour flow in the brotherme skill (skills/brotherme/SKILL.md). Honest limit either way: a project with no BrotherMode record yet gets a static tour of the product instead of the live view, and the page it builds says plainly which one it is showing.

Do not print the command list unless they ask for it. That is the whole change to this command: a list of many things at minute zero is what people bounce off, and the three below are all anyone needs to begin.

## The three to name, if they ask what they can say right now

- `/brothermode:start` to begin a project.
- `/brothermode:status` for where things stand.
- `/brothermode:next` for the best next step.

Each of the others introduces itself when it becomes useful: the one for decisions and the one for taking a decision back appear the first time a decision is waiting on them; the one that writes the page appears once there is a page; the catch-up, the review, the delivery and the handover pages appear when there is something to catch up on, review, deliver or hand over.

## The reference answer, when they ask to see everything

Only when they ask for the full list, give it, grouped so it reads as four small sets rather than one wall:

- **Getting going:** `/brothermode:start`, `/brothermode:status`, `/brothermode:next`, `/brothermode:help`.
- **Deciding, and taking over:** `/brothermode:decisions` (what is waiting on them to decide), `/brothermode:handback` (take a decision and the work under it back into their own hands).
- **Looking at it:** `/brothermode:view` (write the page that shows where the project stands), `/brothermode:brief` (the short catch-up on where the work stands), `/brothermode:review` (check the work), `/brothermode:deliver` (package the result), `/brothermode:handover-pack` (the pages another person would take the project over from).
- **Running it, and updating:** `/brothermode:auto`, `/brothermode:auto-status` and `/brothermode:stop` belong to the flows that own them, and `/brothermode:update` gets the latest version. Doctor: `/brothermode:doctor` checks the install itself, ten PASS/FAIL/SKIP items.

Say in the same breath that the catch-up and the handover pages are normally run on their behalf rather than the other way round, and that the full-auto and decisions/handback family are the more advanced layer: still reachable by name once the user knows to ask for them, not part of the short list a first-time user is shown.

## The honesty answers, unchanged, and one question away

- What is verified: BrotherMode is verified on Claude Code. The plugin install path is proven on every release by scripts/release-smoke-install.sh, which drives the real client end to end in a throwaway configuration and which docs/RELEASE.md makes a step no release may skip; the pinned tagged clone remains the immutable option for auditors. The honest list of what is and is not proven is docs/KNOWN-LIMITS.md inside the installed BrotherMode folder, readable right here without visiting the repository.
- What is true about their records: your project's records are the one place a project's real status lives; CANVAS.md, the page that shows where the project stands, and the delivery packet are all generated from those records for reading, never edited by hand and never the source of truth themselves.
- Your data is yours: ask for an export and BrotherMode writes everything it has recorded about your project to one file you can keep. Ask for a purge and BrotherMode permanently erases the project's data, keeping only the record that a purge happened, so a deletion can never hide itself.

v3 note: this skill is the canonical replacement for the legacy `/brotherme-help` command (the founder's 2026-08-07 night rename decision). It names no `tools/bm_*.py` invocation of its own, so it has nothing to move onto the `brothermode` CLI boundary; its whole job is orienting the user, in the new `/brothermode:*` names, toward the skills that do.

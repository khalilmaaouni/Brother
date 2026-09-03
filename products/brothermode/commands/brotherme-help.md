---
description: Explain what BrotherMode does and how to use it, in plain language
---

> This command works and is supported. Its current name is `/brothermode:help`, and both names do exactly the same thing.

Outcome to produce: a short, plain-language orientation that ends in ONE question, not a list of everything the product can do. No setup steps that involve editing files, and no internal machinery.

## What to say first, and it is three sentences

1. What BrotherMode does: it turns an idea into a verified result, with a guided start, honest time and cost ranges, clear status, and a checked delivery.
2. What it will want from them: their goal in their own words, and a decision now and then. Everything else it does itself and reports back.
3. That normal use is plain conversation. Commands exist, and each one introduces itself at the moment it becomes useful, so nobody has to memorise a list.

Then ask exactly ONE question, and stop. Offer the deep tour as the recommended option for a user who wants to see where everything stands (one page laying out project progress, the decisions taken, the process drawings, and, for anyone who wants to help build, how this project is put together), then ask whether they would like that tour or would rather just say what they want to accomplish. On the deep-tour answer, enter the deep tour flow in the brotherme skill. Honest limit either way: a project with no BrotherMode record yet gets a static tour of the product instead of the live view, and the page it builds says plainly which one it is showing.

Do not print the command list unless they ask for it. That is the whole change to this command: a list of fourteen things at minute zero is what people bounce off, and the three below are all anyone needs to begin.

## The three to name, if they ask what they can say right now

- `/brotherme-start` to begin a project.
- `/brotherme-status` for where things stand.
- `/brotherme-next` for the best next step.

Each of the others introduces itself when it becomes useful: the one for decisions and the one for taking a decision back appear the first time a decision is waiting on them; the one that writes the page appears once there is a page; the catch-up, the review, the delivery and the handover pages appear when there is something to catch up on, review, deliver or hand over.

## The reference answer, when they ask to see everything

Only when they ask for the full list, give it, grouped so it reads as four small sets rather than one wall:

- **Getting going:** `/brotherme-start`, `/brotherme-status`, `/brotherme-next`, `/brotherme-help`.
- **Deciding, and taking over:** `/brotherme-decisions` (what is waiting on them to decide), `/brotherme-handback` (take a decision and the work under it back into their own hands).
- **Looking at it:** `/brotherme-view` (write the page that shows where the project stands), `/brotherme-brief` (the short catch-up on where the work stands), `/brotherme-review` (check the work), `/brotherme-deliver` (package the result), `/brotherme-handover-pack` (the pages another person would take the project over from).
- **Running it, and updating:** `/brotherme-auto`, `/brotherme-auto-status` and `/brotherme-stop` belong to the flows that own them, and `/brotherme-update` gets the latest version.

Say in the same breath that the catch-up and the handover pages are normally run on their behalf rather than the other way round.

## The honesty answers, unchanged, and one question away

- What is verified: BrotherMode is verified on Claude Code. The plugin install path is proven on every release by scripts/release-smoke-install.sh, which drives the real client end to end in a throwaway configuration and which docs/RELEASE.md makes a step no release may skip; the pinned tagged clone remains the immutable option for auditors. The honest list of what is and is not proven is docs/KNOWN-LIMITS.md inside the installed BrotherMode folder, readable right here without visiting the repository.
- What is true about their records: your project's records are the one place a project's real status lives; CANVAS.md, the page that shows where the project stands, and the delivery packet are all generated from those records for reading, never edited by hand and never the source of truth themselves.
- Your data is yours: ask for an export and BrotherMode writes everything it has recorded about your project to one file you can keep. Ask for a purge and BrotherMode permanently erases the project's data, keeping only the record that a purge happened, so a deletion can never hide itself.

---

## Maintainer note, not for the reader above

Kept verbatim from where it used to sit at the top of this file. It was moved on 2026-08-29 because it was the first thing anybody read: the team reported finding fifteen commands and every one of them declaring itself a legacy compatibility shim, which reads as an abandoned product. The mechanism is unchanged and nothing was removed.

> DOCUMENTATION NOTICE, 2026-08-11 (V3 Final, task A2). This command file is not part of the six-name public surface. It keeps working exactly as it does today and is not deprecated in behaviour; only its documented status changed. Physical consolidation of these shims is a later tranche, so nothing here is removed in this release.

> LEGACY v2 COMPATIBILITY SHIM (the founder's 2026-08-07 night rename decision, recorded in this project's working history rather than a file this repository ships). Legacy surface: `/brotherme-help` under the pre-rename `brotherme` plugin id. Replacement: `/brothermode:help` at `skills/help/SKILL.md`. Reason: the founder's 2026-08-07 night namespace rename retired the flat `commands/` layout as the canonical public surface; this file is kept, unchanged below, only so a v2 install or a v2 habit still resolves during the migration window. Test: `tools/test_bm.py`'s `TestTheSeventhCommandAndTheDeepTourAreWired` (the fifteen-command inventory pin) and the naming/ACTIVE_DOCS scan in `tools/test_bm_docs.py` still exercise this exact file and path; do not rename or delete it without updating both. Removal condition: the v3.0.0 tag, at the release court described in freeze answer 14, once `claude plugin validate` and a repository grep show no live consumer of `/brotherme-help` remains.

# AGENTS.md

This file is the machine facing entry point to this repository. It is for an
agent (or an engineer moving fast) working IN this repo, not for someone
deciding whether to install Brother. That page is `README.md`.

## What this repository is

Brother is a marketplace and router over three products that each stand on
their own: BrotherMode (execution provenance), BrotherSBE (change assurance),
BrotherDS (claim verification, experimental, nothing to install yet). Stage 0,
today: this repository is the marketplace and router only, no product code has
moved here. Full detail: `docs/CHARTER.md`.

## The six verb router

Every ask in Brother resolves to one of six verbs, answered by whichever
product the work needs. An agent adding a command, a skill, or a doc page
should fit it under one of these rather than inventing a seventh.

| Verb | Means |
| :-- | :-- |
| start | begin or resume, no menu |
| status | where things stand right now |
| next | the one recommended action |
| review | judge the work against its bar |
| deliver | close with evidence in hand |
| help | orient, plain language |

`/brother` is the one door: type it, or describe the work in plain language,
and it routes to the right product's verb. Bare `/brother` explains itself in
one sentence and asks the one question that routes it; it never prints a
menu.

## The evidence law

No completion claim stands without a verifying command that ran after the
last edit, with its output quoted next to the claim. This binds every change
in this repository, including a change an agent makes here. A number nobody
can reproduce is not evidence. Full statement: `docs/CHARTER.md`.

## PASS, FAIL, NO-DATA

Every check an agent runs, or reports the result of, answers exactly one of
three ways:

- **PASS.** The evidence supports the claim.
- **FAIL.** The evidence contradicts the claim.
- **NO-DATA.** The evidence has not arrived yet. Never a pass, never a hard
  block.

An agent that quietly upgrades NO-DATA to PASS to make a report look clean is
lying about what it knows. An agent that treats NO-DATA as an automatic FAIL
punishes a check for being new. Neither is acceptable here.

## Surface caps

This repository does not grow a new front door for every feature. The numeric
surface caps that once bounded skills, commands, agents, and hooks were
withdrawn 2026-08-22 in favor of the architecture of record (Option B: one
repository, three plugins, one marketplace, see `docs/CHARTER.md`), whose C4
criterion freezes the tool surface: no existing skill or command is renamed,
and no new public command lands without the founder deciding to add one on
purpose. `tests/test_surface.py` verifies the structural shape this repository
commits to (license, no self firing CI, the marketplace catalog, the layout),
not a headcount. Run it with `/usr/bin/python3 -m unittest tests/test_surface.py`.

## Before you claim done

1. Run the nearest existing check for what you touched. `sh scripts/check_all.sh`
   runs everything this repository ships, each reporting its own exit code.
2. Quote the command and its output next to the claim. Never write "done" or
   "fixed" without a verifying command that ran after your last edit.
3. If a check cannot run (missing evidence, not yet wired), report NO-DATA by
   name rather than skipping the line silently.

## Where things are

- `README.md`: the install line, the one door, the receipt, the internal
  product names.
- `docs/CHARTER.md`: the chain, the unit, the verdict tuple, the evidence law.
- `docs/MERGE-PLAN.md`: the staged plan and every decision behind it.
- `docs/VERSIONING.md`: the release contract, the three products' current
  versions, the Stage 0/1/2 gates.
- `docs/_STYLE_GUIDE.md`: the writing rules this repository's prose follows.
- `docs/reference/COMMANDS.md`: every command this repository or its two
  sibling products answer, one table.
- `PROJECT.md`: canonical path, remote, and this repository's own checks.
- `tests/test_surface.py`, `scripts/cleanse.sh`: the files that make the
  structural shape and the client name and dash scans mechanical instead of
  remembered.

## No attribution

Sole author: Khalil Maaouni. No other name, no other organization, and no
attribution to any tool that helped write this repository appears anywhere in
it, by policy, from the first commit. An agent committing here never adds a
co-author trailer, a generated-by footer, or a credit line naming the tool
that produced the change.

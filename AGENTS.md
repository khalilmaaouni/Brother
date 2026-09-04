# AGENTS.md

This file is the machine facing entry point to this repository. It is for an
agent (or an engineer moving fast) working IN this repo, not for someone
deciding whether to install Brother. That page is `README.md`.

## What this repository is

Brother turns a plain-language outcome into checked work, a rerunnable
receipt, useful local memory, and a human acceptance decision. It is a
marketplace and router over three products that each stand on their own:
BrotherMode (execution provenance), BrotherSBE (change assurance), BrotherDS
(claim verification, experimental). The product code lives here, under
`products/`, alongside the umbrella bundle in `bundle/`. Full detail:
`docs/CHARTER.md`.

This repository supports two clients. Claude Code is the one it grew up on
and the one every release is proven against. Codex reads the same repository
through this file and the same `SKILL.md` files, and the section below says
where the two differ.

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

`/brother` is the one door. In Claude Code, type `/brother <outcome>`, or
describe the work in plain language, and it routes to the right product's
verb. In Codex, invoke the brother plugin's skill: it is the same door, and
the file it reads is `skills/using-brother/SKILL.md` under the brother plugin
root (`bundle/skills/using-brother/SKILL.md` in a checkout of this
repository). Do not translate a Claude slash command into a Codex one; there
is no `/brother` command in Codex, there is the skill, and the skill carries
the whole routing order. Bare, with no outcome named, the door explains
itself in one sentence and asks the one question that routes it; it never
prints a menu.

## The evidence law

No completion claim stands without a verifying command that ran after the
last edit, with its output quoted next to the claim. This binds every change
in this repository, including a change an agent makes here. A number nobody
can reproduce is not evidence. Full statement: `docs/CHARTER.md`.

## The receipt contract

A run that changes anything owes a receipt, and the receipt is the product,
not a log of it. Every run that reaches the end writes `receipt/receipt.json`
inside its own run directory and prints that path as its last line. The
receipt names every changed file, every check with the exact command that ran
it and that command's exit code, who wrote the check, the engine revision
that ran it, and the cost fields the worker returned. A field the run cannot
answer says NO-DATA and why, never nothing.

Two refusals hold that contract in place, and an agent working here must not
route around either. A delivery record carrying no per-file checks, or a
malformed one, is refused before it is written. A run that cannot write its
receipt exits non-zero and says so rather than returning 0. A command that
passed before the work, a unit that changed no file, and a test that still
passes after its dependency is reverted all prove nothing, and Brother
reports NO-DATA for them. Prove both with `python3
scripts/test_receipt_door.py` and `python3 scripts/test_brother_run.py`.

## The hard gates

BrotherSBE runs five gates over a change, and each answers on its own
evidence rather than on a reviewer's mood: **numbers** (a reported figure
carries its derivation), **migration** (a schema change carries forward and
reverse evidence), **approval** (a change that needs a person's decision
carries it), **ran** (a check that claims to have run carries the receipt
proving it), and **proof** (a stated behaviour carries the check that
verifies it). Run them with `python3 products/brothersbe/tools/sbe_gate.py .
--strict` from this repository's root. A gate with nothing to read reports
NO-DATA and does not block; that is the honest answer, and `--strict` still
lets it through for exactly that reason.

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

## The laws a session in this repository keeps

These are not style preferences. Each one exists because breaking it cost
something here already.

- **Never print or commit a private term.** This repository is exported to a
  public one. The terms that may never appear in it are listed in a file that
  lives outside every repository, and `python3 scripts/private_terms_scan.py`
  is what reads it. Run it over your own range before pushing. It never
  prints the hit itself, and neither do you: naming the term in a report or a
  commit message is the leak the scan exists to prevent.
- **Never force push.** A rewritten history strands every receipt bound to a
  commit that no longer exists. Merge forward instead.
- **Regenerate, never hand-edit, anything generated.** A product's manifest
  comes from that product's own `scripts/checksums.sh`, run from that
  product's root, after `git add` and staged again afterwards, because the
  script rewrites the file on disk after the first add. The bundle's runtime
  mirror comes from `python3 scripts/bundle_runtime.py`. `SYSTEM.md` comes
  from `python3 scripts/system_doc.py`. Each has a `--check` mode; a
  hand-edited copy fails it.
- **No em dashes and no en dashes, anywhere.** Commas, colons, parentheses.
- **No attribution.** See the last section of this file.

## The shipped skills

Every skill this repository ships is one `SKILL.md` under a plugin's
`skills/` directory, in the format both clients read: YAML frontmatter
carrying `name` and `description`, then the instructions as prose. They are
self contained by rule, which `python3 scripts/test_skills_portable.py`
enforces: no skill names a `~/.claude` path, none names a file that does not
ship beside it, every one that names `${CLAUDE_PLUGIN_ROOT}` also names
`${BROTHER_PLUGIN_ROOT}` for the client that sets that instead, and none
tells a reader to type a slash command without giving a route that works
without one.

- `bundle/skills/using-brother/` is the one door, the routing order behind
  it, and the closing ceremony.
- `products/brothermode/skills/` is the execution surface: the six public
  verbs (`start`, `status`, `next`, `review`, `deliver`, `help`) plus the
  advanced set (`auto`, `auto-status`, `stop`, `brief`, `decisions`,
  `handback`, `handover-pack`, `update`, `view`, `doctor`, `brotherme`,
  `cursor-dispatch`, `cursor-execute`).
- `products/brothersbe/skills/` is the assurance surface: `start`, `status`,
  `next`, `review`, `verify`, `help`, `kickoff`, `design`, `work`, `adopt`,
  `learn`, `handover`, `prove-this-change`, `spec-and-data-prep`.
- `products/brotherds/skills/brotherds/` is claim verification, experimental.

## Codex: what works, and the one gap

Codex reads this file and reads the same `SKILL.md` files, so the
instructions above are the instructions for a Codex session too. Two
differences are real and neither is cosmetic.

The plugin root arrives under a different name. Claude Code exports
`${CLAUDE_PLUGIN_ROOT}`; a Codex install exports `${BROTHER_PLUGIN_ROOT}`.
Both name the same directory, every shipped skill now says so in its own
text, and on a clone install neither is set, which means running the same
commands from the checkout root.

The gap, open and named rather than papered over: seven BrotherMode skills
(`auto`, `deliver`, `handback`, `handover-pack`, `start`, `stop`, `update`)
carry `disable-model-invocation: true` in their frontmatter, which is how
Claude Code is told not to fire them on its own. The canonical Codex package
validator refuses a plugin carrying that value, one error per skill: "skill
`auto` frontmatter field `disable-model-invocation` must be false". Deleting
the key would make Codex validation pass and would silently change Claude
behaviour, so nobody has deleted it. Until a vendor adapter resolves it, a
Codex package built from those skills does not validate, and that is a
release blocker, not a NO-DATA.

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
- `docs/VERSIONING.md`: the release contract, the three products' current
  versions, the Stage 0/1/2 gates.
- `docs/codex/PACKAGE-SHAPE.md`: the Codex plugin package this repository
  ships beside the Claude one, and the validator that decides it.
- `docs/for-engineers/CHEATSHEET.md`: what to type, and which answer deserves
  trust.
- The prose rules are short enough to carry here rather than cite: no em or
  en dashes anywhere (commas, colons, parentheses instead), no attribution to
  any tool or model, and every verdict is one of PASS, FAIL or NO-DATA.
- `PROJECT.md`: canonical path, remote, and this repository's own checks.
- `tests/test_surface.py`, `scripts/cleanse.sh`: the files that make the
  structural shape and the client name and dash scans mechanical instead of
  remembered.
- `scripts/test_skills_portable.py`: the check that keeps every shipped skill
  readable by a client other than the one it was written on.
- `SYSTEM.md`: a generated description of this system, which is why it cannot
  drift. Regenerate it, never edit it.

## No attribution

Sole author: Khalil Maaouni. No other name, no other organization, and no
attribution to any tool that helped write this repository appears anywhere in
it, by policy, from the first commit. An agent committing here never adds a
co-author trailer, a generated-by footer, or a credit line naming the tool
that produced the change.

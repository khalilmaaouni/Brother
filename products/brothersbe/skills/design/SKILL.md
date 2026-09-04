---
name: design
description: "Use when someone is deciding or reviewing the shape of a system before it gets built: what it is for, how the process runs, what the architecture and the data model look like, and how it will be verified. Runs the six design phases in order, each gating the next, and checks the dossier for the artifacts the tier requires. Fires on the work itself: start already routes into it once kickoff has scored the intake, so nothing needs to be typed to reach it."
---

Plugin root: a Claude Code install exports `${CLAUDE_PLUGIN_ROOT}` and a Codex install exports `${BROTHER_PLUGIN_ROOT}`; both name this plugin's own directory, so read whichever variable appears below as the one your client set. On a clone install neither is set: run the same commands from the checkout root instead.

# Design

Design comes before verification. The expensive mistakes are made while deciding what to
build, how the process runs, what shape the system takes, and how the data is modeled.
Checking the result at the end catches none of them.

## Load the law for the phase you are in, and not the rest

Read `${CLAUDE_PLUGIN_ROOT}/SKILL.md` first, then load only what the routing table sends you
to:

| You are doing this | Read |
|---|---|
| purpose brief, process map | `${CLAUDE_PLUGIN_ROOT}/references/phases-purpose-and-process.md` |
| architecture, technology map, data model | `${CLAUDE_PLUGIN_ROOT}/references/phases-architecture-and-data.md` |
| diagrams, dossier documentation | `${CLAUDE_PLUGIN_ROOT}/references/phase-expression.md` |
| tiering a task, checking required artifacts | `${CLAUDE_PLUGIN_ROOT}/references/laws-tier-and-artifacts.md` |
| writing or reviewing an ADR, data model or diagram | `${CLAUDE_PLUGIN_ROOT}/references/laws-design-artifacts.md` |
| consulting a decision table | `${CLAUDE_PLUGIN_ROOT}/references/laws-decision-tables.md` |

## The artifacts

Templates live in `${CLAUDE_PLUGIN_ROOT}/templates/dossier/`, numbered in the order they are
written: 01 purpose, 02 process, 03 ADR, 04 technology map, 05 data model, 06 diagrams,
07 verification. The tier decides which are required. An architecture decision is taken from a
decision table with named criteria and thresholds (`${CLAUDE_PLUGIN_ROOT}/tables/`), and the
ADR records the recommendation, the first and second alternative, and what would flip it.

## The completeness check

```
"${CLAUDE_PLUGIN_ROOT}/bin/sbe" design --strict <dossier-or-repo-root>
```

An empty directory reports NO-DATA naming why, never "clean". A declared dossier root holding
no dossier is a FAIL, not silence.

## Stop conditions

L6's four forcing conditions apply inside every phase: an ambiguity that would change the
design, a contradiction between what was stated and what the code or data shows, a collision
with a hard gate (money, partner data, personal data, production state), or a disproven
assumption. Any one of them stops the work mid-artifact and asks, in the fixed checkpoint
shape: what I found, my recommendation, the alternatives, the one decision I need, and what I
will do if you say nothing.

## Invoking it on purpose

This skill is meant to arrive on its own, which is the whole point of it.
Invoke as /brothersbe:design. That is the deliberate way in, for somebody who wants it; it is not the way most people will meet this.

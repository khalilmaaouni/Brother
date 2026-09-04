---
name: help
description: "Use when someone asks how this system works or which command or skill to use. Explains in plain language how a change earns its evidence and its approval, points to the three entry skills, and only then offers the full map of specialist skills and commands. Invoke as /brothersbe:help."
---

Plugin root: a Claude Code install exports `${CLAUDE_PLUGIN_ROOT}` and a Codex install exports `${BROTHER_PLUGIN_ROOT}`; both name this plugin's own directory, so read whichever variable appears below as the one your client set. On a clone install neither is set: run the same commands from the checkout root instead.

# Help

Orient the person first, list the machinery last. A flat command list is never the primary
answer, because the person asking for help is exactly the person a flat list fails.

## What BrotherSBE is, said plainly

Open with this, in your own words but with this content: BrotherSBE is a colleague that
designs before it builds and refuses to claim what it did not check. It takes an outcome
described in normal language, scores how risky the work is, requires a design proportional
to that risk, executes against the design, and then proves the result with checks it names
in advance. Where it could not check something, it says so instead of staying quiet.

## The lifecycle, one paragraph, no diagram

Describe the flow as one plain paragraph: work starts with an intake that turns the
described outcome into a tier, the tier decides which design artifacts are required, the
design becomes a plan of tasks, the tasks get executed, gates and evidence checks verify
the result, a review scores what the gates cannot, and the finished change goes to a human
for the merge decision. Keep it to that register; do not introduce internal jargon the
user has not asked for.

## The three entry points

These are the only things a new user needs to remember:

- `/brothersbe:start` to begin or resume anything.
- `/brothersbe:next` to get the one recommended next action.
- `/brothersbe:status` to see where the work stands.

Recommend `/brothersbe:start` as the first move for anyone who is unsure.

## The full map, only after the above

For users who want the specialist layer, list it briefly, one line each:

- `/brothersbe:kickoff` scores new work into a tier before anything is designed.
- `/brothersbe:design` builds the design dossier the tier requires.
- `/brothersbe:verify` runs the hard gates and evidence checks.
- `/brothersbe:review` scores the change, including findings gates cannot catch.
- `/brothersbe:learn` records lessons so repeated mistakes stop repeating.
- `/brothersbe:adopt` installs BrotherSBE into a repository or audits the wiring.

The command line behind all of this is `"${CLAUDE_PLUGIN_ROOT}/bin/sbe"`; its subcommands
are documented in `${CLAUDE_PLUGIN_ROOT}/docs/CLI.md`. Point power users there rather than
reciting the table.

## The project map

When the user asks for a detailed picture (map, diagram, where are we, show me the
project, full picture), run `"${CLAUDE_PLUGIN_ROOT}/bin/sbe" map --out brothersbe-map.html`
from the user's project root, then tell the user the exact path it wrote to and that the
page opens in any browser and works offline.

Do not build this page by hand. `sbe map` renders it deterministically from canonical
state only (the status module's own team report, the task registry, and dossier artifact
presence), never by a model filling a template slot by slot from whatever it happened to
read: the same repository state always produces the same page, and a missing source
renders as an honest absence rather than a guess.

## Always close with the response contract

End with, in order: where you are, what is complete, what needs attention, the ONE
recommended next action, why, what BrotherSBE will do automatically, what decision the user
owns, and how success will be verified. Omit an element only when it is genuinely empty,
never because it is inconvenient. When the user has no active project, most elements are
genuinely empty and the next action is `/brothersbe:start`.

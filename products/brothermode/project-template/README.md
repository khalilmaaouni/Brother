# <Project name>

What this is: the top-level explanation of the project, in plain language, for
whoever opens this folder first, human or AI. Who reads it: a founder deciding
whether this project is still worth its keep, a new engineer or a new AI
session getting oriented, and future you in six months who has forgotten the
details.

Copy this whole `project-template/` folder to start a new project. Replace
every `<placeholder>` before you consider the copy finished. Delete this
paragraph once you have.

## What this is, in one paragraph

<One paragraph, no jargon. Say what the thing does and who it is for, the way
you would explain it to a friend who is not technical. Worked example: "This
is a small tool that lets members of our fitness app download their own
workout history as a spreadsheet file, because right now the only way to see
your history is to scroll through it in the app, one entry at a time.">

## Why this exists

<Point at the intake document instead of restating it here, so there is one
place the reasoning lives, not two that can drift apart. Worked example: "The
full reasoning, including who asked for this and what would make us stop, is
in INTAKE.md.">

See `INTAKE.md` for the problem this solves, who has that problem, and the
conditions under which we would stop building or retire it.

## Where things are

`PROGRESS.html` is this project's progress page: open it in a browser to see
where the work stands. Refresh it at every closed loop, and keep it inside its
brevity budget (300 characters for the summary line, 250 for a card, 240 for
an evidence line), which is part of the contract, not a style note. Anything
longer is moved into the collapsed history at the foot of the page, never
deleted. The rule and its comment live in the file itself and in
`references/status-view.md` in the main BrotherMode repository.

See `STRUCTURE.md` for a full map of every file and folder in this template
and what belongs in it. The short version: `ARCHITECTURE.md` for the one-page
technical map, `decisions/` for why past choices were made, `docs/` for the
fuller requirements, process, QA gate, and data model detail.

## How a project here starts and ends

Two documents bracket every project built from this template:

- `CANVAS.md` is filled in at kickoff, before real work starts. It is the
  one-page agreement on the outcome, what is included and excluded, the
  success checks, and the initial forecast. Once you approve it, it is the
  project's source of direction.
- `DELIVERY-PACKET.md` is filled in at delivery. It says what was actually
  delivered, how it was verified after the final edit, what an independent
  review found, and how to roll back if something goes wrong.

Everything between those two documents (the day-to-day files above) exists to
get honestly from the first one to the second one.

## How to run this, verified

<Every command here must actually have been run and its output checked before
it goes in this file. Never invent a command. Worked example:
"`python3 -m pytest tests/ -q` runs the test suite; it should print `N passed`
with zero failures.">

- Build: `<the exact command, copied from wherever it is actually defined>`
- Test: `<the exact command>`
- Run locally: `<the exact command>`

## Status, stated plainly

<Say where this actually stands today, not where the plan says it should be.
Worked example: "Early build. The export script works for one account, run by
hand. Nothing is self-service yet.">

## When this project ends

See `docs/SUNSET.md` (in the main BrotherMode repository, or wherever your
copy of this method lives) for how to retire this project or a feature of it
without breaking whoever depends on it. `INTAKE.md` names the earliest signal
to watch for; `SUNSET.md` is the full process once that signal shows up.

# 0001: Example decision, delete this file's content and write your first real one

What this is: one decision, in its own file, so the record explains itself
later without anyone having to reconstruct the reasoning from a chat log or a
git blame. Who reads it: future you, a teammate, or an AI session trying to
understand why the code looks the way it does instead of some other way.

Rules for this folder: one decision per file, numbered in order
(`0001-`, `0002-`, ...), never renumbered or reordered once written. A later
decision that reverses an earlier one gets its own new file that says so and
names which file it supersedes. Never edit an old decision file to make it
agree with a later choice; that is exactly the history this folder exists to
keep.

---

## Decision

Use a plain CSV file for the workout export, not a PDF or an Excel file.

## Status

Decided, 2026-01-10.

## Context

The export feature (see `../INTAKE.md`) needs to hand a member their own
workout history in a file they can open somewhere else, most likely a
spreadsheet tool.

## The why

CSV opens in every spreadsheet tool, including free ones, on every operating
system, and needs no library beyond what is already in the standard toolkit
to generate. It is also the smallest possible commitment: if the export
feature is retired later (see `docs/SUNSET.md`), there is no proprietary
format lock-in to unwind.

## Alternatives considered

- **Excel (.xlsx) file.** Rejected: requires a third-party library to
  generate correctly, and the extra formatting it allows (columns, styles)
  is not something any of the six people who asked for export actually
  requested.
- **PDF.** Rejected: nobody asked to read this, they asked to reuse it in a
  spreadsheet or another tool. A PDF is the wrong shape for data meant to be
  imported elsewhere.
- **A JSON file.** Rejected for this audience: technically simpler to
  generate correctly than CSV in some ways, but the six people who asked for
  this are not engineers and would not know what to do with a JSON file
  without also being handed a converter.

## Consequences

Column order, once shipped, becomes something people may build their own
spreadsheet formulas against. Changing it later is a breaking change for
anyone who saved a template, so new fields get added at the end of the row,
not inserted in the middle, unless a later decision explicitly overrides
this one.

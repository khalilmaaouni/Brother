# Project Canvas

What this is: the one-page agreement about where this project is going, written
at kickoff, before any real work starts. Who reads it: you, before approving
the direction; every worker session, as the source of direction; and anyone who
later asks "why did we build it this way".

Fill in every `<placeholder>` during kickoff. The canvas is approved when you
say it is, and after that it is the project's source of direction: work that
contradicts it needs the canvas changed first, not quietly ignored.

A non-technical reader should be able to explain the chosen direction, the
major exclusions, and the success checks after reading only this page. If they
cannot, the canvas is not finished.

## Outcome

<What will exist, or be true, when this project is done. One or two sentences,
plain words. Worked example: "Members of our fitness app can download their own
workout history as a spreadsheet file, without asking us for it.">

## User

<Who this is for, and what they get out of it. Worked example: "App members
who want their history outside the app; today their only option is scrolling
one entry at a time.">

## Recommended direction

<The single direction being recommended, stated plainly. One recommendation,
not a menu. Worked example: "A download button on the profile page that
produces one spreadsheet file, built on the export code we already have.">

## Why this direction

<The reason this direction beats the alternatives that were considered. Name
the strongest alternative and the tradeoff. Worked example: "Reuses working
export code, so it is the fastest safe path. The alternative, a full reporting
page, would take roughly four times longer for value nobody asked for yet.">

## Included

<What is in scope. Short list. Worked example: "One-click download, all
history, spreadsheet format, works on phone and desktop.">

## Not included

<What is deliberately out of scope, so nobody quietly builds it. Worked
example: "No charts, no filtering by date, no automatic weekly emails.">

## Success checks

<How we will know it worked: checks a person can actually run, not vague
hopes. Worked example: "A member can download their file in under 10 seconds;
the file opens in a spreadsheet app; the numbers match what the app shows.">

## Main risks

<The few things most likely to go wrong, each with what we would do about it.
Worked example: "Largest accounts may be slow to export; if a test account
takes over a minute, we page the export instead of shipping it as is.">

## Decisions made

<Decisions already taken and approved, each with your recorded approval noted.
Worked example: "Spreadsheet format: CSV, approved 2026-07-31. Placement:
profile page, approved 2026-07-31.">

## Open decisions

<Decisions still waiting on you, each stated as a question with a recommended
answer. Worked example: "Should the file include deleted workouts? Recommended:
no, because members deleted them on purpose.">

## Initial forecast

<Always a range with a confidence level and the assumptions behind it, never a
single number. Worked example: "Likely 2 to 4 working days. Confidence: medium.
Assumes the existing export code works for large accounts; we reforecast after
testing that.">

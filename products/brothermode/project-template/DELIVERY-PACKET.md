# Delivery packet

What this is: the closing document of a project, written when the work is
delivered. It says what you got, how it was checked, and what to do if
something goes wrong. Who reads it: you, deciding whether to accept the
delivery; and anyone who touches this work later and needs to know what was
actually verified versus merely claimed.

Fill in every `<placeholder>` at delivery time. Every claim of "verified" in
this packet must point at a check that was actually run after the final edit,
not before it: evidence gathered earlier than the last change proves nothing
about what is being delivered.

The exit rule, stated as an instruction to whoever writes this packet: do not
present a delivery as "Ready to deliver" unless every required acceptance
check has evidence produced after the final edit, and an independent review
found no unresolved high-severity problems. If either is missing, say so at
the top of this packet instead of claiming readiness.

## What was requested

<The original ask, in the requester's own words where possible. Worked
example: "Let members download their workout history as a spreadsheet.">

## What was delivered

<What actually shipped, plainly. Worked example: "A Download history button on
the profile page that produces one CSV file of the member's full history.">

## What changed from the approved plan

<Every difference between the approved canvas and what shipped. "Nothing" is a
valid answer only if it is true. Worked example: "The button moved from the
settings page to the profile page.">

## Why it changed

<The reason for each change above, and who approved it. Worked example: "User
testing showed nobody looked in settings; move approved 2026-08-02.">

## Verification performed

<Each check that was run after the final edit, with its result. Name the
command or the action, not just "it was tested". Worked example: "Full test
suite run after the last change: all passing. Manual download on one large
test account: 8 seconds, file opens, totals match the app.">

## Independent review findings

<What a reviewer who did not write the work found, including the problems.
"No unresolved high-severity findings" only if the review actually happened.
Worked example: "Reviewer flagged a slow query on large accounts (fixed and
re-verified) and a naming inconsistency (accepted as is, noted below).">

## Known limitations

<What this delivery does not do, or does imperfectly, stated before anyone
discovers it the hard way. Worked example: "Accounts over 50,000 entries are
not paged yet; the largest known account exports in 40 seconds.">

## How to use the result

<The shortest honest instructions for the person receiving this. Worked
example: "Open your profile page, press Download history, and open the file in
any spreadsheet app.">

## How to roll back

<The exact way to undo this delivery if it misbehaves, verified to work, not
guessed. Worked example: "Revert the release commit and redeploy; done in
rehearsal on 2026-08-02, took 4 minutes.">

## What to monitor

<What to watch after delivery, for how long, and what reading means trouble.
Worked example: "Export error rate for two weeks; anything above 1 percent of
attempts means the paging limitation above is biting real users.">

## What remains optional

<Work that was considered and deliberately left for later, so it is a recorded
choice rather than a forgotten idea. Worked example: "Date filtering and
automatic weekly emails: both possible later, neither promised.">

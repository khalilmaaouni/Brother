Status: CURRENT.

# The handover, by hand

This is the whole handover procedure, written so you can run it with nothing
installed. No tool, no account, no repository of ours. If you never adopt
anything else here, this page on its own is worth having, and using it costs
you nothing and tells us nothing.

If you do want it automated, the command is at the foot. Read the procedure
first: the tool exists to save typing, and it is worthless if you do not agree
with what it types.

## What this solves

Work stops and restarts, across days, people, or sessions. Whatever picks it
up next knows less than whatever put it down. Almost everything expensive that
happens at that moment comes from the same three causes:

- Something was half finished and nobody said where it stopped.
- Something was left locked, reserved, or claimed, and the claim outlived the
  person who made it.
- Something was decided, and the reason was lost, so it gets re-litigated or
  silently reversed.

The procedure below exists to make those three things impossible to forget,
rather than easy to remember.

## The one rule that matters

A handover states plainly, in its first line, whether the work is FINISHED or
UNFINISHED. Not "mostly done", not a status colour, not a percentage. One of
two words.

This is the rule people skip, and skipping it is why handovers fail. A reader
who has to infer whether they are inheriting a finished thing or a live one
will guess, and half of them will guess wrong. The word costs nothing and
removes the guess.

If UNFINISHED, the same paragraph says exactly where the work stopped and what
the next person does first.

## The eight pages

Write these as eight files, or eight sections of one document. The order is
the reading order, and it is deliberate: a reader who stops after page two
should still have what they need to act.

**1. Read me first.** What to read, in what order, and any fact that changes
how the rest should be read. Two or three sentences. If there is a trap in
here, this is where it goes.

**2. The handover.** The substance:
   - What is DONE, and beside each item the check that proves it. Not "tests
     pass" but the command that was run and what it printed. A claim with no
     check beside it is a claim, and should be labelled one.
   - What is IN FLIGHT, and precisely where it stopped.
   - What is NOT STARTED that a reader might assume was.
   - Every lock, claim, reservation or lease still held, and how to release it.
   - Anything uncommitted, unsaved, or living in only one place.
   - Every question waiting on a person, with who that person is.

**3. Learnings and mistakes.** What this stretch of work taught, and what it
got wrong. Name the mistakes. A handover with no mistakes in it is not being
read as honest, it is being read as incomplete, and rightly.

**4. Rules and process changes.** What changed about how the work is done, and
for each one, whether anything actually enforces it. If nothing enforces it,
write UNENFORCED beside it. A rule that lives only in someone's memory is
worth writing down as a rule that lives only in someone's memory.

**5. What comes next, in priority order.** Not a list of everything. An
ordered list, with the reason for the order, and an effort range with your
confidence in it. Ranges, never single numbers.

**6. Where the memory lives.** Where the durable record of this work is kept,
so the next person does not rebuild what already exists.

**7. The close report.** The FINISHED or UNFINISHED line, and the state of
every loose end: locks released, schedules stopped, background jobs ended,
work saved somewhere durable.

**8. The next session prompt.** A paste-ready prompt for whoever opens the
next session, so continuing the work never depends on someone remembering to
write one by hand. Fill exactly four things: the goal of the next round in
one sentence; the decisions already made, so the next session does not
re-ask; the ordered work list, every item naming its files and its runnable
done-check; and what is frozen or blocked, and why. Nothing else on this page
needs writing by hand: the rest is generated, including the rules this
project has already paid for once and the facts that make the pack portable
to a different account on the same machine.

## The two halves

Most handover procedures cover only the leaving. That is why they decay: the
person leaving is the one with the least time and the least incentive.

**Closing half**, before work stops:
1. Write the eight pages, filling every section by hand. A generated skeleton
   can carry the facts; the judgement has to be written.
2. The close report opens with FINISHED or UNFINISHED.
3. Release every lock and claim, with a note saying what state you left it in.
4. Put the pages somewhere durable and shared, not only in a chat or a
   directory that gets cleaned.
5. Check your own work against the list above before calling it closed.

**Opening half**, before new work starts:
1. Read the newest handover, in its stated order.
2. Deal with what the previous person left: adopt or release every lock they
   held, and record which you did.
3. Confirm what they claimed. Spot check at least the claims you are about to
   depend on. A handover is evidence, not testimony.
4. State back, to whoever is waiting, what you adopted, what you released, and
   what you are doing first.

The opening half is the one that survives a crash. If somebody's work ends
without warning, no closing procedure runs at all, and the only thing standing
between that and lost work is the next person having a habit of looking.

## Doing it well

- Write for somebody who was not there. Every internal shorthand you use is a
  question they have to ask, and they may not be able to ask it.
- Put bad news first. A handover that buries the problem gets read as far as
  the good news.
- Never round UNFINISHED up to FINISHED because the remainder feels small. The
  remainder always feels small to the person who understands it.
- Prefer a short honest page to a long confident one.
- Do not hand edit a generated fact to look better. The only reason a record
  is worth more than a summary is that nobody with an interest in the answer
  touched it.

## If you want it automated

Everything above is what this tool does, and nothing it does is outside the
procedure above.

    python3 tools/bm_handover.py skeleton     # writes the eight pages, prefilled
    python3 tools/bm_handover.py verify-close # refuses a hollow or stale close
    python3 tools/bm_handover.py zip          # packages it to send
    python3 tools/bm_handover.py detect       # what the previous session left

The verify step is the part worth having: it refuses to agree that a handover
is complete when a section is still a placeholder, when the FINISHED or
UNFINISHED line is missing, or when locks are still held. It was itself found
to be wrong about all three of those once, by a review written to attack it,
and was repaired before it shipped. That history is in
docs/evidence/baton-ceremony/REFUTE-2026-08-11.md, and it is published for the
same reason this page exists: a procedure you cannot inspect is one you should
not trust.

Status: UNAUDITED reference as of 2026-08-06. This page sits outside the mechanical truth guards and is scheduled for the documentation sweep; treat any dated or numeric claim inside with care until then.

# How a project or a feature ends well

What this is: the retirement half of the method. Everything else in this
project helps something start and run. This file is the part that helps it
end on purpose, instead of by accident. Who reads it: the founder deciding
whether to retire something, and any AI session doing the retiring.

Before this file, nothing here addressed this at all.
`docs/REMAINING.md` named the gap directly (item 3, founder items B and F):
"Nothing about SUNSET or graceful evolution, which the brief asked for
explicitly. The system helps a project start and run; it does not help one
end well."

## Why this matters

Every project and every feature costs something to keep alive, even one
nobody is complaining about: it is a thing to read before changing something
nearby, a thing a new session has to understand before trusting the rest of
the code, a row someone has to explain in an audit. A project with no sunset
plan does not get maintained forever. It gets abandoned messily, usually
right when someone needs it retired cleanly.

## The signals that say something should be retired

Watch for these. Treat any one of them as a reason to open this file, not as
proof you must retire right away.

- **Nobody uses it.** Check usage the same way you would check any other
  claim: a log, a query count, a search of who calls it, not a guess.
  Silence for a stated period (name the period at intake, section 11 of
  `docs/INTAKE-TEMPLATE.md`) is the trigger, not a feeling.
- **Its cost now exceeds its value.** The cost side includes the engineering
  time to keep it working, the attention it demands from every future
  reader, and any money it spends running. Compare this against the value
  analysis written at intake (section 6): if the value never showed up, or
  has decayed, that comparison has already been done once. Redo it honestly
  rather than assuming the old numbers still hold.
- **Its founding assumption was disproven.** Something believed true when it
  was built turned out not to be true. This is the fastest, cleanest kill: no
  blame, the world changed or you learned something, act on it.

## How to retire something without breaking what depends on it

1. Find every consumer before touching anything: grep the codebase, check
   the tests, check any downstream project that reads this table, this file,
   or this API. "No writer" is not the same claim as "no reader." Check both.
2. If something still reads it, either that read path is moved first, or the
   retirement waits until the dependency is moved or itself retired. Never
   delete under a live consumer.
3. Delete in the same change that removes the last consumer, not before and
   not long after. A "temporarily unused" thing that sits around gets
   rediscovered as load-bearing by someone who never knew it was scheduled to
   go.
4. If the thing being retired is a promise written in a law or a spec (a
   clause in a rules file, a line in a requirements doc, a claim in a
   README), strike the clause in the same change that removes the code, and
   say so in the commit. A law that describes behavior the code does not
   have is worse than no law: it teaches the next reader, human or AI,
   something false.
5. Run the project's real gate commands after the deletion, the same ones
   you would run for any other change. A deletion is a change like any
   other. It gets the same proof, not an exemption because "it is just
   removing something."

## What to preserve

Keep the decision record and the lesson. Do not keep the code around "just in
case." Version control already keeps it recoverable, and dead code left in
place is read by every future session as if it might still matter.

- Write one entry into the project's decisions folder (see
  `project-template/decisions/0001-example-decision.md` for the format)
  naming what was retired, why, and what evidence justified it (the usage
  check, the cost comparison, or the disproven assumption).
- If the thing being retired taught a reusable lesson (a defect class, a
  pattern that looked right and was not), that lesson belongs in the
  project's lessons register too, not only in the decision note, so the next
  project does not relearn it the hard way.
- The one thing worth keeping about the code itself, if anything, is a short
  note of what it looked like and why it did not work. Not the code.

## How to tell whoever is affected

Retiring something silently is how trust erodes. Name it out loud,
proportional to who is affected.

- If it is an internal tool or an unused code path with no outside users,
  one line in the changelog or the commit message is enough.
- If a teammate, a downstream project, or a founder relies on it even
  occasionally, tell them directly before the change lands, not after, and
  give them the decision note so they can see the reasoning, not just the
  outcome.
- If it is a feature a real user sees or paid for, the notice is the same
  courtesy you would want if a tool you depended on vanished: what is going
  away, when, and what to do instead if there is an alternative.

## How to leave the door open for a future revival

A sunset is not a claim that the idea was wrong forever, only that it is not
worth carrying right now. Keep the door open cheaply.

- The decision note should state what would need to be true for this to
  make sense again (more demand, a cheaper way to build it, the disproven
  assumption becoming true again).
- Keep the intake document that started it findable, so a future revival
  starts from the original problem statement instead of reinventing it from
  a vague memory of "we tried this once."
- Never delete the historical record even when you delete the code. Version
  control and the decisions folder both survive a code deletion on purpose.

## Checklist

- [ ] Found every consumer (reader and writer, not just writer)
- [ ] No live consumer depends on this anymore, or its dependency was moved
      first
- [ ] Deleted in one change, not staged as "unused for now"
- [ ] Any law, spec, or README clause describing this was struck in the same
      change
- [ ] The project's real gate commands still pass after the deletion
- [ ] One decision note written: what, why, what evidence
- [ ] Any reusable lesson moved into the lessons register
- [ ] Whoever is affected was told, proportional to the impact
- [ ] The decision note states what would justify revisiting this later

## Grounded in this project's own sunset events (2026-07-26)

Three real retirements happened in this same project on the same day this
document was written. They are more concrete than any invented example would
be, and each was verified against the actual code or docs before being
written down here.

1. **A table with no writer was deleted.** The `deliveries` table (built to
   guarantee a handover payload is delivered exactly once) had no code
   anywhere writing to it, and `docs/KNOWN-LIMITS.md` had already committed
   in writing that a later phase would either wire it up or remove it. When
   that phase landed without wiring it in, it was deleted rather than left
   sitting unused, per the deletion comment at the top of the schema section
   in `tools/bm_store.py`.
2. **A law clause was struck because no code implemented it.** The rules
   file and the store's schema once promised that a fence past its time
   limit (`ttl_hours`) would be treated as released automatically. Nobody
   ever built the code that expired anything: a fence given a time limit of
   0.36 seconds still blocked a second claim a full second later, proven by
   running it, not by reading the code. The column, the CLI flag, and the
   reclaim branch were deleted, and the clause was struck from the law in
   the same change, because, in the words of that change itself, "a law that
   describes behavior the code does not have is worse than no law."
3. **A feature was removed by a rewrite.** When the work-tracking engine was
   rebuilt (the "V2" rewrite), checkpoint clash detection on tagged
   decisions had no equivalent in the new, ratified design. This was a
   feature removal, not a cleanup, and `docs/REMAINING.md` records it
   plainly as a loss rather than folding it quietly into "improvements,"
   because a stranger reading the project later deserves to know something
   was actually taken away, not just changed.

# Safe Unwatched Time (SUT)

The metric row S10 asks Brother to own, defined here so it exists before the
numbers are wanted. Source: `docs/plan/SWITCHING-STRATEGY-2026-09-04.md`,
section 7 zone 2, which defines SUT as

> The duration and consequence level of engineering work a user is willing to
> delegate without active supervision while preserving correctness evidence,
> scope control, recoverability, and human authority.

Instrument: `scripts/safe_unwatched_time.py`. Self-test:
`scripts/test_safe_unwatched_time.py`.

## The definition, made mechanical

**Safe unwatched time is the longest span a run went with no human present
during which every claim it made stayed true.**

It is measured from the run's own records, never from a person's memory of the
night. Three files carry the whole measurement, and all three are written by
the engine while it runs:

| Record | What it supplies |
| --- | --- |
| `journal.jsonl` | the timeline. Every event carries `at`, an ISO 8601 timestamp with a timezone, so the span has real endpoints. |
| `claims.json` | one entry per unit: `claimed_at`, `released_at`, `state`, and the `evidence` dict holding `check_command` and `exit_code`. |
| `receipt/receipt.json`, or the journal's `receipt.issued` event | how many receipts were issued and how many of them were left unproven. |

The span opens at the run's first recorded event and closes at the first break
below, or at the last recorded event when nothing broke.

## What breaks the span

Four conditions, each read off the record rather than judged:

1. **A claim later refuted by its own check.** An `evidence.verified` event
   whose `check_exit` is not 0, an `integrate.refused` whose reason names the
   unit's own check failing, or a claim whose `evidence.exit_code` is nonzero.
   The agent said a thing was proven and its own proof disagreed.
2. **A receipt carrying NO-DATA where a PASS was claimed.** A `receipt.issued`
   event with `unproven` above zero, or a receipt whose state is unproven while
   its claim state is settled. An unproven receipt is not a small receipt, it
   is an absent one, and this estate's standing rule is that NO-DATA is never a
   pass.
3. **A write outside declared scope.** An `integrate.refused` whose reason names
   a path the unit never declared (the QUARANTINE shape), or a refusal on the
   ground that canonical was dirty. Scope control is one of the four
   preservation properties in the definition, so losing it ends the span even
   when the engine caught it.
4. **A check that never ran.** A claim in the settled state whose evidence
   carries no exit code at all. A unit closed on no check is the cheapest false
   green there is.

The span ends at the FIRST of these, in timestamp order, whichever kind it is.

**A break caught by the engine still ends the span.** Quarantining a stray file
is the system working, and it is exactly why this number can be measured at
all, but the span measures how long the run went without needing a person, and
a quarantine is a thing a person then has to adjudicate. Catching a break makes
the number honest; it does not extend it.

**A crash that resumes cleanly does not break the span.** A `run.resumed` event
is not on the list above. Row E73 proved a killed run resuming from disk with
nothing lost and nothing duplicated, and a recovery the engine performed on its
own is continuity, not a failed claim. A resume that loses or duplicates work
shows up as one of the four breaks anyway, through the record it corrupts.

## The units

Two, always reported together, because either one alone lies:

- **Minutes of wall clock**, from the first recorded event to the break or to
  the last event. Wall clock, not model time: the question is how long a person
  could have been asleep.
- **Units of work**, the count of units that closed inside that span. A long
  span holding no finished work is a long idle, and the second figure is what
  tells those apart.

Reported as one line:

```
safe unwatched time: <minutes> min over <units> units, broken by <reason or none>
```

`NO-DATA` and the reason for it when the run carries no timestamps or no
receipts. NO-DATA is never a pass, and never a zero: a run nobody recorded is
not a run that was safe for zero minutes, it is a run nothing can be said
about.

## What is NOT measured

- **The quality of the work.** SUT says every claim held. It says nothing about
  whether the code was any good, whether the design was right, or whether the
  outcome was worth doing. A run that safely and provably ships a bad decision
  scores exactly as well as one that ships a good one.
- **The consequence level.** Section 7 zone 2 names duration AND consequence,
  and this instrument measures the duration half. Consequence is a property of
  the workload family the run belongs to, declared with the benchmark that
  commissions the run, not derivable from a journal.
- **Human interventions, unless capture was on.** A person acting on a run is
  recorded as an `operator.intervened` journal event, written through the one
  append only journal writer (`python3 scripts/safe_unwatched_time.py <run>
  --record ACTOR ACTION EFFECT`). Its payload is a closed vocabulary of three
  fields (actor class, action kind, effect) and nothing else, so a prompt body,
  a path or a secret cannot be stored; time and run id are the journal's own.
  An event whose effect is `changed_run` is a fifth break, "a person changed
  the run", and closes the span at its timestamp; a status read whose effect is
  `none` is counted and does not break; an operator event that does not
  validate also breaks, because nobody can tell whether it changed anything.
  The older free text `human.intervention` event breaks the same way. The
  figure is strict only when the run also carries an
  `intervention.capture_on` marker (`--mark-capture`, at run start), the
  statement that a human change to this run would have been journaled. A run
  without the marker, which is every run recorded before this change, reads
  `upper_bound` true and its line ends in `(upper bound)`: the instrument could
  not see a person, so it treats the run as unattended. One ceiling stays: a
  person who acted on a captured run without recording it cannot be detected
  from the record, and a marker written late in a run vouches for the whole
  span, so the launcher must write it at the start.
- **Anything about a run that left no journal.** See NO-DATA above.

## Reading a figure honestly

A number is meaningless without the corpus it came from. Quote the run
directory beside every figure, the way this estate quotes the tree beside a
benchmark score. One run is an anecdote with a timestamp; the metric only
starts meaning something across a family of runs, which is what row S10's
`visible_when` already says.

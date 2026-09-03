---
name: prove-this-change
description: "Use when someone is changing an API response or request shape, an event or message contract, a partner or third party integration, a retry, timeout, or idempotency path, a queue consumer, a service boundary, a warehouse model, a SQL transformation, a dbt model, an ELT or ingestion step, a table schema, or a backfill, or when they ask whether a change will break something and what proof a reviewer will want. Fires on the work itself, with nothing started and no command learned. No BrotherSBE session, dossier, or setup required."
---

# I am changing a service, a contract, an integration, or a warehouse model

The person has a backend or a data change in front of them. Give them the
shortest honest path, in their words, and let the tools do the reading.

## Say this first, in one line

What the change breaks if it is wrong, and for whom. For a contract change
that is nearly always: which consumers read the field being altered, and
whether old and new can run side by side during the deploy. For a warehouse
model that is nearly always: who reads this downstream, and does the grain
stay the same.

## The shortest real path

Work on a branch and commit as normal, then:

```bash
sbe review-route          # who should look, derived from the diff
sbe impact                # what it touches, and what a diff cannot tell you
```

Neither takes a flag. Both are rules reading the diff, not a model guessing,
so they answer the same way every time and can be argued with.

Then, when there is something to prove:

```bash
sbe evidence run --out .sbe/evidence/<name>.json -- <the command that proves it>
python3 tools/sbe_report.py .
```

The report is the page a reviewer reads. It cannot fail a build.

## What this work usually needs proven

Name only what the change earns.

### A service, contract, or integration

- **Compatibility**: a removed or renamed field, a narrowed type, or a new
  required input is a breaking change until a consumer check says otherwise.
  Expand first, migrate consumers, contract last.
- **Idempotency and retries**: the same message delivered twice does not
  charge, ship, or insert twice. This is the single most common defect in
  partner integrations and it is cheap to prove with a test.
- **Partial failure**: what the caller sees when the downstream call times out
  halfway, and whether state is left recoverable.
- **Authorization**: every new path enforces it, including the error paths.
- **Rollback**: the exact way back, written before the way forward is merged.

### A warehouse model, pipeline, or migration

- **Grain and keys**: the row meaning did not silently change, and the key is
  still unique. A count and a duplicate check both belong in evidence.
- **Reconciliation**: totals before and after agree, or the difference is
  explained by the change itself rather than discovered later by a report.
- **Downstream consumers**: a diff cannot tell you who selects from this. That
  is the sentence to take to the data owner, and the tool says plainly that it
  could not measure it.
- **Backfill and re-run behavior**: running it twice does not double anything.
- **Migrations**: both directions rehearsed against a restore, with row counts
  that match, before it goes near production.

## Proving it, not just claiming it

Somebody will ask "how do you know". The coverage worth having, in the order
it usually gets skipped: the negative path (the input that is wrong, missing,
malformed, or hostile), partial failure (the call that dies halfway, and
whether it is recoverable without a human editing data), duplicates and
retries (the same request or message twice, nothing doubles), recovery (does
the run after the failure heal on its own), and boundaries (empty, one, many,
and the size where it stops being fast).

Two honesty rules make the evidence worth anything: a test that cannot fail
proves nothing, so force the condition, watch it go red, then fix it and
watch it go green; and absence of a result is not a pass, so a check that
opened no file reads NO-DATA, never PASS.

## What never happens here

Nothing runs against production. A production change is drafted as the exact
command with its rollback for a human to execute. Credentials are never typed,
stored, or logged. No number is asserted without the command that produced
it, and a check that examined nothing reads NO-DATA rather than PASS.

## If they want more

The full cards, with real captured output, are
`docs/cards/CARD-technical-change.md` for the service and contract side and
the warehouse and pipeline side alike, and `docs/cards/CARD-technical-qa.md`
for turning a run into a receipt a reviewer accepts. The command reference is
`docs/commands.md`. Neither card is required to do the work above.

## Invoking it on purpose

This skill is meant to arrive on its own, which is the whole point of it.
Invoke as /brothersbe:prove-this-change. That is the deliberate way in, for somebody who wants it; it is not the way most people will meet this.

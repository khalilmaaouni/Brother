# 08. Behaviour table

## What this is

01-purpose.md states what settlement batch loading must guarantee, and
07-verification.md already lists the check behind each guarantee. The rows
below transcribe those same statements into this repository's fixed row
contract. No row states anything beyond what the dossier already says.

## Rules

| ID | Starting point | Trigger | Required outcome | Proof |
|---|---|---|---|---|
| B1 | A settlement file already loaded as a batch | The same file is loaded a second time | One batch row exists for that file, not two | Idempotency test running the extract twice on one file and asserting one batch row, every pull request |
| B2 | A settlement file load in progress | The load fails partway through | Zero rows land in staging for that batch id, so no partial batch is ever readable | Fault injection test failing the load midway and asserting zero rows for that batch id, every pull request |
| B3 | A batch that has finished loading | The batch completes loading | The batch total matches the file trailer | Reconciliation query comparing summed amount_cents to the trailer total, every load, blocking acceptance |
| B4 | The payout run selecting batches to pay | Payout reads batch state to decide what to pay | Payout reads only batches whose state is accepted | Query asserting no payout row references a batch whose state is not accepted, daily |
| B5 | The migration that introduces batch based loading | The migration is run against a restored copy | The migration reverses cleanly, with matching row counts before and after | Forward and reverse both executed against a restored copy, with row counts before and after, before the migration ships |

## What this does not do

This does not run the checks: it states the rule and hands the proof
obligation to 07-verification.md, where each check already lives.

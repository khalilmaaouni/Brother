# 07. Verification plan

| Claim this design makes | The check that proves it | When it runs |
|---|---|---|
| Loading the same file twice creates one batch | Idempotency test running the extract twice on one file and asserting one batch row | Every pull request |
| A partial batch never reaches staging | Fault injection test failing the load midway and asserting zero rows for that batch id | Every pull request |
| Batch total matches the file trailer | Reconciliation query comparing summed amount_cents to the trailer total | Every load, blocking acceptance |
| Payout reads only accepted batches | Query asserting no payout row references a batch whose state is not accepted | Daily |
| The migration reverses cleanly | Forward and reverse both executed against a restored copy, with row counts before and after | Before the migration ships |

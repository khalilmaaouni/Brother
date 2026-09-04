# 03. Architecture decision record

## Context
The current load overwrites the staging table, so a corrected partner file
destroys the record of what the original said. Payout reads that table. We need
loads that accumulate rather than replace, without making a re-sent identical
file into a duplicate batch.

## Criteria
Deploying teams = 2 (ledger and data platform). Consistency = strong; a batch is
visible to payout only after reconciliation commits. Ops maturity = medium.
Failure isolation = high; one bad partner file must not block the other partners'
loads for the night.

## Options considered

### Rejected: Keep overwriting and archive the raw file to object storage
Cheap, and it preserves the bytes without preserving the loaded rows. A dispute
would still require re-parsing an archived file by hand to see what the ledger
believed at the time, which is the work this design exists to remove.

### Rejected: Upsert rows keyed on the partner's record id
Avoids duplicates without a batch concept, and loses the correction history at
the row level: an updated amount overwrites the old one with no trace. It also
depends on the partner record id being stable across a correction, which two of
our partners do not guarantee.

## Decision
Append only batches. Each load writes a new batch id, keyed on the file's content
hash so an identical re-send is recognised and skipped. Payout reads only batches
that reconciliation has marked accepted.

## Consequences
Staging grows with every load rather than staying one file wide, so retention
becomes a real task. A correction is now two batches, and every consumer must
know to read the accepted one. The content hash means a partner who re-sends a
byte-identical file gets no new batch, which is correct and will surprise someone.

## What would flip this
If partners move to an incremental delta feed rather than a full nightly file,
revisit toward event level ingestion with batch boundaries derived from the feed
rather than from a file.

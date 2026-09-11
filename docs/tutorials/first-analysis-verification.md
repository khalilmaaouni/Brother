# Tutorial: your first verified analysis

Scenario: you need the number of active purchasing customers in the last 30 days for a commercial decision.

## 1. Write the semantic contract before querying

Define customer, purchase, cancellation/refund treatment, 30-day boundary/timezone, entity grain, and authoritative source.

## 2. Derive the primary result

Preserve query/model revision plus data snapshot/time identity.

## 3. Build an independent reconciliation

Use a materially different route on a controlled slice: source-system extract, semantic-layer metric, hand calculation, or alternate aggregation with independently stated joins.

## 4. Probe analytical failure modes

Check duplicates, join multiplication, late events, null ids, timezone boundaries, and dimension history when applicable.

## 5. Produce the handoff

Report the number, exact definition, data identity, query/model revision, reconciliation, and material NO-DATA.

## Finish

The number is decision-grade only to the extent its semantics and source identity are established. Successful SQL execution is not independent proof.

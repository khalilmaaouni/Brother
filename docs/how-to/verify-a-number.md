# Verify a decision-grade number

Use this for metrics, customer lists, forecasts, experiments, or analytical figures that will influence a decision.

## Define before seeing the answer

Record metric definition, grain, filters, timezone, inclusion/exclusion, denominator, and intended decision use.

## Identify data authority

Record source/table/model versions, snapshot time, semantic definition, and known late/backfill behavior.

## Build an independent oracle

Do not validate a query by rerunning the same logic in another cell. Use a materially different route: source control totals, alternate aggregation, known control population, hand-calculated sample, semantic-layer comparison, or certified prior period with explained deltas.

## Test boundaries

Check duplicates, join multiplication, nulls, timezone transitions, slowly changing dimensions, late facts, refunds/cancellations, and denominator drift when relevant.

## Preserve lineage

Record query/code revision and data identity.

## Verify the result

Report the number with definition, source identity, reconciliation evidence, material uncertainty, and `NO-DATA`. A successful SQL query is not independent proof.

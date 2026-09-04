# 08. Behaviour table

## What this is

01-purpose.md states what the revenue mart must guarantee, and
07-verification.md lists the check behind each guarantee. The rows below are
those same statements, rewritten as the fixed ID, Starting point, Trigger,
Required outcome, Proof table this repository's design checker reads. No row
here states anything the dossier does not already say.

## Rules

| ID | Starting point | Trigger | Required outcome | Proof |
|---|---|---|---|---|
| B1 | The revenue mart, after a daily build | The build completes for a snapshot | Mart revenue reconciles to the billing system within one cent | Reconciliation query comparing mart total to the billing export total for the snapshot, every build, blocking publication |
| B2 | An ingestion run reading an export file | The export header row count does not match what staged (a partial file) | No partial day is ever loaded into the mart | Row count assertion against the export header before staging commits, every ingestion run |
| B3 | A snapshot already built for one day | The same day is rebuilt | The rebuild produces rows identical to the first run | Idempotency test rebuilding one snapshot twice and diffing the output, every pull request |
| B4 | An invoice that has been refunded | The refund is recorded | Recognised revenue for the month drops by the refunded amount | Unit test on a refunded invoice asserting the month total drops by the refunded amount, every pull request |
| B5 | The published mart | A snapshot is published | The mart states its own freshness, matching the published snapshot date | Assertion that the freshness column equals the published snapshot date, every build |

## What this does not do

This does not run the checks: it hands the proof obligation to
07-verification.md, where each check already lives.

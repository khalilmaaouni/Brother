# Tutorial: a solo founder release review

Scenario: you built a paid SaaS with AI assistance and want to invite the first real customers.

## 1. Break “ready” into claims

- identity/access;
- money/payment behavior;
- customer-data durability;
- reliability/recovery;
- security/privacy basics;
- operations/observability;
- product truth: the promised core workflow works.

## 2. Reuse managed guarantees

Inspect the actual stack first. Do not rebuild hosted auth, managed backups, payment tooling, or deployment controls just to create custom machinery.

## 3. Prove the highest-cost failures

Examples:

- another user cannot access my account data;
- payment retry cannot duplicate money state;
- backup/restore exists for the tier actually used;
- production errors reach me somewhere I will see them;
- secrets are not client/repo exposed;
- a bad deploy has a roll-back/forward path;
- signup-to-core-value works in production-like conditions.

## 4. Mark the rest honestly

Untested disaster recovery, unmeasured load, or absent external security review are NO-DATA, not hidden green. A founder can intentionally defer them and record the flip condition.

## 5. Make the founder decision

Classify each readiness claim as sufficiently evidenced, not proven, NO-DATA, external-review-needed, or explicitly deferred.

## Finish

You have not proven the startup will succeed. You have made the first customer release less dependent on invisible assumptions.

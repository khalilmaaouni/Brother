# Tutorial: your first risky change

Scenario: a backend service processes payment-provider callbacks. Duplicate delivery is normal; one provider event id must never create more than one ledger effect.

## 1. Contract the invariant

Write the externally meaningful rule before implementation:

> For one provider event id, repeated deliveries create exactly one ledger effect and a consistent acknowledgement.

Success checks must exercise duplicate delivery, not only the happy path.

## 2. Inspect risk boundaries

Identify transaction boundaries, unique constraints, retry behavior, queue semantics, external side effects, and crash windows.

## 3. Choose evidence families

Potentially useful evidence:

- duplicate-delivery functional test;
- idempotency property/invariant;
- database uniqueness/transaction evidence;
- fault injection around the commit boundary;
- observability for duplicate suppression;
- independent business oracle: one event id equals one ledger effect.

## 4. Implement in meaningful units

Do not split one atomic transaction invariant across units merely to increase concurrency.

## 5. Review independence

If the same agent invented the rule and code, bring in provider contract, existing business requirement, or human-specified invariant as a separate source.

## 6. Read NO-DATA

A green implementation with no crash-window evidence can leave crash behavior NO-DATA. Decide whether the risk justifies more evidence before acceptance.

## Finish

The work is reviewable when the receipt states the idempotency claim, evidence, and remaining uncertainty explicitly. Release remains a separate human decision.

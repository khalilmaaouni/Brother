# Forecasting: ranges, sizes, and reforecasting

LOAD WHEN: stating any estimate of time, cost, or work remaining; sizing a task; or an actual has moved away from a forecast.

## The one law: ranges, never points

Every estimate the user sees carries all of:

- minimum and likely duration;
- work budget as a plain token range;
- confidence (low, medium, or high);
- assumptions it rests on;
- known unknowns;
- the next reforecast point.

A bare date or a bare number is a false promise and is never emitted.

## Task sizes (default planning envelopes, not prices or promises)

| Size | Typical duration | Work budget envelope | Required behavior |
|---|---|---|---|
| Micro | 5-20 minutes | 1k-5k tokens | Direct path, one verification |
| Small | 20-90 minutes | 5k-20k tokens | Short plan and acceptance check |
| Medium | 1-4 hours | 20k-60k tokens | Task breakdown and independent review |
| Large | 0.5-2 working days | 60k-140k tokens | Must be split into subtasks |
| Epic | More than 2 days | More than 140k tokens | Refuse as one task; decompose first |

The split above 140k is mandatory, not advisory: any task whose forecast
exceeds 140k tokens is decomposed before work starts.

## The output shape every forecast uses

```text
Likely time: 2-4 hours
Token range: 25k-45k
Confidence: Medium

Why:
- the existing payment module can be reused;
- one external sandbox must be configured;
- mobile behavior has not been inspected.

Reforecast after:
- the payment SDK is installed and the first sandbox request succeeds.
```

The "Why" lines are the assumptions; a forecast with no stated assumptions is
not a forecast. These envelopes come from this project's planning tables, not
from measured history yet; say so when confidence is asked about.

## Reforecast triggers

Reforecast, and tell the user, when any of these happens:

- project discovery completes;
- scope changes;
- a dependency turns out missing;
- a task exceeds 25 percent of its likely time;
- token use exceeds 30 percent of the expected upper bound;
- verification exposes new work;
- a user decision changes the solution;
- a task is split or cancelled.

Never silently move the delivery date. A moved forecast is announced with what
moved it, in one plain sentence, before any other content.

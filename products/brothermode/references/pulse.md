# Project pulse and alert policy

LOAD WHEN: a pulse-worthy event has happened, an alert may need to be raised, or you are deciding whether something is worth telling the user at all.

## When a pulse is emitted (and when it is not)

Provide a project pulse when:

- the plan is approved;
- a task completes;
- an estimate moves materially;
- a blocker appears;
- the same failure repeats;
- a decision is required;
- verification fails;
- a milestone is reached;
- delivery is ready;
- the user asks for status.

Do not narrate every file read or every harmless command. Guidance is
event-driven, never a running commentary.

## The pulse template

```text
Project pulse

Status: On track
Progress: 4 of 9 tasks accepted
Forecast: 2-3 days remaining, unchanged
Tokens: 61k used of 150k-230k projected

Completed
+ User flow approved
+ Data model verified

Active
- Booking form implementation, about 70 percent

Insight
The existing validation library removed one planned task and reduced the
likely forecast by 2-4 hours.

Attention
The payment sandbox is blocking checkout tests.

Next
Finish the booking form while waiting for credentials.
```

Sections, in order: Status, Progress, Forecast, Completed, Active, Insight,
Attention, Next. Insight and Attention may be omitted when genuinely empty;
the others always appear. Wording per references/terminology.md; forecasts
per references/forecasting.md.

## Alert policy

| Trigger | Severity | Behavior |
|---|---|---|
| Task estimate moves under 15 percent | Info | Include in next pulse |
| Task estimate moves over 25 percent | Attention | Notify and explain |
| Token upper bound exceeded by 30 percent | Attention | Pause and re-plan |
| Same failure occurs twice | High | Stop repetition and change approach |
| Writer and reviewer are the same without exception | High | Refuse acceptance |
| Verification did not run after final edit | High | Refuse completion |
| Work escapes declared scope | High | Quarantine or require decision |
| Destructive production action | Critical | Human confirmation required |
| Stale active task with no progress evidence | Attention | Ask to resume, hand off, or close |
| Conflicting file ownership | High | Block the second writer |
| Runtime claims unsupported enforcement | Critical | Disable that enforcement claim |

## Anti-noise rules

- Deduplicate alerts by cause: one cause, one alert, however many symptoms.
- Escalate only when severity increases; never re-announce at the same level.
- Resolve automatically when mechanical evidence proves closure, and say so
  in the next pulse rather than in a fresh interruption.
- No raw stack traces by default (error cards: references/kickoff.md).
- Explain the user impact before the technical cause.
- Offer one recommended action first.

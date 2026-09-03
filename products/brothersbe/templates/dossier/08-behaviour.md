# 08. Behaviour table

<!-- SBE-TEMPLATE-UNFILLED 08-behaviour: this section is still the shipped example.
     Replace it with your own design, then delete this comment. While it is
     here, `sbe_design.py placeholder` FAILs and names this file. -->

## What this is, and who owns it
The other artifacts describe the shape of the system: who does what, how the
data is structured, how the pieces connect. None of them says what the
software must actually DO under a given condition. This one does. It is a
table of rules, each written as a plain-language sentence: this starting
point, this trigger, this required outcome.

The business analyst writes the rows. They know the rule because they know
the business, not because they wrote the code. The tester fills in Proof:
how each row will be shown true, once there is something to test. An
engineer does not grade their own design; a tester checking someone else's
rule against someone else's rule is the whole point of the column.

## Rules
| ID | Starting point | Trigger | Required outcome | Proof |
|---|---|---|---|---|
| B1 | A checkout with a valid payment method | The customer submits checkout | An order is created with status "placed" | Integration test: submit checkout with a valid payment method, assert the order row exists with status placed |
| B2 | An order that has been placed | The warehouse system does not acknowledge receipt within five minutes | The order service retries delivery and raises an alert after three failed retries | Failover drill: stop the warehouse consumer, confirm the retry count and the alert firing inside the five-minute window |
| B3 | A customer placing their first order | The order is placed | Support can see that order from the single order-status screen within one minute | Manual check: place a first order, query the order-status screen immediately after, confirm the order appears within one minute |

## Reading and keeping this table
- Every row needs a Required outcome and a Proof. A Required outcome with no
  Proof is a rule nobody has agreed how to check; it is not finished, no
  matter how well it reads.
- The verification plan cites these rows by ID. Deleting a row that is
  cited breaks the dossier somewhere else, even if this file still looks
  fine on its own.
- IDs are never reused. A retired rule is marked retired, not handed its
  old ID for a different rule; the ID is how every other document points
  back here.

## Supersedes
A row may replace an earlier one instead of getting a new ID. Name the row
it replaces here; leave the cell empty when a row replaces nothing.

| Row | Supersedes |
|---|---|
| B1 | |
| B2 | |
| B3 | |

## Revision log
A requirement change edits the row above and appends one dated line here,
never a new dossier: the date, the row it changed, and one sentence of what
changed. `sbe_design.py behaviour` FAILs, naming the row, when a row's
wording has moved since it was last accepted and nothing here says so.

| Date | Row | Change |
|---|---|---|
| 2026-08-30 | B1 | Initial rule recorded |
| 2026-08-30 | B2 | Initial rule recorded |
| 2026-08-30 | B3 | Initial rule recorded |

## What this does not do
This does not run the check itself: it states the rule and hands the proof
obligation to the verification plan, which is where Proof actually happens.

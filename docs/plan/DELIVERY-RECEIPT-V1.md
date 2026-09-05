# Delivery Receipt, contract v1.2

The receipt is the one file a stranger reads when the transcript is gone. This
page freezes what that file contains, field by field, at the exact JSON path
the engine writes today.

Written down from the code, not from memory. Every path below was read out of a
receipt the engine produced through the stubbed model seam (`DOOR_MODEL_CMD`,
`MODEL_WORKER_CMD`) on the README's toy repository, and
`scripts/test_receipt_contract.py` parses the tables on this page and asserts a
freshly generated receipt against them, so the page cannot drift from the
engine without that check going red.

Where the file lives: `<run_dir>/receipt/receipt.json`
(`brother_run.RECEIPT_DIRNAME` and `RECEIPT_FILENAME`), written by
`scripts/brother_run.py:_write_receipt`, whose whole body is
`receipt_door.receipt_record()` plus the one added key `report`. A run that
cannot write it exits non zero rather than returning 0 with nothing findable.

## The twelve questions

A receipt earns its name by answering these, and by saying so plainly where it
cannot:

1. What was asked?
2. What changed?
3. What did not change?
4. What check proved each changed file?
5. Did it fail before and pass after?
6. Did a dependency revert or counterfactual check run?
7. What remains NO-DATA?
8. What left the declared scope?
9. Who wrote the check?
10. Which engine revision ran it?
11. What should the human inspect first?
12. What did an independent reviewer find, and what check would prove it?

All twelve have a field as of contract 1.2. Two of them did not at 1.0, and
what closed them is recorded under "What 1.1 added" below; the twelfth was
added at 1.2 with the field rows that answer it.

## The fields

`[]` in a path means every element of that array. A row marked `per element` is
required on each element the array holds; an empty array satisfies it
vacuously, so a run that changed nothing exercises fewer rows than a run that
changed something. The generated receipt the check runs against always carries
at least one changed file, one verified unit and one unproven unit, so every
element row under `scope.changed`, `evidence` and `unproven` is exercised for
real.

| JSON path | Type | Required | Answers |
| --- | --- | --- | --- |
| `scope` | object | yes | 2 |
| `scope.changed` | array | yes | 2 |
| `scope.changed[].file` | string | per element | 2 |
| `scope.changed[].unit` | string | per element | 2 |
| `scope.changed[].check_command` | string | per element | 4 |
| `scope.changed[].exit_code` | integer or null | per element | 4 |
| `scope.changed[].output_location` | string | per element | 4 |
| `scope.changed[].check_passed_before` | boolean or null | per element | 5 |
| `scope.changed[].state` | string | per element | 7 |
| `scope.changed[].reason` | string | per element | 7 |
| `scope.declared_untouched` | array | yes | 3 |
| `intent` | object | yes | 1 |
| `intent.outcome` | string | yes | 1 |
| `intent.units` | array | yes | 1 |
| `intent.units[].id` | string | per element | 1 |
| `intent.units[].objective` | string | per element | 1 |
| `evidence` | array | yes | 4 |
| `evidence[].id` | string | per element | 4 |
| `evidence[].state` | string | per element | 4 |
| `evidence[].mark` | number or null | per element | 4 |
| `evidence[].command` | string | per element | 4 |
| `evidence[].exit_code` | integer or null | per element | 4 |
| `evidence[].check_passed_before` | boolean or null | per element | 5 |
| `evidence[].author` | string | per element | 9 |
| `evidence[].evidence_family` | string | per element | 4 |
| `evidence[].independence` | string | per element | 4 |
| `evidence[].output_location` | string | per element | 4 |
| `evidence[].why` | string | per element | 4 |
| `evidence[].dependency_check` | string | per element | 6 |
| `unproven` | array | yes | 7 |
| `unproven[].id` | string | per element | 7 |
| `unproven[].state` | string | per element | 7 |
| `unproven[].mark` | number or null | per element | 7 |
| `unproven[].command` | string | per element | 7 |
| `unproven[].exit_code` | integer or null | per element | 7 |
| `unproven[].check_passed_before` | boolean or null | per element | 5 |
| `unproven[].author` | string | per element | 9 |
| `unproven[].evidence_family` | string | per element | 7 |
| `unproven[].independence` | string | per element | 7 |
| `unproven[].output_location` | string | per element | 7 |
| `unproven[].why` | string | per element | 7 |
| `unproven[].reason` | string | per element | 7 |
| `unproven[].dependency_check` | string | per element | 6 |
| `repair_history` | array | yes | 7 |
| `attention` | object | yes | 11 |
| `attention.risk_triggers` | array | yes | 11 |
| `attention.risk_triggers[].class` | string | per element | 11 |
| `attention.risk_triggers[].unit` | string | per element | 11 |
| `attention.risk_triggers[].words` | array | per element | 11 |
| `attention.unproven_units` | array | yes | 7 |
| `attention.reading_order` | object | yes | 11 |
| `attention.reading_order.REVIEW FIRST` | array | yes | 11 |
| `attention.reading_order.NOT PROVEN` | array | yes | 11 |
| `attention.reading_order.LOW-RISK MECHANICAL` | array | yes | 11 |
| `attention.reading_order.NO NEED TO RE-READ` | array | yes | 11 |
| `attention.cognitive_debt` | object | yes | 11 |
| `attention.cognitive_debt.count` | integer | yes | 11 |
| `attention.cognitive_debt.signals` | array | yes | 11 |
| `attention.review` | object | yes | 12 |
| `attention.review.pass_state` | string | yes | 12 |
| `attention.review.units_reviewed` | array | yes | 12 |
| `attention.review.units_reviewed[].unit` | string | per element | 12 |
| `attention.review.units_reviewed[].tier` | string | per element | 12 |
| `attention.review.units_reviewed[].class` | string | per element | 12 |
| `attention.review.units_reviewed[].reviewer` | string | per element | 12 |
| `attention.review.units_reviewed[].unmeasured_classes` | array | per element | 12 |
| `attention.review.findings` | array | yes | 12 |
| `attention.review.findings[].id` | string | per element | 12 |
| `attention.review.findings[].unit` | string | per element | 12 |
| `attention.review.findings[].file` | string | per element | 12 |
| `attention.review.findings[].reviewer` | string | per element | 12 |
| `attention.review.findings[].severity` | string | per element | 12 |
| `attention.review.findings[].failure` | string | per element | 12 |
| `attention.review.findings[].check_command` | string | per element | 12 |
| `attention.review.findings[].check_exit_code` | integer or null | per element | 12 |
| `attention.review.findings[].state` | string | per element | 12 |
| `attention.review.findings[].repaired` | boolean or null | per element | 12 |
| `containment` | object | yes | 8 |
| `containment.boundary_crossings` | array | yes | 8 |
| `containment.undeclared_scope_units` | array | yes | 8 |
| `containment.contained` | boolean | yes | 8 |
| `continuity` | object | yes | 1 |
| `continuity.capsule` | object or null | yes | 1 |
| `continuity.problem` | string | yes | 1 |
| `continuity.target_revision` | string | yes | 1 |
| `continuity.env_lock` | string | yes | 1 |
| `harness_version` | string | yes | 10 |
| `harness_revision` | string | yes | 10 |
| `report` | string | yes | 1 |

`report` is the delivery report `brother_run` prints verbatim, added to the
record by `_write_receipt` so the file stands alone for a reader who never saw
stdout.

## What 1.1 added

Contract 1.0 named two questions the receipt could not answer, and enforced
that absence with a second table. Row E115 closed both. The absence table is
gone because it now has nothing to hold: every field it forbade is either
written or superseded by one that is.

**Question 6, did a dependency revert or counterfactual check run. Answered by
`evidence[].dependency_check` and `unproven[].dependency_check`.**
`receipt_door.dependency_note()` already built exactly that sentence per unit
and `receipts_for` already stamped it on the in memory receipt; the value
stopped at `receipt_record()`, which never copied it into the record. What
survived on the file was one sided: when the revert re run passed and therefore
disproved the check, the refusal sentence landed in `unproven[].reason`, and
when the revert ran and correctly failed, the receipt said nothing at all,
which read exactly like a revert nobody ever made. The field carries
`dependency_note()`'s own words unchanged, and never a second judgement made
here. A unit with no dependency to revert reads "no dependency declared: this
check proves its own change only", which is a state, not an absent key.

**Question 10, which engine revision ran it. Answered by the top level
`harness_version` and `harness_revision`.** Both were always real and always
measured, and both reached the file only as text inside `report`, under the
cost block, where no reader can query them and no check can assert on them.
They are read from `brother_run._harness_version()` and
`brother_run._harness_revision()`, the same two calls whose results
`build_cost_block` is handed, so the fields and the report prose cannot
disagree. Each is a `git describe --always --dirty` and a `git rev-parse HEAD`
of the engine's own checkout, or the packaged manifest's stamp for an installed
copy with no `.git`, or a NO-DATA sentence naming the repository it could not
read. Note that `harness_revision` on the receipt is the engine that RAN the
delivery, which on a resumed run is not the engine that created it; the
creator's sha stays where it always was, on the Work document's own
`harness_revision` stamp and on each in memory receipt.

**Question 9 was already written, contrary to the expectation the 1.0 row
started from.** `evidence[].author` and `unproven[].author` carry it on every
entry, set by `receipts_for` from the row's own `check_author` and defaulting
honestly to "the planning model", which is the only author that has ever
written a `done_check` in this estate. Verified in the code before this page
claimed it.

A limit worth writing down, found while closing question 6. The 1.0 absence
table was enforced by FIELD NAME, so it only ever refused the exact spelling it
listed. It forbade `dependency_note`, and the field that answers question 6 is
named `dependency_check`, so the absence check would have stayed green through
this change on its own; only question 10, whose fields kept their names, turned
it red. The field table's own question column is the durable half of the
mechanism, and the check now refuses any question this page still claims is
unanswered while a field table row says it is answered, whatever that field is
called. The capitalised claim phrase is reserved as that marker, which is why
no sentence here spells it except a live claim, and 1.1 leaves none to spell.

## The three verdict states

Every unit lands in exactly one state, and the state, never the exit code,
decides where the unit is printed and what it is worth. The state is written by
`receipt_door.receipts_for()` and the mark by `mark_for()` off a fixed three
row table with no partial credit.

**verified** (mark 10.0, printed under `evidence`). Requires all of: the unit
was not refused and not marked `integration_refused`; `check_passed_before` is
exactly `False`, meaning the engine measured the check failing before the work;
`files_changed_by_unit` is present and non empty; no dependency gap; no missing
E18 evidence file where the family demands one; no missing numbers manifest
where the family demands one; a non empty command; and a captured exit code of
0. Anything less is not this state.

**refused** (mark 0.0, printed under `unproven`). The unit appears in
`build_report`'s own refusal list, or the row carries `integration_refused`.
The refusal sentence is copied verbatim into `reason`.

**no-data** (mark `null`, printed under `unproven`). Every other way a green
exit code can fail to prove anything, each with its own sentence in `reason`:
the check already passed before the work began; the unit changed no file; the
pre run check was never recorded; the changed file list was never recorded; the
check still passes with a declared dependency's change reverted, or that re run
could not be made, or it broke rather than failed; an E18 unit with no evidence
file; an E8 or E2 unit with no verified numbers manifest; the check fails the
same way before and after, so it does not run at all; or no exit code was
captured. `null` is never a zero and never a middle value: it contributes
nothing to the arithmetic rather than contributing a bad number.

The brief for this row called the middle state "failed". The engine's word is
`refused`, and this page uses the engine's word.

## The fails before rule

`check_passed_before` is the one fact separating a check that proves the work
from a check that would have passed on the untouched tree. It is stamped by
`brother_run`'s own `_stamp_prechecks` before any worker runs, and it is
tri state on purpose:

- `False`: measured, and the check failed before the work. Only this value can
  lead to `verified`.
- `True`: measured, and the check already passed. Forced to `no-data`, whatever
  the exit code at delivery says.
- `null`, or the key absent on an older record: never measured. Also forced to
  `no-data`, because an unknown must never read as proof.

The field rides on both `scope.changed[]` and the `evidence`/`unproven`
entries, so a reader looking file first and a reader looking unit first meet
the same fact.

## Scope: declared against written

`scope.changed[]` is what was written, one entry per file, built by
`per_file_checks()` from the run's own claim evidence.
`scope.declared_untouched[]` is the other half: every path a unit declared it
owns that no file it changed lands under, so a reviewer knows which declared
files need no second reading.

`containment` compares the two using `scope_audit.covered()`, the same
containment test the scheduler itself uses:

- `boundary_crossings[]`: a changed file outside the scope its own unit
  declared, with the declaration it broke.
- `undeclared_scope_units[]`: a unit that changed files while declaring no
  scope at all. Absent is not read only, and a unit that declared nothing
  should never have been dispatched.
- `contained`: true only when both lists are empty.

Quarantine itself is not a receipt field. It is the upstream verdict
`scope_audit.audit()` returns (`CLEAN`, `QUARANTINE`, `NO-DATA`) at integration
time, where a result that wrote outside its declaration is held rather than
merged. What reaches the receipt is the evidence behind that verdict, in the
two lists above.

## Human review ordering (E75)

`attention.reading_order` holds all four sections, always, even when a section
is empty, because a heading that vanishes reads exactly like a heading nobody
computed. Each entry carries `path`, `unit` and the `why` that put it there.
The precedence, top down:

1. `REVIEW FIRST`: a path outside its unit's declared scope, a path from a unit
   that declared none, a path an independent reviewer named in a finding that
   is confirmed or unproven (S32, question 12 below), or a path naming a risk
   class (auth, money, migration, parsing, concurrency, dependency manifest).
   Risk outranks proof.
2. `NOT PROVEN`: anything else whose own receipt is not `verified`.
3. `LOW-RISK MECHANICAL`: a declared, risk free path whose check proved it.
4. `NO NEED TO RE-READ`: a path a unit declared and never touched.

Beside it, `attention.cognitive_debt` counts what the delivery costs the next
reader (a changed dependency manifest, a path naming a new indirection, a file
outside its unit's declared scope). It is deliberately internal: it rides on
the machine receipt and never becomes a flag, a mode or a screen, because a
number a person is shown is a number a person can be asked to hit.

## What an independent reviewer found (S32)

`attention.review` is on every receipt, empty or not, and answers question 12.
It is written by `brother_run._stamp_review_findings` after the drain and read
back by `receipt_door.review_findings`; nothing in it can change an exit code,
and no finding blocks a merge.

`pass_state` reads `ran` only when at least one unit was really reviewed.
Every other value is a NO-DATA sentence naming what stopped it: no unit
crossed a risk boundary, no reviewer was reachable (`REVIEW_MODEL_CMD` unset),
the reviewer command failed, or its answer held no finding. A delivery that
could not be reviewed must never read like one that was reviewed and came back
clean.

`units_reviewed[]` names each unit the pass considered, the tier it read, the
risk class that selected the reviewer, the reviewer itself, and every other
class that fired and lost its slot (`unmeasured_classes`), so a class is never
dropped in silence.

`findings[].state` is the whole design in one field, and only one of its three
values is a measurement:

- `confirmed`: the finding's own `check_command` was RE-EXECUTED at the
  delivered revision and exited nonzero. `check_exit_code` is that real code.
- `not_reproduced`: it ran and exited 0, so it discriminates nothing. A check
  that already passes proves nothing, here as everywhere else in this estate.
- `no-data`: the reviewer named no command, or the command could not run.
  `check_command` then reads NO-DATA and `check_exit_code` is null.

`repaired` is `false` for a confirmed finding this run left open, and null
wherever the state is not `confirmed`.

A `confirmed` or a `no-data` finding puts its file at the top of
`attention.reading_order`'s REVIEW FIRST, with the reviewer and the exit code
in the `why`. A `not_reproduced` finding moves nothing, because it proved
nothing about the delivered tree.

## Versioning

This is contract **1.2**. It adds the `attention.review` block (row S32:
question 12, what an independent reviewer found and what check would prove
it), and removes and renames nothing.

Contract 1.1 added `evidence[].dependency_check`,
`unproven[].dependency_check`, `harness_version` and `harness_revision` to
1.0, and removed and renamed nothing either, so a reader written against 1.0
reads a 1.2 receipt unchanged.

Within v1 a field is never removed and never renamed. It may only be added.
A reader written against 1.0 keeps working against every later 1.x receipt.

Consequences, in order of how often they will bite:

- Adding a field is a minor bump (1.1, 1.2) and one new row in the table above.
- Adding a field that answers a question this page still claims is unanswered
  means retiring that claim in the same change. The check fails until that
  happens, which is the mechanism, not a side effect. It refuses on the
  question number, so renaming the field on the way in does not slip past it.
- Removing or renaming a field is a v2, and v2 does not exist. Nothing in this
  estate is allowed to ship it under the v1 name.
- A field whose value can be unknown carries a NO-DATA sentence saying why.
  NO-DATA is never a pass and never an absent key: the key stays, the sentence
  explains itself.

The check that holds all of this in place:

```
python3 scripts/test_receipt_contract.py
```

It parses both tables on this page, generates a receipt with the engine, and
asserts the two against each other. The page is the source of truth for the
required list; the engine is the source of truth for what a receipt contains;
the check is what refuses to let them disagree.

# JBEQ-MDM unseen qualification set, record

Authored: 2026-09-06.

Authored by: a Claude subagent (Sonnet, effort high), dispatched blind to
the frozen JBEQ-MDM seed and to the deterministic decision engine. The
subagent never opened `benchmarks/jbeq/mdm/seed-2026-09-05.json`, anything
under `benchmarks/jbeq/mdm/runs/`, the generalization or identity-class
case files, any `benchmarks/jbeq/mdm/prompts/<ID>.md` case prompt file,
`scripts/jbeq_decide.py`, or any test file.

## Files opened while authoring this set

- `benchmarks/jbeq/README.md`
- `benchmarks/jbeq/mdm/fact-sheet-schema.json`
- `benchmarks/jbeq/mdm/prompts/EXTRACTOR-PROMPT.md`
- `benchmarks/jbeq/mdm/decision-rules-addendum.md`
- `scripts/jbeq_mdm.py` (the decision vocabulary block, VOCABULARY_HEADING
  through the end of the VOCABULARY string, plus the seed loader and
  scorer code that documents a seed case's own shape: load_seed,
  write_prompts, score, cmd_score, and the CLI subcommands in main)

No other file in the repository was read to produce the case content
below. benchmarks/jbeq/mdm/unseen-2026-09-06.json and this record are
new files; nothing under benchmarks/jbeq/mdm/seed-2026-09-05.json or
benchmarks/jbeq/mdm/runs/ was touched, read, or modified.

## Corrections after the 2026-09-06 blind audit

An opus auditor labelled all 40 cases blind and compared against the
authored labels (`~/.claude/evidence/audit-unseen-set-2026-09-06.md`).
Every correction below was applied exactly as the audit's section F named it.

1. U-33: `expected` ESCALATE to SUGGEST MERGE. Boundary 4 sentence 2 is
   satisfied word for word (medium evidence plus an irreversible step), so
   as labelled it scored a correct engine as a CRITICAL FALSE MERGE.
2. U-13: `expected` KEEP SEPARATE to LINK AS RELATED. The input states
   three independently valid parents (capital, trade-flow, reporting);
   dropping two of them is data loss, not conservative handling.
3. U-28: `expected` KEEP SEPARATE to LINK AS RELATED, for consistency with
   U-08 (a ship-to serving a store is the same relation as a shared
   delivery centre serving stores).
4. U-23: `expected` KEEP SEPARATE to ESCALATE. A matching reading with
   different kanji corroborates without confirming identity, and
   identifiers missing on both sides is boundary 4's own weak-evidence
   ESCALATE trigger, not a fact that the two are different objects.
5. U-17: `expected` ESCALATE to SUGGEST MERGE. An unvalidated crosswalk is
   medium evidence, and medium evidence with a confirmation reason is
   SUGGEST MERGE's own definition.
6. U-29: `expected` LINK AS RELATED to REJECT MATCH. `scripts/jbeq_mdm.py`'s
   score() compares `given == case["expected"]` as a scalar, so `expected`
   cannot hold a list of accepted answers; REJECT MATCH was chosen to match
   the same shape as U-14, U-22 and U-39 (a request the facts refute).
7. U-25: corporate number changed from 6250001034567 (a three-way collision
   with U-05 and U-37) to 8450001089012, distinct from every other number in
   the set. Promoted to `critical` (class MERGE CAPABILITY GAP) as one of
   the set's three critical merge positives. The sentence announcing that no
   contradiction or tenant boundary is recorded was removed; the input now
   states only the matching identifier and role, and the model must notice
   the absence of any complicating fact itself.
8. U-37: corporate number changed from 6250001034567 to 9630001078234 to
   remove the same collision. Promoted to `critical` (class MERGE CAPABILITY
   GAP) as a second critical merge positive.
9. U-02: building name changed from "大手町ビル" (which names the very
   locality the question asks about, so the case's own NO-DATA answer was
   false) to "第一ビル", which carries no locality. The sentence announcing
   that no other clue exists was also removed, so NO-DATA is a real absence
   the model must notice rather than one the input hands over.
10. U-30 and U-40: the sentence announcing that no other clue exists was
    removed from each input, for the same reason as U-02.
11. U-34: the sentence stating the two dated values "do not conflict" was
    removed; the dates and which is newer are stated, and the model must
    reach the no-conflict reading itself rather than being told it.
12. Allowed lists: AUTO-MERGE and SUGGEST MERGE added to U-01, U-03, U-13,
    U-18, U-28, U-29 and U-36, each of which asks a merge question
    (統合 or 一本化) while previously offering no merge answer, which gave away
    the polarity before the model reasoned. Their expected labels are
    unchanged except where corrections 2, 3 and 6 above changed them.
    Every other case whose question uses 統合 or 一本化 already offered both
    merge answers; none needed a fix.

## Corrections after the 2026-09-06 founder ruling

13. U-17: `expected` SUGGEST MERGE back to ESCALATE, `label_corrected` added.
    Founder ruling A, 2026-09-06 (`docs/decisions/decision-p0-3-u17-crosswalk-2026-09-06.json`),
    the HI-01 precedent: correction 5 above read the crosswalk's own lack
    of validation as its confirmation reason, which is the addendum's
    rule 9 double count it forbids, an unvalidated crosswalk counted once
    as the medium evidence and again as its own confirmation reason. With
    no separately stated confirmation reason, rule 9 gives ESCALATE. U-17
    is not critical, so this does not change the critical wrong or false
    merge counts on any run scored against it; it drops SUGGEST MERGE
    from 2 to 1 and raises ESCALATE from 9 to 10 in the label distribution
    below.

## U-17 correction, 2026-09-06

Re-scored under the current `scripts/jbeq_mdm.py score` (which derives
engine-decided per case from each run's own `decisions.jsonl`, never from
a track-name list) against the seed before and after the U-17 correction
above. "Before" here is a fresh re-run against the pre-correction seed
with today's scorer, not the `score.txt` already committed in each
directory, which predates the per-case engine-decided fix and is stale on
its own totals. Every directory's `answers.json` and `decisions.jsonl`
are untouched; only the seed's U-17 label changed. `score-v3.txt` in each
directory holds the corrected (after) run.

| Run directory | critical wrong before | critical wrong after | engine-decided before | engine-decided after |
|---|---|---|---|---|
| qualification-u1-2026-09-06 | 8 of 26 | 8 of 26 | 20 of 30 | 19 of 30 |
| regression-2026-09-06-u1-pass | 6 of 26 | 6 of 26 | 24 of 30 | 23 of 30 |
| regression-2026-09-06-u1-proposal-gate | 6 of 26 | 6 of 26 | 24 of 30 | 23 of 30 |
| regression-2026-09-06-u1-rule-d | 5 of 26 | 5 of 26 | 25 of 30 | 24 of 30 |

U-17 is not critical, so critical wrong is unchanged everywhere; each
run's engine had said SUGGEST MERGE for U-17 (matching the pre-correction
seed), so engine-decided drops by exactly one in every directory.

## Case count per track

| Track | Cases | Critical |
|---|---|---|
| address | 5 | 2 |
| entity-object | 5 | 4 |
| hierarchy | 5 | 3 |
| identifier | 5 | 4 |
| match-or-no-merge | 5 | 4 |
| requirements | 5 | 3 |
| survivorship | 5 | 2 |
| temporal | 5 | 4 |
| Total | 40 | 26 |

26 of 40 cases are critical, above the 18 the brief asked for. (U-25 and
U-37 were promoted to critical by correction 7 and 8 above, which is why
this table now differs from the pre-audit version.)

## Naive same-identifier auto-merge traps

11 of the 40 cases are built so that a naive same-identifier or
same-address rule would AUTO-MERGE or LINK wrongly, and the correct answer
requires reading a stated refuting fact, a domain mismatch, a tenant
boundary, an operational reason, or a near-match trap instead: U-01, U-03,
U-09, U-16, U-18, U-19, U-20, U-21, U-22, U-23, U-28. (Corrected by the
2026-09-06 audit from a claimed 16: U-17, U-24, U-33, U-38 and U-39 present
no matching identifier at all and do not qualify as this kind of trap.)

## Label distribution after corrections

(U-17's correction above moves one case from SUGGEST MERGE to ESCALATE.)

| Expected answer | Count |
|---|---|
| LINK AS RELATED | 12 |
| ESCALATE | 10 |
| REJECT MATCH | 9 |
| KEEP SEPARATE | 3 |
| NO-DATA | 3 |
| AUTO-MERGE | 2 |
| SUGGEST MERGE | 1 |
| Total | 40 |

Critical cases: 26 of 40. Critical cases expecting a merge answer
(AUTO-MERGE or SUGGEST MERGE): 3 of 26 (U-25, U-33, U-37).

A merge-incapable engine now scores under the 95 percent bar.

## What this set is

This set is the qualification set; the frozen 70 became a regression set
once its rules were authored against it.


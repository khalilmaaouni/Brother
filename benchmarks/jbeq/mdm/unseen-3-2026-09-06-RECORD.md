# JBEQ-MDM unseen-3-2026-09-06, authorship and structure record

## Authorship

Authored by a Claude subagent (Sonnet, effort high), blind to the frozen seed
(`benchmarks/jbeq/mdm/seed-2026-09-05.json`), blind to unseen-1
(`unseen-2026-09-06.json`), blind to unseen-2 (`unseen-2-2026-09-06.json`),
and blind to the decision engine (`scripts/jbeq_decide.py`). None of those
four files was opened during authoring.

## Files opened

* `benchmarks/jbeq/README.md`
* `benchmarks/jbeq/mdm/fact-sheet-schema.json`
* `benchmarks/jbeq/mdm/prompts/EXTRACTOR-PROMPT.md`
* `benchmarks/jbeq/mdm/decision-rules-addendum.md`
* `scripts/jbeq_mdm.py`, restricted to the `決定語彙` / `VOCABULARY` block
  (lines 170 to 212), `load_seed` (lines 244 to 251) and `write_prompts`
  (lines 254 to 298); `cmd_score`'s existence was confirmed only through its
  own `--help` output and one `KeyError` traceback naming the missing
  `seed["scoring"]["merge_answers"]` key, which is why the seed file below
  carries a `scoring.merge_answers` block absent from the task's own field
  list
* `~/.claude/evidence/FIX-DIRECTIVE-2026-09-06.md`, sections 15 to 22
  (lines 593 to 860)
* `~/.claude/evidence/audit-unseen-set-2026-09-06.md`, sections C, D, E
  (lines 17 to 34)
* `~/.claude/evidence/audit-unseen-set-2-2026-09-06.md`, sections C, D, E
  (lines 13 to 34)

No other file in the repository or the evidence directory was read.

## Counts per track

address 5, entity-object 5, hierarchy 5, identifier 5, match-or-no-merge 5,
requirements 5, survivorship 5, temporal 5. 40 cases total, W-01 to W-40.

## Counts per label

AUTO-MERGE 3, SUGGEST MERGE 3, LINK AS RELATED 6, KEEP SEPARATE 9,
REJECT MATCH 9, ESCALATE 8, NO-DATA 2.

## Critical count

30 of 40 critical. Every critical case carries a `critical_class` from the
danger vocabulary named in the brief (FALSE MERGE, WRONG PAYER, WRONG STORE
ASSIGNMENT, HIERARCHY REVERSAL, CROSS-CUSTOMER CONTAMINATION, WRONG LEGAL
ENTITY, WRONG TAX IDENTITY, HISTORICAL REASSIGNMENT, SOURCE PRECEDENCE
VIOLATION, MERGE CAPABILITY GAP). Distribution: MERGE CAPABILITY GAP 6,
SOURCE PRECEDENCE VIOLATION 4, HIERARCHY REVERSAL 4, CROSS-CUSTOMER
CONTAMINATION 3, WRONG PAYER 3, WRONG STORE ASSIGNMENT 3, WRONG TAX IDENTITY
2, FALSE MERGE 2, HISTORICAL REASSIGNMENT 2, WRONG LEGAL ENTITY 1.

## Merge positives among criticals

6 critical cases expect AUTO-MERGE or SUGGEST MERGE, six distinct shapes:
W-01 (address, relocation with continuing history, SUGGEST MERGE), W-06
(entity-object, duplicate commercial account with three years of history,
SUGGEST MERGE, a stated request), W-16 (identifier, a clean validated
corporate-number match with no history, AUTO-MERGE, a stated request), W-21
(match-or-no-merge, a duplicate created by two order channels, AUTO-MERGE),
W-22 (match-or-no-merge, an unvalidated crosswalk plus an irreversible
deletion, SUGGEST MERGE), W-38 (temporal, a trade-name change with unbroken
corporate-number continuity, AUTO-MERGE). This clears the brief's floor of 5
and answers the second audit's finding that a merge-incapable engine could
pass a set with zero merge positives among its criticals.

## Trap ids (a surface identifier or address match that a naive rule would merge)

W-02, W-03, W-04, W-07, W-08, W-09, W-14, W-17, W-18, W-20, W-24, W-25,
W-26, W-32, W-34, W-37, W-40: 17 cases where a surface match (a shared
address, a shared reading, a shared digit string, a shared corporate
number, or a shared attribute value) tempts a naive merge or link, but the
correct answer is something else. Recounted after the 2026-09-06 blind
audit (`~/.claude/evidence/audit-unseen-set-3-2026-09-06.md`, section E):
the earlier 14-id list was misassembled, four of its ids (W-12, W-13,
W-19, W-31) argue from something other than a surface match, and seven
genuine surface-match traps (W-14, W-20, W-25, W-26, W-32, W-34, W-40)
were omitted. This clears the brief's floor of 12.

## Proposal-verb cases, split by expected label

Grep tokens 提案, 求めている, リクエスト, 依頼 across `input` plus
`question`.

* REJECT MATCH with a proposal verb (refuted proposals), 7 of 9 REJECT MATCH
  cases: W-02, W-07, W-12, W-17, W-24, W-31, W-37.
* REJECT MATCH with no proposal verb, 2 of 9: W-13, W-18. These show REJECT
  MATCH is reached without request language too.
* A proposal verb with an expected answer that is NOT REJECT MATCH
  (accepted merges and an accepted link), 3 cases: W-06 (SUGGEST MERGE),
  W-16 (AUTO-MERGE), W-23 (LINK AS RELATED).

This breaks the correlation the second audit named as its strongest
finding, 11 of 11 REJECT MATCH cases carrying a proposal verb and 0 of 29
others carrying one: here a proposal verb neither guarantees REJECT MATCH
(3 counterexamples) nor is required for it (2 counterexamples).

## Other required shapes present

REJECT-versus-KEEP tested both ways: 7 refuted proposals (listed above,
plus W-13 and W-18 without a verb, 9 REJECT MATCH cases in total) and 6
no-proposal KEEP SEPARATE pairs (W-03, W-04, W-08, W-14, W-20, W-26).
Temporal conflicts: W-36, W-37, W-38, W-39 (4, floor is 3). Hierarchy
direction reversals: W-12, W-13 (2). Both-sided contradictions, a kanji
variant name plus a differing lot number: W-03 (address), W-08
(entity-object) (2). Identifier reuse: W-17 (a customer code reassigned
after a defunct company's code was never retired). Manual override: W-31
(an expired override loses to a higher-authority feed, REJECT MATCH).
Relocation with history: W-01 (SUGGEST MERGE, a compatible lifecycle
event). Three-parent hierarchy: W-11 (LINK AS RELATED, capital, trade-flow
and reporting parents are independent). NO-DATA cases that omit the fact
without announcing its own absence: W-28, W-35.

## Structural notes carried over from the two audits

No input recites the merge threshold checklist: the three AUTO-MERGE and
three SUGGEST MERGE cases state concrete facts (a corporate number, a
representative's name, an address, an absence of orders so far) rather than
meta-language like "no contradictions, no tenant boundary are stated".
No two cases share a run of 14 or more characters in their `input` plus
`question` text (checked programmatically). No corporate number is assigned
to two different companies; the three 13-digit strings that appear twice
each belong to one company's own paired corporate number and
T-prefixed qualified-invoice number within a single case (W-07, W-16,
W-21), which is the intended domain-mismatch or continuity fact for that
case, not an accidental collision. No case is framed as a single company
name followed by a count of its own records when the question asks whether
two records are the same party.

## The seed's `scoring` key

`scripts/jbeq_mdm.py`'s `score()` reads `seed["scoring"]["merge_answers"]`,
a key the task brief's field list did not name. It was discovered only
through a `KeyError` traceback from running the scorer, not by reading
`score()`'s body, and is set to `["AUTO-MERGE", "SUGGEST MERGE"]`, matching
the vocabulary's own two merge answers.

## Governing statement

U3 is the qualification set once the rules written against U1 and U2 land;
U2 became a regression set at that moment.

## Corrections after the 2026-09-06 blind audit

Applied against `~/.claude/evidence/audit-unseen-set-3-2026-09-06.md`,
section F, six corrections:

* W-06, `input` and `rationale`: rewritten as one duplicate registration
  created by two order channels sharing three years of history, with no
  separately accumulated credit balances on either side. `expected` stays
  SUGGEST MERGE. As originally written, the case scored an engine applying
  directive section 17 (same legal entity does not mean same account) as
  critically wrong.
* W-19, `critical_class`: FALSE MERGE to MERGE CAPABILITY GAP. Rule 9 read
  literally lists an unvalidated crosswalk as both the medium evidence and
  a confirmation reason, so a rule-9-literal SUGGEST MERGE must not score
  as a false merge. `decision-rules-addendum.md` rule 9 gained the missing
  branch: medium evidence with a confirmation reason stated separately
  from that evidence gives SUGGEST MERGE, medium evidence with no such
  separate reason gives ESCALATE.
* W-14, W-34, W-40, `input`: deleted the sentence in each that performed
  the discrimination for the reader (偶然拾った一致にすぎない, 単に同じ
  営業所が複数の得意先を担当しているため, たまたま); the stated facts
  stand without the conclusion attached.
* W-35, `input`: reworded so the run 「欄が用意されているが」 is no
  longer a two-for-two NO-DATA tell shared with W-28.
* W-25, `input`, `question` and `rationale`: rebuilt from a weak-evidence
  match-score shape into a blank-field-plus-corroborating-fact shape (a
  blank contract number field, a matching device serial number), testing
  addendum rule 8's other branch; `expected` stays ESCALATE.
* W-05, `expected`: KEEP SEPARATE to LINK AS RELATED. The input states
  both contact windows belong to the same company (同社), which is the
  stated relation boundary rule 1 asks for.

Label distribution after corrections: AUTO-MERGE 3, SUGGEST MERGE 3, LINK
AS RELATED 7, KEEP SEPARATE 8, REJECT MATCH 9, ESCALATE 8, NO-DATA 2.

REJECT MATCH and KEEP SEPARATE score as one class under the founder ruling
of 2026-09-06; the engine's choice between them is recorded, not scored.

## Blind audit

Row M8 (~/.claude/evidence/reflection-measures-2026-09-07.md):
`scripts/unseen_set_gate.py` reads this section before `scripts/jbeq_mdm.py`
will `prompts` or `score` against this set. This section is new as of that
row; neither this file nor `unseen-4-2026-09-06-RECORD.md` already carried
one under this heading, so the heading itself is defined here and reused
verbatim on every unseen set from this row forward.

Auditor: opus reviewer, 2026-09-06. Full audit:
`~/.claude/evidence/audit-unseen-set-3-2026-09-06.md`.

Auditor scratch hash (md5; this audit predates this estate's move to
sha256 scratch hashes, so the gate accepts 32 hex characters as well as
64): written and locked to `blind.json` before the seed was opened,
`255b277f95596036f632bb04561be176`.

Agreement: 36 of 40 (section A, counting REJECT MATCH and KEEP SEPARATE as
one class per the founder's 2026-09-06 ruling, which this file's own
scoring note above already applies).

Corrections (answer-level, field `expected`), applied in "Corrections
after the 2026-09-06 blind audit" above and verified against this file's
own `cases`:
- W-05: expected, KEEP SEPARATE to LINK AS RELATED (APPLIED)

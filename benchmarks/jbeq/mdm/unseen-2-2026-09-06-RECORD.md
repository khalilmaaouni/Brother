# JBEQ-MDM second unseen qualification set (U2), authorship record

## Authorship

Authored by a Claude subagent (Sonnet, effort high), dispatched to write a
second unseen JBEQ-MDM qualification set. The authoring session was blind to
the frozen seed (`benchmarks/jbeq/mdm/seed-2026-09-05.json`), blind to the
first unseen set's cases (`benchmarks/jbeq/mdm/unseen-2026-09-06.json`), and
blind to the engine (`scripts/jbeq_decide.py`). No test file was opened. Every
company, store, person free record and identifier in this set is invented; no
real corporation and no client term appears in it.

## Files opened while authoring

* `benchmarks/jbeq/README.md`
* `benchmarks/jbeq/mdm/fact-sheet-schema.json`
* `benchmarks/jbeq/mdm/prompts/EXTRACTOR-PROMPT.md`
* `benchmarks/jbeq/mdm/decision-rules-addendum.md`
* `scripts/jbeq_mdm.py`, the sections around `決定語彙`, `load_seed` and
  `write_prompts` (the sed range read also showed the opening lines of
  `score()`, which was not on the allowed list; nothing from that spillover
  was used to shape any case, and no further lines of `score()` were read
  beyond what that one range happened to include)
* `~/.claude/evidence/FIX-DIRECTIVE-2026-09-06.md`, sections 15 to 22
* `~/.claude/evidence/audit-unseen-set-2026-09-06.md`

On the audit file: it carries no section headers, so sections A and B (the
first unseen set's own disagreements and case by case detail) were read
along with the assigned sections C, D and E, since there was no way to slice
the file to C, D and E alone. Nothing from section B (no case id, no case
phrase, no company name, no answer from the first unseen set) was carried
into any V-01 to V-40 case below; only the structural lessons in C, D and E
(merge questions must offer merge answers, NO-DATA cases must not announce
their own absence, critical cases need merge positives, no identifier reuse
across companies, no template sentences) shaped this set's design.

Not opened, as instructed: `benchmarks/jbeq/mdm/seed-2026-09-05.json`,
`benchmarks/jbeq/mdm/unseen-2026-09-06.json`, anything under
`benchmarks/jbeq/mdm/runs/` or `unseen-prompts/`, the generalization or
identity class files, the case prompt files, `scripts/jbeq_decide.py`, and
no test file.

## Design choices

Every one of the 40 cases carries the full seven label vocabulary in
`allowed`, so a merge question can never leak its polarity by omitting a
merge answer, and a narrow four or five item list can never hint at the
family of the correct answer either. `scoring.merge_answers` is
`["AUTO-MERGE", "SUGGEST MERGE"]`, matching the frozen seed's own scoring
shape.

## Counts

* 40 cases, ids V-01 to V-40.
* Tracks, 5 cases each: entity-object, match-or-no-merge, hierarchy,
  survivorship, temporal, address, identifier, requirements-understanding.
* Critical: 33 of 40.
* Expected label counts: AUTO-MERGE 2, SUGGEST MERGE 3, LINK AS RELATED 5,
  KEEP SEPARATE 10, REJECT MATCH 11, ESCALATE 7, NO-DATA 2.
* Critical merge positives (AUTO-MERGE or SUGGEST MERGE, critical true): 5,
  ids V-03, V-06, V-07, V-32, V-34. A merge incapable engine fails these
  five critical cases outright.
* Naive same identifier or same address AUTO-MERGE traps, 12: V-01, V-02,
  V-09, V-10, V-14, V-15, V-18, V-19, V-24, V-26, V-30, V-33. Each shares a
  literal identifier or address string between two records, and each one's
  correct answer is not AUTO-MERGE.
* Explicit PROPOSAL refuted by its own facts, expected REJECT MATCH: 11 of
  the 40 cases (all 11 REJECT MATCH cases carry an explicit proposal, a
  match engine's suggestion, a batch job, a migration script, or a request
  to reassign, reverse, overwrite, or adopt a value), well past the
  required 6.
* Two records left apart with no proposal, expected KEEP SEPARATE: 10 of
  the 40 cases, all 10 KEEP SEPARATE cases, well past the required 6.
* Temporal cases with effective dates and a genuine two date conflict: 3,
  ids V-21, V-23, V-25.
* Hierarchy direction reversals: 2, ids V-12, V-13.
* Contradictions stated on both records (a differing kanji spelling of one
  name plus a differing lot number): 2, ids V-29, V-31.
* Identifier reuse across time: 1, id V-24.
* Manual override: 1, id V-16.
* Store relocation: 1, id V-03.
* Three valid hierarchy parent types (capital, trade flow, reporting)
  converging on one relation, expecting LINK AS RELATED: 1, id V-11.
* NO-DATA cases: 2, ids V-27, V-38. Each omits the missing fact plainly and
  never states that nothing else is recorded.

U2 is the qualification set once the rules authored against U1 land. U1
became a regression set at that moment.

## Corrections after the 2026-09-06 blind audit

An opus auditor read U2 blind (`~/.claude/evidence/audit-unseen-set-2-2026-09-06.md`):
37 of 40 agreement, zero label changes, and six corrections, one of them
blocking. All six are applied here.

* (blocking) `critical_class` added to every case: the case's own danger
  label in the seed's vocabulary for all 33 critical cases, null on the 7
  non-critical cases (V-05, V-10, V-15, V-19, V-30, V-35, V-40); a
  top-level `critical_classes` list added with the 12 distinct values used.
  `scripts/jbeq_mdm.py:273` reads `case["critical_class"]` on every
  critical wrong answer and no U2 case carried it before this fix, so the
  set could not be scored at all.
* V-29 (address, KEEP SEPARATE, unchanged): input reframed from "the two
  records of company X" to two candidate records that both happen to
  record that company name, removing the collision between boundary rule 1
  (a stated shared identity) and boundary rule 6 (a same-reading kanji
  variant is an absence of support, not a refuting fact).
* V-31 (identifier, KEEP SEPARATE, unchanged): same reframing, and the
  incoherent input fixed. The original stated that a third, unrelated
  company's registered name varied between two spellings; it now presents
  two candidate identifier records, each carrying its own registered name,
  one written phonetically and one in kanji.
* V-32 (identifier): rewritten into the accepted-proposal case. A data
  steward now states a proposal (提案している) to merge two identifier
  records; one record carries its own transaction history the other
  lacks, a confirmation reason under boundary rule 9. Expected changed
  from AUTO-MERGE to SUGGEST MERGE. This is the case that stops a proposal
  verb from predicting REJECT MATCH 11 of 11.
* V-06 (match-or-no-merge, AUTO-MERGE, unchanged): input rewritten to state
  the matching facts (legal-entity type, identifier, role, registered
  address, same registrar and update batch) without reciting the
  "no contradiction, no history, no tenant boundary" absence checklist.
  V-06 and V-32 are now two different shapes rather than twins reciting the
  same checklist back to the reader.
* V-12 (hierarchy content, REJECT MATCH, unchanged): track changed to
  temporal, rebalancing the track V-23's rewrite below vacated. Content
  and expected answer untouched.
* V-23: rewritten from the third temporal effective-date twin into the
  three-parents case named by the founder ruling
  (`docs/decisions/decision-p0-3-rule-d-hi01-2026-09-06.json`): capital,
  trade-flow and reporting parents each name a different, currently valid
  parent for one record. Track changed from temporal to hierarchy;
  expected changed from ESCALATE to LINK AS RELATED. U2 now tests the
  ruling it previously only dodged (V-11 gave all three hierarchy types
  the same parent).
* `benchmarks/jbeq/mdm/decision-rules-addendum.md` rule 11 (text file, not
  the seed): answer changed from KEEP SEPARATE to LINK AS RELATED for a
  record with three valid parents of different hierarchy types, citing the
  founder ruling above. The shipped rule had contradicted that ruling.
* V-36 through V-40 (requirements, unchanged content and expected answers):
  track spelling corrected from "requirements-understanding" to
  "requirements", the name `scripts/jbeq_decide.py`'s UNSUPPORTED_TRACKS and
  the seed's own vocabulary actually use. The U2 qualification run of 17:19
  JST decided these five cases with the general rule table because of this
  spelling; a re-decision after the fix routes them to direct answers.

Counts after the corrections:

* 40 cases, ids V-01 to V-40, unchanged.
* Tracks, 5 cases each (unchanged): entity-object, match-or-no-merge,
  hierarchy, survivorship, temporal, address, identifier, requirements.
* Critical: 33 of 40, unchanged.
* Expected label counts: AUTO-MERGE 1, SUGGEST MERGE 4, LINK AS RELATED 6,
  KEEP SEPARATE 10, REJECT MATCH 11, ESCALATE 6, NO-DATA 2.
* Critical merge positives (AUTO-MERGE or SUGGEST MERGE, critical true): 5,
  ids V-03, V-06, V-07, V-32, V-34, unchanged in count; V-32 stays a merge
  positive under its new label.
* Naive same identifier or same address AUTO-MERGE traps: 12, unchanged
  (V-01, V-02, V-09, V-10, V-14, V-15, V-18, V-19, V-24, V-26, V-30, V-33).
* Explicit proposal verb (提案 / 求めている / リクエスト / 依頼) by expected
  label: REJECT MATCH 11 of 11, SUGGEST MERGE 1 of 4 (V-32). A proposal
  verb predicts REJECT MATCH on 11 of 12 carrying cases now, not 11 of 11.

A merge-incapable engine scores under the 95 percent bar, and a proposal
verb no longer predicts REJECT MATCH.

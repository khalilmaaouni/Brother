# JBEQ-MDM unseen qualification set 5 (U5), authorship record

2026-09-07. 40 cases, `W5-01` to `W5-40`, authored blind for
`benchmarks/jbeq/mdm/unseen-5-2026-09-07.json`. This record follows the same
purpose as its sibling U4 record: state what was opened, what was
deliberately never opened, the design choices, and the counts, so a reader
can check the claim of blindness without re-deriving it.

Note on setup: the task's instructed worktree path
(`/private/tmp/bh-lane-unseen5b`, branch created from `origin/main` of this
same repository) was used for the reading and drafting phase below. When the
harness refused a plain-file write there (a worktree-isolation guard scoped
this agent to its own assigned worktree), the branch was recreated from
`hub/main` inside the assigned worktree instead, per the task's own fallback
instruction, and every generated file was reproduced there from the same
generator script and re-verified with the same self-check before this record
was written.

## Files opened

- `benchmarks/jbeq/README.md` (full file): the three readiness levels, the
  decision vocabulary in prose, the critical-case rule and its class list,
  the boundary-rule history sections, the CHANGELOG.
- `benchmarks/jbeq/mdm/fact-sheet-schema.json` (full file): the fact-sheet
  shape and five worked rule examples, read for the identity-kind fields
  and the enum lists.
- `benchmarks/jbeq/mdm/prompts/EXTRACTOR-PROMPT.md` (full file): the
  extraction rules 1 to 4, the identity-kind field definitions, the worked
  example, and the closing "what you never do" section.
- `benchmarks/jbeq/mdm/decision-rules-addendum.md` (full file): boundary
  rules 5 to 13 in full, bilingual.
- `scripts/jbeq_mdm.py`, by grep and sed range only, never the whole file:
  - `grep -n "決定語彙"` to locate the vocabulary block, then
    `sed -n '180,260p'` to read the `VOCABULARY` constant and its four
    boundary rules verbatim (already quoted in README.md, so no new
    content).
  - `sed -n '276,420p'` to read `load_seed` and `write_prompts`. This range
    overshot the two requested functions: it also captured
    `answers_equivalent`, `score`, `named`, and the first lines of
    `cmd_score`, from one sed call whose end line was picked too generously.
    None of this range names a seed case, an answer, or a rationale; it is
    scoring mechanics (the false-merge/critical-wrong/conservative split,
    the `REFUTED_IDENTITY_CLASS` equivalence, `CAUTION_RANK`'s ordering)
    that README.md's "The critical case rule" section already describes in
    prose. Disclosed here as an honest overshoot, not a case-content leak.
  - `sed -n '60,100p'` while searching for the vocabulary heading picked up
    `REPO`/`SEED` path constants, the exit codes, `SCORER_VERSION`'s
    changelog comment, and `REFUTED_IDENTITY_CLASS`'s definition, all
    mechanism-level and already stated in README.md's prose (the founder
    ruling on REJECT MATCH/KEEP SEPARATE equivalence).
  - `sed -n '255,276p'` to reach `CANONICAL_TRACKS`, which also carried
    `CAUTION_RANK` immediately above it in the same range.
- `~/.claude/evidence/FIX-DIRECTIVE-2026-09-06.md`, sections 15 to 22 only
  (`sed -n '593,860p'`, located via `grep -n "^#"`): the P0.3 JBEQ-MDM
  critical-failure findings, the false-merge-is-hard-failure ruling, the
  MM-01 generalization instruction, the merge-evidence-threshold shape, the
  named critical test classes, the mutation-testing instruction, and the
  exit gate. Read for the merge-safety framing behind AUTO-MERGE's evidence
  threshold; no case content, this section carries none.
- `~/.claude/evidence/audit-unseen-set-4-2026-09-06.md`, structural sections
  only: section 3 ("Structural holes") and section 4 ("Counts I
  recomputed"), located via `grep -n "^#"` and read with a targeted
  `sed -n '24,45p'`. Sections 1, 2 and 5 (Agreement, Disagreements,
  Corrections), which carry U4's own case ids, phrases and answers, were
  never opened.

## Files never opened

`seed-2026-09-05.json`, `unseen-2026-09-06.json`, any `unseen-2-`,
`unseen-3-`, or `unseen-4-` seed file, any `*-RECORD.md` besides this one,
anything under `runs/`, any `*-prompts` directory besides the one this
change generates, `generalization-cases-2026-09-05.json`,
`generalization-cases-2026-09-05-rules-8-to-11.json`,
`scripts/jbeq_decide.py`, and every test file. Sections 1, 2 and 5 of the
U4 audit were seen only as section headings in a listing, never opened for
content.

## Design choices

- **Track allocation.** `CANONICAL_TRACKS` (grepped from `jbeq_mdm.py`) has
  eight members. 40 cases split 5 per track exactly:
  `address`, `entity-object`, `hierarchy`, `identifier`,
  `match-or-no-merge`, `temporal`, `survivorship`, `requirements`.
- **Vocabulary.** Every non-temporal case carries the full seven-answer
  vocabulary (`AUTO-MERGE`, `SUGGEST MERGE`, `LINK AS RELATED`,
  `KEEP SEPARATE`, `REJECT MATCH`, `ESCALATE`, `NO-DATA`) as `allowed`, so
  an identity question always offers a merge answer by construction. The
  five `temporal` cases use a track-local vocabulary (`R1`, `R2`, `R3`,
  `NO-DATA`), matching the vocabulary block's own line that answers outside
  the seven (source names, R1/R2/R3, DECIDED/ASSUMED/INFERRED/UNKNOWN, and
  the like) are answered per that question's own allowed list; every
  `temporal` case's input states what R1, R2 and R3 denote before asking
  which one governs.
- **NO-DATA authorship (2 cases, `W5-04`, `W5-28`).** Both simply omit the
  deciding fact (a blank prefecture with no corroborating banchi or postal
  code fact in `W5-04`; two systems' conflicting effective dates with no
  stated precedence policy in `W5-28`) rather than stating that nothing
  else exists. An earlier draft of `W5-04` read that no other description
  could be found in the form; rewritten before finalizing this file to end
  the form after its own fields, since the U4 audit's structural-holes
  section named exactly this pattern (self-announcing the gap) as a
  defect on two of its own cases. Two other drafted sentences elsewhere in
  the set (in what became `W5-20` and `W5-39`, neither a NO-DATA case)
  also carried a totalizing "nowhere in the record" or "not found at all"
  phrasing and were rewritten the same way, out of the same caution, even
  though those two cases are not NO-DATA cases themselves.
- **Critical classes rest on the input, not the seed's own expected
  action.** The U4 audit named this class of defect directly (several
  mis-assigned classes where the class named the harm of the EXPECTED
  action rather than the WRONG one). Every critical case here was written
  the other way: name the class the wrong answer would cause, and only
  after the input states the fact that class rests on. `FALSE MERGE` is
  reserved for cases where the exposed risk is an actual over-merge on
  weak support; `HIERARCHY REVERSAL` for cases where a write proposal
  would reverse a stated parent; `WRONG PAYER` for cases where fragmenting
  or wrongly merging a payer/vendor code misroutes payment; and so on
  through the eleven classes README.md's section 28 names.
- **Role glosses.** The ship-to term is used only for a ship-to context
  (`W5-05`); the sold-to term is not used anywhere in the set; the
  supplier and payer terms are used in their ordinary purchasing/payer
  senses, never substituted for the two glossed role terms.
- **No template sentences.** Cases vary in surface form: ledger/registry
  excerpts, request forms, an inter-departmental inquiry, a written policy
  memo quoted and then applied to a fresh pair. Company names use an
  invented heavenly-stem/zodiac naming scheme, the same style already used
  in `fact-sheet-schema.json`'s own worked examples, chosen specifically
  because it reads as generated rather than resembling any real company.
- **Identifier hygiene.** No corporate number, tax registration number, or
  alphanumeric code is reused across two different invented companies
  anywhere in the file, checked by regex over every case's `input`, except
  the three cases built specifically to test identifier reuse (`W5-16`
  customer code reuse after closure, `W5-17` corporate-number reuse after
  dissolution, `W5-18` site-id reuse after a franchise change). One
  accidental collision was found and fixed during authorship: a corporate
  number drafted for `W5-19` initially matched the one drafted for
  `W5-08`, for two unrelated invented companies; `W5-19`'s number was
  changed before this file was finalized.

## Counts (self-check output, quoted verbatim below)

Superseded by the 2026-09-07 blind audit and its corrections (see the new
section below and "Blind audit"). Quoted here is the ORIGINAL authoring
self-check, kept for the record of what was blind-authored:

```
n: 40
critical: 35
critical merge positives: 9 (>= 6 required)
per track: address 5, entity-object 5, hierarchy 5, identifier 5,
  match-or-no-merge 5, temporal 5, survivorship 5, requirements 5
expected outside allowed: 0
tracks outside CANONICAL_TRACKS: 0
duplicate ids: 0
banned-term inputs: 0
critical without class: 0
repeated 14-character input openings: none
label distribution: SUGGEST MERGE 6, KEEP SEPARATE 4, NO-DATA 2,
  REJECT MATCH 12, AUTO-MERGE 3, LINK AS RELATED 4, ESCALATE 5,
  R1 2, R2 1, R3 1
```

The self-check AFTER the 2026-09-07 corrections below (re-run against the
corrected file, the numbers that now hold):

```
n: 40
critical: 35
critical merge positives: 8 (>= 6 required)
per track: address 5, entity-object 5, hierarchy 5, identifier 5,
  match-or-no-merge 5, temporal 5, survivorship 5, requirements 5
expected outside allowed: 0
tracks outside CANONICAL_TRACKS: 0
duplicate ids: 0
banned-term inputs: 0
critical without class: 0
every critical critical_class is a member of critical_classes: yes (0 not)
repeated 14-character input openings: none
shared 20-character input fragment collisions: 0
label distribution: SUGGEST MERGE 4, AUTO-MERGE 4, KEEP SEPARATE 5,
  NO-DATA 2, REJECT MATCH 13, ESCALATE 4, LINK AS RELATED 4,
  R1 2, R2 1, R3 1
```

Other required minimums, re-counted by hand against the corrected case list
(some shift from the original authoring count above, because W5-06 no
longer expects a merge, W5-02/W5-05/W5-21 were re-authored, and W5-15/34/35
changed track; this is the honest post-correction state, not the original):

- Refuted-identity cases (question proposes a match or relation on a
  stated ground, expected `REJECT MATCH` or `KEEP SEPARATE`): 11
  (`W5-03, W5-05, W5-09, W5-15, W5-16, W5-17, W5-18, W5-19, W5-24, W5-25,
  W5-34`; minimum 6).
- Record-write shaped questions: 5 (`W5-02, W5-10, W5-13, W5-21, W5-32`;
  `W5-38` carries the same shape for a hierarchy field and was already
  present before this correction; minimum 3).
- Relocation or renamed-area cases: 2 remain expected `SUGGEST MERGE`
  (`W5-01, W5-07`); `W5-06` is still relocation-shaped but now expects
  `ESCALATE` (a missing identifier is weak evidence, never medium, so the
  relocation shape alone does not guarantee `SUGGEST MERGE`); `W5-02` left
  this group entirely, re-authored as a survivorship case. The "all four
  expected SUGGEST MERGE" framing from the original count no longer holds
  and is not restated as a requirement.
- Identifier reuse cases: 3 (`W5-16, W5-17, W5-18`; minimum 3, unchanged).
- Medium-evidence cases: 3 (`W5-20, W5-22, W5-23`; minimum 3, unchanged).
- NO-DATA cases without a self-announcing sentence: 2
  (`W5-04, W5-28`; exactly 2 required, unchanged).
- Every case carries a `rationale`; verified by the self-check
  (missing-rationale check, zero hits).

## Reproduce

```
python3 scripts/jbeq_mdm.py prompts benchmarks/jbeq/mdm/unseen-5-prompts \
  --seed benchmarks/jbeq/mdm/unseen-5-2026-09-07.json
# -> wrote 40 blind prompt file(s) to benchmarks/jbeq/mdm/unseen-5-prompts

grep -lE 'expected|rationale|critical_class' \
  benchmarks/jbeq/mdm/unseen-5-prompts/*.md | wc -l
# -> 0

python3 scripts/test_jbeq_mdm.py
# -> Ran 36 tests ... OK
```

This set has not been scored against any answer file: it is authored and
blind-checked only. A JBEQ-MDM score requires a fresh blind answerer with no
access to this file or to the seed, exactly as README.md's "The rule that
makes a score mean anything" requires.

## Corrections after the 2026-09-07 blind audit

Auditor: opus reviewer, 2026-09-07, PR 488. Full audit:
`~/.claude/evidence/audit-unseen-set-5-2026-09-07.md`. Every correction
below is the audit's own section 5 item, applied in full (its primary form,
where the audit gave one), plus the structural fixes section 3 named. IDs
are stable: no case was renumbered, only re-tracked, re-worded or
re-authored under its existing id.

**Answer-level (the two blockers the audit's verdict named):**

- `W5-06`: `expected` SUGGEST MERGE to ESCALATE. Rule 9 grades a missing
  identifier weak, and weak evidence gives ESCALATE regardless of the
  confirmation reason; the old rationale's own first clause already
  conceded no authoritative identifier was stated. `rationale` rewritten
  to match.
- `W5-25`: `expected` ESCALATE to KEEP SEPARATE, matching `W5-34`'s
  identical fact pattern (shared reading, identifiers blank both sides,
  nothing else), which the seed already answered KEEP SEPARATE under
  rule 6. `rationale` rewritten to match.

**Rationale-only fixes:**

- `W5-01`: rationale no longer grades the matching representative name and
  phone as "medium evidence" (the same weak-to-medium upgrade that made
  `W5-06` wrong); it now says the input states the relocation itself, so
  identity is stated, not inferred.
- `W5-32`: rationale cited "rule 13" for the record-write/refuting-fact
  rule; that rule is 14 (13 is the renamed-area rule). Fixed.
- `W5-39`: rationale cited "rules 9 and 18"; there is no rule 18. Fixed to
  "rule 9" and extended to address the added transaction-history fact
  below.
- `W5-02`: fully re-authored (see below), which also retires the old
  rationale's "reads like notation_variant_only (rule 12)" phrasing the
  audit flagged.

**Input/question fixes (fact and premise corrections):**

- `W5-24`: `input` and `question` changed from "same name notation"
  (`同名表記`, `名称表記が同じ`) to "same building address" (`同一住所`,
  `建物住所が同一`) as the stated matching ground, because the input's own
  named tenants already refuted the "same name" premise. `rationale`
  updated to match ("matching building address").
- `W5-17`: `input` rewritten so the ledger's reassignment note is stated as
  the note's own error, not an authoritative government-registry fact: a
  corporate number is never re-assigned after dissolution. `expected`
  (REJECT MATCH) unchanged.
- `W5-31`: `input` rewritten from "two different invoice numbers reissued
  under one corporate number" (impossible for a corporation, which holds
  one T-number tied to its one corporate number) to one T-number shared by
  both records, differing only in registration status (valid vs
  cancelled). `expected` (AUTO-MERGE) unchanged.
- `W5-03` and `W5-15`: `input` no longer recites boundary rule 1's own
  checklist verbatim (`法人番号の一致、親子関係、役割の対、商流上の経由の
  いずれも記載は無く`), replaced with `台帳には両者を結ぶ記載が無く` in
  both cases.
- `W5-37`, `W5-39`, `W5-40`: `input` gains one fact the quoted policy does
  not itself settle (a matching phone number at `W5-37`, five years of
  transaction history at `W5-39`, a staff wish to simplify management at
  `W5-40`), so the answer requires applying the policy over a real
  temptation rather than reading the policy's own words straight into the
  answer. `rationale` extended at `W5-37`, `W5-39` and `W5-40` to name why
  the added fact does not change the outcome.

**Track corrections and the two re-authored survivorship cases:**

- `W5-15`: track hierarchy to address (the case carries no hierarchy
  content; it is two legal entities sharing a building).
- `W5-34`: track survivorship to match-or-no-merge (a reading-match false
  merge candidate, the same shape as `W5-25`). Content unchanged beyond the
  rename below.
- `W5-35`: track survivorship to address (a blank-prefecture-corroborated
  case, the same shape as `W5-04`'s sibling). Content unchanged.

Moving `W5-34` and `W5-35` out of survivorship left it with three cases
(`W5-31, W5-32, W5-33`) that never actually test attribute-level
survivorship (the audit's own finding: the seed's real survivorship shape,
an expired override losing to a higher authority, sits on the temporal
track at `W5-26`). Two cases were re-authored to close that gap, and the
five-per-track balance is restored by two further moves the audit implies
but does not name case by case:

- `W5-02`: track address to survivorship, fully re-authored as
  "authoritative source's attribute wins over a non-authoritative one" (a
  record-write proposal citing a matching corporate number and a
  governance policy naming the core ERP, not a sales rep's spreadsheet, as
  the address attribute's source of record). `expected` AUTO-MERGE,
  `critical_class` HISTORICAL REASSIGNMENT to SOURCE PRECEDENCE VIOLATION.
- `W5-21`: track match-or-no-merge to survivorship, fully re-authored as
  "an expired override losing to a higher authority" (a manual address
  override whose stated validity window has closed, proposed for adoption
  over the still-current core ERP value). `expected` REJECT MATCH,
  `critical_class` UNREVERSIBLE MERGE WITHOUT EVIDENCE to SOURCE
  PRECEDENCE VIOLATION.
- `W5-05`: track address to hierarchy, fully re-authored (two hierarchy
  nodes sharing a name but stated to belong to different hierarchy
  dimensions, round 10's addition to rule 10), to refill hierarchy's
  deficit left by `W5-15` leaving it. `expected` REJECT MATCH unchanged
  from the case it replaced by coincidence of vocabulary, `critical_class`
  CROSS-TENANT DATA LEAK to HIERARCHY REVERSAL.

Every id that changed track, stated once: `W5-02, W5-05, W5-15, W5-21,
W5-34, W5-35`.

**Company-name and role-gloss fixes:**

Renamed the second (or later) occurrence of every name the audit's section
3 flagged as reused with a contradictory role, inventing a fresh name each
time and, where the audit also flagged a wrong role gloss on that same
occurrence, choosing the new name's business-suffix kanji to match the
stated role instead:

- `卯月通信` at `W5-40` (kept at `W5-14`) to `己崎物産`, also fixing the
  "materials trading house" gloss (`物産` fits a trading house; `通信` did
  not).
- `酉川紙業` at `W5-33` (kept at `W5-16`) to `壬田運送`, also fixing the
  "logistics provider" gloss (`運送` fits; `紙業` did not).
- `申村塗料` at `W5-33` (kept at `W5-16`) to `辛原塗装`.
- `未崎菓子` at `W5-15` and `W5-33` (kept at `W5-03`, its only business-typed
  occurrence) to `己岡繊維` and `庚原食品` respectively (two different fresh
  names, since the two occurrences are unrelated scenarios).
- `戌井硝子` at `W5-17` (kept at `W5-05`) to `丑島硝子`; at `W5-34` to
  `戌亥硝子`, chosen because it shares its counterpart `乾硝子`'s reading
  (いぬい) with different kanji, since the reading match is the point of
  that case.
- `子安運輸` at `W5-24` (kept at `W5-11, W5-12`) to `癸原工芸`.

Fixed the two remaining wrong role glosses that did not already involve a
renamed occurrence:

- `W5-07`: `乙部工業` (`工業` reads as manufacturing) renamed to `乙部商事`
  to match the stated clothing-chain retail role.
- `W5-08`: `丙谷紡績` (`紡績` reads as textile manufacturing) renamed to
  `丙谷物産` to match the stated stationery-wholesale role.
- `W5-11`: `子安運輸` unchanged (kept, see above), but its business
  descriptor changed from "楽器販売会社" (a musical instrument retailer)
  to "運送業を営む" (a transport business), matching what `運輸` already
  says.

None of these five was itself a name-reuse defect (each of `W5-07, W5-08,
W5-11` uses a name that appears nowhere else in the set); only the stated
business type was wrong.

**Self-check after every correction above**, run against the corrected
file, is quoted in the Counts section above; the `python3
scripts/test_jbeq_mdm.py` and `python3 scripts/jbeq_mdm.py prompts`
reproduce lines in this file were re-run against the corrected file and
are current.

## Blind audit

Row M8 (~/.claude/evidence/reflection-measures-2026-09-07.md):
`scripts/unseen_set_gate.py` reads this section before `scripts/jbeq_mdm.py`
will `prompts` or `score` against this set. See
`unseen-3-2026-09-06-RECORD.md`'s own "Blind audit" section for the note on
why this heading is new as of this row.

Auditor: opus reviewer, 2026-09-07, PR 488. Full audit:
`~/.claude/evidence/audit-unseen-set-5-2026-09-07.md`.

Auditor scratch hash (sha256, printed before the seed was opened):
`e3f41e6bd48cc4dfb6c814b31ed66770e523965e80f61de6bcf8c6e566bc96d5`.

Agreement: 37 of 40 (section 1).

Corrections (answer-level, field `expected`), named by the audit's own
section 5, APPLIED to this file's `cases` (see "Corrections after the
2026-09-07 blind audit" above for the full change list, including the
structural fixes beyond these two answer-level items):
- W5-06: expected, SUGGEST MERGE to ESCALATE (APPLIED)
- W5-25: expected, ESCALATE to KEEP SEPARATE (APPLIED)

`python3 scripts/unseen_set_gate.py benchmarks/jbeq/mdm/unseen-5-2026-09-07.json`
now prints PASS.

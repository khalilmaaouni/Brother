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

Other required minimums, counted by hand against the case list:

- Refuted-identity cases (question proposes a match on a stated ground,
  expected `REJECT MATCH` or `KEEP SEPARATE`): 8
  (`W5-09, W5-16, W5-17, W5-18, W5-19, W5-24, W5-32, W5-34`; minimum 6).
- Record-write shaped questions: 3 (`W5-10, W5-13, W5-32`; minimum 3).
- Relocation or renamed-area cases, all expected `SUGGEST MERGE`: 4
  (`W5-01, W5-02, W5-06, W5-07`; minimum 4).
- Identifier reuse cases: 3 (`W5-16, W5-17, W5-18`; minimum 3).
- Medium-evidence cases: 3 (`W5-20` unvalidated crosswalk, no separately
  stated confirmation reason, expects `ESCALATE`; `W5-22` unvalidated
  crosswalk with a separately stated transaction-history reason, expects
  `SUGGEST MERGE`; `W5-23` an explained conflicting identifier with a
  separately stated irreversible-payment-run reason, expects
  `SUGGEST MERGE`; minimum 3, both required scenarios present).
- NO-DATA cases without a self-announcing sentence: 2
  (`W5-04, W5-28`; exactly 2 required).
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
# -> Ran 31 tests ... OK
```

This set has not been scored against any answer file: it is authored and
blind-checked only. A JBEQ-MDM score requires a fresh blind answerer with no
access to this file or to the seed, exactly as README.md's "The rule that
makes a score mean anything" requires.

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
section 5, NOT applied to this file's `cases`: the standing estate rule
against editing a benchmark answer means this scorer-facing implementation
change does not itself carry the fix, so the gate correctly refuses this
set until a separate, deliberate change lands it (or a caller passes
`--regression` to re-decide the spent set anyway):
- W5-06: expected, SUGGEST MERGE to ESCALATE (NOT APPLIED)
- W5-25: expected, ESCALATE to KEEP SEPARATE (NOT APPLIED)

Per the audit's own verdict ("NOT READY as it stands... READY FOR
QUALIFICATION AFTER CORRECTIONS"), this is the correct state for the gate
to report: `python3 scripts/unseen_set_gate.py
benchmarks/jbeq/mdm/unseen-5-2026-09-07.json` prints FAIL naming both
lines above, exit 1.

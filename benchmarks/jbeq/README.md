# JBEQ: the Japanese Business Engineering Qualification

JBEQ is created by section 10 of the founder's morning steering directive of
2026-09-05 (`~/.claude/evidence/MORNING-STEERING-2026-09-05.md`). This
directory holds the qualification and, so far, one track: JBEQ-MDM, as a seed
suite that a machine can score.

This file follows the shape of its sibling `benchmarks/gauntlets/README.md`:
what is here, what state it is in, what is honest about the scope, and a
Reproduce section whose commands run as written.

## Purpose (section 10)

> Measure whether Brother can perform real engineering work expressed in
> Japanese, not merely retrieve Japanese entities.

The existing Japanese benchmark, `benchmarks/ja-adversarial/`, asks roughly
WHICH ENTITY IS THIS. Real master data work asks whether two records are the
same entity, the same legal entity but different customer accounts, the same
customer but different sites, which source is authoritative, which attribute
survives, whether records merge or link, whether the merge can be reversed, and
what happens when the source changes tomorrow. Passing entity retrieval says
nothing about any of that, which is why retrieval qualification and business
engineering qualification are separate levels below.

## The three readiness levels (section 46, verbatim)

LEVEL 1, JAPANESE RETRIEVAL QUALIFIED. Required: 245/245, 78/78 frozen blind,
13/13 negative. "This proves: entity retrieval/disambiguation. Nothing more."

LEVEL 2, JAPANESE BUSINESS ENGINEERING QUALIFIED. Required: "retrieval
qualification + JBEQ general requirements track + Japanese implementation tasks
+ Japanese handover." This permits the claim: "Brother can execute qualified
engineering tasks expressed in Japanese."

LEVEL 3, JAPANESE MDM ENGINEERING QUALIFIED. Required: "JBEQ-MDM critical suite
+ zero critical false merges + hierarchy qualification + survivorship
qualification + temporal qualification + migration qualification + 100%
reconciliation integrity + end-to-end Japanese MDM task." This permits the
stronger claim: "Brother has passed a dedicated Japanese MDM engineering
qualification covering business rules, identity, hierarchy, survivorship,
migration and reconciliation. This is the level relevant to real enterprise MDM
work."

Brother stands at none of the three today. What exists in this directory is a
seed of Level 3's first suite, not a pass of it.

## What is here

| Path | What it is | State |
|---|---|---|
| `mdm/seed-2026-09-05.json` | the frozen JBEQ-MDM seed, 70 cases | FROZEN, NOT YET ANSWERED BLIND |
| `mdm/prompts/` | one blind prompt file per case, generated from the seed | generated, carries no ground truth |
| `../../scripts/jbeq_mdm.py` | writes the prompts, scores an answer file | runs today |
| `../../scripts/test_jbeq_mdm.py` | drives the seed and the scorer backwards | runs today |

The seed is the morning mix section 27 asks for and nothing more: 10 entity
object, 10 match or no-merge, 10 hierarchy, 10 survivorship, 10 temporal, 10
address, 5 identifier, 5 requirements understanding. 33 of the 70 are critical.

Every company, store, person, address and identifier in the seed is invented.
No real corporation and no client term appears in it.

## The rule that makes a score mean anything

A case is scored ONLY against an ANSWER FILE produced by an agent that never
saw the ground truth.

`scripts/jbeq_mdm.py prompts <dir>` writes one file per case holding the input,
the question and the allowed answers, in the seed's own canonical order, and
never the expected answer, never the rationale, never the critical flag. The
answerer reads those files, with no access to `seed-2026-09-05.json`, and
writes `{"CASE-ID": "ANSWER"}`. `scripts/jbeq_mdm.py score <answers.json>`
reads that file and prints the numbers.

A number produced any other way is not a JBEQ-MDM score. In particular, a run
where the answering agent could read the seed proves nothing, and neither does
a score the seed was edited to raise: the seed is frozen the way
`benchmarks/ja-adversarial/adversarial-ja-corpus.json` is frozen.

## The critical case rule (section 28)

Not every case has equal severity. The seed marks critical cases explicitly and
names the class of harm for each. The classes are section 28's own list: FALSE
MERGE, WRONG LEGAL ENTITY, CROSS-CUSTOMER CONTAMINATION, WRONG TAX IDENTITY,
WRONG PAYER, WRONG STORE ASSIGNMENT, HISTORICAL REASSIGNMENT, HIERARCHY
REVERSAL, SOURCE PRECEDENCE VIOLATION, CROSS-TENANT DATA LEAK, UNREVERSIBLE
MERGE WITHOUT EVIDENCE.

Required accuracy on critical cases is 100 percent. Section 28's own sentence:
a single false merge in a critical test means JBEQ-MDM NOT READY. The scorer
enforces exactly that. It prints `critical false merges: k of n`, counting a
critical case answered with a merge where the expected answer is not a merge,
and any critical case answered wrong. If k is not zero it prints `JBEQ-MDM NOT
READY` and exits 1. Otherwise it prints `JBEQ-MDM SEED: p of 70` and exits 0.

NO-DATA is never a pass. A case the answer file does not carry is named in the
output and counted as unanswered, never as passed; an answer file that answers
nothing exits 3.

The dangerous cases the directive names by hand are all in the seed: same
corporate number with different stores expects LINK rather than MERGE (MM-01);
a closed store plus a new store at a similar address leaves the historical
transactions where they were (TM-01); legal parent, commercial parent and
reporting parent differ and are all valid at once (HI-01); an expired manual
override loses to the higher authority (SV-06); string equivalent addresses
that are different physical locations (AD-03, AD-04, AD-10); and 法人番号,
適格請求書発行事業者番号 and internal identifiers are never interchangeable
(MM-02, ID-01, ID-04).

## The long term track table (section 27)

The seed is a beginning. The full future target the directive sets is roughly
745 cases, and this table is copied from section 27 without addition.

| Track | Future Cases | Target |
|---|---|---|
| Entity identity | 100 | 100% critical |
| Address normalization | 100 | at least 98%, 100% critical |
| Corporate identifiers | 60 | 100% |
| Hierarchy reasoning | 80 | 100% critical |
| Match/merge/no-merge | 100 | 100% critical negatives |
| Survivorship | 75 | 100% |
| Temporal logic | 60 | 100% critical |
| Requirements to mapping | 40 | at least 95% |
| Requirements to implementation | 30 | at least 95% |
| Migration/reconciliation | 30 | 100% data integrity |
| Defect triage | 40 | at least 95% |
| Japanese handover | 30 | at least 95% |

Section 10 also names the tracks JBEQ will eventually hold beyond MDM:
JBEQ-DATA-MIGRATION, JBEQ-API, JBEQ-DATA-PIPELINE, JBEQ-SECURITY,
JBEQ-BUG-TRIAGE, JBEQ-REQUIREMENTS, JBEQ-ARCHITECTURE, JBEQ-OPERATIONS. None of
them exists yet, and the directive is explicit that they are not to be built
all at once.

## What is honestly missing

The seed has never been answered blind, so JBEQ-MDM has no score. The
end-to-end Japanese MDM task section 27 asks for beside the 70 cases is not
here either. Neither is any track other than MDM. Until a blind answer file
exists, the only true statement about this qualification is that its first
suite can be scored, not that it has been.

## Reproduce

From the repository root:

```
python3 scripts/test_jbeq_mdm.py
# -> Ran 13 tests ... OK

python3 scripts/jbeq_mdm.py prompts /tmp/jbeq-prompts-check
# -> wrote 70 blind prompt file(s) to /tmp/jbeq-prompts-check
ls /tmp/jbeq-prompts-check | wc -l
# -> 70
```

The test suite drives the controls backwards rather than merely asserting them:
a prompt file that carried the expected answer or the rationale fails the leak
test, one critical case answered with a merge prints JBEQ-MDM NOT READY and
exits 1, and an answer file that answers nothing exits 3 instead of scoring
zero as a pass.

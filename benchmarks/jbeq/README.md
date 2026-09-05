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
| `mdm/seed-2026-09-05.json` | the frozen JBEQ-MDM seed, 70 cases | FROZEN, corrected once (see CHANGELOG), answered blind once, NOT PASSED |
| `mdm/prompts/` | one blind prompt file per case, generated from the seed | generated, carries the decision vocabulary and no ground truth |
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
`benchmarks/ja-adversarial/adversarial-ja-corpus.json` is frozen. The one edit
made to the answers since the freeze is recorded case by case in the CHANGELOG
at the end of this file, with what changed and why, and it did not turn a NOT
READY into a pass.

## The decision vocabulary

The first blind run of the seed, on 2026-09-05, answered 58 of 70 with 9
critical cases wrong. NONE of the nine was a merge. Seven of the nine were a
choice between KEEP SEPARATE, LINK AS RELATED and REJECT MATCH, and the prompts
listed those seven answers without defining any of them. A score built that way
measures two things at once: master data judgement, and a guess at what a label
is supposed to mean. Only the first is worth measuring.

So the seven answers are defined here, the definitions are carried verbatim
into every prompt file, and the seed's own answers were re-read against them.
`scripts/test_jbeq_mdm.py` fails if the block below and the block in
`scripts/jbeq_mdm.py` ever drift apart, and fails if any prompt file lacks it.

<!-- VOCABULARY BLOCK, generated from jbeq_mdm.VOCABULARY, do not hand edit -->
## 決定語彙

回答は次の意味で用いる。1件につき1つだけを選ぶ。

AUTO-MERGE
  JA: 同一のオブジェクトであり、統合の根拠が十分で人の確認を要さない。
  EN: The same object, and the evidence is enough to merge it without a person.
SUGGEST MERGE
  JA: 同一のオブジェクトである可能性が高いが、確定はデータスチュワードの確認を経る。
  EN: Probably the same object, and a data steward confirms before it is merged.
LINK AS RELATED
  JA: 別のマスタオブジェクトだが同一性を示す事実または階層を共有しており、レコードは分けたまま関係として明示的に記録する。
  EN: Different master data objects that share an identity fact or a hierarchy, kept as separate records with the relation written down.
KEEP SEPARATE
  JA: 別のオブジェクトであり、記録すべき関係も無く、両方のレコードがそのまま有効である。
  EN: Different objects with no relation worth recording, and both records stay valid as they are.
REJECT MATCH
  JA: 提示された照合または関係付けの依頼そのものが誤りであり、その関係を記録してはならず、既存の同種のリンクは削除する。
  EN: The proposed match or link is wrong, the records must not carry that relation, and any existing link of that kind is removed.
ESCALATE
  JA: 判断材料は揃っているがルールでは決着せず、業務側の判断に上げる。
  EN: The facts are there but the rule set cannot close it, so the business decides.
NO-DATA
  JA: 判断に必要な事実が入力に無く、決定できないものとして依頼元に差し戻す。
  EN: The input lacks a fact the decision needs, so nothing is decided and the request goes back to where it came from.

境界 (which answer when two look close):

1. LINK AS RELATED か KEEP SEPARATE か。入力自体が関係(共通の法人番号、親子、役割の対、商流上の経由など)を述べていれば LINK AS RELATED、述べていなければ KEEP SEPARATE。
   EN: Answer LINK AS RELATED when the input itself states a relation. Answer KEEP SEPARATE when it states none.
2. KEEP SEPARATE か REJECT MATCH か。同一性または提示された階層を否定する事実が入力にあれば REJECT MATCH、単に裏付けが無いだけなら KEEP SEPARATE。
   EN: Answer REJECT MATCH when the input carries a fact that refutes the proposed identity or the proposed hierarchy. Answer KEEP SEPARATE when the match is merely unsupported.
3. ESCALATE か NO-DATA か。判断材料はあるが規則で決着しないなら ESCALATE、材料そのものが無いなら NO-DATA。
   EN: Answer ESCALATE when there is something to judge but the rules cannot close it. Answer NO-DATA when there is nothing to judge.
4. AUTO-MERGE か SUGGEST MERGE か。取引履歴があるか処理が取り消せず人が見るべきなら SUGGEST MERGE、それが無く識別子と役割が一致するなら AUTO-MERGE。
   EN: Answer SUGGEST MERGE when history or an irreversible step means a person should look. Answer AUTO-MERGE when identifiers and roles agree and nothing needs a person.

上の語彙に無い回答(出典名、R1/R2/R3、DECIDED/ASSUMED/INFERRED/UNKNOWN など)は、その設問の許容回答欄の表記どおりに答える。

## The critical case rule (section 28)

Not every case has equal severity. The seed marks critical cases explicitly and
names the class of harm for each. The classes are section 28's own list: FALSE
MERGE, WRONG LEGAL ENTITY, CROSS-CUSTOMER CONTAMINATION, WRONG TAX IDENTITY,
WRONG PAYER, WRONG STORE ASSIGNMENT, HISTORICAL REASSIGNMENT, HIERARCHY
REVERSAL, SOURCE PRECEDENCE VIOLATION, CROSS-TENANT DATA LEAK, UNREVERSIBLE
MERGE WITHOUT EVIDENCE.

Required accuracy on critical cases is 100 percent. Section 28's own sentence:
a single false merge in a critical test means JBEQ-MDM NOT READY.

The scorer prints THREE separate lines, because until 2026-09-05 it printed one
and called every critical wrong a false merge, which reported nine false merges
in a run that contained none:

| Line | What it counts |
|---|---|
| `critical false merges: k of n` | section 28's own shape: a critical case answered AUTO-MERGE or SUGGEST MERGE where the expected answer is not a merge |
| `critical wrong: k of n` | every critical case answered wrongly, merge or not |
| `conservative wrongs: k` | the critical wrongs that chose a more cautious label than the expected one |

Each line names the cases it counted. The verdict follows the second line,
because section 28 requires 100 percent on critical cases: if `critical wrong`
is above zero the scorer prints `JBEQ-MDM NOT READY` and exits 1. Otherwise it
prints `JBEQ-MDM SEED: p of 70` and exits 0. A conservative wrong is still a
wrong and still blocks the verdict; it is named separately because erring
towards KEEP SEPARATE is a different defect from merging two customers, and
mixing them hides which one an answerer has.

Caution runs AUTO-MERGE, SUGGEST MERGE, LINK AS RELATED, KEEP SEPARATE, REJECT
MATCH, then ESCALATE and NO-DATA together, from the answer that joins the most
data to the answer that writes nothing.

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

JBEQ-MDM has no pass. The seed has been answered blind once, on 2026-09-05, and
the verdict was JBEQ-MDM NOT READY both before and after the vocabulary landed.
The end-to-end Japanese MDM task section 27 asks for beside the 70 cases is not
here. Neither is any track other than MDM.

The one run that exists was answered against the OLD prompts, the ones with no
vocabulary block, so its rescore against the corrected seed is a DIAGNOSTIC and
not a JBEQ-MDM score. A real score needs a fresh blind run against the prompts
as they stand now. Rescored on 2026-09-05 the run reads:

```
critical false merges: 0 of 33
critical wrong: 3 of 33 (EO-06, MM-06, HI-08)
conservative wrongs: 2 (MM-06, HI-08)
JBEQ-MDM NOT READY
```

Zero false merges is the one thing the run does say cleanly, and it says it
only because the count was separated from the other wrongs. The three that
remain are the ones where the seed was right and the answerer was not: EO-06
read a delivery hub the input never links to the store as a related record,
MM-06 rejected a match the input only fails to support, and HI-08 kept two
records apart that the input places under one legal entity.

## Reproduce

From the repository root:

```
python3 scripts/test_jbeq_mdm.py
# -> Ran 19 tests ... OK

python3 scripts/jbeq_mdm.py prompts /tmp/jbeq-prompts-check
# -> wrote 70 blind prompt file(s) to /tmp/jbeq-prompts-check
ls /tmp/jbeq-prompts-check | wc -l
# -> 70

grep -c 決定語彙 benchmarks/jbeq/mdm/prompts/AD-01.md
# -> 1
```

The test suite drives the controls backwards rather than merely asserting them:
a prompt file that carried the expected answer or the rationale fails the leak
test, a prompt file missing the vocabulary block fails, a committed prompt file
out of step with the seed fails, one critical case answered with a merge prints
JBEQ-MDM NOT READY and exits 1, a critical case answered wrong WITHOUT a merge
prints NOT READY too but counts zero false merges, and an answer file that
answers nothing exits 3 instead of scoring zero as a pass.

## CHANGELOG

The seed is frozen. It has been corrected once, and every correction is here.
Nothing else in the seed has changed since `frozen_at`.

### 2026-09-05, the decision vocabulary

Seven expected answers contradicted the definitions above and were corrected in
the same edit that wrote the definitions. Each rationale sentence was rewritten
so it agrees with the new answer. Six of the seven happen to agree with the
first blind run's answers and one does not, so the edit is stated here in full
rather than left to be inferred from a rising number; the run's verdict was NOT
READY before the edit and is NOT READY after it.

| Case | Old | New | Why |
|---|---|---|---|
| EO-02 | KEEP SEPARATE | LINK AS RELATED | The input states both a role pair (sold-to and payer of one trading relationship) and a parent company. KEEP SEPARATE requires no relation to record, and there are two. Matches EO-05, which already links a customer role to a supplier role for one legal entity. |
| MM-10 | KEEP SEPARATE | LINK AS RELATED | The input states 同一法人, a shared identity fact, which is exactly what LINK AS RELATED is for. Matches EO-01 and HI-08, where one corporate number over records with their own credit and terms links rather than merges. |
| HI-05 | KEEP SEPARATE | REJECT MATCH | The request is a legal-hierarchy parent, and the input refutes it outright: no capital relation and no shared officers. That is a refuted proposal, not an unsupported one. Matches HI-02 and HI-04, the same request shape, both already REJECT MATCH. |
| HI-06 | KEEP SEPARATE | LINK AS RELATED | The input states the intermediate holding company was newly established UNDER the existing holding company, which is a hierarchy to record. The old answer denied a relation the input asserts. |
| HI-10 | KEEP SEPARATE | REJECT MATCH | The request sets a distribution route as the legal parent, and the input refutes it. Matches HI-02 and HI-04. The commercial-flow relation belongs in the commercial hierarchy, which is a different node and not a reason to keep the two records unrelated in silence. |
| AD-04 | KEEP SEPARATE | REJECT MATCH | The input carries an explicit contradiction of identity: floors 3 and 8 hold different tenants. Matches AD-03 (same banchi, different municipality) and AD-10 (same floor, different corporate numbers), which this file already names as one family with AD-04. |
| ID-05 | KEEP SEPARATE | REJECT MATCH | The customer code was reused by another legal entity, which refutes the match the name-matching engine proposed. Matches ID-01 and ID-04, the same identifier-coincidence shape, both already REJECT MATCH. |

Left alone deliberately, and why they are not the same defect:

* MM-06 stays KEEP SEPARATE. Neither record carries a corporate number, so the
  match is unsupported rather than refuted, which is boundary rule 2.
* EO-06 stays KEEP SEPARATE. The input never says the store is served by the
  joint delivery hub, and boundary rule 1 asks for a relation the input states.
* HI-01 and HI-03 stay KEEP SEPARATE. Both keep valid rows apart rather than
  collapsing them, and nothing new is recorded between them.
* AD-08 stays ESCALATE and ID-03 stays SUGGEST MERGE. Both were answered
  wrongly in the first blind run and both obey the definitions as they stand.

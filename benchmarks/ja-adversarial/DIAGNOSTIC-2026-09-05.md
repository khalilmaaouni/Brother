# The two remaining blind-corpus misses: required diagnostic, 2026-09-05

Section 5 of the morning steering directive requires this record for every failing case BEFORE any
production code changes. Measured on hub main `a4f55007e`, against the FROZEN corpus
`benchmarks/ja-adversarial/adversarial-ja-corpus.json` (byte identical, not edited by this work), with
`python3 products/brothermode/tools/bm_vault_jbench.py run --cases benchmarks/ja-adversarial/adversarial-ja-corpus.json --verbose`,
which reports:

```
  dictionary_dependent   11/11 (100%), floor 90%  OK
  kana_alias             14/15 (93%), floor 70%  OK
  lexical_only           14/14 (100%), floor 90%  OK
  mixed                  13/13 (100%), floor 70%  OK
  negative               13/13 (100%), floor 90%  OK
  width_variant          11/12 (92%), floor 70%  OK
overall: 76/78 (97%)
  [MISS] ka06 'Tanaka Denki Corporation の所在地はどこですか' -> kana_alias
  [MISS] wv05 'ECOMART Co., Ltd. の代表は誰ですか' -> width_variant
```

The per-case token evidence below comes from `benchmarks/ja-adversarial/diag_positives.py`, added by this
commit: a read-only mirror of the shipped `diag_negatives.py` for the positive classes, because the shipped
helper covers the negative class only. It builds jbench's own in-memory fixture vault, runs jbench's own
`_search`, and changes no product code.

Both misses share one root: the query's IDENTITY token (the company's own name) reaches the right note
through no path at all, so the case is decided entirely by GENERIC ATTRIBUTE bigrams that every note in the
corpus shares. This is section 7's `CURRENT ATTRIBUTE SUPPORT > GENERIC TOKEN OVERLAP` rule failing from
below: not because generic overlap is weighted too highly, but because the identity signal contributes
literally zero and there is nothing for the rule to prefer.

---

## Case ka06

| Field | Value |
| --- | --- |
| CASE ID | `ka06`, class `kana_alias` |
| QUERY | `Tanaka Denki Corporation の所在地はどこですか` |
| EXPECTED ENTITY | `tanaka_denki`, note title `タナカ電機株式会社` |
| WRONG ENTITY | `sakurada_bussan_hr`, note title `桜田物産株式会社 人事部` |
| EXPECTED RANK | 1 |
| ACTUAL RANK | absent: not in the top 10, and still absent at limit 60. The whole result set for this query is one note. |
| POSITIVE SIGNALS FOR EXPECTED | none. Of the 19 analyzed query tokens, the expected note contains ZERO. |
| POSITIVE SIGNALS FOR WRONG | three generic location bigrams: `所在`, `在地`, `地は`, from the note's own sentence `所在地は本社と同じ東京都中央区日本橋一丁目3番2号`. |
| NEGATIVE SIGNALS FOR EXPECTED | none recorded: nothing penalised it, nothing reached it. |
| NEGATIVE SIGNALS FOR WRONG | none applied. The note is a DEPARTMENT of a different company (a personnel department), which no rule notices. |
| LEGAL NAME | expected `タナカ電機株式会社`; wrong `桜田物産株式会社` (the note is its 人事部, a sub-unit, not a company). |
| ALIAS | the query's `Tanaka Denki Corporation` is a romanised rendering of the expected legal name. The corpus dictionary declares no alias for it, and the note declares no English designation of its own. |
| READING | `タナカ` reads `tanaka`; `電機` reads `denki`. The query supplies exactly that reading in Latin script. |
| ENTITY TYPE | expected: a company (`株式会社`). Wrong: a department inside another company. A type conflict exists in the data and is not read. |
| GEOGRAPHY | expected 愛知県名古屋市; wrong 東京都中央区日本橋. The query asks for the location and supplies none, so geography cannot separate them, but neither does it support the winner. |
| RELATIONSHIP | none between the two. The winner is not a parent, subsidiary or confusable of the expected entity; it is an unrelated note that happens to spell the word `所在地`. |
| IDENTITY TOKENS | `tanaka`, `denki`, `corporation`. All three are produced by the analyzer and all three match nothing anywhere in the corpus. |
| ATTRIBUTE SUPPORT | the query's only attribute is `所在地` (location), which the expected note expresses as `本社を置く` and never as `所在地`. The wrong note spells `所在地` literally. |
| CONTRADICTION SUPPORT | none available and none used. |
| NORMALIZATION EFFECT | `normalize()` runs over the query and leaves it unchanged (it is already half-width). `analyze()` emits the Latin tokens as lower-case ASCII and both kana directions for every CJK bigram. It emits NO reading form: a Latin token is never turned back into the kana it romanises, so `tanaka` can never reach `タナカ`. |

**WHY DID THE WRONG ENTITY WIN?** Because the expected note was never a candidate at all: the query names
the company only in romaji, the analyzer produces no reading form for a Latin token, so the three identity
tokens matched nothing, and the only note left standing was the one that literally spells the generic word
`所在地`.

**WHAT SIGNAL SHOULD HAVE CAUSED IT TO LOSE?** The reading: `tanaka` is the Hepburn romanisation of the
expected note's own `タナカ`, and an identity token that resolves to a name in the corpus must outrank a
generic location bigram that any note in any corpus can carry.

---

## Case wv05

| Field | Value |
| --- | --- |
| CASE ID | `wv05`, class `width_variant` |
| QUERY | `ECOMART Co., Ltd. の代表は誰ですか` |
| EXPECTED ENTITY | `eco_mart`, note title `エコマート株式会社` |
| WRONG ENTITY | `sun_rise_kikaku`, note title `サンライズ企画株式会社` (also ahead: `fujimi_tech`, `fujimi_tech_solutions`) |
| EXPECTED RANK | 1 |
| ACTUAL RANK | 11 of 24 results, one place outside the top 10 the benchmark counts. |
| POSITIVE SIGNALS FOR EXPECTED | two generic bigrams only: `代表`, `表は`. Its own name token `ecomart` matched nothing. |
| POSITIVE SIGNALS FOR WRONG | four: the same two generic bigrams `代表`, `表は`, PLUS `co` and `ltd`, which are in the query because the query writes `Co., Ltd.`, and are in this note because it writes its own English designation `SUNRISE PLANNING CO., LTD.` in HALF-WIDTH ASCII. |
| NEGATIVE SIGNALS FOR EXPECTED | none applied. |
| NEGATIVE SIGNALS FOR WRONG | none applied. `co` and `ltd` are a LEGAL FORM, carrying no identity, and nothing marks them as such on the Latin side (`_LEGAL_FORMS` and `strip_legal_forms` cover the Japanese spellings `株式会社` and friends only). |
| LEGAL NAME | expected `エコマート株式会社`, whose note states its English designation verbatim: `英語表記はＥＣＯＭＡＲＴ　ＣＯ．，ＬＴＤ．`, in FULL-WIDTH. Wrong: `サンライズ企画株式会社`, English designation `SUNRISE PLANNING CO., LTD.` in half-width. |
| ALIAS | the note's own declared English designation is an AUTHORITATIVE ALIAS, written by the note itself, and it is exactly what the query names. |
| READING | not the mechanism here: both sides are Latin script and identical letter for letter. |
| ENTITY TYPE | both are companies; type does not separate them. |
| GEOGRAPHY | expected 千葉県船橋市, wrong 東京都新宿区. The query names no geography. |
| RELATIONSHIP | none. The winner is an unrelated advertising agency. |
| IDENTITY TOKENS | `ecomart` (identity bearing), `co`, `ltd` (legal form, identity free). Only the two identity-free ones matched anything. |
| ATTRIBUTE SUPPORT | the query asks for `代表`; the expected note carries `代表は清水直樹`, and so does every other company note in the corpus. Attribute support is real but entirely undiscriminating. |
| CONTRADICTION SUPPORT | none available and none used. |
| NORMALIZATION EFFECT | this is the whole defect. `normalize()` folds full-width to half-width on the QUERY, and `bm_vault._cjk_hits` then matches those tokens against the note's RAW, unnormalised text with `LIKE '%token%'`. The fold is therefore applied on ONE SIDE ONLY. `wv04` passes (`ＡＢＣ商会` asked of a note holding `ABC商会`) precisely because the fold runs in that direction; `wv05` fails in the mirror direction, where the query is half-width and the note is full-width. `ecomart` and `ＥＣＯＭＡＲＴ` are distinct codepoint sequences and SQLite's `LIKE` case-folds ASCII only, so nothing brings them together. |

**WHY DID THE WRONG ENTITY WIN?** Because width folding is applied to the query and never to the note, so
the right company's own name, written full-width in its note, matched nothing, while another company's
half-width legal-form abbreviation `CO., LTD.` matched two extra tokens and outscored it four to two.

**WHAT SIGNAL SHOULD HAVE CAUSED IT TO LOSE?** The note's own declared English designation
`ＥＣＯＭＡＲＴ　ＣＯ．，ＬＴＤ．`, an authoritative alias that names the queried entity exactly, must be
reachable after the same normalisation the query already gets, and a bare legal form shared by every
company on earth must not be able to outweigh it.

---

## What this record commits to, and what it rules out

Both fixes must be general mechanisms, per section 4. Forbidden and not used: editing the frozen corpus,
lowering a threshold or a class floor, raising `--limit` above the shipped 10, hardcoding a fixture id, a
company name or a case id. The two mechanisms this diagnostic names, each stated as a section 7 rule:

1. **Normalisation must be applied to both sides of a comparison.** Today the query is folded and the note
   is not, which turns the correct entity's own authoritative alias into a non-match while an unrelated
   note's legal-form abbreviation scores. Rule: `AUTHORITATIVE ALIAS > SEMANTIC SIMILARITY`, which cannot
   hold while the alias is unreachable.
2. **A romanised name is a reading of the entity, and a reading must reach the entity.** Today a Latin token
   is never resolved to the kana it romanises, so `tanaka` cannot reach `タナカ`. Rule: an identity-bearing
   token must outrank `GENERIC TOKEN OVERLAP`, which it cannot do while it matches nothing. Section 7's
   caution that `SAME KANA READING does not imply same entity` still binds: a reading earns CANDIDACY, and
   the ranking rules above it decide the winner. The reading expansion must never become an identity claim
   on its own.

Both are stated here before any production edit, and each gets its own unit test driven in both directions
plus a mutation control, per section 8.

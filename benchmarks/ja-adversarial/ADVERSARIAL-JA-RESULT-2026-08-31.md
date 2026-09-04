# Adversarial Japanese corpus: blind test result, 2026-08-31 (regenerated 2026-09-05, third pass after JA78)

Item 10 of the red-team directive ("Japanese 245/245 is good. Now try to break it."). An adversarial Japanese entity-retrieval corpus was authored BLIND (a separate author with no access to the system's ranking behavior, no sight of the existing benchmark, no implementation reads, running no retrieval tool), then scored once against the vault. The point of a blind corpus is that a benchmark authored alongside the implementation measures regression coverage, not generalization; this one measures generalization.

This document was regenerated on 2026-09-05 from a fresh run of the shipped harness against the FROZEN corpus (sha1 `f3920b31b83f`, unchanged since 2026-08-31). Two mechanism changes have landed since the original run, both measured here and neither one a corpus edit: VB2-08 (commit `7c1305fd3`, "disambiguate confusable Japanese entities in the fused ranker") closed most of the gap this document originally reported, JA13 closed the single negative case VB2-08 left, E121 taught the analyzer to read an old-name dictionary alias, and JA78 closed the last two positive misses. E96 exists because the numbers below had drifted out of sync with the harness they claim to summarize; the current numbers are what a stranger who runs the reproduce command actually sees today.

## The headline, as of 2026-09-05

- This blind adversarial corpus (78 cases, 32 notes): 78/78 (100%) on the shipped harness.
- Every class is at 100%, floors included, and the negative (disambiguation) class is clean: 13/13 (100%), floor 90%.
- No negative case fails: `diag_negatives.py` reports "negative cases where forbidden appeared: 0".
- There are no remaining misses. What a perfect score does NOT mean is written out under "What 78/78 does not prove" below, which is where the mutation run lives.

## Per-class, blind corpus (2026-09-05 run)

| class | score | floor | verdict |
|---|---|---|---|
| lexical_only | 14/14 (100%) | 90% | OK |
| mixed | 13/13 (100%) | 70% | OK |
| dictionary_dependent | 11/11 (100%) | 90% | OK |
| kana_alias | 15/15 (100%) | 70% | OK |
| width_variant | 12/12 (100%) | 70% | OK |
| negative | 13/13 (100%) | 90% | OK |
| overall | 78/78 (100%) | | |

Command run: `python3 products/brothermode/tools/bm_vault_jbench.py run --cases benchmarks/ja-adversarial/adversarial-ja-corpus.json`. Its decisive output:

```
per-class score table:
  dictionary_dependent   11/11 (100%), floor 90%  OK
  kana_alias             15/15 (100%), floor 70%  OK
  lexical_only           14/14 (100%), floor 90%  OK
  mixed                  13/13 (100%), floor 70%  OK
  negative               13/13 (100%), floor 90%  OK
  width_variant          12/12 (100%), floor 70%  OK
overall: 78/78 (100%)
```

## What closed the last negative case (JA13)

`ng13` was the one case VB2-08 did not move, and it stayed at rank #1 (top3 `['sakurada_bussan', 'fujimi_tech_solutions', 'abc_shokai']`) rather than merely inside the top 10. Its shape: the query `肥後物産株式会社は東京都中央区日本橋に本社を置く繊維商社ですか` NAMES a company the vault does not hold, and then describes it with an address and a trade that belong to a company the vault DOES hold, `桜田物産株式会社`. The trap is a false-attribute one, not a spelling one.

The mechanism let it in through VB2-08's own R2 rule. R2 elects a survivor from a confusable family (here every note sharing the rare token `物産`) by asking which member owns the query's distinguishing attributes, and then drops the members that do not. With `東京都`, `中央区`, `日本橋`, `繊維` and `商社` all in the query, `桜田物産` owned every one of them and its four family peers owned none, so R2 dropped `九州物産`, `金山物産`, `桜田物流` and `桜田物産 人事部` and left the decoy alone at the top. Election by attributes is sound only while the attributes describe a company the vault actually holds; here they described an absent one, so the rule was resolving a family that had no legitimate winner in it.

The fix inverts that one arm in exactly that state, and changes nothing else. `_ja_query_named` reads the company the asker wrote with a legal form attached (the run before `株式会社`, `有限会社` or `合同会社`), which is a name the asker ASSERTED rather than a word the ranker matched. When such a name is present and no candidate note carries it, R2 stops electing a survivor and drops the DOMINATOR instead: the note that owns the description is the one being mistaken for the company nobody holds. `ng13`'s `桜田物産` is now dropped with the reason `described but not named`, and `diag_negatives.py` reports zero negatives where the forbidden note appeared.

The gating is what keeps it from being a corpus edit. The inversion needs a legal-form-anchored name AND no candidate carrying it; name a company the vault holds and R2 keeps its original direction, and a query with no legal form is outside the rule entirely. Both directions are pinned in `products/brothermode/tools/test_bm_vault_disambig.py` on INVENTED company names, never the frozen corpus, alongside a mutation control that disables `_ja_query_named` and shows the decoy served again.

Honest residual at the time JA13 landed, now closed by E121: for `dd11`'s query the vault served `九州物産` only at rank 4, because the corpus supplies the link as the dictionary entry `肥後物産=九州物産(1960年当時の旧社名)` and the shipped analyzer read a dictionary term as a segmentation boundary, not as an alias, so no `A=B` old-name mapping resolved at all. E121 reads the notation instead: an entry written `A=B(reason)` whose parenthesised reason states an identity (a rename or a short form) makes A and B two names for one thing, both registered as segmentation terms and each reaching the other's notes as a match token. `dd11` now serves `九州物産` at rank 1 (top3 `['kyushu_bussan', 'sakurada_bussan', 'sakurada_butsuryu']`), measured against the identical frozen corpus; with alias reading disabled in a mutation control, the same query falls back to rank 4 with `['sakurada_bussan', 'sakurada_butsuryu', 'kaneyama_bussan']` on top, which is the state this document previously described.

Two things E121 deliberately does NOT do. An entry whose reason states a relationship rather than an identity (the corpus's own `金山物産=金山商事の関連会社(創業家が同じ)`) is declined and stays exactly as inert as before, because serving another company's note is a worse failure than serving nothing; `dd05`, `lx11` and `ng11` are unchanged by measurement. And no rule anywhere names a case, a company or a corpus: the notation and its reason vocabulary are read generically, and the permanent tests in `products/brothermode/tools/test_bm_vault_analyzer.py` are written on invented company names with a mutation control that puts the decoy back on top when alias reading is switched off. One honest ranking cost, reported rather than tuned away: `dd04`, whose query writes out a formal association name the note itself never spells, moves from rank 1 to rank 2 (still a hit, and the class stays 11/11) because the name now segments whole instead of spraying the bigrams it used to share with that note.

The other twelve negatives (`ng01` through `ng12`, including the paired case `ng03`/`ng04` on the same forbidden note under a different query and the two cases sharing forbidden note `tozai_shinkin` and `sakurada_bussan` elsewhere in the set) report `PASS(absent)`: the forbidden note does not appear anywhere in the top 10. VB2-08 closed eleven of the twelve disambiguation failures this document originally reported; JA13 closed the twelfth.

## What closed the last two positive cases (JA78)

`ka06` and `wv05` were the two misses this document reported after JA13 and E121. Both were absent from the top 10 entirely, not merely ranked low, and the full diagnostic the steering directive requires is committed beside this file as `DIAGNOSTIC-2026-09-05.md`, written and committed BEFORE any production edit. The short version, which is the same sentence twice: the query's identity token reached the right note through no path at all, so the case was decided by generic attribute bigrams every note in the corpus carries.

`wv05` asked `ECOMART Co., Ltd. の代表は誰ですか`. The right note declares its own English designation full-width (`英語表記はＥＣＯＭＡＲＴ　ＣＯ．，ＬＴＤ．`), and `bm_vault._cjk_hits` matched the query's already-folded tokens against RAW note text, so the width fold was applied on ONE SIDE ONLY. `wv04` passes because it asks a full-width query of a half-width note, which is the direction that happened to work; `wv05` is the mirror, and in it `ecomart` matched nothing while an unrelated advertising agency that writes `SUNRISE PLANNING CO., LTD.` half-width matched the query's `co` and `ltd` and outscored the right company four tokens to two. The fix folds the NOTE side with the same `analyzer.normalize` the query already gets, one normal form on both sides, in a single pass over the notes rather than the one full table scan per token it used to do.

`ka06` asked `Tanaka Denki Corporation の所在地はどこですか`. The right note is titled `タナカ電機株式会社` and nothing in the analyzer ever turned a Latin token back into the kana it romanises, so all three identity tokens (`tanaka`, `denki`, `corporation`) matched nothing anywhere in the corpus, and the single note left standing was an unrelated company's personnel department, which spells the generic word `所在地`. The fix reads a Latin token as its Hepburn kana (`romaji_to_kana`), whole word or nothing, and appends it to the token list so it picks up both kana directions like any segmented kana token. Three of the four romaji cases in this corpus (`ka03`, `ka09`, `ka12`) were passing WITHOUT this mechanism, on shared Japanese attribute words alone; `ka06` is the one whose query shares no attribute word with its note, which is why it was the case that exposed the gap.

Neither fix names a case, a company or a corpus. Both are pinned in `products/brothermode/tools/test_bm_vault_analyzer.py` on INVENTED company names, each with a mutation control that puts the decoy back on top, and both were driven red first: run those tests against the pre-JA78 modules and they report `FAILED (failures=3, errors=11)` at exit 1.

## What 78/78 does not prove

A perfect frozen benchmark is not evidence that the mechanisms underneath it work; it is evidence that nothing the corpus asks about is broken. `scripts/test_ja_mutations.py` disables each named ranking mechanism in turn and re-scores both corpora, and its verdicts are:

```
PROVEN: contradiction penalty
        disabling it makes blind miss ng07, ng08, ng09, ng10, ng11, ng13
PROVEN: relationship-role conflict
        disabling it makes blind miss dd05
PROVEN: exact-name authority
        disabling it makes blind miss ng13
PROVEN: identity-bearing-token weighting
        disabling it makes blind miss ng08
PROVEN: both-sides normalization (JA78)
        disabling it makes blind miss wv05, wv07, wv12
        disabling it makes standard miss W202 ... W225
PROVEN: romaji reading (JA78)
        disabling it makes blind miss ka06
mechanisms PROVEN by the benchmark: 6
mechanisms reported NO-DATA:        2
```

The two NO-DATA verdicts are the honest part, and they are recorded rather than rounded up:

- **entity-type conflict: NO-DATA, because the mechanism does not exist.** Nothing in `bm_vault._search` or `_ja_disambiguate` carries an entity-type vocabulary. The nearest thing is `info[...]["entity"]`, a name-length heuristic for IS this an entity, never a TYPE that could conflict with another, and `bm_vault_entity.py`'s `ENTITY_TYPES` is a corpus check tool retrieval never consults. Section 7's rule that an entity-type conflict must reduce confidence is therefore UNIMPLEMENTED, not merely unproven. `ka06`'s own decoy was a personnel department competing as if it were a company, which is exactly the case that rule would have decided.
- **alias authority: NO-DATA, because the benchmark does not score it.** Emptying both dictionary files, which is the widest alias knob there is (E121's `A=B` reader reaches retrieval only through those files), moves no case from hit to miss in either corpus. The mutation is not inert: the tool checks, and reports, that the analyzer's own token output DID change under it. So E121's alias reader is real and measurable at rank level (`dd11` moves 4 to 1), and this corpus simply never asks a question whose PASS depends on it.

Both belong on a future corpus, not on this one: this one is frozen, and adding cases to it to make a mechanism look proven would be the exact tuning this document refuses everywhere else.

## 2026-08-31 history (superseded by the numbers above)

The original run, before VB2-08 landed, scored this same frozen corpus at 64/78 (82%), with the entire gap in the negative class: 1/13 (8%), below its 90% floor. The rank diagnostic at the time found 12 of 13 negatives failing, the forbidden note ranked #1 in 6 cases and inside the top 3 in 10 cases (ranks observed: [1, 1, 1, 1, 1, 1, 2, 2, 2, 2, 4, 6]). What the traps were, and what won at the time: bank vs shinkin (信用金庫) confusion, parent vs subsidiary, homophone-kanji companies (same reading, different kanji, e.g. 精工 pairs), near-identical names differing by one token, attribute-swap and contradictory-statement traps, and OCR-like character substitutions. That finding is what VB2-08 (PR 180, commit `7c1305fd3`) was named to fix; this document's current numbers are the result of that fix, re-measured against the unchanged corpus.

One caveat carried forward from the original run: the blind author placed dictionary terms under a "dictionary" key; the harness reads `_meta.dictionary_terms`. Both runs score WITH the terms correctly installed under `_meta.dictionary_terms`, so the `dictionary_dependent` number is honest in both.

## Honest scope and what happens next

- The corpus is FROZEN and committed here as permanent regression evidence. It was not tuned to the implementation (the author never saw one), and it must not be tuned afterward. Re-running this exact corpus is how a future disambiguation change gets measured; a negative-class number that only rises after the corpus is edited is not progress.
- `ng13` is closed by JA13, at the mechanism and not at the corpus: the fix is one gated inversion in `_ja_disambiguate`, the corpus file is byte-identical, and the standard 245-case Japanese benchmark stays at 245/245 (100%).
- The old-name alias gap this document reported as open is closed by E121, at the mechanism and not at the corpus: the analyzer now reads an `旧社名=現社名` dictionary entry as an alias, the corpus file is byte-identical (sha1 `f3920b31b83f`), the blind corpus was unchanged at 76/78 with negative 13/13 by that fix alone, and the standard 245-case Japanese benchmark stays at 245/245 (100%). What remains open is narrower: the reason vocabulary that makes `=` an identity is a fixed list, so a rename glossed with a word outside it yields a miss (the query is answered lexically, as it is today) and never a wrong note.

- The last two positive misses are closed by JA78, at the mechanism and not at the corpus: the note side is now folded with the same normalizer the query side already had, and a Latin token is read as the kana it romanises. The corpus file is byte-identical (sha1 `f3920b31b83f`), and the standard 245-case Japanese benchmark stays at 245/245 (100%). What is open after it is written out under "What 78/78 does not prove" above and is not a score: an entity-type conflict rule that the ranking path does not implement at all, and an alias mechanism this corpus never scores. Neither is closed by adding cases to a frozen corpus.
- The reading mechanism reads a READING, never an identity. Section 7 is explicit that the same kana reading does not imply the same entity, so the kana a Latin token produces earns a note CANDIDACY and nothing more; a test in `test_bm_vault_analyzer.py` pins that two different companies reading the same way are both recalled rather than one being chosen. Long vowels are not reconstructed and kanji readings are not resolved, so an unread name is a MISS and never a wrong note.

## Reproduce

Both tools ship in this repository; no external checkout is needed.

```
python3 products/brothermode/tools/bm_vault_jbench.py run --cases benchmarks/ja-adversarial/adversarial-ja-corpus.json
# -> overall 78/78 (100%), negative 13/13 (100%)
python3 benchmarks/ja-adversarial/diag_negatives.py   # forbidden-note ranks per failing negative
```

The scoring uses the product's own harness as a black box; nothing here imports vault internals except the diagnostic, which reuses `bm_vault_jbench.py`'s own fixture builder to report ranks. `diag_negatives.py` resolves the harness from the in-tree sibling `products/brothermode/tools` by default; set `BROTHERMODEUP_TOOLS` to point it elsewhere.

# Adversarial Japanese corpus: blind test result, 2026-08-31 (regenerated 2026-09-04)

Item 10 of the red-team directive ("Japanese 245/245 is good. Now try to break it."). An adversarial Japanese entity-retrieval corpus was authored BLIND (a separate author with no access to the system's ranking behavior, no sight of the existing benchmark, no implementation reads, running no retrieval tool), then scored once against the vault. The point of a blind corpus is that a benchmark authored alongside the implementation measures regression coverage, not generalization; this one measures generalization.

This document was regenerated on 2026-09-04 from a fresh run of the shipped harness against the FROZEN corpus (sha1 `f3920b31b83f`, unchanged since 2026-08-31). Between the original run and this one, VB2-08 (commit `7c1305fd3`, "disambiguate confusable Japanese entities in the fused ranker") landed and closed most of the gap this document originally reported. E96 exists because the numbers below had drifted out of sync with the harness they claim to summarize; the current numbers are what a stranger who runs the reproduce command actually sees today.

## The headline, as of 2026-09-04

- This blind adversarial corpus (78 cases, 32 notes): 75/78 (96%) on the shipped harness.
- Every class clears its floor, negative included: 12/13 (92%), floor 90%.
- One case remains unsolved: `ng13`, forbidden note `sakurada_bussan`, ranked #1 (the top hit).

## Per-class, blind corpus (2026-09-04 run)

| class | score | floor | verdict |
|---|---|---|---|
| lexical_only | 14/14 (100%) | 90% | OK |
| mixed | 13/13 (100%) | 70% | OK |
| dictionary_dependent | 11/11 (100%) | 90% | OK |
| kana_alias | 14/15 (93%) | 70% | OK |
| width_variant | 11/12 (92%) | 70% | OK |
| negative | 12/13 (92%) | 90% | OK |
| overall | 75/78 (96%) | | |

Command run: `python3 products/brothermode/tools/bm_vault_jbench.py run --cases benchmarks/ja-adversarial/adversarial-ja-corpus.json`. Its decisive output:

```
per-class score table:
  dictionary_dependent   11/11 (100%), floor 90%  OK
  kana_alias             14/15 (93%), floor 70%  OK
  lexical_only           14/14 (100%), floor 90%  OK
  mixed                  13/13 (100%), floor 70%  OK
  negative               12/13 (92%), floor 90%  OK
  width_variant          11/12 (92%), floor 70%  OK
overall: 75/78 (96%)
```

## The one remaining negative case

`diag_negatives.py` still finds one failing negative out of thirteen: `ng13`, forbidden note `sakurada_bussan`, ranked #1 (the single best hit, top3 `['sakurada_bussan', 'fujimi_tech_solutions', 'abc_shokai']`). The other twelve negatives (`ng01` through `ng12`, including the paired case `ng03`/`ng04` on the same forbidden note under a different query and the two cases sharing forbidden note `tozai_shinkin` and `sakurada_bussan` elsewhere in the set) now report `PASS(absent)`: the forbidden note does not appear anywhere in the top 10. VB2-08 closed eleven of the twelve disambiguation failures this document originally reported; `ng13` is the one that did not move.

## 2026-08-31 history (superseded by the numbers above)

The original run, before VB2-08 landed, scored this same frozen corpus at 64/78 (82%), with the entire gap in the negative class: 1/13 (8%), below its 90% floor. The rank diagnostic at the time found 12 of 13 negatives failing, the forbidden note ranked #1 in 6 cases and inside the top 3 in 10 cases (ranks observed: [1, 1, 1, 1, 1, 1, 2, 2, 2, 2, 4, 6]). What the traps were, and what won at the time: bank vs shinkin (信用金庫) confusion, parent vs subsidiary, homophone-kanji companies (same reading, different kanji, e.g. 精工 pairs), near-identical names differing by one token, attribute-swap and contradictory-statement traps, and OCR-like character substitutions. That finding is what VB2-08 (PR 180, commit `7c1305fd3`) was named to fix; this document's current numbers are the result of that fix, re-measured against the unchanged corpus.

One caveat carried forward from the original run: the blind author placed dictionary terms under a "dictionary" key; the harness reads `_meta.dictionary_terms`. Both runs score WITH the terms correctly installed under `_meta.dictionary_terms`, so the `dictionary_dependent` number is honest in both.

## Honest scope and what happens next

- The corpus is FROZEN and committed here as permanent regression evidence. It was not tuned to the implementation (the author never saw one), and it must not be tuned afterward. Re-running this exact corpus is how a future disambiguation change gets measured; a negative-class number that only rises after the corpus is edited is not progress.
- `ng13` remains open: one confusable entity (`sakurada_bussan`) still ranks above the note its query actually asked for. This is a BrotherModeUp retrieval-quality finding, not a Brother-side gate defect.

## Reproduce

Both tools ship in this repository; no external checkout is needed.

```
python3 products/brothermode/tools/bm_vault_jbench.py run --cases benchmarks/ja-adversarial/adversarial-ja-corpus.json
# -> overall 75/78 (96%), negative 12/13 (92%)
python3 benchmarks/ja-adversarial/diag_negatives.py   # forbidden-note ranks per failing negative
```

The scoring uses the product's own harness as a black box; nothing here imports vault internals except the diagnostic, which reuses `bm_vault_jbench.py`'s own fixture builder to report ranks. `diag_negatives.py` resolves the harness from the in-tree sibling `products/brothermode/tools` by default; set `BROTHERMODEUP_TOOLS` to point it elsewhere.

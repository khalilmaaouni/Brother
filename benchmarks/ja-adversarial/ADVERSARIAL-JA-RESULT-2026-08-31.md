# Adversarial Japanese corpus: blind test result, 2026-08-31

Item 10 of the red-team directive ("Japanese 245/245 is good. Now try to break it."). An adversarial Japanese entity-retrieval corpus was authored BLIND (a separate author with no access to the system's ranking behavior, no sight of the existing benchmark, no implementation reads, running no retrieval tool), then scored once against the real vault. The point of a blind corpus is that a benchmark authored alongside the implementation measures regression coverage, not generalization; this one measures generalization.

## The headline

- The team's own 245-case benchmark: 245/245 (100%) on the real vault (BrotherModeUp origin/main 2f9c1de).
- This blind adversarial corpus (78 cases, 32 notes): 64/78 (82%) on the same vault.
- The entire gap is in ONE class: disambiguation (the negative class), 1/13 (8%). Every other class clears its floor.

## Per-class, blind corpus

| class | score | floor | verdict |
|---|---|---|---|
| lexical_only | 14/14 (100%) | 90% | OK |
| mixed | 13/13 (100%) | 70% | OK |
| dictionary_dependent | 11/11 (100%) | 90% | OK |
| kana_alias | 14/15 (93%) | 70% | OK |
| width_variant | 11/12 (92%) | 70% | OK |
| negative | 1/13 (8%) | 90% | BELOW FLOOR |
| overall | 64/78 (82%) | | |

Read plainly: retrieval generalizes well on orthographic variance it was NOT tuned against (kana across hiragana/katakana/kanji/romaji, full and half width, mixed Japanese-English, 株式会社 placement, spacing). It does not need the exact cases it was trained beside to handle those. The weakness is disambiguation.

## The negative-class finding is real, not a small-corpus artifact

A negative case passes only if a plausible-but-wrong near-match (the forbidden note) stays OUT of the top 10. With 32 notes, a naive worry is that top-10 just catches the forbidden note by volume. The rank diagnostic (diag_negatives.py) disproves that: of the 12 failing negatives, the forbidden note was ranked

- number 1 (the single best hit) in 6 cases,
- inside the top 3 in 10 cases.

Ranks observed: [1, 1, 1, 1, 1, 1, 2, 2, 2, 2, 4, 6]. The confusable entity is being returned as the BEST match, not merely present. That is a genuine false-positive weakness.

What the traps were, and what won: bank vs shinkin (信用金庫) confusion, parent vs subsidiary, homophone-kanji companies (same reading, different kanji, e.g. 精工 pairs), near-identical names differing by one token, attribute-swap and contradictory-statement traps, and OCR-like character substitutions. The retriever fused-ranked the wrong member of each pair to the top.

## Honest scope and what happens next

- This is a BrotherModeUp retrieval-quality finding, not a Brother-side gate defect. Per the productization freeze (no new subsystems), the fix (entity disambiguation in the fused ranker) is NAMED for the ModeUp lane, not built cosmetically tonight.
- The corpus is FROZEN and committed here as permanent regression evidence. It was not tuned to the implementation (the author never saw one), and it must not be tuned afterward. When disambiguation improves, re-running this exact corpus measures the gain; a negative-class number that only rises after the corpus is edited is not progress.
- One caveat recorded: the blind author placed dictionary terms under a "dictionary" key; the harness reads _meta.dictionary_terms. The score above is WITH the terms correctly installed under _meta.dictionary_terms (dictionary_dependent 11/11 both ways), so the dictionary_dependent number is honest.

## Reproduce

Needs a BrotherModeUp checkout on origin/main (the full vault). From this repository:

```
python3 <bmu>/tools/bm_vault_jbench.py run --cases benchmarks/ja-adversarial/adversarial-ja-corpus.json
# -> overall 64/78 (82%), negative 1/13 (8%)
python3 benchmarks/ja-adversarial/diag_negatives.py   # forbidden-note ranks per failing negative
```

The scoring uses the product's own CLI as a black box; nothing here imports vault internals except the diagnostic, which reuses jbench's own fixture builder to report ranks.

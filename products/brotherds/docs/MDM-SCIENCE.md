# Master data science in BrotherDS

## What this adds, in one minute

Master data decisions merge customer or product records into one golden record per real entity. A claim like "precision above 0.9 at threshold 0.8" or "we under merge less than 2 percent" drives downstream risk. BrotherDS checks such claims before the decision ships. It uses deterministic gates M7 to M22. Each gate looks only at evidence you declare in the claim file and answers PASS, FAIL, or NO-DATA. NO-DATA means the evidence needed to judge is missing, so the tool does not guess. Plain words first, formulas second. If a gate cannot compute a number it tells you which field is missing or malformed. This keeps review on gaps, not on model internals.

The same mechanism checks whether a match score can be used as a probability, whether a threshold matches declared error costs, and whether Japanese or Latin normalization was applied before keys were compared. Forecasts issued as quantiles are scored later with a proper score. Lessons from past failures are recalled at receipt time through the Vault.

## The commands

Every path and number you pass must be written down, never invented.

* `python3 products/brotherds/bds.py mdm-eval review <review.csv> --merge-threshold 0.8` evaluates a clerical review file and prints strata, precision above threshold, and recall. The CSV must have `score` and `label` with optional `stratum_population`. Scores must be in [0,1] and labels are `match` or `non-match`. The tool bands scores by tenths, 0.0 to 0.1, 0.1 to 0.2, up to 1.0, and builds stratified estimates recomputably.
* `python3 products/brotherds/bds.py mdm-eval clusters <gold.csv>` reads `record_id`, `predicted_cluster` and `true_cluster` and prints `gold_clusters` ready for M10 with its pairwise and B-cubed precision and recall.
* `python3 products/brotherds/bds.py mdm-eval drift <reference.csv> <current.csv> [--bins N]` reads a `score` column from each file and prints `score_drift` ready for M13 with its PSI and band.

* `python3 products/brotherds/bds.py check <claim.json>` runs gates M7 to M22 and related packs on one claim file and prints PASS, FAIL, NO-DATA lines plus a VERDICT.

* `python3 products/brotherds/bds.py receipt <claim.json> <out.md>` writes a markdown receipt for the claim, including Vault context when a Vault is set. If the claim failed gates, the receipt surfaces relevant lessons.

* `python3 products/brotherds/bds.py score <claim.json> <actual> "<who>" <date>` scores a resolved quantile forecast against an actual value, writes `wis`, `dispersion`, `underprediction`, `overprediction`, and `band_hit` back into the claim file, and reports HELD, MISSED, or UNSCOREABLE.

* `python3 products/brotherds/bds.py ledger <dir> [--propose-lessons]` reads all claims in a directory, reports coverage and recurring failures, and optionally proposes lesson candidates to the Vault inbox through the intake door, once per distinct pattern.

* `python3 products/brotherds/mdm_normalize.py key company ja "<text>"` prints a deterministic match key. Kind is `company`, `person`, or `phone`. Locale is `ja` or `latin`. Use this to test normalization before you declare it.

* `python3 products/brotherds/examples/mdm_demo.py` runs a small end to end demo of the same steps.

## The gates, M7 to M22

Each gate follows the contract: missing evidence is NO-DATA, malformed input is FAIL naming the field, no crash. Table columns: gate, what it asks, when it FAILs, when it is NO-DATA, the source it rests on.

| gate | what it asks | when it FAILs | when it is NO-DATA | source it rests on |
|---|---|---|---|---|
| M7.precision_evidence | Does claimed precision lie inside what stratified review above the merge threshold supports | claimed precision above Wilson interval upper bound for strata with score_lo at or above merge_threshold, or strata are malformed | no review_strata, no merge_threshold, no claimed precision, no stratum above threshold, or any band straddles the threshold | Cochran 1977 stratified sampling, Wilson 1927 interval via mdm_eval.stratified_estimate |
| M8.recall_evidence | Can recall be estimated from review covering both sides of threshold | review has no sample below threshold, or claimed recall exceeds review recall point estimate by more than 0.05, or when blocking is declared exceeds end to end estimate recall among candidates times pair completeness by more than 0.05, or strata malformed | no review_strata, no merge_threshold, no claimed recall, or band straddles threshold | Cochran 1977, Binette et al 2022/2023, Christen 2012 blocking completeness |
| M9.blocking_ceiling | Does claimed recall stay within what blocking allowed to be seen | claimed recall above blocking pair_completeness, or blocking numbers inconsistent such as n_candidate_pairs above total pairs | no blocking evidence, or pair_completeness returned as None | Christen 2012 Data Matching reduction ratio and pair completeness, mdm_eval.blocking_metrics |
| M10.cluster_metrics | Does claimed precision hold on gold cluster subset under declared basis | claimed precision above pairwise or B-cubed precision by more than 0.05, or clusters malformed or overlapping | no gold_clusters, no claimed precision, no predicted pairs for pairwise basis so precision undefined, or B-cubed undefined for declared basis | Bagga and Baldwin 1998 B-cubed, pairwise counting, Binette et al weighted aggregates |
| M11.overmerge | Did any cluster grow to giant size without review | giant_clusters count greater than 0 and giant_clusters_reviewed is not true, with transitive closure chaining note | no cluster_sizes | Christen 2012, incremental entity resolution notes, convention max of 50 and 0.01 share of records |
| M12.fs_parameters | Are Fellegi Sunter m and u valid and does agreement support a match | any m or u outside (0,1), or m less than or equal to u so agreement would count against a match | no fs_parameters or empty list | Fellegi and Sunter 1969, Splink theory notes on m, u, weight log2 m/u |
| M13.score_drift | Has match score distribution drifted since reference | psi_band is major and acknowledged is not true, or reference or current list malformed | no score_drift object | PSI industry bands, psi_band with 0.10 and 0.25 as conventions |
| M14.quality_dimensions | Do declared quality dimensions meet thresholds and does accuracy name a method | unknown dimension, value or threshold outside [0,1], value below threshold, or accuracy without non empty method | no quality or empty quality | ISO 8000 syntactic semantic pragmatic, ISO IEC 25012, DAMA, Great Expectations, Soda, Deequ, dbt |
| M15.golden_lineage | Is survivorship lineage declared per attribute with rule, source, and conflict rate | any entry missing rule or source_system, or conflict_rate outside [0,1] | no golden_record or empty list | Stibo, Oracle, Data Ladder survivorship families, Informatica Match and Merge |
| M16.metric_choice | Does headline metric match false merge cost class | headline_metric is accuracy, or f1 with catastrophic cost, or f_beta missing beta or beta less than or equal to 0, or catastrophic with beta greater than or equal to 1, or unknown metric | no headline_metric | Hand and Christen 2018 on F-measure weighting |
| M17.review_power | Does review above threshold hold enough pairs for required margin | sampled pairs above threshold below n_required equals ceil of z squared times p times 1 minus p over margin squared with z 1.96 and p the claimed precision (0.5 when it is exactly 0 or 1), or strata malformed | no merge_threshold, no required_margin, no claimed precision, no review_strata, or band straddles threshold | Cochran 1977 sample size for a proportion |
| M18.representative_gold | Is gold sample frame suitable and weighted when needed | frame convenience or benchmark with claimed precision present, or probability without weighted true, or unknown frame | no gold_sample | Binette et al 2022/2023 probability samples weighted to population |
| M19.labeller_agreement | Do model labels have enough calibrated overlap with human labels | labeller kind llm or model with overlap_n below 50, or missing agreement_kappa, or kappa outside minus1 to 1, or below 0.8, or unknown kind | no labeller | Cohen 1960 kappa, Landis and Koch 1977 bands |
| M20.score_calibration | Are scores declared as probabilities calibrated | fewer than 3 strata with finite mean_score in [0,1], or stratum with sampled less than 1 or positive outside 0 to sampled or score_lo or score_hi outside [0,1] or lo greater than hi, or predicted mean_score outside Wilson interval for observed rate, ECE reported | evaluation missing or scores_are_probabilities is not true | Guo et al 2017 calibration, Wilson 1927, mdm_eval.wilson_interval, ECE |
| M21.threshold_cost | Does merge threshold respect declared error costs for calibrated scores | costs or threshold non finite or outside allowed range, or threshold below cost implied t star minus 0.02 where t star equals false_merge over false_merge plus missed_match | evaluation missing or scores_are_probabilities is not true, or error_costs or merge_threshold absent | Standard decision theory Bayes rule, Hand and Christen 2018 |
| M22.normalization | Was normalization declared and sufficient for locale and do probes match under reference keys | unknown locale, steps not a list, step not a string, required steps missing, probes not object or not list or not pair of strings, or probe pair unequal under company_key, person_key, or phone_key | no normalization or normalization is null | mdm_normalize REQUIRED_STEPS, NFKC and locale steps |

Detail strings always name the field. For example M7 reports stratified_estimate rejected malformed strata with the ValueError text, M17 reports review_strata index and field such as score_lo, M20 reports mean_score must be a finite number and sampled must be an integer greater than or equal to 1.

## The evaluation block: field reference

All fields below live under `master_data`. Fields under `master_data.evaluation` carry evidence the gates read. Fields under `master_data.normalization` carry script handling. Type and reader:

* `master_data.merge_threshold` number in [0,1]. Read by M7, M8, M17, M21. Threshold that separates candidate merge from review.
* `master_data.false_merge_cost_class` string `catastrophic` or similar. Read by M16 with beta.
* `master_data.evaluation.claimed_precision` number in [0,1]. Also fallback `match.precision`. Read by M7, M10, M17, M18.
* `master_data.evaluation.claimed_recall` number in [0,1]. Also fallback `match.recall`. Read by M8, M9.
* `master_data.evaluation.review_strata` array of objects. Read by M7, M8, M17, M20. Each entry holds `population` int greater than or equal to 0, `sampled` int greater than or equal to 1, `positive` int 0 to sampled, `score_lo` number, `score_hi` number, optional `mean_score` number in [0,1] for M20.
* `master_data.evaluation.required_margin` number in (0, 0.5). Read by M17.
* `master_data.evaluation.headline_metric` string `accuracy`, `f1`, `f_beta`, `precision`, `recall`. Read by M16.
* `master_data.evaluation.beta` number greater than 0 when headline_metric is f_beta. Read by M16.
* `master_data.evaluation.gold_sample` object with `frame` string `convenience`, `benchmark`, `probability`, `census` and `weighted` bool. Read by M18.
* `master_data.evaluation.labeller` object with `kind` string `human`, `llm`, `model`, `overlap_n` int greater than or equal to 50 for model kinds, `agreement_kappa` number in minus1 to 1 at least 0.8. Read by M19.
* `master_data.evaluation.gold_clusters` object with `pred` array of arrays of record ids and `true` array of arrays. Optional `metric_basis` string `pairwise` or `bcubed`. Read by M10.
* `master_data.evaluation.cluster_sizes` array of positive ints. Read by M11. Also `giant_clusters_reviewed` bool.
* `master_data.evaluation.fs_parameters` array of objects with `field` string, `m` number in (0,1), `u` number in (0,1). Read by M12.
* `master_data.evaluation.blocking` object with `n_records`, `n_candidate_pairs`, `n_true_pairs`, `n_true_pairs_in_candidates` ints greater than or equal to 0. Read by M8 and M9 through mdm_eval.blocking_metrics.
* `master_data.evaluation.score_drift` object with `reference` array of numbers and `current` array of numbers and optional `acknowledged` bool. Read by M13.
* `master_data.evaluation.quality` object mapping known dimension names to object with `value` number in [0,1], `threshold` number in [0,1], `method` string. Known names are `completeness`, `validity`, `uniqueness`, `consistency`, `timeliness`, `accuracy`. Read by M14.
* `master_data.evaluation.golden_record` array of objects with `attribute` string, `rule` non empty string, `source_system` non empty string, `conflict_rate` number in [0,1]. Read by M15.
* `master_data.evaluation.scores_are_probabilities` bool. Read by M20 and M21.
* `master_data.evaluation.error_costs` object with `false_merge` positive finite number and `missed_match` positive finite number. Read by M21.
* `master_data.normalization.locale` string `ja` or `latin`. Read by M22.
* `master_data.normalization.steps` array of strings. Read by M22.
* `master_data.normalization.probes` object with optional keys `company`, `person`, `phone` each value is array of two string pairs. Read by M22.

If a gate can read a fallback path, the claim module tries the evaluation field first, then the generic `match` field where applicable, such as for claimed_precision.

## Japanese and other scripts: normalization before matching

`mdm_normalize` provides deterministic string to key transforms using only Python 3.9 standard library. Call `python3 products/brotherds/mdm_normalize.py key company ja "株式会社アイウ商事"` to see a key.

Steps as implemented:

1. `nfkc` applies Unicode NFKC. This maps half width to full width and compatibility forms to canonical forms. For example full width parentheses `（株）` become `(株)`.
2. `normalize_space` collapses any whitespace sequence to single spaces and trims ends.
3. `normalize_long_vowel` normalizes long vowel variants. If the previous character is katakana and current is a dash variant in the set including U+2010 to U+2015, U+2212, U+FF0D, hyphen, or U+FF70, it becomes katakana chouon U+30FC. This maps `コ-ヒ-` to `コーヒー` but leaves `03-1234` unchanged because `-` is not after katakana.
4. `normalize_itaiji` translates variant kanji via `_ITAIJI_MAP`. Examples: `髙` U+9AD9 to `高` U+9AD8, `﨑` U+FA11 to `崎` U+5D0E, `斎` and `齋` to `斉` U+658E, `辺` U+908A and `邊` U+9089 to `辺` U+8FBA, `澤` U+6FA4 to `沢` U+6CA2, `廣` U+5EE3 to `広` U+5E83, `國` U+570B to `国` U+56FD.
5. `normalize_small_ke` normalizes small ka variant `ヶ`, `ケ`, `ヵ` to `ケ` U+30B1 only when between two kanji, such as `霞ヶ関` to `霞ケ関` and `三ヶ月` to `三ケ月`.
6. `strip_corporate` removes corporate suffixes at start or end, repeatedly until stable. For `ja` it removes literal forms `株式会社`, `有限会社`, `合同会社`, `合資会社`, `合名会社`, `カブシキガイシャ`, `(株)`, `(有)`, `(同)`, full width parent forms, `㈱`, `㈲`, `㈹`, plus roman patterns `K.K.`, `KK`, `Co., Ltd.` variants, `Ltd.`, `Inc.`. For `latin` it removes forms like `GmbH`, `AG`, `SA`, `Ltd`, `Inc`, case insensitive, as separate words with optional period.
7. `fold_latin` applies NFKD, strips combining marks, then casefolds. This maps `Müller` to `muller`. It is the only step that discards diacritics.
8. Final keys: `company_key` builds from nfkc, space, optional Japanese steps, strip_corporate, fold_latin, then removes spaces and characters in ` .,・･()（）` for company. `person_key` uses nfkc, space, optional itaiji and long vowel, fold_latin, then removes spaces and `・･`. `phone_key` applies nfkc, maps leading `+81` to `0`, then keeps only digits.

`REQUIRED_STEPS` per locale:

* `ja` requires `["nfkc", "space", "long_vowel", "itaiji", "small_ke", "corporate"]`. Without them width, kana and variant kanji differences split one customer into several.
* `latin` requires `["nfkc", "space", "fold", "corporate"]`.

Gate M22 checks that every required name appears in `normalization.steps`, that steps are strings, that locale is known, and that any `probes` are equal under the reference implementation. Probe kinds are `company`, `person`, `phone`. Each probe is a pair of strings that must map to the same key. Probes do not prove coverage, they only warn when expected equality fails.

Documented limit of `fold_latin`: it is lossy by design. NFKD plus combining removal plus casefold makes `Müller` and `MULLER` equal but also maps many accented forms to the same ascii base. If your data must keep a distinction carried only by a diacritic, do not use a folded key alone to decide match. The gate does not detect this, it only checks you declared `fold` for `latin`. If you need accent sensitive blocking, declare it outside the key and document why `fold` remains required for the shared component.

## Forecast scoring: weighted interval score and band coverage

Quantile forecasts are stored as ordered quantiles in the claim. Median key `0.5` is required. Remaining quantiles must form symmetric pairs around the median such as `0.1` with `0.9` for alpha 0.2. Quantile values must be non decreasing with probability.

Functions from `forecast_score`:

* `pinball_loss(tau, q, y)` is `(1{y < q} - tau) * (q - y)` for `tau` in (0,1).
* `interval_score(lo, hi, y, alpha)` is `hi - lo` plus `2 over alpha` times distance outside the interval.
* `wis(quantiles, y)` is the weighted interval score per Bracher, Ray, Gneiting and Reich 2021 and Gneiting and Raftery 2007. For each interval pair at level `alpha = 2 * tau`, weight `w = alpha over 2`, median weight `0.5`, denominator `K + 0.5` where `K` is number of intervals. Dispersion is weighted widths over denominator, underprediction is weighted excess above hi, overprediction is weighted deficit below lo, plus median absolute error weighted by 0.5. Return `wis = dispersion + underprediction + overprediction`, plus `alphas`, `n_intervals`, `ignored` unpaired quantiles, `relative_wis = wis over abs(median)` or NO-DATA string when median is zero.
* `band_hit(quantiles, y, lo_key="0.1", hi_key="0.9")` returns True when y inside the band, False when outside, None when keys absent.
* `coverage(hits, nominal=0.8, z=1.96)` computes Wilson score interval for hit rate with nominal 0.8 for the 0.1 to 0.9 band. Returns `n`, `hits`, `coverage`, `lo`, `hi`, `verdict`. Verdict is NO-DATA when `n < 5`, otherwise `over-confident` if hi below nominal, `under-confident` if lo above nominal, else `calibrated`.

Why WIS: a plain hit count only records inside or outside. WIS rewards sharp and calibrated bands and decomposes width versus misses.

Example with `{"0.1": 80, "0.5": 100, "0.9": 130}`, `y=100` gives wis 3.333, dispersion 3.333, under 0, over 0. With `y=200` wis 83.333, dispersion 3.333, under 80, over 0. The `ledger` command aggregates band_hit across claims and reports `0.1-0.9 band coverage:` with the Wilson verdict.

## The Vault: what it recalls and what it learns

The Vault is shared memory for the team. It is a directory of markdown lessons. Tools are under `products/brothermode/tools` with `bm_vault.py` providing `index`, `recall`, and `intake` operations. Recall is bounded by `BROTHERDS_RECALL_TIMEOUT_S` which defaults to short and can be set such as `60`.

What it recalls: `receipt` calls `bm_vault.py recall` for the gates a claim failed. It indexes the Vault with `bm_vault.py index --vault $BROTHERDS_VAULT`, searches lessons by gate id, and appends a Vault context block to the receipt. If no lesson matches or recall times out, the receipt states no lessons surfaced or recall not available. `0` is treated as evidence, not as missing, so a zero count does not suppress recall.

What it learns: two sources can become lesson candidates through the intake door, each at most once per distinct pattern, only when `BROTHERDS_VAULT` is set:

* A MISSED quantile score where `wis` is written and outcome contains MISSED.
* A gate failed on two or more distinct claims in a ledger run, evaluated by `lessons.recurring_failures` with `min_claims=2`.

In both cases the tool writes a candidate markdown file under `00-Inbox` via the intake door and records the seen key in `.bds-lessons-seen.json` beside the claims so the same pattern is not proposed twice. A person decides whether the candidate becomes a rule. The inbox file contains title `Recurring FAIL <gate> in <count> claims`, share percent, claim ids truncated after 20 with and N more, guidance from `GUIDANCE` such as `Sample below the merge threshold before you claim recall` for M8, example finding with digits replaced by N, and footer `Proposed by BrotherDS from the claim ledger; a person decides whether it becomes a rule.`

Where the Vault tools are found: `products/brothermode/tools/bm_vault.py` with commands `index`, `recall`, `intake`. Environment: `BROTHERDS_VAULT` path to vault directory, `BROTHERDS_VAULT_TOOLS` path to tools, `BROTHERDS_RECALL_TIMEOUT_S` seconds for recall.

## Worked example

Two sample claims ship in `products/brotherds/examples`.

* `example-mdm-evaluated.json` is a complete claim. It declares `master_data.merge_threshold 0.8`, `evaluation.review_strata` derived from `review-sample-mdm.csv` with four strata, `precision_above` and `recall` from stratified estimates, `blocking` with pair completeness, `gold_clusters`, `cluster_sizes`, `fs_parameters` with m above u, `score_drift` acknowledged, `quality` with thresholds and method, `golden_record` with rule and source, `headline_metric`, `required_margin`, `gold_sample` probability weighted, `labeller` human, `scores_are_probabilities`, `error_costs`, and Japanese normalization with required steps and passing probes. Running `python3 products/brotherds/bds.py check example-mdm-evaluated.json` prints PASS for M7 through M22 and VERDICT PASS or NO-DATA free, so the decision can proceed.

* `example-mdm-overclaim.json` is the hurried claim. It claims high precision and recall but supplies review only above threshold, missing below threshold strata, plus convenience gold frame, missing kappa, and incomplete normalization. Running `python3 products/brotherds/bds.py check example-mdm-overclaim.json` prints FAIL for M8.recall_evidence with detail `cannot estimate recall: review strata do not sample pairs below the merge threshold` and also FAIL for M7, M9, M16, M18, M19, and M22 with missing `itaiji` and `small_ke`. Overall VERDICT is FAIL. The receipt recalls lesson `Recall above the merge threshold` from `40-Failures/recall-above-threshold.md`.

Deriving the numbers: run `python3 products/brotherds/bds.py mdm-eval review products/brotherds/examples/review-sample-mdm.csv --merge-threshold 0.8`. Output contains `merge_threshold 0.8`, `strata` length 4, `precision_above` with estimate and lo and hi and n_sampled, `recall` with `covers_below_threshold true` and recall point estimate, and `n_rows`. Copy `strata` into `master_data.evaluation.review_strata` and set `claimed_precision` within the interval to pass M7. Do the same for recall to pass M8.

## What the gates do not do

* Tolerances `0.05` and `0.02` are conventions, not derived optima. M8 and M10 allow claimed values to exceed estimates by up to 0.05 before FAIL. M21 allows `merge_threshold` to sit 0.02 below cost implied t star before FAIL and notes more conservative thresholds. A tighter business risk may need a smaller allowance, and the gates will not choose it for you.
* PSI bands are conventions not tests. M13 uses `psi_band` with `<0.10` stable, `<0.25` moderate, else major. These cutoffs are industry practice from credit scorecard monitoring, not critical values, and are sample size and binning sensitive.
* The giant cluster threshold is a convention. M11 flags `giant_clusters` where size greater than or equal to `max(50, 0.01 * n_records)`. A single large component is almost always over merging, but the exact cutoff depends on entity size distribution and is not a theorem.
* The recall estimate is a point estimate. M8 compares claimed recall to `review_recall` point estimate `matches_above over matches_total`. It does not form a confidence interval for recall, so narrow samples can appear precise when they are not.
* No gate can see labels it was not given. If you do not supply review strata, gold clusters, blocking counts, or human overlap, the gate returns NO-DATA. NO-DATA is not a soft PASS. It means stop and collect evidence. Similarly `fold_latin` being present does not prove you handled phone or person keys correctly, and probe equality does not prove recall in production.

## Research basis

This documentation draws on `research/C1-mdm-data-science-best-practice-2026-09-12.md` and `research/C2-harness-and-market-practice-2026-09-12.md`, plus classic sources named in modules:

* Wilson 1927 for Wilson score interval, Clopper and Pearson 1934 for exact binomial interval, Bagga and Baldwin 1998 for B-cubed, Christen 2012 Data Matching for blocking measures and quality versus complexity, Fellegi and Sunter 1969 for m and u likelihood weights, Cochran 1977 for stratified sampling and Cochran sample size, Hand and Christen 2018 for F-measure weighting, Binette et al 2022/2023 for representative entity resolution estimates, Cohen 1960 for kappa, Landis and Koch 1977 for kappa bands, Guo et al 2017 for calibration and ECE, Gneiting and Raftery 2007 for interval score and WIS approximation to CRPS, Bracher, Ray, Gneiting and Reich 2021 for weighted interval score with weights w0 0.5 and wk alpha over 2 and decomposition into dispersion plus under plus over, Senzing auditing for precision as Same Positives over Newer Count, and Potato Judge Calibration and related human evaluation guidance for LLM as labeller temperature and k sampling and honeypot targets.

Treat the research notes as synthesis with confidence varying by section. Where notes mark a threshold as convention, gates implement it as such and report it in the detail string so you can see the allowance that was applied.

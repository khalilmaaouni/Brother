# BrotherDS

**Every number that reaches a decision carries its proof, and is scored later
against what actually happened.**

BrotherDS is the third of three products. BrotherMode records what happened
during a piece of work. BrotherSBE decides whether a change to a system can be
trusted. BrotherDS takes a different unit: **the claim**, one number that
reaches a decision, and asks whether that number is true, what it does not
establish, and whether reality later agreed with it.

MIT licensed. No account, no service, no data leaves your machine.

## Why

A pipeline can be perfectly built, perfectly reviewed and perfectly released,
and still produce a number that is wrong. Change assurance does not catch that,
because nothing changed.

On the first evening this tool was pointed at a real warehouse it found that
2,350 product identifiers each covered more than one distinct product, so draft
beer and bottled beer had been silently merged in every per-product figure ever
computed there. Nothing was broken. Everything ran. The number was just wrong.

## Install

Python 3.9 or newer. One dependency, and only for claims that query a database.

```bash
pip install duckdb
```

## Use

A claim is a JSON file. Write one, then check it.

```bash
python3 bds.py check examples/example-descriptive.json
```

The engine re-runs every derivation, applies the core gates and the pack for
the claim type, and returns one of three verdicts.

```
PASS      G9.grain                   one row per calendar month, whole network
PASS      G5.rederivation            2 independent derivations agree at 10,493,293,552
NO-DATA   G3.uncertainty             not established: this is a census, not a sample
FAIL      G6.baseline                does NOT beat carrying the last value forward
```

`PASS`, `FAIL`, `NO-DATA`. **NO-DATA is never a pass and never a block.** It
means the claim needed something that was never measured, which is a different
thing from the claim being wrong.

Then render the receipt a decision maker actually reads:

```bash
python3 bds.py receipt examples/example-descriptive.json
```

And verify the engine itself:

```bash
python3 bds.py selftest
```

## The ten gates

| Gate | What it refuses |
|---|---|
| G1 | an empty "what was not established" list |
| G2 | a claim that does not name one of the five evidence classes |
| G3 | a bare number with no uncertainty and no reason for its absence |
| G4 | causal language with no design, or a design whose assumption was never tested |
| G5 | a number reached by only one path |
| G6 | MAPE on near-zero denominators, accuracy with no baseline, seasonality on too little history |
| G7 | a stated value that no longer reproduces |
| G8 | an incomplete protocol for a non-system claim |
| G9 | a number with no declared grain, or a key that is not unique |
| G10 | two claims using one metric name with two different definitions |

## Claim types and packs

Declare `claim_type` as one of `DESCRIPTIVE`, `FORECAST`, `CAUSAL`,
`MASTER_DATA`, `EXPERIMENT`, `DETECTION`, or `PIPELINE`. A declared value takes
precedence; an unknown value is FAIL at `G15.claim_type`.

If no type is declared, inference checks these rules in order and stops at the
first match:

1. A causal token in `statement`, such as "caused", "drove", or "lift", gives `CAUSAL`.
2. A non-empty `accuracy` block gives `FORECAST`.
3. A `match` object containing `precision`, `recall`, or `threshold` gives `MASTER_DATA`.

Only `CAUSAL`, `FORECAST`, and `MASTER_DATA` are ever inferred. `DESCRIPTIVE`,
`EXPERIMENT`, `DETECTION`, and `PIPELINE` must be declared. Silence does not
mean `DESCRIPTIVE`. An inferred type still receives NO-DATA at
`G15.claim_type`, because nobody declared it; the compact receipt card shows
that distinction. Declare `EXPERIMENT` for an experiment with "lift" in its
statement, or it will be inferred as `CAUSAL` and the experiment pack will not run.

Each pack runs only for its matching effective claim type, alongside the core
gates. The fields below are declared evidence: a gate does not run an experiment,
merge records, or execute a pipeline. Some gates report NO-DATA or only apply
when particular wording or fields are present; absence of a gate line is not PASS.

### EXPERIMENT pack

All fields in this table are under `experiment`.

| Gate | What it refuses | Fields read |
|---|---|---|
| `X1.srm` | It refuses invalid arm counts or allocation ratios, and arm imbalance above its chi-square threshold. | `arms[].users` or `arms[].units`, `intended_ratio` |
| `X2.mde` | It refuses invalid inputs or an observed relative effect whose magnitude is below the computed minimum detectable effect. | `arms[].users` or `arms[].units`, `primary_metric.baseline_rate`, `primary_metric.observed_effect_relative`, `alpha`, `power` |
| `X3.guardrail` | It refuses invalid guardrail entries or a declared significant change in a guardrail's bad direction. | `guardrails[].name`, `guardrails[].delta`, `guardrails[].significant`, `guardrails[].direction_bad` |
| `X4.design_hygiene` | It refuses invalid hygiene values and reports missing ones as NO-DATA, without testing whether the reported hygiene worked. | `aa_test_run`, `novelty_window_excluded_days`, `variance_reduction_method` |
| `X5.pvalue_wording` | It refuses invalid result fields or a p-value with neither effect size nor interval, and reports NO-DATA if just one of those two is missing. | `p_value`, `effect_size`, `ci` |

Omitted `intended_ratio` means equal allocation; omitted `alpha` and `power`
use 0.05 and 0.8. The arm-balance threshold table covers two through five arms.
The minimum detectable effect uses the smallest arm and a baseline-variance
approximation, with alpha 0.05 or 0.01 and power 0.8 or 0.9; unsupported table
settings are NO-DATA. `X5` checks the supplied result fields, not their derivation
from conversions.

### DETECTION pack

Fields are under `detection` unless marked as top-level.

| Gate | What it refuses | Fields read |
|---|---|---|
| `D1.confusion` | It refuses precision or recall alone without complete counts, or a stated value that differs from its computable count-based value by more than 0.005. | `tp`, `fp`, `fn`, `precision`, `recall` |
| `D2.base_rate` | It refuses "accurate" or "accuracy" wording when the base rate is below 0.10 and computed positive predictive value is below 0.5. | top-level `statement`; `base_rate`, `recall`, `specificity`, `tp`, `fn`, `fp`, `tn` |
| `D3.anomaly_evidence` | It withholds a pass with NO-DATA when "caught", "detected", or "early" wording has neither precision at k nor lead time. | top-level `statement`; `precision_at_k`, `lead_time` |
| `D4.judge_admissible` | It refuses an `llm` judge without a gate id, with missing kappa or kappa below 0.6, or with a parsed calibration date older than 30 days. | `judge.kind`, `judge.gate_id`, `judge.kappa`, `judge.calibrated_on` |
| `D5.wording` | It adds NO-DATA wording advice after `D2.base_rate` fails, asking for precision and positive predictive value at the base rate instead of accuracy alone. | the `D2.base_rate` finding, derived from the fields above |
| `D6.trajectory_reward` | It refuses `self_report` as the reward source for agent-success wording and reports missing reward evidence as NO-DATA. | top-level `statement`; `trajectory_reward`, `reward_source` |
| `D7.first_broken_step` | It refuses a supplied first-broken-step value that cannot convert to an integer index within the declared steps, and reports a missing index as NO-DATA. | `steps`, `first_broken_step` |

`D1` emits no finding when both precision and recall are stated without complete
counts. `D2` reports a missing base rate as NO-DATA, but emits no finding if the
rate is present and recall or specificity cannot be obtained. `D4` currently
accepts missing or unparseable calibration dates. `D5` adds advice to the gate
list; it does not rewrite the statement.

### MASTER_DATA pack

Fields are under `master_data` unless marked as top-level. Wording checks read
`statement`, `question`, and `decision` together.

| Gate | What it refuses | Fields read |
|---|---|---|
| `M1.threshold_bands` | It refuses nonnumeric thresholds or values outside zero to one, a review threshold above the merge threshold, or a merge threshold without a review threshold. | `review_threshold`, `merge_threshold` |
| `M2.survivorship` | It refuses merge or golden-record wording without attribute rules, or with a rule outside the accepted set. | top-level `statement`, `question`, `decision`; `survivorship` |
| `M3.false_merge_cost` | It refuses an unknown false-merge cost class or a catastrophic cost without `review_queue=true`. | `false_merge_cost_class`, `review_queue` |
| `M4.review_sample` | It refuses either declared sample size being zero and reports both missing sizes as NO-DATA. | `review_sample_n`, `ground_truth_n` |
| `M5.residual_error` | It withholds a pass with NO-DATA when benchmark or gold-set wording lacks a declared residual error rate. | top-level `statement`, `question`, `decision`; `known_residual_error_rate` |
| `M6.twin_metrics` | It reports missing match rate or sampled accuracy as NO-DATA for matching wording, and refuses both metrics together without a positive accuracy sample size. | top-level `statement`, `question`, `decision`; `match_rate`, `sampled_accuracy`, `sampled_accuracy_n` |

Survivorship rules are `recency`, `reliability`, `completeness`,
`source_priority`, or `manual`. False-merge cost classes are `catastrophic`,
`recoverable`, or `cosmetic`. A review threshold alone can pass `M1`.
Most M gates emit no finding without a `master_data` object; `M2` can still
refuse merge wording. The separate core checks also read the `match` block.

#### Master data science, M7 to M19 (1.0.14)

Two more MASTER_DATA packs judge whether the numbers in a merge claim are
backed by evidence that can be recomputed. They read
`master_data.evaluation`. `M7` to `M15` (pack_mdm_science.py) check the
claimed precision against a stratified clerical review, refuse a recall
estimated from a sample drawn only above the merge threshold, cap recall at
blocking pair completeness, compare pairwise and B-cubed precision on a gold
subset, flag unreviewed giant clusters, check Fellegi-Sunter m and u, match
score drift (PSI), quality dimensions and golden-record lineage. `M16` to
`M19` (pack_mdm_decision.py) check that the evaluation fits the decision: the
headline metric and its beta against the false-merge cost, the review sample
size against the margin the decision needs, a representative gold sample, and
human agreement when a model labelled the review.

`python3 bds.py mdm-eval review <review.csv> --merge-threshold 0.8`
recomputes the strata, precision and recall from the review itself, so the
claim carries derived numbers rather than typed ones.
`python3 examples/mdm_demo.py` runs the whole path on synthetic data, and
`python3 examples/estimator_study.py` validates the two estimators the gates
rely on by simulation: over 200 seeded replications the precision interval
covered the true precision 98 percent of the time (nominal 95), and the
end-to-end recall estimate erred by 0.0009 on average against 0.0522 for
review recall alone, which is why M8 multiplies by pair completeness. The
full guide is docs/MDM-SCIENCE.md; the research behind it is in
research/C1-mdm-data-science-best-practice-2026-09-12.md and
research/C2-harness-and-market-practice-2026-09-12.md.

### PIPELINE pack

Fields are under `pipeline` unless marked as top-level.

| Gate | What it refuses | Fields read |
|---|---|---|
| `P1.contract` | It refuses a malformed or incomplete supplied contract and reports an absent contract as NO-DATA. | `contract.version`, `contract.owner`, `contract.schema_hash` |
| `P2.checks` | It refuses malformed check data or a strong check that failed or was skipped, while weak failures only receive an annotation. | `checks[].id`, `checks[].name`, `checks[].severity`, `checks[].result`, `check_suite_id` |
| `P3.upstream` | It refuses invalid run or upstream identifiers for a SYSTEM claim with a run id and reports a missing upstream check as NO-DATA. | top-level `origin`; `run_id`, `upstream_check_id` |
| `P4.reconciliation` | It refuses invalid reconciliation fields or absolute variance above tolerance with an absent or open resolution. | `reconciliation.system_of_record`, `reconciliation.window_start`, `reconciliation.window_end`, `reconciliation.variance`, `reconciliation.variance_tolerance`, `reconciliation.resolution_path` |
| `P5.freshness` | It refuses invalid or timezone-inconsistent timestamps, or arrival after the deadline. | `freshness.expected_by`, `freshness.arrived_at` |
| `P6.disclosure` | It withholds a pass with NO-DATA for missing disclosures and never returns FAIL. | `disclosure.rights_restriction`, `disclosure.source`, `disclosure.cost_basis`, `disclosure.owner`, `disclosure.retention` |
| `P7.node_receipts` | It refuses malformed node traces and reports missing identities or traces, including untraced workflow-success wording, as NO-DATA. | top-level `statement`; `node_receipts[].node_id`, `node_receipts[].hash`, `node_receipts[].run_id` |
| `P8.contamination` | It refuses an invalid contamination share or one above 0.05, and reports an absent rate for bulk-ingest wording as NO-DATA. | top-level `statement`; `contamination_rate` |

Check severity is `strong` or `weak`; result is `pass`, `fail`, or `skipped`.
Missing check fields produce NO-DATA unless a failure is already present;
a missing `check_suite_id` is only annotated. Reconciliation outside tolerance
can pass with `resolution_path` set to `explained`, `corrected`, or `escalated`.
The pack checks those declarations, not whether the resolution was carried out.
Each node receipt needs `node_id` and at least one of `hash` or `run_id`.

## The five evidence classes

Most numbers in a business are not in any system. Treating a vendor extract, an
expert's judgement and an open hypothesis as if they were all "data" is how
commercial analysis goes wrong quietly.

- **SYSTEM** a query against a governed source. Interrogated by independent
  re-derivation.
- **THIRD_PARTY** a vendor or panel dataset. Interrogated on coverage,
  collection method and known biases. Its discount does not shrink as the sample
  grows.
- **ELICITED** expert or manual judgement. Interrogated with a calibration
  question, weighted by performance rather than equally (Cooke's classical
  model beat equal weighting in 32 of 33 cross validation studies).
- **ASSUMPTION** stated and unverified. Requires a sensitivity range and a
  result.
- **HYPOTHESIS** openly untested. May inform a test. May never alone reach a
  decision.

## What capability is actually built

| Capability | Status |
|---|---|
| Claim authoring | PASS |
| Gate checking | PASS |
| EXPERIMENT pack | PASS, 2026-09-11 |
| DETECTION pack | PASS, 2026-09-11 |
| MASTER_DATA pack | PASS, 2026-09-11 |
| PIPELINE pack | PASS, 2026-09-11 |
| Claim Receipt | PASS |
| Score against outcome | PASS for interval claims |
| Claim Ledger | PASS |
| Brother routing | explicit ask only in 1.0.13 |
| Vault recall | NO-DATA |
| Vault lesson candidate | NO-DATA |
| Snowflake adapter | FUTURE |
| Databricks adapter | FUTURE |
| Probability calibration | FUTURE |
| Causal outcome resolution | LIMITED |

PASS means built and exercised by `selftest`. NO-DATA means the seam is named
but nothing is wired to it yet. FUTURE means it is not started. LIMITED means
it does part of the job and says so: G4.causal refuses an undesigned causal
claim and `safe_wording` rewrites it into an honest association, but nothing
here runs the causal design itself.

## What this is not

Not a dashboard, not a notebook, not an AutoML platform, not a data catalogue,
not an orchestrator, and not a replacement for a language model. Not a BI
tool, semantic layer, data-quality platform, experiment platform or AutoML.
It governs and scores analytical work. It does not try to be a better general
analyst than the frontier models.

An LLM may sit in a verification path only as first-pass triage that a
deterministic check or a human confirms. It may never be the final arbiter of
arithmetic. The measured state of the art justifies this: the best system on
Spider 2.0 scores 30.35 percent, and Databricks' own documentation says
single-model text-to-SQL "fails a lot in production".

**The language model may propose. The mathematics disposes.**

## Honest limits

Read `SPEC.md` for the full list. The short version: nothing forces a claim to
exist; the "not established" field is checked for being non-empty and never for
being honest; and the adjustment methods for the four non-system classes are
documented but not yet computed.

Those are stated here rather than discovered later, because a limit that no file
enforces should say so.

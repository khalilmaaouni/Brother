---
name: brotherds
description: Use when a number is about to reach a decision. Turns a figure into a checkable claim that carries its query, its grain, its uncertainty and what it does not establish, then runs the core gates and the matching claim pack and returns PASS, FAIL or NO-DATA. Also use for MDM product identification: resolving or verifying a Japanese retail JAN/EAN/GTIN barcode, or classifying a product's packaging type from its name. Triggers include "what is our", "how much did", "did the promotion work", "what is the forecast", "is this number right", "can I put this in the deck", "elasticity", "incremental", "lift", "MAPE", "forecast accuracy", "verify this claim", "claim receipt", "can I trust this number", "did the A/B test work", "is the detector accurate", "did the merge go right", "is the pipeline reconciled", "can we trust this match rate", "evaluate the steward review", "dedup precision", "dedup recall", "golden record", "customer master cleanup", "Japanese name matching", "is the forecast calibrated", "what does the team keep getting wrong", "MDM product identification", "JAN code", "JAN code verification", "barcode check", "Japanese retail barcode", "find this product's barcode", "verify this GTIN", and any moment you are about to state a figure a person will act on.
---

# BrotherDS

Every number that reaches a decision carries its proof, and is scored later
against what actually happened.

## When this applies

The trigger is not "someone mentioned data". It is **a number is about to reach
a decision**. If the figure will sit in a deck, a plan, a business case or a
sentence somebody acts on, it is a claim and it needs a receipt. If it is
exploratory and nobody will act on it, say so and move on. Ceremony on throwaway
work is how a good control gets abandoned.

## The first thing to do, before any SQL

Ask what decision the number serves. If there is no answer, there is no claim,
only trivia. Write the decision down before writing the query, because the
decision determines the grain, and the grain determines the query.

## The five origins

Every claim names one. Most numbers in a business are not in a system, and
treating a vendor extract, an expert's judgement and an open hypothesis as if
they were all "data" is how commercial analysis goes wrong quietly.

| Origin | Use when | You must also capture |
|---|---|---|
| `SYSTEM` | it comes from a query against a governed source | two independent routes to the same number |
| `THIRD_PARTY` | vendor, panel or external dataset | provider, collection method, coverage, known biases |
| `ELICITED` | a person's judgement, however expert | their role, the protocol used, a calibration question, the seed score |
| `ASSUMPTION` | stated and unverified | who stated it, the plausible range, what the decision does across that range |
| `HYPOTHESIS` | openly untested | the test that would settle it, the cost of being wrong |

An `ELICITED` number is not worse than a `SYSTEM` one. A well-elicited expert
estimate often beats a badly-built query. What is fatal is not knowing which one
you are holding.

## The workflow

Authoring the claim is a side effect of doing the analysis, not a separate
JSON-writing chore. Talk the analyst through the plain-words version of the
schema, then hand the answers to `bds.py author` on stdin as `key=value` lines
(dotted keys nest objects, `not_established=` may repeat for each limit,
`evidence.derivation=name|sql` may repeat for each independent route to the
number). The tool writes the file and shows the G-gate result in the same
step; never open the JSON in an editor and type it by hand.

- What decision does this number serve, and the statement itself.
- Which of the five origins, honestly.
- The `claim_type`: `DESCRIPTIVE`, `FORECAST`, `CAUSAL`, `MASTER_DATA`,
  `EXPERIMENT`, `DETECTION`, or `PIPELINE`, declared separately from origin.
- The grain: one row per what.
- The uncertainty: an interval and its method, or `NOT_ESTABLISHED` and why.
- What this does NOT establish, at least one, never a formality.
- The derivation (SYSTEM: two independent SQL routes) or the origin's protocol
  (THIRD_PARTY/ELICITED/ASSUMPTION/HYPOTHESIS: capture the fields listed under
  the five origins above, checked by gate G8).

```bash
python3 bds.py author claims/rgm-014.json <<'EOF'
id=RGM-014
statement=State the claim in one sentence, in the decision maker's own words.
value=8123
unit=orders
origin=SYSTEM
question=What decision does this number serve?
decision=What changes depending on the answer?
grain=one row per order line, week ending 2026-08-16
uncertainty.kind=NOT_ESTABLISHED
uncertainty.why=this is a census, not a sample
not_established=Completeness against the order-management system was not checked.
not_established=Cancellations after the week close were not reconciled.
evidence.source=~/path/to/your.duckdb
evidence.derivation=fact table rollup|select sum(orders) from fact_order_line
evidence.derivation=monthly mart|select sum(orders) from mart_weekly
EOF
python3 bds.py receipt claims/rgm-014.json out.md # the page a human reads
python3 bds.py register claims/rgm-014.json       # make its metric definition the reference
python3 bds.py score claims/rgm-014.json 8123 "ops lead" 2026-09-30
python3 bds.py ledger claims/                     # the verified claim rate
```

`author` refuses to write anything with a missing required field (id,
statement, value, unit, origin, question, decision, grain, uncertainty,
not_established) and names what is missing; nothing is silently defaulted. Use
`bds.py new` only for a scaffold you intend to hand-fill outside a guided
conversation; `author` is the path this skill actually uses.

## Declaring the claim type and collecting pack evidence

Ask the analyst to declare the type. If it is absent, the engine checks
`statement` for causal tokens first and infers `CAUSAL`; otherwise a non-empty
`accuracy` block gives `FORECAST`, then a `match` object containing `precision`,
`recall`, or `threshold` gives `MASTER_DATA`. Only those three are inferred.
`DESCRIPTIVE`, `EXPERIMENT`, `DETECTION`, and `PIPELINE` require an explicit
declaration. Inference still receives NO-DATA at `G15.claim_type` on the compact
card. In particular, declare `EXPERIMENT` for an A/B result with "lift" in its
statement so the experiment pack runs instead of inference selecting `CAUSAL`.
Each pack runs only for its matching type, alongside the core gates.

For `EXPERIMENT`, ask for `experiment.arms` with each arm's `users` or `units`,
the `intended_ratio`, `primary_metric.baseline_rate` and
`primary_metric.observed_effect_relative`, plus `alpha` and `power`. Under the
same `experiment` block, collect each `guardrails` entry's `name`, `delta`,
`significant` and `direction_bad` (`up` or `down`), the reported `aa_test_run`,
`novelty_window_excluded_days` and `variance_reduction_method` (`none`, `CUPED`
or `other`), and `p_value`, `effect_size` and `ci`. Ask for the effect size and
interval even when the analyst starts with a p-value; hygiene declarations do
not prove that the hygiene worked.

For `DETECTION`, ask for `detection.tp`, `fp`, `fn` and `tn`, the stated
`precision` and `recall`, and the deployment `base_rate` and `specificity`.
Within `detection`, collect `precision_at_k` or `lead_time` when the claim says
an event was caught, detected or caught early. If a judge is used, ask for
`judge.kind` and, for `llm`, `judge.gate_id`, `judge.kappa` and
`judge.calibrated_on`. For agent-success wording, ask for `trajectory_reward`
and `reward_source`; self-report is refused. For a multi-step result, collect
`steps` and `first_broken_step`. The wording gate adds advice after a base-rate
failure; it does not rewrite the claim or establish missing evidence.

For `MASTER_DATA`, ask for `master_data.review_threshold`, `merge_threshold`
and per-attribute `survivorship` rules (`recency`, `reliability`, `completeness`,
`source_priority` or `manual`). In the same block, collect
`false_merge_cost_class` (`catastrophic`, `recoverable` or `cosmetic`) and
`review_queue`, then `review_sample_n`, `ground_truth_n` and, when a benchmark
or gold set is named, `known_residual_error_rate`. Ask for both `match_rate`
and `sampled_accuracy`, with `sampled_accuracy_n`. These pack fields live under
`master_data`; they do not replace the separate `match` evidence that core
gates read.

For `PIPELINE`, ask for `pipeline.contract.version`, `owner` and `schema_hash`;
each `pipeline.checks` entry's `id`, `name`, `severity` (`strong` or `weak`) and
`result` (`pass`, `fail` or `skipped`); and `pipeline.check_suite_id`, `run_id`
and `upstream_check_id`. Collect `pipeline.reconciliation.system_of_record`,
`window_start`, `window_end`, `variance`, `variance_tolerance` and
`resolution_path` (`explained`, `corrected`, `escalated` or `open`), plus
`pipeline.freshness.expected_by` and `arrived_at` as ISO timestamps. Ask for
`pipeline.disclosure.rights_restriction`, `source`, `cost_basis`, `owner` and
`retention`, each `pipeline.node_receipts` entry's `node_id` and `hash` or
`run_id`, and `pipeline.contamination_rate` for a bulk load. These are evidence
about the number and its run; they do not replace review of a pipeline change.

The guided `author` input supports nested objects but does not parse pack lists,
booleans or most numeric pack fields into their required types. Do not promise
that passing those as text will produce a valid pack claim. Collect the evidence
from the analyst's structured claim export, then use `bds.py check` and
`bds.py receipt` on that file; name missing evidence as NO-DATA. The four pack
examples in `examples/` show the field shapes, not proof of a real result.

## Naming a metric, and why it matters more than the number

If the claim computes something with a name people use in meetings (GMV,
revenue, incremental volume, share), declare it:

```json
"metric": {"name": "GMV", "definition": "gross order value before cancellations,
           all wholesalers including demo accounts", "definition_source": "..."}
```

Gate G10 compares that definition against the registry. Two claims using one
name with two definitions is a FAIL that names both. This is the failure that
actually destroys trust in a commercial team: not a wrong number, but two right
numbers that disagree because nobody wrote down what the word meant.

First use is NO-DATA, because one claim proposes rather than defines. Run
`register` to make it the reference.

Turn the conversation straight into `bds.py author` stdin. Do not ask the
analyst to write JSON; run the command for them and show them the receipt.

## What you must never do

**Never fill `not_established` with a formality.** It is the field that makes the
receipt worth carrying. A reviewer reads it to know where to spend attention. If
you write "standard caveats apply" you have destroyed the only part a busy
person actually needs. Write the specific things this analysis did not settle:
the reconciliation nobody ran, the assumption nobody tested, the population that
was not checked.

**Never write causal language you cannot support.** "Drove", "caused",
"incremental", "lift", "impact of", "thanks to". Gate G4 refuses these unless the
claim names a design (randomised holdout, difference in differences, synthetic
control, event study, and so on) AND names the test that checked that design's
assumption. If there is no design, rewrite the sentence as an association, or
declare the claim a `HYPOTHESIS` and say what test would settle it.

**Never report an accuracy figure without a baseline.** A forecast is only good
relative to carrying the last value forward. Gate G6 computes that comparison and
will tell you when a model is worse than doing nothing, which happens more often
than people expect.

**Never let a language model be the final arbiter of arithmetic.** Propose with
the model, dispose with the mathematics. The measured reliability of language
models on exactly this work is poor: the best system on Spider 2.0 scores 30.35
percent. Anything a deterministic check could verify, verify deterministically.

## Reading the verdicts

- `PASS` the gate found what it needed and it held.
- `FAIL` the gate found something wrong. Fix the claim or fix the analysis.
- `NO-DATA` the claim needed something that was never measured. **This is not a
  pass and not a block.** It is the honest state of most real analysis, and
  saying so is the product working, not failing.

A claim with several NO-DATA verdicts is normal and useful. A claim with none is
either exceptional work or a claim whose author was not honest about its limits.

## What this does not do

It does not decide whether a claim is true. It decides whether the claim is
proven, disproven or unexamined. Truth arrives later, when the outcome does, and
the `outcome` field is where reality gets to grade the work. A gate verdict is a
statement about proof. Only an outcome is a statement about the world.

It also does not review a change to a pipeline. That is BrotherSBE's data
reviewer, which already covers grain, fan out, keys, system of record,
reconciliation, freshness and cost. Do not rebuild it; if a pipeline changed,
route there.

## Talking to the person

Outcome first, in plain words. Not "G4 returned FAIL on the causal predicate".
Instead: "This says the campaign drove the increase, but nothing here separates
the campaign from what would have happened anyway. Either soften the sentence to
an association, or run a holdout and we can claim it properly."

One recommended next action, never a menu.

## Master data evidence to collect (1.0.14)

For a `MASTER_DATA` claim, ask for the steward review as a CSV (score, label,
optional stratum_population) and run
`python3 bds.py mdm-eval review <csv> --merge-threshold <t>`; copy its strata
into `master_data.evaluation.review_strata` instead of typing precision and
recall. Then ask for: `claimed_precision`, `claimed_recall`,
`headline_metric` (and `beta` when it is `f_beta`), `required_margin`, the
`blocking` counts, a `gold_clusters` subset with its `gold_sample` frame,
`cluster_sizes`, `fs_parameters`, `score_drift`, `quality` dimensions,
`golden_record` lineage, and who labelled the review (`labeller`). A recall
figure from a sample drawn only above the merge threshold is refused by
`M8.recall_evidence`; say so plainly rather than softening it. Missing
evidence stays NO-DATA. docs/MDM-SCIENCE.md is the field reference.

For a quantile forecast, `score` now also records the weighted interval score
and whether reality fell inside the 0.1 to 0.9 band, and `ledger` prints the
band's coverage against the nominal 0.8 once five quantile claims resolve.

## MDM product identification: JAN / EAN / GTIN barcode lookup

Different shape from the claim workflow above: no `bds.py check` here, this
is resolving or verifying a product identifier, not gating a stated number.
Trigger on a missing or suspect Japanese retail barcode, or on identifying a
product from a bare name, for MDM or product-master cleanup work.

Full protocol, worked example, and the literal subagent brief for the web
research steps: `docs/MDM-JAN-LOOKUP.md`. In short, in order:

1. Internal join first, always, before any internet call: check every other
   file or table already on hand for the missing code.
   `python3 mdm_jan_lookup.py resolve --input <csv> [<csv> ...] --out <out.csv>`.
2. Checksum-validate every candidate (`mdm_validate.gtin_check`, reused, not
   reimplemented): a code that fails is provably wrong, one that passes is
   only plausible.
3. Only unresolved rows go to real web research, run by a subagent briefed
   with the anti-fabrication and anti-delegation rules in
   `docs/MDM-JAN-LOOKUP.md` verbatim: never invent a code or a URL, never
   delegate the search to another agent, report NONE rather than guess.
4. Report HIGH/MEDIUM/LOW/NONE confidence, justified in each row's own notes.
   A second-opinion LLM lane's output is a proposal only, checksum-gated
   before it can count above NONE.
5. Packaging type (`loose`/`case`/`half_case`/`syrup`/`powder`/`canister`/
   `other`) comes from the product name text via `classify_packaging`
   (`python3 mdm_jan_lookup.py classify "<name>"`), never guessed
   independently.

Done-check for this capability: `python3 mdm_jan_lookup.py --selftest`.

## In 1.0.13

Brother routing is explicit ask only: nothing here calls BrotherMode or
BrotherSBE on its own, and nothing here calls them on your behalf without you
asking for it. Vault recall and Vault lesson candidates are both NO-DATA,
named as seams and not wired in. `bds.py receipt` puts a compact card at the
top of the page, above the full gate list: the claim, its declared or inferred
claim type, the number, its definition, grain, re-derivation, declared evidence
independence (NO-DATA when absent), causality (or the literal NOT CLAIMED when
the statement makes no causal assertion), uncertainty, what it does not
establish, a deterministically rewritten safe wording when a causal claim
did not earn its wording, reality (OPEN until scored), the Vault line, and
the receipt's own path. Score against a real outcome and the ledger both work
today for a claim that stated an interval; only `bds.py score`, run against a
real outcome, moves a claim out of OPEN.

# Playbook: a location-anchored account master

This playbook is for a team consolidating hundreds of thousands of B2B outlet records in SAP against a licensed third party establishment level reference database, with a five level account hierarchy whose top levels are derived by roll up. An outlet is a physical location. The master is location anchored. You match only at the outlet level, then you derive corporate parents by a vote. This order makes later unmerges possible and keeps the hierarchy checkable.

Use the audit tool to prove each practice. Gates are M23 through M30, commands are mdm_audit audit, mdm_audit evaluate and bds check.

## Anchors, in order of trust

Trust is ranked by how hard the identifier is to share by accident.

1. 13 digit corporate number with its check digit, validated. Use it only when the check digit is correct and the number is present on both sides. Sole proprietors and individual outlets have no corporate number, do not invent one or copy a parent number down to the outlet.
2. The qualified invoice issuer registration number, validated with its own check. It is a legal registration, but an outlet can operate under a parent issuer, so it is weaker than the corporate number for outlet identity.
3. Address normalized against the national address base registry. Normalize with the same normalizer version for source and reference, keep the version in the manifest, and treat a normalized address as a blocking key, not as proof on its own.
4. Phone as a blocking key, never as proof. A headquarters phone repeats across many outlets, a shared number on many source records is a hub, not evidence.
5. Names last. Names carry variation, use them only inside a block defined by a stronger key or by an embedding search, and require a labelled sample for any name based pathway.

Test: every pathway is labelled and every phone hub is reviewed. M24 fails when matched rows rest on a shared key with at least hub_min sources unless hubs_reviewed is true. M25 fails when a material pathway has no labelled sample. Validators for the corporate number and the qualified invoice issuer registration number are in products/brotherds/mdm_validate.py, run them before any match and hash the normalizer version.

## Enforce one reference per outlet at match time

Write an assignment ledger or conflict table at match time, before promotion. One row per source_id with source id, reference id, pathway, score, shared key evidence and resolution state. Include the reference snapshot the match was run against.

Test: collisions are counted. M24 fails when X reference record(s) received more than one source record unless collisions_reviewed is true, and says NO-DATA when the computed audit has no collision counts instead of passing. The audit output collisions gives reference_ids_with_multiple_sources and top ten by count, key_hubs gives hubs and rows_on_hubs for key_phone. M23 also fails if reported pathway counts do not match computed. Fix collisions in the ledger, keep at most one source per reference, promote only resolved rows. Do not discover collisions after export.

## Keep a decision-evidence manifest for every run

For every run keep a manifest that lists reference snapshot, normalizer version, blocking rule and candidate count per source, pathway, model and prompt version, score, threshold and human label when present. Hash the inputs and store input_sha256 from mdm_audit audit_path beside the manifest. This is what separates a coverage gap from a candidate miss from a scoring rejection.

Test: M23 compares reported n_input, status_counts and pathway_counts to computed n_rows and reconciliation counts, and checks that percents recompute. Provenance lists which of reference_snapshot, model_version and normalizer_version were present. Without a manifest you cannot tell whether an unmatched record had no candidate, had a candidate scored too low, or was rejected by the verifier, and you cannot make an unmerge attributable.

Practice: write the manifest as JSON beside results.csv and include it in the claim under master_data audit computed and reported.

## Cascade, and measure every stage

Run deterministic keys first, then embedding auto accept, then an LLM verifier only on the uncertain band. Do not let the LLM see pairs that a key already decided, and do not let the embedding auto accept pairs that the LLM later overturns without a recorded override.

Test: each stage is a pathway with its own pathway_counts and score band. M25 measures each pathway separately, material pathways are those with at least 0.05 of matches, and fails when the combined share of unreviewed material pathways reaches 0.05 or more, and fails an overall claimed precision that has no labelled sample behind it; pathway precision is weighted over score-band strata. The audit output pathway_band shows per pathway counts in lt_0.80, 0.80_0.90, 0.90_0.95 and ge_0.95. Use those bands to set the uncertain band and keep the threshold in the manifest.

Measure cost beside quality. Record cost per thousand pairs for the embedding stage and for the LLM verifier stage, and keep it beside the Wilson interval for that stage.

## Label the right sample

The review plan is stratified by pathway and score band, unmatched strata included, about one hundred per stratum at a 0.10 margin, labels that split coverage gaps from misses.

Run python3 products/brotherds/mdm_audit.py audit results.csv --plan plan.csv to generate the plan, then label the label column. Strata are every MATCH pathway crossed with the four score bands plus one stratum per NO_MATCH reason. Sample size per stratum is min of population and max of min_n and min of max_n and ceil of 1.96 squared times 0.25 divided by margin squared, defaults min_n 10, max_n 400, margin 0.10 and seed 7, selection is deterministic after sorting by source_id with each stratum drawing with its own seed derived from the plan seed and the stratum name, so strata are independent.

Label rule: in MATCH strata label 1 when the link is correct, else 0. In unmatched strata label 1 when an exhaustive search finds a correct reference record the matcher missed, and 0 when no reference record exists at all, a coverage gap. Then run python3 products/brotherds/mdm_audit.py evaluate plan-labelled.csv --results results.csv, which returns pathway_review weighted over score-band strata and unmatched_review weighted over unmatched strata by populations with reference_absent and matchable_missed. The unmatched share is weighted because strata are sampled at different rates, so pooling biases it. M25 uses pathway_review, M28 uses the weighted unmatched_review and reports a conservative recall range from precision_lo times M divided by (M plus U times q_hi) to M divided by (M plus U times q_lo), bounded at 0 when nothing matched; an honest claim states the lower end, and the range covered the true recall in 59 of 60 synthetic runs. Do not label a random sample of the whole file and do not label only MATCH rows. Reusing the same plan seed across repeated audits repeats the same sample positions; choose a new seed for every audit and record it in the claim.

## Report by segment, never one headline

Do not report one match rate for the whole file. Report per segment, for example per chain, with intervals.

Test: M27 reads segments with segment, n and matched, or computed segments from the table. It keeps only segments with at least min_segment_n records, default 30, as eligible, counts small segments as NO-DATA, computes a Bonferroni z over k eligible segments, then for each eligible segment compares its rate to the pooled rest with a Newcombe hybrid score interval. It fails when a segment is materially below the rest by more than material_gap, default 0.10, and segment_disclosure is not true. Declare the material gap in advance.

Practice: if a chain is small list it as small with N under min_n, do not claim the headline covers it. The audit output segments gives Wilson 95 percent intervals per segment, use them in the report.

## Treat the LLM verifier as a labeller that must earn trust

An LLM verifier is a labeller, not ground truth. It must earn trust with a labelled sample.

Test: M26 checks verifier kind, requires at least 30 human labelled confirmations and at least 30 sampled rejections for kind llm or model, else FAIL, and fails a claimed verifier precision with no labelled confirmation sample and a review whose sample exceeds its population. It checks claimed verifier precision against the Wilson interval of the confirmed sample, and always reports false omission rate over rejections and, when supplied, miss rate over a frame of known true matches. A sample of rejections estimates the false omission rate, not the miss rate, which needs a frame of known true matches. Keep the model version pinned in the manifest and in claim master_data audit verifier, and compute agreement with humans.

Agreement is M30, which reads labeller_confusion with both_match, model_only, human_only and both_nonmatch, requires at least 50 overlapping labels, computes kappa with a large sample standard error, checks the one-sided 95 percent lower bound with z equals 1.645 against min_kappa_lower default 0.6, and reports positive agreement 2a divided by (2a plus b plus c) and negative agreement 2d divided by (2d plus b plus c) because kappa depends on prevalence. The demo's honest claim can still fail M30: in the synthetic run the LLM verifier barely agrees with the stewards with kappa near zero, and a truthful claim is still refused when its evidence says the verifier cannot be trusted.

Do not let the verifier decide and then label only its confirmations. You must also sample its rejections.

## Roll the hierarchy up, and check it

Match only at the outlet level, derive corporate levels by confidence weighted vote, flag every conflict, no cycles, one parent per node at a time, zero linkages to retired entities.

Test: M29 reads hierarchy with edges as child parent pairs, retired, linkages and synthesized. It fails when a child has more than one distinct parent, when there is a cycle, or when any linkage points to a retired entity. On pass it reports node count, edge count and share synthesized.

Practice: build the five level hierarchy by rolling up from the outlet, each parent chosen by confidence weighted vote over outlets that link to the same reference, keep the vote evidence in the ledger. If two outlets vote for different parents for the same child, flag the conflict and route to a steward. List synthesized nodes in synthesized and count them separately. Remove retired entities from linkages before the check. Keep the hierarchy snapshot at the same time as the reference snapshot, and check that every edge parent exists in the snapshot. A downstream system in SAP should receive only the checked snapshot and should reject any load where M29 fails.

## What comes next

Lifecycle measures after go live are not in this release, but declare baselines before targets. After promotion measure unmerge rate, duplicate at entry rate, churn, and override supersede rate. Each needs a baseline window declared before its target, for example four weeks of live operation before a target is set. Do not set a target from the first matching run. Keep these series beside cost per thousand pairs so a quality gain that doubles cost is visible.

## What this toolkit does not do

It does not match records, it does not decide release or acceptance, it does not replace stewards. It checks the claims that a matching run makes about its own results, and it prints PASS, FAIL or NO-DATA per gate. A person decides. A PASS means the evidence was present and supported the claim, a FAIL means the evidence contradicted the claim, a NO-DATA means the evidence was absent. Only FAIL decides the exit code, NO-DATA tells you what to supply next. Use python3 products/brotherds/bds.py check claim.json to run the gates and route the result to the steward who owns the decision.

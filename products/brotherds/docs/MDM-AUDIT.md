# Auditing a matching run

This tool audits a finished matching run before a decision. It checks that reported counts add up and match the exported results table, that each pathway and the verifier were measured with a labelled sample, that collisions and shared keys were reviewed, and that segment and hierarchy claims hold. It does not prove the master is correct, a similarity score is not a precision and a share reported as MATCH is not a recall. The tool audits a matcher, it is not a matcher.

## The seven questions a matching report must answer

1. No sampled precision for any pathway, similarity distributions were shown as quality. M25 catches this, it fails when precision_basis is similarity and requires a labelled sample for every material pathway.
2. No recall, the unmatched share mixes reference coverage gaps with matcher misses. M28 catches this, it requires a labelled sample of unmatched records that splits reference absent from matchable missed and then bounds recall.
3. No check that one reference record received at most one source record. M24 catches this, it reads collisions from the audit and fails when reference_ids_with_multiple_sources is greater than zero without review.
4. A shared phone number was treated as proof although headquarters numbers repeat across many outlets. M24 catches this, it reads key_hubs for key_phone and fails when matched rows rest on a hub without review.
5. An LLM verifier with no measured precision on what it confirmed and no measured error on what it rejected. M26 catches this, it requires at least 30 labelled confirmations and 30 sampled rejections for an llm or model verifier and checks the Wilson interval.
6. The report counts disagree with each other and with the table. M23 catches this, it recomputes every sum and percent and compares reported counts to computed counts from the table.
7. Per chain rates were given without intervals so a headline could hide a gap. M27 catches this, it computes Wilson intervals per segment and a Newcombe hybrid interval for each segment against the rest with a Bonferroni correction.

## The results table

One row per source record. UTF-8, header row, comma separated, read with csv.DictReader. Contract frozen 2026-09-12.

Required columns:

* source_id, identifier for the source record. Duplicate source_id is a reconciliation problem.
* reference_id, identifier for the matched reference record, empty for NO_MATCH rows. A MATCH row with empty reference_id is a problem, a NO_MATCH row with a reference_id is a problem.
* pathway, free text. For MATCH it names how the row was matched, for example key_phone, vector_auto, llm_verified. For NO_MATCH it names why, for example no_candidate or rejected_by_verifier.
* status, MATCH or NO_MATCH, case insensitive on read and stored upper case. Any other value is a problem.
* score, float in 0 to 1 inclusive or empty. Outside the range or not a number is a problem.

Optional columns that enable extra checks:

* verifier, CONFIRMED, REJECTED, SKIPPED or empty.
* segment, grouping such as parent account or chain.
* key_phone, raw phone text. Normalized by converting full width digits with NFKC then keeping digits only, empty result skipped. A hub is a normalized key shared by at least hub_min source rows.
* hit_a and hit_b, 0 or 1, whether two independent methods each found a match.
* reference_snapshot, model_version, normalizer_version.

If a required column is missing the library raises ValueError naming it, the CLI prints NO-DATA with the reason and exits 2.

Audit output from mdm_audit.audit(rows, hub_min=3):

* n_rows, total row count.
* reconciliation, with status_counts for MATCH and NO_MATCH, pathway_counts over MATCH rows, no_match_reasons over NO_MATCH rows, verifier_counts, problems list, consistent true only when problems is empty. Problems include duplicate source_id, bad status, bad score, MATCH with empty reference_id, NO_MATCH with a reference_id, and status counts that do not sum to n_rows.
* collisions, with reference_ids_with_multiple_sources, rows_in_collisions, top ten pairs of reference_id and count sorted by count descending then id ascending.
* key_hubs, for key key_phone with hub_min, hubs, rows_on_hubs, matched_rows_on_hubs, top ten hubs, or state NO-DATA when the column is absent.
* pathway_band, per MATCH pathway counts in lt_0.80, 0.80_0.90, 0.90_0.95, ge_0.95 and no_score.
* segments, list of segment, n, matched, rate, lo, hi with Wilson 95 percent, sorted by n descending then segment ascending, or empty list when column absent.
* capture_recapture, when hit_a or hit_b present with m greater than 0, state EXPLORATORY, n1, n2, m, n_hat equal to (n1 plus 1) times (n2 plus 1) divided by (m plus 1) minus 1, and a note that positively dependent methods make the estimate overstate recall and that it is never a gate. Otherwise state NO-DATA.
* provenance, present and missing among reference_snapshot, model_version, normalizer_version, present means at least one non empty value.
* input_sha256, hex digest of file bytes when run from a path with audit_path, else null. audit_path reads utf-8-sig.

## Commands

```shell
python3 products/brotherds/mdm_audit.py audit results.csv --plan plan.csv
python3 products/brotherds/mdm_audit.py evaluate plan-labelled.csv --results results.csv
python3 products/brotherds/bds.py check claim.json
python3 products/brotherds/examples/mdm_archetype.py --out demo
```

The first command audits the results table and writes a stratified review plan. The second evaluates a labelled plan and when given results.csv resolves populations against the full table. The third checks a claim file and runs gates M23 through M30, printing PASS, FAIL or NO-DATA per gate. The fourth creates a small synthetic demo with results, claim and plan.

## The review plan and how to label it

Strata are every MATCH pathway crossed with the four score bands lt_0.80, 0.80_0.90, 0.90_0.95 and ge_0.95, empty strata skipped, plus one stratum per NO_MATCH reason named unmatched:reason.

Sample size per stratum is the minimum of population and the maximum of min_n and the minimum of max_n and ceil of 1.96 squared times 0.25 divided by margin squared. Defaults are margin 0.10, min_n 10, max_n 400 and seed 7, so a large stratum samples about 96 rows.

Selection is deterministic. Each stratum draws with its own seed derived from the plan seed and the stratum name, so strata are independent. Rows in a stratum are sorted by source_id, sampled with random.Random(stratum_seed), then chosen rows are sorted by source_id. Same inputs and seed give byte identical output. Reusing the same plan seed across repeated audits repeats the same sample positions; choose a new seed for every audit and record it in the claim.

Columns in order are plan_id as P0001, P0002 and so on, stratum, source_id, reference_id, pathway, score, verifier, stratum_population, stratum_sampled, label.

Label is empty for labellers to fill, empty rows are excluded except from unlabelled count, any label other than 0 or 1 raises ValueError naming plan_id. In MATCH strata label 1 means the link is correct and 0 means it is not. In unmatched strata label 1 means an exhaustive search finds a correct reference record that the matcher missed, and 0 means no reference record exists at all, a coverage gap.

Evaluation returns strata with population, sampled, labelled and positive, pathway_review weighted over score-band strata per pathway, verifier with confirmed_review for CONFIRMED and rejected_review for REJECTED, and unmatched_review weighted over the unmatched strata by their populations with population, sampled, reference_absent counting label 0 and matchable_missed counting label 1. The unmatched share is weighted because strata are sampled at different rates, so pooling biases it; pathway precision likewise uses its score-band strata weighted.

## The gates

Each gate reads A equal to claim master_data audit and E equal to claim master_data evaluation. Verdicts are exactly PASS, FAIL or NO-DATA. A malformed field makes the gate FAIL naming the field, never an exception.

### M23.report_reconciliation

What it asks: do reported counts add up and match the results table when available.

When it says NO-DATA: when A has no reported, detail is no reported figures: a match report must state its counts to be checked.

When it FAILs: n_input must be non negative int and status_counts a dict of non negative ints else FAIL naming the field. Sum of status_counts not equal n_input is status counts sum to X, not n_input Y. pathway_counts when present must be dict of non negative ints else FAIL naming pathway_counts, sum not equal MATCH count is pathway counts sum to X, not the MATCH count Y. percents when present must be dict else FAIL naming percents, each entry must have value, numerator and denominator, denominator 0 or missing keys is problem naming the percent, and abs(value minus 100 times numerator divided by denominator) greater than 0.05 is percent name says V but N/D is W with W to two decimals. When computed is present, computed n_rows not equal n_input is the results table has X rows, the report says Y, each status and pathway count mismatch is reported similarly, and computed consistent false is the results table itself is inconsistent followed by first three problems. Any problem makes FAIL with N problem(s) joined by semicolon.

When it passes: detail is every count sums and every percent recomputes (K check(s)) where K counts checks performed.

Claim fields it reads: A reported with n_input, status_counts, pathway_counts, percents, and optionally A computed with n_rows and reconciliation status_counts, pathway_counts and consistent.

### M24.assignment_uniqueness

What it asks: does one reference record receive at most one source record and do matched rows rest on a shared key.

When it says NO-DATA: when A has no computed, detail is no computed audit: run mdm-audit on the results table. When the computed audit has no collision counts, it says NO-DATA instead of passing: no collision counts: run mdm-audit on the results table. A shared key check with state NO-DATA adds a note but is not a fail alone.

When it FAILs: one_to_one defaults true. When one_to_one true and collisions reference_ids_with_multiple_sources greater than 0 and collisions_reviewed not true, problem is X reference record(s) received more than one source record (Y rows); a location-anchored master allows one; review them or declare one_to_one false. When key_hubs matched_rows_on_hubs greater than 0 and hubs_reviewed not true, problem is X matched row(s) rest on a shared key used by at least H source records (Z hub(s)); a shared key is not proof of identity; review them. Problems joined by semicolon cause FAIL.

When it passes: detail says one reference record per source or one_to_one declared false, and adds shared-key check NO-DATA: no key column when key_hubs state is NO-DATA.

Claim fields it reads: A computed with collisions and key_hubs, plus A one_to_one, collisions_reviewed, hubs_reviewed.

### M25.pathway_precision

What it asks: does every material pathway have a labelled sample that supports its claimed precision.

When it says NO-DATA: when neither computed pathway_counts nor reported pathway_counts exists or total is 0.

When it FAILs: if precision_basis is similarity, detail is a similarity score distribution is not a precision: label a sample of each pathway. Each pathway_review entry must have non negative ints with positive less than or equal sampled and sampled less than or equal population, else FAIL naming pathway and field. Material pathways are those with count divided by total greater than or equal 0.05. The 5 percent rule applies to the combined share of unreviewed pathways, so FAIL when pathway(s) carrying P of matches have no labelled sample: names where P is combined share to three decimals and names sorted, absent review or sampled 0. For each reviewed pathway with sampled at least 1, compute weighted Wilson lo hi from positive and sampled over score-band strata. For each pathway in claimed_pathway_precision, no review is claimed precision for p has no labelled sample, claimed greater than hi plus 1e-12 is claimed precision for p is C but its sample supports at most H. For overall claimed_precision in E, if it exists but no reviewed pathway has a labelled sample behind it, FAIL overall claimed precision C has no labelled sample behind it. With at least one reviewed pathway, build weighted strata from reviewed pathways present in counts and compute stratified estimate, claimed greater than hi plus 1e-12 is overall claimed precision C exceeds H.

When it passes: lists each reviewed pathway as p k/n [lo, hi] joined by semicolon, rates to three decimals, weighted over score-band strata.

Claim fields it reads: A precision_basis, A computed or reported pathway_counts, A pathway_review, A claimed_pathway_precision and E claimed_precision.

### M26.verifier_evidence

What it asks: was confirmation precision measured and were rejections sampled enough.

When it says NO-DATA: when A has no verifier, detail is no verifier declared. When kind is human or rule and neither confirmed_review nor rejected_review exists, detail is no labelled sample of the verifier's decisions.

When it FAILs: kind must be llm, model, human or rule else FAIL unknown verifier kind. confirmed_review and rejected_review when present must be population, sampled, positive of non negative ints with positive less than or equal sampled and sampled less than or equal population, else FAIL naming it, including when sample exceeds its population. For kind llm or model, confirmed absent or sampled less than 30 is an llm or model verifier needs at least 30 human-labelled confirmations to state its precision, rejected absent or sampled less than 30 is its rejections were never sampled enough (need 30); N record(s) were discarded unexamined where N is population or an unknown number of. If claimed_precision is present but confirmed_review has no labelled confirmation sample, FAIL claimed verifier precision C has no labelled confirmation sample. When confirmed has sampled at least 1, Wilson lo hi is computed, claimed_precision above hi plus 1e-12 is claimed verifier precision C exceeds H. Details include when rejected has sampled at least 1, false omission rate r [lo, hi], and miss rate as k/s equals m [lo, hi] when known_match_frame with sampled and rejected is present, else miss rate NO-DATA: it needs a frame of known true matches, not a sample of rejections.

Claim fields it reads: A verifier with kind, confirmed_review, rejected_review, known_match_frame and claimed_precision.

### M27.segment_disparity

What it asks: does a headline rate hide a material gap for a segment.

When it says NO-DATA: when neither A segments nor computed segments exists or list is empty, or fewer than two segments have at least min_n records.

When it FAILs: each entry needs string segment and ints n at least 0 and matched between 0 and n, else FAIL naming segment and field n or matched. gap is material_gap default 0.10, min_n default 30. Eligible are segments with n at least min_n, small are the rest. k is number eligible, z is NormalDist inv_cdf of 1 minus 0.05 divided by (2 times k), Bonferroni over k. For each eligible segment, m1 equals matched, n1 equals n, m2 equals sum matched over all minus m1, n2 equals sum n over all minus n1, skip when n2 is 0. p1 equals m1 divided by n1, p2 equals m2 divided by n2, Wilson intervals l1 u1 and l2 u2 at z. Newcombe hybrid interval for d equals p1 minus p2 is lower equals d minus sqrt of (p1 minus l1) squared plus (u2 minus p2) squared, upper equals d plus sqrt of (u1 minus p1) squared plus (p2 minus l2) squared. below are eligible with upper less than minus gap, above are eligible with lower greater than gap. Detail contains k eligible segment(s), Bonferroni z to three decimals, below names or none, above names or none, and N small segment(s) NO-DATA (under min_n) when small non empty. FAIL when below non empty and segment_disclosure not true, prefixed with a single headline rate hides segment(s) materially below the rest.

Claim fields it reads: A segments or A computed segments with segment, n and matched, plus A segment_disclosure, material_gap and min_segment_n.

### M28.coverage_split

What it asks: among unmatched records what share is a coverage gap and what share is a miss, and what does that imply for recall. Recall is a range, not a point: wrong matches can hide further misses. The conservative recall range runs from precision_lo x M / (M + U x q_hi) to M / (M + U x q_lo); it covered the true recall in 59 of 60 synthetic runs. An honest claim states the lower end.

When it says NO-DATA: when A has no unmatched_review, or sampled is 0, or E claimed_recall exists but matched count M is not available.

When it FAILs: all four of population, sampled, reference_absent and matchable_missed must be non negative ints, else FAIL naming field. reference_absent plus matchable_missed not equal sampled is every sampled unmatched record must be labelled reference absent or matchable (X plus Y not equal S). Compute q equals matchable_missed divided by sampled with Wilson q_lo q_hi, weighted over unmatched strata by their populations when strata are present, and a equals reference_absent divided by sampled similarly weighted; the weighted missed share must be used when strata are present because pooling biases it. M is MATCH count from computed reconciliation status_counts MATCH when present else reported status_counts MATCH, U is unmatched population. When M exists, the recall range is lower equals precision_lo times M divided by (M plus U times q_hi) to upper equals M divided by (M plus U times q_lo), using weighted q and precision_lo from weighted pathway precision when available else 1, bounded at 0 when nothing matched. If claimed recall greater than upper plus 1e-12, FAIL claimed recall C exceeds B, the most the unmatched sample allows where B is upper bound.

When it passes: detail is reference absent a [lo, hi]; matchable but missed q [lo, hi] weighted when strata present; recall range [lower, upper] with upper bound B, Wilson 95 percent, bounded at 0 when nothing matched.

Claim fields it reads: A unmatched_review with population, sampled, reference_absent and matchable_missed, plus strata for unmatched when present, plus A computed or reported MATCH count and E claimed_recall and precision_lo.

### M29.hierarchy_integrity

What it asks: is the rolled up hierarchy a clean tree with one parent per node, no cycles and no linkages to retired entities.

When it says NO-DATA: when A has no hierarchy.

When it FAILs: edges must be list of two string lists child parent, else FAIL naming edges, retired and synthesized lists of strings default empty, linkages list of two string lists default empty. Problems are each child with more than one distinct parent: node c has more than one parent (p1, p2) with parents sorted, any cycle in child to parent graph: cycle through nodes sorted and comma separated, each cycle node set once, linkages whose entity second element is in retired: N linkage(s) point to retired entities; must be zero. Problems joined by semicolon cause FAIL.

When it passes: N node(s), E edge(s), S synthesized (share to three decimals) where nodes are all ids in edges.

Claim fields it reads: A hierarchy with edges, retired, linkages and synthesized.

### M30.agreement_bound

What it asks: do model and human agree enough that model labels can be trusted, using a chance corrected measure with a lower bound.

When it says NO-DATA: when A has no labeller_confusion.

When it FAILs: a, b, c, d as both_match, model_only, human_only and both_nonmatch must be non negative ints, else FAIL naming field. n equals sum, n less than 50 is fewer than 50 overlapping labels (n). pe equals 1 is kappa undefined: both labellers gave one constant label. kappa equals (po minus pe) divided by (1 minus pe), se equals sqrt of po times (1 minus po) divided by (n times (1 minus pe) squared), lower equals kappa minus 1.645 times se for a one-sided 95 percent lower bound with z equals 1.645. mk is min_kappa_lower default 0.6. FAIL when lower less than mk, detail is kappa K with one-sided 95 percent lower bound L is below mk; positive agreement PA, negative agreement NA.

When it passes: kappa K, one-sided lower bound L, positive agreement PA, negative agreement NA, n equals N.

Claim fields it reads: A labeller_confusion with both_match, model_only, human_only, both_nonmatch, and A min_kappa_lower.

## Statistical notes

Chapman capture-recapture is exploratory and never a gate because positively dependent methods make it overstate recall. A sample of verifier rejections estimates the false omission rate, not the miss rate, which needs a frame of known true matches. Segment disparity uses a Newcombe hybrid score interval with Bonferroni correction and a material gap declared in advance. The kappa gate uses the one-sided 95 percent lower bound with z equals 1.645, not the point estimate, and reports positive and negative agreement because kappa depends on prevalence. The recall bound in M28 is a recall range from precision_lo times M divided by (M plus U times q_hi) to M divided by (M plus U times q_lo), weighted when strata are present and bounded at 0 when nothing matched; it covered the true recall in 59 of 60 synthetic runs because true positives cannot exceed matches and wrong matches can hide further misses.

## What NO-DATA means here

NO-DATA is never a pass and never a block, only FAIL decides the exit code. NO-DATA means the claim did not give the tool enough to check the question. The CLI prints NO-DATA with a reason and exits 2 when a required CSV column is missing. For gates, NO-DATA tells the reader what is missing so the team can supply it. A gate that says NO-DATA has not approved the claim, it has said the evidence is absent. Supply the missing table, sample or field and run the check again.

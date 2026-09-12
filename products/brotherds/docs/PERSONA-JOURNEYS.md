# Five people, one claim ledger

## How to run the journeys

Run the full suite from the repo root with either:

* `python3 products/brotherds/tests/test_persona_journeys.py`
* `sh products/brotherds/tests/run_all.sh`

They run in a temporary home and a temporary Vault and never touch the real ones. Each test creates a temp dir like `bds-persona-XXXX`, sets `HOME` to `tmp/home`, sets `BROTHERDS_VAULT` to `tmp/vault`, sets `BROTHERDS_VAULT_TOOLS` to `products/brothermode/tools`, sets `BROTHERDS_RECALL_TIMEOUT_S` to `60`, and runs `bm_vault.py index --vault $BROTHERDS_VAULT` before any claim is checked. Real `~/.claude/bm_vault_index.sqlite3` is left at its original mtime. All claims used in these journeys are copies, so seeding and resetting preserves the ledger seen file `.bds-lessons-seen.json` beside the claims.

## B1, customer master PM: derive the numbers, then check

Situation: the customer master PM has a clerical review sample in `review-sample-mdm.csv` and a draft claim file. She needs numbers that weight back to the population before signing off on deduplication. The next decision will merge production customer records.

Exact commands in order, as in the test:

1. `python3 products/brotherds/bds.py mdm-eval review products/brotherds/examples/review-sample-mdm.csv --merge-threshold 0.8`
2. copy the output `strata` array into `master_data.evaluation.review_strata` of `example-mdm-evaluated.json`, set id to `B1-DEDUP-001`, write to `claims/b1/B1-DEDUP-001.json`
3. `python3 products/brotherds/bds.py check claims/b1/B1-DEDUP-001.json`
4. `python3 products/brotherds/bds.py receipt claims/b1/B1-DEDUP-001.json claims/b1/B1-receipt.md`

What they see:

* Step 1 exits 0 and prints JSON with `strata`, `precision_above`, `recall`, `merge_threshold 0.8`, and `n_rows`. The test asserts `len(strata) == 4` and keys present.
* Step 3 prints gates and `VERDICT` not `FAIL`. No gate is `FAIL`. Specifically `M7.precision_evidence` is `PASS`, `M8.recall_evidence` is `PASS`, `M19.labeller_agreement` is `PASS`, and every gate in `M7.` to `M19.` is `PASS`.
* Step 4 exits 0 and writes a receipt that contains `Vault context:`.

What decision it protects: merging the customer master at threshold 0.8 with a precision and recall that the stratified review can actually support. By deriving `strata` first, the PM avoids quoting a convenience benchmark that looks optimistic while the population precision is lower.

## B3, audit reviewer: catch the hurried claim, recall the lesson, check the normalization

Situation: the audit reviewer receives an overclaim that asserts high precision and recall from a review sampled only above threshold, uses a convenience gold frame, and skips Japanese normalization steps itaiji and small ke. She must catch it, show the Vault lesson, and prove the normalization fix.

Exact commands in order, as in the test:

1. `python3 products/brotherds/bds.py check products/brotherds/examples/example-mdm-overclaim.json`
2. `python3 products/brotherds/bds.py receipt products/brotherds/examples/example-mdm-overclaim.json claims/b3a/B3a-receipt.md`
3. `python3 products/brotherds/bds.py check claims/b3b/B3-JA-001.json` where the claim has `master_data.merge_threshold 0.8`, `master_data.normalization.locale ja`, `steps ["nfkc", "space"]`
4. `python3 products/brotherds/bds.py check claims/b3b/B3-JA-002.json` where steps are `["nfkc", "space", "long_vowel", "itaiji", "small_ke", "corporate"]` and probes include `company [["株式会社ｱｲｳ商事", "(株)アイウ商事"]]` and `phone [["０３－１２３４－５６７８", "03-1234-5678"]]`

What they see:

* Step 1 prints `VERDICT FAIL`. `M8.recall_evidence` is `FAIL` and its detail contains `below the merge threshold`. Gates `M7.`, `M9.`, `M16.`, `M18.`, `M19.` are `FAIL` as well. The test asserts at least one gate per prefix `M7.`, `M9.`, `M16.`, `M18.`, `M19.` exists and is `FAIL`.
* Step 2 receipt contains `lessons surfaced` and title `recall above threshold` drawn from `40-Failures/recall-above-threshold.md` via `BROTHERDS_RECALL_TIMEOUT_S` 60 recall.
* Step 3 prints `M22.normalization` `FAIL` with detail containing `itaiji`.
* Step 4 prints `M22.normalization` `PASS` with detail listing steps and probe counts.

What decision it protects: stopping a merge that would have claimed recall without sampling below threshold, and stopping a Japanese merge that would have split `株式会社ｱｲｳ商事` and `(株)アイウ商事` into two customers because width, long vowel, itaiji, small ke, and corporate stripping were not declared. The second check shows the exact fix that makes the keys equal under `company_key`.

## A2, finance owner: forecasts scored with the weighted interval score

Situation: the finance owner holds six quantile forecast claims of form median plus 0.1 and 0.9 bands and waits for actuals to arrive. He needs a proper score that rewards sharp and calibrated intervals, not just hit count.

Exact commands in order, as in the test:

1. For `i` in 1 to 6: `python3 products/brotherds/bds.py score claims/a2/A2-FC-i.json <actual> "finance owner" "2026-09-30"` where actuals are `470 500 530 555 700 480` and each claim is a clone of `example-forecast-quantiles.json` with id `A2-FC-i`
2. `python3 products/brotherds/bds.py ledger claims/a2`

What they see:

* Each `score` exits 0, prints a line containing `  wis`, and writes back `review.result.wis` numeric and `review.result.band_hit` bool. For example with quantiles `0.1 80 0.5 100 0.9 130` and `y 100` wis 3.333, with `y 200` wis 83.333 plus dispersion 3.333 and underprediction 80.
* The run where actual is `700` prints `OUTCOME MISSED` and `lesson candidate: OK` and creates a file starting with `missed` in `vault/00-Inbox`.
* The `ledger` output contains `0.1-0.9 band coverage:` and `over 6` indicating coverage is over confident across 6 resolved claims when measured against nominal 0.8.

What decision it protects: tying a budget or inventory decision to forecast sharpness and calibration. Underprediction penalty is visible as a separate component, so a forecast that looks close on median but misses the interval tail is penalized and a lesson is proposed.

## A1, founder: the ledger turns habits into lessons

Situation: the founder sees habits repeating across claims. Two overclaims fail the same gate while one evaluated claim passes. She wants the ledger to turn that pattern into a single lesson candidate once, then stay quiet on rerun.

Exact commands in order, as in the test:

1. Seed `claims/a1` with `OC-1.json` and `OC-2.json` both clones of `example-mdm-overclaim.json` and `evaluated.json` clone of `example-mdm-evaluated.json`
2. `python3 products/brotherds/bds.py ledger claims/a1 --propose-lessons`
3. Reseed the same three files into `claims/a1` again, preserving `.bds-lessons-seen.json`
4. `python3 products/brotherds/bds.py ledger claims/a1 --propose-lessons`

What they see:

* Step 2 prints `RECURRING GATE FAILURES` and a line containing `M8.recall_evidence` and `2 claims`. It also prints at least 5 lines with `lesson candidate: OK id=` and writes new files to `vault/00-Inbox`, including `recurring-fail-m8-recall-evidence`.
* Step 4 prints `lesson candidates: none new` and does not write new inbox files. The count in `00-Inbox` stays the same. The seen file beside `claims/a1` carries the memory so re proposal is idempotent, and a person still must decide whether it becomes a rule.

What decision it protects: turning a repeated failure such as recall without below threshold samples into shared memory instead of tribal knowledge. The founder gets a proposal that names the gate, the share, the claim ids, and guidance such as `Sample below the merge threshold before you claim recall`, with no duplicate inbox noise.

## B2, warehouse lead: a reconciled pipeline claim

Situation: the warehouse lead presents a fully reconciled pipeline claim where data quality checks, lineage, and grain have already been addressed. The pipeline is ready for the next load.

Exact commands in order, as in the test:

1. `python3 products/brotherds/bds.py check products/brotherds/examples/example-pipeline-reconciled.json`

What they see:

* The command exits 0 and prints gates `P1.` through `P8.` each as `PASS`. The test asserts for each `i` in 1 to 8, at least one gate starts with `P{i}.` and every such gate is `PASS`. No gate is `FAIL`. This holds when freshness, volume, schema, distribution, and lineage are declared for the tables.

What decision it protects: letting the warehouse pipeline run without holding the load for an unverified claim. The check confirms the upstream promises with the same NO-DATA philosophy used for master data gates, so missing evidence would have been `NO-DATA` rather than a silent pass.

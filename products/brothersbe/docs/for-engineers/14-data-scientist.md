# Data scientist

One artifact walked end to end: promoting a challenger renewal-risk model over
an aging baseline heuristic, with the promotion blocked until three rules are
proven from a pinned holdout. The full dossier is in
`examples/model-promotion/`. The centrepiece is `05-data-model.md`, which is
the artifact the `datamodel` check reads, plus the `numbers` gate on the
challenger's holdout AUC and the `proof` gate on the promotion rules
`08-behaviour.md` states.

Every block of output below was produced by running the command above it from
the clone root, with the dossier copied to `design/model-promotion`.

## Shortest path from a design doc to a verdict

```
mkdir -p design/model-promotion
python3 tools/sbe_intake.py design/model-promotion
# write the artifacts, including 05-data-model.md and numbers-manifest.json
python3 tools/sbe_design.py design/model-promotion      # design checks
python3 tools/sbe_gate.py design/model-promotion         # numbers, ran, proof
```

## Step 1: production serving means T3

```
$ printf 'additive\ny\nn\ny\nmany\nfeature\nthe head of data science\n...\n...\n' | python3 tools/sbe_intake.py design/model-promotion
Does this change a data model, an API contract, or a file interface others depend on? (no/additive/breaking; additive means nothing that exists today has to change) Does it cross a service, system, or team boundary? (y/n) Is it reversible in under an hour? (y/n) Does it touch money, partner data, personal data, or production state? (y/n) How many downstream consumers break if it is wrong? (none/some/many) Is this a feature or a defect? (feature/defect) Who wants this? (a named human) What outcome is desired? What is the value hypothesis (why is this worth doing)? tier T3 (artifacts required: 01, 02, 03, 04, 05, 06, 07, 08) written to design/model-promotion/00-intake.json
To override this tier, edit that file and set all three fields: "tier" (the tier you are moving to), "override" (the same tier, declaring the move), and "override_reason" (at least 3 words and 12 characters). A move with any of the three missing or disagreeing FAILs the design check as an edit rather than an override.
```

Additive contract, crosses the boundary between the feature store, training,
the registry and the serving system, not reversible in an hour once a bad
model is scoring live customers, touches production state, and many
downstream retention decisions break if the promoted model is wrong. That is
T3, and it is why a promotion is not a fire-and-forget training run: it owes
every artifact through the behaviour table.

## Step 2: the data model artifact and the failing run

`05-data-model.md` carries conceptual entities, relationships with
cardinality, attribute roles, historization, source systems with failover,
and the physical layer, exactly as it does for a warehouse mart. The check
reads two things hard: every entity names its system of record, and every
relationship line carries a cardinality.

The first pass declared five entities. Four named their system of record.
`TrainingRun` did not, because the experiment tracker felt too obvious to
write down.

```
$ python3 tools/sbe_design.py datamodel design/model-promotion
BROTHERSBE DESIGN CHECKS  (advisory unless --strict; NO-DATA is never a pass; WAIVED is not a pass either)
  scope      -        read 1 dossier under design/model-promotion (.); 0 of 0 director(y/ies) directly under design/model-promotion contributed no dossier
  dossier: . (under design/model-promotion)
  datamodel  FAIL     entity 'TrainingRun' does not name the system that owns it (accepted as any of: system of record, system of truth, source of truth, book of record, authoritative source, mastered by, owned by, owner, sor, as `<phrase>: the OMS` on the bullet, or as a table column headed with one of them); examined . under design/model-promotion [severity: gate]
```

The fix is one clause on one bullet:

```
- TrainingRun: one training execution with its code version, hyperparameters, and random seed; system of record: the experiment tracker.
```

```
$ python3 tools/sbe_design.py datamodel design/model-promotion
BROTHERSBE DESIGN CHECKS  (advisory unless --strict; NO-DATA is never a pass; WAIVED is not a pass either)
  scope      -        read 1 dossier under design/model-promotion (.); 0 of 0 director(y/ies) directly under design/model-promotion contributed no dossier
  dossier: . (under design/model-promotion)
  datamodel  PASS     5 entities, each with a system of record; 4 relationship line(s) read, each carrying cardinality; examined . under design/model-promotion [severity: gate]
```

Nothing about whether the split is right, whether the challenger's features
leak the label, or whether the seed was actually honoured. Five entities, each
with a system of record; four relationship lines, each carrying cardinality.

## Step 3: the numbers gate, which is the one built for you

The figure here is not a revenue total, it is an evaluation metric: the
challenger's holdout AUC. It gets the same treatment a dollar figure gets,
because a metric nobody can re-derive is exactly as undefendable as a revenue
number nobody can reconcile. `numbers-manifest.json`:

```json
{"figures": [{
  "label": "model_promotion_challenger_holdout_auc",
  "snapshot_id": "snap_2026_02_14",
  "query": "python3 compute_auc_rank.py holdout.csv  # Mann-Whitney rank-sum",
  "second_derivation": "python3 compute_auc_trapezoid.py holdout.csv  # trapezoidal ROC integration",
  "rerun": {"ran": true, "primary": 0.765625, "secondary": 0.765625}
}]}
```

`compute_auc_rank.py` ranks every holdout row by score and sums the positive
class's ranks. `compute_auc_trapezoid.py` never ranks anything: it sweeps
every distinct score as a threshold, plots the ROC curve, and integrates it
with the trapezoid rule. Two different pieces of arithmetic computing the same
statistic, which is what "independent" means here, not two people typing the
same formula twice.

### Failing run one: the second derivation is the first one again

The tempting shortcut is calling the same script twice with a comment saying
when it was rerun.

```
$ python3 tools/sbe_gate.py numbers design/model-promotion
BROTHERSBE HARD GATES  (advisory unless --strict; NO-DATA is never a pass; WAIVED is not a pass either)
  numbers   FAIL     model_promotion_challenger_holdout_auc: the second derivation is the first one again (it differs only in case, whitespace, comments or trailing punctuation, if at all), so nothing independent re-derived this figure [severity: gate]
```

It strips comments, case, whitespace and trailing punctuation before
comparing. Calling `compute_auc_rank.py` twice is not two derivations,
whatever the trailing comment says.

### Failing run two: the snapshot is a placeholder

With the genuinely independent trapezoidal script back in, but
`"snapshot_id": "TODO"`:

```
$ python3 tools/sbe_gate.py numbers design/model-promotion
BROTHERSBE HARD GATES  (advisory unless --strict; NO-DATA is never a pass; WAIVED is not a pass either)
  numbers   FAIL     model_promotion_challenger_holdout_auc: no snapshot_id recorded ('TODO'); a live warehouse drifts, so pin the read. A placeholder is not a pin [severity: gate]
```

The feature store rebuilds daily, so an unpinned metric is a metric about a
holdout that no longer exists by the time anyone reads the number again.
`TODO` parses as valid JSON and still gets refused by name.

### The passing run

```
$ python3 tools/sbe_gate.py numbers design/model-promotion
BROTHERSBE HARD GATES  (advisory unless --strict; NO-DATA is never a pass; WAIVED is not a pass either)
  numbers   PASS     1 figure(s) each pinned to a snapshot, with a second derivation whose text differs beyond case, whitespace and comments, re-run to zero drift; read 1 numbers-manifest.json under design/model-promotion (numbers-manifest.json); 0 of 0 director(y/ies) directly under design/model-promotion contributed no numbers-manifest.json [severity: gate]
```

Read the PASS sentence exactly. It proves the two scripts computed the same
number on this rerun. It does not know one is a rank-sum and the other a ROC
integral; renaming a variable in one would have passed just as green.

## Step 4: the promotion rules, and the proof gate that checks them against what actually ran

`08-behaviour.md` states the promotion rules as rows: the challenger must beat
the baseline by a fixed AUC margin, no customer may appear in both the
training set and the holdout, and the training run must record its seed. Each
row's Proof column backticks a check name. The `ran` gate reads
`ran-receipt.json` for those names; the `proof` gate refuses a row that cites
a check no receipt records.

```
$ python3 tools/sbe_gate.py design/model-promotion
BROTHERSBE HARD GATES  (advisory unless --strict; NO-DATA is never a pass; WAIVED is not a pass either)
  numbers   PASS     1 figure(s) each pinned to a snapshot, with a second derivation whose text differs beyond case, whitespace and comments, re-run to zero drift; read 1 numbers-manifest.json under design/model-promotion (numbers-manifest.json); 0 of 0 director(y/ies) directly under design/model-promotion contributed no numbers-manifest.json [severity: gate]
  migration NO-DATA  no migration in this change, or no migration-receipt.json; no migration-receipt.json read under design/model-promotion; 0 of 0 director(y/ies) directly under design/model-promotion contributed no migration-receipt.json [severity: gate]
  approval  NO-DATA  no APPROVAL file and no Approved-by trailer; if this change touches no money or partner path that is correct; no APPROVAL read under design/model-promotion; 0 of 0 director(y/ies) directly under design/model-promotion contributed no APPROVAL [severity: gate]
  ran       PASS     3 recorded check(s), each with a zero exit and a nonzero duration; 1 of 1 receipt(s) record no build-system producer (run `--strict --strict-producer` to make that block); read 1 ran-receipt.json under design/model-promotion (ran-receipt.json); 0 of 0 director(y/ies) directly under design/model-promotion contributed no ran-receipt.json [severity: gate]
  proof     PASS     3 cited check(s) across 5 behaviour row(s) are each recorded by a ran-receipt; 2 row(s) state their Proof as prose and cite no check in backticks, so nothing here was owed for them (B4, B5). This matches NAMES, not runs: a receipt entry naming a check satisfies the row whether or not the check did anything [severity: gate]
NOT EXAMINED: regression, cross-device, performance, UX. No gate here opens evidence for these classes, so the verdicts above say nothing about them (NO-DATA is never a pass).
```

The `proof` line says exactly what it checked: three cited checks, each
matched by name to a receipt entry with a zero exit and a measured duration.
Two rows (B4, the numbers gate already covers the metric's re-derivation; B5,
an audit-time read of an append-only record) state their proof as prose and
own nothing here, which the sentence reports rather than hides. `split_check.py`
is the one worth keeping close: it is the entity-overlap and cutoff-leakage
check most split changes never run at all, and it landed in `scripts/` the
same night this dossier did.

## What it catches that a human reviewer usually misses

- **An entity with no system of record.** For a training run this is the
  experiment tracker, not "wherever the notebook happened to log it."
- **A second derivation that is the first one typed again.** The most common
  way a reported metric gets faked, and it looks identical to a genuine rerun
  until the text is diffed.
- **An unpinned metric.** A feature store that rebuilds daily makes any
  unsnapshotted AUC a number about data that no longer exists.
- **A Proof column naming a check nobody ran.** A row can read as fully
  covered while citing a check name that exists nowhere in any receipt; this
  gate is the one that reads the receipt back, not just the row.
- **A promotion rule with no runnable check at all.** Reported as prose,
  never failed, so adding the first backticked check is visible progress
  rather than an all-or-nothing switch nobody flips.

## What it cannot judge, and hands back

- Whether the holdout genuinely represents production traffic, whether the
  feature set actually leaks the label through some column nobody thought to
  check, whether 0.05 AUC is the right margin for this decision. It reads
  that you declared these; it does not evaluate your model.
- Whether the two AUC derivations are genuinely independent statistics or two
  copies of the same idea with different variable names. Text difference
  only.
- Whether a check cited by name actually ran the thing its name claims.
  `_check_entry_problems` reads an exit code and a duration, not the process
  that produced them; `--strict-producer` raises that cost by one exported
  environment variable, and states plainly that it is not an attestation.
- Anything about your training code, your feature pipeline, or whether the
  challenger will still beat the baseline next quarter. It never trains or
  scores a model itself.

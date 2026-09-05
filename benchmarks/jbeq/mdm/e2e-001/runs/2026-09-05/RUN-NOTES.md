# Run notes, JBEQ-MDM E2E-001, 2026-09-05

What is in this directory: the artefacts of one real execution of the
scenario in the parent directory, plus the delivery receipt Brother's engine
minted over it, plus the code that produced them.

## How it was run

A throwaway git repository was created outside every real checkout, holding
only the scenario data (`data/`), the requirement, and `checks/`, the six
acceptance checks. The checks were committed BEFORE any of the work, and
each one was observed exiting 1 on the empty tree first, so every check in
the receipt failed before its unit and passed after it.

The scenario was then executed through the engine's own public entry point:

    python3 scripts/brother_run.py "<the outcome, in Japanese>" \
        --cwd <scenario repo> --runs-root <run root> --slots 2 --quiet

The engine decomposed the outcome into six units, claimed them in isolated
lanes, ran each unit's own check, integrated serially, and wrote the
receipt. The delivery report ended `verdicts: 6 PASS, 0 FAIL, 0 NO-DATA` at
exit 0.

## What the model seam was

The engine's two model commands (`DOOR_MODEL_CMD`, `MODEL_WORKER_CMD`) were
pointed at two small scripts, the documented seam that
`scripts/product_acceptance.py`, `scripts/fault_lab.py` and
`scripts/codex_smoke.py` already drive. This is stated plainly rather than
implied: the plan and the files came from the engineer, not from a headless
model session, because the lane that ran this may not open one. Everything
else in the run is the real engine: real claims, real isolated lanes, real
done_checks executed as shell commands, real serial integration, real
before-and-after discrimination, real receipt.

## What the engine caught that a hand run would not have

Three rounds, three findings, each one a defect in the work rather than in
the engine.

1. FIRST RUN, all six units refused. Every done_check pointed at a file its
   own unit was about to write, so each check crashed rather than failed,
   and the engine refuses a unit whose check cannot run. Fixed by committing
   the checks up front, which is also the better engineering.
2. SECOND RUN, U3 failed. `check_reconcile.py` asserted the reconciliation
   script was independent by searching its whole source for the string
   `mdm_transform`, and the script's own comment saying it does not import
   that module contained the string. Fixed by matching an import statement.
3. SECOND RUN, three NO-DATA verdicts. Each said a declared dependency was
   not exercised by the unit's check. All three were right, and two of the
   checks were genuinely too thin: `check_outputs.py` read `out/` without
   ever running the migration code, so it would have passed with the whole
   module reverted. It now re-runs the migration into a temporary directory
   and compares the seven output files byte for byte, which is the
   reproducibility property that matters anyway. The third NO-DATA was a
   dependency that should never have been declared, and the plan was
   corrected rather than the check padded.

## Checker output on this directory

    golden.csv: PASS
    links.csv: PASS
    mapping.json: PASS
    decisions.json: PASS
    reconciliation.json: PASS
    handover.ja.md: PASS
    critical integrity: PASS
    handover sections: PASS (11 of 11 present)
    jbeq-mdm e2e: PASS

Command: `python3 scripts/jbeq_e2e_check.py benchmarks/jbeq/mdm/e2e-001/runs/2026-09-05`, exit 0.

## What is honest to say about the two answer-key artefacts

`mapping.json` and `decisions.json` are emitted by the migration module from
`src/field_map.json` and `src/rule_book.json`, so they are outputs of code
that ran. They were nonetheless authored by the same engineer who wrote the
ground truth beside this scenario, so for THIS run those two artefact lines
are a consistency check. They become a real answer key for the next run, by
another agent or another model, which is what the scenario exists for.
`golden.csv`, `links.csv` and `reconciliation.json` are not in that
position: they are computed, and `reconciliation.json` is computed by a
second script that never imports the transformation.

## What is NO-DATA

- Token and cost figures in the receipt: the seam scripts are not the Claude
  CLI, so no usage was reported. The receipt says so field by field.
- Release and acceptance: both screens are recorded as not yet answered.
  Nobody accepted this delivery; the engine never fills that in.
- Performance, lock duration and volume: the fixture is eight customer rows.

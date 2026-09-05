"""The plan for the JBEQ-MDM E2E-001 run, handed to the engine's door.

This is NOT a model. It is the engineer's own decomposition, delivered
through the documented DOOR_MODEL_CMD seam (the same seam
scripts/product_acceptance.py, scripts/fault_lab.py and scripts/codex_smoke.py
already drive), because a builder lane may not open a headless model session.

Every done_check below names a script that is ALREADY COMMITTED in the target
repository before the run starts, so each one runs, fails, and then passes.
The first attempt at this plan pointed each check at a file the unit itself
was about to write, and the engine refused all six units because a check that
cannot run is not a check that failed.

A SECOND CORRECTION, after the run of 06:40 on 2026-09-05 returned three
NO-DATA verdicts. Each one said the same thing: a declared dependency that
the unit's check does not actually exercise. They were right, and the fix
was in this plan rather than in the checks. The reconciliation script is
independent of the migration module BY DESIGN, so U3 depends on nothing.
The outputs do not technically depend on the test file, so U4 depends on U1
alone. The handover quotes the reconciliation, not the golden records, so U6
depends on U5 alone. Padding a check until it touches a dependency nobody
needs is how a NO-DATA gets laundered into a PASS.
"""
import json
import sys

sys.stdin.read()

print(json.dumps([
    {
        "id": "U1",
        "objective": "write the migration module: normalization, crosswalk, "
                     "matching that links on a shared corporate number and "
                     "never merges a store level customer, survivorship, "
                     "effective dating, and the rule book it emits",
        "done_check": "python3 checks/check_module.py",
        "writes": ["src"],
        "deps": [],
    },
    {
        "id": "U2",
        "objective": "write the tests for the migration module, one per "
                     "sentence of the requirement",
        "done_check": "python3 checks/check_tests.py",
        "writes": ["tests"],
        "deps": ["U1"],
    },
    {
        "id": "U3",
        "objective": "write the independent reconciliation script, which "
                     "counts from the source files and the outputs without "
                     "importing the migration module",
        "done_check": "python3 checks/check_reconcile.py",
        "writes": ["recon"],
        "deps": [],
    },
    {
        "id": "U4",
        "objective": "run the migration with the contradictory row injected "
                     "and produce the golden records, the links, the rejects, "
                     "the carried transactions, the mapping and the decisions",
        "done_check": "python3 checks/check_outputs.py",
        "writes": ["out/golden.csv", "out/links.csv", "out/rejects.csv",
                   "out/transactions_out.csv", "out/source_customers.csv",
                   "out/mapping.json", "out/decisions.json"],
        "deps": ["U1"],
    },
    {
        "id": "U5",
        "objective": "reconcile the run independently and write the counts",
        "done_check": "python3 checks/check_reconciliation.py",
        "writes": ["out/reconciliation.json"],
        "deps": ["U3", "U4"],
    },
    {
        "id": "U6",
        "objective": "write the Japanese handover another engineer can "
                     "continue from",
        "done_check": "python3 checks/check_handover.py",
        "writes": ["out/handover.ja.md"],
        "deps": ["U5"],
    },
]))

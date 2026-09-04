# Worked dossiers

Five complete dossiers, one per role, used by the role files one directory up.
Every one of them passes all five design checks. Copy a directory as a starting
point rather than copying the shipped templates, which carry a marker that fails
the `placeholder` check until you delete it.

| Directory | Tier | Role file | Receipts it carries |
|---|---|---|---|
| `backend-idempotency/` | T2 | `10-backend-engineer.md` | `ran-receipt.json` |
| `data-warehouse/` | T3 | `11-data-engineer.md` | `numbers-manifest.json` |
| `infra-topology/` | T3 | `12-infrastructure-architect.md` | `APPROVAL` |
| `etl-job/` | T3 | `13-etl-builder.md` | `migration-receipt.json`, `ran-receipt.json` |
| `model-promotion/` | T3 | `14-data-scientist.md` | `numbers-manifest.json`, `ran-receipt.json` |

Check one from the BrotherSBE clone:

```
python3 tools/sbe_design.py <path>/examples/etl-job
python3 tools/sbe_gate.py <path>/examples/etl-job
```

Note that `infra-topology/` carries an `APPROVAL` file with no signed trailer, so
its approval gate FAILs on purpose. That is the failing example the infrastructure
file walks through.

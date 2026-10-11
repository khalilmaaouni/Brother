# Configuration reference

Public configuration should remain smaller than Brother's internal implementation surface.

## Repository hooks

For supported scoped installs, `.brother/config` can contain:

```text
hooks: on
```

or:

```text
hooks: off
```

Do not invent additional public keys unless runtime/tests implement them.

## Vault root

Current routing guidance recognizes sources including `BM_VAULT_ROOT`, `BROTHERMODE_VAULT`, and Brother-recorded configuration. No resolved root means unconfigured/NO-DATA, not a guessed directory.

## Advanced worker/decomposer controls

Current advanced paths may use `MODEL_WORKER_CMD` and `DOOR_MODEL_CMD`. These are integration controls, not the first-day interface.

## Fence mode

Some product paths expose advisory/enforced fencing. Exact variable names/initialization can change; production setup should use installed help/tests rather than old copied README paragraphs.

## Session start output

A session start is quiet by default. Setup lines, update notices, the recovery pointer after a context compaction, store health warnings and reconciliation rows that need action show in every mode; the rest is not printed. Set `BROTHER_VERBOSE_START=1` in the environment that launches your host to print the full start block. `BROTHERMODE_MAINTAINER=1` has the same effect. Only the exact value `1` turns it on.

## Rule

Safety/scope/evidence-affecting configuration gets one canonical entry here and is linked elsewhere rather than restated inconsistently.

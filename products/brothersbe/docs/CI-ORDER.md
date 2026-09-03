# The consumer CI order

The order a consuming repository's CI should run BrotherSBE's controls, now
that the full path from design to approved change exists. Every step names
the command that exists today; nothing here is aspirational, and every result
must bind to the same head commit: evidence, approval, or convergence from
another commit cannot satisfy the current change.

| # | Step | Command |
|---|---|---|
| 1 | install verification | `sh scripts/verify-install.sh` |
| 2 | dossier checks | `bin/sbe design <dossier> --strict` |
| 3 | impact reconciliation | `bin/sbe impact . --base <base> --intake <dossier>/00-intake.json --strict` |
| 4 | plan validation | `bin/sbe plan <dossier>` |
| 5 | task-scope validation | `bin/sbe work check <task-id>` per open task (the registry postcondition) |
| 6 | hard gates | `bin/sbe gate <dossier> --strict` |
| 7 | evidence verification | `bin/sbe evidence verify <receipt>` per receipt |
| 8 | convergence | `bin/sbe converge <dossier> --base <base> --head <head>` |
| 9 | GitHub approval verification | `bin/sbe pr verify <number> --repo <owner/name>` |
| 10 | final status | `bin/sbe status . --team` |

Recommended required check names, one per step where a platform wants named
checks: `BrotherSBE / dossier`, `impact`, `plan`, `gates`, `evidence`,
`convergence`, `approval`, `final-status`.

Two honest constraints. Step 9 needs a token with read permission on the
repository; without one it reports NO-DATA and exits nonzero, which is the
correct CI behavior for an approval nobody could verify (never wire it to
continue-on-error). And this order has run end to end only on this
repository's own fixtures: no external estate has executed it yet, and no
document may claim otherwise until one has.

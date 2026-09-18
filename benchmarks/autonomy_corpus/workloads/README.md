# Intentionally empty

This directory holds zero workload `.json` files on purpose. See
`../README.md` for the schema a workload file must satisfy and for why no
workload has been fabricated here: `docs/plan/ORCH-1020-WBS.json` unit
DOM-20.04 is explicit that a harness over empty fixtures must report
NO-DATA rather than a pass, which is exactly what
`python3 scripts/autonomy_corpus.py benchmarks/autonomy_corpus/workloads`
does today.

# Intentionally empty

This directory holds zero run fixtures on purpose. See `../README.md` for
what a qualifying run directory looks like and why none has been
fabricated here: `docs/plan/ORCH-1020-WBS.json` unit DOM-20.05 is explicit
that a harness over empty fixtures must report NO-DATA rather than a
pass, which is exactly what
`python3 scripts/endurance_classes.py benchmarks/endurance_classes/runs`
does today.

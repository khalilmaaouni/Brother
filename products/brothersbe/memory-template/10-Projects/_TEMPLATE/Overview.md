# Overview: <project name>

One paragraph: what this project is and who depends on it.

## Shape
- Kind: <backend service | warehouse + SQL | pipeline | data quality | infrastructure | performance>
- Repo(s): <paths or urls>
- Entry points: <the seams a change usually touches>

## Stack
- Languages / frameworks: <...>
- Data stores / warehouse: <engine, dataset, snapshot convention>
- Build: <exact command, copied verbatim from the repo>
- Tests: <exact command>
- CI gate: <what must be green to merge; note if sbe_gate.py --strict runs here>

## Invariants (the things that must stay true)
- <a rule a change must never break, and how it is checked>
- <the blast-radius line: what no agent may apply to production>

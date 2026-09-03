# <Project name>, documentation pack

What this is: the index of the fuller documentation pack, one level deeper
than `../ARCHITECTURE.md`. Who reads it: the founder checking whether the
project still does what it was meant to, and any engineer or AI session
implementing, reviewing, or auditing a change. This pack mirrors the
structure BrotherMode itself uses for its own documentation
(`docs/ba/` in the BrotherMode repository), so if you have used one, you can
navigate the other without relearning the shape.

## What is in this pack, and when to read it

| File | Read this when | Who it is mainly for |
|---|---|---|
| `REQUIREMENTS.md` | You need to check whether the project does what it was actually asked to do, or you are reviewing a change before it lands. | Founder approving scope, engineer implementing or reviewing a change |
| `PROCESS.md` | You want to see how a real run of the system actually flows, step by step, including what happens when something fails partway through. | Founder learning how the thing behaves, engineer onboarding |
| `QA-GATES.md` | You are about to merge a change, or you want proof a "tested" claim is actually enforced somewhere, not just asserted. | Engineer merging code, founder auditing a quality claim |
| `DATA-MODEL.md` | You are deciding what the project is allowed to hold, reviewing a privacy question, or trying to understand what one table or model is for. | Founder reviewing privacy or retention, engineer touching storage |

## Status of the project this pack describes

<State plainly where the project actually stands today, the same honesty
`README.md` uses. Worked example: "As of 2026-01-10, this is a one-off script
run by hand for six known requesters (see INTAKE.md section 8). Nothing here
describes a self-service feature yet, because one has not been built.">

## Sources of truth for this pack

<List the actual documents or files every claim in this pack traces back to,
the same discipline BrotherMode's own BA pack uses: nothing in REQUIREMENTS,
PROCESS, QA-GATES, or DATA-MODEL should be invented, it should point at a
real spec, a real file, or real, run code. Worked example:
"1. INTAKE.md, the ratified problem statement. 2. decisions/, the recorded
choices. 3. The export script itself, read on 2026-01-10.">

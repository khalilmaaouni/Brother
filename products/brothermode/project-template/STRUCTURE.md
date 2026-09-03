# What each path in this template is for

What this is: a map of this template's own folder, so a human or an AI
session can find the right file without guessing or re-reading everything.
Who reads it: whoever just copied this template and wants to know where to
put something, or whoever inherited the project later and needs to navigate
it fast.

| Path | What belongs here | Read or write this when |
|---|---|---|
| `README.md` | The plain-language what and why, how to run it, current status. | First, before anything else, whether you are the founder or a new session. |
| `INTAKE.md` | The problem-first gate: the problem, the why, the why behind the why, kill criteria, the sunset trigger. | Before design or code starts, and again whenever scope is about to grow. |
| `ARCHITECTURE.md` | The one-page technical map: the pieces, how they connect, a diagram. | Before touching the code, or when explaining the system to someone new. |
| `decisions/` | One file per real decision, numbered in order, each with the WHY and the alternatives that were rejected. | Whenever a choice is made that a future reader could not otherwise reconstruct from the code alone. |
| `docs/README.md` | The index of the fuller documentation pack below, and which of its files to read for which question. | When you need more depth than `ARCHITECTURE.md` gives. |
| `docs/REQUIREMENTS.md` | What the system must do, as testable, numbered statements with a source for each one. | When deciding if the scope is right, or reviewing whether a change actually satisfies what was asked. |
| `docs/PROCESS.md` | How the system actually behaves step by step, including failure and recovery paths, usually as diagrams with a plain-language paragraph under each. | When you need to see the real flow, not just the static map. |
| `docs/QA-GATES.md` | The exact commands that prove the system works, what each one protects, and whether it blocks a release. | Before merging a change, or when auditing whether a "tested" claim is actually enforced somewhere. |
| `docs/DATA-MODEL.md` | What data the system holds, table by table or model by model, in plain language, plus a diagram. | When deciding what the system is allowed to store, or reviewing a privacy or retention question. |
| `.gitignore` | Keeps machine-generated state and local secrets out of version control from the very first commit, before any tool has a chance to set up its own ignore rules. | Never edited casually. Add a line here the moment a new tool starts writing local state you would not want committed. |

## What is deliberately NOT in this template

No `LICENSE`, no CI workflow file, and no test framework are pre-chosen here,
because those decisions depend on the project's own language, audience, and
hosting, and inventing one blind would just get deleted or replaced on day
one. Add them in your first real decision entry (`decisions/0002-...md`) once
you have actually chosen, so the choice and its reasoning are on record
rather than silently assumed.

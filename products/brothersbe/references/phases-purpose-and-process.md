# Phases 1 and 2: purpose and process

LOAD WHEN: a design is starting and the purpose brief or the process map is being written or reviewed.

(Extracted verbatim from SKILL.md, Phases 1 and 2. The routing table in SKILL.md names when to load this file.)

## Phase 1. Purpose (business analysis)

What is this for, who needs it, what does success look like, what breaks if it is wrong,
what is explicitly out of scope. No design starts while the purpose is unstated. The
artifact is `01-purpose.md` (template in `templates/dossier/`): problem stated without a
solution inside it, users and what they do today instead, observable success criteria,
explicit non-goals, and the blast radius named.

What breaks if it is wrong is what sizes everything downstream, including the tier.

## Phase 2. Process and workflow

The workflow as it exists and as it will exist, before any architecture: actors, steps,
triggers, decision points, exception paths, and the handoffs between systems and people.
An architecture is a machine for running a process, so the process is drawn first.

The artifact is `02-process.md`. Every step names an actor, a trigger, and what happens
when it fails. Every handoff names both sides and the contract between them (what is
handed over, and the timing or acknowledgement expected).

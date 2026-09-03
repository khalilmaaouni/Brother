# Module: vault

LOAD WHEN: memory is being read at the start of a run, or written back at a milestone, at a fence close, or at session end.

(The memory half of the run, grouped here so a session that is not using a vault can
skip it. The routing table in `references/modules.md` names when to load it.)

ENFORCEMENT: DECLARED BUT NOT ENFORCED. Nothing anywhere reads
`.brothersbe/profile.json` for this module, so switching it on or off changes nothing
a machine can observe. SKILL.md's step 2 still says "read memory" at every profile,
and the paragraph below about a run with no vault is a reader instruction, not a
control. Giving this module a real enforcement point is named work, not shipped work.

## What this module is

A vault is a directory of markdown the operator owns, outside the repository, that
outlives the session: a project overview, an open-items list, a failures index, a
session log per active day, and `LEARNED.md`. `BROTHERSBE_VAULT` points at it. The
shipped starting point is `memory-template/`.

## What the run does when this module is ON

Step 2 of the spine, which SKILL.md states unconditionally:

> Read memory: project overview, open items, failures index, LEARNED.md. Say so if
> memory is missing; never block on it.

The close is the other half: the session log, the updated open items, the updated
failures index. The law is L17, declared in `references/laws-closing-and-review.md`,
and it stays law at every profile.

## What the run does when there is no vault

Nothing computes this section. The law does not go away; its target does. With no vault, the close writes to the
project `STATE.md` and nothing else, and the run says once, in plain words, that no
vault was configured so the close is on disk in the repository only. That sentence is
the honest form of the same law: a close nobody can find later is the failure L17
exists to prevent, and silence about a missing vault is how it happens.

The checks fed by a vault (`ledger-coverage`, `vault-log-per-active-day` and the
rest, in `tools/sbe_score.py`) report NO-DATA when `BROTHERSBE_VAULT` points at
nothing, which is what the shipped CI does. NO-DATA is never a pass and never a
block, here as everywhere.

## The surfaces

- `memory-template/` (README, `LEARNED.md`, `TEAM-VAULT.md`), copied once into the vault.
- `BROTHERSBE_VAULT`, the environment variable every vault-reading tool resolves.
- `tools/sbe_telemetry.py handoff` and the resume brief, which are written into the vault.

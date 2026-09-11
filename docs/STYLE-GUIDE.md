# Brother documentation style guide

Brother documentation is part of the assurance surface. It must be easier to use than the runtime is to inspect while never claiming more than the runtime proves.

## Four page types

| Type | Reader question | Required shape |
| --- | --- | --- |
| Tutorial | Teach me by taking me through it. | One path, prerequisites, steps, expected evidence, finish state. |
| How-to | How do I accomplish this task? | When to use, prerequisites, actions, verification, failure handling. |
| Reference | What exactly is the contract? | Definitions, fields, states, commands, invariants, boundaries, authority. |
| Explanation | Why is it designed this way? | Problem, reasoning, trade-offs, implications, links to procedure. |

Persona pages are application guides. They describe professional risks/evidence without becoming product modes.

## Put the gist before mechanics

The first paragraph says what the page is for. Readers should not need BrotherMode/SBE names, roadmap ids, or historical debate references before they can act. Historical ids belong in evidence, not first-use prose.

## Claim narrowly

Bad: `Brother proves your migration is safe.`

Better: `Brother records the migration checks, results, and gaps; a human still decides whether that evidence is sufficient for the production change.`

A tool verdict never silently expands into a broader product claim.

## Use the evidence vocabulary exactly

- `PASS`: named evidence supports the named claim.
- `FAIL`: named evidence contradicts the named claim.
- `NO-DATA`: evidence did not establish the answer.
- `CONFLICT`: documentation-review label only, unless the runtime later implements it.

Do not use verified/proven/safe/ready/complete without naming the scope when readers could mistake a local proof for a global one.

## Evidence is not authority

Use human acceptance for the person's decision. Never say Brother approved when Brother only assembled evidence or locally integrated a unit.

## Memory is context

Use recalls/warns/suggests/provides context. Current evidence, repository state, and human decisions outrank Vault memory.

## Keep host instructions separate

Claude Code and Codex do not have identical surfaces. Never present a Claude slash command as a Codex instruction.

## Current-state claims require assurance

Material claims about safety, scope, commands, evidence interpretation, or adoption belong in `docs/assurance/DOC-CLAIMS.md` with authority, test/evidence, repeaters, and last review. Two incompatible live statements must fail documentation validation.

## Link rules

Prefer relative links. Never link to a decision record that may not exist in a clean checkout as if guaranteed. Do not use generated `SYSTEM.md` as a beginner tutorial. No dead coming-soon links. Compatibility stubs contain links only, not duplicate behavioral claims.

## Commands

Make the working directory obvious when it matters. Do not show a command that dirties the target repo with plan/run state when the runtime requires a clean baseline.

## Tone

Direct, concrete, calm, senior. No hype, no decorative metaphors hiding mechanics, no competitive chest-beating. It is acceptable to say experimental, slower for tiny changes, or NO-DATA.

## Required endings

Tutorial: say what is proven and what remains a human decision. How-to: include verification. Reference: name authority. Explanation: link to canonical procedure/reference instead of repeating commands.

---
name: learn
description: Use when a lesson from an incident, a repeated correction, a review finding or a measured outcome should become a shared rule, or when a session wants to propose an amendment to the laws. Proposes; it never lands a change to shared behavior. Invoke as /brothersbe:learn.
---

# Learn

No colleague's tool changes behavior silently. This skill exists so a lesson can travel
without a session quietly rewriting the law everyone else is running under.

Read `${CLAUDE_PLUGIN_ROOT}/SKILL.md`, then
`${CLAUDE_PLUGIN_ROOT}/references/laws-closing-and-review.md` (L17, the closing and memory
write-back).

## What qualifies as a lesson

One of four things, named: an incident, a correction repeated more than once, a review
finding, or a measured outcome. An opinion formed mid-session does not qualify, however
strongly held. Say which of the four this is, and cite it.

## What a proposal must carry

1. The defect, stated as the thing that went wrong and what it cost.
2. The rule that would have caught it, in law form: WHEN (an observable trigger), INPUTS (the
   named things it reads), RULE (a decision table or an explicit condition, never an
   adjective), OUTPUT (proceed, proceed with a label, stop and ask, or refuse), ENFORCED BY (a
   real path, a template field, a CI step, or the words "human review").
3. A fixture in `evals/` that proves the check catches the defect, calibrated by re-injecting
   the defect so a green result cannot come from the test being broken.
4. Which existing law it merges with or displaces. A law that accretes beside another one is
   how a law file grows past the point where anyone reads it.

A rule that cannot name an enforcement point is not a law. It is advice, and it belongs in
`${CLAUDE_PLUGIN_ROOT}/PRACTICES.md`, which says so about itself.

## Where the proposal goes

Into the pending-amendments note in the memory vault, and nowhere else. A session may PROPOSE
an amendment and may not LAND one. A human merges it at the review, one consolidation per
cycle, through a reviewed pull request. The same applies to a team law in
`memory-template/LEARNED.md`.

# Guided kickoff, decision cards, and error cards

LOAD WHEN: a new project or sizable goal is being started, a decision must be put to the user, or an error must be reported to the user.

## The kickoff flow

The goal of kickoff is one agreed direction, not a completed interview.

1. Detect before asking. Look at what is already there (project type, git
   repository, existing tests, uncommitted work) and infer the user's
   experience level from how they describe the goal. Never ask a question the
   tree can answer.
2. Scan the project context once and keep the map; do not rescan unchanged
   areas later.
3. Ask adaptive questions ONLY when the answer would change the scope or the
   solution. Everything else gets a stated assumption the user can correct.
4. One decision at a time, as a decision card (below), recommended option
   first. Never a wall of questions.
5. Present the recommended direction first; alternatives only when they are
   materially useful, never for completeness.
6. Close kickoff by recording the project brief: outcome, intended user,
   recommended direction and why, what is included, what is explicitly not,
   success checks, main risks, decisions made, decisions still open, and the
   initial forecast (format per references/forecasting.md). Record it with
   `python3 tools/bm_project.py start`, which writes the project into the
   store (the single source of truth) and generates `CANVAS.md` in the
   project folder as a readable view; the status and next-step flows read
   the store after a restart, never the markdown.
7. The brief is the source of direction from then on. Work does not start
   until the main scope and the highest-stakes open decision are settled.

State expectations at kickoff start in forecast form: how long the definition
itself will likely take, as a range with confidence, per
references/forecasting.md.

Before building an options artifact (references/visual-surface.md rule 4),
say one plain-language line naming what is being built and that it takes a
moment, THEN build it: an unexplained wait reads as broken, not as working,
and this cost a session a founder-scored 1 of 5 on 2026-08-29. Say what the
options actually are ("comparing the local-server and Artifact approaches"),
never a bare "working on it." Once a reusable intake template exists
(references/intake-artifact-template.md), building from it is fast enough
that this line still fires, but the wait it is covering is short.

A wait longer than the artifact build itself, a dispatched research or
design agent, a real background command, gets the same treatment scaled up:
one bulleted status before it starts (what is running, on what, roughly how
long), and a fresh bulleted status at every check-in while it runs, never a
silent re-arm with nothing beside it. Founder direction 2026-08-29, given
after exactly that silence read as idle. This is not specific to kickoff:
the same rule covers any wait in any flow.

All wording obeys references/terminology.md: plain language, no machinery.

## Decision cards

Every decision put to the user uses this shape:

```text
Decision needed: Payment approach

Recommended: Hosted checkout
Why: Lowest security burden and fastest reliable launch

Other option: Fully embedded checkout
Tradeoff: Better visual control, but more implementation and compliance work

[Use hosted checkout]
[Use embedded checkout]
[Explain more]
```

Rules:

- The recommended option always comes first, with its Why.
- Each alternative carries its Tradeoff in one line.
- 2 to 4 option buttons, one of which may be "Explain more".
- In clients with a native question UI (in Claude Code, the AskUserQuestion
  windows), the card travels through that UI, recommended option first and
  marked. Chat text carries evidence and context, never the option list.
- One card per decision, highest-stakes decision first when several queue up.

## Error cards

Every error reported to the user uses these four sections, in this order:

```text
I could not verify the payment flow.

What happened
The sandbox rejected the merchant credentials.

Impact
Checkout cannot be marked ready.

Recommended action
Add valid sandbox credentials, then I will rerun the complete payment test.

What remains safe
The booking and availability work is unchanged.
```

Rules:

- What happened / Impact / Recommended action / What remains safe, always in
  that order, always all four.
- No raw stack traces by default. The technical trace appears only under the
  advanced view (references/status-view.md).
- Explain the user impact before the technical cause.
- Offer exactly one recommended action first; alternatives only if the
  recommended one may be unavailable to the user.
- "What remains safe" is mandatory because it is the sentence that prevents
  panic; if nothing is safe, say that plainly instead.

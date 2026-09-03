# Practices

This file is advice, and says so. Nothing here is enforced by a check. It is here
because it is true and useful, not because it can be verified. The laws, which do
carry enforcement points, are in [SKILL.md](SKILL.md).

The split is deliberate. A rule stated in a prompt is not a control; a control is a
check that runs. Mixing the two makes the law file feel stronger than it is, which is
the failure mode this split exists to prevent. So: laws name machinery, practices
admit they are judgment.

## Judgment that resists tabulation

- Naming: a name that needs a comment to explain it is the wrong name.
- Cohesion: code that changes together belongs together, whatever the layer diagram says.
- When to split a service: split along the axis where two teams disagree about deploy
  cadence, not along nouns. The decision table in `tables/architecture.json` scores the
  shape question; it does not tell you where the seam runs.
- Estimation: give a range and the assumption that would break it, never a single number.
- Reading before writing: the fastest way through unfamiliar code is to read its tests first.
- Deleting: code that is not called is not an asset. Removing it is cheaper than
  carrying it, and the test suite is the check on whether you were right.

## Working with people

- A stakeholder who cannot describe the failure mode has not finished describing the
  requirement.
- Write the summary for the person who was not in the room.
- When a decision is reversed, record why, not just what. The ADR template has a place
  for this ("What would flip this"); using it well is judgment, having it is law.
- Bad news travels first. A failed gate reported late costs more than the failure.
- Disagreement is cheap in review and expensive in production. Say the objection while
  the design is still a document.

## The loop close-out interview

When a run closes with open loops (work remaining, decisions pending), the closing
report does not end at a list. Each loop is put to its decision owner as a set of
questions, one set per loop, and every question carries three things: a
recommendation, the case for it, and the case against it. A question without a
recommendation exports the thinking to the busiest person in the room; a
recommendation without its cons is advocacy wearing analysis's clothes.

Order the sets so decisions that gate other decisions come first, and say which
answers unblock which loops. Triage before asking: gating decisions first, then
direction, then technical choices, then process, and within a set the question
whose answer changes the most other answers leads.

Delivery follows the room. When the harness offers a native question surface
(selectable options with a marked recommendation), the interview runs there BY
DEFAULT, a handful of questions per screen in dependency order, never the whole
backlog at once. The recommended option is marked as such, and the case against
it rides in that option's own description, because a recommendation whose cons
are hidden a click away is advocacy again. Prose is reserved for environments
with no question surface at all, and reaching for it anywhere else is the
failure this paragraph exists to stop: thirty decisions in a wall of text is how
half of them go unanswered.

Answers become recorded decisions, in the working state, the vault, or an ADR,
wherever that class of decision already lives. Questions left unanswered stay
named open items and are asked again at the next close, never silently dropped.
This is judgment, not a control: nothing checks that a question was asked well,
and the interview stands or falls on the honesty of its cons.

## How a practice becomes a law

When someone builds a check for one of these, it stops being advice: it moves into
SKILL.md in the law form (WHEN, INPUTS, RULE, OUTPUT, ENFORCED BY) with the new check
named on the enforcement line, and it ships with a fixture in `evals/` proving the check
catches the defect it claims to catch. That promotion rides a reviewed pull request, the
same as any other law change. Until then it stays here, where it is honest.

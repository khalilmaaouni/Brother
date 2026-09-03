# Phase 5: expression, diagrams and documentation

LOAD WHEN: a diagram is being drawn or changed, or the dossier's documentation is being written.

(Extracted verbatim from SKILL.md, Phase 5. The routing table in SKILL.md names when to load this file.)

## Phase 5. Expression (diagrams and documentation)

Diagrams are code (Mermaid), committed with the design, diffed in review, in
`06-diagrams.md`, inside a fenced code block so they diff as source rather than as prose.
What a tool checks here, and it is less than this section used to claim: `06-diagrams.md`
holds at least one fenced diagram whose every node appears somewhere else in the dossier.
No tool reads the tier when checking diagrams and no tool counts diagram TYPES, so a T2
dossier carrying a single flowchart passes. This paragraph used to state a required set per
tier (a context diagram plus a workflow or sequence diagram plus an entity relationship
diagram at T2, and more at T3) that nothing enforced, which is a law claiming an enforcement
it does not have, the exact failure this project exists to prevent. The set is worth
writing and is [human] guidance, not a gate: at T2 and above, a reviewer should expect
context, a workflow or sequence view, and the data delta, and should say so in review.
T1 requires `01-purpose.md` and nothing else, so it has no diagram artifact and no required
diagram: a sketch there is welcome and is not a gate.

Every element that appears in a diagram appears somewhere else in the dossier. That is L5,
it is mechanical, and it is what stops a diagram drifting quietly away from the system it
claims to show. Two neighbouring rules are [human] review and are marked as such rather than
implied to be enforced: that every node is named, and that every edge says what flows and by
what trigger or protocol. Nothing parses an edge label for a trigger or a protocol; the
parser that touches edge labels exists to DISCARD their words so they are not mistaken for
nodes.

Documentation is brief by default, written for a human to follow in order, commented where
a choice is non-obvious. Length is sized to the difficulty of the task, never to the effort
spent.

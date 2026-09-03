# Phase 6: verification

LOAD WHEN: the gates are about to run, or a verification plan is being written.

(Extracted verbatim from SKILL.md, Phase 6. The routing table in SKILL.md names when to load this file.)

## Phase 6. Verification

Now, and not before, the gates. Four failure classes are silent: a wrong result looks
exactly like a right one, and detection latency runs from minutes to never. For these,
verification is structural. Each has a mechanical check in `tools/sbe_gate.py`, run
advisory in a session and enforcing (`--strict`, exits nonzero) in CI. Output that has not
cleared its gate is presented with the label UNVERIFIED next to the item itself, not in
a footnote; the label is the agent's to write (L7, L16), and no tool applies it.
The design side runs the same way through `tools/sbe_design.py` (artifacts, adr, datamodel,
diagrams, placeholder), and the weekly code-graded checks through `tools/sbe_score.py`. The plan for all of it is `07-verification.md`: every
claim the design makes names the check that will prove it, and when that check runs.

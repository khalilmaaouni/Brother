# BrotherME's status, review, deliver and next-step flows

Split out of skills/brotherme/SKILL.md by the intake byte floor decision of
2026-09-09 (docs/decisions/intake-byte-floor-2026-09-09.json, option A): no
first intake answer needs these four flows, and scripts/intake_measure.py's
`_skill_stack` sums every references/*.md file the kickoff-only SKILL.md text
still points at, so keeping these citations in that file charged every intake
for references/delegation.md, references/status-view.md, references/pulse.md
and references/definition-of-done.md. The sections below are unchanged from
the guided skill; only their location moved.

## Next-step flow

When the user asks what to do next, run `python3 "${CLAUDE_PLUGIN_ROOT}/tools/bm_project.py" next` (a plugin install exports `${CLAUDE_PLUGIN_ROOT}` for skill and command content, so that path resolves on its own; on a clone install, where the variable is unset, run `python3 tools/bm_project.py next` instead, from the BrotherMode root, the directory that holds `tools/`; either way, run it from the user's project folder so it reads that project's own records) and read its recommendation straight from those records, never from CANVAS.md by hand; recommend exactly one next step, stated first, with a short reason and a time range per references/forecasting.md. If a decision from the user is what blocks progress, present that decision instead, with a recommended option first, using the decision card format in references/kickoff.md. When work is being handed to a helper, the split follows the guided loop in references/delegation.md: the coordinator plans and judges, a cheaper helper executes, and the user hears only "picking the right helper for the job" unless they ask for the advanced view.

## Status flow

When the user asks where things stand, produce the default status view from references/status-view.md: exactly Goal, Direction, Progress, Time remaining, Decision needed, Risk, Evidence, and Next step. Nothing more unless the user explicitly asks for the advanced view. What deserves proactive mention between status requests is governed by references/pulse.md.

## Review flow

When the user asks for a review, apply every point of references/definition-of-done.md to the work. Report each point as a pass or a not-yet with its evidence. Never drop or soften a failing point.

## Deliver flow

When the user asks to wrap up, run `python3 "${CLAUDE_PLUGIN_ROOT}/tools/bm_project.py" deliver` to generate the delivery packet from that project's own records; never fill DELIVERY-PACKET.md by hand. A plugin install exports `${CLAUDE_PLUGIN_ROOT}` for skill and command content, so that path resolves on its own; on a clone install, where the variable is unset, run `python3 tools/bm_project.py deliver` instead, from the BrotherMode root (the directory that holds `tools/`). Either way, run it from the user's project folder so it reads and writes that project's own records. Delivery requires proof: a verifying check that ran after the last change and passed. Without it, say plainly what remains and do not call the work delivered.

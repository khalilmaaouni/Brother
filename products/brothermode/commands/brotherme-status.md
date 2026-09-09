---
description: Show where the project stands right now, in plain language
---

> This command works and is supported. Its current name is `/brothermode:status`, and both names do exactly the same thing.

Outcome to produce: one short status view the user can read in under a minute, leading with what has been achieved, not with process.

Follow skills/status/SKILL.md in this plugin exactly; it is the single source for this command.

The ship verdict always leads (R-4, persona dogfood 2026-09-07): do not ship yet, naming the open decisions, risks and missing evidence, or nothing is blocking. Answer in the language the user wrote in, per references/honesty.md. If the user's message carried a specific question, answer that question first, in one sentence that contains the thing they asked for (R-12, persona dogfood 2026-09-07), before anything else; an incident ask (down, outage, hotfix, urgent) routes straight to `/brothersbe:start` with only the Verdict line printed first, and an audit ask (audit, compliance, evidence for) prints the Verdict, then the receipts and decisions, then the sbe verify command.

---

## Maintainer note, not for the reader above

Kept verbatim from where it used to sit at the top of this file. It was moved on 2026-08-29 because it was the first thing anybody read: the team reported finding fifteen commands and every one of them declaring itself a legacy compatibility shim, which reads as an abandoned product. The mechanism is unchanged and nothing was removed.

> DOCUMENTATION NOTICE, 2026-08-11 (V3 Final, task A2). This command file is not part of the six-name public surface. It keeps working exactly as it does today and is not deprecated in behaviour; only its documented status changed. Physical consolidation of these shims is a later tranche, so nothing here is removed in this release.

> LEGACY v2 COMPATIBILITY SHIM (the founder's 2026-08-07 night rename decision, recorded in this project's working history rather than a file this repository ships). Legacy surface: `/brotherme-status` under the pre-rename `brotherme` plugin id. Replacement: `/brothermode:status` at `skills/status/SKILL.md`. Reason: the founder's 2026-08-07 night namespace rename retired the flat `commands/` layout as the canonical public surface; this file is kept, unchanged below, only so a v2 install or a v2 habit still resolves during the migration window. Test: `tools/test_bm.py`'s `TestTheSeventhCommandAndTheDeepTourAreWired` (the fifteen-command inventory pin) and the naming/ACTIVE_DOCS scan in `tools/test_bm_docs.py` still exercise this exact file and path; do not rename or delete it without updating both. Removal condition: the v3.0.0 tag, at the release court described in freeze answer 14, once `claude plugin validate` and a repository grep show no live consumer of `/brotherme-status` remains.

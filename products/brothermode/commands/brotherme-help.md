---
description: Explain what BrotherMode does and how to use it, in plain language
---

> This command works and is supported. Its current name is `/brothermode:help`, and both names do exactly the same thing.

Outcome to produce: a short, plain-language orientation that ends in ONE question, not a list of everything the product can do. No setup steps that involve editing files, and no internal machinery.

Follow skills/help/SKILL.md in this plugin exactly; it is the single source for this command.

Answer in the language the user wrote in, per references/honesty.md. If the user's message carried a specific question, answer that question first, then ask exactly ONE question and stop (R-4, persona dogfood 2026-09-07): a fixed pitch that answers everything except what they actually asked is not an answer. Offer the deep tour as the recommended option for a user who wants to see where everything stands, then ask whether they would like that tour or would rather just say what they want to accomplish. Do not print the command list unless they ask for it.

---

## Maintainer note, not for the reader above

Kept verbatim from where it used to sit at the top of this file. It was moved on 2026-08-29 because it was the first thing anybody read: the team reported finding fifteen commands and every one of them declaring itself a legacy compatibility shim, which reads as an abandoned product. The mechanism is unchanged and nothing was removed.

> DOCUMENTATION NOTICE, 2026-08-11 (V3 Final, task A2). This command file is not part of the six-name public surface. It keeps working exactly as it does today and is not deprecated in behaviour; only its documented status changed. Physical consolidation of these shims is a later tranche, so nothing here is removed in this release.

> LEGACY v2 COMPATIBILITY SHIM (the founder's 2026-08-07 night rename decision, recorded in this project's working history rather than a file this repository ships). Legacy surface: `/brotherme-help` under the pre-rename `brotherme` plugin id. Replacement: `/brothermode:help` at `skills/help/SKILL.md`. Reason: the founder's 2026-08-07 night namespace rename retired the flat `commands/` layout as the canonical public surface; this file is kept, unchanged below, only so a v2 install or a v2 habit still resolves during the migration window. Test: `tools/test_bm.py`'s `TestTheSeventhCommandAndTheDeepTourAreWired` (the fifteen-command inventory pin) and the naming/ACTIVE_DOCS scan in `tools/test_bm_docs.py` still exercise this exact file and path; do not rename or delete it without updating both. Removal condition: the v3.0.0 tag, at the release court described in freeze answer 14, once `claude plugin validate` and a repository grep show no live consumer of `/brotherme-help` remains.

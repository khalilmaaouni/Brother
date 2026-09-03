---
description: Package the finished work into one delivery summary with the evidence that it works
---

> This command works and is supported. Its current name is `/brothermode:deliver`, and both names do exactly the same thing.

Outcome to produce: one delivery packet the user can read, share, and act on, proving what was built and how it was checked.

Enter the delivery flow of the brotherme skill. Run the mechanical command `python3 "${CLAUDE_PLUGIN_ROOT}/tools/bm_project.py" deliver` to generate the delivery packet from that project's own records; never fill DELIVERY-PACKET.md by hand and never answer from memory of this conversation about what is done. A plugin install exports `${CLAUDE_PLUGIN_ROOT}` for skill and command content, so that path resolves on its own; on a clone install, where the variable is unset, run `python3 tools/bm_project.py deliver` instead, from the BrotherMode root (`~/.claude/skills/brothermode`). Either way, run it from the user's project folder so it reads and writes that project's own records. Do not say "ready to deliver" unless a verifying check ran after the last edit and passed; if any check is missing or failing, say so plainly and list what remains instead of delivering.

---

## Maintainer note, not for the reader above

Kept verbatim from where it used to sit at the top of this file. It was moved on 2026-08-29 because it was the first thing anybody read: the team reported finding fifteen commands and every one of them declaring itself a legacy compatibility shim, which reads as an abandoned product. The mechanism is unchanged and nothing was removed.

> DOCUMENTATION NOTICE, 2026-08-11 (V3 Final, task A2). This command file is not part of the six-name public surface. It keeps working exactly as it does today and is not deprecated in behaviour; only its documented status changed. Physical consolidation of these shims is a later tranche, so nothing here is removed in this release.

> LEGACY v2 COMPATIBILITY SHIM (the founder's 2026-08-07 night rename decision, recorded in this project's working history rather than a file this repository ships). Legacy surface: `/brotherme-deliver` under the pre-rename `brotherme` plugin id. Replacement: `/brothermode:deliver` at `skills/deliver/SKILL.md`. Reason: the founder's 2026-08-07 night namespace rename retired the flat `commands/` layout as the canonical public surface; this file is kept, unchanged below, only so a v2 install or a v2 habit still resolves during the migration window. Test: `tools/test_bm.py`'s `TestTheSeventhCommandAndTheDeepTourAreWired` (the fifteen-command inventory pin) and the naming/ACTIVE_DOCS scan in `tools/test_bm_docs.py` still exercise this exact file and path; do not rename or delete it without updating both. Removal condition: the v3.0.0 tag, at the release court described in freeze answer 14, once `claude plugin validate` and a repository grep show no live consumer of `/brotherme-deliver` remains.

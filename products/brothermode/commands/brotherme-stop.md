---
description: Stop the Full-Auto controller run right now, draining in-flight work and releasing every held claim
---

> This command works and is supported. Its current name is `/brothermode:stop`, and both names do exactly the same thing.

Outcome to produce: the controller run for this project is stopped, cleanly, with the user told exactly what state it landed in.

Enter the Full-Auto stop flow. This is the kill switch on the RUN itself (distinct from pausing or revoking the underlying autonomy contract, which is `bm-autonomy pause` or `bm-autonomy revoke`, a separate act the user may also want).

Run the mechanical command `python3 tools/bm_controller.py stop --project <id> --controller-id <the same id the run was started or resumed with> --actor-name <the user's name>`; never answer from memory of this conversation about what state the run was in. This command never fails hard: if there is no run at all, or it already reached a terminal state, it says so and does nothing further. Otherwise it drains any in-flight unit (recording whatever result had already arrived, never inventing one), releases every fence it held, and reports the state it moved from and to.

Tell the user plainly what happened and, if any unit was left mid-flight when the stop landed, name it: nothing is lost, but that unit will need a fresh dispatch the next time the run resumes.

---

## Maintainer note, not for the reader above

Kept verbatim from where it used to sit at the top of this file. It was moved on 2026-08-29 because it was the first thing anybody read: the team reported finding fifteen commands and every one of them declaring itself a legacy compatibility shim, which reads as an abandoned product. The mechanism is unchanged and nothing was removed.

> DOCUMENTATION NOTICE, 2026-08-11 (V3 Final, task A2). This command file is not part of the six-name public surface. It keeps working exactly as it does today and is not deprecated in behaviour; only its documented status changed. Physical consolidation of these shims is a later tranche, so nothing here is removed in this release.

> LEGACY v2 COMPATIBILITY SHIM (the founder's 2026-08-07 night rename decision, recorded in this project's working history rather than a file this repository ships). Legacy surface: `/brotherme-stop` under the pre-rename `brotherme` plugin id. Replacement: `/brothermode:stop` at `skills/stop/SKILL.md` (an internal, hidden skill: reachable by exact name, not part of the nine advertised in `/help`). Reason: the founder's 2026-08-07 night namespace rename retired the flat `commands/` layout as the canonical public surface; this file is kept, unchanged below, only so a v2 install or a v2 habit still resolves during the migration window. Test: `tools/test_bm.py`'s `TestTheSeventhCommandAndTheDeepTourAreWired` (the fifteen-command inventory pin) and the naming/ACTIVE_DOCS scan in `tools/test_bm_docs.py` still exercise this exact file and path; do not rename or delete it without updating both. Removal condition: the v3.0.0 tag, at the release court described in freeze answer 14, once `claude plugin validate` and a repository grep show no live consumer of `/brotherme-stop` remains.

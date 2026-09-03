---
description: Begin or resume the Full-Auto controller, which sequences a signed outcome to a checked deliverable
argument-hint: [nothing needed once a contract is signed]
---

> This command works and is supported. Its current name is `/brothermode:auto`, and both names do exactly the same thing.

Outcome to produce: the durable Full-Auto controller (`docs/FULL-AUTO.md`) either begins a fresh run or resumes an existing one, and the user sees plainly what it just did and what happens next.

Enter the Full-Auto flow. This is a different, more advanced layer than the guided start (`/brotherme-start`): it requires a signed authorisation (the autonomy contract, `docs/AUTONOMY.md`) before any run can begin, and it drives real work through a resumable loop rather than a single conversation.

FIRST, check for a live contract by running `python3 tools/bm_autonomy.py show --project <id>`. If none exists, the user has not authorised autonomous work yet: explain in plain language what signing means (the outcome, how done is defined, which folders and actions are allowed, an optional token and minute budget, and that credential entry, payments, account sign-in, permanent deletion, and publishing can never be granted, ever), gather the answers, and run `python3 tools/bm_autonomy.py sign` yourself with the gathered details. Never invent an outcome or a done definition the user did not actually state.

ONCE a live contract exists, run `python3 tools/bm_controller.py start --project <id> --outcome "<the same outcome>" --done-definition "<the same done definition>" --controller-id <a stable id for this session> --actor-name <the user's name>`. Read its output rather than answering from memory of this conversation about what state the run is in:

- If it reports a fresh run in state `NEW`, the unit graph does not exist yet. Building that graph (breaking the outcome into small, dependency-ordered units with a done-check command each) is this session's own judgement, never something the controller invents; do that work, then hand the graph to `python3 tools/bm_controller.py plan --units-file <file> --controller-id <id> --actor-name <name>`.
- If it reports a dispatched unit and a printed brief, that unit is now this session's own work to do. Do it, then call `python3 tools/bm_controller.py record-result --dispatch-id <id from status --json> --worker-claim "<what was done>" --artifact <path> --controller-id <id> --actor-name <name>` to hand the result back. Never call `record-result` for work that was not actually done.
- If it reports `DELIVERABLE_READY`, name exactly what the report says remains founder-gated (open human steps, failed units) and tell the user plainly that only they can accept it, with `/brotherme-auto-status` for the full picture and a manual `bm-controller complete` call once they are satisfied.
- If it reports `WAITING_HUMAN` or `PAUSED`, say so in plain language and name the one thing that would unblock it.

Run this same command again, with the same `--controller-id`, to continue driving the loop forward; it always resumes from where it left off and never repeats completed work.

---

## Maintainer note, not for the reader above

Kept verbatim from where it used to sit at the top of this file. It was moved on 2026-08-29 because it was the first thing anybody read: the team reported finding fifteen commands and every one of them declaring itself a legacy compatibility shim, which reads as an abandoned product. The mechanism is unchanged and nothing was removed.

> DOCUMENTATION NOTICE, 2026-08-11 (V3 Final, task A2). This command file is not part of the six-name public surface. It keeps working exactly as it does today and is not deprecated in behaviour; only its documented status changed. Physical consolidation of these shims is a later tranche, so nothing here is removed in this release.

> LEGACY v2 COMPATIBILITY SHIM (the founder's 2026-08-07 night rename decision, recorded in this project's working history rather than a file this repository ships). Legacy surface: `/brotherme-auto` under the pre-rename `brotherme` plugin id. Replacement: `/brothermode:auto` at `skills/auto/SKILL.md` (an internal, hidden skill: reachable by exact name, not part of the nine advertised in `/help`). Reason: the founder's 2026-08-07 night namespace rename retired the flat `commands/` layout as the canonical public surface; this file is kept, unchanged below, only so a v2 install or a v2 habit still resolves during the migration window. Test: `tools/test_bm.py`'s `TestTheSeventhCommandAndTheDeepTourAreWired` (the fifteen-command inventory pin) and the naming/ACTIVE_DOCS scan in `tools/test_bm_docs.py` still exercise this exact file and path; do not rename or delete it without updating both. Removal condition: the v3.0.0 tag, at the release court described in freeze answer 14, once `claude plugin validate` and a repository grep show no live consumer of `/brotherme-auto` remains.

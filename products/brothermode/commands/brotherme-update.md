---
description: Check the installed version against the newest release and explain how to update, in plain language
---

> This command works and is supported. Its current name is `/brothermode:update`, and both names do exactly the same thing.

Outcome to produce: tell the user their installed version, the newest available version, whether they match, and the exact update steps for their install path.

1. Read the VERSION file at the BrotherMode root (the folder this command's plugin or skill is installed in). That is the installed version.
2. Check the newest release tag: `git ls-remote --tags https://github.com/khalilmaaouni/BrotherModeUp.git`. If the network refuses, say plainly the check could not run, and do not guess a version.
3. Tell the user, in plain words: their installed version, the newest version, and whether they match.
4. If they already match, say so and stop; no update steps needed.
5. If the installed version is NOT one of the tags that exist (a development copy, for example a version ending `.dev1`, or anything newer than the newest release), say plainly that they are running a development copy, that no released version is newer than what they have, and STOP. Do not send them to a checkout. Without this step the next one fires on any mismatch and tells a developer to check out an OLDER tag while calling it an update, which is a downgrade wearing the wrong word.
6. If an update is genuinely available, meaning their installed version is a released one and an older one, give the exact steps for their own install path only:
   - Plugin install: `/plugin marketplace update brotherme-marketplace`, then `/plugin update brotherme`, then restart Claude Code.
   - Pinned clone: inside the skill folder, run `git fetch --tags`, then `git checkout <newest tag>` (the exact commands are in docs/RELEASE.md).
7. Pinned clone only, right after the checkout: run `python3 scripts/doctor.py`. Its CHECKSUMS.sha256 self-check is what catches a half-finished update, a checkout that stopped partway, by naming the exact file that does not match what was released. A dirty working tree (local edits left over from before) is a named SKIP, not something this check detects: it declines to compare rather than risk a false alarm on ordinary uncommitted work, so it does not catch leftover local edits by itself. A plugin install has no checkout step to verify this way; `/plugin update` verifies its own files.
8. Then re-run `python3 scripts/doctor.py` once more, the same way you would right after a fresh install: every one of its eleven checks should read PASS or SKIP (SKIP is not a failure: for most checks it means that check found nothing to look at yet, and for the checksum check specifically it can also mean a dirty working tree made an honest comparison impossible, per step 7 above). If any check reads FAIL, follow the one-sentence fix it prints before doing anything else.
9. Rollback, if a FAIL will not clear: `git checkout <the tag you were on before>` inside the skill folder, then run `python3 scripts/doctor.py` again to confirm the rollback itself is healthy.
10. Give one recommended next action, and state any time cost as a range, never a promise, in plain words a non-engineer would follow.
11. Say plainly: updating never touches the user's project data or records; it only replaces the BrotherMode files themselves.

---

## Maintainer note, not for the reader above

Kept verbatim from where it used to sit at the top of this file. It was moved on 2026-08-29 because it was the first thing anybody read: the team reported finding fifteen commands and every one of them declaring itself a legacy compatibility shim, which reads as an abandoned product. The mechanism is unchanged and nothing was removed.

> DOCUMENTATION NOTICE, 2026-08-11 (V3 Final, task A2). This command file is not part of the six-name public surface. It keeps working exactly as it does today and is not deprecated in behaviour; only its documented status changed. Physical consolidation of these shims is a later tranche, so nothing here is removed in this release.

> LEGACY v2 COMPATIBILITY SHIM (the founder's 2026-08-07 night rename decision, recorded in this project's working history rather than a file this repository ships). Legacy surface: `/brotherme-update` under the pre-rename `brotherme` plugin id. Replacement: `/brothermode:update` at `skills/update/SKILL.md`. Reason: the founder's 2026-08-07 night namespace rename retired the flat `commands/` layout as the canonical public surface; this file is kept, unchanged below, only so a v2 install or a v2 habit still resolves during the migration window. Test: `tools/test_bm.py`'s `TestTheSeventhCommandAndTheDeepTourAreWired` (the fifteen-command inventory pin) and the naming/ACTIVE_DOCS scan in `tools/test_bm_docs.py` still exercise this exact file and path; do not rename or delete it without updating both. Removal condition: the v3.0.0 tag, at the release court described in freeze answer 14, once `claude plugin validate` and a repository grep show no live consumer of `/brotherme-update` remains.

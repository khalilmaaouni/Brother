# Release process

Written 2026-07-26, the day the first version was cut. Read this before cutting
any release, and read it especially if you are a machine (an AI coding
session) about to run these steps: three of them are marked FOUNDER-GATED and
a machine must refuse to perform them.

## The problem this solves

The install instruction in `README.md` and `docs/SETUP.md` clones a git
branch into `~/.claude/skills/brothermode`, and the code in that directory
then runs automatically on every Claude Code session through six hooks
(`SessionStart`, `SessionEnd`, `Stop`, `PreCompact`, `PreToolUse`; `python3
tools/bm_project_facts.py --field hook_events` prints the live list). The
original external audit of this project named that combination, a moving branch
feeding
auto-run code, as the weakest link in the whole design. Two problems live
inside that one sentence:

1. **"Moving branch" means the code you run today can be different from the
   code you reviewed yesterday**, with no signal that anything changed. A
   `git pull` silently replaces what is on disk.
2. **Nobody could answer "what exactly did I install"** without reading
   every file by hand, because there was no manifest and no release to point
   at.

This document is the discipline that answers both: tagged, immutable
releases (problem 1) with a checksum manifest a user can check against
(problem 2).

CORRECTED 2026-07-27. The paragraph here used to say no version had ever been
tagged and continuous integration had never executed. Both statements were false
by the time anyone read them: `v2.0.0-rc.1` was tagged on 2026-07-26 and CI has
run more than twenty times. The instruction attached to the old paragraph, "do
not let a later, cleaner-sounding paragraph replace it", was well meant and
became a defence of a stale fact. Prefer a statement that names its date and its
evidence over one that asks to be preserved.

## The version scheme

`VERSION`, one line, holds the current identity. Read it rather than
trusting a number typed into this paragraph:

```bash
cat VERSION
python3 tools/bm_project_facts.py --field release_tag
python3 tools/bm_project_facts.py --field install_target_tag
```

RECONCILED 2026-08-01, opening the release-closure program (Loop 0): the tree
now reads `2.0.0-rc.12.dev1`, a DEVELOPMENT identity, not a release candidate.
`release_tag` is `None` for it on purpose: a development identity claims no
tag at all. The public install target stays pinned at the last tag actually
known to resolve, `install_target_tag`. That was `v2.0.0-rc.9` when this
paragraph was written 2026-08-01; it has moved since. The live value is
`PUBLIC_INSTALL_TAG` in `tools/bm_project_facts.py`, currently `v1.0.7`,
independent of whatever VERSION says. See "The version law" below for the
rule this follows.

`2.0.0-rc.11` and `2.0.0-rc.10` are both **SUPERSEDED, NEVER TAGGED**. Both
were cut the same day, 2026-08-01: rc.11's release-cut commit is `54cb898`,
rc.10's is `8aa6dd1`. Both landed on `main` and were pushed, but the founder
had not yet cut either tag before the next commit moved past it, so neither
`v2.0.0-rc.10` nor `v2.0.0-rc.11` exists, and by ratified decision
(amendment A1, the external review of the release-closure plan) neither ever
will: cutting either tag now would point it at a commit the tree has already
moved past, recreating the exact two-trees ambiguity `rc.1` reproduced once
already. Their pinned clone commands never resolved during their short life,
which the pages honestly stated the whole time. `v2.0.0-rc.9`, cut
2026-07-31 so that the tagged bytes and the first fully green CI run
(`30564943060`) were the same bytes, is SUPERSEDED normally, and remains the
public install target until a future tag replaces it. This project creates
release tags through
the GitHub Desktop app (a command-line tag does not push from Desktop,
confirmed empirically), so the tag is cut by the founder immediately after the
release-cut commit lands, and the release-truth suite holds this page honest in
both directions: its tag tests SKIP with a stated reason while the tag does not
exist and become mandatory the moment it does.

`v2.0.0-rc.6` is SUPERSEDED, not withdrawn: it is sound and verifies, but it was
tagged two commits before the Windows shell-quoting fix, so its tagged tree still
has red CI and cannot point at the green run as its evidence.

**`v2.0.0-rc.5` IS WITHDRAWN. Do not install it.** It was tagged and pushed on
2026-07-31 and withdrawn roughly fifteen minutes later, on the same grounds as
`rc.1`: its `CHECKSUMS.sha256` did not describe its own tree. Eight shipped
files were absent from the manifest, including `tools/bm_docs.py`,
`tools/bm_docs_export.py` and `tools/bm_packs.py`, so `verify-install.sh`
against that tag reports three real tools as unknown files. The cause was
operator error, recorded rather than smoothed over: the release-cut session ran
`scripts/checksums.sh` with its output redirected to `/dev/null` instead of
passing the documented output path, so the manifest was never rewritten and the
commit message claiming it had been regenerated was false. The tag object still
exists on the remote because deleting a published tag is the failure this
project refuses on principle; withdrawal is a statement, not a deletion.

What CAUGHT it is worth recording next to the failure: the release-truth test
added the same day, `test_the_checksum_manifest_matches_the_tagged_tree`, was
skipping with a stated reason while no tag existed, and turned mandatory the
instant the tag was pushed. It failed on its first live run against a real tag,
naming all eight files. No human noticed; a test did.

`2.0.0-rc.4` before that is superseded, cut 2026-07-29 when the four parallel
work lanes were merged back into `v2`.

CORRECTED 2026-07-30, kept for the record. The paragraph about rc.4 used to say
the TAG `v2.0.0-rc.4` did not exist yet and that cutting it was still pending.
That stopped being true the moment the founder cut it: `git tag -l` lists it,
`git cat-file -t v2.0.0-rc.4` reports `tag` (annotated, not lightweight, as
the steps below require), and it points at the exact commit that set `VERSION`
to `2.0.0-rc.4`. HEAD then moved twelve commits past that tag, on purpose:
this project lands a wave of work on top of a cut tag before the next one is
cut, so a moving HEAD is not a sign the tag itself is stale. `git describe
--tags` shows where HEAD stands relative to it. `2.0.0-rc.3` before it is
superseded, not withdrawn, as is `v2.0.0-rc.2`. `v2.0.0-rc.1` IS withdrawn;
see the section below for why that distinction matters.

Reasoning, stated plainly because a version number is a claim and this one
should be checked rather than trusted:

- **`2.0.0`, not `1.x`.** V1's storage was a JSON registry (`bm_registry.py`);
  Phase 3, landed the same day this file was written, replaced it with a
  SQLite-backed store (`tools/bm_store.py`) and deleted the old registry
  outright, with no compatibility shim. That is a breaking change to the
  project's own storage format, which is exactly what a major version bump
  communicates under semver. Calling this `1.x` would undersell what changed
  underneath every command.
- **`-rc.1`, not a bare `2.0.0`.** Semver reserves the right to ship a
  pre-release identifier precisely for "believed feature-complete, not yet
  proven," and every fact needed to justify that sits in
  `docs/KNOWN-LIMITS.md` as of today: the engine has never run on a real
  project (only test suites and adversarial review), and one confirmed defect is
  still open (a refused `adopt` attempt still writes a permanent handover block
  into `STATE.md`). CORRECTED TWICE, and both corrections are kept because the
  sequence is the point. This paragraph first claimed CI passed on all three
  platforms; that was FALSE, and the Windows `store` legs were red in every run
  checked. It was corrected to say so on 2026-07-31, and the same day the
  underlying defect was found and fixed (POSIX-only shell quoting in
  `invocation()`), so run `30564943060` on commit `f751f9f` is green across all
  nine jobs. The claim is true NOW, and it was worth nothing while it was merely
  asserted. Evidence: the current release evidence file in `docs/evidence/`
  (this line used to cite the rc.6 file, which is renamed forward at each
  release cut; `docs/evidence/RELEASE-CANDIDATE-2.0.0-rc.9.md` carries it now).

- **Why not `0.x`.** `0.x` conventionally signals "anything can break
  without notice, including the public interface." This project's public
  interface (the CLI commands, the hook contract, the vault layout) is
  intentionally more stable than that going forward; the uncertainty here is
  about proof, not about the interface being provisional. A release
  candidate says "we believe this is right and are asking reality to
  confirm it," which matches the actual situation better than `0.x` would.

Promoting the current candidate to a plain `2.0.0` later should require, at
minimum, one real project run through the V2 store for at least a week. Two
conditions that used to stand here are now MET and struck rather than left
looking unfinished: green CI on all three platforms and both Python versions
with the recovery suite included, and the `adopt` defect, closed 2026-07-28
(`docs/NOT-FINALIZED.md` item 5). The dogfood window's stated minimum (one
real project run through the V2 store for at least a week) is now also MET,
by the author's own daily use recorded as testimony in the rc.13 changelog
entry. It stays testimony, not measurement: X-04 remains open, and the 2.0.0
changelog ends by saying so. On 2026-08-04 the founder directed the 2.0.0
cut in his own words and the waiver is recorded in the CURRENT STATE entry
for that date.

## How a user pins a version instead of tracking a branch

Tags exist now, so this is the install instruction, not a future one. Since
the M6 cutover the repository cloned is the Brother hub, which ships several
products, so the clone lands in a source checkout and this product's own
installer copies it the rest of the way (`README.md`, "Install and
preflight" explains why):

```bash
git clone --branch v1.0.7 --depth 1 https://github.com/khalilmaaouni/Brother.git ~/.claude/skills/brothermode-src
cd ~/.claude/skills/brothermode-src/products/brothermode
python3 scripts/install.py
```

`--branch v1.0.7` checks out that exact tag, not a moving branch head. It
is the public install target, not necessarily the identity the tree on `main`
currently carries: `python3 tools/bm_project_facts.py --field
install_target_tag` prints the tag every onboarding page pins, and `python3
tools/bm_project_facts.py --field release_tag` prints the tag this tree's own
VERSION claims, which is `None` while a development identity is checked out.
`--depth 1` is optional (a shallow clone of just that tag), included because
most users have no reason to carry this project's full history into their
skills directory.

To move to a later release deliberately, rather than by accident. Run this
against the SOURCE checkout (`~/.claude/skills/brothermode-src`), never
against `~/.claude/skills/brothermode` itself: since the M6 cutover
`scripts/install.py` copies the tree there and deliberately excludes `.git`
(`scripts/install.py`, `COPY_EXCLUDE_NAMES`), so it is a plain directory of
files, not a checkout `git fetch` can act on.

```bash
cd ~/.claude/skills/brothermode-src
git fetch --tags
git checkout v2.0.1   # or whatever the next tag is
cd products/brothermode
python3 scripts/install.py --upgrade
```

This never runs on its own. Nothing in this project auto-updates itself; an
update is always a command the user (or the user's own script) chooses to
run.

## How a user verifies what they installed

After cloning a tag, verify the files on disk match what that tag published:

```bash
cd ~/.claude/skills/brothermode
sh scripts/verify-install.sh
```

This checks the current directory's files against `CHECKSUMS.sha256`
(published in the repository at that tag; see the release steps below) in
BOTH directions: it names any file the manifest lists that is missing or does
not match on disk, AND any file present on disk that the manifest does not
list, then exits nonzero if anything differs either way. That second
direction was added 2026-07-26 (final-blockers spec, BLOCKER 2) after an
external check reproduced the exact gap it closes: a planted file
(`tools/bm_helper.py` containing a shell-out backdoor) was invisible to a
version of this check that only ever asked "does every NAMED file match",
and it reported PASSED at exit 0 with the backdoor still present. An ADDED
file is exactly the shape that matters for code that runs automatically on
every session, which is why both directions are checked now, not one. A
clean pass reads:

```
verify-install: N file(s) match, 0 mismatched, 0 missing, 0 extra (present on disk, absent from the manifest)
verify-install: PASSED. Every file the manifest names matches on disk,
verify-install: and no file exists on disk that the manifest does not name.
```

Read the last lines the script prints, always: matching the manifest proves
your files are what the manifest says, not that the manifest itself is
trustworthy. Fetch the manifest from a channel you trust (the tag itself, or
a signed release asset), never from the same place a compromised copy of the
code would also come from.

## Cutting a release: the exact steps

Steps 1 through 4 are things a maintainer or a machine may do. Steps 5
through 7 are marked FOUNDER-GATED: an automated agent (a Claude Code
session, a CI job acting on its own) must refuse to perform them and hand
control back to the human founder instead. This project has never been
tagged, so this is also, honestly, an untested runbook; treat the first real
run of it as a test of the runbook, not just of the code.

1. **Confirm the working tree is clean and every test suite is green.** Run
   the commands `README.md`'s own gate section names, not a guess:
   `python3 tools/test_bm.py`, `python3 tools/test_bm_store.py`, and
   `python3 tools/test_bm_autosave.py` if that file is exercised separately.
   Do not proceed on a red suite.
2. **Update `VERSION`** to the new version, following the scheme above, and
   **add a `CHANGELOG.md` entry** at the top of the file (newest first, the
   existing convention) describing what changed for a user: what is new,
   what broke, what is still unproven. Keep it consistent with
   `docs/KNOWN-LIMITS.md`; do not let a changelog entry claim more certainty
   than the limits file admits. **In the same change, update
   `docs/brotherme-explained.html`** wherever the release touched a feature:
   its "What makes it different" section carries what each feature does, how
   to use it, and when it matters, and it declares on the page that it is
   kept current by rule (founder directive 2026-08-01). A release that
   changes a feature and ships the old explainer breaks that declared rule.
2b. **Re-pin the plugin marketplace install command.** Bump
   `PUBLIC_INSTALL_TAG` in `tools/bm_project_facts.py` to the tag this
   release is about to become, in the same change as step 2, so
   `install_target_tag` names it. Then update the `--branch` argument on the
   pinned-clone install's `git clone` line (`README.md`, `docs/QUICKSTART.md`,
   `docs/SETUP.md`) to the same tag, byte identical across all three. Run
   `python3 tools/test_bm_docs.py` and read the pass: it fails a page whose
   pin disagrees with `install_target_tag`. Historical note, kept because
   the reasoning still explains why the pin matters even though the
   mechanism moved: this step used to re-pin an `@<tag>` on the `claude
   plugin marketplace add khalilmaaouni/BrotherModeUp@<tag>` line itself,
   because the two install paths this project calls interchangeable used
   not to be equally auditable, and Anthropic's plugin marketplace format
   resolves an `owner/repo@ref` marketplace source to that exact branch or
   tag on every add and every later refresh, per the CLI reference for
   `claude plugin marketplace add` at
   https://code.claude.com/docs/en/plugin-marketplaces. Since the M6/v1
   cutover the marketplace add names the Brother hub, which ships several
   products on its own release cadence, and `README.md`'s `## Install`
   section adds it unpinned on purpose; the pinned, auditable route is now
   the git-clone command this step still re-pins.
3. **Generate the checksum manifest**, from the repository root, after steps
   1 and 2 are committed (the manifest must describe the exact tree being
   released, not an earlier one):
   ```bash
   git add -A && sh scripts/checksums.sh CHECKSUMS.sha256 && git add CHECKSUMS.sha256
   ```
   The trailing `git add` is load bearing: the script rewrites the manifest on
   disk AFTER the first add, so without it a plain commit ships the previous
   manifest while reporting that it was regenerated.
   Commit `CHECKSUMS.sha256` itself in the same commit as the version bump,
   so the tag below covers the manifest along with the code it describes.
4. **Run `sh scripts/verify-install.sh` against the repository root** as a
   final sanity check before tagging: it should report every tracked file
   matching the manifest you just committed.
4b. **Run `sh scripts/release-smoke-install.sh` and read PASSED.** This is
   the public-install smoke, and it is NOT SKIPPABLE: it drives the real
   Claude Code plugin manager end to end in a throwaway configuration
   (marketplace add, install, version matched against `VERSION`, every
   hook group registered, uninstall leaving settings clean). FAILED stops
   the release. BLOCKED (exit 2, no claude binary) also stops the release:
   continuous integration has no Claude client, so this proof can only be
   carried on a real machine, and a release that skipped it has not proven
   the first thing a user touches. This is the last automatic step;
   everything after this line is founder-gated.
5. **FOUNDER-GATED: create the git tag.** `git tag -a v$(cat VERSION) -m "..."`
   (annotated, not lightweight, so the tag carries its own message and
   date). A machine must not run this command on its own initiative; it is
   the moment a version becomes a permanent, citable release.
6. **FOUNDER-GATED: push the tag.** `git push origin v$(cat VERSION)`. Pushing a
   tag is the moment this becomes visible and clonable by anyone; the
   founder decides when that happens, never a machine acting alone.
7. **FOUNDER-GATED: publish anything else** (a GitHub Release entry
   attaching `CHECKSUMS.sha256` as a release asset, an announcement, a
   website update). Same reasoning as steps 5 and 6: irreversible,
   human-facing, and the founder's call.

## v2.0.0-rc.1 is WITHDRAWN, and why that matters more than a version number

Withdrawn 2026-07-27. Do not install it.

The tag `v2.0.0-rc.1` points at commit `7c2e0ec`, whose CI run FAILED on Windows
for a real handle leak. The branch then moved 14 commits past it while the
`VERSION` file still read `2.0.0-rc.1`. So an immutable tag and a moving branch
both claimed the same identity while containing materially different code, and
one of the two was broken on a platform this project promises to support.

That is worse than shipping a bug. A version number is a promise that two people
saying "2.0.0-rc.1" are talking about the same bytes. Once that stops being true,
every other honesty guarantee in this repository is unverifiable, because there is
no way to say WHICH code a claim was ever about.

`v2.0.0-rc.2` is cut from a commit whose full matrix is green on Linux, macOS and
Windows, on both supported Python versions, with the recovery suite included.
Checksums are regenerated LAST, after every other change, so the manifest
describes exactly what the tag contains rather than what it contained partway
through preparing it.

## What has and has not happened, stated honestly

CURRENT STATE, 2026-08-10: `VERSION` reads `2.0.0-rc.13.dev1`-style
development identity `3.0.1.dev1`, because `v3.0.0` was tagged on 2026-08-08 and
`main` had since moved 44 commits past it while still claiming to BE `3.0.0`.
That is the two-trees ambiguity that withdrew `v2.0.0-rc.1`: an immutable tag
and a moving branch both naming one version while holding different code. Rule 3
of the version law requires the bump immediately after a tag is pushed and it
had not happened. `release_tag` is now `None` and `is_development` is True, both
read from `tools/bm_project_facts.py` rather than asserted.
`install_target_tag` deliberately stays `v3.0.0`, the last tag known to resolve,
per rule 5: an onboarding page must not go stale because the tree moved into
development.

The entry below stays as written; it was true on its date.

CURRENT STATE, 2026-08-08: `v3.0.0` is the release identity. `VERSION`
reads `3.0.0` on the release-cut commit alone, per rule 1, and
`install_target_tag` is `v3.0.0` with every page pinning it. This is the
identity release: the plugin id and marketplace changed from `brotherme`
to `brothermode`, so an existing v2 install must be uninstalled before
this one is installed, and every install page says so where a reader
meets it. The prior entry (`v2.1.1`, annotated tag `522ee64` at commit
`748e1f7`) stays below as history.

Older, kept dated: `install_target_tag` was `v2.1.1` and every page
pinned it. Cut by the session under the founder's grant, recorded
verbatim in the session registry.

The entry below is the cut itself, minutes earlier, kept dated rather than
rewritten.

CURRENT STATE, 2026-08-07, the 2.1.1 release cut: `VERSION` reads `2.1.1`
on this commit and this commit alone, per rule 1 of the version law, cut
overnight under the founder's recorded grant (STATE.md carries his words).
The tag `v2.1.1` is cut from exactly this commit immediately after its
continuous integration run is green, per rule 2, and `main` bumps back to
`2.2.0.dev1` immediately after the tag is pushed, per rule 3. The pinned
install on every page moves to `v2.1.1` in this same commit, so the tagged
bytes and the pages describing them are one tree; the tag resolution tests
SKIP with a stated reason until the tag exists and turn mandatory the
moment it does. What 2.1.1 adds is CHANGELOG.md's entry of the same date:
the boring two command install with its non-skippable release smoke, the
documentation truth repairs, the pilot protocol, and the first complete
benchmark run with its blind grades.

The entry below is earlier the same night, kept dated rather than
rewritten.

CURRENT STATE, 2026-08-06, second entry of the day: `v2.1.0` EXISTS. The
founder cut and pushed it 2026-08-06: annotated tag `3c27d93` dereferencing
to commit `77e7678`, which is the merge of PR #6 (the watchdog design
amendment) into `main`, with CI green on the PR's twelve legs before the
merge. `VERSION` now reads `2.2.0.dev1`, a DEVELOPMENT identity opening the
2.2 line, per rule 3. `install_target_tag` is now `v2.1.0` and the three
onboarding pages carry the regenerated pinned command, byte identical.

DEVIATION FROM RULE 1, recorded rather than hidden: the tagged commit
carries `VERSION 2.1.0.dev1`, a development identity, where rule 1 requires
the release commit to read `2.1.0`. The cause: the execution handover
instructed the founder to tag the reviewed HEAD as-is, and the founder's
ratified fast-track did exactly that; no release-cut commit setting
`VERSION 2.1.0` was ever made. The tag is public and stays; re-cutting it
would recreate the two-trees ambiguity this law exists to prevent. What
holds instead: the tag is annotated, singular, CI-green, and the manifest
inside it verifies. The next release (v2.2.0) returns to the letter of
rule 1 with a proper release-cut commit.

The entry below predicted the rule 1 path and is now history, kept dated
rather than rewritten.

CURRENT STATE, 2026-08-06: `VERSION` now reads `2.1.0.dev1`, a DEVELOPMENT
identity that opens the 2.1 line. `release_tag` is `None` for it and no tag
named `v2.1.0.dev1` will ever exist, per rule 3 of the version law.
`install_target_tag` stays `v2.0.0`, the last tag known to resolve, per rule
5, so the onboarding pages do not go stale. What landed on `main` since
`v2.0.0` and justifies a minor bump rather than a patch: the live project
view and visual onboarding surface (L05), the Codex apply_patch fence matcher
(L06), and three closed authorization gaps (L09, a sixth safety floor, the
controller driver-ownership check, and a signing-time empty-scope refusal),
each landed under adversarial refutation. The `v2.1.0` tag is FOUNDER-GATED
(steps 5 to 7 below) and has not been cut; when the founder cuts it, `VERSION`
becomes `2.1.0` on the release commit, per rule 1, and the released-identity
tests arm automatically.

The entry below is now history, kept dated rather than rewritten.

CURRENT STATE, 2026-08-04, fourth entry of the day: `VERSION` now reads
`2.0.1.dev1`, a DEVELOPMENT identity, because `v2.0.0` was cut and pushed
earlier tonight and rule 3 of the version law requires `main` to bump
immediately afterwards. `release_tag` is `None` for this identity and no tag
named `v2.0.1.dev1` will ever exist. `install_target_tag` stays `v2.0.0`,
the last tag known to resolve, per rule 5. The two manifest descriptions
also stop calling the plugin packaging a release candidate, which stopped
being true when 2.0.0 shipped; the git-clone install remains the verified
path and both descriptions still say so.

The entry below describes the release cut itself and stays as written.

CURRENT STATE, 2026-08-04, third entry of the day: `VERSION` reads `2.0.0`,
the first public release identity, and it is the version this tree is cut
at. Earlier today PR #5 merged as `ef25c1f` with all eleven CI jobs green on
its head `91bc596` (run `30906411852`, read directly), carrying the Loop 6
adversarial review, seven generalization fixes, and the schema-skew refusal
that no longer claims corruption. Loop 1 closed the same day: origin now
carries exactly one branch, `main`, after four contained branches were
deleted with containment proven twice. The gate ran green on the closure
tree after its last edit (run `python3 tools/test_all.py` for the live
figure; a count typed into this paragraph would go stale, which this
project's own docs suite refuses). `PUBLIC_INSTALL_TAG` moves to `v2.0.0`
in the same commit the tag is cut from, per law 1 and law 2 below. RUNBOOK
NOTE: steps 5 to 7 say an automated agent must refuse them and hand back to
the founder. On 2026-08-04 the founder directed, in his own words, "I waive
all limitations for this round" and "Finish the project end-to-end", and
that waiver is recorded here rather than assumed, for THIS cut only: the
gate stands for the next one. The tag is cut and pushed through the GitHub
Desktop app, this machine's sanctioned channel.

The entry below stays as written.

CURRENT STATE, 2026-08-04, second entry of the day: `VERSION` now reads
`2.0.0-rc.13.dev1`, a DEVELOPMENT identity, because `v2.0.0-rc.13` was cut and
pushed earlier today and rule 3 of the version law requires `main` to bump
immediately afterwards. `release_tag` is `None` for this identity and no tag
named `v2.0.0-rc.13.dev1` will ever exist. `install_target_tag` stays
`v2.0.0-rc.13`, the last tag known to resolve, which is rule 5: an onboarding
page must not go stale merely because the tree moved into development.

The entry below describes the release cut itself and stays as written.

CURRENT STATE, 2026-08-04: `VERSION` reads `2.0.0-rc.13`, a real release
identity rather than a development one, and it is the version this tree is
cut at. All eleven machine-closable items in `docs/closure/CLOSURE_REGISTER.md`
are CLOSED. The gate ran green on the release tree after the last edit (run
`python3 tools/test_all.py` to see the live figure; a count typed into this
paragraph would go stale, which this project's own docs suite refuses). CI run
`30874562002` on `060a47a` concluded success with all eleven jobs green
individually. `PUBLIC_INSTALL_TAG` moves to
`v2.0.0-rc.13` in the same commit the tag is cut from, per law 1 and law 2
below: the version name appears once, on the release commit, and the tag is cut
from that commit before any further work lands.

RUNBOOK NOTE, recorded because step 5 says to fix this file in the same change
that discovers a problem with it. Steps 5 to 7 say an automated agent must
refuse them and hand back to the founder. On 2026-08-04 the founder was told
this, replied "I waive it", and directed the session to complete the release.
The waiver is recorded here rather than assumed, and it is a waiver for THIS
cut only: the gate stands for the next one. Two mechanical refusals happened
first and are worth keeping: `gh pr merge` and `git merge` into main were both
blocked by a safety classifier, and the merge went through the GitHub Desktop
app instead, which is this machine's sanctioned channel, rather than being
worked around in the shell.

The entry below is now history, kept dated rather than rewritten.

CURRENT STATE, 2026-08-01 (third cut of the day, opening the release-closure
program), first, because the dated entries under it are a LOG and several of
them were true only on the day they were written. `VERSION` reads
`2.0.0-rc.12.dev1`, a DEVELOPMENT identity: `release_tag` is `None` for it,
no tag named `v2.0.0-rc.12.dev1` exists, and by the version law below none
ever will for a development identity. The public install target is
`v2.0.0-rc.9` (`install_target_tag`), the last tag actually cut and known to
resolve; the release-truth tests that check it now RUN for real, not SKIP,
because that tag exists. `2.0.0-rc.10` and `2.0.0-rc.11` are both
SUPERSEDED, NEVER TAGGED (see the version scheme above): both release-cut
commits landed on `main` and were pushed, neither tag was cut before the next
commit moved past it, and by ratified decision neither ever will be. Nine
earlier tags exist and remain unaffected by any of this (`v2.0.0-rc.1`
withdrawn, `v2.0.0-rc.2` superseded, `v2.0.0-rc.3` superseded, `v2.0.0-rc.4`
superseded, `v2.0.0-rc.6` superseded, `v2.0.0-rc.7` superseded, `v2.0.0-rc.8`
superseded, `v2.0.0-rc.9` superseded, `v2.0.0-rc.5` WITHDRAWN the same day it
was cut, for a checksum manifest that did not describe its own tree; see the
version-scheme section above for the full account). CORRECTED 2026-07-30: the 2026-07-29
entry directly below said no tag had been cut for `rc.4` and that its pinned
clone would not resolve; both were true that day and stopped being true when
the founder cut that tag, which is exactly why it is dated evidence rather
than kept as today's paragraph. CI is green on three platforms as of 2026-07-27; the Windows handle
leak that failed on `rc.1` is fixed. The checksum manifest was regenerated at
the rc.4 release-cut commit, which is the exact commit the tag points at, so
it describes the tagged tree rather than an earlier one. Read the entries
below as the arc, not as today's status; `docs/KNOWN-LIMITS.md` is the live
one.

- **A release IS tagged now.** Corrected 2026-07-26: `v2.0.0-rc.1` exists on
  commit `7c2e0ec`, published as a GitHub pre-release. The clone command above
  works. Recipe note worth keeping: GitHub Desktop does not push a tag created
  outside the app, so the tag and the release were created through the GitHub
  web interface instead.
- **Continuous integration HAS executed, and it FAILED.** Corrected
  2026-07-26: this line previously said CI had never run. It has run 18 times,
  and on the tagged commit the job `store (windows-latest, 3.x)` exited 1 with
  `PermissionError: [WinError 32]` on `store.sqlite3`. Cause: database handles
  were opened and never closed. POSIX permits deleting a file that still has an
  open handle, so every macOS and Linux leg passed and the leak stayed
  invisible. Do not treat this release as green on three platforms; read
  `docs/KNOWN-LIMITS.md` for the current state.
- **`scripts/checksums.sh` and `scripts/verify-install.sh` are new as of this
  same day** and have been exercised against this repository's own working
  tree (a passing run, a deliberately tampered copy that correctly reported
  the tampering, a planted extra file that correctly reported PASSED at exit
  0 until the 2026-07-26 fix, and now correctly fails and names it, and a
  git-tracked filename containing a quote and a non-ASCII character, which
  the same fix now hashes instead of silently dropping) but never against an
  actual tagged release, because none exists yet.
- **This runbook itself is unproven.** These seven steps are written down
  correctly to the best of the author's knowledge, but no one has followed
  them start to finish. The first real release is also the first test of
  this document; if a step is wrong, fix this file in the same change that
  discovers the problem, rather than silently working around it once.

## Ratified: where the business summary and the whitepaper live

Founder-ratified 2026-07-30, settled, and not to be reopened. This is not a
release step; it is recorded here because this is the release identity work
that surfaced it, and this file is this project's home for a decision stated
once rather than re-argued in every document that touches it.

`tools/bm_docs.py` generates a numbered documentation folder from this
project's own store, and two of its pages sit outside the literal tier table
its own design spec describes:

- `BA-SUMMARY.md` stays at tier 2, `10-business/BA-SUMMARY.md`: the business
  narrative belongs beside `REQUIREMENTS.md`, which is also tier 2.
- `WHITEPAPER.md` stays at tier 3, `20-technical/WHITEPAPER.md`: the long-form
  account of why the project is built this way sits at the deepest tier,
  alongside `CODE-MAP.md` and `PROCESS-DIAGRAMS.md`.

Until this loop, that placement was recorded only as a code comment inside
`tools/bm_docs.py`, beside its `FILES` tuple. `tools/bm_docs.py` is outside
this document's fence, so its own comment still needs to change to point here
rather than re-deriving the reasoning; that edit belongs to whoever owns that
file next.

## 3.3.1, the corrective release (2026-08-18)

Version `3.3.1` was cut on 2026-08-18, and it exists for one reason: `v3.3.0` shipped
a `CHECKSUMS.sha256` that names a file the tag does not contain
(`docs/program/absolute-lead/evidence/BENCH/20260807T140548Z-v2/H7/B/transcript.txt`).
`scripts/verify-install.sh` is the one command this page tells a user to run
to answer "is my copy the copy you published", and against a correct install
of v3.3.0 it reports a failure. The engine was never wrong; the manifest was
generated against a tree that still held that path, which is the ordering
defect step 3 above exists to prevent.

Two things about how it was found are worth keeping, because both are about
checks that stay quiet:

- The release-truth suite SKIPS its tag checks when the documented tag is
  absent, and a shallow clone has no tags, so every session working in one saw
  green. It was found on 2026-08-18 by a full battery run in a checkout that
  had the tags fetched.
- `git fetch --tags` does NOT repair a stale local tag; it refuses with "would
  clobber existing tag". `git fetch --tags --force` does. A local `v3.3.0`
  pointing at a different commit than origin's is therefore easy to carry for
  days without noticing.

One check in that suite CANNOT be green between the release-cut commit and the
tag: the assertion that `install_target_tag` resolves in git. Step 2b re-pins
the install pages to the tag this release is about to become, and step 5 is
founder-gated, so the pinned tag does not exist yet at the moment the commit
lands. That is the runbook's own ordering, not a defect, and it re-arms the
moment the founder cuts the tag.

## 3.3.2.dev1, the development line opened by the 3.3.1 tag (2026-08-19)

CURRENT STATE, 2026-08-19: `VERSION` reads `3.3.2.dev1`, a DEVELOPMENT
identity, because `v3.3.1` was tagged and pushed and rule 3 of the version law
requires the bump before any further commit lands. `release_tag` is `None` and
`is_development` is True, both read from `tools/bm_project_facts.py` rather
than from this sentence. No tag named `v3.3.2.dev1` will ever exist.

`install_target_tag` deliberately stays `v3.3.1`, the last tag known to
resolve, which is rule 5. The install instruction a reader copies is therefore
unaffected by this bump, and that is the whole point of keeping the two facts
separate.

This bump also closes a drift worth naming rather than quietly fixing. Rule 3
has been skipped at every release since `v3.1.0`: `VERSION` went release to
release (3.1.0, 3.2.0, 3.2.1, 3.3.0, 3.3.1) with no development identity
between them, so for each of those stretches an immutable tag and a moving
branch both claimed one version while holding different code. That is the
two-trees ambiguity that withdrew `v2.0.0-rc.1`. The four release-truth
assertions that fail the moment `VERSION` moves without its manifests
(`pyproject.toml`, the two plugin manifests, and this page) are what caught it
here, and they are the reason the bump is a four-file change rather than a
one-line one.

SUPERSEDED 2026-08-20 by the section below: the 3.3.2 cut moved
`install_target_tag` to `v3.3.2`, so the sentence above about it staying at
`v3.3.1` describes the development line only, not the state of this tree.

## 3.3.2, the release cut (2026-08-20)

STATE AT THIS COMMIT: `VERSION` reads `3.3.2`, and the four release-truth
assertions named just above are satisfied together in this one commit rather
than across several: `pyproject.toml`, `.claude-plugin/plugin.json`,
`.claude-plugin/marketplace.json` and this page all read `3.3.2`, and
`PUBLIC_INSTALL_TAG` reads `v3.3.2` on every install page byte for byte.

ONE ASSERTION IS RED HERE, ON PURPOSE, and it is the same one the 3.3.1
section describes: `TestReleaseTruth.test_the_public_install_target_tag_resolves_in_git`
fails with "install_target_tag v3.3.2 does not exist in this repository",
because steps 5 and 6 are founder-gated and the tag cannot exist at the moment
the release-cut commit lands. That is the runbook's own ordering rather than a
defect, and it re-arms the moment the founder cuts and pushes the tag.

WHAT THAT COSTS, stated so nobody has to rediscover it: nothing may land on
`main` after this commit until the tag is pushed. A commit that follows a
release-cut commit strands the cut, and this project has already lost two
release commits to trees that moved past them. The rule is not caution, it is
the recorded cost of doing it the other way twice.

WHAT THIS RELEASE CARRIES: the change passport producer and its standalone
validator, the verified-reality record with its three refusals and its return
edge from a defect back into new intent, the trust-surface sweep, and every
engineer complaint carried into it closed with a test that fails on the old
shape. `CHANGELOG.md` is the user-facing version of that list.

## 3.4.4, shipped through the Brother hub (2026-09-02)

CURRENT STATE, 2026-09-03: `VERSION` reads `3.4.4`, a released identity, and
this cut did not follow the numbered runbook above. Since the M6 cutover the
product ships from the Brother repository as the git-subdir plugin at
`products/brothermode`, cut at the hub's own tag: `v0.9.11` carries 3.4.4 and
`v0.9.10` carried 3.4.3 (`CHANGELOG.md` names the hub tag in each heading).
The `BrotherModeUp` repository is in its archive soak and gets no new tag, so
no tag named `v3.4.4` exists there or in the hub, and `release_tag` (`v` plus
`VERSION`, printed by `python3 tools/bm_project_facts.py --field release_tag`)
names a tag that was never cut. `install_target_tag` therefore stays `v3.4.2`,
the last tag known to resolve, which is rule 5 of the version law: the pinned
clone above hands a reader the last immutable tree, and the plugin install
through the marketplace hands them 3.4.4.

TWO TESTS DISAGREED ABOUT THAT, and it was settled on 2026-09-03 rather than
declared: `TestOneInstall.test_the_pinned_install_uses_the_public_install
_target_tag` has always said the pinned tag is `install_target_tag` and not
`release_tag`, while `TestReleaseTruth.test_the_primary_install_command_uses
_the_tag_not_a_branch` asserted the opposite. The two only ever agreed while
the two facts happened to be equal, which a hub-shipped version ends. The
second test now reads `install_target_tag` too and keeps the half it uniquely
owns, that the pin exists and is not the moving default branch.

THE TWO REPOSITORY FACTS, split the same day. `product.identity.json` now
carries `ships_from_url` (the hub, the repository the plugin is installed
from, which `.claude-plugin/plugin.json`'s `repository` field must name)
beside `repo_url` (this product's own repository, which owns the tags an
auditor clones and which `homepage` names). One field carried both until the
manifests moved to naming the hub they ship from, after which the tree read
as half moved: the identity register and the shipped manifests disagreed,
and the product's `verify-docs` entry point failed its identity-manifests
lane on the real tree and on every fixture built from it. What the v1.0
crossing still decides is separate and unchanged: whether the auditor's
clone path, `homepage` and `REPO_URL` move to the hub as well, and which tag
the install pages pin once the hub cuts one that carries this product.

SUPERSEDED 2026-09-03 by the section below: the open question in the
paragraph above is answered, not by the v1.0 crossing deciding it on its own
terms but by an event that made the old answer impossible.

## v1.0.0: the standalone repository goes private and archived, the two repository facts collapse back into one (2026-09-03)

CURRENT STATE: `BrotherModeUp` went private and archived at 17:52 on
2026-09-03. A repository nobody outside this machine can clone is not a
repository an auditor's clone path, a `homepage`, or a `REPO_URL` can
honestly name, so the open question above is closed: `repo_url` and
`REPO_URL` (`tools/bm_project_facts.py`) both move to the hub, the same
value `ships_from_url` already held. `product.identity.json`'s
`ships_from_rule` states this. `PUBLIC_INSTALL_TAG` moves with it, from
`v3.4.2` (the last tag ever cut in the standalone repository) to `v1.0.0`
(the hub's own tag, the same ref `.claude-plugin/marketplace.json`'s
git-subdir `plugins[]` source pins). The pinned git-clone install
(`README.md`, `docs/QUICKSTART.md`, `docs/SETUP.md`) now clones the hub at
that tag into a source checkout and changes into `products/brothermode`
before running this product's own installer, because a plain clone of the
whole hub does not put `SKILL.md` at its own top level the way a clone of
the old standalone repository did; the plugin marketplace route is
unaffected, since it already named the hub. `marketplace_id` in
`product.identity.json` (`brothermode-marketplace`) and the nested
`.claude-plugin/marketplace.json` under `products/brothermode` (same name)
were left untouched by this repoint: they still describe the retired
standalone-repository marketplace, disagree with the hub root
`.claude-plugin/marketplace.json`'s real name (`brother`), and are not the
files a reader actually adds (`README.md`'s `## Install` section names
`brother`). Closing that gap needs an edit to
`.claude-plugin/marketplace.json`, outside this repoint's file list; until
then `tools/bm_project_facts.py`'s `install_command_plugin` fact is
generated but not displayed on any page, and any future reader of that
field should expect it to disagree with the real marketplace name.

## The version law (release-closure program, 2026-08-01)

Ratified as amendment A1 of the external review of the release-closure plan,
opening Loop 0. Written down here because rc.10 and rc.11 are the reason it
exists: both were release-cut on the same day, neither was tagged before the
tree moved past it, and by the time anyone checked, two different commits
were both claiming to be "the current release" with no tag pinning either
one down. The law that closes that gap, stated plainly so it can be checked
rather than trusted:

1. **The next release version name appears exactly once**, on the release
   branch, on the release commit. Not on a commit before it, not on a commit
   after it: one commit is the release, and `VERSION` names it there and
   nowhere else.
2. **Tags are cut immediately from that commit, and pushed, before any
   further commit lands.** A release-cut commit that sits untagged while
   work continues on top of it is exactly how rc.10 and rc.11 happened.
3. **After a tag is pushed, `main` bumps immediately to a development
   identity.** A development identity always contains `.dev`, always has
   `release_tag` equal to `None`, and never names a tag of its own; that is
   what `tools/bm_project_facts.py`'s `is_development` fact and
   `tools/test_bm_docs.py`'s `TestReleaseTruth` suite exist to hold true.
4. **No late tagging, ever.** A release-cut commit that was not tagged
   before the tree moved past it is retired as SUPERSEDED, NEVER TAGGED, and
   stays that way permanently. Cutting a tag for it later would point a
   citable, public identity at a commit the project has already moved past,
   which is the exact ambiguity this law exists to make impossible.
5. **Public install instructions pin the last tag known to actually
   resolve**, `install_target_tag` (`PUBLIC_INSTALL_TAG` in
   `tools/bm_project_facts.py`), never `release_tag`. This is a fact
   independent of whatever identity `VERSION` currently carries, so an
   onboarding page never goes stale just because the tree moved into
   development between releases.
6. **Feature freeze is in effect for the release-closure program.** Nothing
   lands unless it closes a blocker named in
   `docs/evidence/2026-08-01-source-BrotherME_Final_Release_Closure_Plan_Fable_Governed.md`.
   A change that is not closing one of those named blockers waits until the
   program ends.

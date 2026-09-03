# Rollout: staged adoption for a client organization

This page is for an organization bringing BrotherSBE into its own repositories,
not for this repository's own release (that is `docs/RELEASE.md`). It answers
four questions in order: how to turn this on without betting a merge queue on
day one, who owns a fix when something is wrong, how an upgrade and a rollback
actually work, and what this release does not yet claim, stated plainly rather
than left to be discovered later.

## Staged rollout

### Stage 0: shadow mode

Copy `.github/workflows/brothersbe-gates.yml` into the target repository and
let it run on pull requests **without making it a required check.** It
reports on every PR; nothing blocks a merge yet. This is the stage that
exists because of what INTERNAL-EVAL means for a new estate's risk, stated
plainly: every gate in this project has been proven against BrotherSBE's OWN
repository's own fixtures and evals (`docs/KNOWN-LIMITS.md` names the
maturity of each one; the suite counts as this wave was written were
`tools/test_sbe.py` 47, `tools/test_sbe_evidence.py` 31,
`tools/test_sbe_bypass.py` 21, `tools/test_sbe_impact.py` 16,
`tools/test_sbe_fence_hook.py` 44, `tools/test_sbe_tasks.py` 15,
`tools/test_sbe_adopt.py` 17). None of that proves the gates behave the same
way against a codebase they have never seen. Shadow mode is how a client
estate becomes a second data point instead of a blind trust: watch a real
sprint or two of PRs, read every FAIL and every WAIVED design check that
`brothersbe-gates.yml`'s own summary step already surfaces
(`docs/KNOWN-LIMITS.md`, "a waiver is not a pass"), and decide from real
output whether the false-positive rate is one this org can live with as a
blocking gate.

### Stage 1: enforced on the copied workflow

Once shadow mode's output looks right, a repository admin makes
`BrotherSBE gates` a required status check in GitHub's branch protection
settings for the target branch. **This is a GitHub-side click BrotherSBE
cannot do and cannot verify** (see Blocked, below); nothing in this project
holds a token that could flip that switch or confirm it was flipped.

### Stage 2: the adoption kit

Run `sbe adopt .` (dry run first, then `--apply`) to detect the repository's
stack and propose `.brothersbe/policy.json` and a `.github/CODEOWNERS`
example. Every CODEOWNERS line ships the placeholder `@REPLACE-ME`; it
protects nothing until a human with repository membership replaces it, and
`docs/ADOPTION.md` says so before the checklist of what to click. This stage
is optional and can run before, during, or after Stage 1; nothing in Stage 1
depends on it.

**The gate at every stage above is a human, not a script.** Moving from one
stage to the next is a deliberate, founder-triggered decision, the same
discipline this repository's own `PUBLISH-CHECKLIST.md` holds itself to for
its own release ("Publishing is a deliberate, founder-triggered step.
Nothing here is pushed" until a person runs the checklist and clicks). Here,
"the founder" means whoever at the adopting organization owns the risk of a
blocking gate on their merge queue; nothing in this project can identify that
person for you, and nothing here advances a stage on its own.

## Support and ownership model

- **A bug in a gate or check itself** (a control that FAILs on code it should
  pass, or passes over evidence it never examined) is BrotherSBE's own
  maintainer's fix, upstream, not the adopting org's to patch locally. Report
  it with the failing case attached; a fix without a reproducing case is not
  verifiable and will not be treated as one (`docs/RELEASE.md`'s own
  changelog discipline: "a changelog line nothing can verify is a press
  release").
- **A false positive against this org's own codebase idiom** (a real,
  org-specific pattern the detectors were not built to recognize) is reported
  the same way, but the org's own team owns whether to waive it locally in
  the meantime, and only visibly: `.sbe-exempt`, never a silent skip, per the
  WAIVED discipline `docs/KNOWN-LIMITS.md` already states for this project's
  own repository. A waiver is not a pass, and `brothersbe-gates.yml`'s design
  waiver step already surfaces every one in the PR and the job summary so a
  human sees it.
- **Response expectation:** stated honestly, because inventing one here would
  be exactly the kind of unverifiable claim this whole project exists to
  catch: this repository commits to no SLA. There is no on-call, no ticket
  queue, no response-time promise. Set expectations with whoever adopts this
  before relying on it as a blocking gate for anything time-sensitive.
- **Where issues go:** wherever the repository this file ships from directs
  issues at adoption time; this page does not invent a channel that is not
  configured, because a placeholder contact is a guess dressed up as support,
  the same reasoning `docs/ADOPTION.md` gives for refusing to guess a
  CODEOWNERS username.

## Upgrade and rollback

Two scripts prove, mechanically, that installing, upgrading, and rolling back
never need anything beyond the tree itself:

- `scripts/test-install-artifact.sh [ref]`: archives the given ref (default
  `HEAD`) with `git archive`, extracts it into an empty directory, and checks
  that `scripts/verify-install.sh` PASSES and `bin/sbe doctor` reports no
  FAIL from that fresh copy alone. This is the check for the kill criterion
  this wave was cut against: **an install that needs a manual global settings
  edit fails this test outright**, because the whole install is "extract and
  run," nothing written outside the one directory the script creates.
- `scripts/test-upgrade-rollback.sh`: finds the most recent tag that is an
  ancestor of `HEAD`, installs it fresh, upgrades to `HEAD` fresh, then rolls
  back to that same tag fresh, verifying with `scripts/verify-install.sh` at
  every step. **Two tags exist**: `v1.0.0-rc.1` (commit `9011cf4a`, cut and
  published 2026-07-31, predating the guided skills) and `v1.0.0-rc.2` (cut
  2026-08-01 at the release that carries the guided skills, the beginner
  explainer, and the help map; it publishes with that release). The script
  finds the newest ancestor tag and exercises the real upgrade and rollback
  path, not the NO-DATA case; pin to `v1.0.0-rc.2` once it is published,
  because `v1.0.0-rc.1` misses the guided skills. `tools/test_sbe.py` does not assert this behavior (that assertion
  lives in the two scripts' own calibration, run directly).

Those two scripts REHEARSE the path; neither one performs it on your machine.
The command that does, for the recommended plugin install, is:

```bash
scripts/rollback-install.sh              # preview: the install it found, the
                                         # version now, the version it would
                                         # return to, every command
scripts/rollback-install.sh --apply      # performs it, then verifies
```

It does not assume where your installation is. It reads Claude Code's own
records (`~/.claude/plugins/installed_plugins.json` for the installed plugin's
location and version, `~/.claude/plugins/known_marketplaces.json` for the
repository that carries the release tags), moves that repository to the newest
earlier `v<number>` release tag that is an ancestor of the installed commit
(`--to <tag>` to name one yourself, still checked the same way), re-runs the
same `claude plugin marketplace add` plus `claude plugin install` pair
`install.sh` uses, then re-reads the first record and runs
`scripts/verify-install.sh` against the bytes that are now installed, not
against the source they came from. `--source-dir` names that source directory
by hand. `--install-dir` SELECTS, by path, which recorded installation to roll
back, and a path matching no recorded installation is refused rather than
adopted: it used to replace the directory while keeping the version, commit and
marketplace of whichever installation the record happened to name first, which
meant pointing it at an unrecorded directory rewrote that directory using a
different installation's identity and reported success. **It refuses rather
than guesses**: an installation with no earlier release in its history, no
reachable source, uncommitted changes in that source, or an `--install-dir`
this machine has no record of, is refused with the reason named and nothing
written,
because a rollback with no previous version to name is a guess dressed as an
undo. What it does not claim is in `docs/KNOWN-LIMITS.md` ("The recommended
install path has an undo now, and here is exactly how much of one").

For an adopting organization, the delta over the canonical clone is two
flags: pin a tag, and go shallow. Naming a specific version here would go
stale the moment the next one is cut (and, as of this writing, would go
stale immediately: see `docs/KNOWN-LIMITS.md`, "Only one tag is published on
origin"), so see which tags actually exist before you pin:

```bash
git ls-remote --tags <repository-url>
```

Then substitute the tag you saw for `<tag>` below.
[docs/RELEASE.md](RELEASE.md#pinning-an-install-to-a-release) documents the
rest (the same `scripts/verify-install.sh` check, run the same way, after the
install, after an upgrade, and after a rollback):

```bash
git clone --branch <tag> --depth 1 <repository-url> ~/.claude/skills/brothersbe
```

`verify-install.sh` PASSED means the files on disk match the manifest that
shipped with that ref, in both directions (nothing missing, nothing altered,
nothing extra). It does not prove the manifest itself is authentic; take the
manifest from the tag you trust, not from the same channel as the code
(`docs/RELEASE.md` states the same limit for this repository's own install).
Upgrading and rolling back are the same shape as the pin above: `git
ls-remote --tags` again to see what is newest, `git fetch --tags && git
checkout <new-tag>`, then `scripts/verify-install.sh` again.

## Blocked, verbatim

Four things this release does not do and does not claim to do. None of the
four is quietly implied anywhere in this project's docs; each is named here
because a limit that is only true and never stated is the same defect class
`docs/KNOWN-LIMITS.md` exists to catch.

- **Signed release.** A signed release is blocked on a key the founder holds
  and is never claimed here. Nothing in this repository signs a tag, a
  commit, or an artifact; `git tag -a` (below) is an annotated tag, not a
  signature.
- **Branch protection.** Nothing here can read, set, or verify GitHub branch
  protection, required status checks, or whether review from a code owner is
  required. All three are settings on GitHub's code review platform, not on a
  filesystem; `sbe adopt`'s own report calls this `UNVERIFIABLE-HERE`,
  unconditionally, because this tool holds no GitHub credentials and asks for
  none (`docs/KNOWN-LIMITS.md`, "The adoption kit proposes, and verifies only
  what a filesystem can answer").
- **`gh auth`.** This project never runs `gh auth login`, never holds a
  GitHub token, and never automates a GitHub-side action (opening a repo,
  creating a release, protecting a branch) on anyone's behalf. Every such
  step in this document and in `PUBLISH-CHECKLIST.md` is a human clicking or
  authorizing in the GUI, on purpose: credentials are never typed, stored, or
  logged by this tooling.
- **Real-estate maturity claims.** Every INTERNAL-EVAL maturity label in this
  project (`docs/KNOWN-LIMITS.md`, `docs/CLI.md`) means proven against this
  repository's own fixtures and evals, and no other estate. This release does
  not claim its gates have been exercised across multiple independent client
  codebases; the staged rollout above exists precisely because that claim
  cannot honestly be made yet, and shadow mode is how a real second estate
  starts building that evidence rather than this document asserting it in
  advance.

## The tag itself

Cutting a release tag such as `v1.0.0-rc.2` is `claude plugin tag` (validates that `plugin.json`
and the marketplace entry in `.claude-plugin/marketplace.json` agree before
tagging) or, equivalently, `git tag -a v1.0.0-rc.2 -m "<one line from
CHANGELOG.md>"`, exactly as `docs/RELEASE.md` already documents for a
maintainer cutting any release. **This command is not executed by this
page or by whoever wrote it.** It is executed by whoever owns the release,
after this wave's changes land and the full gate suite reruns green on that
landed state, never before, and never as a side effect of writing
documentation about it.

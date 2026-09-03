# Adopting BrotherSBE into a repository

`sbe adopt` and `sbe init` are commands you run from a copy of BrotherSBE
that is ALREADY INSTALLED (one of the three paths in
[README.md](../README.md): the marketplace pair, `sh install.sh`, or the
tag-pinned path in [docs/ROLLOUT.md](ROLLOUT.md)). They are not a fourth way
to install the tool itself; they configure a target repository to work with
an install you already have. `sbe adopt` proposes what that repository is
missing; it never applies a protection GitHub controls, because nothing here
holds a GitHub token or admin rights, and it never claims otherwise. This
page is the honest checklist: what `sbe adopt` can do for you, and what only
you (or your repo admin) can click.

## What `sbe adopt` does

```bash
bin/sbe adopt .                 # dry run: prints every proposal as a diff, writes nothing
bin/sbe adopt . --apply         # writes what was proposed; never overwrites an existing file
bin/sbe adopt . --apply --force # overwrites an existing file that differs from the proposal
bin/sbe adopt . --json          # machine-readable
```

It detects the stack (languages by extension, a migrations directory, dbt
models, API contract files, existing CI workflows) by walking the tree, the
same way `skills/adopt/SKILL.md` already documents: **detected from files,
not asked**. From that it proposes two files:

- `.brothersbe/policy.json`: a **provisional** repository policy. BrotherSBE's
  wave 3 (the repository policy file and its own JSON schema) has not shipped
  as this page is written; what `sbe adopt` proposes is a smaller shape built
  from what it can already detect, and the file says so on its own `note`
  field. A detected migrations directory or dbt project adds a matching
  `migrations` or `dbtModels` entry under `protectedPaths`; neither appears
  when neither is detected.

  Every other `protectedPaths` entry obeys one law: **only a path that
  exists under the target root is proposed**. Two entries are proposed
  unconditionally because the kit itself creates them (`.brothersbe/`, which
  `sbe adopt --apply` writes, and `design/`, which `sbe init --apply`
  writes). The remaining categories (the plugin manifest, the hooks, where
  the evidence schema is declared in code, product and consumer CI, and
  release identity files) name paths from a repository shaped like this
  one, and each path is checked against your tree first: a protection rule
  over a path that does not exist protects nothing while looking like it
  does, and the first external-proof run showed every one of them is a
  ghost in a foreign clone. A category that loses paths is not dropped
  silently: the policy's `_notProposed` block names every missing path, the
  report carries the same block under `notProposed`, and the human output
  prints one `NOT-PROPOSED` line per dropped category. If your repository
  keeps the equivalent files elsewhere, add the real paths to
  `protectedPaths` by hand before applying.
- `.github/CODEOWNERS`: generated straight from that same `protectedPaths`
  map (never a second hand-typed list, so the two cannot drift apart), so
  it too carries only paths that exist, plus a comment naming the dropped
  categories. Every line carries the placeholder `@REPLACE-ME`: this tool
  has no repository membership to read a real username or team from, and
  typing one in would be a guess dressed up as a proposal. **Replace it
  before this file protects anything.**

Both proposals are **deterministic**: the same tree produces the same
content every time, with no timestamp or run id inside either file. That is
what lets `--apply` run twice safely: the second run finds every proposal
already matches what is on disk and writes nothing, and says so.

## What `sbe adopt` reports, and the line it will never cross

The adoption report also names three protections it was asked to check:
branch protection, required status checks, and whether review from a code
owner is *required* (not just possible). **All three are settings on
GitHub's code review platform, not on a filesystem.** A `git clone` cannot
read them, this tool holds no GitHub credentials, and it asks for none. So
every one of those three always reports `UNVERIFIABLE-HERE`, naming what
reading it for real would take (a GitHub token with repo scope, plus admin
rights on the repository): **never `PRESENT`, no matter what is or is not
in the tree.** That is the one rule this whole command is built around, and
`tools/test_sbe_adopt.py::TestAdoptionReportNeverClaimsPresent` pins it as a
kill criterion: a report that ever claims one of those three PRESENT from a
local read is worse than the refusal `sbe adopt` used to print instead of a
command at all.

A CODEOWNERS file *existing in the tree* is a different, locally-checkable
fact, and is reported separately under `localFacts` (`PRESENT`/`ABSENT`) so
it is never read as proof that GitHub is actually configured to require that
review.

## What only a human with admin rights can do

`sbe adopt` cannot turn any of these on. Each is a real setting on your host,
named here with its exact path so nobody has to search for it. **GitHub first
because it is this project's reference implementation, then the same four
controls on Bitbucket Cloud**, which names them differently and does not have
CODEOWNERS at all.

### On GitHub

1. **Require a pull request before merging.** Settings > Branches > Branch
   protection rules > add a rule for your default branch > "Require a pull
   request before merging".
2. **Require status checks to pass before merging**, and select the
   "BrotherSBE gates" job (and, once your branch protection scope covers it,
   the consumer-checks job) as **required**. The same screen, "Require status
   checks to pass before merging". Adding the workflow file does not do this
   by itself: `docs/KNOWN-LIMITS.md` states the same limit as "The CI workflow
   guards nothing until you copy it" (L16), and both shipped workflows repeat
   it in their own header comments.
3. **Require review from Code Owners.** Same screen, "Require review from
   Code Owners", once `.github/CODEOWNERS` exists and every `@REPLACE-ME` has
   been replaced with a real user or team.
4. **Restrict who can push to the protected branch**, if you want to prevent
   a direct push around the pull request entirely.

### On Bitbucket Cloud

The same four intentions, under different names. Bitbucket calls these branch
restrictions and merge checks, and they live under **Repository settings >
Branch restrictions** (add a restriction for your default branch) unless
noted:

1. **Require a pull request before merging.** Add a branch restriction of
   type "Prevent changes without a pull request" for the default branch.
   That is the control GitHub spells "Require a pull request before merging".
2. **Require the build to pass before merging.** Repository settings > Merge
   checks, then enable the check for a minimum number of successful builds.
   The build key BrotherSBE reports under is `local-gates`, and a pipeline
   run reports whatever key `ci/bitbucket-pipelines.yml` posts. As on GitHub,
   adding the pipeline file guards nothing until this check is switched on.
3. **Require approvals.** Merge checks again, "Minimum number of approvals".
   **CODEOWNERS has no Bitbucket Cloud equivalent on the free plan**: code
   owners there is a paid feature and works from a different file, so `sbe
   adopt`'s CODEOWNERS proposal does not apply to you, and `sbe protections
   verify` reports NO-DATA rather than FAIL for its absence on a Bitbucket
   remote. Use "Minimum number of approvals" plus **Default reviewers**
   (Repository settings > Default reviewers) to get the effect that matters:
   a second named person has to approve.
4. **Restrict who can push.** A branch restriction of type "Restrict who can
   push", which is the direct counterpart of GitHub's option 4. **On Bitbucket
   treat this as part of step 2, not as optional.** Merge checks gate the
   pull request's own merge action. A merge performed locally and pushed
   straight at the default branch never invokes that action, so the checks in
   step 2 do not see it and the branch moves anyway. That is a property of
   git plus pull-request-shaped checking rather than a Bitbucket defect, and
   it is the same reason GitHub ships its option 4; it matters more here only
   because Bitbucket's merge-check screen reads as though it is the whole
   gate. Whether any Bitbucket setting closes that path without a push
   restriction is UNVERIFIED by this project: the claim was raised in an
   external review on 2026-08-18, and Atlassian's documentation could not be
   reached from the session that recorded it, so nobody here has confirmed
   the vendor's own wording. What does not depend on the answer: step 4
   closes the bypass for everyone its restriction excludes, whatever the
   merge-check screen does. It cannot close it for the actors the
   restriction still allows to push, so the allow list must hold only
   identities whose direct pushes you accept without the pull request gate,
   and on a small team that list is ideally empty.

**What is genuinely the same on both hosts, and it is the important one:** the
signed `Approved-by` trailer that BrotherSBE's approval gate reads is verified
locally by git against a key you trust. It needs no host API, no token and no
provider feature, so it produces the same verdict on Bitbucket as on GitHub.
Proven on a live Bitbucket repository on 2026-08-17: a commit authored by one
identity and signed by a second reached PASS on a clone pulled back down from
the remote. The host settings above are defence in depth around that; the gate
itself does not depend on them.

## `sbe init`

`sbe adopt` proposes policy and CODEOWNERS; `sbe init` writes BrotherSBE's
local footprint (config, dossier directory, receipt) into a target
repository. Neither command installs BrotherSBE itself: both assume the tool
is already on the machine running them, the same distinction the top of this
page draws. See `docs/CLI.md` ("sbe init") for its config file, dossier
directory, optional consumer CI copy, and the installation receipt with
exact uninstall instructions.

## Limits, stated where the behavior is

Full text: `docs/KNOWN-LIMITS.md` ("The adoption kit proposes, and verifies
only what a filesystem can answer").

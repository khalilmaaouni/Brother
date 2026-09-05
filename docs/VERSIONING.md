# Versioning

This is the release contract for Brother: what the three internal products
are, what version each is at, and the gates that decide when this repository
moves from a router into a merge. Every version figure below is copied from
`.claude-plugin/marketplace.json`, not guessed.

## The three internal products

The single Brother install is a facade. Underneath it are three products,
each with its own version and its own release cadence. Since the one-repo
cutover of 2026-08-31 (M6), BrotherMode and BrotherSBE ship from this
repository's `products/` tree. Source: `.claude-plugin/marketplace.json`'s
`plugins` list.

| Internal name | Ships from | Version | Covers | For |
| :-- | :-- | :-- | :-- | :-- |
| BrotherMode | products/brothermode (this repository) | 3.4.4 | execution provenance: what an assistant or teammate actually did, what checked it, what it is waiting on | whoever runs the project |
| BrotherSBE | products/brothersbe (this repository) | 3.7.3 | change assurance: hard gates that refuse a change on evidence, not confidence | engineers |
| BrotherDS | khalilmaaouni/BrotherDS | 0.1.0, EXPERIMENTAL | claim verification: whether a promised number turned out to be true | nobody yet, nothing to install |

BrotherDS reports its own north star, Verified Claim Rate, as NO-DATA today:
no claim has ever resolved. It is versioned and listed here for completeness,
not as something to install.

Each product keeps its own release notes and its own queue; this file does
not restate them. Where a product's own document and this file disagree about
that product's version, treat this file as stale and re-read
`.claude-plugin/marketplace.json`, since that file is the source this file
was built from.

## The umbrella itself

This repository (`brother`, the router plugin under `bundle/`) versions
separately from the three products it fronts, because it is a marketplace and
a facade, not a merge of their code. Its own version lives in
`.claude-plugin/marketplace.json` and `bundle/.claude-plugin/plugin.json`.
Current version: 1.0.5.

## Stage 0, Stage 1, Stage 2

Brother's own program moves through three stages, each gated on evidence, not
on a date. Source: `docs/CHARTER.md` and `docs/MERGE-PLAN.md`.

**Past Stage 0, not yet Stage 1.** The one-repo cutover (M6, 2026-08-31)
already moved BrotherMode and BrotherSBE's code physically into this
repository's `products/` tree, at the versions in the table above, which is
more than the original Stage 0 promised (router only, no product code
moved). The two Stage 1 gates below have not both cleared, so this
repository's own version (0.9.11) has not bumped to 1.0.0 the way
`docs/MERGE-PLAN.md` ties that number to Stage 1's completion.

**Stage 1, conditional.** The physical merge of BrotherMode and BrotherSBE
begins only once two gates clear:

1. One claim has resolved end to end in BrotherDS.
2. BrotherSBE's open pull request queue is drained to zero (a draft pull
   request does not count against this gate).

Neither gate has a date; each has a check, in `docs/MERGE-PLAN.md`.

**Stage 2, last.** BrotherDS joins by a clean extraction of its shippable
files, never by carrying its history, and only after the Stage 1 surface cut
is proven to hold.

## How a version bump here should read

A change to any figure in this file should be a copy from an updated
`.claude-plugin/marketplace.json`, in the same commit that updated the
manifest, never the other way around. `.claude-plugin/marketplace.json` is
the source of truth for these version numbers.

## Before every merge into main

Every merge into `main` runs `sh scripts/required_fast.sh` locally first,
and passes it (exit 0). It is the cheap mandatory pre-merge contract: a
fixed slice of the full battery (`scripts/check_all.sh`, 35 minutes) picked
for signal per dollar of wall clock, deterministic, about 90 seconds on this
machine. It is not a substitute for the full battery at a release candidate,
only the floor nobody merges under. A GitHub Actions workflow
(`.github/workflows/required-fast.yml`, `workflow_dispatch` only, per the
dispatch-only law) runs the identical script on a clean `ubuntu-latest`
runner for anyone who wants the same proof off this machine.

The version cut itself (`scripts/cut_v1.0.0.sh`) is a separate, later, and
still founder-only act: it bumps both manifests, points every ref at the
tag, and stops before the push. required-fast is the gate before the merge
that precedes a cut, not part of the cut script itself.

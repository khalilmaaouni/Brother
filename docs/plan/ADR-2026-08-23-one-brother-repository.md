# ADR: the one Brother repository

Status: CURRENT, and this is the architecture of record `docs/CHARTER.md` points at.

Scope of this page. The decision itself was taken by the founder on 2026-08-22 and
written up in full, with its context, its nine criteria, its four options, its decision
table and its verbatim founder rounds, in `products/brothermode/docs/plan/ADR-2026-08-23-one-brother-repository.md`
(416 lines, the deciding record). That page is a plan written before the move. This page
is the same decision read back off the tree as it stands today: every statement below
names the file or script it was read from, and nothing is named here that is not in the
tree. Where the two pages disagree about what exists, this one is describing reality and
the other one is describing intent.

## The decision, and where it is written

Option B: one repository, three plugins under one marketplace, with a thin bundle above
them. The deciding record's "Decision" section states Option B; its "AMENDMENT,
2026-08-23: Option B PLUS, a thin bundle above the three" adds the bundle. Two criteria
from that record are still load bearing and are named in `docs/CHARTER.md`: C3, each
product installable alone, and C4, the frozen tool surface, no skill or command renamed
and no new public command.

## The three products, and which of them ship

`products/` holds three directories: `products/brothermode`, `products/brothersbe`,
`products/brotherds`.

`.claude-plugin/marketplace.json` lists three plugins, not three products:

- `brothermode`, source path `products/brothermode`, version 3.4.4
- `brothersbe`, source path `products/brothersbe`, version 3.7.3
- `brother`, source path `bundle`, version 1.0.1, the thin bundle

`products/brotherds` is in the tree and is not in the marketplace. That is deliberate and
is asserted, not assumed: `tests/test_surface.py` refuses a marketplace that lists the
claims product before its own `plugins/brotherds` directory exists, which it does not. `products/brotherds` today
holds `bds.py`, `SPEC.md`, `OPTIONS.md`, `EXTRACTION-NOTE.md` and its own `skills/`.

Each of the three product directories carries its own manifest, `.claude-plugin/plugin.json`,
`products/brotherds` included. `tests/test_surface.py` asserts it for each of the three it
knows by name that is present on disk, which is C3 held by a test rather than by intention.

## The shared seam

The two documents that travel between products live at the root, in `contracts/`:
`contracts/change-passport.v1.json` and `contracts/handoff-package.v1.json`, with
`contracts/README.md` beside them.

They are copies by design, because an installed plugin's cache holds that plugin's
directory alone. `scripts/test_contracts_root.py` is the check that holds the root copies
byte identical to `products/brothermode/schema/change-passport.v1.json` and
`products/brothersbe/contracts/handoff-package.v1.json`. `tests/test_surface.py` names
that script as the check.

## The bundle: what an install actually gets

`bundle/` holds `MANIFEST.json`, `commands/`, `skills/` and `runtime/`.

`runtime/` is generated, never hand edited. `scripts/bundle_runtime.py` is the one place
that copies the engine into `bundle/runtime/`, and its `--check` mode refuses a stale
copy. Its own docstring states the reason: `bundle/` once held commands and skills only,
so the route that told a session to run `scripts/brother_run.py` was dead on an installed
machine, which has no checkout of this repository beside it.

## The engine loop

`scripts/brother_run.py` is the one door in front of the spine: a plain outcome in, a
verified delivery report out. It calls pieces that each exist and are each tested alone,
named in its own docstring: `scripts/door.py`, `scripts/work_record.py`,
`scripts/loop_bridge.py`, `scripts/model_worker.py`, `scripts/integrate.py` and
`scripts/claim_store.py`. It is not a second implementation of any of them.

Its `_verify_evidence` step re-executes each unit's recorded check and refuses the
unprovable; `scripts/receipt_door.py` cites that step by name and by line range.

Which nodes may run now, and which may run together, is `scripts/graph_loop.py`.
`scripts/merge_queue.py` reuses that file's own write set conflict logic rather than
carrying a second copy, and is registered in `scripts/check_all.sh` as `merge-queue`.

## The claim store

`scripts/claim_store.py` holds the single writer rule: no worker starts before its claim
exists, and two never own one unit. `scripts/fence_expiry.py` fails on a claim that can
never expire, and both it and its self test are registered in `scripts/check_all.sh`.
`scripts/write_ledger.py` records attribution at write time, so a shared tree can say who
wrote an undeclared file instead of guessing.

## The receipts

`scripts/receipt_door.py` is the screen that hands over the facts a delivery can prove.
`scripts/v3_receipts.py` records pre action memory receipts by wrapping
`bm_recurrence.py`'s own CLI through subprocess, never its internals, and refuses to emit
a receipt for a unit with no genuinely surfaced lesson. `scripts/test_receipt_door.py` is
registered in `scripts/required_fast.sh` as `receipt-door`.

## The hub and public split

Work happens in the private hub, `khalilmaaouni/brother-hub`, which is `origin` in a
working checkout. The public export target is `khalilmaaouni/Brother`, named in
`scripts/release_invariant.py` as `PUBLIC_REMOTE`. A session never pushes to the second
one, and the two paragraphs below are the mechanism, not the etiquette.

`scripts/export_public.py` is the single route from the hub to the public target. It
reads `docs/plan/EXPORT-ALLOWLIST.txt`, and a path not listed there never leaves the hub;
a missing allowlist is a refusal, not a pass.

`scripts/edition_guard.py` binds a directory to its nearest `.brother-edition` file and
refuses a push whose remote is the public target unless the invocation is the exporter's
own, so forgetting fails private rather than public.

## The editions and their vaults

`editions/` holds `client-one`, `client-two`, `dev`, `personal` and its own `README.md`.
That README states the rule: each edition names itself and its vault in its own
`.brother-edition` file, and the repository root is the public core, because the exporter
ships the root and never `editions/`. The root's own `.brother-edition` reads
`edition: public-core` and `vault: none`.

The vault is therefore not a directory in this repository. It is a per edition binding,
and the only vault statement this tree can make about itself is the root's `vault: none`.
`scripts/vault_benchmark_v2.py` scores vault institutional memory as dimension D of the
atomic benchmark; it measures a vault, it does not contain one.

## The release chain

`scripts/release_invariant.py` checks the chain that makes an install string identify one
exact set of bytes, and names each link it could and could not read:

1. `bundle/.claude-plugin/plugin.json` agrees with the `brother` entry of
   `.claude-plugin/marketplace.json`
2. `docs/releases/<version>.md` exists for that version
3. the public repository carries tag `v<version>`, by one `git ls-remote` call
4. the installed plugin cache for that version, when present on this machine, is byte
   identical to the repo bundle

Its exits are this estate's convention and are documented in its own docstring: 0 when
every reachable link agrees, 1 when a reachable link contradicts, 2 for NO-DATA when not
even the in repo declarations could be read. `scripts/readiness_gate.py` runs it and
treats exit 0 as PASS.

## What runs, and where each check is registered

`scripts/required_fast.sh` is the small pre merge slice; `scripts/check_all.sh` is every
check this repository ships. A check that is in neither is a check nobody runs, which is
why each new checker in this tree is registered in the same change that lands it.

`tests/test_surface.py` is the structural test for the shape this record describes: MIT
license, no self firing workflow under `.github/workflows`, the marketplace listing at
least two plugins each naming its own source, per product manifests, the root
`contracts/` directory, `COORDINATION.md` naming this record, and this file itself
opening and naming the decision. `scripts/charter_paths.py` is the narrower guard: every
repository path `docs/CHARTER.md` names in backticks must exist in the tree, which is the
defect that produced this file.

## What would flip this

The deciding record's own "What would flip this" section governs the decision. What would
flip this page is narrower: any of the files named above moving or disappearing. That is
not left to a reader's diligence. `scripts/charter_paths.py` fails when the charter names
a path the tree lacks, and `tests/test_surface.py` fails when this file stops opening or
stops naming the decision.

# Versioning

This is Brother's release contract. Version figures are copied from the version source manifests and are not guessed. The release contract in the current versioning document that remains true is retained here: version changes follow the manifests, a cut follows its required checks and owner decision, and release claims require verifiable evidence.

## One plugin

Since 1.1.0, there is one plugin named `brother`. The old plugins `brothermode`, `brothersbe` and `brotherds` are retired from the catalog, leaving one catalog entry in each surviving catalog. Pinned installs of old plugins keep resolving; see `docs/how-to/migrate-to-one-plugin.md`.

The target is one installable plugin tree and one plugin on each host: Claude Code, Codex, Cursor and Antigravity, with Cursor advisory until its signed in canary demonstrates a deny. In 1.1.0 Antigravity is experimental and unverified: its install path ships, but no Antigravity check gates the release and no parity or certification claim is made (owner scope decision, docs/decisions/scope-1.1.0-defer-to-1.1.1-2026-10-03.json); its certification is a 1.1.1 item. The accepted host proof specification says package level checks exist, but it also says no signed in real host hook fire has yet been recorded for any of those hosts.

## Version numbers

Current version: 1.1.1.

Copy any version figure in this document from the updated version source manifests in the same change that updates those manifests. The manifests are the source of truth for version numbers.

## Release gates

Before each merge to `main`, run `sh scripts/required_fast.sh` locally and require exit 0. This is the mandatory pre-merge check; it does not replace the full battery at a release candidate.

A release cut follows the required checks and remains an owner decision. The cut must keep the version manifests and their references aligned with the release tag, and stop before pushing or publishing.

Release verification must use evidence produced after the last edit. A check that cannot run is NO-DATA, not PASS, and a release readiness result must expose PASS, FAIL or NO-DATA for each required row rather than passing by omission.

## 1.1.0 work still required

OP1, HP1 and U8 describe work for the 1.1.0 cut; their completion and evidence are recorded in the release note.

The 1.1.0 cut establishes one catalog entry, `brother`; unreadable catalog input must not pass.

HP1, the live host proof (a signed in session on each host firing the shipped hooks), is NOT recorded for 1.1.0: `python3 scripts/host_live_verify.py docs/plan/evidence/hp1-real-2026-10-04` reads NO-DATA, and the release note says so. Package level evidence alone is not live host proof. Antigravity rows report NO-DATA and gate nothing in 1.1.0.

Describe OP1, HP1 and U8 as shipped only where their evidence supports that claim.

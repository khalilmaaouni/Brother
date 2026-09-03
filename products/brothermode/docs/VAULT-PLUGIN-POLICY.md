# The vault plugin trust policy

Companion to `docs/VAULT-TRUST-BOUNDARY.md`. That page states what the
vault's access controls do and do not cover. This page states the same
kind of plain fact about a different crossing: a community plugin.

## Why a plugin is a boundary crossing

An Obsidian community plugin runs as arbitrary JavaScript inside the same
process that has the vault open. It is not sandboxed from the vault's
files, and Obsidian's plugin API does not partition one plugin's file
access from another's. A plugin that asks to read one note can, in
practice, read every note, write any note, and reach the network, exactly
as described in `docs/VAULT-TRUST-BOUNDARY.md`'s bypass section: nothing
about `bm_vault_policy.py`, `bm_vault_audit.py`, or the recall path stands
between a running plugin and a direct file open, because a plugin is
inside the same trust domain as Obsidian itself, not a caller of the
served path.

So the operating assumption for a sensitive vault is: **a community
plugin has full vault access inside the disk boundary already described
in VAULT-TRUST-BOUNDARY.md.** Nothing below changes that fact. The policy
exists to decide, deliberately, which plugins are worth accepting that
exposure for, not to reduce the exposure itself.

## The posture: core features first

Given that every community plugin is a full-access crossing, the default
answer for a sensitive vault is to prefer a core Obsidian feature over a
community plugin wherever one covers the need. A core feature ships from
Obsidian's own release, is not third-party code, and is not a separate
update channel to track. A community plugin is added only when no core
feature covers the need and the crossing is named and justified in the
allowlist below.

## What an allowlist entry must name

Every community plugin that is allowed on a sensitive vault gets an entry
naming, in one place, so the crossing is legible rather than assumed:

- **What it reads.** The scope of vault content the plugin's stated
  purpose requires touching (a folder, a file type, note metadata, the
  whole vault).
- **What it writes.** Whether the plugin only reads, or also creates,
  edits, or deletes files, and where.
- **Its update channel.** Where new versions come from (Obsidian's
  community plugin browser, a GitHub release, manual install) and who
  reviews an update before it lands, since an update can change a
  plugin's behavior without changing its name.
- **Why the crossing is worth it.** The concrete capability gained that
  no core feature provides, stated plainly enough that someone could
  disagree with it.

An entry missing any of the four is not a complete allowlist entry.

## Refused by name

Two categories are refused outright on a sensitive vault, regardless of
how a would-be allowlist entry might read:

- **Sync and publish plugins.** A plugin whose job is to copy vault
  content to a third-party server (a sync service, a publish/hosting
  service) is refused by name. The sanctioned sync routes for a sensitive
  vault are Obsidian Sync's audited end-to-end encryption, or the
  estate's own age relay; neither of those is a community plugin crossing
  the same trust domain as unreviewed third-party JavaScript.
- **Plugins that phone home or embed remote content.** A plugin that
  calls out to a remote service as part of normal operation (telemetry,
  a hosted API, remote embeds fetched at render time) is refused, because
  it turns a local file-access crossing into a network exfiltration path
  for the same full vault access described above.

A plugin that is refused for either reason is refused whether or not it
also offers a genuinely useful feature; the refusal is about the crossing
it opens, not about whether the feature is good.

## Review cadence

An allowlist entry is re-classified (allowed, review, or refuse,
re-checked against the four fields above) on every version update the
plugin receives, not on a fixed calendar. An update is exactly the event
that can change what a plugin reads, writes, or phones home to without
changing its name or its entry in `community-plugins.json`.

## The approval chain this policy would route through in service mode

Accepting a plugin onto the allowlist is a registry mutation like any
other. `docs/VAULT-ENTRA-APPROVAL-CONTRACT.md` (VB10-05) names the
sequential tiers (requester, owner, optional reviewer, named approver
users or Entra groups, nested groups explicitly unsupported) a
service-mode deployment implements against Entra ID for exactly this
kind of registry mutation. It is a contract only; nothing in it is built
until the founder gates service mode, and today an allowlist entry is
still a human classification against the four fields above, not an
Entra-gated approval.

## The measurement: counts on the reference vault

Measured 2026-08-30 against the estate's own reference vault, read-only.
The full inventory and per-plugin classification table live INSIDE that
vault (a private surface), because a vault's installed-plugin list is
machine-internals detail this public page has no need to carry. What the
policy needs on the record here:

- 25 community plugins were enabled; this is not the zero-plugin posture.
- Classified: 14 allowed (local-only, no known write or network path),
  10 review (the four-field entry is not yet written; review means
  unexamined, never found-safe), 1 refuse.
- The one refusal is a local REST API plugin: a listening HTTP server
  exposing full vault read and write to any local caller, which is the
  network-exposure category this policy refuses by name, whatever
  convenience it buys.
- The vault's core sync toggle was ON; whether that stays on is the
  founder's sanctioned-sync decision (audited E2EE sync or the age
  relay), named on the founder queue.

The classification is a measurement taken once, on the date stated, and
is redone on every plugin update per the cadence above.

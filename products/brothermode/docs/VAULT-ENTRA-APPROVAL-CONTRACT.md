# The Entra approval chain: a service-mode contract

WBS row VB10-05. Companion to `docs/VAULT-TRUST-BOUNDARY.md` (what the
vault's access controls cover today, and what service mode would need) and
`docs/VAULT-PLUGIN-POLICY.md` (the same honesty discipline applied to a
different crossing). This page applies that discipline to a third thing:
an approval chain for actions the estate already performs by hand.

## What this is

This is a contract: the shape a service-mode deployment implements against
Microsoft Entra ID, written down now so the shape is settled before anyone
builds it. Nothing mechanical exists in this repository because of this
page. `tools/bm_vault_pane.py` says so in its own docstring today: "ENTRA
BINDS THIS AT SERVICE MODE (the approved VB10-05 contract, not built
here): this file claims nothing mechanical about Entra." This page is that
referenced contract. Building against it is gated on the founder's own
decision to turn on service mode; this page does not make that decision,
it only means the decision does not have to also invent the shape when it
is made.

## The sequential tiers

Modeled on Microsoft Entra ID entitlement management's own access-package
approval flow: a request moves through zero or more approval stages in
order, one decision per stage, and any denial at a stage ends the request
without falling through to the next stage. This contract names four tiers,
in order. A given estate action may skip the optional tier; it may never
skip its ordering.

1. **Requester.** The principal asking for the action (a promotion, a
   registry mutation, a restricted-note read) to happen. Not an approval
   tier by itself; it is the event every later tier reacts to, and it is
   the one identity in the chain that can never also be its own approver
   for the same request, matching Entra entitlement management's own rule
   that "approvers aren't able to approve their own access package
   requests."
2. **Owner.** The principal recorded as owning the resource the action
   touches (`tools/bm_vault_principals.py`'s own `owner` role, one of its
   four: `reader`, `editor`, `steward`, `owner`). The owner tier answers
   "does the person accountable for this resource agree," and is
   mandatory: every sequential chain this contract governs starts here.
3. **Reviewer, optional.** A second, independent look before an approver
   acts, for a request class the founder marks as needing one (Entra's own
   multi-stage approval supports exactly this shape: "you could designate
   the resource owner as a second approver and a security reviewer as the
   third approver," letting a security team have oversight without being
   the accountable owner). Optional per request class, not per request:
   whether a class needs a reviewer is a standing decision, not a runtime
   choice.
4. **Approver: named users or Entra groups, nested groups explicitly
   unsupported.** The final tier. An approver is either a named individual
   or a named Entra security group, matching Entra entitlement management's
   own approver model: "The approver can be a specified identity or member
   of a group, the requestor's Manager, Internal sponsor, or External
   sponsor" (Microsoft Learn, *Change approval settings for an access
   package in entitlement management*, `entitlement-management-access-
   package-approval-policy`, checked 2026-08-30). **Nested groups are
   explicitly unsupported**: an approver group resolves its direct members
   only, never a group nested inside it. The reason is that Entra ID's own
   governance surfaces do not resolve nested membership consistently, so
   picking anything other than direct-membership-only would inherit an
   ambiguity this contract can settle instead of import. Two governance
   facts back that choice, both Microsoft Learn, both checked 2026-08-30:
   access reviews flatten nested groups for review scope but their removal
   result does not cascade into the nested group ("If a user is flagged
   for removal due to their membership in a nested group, they won't be
   automatically removed from the nested group, but only from direct group
   membership," `create-access-review`), and PIM's role-assignable groups
   refuse active nesting outright ("role-assignable groups can't have
   other groups nested inside them... This is applicable to active
   membership," `privileged-identity-management/concept-pim-for-groups`).
   Given Entra's own features already draw a hard line against depending
   on nested membership for a governance decision, this contract draws the
   same line for approval: a group approver's membership is read flat, one
   level, and a nested group inside it grants nobody approver standing.

## Per tier: Entra resolution, logging duty, estate surface

Every tier below names the same three things: how service mode would ask
Entra who is real, what the audit records, and which of the estate's own
surfaces the tier governs today (in single-machine mode, by the registry
seam) and would govern in service mode (through Entra).

### 1. Requester

- **Entra resolution.** The signed-in identity making the call, taken from
  whatever authenticates the service-mode transport in front of
  `bm_vault_serve.py` (named but not built, per
  `docs/VAULT-TRUST-BOUNDARY.md`'s "Transport identity or authentication in
  front of the serve layer"). Today: the client-declared `principal`
  string every recall and pane action already carries, unauthenticated.
- **Logging duty.** Every audit row already carries this: `bm_vault_
  audit.append`'s `principal` field, on every call, no tier-specific
  addition needed.
- **Estate surface.** All three: promotions, registry mutations,
  restricted-note access. The requester is present on every request by
  definition.

### 2. Owner

- **Entra resolution.** In service mode, the resource's recorded owner
  principal is cross-checked against an Entra-verified identity or group
  membership before the owner's decision counts, the same shape Entra's
  own "Group owner(s)" reviewer type uses in access reviews. Today: `bm_
  vault_principals.py`'s `owner` role on the resource's recorded principal,
  read with no Entra call, `status_of`'s registry lookup alone.
- **Logging duty.** The same `bm_vault_audit.append` row as every action
  today, with the acting principal recorded and the audit's own honest
  caveat unchanged: "principal values are client-declared, not
  authenticated identities" until service mode's Entra check lands.
- **Estate surface.** Promotions (`tools/bm_vault_promotions.py promote`,
  moving a note between `lc.STATES`: `candidate`, `validated`,
  `canonical`, `rejected`) and registry mutations (`tools/bm_vault_
  principals.py add` / `revoke` / `reactivate` / `set-role`). Restricted-
  note access is read-only and has no owner-approval step; the owner tier
  does not govern it.

### 3. Reviewer (optional)

- **Entra resolution.** A named Entra security group for the request
  class, resolved the same way the approver tier resolves a group (direct
  membership only, nested groups unsupported, same citation and same
  reason as the approver tier below). Today: no equivalent exists; there
  is no second-look step before a promotion or a registry mutation lands.
- **Logging duty.** A reviewer decision, when a class carries one, gets
  its own audit row, same shape as every other tier's, distinguishable
  from the owner's and the approver's rows by which tier recorded it, so a
  reader of the audit trail can answer "did this pass a reviewer" without
  guessing from timing.
- **Estate surface.** Named per request class, not universal. A class the
  founder marks as needing one (a restricted-note access grant is the
  most likely first candidate, given it is the one surface with no owner
  gate today) gets a reviewer stage; a routine promotion may not.

### 4. Approver (named users or Entra groups)

- **Entra resolution.** A named user resolves as that Entra identity
  directly. A named group resolves to its direct member list only, read
  fresh at decision time (not cached, so a membership change before the
  decision is made is the membership service mode actually honors);
  nested groups are unsupported per the reasoning above. Today: no
  equivalent tier exists; the closest analog is `bm_vault_pane.py`'s own
  refusal check, `bm_vault_principals.status_of`, which only tells the
  pane whether the clicking principal is revoked, never who is allowed to
  approve.
- **Logging duty.** The approver's decision closes the chain, and its
  audit row records the outcome the way `bm_vault_pane.py` already records
  a pane click: `event_id`, `principal`, a `query` field encoding the
  action (`"pane:%s:%s id=%s" % (kind, decision, item_id)` is today's
  exact format for promotion and curation clicks), `served_ids` naming
  what was actually acted on, and `refused` set when a refusal, not an
  approval, ended the chain.
- **Estate surface.** All three, as the final gate: promotions, registry
  mutations, and restricted-note access, plus `bm_vault_pane.py`'s own
  `POST /act`, which is the one estate surface that already has a click
  transport waiting for this tier to exist behind it.

## Mapping table: estate surface to tier chain

| Estate surface | Requester | Owner | Reviewer | Approver |
| --- | --- | --- | --- | --- |
| Promotions (`bm_vault_promotions.py promote`) | always | mandatory | per class | mandatory |
| Registry mutations (`bm_vault_principals.py add`/`revoke`/`reactivate`/`set-role`) | always | mandatory | per class | mandatory |
| Restricted-note access (policy `deny` overridden by request) | always | not applicable, read-only | per class, most likely candidate | mandatory |
| `bm_vault_pane.py` `POST /act` | always (the clicking principal) | mandatory (already the resource's owner) | per class | mandatory, not yet gated |

## What service mode adds, what stays true today

Service mode adds the actual Entra calls: verifying a requester's identity
against a real sign-in rather than a declared string, resolving an
approver group's direct membership from Entra rather than trusting a
locally maintained list, and gating `bm_vault_pane.py`'s `POST /act` on an
approver tier that does not exist yet. None of that is built by this page.

What stays true in single-machine mode today, per VB11-05's own resolution
(`bm_vault_pane.py`'s ROLE REQUIREMENT paragraph): the registry seam
(`bm_vault_principals.py`) already answers roles, opt-in per identity,
with no Entra call and no write control of its own. A revoked principal's
click is refused today through that seam alone. This contract does not
ask single-machine mode to grow an Entra dependency; it names the shape
service mode fills in when the founder gates it.

## What this contract does not claim

This is a plan, not a build. Nothing in this repository calls Entra ID,
Microsoft Graph, or any identity provider today. Confirmed by grep, its
own output quoted in the honesty footer below: no source file in this
tree names an Entra endpoint, an OAuth flow against Microsoft's identity
platform, or a group-membership API call. `bm_vault_pane.py`'s own comment
already says the same thing about itself: "this file claims nothing
mechanical about Entra." This page inherits that same claim, and extends
it to the whole chain: none of the four tiers above run against a real
Entra tenant until the founder turns service mode on and someone builds
against this contract.

## Honesty footer

Grep run against this repository's tracked source (`tools/`, `docs/`)
2026-08-30, from the repository root:

```
$ grep -rniE "graph\.microsoft\.com|login\.microsoftonline|entra.*(api|endpoint|oauth|token endpoint)" tools/ docs/*.md
(no matches)
```

No matches. Nothing in this repository calls Entra today. The two facts
this page relies on for the approver and nested-group design were checked
against Microsoft's own documentation, not recalled from training data:

- Group-based approver resolution: Microsoft Learn, *Change approval
  settings for an access package in entitlement management*,
  `https://learn.microsoft.com/en-us/entra/id-governance/entitlement-
  management-access-package-approval-policy`, checked 2026-08-30.
- Nested-group limitation in Entra governance features: Microsoft Learn,
  *Create an access review of groups and applications*,
  `https://learn.microsoft.com/en-us/entra/id-governance/create-access-
  review`, and *Privileged Identity Management (PIM) for Groups*,
  `https://learn.microsoft.com/en-us/entra/id-governance/privileged-
  identity-management/concept-pim-for-groups`, both checked 2026-08-30.

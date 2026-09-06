#!/usr/bin/env python3
"""bm_vault_context.py: RequestContext and tenancy for the served vault (WBS row VB3-03).

Enterprise mode cannot construct a recall without tenant and principal context, and every
audit event needs an immutable request id. Today the estate is single-tenant by
construction: bm_vault.py resolves exactly one index, answer ledger and access audit file,
all hardcoded under ~/.claude, no matter who asks or what BM_VAULT_ROOT names at recall
time (confirmed by reading tools/bm_vault.py's _search: it queries that one global SQLite
index directly, never filtered to whatever vault root the caller passed). The served
endpoint (bm_vault_serve.py) is where the context boundary enters, so this module owns
exactly the two small things that boundary needs: minting the immutable request id, and
resolving a tenant string into a subprocess environment that is ACTUALLY isolated, not
merely pointed at a different content directory.

SEAM CHOSEN: full "two vault roots" isolation via HOME, not a per-note tenant column. A
per-note tenant field would work too, but it means touching bm_vault.py's schema,
_upsert_note and _search -- a bigger, riskier diff than one WBS row justifies, and it still
has to solve the exact same problem (the index, ledger and audit paths are computed from
os.path.expanduser("~"), not from BM_VAULT_ROOT). Reusing that existing home-relative
plumbing is the smaller, honest seam: point a tenant's subprocess HOME at a private
directory and its BM_VAULT_ROOT at that same directory's own "vault" subfolder, and
bm_vault.py's every hardcoded ~/.claude path (the index, the answer ledger, the access
audit, the installer config) resolves inside it for free, with zero changes to bm_vault.py
itself. Convention, PRE-PROVISIONED by whoever operates the estate (this module only reads
it, it never creates a directory on a caller's say-so -- an unprovisioned tenant name must
refuse, never silently spin up empty state for it):

  <tenants-root>/<tenant>/vault      the tenant's own BM_VAULT_ROOT
  <tenants-root>/<tenant>/.claude    the tenant's own index, ledger and access audit,
                                     already `bm_vault.py index`-ed

No id or path this module builds ever embeds the tenant string as an identifier that
leaves this process; it is used only to select a directory, the same way any other config
path already is.

No em or en dashes anywhere in this file.
"""
import os
import re
import uuid

#: Safe as a single directory component and nothing else: no '.', '/', or whitespace, so a
#: tenant string can never walk out of tenants-root or address a second segment.
#: Deliberately narrower than a general-purpose identifier for exactly that reason.
TENANT_RE = re.compile(r"^[A-Za-z0-9_-]+$")


def new_request_id():
    """A fresh, immutable request id: hex uuid4, minted exactly once per served request,
    server-side. Carries no tenant, principal or query fragment -- safe to log, return to
    the caller, and feed back into bm_vault.py as --event-id (VB6-03's existing per-answer
    id, reused rather than duplicated) without leaking anything it names."""
    return uuid.uuid4().hex


def missing_enterprise_fields(tenant, principal):
    """The required-but-absent field names ("tenant", "principal", or both, in that order)
    for one request under enterprise mode. Empty list means the request may proceed. Either
    value must be a non-empty, non-whitespace string; anything else (missing, None, "",
    "   ", a non-string) counts as absent -- never a guess at what the caller meant."""
    missing = []
    if not (isinstance(tenant, str) and tenant.strip()):
        missing.append("tenant")
    if not (isinstance(principal, str) and principal.strip()):
        missing.append("principal")
    return missing


def tenant_env(tenants_root, tenant):
    """(env overrides dict, error) for one tenant, to merge into a subprocess environment
    that already isolates that tenant's index, ledger and access audit (see the module
    docstring). Returns (None, "reason") -- never a guess, never a silent fallback to the
    shared, unscoped environment -- when: no tenants_root is configured; tenant is not a
    clean single-segment name; or the tenant is not already provisioned (both its vault and
    its .claude state directory must already exist on disk)."""
    if not tenants_root:
        return None, ("enterprise mode has no --tenants-root/BM_VAULT_TENANTS_ROOT "
                      "configured; a tenant cannot be resolved to anywhere")
    if not isinstance(tenant, str) or not TENANT_RE.match(tenant):
        return None, ("tenant %r is not a safe identifier (letters, digits, - and _ "
                      "only)" % (tenant,))
    home = os.path.join(tenants_root, tenant)
    vault = os.path.join(home, "vault")
    state = os.path.join(home, ".claude")
    if not os.path.isdir(vault) or not os.path.isdir(state):
        return None, ("tenant %r is not provisioned: expected both %s and %s to already "
                      "exist" % (tenant, vault, state))
    return {"HOME": home, "BM_VAULT_ROOT": vault}, None
